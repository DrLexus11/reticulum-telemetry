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

    def test_valid_json_that_is_not_an_object_starts_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "names.json")
            for text in ("[]", "null", "3", '"name"'):
                open(path, "w").write(text)
                self.assertIsNone(board_names.BoardNames(path).name_for("0a0b0c0d"))


    def test_lxmf_display_names_are_read_from_the_announce(self):
        name = board_names.lxmf_display_name
        self.assertEqual(name(b"\x92\xc4\x05Ayse \xc0"), "Ayse")      # [bin "Ayse ", nil]
        self.assertEqual(name(b"\x92\xa4Ayse\x08"), "Ayse")            # [str "Ayse", 8]
        self.assertEqual(name(b"Old client"), "Old client")             # bare name
        self.assertIsNone(name(b"\x92\xc0\xc0"))                       # no name set
        self.assertIsNone(name(b"\x92\xc4\x09short"))                  # cut short
        self.assertIsNone(name(b""))

    def test_a_parser_can_be_given(self):
        names = board_names.BoardNames(parse=board_names.lxmf_display_name)
        self.assertTrue(names.heard(bytes.fromhex("0a0b0c0d") + bytes(12), b"\x92\xc4\x04Ayse\xc0"))
        self.assertEqual(names.name_for("0a0b0c0d"), "Ayse")

if __name__ == "__main__":
    unittest.main()
