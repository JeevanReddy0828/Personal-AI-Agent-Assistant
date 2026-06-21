from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

import laptop_agent.webui as webui
from laptop_agent.tools.obsidian import ObsidianVault


class NotesApiTests(unittest.TestCase):
    """Exercise /api/notes (read with backlinks, and search) against a live server,
    with the vault pointed at a throwaway folder."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        vault = ObsidianVault(cls._tmp.name)
        vault.save_note("Hub", "Links to [[Leaf]].", folder="")
        vault.save_note("Leaf", "A leaf note about passkeys.", folder="")
        # AgentContext is frozen; swap the vault in without rebuilding the app.
        object.__setattr__(webui._orchestrator.context, "obsidian", vault)
        cls.server = ThreadingHTTPServer((webui.HOST, 8796), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:8796"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls._tmp.cleanup()

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base + "/api/notes",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        return json.loads(urllib.request.urlopen(req, timeout=15).read())

    def test_read_returns_text_and_links(self) -> None:
        data = self._post({"action": "read", "name": "Leaf"})
        self.assertTrue(data["ok"])
        self.assertIn("passkeys", data["text"])
        self.assertEqual(data["backlinks"], ["Hub"])  # Hub links to Leaf
        self.assertEqual(data["outlinks"], [])

    def test_read_outlinks(self) -> None:
        data = self._post({"action": "read", "name": "Hub"})
        self.assertEqual(data["outlinks"], ["Leaf"])

    def test_search(self) -> None:
        data = self._post({"action": "search", "query": "passkeys"})
        self.assertTrue(data["ok"])
        self.assertEqual([h["name"] for h in data["results"]], ["Leaf"])

    def test_missing_note(self) -> None:
        data = self._post({"action": "read", "name": "Ghost"})
        self.assertFalse(data["ok"])


if __name__ == "__main__":
    unittest.main()
