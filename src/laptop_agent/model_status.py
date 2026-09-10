from __future__ import annotations

import threading
import time

# Chat model tiers the orchestrator may use, in fallback order. It escalates
# fast -> smart -> ultra by complexity, then degrades the other way when a tier is
# busy, ending at the cross-provider "openrouter" safety net.
TIERS = ("fast", "smart", "ultra", "openrouter")


class ModelStatus:
    """In-memory record of how each model tier last behaved, so the app can fall
    back gracefully and surface a "the advanced model is busy" state in health.

    Reachability is ephemeral (it changes with provider load), so this is not
    persisted. Thread-safe: the web server answers chat turns on worker threads.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, str] = {}  # tier -> "ok" | "degraded"
        self._updated_at: dict[str, float] = {}

    def record(self, tier: str, ok: bool) -> None:
        with self._lock:
            self._state[tier] = "ok" if ok else "degraded"
            self._updated_at[tier] = time.monotonic()

    def should_attempt(self, tier: str, cooldown_seconds: float = 60.0) -> bool:
        """Whether a provider should receive another request now.

        A known-failed tier is skipped for a short cooldown so a retired model or
        bad route does not add a failing network round-trip to every message. A
        later retry still lets a provider recover without restarting the app.
        """
        with self._lock:
            if self._state.get(tier) != "degraded":
                return True
            last_failure = self._updated_at.get(tier, 0.0)
        return time.monotonic() - last_failure >= max(0.0, cooldown_seconds)

    def status(self, tier: str) -> str:
        """'ok', 'degraded', or 'unknown' if the tier hasn't been exercised yet."""
        with self._lock:
            return self._state.get(tier, "unknown")

    def degraded_tiers(self) -> list[str]:
        with self._lock:
            return [tier for tier, state in self._state.items() if state == "degraded"]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            tiers = dict(self._state)
        return {"tiers": tiers, "degraded": any(state == "degraded" for state in tiers.values())}
