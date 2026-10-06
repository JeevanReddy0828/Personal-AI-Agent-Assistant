# The test runner

What the runner isolates and why a failing run writes test-failures.log.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

Tests: `python -B tests/run_tests.py` (isolated configuration/data). See REVIEW_REPORT.md for current validation results and optional browser checks.

**The runner makes `os.startfile`, `webbrowser.open` and `keybd_event` inert** — they
succeed and do nothing (the music tool also pressed the real volume keys). A URL handed to the OS is fetched by the browser, not by the test process, so the
socket guard never saw it: the routing contract opened a real YouTube video on this laptop
on every run, and a sweep rerunning it 48 times was reported as an automation. A test that
needs to see what was opened still injects its own fake (`test_music.py`,
`test_web_targets.py`). Known limit: on macOS and Linux, `desktop.py` (and `web.py` on
macOS) launch `open`/`xdg-open` through `subprocess`, which this does not touch.

**A failing run writes `test-failures.log` at the repo root** (gitignored by `*.log`,
deleted on the next clean run so a stale report cannot mislead) holding each test id and
traceback plus the interpreter, platform and argv. `TextTestRunner` already prints all of
that — *above* the summary — so it is the first thing lost to `| tail -3`, a scrolled
terminal or a CI log view that keeps only the end, which is how this repo acquired "one
unreproduced test error: `FAILED (errors=1)` with no name captured". The path is printed
as the **last** line, after the summary, so a tail still shows where the detail went.
**The console is not a durable record.** Twelve consecutive local runs of the full unit
suite (36.8–39.9s each) did not reproduce the original error, so it remains unexplained —
the file does not diagnose it, it only guarantees the next one cannot be lost the same
way. If it does recur, `test_approvals.py` is where to look first: its timeouts are 0.2s
and 0.3s against 2.0s joins, which is the shape that only fires on a loaded machine.
