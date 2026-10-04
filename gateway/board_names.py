"""Board names, learned from announces. Pure apart from its JSON file.

A report carries only a 4-byte sender id: the first four bytes of the board's
transport identity hash (TelemetryUplink / LoopServicesImpl.h). The same
identity announces the board's NomadNet node with its name as app data, so
the gateway can name every board without a byte more on the air. Kept on
disk, so a restarted gateway names boards at once instead of after their next
announce (a board announces its node about once an hour).
"""

import json
import os
import tempfile

NAME_MAX = 64


def sender_of(identity_hash):
    """The report's sender id for an identity hash: its first 4 bytes, as hex."""
    return bytes(identity_hash[:4]).hex()


def clean(app_data):
    """A printable name from announce app data, or None."""
    if not app_data:
        return None
    try:
        text = bytes(app_data).decode("utf-8")
    except UnicodeDecodeError:
        return None
    text = "".join(ch for ch in text if ch.isprintable()).strip()
    return text[:NAME_MAX] or None


class BoardNames:
    def __init__(self, path=None):
        self.path = os.path.expanduser(path) if path else None
        self._names = {}
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    loaded = json.load(f)
                self._names = {k: v for k, v in loaded.items()
                               if isinstance(k, str) and isinstance(v, str)}
            except (OSError, ValueError):
                self._names = {}

    def heard(self, identity_hash, app_data):
        """Record a board's announced name. True if it was new or changed."""
        name = clean(app_data)
        if name is None:
            return False
        sender = sender_of(identity_hash)
        if self._names.get(sender) == name:
            return False
        self._names[sender] = name
        self._save()
        return True

    def name_for(self, sender_hex):
        return self._names.get(sender_hex)

    def _save(self):
        if not self.path:
            return
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".board_names.")
        with os.fdopen(fd, "w") as f:
            json.dump(self._names, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
