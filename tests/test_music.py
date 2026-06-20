from __future__ import annotations

import unittest

from laptop_agent.tools.base import ToolResult
from laptop_agent.tools.music import MusicTool


class FakeWeb:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def open_url(self, url: str) -> ToolResult:
        self.opened.append(url)
        return ToolResult.success(f"Opened URL: {url}", url=url)


class MusicPlayTests(unittest.TestCase):
    def tool(self) -> tuple[MusicTool, FakeWeb]:
        web = FakeWeb()
        return MusicTool(approval_gate=None, desktop=None, web=web), web

    def test_url_passthrough(self) -> None:
        tool, web = self.tool()
        result = tool.play("https://youtu.be/abc")
        self.assertTrue(result.ok)
        self.assertEqual(web.opened, ["https://youtu.be/abc"])

    def test_generic_youtube_request_searches_music(self) -> None:
        tool, web = self.tool()
        result = tool.play("some youtube music")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["query"], "music")
        self.assertIn("youtube.com/results?search_query=music", web.opened[0])

    def test_named_song_searches_youtube(self) -> None:
        tool, web = self.tool()
        result = tool.play("lofi beats")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["query"], "lofi beats")
        self.assertIn("search_query=lofi+beats", web.opened[0])

    def test_empty_target(self) -> None:
        tool, _ = self.tool()
        self.assertFalse(tool.play("   ").ok)


if __name__ == "__main__":
    unittest.main()
