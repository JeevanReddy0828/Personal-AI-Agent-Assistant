"""Which chat tier answers a request, decided by a fixed pipeline.

An explicit cue in the request decides first. Otherwise a local Laya server scores the
request's difficulty, and the keyword heuristic answers whenever Laya is not configured,
fails, answers late, or is cooling down after a failure.

The pipeline has LangGraph's shape without the dependency: each step takes the state and
returns updates merged into it, and `EDGES` names the next step or a function choosing
among `BRANCHES[step]`.
"""

from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from laptop_agent.failures import record_failure

DIFFICULTY_QUESTION = {
    "type": "score",
    "instructions": "How hard is `request` for a language model?",
    "criteria": [
        "trivial: a lookup or one-liner",
        "easy: short answer, no reasoning",
        "moderate: several steps",
        "hard: long multi-step reasoning or specialist knowledge",
    ],
}
THRESHOLDS = (1.55, 2.05)
EXPLICIT_CUES = ("use the big model", "ultra model", "think hard", "think carefully", "in depth",
                 "in-depth", "step by step", "step-by-step", "show your work", "show your reasoning",
                 "deep dive", "thorough", "comprehensive", "rigorous")
DEFAULT_DEADLINE_MS = 250
COOLDOWN_SECONDS = 60.0
END = "end"

# (request body, timeout in seconds) -> parsed response JSON; raises on any failure.
DecisionBackend = Callable[[dict[str, Any], float], dict[str, Any]]

State = dict[str, Any]

# Laya runs on this machine, and the default opener's system-proxy lookup (on Windows a
# registry read plus DNS work) is unbounded, so the proxy is bypassed entirely.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class TierDecision:
    level: int
    source: str
    score: float | None = None
    follow_up: bool = False
    steps: list[tuple[str, float]] = field(default_factory=list)


def _after_cue(state: State) -> str:
    if "level" in state:
        return END
    return "laya" if state["laya_ready"] else "heuristic"


def _after_laya(state: State) -> str:
    return END if "level" in state else "heuristic"


EDGES: dict[str, str | Callable[[State], str]] = {
    "context": "explicit_cue",
    "explicit_cue": _after_cue,
    "laya": _after_laya,
    "heuristic": END,
}
BRANCHES: dict[str, tuple[str, ...]] = {
    "explicit_cue": (END, "laya", "heuristic"),
    "laya": (END, "heuristic"),
}


def _level_for(score: float) -> int:
    if score < THRESHOLDS[0]:
        return 0
    if score <= THRESHOLDS[1]:
        return 1
    return 2


class TierDecider:
    def __init__(
        self,
        heuristic: Callable[[str], int],
        url: str | None = None,
        *,
        backend: DecisionBackend | None = None,
        api_key: str | None = None,
        deadline_ms: int = DEFAULT_DEADLINE_MS,
        use_context: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._heuristic = heuristic
        self._url = (url or "").strip()
        self._api_key = (api_key or "").strip()
        self._backend = backend or (self._http_backend if self._url else None)
        self.deadline_ms = deadline_ms
        self.use_context = use_context
        self._clock = clock
        self._cooldown_until: float | None = None
        self._steps: dict[str, Callable[[State], State]] = {
            "context": self._context,
            "explicit_cue": self._explicit_cue,
            "laya": self._laya,
            "heuristic": self._heuristic_step,
        }

    def laya_configured(self) -> bool:
        return self._backend is not None

    def _cooling_down(self) -> bool:
        return self._cooldown_until is not None and self._clock() < self._cooldown_until

    def status(self) -> dict[str, Any]:
        return {
            "laya": self.laya_configured(),
            "cooling_down": self._cooling_down(),
            "deadline_ms": self.deadline_ms,
            "use_context": self.use_context,
        }

    def decide(self, text: str, history: list[dict[str, str]] | None = None) -> TierDecision:
        state: State = {
            "text": text,
            "history": history or [],
            "laya_ready": self.laya_configured() and not self._cooling_down(),
        }
        steps: list[tuple[str, float]] = []
        try:
            name = "context"
            while name != END:
                started = self._clock()
                state.update(self._steps[name](state))
                steps.append((name, (self._clock() - started) * 1000.0))
                edge = EDGES[name]
                name = edge if isinstance(edge, str) else edge(state)
        except Exception as exc:
            record_failure("decisions/decide", exc)
            try:
                level = self._heuristic(text)
            except Exception as heuristic_exc:
                record_failure("decisions/heuristic", heuristic_exc)
                level = 0
            return TierDecision(level=level, source="heuristic", steps=steps)
        return TierDecision(
            level=state["level"],
            source=state["source"],
            score=state.get("score"),
            follow_up=state.get("follow_up", False),
            steps=steps,
        )

    @staticmethod
    def mermaid() -> str:
        # Mermaid reserves a bare `end` node, so the terminal is drawn as LangGraph draws it.
        def node(name: str) -> str:
            return "__end__" if name == END else name

        lines = ["flowchart TD"]
        for name, edge in EDGES.items():
            if isinstance(edge, str):
                lines.append(f"  {node(name)} --> {node(edge)}")
            else:
                lines.extend(f"  {node(name)} -.-> {node(target)}" for target in BRANCHES[name])
        return "\n".join(lines)

    def _context(self, state: State) -> State:
        text = state["text"]
        previous = None
        for turn in reversed(state["history"]):
            said = (turn.get("text") or "").strip()
            if turn.get("role") == "user" and said and said != text.strip():
                previous = said
                break
        return {
            "previous_request": previous,
            "follow_up": previous is not None and len(text.split()) <= 6,
        }

    def _explicit_cue(self, state: State) -> State:
        low = state["text"].lower()
        if any(cue in low for cue in EXPLICIT_CUES):
            return {"level": 2, "source": "cue"}
        return {}

    def _laya(self, state: State) -> State:
        assert self._backend is not None
        request = state["text"]
        if self.use_context and state["follow_up"]:
            request = f"{state['previous_request']}\n{request}"
        body = {
            "state": {"request": request},
            "questions": {"difficulty": DIFFICULTY_QUESTION},
            "model": "english",
        }
        backend = self._backend
        timeout = self.deadline_ms / 1000
        outcome: dict[str, Any] = {}

        def call() -> None:
            try:
                outcome["response"] = backend(body, timeout)
            except BaseException as exc:
                outcome["error"] = exc

        # urllib's timeout bounds each socket operation, not the whole call, so the deadline
        # is enforced here; a call still running past it is abandoned and its answer dropped.
        started = self._clock()
        worker = threading.Thread(target=call, name="laya-decision", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            return self._laya_failed(f"no answer within the {self.deadline_ms} ms deadline")
        if "error" in outcome:
            return self._laya_failed(outcome["error"])
        elapsed_ms = (self._clock() - started) * 1000.0
        if elapsed_ms > self.deadline_ms:
            return self._laya_failed(f"answered in {elapsed_ms:.0f} ms, over the {self.deadline_ms} ms deadline")
        try:
            score = outcome["response"]["answers"]["difficulty"]["score"]
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise TypeError(f"non-numeric difficulty score {score!r}")
            value = float(score)
            if not math.isfinite(value):
                raise ValueError(f"non-finite difficulty score {score!r}")
        except Exception as exc:
            return self._laya_failed(exc)
        self._cooldown_until = None
        return {"level": _level_for(value), "source": "laya", "score": value}

    def _laya_failed(self, error: BaseException | str) -> State:
        record_failure("decisions/laya", error)
        self._cooldown_until = self._clock() + COOLDOWN_SECONDS
        return {}

    def _heuristic_step(self, state: State) -> State:
        return {"level": self._heuristic(state["text"]), "source": "heuristic"}

    def _http_backend(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(
            self._url.rstrip("/") + "/v1/systemone",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with _OPENER.open(request, timeout=timeout) as response:
            return json.load(response)
