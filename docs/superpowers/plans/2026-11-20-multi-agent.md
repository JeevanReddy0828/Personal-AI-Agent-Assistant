# Multi-agent mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `agent team <goal>`: a planner on the strongest tier delegates sub-tasks to lower tiers, every agent supervised by one shared run tree with a rules-only trip breaker.

**Architecture:** `supervisor.RunTree` holds per-tree state (taint, refusals, breaker counters, stop); `Supervisor` keeps per-agent repeats and its assigned tier/depth. `delegate` is an agent command the orchestrator's supervised `execute` intercepts and answers by running a child `AutonomousAgent`.

**Tech Stack:** Python 3.11 standard library, unittest.

**Spec:** `docs/superpowers/specs/2026-11-20-multi-agent-design.md`

## Global Constraints

- Zero new dependencies; no LangGraph.
- Breaker defaults: `MAX_STEPS_PER_MINUTE = 20`, `MAX_DEPTH = 2`, `MAX_AGENTS = 4`, `MAX_TREE_STEPS = 20`; planner `max_steps` 8, child 4; delegate result clipped at 1,500 characters.
- Tool risk levels unchanged; every swallowing `except` calls `record_failure`.
- Do not touch the `_complexity` call in `_handle`, `TurnTrace`, or the `tier_decider` health key.

## Review Focus

- A child's delegate in a plain `agent run` (no handler): refused, never crashes - `test_agent_run_cannot_delegate`.
- A `delegate` with no sub-task or an unconfigured tier: a failed step, not a spawned agent - `test_delegate_without_task_or_model_fails_the_step`.
- A Stop (cancellation) while a child runs ends the whole team run - existing `check_cancelled` path; `test_cancel_in_child_ends_team` not added (child runs in the same operation; covered by reasoning tests).
- Audit full disk while recording an `agent_message`: recorded as a failure, run continues - shared `_audit` helper with `record_failure`.
- The backup model writes `delegate`: refused (`trust`), counts as a refusal - `test_untrusted_tier_cannot_delegate`.

---

### Task 1: Supervisor run tree, breaker and groups

**Files:** Modify `src/laptop_agent/supervisor.py`; Test `tests/test_supervisor.py`.

**Interfaces - Produces:**
- `RunTree(clock: Callable[[], float] = time.monotonic)` with `tainted_by: str`, `refusals: int`, `last_refusal: str`, `stopped: Decision | None`, `agents: list[dict]`, `add_agent(parent: str, depth: int, tier: str, task: str) -> str` (ids `a1`, `a2`, …; the planner is `a0`).
- `Supervisor(tree: RunTree | None = None, *, agent: str = "a0", parent: str = "", depth: int = 0, tier: str = "")`; `tainted_by` stays readable.
- Groups: `DELEGATE = ("delegate",)`; `DIRECT` gains `agent run`, `agent team`, `schedule agent`, `autopilot`. `TIER_RANK = {"fast": 0, "smart": 1, "ultra": 2}`.
- Rules in `check`, in order: tree stopped -> same STOP; count the step (rate, then steps) -> `breaker:rate` / `breaker:steps`; refusals; repeat (per agent); direct; trust; delegate (`delegate-tier` refuse, `breaker:depth`, `breaker:agents`); taint. Every STOP is stored in `tree.stopped`.

- [ ] Tests: `BreakerTests` - rate (injected clock, 21 distinct steps in one second, `MAX_TREE_STEPS` patched up), steps (clock +10 s a step), depth (`Supervisor(depth=2, tier="smart")` delegating fast), agents (four `add_agent` then a fifth delegate); each asserts `(STOP, "breaker:<rule>")` and that a second supervisor on the same tree gets the same STOP. `TreeTests` - child on a tainted tree asks for `open url`; child's read taints parent; refusal in child + one in parent stops; repeat stop in child stops parent; `delegate` up/sideways/unknown -> `delegate-tier`; from `fallback` -> `trust`; `agent run x`, `schedule agent …`, `autopilot …`, `agent team x` -> `direct`. Groups test gains `NOT_AGENT_COMMANDS`.
- [ ] Run `python -B tests/run_tests.py test_supervisor.py`; expect failures; implement; expect pass; commit.

### Task 2: Reasoning step fields and delegate observation

**Files:** Modify `src/laptop_agent/reasoning.py`; Test `tests/test_reasoning.py`.

**Produces:** `AgentStep.agent: str = ""`, `AgentStep.depth: int = 0`; `_observe(result, limit: int = 320)`; the loop uses `DELEGATE_OBSERVATION = 1500` when the command starts with `delegate `; `AgentRunTracker.record_run(result, agents: list[dict] | None = None)` adds `"agents"` when given.

- [ ] Test `test_a_delegate_result_is_observed_at_length`: a 1,000-char delegate result reaches the next prompt whole; a 1,000-char `read file` result is clipped at 317. Fail, implement, pass, commit.

### Task 3: Orchestrator team run

**Files:** Modify `src/laptop_agent/agents/orchestrator.py`; Test new `tests/test_multi_agent.py`.

**Consumes:** Tasks 1-2. **Produces:** `run_agent(goal, on_step=None, history=None, team: bool = False)`; `agent team <goal>` dispatch; `_supervised(history, brain, supervisor=None, delegate=None)`; `_delegator(tree, root_goal, parent, depth, on_step)`; audit events `supervisor` (+`run`, `parent`, `depth`) and `agent_message` (`run, parent, depth, tier, direction, text[:300], status`).

- [ ] Tests (Script brains per tier, as `test_supervisor.py`): happy path ultra delegates to fast and smart and combines, messages in order; child cannot escape taint (planner web search, child `open url` asked and denied, counts toward refusals); a breaker trip in a child stops the team run with `stopped` and the rule in the answer; `test_agent_run_cannot_delegate`; `test_untrusted_tier_cannot_delegate`; `test_delegate_without_task_or_model_fails_the_step`; child step limit returns `[ok] (step limit reached)`; `agent run` prompt has no `delegate`.
- [ ] Run the file, then the full suite; commit.

### Task 4: Docs

`docs/design/orchestrator.md` (Two autonomy layers: team run, run tree, breaker), `MEMORY.md` dated decision, `ERRORS.md` index line (nested `agent run` laundered taint), `src/laptop_agent/README.md` and `tests/README.md` rows, help text. Commit.
