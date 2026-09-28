"""Accounts for the web and desktop app: who may sign in, and with what role.

Two roles, because the app has two kinds of use. `dev` is everything, including the tools
that change the machine or show its internals; `personal` is the everyday assistant. The
CLI and the Tkinter dashboard run as the operating-system user and never consult this:
whoever can run them can already read this file, so they are also how an owner who is
locked out gets back in (`python -m laptop_agent.accounts`).

Passwords are hashed with scrypt from the standard library, so this adds no dependency.
The parameters travel inside each hash, so they can be raised later and an old hash still
verifies.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import re
import secrets
import sys
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from laptop_agent.storage import atomic_write_text, read_json, synchronized

ROLES = ("dev", "personal")
_USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
MIN_PASSWORD, MAX_PASSWORD = 8, 256

# OWASP's minimum for scrypt: N=2^17, r=8, p=1 needs 128 MiB per hash. Measured at 0.6s on
# this laptop, paid once per sign-in, never per request. hashlib's default `maxmem` is
# 32 MiB, which refuses these parameters outright.
SCRYPT_COST = (2 ** 17, 8, 1)
_KEY_BYTES = 64


class AccountError(ValueError):
    """A request the account rules refuse; the message is safe to show."""


@dataclass(frozen=True)
class Principal:
    """Who a request acts for. Read from the account on every request, so a role change
    or a disabled account takes effect at once rather than when the session ends."""

    account_id: str
    username: str
    role: str


@dataclass
class Account:
    id: str
    username: str
    role: str
    password_hash: str | None = None
    google_sub: str | None = None
    google_email: str | None = None
    disabled: bool = False
    created_at: str = ""

    def public(self) -> dict[str, object]:
        """What may leave the process: never the hash."""
        return {"id": self.id, "username": self.username, "role": self.role,
                "google_email": self.google_email, "disabled": self.disabled,
                "has_password": bool(self.password_hash), "created_at": self.created_at}


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          maxmem=256 * n * r * p, dklen=_KEY_BYTES)


def hash_password(password: str, cost: tuple[int, int, int] = SCRYPT_COST) -> str:
    n, r, p = cost
    salt = secrets.token_bytes(16)
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(_scrypt(password, salt, n, r, p))}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        expected = _unb64(key)
        candidate = _scrypt(password, _unb64(salt), n, r, p)
    except (ValueError, TypeError):
        return False
    return scheme == "scrypt" and hmac.compare_digest(candidate, expected)


def check_password(password: str) -> None:
    if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AccountError(f"A password needs {MIN_PASSWORD} to {MAX_PASSWORD} characters.")


def normalise_username(username: str) -> str:
    name = (username or "").strip().lower()
    if not _USERNAME.fullmatch(name):
        raise AccountError("A username is 1-32 letters, digits, dots, dashes or underscores, "
                           "starting with a letter or digit.")
    return name


class AccountStore:
    """`data_dir/accounts.json`. Any account at all switches sign-in on for the web app,
    a disabled one included: disabling the last account must not open the app to anyone."""

    def __init__(self, path: Path, cost: tuple[int, int, int] = SCRYPT_COST) -> None:
        self.path = Path(path)
        self.cost = cost
        self._dummy: str | None = None

    def _read(self) -> list[Account]:
        names = {field.name for field in fields(Account)}
        data = read_json(self.path, {"accounts": []})
        return [Account(**{key: value for key, value in raw.items() if key in names})
                for raw in data.get("accounts", []) if isinstance(raw, dict)]

    def _write(self, accounts: list[Account]) -> None:
        atomic_write_text(self.path, _json({"accounts": [asdict(account) for account in accounts]}))

    @synchronized
    def list(self) -> list[Account]:
        return self._read()

    @synchronized
    def exists(self) -> bool:
        return bool(self._read())

    @synchronized
    def get(self, account_id: str) -> Account | None:
        return next((account for account in self._read() if account.id == account_id), None)

    @synchronized
    def find(self, username: str) -> Account | None:
        name = (username or "").strip().lower()
        return next((account for account in self._read() if account.username == name), None)

    @synchronized
    def find_by_google_sub(self, sub: str) -> Account | None:
        if not sub:
            return None
        return next((account for account in self._read() if account.google_sub == sub), None)

    def create(self, username: str, role: str, password: str | None = None,
               now: Callable[[], datetime] = lambda: datetime.now(UTC), first: bool = False) -> Account:
        """A new account. `first` refuses unless there are none yet, decided under the same
        lock as the write, so two set-up requests racing cannot both create an owner."""
        name = normalise_username(username)
        if role not in ROLES:
            raise AccountError(f"A role is one of: {', '.join(ROLES)}.")
        if password is not None:
            check_password(password)
        # Hashed before the lock is taken: every request reads this file, and a 0.6s hash
        # under the lock would stall them all for as long as a sign-in takes.
        account = Account(id=secrets.token_hex(8), username=name, role=role,
                          password_hash=hash_password(password, self.cost) if password is not None else None,
                          created_at=now().isoformat(timespec="seconds"))
        self._insert(account, first)
        return account

    @synchronized
    def _insert(self, account: Account, first: bool = False) -> None:
        accounts = self._read()
        if first and accounts:
            raise AccountError("Sign-in is already set up.")
        if any(existing.username == account.username for existing in accounts):
            raise AccountError(f"There is already an account called {account.username}.")
        self._write(accounts + [account])

    def authenticate(self, username: str, password: str) -> Account | None:
        """The account, or None. Every path runs one hash, so how long a refusal takes
        does not say whether the username exists. The hash runs outside the file lock."""
        account = self.find(username)
        stored = account.password_hash if account and not account.disabled else None
        if stored is None:
            if self._dummy is None:
                self._dummy = hash_password(secrets.token_urlsafe(16), self.cost)
            verify_password(password or "", self._dummy)
            return None
        return account if verify_password(password or "", stored) else None

    def _update(self, account_id: str, change: Callable[[Account], None]) -> Account:
        accounts = self._read()
        for account in accounts:
            if account.id == account_id:
                change(account)
                self._write(accounts)
                return account
        raise AccountError("No such account.")

    def set_password(self, account_id: str, password: str) -> Account:
        check_password(password)
        hashed = hash_password(password, self.cost)
        return self._locked_update(account_id, lambda account: setattr(account, "password_hash", hashed))

    @synchronized
    def _locked_update(self, account_id: str, change: Callable[[Account], None]) -> Account:
        return self._update(account_id, change)

    @synchronized
    def set_role(self, account_id: str, role: str) -> Account:
        if role not in ROLES:
            raise AccountError(f"A role is one of: {', '.join(ROLES)}.")
        return self._update(account_id, lambda account: setattr(account, "role", role))

    @synchronized
    def set_disabled(self, account_id: str, disabled: bool) -> Account:
        return self._update(account_id, lambda account: setattr(account, "disabled", disabled))

    @synchronized
    def link_google(self, account_id: str, sub: str, email: str | None) -> Account:
        if not sub:
            raise AccountError("No Google account to link.")
        owner = self.find_by_google_sub(sub)
        if owner is not None and owner.id != account_id:
            raise AccountError("That Google account is already linked to another account.")

        def link(account: Account) -> None:
            account.google_sub, account.google_email = sub, email

        return self._update(account_id, link)

    @synchronized
    def unlink_google(self, account_id: str) -> Account:
        def unlink(account: Account) -> None:
            if not account.password_hash:
                raise AccountError("Set a password first, or this account could never sign in again.")
            account.google_sub = account.google_email = None

        return self._update(account_id, unlink)

    @synchronized
    def delete(self, account_id: str) -> None:
        accounts = self._read()
        remaining = [account for account in accounts if account.id != account_id]
        if len(remaining) == len(accounts):
            raise AccountError("No such account.")
        self._write(remaining)


def _json(data: object) -> str:
    return json.dumps(data, indent=2)


def main(argv: list[str] | None = None, store: AccountStore | None = None,
         sessions: object | None = None, ask: Callable[[str], str] = getpass.getpass,
         out=sys.stdout) -> int:
    """Manage accounts from the terminal, which is how a locked-out owner gets back in."""
    parser = argparse.ArgumentParser(prog="python -m laptop_agent.accounts",
                                     description="Manage J.A.R.V.I.S sign-in accounts.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create an account")
    create.add_argument("username")
    create.add_argument("--role", choices=ROLES, required=True)
    commands.add_parser("list", help="list accounts")
    for name, text in (("password", "set a new password"), ("disable", "stop an account signing in"),
                       ("enable", "let a disabled account sign in"), ("delete", "delete an account")):
        commands.add_parser(name, help=text).add_argument("username")
    role = commands.add_parser("role", help="change an account's role")
    role.add_argument("username")
    role.add_argument("role", choices=ROLES)
    args = parser.parse_args(argv)

    if store is None or sessions is None:
        from laptop_agent.config import load_config
        from laptop_agent.sessions import SessionStore

        data_dir = load_config().data_dir
        store = store or AccountStore(data_dir / "accounts.json")
        sessions = sessions or SessionStore(data_dir / "sessions.json")

    def new_password() -> str:
        first = ask("New password: ")
        if first != ask("Repeat it: "):
            raise AccountError("The two passwords differ.")
        return first

    try:
        if args.command == "list":
            accounts = store.list()
            for account in accounts:
                google = f"  google:{account.google_email}" if account.google_sub else ""
                state = "  (disabled)" if account.disabled else ""
                print(f"{account.username:<24}{account.role:<10}{google}{state}", file=out)
            if not accounts:
                print("No accounts: the web app does not ask anyone to sign in.", file=out)
            return 0
        if args.command == "create":
            account = store.create(args.username, args.role, new_password())
            print(f"Created {account.username} ({account.role}). The web app now asks for a sign-in.", file=out)
            return 0
        account = store.find(args.username)
        if account is None:
            raise AccountError(f"No account called {normalise_username(args.username)}.")
        if args.command == "password":
            store.set_password(account.id, new_password())
            sessions.revoke_account(account.id)          # type: ignore[attr-defined]
            print(f"Password changed for {account.username}; its sessions were signed out.", file=out)
        elif args.command in ("disable", "enable"):
            store.set_disabled(account.id, args.command == "disable")
            if args.command == "disable":
                sessions.revoke_account(account.id)      # type: ignore[attr-defined]
            print(f"{account.username} {args.command}d.", file=out)
        elif args.command == "role":
            store.set_role(account.id, args.role)
            print(f"{account.username} is now {args.role}.", file=out)
        elif args.command == "delete":
            store.delete(account.id)
            sessions.revoke_account(account.id)          # type: ignore[attr-defined]
            left = "" if store.exists() else " No accounts are left, so the web app no longer asks for a sign-in."
            print(f"Deleted {account.username}.{left}", file=out)
        return 0
    except AccountError as exc:
        print(str(exc), file=out)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
