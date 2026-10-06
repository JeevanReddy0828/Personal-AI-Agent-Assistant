# `.github/workflows/` — continuous integration

(This note lives here rather than in `.github/` itself: GitHub shows a `.github/README.md`
as the repository's front page in place of the root `README.md`.)

## Purpose

Runs the test suite on every push and pull request, so a change that breaks something on
another operating system or Python version is caught before it is merged. A pull request
is merged only when every job here is green on its final commit.

## How it connects

`ci.yml` calls the same runner you use locally, `tests/run_tests.py`, so CI and your
machine run identical isolation (no `.env`, no network beyond localhost, no desktop side
effects). It installs nothing the app requires; `tzdata` is a test-only install because
Windows has no time-zone database.

```
 push / pull request
        ├─ unit     × {ubuntu, windows} × {Python 3.11, 3.13}   python -B tests/run_tests.py
        └─ browser    ubuntu, Python 3.13, Playwright Chromium   tests/run_tests.py "test_browser_*.py"
```

## Usage

Nothing to run by hand: open a pull request and the checks appear on it. To reproduce a
failing job locally, run the command from the job, for example
`python -B tests/run_tests.py`. When one job fails, GitHub cancels the others still running
(fail-fast), so a red run may be hiding further failures; rerun after the fix rather than
assuming the first error was the only one.

## Contents

| File | What it does |
|---|---|
| `ci.yml` | The "Tests" workflow: the unit matrix and the browser job described above. |

## Read next

- `tests/README.md` for what the suites cover and how the runner isolates them.
- `AGENTS.md` → "Working together" (merging only on green CI).
