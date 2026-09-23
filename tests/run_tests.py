"""Run the unit suite without reading .env, contacting services, or using personal data."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
sys.dont_write_bytecode = True

# Already covered by the `*.log` rule in .gitignore.
FAILURE_REPORT = ROOT / "test-failures.log"


def write_failure_report(result, path: Path, argv: list[str] | None = None) -> int:
    """Persist every failure and error with its traceback, and say where.

    `TextTestRunner` prints all of this — *above* the summary line, which makes it the
    first thing lost to `| tail -3`, to a scrolled-back terminal, and to a CI log view
    that keeps only the end. A run here once reported `FAILED (errors=1)` with no name
    captured and was never reproduced; a whole class of that is just truncation, and a
    file survives truncation.

    Returns the number of entries written, so the caller can name the count on the last
    line of output, where a tail will still show it.
    """
    entries = [("ERROR", test, trace) for test, trace in getattr(result, "errors", [])]
    entries += [("FAIL", test, trace) for test, trace in getattr(result, "failures", [])]
    # `wasSuccessful()` is false when this is non-empty, independently of the other two.
    # Without it a run that failed *only* because an `expectedFailure` started passing
    # would write "0 failure(s)/error(s)" — a report claiming nothing is wrong, for a run
    # that just failed, which is worse than no file because it looks authoritative.
    entries += [("UNEXPECTED SUCCESS", test, "This test is marked expectedFailure and passed.")
                for test in getattr(result, "unexpectedSuccesses", [])]
    lines = [
        f"{len(entries)} failure(s)/error(s)",
        f"python   {sys.version.split()[0]} on {sys.platform}",
        f"argv     {' '.join(argv or sys.argv)}",
        f"ran      {getattr(result, 'testsRun', '?')} tests",
        "",
    ]
    for kind, test, trace in entries:
        lines += [f"=== {kind}: {test} ===", trace.rstrip(), ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(entries)


def main() -> int:
    import laptop_agent.config as config

    config._load_dotenv = lambda *a, **k: None
    for key in list(os.environ):
        if key.startswith(("OPENAI_", "OPENROUTER_", "SMTP_", "IMAP_", "GOOGLE_", "MICROSOFT_", "JOBRIGHT_", "SEARCH_", "BRAVE_", "SERPER_", "SERPAPI_", "OBSIDIAN_", "LAPTOP_AGENT_")):
            os.environ.pop(key, None)
    os.environ["LAPTOP_AGENT_LLM_PROVIDER"] = "heuristic"
    connect = socket.socket.connect

    def local_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise OSError("External connections are disabled by the test runner")
        return connect(sock, address)

    socket.socket.connect = local_only
    with tempfile.TemporaryDirectory(prefix="jarvis_tests_") as scratch:
        try:
            os.chdir(scratch)
            os.environ["LAPTOP_AGENT_DATA_DIR"] = str(Path(scratch) / "data")
            import laptop_agent.webui as webui

            webui.UPLOAD_DIR = Path(scratch) / "uploads"
            pattern = sys.argv[1] if len(sys.argv) > 1 else "test_*.py"
            suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
            result = unittest.TextTestRunner(verbosity=1).run(suite)
            # The report is a convenience and must never outrank the result it describes.
            # Unguarded, an unwritable path (read-only checkout, full disk) raised out of
            # here and the pathlib traceback pushed the summary and the test names off the
            # end of a `| tail` — the aid against lost diagnostics losing the diagnosis.
            # On the success path it was worse: a green suite reported as a runner crash,
            # because housekeeping sat on the critical path of the return.
            if result.wasSuccessful():
                try:
                    # A stale report from an earlier run is worse than none: it describes
                    # a failure that no longer exists, in a file nothing else clears.
                    FAILURE_REPORT.unlink(missing_ok=True)
                except OSError as exc:
                    print(f"note: could not clear {FAILURE_REPORT}: {exc}")
                return 0
            try:
                count = write_failure_report(result, FAILURE_REPORT, sys.argv)
                # Last line on purpose — after the summary, so a `| tail` still shows it.
                print(f"\n{count} failure(s)/error(s) written to {FAILURE_REPORT}")
            except OSError as exc:
                print(f"\nnote: could not write {FAILURE_REPORT}: {exc}"
                      f"\nThe failures are above, in the runner's own output.")
            return 1
        finally:
            os.chdir(ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
