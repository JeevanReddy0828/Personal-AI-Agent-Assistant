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
import threading
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from laptop_agent.storage import StorageDamaged, atomic_write_text, read_json_strict, synchronized

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
    # Moves on with every new password and every disable, and a session carries the epoch it
    # was granted under (webui._principal compares them): see `set_password`.
    epoch: int = 0

    def public(self) -> dict[str, object]:
        """What may leave the process: never the hash."""
        return {"id": self.id, "username": self.username, "role": self.role,
                "google_email": self.google_email, "disabled": self.disabled,
                "has_password": bool(self.password_hash), "created_at": self.created_at}


def _keep_a_developer(accounts: list[Account]) -> None:
    # Checked under the file lock, on the list about to be written: two developers demoting
    # each other at the same moment must not both succeed and leave the app with none.
    if not any(account.role == "dev" and not account.disabled for account in accounts):
        raise AccountError("That would leave no developer account. Use the command line if you mean it.")


def _valid(account: Account) -> bool:
    text_or_none = (account.password_hash, account.google_sub, account.google_email)
    return (isinstance(account.id, str) and bool(account.id) and account.role in ROLES
            and isinstance(account.username, str) and bool(_USERNAME.fullmatch(account.username))
            and isinstance(account.disabled, bool) and all(v is None or isinstance(v, str) for v in text_or_none)
            and type(account.epoch) is int and account.epoch >= 0)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# One hash holds 128 MiB for about half a second, outside the GIL, and the web server runs a
# thread per request: measured, four hashes at once took the peak working set from 21 MiB to
# 534 MiB, so fifty sign-in attempts from one device on the network would ask for 6.4 GB.
# The sign-in limiter cannot prevent that, because it counts a failure only once its hash
# has finished. So two hash at a time, and a request that cannot get a turn within
# HASH_WAIT seconds is told to try again instead of queueing without end.
HASH_SLOTS = 2
HASH_WAIT = 5.0
_hashing = threading.BoundedSemaphore(HASH_SLOTS)


class HashingBusy(RuntimeError):
    """No hashing slot came free in time. Nothing was decided: not a wrong password."""


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    if not _hashing.acquire(timeout=HASH_WAIT):
        raise HashingBusy("Too many sign-ins at once. Try again in a moment.")
    try:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                              maxmem=256 * n * r * p, dklen=_KEY_BYTES)
    finally:
        _hashing.release()


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
        """Every account, or `StorageDamaged`. Only a missing file means "no accounts": read as
        empty, a damaged file would switch sign-in off and hand the app to anyone, and read from
        its backup it could bring back a deleted account, an old password or an old role."""
        raw_accounts = read_json_strict(self.path, {"accounts": []}).get("accounts")
        if not isinstance(raw_accounts, list):
            raise StorageDamaged(f"{self.path} does not hold a list of accounts.")
        names = {field.name for field in fields(Account)}
        accounts = []
        for raw in raw_accounts:
            try:
                account = Account(**{key: value for key, value in raw.items() if key in names})
            except (AttributeError, TypeError) as exc:
                raise StorageDamaged(f"{self.path} holds an account that cannot be read.") from exc
            if not _valid(account):
                raise StorageDamaged(f"{self.path} holds an account that is not valid.")
            accounts.append(account)
        return accounts

    def _write(self, accounts: list[Account]) -> None:
        # No backup: an older copy of this file is a way to roll a decision back.
        atomic_write_text(self.path, _json({"accounts": [asdict(account) for account in accounts]}), backup=False)

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

    def _update(self, account_id: str, change: Callable[[Account], None], keep_developer: bool = False) -> Account:
        accounts = self._read()
        for account in accounts:
            if account.id == account_id:
                change(account)
                if keep_developer:
                    _keep_a_developer(accounts)
                self._write(accounts)
                return account
        raise AccountError("No such account.")

    def set_password(self, account_id: str, password: str) -> Account:
        """Also ends every session granted under the old password, including one whose sign-in
        was checked just before this and is still finishing: revoking sessions cannot, since that
        sign-in creates its session after the revoke has run."""
        check_password(password)
        hashed = hash_password(password, self.cost)

        def change(account: Account) -> None:
            account.password_hash = hashed
            account.epoch += 1

        return self._locked_update(account_id, change)

    @synchronized
    def _locked_update(self, account_id: str, change: Callable[[Account], None]) -> Account:
        return self._update(account_id, change)

    @synchronized
    def set_role(self, account_id: str, role: str, keep_developer: bool = False) -> Account:
        """`keep_developer` refuses a change that would leave no enabled developer: the web
        app asks for it, the command line (the way back in) does not."""
        if role not in ROLES:
            raise AccountError(f"A role is one of: {', '.join(ROLES)}.")
        return self._update(account_id, lambda account: setattr(account, "role", role), keep_developer)

    @synchronized
    def set_disabled(self, account_id: str, disabled: bool, keep_developer: bool = False) -> Account:
        def change(account: Account) -> None:
            if disabled:
                account.epoch += 1   # so enabling it again brings back no session, however late
            account.disabled = disabled

        return self._update(account_id, change, keep_developer)

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
    def delete(self, account_id: str, keep_developer: bool = False) -> None:
        accounts = self._read()
        remaining = [account for account in accounts if account.id != account_id]
        if len(remaining) == len(accounts):
            raise AccountError("No such account.")
        if keep_developer:
            _keep_a_developer(remaining)
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
        return _run(args, store, sessions, new_password, out)
    except StorageDamaged as exc:
        print(f"{exc} Nobody can sign in to the web app until it is repaired. Fix the file, or move "
              "it aside and create the first account again: python -m laptop_agent.accounts create "
              "<name> --role dev", file=out)
        return 1


def _run(args, store: AccountStore, sessions, new_password: Callable[[], str], out) -> int:
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
