# Routing everyday requests

How everyday phrasing is routed, and the rules each one broke once.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

- **Everyday requests.** Driving three corpora of how people actually talk (timers, lists,
  facts, dates, zones, "X and Y", follow-up answers) through `handle()`, then the live
  server and the page, is what shaped this layer; `tests/test_everyday_requests.py` holds
  the result as a routing **CONTRACT** (phrase → command that must run), **MUST_STAY_CHAT**
  near misses, a never-crash sweep with and without a model, and a fuzz of every direct
  prefix. Rules it established, each broken once:
  - **One right answer is computed**: arithmetic, conversions, dates, zones, reminder
    times, random draws. Anything not connected (calendar, smart home, phone) is said
    plainly; currency goes to the live-search answer because no FX API could be verified.
  - **Polite prefixes are one pattern** (`heuristic._POLITE`, "can you please",
    "would you mind"), shared by every rule that needs it; a private copy drifted. A request
    put inside another ("can you tell me …", "would you mind telling me …", "can you check if
    …") is tried again as the question inside (`_inner_question`) by the clock, the calculator
    and system status, and that question is what the tool is handed.
  - **Future-weather locations stay places.** The fast path accepts a short place and day,
    but an indoor object after a determiner is not an outdoor forecast location. A
    possessive location or an article-prefixed household object goes to the router;
    article-prefixed places such as the UK and the beach still use the fast path.
  - **Prose guard**: a direct prefix whose words read as English ("schedule a meeting…",
    "time for a break", "forget the timer") goes to the router instead (`_reads_as_prose`).
  - **Typing into another app** requires quoted text or an explicitly named app. "Type out a
    poem" stays with the assistant; `type "hello" in notepad` sends hello without quote marks.
  - **Stop ≠ delete.** "stop/turn off/dismiss" only touches what is going off or a running
    timer, never a schedule; "cancel/delete" removes, prefers what is ringing, and removing
    more than one asks first (HIGH).
  - **A task is not a process.** "stop the backup task" and "stop the timer task" must not
    become `end process`. Ending a process requires that word or an explicit "force quit".
  - **Two requests in one sentence** split only where every part starts like a request and
    routes on its own (`_split_requests`); "remind me to buy milk and eggs at 6pm" and
    "search for flights and hotels in paris" stay whole, and so does a briefing format: "set my
    briefing to weather and news" split into a briefing of weather and a news read-out, because
    both halves route. In a sentence that is or routes to `briefing set/add/remove`, a part that is only a section
    ("news") belongs to the format; a real second request ("and set a timer for 10 minutes") still
    splits off - refusing every split swallowed it.
  - **A reply to our own question completes it** (`_follow_up`): "set a timer" → "How long
    should the timer run?" → "10 minutes". Keyed to the exact question strings; history
    arrives as `{"role", "text"}` - a test written with "content" passed while the page got
    nothing. Refusals, questions, new requests and filler ("it's", "to") are not answers.
  - **List items said without commas are asked about** (Jeevan, 2026-10-22): "add eggs milk and
    bread" may be three items or "eggs milk" one, and nothing tells it from "almond milk and
    bread", so a multi-word item before the last "and", with no comma anywhere, asks "Add eggs,
    milk and bread to your shopping list as 3 items? Say yes, no to add ... as you said it, or say
    them again with "and" between." A digit or a joining word ("2 apples", "a loaf of bread")
    marks a phrase and is not asked about. "yes" splits and "no" keeps it as said, read with
    punctuation dropped and a "please" or "thanks" allowed ("yes, please" was once added as two
    items - Codex); the items said again with more separators than before are added as said, the
    same words again are asked about again (not taken as a "no"), a new request is not taken as
    items, and a cancel adds nothing. The answer is read ahead of the refusal check ("no") and of
    the eight-word limit on follow-ups (tasks said again run longer). The question is matched on the turn's
    first line, because the page appends the tool data after a line break. Todo phrases are asked
    about too ("call mom pay rent and buy milk"); saying them again with "and" is the answer there.
  - **A computed answer can be asked again with one thing changed** (`_asked_again`): "and in
    feet?" after a conversion, "what about london" after the time, "and halloween?" or "in
    weeks?" after a count of days. Keyed to the answer's own shape, never the user's words,
    so a chain of them works; a connecting word is required, and the new request must parse
    as its own ("what about the weather in london" is not the time in London). It runs
    before the refusal check, which reads "what about meters" as a question. A new amount
    ("what about 10?", "and 12 km?", which turns the conversion round) is rebuilt by
    `units.convert_again` from the answer's own units; after a time between zones, a new
    place or a new time; after a gap between zones, a new place. Also: a new place after a
    forecast (only a place the zone table knows by that whole name - "and humidity?" is about
    the forecast above, which the chat model can read), a new rate or amount after a percentage,
    a new number after a prime check, another holiday after "when is …". And "again" after a
    coin, a die or a number re-runs the user's own draw request: the answer "You rolled a
    **14**." does not say it was a d20.
  - **The last line of defence** (`_unexpected_failure`): whatever a tool raises, the user
    gets a sentence and `failures` gets the traceback - 21 crash classes were found by the
    prefix fuzz before it existed.
  - **A command handed back unchanged is conversation** (`_DECLINED`). The dispatch has
    already declined that exact text, so a router naming the sentence itself as a command
    used to end in "I don't know how to do that yet" - and "convert 100 usd to eur" never
    reached the live rate that "how much is 100 dollars in euros" gets. Since #184 a routed
    command that *differs* and matches nothing ("currency convert 100 usd eur") is answered
    the same way; a real command that runs and fails still reports its failure.
  - **A negated request is never turned into its positive** (`heuristic.is_negated`). Routes
    match anywhere in a sentence, so "do not open youtube" opened it and "do not remind me to
    call mom" set the reminder. A sentence that opens with a negation is not routed by the
    instant router, and whatever command the model returns for it is answered rather than
    run (`_route.decided`) - the model can still say `open url …`. Not negations: "never
    mind the timer" (a cancellation), and "don't forget to…" / "don't let me forget/miss…",
    which ask for a reminder and may become only that (`asks_not_to_forget`): the model routed
    "don't forget to call mom at 5pm" to `open url …mom.com`, and "mom" was in the words.
  - **The LLM router may not invent a shell command** (`_repair_shell_command`). For "change
    my desktop background to blue" it wrote `reg add "HKCU\Control Panel\Colors" ...`. A
    routed `run command` stands only when the words asked to run something, named a shell
    to do it in, or contain the command itself; a question or a "don't run" is answered,
    and anything else gets a fixed reply saying nothing was run. A question is read after
    any courtesy in front of it, and "what does … do" / "how to run …" anywhere: checked
    only at the very start, "please explain how to run npm install" raised an approval card.
  - **Open limit, not accepted behaviour: the LLM router substitutes a nearby tool.**
    Measured 2026-10-05 on 22 requests the app cannot do: 8 became a tool that changes state
    or spends a call - "book me a table for two at 7pm" set a reminder, "set my wallpaper to
    a beach photo" made a hosted picture, "text john" became an email, "get me an uber" a
    reminder. A reminder nobody asked for is still a task persisted without consent. Not
    fixed because every measured fix broke real requests: a keyword gate would lose the 12
    of 18 indirect requests only the LLM understands ("ping me at 5", "whip up a sketch"); a
    prompt rule made the model claim it cannot send email; the few-shot example alone copied
    its own "7pm" into "call mom" (pair log 26e58bc). A second, narrow YES/NO call after the
    LLM picked a state-changing tool ("does `<command>` do what was asked?") caught all 8
    substitutions but refused 2-3 of 15 legitimate reminders ("ping me at 5…"), was no better
    for being told when a reminder counts, found nothing to catch outside reminders, and
    would add ~350 ms to every LLM-routed picture, email and document - not shipped, by
    agreement (pair log e9aa89e, 525ce46). Measure any new idea on both corpora.
    (2026-10-14: the wallpaper no longer reaches the LLM router. `tools/system.py` takes "set my
    wallpaper to …" and asks for an image file when none is named.)
  - **A time on the laptop's clock takes its own day's offset.** `datetime.now().astimezone()`
    carries only today's, so every caller that reads the laptop's clock passes `local=True`
    to `parse_when`/`describe` (`test_every_production_call_passes_local` finds one that
    does not), and roll-forwards count calendar days, not hours. Tests put a named zone in
    `timeparse.LOCAL_ZONE`: Windows cannot change a process's zone and CI runs in UTC. A
    fixed-offset `now` without `local` parses exactly as before.
    The ticker does the same for scheduled jobs (`claim_due_jobs(local=True)`), so a
    01:30 job fires once, not twice, on the night the clocks go back.
  Reminders are **delivered**: `/api/reminders` (polled, with `next_in`) raises a card,
  chime, notification and in voice mode speech; the CLI has a watcher thread. Verify changes
  here with the corpus harness pattern - through `handle()` *and* through the page.
  A listing that names today, tomorrow, a weekday or a date filters one-off reminders by
  their due instant on the laptop's local calendar day and includes enabled repeating jobs
  scheduled for that day; an unqualified listing still shows all active reminders and
  repeating jobs. A creation such as "remind me to pay the bill due friday" must never
  become a list request.
