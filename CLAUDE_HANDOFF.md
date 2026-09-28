# Draft message to Claude

Claude, please work with Codex as a pair of developers on J.A.R.V.I.S. We share
responsibility for the app and alternate implementation and review for each feature.

Codex reviewed the project from `de66e17` (`claude/meter-dock`) and prepared a local
`codex/collaboration-handoff` branch in a separate worktree:
`C:/Users/barla/.codex/worktrees/collaboration-handoff/codex new project`.
The user's checkout at `E:/projects/codex new project` was left on its existing branch.
Use `git log -1 codex/collaboration-handoff` and
`git diff de66e17..codex/collaboration-handoff` to inspect the committed handoff locally.
If you cannot access that repository, ask for the branch/patch; it has not been pushed.

Please begin by reading `CLAUDE.md`, `MEMORY.md`, `ERRORS.md`, `README.md` and
`CHANGES_MADE.md` on that branch. Treat `REVIEW_REPORT.md` as historical evidence.
The handoff contains the source map, exact changes, proposed feature order, test evidence,
and a reusable feature record. This batch changes documentation only: current approvals,
reminder delivery, shared context wiring and native speech are now described accurately.
Its four targeted test runs passed 57 tests; it does not claim a new full-suite/CI pass.

First, review DOCS-01 and return any corrections with file references. Then confirm the
status and owner of `claude/tests-open-nothing`, the reminder/DST/clock branches, and the
voice meter/dock branches. Codex found existing work and deliberately did not duplicate it.
Proposed next priority is test desktop isolation, followed by reminder/time reliability
and voice usability. Please challenge that order if your current work or user feedback
provides better evidence.

For each feature:

1. Record its user outcome, acceptance criteria, owner, reviewer, branch/worktree, base,
   owned files/functions and dependencies in `CHANGES_MADE.md` before coding. Mark proposed
   assignments clearly; acknowledge ownership before treating a task as accepted.
2. Work in your own `claude/<feature>` branch and separate worktree; Codex uses
   `codex/<feature>`. Never switch or overwrite the other agent's checkout. Coordinate
   shared orchestrator, routing, web UI, test-helper and documentation edits explicitly.
3. Agree API/result/history shapes and storage, approval and cancellation behavior before
   parallel work. Preserve Python 3.11+, zero required dependencies, injectable IO backends,
   `ToolResult`, shared `app.build_context()` wiring and the existing approval boundary.
4. Hand off small commits with exact diffs, test commands/results and known limits. Use
   isolated data, fixed clocks and IO fakes. Browser flows need browser checks; mocked
   speech does not prove microphone hardware works. Check every CI job after a push.
5. Act as each other's reviewer: identify issues, explain disagreements with evidence,
   resolve findings and update the log. Put accepted architectural decisions in
   `MEMORY.md` and confirmed failure lessons in `ERRORS.md`.
6. Preserve user work and obey existing authorization requirements for integration and
   external actions. Do not auto-merge, force-push, delete branches or change secrets.

Our durable communication is the repository handoff plus exact commits/patches.
Uncommitted files in separate worktrees are not automatically shared. Append a dated
`Claude -> Codex` reply under the relevant feature record, identifying the commit reviewed,
and relay it through the user or an authorized channel. Do not assume a live messaging
connection exists or that a draft assignment has already been accepted.

Please reply with: reviewed commit; DOCS-01 findings; your active branches and owned files;
your proposed next feature; the task you want Codex to implement or review; acceptance
criteria; and any blocking interface decisions. We should leave every feature with a
clear owner, a tested result, peer review and an explicit next step.

---

Status: drafted by Codex on 2026-09-28; not sent. Claude has not yet replied or accepted
any proposed assignment. See [CHANGES_MADE.md](CHANGES_MADE.md) for the working record.
