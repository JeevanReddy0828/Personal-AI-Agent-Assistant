# `planner/` — deciding what a sentence means

## Purpose

A planner turns a sentence into a `PlanDecision`: either `action="command"` with the exact
command to run ("set the volume to fifty" → `media volume 50`), or `action="chat"` (answer
it in words). There are two, used in order:

1. **The instant router** (`heuristic.py`): hand-written patterns, no network, about 2 ms.
   Most everyday requests are decided here.
2. **The language-model router** (`openai_compatible.py`): asks a hosted model when the
   instant router has no answer. The same class also writes the chat answers themselves,
   streams them, describes images and reports why a model call failed.

## How it connects

```
 agents/orchestrator.py  _route(text)
        │
        ├─ planner/heuristic.py  HeuristicPlannerProvider.plan()  ── decided? done
        ├─ is_plain_question(): a plain knowledge question skips routing entirely
        └─ planner/openai_compatible.py  OpenAICompatiblePlannerProvider.plan()
              (one provider per model tier, built in app.py from OPENAI_* settings)
```

The orchestrator also imports small helpers from `heuristic.py` that several places must
agree on: `strip_address` (drop "hey jarvis"), `is_negated` ("do not open youtube" is never
run), `asks_not_to_forget`, `is_plain_question`, `fact_question` and others. Shared word
lists come from the tools themselves (window positions from `tools/windows.py`, spoken
numbers from `tools/calculator.py`) so the router and the tool cannot disagree.

## Usage

```python
from laptop_agent.planner import HeuristicPlannerProvider

decision = HeuristicPlannerProvider().plan("remind me to call mom at 6pm", "", {})
print(decision.action, decision.command)   # command  reminder add to call mom at 6pm
```

Changing a route: add or adjust the pattern in `heuristic.py`, match the **whole sentence**
(a word matched anywhere grabs ordinary sentences: "give me a template for a follow up
email" once read the inbox), keep the polite prefix `_POLITE`, and add both a phrase that
must route and a near miss that must stay chat to the contract in `selfcheck.py`.

## Contents

| File | What it does |
|---|---|
| `core.py` | `PlanDecision` (command or chat, with confidence and explanation), the `PlannerProvider` protocol, the `Planner` wrapper, and `CutOff` (a reply that hit its length limit). |
| `heuristic.py` | `HeuristicPlannerProvider`: every instant route (reminders, timers, lists, facts, weather, news, email, files, windows, music, documents, pictures …) plus the shared sentence helpers listed above. |
| `openai_compatible.py` | `OpenAICompatiblePlannerProvider`: routing, chat answers (plain and streamed), image description and keep-warm pings against any OpenAI-compatible endpoint (NVIDIA by default, OpenRouter as a backup); `classify_failure` tells a busy model from a misconfigured one. |
| `__init__.py` | Re-exports the classes above. |

## Read next

- `docs/design/conventions.md` item 5 (why heuristics come first and how
  `is_plain_question` stays conservative), `docs/design/routing.md`, and
  `docs/design/models.md`.
- Tests: `test_planner.py`, `test_llm_planner.py`, `test_everyday_requests.py`,
  `test_selfcheck.py`.
