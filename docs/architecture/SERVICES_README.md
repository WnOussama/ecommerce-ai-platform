# AI Core Services - Architecture Guide

## Overview

This document describes the multi-service architecture with **Shared Core** pattern.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              SHARED CORE                                          │
│                                                                                   │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │  LLM Gateway   │  │  RAG Service   │  │   Security     │  │    Tenant      │ │
│  │  • OpenAI      │  │  • ChromaDB    │  │  • Sanitize    │  │  • Plans       │ │
│  │  • Anthropic   │  │  • Embeddings  │  │  • Injection   │  │  • Limits      │ │
│  │  • Cost track  │  │  • Similarity  │  │  • Guardrails  │  │  • Features    │ │
│  └────────────────┘  └────────────────┘  └────────────────┘  └────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 │                                           │
                 ▼                                           ▼
┌────────────────────────────────────┐   ┌────────────────────────────────────┐
│          CLIENT AGENT              │   │           ADMIN AGENT              │
│                                    │   │                                    │
│  • Conversational tone             │   │  • Structured JSON only            │
│  • Strict output guardrails        │   │  • No free-text execution          │
│  • Limited predefined actions      │   │  • Confirmation workflow required  │
│  • Natural language responses      │   │  • Mandatory audit logging         │
└────────────────────────────────────┘   └────────────────────────────────────┘
         │                                           │
         ▼                                           ▼
┌────────────────────────────────────┐   ┌────────────────────────────────────┐
│     AI-CHAT-SERVICE (8001)         │   │    AI-ADMIN-SERVICE (8002)         │
└────────────────────────────────────┘   └────────────────────────────────────┘
```

## Shared Core Components

### 1. LLM Gateway (`shared/llm_gateway.py`)
- Multi-provider support (OpenAI, Anthropic)
- Automatic retry with exponential backoff
- Fallback to secondary provider
- Cost tracking per tenant
- JSON response format support

### 2. RAG Service (`shared/rag_service.py`)
- ChromaDB integration
- Multi-tenant collection isolation
- Document types: products, FAQs, policies
- Batch embedding generation

### 3. Security Service (`shared/security_service.py`)
- Input sanitization
- Prompt injection detection
- Output guardrails (client)
- JSON validation (admin)
- Threat level assessment

### 4. Tenant Service (`shared/tenant_service.py`)
- Plan management (Starter, Professional, Enterprise)
- Feature access control
- Usage limits enforcement
- LLM model restrictions per plan

## Agent Differences

| Aspect | Client Agent | Admin Agent |
|--------|--------------|-------------|
| **Output Format** | Natural language text | Structured JSON only |
| **Execution** | LLM + guardrails | Predefined commands |
| **Tone** | Conversational, formal | Technical, factual |
| **Actions** | 6 limited types | Whitelist only |
| **Confirmation** | Not required | Required for HIGH risk |
| **Audit** | Basic logging | Full audit mandatory |

## Client Agent Actions (Limited)

```python
class ClientActionType(str, Enum):
    RESPOND = "respond"              # Text response
    SHOW_PRODUCTS = "show_products"  # Show products
    SHOW_FAQ = "show_faq"            # Show FAQ
    GENERATE_COUPON = "generate_coupon"  # Generate coupon
    REDIRECT = "redirect"            # Redirect to page
    ESCALATE = "escalate"            # Escalate to human
```

## Admin Agent Commands (Predefined)

| Command | Risk | Confirmation |
|---------|------|--------------|
| `get_sales_analytics` | LOW | ❌ |
| `get_customer_analytics` | LOW | ❌ |
| `generate_sales_report` | LOW | ❌ |
| `suggest_marketing_strategy` | MEDIUM | ❌ |
| `segment_customers` | MEDIUM | ✅ |
| `generate_bulk_coupons` | HIGH | ✅ |
| `update_product_prices` | HIGH | ✅ |

## Directory Structure

```
src/ai-core/app/
├── services/
│   ├── chat_service.py        # Chat Service (Port 8001)
│   ├── admin_service.py       # Admin Service (Port 8002)
│   ├── sync_service.py        # Sync Worker
│   └── message_queue/
│       └── redis_queue.py     # Redis Streams
│
└── domain/services/
    ├── shared/                 # SHARED CORE
    │   ├── llm_gateway.py     # LLM abstraction
    │   ├── rag_service.py     # RAG/ChromaDB
    │   ├── security_service.py # Security
    │   ├── tenant_service.py  # Multi-tenant
    │   ├── context_manager.py # Context
    │   └── prompt_registry.py # Prompts
    │
    ├── client/
    │   └── agent_v2.py        # Client Agent
    │
    └── admin/
        └── agent_v2.py        # Admin Agent
```

## Running the Services

### Development (Docker Compose)

```bash
# Using the multi-service compose file
cd infrastructure/docker
docker-compose -f docker-compose.multi-service.yml up -d

# With monitoring stack
docker-compose -f docker-compose.multi-service.yml --profile monitoring up -d
```

### Development (Local)

```bash
# Terminal 1: Chat Service
cd src/ai-core
python -m app.services.chat_service

# Terminal 2: Admin Service
python -m app.services.admin_service

# Terminal 3: Sync Service (Worker)
python -m app.services.sync_service
```

### Production

```bash
# Chat Service (4 workers for high throughput)
uvicorn app.services.chat_service:app --host 0.0.0.0 --port 8001 --workers 4

# Admin Service (2 workers for heavier operations)
uvicorn app.services.admin_service:app --host 0.0.0.0 --port 8002 --workers 2

# Sync Service (background worker)
python -m app.services.sync_service
```

## Scaling

### Chat Service (High Traffic)
```bash
# Scale to 4 instances
docker-compose -f docker-compose.multi-service.yml up -d --scale ai-chat-service=4
```

### Sync Service (Background Processing)
```bash
# Scale to 3 workers
docker-compose -f docker-compose.multi-service.yml up -d --scale ai-sync-service=3
```

## API Endpoints

### Chat Service (Port 8001)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/chat/message` | POST | Send chat message |
| `/api/v1/chat/history/{id}` | GET | Get conversation history |
| `/api/v1/recommendations` | POST | Get product recommendations |
| `/api/v1/coupons/generate` | POST | Generate personalized coupon |
| `/api/v1/faq/search` | POST | Search FAQ |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

### Admin Service (Port 8002)
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/command` | POST | Execute AI command |
| `/api/v1/admin/confirm` | POST | Confirm pending action |
| `/api/v1/analytics/dashboard` | GET | Get dashboard data |
| `/api/v1/analytics/report` | POST | Generate report |
| `/api/v1/tenants/current` | GET | Get current tenant |
| `/health` | GET | Health check |
| `/metrics` | GET | Prometheus metrics |

## Message Queue

### Queue Names
| Queue | Producer | Consumer | Purpose |
|-------|----------|----------|---------|
| `queue:embedding:requests` | Chat, Admin | Sync | Generate embeddings |
| `queue:catalog:sync` | Admin | Sync | Sync catalog from e-commerce |
| `queue:events:audit` | All | Monitoring | Audit events |
| `queue:dlq` | Sync | Monitoring | Failed messages |

### Message Types
- `GENERATE_PRODUCT_EMBEDDINGS`
- `GENERATE_FAQ_EMBEDDINGS`
- `DELETE_EMBEDDINGS`
- `SYNC_CATALOG`
- `SYNC_CUSTOMERS`
- `BULK_COUPON_GENERATION`
- `BULK_PRICE_UPDATE`

## Nginx Routing

The Nginx gateway routes requests to the appropriate service:

```nginx
/api/v1/chat/*           → ai-chat-service:8001
/api/v1/recommendations/* → ai-chat-service:8001
/api/v1/coupons/*        → ai-chat-service:8001
/api/v1/faq/*            → ai-chat-service:8001

/api/v1/admin/*          → ai-admin-service:8002
/api/v1/analytics/*      → ai-admin-service:8002
/api/v1/tenants/*        → ai-admin-service:8002
/api/v1/operations/*     → ai-admin-service:8002
```

## Migration Steps

### Phase 1: Deploy Multi-Service (Current)
1. ✅ Create separate service files
2. ✅ Implement Redis queue
3. ✅ Configure Nginx routing
4. ✅ Update docker-compose
5. ✅ Update Prometheus scrape config

### Phase 2: Parallel Running
```bash
# Run both architectures
docker-compose -f docker-compose.yml up -d  # Old monolith
docker-compose -f docker-compose.multi-service.yml up -d  # New services

# Route traffic to new services via Nginx
```

### Phase 3: Full Migration
1. Route 100% traffic to new services
2. Deprecate monolith
3. Remove old docker-compose.yml

## Monitoring

### Prometheus Targets
- `ai-chat-service:8001/metrics`
- `ai-admin-service:8002/metrics`
- Sync service pushes metrics via Redis

### Key Metrics
- `http_requests_total{service="chat"}`
- `http_request_duration_seconds{service="chat"}`
- `queue_messages_total{queue="embedding"}`
- `queue_processing_seconds{queue="embedding"}`

## Troubleshooting

### Chat Service Not Responding
```bash
# Check service health
curl http://localhost:8001/health

# Check logs
docker-compose logs -f ai-chat-service
```

### Queue Backlog
```bash
# Check queue length
redis-cli XLEN queue:embedding:requests

# Check pending messages
redis-cli XPENDING queue:embedding:requests sync-service
```

### Sync Service Not Processing
```bash
# Check worker logs
docker-compose logs -f ai-sync-service

# Check DLQ for failed messages
redis-cli XLEN queue:dlq
```


