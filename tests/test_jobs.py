from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.jobs import JobTracker, normalize_stage


class JobTrackerTests(unittest.TestCase):
    def _tracker(self, root: str) -> JobTracker:
        return JobTracker(Path(root) / "jobs.json")

    def test_add_normalizes_stage_and_assigns_ids(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jt = self._tracker(raw)
            a = jt.add("Stripe", role="SWE", stage="onsite")  # alias -> interview
            b = jt.add("Datadog")
            self.assertEqual(a["id"], 1)
            self.assertEqual(a["stage"], "interview")
            self.assertEqual(b["id"], 2)
            self.assertEqual(b["stage"], "applied")

    def test_add_requires_company(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                self._tracker(raw).add("   ")

    def test_update_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jt = self._tracker(raw)
            job = jt.add("Notion")
            self.assertEqual(jt.update(job["id"], stage="offer")["stage"], "offer")
            self.assertIsNone(jt.update(999, stage="offer"))
            self.assertTrue(jt.remove(job["id"]))
            self.assertFalse(jt.remove(job["id"]))

    def test_stats_funnel_and_response_rate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            jt = self._tracker(raw)
            jt.add("A", stage="applied")
            jt.add("B", stage="screen")
            jt.add("C", stage="interview")
            jt.add("D", stage="offer")
            stats = jt.stats()
            self.assertEqual(stats["total"], 4)
            self.assertEqual(stats["interviews"], 2)  # interview + offer
            self.assertEqual(stats["offers"], 1)
            self.assertEqual(stats["response_rate"], round(3 / 4, 3))  # 3 advanced past applied
            funnel = {row["stage"]: row["count"] for row in stats["funnel"]}
            self.assertEqual(funnel["applied"], 1)
            self.assertEqual(funnel["offer"], 1)
            self.assertTrue(stats["by_week"])  # one or more weeks recorded

    def test_persists_across_reload(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self._tracker(raw).add("Figma", stage="screen")
            reloaded = self._tracker(raw)
            self.assertEqual(len(reloaded.list()), 1)
            self.assertEqual(reloaded.add("Linear")["id"], 2)  # next_id survived

    def test_stage_aliases(self) -> None:
        self.assertEqual(normalize_stage("Phone Screen"), "applied")  # unknown phrase -> default
        self.assertEqual(normalize_stage("onsite"), "interview")
        self.assertEqual(normalize_stage("declined"), "rejected")


if __name__ == "__main__":
    unittest.main()
