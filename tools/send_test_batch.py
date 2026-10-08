#!/usr/bin/env python3
"""Send a gateway a telemetry batch (T2) the way a board would, to check the
LXMF leg end to end: a throwaway identity signs one LXMF message whose content
is a batch of two health reports, an hour and half an hour old, and deposits it
at a propagation node. The gateway's inbox should collect it on its next pass
and publish two backfill messages.

    python tools/send_test_batch.py --gateway <uplink destination hash> \\
        --propagation-node <hash>

The reports carry the throwaway identity's sender id, so no real board gets
history it did not report. Uses the host's shared Reticulum instance.
"""

import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import telemetry_batch_codec as bc  # noqa: E402
import telemetry_codec as tc  # noqa: E402


def wait_for(predicate, seconds, step=0.5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--gateway", required=True, help="the gateway's uplink destination hash")
    parser.add_argument("--propagation-node", required=True, help="where to deposit the batch")
    parser.add_argument("--rnsconfig", default=None)
    args = parser.parse_args()

    import LXMF
    import RNS

    RNS.Reticulum(args.rnsconfig)
    store = tempfile.mkdtemp(prefix="send-test-batch-")
    identity = RNS.Identity()
    router = LXMF.LXMRouter(identity=identity, storagepath=store)
    source = router.register_delivery_identity(identity, display_name="T2 test sender")
    # The gateway validates the signature, so it must know this identity.
    router.announce(source.hash)

    uplink = bytes.fromhex(args.gateway)
    if not RNS.Transport.has_path(uplink):
        RNS.Transport.request_path(uplink)
    if not wait_for(lambda: RNS.Identity.recall(uplink) is not None, 60):
        sys.exit("the gateway's identity is not known; is it announcing?")
    gateway_identity = RNS.Identity.recall(uplink)
    destination = RNS.Destination(gateway_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                  "lxmf", "delivery")

    sender = int.from_bytes(identity.hash[:4], "big")
    now = int(time.time())
    entries = [{"time_kind": bc.TIME_ABSOLUTE, "time": now - age,
                "report": tc.encode(tc.Telemetry(sender_id=sender, uptime_s=5000 - age, heap_bytes=80 * 1024,
                                                 largest_bytes=50 * 1024, paths=4, nodes=2))}
               for age in (3600, 1800)]
    batch = bc.encode(sender, now, entries)

    router.set_outbound_propagation_node(bytes.fromhex(args.propagation_node))
    message = LXMF.LXMessage(destination, source, batch, title="telemetry/batch",
                             desired_method=LXMF.LXMessage.PROPAGATED)
    router.handle_outbound(message)
    print("sender %08x: batch of %d bytes, two reports; depositing..." % (sender, len(batch)), flush=True)
    if not wait_for(lambda: message.state in (LXMF.LXMessage.SENT, LXMF.LXMessage.DELIVERED, LXMF.LXMessage.FAILED), 180):
        sys.exit("not deposited within 180 s (state 0x%02x)" % message.state)
    if message.state == LXMF.LXMessage.FAILED:
        sys.exit("deposit failed")
    print("deposited at the propagation node; the gateway should publish "
          "mesh/telemetry/%08x/backfill on its next pass" % sender)


if __name__ == "__main__":
    main()
