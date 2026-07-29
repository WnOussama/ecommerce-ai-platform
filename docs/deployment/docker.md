# Déploiement — Stack Docker complète

Procédure vérifiée pour faire tourner l'AI Core entièrement conteneurisé
(Postgres, Redis, ai-core avec ChromaDB persistant réel). Pour un chemin de
développement plus rapide à itérer (backing services en conteneur, API en
natif), voir [`docs/runbooks/local-dev.md`](../runbooks/local-dev.md).

## Prérequis

- Docker Desktop, avec de l'espace disque disponible (voir note plus bas)
- `infrastructure/docker/.env` avec au minimum `DB_PASSWORD`, `DB_NAME`,
  `DB_USER`, `LLM_PROVIDER`, `SECURITY_JWT_SECRET_KEY`,
  `SECURITY_API_KEY_ENCRYPTION_KEY` (32+ caractères chacune)

## 1. Build de l'image ai-core

```bash
cd infrastructure/docker
docker-compose -f docker-compose.dev.yml build ai-core
```

### Taille et temps de build

L'image installe `sentence-transformers` (utilisé uniquement pour le
re-ranking cross-encoder), qui entraîne `torch` en dépendance transitive. Sur
une plateforme où pip résout des wheels CUDA (`nvidia-cudnn-cu13`,
`nvidia-cusolver`, etc. — plusieurs centaines de Mo au total), le build peut
télécharger 1 Go+ de bibliothèques GPU **jamais utilisées** par ce projet
(aucune inférence GPU nulle part dans le code). Sur une connexion instable,
ce téléchargement est le point de rupture le plus probable du build (nous
avons observé un échec après 68 minutes sur un téléchargement interrompu
d'une seule roue CUDA de 444 Mo).

**Recommandation non appliquée dans ce commit** (changement plus large,
hors scope de cette session) : épingler `torch` avec l'index CPU-only
(`--extra-index-url https://download.pytorch.org/whl/cpu`) pour éliminer ce
téléchargement inutile et accélérer le build de manière significative.

## 2. Services de fond

```bash
docker-compose -f docker-compose.dev.yml up -d postgres redis
```

> Le service `chromadb` du compose n'est **pas nécessaire** — voir la note
> ci-dessous.

## 3. Migrations

Depuis `src/ai-core`, avec `DB_HOST=localhost` (le port Postgres est exposé
sur l'hôte) :

```bash
DB_HOST=localhost DB_PORT=5432 DB_NAME=saas_ecommerce DB_USER=saas_user \
DB_PASSWORD=<mot de passe> SECURITY_JWT_SECRET_KEY=<32+ car.> LLM_PROVIDER=mock \
./.venv/bin/alembic upgrade head
```

## 4. Démarrer ai-core

```bash
docker-compose -f docker-compose.dev.yml up -d ai-core
curl -s http://localhost:8000/health
curl -s http://localhost:8000/health/db   # doit renvoyer connected:true
```

## 5. Peupler des données de démonstration (dans le conteneur)

```bash
docker exec saas_ai_core python -m scripts.seed_demo_data
```

Contrairement à l'exécution native (voir le runbook local-dev), ce chemin
utilise le **vrai** ChromaDB persistant (Python 3.11, image `ai-core`), pas
le fallback `InMemorySearchableVectorStore`. Vérification faite dans cette
session : les produits indexés sont retrouvables après `docker restart
saas_ai_core` (nouveau processus, même disque `/app/data/chroma`).

## ChromaDB : persistance réelle {#chromadb-persistance-réelle}

**Ce qui a été trouvé et corrigé dans cette session** : les trois adaptateurs
ChromaDB du code (`app/infrastructure/vector_store/service.py`,
`app/services/rag/product_indexer.py::ChromaVectorStore`,
`app/services/rag/retrieval_service.py::ChromaSearchableVectorStore`)
construisaient le client avec
`chromadb.Client(Settings(chroma_db_impl="duckdb+parquet", ...))` — une API
supprimée que `chromadb==0.4.22` refuse explicitement au runtime
(`"deprecated configuration of Chroma"`). Résultat : **la persistance
ChromaDB n'a jamais fonctionné, dans aucun environnement**, y compris sous
Python 3.11 dans le conteneur `ai-core` prévu pour ça — le fallback gracieux
vers `InMemorySearchableVectorStore` masquait totalement le problème (aucune
erreur visible, juste un `WARNING` dans les logs).

Corrigé en remplaçant par l'API moderne :
```python
chromadb.PersistentClient(path=persist_directory, settings=Settings(anonymized_telemetry=False))
```

**Deuxième problème découvert en corrigeant le premier** :
`app/services/rag/factory.py::get_vector_store()` — la seule factory
existante pour obtenir un vector store — ne retournait que
`ChromaSearchableVectorStore`, une classe **en lecture seule** (`search`,
`count`, pas d'`upsert`). `ProductIndexer` (le service d'indexation) a
besoin d'écrire (`upsert`), et aucune factory ne wirait la classe
write-capable (`ChromaVectorStore`, définie séparément dans
`product_indexer.py`) pour cet usage. Toute tentative d'indexer réellement
via `get_vector_store()` échouait silencieusement (`AttributeError` capturée
et journalisée comme "Failed to store batch").

Corrigé en ajoutant `upsert()` et `get_ids()` directement à
`ChromaSearchableVectorStore` (elle avait déjà toute la logique de gestion
de collection nécessaire) plutôt que de dupliquer une troisième factory — la
même instance singleton sert maintenant indexation et recherche de façon
cohérente, comme c'est déjà le cas pour `InMemorySearchableVectorStore` (qui
avait toujours eu les deux).

Tests de régression ajoutés dans
`tests/unit/test_retrieval_service.py::TestChromaSearchableVectorStoreUpsert`
(upsert réel + recherche, `get_ids`, persistance à travers une nouvelle
instance de client) — ils utilisent un vrai `chromadb.PersistentClient` (pas
de mock) et sont marqués `pytest.importorskip("chromadb")`, donc ignorés
proprement sur un environnement où chromadb n'importe pas (ex. Python 3.14
natif), mais s'exécutent réellement en CI (Python 3.11) et dans le
conteneur.

## Le service `chromadb` du docker-compose est mort

`docker-compose.dev.yml` définit un service `chromadb` (serveur HTTP,
image `chromadb/chroma:0.4.22`) et le service `ai-core` reçoit
`CHROMA_HOST`/`CHROMA_PORT` en variables d'environnement pour s'y connecter.
**Aucun code applicatif ne les lit** — les trois adaptateurs ChromaDB du
projet utilisent tous un client embarqué (`PersistentClient` avec
`persist_directory` local), jamais un `HttpClient` réseau. Ce service
`chromadb` est donc de l'infrastructure orpheline : il ne sert à rien tel
quel, et il est de surcroît cassé sur macOS Apple Silicon — l'image
`chromadb/chroma:0.4.22` crash-loop indéfiniment au démarrage sur ARM64
(son propre script de "réparation" réinstalle un `numpy>=2.0` qui a
supprimé `np.float_`, dont son propre code a toujours besoin — la boucle ne
se termine jamais). Il n'est **pas nécessaire** de le démarrer.

Deux options pour une prochaine itération, non traitées dans cette session
(hors scope) : soit supprimer ce service du compose (l'infra orpheline
prête à confusion), soit migrer les adaptateurs vers un vrai
`chromadb.HttpClient` pointant sur ce service pour un déploiement
multi-instance (le mode embarqué actuel ne partage pas l'état entre
plusieurs réplicas de `ai-core`).
