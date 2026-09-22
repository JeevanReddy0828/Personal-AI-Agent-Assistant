from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import run_tests


class _FakeTest:
    def __init__(self, name: str) -> None:
        self.name = name

    def __str__(self) -> str:
        return self.name


class _FakeResult:
    def __init__(self, errors=(), failures=(), tests_run=7) -> None:
        self.errors = list(errors)
        self.failures = list(failures)
        self.testsRun = tests_run


class FailureReportTests(unittest.TestCase):
    """A run once reported `FAILED (errors=1)` with no name captured, and was never
    reproduced. `TextTestRunner` does print the name and traceback — above the summary,
    which is exactly what `| tail -3`, a scrolled terminal and a truncated CI log throw
    away first. The console is not a durable record; a file is."""

    def report(self, result) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "test-failures.log"
            count = run_tests.write_failure_report(result, path, argv=["run_tests.py", "test_x.py"])
            return count, path.read_text(encoding="utf-8")

    def test_an_error_keeps_its_name_and_traceback(self) -> None:
        result = _FakeResult(errors=[(_FakeTest("test_thing (pkg.Case.test_thing)"),
                                      "Traceback (most recent call last):\n  ZeroDivisionError: x")])
        count, text = self.report(result)
        self.assertEqual(count, 1)
        self.assertIn("test_thing (pkg.Case.test_thing)", text)
        self.assertIn("ZeroDivisionError", text)
        self.assertIn("ERROR", text)

    def test_failures_and_errors_are_both_recorded(self) -> None:
        result = _FakeResult(
            errors=[(_FakeTest("case.test_a"), "boom")],
            failures=[(_FakeTest("case.test_b"), "AssertionError: nope")],
        )
        count, text = self.report(result)
        self.assertEqual(count, 2)
        self.assertIn("case.test_a", text)
        self.assertIn("case.test_b", text)
        self.assertIn("AssertionError: nope", text)

    def test_the_report_names_the_environment_the_run_happened_in(self) -> None:
        """The unreproduced error may well be platform- or version-specific, so the
        report has to say which interpreter and OS produced it — by the time anyone
        reads the file, the shell that ran it is gone."""
        count, text = self.report(_FakeResult(errors=[(_FakeTest("t"), "tb")]))
        self.assertEqual(count, 1)
        self.assertIn(sys.version.split()[0], text)
        self.assertIn(sys.platform, text)
        self.assertIn("test_x.py", text, "the report must say what was being run")
        self.assertIn("7 tests", text)

    def test_a_clean_result_writes_an_empty_report(self) -> None:
        count, text = self.report(_FakeResult())
        self.assertEqual(count, 0)
        self.assertIn("0 failure(s)/error(s)", text)

    def test_the_report_path_is_not_committed_by_accident(self) -> None:
        """It lands at the repo root, which is only safe because `*.log` is ignored."""
        self.assertEqual(run_tests.FAILURE_REPORT.parent, run_tests.ROOT)
        ignored = subprocess.run(
            ["git", "check-ignore", str(run_tests.FAILURE_REPORT)],
            cwd=run_tests.ROOT, capture_output=True, text=True,
        )
        self.assertEqual(ignored.returncode, 0,
                         "test-failures.log is not gitignored; it would be committed")


if __name__ == "__main__":
    unittest.main()
