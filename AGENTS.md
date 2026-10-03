# AGENTS.md — how Claude and Codex work on J.A.R.V.I.S

Both agents load this file at the start of every session: Codex reads `AGENTS.md` itself
(root down to the working directory, 32 KiB combined), and Claude Code imports it from
`CLAUDE.md`. So it holds only what neither of us may ever miss, and a map to the rest.
**`CLAUDE.md` stays the canonical guide to each subsystem** - read the section for the area
you are about to edit (map below) before you change it. Keep this file under 8 KiB.

## Before changing anything

1. Search the **symptom index** at the top of `ERRORS.md` for what you are seeing.
2. Read the `CLAUDE.md` section for the files you will touch (map below).
3. `git fetch` and `git status -sb`: the other agent works in this repository at the same
   time, from its own worktrees, and branches move under you.

## Working together

- **Jeevan owns the product and does the merging** (`gh pr merge`). Neither agent merges,
  force-pushes or deletes a branch. Push only to a named branch: `git push origin <b>:<b>`.
- **Branches:** `claude/<feature>` and `codex/<feature>`, each from current `main`. Keep out
  of the other agent's branches and worktrees, except a synchronization agreed in the log.
- **The pair log** is `CHANGES_MADE.md` on the `claude/pair-log` branch: dated
  `Claude -> Codex` / `Codex -> Claude` entries, appended at the end, earlier ones kept.
- **Debate, don't defer.** A proposal comes with its evidence. The answer is *agree*,
  *disagree because <evidence>*, or *agree if <change>*. Silence is not agreement: a shared
  decision gets one reply before code lands. Settle it with a measurement - a probe, a test
  that fails, a live run. If the evidence cannot decide, write both options for Jeevan.
- **Reviews are adversarial.** Run the branch's tests, undo at least one guard and watch a
  test fail, then post `gh pr comment <n>` with `Verdict: approve at <sha>` or
  `Verdict: request changes` plus a reproduction. A verdict covers that exact head only.
- **Hand-off at the end of a session:** a pair-log entry saying what is open, what waits on
  whom, and what you did not finish.

## Always

- **Zero required dependencies.** A heavy capability is an optional extra that fails with a
  clear `ToolResult.failure` and an install hint, never a crash.
- **The approval gate decides risk:** read-only or local = LOW; a network read = MEDIUM;
  changing anything outside the laptop = HIGH/CRITICAL, which raises an approval card.
- **Tools return `ToolResult`** (`ok`, `message`, `data`); network and engine calls sit behind
  an injectable backend so the success path is tested offline.
- **Every swallowing `except` records the reason** (`failures.record_failure`).
- **Never commit secrets:** scan the staged diff for `nvapi-` and the Gmail app password.
- **Match the surrounding code:** `from __future__ import annotations`, full type hints, and
  a comment only where the reason is not obvious.

## Testing and verifying

- `python -B tests/run_tests.py` runs the suite in an isolated configuration (about five
  minutes). It reads **only its first argument**, so run one file per call:
  `python -B tests/run_tests.py test_planner.py`.
- Run the **full suite** before pushing a change to `agents/orchestrator.py`, a dispatcher,
  `access.py` or `webui_assets/`: their structural guards live in other test files.
- Browser checks are opt-in: set `JARVIS_BROWSER_TESTS=1`.
- A fix comes with a test that **failed before** it. Undo each guard in turn and watch a test
  fail; a guard nothing catches is not tested.
- Live checks run on a throwaway instance with **both** `LAPTOP_AGENT_PORT` and
  `LAPTOP_AGENT_DATA_DIR` set (the port alone writes into the real store), stopped afterwards.
- Call a model before wiring it in, and pace live model calls about 12 seconds apart.

## Where the detail is: sections of CLAUDE.md

| You are editing | Read first |
|---|---|
| `planner/heuristic.py`, routing in `planner/openai_compatible.py` | "Non-negotiable conventions" item 5; "Everyday requests" |
| a tool in `tools/` | its entry in "Architecture (map)" |
| `knowledge.py`, `terms.py`, `context.py`, `embeddings.py` | their entries in "Architecture (map)"; "Session context" |
| model tiers, providers, reply length | "LLM brain — tiered models"; "How long a reply may run" |
| `reasoning.py` (agent mode) | "Two autonomy layers"; "The agent trusts a reply only up to its first runnable ACTION" |
| `webui.py`: server, caching, LAN, accounts, sessions | "Running it" |
| `access.py`, personal accounts | "A `personal` account is the assistant, not the machine" |
| `webui_assets/` (page, voice, orb, meter) | the "Voice…", "Barge-in…", "The dock…" and "Orb focus…" paragraphs; "Nothing in the page may assume a secure context" |
| `analytics/` | `docs/analytics.md`, `docs/forecasting.md` |
| the test runner itself | "The runner makes `os.startfile`"; "A failing run writes `test-failures.log`" |
| packaging | "A packaged app searches `sys._MEIPASS` too" |

## Jeevan's setup

- Windows. His terminal is **Windows PowerShell 5.1**: give
  `foreach ($n in 1,2) { gh pr merge $n --merge; if ($LASTEXITCODE -ne 0) { break } }`,
  never a bash `for` loop or `||`.
- Durable project notes live in his Obsidian vault, root
  `F:\obsidian\Claude mem-Obsidian main memory\Claude Mem` (this project: `Personal AI Agent\`).
