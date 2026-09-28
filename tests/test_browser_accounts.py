"""Opt-in Chromium checks of what each kind of account is offered: JARVIS_BROWSER_TESTS=1.

The server refuses a `personal` account what it may not use; the page should not offer it
either, and must not loop on the refusals it still meets. Asserted as boxes, not attributes:
ERRORS.md records a meter that passed its test for three months while rendering nothing.
"""
from __future__ import annotations

import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import laptop_agent.webui as webui
from laptop_agent.accounts import AccountStore
from laptop_agent.sessions import SessionStore


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class AccountsInTheBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls._tmp = tempfile.TemporaryDirectory()
        accounts = AccountStore(Path(cls._tmp.name) / "accounts.json", cost=(2 ** 10, 8, 1))
        sessions = SessionStore(Path(cls._tmp.name) / "sessions.json")
        cls.patches = [patch.object(webui, "ACCOUNTS", accounts), patch.object(webui, "SESSIONS", sessions),
                       patch.object(webui, "system_metrics", return_value={"cpu_percent": 1, "ram_percent": 1, "gpus": []})]
        for patcher in cls.patches:
            patcher.start()
        cls.tokens = {}
        for username, role in (("jeevan", "dev"), ("family", "personal")):
            account = accounts.create(username, role, "correct horse battery")
            cls.tokens[role] = sessions.create(account.id, "password")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        for patcher in cls.patches:
            patcher.stop()
        cls._tmp.cleanup()

    def open_as(self, role: str, width: int = 1280, height: int = 900):
        context = self.browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        self.addCleanup(context.close)
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        context.add_cookies([{"name": "jarvis_session", "value": self.tokens[role], "url": self.url}])
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, []))
        page.goto(self.url)
        # An arrow function, not an expression: the page's CSP refuses the eval an expression needs.
        page.wait_for_function(f"() => document.body.dataset.role === '{role}'", timeout=6000)
        return page

    @staticmethod
    def on_screen(page, selector: str) -> bool:
        return bool(page.evaluate(
            "(s) => [...document.querySelectorAll(s)].some(e => { const r = e.getBoundingClientRect();"
            " return r.width > 0 && r.height > 0; })", selector))

    def ask(self, page, text: str) -> str:
        before = page.locator(".msg").count()
        page.fill("#ta", text)
        page.press("#ta", "Enter")
        page.wait_for_function(f"() => document.querySelectorAll('.msg').length >= {before + 2}", timeout=10000)
        page.wait_for_timeout(1500)   # a local answer is revealed by the typewriter
        return page.locator(".msg").last.inner_text()

    def test_a_personal_account_is_not_offered_what_it_cannot_use(self):
        page = self.open_as("personal")
        loads: list[str] = []
        page.on("load", lambda: loads.append(page.url))
        self.assertEqual(page.inner_text("#empty h1"), "How can I help, Family?")
        for selector in ("#attachBtn", "#agentBtn", ".scard[title=Summarize]"):
            self.assertFalse(self.on_screen(page, selector), f"{selector} is offered to a personal account")
        self.assertTrue(self.on_screen(page, ".scard[title=Research]"))
        page.click("#healthPill")
        page.wait_for_timeout(400)
        for selector in ("#vstat", "#vaultSearch", "#agentList", "#schedPanel", "#runsPanel", "#acctPanel"):
            self.assertFalse(self.on_screen(page, selector), f"{selector} is in a personal account's drawer")
        self.assertTrue(self.on_screen(page, "#mapPanel"))
        self.assertNotIn("Obsidian vault", page.inner_text("#connlist"))
        page.keyboard.press("Escape")
        self.assertIn("needs a developer account", self.ask(page, "latency"))
        page.wait_for_timeout(1500)
        self.assertEqual(loads, [], "the page reloaded itself on a refusal")

    def test_a_personal_account_is_not_offered_account_management(self):
        page = self.open_as("personal")
        page.click("#hudBtn")
        page.wait_for_timeout(300)
        self.assertTrue(self.on_screen(page, "#acctOut"), "the popover did not open")
        self.assertFalse(self.on_screen(page, "#acctManage"))

    def test_a_developer_adds_an_account_from_the_drawer(self):
        page = self.open_as("dev")
        page.click("#hudBtn")
        page.click("#acctManage")
        page.wait_for_function("() => document.getElementById('acctPanel').open", timeout=3000)
        page.wait_for_function("() => document.querySelectorAll('#admList .schedcard').length >= 2", timeout=6000)
        page.fill("#admUser", "guest1")
        page.select_option("#admRole", "personal")
        page.fill("#admPw", "a good password")
        page.click("#admAdd button[type=submit]")
        self.assertIn("Type your password first", page.inner_text("#admMsg"))
        page.fill("#admCur", "correct horse battery")
        page.click("#admAdd button[type=submit]")
        page.wait_for_function(
            "() => [...document.querySelectorAll('#admList .spec')].some(e => e.textContent === 'guest1')", timeout=8000)
        self.assertIn("Added guest1", page.inner_text("#admMsg"))
        self.assertTrue(self.on_screen(page, "#admList"))
        self.assertEqual(page.input_value("#admUser"), "", "the form was not cleared after adding")

    def test_a_developer_sees_what_is_set_up_and_what_to_do(self):
        page = self.open_as("dev")
        page.click("#healthPill")
        page.click("#setupPanel summary")
        page.wait_for_function("() => document.querySelectorAll('#setupList .setrow').length === 18", timeout=6000)
        self.assertTrue(self.on_screen(page, "#setupList .setrow"))
        self.assertRegex(page.inner_text("#setupPanel summary"), r"^Setup · (\d+ to look at|all set)$")
        todo = page.eval_on_selector_all("#setupList .setrow:not([data-state=ready]) .setnext", "els => els.length")
        rows = page.eval_on_selector_all("#setupList .setrow:not([data-state=ready])", "els => els.length")
        self.assertEqual(todo, rows, "a row that is not ready shows no next step")
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))

    def test_a_personal_account_is_not_shown_the_setup(self):
        page = self.open_as("personal")
        page.click("#healthPill")
        page.wait_for_timeout(300)
        self.assertFalse(self.on_screen(page, "#setupPanel"))

    def test_a_developer_is_offered_everything_as_before(self):
        page = self.open_as("dev")
        self.assertEqual(page.inner_text("#empty h1"), "How can I help, Jeevan?")
        for selector in ("#attachBtn", "#agentBtn", ".scard[title=Summarize]"):
            self.assertTrue(self.on_screen(page, selector), f"{selector} is missing for a developer")
        self.assertNotIn("developer account", self.ask(page, "latency"))

    def test_a_personal_account_on_a_phone_keeps_a_working_composer(self):
        page = self.open_as("personal", 390, 844)
        self.assertFalse(self.on_screen(page, "#attachBtn"))
        self.assertTrue(self.on_screen(page, "#ta"))
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= 390"))


if __name__ == "__main__":
    unittest.main()
