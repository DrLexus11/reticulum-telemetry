"""Batches (T2) into backfill messages: gateway/backfill.py against the shared
fixture, tests/fixtures/telemetry_batch_v1.json (a copy of the firmware's)."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import backfill  # noqa: E402
import telemetry_batch_codec as bc  # noqa: E402
import telemetry_codec as tc  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/telemetry_batch_v1.json").read_text())


def case(name):
    return bytes.fromhex(next(c for c in FIXTURE["encode"] if c["name"] == name)["hex"])


class BatchFixture(unittest.TestCase):

    def test_the_copy_decodes_every_case_as_pinned(self):
        for c in FIXTURE["decode"]:
            with self.subTest(c["name"]):
                got = bc.decode(bytes.fromhex(c["hex"]))
                self.assertEqual(got is None, c["batch"] is None)


class Backfill(unittest.TestCase):

    def test_each_report_becomes_a_message_at_its_own_time(self):
        batch = bc.decode(case("absolute_health_and_detail"))
        out, refused = backfill.messages(case("absolute_health_and_detail"), 1791200000.0,
                                         gateway="gw", name_for=lambda s: "N-" + s)
        self.assertIsNone(refused)
        kept = [e for e in batch["entries"]]
        self.assertEqual(len(out), len(kept))
        for topic, m in out:
            json.dumps(m)
            self.assertEqual(topic, "mesh/telemetry/%08x/backfill" % batch["sender_id"])
            self.assertTrue(m["backfill"]["exact"])
        self.assertEqual([m["received_at"] for _, m in out], [1791096400, 1791098200])
        self.assertEqual([m["kind"] for _, m in out], ["health", "detail"])

    def test_a_board_without_a_clock_is_dated_from_collection_and_says_so(self):
        out, refused = backfill.messages(case("relative_clock_not_set"), 1000000.0)
        self.assertIsNone(refused)
        self.assertEqual([m["received_at"] for _, m in out], [996400.0, 998200.0, 1000000.0])
        self.assertFalse(any(m["backfill"]["exact"] for _, m in out))

    def test_a_batch_signed_by_another_board_is_refused(self):
        batch = bc.decode(case("absolute_health_and_detail"))
        out, refused = backfill.messages(case("absolute_health_and_detail"), 0.0,
                                         signer=batch["sender_id"] ^ 1)
        self.assertEqual(out, [])
        self.assertIn("claims", refused)

    def test_a_report_from_another_sender_inside_a_batch_is_skipped(self):
        foreign = tc.encode(tc.Telemetry(sender_id=0x99999999, uptime_s=5))
        own = tc.encode(tc.Telemetry(sender_id=0x0a0b0c0d, uptime_s=6))
        raw = bc.encode(0x0a0b0c0d, 1791100000, [
            {"time_kind": bc.TIME_ABSOLUTE, "time": 1791099000, "report": foreign},
            {"time_kind": bc.TIME_ABSOLUTE, "time": 1791099300, "report": own}])
        out, refused = backfill.messages(raw, 0.0)
        self.assertIsNone(refused)
        self.assertEqual([m["uptime_s"] for _, m in out], [6])

    def test_garbage_is_refused(self):
        out, refused = backfill.messages(b"\x31\x00", 0.0)
        self.assertEqual(out, [])
        self.assertIsNotNone(refused)


if __name__ == "__main__":
    unittest.main()
