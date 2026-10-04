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


def lxmf_display_name(app_data):
    """The display name from an lxmf.delivery announce, or None.

    LXMF 0.5 and later announce a msgpack list whose first element is the name
    (bytes, or nil); older clients announce the bare name. Only that first
    element is read, so no msgpack library is needed.
    """
    if not app_data:
        return None
    data = bytes(app_data)
    if 0x91 <= data[0] <= 0x9f:                     # fixarray
        at = 1
        if at >= len(data):
            return None
        tag = data[at]
        if tag == 0xc0:                             # nil: no name set
            return None
        if tag in (0xc4, 0xd9):                     # bin8, str8
            if at + 2 > len(data):
                return None
            length, at = data[at + 1], at + 2
        elif 0xa0 <= tag <= 0xbf:                   # fixstr
            length, at = tag & 0x1f, at + 1
        else:
            return None
        if at + length > len(data):
            return None
        return clean(data[at:at + length])
    return clean(data)


class BoardNames:
    def __init__(self, path=None, parse=clean):
        self.parse = parse
        self.path = os.path.expanduser(path) if path else None
        self._names = {}
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    loaded = json.load(f)
                if not isinstance(loaded, dict):
                    raise ValueError("board names cache is not an object")
                self._names = {k: v for k, v in loaded.items()
                               if isinstance(k, str) and isinstance(v, str)}
            except (OSError, ValueError):
                self._names = {}

    def heard(self, identity_hash, app_data):
        """Record a board's announced name. True if it was new or changed."""
        name = self.parse(app_data)
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
