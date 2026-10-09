"""Does this actually work, or does it only look like it does?

Every expensive bug found in this codebase shares one shape: a feature that appeared
healthy and was not, with a passing test beside it.

- The barge-in meter rendered nothing for three months. Its test asserted
  `#vmeter.hidden` is false, which is true of an element inside a `display:none` parent.
- The page ETag never saved a byte, because a duplicate `no-store` header meant no
  browser ever stored the page it would revalidate. Its measurement was taken with curl
  passing `If-None-Match` by hand.
- "do i have any reminders" was answered by a chat model that cannot see the reminder
  store, because the tool-word list had no plurals.
- "whatsapp on the left and chrome on the right" never reached the window tool, though
  the tool had always parsed it correctly.

The last two are the same failure: **the user says something and it does not reach the
tool that handles it.** Nothing in the suite asserted that end to end, so this does —
against the real router, in one table that both a test and a command read.

The table is the point. A routing rule is only correct relative to the phrasings people
actually use, and those live here where they can be read, added to and argued with,
rather than scattered through the assertions of thirty test methods.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from laptop_agent.planner.heuristic import HeuristicPlannerProvider, is_plain_question

# (what a person says, the command it must reach, why this one is in the list).
#
# "reaches" means the instant router resolves it without a model. A phrasing that has to
# ask an LLM what it means is not broken, but it is not guaranteed either — and every
# routing bug found so far was a phrasing that silently fell through to one.
ROUTING_CONTRACT: tuple[tuple[str, str, str], ...] = (
    ("record voice upto 20 seconds", "record", "REC-01: was an invented transcribe filename"),
    ("record my voice for 10 seconds", "record", "requested microphone duration"),
    ("record a voice note", "record", "default 20 seconds"),
    ("record audio up to 2 minutes", "record", "maximum duration in minutes"),
    ("record my voice for up to 20 seconds", "record", "missed, so a chat model asked 'May I?' after every 'yes'"),
    ("can you record my voice for 20 seconds?", "record", "politeness and a question mark"),
    ("record my voice for twenty seconds", "record", "Vosk writes the number as a word"),
    ("start recording", "record", "the other verb people use"),
    # Reminders — #122. Two of these were answered by a model with no access to the store.
    ("what are my reminders", "reminders", "the phrasing the backlog reported"),
    ("do i have any reminders", "reminders", "no 'my', so it reached no router at all"),
    ("what reminders do i have", "reminders", "same, and it was answered by a guess"),
    ("what reminders do i have tomorrow", "reminders on tomorrow", "a day must narrow the listing"),
    ("can you show me my reminders", "reminders", "politeness `strip_address` cannot remove"),
    ("remind me to call mom at 6pm", "reminder add", "a creation must beat a listing"),
    ("remind me to pay the bill due friday", "reminder add",
     "'due' in the sentence must not turn a creation into a listing"),
    ("tell me at 3pm to join the call", "reminder add to join the call at 3pm",
     "starts like a question; the chat model answered it and set nothing"),
    ("every monday at 9 remind me to file my timesheet", "reminder add to file my timesheet every monday at 9",
     "the time said first was dropped and the reminder refused"),
    # Windows — #124. The tool always parsed these; the router never sent them.
    ("put whatsapp on the left and chrome on the right", "window", "the verb form"),
    ("whatsapp on the left and chrome on the right", "window", "no verb at all"),
    ("i want whatsapp on the left and chrome on the right", "window", "the reported phrasing"),
    ("notepad on the top left and spotify on the bottom right", "window",
     "compound positions the planner's hand-copied list had lost"),
    ("left side whatsapp right side chrome", "window", "how it arrives by voice"),
    # The rest of the surface, one representative phrasing each.
    ("what time is it", "time", "clock"),
    ("calculate 2+2*3", "calculate", "arithmetic must never reach a language model"),
    ("what is the weather in austin", "weather", "weather"),
    ("news", "news", "headlines"),
    ("draw me a picture of a fox", "image", "image generation"),
    ("what do you remember about me", "memory", "stored profile"),
    ("remember my name is jeevan", "remember", "storing a detail"),
    # Everyday requests — the corpora of 2026-09-26, each once answered by a model that
    # could not act on it, or by the wrong tool.
    ("split $120 between 4 people", "calculate", "arranged windows once"),
    ("what's 15% of 80", "calculate", "percent phrasing"),
    ("is 9991 a prime number", "calculate", "a yes or no a model gets wrong: 97 x 103"),
    ("what's the average of 4, 8 and 15", "calculate", "an average, kept exact"),
    ("round 3.14159 to two decimals", "calculate", "rounding, half up"),
    ("how many tablespoons in a quarter cup", "convert", "an amount said as a fraction"),
    ("how many teaspoons in half a cup", "convert", "same"),
    ("what's 2 to the power of 10", "calculate", "was rewritten to '2 **2 10'"),
    ("what is five plus five", "calculate", "dictated numbers"),
    ("what's 1/4 of 200", "calculate", "a fraction of"),
    ("what's the weather", "weather", "no place: where the user is"),
    ("will it rain tomorrow", "weather", "a question with no weather word"),
    ("do i need an umbrella", "weather", "same"),
    ("should i bring a jacket", "weather", "same"),
    ("what should i wear today", "weather", "was a web search for the sentence"),
    ("temperature today", "weather", "was a web search for the sentence"),
    ("Delhi weather tomorrow", "weather", "the place first"),
    ("how hot will it be in austin on saturday", "weather austin", "the future tense, with a place and a day"),
    ("how cold will it be tomorrow", "weather", "same, no place"),
    ("search for best laptops 2026", "web search", "search, said plainly"),
    ("what's in my downloads folder", "scan files ~/Downloads", "a home folder by name"),
    ("show me my desktop files", "scan files ~/Desktop", "same, with 'files' said"),
    ("translate good morning to spanish", "translate", "translation, said plainly"),
    ("how do you say thank you in japanese", "translate thank you to japanese", "how people ask for one"),
    ("what's the word for library in german", "translate library to german", "same"),
    ("translate this into french: the meeting is at noon", "translate", "the language before a colon"),
    ("how to say i love you in korean", "translate i love you to korean", "how it is typed into a search box"),
    ("say never again in french", "translate never again to french", "again is inside the phrase, not a repeat request"),
    ("how do you say again in french", "translate again to french", "the word itself can be translated"),
    ("say good night in hindi", "translate good night to hindi", "how it is said aloud"),
    ("in spanish, how do you say good luck", "translate good luck to spanish", "the language first"),
    ("what's the french word for apple", "translate apple to french", "same"),
    ("pause the music", "media", "whole-message media control"),
    ("skip this song", "media", "same"),
    ("set the volume to 50", "media volume 50", "a level, not a step"),
    ("turn the volume all the way up", "media volume 100", "an end of the range in words"),
    ("max volume", "media volume 100", "same"),
    ("go back to the last song", "media previous", "the previous track, said in full"),
    ("a bit louder", "media volumeup", "a step said with its size"),
    ("turn it up a little", "media volumeup", "same"),
    ("a little quieter", "media volumedown", "same"),
    ("play lofi hip hop", "play music", "play only at the start"),
    ("what time is it in tokyo", "time", "a zone"),
    ("what time is it in california", "time", "a state, refused as a zone once"),
    ("convert 9am pst to ist", "time", "a time read in another zone, computed"),
    ("what time is it in new york when it's 9am here", "time", "same, said the other way"),
    ("how many hours ahead is tokyo", "time", "the gap between two zones"),
    ("could you please tell me what time it is", "time", "both halves of a polite prefix"),
    ("tech news", "news", "a topic first"),
    ("latest tech news", "news tech", "a describing word first: lost the topic once"),
    ("what's happening in the world today", "news", "names no 'news': was a web search for the sentence"),
    ("what's going on in the world of sports right now", "news sports", "a topic after 'the world of'"),
    ("set a timer for five minutes", "timer", "spoken numbers"),
    ("can you please set a timer for five minutes", "timer", "'can you' and 'please' together"),
    ("count down 10 minutes", "timer", "another word for it"),
    ("start a countdown for 90 seconds", "timer", "same"),
    ("how much time is left on my timer", "timers", "time left"),
    ("wake me up at 7", "alarm", "an alarm said as speech"),
    ("get me up at 6", "alarm", "another way to say it"),
    ("what alarms do i have", "alarms", "a listing the chat model cannot see; only the next alarm was routed"),
    ("do i have any alarms set", "alarms", "same"),
    ("how many items are on my shopping list", "list shopping show", "a count the chat model cannot see"),
    ("snooze for 5 minutes", "reminder snooze", "minutes, never a reminder id"),
    ("what's my next reminder", "reminders next", "read as a date nobody gave, once"),
    ("what are my scheduled jobs", "schedule list", "the router heard 'jobs' and opened the job tracker"),
    ("never mind the timer", "reminder delete", "letting go of one"),
    ("i don't need the alarm anymore", "reminder delete", "same"),
    ("stop reminding me about the oven", "reminder delete", "same"),
    ("add milk to my shopping list", "list", "lists had no home"),
    ("do i need anything from the store", "list shopping show", "was answered from nothing"),
    ("add milk and bread to groceries", "list groceries add", "a list named without the word 'list'"),
    ("take eggs off the shopping", "list shopping remove", "same"),
    ("split 90 dollars three ways", "calculate", "a currency word, and ways with no 'between'"),
    ("how far is it to boston", "distance here to boston", "no start named"),
    ("would you mind adding eggs to my shopping list", "list", "a gerund after a polite prefix"),
    ("what's on my calendar today", "calendar", "was a web search; none is connected"),
    ("is there anything i need to do today", "calendar", "was a web search for the sentence"),
    ("what do i need to do tomorrow", "calendar", "same"),
    ("schedule a meeting with john tomorrow at 3pm", "calendar add", "answered with command syntax once"),
    ("my name is jeevan", "remember", "a fact said without 'remember'"),
    ("change my name to Jeev", "remember", "a fact corrected"),
    ("what do you remember", "memory", "no 'about me'"),
    ("what did i ask you to remember", "memory", "same"),
    ("how much battery do i have", "system status", "this machine"),
    ("can you look at my screen", "read screen", "asking to look, as a whole sentence"),
    ("what do you see on my screen", "read screen", "same"),
    ("look at my screen and tell me if the code is right", "read screen", "a request after the look"),
    ("look at me and tell me if i look tired", "look at webcam", "asking to be looked at, as a whole sentence"),
    ("use my webcam to check my posture", "look at webcam", "a request that opens by naming the camera"),
    ("how long does it take to drive to chicago", "distance here to chicago", "no start named: was a chat answer"),
    ("how long would it take me to drive to dallas from houston", "distance houston to dallas", "start said last"),
    ("hey jarvis, what's my name", "what's my name", "read back, never guessed"),
    ("hey jarvis, flip a coin", "flip a coin", "a model cannot draw at random"),
)
# Not in the table: exact command words like `failures`, `latency` and `capabilities`.
# Those are matched by the orchestrator's own direct-prefix dispatch, which runs BEFORE
# the router, so the router correctly returns nothing for them — asserting otherwise
# tests the wrong layer, which is what the first run of this file did. The contract is
# about phrasings a person would actually use, and those are the ones at risk.

# Phrasings that must NOT be grabbed by a tool. Every one of these is a sentence that a
# real routing rule was, or nearly was, wrong about — they are regression fuel, not
# decoration.
MUST_STAY_CHAT: tuple[tuple[str, str], ...] = (
    ("record a podcast about space", "not a voice capture instruction"),
    ("how do alarms work", "a question about alarms, not a listing"),
    ("what are alarms?", "a definition; \"what\" lists only with my/the or a \"do i have\" (Codex, #255)"),
    ("alarms are annoying", "a remark"),
    ("how many items fit in a suitcase", "not a list of ours"),
    ("what is the record for the 100m", "record is a noun"),
    ("can you record?", "asks whether it can; only a named request starts the microphone"),
    ("do not open youtube", "a leading 'do not' was skipped and YouTube opened"),
    ("do not remind me to call mom", "same, and the reminder was set"),
    ("don't search the web for cats", "same"),
    ("do not run this command: del notes.txt", "same, and it asked to run it"),
    ("what is a reminder", "a definition, not a listing"),
    ("tell me in one sentence how to cook rice", "'in one' is not a time"),
    ("tell me at least three reasons to learn rust", "'at least' is not a time"),
    ("the value is in the middle and the key is on the left", "prose, not a placement"),
    ("my keys are on the right", "a lone placement is where ordinary prose lives"),
    ("what is happening in the world of my dreams", "'the world of' is not always the news"),
    ("what's the biggest country in the world", "a fact about the world, not its news"),
    ("what are the largest files in a typical linux install", "no folder on this machine"),
    ("turn left and then right", "two positions, no window"),
    ("my screen is cracked, what should i do", "'my screen' anywhere took a screenshot for the vision model"),
    ("dim my screen brightness", "same"),
    ("what do you see in this code", "'what do you see' anywhere turned the camera on"),
    ("what do you see happening with ai next year", "same"),
    ("how do i use my webcam with zoom", "a question about the camera, not a request to use it"),
    ("what do you see as the main risk here", "'what … here' anywhere listed the folder"),
    ("i can't read the screen, it's too bright", "'read the screen' inside a complaint"),
    ("how long does it take to learn to drive", "driving is the subject, not a trip"),
    ("get me up to speed on the project", "'get me up' with no time is not an alarm"),
    ("do i need anything from the store to make lasagna", "a recipe question, not the list"),
    ("how far is the moon", "a knowledge question; only 'how far is it to X' starts from here"),
    ("the last song was great", "a remark about a song, not a request"),
    ("max volume on my headphones is too loud", "a remark, not a setting"),
    ("add salt to the soup", "a bare list name only when memory knows it as a list"),
    ("add a comment to the task", "one task, not the task list"),
    ("round trip to boston", "'round' is not always arithmetic"),
    ("convert 10:30 to minutes", "a time, but no zone to read it in"),
    ("how far ahead is the project", "not a zone"),
    ("what's in my calendar", "not a folder on this machine"),
    ("show me my desktop", "may mean the screen; a listing says 'files' or 'folder'"),
    ("how hot will it be if i add more chili", "about the pot, not the sky: the weather form is the whole sentence"),
    ("a bit louder than that sounds odd", "a remark, not a volume step"),
    ("should i put the legend on the right", "a decision belongs to the advisor"),
    ("how does tcp congestion control work", "a plain question"),
    ("update my resume", "'resume' is not a media key"),
    ("translate this into a plan of action", "no language closes it: 'translate' as a figure of speech"),
    ("what's the best way to say sorry in japanese", "asks for advice, not the phrase translated"),
    ("say something in french", "asks to be talked to, not for 'something' translated"),
    ("say that again in english", "asks for a repeat"),
    ("what's the french word for a man who sings", "a short definition to name, not literal text to translate"),
    ("what's the english word for when you're sad", "a feeling to name, not a word to translate"),
    ("what's the spanish word for a man who sells fish", "a description to name, longer than a phrase"),
    ("how to write a good resume", "same"),
    ("improve my resume summary", "same"),
    ("resume where we left off", "same"),
    ("tailor my resume for the google job", "same"),
    ("what does pause mean", "'pause' only as the whole message"),
    ("i need to pause and think about this", "same"),
    ("how do i play chess", "'play' is not always music"),
    ("how do i play guitar better", "same"),
    ("time management tips", "starts with 'time', asks nothing of the clock"),
    ("i want sushi for dinner, any ideas?", "'hi' inside 'sushi' was a greeting once"),
    ("how do i prioritize tasks at work", "'tasks' is not the task dashboard"),
    ("what's my ip", "not a fact anyone told us"),
    ("python list", "not a list that exists"),
    ("what should i wear to the interview", "not about the weather"),
    ("double check my work", "'double' without a number"),
    ("half of my team is remote", "'half of' without a number"),
    ("i don't need the car anymore", "not a timer, alarm or reminder"),
    ("stop reminding me", "names nothing to stop"),
    ("good news, i got the job", "sharing news, not asking for it: got the day's top stories"),
    ("fake news is a problem", "was a headline search for 'is a problem'"),
    ("is there anything i need to know about python", "no day, so not the agenda"),
    ("what do i need to do to learn rust", "same"),
    ("what's the date of the french revolution", "a date question about no date we can compute"),
)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    seconds: float = 0.0

    @property
    def mark(self) -> str:
        return "ok" if self.ok else "FAIL"


def check_routing(router: HeuristicPlannerProvider | None = None) -> list[Check]:
    """Every phrasing in the contract reaches the command it names."""
    router = router or HeuristicPlannerProvider()
    checks: list[Check] = []
    for phrase, expected, why in ROUTING_CONTRACT:
        started = time.perf_counter()
        decision = router.plan(phrase, "", {})
        command = getattr(decision, "command", None) or ""
        elapsed = time.perf_counter() - started
        reached = command.startswith(expected)
        detail = f"-> {command or 'no command'}" if reached else (
            f"expected {expected!r}, got {command or 'no command'!r} ({why})"
        )
        checks.append(Check(f"routes: {phrase}", reached, detail, elapsed))
    return checks


def check_not_grabbed(router: HeuristicPlannerProvider | None = None) -> list[Check]:
    """And the sentences that must be left alone still are.

    A router that routes everything is not a working router, and every widening in this
    file's history was one edit away from grabbing ordinary prose.
    """
    router = router or HeuristicPlannerProvider()
    checks: list[Check] = []
    for phrase, why in MUST_STAY_CHAT:
        decision = router.plan(phrase, "", {})
        command = getattr(decision, "command", None) or ""
        # A plain question skips routing entirely and is answered directly; that is a
        # correct outcome here, not a miss.
        left_alone = not command
        detail = "left for chat" if left_alone else f"grabbed by {command!r} ({why})"
        checks.append(Check(f"not grabbed: {phrase}", left_alone, detail))
    return checks


def check_plain_questions() -> list[Check]:
    """A question about the user's own data must never take the model-only shortcut.

    `is_plain_question` is documented as unable to regress tool routing. It could: the
    word list ended each alternative with `\\b`, so plurals slipped past it and seven
    kinds of question about the user's own data were answered by a model that can see
    none of them.
    """
    owned = ("do i have any reminders", "do i have any drafts", "do i have any documents",
             "do i have any tasks", "do i have any downloads", "do i have any screenshots",
             "do i have any workflows", "do i have any emails")
    knowledge = ("how does tcp congestion control work", "what is a bloom filter",
                 "why do databases use write-ahead logging")
    checks = [
        Check(f"not a plain question: {phrase}", not is_plain_question(phrase),
              "goes to the router" if not is_plain_question(phrase)
              else "answered by a model that cannot see it")
        for phrase in owned
    ]
    checks += [
        Check(f"plain question: {phrase}", is_plain_question(phrase),
              "answered directly" if is_plain_question(phrase) else "pays for a routing call")
        for phrase in knowledge
    ]
    return checks


def run_selfcheck(router: HeuristicPlannerProvider | None = None) -> tuple[list[Check], str]:
    """Every offline check, plus a one-line verdict.

    Offline on purpose: this has to be runnable at any moment, on a train, without
    spending the user's free-tier quota to find out whether their own phrasings work.
    """
    router = router or HeuristicPlannerProvider()
    checks = check_routing(router) + check_not_grabbed(router) + check_plain_questions()
    failed = [check for check in checks if not check.ok]
    if failed:
        verdict = f"{len(failed)} of {len(checks)} checks failed."
    else:
        verdict = f"All {len(checks)} checks pass."
    return checks, verdict
