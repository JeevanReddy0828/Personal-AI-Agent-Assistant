from __future__ import annotations

import threading

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

    def record(self, tier: str, ok: bool) -> None:
        with self._lock:
            self._state[tier] = "ok" if ok else "degraded"

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
