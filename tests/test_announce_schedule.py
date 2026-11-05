"""When the gateway announces (gateway/announce_schedule.py): at start, every
interval, and when an interface comes up -- rationed, with a gap that doubles
while interfaces keep coming up, and never waiting on the shared instance."""

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import announce_schedule  # noqa: E402
from announce_schedule import AnnounceSchedule  # noqa: E402

UP = {"AutoInterface[Default]": True, "TCPServerInterface[LAN]": True}


def run(schedule, now, states):
    """One poll of serve_forever: what was due, recorded as announced."""
    reason = schedule.due(now, states)
    if reason is not None:
        schedule.announced(now)
    return reason


class Schedule(unittest.TestCase):

    def test_announces_at_start_and_every_interval(self):
        s = AnnounceSchedule(600)
        self.assertEqual(run(s, 0, UP), "start")
        self.assertIsNone(run(s, 599, UP))
        self.assertEqual(run(s, 600, UP), "interval")
        self.assertIsNone(run(s, 1199, UP))
        self.assertEqual(run(s, 1200, UP), "interval")

    def test_interfaces_up_at_start_are_covered_by_the_start_announce(self):
        s = AnnounceSchedule(600)
        self.assertEqual(run(s, 0, UP), "start")
        self.assertIsNone(run(s, 30, UP))

    def test_an_interface_coming_back_announces_once(self):
        s = AnnounceSchedule(600)
        run(s, 0, UP)
        self.assertIsNone(run(s, 100, {**UP, "AutoInterface[Default]": False}))
        self.assertEqual(run(s, 102, UP), ["AutoInterface[Default]"])
        self.assertIsNone(run(s, 104, UP))

    def test_a_new_interface_counts_as_come_up(self):
        s = AnnounceSchedule(600)
        run(s, 0, UP)
        self.assertEqual(run(s, 50, {**UP, "TCPInterface[Client on 10.0.0.2]": True}),
                         ["TCPInterface[Client on 10.0.0.2]"])

    def test_an_interface_that_appears_down_does_not_announce(self):
        s = AnnounceSchedule(600)
        run(s, 0, UP)
        self.assertIsNone(run(s, 50, {**UP, "RNodeInterface[LoRa]": False}))

    def test_rate_limited_but_not_lost(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": False, "B": False})
        self.assertEqual(run(s, 20, {"A": True, "B": False}), ["A"])
        self.assertEqual(s.gap_s, 20)
        # B comes up three seconds later: held until the (doubled) gap has passed.
        self.assertIsNone(run(s, 23, {"A": True, "B": True}))
        self.assertIsNone(run(s, 39, {"A": True, "B": True}))
        self.assertEqual(run(s, 40, {"A": True, "B": True}), ["B"])
        self.assertIsNone(run(s, 42, {"A": True, "B": True}))

    def test_flapping_backs_off_to_the_interval(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": True})
        times = []
        for t in range(1, 3601):           # down and up every second for an hour
            if run(s, t, {"A": t % 2 == 0}) is not None:
                times.append(t)
        # 10, 20, 40, 80, 160, 320 s gaps, then one per interval.
        self.assertEqual(times[:6], [10, 30, 70, 150, 310, 630])
        self.assertEqual(len(times), 10)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertEqual(gaps[-4:], [600, 600, 600, 600])

    def test_a_fresh_client_name_every_poll_backs_off(self):
        # A TCP server's spawned interface is named after the client's ephemeral
        # port: a client stuck reconnecting is a "new" interface on every poll.
        # interface_states() leaves spawned ones out; this is the backstop if a
        # churning name reaches the schedule anyway.
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, UP)
        announces = []
        for poll, t in enumerate(range(2, 7202, 2)):   # two hours of polls
            states = {**UP, "TCPInterface[Client on LAN/10.0.0.2:%d]" % (40000 + poll): True}
            if run(s, t, states) is not None:
                announces.append(t)
        # Without backoff this would be one per 10 s: 720. With it, about one
        # per interval once the gap has grown.
        self.assertLessEqual(len(announces), 18)
        self.assertEqual(s.gap_s, 600)

    def test_a_quiet_interval_resets_the_gap(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": True})
        run(s, 5, {"A": False})
        run(s, 10, {"A": True})
        run(s, 15, {"A": False})
        self.assertEqual(run(s, 30, {"A": True}), ["A"])
        self.assertEqual(s.gap_s, 40)
        self.assertEqual(run(s, 630, {"A": True}), "interval")
        # Nothing comes up for an interval; then A does, 15 s after the last
        # announce: the gap is back to 10 s, so it is announced at once.
        run(s, 640, {"A": False})
        self.assertEqual(run(s, 645, {"A": True}), ["A"])
        self.assertEqual(s.gap_s, 20)

    def test_the_start_announce_counts_toward_the_gap(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": False})
        self.assertIsNone(run(s, 2, {"A": True}))
        self.assertEqual(run(s, 10, {"A": True}), ["A"])

    def test_one_that_went_down_again_before_its_announce_is_dropped(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": False})
        self.assertIsNone(run(s, 2, {"A": True}))
        self.assertIsNone(run(s, 4, {"A": False}))
        self.assertIsNone(run(s, 12, {"A": False}))

    def test_an_announce_for_an_interface_restarts_the_interval(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": True})
        run(s, 100, {"A": False})
        self.assertEqual(run(s, 300, {"A": True}), ["A"])
        self.assertIsNone(run(s, 600, {"A": True}))
        self.assertEqual(run(s, 900, {"A": True}), "interval")

    def test_no_fresh_look_is_no_change(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        self.assertEqual(run(s, 0, None), "start")       # the start announce waits on no read
        self.assertIsNone(run(s, 5, UP))                  # the first fresh look only learns
        self.assertIsNone(run(s, 50, None))
        self.assertEqual(run(s, 600, None), "interval")   # the interval never waits on a read
        self.assertIsNone(run(s, 610, None))
        # A stale gap does not lose what it hid: down before it, up after.
        self.assertIsNone(run(s, 620, {**UP, "AutoInterface[Default]": False}))
        self.assertIsNone(run(s, 625, None))
        self.assertEqual(run(s, 630, UP), ["AutoInterface[Default]"])

    def test_shared_instance_away_then_back_announces(self):
        # interface_states() leaves rnsd's interfaces out while its RPC fails.
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, UP)
        self.assertIsNone(run(s, 50, {}))
        self.assertEqual(sorted(run(s, 60, UP)), sorted(UP))


class _Interface:
    def __init__(self, name, online, parent=None):
        self.name, self.online, self.parent_interface = name, online, parent

    def __str__(self):
        return self.name


class _Transport:
    def __init__(self, interfaces):
        self.interfaces = interfaces


class _Reticulum:
    def __init__(self, stats=None, error=None):
        self.stats, self.error = stats, error

    def get_interface_stats(self):
        if self.error:
            raise self.error
        return self.stats


class States(unittest.TestCase):

    def test_own_and_shared_instance_interfaces(self):
        transport = _Transport([_Interface("LocalInterface[37428]", True)])
        reticulum = _Reticulum({"interfaces": [
            {"name": "AutoInterface[Default]", "status": True},
            {"name": "RNodeInterface[LoRa]", "status": False},
        ]})
        self.assertEqual(announce_schedule.interface_states(reticulum, transport), {
            "LocalInterface[37428]": True,
            "AutoInterface[Default]": True,
            "RNodeInterface[LoRa]": False,
        })

    def test_shared_instance_unreachable_leaves_its_interfaces_out(self):
        transport = _Transport([_Interface("LocalInterface[37428]", False)])
        reticulum = _Reticulum(error=ConnectionRefusedError())
        self.assertEqual(announce_schedule.interface_states(reticulum, transport),
                         {"LocalInterface[37428]": False})

    def test_an_instance_owner_sees_each_interface_once(self):
        auto = _Interface("AutoInterface[Default]", True)
        reticulum = _Reticulum({"interfaces": [{"name": "AutoInterface[Default]", "status": True}]})
        self.assertEqual(announce_schedule.interface_states(reticulum, _Transport([auto])),
                         {"AutoInterface[Default]": True})

    def test_spawned_client_interfaces_are_left_out(self):
        server = _Interface("TCPServerInterface[LAN/0.0.0.0:4242]", True)
        client = _Interface("TCPInterface[Client on LAN/10.0.0.2:51234]", True, parent=server)
        reticulum = _Reticulum({"interfaces": [
            {"name": "TCPServerInterface[LAN/0.0.0.0:4242]", "status": True},
            {"name": "TCPInterface[Client on LAN/10.0.0.3:40001]", "status": True,
             "parent_interface_name": "TCPServerInterface[LAN/0.0.0.0:4242]"},
            {"name": "AutoInterfacePeer[Default/fe80::1]", "status": True,
             "parent_interface_name": "AutoInterface[Default]"},
            {"name": "AutoInterface[Default]", "status": True, "parent_interface_name": None},
        ]})
        self.assertEqual(announce_schedule.interface_states(reticulum, _Transport([server, client])), {
            "TCPServerInterface[LAN/0.0.0.0:4242]": True,
            "AutoInterface[Default]": True,
        })

    def test_missing_fields_are_tolerated(self):
        reticulum = _Reticulum({"interfaces": [{"status": True}, {"name": "X"}]})
        self.assertEqual(announce_schedule.interface_states(reticulum, _Transport(None)), {"X": False})
        self.assertEqual(announce_schedule.interface_states(_Reticulum(None), _Transport([])), {})


class Reader(unittest.TestCase):

    def test_a_snapshot_goes_stale(self):
        clock = [100.0]
        reader = announce_schedule.InterfaceReader(lambda: dict(UP), clock=lambda: clock[0])
        self.assertIsNone(announce_schedule.fresh(reader.latest(), 100.0))   # nothing read yet
        reader.read_once()
        self.assertEqual(announce_schedule.fresh(reader.latest(), 105.0), UP)
        self.assertEqual(announce_schedule.fresh(reader.latest(), 100.0 + announce_schedule.STALE_S), UP)
        self.assertIsNone(announce_schedule.fresh(reader.latest(), 100.1 + announce_schedule.STALE_S))

    def test_a_read_that_hangs_holds_up_nothing(self):
        release = threading.Event()
        reads = []

        def hang():
            reads.append(1)
            release.wait(5)
            return dict(UP)

        reader = announce_schedule.InterfaceReader(hang, poll_s=0.01).start()
        try:
            for _ in range(200):
                if reads:
                    break
                threading.Event().wait(0.01)
            self.assertTrue(reads, "the reader thread never read")
            # The read is stuck; the caller still gets an answer at once, and
            # the schedule still makes its start and interval announces.
            self.assertEqual(reader.latest(), (None, None))
            s = AnnounceSchedule(600)
            self.assertEqual(run(s, 0, announce_schedule.fresh(reader.latest(), 0)), "start")
            self.assertEqual(run(s, 600, announce_schedule.fresh(reader.latest(), 600)), "interval")
        finally:
            release.set()

    def test_a_failing_read_does_not_end_the_thread(self):
        calls = []
        done = threading.Event()

        def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise ConnectionRefusedError()
            done.set()
            return {}

        reader = announce_schedule.InterfaceReader(flaky, poll_s=0.01).start()
        self.assertTrue(done.wait(5))
        self.assertIsNotNone(reader.latest()[0])


class GatewayUsesIt(unittest.TestCase):

    def test_serve_forever_reads_on_a_thread_and_asks_the_schedule(self):
        source = (ROOT / "gateway" / "telemetry_gateway.py").read_text(encoding="utf-8")
        body = source[source.index("def serve_forever"):source.index("\ndef main")]
        self.assertIn("announce_schedule.AnnounceSchedule(self.announce_interval)", body)
        self.assertIn("announce_schedule.InterfaceReader(", body)
        self.assertIn("announce_schedule.interface_states(self.reticulum, self.rns.Transport)).start()", body)
        self.assertIn("announce_schedule.fresh(reader.latest(), now)", body)
        self.assertIn("schedule.announced(now)", body)
        self.assertIn("time.monotonic()", body)
        # The loop itself never calls the RPC.
        loop = body[body.index("while True:"):]
        self.assertNotIn("interface_states(", loop)
        self.assertNotIn("get_interface_stats", loop)


if __name__ == "__main__":
    unittest.main()
