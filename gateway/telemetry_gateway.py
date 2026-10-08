"""The telemetry gateway: board health reports off the mesh and into MQTT.

Any node with an uplink can run one. It announces rnstransport.telemetry.uplink
-- the boards keep the gateways they hear and send each report to the nearest
one they have a path to -- decodes each report and publishes it as JSON,
retained: the five-minute health report (telemetry_codec.py, pinned by
tests/fixtures/telemetry_v1.json) on mesh/telemetry/<sender>, the half-hourly
detail report (telemetry_detail_codec.py, tests/fixtures/telemetry_detail_v1.json)
on mesh/telemetry/<sender>/detail. The first byte tells them apart.

    python gateway/telemetry_gateway.py --identity ~/.impr-tak/telemetry-gateway/identity

Reticulum is reached through the host's shared instance (rnsd) unless
--rnsconfig names another. The broker is localhost:1883 by default; this
gateway never takes credentials on the command line -- if the broker needs
them, they are read from the environment (MQTT_USERNAME, MQTT_PASSWORD).
"""

import argparse
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backfill  # noqa: E402
import board_names  # noqa: E402
import lxmf_inbox  # noqa: E402
import positions  # noqa: E402
import probes  # noqa: E402
import spool  # noqa: E402
import report  # noqa: E402
import telemetry_codec  # noqa: E402
import telemetry_detail_codec  # noqa: E402

APP_NAME = "rnstransport"
ASPECTS = ("telemetry", "uplink")

# Boards forget a gateway unheard for two hours (TelemetryUplink.h); this keeps
# well inside that, at the cost of one announce per gateway per interval.
ANNOUNCE_INTERVAL_S = 600


class Gateway:
    def __init__(self, identity_path, rnsconfig, broker, port, announce_interval, name, propagation_nodes=(),
                 positions_path="~/.config/reticulum-telemetry/board_positions.json"):
        import RNS
        import paho.mqtt.client as mqtt

        self.rns = RNS
        self.reticulum = RNS.Reticulum(rnsconfig)
        self.identity = self._identity(identity_path)
        self.destination = RNS.Destination(self.identity, RNS.Destination.IN,
                                           RNS.Destination.SINGLE, APP_NAME, *ASPECTS)
        self.destination.set_packet_callback(self._packet)
        # Boards' names, from their NomadNet announces (board_names.py), kept
        # beside the identity so a restart names them at once.
        folder = os.path.dirname(os.path.expanduser(identity_path)) or "."
        self.names = board_names.BoardNames(os.path.join(folder, "board_names.json"))
        # Boards to probe, by their NomadNet node (probes.py).
        self.probe_book = probes.ProbeBook(os.path.join(folder, "probe_targets.json"))
        # People, from their LXMF display names: who a board hears is often a
        # phone. Kept apart, so a board announcing both keeps its node's name.
        self.people = board_names.BoardNames(os.path.join(folder, "people_names.json"),
                                             parse=board_names.lxmf_display_name)
        gateway = self

        class _NodeAnnounces:
            aspect_filter = "nomadnetwork.node"

            def received_announce(self, destination_hash, announced_identity, app_data):
                if announced_identity is not None:
                    gateway.probe_book.heard(board_names.sender_of(announced_identity.hash),
                                             bytes(destination_hash).hex(),
                                             announced_identity.get_public_key().hex())
                if announced_identity is not None and gateway.names.heard(announced_identity.hash, app_data):
                    print("[gateway] %s is %s" % (board_names.sender_of(announced_identity.hash),
                                                  gateway.names.name_for(board_names.sender_of(announced_identity.hash))),
                          flush=True)

        class _PeopleAnnounces:
            aspect_filter = "lxmf.delivery"

            def received_announce(self, destination_hash, announced_identity, app_data):
                if announced_identity is not None:
                    gateway.people.heard(announced_identity.hash, app_data)

        RNS.Transport.register_announce_handler(_NodeAnnounces())
        RNS.Transport.register_announce_handler(_PeopleAnnounces())
        self.announce_interval = announce_interval
        self.name = name
        self.received = 0
        self.refused = 0

        self.mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                client_id="telemetry-gateway-%s" % self.destination.hash.hex()[:8])
        user = os.environ.get("MQTT_USERNAME")
        if user:
            self.mqtt.username_pw_set(user, os.environ.get("MQTT_PASSWORD"))
        self.mqtt.on_connect = lambda c, u, f, rc, p=None: print(
            "[gateway] broker %s:%d %s" % (broker, port, "connected" if rc == 0 else "refused: %s" % rc),
            flush=True)
        # Reports keep arriving while the broker is away; paho queues and
        # reconnects, so a broker restart loses nothing retained.
        self.mqtt.connect_async(broker, port)
        self.mqtt.loop_start()

        # T2: reports boards kept while no gateway was in reach, collected from
        # the propagation nodes as LXMF batches (lxmf_inbox.py, backfill.py).
        # After MQTT, so a delivery always has a client to publish through.
        # The delivery callback only spools: once it returns, the propagation
        # node deletes its copy (spool.py).
        self.spool = spool.Spool(os.path.join(folder, "spool"))
        self._drain_now = threading.Event()
        self._drain_now.set()   # whatever a previous run left, at start
        threading.Thread(target=self._drain_forever, daemon=True, name="spool-drain").start()
        import LXMF
        self.inbox = lxmf_inbox.Inbox(RNS, LXMF, self.identity, os.path.join(folder, "lxmf"), name,
                                      self._batch, log=lambda line: print(line, flush=True),
                                      always=propagation_nodes)
        threading.Thread(target=self.inbox.sync_forever, daemon=True, name="lxmf-inbox").start()
        threading.Thread(target=self._probe_forever, daemon=True, name="probes").start()
        self.positions_path = positions_path
        threading.Thread(target=self._positions_forever, daemon=True, name="positions").start()

    def _identity(self, path):
        path = os.path.expanduser(path)
        if os.path.exists(path):
            identity = self.rns.Identity.from_file(path)
            if identity is None:
                sys.exit("could not read the gateway identity at %s" % path)
            return identity
        parent = os.path.dirname(path)
        if parent:   # a bare filename lives in the current directory
            os.makedirs(parent, exist_ok=True)
        identity = self.rns.Identity()
        identity.to_file(path)
        os.chmod(path, 0o600)
        print("[gateway] new identity written to %s" % path, flush=True)
        return identity

    def name_for(self, sender_hex):
        return self.names.name_for(sender_hex) or self.people.name_for(sender_hex)

    def _packet(self, data, packet):
        data = bytes(data)
        if data[:1] == bytes([telemetry_detail_codec.WIRE_VERSION]):
            self._detail(data, packet)
            return
        t = telemetry_codec.decode(data)
        if t is None:
            self.refused += 1
            print("[gateway] refused a %d-byte packet: not a v%d report"
                  % (len(data), telemetry_codec.WIRE_VERSION), flush=True)
            return
        self.received += 1
        via = str(packet.receiving_interface) if getattr(packet, "receiving_interface", None) else None
        message = report.to_message(t, time.time(), hops=getattr(packet, "hops", None),
                                    via=via, gateway=self.name,
                                    name=self.names.name_for(report.sender_hex(t.sender_id)))
        self.mqtt.publish(report.topic(t.sender_id), json.dumps(message), qos=1, retain=True)
        print("[gateway] %s up %ds heap %dK largest %dK crashes %d, %s hop(s)"
              % (message["sender"], t.uptime_s, t.heap_bytes // 1024, t.largest_bytes // 1024,
                 t.crashes, message["hops"]), flush=True)

    def _detail(self, data, packet):
        d = telemetry_detail_codec.decode(data)
        if d is None:
            self.refused += 1
            print("[gateway] refused a %d-byte detail report" % len(data), flush=True)
            return
        self.received += 1
        via = str(packet.receiving_interface) if getattr(packet, "receiving_interface", None) else None
        message = report.detail_to_message(d, time.time(), hops=getattr(packet, "hops", None),
                                           via=via, gateway=self.name,
                                           name=self.names.name_for(report.sender_hex(d["sender_id"])),
                                           name_for=self.name_for)
        self.mqtt.publish(report.detail_topic(d["sender_id"]), json.dumps(message), qos=1, retain=True)
        print("[gateway] %s detail: firmware %s %s, %d interface(s), %d neighbour(s)"
              % (message["sender"], message["firmware"]["hash"], message["firmware"]["env"],
                 len(message["interfaces"]), len(message["neighbours"])), flush=True)

    def _batch(self, content, collected_at, signer):
        """The LXMF delivery callback: durable first, published by the drain."""
        self.spool.put(content, collected_at, signer)
        self._drain_now.set()

    def _to_messages(self, content, collected_at, signer):
        out, refused = backfill.messages(content, collected_at, gateway=self.name,
                                         name_for=self.name_for, signer=signer)
        if refused:
            self.refused += 1
        elif out:
            first, last = out[0][1]["received_at"], out[-1][1]["received_at"]
            print("[gateway] %08x backfill: %d report(s), %s to %s"
                  % (signer, len(out), time.strftime("%m-%d %H:%M", time.localtime(first)),
                     time.strftime("%m-%d %H:%M", time.localtime(last))), flush=True)
        return out, refused

    def _publish_acknowledged(self, topic, message):
        # Not retained: the live topic keeps the board's current state.
        info = self.mqtt.publish(topic, json.dumps(message), qos=1, retain=False)
        try:
            info.wait_for_publish(timeout=15)
        except (RuntimeError, ValueError):     # not queued: disconnected, or the queue is full
            return False
        return info.is_published()

    def _drain_forever(self):
        while True:
            self._drain_now.wait(timeout=60)
            self._drain_now.clear()
            try:
                self.spool.drain(self._to_messages, self._publish_acknowledged,
                                 log=lambda line: print(line, flush=True))
            except Exception as error:                      # noqa: BLE001
                print("[spool] drain failed: %s" % error, flush=True)

    def _positions_forever(self):
        """Publish each board's configured position (positions.py), at start and
        whenever the file changes."""
        published = None
        while True:
            current = positions.load(self.positions_path)
            if current != published:
                now = time.time()
                for sender, position in current.items():
                    message = positions.message(sender, position, now, name=self.name_for(sender))
                    self.mqtt.publish(report.TOPIC_PREFIX + sender + "/position", json.dumps(message),
                                      qos=1, retain=True)
                for sender in set(published or {}) - set(current):
                    # Taken out of the file: clear the retained position.
                    self.mqtt.publish(report.TOPIC_PREFIX + sender + "/position", b"", qos=1, retain=True)
                if current:
                    print("[gateway] %d board position(s) published" % len(current), flush=True)
                published = current
            time.sleep(60)

    def _probe_forever(self):
        time.sleep(60)   # let paths form after start
        next_pass = time.time()
        while True:
            for sender, target in self.probe_book.snapshot():
                try:
                    self._probe(sender, target)
                except Exception as error:                      # noqa: BLE001
                    print("[probe] %s: %s" % (sender, error), flush=True)
                time.sleep(2)   # one board at a time: no burst on a shared channel
            # Each pass starts an interval after the last one started, however
            # many boards there are.
            next_pass += probes.PROBE_INTERVAL_S
            time.sleep(max(0.0, next_pass - time.time()))

    def _probe(self, sender, target):
        RNS = self.rns
        node_hash = bytes.fromhex(target["node"])
        identity = None
        if target.get("key"):
            identity = RNS.Identity(create_keys=False)
            identity.load_public_key(bytes.fromhex(target["key"]))
        else:
            identity = RNS.Identity.recall(node_hash)
        if identity is None:
            RNS.Transport.request_path(node_hash)
            print("[probe] %s: identity not known yet; waiting for its announce" % sender, flush=True)
            return
        destination = RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                      "rnstransport", "probe")
        if not RNS.Transport.has_path(destination.hash):
            RNS.Transport.request_path(destination.hash)
            deadline = time.time() + 15
            while not RNS.Transport.has_path(destination.hash) and time.time() < deadline:
                time.sleep(0.5)
        if not RNS.Transport.has_path(destination.hash):
            self._probe_result(sender, False, heal=(destination.hash, node_hash))
            return
        hops = RNS.Transport.hops_to(destination.hash)
        try:
            via = probes.kind_of(self.reticulum.get_next_hop_if_name(destination.hash))
        except Exception:                                       # noqa: BLE001
            via = None
        receipt = RNS.Packet(destination, os.urandom(16)).send()
        if not receipt:
            self._probe_result(sender, False, hops=hops, via=via, heal=(destination.hash, node_hash))
            return
        receipt.set_timeout(probes.PROBE_TIMEOUT_S)
        receipt.set_delivery_callback(
            lambda r: self._probe_result(sender, True, rtt=r.get_rtt(), hops=hops, via=via))
        receipt.set_timeout_callback(lambda r: self._probe_result(sender, False, hops=hops, via=via,
                                                                  heal=(destination.hash, node_hash)))

    def _probe_result(self, sender, delivered, rtt=None, hops=None, via=None, heal=()):
        should_heal, message = self.probe_book.result(sender, time.time(), delivered, rtt_s=rtt, hops=hops,
                                                      via=via, name=self.name_for(sender), gateway=self.name)
        self.mqtt.publish(report.TOPIC_PREFIX + sender + "/probe", json.dumps(message), qos=1, retain=True)
        if heal and should_heal:
            # The shared instance holds the path for everything on this host,
            # the TAK bridge included: dropping it heals the route for all.
            for destination_hash in heal:
                try:
                    self.reticulum.drop_path(destination_hash)
                except Exception as error:                      # noqa: BLE001
                    print("[probe] %s: could not drop a path: %s" % (sender, error), flush=True)
                self.rns.Transport.request_path(destination_hash)
            print("[probe] %s: probes lost in a row; dropped its paths and asked again" % sender, flush=True)

    def announce(self):
        self.destination.announce(app_data=self.name.encode("utf-8"))

    def serve_forever(self):
        print("[gateway] %s on <%s>, announcing every %d s"
              % (self.name, self.destination.hash.hex(), self.announce_interval), flush=True)
        self.announce()
        while True:
            time.sleep(self.announce_interval)
            try:
                self.announce()
            except Exception as error:                      # noqa: BLE001
                print("[gateway] announce failed: %s" % error, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--identity", required=True,
                        help="the gateway's Reticulum identity file; created if absent")
    parser.add_argument("--rnsconfig", default=None,
                        help="Reticulum config directory; default: the shared instance")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--announce-interval", type=int, default=ANNOUNCE_INTERVAL_S)
    parser.add_argument("--name", default="gateway", help="announced with the destination")
    parser.add_argument("--positions", default="~/.config/reticulum-telemetry/board_positions.json",
                        help="host-local file of configured board positions (gateway/positions.py)")
    parser.add_argument("--propagation-node", action="append", default=[], metavar="HASH",
                        help="an LXMF propagation node to collect batches from even unheard (repeatable); "
                             "nodes heard announcing are visited anyway")
    args = parser.parse_args()
    if args.announce_interval <= 0:
        sys.exit("--announce-interval must be a positive number of seconds")
    try:
        nodes = [bytes.fromhex(h) for h in args.propagation_node]
    except ValueError:
        sys.exit("--propagation-node takes a destination hash in hex")
    if any(len(n) != 16 for n in nodes):
        sys.exit("--propagation-node takes a 16-byte destination hash (32 hex digits)")
    gateway = Gateway(args.identity, args.rnsconfig, args.broker, args.port,
                      args.announce_interval, args.name, propagation_nodes=nodes,
                      positions_path=args.positions)
    try:
        gateway.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] received %d, refused %d" % (gateway.received, gateway.refused))


if __name__ == "__main__":
    main()
