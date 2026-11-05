"""When the gateway announces (gateway/announce_schedule.py): at start, every
interval, and when an interface comes up -- at most once per MIN_GAP_S."""

import sys
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
        # B comes up three seconds later: held until the gap has passed.
        self.assertIsNone(run(s, 23, {"A": True, "B": True}))
        self.assertIsNone(run(s, 29, {"A": True, "B": True}))
        self.assertEqual(run(s, 30, {"A": True, "B": True}), ["B"])
        self.assertIsNone(run(s, 32, {"A": True, "B": True}))

    def test_flapping_announces_at_most_once_per_gap(self):
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, {"A": True})
        announces = 0
        for t in range(1, 101):            # down and up every second for 100 s
            if run(s, t, {"A": t % 2 == 0}) is not None:
                announces += 1
        self.assertLessEqual(announces, 10)
        self.assertGreaterEqual(announces, 9)

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

    def test_shared_instance_away_then_back_announces(self):
        # interface_states() leaves rnsd's interfaces out while its RPC fails.
        s = AnnounceSchedule(600, min_gap_s=10)
        run(s, 0, UP)
        self.assertIsNone(run(s, 50, {}))
        self.assertEqual(sorted(run(s, 60, UP)), sorted(UP))


class _Interface:
    def __init__(self, name, online):
        self.name, self.online = name, online

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

    def test_missing_fields_are_tolerated(self):
        reticulum = _Reticulum({"interfaces": [{"status": True}, {"name": "X"}]})
        self.assertEqual(announce_schedule.interface_states(reticulum, _Transport(None)), {"X": False})
        self.assertEqual(announce_schedule.interface_states(_Reticulum(None), _Transport([])), {})


class GatewayUsesIt(unittest.TestCase):

    def test_serve_forever_asks_the_schedule(self):
        source = (ROOT / "gateway" / "telemetry_gateway.py").read_text(encoding="utf-8")
        body = source[source.index("def serve_forever"):source.index("\ndef main")]
        self.assertIn("announce_schedule.AnnounceSchedule(self.announce_interval)", body)
        self.assertIn("announce_schedule.interface_states(self.reticulum, self.rns.Transport)", body)
        self.assertIn("schedule.announced(now)", body)
        self.assertIn("time.monotonic()", body)


if __name__ == "__main__":
    unittest.main()
