"""Saving a file on Windows retries briefly when another program has it open.

Two tests that save a file each failed once in a full run on Windows and passed when run again
(a reminder schedule, a research report). An antivirus scan or the search indexer holding the file
makes `os.replace` raise PermissionError for a moment, and the save failed the whole turn.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laptop_agent import storage


class ReplaceRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "store.json"
        self.path.write_text('{"old": 1}', encoding="utf-8")

    def busy_then(self, failures: int):
        real = os.replace
        calls = []

        def replace(source, target):
            calls.append(target)
            if len(calls) <= failures:
                raise PermissionError(32, "The process cannot access the file because it is being used")
            return real(source, target)

        return replace, calls

    def test_a_file_busy_for_a_moment_is_still_saved_on_windows(self) -> None:
        replace, calls = self.busy_then(2)
        with (
            patch.object(storage, "RETRY_BUSY_REPLACE", True),
            patch.object(storage.os, "replace", replace),
            patch.object(storage.time, "sleep"),
        ):
            storage.atomic_write_text(self.path, '{"new": 2}', backup=False)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"new": 2}')
        self.assertEqual(len(calls), 3)
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])

    def test_a_file_that_stays_busy_still_fails_and_leaves_no_temporary(self) -> None:
        replace, calls = self.busy_then(100)
        with (
            patch.object(storage, "RETRY_BUSY_REPLACE", True),
            patch.object(storage.os, "replace", replace),
            patch.object(storage.time, "sleep"),
        ):
            with self.assertRaises(PermissionError):
                storage.atomic_write_text(self.path, '{"new": 2}', backup=False)
        self.assertEqual(len(calls), storage.REPLACE_ATTEMPTS)
        self.assertEqual(self.path.read_text(encoding="utf-8"), '{"old": 1}')
        self.assertEqual(list(Path(self._tmp.name).glob("*.tmp")), [])

    def test_elsewhere_a_permission_error_is_not_retried(self) -> None:
        replace, calls = self.busy_then(1)
        with patch.object(storage, "RETRY_BUSY_REPLACE", False), patch.object(storage.os, "replace", replace):
            with self.assertRaises(PermissionError):
                storage.atomic_write_text(self.path, '{"new": 2}', backup=False)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
