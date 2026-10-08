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
# systemd user services beside these: deploy/install-services.sh.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}/reticulum-telemetry"
mkdir -p "$data/mosquitto" "$data/prometheus" "$data/grafana"
# Host-local broker additions (mosquitto.conf include_dir), e.g. the bridge to
# production. Outside the repo: they name hosts.
confd="${XDG_CONFIG_HOME:-$HOME/.config}/reticulum-telemetry/mosquitto.d"
mkdir -p "$confd"

# --restart=always with podman-restart.service enabled (install-services.sh)
# brings the containers back after the host reboots.
run() { podman rm -f "$1" >/dev/null 2>&1 || true; podman run -d --restart=always --name "$@" >/dev/null; echo "started $1"; }

run rt-mosquitto --network host \
  -v "$here/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro,Z" \
  -v "$confd:/mosquitto/config/conf.d:ro,Z" \
  -v "$data/mosquitto:/mosquitto/data:Z" \
  docker.io/library/eclipse-mosquitto:2

run rt-prometheus --network host --user "$(id -u):$(id -g)" --userns keep-id \
  -v "$here/prometheus.yml:/etc/prometheus/prometheus.yml:ro,Z" \
  -v "$data/prometheus:/prometheus:Z" \
  docker.io/prom/prometheus:latest \
  --config.file=/etc/prometheus/prometheus.yml --storage.tsdb.path=/prometheus \
  --storage.tsdb.retention.time=30d --web.listen-address=127.0.0.1:9090 \
  --web.enable-remote-write-receiver

# The topology plugin is copied, not bind-mounted from the checkout: a branch
# switch removes and recreates the folder, and a running Grafana would keep the
# deleted one (module.js 404, "Error loading", 2026-10-08). Run this again after
# changing the plugin.
rm -rf "$data/grafana-plugins" && mkdir -p "$data/grafana-plugins"
cp -r "$here/grafana/plugins/." "$data/grafana-plugins/"

run rt-grafana --network host --user "$(id -u):$(id -g)" --userns keep-id \
  -e GF_SERVER_HTTP_ADDR=127.0.0.1 -e GF_SERVER_HTTP_PORT=3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true -e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
  -e GF_ANALYTICS_REPORTING_ENABLED=false -e GF_ANALYTICS_CHECK_FOR_UPDATES=false \
  -v "$here/grafana/provisioning:/etc/grafana/provisioning:ro,Z" \
  -v "$here/grafana/dashboards:/var/lib/grafana/dashboards:ro,Z" \
  -v "$data/grafana-plugins/drlexus11-meshtopology-panel:/var/lib/grafana/plugins/drlexus11-meshtopology-panel:ro,Z" \
  -e GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=drlexus11-meshtopology-panel \
  -v "$data/grafana:/var/lib/grafana/data:Z" \
  -e GF_PATHS_DATA=/var/lib/grafana/data \
  docker.io/grafana/grafana-oss:latest

# The log collector (T6): this host's logs to production's Loki, through the
# tunnel. Only the logs folder and the serial captures are mounted, read-only:
# ~/.impr-tak itself holds identities and secrets a log shipper has no business
# seeing.
mkdir -p "$data/alloy" "$HOME/.impr-tak/logs" "$HOME/.impr-tak/soak"
run rt-alloy --network host --user "$(id -u):$(id -g)" --userns keep-id \
  -e ALLOY_HOST="$(uname -n)" \
  -v "$here/alloy/config.alloy:/etc/alloy/config.alloy:ro,Z" \
  -v "$HOME/.impr-tak/logs:/logs:ro" \
  -v "$HOME/.impr-tak/soak:/soak:ro" \
  -v "$data/alloy:/alloy-data:Z" \
  docker.io/grafana/alloy:v1.11.3 \
  run --server.http.listen-addr=127.0.0.1:12345 --storage.path=/alloy-data /etc/alloy/config.alloy

echo "MQTT 127.0.0.1:1883 · Prometheus http://127.0.0.1:9090 · Grafana http://127.0.0.1:3000 (dashboard: Mesh boards)"
