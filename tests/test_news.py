from __future__ import annotations

import unittest

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.news import NewsTool

FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Houthis seize Red Sea port of Mokha</title>
    <link>https://www.bbc.co.uk/news/articles/abc123</link>
    <pubDate>Thu, 10 Sep 2026 18:15:47 GMT</pubDate>
    <description>Capture brings the group closer to the Bab al-Mandab Strait.</description>
  </item>
  <item>
    <title>Justice Dept. Investigates Nvidia Deal - The New York Times</title>
    <link>https://news.google.com/rss/articles/CBMiek</link>
    <source>The New York Times</source>
    <pubDate>Thu, 10 Sep 2026 00:23:24 GMT</pubDate>
    <description>Justice Dept. Investigates Nvidia Deal - The New York Times&amp;nbsp; NYT</description>
  </item>
</channel></rss>"""


class NewsToolTests(unittest.TestCase):
    def tool(self, feed: bytes = FEED, page_reader=None, **kwargs) -> NewsTool:
        return NewsTool(backend=lambda url: feed, page_reader=page_reader, **kwargs)

    def test_headlines_carry_source_age_and_link(self) -> None:
        result = self.tool().headlines()
        self.assertTrue(result.ok)
        first = result.data["headlines"][0]
        self.assertEqual(first["title"], "Houthis seize Red Sea port of Mokha")
        self.assertTrue(first["url"].startswith("https://www.bbc.co.uk/"))
        self.assertIn("ago", first["age"])
        self.assertIn("Bab al-Mandab", first["summary"])
        self.assertIn("Bab al-Mandab", result.message)

    def test_a_description_that_only_repeats_the_headline_is_not_a_summary(self) -> None:
        # Google News echoes the title as the description; printing it twice is noise.
        item = next(h for h in self.tool().headlines().data["headlines"] if "Nvidia" in h["title"])
        self.assertEqual(item["summary"], "")

    def test_duplicate_stories_across_feeds_appear_once(self) -> None:
        result = self.tool().headlines()
        titles = [h["title"] for h in result.data["headlines"]]
        self.assertEqual(len(titles), len(set(titles)))

    def test_article_text_is_fetched_for_readable_links_only(self) -> None:
        asked: list[str] = []

        def reader(url: str) -> str:
            asked.append(url)
            return "The port of Mokha fell overnight. " * 20

        result = self.tool(page_reader=reader).headlines(with_text=3)
        # the Google News link is a consent page, so spending a fetch on it is wasted
        self.assertTrue(all("news.google.com" not in url for url in asked), asked)
        withtext = [h for h in result.data["headlines"] if h.get("text")]
        self.assertEqual(len(withtext), 1)
        self.assertIn("Mokha fell overnight", withtext[0]["text"])

    def test_a_failing_page_read_does_not_lose_the_headline(self) -> None:
        def broken(url: str) -> str:
            raise RuntimeError("blocked")

        result = self.tool(page_reader=broken).headlines()
        self.assertTrue(result.ok)
        self.assertEqual(len(result.data["headlines"]), 2)

    def test_a_topic_search_is_reported_as_such(self) -> None:
        result = self.tool().headlines("nvidia")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["topic"], "nvidia")
        self.assertIn("about nvidia", result.message)

    def test_an_unreachable_feed_fails_clearly(self) -> None:
        def dead(url: str) -> bytes:
            raise TimeoutError("no route")

        result = NewsTool(backend=dead).headlines()
        self.assertFalse(result.ok)
        self.assertIn("could not reach the news feeds", result.message)

    def test_malformed_xml_is_survived(self) -> None:
        result = NewsTool(backend=lambda url: b"<not xml").headlines()
        self.assertFalse(result.ok)

    def test_denied_approval_stops_the_fetch(self) -> None:
        calls: list[str] = []

        def backend(url: str) -> bytes:
            calls.append(url)
            return FEED

        tool = NewsTool(backend=backend, approval_gate=ApprovalGate(ask=lambda request: False))
        with self.assertRaises(ApprovalDenied):
            tool.headlines()
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
