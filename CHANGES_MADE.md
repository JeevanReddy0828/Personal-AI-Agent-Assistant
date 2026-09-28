# Changes made and Claude / Codex collaboration

## Snapshot and scope

- Author: Codex. Review date: 2026-09-28.
- App: J.A.R.V.I.S (`laptop-agent` 0.43.0), local-first Python 3.11+ assistant.
- Reviewed base: `de66e1789503bc567ae22c57f2ec1db7597d3d77`, taken from the user's
  clean `claude/meter-dock` checkout. This is a local snapshot, not verified remote main.
- Working branch: `codex/collaboration-handoff`.
- Isolated checkout: `C:/Users/barla/.codex/worktrees/collaboration-handoff/codex new project`.
- Original checkout: `E:/projects/codex new project`. Its branch and files were preserved.
- Scope: focused architecture/documentation review and collaboration setup. Inventory:
  75 Python source files and 84 `test_*.py` files. This was not an exhaustive audit of
  every function, a new product feature, or live integration certification.

Read first: [CLAUDE.md](CLAUDE.md), [MEMORY.md](MEMORY.md), [ERRORS.md](ERRORS.md),
[README.md](README.md). The dated [REVIEW_REPORT.md](REVIEW_REPORT.md) preserves past
acceptance evidence. The [draft message](CLAUDE_HANDOFF.md) starts the collaboration.

## What changed in this branch

| File | Change and reason |
| --- | --- |
| `CHANGES_MADE.md` | Added the current baseline, source map, evidence, proposed roadmap and collaboration protocol. |
| `CLAUDE_HANDOFF.md` | Added a ready-to-send message with explicit first steps and a response format. |
| `CLAUDE.md` | Expanded existing pair-coding guidance with separate worktrees, ownership, review and handoff rules; corrected stale duplicated-wiring advice. |
| `MEMORY.md` | Corrected `AgentContext` wiring: `app.build_context()` is shared by app and tests. |
| `README.md` | Corrected stale approval and reminder limitations; clarified loopback default versus optional LAN pairing; linked the handoff. |
| `packaging/README.md` | Corrected native speech and current Riva/Vosk/Whisper selection behavior. |
| `REVIEW_REPORT.md` | Labeled historical findings and test counts as a dated snapshot instead of silently rewriting old evidence. |

Runtime source, tests, dependency declarations, CI, personal `.env`, application data,
other agents' worktrees and the Obsidian vault were not edited. No push, PR, merge,
deployment, external message or product feature implementation is part of this batch.
The local branch is the reviewable deliverable; inspect its commit with
`git log -1 codex/collaboration-handoff`.

## Current implementation: where to work

| Area | Source and current contract | Relevant existing tests |
| --- | --- | --- |
| Composition and routing | `app.py` builds shared `AgentContext`; `agents/orchestrator.py` dispatches commands; `planner/heuristic.py` handles common requests before model fallback. | `test_orchestrator.py`, `test_command_dispatch.py`, `test_planner.py` |
| Chat and model context | `context.py` normalizes history, ranks chunks and budgets prompts; `planner/openai_compatible.py` owns transport; `model_status.py` and `tracing.py` expose failures/latency. Client history uses `{role, text}`. | `test_context.py`, `test_conversation_flow.py`, `test_model_fallback.py` |
| Web/native interface | `webui.py` serves stdlib HTTP/SSE; `webui_page.py` inlines `webui_assets/app.html`, `app.css`, `app.js` at import. Restart after asset edits; preserve nonce CSP and mutation-token handling. | `test_webui.py`, `test_page_assets.py`, `test_browser_regressions.py` |
| Approvals and cancellation | `safety.py`, `approvals.py`, `cancellation.py`: web MEDIUM requests pass the interface callback; HIGH/CRITICAL wait for the exact approval id, with denial on timeout/no listener. Stop is cooperative. | `test_approvals.py`, `test_security_regressions.py`, `test_reliability_regressions.py` |
| Voice | `voice.py`, `tools/transcribe.py`, and `app.js`: native server-side STT/TTS, browser speech paths, echo handling and provisional pause/resume for barge-in. | `test_voice.py`, `test_transcribe.py`, `test_webui_voice_io.py`, `test_browser_regressions.py` |
| Reminders and scheduling | `timeparse.py`, `reminders.py`, `scheduler.py`, CLI watcher and page polling. Due cards/chimes, permitted browser notifications and voice announcements exist. Stop and delete are distinct. | `test_timeparse.py`, `test_reminder_delivery.py`, `test_scheduler.py`, `test_browser_reminders.py` |
| Durable memory | `storage.py` provides locks, atomic replacement and recovery copies; `memory.py`, `knowledge.py`, `tools/obsidian.py` provide profile/index/vault operations. | `test_knowledge.py`, `test_obsidian.py`, `test_reliability_regressions.py` |
| Jobs and resume workflow | `jobs.py`, `copilot.py`, `tools/jobright.py`, `tools/resume_pdf.py`: leads, stages, keyword scoring, grounded tailoring and verified one-page PDF exports already exist. | `test_jobs.py`, `test_copilot.py`, `test_jobright.py`, `test_webui_pipeline.py` |
| Autonomy and tools | `reasoning.py`, `autopilot.py`, `tasks.py`, `workflows.py`, `tools/`: bounded execution and safe-command restrictions; tools return `ToolResult`. | `test_reasoning.py`, `test_autopilot.py`, `test_tasks.py`, `test_workflows.py` |
| Distribution | `pyproject.toml`, `packaging/`: optional extras, bundled assets/time-zone data and native launcher. No mandatory runtime dependencies. | `test_packaging.py`, `test_page_assets.py` |

## Findings and thoughts

1. **Build dependable everyday flows before expanding the feature list.** The code already
   has reminders, jobs, research, memory and generation. A successful flow should include
   setup, a visible result, failure recovery and a way to cancel. Adding another command
   is less valuable than making those paths consistently usable.
2. **Keep the existing stack.** Python/std-library serving, optional extras, injectable
   tool backends and JSON stores are deliberate constraints. There is no evidence from
   this review that replacing them with a web framework or database would improve the app.
3. **Documentation drift is a concrete coordination bug.** README still described blocked
   web approvals and absent notifications; MEMORY still required duplicate context wiring;
   packaging guidance contradicted the native speech path. These statements are corrected
   using the source, not historical reviews as current truth.
4. **Large shared files need careful ownership.** Orchestrator, heuristic routing, web
   handlers and `app.js` each contain substantial behavior. Avoid a broad rewrite. When a
   feature requires changes there, name the exact functions owned by each agent and review
   extraction separately from behavior changes. The web asset split is already done.
5. **Testing has an active cross-agent dependency.** At this base, `tests/run_tests.py`
   blocks external Python socket connections but does not generally neutralize OS launch
   or media-key calls. The local `claude/tests-open-nothing` branch contains a proposed fix
   at `9954686`, including test coverage. Its merge-base diff was inspected; its live owner,
   remote CI and readiness were not verified. Coordinate with Claude instead of duplicating
   the implementation. A test's claim to be offline is not proof it cannot open an app.
6. **Separate evidence from aspiration.** An older passing suite and 8.5/10 review score
   apply to their dated snapshot. Hardware, authenticated providers and installer behavior
   remain separate acceptance steps. This pass does not confirm their present operation.

## Proposed feature order

These are proposals for discussion, not assigned work or promises of implementation.
Confirm the current branch and owner before claiming any item. Codex and Claude alternate
implementation and review; neither agent is permanently restricted to frontend or backend.

| Priority / ID | Next slice | Acceptance and likely touchpoints |
| --- | --- | --- |
| P0 / TEST-01 | Review and integrate existing test desktop isolation work. | Coordinate `claude/tests-open-nothing`; exercise real routing while spies verify no browser/app launch or media-key change. Run the full offline suite after isolation is confirmed. `tests/run_tests.py`, `test_run_tests.py`. |
| P1 / TIME-01 | Reconcile reminder time-zone and clock-test branches. | Existing branches include `claude/reminder-dst-offset`, `claude/date-tests-stopped-clock`, `claude/reminder-midnight-flake`. Inspect their actual diffs/status first. Fixed-clock cases cover DST transitions, explicit offsets, midnight, recurring weekdays, stop versus delete and no duplicate execution. |
| P1 / VOICE-01 | Finish voice controls across dock positions and window sizes. | Coordinate existing `claude/meter-dock` and `claude/compact-meter` work. Browser/native paths handle genuine interruption, coughs, silence, echo, pending response cancellation and cleanup; controls remain visible/clickable in narrow and orb-focus views. Mock health polling in tests; then perform a hardware check. |
| P1 / SETUP-01 | Make capability setup understandable. | Extend existing health/settings UI: show configured, available, unavailable and degraded states; explain the next action without exposing secrets. Offline startup remains useful. `health.py`, `webui.py`, `app.js`. |
| P2 / JOBS-01 | Improve recovery through the existing lead-to-PDF journey. | Clear status for login failure, absent job descriptions, stale exports and overflow; preserve grounded source excerpts and one-page checks. Mock browser/provider tests plus a separately authorized account smoke test. |
| P2 / MEMORY-01 | Make stored facts and source references easier to inspect. | Build on existing memory/vault UI; distinguish profile facts from retrieved notes, show sources, and verify a correction survives restart. Preserve vault-wide links and existing storage recovery. |
| P2 / CHAT-01 | Measure routing and answer delivery before optimizing. | Compare a fixed corpus of commands, knowledge questions and follow-ups using traces; verify first-token latency, fallback notices, cancellation and original-session ownership. Preserve `{role, text}` compatibility. |
| P3 / RELEASE-01 | Validate the packaged offline experience. | Clean Windows profile: launch, restart/history, assets, named time zones, missing extras, selected STT, TTS and port conflict messaging. Record build/version and failures; signing/release remains separate authorized work. |

A calendar integration is a possible later feature, not an existing capability. Define
provider/account scope and injectable read-only behavior before adding writes. Likewise,
true reminder delivery after the app closes requires a separate background-service design.
Do not imply that the current page polling already provides that capability.

## How we collaborate on every feature

1. **Read and reconcile.** Start with project rules, this log, current Git status and
   `git worktree list`. Compare the actual base with the feature's recorded base. Existing
   branches are coordination signals, not proof of active ownership or completed work.
2. **Claim a bounded slice.** Add a record below with ID, owner, reviewer, branch/worktree,
   base, files/functions, acceptance criteria and dependencies. The other agent must
   acknowledge overlapping ownership before concurrent changes to the same area.
3. **Use separate branches and worktrees.** Use `codex/<feature>` and `claude/<feature>`.
   Branches alone do not isolate concurrent edits in one directory. Keep commits scoped;
   never sweep up another agent's changes or generated screenshots with `git add -A`.
4. **Agree interfaces before parallel implementation.** Record command/API/result shapes,
   history format, approval level, cancellation behavior, storage effects and optional
   dependency behavior. Put permanent accepted decisions in `MEMORY.md`.
5. **Implement and verify.** Reuse shared composition and test helpers. Inject IO fakes;
   use temporary data and fixed clocks. Run affected tests through `tests/run_tests.py`.
   UI behavior needs real browser verification; new integrations need their own checks.
6. **Review as peers.** Implementer hands off exact commits, diff, test evidence, known
   limitations and questions. Reviewer checks correctness, regressions and acceptance,
   then records findings. Address findings explicitly; a proposed fix is not a passed test.
7. **Integrate and update.** Respect repository/user authorization for PRs, merges and
   external actions. Check every CI job when pushed; cancelled is not passed. After an
   authorized integration, update status, decision/failure logs and relevant user docs.

Communication is explicit: this Markdown and Git commits form the durable handoff.
Separate worktrees do not automatically see each other's uncommitted files. Share an
exact branch/commit or patch, integrate it deliberately, and relay the message through
the user or an authorized agent channel. No direct Claude connection or automatic
cross-agent messaging was established by this task.

### Current feature record

- ID: DOCS-01.
- Owner: Codex. Reviewer: Claude (requested in draft; not yet accepted).
- Branch/base: `codex/collaboration-handoff` / `de66e17`.
- Scope: the seven Markdown files listed above; no runtime changes.
- State: implemented and locally checked; awaiting peer review and authorized integration.
- Next action for Claude: review documentation accuracy and proposed priorities; report
  the status/ownership of TEST-01, TIME-01 and VOICE-01 before claiming new code work.

### Copy for the next feature

```text
Date / author:
Feature ID / user outcome:
State: proposed | claimed | implementing | review | blocked | integrated
Owner / reviewer (mark proposed versus accepted):
Branch / worktree / base commit:
Files and functions owned:
Dependencies / interface decisions:
Acceptance criteria:
Changes and commit(s):
Verification: exact commands, platform, counts, failures/skips, artifact paths
Open questions / limitations:
Next action / responsible agent:
```

Append dated `Codex -> Claude` or `Claude -> Codex` replies with the feature ID and
commit reviewed. Preserve previous entries. Resolve simultaneous documentation edits
by retaining both authors' evidence, not by choosing one entire file version.

## Verification for DOCS-01

Executed on Windows with Python 3.14.0 through the project runner:

| Command | Result |
| --- | --- |
| `python -B tests/run_tests.py test_approvals.py` | 8 passed |
| `python -B tests/run_tests.py test_transcribe.py` | 33 passed |
| `python -B tests/run_tests.py test_packaging.py` | 6 passed |
| `python -B tests/run_tests.py test_page_assets.py` | 10 passed |

Total: 57 tests passed; zero failures or skips in these four runs. Reminder notification
and shared-builder corrections were verified by source inspection, not a live UI test.
The full suite, browser suite, remote CI, Python 3.11/3.13 execution, actual audio devices,
provider calls and installer were not run in this documentation batch. Full-suite work
is deferred to TEST-01 to avoid OS side effects already being addressed on Claude's branch.
Final checks: `git diff --check` passed; all relative Markdown link targets in the
seven changed files exist; both new documents have balanced fenced blocks. Git scope
is seven Markdown files only. The original checkout remains clean on
`claude/meter-dock` at `de66e17`.
