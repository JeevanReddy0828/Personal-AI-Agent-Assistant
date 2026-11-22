from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tier_outcome_labels", ROOT / "docs/evals/tier_outcome_labels.py")
labels = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(labels)
_score_spec = importlib.util.spec_from_file_location("tier_outcome_score", ROOT / "docs/evals/tier_outcome_score.py")
scorer = importlib.util.module_from_spec(_score_spec)
_score_spec.loader.exec_module(scorer)


class OutcomeLabelTests(unittest.TestCase):
    """The label is the smallest model whose answer the judge accepted."""

    def test_the_smallest_sufficient_model_wins(self) -> None:
        self.assertEqual(labels.outcome_label(3, 3, False), "fast")
        self.assertEqual(labels.outcome_label(2, 3, False), "fast")
        self.assertEqual(labels.outcome_label(1, 3, True), "smart")
        self.assertEqual(labels.outcome_label(0, 3, False), "ultra")

    def test_one_lucky_fast_answer_is_not_enough(self) -> None:
        self.assertEqual(labels.outcome_label(1, 3, True), "smart")
        self.assertEqual(labels.outcome_label(1, 2, True), "smart")

    def test_the_judge_sees_every_answer_and_names_each_key(self) -> None:
        prompt = labels.judge_prompt("q", ["a", "b", "c", "d"])
        for n in range(1, 5):
            self.assertIn(f"<answer_{n}>", prompt)
            self.assertIn(f'"answer_{n}": "sufficient" or "insufficient"', prompt)

    def test_a_verdict_is_read_from_the_judges_reply(self) -> None:
        reply = 'Here you go: {"answer_1": "Insufficient", "answer_2": "sufficient", "answer_3": "sufficient", "reason": "1 has an error"}'
        self.assertEqual(labels.parse_verdict(reply, 3), ([False, True, True], "1 has an error"))

    def test_an_unclear_verdict_is_refused_not_guessed(self) -> None:
        for reply in (
            "",
            "both fine",
            '{"answer_1": "sufficient", "answer_2": "sufficient"}',
            '{"answer_1": "yes", "answer_2": "no", "answer_3": "no"}',
        ):
            with self.assertRaises(ValueError):
                labels.parse_verdict(reply, 3)

    def test_a_rate_limit_is_retried_after_a_wait(self) -> None:
        waits: list[float] = []
        call = labels.Caller("http://127.0.0.1:9", "k", sleep=waits.append)
        attempts: list[int] = []

        def once(*args: object, **kwargs: object) -> tuple[str, float]:
            attempts.append(1)
            if len(attempts) < 3:
                raise labels.urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)
            return "ok", 0.1

        call._once = once
        self.assertEqual(call("m", [], max_tokens=1, temperature=0.0, think=False), ("ok", 0.1))
        self.assertEqual(waits, [labels.RETRY_WAIT_SECONDS] * 2)

    def test_other_http_errors_are_not_retried(self) -> None:
        call = labels.Caller("http://127.0.0.1:9", "k", sleep=lambda _: None)
        attempts: list[int] = []

        def once(*args: object, **kwargs: object) -> tuple[str, float]:
            attempts.append(1)
            raise labels.urllib.error.HTTPError("u", 500, "boom", None, None)

        call._once = once
        with self.assertRaises(labels.urllib.error.HTTPError):
            call("m", [], max_tokens=1, temperature=0.0, think=False)
        self.assertEqual(len(attempts), 1)

    def test_calls_are_paced_twelve_seconds_apart(self) -> None:
        waits: list[float] = []
        call = labels.Caller("http://127.0.0.1:9", "k", sleep=waits.append)
        call._last = labels.time.monotonic()
        with self.assertRaises(OSError):
            call("m", [], max_tokens=1, temperature=0.0, think=False)
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 11.0)


class RecordedLabelsTests(unittest.TestCase):
    """The committed labels cover both request sets and nothing else, and hold no answer text."""

    def test_every_request_has_one_valid_label(self) -> None:
        recorded = json.loads((ROOT / "docs/evals/tier_outcome_labels.json").read_text(encoding="utf-8"))
        ids = [item["id"] for item in recorded["items"]]
        wanted = [item["id"] for item in labels.load_requests()]
        self.assertEqual(sorted(ids), sorted(wanted))
        for item in recorded["items"]:
            self.assertEqual(set(item), {"id", "label", "fast_passes", "smart_ok"})
            self.assertEqual(
                item["label"], labels.outcome_label(item["fast_passes"], recorded["fast_samples"], item["smart_ok"])
            )

    def test_the_score_counts_hard_requests_sent_up_and_easy_ones_wasted(self) -> None:
        truth = {"a": "fast", "b": "fast", "c": "smart", "d": "ultra"}
        guess = {"a": "fast", "b": "ultra", "c": "fast", "d": "smart"}
        result = scorer.score(truth, guess)
        self.assertEqual(result["accuracy"], 0.25)
        self.assertEqual(result["hard_sent_up"], "1/2")
        self.assertEqual(result["easy_sent_up"], "1/2")
        self.assertEqual(result["confusion"]["fast"], [1, 0, 1])


if __name__ == "__main__":
    unittest.main()
