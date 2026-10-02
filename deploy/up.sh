#!/usr/bin/env bash
# Mosquitto, Prometheus and Grafana for the telemetry stack, under rootless
# podman on the host network, every service bound to loopback: none of them
# has authentication worth the name here, so nothing off this host may reach
# them. Data lives in ~/.local/share/reticulum-telemetry, outside the repo.
#
#   deploy/up.sh        start (or restart) all three
#   deploy/down.sh      stop and remove them; data is kept
#
# The gateway (gateway/telemetry_gateway.py) and the backend (backend/) run as
# ordinary processes beside these.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}/reticulum-telemetry"
mkdir -p "$data/mosquitto" "$data/prometheus" "$data/grafana"

run() { podman rm -f "$1" >/dev/null 2>&1 || true; podman run -d --name "$@" >/dev/null; echo "started $1"; }

run rt-mosquitto --network host \
  -v "$here/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro,Z" \
  -v "$data/mosquitto:/mosquitto/data:Z" \
  docker.io/library/eclipse-mosquitto:2

run rt-prometheus --network host --user "$(id -u):$(id -g)" --userns keep-id \
  -v "$here/prometheus.yml:/etc/prometheus/prometheus.yml:ro,Z" \
  -v "$data/prometheus:/prometheus:Z" \
  docker.io/prom/prometheus:latest \
  --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=30d --web.listen-address=127.0.0.1:9090

run rt-grafana --network host --user "$(id -u):$(id -g)" --userns keep-id \
  -e GF_SERVER_HTTP_ADDR=127.0.0.1 -e GF_SERVER_HTTP_PORT=3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
  -e GF_ANALYTICS_REPORTING_ENABLED=false -e GF_ANALYTICS_CHECK_FOR_UPDATES=false \
  -v "$here/grafana/provisioning:/etc/grafana/provisioning:ro,Z" \
  -v "$here/grafana/dashboards:/var/lib/grafana/dashboards:ro,Z" \
  -v "$data/grafana:/var/lib/grafana/data:Z" \
  -e GF_PATHS_DATA=/var/lib/grafana/data \
  docker.io/grafana/grafana-oss:latest

echo "MQTT 127.0.0.1:1883 · Prometheus http://127.0.0.1:9090 · Grafana http://127.0.0.1:3000 (dashboard: Mesh boards)"
