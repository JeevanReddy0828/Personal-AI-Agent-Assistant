"""Opt-in Chromium checks that a due reminder reaches the screen: JARVIS_BROWSER_TESTS=1.

Asserted as boxes, not attributes. ERRORS.md records a meter that passed its test for
three months while rendering nothing, because the test read `hidden` on an element inside
a `display:none` parent.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.reminders import ReminderStore


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class ReminderDeliveryInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.metrics_patch = patch.object(webui, "system_metrics", return_value={"cpu_percent": 1, "ram_percent": 1, "gpus": []})
        cls.metrics_patch.start()
        cls._tmp = tempfile.TemporaryDirectory()
        cls._original = webui._orchestrator.context.reminders
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.metrics_patch.stop()
        cls._tmp.cleanup()

    def setUp(self):
        self.store = ReminderStore(Path(self._tmp.name) / f"{self._testMethodName}.json")
        object.__setattr__(webui._orchestrator.context, "reminders", self.store)
        self.addCleanup(lambda: object.__setattr__(webui._orchestrator.context, "reminders", self._original))
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        self.context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page = self.context.new_page()
        self.errors: list[str] = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def card_box(self, timeout: float = 6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            card = self.page.locator(".remcard")
            if card.count():
                box = card.first.bounding_box()
                if box and box["height"] > 0 and box["width"] > 0:
                    return box
            self.page.wait_for_timeout(100)
        return None

    def test_a_due_reminder_is_on_screen_and_done_completes_it(self):
        self.store.add((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "call mom")
        self.page.goto(self.url)
        box = self.card_box()
        self.assertIsNotNone(box, "no reminder card on screen")
        self.assertIn("call mom", self.page.locator(".remcard .remmsg").inner_text())
        viewport = self.page.viewport_size
        self.assertLessEqual(box["x"] + box["width"], viewport["width"])
        self.page.locator(".remcard .remdone").click()
        self.page.wait_for_function("() => !document.querySelector('.remcard')", timeout=6000)
        self.assertEqual(self.store.list(), [])

    def test_a_timer_goes_off_on_time_without_a_reload(self):
        # Due in two seconds: the page must look again when it is due, not in thirty.
        self.store.add((datetime.now(UTC) + timedelta(seconds=2)).isoformat(), "Timer (2 seconds)")
        self.page.goto(self.url)
        self.page.wait_for_timeout(500)
        self.assertEqual(self.page.locator(".remcard").count(), 0, "went off early")
        started = time.monotonic()
        self.assertIsNotNone(self.card_box(timeout=6.0), "the timer never went off")
        self.assertLess(time.monotonic() - started, 4.5)

    def test_snooze_moves_it_later(self):
        item = self.store.add((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "stretch")["reminder"]
        self.page.goto(self.url)
        self.assertIsNotNone(self.card_box())
        self.page.get_by_role("button", name="Snooze 10 min").click()
        self.page.wait_for_function("() => !document.querySelector('.remcard')", timeout=6000)
        (snoozed,) = self.store.list()
        self.assertEqual(snoozed["id"], item["id"])
        self.assertGreater(datetime.fromisoformat(snoozed["due_at"]), datetime.now(UTC) + timedelta(minutes=9))

    def test_a_dismissed_reminder_is_not_raised_again_by_a_reload(self):
        self.store.add((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "stretch")
        self.page.goto(self.url)
        self.assertIsNotNone(self.card_box())
        self.page.get_by_role("button", name="Dismiss").click()
        self.assertEqual(self.page.locator(".remcard").count(), 0)
        self.page.reload()
        self.page.wait_for_timeout(1000)
        self.assertEqual(self.page.locator(".remcard").count(), 0, "announced twice")
        self.assertEqual(len(self.store.due()), 1, "dismissing must not complete it")

    def test_cancelling_them_all_asks_with_a_readable_card(self):
        # The preview ran every reminder into one line and split words mid-way ("a t").
        self.store.add((datetime.now(UTC) + timedelta(hours=2)).isoformat(), "stretch")
        self.store.add((datetime.now(UTC) + timedelta(hours=3)).isoformat(), "x" * 300)
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.url)
        self.page.wait_for_selector("#ta")
        self.page.fill("#ta", "cancel all my reminders")
        self.page.keyboard.press("Enter")
        self.page.wait_for_selector(".apcard", timeout=15000)
        lines = [line for line in self.page.locator(".apcard .appre").inner_text().split("\n") if line.strip()]
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("stretch", lines[0])
        box = self.page.locator(".apcard").bounding_box()
        self.assertLessEqual(box["x"] + box["width"], 391)
        self.assertTrue(self.page.evaluate(
            "(() => { const p = document.querySelector('.apcard .appre'); return p.scrollWidth <= p.clientWidth + 1; })()"),
            "a long reminder scrolls sideways inside the card")
        self.page.get_by_role("button", name="Deny").click()
        self.page.wait_for_function("() => !document.querySelector('.apcard')", timeout=6000)
        self.assertEqual(len(self.store.list()), 2, "denied, so nothing is removed")

    def add_due(self, *messages):
        for minutes, message in enumerate(messages, start=1):
            self.store.add((datetime.now(UTC) - timedelta(minutes=minutes)).isoformat(), message)

    def shown(self) -> int:
        # A box, not a class: a card is on screen only if it has a height.
        return self.page.evaluate(
            "() => [...document.querySelectorAll('#remtray [data-rem]')]"
            ".filter(card => card.getBoundingClientRect().height > 0).length")

    def test_many_due_reminders_show_a_few_and_count_the_rest(self):
        # Five due at once covered the screen, settings included, and each was dismissed alone.
        self.add_due(*[f"reminder {n}" for n in range(8)])
        self.page.goto(self.url)
        self.page.wait_for_selector("[data-rem-more]")
        self.assertEqual(self.shown(), 3)
        more = self.page.locator("[data-rem-more]")
        self.assertIn("+5 more reminders", more.inner_text())
        more.get_by_role("button", name="Show all").click()
        self.assertEqual(self.shown(), 8)
        tray = self.page.locator("#remtray").bounding_box()
        self.assertLessEqual(tray["y"] + tray["height"], self.page.viewport_size["height"], "runs off the screen")
        more.get_by_role("button", name="Dismiss all").click()
        self.assertEqual(self.page.locator("#remtray [data-rem], [data-rem-more]").count(), 0)
        self.assertEqual(len(self.store.due()), 8, "dismissing must not complete them")

    def test_dismissing_one_brings_the_next_into_view(self):
        self.add_due("one", "two", "three", "four")
        self.page.goto(self.url)
        self.page.wait_for_selector("[data-rem-more]")
        self.assertEqual(self.shown(), 3)
        self.page.locator("#remtray [data-rem]").first.get_by_role("button", name="Dismiss").click()
        self.assertEqual(self.shown(), 3)
        self.assertEqual(self.page.locator("[data-rem-more]").count(), 0, "a summary of nothing hidden")

    def test_on_a_phone_one_shows(self):
        self.add_due("one", "two", "three")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.url)
        self.page.wait_for_selector("[data-rem-more]")
        self.assertEqual(self.shown(), 1)
        self.assertIn("+2 more reminders", self.page.locator("[data-rem-more]").inner_text())

    def test_the_settings_popover_is_above_the_reminders(self):
        self.add_due("one", "two", "three")
        self.page.goto(self.url)
        self.assertIsNotNone(self.card_box())
        self.page.click("#hudBtn")
        self.page.wait_for_function("() => document.querySelector('#hudPop').getBoundingClientRect().height > 0")
        on_top = self.page.evaluate("""() => {
            const pop = document.querySelector('#hudPop').getBoundingClientRect();
            const tray = document.querySelector('#remtray').getBoundingClientRect();
            const x = Math.max(pop.left, tray.left) + 10, y = Math.max(pop.top, tray.top) + 10;
            return !!document.elementFromPoint(x, y).closest('#hudPop');
        }""")
        self.assertTrue(on_top, "a reminder covers the settings the user just opened")

    def test_reminder_text_is_text_not_markup(self):
        self.store.add((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "<img src=x onerror=alert(1)>")
        self.page.goto(self.url)
        self.assertIsNotNone(self.card_box())
        self.assertEqual(self.page.locator(".remcard img").count(), 0)
        self.assertIn("<img", self.page.locator(".remcard .remmsg").inner_text())


if __name__ == "__main__":
    unittest.main()
