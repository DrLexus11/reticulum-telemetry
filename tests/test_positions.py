"""Configured board positions (gateway/positions.py)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "gateway"))

import positions  # noqa: E402


class Positions(unittest.TestCase):

    def load(self, content):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w") as f:
                f.write(content if isinstance(content, str) else json.dumps(content))
            return positions.load(path)

    def test_valid_entries_are_read_and_bad_ones_left_out(self):
        got = self.load({"0a0b0c0d": {"lat": 41.01, "lon": 29.02},
                         "RAD": {"lat": 1, "lon": 2},              # not a sender id
                         "11223344": {"lat": 91, "lon": 0},        # off the globe
                         "55667788": {"lat": "41", "lon": 29},     # not a number
                         "99aabbcc": {"lat": True, "lon": 29},     # a bool is not a number
                         "ddeeff00": [41, 29]})
        self.assertEqual(got, {"0a0b0c0d": {"lat": 41.01, "lon": 29.02}})

    def test_a_missing_or_broken_file_is_empty(self):
        self.assertEqual(positions.load("/nonexistent/positions.json"), {})
        self.assertEqual(self.load("{not json"), {})
        self.assertEqual(self.load("[]"), {})

    def test_the_message_says_where_the_position_came_from(self):
        m = positions.message("0a0b0c0d", {"lat": 41.0, "lon": 29.0}, 100.0, name="RAD-1")
        self.assertEqual((m["source"], m["lat"], m["lon"], m["name"]), ("configured", 41.0, 29.0, "RAD-1"))


    def test_what_was_published_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "published.json")
            self.assertEqual(positions.load_published(path), [])
            positions.save_published(path, ["11223344", "0a0b0c0d"])
            self.assertEqual(positions.load_published(path), ["0a0b0c0d", "11223344"])
            with open(path, "w") as f:
                f.write('["0a0b0c0d", "RAD", 5]')
            self.assertEqual(positions.load_published(path), ["0a0b0c0d"])


if __name__ == "__main__":
    unittest.main()
