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
    rename = {"name": "Board", "sender": "Sender id"}
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
    exclude = {"Time": True, "Value #A": True}
    for ref in refs:
        exclude["Time %s" % ref] = True
    order = {"Board": 0, "Sender id": 1}
    for i, (title, _, _, _) in enumerate(COLUMNS):
        order[title] = 2 + i
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
    return {
        "uid": "mesh-boards", "title": "Mesh boards", "tags": ["mesh", "telemetry"],
        "timezone": "browser", "refresh": "30s", "schemaVersion": 39, "version": 2,
        "time": {"from": "now-24h", "to": "now"}, "panels": panels,
    }


if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
    print("wrote", OUT)
