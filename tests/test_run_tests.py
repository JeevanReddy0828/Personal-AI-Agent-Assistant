from __future__ import annotations

import os
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
    def __init__(self, errors=(), failures=(), unexpected=(), tests_run=7) -> None:
        self.errors = list(errors)
        self.failures = list(failures)
        self.unexpectedSuccesses = list(unexpected)
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

    def test_an_unexpected_success_is_recorded_as_a_reason(self) -> None:
        """`wasSuccessful()` is false when this is non-empty, independently of failures
        and errors. Left out, a run that failed only for this reason wrote
        "0 failure(s)/error(s)" — a report claiming nothing is wrong, for a run that just
        failed, which is worse than no file because it looks authoritative."""
        count, text = self.report(_FakeResult(unexpected=[_FakeTest("case.test_c")]))
        self.assertEqual(count, 1)
        self.assertIn("case.test_c", text)
        self.assertIn("UNEXPECTED SUCCESS", text)

    def test_an_unwritable_report_never_replaces_the_test_result(self) -> None:
        """The report is a convenience. Unguarded, an unwritable path raised out of the
        runner and the traceback pushed the summary and the test names off the end of a
        `tail` — and on the success path turned a green suite into a runner crash."""
        # Both subjects are planted throwaways. Pointing the subprocess at
        # `test_run_tests.py` instead would re-enter *this* test, which spawns another
        # subprocess, forever — it reached 120 processes before being killed.
        here = Path(__file__).resolve().parent
        bodies = {
            "test_probe_boom.py": "import unittest\n\n\n"
                                  "class Boom(unittest.TestCase):\n"
                                  "    def test_raises(self):\n"
                                  "        raise ZeroDivisionError('planted')\n",
            "test_probe_fine.py": "import unittest\n\n\n"
                                  "class Fine(unittest.TestCase):\n"
                                  "    def test_passes(self):\n"
                                  "        self.assertTrue(True)\n",
        }
        for name, body in bodies.items():
            planted = here / name
            planted.write_text(body, encoding="utf-8")
            self.addCleanup(planted.unlink, True)

        blocked = run_tests.FAILURE_REPORT
        if blocked.exists() and blocked.is_file():
            blocked.unlink()
        blocked.mkdir(exist_ok=True)          # a directory cannot be written or unlinked
        self.addCleanup(lambda: blocked.is_dir() and blocked.rmdir())

        for pattern, expect in (("test_probe_boom.py", "FAILED"),
                                ("test_probe_fine.py", "OK")):
            with self.subTest(pattern=pattern):
                done = subprocess.run(
                    [sys.executable, "-B", "tests/run_tests.py", pattern],
                    cwd=run_tests.ROOT, capture_output=True, text=True,
                    env={**os.environ, "PYTHONPATH": "src"},
                )
                out = done.stdout + done.stderr
                self.assertIn(expect, out, "the real test result did not survive")
                self.assertNotIn("Traceback (most recent call last):\n  File", out.split(expect)[-1],
                                 "report IO raised past the result")
                self.assertIn("could not", out, "the reason was swallowed instead of reported")

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
