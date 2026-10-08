"""The gateway's LXMF inbox: batches boards could not deliver live (T2).

A board with no gateway in reach sends its kept reports, about hourly, as one
LXMF message to the gateway's LXMF delivery destination -- the gateway's own
identity with "lxmf"/"delivery", which the board computes from the identity it
learned from the gateway's uplink announce, so nothing extra is announced. The
message waits in the board's own propagation node. This inbox collects from
every propagation node it hears, one at a time, and hands each batch on with
the sender id of the identity that signed it.

A batch whose signature is invalid is dropped: the signer is what stops one
node writing another's history. A batch whose signer is not yet known -- its
delivery announce lost in the very partition the batch covers -- is kept with
its signed bytes; the gateway asks for a path to the signer, which prompts its
announce, and checks again on every pass, for as long as Prometheus would still
accept the reports (48 h).
"""

import json
import os
import threading
import time

SYNC_INTERVAL_S = 600      # one pass over the propagation nodes heard
SYNC_SOON_S = 30           # after a node is newly heard: a vehicle back in range
PN_FORGET_S = 2 * 3600     # a propagation node unheard this long is not visited
TRANSFER_TIMEOUT_S = 180   # per node: a LoRa path, a link and a resource take time
UNVERIFIED_KEEP_S = 48 * 3600   # Prometheus's out-of-order window: older is useless
# Kept before anyone is authenticated, so bounded: anyone can make identities.
# Two days of hourly batches per signer; the whole queue a few MB at most.
UNVERIFIED_MAX_PER_SIGNER = 48
UNVERIFIED_MAX_FILES = 500
UNVERIFIED_MAX_BYTES = 4 * 1024 * 1024


class Inbox:
    def __init__(self, rns, lxmf, identity, storagepath, display_name, on_batch, log=print, always=(),
                 unverified_dir=None):
        self.rns = rns
        self.lxmf = lxmf
        self.identity = identity
        self.on_batch = on_batch
        self.log = log
        self.router = lxmf.LXMRouter(identity=identity, storagepath=storagepath)
        self.destination = self.router.register_delivery_identity(identity, display_name=display_name)
        self.router.register_delivery_callback(self._delivered)
        self._lock = threading.Lock()
        self._heard = {}   # propagation node hash -> when last heard
        self._new_node = threading.Event()
        self._new_node.set()   # the first pass soon after start, not an interval later
        # Nodes visited whether or not they were heard announcing -- the deck's
        # own lxmd, say, which announces rarely (--propagation-node).
        self._always = [bytes(h) for h in always]
        self.collected = 0
        self.dropped = 0
        self.unverified_dir = os.path.expanduser(unverified_dir or os.path.join(storagepath, "unverified"))
        os.makedirs(self.unverified_dir, exist_ok=True)
        inbox = self

        class _PropagationAnnounces:
            aspect_filter = "lxmf.propagation"

            def received_announce(self, destination_hash, announced_identity, app_data):
                now = time.time()
                key = bytes(destination_hash)
                with inbox._lock:
                    previous = inbox._heard.get(key)
                    inbox._heard[key] = now
                # Newly heard, or heard again after being forgotten: a node that
                # was out of range may be holding batches, so go soon.
                if previous is None or now - previous >= PN_FORGET_S:
                    inbox._new_node.set()

        rns.Transport.register_announce_handler(_PropagationAnnounces())

    def _delivered(self, message, collected_at=None):
        collected_at = time.time() if collected_at is None else collected_at
        content = message.content if isinstance(message.content, (bytes, bytearray)) else b""
        source = self.rns.hexrep(message.source_hash, delimit=False)
        if not content or content[0] != 0x31:
            self.log("[inbox] ignored a message from <%s>: not a telemetry batch" % source[:16])
            return
        if not message.signature_validated:
            if message.unverified_reason == self.lxmf.LXMessage.SOURCE_UNKNOWN and message.packed:
                self._keep_unverified(message, collected_at)
                return
            self.dropped += 1
            self.log("[inbox] dropped a batch from <%s>: signature invalid" % source[:16])
            return
        signer_identity = self.rns.Identity.recall(message.source_hash)
        if signer_identity is None:
            self.dropped += 1
            self.log("[inbox] dropped a batch: signer identity not known")
            return
        signer = int.from_bytes(signer_identity.hash[:4], "big")
        self.collected += 1
        self.on_batch(bytes(content), collected_at, signer)

    def _keep_unverified(self, message, collected_at):
        source = self.rns.hexrep(message.source_hash, delimit=False)
        name = "%.6f-%s-%s.json" % (collected_at, source[:16], self.rns.hexrep(message.hash, delimit=False)[:16])
        files = [n for n in os.listdir(self.unverified_dir) if n.endswith(".json")]
        if any(n.endswith("-%s.json" % name.rsplit("-", 1)[1][:-5]) for n in files):
            return   # already kept
        size = sum(os.path.getsize(os.path.join(self.unverified_dir, n)) for n in files)
        from_signer = sum(1 for n in files if n.split("-")[1] == source[:16])
        if (from_signer >= UNVERIFIED_MAX_PER_SIGNER or len(files) >= UNVERIFIED_MAX_FILES
                or size >= UNVERIFIED_MAX_BYTES):
            self.dropped += 1
            self.log("[inbox] batch from <%s> refused: the unverified queue is full" % source[:16])
            return
        record = {"packed": bytes(message.packed).hex(), "collected_at": collected_at}
        path = os.path.join(self.unverified_dir, name)
        with open(path + ".tmp", "w") as f:
            json.dump(record, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path + ".tmp", path)
        dir_fd = os.open(self.unverified_dir, os.O_RDONLY)   # the rename, durable too
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        self.rns.Transport.request_path(message.source_hash)   # prompts the signer's announce
        self.log("[inbox] batch from <%s> kept until its signer is known" % source[:16])

    def retry_unverified(self, now=None):
        """Check kept batches again; those now verifiable go on, those too old
        for Prometheus are dropped."""
        now = time.time() if now is None else now
        asked = set()
        for name in sorted(os.listdir(self.unverified_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.unverified_dir, name)
            try:
                with open(path) as f:
                    record = json.load(f)
                message = self.lxmf.LXMessage.unpack_from_bytes(bytes.fromhex(record["packed"]))
            except Exception as error:                      # noqa: BLE001
                self.log("[inbox] %s unreadable, dropped: %s" % (name, error))
                os.remove(path)
                continue
            if message.signature_validated:
                # Removed only once the batch is safely spooled: a failed
                # hand-off leaves it here for the next pass.
                try:
                    self._delivered(message, collected_at=record["collected_at"])
                except Exception as error:                  # noqa: BLE001
                    self.log("[inbox] %s: hand-off failed, kept: %s" % (name, error))
                    continue
                os.remove(path)
            elif message.unverified_reason != self.lxmf.LXMessage.SOURCE_UNKNOWN:
                self.dropped += 1
                self.log("[inbox] %s: signature invalid, dropped" % name)
                os.remove(path)
            elif now - record["collected_at"] > UNVERIFIED_KEEP_S:
                self.dropped += 1
                self.log("[inbox] %s: signer still unknown after 48 h, dropped" % name)
                os.remove(path)
            else:
                asked.add(bytes(message.source_hash))
        for source in asked:   # once per signer, not once per batch
            self.rns.Transport.request_path(source)

    def propagation_nodes(self, now=None):
        now = time.time() if now is None else now
        with self._lock:
            heard = [h for h, at in self._heard.items() if now - at < PN_FORGET_S]
        return self._always + [h for h in heard if h not in self._always]

    def sync_one(self, node_hash):
        """Download what waits for the gateway at one propagation node. Returns
        the router's final transfer state."""
        r = self.lxmf.LXMRouter
        self.router.set_outbound_propagation_node(node_hash)
        self.router.request_messages_from_propagation_node(self.identity)
        deadline = time.time() + TRANSFER_TIMEOUT_S
        state = self.router.propagation_transfer_state
        while time.time() < deadline:
            state = self.router.propagation_transfer_state
            if state == r.PR_COMPLETE or state >= r.PR_NO_PATH:
                break
            time.sleep(1)
        return state

    def sync_forever(self, interval=SYNC_INTERVAL_S):
        while True:
            if self._new_node.wait(timeout=interval):
                self._new_node.clear()
                time.sleep(SYNC_SOON_S)   # let its path and the announce settle
            try:
                self.retry_unverified()
            except Exception as error:                      # noqa: BLE001
                self.log("[inbox] retrying unverified batches failed: %s" % error)
            for node in self.propagation_nodes():
                try:
                    state = self.sync_one(node)
                    if state != self.lxmf.LXMRouter.PR_COMPLETE:
                        self.log("[inbox] <%s>: sync ended in state 0x%02x"
                                 % (self.rns.hexrep(node, delimit=False)[:16], state))
                except Exception as error:                      # noqa: BLE001
                    self.log("[inbox] <%s>: sync failed: %s" % (self.rns.hexrep(node, delimit=False)[:16], error))
