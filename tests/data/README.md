# `tests/data/` — fixtures shared across languages

## Purpose

Test data that more than one test, or more than one language, must agree on. Keeping it in
one file means a rule cannot be fixed on one side and forgotten on the other.

## How it connects

`speech_cases.json` is read by both halves of the voice loop's tests:

- `tests/test_voice.py` checks the Python side, `voice.clean_for_speech()`, which the server
  uses before text-to-speech.
- `tests/test_browser_regressions.py` checks the JavaScript side, `speakable()` in
  `src/laptop_agent/webui_assets/app.js`, which the page uses before speaking in the browser.

They are two implementations of the same rules and had already drifted once (a leading
"- " bullet was spoken by one and dropped by the other), so both assert against this file.

## Usage

Add a case as `"<markdown the model might write>": "<what should be spoken>"` under
`cases`, then run both:

```powershell
python -B tests/run_tests.py test_voice.py
$env:JARVIS_BROWSER_TESTS="1"; python -B tests/run_tests.py test_browser_regressions.py
```

## Contents

| File | What it does |
|---|---|
| `speech_cases.json` | Markdown → the text that should be read aloud (images, links, code fences and URLs removed, emphasis and headings flattened), with a `_why` note explaining the file. |
| `nonstreamed_speech_cases.json` | Frozen short and long tool replies for the offline fake-TTS latency and completeness check. `repeat` expands the exact reply deterministically. |

Run `python -B tests/measure_nonstreamed_speech.py` for the pre-change one-chunk
measurement. Its browser fake waits `20 + 2 * len(text)` milliseconds for `/api/tts` and
reports first-audio time plus words and characters voiced for hosted and browser paths.
Set `SPEECH_SOURCE_ROOT` to a separate checkout to replay the same corpus against an
older implementation; `SPEECH_EXPECT_SPLIT=0` allows its expected one-chunk result.
