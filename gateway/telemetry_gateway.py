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
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import board_names  # noqa: E402
import report  # noqa: E402
import telemetry_codec  # noqa: E402
import telemetry_detail_codec  # noqa: E402

APP_NAME = "rnstransport"
ASPECTS = ("telemetry", "uplink")

# Boards forget a gateway unheard for two hours (TelemetryUplink.h); this keeps
# well inside that, at the cost of one announce per gateway per interval.
ANNOUNCE_INTERVAL_S = 600


class Gateway:
    def __init__(self, identity_path, rnsconfig, broker, port, announce_interval, name):
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
        # People, from their LXMF display names: who a board hears is often a
        # phone. Kept apart, so a board announcing both keeps its node's name.
        self.people = board_names.BoardNames(os.path.join(folder, "people_names.json"),
                                             parse=board_names.lxmf_display_name)
        gateway = self

        class _NodeAnnounces:
            aspect_filter = "nomadnetwork.node"

            def received_announce(self, destination_hash, announced_identity, app_data):
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
    args = parser.parse_args()
    if args.announce_interval <= 0:
        sys.exit("--announce-interval must be a positive number of seconds")
    gateway = Gateway(args.identity, args.rnsconfig, args.broker, args.port,
                      args.announce_interval, args.name)
    try:
        gateway.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] received %d, refused %d" % (gateway.received, gateway.refused))


if __name__ == "__main__":
    main()
