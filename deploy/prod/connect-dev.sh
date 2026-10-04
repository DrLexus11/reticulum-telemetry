#!/usr/bin/env bash
# On the dev host (the one running the gateway and the dev stack): tunnel to
# the production host and bridge mesh/telemetry/# from the dev broker to it.
#
#   deploy/prod/connect-dev.sh <ssh host alias of the production host>
#
# The alias must work non-interactively (key, BatchMode). The bridge keeps a
# persistent session, so reports queue on the dev broker while the tunnel is
# down and are delivered when it returns.
set -euo pipefail
host="${1:?usage: connect-dev.sh <ssh host alias>}"
here="$(cd "$(dirname "$0")/.." && pwd)"
cfg="${XDG_CONFIG_HOME:-$HOME/.config}/reticulum-telemetry"
mkdir -p "$cfg/mosquitto.d" "$cfg/../systemd/user"
printf 'PROD_SSH_HOST=%s\n' "$host" > "$cfg/prod-tunnel.env"
cat > "$cfg/mosquitto.d/bridge-prod.conf" <<CONF
# Bridge to the production broker through the SSH tunnel (connect-dev.sh).
connection prod
address 127.0.0.1:18830
topic mesh/telemetry/# out 1
cleansession false
clientid reticulum-telemetry-bridge-$(hostname)
restart_timeout 10 60
CONF
cp "$here/systemd/reticulum-telemetry-prod-tunnel.service" "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now reticulum-telemetry-prod-tunnel.service
podman restart rt-mosquitto >/dev/null
echo "tunnel: $(systemctl --user is-active reticulum-telemetry-prod-tunnel.service); bridge configured in $cfg/mosquitto.d/bridge-prod.conf"
