# ai-core

## Overview

The FastAPI service that owns everything: chat with retrieval, the tenant rules engine, the admin AI agent, insights and analytics, coupons, and tenant API keys. There is no self signup or email: an admin provisions tenants. It is the only process that touches Postgres. Storefront modules and the backoffice call it over HTTP.

## Stack

- **Language / Runtime**: Python 3.11+
- **Framework**: FastAPI, Pydantic v2, SQLAlchemy 2 async (asyncpg), Alembic
- **Data**: Postgres 16, Redis (rate limit, admin action state, cache), ChromaDB running embedded
- **Package manager**: pip (`requirements.txt`, `requirements-dev.txt`)

## Key files

| File | Owns |
|---|---|
| `app/main.py` | `create_application()`: middleware order, routers, exception handlers |
| `app/api/middleware/tenant_context.py` | Auth: dev header shortcut, API key lookup, HMAC signature check, tenant cache |
| `app/api/v1/endpoints/` | Routers: chat, rules, admin, insights, analytics, coupons, recommendations, tenants |
| `app/core/config/settings.py` | The live `settings` singleton. Env groups use prefixes (`SECURITY_`, `REDIS_`, `LLM_`) |
| `app/core/security/` | `guardrails` (prompt injection, PII), `admin_safety` (dry run, confirm), `api_key_security` (HMAC), `secret_box` (Fernet), `rate_limiter` |
| `app/domain/services/` | Chat, client agent, admin agent, shared LLM gateway |
| `app/infrastructure/database/` | SQLAlchemy models and repositories (repositories filter by `tenant_id`) |
| `app/infrastructure/llm/`, `vector_store/` | Groq provider (the only LLM) and ChromaDB access |
| `app/services/rag/` | Embeddings (local MiniLM, or OpenAI with a key), indexing, retrieval |
| `tests/conftest.py`, `tests/support/` | Env defaults before any app import. Stub LLM and stub embeddings for tests only |
| `scripts/seed_demo_data.py` | Seeds a demo tenant |

## Commands

```bash
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload
pytest tests/unit/                   # fast, needs no services
pytest tests/integration/            # needs Postgres, skips itself if unreachable
ruff check app/ && ruff format --check app/
python -m scripts.seed_demo_data
# Inside the dev compose container:
docker exec saas_ai_core sh -c "cd /app && python -m pytest tests/ -q"
```

## Conventions

- Import `settings` from `app.core.config.settings`. A new required secret must also be added to `tests/conftest.py`, `alembic/env.py`, `.env.example`, the CI env, and the compose files, or boot and tests fail.
- Layers: `api` (routers, middleware) then `domain/services` then `infrastructure`. Repositories always scope by `tenant_id`.
- Real auth needs an API key (`Authorization: Bearer` or `X-API-Key`) plus `X-Timestamp` and `X-Signature` (HMAC SHA256 over timestamp, method, path, body hash, within 5 minutes). Use `APIClientSigner` to build them. The tenant secret is stored encrypted with `SECURITY_API_KEY_ENCRYPTION_KEY`.
- The `X-Tenant-ID` shortcut is gated only by `SECURITY_ALLOW_DEV_TENANT_HEADER`. Never derive it from `ENVIRONMENT`.
- Coupons come from tenant rules with a `generate_coupon` action, never from a bare keyword. Use the `first_message` condition for a welcome coupon and `min_cart_total` for a spend threshold. The shop sends `customer_id` and `cart_total` from its server side. One coupon per visitor and rule, or again after `cooldown_days`. `max_per_hour` caps a rule per tenant (default 10). Keyword conditions match whole words.
- Ruff: line length 100, target py311, rules E, F, I, W. `asyncio_mode` is `auto` in pytest.

## Gotchas

- Alembic history is broken. The live dev database was built with `create_all` and is stamped `a1f4c7d92b3e`, which has no file, and `001_initial` is missing. Schema changes there were applied by hand with `ALTER TABLE`. `alembic upgrade head` fails on that database.
- Do not call `create_application()` in a unit test that reaches a database endpoint. The shared asyncpg engine is bound to an old event loop and fails with "attached to a different loop". Wrap the middleware in a tiny Starlette app instead.
- `app/core/config/security_settings.py` (`StrictSecuritySettings`) is exported but never called. Real settings live in `settings.py`.
- The `chromadb` service in the compose files is unused. ChromaDB runs embedded from `data/chroma`, and nothing connects to that container.
- With a `customer_id`, ai-core reuses that visitor's open conversation, so `first_message` is true only once per visitor. A welcome coupon refused by the hourly cap is not offered again.
- `docker-compose.dev.yml` is the only compose file. The first embedding call downloads a ~80 MB model into `data/onnx_models` (git ignored).
- ChromaDB 0.4.22 needs `numpy<2`. If ai-core logs "ChromaDB not available", the image is stale: rebuild it.
- To sync the real catalog from inside docker, use the PrestaShop address `http://prestashop`, not `localhost:8080` or the container name.

## Agent skills

- [fastapi](../../.agents/skills/fastapi/): `fastapi/fastapi`, how to write routers, dependencies, and models
- [redis-core](../../.agents/skills/redis-core/), [redis-connections](../../.agents/skills/redis-connections/), [redis-security](../../.agents/skills/redis-security/): `redis/agent-skills`, data structures, connection handling, and locking down Redis (rate limit, admin action state, cache)
- [supabase-postgres-best-practices](../../.agents/skills/supabase-postgres-best-practices/): `supabase/agent-skills`, Postgres queries, indexes, and schema design
- [pytest-coverage](../../.agents/skills/pytest-coverage/): `github/awesome-copilot`, running pytest with coverage and closing gaps

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
