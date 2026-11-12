from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from laptop_agent.safety import ApprovalDenied, ApprovalGate
from laptop_agent.tools.web import WebTool


class UrlValidationTests(unittest.TestCase):
    """Reported: "download it for me" reached the approval gate as `https://it for me` —
    the words were pasted straight into a URL."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tool(self, ask=lambda request: True) -> WebTool:
        return WebTool(ApprovalGate(ask=ask), Path(self._tmp.name))

    def test_a_phrase_is_not_a_url(self) -> None:
        for phrase in ("it for me", "a simple dataset", "something", "the file you mentioned"):
            self.assertFalse(WebTool._looks_like_url(phrase), phrase)

    def test_a_real_address_is(self) -> None:
        for url in ("https://example.com/data.csv", "example.com", "localhost:8770",
                    "https://youtu.be/abc123", "http://127.0.0.1:8770"):
            self.assertTrue(WebTool._looks_like_url(url), url)

    def test_download_refuses_a_phrase_without_asking_for_approval(self) -> None:
        asked: list[str] = []
        result = self.tool(ask=lambda request: asked.append(request.action) or True).download("it for me")
        self.assertFalse(result.ok)
        self.assertIn("not an address", result.message)
        self.assertEqual(asked, [])      # never reaches the gate, so never reaches the network

    def test_open_url_refuses_a_phrase(self) -> None:
        result = self.tool().open_url("it for me")
        self.assertFalse(result.ok)
        self.assertIn("not an address", result.message)

    def test_a_sentence_ending_is_not_a_host(self) -> None:
        # "me." parses as a netloc with a dot in it; a host needs a real TLD.
        for phrase in ("me.", "now.", "this.", "one.two."):
            self.assertFalse(WebTool._looks_like_url(phrase), phrase)


class UrlInsideAPhraseTests(unittest.TestCase):
    """Reported after the guard above shipped: "download it for me -
    https://gdoc.io/..." was refused with the link sitting right there, and so was a
    link pasted on its own line. Refusing every phrase was too blunt — only a request
    with no address at all should be refused."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_a_trailing_link_is_used(self) -> None:
        self.assertEqual(
            WebTool._extract_url("it for me - https://gdoc.io/resume-templates/classic/"),
            "https://gdoc.io/resume-templates/classic/",
        )

    def test_a_link_on_its_own_line_is_used(self) -> None:
        self.assertEqual(
            WebTool._extract_url("it for me\nhttps://www.kaggle.com/datasets/kaggle/pokemon"),
            "https://www.kaggle.com/datasets/kaggle/pokemon",
        )

    def test_surrounding_punctuation_is_not_part_of_the_link(self) -> None:
        self.assertEqual(
            WebTool._extract_url("grab (https://example.com/a.pdf) now"),
            "https://example.com/a.pdf",
        )
        self.assertEqual(
            WebTool._extract_url("see https://example.com/report.html."),
            "https://example.com/report.html",
        )

    def test_a_phrase_with_no_link_is_still_refused(self) -> None:
        for phrase in ("it for me", "download it for me.", "please get me this one", "that file"):
            self.assertIsNone(WebTool._extract_url(phrase), phrase)

    def test_open_url_opens_the_link_inside_the_phrase(self) -> None:
        opened: list[str] = []
        tool = WebTool(ApprovalGate(ask=lambda request: True), Path(self._tmp.name))
        tool._launch_browser = lambda url: opened.append(url) or True  # type: ignore[method-assign]
        result = tool.open_url("open this one for me https://example.com/page")
        self.assertTrue(result.ok)
        self.assertEqual(opened, ["https://example.com/page"])

    def test_the_gate_is_asked_about_the_extracted_link_only(self) -> None:
        asked: list[str] = []
        tool = WebTool(
            ApprovalGate(ask=lambda request: asked.append(request.action) or False),
            Path(self._tmp.name),
        )
        with self.assertRaises(ApprovalDenied):
            tool.download("it for me - https://example.com/data.csv")
        self.assertEqual(asked, ["Download file: https://example.com/data.csv"])


class WebSchemesOnlyTests(unittest.TestCase):
    """`open url` runs with no card in the web app and hands the address to the Windows shell:
    file://server/share/x.exe started a program, and ms-msdt:, search-ms: start their handlers."""

    NOT_WEB = ("file://localhost/C:/Windows/System32/calc.exe", "file://evil.example.com/share/x.exe",
               "ms-msdt://a.com/x", "search-ms://q.com", "ftp://ftp.example.com/a", "vscode://ms.ext/x")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name)

    def tool(self, body: bytes = b"data", asked: list[str] | None = None) -> WebTool:
        import io

        gate = ApprovalGate(ask=lambda request: (asked.append(request.action) if asked is not None else None) or True)
        return WebTool(gate, self.folder, opener=lambda url: io.BytesIO(body))

    def test_only_http_and_https_are_addresses(self) -> None:
        for url in self.NOT_WEB:
            self.assertIsNone(WebTool._extract_url(url), url)
        self.assertEqual(WebTool._extract_url("HTTP://Example.com/a"), "HTTP://Example.com/a")

    def test_open_url_never_launches_another_scheme(self) -> None:
        for url in self.NOT_WEB:
            asked: list[str] = []
            tool = self.tool(asked=asked)
            launched: list[str] = []
            tool._launch_browser = lambda target: launched.append(target) or True  # type: ignore[method-assign]
            self.assertFalse(tool.open_url(url).ok, url)
            self.assertEqual((asked, launched), ([], []), url)

    def test_download_refuses_another_scheme_before_asking(self) -> None:
        asked: list[str] = []
        self.assertFalse(self.tool(asked=asked).download("file://localhost/C:/Windows/win.ini").ok)
        self.assertEqual(asked, [])

    def test_a_redirect_off_the_web_is_refused(self) -> None:
        import urllib.error
        import urllib.request

        from laptop_agent.tools.web import _WebRedirects

        request = urllib.request.Request("https://example.com/a")
        with self.assertRaises(urllib.error.HTTPError):
            _WebRedirects().redirect_request(request, None, 302, "Found", {}, "ftp://example.com/a")
        self.assertIsNotNone(_WebRedirects().redirect_request(request, None, 302, "Found", {}, "https://example.org/b"))

    def test_a_download_never_overwrites(self) -> None:
        (self.folder / "data.csv").write_bytes(b"mine")
        result = self.tool(body=b"theirs").download("https://example.com/data.csv")
        self.assertTrue(result.ok, result.message)
        self.assertEqual((self.folder / "data.csv").read_bytes(), b"mine")
        self.assertEqual(Path(result.data["path"]).read_bytes(), b"theirs")

    def test_a_filename_cannot_leave_the_downloads_folder(self) -> None:
        result = self.tool().download("https://example.com/a", filename="..\\..\\evil.bat")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(Path(result.data["path"]).parent.resolve(), self.folder.resolve())

    def test_a_download_over_the_limit_is_stopped_and_removed(self) -> None:
        from unittest.mock import patch

        with patch("laptop_agent.tools.web.MAX_DOWNLOAD_BYTES", 3):
            result = self.tool(body=b"too much").download("https://example.com/big.bin")
        self.assertFalse(result.ok)
        self.assertEqual(list(self.folder.iterdir()), [])


class BrowserWebOnlyTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_browser_never_loads_a_local_file(self) -> None:
        from laptop_agent.tools.browser import BrowserAutomationTool

        asked: list[str] = []
        tool = BrowserAutomationTool(ApprovalGate(ask=lambda request: asked.append(request.action) or True))
        for call in (tool.inspect_page("file:///C:/Users/me/secret.txt"), tool.inspect_forms("file:///C:/x.html"),
                     tool.preview_form_fill("file:///C:/x.html", {"name": "A"}),
                     tool.fill_form("file:///C:/x.html", {"name": "A"})):
            result = await call
            self.assertFalse(result.ok)
            self.assertIn("http", result.message)
        self.assertEqual(asked, [])

    def test_a_host_with_a_port_is_given_https(self) -> None:
        from laptop_agent.tools.browser import BrowserAutomationTool

        self.assertEqual(BrowserAutomationTool._normalize_url("localhost:8000"), "https://localhost:8000")


class ResearchFetchTests(unittest.TestCase):
    def test_a_local_file_is_never_fetched_as_a_page(self) -> None:
        from laptop_agent.tools.research import fetch_page_text

        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as handle:
            handle.write("private words")
        self.addCleanup(Path(handle.name).unlink)
        self.assertEqual(fetch_page_text(Path(handle.name).as_uri()), "")


if __name__ == "__main__":
    unittest.main()
