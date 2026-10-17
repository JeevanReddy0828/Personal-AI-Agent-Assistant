"""`mcp`, `mcp <server> <tool> <json arguments>` - tools from MCP servers the owner configured.

Blender, a database or any other program that speaks the Model Context Protocol could not be
reached. Servers are listed in `<data dir>/mcp.json` ("mcpServers", as Claude Desktop writes it,
or "servers"), each started on first use as a subprocess speaking newline-delimited JSON-RPC over
stdio, with no extra package. Starting one runs a program, so it asks first (HIGH, once a session).
Every call asks: CRITICAL when the tool says it destroys, HIGH otherwise - a server's "read only"
is its own claim, so it never lowers the card. A server inherits only a safe slice of our
environment, never the .env secrets. A server's text is data shown to the user, never instructions.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from laptop_agent.cancellation import check_cancelled
from laptop_agent.failures import record_failure
from laptop_agent.safety import ApprovalDenied, ApprovalGate, ApprovalRequest, RiskLevel
from laptop_agent.tools.base import ToolResult

PROTOCOL = "2025-06-18"
TIMEOUT = 30.0
MAX_TEXT = 8000
MAX_PREVIEW = 2000
Spawn = Callable[[list[str], dict[str, str]], subprocess.Popen]
# What a server inherits from us, and nothing else: the whole environment carried the .env secrets
# (the NVIDIA key, the mail password) into every third-party program (review of #293). The same
# allowlist the MCP SDK uses; a server that needs a key gets it from its own "env" in mcp.json.
_INHERITED = ("APPDATA", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA", "PATH", "PATHEXT", "PROCESSOR_ARCHITECTURE",
              "PROGRAMFILES", "SYSTEMDRIVE", "SYSTEMROOT", "TEMP", "TMP", "USERNAME", "USERPROFILE", "WINDIR",
              "HOME", "LOGNAME", "SHELL", "TERM", "USER", "LANG", "LC_ALL", "TMPDIR")
_FAILURES = (OSError, TimeoutError, ConnectionError, RuntimeError, ValueError)


def server_env(extra: dict[str, str]) -> dict[str, str]:
    return {**{key: os.environ[key] for key in _INHERITED if key in os.environ}, **extra}


def _spawn(argv: list[str], env: dict[str, str]) -> subprocess.Popen:
    # Windows does not resolve "npx" to npx.cmd for Popen, which is how Claude Desktop configs say it.
    program = shutil.which(argv[0], path=env.get("PATH")) or argv[0]
    return subprocess.Popen([program, *argv[1:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", bufsize=1,
                            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class McpServer:
    def __init__(self, name: str, argv: list[str], env: dict[str, str], spawn: Spawn = _spawn) -> None:
        self.name = name
        self.argv = argv
        self._process = spawn(argv, server_env(env))
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 0
        self._lock = threading.Lock()
        self._noted_noise = False
        threading.Thread(target=self._read, name=f"mcp-{name}", daemon=True).start()
        try:
            self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                        "clientInfo": {"name": "jarvis", "version": "1"}})
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except BaseException:
            self.close()          # it never joins the running set, so nothing else would end it
            raise

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def _read(self) -> None:
        for line in self._process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _send(self, message: dict[str, Any]) -> None:
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _answer(self, message: dict[str, Any]) -> None:
        """A request from the server: JSON-RPC needs a reply. Ping is answered; nothing else is served."""
        if message.get("method") == "ping":
            self._send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
        else:
            self._send({"jsonrpc": "2.0", "id": message["id"],
                        "error": {"code": -32601, "message": "not supported by this client"}})

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        """Our request's response. The deadline is for the whole wait, not per line - a server that
        logs or sends progress forever used to hold the request, and its lock, for good - and Stop
        ends it."""
        timeout = TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + timeout
        with self._lock:
            self._next_id += 1
            ident = self._next_id
            self._send({"jsonrpc": "2.0", "id": ident, "method": method, **({"params": params} if params else {})})
            while True:
                check_cancelled()
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"{self.name} did not answer {method} within {timeout:.0f}s")
                try:
                    line = self._lines.get(timeout=min(left, 0.25))
                except queue.Empty:
                    continue
                if line is None:
                    raise ConnectionError(f"{self.name} stopped")
                try:
                    message = json.loads(line)
                except ValueError as exc:
                    if not self._noted_noise:   # one record per server, not one per log line
                        self._noted_noise = True
                        record_failure("mcp/stdout", exc, server=self.name)
                    continue
                if not isinstance(message, dict):
                    continue
                if "method" in message:          # the server's own request or notification, never our answer
                    if "id" in message:
                        self._answer(message)
                    continue
                if message.get("id") != ident:
                    continue
                if "error" in message:
                    error = message["error"]
                    raise RuntimeError(str(error.get("message") or error) if isinstance(error, dict) else str(error))
                result = message.get("result")
                return result if isinstance(result, dict) else {}

    def tools(self) -> list[dict[str, Any]]:
        found, cursor = [], None
        for _ in range(20):
            result = self.request("tools/list", {"cursor": cursor} if cursor else None)
            found.extend(tool for tool in result.get("tools") or [] if isinstance(tool, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return found

    def close(self) -> None:
        try:
            self._process.terminate()
        except OSError as exc:
            record_failure("mcp/close", exc, server=self.name)


def configured(path: Path) -> dict[str, tuple[list[str], dict[str, str]]]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    servers = (data.get("mcpServers") or data.get("servers") or {}) if isinstance(data, dict) else {}
    found = {}
    for name, entry in servers.items() if isinstance(servers, dict) else []:
        if isinstance(entry, dict) and isinstance(entry.get("command"), str):
            args = [str(arg) for arg in entry.get("args") or []]
            env = {str(key): str(value) for key, value in (entry.get("env") or {}).items()}
            found[str(name).lower()] = ([entry["command"], *args], env)
    return found


def _risk(tool: dict[str, Any]) -> RiskLevel:
    """HIGH unless the server says the tool destroys. A server's own "read only" is not trusted
    (the MCP spec says as much): at MEDIUM the web app ran it with no card at all."""
    hints = tool.get("annotations")
    if isinstance(hints, dict) and hints.get("destructiveHint") is True:
        return RiskLevel.CRITICAL
    return RiskLevel.HIGH


class McpTool:
    def __init__(self, approval_gate: ApprovalGate, config: Path, spawn: Spawn = _spawn) -> None:
        self._gate = approval_gate
        self._config = config
        self._spawn = spawn
        self._running: dict[str, McpServer] = {}
        self._tools: dict[str, list[dict[str, Any]]] = {}
        self._starting = threading.Lock()   # two requests at once would start two of the same server
        atexit.register(self.close)   # a server is a child process, which Windows leaves running

    def _servers(self) -> dict[str, tuple[list[str], dict[str, str]]] | ToolResult:
        try:
            servers = configured(self._config)
        except ValueError as exc:
            return ToolResult.failure(f"{self._config} isn't valid JSON: {exc}")
        if not servers:
            return ToolResult.failure(f"No MCP servers are set up. Add them to {self._config} as "
                                      "{\"mcpServers\": {\"name\": {\"command\": \"...\", \"args\": [...]}}}.")
        return servers

    def _server(self, name: str, argv: list[str], env: dict[str, str]) -> McpServer:
        """The running server, started (after asking) when it is not running - including when it
        exited or its command in mcp.json changed since it was started. It counts as running only
        once its tools are listed: stored before that, a failed listing left it "running" with no
        tools and every later call raised KeyError."""
        with self._starting:
            running = self._running.get(name)
            if running is not None and running.alive and running.argv == argv:
                return running
            if running is not None:
                running.close()
                del self._running[name]
                self._tools.pop(name, None)
            self._gate.require(ApprovalRequest(action=f"Start MCP server {name}", risk=RiskLevel.HIGH,
                                               reason="This runs the program configured for it in mcp.json.",
                                               preview=" ".join(argv)))
            started = McpServer(name, argv, env, self._spawn)
            try:
                tools = started.tools()
            except BaseException:
                started.close()
                raise
            self._running[name], self._tools[name] = started, tools
            return started

    def listing(self) -> ToolResult:
        servers = self._servers()
        if isinstance(servers, ToolResult):
            return servers
        lines = []
        for name, (argv, env) in servers.items():
            try:
                self._server(name, argv, env)
            except ApprovalDenied:
                raise                    # a refusal is the user's answer, not a server that failed
            except _FAILURES as exc:
                record_failure("mcp/start", exc, server=name)
                lines.append(f"- **{name}**: not started ({exc})")
                continue
            tools = ", ".join(str(tool.get("name", "?")) for tool in self._tools[name]) or "no tools"
            lines.append(f"- **{name}**: {tools}")
        return ToolResult.success("MCP servers:\n" + "\n".join(lines))

    def call(self, server: str, tool: str, arguments: str) -> ToolResult:
        servers = self._servers()
        if isinstance(servers, ToolResult):
            return servers
        server = server.strip().lower()
        if server not in servers:
            return ToolResult.failure(f"There's no MCP server called {server!r}. Say \"mcp\" to see them.")
        try:
            args = json.loads(arguments) if arguments.strip() else {}
        except ValueError:
            return ToolResult.failure("The arguments must be JSON, for example {\"query\": \"cube\"}.")
        if not isinstance(args, dict):
            return ToolResult.failure("The arguments must be a JSON object.")
        try:
            running = self._server(server, *servers[server])
        except ApprovalDenied:
            raise
        except _FAILURES as exc:
            record_failure("mcp/start", exc, server=server)
            return ToolResult.failure(f"{server} wouldn't start: {exc}")
        found = next((item for item in self._tools[server] if item.get("name") == tool), None)
        if found is None:
            return ToolResult.failure(f"{server} has no tool {tool!r}. It has: "
                                      + (", ".join(str(item.get("name", "?")) for item in self._tools[server])
                                         or "none") + ".")
        preview = json.dumps(args, indent=2)
        if len(preview) > MAX_PREVIEW:   # never cut silently: the part that matters may be the tail
            preview = (preview[:MAX_PREVIEW] + f"\n… CUT: {len(preview) - MAX_PREVIEW:,} more characters of "
                       "arguments are not shown. Deny unless you know what they are.")
        self._gate.require(ApprovalRequest(action=f"Use {server}'s tool {tool}", risk=_risk(found),
                                           reason=str(found.get("description") or "An MCP server's tool.")[:300],
                                           preview=preview))
        try:
            result = running.request("tools/call", {"name": tool, "arguments": args})
        except _FAILURES as exc:
            record_failure("mcp/call", exc, server=server, tool=tool)
            return ToolResult.failure(f"{server}'s {tool} failed: {exc}")
        text = "\n".join(str(part.get("text", "")) for part in result.get("content") or []
                         if isinstance(part, dict) and part.get("type") == "text").strip() or "(no text in the reply)"
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + f"\n\n… ({len(text):,} characters in all)"
        if result.get("isError"):
            return ToolResult.failure(f"{server}'s {tool} reported an error:\n{text}")
        return ToolResult.success(text, server=server, tool=tool)

    def close(self) -> None:
        for running in self._running.values():
            running.close()
