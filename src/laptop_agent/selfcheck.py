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
    # Reminders — #122. Two of these were answered by a model with no access to the store.
    ("what are my reminders", "reminders", "the phrasing the backlog reported"),
    ("do i have any reminders", "reminders", "no 'my', so it reached no router at all"),
    ("what reminders do i have", "reminders", "same, and it was answered by a guess"),
    ("can you show me my reminders", "reminders", "politeness `strip_address` cannot remove"),
    ("remind me to call mom at 6pm", "reminder add", "a creation must beat a listing"),
    ("remind me to pay the bill due friday", "reminder add",
     "'due' in the sentence must not turn a creation into a listing"),
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
    ("search for best laptops 2026", "web search", "search, said plainly"),
    ("pause the music", "media", "whole-message media control"),
    ("skip this song", "media", "same"),
    ("set the volume to 50", "media volume 50", "a level, not a step"),
    ("play lofi hip hop", "play music", "play only at the start"),
    ("what time is it in tokyo", "time", "a zone"),
    ("what time is it in california", "time", "a state, refused as a zone once"),
    ("could you please tell me what time it is", "time", "both halves of a polite prefix"),
    ("tech news", "news", "a topic first"),
    ("set a timer for five minutes", "timer", "spoken numbers"),
    ("can you please set a timer for five minutes", "timer", "'can you' and 'please' together"),
    ("count down 10 minutes", "timer", "another word for it"),
    ("start a countdown for 90 seconds", "timer", "same"),
    ("how much time is left on my timer", "timers", "time left"),
    ("wake me up at 7", "alarm", "an alarm said as speech"),
    ("snooze for 5 minutes", "reminder snooze", "minutes, never a reminder id"),
    ("what's my next reminder", "reminders next", "read as a date nobody gave, once"),
    ("never mind the timer", "reminder delete", "letting go of one"),
    ("i don't need the alarm anymore", "reminder delete", "same"),
    ("stop reminding me about the oven", "reminder delete", "same"),
    ("add milk to my shopping list", "list", "lists had no home"),
    ("would you mind adding eggs to my shopping list", "list", "a gerund after a polite prefix"),
    ("what's on my calendar today", "calendar", "was a web search; none is connected"),
    ("schedule a meeting with john tomorrow at 3pm", "calendar add", "answered with command syntax once"),
    ("my name is jeevan", "remember", "a fact said without 'remember'"),
    ("change my name to Jeev", "remember", "a fact corrected"),
    ("what do you remember", "memory", "no 'about me'"),
    ("what did i ask you to remember", "memory", "same"),
    ("how much battery do i have", "system status", "this machine"),
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
    ("what is a reminder", "a definition, not a listing"),
    ("the value is in the middle and the key is on the left", "prose, not a placement"),
    ("my keys are on the right", "a lone placement is where ordinary prose lives"),
    ("turn left and then right", "two positions, no window"),
    ("should i put the legend on the right", "a decision belongs to the advisor"),
    ("how does tcp congestion control work", "a plain question"),
    ("update my resume", "'resume' is not a media key"),
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
