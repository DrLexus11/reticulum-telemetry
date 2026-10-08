"""The durable hand-off between the LXMF inbox and MQTT (gateway/spool.py)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import spool  # noqa: E402


def two_messages(batch, collected_at, signer):
    return [("t/a", {"n": 1}), ("t/b", {"n": 2})], None


class SpoolTest(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.spool = spool.Spool(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_a_batch_is_on_disk_before_put_returns_and_gone_once_acknowledged(self):
        self.spool.put(b"\x31\x00", 100.0, 0x0a0b0c0d)
        self.assertEqual(len(self.spool.pending()), 1)
        sent = []
        self.assertEqual(self.spool.drain(two_messages, lambda t, m: sent.append(t) or True, log=lambda _l: None), 1)
        self.assertEqual(sent, ["t/a", "t/b"])
        self.assertEqual(self.spool.pending(), [])

    def test_an_unacknowledged_batch_stays_for_the_next_drain(self):
        self.spool.put(b"\x31\x00", 100.0, 1)
        self.spool.put(b"\x31\x01", 200.0, 1)
        self.assertEqual(self.spool.drain(two_messages, lambda t, m: False, log=lambda _l: None), 0)
        self.assertEqual(len(self.spool.pending()), 2)
        self.assertEqual(self.spool.drain(two_messages, lambda t, m: True, log=lambda _l: None), 2)
        self.assertEqual(self.spool.pending(), [])

    def test_oldest_first_and_the_batch_comes_back_intact(self):
        seen = []
        self.spool.put(b"\x31\x02", 300.0, 7)
        self.spool.put(b"\x31\x01", 200.0, 7)
        self.spool.drain(lambda b, c, s: (seen.append((b, c, s)) or ([], None)), lambda t, m: True,
                         log=lambda _l: None)
        self.assertEqual(seen, [(b"\x31\x01", 200.0, 7), (b"\x31\x02", 300.0, 7)])

    def test_a_refused_batch_is_not_kept_forever(self):
        self.spool.put(b"junk", 100.0, 1)
        self.spool.drain(lambda b, c, s: ([], "no"), lambda t, m: True, log=lambda _l: None)
        self.assertEqual(self.spool.pending(), [])

    def test_an_unreadable_file_is_set_aside(self):
        with open(os.path.join(self.dir.name, "1.000000-00000001.json"), "w") as f:
            f.write("{not json")
        self.spool.drain(two_messages, lambda t, m: True, log=lambda _l: None)
        self.assertEqual(self.spool.pending(), [])
        self.assertTrue(any(n.endswith(".bad") for n in os.listdir(self.dir.name)))


if __name__ == "__main__":
    unittest.main()
