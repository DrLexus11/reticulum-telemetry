"""The telemetry gateway: board health reports off the mesh and into MQTT.

Any node with an uplink can run one. It announces rnstransport.telemetry.uplink
-- the boards keep the gateways they hear and send each report to the nearest
one they have a path to -- decodes each report (telemetry_codec.py, pinned by
tests/fixtures/telemetry_v1.json) and publishes it as JSON on
mesh/telemetry/<sender>, retained.

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
        self.names = board_names.BoardNames(
            os.path.join(os.path.dirname(os.path.expanduser(identity_path)) or ".", "board_names.json"))
        gateway = self

        class _NodeAnnounces:
            aspect_filter = "nomadnetwork.node"

            def received_announce(self, destination_hash, announced_identity, app_data):
                if announced_identity is not None and gateway.names.heard(announced_identity.hash, app_data):
                    print("[gateway] %s is %s" % (board_names.sender_of(announced_identity.hash),
                                                  gateway.names.name_for(board_names.sender_of(announced_identity.hash))),
                          flush=True)

        RNS.Transport.register_announce_handler(_NodeAnnounces())
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

    def _packet(self, data, packet):
        t = telemetry_codec.decode(bytes(data))
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
