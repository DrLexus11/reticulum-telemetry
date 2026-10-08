"""The gateway's LXMF inbox: batches boards could not deliver live (T2).

A board with no gateway in reach sends its kept reports, about hourly, as one
LXMF message to the gateway's LXMF delivery destination -- the gateway's own
identity with "lxmf"/"delivery", which the board computes from the identity it
learned from the gateway's uplink announce, so nothing extra is announced. The
message waits in the board's own propagation node. This inbox collects from
every propagation node it hears, one at a time, and hands each batch on with
the sender id of the identity that signed it.

A batch whose signature LXMF could not validate is dropped: the signer is what
stops one node writing another's history. The board's identity is known to the
gateway from its announces (its NomadNet node announces hourly).
"""

import threading
import time

SYNC_INTERVAL_S = 600      # one pass over the propagation nodes heard
SYNC_SOON_S = 30           # after a node is newly heard: a vehicle back in range
PN_FORGET_S = 2 * 3600     # a propagation node unheard this long is not visited
TRANSFER_TIMEOUT_S = 180   # per node: a LoRa path, a link and a resource take time


class Inbox:
    def __init__(self, rns, lxmf, identity, storagepath, display_name, on_batch, log=print, always=()):
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

    def _delivered(self, message):
        content = message.content if isinstance(message.content, (bytes, bytearray)) else b""
        if not content or content[0] != 0x31:
            self.log("[inbox] ignored a message from <%s>: not a telemetry batch"
                     % self.rns.hexrep(message.source_hash, delimit=False)[:16])
            return
        if not message.signature_validated:
            self.dropped += 1
            self.log("[inbox] dropped a batch from <%s>: signature not validated (%s)"
                     % (self.rns.hexrep(message.source_hash, delimit=False)[:16], message.unverified_reason))
            return
        signer_identity = self.rns.Identity.recall(message.source_hash)
        if signer_identity is None:
            self.dropped += 1
            self.log("[inbox] dropped a batch: signer identity not known")
            return
        signer = int.from_bytes(signer_identity.hash[:4], "big")
        self.collected += 1
        self.on_batch(bytes(content), time.time(), signer)

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
            for node in self.propagation_nodes():
                try:
                    state = self.sync_one(node)
                    if state != self.lxmf.LXMRouter.PR_COMPLETE:
                        self.log("[inbox] <%s>: sync ended in state 0x%02x"
                                 % (self.rns.hexrep(node, delimit=False)[:16], state))
                except Exception as error:                      # noqa: BLE001
                    self.log("[inbox] <%s>: sync failed: %s" % (self.rns.hexrep(node, delimit=False)[:16], error))
