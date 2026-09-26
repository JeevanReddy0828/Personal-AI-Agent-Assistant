from __future__ import annotations

import asyncio
import json
import sys
import threading
from datetime import UTC, datetime

from laptop_agent.app import build_orchestrator
from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalDenied


def _due_reminder_lines(reminders, announced: set) -> list[str]:
    """Lines for reminders that have fallen due since the last look, once each."""
    lines = []
    for item in reminders.due():
        key = (item.get("id"), item.get("due_at"))
        if key in announced:
            continue
        announced.add(key)
        lines.append(f"Reminder: {item.get('message')}   "
                     f"(say 'reminder done {item.get('id')}' or 'snooze')")
    return lines


def _seconds_to_next(reminders) -> float:
    upcoming = [item for item in reminders.list() if item not in reminders.due()]
    if not upcoming:
        return 15.0
    try:
        due = datetime.fromisoformat(str(upcoming[0].get("due_at", "")))
    except ValueError:
        return 15.0
    return max(1.0, min(15.0, (due - datetime.now(UTC)).total_seconds() + 0.3))


def _watch_reminders(reminders, stop: threading.Event) -> None:
    """Print reminders as they fall due, from a background thread.

    A reminder set here used to be stored, confirmed and never shown again: nothing read the
    due list unless you asked for it. Printing over the prompt is untidy, but a reminder that
    arrives mid-line is still a reminder; one that never arrives is not.
    """
    announced: set = set()
    wait = 0.0
    while not stop.wait(wait):
        try:
            for line in _due_reminder_lines(reminders, announced):
                print(f"\n{line}\n> ", end="", flush=True)
            wait = _seconds_to_next(reminders)
        except Exception as exc:  # a watcher must never take the session down with it
            record_failure("cli.reminders", exc)
            wait = 15.0


async def repl() -> None:
    orchestrator = build_orchestrator()
    history: list[dict[str, str]] = []  # this terminal session's transcript, for follow-ups
    stop = threading.Event()
    threading.Thread(target=_watch_reminders, args=(orchestrator.context.reminders, stop),
                     daemon=True, name="reminders").start()
    print("Laptop Agent MVP. Type 'help' for commands, 'exit' to quit.")
    try:
        await _converse(orchestrator, history)
    finally:
        stop.set()


async def _converse(orchestrator, history: list[dict[str, str]]) -> None:
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text.lower() in {"exit", "quit"}:
            return
        try:
            result = await orchestrator.handle(text, history=history)
        except ApprovalDenied as exc:
            print(f"Denied: {exc}")
            continue
        # Keep a bounded digest of the tool data with the reply, so "summarize this"
        # after `read file …` has the file text to work from, not just the status line.
        reply = result.message
        if result.data:
            reply += "\n" + json.dumps(_json_safe(result.data), default=str)[:4000]
        history += [{"role": "user", "text": text[:40000]}, {"role": "assistant", "text": reply[:40000]}]
        del history[:-80]
        print(result.message)
        if result.data:
            print(json.dumps(_json_safe(result.data), indent=2, default=str))


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return _json_safe(value.__dict__)
    return value


def main() -> None:
    # Windows terminals default to cp1252, which can't encode characters that appear
    # in tool messages (arrows, curly quotes, em dashes). Print as UTF-8 so the CLI
    # never crashes with a UnicodeEncodeError mid-answer.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    asyncio.run(repl())


if __name__ == "__main__":
    main()
