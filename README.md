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
- Parallel task dashboard that records `multi` subtask status/results and can retry failed subtasks.
- Agent control room: inspect specialist agents, live working/idle/available counts, and per-agent details.
- Searchable local knowledge base: index text/PDF/DOCX/image/audio/video into a persistent index and recall across it offline with TF-IDF-style ranking.
- ChatGPT-style desktop chat app (`run_desktop`) with Markdown-rendered replies, chat sessions, file upload of any type, drag-and-drop, and a browser voice mode (speech in, speech out) with animated mic states.
- Live system metrics in the app (CPU, RAM, and GPU when available).
- Vision: "look at my screen" and "describe image" use a vision model (OCR is the fallback).
- Three-tier model routing: simple turns use a fast model, complex questions a stronger one, and the hardest a top-tier model — chosen automatically by task complexity.
- Streaming replies in the app: conversational answers appear token-by-token instead of after a long wait, and the model is kept warm to avoid cold-start latency.
- Obsidian vault integration used as durable, human-readable memory: search/read/save notes, and remembered facts are mirrored into the vault.
- Approval-gated web search (DuckDuckGo, dependency-free) returning titles, URLs, and snippets.
- Autonomous `research` workflow: searches the web, fetches and reads the top pages, summarizes, and indexes the findings into the knowledge base.
- `research report <topic>`: a multi-section Markdown brief (overview, key findings, caveats, sources), saveable to a file or the Obsidian vault.
- Multi-agent control room: a live roster of specialist agents with working/idle status, surfaced as a panel in the app.
- Knowledge recall ranked with TF-IDF; summarized documents auto-index for later recall.
- Parallel subtasks with retry/failure recovery.
- Markdown research reports with overview, key findings, caveats, and source links, returned in chat or saved to disk/Obsidian.
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
- Tool-level email attachment support for SMTP plus Gmail/Outlook OAuth draft/send payloads.
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
index file report.pdf
index file lecture.mp4
recall billing invoices
what do I know about kubernetes
knowledge list
knowledge stats
knowledge export reports/knowledge.md
knowledge forget 1
agents
agent files
web search python asyncio tutorial
search the web for best mechanical keyboards
google walrus operator
research the history of jazz
look into rust async runtimes
research report local-first AI agents
save research report local-first AI agents to reports/local-first-ai.md
save research report local-first AI agents to obsidian
multi scan files . ;; summarize file notes.md
multi retry failed
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

Email attachments are supported in the email tool API by passing file paths on
`EmailDraft.attachments`. SMTP sends attach files as MIME parts; Gmail OAuth
draft/send payloads include a raw RFC2822 MIME message; Outlook OAuth draft/send
payloads include Microsoft Graph `fileAttachment` objects. Attachment paths are
validated before approval, previews list attachment names and sizes, and the
combined attachment payload is capped at 20 MB. Default `mailto:` drafts cannot
reliably attach files automatically, so they fail clearly when attachments are
present.

## LLM brain

By default the agent uses an offline heuristic planner that maps common phrasings
to internal commands. Plug in any OpenAI-compatible chat-completions API to make
it a real conversational brain: it then answers free-form questions, holds a
conversation, and routes natural language to the right command.

Put credentials in a local `.env` file (gitignored, loaded automatically), or set
them as environment variables:

```text
LAPTOP_AGENT_LLM_PROVIDER=openai
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

Any OpenAI-compatible endpoint works. For example, NVIDIA's hosted models:

```text
OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
OPENAI_MODEL=nvidia/nemotron-3-ultra-550b-a55b
```

Routing is tiered for low latency: a fast deterministic router handles common
requests instantly with no network call, and the LLM is used only for genuine
conversation or unusual phrasings. Tool results are formatted into plain language
locally (no second model call). A small fast model is recommended — routing stays
reliable through few-shot examples, so a large model is not needed, and a startup
warm-up removes cold-start latency. The brain is given the current directory and
acts on sensible defaults ("the readme" means README.md here) instead of asking
for paths.

Reasoning models are handled: chain-of-thought is ignored and JSON is extracted
even when wrapped in `<think>` blocks or Markdown. If the model replies in prose
instead, that reply is shown as conversation. If any required value is missing or
the model is unreachable, the app falls back to the offline heuristic planner.
The brain can propose draft/preparation commands, but final risky actions still
go through the approval gate, and a planned command never re-triggers planning.

## Obsidian memory

Point the agent at an Obsidian vault folder to use it as durable, human-readable
memory. Set `OBSIDIAN_VAULT` (in `.env` or the environment) to the vault path:

```text
OBSIDIAN_VAULT=F:\obsidian\My Vault
```

Then commands like `notes status`, `notes list`, `notes search <query>`,
`read note <name>`, `save note <title> : <body>`, and `remember note <text>`
read and write Markdown in the vault. Anything you ask the agent to `remember`
is also mirrored into `Agent Memory/Memory log.md`, so it persists across
restarts and is visible/editable inside Obsidian.

## Tiered models

The agent picks a model by how hard the task is — simple turns stay instant and
only the hardest questions pay for the biggest model:

```text
OPENAI_MODEL=meta/llama-3.1-8b-instruct                  # simple turns + routing
OPENAI_SMART_MODEL=nvidia/llama-3.3-nemotron-super-49b-v1 # complex questions
OPENAI_ULTRA_MODEL=nvidia/nemotron-3-ultra-550b-a55b      # very complex / deep
OPENAI_VISION_MODEL=meta/llama-3.2-11b-vision-instruct    # images + screen
```

Each tier is optional and falls back to the next one down. Bigger models get a
longer request timeout (the 550B can take ~45-60s). With `OPENAI_VISION_MODEL`
set, `look at my screen` and `describe image <path>` use the vision model;
otherwise they fall back to OCR (needs the Tesseract binary).

## Security model

High-risk actions require explicit confirmation:

- Sending email.
- Creating mailbox drafts through OAuth APIs.
- Exchanging or deleting mailbox OAuth tokens.
- Submitting forms or applications.
- Writing converted files to disk.
- Moving files when organizing a folder.
- Downloading files.
- Launching apps or opening external URLs.
- Searching the web (the query is sent to an external search engine).
- Reading your inbox (read-only; medium risk, allowed in the guarded web app).
- Researching a topic (one approval covers the search plus fetching several pages).
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
captured image locally. The knowledge base (`index file`, `recall`,
`knowledge list/forget`) stores and searches extracted text in a local JSON
file under `.agent_data/` and never leaves the machine; it is read/write on
your own data only, like the profile memory, so it is not approval-gated. `convert file`
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
  tools/           File, web, web-search, research, browser, desktop, email, music, and transcribe tools.
  app.py           Shared app factory used by CLI and GUI.
  audit.py         JSONL audit logger.
  cli.py           Interactive command-line interface.
  gui.py           Tkinter desktop interface.
  config.py        Runtime paths and settings.
  memory.py        Local JSON profile/preferences store.
  planner/         Natural-language route planners.
  knowledge.py     Persistent searchable index over extracted text.
  safety.py        Approval gate and risk levels.
  tasks.py         In-memory dashboard of parallel task runs.
  voice.py         Optional speech-to-text and text-to-speech adapters.
tests/             Dependency-free unit tests.
```

## Next build milestones

1. Add a local/remote LLM planner behind the orchestrator.
2. Add scheduled/recurring research monitors that append updates to Obsidian.
3. Wire user-facing CLI/browser syntax for email attachments.
4. Persist task/control-room dashboard history across app restarts.
5. Add richer knowledge snippets with highlighted matched terms and source context.
