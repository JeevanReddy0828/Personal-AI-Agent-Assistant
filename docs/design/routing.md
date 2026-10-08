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
    "would you mind"), shared by every rule that needs it; a private copy drifted.
  - **Future-weather locations stay places.** The fast path accepts a short place and day,
    but an indoor object after a determiner is not an outdoor forecast location. A
    possessive location or an article-prefixed household object goes to the router;
    article-prefixed places such as the UK and the beach still use the fast path.
  - **Prose guard**: a direct prefix whose words read as English ("schedule a meeting…",
    "time for a break", "forget the timer") goes to the router instead (`_reads_as_prose`).
  - **Stop ≠ delete.** "stop/turn off/dismiss" only touches what is going off or a running
    timer, never a schedule; "cancel/delete" removes, prefers what is ringing, and removing
    more than one asks first (HIGH).
  - **Two requests in one sentence** split only where every part starts like a request and
    routes on its own (`_split_requests`); "remind me to buy milk and eggs at 6pm" and
    "search for flights and hotels in paris" stay whole.
  - **A reply to our own question completes it** (`_follow_up`): "set a timer" → "How long
    should the timer run?" → "10 minutes". Keyed to the exact question strings; history
    arrives as `{"role", "text"}` - a test written with "content" passed while the page got
    nothing. Refusals, questions, new requests and filler ("it's", "to") are not answers.
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
