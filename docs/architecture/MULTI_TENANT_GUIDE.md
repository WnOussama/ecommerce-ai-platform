# Multi-Tenant Architecture Guide

## Vue d'Ensemble

Ce document décrit l'architecture multi-tenant production-grade du SaaS AI E-commerce.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        MULTI-TENANT ISOLATION LAYERS                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                        LAYER 1: API MIDDLEWARE                           │   │
│  │                                                                          │   │
│  │   Request → Extract API Key → Validate → Inject TenantContext            │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     LAYER 2: REPOSITORY PATTERN                          │   │
│  │                                                                          │   │
│  │   TenantAwareRepository → Auto tenant_id filter on ALL queries           │   │
│  │                                                                          │   │
│  │   ❌ session.query(Customer).filter(id == x)  # INTERDIT                 │   │
│  │   ✅ repository.get_by_id(x)                   # tenant_id auto           │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     LAYER 3: DATABASE INDEXES                            │   │
│  │                                                                          │   │
│  │   Index composite (tenant_id, column) sur TOUTES les tables              │   │
│  │   → Performance garantie pour queries filtrées                           │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                      │                                          │
│                                      ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │                     LAYER 4: VECTOR STORE (ChromaDB)                     │   │
│  │                                                                          │   │
│  │   Single Collection + Metadata Filter (tenant_id)                        │   │
│  │   → Scalable à 1000+ tenants                                             │   │
│  │                                                                          │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 1. Repository Pattern Multi-Tenant

### Base Repository

```python
from app.infrastructure.database.repositories import (
    TenantContext,
    TenantAwareRepository,
    CustomerRepository,
)

# Créer le contexte tenant (OBLIGATOIRE)
ctx = TenantContext(
    tenant_id="tenant_123",
    user_id="user_456",      # Pour audit
    request_id="req_789",    # Pour tracing
)

# Créer le repository
repo = CustomerRepository(session, ctx)

# TOUTES les queries sont automatiquement filtrées par tenant_id
customers = await repo.get_all()           # WHERE tenant_id = 'tenant_123'
customer = await repo.get_by_id("cust_1")  # WHERE tenant_id = 'tenant_123' AND id = 'cust_1'
```

### Protections Automatiques

| Protection | Description |
|------------|-------------|
| **tenant_id obligatoire** | Impossible de créer un repository sans tenant_id |
| **Filtrage auto** | Toutes les queries SELECT incluent tenant_id |
| **Validation update** | Update/Delete vérifient que l'entité appartient au tenant |
| **Création forcée** | Create force toujours le tenant_id correct |
| **Modification interdite** | Impossible de modifier tenant_id via update |

### Exceptions

```python
from app.infrastructure.database.repositories import (
    TenantIdMissingError,    # tenant_id manquant
    CrossTenantAccessError,  # Accès à données d'un autre tenant
    TenantIsolationError,    # Tentative de modification tenant_id
)
```

## 2. Index Composites MySQL

### Index Obligatoires

Chaque table doit avoir ces index:

```sql
-- Index principal (lookup par ID)
CREATE INDEX idx_{table}_tenant_id ON {table} (tenant_id, id);

-- Index chronologique
CREATE INDEX idx_{table}_tenant_created ON {table} (tenant_id, created_at);

-- Index par statut
CREATE INDEX idx_{table}_tenant_status ON {table} (tenant_id, status);
```

### Migration Alembic

```python
# migrations/versions/002_add_tenant_composite_indexes.py
op.create_index(
    'idx_customer_tenant_id',
    'customers',
    ['tenant_id', 'id'],
)
```

### Performance

Sans index composite:
```
SELECT * FROM customers WHERE tenant_id = 'X' AND id = 'Y'
→ Full table scan + filter 😱
```

Avec index composite:
```
SELECT * FROM customers WHERE tenant_id = 'X' AND id = 'Y'
→ Index seek direct ✅
```

## 3. ChromaDB Single Collection

### Avant (❌ Non scalable)

```
Collections:
├── tenant_001_products
├── tenant_001_faqs
├── tenant_002_products
├── tenant_002_faqs
└── ... (explosion à 1000+ collections)
```

### Après (✅ Scalable)

```
Collections:
├── saas_products     ← Tous les produits, filtrés par metadata
├── saas_faqs         ← Toutes les FAQs, filtrés par metadata
├── saas_policies     ← Toutes les policies
└── saas_conversations
```

### Usage

```python
from app.domain.services.shared import RAGServiceV2, RAGQuery

rag = RAGServiceV2()

# Recherche TOUJOURS filtrée par tenant_id
result = await rag.search(RAGQuery(
    query="téléphone pas cher",
    tenant_id="tenant_123",  # OBLIGATOIRE
    document_types=[DocumentType.PRODUCT],
))

# Le filtre ChromaDB généré:
# where={"tenant_id": "tenant_123"}
```

### Indexation

```python
# Indexer des documents (tenant_id obligatoire dans metadata)
await rag.index_documents(
    tenant_id="tenant_123",
    doc_type=DocumentType.PRODUCT,
    documents=[
        {"id": "prod_1", "name": "iPhone 15", "price": 999},
        {"id": "prod_2", "name": "Samsung S24", "price": 899},
    ],
)

# Documents stockés avec metadata:
# {
#   "tenant_id": "tenant_123",  ← Ajouté automatiquement
#   "name": "iPhone 15",
#   "price": 999,
#   ...
# }
```

## 4. Tests d'Isolation

### Tests Unitaires

```python
# tests/unit/test_tenant_isolation.py

def test_repository_requires_tenant_context():
    """Repository DOIT avoir un TenantContext"""
    with pytest.raises(TenantIdMissingError):
        CustomerRepository(session, None)

def test_cross_tenant_access_blocked():
    """Accès cross-tenant = Exception"""
    ctx = TenantContext(tenant_id="tenant_A")
    repo = CustomerRepository(session, ctx)
    
    # Entité d'un autre tenant
    entity = Customer(tenant_id="tenant_B")
    
    with pytest.raises(CrossTenantAccessError):
        await repo.update(entity)

def test_tenant_id_modification_blocked():
    """Modification tenant_id = Interdit"""
    with pytest.raises(TenantIsolationError):
        await repo.update_by_id("id", {"tenant_id": "other"})
```

### Query Validator

```python
from app.infrastructure.database.repositories import TenantQueryValidator

# Valider que toutes les queries incluent tenant_id
query = "SELECT * FROM customers WHERE name = 'test'"
is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
assert is_valid is False  # ❌ tenant_id manquant
```

## 5. Checklist Production

### Base de Données

- [ ] Toutes les tables ont `tenant_id` (NOT NULL, FK)
- [ ] Index composite `(tenant_id, id)` sur chaque table
- [ ] Index composite `(tenant_id, created_at)` sur chaque table
- [ ] Index composite pour chaque foreign key

### Code

- [ ] Utiliser `TenantAwareRepository` pour TOUTES les queries
- [ ] Ne JAMAIS faire de query directe sans repository
- [ ] TenantContext injecté dans chaque requête API
- [ ] Tests d'isolation exécutés en CI

### ChromaDB

- [ ] 4 collections max (products, faqs, policies, conversations)
- [ ] Filtrage par `tenant_id` dans metadata
- [ ] Validation post-query que les résultats appartiennent au tenant

## 6. Risques et Mitigations

| Risque | Mitigation |
|--------|------------|
| Query sans tenant_id | Repository pattern force le filtrage |
| Cross-tenant accidentel | Validation avant update/delete |
| Performance | Index composites |
| Explosion collections ChromaDB | Single collection pattern |
| Oubli en review | Tests automatisés en CI |

## Fichiers Clés

```
app/infrastructure/database/repositories/
├── __init__.py
├── base.py              ← TenantAwareRepository
└── repositories.py      ← CustomerRepository, etc.

app/domain/services/shared/
└── rag_service_v2.py    ← Single collection ChromaDB

tests/unit/
└── test_tenant_isolation.py  ← Tests isolation
```

