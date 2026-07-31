#!/usr/bin/env bash
#
# One-command startup for a live public demo (defense day):
# brings up the full docker-compose stack, waits for ai-core to be
# healthy, then starts a Cloudflare Quick Tunnel and prints the public
# URL. Ctrl+C stops the tunnel; the stack keeps running (stop it
# separately with `docker compose down` if needed).
#
# Requirements: docker, cloudflared (brew install cloudflared)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/infrastructure/docker"
COMPOSE_FILE="$COMPOSE_DIR/docker-compose.dev.yml"

echo "==> Starting docker-compose stack..."
(cd "$COMPOSE_DIR" && docker compose -f "$COMPOSE_FILE" up -d)

echo "==> Waiting for ai-core to be healthy..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:8000/health >/dev/null 2>&1; then
        echo "    ai-core is up."
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "    ai-core did not become healthy in time - check 'docker logs saas_ai_core'." >&2
        exit 1
    fi
    sleep 2
done

echo "==> Starting Cloudflare Quick Tunnel (Ctrl+C to stop the tunnel)..."
echo "    The public URL will appear below once Cloudflare assigns one."
echo ""

cloudflared tunnel --url http://localhost:8000
