# ERRORS.md — failure log

Mistakes and their root cause + fix, so they don't recur. Append after any real bug or
near-miss. Newest first.

## Reliability pass (2026-09-11)

- **Graceful degradation hid two outages.** The provider caught `URLError` (which
  `HTTPError` subclasses) and returned None. The orchestrator read None as congestion, so
  a tier returning HTTP 400 on *every* request reported itself as merely busy; and the
  document tool turned the same None into "The model returned an empty document" when the
  API 503'd. Neither was visible anywhere. Fix: retry 5xx/429/timeouts three times, never
  retry 4xx, and `failures.py` records every caught failure (`failures` command,
  `/api/failures`). **Rule: an `except` that swallows must also record. A boundary that
  loses its reason converts a loud failure into a silent wrong answer.**
- **The approval bridge was wired into one handler.** Risky actions became approvable in
  the web app, but only `/api/stream` registered a listener, so `agent run` denied every
  risky step immediately while the user watched it. Both streaming handlers now share one
  `_approval_bridge` context manager. Rule: when a capability depends on registration,
  register it in a shared helper, not per call site.
- **The agent gave three different confident wrong numbers.** "Count the python files in
  src" answered 27, then 6, then 42, against a true 65 that `scan files src` had all
  along. Three separate omissions: `scan` capped its listing at 200 and reported the cap
  as the total; nothing tallied by file type, so the question was unanswerable; and the
  agent's observation named its data's keys (`[data: files, root]`) and clipped the
  message without saying so. Fix: `scan` returns `total_files`/`listed`/`complete` and a
  `by_extension` tally over the whole walk, and `_observe` reports a list's length, a
  small mapping's **contents**, and states when a message was clipped. Rule: if a model
  is expected to reason about a quantity, hand it the quantity - never a sample and a
  hope. And a truncation the model cannot see is a lie by omission.
- **Committed to `main` again.** Second time this session. Moved to a branch and reset.
  Check `git branch --show-current` before the first commit of any change.

## NVIDIA model survey (2026-09-11)

- **The ultra tier failed every request and reported itself as busy.** NVIDIA moved the
  endpoint to the V2 model runner, which rejects `reasoning_budget`:
  `HTTP 400 ValueError: thinking_token_budget is not yet supported by the V2 model runner`.
  Because an empty answer is treated as congestion, the tier degraded down on every turn and
  health showed `ultra: degraded` - so a bad parameter looked exactly like a busy model for
  as long as it took to benchmark something else. Fix: stop sending it; the configured budget
  now only sizes `max_tokens` locally. Rule: when a tier is permanently "degraded", send it
  one request by hand and read the HTTP body before believing the congestion story.
- **`/v1/models` is a catalog, not an entitlement list.** It advertises 80 models on this
  account; `llama3-chatqa-1.5-70b`, `codestral-22b`, `gemma-3-12b`, `nemotron-4-340b`,
  `llama-3.1-nemotron-ultra-253b`, `nemotron-nano-3-30b`, `gemma-3-4b`, `mistral-nemo-12b`,
  `minitron-8b`, `nemotron-51b`, `zamba2-7b`, `cosmos-reason2` and `phi-3-vision` all return
  **404** on first call, and `llama-3.2-90b-vision`, `llama-guard-4-12b` and
  `mistral-nemotron` time out. Rule: never wire a model in off the listing - call it first.
- **An unpaced benchmark measures the throttle, not the model.** Twelve routing turns back to
  back tripped the 60s degradation cooldown on all four tiers, after which `_route` stops
  consulting the LLM entirely and unrouteable requests return the canned "I do not know the
  right tool route yet". The first diagram measurement was therefore meaningless. Rule: pace
  live-model measurements ~12s apart and assert tier health at the end of the run.
- **`nemotron-parse` invents captions.** A screenshot of this app's own home view came back
  with 37 `Caption` regions for 2 pictures, one reading "Figure 1: The S-color image of the
  alpha-ray diffraction pattern..." - fabricated from training data. Captions are dropped;
  extraction went from 2121 characters to 901, all real.
- **A guard that refused every phrase broke the commoner phrasing.** #64 made `download`
  reject any argument containing a space, which fixed `download it for me` and broke
  `download it for me - https://gdoc.io/...`, link included. Fix: extract the first address
  and refuse only when there is none. Rule: a validator should look for the thing it wants,
  not reject the shape it dislikes.
- **Dead config looks like configured config.** `OPENAI_SPEECH_MODEL` and
  `OPENAI_REASONING_MODEL` were added to `.env` and read by nothing, and
  `OPENAI_VISION_MODEL` appeared three times with different values (first wins, so the
  working one survived by luck). Speech selects a model by Riva **function id**, never a
  model name. Rule: a new env name needs a `config.py` field in the same change.

## Session context (2026-09-10)

- **Follow-ups lost the conversation.** After the advisor produced a schema, "build a flow
  chart for this schema" and "build erd for this" (agent mode) went hunting for `schema.json`
  on disk. Four causes: `/api/agent` sent no history at all; the provider clipped every turn
  to 300 chars and kept 8 turns, so the schema was gone even in chat; the advisor never saw
  the conversation; the client sent only the last 12 messages. Fix: `context.py` chunks the
  whole session and budgets a block for every prompt, with a note naming what "this" refers
  to; history is threaded through agent, advisor, CLI and `/api/agent`. Rule: any new
  model-facing path must take `history` and build its context with `context_block`.
- **A chat decision with no text fell into the no-LLM fallback.** `PlanDecision.is_chat`
  requires a response, so a router reply of `action=chat, response=null` returned the canned
  "I do not have an LLM provider connected" message even with a model configured (and the
  non-streaming path also recorded the fast tier as down). Fix: `is_chat` now means
  `action == "chat"`, `_route`'s heuristic shortcut checks for text, and the fast tier is
  asked for the reply.
- **Grounded-news prompts looked like follow-ups.** The synthesized "use the web results
  below… prefer this live information" prompt contains "this", so every fresh-news answer got
  a note telling the model the question referred to the previous reply. Fix: rank the session
  context on the user's own words (`context_query=`), and `refers_back` ignores long messages.
- **Long fenced code was sawn in half; a `DONE:` YAML key posed as the FINAL header.** The
  hard-split loop cut a 2.8k-char code block into two unbalanced pieces, and header detection
  matched inside fenced blocks. Fix: long fences split at line boundaries with every piece
  re-fenced; header candidates outside fenced spans only. Also: a hard-cut fallback marked a
  whole chunk as shown when only its prefix was, hiding the rest from retrieval.
- **A one-line FINAL dropped the diagram written before it.** The agent wrote the Mermaid
  chart, then `FINAL: the chart is above`, and only the FINAL text reached the user. Fix: keep a
  pre-FINAL body when it has deliverable structure (fence, heading, table, list, diagram) and
  prefer an upper-case FINAL header over a prose `Answer:` line; the prompt asks for the whole
  result after FINAL.

## Live-testing pass (2026-09-09)

- **Chat brain pointed at retired models.** Every chat failed: `meta/llama-3.1-8b-instruct`
  and `nvidia/llama-3.3-nemotron-super-49b-v1` returned HTTP **410 Gone** (NVIDIA EOL'd them
  2026-08-26). A new key can't revive a retired model. Fix: probe `/models` + a ping per
  candidate to find models the account can actually call (404 = "not for this account"), then
  point `.env` tiers at provisioned ones (`nvidia/nemotron-3-super-120b-a12b`). Model IDs live
  in `.env` (per-user), never in code. Rule: when "the model is down", check for 410/404 first.
- **Chronic "fast model busy" note.** The fast tier (`nemotron-3-nano-omni-30b …reasoning`) was
  a slow reasoning model, so nearly every reply timed out and degraded to smart → the honest
  "busy" note showed on *every* answer. Fix: a fast tier must be reliably fast; pointed it at
  `super-120b` (0.4–5 s), which is faster than the "fast" model was.
- **Image attachment dead-ended on OCR.** A bare image upload was composed as `process file`,
  which the file processor maps to OCR (Tesseract) → "Tesseract not found" even though a vision
  model was configured. Fix: route bare image uploads to `describe image <path>` (vision-first,
  OCR fallback) in `webui._bare_attachment_command`.
- **Multi-query message misrouted to IMAP + raw bytes error.** A message containing "summarize"
  and "email" in *unrelated* sentences matched the digest trigger (both words anywhere) and hit
  IMAP, whose `imaplib.IMAP4.error` propagated as a raw `b'[AUTHENTICATIONFAILED] …'`. Fix:
  require the summarise verb *near* the inbox noun on one line; catch `IMAP4.error`/`OSError` in
  `search_inbox` and return a friendly `ToolResult.failure` with an app-password hint.
- **Replies cut off mid-sentence.** The advisor brain and streaming chat both capped at 900
  tokens, truncating long comparisons/designs. Fix: advisor `_build_agent_brain(answer_max_tokens=4000)`,
  `stream_answer` default 900 → 2048.
- **"Connection error: Reload J.A.R.V.I.S".** The per-process API token rotates on restart, so an
  open tab's token goes stale → same-origin 403. Fix: the page reloads once on a same-origin 403
  to pick up a fresh token (5 s guard against loops).
- **Logic/puzzles rambled.** Jug puzzles, "show your reasoning", "find the flaw" scored as simple
  chat and answered on the non-reasoning tier, talking in circles. Fix: `_complexity` escalates
  those cues to the reasoning (ultra) tier so the model thinks first. Tradeoff: slower — the new
  model+latency line makes that visible.
- **Voice failures were silent.** A blocked/absent mic or offline speech service only logged to a
  hidden debug HUD, so voice "just didn't listen". Fix: `rec.onerror` surfaces an actionable
  message (allow mic / no mic / offline) and exits voice mode cleanly.
- **C++/C# ATS scored 0.** `\bc\+\+\b`/`\bc#\b` never match (a `\b` can't sit after `+`/`#`), and
  the `len<3` filter dropped `c#`. Fix: a token-boundary matcher over `[A-Za-z0-9+#]` (not `.`, so
  "AWS." still matches), and keep short symbol skills. Also: malformed model JSON crashed the
  resume renderer (non-dict list entries) — render now skips non-dict/non-list shapes and
  `tailor_resume` rejects experiences with no usable objects.
- **Review batch left the branch red.** Codex's uncommitted batch over-corrected multi-task `ok`
  to `all(...)` (broke partial-success tests) and missed a token header on one webui test. Fix:
  `ok = any-succeeded` (all-failed → False, per user), and add the missing `X-Jarvis-Token`.

- **Nearly rebuilt an existing feature.** Started building an "inbox digest" that already
  existed on `main` (`_email_digest`). Root cause: trusted a stale backlog note over the
  code. Fix/rule: `git grep` the repo for a feature before building it.
- **window_fx targeted any window by title.** `FindWindow("J.A.R.V.I.S")` matched and
  modified an unrelated third-party app with the same title. Fix: enumerate only top-level
  windows owned by *our* process AND matching the title.
- **Compact layout collapsed the UI.** Hid two grid columns with `display:none` while
  keeping a `0 0 1fr` track list, so CSS grid auto-placed the chat column into a 0-width
  track. Fix: collapse to a single `minmax(0,1fr)` track and span the chat column.
- **Ported keyword extractor kept trailing dots.** `"aws."`/`"kubernetes."` tokens broke
  whole-word ATS matching. Fix: strip stray leading/trailing dots, keep `c++`/`c#`/`node.js`.

- **Review regressions (2026-09-08).** Unit-only coverage missed executable Markdown
  attributes, cross-origin mutations, duplicate due jobs, chat ownership and mobile
  overflow. Keep security/concurrency/browser tests in CI. Browser conditions use CDP
  evaluation because string-based Playwright wait predicates can conflict with nonce CSP.
- **False resume confidence.** Lexical overlap approved invented employers and metrics.
  Export now requires exact source excerpts and grounded factual fields; generated
  application drafts explicitly require user review.
