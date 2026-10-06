# Conventions, and why

The seven rules in CLAUDE.md, with the measurements and incidents behind them.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

## Non-negotiable conventions

1. **Zero required deps.** New heavy capability → optional extra + graceful
   fallback (return a clear `ToolResult.failure` with an install hint; never
   crash). Follow the OCR/transcribe/metrics pattern.
2. **Approval gate for anything risky.** Sending mail, writing/moving files,
   downloads, launching apps, shell, browser state changes → go through
   `safety.ApprovalGate` with the right `RiskLevel`. Read-only/local = LOW/none;
   network read (web search, inbox read) = MEDIUM; external state change =
   HIGH/CRITICAL. MEDIUM runs through in the web app; HIGH/CRITICAL raises an approval
   card and waits (`approvals.py`). Getting the level right therefore decides whether a
   demo stops for a click, so do not reach for HIGH on a read.
3. **Tools return `ToolResult`** (`tools/base.py`): `ok`, `message`, `data`.
4. **Testable network/IO.** Put network/engine calls behind an **injectable
   backend** (see `transcribe.py`, `websearch.py`, `research.py`, the LLM
   provider's transport) so the success path is unit-tested offline. Tests use
   `unittest`, are dependency-free, and live in `tests/`.
5. **Heuristic-first routing for latency.** Common requests route via
   `planner/heuristic.py` with zero network cost; the LLM is the fallback.
   `is_plain_question()` adds a second short-circuit: a question that asks for
   knowledge and names nothing to act on (no tool word, no path/URL, not a decision)
   skips the routing call entirely and is answered directly. It is deliberately
   conservative — anything else falls through to the router, so tool routing cannot
   regress. Measured: median time-to-first-token fell from 6.5-13.0s to 0.8-1.8s,
   routing from ~1000ms to ~5ms, and it removed a real defect where the LLM router
   sent ordinary questions to the `solve` research pipeline (21s, 24s, 82s, never
   streaming a token). Verify changes here with `latency` / `/api/traces`.

   **"Conservative" is a property of `_TOOL_SIGNALS`, and it had a hole in it.** The
   alternation ends in `\b`, so `reminder\b` does not match "reminders", and only `files`,
   `notes` and `jobs` were ever written in the plural. So "do i have any reminders /
   drafts / documents / tasks / downloads / screenshots / workflows" were all classified
   as plain knowledge and answered by the chat model, which cannot see any of them — the
   exact regression this short-circuit is documented as unable to cause. `do i have any
   notes` behaved, purely by accident of spelling. A trailing `s?` covers the whole list;
   a false positive costs one routing call, which is the direction this must err in, and
   measured over 20 genuine knowledge questions none flipped. **Adding a noun here means
   adding it in the form the user says it.**

   **A route matched by an exact set of strings is a route that does not exist.**
   `_reminder` listed four literals, so fourteen ways of asking to see the list missed —
   including "can you show me my reminders", which `strip_address` cannot help with since
   it removes greetings and the wake word but not "can you". `_REMINDER_ASK` carries the
   same polite prefix `_ARRANGE_ASK` already uses, and both `^`-anchor **inside the
   pattern** rather than relying on the caller's `.match()`: left to the call site,
   `.search("remind me to tell bob to check my reminders")` matched, so any later reuse
   would have turned a creation into a listing and dropped the reminder. Two traps worth
   keeping: read "due" **inside** the listing branch, because against the whole sentence
   "remind me to pay the bill due friday" files as a listing; and require the plural (or
   an explicit "my reminder") so "what is a reminder" stays a definition question.

   **The routing call has its own deadline (`route_timeout`, 2.5s).** Measured over 300
   recorded turns: 77% route `direct` at 0ms, 15% `heuristic` at a 2ms median, and the
   remaining **8% reach the LLM router at a 951ms median, a 2492ms p90 and a 7954ms worst
   case** - those turns take 4505ms end to end, because the classify call is spent *before*
   the answer begins and the user is looking at nothing for all of it. It was bounded only
   by the 45s ceiling shared with the answer itself. Past a couple of seconds the
   heuristic's own answer beats waiting for a better one. A routing **timeout** now returns
   `action=chat` with **no** response text so the chat ladder answers; it used to return
   "I could not reach my language model", replacing a working answer with an error because
   only the classify call ran out of time. A genuine connection failure still says so.
   Both record to `failures.py`.

   Measured in the same pass and deliberately **not** changed, so nobody repeats the work:
   `build_context` costs 0.06ms memoized and 4.16ms cold on an 80-turn transcript, and a
   whole local turn is 16.4ms whether the history holds 0, 20 or 80 turns - against a
   1723ms median time-to-first-token, none of that is worth touching. The remaining TTFT
   is the model answering, which is network, not ours.
6. **Never commit secrets.** `.env` is gitignored. Scan staged diffs for
   `nvapi-` (and the Gmail app password) before every push.
7. **Match the surrounding style.** Concise comments, full type hints,
   `from __future__ import annotations`.
