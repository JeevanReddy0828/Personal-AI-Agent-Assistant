# MEMORY.md — decision log

Permanent architectural facts and decisions. Append when a choice is made that future
sessions must respect. See `CLAUDE.md` for the operating principles and full architecture.

## Locked stack / constraints
- Python 3.11+, **zero required runtime dependencies** (`dependencies = []`). New heavy
  capability → optional extra + graceful fallback (clear `ToolResult.failure` + install hint).
- Tools return `ToolResult` (`tools/base.py`). Network/IO behind an **injectable backend**
  so the success path is unit-tested offline.
- Risky actions (send mail, write/move files, downloads, launch apps, shell, browser state)
  go through `safety.ApprovalGate` with the right `RiskLevel`.
- LLM access uses the project's own OpenAI-compatible transport (`planner/openai_compatible.py`),
  **never** the `openai` SDK. Chat escalates fast→smart→ultra→OpenRouter with graceful fallback.
- Persistence is JSON files under `data_dir` (no DB). Web app is one stdlib-served page,
  binds loopback, with per-process browser mutation tokens and origin checks.
- `AgentContext` is a frozen dataclass wired in one place, `app.build_context`; the test
  builder starts from it, so a new field is added there and nowhere else.

## Decisions
- 2026-10-02: **Docs: one place per kind of knowledge (Claude, at Jeevan's request).**
  Something broken → `ERRORS.md`'s symptom index first (one line per lesson: symptom → cause
  → guard), detail in its dated sessions; a decision → here; how a subsystem works →
  CLAUDE.md; agent hand-offs → `CHANGES_MADE.md` on `claude/pair-log`. After any fix, add
  the index line. CLAUDE.md is re-read whole by every new session and worktree (108 KB), so
  slimming it to an index with topic files is proposed, after the open PRs that edit it merge.
- 2026-10-01: **Retrieval reads documents as lines (#165, #169; Claude).** `terms.sentences`
  is the one sentence splitter: file summaries skip tables and code, knowledge answers keep
  headings, table rows (`cell: cell`) and code lines but never a mermaid source. Two-letter
  function words weigh `FUNCTION_WEIGHT` 0.2 in passage scoring only (swept; 0 broke
  follow-up document choice); document search is unchanged.
- 2026-10-01: **Routing (#166-#168; Claude).** A router command equal to the input that no
  dispatcher ran is answered as conversation. News requests are whole-sentence grammar, and
  news the user shares is not a freshness search. Scheduled-jobs phrasings route instantly
  to `schedule list`, which names each job.
- 2026-10-01: **The chat prompt states rules without quoting the replies it forbids (#164)**:
  quoted, the model copied them (17/25 → 1/25).
- 2026-10-01: **Reminder cards: at most three show (one at ≤700px) plus a "+N more" card
  (reminder-stack PR).** The tray sits under approval cards (69 < 70) and under the header
  while the settings popover is open.
- 2026-09-28: **AUTH-01 phase 1 (accounts, sessions, sign-in; Claude).** Accounts switch
  sign-in on (none = unchanged; any = every request, loopback included); first account is
  loopback-only and `dev`; stdlib scrypt N=2^17; server-side sessions persisted as token
  hashes (7-day idle, 30-day absolute); roles `dev`/`personal`, re-read every request.
  Proposed and debated in CHANGES_MADE.md; Google sign-in and Gmail are phase 2.
- 2026-09-28: **AUTH-01 phase 3 (what a `personal` account may do; Claude).** It is the
  assistant, not the machine: refused at four points, each for a path the others miss — the
  gate (HIGH/CRITICAL, before anyone is asked), the orchestrator (on the command about to be
  dispatched), the approval broker (its own cards only) and the web server (an allow-list of
  routes). After Codex's review, commands are default-deny where they are claimed: only forms
  marked everyday are dispatched for it, so an unclassified command is refused at runtime.
  Both command lists mirror the dispatchers exactly, held there by an AST test.
- 2026-09-28: **Both accounts are the owner's** (Jeevan's answer). A `personal` account is
  the owner in a safer everyday mode, not another person, so per-account data isolation is
  dropped: reminders, lists, facts, pictures and documents stay one store and the chat
  prompt carries the owner's facts. What it is refused limits scope, not privacy. Mail
  stays per account all the same (phase 2b): each account's own Gmail consent, never the
  owner's IMAP/SMTP app password as a fallback, since that reaches the whole mailbox and
  the OAuth grant only reads and sends.
- 2026-06: Adopted **Agent Operating Principles** (CLAUDE.md preamble) as the governing
  doc. Codebase already conformed, so adopted going forward — no refactor.
- 2026-06: **Job-search dashboard** initiative. Web UI became multi-page (header nav +
  hash router: Chat/Overview/Jobs). New `jobs.py` (JobTracker pipeline + `/api/jobs`) and
  `copilot.py` (ATS + grounded tailoring + `/api/copilot`). PRs #30–#32.
- 2026-06: Integrated the Agentic-AI-JOB-CoPilot by **porting its stdlib logic** (ATS
  scoring, keyword/grounding) onto our LLM provider — not bolting on its FastAPI/Next/openai
  stack — to preserve the locked stack. (`copilot.py`)

## Everyday requests (2026-09-26) — branch `claude/compassionate-cerf-etqyta`

- **A value with one right answer is computed, never asked of a model**: arithmetic
  (`calculator`), unit conversion (`tools/units.py`), date counting and holidays
  (`tools/dates.py`), time zones (`clock`), coin/dice/numbers (`tools/chance.py`, `secrets`),
  reminder times (`timeparse`). A model only gets what it can actually answer.
- **Honest about what is not connected.** There is no calendar: "what's on my calendar"
  says so and shows reminders; "add X to my calendar" sets a reminder and says that.
  Currency goes to the live-search answer, **not** a dedicated FX API - none was reachable
  to verify from the dev sandbox. The upgrade path is a tool with an injectable backend,
  once someone has seen an API's real response.
- **Reminders are delivered, not just stored**: the page polls `/api/reminders` (it says
  when to look again via `next_in`) and raises a card, a chime, a notification and, in
  voice mode, speech; the CLI runs a watcher thread. Timers and alarms are reminders.
- **`reminder stop` ≠ `reminder delete`.** Stop touches only what is going off or a running
  timer, never a schedule; delete removes, prefers the ringing one, and removing more than
  one asks first (HIGH). Keep that split for any new "turn off"-like verb.
- **The scheduler has `days`** (0=Monday) on the daily kind: weekdays, weekends, named days.
  Persisted only when non-empty, so older jobs read as every day. Repeating reminders and
  alarms use it; the same request twice is one job.
- **Two requests in one sentence split only where every part starts like a request
  (`_REQUEST_START`) and routes through the heuristic alone.** Free-text commands
  (`_WHOLE_ARGUMENT`) never split. Parts run through `_handle(..., _whole=False)`.
- **Follow-up answers are keyed to our own question strings** ("How long should the timer
  run?", "What should I remind you about", "You haven't told me your …", "Which one? …").
  Rewording one of those questions means updating `_follow_up`. History arrives as
  `{"role", "text"}` from the page and the CLI; read it with `normalize_history`.

## CI and packaging (2026-09-17)

- **CI must be green, and `cancelled` is not passing.** The matrix (ubuntu/windows x
  3.11/3.13 + browser) runs fail-fast, so one red job cancels the rest and hides their
  failures. `main` was red from #84 (Windows only) and from #87 on both platforms, through
  #110 - two stacked bugs, an audit-rotation `tail` and a Windows-only `%-I` strftime
  crash. Green again at #111. Read every job's conclusion before concluding why CI is red.
- **`tzdata` is bundled in packaged builds** (`--collect-all tzdata`, both `packaging/*.ps1`)
  and installed in the CI unit job, because Windows ships no time zone database. This does
  **not** change the locked stack: `dependencies = []` still holds and a test asserts it.
  Bundling is the installer's business; the app still degrades gracefully without it.
- **A response is not delivered until the request body has been read.** See CLAUDE.md
  (`_drain_request_body`). Any new rejecting path must go through `_send`/`_json`, never
  write a status line directly, or it reintroduces the connection reset.

## Review stabilization (2026-09-08)

- User authorized the report's remediation queue. See REVIEW_REPORT.md for the baseline
  and acceptance evidence. Preserve zero required runtime dependencies.
- JSON persistence uses atomic replacement, previous-version backups and cross-process
  file locks. Cached stores reload inside their mutation lock.
- Cancellation is cooperative across request context, planner streams, approval gates
  and autonomous steps. Ordinary exception fallback must not swallow OperationCancelled.
- Exported resume prose consists of source excerpts. Do not restore lexical overlap as
  a factual verification claim. Profile links are sanitized, exports version-checked,
  and a PDF is published only after single-page verification.
- Native webview uses the configured stable port and a persistent profile. Keep the
  mobile composer and chat drawer functional and honor reduced-motion preferences.
- Jobright scraping and the lead stage are already implemented; the old proposal to
  add them was stale. Historical rejected records cannot reconstruct missing stage history.
- Run tests through tests/run_tests.py to isolate personal configuration/data. Browser
  checks are opt-in with JARVIS_BROWSER_TESTS=1 and use mocked external integrations.

## Session context (2026-09-10) — branch `claude/session-context`

- The model never sees raw history any more: every prompt goes through
  `context.build_context(history, query, budget)`. Design follows the documented chat-memory
  hierarchy (recent verbatim → older summarized → rest retrieved): the summary buffer pattern
  (LangChain/Mem0), Anthropic's contextual retrieval (chunks indexed with situating context,
  BM25) and conversational query rewriting (resolve "this" before retrieval/research).
  Small sessions go in verbatim (Anthropic: under ~200k tokens just include everything).
  The rolling summary is an injected callable (`register_summarizer`, the orchestrator uses
  the fast tier) and always runs in the background — a request never waits for it.
  Budgets live in `context.py` (`ROUTE_BUDGET` 2.4k chars, `CHAT_BUDGET` 9k, `AGENT_BUDGET` 6k,
  `ADVISOR_BUDGET` 5k). Raise them there, not per call site.
- A router decision of `action=chat` with no `response` is legitimate (a follow-up deferred to
  the answerer): `PlanDecision.is_chat` means `action == "chat"`, and `_route`'s heuristic
  short-circuit checks `fast.response` explicitly. The fast tier answers a text-less chat turn.
- Provider `answer`/`stream_answer` take `context_query=` for synthesized prompts (grounded
  news) so the session context is ranked on the user's words; `_call_with_query` falls back
  positionally for providers/test doubles without it. `refers_back` ignores messages over 60
  words and short messages that start with a command verb; the advisor skips web research when
  the problem refers back. `build_context` is memoized (16 entries) across the tier ladder.
- Every entry point passes the session: `/api/stream`, `/api/agent` and `/api/command` accept
  `history`; the web client sends up to 80 turns (server cap 100); the CLI keeps its own list.
  Adding a new model-facing path means threading `history` through it.

## Live-testing pass (2026-09-09) — branch `codex/review-stabilization-final`

- **NVIDIA models get retired.** IDs return HTTP 410 (Gone) at end-of-life and 404 ("not for
  this account") when unprovisioned. The current key has the **nemotron-3 generation**
  (`super-120b`, `nano-omni-30b`, `lightning-30b`), `llama-3.2-11b-vision`, and
  `deepseek-v4-pro`. `deepseek-v4-pro` returns empty under the app's ultra **reasoning** params,
  so the ultra tier must be a nemotron. All chat tiers are `nemotron-3-super-120b` for now (the
  only reliably fast + reasoning-capable model on the key); ultra just runs it with reasoning on.
  Model IDs live in `.env` (per-user); `.env` changes need a server restart (config reads at import).
- The per-session **API token** rotates on restart; a stale tab self-heals by reloading once on a
  same-origin 403. Keep the token per-process (security) rather than persisting it.
- **Image attachments** route to the vision model (`describe image`, vision-first with OCR
  fallback), not the OCR-only file processor.
- **Complexity routing:** logic/puzzle/multi-step cues escalate to the reasoning (ultra) tier.
  Reply budgets: advisor 4000 tokens, streaming chat 2048 (900 truncated long answers).
- **Voice barge-in** keeps a recognizer alive while speaking (echo-filtered); the manual
  Interrupt button / Space bar are the reliable fallback. Works best with headphones.
- Known model limits (not code bugs): can't reliably honor hard lexical constraints (e.g. "no
  letter e"); the decision-advisor injects assumptions on non-decision "compare X and Y" prompts.

## UI redesign (2026-09-09) — branch `claude/ui-redesign`

- Direction: a **calm, premium dark workspace** (not a sci-fi HUD). Kept: navy/black base, one
  cyan accent, the J.A.R.V.I.S identity, the animated particle orb as the sole glowing focal point.
  Dropped: scan sweep, HUD grid, corner brackets, liquid-glass/metallic buttons, amber/orange
  action colours, all-caps letter-spaced labels, tiny monospaced UI text.
- Layout: slim left rail (New chat + recent chats + status row) · presence panel (orb, soft
  divider, `body[data-core]` drives its ambient glow) · wide quiet chat column · **System status
  drawer** (`#sysDrawer`) for models/usage/vault + tool panels. Voice mode is a pill inside the
  composer; the send button is cyan. Mobile (≤520px) composer is two rows.
- Typography: sans-serif everywhere (Segoe UI Variable → system stack — the CSP is
  `font-src 'self'` and the app is offline-first, so no web fonts); monospace only for model
  names, timings and diagnostics. Green is reserved for healthy/positive status (health dots, high ATS scores).
- The palette lives only in the `:root` tokens; the JS reads `--accent`/`--violet`/`--violet-2`/
  `--voice` via `getComputedStyle` for the orb, and inline styles use the token names directly.
- `PAGE` is read at import: after editing CSS/JS restart the server, and do a real reload —
  a hash-only navigation (`#/chat`) does not refetch the page.

## 2026-09-28 — REC-01 voice notes

On `codex/record-voice`, based directly on main `ff163fa`: browser recording defaults to
20 seconds and refuses durations above 120. Capture and save are local; transcription is
an explicit action because auto/Riva can be hosted. Playback/download survive a speech
backend failure. Recorded assistant messages retain artifact metadata and transcripts
when their chat is reopened; in-flight work never changes ownership to the selected chat.

The server persists recordings beneath its supplied configuration, validates PCM bytes
and duration, and serves them privately. The app's orchestrator now receives config.data_dir
explicitly so its artifact location also follows the supplied configuration. Existing
VOICE-02/STOP-01 changes are separate branches and are not included here. AUTH ownership,
health/setup UI and reminders were left for Claude. Physical microphone/native-window
verification remains Jeevan's task; fake Chromium media proves the browser flow only.

## 2026-09-28 — VOICE-03 bounded hosted speech

Branch `codex/riva-deadline` starts directly from main `ff163fa`. Default Riva budget:
`min(120, max(10, 5 + WAV_seconds/2))`; optional finite override up to 600 seconds. The
SDK exposes an asynchronous future but its blocking helper has no timeout argument.
Use bounded future waits and cancel the actual RPC, close the channel, and propagate
Stop without local fallback. Explicit Riva reports a timeout; auto chooses its existing
local engine. This bounds only hosted waiting, not local ASR or complete file processing.
No live credentials or provider calls were used in verification. VOICE-02 remains a
separate reviewed fallback fix. REC-01 and AUTH-01 are not stacked into this branch.

## 2026-09-28 — Google identity, phase 2a

- AUTH-01 2a is based on Claude's repaired auth-admin 1327dea. Identity uses Google `sub`,
  not email; there is no self-registration or role upgrade. Link/unlink require the
  account's current password in that request, rate limited; Google-only identity changes
  first need the local owner CLI to set a password.
- Browser and native sign-in both complete in the initiating window. An external callback
  gets no app session, even when its browser has different cookies. Account/session
  changes while consent is pending invalidate linking. Unlink before replacement; both
  link and unlink revoke other sessions and rotate the current session.
- Google Desktop clients use a canonical 127.0.0.1 loopback callback derived from app port;
  legacy GOOGLE_REDIRECT_URI is not reused. Mail consent/tokens/permissions remain phase 2b.
- Google errors displayed by the app are fixed safe messages. Codes and token replies must
  never enter failure logs, chats, account JSON or browser storage. Account email is an
  optional verified display label, never the lookup key.


## 2026-10-01 — GPU-01, non-elevated telemetry

- Owner Codex, reviewer Claude; codex/gpu-counters starts at main ec6a079. Scope is
  metrics.py, test_metrics.py and existing docs; health/setup/UI ownership is unchanged.
- NVIDIA stays first. The Windows fallback runs both Get-Counter paths in one hidden,
  six-second-bounded PowerShell call, using an injectable runner and captured samples.
  Sum processes on each physical 3D engine, then use the busiest engine per adapter LUID
  (clamped to 100%). Sum adapter-level dedicated usage, not per-process memory.
- Names/capacity use stdlib ctypes and DXGI EnumAdapters1/GetDesc1 with exact LUID matching.
  Release every COM interface. Do not zip WMI names against counter order. Unknown stays
  None; a real zero stays zero. No new dependency and no elevation request.
- Normal Windows metrics requests return immediately with the prior snapshot; only a
  daemon worker collects. First read is unknown, force=True remains synchronous for
  diagnostics, and other platforms retain the synchronous cache. TTL starts at completion.
- Design references: [DXGI enumeration](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgifactory1-enumadapters1),
  [adapter LUID/description](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/ns-dxgi-dxgi_adapter_desc1),
  [independent GPU engines](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/).
  Our fallback deliberately covers only the requested 3D counters, not all Task Manager engines.


### GPU-01 review follow-up (2026-10-01)

Claude found one-shot callers inheriting the async cache: system status/briefing now
force a fresh sample. Only HTTP polling uses stale-while-refresh. Optional util_kind=3D
labels counter data; unknown usage remains unknown through prose and UI formatting.

## ANALYTICS-04 update — 2026-10-01

ANALYTICS-04: codex/analytics-drivers starts at main 766b645; Codex implements and Claude reviews. drivers() fits only the prefix, and anomalies() is a whole-sample diagnostic. Immutable JSON-safe results and consumer rules are documented in docs/analytics.md. Runtime has no extra dependency or IO.
