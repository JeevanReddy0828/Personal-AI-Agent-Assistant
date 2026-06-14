from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.metrics import system_metrics
from laptop_agent.tools.obsidian import ObsidianVault


class ObsidianTests(unittest.TestCase):
    def test_unconfigured_reports_clearly(self) -> None:
        vault = ObsidianVault(None)
        self.assertFalse(vault.available())
        self.assertFalse(vault.status().ok)

    def test_save_search_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            saved = vault.save_note("Project Orion", "Budget is 60k. Risk: vendor lock-in.")
            self.assertTrue(saved.ok)
            self.assertTrue(vault.status().ok)
            hits = vault.search("vendor lock")
            self.assertTrue(hits.data["results"])
            self.assertEqual(hits.data["results"][0]["name"], "Project Orion")
            read = vault.read_note("Project Orion")
            self.assertIn("vendor lock-in", read.data["text"])

    def test_append_memory_accumulates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            vault.append_memory("name: Ada")
            vault.append_memory("favorite language: Python")
            note = Path(raw) / "Agent Memory" / "Memory log.md"
            body = note.read_text(encoding="utf-8")
            self.assertIn("name: Ada", body)
            self.assertIn("favorite language: Python", body)

    def test_missing_folder_fails(self) -> None:
        vault = ObsidianVault("Z:/no/such/vault")
        self.assertFalse(vault.status().ok)
        self.assertFalse(vault.search("x").ok)


class MetricsTests(unittest.TestCase):
    def test_metrics_shape(self) -> None:
        m = system_metrics()
        self.assertIn("cpu_percent", m)
        self.assertIn("ram_total_mb", m)
        self.assertIsInstance(m["gpus"], list)


if __name__ == "__main__":
    unittest.main()
