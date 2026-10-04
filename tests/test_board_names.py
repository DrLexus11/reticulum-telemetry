import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gateway"))
import board_names  # noqa: E402

HASH = bytes.fromhex("0a0b0c0d") + bytes(12)


class BoardNamesTests(unittest.TestCase):
    def test_the_sender_id_is_the_first_four_bytes_of_the_identity_hash(self):
        self.assertEqual("0a0b0c0d", board_names.sender_of(HASH))

    def test_a_name_is_learned_and_kept_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "names.json")
            names = board_names.BoardNames(path)
            self.assertTrue(names.heard(HASH, b"board-1"))
            self.assertFalse(names.heard(HASH, b"board-1"))     # unchanged
            self.assertEqual("board-1", board_names.BoardNames(path).name_for("0a0b0c0d"))

    def test_a_rename_replaces_the_old_name(self):
        names = board_names.BoardNames()
        names.heard(HASH, b"board-1")
        self.assertTrue(names.heard(HASH, b"board-2"))
        self.assertEqual("board-2", names.name_for("0a0b0c0d"))

    def test_unusable_app_data_names_nothing(self):
        names = board_names.BoardNames()
        for data in (None, b"", b"\xff\xfe", b"\x00\x01"):
            self.assertFalse(names.heard(HASH, data))
        self.assertIsNone(names.name_for("0a0b0c0d"))

    def test_a_long_name_is_cut(self):
        names = board_names.BoardNames()
        names.heard(HASH, b"x" * 200)
        self.assertEqual(board_names.NAME_MAX, len(names.name_for("0a0b0c0d")))

    def test_a_corrupt_file_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "names.json")
            open(path, "w").write("{not json")
            self.assertIsNone(board_names.BoardNames(path).name_for("0a0b0c0d"))


if __name__ == "__main__":
    unittest.main()
