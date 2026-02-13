# Security Architecture Guide

## Vue d'Ensemble

Ce document décrit l'architecture de sécurité production-ready du SaaS.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          SECURITY ARCHITECTURE                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Requête API                                                                     │
│       │                                                                          │
│       ▼                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  1. RATE LIMITING (Global → Tenant → Endpoint)                           │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│       │                                                                          │
│       ▼                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  2. API KEY VALIDATION (HMAC Signature + Expiration)                     │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│       │                                                                          │
│       ▼                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │  3. TENANT CONTEXT (Multi-tenant isolation)                              │  │
│  └──────────────────────────────────────────────────────────────────────────┘  │
│       │                                                                          │
│       ├─────────────────────────────────────────┐                               │
│       ▼                                         ▼                               │
│  ┌─────────────────────────┐   ┌─────────────────────────────────────────────┐ │
│  │  CLIENT AI              │   │  ADMIN AI                                   │ │
│  │  - Prompt Defense       │   │  - Dry Run Preview                          │ │
│  │  - Output Guardrails    │   │  - Double Confirmation                      │ │
│  │                         │   │  - Human Approval (CRITICAL)                │ │
│  └─────────────────────────┘   └─────────────────────────────────────────────┘ │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. API Key Security

### Problème Résolu

**Avant (Simple Hash):**
```
api_key = "sk_live_xxxxx"
stored = sha256(api_key)  # Pas d'expiration, pas de signature, vulnérable
```

**Après (HMAC + Expiration + Rotation):**
```
api_key = "sk_live_{tenant}_{key_id}_{random}"
signature = HMAC-SHA256(secret, timestamp + method + path + body_hash)
```

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          API KEY SECURITY                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Format: sk_{type}_{tenant}_{key_id}_{random}                                   │
│  Exemple: sk_live_abc12_k1a2b3_x7y8z9w0...                                      │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────┐            │
│  │  REQUEST SIGNING (Client → Server)                               │            │
│  ├─────────────────────────────────────────────────────────────────┤            │
│  │                                                                  │            │
│  │  Headers:                                                        │            │
│  │    X-API-Key: sk_live_abc12_k1a2b3_xxx                          │            │
│  │    X-Timestamp: 1707750000 (Unix)                                │            │
│  │    X-Signature: hmac_sha256(secret, canonical_request)          │            │
│  │                                                                  │            │
│  │  Canonical Request:                                              │            │
│  │    {timestamp}\n{METHOD}\n{path}\n{body_hash}                   │            │
│  │                                                                  │            │
│  └─────────────────────────────────────────────────────────────────┘            │
│                                                                                  │
│  Validations:                                                                    │
│  ✓ Format de la clé                                                             │
│  ✓ Clé active et non expirée                                                    │
│  ✓ Signature HMAC valide                                                        │
│  ✓ Timestamp dans fenêtre (±5 min)                                              │
│  ✓ IP dans whitelist (si configuré)                                             │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Usage

```python
from app.core.security import APIKeyManager, APIKeyType, APIClientSigner

# === SERVEUR ===

# Créer une clé
manager = APIKeyManager(db, cache)
key_with_secret = await manager.create_key(
    tenant_id="tenant_123",
    key_type=APIKeyType.LIVE,
    name="Production API Key",
    expiry_days=365,
)

# Afficher UNE SEULE FOIS
print(f"API Key: {key_with_secret.full_key}")
print(f"Secret: {key_with_secret.secret}")

# Valider une requête
api_key, error = await manager.validate_request(
    api_key=request.headers["X-API-Key"],
    signature=request.headers["X-Signature"],
    timestamp=int(request.headers["X-Timestamp"]),
    method=request.method,
    path=request.path,
    body=request.body,
    client_ip=request.client.host,
)

if error:
    raise HTTPException(401, error)


# === CLIENT ===

# Signer les requêtes
signer = APIClientSigner(api_key, secret)
headers = signer.sign_request("POST", "/api/v1/chat", body)
response = requests.post(url, headers=headers, data=body)
```

### Rotation de Clé

```python
# Rotation sans downtime (dual key pendant 24h)
new_key = await manager.rotate_key(
    key_id="old_key_id",
    rotated_by="admin@example.com",
)

# L'ancienne clé reste valide pendant 24h
# La nouvelle clé devient primaire immédiatement
```

---

## 2. Rate Limiting Avancé

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          RATE LIMITING LAYERS                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Requête                                                                         │
│     │                                                                            │
│     ▼                                                                            │
│  ┌─────────────────────────────────────┐                                        │
│  │ LAYER 1: GLOBAL (per IP)            │  Protection DDoS                       │
│  │ 1000 req/min par IP                 │  Sliding window                        │
│  └──────────────┬──────────────────────┘                                        │
│                 │                                                                │
│                 ▼                                                                │
│  ┌─────────────────────────────────────┐                                        │
│  │ LAYER 2: TENANT (per plan)          │  Selon abonnement                      │
│  │ Starter: 30/min                     │                                        │
│  │ Professional: 100/min               │                                        │
│  │ Enterprise: 300/min                 │                                        │
│  └──────────────┬──────────────────────┘                                        │
│                 │                                                                │
│                 ▼                                                                │
│  ┌─────────────────────────────────────┐                                        │
│  │ LAYER 3: ENDPOINT                   │  Limites spécifiques                   │
│  │ /chat: 60/min                       │                                        │
│  │ /admin/command: 10/min              │                                        │
│  │ /sync: 5/min                        │                                        │
│  └──────────────┬──────────────────────┘                                        │
│                 │                                                                │
│                 ▼                                                                │
│  ┌─────────────────────────────────────┐                                        │
│  │ BURST ALLOWANCE                     │  Pics temporaires                      │
│  │ +50% pendant 10s                    │                                        │
│  └─────────────────────────────────────┘                                        │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Algorithme: Sliding Window

```
Fenêtre glissante de 60 secondes:

    Maintenant = T
    Fenêtre = [T-60s, T]
    
    ┌───────────────────────────────────────────────────────────┐
    │  · · · · · · · · · · ·│· · · · · · · · · · · ·│· · · · ·  │
    │  <---- Hors fenêtre --│--- Dans la fenêtre --│-> Nouvelle │
    │  (supprimé)           │  (compté)            │   requête  │
    └───────────────────────────────────────────────────────────┘
                           T-60s                    T
```

### Usage

```python
from app.core.security import AdvancedRateLimiter, RateLimitConfig

# Configuration
config = RateLimitConfig(
    global_limit_per_minute=1000,
    tenant_limits={
        "starter": 30,
        "professional": 100,
        "enterprise": 300,
    },
    endpoint_limits={
        "/api/v1/chat": 60,
        "/api/v1/admin/command": 10,
    },
    burst_multiplier=1.5,
)

limiter = AdvancedRateLimiter(redis_client, config)

# Vérifier
result = await limiter.check(
    client_ip="1.2.3.4",
    tenant_id="tenant_123",
    tenant_plan="professional",
    endpoint="/api/v1/chat",
)

if not result.allowed:
    raise HTTPException(429, headers=result.to_headers())
```

### Headers de Réponse

```http
HTTP/1.1 429 Too Many Requests
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1707750060
Retry-After: 30
```

---

## 3. Admin AI Safety

### Workflow de Sécurité

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          ADMIN AI SAFETY WORKFLOW                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Commande Admin: "generate_bulk_coupons"                                        │
│       │                                                                          │
│       ▼                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 1. RISK ASSESSMENT                                                       │   │
│  │    Action: generate_bulk_coupons                                         │   │
│  │    Risk Level: HIGH                                                      │   │
│  │    Approval Type: DOUBLE (double confirmation + délai 60s)              │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│       │                                                                          │
│       ▼                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 2. DRY RUN PREVIEW                                                       │   │
│  │    ┌──────────────────────────────────────────────────────────────────┐ │   │
│  │    │ Affected Items: 500 coupons                                      │ │   │
│  │    │ Discount: 15%                                                     │ │   │
│  │    │ Estimated Cost: 3,750€ (max)                                     │ │   │
│  │    │ Warnings: "High discount may impact margins"                     │ │   │
│  │    └──────────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│       │                                                                          │
│       ▼                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 3. CONFIRMATION REQUEST                                                  │   │
│  │    Token: "abc123..."                                                    │   │
│  │    Expires: 30 min                                                       │   │
│  │    Can Execute At: +60s (délai obligatoire)                             │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│       │                                                                          │
│       ▼ (1ère confirmation)                                                     │
│       │                                                                          │
│       ▼ (2ème confirmation après délai)                                         │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 4. EXECUTION                                                             │   │
│  │    Status: EXECUTING → COMPLETED                                         │   │
│  │    Rollback Available: 24h                                               │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│       │                                                                          │
│       ▼                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 5. AUDIT LOG                                                             │   │
│  │    Who: admin@example.com                                                │   │
│  │    What: generate_bulk_coupons (500 coupons, 15% discount)              │   │
│  │    When: 2024-02-12T10:30:00Z                                           │   │
│  │    Result: SUCCESS                                                       │   │
│  └──────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Niveaux de Risque

| Risk Level | Actions | Approval | Délai |
|------------|---------|----------|-------|
| **LOW** | Analytics, Reports | Aucune | 0 |
| **MEDIUM** | Suggestions, Segmentation | Simple confirmation | 0 |
| **HIGH** | Bulk coupons, Prix | Double confirmation | 60-120s |
| **CRITICAL** | Suppressions, Orders | Human approval | Variable |

### Usage

```python
from app.core.security import AdminAISafetySystem

safety = AdminAISafetySystem(cache, db, notifications)

# 1. DRY RUN - Simulation
dry_run = await safety.create_dry_run(
    tenant_id="tenant_123",
    action_name="generate_bulk_coupons",
    parameters={"max_count": 500, "discount_percent": 15},
    initiated_by="admin@example.com",
)

print(f"Affected: {dry_run.affected_items_count}")
print(f"Impact: {dry_run.estimated_impact}")
print(f"Warnings: {dry_run.warnings}")

# 2. REQUEST CONFIRMATION
pending = await safety.request_confirmation(
    dry_run=dry_run,
    tenant_id="tenant_123",
    parameters={"max_count": 500, "discount_percent": 15},
    initiated_by="admin@example.com",
    reason="Black Friday promotion",
)

print(f"Confirmation token: {pending.confirmation_token}")
print(f"Can execute at: {pending.can_execute_at}")

# 3. CONFIRM (1ère fois)
pending, error = await safety.confirm_action(
    action_id=pending.id,
    confirmation_token=pending.confirmation_token,
    confirmed_by="admin@example.com",
)

# 4. CONFIRM (2ème fois après délai, pour HIGH risk)
# ... attendre le délai ...
pending, error = await safety.confirm_action(...)

# 5. EXECUTE
result, error = await safety.execute_action(
    action_id=pending.id,
    executor=execute_bulk_coupons,  # Fonction d'exécution
)

# 6. ROLLBACK (si nécessaire, dans les 24h)
await safety.rollback_action(
    action_id=pending.id,
    rollback_by="admin@example.com",
    reason="Erreur de paramètre",
)
```

### Human Approval (CRITICAL)

```python
# Actions CRITICAL nécessitent approbation humaine par un autre admin

# Admin 1 initie
pending = await safety.request_confirmation(...)
# Status: PENDING_HUMAN_APPROVAL

# Notification envoyée aux autres admins

# Admin 2 approuve (doit être différent d'Admin 1)
pending, error = await safety.approve_action(
    action_id=pending.id,
    approver_id="other_admin@example.com",  # ≠ initiated_by
    approval_reason="Approuvé après vérification",
)

# Maintenant l'action peut être exécutée
```

---

## Fichiers Créés

| Fichier | Description |
|---------|-------------|
| `api_key_security.py` | HMAC, expiration, rotation |
| `rate_limiter.py` | Multi-tier rate limiting |
| `admin_safety.py` | Dry run, confirmation, human approval |

---

## Checklist de Sécurité

### API Keys
- [x] Format structuré avec metadata
- [x] HMAC signature validation
- [x] Timestamp anti-replay (±5 min)
- [x] Expiration automatique
- [x] Rotation sans downtime
- [x] IP whitelist optionnel
- [x] Scopes/permissions

### Rate Limiting
- [x] Protection DDoS (per IP)
- [x] Limites par plan tenant
- [x] Limites par endpoint
- [x] Burst allowance
- [x] Headers standards (X-RateLimit-*)
- [x] Retry-After header

### Admin AI Safety
- [x] Risk assessment automatique
- [x] Dry run preview obligatoire
- [x] Double confirmation (HIGH risk)
- [x] Human approval (CRITICAL risk)
- [x] Délai obligatoire avant exécution
- [x] Rollback support
- [x] Audit logging complet

