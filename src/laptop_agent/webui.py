"""Minimal local web preview for the laptop agent.

Stdlib-only HTTP server that wraps the same AgentOrchestrator used by the CLI
and GUI, so you can try the agent from a browser. This is a local development
preview, not a production server: it binds to localhost only, and every
approval-gated action is denied, so only read-only features run. Risky actions
(sending email, opening apps/URLs, moving files) return a clean "denied"
message instead of executing.

Run:  python -m laptop_agent.webui  (then open the printed http://127.0.0.1 URL)
"""

from __future__ import annotations

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from laptop_agent.app import build_orchestrator
from laptop_agent.cli import _json_safe
from laptop_agent.safety import ApprovalDenied

HOST = "127.0.0.1"
PORT = 8770

# Preview mode: deny everything that reaches the approval gate. LOW-risk
# (read-only) actions are auto-skipped by the gate and still run.
_orchestrator = build_orchestrator(approval_callback=lambda request: False)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Laptop Agent — local preview</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
         margin: 0; background: #0f1115; color: #e7e9ee; }
  header { padding: 16px 20px; border-bottom: 1px solid #232734; }
  h1 { font-size: 16px; margin: 0; }
  .sub { color: #98a2b3; font-size: 12px; margin-top: 4px; }
  main { max-width: 860px; margin: 0 auto; padding: 16px 20px 120px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0 4px; }
  .chip { background: #1a1f2b; border: 1px solid #2a3140; color: #c7cdd9; border-radius: 999px;
          padding: 6px 11px; font-size: 12px; cursor: pointer; }
  .chip:hover { background: #222838; }
  .msg { margin: 14px 0; }
  .who { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: #7b8494; margin-bottom: 4px; }
  .bubble { background: #161b25; border: 1px solid #232a38; border-radius: 10px; padding: 10px 12px; white-space: pre-wrap; }
  .bubble.you { background: #1d2430; }
  .bubble.err { border-color: #5b2330; }
  pre { margin: 8px 0 0; background: #0c0f15; border: 1px solid #1d2330; border-radius: 8px;
        padding: 10px; overflow: auto; font-size: 12px; max-height: 320px; }
  form { position: fixed; bottom: 0; left: 0; right: 0; background: #0f1115cc; backdrop-filter: blur(6px);
         border-top: 1px solid #232734; padding: 12px 20px; }
  .row { max-width: 860px; margin: 0 auto; display: flex; gap: 8px; }
  input[type=text] { flex: 1; background: #161b25; border: 1px solid #2a3140; color: #e7e9ee;
                     border-radius: 8px; padding: 10px 12px; font-size: 14px; }
  button { background: #3b6cf6; border: 0; color: white; border-radius: 8px; padding: 10px 16px; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: .6; cursor: default; }
</style>
</head>
<body>
<header>
  <h1>Laptop Agent — local preview</h1>
  <div class="sub">Read-only preview. Approval-gated actions (send email, open apps, move files) are denied here.</div>
</header>
<main>
  <div class="chips" id="chips"></div>
  <div id="log"></div>
</main>
<form id="form">
  <div class="row">
    <input id="input" type="text" placeholder="Try: help" autocomplete="off" autofocus />
    <button id="send" type="submit">Send</button>
  </div>
</form>
<script>
  const examples = ["help", "memory", "tasks", "scan files .", "email oauth status",
                    "multi help ;; tasks", "what can you do"];
  const chips = document.getElementById("chips");
  const log = document.getElementById("log");
  const input = document.getElementById("input");
  const form = document.getElementById("form");
  const send = document.getElementById("send");

  examples.forEach(function (cmd) {
    const c = document.createElement("div");
    c.className = "chip"; c.textContent = cmd;
    c.onclick = function () { input.value = cmd; input.focus(); };
    chips.appendChild(c);
  });

  function bubble(who, text, cls) {
    const wrap = document.createElement("div");
    wrap.className = "msg";
    wrap.innerHTML = '<div class="who"></div><div class="bubble ' + (cls || "") + '"></div>';
    wrap.querySelector(".who").textContent = who;
    wrap.querySelector(".bubble").textContent = text;
    log.appendChild(wrap);
    return wrap;
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    const command = input.value.trim();
    if (!command) return;
    bubble("you", command, "you");
    input.value = ""; send.disabled = true;
    try {
      const res = await fetch("/api/command", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ command: command })
      });
      const data = await res.json();
      const b = bubble("agent", data.message || "(no message)", data.ok ? "" : "err");
      if (data.data && Object.keys(data.data).length) {
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(data.data, null, 2);
        b.querySelector(".bubble").appendChild(pre);
      }
    } catch (err) {
      bubble("agent", "Request failed: " + err, "err");
    } finally {
      send.disabled = false; input.focus();
      window.scrollTo(0, document.body.scrollHeight);
    }
  });
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # quieter console
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:
        if self.path != "/api/command":
            self._send(404, b"not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            command = str(payload.get("command", "")).strip()
        except (ValueError, UnicodeDecodeError):
            self._send(400, b'{"ok": false, "message": "bad request", "data": {}}', "application/json")
            return

        try:
            result = asyncio.run(_orchestrator.handle(command))
            body = {"ok": result.ok, "message": result.message, "data": _json_safe(result.data)}
        except ApprovalDenied as exc:
            body = {"ok": False, "message": f"Denied (preview mode blocks risky actions): {exc}", "data": {}}
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            body = {"ok": False, "message": f"Error: {exc}", "data": {}}
        self._send(200, json.dumps(body, default=str).encode("utf-8"), "application/json")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Laptop Agent web preview running at {url}")
    print("Read-only preview: approval-gated actions are denied. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
