# `docs/design/` — how each subsystem works, and why

## Purpose

The detailed design notes for J.A.R.V.I.S: what each subsystem does, the decisions behind it,
the measurements that settled them, and the mistakes not to repeat. They used to be one
127 KB `CLAUDE.md` that every agent session re-read whole; on 2026-10-06 that file was cut to
its core and every other paragraph moved here **unchanged**, one file per area.

## How it connects

- `CLAUDE.md` (repository root) is the short core: rules, the request flow, what works today,
  and a table pointing at the file to read for each area.
- `AGENTS.md` carries the same map for Codex, and `tests/test_agents_md.py` checks that every
  section and file it names exists.
- The folder READMEs (`src/laptop_agent/*/README.md`, `tests/README.md`, …) say what each
  folder holds; these notes say why it is built that way.
- `ERRORS.md` holds the failure log; its symptom index is the place to start when something
  misbehaves.

## Usage

Before changing an area, read its file below. When you learn something future sessions must
not re-learn, add it to the file for that area (one paragraph, with the measurement), not to
`CLAUDE.md`.

## Contents

| File | What it covers |
|---|---|
| `conventions.md` | The seven rules in `CLAUDE.md`, with the measurements behind them (routing first, `is_plain_question`, the routing deadline). |
| `architecture-map.md` | The long map: every tool and subsystem, with the decisions that shaped it. |
| `routing.md` | Everyday requests: polite prefixes, the prose guard, follow-up answers, negation, the shell backstop, the open substitution limit. |
| `orchestrator.md` | `handle()`, session context, the freshness path, the search backend, `AgentContext`, the two autonomy layers, agent replies. |
| `models.md` | Model tiers, reply length, `reasoning_budget`, busy versus broken tiers, persistence, the OpenRouter backup, what the chat tier is told. |
| `web-server.md` | Routes and SSE, ports, the request-body rule, page caching, LAN mode, the Setup report. |
| `accounts.md` | Accounts and sessions, the `personal` role, account management, Google identity. |
| `web-ui.md` | Maths and diagrams, secure-context rules, the desktop window, the page assets, design, orb focus, the routed pages. |
| `voice.md` | Speech output and barge-in, the meter and dock, voice notices, speech-to-text engines, packaged models, recordings, the Riva deadline. |
| `analytics.md` | Notes on wiring the forecasting and diagnostics cores. |
| `metrics.md` | GPU counters and labels. |
| `running.md` | Start commands and why a throwaway instance needs its own data directory. |
| `testing.md` | What the test runner isolates, and `test-failures.log`. |
| `watch-outs.md` | Working alongside Codex, and the open watch-outs list. |
