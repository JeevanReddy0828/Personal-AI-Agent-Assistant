"""Sign-in for the web and desktop app, against a live server.

Every test swaps in its own account store, session store and sign-in limiter, so nothing
here writes an account into the data directory the rest of the suite shares: one stray
account would make every other web test meet a sign-in page.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from laptop_agent.accounts import AccountStore
from laptop_agent.sessions import SessionStore

CHEAP = (2 ** 10, 8, 1)
GOOD = "correct horse battery"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class SignInTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import laptop_agent.webui as webui

        cls.webui = webui
        cls.server = ThreadingHTTPServer((webui.HOST, 0), webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://{webui.HOST}:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.accounts = AccountStore(Path(tmp.name) / "accounts.json", cost=CHEAP)
        self.sessions = SessionStore(Path(tmp.name) / "sessions.json")
        self.clock = _Clock()
        for name, value in (("ACCOUNTS", self.accounts), ("SESSIONS", self.sessions),
                            ("_SIGNIN_LIMIT", self.webui._SignInLimit(clock=self.clock))):
            patcher = patch.object(self.webui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, method: str, path: str, body: dict | None = None, cookie: str = "",
             token: bool = True, headers: dict[str, str] | None = None):
        """(status, parsed body, response headers). Never raises on a 4xx."""
        sent = {"Content-Type": "application/json", **(headers or {})}
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

    # --- work already running when its session ends
    def test_work_already_running_stops_when_its_session_ends(self) -> None:
        """Revoking ended the session but not what it had started. Every command a request
        goes on to dispatch now asks again whether that request's own session stands."""
        from laptop_agent import access

        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        seen: list[str] = []

        async def handle(command, history=None, on_token=None, **kwargs):
            seen.append(access.current().username)
            access.ensure_signed_in()
            self.sessions.revoke(cookie.split("=", 1)[1])   # signed out from another device
            access.ensure_signed_in()
            seen.append("a second command ran")

        with patch.object(self.webui._orchestrator, "handle", handle):
            status, body, _ = self.call("POST", "/api/command", {"command": "agent run tidy up"}, cookie=cookie)
        self.assertEqual((status, body["ok"]), (401, False), body)
        self.assertIn("session ended", body["message"])
        self.assertEqual(seen, ["jeevan"])

    # --- no accounts: exactly as before
    def test_without_accounts_nothing_asks_for_a_sign_in(self) -> None:
        status, page, _ = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(self.webui._API_TOKEN, page)
        self.assertEqual(self.call("GET", "/api/me")[1],
                         {"ok": True, "accounts": False, "local": True, "user": None})
        self.assertEqual(self.call("POST", "/auth/login", {"username": "x", "password": GOOD}, token=False)[0], 409)

    # --- setting it up
    def test_set_up_creates_the_owner_signed_in_as_dev(self) -> None:
        status, body, headers = self.call("POST", "/auth/bootstrap", {"username": "Jeevan", "password": GOOD})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["user"], {"username": "jeevan", "role": "dev"})
        cookie = headers["Set-Cookie"]
        for attribute in ("jarvis_session=", "HttpOnly", "SameSite=Strict", "Path=/", "Max-Age="):
            self.assertIn(attribute, cookie)
        self.assertEqual(self.call("GET", "/api/me", cookie=cookie.split(";", 1)[0])[1]["user"],
                         {"id": self.accounts.find("jeevan").id, "username": "jeevan", "role": "dev"})
        again = self.call("POST", "/auth/bootstrap", {"username": "someone", "password": GOOD},
                          cookie=cookie.split(";", 1)[0])
        self.assertEqual(again[0], 400)
        self.assertIn("already set up", again[1]["message"])
        self.assertEqual([account.username for account in self.accounts.list()], ["jeevan"])

    def test_set_up_needs_the_page_token_and_this_machine(self) -> None:
        self.assertEqual(self.call("POST", "/auth/bootstrap", {"username": "j", "password": GOOD}, token=False)[0], 403)
        with patch.object(self.webui.Handler, "_client_is_local", lambda self: False):
            status, body, _ = self.call("POST", "/auth/bootstrap", {"username": "j", "password": GOOD})
        self.assertEqual(status, 403, body)
        self.assertFalse(self.accounts.exists())

    def test_set_up_refuses_a_weak_password_and_creates_nothing(self) -> None:
        status, body, _ = self.call("POST", "/auth/bootstrap", {"username": "jeevan", "password": "short"})
        self.assertEqual(status, 400)
        self.assertIn("password", body["message"])
        self.assertFalse(self.accounts.exists())

    # --- signed out, once accounts exist
    def test_signed_out_gets_the_sign_in_page_and_never_the_token(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        for path in ("/", "/?app=1", "/index.html"):
            with self.subTest(path):
                status, page, headers = self.call("GET", path, token=False)
                self.assertEqual(status, 401)
                self.assertIn('id="u"', page)
                self.assertNotIn(self.webui._API_TOKEN, page)
                self.assertNotIn("J.A.R.V.I.S", page, "the sign-in page must not say what it guards")
                self.assertIn(f'nonce="{self.webui._SCRIPT_NONCE}"', page)
                self.assertIn("no-store", headers.get("Cache-Control", ""))
        for method, path in (("GET", "/api/health"), ("GET", "/api/reminders"), ("POST", "/api/command")):
            with self.subTest(path):
                status, body, _ = self.call(method, path, {"command": "time"} if method == "POST" else None)
                self.assertEqual(status, 401)
                self.assertTrue(body["signin"])

    def test_sign_in_opens_the_app(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in("JEEVAN")
        status, page, _ = self.call("GET", "/", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(self.webui._API_TOKEN, page)
        self.assertEqual(self.call("GET", "/api/health", cookie=cookie)[0], 200)

    def test_a_wrong_password_and_an_unknown_user_look_the_same(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        wrong = self.call("POST", "/auth/login", {"username": "jeevan", "password": "not it at all"}, token=False)
        unknown = self.call("POST", "/auth/login", {"username": "nobody", "password": GOOD}, token=False)
        self.assertEqual((wrong[0], wrong[1]), (unknown[0], unknown[1]))
        self.assertEqual(wrong[0], 401)
        self.assertNotIn("Set-Cookie", wrong[2])

    def test_a_sign_in_from_another_site_is_refused(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        for headers in ({"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers):
                status, _, response = self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD},
                                                token=False, headers=headers)
                self.assertEqual(status, 403)
                self.assertNotIn("Set-Cookie", response)

    def test_an_oversized_sign_in_body_is_refused_unread(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        status, _, _ = self.call("POST", "/auth/login", {"username": "jeevan", "password": "x" * 5000}, token=False)
        self.assertEqual(status, 400)

    def test_repeated_failures_wait_then_recover(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        for _ in range(5):
            self.assertEqual(self.call("POST", "/auth/login", {"username": "jeevan", "password": "wrong guess"},
                                       token=False)[0], 401)
        status, body, _ = self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD}, token=False)
        self.assertEqual(status, 429, "the right password must also wait while locked")
        self.assertIn("30 seconds", body["message"])
        self.clock.now += 31
        self.sign_in()

    def hashing_taken(self):
        """Every hashing slot held, and a short wait for one: the state a burst leaves."""
        import contextlib

        import laptop_agent.accounts as accounts

        @contextlib.contextmanager
        def held():
            for _ in range(accounts.HASH_SLOTS):
                accounts._hashing.acquire()
            try:
                with patch.object(accounts, "HASH_WAIT", 0.05):
                    yield
            finally:
                for _ in range(accounts.HASH_SLOTS):
                    accounts._hashing.release()
        return held()

    def test_a_sign_in_that_cannot_get_a_turn_is_told_to_retry_not_refused(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        with self.hashing_taken():
            for _ in range(6):
                status, body, headers = self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD},
                                                  token=False)
                self.assertEqual((status, headers.get("Retry-After")), (503, "5"), body)
        self.sign_in()   # six busy answers were not six failures, so there is nothing to wait out

    def test_setting_up_or_changing_a_password_while_busy_changes_nothing(self) -> None:
        with self.hashing_taken():
            self.assertEqual(self.call("POST", "/auth/bootstrap", {"username": "jeevan", "password": GOOD})[0], 503)
        self.assertFalse(self.accounts.exists())
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        with self.hashing_taken():
            status, _, _ = self.call("POST", "/auth/password", {"current": GOOD, "new": "a brand new password"},
                                     cookie=cookie)
        self.assertEqual(status, 503)
        self.sign_in(password=GOOD)

    # --- damaged storage fails closed (Codex's review of #138)
    def test_damaged_account_storage_refuses_everyone(self) -> None:
        # Read as "no accounts", a damaged file switched sign-in off and served the app, and its
        # API token, to anyone. A valid backup beside it must not be used either: it can hold a
        # deleted account, an old password or an old role.
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        good = self.accounts.path.read_text(encoding="utf-8")
        Path(str(self.accounts.path) + ".bak").write_text(good, encoding="utf-8")
        for damage in ("{broken", '{"accounts": "x"}', '{"accounts": [{"id": 1}]}',
                       '{"accounts": [{"id": "a", "username": "x", "role": "admin"}]}',
                       '{"accounts": [{"id": "a", "username": "x", "role": "dev", "disabled": "no"}]}', "[]"):
            with self.subTest(damage):
                self.accounts.path.write_text(damage, encoding="utf-8")
                status, page, _ = self.call("GET", "/", token=False)
                self.assertEqual(status, 503)
                self.assertNotIn(self.webui._API_TOKEN, str(page))
                self.assertEqual(self.call("GET", "/api/me", cookie=cookie)[0], 503)
                self.assertEqual(self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD},
                                           token=False)[0], 503)
        self.accounts.path.write_text(good, encoding="utf-8")
        self.assertEqual(self.call("GET", "/api/me", cookie=cookie)[0], 200, "repairing the file did not recover")

    def test_a_damaged_session_store_signs_everyone_out_and_never_revives_a_revoked_one(self) -> None:
        owner = self.accounts.create("jeevan", "dev", GOOD)
        kept, revoked = self.sessions.create(owner.id, "password"), self.sessions.create(owner.id, "password")
        before_revoke = self.sessions.path.read_text(encoding="utf-8")
        self.sessions.revoke(revoked)
        # The backup a generic reader would fall back to still lists the revoked session.
        Path(str(self.sessions.path) + ".bak").write_text(before_revoke, encoding="utf-8")
        self.sessions.path.write_text("{broken", encoding="utf-8")
        for token in (revoked, kept):
            self.assertEqual(self.call("GET", "/api/me", cookie=f"jarvis_session={token}")[0], 401)
        self.sign_in()

    def test_neither_store_keeps_a_backup(self) -> None:
        for store in (self.accounts, self.sessions):
            Path(str(store.path) + ".bak").write_text("{}", encoding="utf-8")
        owner = self.accounts.create("jeevan", "dev", GOOD)
        self.sessions.create(owner.id, "password")
        self.accounts.set_role(owner.id, "dev")
        for store in (self.accounts, self.sessions):
            self.assertFalse(Path(str(store.path) + ".bak").exists(), f"{store.path.name} kept a backup")

    def test_sign_out_ends_the_session_everywhere_it_is_used(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        status, _, headers = self.call("POST", "/auth/logout", {}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.call("GET", "/", cookie=cookie)[0], 401)

    def test_signing_in_again_replaces_the_old_session(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        first = self.sign_in()
        status, _, headers = self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD},
                                       token=False, headers={"Cookie": first})
        self.assertEqual(status, 200)
        second = headers["Set-Cookie"].split(";", 1)[0]
        self.assertNotEqual(first, second)
        self.assertEqual(self.call("GET", "/api/me", cookie=first)[0], 401)
        self.assertEqual(self.call("GET", "/api/me", cookie=second)[0], 200)

    def test_a_disabled_account_is_signed_out_at_its_next_request(self) -> None:
        account = self.accounts.create("jeevan", "dev", GOOD)
        cookie = self.sign_in()
        self.accounts.set_disabled(account.id, True)
        self.assertEqual(self.call("GET", "/api/me", cookie=cookie)[0], 401)

    def test_changing_a_password(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        here, elsewhere = self.sign_in(), self.sign_in()
        wrong = self.call("POST", "/auth/password", {"current": "not it", "new": "a brand new phrase"}, cookie=here)
        self.assertEqual(wrong[0], 403)
        status, body, _ = self.call("POST", "/auth/password", {"current": GOOD, "new": "a brand new phrase"},
                                    cookie=here)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.call("GET", "/api/me", cookie=here)[0], 200)
        self.assertEqual(self.call("GET", "/api/me", cookie=elsewhere)[0], 401)
        self.assertEqual(self.call("POST", "/auth/login", {"username": "jeevan", "password": GOOD}, token=False)[0], 401)
        # A sign-in after the change must give a session that works, not only a 200.
        fresh = self.sign_in(password="a brand new phrase")
        self.assertEqual(self.call("GET", "/api/health", cookie=fresh)[0], 200)

    def test_developer_routes_need_a_developer(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        self.accounts.create("family", "personal", GOOD)
        dev, personal = self.sign_in("jeevan"), self.sign_in("family")
        for path in ("/api/traces", "/api/failures", "/api/agent-runs"):
            with self.subTest(path):
                self.assertEqual(self.call("GET", path, cookie=dev)[0], 200)
                status, body, headers = self.call("GET", path, cookie=personal)
                self.assertEqual(status, 403)
                self.assertIn("developer", body["message"])
                # The page reloads on a bare 403 (a stale token); this marks a final refusal.
                self.assertEqual(headers["X-Jarvis-Denied"], "role")
        self.assertEqual(self.call("POST", "/api/agent", {"goal": "list files"}, cookie=personal)[0], 403)
        self.assertEqual(self.call("GET", "/api/health", cookie=personal)[0], 200)

    def test_a_role_change_applies_to_the_next_request(self) -> None:
        account = self.accounts.create("family", "personal", GOOD)
        cookie = self.sign_in("family")
        self.assertEqual(self.call("GET", "/api/traces", cookie=cookie)[0], 403)
        self.accounts.set_role(account.id, "dev")
        self.assertEqual(self.call("GET", "/api/traces", cookie=cookie)[0], 200)

    # --- managing accounts, from this computer
    def manage(self, cookie: str, current: str = GOOD, **body):
        return self.call("POST", "/api/accounts", {**body, "current": current}, cookie=cookie)

    def test_a_developer_adds_a_personal_account_that_can_sign_in(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        owner = self.sign_in()
        status, body, _ = self.manage(owner, action="create", username="Family", role="personal",
                                      password="another good one")
        self.assertEqual(status, 200, body)
        self.assertEqual([(a["username"], a["role"]) for a in body["accounts"]],
                         [("jeevan", "dev"), ("family", "personal")])
        self.assertNotIn("password_hash", json.dumps(body))
        self.sign_in("family", "another good one")
        listed = self.call("GET", "/api/accounts", cookie=owner)[1]
        self.assertEqual(len(listed["accounts"]), 2)

    def test_every_change_asks_for_the_developers_own_password(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        owner = self.sign_in()
        for current in ("", "not my password"):
            status, _, headers = self.manage(owner, current=current, action="create", username="x",
                                             role="dev", password="a good password")
            self.assertEqual((status, headers.get("X-Jarvis-Denied")), (403, "password"))
        self.assertIsNone(self.accounts.find("x"), "a session left signed in minted an account")

    def test_wrong_passwords_here_count_toward_the_same_wait(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        owner = self.sign_in()
        for _ in range(5):
            self.manage(owner, current="wrong guess", action="create", username="x", role="dev", password="p" * 8)
        self.assertEqual(self.manage(owner, action="create", username="x", role="dev", password="p" * 8)[0], 429)

    def test_only_a_developer_on_this_computer_manages_accounts(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        self.accounts.create("family", "personal", GOOD)
        family = self.sign_in("family")
        for method, body in (("GET", None), ("POST", {"action": "create", "username": "x", "role": "dev",
                                                       "password": "p" * 8, "current": GOOD})):
            status, _, headers = self.call(method, "/api/accounts", body, cookie=family)
            self.assertEqual((status, headers.get("X-Jarvis-Denied")), (403, "role"))
        owner = self.sign_in()
        with patch.object(self.webui.Handler, "_client_is_local", lambda handler: False):
            status, _, headers = self.call("GET", "/api/accounts", cookie=owner)
        self.assertEqual((status, headers.get("X-Jarvis-Denied")), (403, "local"))

    def test_nobody_demotes_disables_or_deletes_themselves_here(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        owner = self.sign_in()
        for action, extra in (("role", {"role": "personal"}), ("disable", {}), ("delete", {}),
                              ("password", {"password": "a brand new one"})):
            with self.subTest(action):
                status, body, _ = self.manage(owner, action=action, username="jeevan", **extra)
                self.assertEqual(status, 400, body)
        account = self.accounts.find("jeevan")
        self.assertEqual((account.role, account.disabled), ("dev", False))
        self.sign_in()

    def test_disabling_resetting_or_deleting_signs_that_account_out(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        owner = self.sign_in()
        for name, action, extra in (("amy", "disable", {}), ("ben", "password", {"password": "a brand new one"}),
                                    ("cat", "delete", {})):
            with self.subTest(action):
                self.accounts.create(name, "personal", GOOD)
                theirs = self.sign_in(name)
                self.assertEqual(self.call("GET", "/api/me", cookie=theirs)[1]["user"]["username"], name)
                status, body, _ = self.manage(owner, action=action, username=name, **extra)
                self.assertEqual(status, 200, body)
                self.assertEqual(self.call("GET", "/api/me", cookie=theirs)[0], 401, "still signed in")
        self.sign_in("ben", "a brand new one")
        self.assertIsNone(self.accounts.find("cat"))

    def test_enabling_an_account_again_does_not_bring_its_old_sessions_back(self) -> None:
        # A disabled account is refused on its next request anyway; what revoking adds is that
        # a cookie taken before the disable stays dead once the account is enabled again.
        self.accounts.create("jeevan", "dev", GOOD)
        self.accounts.create("amy", "personal", GOOD)
        owner, theirs = self.sign_in(), self.sign_in("amy")
        self.assertEqual(self.manage(owner, action="disable", username="amy")[0], 200)
        self.assertEqual(self.manage(owner, action="enable", username="amy")[0], 200)
        self.assertEqual(self.call("GET", "/api/me", cookie=theirs)[0], 401, "an old session came back to life")
        self.sign_in("amy")

    def test_a_sign_in_checked_before_a_reset_or_disable_does_not_outlive_it(self) -> None:
        # The review of #142, reproduced at the real hash cost: a sign-in whose password was
        # checked just before a developer reset it (or disabled the account) finished its hash
        # after the revoke and minted a live session, which also survived disable-then-enable.
        # Replayed here without timing: the check answers from the account as it was.
        self.accounts.create("jeevan", "dev", GOOD)
        self.accounts.create("amy", "personal", GOOD)
        owner = self.sign_in()
        for action, extra in (("password", {"password": "a brand new phrase"}), ("disable", {})):
            with self.subTest(action):
                as_it_was = self.accounts.find("amy")
                self.assertEqual(self.manage(owner, action=action, username="amy", **extra)[0], 200)
                with patch.object(self.accounts, "authenticate", return_value=as_it_was):
                    late = self.sign_in("amy")
                if action == "disable":
                    self.assertEqual(self.manage(owner, action="enable", username="amy")[0], 200)
                self.assertEqual(self.call("GET", "/api/me", cookie=late)[1].get("user"), None)
                self.assertEqual(self.call("GET", "/api/health", cookie=late)[0], 401, "a late session is live")
                # ...while signing in now, after the change, works as it should.
                now = self.sign_in("amy", "a brand new phrase")
                self.assertEqual(self.call("GET", "/api/health", cookie=now)[0], 200)

    def test_changes_are_audited_with_who_made_them(self) -> None:
        self.accounts.create("jeevan", "dev", GOOD)
        owner = self.sign_in()
        self.manage(owner, action="create", username="family", role="personal", password="p" * 8)
        self.manage(owner, action="role", username="family", role="dev")
        events = [(event.get("event_type"), (event.get("payload") or {}).get("by"))
                  for event in self.webui._orchestrator.context.audit.tail(20)]
        self.assertIn(("account_created", "jeevan"), events)
        self.assertIn(("account_role", "jeevan"), events)


class NetworkAccessTests(unittest.TestCase):
    def test_an_account_stands_in_for_the_passcode(self) -> None:
        """A network bind was refused without LAPTOP_AGENT_LAN_PASSCODE. An account guards
        every request at least as well, so with one the passcode is no longer required;
        with neither, the refusal stands (test_lan_access covers that)."""
        import importlib
        import os

        import laptop_agent.webui as webui

        keys = ("LAPTOP_AGENT_HOST", "LAPTOP_AGENT_LAN_PASSCODE", "LAPTOP_AGENT_DATA_DIR")
        saved = {key: os.environ.get(key) for key in keys}
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        AccountStore(Path(tmp.name) / "accounts.json", cost=CHEAP).create("jeevan", "dev", GOOD)
        try:
            os.environ.update({"LAPTOP_AGENT_HOST": "0.0.0.0", "LAPTOP_AGENT_DATA_DIR": tmp.name})
            os.environ.pop("LAPTOP_AGENT_LAN_PASSCODE", None)
            reloaded = importlib.reload(webui)
            self.assertTrue(reloaded.LAN_MODE)
            self.assertTrue(reloaded.ACCOUNTS.exists())
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            importlib.reload(webui)


class SignInLimitTests(unittest.TestCase):
    def test_backoff_doubles_to_a_ceiling_and_a_success_clears_it(self) -> None:
        import laptop_agent.webui as webui

        clock = _Clock()
        limit = webui._SignInLimit(clock=clock)
        for _ in range(4):
            limit.fail("k")
        self.assertEqual(limit.wait("k"), 0)
        limit.fail("k")
        self.assertEqual(limit.wait("k"), 30)
        for waited in (60, 120, 240, 480, 900, 900):
            limit.fail("k")
            self.assertEqual(limit.wait("k"), waited)
        clock.now += 899.5
        self.assertEqual(limit.wait("k"), 1, "a part-second left still reads as a second")
        limit.clear("k")
        self.assertEqual(limit.wait("k"), 0)

    def test_the_table_is_bounded(self) -> None:
        import laptop_agent.webui as webui

        limit = webui._SignInLimit(clock=_Clock())
        for index in range(webui._SignInLimit.TRACKED + 50):
            limit.fail(f"user:lan:made-up-{index}")
        self.assertEqual(len(limit._failures), webui._SignInLimit.TRACKED)


if __name__ == "__main__":
    unittest.main()
