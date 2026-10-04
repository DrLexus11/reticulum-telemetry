# reticulum-telemetry

Health telemetry for a Reticulum mesh of ESP32 boards: each board's report
travels over the mesh to a gateway, and lands in Prometheus and Grafana.

```
board --(one encrypted Reticulum packet)--> gateway --MQTT--> backend --> Prometheus --> Grafana
```

- **Boards** (firmware in `microReticulum_Firmware`, `TelemetryUplink.h`) send a
  29-34 byte report every 5 minutes to the nearest gateway they have heard
  announce `rnstransport.telemetry.uplink`: uptime and restarts, heap and its
  largest block, which carriers are present and up, peers, paths, battery.
- **The gateway** (`gateway/`, Python) is any node with an uplink. It announces
  that destination, decodes each report and publishes it as JSON on
  `mesh/telemetry/<sender>`, retained.
- **The backend** (`backend/`, Go) subscribes and serves Prometheus metrics,
  `mesh_board_*`, one series set per board.
- **The topic layout** -- what is published, and what is held for commands: `docs/MQTT.md`.
- **Deploy** (`deploy/`): Mosquitto, Prometheus and Grafana under rootless podman,
  with a provisioned "Mesh boards" dashboard.

## The wire format

Version 1 is owned by the firmware repository: `TelemetryCodec.h` and its
Python twin `tools/telemetry_codec.py`, pinned by
`tests/fixtures/telemetry_v1.json`. `gateway/telemetry_codec.py` and
`tests/fixtures/telemetry_v1.json` here are copies; a change is made there and
copied here, and `tests/test_codec_fixture.py` catches a copy that drifted.

## Surviving a reboot

`deploy/install-services.sh` makes the stack come back on its own: the
containers run with `--restart=always` under `podman-restart.service`, and the
gateway and backend run as enabled systemd user services (units in
`deploy/systemd/`). It needs lingering (`loginctl enable-linger $USER`) and
asks once for the gateway's name, which stays in
`~/.config/reticulum-telemetry/gateway.env`, outside the repository.

## Two environments

- **Dev:** the stack on the mesh host itself (`deploy/up.sh`), where the gateway
  runs. As above.
- **Production:** `deploy/prod/` -- the same stack under docker compose on a
  separate host (`deploy/prod/deploy.sh` there). Its broker and Prometheus are
  on loopback, Grafana on that host's tailnet address. The mesh host reaches it
  through an SSH tunnel and bridges `mesh/telemetry/#` from the dev broker
  (`deploy/prod/connect-dev.sh <ssh alias>` on the mesh host). The bridge keeps
  a persistent session, so reports queue on the dev broker while the tunnel is
  down. Host names, addresses and the Grafana admin password stay in host-local
  files (`deploy/prod/.env`, `~/.config/reticulum-telemetry/`), never here.

## Running it on one host

```
deploy/up.sh                                          # Mosquitto, Prometheus, Grafana
python gateway/telemetry_gateway.py --identity ~/.config/telemetry-gateway/identity --name <gateway name>
(cd backend && go build -o ../reticulum-telemetry-backend .) && ./reticulum-telemetry-backend
python tools/send_test_report.py <gateway destination hash>   # a synthetic report, end to end
```

Grafana is at http://127.0.0.1:3000 (anonymous viewer). Everything listens on
loopback only: none of these services has authentication worth the name in
this setup. The gateway reaches Reticulum through the host's shared instance.
Broker credentials, where a broker needs them, come from `MQTT_USERNAME` and
`MQTT_PASSWORD` in the environment, never from the command line.

## Tests

```
python -m unittest discover -s tests      # codec against the fixture, the JSON message
(cd backend && go test ./...)             # metrics from a message; absent fields; refusals
```

## Contributing

Nothing operational goes in this repository: no node or destination hashes, no
identities, keys, broker credentials, coordinates or callsigns -- not in code,
tests, fixtures, dashboards or commit messages. Fixtures are synthetic.
