from __future__ import annotations

import unittest

from laptop_agent.model_status import ModelStatus


class ModelStatusTests(unittest.TestCase):
    def test_unknown_until_recorded(self) -> None:
        status = ModelStatus()
        self.assertEqual(status.status("smart"), "unknown")
        self.assertEqual(status.snapshot(), {"tiers": {}, "degraded": False})

    def test_record_and_snapshot(self) -> None:
        status = ModelStatus()
        status.record("fast", True)
        status.record("smart", False)
        self.assertEqual(status.status("fast"), "ok")
        self.assertEqual(status.status("smart"), "degraded")
        snap = status.snapshot()
        self.assertTrue(snap["degraded"])
        self.assertEqual(snap["tiers"], {"fast": "ok", "smart": "degraded"})
        self.assertEqual(status.degraded_tiers(), ["smart"])

    def test_recovery_clears_degraded(self) -> None:
        status = ModelStatus()
        status.record("smart", False)
        self.assertTrue(status.snapshot()["degraded"])
        status.record("smart", True)  # tier recovered
        self.assertFalse(status.snapshot()["degraded"])
        self.assertEqual(status.degraded_tiers(), [])


if __name__ == "__main__":
    unittest.main()
