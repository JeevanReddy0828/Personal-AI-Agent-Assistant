# The orchestrator

`handle()`, session context, freshness, the search backend, `AgentContext` and the two autonomy layers.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

- `orchestrator.handle(text, _allow_planner, history, on_token)` is the core
  entry. It checks direct command prefixes, then routes via heuristic → LLM.
  Tool results are turned into plain language by `_humanize` (local, no extra
  LLM call). Chat replies stream via the `on_token` callback when provided.
- **Session context.** `history` is the whole session transcript (the web client sends
  up to 80 turns per request; the CLI keeps its own list). `context.build_context(history,
  query, budget=…)` follows the standard chat-memory hierarchy: when the whole transcript
  fits the budget it goes in verbatim; otherwise the recent turns are quoted verbatim, the
  older turns become a **rolling summary** written by the fast tier in the background
  (`register_summarizer`, cached per transcript prefix and folded incrementally; a
  heading outline stands in until it exists), and the best earlier chunks are retrieved
  with **contextual BM25** (each Markdown chunk — fenced code kept whole — is indexed with
  its turn's title and section heading). A follow-up ("build an ERD for this") is rewritten
  into a standalone query for retrieval and for the advisor's web research
  (`resolve_reference`), and the block ends with a note naming what "this" refers to. It feeds the router (`ROUTE_BUDGET`), the chat tiers
  (`CHAT_BUDGET`), the autonomous agent (`AGENT_BUDGET`, via `AutonomousAgent.run(context=…)`)
  and the advisor (`ADVISOR_BUDGET`, `ProblemSolver.solve(conversation=…)`). The router is
  taught that a back-reference is a follow-up: resolve it from the transcript, emit a command
  only when it names something actionable there, else `action=chat` with `response` possibly
  null (`PlanDecision.is_chat` means `action == "chat"`; the fast tier answers a text-less
  chat decision) — so "build an ERD for this" is answered from the schema in the conversation
  instead of the agent scanning the filesystem. `/api/agent` takes `history` like
  `/api/stream`; the web client stores a bounded digest of each reply's tool data (`extra`)
  and the CLI appends one, so "summarize this" after `read file …` has the text. Results are
  memoized per (history, query, budget), and a synthesized prompt (grounded news) passes
  `context_query=` so the context is ranked on the user's own words.
- **Freshness path.** Before answering a chat turn, `_needs_fresh_info` flags
  time-sensitive questions (keywords + patterns like "did X end", recent years);
  `_grounded_news_answer` then runs a web search (one retry for the flaky free
  DDG endpoint) and synthesizes a cited answer from the results, preferring live
  data over training knowledge. If search yields nothing it falls back to model
  chat but appends a "may be out of date" disclaimer (`stale_warning` in data).
- **Search backend.** `websearch.build_search_backend(provider, key)` returns a
  resilient backend: a real API (Brave / Serper.dev / SerpApi — note the latter two
  are different services — key-gated via `SEARCH_PROVIDER` / `SEARCH_API_KEY` /
  `BRAVE_API_KEY` / `SERPER_API_KEY` / `SERPAPI_API_KEY`) with automatic DuckDuckGo
  fallback, else DDG directly. `app.py` shares one backend across `websearch` and
  `research`. API backends take an injectable HTTP transport (offline-tested).
- **AgentContext** is a frozen dataclass of all tools/subsystems, wired in exactly one
  place: `app.build_context(config, approval_callback)`. Adding a field means editing
  **that function and nothing else** — the test builder starts from the same wiring and
  `dataclasses.replace`s only the nine tools it needs to fake, so a new field reaches the
  tests without being named there. Verified by doing it: a 25th field added to the
  dataclass and to `build_context`, with the test builder untouched, leaves all 131
  orchestrator tests passing. Forgetting `build_context` fails loudly and by name
  (`TypeError: ... missing 1 required positional argument: 'probe_field'`), and
  `test_one_place_wires_every_field_of_the_agent_context` is the single test that says so.
  This used to be two files, and forgetting the second turned every orchestrator test into
  the same TypeError — "the usual source of a wave of failures after a merge".
  `build_context` is safe to call in a test: measured at 21ms with a temp config, touching
  no network and creating only lock files.
- **Two autonomy layers, don't conflate them.** `autopilot.py` runs a *static*
  plan restricted to a safe read-only allowlist (blocks anything risky).
  `reasoning.py`'s `AutonomousAgent` is the *LLM-driven* plan/act/observe/replan
  loop that can use any command (risky ones still hit the approval gate). Its
  reasoning brain is an injected `decide(prompt)->str` callable so the loop is
  unit-tested offline; in `orchestrator._build_agent_brain` it's backed by
  `provider.answer` on the smart (or fast) tier. Persisted via the `agent_runs`
  AgentContext field.
- **The agent trusts a reply only up to its first runnable ACTION** (`parse_agent_decision`).
  The model writes ACTION, an OBSERVATION it made up and a FINAL built on it, all in one
  reply, and FINAL used to win: runs ended on a README summary of a file that does not
  exist, or on "[the content would be provided here after the action runs]". An
  upper-case OBSERVATION before any FINAL is cut off, an ACTION before FINAL runs first,
  and a reply with neither header is asked again once (`_ask_again`). Known limit: a
  well-formed FINAL can still be wrong, which parsing cannot see.
