# SaaS AI E-commerce Assistant

An AI chat assistant for e-commerce sites (PrestaShop today; Shopify/WooCommerce
adapters scaffolded but not wired up), plus an admin backoffice for tenants to
configure it and see what it's actually doing.

## What's real here

This project went through a deliberate pass to replace every fabricated number
and stubbed endpoint with something computed from real data - there is no
`orders` table, so there is no revenue, no order count, no fake "Product A"
in a report. If a metric isn't backed by a real column, it's either omitted
or the endpoint honestly returns `501 Not Implemented`.

- **Chat** (`/api/v1/chat/message`) - RAG-backed product search (ChromaDB +
  embeddings), guardrails (prompt-injection detection, PII masking, output
  sanitization), conversations/messages persisted to Postgres.
- **Tenant rules engine** (`/api/v1/rules`) - conditions (`intent`,
  `keywords_any`/`keywords_all`) matched against each message, actions
  (`canned_response`, `inject_instruction`, `generate_coupon`) applied before
  the LLM call. Editable from the backoffice without touching JSON.
- **Admin AI agent** (`/api/v1/admin/command`) - a small set of *predefined*
  actions (no free-form execution), each risk-classified (LOW/MEDIUM/HIGH/
  CRITICAL) and gated by a real dry-run → confirm → (double-confirm / human
  approval) → execute workflow, backed by Redis so it survives restarts and
  is shared across workers.
- **Insights** (`/api/v1/insights/summary`) - most-requested products, unmet
  demand (searches that returned nothing - a real catalog-gap signal),
  intent distribution, peak hours, coupon conversion, low stock. Computed
  from `messages`/`conversations`/`products`/`coupons`, not invented.
- **Analytics** (`/api/v1/analytics/*`) - dashboard metrics, per-day chart
  series, AI performance (guardrail blocks, hallucination-flag rate, LLM
  cost), and a cost/message report (tokens, cost-per-conversation, most
  expensive recent messages).
- **Recommendations** (`/api/v1/recommendations/*`) - content-based
  "similar products" via the vector store, "trending" from real chat-demand
  counts.
- **Backoffice** (Laravel/Filament) - a pure REST client over the AI Core API
  (no direct database access, enforced by architecture, not just convention).
  Dashboard with real chart widgets, a chat-transcript admin agent page,
  rules CRUD, cost/message tracking, a conversations browser, and the
  insights page above.
- **PrestaShop storefront demo** - a real PrestaShop 8 instance
  (`infrastructure/docker/docker-compose.dev.yml`) running the
  `aiassistant` module (`src/platform-adapters/prestashop/aiassistant`),
  a working chat widget wired to the AI Core API on an actual storefront.

## Architecture

A single FastAPI application (`ai-core`) owns Postgres exclusively; the
Laravel backoffice talks to it only over HTTP. No other service touches the
database directly.

```
┌──────────────┐        ┌──────────────────────────────┐
│  Storefront  │──chat─▶│                                │
│  (PrestaShop)│        │   ai-core (FastAPI, Python)    │
└──────────────┘        │                                │
                         │  chat · rules · admin agent    │
┌──────────────┐  REST   │  insights · analytics · recos  │
│  Backoffice  │────────▶│                                │
│ (Laravel/    │         └──────┬──────────┬──────────────┘
│  Filament)   │                │          │
└──────────────┘                ▼          ▼
                          ┌───────────┐ ┌─────────┐   ┌───────────┐
                          │ PostgreSQL│ │  Redis  │   │  ChromaDB │
                          │ (owner:   │ │ (rate   │   │ (product  │
                          │  ai-core) │ │ limits, │   │  vectors) │
                          │           │ │ admin   │   │           │
                          │           │ │ safety) │   │           │
                          └───────────┘ └─────────┘   └───────────┘
```

See `docs/diagrams/` for the full C4 container diagram, the chat request
sequence (guardrails → rules → RAG → persistence), and the rules-evaluation
flow.

## Project structure

```
src/
├── ai-core/                 # FastAPI backend (owns Postgres exclusively)
│   ├── app/
│   │   ├── api/v1/endpoints/    # chat, rules, admin, analytics, insights,
│   │   │                        # recommendations, coupons, faq, tenants, sync
│   │   ├── core/security/       # guardrails, admin_safety (dry-run/confirm/rollback)
│   │   ├── domain/services/admin/  # AdminAgent - predefined-action executor
│   │   ├── infrastructure/llm/  # GroqLLMProvider - the only LLM provider
│   │   ├── infrastructure/database/ # SQLAlchemy models, repositories, UnitOfWork
│   │   └── services/            # chat turn orchestrator, rules evaluator,
│   │                             # insights, RAG/retrieval
│   └── tests/                   # unit, integration (real Postgres), ai_evaluation
│
├── platform-adapters/        # e-commerce plugins
│   ├── prestashop/               # active
│   ├── shopify/                  # scaffolded, not wired up
│   └── woocommerce/              # scaffolded, not wired up
│
└── backoffice/               # Laravel/Filament admin panel (REST client only)
    ├── app/Filament/Pages/       # Dashboard, AdminAgent, Rules, CostTracking,
    │                             # Conversations, Insights, TenantSettings
    ├── app/Filament/Widgets/     # chart widgets (conversations, LLM cost,
    │                             # guardrail blocks, intent distribution)
    └── app/Services/AiCoreClient.php  # the only thing allowed to call ai-core

infrastructure/
├── docker/                   # docker-compose.dev.yml, per-service Dockerfiles
└── scripts/demo-day.sh       # one-command stack + public tunnel for a live demo

docs/
├── diagrams/                 # PlantUML (C4 container, sequences, rules flow)
├── api/                      # exported OpenAPI schema
├── deployment/                # containerized deploy procedure
├── runbooks/                  # local-dev runbook (native + containerized paths)
├── rapport-de-stage/          # internship report (LaTeX)
└── presentation/              # soutenance deck + speaker script
```

## Quick start

**Prerequisites:** Docker & Docker Compose v2+.

```bash
cp infrastructure/docker/.env.example infrastructure/docker/.env
```

Edit `.env`:
- `LLM_GROQ_API_KEY` - **required**, no mock and no fallback. Get a free
  key at [console.groq.com/keys](https://console.groq.com/keys); the app
  raises a clear startup error if it's missing or malformed. The test
  suite doesn't need this - it runs against an isolated test double
  (`tests/support/stub_llm_provider.py`), never a real key or network call.
- `LLM_OPENAI_API_KEY` - optional, only powers RAG product-search
  embeddings (Groq has no embeddings API). Without it, RAG search falls
  back to a deterministic mock embedding service (pipeline works, results
  aren't semantically meaningful - see `docs/runbooks/local-dev.md`).
- `BACKOFFICE_APP_KEY` - generate with `php artisan key:generate --show`
  from `src/backoffice` (needs a local PHP/Composer install), or leave it
  and let the container fail once with a clear error telling you to set it.
- `BACKOFFICE_TENANT_ID` - there's no tenant signup UI yet; seed a demo
  tenant first (see below) and paste its UUID here so the backoffice's
  dev-mode `X-Tenant-ID` header has something real to point at.

```bash
# Start everything
cd infrastructure/docker && docker compose -f docker-compose.dev.yml up -d

# Seed a demo tenant + product catalog + default rules, prints the tenant UUID
docker exec saas_ai_core python -m scripts.seed_demo_data
```

### Services

| Service | URL | Notes |
|---|---|---|
| AI Core API | http://localhost:8000 | `/docs` for Swagger, `/health` for the healthcheck |
| Backoffice | http://127.0.0.1:8090/admin | use `127.0.0.1`, not `localhost` |
| PrestaShop storefront | http://localhost:8080 | chat widget demo (`aiassistant` module) |
| Grafana | http://localhost:3000 | admin/admin by default |
| Prometheus | http://localhost:9090 | |

For a live public demo: `infrastructure/scripts/demo-day.sh` brings up the
stack and opens a Cloudflare Quick Tunnel.

## Tests

```bash
# ai-core (430+ tests: unit + integration against real Postgres + AI eval -
# no Groq key or network needed, the LLM is a test double, see tests/support/)
docker exec saas_ai_core sh -c "cd /app && python -m pytest tests/ -q"
docker exec saas_ai_core sh -c "cd /app && ruff check . && ruff format --check ."

# backoffice
cd src/backoffice
php artisan test
vendor/bin/pint --test
```

## Design notes worth knowing before you dig in

- **No orders table.** The PrestaShop client only pulls products and
  categories. Every "demand" signal in this project (most-requested
  products, trending, unmet demand) comes from what customers actually
  *asked the chatbot for*, not from sales data that doesn't exist here.
- **The admin agent doesn't do free-form execution.** `AdminCommandParser`
  only recognizes a fixed set of actions; anything else is rejected. This
  is a deliberate boundary, not a missing feature - see
  `app/core/security/admin_safety.py`.
- **Multi-tenancy in dev** uses an `X-Tenant-ID` header accepted only when
  `ENVIRONMENT=development`; there's no tenant signup/API-key issuance flow
  yet (see `docs/deployment/docker.md`).
- **One real LLM, no mock.** Groq is the only chat provider
  (`app/infrastructure/llm/provider_factory.py`) - a missing/invalid key
  raises at startup instead of silently degrading to a canned response.
  RAG embeddings are a separate concern (Groq has no embeddings API) and
  do have a mock fallback for dev/test convenience.

## License

MIT

---

**Projet PFE** - 2025-2026
