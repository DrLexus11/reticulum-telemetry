"""Where each board is, for the dashboard's map.

No fielded board measures its position yet -- GNSS comes with the Rev 3
mezzanine -- so a board's position is *configured*: a host-local file, never
in this repository, as positions are exercise data:

    ~/.config/reticulum-telemetry/board_positions.json
    {"0a0b0c0d": {"lat": 41.0, "lon": 29.0}, ...}

The gateway publishes each as a retained message on
mesh/telemetry/<sender>/position, with "source": "configured", and again
whenever the file changes. A board that measures its own position will
publish the same shape with "source": "gnss"; the map need not change. A
configured position is never presented as a live fix: the source says which.
Pure apart from reading the file.
"""

import json
import os
import re
import tempfile

SENDER = re.compile(r"^[0-9a-f]{8}$")


def load(path):
    """{sender: {"lat": float, "lon": float}} from the file; entries that are
    not a sender id with a valid latitude and longitude are left out. A missing
    or unreadable file is an empty mapping."""
    path = os.path.expanduser(path)
    try:
        with open(path) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for sender, value in raw.items():
        if not isinstance(sender, str) or not SENDER.match(sender) or not isinstance(value, dict):
            continue
        lat, lon = value.get("lat"), value.get("lon")
        if isinstance(lat, bool) or isinstance(lon, bool):
            continue
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            continue
        out[sender] = {"lat": float(lat), "lon": float(lon)}
    return out


def message(sender, position, at, name=None, source="configured"):
    return {"v": 1, "sender": sender, "name": name, "lat": position["lat"], "lon": position["lon"],
            "source": source, "at": round(at, 3)}


def load_published(path):
    """The senders a previous run published, so removals can be cleared."""
    try:
        with open(os.path.expanduser(path)) as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    return [s for s in raw if isinstance(s, str) and SENDER.match(s)] if isinstance(raw, list) else []


def save_published(path, senders):
    path = os.path.expanduser(path)
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".positions_published.")
    with os.fdopen(fd, "w") as f:
        json.dump(sorted(senders), f)
    os.replace(tmp, path)
