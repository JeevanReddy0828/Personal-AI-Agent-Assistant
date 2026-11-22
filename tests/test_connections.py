from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from laptop_agent import connections
from laptop_agent.model_status import BROKEN
from laptop_agent.planner.openai_compatible import OpenAICompatiblePlannerProvider, classify_failure


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive, as the hosted endpoints answer

    def log_message(self, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        self.server.ports.append(self.client_address[1])
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.endswith("/broken"):
            self._send(401, b'{"error":"bad key"}')
        elif self.path.endswith("/stream") or body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            chunk = {"choices": [{"delta": {"content": "hello"}, "finish_reason": "stop"}]}
            for line in (f"data: {json.dumps(chunk)}\n\n", "data: [DONE]\n\n"):
                data = line.encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
        elif self.path.endswith("/endless"):
            self.send_response(200)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            try:
                for _ in range(200):
                    data = b"data: more\n\n"
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                    self.wfile.flush()
                    time.sleep(0.01)
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                pass
        else:
            self._send(200, json.dumps({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}).encode())
        if self.server.close_after:
            self.close_connection = True

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        pass  # a client closing a connection it gave up on is what some tests do


class ConnectionReuseTests(unittest.TestCase):
    """Every model call opened a new TCP and TLS connection: urllib sends `Connection: close`,
    so the routing call and the chat stream after it each paid the handshake to one host."""

    def setUp(self) -> None:
        connections.clear()
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.stopped = False
        self.server.ports = []
        self.server.close_after = False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def tearDown(self) -> None:
        connections.clear()
        self.stop()

    def stop(self) -> None:
        if not self.stopped:
            self.stopped = True
            self.server.shutdown()
            self.server.server_close()

    def post(self, path: str = "/chat/completions", timeout: float = 5):
        request = urllib.request.Request(
            self.base + path, data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
        )
        return connections.urlopen(request, timeout=timeout)

    def test_calls_to_one_host_share_a_connection(self) -> None:
        for _ in range(3):
            with self.post() as response:
                self.assertEqual(json.loads(response.read())["choices"][0]["message"]["content"], "ok")
        self.assertEqual(len(self.server.ports), 3)
        self.assertEqual(len(set(self.server.ports)), 1, "each call opened its own connection")

    def test_the_provider_reuses_it_from_routing_to_the_stream(self) -> None:
        provider = OpenAICompatiblePlannerProvider("k", "m", base_url=self.base)
        self.assertEqual(provider._http_transport({"model": "m", "messages": []}), "ok")
        self.assertEqual("".join(provider.stream_answer("hi", {})), "hello")
        self.assertEqual(provider._http_transport({"model": "m", "messages": []}), "ok")
        self.assertEqual(len(self.server.ports), 3)
        self.assertEqual(len(set(self.server.ports)), 1, "the provider opened a connection per call")

    def test_a_stream_read_to_done_is_reused(self) -> None:
        with self.post("/stream") as response:
            for raw in response:
                if raw.strip() == b"data: [DONE]":
                    connections.drain(response)
                    break
        with self.post() as response:
            response.read()
        self.assertEqual(len(set(self.server.ports)), 1, "the stream's connection was not given back")

    def test_a_stream_left_early_is_not_reused(self) -> None:
        with self.post("/endless") as response:
            next(iter(response))
        with self.post() as response:
            response.read()
        self.assertEqual(len(set(self.server.ports)), 2, "a connection with a body still coming was reused")

    def test_a_connection_the_server_closed_is_replaced(self) -> None:
        self.server.close_after = True
        for _ in range(2):
            with self.post() as response:
                response.read()
        self.assertEqual(len(set(self.server.ports)), 2)

    def test_a_connection_the_server_closed_is_seen_as_dead(self) -> None:
        # Caught before the request is sent, so no attempt is spent finding out.
        with self.post() as response:
            response.read()
        [(connection, _)] = next(iter(connections._idle.values()))
        self.assertTrue(connections._alive(connection))
        self.server.close_after = True
        connection.sock.settimeout(5)
        with self.post() as response:  # takes that connection, and the server closes it after
            response.read()
        deadline = time.monotonic() + 2
        while connections._alive(connection) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(connections._alive(connection))

    def test_a_dead_connection_that_looked_alive_is_retried_once(self) -> None:
        # The liveness check misses a close that has not arrived yet; the request then fails
        # on the reused connection and goes again on a new one, instead of failing the turn.
        self.server.close_after = True
        alive, connections._alive = connections._alive, lambda connection: True
        try:
            for _ in range(2):
                with self.post() as response:
                    self.assertIn(b"ok", response.read())
        finally:
            connections._alive = alive
        self.assertEqual(len(set(self.server.ports)), 2)

    def test_an_idle_connection_too_old_is_not_reused(self) -> None:
        with self.post() as response:
            response.read()
        for key, pool in connections._idle.items():
            connections._idle[key] = [(conn, since - connections.IDLE_SECONDS - 1) for conn, since in pool]
        with self.post() as response:
            response.read()
        self.assertEqual(len(set(self.server.ports)), 2)

    def test_an_error_status_is_still_an_http_error(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post("/broken")
        self.assertEqual(caught.exception.code, 401)
        self.assertIn(b"bad key", caught.exception.read())
        self.assertEqual(classify_failure(caught.exception, "m")[0], BROKEN)
        with self.post() as response:
            response.read()
        self.assertEqual(len(set(self.server.ports)), 1, "a read error body should leave the connection usable")

    def test_an_unreachable_host_is_a_url_error(self) -> None:
        port = self.server.server_address[1]
        self.stop()
        request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", data=b"{}", method="POST")
        with self.assertRaises(urllib.error.URLError):
            connections.urlopen(request, timeout=2)


if __name__ == "__main__":
    unittest.main()
