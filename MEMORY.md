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
- `AgentContext` is a frozen dataclass; adding a field means updating `app.build_orchestrator`
  AND the test builder in `tests/test_orchestrator.py`.

## Decisions
- 2026-06: Adopted **Agent Operating Principles** (CLAUDE.md preamble) as the governing
  doc. Codebase already conformed, so adopted going forward — no refactor.
- 2026-06: **Job-search dashboard** initiative. Web UI became multi-page (header nav +
  hash router: Chat/Overview/Jobs). New `jobs.py` (JobTracker pipeline + `/api/jobs`) and
  `copilot.py` (ATS + grounded tailoring + `/api/copilot`). PRs #30–#32.
- 2026-06: Integrated the Agentic-AI-JOB-CoPilot by **porting its stdlib logic** (ATS
  scoring, keyword/grounding) onto our LLM provider — not bolting on its FastAPI/Next/openai
  stack — to preserve the locked stack. (`copilot.py`)

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
