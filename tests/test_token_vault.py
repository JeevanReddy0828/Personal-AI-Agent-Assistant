from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from laptop_agent.token_vault import TokenVault, TokenVaultError


class TokenVaultTests(unittest.TestCase):
    """A credential store must not keep, or come back from, an older copy of itself (Codex's
    review of #160): `forget` removed a token from the vault while its `.bak` kept it, and a
    damaged vault was read back from that `.bak`, restoring the token the user had removed."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "email_tokens.json"
        # DPAPI exists only on Windows; a reversible stand-in keeps the test on every platform.
        for name, value in (("is_available", lambda self: True),
                            ("_encrypt", staticmethod(lambda data: data[::-1])),
                            ("_decrypt", staticmethod(lambda data: data[::-1]))):
            patcher = patch.object(TokenVault, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.vault = TokenVault(self.path)

    def test_a_forgotten_token_is_left_nowhere(self) -> None:
        self.vault.store("gmail", {"access_token": "gmail-secret"})
        self.vault.store("outlook", {"access_token": "outlook-secret"})
        self.assertTrue(self.vault.forget("gmail"))
        self.assertIsNone(self.vault.load("gmail"))
        self.assertEqual(self.vault.load("outlook"), {"access_token": "outlook-secret"})
        self.assertEqual(sorted(path.name for path in self.dir.iterdir() if not path.name.endswith(".lock")),
                         ["email_tokens.json"])

    def test_a_damaged_vault_is_never_read_back_from_an_older_copy(self) -> None:
        self.vault.store("gmail", {"access_token": "gmail-secret"})
        stale = Path(str(self.path) + ".bak")
        stale.write_bytes(self.path.read_bytes())   # a backup left by an earlier version
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(TokenVaultError):
            self.vault.load("gmail")
        self.assertEqual(self.vault.status()["providers"], [])
        self.assertTrue(self.vault.status()["damaged"])

    def test_a_damaged_vault_gives_way_to_a_fresh_token_or_a_forget(self) -> None:
        self.path.write_text("{ not json", encoding="utf-8")
        with patch("laptop_agent.token_vault.record_failure") as recorded:
            info = self.vault.store("gmail", {"access_token": "fresh"})
        self.assertEqual(info.provider, "gmail")
        self.assertEqual(self.vault.load("gmail"), {"access_token": "fresh"})
        recorded.assert_called_once()
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertTrue(self.vault.forget("outlook"), "a vault that cannot be read may hold it, so it is cleared")
        self.assertEqual(self.vault.status(), {"available": True, "path": str(self.path), "providers": [],
                                               "damaged": False})


if __name__ == "__main__":
    unittest.main()
