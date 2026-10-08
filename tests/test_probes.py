"""Reachability probes (gateway/probes.py): targets, counts, messages."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import probes  # noqa: E402


class Probes(unittest.TestCase):

    def test_interface_names_become_kinds(self):
        self.assertEqual(probes.kind_of("UDPInterface[RAD-01 Rev2/0.0.0.0:4244]"), "udp")
        self.assertEqual(probes.kind_of("RNodeInterface[LoRa 868]"), "lora")
        self.assertEqual(probes.kind_of("TCPServerInterface[Columba LAN TCP Server/0.0.0.0:4242]"), "tcp")
        self.assertEqual(probes.kind_of("AutoInterface[Default]"), "auto")
        self.assertEqual(probes.kind_of("Something"), "other")
        self.assertIsNone(probes.kind_of(None))

    def test_targets_survive_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "targets.json")
            book = probes.ProbeBook(path)
            self.assertTrue(book.heard("0a0b0c0d", "aa" * 16, "11" * 64))
            self.assertFalse(book.heard("0a0b0c0d", "aa" * 16, "11" * 64))
            self.assertTrue(book.heard("0a0b0c0d", "bb" * 16, "11" * 64))   # a new node hash replaces
            self.assertEqual(probes.ProbeBook(path).targets,
                             {"0a0b0c0d": {"node": "bb" * 16, "key": "11" * 64}})

    def test_an_older_targets_file_still_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "targets.json")
            open(path, "w").write('{"0a0b0c0d": "%s"}' % ("aa" * 16))
            self.assertEqual(probes.ProbeBook(path).targets, {"0a0b0c0d": {"node": "aa" * 16, "key": None}})

    def test_a_corrupt_targets_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "targets.json")
            for text in ("{not json", "[]", "null"):
                open(path, "w").write(text)
                self.assertEqual(probes.ProbeBook(path).targets, {})

    def test_messages_count_and_say_what_happened(self):
        book = probes.ProbeBook()
        m = book.result("0a0b0c0d", 100.0, True, rtt_s=2.41234, hops=2, via="udp", name="RAD-1", gateway="gw")
        json.dumps(m)
        self.assertEqual((m["sent_total"], m["delivered_total"], m["rtt_s"]), (1, 1, 2.4123))
        lost = book.result("0a0b0c0d", 400.0, False, rtt_s=5.0, hops=None)
        self.assertEqual((lost["sent_total"], lost["delivered_total"]), (2, 1))
        self.assertIsNone(lost["rtt_s"])      # no round-trip time without a proof
        self.assertFalse(lost["delivered"])


if __name__ == "__main__":
    unittest.main()
