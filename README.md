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
- Offline document summarization (extractive, no LLM required) for text, Markdown, and (with extras) PDF/DOCX.
- File metadata/word-count inspection and CSV/TSV/Markdown table extraction.
- Approval-gated document conversion to `.txt`/`.md`.
- Folder organization that previews moves by file type and applies them only after approval.
- Optional image OCR (text extraction from screenshots/photos) via Tesseract.
- Optional offline audio/video transcription via local Whisper.
- Unified `extract text` and `summarize file` that auto-OCR images and transcribe media first.
- `read screen` desktop understanding: capture a screenshot and OCR its text in one step.
- Parallel task dashboard that records `multi` subtask status and results.
- Browser URL opening through the system browser.
- Optional Playwright workflow hooks for browser automation.
- Optional browser form inspection for application pages.
- Job-application review packages that map stored profile values to detected fields.
- Fill-preview packages that show what would be typed into which selector.
- Approved browser fill action for safe mapped text fields, with no submit action.
- Desktop hooks for opening apps and media keys on Windows.
- Email draft generation via `mailto:` and optional SMTP sending.
- IMAP inbox search with explicit approval.
- OAuth authorization URL helpers for Gmail and Outlook app setup.
- OAuth authorization-code exchange with local encrypted token storage on Windows.
- OAuth-backed Gmail/Outlook read/search commands using stored access tokens.
- OAuth-backed Gmail/Outlook draft creation and send commands with final approval.
- Music playback through local files, folders, URLs, or media keys.
- Multi-agent orchestration skeleton for running independent subtasks.
- Local JSON memory for profile/preferences.
- Natural-language planner with offline heuristic routing.
- Optional OpenAI-compatible planner provider.

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
remember my name is Your Name
audit
scan files .
find resume in .
search files resume .
summarize file notes.md
summarize the file report.pdf
file info report.pdf
extract tables data.csv
convert file notes.md to notes.txt
organize folder C:\Users\you\Downloads
organize folder C:\Users\you\Downloads apply
ocr image receipt.png
extract text from screenshot shot.png
transcribe meeting.mp3
transcribe the audio lecture.mp4
extract text contract.pdf
extract text voicememo.m4a
summarize file poster.png
summarize the recording standup.mp3
read screen
multi scan files . ;; summarize file notes.md
tasks
open url https://example.com
open website example.com
inspect forms https://example.com/apply
inspect forms at example.com/apply
preview form fill https://example.com/apply
preview form fill for example.com/apply
fill form https://example.com/apply
fill the form at example.com/apply
email search recruiter
email unread
email oauth status
email oauth url gmail
email oauth url outlook
email oauth exchange gmail <authorization-code>
email oauth refresh gmail
email oauth forget gmail
email tokens status
email api search gmail invoice
email api unread outlook
email api draft gmail to person@example.com subject Hello body Draft only
email api send outlook to person@example.com subject Hello body Send after approval
play music C:\Users\you\Music
email to person@example.com subject Hello body Draft this message only
plan apply job at Example Corp from https://example.com/jobs/123
apply to the job at https://example.com/jobs/123
```

## Optional capabilities

Install extras as needed:

```powershell
pip install -e ".[browser,desktop,docs,voice,ocr,transcribe]"
playwright install chromium
```

`inspect forms <url>`, `preview form fill <url>`, `fill form <url>`, and
job-application field extraction require the browser extra. Without it, the
agent still creates a safe review plan and tells you what to install.

Voice buttons require optional packages. `Speak` uses `pyttsx3`; `Listen` uses
`SpeechRecognition` and may require a microphone backend such as PyAudio.

`ocr image <path>` requires the `ocr` extra plus the Tesseract OCR binary on
PATH. `transcribe <path>` requires the `transcribe` extra (local Whisper) plus
`ffmpeg` on PATH; set `LAPTOP_AGENT_WHISPER_MODEL` to pick a model size (default
`base`). Without these extras, each command returns a clear install hint instead
of failing hard.

## Email setup

Drafts work without account credentials by opening your default mail client.
SMTP sending requires:

```powershell
$env:SMTP_HOST="smtp.example.com"
$env:SMTP_USERNAME="you@example.com"
$env:SMTP_PASSWORD="app-password-or-secret"
```

Inbox search uses IMAP and requires:

```powershell
$env:IMAP_HOST="imap.example.com"
$env:IMAP_USERNAME="you@example.com"
$env:IMAP_PASSWORD="app-password-or-secret"
```

Commands:

```text
email search invoice
email unread
```

For OAuth app setup helpers:

```powershell
$env:GOOGLE_CLIENT_ID="your-google-client-id"
$env:GOOGLE_CLIENT_SECRET="your-google-client-secret"
$env:MICROSOFT_CLIENT_ID="your-microsoft-client-id"
$env:MICROSOFT_CLIENT_SECRET="your-microsoft-client-secret"
```

Then run:

```text
email oauth status
email oauth url gmail
email oauth url outlook
email oauth exchange gmail <authorization-code>
email oauth exchange outlook <authorization-code>
email oauth refresh gmail
email oauth refresh outlook
email tokens status
email oauth forget gmail
```

The OAuth exchange stores token responses in `.agent_data/email_tokens.json`
encrypted with Windows DPAPI. On non-Windows systems the vault reports that
encrypted storage is unavailable and refuses to store tokens.

After storing a token, read-only provider API commands are available:

```text
email api search gmail invoice
email api unread gmail
email api search outlook recruiter
email api unread outlook
email api draft gmail to person@example.com subject Hello body Draft only
email api send outlook to person@example.com subject Hello body Send after approval
```

These commands read message metadata/snippets only and require approval. Token
refresh is explicit and approval-gated:

```text
email oauth refresh gmail
email oauth refresh outlook
```

OAuth draft/send commands also require stored tokens. Draft creation writes a
draft into the mailbox. Send commands require final approval and then send the
message through Gmail or Outlook.

## LLM planner

By default the agent uses an offline heuristic planner. It recognizes common
natural-language requests and converts them into internal safe commands.

To use an OpenAI-compatible chat-completions planner, set:

```powershell
$env:LAPTOP_AGENT_LLM_PROVIDER="openai"
$env:OPENAI_API_KEY="your-key"
$env:OPENAI_MODEL="your-model"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
```

If any required value is missing, the app falls back to the offline heuristic
planner. The planner can propose draft/preparation commands, but final risky
actions still go through the approval gate.

## Security model

High-risk actions require explicit confirmation:

- Sending email.
- Creating mailbox drafts through OAuth APIs.
- Reading inbox metadata or snippets.
- Exchanging or deleting mailbox OAuth tokens.
- Submitting forms or applications.
- Writing converted files to disk.
- Moving files when organizing a folder.
- Downloading files.
- Launching apps or opening external URLs.
- Running browser automation that changes external state.
- Any future shell execution.

Form inspection reads labels, names, placeholders, and required flags. It does
not type, click submit, or send applications.

Fill previews create selector/value review data only. They do not modify the
page.

`fill form <url>` requires approval before inspection and again before typing.
It fills only mapped text-like fields in a headed browser, skips uploads,
checkboxes, radios, selects, and password fields, waits briefly for review, then
closes the browser. It never clicks submit.

Document summarization, `file info`, `extract tables`, `ocr image`, and
`transcribe` are read-only: they only read local files and never change them.
Transcription runs fully offline through a local Whisper model. `summarize file`
and `extract text` reuse the same readers, so they stay read-only even for
images and media. `read screen` first captures a screenshot, which is itself an
approval-gated action because screen contents can be private; it then OCRs the
captured image locally. `convert file`
and `organize folder ... apply` change the
filesystem, so each requires explicit approval and shows a preview first.
`organize folder` without `apply` only previews the planned moves and never
touches your files; the apply step skips any move whose destination already
exists rather than overwriting it.

The CLI approval gate prompts in the terminal. The GUI approval gate shows a
visible modal dialog before continuing.

Audit events are written to `.agent_data/audit.jsonl` by default.

## Project layout

```text
src/laptop_agent/
  agents/          Task router and specialist agent orchestration.
  tools/           File, web, browser, desktop, email, music, and transcribe tools.
  app.py           Shared app factory used by CLI and GUI.
  audit.py         JSONL audit logger.
  cli.py           Interactive command-line interface.
  gui.py           Tkinter desktop interface.
  config.py        Runtime paths and settings.
  memory.py        Local JSON profile/preferences store.
  planner/         Natural-language route planners.
  safety.py        Approval gate and risk levels.
  tasks.py         In-memory dashboard of parallel task runs.
  voice.py         Optional speech-to-text and text-to-speech adapters.
tests/             Dependency-free unit tests.
```

## Next build milestones

1. Add a local/remote LLM planner behind the orchestrator.
2. Add attachment support for email drafts/sends.
3. Persist the task dashboard and surface it in the desktop GUI.
4. Add retry/failure recovery for parallel subtasks.
5. Add a searchable long-term memory/knowledge base over extracted text.
