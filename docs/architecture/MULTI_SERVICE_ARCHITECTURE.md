# 🏗️ Architecture Multi-Services - SaaS AI E-commerce

## Évolution Architecturale: Du Monolithe aux Microservices

### Problème Initial: Single Point of Failure

L'architecture monolithique initiale présentait plusieurs problèmes critiques:

```
❌ ARCHITECTURE MONOLITHIQUE (AVANT)
┌─────────────────────────────────────────────────────────────────────┐
│                        AI-CORE SERVICE                               │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ Client Chat │  │ Admin AI    │  │ Sync/Embed  │                 │
│  │   Agent     │  │   Agent     │  │   Worker    │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│        ▲               ▲                ▲                           │
│        │               │                │                           │
│        └───────────────┴────────────────┘                           │
│                        │                                             │
│              SINGLE PROCESS - BLOCKED                                │
│                                                                      │
│  ⚠️ Problèmes:                                                      │
│  • Scaling indépendant impossible                                   │
│  • Admin AI peut bloquer Client AI                                  │
│  • Embedding sync impacte latence chat                              │
│  • Un crash = tout le système down                                  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Architecture Corrigée: 3 Services Indépendants

```
✅ ARCHITECTURE MULTI-SERVICES (APRÈS)

┌──────────────────────────────────────────────────────────────────────────────────┐
│                                 NGINX GATEWAY                                     │
│                                                                                   │
│   /api/v1/chat/*         /api/v1/admin/*         (Internal Queue)               │
│   /api/v1/recommendations/*  /api/v1/analytics/*                                 │
│   /api/v1/coupons/*      /api/v1/tenants/*                                       │
│   /api/v1/faq/*                                                                   │
│        │                       │                        │                         │
└────────┼───────────────────────┼────────────────────────┼─────────────────────────┘
         │                       │                        │
         ▼                       ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│                 │    │                 │    │                 │
│  AI-CHAT        │    │  AI-ADMIN       │    │  AI-SYNC        │
│  SERVICE        │    │  SERVICE        │    │  SERVICE        │
│                 │    │                 │    │                 │
│  Port: 8001     │    │  Port: 8002     │    │  (Worker)       │
│  Workers: 4     │    │  Workers: 2     │    │  Workers: 1-N   │
│                 │    │                 │    │                 │
│ ┌─────────────┐ │    │ ┌─────────────┐ │    │ ┌─────────────┐ │
│ │ Chat        │ │    │ │ Admin       │ │    │ │ Embedding   │ │
│ │ Recommend   │ │    │ │ Analytics   │ │    │ │ Generator   │ │
│ │ Coupons     │ │    │ │ Reports     │ │    │ │             │ │
│ │ FAQ         │ │    │ │ Tenants     │ │    │ │ Catalog     │ │
│ └─────────────┘ │    │ └─────────────┘ │    │ │ Sync        │ │
│                 │    │                 │    │ │             │ │
│ Optimisé pour:  │    │ Optimisé pour:  │    │ │ Bulk Ops    │ │
│ • Latence min   │    │ • Requêtes      │    │ └─────────────┘ │
│ • Throughput ↑  │    │   analytiques   │    │                 │
│                 │    │ • Opérations    │    │ Optimisé pour:  │
│                 │    │   complexes     │    │ • Background    │
│                 │    │                 │    │ • Async         │
│                 │    │                 │    │ • Batch         │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         │                      │                      │
         └──────────────────────┼──────────────────────┘
                                │
                     ┌──────────┴──────────┐
                     │                     │
                     ▼                     ▼
              ┌─────────────┐       ┌─────────────┐
              │   Redis     │       │   MySQL     │
              │   Queue     │       │             │
              │   Cache     │       │             │
              │   Sessions  │       │             │
              └─────────────┘       └─────────────┘
                     │
                     ▼
              ┌─────────────┐
              │  ChromaDB   │
              │  (Vectors)  │
              └─────────────┘
```

---

## Communication Inter-Services via Redis Streams

### Pourquoi Redis Streams (vs RabbitMQ/Kafka)?

| Critère | Redis Streams | RabbitMQ | Kafka |
|---------|---------------|----------|-------|
| **Complexité** | Faible | Moyenne | Haute |
| **Infra supplémentaire** | Non (Redis déjà utilisé) | Oui | Oui |
| **Suffisant pour PFE** | ✅ Oui | ✅ Oui | ❌ Overkill |
| **Consumer Groups** | ✅ Oui | ✅ Oui | ✅ Oui |
| **Persistance** | ✅ Oui | ✅ Oui | ✅ Oui |
| **Learning curve** | Faible | Moyenne | Haute |

### Flux de Messages

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           REDIS STREAMS ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  QUEUES:                                                                        │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                                                                           │  │
│  │  queue:embedding:requests    ─────►  AI-SYNC-SERVICE                     │  │
│  │  (Product/FAQ embeddings)            (Consumer Group: sync-service)      │  │
│  │                                                                           │  │
│  │  queue:catalog:sync          ─────►  AI-SYNC-SERVICE                     │  │
│  │  (Catalog sync requests)             (Consumer Group: sync-service)      │  │
│  │                                                                           │  │
│  │  queue:events:audit          ─────►  ALL SERVICES                        │  │
│  │  (Audit events)                      (Pub/Sub via Streams)               │  │
│  │                                                                           │  │
│  │  queue:dlq                   ─────►  MONITORING                          │  │
│  │  (Dead Letter Queue)                 (Failed messages)                   │  │
│  │                                                                           │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│                                                                                  │
│  FLOW EXAMPLE - Product Catalog Update:                                         │
│                                                                                  │
│  ┌────────────┐    ┌────────────┐    ┌────────────┐    ┌────────────┐          │
│  │ PrestaShop │───►│ AI-ADMIN   │───►│  Redis     │───►│ AI-SYNC    │          │
│  │  Webhook   │    │  SERVICE   │    │  Stream    │    │  SERVICE   │          │
│  └────────────┘    └────────────┘    └────────────┘    └────────────┘          │
│        │                │                  │                  │                 │
│        │                │                  │                  │                 │
│   1. Product       2. Validate &      3. Queue         4. Generate              │
│      updated          Persist          message           embeddings             │
│                                                               │                 │
│                                                               ▼                 │
│                                                        ┌────────────┐           │
│                                                        │  ChromaDB  │           │
│                                                        │  (Store)   │           │
│                                                        └────────────┘           │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Détail des Services

### 1. AI-CHAT-SERVICE (Port 8001)

**Responsabilités:**
- Conversations chatbot client
- Recommandations produits
- Génération de coupons personnalisés
- Réponses FAQ

**Caractéristiques:**
- Optimisé pour latence minimale (< 3s P95)
- 4 workers Uvicorn (scaling horizontal possible)
- Rate limit: 60 req/min par tenant
- Cache agressif (Redis) pour réponses fréquentes

**Endpoints:**
```
POST /api/v1/chat/message         # Envoyer un message
GET  /api/v1/chat/history         # Historique conversation
POST /api/v1/recommendations      # Obtenir recommandations
POST /api/v1/coupons/generate     # Générer coupon
GET  /api/v1/faq/search           # Rechercher FAQ
```

### 2. AI-ADMIN-SERVICE (Port 8002)

**Responsabilités:**
- Commandes IA admin
- Analytics et rapports
- Gestion des tenants
- Opérations bulk (délégation au Sync Service)

**Caractéristiques:**
- Timeouts plus longs (120s pour analytics)
- 2 workers (opérations plus lourdes)
- Rate limit: 30 req/min
- Validation stricte + audit logging

**Endpoints:**
```
POST /api/v1/admin/command        # Exécuter commande IA
GET  /api/v1/admin/actions        # Historique actions
GET  /api/v1/analytics/dashboard  # Dashboard metrics
POST /api/v1/analytics/report     # Générer rapport
GET  /api/v1/operations/{id}/status # Status opération bulk
```

### 3. AI-SYNC-SERVICE (Background Worker)

**Responsabilités:**
- Génération d'embeddings (asynchrone)
- Synchronisation catalogue (PrestaShop, etc.)
- Opérations bulk (coupons, prix)
- Traitement des événements

**Caractéristiques:**
- Pas d'endpoint HTTP (worker uniquement)
- Consumer de Redis Streams
- Scalable horizontalement (multiple workers)
- Retry automatique avec DLQ

**Messages traités:**
```
GENERATE_PRODUCT_EMBEDDINGS   # Nouveaux produits → embeddings
GENERATE_FAQ_EMBEDDINGS       # Nouvelles FAQs → embeddings
DELETE_EMBEDDINGS             # Suppression embeddings
SYNC_CATALOG                  # Sync depuis e-commerce
BULK_COUPON_GENERATION        # Génération coupons en masse
BULK_PRICE_UPDATE             # Mise à jour prix en masse
```

---

## Avantages de cette Architecture

### 1. Isolation des Pannes
```
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  ❌ Sync Service crash                                             │
│     └──► Chat Service continue de fonctionner                      │
│     └──► Admin Service continue de fonctionner                     │
│     └──► Seuls les nouveaux embeddings sont retardés               │
│                                                                    │
│  ❌ Admin Service surchargé                                        │
│     └──► Chat Service non impacté                                  │
│     └──► Clients peuvent toujours utiliser le chatbot              │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 2. Scaling Indépendant
```
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  Scénario: Black Friday (trafic chat x10)                         │
│                                                                    │
│  ┌──────────────┐                                                  │
│  │ Chat Service │  4 workers → 16 workers (docker-compose scale)   │
│  └──────────────┘                                                  │
│                                                                    │
│  ┌──────────────┐                                                  │
│  │Admin Service │  2 workers → 2 workers (inchangé)                │
│  └──────────────┘                                                  │
│                                                                    │
│  ┌──────────────┐                                                  │
│  │ Sync Service │  1 worker → 3 workers (backlog embeddings)       │
│  └──────────────┘                                                  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

### 3. Déploiement Indépendant
```
┌────────────────────────────────────────────────────────────────────┐
│                                                                    │
│  Mise à jour Chat Service (nouvelle feature):                      │
│  └──► Deploy chat-service v1.2.0                                   │
│  └──► Admin et Sync restent en v1.1.0                              │
│  └──► Zero downtime pour les autres services                       │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## Commandes Docker Compose

### Démarrage Complet
```bash
# Tous les services
docker-compose -f docker-compose.multi-service.yml up -d

# Avec monitoring
docker-compose -f docker-compose.multi-service.yml --profile monitoring up -d
```

### Scaling
```bash
# Scaler le chat service
docker-compose -f docker-compose.multi-service.yml up -d --scale ai-chat-service=4

# Scaler le sync service (plus de workers)
docker-compose -f docker-compose.multi-service.yml up -d --scale ai-sync-service=3
```

### Logs par Service
```bash
# Chat service uniquement
docker-compose -f docker-compose.multi-service.yml logs -f ai-chat-service

# Admin service
docker-compose -f docker-compose.multi-service.yml logs -f ai-admin-service

# Sync service (workers)
docker-compose -f docker-compose.multi-service.yml logs -f ai-sync-service
```

---

## Migration depuis Monolithe

Pour migrer progressivement:

### Phase 1: Préparation (actuelle)
- ✅ Créer les 3 services séparés
- ✅ Implémenter Redis Queue
- ✅ Configurer Nginx routing
- ✅ Mettre à jour Prometheus

### Phase 2: Déploiement Parallèle
```bash
# Démarrer les nouveaux services en parallèle du monolithe
docker-compose -f docker-compose.yml -f docker-compose.multi-service.yml up -d
```

### Phase 3: Bascule Progressive
- Router 10% trafic chat vers nouveau service
- Monitorer métriques
- Augmenter progressivement jusqu'à 100%

### Phase 4: Décommissionnement
- Arrêter l'ancien service monolithique
- Supprimer les anciennes configurations

---

## Conclusion

Cette architecture multi-services résout les problèmes critiques:

| Problème | Solution |
|----------|----------|
| Single Point of Failure | 3 services isolés |
| Scaling impossible | Scaling indépendant par service |
| Admin bloque Chat | Services séparés |
| Embedding impacte latence | Sync service asynchrone |

**Adapté au PFE car:**
- Utilise Redis (déjà en place)
- Pas de nouvelle infrastructure (pas Kafka/RabbitMQ)
- Docker Compose suffit (pas Kubernetes)
- Démontre les concepts microservices sans complexité excessive

