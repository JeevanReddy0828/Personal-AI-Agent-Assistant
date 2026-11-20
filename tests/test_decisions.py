from __future__ import annotations

import io
import json
import threading
import time
import unittest
import urllib.request
from typing import Any
from unittest import mock

from laptop_agent import decisions
from laptop_agent.decisions import (
    COOLDOWN_SECONDS,
    DIFFICULTY_QUESTION,
    END,
    TierDecider,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeBackend:
    def __init__(self, response: dict[str, Any] | None = None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[tuple[dict[str, Any], float]] = []

    def __call__(self, body: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.calls.append((body, timeout))
        if self.error is not None:
            raise self.error
        return self.response or {}


def scored(score: Any) -> dict[str, Any]:
    return {"model": "english", "answers": {"difficulty": {"score": score}}}


def heuristic(text: str) -> int:
    return 1


def step_names(decision) -> list[str]:
    return [name for name, _ in decision.steps]


class NoLayaTests(unittest.TestCase):
    def test_without_url_or_backend_the_heuristic_decides(self) -> None:
        decider = TierDecider(heuristic)
        decision = decider.decide("what is a monad")
        self.assertFalse(decider.laya_configured())
        self.assertEqual(decision.level, 1)
        self.assertEqual(decision.source, "heuristic")
        self.assertIsNone(decision.score)
        self.assertEqual(step_names(decision), ["context", "explicit_cue", "heuristic"])
        for _, ms in decision.steps:
            self.assertIsInstance(ms, float)

    def test_status_reports_configuration(self) -> None:
        decider = TierDecider(heuristic, backend=FakeBackend(scored(1.0)), deadline_ms=300, use_context=True)
        self.assertEqual(
            decider.status(),
            {"laya": True, "cooling_down": False, "deadline_ms": 300, "use_context": True},
        )
        self.assertTrue(TierDecider(heuristic, "http://127.0.0.1:9").laya_configured())


class ExplicitCueTests(unittest.TestCase):
    def test_explicit_cue_wins_without_calling_laya(self) -> None:
        backend = FakeBackend(scored(0.1))
        decision = TierDecider(heuristic, backend=backend).decide("Think hard about this: 2+2?")
        self.assertEqual(decision.level, 2)
        self.assertEqual(decision.source, "cue")
        self.assertEqual(backend.calls, [])
        self.assertEqual(step_names(decision), ["context", "explicit_cue"])


class LayaTests(unittest.TestCase):
    def test_score_maps_to_level_by_thresholds(self) -> None:
        for score, level in ((0.4, 0), (1.8, 1), (2.6, 2)):
            with self.subTest(score=score):
                backend = FakeBackend(scored(score))
                decision = TierDecider(heuristic, backend=backend, deadline_ms=250).decide("explain monads")
                self.assertEqual(decision.level, level)
                self.assertEqual(decision.source, "laya")
                self.assertEqual(decision.score, score)
                self.assertEqual(step_names(decision), ["context", "explicit_cue", "laya"])
                body, timeout = backend.calls[0]
                self.assertEqual(
                    body,
                    {
                        "state": {"request": "explain monads"},
                        "questions": {"difficulty": DIFFICULTY_QUESTION},
                        "model": "english",
                    },
                )
                self.assertEqual(timeout, 0.25)

    def test_failure_falls_back_records_once_and_cools_down(self) -> None:
        clock = FakeClock()
        backend = FakeBackend(error=TimeoutError("timed out"))
        decider = TierDecider(heuristic, backend=backend, clock=clock)
        with mock.patch("laptop_agent.decisions.record_failure") as record:
            first = decider.decide("explain monads")
            self.assertEqual((first.level, first.source), (1, "heuristic"))
            self.assertEqual(step_names(first), ["context", "explicit_cue", "laya", "heuristic"])
            record.assert_called_once()
            self.assertEqual(record.call_args.args[0], "decisions/laya")
            self.assertTrue(decider.status()["cooling_down"])

            clock.now += COOLDOWN_SECONDS - 1
            second = decider.decide("explain monads")
            self.assertEqual(second.source, "heuristic")
            self.assertEqual(len(backend.calls), 1)
            self.assertEqual(step_names(second), ["context", "explicit_cue", "heuristic"])
            record.assert_called_once()

            clock.now += 2
            backend.error = None
            backend.response = scored(2.6)
            third = decider.decide("explain monads")
            self.assertEqual(len(backend.calls), 2)
            self.assertEqual((third.level, third.source), (2, "laya"))
            self.assertFalse(decider.status()["cooling_down"])

    def test_malformed_response_falls_back(self) -> None:
        for response in ({"model": "english"}, scored("1.2"), scored(None), {"answers": {}}):
            with self.subTest(response=response):
                with mock.patch("laptop_agent.decisions.record_failure") as record:
                    decision = TierDecider(heuristic, backend=FakeBackend(response)).decide("explain monads")
                self.assertEqual((decision.level, decision.source), (1, "heuristic"))
                self.assertIsNone(decision.score)
                record.assert_called_once()

    def test_late_answer_falls_back(self) -> None:
        clock = FakeClock()

        def slow(body: dict[str, Any], timeout: float) -> dict[str, Any]:
            clock.now += 0.3
            return scored(0.4)

        with mock.patch("laptop_agent.decisions.record_failure") as record:
            decision = TierDecider(heuristic, backend=slow, deadline_ms=250, clock=clock).decide("explain monads")
        self.assertEqual((decision.level, decision.source), (1, "heuristic"))
        record.assert_called_once()

    def test_huge_integer_score_fails_and_cools_down(self) -> None:
        decider = TierDecider(heuristic, backend=FakeBackend(scored(10**400)))
        with mock.patch("laptop_agent.decisions.record_failure") as record:
            decision = decider.decide("explain monads")
        self.assertEqual((decision.level, decision.source), (1, "heuristic"))
        record.assert_called_once()
        self.assertEqual(record.call_args.args[0], "decisions/laya")
        self.assertTrue(decider.status()["cooling_down"])


class WallClockDeadlineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.release = threading.Event()

    def tearDown(self) -> None:
        self.release.set()

    def test_a_blocked_backend_is_abandoned_at_the_deadline(self) -> None:
        def blocked(body: dict[str, Any], timeout: float) -> dict[str, Any]:
            self.release.wait(5)
            return scored(0.4)

        decider = TierDecider(heuristic, backend=blocked, deadline_ms=100)
        with mock.patch("laptop_agent.decisions.record_failure") as record:
            started = time.monotonic()
            decision = decider.decide("explain monads")
            elapsed_ms = (time.monotonic() - started) * 1000
        self.assertLess(elapsed_ms, 100 + 200)
        self.assertEqual((decision.level, decision.source), (1, "heuristic"))
        record.assert_called_once()
        self.assertTrue(decider.status()["cooling_down"])


HISTORY = [
    {"role": "user", "text": "explain how a transformer attends"},
    {"role": "assistant", "text": "It weighs every token..."},
    {"role": "user", "text": "and the decoder?"},
]


class ContextTests(unittest.TestCase):
    def test_follow_up_sent_alone_without_use_context(self) -> None:
        backend = FakeBackend(scored(1.0))
        decision = TierDecider(heuristic, backend=backend).decide("and the decoder?", HISTORY)
        self.assertTrue(decision.follow_up)
        self.assertEqual(backend.calls[0][0]["state"]["request"], "and the decoder?")

    def test_follow_up_carries_previous_request_with_use_context(self) -> None:
        backend = FakeBackend(scored(1.0))
        TierDecider(heuristic, backend=backend, use_context=True).decide("and the decoder?", HISTORY)
        self.assertEqual(
            backend.calls[0][0]["state"]["request"],
            "explain how a transformer attends\nand the decoder?",
        )

    def test_follow_up_flag(self) -> None:
        decider = TierDecider(heuristic)
        self.assertTrue(decider.decide("what about decoders", HISTORY[:2]).follow_up)
        self.assertFalse(decider.decide("what about decoders").follow_up)
        self.assertFalse(decider.decide("what about decoders", []).follow_up)


class HttpBackendTests(unittest.TestCase):
    def _capture(self, api_key: str | None) -> tuple[Any, float, Any]:
        captured: dict[str, Any] = {}

        class FakeOpener:
            def open(self, request: Any, timeout: float) -> io.BytesIO:
                captured["request"] = request
                captured["timeout"] = timeout
                return io.BytesIO(json.dumps(scored(1.8)).encode("utf-8"))

        decider = TierDecider(heuristic, "http://127.0.0.1:8000/", api_key=api_key)
        with mock.patch.object(decisions, "_OPENER", FakeOpener()):
            decision = decider.decide("explain monads")
        return captured["request"], captured["timeout"], decision

    def test_opener_has_no_proxy_handler(self) -> None:
        handlers = [h for h in decisions._OPENER.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(handlers, [])

    def test_posts_json_to_systemone(self) -> None:
        request, timeout, decision = self._capture(None)
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/systemone")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertIsNone(request.get_header("Authorization"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["state"], {"request": "explain monads"})
        self.assertEqual(body["questions"], {"difficulty": DIFFICULTY_QUESTION})
        self.assertEqual(body["model"], "english")
        self.assertEqual(timeout, 0.25)
        self.assertEqual((decision.level, decision.source, decision.score), (1, "laya", 1.8))

    def test_bearer_header_only_with_api_key(self) -> None:
        request, _, _ = self._capture("secret-key")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")


class MermaidTests(unittest.TestCase):
    def test_mermaid_names_every_step_and_end(self) -> None:
        chart = TierDecider.mermaid()
        self.assertTrue(chart.startswith("flowchart TD"))
        for name in ("context", "explicit_cue", "laya", "heuristic", END):
            self.assertIn(name, chart)


class NeverRaisesTests(unittest.TestCase):
    def test_an_unexpected_step_error_falls_back_to_the_heuristic(self) -> None:
        backend = FakeBackend(scored(0.4))
        with mock.patch("laptop_agent.decisions.record_failure") as record:
            decision = TierDecider(lambda text: 2, backend=backend).decide("hello", [None])  # type: ignore[list-item]
        self.assertEqual((decision.level, decision.source), (2, "heuristic"))
        self.assertEqual(backend.calls, [])
        record.assert_called_once()
        self.assertEqual(record.call_args.args[0], "decisions/decide")

    def test_a_failing_heuristic_does_not_escape(self) -> None:
        def broken(text: str) -> int:
            raise RuntimeError("boom")

        with mock.patch("laptop_agent.decisions.record_failure") as record:
            decision = TierDecider(broken).decide("hello")
        self.assertEqual((decision.level, decision.source), (0, "heuristic"))
        self.assertEqual(
            [call.args[0] for call in record.call_args_list],
            ["decisions/decide", "decisions/heuristic"],
        )


if __name__ == "__main__":
    unittest.main()
