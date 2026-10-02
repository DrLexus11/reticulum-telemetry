#!/usr/bin/env bash
# Stop and remove the telemetry containers. Data in ~/.local/share/reticulum-telemetry is kept.
for c in rt-grafana rt-prometheus rt-mosquitto; do podman rm -f "$c" >/dev/null 2>&1 && echo "removed $c"; done
