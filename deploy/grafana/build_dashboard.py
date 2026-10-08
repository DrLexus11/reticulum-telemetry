#!/usr/bin/env python3
"""Build deploy/grafana/dashboards/mesh-boards.json.

The dashboard is generated, not hand-edited: run this after changing it and
commit both. Every per-board query is reduced to one series per sender
(`max by (sender)`) and joined on `sender`, and names come from
`mesh_board_info{sender, name}` -- a board shows once, by name, with every
column filled.
"""

import json
import os

DS = {"type": "prometheus", "uid": "prometheus"}
NAMED = " * on(sender) group_left(name) max by (sender, name) (mesh_board_info)"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards", "mesh-boards.json")

_ids = iter(range(1, 1000))


def target(expr, ref, legend=None, instant=False, table=False):
    t = {"datasource": DS, "refId": ref, "expr": expr}
    if legend:
        t["legendFormat"] = legend
    if instant:
        t["instant"] = True
        t["range"] = False
    if table:
        t["format"] = "table"
    return t


def thresholds(*steps):
    """steps: (color, value) pairs, the first value None."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def stat(title, expr, x, w, unit="none", steps=None, description=""):
    return {
        "id": next(_ids), "type": "stat", "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": 6, "w": w, "h": 4},
        "targets": [target(expr, "A", instant=True)],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": steps or thresholds(("green", None)),
                                     "color": {"mode": "thresholds"}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"], "values": False},
                    "colorMode": "background", "graphMode": "none", "textMode": "value"},
    }


def timeseries(title, expr, y, x, w, unit, legend="{{name}}", description="", steps=None):
    return {
        "id": next(_ids), "type": "timeseries", "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": 8},
        "targets": [target(expr, "A", legend=legend)],
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"lineWidth": 2, "spanNulls": False},
                                     **({"thresholds": steps, "custom": {"lineWidth": 2, "spanNulls": False,
                                                                         "thresholdsStyle": {"mode": "dashed"}}}
                                        if steps else {})},
                        "overrides": []},
        "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["lastNotNull"]},
                    "tooltip": {"mode": "multi"}},
    }


# The boards table: (column title, expr per board, unit, extra field config).
COLUMNS = [
    ("Last report", "time() - max by (sender) (mesh_board_report_received_timestamp_seconds)", "s",
     {"thresholds": thresholds(("green", None), ("orange", 900), ("red", 1800)),
      "custom": {"cellOptions": {"type": "color-text"}}}),
    ("Uptime", "max by (sender) (mesh_board_uptime_seconds)", "dtdurations", {}),
    ("Heap free", "max by (sender) (mesh_board_heap_free_bytes)", "bytes", {}),
    ("Largest block", "max by (sender) (mesh_board_heap_largest_block_bytes)", "bytes",
     {"thresholds": thresholds(("red", None), ("orange", 20480), ("green", 40960)),
      "custom": {"cellOptions": {"type": "color-text"}}}),
    ("Restarts", "max by (sender) (mesh_board_crashes + mesh_board_panics)", "none",
     {"description": "Lifetime crashes + panics, as the board counts them"}),
    ("Hops", "max by (sender) (mesh_board_hops_to_gateway)", "none", {}),
    ("Reachable", "max by (sender) (mesh_board_probe_success)", "none",
     {"mappings": [{"type": "value", "options": {"1": {"text": "yes", "color": "green"},
                                                 "0": {"text": "no", "color": "red"}}}],
      "custom": {"cellOptions": {"type": "color-text"}},
      "description": "The gateway's latest probe: did a packet to the board get a proof back"}),
    ("RTT", "max by (sender) (mesh_board_probe_rtt_seconds)", "s", {}),
    ("Carriers up", "sum by (sender) (mesh_board_interface_up)", "none", {}),
    ("Paths", "max by (sender) (mesh_board_paths)", "none", {}),
    ("Nodes", "max by (sender) (mesh_board_nodes)", "none", {}),
    ("Clock", "max by (sender) (mesh_board_time_current)", "none",
     {"mappings": [{"type": "value", "options": {"1": {"text": "synced", "color": "green"},
                                                 "0": {"text": "not synced", "color": "orange"}}}],
      "custom": {"cellOptions": {"type": "color-text"}}}),
    ("Relaying", "max by (sender) (mesh_board_relaying)", "none",
     {"mappings": [{"type": "value", "options": {"1": {"text": "yes"}, "0": {"text": "no"}}}]}),
    ("PSRAM free", "max by (sender) (mesh_board_psram_free_bytes)", "bytes", {}),
    ("Battery", "max by (sender) (mesh_board_battery_percent)", "percent", {}),
]


def boards_table(y):
    refs = [chr(ord("B") + i) for i in range(len(COLUMNS))]
    targets = [target("max by (sender, name) (mesh_board_info)", "A", instant=True, table=True)]
    targets += [target(expr, ref, instant=True, table=True) for (_, expr, _, _), ref in zip(COLUMNS, refs)]
    # The running image, from the detail report: its labels become columns.
    targets.append(target("max by (sender, hash, version, env) (mesh_board_firmware_info)", "FW",
                          instant=True, table=True))
    rename = {"name": "Board", "sender": "Sender id", "hash": "Firmware", "version": "Version",
              "env": "Build"}
    overrides = []
    for (title, _, unit, extra), ref in zip(COLUMNS, refs):
        rename["Value #%s" % ref] = title
        props = [{"id": "unit", "value": unit}]
        if "thresholds" in extra:
            props += [{"id": "thresholds", "value": extra["thresholds"]},
                      {"id": "color", "value": {"mode": "thresholds"}}]
        if "mappings" in extra:
            props.append({"id": "mappings", "value": extra["mappings"]})
        if "description" in extra:
            props.append({"id": "description", "value": extra["description"]})
        if "custom" in extra:
            props.append({"id": "custom.cellOptions", "value": extra["custom"]["cellOptions"]})
        overrides.append({"matcher": {"id": "byName", "options": title}, "properties": props})
    exclude = {"Time": True, "Value #A": True, "Value #FW": True, "Time FW": True}
    for ref in refs:
        exclude["Time %s" % ref] = True
    order = {"Board": 0, "Sender id": 1}
    for i, (title, _, _, _) in enumerate(COLUMNS):
        order[title] = 2 + i
    order.update({"Firmware": 2 + len(COLUMNS), "Version": 3 + len(COLUMNS), "Build": 4 + len(COLUMNS)})
    return {
        "id": next(_ids), "type": "table", "title": "Boards -- one row each, latest report",
        "datasource": DS, "gridPos": {"x": 0, "y": y, "w": 24, "h": 8},
        "targets": targets,
        "transformations": [
            {"id": "joinByField", "options": {"byField": "sender", "mode": "outer"}},
            {"id": "organize", "options": {"excludeByName": exclude, "renameByName": rename,
                                           "indexByName": order}},
            {"id": "sortBy", "options": {"sort": [{"field": "Board"}]}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }


def named_table(title, columns, x, y, w, h, description=""):
    """A table of boards, one row each: every column's query is reduced to one
    series per board and named, so the rows merge on (sender, name)."""
    refs = [chr(ord("B") + i) for i in range(len(columns))]
    targets = [target("(%s)%s" % (expr, NAMED), ref, instant=True, table=True)
               for (_, expr, _, _), ref in zip(columns, refs)]
    rename = {"name": "Board"}
    exclude = {"Time": True, "sender": True}
    overrides = []
    for (col, _, unit, extra), ref in zip(columns, refs):
        rename["Value #%s" % ref] = col
        exclude["Time %s" % ref] = True
        props = [{"id": "unit", "value": unit}]
        if "thresholds" in extra:
            props += [{"id": "thresholds", "value": extra["thresholds"]},
                      {"id": "color", "value": {"mode": "thresholds"}},
                      {"id": "custom.cellOptions", "value": {"type": "color-text"}}]
        overrides.append({"matcher": {"id": "byName", "options": col}, "properties": props})
    return {
        "id": next(_ids), "type": "table", "title": title, "description": description,
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": targets,
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": exclude, "renameByName": rename}},
            {"id": "sortBy", "options": {"sort": [{"field": "Board"}]}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": overrides},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }


# A link heard within 15 minutes is live; within an hour, fading; older, stale.
FRESHNESS = thresholds(("green", None), ("orange", 900), ("red", 3600))
# Names are not unique, so rows and columns carry the id beside the name: two
# boards both called "RAD" stay two rows.
LINK_AGE = ('label_join(label_join(time() - max by (sender, neighbour, sender_name, neighbour_name)'
            ' (mesh_link_heard_timestamp_seconds), "row", " \u00b7 ", "sender_name", "sender"),'
            ' "column", " \u00b7 ", "neighbour_name", "neighbour")')


def who_hears_whom(x, y, w, h):
    return {
        "id": next(_ids), "type": "table", "title": "Who hears whom -- time since heard directly",
        "description": "Rows hear columns. From each board's detail report (every 30 min): the one-hop "
                       "neighbours it heard announce, over any carrier. Empty: not heard directly.",
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(LINK_AGE, "A", instant=True, table=True)],
        "transformations": [
            {"id": "groupingToMatrix", "options": {"columnField": "column", "rowField": "row",
                                                   "valueField": "Value", "emptyValue": "null"}},
            {"id": "organize", "options": {"renameByName": {"row\\column": "hears \u2192"}}},
        ],
        "fieldConfig": {"defaults": {"unit": "s", "decimals": 0, "thresholds": FRESHNESS,
                                     "color": {"mode": "thresholds"},
                                     "custom": {"align": "center", "cellOptions": {"type": "color-background"}}},
                        "overrides": [{"matcher": {"id": "byName", "options": "hears \u2192"},
                                       "properties": [{"id": "custom.cellOptions", "value": {"type": "auto"}},
                                                      {"id": "custom.align", "value": "left"}]}]},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }


def topology(x, y, w, h):
    """The mesh map: the drlexus11-meshtopology-panel plugin (deploy/grafana/plugins),
    drawn like Crosstalk's network view. The refIds are the plugin's contract."""
    return {
        "id": next(_ids), "type": "drlexus11-meshtopology-panel", "title": "Topology -- who hears whom",
        "description": "Boards (server icons) and the nodes they hear directly (person icons). A ring's "
                       "colour is how recently the board reported, or the node was heard; a link's colour "
                       "is its carrier, an arrow means only one side hears the other. Hover for details.",
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [
            target("max by (node, name) (mesh_node_info)", "A", instant=True, table=True),
            target("time() - max by (sender, neighbour, interface) (mesh_link_heard_timestamp_seconds)", "B",
                   instant=True, table=True),
            target("max by (sender, neighbour, interface) (mesh_link_rssi_dbm)", "C", instant=True, table=True),
            target("time() - max by (sender) (mesh_board_report_received_timestamp_seconds)", "D",
                   instant=True, table=True),
            target("max by (sender, name) (mesh_board_info)", "E", instant=True, table=True),
        ],
        "options": {"edgeLabels": True},
    }


def links_table(x, y, w, h):
    labels = "sender, neighbour, sender_name, neighbour_name, interface"
    return {
        "id": next(_ids), "type": "table", "title": "Neighbour links",
        "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [
            target("time() - max by (%s) (mesh_link_heard_timestamp_seconds)" % labels, "A",
                   instant=True, table=True),
            target("max by (%s) (mesh_link_rssi_dbm)" % labels, "B", instant=True, table=True),
        ],
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True},
                "renameByName": {"sender_name": "Board", "sender": "Board id", "neighbour_name": "Hears",
                                 "neighbour": "Neighbour id", "interface": "Over",
                                 "Value #A": "Heard ago", "Value #B": "RSSI"},
                "indexByName": {"Board": 0, "Board id": 1, "Hears": 2, "Neighbour id": 3, "Over": 4,
                                "Heard ago": 5, "RSSI": 6}}},
            {"id": "sortBy", "options": {"sort": [{"field": "Board"}]}},
        ],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": [
            {"matcher": {"id": "byName", "options": "Heard ago"},
             "properties": [{"id": "unit", "value": "s"}, {"id": "thresholds", "value": FRESHNESS},
                            {"id": "color", "value": {"mode": "thresholds"}},
                            {"id": "custom.cellOptions", "value": {"type": "color-text"}}]},
            {"matcher": {"id": "byName", "options": "RSSI"}, "properties": [{"id": "unit", "value": "dBm"}]}]},
        "options": {"showHeader": True, "cellHeight": "sm"},
    }


PN_COLUMNS = [
    ("Messages", "max by (sender) (mesh_board_pn_store_messages)", "none", {}),
    ("Store", "max by (sender) (mesh_board_pn_store_bytes)", "bytes", {}),
    ("Peers", "max by (sender) (mesh_board_pn_peers)", "none", {}),
    ("Syncs OK", "max by (sender) (mesh_board_pn_syncs_ok_total)", "none", {}),
    ("Syncs failed", "max by (sender) (mesh_board_pn_syncs_failed_total)", "none",
     {"thresholds": thresholds(("green", None), ("orange", 1))}),
    ("Last sync", "max by (sender) (mesh_board_pn_last_sync_age_seconds)"
                  " + (time() - max by (sender) (mesh_board_detail_received_timestamp_seconds))", "s",
     {"thresholds": thresholds(("green", None), ("orange", 3600), ("red", 21600))}),
]

RADIO_COLUMNS = [
    ("Carriers online", "sum by (sender) (mesh_board_interface_online)", "none", {}),
    ("Channel use", "max by (sender) (mesh_board_radio_channel_utilisation_percent)", "percent",
     {"thresholds": thresholds(("green", None), ("orange", 30), ("red", 60))}),
    ("Own airtime", "max by (sender) (mesh_board_radio_airtime_percent)", "percent", {}),
    ("Noise floor", "max by (sender) (mesh_board_radio_noise_floor_dbm)", "dBm", {}),
    ("Last RSSI", "max by (sender) (mesh_board_radio_rssi_dbm)", "dBm", {}),
    ("Neighbours", "count by (sender) (mesh_link_heard_timestamp_seconds)", "none", {}),
    ("Temp", "max by (sender) (mesh_board_temperature_celsius)", "celsius",
     {"thresholds": thresholds(("green", None), ("orange", 60), ("red", 75))}),
    ("CRC errors /h", "max by (sender) (increase(mesh_board_lora_crc_errors_total[1h]))", "none",
     {"thresholds": thresholds(("green", None), ("orange", 5), ("red", 30))}),
    ("Clock age", "max by (sender) (mesh_board_clock_age_seconds)", "s",
     {"thresholds": thresholds(("green", None), ("orange", 6 * 3600), ("red", 24 * 3600))}),
    ("IFAC rejects /h", "max by (sender) (increase(mesh_board_ifac_rejected_total[1h]))", "none",
     {"thresholds": thresholds(("green", None), ("red", 1))}),
]


def build():
    panels = [{
        "id": next(_ids), "type": "alertlist", "title": "Fleet alerts -- firing and pending",
        "gridPos": {"x": 0, "y": 0, "w": 24, "h": 6},
        "options": {"viewMode": "list", "groupMode": "default", "maxItems": 20, "sortOrder": 1,
                    "dashboardAlerts": False, "alertName": "", "alertInstanceLabelFilter": "",
                    "stateFilter": {"firing": True, "pending": True, "noData": True,
                                    "normal": False, "error": True}},
    }]
    panels += [
        stat("Reporting now", "count((time() - mesh_board_report_received_timestamp_seconds) < 900) or vector(0)",
             0, 6, description="Boards heard from in the last 15 minutes"),
        stat("Silent", "count((time() - mesh_board_report_received_timestamp_seconds) >= 900) or vector(0)",
             6, 6, steps=thresholds(("green", None), ("red", 1)),
             description="Boards not heard from for 15 minutes or more"),
        stat("Restarts, last 24 h",
             "sum(increase(mesh_board_crashes[24h]) + increase(mesh_board_panics[24h])) or vector(0)",
             12, 6, steps=thresholds(("green", None), ("orange", 1), ("red", 3))),
        stat("Low on memory", "count(mesh_board_heap_largest_block_bytes < 20480) or vector(0)",
             18, 6, steps=thresholds(("green", None), ("red", 1)),
             description="Boards whose largest free block is under 20 KB"),
    ]
    panels.append(boards_table(10))
    panels.append({
        "id": next(_ids), "type": "state-timeline", "title": "Carriers up",
        "datasource": DS, "gridPos": {"x": 0, "y": 18, "w": 24, "h": 8},
        "targets": [target("max by (sender, interface) (mesh_board_interface_present == 1)"
                           " * on(sender, interface) group_left max by (sender, interface) (mesh_board_interface_up)"
                           + NAMED, "A", legend="{{name}} {{interface}}")],
        "fieldConfig": {"defaults": {"mappings": [{"type": "value", "options": {
            "1": {"text": "up", "color": "green"}, "0": {"text": "down", "color": "red"}}}],
            "color": {"mode": "thresholds"}, "thresholds": thresholds(("red", None), ("green", 1))},
            "overrides": []},
        "options": {"showValue": "never", "mergeValues": True, "alignValue": "left", "rowHeight": 0.8},
    })
    y = 26
    panels += [
        timeseries("Heap free", "max by (sender) (mesh_board_heap_free_bytes)" + NAMED, y, 0, 12, "bytes"),
        timeseries("Largest free block", "max by (sender) (mesh_board_heap_largest_block_bytes)" + NAMED,
                   y, 12, 12, "bytes", steps=thresholds(("red", None), ("orange", 20480), ("green", 40960))),
        timeseries("Time since last report",
                   "(time() - max by (sender) (mesh_board_report_received_timestamp_seconds))" + NAMED,
                   y + 8, 0, 12, "s", steps=thresholds(("green", None), ("orange", 900), ("red", 1800))),
        timeseries("Restarts (crashes + panics, lifetime)",
                   "max by (sender) (mesh_board_crashes + mesh_board_panics)" + NAMED, y + 8, 12, 12, "none"),
        timeseries("Paths and nodes", "max by (sender) (mesh_board_paths)" + NAMED, y + 16, 0, 12, "none",
                   legend="{{name}} paths"),
        timeseries("Uptime", "max by (sender) (mesh_board_uptime_seconds)" + NAMED, y + 16, 12, 12,
                   "dtdurations", description="Drops to zero on every restart"),
    ]
    panels[-2]["targets"].append(target("max by (sender) (mesh_board_nodes)" + NAMED, "B", legend="{{name}} nodes"))

    # The detail report (every 30 min): who hears whom, the stores, the carriers.
    y = 50
    panels += [
        topology(0, y, 24, 16),
        who_hears_whom(0, y + 16, 12, 12),
        links_table(12, y + 16, 12, 12),
        named_table("Propagation nodes -- LXMF stores", PN_COLUMNS, 0, y + 28, 12, 7,
                    description="Boards running a propagation node. Sync counts are since the board booted."),
        named_table("Radio, carriers and system", RADIO_COLUMNS, 12, y + 28, 12, 7),
        timeseries("Received, by carrier",
                   "sum by (sender, interface) (rate(mesh_board_interface_rx_bytes_total[1h]))"
                   " * on(sender) group_left(name) max by (sender, name) (mesh_board_info)",
                   y + 35, 0, 12, "Bps", legend="{{name}} {{interface}}",
                   description="Hourly average from cumulative byte counters; a restart reads as a reset"),
        timeseries("Sent, by carrier",
                   "sum by (sender, interface) (rate(mesh_board_interface_tx_bytes_total[1h]))"
                   " * on(sender) group_left(name) max by (sender, name) (mesh_board_info)",
                   y + 35, 12, 12, "Bps", legend="{{name}} {{interface}}"),
        timeseries("Probe round-trip time", "max by (sender) (mesh_board_probe_rtt_seconds)" + NAMED,
                   y + 51, 0, 12, "s",
                   description="The gateway's probe of each board, every 5 minutes: measured, not inferred"),
        timeseries("Probe loss, last hour",
                   "(1 - (max by (sender) (increase(mesh_board_probes_delivered_total[1h]))"
                   " / max by (sender) (increase(mesh_board_probes_sent_total[1h]))))" + NAMED,
                   y + 51, 12, 12, "percentunit",
                   steps=thresholds(("green", None), ("orange", 0.2), ("red", 0.5))),
        timeseries("LoRa channel use", "max by (sender) (mesh_board_radio_channel_utilisation_percent)" + NAMED,
                   y + 43, 0, 12, "percent"),
        timeseries("LoRa noise floor", "max by (sender) (mesh_board_radio_noise_floor_dbm)" + NAMED,
                   y + 43, 12, 12, "dBm"),
    ]
    return {
        "uid": "mesh-boards", "title": "Mesh boards", "tags": ["mesh", "telemetry"],
        "timezone": "browser", "refresh": "30s", "schemaVersion": 39, "version": 4,
        "time": {"from": "now-24h", "to": "now"}, "panels": panels,
    }


if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
    print("wrote", OUT)
