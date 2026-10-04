#!/usr/bin/env bash
# Deploy or update the production stack on this host, from this checkout.
# Creates deploy/prod/.env on first run: Grafana on this host's tailnet
# address, and a random Grafana admin password that is never printed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
if [ ! -f .env ]; then
  bind="$(tailscale ip -4 2>/dev/null | head -1)"
  umask 077
  printf 'GRAFANA_BIND=%s\nGRAFANA_ADMIN_PASSWORD=%s\n' "${bind:-127.0.0.1}" "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')" > .env
  echo "wrote $here/.env (Grafana on ${bind:-127.0.0.1}; admin password inside, mode 600)"
fi
docker compose up -d --build
docker compose ps --format '{{.Service}} {{.Status}}'
