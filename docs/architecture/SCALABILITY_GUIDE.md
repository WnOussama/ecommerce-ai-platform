# Scalability Architecture Guide

## Vue d'Ensemble

Ce document décrit l'architecture de scalabilité production-ready.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         SCALABILITY ARCHITECTURE                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│                           LOAD BALANCER                                          │
│                               │                                                  │
│              ┌────────────────┼────────────────┐                                │
│              │                │                │                                │
│              ▼                ▼                ▼                                │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                       │
│  │ Chat Pod 1   │  │ Chat Pod 2   │  │ Chat Pod N   │  ← HPA (2-10 pods)      │
│  │ (Stateless)  │  │ (Stateless)  │  │ (Stateless)  │                          │
│  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘                       │
│          │                  │                  │                                │
│          └──────────────────┼──────────────────┘                                │
│                             │                                                    │
│                             ▼                                                    │
│                    ┌─────────────────┐                                          │
│                    │     REDIS       │  ← Tout le contexte ici                  │
│                    │  (Sessions,     │                                          │
│                    │   Context,      │                                          │
│                    │   Cache)        │                                          │
│                    └─────────────────┘                                          │
│                             │                                                    │
│              ┌──────────────┼──────────────┐                                    │
│              │                             │                                     │
│              ▼                             ▼                                     │
│  ┌───────────────────────┐    ┌───────────────────────────────────┐            │
│  │    MySQL PRIMARY      │    │       MySQL REPLICAS              │            │
│  │    (Write)            │    │  ┌─────────┐  ┌─────────┐        │            │
│  │                       │───▶│  │Replica 1│  │Replica 2│        │            │
│  └───────────────────────┘    │  └─────────┘  └─────────┘        │            │
│                               └───────────────────────────────────┘            │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. Stateless Design

### Principe

**Aucun état local** dans les pods FastAPI. Tout est dans Redis.

```python
# ❌ AVANT (Stateful)
class ChatService:
    def __init__(self):
        self._sessions = {}  # État local = problème!
    
    async def handle(self, session_id):
        session = self._sessions.get(session_id)  # Fonctionne seulement si même pod

# ✅ APRÈS (Stateless)
class ChatService:
    def __init__(self, session_store: StatelessSessionStore):
        self._store = session_store  # Redis
    
    async def handle(self, session_id):
        session = await self._store.get_session(session_id)  # N'importe quel pod
```

### Données dans Redis

| Clé | Contenu | TTL |
|-----|---------|-----|
| `session:{tenant}:{id}` | Session utilisateur | 1h |
| `conv:{tenant}:{id}` | Contexte conversation | 24h |
| `ctx:{tenant}:{key}` | Contexte générique | 30min |
| `ratelimit:{tenant}:{ip}` | Compteurs rate limit | 1min |

### Usage

```python
from app.infrastructure.cache.stateless_session import (
    StatelessSessionStore, RequestContextManager
)

# Initialisation
store = StatelessSessionStore(redis_client)
ctx_manager = RequestContextManager(store)

# Dans chaque requête
async def handle_request(tenant_id: str, session_id: str):
    ctx = await ctx_manager.create_context(
        tenant_id=tenant_id,
        session_id=session_id,
    )
    
    # ctx.session et ctx.conversation sont chargés depuis Redis
    # N'importe quel pod peut traiter cette requête
    
    # À la fin
    await ctx_manager.save_context(ctx)
```

---

## 2. Horizontal Scaling (Kubernetes)

### Horizontal Pod Autoscaler (HPA)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  scaleTargetRef:
    name: ai-chat-service
  
  minReplicas: 2
  maxReplicas: 10
  
  metrics:
    # CPU
    - type: Resource
      resource:
        name: cpu
        target:
          averageUtilization: 70
    
    # Memory
    - type: Resource
      resource:
        name: memory
        target:
          averageUtilization: 80
    
    # Custom: RPS
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          averageValue: "100"
```

### Comportement de Scaling

```
Scale UP:
  - Stabilization: 60s (évite flapping)
  - Max +2 pods ou +50% à la fois
  
Scale DOWN:
  - Stabilization: 300s (5 min, conservateur)
  - Max -1 pod à la fois
```

### Workers avec KEDA (Queue-based)

```yaml
# Scale basé sur la longueur de la queue Redis
triggers:
  - type: redis-streams
    metadata:
      stream: jobs:embeddings
      lagCount: "10"  # Scale si lag > 10 messages
```

---

## 3. Read Replicas MySQL

### Architecture

```
Application
    │
    ├── SELECT (80%) ────────────────┐
    │                                │
    ├── INSERT/UPDATE (20%) ──┐      │
    │                         │      │
    ▼                         ▼      ▼
┌─────────────┐      ┌─────────────────────────┐
│   PRIMARY   │      │      REPLICAS (2+)      │
│   (Write)   │─────▶│  (Read, Load Balanced)  │
└─────────────┘      └─────────────────────────┘
```

### Routing Automatique

```python
from app.infrastructure.database.read_replica import DatabaseRouter

router = DatabaseRouter(config)

# Lecture → Replica (automatique)
async with router.session() as session:
    result = await session.execute(select(Customer))

# Écriture → Primary (automatique)
async with router.write_session() as session:
    session.add(Customer(...))

# Transaction → Primary (toutes les opérations)
async with router.transaction() as session:
    # Tout va sur Primary
    pass

# Lecture fraîche → Primary (forcé)
async with router.session(force_primary=True) as session:
    # Pour éviter replication lag
    pass
```

### Health Check & Failover

```python
# Vérification santé
health = await router.health_check()
# {
#   "primary": {"healthy": True},
#   "replicas": [
#     {"healthy": True, "replication_lag_ms": 50},
#     {"healthy": True, "replication_lag_ms": 80}
#   ]
# }

# Failover automatique si replica unhealthy
# → Requêtes redirigées vers autres replicas
# → Si aucun replica → fallback sur Primary
```

---

## 4. Configuration Kubernetes

### Services

| Service | Pods | HPA | Scaling |
|---------|------|-----|---------|
| ai-chat-service | 2-10 | CPU 70%, RPS 100 | Horizontal |
| ai-admin-service | 2-5 | CPU 70% | Horizontal |
| ai-sync-workers | 1-10 | Queue lag | KEDA |

### Resources

| Service | CPU Request | CPU Limit | Memory Request | Memory Limit |
|---------|-------------|-----------|----------------|--------------|
| Chat | 500m | 2000m | 512Mi | 2Gi |
| Admin | 250m | 1500m | 256Mi | 1.5Gi |
| Workers | 500m | 2000m | 512Mi | 2Gi |

### High Availability

```yaml
# Pod Anti-Affinity: spread across nodes
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: kubernetes.io/hostname

# Pod Disruption Budget: always keep 1+ running
apiVersion: policy/v1
kind: PodDisruptionBudget
spec:
  minAvailable: 1
```

---

## 5. Checklist Scalabilité

### Stateless Design
- [x] Sessions dans Redis
- [x] Contexte conversation dans Redis
- [x] Cache dans Redis
- [x] Rate limiting dans Redis
- [x] Pas d'état local dans les pods

### Horizontal Scaling
- [x] HPA configuré (CPU, Memory, Custom metrics)
- [x] KEDA pour workers (queue-based)
- [x] Pod Anti-Affinity (spread across nodes)
- [x] PodDisruptionBudget

### Database
- [x] Read Replicas MySQL
- [x] Routing automatique Read/Write
- [x] Health check replicas
- [x] Failover automatique
- [x] Replication lag monitoring

### Kubernetes
- [x] Rolling updates (zero downtime)
- [x] Liveness/Readiness probes
- [x] Resource limits
- [x] Network policies
- [x] Ingress avec TLS

---

## Fichiers Créés

| Fichier | Description |
|---------|-------------|
| `stateless_session.py` | Session store Redis |
| `read_replica.py` | Router MySQL Read/Write |
| `k8s/base/chat-service.yaml` | Deployment + HPA Chat |
| `k8s/base/admin-service.yaml` | Deployment + HPA Admin |
| `k8s/base/sync-workers.yaml` | Workers + KEDA |
| `k8s/base/databases.yaml` | Redis + MySQL + Replicas |
| `k8s/base/shared.yaml` | ConfigMaps, RBAC, Ingress |

---

## Commandes Utiles

```bash
# Appliquer la configuration
kubectl apply -k infrastructure/k8s/base/

# Voir les HPA
kubectl get hpa

# Voir les pods et leur distribution
kubectl get pods -o wide

# Voir les métriques
kubectl top pods

# Scale manuel (test)
kubectl scale deployment ai-chat-service --replicas=5

# Voir les events de scaling
kubectl describe hpa ai-chat-service-hpa
```

