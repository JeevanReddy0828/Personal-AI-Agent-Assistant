from __future__ import annotations

import threading
from pathlib import Path
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from laptop_agent.config import load_config

_CONFIG = load_config()
PNG_HEADER = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A]) + b"\n"


class CacheHeaderTests(unittest.TestCase):
    """One response, one Cache-Control.

    `end_headers` used to send `no-store` on every response, on top of whatever the route
    had already chosen, so responses went out with two Cache-Control headers. Folded,
    `no-store` wins — which silently defeated both deliberate choices in the file: the
    page's ETag could never produce a 304 because a browser was forbidden to store the
    page it would revalidate, and a generated image was re-fetched on every render
    despite asking for a day of caching.
    """

    @classmethod
    def setUpClass(cls):
        import laptop_agent.webui as webui

        cls.webui = webui
        cls.metrics = patch.object(webui, "system_metrics",
                                   return_value={"cpu_percent": 1, "ram_percent": 2, "gpus": []})
        cls.metrics.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.metrics.stop()

    def request(self, path="/", headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read(), exc.headers

    def cache_values(self, headers):
        return headers.get_all("Cache-Control") or []

    def test_the_page_carries_exactly_one_cache_control(self) -> None:
        _status, _body, headers = self.request("/")
        self.assertEqual(self.cache_values(headers), ["private, no-cache"])

    def test_the_page_is_never_offered_to_a_shared_cache(self) -> None:
        """The page embeds the per-process API token, and that token is shell, files and
        mail on this laptop. It only became cacheable at all when the blanket `no-store`
        stopped overriding the route, so `private` has to arrive in the same change."""
        _status, body, headers = self.request("/")
        self.assertIn(b"X-Jarvis-Token", body, "the page no longer carries the token; revisit this")
        for value in self.cache_values(headers):
            self.assertIn("private", value, "a token-bearing page was offered to shared caches")

    def test_an_api_answer_defaults_to_no_store(self) -> None:
        _status, _body, headers = self.request("/api/metrics")
        self.assertEqual(self.cache_values(headers), ["no-store"])

    def test_a_generated_image_keeps_the_caching_it_asks_for(self) -> None:
        directory = (_CONFIG.data_dir / "images").resolve()
        directory.mkdir(parents=True, exist_ok=True)
        picture = directory / "cache-probe.png"
        picture.write_bytes(PNG_HEADER)
        self.addCleanup(picture.unlink, True)

        status, _body, headers = self.request("/api/image?name=cache-probe.png")
        self.assertEqual(status, 200)
        self.assertEqual(self.cache_values(headers), ["private, max-age=86400"])

    def test_a_conditional_request_for_the_page_is_answered_304(self) -> None:
        """The whole point of the ETag. `no-store` on top of it made this unreachable from
        a browser: nothing was stored, so nothing was ever revalidated."""
        _status, body, headers = self.request("/")
        etag = headers.get("ETag")
        self.assertTrue(etag, "the page must carry an ETag")

        status, second, headers = self.request("/", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        self.assertEqual(second, b"")
        self.assertEqual(self.cache_values(headers), ["private, no-cache"])
        self.assertGreater(len(body), 100_000, "the 200 really is the whole page")

    def test_nothing_user_specific_is_offered_to_a_shared_cache(self) -> None:
        """Every route that opts out of the `no-store` default must say `private`. None of
        them were storable at all until the blanket `no-store` stopped overriding them, so
        each one is newly exposed and has to be checked, not just the page."""
        import re

        source = (Path(self.webui.__file__)).read_text(encoding="utf-8")
        chosen = re.findall(r'_cache\("([^"]+)"\)', source)
        self.assertGreaterEqual(len(chosen), 4, "the cache call sites moved; revisit this")
        for value in chosen:
            self.assertIn("private", value,
                          f"a route opts out of no-store as {value!r}, which shared caches may hold")

    def test_every_route_answers_with_at_most_one_cache_control(self) -> None:
        for path in ("/", "/api/metrics", "/api/health", "/api/traces", "/nope"):
            with self.subTest(path=path):
                _status, _body, headers = self.request(path)
                self.assertLessEqual(len(self.cache_values(headers)), 1,
                                     f"{path} sent duplicate Cache-Control headers")


if __name__ == "__main__":
    unittest.main()
