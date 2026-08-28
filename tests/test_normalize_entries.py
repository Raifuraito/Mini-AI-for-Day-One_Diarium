"""
Regression tests for ingest.py's normalize_entries() -- the function that
turns a raw parsed export JSON into this project's internal entry format,
across every supported export shape (Day One, Diarium, and a generic
fallback for anything else).

Why this file exists: two real bugs were found by writing these tests
before this file existed, both around a subtle collision -- Day One's
export and one plausible Diarium export shape both use "entries" as their
top-level container key. Without a shape check beyond "does this key
exist", a non-Day-One export using that same key name would either get
silently misparsed (wrong bug) or crash outright (worse bug). Both are
fixed in normalize_entries() now; these tests exist so a future change
can't reintroduce either one without a test failure catching it.

How to run this:

    cd journal-rag              (the project's root folder)
    python3 -m unittest tests.test_normalize_entries -v

No sample export files, no API key, and no internet connection are needed
-- every test builds a small, fake JSON structure in Python itself and
checks what normalize_entries() does with it. Safe to run any time, costs
nothing, and takes under a second.
"""

import sys
import os
import unittest

# So "import ingest" finds the real ingest.py regardless of which folder
# this test happens to be run from.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import normalize_entries


class TestDayOneFormat(unittest.TestCase):
    """The one export format this project has real, verified support for --
    built and tested against actual Day One exports, not just guessed at."""

    def test_basic_day_one_export(self):
        raw = {
            "entries": [
                {"uuid": "abc123", "text": "Had a great day.", "creationDate": "2024-01-01T12:00:00Z"},
                {"uuid": "def456", "text": "  Trimmed text.  ", "modifiedDate": "2024-01-02T12:00:00Z"},
            ]
        }
        result = normalize_entries(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["id"], "abc123")
        self.assertEqual(result[0]["date"], "2024-01-01T12:00:00Z")
        self.assertEqual(result[0]["text"], "Had a great day.")
        # creationDate missing -> falls back to modifiedDate, not blank.
        self.assertEqual(result[1]["date"], "2024-01-02T12:00:00Z")
        # Leading/trailing whitespace in the entry text gets trimmed.
        self.assertEqual(result[1]["text"], "Trimmed text.")

    def test_empty_day_one_export(self):
        # A real Day One export with zero entries (e.g. a brand new
        # journal) should return cleanly, not error out.
        result = normalize_entries({"entries": []})
        self.assertEqual(result, [])


class TestDiariumFormat(unittest.TestCase):
    """Diarium's export has no published schema anywhere (checked their
    forums, docs, and a third-party decoder of their separate .diary
    backup format -- none document this JSON export specifically, and
    Diarium's own developer has confirmed the app can't even re-import
    its own export). These tests cover several plausible shapes rather
    than betting on one guess, matching how normalize_entries() itself
    is written."""

    def test_capitalized_keys_entries_container(self):
        raw = {
            "Entries": [
                {"Id": "1", "Date": "2024-01-01", "Text": "Diarium entry one."},
                {"Id": "2", "Date": "2024-01-02", "Content": "Uses Content instead of Text."},
            ]
        }
        result = normalize_entries(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["text"], "Diarium entry one.")
        self.assertEqual(result[1]["text"], "Uses Content instead of Text.")

    def test_lowercase_keys_entries_container(self):
        # This is the exact shape that used to collide with the Day One
        # branch (see module docstring) -- a lowercase "entries" key, but
        # WITHOUT a "uuid" field on its items, so it must NOT be treated
        # as Day One.
        raw = {
            "entries": [
                {"id": "3", "date": "2024-01-03", "body": "Lowercase variant entry."},
            ]
        }
        result = normalize_entries(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "Lowercase variant entry.")

    def test_diaryentries_container_with_timestamp_field(self):
        raw = {
            "DiaryEntries": [
                {"ID": "4", "Timestamp": "2024-01-04", "text": "DiaryEntries container variant."},
            ]
        }
        result = normalize_entries(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "DiaryEntries container variant.")
        self.assertEqual(result[0]["date"], "2024-01-04")

    def test_entry_with_no_text_field_at_all(self):
        # Should not crash -- just yield an empty string for that entry's
        # text rather than erroring the whole ingest out.
        raw = {"Entries": [{"Id": "5", "Date": "2024-01-09"}]}
        result = normalize_entries(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "")

    def test_entries_key_with_non_dict_items_does_not_crash(self):
        # A container key matched by name ("entries"), but its items
        # aren't objects at all (e.g. a list of plain strings) -- not a
        # real Diarium export, just a coincidental key-name match. This
        # used to crash with an AttributeError; now it's skipped cleanly
        # and returns an empty list instead of raising.
        result = normalize_entries({"entries": ["just", "strings"]})
        self.assertEqual(result, [])


class TestDayOneDiariumCollision(unittest.TestCase):
    """Both formats can use "entries" as their container key name. These
    tests specifically confirm the two are told apart correctly, since
    getting this wrong either misparses a non-Day-One export using Day
    One's field names (silently wrong data) or crashes."""

    def test_uuid_field_is_what_distinguishes_day_one(self):
        # Only Day One entries carry a "uuid" field -- that's what tells
        # this apart from the same-key-named Diarium variant above.
        day_one_shaped = {"entries": [{"uuid": "x1", "text": "Real Day One entry.", "creationDate": "2024-01-01"}]}
        result = normalize_entries(day_one_shaped)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "x1")
        self.assertEqual(result[0]["text"], "Real Day One entry.")

    def test_malformed_entries_key_falls_through_instead_of_crashing(self):
        # "entries" present but not a list at all (e.g. a typo'd export,
        # or a format this project has never seen) -- must not crash, and
        # must still find a valid Diarium-shaped "Entries" key elsewhere
        # in the same object if one exists.
        raw = {
            "entries": "not a list",
            "Entries": [{"Id": "1", "Text": "fallthrough test"}],
        }
        result = normalize_entries(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "fallthrough test")


class TestGenericFallback(unittest.TestCase):
    """Covers any other journal app's export -- a flat JSON list of
    date/text-shaped objects, which is the shape most non-Day-One,
    non-Diarium exports are likely to already be close to."""

    def test_flat_list_of_entries(self):
        raw = [
            {"date": "2024-01-05", "text": "Generic flat list entry."},
            {"Date": "2024-01-06", "Content": "Generic with capitalized Date/Content."},
        ]
        result = normalize_entries(raw)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["text"], "Generic flat list entry.")
        self.assertEqual(result[1]["text"], "Generic with capitalized Date/Content.")

    def test_non_dict_items_are_skipped_not_crashed_on(self):
        raw = [
            {"date": "2024-01-07", "text": "Valid entry."},
            "not a dict",
            None,
            {"date": "2024-01-08", "text": "Another valid entry."},
        ]
        result = normalize_entries(raw)
        self.assertEqual(len(result), 2)

    def test_empty_list_returns_cleanly(self):
        self.assertEqual(normalize_entries([]), [])


class TestUnrecognizedFormat(unittest.TestCase):
    """When nothing matches, normalize_entries() should fail with a
    specific, diagnosable error -- not a bare "unrecognized format", and
    never an unhandled crash (AttributeError/TypeError/KeyError etc)."""

    def test_dict_with_unknown_keys_raises_helpful_error(self):
        with self.assertRaises(ValueError) as cm:
            normalize_entries({"totally_unknown_key": "value", "another_key": 123})
        message = str(cm.exception)
        self.assertIn("totally_unknown_key", message)
        self.assertIn("GitHub issue", message)

    def test_wrong_top_level_type_raises_helpful_error(self):
        with self.assertRaises(ValueError) as cm:
            normalize_entries(42)
        self.assertIn("int", str(cm.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
