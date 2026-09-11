from __future__ import annotations

import io
import unittest
import urllib.error

from laptop_agent.failures import FailureLog
from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider


def http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions", status, "boom", {}, io.BytesIO(b"detail")
    )


class FailureLogTests(unittest.TestCase):
    """Two bugs hid behind code that handled an error politely: a permanently 400-ing
    ultra tier reported itself as "busy", and a 503 became "the model returned an empty
    document". Catching something must also record it."""

    def test_a_caught_exception_is_recorded_with_context(self) -> None:
        log = FailureLog()
        try:
            raise ValueError("no route to host")
        except ValueError as exc:
            log.record("llm/answer", exc, model="some-model", attempt=2)
        entry = log.recent()[0]
        self.assertEqual(entry["where"], "llm/answer")
        self.assertEqual(entry["kind"], "ValueError")
        self.assertIn("no route", str(entry["message"]))
        self.assertEqual(entry["context"]["model"], "some-model")
        self.assertIn("ValueError", str(entry["trace_tail"]))

    def test_newest_first(self) -> None:
        log = FailureLog()
        log.record("a", "first")
        log.record("b", "second")
        self.assertEqual([e["where"] for e in log.recent()], ["b", "a"])

    def test_the_ring_is_bounded(self) -> None:
        log = FailureLog(limit=5)
        for index in range(20):
            log.record("spot", "failure %d" % index)
        self.assertEqual(log.summary()["kept"], 5)
        self.assertEqual(log.summary()["distinct"], 1)

    def test_repeats_are_counted_even_after_they_age_out(self) -> None:
        log = FailureLog(limit=3)
        for _ in range(9):
            log.record("llm/http", "503")
        frequent = log.summary()["most_frequent"]
        self.assertEqual(frequent[0]["count"], 9)

    def test_context_is_trimmed_not_stored_whole(self) -> None:
        log = FailureLog()
        log.record("spot", "x", blob="y" * 5000)
        self.assertLess(len(str(log.recent()[0]["context"]["blob"])), 300)

    def test_recording_never_raises(self) -> None:
        class Hostile:
            def __str__(self):
                raise RuntimeError("cannot render")

        log = FailureLog()
        log.record("spot", "fine", bad=Hostile())   # must not propagate
        log.record("spot", Hostile())               # nor here
        self.assertTrue(log.recent())


class TransportRetryTests(unittest.TestCase):
    """Measured: the same request that returned 0 characters in 0.3s succeeded on the
    next attempt. The document tool reported that as "the model returned an empty
    document", which told the user nothing true."""

    def provider(self, responses):
        provider = OpenAICompatiblePlannerProvider(
            "k", "m", base_url="https://example.invalid/v1", transport=lambda payload: "ok"
        )
        self.calls: list[int] = []

        def fake_urlopen(request, timeout=None):
            index = len(self.calls)
            self.calls.append(index)
            outcome = responses[min(index, len(responses) - 1)]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        provider._sleep_calls = []
        return provider, fake_urlopen

    def run_transport(self, responses):
        import time as time_module

        from laptop_agent.planner import openai_compatible as module

        provider, fake_urlopen = self.provider(responses)
        original_open, original_sleep = module.urllib.request.urlopen, time_module.sleep
        module.urllib.request.urlopen = fake_urlopen
        time_module.sleep = lambda seconds: None
        try:
            return provider._http_transport({"model": "m", "messages": []})
        finally:
            module.urllib.request.urlopen = original_open
            time_module.sleep = original_sleep

    def ok_response(self, text="hello"):
        import json

        class Response:
            def read(self_inner):
                return json.dumps(
                    {"choices": [{"message": {"content": text}}]}
                ).encode("utf-8")

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return Response()

    def test_a_503_is_retried_and_then_succeeds(self) -> None:
        out = self.run_transport([http_error(503), self.ok_response("recovered")])
        self.assertEqual(out, "recovered")
        self.assertEqual(len(self.calls), 2)

    def test_a_429_is_retried(self) -> None:
        out = self.run_transport([http_error(429), http_error(429), self.ok_response("third time")])
        self.assertEqual(out, "third time")
        self.assertEqual(len(self.calls), 3)

    def test_a_400_is_not_retried(self) -> None:
        # The request itself is wrong — repeating it only makes the user wait twice.
        # This is the shape of the ultra-tier bug: HTTP 400 on every single attempt.
        with self.assertRaises(urllib.error.HTTPError):
            self.run_transport([http_error(400)])
        self.assertEqual(len(self.calls), 1)

    def test_it_gives_up_rather_than_retrying_forever(self) -> None:
        with self.assertRaises(urllib.error.HTTPError):
            self.run_transport([http_error(503)])
        self.assertEqual(len(self.calls), 3)

    def test_a_timeout_is_retried(self) -> None:
        out = self.run_transport([TimeoutError("slow"), self.ok_response("after timeout")])
        self.assertEqual(out, "after timeout")

    def test_every_attempt_is_recorded(self) -> None:
        from laptop_agent.failures import FAILURES

        FAILURES.clear()
        self.run_transport([http_error(503), self.ok_response()])
        recorded = FAILURES.recent()
        self.assertTrue(recorded)
        self.assertEqual(recorded[0]["where"], "llm/http")
        self.assertEqual(recorded[0]["context"]["status"], 503)


if __name__ == "__main__":
    unittest.main()
