"""Local chat app for the laptop agent.

A stdlib-only HTTP server that serves a single ChatGPT-style chat interface and
wires it to the same AgentOrchestrator the CLI uses. It binds to localhost only.
High-risk actions (sending email, moving/overwriting files, downloads, browser
form fills) are blocked here; read/search/research actions are allowed.

Features: one unified chat that routes intent through the LLM, file uploads of
any type (the agent reads/summarizes/OCRs/transcribes/indexes them), and a
browser-based voice mode (speech-to-text in, text-to-speech out).

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
from laptop_agent.safety import ApprovalDenied, ApprovalRequest, RiskLevel

HOST = "127.0.0.1"
PORT = 8770
UPLOAD_DIR = Path(tempfile.gettempdir()) / "laptop_agent_uploads"
MAX_UPLOAD_BYTES = 35 * 1024 * 1024


def _planner_label() -> str:
    provider = getattr(_orchestrator.planner, "provider", None)
    name = type(provider).__name__ if provider else ""
    if "OpenAI" in name:
        model = getattr(provider, "model", "") or ""
        return model.split("/")[-1][:18] if model else "llm"
    return "heuristic"


def _guarded_approval(request: ApprovalRequest) -> bool:
    return request.risk == RiskLevel.MEDIUM


_orchestrator = build_orchestrator(approval_callback=_guarded_approval)


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
    --bg:#08090d; --bg2:#0c0e14; --panel:#11141c; --line:#1c2230; --line2:#283143;
    --amber:#ffb000; --amber-b:#ffc94d; --amber-soft:#7a5a16; --ice:#5fd0e6;
    --text:#e9eef4; --muted:#7c879a; --ok:#54e0a0; --danger:#ff5d6c;
    --display:'Chakra Petch',sans-serif; --body:'IBM Plex Sans',sans-serif; --mono:'IBM Plex Mono',monospace;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--text);font-family:var(--body);overflow:hidden}
  .bg{position:fixed;inset:0;z-index:0;pointer-events:none}
  .bg.glow{background:radial-gradient(55% 45% at 50% 0%,rgba(255,176,0,.07),transparent 70%),radial-gradient(40% 40% at 100% 100%,rgba(95,208,230,.05),transparent 70%)}
  .bg.grid{background-image:linear-gradient(var(--line) 1px,transparent 1px),linear-gradient(90deg,var(--line) 1px,transparent 1px);background-size:48px 48px;opacity:.10;mask-image:radial-gradient(ellipse 70% 60% at 50% 30%,#000,transparent 80%)}

  .app{position:relative;z-index:1;display:flex;flex-direction:column;height:100vh;max-width:880px;margin:0 auto;padding:0 18px}

  header{display:flex;align-items:center;gap:13px;padding:14px 4px 12px;border-bottom:1px solid var(--line)}
  .reactor{width:34px;height:34px;flex:none}
  .brand .n{font-family:var(--display);font-weight:700;letter-spacing:5px;font-size:17px}
  .brand .n b{color:var(--amber)}
  .brand .s{font-family:var(--mono);font-size:9px;color:var(--muted);letter-spacing:1px;margin-top:1px}
  header .sp{flex:1}
  .hbtn{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:11px;color:var(--text);background:var(--panel);border:1px solid var(--line2);border-radius:999px;padding:8px 14px;cursor:pointer;transition:.16s}
  .hbtn:hover{border-color:var(--amber-soft);color:var(--amber-b)}
  .hbtn .dot{width:7px;height:7px;border-radius:50%;background:var(--ice);box-shadow:0 0 8px var(--ice)}
  .pill{font-family:var(--mono);font-size:9.5px;color:var(--muted);letter-spacing:1px;padding:5px 10px;border:1px solid var(--line);border-radius:999px}

  .chat{flex:1;overflow-y:auto;padding:22px 4px 8px;scroll-behavior:smooth}
  .empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:18px;padding-bottom:40px}
  .empty .orb{width:78px;height:78px}
  .empty h1{font-family:var(--display);font-weight:600;letter-spacing:3px;font-size:22px;margin:0}
  .empty p{color:var(--muted);font-size:13.5px;margin:0;max-width:440px;line-height:1.6}
  .suggest{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:8px;width:100%;max-width:520px}
  .scard{text-align:left;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:13px 15px;cursor:pointer;transition:.16s;font-size:13px;color:var(--text)}
  .scard:hover{border-color:var(--amber-soft);transform:translateY(-2px)}
  .scard b{display:block;font-family:var(--display);font-weight:600;font-size:11px;letter-spacing:1.5px;color:var(--amber-b);text-transform:uppercase;margin-bottom:4px}

  .msg{display:flex;gap:12px;margin:18px 0;animation:rise .3s ease both}
  .msg .av{width:30px;height:30px;border-radius:8px;flex:none;display:flex;align-items:center;justify-content:center;font-family:var(--display);font-weight:700;font-size:11px}
  .msg.user .av{background:#13202b;color:var(--ice);border:1px solid #1d3441}
  .msg.bot .av{background:#1a1405;color:var(--amber);border:1px solid #3a2c0a}
  .msg .content{flex:1;min-width:0;padding-top:3px}
  .msg .who{font-family:var(--display);font-size:10px;letter-spacing:2px;text-transform:uppercase;color:var(--muted);margin-bottom:5px}
  .msg .text{font-size:14.5px;line-height:1.7;white-space:pre-wrap;word-wrap:break-word;color:#e4eaf1}
  .msg.user .text{color:#cfe9f1}
  .msg.err .text{color:var(--danger)}
  .att{display:inline-flex;align-items:center;gap:7px;margin:6px 6px 0 0;background:#0b1016;border:1px solid var(--line2);border-radius:8px;padding:6px 10px;font-family:var(--mono);font-size:11px;color:var(--muted)}
  .att .ic{color:var(--amber)}
  .det{margin-top:8px}
  .det>summary{font-family:var(--mono);font-size:9.5px;letter-spacing:1.5px;text-transform:uppercase;color:var(--amber-soft);cursor:pointer;list-style:none;user-select:none}
  .det>summary::-webkit-details-marker{display:none}
  .det>summary::before{content:'\25B8  ';color:var(--amber)}
  .det[open]>summary::before{content:'\25BE  '}
  .data{margin-top:6px;font-family:var(--mono);font-size:11px;line-height:1.5;color:#8fa1b6;background:#080a0e;border:1px solid var(--line);border-left:2px solid var(--amber-soft);border-radius:6px;padding:10px 12px;max-height:230px;overflow:auto;white-space:pre-wrap}
  .dots span{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--amber);margin-right:4px;animation:blink 1.2s infinite}
  .dots span:nth-child(2){animation-delay:.2s}.dots span:nth-child(3){animation-delay:.4s}

  .composer{padding:10px 0 16px}
  .chips{display:flex;flex-wrap:wrap;gap:7px;margin-bottom:8px}
  .chip{display:inline-flex;align-items:center;gap:8px;background:var(--panel);border:1px solid var(--line2);border-radius:9px;padding:7px 10px;font-family:var(--mono);font-size:11px}
  .chip .ic{color:var(--amber)}.chip .rm{cursor:pointer;color:var(--muted)}.chip .rm:hover{color:var(--danger)}
  .box{display:flex;align-items:flex-end;gap:8px;background:var(--bg2);border:1px solid var(--line2);border-radius:16px;padding:8px 8px 8px 6px;transition:.18s}
  .box:focus-within{border-color:var(--amber-soft);box-shadow:0 0 0 1px var(--amber-soft),0 0 26px rgba(255,176,0,.10)}
  .iconbtn{width:40px;height:40px;flex:none;border:none;background:transparent;color:var(--muted);border-radius:11px;cursor:pointer;font-size:17px;display:flex;align-items:center;justify-content:center;transition:.15s}
  .iconbtn:hover{background:#161a23;color:var(--amber-b)}
  .iconbtn.live{color:var(--amber)}
  .iconbtn.live::after{content:'';position:absolute;width:40px;height:40px;border-radius:11px;border:1.5px solid var(--amber);animation:ring 1.1s ease-out infinite}
  #ta{flex:1;background:transparent;border:none;outline:none;color:var(--text);font-family:var(--body);font-size:14.5px;line-height:1.5;resize:none;max-height:160px;padding:10px 4px}
  #ta::placeholder{color:#46505f}
  .sendbtn{width:42px;height:42px;flex:none;border:none;border-radius:12px;background:var(--amber);color:#08090d;cursor:pointer;font-size:17px;transition:.15s}
  .sendbtn:hover{background:var(--amber-b)} .sendbtn:disabled{opacity:.35;cursor:default}
  .hint{text-align:center;color:#3f4858;font-family:var(--mono);font-size:9.5px;margin-top:8px;letter-spacing:.5px}

  /* voice overlay */
  .voice{position:absolute;inset:0;z-index:5;background:rgba(8,9,13,.94);backdrop-filter:blur(8px);display:none;flex-direction:column;align-items:center;justify-content:center;gap:26px}
  .voice.on{display:flex}
  .vorb{width:190px;height:190px;position:relative;display:flex;align-items:center;justify-content:center}
  .vstate{font-family:var(--display);letter-spacing:4px;text-transform:uppercase;font-size:13px;color:var(--amber-b)}
  .vtrans{min-height:24px;max-width:560px;text-align:center;color:var(--text);font-size:16px;line-height:1.5;padding:0 20px}
  .vsub{font-family:var(--mono);font-size:11px;color:var(--muted)}
  .vend{margin-top:6px;font-family:var(--display);letter-spacing:2px;font-size:12px;color:#08090d;background:var(--amber);border:none;border-radius:999px;padding:11px 26px;cursor:pointer}
  .vend:hover{background:var(--amber-b)}
  .bars{display:flex;align-items:center;gap:4px;height:34px}
  .bars i{width:4px;height:8px;background:var(--amber);border-radius:3px;animation:bars 1s ease-in-out infinite}
  .bars i:nth-child(2){animation-delay:.12s}.bars i:nth-child(3){animation-delay:.24s}.bars i:nth-child(4){animation-delay:.36s}.bars i:nth-child(5){animation-delay:.48s}
  .voice[data-state="thinking"] .bars{opacity:.25}
  .drop{position:absolute;inset:0;z-index:6;background:rgba(255,176,0,.06);border:2px dashed var(--amber);border-radius:18px;display:none;align-items:center;justify-content:center;font-family:var(--display);letter-spacing:2px;color:var(--amber-b);pointer-events:none}
  .drop.on{display:flex}

  ::-webkit-scrollbar{width:9px}::-webkit-scrollbar-thumb{background:#1b2230;border-radius:9px}::-webkit-scrollbar-thumb:hover{background:#2a3447}
  @keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
  @keyframes blink{0%,100%{opacity:.3}50%{opacity:1}}
  @keyframes ring{from{opacity:.7;transform:scale(1)}to{opacity:0;transform:scale(1.5)}}
  @keyframes spin{to{transform:rotate(360deg)}}
  @keyframes spinrev{to{transform:rotate(-360deg)}}
  @keyframes corepulse{0%,100%{r:6;opacity:1}50%{r:10;opacity:.65}}
  @keyframes bars{0%,100%{height:8px}50%{height:30px}}
  .r-ring1{transform-origin:50px 50px;animation:spin 9s linear infinite}
  .r-ring2{transform-origin:50px 50px;animation:spinrev 6s linear infinite}
  .r-core{animation:corepulse 2.6s ease-in-out infinite}
  .busy .r-core,.busy .r-mid{stroke:var(--amber)!important;fill:var(--amber)!important}
  .busy .r-ring1,.busy .r-ring2{stroke:var(--amber)!important}
</style>
</head>
<body>
<div class="bg glow"></div><div class="bg grid"></div>

<div class="app">
  <header>
    <svg class="reactor" id="reactor" viewBox="0 0 100 100" aria-hidden="true">
      <circle class="r-ring1" cx="50" cy="50" r="42" fill="none" stroke="#5fd0e6" stroke-width="2" stroke-dasharray="6 10"/>
      <circle class="r-ring2" cx="50" cy="50" r="33" fill="none" stroke="#5fd0e6" stroke-width="1.4" stroke-dasharray="3 7" opacity=".7"/>
      <circle class="r-mid" cx="50" cy="50" r="21" fill="none" stroke="#5fd0e6" stroke-width="2.5"/>
      <circle class="r-core" cx="50" cy="50" r="6" fill="#5fd0e6"/>
    </svg>
    <div class="brand"><div class="n">J<b>.</b>A<b>.</b>R<b>.</b>V<b>.</b>I<b>.</b>S</div><div class="s">local-first assistant</div></div>
    <div class="sp"></div>
    <span class="pill">{{PLANNER}}</span>
    <span class="pill">guarded</span>
    <button class="hbtn" id="voiceBtn"><span class="dot"></span> Voice</button>
  </header>

  <div class="chat" id="chat">
    <div class="empty" id="empty">
      <svg class="orb" viewBox="0 0 100 100" aria-hidden="true">
        <circle class="r-ring1" cx="50" cy="50" r="42" fill="none" stroke="#ffb000" stroke-width="2" stroke-dasharray="6 10"/>
        <circle class="r-mid" cx="50" cy="50" r="22" fill="none" stroke="#ffb000" stroke-width="2.5"/>
        <circle class="r-core" cx="50" cy="50" r="6" fill="#ffb000"/>
      </svg>
      <h1>How can I help, Jeevan?</h1>
      <p>Ask me anything, drop in a file of any type, or tap Voice to talk. I'll read, summarize, search, research, and remember — in plain language.</p>
      <div class="suggest" id="suggest"></div>
    </div>
  </div>

  <div class="composer">
    <div class="chips" id="chips"></div>
    <div class="box">
      <button class="iconbtn" id="attachBtn" title="Attach a file">&#128206;</button>
      <textarea id="ta" rows="1" placeholder="Message J.A.R.V.I.S…  (drop a file, or tap the mic)"></textarea>
      <button class="iconbtn" id="micBtn" title="Dictate" style="position:relative">&#127908;</button>
      <button class="sendbtn" id="sendBtn" title="Send">&#10148;</button>
    </div>
    <div class="hint" id="hint">Guarded mode — high-risk actions are blocked here. Press Enter to send, Shift+Enter for a new line.</div>
  </div>

  <input type="file" id="file" multiple style="display:none" />
  <div class="drop" id="drop">Drop files to attach</div>

  <div class="voice" id="voice" data-state="listening">
    <div class="vorb">
      <svg viewBox="0 0 100 100" width="190" height="190" aria-hidden="true">
        <circle class="r-ring1" cx="50" cy="50" r="44" fill="none" stroke="#ffb000" stroke-width="1.5" stroke-dasharray="5 9"/>
        <circle class="r-ring2" cx="50" cy="50" r="36" fill="none" stroke="#5fd0e6" stroke-width="1.2" stroke-dasharray="3 8" opacity=".7"/>
        <circle class="r-mid" cx="50" cy="50" r="26" fill="none" stroke="#ffb000" stroke-width="2.5"/>
        <circle class="r-core" cx="50" cy="50" r="7" fill="#ffb000"/>
      </svg>
    </div>
    <div class="bars"><i></i><i></i><i></i><i></i><i></i></div>
    <div class="vstate" id="vstate">Listening</div>
    <div class="vtrans" id="vtrans">Say something…</div>
    <button class="vend" id="vend">End voice</button>
    <div class="vsub" id="vsub"></div>
  </div>
</div>

<script>
  const chat=document.getElementById('chat'), empty=document.getElementById('empty'),
        ta=document.getElementById('ta'), sendBtn=document.getElementById('sendBtn'),
        attachBtn=document.getElementById('attachBtn'), fileIn=document.getElementById('file'),
        chips=document.getElementById('chips'), micBtn=document.getElementById('micBtn'),
        reactor=document.getElementById('reactor'), drop=document.getElementById('drop'),
        voiceBtn=document.getElementById('voiceBtn'), voice=document.getElementById('voice'),
        vstate=document.getElementById('vstate'), vtrans=document.getElementById('vtrans'),
        vend=document.getElementById('vend'), vsub=document.getElementById('vsub');
  let attachments=[], busy=false;

  const SUG=[
    ["Get oriented","What can you do?"],
    ["Summarize","Summarize the README"],
    ["Research","Research local-first AI agents"],
    ["Memory","What do you remember about me?"],
  ];
  const suggest=document.getElementById('suggest');
  SUG.forEach(([t,q])=>{const c=document.createElement('div');c.className='scard';c.innerHTML='<b>'+t+'</b>'+q;c.onclick=()=>send(q);suggest.appendChild(c);});

  function auto(){ta.style.height='auto';ta.style.height=Math.min(ta.scrollHeight,160)+'px';}
  ta.addEventListener('input',auto);
  ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(ta.value);}});
  sendBtn.onclick=()=>send(ta.value);

  function bubble(role,text){
    if(empty&&empty.parentNode){empty.remove();}
    const m=document.createElement('div');m.className='msg '+role;
    const av=role==='user'?'YOU':'J';
    m.innerHTML='<div class="av">'+av+'</div><div class="content"><div class="who">'+(role==='user'?'You':'J.A.R.V.I.S')+'</div><div class="text"></div></div>';
    m.querySelector('.text').textContent=text;
    chat.appendChild(m);chat.scrollTop=chat.scrollHeight;return m;
  }
  function thinking(){
    const m=bubble('bot','');m.classList.add('err','tmp');m.classList.remove('err');
    m.querySelector('.text').innerHTML='<span class="dots"><span></span><span></span><span></span></span>';
    return m;
  }
  function setBusy(b){busy=b;sendBtn.disabled=b;reactor.classList.toggle('busy',b);}

  async function uploadFile(file){
    const data=await new Promise(res=>{const r=new FileReader();r.onload=()=>res(r.result);r.readAsDataURL(file);});
    const r=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,data})});
    const d=await r.json();
    if(d.ok){attachments.push(d);renderChips();}
    else{bubble('bot','Could not attach '+file.name+': '+(d.message||'upload failed')).classList.add('err');}
  }
  function renderChips(){
    chips.innerHTML='';
    attachments.forEach((a,i)=>{
      const c=document.createElement('div');c.className='chip';
      c.innerHTML='<span class="ic">&#128196;</span>'+a.name+' <span class="rm">&times;</span>';
      c.querySelector('.rm').onclick=()=>{attachments.splice(i,1);renderChips();};
      chips.appendChild(c);
    });
  }
  attachBtn.onclick=()=>fileIn.click();
  fileIn.onchange=()=>{[...fileIn.files].forEach(uploadFile);fileIn.value='';};

  ['dragenter','dragover'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('on');}));
  ['dragleave','drop'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();if(e==='drop'||ev.clientX===0)drop.classList.remove('on');}));
  document.addEventListener('drop',ev=>{ev.preventDefault();drop.classList.remove('on');if(ev.dataTransfer&&ev.dataTransfer.files)[...ev.dataTransfer.files].forEach(uploadFile);});

  async function send(text){
    text=(text||'').trim();
    if((!text&&!attachments.length)||busy)return;
    const sent=attachments.slice();
    const um=bubble('user',text||'(sent attachment)');
    if(sent.length){const box=document.createElement('div');sent.forEach(a=>{const s=document.createElement('span');s.className='att';s.innerHTML='<span class="ic">&#128196;</span>'+a.name;box.appendChild(s);});um.querySelector('.content').appendChild(box);}
    ta.value='';auto();attachments=[];renderChips();setBusy(true);
    const tmp=thinking();
    let replyText='';
    try{
      const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({command:text,attachments:sent.map(a=>a.path)})});
      const d=await r.json();
      tmp.remove();
      const node=bubble(d.ok?'bot':'bot',d.message||'(no output)');
      if(!d.ok)node.classList.add('err');
      replyText=d.message||'';
      const data=Object.assign({},d.data||{});delete data.planner;
      if(Object.keys(data).length){
        const det=document.createElement('details');det.className='det';
        const sm=document.createElement('summary');sm.textContent='details';det.appendChild(sm);
        const pre=document.createElement('div');pre.className='data';pre.textContent=JSON.stringify(data,null,2);det.appendChild(pre);
        node.querySelector('.content').appendChild(det);
      }
    }catch(err){tmp.remove();bubble('bot','Connection error: '+err).classList.add('err');}
    finally{setBusy(false);ta.focus();if(voiceActive&&replyText)speak(replyText);}
    return replyText;
  }

  /* ---------- voice ---------- */
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  let rec=null, voiceActive=false, recognizing=false;
  if(!SR){micBtn.style.display='none';}

  function vSet(state,label){voice.dataset.state=state;vstate.textContent=label;}

  // dictation (composer mic): fills the text box
  let dictating=false;
  micBtn.onclick=()=>{
    if(!SR)return;
    if(dictating){rec&&rec.stop();return;}
    rec=new SR();rec.lang='en-US';rec.interimResults=true;rec.continuous=false;
    dictating=true;micBtn.classList.add('live');
    let acc=ta.value?ta.value+' ':'';
    rec.onresult=e=>{let t='';for(let i=e.resultIndex;i<e.results.length;i++)t+=e.results[i][0].transcript;ta.value=acc+t;auto();};
    rec.onerror=()=>{};
    rec.onend=()=>{dictating=false;micBtn.classList.remove('live');};
    rec.start();
  };

  // voice conversation: listen -> send -> speak -> listen
  voiceBtn.onclick=()=>{ if(!SR){alert('Speech recognition is not available in this window.');return;} voiceActive?endVoice():startVoice(); };
  vend.onclick=endVoice;
  function startVoice(){voiceActive=true;voice.classList.add('on');vsub.textContent=SR?'':'';listen();}
  function endVoice(){voiceActive=false;voice.classList.remove('on');try{rec&&rec.stop();}catch(e){}try{speechSynthesis.cancel();}catch(e){}}
  function listen(){
    if(!voiceActive)return;
    vSet('listening','Listening');vtrans.textContent='Say something…';
    rec=new SR();rec.lang='en-US';rec.interimResults=true;rec.continuous=false;recognizing=true;
    let finalT='';
    rec.onresult=e=>{let t='';for(let i=0;i<e.results.length;i++)t+=e.results[i][0].transcript;vtrans.textContent=t;if(e.results[e.results.length-1].isFinal)finalT=t;};
    rec.onerror=()=>{};
    rec.onend=async()=>{recognizing=false;if(!voiceActive)return;
      const q=(finalT||vtrans.textContent||'').trim();
      if(!q||q==='Say something…'){listen();return;}
      vSet('thinking','Thinking');
      const reply=await send(q);
      if(!voiceActive)return;
      if(reply){vSet('speaking','Speaking');vtrans.textContent=reply.slice(0,220);speak(reply);}else{listen();}
    };
    try{rec.start();}catch(e){}
  }
  function speak(text){
    try{
      speechSynthesis.cancel();
      const u=new SpeechSynthesisUtterance(text.replace(/[`*#_>]/g,'').slice(0,600));
      u.rate=1.04;u.pitch=1;
      u.onend=()=>{if(voiceActive)listen();};
      u.onerror=()=>{if(voiceActive)listen();};
      speechSynthesis.speak(u);
    }catch(e){if(voiceActive)listen();}
  }

  ta.focus();
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
            page = PAGE.replace("{{PLANNER}}", _planner_label())
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
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
        else:
            self._send(404, b"not found", "text/plain")

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

    def _handle_command(self) -> None:
        try:
            payload = self._read_json()
            command = str(payload.get("command", "")).strip()
            attachments = payload.get("attachments") or []
            if not isinstance(attachments, list):
                attachments = []
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "message": "bad request"})
            return

        paths = [str(p) for p in attachments if p]
        if paths:
            listing = "; ".join(paths)
            if not command:
                command = "Summarize or describe the attached file(s)."
            command = (
                f"{command}\n\n[The user attached file(s) saved at: {listing}. "
                "Use the path(s) as the target for any file, image, audio, document, or indexing action.]"
            )

        try:
            result = asyncio.run(_orchestrator.handle(command))
            body = {"ok": result.ok, "message": result.message, "data": _json_safe(result.data)}
        except ApprovalDenied as exc:
            body = {"ok": False, "message": f"Blocked — that high-risk action needs the desktop app: {exc}", "data": {}}
        except Exception as exc:  # pragma: no cover - defensive for the preview server.
            body = {"ok": False, "message": f"Error: {exc}", "data": {}}
        self._json(200, body)


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
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
        "--window-size=1100,820",
    ]
    return subprocess.Popen(args)


def _launch_webview(url: str) -> bool:
    try:
        import webview  # type: ignore
    except ImportError:
        return False
    webview.create_window("J.A.R.V.I.S", url, width=1100, height=820, background_color="#08090d")
    webview.start()
    return True


def run_desktop() -> None:
    """Serve the chat and open it in a dedicated desktop window (no browser chrome)."""
    server = ThreadingHTTPServer((HOST, 0), Handler)
    port = server.server_address[1]
    url = f"http://{HOST}:{port}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
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
