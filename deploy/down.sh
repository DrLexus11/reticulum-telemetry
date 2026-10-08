#!/usr/bin/env bash
# Stop and remove the telemetry containers. Data in ~/.local/share/reticulum-telemetry is kept.
# A container already gone is fine (--ignore); any other failure is reported and
# makes the script fail, after trying the rest.
status=0
for c in rt-alloy rt-grafana rt-prometheus rt-mosquitto; do
  if podman rm -f --ignore "$c" >/dev/null; then echo "removed $c (or already gone)"; else echo "could not remove $c" >&2; status=1; fi
done
exit $status
