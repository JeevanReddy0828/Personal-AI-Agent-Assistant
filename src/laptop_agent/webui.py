"""Local web command deck for the laptop agent.

A stdlib-only HTTP server that serves a HUD-styled "command deck" dashboard and
wires it to the same AgentOrchestrator the CLI and GUI use. It binds to
localhost only. High-risk actions (sending email, moving/overwriting files,
downloads, browser form fills) are blocked here; read and search actions,
including web search and research, are allowed so the deck is genuinely useful.

Run:  python -m laptop_agent.webui   (then open the printed http://127.0.0.1 URL)
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from laptop_agent.app import build_orchestrator
from laptop_agent.cli import _json_safe
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel

HOST = "127.0.0.1"
PORT = 8770


def _planner_label() -> str:
    provider = getattr(_orchestrator.planner, "provider", None)
    name = type(provider).__name__ if provider else ""
    if "OpenAI" in name:
        model = getattr(provider, "model", "") or ""
        return model.split("/")[-1][:18] if model else "llm"
    return "heuristic"


def _guarded_approval(request: ApprovalRequest) -> bool:
    # Allow read/search-tier actions (LOW never reaches here; MEDIUM = web
    # search, research, page inspection). Block anything that changes external
    # state or writes/overwrites local files.
    return request.risk == RiskLevel.MEDIUM


_orchestrator = build_orchestrator(approval_callback=_guarded_approval)


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>J.A.R.V.I.S — Command Deck</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet" />
<style>
  :root{
    --bg:#07080b; --panel:#0c0e13; --panel2:#0f121a; --line:#1b2230;
    --amber:#ffb000; --amber-bright:#ffc94d; --amber-soft:#7a5a16;
    --ice:#5fd0e6; --text:#e9eef4; --muted:#6c778a; --ok:#54e0a0; --danger:#ff5d6c;
    --display:'Chakra Petch',sans-serif; --body:'IBM Plex Sans',sans-serif; --mono:'IBM Plex Mono',monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%}
  body{
    margin:0; background:var(--bg); color:var(--text); font-family:var(--body);
    overflow:hidden; letter-spacing:.2px;
  }
  /* atmosphere layers */
  .bg{position:fixed; inset:0; z-index:0; pointer-events:none}
  .bg.grid{
    background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);
    background-size:46px 46px; opacity:.16; mask-image:radial-gradient(ellipse 80% 70% at 50% 40%,#000 35%,transparent 85%);
  }
  .bg.glow{background:radial-gradient(60% 50% at 50% -8%,rgba(255,176,0,.10),transparent 70%),radial-gradient(40% 40% at 92% 100%,rgba(95,208,230,.06),transparent 70%)}
  .bg.scan{background:repeating-linear-gradient(0deg,rgba(255,255,255,.022) 0 1px,transparent 1px 3px); opacity:.5; mix-blend-mode:overlay}
  .bg.grain{opacity:.05; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}

  .deck{position:relative; z-index:1; display:grid; grid-template-columns:264px 1fr 330px; grid-template-rows:auto 1fr;
    gap:14px; height:100vh; padding:14px}

  .panel{position:relative; background:linear-gradient(180deg,var(--panel),var(--panel2)); border:1px solid var(--line);
    border-radius:4px; opacity:0; transform:translateY(10px); animation:reveal .7s cubic-bezier(.2,.8,.2,1) forwards}
  .panel::before,.panel::after{content:""; position:absolute; width:13px; height:13px; pointer-events:none}
  .panel::before{top:-1px; left:-1px; border-top:2px solid var(--amber); border-left:2px solid var(--amber)}
  .panel::after{bottom:-1px; right:-1px; border-bottom:2px solid var(--amber); border-right:2px solid var(--amber)}
  .d1{animation-delay:.05s}.d2{animation-delay:.16s}.d3{animation-delay:.27s}.d4{animation-delay:.38s}

  .phead{display:flex; align-items:center; gap:8px; padding:11px 14px 9px; border-bottom:1px solid var(--line)}
  .phead .tick{width:6px; height:6px; background:var(--amber); box-shadow:0 0 8px var(--amber)}
  .phead h2{margin:0; font-family:var(--display); font-weight:600; font-size:11px; letter-spacing:3px; color:var(--muted); text-transform:uppercase}
  .phead .meta{margin-left:auto; font-family:var(--mono); font-size:10px; color:var(--amber-soft)}

  /* ---- top rail ---- */
  .rail{grid-column:1 / -1; display:flex; align-items:center; gap:18px; padding:10px 18px}
  .ident{display:flex; align-items:center; gap:14px}
  .reactor{width:46px; height:46px; flex:none}
  .ident .name{font-family:var(--display); font-weight:700; font-size:22px; letter-spacing:6px; line-height:1}
  .ident .name b{color:var(--amber)}
  .ident .sub{font-family:var(--mono); font-size:10px; color:var(--muted); margin-top:3px; letter-spacing:1px}
  .rail .spacer{flex:1}
  .telemetry{display:flex; gap:10px; align-items:stretch}
  .chip{font-family:var(--mono); font-size:10px; letter-spacing:.6px; color:var(--text); border:1px solid var(--line);
    background:#0a0c11; padding:7px 11px; border-radius:3px; display:flex; flex-direction:column; gap:2px; min-width:78px}
  .chip span{color:var(--muted); font-size:8.5px; letter-spacing:1.6px; text-transform:uppercase}
  .chip b{font-weight:500; color:var(--amber-bright)}
  .chip .dot{display:inline-block; width:6px; height:6px; border-radius:50%; background:var(--ice); box-shadow:0 0 8px var(--ice); margin-right:5px; animation:blink 2.4s infinite}

  /* ---- left capabilities ---- */
  .cap{overflow:auto}
  .capgroup{padding:10px 12px 4px}
  .capgroup>.lbl{font-family:var(--mono); font-size:9px; letter-spacing:2px; color:var(--amber-soft); text-transform:uppercase; margin:6px 4px}
  .cmd{display:flex; align-items:center; gap:9px; width:100%; text-align:left; cursor:pointer; color:var(--text);
    background:transparent; border:1px solid transparent; border-radius:3px; padding:9px 10px; font-family:var(--mono);
    font-size:11.5px; transition:.16s; margin:2px 0}
  .cmd:hover{background:#11151e; border-color:var(--line); transform:translateX(3px)}
  .cmd .ic{width:16px; color:var(--amber); font-family:var(--display); font-weight:700; font-size:13px; text-align:center}
  .cmd:hover .ic{text-shadow:0 0 10px var(--amber)}

  /* ---- center stream ---- */
  .center{display:flex; flex-direction:column; min-height:0}
  .stream{flex:1; overflow:auto; padding:16px 18px; scroll-behavior:smooth}
  .turn{margin-bottom:16px; animation:slidein .35s ease both}
  .turn .who{font-family:var(--display); font-size:10px; letter-spacing:2.5px; text-transform:uppercase; color:var(--muted); margin-bottom:5px; display:flex; gap:8px; align-items:center}
  .turn .who::before{content:""; width:14px; height:1px; background:var(--line)}
  .turn.you .who{color:var(--ice)} .turn.sys .who{color:var(--amber)}
  .turn .body{font-size:14px; line-height:1.65; white-space:pre-wrap; color:#dde4ec}
  .turn.you .body{color:#bfe7f0}
  .turn.err .body{color:var(--danger)}
  .data{margin-top:6px; font-family:var(--mono); font-size:11px; line-height:1.55; color:#8fa1b6; background:#080a0e;
    border:1px solid var(--line); border-left:2px solid var(--amber-soft); border-radius:3px; padding:10px 12px;
    max-height:240px; overflow:auto; white-space:pre-wrap}
  .det{margin-top:9px}
  .det>summary{font-family:var(--mono); font-size:9.5px; letter-spacing:1.6px; text-transform:uppercase; color:var(--amber-soft);
    cursor:pointer; list-style:none; padding:2px 0; user-select:none}
  .det>summary::-webkit-details-marker{display:none}
  .det>summary::before{content:'\25B8  '; color:var(--amber)}
  .det[open]>summary::before{content:'\25BE  '}

  /* ---- command bar ---- */
  .bar{border-top:1px solid var(--line); padding:13px 16px; background:#090b10; position:relative; overflow:hidden}
  .bar.busy::after{content:""; position:absolute; inset:0; background:linear-gradient(90deg,transparent,rgba(255,176,0,.16),transparent); animation:sweep 1.1s linear infinite}
  .barrow{display:flex; gap:10px; align-items:center; position:relative; z-index:1}
  .prompt{font-family:var(--display); font-weight:700; color:var(--amber); font-size:16px; text-shadow:0 0 10px rgba(255,176,0,.5)}
  #input{flex:1; background:#0c0f15; border:1px solid var(--line); color:var(--text); font-family:var(--mono); font-size:13.5px;
    padding:13px 14px; border-radius:3px; caret-color:var(--amber); outline:none; transition:.18s}
  #input:focus{border-color:var(--amber-soft); box-shadow:0 0 0 1px var(--amber-soft),0 0 22px rgba(255,176,0,.12)}
  #input::placeholder{color:#46505f}
  .send{font-family:var(--display); font-weight:600; letter-spacing:2px; font-size:12px; color:var(--bg); background:var(--amber);
    border:none; border-radius:3px; padding:0 20px; height:44px; cursor:pointer; transition:.16s}
  .send:hover{background:var(--amber-bright); box-shadow:0 0 20px rgba(255,176,0,.4)}
  .send:disabled{opacity:.4; cursor:default; box-shadow:none}

  /* ---- right column ---- */
  .right{display:grid; grid-template-rows:1fr auto; gap:14px; min-height:0}
  .log{overflow:auto; padding:10px 12px; display:flex; flex-direction:column}
  .ev{font-family:var(--mono); font-size:11px; padding:7px 8px; border-bottom:1px dashed #141a26; display:flex; gap:8px; align-items:baseline; animation:slidein .3s ease both}
  .ev .mk{font-weight:600} .ev.ok .mk{color:var(--ok)} .ev.fail .mk{color:var(--danger)} .ev.info .mk{color:var(--ice)}
  .ev .lbl{color:#9aa7ba; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap}
  .ev .t{color:#3f4858; font-size:9.5px}
  .stat{padding:12px 14px; display:grid; grid-template-columns:1fr 1fr; gap:9px}
  .stat .cell .k{font-family:var(--mono); font-size:8.5px; letter-spacing:1.4px; color:var(--muted); text-transform:uppercase}
  .stat .cell .v{font-family:var(--display); font-size:21px; font-weight:600; color:var(--amber-bright); line-height:1.2}
  .stat .cell .v.ice{color:var(--ice)}

  ::-webkit-scrollbar{width:9px;height:9px} ::-webkit-scrollbar-track{background:transparent}
  ::-webkit-scrollbar-thumb{background:#1b2230; border-radius:9px} ::-webkit-scrollbar-thumb:hover{background:#2a3447}

  @keyframes reveal{to{opacity:1;transform:none}}
  @keyframes slidein{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  @keyframes sweep{from{transform:translateX(-100%)}to{transform:translateX(100%)}}
  @keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes spinrev{to{transform:rotate(-360deg)}}
  @keyframes corepulse{0%,100%{r:6;opacity:1}50%{r:9;opacity:.7}}
  .r-ring1{transform-origin:50px 50px; animation:spin 9s linear infinite}
  .r-ring2{transform-origin:50px 50px; animation:spinrev 6s linear infinite}
  .r-core{animation:corepulse 2.6s ease-in-out infinite}
  .busycore .r-core,.busycore .r-mid{stroke:var(--amber)!important; fill:var(--amber)!important}
  .busycore .r-ring1,.busycore .r-ring2{stroke:var(--amber)!important}
</style>
</head>
<body>
<div class="bg grid"></div><div class="bg glow"></div><div class="bg scan"></div><div class="bg grain"></div>

<div class="deck">
  <!-- top rail -->
  <header class="panel d1 rail">
    <div class="ident">
      <svg class="reactor" id="reactor" viewBox="0 0 100 100" aria-hidden="true">
        <circle class="r-ring1" cx="50" cy="50" r="42" fill="none" stroke="#5fd0e6" stroke-width="1.5" stroke-dasharray="6 10" opacity=".8"/>
        <circle class="r-ring2" cx="50" cy="50" r="34" fill="none" stroke="#5fd0e6" stroke-width="1" stroke-dasharray="3 7" opacity=".6"/>
        <circle class="r-mid" cx="50" cy="50" r="22" fill="none" stroke="#5fd0e6" stroke-width="2"/>
        <circle class="r-core" cx="50" cy="50" r="6" fill="#5fd0e6"/>
      </svg>
      <div>
        <div class="name">J<b>.</b>A<b>.</b>R<b>.</b>V<b>.</b>I<b>.</b>S</div>
        <div class="sub" id="boot">initializing command deck…</div>
      </div>
    </div>
    <div class="spacer"></div>
    <div class="telemetry">
      <div class="chip"><span>status</span><b><i class="dot"></i>online</b></div>
      <div class="chip"><span>planner</span><b id="t-planner">{{PLANNER}}</b></div>
      <div class="chip"><span>guard</span><b style="color:var(--amber-bright)">high-risk off</b></div>
      <div class="chip"><span>uplink</span><b id="t-lat">— ms</b></div>
      <div class="chip"><span>time</span><b id="t-clock">--:--:--</b></div>
    </div>
  </header>

  <!-- left capabilities -->
  <aside class="panel d2 cap">
    <div class="phead"><i class="tick"></i><h2>Capabilities</h2></div>
    <div id="caps"></div>
  </aside>

  <!-- center stream -->
  <main class="panel d3 center">
    <div class="phead"><i class="tick"></i><h2>Conversation</h2><div class="meta" id="t-count">00 cmds</div></div>
    <div class="stream" id="stream"></div>
    <div class="bar" id="bar">
      <div class="barrow">
        <span class="prompt">&#10095;</span>
        <input id="input" type="text" autocomplete="off" spellcheck="false" placeholder="ask me anything, or tell me what to do —  summarize the readme" />
        <button class="send" id="send">EXECUTE</button>
      </div>
    </div>
  </main>

  <!-- right column -->
  <section class="right">
    <div class="panel d4 log-wrap" style="display:flex;flex-direction:column;min-height:0">
      <div class="phead"><i class="tick"></i><h2>Activity Log</h2></div>
      <div class="log" id="log"></div>
    </div>
    <div class="panel d4">
      <div class="phead"><i class="tick"></i><h2>Telemetry</h2></div>
      <div class="stat">
        <div class="cell"><div class="k">commands</div><div class="v" id="s-cmds">0</div></div>
        <div class="cell"><div class="k">ok / fail</div><div class="v ice" id="s-ratio">0 / 0</div></div>
      </div>
    </div>
  </section>
</div>

<script>
  const CAPS = [
    ["files", [
      ["List files here","◷","go","what files are in this folder?"],
      ["Summarize a document","≣","type","summarize the file "],
      ["My knowledge base","⌸","go","what's saved in my knowledge base?"],
    ]],
    ["intel", [
      ["Search the web","⌕","type","search the web for "],
      ["Research a topic","◎","type","research "],
      ["Recall what I know","↺","type","what do I know about "],
    ]],
    ["assistant", [
      ["My recent tasks","⎙","go","show my recent tasks"],
      ["What you remember","◈","go","what do you remember about me?"],
      ["Activity log","⟳","go","show the audit log"],
      ["What can you do?","?","go","what can you do?"],
    ]],
  ];
  const stream=document.getElementById('stream'), input=document.getElementById('input'),
        send=document.getElementById('send'), log=document.getElementById('log'),
        bar=document.getElementById('bar'), reactor=document.getElementById('reactor');
  let cmds=0, oks=0, fails=0;

  const caps=document.getElementById('caps');
  CAPS.forEach(([group,items])=>{
    const g=document.createElement('div'); g.className='capgroup';
    g.innerHTML='<div class="lbl">'+group+'</div>';
    items.forEach(([label,ic,mode,text])=>{
      const b=document.createElement('button'); b.className='cmd';
      b.innerHTML='<span class="ic">'+ic+'</span><span>'+label+'</span>';
      b.onclick=()=>{ if(mode==='go'){ run(text); } else { input.value=text; input.focus(); } };
      g.appendChild(b);
    });
    caps.appendChild(g);
  });

  function clock(){const d=new Date();document.getElementById('t-clock').textContent=d.toLocaleTimeString('en-GB');}
  setInterval(clock,1000); clock();

  function turn(who,text,cls){
    const t=document.createElement('div'); t.className='turn '+(cls||'');
    const w=document.createElement('div'); w.className='who'; w.textContent=who;
    const b=document.createElement('div'); b.className='body'; b.textContent=text;
    t.appendChild(w); t.appendChild(b); stream.appendChild(t); stream.scrollTop=stream.scrollHeight; return t;
  }
  function event(label,status){
    const e=document.createElement('div'); e.className='ev '+status;
    const mk=status==='ok'?'✓':status==='fail'?'✗':'▸';
    e.innerHTML='<span class="mk">'+mk+'</span><span class="lbl"></span><span class="t">'+new Date().toLocaleTimeString('en-GB')+'</span>';
    e.querySelector('.lbl').textContent=label; log.insertBefore(e,log.firstChild);
  }
  function setBusy(on){send.disabled=on; bar.classList.toggle('busy',on); reactor.classList.toggle('busycore',on); input.disabled=on;}

  async function run(command){
    if(!command||!command.trim()) return;
    turn('operator',command,'you'); input.value=''; setBusy(true);
    const t0=performance.now();
    try{
      const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command})});
      const d=await r.json();
      document.getElementById('t-lat').textContent=Math.round(performance.now()-t0)+' ms';
      const node=turn('jarvis',d.message||'(no output)',d.ok?'sys':'err');
      const data=Object.assign({},d.data||{}); delete data.planner;
      if(Object.keys(data).length){
        const det=document.createElement('details'); det.className='det';
        const sum=document.createElement('summary'); sum.textContent='details'; det.appendChild(sum);
        const pre=document.createElement('div'); pre.className='data';
        pre.textContent=JSON.stringify(data,null,2); det.appendChild(pre);
        node.appendChild(det);
      }
      cmds++; d.ok?oks++:fails++; event(command, d.ok?'ok':'fail');
    }catch(err){ turn('jarvis','uplink error: '+err,'err'); cmds++; fails++; event(command,'fail'); }
    finally{
      setBusy(false); input.focus();
      document.getElementById('s-cmds').textContent=cmds;
      document.getElementById('s-ratio').textContent=oks+' / '+fails;
      document.getElementById('t-count').textContent=String(cmds).padStart(2,'0')+' cmds';
    }
  }

  send.onclick=()=>run(input.value);
  input.addEventListener('keydown',e=>{if(e.key==='Enter')run(input.value);});

  // boot sequence
  const boot=document.getElementById('boot');
  const lines=['booting kernel…','mounting tools: files · web · research · knowledge','approval gate: armed','command deck ready'];
  let bi=0;(function step(){ if(bi<lines.length){ boot.textContent=lines[bi++]; setTimeout(step,520);} else { boot.textContent='all systems nominal — awaiting orders'; } })();
  setTimeout(()=>{ turn('jarvis',"Command deck online. Talk to me normally — ask a question or tell me what to do, like “summarize the readme” or “research local-first AI.” I'll route it and answer in plain language. Guarded mode is on, so high-risk actions are blocked here.",'sys'); event('command deck online','info'); input.focus(); },900);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: object) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            page = PAGE.replace("{{PLANNER}}", _planner_label())
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
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
            body = {"ok": False, "message": f"Blocked — high-risk action requires the desktop app: {exc}", "data": {}}
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            body = {"ok": False, "message": f"Error: {exc}", "data": {}}
        self._send(200, json.dumps(body, default=str).encode("utf-8"), "application/json")


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"J.A.R.V.I.S command deck running at {url}")
    print("Guarded mode: high-risk actions blocked; read/search/research allowed. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def _find_chromium() -> str | None:
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "brave"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    return next((path for path in candidates if os.path.exists(path)), None)


def _launch_app_window(url: str) -> subprocess.Popen | None:
    exe = _find_chromium()
    if not exe:
        return None
    profile = Path(tempfile.gettempdir()) / "laptop_agent_deck_profile"
    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--window-size=1340,880",
    ]
    return subprocess.Popen(args)


def _launch_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    webview.create_window("J.A.R.V.I.S — Command Deck", url, width=1340, height=880, background_color="#07080b")
    webview.start()
    return True


def run_desktop() -> None:
    """Serve the deck and open it in a dedicated desktop window (no browser chrome)."""
    server = ThreadingHTTPServer((HOST, 0), Handler)
    port = server.server_address[1]
    url = f"http://{HOST}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"J.A.R.V.I.S command deck serving at {url}")

    if _launch_webview(url):  # blocks until the native window closes
        server.shutdown()
        return

    process = _launch_app_window(url)
    if process is None:
        print("No Chromium-based browser found; opening in the default browser instead.")
        webbrowser.open(url)
    else:
        print("Command deck opened as a desktop window.")
    # Keep the server alive regardless of the browser launcher process, which
    # exits immediately when it hands the window off to an existing instance.
    print("Deck is running. Press Ctrl+C here to stop it.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
        server.shutdown()


if __name__ == "__main__":
    main()
