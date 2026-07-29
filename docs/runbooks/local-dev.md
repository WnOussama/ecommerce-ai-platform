# Runbook — Environnement de développement local

Procédure vérifiée pour faire tourner l'AI Core en local avec des données de
démonstration. Deux chemins sont documentés : le chemin **natif** (rapide à
itérer, testé de bout en bout dans ce runbook) et le chemin **conteneurisé**
(voir [`docs/deployment/docker.md`](../deployment/docker.md)).

## Prérequis

- Docker Desktop (pour Postgres/Redis, voire ChromaDB/ai-core en conteneur)
- Python 3.11 (la version ciblée par `ai-core.Dockerfile`). Une version plus
  récente fonctionne pour l'API et les tests, mais **pas** pour ChromaDB (voir
  la note en fin de document).

## 1. Services de fond (Postgres, Redis)

```bash
cd infrastructure/docker
docker-compose -f docker-compose.dev.yml up -d postgres redis
```

Attendre que les deux soient `healthy` :

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

> ChromaDB en conteneur n'est **pas nécessaire** pour ce chemin : le code de
> l'application (`app/services/rag/factory.py::get_vector_store`) utilise un
> client ChromaDB **embarqué** (persistance locale via `persist_directory`),
> jamais un client HTTP vers un serveur ChromaDB. Le service `chromadb` du
> `docker-compose.dev.yml` n'est donc consommé par aucun code applicatif à ce
> jour — voir [`docs/deployment/docker.md`](../deployment/docker.md) pour le
> détail.

## 2. Variables d'environnement

Copier `.env.example` vers `.env` dans `src/ai-core/` et renseigner au
minimum `DB_PASSWORD`, `SECURITY_JWT_SECRET_KEY`,
`SECURITY_API_KEY_ENCRYPTION_KEY` (32+ caractères,
`python -c "import secrets; print(secrets.token_hex(32))"`).

Charger le fichier avec un outil qui respecte les guillemets JSON (le shell
`source`/`export` les strip, ce qui casse le parsing de champs comme
`SECURITY_CORS_ORIGINS` s'ils sont au format JSON) :

```bash
cd src/ai-core
./.venv/bin/dotenv -f .env run -- ./.venv/bin/python -m uvicorn app.main:app --reload
```

`SECURITY_CORS_ORIGINS` accepte aussi bien `a,b` (comme documenté dans
`.env.example`) que `["a","b"]` — voir le validateur ajouté dans
`app/core/config/settings.py`.

## 3. Migrations

```bash
cd src/ai-core
DB_HOST=localhost DB_PORT=5432 DB_NAME=saas_ecommerce DB_USER=saas_user \
DB_PASSWORD=<mot de passe> SECURITY_JWT_SECRET_KEY=<32+ car.> LLM_PROVIDER=mock \
./.venv/bin/alembic upgrade head
```

Vérifier : `alembic current` doit afficher `41a15dea72c1 (head)`.

> **Note** : `app/infrastructure/database/migrations/versions/002_add_tenant_composite_indexes.py`
> existe dans le repo mais est **hors de `script_location`** (`alembic/`
> configuré dans `alembic.ini`). Cette migration n'est jamais exécutée par
> `alembic upgrade head` et son `down_revision` (`001_initial`) ne correspond à
> aucune migration réelle de la chaîne. Les index composites `(tenant_id, ...)`
> qu'elle décrit comme "CRITIQUES" pour l'isolation multi-tenant ne sont donc
> **pas appliqués** en l'état.

## 4. Peupler des données de démonstration

```bash
cd src/ai-core
DB_HOST=localhost DB_PORT=5432 DB_NAME=saas_ecommerce DB_USER=saas_user \
DB_PASSWORD=<mot de passe> LLM_PROVIDER=mock SECURITY_JWT_SECRET_KEY=<32+ car.> \
SECURITY_API_KEY_ENCRYPTION_KEY=<32+ car.> \
./.venv/bin/python -m scripts.seed_demo_data
```

Crée un tenant de démo, 5 produits (chaussures/accessoires running), les
indexe dans le vector store, puis affiche l'id du tenant à utiliser :

```
Tenant de démonstration prêt: <uuid>
Header pour tester l'API en dev: -H "X-Tenant-ID: <uuid>"
```

## 5. Lancer le serveur et tester

```bash
./.venv/bin/dotenv -f .env run -- ./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En mode `ENVIRONMENT=development`, le header `X-Tenant-ID: <uuid>` (obtenu à
l'étape 4) suffit à s'authentifier — pas besoin d'API key réelle.

```bash
curl -s http://127.0.0.1:8000/api/v1/chat/message \
  -H "X-Tenant-ID: <uuid>" -H "Content-Type: application/json" \
  -d '{"message": "Bonjour, avez-vous des chaussures de trail ?", "use_rag": true}'
```

## Limite connue : embeddings mock ne sont pas sémantiques

`LLM_PROVIDER=mock` utilise `MockEmbeddingService`
(`app/services/rag/embedding_service.py`), qui **hashe la chaîne complète en
SHA256** pour dériver un vecteur pseudo-aléatoire déterministe. Ce vecteur n'a
**aucune signification sémantique** : deux textes proches en sens (« chaussures
de running » / « chaussures pour courir ») n'ont pas de vecteurs proches — seul
un texte strictement identique retrouve un score de similarité de 1.0.

Conséquence : en mode mock, une recherche RAG en langage naturel retourne
généralement **0 résultat** (le pipeline fonctionne, mais ne peut pas
démontrer de pertinence sémantique). `scripts/seed_demo_data.py` le vérifie
avec une requête reprenant le texte exact indexé (preuve mécanique du
pipeline embed → store → query → rank), pas avec une requête naturelle.

Pour une démonstration de pertinence sémantique réelle, il faut
`LLM_PROVIDER=openai` avec une clé API valide (coût marginal pour un petit
catalogue de démo), ou un service d'embedding local (non implémenté à ce
jour — seuls `MockEmbeddingService` et un `EmbeddingService` basé sur l'API
OpenAI existent dans `app/services/rag/embedding_service.py`).

## Limite connue : réindexation toujours complète

`ProductIndexer.index_all_products(skip_unchanged=True)` (le défaut) est censé
sauter les produits inchangés via leur `content_hash`, mais
`_get_existing_hashes()` retourne toujours un dictionnaire vide (voir le
commentaire dans `product_indexer.py` : implémentation reconnue comme
incomplète). En pratique, chaque réindexation régénère **tous** les
embeddings du catalogue, même sans changement — un point d'attention pour le
coût si l'on passe un jour à des embeddings OpenAI facturés à l'usage.

## Python 3.14 vs 3.11 — pourquoi ça compte

Ce runbook a été validé avec un venv Python 3.14 (macOS récent) pour tout ce
qui touche FastAPI/SQLAlchemy/Postgres. **ChromaDB (`chromadb==0.4.22`)
n'importe pas sous Python 3.14** : sa dépendance `pydantic.v1` (compat shim)
et une partie de son code (`chromadb/api/types.py`) référencent `np.float_`,
supprimé dans NumPy 2.0. `get_vector_store()` intercepte cette exception et
bascule automatiquement sur `InMemorySearchableVectorStore` (dégradation
silencieuse, journalisée en `WARNING`) — l'API continue de fonctionner, mais
sans persistance entre redémarrages du processus.

Pour une persistance réelle du vector store, utiliser le conteneur `ai-core`
(Python 3.11, voir [`docs/deployment/docker.md`](../deployment/docker.md)),
où ChromaDB s'importe et fonctionne correctement — voir
[la note sur la correction de l'API ChromaDB](../deployment/docker.md#chromadb-persistance-réelle)
ci-dessous : même sous Python 3.11, ChromaDB nécessitait une correction de
code pour fonctionner (l'ancienne configuration `chroma_db_impl=` était
supprimée par chromadb lui-même), désormais appliquée et vérifiée avec
persistance réelle à travers un redémarrage de conteneur.
