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

- **`AgentContext`** (a frozen dataclass in `orchestrator.py`) holds every tool and store.
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
| `orchestrator.py` | `AgentContext` and `AgentOrchestrator`: direct dispatch, routing, the checks applied to every routed command, the chat tiers and their fallback, follow-up answers ("10 minutes" after "How long should the timer run?"), and the plain-language summaries of tool results. |
| `control_room.py` | `AgentControlRoom`: maps commands to 14 specialist slots (planner, files, knowledge, research, browser, vision, email, music, notes, tasks, workflow, autopilot, reminders, terminal) and marks them working or done for the dashboard. |
| `__init__.py` | Package marker. |

## Read next

- `CLAUDE.md` → "Everyday requests" (the rules the dispatch and routing follow) and
  "Session context" (what the history becomes before a model sees it).
- `planner/README.md` for the routers, `tools/README.md` for what the commands reach.
- Tests: `test_orchestrator.py`, `test_command_dispatch.py`, `test_everyday_requests.py`.
