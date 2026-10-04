#!/usr/bin/env bash
# Make the stack survive a host reboot: the containers come back through
# podman-restart.service (they run with --restart=always, see up.sh), and the
# gateway and backend run as enabled systemd user services. Needs lingering
# (loginctl enable-linger $USER) so user services start without a login.
#
# The unit files assume the defaults this repo documents: the repo at
# ~/projects/reticulum-telemetry, the RNS venv at
# ~/.local/share/rnode-rns-venv, the backend built to ~/.local/bin, the
# gateway identity in ~/.impr-tak/telemetry-gateway/identity. The gateway's
# name is this host's own setting, asked for once and kept in
# ~/.config/reticulum-telemetry/gateway.env -- never in the repository.
# Edit the copies in ~/.config/systemd/user for anything else.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$units" "$HOME/.impr-tak"
env_dir="${XDG_CONFIG_HOME:-$HOME/.config}/reticulum-telemetry"
if [ ! -f "$env_dir/gateway.env" ]; then
  read -r -p "Gateway name (shown in every report it publishes): " name
  [ -n "$name" ] || { echo "a gateway needs a name" >&2; exit 1; }
  mkdir -p "$env_dir"
  printf 'GATEWAY_NAME=%s\n' "$name" > "$env_dir/gateway.env"
fi
cp "$here"/systemd/reticulum-telemetry-*.service "$units/"
systemctl --user daemon-reload
systemctl --user enable podman-restart.service
systemctl --user enable --now reticulum-telemetry-gateway.service reticulum-telemetry-backend.service
loginctl show-user "$USER" -p Linger
