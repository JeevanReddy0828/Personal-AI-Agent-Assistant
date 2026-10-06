# `docs/review/` — review evidence for UI changes

## Purpose

Evidence that a change to the web page looks and behaves right: full-page screenshots at a
desktop and a phone size, and short notes from reviews of specific UI fixes.

## How it connects

`tests/test_browser_regressions.py` (an opt-in browser suite) renders the page in headless
Chromium and **rewrites `desktop.png` and `mobile.png` on every run**. Review notes are
written by hand when a UI fix needs its validation recorded.

## Usage

```powershell
$env:JARVIS_BROWSER_TESTS="1"; python -B tests/run_tests.py test_browser_regressions.py
```

Because every browser run rewrites the two images, discard those changes unless a pull
request is meant to update the evidence:

```powershell
git checkout -- docs/review
```

## Contents

| File | What it is |
|---|---|
| `desktop.png` | The page at a desktop viewport, from the last browser regression run that was committed. |
| `mobile.png` | The same at a phone viewport. |
| `gpu-status-followups.md` | Review note (2026-10-02): how GPU adapter names are shown in the status drawer, and how it was validated. |

## Read next

- `docs/design/watch-outs.md` (the note about discarding these images) and
  `docs/design/web-ui.md`.
