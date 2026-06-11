# Laptop Agent

A local-first personal agent scaffold inspired by voice assistants: chat/task routing,
safe laptop tools, browser automation hooks, file intelligence, email drafting, and
multi-agent task execution.

This is an MVP foundation, not an unrestricted autopilot. Anything that can leak
data, send messages, submit forms, run commands, or download files goes through an
approval gate.

## What works now

- Interactive CLI with task routing.
- Tkinter desktop UI with command input, approval dialogs, audit viewer, and voice buttons.
- Safety approval gate for risky actions.
- Durable JSONL audit log for approval decisions.
- File scanning, reading, and text search.
- Browser URL opening through the system browser.
- Optional Playwright workflow hooks for browser automation.
- Desktop hooks for opening apps and media keys on Windows.
- Email draft generation via `mailto:` and optional SMTP sending.
- Music playback through local files, folders, URLs, or media keys.
- Multi-agent orchestration skeleton for running independent subtasks.
- Local JSON memory for profile/preferences.

## Quick start

```powershell
python -m laptop_agent.cli
```

If Python cannot find the package without installation, run:

```powershell
$env:PYTHONPATH="src"
python -m laptop_agent.cli
```

For the desktop UI:

```powershell
$env:PYTHONPATH="src"
python -m laptop_agent.gui
```

Try commands like:

```text
help
remember name = Your Name
audit
scan files .
search files resume .
open url https://example.com
play music C:\Users\you\Music
email to person@example.com subject Hello body Draft this message only
plan apply job at Example Corp from https://example.com/jobs/123
```

## Optional capabilities

Install extras as needed:

```powershell
pip install -e ".[browser,desktop,docs,voice]"
playwright install chromium
```

Voice buttons require optional packages. `Speak` uses `pyttsx3`; `Listen` uses
`SpeechRecognition` and may require a microphone backend such as PyAudio.

## Security model

High-risk actions require explicit confirmation:

- Sending email.
- Submitting forms or applications.
- Downloading files.
- Launching apps or opening external URLs.
- Running browser automation that changes external state.
- Any future shell execution.

The CLI approval gate prompts in the terminal. The GUI approval gate shows a
visible modal dialog before continuing.

Audit events are written to `.agent_data/audit.jsonl` by default.

## Project layout

```text
src/laptop_agent/
  agents/          Task router and specialist agent orchestration.
  tools/           File, web, browser, desktop, email, and music tools.
  app.py           Shared app factory used by CLI and GUI.
  audit.py         JSONL audit logger.
  cli.py           Interactive command-line interface.
  gui.py           Tkinter desktop interface.
  config.py        Runtime paths and settings.
  memory.py        Local JSON profile/preferences store.
  safety.py        Approval gate and risk levels.
  voice.py         Optional speech-to-text and text-to-speech adapters.
tests/             Dependency-free unit tests.
```

## Next build milestones

1. Add a local/remote LLM planner behind the orchestrator.
2. Add real Playwright form filling with field review before submit.
3. Add Gmail/Outlook OAuth integrations.
4. Add document OCR and media transcription.
5. Add stronger desktop screen understanding with OCR.
6. Add a task dashboard for parallel agent progress.
