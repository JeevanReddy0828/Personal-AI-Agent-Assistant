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
- Daily/status briefing that combines due reminders, task history, knowledge stats, agent status, and system metrics.
- File scanning, reading, and text search.
- Offline document summarization (extractive, no LLM required) for text, Markdown, and (with extras) PDF/DOCX.
- Offline question answering over a specific file with supporting excerpts.
- File metadata/word-count inspection and CSV/TSV/Markdown table extraction.
- Approval-gated document conversion to `.txt`/`.md`.
- Folder organization that previews moves by file type and applies them only after approval.
- Optional image OCR (text extraction from screenshots/photos) via Tesseract.
- Optional offline audio/video transcription via local Whisper.
- Unified `extract text` and `summarize file` that auto-OCR images and transcribe media first.
- `read screen` desktop understanding: capture a screenshot and OCR its text in one step.
- Persistent parallel task dashboard that records `multi` subtask status/results and can retry failed subtasks across app restarts.
- Persistent sequential workflows: run multi-step plans across tools, stop on first failure, and retry from the failed step.
- Autopilot mode: plans and runs unattended safe local/read-only work, while blocking commands that need supervision or approval.
- Autonomous agent mode (`agent run <goal>`): an LLM-driven plan → act → observe → replan loop that pursues a goal across many tools, deciding the next command from what it just observed. Unlike autopilot it can genuinely act — risky steps still pass through the approval gate — and every run is persisted (`agent runs`, `agent last`). In the desktop app, a 🤖 toggle turns the composer into agent mode and streams the live step-by-step trace (each thought, command, and observation) as it runs. Speak naturally — the agent translates intent into the right actions, so you rarely need to remember commands.
- Live-news grounding: time-sensitive questions ("did the Iran war end?", "latest on X", "who won the 2026 …", a recent year) automatically trigger a web search, and the answer is synthesized from those live results with inline citations and a Sources list — instead of relying on stale model knowledge. If live search is unavailable, the reply says so rather than sounding confidently out of date.
- Pluggable web search: DuckDuckGo by default (no key), or a real search API (Brave / Serper) when a key is configured — with automatic DuckDuckGo fallback if the API errors. Shared by web search, news grounding, and research.
- Recurring scheduler: `schedule <when> :: <command>` and `schedule agent <when> :: <goal>` run commands or autonomous agent goals on a recurring basis (`every 30 minutes`, `every 2 hours`, `hourly`, `daily at 08:00`). Jobs persist across restarts; a background ticker fires due jobs each minute while the app runs (`schedule list`, `schedule remove <id>`, `schedule run due`).
- Persistent local reminders: add dated reminders, list active/upcoming items, show due items, and mark them complete.
- Agent control room: inspect specialist agents, live working/idle/available counts, and per-agent details.
- Searchable local knowledge base: index text/PDF/DOCX/image/audio/video into a persistent index and recall across it offline with TF-IDF-style ranking.
- ChatGPT-style desktop chat app (`run_desktop`) with Markdown-rendered replies, chat sessions, file upload of any type, drag-and-drop, and a browser voice mode (speech in, speech out) with animated mic states.
- Live system metrics in the app (CPU, RAM, and GPU when available).
- Vision: "look at my screen", "describe image", and "look at webcam" use a vision model (OCR is the fallback). Webcam capture is an optional extra (`pip install laptop-agent[vision]`, OpenCV) with a graceful install hint when absent.
- Three-tier model routing: simple turns use a fast model, complex questions a stronger one, and the hardest a top-tier model — chosen automatically by task complexity. If a higher tier is busy or unreachable, it falls back to the next tier down (and, with an optional OpenRouter key, to a free cross-provider model when the whole primary provider is throttled), tells you it answered with the faster/backup model, and flags the busy tier in the health pill.
- Streaming replies in the app: conversational answers appear token-by-token instead of after a long wait, and the model is kept warm to avoid cold-start latency.
- Live health/status: a self-check (`/api/health`) surfaces whether the AI, vault, and email are connected and reachable, with first-run setup guidance when the AI is not configured.
- Stop a running generation mid-stream (Esc or the stop button) and keyboard shortcuts (Ctrl+K new chat).
- Universal file processor: `process file <path>` auto-detects the type and runs the best action (spreadsheet → stats, doc → summary, image → OCR, audio/video → transcript); plus `analyze spreadsheet <path>` for per-column CSV/TSV stats.
- Real weather: `weather <location>` returns actual current conditions + a 3-day forecast from Open-Meteo (free, no API key) — not a web-search link.
- Travel & maps (free, no key): `distance <a> to <b>` gives real driving miles + ETA (OSRM); `trip <a> to <b> to <c> …` chains a multi-stop route with per-leg + total miles/time; `around <category>` and `<category> near me` find real places around your IP-derived location; `hotels near <place>` / `nearby <category> near <place>` list real places with distances (OpenStreetMap); `where am i` returns your approximate location. The web app's **Map panel** plots any place or `A to B` route on an embedded OpenStreetMap with a directions link. Flight queries route through web search.
- YouTube summarizer: `summarize youtube <url>` fetches the transcript, summarizes it (TL;DR + key points), and indexes it so you can ask follow-up questions about the video (`youtube` extra).
- Real-time voice loop in the web app: speaks each sentence as it streams (low latency), with a violet "listening" theme shift instead of a written overlay.
- Native desktop app: a true window (pywebview, no browser chrome) packaged into a standalone `JARVIS.exe` via PyInstaller. In the native window, speech is handled server-side (`/api/transcribe` + `/api/tts`).
- Lightweight or accurate speech-to-text: choose **Vosk** (~50 MB, no PyTorch/ffmpeg) for a tiny download or **Whisper** for accuracy, via `LAPTOP_AGENT_STT` (`auto` by default).
- Web-UI panels: a Scheduled-jobs panel (view/add/remove/enable), an Agent-runs history panel, a Map panel (plot a place or `A to B` route on OpenStreetMap), a memory-vault browser (search your Obsidian notes, open them in a rendered preview, and click through wiki-link backlinks/outlinks), and a Trip planner (add/reorder stops, see per-leg miles + ETA and totals, and view the chained route drawn inline with a one-click "open full route on OpenStreetMap" link), alongside the control room and live metrics.
- Holographic HUD redesign: a reactive 3D particle-sphere core that energizes, expands, and runs a scanning sweep while the agent works.
- Adaptive HUD controls (header gear): a transparency slider for a see-through window, a compact layout toggle (chat-only HUD), and an always-on-top pin — settings persist. Real window effects apply in the desktop app (Windows); a browser gets the visual fade.
- Obsidian vault integration used as durable, human-readable memory: search/read/save notes, and remembered facts are mirrored into the vault. Retrieval follows Obsidian best practices — search weights titles, aliases, and frontmatter summaries above raw body text; `ask vault <question>` answers from a note *plus its linked neighbours* (link-aware); and `notes audit` flags orphans, broken links, and notes missing a summary.
- Approval-gated web search (DuckDuckGo, dependency-free) returning titles, URLs, and snippets.
- Autonomous `research` workflow: searches the web, fetches and reads the top pages, summarizes, and indexes the findings into the knowledge base.
- `research report <topic>`: a multi-section Markdown brief (overview, key findings, caveats, sources), saveable to a file or the Obsidian vault.
- Problem-solver / advisor: `solve <problem or decision>` researches the question, lays out 2–4 options with pros/cons/risks/effort, commits to a recommendation, and gives a concrete action plan — grounded in live web context and indexed for recall. You don't have to type the command: the assistant's brain automatically routes decision/problem questions ("I'm torn between two offers…", "how should I handle…") to it. Uses the strongest available model with automatic fallback when a tier is busy.
- Multi-agent control room: a live roster of specialist agents with working/idle status, surfaced as a panel in the app.
- Knowledge recall ranked with TF-IDF; summarized documents auto-index for later recall.
- Knowledge question answering synthesizes answers from indexed local documents with source excerpts.
- Parallel subtasks with retry/failure recovery.
- Markdown research reports with overview, key findings, caveats, and source links, returned in chat or saved to disk/Obsidian.
- Browser URL opening through the system browser.
- Optional Playwright workflow hooks for browser automation.
- Optional browser form inspection for application pages.
- Job-application review packages that map stored profile values to detected fields.
- Fill-preview packages that show what would be typed into which selector.
- Approved browser fill action for safe mapped text fields, with no submit action.
- Desktop hooks for opening apps and media keys on Windows.
- Approval-gated terminal command execution with timeout and captured output.
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
briefing
reminder add 2026-06-20 09:00 Call Alex
remind me to call Alex at 2026-06-20 09:00
reminders
reminders due
reminder done 1
scan files .
find resume in .
search files resume .
summarize file notes.md
summarize the file report.pdf
ask file report.pdf about the migration risks
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
ask knowledge what changed about billing invoices
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
workflow scan files . ;; summarize file README.md ;; tasks
workflow status
workflow retry failed
autopilot daily briefing
autopilot project health
autopilot workflow briefing ;; tasks ;; run command dir
autopilot status
agent run summarize README.md and index it into the knowledge base
agent runs
agent last
look at webcam
look at webcam what am I holding?
schedule daily at 08:00 :: briefing
schedule agent every 2 hours :: triage my unread email
schedule list
schedule run due
open url https://example.com
open website example.com
run command dir
run command in C:\Users\you\Projects :: git status --short
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
pip install -e ".[browser,desktop,docs,voice,ocr,transcribe,vision]"
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

## Web search backend

Web search, live-news grounding, and `research` use DuckDuckGo by default (zero
config, but the free endpoint occasionally rate-limits). For reliable results,
add a real search API key — the agent uses it and automatically falls back to
DuckDuckGo if the API errors or returns nothing:

```text
SEARCH_PROVIDER=serpapi        # or: brave / serper  (inferred if you only set a key below)
SEARCH_API_KEY=your-key        # or BRAVE_API_KEY / SERPER_API_KEY / SERPAPI_API_KEY
```

Supported providers: [Brave Search API](https://api.search.brave.com/),
[Serper.dev](https://serper.dev/), and [SerpApi](https://serpapi.com/) — the last
two return Google results (note: Serper.dev and SerpApi are different services).
No key → DuckDuckGo.

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
OPENROUTER_API_KEY=                                       # optional cross-provider fallback
```

Each tier is optional and falls back to the next one down — both by configuration
(a missing tier is skipped) and at runtime: if the chosen tier is congested or
unreachable for a given turn, the answer comes from the next tier down, is tagged
as degraded (a brief "answered with my faster model" note), and the busy tier shows
up in `/api/health` (`llm.tiers`) and the web status pill. If you add a free
[OpenRouter](https://openrouter.ai/keys) key (`OPENROUTER_API_KEY`), it's used as a
last-resort **cross-provider** fallback when every primary tier is throttled, so
chat keeps answering (tagged "answered with a backup model"). Bigger models get a
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
- Running terminal/shell commands.

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

`run command <command>` and `run command in <cwd> :: <command>` require critical
approval every time. Commands run synchronously with a timeout and captured
stdout/stderr; they do not open an interactive shell or background service.

`autopilot <goal>` is intentionally limited to safe local/read-only commands.
If a planned step would send data, write files, open external URLs, run shell
commands, or otherwise need approval, autopilot records it as blocked instead
of waiting for confirmation.

`agent run <goal>` is the autonomous counterpart: the LLM picks one command at a
time from the agent's command set, runs it, reads the result, and decides the
next move, up to a step cap. It is *not* limited to the safe allowlist — risky
steps still go through the approval gate (and are auto-denied in the guarded web
UI), so it can do real work while staying safe. Requires a configured LLM; with
only the offline heuristic router it reports that no reasoning model is available.

The CLI approval gate prompts in the terminal. The GUI approval gate shows a
visible modal dialog before continuing.

Audit events are written to `.agent_data/audit.jsonl` by default.

## Project layout

```text
src/laptop_agent/
  agents/          Task router and specialist agent orchestration.
  tools/           File, file_processor, web, web-search, research, browser, desktop, email, music,
                   weather, travel (maps), youtube, transcribe (OCR + Vosk/Whisper STT), and webcam tools.
  app.py           Shared app factory used by CLI and GUI.
  audit.py         JSONL audit logger.
  cli.py           Interactive command-line interface.
  gui.py           Tkinter desktop interface.
  config.py        Runtime paths and settings.
  autopilot.py     Unattended safe-work planner and run history.
  reasoning.py     Autonomous agent loop (plan/act/observe/replan) and run history.
  scheduler.py     Recurring job store (interval / daily) for commands and agent goals.
  memory.py        Local JSON profile/preferences store.
  planner/         Natural-language route planners.
  knowledge.py     Persistent searchable index over extracted text.
  safety.py        Approval gate and risk levels.
  tasks.py         Persistent dashboard of parallel task runs.
  workflows.py     Persistent sequential workflow run history.
  reminders.py     Persistent local reminder store.
  voice.py         Optional speech-to-text and text-to-speech adapters.
tests/             Dependency-free unit tests.
```

## Next build milestones

1. Add a local/remote LLM planner behind the orchestrator.
2. Add scheduled/recurring research monitors that append updates to Obsidian.
3. Wire user-facing CLI/browser syntax for email attachments.
4. Persist control-room dashboard history across app restarts.
5. Add richer knowledge snippets with highlighted matched terms and source context.
