# SaaS AI E-commerce Assistant

An AI chat assistant for online shops (PrestaShop today) plus an admin backoffice. Three apps, one repo, no shared workspace tooling.

## Stack

- **Language / Runtime**: Python 3.11+ (ai-core), PHP 8.3 (backoffice), PHP 7.2+ (PrestaShop module)
- **Framework**: FastAPI (ai-core), Laravel 13 with Filament 3 (backoffice)
- **Key dependencies**: SQLAlchemy 2 async and Alembic, Postgres 16, Redis, ChromaDB (embedded), Groq for chat (`openai/gpt-oss-120b`), a local MiniLM model for embeddings (OpenAI optional)
- **Package manager**: pip (`requirements.txt`), Composer and npm

## Build approach

<TBD, set by /scope>

## Commands

```bash
# Dev stack (Postgres, Redis, ai-core :8000, backoffice :8090). Needs infrastructure/docker/.env
cd infrastructure/docker && docker-compose -f docker-compose.dev.yml up -d

make migrate                           # alembic upgrade head (see the ai-core migrations note)

# Test
make test                              # ai-core unit tests only
cd src/backoffice && php artisan test  # backoffice

# Lint (this is what CI runs; the Makefile lint targets still call black and mypy)
cd src/ai-core && ruff check app/ && ruff format --check app/
cd src/backoffice && vendor/bin/pint --test
```

## Specs

Stored in `docs/specs/`. Format: `docs/specs/NNNN-title.md`.

## Rules

- ai-core is the only owner of Postgres. The backoffice and the storefront modules talk to it over HTTP, never to the database.
- Every query is scoped by `tenant_id`. Real auth is an API key plus an HMAC signature; the `X-Tenant-ID` shortcut only works when `SECURITY_ALLOW_DEV_TENANT_HEADER=true` (dev compose only, never production).
- Never commit `.env` files (only `.env.example` is tracked). Secrets come from environment variables.
- Real data only, no mocks in app code: Groq LLM, real embeddings, ChromaDB required (test doubles live in `tests/support/`). A metric with no backing column is omitted, or the endpoint returns `501`.
- Branch from `dev` and merge into `dev`, the GitHub default. `main` is production only: do not touch it until we go live.
- Postgres and Redis are not published to the host. Use `docker exec`. Redis needs `REDIS_PASSWORD`.
- Match the language of the file you edit. ai-core comments and docstrings are mostly French.

## Agent skills

- [docker-patterns](.agents/skills/docker-patterns/): `affaan-m/ecc`, Dockerfile and compose habits for the dev stack
- [prometheus-configuration](.agents/skills/prometheus-configuration/): `wshobson/agents`, Prometheus and alert setup
- Declined: sqlalchemy and alembic skill, kubernetes skill, laravel filament skill
- MCP servers: mcp-redis (recommended), postgres-mcp (recommended), github-mcp-server (recommended), laravel mcp (recommended)

## Context files

- [src/ai-core/AGENTS.md](src/ai-core/AGENTS.md) (FastAPI service: chat, rules, admin agent, auth, tenants)
- [src/backoffice/AGENTS.md](src/backoffice/AGENTS.md) (Laravel and Filament admin, a pure REST client of ai-core)
- [src/platform-adapters/prestashop/aiassistant/AGENTS.md](src/platform-adapters/prestashop/aiassistant/AGENTS.md) (PrestaShop chat widget module)

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
