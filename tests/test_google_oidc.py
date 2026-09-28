from __future__ import annotations

import base64
import json
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
from laptop_agent.google_oidc import GoogleError, GoogleFlows, identity, TTL


def jwt(expected_nonce, **changes):
    claims = dict(iss="https://accounts.google.com", aud="client", exp=2000, iat=900,
                  nonce=expected_nonce, sub="subject-a", email="a@example.test", email_verified=True)
    claims.update(changes)
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "header." + body + ".signature"


class GoogleFlowsTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.calls = []
        self.flows = GoogleFlows("client", "secret", transport=self.exchange,
                                 clock=lambda: self.now, wall=lambda: self.now)

    def exchange(self, fields):
        self.calls.append(fields)
        return {"id_token": jwt(self.query["nonce"][0]), "refresh_token": "must-not-be-stored"}

    def launched(self, account=None, purpose="signin"):
        self.flow, self.proof = self.flows.start("http://127.0.0.1:8770/auth/google/callback", "session", account, purpose)
        ticket = self.flow.launch
        url, self.binding = self.flows.launch(ticket)
        self.query = parse_qs(urlsplit(url).query)
        return ticket

    def callback(self):
        self.flows.callback(self.query["state"][0], self.binding, "code")

    def complete(self, **kwargs):
        return self.flows.complete(self.flow.id, kwargs.get("proof", self.proof),
                                   kwargs.get("session", "session"), kwargs.get("account"))

    def test_pkce_nonce_scopes_and_one_time_completion(self):
        ticket = self.launched()
        self.assertEqual(self.query["scope"], ["openid email profile"])
        self.assertEqual(self.query["code_challenge_method"], ["S256"])
        self.assertNotIn("include_granted_scopes", self.query)
        self.assertIsNone(self.complete())
        with self.assertRaises(GoogleError):
            self.flows.launch(ticket)
        self.callback()
        expected = base64.urlsafe_b64encode(__import__('hashlib').sha256(self.calls[0]["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(self.query["code_challenge"], [expected])
        result = self.complete()
        self.assertEqual(result.result.sub, "subject-a")
        self.assertNotIn("must-not-be-stored", repr(result))
        self.assertEqual(result.verifier, "")
        with self.assertRaises(GoogleError):
            self.complete()

    def test_wrong_missing_binding_and_replay_do_not_exchange(self):
        self.launched()
        for state, binding in (("bad", self.binding), (self.query["state"][0], ""), (self.query["state"][0], "bad")):
            with self.assertRaises(GoogleError):
                self.flows.callback(state, binding, "code")
        self.assertFalse(self.calls)
        self.callback()
        with self.assertRaises(GoogleError):
            self.callback()
        self.assertEqual(len(self.calls), 1)

    def test_callback_race_claims_state_before_exchange(self):
        self.launched()
        entered, release = threading.Event(), threading.Event()
        def delayed(fields):
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test did not release exchange")
            return self.exchange(fields)
        self.flows.transport = delayed
        worker = threading.Thread(target=self.callback, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertIsNone(self.complete())
            with self.assertRaises(GoogleError):
                self.callback()
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.complete().result.sub, "subject-a")
        self.assertEqual(len(self.calls), 1)

    def test_another_window_cannot_complete_or_cancel(self):
        self.launched()
        self.callback()
        for proof in ("", "wrong", self.flow.id):
            with self.assertRaises(GoogleError):
                self.complete(proof=proof)
            self.flows.cancel(self.flow.id, proof)
        self.assertIsNotNone(self.complete())

    def test_changed_session_or_account_invalidates_flow(self):
        for kwargs in ({"session": "different"}, {"account": "different"}):
            self.launched()
            self.callback()
            with self.assertRaisesRegex(GoogleError, "changed"):
                self.complete(**kwargs)
            with self.assertRaises(GoogleError):
                self.complete()

    def test_expiration_capacity_cancel_and_provider_denial(self):
        self.flows.capacity = 1
        self.launched()
        with self.assertRaisesRegex(GoogleError, "Too many"):
            self.flows.start("redirect", "", None, "signin")
        self.now += TTL
        with self.assertRaises(GoogleError):
            self.callback()
        self.launched()
        self.flows.callback(self.query["state"][0], self.binding, "", denied=True)
        with self.assertRaisesRegex(GoogleError, "cancelled"):
            self.complete()
        self.assertFalse(self.calls)
        self.launched()
        self.flows.cancel(self.flow.id, self.proof)
        with self.assertRaises(GoogleError):
            self.complete()

    def test_provider_failures_never_echo_secrets(self):
        self.launched()
        def broken(fields):
            raise RuntimeError("private-token-and-code")
        self.flows.transport = broken
        self.callback()
        with self.assertRaisesRegex(GoogleError, "Google sign-in failed") as error:
            self.complete()
        self.assertNotIn("private", str(error.exception))

    def test_claims_fail_closed(self):
        cases = [dict(iss="https://evil.test"), dict(aud="other"), dict(aud=["client","other"]),
                 dict(azp="other"), dict(exp=1000), dict(exp=True), dict(exp=float("inf")),
                 dict(iat=1061), dict(iat=None), dict(nonce="different"), dict(sub=""), dict(sub=123)]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(GoogleError):
                identity(jwt("nonce", **changes), "client", "nonce", 1000)
        for token in (None, "broken", "h.W10.s", "h.e30.s", "a"*17000):
            with self.subTest(token=str(token)[:20]), self.assertRaises(GoogleError):
                identity(token,"client","nonce",1000)
        valid = identity(jwt("nonce", aud=["client","other"], azp="client", email_verified=False), "client", "nonce", 1000)
        self.assertEqual(valid.sub, "subject-a")
        self.assertIsNone(valid.email)

    def test_identity_expiring_before_completion_is_refused(self):
        self.launched()
        self.flows.transport=lambda _: {"id_token": jwt(self.query["nonce"][0], exp=1010)}
        self.callback()
        self.now=1011
        with self.assertRaisesRegex(GoogleError,"expired"):
            self.complete()


class GoogleTransportTests(unittest.TestCase):
    def call(self, status=200, chunks=None, error=None):
        from unittest.mock import MagicMock, patch
        from laptop_agent.google_oidc import exchange
        connection=MagicMock()
        response=connection.getresponse.return_value
        response.status=status
        response.read1.side_effect=chunks or [b'{"id_token":"fixture"}',b'']
        if error:connection.request.side_effect=error
        with patch("laptop_agent.google_oidc.http.client.HTTPSConnection",return_value=connection) as factory:
            try:
                return exchange({"code":"private-code","client_secret":"private-secret"})
            finally:
                connection.close.assert_called_once()
                factory.assert_called_once_with("oauth2.googleapis.com",timeout=10)
                self.assertEqual(connection.request.call_args.args[:2],("POST","/token"))

    def test_fixed_tls_endpoint_and_connection_closed(self):
        self.assertEqual(self.call(),{"id_token":"fixture"})

    def test_redirect_bad_json_oversized_and_io_errors_are_sanitized(self):
        for arguments in ({"status":302},{"chunks":[b'not json',b'']},
                          {"chunks":[b'x'*8192]*9},{"error":OSError("private-code private-secret")}):
            with self.subTest(arguments=list(arguments)),self.assertRaises(GoogleError) as error:
                self.call(**arguments)
            self.assertNotIn("private",str(error.exception))

    def test_trickling_body_stops_at_read_deadline(self):
        from unittest.mock import patch
        with patch("laptop_agent.google_oidc.time.monotonic",side_effect=[0,21]), self.assertRaisesRegex(GoogleError,"time"):
            self.call()
