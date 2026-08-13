# Discours — Restitution technique

Script slide par slide pour `restitution.html`. Ton : oral, technique, direct — c'est une revue avec un expert, pas un pitch. Environ 15-18 minutes à un rythme normal.

---

**1 — Titre**

> Bonjour. Je vais vous présenter le travail technique réalisé pendant mon stage chez Prodexo : un assistant IA conversationnel pour l'e-commerce, avec son backoffice d'administration. Cette présentation est plus détaillée que la soutenance finale — l'objectif aujourd'hui, c'est de valider avec vous les choix d'architecture et d'implémentation avant la restitution devant le jury.

**2 — Sommaire**

> On va parcourir ça en trois temps : d'abord un rappel rapide du projet et de la stack, ensuite l'ensemble des diagrammes de conception — cas d'utilisation, architecture, classes, séquences, déploiement — et enfin des extraits de code réels sur les trois mécanismes les plus sensibles du système, avant de terminer sur les tests et les points que j'aimerais qu'on discute ensemble.

**3 — Rappel du projet**

> Rapidement : c'est une plateforme mono-tenant... pardon, multi-tenant, avec un chat augmenté par recherche vectorielle sur le catalogue produit, un moteur de règles métier pour personnaliser le comportement du bot sans toucher au code, un agent IA d'administration qui exécute des actions sous contrôle, et un backoffice Laravel qui consomme tout ça en REST pur, sans jamais toucher la base de données directement.

**4 — Stack technique**

> Côté backend, FastAPI en asynchrone avec SQLAlchemy 2.0 et Alembic pour les migrations. PostgreSQL est la seule base relationnelle, et c'est le ai-core qui en a la propriété exclusive. ChromaDB pour la recherche vectorielle, Redis pour le rate limiting et pour l'état des actions d'administration en attente. Côté backoffice, Laravel 13 avec Filament et Livewire. Tout tourne en Docker Compose, avec une CI GitHub Actions qui fait tourner les tests, le lint, et des scans de sécurité — Trivy et SonarCloud.

**5 — Diagramme de cas d'utilisation**

> Voici les cas d'utilisation. On a le client final qui interagit avec le chatbot — recherche produit, question, code promo. L'administrateur qui configure les règles et consulte les insights. Et un acteur un peu particulier, l'agent IA d'administration, qui a ses propres cas d'utilisation encadrés — génération de coupons, modification de prix — mais toujours sous supervision humaine.

**6 — Architecture C4**

> Ça, c'est le niveau conteneurs du modèle C4. Le point important : ce n'est pas une architecture microservices. C'est un monolithe FastAPI unique, qui expose quatre modules internes — chat, moteur de règles, agent admin, et insights. Le backoffice ne parle jamais directement à la base : il passe uniquement par l'API REST. J'ai un point à soumettre là-dessus tout à l'heure sur la pertinence de ce choix.

**7 — Diagramme de classes**

> Le diagramme de classes montre les entités du domaine à gauche — tenant, conversation, message, règle, coupon — et à droite les trois blocs métier : le moteur de règles avec son évaluateur pur, l'agent d'administration avec son système de sécurité, et le service d'insights. Je précise que ce diagramme a été entièrement revu pendant le stage : la version précédente décrivait encore une architecture d'agent qui a été supprimée.

**8 — Séquence chat**

> Voici le cycle de vie complet d'une requête de chat. Le message passe d'abord par les garde-fous d'entrée, puis par la classification d'intention, puis par le moteur de règles. Si une règle de type réponse automatique matche, on court-circuite complètement le LLM — c'est le chemin de gauche. Sinon on continue vers la recherche RAG, l'appel au modèle de langage, les garde-fous de sortie, et la persistance.

**9 — Séquence agent admin**

> Ça, c'est le cas concret de la génération de coupons en masse, qui est une action à risque élevé. On voit les trois phases : d'abord une exécution à blanc qui simule l'impact sans rien modifier, ensuite une confirmation explicite de l'administrateur, et enfin l'exécution réelle qui crée les coupons en base. L'état intermédiaire est stocké dans Redis, pas en mémoire — j'y reviens dans les extraits de code.

**10 — Diagramme d'activités règles**

> Celui-ci zoome spécifiquement sur la logique d'évaluation des règles : comment on charge les règles actives par priorité, comment chaque condition est testée, et comment on bascule vers l'une des trois actions possibles. C'est une fonction pure, sans aucun accès base de données, ce qui la rend testable de façon exhaustive.

**11 — Diagramme de déploiement**

> Et voici la vue déploiement réelle : sept conteneurs Docker sur un même réseau bridge. Je veux insister sur un point : le conteneur ai-core est le seul à détenir les identifiants PostgreSQL. Ce n'est pas juste une convention de code, c'est une contrainte réseau — le backoffice n'a physiquement pas les credentials pour se connecter à la base.

**12 — Code : évaluation des règles**

> Voici l'extrait réel de l'évaluateur de règles. Ce qui mérite d'être noté : une règle sans aucune des trois conditions connues — intent, keywords_any, keywords_all — ne matche jamais, volontairement. Sans cette garde, une règle mal configurée pourrait intercepter tout le trafic d'un tenant par accident.

**13 — Code : court-circuit dans le chat**

> Ici on voit où le moteur de règles s'intègre dans le endpoint de chat : juste après la classification d'intention, avant tout appel RAG ou LLM. Si l'action est de type canned_response, on retourne directement — le commentaire dans le code est explicite là-dessus, le LLM n'est jamais sollicité sur cette branche. Et c'est vérifié par un test d'intégration avec un faux fournisseur LLM qui espionne les appels.

**14 — Code : registre des risques**

> Et voici le registre fermé des actions administratives. Chaque action a un niveau de risque : faible pour une simple lecture, élevé pour une génération de coupons en masse avec double confirmation et un délai d'une heure entre deux exécutions, critique pour une suppression de données client qui nécessite une approbation humaine. Toute action absente de ce registre est rejetée avant même d'atteindre la logique d'exécution.

**15 — Tests et qualité**

> Sur la qualité : 563 tests côté Python, avec de vrais tests d'intégration contre une base PostgreSQL réelle, pas seulement des mocks. 36 tests côté PHP. Zéro erreur de lint sur la version livrée. Et on a résolu treize CVE détectées par l'analyse de dépendances pendant le stage.

**16 — Points à valider**

> J'aimerais qu'on discute de quatre points avec vous : le choix du monolithe plutôt qu'une décomposition en services, si la liste fermée d'actions pour l'agent admin est suffisante pour vos besoins, l'impact de l'absence de table de commandes sur la suite de la feuille de route analytique, et la stratégie de mise en production au-delà de l'environnement Docker Compose actuel.

**17 — Clôture**

> Voilà pour la partie technique. Je suis preneur de vos retours et de vos questions avant de préparer la version finale pour le jury.

---

*Astuce de présentation : sur les diapositives de diagrammes (5 à 11), cliquer sur l'image l'ouvre en plein écran — utile si l'expert veut voir un détail précis (un port, une condition, un label de relation) sans plisser les yeux sur la version réduite.*
