"""Local chat app for the laptop agent.

A stdlib-only HTTP server that serves a workstation-style chat interface and
wires it to the same AgentOrchestrator the CLI uses. It binds to localhost only.
High-risk actions (sending email, moving/overwriting files, downloads, browser
form fills) are blocked here; read/search/research actions are allowed.

Panes: chat sessions, a Markdown-rendered conversation with file upload and
voice, live system metrics (CPU/GPU/RAM), and the Obsidian memory vault.

Run:  python -m laptop_agent.webui                 (browser tab)
      python -c "from laptop_agent.webui import run_desktop; run_desktop()"  (app window)
"""

from __future__ import annotations

import asyncio
import base64
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
from laptop_agent.config import load_config
from laptop_agent.health import system_health
from laptop_agent.metrics import system_metrics
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel
from laptop_agent.voice import SpeechChunker

HOST = "127.0.0.1"
PORT = 8770
UPLOAD_DIR = Path(tempfile.gettempdir()) / "laptop_agent_uploads"
MAX_UPLOAD_BYTES = 35 * 1024 * 1024
_CONFIG = load_config()
_LLM_STATUS: dict[str, object] = {"reachable": None}  # cached, updated by warm-up cycle


def _compose_command(command: str, attachments: object) -> str:
    """Fold uploaded file paths into the command the orchestrator runs.

    A bare upload (no typed message) goes straight through the universal file
    processor, which auto-detects the type and picks the best action. When the
    user typed something, the paths are appended as context so the LLM/heuristic
    router can target them for whatever the message asks.
    """
    command = (command or "").strip()
    paths = [str(p) for p in attachments if p] if isinstance(attachments, list) else []
    if not paths:
        return command
    if not command:
        if len(paths) == 1:
            return f"process file {paths[0]}"
        return "multi " + " ;; ".join(f"process file {path}" for path in paths)
    listing = "; ".join(paths)
    return (
        f"{command}\n\n[The user attached file(s) saved at: {listing}. "
        "Use the path(s) as the target for any file, image, audio, document, or indexing action.]"
    )


def _schedule_snapshot() -> dict:
    """Current scheduled jobs as plain JSON for the web panel."""
    result = asyncio.run(_orchestrator.handle("schedule list"))
    return {"ok": result.ok, "message": result.message, "jobs": _json_safe(result.data.get("jobs", []))}


def _agent_runs_snapshot() -> dict:
    """Autonomous agent run history as plain JSON for the web panel."""
    result = asyncio.run(_orchestrator.handle("agent runs"))
    return {"ok": result.ok, "runs": _json_safe(result.data.get("agent_runs", []))}


def _model_label(provider) -> str:
    model = getattr(provider, "model", "") or ""
    return model.split("/")[-1][:20] if model else "llm"


def _planner_label() -> str:
    provider = getattr(_orchestrator.planner, "provider", None)
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "heuristic"


def _smart_label() -> str:
    planner = getattr(_orchestrator, "smart_planner", None)
    provider = getattr(planner, "provider", None) if planner else None
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "—"


def _ultra_label() -> str:
    planner = getattr(_orchestrator, "ultra_planner", None)
    provider = getattr(planner, "provider", None) if planner else None
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "—"


def _vision_label() -> str:
    planner = getattr(_orchestrator, "vision_planner", None)
    provider = getattr(planner, "provider", None) if planner else None
    if provider and "OpenAI" in type(provider).__name__:
        return _model_label(provider)
    return "—"


def _guarded_approval(request: ApprovalRequest) -> bool:
    return request.risk == RiskLevel.MEDIUM


_orchestrator = build_orchestrator(approval_callback=_guarded_approval)


def _refresh_llm_status() -> None:
    """Ping the model and cache reachability (also keeps it warm)."""
    provider = getattr(_orchestrator.planner, "provider", None)
    ping = getattr(provider, "ping", None)
    if ping is None:
        _LLM_STATUS["reachable"] = None  # heuristic planner — not applicable
        return
    _LLM_STATUS["reachable"] = ping()


def _warmup() -> None:
    threading.Thread(target=_refresh_llm_status, daemon=True).start()


def _keep_warm(interval: float = 200.0) -> None:
    """Ping the fast model periodically so it does not go cold between messages,
    and refresh the cached reachability used by the health check."""
    if getattr(getattr(_orchestrator.planner, "provider", None), "ping", None) is None:
        return

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            _refresh_llm_status()

    threading.Thread(target=loop, daemon=True).start()


def _schedule_ticker(interval: float = 60.0) -> None:
    """Fire any due scheduled jobs once a minute. Daemon thread so it stops with the app."""

    def loop() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            try:
                asyncio.run(_orchestrator.run_due_schedules())
            except Exception:
                pass  # never let a scheduled run take down the ticker

    threading.Thread(target=loop, daemon=True).start()


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>J.A.R.V.I.S</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&display=swap" rel="stylesheet" />
<style>
  :root{
    --bg:#08090d; --bg2:#0c0e14; --panel:#11141c; --panel2:#0d1018; --line:#1c2230; --line2:#283143;
    --amber:#ffb000; --amber-b:#ffc94d; --amber-soft:#7a5a16; --ice:#5fd0e6;
    --text:#e9eef4; --muted:#7c879a; --ok:#54e0a0; --danger:#ff5d6c; --purple:#a98bff;
    --display:'Chakra Petch',sans-serif; --body:'IBM Plex Sans',sans-serif; --mono:'IBM Plex Mono',monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--text);font-family:var(--body);overflow:hidden}
  .app{display:grid;grid-template-columns:212px 1fr 286px;grid-template-rows:54px 1fr;height:100vh}

  header{grid-column:1/-1;display:flex;align-items:center;gap:12px;padding:0 16px;border-bottom:1px solid var(--line);background:var(--bg2)}
  .reactor{width:28px;height:28px}
  .brand .n{font-family:var(--display);font-weight:700;letter-spacing:4px;font-size:15px}
  .brand .n b{color:var(--amber)}
  header .sp{flex:1}
  .pill{font-family:var(--mono);font-size:9.5px;color:var(--muted);letter-spacing:.5px;padding:5px 10px;border:1px solid var(--line);border-radius:999px}
  .pill b{color:var(--amber-b);font-weight:500}
  #healthPill{cursor:default} #healthPill .dot{transition:background .4s,box-shadow .4s}
  #healthPill.ok .dot{background:var(--ok);box-shadow:0 0 8px var(--ok)}
  #healthPill.degraded .dot{background:var(--amber);box-shadow:0 0 8px var(--amber)}
  #healthPill.setup .dot{background:var(--danger);box-shadow:0 0 8px var(--danger)}
  .setupcard{text-align:left;max-width:480px;background:var(--panel);border:1px solid var(--danger);border-radius:12px;padding:14px 16px;font-size:13px;line-height:1.6;color:var(--text)}
  .setupcard b{font-family:var(--display);letter-spacing:1px;color:var(--amber-b);display:block;margin-bottom:6px}
  .setupcard code{font-family:var(--mono);font-size:12px;background:#0a0d13;border:1px solid var(--line);border-radius:4px;padding:1px 5px;color:var(--amber-b)}
  .pill.smart b{color:var(--purple)}
  .vbtn{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;color:var(--text);background:var(--panel);border:1px solid var(--line2);border-radius:999px;padding:7px 13px;cursor:pointer}
  .vbtn:hover{border-color:var(--amber-soft);color:var(--amber-b)}
  .vbtn .dot{width:7px;height:7px;border-radius:50%;background:var(--ice);box-shadow:0 0 8px var(--ice)}
  .vbtn.wide{width:100%;justify-content:center;margin:2px 0 12px;padding:11px;font-size:12px;letter-spacing:1px;background:var(--bg2)}
  .vbtn.wide.on{background:var(--amber);color:#08090d;border-color:var(--amber)} .vbtn.wide.on .dot{background:#08090d;box-shadow:none}
  details.conn{border:1px solid var(--line);border-radius:10px;background:var(--panel);overflow:hidden}
  details.conn>summary{font-family:var(--display);font-size:11px;letter-spacing:2px;text-transform:uppercase;color:var(--amber-b);padding:11px 12px;cursor:pointer;list-style:none;user-select:none}
  details.conn>summary::-webkit-details-marker{display:none}
  details.conn>summary::after{content:'\25BE';float:right;color:var(--muted)} details.conn[open]>summary::after{content:'\25B4'}
  details.conn>div,details.conn>.seclbl,details.conn>.vstat{margin-left:6px;margin-right:6px}
  .crow{display:flex;align-items:center;gap:8px;padding:7px 8px;font-family:var(--mono);font-size:11px;border-bottom:1px dashed #141a26}
  .crow .d{width:7px;height:7px;border-radius:50%;flex:none;background:var(--ok);box-shadow:0 0 7px var(--ok)} .crow .d.off{background:#3a4150;box-shadow:none} .crow .d.warn{background:var(--amber);box-shadow:0 0 7px var(--amber)}
  .crow .k{color:var(--muted)} .crow .v{margin-left:auto;color:var(--text)}

  aside{background:var(--panel2);border-right:1px solid var(--line);overflow-y:auto;padding:12px 10px}
  aside.right{border-right:none;border-left:1px solid var(--line)}
  .seclbl{font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--amber-soft);text-transform:uppercase;margin:14px 6px 8px}
  .newchat{width:100%;text-align:left;background:var(--amber);color:#08090d;border:none;border-radius:9px;padding:10px 12px;font-family:var(--display);font-weight:600;font-size:12px;letter-spacing:1px;cursor:pointer}
  .newchat:hover{background:var(--amber-b)}
  .sess{display:block;width:100%;text-align:left;background:transparent;border:1px solid transparent;border-radius:8px;padding:9px 11px;color:var(--text);font-size:12.5px;cursor:pointer;margin:3px 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sess:hover{background:var(--panel)} .sess.active{background:var(--panel);border-color:var(--line2)}

  main{display:flex;flex-direction:column;min-height:0;position:relative}
  .hero{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:10px 0 6px;border-bottom:1px solid var(--line);
    background:radial-gradient(60% 120% at 50% 0%,rgba(255,176,0,.05),transparent 70%)}
  #core{width:240px;height:118px;display:block}
  .corestate{font-family:var(--display);letter-spacing:5px;text-transform:uppercase;font-size:11px;color:var(--ice);margin-top:-6px;transition:color .4s}
  .corestate b{color:#fff;font-weight:600}
  .corestate.think{color:var(--amber-b)} .corestate.speak{color:var(--amber)} .corestate.listen{color:var(--ice)}
  .corestate .blip{display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor;box-shadow:0 0 8px currentColor;margin-right:8px;vertical-align:middle;animation:blink 2s infinite}
  .chat{flex:1;overflow-y:auto;padding:18px 22px 6px}
  .empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:16px;padding-bottom:30px}
  .empty .orb{width:70px;height:70px}
  .empty h1{font-family:var(--display);font-weight:600;letter-spacing:2px;font-size:20px;margin:0}
  .empty p{color:var(--muted);font-size:13px;margin:0;max-width:420px;line-height:1.6}
  .suggest{display:grid;grid-template-columns:1fr 1fr;gap:9px;width:100%;max-width:480px}
  .scard{text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:11px 13px;cursor:pointer;font-size:12.5px}
  .scard:hover{border-color:var(--amber-soft)} .scard b{display:block;font-family:var(--display);font-size:10px;letter-spacing:1px;color:var(--amber-b);text-transform:uppercase;margin-bottom:3px}

  .msg{display:flex;gap:11px;margin:16px 0;animation:rise .25s ease both}
  .msg .av{width:28px;height:28px;border-radius:7px;flex:none;display:flex;align-items:center;justify-content:center;font-family:var(--display);font-weight:700;font-size:10px}
  .msg.user .av{background:#13202b;color:var(--ice);border:1px solid #1d3441}
  .msg.bot .av{background:#1a1405;color:var(--amber);border:1px solid #3a2c0a}
  .msg .content{flex:1;min-width:0;padding-top:2px}
  .msg .who{font-family:var(--display);font-size:9px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:4px}
  .md{font-size:14px;line-height:1.65;color:#e4eaf1;word-wrap:break-word}
  .md p{margin:.4em 0} .md h3{font-size:15px;margin:.6em 0 .3em;color:#fff} .md h4{font-size:13.5px;margin:.5em 0 .2em;color:#fff}
  .md ul,.md ol{margin:.3em 0;padding-left:1.3em} .md li{margin:.18em 0}
  .md code{font-family:var(--mono);font-size:12.5px;background:#0a0d13;border:1px solid var(--line);border-radius:4px;padding:1px 5px;color:var(--amber-b)}
  .md pre{background:#070a0f;border:1px solid var(--line);border-left:2px solid var(--amber-soft);border-radius:7px;padding:11px 13px;overflow-x:auto;margin:.5em 0}
  .md pre code{background:none;border:none;padding:0;color:#bcd3df}
  .md strong{color:#fff;font-weight:500} .md a{color:var(--amber-b)}
  .msg.user .md{color:#cfe9f1} .msg.err .md{color:var(--danger)}
  .att{display:inline-flex;align-items:center;gap:6px;margin:6px 6px 0 0;background:#0b1016;border:1px solid var(--line2);border-radius:7px;padding:5px 9px;font-family:var(--mono);font-size:11px;color:var(--muted)}
  .att .ic{color:var(--amber)}
  .det{margin-top:7px} .det>summary{font-family:var(--mono);font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:var(--amber-soft);cursor:pointer;list-style:none}
  .det>summary::-webkit-details-marker{display:none} .det>summary::before{content:'\25B8  ';color:var(--amber)} .det[open]>summary::before{content:'\25BE  '}
  .data{margin-top:5px;font-family:var(--mono);font-size:10.5px;color:#8fa1b6;background:#080a0e;border:1px solid var(--line);border-radius:6px;padding:9px 11px;max-height:200px;overflow:auto;white-space:pre-wrap}
  .dots span{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--amber);margin-right:4px;animation:blink 1.2s infinite}
  .dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}

  .composer{padding:8px 20px 14px}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:7px}
  .chip{display:inline-flex;align-items:center;gap:7px;background:var(--panel);border:1px solid var(--line2);border-radius:8px;padding:6px 9px;font-family:var(--mono);font-size:11px}
  .chip .ic{color:var(--amber)} .chip .rm{cursor:pointer;color:var(--muted)} .chip .rm:hover{color:var(--danger)}
  .box{display:flex;align-items:flex-end;gap:6px;background:var(--bg2);border:1px solid var(--line2);border-radius:15px;padding:6px 6px 6px 5px}
  .box:focus-within{border-color:var(--amber-soft);box-shadow:0 0 0 1px var(--amber-soft)}
  .iconbtn{width:38px;height:38px;flex:none;border:none;background:transparent;color:var(--muted);border-radius:10px;cursor:pointer;font-size:16px;position:relative}
  .iconbtn:hover{background:#161a23;color:var(--amber-b)} .iconbtn.live{color:var(--amber)}
  .iconbtn.live::after{content:'';position:absolute;inset:0;border-radius:10px;border:1.5px solid var(--amber);animation:ring 1.1s ease-out infinite}
  #ta{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-family:var(--body);font-size:14px;line-height:1.5;resize:none;max-height:150px;padding:9px 4px}
  #ta::placeholder{color:#46505f}
  .sendbtn{width:40px;height:40px;flex:none;border:none;border-radius:11px;background:var(--amber);color:#08090d;cursor:pointer;font-size:16px}
  .sendbtn.stop{background:var(--danger);color:#fff}
  #agentBtn.on{color:var(--amber-b);border-color:var(--amber-soft);background:rgba(255,184,77,.08)}
  .trace{margin:6px 0 2px;border:1px solid var(--line);border-left:2px solid var(--amber-soft);border-radius:10px;background:#0a0d13;overflow:hidden}
  .trace .thead{font-family:var(--mono);font-size:10.5px;letter-spacing:.5px;color:var(--amber-b);padding:8px 12px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center}
  .trace .thead .gdot{width:7px;height:7px;border-radius:50%;background:var(--amber);box-shadow:0 0 7px var(--amber);animation:pulse 1.3s infinite}
  .trace.done .thead .gdot{background:var(--ok);box-shadow:0 0 7px var(--ok);animation:none}
  .trace.fail .thead .gdot{background:var(--danger);box-shadow:0 0 7px var(--danger);animation:none}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  .tstep{padding:8px 12px;border-bottom:1px solid #0f141c;font-size:12.5px;line-height:1.5}
  .tstep:last-child{border-bottom:none}
  .tstep .tn{font-family:var(--mono);font-size:10px;color:var(--muted)}
  .tstep .tcmd{font-family:var(--mono);font-size:11.5px;color:var(--text);background:#070a0f;border:1px solid var(--line);border-radius:5px;padding:2px 7px;display:inline-block;margin:2px 0}
  .tstep .tobs{color:var(--muted);font-size:11.5px}
  .tstep.failed .tcmd{border-color:var(--danger);color:#ff8b96}
  .sendbtn:hover{background:var(--amber-b)} .sendbtn:disabled{opacity:.35}
  .hint{text-align:center;color:#3f4858;font-family:var(--mono);font-size:9px;margin-top:7px}

  .metric{margin:9px 6px}
  .metric .top{display:flex;justify-content:space-between;font-family:var(--mono);font-size:10.5px;color:var(--muted);margin-bottom:5px}
  .metric .top b{color:var(--text);font-weight:500}
  .bar{height:6px;background:#0a0d13;border-radius:6px;overflow:hidden}
  .bar i{display:block;height:100%;background:linear-gradient(90deg,var(--amber-soft),var(--amber));transition:width .6s}
  .bar.g i{background:linear-gradient(90deg,#1d6f86,var(--ice))}
  .vault{margin:6px}
  .vstat{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;color:var(--text);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 11px}
  .vstat .d{width:7px;height:7px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok)} .vstat .d.off{background:var(--danger);box-shadow:0 0 8px var(--danger)}
  .note{font-family:var(--mono);font-size:11px;color:var(--muted);padding:6px 8px;border-bottom:1px dashed #141a26;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .note::before{content:'\25C8  ';color:var(--amber-soft)}
  .agentSummary{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 6px 10px}
  .agentStat{background:#090c12;border:1px solid var(--line);border-radius:8px;padding:7px 6px;text-align:center}
  .agentStat b{display:block;color:var(--text);font-family:var(--display);font-size:15px}
  .agentStat span{font-family:var(--mono);font-size:8.5px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
  .agentcard{display:block;width:calc(100% - 12px);margin:6px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 10px;text-align:left;color:var(--text);cursor:pointer}
  .agentcard:hover{border-color:var(--amber-soft);background:#151923}
  .agentcard .top{display:flex;align-items:center;gap:7px}
  .agentcard .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);box-shadow:0 0 8px var(--ok);flex:none}
  .agentcard.working .dot{background:var(--amber);box-shadow:0 0 8px var(--amber);animation:blink 1s infinite}
  .agentcard.unavailable .dot{background:#3a4150;box-shadow:none}
  .agentcard b{font-family:var(--display);font-size:11px;letter-spacing:1px}
  .agentcard .status{margin-left:auto;font-family:var(--mono);font-size:9px;color:var(--muted);text-transform:uppercase}
  .agentcard .role{font-size:11px;color:var(--muted);line-height:1.35;margin-top:5px}
  .agentcard .task{font-family:var(--mono);font-size:10px;color:var(--amber-b);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:5px}
  /* scheduled jobs panel */
  .schedform{display:flex;flex-direction:column;gap:6px;margin:6px}
  .schedform select,.schedform input{background:var(--panel);border:1px solid var(--line);border-radius:7px;color:var(--text);font-family:var(--mono);font-size:11px;padding:7px 8px;outline:none}
  .schedform select:focus,.schedform input:focus{border-color:var(--amber-soft)}
  .schedform button{background:var(--amber-soft);border:none;border-radius:7px;color:#0a0c10;font-family:var(--display);font-size:11px;letter-spacing:1px;padding:8px;cursor:pointer}
  .schedform button:hover{filter:brightness(1.12)}
  .schedmsg{font-family:var(--mono);font-size:10px;color:var(--muted);min-height:12px}
  .schedmsg.err{color:var(--amber-b)}
  .schedcard{margin:6px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
  .schedcard.off{opacity:.5}
  .schedcard .top{display:flex;align-items:center;gap:7px}
  .schedcard .kind{font-family:var(--mono);font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--amber-soft)}
  .schedcard .when{margin-left:auto;font-family:var(--mono);font-size:9px;color:var(--muted)}
  .schedcard .spec{font-size:11px;color:var(--text);line-height:1.35;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .schedcard .meta{font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:5px;display:flex;gap:9px;align-items:center}
  .schedcard .meta button{background:none;border:none;color:var(--muted);font-family:var(--mono);font-size:9px;cursor:pointer;padding:0;text-transform:uppercase;letter-spacing:.5px}
  .schedcard .meta button:hover{color:var(--amber-b)}
  .runcard{margin:6px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
  .runcard[open]{border-color:rgba(255,176,0,.28)}
  .runcard summary{list-style:none;cursor:pointer}
  .runcard summary::-webkit-details-marker{display:none}
  .runcard .top{display:flex;align-items:center;gap:7px}
  .runcard .status{font-family:var(--mono);font-size:9px;letter-spacing:1px;text-transform:uppercase;color:var(--ok)}
  .runcard.failed .status{color:#ff5d6c}
  .runcard.stopped .status{color:var(--amber-b)}
  .runcard .when{margin-left:auto;font-family:var(--mono);font-size:9px;color:var(--muted)}
  .runcard .goal{font-size:11px;color:var(--text);line-height:1.35;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .runcard .meta{font-family:var(--mono);font-size:9px;color:var(--muted);margin-top:5px;display:flex;gap:9px;align-items:center}
  .runcard .answer{font-size:11px;color:var(--muted);line-height:1.4;margin-top:8px;white-space:pre-wrap}
  .runstep{border-top:1px solid var(--line);padding-top:7px;margin-top:7px}
  .runstep .meta{margin-top:0;text-transform:uppercase}
  .runstep .cmd{font-family:var(--mono);font-size:10px;color:var(--amber-b);margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .runstep .txt{font-size:11px;color:var(--muted);line-height:1.35;margin-top:4px;white-space:pre-wrap}

  /* leave the hero core visible above the overlay so its colour shows in voice mode */
  .voice{position:absolute;inset:150px 0 0 0;z-index:5;background:rgba(8,9,13,.92);backdrop-filter:blur(7px);display:none;flex-direction:column;align-items:center;justify-content:center;gap:22px;color:var(--ice)}
  .voice.on{display:flex}
  .bars{display:flex;align-items:center;gap:4px;height:30px}
  .bars i{width:4px;height:8px;background:currentColor;border-radius:3px;animation:bars 1s ease-in-out infinite}
  .bars i:nth-child(2){animation-delay:.12s}.bars i:nth-child(3){animation-delay:.24s}.bars i:nth-child(4){animation-delay:.36s}.bars i:nth-child(5){animation-delay:.48s}
  .voice[data-state="thinking"] .bars{opacity:.25}
  .vstate{font-family:var(--display);letter-spacing:4px;text-transform:uppercase;font-size:13px;color:currentColor}
  .vcap{font-family:var(--mono);font-size:9px;letter-spacing:3px;text-transform:uppercase;color:var(--muted)}
  .vtrans{max-width:560px;min-height:52px;text-align:center;color:var(--text);font-size:18px;line-height:1.5;padding:0 20px}
  .vend{font-family:var(--display);letter-spacing:2px;font-size:12px;color:#08090d;background:var(--amber);border:none;border-radius:999px;padding:11px 26px;cursor:pointer}
  .drop{position:absolute;inset:10px;z-index:6;background:rgba(255,176,0,.06);border:2px dashed var(--amber);border-radius:16px;display:none;align-items:center;justify-content:center;font-family:var(--display);letter-spacing:2px;color:var(--amber-b);pointer-events:none}
  .drop.on{display:flex}

  ::-webkit-scrollbar{width:8px}::-webkit-scrollbar-thumb{background:#1b2230;border-radius:8px}
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
  @keyframes blink{0%,100%{opacity:.3}50%{opacity:1}}
  @keyframes ring{from{opacity:.7;transform:scale(1)}to{opacity:0;transform:scale(1.5)}}
  @keyframes spin{to{transform:rotate(360deg)}}@keyframes spinrev{to{transform:rotate(-360deg)}}
  @keyframes corepulse{0%,100%{r:6;opacity:1}50%{r:9;opacity:.65}}
  @keyframes bars{0%,100%{height:8px}50%{height:28px}}
  .r-ring1{transform-origin:50px 50px;animation:spin 9s linear infinite}
  .r-ring2{transform-origin:50px 50px;animation:spinrev 6s linear infinite}
  .r-core{animation:corepulse 2.6s ease-in-out infinite}
  .busy .r-core,.busy .r-mid{stroke:var(--amber)!important;fill:var(--amber)!important}
  .busy .r-ring1,.busy .r-ring2{stroke:var(--amber)!important}
</style>
</head>
<body>
<div class="app">
  <header>
    <svg class="reactor" id="reactor" viewBox="0 0 100 100" aria-hidden="true">
      <circle class="r-ring1" cx="50" cy="50" r="42" fill="none" stroke="#5fd0e6" stroke-width="2" stroke-dasharray="6 10"/>
      <circle class="r-mid" cx="50" cy="50" r="21" fill="none" stroke="#5fd0e6" stroke-width="2.5"/>
      <circle class="r-core" cx="50" cy="50" r="6" fill="#5fd0e6"/>
    </svg>
    <div class="brand"><div class="n">J<b>.</b>A<b>.</b>R<b>.</b>V<b>.</b>I<b>.</b>S</div><div class="s">your local assistant</div></div>
    <div class="sp"></div>
    <span class="pill" id="healthPill" title="System status"><span class="dot" id="healthDot"></span> <span id="healthText">checking…</span></span>
  </header>

  <aside class="left">
    <button class="newchat" id="newChat">+  New chat</button>
    <div class="seclbl">Sessions</div>
    <div id="sessions"></div>
  </aside>

  <main>
    <div class="hero">
      <canvas id="core" width="480" height="236"></canvas>
      <div class="corestate" id="corestate"><span class="blip"></span>J.A.R.V.I.S · <b>online</b></div>
    </div>
    <div class="chat" id="chat">
      <div class="empty" id="empty">
        <svg class="orb" viewBox="0 0 100 100" aria-hidden="true">
          <circle class="r-ring1" cx="50" cy="50" r="42" fill="none" stroke="#ffb000" stroke-width="2" stroke-dasharray="6 10"/>
          <circle class="r-mid" cx="50" cy="50" r="22" fill="none" stroke="#ffb000" stroke-width="2.5"/>
          <circle class="r-core" cx="50" cy="50" r="6" fill="#ffb000"/>
        </svg>
        <h1>How can I help, Jeevan?</h1>
        <p>Talk to me, drop a file of any type, or tap Voice. Simple things run on a fast model; complex questions escalate to a stronger one. I remember things in your Obsidian vault.</p>
        <div class="setupcard" id="setupCard" style="display:none"></div>
        <div class="suggest" id="suggest"></div>
      </div>
    </div>
    <div class="composer">
      <div class="chips" id="chips"></div>
      <div class="box">
        <button class="iconbtn" id="attachBtn" title="Attach a file">&#128206;</button>
        <button class="iconbtn" id="agentBtn" title="Agent mode — let J.A.R.V.I.S plan and act over multiple steps">&#129302;</button>
        <textarea id="ta" rows="1" placeholder="Message J.A.R.V.I.S…  (drop a file, or tap the mic)"></textarea>
        <button class="iconbtn" id="micBtn" title="Dictate">&#127908;</button>
        <button class="sendbtn" id="sendBtn" title="Send">&#10148;</button>
      </div>
      <div class="hint" id="hint">Guarded mode — high-risk actions blocked here. Enter to send · Shift+Enter newline · Esc to stop · Ctrl+K new chat.</div>
    </div>
    <input type="file" id="file" multiple style="display:none" />
    <div class="drop" id="drop">Drop files to attach</div>
    <div class="voice" id="voice" data-state="listening">
      <div class="bars"><i></i><i></i><i></i><i></i><i></i></div>
      <div class="vstate" id="vstate">Listening</div>
      <div class="vcap">subtitles</div>
      <div class="vtrans" id="vtrans">Say something…</div>
      <button class="vend" id="vend">End voice</button>
    </div>
  </main>

  <aside class="right">
    <button class="vbtn wide" id="voiceBtn"><span class="dot"></span> Voice mode</button>
    <details class="conn" open>
      <summary>Connections &amp; system</summary>
      <div id="connlist"></div>
      <div class="seclbl">Usage</div>
      <div id="metrics"></div>
      <div class="seclbl">Memory vault</div>
      <div class="vstat" id="vstat"><span class="d off"></span><span id="vtext">checking…</span></div>
      <div id="notes"></div>
    </details>
    <details class="conn" open>
      <summary>Agent control room</summary>
      <div id="agentSummary"></div>
      <div id="agentList"></div>
    </details>
    <details class="conn" id="schedPanel">
      <summary>Scheduled jobs</summary>
      <div class="schedform">
        <select id="schedKind"><option value="command">command</option><option value="agent">agent</option></select>
        <input id="schedWhen" type="text" placeholder="when — e.g. daily at 08:00" autocomplete="off">
        <input id="schedSpec" type="text" placeholder="command or goal to run" autocomplete="off">
        <button id="schedAdd" type="button">Schedule it</button>
        <div class="schedmsg" id="schedMsg"></div>
      </div>
      <div id="schedList"></div>
    </details>
    <details class="conn" id="runsPanel">
      <summary>Agent runs</summary>
      <div id="runsList"></div>
    </details>
  </aside>
</div>

<script>
  const chat=document.getElementById('chat'), ta=document.getElementById('ta'), sendBtn=document.getElementById('sendBtn'),
        attachBtn=document.getElementById('attachBtn'), fileIn=document.getElementById('file'), chips=document.getElementById('chips'),
        micBtn=document.getElementById('micBtn'), reactor=document.getElementById('reactor'), drop=document.getElementById('drop'),
        voiceBtn=document.getElementById('voiceBtn'), voice=document.getElementById('voice'), vstate=document.getElementById('vstate'),
        vtrans=document.getElementById('vtrans'), vend=document.getElementById('vend'),
        sessionsEl=document.getElementById('sessions'), agentBtn=document.getElementById('agentBtn'),
        hint=document.getElementById('hint');
  let attachments=[], busy=false, voiceActive=false, currentAbort=null, agentMode=false;

  /* ---- AI core animation (Iron-Man / Jarvis style) ---- */
  const coreCanvas=document.getElementById('core'), cctx=coreCanvas.getContext('2d'), corestate=document.getElementById('corestate');
  let coreState='idle', activeTier='fast';
  const TIER_COLORS={fast:'#54e0a0',smart:'#a98bff',ultra:'#ff5d6c'};   // green / violet / red
  const VOICE_COLOR='#5fd0e6';                                          // cyan in voice mode
  const TIER_NAME={fast:'fast model',smart:'complex model',ultra:'deep model · 550B'};
  function coreColor(){
    if(coreState==='listening')return VOICE_COLOR;
    if(coreState==='thinking'||coreState==='speaking')return TIER_COLORS[activeTier]||'#ffb000';
    return voiceActive?VOICE_COLOR:'#5fd0e6';
  }
  // mirror of the server's complexity classifier, to colour the wait by predicted tier
  function estimateTier(text){
    const s=(text||'').toLowerCase(), w=(text||'').split(/\s+/).length;
    if(w>70||/in.?depth|step.?by.?step|comprehensive|thorough|rigorous|deep dive|detailed analysis|prove|derive|full implementation|design a system|architecture|from scratch|think hard|deeply|big model|ultra/.test(s))return 'ultra';
    if(w>35||/explain|why|analy|compare|design|reason|debug|refactor|optimi|trade.?off|write code|implement|algorithm|strategy|pros and cons|evaluate|critique/.test(s))return 'smart';
    return 'fast';
  }
  function setCore(s,tier){
    coreState=s; if(tier)activeTier=tier;
    const col=coreColor();
    let label;
    if(s==='thinking')label= activeTier==='ultra' ? 'reasoning · <b>'+TIER_NAME.ultra+'</b> · this can take a moment' : 'analyzing · <b>'+TIER_NAME[activeTier]+'</b>';
    else if(s==='speaking')label='speaking · <b>'+TIER_NAME[activeTier]+'</b>';
    else if(s==='listening')label='<b>listening…</b>';
    else label='J.A.R.V.I.S · <b>online</b>';
    corestate.className='corestate';corestate.style.color=col;corestate.innerHTML='<span class="blip"></span>'+label;
    voice.style.color=col;  // overlay bars + state text follow the same colour
  }
  function fitCanvas(){const r=coreCanvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;coreCanvas.width=r.width*dpr;coreCanvas.height=r.height*dpr;cctx.setTransform(dpr,0,0,dpr,0,0);}
  window.addEventListener('resize',fitCanvas);
  function drawCore(t){
    const r=coreCanvas.getBoundingClientRect(),w=r.width,h=r.height; if(!w)return;
    cctx.clearRect(0,0,w,h); const cx=w/2,cy=h/2,R=Math.min(w,h)/2-8;
    const col=coreColor();
    const spd=coreState==='thinking'?2.4:coreState==='speaking'?1.6:0.6;
    const pulse=(Math.sin(t*(coreState==='thinking'?6:3))+1)/2;
    const glow=cctx.createRadialGradient(cx,cy,0,cx,cy,R*1.15);
    glow.addColorStop(0,col+'30');glow.addColorStop(.45,col+'12');glow.addColorStop(1,'transparent');
    cctx.fillStyle=glow;cctx.beginPath();cctx.arc(cx,cy,R*1.15,0,7);cctx.fill();
    for(let i=0;i<3;i++){const rr=R*(0.5+i*0.17),off=t*spd*(i%2?-1:1)+i*1.3;cctx.strokeStyle=col;cctx.globalAlpha=.55-i*.12;cctx.lineWidth=1.6;
      for(let a=0;a<3;a++){const s=off+a*2.094;cctx.beginPath();cctx.arc(cx,cy,rr,s,s+1.05);cctx.stroke();}}
    cctx.globalAlpha=.35;cctx.lineWidth=1;cctx.strokeStyle=col;
    for(let k=0;k<56;k++){const a=t*0.3+k*0.112,r1=R*0.88,r2=R*0.95;cctx.beginPath();cctx.moveTo(cx+r1*Math.cos(a),cy+r1*Math.sin(a));cctx.lineTo(cx+r2*Math.cos(a),cy+r2*Math.sin(a));cctx.stroke();}
    const np=coreState==='thinking'?16:9;cctx.globalAlpha=.85;
    for(let p=0;p<np;p++){const a=t*spd*1.3+p*(6.283/np),rr=R*0.72,x=cx+rr*Math.cos(a),y=cy+rr*Math.sin(a);cctx.fillStyle=col;cctx.beginPath();cctx.arc(x,y,1.7,0,7);cctx.fill();}
    cctx.globalAlpha=1;
    if(coreState==='speaking'){cctx.strokeStyle=col;cctx.lineWidth=2;cctx.beginPath();for(let a=0;a<=72;a++){const ang=a/72*6.283,amp=R*0.32+Math.sin(ang*7+t*9)*R*0.06;const x=cx+amp*Math.cos(ang),y=cy+amp*Math.sin(ang);a?cctx.lineTo(x,y):cctx.moveTo(x,y);}cctx.closePath();cctx.stroke();}
    else if(coreState==='listening'){const rp=(t*0.6)%1;cctx.strokeStyle=col;cctx.globalAlpha=1-rp;cctx.lineWidth=2;cctx.beginPath();cctx.arc(cx,cy,R*0.3+rp*R*0.5,0,7);cctx.stroke();cctx.globalAlpha=1;}
    const cr=R*0.17*(0.82+pulse*0.32);const cg=cctx.createRadialGradient(cx,cy,0,cx,cy,cr*2.2);cg.addColorStop(0,'#ffffff');cg.addColorStop(.4,col);cg.addColorStop(1,'transparent');
    cctx.fillStyle=cg;cctx.beginPath();cctx.arc(cx,cy,cr*2.2,0,7);cctx.fill();cctx.fillStyle='#fff';cctx.beginPath();cctx.arc(cx,cy,cr*0.45,0,7);cctx.fill();
  }
  function coreLoop(){drawCore(performance.now()/1000);requestAnimationFrame(coreLoop);}
  fitCanvas();coreLoop();

  /* markdown */
  function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function inline(s){s=esc(s);
    s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
    s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
    s=s.replace(/(^|[^\w])\*([^*]+)\*/g,'$1<em>$2</em>');
    s=s.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2">$1</a>');
    return s;}
  function mdToHtml(src){
    const fences=[]; src=String(src).replace(/```(\w*)\n?([\s\S]*?)```/g,(m,l,c)=>{fences.push(c);return '@@F'+(fences.length-1)+'@@';});
    let html='',list=null; const close=()=>{if(list){html+='</'+list+'>';list=null;}};
    for(const raw of src.split('\n')){
      const t=raw.trim();
      let m;
      if(/^@@F\d+@@$/.test(t)){close();html+='<pre><code>'+esc(fences[+t.slice(3,-2)])+'</code></pre>';continue;}
      if((m=raw.match(/^(#{1,3})\s+(.*)$/))){close();const lv=Math.min(m[1].length+2,4);html+='<h'+lv+'>'+inline(m[2])+'</h'+lv+'>';continue;}
      if((m=raw.match(/^\s*[-*]\s+(.*)$/))){if(list!=='ul'){close();html+='<ul>';list='ul';}html+='<li>'+inline(m[1])+'</li>';continue;}
      if((m=raw.match(/^\s*\d+\.\s+(.*)$/))){if(list!=='ol'){close();html+='<ol>';list='ol';}html+='<li>'+inline(m[1])+'</li>';continue;}
      if(t===''){close();continue;}
      close();html+='<p>'+inline(raw)+'</p>';
    }
    close();return html;
  }

  /* suggestions */
  const SUG=[["Get oriented","What can you do?"],["Summarize","Summarize the README"],["Research","Research local-first AI agents"],["Memory","What do you remember about me?"]];
  const suggest=document.getElementById('suggest');
  SUG.forEach(([t,q])=>{const c=document.createElement('div');c.className='scard';c.innerHTML='<b>'+t+'</b>'+q;c.onclick=()=>send(q);suggest.appendChild(c);});

  /* sessions (localStorage) */
  let sessions=JSON.parse(localStorage.getItem('jarvis_sessions')||'[]'), current=null;
  function saveSessions(){localStorage.setItem('jarvis_sessions',JSON.stringify(sessions.slice(0,40)));}
  function renderSessions(){sessionsEl.innerHTML='';sessions.forEach(s=>{const b=document.createElement('button');b.className='sess'+(s.id===current?' active':'');b.textContent=s.title||'New chat';b.onclick=()=>loadSession(s.id);sessionsEl.appendChild(b);});}
  function newSession(){const s={id:'s'+Date.now(),title:'',msgs:[]};sessions.unshift(s);current=s.id;saveSessions();renderSessions();chat.innerHTML='';chat.appendChild(emptyEl());}
  function curSession(){return sessions.find(s=>s.id===current);}
  function loadSession(id){current=id;const s=curSession();chat.innerHTML='';if(!s||!s.msgs.length){chat.appendChild(emptyEl());}else{s.msgs.forEach(m=>renderMsg(m.role,m.text,m.atts));}renderSessions();}
  let emptyNode=document.getElementById('empty');
  function emptyEl(){return emptyNode.cloneNode(true);}
  document.getElementById('newChat').onclick=newSession;

  /* messages */
  function clearEmpty(){const e=chat.querySelector('.empty');if(e)e.remove();}
  function renderMsg(role,text,atts){
    clearEmpty();
    const m=document.createElement('div');m.className='msg '+role;
    m.innerHTML='<div class="av">'+(role==='user'?'YOU':'J')+'</div><div class="content"><div class="who">'+(role==='user'?'You':'J.A.R.V.I.S')+'</div><div class="md"></div></div>';
    m.querySelector('.md').innerHTML=role==='user'?esc(text).replace(/\n/g,'<br>'):mdToHtml(text);
    if(atts&&atts.length){const box=document.createElement('div');atts.forEach(a=>{const s=document.createElement('span');s.className='att';s.innerHTML='<span class="ic">&#128196;</span>'+a;box.appendChild(s);});m.querySelector('.content').appendChild(box);}
    chat.appendChild(m);chat.scrollTop=chat.scrollHeight;return m;
  }
  function thinking(tier){clearEmpty();const m=document.createElement('div');m.className='msg bot';const note=tier==='ultra'?' <span style="color:#ff5d6c;font-size:11px">thinking on the 550B model — this can take ~45-60s</span>':tier==='smart'?' <span style="color:#a98bff;font-size:11px">on the complex model…</span>':'';m.innerHTML='<div class="av">J</div><div class="content"><div class="who">J.A.R.V.I.S</div><div class="md"><span class="dots"><span></span><span></span><span></span></span>'+note+'</div></div>';chat.appendChild(m);chat.scrollTop=chat.scrollHeight;return m;}
  function setBusy(b,tier){busy=b;reactor.classList.toggle('busy',b);setCore(b?'thinking':(voiceActive?'listening':'idle'),tier);
    sendBtn.innerHTML=b?'&#9632;':'&#10148;'; sendBtn.title=b?'Stop':'Send'; sendBtn.classList.toggle('stop',b);}
  function stopGen(){if(currentAbort){try{currentAbort.abort();}catch(e){}}}

  /* composer */
  function auto(){ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,150)+'px';}
  ta.addEventListener('input',auto);
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(ta.value);}});
  sendBtn.onclick=()=>{ if(busy){stopGen();} else {send(ta.value);} };
  // agent-mode toggle
  function setAgentMode(on){agentMode=on;agentBtn.classList.toggle('on',on);
    ta.placeholder=on?'Give J.A.R.V.I.S a goal — it will plan and act over multiple steps…':'Message J.A.R.V.I.S…  (drop a file to auto-process it, or tap the mic)';
    hint.textContent=on?'Agent mode — plans, runs tools, and observes step by step. High-risk actions still blocked here.':'Guarded mode — high-risk actions blocked here. Enter to send · Shift+Enter newline · Esc to stop · Ctrl+K new chat.';}
  agentBtn.onclick=()=>setAgentMode(!agentMode);
  // keyboard shortcuts
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){ if(busy)stopGen(); else if(voiceActive)endVoice(); }
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){ e.preventDefault(); newSession(); ta.focus(); }
  });

  async function uploadFile(file){
    const data=await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result);fr.readAsDataURL(file);});
    const res=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,data})});
    const d=await res.json(); if(d.ok){attachments.push(d);renderChips();}
  }
  function renderChips(){chips.innerHTML='';attachments.forEach((a,i)=>{const c=document.createElement('div');c.className='chip';c.innerHTML='<span class="ic">&#128196;</span>'+a.name+' <span class="rm">&times;</span>';c.querySelector('.rm').onclick=()=>{attachments.splice(i,1);renderChips();};chips.appendChild(c);});}
  attachBtn.onclick=()=>fileIn.click();
  fileIn.onchange=()=>{[...fileIn.files].forEach(uploadFile);fileIn.value='';};
  ['dragenter','dragover'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('on');}));
  document.addEventListener('dragleave',ev=>{if(ev.clientX===0&&ev.clientY===0)drop.classList.remove('on');});
  document.addEventListener('drop',ev=>{ev.preventDefault();drop.classList.remove('on');if(ev.dataTransfer&&ev.dataTransfer.files)[...ev.dataTransfer.files].forEach(uploadFile);});

  async function send(text){
    text=(text||'').trim(); if((!text&&!attachments.length)||busy)return;
    if(agentMode&&text){return runAgent(text);}
    if(!current)newSession();
    const sent=attachments.slice(), attNames=sent.map(a=>a.name);
    const s=curSession();
    const history=s?s.msgs.slice(-12).map(m=>({role:m.role==='bot'?'assistant':'user',text:m.text||''})):[];
    renderMsg('user',text||'(sent attachment)',attNames);
    if(s){s.msgs.push({role:'user',text:text||'(sent attachment)',atts:attNames});if(!s.title)s.title=(text||'Attachment').slice(0,32);saveSessions();renderSessions();}
    const predicted=estimateTier(text); activeTier=predicted;
    ta.value='';auto();attachments=[];renderChips();setBusy(true,predicted);
    const node=renderMsg('bot',''); const md=node.querySelector('.md');
    md.innerHTML='<span class="dots"><span></span><span></span><span></span></span>'+(predicted==='ultra'?' <span style="color:#ff5d6c;font-size:11px">on the 550B model — this can take a moment</span>':predicted==='smart'?' <span style="color:#a98bff;font-size:11px">on the complex model…</span>':'');
    let reply='', streamed='';
    currentAbort=new AbortController();
    try{
      const speakStream=voiceActive; if(speakStream)voiceTurnReset();
      const r=await fetch('/api/stream',{method:'POST',headers:{'Content-Type':'application/json'},signal:currentAbort.signal,body:JSON.stringify({command:text,attachments:sent.map(a=>a.path),history,voice:speakStream})});
      const reader=r.body.getReader(), dec=new TextDecoder(); let buf='', done=null;
      while(true){
        const {done:fin,value}=await reader.read(); if(fin)break;
        buf+=dec.decode(value,{stream:true}); let i;
        while((i=buf.indexOf('\n\n'))>=0){
          const line=buf.slice(0,i); buf=buf.slice(i+2);
          if(!line.startsWith('data:'))continue;
          let ev; try{ev=JSON.parse(line.slice(5).trim());}catch(e){continue;}
          if(ev.type==='token'){streamed+=ev.text;md.innerHTML=mdToHtml(streamed);chat.scrollTop=chat.scrollHeight;}
          else if(ev.type==='tts'){if(speakStream)enqueueTTS(ev.text);}
          else if(ev.type==='done'){done=ev;}
        }
      }
      const d=done||{ok:true,message:streamed,data:{}};
      reply=d.message||streamed||'(no output)';
      if(!d.ok)node.classList.add('err');
      md.innerHTML=mdToHtml(reply);
      activeTier=(d.data&&d.data.planner&&d.data.planner.model)||predicted;
      const data=Object.assign({},d.data||{});['planner','messages','sources','fields','fill_preview','field_mappings','results'].forEach(k=>delete data[k]);
      if(Object.keys(data).length){const det=document.createElement('details');det.className='det';det.innerHTML='<summary>details</summary>';const pre=document.createElement('div');pre.className='data';pre.textContent=JSON.stringify(data,null,2);det.appendChild(pre);node.querySelector('.content').appendChild(det);}
      const ss=curSession();if(ss){ss.msgs.push({role:'bot',text:reply});saveSessions();}
      loadVault();
    }catch(err){
      if(err&&err.name==='AbortError'){reply=streamed;md.innerHTML=mdToHtml(streamed||'_(stopped)_');const ss=curSession();if(ss&&streamed){ss.msgs.push({role:'bot',text:streamed});saveSessions();}}
      else{md.innerHTML='';node.classList.add('err');md.textContent='Connection error: '+err;}
    }
    finally{currentAbort=null;setBusy(false);ta.focus();loadAgents();if(voiceActive)voiceTurnDone(reply);}
    return reply;
  }

  /* autonomous agent mode — streams plan/act/observe steps into a live trace */
  function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
  async function runAgent(goal){
    if(!current)newSession();
    const s=curSession();
    renderMsg('user',goal); if(s){s.msgs.push({role:'user',text:goal});if(!s.title)s.title=goal.slice(0,32);saveSessions();renderSessions();}
    ta.value='';auto();setBusy(true,'smart');
    const node=renderMsg('bot',''); const md=node.querySelector('.md');
    const trace=document.createElement('div');trace.className='trace';
    trace.innerHTML='<div class="thead"><span class="gdot"></span> AGENT · planning…</div>';
    md.innerHTML='';md.appendChild(trace);
    let reply='';
    currentAbort=new AbortController();
    try{
      const r=await fetch('/api/agent',{method:'POST',headers:{'Content-Type':'application/json'},signal:currentAbort.signal,body:JSON.stringify({goal})});
      const reader=r.body.getReader(), dec=new TextDecoder(); let buf='';
      while(true){
        const {done:fin,value}=await reader.read(); if(fin)break;
        buf+=dec.decode(value,{stream:true}); const parts=buf.split('\n\n'); buf=parts.pop();
        for(const p of parts){const line=p.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;
          let ev;try{ev=JSON.parse(line.slice(6));}catch(e){continue;}
          if(ev.type==='step'){const st=ev.step;const row=document.createElement('div');row.className='tstep '+(st.status||'');
            row.innerHTML='<div class="tn">step '+(st.index+1)+(st.thought?' · '+esc(st.thought):'')+'</div>'+
              '<span class="tcmd">'+esc(st.command)+'</span><div class="tobs">'+esc(st.message)+'</div>';
            trace.appendChild(row);chat.scrollTop=chat.scrollHeight;
            trace.querySelector('.thead').innerHTML='<span class="gdot"></span> AGENT · '+(st.index+1)+' step(s)…';}
          else if(ev.type==='done'){reply=ev.message||'';trace.classList.add(ev.ok?'done':'fail');
            trace.querySelector('.thead').innerHTML='<span class="gdot"></span> AGENT · '+(ev.ok?'done':'stopped');
            const ans=document.createElement('div');ans.className='md';ans.style.marginTop='8px';ans.innerHTML=mdToHtml(reply);
            node.querySelector('.content').appendChild(ans);}
        }
      }
      const ss=curSession();if(ss&&reply){ss.msgs.push({role:'bot',text:reply});saveSessions();}
      loadVault();
    }catch(err){
      if(err&&err.name==='AbortError'){trace.classList.add('fail');trace.querySelector('.thead').innerHTML='<span class="gdot"></span> AGENT · stopped';}
      else{node.classList.add('err');const e=document.createElement('div');e.className='md';e.textContent='Agent error: '+err;node.querySelector('.content').appendChild(e);}
    }
    finally{currentAbort=null;setBusy(false);ta.focus();loadAgents();if(voiceActive&&reply)speak(reply);}
    return reply;
  }

  /* connections dropdown */
  const PLANNER='{{PLANNER}}', SMART='{{SMART}}', ULTRA='{{ULTRA}}', VISION='{{VISION}}';
  const conn={fast:[PLANNER!=='heuristic'?'ok':'off',PLANNER],smart:[SMART!=='—'?'ok':'off',SMART],ultra:[ULTRA!=='—'?'ok':'off',ULTRA],vision:[VISION!=='—'?'ok':'off',VISION],vault:['off','checking…'],gpu:['off','n/a']};
  function renderConn(){
    const rows=[['LLM · simple',conn.fast],['LLM · complex',conn.smart],['LLM · very complex',conn.ultra],['Vision',conn.vision],['Obsidian vault',conn.vault],['GPU',conn.gpu]];
    document.getElementById('connlist').innerHTML=rows.map(([k,[s,v]])=>'<div class="crow"><span class="d '+(s==='ok'?'':s)+'"></span><span class="k">'+k+'</span><span class="v">'+v+'</span></div>').join('');
  }
  renderConn();

  /* metrics */
  function bar(label,val,unit,cls){return '<div class="metric"><div class="top"><span>'+label+'</span><b>'+(val==null?'n/a':val+unit)+'</b></div><div class="bar '+(cls||'')+'"><i style="width:'+(val==null?0:Math.min(val,100))+'%"></i></div></div>';}
  async function loadMetrics(){try{const m=await (await fetch('/api/metrics')).json();let h=bar('CPU',m.cpu_percent,'%');h+=bar('Memory',m.ram_percent,'%');(m.gpus||[]).forEach(g=>{h+=bar('GPU · '+g.name.replace(/NVIDIA |GeForce /g,''),g.util_percent,'%','g');h+=bar('VRAM',g.mem_total_mb?Math.round(g.mem_used_mb/g.mem_total_mb*100):null,'%','g');});document.getElementById('metrics').innerHTML=h;
    if(m.gpus&&m.gpus.length){conn.gpu=['ok',m.gpus[0].name.replace(/NVIDIA |GeForce /g,'')];}else{conn.gpu=['warn','run as admin'];}renderConn();}catch(e){}}
  setInterval(loadMetrics,5000);loadMetrics();

  /* vault */
  async function loadVault(){try{const v=await (await fetch('/api/vault')).json();const d=document.querySelector('#vstat .d');const t=document.getElementById('vtext');if(v.ok){d.classList.remove('off');t.textContent=(v.status.note_count||0)+' notes connected';conn.vault=['ok',(v.status.note_count||0)+' notes'];}else{d.classList.add('off');t.textContent='not connected';conn.vault=['off','not connected'];}renderConn();const notes=document.getElementById('notes');notes.innerHTML='';(v.notes||[]).slice(0,8).forEach(n=>{const e=document.createElement('div');e.className='note';e.textContent=n.name;notes.appendChild(e);});}catch(e){}}
  loadVault();

  /* agent control room */
  async function loadAgents(){try{const r=await fetch('/api/agents');const d=await r.json();if(!d.ok)return;const s=d.control_room.summary;
    document.getElementById('agentSummary').innerHTML='<div class="agentSummary"><div class="agentStat"><b>'+s.working+'</b><span>working</span></div><div class="agentStat"><b>'+s.idle+'</b><span>idle</span></div><div class="agentStat"><b>'+s.available+'</b><span>available</span></div></div>';
    document.getElementById('agentList').innerHTML=d.control_room.agents.map(a=>{const done=(a.completed||0)>0?' · ✓'+a.completed:'';const fail=(a.failed||0)>0?' ✗'+a.failed:'';return '<button class="agentcard '+a.status+'" data-agent="'+esc(a.id)+'"><div class="top"><span class="dot"></span><b>'+esc(a.name)+'</b><span class="status">'+esc(a.status)+done+fail+'</span></div><div class="role">'+esc(a.role)+'</div><div class="task">'+esc(a.current_task||a.last_message||'Ready.')+'</div></button>';}).join('');
    document.querySelectorAll('.agentcard').forEach(btn=>{btn.onclick=()=>send('agent '+btn.dataset.agent);});
  }catch(e){}}
  setInterval(loadAgents,2500);loadAgents();

  /* scheduled jobs */
  function renderSchedule(jobs){
    const list=document.getElementById('schedList');
    if(!jobs||!jobs.length){list.innerHTML='<div class="note">No scheduled jobs yet.</div>';return;}
    list.innerHTML=jobs.map(j=>{
      const last=j.last_run_at?('· last '+esc(String(j.last_run_at).slice(0,16).replace('T',' '))+(j.last_status?' ('+esc(j.last_status)+')':'')):'· not run yet';
      const toggle=j.enabled?'disable':'enable';
      return '<div class="schedcard'+(j.enabled?'':' off')+'"><div class="top"><span class="kind">'+esc(j.kind)+'</span><span class="when">'+esc(j.schedule_text||'')+'</span></div>'+
        '<div class="spec">'+esc(j.spec||'')+'</div>'+
        '<div class="meta"><span>'+(j.run_count||0)+' run(s) '+last+'</span>'+
        '<button data-act="'+toggle+'" data-id="'+j.id+'">'+toggle+'</button>'+
        '<button data-act="remove" data-id="'+j.id+'">remove</button></div></div>';
    }).join('');
    list.querySelectorAll('button[data-act]').forEach(b=>{b.onclick=()=>postSched({action:b.dataset.act,id:b.dataset.id});});
  }
  async function loadSchedule(){try{const d=await (await fetch('/api/schedule')).json();renderSchedule(d.jobs);}catch(e){}}
  async function postSched(payload){
    const msg=document.getElementById('schedMsg');msg.className='schedmsg';msg.textContent='…';
    try{const r=await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const d=await r.json();msg.textContent=d.message||'';msg.className='schedmsg'+(d.ok?'':' err');renderSchedule(d.jobs);
      if(d.ok&&payload.action==='add'){document.getElementById('schedWhen').value='';document.getElementById('schedSpec').value='';}
    }catch(e){msg.textContent='Could not reach the scheduler.';msg.className='schedmsg err';}
  }
  document.getElementById('schedAdd').onclick=()=>postSched({action:'add',kind:document.getElementById('schedKind').value,
    when:document.getElementById('schedWhen').value,spec:document.getElementById('schedSpec').value});
  document.getElementById('schedSpec').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('schedAdd').click();});
  document.getElementById('schedPanel').addEventListener('toggle',function(){if(this.open)loadSchedule();});
  setInterval(()=>{if(document.getElementById('schedPanel').open)loadSchedule();},15000);

  /* agent runs */
  function renderAgentRuns(runs){
    const list=document.getElementById('runsList');
    if(!runs||!runs.length){list.innerHTML='<div class="note">No agent runs yet.</div>';return;}
    list.innerHTML=runs.slice().reverse().map(r=>{
      const status=String(r.status||'');
      const cls=['ok','failed','stopped'].includes(status)?status:'';
      const when=r.created_at?esc(String(r.created_at).slice(0,16).replace('T',' ')):'';
      const summary=esc(String(r.ok_count||0))+' ok / '+esc(String(r.failed_count||0))+' failed across '+esc(String(r.step_count||0))+' steps';
      const steps=(r.steps||[]).map(s=>{
        const idx=esc(String(s.index||0));
        const stepStatus=esc(String(s.status||''));
        const cmd=esc(String(s.command||''));
        const thought=esc(String(s.thought||''));
        const msg=esc(String(s.message||''));
        return '<div class="runstep"><div class="meta"><span>'+idx+' &middot; '+stepStatus+'</span></div>'+
          '<div class="cmd">'+cmd+'</div><div class="txt">'+thought+(thought&&msg?'<br>':'')+msg+'</div></div>';
      }).join('');
      const answer=r.final_answer?'<div class="answer">'+esc(String(r.final_answer))+'</div>':'';
      return '<details class="runcard '+cls+'"><summary><div class="top"><span class="status">'+esc(status)+'</span><span class="when">'+when+'</span></div>'+
        '<div class="goal">'+esc(String(r.goal||''))+'</div><div class="meta"><span>'+summary+'</span></div></summary>'+answer+steps+'</details>';
    }).join('');
  }
  async function loadAgentRuns(){try{const d=await (await fetch('/api/agent-runs')).json();renderAgentRuns(d.runs);}catch(e){}}
  document.getElementById('runsPanel').addEventListener('toggle',function(){if(this.open)loadAgentRuns();});
  setInterval(()=>{if(document.getElementById('runsPanel').open)loadAgentRuns();},10000);

  /* health / first-run */
  async function loadHealth(){try{const h=await (await fetch('/api/health')).json();
    const pill=document.getElementById('healthPill'),txt=document.getElementById('healthText');
    const label={ok:'online',degraded:'AI unreachable',setup:'setup needed'}[h.overall]||'online';
    pill.className='pill '+h.overall; txt.textContent=label;
    pill.title=`AI: ${h.llm.configured?(h.llm.reachable===false?'configured but unreachable':'connected'):'not configured'} · vault: ${h.vault.connected?'connected':'off'} · email: ${h.email.configured?'on':'off'}`;
    const card=document.getElementById('setupCard');
    if(card){
      if(h.overall==='setup'){card.style.display='';card.innerHTML='<b>Connect your AI</b>No language model is configured, so I can only use offline routing. Add an OpenAI-compatible key to your <code>.env</code> (e.g. <code>OPENAI_API_KEY</code>, <code>OPENAI_MODEL</code>, <code>OPENAI_BASE_URL</code>) and restart. File, search, and memory commands still work without it.';}
      else if(h.overall==='degraded'){card.style.display='';card.innerHTML='<b>AI endpoint unreachable</b>Your model is configured but I can\'t reach it right now — check your network or API key. Offline commands (files, search, memory) still work.';}
      else card.style.display='none';
    }
  }catch(e){}}
  setInterval(loadHealth,12000);loadHealth();

  /* voice */
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition; let rec=null,dictating=false;
  if(!SR)micBtn.style.display='none';
  // pick the most human-sounding installed voice (Edge "Natural"/"Online" neural voices)
  let ttsVoice=null;
  function pickVoice(){
    const vs=speechSynthesis.getVoices(); if(!vs.length)return;
    const prefer=['Natural','Online','Aria','Jenny','Ava','Emma','Sonia','Libby','Michelle','Andrew','Guy','Ryan','Google US English'];
    for(const p of prefer){const v=vs.find(x=>x.name.includes(p)&&/^en/i.test(x.lang));if(v){ttsVoice=v;return;}}
    ttsVoice=vs.find(x=>/^en/i.test(x.lang))||vs[0];
  }
  speechSynthesis.onvoiceschanged=pickVoice; pickVoice();
  micBtn.onclick=()=>{if(!SR)return;if(dictating){rec&&rec.stop();return;}rec=new SR();rec.lang='en-US';rec.interimResults=true;dictating=true;micBtn.classList.add('live');const base=ta.value?ta.value+' ':'';rec.onresult=e=>{let t='';for(let i=e.resultIndex;i<e.results.length;i++)t+=e.results[i][0].transcript;ta.value=base+t;auto();};rec.onend=()=>{dictating=false;micBtn.classList.remove('live');};rec.start();};
  voiceBtn.onclick=()=>{if(!SR){alert('Speech recognition is not available here.');return;}voiceActive?endVoice():startVoice();};
  vend.onclick=endVoice;
  function vSet(st,l){voice.dataset.state=st;vstate.textContent=l;}
  function startVoice(){voiceActive=true;voice.classList.add('on');voiceBtn.classList.add('on');listen();}
  function endVoice(){voiceActive=false;voice.classList.remove('on');voiceBtn.classList.remove('on');setCore('idle');ttsQueue=[];speaking=false;streamComplete=true;try{rec&&rec.stop();}catch(e){}try{speechSynthesis.cancel();}catch(e){}}
  let recognizing=false, speaking=false, lastSpoken='';
  // streaming speech: sentences arrive as `tts` events mid-generation and are spoken
  // one at a time so the first sentence plays while the rest is still being written.
  let ttsQueue=[], streamComplete=false, spokeAny=false;
  function voiceTurnReset(){ttsQueue=[];streamComplete=false;spokeAny=false;speaking=false;}   // no cancel(): Chrome drops the next speak() if cancel() ran just before it
  function voiceTurnDone(reply){
    streamComplete=true;
    if(!spokeAny){ if(reply){enqueueTTS(reply);} else { afterTurn(); } }   // no streamed sentences (e.g. a tool result) — speak the whole reply
    else pumpTTS();                                                        // resume check in case the queue already drained
  }
  function enqueueTTS(text){const t=(text||'').trim();if(!t)return;spokeAny=true;ttsQueue.push(t);pumpTTS();}
  function pumpTTS(){
    if(!voiceActive){ttsQueue=[];return;}
    if(speaking)return;                                  // one utterance at a time
    if(!ttsQueue.length){if(streamComplete)afterTurn();return;}
    speakChunk(ttsQueue.shift());
  }
  function afterTurn(){if(voiceActive)setTimeout(()=>{if(voiceActive&&!speaking&&!ttsQueue.length)listen();},500);else setCore('idle');}  // echo-guard delay
  // reject recognized speech that is really the agent hearing its own voice
  function isEcho(q){
    const norm=s=>s.toLowerCase().replace(/[^a-z0-9 ]/g,'').replace(/\s+/g,' ').trim();
    const a=norm(q), b=norm(lastSpoken); if(!a||!b)return false;
    if(b.includes(a)||a.includes(b))return true;
    const bw=new Set(b.split(' ')), aw=a.split(' ');
    return aw.length>1 && aw.filter(w=>bw.has(w)).length/aw.length>0.6;
  }
  function listen(){
    if(!voiceActive||recognizing||speaking)return;      // never listen while speaking
    setCore('listening');vSet('listening','Listening');vtrans.textContent='Listening — speak now';
    // continuous + our own silence timer: Chrome's built-in end-of-speech detection can
    // wait many seconds before firing onend, which feels like a long "thinking" pause.
    // We finalize ~1.2s after the user stops talking so the turn snaps to the reply.
    rec=new SR();rec.lang='en-US';rec.interimResults=true;rec.continuous=true;recognizing=true;
    let fin='',heard=false,silence=null;
    const finalize=()=>{try{rec.stop();}catch(e){}};
    rec.onresult=e=>{let t='';for(let i=0;i<e.results.length;i++)t+=e.results[i][0].transcript;heard=true;fin=t;vtrans.textContent=t;clearTimeout(silence);silence=setTimeout(finalize,1200);};
    rec.onerror=()=>{};
    rec.onend=async()=>{
      clearTimeout(silence);recognizing=false; if(!voiceActive||speaking)return;
      const q=(fin||(heard?vtrans.textContent:'')||'').trim();
      if(q.length<2||isEcho(q)){listen();return;}      // ignore noise, empty, or our own echo
      vSet('thinking','Thinking');
      await send(q);                                    // send() streams sentences back via voiceTurnDone; it drives speech, not us
    };
    try{rec.start();}catch(e){recognizing=false;}
  }
  function speakChunk(text){try{
    speaking=true; try{rec&&rec.stop();}catch(e){}      // never listen while we talk (avoids echo)
    try{speechSynthesis.resume();}catch(e){}            // defeat Chrome's "paused engine" bug that silently swallows speak()
    if(!ttsVoice)pickVoice();
    const clean=text.replace(/[`*#_>\[\]()]/g,'').replace(/\s+/g,' ').trim();
    if(!clean){speaking=false;pumpTTS();return;}
    lastSpoken=(lastSpoken?lastSpoken+' '+clean:clean).slice(-600);        // accumulate spoken text so we can reject the echo
    const u=new SpeechSynthesisUtterance(clean.slice(0,800));if(ttsVoice)u.voice=ttsVoice;u.rate=1.0;u.pitch=1.0;
    setCore('speaking');vSet('speaking','Speaking');
    if(voiceActive)vtrans.textContent=clean.slice(0,240);                  // static, readable subtitles
    u.onboundary=(e)=>{if(voiceActive&&e.charIndex!=null){const end=e.charIndex+(e.charLength||0);const start=Math.max(0,end-240);vtrans.textContent=(start>0?'…':'')+clean.slice(start,start+240);}};
    u.onend=()=>{speaking=false;pumpTTS();};            // next sentence, or resume listening when the queue drains
    u.onerror=()=>{speaking=false;pumpTTS();};
    speechSynthesis.speak(u);
  }catch(e){speaking=false;pumpTTS();}}

  renderSessions(); ta.focus();
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

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, default=str).encode("utf-8"), "application/json")

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            page = (
                PAGE.replace("{{PLANNER}}", _planner_label())
                .replace("{{SMART}}", _smart_label())
                .replace("{{ULTRA}}", _ultra_label())
                .replace("{{VISION}}", _vision_label())
            )
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/api/health":
            self._json(200, system_health(_orchestrator, _LLM_STATUS.get("reachable"), _CONFIG))
        elif self.path == "/api/metrics":
            self._json(200, system_metrics())
        elif self.path == "/api/agents":
            self._json(200, {"ok": True, "control_room": _json_safe(_orchestrator.control_room.snapshot())})
        elif self.path == "/api/vault":
            status = asyncio.run(_orchestrator.handle("notes status"))
            listing = asyncio.run(_orchestrator.handle("notes list"))
            self._json(
                200,
                {
                    "ok": status.ok,
                    "message": status.message,
                    "status": _json_safe(status.data),
                    "notes": listing.data.get("notes", []),
                },
            )
        elif self.path == "/api/schedule":
            self._json(200, _schedule_snapshot())
        elif self.path == "/api/agent-runs":
            self._json(200, _agent_runs_snapshot())
        else:
            self._send(404, b"not found", "text/plain")

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("payload too large")
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_POST(self) -> None:
        if self.path == "/api/upload":
            self._handle_upload()
        elif self.path == "/api/command":
            self._handle_command()
        elif self.path == "/api/stream":
            self._handle_stream()
        elif self.path == "/api/agent":
            self._handle_agent()
        elif self.path == "/api/schedule":
            self._handle_schedule()
        else:
            self._send(404, b"not found", "text/plain")

    def _command_with_attachments(self, payload: dict) -> tuple[str, list]:
        command = _compose_command(str(payload.get("command", "")), payload.get("attachments") or [])
        history = payload.get("history") or []
        return command, history if isinstance(history, list) else []

    def _handle_stream(self) -> None:
        try:
            payload = self._read_json()
            command, history = self._command_with_attachments(payload)
            voice = bool(payload.get("voice"))
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj: dict) -> None:
            try:
                self.wfile.write(("data: " + json.dumps(obj, default=str) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # In voice mode, split the streamed reply into sentences and push each as a
        # `tts` event the moment it completes, so the browser starts speaking the
        # first sentence instead of waiting for the whole reply (low time-to-audio).
        chunker = SpeechChunker() if voice else None

        def on_token(token: str) -> None:
            emit({"type": "token", "text": token})
            if chunker is not None:
                for sentence in chunker.feed(token):
                    emit({"type": "tts", "text": sentence})

        agent_id = _orchestrator.control_room.start(command)
        try:
            result = asyncio.run(_orchestrator.handle(command, history=history, on_token=on_token))
            if chunker is not None:
                tail = chunker.flush()
                if tail:
                    emit({"type": "tts", "text": tail})
            _orchestrator.control_room.finish(agent_id, result.message, ok=result.ok)
            emit({"type": "done", "ok": result.ok, "message": result.message, "data": _json_safe(result.data)})
        except ApprovalDenied as exc:
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            emit({"type": "done", "ok": False, "message": f"Blocked — that high-risk action needs the desktop app: {exc}", "data": {}})
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            emit({"type": "done", "ok": False, "message": f"Error: {exc}", "data": {}})

    def _handle_agent(self) -> None:
        """Run the autonomous agent, streaming each plan/act/observe step over SSE."""
        try:
            payload = self._read_json()
            goal = str(payload.get("goal", "")).strip()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return
        if not goal:
            self._json(400, {"ok": False, "message": "no goal"})
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def emit(obj: dict) -> None:
            try:
                self.wfile.write(("data: " + json.dumps(obj, default=str) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        emit({"type": "start", "goal": goal})
        try:
            result = asyncio.run(
                _orchestrator.run_agent(goal, on_step=lambda step: emit({"type": "step", "step": step.__dict__}))
            )
            emit({"type": "done", "ok": result.ok, "message": result.message, "data": _json_safe(result.data)})
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            emit({"type": "done", "ok": False, "message": f"Error: {exc}", "data": {}})

    def _handle_upload(self) -> None:
        try:
            payload = self._read_json()
            name = Path(str(payload.get("name", "upload.bin"))).name or "upload.bin"
            data = str(payload.get("data", ""))
            if data.startswith("data:") and "," in data:
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data, validate=False)
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "could not read upload"})
            return
        if len(raw) > MAX_UPLOAD_BYTES:
            self._json(413, {"ok": False, "message": "file too large (max 35MB)"})
            return
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        dest = UPLOAD_DIR / name
        dest.write_bytes(raw)
        self._json(200, {"ok": True, "path": str(dest), "name": name, "size": len(raw)})

    def _handle_schedule(self) -> None:
        """Add / remove / toggle a scheduled job, then return the refreshed list.

        Mutations route through the same orchestrator commands the CLI uses, so the
        parsing/validation and approval semantics stay identical; the response always
        carries the current job list so the panel re-renders from one round-trip.
        """
        try:
            payload = self._read_json()
            action = str(payload.get("action", "")).strip().lower()
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        message = ""
        ok = True
        if action == "add":
            kind = "agent" if str(payload.get("kind", "command")).strip().lower() == "agent" else "command"
            when = str(payload.get("when", "")).strip()
            spec = str(payload.get("spec", "")).strip()
            if not when or not spec:
                self._json(400, {"ok": False, "message": "Provide both a schedule and a command/goal.", "jobs": []})
                return
            prefix = "schedule agent " if kind == "agent" else "schedule "
            result = asyncio.run(_orchestrator.handle(f"{prefix}{when} :: {spec}"))
            ok, message = result.ok, result.message
        elif action == "remove":
            try:
                job_id = int(str(payload.get("id", "")).lstrip("#"))
            except ValueError:
                self._json(400, {"ok": False, "message": "Invalid job id.", "jobs": []})
                return
            result = asyncio.run(_orchestrator.handle(f"schedule remove {job_id}"))
            ok, message = result.ok, result.message
        elif action in {"enable", "disable"}:
            try:
                job_id = int(str(payload.get("id", "")).lstrip("#"))
            except ValueError:
                self._json(400, {"ok": False, "message": "Invalid job id.", "jobs": []})
                return
            changed = _orchestrator.context.scheduler.set_enabled(job_id, action == "enable")
            ok = changed
            message = f"Job #{job_id} {action}d." if changed else f"No scheduled job #{job_id}."
        else:
            self._json(400, {"ok": False, "message": "Unknown action.", "jobs": []})
            return

        snapshot = _schedule_snapshot()
        self._json(200, {"ok": ok, "message": message, "jobs": snapshot["jobs"]})

    def _handle_command(self) -> None:
        try:
            payload = self._read_json()
            command = _compose_command(str(payload.get("command", "")), payload.get("attachments") or [])
            history = payload.get("history") or []
            if not isinstance(history, list):
                history = []
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        # Light up the matching specialist in the control room while this runs,
        # so the agents panel reflects single commands, not just multi subtasks.
        agent_id = _orchestrator.control_room.start(command)
        try:
            result = asyncio.run(_orchestrator.handle(command, history=history))
            _orchestrator.control_room.finish(agent_id, result.message, ok=result.ok)
            body = {"ok": result.ok, "message": result.message, "data": _json_safe(result.data)}
        except ApprovalDenied as exc:
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            body = {"ok": False, "message": f"Blocked — that high-risk action needs the desktop app: {exc}", "data": {}}
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            _orchestrator.control_room.finish(agent_id, str(exc), ok=False)
            body = {"ok": False, "message": f"Error: {exc}", "data": {}}
        self._json(200, body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    _warmup()
    _keep_warm()
    _schedule_ticker()
    print(f"J.A.R.V.I.S chat running at {url}")
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
        "--window-size=1280,860",
    ]
    return subprocess.Popen(args)


def _launch_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    webview.create_window("J.A.R.V.I.S", url, width=1280, height=860, background_color="#08090d")
    webview.start()
    return True


def run_desktop() -> None:
    """Serve the chat and open it in a dedicated desktop window (no browser chrome)."""
    server = ThreadingHTTPServer((HOST, 0), Handler)
    port = server.server_address[1]
    url = f"http://{HOST}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _warmup()
    _keep_warm()
    _schedule_ticker()
    print(f"J.A.R.V.I.S chat serving at {url}")

    if _launch_webview(url):
        server.shutdown()
        return

    process = _launch_app_window(url)
    if process is None:
        print("No Chromium-based browser found; opening in the default browser instead.")
        webbrowser.open(url)
    else:
        print("Chat opened as a desktop window.")
    print("Running. Press Ctrl+C here to stop.")
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
