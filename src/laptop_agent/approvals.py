"""Pending approval requests, so a risky action can be approved from the web app.

The browser had no way to answer the approval gate, so `_guarded_approval` auto-denied
everything above MEDIUM: `download https://example.com/data.csv` came back "Blocked -
that high-risk action requires interactive approval in the CLI or Tkinter interface".
Safe, but it meant downloads, shell commands and sending mail simply did not work in the
app's main interface.

This holds a request while the worker thread waits, hands it to the page, and returns the
user's answer. Deliberately strict:

- nothing is ever auto-approved here; the default outcome is denial
- an answer must name the exact request id, so one approval cannot authorise a different
  action, and an id is single-use
- waiting has a timeout, and a timeout denies
- the registry is in-memory and per-process; restarting the app forgets everything
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field

# Long enough for a person to read the action and decide, short enough that an abandoned
# tab does not pin a worker thread for the life of the process.
DEFAULT_TIMEOUT = 120.0


@dataclass
class PendingApproval:
    id: str
    action: str
    risk: str
    reason: str
    preview: str | None
    created_at: float
    _event: threading.Event = field(default_factory=threading.Event, repr=False)
    _approved: bool = field(default=False, repr=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action,
            "risk": self.risk,
            "reason": self.reason,
            "preview": self.preview,
            "waiting_for": round(time.monotonic() - self.created_at, 1),
        }


class ApprovalBroker:
    """Bridges a blocking approval callback on a worker thread to an HTTP answer."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}
        self._listeners: list[object] = []

    def pending(self) -> list[dict[str, object]]:
        with self._lock:
            return [item.as_dict() for item in self._pending.values()]

    def request(self, action: str, risk: str, reason: str, preview: str | None = None) -> bool:
        """Block until the user answers, or until the timeout denies it."""
        entry = PendingApproval(
            id=secrets.token_urlsafe(12),
            action=action,
            risk=risk,
            reason=reason,
            preview=preview,
            created_at=time.monotonic(),
        )
        with self._lock:
            listeners = list(self._listeners)
            if not listeners:
                # Nobody is connected to answer, so waiting the full timeout would only
                # pin this thread for two minutes before denying anyway. Deny now. This
                # also keeps the test suite fast: without it one risky-action test took
                # the whole timeout and the run went from 18s to 138s.
                return False
            self._pending[entry.id] = entry
        for notify in listeners:
            try:
                notify(entry.as_dict())  # type: ignore[operator]
            except Exception:
                # A dead SSE connection must not stop the approval from being answerable
                # through the polling route.
                pass
        try:
            answered = entry._event.wait(self.timeout)
        finally:
            with self._lock:
                self._pending.pop(entry.id, None)
        # A timeout is a denial: silence is never consent for a risky action.
        return bool(answered and entry._approved)

    def resolve(self, request_id: str, approved: bool) -> bool:
        """Answer one pending request. True if it existed and is now answered."""
        with self._lock:
            entry = self._pending.get(request_id)
            if entry is None or entry._event.is_set():
                return False
            entry._approved = bool(approved)
            entry._event.set()
            return True

    def add_listener(self, notify) -> None:
        with self._lock:
            self._listeners.append(notify)

    def remove_listener(self, notify) -> None:
        with self._lock:
            if notify in self._listeners:
                self._listeners.remove(notify)
