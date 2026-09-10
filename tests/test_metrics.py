from __future__ import annotations

import unittest

import laptop_agent.metrics as metrics


class MetricsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = metrics._collect_system_metrics
        self._calls = 0

        def fake_collect() -> dict[str, object]:
            self._calls += 1
            return {"cpu_percent": 12.5, "gpus": [{"name": "test"}]}

        metrics._collect_system_metrics = fake_collect
        metrics._cached_metrics = None
        metrics._cached_at = 0.0

    def tearDown(self) -> None:
        metrics._collect_system_metrics = self._orig
        metrics._cached_metrics = None
        metrics._cached_at = 0.0

    def test_second_call_within_ttl_is_cached(self) -> None:
        a = metrics.system_metrics()
        b = metrics.system_metrics()
        self.assertEqual(self._calls, 1)  # collected once, served from cache
        self.assertEqual(a, b)

    def test_force_bypasses_cache(self) -> None:
        metrics.system_metrics()
        metrics.system_metrics(force=True)
        self.assertEqual(self._calls, 2)

    def test_returns_independent_copy(self) -> None:
        first = metrics.system_metrics()
        first["cpu_percent"] = 999
        first["gpus"].append({"name": "mutated"})
        second = metrics.system_metrics()  # still cached
        self.assertEqual(second["cpu_percent"], 12.5)  # caller mutation did not leak
        self.assertEqual(len(second["gpus"]), 1)


if __name__ == "__main__":
    unittest.main()
