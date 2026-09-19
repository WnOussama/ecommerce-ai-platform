# Backlog: security, rate limiting and load testing

Written on 2026-09-19 at the end of the version 1 review. Version 1 is the first
complete, working version: real Groq LLM, real local embeddings, signed API
keys, coupons, storefront widget, backoffice. This file lists what to improve
next, in order. Nothing here blocks a demo. Items marked **before production**
should be done before real shoppers use it.

How to read the evidence tags:

- **Tested live**: reproduced against the running stack.
- **From code**: read in the source, not measured yet.

## 1. Before production

| # | Item | Evidence | Suggested fix |
|---|------|----------|---------------|
| 1 | The PrestaShop module and the backoffice do not sign requests. Only the dev header path works for them. | Tested live | Add HMAC signing to `classes/AiCoreClient.php` and `app/Services/AiCoreClient.php`. Config: API key plus secret. Use `APIClientSigner` as the reference (the signed target is path plus `?query`). |
| 2 | The rate limiter runs before the tenant is known, so it limits by IP, not by tenant. The plan limits (30, 100, 300 per minute) are never used. | From code | `RateLimiterMiddleware` is added last, which makes it the outermost layer. Move it inside `TenantContextMiddleware`, or make the tenant context set `request.state` first. Measure before and after. |
| 3 | The limiter trusts the first `X-Forwarded-For` value. | From code | Only trust it from the reverse proxy (nginx sets it, and the app ignores it from anywhere else). |
| 4 | One bucket for everything. A cheap call and an LLM chat cost the same. One shopper can use the whole tenant quota. | From code | Separate limits per route group, a per visitor limit for chat (`customer_id`), and a daily token or cost budget per tenant. |
| 5 | Request body size is not limited. The `max_request_size_mb` setting exists but nothing reads it. A 6 MB body was accepted. | Tested live | Add a body size middleware (for example 1 MB for chat) and set `client_max_body_size` in nginx. |
| 6 | `/docs`, `/redoc`, `/openapi.json`, `/metrics` and `/health/db` are public. `/health/db` shows the database name, host, port and exact PostgreSQL version. `/metrics` carries tenant ids in labels. | Tested live | Disable the docs in production. Put `/metrics` and `/health/db` on an internal port or behind auth. Keep `/health/live` and `/health/ready` minimal. |
| 7 | `POST /sync/catalog` fetches any `shop_url` the caller gives. Blind SSRF: internal hosts answer in about 0.01 s, and a blackholed address hung for 30 s, so timing leaks the internal network. | Tested live | Store the shop URL per tenant on the server. Reject private, loopback and link local ranges after DNS resolution, unless the host is on an allowlist (`prestashop` in dev). Do not follow redirects. |
| 8 | No TLS. nginx listens on port 80 only. Cookies are not `Secure`. No HSTS. | From code | Terminate TLS in nginx. Set `SESSION_SECURE_COOKIE=true` in the backoffice. Add HSTS. |
| 9 | No security headers on ai-core, the backoffice or PrestaShop (no `X-Frame-Options`, no CSP, no `nosniff`). Banners show `uvicorn`, `PHP/8.4` and `Apache/2.4`. The Filament admin can be framed (clickjacking). | Tested live | Add the headers in nginx. Hide version banners. |
| 10 | The PrestaShop module folder serves its dev files to anyone: `AGENTS.md`, `README.md`, `composer.json`, `phpunit.xml` all return 200. `AGENTS.md` describes the security posture. | Tested live | Add a `.htaccess` in the module that denies `*.md`, `*.json`, `*.xml`, `*.lock` and `tests/`, or build the module zip without those files. |
| 11 | The backoffice container runs as root and serves with `php artisan serve` (development stage only). | Tested live | Add a production Docker stage: non root user, php-fpm and nginx. |
| 12 | The app connects to Postgres as `saas_user`, a **superuser** and the table owner. | Tested live | Create a limited application role (no superuser, no owner, only the grants it needs). Run migrations with a separate owner role. This is also the first step for row level security (item 20). |
| 13 | One API key has full power. The storefront key (held by PrestaShop) can also call the admin agent (`delete_customer_data`, `update_product_prices`, `bulk_order_modification`). | From code | Scoped keys: a chat only key for the storefront, an admin key for the backoffice. Check the scope in the middleware. |
| 14 | Tenants whose key was issued before HMAC have no secret and are refused. | Tested live | Run `python -m scripts.reissue_tenant_credentials <tenant_id>`, then restart ai-core (the auth cache is in memory). |
| 15 | A signed request replayed unchanged is accepted for 5 minutes. No nonce. | From code | Store a short lived nonce or the signature in Redis (SETNX with a 5 minute TTL). |

## 2. Soon after

- GitGuardian reports the old default `Admin123!` in commit `34c5c61`. Dismiss the incident as revoked in the GitGuardian dashboard and change the local PrestaShop admin password.
- The storefront also loads an unrelated legacy widget (`/assistant-widget.js`, element `<assistant-chat-widget>`) from the theme `footer.tpl`. It covers our chat bubble and posts to a route ai-core does not have. Remove it from the demo theme.
- `POST /chat/feedback`: the `message_id` returned to the widget looks like `msg_ab12...`, which is not the stored message UUID, so feedback is accepted and silently not saved. Verify and return the real id.
- A per visitor throttle in the PrestaShop chat controller (today only the Origin check protects it).
- `infrastructure/k8s` is stale (OpenAI model names, a Chroma server, settings the app does not read). Rewrite or remove it.
- GitHub Actions are pinned by mutable tags. Pin by commit SHA.
- Grafana falls back to `admin`/`admin` and promtail mounts `docker.sock`. Fix before any shared environment.
- Not tested yet: indirect prompt injection through product descriptions (a product text that says "ignore your rules"), PII in logs, `pip-audit` and `composer audit`, conversation data retention and deletion (GDPR).

## 3. Row level security (to decide together)

Today isolation is done in the application: every repository query filters by
`tenant_id`, and a cross tenant sweep of all id endpoints found one gap (pending
admin actions, fixed). RLS would add a second lock inside Postgres, so a missing
filter in future code cannot leak data.

- **For**: defence in depth against a forgotten `tenant_id` filter or a raw query.
- **Against**: extra moving parts, a small per query cost, harder debugging.
- **Prerequisites**: item 12 first (a non owner, non superuser app role, otherwise RLS is bypassed). Then a policy per table, and `SET LOCAL app.tenant_id` at the start of every transaction (with asyncpg and a connection pool this must be set per transaction, not per connection).
- **Suggested path**: limited DB role, then RLS on the two or three most sensitive tables (conversations, messages, coupons) behind a feature flag, with the existing isolation tests running against it.

## 4. Load test plan

Goal: know how many chat requests per second one ai-core instance can serve, and
where it breaks first.

1. **Tool**: k6 (scripted in JS) or Locust (Python). Run from a separate machine or container.
2. **Do not load test the real Groq API.** It has a free tier rate limit and it costs money. Point `LLM_GROQ_BASE_URL` (or the provider factory) at a small fake, Groq compatible server that answers after a fixed delay.
3. **Scenarios**:
   - cheap read calls (`/tenants/current`, `/rules`) to measure the framework and auth overhead;
   - chat with retrieval (embedding plus ChromaDB plus fake LLM) at 1, 5, 20, 50 users;
   - mixed traffic: 90% chat, 5% rules, 5% analytics.
4. **Measure**: p50 and p95 latency, error rate, 429 count, CPU and memory of ai-core, Postgres connections, Redis ops, ChromaDB query time, the embedding model (ONNX, CPU bound).
5. **Prepare first**: fix items 2 to 5 above, otherwise the test only measures the limiter. Run ai-core with several workers (`uvicorn --workers N`) in a production like compose, not the `--reload` dev container.
6. **Pass criteria (proposal)**: p95 chat latency under 3 s at the expected shop traffic, no 5xx, graceful 429 beyond the limit.
