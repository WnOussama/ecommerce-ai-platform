# prestashop/aiassistant

## Overview

A PrestaShop 8 module that adds a chat widget to the storefront and relays each message to ai-core. The browser never sees the ai-core URL or the tenant id: the widget calls a local front controller, and PHP forwards the request server side using the credentials set in the module configuration.

## Stack

- **Language / Runtime**: PHP 7.2.5+ (PrestaShop module)
- **Package manager**: Composer (dev only, PHPUnit 9)

## Key files

| File | Owns |
|---|---|
| `aiassistant.php` | Module class: install, hooks, configuration form |
| `classes/AiCoreClient.php` | Server side HTTP client to ai-core (sends `X-Tenant-ID`) |
| `controllers/front/chat.php` | Same origin endpoint the widget posts to |
| `views/js/widget.js`, `views/templates/hook/widget.tpl` | Widget UI. Messages are escaped before rendering |
| `tests/AiCoreClientTest.php` | Real HTTP tests against ai-core |

## Commands

```bash
composer install
vendor/bin/phpunit    # skips itself if nothing answers on http://localhost:8000
```

## Conventions

- Keep ai-core credentials on the server. Never pass the API URL or tenant id to `widget.js`.
- Escape user and bot text before inserting it into the page.
- `customer_id` (guest or customer id) and `cart_total` are read from PrestaShop's session in `chat.php`, never from the browser request.
- The widget can only apply cart rules named `AI Assistant discount`, and PrestaShop's own `checkValidity` must pass. Cross site POSTs are refused by an Origin check.

## Gotchas

- The demo shop also loads an unrelated `assistant-widget.js` (the `<assistant-chat-widget>` element) from the theme `footer.tpl`. It is not in this repo and its bubble covers ours. Hide it when testing this widget.
- The client sends only `X-Tenant-ID`, with no API key or signature. That works only while ai-core has `SECURITY_ALLOW_DEV_TENANT_HEADER=true`. It must be updated to sign requests before a production setup.

## Agent skills

- [prestashop-module-development](../../../../.agents/skills/prestashop-module-development/): `jeffsenso/prestashop-skills`, hooks, front controllers, and module configuration

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
