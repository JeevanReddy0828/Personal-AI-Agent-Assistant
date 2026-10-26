"""A copy hosted on someone else's server: sign-in only, one hostname, over HTTPS, no machine.

`LAPTOP_AGENT_HOSTED=1` is read on every call, so these tests switch it with `patch.dict`
rather than reloading the module. Every live-server test swaps in its own account store,
session store and sign-in limiter, as test_webui_auth.py does, so no account lands in the
data directory the rest of the suite shares.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from laptop_agent import access
from laptop_agent.accounts import AccountStore, Principal
from laptop_agent.sessions import SessionStore

CHEAP = (2 ** 10, 8, 1)
GOOD = "correct horse battery"
NAME = "jarvis.example.com"
HOSTED_ENV = {"LAPTOP_AGENT_HOSTED": "1", "LAPTOP_AGENT_ALLOWED_HOSTS": NAME}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class LiveHostedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import laptop_agent.webui as webui

        cls.webui = webui
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.accounts = AccountStore(Path(tmp.name) / "accounts.json", cost=CHEAP)
        self.sessions = SessionStore(Path(tmp.name) / "sessions.json")
        for name, value in (("ACCOUNTS", self.accounts), ("SESSIONS", self.sessions),
                            ("_SIGNIN_LIMIT", self.webui._SignInLimit())):
            patcher = patch.object(self.webui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, HOSTED_ENV)
        env.start()
        self.addCleanup(env.stop)

    def call(self, method: str, path: str, body: dict | None = None, cookie: str = "", host: str = NAME,
             origin: str | None = "https://" + NAME, token: bool = True, headers: dict | None = None):
        """(status, parsed body, response headers). Never raises on a 4xx."""
        sent = {"Host": host, "Content-Type": "application/json", **(headers or {})}
        if origin is not None:
            sent["Origin"] = origin
        if token:
            sent["X-Jarvis-Token"] = self.webui._API_TOKEN
        if cookie:
            sent["Cookie"] = cookie
        request = urllib.request.Request(self.base + path, method=method, headers=sent,
                                         data=None if body is None else json.dumps(body).encode())
        try:
            response = urllib.request.urlopen(request, timeout=15)
        except urllib.error.HTTPError as error:
            response = error
        raw = response.read().decode("utf-8")
        kind = response.headers.get("Content-Type", "")
        return response.status, (json.loads(raw) if kind.startswith("application/json") else raw), response.headers

    def sign_in(self, username: str = "jeevan", password: str = GOOD) -> str:
        status, body, headers = self.call("POST", "/auth/login", {"username": username, "password": password},
                                          token=False)
        self.assertEqual(status, 200, body)
        return headers["Set-Cookie"].split(";", 1)[0]

    # --- who is served at all
    def test_only_the_configured_name_is_served(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        for host in ("other.example.com", f"127.0.0.1:{self.port}", f"localhost:{self.port}", "203.0.113.9",
                     "jarvis.example.com.evil.net"):
            with self.subTest(host=host):
                self.assertEqual(self.call("GET", "/", host=host, origin=None)[0], 403)
        for host in (NAME, NAME.upper(), f"{NAME}:443"):
            with self.subTest(host=host):
                self.assertEqual(self.call("GET", "/", host=host, origin=None)[0], 401)   # the sign-in page

    def test_the_origin_must_be_https_on_that_name(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        body = {"username": "jeevan", "password": GOOD}
        for origin in (f"http://{NAME}", "https://evil.example.com", f"https://{NAME}.evil.net"):
            with self.subTest(origin=origin):
                self.assertEqual(self.call("POST", "/auth/login", body, origin=origin, token=False)[0], 403)
        self.assertEqual(self.call("POST", "/auth/login", body, token=False)[0], 200)

    def test_nothing_is_served_before_an_account_exists(self) -> None:
        # No accounts: the old behaviour showed a passcode form, or served this machine's owner.
        status, page, _ = self.call("GET", "/", origin=None)
        self.assertEqual(status, 401)
        self.assertNotIn("Locked", page)
        self.assertEqual(self.call("GET", "/api/health", origin=None)[0], 401)
        self.assertEqual(self.call("POST", "/api/command", {"command": "time"})[0], 401)
        # The passcode that opens a phone on a home network opens nothing here, even the right one.
        with patch.object(self.webui, "LAN_PASSCODE", "the-right-passcode"),                 patch.object(self.webui, "LAN_MODE", True):
            status, body, headers = self.call("POST", "/api/pair", {"passcode": "the-right-passcode"}, token=False)
        self.assertEqual((status, body["message"]), (403, "Sign in with an account."))
        self.assertNotIn("Set-Cookie", headers)
        # The peer here is 127.0.0.1, which un-hosted is the owner at the keyboard and may
        # create the first account. Hosted, it is a proxy, and may not.
        status, body, _ = self.call("POST", "/auth/bootstrap", {"username": "mallory", "password": GOOD})
        self.assertIn(status, (401, 403), body)
        self.assertFalse(self.accounts.exists())
        status, body, _ = self.call("POST", "/auth/login", {"username": "x", "password": GOOD}, token=False)
        self.assertEqual(status, 409)
        self.assertIn("on the server", body["message"])

    def test_a_request_from_this_host_is_not_this_machine(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        self.assertFalse(self.call("GET", "/api/me", cookie=cookie, origin=None)[1]["local"])
        with patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": ""}):
            # The control: the same socket peer, not hosted, on the names this server answers to.
            status, body, _ = self.call("GET", "/api/me", cookie=cookie, host=f"127.0.0.1:{self.port}",
                                        origin=None)
            self.assertEqual(status, 200)
            self.assertTrue(body["local"])

    # --- what a signed-in account is
    def test_the_owner_is_personal_and_the_cookie_is_secure(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        status, body, headers = self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD},
                                          token=False)
        self.assertEqual((status, body["user"]["role"]), (200, "personal"))
        cookie = headers["Set-Cookie"]
        for part in ("Secure", "HttpOnly", "SameSite=Strict"):
            self.assertIn(part, cookie)
        self.assertIn("max-age=", headers["Strict-Transport-Security"])
        status, me, _ = self.call("GET", "/api/me", cookie=cookie.split(";", 1)[0], origin=None)
        self.assertEqual(me["user"]["role"], "personal")
        with patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": ""}):
            self.assertEqual(self.webui._session_cookie("t").count("Secure"), 0)
            self.assertNotIn("Strict-Transport-Security", self.call(
                "GET", "/", host=f"127.0.0.1:{self.port}", origin=None, cookie=cookie.split(";", 1)[0])[2])

    def test_the_machine_is_closed_to_the_owner_too(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        for command in ("run command echo hi", "read file notes.txt", "open app notepad", "scan files .",
                        "screenshot", "email digest", "windows", "download http://example.com/a.zip"):
            with self.subTest(command=command):
                status, body, _ = self.call("POST", "/api/command", {"command": command}, cookie=cookie)
                self.assertFalse(body.get("ok"), (status, body))
                self.assertIn("isn't available on a hosted copy", body["message"])
                self.assertNotIn("developer account", body["message"])   # there is none to ask for
        for path in ("/api/accounts", "/api/agent", "/api/upload", "/api/schedule", "/api/notes"):
            with self.subTest(route=path):
                status, body, headers = self.call("POST", path, {"action": "list"}, cookie=cookie)
                self.assertEqual(status, 403, body)
                self.assertEqual(headers.get("X-Jarvis-Denied"), "role")

    def test_what_it_is_for_still_works(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        status, body, _ = self.call("POST", "/api/command", {"command": "calculate 17*23"}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"], body)
        self.assertIn("391", body["message"])

    # --- the sign-in limit counts people, not the proxy
    def test_the_sign_in_limit_counts_the_forwarded_client(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)

        def attempt(who: str, username: str):
            return self.call("POST", "/auth/login", {"username": username, "password": "wrong password"},
                             token=False, headers={"Fly-Client-IP": who})[0]

        for n in range(5):
            self.assertEqual(attempt("198.51.100.7", f"guess{n}"), 401)
        self.assertEqual(attempt("198.51.100.7", "guess-new"), 429, "the sixth try from one client waits")
        self.assertEqual(attempt("198.51.100.8", "someone-else"), 401, "another client is not locked out with it")

    def test_a_forged_client_address_is_ignored(self) -> None:
        handler = self.webui.Handler.__new__(self.webui.Handler)
        handler.client_address = ("10.1.1.1", 1234)
        for sent, expected in (("198.51.100.7", "198.51.100.7"), ("not an ip", "10.1.1.1"), ("", "10.1.1.1"),
                               ("1.2.3.4, 5.6.7.8", "10.1.1.1")):
            handler.headers = {"Fly-Client-IP": sent}
            with self.subTest(sent=sent):
                self.assertEqual(handler._client(), expected)
        with patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": ""}):
            handler.headers = {"Fly-Client-IP": "198.51.100.7"}
            self.assertEqual(handler._client(), "10.1.1.1", "un-hosted, the header is not believed")


class StartupTests(unittest.TestCase):
    def setUp(self) -> None:
        import laptop_agent.webui as webui

        self.webui = webui

    def problem(self, host="0.0.0.0", passcode="", accounts=False, hosted=True, allowed=(NAME,)):
        return self.webui._exposure_problem(host, passcode, accounts, hosted, frozenset(allowed))

    def test_a_hosted_copy_needs_a_bind_and_a_name_but_not_an_account_or_passcode(self) -> None:
        self.assertIsNone(self.problem())     # the first account is made on the running server
        self.assertIn("bind 0.0.0.0", self.problem(host="127.0.0.1"))
        self.assertIn("LAPTOP_AGENT_ALLOWED_HOSTS", self.problem(allowed=()))

    def test_the_unhosted_rule_is_unchanged(self) -> None:
        self.assertIn("LAPTOP_AGENT_LAN_PASSCODE", self.problem(hosted=False))
        self.assertIsNone(self.problem(hosted=False, passcode="long-enough-code"))
        self.assertIsNone(self.problem(hosted=False, accounts=True))
        self.assertIsNone(self.problem(host="127.0.0.1", hosted=False))

    def test_only_plain_hostnames_are_allowed(self) -> None:
        raw = "Jarvis.Example.com, https://a.example.com, b.example.com:443, *.example.com, localhost, " \
              "10.0.0.1, , c-d.example.org"
        with patch.dict(os.environ, {"LAPTOP_AGENT_ALLOWED_HOSTS": raw}):
            self.assertEqual(self.webui._allowed_hosts(), frozenset({"jarvis.example.com", "c-d.example.org"}))


class AccessTests(unittest.TestCase):
    DEV = Principal("d1", "jeevan", "dev")

    def test_hosted_makes_a_developer_personal(self) -> None:
        with access.acting_as(self.DEV):
            self.assertFalse(access.is_personal())
            self.assertIsNone(access.refused_command("run command echo hi"))
            with patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": "1"}):
                self.assertTrue(access.is_personal())
                self.assertEqual(access.refused_command("run command echo hi"), "run command")

    def test_a_value_that_is_not_yes_is_not_hosted(self) -> None:
        for value in ("", "0", "no", "maybe", "false"):
            with self.subTest(value=value), patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": value}):
                self.assertFalse(access.hosted())
        for value in ("1", "true", "YES", " on "):
            with self.subTest(value=value), patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": value}):
                self.assertTrue(access.hosted())

    def test_the_machines_own_work_is_still_the_owners(self) -> None:
        # No principal: the CLI, the scheduler's ticker. Hosted does not change who runs those.
        with patch.dict(os.environ, {"LAPTOP_AGENT_HOSTED": "1"}):
            self.assertFalse(access.is_personal())


if __name__ == "__main__":
    unittest.main()
