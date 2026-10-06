# ERRORS.md — failure log

Mistakes and their root cause + fix, so they don't recur. **Start at the symptom index:
search it for what you are seeing** — an error text, a wrong behaviour, a test that lies —
before debugging. Each line gives the cause and the guard that now holds it; the dated
sessions below carry the detail, newest first.

**After any real bug or near-miss:** add one index line under its area (symptom → cause →
guard, with the date), and a session entry only when the line cannot carry the lesson.

## Symptom index

### Tests and verification
- **A test passes against the bug** → the guard could not fail (`delete crypto.randomUUID`
  hits nothing on the prototype; a second cache layer masked the first) → revert the fix and
  watch the test fail before believing it. (09-12, 09-17)
- **A revert "proves" the code is dead** → the revert was wrong: a later CSS declaration won,
  or a scripted edit hit the wrong line → anchor edits on the line that names the thing. (09-19, 09-20)
- **"Visible" in the test, not on screen** → `hidden` is false inside a `display:none`
  parent → assert `getBoundingClientRect().height > 0`; compare pixels for clipping. (09-19, 09-17)
- **A layering bug in a screenshot** → a 250ms fade-in → check computed opacity and
  `elementFromPoint` before touching z-index. (09-26)
- **`FAILED (errors=1)` with no test name** → `| tail` cut it → read `test-failures.log`. (09-22)
- **A targeted run said OK, CI went red** → `run_tests.py` reads only its FIRST argument, and
  structural guards live elsewhere (`_handle` < 220 lines, `OTHER_BRANCHES`, page scans) →
  one file per invocation; full suite for orchestrator, dispatch, `access.py` or page changes. (10-01)
- **CI red for one obvious reason** → fail-fast marks the other jobs `cancelled`, not
  failed → read every job; read a branch's CI after its first push. (09-17, 09-26)
- **Browser test passes here, fails on CI** → the page re-derives state from its own polls
  (`/api/health` every 12s), or the test counts frames tuned to this laptop → hold state
  against the polls; assert shape, never a frame count or duration. (09-26, 09-17)
- **A test fails at certain hours** → a time with no date, or a day word counted from now
  near midnight → give every time a day; stop the clock (`StoppedClock`); reproduce with
  `TZ=XXX-04:39:10`. (09-26, 09-27)
- **Unit tests green, the page does nothing** → the fixture used a remembered shape
  (`content`) where the client sends `{"role", "text"}` → copy fixtures from the client's
  code; use the feature through the page. (09-26)
- **A test that cannot tell the fix from the bug** → the fixture put the distractor next to
  the answer, unlike the live failure → build fixtures in the live failure's shape. (10-01)
- **A docs edit fails `test_planner`** → the repo-prose corpus test routed a doc
  sentence to a tool ("split at full stops": `full` is a window position) → reword the doc. (10-01)
- **A corpus or harness gives a confident number** → wrong register (docs, not conversation),
  ids that moved, a wrong check → read raw output; resolve documents by source substring. (09-20, 09-12, 09-26)
- **An offline eval disagrees with the live app** → a different document pool changes IDF →
  reproduce on the live store's contents (`data_dir/knowledge.json`). (10-01)
- **Running the suite opened YouTube / changed the volume** → OS handoffs escape the socket
  guard → the runner stubs `os.startfile`, `webbrowser.open`, `keybd_event`. (09-27)
- **Windows CI: same file, different path** (`RUNNER~1`) → compare with `samefile`. (09-28)

### Routing and the chat model
- **"what's the best way to cook rice" took 20-80 s** → `_advise` and `_DECIDING` treated
  every "best way to" as a decision, so a how-to went to the advisor's research run → only an
  explicit decision ("help me decide", "should I X or Y") goes there; a how-to is a plain
  question answered directly (Jeevan's call). (10-06)
- **"Play my voicemail/messages" opened YouTube** → the broad `play <target>` rule
  treated a personal inbox as a song, and the music tool would do the same with a
  model-routed command → share one personal-message guard between router and tool;
  keep a song titled "Voicemail" playable. (10-05)
- **"Write up a one pager" put `up` in the document topic** → the document heuristic
  stripped `write` but not its phrasal particle → consume `write up` together, and assert
  the generated file's request and visible reply. (10-05)
- **"Throw together slides" answered as chat or saved a PDF** → the heuristic and
  document format parser recognized only narrower creation verbs → keep both front-deck
  expressions aligned and assert the saved PPTX format through `handle()`. (10-05)
- **"Set the volume to fifty" answered as chat** → the level route accepted only digits,
  though voice transcription spells numbers out → use the calculator's existing number
  grammar for level words and assert the visible percent reply through `handle()`. (10-05)
- **"Could you make a slide deck" returned a PDF** → the router understood the request, but
  `document.split_format` recognized only `can you`/`please` before a front-named deck → keep
  the router and format parser's courtesy forms aligned; assert the saved format and reply,
  not just the routed command. (10-05)
- **A request answered by a model that cannot see the data** → a hand-written word list
  missed plurals (`_TOOL_SIGNALS`) → enumerate lists against real inputs; add a noun as users
  say it. (09-20)
- **"Find the email from Alex about the budget" read an inbox digest** → a broad
  "find ... email" rule won before targeted search, and IMAP treated a natural sender/topic
  phrase as one TEXT literal → route named senders first and search FROM plus TEXT. A
  `@gmail.com` sender is not an instruction to switch to Gmail OAuth; only an explicit
  provider suffix chooses that account. Assert the visible matching reply. (10-05)
- **"Search my email for the budget" got a chat answer** → search recognized "search email"
  but not the spoken possessive before "email" → accept "my/the" and assert the mailbox
  query and reply through `handle()`. The dictated "and then tell me what you find" must
  not be sent as literal search text. (10-05)
- **"Emails from yesterday" searched for a sender named yesterday** → the sender search
  ran before a period-based inbox read → reject a time span as a sender while preserving
  "from my landlord". A mention such as "new email standard" is not an inbox-read
  request; require a whole-sentence unread ask. (10-05)
- **One phrasing routes, fourteen do not** → an exact-string route → match the shape, polite
  prefix included; reproduce a backlog item's scope first. (09-20)
- **Every phrase with an apostrophe missed its tool from a phone** (8/8 contract phrases) →
  keyboards type "what’s" and every route is written with `'` → `_fold_apostrophes` folds
  them up to the first verb that takes a path. Exempting only sentences that START with one
  folded "please read file Jeevan’s notes.txt" (Codex's review). Test input copied from a
  desktop keyboard hides the whole class. (10-05)
- **`read file "C:\…\notes.txt"` → "File does not exist"**, and with "please" in front the chat
  model answered → Windows' "Copy as path" quotes the path, and a `?` after it was read as
  part of the name → `_clean_paths` unquotes a quoted span that looks like a path and drops a
  `?` after one. A quote counts only at a word's edge, or the apostrophes in
  `C:\Jeevan's docs\the kids' photos.txt` pair up. (10-05)
- **Routed to the right tool, which then refused it** ("what's 70 fahrenheit in celsius" →
  "I don't know how to do that yet") → the router prefixes `convert` and the parser stripped
  it only before "how many" → strip it before every lead-in the parser reads. A contract that
  checks the routed command cannot see this: assert the reply. (10-05)
- **A trailing "please" hid the request** (24/73 contract phrases) → every route ends in
  `[?.!]*$` → `strip_address` drops a trailing please/thanks unless it is the content ("say
  please"). A test of a router guard must reach the router: a bare "remind me …" is a direct
  command, and the first test passed with the guard removed. (10-05)
- **Ordinary talk triggers a tool** ("good news, i got the job" → headlines) → a keyword
  anywhere in the sentence → whole-sentence grammar; refuse words only talk uses. (10-01)
- **"what's happening in the world today" got a web search, not headlines** → the news
  grammar needs the word "news" → `_WORLD_NEWS`, whole sentence, a pronoun is no topic. (10-05)
- **"I don't know how to do that yet" for an answerable question** → the router echoed the
  input as a command nothing runs → answer it as conversation (`_DECLINED`). (10-01)
- **A reminder refused for "no time", though the time was said** → it was said first
  ("every monday at 9 remind me…") and only the words after "remind me" were kept → carry a
  leading run of time words (`_TIME_FIRST`); "tell me at 3pm to…" starts like a question, so
  it needs its own route (`_TELL_ME_AT`). Found by an indirect-phrasing corpus, not the
  contract: a prefix check cannot see a dropped time. (10-05)
- **The model asks "May I…?" and never acts** → the prompt quoted the forbidden replies
  (17/25 → 1/25 reworded) → state the rule, never the bad example. (10-01)
- **The model says it cannot do something it can** → the prompt said only what it cannot →
  `_CAPABILITIES`; a new tool goes there and into the routing contract, in the model's own
  phrasings. (09-28)
- **The model reports a file, image or action that never happened** → `_NO_TOOL_CLAIMS`;
  widen the guard to the class, not the instance. (09-12)
- **A list in a pattern drifted from its owner** (`_POSITION_WORD`) → derive it from the
  owning module, or test that the two lists match. (09-20)
- **"my screen is cracked, what should i do" took a screenshot** and sent it to the vision
  model (MEDIUM, so no approval card) → `"my screen" in lowered` and a look-verb-anywhere
  regex → `_SCREEN_ASK`, the whole sentence asking to look. A route that captures private
  data must never match on a noun alone. (10-05)
- **"what do you see in this code" turned the webcam on** - the capture has no approval gate
  and the frame goes to the vision model → "what do you see" / "look at me" matched anywhere →
  `_WEBCAM_ASK`, whole sentences or a request that opens by naming the camera. Same class as
  the screen: sweep every route that captures for substring matching. (10-05)
- **"minimize distractions while studying" answered "no window matches"** (22 of 24 ordinary
  sentences) → `_ARRANGE_ASK` took a verb with a position word anywhere after it, or
  "minimize" plus any word, and the `split`/`snap`/`arrange` prefixes only looked for a
  position → a whole sentence with a short name that no function word is part of, shared by
  the router and the prefixes (`asks_to_arrange`). (10-05)
- **"give me a template for a follow up email" read the inbox** (14 of 18 sentences that
  only mentioned email; MEDIUM, so no card) → `"new email" in lowered` and verb-anywhere /
  "latest … email" regexes → `_MAIL_ASK` / `_MAIL_DIGEST_ASK`, the whole sentence asking for
  your mail. The same sweep, applied to a route that reads private data. (10-05)
- **"the police will investigate the crash" started a web research run** (14 of 16 ordinary
  sentences) → `research|look into|investigate|read up on` matched anywhere →
  `_RESEARCH_ASK`: the request opens the sentence (polite prefix or "I want you to"), and
  "do some research on X" no longer keeps "on" in the topic. (10-06)
- **"stop the alarm" deleted a schedule** → an ambiguous verb wired to the destructive
  action → the safe reading wins and says how to ask for the other. (09-26)
- **"remind me how to center a div" → "I could not find a time in that"** → "remind me" was
  always a reminder, on the direct prefix, the instant router and the model's route →
  `asks_to_be_told`: "remind me what/how/who/where/why…" with no time in it is answered,
  on all three. "remind me what to buy at 5pm" is still a reminder. (10-06)
- **"my name is on the list" stored the user's name as "on the list"**, which every chat
  prompt then carried → `_MY_FACT` took any value → `_plausible_fact`: a phone needs
  digits, an email an `@`, a date may start with "on"/"in" but not "next"/"coming", and
  any other value may not start with a preposition or "when/that/not". (10-06)
- **"do not open youtube" opened YouTube** ("do not remind me…" set the reminder) → routes
  match anywhere, so a leading negation was skipped → `is_negated` holds EVERY route, since
  the model may still answer with the positive; "don't forget to…" asks for the thing and is
  not a negation - it may become a reminder and nothing else, since the model once routed it
  to `open url …mom.com`. Guarding only the instant router, and calling one good live sample proof,
  was #191's mistake (Codex's review): a safety property needs a guard, not a sample. (10-05)
- **A shell command for a sentence that never asked for one** ("change my desktop background"
  → `reg add HKCU\…`) → the LLM router fills the gap with the shell, despite the prompt
  saying not to → `_repair_shell_command`; the rest of tool substitution is an OPEN limit in
  CLAUDE.md, measured and not fixed. (10-05)
- **"it's" saved as a name; `1e309` read as 309** → filler accepted; no left boundary on a
  number → `_meaningful`; `(?<![\w.])`. (09-26)
- **A validator rejects the common phrasing** → it rejected a shape → look for the wanted
  thing. (09-11)
- **"convert 5k to miles" said kelvin and miles do not convert** → a bare "k" was always
  kelvin → against a length it is kilometres. Found with four routes missing a common form
  ("get me up at 6", "split 90 dollars three ways", "how far is it to boston", "do i need
  anything from the store") by an everyday-phrasing corpus. (10-05)
- **"how long does it take to drive to chicago" got a chat answer** → only "how long to
  drive" was a route, and nothing could start from where the user is → `_DRIVE` (start
  before, after or absent) and `distance here to …` via the IP lookup "around me" uses. (10-05)
- **Follow-ups lose the conversation** → a model-facing path without `history` → every one
  takes `history` and `context_block`; rank on the user's words (`context_query=`). (09-10)
- **Confident wrong counts from the agent** → it got a sample → hand it quantities
  (`total_files`, `by_extension`) and say when text was clipped. (09-11)
- **A count instead of an answer** ("1 scheduled job(s).") → the content was only in `data`
  → name what is counted. (10-01, 09-11)
- **The agent answered with its own deliberation, or a file that does not exist** → a reply
  with no ACTION/FINAL was taken whole, and the model writes ACTION, an invented OBSERVATION
  and a FINAL built on it in one reply → trust a reply up to its first upper-case ACTION, cut
  an OBSERVATION it wrote, ask once more (#172). Log the raw replies before trusting an eval. (10-02)
- **A command the agent was cut off in the middle of** → `finish_reason: length` stopped at
  the transport → `CutOff` reaches the loop; never run, one retry, never the partial (#178). (10-02)
- **What the user said ("one page", "largest") was gone from the routed command** → the LLM
  router rewrote the sentence → the instant route keeps it, and a `_repair_*` hook puts back
  what the router drops (#173, #176). (10-02)

### Models and providers
- **A tier "busy" for hours** → a 400/401/404/410 misconfiguration read as congestion →
  `classify_failure`; send one request by hand and read the HTTP body. (09-11, 09-09)
- **`HTTP 400 thinking_token_budget is not yet supported`** → never send `reasoning_budget`. (09-11)
- **A listed model returns 404** → `/v1/models` is a catalog → call a model before wiring it. (09-11)
- **Benchmark numbers make no sense** → unpaced turns tripped the 60s cooldown → pace ~12s. (09-11)
- **A broken tier keeps being marked busy** → a caller recorded without the reason →
  pass `on_failure`; every swallowing `except` records (`failures.py`). (09-30, 09-17)
- **"Could not reach my language model" on a slow router** → routing shared the answer's
  45s → `route_timeout` 2.5s, timeout falls through to chat. (09-17)
- **Fast tier always "busy"; replies cut mid-sentence** → a slow reasoning model on the fast
  tier; 900-token caps. (09-09)
- **Invented captions from `nemotron-parse`; dead env names** → drop `Caption` regions; every
  env name gets a `config.py` field. (09-11)
- **A long reply stops mid-sentence, with no notice** → a 2,048-token stream cap that never
  read `finish_reason` → 16,384 on NVIDIA (measured to accept 65,536) and a cut-off note; a
  note alone is not an answer, or the fallback never runs (#177). (10-02)
- **The model will not shorten its own draft** (335 → 319 words, asked for 142) → draft the
  request again to a smaller budget instead (#176). (10-02)

### Retrieval: knowledge, files, context
- **A summary or answer that is a wall of Markdown** → a splitter flattened lines → one
  splitter, `terms.sentences` (`structure=True` keeps rows and code for Q&A). (10-01)
- **"do"/"in" outrank the subject with one document indexed** → equal IDF → function words
  weigh `FUNCTION_WEIGHT` 0.2; zero broke follow-ups. (10-01)
- **"J.A.R.V.I.S" never matches "jarvis"** → a step read raw text where the tokenizer
  collapses acronyms → every pre-check reads what the tokenizer reads. (10-01, `terms.py`)
- **`TypeError: 'int'/'list' object is not callable` after an import** → a parameter or local
  of the same name shadows it → import a common word under an alias. (10-01)
- **Answers from an unrelated scrape** → passage score re-decided the document → the referent
  picks the document, the question the passage; generated kinds are discounted. (CLAUDE.md)

### Time and dates
- **A reminder an hour off across DST** → `astimezone()` is a fixed offset → `local=True`;
  count calendar days. (09-28)
- **A job fired twice in the repeated hour** → target rebuilt from each tick's offset → the
  zone's first occurrence; keep `fold`. (09-28)
- **"Reminder date/time must look like YYYY-MM-DD"** → no natural-time parser → `timeparse.py`;
  one-right-answer values are parsed, never inferred. (09-17)
- **`ValueError: Invalid format string` on Windows** → `%-I` → strip zeros by hand. (09-17)

### Web server, sessions, security
- **Client sees a connection reset, not the 4xx** → the body was not read →
  `_drain_request_body` (bytes, not a flag). (09-17)
- **Flaky phone pairing; two apps answering** → a second process bound the port →
  `_refuse_if_running`. (09-12)
- **ETag/304 never used; a token cacheable** → duplicate `no-store`; a directive that starts
  working is new behaviour → measure the real client; everything opted out says `private`. (09-19)
- **A session outlives a reset or disable** → no credential epoch → sessions bind to it. (09-30)
- **A clean merge dropped a rule** → a second copy of a check → find copies by what they read. (10-01)
- **Secrets in failure records** → recorded what was caught → record what you constructed. (10-01)
- **A fallback states a wrong cause** ("Wrong passcode." on a 429) → never assert a cause you
  have not established. (09-12)
- **OAuth: popup `closed`, redirects, Riva hangs** → bound completion + Cancel + expiry;
  re-navigate after the first redirect; future + deadline + cancel. (09-28)

### Page and UI
- **A feature shipped but never on screen** → it lived in a `display:none` panel → assert
  boxes; deleting the code that shows a thing leaves its styles looking healthy. (09-19)
- **A popover under another layer despite a higher z-index** → an ancestor makes a stacking
  context (`position` + `z-index`, `backdrop-filter`) → compare at the root;
  `body:has(...)` to swap. (10-01)
- **Notifications cover the screen** → an unbounded stack → cap + "+N more"; Dismiss all. (10-01)
- **Several reminders at once: one loud, harsh chime and a pile of OS notifications** → each
  card chimed at the same instant (5 due = 10 tones summed to 0.9 of full scale) → announce
  the batch once (`announceReminders`); the cap above fixed the cards, not the sound. (10-05)
- **A CSS rule silently gone** → a stray `*/`. **A grid collapsed** → a 0-width track. (09-17, 09-09)
- **Advice in the UI that does nothing** → check the control can deliver it. (09-17)
- **A hidden surface hid wrong messages** → check each message before surfacing it. (09-27)
- **Browser-pane screenshots time out** → use headless Playwright at real viewports. (10-01)

### Data and state
- **Test data in the user's real store** → a throwaway instance changed only the port →
  set `LAPTOP_AGENT_DATA_DIR` too; stop it when done. (09-17, 09-12, 09-28)
- **State leaks between tests or runs** → persistence read the process-wide config → take the
  location from the caller (`data_dir`). (09-17)
- **A TTL cache blocks every caller on a miss** → collection under the lock → background
  refresh. (10-01)
- **Vault links "broken"** → audited the subfolder → the root holds `.obsidian`. (09-09, 09-12)

### Shell, git and editing
- **A file mangled by escapes** → a heredoc collapsed `\\` → exact-match edits or the Write
  tool; `bytes([0x89])` over `b"\x89"`. (09-17, 09-20, 10-01)
- **A scripted edit broke another line or file** → anchored on part of a line → anchor on the
  whole line, newline included. (09-12)
- **`sed -i` rewrote CRLF as LF** → harmless under autocrlf → check `git diff --stat` for
  whole-file churn. (10-01)
- **`pkill -f` killed its own shell** → write the pattern as `m laptop_agent[.]webui`. (09-26)
- **Import error on Python 3.11 only** → a backslash inside an f-string expression. (09-26)
- **Committed to `main`; committed `docs/review/*.png`** → check the branch before the first
  commit; discard noisy paths before `git add`. (09-11, 09-22)
- **Nearly rebuilt an existing feature** → `git grep` before building. (09-09)
- **`gh pr merge` refused as "merge without review"** → the auto-mode classifier does not
  count PR-comment verdicts → never route around it; hand Jeevan the commands. His terminal
  is Windows PowerShell 5.1: `foreach (...) { ...; if ($LASTEXITCODE -ne 0) { break } }`,
  never a bash loop or `||`. (10-02)
- **A stacked PR would merge into its parent branch, not main** → `gh pr edit N --base main`
  before it is merged, with its parent merged first in the same batch. (10-02)
- **A new worktree cost ~27k tokens of context** → the Read tool loads that worktree's
  CLAUDE.md → read files in other worktrees through the shell. (10-02)

## Session 2026-10-01/02 — driving the app found nine user-visible bugs

Exploratory testing through the live page (throwaway instance, own port and data dir) and
real-viewport screenshots. Fixes: #164 (chat), #165 (files), #166 (news), #167 (routing),
#168 (schedules), #169 (knowledge), and the reminder-stack PR.

- **A keyword anywhere in the sentence is not a request.** The news route fired on "news"
  anywhere: "good news, i got the job" fetched the day's top stories, "fake news is a
  problem" searched for "is a problem", and "latest tech news" lost "tech" because a topic
  counted only with nothing before it. **Rule: a route that keys on a noun reads the whole
  sentence around it; when the words beside the noun are ones only talk uses, refuse and
  let the router decide.**
- **The router can hand the sentence back.** It echoed "convert 100 usd to eur" as a
  command the dispatch had already declined, so the turn ended in "I don't know how to do
  that yet" - while "how much is 100 dollars in euros" got a live rate with sources.
  **Rule: a command equal to the input that nothing dispatched is conversation.**
- **A prompt that quotes the failure teaches it.** The chat prompt illustrated its
  no-permission rule with the forbidden replies, and the model copied them: 17/25 replies
  asked leave to act, 1/25 once the rule was stated without quotes and told to end with
  the instruction. **Rule: state the rule, never the bad example.**
- **A second copy of a fixed function kept the bug.** The knowledge base had its own
  sentence splitter - the one just fixed in the file tool - and answered "how do I start the
  app" with 16,170 characters of badges, image links and a table of contents. The
  `terms.py` rule again: **one splitter, shared, or the fix reaches one caller.**
- **Zero was the wrong weight, and a guard test said so.** Making two-letter words
  stopwords fixed passage choice with one document indexed, and broke
  `test_the_referent_picks_the_document`: a follow-up's own document had no matching
  passage left. A sweep found 0.2 (README alone 4/12 -> 9/12, no follow-up broken). **Rule:
  when a tuning change breaks a guard test, the change is too big - sweep, do not delete
  the guard.**
- **A pre-check must read what the tokenizer reads.** The substring check before tokenizing
  read raw text, where "jarvis" is not in "J.A.R.V.I.S"; asked for the name alone, the
  window that says it was skipped.
- **An import shadowed by a parameter of the same name.** `from terms import sentences` met
  `summarize_text(..., sentences: int)`, and a local `sentences = [...]` in `answer()`:
  `TypeError: 'int' object is not callable`. **Rule: import a common word under an alias.**
- **`run_tests.py a.py b.py` runs `a.py` alone and prints OK** - it reads its first argument
  as a discover pattern. Four "targeted" files once ran 124 tests in 1.3s.
- **The first fixture modelled the wrong failure.** It put the distractor next to the
  answer, so one window held both and the test could not tell a good weight from a bad
  one; live, the distractor was elsewhere in the README. **Rule: build the fixture in the
  live failure's shape, and assert the property that failed live.**
- **Docs are test input.** `test_no_sentence_in_this_repos_own_prose_routes_to_the_window_tool`
  reads CLAUDE.md, README and ERRORS.md, and a new line beginning "split at full stops"
  routed to the window tool (`full` is a position). The doc was reworded; that narrow
  router class is accepted, as for window placements.
- **An offline eval disagreed with the live app.** Offline indexed three documents, live
  had one, and IDF changes with the pool: the browser-tab row led offline and was absent
  live. **Rule: reproduce on the live store's exact contents (`data_dir/knowledge.json`)
  before believing an offline number.**
- **A z-index inside a stacking context counts only there.** The settings popover (70) sat
  under the reminder tray because it lives inside the header, `position:relative;
  z-index:60; backdrop-filter`. And five fired reminders covered a phone's whole screen:
  **a notification stack needs a cap and a summary.**
- **A count is not an answer, again.** `schedule list` said "1 scheduled job(s)." and named
  none - the same lesson as the file listing in `_humanize`.

## Session 2026-10-01 — GPU metrics depended on elevation; cache misses blocked requests

- **A vendor utility is not the only source of GPU telemetry.** On this Optimus laptop,
  `nvidia-smi` exits 4 without elevation, while Windows GPU performance counters return
  both adapter LUIDs. GPU-01 keeps the vendor path first and falls back to those counters.
  WMI names only the active AMD adapter, so matching names by list position would mislabel
  cards; use DXGI's exact LUID and leave unmatched names/capacity generic/unknown.
- **A TTL cache can still block every caller on a miss.** The former metrics cache ran
  collection under its lock. Adding a roughly two-second GPU probe there would stall
  `/api/metrics`, including concurrent readers. Windows now uses one background refresh
  with independent snapshots and a two-second TTL measured after collection completes.
  Failed refreshes are throttled too. Failure records contain fixed causes, not raw
  process output, and each cause is recorded once per process.

## Session 2026-10-01 — a clean merge that was still wrong (#145 after #148)

- **A merge without a conflict can still drop a rule.** #148 taught `_principal` that a
  session is good only for the epoch it was granted under. #145, written before it, read
  the session cookie itself for its Google routes and checked only `disabled`. The two
  merged with no conflict, and a session `/api/me` refused was still signed in to the
  Google routes: it read the linked email and blocked Google sign-in in its browser. In the
  pair log I had told Codex #145 needed no change, having checked where sessions are
  *created* (`SESSIONS.create`) and not where they are *read* (`SESSIONS.resolve`). **Rule:
  when a check gains a rule, find every copy of the check by what it reads, not only by what
  it writes, and leave one definition.**
- **Google sign-in recorded no failure at all.** A wrong client secret (HTTP 401
  `invalid_client`) said "Start again", forever, with nothing in `failures`. The records are
  now strings this code builds — status, OAuth error code, the claim check's name, an
  exception's type — because the token request holds the code and the client secret.
  **Rule: where a path handles secrets, record what you constructed, not what you caught.**

## Session 2026-10-01 — forecasting validation traps caught before integration

- **Winning a selection backtest is not independent evidence.** A core that picks a
  model only when it beats a baseline will pass that comparison by construction. Check
  untouched future values too; tune and discover season on the prefix, never on the
  full series before pretending to backtest it.
- **Intervals need their own horizon errors.** One-step residuals pooled across horizons
  can understate longer forecasts' uncertainty. Calibrate separately from selection,
  return null when calibration is too sparse, and never invent an infinite MASE for a
  constant/zero-scale series. Tests use deterministic synthetic data only.

## Session 2026-09-30 — a broken model reported as busy by the keep-warm ping

- **A caller that drops the reason undoes the split the reason exists for.**
  `classify_failure` tells a misconfigured tier (broken: retried after 900s, kept across a
  restart) from a loaded one (busy: 60s), but `ping()` swallowed its exception and the
  keep-warm loop called `record("fast", False)` with no reason, which reads as busy. So
  every 200s the ping demoted a fast tier a chat turn had found broken: retried after 60s
  instead of 900, and dropped from `model_status.json`, so a restart forgot it. Found in
  the review of #144; `ping` now takes the same optional `on_failure` sink as `answer`.
  **Rule: whoever records a failure passes the reason it was given, or it overwrites one
  it was not.**

## Session 2026-09-30 — a sign-in that outlived the reset it raced

- **Revoking sessions cannot end one created after the revoke.** A sign-in checks the
  password (0.6s of scrypt) and then creates its session. A developer's reset in between
  wrote the new hash and revoked every session, and the sign-in, already verified against
  the old hash, created a live one; after disable-then-enable the same happened. The review
  reproduced it at the real hash cost, and the test replays it without timing by answering
  the check from the account as it was. **Rule: a session carries the version of the
  credentials it was granted under; a revoke cannot see what is created after it.**
- **A test of a sign-in must use the session it gets.** Leaving the epoch out of
  `_start_session` passed every test, since they asserted the sign-in's 200 and never used
  the cookie - and after any password change that bug would have locked everyone out.

## Session 2026-09-28 — a recording request answered with "May I?" after every "yes"

- **A request nothing routes reaches a model that thinks it can do it.** Asked "record voice
  upto 20 seconds" in an app older than REC-01, the fast chat model answered "May I record
  your voice for up to 20 seconds?" and asked again after each "yes". The prompt already
  forbade asking permission, but its list of tool actions named windows, apps, reminders,
  mail and downloads and not recording, and the capability line never mentioned it, so the
  model took recording for its own to do once allowed. REC-01 routes that exact sentence,
  but not the model's own wording ("for up to 20 seconds"), "can you ...", "twenty seconds"
  (how Vosk writes it) or "start recording". **Rule: a new tool goes into the chat prompt's
  capabilities, and its routing contract includes the phrasings the model itself uses.**
- The app in the report was a scratch instance on port 8791 (data in `E:/Temp/jarvis-test`)
  that an agent had started the day before; it ran whatever was checked out then. **Stop a
  throwaway instance when the check is done.**

## Session 2026-09-28 — a scheduled job that fired twice in the repeated hour

- **A target rebuilt from each tick's own offset moves with the clock.** `Schedule.is_due`
  built today's target as `now.replace(hour, minute)`, so on the night the clocks go back
  a 01:30 job fired at 01:30 EDT, and at 01:30 EST the target was an hour later than that
  run and it fired again. The ticker now asks for today's target by the zone's rules, the
  first occurrence of a repeated time. "Fire once per local date" looked simpler and was
  wrong: `mark_ran` records the finish time, so a job running past midnight would have
  skipped the next day.
- **A stopped clock must keep which of two identical readings it is.** The test helper
  returned a naive local time, and a naive 01:30 in the repeated hour means the first
  occurrence, so a clock stopped at the second 01:30 read as the first and the test of
  that hour passed against the bug. Breaking the fix on purpose is what showed it.
  **Rule: a naive local time cannot carry the repeated hour; keep `fold`, or stay aware.**

## Session 2026-09-28 — reminders an hour off across a daylight-saving change

- **`datetime.now().astimezone()` is a fixed offset, not a zone.** `timeparse` stamped it
  onto every day it placed a time, so on this Eastern-time laptop "Monday at 7am", said on
  Friday 30 October 2026, was stored as 07:00-04:00 and would ring at 6:00 once the clocks
  went back; the confirmation read it back in the same offset and said 7:00. **Rule: an
  offset read today says nothing about another day. Place each time with the zone's rules
  for that day, and count days on the calendar, not in hours.**
- **A fix applied by hand to a list of call sites misses one.** The first version changed
  eight `parse_when`/`describe` calls and missed the repeating path's one-off fallback.
  **Rule: when a fix has to reach every caller, make a test find the callers.**

## Session 2026-09-27 — a reminder test that failed one minute a day

- **A time given relative to now still has a day, and it is not always today.**
  `test_due_and_upcoming_with_the_time_to_the_next` set a reminder "a minute ago" and
  asserted the card said "today at". For the first minute after midnight a minute ago is
  yesterday and the card gives the full date: CI run 36280996201 failed exactly so, on a
  UTC runner at 00:00. Moving the reminder cannot fix it, because the day word is counted
  from the clock `_reminders_snapshot` reads, so the test now stops that clock
  (`StoppedClock`) and asserts the exact string. To reproduce a clock-dependent test, set
  `TZ` to a fixed offset that puts local midnight inside the run (`TZ=XXX-04:39:10` works to
  the second, on Windows too): the old test failed on the first try, and running every test
  file from 00:00:05 found no other that fails in that minute. **Rule: a test that asserts a
  day word stops the clock that decides it. "Give every time a day" is not enough when the
  day is counted from now.**

## Session 2026-09-27 — voice notices on screen

- **A hidden surface hides the bugs in what is written to it, not only the text.** Voice
  notices went to `#vtrans`, `display:none` since June, and one of them was wrong as well
  as invisible: the listening turn showed `/api/transcribe`'s message whenever `ok` was
  false, and `ok` is false for silence too, so moving the notices somewhere visible as
  they were would have put a "nothing found" card up after every quiet moment. **Rule:
  before surfacing messages that were never seen, check each one was ever right - nobody
  has read them, so nobody has checked them.**

## Session 2026-09-27 — the suite played Despacito

- **Every run of the unit suite opened a real YouTube video on this laptop.** The
  everyday-requests routing contract (added in `7842ed2`) fakes the music tool's YouTube
  lookup, which always answers `kJQP7kiw5Fk`, but left the real `WebTool` behind it, and
  its approver lets MEDIUM through, so "play …" reached `os.startfile` and Windows opened
  the video in Chrome. The runner's socket guard never saw it: the test process makes no
  connection, the browser does. It was reported as "an automation that keeps opening
  YouTube", because another session's sweep was rerunning that module 48 times.
  The same tests pressed the real volume keys through `keybd_event` (`MusicTool.set_volume`),
  which is what kept setting the laptop to 50%. `run_tests.main()` now replaces
  `os.startfile`, `webbrowser.open` and `keybd_event` with a stub that succeeds and does
  nothing. **Rule: a guard on the network is not a guard on side effects.
  Anything handed to another process (the shell, a browser, an app) escapes a socket check,
  so fake the handoff, not the connection.**

## Session 2026-09-26 — everyday-requests hardening

- **Four new browser tests passed here and failed on CI, because the page re-derived what
  they had set.** They forced the server speech path, but the page re-reads its engine from
  `/api/health` on load and every 12s: CI has no engine, so an answer landing mid-test
  switched voice to the browser recognizer, while this laptop's own engine kept it in
  place. **Rule: a test that sets page state must hold it against the page's own polls -
  and when a browser test passes locally, ask what this machine has that CI does not.**

- **A test that names a time of day without a date passes or fails by the hour it runs.**
  `test_which_one_then_the_pick` set "call mom at 6pm" beside "buy milk tomorrow at 9" and
  cancelled "the second one". After 6pm, "6pm" means tomorrow evening, so milk sorted first
  and the wrong reminder went - on CI's UTC runners, every run from 14:00 to 20:00 EDT. It
  passed the morning it was written. **Rule: in a reminder or timer test, give every time
  a day, or the suite depends on the clock.**

- **Pausing on a loud moment made the trigger provisional, and the counter behind it kept
  treating it as final.** Barge-in's three-in-25s switch counted every loud moment, so once
  the reply paused instead of stopping, three coughs in one reply switched voice
  interruption off for the session - silently, since the notice lands in the hidden voice
  panel. Moving the count to where an interruption commits was not enough on its own: it
  had also been the only limit on false pauses, and every sentence re-arms barge-in. The
  same change said server-STT listening waits for "a quarter second of sound", but its
  counter never reset, so clicks seconds apart added up to speech - the reset barge-in
  already had, missing from its twin. **Rule: when an action becomes provisional, move
  whatever counted it to where it becomes final - after asking what else that count was
  quietly limiting.**

- **Eight commits were red on Windows and nobody looked.** CI ran on every push to the
  branch; I ran the suite only locally, on Linux, and first read a CI result once the PR
  existed - two Windows-only failures (an 8.3 short temp path, and a path Windows reports
  missing where Linux raises) had been red since the third commit. **Rule: after the
  first push to a branch, read its CI run - every job, not the summary - before building
  further on it. A local pass on one OS says nothing about the others the matrix runs.**

- **The voice loop answered itself, again, through three separate holes.** The echo guard
  compared a transcript with one spoken sentence at a time, so a transcript straddling two
  of our sentences matched neither; server barge-in learned our echo level before playback
  had started (so it learned silence) and then transcribed 12s of its own reply; and the
  server-STT listening turn had neither the echo check nor the 400ms tail guard the
  browser path had. **Rule: two implementations of one guard drift - when a check exists
  on one path of the voice loop, grep for the other path before calling the loop fixed.**

- **Tests that build their own input shape passed against code that did nothing in the
  real client.** Follow-up answers ("set a timer" → "How long?" → "10 minutes") read
  `history[i]["content"]`. Every unit test passed - I had written the fixture with
  "content" too. In the real page every follow-up fell through to chat, because
  `app.js sessionHistory` and the CLI send `{"role", "text"}`. Found only by typing the
  conversation into the page in Chromium. **Rule: a fixture for client input is copied
  from the client's own code, never written from memory of a common API shape; and a
  conversational feature is not done until it has been used through the page.**
  `normalize_history` already read both keys - use the shared reader, not a new one.

- **An ambiguous verb was wired to the destructive action.** "stop the alarm" routed to
  `reminder delete alarm`, which matched the repeating job first: with nothing ringing it
  deleted a weekday alarm schedule (an overslept morning), and while one was ringing it
  deleted the schedule and left the ring. No test covered it; reading the replies of a
  corpus did. **Rule: where a phrase has a safe and a destructive reading, the safe one
  wins and the reply says how to ask for the other.** Now `reminder stop` touches only
  what is going off (or a running timer); `reminder delete` prefers the ringing one.

- **A digit inside a token was read as a number.** "timer 1e309 minutes" set a 309-minute
  timer: `\d+` had no left boundary. **Numbers in natural-language parsing need
  `(?<![\w.])`**, and a fuzz corpus should include `1e309`, `0x10`, `9` * 50.

- **Filler was stored as an answer.** Replying "it's" to "You haven't told me your name"
  saved the name "it's"; "to" became a reminder reading "to". A slot-filling reply must
  carry something beyond filler words (`_meaningful`).

- **A screenshot taken mid-animation looked like a layering bug.** The approval card
  appeared translucent over the composer on a phone. It was its 250ms fade-in: computed
  opacity 1 and `elementFromPoint` at its centre said "card on top" once settled. Check
  those two before touching z-index or backgrounds. (The same look did expose a real
  defect: the preview ran items together - fixed with `white-space: pre-line`.)

- **The harness's own check was wrong.** It reported "0 of 24" reminders listed; it looked
  for `"number N "` with a trailing space, and every line ends in a newline. Printing the
  raw listing settled it - and exposed the real gap, "You have 35 reminders" over twenty
  lines with no word about the rest. **When a harness number looks wrong, read the raw
  output before believing either the number or the code.**

- **Python 3.11 rejects a backslash inside an f-string expression** (3.12 allows it).
  `f"{re.sub(r'^to\s+', ...)}"` failed at import. 3.11 is the floor and CI runs it:
  compute outside the f-string.

- **`pkill -f "python3 -m laptop_agent.webui"` killed its own shell** (exit 144): the
  pattern matched the `bash -c` line running it. Use `pkill -f "m laptop_agent[.]webui"`.

## Session 2026-09-22

- **A ghost that was probably truncation, not flakiness.** The backlog carried "one
  unreproduced test error": `FAILED (errors=1)` with no name captured. `TextTestRunner`
  prints the name and the traceback - immediately above the summary line, so `| tail -3`
  throws away the only part worth having and keeps the part that says nothing. Every
  command in this session that ran the suite piped it through `tail`, including the ones
  that found real failures; those were caught only because I widened the grep afterwards.
  **Rule: the console is not a durable record. When a diagnostic has to survive being
  read later, by someone who no longer has the shell, write it to a file and print the
  path last - after the summary, where a tail still shows it.**

- **Committed two binaries I had been told to discard.** `git add -A` swept up
  `docs/review/*.png`, which the Chromium suite rewrites on every run and which CLAUDE.md
  explicitly says to `git checkout --` unless a review PR wants new evidence. I had
  discarded them correctly four times earlier in the session and then stopped checking.
  **Rule: a known-noisy path deserves a discard immediately before `git add`, not a habit
  of remembering - the habit is what fails on the fifth repetition.**

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

## 2026-09-28 — a recording request was treated as a made-up media file

`record voice upto 20 seconds` could reach `transcribe record 20s` and report a missing
file. There was no microphone-recording route, and target repair accepted any stem token
shared with the conversation. The verb `record` therefore counted as a named file.
REC-01 adds deterministic recording intent and requires the complete transcription
filename in the conversation (or history), rather than one shared stem token. Regression
cases cover the reported sentence, invented `record.wav`, real filenames and near misses.

The first browser persistence assertion expected a reload to reopen a chat automatically;
the existing app starts a new chat. Correct verification reopens the saved original chat
and asserts its actual audio box and transcript. Cancelling while permission is pending
also checks that a late MediaStream is stopped and no file is created.

REC-01's first Windows CI run exposed a test-only path alias: the temporary directory
used `RUNNER~1`, while safe artifact resolution returned `runneradmin`. Comparing Path
spellings failed although they named the same saved WAV. Assert one backend call and
`samefile` identity, which checks the intended file boundary across Windows short names.
The Linux units and Chromium job passed on that revision; the corrected test is rerun.

## 2026-09-28 — Riva could wait forever before reaching local speech

VOICE-02 fixed ordinary SDK failures, but a server accepting the socket and never
answering produced no exception to catch. Riva's synchronous offline_recognize helper
passes no timeout to gRPC. VOICE-03 obtains its future, waits with a monotonic budget,
and cancels the RPC/ closes the channel on timeout, error, Stop and successful completion.
A timeout is logged before auto mode tries local ASR. A Stop that races a connection
error also propagates cancellation instead of accidentally launching fallback.

Eight isolated future tests cover deadlines, cleanup, success, Stop/races, auto/explicit
mode and configuration. A real nvidia-riva-client/grpc call using synthetic silence and
a dummy key to a silent localhost TCP listener saw a TLS ClientHello, returned a timeout
in 0.680 seconds for a 0.5-second budget (including SDK import), and closed the socket.
This tests a stalled transport, not hosted model quality or hardware microphone capture.

## 2026-09-28 — OAuth popup isolation and redirect tests

A popup's `closed` property can become true when a provider isolates its opener, even
though its sign-in flow continues. Cancelling the server flow on that signal stranded
Google sign-in. The original window now waits on its proof-bound completion, with an
explicit Cancel button and ten-minute expiry. The Chromium fake provider deliberately
sets Cross-Origin-Opener-Policy: same-origin to preserve this regression check.

Playwright route handlers only intercept the first request in a redirect chain. The
fake-Google browser fixture reads the local launch 303 without following it, preserves
its Set-Cookie, and performs a new navigation so the provider can be intercepted fully.
HTTP tests independently assert the production 303; browser tests exercise real cross-site
cookie handling. Plain HTTP tests alone did not expose this lifecycle issue.

An old flow's cancellation must not clear a newer flow's proof cookie in another tab.
Cancel now clears that cookie only after a matching flow/proof was actually cancelled.


## Season peak review correction — 2026-10-02

Wrong 11-month cycle for a 12-month signal -> the search stopped on a rising autocorrelation shoulder -> require both neighbours below the candidate; a boundary-check lag is never itself a candidate.

### GPU-01 review follow-up (2026-10-01)

GPU-01 review caught a cache-consumer regression: one-shot status reported old values as
current or omitted a cold CPU sample. Both callers now refresh synchronously. JavaScript
null arithmetic also converted unknown VRAM into zero; test the rendered n/a state.


## GPU adapter labels — 2026-10-02

Indistinguishable GPU rows -> Overview discarded each adapter name -> share escaped adapter-name labels with the drawer, adding distinct indexed fallbacks and the 3D qualifier.

## Diagnostics reference correction — 2026-10-02

Unclear R2 beside baseline MAE -> two different reference predictors -> both now use the fixed training mean. R2 remains a squared-error comparison, MAE an absolute-error comparison. Near-constant training data is refused at normalized sd <= 1e-12; tails under ten rows warn. See docs/analytics.md for the superseding contract and tests.


## ANALYTICS-04 update — 2026-10-01

ANALYTICS-04 validation: negating a symmetric tail around its unchanged mean did not change its variance, so a target-standardization leakage mutation survived. Varying tail scale as well made the test fail when training preprocessing accidentally includes held-out targets. A passing test must distinguish the intended failure.
