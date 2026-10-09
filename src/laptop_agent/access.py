"""Who a request acts for, and what a `personal` account may not do.

The web server sets the principal for each signed-in request (`acting_as`), and three places
read it. The approval gate refuses a personal account anything risky enough to need approval
at HIGH or CRITICAL. The orchestrator refuses it the developer forms below with a reason,
and dispatches for it only what is marked everyday. The approval broker shows an account
only the approvals it asked for itself.

A personal account is the assistant, not the machine. It never acts on the laptop itself
(apps, windows, the screen, the camera, files, the shell, the browser, music), never reaches
the owner's mailbox, notes, documents or job search, never sees the app's internals, and
never starts anything that acts on its own. What it shares with the owner until data is
kept per account: reminders, timers, lists and remembered facts.

No principal (the CLI, the Tkinter dashboard, the scheduler's ticker) means the machine's
owner, as it always has: whoever runs those can already read every file this guards.

A principal also carries a check that its session still stands. Revoking a session ended
it but not work it had started: an agent run kept dispatching commands under the principal
it began with. `ensure_signed_in` runs wherever Stop is checked, at the start of every turn
and of every step of one, so that work now stops at its next step as Stop would stop it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

from laptop_agent.accounts import Principal
from laptop_agent.cancellation import OperationCancelled

_current: ContextVar[Principal | None] = ContextVar("principal", default=None)
_still_signed_in: ContextVar[Callable[[], bool] | None] = ContextVar("still_signed_in", default=None)


class SignedOut(OperationCancelled):
    """The session this work runs under ended after the work began: a password changed
    elsewhere, or the account was disabled or deleted. A cancellation, so a run stops
    exactly as Stop stops it and no tool's error fallback starts more work."""


@contextmanager
def acting_as(principal: Principal | None, still_signed_in: Callable[[], bool] | None = None) -> Iterator[None]:
    token = _current.set(principal)
    check = _still_signed_in.set(still_signed_in if principal is not None else None)
    try:
        yield
    finally:
        _still_signed_in.reset(check)
        _current.reset(token)


def current() -> Principal | None:
    return _current.get()


def ensure_signed_in() -> None:
    """Raise SignedOut when the session the current work runs under has ended. Nothing to
    check without a principal or without a check, as for the CLI and the ticker."""
    check = _still_signed_in.get()
    if check is not None and not check():
        raise SignedOut("Your session ended, so this stopped.")


def is_personal() -> bool:
    principal = _current.get()
    return principal is not None and principal.role != "dev"


def sees_approval(viewer: Principal | None, owner: Principal | None) -> bool:
    """Whether `viewer` may see and answer an approval that `owner` asked for. An account
    answers only its own; one the machine asked for itself (a scheduled job, nobody signed
    in) goes to a developer. Without accounts both are None, and everyone sees everything."""
    if owner is not None:
        return viewer is not None and viewer.account_id == owner.account_id
    return viewer is None or viewer.role == "dev"


# Exactly the forms the orchestrator's dispatchers match, whole or as a prefix, so a sentence
# that merely starts with one of these words ("knowledge is power") is left alone. These
# are refused with a reason, before anything else is looked at. tests/test_access.py holds
# both lists to the dispatchers: every entry is a form they match, and every form they
# match is in exactly one of the two.
_DEV_EXACT = frozenset({
    # the app's internals
    "audit", "show audit", "briefing", "daily briefing", "status briefing", "morning briefing",
    "timecard", "failures", "errors", "what broke", "recent errors", "selfcheck", "self check",
    "self-check", "does everything work", "check yourself", "diagnose", "latency", "traces",
    "why slow", "speed",
    # acting on its own
    "autopilot status", "autonomous status", "schedule", "schedule list", "schedules",
    "show schedule", "schedule run due", "run due schedules", "schedule tick", "agent runs",
    "agent history", "autonomous runs", "agent last", "agent status", "last agent run", "agents",
    "agent control", "control room", "agent dashboard", "tasks", "task dashboard", "show tasks",
    "workflow status", "workflows", "workflow dashboard", "workflow retry failed",
    "retry failed workflow", "multi retry failed", "retry failed tasks", "retry failed subtasks",
    # the owner's job search, notes and indexed documents
    "jobs", "job list", "list jobs", "job tracker", "jobright", "jobright pull", "pull jobs",
    "pull jobright", "jobright sync", "notes", "vault", "notes status", "vault status", "obsidian",
    "obsidian status", "notes list", "list notes", "notes audit", "vault audit", "audit notes",
    "audit vault", "knowledge list", "knowledge", "knowledge stats", "knowledge status",
    "knowledge reindex", "reindex knowledge", "knowledge backfill", "knowledge prune",
    "prune knowledge", "knowledge clear", "forget knowledge",
    # the owner's mailbox: there is one connection, and it is not the personal account's
    "email digest", "summarize inbox", "summarize my inbox", "summarize my emails",
    "summarize my unread", "inbox digest", "email oauth", "email oauth status", "email tokens",
    "email token status", "email tokens status",
    # the laptop itself
    "read screen", "screen text", "what is on my screen", "what's on my screen", "look at my screen",
    "look at webcam", "describe webcam", "what do you see", "look at me", "webcam", "capture webcam",
    "webcam capture", "take a photo", "windows", "list windows", "show windows",
    "what windows are open", "window", "split", "snap", "arrange", "screenshot", "take a screenshot",
    "take screenshot", "screen shot", "record",
})
_DEV_PREFIX = (
    # the app's internals, and acting on its own
    "timecard", "autopilot", "schedule", "agent", "workflow", "multi", "plan apply job",
    # the owner's job search, notes and indexed documents
    "tailor job", "resume file", "job add", "job stage", "job remove", "job delete", "remember note",
    "notes search", "note search", "ask vault", "ask notes", "read note", "save note",
    "knowledge export", "knowledge forget", "knowledge search", "ask knowledge",
    "answer from knowledge", "recall", "save research report",
    # the owner's mailbox
    "email", "send email",
    # the laptop itself: files, the screen, the camera, the browser, apps, the shell, sound
    "scan files", "read file", "ask file", "summarize file", "extract text", "index file", "file info",
    "extract tables", "analyze spreadsheet", "analyse spreadsheet", "inspect file", "process file",
    "ocr", "transcribe", "convert file", "organize folder", "search files", "read screen",
    "look at screen", "describe image", "look at image", "look at webcam", "describe webcam",
    "open url", "download", "inspect page", "inspect forms", "preview form fill", "fill form",
    "window", "windows", "split", "snap", "arrange", "open app", "screenshot", "run command",
    "terminal", "shell", "play music", "media",
)


# What a personal account may have dispatched, exactly as the dispatchers match it: whole, or
# by a prefix written with its trailing space. Nothing else is dispatched for it. That is
# Codex's review of #140: default-deny where a command is claimed, so a command added later,
# or one no list names, is refused when it runs, not only in CI. The everyday branches chosen
# by a pattern (a coin, a date question, a list edit) are checked beside the dispatch table,
# in `AgentOrchestrator._everyday`.
EVERYDAY_EXACT = frozenset({
    "help", "/help", "memory", "show memory",
    "reminders", "reminders list", "show reminders", "reminders due", "due reminders", "show due reminders",
    "reminders next", "reminders next alarm", "reminders next timer", "reminder done", "reminder stop",
    "reminder snooze", "timers", "timer", "alarms",
    "time", "date", "clock", "what time is it", "what is the time", "current time", "today", "what day is it",
    "datetime", "capabilities", "what can you do",
    "news", "weather", "forecast", "weather here", "local weather", "weather forecast",
    "where am i", "where am i?", "my location", "locate me", "what's my location",
    "lists", "my lists", "show lists", "show my lists", "calendar", "agenda", "my calendar", "my agenda",
    "system status", "status", "battery", "disk space", "computer status",
})
EVERYDAY_PREFIX = (
    "remember ", "forget ", "reminder add ", "remind me ", "reminder done ", "reminder stop ",
    "reminder snooze ", "reminders on ", "timer ", "timers ", "alarm ", "solve ", "advise me on ", "advise ", "strategize ",
    "strategise ", "research report ", "research ", "time ", "date ", "clock ", "calculate ", "calc ",
    "compute ", "news ", "document ", "image ", "weather ", "translate ", "distance ", "trip ", "around ", "map ",
    "hotels near ", "hotels in ", "nearby ", "summarize youtube ", "youtube summary ", "web search ",
    "search web ", "list ", "calendar add ", "convert ",
)


def everyday_form(lowered: str) -> bool:
    """Whether `lowered` (the dispatchers' own `command.lower()`) is a form marked everyday."""
    return lowered in EVERYDAY_EXACT or lowered.startswith(EVERYDAY_PREFIX)


def refused_command(command: str) -> str | None:
    """For a personal account, the developer-only form `command` starts with; else None."""
    if not is_personal():
        return None
    lowered = " ".join((command or "").lower().split())
    if lowered in _DEV_EXACT:
        return lowered
    return next((form for form in _DEV_PREFIX if lowered.startswith(form + " ")), None)
