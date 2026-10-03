# MQTT: the topic layout

What a gateway publishes, and the namespaces held for what comes next. The
broker is the seam between the mesh and anything on the internet side --
dashboards today, IoT integrations and commands later -- so the layout is a
contract, versioned like the wire format.

## In use: telemetry

| Topic | Direction | QoS | Retained | Payload |
|---|---|---|---|---|
| `mesh/telemetry/<sender>` | gateway -> broker | 1 | yes | one decoded report, JSON (below) |

- `<sender>` is the board's 32-bit sender id, as 8 lower-case hex digits
  (`TelemetryCodec.h`; the same id the position codec carries). Two boards in
  one fleet sharing an id would share a topic: the id is derived from the node's
  identity, and a fleet of a few hundred makes that a one-in-ten-thousand
  event, not a design case.
- **Retained**, so a subscriber that starts late sees every board's latest
  state at once. A board that stops reporting keeps its last message;
  `received_at` is how a consumer tells it is old.
- **QoS 1 from the gateway to the broker**: on that leg a report may arrive
  twice, never not at all. It says nothing about subscribers: one without a
  persistent session misses reports while it is away and gets only the
  retained latest state when it returns. Telemetry here is **latest state**,
  not history; history is the backend's job (Prometheus keeps the series).
  Every report carries `uptime_s` and `received_at`, so a duplicate is harmless.
- One gateway or several: whichever gateway a report reached publishes it. Two
  gateways that both hear the same report publish the same topic; `gateway`
  says which.

### Payload, version 1

An example with synthetic identifiers (sender, gateway and times are made up;
the shape and the units are real):

```json
{
  "v": 1,
  "sender": "0a0b0c0d",
  "received_at": 1790000000.0,
  "hops": 2,
  "via": "LocalInterface[rns/default]",
  "gateway": "gw-1",
  "uptime_s": 2502,
  "reset": "software",
  "boots": 14,
  "crashes": 2,
  "panics": 1,
  "heap_bytes": 88064,
  "largest_bytes": 64512,
  "psram_bytes": 2023424,
  "interfaces_present": [
    "lora",
    "wifi"
  ],
  "interfaces_up": [
    "lora",
    "wifi"
  ],
  "ble_peers": 0,
  "espnow_peers": 0,
  "paths": 29,
  "nodes": 48,
  "relaying": true,
  "relay_expected": true,
  "time_current": true,
  "battery_mv": null,
  "battery_pct": null
}
```

Built by `gateway/report.py` (`to_message`), tested in `tests/test_report.py`.
Unknown values are `null`, never a made-up zero: a battery that is not measured
is not empty. `v` changes only for a breaking change; new fields are added
without one, and a consumer ignores fields it does not know.

## Held for the node control plane

Agreed 2026-10-03 (firmware `TAKDeliveryPlan.md`, *Direction after PR F*):
commands travel the other way -- from the broker, through a gateway, over
LXMF to the node, which may be offline and receives them when it reappears.
Nothing publishes or subscribes to these yet; they are written down now so
nothing else takes them.

| Topic | Direction | Purpose |
|---|---|---|
| `mesh/command/<sender>` | broker -> gateway | a command for one node |
| `mesh/reply/<sender>/<command id>` | gateway -> broker | that node's answer |
| `mesh/log/<sender>` | gateway -> broker | device log lines (F5) |

Commands will be authenticated end to end (signed for the node, not trusted
because they came from the broker), and the broker stays on loopback or behind
authentication: a topic anyone can publish to must never be a way to
reconfigure a radio.
