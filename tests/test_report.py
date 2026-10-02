import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import report  # noqa: E402
import telemetry_codec as tc  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/telemetry_v1.json").read_text())


class ReportMessage(unittest.TestCase):

    def test_every_fixture_report_becomes_a_message(self):
        for case in FIXTURE["decode"]:
            if case["telemetry"] is None:
                continue
            with self.subTest(case["name"]):
                t = tc.decode(bytes.fromhex(case["hex"]))
                message = report.to_message(t, 1790000000.25, hops=2, via="TCPInterface[x]")
                json.dumps(message)   # must serialise
                self.assertEqual(message["sender"], "%08x" % t.sender_id)
                self.assertEqual(message["heap_bytes"], t.heap_bytes)

    def test_absent_optional_fields_are_null_not_zero(self):
        t = tc.Telemetry(sender_id=1)
        message = report.to_message(t, 1.0)
        self.assertIsNone(message["psram_bytes"])
        self.assertIsNone(message["battery_mv"])
        self.assertIsNone(message["battery_pct"])
        self.assertIsNone(message["hops"])

    def test_interfaces_are_spelled_out(self):
        self.assertEqual(report.interfaces(tc.IF_LORA | tc.IF_ESPNOW), ["lora", "espnow"])
        self.assertEqual(report.interfaces(0), [])

    def test_the_topic_names_the_sender(self):
        self.assertEqual(report.topic(0x0a0b0c0d), "mesh/telemetry/0a0b0c0d")


if __name__ == "__main__":
    unittest.main()
