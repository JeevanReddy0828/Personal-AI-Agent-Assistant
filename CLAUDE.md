# CLAUDE.md

Guidance for AI coding agents (Claude Code, Codex) working in this repo.

## What this is

A **local-first, voice-capable personal laptop agent** ("J.A.R.V.I.S"), package
`laptop_agent`. It chats, runs safe laptop tools, does file intelligence, web
search/research, OCR/vision, email, a knowledge base, and an autonomous task
layer — all behind an approval gate, with an LLM "brain" that streams replies.

- **Python 3.11+**, **zero required runtime dependencies** (`dependencies = []`).
  Heavy features live behind optional extras (`browser`, `desktop`, `docs`,
  `voice`, `app`, `ocr`, `transcribe`, `stt`, `youtube`, `metrics`, `vision`).
- GitHub: `JeevanReddy0828/Personal-AI-Agent-Assistant`. Owner: Jeevan
  (Bala Showri Jeev An Reddy Arlagadda), Austin TX.

## Non-negotiable conventions

1. **Zero required deps.** New heavy capability → optional extra + graceful
   fallback (return a clear `ToolResult.failure` with an install hint; never
   crash). Follow the OCR/transcribe/metrics pattern.
2. **Approval gate for anything risky.** Sending mail, writing/moving files,
   downloads, launching apps, shell, browser state changes → go through
   `safety.ApprovalGate` with the right `RiskLevel`. Read-only/local = LOW/none;
   network read (web search, inbox read) = MEDIUM; external state change =
   HIGH/CRITICAL.
3. **Tools return `ToolResult`** (`tools/base.py`): `ok`, `message`, `data`.
4. **Testable network/IO.** Put network/engine calls behind an **injectable
   backend** (see `transcribe.py`, `websearch.py`, `research.py`, the LLM
   provider's transport) so the success path is unit-tested offline. Tests use
   `unittest`, are dependency-free, and live in `tests/`.
5. **Heuristic-first routing for latency.** Common requests route via
   `planner/heuristic.py` with zero network cost; the LLM is the fallback.
6. **Never commit secrets.** `.env` is gitignored. Scan staged diffs for
   `nvapi-` (and the Gmail app password) before every push.
7. **Match the surrounding style.** Concise comments, full type hints,
   `from __future__ import annotations`.

## Architecture (map)

```
Interfaces: CLI (cli.py) · Tkinter dashboard (dashboard.py/gui.py) · web app (webui.py)
        |
AgentOrchestrator (agents/orchestrator.py) — routes text -> one tool or a chat reply
        |
Router: planner/heuristic.py (instant)  +  planner/openai_compatible.py (LLM)
        |
Tools (tools/): files, file_processor (universal "process file" dispatcher),
        web, websearch, research, browser, desktop, email,
        music, weather (Open-Meteo, real forecast — no key),
        travel (maps: OSRM driving distance/ETA, multi-stop `trip` chaining legs +
            totals, IP-geolocated "around me", `map` -> OpenStreetMap embed for the
            web Map panel, + OpenStreetMap hotels/places — no key),
        youtube (transcript -> summary, indexed for Q&A; `youtube` extra),
        transcribe (OCR + STT: Vosk lightweight or Whisper), webcam (vision extra), obsidian
Subsystems: knowledge.py (TF-IDF index + Q&A), tasks.py (parallel + retry),
        workflows.py, autopilot.py (safe allowlist), reasoning.py (autonomous
        agent loop — plan/act/observe/replan over any tool), scheduler.py
        (recurring jobs), reminders.py, metrics.py, health.py,
        agents/control_room.py (specialist roster), safety.py, audit.py,
        memory.py, token_vault.py (DPAPI), config.py
```

- `orchestrator.handle(text, _allow_planner, history, on_token)` is the core
  entry. It checks direct command prefixes, then routes via heuristic → LLM.
  Tool results are turned into plain language by `_humanize` (local, no extra
  LLM call). Chat replies stream via the `on_token` callback when provided.
- **Freshness path.** Before answering a chat turn, `_needs_fresh_info` flags
  time-sensitive questions (keywords + patterns like "did X end", recent years);
  `_grounded_news_answer` then runs a web search (one retry for the flaky free
  DDG endpoint) and synthesizes a cited answer from the results, preferring live
  data over training knowledge. If search yields nothing it falls back to model
  chat but appends a "may be out of date" disclaimer (`stale_warning` in data).
- **Search backend.** `websearch.build_search_backend(provider, key)` returns a
  resilient backend: a real API (Brave / Serper.dev / SerpApi — note the latter two
  are different services — key-gated via `SEARCH_PROVIDER` / `SEARCH_API_KEY` /
  `BRAVE_API_KEY` / `SERPER_API_KEY` / `SERPAPI_API_KEY`) with automatic DuckDuckGo
  fallback, else DDG directly. `app.py` shares one backend across `websearch` and
  `research`. API backends take an injectable HTTP transport (offline-tested).
- **AgentContext** is a frozen dataclass of all tools/subsystems. Adding a field
  means updating `app.py`'s `build_orchestrator` AND the test builder in
  `tests/test_orchestrator.py` (this is the usual source of a wave of failures
  after a merge — fix the builder).
- **Two autonomy layers, don't conflate them.** `autopilot.py` runs a *static*
  plan restricted to a safe read-only allowlist (blocks anything risky).
  `reasoning.py`'s `AutonomousAgent` is the *LLM-driven* plan/act/observe/replan
  loop that can use any command (risky ones still hit the approval gate). Its
  reasoning brain is an injected `decide(prompt)->str` callable so the loop is
  unit-tested offline; in `orchestrator._build_agent_brain` it's backed by
  `provider.answer` on the smart (or fast) tier. Persisted via the `agent_runs`
  AgentContext field.

## LLM brain — tiered models

Configured via env / `.env` (auto-loaded by `config.py`). Pick by task complexity:
- `OPENAI_MODEL` — fast/simple (e.g. `meta/llama-3.1-8b-instruct`)
- `OPENAI_SMART_MODEL` — complex (`nvidia/llama-3.3-nemotron-super-49b-v1`)
- `OPENAI_ULTRA_MODEL` — very complex (`nvidia/nemotron-3-ultra-550b-a55b`)
- `OPENAI_VISION_MODEL` — screen/images (`meta/llama-3.2-11b-vision-instruct`)
- `OPENAI_BASE_URL` (NVIDIA: `https://integrate.api.nvidia.com/v1`), `OPENAI_API_KEY`

Chat escalates fast→smart→ultra by `_complexity`, and **degrades gracefully**: if a
higher tier is congested/unreachable (its `answer`/`stream_answer` yields nothing)
the orchestrator falls back to the next tier down, tags the reply
(`degraded=True` in data, plus `planner.requested_model` vs `planner.model`) with a
short "_my smart model was busy_" note, and records the outcome in
`orchestrator.model_status` (`model_status.py`, thread-safe per-tier ok/degraded).
`health.system_health` surfaces this as `llm.tiers` + `llm.degraded_tier`, and the
web pill shows "smart/ultra model busy" while the fast tier stays healthy.

After all primary (e.g. NVIDIA) tiers, an optional **cross-provider fallback** is
tried: `OPENROUTER_API_KEY` (+ `OPENROUTER_MODEL`, default a free model;
`OPENROUTER_BASE_URL`) builds an OpenRouter planner (`app._build_openrouter_planner`,
passed as `AgentOrchestrator(..., fallback_planner=…)`). Since OpenRouter is a
different backend, it can answer when NVIDIA is throttled; its reply is tagged
`model="openrouter"` / degraded with a "_backup model_" note and tracked as the
`openrouter` tier in `model_status`/health. Absent the key it's simply skipped.

Routing uses few-shot **message turns** for reliability. The 8B alone won't route
without them. The web app (`webui.py`) streams chat via `/api/stream` (SSE) —
which, when the request sets `voice:true`, also emits incremental `tts` sentence
events (carved by `voice.SpeechChunker`) so the browser voice loop starts speaking
the first sentence before generation finishes — streams autonomous-agent traces
via `/api/agent`, exposes `/api/health`, serves a
Scheduled-jobs panel via `/api/schedule` (GET lists jobs; POST add/remove/enable/
disable, routed through the same `schedule …` orchestrator commands), exposes
read-only autonomous-agent run history via `/api/agent-runs`, serves a Map panel
via `/api/map` (POST a place or `A to B` -> OpenStreetMap embed/bbox/directions,
routed through the `map …` orchestrator command), serves server-side
voice for the native window (`/api/transcribe` STT, `/api/tts` offline TTS), and runs a 60s
background `_schedule_ticker` for due scheduled jobs; it keeps the model warm to
avoid cold-start latency.

## Running it

```powershell
$env:PYTHONPATH="src"
python -m laptop_agent.cli                                              # terminal
python -m laptop_agent.webui --desktop                                  # desktop app window (or: laptop-agent-deck)
python -m laptop_agent.webui                                            # browser tab
```

The desktop window prefers a true native **pywebview** window (`app` extra; no
Edge browser, its own taskbar entry) and falls back to a frameless Chrome/Edge
`--app` window when pywebview is absent. Because Edge WebView2 (pywebview's
Windows backend) ships no Web Speech API, the native window does voice
**server-side**: it sets `?app=1`, records the mic, transcribes via `/api/transcribe`
(local `TranscribeTool`/Whisper), and plays sentences from `/api/tts` (offline
pyttsx3). The Chrome/Edge fallback still uses the in-browser Web Speech API.
`packaging/` bundles all this into a standalone `JARVIS.exe` via PyInstaller.

Speech-to-text has two engines, chosen by `LAPTOP_AGENT_STT` (default `auto`):
**Vosk** (lightweight — ~50MB model, no PyTorch/ffmpeg; reads the 16kHz mono WAV the
browser encodes via Web Audio) and **Whisper** (accurate, heavy). `auto` prefers Vosk
when a model is present in `models/` (or `VOSK_MODEL`), else Whisper. `build_app_small.ps1`
bundles the Vosk path for a far smaller `JARVIS.exe`.

Tests: `$env:PYTHONPATH="src"; python -m pytest tests -q` (402 passing).

## Working alongside another agent (Codex)

Both Claude and Codex edit this repo. To avoid collisions:
- **Work on a branch**, not `main` (e.g. `claude/<feature>`, `codex/<feature>`).
- `git pull` / rebase before a batch; merge to `main` between sessions.
- Expect to reconcile the shared **test builder** and **control-room roster
  count** when the other agent adds an `AgentContext` field or a specialist.

## Outstanding / watch-outs

- **Rotate the NVIDIA API key and Gmail app password** (both were pasted in chat;
  they live only in gitignored `.env`).
- GPU metrics need an elevated launch on this laptop (Optimus dGPU).
- The user keeps durable project memory in an Obsidian vault at
  `F:\obsidian\Claude mem-Obsidian main memory\Claude Mem\Personal AI Agent`.
  Keep those notes in sync when shipping features.
