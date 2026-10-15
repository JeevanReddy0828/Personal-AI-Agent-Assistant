"""MCP: tools from servers the owner configured, through a real subprocess speaking the protocol."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import textwrap
import unittest
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
        for name, risk in (("echo", RiskLevel.MEDIUM), ("make", RiskLevel.HIGH), ("wipe", RiskLevel.CRITICAL)):
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


if __name__ == "__main__":
    unittest.main()
