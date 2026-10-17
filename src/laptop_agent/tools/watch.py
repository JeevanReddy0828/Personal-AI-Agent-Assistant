"""`watch news <topic>`, `watch health`, `watches`, `unwatch <topic|health>` - told when it changes.

Nothing could say "tell me when there's news about Nvidia" or "warn me if my battery runs low":
headlines and the machine's readings were answered only when asked. A watch is a scheduled job
like any other (`watch check …`, run by the ticker through `handle()`), and what it finds is a
reminder due now, so it arrives the way a reminder does: a card, a chime and, in voice mode,
speech. A topic's headlines are recorded when the watch starts, so only what is new is told.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from laptop_agent.scheduler import SchedulerStore
from laptop_agent.storage import atomic_write_text, file_lock, read_json
from laptop_agent.tools.base import ToolResult

NEWS_EVERY = "every 2 hours"
HEALTH_EVERY = "every 5 minutes"
SEEN_KEPT = 300
QUIET_FOR = timedelta(hours=2)

BATTERY_LOW = 15
CPU_HIGH = 90.0
RAM_HIGH = 92.0
DISK_LOW_GB = 5.0

_CHECK = "watch check "

Headlines = Callable[[str], list[str]]
Readings = Callable[[], dict[str, Any]]
Notify = Callable[[str], None]


def topic_of(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s&.+#-]", " ", text or "").lower().split())[:80]


def _key(title: str) -> str:
    return " ".join(title.lower().split())[:120]


class Watches:
    def __init__(self, path: Path, scheduler: SchedulerStore, notify: Notify, headlines: Headlines,
                 readings: Readings, clock: Callable[[], datetime] | None = None) -> None:
        self._path = path
        self._scheduler = scheduler
        self._notify = notify
        self._headlines = headlines
        self._readings = readings
        self._clock = clock or (lambda: datetime.now().astimezone())

    def _load(self) -> dict[str, Any]:
        data = read_json(self._path, {})
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        atomic_write_text(self._path, json.dumps(data, indent=2))

    def _jobs(self, spec: str | None = None) -> list:
        return [job for job in self._scheduler.list_jobs()
                if job.spec.lower().startswith(_CHECK) and (spec is None or job.spec.lower() == spec)]

    def watch_news(self, topic: str) -> ToolResult:
        topic = topic_of(topic)
        if not topic:
            return ToolResult.failure("What should I watch the news for? Try \"watch news nvidia\".")
        spec = f"{_CHECK}news {topic}"
        if self._jobs(spec):
            return ToolResult.success(f"I'm already watching the news about {topic}.", topic=topic)
        titles = self._headlines(topic)
        with file_lock(self._path):
            data = self._load()
            data.setdefault("news", {})[topic] = {"seen": [_key(t) for t in titles][-SEEN_KEPT:], "seeded": bool(titles)}
            self._save(data)
        job = self._scheduler.add("command", spec, NEWS_EVERY, self._clock())
        return ToolResult.success(
            f"Watching the news about **{topic}**. I'll check {NEWS_EVERY} and tell you when there's a new "
            "headline. Say \"stop watching " + topic + "\" to end it.", topic=topic, job=job.to_dict())

    def check_news(self, topic: str) -> ToolResult:
        topic = topic_of(topic)
        titles = self._headlines(topic)
        if not titles:
            return ToolResult.failure(f"No headlines about {topic} could be read this time.", topic=topic)
        with file_lock(self._path):
            data = self._load()
            state = data.setdefault("news", {}).setdefault(topic, {"seen": [], "seeded": False})
            seen = set(state.get("seen") or [])
            fresh = [title for title in titles if _key(title) not in seen]
            told = fresh if state.get("seeded") else []
            state["seen"] = (list(state.get("seen") or []) + [_key(t) for t in fresh])[-SEEN_KEPT:]
            state["seeded"] = True
            self._save(data)
        if told:
            more = f" (and {len(told) - 1} more)" if len(told) > 1 else ""
            self._notify(f"News about {topic}: {told[0]}{more}")
        return ToolResult.success(f"{len(told)} new headline(s) about {topic}.", topic=topic, new=told)

    def watch_health(self) -> ToolResult:
        spec = f"{_CHECK}health"
        if self._jobs(spec):
            return ToolResult.success("Health alerts are already on.")
        job = self._scheduler.add("command", spec, HEALTH_EVERY, self._clock())
        return ToolResult.success(
            f"Health alerts are on. I'll check {HEALTH_EVERY} and warn you when the battery is at {BATTERY_LOW}% or "
            f"less and not charging, the CPU stays above {CPU_HIGH:.0f}%, memory passes {RAM_HIGH:.0f}%, or the disk "
            f"has under {DISK_LOW_GB:.0f} GB free. Say \"turn off health alerts\" to stop.", job=job.to_dict())

    def check_health(self) -> ToolResult:
        reading = self._readings()
        now = self._clock()
        found: dict[str, str] = {}
        battery, plugged = reading.get("battery_percent"), reading.get("plugged")
        if battery is not None and battery <= BATTERY_LOW and not plugged:
            found["battery"] = f"battery at {battery:.0f}% and not charging"
        ram = reading.get("ram_percent")
        if ram is not None and ram >= RAM_HIGH:
            found["memory"] = f"memory {ram:.0f}% used"
        disk = reading.get("disk_free_gb")
        if disk is not None and disk < DISK_LOW_GB:
            found["disk"] = f"only {disk:.1f} GB of disk free"
        cpu = reading.get("cpu_percent")
        with file_lock(self._path):
            data = self._load()
            health = data.setdefault("health", {})
            # One busy sample is a compile or a page load; two checks running is a problem.
            was_high = bool(health.get("cpu_high"))
            health["cpu_high"] = cpu is not None and cpu >= CPU_HIGH
            if health["cpu_high"] and was_high:
                found["cpu"] = f"CPU at {cpu:.0f}% for over five minutes"
            last = health.setdefault("last", {})
            told = []
            for kind, text in found.items():
                when = last.get(kind)
                if when and now - datetime.fromisoformat(when) < QUIET_FOR:
                    continue
                last[kind] = now.isoformat()
                told.append(text)
            self._save(data)
        if told:
            self._notify("Laptop health: " + "; ".join(told) + ".")
        return ToolResult.success(f"{len(told)} health warning(s).", warnings=told, reading=reading)

    def listing(self) -> ToolResult:
        jobs = self._jobs()
        if not jobs:
            return ToolResult.success("You're not watching anything. Try \"watch the news about nvidia\" or "
                                      "\"turn on health alerts\".", watches=[])
        lines = []
        for job in jobs:
            what = job.spec[len(_CHECK):]
            label = "Health alerts" if what == "health" else f"News about {what[len('news '):]}"
            lines.append(f"- {label} ({job.schedule.describe()})")
        return ToolResult.success("Watching:\n" + "\n".join(lines), watches=[job.to_dict() for job in jobs])

    def unwatch(self, target: str) -> ToolResult:
        target = topic_of(re.sub(r"^(?:the\s+)?(?:news\s+)?(?:about|on|for)\s+", "", topic_of(target)))
        health = target in {"health", "health alerts", "system health", "battery", "laptop health"}
        spec = f"{_CHECK}health" if health else f"{_CHECK}news {target}"
        jobs = self._jobs(spec)
        if not jobs:
            return ToolResult.failure(f"I'm not watching {'health' if health else target}. Say \"watches\" to see "
                                      "what I am watching.")
        for job in jobs:
            self._scheduler.remove(job.id)
        with file_lock(self._path):
            data = self._load()
            if health:
                data.pop("health", None)
            else:
                data.get("news", {}).pop(target, None)
            self._save(data)
        return ToolResult.success("Health alerts are off." if health else f"Stopped watching the news about {target}.")
