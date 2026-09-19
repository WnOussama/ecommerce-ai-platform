# Version 1 test report

Date: 2026-09-19. Environment: local Docker stack (ai-core, Postgres, Redis,
backoffice, PrestaShop 8, MySQL) on branch `dev`, real Groq LLM
(`openai/gpt-oss-120b`), real local embeddings, real Chrome.

## Automated

| Suite | Result |
|---|---|
| ai-core (pytest, unit and Postgres integration) | 481 passed |
| ai-core on a database built only by `alembic upgrade head` (CI conditions) | passed |
| ai-core lint (`ruff check`, `ruff format --check`) | clean |
| Backoffice (PHPUnit) | 40 passed |
| Backoffice lint (Pint) | clean |
| PrestaShop module (PHPUnit against the live ai-core) | 5 passed |
| Production style auth attacks (dev header off, 13 checks) | 13 of 13 |

## Browser, storefront (`http://localhost:8080`)

| Test | Result |
|---|---|
| Product question in French returns the right product with real price and stock | Pass |
| Product that does not exist ("sac à dos randonnée") is not invented | Pass |
| Prompt injection is blocked | Pass |
| XSS payload is shown as text and never executes | Pass |
| "Find a watch" takes the shopper to the product page | Pass |
| Conversation is restored after the page changes | Pass |
| Threshold coupon: real cart over 100 EUR gives 15%, one click applies it in PrestaShop | Pass |
| A second threshold coupon is refused inside the 30 day cooldown | Pass |
| Welcome coupon for a brand new visitor, once per visitor | Pass (API and storefront endpoint) |
| AI coupons are non combinable in PrestaShop | Pass (fixed during the test) |

## Browser, backoffice (`http://localhost:8090/admin`)

| Page | Result |
|---|---|
| Login and dashboard: 107 conversations, resolution 0.9%, cost $0.32, guardrail blocks 5, hallucination 13.6% | Pass: every figure recomputed from the database |
| Conversations: list, status filter, full transcript of a real storefront chat | Pass |
| Rules: list shows the new condition labels | Pass |
| Rules: edit through the UI keeps hidden keys (`max_per_hour`, cooldown, condition) | Pass |
| Rules: create through the UI reaches ai-core correctly | Pass |
| Cost tracking: totals equal ai-core's cost report (0.317, 147 messages, 104 conversations) | Pass |
| Insights: products, unmet requests, intents, peak hours, coupons | Loads with real data. Two metrics are misleading, see the backlog |
| Admin agent: French read only request mapped to `get_analytics` and answered | Pass |
| Admin agent: destructive request refused ("Reason is required") | Pass (safe), but see the backlog |
| Tenant settings: usage shown, Save works | Pass |

Not done on purpose: regenerating the API key (would invalidate it) and placing
an order.

## Bugs found and fixed during this test

- Two AI coupons stacked on the real cart. Fixed: cart rules are now non combinable.
- Backoffice panel users were lost on every container recreate. Fixed: the database is on a named volume.
- Pending admin actions and conversation close were not scoped by tenant. Fixed and covered by tests.
- Signing bugs found by code review (timezone skew, query string, non ASCII signature, wrong encryption key). Fixed.

## Known gaps

See `security-and-scaling.md` in this folder: what to do before production, product gaps, the row level security discussion and the load test plan.
