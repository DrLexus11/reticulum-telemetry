"""The gateway's codec against the shared fixture, tests/fixtures/telemetry_v1.json.

The firmware repository owns the wire format (TelemetryCodec.h) and its Python
twin; gateway/telemetry_codec.py is a copy of that twin, and the fixture here
is a copy of the firmware's. Both copies must stay byte-identical with their
originals -- a change is made there first and copied here in the same week.
This test is what catches a copy that drifted.
"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import telemetry_codec as tc  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/telemetry_v1.json").read_text())


def fields_of(t):
    return {name: getattr(t, name) for name in tc.Telemetry.__slots__}


class TelemetryFixture(unittest.TestCase):

    def test_the_fixture_is_this_wire_version(self):
        self.assertEqual(FIXTURE["version"], tc.WIRE_VERSION)

    def test_every_case_encodes_to_the_pinned_bytes(self):
        for case in FIXTURE["encode"]:
            with self.subTest(case["name"]):
                self.assertEqual(tc.encode(tc.Telemetry(**case["telemetry"])).hex(), case["hex"])

    def test_every_case_decodes_as_pinned(self):
        for case in FIXTURE["decode"]:
            with self.subTest(case["name"]):
                decoded = tc.decode(bytes.fromhex(case["hex"]))
                if case["telemetry"] is None:
                    self.assertIsNone(decoded)
                else:
                    self.assertEqual(fields_of(decoded), case["telemetry"])


if __name__ == "__main__":
    unittest.main()
