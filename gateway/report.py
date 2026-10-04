"""A decoded health report as the JSON the gateway publishes. Pure, so it is
tested without Reticulum or a broker; telemetry_gateway.py does the I/O.

One message per report, on mesh/telemetry/<sender>, retained -- a subscriber
that starts late still sees every board's latest state. Field names follow
the codec; byte counts stay bytes; interface sets are spelled out, because a
dashboard filtering on "lora" should not need to know that LoRa is bit 0.
"""

import telemetry_codec as tc

TOPIC_PREFIX = "mesh/telemetry/"

INTERFACES = (("lora", tc.IF_LORA), ("ble", tc.IF_BLE), ("wifi", tc.IF_WIFI),
              ("espnow", tc.IF_ESPNOW), ("halow", tc.IF_HALOW))


def interfaces(mask):
    return [name for name, bit in INTERFACES if mask & bit]


def sender_hex(sender_id):
    return "%08x" % sender_id


def topic(sender_id):
    return TOPIC_PREFIX + sender_hex(sender_id)


def to_message(t, received_at, hops=None, via=None, gateway=None, name=None):
    """The JSON body for one decoded report. None values stay null.

    `name` is the board's announced name (board_names.py), or None until the
    gateway has heard one. Optional: version 1 consumers ignore it.
    """
    return {
        "v": 1,
        "sender": sender_hex(t.sender_id),
        "name": name,
        "received_at": round(received_at, 3),
        "hops": hops,
        "via": via,
        "gateway": gateway,
        "uptime_s": t.uptime_s,
        "reset": tc.RESET_NAMES.get(t.reset, "unknown"),
        "boots": t.boots,
        "crashes": t.crashes,
        "panics": t.panics,
        "heap_bytes": t.heap_bytes,
        "largest_bytes": t.largest_bytes,
        "psram_bytes": t.psram_bytes if t.psram_known else None,
        "interfaces_present": interfaces(t.if_present),
        "interfaces_up": interfaces(t.if_up),
        "ble_peers": t.ble_peers,
        "espnow_peers": t.espnow_peers,
        "paths": t.paths,
        "nodes": t.nodes,
        "relaying": t.relaying,
        "relay_expected": t.relay_expected,
        "time_current": t.time_current,
        "battery_mv": t.battery_mv if t.battery_known else None,
        "battery_pct": t.battery_pct if t.battery_known else None,
    }


DETAIL_SUFFIX = "/detail"


def detail_topic(sender_id):
    return topic(sender_id) + DETAIL_SUFFIX


def detail_to_message(d, received_at, hops=None, via=None, gateway=None, name=None, name_for=None):
    """The JSON body for one decoded detail report (telemetry_detail_codec.py).

    Counters stay as the board counts them -- bytes since boot -- so a consumer
    takes rates and reads a restart as a counter reset. Unknowns are null, not
    the wire's sentinels. `name_for(sender_hex)` names neighbours the gateway
    has heard announce; a neighbour it has not heard has a null name.
    """
    import telemetry_detail_codec as dc

    def known_dbm(v):
        return None if v == dc.RSSI_UNKNOWN else v

    radio = None
    if d["radio_known"]:
        radio = {"rssi_dbm": known_dbm(d["rssi"]), "snr_db": d["snr_q"] / 4.0,
                 "noise_dbm": known_dbm(d["noise"]),
                 "utilisation_pct": d["utilisation_pct"], "airtime_pct": d["airtime_pct"]}
    propagation = None
    if d["propagation_known"]:
        propagation = {"messages": d["store_messages"], "bytes": d["store_bytes"],
                       "peers": d["pn_peers"], "sync_ok": d["sync_ok"], "sync_failed": d["sync_fail"],
                       "last_sync_s": d["last_sync_s"]}
    neighbours = []
    for n in d["neighbours"]:
        node = sender_hex(n["id"])
        neighbours.append({"node": node, "name": name_for(node) if name_for else None,
                           "interface": dc.IF_NAMES.get(n["kind"], "other"),
                           "rssi_dbm": known_dbm(n["rssi"]), "heard_s": n["heard_s"]})
    return {
        "v": 1,
        "sender": sender_hex(d["sender_id"]),
        "name": name,
        "received_at": round(received_at, 3),
        "hops": hops,
        "via": via,
        "gateway": gateway,
        "uptime_s": d["uptime_s"],
        "firmware": {"hash": d["fw_hash"],
                     "version": "%d.%02d" % (d["fw_version"] >> 8, d["fw_version"] & 0xFF),   # as rnodeconf prints it
                     "env": d["env"] or None},
        "interfaces": [{"interface": dc.IF_NAMES.get(f["kind"], "other"), "up": f["up"],
                        "rx_bytes": f["rx_bytes"], "tx_bytes": f["tx_bytes"]} for f in d["interfaces"]],
        "radio": radio,
        "propagation": propagation,
        "neighbours": neighbours,
        "neighbours_truncated": d["neighbours_truncated"],
    }
