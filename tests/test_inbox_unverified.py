"""Batches whose signer is not yet known (gateway/lxmf_inbox.py): kept, retried,
delivered once verifiable, dropped when too old or invalid. RNS and LXMF are
stood in for, so this runs without a mesh."""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import lxmf_inbox  # noqa: E402

KNOWN = set()       # source hashes whose identity the stand-in RNS can recall
BAD = set()         # packed payloads whose signature is invalid


class Message:
    def __init__(self, packed, source=b"S" * 16, content=b"\x31batch"):
        self.packed = packed
        self.source_hash = source
        self.hash = b"H" * 32
        self.content = content
        self.signature_validated = source in KNOWN and packed not in BAD
        self.unverified_reason = None if self.signature_validated else (
            0x02 if packed in BAD else 0x01)


class LXMessage:
    SOURCE_UNKNOWN = 0x01
    SIGNATURE_INVALID = 0x02

    @staticmethod
    def unpack_from_bytes(packed):
        return Message(packed)


class Router:
    def __init__(self, identity=None, storagepath=None):
        pass

    def register_delivery_identity(self, identity, display_name=None):
        return types.SimpleNamespace(hash=b"D" * 16)

    def register_delivery_callback(self, callback):
        pass


lxmf = types.SimpleNamespace(LXMRouter=Router, LXMessage=LXMessage)
paths = []


class Identity:
    hash = bytes.fromhex("0a0b0c0d") + bytes(12)

    @staticmethod
    def recall(source):
        return Identity() if source in KNOWN else None


rns = types.SimpleNamespace(
    Transport=types.SimpleNamespace(register_announce_handler=lambda h: None,
                                    request_path=lambda h: paths.append(h)),
    Identity=Identity,
    hexrep=lambda b, delimit=False: bytes(b).hex())


class Unverified(unittest.TestCase):

    def setUp(self):
        KNOWN.clear()
        BAD.clear()
        paths.clear()
        self.dir = tempfile.TemporaryDirectory()
        self.batches = []
        self.inbox = lxmf_inbox.Inbox(rns, lxmf, object(), self.dir.name, "gw",
                                      lambda b, at, signer: self.batches.append((b, at, signer)),
                                      log=lambda _l: None)

    def tearDown(self):
        self.dir.cleanup()

    def kept(self):
        return [n for n in os.listdir(self.inbox.unverified_dir) if n.endswith(".json")]

    def test_an_unknown_signer_is_kept_and_its_path_requested(self):
        self.inbox._delivered(Message(b"p1"), collected_at=100.0)
        self.assertEqual(self.batches, [])
        self.assertEqual(len(self.kept()), 1)
        self.assertEqual(paths, [b"S" * 16])

    def test_once_known_it_is_delivered_with_its_collection_time(self):
        self.inbox._delivered(Message(b"p1"), collected_at=100.0)
        self.inbox.retry_unverified(now=200.0)
        self.assertEqual(self.batches, [])           # still unknown: kept, path asked again
        self.assertEqual(len(paths), 2)
        KNOWN.add(b"S" * 16)
        self.inbox.retry_unverified(now=300.0)
        self.assertEqual(self.batches, [(b"\x31batch", 100.0, 0x0a0b0c0d)])
        self.assertEqual(self.kept(), [])

    def test_too_old_for_prometheus_it_is_dropped(self):
        self.inbox._delivered(Message(b"p1"), collected_at=100.0)
        self.inbox.retry_unverified(now=100.0 + lxmf_inbox.UNVERIFIED_KEEP_S + 1)
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.batches, [])

    def test_an_invalid_signature_is_dropped_at_once(self):
        BAD.add(b"p2")
        self.inbox._delivered(Message(b"p2"), collected_at=100.0)
        self.assertEqual(self.kept(), [])
        self.assertEqual(self.batches, [])

    def test_a_failed_hand_off_keeps_the_file(self):
        self.inbox._delivered(Message(b"p1"), collected_at=100.0)
        KNOWN.add(b"S" * 16)

        def fail(*_a):
            raise OSError("disk full")
        self.inbox.on_batch = fail
        self.inbox.retry_unverified(now=200.0)
        self.assertEqual(len(self.kept()), 1)
        self.inbox.on_batch = lambda b, at, signer: self.batches.append((b, at, signer))
        self.inbox.retry_unverified(now=300.0)
        self.assertEqual(len(self.batches), 1)
        self.assertEqual(self.kept(), [])

    def test_the_queue_is_bounded_per_signer_and_deduplicated(self):
        for i in range(lxmf_inbox.UNVERIFIED_MAX_PER_SIGNER + 5):
            m = Message(b"p%d" % i)
            m.hash = bytes([i]) + bytes(31)
            self.inbox._delivered(m, collected_at=100.0 + i)
        self.assertEqual(len(self.kept()), lxmf_inbox.UNVERIFIED_MAX_PER_SIGNER)
        again = Message(b"p0")
        again.hash = bytes([0]) + bytes(31)
        other = Message(b"q", source=b"T" * 16)
        self.inbox._delivered(other, collected_at=500.0)
        self.assertEqual(len(self.kept()), lxmf_inbox.UNVERIFIED_MAX_PER_SIGNER + 1)   # another signer still fits

    def test_a_duplicate_is_kept_once(self):
        self.inbox._delivered(Message(b"p1"), collected_at=100.0)
        self.inbox._delivered(Message(b"p1"), collected_at=101.0)
        self.assertEqual(len(self.kept()), 1)

    def test_one_path_request_per_signer_per_pass(self):
        for i in range(3):
            m = Message(b"p%d" % i)
            m.hash = bytes([i]) + bytes(31)
            self.inbox._delivered(m, collected_at=100.0 + i)
        paths.clear()
        self.inbox.retry_unverified(now=200.0)
        self.assertEqual(paths, [b"S" * 16])


if __name__ == "__main__":
    unittest.main()
