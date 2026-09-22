# ERRORS.md — failure log

Mistakes and their root cause + fix, so they don't recur. Append after any real bug or
near-miss. Newest first.

## Session 2026-09-20 (later)

- **A corpus that measured the wrong register, and said so confidently.** Matching bare
  name-then-position for window arrangement risks grabbing ordinary sentences, so I swept
  2148 real sentences from this repo's own CLAUDE.md, README.md and ERRORS.md, got zero
  matches, and wrote that number into the PR as proof. Review then found
  "the value is in the middle and the key is on the left" routing to the window tool. The
  corpus is technical documentation - long sentences with subordinate clauses that leave
  words over, which is precisely what the whole-sentence rule rejects. The exposure was in
  *short conversational statements*, which that corpus barely contains. **Rule: a corpus
  proves nothing about a register it does not contain. Before quoting a zero, ask which
  inputs would break the rule and check the corpus actually holds that shape - a large N
  in the wrong register is more convincing than no evidence, and worse.**

- **The same list-duplication bug twice in two days.** `_TOOL_SIGNALS` failed by omission
  (no plurals); `_POSITION_WORD` failed by omission (no corners, thirds or halves,
  hand-copied from `windows.LAYOUTS` and drifted). Both were guards whose correctness
  rested on a hand-maintained copy of a list that exists elsewhere in the repo. **Rule:
  when a pattern enumerates a vocabulary another module owns, derive it from that module.
  If the import is not possible, the duplication is a scheduled failure - write the test
  that compares the two lists.**

- **Walked into the heredoc-escaping trap again**, mangling `test_planner.py` with regex
  escapes inside a non-raw string and having to revert the file. ERRORS.md already carried
  three entries about this exact thing - and writing *this* entry through a heredoc turned
  its own escape into a real newline, so the paragraph warning about the trap arrived
  broken by the trap. **Rule: a heredoc containing regex escapes is not a caution, it is a
  prohibition - use exact-match editing for those lines, including when the line is prose
  about escaping.**

## Session 2026-09-20

- **A short-circuit that could not regress tool routing, regressed tool routing.**
  `is_plain_question` is documented as conservative: anything naming a tool or the user's
  own data falls through to the router. But the word list ends each alternative with
  ``, so `reminder` never matched "reminders", and only `files`, `notes` and `jobs`
  were ever written in the plural. "do i have any reminders / drafts / documents / tasks
  / downloads / screenshots / workflows" were classified as plain knowledge and answered
  by the chat model, which can see none of them. **Rule: when a guard's correctness rests
  on a hand-written list, the bug will be an omission from the list, not a flaw in the
  logic - enumerate the list against real inputs rather than reading the code.** A
  one-character `s?` covered all of it.

- **The recorded symptom was a third of the defect.** The backlog said one phrasing of
  "what are my reminders" went to the LLM. Measuring found fourteen phrasings missing and
  two reaching no router at all. **Rule: reproduce a backlog item before believing its
  scope. A bug report describes where someone happened to stand, not the size of the
  hole.**

- **Nearly verified a fix by breaking an unrelated feature.** Reverting the polite-prefix
  regex to watch its test fail, the script matched on line *content* and hit
  `_ARRANGE_ASK` - whose constant name sits on the line above the pattern. The test output
  came back empty rather than with the expected failure, which is the only reason it was
  caught. **Rule: anchor a scripted edit to the line that names the thing (`startswith
  "_NAME = "`), never to a substring of the body, and treat an unexpected *shape* of
  output as a failed step rather than a passed one.**

## Session 2026-09-19

- **A feature shipped, tested and documented, that had never once been on screen.** The
  barge-in meter (#109) was added inside `#voice`. But `f6a145d` had removed
  `voice.classList.add('on')` back in June - deliberately, to drop the written overlay -
  and left `.voice.on{display:flex}` behind, so `#voice` has been `display:none` ever
  since. Three months of "tune the 0.045 floor against the meter" advice, against an
  element that rendered nothing. Its test passed the whole time because it asserted
  `#vmeter.hidden` is false, and `hidden` is false on an element inside a `display:none`
  parent. **Rule: an element's own visibility attribute is not evidence it is on screen.
  Assert a box - `getBoundingClientRect().height > 0` - and remember that deleting the
  code that SHOWS a thing leaves every rule that styles it looking perfectly healthy.**

- **A measurement that proved the wrong client.** CLAUDE.md recorded the page ETag as
  "183,536 bytes -> 0". True - for curl passing `If-None-Match` by hand. A browser was
  never going to send one, because a duplicate `Cache-Control: no-store` from
  `end_headers` forbade storing the page it would revalidate, so the 304 path was
  unreachable in the only client that matters. Warm reload in Chromium: no
  `If-None-Match`, 200, the full 196KB, every time. **Rule: measure what the user's client
  actually does, not what your tool can be instructed to do. A hand-set request header
  tests the server's half of a negotiation and says nothing about the other half.**

- **Fixed a header bug and opened a token leak with the same line.** Making the route's
  `no-cache` take effect meant the page - which embeds the API token for shell, files and
  mail - became genuinely storable for the first time, and bare `no-cache` lets shared
  caches hold it, over plain HTTP, with a phone on the same wifi. Found in review of my
  own change; the first fix then missed the two SSE streams carrying the conversation.
  **Rule: when a directive that was being overridden starts taking effect, every consumer
  of it is new behaviour and needs re-reviewing as if freshly written - and sweep the call
  sites from the source rather than listing the ones you remember.**

- **A revert that changed nothing because the revert was wrong.** Verifying a CSS guard, I
  inserted `display:none` into a rule that later declares `display:flex`; the later one
  won, the test passed, and for a moment that read as "the guard is dead". **Rule: when
  reverting proves nothing, suspect the revert before concluding the code or the test is
  dead - and in CSS, check for a later declaration in the same block.**

## Session 2026-09-17

- **Two places listed the same 24 fields.** `AgentContext` was wired once in
  `app.build_orchestrator` and again in the test builder, so adding a field meant editing
  two files and forgetting the second turned every orchestrator test into the same
  TypeError - what CLAUDE.md called "the usual source of a wave of failures after a merge".
  The wiring is now `app.build_context`, and the tests start from it and
  `dataclasses.replace` only the nine tools they fake. **Rule: when a list must be repeated
  in two places, the second copy is not documentation of the first, it is a future merge
  conflict. Measure whether the real wiring can simply be reused - here it cost 21ms,
  touched no network, and made the duplicate unnecessary.**
- **Mangled the same line twice with shell-and-Python escaping.** Writing
  `b"PNG
"` through a heredoc put a real newline in the file, and the repair
  attempt broke it again. ERRORS.md already carries two entries about exactly this
  (a stray backslash in a regex, prefix-anchored import surgery). **Rule: for a line
  containing escapes, edit by exact match or by line index - never rebuild it through a
  heredoc. Better still, write `bytes([0x89])` and have no escape to mangle.**

- **A guard that looked like a no-op, caught by reverting it.** Clearing a tier's stored
  break time when it recovers appeared to do nothing - the file is written from the state
  either way, and the suite passed with the line removed. It is load-bearing: without it a
  tier that breaks, recovers and breaks again inherits the FIRST break's timestamp, so its
  cooldown is already long expired and it is retried on every turn, which is the failing
  round-trip the mechanism exists to avoid. The test was missing, not the code. **Rule: the
  revert step does not only check the fix - when reverting changes nothing, either the line
  is dead or the test is, and both are worth knowing.**
- **Persisted state read the process-wide config.** `ModelStatus` was given
  `load_config().data_dir`, but the test runner points that at one directory for the whole
  suite while each test builds its own config, so a tier one test recorded as broken was
  still broken for the next and three unrelated tests failed with `'broken' != 'degraded'`.
  The path is now a constructor parameter. **Rule: anything persistent takes its location
  from its caller. State that ignores the caller's own config is shared state, and it will
  leak between runs as readily as between tests.** The same line in `TraceStore` is why 300
  traces from a test run ended up in the live `.agent_data`; the orchestrator now takes one
  `data_dir` that traces, tier health, images and documents all share. The guard is a
  **sweep** of the process-wide directory rather than a list of the known stores, because
  this failure recurs by addition - the next person writes `load_config().data_dir` copying
  the line above them, and nothing notices.

- **The fallback ladder decided everything from `bool(reply)`.** A retired model id (410),
  a rejected key (401), a model the account cannot call (404), a refused parameter (400)
  and a genuinely overloaded endpoint (503) all reached it as the same empty reply and were
  recorded identically as "degraded" - so a permanent misconfiguration was retried every
  60s forever and reported to the user as *busy*, which is advice to wait for something
  that will never recover. This is the boundary underneath **both** of the outages already
  in this file: the ultra tier 400ing on every request while health called it congested,
  and chat pointed at models NVIDIA had retired where "a new key can't revive a retired
  model". Fix: `classify_failure` splits DEGRADED from BROKEN, the reason travels to
  `ModelStatus`, broken tiers get a 900s cooldown instead of 60s, and health names the tier
  and what to change. **Rule: when a boundary collapses several causes into one value, the
  code above it cannot make a correct decision no matter how well it is written - and every
  bug that follows looks like a bug in the caller.**
- **`stream_answer` swallowed its failure entirely** - no record, on the path that serves
  every chat turn, so a stream that never started was indistinguishable from a model with
  nothing to say. Same rule as the reliability pass, still being rediscovered: an `except`
  that only returns a fallback must record the reason.

- **The call that only picks a path could take as long as the answer.** Routing shared one
  `timeout = 45` with everything else, and it is spent *before* the reply starts, so the
  user watches an empty screen for the whole of it. Measured over 300 recorded turns: the
  LLM router ran on 8% of them at a median of 951ms, a p90 of 2492ms and a worst case of
  7954ms, and those turns took 4505ms end to end. Worse, a routing **timeout** returned the
  canned "I could not reach my language model" - so a model that was merely slow to
  classify produced an error in place of an answer the chat call would have given, and the
  `except` recorded nothing. Fix: `route_timeout` of 2.5s, a timeout falls through to the
  chat ladder with no response text, a real connection failure still says so, and both are
  recorded. **Rule: a step that only decides how to answer gets a deadline shorter than the
  answer's, and a fallback must never assert a cause it has not established - "slow" and
  "unreachable" are different facts.**
- **Most of the latency pass was measuring things and leaving them alone.** `build_context`
  costs 0.06ms memoized and 4.16ms cold at 80 turns; a whole local turn is 16.4ms whether
  the transcript holds 0, 20 or 80 turns; heuristic routing is 2ms median, 17ms worst.
  Against a 1723ms median time-to-first-token none of it is worth touching, and the rest of
  TTFT is the model answering over the network. **Rule: the output of a performance pass is
  as often a measurement that forbids a change as one that justifies it. Write the numbers
  down either way, or the next session pays to learn them again.**

- **Natural time never reached a parser at all.** `remind me to call mom at 6pm` answered
  "Reminder date/time must look like YYYY-MM-DD": the only accepted form was an ISO stamp,
  which is not how anyone speaks and certainly not how anyone dictates. Two separate gaps -
  `_parse_due_at` handed "6pm" straight to `datetime.fromisoformat`, and the heuristic
  router only recognised a reminder when it carried an ISO date, so every other phrasing
  fell through to the LLM. Fix: `timeparse.py`, deterministic and offline, shared with
  `scheduler` so the two cannot drift. **Rule: a value with exactly one right answer - a
  date, a sum, a file size - is parsed, never inferred. A model asked for a date returns a
  plausible one, and a reminder on the wrong day is worse than one that refuses.**
- **Wrote into the user's real data while testing, again.** A throwaway server started with
  `LAPTOP_AGENT_PORT=8791` still used the real `.agent_data`, so three test reminders
  landed in the user's own list. ERRORS.md already recorded this exact failure once - 20
  `loadtest_N` keys in "what do you remember about me?" - and the lesson had been written
  down without being made hard to repeat. **Rule: `LAPTOP_AGENT_PORT` isolates the port and
  nothing else. A throwaway instance sets `LAPTOP_AGENT_DATA_DIR` too, and CLAUDE.md now
  carries the command so the next session does not rediscover it.**

- **A rejection that never reached the client.** Every rejecting POST path - 403 untrusted,
  401 locked, 404 unknown path, 429 too many attempts - answered without reading the body
  the client had already sent. Closing a socket that still holds unread data makes the OS
  reset the connection, so the client's pending read failed instead of seeing the status.
  Measured on Windows: a 1MB POST to an unknown path raised `ConnectionAbortedError`
  **[WinError 10053] 6 times in 12**, a bad token 3 in 12; a 2-byte body never tripped it
  locally, which is why it only ever showed up as an intermittently red CI test. Fix:
  `_drain_request_body()` at the single `_send` choke point, tracking **bytes read rather
  than a boolean** - `_pair` reads only the first 4096 bytes of a passcode POST, and a flag
  would have called the rest consumed and reset exactly the path a phone uses. **Rule: read
  the body before you answer. A status code the peer never receives is not an answer, and
  the paths that reject are the ones where being told why matters most.**
- **One red CI job was hiding three others.** The matrix runs fail-fast, so the Windows
  jobs reported `cancelled`, not `failed`, and were never read. An audit bug had kept
  `main` red since #104; fixing it revealed a Windows crash in `clock.py`, a browser test
  of mine tuned to local hardware, and an intermittent socket reset. **Rule: `cancelled` is
  unknown, never passing. Check every job's conclusion before believing you understand why
  CI is red, and expect to repeat the cycle until a run is green end to end.**
- **`tail` read one generation and said nothing.** `AuditLogger.tail` opened only the
  current file, so straight after a rotation it returned however few lines had landed
  since. The real call is `tail(50)` from the `audit` command, so the audit view went
  nearly empty every time the log rotated. The test asserted `tail(5) == 5`, which was
  decided by byte arithmetic alone - six records fit a generation on Windows so it passed
  locally, four fit on the runner so it failed there. **Rule: a count that depends on how
  long a timestamp happens to be is not an assertion. Assert against what is on disk.**
- **The message explaining a missing dependency could not be printed.** Without `tzdata`
  the zone lookup raises, and `clock.py`'s failure is meant to say so and still give the
  local time - formatted with `%-I`, a glibc extension that raises
  `ValueError: Invalid format string` on Windows. So the advice for a missing time zone
  database crashed on the one platform where it is usually missing, a packaged JARVIS.exe
  included. The same file already carried a comment saying `%-d` is not portable and to
  strip the zero by hand, and the line already had the `.lstrip('0')`. **Rule: an error
  path runs on the machine that is already broken - it gets the same portability care as
  the success path, and more testing, not less.**
- **A test tuned to this laptop.** A browser test counted rendered frames between two
  animation end states and demanded six. A 60fps desktop puts ~13 there; the CI runner
  draws ~34fps and put 5, so it failed a feature that worked. **Rule: never assert a frame
  count, a duration or a throughput calibrated on the machine you wrote it on. Assert the
  shape - it eased rather than cut - and leave the margin to the hardware.**
- **A stray `*/` silently deleted a CSS rule.** Adding a second comment block left the
  first one closed and the new text running as bare CSS, which swallowed the
  `position:fixed` rule that followed. The probe still looked right, because it measured
  the chat fade and the glow - neither of which needs that rule. The suite caught it on
  `'relative' != 'fixed'`. **Rule: a CSS rule that vanishes does not raise. When a probe
  and a test disagree, the probe is measuring the wrong thing.**
- **One class carried both the intent and the mechanism.** `orbfocus` made the stage a
  fixed overlay *and* faded the chat, and only came off once the orb had landed - so
  leaving cost 1100ms against 500ms to enter, with the chat invisible for the first 520ms.
  The ambient glow, sized as a percentage of a `.stage` whose box changes when the overlay
  drops, snapped 760px to 248px in a single frame. Fix: `orbstage` is the mechanism and
  waits; `orbfocus` is the intent and flips on the click. **Rule: when a class means two
  things with different lifetimes, it is two classes. And never size something against a
  box that is about to change underneath it.**
- **A marker drawn outside a clipped box.** The barge-in meter's threshold tick was drawn
  2px proud of its track, inside `overflow:hidden`, so the one reference line the meter
  exists to show was clipped flush. `getBoundingClientRect` reports the layout box and
  said 10px tall - clipping is a paint effect. Screenshotting with and without the clip
  gave different bytes. **Rule: geometry APIs do not see paint. To check whether something
  is visible, compare pixels.**
- **A hint that recommended a no-op.** "Lower it if talking over me does nothing" - but
  the threshold is `max(slider, leak*2.2)`, so whenever the room echo dominates, which is
  the case where barge-in actually fails, the slider does nothing through its whole range.
  **Rule: before writing advice into the UI, check the control can actually deliver it.**

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

- **Reported four broken vault links that were never broken.** Audited the vault by
  pointing `ObsidianVault` at `…\Claude Mem\Personal AI Agent`, the *project subfolder*.
  Obsidian resolves wiki-links across the whole vault, so `[[Memory log]]`,
  `[[Concepts MOC]]`, `[[Obsidian Memory]]` and `[[Knowledge Base]]` — which live in
  sibling folders — looked missing. All four exist, `.obsidian` sits at `Claude Mem`, and
  `OBSIDIAN_VAULT` was already set correctly. Root cause: trusted a path written in
  CLAUDE.md instead of reading the configured one. Fix/rule: resolve a vault through
  `load_config().obsidian_vault`, and confirm a root by finding `.obsidian` before
  concluding anything is missing. CLAUDE.md now records the root, not the subfolder.
- **The audit's own link extractor produced both false positives it then found.**
  Scanning the real vault turned up two genuine-looking failures, both wrong:
  `` `[[wikilinks]]` `` inside inline code in a note that *documents* the convention was
  read as a link to a missing note, and `[[Logs/2026-07-02]]` failed to resolve because
  the folder prefix was kept — which also left its target counted as an orphan, so one
  bug produced a contradiction (a note both linked and orphaned). Fix: `_wikilinks`
  strips fenced/inline code first and takes the last path segment.

## Session 2026-09-12 → 16

- **A guard that could not fail, twice.** (1) A browser test deleted `crypto.randomUUID`
  to simulate an insecure context — but it lives on `Crypto.prototype`, so the delete did
  nothing and the test **passed against the unfixed code**. Shadow it on the instance with
  `Object.defineProperty`. (2) A knowledge staleness test passed with the `_save`
  invalidation removed, because the mtime cache key already covered it. Rule: revert the
  fix and watch the test fail before believing it.
- **An eval harness that reported the right answer as a miss.** Ranking cases hardcoded
  document ids; the README had been re-indexed and moved from 47 to 52. Everything scored
  8/15 and the conclusion "kind weighting does nothing" was wrong — the truth was 12/15
  rising to 13/15. Resolve expected documents by source substring, never by id.
- **An error message that lied for half an hour.** The LAN unlock page fell back to
  "Wrong passcode." for *any* failed response, including a 429 and a non-JSON body, so a
  working passcode looked wrong. Never let a fallback assert a cause it does not know.
- **Two servers on one port, twice in one session.** `allow_reuse_address` lets a second
  process bind a port already being served on Windows. They hold separate approval and
  LAN passcode state, so it presented as random flakiness (a phone unlocking, then being
  asked again). `_refuse_if_running()` probes the port at both entry points now.
- **Prefix-anchored import surgery broke two files.** Scripted edits anchored on
  `from laptop_agent.tools.base import ToolResult` and
  `from laptop_agent.planner import HeuristicPlannerProvider`, both of which continued
  with `, reserve_new_path` / `, PlanDecision, Planner`. Anchor on the whole line
  including its newline.
- **Diagnosed from a path I had invented.** Reported four broken wiki-links in the vault
  by pointing `ObsidianVault` at the project *subfolder*; Obsidian resolves links
  vault-wide, and all four targets existed one folder over. `.obsidian` and
  `OBSIDIAN_VAULT` both said the root was `Claude Mem`. Read the configured path, do not
  trust a path written in prose.
- **A load test wrote into the user's real memory.** 20 `loadtest_N` keys appeared in
  "what do you remember about me?" because the concurrency test ran against the live app
  on 8770. Point load tests at an isolated data dir. It also exposed that there was no
  `forget` at all — anything the store was told was permanent.
- **Guards keep being the wrong shape rather than absent.** `_NO_TOOL_CLAIMS` forbade
  claiming a *file* was made, so the model invented an approval flow for a window request
  and reported "[Window arrangement initiated]" having done nothing. The failure class
  recurs in new forms; widen the guard to the class, not the instance.
- **A tie-break that was exactly backwards for the case that mattered.** "shortest title
  wins" was meant to prefer a real Chrome window over a page mentioning Chrome — instead
  it picked **Live Caption**, a Chrome-hosted widget also running as `chrome.exe` with a
  shorter title. Rank title+process matches above either alone.
