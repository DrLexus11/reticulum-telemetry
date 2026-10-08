"""Reachability probes: does traffic get through to each board, and how fast.

The rest of the telemetry is the board's own word that it is alive; a probe
measures the mesh. Every PROBE_INTERVAL_S the gateway sends each board a small
packet to its rnstransport.probe destination -- every board answers probes
with a proof (RNode_Firmware.ino enables it) -- and publishes what happened on
mesh/telemetry/<sender>/probe, retained:

    {"v": 1, "sender": "0a0b0c0d", "name": ..., "at": ..., "delivered": true,
     "rtt_s": 2.41, "hops": 2, "via": "udp", "gateway": ...,
     "sent_total": 37, "delivered_total": 35}

"via" is the kind of interface the path to the board leaves the gateway's host
by (asked of the shared instance: from a client every packet leaves by the
local link). The counts are cumulative since the gateway started.

Targets are the boards whose NomadNet node the gateway has heard announce: the
same identity holds the probe destination. Each is kept on disk with the
node's destination hash and the identity's public key (a probe is encrypted to
it), so a restarted gateway probes at once instead of after the next hourly
announce, and without depending on Reticulum's identity cache, which a client
of a shared instance loads only once. Pure apart from that file;
telemetry_gateway.py sends.
"""

import json
import os
import tempfile

PROBE_INTERVAL_S = 300
PROBE_TIMEOUT_S = 60     # a two-hop LoRa round trip takes seconds; a minute is generous

KINDS = (("rnode", "lora"), ("lora", "lora"), ("udp", "udp"), ("tcp", "tcp"), ("backbone", "tcp"),
         ("auto", "auto"), ("ble", "ble"), ("i2p", "i2p"), ("serial", "serial"), ("kiss", "serial"),
         ("halow", "halow"), ("local", "local"))


def kind_of(interface_name):
    """'UDPInterface[RAD-01 Rev2/0.0.0.0:4244]' -> 'udp'; None stays None."""
    if not interface_name:
        return None
    low = interface_name.lower()
    for needle, kind in KINDS:
        if needle in low:
            return kind
    return "other"


class ProbeBook:
    def __init__(self, path=None):
        self.path = os.path.expanduser(path) if path else None
        self.targets = {}   # sender hex -> {"node": destination hash hex, "key": public key hex}
        self.sent = {}
        self.delivered = {}
        if self.path and os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if isinstance(v, str):            # an older file: the node hash only
                            v = {"node": v, "key": None}
                        if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("node"), str):
                            self.targets[k] = {"node": v["node"], "key": v.get("key")}
            except (OSError, ValueError):
                self.targets = {}

    def heard(self, sender_hex, node_hash_hex, public_key_hex=None):
        """A board's NomadNet node announced. True if the target is new or changed."""
        entry = {"node": node_hash_hex, "key": public_key_hex}
        if self.targets.get(sender_hex) == entry:
            return False
        self.targets[sender_hex] = entry
        self._save()
        return True

    def result(self, sender_hex, at, delivered, rtt_s=None, hops=None, via=None, name=None, gateway=None):
        """Count one probe and return its message."""
        self.sent[sender_hex] = self.sent.get(sender_hex, 0) + 1
        if delivered:
            self.delivered[sender_hex] = self.delivered.get(sender_hex, 0) + 1
        return {
            "v": 1,
            "sender": sender_hex,
            "name": name,
            "at": round(at, 3),
            "delivered": bool(delivered),
            "rtt_s": round(rtt_s, 4) if delivered and rtt_s is not None else None,
            "hops": hops,
            "via": via,
            "gateway": gateway,
            "sent_total": self.sent[sender_hex],
            "delivered_total": self.delivered.get(sender_hex, 0),
        }

    def _save(self):
        if not self.path:
            return
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".probe_targets.")
        with os.fdopen(fd, "w") as f:
            json.dump(self.targets, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)
