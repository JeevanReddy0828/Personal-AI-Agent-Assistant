# Multi-agent mode: delegation as a supervised step

Status: Approved by Jeevan 2026-11-20. Branch `claude/multi-agent`, from `main` at 49112ea. How it was built is in `orchestrator.md` ("A team run is one tree").

## Why

Agent mode (`reasoning.AutonomousAgent`) runs one model on one goal. Jeevan wants three things on top
of it:

1. **Agents that talk to each other.** A run can hand a sub-task to another agent and get its answer
   back, and the messages between them are recorded.
2. **Tier delegation.** A strong model (ultra, or smart) plans and gives the sub-tasks that do not
   need it to a cheaper tier (smart or fast), then combines the answers.
3. **One monitor over all of it.** Every step any agent takes passes through `supervisor.py`, is in
   the audit log, and can be stopped like a kill switch. A "trip breaker" of plain rules, never a
   model, stops the whole run tree and records which rule tripped. A delegated agent inherits its
   parent's taint and trust, so delegation cannot launder a refused step.

Jeevan chose **option A**: delegation is one more agent command, `delegate`, which the supervisor
checks like any other step. (Option B, a fixed plan fanned out by the orchestrator, was not chosen.)

## A gap this closes

Today `agent run …`, `schedule agent …` and `autopilot …` are in no supervisor group, so a run
step may dispatch them (allowed before the run is tainted, asked once after). `agent run` then builds
a fresh `Supervisor()` with no taint, no refusal count and no repeat memory: a run can already launder
itself. After this change they are `DIRECT` (never a step), and the only way an agent starts another
is `delegate`, which carries the tree's state.

## Goals

- `agent team <goal>`: a planning agent on the strongest tier that may delegate sub-tasks to lower
  tiers and combine the answers.
- Every step of every agent in the tree goes through one shared supervisor state and the audit log.
- Breaker rules: steps per minute across the tree, delegation depth, agents started per run, total
  steps across the tree. A trip stops every agent in the tree and names the rule.
- Children inherit the tree's taint, refusals and the parent's trust; nothing a child does can reset
  them.
- Offline tests for each breaker rule tripping and for a child that cannot escape its parent's taint.

## Non-goals

- No LangGraph, LangChain or any new package, required or optional. The run tree plus the existing
  loop is the graph; per-agent steps stay plain functions.
- No parallel children. Approval cards and desktop tools come one at a time; children run in turn.
- No change to `agent run` (single agent, as today), to tool risk levels, or to the page
  (`webui_assets/`). Child steps go to the same `on_step` callback; starting a team run from the
  page's live agent panel is a follow-up.
- No model judging anything in the supervisor or the breaker.
- Not touching the tier-choice work on `claude/laya-tier-pipeline`: the `_complexity` call in
  `_handle`, `TurnTrace` and the `tier_decider` health key.

## Anti-goals (it fails even if it "works" when…)

- a child can run a step its parent's tree would have asked about or refused;
- a trip, a Stop or a supervisor STOP in a child leaves the parent (or a sibling) running;
- a delegated step runs without an audit line, or the audit cannot say which agent ran it;
- a sub-agent can start a sub-agent past the depth cap, or by any route other than `delegate`.

## How it works

### Entry point

`agent team <goal>` (dispatched next to `agent run` in `orchestrator._dispatch`; `run_agent(…,
team=True)` for the web path that streams steps). Access is the same as `agent run`: the `agent`
prefix is already developer-only in `access.py`.

The planner's brain is ultra, else smart, else fast, with the existing fallback ladder behind it
(`_build_agent_brain`). If the planner's tier has no lower tier configured, `delegate` is left out
of its prompt and the run is an ordinary single-agent run.

### The `delegate` command

Listed in the planner's AVAILABLE COMMANDS only for `agent team`, only for the tiers below the
planner that are configured:

```
delegate <fast|smart> <sub-task>
```

- **Tier rule:** strictly lower than the agent's *assigned* tier (the planner's is fixed when the run
  starts; a child's is the one it was given): ultra may delegate to smart or fast, smart to fast. A
  higher or equal tier, or an unknown one, is refused (rule `delegate-tier`). It is judged against the
  assigned tier, not `decide.tier`, so a busy ultra answered by smart does not get the offered
  `delegate smart` refused. Trust is still judged by `decide.tier`, as for every step, so a
  `delegate` written by the backup is refused (`trust`) and counts as a refusal like any other.
- **The child** is an `AutonomousAgent` whose `decide` is that tier with the backup behind it, and
  whose `execute` is a supervised execute bound to the **same `RunTree`** (below), with `depth =
  parent depth + 1`. It gets the sub-task as its GOAL plus one line naming the parent's goal, not
  the conversation context. Its `max_steps` is 4; the planner's is 8.
- **A child only looks things up and works things out** (added after the live test, 11-20: helpers
  wrote three documents and tried to save a note instead of answering). It is offered, and its
  supervisor allows, only READS, `supervisor.HELPER_COMPUTES` (convert, weather, totals and the
  like) and `delegate`, but not `look at webcam` or `read screen` (`HELPER_SENSORS`: the webcam asks
  nothing); anything else is refused with rule `helper`. That refusal is counted toward the tree's
  stop only when the tree has read outside text and the step is a real command (a possible injected
  attempt to act); otherwise it is not: a helper that meant to answer wrote "ACTION: (no command needed)" twice,
  and the two refusals ended the run; its own 4 steps bound it. Its goal line tells it to answer in
  FINAL and use a command only for what it cannot know, and every agent's prompt now says FINAL may
  come first when no command is needed. Acting is the
  planner's, so a child never reaches the taint rule's ASK: it is refused first.
- **A child at the depth cap** is not offered `delegate`; one at a lower depth is offered it for the
  tiers below its own.
- **Its answer** comes back as the parent's OBSERVATION: `[ok] <child FINAL>` or `[failed] …`.
  `_observe` clips at 320 characters, which is too short to combine from, so a delegate result is
  clipped at 1,500 characters, saying so when clipped (same wording as `_observe`).
- **Combining** is the planner's own FINAL; no extra step.

### The run tree (shared supervisor state)

A new `RunTree` in `supervisor.py` holds what must be one per tree:

- `tainted_by`: set by any agent's read, seen by every agent. A child starts tainted if the tree is,
  and a child's read taints the parent (its answer is outside text to the parent).
- refusals and `last_refusal`: counted across the tree, so two refusals anywhere stop the tree.
- the breaker counters and `tripped` (rule, reason).
- an injectable clock (`time.monotonic` by default) for the per-minute rule.
- the agents started: id, parent id, depth, tier, sub-task.

`Supervisor` keeps its per-agent part (repeat counts: the same step a third time *in one agent*) and
takes an optional `tree`; with none it makes its own, so `agent run` and every existing test behave
as now.

### Trust

- `delegate` from a tier that is not trusted (`untrusted_tiers()`) is refused, rule `trust`, like any
  step outside `safe`/`reads`. So an untrusted model can never start an agent that acts.
- A child's own steps are judged by its own tier's trust, as every step is now.

### Groups

- New group `delegate` holding the form `delegate`. Not tainting, never asked about by taint (it
  does not act itself; the child inherits the taint and its acting steps are asked), counted by the
  breaker.
- `agent run`, `schedule agent`, `autopilot` (and `agent team`) join `DIRECT`. They stay out of
  `_AGENT_COMMANDS`, as now.

### The trip breaker

Checked at the top of `Supervisor.check`, before the existing rules, for every step of every agent:

| Rule | Default | Trips when |
|---|---|---|
| `breaker:rate` | 15 steps a minute (below the total, or a burst stops only where the total would) | the tree's steps in the last 60 s would exceed it |
| `breaker:depth` | 2 | a `delegate` would start an agent deeper than 2 (planner is depth 0); a backstop, since three tiers delegating strictly down cannot go deeper |
| `breaker:agents` | 4 | a `delegate` would start a fifth agent in the tree (planner not counted) |
| `breaker:steps` | 20 | the tree's total steps would exceed it |

A "step" for the breaker is every `check` call in the tree, whatever its verdict (refused and asked
steps count, and so do the `delegate` steps themselves); the model calls a child makes to ask again
or summarise are not steps. With a planner of 8 and children of 4, a full tree could take 24, so the
cap of 20 is a real guard, not only a test fixture.

Defaults are module constants; tests patch them. A trip:

1. sets `tree.tripped = (rule, reason)` and returns `Verdict.STOP` with that rule;
2. makes every later `check` in any agent of the tree return the same STOP at once (the kill switch);
3. is written to the audit log like every verdict, with the rule.

**Any supervisor STOP in any agent stops the tree**, not only a breaker trip: a child stopped for
refusals sets `tree.stopped = (rule, reason)` before `RunStopped` is
raised, and every later `check` in the tree returns that STOP. `AutonomousAgent.run` keeps catching
`RunStopped` as it does now; the parent's `delegate` execution reads `tree.stopped` after the child
returns and, if set, raises `RunStopped` itself, so the whole tree returns with the stop message as
its answer and status `stopped`. The planner is then asked once, with no step left to take, what its
steps found (`AutonomousAgent(close_on_stop=...)`; a helper is not asked, the planner closes), and that
follows the stop message: live, a ten-city run that tripped the agents cap lost four cities' results,
and a run stopped for repeating a read answered nothing about the file. A child that merely ran out
of its 4 steps is not a stop, and neither is a helper repeating a step a third time (changed
11-20, after a live run where a helper asking the knowledge base the same question three times ended
the team before its planner had acted): that step is refused (rule `repeat`, not counted) and the
helper told to answer; a planner repeating still stops the tree. A helper given a change to make is
told to answer "Not done - the planner must do this itself" rather than claim it, after a live run
where a helper searched instead and the team said a fact was saved. A child out of steps: its
step-limit summary comes back as `[ok] (step limit reached) <summary>`. The existing Stop
button keeps working through `check_cancelled`, since children run in the same request.

### No running wild (added 2026-11-20)

Jeevan asked that agents never run wild: never hammer a site, never loop off each other, never leave
something hard for a person to undo. Beyond the breaker, plain rules in `supervisor.py`, each with its
own test in `tests/test_agent_limits.py`:

| Rule | Limit | Where |
|---|---|---|
| Web budget per run | 30 requests; `research`, `research report` and `solve` cost 6 (a search and its pages), other network steps 1 (`WEB_COST`) | `RunTree.charge`, `breaker:web` |
| One site per run | 5 steps to one host (from a URL), or to the search engine, or to one service | `breaker:site` |
| Time per run | 10 minutes, checked at each step | `breaker:time` |
| Model calls per run | 30, every agent's, retries and closing answers included | `RunTree.count_call` via `orchestrator._counted`, `breaker:calls` |
| Messages per run | 1 (`message`, `email api send`); the next is refused and counted | rule `sends` |
| Approval cards per run | 3 (a carded step, or a step asked about after outside text) | `breaker:cards` |
| A run nobody watches | a scheduled run only reads and computes | rule `unattended` |
| A day, across every run | 30 runs, 150 steps; kept in `agent_ledger.json`, reset at local midnight; unreadable, it fails closed | `AgentLedger`, `breaker:daily` |
| One run at a time | a second run, from another tab or a schedule, is refused at once | `orchestrator._agent_running` |
| Scheduled agents | at most hourly and at most 5; an older, more frequent one is not run | `_agent_schedule_refused`, `run_due_schedules` |
| Kill switch | `agents off` refuses every run, team and scheduled agent, survives a restart and a new day; a run going stops at its next step; `agents on` lifts it. Neither is a step of a run (`DIRECT`) | `AgentLedger.set_off` |

A stop from any of these, like every stop, still gives what the steps found.

### What is recorded

- Every verdict's audit line (`supervisor` event) gains `run`, `parent`, `depth`.
- New audit event `agent_message` for each hand-off: `{run, parent, depth, tier, direction:
  "task"|"result", text (clipped 300), status}`.
- `AgentStep` gains `agent` (id, "" for the planner) and `depth` (0), defaulted, so the web trace
  and saved runs read as before. Child steps go to the same `on_step` callback.
- The saved run (`AgentRunTracker.record_run`) gains `agents`: the tree's list of agents and their
  steps. The control room shows one entry for the team run.

## Testing (offline, unittest, no packages)

In `tests/test_supervisor.py` (rules) and a new `tests/test_multi_agent.py` (orchestrator), with
`Script` brains per tier as the existing tests do:

- each breaker rule trips: rate (injected clock), depth, agents, steps; the audit names the rule;
  a sibling or parent checking after a trip gets STOP;
- a child cannot escape taint: the planner reads the web, delegates; the child's `open url` is
  refused (`helper`); a child that reads taints the planner;
- a refusal in a child plus one in the parent stops the tree;
- `delegate` up or sideways in tier is refused; from an untrusted tier is refused;
- `agent run`, `schedule agent`, `autopilot` as steps are refused (`direct`) - fails on `main` today;
- the happy path: ultra delegates to fast and smart, combines; `agent_message` events in order;
- `agent run` unchanged: no `delegate` in its prompt;
- the groups tests still hold (every agent command in exactly one group). The test that every form
  names a real agent command gains one explicit, named list of forms that are deliberately not agent
  commands (`delegate`, `agent run`, `agent team`, `schedule agent`, `autopilot`); nothing else is
  loosened.

Each guard is undone once and a test watched failing. Full suite before pushing (orchestrator change).

## Docs

`docs/design/orchestrator.md` ("Two autonomy layers" gains the team run and the breaker),
`MEMORY.md` (a dated decision), an `ERRORS.md` index line for the laundering gap, and the folder
READMEs that list files.

## Left to the builder

Names of helpers and where they sit, the exact prompt wording for the planner and the child, the
audit payload field order, and how the saved run nests children.

## My calls (Claude's, not Jeevan's; flagged for review)

- A new `agent team <goal>` command rather than changing `agent run`.
- The breaker numbers in the table, planner `max_steps` 8 and child `max_steps` 4.
- Delegation strictly downwards (not to the same tier), judged against the assigned tier.
- Any supervisor stop in any agent ends the whole tree.
- Repeats stay per agent; taint, refusals and the breaker are per tree.
- The child sees the sub-task and the parent's goal, not the conversation.
- `delegate` is not asked about by the taint rule; the child's acting steps are.
