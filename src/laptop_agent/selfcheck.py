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
