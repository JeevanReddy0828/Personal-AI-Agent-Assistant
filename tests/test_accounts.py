"""Accounts and sessions: the two stores sign-in rests on.

Hashing uses a cheap scrypt cost here so the suite stays fast, except for one test that
runs the real one: hashlib refuses N=2^17 under its default memory limit, and a store that
cannot hash its own parameters would fail every real sign-in while every cheap test passed.
"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import laptop_agent.accounts as accounts_module
from laptop_agent.accounts import (
    SCRYPT_COST, AccountError, AccountStore, HashingBusy, hash_password, main, verify_password,
)
from laptop_agent.storage import StorageDamaged
from laptop_agent.sessions import ABSOLUTE_SECONDS, IDLE_SECONDS, SessionStore

CHEAP = (2 ** 10, 8, 1)
GOOD = "correct horse battery"


class PasswordHashTests(unittest.TestCase):
    def test_the_real_cost_hashes_and_verifies(self) -> None:
        stored = hash_password(GOOD, SCRYPT_COST)
        self.assertTrue(stored.startswith(f"scrypt${SCRYPT_COST[0]}$8$1$"))
        self.assertTrue(verify_password(GOOD, stored))

    def test_a_wrong_or_malformed_input_is_refused(self) -> None:
        stored = hash_password(GOOD, CHEAP)
        self.assertFalse(verify_password("correct horse batterY", stored))
        for broken in ("", "scrypt$", "md5$1$2$3$x$y", stored.replace("scrypt", "bcrypt"), stored[:-4]):
            with self.subTest(broken):
                self.assertFalse(verify_password(GOOD, broken))

    def test_the_same_password_hashes_differently(self) -> None:
        self.assertNotEqual(hash_password(GOOD, CHEAP), hash_password(GOOD, CHEAP))

    def test_no_more_than_two_hash_at_once(self) -> None:
        # A real hash holds 128 MiB, and the web server runs a thread per request.
        running, peak, lock = 0, 0, threading.Lock()
        real = accounts_module.hashlib.scrypt

        def counted(*args, **kwargs):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            try:
                time.sleep(0.05)
                return real(*args, **kwargs)
            finally:
                with lock:
                    running -= 1

        with patch.object(accounts_module.hashlib, "scrypt", counted):
            threads = [threading.Thread(target=hash_password, args=(GOOD, CHEAP)) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.assertEqual(peak, accounts_module.HASH_SLOTS)

    def test_a_hash_that_cannot_get_a_turn_says_busy_and_decides_nothing(self) -> None:
        stored = hash_password(GOOD, CHEAP)
        for _ in range(accounts_module.HASH_SLOTS):
            accounts_module._hashing.acquire()
        try:
            with patch.object(accounts_module, "HASH_WAIT", 0.05):
                with self.assertRaises(HashingBusy):   # never False: nothing was checked
                    verify_password(GOOD, stored)
        finally:
            for _ in range(accounts_module.HASH_SLOTS):
                accounts_module._hashing.release()
        self.assertTrue(verify_password(GOOD, stored))


class AccountStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AccountStore(Path(self.tmp.name) / "accounts.json", cost=CHEAP)

    def test_no_accounts_means_sign_in_is_off(self) -> None:
        self.assertFalse(self.store.exists())
        self.store.create("Jeevan", "dev", GOOD)
        self.assertTrue(self.store.exists())

    def test_usernames_are_case_insensitive_and_unique(self) -> None:
        account = self.store.create("  Jeevan ", "dev", GOOD)
        self.assertEqual(account.username, "jeevan")
        with self.assertRaises(AccountError):
            self.store.create("JEEVAN", "personal", GOOD)
        self.assertEqual(self.store.find("JeEvAn").id, account.id)

    def test_rules_refuse_bad_input_with_a_readable_reason(self) -> None:
        for username, role, password in (("", "dev", GOOD), ("has space", "dev", GOOD), ("x" * 33, "dev", GOOD),
                                         ("-dash", "dev", GOOD), ("ok", "admin", GOOD), ("ok", "dev", "short"),
                                         ("ok", "dev", "p" * 257)):
            with self.subTest(username=username, role=role, password=len(password)):
                with self.assertRaises(AccountError):
                    self.store.create(username, role, password)
        self.assertFalse(self.store.exists())

    def test_the_web_can_never_leave_the_app_without_a_developer(self) -> None:
        owner = self.store.create("jeevan", "dev", GOOD)
        for change in (lambda: self.store.set_role(owner.id, "personal", keep_developer=True),
                       lambda: self.store.set_disabled(owner.id, True, keep_developer=True),
                       lambda: self.store.delete(owner.id, keep_developer=True)):
            with self.assertRaises(AccountError):
                change()
        self.assertEqual((self.store.get(owner.id).role, self.store.get(owner.id).disabled), ("dev", False))
        second = self.store.create("sam", "dev", GOOD)
        self.store.set_role(second.id, "personal", keep_developer=True)   # one developer is still left
        with self.assertRaises(AccountError):
            self.store.set_role(owner.id, "personal", keep_developer=True)

    def test_the_command_line_can_still_do_anything(self) -> None:
        # It is the way back in, so it is never told no by this rule.
        owner = self.store.create("jeevan", "dev", GOOD)
        self.store.set_role(owner.id, "personal")
        self.assertEqual(self.store.get(owner.id).role, "personal")

    def test_only_a_missing_file_means_no_accounts(self) -> None:
        self.assertFalse(self.store.exists())
        self.store.create("jeevan", "dev", GOOD)
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(StorageDamaged):
            self.store.exists()
        self.store.path.unlink()
        self.assertFalse(self.store.exists())

    def test_the_command_line_says_how_to_recover_damaged_storage(self) -> None:
        self.store.path.write_text("{broken", encoding="utf-8")
        out = io.StringIO()
        sessions = SessionStore(Path(self.tmp.name) / "sessions.json")
        self.assertEqual(main(["list"], self.store, sessions, ask=lambda _: GOOD, out=out), 1)
        self.assertIn("create the first account again", out.getvalue())

    def test_authenticate(self) -> None:
        account = self.store.create("jeevan", "dev", GOOD)
        self.assertEqual(self.store.authenticate("Jeevan", GOOD).id, account.id)
        self.assertIsNone(self.store.authenticate("jeevan", "wrong password"))
        self.assertIsNone(self.store.authenticate("nobody", GOOD))
        self.store.set_disabled(account.id, True)
        self.assertIsNone(self.store.authenticate("jeevan", GOOD))

    def test_every_refusal_runs_one_hash(self) -> None:
        """How long a refusal takes must not say whether the username exists."""
        self.store.create("jeevan", "dev", GOOD)
        google_only = self.store.create("guest", "personal")
        self.store.link_google(google_only.id, "sub-1", "guest@example.com")
        disabled = self.store.create("old", "personal", GOOD)
        self.store.set_disabled(disabled.id, True)
        for username in ("nobody", "guest", "old", "jeevan"):
            with self.subTest(username):
                with patch.object(accounts_module, "verify_password", wraps=verify_password) as spy:
                    self.store.authenticate(username, "wrong password")
                self.assertEqual(spy.call_count, 1)

    def test_changes(self) -> None:
        account = self.store.create("jeevan", "personal", GOOD)
        self.store.set_password(account.id, "a different phrase")
        self.assertIsNone(self.store.authenticate("jeevan", GOOD))
        self.assertIsNotNone(self.store.authenticate("jeevan", "a different phrase"))
        self.assertEqual(self.store.set_role(account.id, "dev").role, "dev")
        with self.assertRaises(AccountError):
            self.store.set_role(account.id, "root")
        self.store.delete(account.id)
        self.assertFalse(self.store.exists())
        with self.assertRaises(AccountError):
            self.store.delete(account.id)

    def test_google_links_one_account_per_google_identity(self) -> None:
        first = self.store.create("jeevan", "dev", GOOD)
        second = self.store.create("guest", "personal", GOOD)
        self.store.link_google(first.id, "sub-1", "j@example.com")
        self.assertEqual(self.store.find_by_google_sub("sub-1").id, first.id)
        self.assertIsNone(self.store.find_by_google_sub(""))
        with self.assertRaises(AccountError):
            self.store.link_google(second.id, "sub-1", "j@example.com")

    def test_unlinking_google_never_strands_an_account(self) -> None:
        google_only = self.store.create("guest", "personal")
        self.store.link_google(google_only.id, "sub-2", "g@example.com")
        with self.assertRaises(AccountError):
            self.store.unlink_google(google_only.id)
        self.store.set_password(google_only.id, GOOD)
        self.assertIsNone(self.store.unlink_google(google_only.id).google_sub)

    def test_what_leaves_the_process_never_carries_the_hash(self) -> None:
        account = self.store.create("jeevan", "dev", GOOD)
        public = account.public()
        self.assertNotIn("password_hash", public)
        self.assertNotIn(account.password_hash, json.dumps(public))
        self.assertTrue(public["has_password"])


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "sessions.json"
        self.now = [1_800_000_000.0]
        self.sessions = SessionStore(self.path, clock=lambda: self.now[0])

    def test_a_token_resolves_and_is_never_stored(self) -> None:
        token = self.sessions.create("acct-1", "password")
        self.assertEqual(self.sessions.resolve(token).account_id, "acct-1")
        self.assertNotIn(token, self.path.read_text(encoding="utf-8"))
        self.assertIsNone(self.sessions.resolve("not-a-token"))
        self.assertIsNone(self.sessions.resolve(""))

    def test_a_restart_keeps_the_session(self) -> None:
        token = self.sessions.create("acct-1", "password")
        reopened = SessionStore(self.path, clock=lambda: self.now[0])
        self.assertEqual(reopened.resolve(token).account_id, "acct-1")

    def test_idle_expiry(self) -> None:
        token = self.sessions.create("acct-1", "password")
        self.now[0] += IDLE_SECONDS - 1
        self.assertIsNotNone(self.sessions.resolve(token))
        self.now[0] += IDLE_SECONDS
        self.assertIsNone(self.sessions.resolve(token))

    def test_absolute_expiry_even_when_used_every_day(self) -> None:
        token = self.sessions.create("acct-1", "password")
        for _day in range(ABSOLUTE_SECONDS // 86400 - 1):
            self.now[0] += 86400
            self.assertIsNotNone(self.sessions.resolve(token))
        self.now[0] += 2 * 86400
        self.assertIsNone(self.sessions.resolve(token))

    def test_seen_is_written_at_most_once_a_minute(self) -> None:
        token = self.sessions.create("acct-1", "password")
        before = self.path.stat().st_mtime_ns, self.path.read_text(encoding="utf-8")
        self.now[0] += 30
        self.sessions.resolve(token)
        self.assertEqual((self.path.stat().st_mtime_ns, self.path.read_text(encoding="utf-8")), before)
        self.now[0] += 60
        self.sessions.resolve(token)
        self.assertNotEqual(self.path.read_text(encoding="utf-8"), before[1])

    def test_revoke_and_revoke_account(self) -> None:
        mine = self.sessions.create("acct-1", "password")
        other_device = self.sessions.create("acct-1", "google")
        theirs = self.sessions.create("acct-2", "password")
        self.sessions.revoke(mine)
        self.assertIsNone(self.sessions.resolve(mine))
        kept = self.sessions.create("acct-1", "password")
        self.assertEqual(self.sessions.revoke_account("acct-1", keep=kept), 1)
        self.assertIsNone(self.sessions.resolve(other_device))
        self.assertIsNotNone(self.sessions.resolve(kept))
        self.assertIsNotNone(self.sessions.resolve(theirs))

    def test_a_revocation_from_another_process_takes_effect_at_once(self) -> None:
        """The CLI revokes from its own process; the running server must not keep serving."""
        token = self.sessions.create("acct-1", "password")
        self.assertIsNotNone(self.sessions.resolve(token))
        SessionStore(self.path, clock=lambda: self.now[0]).revoke_account("acct-1")
        self.assertIsNone(self.sessions.resolve(token))


class CommandLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AccountStore(Path(self.tmp.name) / "accounts.json", cost=CHEAP)
        self.sessions = SessionStore(Path(self.tmp.name) / "sessions.json")

    def run_cli(self, *argv: str, answers: tuple[str, ...] = ()) -> tuple[int, str]:
        replies = iter(answers)
        out = io.StringIO()
        code = main(list(argv), store=self.store, sessions=self.sessions, ask=lambda prompt: next(replies), out=out)
        return code, out.getvalue()

    def test_create_list_and_recover(self) -> None:
        code, text = self.run_cli("create", "Jeevan", "--role", "dev", answers=(GOOD, GOOD))
        self.assertEqual(code, 0, text)
        self.assertIn("now asks for a sign-in", text)
        self.assertIn("jeevan", self.run_cli("list")[1])
        token = self.sessions.create(self.store.find("jeevan").id, "password")
        code, text = self.run_cli("password", "jeevan", answers=("a new passphrase", "a new passphrase"))
        self.assertEqual(code, 0, text)
        self.assertIsNone(self.sessions.resolve(token), "a password change must sign old sessions out")
        self.assertIsNotNone(self.store.authenticate("jeevan", "a new passphrase"))

    def test_mismatched_passwords_create_nothing(self) -> None:
        code, text = self.run_cli("create", "jeevan", "--role", "dev", answers=(GOOD, GOOD + "!"))
        self.assertEqual(code, 1)
        self.assertIn("differ", text)
        self.assertFalse(self.store.exists())

    def test_disable_and_delete_sign_the_account_out(self) -> None:
        account = self.store.create("guest", "personal", GOOD)
        token = self.sessions.create(account.id, "password")
        self.assertEqual(self.run_cli("disable", "guest")[0], 0)
        self.assertIsNone(self.sessions.resolve(token))
        self.assertIsNone(self.store.authenticate("guest", GOOD))
        self.assertEqual(self.run_cli("enable", "guest")[0], 0)
        self.assertIsNotNone(self.store.authenticate("guest", GOOD))
        code, text = self.run_cli("delete", "guest")
        self.assertEqual(code, 0)
        self.assertIn("no longer asks", text)

    def test_role_change_and_an_unknown_account(self) -> None:
        self.store.create("guest", "personal", GOOD)
        self.assertEqual(self.run_cli("role", "guest", "dev")[0], 0)
        self.assertEqual(self.store.find("guest").role, "dev")
        code, text = self.run_cli("disable", "nobody")
        self.assertEqual(code, 1)
        self.assertIn("No account called nobody", text)


if __name__ == "__main__":
    unittest.main()
