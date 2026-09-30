"""Opt-in Chromium checks that saved chats stay with their account: JARVIS_BROWSER_TESTS=1.

Chats live in the browser's storage. One origin-wide key let a personal account signed in on
the same browser reopen the owner's chats (Codex's review of #138). They are now kept per
account id, read only once the page knows who is signed in.
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

OWNER_TEXT = "Owner-only mailbox fixture"


@unittest.skipUnless(os.environ.get("JARVIS_BROWSER_TESTS") == "1", "Opt-in Chromium checks")
class ChatsStayWithTheirAccount(unittest.TestCase):
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

    def setUp(self):
        # One browser, shared by both accounts, as on a family laptop.
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
        self.addCleanup(self.context.close)
        self.context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.url) else route.abort())
        self.page = self.context.new_page()

    def as_(self, role: str):
        self.context.add_cookies([{"name": "jarvis_session", "value": self.tokens[role], "url": self.url}])
        self.page.goto(self.url)
        name = {"dev": "jeevan", "personal": "family"}[role]
        # Arrow functions only: the page's CSP refuses the eval a bare expression needs. Ready
        # means the page knows who is signed in and has chosen whose chats to load.
        self.page.wait_for_function(
            f"() => chatKey !== null && document.getElementById('acctWho').textContent.endsWith('{name}')", timeout=6000)
        self.page.wait_for_timeout(200)

    def rail(self) -> str:
        return self.page.locator("#sessions").inner_text()

    def test_a_personal_account_never_opens_the_owners_chats(self):
        self.as_("dev")
        original = self.page.evaluate(f"""() => {{
            newSession(); const s = curSession(); s.title = 'Private owner chat';
            s.msgs.push({{role: 'bot', text: '{OWNER_TEXT}', at: Date.now()}}); saveSessions(); return current;
        }}""")
        self.as_("personal")
        self.assertNotIn("Private owner chat", self.rail())
        self.page.evaluate("id => loadSession(id)", original)
        self.assertNotIn(OWNER_TEXT, self.page.locator("#chat").inner_text())
        self.as_("dev")
        self.assertIn("Private owner chat", self.rail(), "the owner lost their own chat")

    def test_chats_from_before_sign_in_go_to_the_first_developer_only(self):
        self.page.goto(self.url + "/api/me")   # an origin to write storage from, before the app runs
        self.page.evaluate("""() => localStorage.setItem('jarvis_sessions', JSON.stringify(
            [{id: 'legacy-1', title: 'Before sign-in', msgs: [{role: 'user', text: 'hello', at: 1}]}]))""")
        self.as_("personal")
        self.assertNotIn("Before sign-in", self.rail())
        self.assertIsNotNone(self.page.evaluate("() => localStorage.getItem('jarvis_sessions')"),
                             "a personal account must not take the owner's old chats either")
        self.as_("dev")
        self.assertIn("Before sign-in", self.rail())
        self.assertIsNone(self.page.evaluate("() => localStorage.getItem('jarvis_sessions')"))

    def test_a_tab_reloads_when_someone_else_signs_in_elsewhere(self):
        self.as_("dev")
        self.context.add_cookies([{"name": "jarvis_session", "value": self.tokens["personal"], "url": self.url}])
        with self.page.expect_navigation(timeout=6000):
            self.page.evaluate("() => window.dispatchEvent(new Event('focus'))")
        self.page.wait_for_function("() => document.getElementById('acctWho').textContent.endsWith('family')", timeout=6000)


if __name__ == "__main__":
    unittest.main()
