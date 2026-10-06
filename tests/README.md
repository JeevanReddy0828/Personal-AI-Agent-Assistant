# `tests/` — the unit and browser suites

## Purpose

Proof that the assistant does what it says: about 1,840 unit tests covering routing,
every tool, the stores, the web server, sign-in and the page, plus opt-in browser tests
that drive the real page in Chromium. The tests use only the standard library
(`unittest`), never touch the network or your data, and never act on the desktop.

## How it connects

`run_tests.py` is the entry point, and it isolates everything before any test runs:

- `.env` is not read, and every `OPENAI_*`, `SMTP_*`, `IMAP_*`, `GOOGLE_*`, `OBSIDIAN_*`,
  `LAPTOP_AGENT_*` … variable is removed, so no real key or mailbox is reachable.
- Sockets may only connect to localhost.
- `webbrowser.open`, `os.startfile` and the Windows media keys become no-ops (a test once
  played a real YouTube video and left the laptop's volume at 50%).
- The data directory and uploads go to a temporary folder deleted afterwards.

Tests import the package from `src/` and build the app the same way it runs: most go
through `app.build_context()` with an isolated config and replace only the tools they need
to fake. Network and engine calls are tested through each tool's injectable backend.

## Usage

```powershell
python -B tests/run_tests.py                       # the whole suite, about 5 minutes
python -B tests/run_tests.py test_planner.py       # one file (only the FIRST argument is read)
python -B tests/run_tests.py "test_webui*.py"      # a glob
$env:JARVIS_BROWSER_TESTS="1"; python -B tests/run_tests.py "test_browser_*.py"   # browser suites
```

The browser suites need `pip install playwright pypdf` and
`python -m playwright install chromium`. A failing run also writes `test-failures.log` at
the repository root with every failing test and its traceback (the last line printed says
where), and a clean run deletes it.

Run the **full** suite before pushing changes to the orchestrator, a dispatcher,
`access.py` or `webui_assets/`: their structural guards live in other files. When fixing a
bug, write the test first and watch it fail, then undo the fix again and confirm the test
catches it. CI (`.github/workflows/ci.yml`) runs the same commands on Ubuntu and Windows.

## Contents

About 110 files, one or more per area. The naming tells you where to look:

| Area | Files |
|---|---|
| Runner and repository guards | `run_tests.py`, `test_run_tests.py`, `test_page_integrity.py` (no control characters or mangled escapes in source), `test_folder_readmes.py` (every folder has a README naming each of its code files), `test_agents_md.py`, `test_packaging.py` |
| Routing and conversation | `test_planner.py`, `test_llm_planner.py`, `test_everyday_requests.py` (the routing contract and the never-crash sweeps), `test_selfcheck.py`, `test_conversation_flow.py`, `test_command_dispatch.py`, `test_orchestrator.py`, `test_context.py`, `test_monologue.py` |
| Model tiers and reliability | `test_model_fallback.py`, `test_model_status.py`, `test_openrouter_fallback.py`, `test_failures*.py`, `test_tracing.py`, `test_reliability_regressions.py` |
| Safety and accounts | `test_access.py`, `test_accounts.py`, `test_approvals.py`, `test_security_regressions.py`, `test_lan_access.py`, `test_token_vault.py`, `test_google_*.py`, `test_webui_auth.py` |
| Web server and page | `test_webui*.py` (one per panel or route group), `test_page_assets.py`, `test_webui_caching.py`, `test_window_fx.py` |
| Browser (opt-in) | `test_browser_*.py`: the real page in Chromium, including accounts, reminders, GPU status and the regression screenshots in `docs/review/` |
| Tools | `test_<tool>.py` for nearly every tool: `test_calculator.py`, `test_units_and_dates.py`, `test_clock.py`, `test_weather.py`, `test_travel.py`, `test_news.py`, `test_websearch.py`, `test_research.py`, `test_web_targets.py`, `test_browser_tool.py`, `test_document.py`, `test_write_up_document.py`, `test_deck_courtesy.py`, `test_imagegen*.py`, `test_windows.py`, `test_music*.py`, `test_terminal_tool.py`, `test_email_tool.py`, `test_email_search_phrasing.py`, `test_obsidian.py`, `test_youtube.py`, `test_transcribe.py`, `test_webcam.py`, `test_file_*.py`, `test_forecast*.py`, `test_diagnostics_tool.py` |
| Health | `test_health.py`, `test_setup_report.py`, `test_metrics.py` |
| Stores and memory | `test_knowledge*.py`, `test_embeddings.py`, `test_terms.py`, `test_reminders.py`, `test_reminder_delivery.py`, `test_scheduler*.py`, `test_timeparse.py`, `test_daylight_saving.py`, `test_retention.py`, `test_personal_life.py` |
| Autonomy and jobs | `test_reasoning.py`, `test_autopilot.py`, `test_workflows.py`, `test_tasks.py`, `test_advisor.py`, `test_control_room.py`, `test_jobs.py`, `test_copilot.py`, `test_jobright.py` |
| Analytics | `test_forecast.py`, `test_analytics_diagnostics.py` |
| Voice | `test_voice.py`, `test_webui_voice*.py`, `test_recordings.py`, `test_riva_deadline.py`, `test_tts_engine.py` (the hosted Magpie voice and its offline fallback), `test_spoken_*.py` |
| `data/` | Shared fixtures; see its README. |

`test_everyday_requests.py` also defines `Everyday`, a harness that wires the whole app with
every outside effect faked; reuse it to probe how a sentence is handled end to end.

## Read next

- `docs/design/testing.md` → "The runner makes `os.startfile` … inert" and "A failing run
  writes `test-failures.log`".
- `AGENTS.md` → "Testing and verifying".
