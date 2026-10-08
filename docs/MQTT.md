# MQTT: the topic layout

What a gateway publishes, and the namespaces held for what comes next. The
broker is the seam between the mesh and anything on the internet side --
dashboards today, IoT integrations and commands later -- so the layout is a
contract, versioned like the wire format.

## In use: telemetry

| Topic | Direction | QoS | Retained | Payload |
|---|---|---|---|---|
| `mesh/telemetry/<sender>` | gateway -> broker | 1 | yes | one decoded health report, JSON (below) |
| `mesh/telemetry/<sender>/detail` | gateway -> broker | 1 | yes | one decoded detail report, JSON (below) |
| `mesh/telemetry/<sender>/backfill` | gateway -> broker | 1 | **no** | one report the board kept while no gateway was in reach (T2, below) |

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
  "name": "board-1",
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
`name` is the board's announced NomadNet name, which the gateway learns from
the board's own announces (`gateway/board_names.py`): the sender id is the
first four bytes of the same identity's hash. It is `null` until the gateway
has heard one; the backend then shows the board by its sender id.
Unknown values are `null`, never a made-up zero: a battery that is not measured
is not empty. `v` changes only for a breaking change; new fields are added
without one, and a consumer ignores fields it does not know.

### Detail payload, version 1

Every 30 minutes, and with a board's first health report after it boots. The
wire format is the firmware's `TelemetryDetailCodec.h` (0x21), pinned by
`tests/fixtures/telemetry_detail_v1.json`. Synthetic identifiers again:

```json
{
  "v": 1,
  "sender": "0a0b0c0d",
  "name": "board-1",
  "received_at": 1790000000.0,
  "hops": 2,
  "via": "LocalInterface[rns/default]",
  "gateway": "gw-1",
  "uptime_s": 2502,
  "firmware": {"hash": "a1b2c3d4", "version": "1.86", "env": "impr-rad01-rev1"},
  "interfaces": [
    {"interface": "lora", "up": true, "rx_bytes": 15982, "tx_bytes": 9120},
    {"interface": "udp", "up": true, "rx_bytes": 2557, "tx_bytes": 1840}
  ],
  "radio": {"rssi_dbm": -57, "snr_db": 10.0, "noise_dbm": -110,
            "utilisation_pct": 14, "airtime_pct": 12},
  "propagation": {"messages": 7, "bytes": 2048, "peers": 1,
                  "sync_ok": 0, "sync_failed": 0, "last_sync_s": null},
  "neighbours": [
    {"node": "11223344", "name": "board-2", "interface": "lora", "rssi_dbm": -68, "heard_s": 120}
  ],
  "neighbours_truncated": false
}
```

Built by `gateway/report.py` (`detail_to_message`), tested in
`tests/test_detail_fixture.py`.

- `firmware.hash` is the first four bytes of the running image's SHA-256;
  `version` reads as `rnodeconf` prints it.
- Interface byte counts are **cumulative since the board booted**, as each
  interface counts them: take a rate, and read a drop as a restart. Kinds:
  `lora`, `ble_peer`, `espnow`, `tcp_server`, `tcp_client`, `udp`, `auto`,
  `serial`, `halow`, `other`.
- `radio` is `null` on a board without LoRa; `propagation` is `null` on a
  board that runs no propagation node; `last_sync_s` is `null` until a sync
  has completed.
- `neighbours` are the nodes the board heard **directly** (one hop) announce,
  most recent first, at most 48: `node` is four bytes of the identity hash --
  a board's own sender id when the neighbour is a board. `name` is what the
  gateway has heard that identity announce (NomadNet for boards, LXMF display
  names for people), else `null`. `heard_s` has minute resolution and
  saturates at 255 minutes.

### Backfill, version 1 (T2)

A board with no gateway in reach keeps its health and detail reports and,
about hourly, sends them as one LXMF message -- a batch, the firmware's
`TelemetryBatchCodec.h` (0x31), pinned by `tests/fixtures/telemetry_batch_v1.json`
-- to the gateway's LXMF delivery destination, through its own propagation
node. The gateway collects batches from every propagation node it hears, and
from any named with `--propagation-node`, and accepts one only when LXMF
validated its signature and the signer is the board the batch names
(`gateway/lxmf_inbox.py`, `gateway/backfill.py`).

Each report becomes one message on `mesh/telemetry/<sender>/backfill`, **not
retained**: on the live topic an hour-old report would overwrite the board's
current state. The message is the live message's shape -- health or detail,
as above -- with `received_at` set to when the report was **taken**, plus:

```json
  "kind": "health",
  "backfill": {"exact": true, "collected_at": 1790003600.0}
```

`exact` is false only when the board's clock was not set, so the time was
counted back from collection and is late by however long the batch waited.
The backend writes these into Prometheus at their own time through its
remote-write receiver, under the live metric names with an added
`backfill="exact"` or `"approximate"` label, so dashboards fill the gap without
a change. Prometheus accepts samples up to 48 h old (`out_of_order_time_window`):
a longer partition loses its oldest reports. `tools/send_test_batch.py` sends
a test batch the way a board would.

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
