"""Google identity from a direct TLS code exchange; no mailbox tokens are retained."""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import math
import re
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from laptop_agent.failures import record_failure

TTL = 600
PROOF_COOKIE = "jarvis_google_proof"
FLOW_COOKIE = "jarvis_google_flow"


class GoogleError(ValueError):
    """Safe to display; never includes provider replies, codes or tokens."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cookie(name: str, value: str, *, lax: bool = False) -> str:
    return f"{name}={value}; Path=/auth/google; HttpOnly; SameSite={'Lax' if lax else 'Strict'}; Max-Age={TTL if value else 0}"


# Token endpoint errors that no retry can fix: the client settings themselves are wrong.
_CLIENT_ERRORS = frozenset({"invalid_client", "unauthorized_client", "redirect_uri_mismatch"})


def _oauth_error(body: bytes) -> str:
    """The token endpoint's `error` code, when it is a plain code. It is the only part of a
    reply that is ever recorded: a reply can carry tokens, and the request carried the
    authorization code and the client secret."""
    try:
        code = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return ""
    return code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else ""


def exchange(fields: dict) -> dict:
    connection = http.client.HTTPSConnection("oauth2.googleapis.com", timeout=10)
    try:
        deadline = time.monotonic() + 20
        connection.request("POST", "/token", urlencode(fields),
                           {"Content-Type": "application/x-www-form-urlencoded"})
        response = connection.getresponse()
        chunks, length = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                record_failure("google/token", "no complete reply within 20s")
                raise GoogleError("Google did not answer in time. Try again.")
            if connection.sock:
                connection.sock.settimeout(min(10, remaining))
            chunk = response.read1(min(8192, 65537 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
            if length > 65536:
                record_failure("google/token", "reply over 64 KiB")
                raise GoogleError("Google returned an invalid sign-in response. Try again.")
        if response.status != 200:
            code = _oauth_error(b"".join(chunks))
            record_failure("google/token", f"HTTP {response.status} {code or 'without an error code'}")
            if code in _CLIENT_ERRORS:
                # "Start again" would be advice to retry something that cannot work.
                raise GoogleError("Google refused this app's client settings. Check GOOGLE_CLIENT_ID "
                                  "and GOOGLE_CLIENT_SECRET (a Desktop client).")
            raise GoogleError("Google could not finish sign-in. Start again.")
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if isinstance(exc, GoogleError):
            raise
        # The type alone: an exception's text is not ours to vouch for.
        errno = getattr(exc, "errno", None)
        record_failure("google/token", type(exc).__name__ + (f" errno {errno}" if isinstance(errno, int) else ""))
        raise GoogleError("Could not complete Google sign-in. Check your connection and client settings.") from None
    finally:
        connection.close()


@dataclass(frozen=True)
class Identity:
    sub: str
    email: str | None
    expires: float


# Which check refused an ID token: recorded in place of the token, whose claims are the
# user's identity. "time" is the one a wrong laptop clock produces.
_CLAIM_CHECKS = frozenset({"shape", "aud", "azp", "iss", "nonce", "time", "sub"})


def identity(token: str, client_id: str, nonce: str, now: float) -> Identity:
    # Only called on our own TLS token endpoint response, never a browser-supplied JWT.
    try:
        if not isinstance(token, str) or len(token) > 16384:
            raise ValueError("shape")
        header, body, signature = token.split(".")
        if not header or not signature:
            raise ValueError("shape")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        aud = claims.get("aud")
        audiences = aud if isinstance(aud, list) else [aud]
        if client_id not in audiences or (len(audiences) > 1 and "azp" not in claims):
            raise ValueError("aud")
        if claims.get("azp", client_id) != client_id:
            raise ValueError("azp")
        if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
            raise ValueError("iss")
        if claims.get("nonce") != nonce:
            raise ValueError("nonce")
        exp, iat = claims.get("exp"), claims.get("iat")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (exp, iat)):
            raise ValueError("time")
        if exp <= now or iat > now + 60 or iat > exp:
            raise ValueError("time")
        sub = claims.get("sub")
        if not isinstance(sub, str) or not sub.strip() or len(sub) > 255:
            raise ValueError("sub")
        email = claims.get("email")
        if claims.get("email_verified") is not True or not isinstance(email, str) or len(email) > 320:
            email = None
        return Identity(sub, email, exp)
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        reason = str(exc) if str(exc) in _CLAIM_CHECKS else type(exc).__name__
        record_failure("google/id-token", f"rejected at {reason}")
        raise GoogleError("Google's identity could not be verified. Start again.") from None


@dataclass
class Flow:
    id: str
    proof: str
    launch: str
    redirect: str
    session: str
    account: object
    created: float
    purpose: str
    state: str = ""
    binding: str = ""
    verifier: str = ""
    nonce: str = ""
    status: str = "waiting"
    result: Identity | None = None
    error: str = ""


class GoogleFlows:
    def __init__(self, client_id: str = "", client_secret: str = "", *, transport=exchange,
                 clock=time.monotonic, wall=time.time, capacity=32):
        self.client_id, self.client_secret = client_id, client_secret
        self.transport, self.clock, self.wall, self.capacity = transport, clock, wall, capacity
        self._lock = threading.Lock()
        self._flows: dict[str, Flow] = {}
        self._exchanges = threading.BoundedSemaphore(4)

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    def _prune(self):
        self._flows = {key: flow for key, flow in self._flows.items() if self.clock() - flow.created < TTL}

    def start(self, redirect: str, session: str, account, purpose: str):
        if not self.configured:
            raise GoogleError("Google sign-in is not configured. Ask the owner to set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET for a Desktop client.")
        proof = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            if len(self._flows) >= self.capacity:
                raise GoogleError("Too many Google sign-ins are pending. Try again later.")
            flow = Flow(secrets.token_urlsafe(32), digest(proof), secrets.token_urlsafe(32),
                        redirect, digest(session), account, self.clock(), purpose)
            self._flows[flow.id] = flow
            return flow, proof

    def launch(self, ticket: str):
        with self._lock:
            self._prune()
            flow = next((f for f in self._flows.values() if f.launch and secrets.compare_digest(f.launch, ticket)), None)
            if flow is None:
                raise GoogleError("This Google sign-in link expired or was already opened. Start again.")
            flow.launch = ""
            binding = secrets.token_urlsafe(32)
            flow.binding = digest(binding)
            flow.state, flow.verifier, flow.nonce = (secrets.token_urlsafe(32) for _ in range(3))
            challenge = base64.urlsafe_b64encode(hashlib.sha256(flow.verifier.encode()).digest()).decode().rstrip("=")
            url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
                "client_id": self.client_id, "redirect_uri": flow.redirect, "response_type": "code",
                "scope": "openid email profile", "state": flow.state, "nonce": flow.nonce,
                "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"})
            return url, binding

    def callback(self, state: str, binding: str, code: str, denied: bool = False):
        with self._lock:
            self._prune()
            flow = next((f for f in self._flows.values() if f.state and secrets.compare_digest(f.state, state)), None)
            if flow is None or not binding or not secrets.compare_digest(flow.binding, digest(binding)):
                raise GoogleError("This Google sign-in could not be matched to this browser. Start again.")
            flow.state = ""  # claim before exchanging: a racing callback cannot reuse the code
            flow.status = "exchanging"
        try:
            if denied or not code:
                raise GoogleError("Google sign-in was cancelled. You can try again.")
            if not self._exchanges.acquire(blocking=False):
                raise GoogleError("Google sign-in is busy. Please start again.")
            try:
                reply = self.transport({"code": code, "client_id": self.client_id,
                                        "client_secret": self.client_secret, "redirect_uri": flow.redirect,
                                        "grant_type": "authorization_code", "code_verifier": flow.verifier})
                result = identity(reply.get("id_token"), self.client_id, flow.nonce, self.wall())
            finally:
                self._exchanges.release()
            with self._lock:
                flow.result, flow.status = result, "ready"
        except Exception as exc:
            if not isinstance(exc, GoogleError):
                # By type only: the exchange this wraps held the code and the client secret.
                record_failure("google/callback", f"unexpected {type(exc).__name__}")
            with self._lock:
                flow.error = str(exc) if isinstance(exc, GoogleError) else "Google sign-in failed. Start again."
                flow.status = "failed"
        finally:
            flow.verifier = flow.nonce = ""

    def complete(self, flow_id: str, proof: str, session: str, account):
        with self._lock:
            self._prune()
            flow = self._flows.get(flow_id)
            if flow is None or not proof or not secrets.compare_digest(flow.proof, digest(proof)):
                raise GoogleError("Google sign-in expired or belongs to another window. Start again.")
            if flow.session != digest(session) or flow.account != account:
                del self._flows[flow_id]
                raise GoogleError("Your signed-in account changed. Start again.")
            if flow.status in {"waiting", "exchanging"}:
                return None
            del self._flows[flow_id]
            if flow.error:
                raise GoogleError(flow.error)
            if flow.result is None or flow.result.expires <= self.wall():
                raise GoogleError("Google sign-in expired. Start again.")
            return flow

    def cancel(self, flow_id: str, proof: str):
        with self._lock:
            flow = self._flows.get(flow_id)
            if flow and proof and secrets.compare_digest(flow.proof, digest(proof)):
                del self._flows[flow_id]
                return True
            return False
