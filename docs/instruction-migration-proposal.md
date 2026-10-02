# Draft heading-to-destination map for CLAUDE.md

Proposal only: no instruction files or source modules are created/moved by this document.
Inventory baseline: main 0ce6847. Reconcile #175/#179/#181 and subsequent changes before
migration. Every source heading appears below; mixed headings still require a paragraph
inventory. This draft is not a claim that lossless extraction has already been completed.

## Loading mechanism and canonical ownership

Keep one canonical rule body in CLAUDE.md at each applicable scope, with an AGENTS.md
pointer instructing Codex to read it. The root AGENTS.md must also require inspection of
applicable nested guidance before edits and map flat files to their topic. A pointer is
an instruction to read, not a promise of automatic include expansion. Target the root
CLAUDE.md core at <=8 KiB; keep its AGENTS.md wrapper small and measure their combined size.

Official Codex documentation describes startup discovery from root to the working directory,
one selected file per directory, with a default 32 KiB combined cap. It does not establish
Claude-style read-triggered discovery of deeper instructions. Validate both clients in
cold-start exercises; do not silently change a user-level fallback-name configuration.
[Official OpenAI guide](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

## Heading inventory

Line ranges refer to 0ce6847, ending immediately before the next heading, including nested
headings. They partition the original body instead of double-counting parent sections.

| Original heading | Baseline lines | Proposed canonical destination |
| --- | --- | --- |
| CLAUDE.md | 1-2 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| ANALYTICS-04 update — 2026-10-01 | 3-12 | src/laptop_agent/analytics/CLAUDE.md; retain API detail in docs/analytics.md |
| Agent Operating Principles | 13-14 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 1 — Foundation | 15-16 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Identity | 17-21 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Non-Negotiable Rules (Karpathy) | 22-29 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 2 — Communication | 30-36 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 3 — Core Process | 37-47 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 4 — Scope & Boundaries | 48-58 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 5 — Safety & Guardrails | 59-68 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 6 — Code Quality & Efficiency | 69-77 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 7 — Memory & Stack | 78-90 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Part 8 — Operational Modes | 91-100 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| What this is | 101-112 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Non-negotiable conventions | 113-185 | Root core for cross-cutting rules; domain details to the applicable nested topic (paragraph audit required) |
| Architecture (map) | 186-643 | Root compact map; src/laptop_agent/CLAUDE.md routing index; planner/, agents/, tools/, analytics/, webui_assets/ topics; flat-module topics (paragraph audit required) |
| LLM brain — tiered models | 644-963 | src/laptop_agent/planner/CLAUDE.md; routing/agent paragraphs to agents/CLAUDE.md; configuration and voice exceptions to flat-module topics (paragraph audit required) |
| Running it | 964-1417 | Root minimal start/test/isolation commands; tests/CLAUDE.md, webui_assets/CLAUDE.md, tools/CLAUDE.md and flat-module topics for embedded decisions (paragraph audit required) |
| Working alongside another agent (Codex) | 1418-1425 | Root CLAUDE.md concise core; unchanged original retained in historical snapshot |
| Outstanding / watch-outs | 1426-1450 | Root ownership/status pointers; each open technical watch-out beside its subsystem (paragraph audit required) |
| Recorder integration (REC-01, 2026-09-28) | 1451-1469 | src/laptop_agent/tools/CLAUDE.md media section; webui_assets/CLAUDE.md recorder UI; flat webui/voice topic |
| Riva deadline (VOICE-03, 2026-09-28) | 1470-1482 | src/laptop_agent/tools/CLAUDE.md transcription section |
| AUTH-01 phase 2a — Google identity handoff (Codex, 2026-09-28) | 1483-1516 | src/laptop_agent/instructions/auth-safety.md, required by the flat-module index |
| GPU-01 review follow-up (2026-10-01) | 1517-1521 | src/laptop_agent/instructions/metrics.md plus webui_assets/CLAUDE.md labels |

## Directory scopes and the flat-module exception

Existing directories support nested rules without moving Python modules:

| Edit region | Required rule body |
| --- | --- |
| planner/ | Transport, deadlines, truncation, tier degradation, routing experiments |
| agents/ | Dispatch, agent loops, cancellation, approval/account boundaries |
| tools/ | Injectable IO, optional dependencies, ToolResult, media/files/documents/network contracts |
| analytics/ | Pure numerical APIs, validation/backtests, diagnostics and docs/analytics.md links |
| webui_assets/ | CSP/escaping, async request state, views, voice UI, browser checks |
| tests/ | Isolated runner, no real services/desktop actions, meaningful undo checks |

Flat siblings cannot be distinguished by a directory loader. The short src/laptop_agent/
CLAUDE.md index must require these shared topic documents by edit target:

| Flat-file targets | Proposed topic |
| --- | --- |
| knowledge.py, terms.py, embeddings.py, context.py | instructions/retrieval.md |
| accounts.py, approvals.py, safety.py, vault/account helpers | instructions/auth-safety.md |
| voice.py, webui.py recorder/speech routes | instructions/voice-media.md |
| webui.py, webui_page.py, window_fx.py | instructions/web-server.md |
| storage.py, jobs.py, scheduler.py, reminders.py and other persistent stores | instructions/storage-scheduling.md |
| metrics.py and telemetry consumers | instructions/metrics.md |

A change spanning regions reads each applicable topic. Shared invariants have one canonical
home and a short cross-reference in other scopes. Do not move code merely to make loaders
work, copy full rule bodies under both filenames, or assume flat-module topics auto-load.

## Losslessness and audit gates

1. Save the exact pre-migration CLAUDE.md as a versioned historical snapshot with its hash.
   Link it from the root index. Preserve existing docs and their history.
2. Build a paragraph inventory for each mixed heading above: original span/hash, destination,
   current-rule versus historical rationale, and any cross-reference. Account for every span.
3. Claude audits the map and rule inventory before migration. Retain original heading aliases
   where practical; otherwise map every old heading to a new target and check links.
4. Measure root size and discovered root-to-leaf chain size. Verify both instruction clients
   identify the rule sources rather than inferring success from file names.
5. Cold starts: Codex audits auth and Claude audits retrieval/UI (or exchange ownership).
   Each must locate a prohibition, a previously rejected approach and the test command before
   proposing a change. Include a root-started Codex task editing a deeply nested file.
6. Claude tells Jeevan before the agreed migration; PR merge order stays with Jeevan.

Open decision: tools/ has many unrelated integrations, so its nested core should route to
small topics rather than becoming another giant always-loaded file. Choose the split after
paragraph sizes are measured. The map deliberately does not claim all content fits 8 KiB yet.
