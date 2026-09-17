from __future__ import annotations

import threading
import time

# Chat model tiers the orchestrator may use, in fallback order. It escalates
# fast -> smart -> ultra by complexity, then degrades the other way when a tier is
# busy, ending at the cross-provider "openrouter" safety net.
TIERS = ("fast", "smart", "ultra", "openrouter")


# A tier that is merely loaded will answer again shortly. A tier that is misconfigured -
# a retired model id, a wrong key, a model this account cannot call - will not, no matter
# how long it is given, and retrying it just adds a failing round trip to every message.
DEGRADED = "degraded"  # 429/503/timeouts/network: transient, retry soon.
                       # The value keeps its old spelling on purpose: health, the web
                       # status pill and the existing tests all read it, and "broken"
                       # is the new information, not a rename of the old.
BROKEN = "broken"      # 400/401/403/404/410: the request itself is wrong, needs a human
OK = "ok"

DEGRADED_COOLDOWN = 60.0
# Long, not forever: a key can be fixed or a model re-provisioned while the app runs, and
# a tier that is never retried can never be seen to recover.
BROKEN_COOLDOWN = 900.0


class ModelStatus:
    """In-memory record of how each model tier last behaved, and **why**.

    The why is the point. Every one of these produced an empty reply and was recorded
    identically as "degraded": a retired model (HTTP 410), a wrong API key (401), a model
    the account cannot call (404), a rejected parameter (400) and a genuinely overloaded
    endpoint (503). So a permanent misconfiguration was retried every 60s forever and
    reported to the user as "busy" - advice to wait, for something that would never
    recover. ERRORS.md records that costing real time twice: the ultra tier 400ing on
    every request while health called it busy, and chat pointed at models NVIDIA had
    retired, where "a new key can't revive a retired model".

    Reachability is ephemeral (it changes with provider load), so this is not persisted.
    Thread-safe: the web server answers chat turns on worker threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, str] = {}  # tier -> "ok" | "degraded" | "broken"
        self._updated_at: dict[str, float] = {}
        self._detail: dict[str, str] = {}

    def record(self, tier: str, ok: bool, reason: str = "", detail: str = "") -> None:
        """Record an outcome. `reason` is DEGRADED or BROKEN; anything else means busy,
        because an unexplained failure is the transient assumption, not the permanent one -
        guessing "broken" would stop trying a tier that was only having a bad minute."""
        with self._lock:
            if ok:
                self._state[tier] = OK
                self._detail.pop(tier, None)
            else:
                self._state[tier] = BROKEN if reason == BROKEN else DEGRADED
                if detail:
                    self._detail[tier] = detail
            self._updated_at[tier] = time.monotonic()

    def should_attempt(self, tier: str, cooldown_seconds: float | None = None) -> bool:
        """Whether a provider should receive another request now.

        A failed tier is skipped for a cooldown so it does not add a failing network
        round-trip to every message; a broken one waits far longer than a busy one.
        """
        with self._lock:
            state = self._state.get(tier, OK)
            if state == OK:
                return True
            last_failure = self._updated_at.get(tier, 0.0)
        wait = cooldown_seconds
        if wait is None:
            wait = BROKEN_COOLDOWN if state == BROKEN else DEGRADED_COOLDOWN
        return time.monotonic() - last_failure >= max(0.0, wait)

    def reason(self, tier: str) -> str:
        """Why a tier last failed, in words a user can act on. '' when it is fine."""
        with self._lock:
            return self._detail.get(tier, "")

    def broken_tiers(self) -> list[str]:
        """Tiers that need a human, not patience."""
        with self._lock:
            return [tier for tier, state in self._state.items() if state == BROKEN]

    def status(self, tier: str) -> str:
        """'ok', 'degraded', or 'unknown' if the tier hasn't been exercised yet."""
        with self._lock:
            return self._state.get(tier, "unknown")

    def degraded_tiers(self) -> list[str]:
        """Every tier that is not answering, busy or broken alike. Callers that care
        about the difference ask `broken_tiers()` or `reason()`."""
        with self._lock:
            return [tier for tier, state in self._state.items() if state != OK]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            tiers = dict(self._state)
            details = dict(self._detail)
        return {
            "tiers": tiers,
            "degraded": any(state != OK for state in tiers.values()),
            # Named separately because the advice differs: busy means wait, broken means
            # go and change something.
            "broken": [tier for tier, state in tiers.items() if state == BROKEN],
            "reasons": details,
        }
