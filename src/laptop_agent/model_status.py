from __future__ import annotations

import threading

# Chat model tiers, fastest first. The orchestrator escalates fast -> smart ->
# ultra by question complexity and falls back the other way when a tier is busy.
TIERS = ("fast", "smart", "ultra")


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
