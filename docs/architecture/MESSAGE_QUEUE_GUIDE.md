# Message Queue Architecture - Redis Streams

## Problème Résolu

**Avant (Redis Pub/Sub - NON FIABLE):**
- Message perdu si subscriber down
- Pas de persistance
- Pas de retry
- Pas d'acknowledgment

**Après (Redis Streams - FIABLE):**
- Messages persistés
- Consumer Groups (scaling horizontal)
- Acknowledgment explicite
- Retry automatique avec backoff exponentiel
- Dead Letter Queue
- Message claiming (récupération après crash)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        REDIS STREAMS ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────┐     ┌─────────────────────────────────────────────┐            │
│  │  PRODUCERS  │     │            REDIS STREAMS                    │            │
│  │             │     │                                             │            │
│  │ Chat Service├────►│ jobs:embeddings    ──┬──► Consumer Group    │            │
│  │ Admin Svc   │     │ jobs:catalog_sync  ──┤    (sync-workers)    │            │
│  │             │     │ jobs:bulk_ops      ──┤         │            │            │
│  └─────────────┘     │ jobs:reports       ──┘         │            │            │
│                      │                                ▼            │            │
│                      │                    ┌───────────────────┐    │            │
│                      │                    │ WORKER INSTANCES  │    │            │
│                      │                    │                   │    │            │
│                      │                    │ worker-1          │    │            │
│                      │                    │ worker-2          │    │            │
│                      │                    │ worker-3          │    │            │
│                      │                    └─────────┬─────────┘    │            │
│                      │                              │              │            │
│                      │                    ┌─────────▼─────────┐    │            │
│                      │                    │  jobs:dlq (DLQ)   │    │            │
│                      │                    │  (Failed jobs)    │    │            │
│                      │                    └───────────────────┘    │            │
│                      │                                             │            │
│                      └─────────────────────────────────────────────┘            │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Streams Disponibles

| Stream | Description | Producteurs | Consommateurs |
|--------|-------------|-------------|---------------|
| `jobs:embeddings` | Génération d'embeddings | Chat, Admin | Sync Worker |
| `jobs:catalog_sync` | Synchronisation catalogue | Admin | Sync Worker |
| `jobs:bulk_ops` | Opérations bulk (coupons, prix) | Admin | Sync Worker |
| `jobs:reports` | Génération de rapports | Admin | Sync Worker |
| `jobs:scheduled` | Jobs différés (retry) | Workers | Workers |
| `jobs:dlq` | Dead Letter Queue | Workers | Admin (manuel) |

## Usage

### Producer (Publier un job)

```python
from app.services.message_queue import (
    ReliableQueue, StreamName, EmbeddingJob
)
import redis.asyncio as redis

# Connexion Redis
redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)
queue = ReliableQueue(redis_client)

# Créer un job
job = EmbeddingJob(
    tenant_id="tenant_123",
    payload={
        "document_type": "product",
        "documents": [
            {"id": "prod_1", "name": "iPhone 15", "description": "..."},
            {"id": "prod_2", "name": "Samsung S24", "description": "..."},
        ],
        "operation": "upsert",
    }
)

# Publier
stream_id = await queue.enqueue(StreamName.EMBEDDINGS, job)
print(f"Job published: {stream_id}")
```

### Consumer (Worker)

```python
from app.services.message_queue import QueueWorker, StreamName, Job
import redis.asyncio as redis

redis_client = redis.from_url("redis://localhost:6379", decode_responses=True)

# Handler pour traiter les jobs
async def handle_embedding_job(job: Job) -> dict:
    print(f"Processing job {job.id} for tenant {job.tenant_id}")
    
    # Traiter le job...
    documents = job.payload.get("documents", [])
    # await rag_service.index_documents(...)
    
    return {"indexed": len(documents)}

# Créer le worker
worker = QueueWorker(
    redis_client=redis_client,
    stream=StreamName.EMBEDDINGS,
    handler=handle_embedding_job,
    group_name="sync-workers",
    consumer_name="worker-1",
)

# Démarrer (boucle infinie)
await worker.run()
```

### Jobs Différés (Scheduled)

```python
# Planifier un job pour dans 5 minutes
await queue.schedule(
    stream=StreamName.EMBEDDINGS,
    job=job,
    delay_seconds=300,  # 5 minutes
)
```

## Retry Logic

```
Tentative 1: Exécution immédiate
     │
     ▼ Échec
Tentative 2: +60s (1 minute)
     │
     ▼ Échec  
Tentative 3: +120s (2 minutes)
     │
     ▼ Échec
Tentative 4: +240s (4 minutes)
     │
     ▼ Échec (max_retries atteint)
     │
     ▼
   DLQ (Dead Letter Queue)
```

### Configuration

```python
from app.services.message_queue import QueueConfig

config = QueueConfig(
    max_retries=3,              # Nombre max de tentatives
    retry_delay_seconds=60,     # Délai initial avant retry
    batch_size=10,              # Jobs traités en batch
    block_timeout_ms=5000,      # Timeout d'attente pour nouveaux jobs
    claim_timeout_ms=30000,     # Timeout avant claiming de jobs abandonnés
    max_stream_length=100000,   # Max messages dans le stream
)
```

## Dead Letter Queue (DLQ)

### Consulter les jobs échoués

```python
from app.services.message_queue import DLQManager

dlq = DLQManager(redis_client)

# Lister les jobs échoués
dead_jobs = await dlq.get_dead_jobs(count=50, tenant_id="tenant_123")

for job in dead_jobs:
    print(f"Job {job['job_id']}: {job['error']}")
```

### Réessayer un job

```python
# Réessayer un job spécifique
success = await dlq.retry_job(stream_id="1234567890-0")

# Purger les vieux jobs (> 7 jours)
deleted = await dlq.purge(older_than_days=7)
```

## Monitoring

### Métriques Redis

```bash
# Longueur de la queue
redis-cli XLEN jobs:embeddings

# Infos consumer group
redis-cli XINFO GROUPS jobs:embeddings

# Messages pending
redis-cli XPENDING jobs:embeddings sync-workers

# Statistiques DLQ
redis-cli XLEN jobs:dlq
```

### Métriques Prometheus

```python
# Métriques automatiquement incrémentées
queue:jobs:embeddings:enqueued      # Jobs ajoutés
queue:jobs:embeddings:completed     # Jobs terminés
queue:jobs:embeddings:dead          # Jobs en DLQ
queue:jobs:embeddings:processing_time_ms  # Temps total de traitement
```

## Scaling

### Horizontal Scaling (Multiple Workers)

```yaml
# docker-compose.yml
services:
  sync-worker:
    image: saas-ai/sync-worker
    deploy:
      replicas: 3  # 3 workers
```

Tous les workers partagent le même Consumer Group → load balancing automatique.

### Par Type de Job

```python
# Worker dédié embeddings (haute priorité)
embedding_worker = QueueWorker(
    stream=StreamName.EMBEDDINGS,
    group_name="embedding-workers",
)

# Worker dédié bulk ops (basse priorité)
bulk_worker = QueueWorker(
    stream=StreamName.BULK_OPERATIONS,
    group_name="bulk-workers",
)
```

## Fichiers Clés

```
app/services/message_queue/
├── __init__.py
├── redis_queue.py        # Legacy (compatibilité)
└── reliable_queue.py     # Nouveau système FIABLE

app/workers/
├── __init__.py
└── sync_worker.py        # Worker principal
```

## Comparaison

| Aspect | Pub/Sub | Redis Streams |
|--------|---------|---------------|
| Persistance | ❌ Non | ✅ Oui |
| Acknowledgment | ❌ Non | ✅ Oui (XACK) |
| Retry | ❌ Manuel | ✅ Automatique |
| DLQ | ❌ Non | ✅ Oui |
| Consumer Groups | ❌ Non | ✅ Oui |
| Message Claiming | ❌ Non | ✅ Oui |
| Ordre garanti | ❌ Non | ✅ FIFO |
| Scaling horizontal | ❌ Limité | ✅ Facile |

