# backoffice

## Overview

The admin panel for shop owners, built with Laravel and Filament. It is a pure REST client of ai-core. It never reads the ai-core database. Its own SQLite file only holds panel users and sessions.

## Stack

- **Language / Runtime**: PHP 8.3
- **Framework**: Laravel 13, Filament 3 (Livewire), Vite
- **Package manager**: Composer and npm

## Key files

| File | Owns |
|---|---|
| `app/Services/AiCoreClient.php` | Every HTTP call to ai-core (base URL, auth headers, errors) |
| `config/aicore.php` | `AICORE_API_URL`, `AICORE_ROOT_URL`, `AICORE_TENANT_ID`, `AICORE_API_KEY` |
| `app/Filament/Pages/` | AdminAgent, Conversations, CostTracking, Insights, Rules, TenantSettings |
| `app/Filament/Widgets/` | Dashboard charts and stat cards (time series come from ai-core) |
| `app/Providers/Filament/AdminPanelProvider.php` | Panel setup, auth middleware, page and widget registration |
| `app/Exceptions/AiCoreException.php` | Error type thrown when ai-core fails |

## Commands

```bash
composer setup                # install, .env, key, migrate, npm build
composer dev                  # server, queue, logs, vite together
php artisan test              # or: composer test
vendor/bin/pint --test        # format check (CI). Drop --test to fix
```

## Conventions

- Pages and widgets get data through `AiCoreClient` only. Do not add Eloquent models for ai-core data.
- Format with Pint before committing. CI runs `pint --test`.

## Gotchas

- `AiCoreClient` authenticates with the dev `X-Tenant-ID` header (`AICORE_TENANT_ID`). It does not sign requests, so the real API key path, which now needs `X-Timestamp` and `X-Signature`, does not work from here yet.
- Some feature tests fail inside the docker container because they assume the default `AICORE_API_URL` (`http://localhost:8000/api/v1`) while the container sets `http://ai-core:8000/api/v1`. Run them with the default URL before calling a failure real.
- The running docker image has no `tests/` folder. Run PHPUnit in a throwaway container from the same image with `tests/` and `phpunit.xml` mounted, plus `APP_KEY`, an empty `.env`, and sqlite in memory.
- The Rules page keeps condition and action keys its form does not manage (for example `max_per_hour`), so editing a rule does not erase them.
- The docker image only has a `development` stage and serves with `php artisan serve`. It is not production ready.

## Agent skills

- [laravel-specialist](../../.agents/skills/laravel-specialist/): `jeffallan/claude-skills`, Laravel structure, services, and testing

_Drafted by /audit from the repo, worth a quick human pass. Edit freely: once a line stops matching this draft, later runs treat it as curated and will flag rather than overwrite it._
