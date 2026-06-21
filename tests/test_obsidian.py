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

    def test_backlinks_and_note_detail(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            vault.save_note("Hub", "See [[Spoke A]] and [[Spoke B|the second]].")
            vault.save_note("Spoke A", "Back to [[Hub]] and over to [[Spoke B#section]].")
            vault.save_note("Spoke B", "Standalone note.")

            back = vault.backlinks("Spoke B")
            self.assertCountEqual(back.data["backlinks"], ["Hub", "Spoke A"])  # alias + heading forms count

            detail = vault.note_detail("Hub")
            self.assertTrue(detail.ok)
            self.assertCountEqual(detail.data["outlinks"], ["Spoke A", "Spoke B"])
            self.assertEqual(detail.data["backlinks"], ["Spoke A"])  # Spoke A links back to Hub

    def test_note_detail_missing_note(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertFalse(ObsidianVault(raw).note_detail("Nope").ok)

    def test_search_weights_title_alias_summary_over_body(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            # Body mentions "fallback" many times but it's not what the note is about.
            vault.save_note("Logging", "fallback fallback fallback fallback noise about logs.", folder="")
            # This note is *about* fallback — signalled by title + summary, body says it once.
            vault.save_note(
                "Tier Fallback",
                "---\naliases: [degraded models]\nsummary: How chat degrades when a model tier is busy.\n---\n"
                "The system picks another tier once.",
                folder="",
            )
            top = vault.search("fallback").data["results"][0]
            self.assertEqual(top["name"], "Tier Fallback")  # title/summary beat raw body frequency

    def test_search_and_resolve_by_alias(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            vault.save_note(
                "Approval Gate",
                "---\naliases: [permission check, safety gate]\nsummary: The risk chokepoint.\n---\nGuards risky actions.",
                folder="",
            )
            # A synonym the title/body never use still finds and opens the note.
            self.assertEqual(vault.search("permission check").data["results"][0]["name"], "Approval Gate")
            self.assertTrue(vault.read_note("safety gate").ok)

    def test_context_for_pulls_linked_neighbours(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            # The query matches Hub by title, but the actual fact lives in a linked note.
            vault.save_note("Routing", "Routing overview. See [[Latency]] for numbers.", folder="")
            vault.save_note("Latency", "The 550B ultra model takes about 45 to 60 seconds.", folder="")
            bundle = vault.context_for("routing")
            self.assertEqual(bundle.data["primary"], "Routing")
            self.assertIn("Latency", bundle.data["notes"])  # neighbour pulled in
            self.assertIn("45 to 60 seconds", bundle.data["context"])  # the fact is now in context

    def test_audit_flags_orphans_broken_links_and_missing_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            vault = ObsidianVault(raw)
            vault.save_note("Hub", "---\nsummary: A hub.\n---\nLinks to [[Leaf]] and [[Ghost]].", folder="")
            vault.save_note("Leaf", "---\nsummary: A leaf.\n---\nBack to [[Hub]].", folder="")
            vault.save_note("Island", "No links, no summary here.", folder="")
            audit = vault.audit().data
            self.assertIn("Island", audit["orphans"])
            self.assertIn("Island", audit["missing_summary"])
            self.assertEqual(audit["broken_links"], [{"note": "Hub", "link": "Ghost"}])


class MetricsTests(unittest.TestCase):
    def test_metrics_shape(self) -> None:
        m = system_metrics()
        self.assertIn("cpu_percent", m)
        self.assertIn("ram_total_mb", m)
        self.assertIsInstance(m["gpus"], list)


if __name__ == "__main__":
    unittest.main()
