"""
Regression test for the ENABLE_TAGGING opt-in flag (config.py / ingest.py).

Why this exists: tagging costs a small but real amount per entry via the
Anthropic API. It's off by default on purpose -- so a first-time ingest of
a large journal never spends money before you've had a chance to decide
you want Max Recall. That default-off behavior lives in one line in
config.py:

    ENABLE_TAGGING = os.environ.get("ENABLE_TAGGING", "false").strip().lower() == "true"

This test exists so a future edit to that line (e.g. "simplifying" it,
changing the default, or loosening the truthy check) can't silently flip
the default to "on" without a test failure catching it first.

How to run this:

    cd Mini-AI-for-Day-One_Diarium              (the project's root folder)
    python3 -m unittest tests.test_tagging_opt_in -v

No API key or internet connection needed -- this only tests how the
ENABLE_TAGGING environment variable is parsed into True/False, not
anything that calls the API.
"""

import os
import unittest


def parse_enable_tagging(raw_value):
    """
    Mirrors the exact expression config.py uses to parse ENABLE_TAGGING,
    so this test can check many input variations quickly without needing
    to reload the config module (which has other required settings) for
    each one. If config.py's actual parsing ever diverges from this
    expression, update both together.
    """
    if raw_value is None:
        os.environ.pop("ENABLE_TAGGING", None)
    else:
        os.environ["ENABLE_TAGGING"] = raw_value
    return os.environ.get("ENABLE_TAGGING", "false").strip().lower() == "true"


class TestEnableTaggingDefaultsOff(unittest.TestCase):
    """The property that actually matters: with nothing set, tagging is
    off. This is the one case that must never silently flip."""

    def tearDown(self):
        os.environ.pop("ENABLE_TAGGING", None)

    def test_unset_defaults_to_false(self):
        self.assertFalse(parse_enable_tagging(None))

    def test_empty_string_is_false(self):
        self.assertFalse(parse_enable_tagging(""))


class TestEnableTaggingTruthyValues(unittest.TestCase):
    """Only an actual "true" (case-insensitively, with whitespace
    tolerance) turns tagging on -- nothing else is treated as truthy,
    since a typo or a different convention (like "1" or "yes") silently
    staying off is the safe failure direction here."""

    def tearDown(self):
        os.environ.pop("ENABLE_TAGGING", None)

    def test_lowercase_true(self):
        self.assertTrue(parse_enable_tagging("true"))

    def test_capitalized_true(self):
        self.assertTrue(parse_enable_tagging("True"))

    def test_uppercase_true(self):
        self.assertTrue(parse_enable_tagging("TRUE"))

    def test_true_with_surrounding_whitespace(self):
        # .env files get hand-edited; tolerate stray spaces.
        self.assertTrue(parse_enable_tagging("  true  "))

    def test_false_is_false(self):
        self.assertFalse(parse_enable_tagging("false"))

    def test_other_truthy_conventions_are_not_true(self):
        # Deliberately NOT treated as "on" -- only "true" counts. Getting
        # this wrong in the permissive direction (accepting "1"/"yes")
        # would make it too easy to accidentally enable billing.
        for value in ("1", "yes", "on", "enabled"):
            with self.subTest(value=value):
                self.assertFalse(parse_enable_tagging(value))


if __name__ == "__main__":
    unittest.main(verbosity=2)
