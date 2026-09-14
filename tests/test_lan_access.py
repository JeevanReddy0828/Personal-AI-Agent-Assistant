from __future__ import annotations

import importlib
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

PASSCODE = "test-passcode-123"


def _reload_webui(**env: str):
    """Import webui with a given environment. HOST and the passcode are read at import."""
    saved = {key: os.environ.get(key) for key in
             ("LAPTOP_AGENT_HOST", "LAPTOP_AGENT_PORT", "LAPTOP_AGENT_LAN_PASSCODE")}
    for key, value in env.items():
        os.environ[key] = value
    for key in saved:
        if key not in env:
            os.environ.pop(key, None)
    try:
        import laptop_agent.webui as webui
        return importlib.reload(webui)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class BindGuardTests(unittest.TestCase):
    """Binding to the network hands anyone on the same wifi a page carrying the API token,
    which is shell, files and mail on this laptop. It is refused without a passcode."""

    @classmethod
    def tearDownClass(cls):
        _reload_webui()  # leave the module back on its loopback defaults

    def test_a_network_bind_without_a_passcode_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            _reload_webui(LAPTOP_AGENT_HOST="0.0.0.0")
        self.assertIn("LAPTOP_AGENT_LAN_PASSCODE", str(caught.exception))

    def test_a_short_passcode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _reload_webui(LAPTOP_AGENT_HOST="0.0.0.0", LAPTOP_AGENT_LAN_PASSCODE="abc")

    def test_loopback_needs_no_passcode(self) -> None:
        webui = _reload_webui(LAPTOP_AGENT_HOST="127.0.0.1")
        self.assertFalse(webui.LAN_MODE)


class AddressLiteralTests(unittest.TestCase):
    """Only an IP is accepted as a Host in LAN mode. DNS rebinding needs a name the
    attacker controls, so names stay refused."""

    def setUp(self) -> None:
        self.webui = _reload_webui(LAPTOP_AGENT_HOST="127.0.0.1")

    def test_an_ip_is_an_address_literal(self) -> None:
        self.assertTrue(self.webui._is_address_literal("192.168.4.68:8770"))
        self.assertTrue(self.webui._is_address_literal("10.0.0.2"))
        self.assertTrue(self.webui._is_address_literal("[::1]:8770"))

    def test_a_name_is_not(self) -> None:
        self.assertFalse(self.webui._is_address_literal("evil.example.com:8770"))
        self.assertFalse(self.webui._is_address_literal("jarvis.local:8770"))
        self.assertFalse(self.webui._is_address_literal(""))


class LanGateTests(unittest.TestCase):
    """A device that is not this machine must present the passcode before it is served
    anything at all."""

    @classmethod
    def setUpClass(cls):
        cls.webui = _reload_webui(LAPTOP_AGENT_HOST="0.0.0.0", LAPTOP_AGENT_LAN_PASSCODE=PASSCODE)
        cls.metrics = patch.object(cls.webui, "system_metrics",
                                   return_value={"cpu_percent": 1, "ram_percent": 2, "gpus": []})
        cls.metrics.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.metrics.stop()
        _reload_webui()

    def setUp(self) -> None:
        self.webui._LAN_SESSIONS.clear()
        self.webui._LAN_FAILURES.clear()
        # The test server is reached over loopback, which the gate correctly treats as
        # this machine. Every case below is about a device that is NOT this machine.
        remote = patch.object(self.webui.Handler, "_client_is_local", lambda self: False)
        remote.start()
        self.addCleanup(remote.stop)

    def request(self, path="/", method="GET", body=None, host=None, cookie=None):
        host = host or f"192.168.4.68:{self.port}"
        headers = {"Host": host}
        if cookie:
            headers["Cookie"] = cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as exc:
            with exc:  # else Python warns about the unclosed error response
                return exc.code, exc.read(), exc.headers

    def test_a_new_device_gets_the_unlock_page_not_the_app(self) -> None:
        status, body, headers = self.request("/")
        self.assertEqual(status, 401)
        self.assertIn(b"Passcode", body)
        self.assertNotIn(b"J.A.R.V.I.S", body, "the lock screen must not say what it guards")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_an_api_call_without_the_passcode_is_refused(self) -> None:
        status, _body, _ = self.request("/api/health")
        self.assertEqual(status, 401)

    def test_the_wrong_passcode_is_refused(self) -> None:
        status, _body, _ = self.request("/api/pair", method="POST", body={"passcode": "nope"})
        self.assertEqual(status, 403)

    def test_the_right_passcode_opens_the_app(self) -> None:
        status, _body, headers = self.request("/api/pair", method="POST", body={"passcode": PASSCODE})
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)

        status, body, _ = self.request("/", cookie=cookie.split(";")[0])
        self.assertEqual(status, 200)
        self.assertIn(b"J.A.R.V.I.S", body)

    def test_a_named_host_is_refused_even_with_a_session(self) -> None:
        """DNS rebinding: a name the attacker controls, resolved to this machine."""
        _s, _b, headers = self.request("/api/pair", method="POST", body={"passcode": PASSCODE})
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        status, _body, _ = self.request("/api/health", host=f"evil.example.com:{self.port}", cookie=cookie)
        self.assertEqual(status, 403)

    def test_guessing_is_rate_limited(self) -> None:
        for _ in range(10):
            self.request("/api/pair", method="POST", body={"passcode": "nope"})
        status, body, _ = self.request("/api/pair", method="POST", body={"passcode": PASSCODE})
        self.assertEqual(status, 429, "a correct passcode after 10 failures should still be locked out")
        self.assertIn(b"Too many attempts", body)


class ThisMachineTests(unittest.TestCase):
    """The laptop itself never sees the lock screen, LAN mode or not."""

    @classmethod
    def setUpClass(cls):
        cls.webui = _reload_webui(LAPTOP_AGENT_HOST="0.0.0.0", LAPTOP_AGENT_LAN_PASSCODE=PASSCODE)
        cls.metrics = patch.object(cls.webui, "system_metrics",
                                   return_value={"cpu_percent": 1, "ram_percent": 2, "gpus": []})
        cls.metrics.start()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.webui.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.metrics.stop()
        _reload_webui()

    def test_loopback_is_served_without_a_passcode(self) -> None:
        self.webui._LAN_SESSIONS.clear()
        port = self.server.server_port
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health", headers={"Host": f"127.0.0.1:{port}"})
        with urllib.request.urlopen(req, timeout=20) as response:
            self.assertEqual(response.status, 200)


if __name__ == "__main__":
    unittest.main()
