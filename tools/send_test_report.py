"""Send one synthetic health report to a gateway, to check it end to end.

    python tools/send_test_report.py <gateway destination hash>

The report is the first case of tests/fixtures/telemetry_v1.json -- synthetic
values, never a real board's. Sent the way a board sends: one SINGLE packet to
rnstransport.telemetry.uplink. Then watch mesh/telemetry/# on the broker.
"""

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import telemetry_codec  # noqa: E402


def main():
    usage = "usage: send_test_report.py <32-hex-digit gateway destination hash>"
    if len(sys.argv) != 2 or len(sys.argv[1]) != 32:
        sys.exit(usage)
    try:
        target = bytes.fromhex(sys.argv[1])   # checked before Reticulum starts
    except ValueError:
        sys.exit(usage)
    import RNS
    RNS.Reticulum(os.environ.get("RNS_CONFIG"))
    case = json.loads((ROOT / "tests/fixtures/telemetry_v1.json").read_text())["encode"][0]
    payload = bytes.fromhex(case["hex"])
    if not RNS.Transport.has_path(target):
        RNS.Transport.request_path(target)
        deadline = time.time() + 15
        while not RNS.Transport.has_path(target) and time.time() < deadline:
            time.sleep(0.2)
    identity = RNS.Identity.recall(target)
    if identity is None:
        sys.exit("no path to %s, or its identity is unknown" % sys.argv[1])
    destination = RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                  "rnstransport", "telemetry", "uplink")
    RNS.Packet(destination, payload).send()
    print("sent %d bytes (%s, sender %s) to %s"
          % (len(payload), case["name"], payload[2:6].hex(), sys.argv[1]))
    time.sleep(1)


if __name__ == "__main__":
    main()
