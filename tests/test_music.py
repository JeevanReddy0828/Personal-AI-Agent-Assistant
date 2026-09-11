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


def fake_resolver(query: str) -> list[dict[str, str]]:
    return [
        {"id": "kJQP7kiw5Fk", "title": f"{query} - the top hit"},
        {"id": "gm3-m2CFVWM", "title": f"{query} - a cover"},
    ]


def dead_resolver(query: str) -> list[dict[str, str]]:
    return []


class MusicPlayTests(unittest.TestCase):
    def tool(self, resolver=fake_resolver) -> tuple[MusicTool, FakeWeb]:
        web = FakeWeb()
        return MusicTool(approval_gate=None, desktop=None, web=web, resolver=resolver), web

    def test_url_passthrough(self) -> None:
        tool, web = self.tool()
        result = tool.play("https://youtu.be/abc")
        self.assertTrue(result.ok)
        self.assertEqual(web.opened, ["https://youtu.be/abc"])

    def test_a_named_song_opens_the_video_not_the_search_page(self) -> None:
        # Reported: "it can only search in youtube, cant play any video".
        tool, web = self.tool()
        result = tool.play("despacito")
        self.assertTrue(result.ok)
        self.assertEqual(web.opened, ["https://www.youtube.com/watch?v=kJQP7kiw5Fk"])
        self.assertEqual(result.data["video_id"], "kJQP7kiw5Fk")
        self.assertIn("Playing", result.message)

    def test_the_resolver_is_given_the_cleaned_query(self) -> None:
        seen: list[str] = []

        def spy(query: str) -> list[dict[str, str]]:
            seen.append(query)
            return fake_resolver(query)

        tool, _ = self.tool(spy)
        tool.play("some youtube music")
        self.assertEqual(seen, ["music"])

    def test_an_unreachable_youtube_falls_back_to_the_search_page(self) -> None:
        # Losing the network should cost a click, not the feature.
        tool, web = self.tool(dead_resolver)
        result = tool.play("lofi beats")
        self.assertTrue(result.ok)
        self.assertFalse(result.data["resolved"])
        self.assertIn("search_query=lofi+beats", web.opened[0])

    def test_a_browser_failure_is_reported_rather_than_claimed_as_playing(self) -> None:
        class RefusingWeb(FakeWeb):
            def open_url(self, url: str) -> ToolResult:
                return ToolResult.failure("Approval denied")

        tool = MusicTool(None, None, RefusingWeb(), resolver=fake_resolver)
        self.assertFalse(tool.play("despacito").ok)

    def test_empty_target(self) -> None:
        tool, _ = self.tool()
        self.assertFalse(tool.play("   ").ok)


class BackReferenceTests(unittest.TestCase):
    """Reported: "play songs in the youtube you just opened" searched YouTube for
    "in you just opened" — the cleanup stripped the real words and kept the filler."""

    def test_a_back_reference_asks_instead_of_searching(self) -> None:
        self.assertEqual(MusicTool._youtube_query("songs in the youtube you just opened"), "")
        self.assertEqual(MusicTool._youtube_query("it"), "")
        self.assertEqual(MusicTool._youtube_query("that one again"), "")

    def test_a_real_request_still_becomes_a_query(self) -> None:
        self.assertEqual(MusicTool._youtube_query("new telugu songs"), "new telugu")
        self.assertEqual(MusicTool._youtube_query("despacito"), "despacito")
        self.assertEqual(MusicTool._youtube_query("some youtube music"), "music")

    def test_trailing_sentence_punctuation_is_not_part_of_the_title(self) -> None:
        # Dictated speech ends in a full stop; "new telugu hits." was searched with it.
        self.assertEqual(MusicTool._youtube_query("new telugu hits."), "new telugu hits")
        self.assertEqual(MusicTool._youtube_query("despacito?"), "despacito")


class ResolverParsingTests(unittest.TestCase):
    """The resolver reads YouTube's own embedded JSON; these are the shapes it must
    survive without the network."""

    def parse(self, body: str):
        from laptop_agent.tools import music

        captured: list[dict[str, str]] = []

        class FakeResponse:
            def read(self, _n: int = 0) -> bytes:
                return body.encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        original = music.urllib.request.urlopen
        music.urllib.request.urlopen = lambda *a, **k: FakeResponse()
        try:
            captured = music._default_resolver("anything")
        finally:
            music.urllib.request.urlopen = original
        return captured

    def test_results_are_returned_in_page_order_without_duplicates(self) -> None:
        body = (
            '{"videoRenderer":{"videoId":"aaaaaaaaaaa","title":{"runs":[{"text":"First"}]}},'
            '"videoRenderer":{"videoId":"aaaaaaaaaaa","title":{"runs":[{"text":"First again"}]}},'
            '"videoRenderer":{"videoId":"bbbbbbbbbbb","title":{"runs":[{"text":"Second"}]}}}'
        )
        got = self.parse(body)
        self.assertEqual([v["id"] for v in got], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
        self.assertEqual(got[0]["title"], "First")

    def test_an_escaped_title_is_decoded(self) -> None:
        body = (
            '"videoRenderer":{"videoId":"ccccccccccc","title":{"runs":'
            '[{"text":"He said \\"hi\\" \\u2014 loudly"}]}}'
        )
        self.assertEqual(self.parse(body)[0]["title"], 'He said "hi" — loudly')

    def test_a_page_with_no_results_yields_nothing(self) -> None:
        self.assertEqual(self.parse("<html>nothing here</html>"), [])


if __name__ == "__main__":
    unittest.main()
