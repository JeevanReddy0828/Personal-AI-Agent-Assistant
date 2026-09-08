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
