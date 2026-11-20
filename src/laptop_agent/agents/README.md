# `agents/` — the orchestrator and the specialist roster

## Purpose

This is the decision-maker. `AgentOrchestrator` takes one sentence (plus the conversation
so far) and decides whether it is a command for a tool or something to answer, runs it, and
turns the result into a reply. The control room is the small live roster the web page shows
in its "tool activity" panel.

## How it connects

```
 webui.py / cli.py / dashboard.py
        │  await orchestrator.handle(text, history=..., on_token=...)
        ▼
 AgentOrchestrator.handle  ── opens a latency trace (tracing.py), then _handle:
        │
        ├─ prose guard: does a command word start a normal English sentence?
        ├─ _DISPATCH: 18 groups (_dispatch_meta, _dispatch_files, _dispatch_web, …)
        │     each returns a ToolResult, or None for "not mine"
        ├─ _route: planner/heuristic.py, then the language-model router (planner/)
        │     every routed command is checked again (negation, invented shell commands,
        │     image subjects, targets nobody named) before it runs
        └─ chat: the model tiers fast → smart → ultra, falling back when one is busy
        ▼
 tools/* and the stores, all reached through AgentContext
```

- **`AgentContext`** (a frozen dataclass in `common.py`) holds every tool and store.
  It is built in exactly one place, `app.build_context()`; add a new tool there and nowhere
  else.
- Risky tools raise an approval request through `safety.ApprovalGate`; in the web app
  `approvals.py` turns it into a card you click.
- What a `personal` account may run is checked here too (`_account_limits`, using
  `access.py`).

## Usage

You rarely call this directly; the interfaces do. In code or a test:

```python
import asyncio
from laptop_agent.app import build_orchestrator

# approval_callback answers risky actions; None means the gate asks on the terminal.
orchestrator = build_orchestrator(approval_callback=None)
result = asyncio.run(orchestrator.handle("what time is it in tokyo"))
print(result.ok, result.message)
```

This uses your real configuration and data directory. In tests, build the context from an
isolated config instead (see `tests/test_orchestrator.py`).

Adding a command: put its parsing in the right `_dispatch_*` group (or teach
`planner/heuristic.py` to produce it), have the tool return a `ToolResult`, and add a row to
the routing contract in `selfcheck.py` so `tests/test_everyday_requests.py` holds it.

## Contents

| File | What it does |
|---|---|
| `orchestrator.py` | `AgentOrchestrator`: its set-up, `handle`/`_handle` (the path every message takes) and the `_DISPATCH` order. The class is assembled from the parts below, one per area, split out of this file unchanged on 2026-11-20; a part reads the rest through `self`. |
| `common.py` | `AgentContext`, and the helpers and constants the parts share. |
| `routing.py` | Understanding a request: the model router, the checks on every routed command, follow-ups ("10 minutes" after "How long should the timer run?"), prose, split requests. |
| `chat_tiers.py` | The chat tiers and their fallback, how hard a question is, answers grounded in a live search. |
| `dispatch_core.py` | Command tables for the app itself: memory, jobs, automation, files, knowledge, notes, vision, tasks, research, utilities. |
| `dispatch_outside.py` | Command tables that reach outside: generation, translation, travel, the web, the desktop, email. |
| `everyday.py` | Everyday answers: dates, lists, facts, calendar, status, and what a `personal` account may run. |
| `reminders.py` | Reminders, timers, alarms and repeats. |
| `briefing_jobs.py` | The daily briefing, the job tracker and job applications. |
| `agent_mode.py` | Agent runs and teams under the supervisor, the shell, schedules, autopilot, workflows, the control room. |
| `knowledge_actions.py` | Research, solving, the knowledge base, vision, memory, notes and email. |
| `tool_access.py` | Tools built on first use. |
| `presentation.py` | Help, capabilities, reports, and the plain-language summaries of tool results (`_humanize`). |
| `control_room.py` | `AgentControlRoom`: maps commands to 14 specialist slots (planner, files, knowledge, research, browser, vision, email, music, notes, tasks, workflow, autopilot, reminders, terminal) and marks them working or done for the dashboard. |
| `__init__.py` | Package marker. |

## Read next

- `docs/design/routing.md` (the rules the dispatch and routing follow) and
  `docs/design/orchestrator.md` (session context: what the history becomes before a model sees it).
- `planner/README.md` for the routers, `tools/README.md` for what the commands reach.
- Tests: `test_orchestrator.py`, `test_command_dispatch.py`, `test_everyday_requests.py`.
