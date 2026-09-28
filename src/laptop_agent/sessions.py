"""Signed-in sessions for the web and desktop app, kept on the server.

A session is a random 256-bit token in an HttpOnly cookie, and only its SHA-256 is stored,
so this file holds nothing a reader could present as a cookie. It lives on disk, not in
memory, because the desktop app restarts often and a restart would sign every device out.
It lives on the server, not signed into the cookie, because a session has to be revocable:
signing out, a password change and a disabled account all end it at once.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from laptop_agent.storage import atomic_write_text, file_lock, read_json

IDLE_SECONDS = 7 * 86400
ABSOLUTE_SECONDS = 30 * 86400
# Recording `seen` on every request would cost a disk write per poll; a minute is precise
# enough for a seven-day idle limit.
TOUCH_SECONDS = 60


@dataclass
class Session:
    account_id: str
    created: float
    seen: float
    method: str


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class SessionStore:
    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._stamp: tuple[int, int] | None = None

    def _refresh(self) -> None:
        """Reload when the file changed under us: the CLI revokes sessions from its own
        process, and a revoked session must stop working here at once."""
        try:
            stat = self.path.stat()
            stamp: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            stamp = None
        if stamp is not None and stamp == self._stamp:
            return
        loaded: dict[str, Session] = {}
        for digest, raw in (read_json(self.path, {"sessions": {}}).get("sessions") or {}).items():
            try:
                loaded[str(digest)] = Session(str(raw["account_id"]), float(raw["created"]),
                                              float(raw["seen"]), str(raw.get("method", "")))
            except (KeyError, TypeError, ValueError):
                continue
        self._sessions = loaded
        self._stamp = stamp

    def _alive(self, session: Session, now: float) -> bool:
        return now - session.seen < IDLE_SECONDS and now - session.created < ABSOLUTE_SECONDS

    def _save(self) -> None:
        now = self.clock()
        self._sessions = {digest: session for digest, session in self._sessions.items()
                          if self._alive(session, now)}
        atomic_write_text(self.path, json.dumps(
            {"sessions": {digest: asdict(session) for digest, session in self._sessions.items()}}, indent=2))
        stat = self.path.stat()
        self._stamp = (stat.st_mtime_ns, stat.st_size)

    def create(self, account_id: str, method: str) -> str:
        """A new session for the account; returns the token for the cookie, never stored."""
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._lock, file_lock(self.path):
            self._refresh()
            self._sessions[_digest(token)] = Session(account_id, now, now, method)
            self._save()
        return token

    def resolve(self, token: str) -> Session | None:
        if not token:
            return None
        digest = _digest(token)
        now = self.clock()
        with self._lock, file_lock(self.path):
            self._refresh()
            session = self._sessions.get(digest)
            if session is None:
                return None
            if not self._alive(session, now):
                del self._sessions[digest]
                self._save()
                return None
            if now - session.seen >= TOUCH_SECONDS:
                session.seen = now
                self._save()
            return Session(**asdict(session))

    def revoke(self, token: str) -> None:
        with self._lock, file_lock(self.path):
            self._refresh()
            if self._sessions.pop(_digest(token or ""), None) is not None:
                self._save()

    def revoke_account(self, account_id: str, keep: str | None = None) -> int:
        """End every session of an account, except `keep` (the one changing its password)."""
        kept = _digest(keep) if keep else None
        with self._lock, file_lock(self.path):
            self._refresh()
            doomed = [digest for digest, session in self._sessions.items()
                      if session.account_id == account_id and digest != kept]
            for digest in doomed:
                del self._sessions[digest]
            if doomed:
                self._save()
        return len(doomed)
