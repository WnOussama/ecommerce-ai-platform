# AI Observability Guide

## Vue d'Ensemble

Architecture d'observabilité complète pour le SaaS AI.

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         AI OBSERVABILITY STACK                                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐           │
│  │   AI Services   │────▶│   Prometheus    │────▶│    Grafana      │           │
│  │                 │     │                 │     │                 │           │
│  │ • Chat Service  │     │ • Scrape /15s   │     │ • Dashboards    │           │
│  │ • Admin Service │     │ • Store metrics │     │ • Multi-tenant  │           │
│  │ • Sync Workers  │     │ • Alert rules   │     │ • Real-time     │           │
│  └─────────────────┘     └────────┬────────┘     └─────────────────┘           │
│                                   │                                             │
│                                   ▼                                             │
│                          ┌─────────────────┐                                   │
│                          │  Alertmanager   │                                   │
│                          │                 │                                   │
│                          │ • Slack         │                                   │
│                          │ • PagerDuty     │                                   │
│                          │ • Email         │                                   │
│                          └─────────────────┘                                   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Métriques AI Spécifiques

### 1. LLM Metrics

| Métrique | Type | Labels | Description |
|----------|------|--------|-------------|
| `llm_tokens_total` | Counter | tenant_id, model, token_type | Tokens utilisés (input/output) |
| `llm_cost_dollars_total` | Counter | tenant_id, model | Coût en dollars |
| `llm_request_duration_seconds` | Histogram | tenant_id, model, operation | Latence (buckets P50/P95/P99) |
| `llm_requests_total` | Counter | tenant_id, model, status | Requêtes totales |
| `llm_errors_total` | Counter | tenant_id, model, error_type | Erreurs par type |

### 2. RAG Metrics

| Métrique | Type | Labels | Description |
|----------|------|--------|-------------|
| `rag_query_duration_seconds` | Histogram | tenant_id, doc_type, stage | Latence par étape |
| `rag_queries_total` | Counter | tenant_id, doc_type, result | Queries (hit/miss/partial) |
| `rag_documents_retrieved` | Histogram | tenant_id, doc_type | Docs par query |
| `rag_rerank_score_improvement` | Histogram | tenant_id | Amélioration reranking |
| `embedding_queue_backlog` | Gauge | queue_name | Jobs en attente |
| `rag_index_documents_total` | Gauge | tenant_id, doc_type | Taille index |

### 3. Quality Metrics

| Métrique | Type | Labels | Description |
|----------|------|--------|-------------|
| `ai_hallucination_detected_total` | Counter | tenant_id, detection_method | Hallucinations détectées |
| `ai_response_confidence` | Histogram | tenant_id | Score de confiance |
| `guardrail_triggers_total` | Counter | tenant_id, guardrail_type, action | Guardrails déclenchés |
| `prompt_injection_attempts_total` | Counter | tenant_id, threat_level, blocked | Injections détectées |
| `output_validation_failures_total` | Counter | tenant_id, failure_type | Échecs validation |

### 4. Business Metrics

| Métrique | Type | Labels | Description |
|----------|------|--------|-------------|
| `conversations_total` | Counter | tenant_id | Conversations démarrées |
| `conversations_active` | Gauge | tenant_id | Conversations actives |
| `messages_per_conversation` | Histogram | tenant_id | Messages par conversation |
| `conversation_duration_seconds` | Histogram | tenant_id, outcome | Durée conversations |
| `conversion_events_total` | Counter | tenant_id, event_type | Événements conversion |
| `response_feedback_total` | Counter | tenant_id, feedback | Feedback utilisateur |

---

## Usage

### Setup FastAPI

```python
from fastapi import FastAPI
from app.core.monitoring import setup_metrics

app = FastAPI()
setup_metrics(app)  # Ajoute middleware + endpoint /metrics
```

### Recording Metrics

```python
from app.core.monitoring import get_metrics_collector

metrics = get_metrics_collector()

# LLM Request (avec context manager)
with metrics.track_llm_request(tenant_id, model="gpt-4", operation="chat"):
    response = await llm.generate(prompt)

# Tokens et coût
metrics.record_llm_tokens(
    tenant_id=tenant_id,
    model="gpt-4",
    input_tokens=100,
    output_tokens=500,
    cost=0.015,
)

# RAG Query
with metrics.track_rag_query(tenant_id, doc_type="product"):
    results = await rag.search(query)

metrics.record_rag_result(tenant_id, "product", documents_found=5)

# Quality
metrics.record_hallucination_detected(tenant_id, "factual_check")
metrics.record_response_confidence(tenant_id, 0.85)
metrics.record_guardrail_trigger(tenant_id, "profanity", "blocked")
metrics.record_prompt_injection_attempt(tenant_id, "high", blocked=True)

# Business
metrics.record_conversation_started(tenant_id)
metrics.record_conversation_ended(tenant_id, message_count=10, duration_seconds=300)
metrics.record_conversion_event(tenant_id, "purchased")
metrics.record_feedback(tenant_id, "positive")
```

### Decorators

```python
from app.core.monitoring import track_llm_call, track_rag_query

@track_llm_call(model="gpt-4", operation="chat")
async def generate_response(tenant_id: str, prompt: str):
    # Automatiquement tracké
    return await llm.generate(prompt)

@track_rag_query(doc_type="product")
async def search_products(tenant_id: str, query: str):
    # Automatiquement tracké
    return await rag.search(query)
```

---

## Dashboards Grafana

### Vue d'ensemble SLA

```
┌─────────────────────────────────────────────────────────────────────────┐
│  API Availability │ LLM Latency P95 │ Hallucination │ RAG Hit Rate    │
│     99.95%        │     1.2s        │    Rate 2.1%  │    78%          │
│     ✅ Green      │     ✅ Green    │    ✅ Green   │    ⚠️ Yellow    │
└─────────────────────────────────────────────────────────────────────────┘
```

### LLM Usage par Tenant

```
Token Usage by Tenant                    LLM Cost by Tenant (Daily)
│                                        │
│    ████ tenant_A                       │    ████ tenant_A: $45
│    ██ tenant_B                         │    ██ tenant_B: $12
│    █ tenant_C                          │    █ tenant_C: $8
└────────────────────────────────        └────────────────────────────────
```

### Variables de filtrage

- `$tenant_id` - Filtrer par tenant
- `$model` - Filtrer par modèle LLM

---

## Alertes SLA

### Critiques (severity: critical)

| Alert | Condition | Action |
|-------|-----------|--------|
| APIAvailabilityLow | < 99.9% pendant 5min | Page on-call |
| LLMLatencyCritical | P95 > 10s pendant 3min | Page on-call |
| EmbeddingQueueBacklogCritical | > 500 jobs pendant 5min | Scale workers |
| CriticalThreatDetected | Prompt injection critique | Review immédiat |

### Warnings (severity: warning)

| Alert | Condition | Action |
|-------|-----------|--------|
| LLMLatencyHigh | P95 > 5s pendant 5min | Monitor |
| RAGHitRateLow | < 60% pendant 30min | Review indexing |
| HallucinationRateHigh | > 5% pendant 30min | Review prompts |
| TenantHighTokenUsage | > 100k tokens/h | Notify billing |

### Info (severity: info)

| Alert | Condition | Action |
|-------|-----------|--------|
| PendingHumanApprovals | > 5 pendant 30min | Review queue |
| PromptInjectionDetected | Any blocked | Log for review |

---

## Queries Prometheus Utiles

### LLM Performance

```promql
# Latence P95 par modèle
histogram_quantile(0.95, 
  sum(rate(llm_request_duration_seconds_bucket[5m])) by (le, model)
)

# Tokens par tenant (dernière heure)
sum(increase(llm_tokens_total[1h])) by (tenant_id)

# Coût journalier par tenant
sum(increase(llm_cost_dollars_total[24h])) by (tenant_id)

# Taux d'erreur LLM
100 * sum(rate(llm_errors_total[5m])) / sum(rate(llm_requests_total[5m]))
```

### RAG Performance

```promql
# Hit rate
100 * sum(rate(rag_queries_total{result="hit"}[1h])) 
    / sum(rate(rag_queries_total[1h]))

# Latence par étape
histogram_quantile(0.95, 
  sum(rate(rag_query_duration_seconds_bucket[5m])) by (le, stage)
)

# Backlog queue
sum(embedding_queue_backlog) by (queue_name)
```

### Quality

```promql
# Taux d'hallucination
100 * sum(rate(ai_hallucination_detected_total[1h])) 
    / sum(rate(llm_requests_total{status="success"}[1h]))

# Confiance médiane
histogram_quantile(0.5, 
  sum(rate(ai_response_confidence_bucket[1h])) by (le)
)

# Injections bloquées
sum(increase(prompt_injection_attempts_total{blocked="true"}[24h]))
```

---

## Fichiers

| Fichier | Description |
|---------|-------------|
| `ai_metrics.py` | Définitions métriques + Collector |
| `metrics_middleware.py` | Middleware FastAPI + Decorators |
| `grafana/dashboards/ai-observability.json` | Dashboard Grafana |
| `prometheus/alerts/ai_alerts.yml` | Règles d'alerting |

---

## Setup Docker

```yaml
# docker-compose.yml
services:
  prometheus:
    image: prom/prometheus
    volumes:
      - ./monitoring/prometheus:/etc/prometheus
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
  
  grafana:
    image: grafana/grafana
    volumes:
      - ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards
    environment:
      - GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/var/lib/grafana/dashboards/ai-observability.json
  
  alertmanager:
    image: prom/alertmanager
    volumes:
      - ./monitoring/alertmanager:/etc/alertmanager
```

