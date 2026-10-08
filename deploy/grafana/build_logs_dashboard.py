#!/usr/bin/env python3
"""Build deploy/grafana/dashboards/mesh-logs.json: the mesh's logs (T6).

Generated, like mesh-boards.json: run this after changing it and commit both.
Filter by job (gateway, backend, bridge, serial) and board, search the text.
"""

import json
import os

DS = {"type": "loki", "uid": "loki"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboards", "mesh-logs.json")
SELECTOR = '{job=~"$job", board=~"$board"} |~ "$search"'
# Lines worth a look at a glance: errors, failures and the firmware's panic words.
TROUBLE = '(?i)error|fail|refused|timed? ?out|panic|guru|abort|restart|lost'


def target(expr, ref, legend=None):
    t = {"datasource": DS, "refId": ref, "expr": expr, "queryType": "range"}
    if legend:
        t["legendFormat"] = legend
    return t


def variable(name, label, all_value):
    return {"type": "query", "name": name, "label": label, "datasource": DS,
            "query": {"label": name, "stream": "", "type": 1}, "refresh": 2, "sort": 1,
            "includeAll": True, "multi": True, "allValue": all_value,
            "current": {"text": ["All"], "value": ["$__all"]}}


def build():
    panels = [
        {"id": 1, "type": "timeseries", "title": "Log lines, by job", "datasource": DS,
         "gridPos": {"x": 0, "y": 0, "w": 12, "h": 7},
         "targets": [target('sum by (job) (count_over_time(%s [$__auto]))' % SELECTOR, "A", "{{job}}")],
         "fieldConfig": {"defaults": {"custom": {"drawStyle": "bars", "fillOpacity": 60, "stacking": {"mode": "normal"}}},
                         "overrides": []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom"}}},
        {"id": 2, "type": "timeseries", "title": "Errors, failures, panics -- by job and board", "datasource": DS,
         "gridPos": {"x": 12, "y": 0, "w": 12, "h": 7},
         "targets": [target('sum by (job, board) (count_over_time({job=~"$job", board=~"$board"} |~ "%s" [$__auto]))'
                            % TROUBLE, "A", "{{job}} {{board}}")],
         "fieldConfig": {"defaults": {"color": {"mode": "palette-classic"},
                                      "custom": {"drawStyle": "bars", "fillOpacity": 60, "stacking": {"mode": "normal"}}},
                         "overrides": []},
         "options": {"legend": {"displayMode": "list", "placement": "bottom"}}},
        {"id": 3, "type": "logs", "title": "Lines", "datasource": DS,
         "gridPos": {"x": 0, "y": 7, "w": 24, "h": 20},
         "targets": [target(SELECTOR, "A")],
         "options": {"showTime": True, "showLabels": True, "wrapLogMessage": True, "sortOrder": "Descending",
                     "enableLogDetails": True, "dedupStrategy": "none"}},
    ]
    return {
        "uid": "mesh-logs", "title": "Mesh logs", "tags": ["mesh", "logs"],
        "timezone": "browser", "refresh": "30s", "schemaVersion": 39, "version": 1,
        "time": {"from": "now-6h", "to": "now"},
        "links": [{"title": "Mesh boards", "type": "link", "url": "/d/mesh-boards", "icon": "dashboard"}],
        "templating": {"list": [
            variable("job", "Job", ".+"),
            variable("board", "Board", ".*"),
            {"type": "textbox", "name": "search", "label": "Search (regex)", "query": "",
             "current": {"text": "", "value": ""}},
        ]},
        "panels": panels,
    }


if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(build(), f, indent=2)
        f.write("\n")
    print("wrote", OUT)
