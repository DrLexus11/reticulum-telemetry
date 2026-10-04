"""The gateway's detail codec against the shared fixture,
tests/fixtures/telemetry_detail_v1.json.

As with the health report (test_codec_fixture.py): the firmware repository
owns the wire format (TelemetryDetailCodec.h) and its Python twin;
gateway/telemetry_detail_codec.py and the fixture are copies, kept
byte-identical. This test catches a copy that drifted.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import report  # noqa: E402
import telemetry_detail_codec as dc  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/telemetry_detail_v1.json").read_text())


class DetailFixture(unittest.TestCase):

    def test_the_fixture_is_this_wire_version(self):
        self.assertEqual(FIXTURE["version"], dc.WIRE_VERSION)

    def test_every_case_encodes_to_the_pinned_bytes(self):
        for case in FIXTURE["encode"]:
            with self.subTest(case["name"]):
                self.assertEqual(dc.encode(case["detail"]).hex(), case["hex"])

    def test_every_case_decodes_as_pinned(self):
        for case in FIXTURE["decode"]:
            with self.subTest(case["name"]):
                self.assertEqual(dc.decode(bytes.fromhex(case["hex"])), case["detail"])


class DetailMessage(unittest.TestCase):

    def test_every_fixture_report_becomes_a_message(self):
        for case in FIXTURE["decode"]:
            if case["detail"] is None:
                continue
            with self.subTest(case["name"]):
                d = dc.decode(bytes.fromhex(case["hex"]))
                message = report.detail_to_message(d, 1790000000.25, name_for=lambda s: "N-" + s)
                json.dumps(message)   # must serialise
                self.assertEqual(message["sender"], "%08x" % d["sender_id"])
                self.assertEqual(len(message["neighbours"]), len(d["neighbours"]))
                for n in message["neighbours"]:
                    self.assertEqual(n["name"], "N-" + n["node"])

    def test_unknowns_are_null_not_sentinels(self):
        d = dc.new_detail(sender_id=1, radio_known=True, propagation_known=True,
                          neighbours=[{"id": 2, "kind": 1, "rssi": dc.RSSI_UNKNOWN, "heard_s": 60}])
        message = report.detail_to_message(dc.decode(dc.encode(d)), 1.0)
        self.assertIsNone(message["radio"]["rssi_dbm"])
        self.assertIsNone(message["radio"]["noise_dbm"])
        self.assertIsNone(message["propagation"]["last_sync_s"])
        self.assertIsNone(message["neighbours"][0]["rssi_dbm"])
        self.assertIsNone(message["neighbours"][0]["name"])
        self.assertIsNone(message["firmware"]["env"])

    def test_a_board_without_radio_or_store_says_so(self):
        message = report.detail_to_message(dc.new_detail(sender_id=1), 1.0)
        self.assertIsNone(message["radio"])
        self.assertIsNone(message["propagation"])

    def test_the_version_reads_as_rnodeconf_prints_it(self):
        message = report.detail_to_message(dc.new_detail(fw_version=0x0156), 1.0)
        self.assertEqual(message["firmware"]["version"], "1.86")

    def test_the_detail_topic_sits_under_the_board(self):
        self.assertEqual(report.detail_topic(0x0a0b0c0d), "mesh/telemetry/0a0b0c0d/detail")


if __name__ == "__main__":
    unittest.main()
