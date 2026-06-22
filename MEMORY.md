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
  binds loopback, no auth.
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

## Open / pending decisions
- **jobright daily pull.** jobright.ai has no public API (auth-gated). The user's repo
  `job-agent--Jarvis` has a Playwright jobright scraper with a persisted login session
  (`outputs/session.json`) — this makes an unattended scheduled pull feasible. Decision
  pending: **(A) port just that scraper** into JARVIS behind the `browser` extra, feeding
  JobTracker + CoPilot, OR **(B) bridge** (run that repo standalone, JARVIS ingests its
  output JSON). Do NOT fold in its full FastAPI/Next/anthropic/auth/DB stack (mismatch +
  duplicates our tracker/CoPilot). Caveats: third-party ToS/fragility, on-disk session
  credential, Playwright weight. Also need the user's resume file path to enable auto-tailor.
- 6 jobright leads were added to the tracker manually (stage `applied`, `source: jobright`,
  "not yet applied"). There is no "lead" stage yet — a small addition if accurate-funnel matters.
