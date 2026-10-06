# `tools/` — the things the assistant can actually do

## Purpose

Each module here is one capability: read a file, search the web, arrange windows, draw a
picture, convert units, send an email. (Reminders, timers, lists and facts are stores in
the package folder above, not tools.) A tool does the work
and reports back; it never decides what a sentence meant (that is `agents/` and
`planner/`).

Three rules every tool follows:

1. **It returns a `ToolResult`** (`base.py`): `ok`, a human-readable `message`, and `data`
   for the page or the next step.
2. **Anything risky asks first.** Writing or moving files, downloads, shell commands,
   opening apps, sending mail and browser actions call `safety.ApprovalGate.require(...)`
   with a `RiskLevel`. Reads are LOW, network reads MEDIUM, changes HIGH or CRITICAL; in the
   web app HIGH and CRITICAL show an approval card.
3. **Network and engines sit behind an injectable backend** (a function passed to the
   constructor), so the success path is tested offline. A missing optional package returns
   `ToolResult.failure` with the install command instead of crashing.

## How it connects

```
 agents/orchestrator.py ── _dispatch_* group matches "read file …", "weather boston", …
        │  calls self.context.<tool>.<method>(…)     (tools wired in app.build_context)
        │  or a tool built on first use              (weather, travel, news, YouTube)
        ▼
 tools/<module>.py ── optional: safety.ApprovalGate → approvals card in the web app
        │           ── optional: backend (HTTP, Playwright, OCR, speech engine, ctypes)
        ▼
 ToolResult ── turned into a reply by the orchestrator; data drawn by the page
```

Some tools are also building blocks for others: `calculator` supplies the spoken-number
grammar the router uses for volume levels, `windows` supplies the position words,
`files.read_rows` is shared by `forecast` and `analyze spreadsheet`, and `research`'s page
reader is used by `news`.

## Usage

Talk to the assistant, or type the command form. Typing `help` lists all of them. Common ones:

```
read file <path>                 summarize file <path>         ask file <path> about <question>
scan files <path> [by size]      search files <query> <path>   process file <path>
web search <query>               research <topic>              news [topic]
weather <place>                  distance <A> to <B>           map <place|A to B>
calculate <expression>           convert <n> <unit> to <unit>  how many days until <date>
image <description>              document <request> [as pdf|word|markdown]
window <name> <position>         play music <file|folder|url>  media volume <0-100>
email unread | email digest      run command <command>         read screen [question]
forecast <column> in <file.csv>  what drives <column> in <file.csv>
```

**Adding a tool:** write the module with a class whose methods return `ToolResult`, take
network/engine access as an injectable backend, and gate anything risky. Add it to
`AgentContext` and to `app.build_context()` (that one place only), dispatch its command in
the orchestrator, give it an instant route in `planner/heuristic.py` if people will say it
in words, and add a contract row in `selfcheck.py`. Then `tests/test_<tool>.py`.

## Contents

### Foundation
| File | What it does |
|---|---|
| `base.py` | `ToolResult` (`success` / `failure`) and `reserve_new_path` for naming generated files without overwriting. |

### Files and documents
| File | What it does |
|---|---|
| `files.py` | Scan, read, search, summarize and ask questions of files; spreadsheet stats; the shared CSV reader and number parser. |
| `file_processor.py` | `process file <path>`: detects the type and picks the right action. |
| `document.py` | `document …`: the model writes Markdown, rendered to PDF, Word, PowerPoint or Markdown. |
| `resume_pdf.py` | Renders HTML (resumes, documents) to PDF with the browser extra's Chromium, offline. |
| `textcard.py` | Renders exact text as an image, because a diffusion model cannot spell. |
| `forecast.py` | `forecast <column> in <file.csv>`: turns a CSV column into a clean series for `analytics/forecast.py`. |
| `diagnostics.py` | `what drives …` / `anomalies in …`: the CSV side of `analytics/diagnostics.py`. |

### Web, research and information
| File | What it does |
|---|---|
| `web.py` | `open url`, `download`: validates the target, then opens or fetches it (asks first). |
| `websearch.py` | Web search through Brave / Serper / SerpApi when keyed, DuckDuckGo otherwise. |
| `research.py` | Searches, fetches pages, extracts readable text, writes a sourced report. |
| `browser.py` | Playwright automation: inspect pages and forms, preview and fill forms (asks first). |
| `news.py` | Real headlines from free feeds (BBC first, Google News for topics), with article text. |
| `weather.py` | Open-Meteo current weather and 3-day forecast; no key. |
| `travel.py` | Driving distance and time (OSRM), multi-stop trips, maps, places nearby. |
| `youtube.py` | Video transcript → summary, indexed for questions (`youtube` extra). |
| `jobright.py` | Pulls early-career job leads from Jobright with Playwright and filters them. |

### Everyday answers, computed
| File | What it does |
|---|---|
| `calculator.py` | Exact arithmetic with a hand-written parser (never `eval`); also spoken numbers. |
| `units.py` | Unit conversions ("5 miles to km", "how many ounces in a pound"). |
| `dates.py` | Days until a date or holiday, what day something falls on. |
| `clock.py` | The time and date from this laptop's clock, in any time zone. |
| `chance.py` | Coin flips, dice and random numbers, drawn with `secrets`. |

### The laptop itself
| File | What it does |
|---|---|
| `desktop.py` | Screenshots and opening apps. |
| `windows.py` | Arranges windows by name ("Chrome on the left"); ctypes behind an injectable backend. |
| `terminal.py` | `run command …`: shell commands with a preview, a timeout and approval. |
| `music.py` | Media keys, volume levels and YouTube music; refuses to treat "my voicemail" as a song. |
| `webcam.py` | One camera frame for the vision model (`vision` extra). |
| `transcribe.py` | OCR (NVIDIA parse, Tesseract) and speech-to-text (Riva, Vosk, Whisper). |

### Mail, notes and pictures
| File | What it does |
|---|---|
| `email.py` | IMAP search, unread and digest; SMTP and OAuth (Gmail, Outlook) drafts and sends, which ask first. |
| `obsidian.py` | Your Obsidian vault: search, read, save notes, link-aware answers, an audit. |
| `imagegen.py` | Text-to-image through NVIDIA's hosted FLUX models. |
| `__init__.py` | Package marker. |

## Read next

- `docs/design/architecture-map.md`: the reasoning and measurements behind most tools.
- `agents/README.md` for how commands reach a tool.
- Tests: `test_<tool>.py` for nearly every module (for example `test_weather.py`,
  `test_windows.py`, `test_email_tool.py`).
