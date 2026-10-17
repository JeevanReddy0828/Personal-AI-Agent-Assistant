"""MCP: tools from servers the owner configured, through a real subprocess speaking the protocol."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

from laptop_agent.access import acting_as, refused_command
from laptop_agent.accounts import Principal
from laptop_agent.safety import ApprovalDenied, ApprovalGate, RiskLevel
from laptop_agent.tools.mcp import McpTool, configured

import test_orchestrator

PERSONAL = Principal("p1", "family", "personal")
# A small MCP server: one read-only tool, one that changes things, one that destroys, one that errors.
SERVER = textwrap.dedent('''
    import json, sys
    TOOLS = [
        {"name": "echo", "description": "Says it back.", "inputSchema": {"type": "object"},
         "annotations": {"readOnlyHint": True}},
        {"name": "make", "description": "Makes a thing.", "inputSchema": {"type": "object"}},
        {"name": "wipe", "description": "Deletes all.", "inputSchema": {"type": "object"},
         "annotations": {"destructiveHint": True}},
        {"name": "fail", "description": "Always fails.", "inputSchema": {"type": "object"}},
    ]
    print("a stray log line on stdout", flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method, ident = message["method"], message["id"]
        if method == "initialize":
            result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake", "version": "1"}}
        elif method == "tools/list":
            if message.get("params", {}).get("cursor") == "page2":
                result = {"tools": TOOLS[2:]}
            else:
                result = {"tools": TOOLS[:2], "nextCursor": "page2"}
        elif method == "tools/call":
            name, args = message["params"]["name"], message["params"]["arguments"]
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}), flush=True)
            if name == "fail":
                result = {"content": [{"type": "text", "text": "it broke"}], "isError": True}
            else:
                result = {"content": [{"type": "text", "text": name + ":" + json.dumps(args, sort_keys=True)}]}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "no such method"}}),
                  flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
''')


class McpTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "server.py").write_text(SERVER, encoding="utf-8")
        self.config = root / "mcp.json"
        self.config.write_text(json.dumps({"mcpServers": {"Fake": {"command": sys.executable,
                                                                   "args": ["-u", str(root / "server.py")]}}}),
                               encoding="utf-8")
        self.cards: list = []
        self.approve = True

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        self.tool = McpTool(ApprovalGate(ask=ask), self.config)
        self.addCleanup(self.tool.close)

    def test_starting_a_server_asks_once_and_lists_every_page_of_tools(self) -> None:
        result = self.tool.listing()
        self.assertTrue(result.ok, result.message)
        self.assertIn("echo, make, wipe, fail", result.message)
        self.tool.listing()
        starts = [card for card in self.cards if card.action.startswith("Start MCP server")]
        self.assertEqual(len(starts), 1)
        self.assertIs(starts[0].risk, RiskLevel.HIGH)
        self.assertIn("server.py", starts[0].preview)

    def test_a_call_asks_at_the_risk_the_tool_declares(self) -> None:
        # A server's readOnlyHint is its own claim: at MEDIUM the web app ran "echo" with no card.
        for name, risk in (("echo", RiskLevel.HIGH), ("make", RiskLevel.HIGH), ("wipe", RiskLevel.CRITICAL)):
            with self.subTest(name):
                self.cards.clear()
                result = self.tool.call("fake", name, '{"x": 1}')
                self.assertEqual(result.message, name + ':{"x": 1}')
                self.assertIs(self.cards[-1].risk, risk)

    def test_a_denied_call_is_never_sent(self) -> None:
        self.tool.listing()
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            self.tool.call("fake", "wipe", "{}")

    def test_bad_arguments_unknown_tools_and_tool_errors_are_failures(self) -> None:
        self.assertFalse(self.tool.call("fake", "echo", "not json").ok)
        self.assertFalse(self.tool.call("fake", "echo", "[1, 2]").ok)
        self.assertIn("has no tool", self.tool.call("fake", "nope", "{}").message)
        failed = self.tool.call("fake", "fail", "{}")
        self.assertFalse(failed.ok, "a tool's own error was reported as success")
        self.assertIn("it broke", failed.message)
        self.assertFalse(self.tool.call("other", "echo", "{}").ok)

    def test_no_config_says_how_to_add_one(self) -> None:
        empty = McpTool(ApprovalGate(ask=lambda r: True), self.config.with_name("missing.json"))
        self.assertIn("mcpServers", empty.listing().message)

    def test_both_config_shapes_are_read(self) -> None:
        self.config.write_text(json.dumps({"servers": {"a": {"command": "x", "args": ["y"], "env": {"K": "v"}}}}),
                               encoding="utf-8")
        self.assertEqual(configured(self.config), {"a": (["x", "y"], {"K": "v"})})

    def test_a_server_that_will_not_start_is_reported(self) -> None:
        self.config.write_text(json.dumps({"mcpServers": {"gone": {"command": "no-such-program-xyz"}}}), encoding="utf-8")
        result = McpTool(ApprovalGate(ask=lambda r: True), self.config).listing()
        self.assertIn("not started", result.message)


class McpCommandTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def test_mcp_is_developer_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = self.build(Path(tmp))
            with acting_as(PERSONAL):
                self.assertEqual(refused_command("mcp"), "mcp")
                self.assertEqual(refused_command("mcp fake echo {}"), "mcp")
            result = asyncio.run(orch.handle("mcp", history=[]))
        self.assertIn("mcpServers", result.message)


# A server that misbehaves on purpose, one way per MODE, and writes every tools/call it receives
# to CALLS so a test can prove a refused call never reached it.
ODD = textwrap.dedent("""
    import json, os, sys
    MODE, CALLS = sys.argv[1], sys.argv[2]
    listed = os.path.exists(CALLS + ".listed")
    out = sys.stdout
    if MODE == "badbytes":
        out = open(sys.stdout.fileno(), "wb", buffering=0)
        out.write(b"caf\\xe9 started\\n")
    def say(message):
        data = json.dumps(message) + "\\n"
        if MODE == "badbytes":
            out.write(data.encode())
        else:
            sys.stdout.write(data); sys.stdout.flush()
    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:
            continue
        method, ident = message["method"], message["id"]
        if method == "initialize":
            if MODE == "noinit":
                continue
            if MODE == "ping":
                say({"jsonrpc": "2.0", "id": ident, "method": "ping"})
                answer = json.loads(sys.stdin.readline())
                open(CALLS + ".pong", "w").write(json.dumps(answer))
            if MODE == "junk":
                print("42", flush=True); print("null", flush=True)
            say({"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": "x", "capabilities": {}}})
        elif method == "tools/list":
            if MODE == "listfails" and not listed:
                open(CALLS + ".listed", "w").close()
                say({"jsonrpc": "2.0", "id": ident, "error": "plain string error"})
                continue
            say({"jsonrpc": "2.0", "id": ident, "result": {"tools": [{"name": "env"}, {"name": "wipe",
                 "annotations": {"destructiveHint": True}}, {"name": "echo"}]}})
        elif method == "tools/call":
            name = message["params"]["name"]
            open(CALLS, "a").write(name + "\\n")
            if MODE == "chatty":
                import time
                for _ in range(40):
                    say({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}); time.sleep(0.05)
            if name == "env":
                text = json.dumps({k: os.environ.get(k) for k in ("JARVIS_FAKE_SECRET", "FROM_CONFIG")})
            else:
                text = json.dumps(message["params"]["arguments"], sort_keys=True)
            say({"jsonrpc": "2.0", "id": ident, "result": {"content": [{"type": "text", "text": text}]}})
""")


class ReviewOf293Tests(unittest.TestCase):
    """Each finding of the review of #293, reproduced against a real subprocess first."""

    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "odd.py").write_text(ODD, encoding="utf-8")
        self.calls = self.root / "calls.txt"
        self.cards: list = []
        self.approve = True

    def tool(self, mode: str, env: dict | None = None) -> McpTool:
        config = self.root / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"odd": {"command": sys.executable, "env": env or {},
                                                             "args": ["-u", str(self.root / "odd.py"), mode,
                                                                      str(self.calls)]}}}), encoding="utf-8")

        def ask(request) -> bool:
            self.cards.append(request)
            return self.approve

        tool = McpTool(ApprovalGate(ask=ask), config)
        self.addCleanup(tool.close)
        return tool

    def test_a_server_never_inherits_our_secrets(self) -> None:
        import os
        os.environ["JARVIS_FAKE_SECRET"] = "nvapi-FAKE-SECRET"
        self.addCleanup(os.environ.pop, "JARVIS_FAKE_SECRET", None)
        result = self.tool("plain", env={"FROM_CONFIG": "given"}).call("odd", "env", "{}")
        self.assertEqual(json.loads(result.message), {"JARVIS_FAKE_SECRET": None, "FROM_CONFIG": "given"})

    def test_a_listing_that_fails_once_is_retried_not_stuck(self) -> None:
        tool = self.tool("listfails")
        self.assertIn("not started (plain string error)", tool.listing().message)
        self.assertTrue(tool.call("odd", "echo", '{"a": 1}').ok, "a failed listing left a server with no tools")

    def test_a_server_that_never_initializes_is_ended(self) -> None:
        import laptop_agent.tools.mcp as mcp
        spawned: list = []

        def spawn(argv, env):
            spawned.append(mcp._spawn(argv, env))
            return spawned[-1]

        tool = self.tool("noinit")
        tool._spawn = spawn
        with unittest.mock.patch.object(mcp, "TIMEOUT", 0.5):
            self.assertIn("not started", tool.listing().message)
        spawned[0].wait(timeout=5)
        self.assertIsNotNone(spawned[0].poll(), "the half-started server was left running")

    def test_a_server_that_exits_is_started_again(self) -> None:
        tool = self.tool("plain")
        self.assertTrue(tool.call("odd", "echo", "{}").ok)
        tool._running["odd"]._process.kill()
        tool._running["odd"]._process.wait(timeout=5)
        self.assertTrue(tool.call("odd", "echo", "{}").ok, "a dead server stayed 'running'")
        self.assertEqual(sum(card.action.startswith("Start MCP server") for card in self.cards), 2)

    def test_junk_lines_bad_bytes_and_its_own_requests_do_not_break_a_call(self) -> None:
        for mode in ("junk", "badbytes", "ping"):
            with self.subTest(mode):
                result = self.tool(mode).call("odd", "echo", '{"k": "v"}')
                self.assertEqual(result.message, '{"k": "v"}')
        self.assertEqual(json.loads((self.calls.parent / "calls.txt.pong").read_text())["result"], {})

    def test_a_refused_call_never_reaches_the_server_and_a_refused_start_is_a_refusal(self) -> None:
        tool = self.tool("plain")
        tool.listing()
        self.approve = False
        with self.assertRaises(ApprovalDenied):
            tool.call("odd", "wipe", "{}")
        self.assertNotIn("wipe", self.calls.read_text() if self.calls.exists() else "")
        with self.assertRaises(ApprovalDenied):
            self.tool("plain").listing()

    def test_progress_does_not_extend_the_deadline(self) -> None:
        import laptop_agent.tools.mcp as mcp
        tool = self.tool("chatty")
        tool.listing()
        with unittest.mock.patch.object(mcp, "TIMEOUT", 1.0):
            started = __import__("time").monotonic()
            result = tool.call("odd", "echo", "{}")
        self.assertFalse(result.ok)
        self.assertLess(__import__("time").monotonic() - started, 1.8, "every progress line restarted the wait")

    def test_a_long_argument_preview_says_it_was_cut(self) -> None:
        tool = self.tool("plain")
        tool.call("odd", "echo", json.dumps({"code": "x" * 3000 + "; rm -rf /"}))
        self.assertIn("CUT:", self.cards[-1].preview)


class McpArgumentsTests(unittest.TestCase):
    build = test_orchestrator.OrchestratorTests.build
    config = test_orchestrator.OrchestratorTests.config

    def test_json_arguments_reach_the_tool_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            orch = self.build(Path(tmp))
            seen: list = []
            orch._mcp_cache = type("Fake", (), {"call": lambda self, s, t, a: seen.append(a) or
                                                __import__("laptop_agent.tools.base", fromlist=["x"]).ToolResult.success("ok")})()
            asyncio.run(orch.handle('mcp db query {"sql": "select * from forecast", "out": "C:/r.csv", "t": "it’s"}',
                                    history=[]))
        self.assertEqual(json.loads(seen[0]), {"sql": "select * from forecast", "out": "C:/r.csv", "t": "it’s"})


if __name__ == "__main__":
    unittest.main()
