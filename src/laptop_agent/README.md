# `laptop_agent` — the J.A.R.V.I.S package

## Purpose

Everything the assistant is: the interfaces you talk to (terminal, web page, desktop
window), the orchestrator that decides what a sentence means, the tools that act on the
laptop or the web, and the stores that remember things between runs. It is pure Python 3.11+
with **no required dependencies**: anything heavy (browser automation, OCR, speech, Office
files) is an optional extra, and a tool whose extra is missing returns a clear failure with
the install command instead of crashing.

## How a request travels

```
 you ──> cli.py / webui.py (+ webui_page.py, webui_assets/) / dashboard.py
            │   the web server also signs you in (accounts, sessions, access)
            ▼
 agents/orchestrator.py  AgentOrchestrator.handle(text, history)
            │  1. a direct command ("remind me …", "read file …")   -> its tool
            │  2. otherwise the instant router  planner/heuristic.py
            │  3. otherwise the language-model router  planner/openai_compatible.py
            │  4. otherwise a chat answer from the model tiers (fast → smart → ultra)
            ▼
 tools/*  (each returns a ToolResult; anything risky asks first through safety.ApprovalGate)
            │
            ▼
 stores: memory, reminders, scheduler, knowledge, jobs, traces … (all written via storage.py)
```

`app.py` builds the one `AgentContext` that hands every tool and store to the orchestrator;
it is the only place they are wired together.

## Usage

```powershell
$env:PYTHONPATH="src"
python -m laptop_agent.cli               # terminal chat
python -m laptop_agent.webui             # web app on http://localhost:8770
python -m laptop_agent.webui --desktop   # the same app in its own window
```

Installed as a package, the same entry points are `laptop-agent`, `laptop-agent-deck`
(desktop window) and `laptop-agent-dashboard` (the Tkinter HUD). Settings come from
environment variables or a `.env` file read by `config.py`. A second, throwaway instance
needs **both** `LAPTOP_AGENT_PORT` and `LAPTOP_AGENT_DATA_DIR`, or it writes into your real
data.

## Contents

### Interfaces
| File | What it does |
|---|---|
| `cli.py` | Terminal REPL; also prints reminders as they fall due. |
| `webui.py` | The stdlib HTTP server behind the web and desktop app: chat streaming, approvals, sign-in, LAN mode, every `/api/...` route. |
| `webui_page.py` | Stitches `webui_assets/` into the single page the server sends. |
| `dashboard.py` | Tkinter "HUD" desktop dashboard (`laptop-agent-dashboard`). |
| `gui.py` | An older, simpler Tkinter window; run directly with `python -m laptop_agent.gui`. |
| `window_fx.py` | Transparency and always-on-top for the desktop window (Windows only, no-op elsewhere). |
| `voice.py` | Text-to-speech, and cleaning a reply so it can be spoken (no URLs or code read aloud). |
| `recordings.py` | Saving voice recordings the user asked for, separate from throwaway speech clips. |

### Wiring and settings
| File | What it does |
|---|---|
| `__init__.py` | The package version (`__version__`). |
| `app.py` | `build_context()` wires every tool and store; `build_orchestrator()` adds the model tiers. |
| `config.py` | `AppConfig` and `load_config()`: reads environment variables and `.env`. |

### Deciding and answering
| File | What it does |
|---|---|
| `agents/` | The orchestrator (routing, dispatch, chat) and the specialist roster. See its README. |
| `planner/` | The instant router and the language-model client. See its README. |
| `context.py` | Turns the chat transcript into the context block every model call sees; resolves "this" in follow-ups. |
| `model_status.py` | Which model tier is healthy, busy or misconfigured; survives restarts for broken tiers. |
| `advisor.py` | `solve <problem>`: a researched, structured recommendation. |
| `reasoning.py` | The autonomous agent loop (plan, act, observe, re-plan) behind agent mode. |
| `autopilot.py` | A fixed plan limited to safe read-only commands. |
| `workflows.py`, `tasks.py` | History of multi-step workflows and parallel task batches. |
| `cancellation.py` | Cooperative Stop: long work checks it and ends cleanly. |
| `tracing.py` | Per-turn timings (never the text) for `latency` and `/api/traces`. |
| `failures.py` | Every swallowed error is recorded here (`failures`, `/api/failures`). |
| `selfcheck.py` | `selfcheck`: does routing actually reach the right tools? |

### Safety, accounts and sign-in
| File | What it does |
|---|---|
| `safety.py` | `RiskLevel` and `ApprovalGate`: risky actions must be approved. |
| `approvals.py` | Lets the web page answer an approval request (the card you click). |
| `access.py` | What a `personal` account may not do, checked per request. |
| `accounts.py`, `sessions.py` | Accounts (scrypt passwords, roles) and server-side sessions. |
| `google_oidc.py` | "Sign in with Google" identity check; keeps no mailbox tokens. |
| `token_vault.py` | Encrypted (DPAPI) storage for email OAuth tokens. |
| `audit.py` | Append-only log of every approval decision and of sign-in events. |

### Memory, knowledge and storage
| File | What it does |
|---|---|
| `memory.py` | Facts about you, notes and lists ("add milk to my shopping list"). |
| `knowledge.py` | Your indexed documents: TF-IDF search plus vectors, and question answering. |
| `embeddings.py` | Semantic vectors for `knowledge.py`; falls back to keywords when offline. |
| `terms.py` | The one word and sentence splitter all retrieval code shares. |
| `storage.py` | Atomic, locked JSON files; every store above writes through it. |
| `retention.py` | Trims generated images, documents and uploads so they do not grow forever. |

### Everyday
| File | What it does |
|---|---|
| `reminders.py` | Reminders and timers; the interfaces deliver them when due. |
| `scheduler.py` | Recurring jobs ("every weekday at 8"), run by the web server's ticker. |
| `timeparse.py` | "6pm", "tomorrow at 9", "in 20 minutes" → an exact time on this laptop's clock. |

### Jobs
| File | What it does |
|---|---|
| `jobs.py` | The job-application tracker and its pipeline stages. |
| `copilot.py` | ATS scoring and grounded resume/cover-letter tailoring. |

### Health
| File | What it does |
|---|---|
| `health.py` | `/api/health` and the Setup panel: what is ready and what to install or set. |
| `metrics.py` | CPU, memory, GPU and battery readings for the status drawer. |

### Sub-folders
`agents/`, `analytics/`, `planner/`, `tools/` and `webui_assets/` each have their own
README.

## Read next

- `CLAUDE.md` at the repository root for the rules (zero required dependencies, the approval
  gate, `ToolResult`, injectable backends), and `docs/design/` for the reasoning behind each
  subsystem.
- `ERRORS.md`: start at its symptom index when something misbehaves.
- `tests/README.md`: how to run the suite and where each area is tested.
