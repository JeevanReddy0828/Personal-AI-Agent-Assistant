"""GPU status rows keep refreshing when adapter names are missing."""
import os
import unittest

import test_browser_regressions as browser_tests


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class GPUStatusTests(unittest.TestCase):
    setUpClass = classmethod(browser_tests.BrowserRegressions.setUpClass.__func__)
    tearDownClass = classmethod(browser_tests.BrowserRegressions.tearDownClass.__func__)
    setUp = browser_tests.BrowserRegressions.setUp
    tearDown = browser_tests.BrowserRegressions.tearDown
    wait_js = browser_tests.BrowserRegressions.wait_js

    def show_metrics(self, names):
        self.page.route("**/api/metrics", lambda route: route.fulfill(json={
            "cpu_percent": 37, "ram_percent": 51, "gpus": [
                {"name": name, "util_kind": "3D", "util_percent": 14,
                 "mem_used_mb": 1024, "mem_total_mb": 4096} for name in names]}))
        self.page.reload()
        self.page.locator("#railStatus").click()
        self.wait_js("document.getElementById('metrics').textContent.includes('37%')")

    def test_missing_names_refresh_connection_and_drawer(self):
        for name in (None, "", "   ", 17):
            with self.subTest(name=name):
                self.show_metrics([name])
                self.wait_js("document.getElementById('connlist').textContent.includes('GPU 1')")
                self.assertIn("GPU 1", self.page.locator("#metrics").inner_text())
                self.page.unroute("**/api/metrics")

    def test_each_vram_row_names_its_adapter_and_escapes_html(self):
        names = ["AMD Radeon(TM) Graphics", "NVIDIA GeForce RTX 4060", None,
                 '<img src=x onerror="window.__gpuXss=1">']
        self.show_metrics(names)
        labels = self.page.locator("#metrics .metric .top span").all_text_contents()
        for name in (names[0], names[1], "GPU 3", names[3]):
            self.assertIn("VRAM · " + name, labels)
        self.assertEqual(self.page.locator("#metrics img").count(), 0)
        self.assertIsNone(self.page.evaluate("window.__gpuXss"))
