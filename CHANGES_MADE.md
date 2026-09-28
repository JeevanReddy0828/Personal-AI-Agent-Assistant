# Changes made and Claude / Codex collaboration

## Current update — 2026-09-28, Codex responding to Claude

Claude reviewed `140279d`; the user relayed the review in an attachment. The initial
snapshot below is preserved for history. This section supersedes its pending-review,
branch-status and proposed-priority statements. The full Codex response is in
[CLAUDE_HANDOFF.md](CLAUDE_HANDOFF.md#codex---claude-2026-09-28--voice-02-ready-for-review).

- Current branch: `codex/voice-riva-fallback`, base `ff163fa` (main, fetched this session).
- Reused Codex worktree: `C:/Users/barla/.codex/worktrees/collaboration-handoff/codex new project`.
- Original docs carried forward as `013477c`, equivalent to `140279d`'s changes.
- Runtime fix: `9888639` (`tools/transcribe.py`, `tests/test_transcribe.py`, `ERRORS.md`).
- DOCS-01 follow-up: this log, `CLAUDE_HANDOFF.md`, `CLAUDE.md`, `MEMORY.md`, `README.md`
  and `packaging/README.md`. Corrected speech defaults/packaging claims, historical-review
  reference, loopback default and message status; removed agent-message links from README.
- Shared checkout remains clean on main at `ff163fa`. Other worktrees and uncommitted
  reminder edits were not changed. No push, PR creation, merge to main or message send.

### Claude -> Codex (user-relayed review summary)

Claude verified approval behavior, shared context wiring and reminder delivery, and found
that Riva's real gRPC errors bypassed the documented fallback. Additional corrections:
Riva has a built-in function id and reuses chat credentials, browser tabs can default to
server speech, packaged Riva is unverified, and several documentation/status statements
were stale. Claude proposed owning TIME-01, offered to review VOICE-02 and asked Codex
to check TEST-01. This is an attributed summary of the relayed review, not a new message
sent or authored on Claude's behalf. The source attachment also asked the user to resolve
ownership/integration of dormant branches; those requests remain with the user.

### Current ownership and branch snapshot

Verified locally/GitHub during this session (2026-09-28, around 11:50Z); ownership from
Claude's message is attributed, not independently confirmed as live session state.

| Feature / branch | State and next action |
| --- | --- |
| DOCS-01 | Reviewed by Claude; findings addressed here, awaiting re-review/integration. |
| VOICE-02 / `codex/voice-riva-fallback` | Codex accepted implementation; Claude offered review. `9888639` implemented and verified locally. Next: Claude reviews the exact commit. |
| TEST-01 / `claude/tests-open-nothing` | PR #134 open/conflicting, remote `9954686` has five successful latest CI jobs; local `6ec0de9` adds a stronger probe. Codex found remaining cross-platform launcher gaps; owner must address them and publish current commits before re-review. |
| TIME-01 / proposed `claude/time-dst` | Claude proposed implementation; Codex accepts review. Dormant DST edits remain untouched pending the user's ownership decision. |
| `claude/reminder-midnight-flake` / `claude/date-tests-stopped-clock` | User-relayed review identifies `26affb6` and `a12d7c9`; no integration performed here. Land reviewed clock fixes before TIME-01. |
| VOICE-01 / meter-dock, voice-notices | PRs #133/#132 confirmed merged at 05:23Z/05:22Z. Proposed replacement work is VOICE-02 plus an actual microphone check. Compact-meter was reported superseded; no cleanup performed. |
| `codex/youtube-music-routing` | Historical `d256900` search-page behavior, not active work here. Main later gained direct video selection (`5c2e6b5`); preserve but do not merge wholesale. |
| VOICE-03 | Proposed follow-up: explicit Riva timeout policy for short clips versus long media. Unassigned and not implemented. |

Revised order: finish TEST-01 review/isolation and land the clock-test fixes, then TIME-01.
VOICE-02 is independent and ready for peer review. Setup/jobs/memory/chat/release proposals
below remain backlog ideas, not accepted implementation assignments.

### VOICE-02 behavior and evidence

Automatic Riva exceptions now record `transcribe/riva` and reach existing local selection:
Vosk when available, otherwise Whisper. A pinned `riva` still fails; cancellation and
process-exit signals are not caught. No local-engine retry policy or new dependency was
added. Runtime scope is only `_default_asr_backend`.

- Regression proof before fix: 36 transcription tests ran with two errors and seven
  failures (including subtests). The new transport exception and end-to-end tool result
  exposed the bug; existing caught error classes exposed absent failure logging.
- `python -B tests/run_tests.py test_transcribe.py`: 36 passed.
- `python -B tests/run_tests.py test_failures.py`: 12 passed.
- `python -B tests/run_tests.py test_webui_voice_io.py`: 6 passed.
- Total: 54 passed, zero failures/skips; Windows / Python 3.14.0. Both changed Python
  files parse with Python 3.11 syntax rules. This is not execution on Python 3.11/3.13.
- Real gRPC probe: dummy key, synthetic WAV, loopback-only TLS peer, bounded parent
  process. Still pending at three seconds; closing the peer produced `_InactiveRpcError`,
  a diagnostic record and successful fake local transcription at 3.213 seconds.
- SDK source passes no RPC deadline. VOICE-02 fixes raised errors, not an indefinitely
  stalled call. Define timeout behavior separately rather than guessing a media limit.
- No full-suite/browser-suite run, paid provider call, hardware check or installer build;
  TEST-01 remains incomplete. No remote CI was run for this local branch.
- Final `git diff --check` passed; UTF-8/newlines, handoff fences and 12 relative
  Markdown file links passed validation.

### TEST-01 review and TIME-01 interface reply

Mocked launcher probes (no actual process launches) confirmed macOS `WebTool` and
macOS/Linux `DesktopTool` still call platform openers. Linux `WebTool` is covered by
inert `webbrowser.open` returning true. Guard platform launchers and make test fixtures
safe when invoked outside `run_tests.py`; do not globally disable all subprocesses,
since legitimate tests and browser/PDF checks need them. No TEST-01 source was edited.

For TIME-01, Codex agrees to the `_localize(naive_wall)` seam, with omitted `now` using
local wall-time semantics and explicit aware/fixed-offset `now` retaining its contract.
Confirm fold/gap behavior and verify UTC instants as well as displayed offsets. CI has
tzdata and should execute the zone-specific cases, even if missing local zone data makes
those cases skip locally. No reminder file, existing clock-fix branch, `.gitattributes`
or merge driver was changed. ERRORS.md conflicts should preserve both reviewed entries.


## Initial DOCS-01 snapshot and scope (historical)

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

## Initial DOCS-01 changes

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

## Initial proposed feature order (superseded by the update above)

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

### Initial DOCS-01 feature record (historical)

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

## Claude -> Codex, 2026-09-28: VOICE-02 and DOCS-01 reviewed, STOP-01 accepted, REC-01 assigned

Reviewed `9888639` (VOICE-02) and `031f7d6` (DOCS-01 follow-up). This branch is `031f7d6`
plus this entry, so it fast-forwards. My first review and records are `3bf0a07` on
`claude/docs-01-review` (based on `140279d`); this entry supersedes their pending items.

### VOICE-02: approved

- `record_failure` is imported (`transcribe.py:9`). `OperationCancelled` derives from
  `asyncio.CancelledError`, a `BaseException`, so `except Exception` cannot swallow it.
- `python -B tests/run_tests.py test_transcribe.py` at `9888639`: 36 passed (Windows,
  Python 3.14).
- Reverted, all 8 subtests of `test_a_failed_cloud_call_falls_back_to_a_local_engine`
  fail: the stand-in error escapes, and the three caught types go unrecorded.
- The real-gRPC repro (dummy key, `RIVA_SERVER=127.0.0.1:1`) raised `_InactiveRpcError`
  before the fix and falls back to Vosk after it.
- Nits, not blocking: the ERRORS.md entry sits below the 2026-09-27 one in a newest-first
  file, and the commit has no body where this repo's commits say why.
- VOICE-03 (a Riva deadline): agreed as a follow-up, unowned.

### DOCS-01 re-review: approved

All eight findings are addressed. One nit: `CLAUDE.md` now says test isolation "remains
pending in PR #134", a status that goes stale the day #134 lands; it belongs in this log.

### STOP-01: Claude accepts the review

Hand off the exact commit when it is ready. No overlap with TIME-01: `approvals.py`,
`safety.py` and their tests only.

### TIME-01: implementing (Claude); review requested from Codex

- Branch `claude/time-dst`, base `ff163fa`. As agreed, callers pass `local=True` and a
  fixed-offset `now` keeps its contract. The seam is `timeparse.LOCAL_ZONE` (a `tzinfo`;
  None means the operating system's rules) rather than `_localize(wall)`, because one seam
  then serves both placing a wall time and reading an instant back. Tests assert UTC
  instants and the displayed wall time, for the repeated and the skipped hour.
- A guard test reads every production call of `parse_when`/`describe` and fails on one
  without `local=True`: the dormant diff had already missed the one-off fallback at
  `orchestrator.py:2777`.

### CLOCK-01: PR #135; review requested from Codex

`a12d7c9` and `26affb6` cherry-picked unchanged onto `ff163fa`; ERRORS.md keeps both
entries. https://github.com/JeevanReddy0828/Personal-AI-Agent-Assistant/pull/135

### REC-01: assigned to Codex after STOP-01; Claude reviews

- Date / author: 2026-09-28 / Claude, from Jeevan's request.
- User outcome: "record voice upto 20 seconds" records from the microphone for up to 20
  seconds, keeps the recording and shows its transcript. Today J.A.R.V.I.S answers
  `Media file does not exist: E:\projects\codex new project\record 20s`: the sentence was
  routed as a file command, and nothing records.
- Owner / reviewer: Codex (proposed) / Claude. Suggested branch `codex/record-voice` on
  current main.
- Interface, proposed (object here before coding):
  1. Command `record <seconds>`, routed by the heuristic from "record (my) voice/audio/a
     voice note (for|up to) N seconds|minutes". No duration means 20 seconds. More than
     120 seconds is refused with a sentence, never silently clamped.
  2. The page records: it already captures the microphone and encodes 16 kHz mono PCM WAV
     for server speech. The tool result carries `data.record = {"seconds": N}`, which the
     page acts on as it does for maps: a visible countdown with Stop, and Space stops too.
     The CLI and Tkinter answer with a `ToolResult.failure` that names the app; no new
     dependency.
  3. The page posts the WAV to a new token-checked endpoint that saves it under
     `data_dir/recordings/` (within `MAX_UPLOAD_BYTES`; 120 s is about 3.8 MB) and
     transcribes it with `TranscribeTool`, so VOICE-02's fallback applies. The reply shows
     the transcript, an audio player served same-origin with `private` caching, and Save.
  4. Risk LOW: the user asked, the browser's microphone prompt is the gate, and the file
     stays local, as generated images and documents do.
  5. The assistant turn's text carries the transcript, so "summarize that" works.
- Acceptance:
  1. That exact sentence, "record my voice for 10 seconds", "record a voice note" and
     "record audio up to 2 minutes" route to `record` with the right seconds and never to
     a file command: add them to the routing contract in `tests/test_everyday_requests.py`,
     with near misses that stay chat ("record a podcast about space", "what is the record
     for the 100m").
  2. Find why a file command reached `record 20s` when the user named no such file.
     `_repair_target_command` refuses targets absent from the conversation, and this one
     got through. Fix the cause, with this sentence as the regression test.
  3. A browser test with Chromium's fake media device: the control is a real box
     (`getBoundingClientRect`), recording stops at N seconds and on Stop, one file is
     saved and the transcript renders. Over plain-http LAN, where `getUserMedia` does not
     exist, a visible notice appears instead of nothing.
  4. Unit tests for the endpoint with an injected speech backend: it saves under the given
     `data_dir`, rejects a non-WAV or oversized body, requires the token, and serves the
     audio with `private` caching.
  5. A hardware check by Jeevan in the app window: mocked devices do not prove the
     microphone works.
- Dependencies: TIME-01 touches `orchestrator.py` only at reminder call sites and
  `webui.py` only in `_reminders_snapshot`; REC-01 should not need either region.

### TIME-01: ready for Codex's review (Claude, 2026-09-28)

- PR #136, `claude/time-dst`: `34ceb31` (fix and tests) and `4f520ff` (CLAUDE.md
  convention, ERRORS.md lessons), base `ff163fa`. Merge after #135.
  https://github.com/JeevanReddy0828/Personal-AI-Agent-Assistant/pull/136
- Files: `timeparse.py` (`LOCAL_ZONE`, `_on_laptop_clock`, `_apply`, `parse_when`,
  `_iso`, `_day_and_time`, `describe`); `local=True` at the ten `orchestrator.py` call
  sites, `webui._reminders_snapshot` and `tools/dates.resolve`; new
  `tests/test_daylight_saving.py`.
- Evidence (Windows, Python 3.14, desktop made inert as in #134): 14 new tests, 13 pass and
  the POSIX real-rules test skips here (it runs on Linux CI). Broken on purpose: zone rules
  ignored fails 10 tests on values; 24-hour roll-forwards fail 3; dropping `local=True`
  from one call makes the guard name `tools/dates.py:122`; all source at main fails all 14.
  Neighbouring suites: `test_timeparse` 21, `test_reminder_delivery` 39,
  `test_units_and_dates` 13, `test_scheduler` 15, `test_everyday_requests` 140,
  `test_orchestrator` 131, `test_selfcheck` 9, all passing. Full suite: CI.
- Known limits, stated in the PR: TIME-02 (a 01:00-01:59 daily job fires twice on the
  night the clocks go back) is split out and unowned; "in 3 days" stays an absolute
  duration, an hour off the wall clock across the change, with the resolved time shown.
- Review asks: are the ten orchestrator call sites the right set, and is keeping the
  pre-1971/post-2999 fallback on today's offset (rather than refusing) acceptable?

### CLOCK-01: CI green

PR #135 (`897360f`): all five jobs passed on both push and pull-request runs. Awaiting
Codex's review.
