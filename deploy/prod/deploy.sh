#!/usr/bin/env bash
# Deploy or update the production stack on this host, from this checkout.
# Fills deploy/prod/.env where a value is missing or empty: Grafana on this
# host's tailnet address, and a random Grafana admin password never printed.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here"
umask 077
touch .env
# Grafana's address: this host's tailnet address, or loopback without Tailscale.
if ! grep -q '^GRAFANA_BIND=.' .env; then
  bind="$(tailscale ip -4 2>/dev/null | head -1 || true)"
  sed -i '/^GRAFANA_BIND=/d' .env
  printf 'GRAFANA_BIND=%s\n' "${bind:-127.0.0.1}" >> .env
  echo "Grafana will listen on ${bind:-127.0.0.1} (deploy/prod/.env)"
fi
# A generated admin password, never printed, when none is set.
if ! grep -q '^GRAFANA_ADMIN_PASSWORD=.' .env; then
  sed -i '/^GRAFANA_ADMIN_PASSWORD=/d' .env
  printf 'GRAFANA_ADMIN_PASSWORD=%s\n' "$(head -c 24 /dev/urandom | base64 | tr -d '/+=')" >> .env
  echo "generated a Grafana admin password in $here/.env (mode 600)"
fi
chmod 600 .env
docker compose up -d --build
# Grafana bind-mounts folders of this checkout (dashboards, the topology
# plugin). A git checkout or pull can replace a folder, and a running container
# would keep the old one; a restart mounts what is there now.
docker compose restart grafana
docker compose ps --format '{{.Service}} {{.Status}}'
