"""The J.A.R.V.I.S web interface: one HTML document (markup + CSS + JS).

Kept apart from ``webui.py`` so the server logic stays readable; the page is a
plain string constant, so nothing about packaging or imports changes. The server
reads it once at import and substitutes the ``{{...}}`` placeholders per request,
so edits here need a restart.
"""

from __future__ import annotations

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>J.A.R.V.I.S</title>

<style>
  :root{
    --bg:#090d14; --bg-2:#0b1019; --surface:#0f1520; --surface-2:#121926; --surface-3:#182133;
    --hair:rgba(255,255,255,.06); --hair-2:rgba(255,255,255,.1); --hair-3:rgba(255,255,255,.16);
    --text:#e7edf3; --text-2:#b9c4d0; --muted:#8793a3; --faint:#5d6979;
    --accent:#62d3ea; --accent-2:#93e4f2; --accent-ink:#06222a;
    --accent-soft:rgba(98,211,234,.12); --accent-line:rgba(98,211,234,.38);
    --violet:#8b7cff; --ok:#3ddc97; --warn:#f2b955; --danger:#ff6b7a;
    --sans:"Segoe UI Variable Text","Segoe UI Variable","Segoe UI",-apple-system,"SF Pro Text","Helvetica Neue",system-ui,sans-serif;
    --display:"Segoe UI Variable Display","Segoe UI Variable","Segoe UI",-apple-system,"SF Pro Display","Helvetica Neue",system-ui,sans-serif;
    --mono:"Cascadia Mono","Cascadia Code",ui-monospace,Consolas,"SF Mono",Menlo,monospace;
    --r-sm:8px; --r-md:12px; --r-lg:16px; --r-xl:22px;
    --fast:150ms; --med:220ms; --ease:cubic-bezier(.2,.7,.2,1);
    --shadow-1:0 1px 2px rgba(0,0,0,.35),0 10px 28px -14px rgba(0,0,0,.6);
    --shadow-2:0 20px 60px -20px rgba(0,0,0,.75);
    --rail-w:248px; --presence-w:clamp(280px,26vw,400px); --head-h:56px;
    --violet-2:#b48cff; --voice:#a394ff;
  }
  *{box-sizing:border-box}
  html,body{height:100%;margin:0}
  body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;overflow:hidden;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
  /* atmosphere: a faint cool tint top-left, a faint violet tint bottom-right, and fine grain */
  body::before{content:'';position:fixed;inset:0;z-index:-2;pointer-events:none;
    background:radial-gradient(60% 50% at 6% 0%,rgba(98,211,234,.07),transparent 60%),
               radial-gradient(50% 45% at 100% 100%,rgba(139,124,255,.06),transparent 60%),
               linear-gradient(180deg,#0a0f17,#080c13)}
  body::after{content:'';position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.035;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='180' height='180'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}
  ::selection{background:rgba(98,211,234,.28)}
  ::-webkit-scrollbar{width:9px;height:9px}::-webkit-scrollbar-thumb{background:rgba(255,255,255,.1);border-radius:8px;border:2px solid transparent;background-clip:padding-box}::-webkit-scrollbar-thumb:hover{background:rgba(255,255,255,.18);background-clip:padding-box}
  button,input,select,textarea{font:inherit;color:inherit}
  button{touch-action:manipulation;cursor:pointer}
  :focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
  svg{flex:none}

  .app{position:relative;display:grid;grid-template-columns:var(--rail-w) var(--presence-w) minmax(0,1fr);grid-template-rows:var(--head-h) minmax(0,1fr);height:100dvh;min-height:0}
  .app>*{min-width:0;min-height:0}

  /* ---------- top bar ---------- */
  header{grid-column:1/-1;position:relative;z-index:60;display:flex;align-items:center;gap:14px;padding:0 14px 0 18px;border-bottom:1px solid var(--hair);background:rgba(9,13,20,.72);backdrop-filter:blur(14px)}
  .brand{display:flex;align-items:center;gap:11px;min-width:calc(var(--rail-w) - 18px)}
  .mark,.msg .av{width:26px;height:26px;border-radius:50%;flex:none;background:radial-gradient(circle at 35% 30%,#d7f8fd,var(--accent) 42%,#5d5be0 100%);box-shadow:inset 0 0 0 1px rgba(255,255,255,.08)}
  .mark{transition:box-shadow var(--med)}
  .mark.busy{box-shadow:inset 0 0 0 1px rgba(255,255,255,.08),0 0 0 4px var(--accent-soft)}
  .brand .n{font:600 14.5px/1.1 var(--display);letter-spacing:.6px;color:var(--text)}
  .brand .s{font:400 12px/1.2 var(--sans);color:var(--muted);margin-top:2px}
  header .sp{flex:1}
  .nav{display:flex;gap:2px;padding:3px;border-radius:999px;background:rgba(255,255,255,.035)}
  .navbtn{display:inline-flex;align-items:center;border:0;background:transparent;color:var(--muted);font:500 13px/1 var(--sans);padding:8px 14px;border-radius:999px;transition:background var(--fast),color var(--fast)}
  .navbtn:hover{color:var(--text)}
  .navbtn.on{background:var(--surface-3);color:var(--text);box-shadow:var(--shadow-1)}
  #mobileChats{display:none}
  .pill{display:inline-flex;align-items:center;gap:8px;height:32px;padding:0 12px 0 10px;border-radius:999px;border:1px solid var(--hair);background:rgba(255,255,255,.02);color:var(--muted);font:500 12.5px var(--sans);transition:background var(--fast),color var(--fast),border-color var(--fast)}
  .pill:hover{background:rgba(255,255,255,.05);color:var(--text);border-color:var(--hair-2)}
  .pill .dot{width:7px;height:7px;border-radius:50%;background:var(--faint);transition:background .4s}
  .pill.ok>.dot,.sysbtn.ok>.dot{background:var(--ok)}
  .pill.busy>.dot,.sysbtn.busy>.dot,.pill.degraded>.dot,.sysbtn.degraded>.dot{background:var(--warn)}
  .pill.setup>.dot,.sysbtn.setup>.dot{background:var(--danger)}
  .hud{display:flex;align-items:center;gap:6px;position:relative}
  .hudbtn{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border:0;border-radius:10px;background:transparent;color:var(--muted);transition:background var(--fast),color var(--fast)}
  .hudbtn svg{width:18px;height:18px}
  .hudbtn:hover,.hudbtn.on{background:rgba(255,255,255,.06);color:var(--text)}
  .hudpop{position:absolute;top:42px;right:0;z-index:70;width:252px;background:var(--surface-2);border:1px solid var(--hair-2);border-radius:var(--r-md);padding:8px;display:none;flex-direction:column;gap:2px;box-shadow:var(--shadow-2)}
  .hudpop.open{display:flex}
  .hudpop .toggle{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;padding:9px 10px;border:0;border-radius:9px;background:transparent;text-align:left;font:500 13px var(--sans);color:var(--text);cursor:pointer;transition:background var(--fast)}
  .hudpop .toggle:hover{background:rgba(255,255,255,.04)}
  .hudpop .toggle small{display:block;font:400 11.5px/1.3 var(--sans);color:var(--muted);margin-top:2px}
  .hudpop .sw{width:34px;height:20px;border-radius:999px;background:rgba(255,255,255,.12);position:relative;flex:none;transition:background var(--fast)}
  .hudpop .sw::after{content:'';position:absolute;top:3px;left:3px;width:14px;height:14px;border-radius:50%;background:#cbd5df;transition:left var(--fast),background var(--fast)}
  .hudpop .toggle.on .sw{background:var(--accent)}
  .hudpop .toggle.on .sw::after{left:17px;background:var(--accent-ink)}
  .hudpop .row{display:flex;flex-direction:column;gap:8px;padding:9px 10px 6px}
  .hudpop .lbl{display:flex;justify-content:space-between;font:500 13px var(--sans);color:var(--text)}
  .hudpop .lbl span:last-child{font:12px var(--mono);color:var(--muted)}
  .hudpop input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:3px;background:rgba(255,255,255,.12);outline:none;margin:4px 0}
  .hudpop input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:var(--accent);cursor:pointer;border:2px solid var(--surface-2)}
  .hudpop .hint2{font:11.5px/1.4 var(--sans);color:var(--faint);padding:4px 10px 6px}

  /* ---------- left rail ---------- */
  .left{display:flex;flex-direction:column;min-height:0;padding:14px 12px 12px;border-right:1px solid var(--hair);background:rgba(255,255,255,.012)}
  .newchat{display:flex;align-items:center;gap:9px;width:100%;padding:10px 12px;border-radius:var(--r-md);border:1px solid var(--hair-2);background:rgba(255,255,255,.04);color:var(--text);font:500 13.5px var(--sans);text-align:left;transition:background var(--fast),border-color var(--fast)}
  .newchat svg{width:16px;height:16px;color:var(--accent)}
  .newchat:hover{background:rgba(255,255,255,.07);border-color:var(--hair-3)}
  .seclbl{font:500 11.5px var(--sans);color:var(--faint);margin:20px 10px 6px}
  #sessions{flex:1;min-height:0;overflow-y:auto;margin:0 -4px;padding:0 4px 4px}
  .sessrow{position:relative;display:flex;align-items:center;margin:1px 0;border-radius:9px;transition:background var(--fast)}
  .sessrow:hover{background:rgba(255,255,255,.045)}
  .sessrow.active{background:var(--accent-soft)}
  .sess{display:block;flex:1;min-width:0;text-align:left;border:0;background:transparent;border-radius:9px;padding:8px 10px;color:var(--text-2);font:400 13.5px/1.35 var(--sans);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:color var(--fast)}
  .sessrow:hover .sess{color:var(--text)}
  .sessrow.active .sess{color:#eef8fb}
  .sess .ghosttag{color:var(--violet);margin-right:6px;font-size:11px;letter-spacing:.04em}
  .sessdel{flex:none;border:0;background:transparent;color:var(--faint);padding:6px 8px;border-radius:8px;opacity:0;transition:opacity var(--fast),color var(--fast)}
  .sessrow:hover .sessdel,.sessdel:focus-visible{opacity:1}
  .sessdel:hover{color:var(--danger);background:rgba(255,107,122,.1)}
  .sessdel svg{width:13px;height:13px}
  /* incognito: the composer picks up a violet edge so the mode is never a surprise */
  body.ghosting .composer{border-color:rgba(163,148,255,.5)}
  body.ghosting .newchat.ghost{background:rgba(163,148,255,.14);color:#d9d2ff}
  .railfoot{margin-top:10px;padding-top:10px;border-top:1px solid var(--hair)}
  .sysbtn{display:flex;align-items:center;gap:9px;width:100%;padding:9px 10px;border:0;border-radius:9px;background:transparent;color:var(--muted);font:500 12.5px var(--sans);text-align:left;transition:background var(--fast),color var(--fast)}
  .sysbtn:hover{background:rgba(255,255,255,.045);color:var(--text)}
  .sysbtn .dot{width:7px;height:7px;border-radius:50%;background:var(--faint);flex:none;transition:background .4s}
  .sysbtn span:nth-child(2){flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .sysbtn svg{width:14px;height:14px;opacity:.6}

  /* ---------- presence panel: the orb is the one luminous thing on the page ---------- */
  .stage{position:relative;display:flex;flex-direction:column;align-items:center;justify-content:center;min-width:0;overflow:hidden;isolation:isolate}
  .stage::before{content:'';position:absolute;left:50%;top:50%;width:min(82%,440px);aspect-ratio:1;transform:translate(-50%,-52%);border-radius:50%;pointer-events:none;z-index:-1;
    background:radial-gradient(circle,rgba(98,211,234,.17),rgba(122,110,255,.11) 42%,transparent 70%);filter:blur(30px);opacity:.7;transition:opacity var(--med) var(--ease),transform var(--med) var(--ease)}
  body[data-core="thinking"] .stage::before,body[data-core="listening"] .stage::before,body[data-core="speaking"] .stage::before{opacity:1;transform:translate(-50%,-52%) scale(1.14)}
  body.voicing .stage::before{background:radial-gradient(circle,rgba(150,136,255,.2),rgba(98,211,234,.09) 45%,transparent 70%)}
  .stage::after{content:'';position:absolute;top:0;bottom:0;right:0;width:1px;pointer-events:none;background:linear-gradient(180deg,transparent,var(--hair-2) 25%,var(--hair-2) 75%,transparent)}
  #core{display:block;position:absolute;inset:0;width:100%;height:100%}
  .corestate{position:absolute;left:0;right:0;bottom:34px;z-index:2;text-align:center;font:500 12.5px var(--sans);color:var(--core-color,var(--muted));transition:color var(--med),opacity var(--med)}
  .corestate b{font-weight:600;color:var(--text)}
  .corestate .blip{display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor;margin-right:7px;vertical-align:middle;opacity:.9}
  body.voicing .corestate{opacity:0}
  .voice{position:absolute;inset:0;z-index:5;display:none;flex-direction:column;align-items:center;justify-content:flex-end;gap:12px;padding:0 26px 30px;color:var(--core-color,var(--accent));pointer-events:none;
    background:linear-gradient(180deg,transparent 42%,rgba(9,13,20,.9) 78%)}
  .voice.on{display:flex}
  .voice>*{pointer-events:auto;position:relative}
  .bars{display:flex;align-items:center;gap:4px;height:26px}
  .bars i{width:3px;height:6px;background:currentColor;border-radius:3px;animation:bars 1s ease-in-out infinite}
  .bars i:nth-child(2){animation-delay:.12s}.bars i:nth-child(3){animation-delay:.24s}.bars i:nth-child(4){animation-delay:.36s}.bars i:nth-child(5){animation-delay:.48s}
  .voice[data-state="thinking"] .bars,.voice[data-state="idle"] .bars{opacity:.3}
  .vstate{font:500 13px var(--sans);color:currentColor}
  .vtrans{max-width:360px;min-height:48px;text-align:center;color:var(--text-2);font:400 15.5px/1.5 var(--sans)}
  .vdbg{font:11px var(--mono);color:var(--faint);min-height:14px}
  .vbtns{display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:4px}
  .vbtn{border:1px solid var(--hair-2);background:rgba(255,255,255,.04);color:var(--text-2);border-radius:999px;padding:9px 18px;font:500 13px var(--sans);transition:background var(--fast),color var(--fast),border-color var(--fast)}
  .vbtn:hover{background:rgba(255,255,255,.08);color:var(--text);border-color:var(--hair-3)}
  .vbtn.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
  .vbtn.primary:hover{background:var(--accent-2);border-color:var(--accent-2)}

  /* ---------- conversation ---------- */
  main.chatcol{display:flex;flex-direction:column;min-height:0;position:relative}
  .chat{flex:1;overflow-y:auto;padding:30px 0 10px}
  .chat>*{width:min(100% - 56px,820px);margin-left:auto;margin-right:auto}
  .empty{height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;gap:14px;padding-bottom:36px}
  .empty h1{font:600 28px/1.2 var(--display);letter-spacing:-.3px;color:var(--text);margin:0}
  .empty p{font:400 15px/1.6 var(--sans);color:var(--muted);max-width:470px;margin:0}
  .setupcard{width:100%;max-width:540px;text-align:left;background:var(--surface-2);border:1px solid rgba(255,107,122,.3);border-radius:var(--r-md);padding:14px 16px;font-size:14px;line-height:1.6;color:var(--text-2)}
  .setupcard b{display:block;font-weight:600;color:var(--text);margin-bottom:4px}
  .setupcard code{font:12.5px var(--mono);background:rgba(255,255,255,.06);border-radius:5px;padding:1px 6px;color:var(--accent-2)}
  .suggest{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;max-width:620px;margin-top:6px}
  .scard{border:1px solid var(--hair-2);background:rgba(255,255,255,.03);color:var(--text-2);border-radius:999px;padding:9px 15px;font:400 13.5px var(--sans);text-align:left;transition:background var(--fast),border-color var(--fast),color var(--fast)}
  .scard:hover{background:rgba(255,255,255,.06);border-color:var(--accent-line);color:var(--text)}

  .msg{display:flex;gap:12px;margin-bottom:22px;animation:rise .22s var(--ease) both;position:relative}
  .msg.user{flex-direction:row-reverse}
  .msg .av{margin-top:2px;font-size:0;color:transparent}
  .msg .content{flex:1;min-width:0}
  .msg.user .content{flex:0 1 auto;max-width:78%}
  .msg .who{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
  .md{font:400 15px/1.7 var(--sans);color:var(--text);word-wrap:break-word;overflow-wrap:anywhere;min-width:0}
  .msg.user .md{background:var(--surface-3);border-radius:18px 18px 6px 18px;padding:11px 16px;color:var(--text)}
  .md p{margin:.45em 0} .md p:first-child{margin-top:0} .md p:last-child{margin-bottom:0}
  .md h3{font:600 17px/1.3 var(--display);margin:.9em 0 .35em;color:var(--text)} .md h4{font:600 15px/1.3 var(--display);margin:.8em 0 .3em;color:var(--text)}
  .md ul,.md ol{margin:.35em 0;padding-left:1.4em} .md li{margin:.2em 0}
  .md code{font:13px var(--mono);background:rgba(255,255,255,.06);border-radius:5px;padding:1px 6px;color:var(--accent-2)}
  .md pre{background:#0a0f17;border:1px solid var(--hair);border-radius:10px;padding:12px 14px;overflow:auto;margin:.6em 0;max-width:100%}
  .md pre code{background:none;padding:0;color:var(--text-2)}
  .md strong{color:#fff;font-weight:600} .md a{color:var(--accent);text-decoration:none} .md a:hover{text-decoration:underline}
  /* generated pictures and data tables in a reply */
  .md img{display:block;max-width:min(100%,560px);height:auto;margin:.6em 0;border:1px solid var(--hair);border-radius:var(--r-md);background:var(--surface-2)}
  .md .tw{overflow-x:auto;margin:.6em 0;border:1px solid var(--hair);border-radius:var(--r-md)}
  .md table{border-collapse:collapse;width:100%;font-size:13.5px}
  .md th,.md td{text-align:left;padding:7px 12px;border-bottom:1px solid var(--hair);vertical-align:top}
  .md th{font-weight:600;color:var(--text-2);background:rgba(255,255,255,.03);white-space:nowrap}
  .md tbody tr:last-child td{border-bottom:none}
  .md .mathblock{margin:.55em 0;font-size:1.04em;color:var(--text);overflow-x:auto}
  .md .frac{display:inline-flex;flex-direction:column;vertical-align:middle;text-align:center;
    font-size:.92em;line-height:1.15;margin:0 .18em}
  .md .frac .num{border-bottom:1px solid currentColor;padding:0 .28em}
  .md .frac .den{padding:0 .28em}
  .md sup,.md sub{font-size:.72em;line-height:0}
  .md .dgm{margin:.7em 0;padding:10px 12px;border:1px solid var(--hair);border-radius:var(--r-md);background:var(--surface);overflow-x:auto}
  /* save / copy / export actions attached to a picture or a table */
  .md .figure{position:relative;display:inline-block;max-width:100%}
  .md .figure img{margin:.6em 0}
  .md .oactions{display:flex;gap:6px}
  .md .figure .oactions{position:absolute;top:14px;right:8px;opacity:0;transition:opacity var(--fast)}
  .md .figure:hover .oactions,.md .figure:focus-within .oactions{opacity:1}
  .md .tableacts{margin:-2px 0 .8em}
  .oact{border:1px solid var(--hair-2);background:rgba(12,17,26,.82);color:var(--text-2);
    border-radius:7px;padding:3px 9px;font:500 11.5px/1.5 var(--sans);letter-spacing:.02em;
    backdrop-filter:blur(6px);transition:color var(--fast),border-color var(--fast),background var(--fast)}
  .oact:hover{color:var(--text);border-color:var(--accent-line);background:var(--surface-3)}
  /* a written document handed back as a download */
  .md a.doclink{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--hair-2);
    background:var(--surface-2);border-radius:9px;padding:7px 12px;margin:.3em 0;
    color:var(--text);text-decoration:none;font-weight:500}
  .md a.doclink:hover{border-color:var(--accent-line);background:var(--surface-3);text-decoration:none}
  .md a.doclink .dockind{font:600 10.5px/1 var(--mono);letter-spacing:.06em;color:var(--accent);
    border:1px solid var(--accent-line);border-radius:5px;padding:3px 5px}
  .msg.err .md{color:var(--danger)}
  .att{display:inline-flex;align-items:center;gap:6px;margin:8px 6px 0 0;background:rgba(255,255,255,.04);border:1px solid var(--hair-2);border-radius:8px;padding:5px 10px;font:12.5px var(--sans);color:var(--text-2)}
  .att .ic{color:var(--muted)}
  .copybtn{display:inline-block;margin-top:6px;border:0;background:transparent;color:var(--faint);font:12px var(--sans);padding:4px 8px;margin-left:-8px;border-radius:6px;opacity:0;transition:opacity var(--fast),color var(--fast),background var(--fast)}
  .msg:hover .copybtn,.copybtn:focus-visible{opacity:1}
  @media(hover:none){.copybtn{opacity:.7}}
  .copybtn:hover{color:var(--text);background:rgba(255,255,255,.05)}
  .meta{display:inline-block;margin:6px 10px 0 0;font:11px var(--mono);color:var(--faint)}
  .det{margin-top:6px} .det>summary{font:12px var(--sans);color:var(--faint);cursor:pointer;list-style:none}
  .det>summary::-webkit-details-marker{display:none} .det>summary::before{content:'\25B8  '} .det[open]>summary::before{content:'\25BE  '}
  .data{margin-top:6px;font:11.5px/1.5 var(--mono);color:var(--muted);background:#0a0f17;border:1px solid var(--hair);border-radius:8px;padding:10px 12px;max-height:220px;max-width:100%;overflow:auto;white-space:pre-wrap}
  /* Thinking indicator. Adapted from the "big-octopus-60" CSS loader by alexruix on
     uiverse.io (MIT): a travelling pill that stretches and snaps back. Rewritten onto the
     accent tokens, sized for a chat line rather than a page, and carrying no glow, so the
     orb stays the only glowing thing. The three blinking dots it replaces read as
     "stalled" more than "working". */
  .think{position:relative;display:inline-block;width:64px;height:8px;vertical-align:middle}
  .think i{position:absolute;bottom:0;left:0;display:block;height:8px;width:8px;border-radius:50px;
    background:var(--accent);animation:think-run 2.4s ease both infinite}
  .think i::before{content:'';position:absolute;top:0;left:0;height:100%;width:100%;
    border-radius:inherit;background:var(--accent-2);animation:think-fill 2.4s ease both infinite}
  @keyframes think-run{
    0%{width:8px;transform:translateX(0)}
    40%{width:100%;transform:translateX(0)}
    80%{width:8px;transform:translateX(56px)}
    90%{width:100%;transform:translateX(0)}
    100%{width:8px;transform:translateX(0)}}
  @keyframes think-fill{
    0%{width:8px}
    40%{width:80%}
    80%{width:100%}
    90%{width:80%}
    100%{width:8px}}
  .tiernote{font:12.5px var(--sans);color:var(--muted);margin-left:8px}
  .trace{margin:2px 0 4px;border:1px solid var(--hair-2);border-radius:12px;background:var(--surface);overflow:hidden}
  .trace .thead{font:500 12.5px var(--sans);color:var(--accent);padding:9px 12px;border-bottom:1px solid var(--hair);display:flex;gap:8px;align-items:center}
  .trace .thead .gdot{width:7px;height:7px;border-radius:50%;background:var(--accent);animation:pulse 1.3s infinite}
  .trace.done .thead{color:var(--text-2)} .trace.done .thead .gdot{background:var(--ok);animation:none}
  .trace.fail .thead{color:var(--danger)} .trace.fail .thead .gdot{background:var(--danger);animation:none}
  .tstep{padding:9px 12px;border-bottom:1px solid var(--hair);font-size:13px;line-height:1.5}
  .tstep:last-child{border-bottom:0}
  .tstep .tn{font:12px var(--sans);color:var(--muted)}
  .tstep .tcmd{font:12px var(--mono);color:var(--text);background:rgba(255,255,255,.05);border-radius:5px;padding:2px 7px;display:inline-block;margin:3px 0}
  .tstep .tobs{color:var(--muted);font-size:12.5px}
  .tstep.failed .tcmd{color:var(--danger)}

  /* ---------- composer ---------- */
  .composer{padding:8px 28px 14px;position:relative}
  .composer>*{width:min(100%,820px);margin-left:auto;margin-right:auto}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
  .chip{display:inline-flex;align-items:center;gap:7px;background:var(--surface-2);border:1px solid var(--hair-2);border-radius:9px;padding:6px 8px 6px 10px;font:12.5px var(--sans);color:var(--text-2)}
  .chip .ic{color:var(--muted)}
  .chip .rm{border:0;background:transparent;color:var(--muted);font-size:15px;line-height:1;padding:0 3px;border-radius:5px}
  .chip .rm:hover{color:var(--danger);background:rgba(255,255,255,.05)}
  .box{display:flex;align-items:flex-end;gap:4px;background:var(--surface-2);border:1px solid var(--hair-2);border-radius:var(--r-xl);padding:8px 8px 8px 8px;box-shadow:var(--shadow-1);transition:border-color var(--fast),box-shadow var(--fast)}
  .box:focus-within{border-color:var(--accent-line);box-shadow:0 0 0 3px rgba(98,211,234,.1),var(--shadow-1)}
  #ta{flex:1;min-width:0;background:transparent;border:0;outline:0;color:var(--text);font:400 15px/1.5 var(--sans);resize:none;max-height:160px;padding:9px 6px;min-height:40px}
  #ta::placeholder{color:var(--faint)}
  .iconbtn{width:38px;height:38px;flex:none;display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:11px;background:transparent;color:var(--muted);position:relative;transition:background var(--fast),color var(--fast)}
  .iconbtn svg{width:19px;height:19px}
  .iconbtn:hover{background:rgba(255,255,255,.06);color:var(--text)}
  .iconbtn.live{color:var(--accent)}
  .iconbtn.live::after{content:'';position:absolute;inset:0;border-radius:11px;border:1.5px solid var(--accent);animation:ring 1.1s ease-out infinite}
  #agentBtn.on{color:var(--accent);background:var(--accent-soft)}
  .voicetoggle{display:inline-flex;align-items:center;gap:7px;height:38px;padding:0 13px 0 11px;flex:none;border-radius:999px;border:1px solid var(--hair-2);background:transparent;color:var(--muted);font:500 13px var(--sans);transition:background var(--fast),color var(--fast),border-color var(--fast)}
  .voicetoggle:hover{color:var(--text);border-color:var(--hair-3);background:rgba(255,255,255,.04)}
  .voicetoggle .vico{display:flex} .voicetoggle .vico svg{width:17px;height:17px}
  .voicetoggle .on-label{display:none}
  .voicetoggle.on{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
  .voicetoggle.on:hover{background:var(--accent-2);border-color:var(--accent-2)}
  .voicetoggle.on .off-label{display:none} .voicetoggle.on .on-label{display:inline}
  .voicetoggle.on .vico svg{animation:blink 1.1s infinite}
  .sendbtn{width:38px;height:38px;flex:none;display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:50%;background:var(--accent);color:var(--accent-ink);transition:background var(--fast),transform var(--fast)}
  .sendbtn svg{width:18px;height:18px}
  .sendbtn:hover{background:var(--accent-2)} .sendbtn:active{transform:scale(.95)}
  .sendbtn.stop{background:var(--danger);color:#fff;animation:stoppulse 1.2s ease-in-out infinite}
  @keyframes stoppulse{0%,100%{box-shadow:0 0 0 0 rgba(255,107,122,.45)}50%{box-shadow:0 0 0 7px rgba(255,107,122,0)}}
  .hint{margin-top:9px;text-align:center;font:12px var(--sans);color:var(--faint)}
  .drop{position:absolute;inset:12px;z-index:6;display:none;align-items:center;justify-content:center;border:1.5px dashed var(--accent-line);border-radius:var(--r-lg);background:rgba(98,211,234,.06);backdrop-filter:blur(2px);font:500 14px var(--sans);color:var(--accent-2);pointer-events:none}
  .drop.on{display:flex}

  /* ---------- system status drawer ---------- */
  .scrim{position:fixed;inset:0;z-index:110;background:rgba(3,5,9,.4);opacity:0;pointer-events:none;transition:opacity var(--med)}
  .scrim.open{opacity:1;pointer-events:auto}
  .drawer{position:fixed;top:0;right:0;bottom:0;width:min(400px,100vw);z-index:120;display:flex;flex-direction:column;background:rgba(12,17,25,.97);border-left:1px solid var(--hair-2);box-shadow:var(--shadow-2);backdrop-filter:blur(16px);
    transform:translateX(102%);visibility:hidden;transition:transform var(--med) var(--ease),visibility 0s linear var(--med)}
  .drawer.open{transform:none;visibility:visible;transition:transform var(--med) var(--ease),visibility 0s}
  .drawer-head{display:flex;align-items:center;gap:10px;padding:14px 12px 12px 18px;border-bottom:1px solid var(--hair)}
  .drawer-head h2{flex:1;margin:0;font:600 15px var(--display);color:var(--text)}
  .drawer-body{flex:1;min-height:0;overflow-y:auto;padding:6px 12px 24px}
  .dsec{padding:8px 0 4px}
  .dsec>h3{font:500 11.5px var(--sans);color:var(--faint);margin:14px 8px 6px}
  .crow{display:flex;align-items:center;gap:10px;padding:8px 8px;font:13px var(--sans);border-bottom:1px solid var(--hair)}
  .crow:last-child{border-bottom:0}
  .crow .d{width:7px;height:7px;border-radius:50%;background:var(--ok);flex:none} .crow .d.off{background:var(--faint)} .crow .d.warn{background:var(--warn)}
  .crow .k{color:var(--muted)} .crow .v{margin-left:auto;font:12px var(--mono);color:var(--text-2);text-align:right;max-width:58%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .metric{margin:9px 8px}
  .metric .top{display:flex;justify-content:space-between;font:12.5px var(--sans);color:var(--muted);margin-bottom:6px}
  .metric .top b{font:500 12px var(--mono);color:var(--text)}
  .bar{height:5px;border-radius:5px;background:rgba(255,255,255,.07);overflow:hidden}
  .bar i{display:block;height:100%;background:var(--accent);opacity:.85;transition:width .6s var(--ease)}
  .bar.g i{background:var(--violet)}
  .vstat{display:flex;align-items:center;gap:8px;padding:8px;font:13px var(--sans);color:var(--text-2)}
  .vstat .d{width:7px;height:7px;border-radius:50%;background:var(--ok)} .vstat .d.off{background:var(--faint)}
  .vaultsearch{width:calc(100% - 16px);margin:2px 8px 6px;background:rgba(255,255,255,.04);border:1px solid var(--hair-2);border-radius:9px;color:var(--text);font:13px var(--sans);padding:8px 10px;outline:0;transition:border-color var(--fast)}
  .vaultsearch:focus{border-color:var(--accent-line)}
  .note{display:block;width:100%;text-align:left;border:0;background:transparent;font:13px var(--sans);color:var(--text-2);padding:6px 8px;border-radius:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .note.clk{transition:background var(--fast),color var(--fast)}
  .note.clk:hover{background:rgba(255,255,255,.05);color:var(--text)}
  details.conn{border-radius:var(--r-md);background:rgba(255,255,255,.025);margin:6px 0;overflow:hidden}
  details.conn>summary{display:flex;align-items:center;font:500 13.5px var(--sans);color:var(--text-2);padding:11px 12px;cursor:pointer;list-style:none;user-select:none;transition:color var(--fast)}
  details.conn>summary:hover{color:var(--text)}
  details.conn>summary::-webkit-details-marker{display:none}
  details.conn>summary::after{content:'';width:7px;height:7px;margin-left:auto;margin-right:3px;border-right:1.5px solid var(--faint);border-bottom:1.5px solid var(--faint);transform:rotate(-45deg);transition:transform var(--fast)}
  details.conn[open]>summary::after{transform:rotate(45deg)}
  details.conn>*:not(summary){margin-left:6px;margin-right:6px}
  details.conn>*:last-child{margin-bottom:8px}
  .agentSummary{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:4px 8px 10px}
  .agentStat{background:rgba(255,255,255,.03);border-radius:9px;padding:8px 6px;text-align:center}
  .agentStat b{display:block;font:600 16px var(--display);color:var(--text)}
  .agentStat span{font:11.5px var(--sans);color:var(--muted)}
  .agentcard{display:block;width:calc(100% - 12px);margin:4px 6px;background:transparent;border:1px solid transparent;border-radius:10px;padding:9px 10px;text-align:left;color:var(--text);transition:background var(--fast),border-color var(--fast)}
  .agentcard:hover{background:rgba(255,255,255,.04);border-color:var(--hair)}
  .agentcard .top{display:flex;align-items:center;gap:8px}
  .agentcard .dot{width:7px;height:7px;border-radius:50%;background:var(--faint);flex:none}
  .agentcard.idle .dot{background:var(--ok)}
  .agentcard.working .dot{background:var(--accent);animation:blink 1s infinite}
  .agentcard.unavailable .dot{background:var(--faint)}
  .agentcard b{font:500 13px var(--sans)}
  .agentcard .status{margin-left:auto;font:11.5px var(--sans);color:var(--muted)}
  .agentcard .role{font-size:12px;color:var(--muted);line-height:1.4;margin-top:4px}
  .agentcard .task{font:11.5px var(--mono);color:var(--text-2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:5px}
  .schedform{display:flex;flex-direction:column;gap:6px;margin:4px 6px 6px}
  .schedform select,.schedform input,.mapform input,.jobform input,.jobform select,.resumebox input,.resumebox textarea,.tlarea,.profilegrid input,.jobrow select,.pcard select{
    background:rgba(255,255,255,.04);border:1px solid var(--hair-2);border-radius:9px;color:var(--text);font:13.5px var(--sans);padding:8px 10px;outline:0;transition:border-color var(--fast)}
  .schedform select:focus,.schedform input:focus,.mapform input:focus,.jobform input:focus,.jobform select:focus,.resumebox input:focus,.resumebox textarea:focus,.tlarea:focus,.profilegrid input:focus,.jobrow select:focus,.pcard select:focus{border-color:var(--accent-line)}
  select option{background:var(--surface-2);color:var(--text)}
  .schedform button,.mapform button,.jobform button,.resumebox button,.rrow button,.pipebar button,.pcard button,.tripstop button{
    border:1px solid var(--hair-2);background:rgba(255,255,255,.04);color:var(--text-2);border-radius:9px;padding:8px 13px;font:500 13px var(--sans);transition:background var(--fast),color var(--fast),border-color var(--fast)}
  .schedform button:hover,.mapform button:hover,.jobform button:hover,.resumebox button:hover,.rrow button:hover,.pipebar button:hover,.pcard button:hover{background:rgba(255,255,255,.08);color:var(--text);border-color:var(--hair-3)}
  #schedAdd,#mapGo,#tripGo,#jobAdd,#tlGo,#rsSave,#pullBtn{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)}
  #schedAdd:hover,#mapGo:hover,#tripGo:hover,#jobAdd:hover,#tlGo:hover,#rsSave:hover,#pullBtn:hover{background:var(--accent-2);border-color:var(--accent-2);color:var(--accent-ink)}
  button:disabled{opacity:.5;cursor:default}
  .schedmsg,.mapmsg{font:12px var(--sans);color:var(--muted);min-height:14px;margin:2px 8px}
  .schedmsg.err,.mapmsg.err{color:var(--warn)}
  .schedcard,.runcard{margin:4px 6px;background:rgba(255,255,255,.03);border-radius:10px;padding:9px 11px}
  .schedcard.off{opacity:.5}
  .schedcard .top,.runcard .top{display:flex;align-items:center;gap:8px}
  .schedcard .kind{font:500 11.5px var(--sans);color:var(--accent)}
  .schedcard .when,.runcard .when{margin-left:auto;font:11.5px var(--mono);color:var(--muted)}
  .schedcard .spec,.runcard .goal{font-size:13px;color:var(--text);line-height:1.4;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .schedcard .meta,.runcard .meta{font:11.5px var(--sans);color:var(--muted);margin-top:5px;display:flex;gap:10px;align-items:center}
  .schedcard .meta button{border:0;background:none;color:var(--muted);font:500 12px var(--sans);padding:0}
  .schedcard .meta button:hover{color:var(--accent)}
  .runcard summary{list-style:none;cursor:pointer}
  .runcard summary::-webkit-details-marker{display:none}
  .runcard .status{font:500 11.5px var(--sans);color:var(--ok)}
  .runcard.failed .status{color:var(--danger)} .runcard.stopped .status{color:var(--warn)}
  .runcard .answer{font-size:12.5px;color:var(--muted);line-height:1.45;margin-top:8px;white-space:pre-wrap}
  .runstep{border-top:1px solid var(--hair);padding-top:7px;margin-top:7px}
  .runstep .meta{margin-top:0}
  .runstep .cmd{font:11.5px var(--mono);color:var(--accent-2);margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .runstep .txt{font-size:12.5px;color:var(--muted);line-height:1.4;margin-top:4px;white-space:pre-wrap}
  .mapform{display:flex;gap:6px;margin:4px 6px 6px}
  .mapform input{flex:1;min-width:0}
  .mapframe{width:calc(100% - 12px);height:210px;margin:6px;border:1px solid var(--hair);border-radius:10px;background:var(--surface)}
  .mappt{font-size:12.5px;color:var(--text);margin:3px 8px;line-height:1.35}
  .mappt b{color:var(--muted);font-weight:500;margin-right:6px}
  .maplink{display:block;margin:8px 8px 4px;font-size:12.5px;color:var(--accent);text-decoration:none}
  .maplink:hover{text-decoration:underline}
  .tripstop{display:flex;align-items:center;gap:6px;margin:4px 8px;font:13px var(--sans);color:var(--text)}
  .tripstop .seq{width:18px;height:18px;flex:none;border-radius:50%;background:var(--accent);color:var(--accent-ink);font:600 10px var(--sans);display:flex;align-items:center;justify-content:center}
  .tripstop .nm{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .tripstop button{padding:2px 7px;border-radius:6px;font-size:12px}
  .tripsvg{width:calc(100% - 12px);height:150px;margin:6px;border:1px solid var(--hair);border-radius:10px;background:var(--surface)}
  .tripsvg .route{fill:none;stroke:var(--accent);stroke-width:2.5;stroke-linejoin:round;stroke-linecap:round}
  .tripsvg .stopdot{fill:var(--accent-2)}
  .tripsvg .stopnum{fill:var(--accent-ink);font-size:8px;font-weight:700;font-family:var(--sans)}
  .tripleg{font-size:12.5px;color:var(--text);margin:3px 8px;line-height:1.4}
  .tripleg .d{color:var(--muted);font:11.5px var(--mono)}
  .triptotal{font:600 13px var(--sans);color:var(--accent-2);margin:8px 8px 2px}

  /* ---------- note viewer ---------- */
  .noteviewer{position:fixed;inset:72px 16px 20px;z-index:130;max-width:860px;margin:auto;display:none;flex-direction:column;background:var(--surface-2);border:1px solid var(--hair-2);border-radius:var(--r-lg);box-shadow:var(--shadow-2);overflow:hidden}
  .noteviewer.open{display:flex}
  .nv-head{display:flex;align-items:center;gap:10px;padding:12px 12px 12px 18px;border-bottom:1px solid var(--hair)}
  .nv-title{flex:1;font:600 15px var(--display);color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .nv-close{width:32px;height:32px;border:0;border-radius:9px;background:transparent;color:var(--muted);font-size:18px;line-height:1}
  .nv-close:hover{background:rgba(255,255,255,.06);color:var(--text)}
  .nv-body{flex:1;overflow:auto;padding:18px 22px;font-size:14.5px;line-height:1.65;color:var(--text)}
  .nv-links{padding:10px 16px;border-top:1px solid var(--hair);display:flex;flex-wrap:wrap;gap:6px;align-items:center;max-height:40%;overflow:auto}
  .nv-links .lk{border:1px solid var(--hair-2);background:rgba(255,255,255,.03);color:var(--text-2);border-radius:999px;padding:5px 11px;font:12.5px var(--sans);transition:background var(--fast),border-color var(--fast)}
  .nv-links .lk:hover{background:rgba(255,255,255,.07);border-color:var(--accent-line);color:var(--text)}
  .nv-links .lbl{font:500 11.5px var(--sans);color:var(--faint);margin-right:2px}

  /* ---------- routed pages ---------- */
  .page{grid-column:1/-1;grid-row:2;display:none;overflow:auto;padding:28px 32px 40px}
  .page>*{max-width:1180px;margin-left:auto;margin-right:auto}
  body[data-view="overview"] .left,body[data-view="overview"] .stage,body[data-view="overview"] main.chatcol,
  body[data-view="jobs"] .left,body[data-view="jobs"] .stage,body[data-view="jobs"] main.chatcol,
  body[data-view="pipeline"] .left,body[data-view="pipeline"] .stage,body[data-view="pipeline"] main.chatcol{display:none}
  body[data-view="overview"] #page-overview{display:block}
  body[data-view="jobs"] #page-jobs{display:block}
  body[data-view="pipeline"] #page-pipeline{display:block}
  .pagehead{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:18px}
  .pagehead h2{font:600 22px/1.2 var(--display);letter-spacing:-.2px;color:var(--text);margin:0}
  .pagehead .sub{margin-left:auto;font:12.5px var(--sans);color:var(--muted);overflow-wrap:anywhere}
  .statcards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}
  .statcard{background:var(--surface);border:1px solid var(--hair);border-radius:var(--r-lg);padding:16px 18px}
  .statcard .k{font:500 12.5px var(--sans);color:var(--muted)}
  .statcard .v{font:600 28px/1.1 var(--display);letter-spacing:-.4px;color:var(--text);margin-top:6px}
  .statcard .v small{font:400 13px var(--sans);color:var(--muted);letter-spacing:0}
  .charts{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:18px}
  .chartcard{background:var(--surface);border:1px solid var(--hair);border-radius:var(--r-lg);padding:16px 18px;min-width:0}
  .chartcard .t{font:600 13.5px var(--sans);color:var(--text-2);margin-bottom:12px}
  .chartcard summary{font:600 13.5px var(--sans);color:var(--text-2);cursor:pointer}
  .jobform{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px}
  .jobform>*{max-width:100%;min-width:0}
  .jobform input.company{flex:1;min-width:160px}
  .jobrow{display:flex;flex-wrap:wrap;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--hair);border-radius:12px;padding:10px 14px;margin-bottom:8px}
  .jobrow .co{font-size:14px;color:var(--text);min-width:140px}
  .jobrow .co small{display:block;color:var(--muted);font-size:12px;margin-top:1px}
  .jobrow select{padding:5px 8px;font-size:12.5px}
  .jobrow .nd{font:12px var(--mono);color:var(--muted)}
  .jobrow .rm{margin-left:auto;border:0;background:none;color:var(--muted);font-size:17px;padding:2px 6px;border-radius:6px}
  .jobrow .rm:hover{color:var(--danger);background:rgba(255,255,255,.05)}
  .tlarea{width:100%;min-height:90px;resize:vertical;margin-bottom:8px;font-family:var(--mono);font-size:12.5px}
  .resumebox{display:flex;flex-direction:column;gap:10px;margin:0 0 14px}
  .resumebox textarea{width:100%;min-height:90px;resize:vertical;font-family:var(--mono);font-size:12.5px}
  .resumebox .rrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
  .resumebox input{flex:1;min-width:160px;max-width:100%}
  .rstat{font:12.5px var(--sans);color:var(--muted)}
  .profilegrid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
  .profilegrid label{font:12.5px var(--sans);color:var(--muted)}
  .profilegrid input{display:block;width:100%;margin-top:6px;min-width:0}
  .pipebar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 14px}
  .pipebar .grow{flex:1}
  .pipebar label{display:inline-flex;align-items:center;gap:6px}
  .board{display:flex;gap:10px;overflow-x:auto;padding:2px 0 12px;align-items:flex-start}
  .col{flex:0 0 228px;background:rgba(255,255,255,.025);border-radius:12px;padding:10px}
  .col h4{margin:0 0 8px;font:500 12.5px var(--sans);color:var(--muted);display:flex;justify-content:space-between;text-transform:capitalize}
  .col h4 b{color:var(--text-2);font-weight:600}
  .pcard{background:var(--surface-2);border:1px solid var(--hair);border-radius:10px;padding:10px;margin-bottom:8px}
  .pcard .pco{font-size:13.5px;color:var(--text);font-weight:600;line-height:1.3}
  .pcard .prole{font-size:12px;color:var(--muted);margin:2px 0 7px}
  .pcard .prow{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .pcard button,.pcard select{min-height:30px;padding:4px 9px;font-size:12.5px;border-radius:8px}
  .score{font:600 11px var(--mono);padding:3px 7px;border-radius:999px;color:var(--accent-ink)}
  .score.s-hi{background:var(--ok)}.score.s-mid{background:var(--warn)}.score.s-lo{background:var(--danger);color:#fff}.score.s-na{background:rgba(255,255,255,.1);color:var(--text-2)}
  .badge{font:11.5px var(--sans);padding:3px 8px;border-radius:999px;border:1px solid var(--hair-2);color:var(--muted)}
  .badge.t-on{color:var(--ok);border-color:rgba(61,220,151,.35)}

  /* ---------- compact layout: chat only ---------- */
  body.compact .app{grid-template-columns:minmax(0,1fr)}
  body.compact .left,body.compact .stage{display:none}
  body.compact main.chatcol{grid-column:1/-1}
  body.compact .brand{min-width:0}

  /* ---------- motion ---------- */
  @keyframes rise{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
  @keyframes blink{0%,100%{opacity:.3}50%{opacity:1}}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
  @keyframes ring{from{opacity:.7;transform:scale(1)}to{opacity:0;transform:scale(1.5)}}
  @keyframes bars{0%,100%{height:6px}50%{height:22px}}

  /* ---------- responsive ---------- */
  @media(max-width:1100px){
    :root{--rail-w:224px}
    .app{grid-template-columns:var(--rail-w) minmax(0,1fr)}
    .stage{display:none}main.chatcol{grid-column:2}
    .brand{min-width:0}
  }
  @media(max-width:700px){
    .app{grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr)}
    .left,.stage{display:none}main.chatcol{grid-column:1}
    #mobileChats{display:inline-flex}
    body.showChats .left{display:flex;position:fixed;top:112px;bottom:0;left:0;width:min(86vw,320px);z-index:90;background:var(--bg-2);border-right:1px solid var(--hair-2);box-shadow:var(--shadow-2)}
    header{flex-wrap:wrap;padding:10px 12px;gap:8px;height:auto;min-height:56px}
    .brand .s{display:none}
    #nav{order:10;flex-basis:100%;overflow:auto;justify-content:space-between}
    .navbtn{padding:8px 11px;font-size:12.5px}
    .page{padding:16px 12px 32px}.charts,.profilegrid{grid-template-columns:1fr}
    .statcards{grid-template-columns:1fr 1fr}.pagehead .sub{margin-left:0}
    .chat{padding-top:18px}.chat>*{width:calc(100% - 24px)}
    .composer{padding:8px 12px 10px}
    .msg{gap:8px;margin-bottom:16px}.msg.user .content{max-width:88%}
    .md{font-size:14.5px}
    .empty h1{font-size:24px}
    button{min-height:36px}.hint{font-size:11.5px}
    .drawer{width:100vw;border-left:0}
  }
  @media(max-width:520px){
    .voicetoggle{width:38px;padding:0;justify-content:center}.voicetoggle .vlabel{display:none!important}
    .pill #healthText{display:none}.pill{padding:0 9px}
    /* two-row composer: the text field takes the full width, the actions sit beneath it */
    .box{flex-wrap:wrap;padding:6px 8px 8px}
    #ta{flex:1 0 100%;order:-1;min-height:36px;padding:8px 6px 4px}
    #agentBtn{margin-right:auto}
  }
  @media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important}}
  @media(prefers-reduced-motion:reduce){.think i{width:100%}}
</style>
</head>
<body data-view="chat">
<div class="app">
  <header>
    <div class="brand">
      <span class="mark" id="reactor" aria-hidden="true"></span>
      <div><div class="n">J.A.R.V.I.S</div><div class="s">Local assistant</div></div>
    </div>
    <nav class="nav" id="nav" aria-label="Sections">
      <button id="mobileChats" class="navbtn" aria-expanded="false" aria-controls="leftPanel">Chats</button>
      <button class="navbtn on" data-view="chat">Chat</button>
      <button class="navbtn" data-view="overview">Overview</button>
      <!-- Jobs and Pipeline are off the nav for now. The pages, routes and APIs are all
           still here and reachable at #/jobs and #/pipeline; only the buttons are gone.
           The version with them in the nav is preserved on feature/jobs-pipeline-nav. -->
    </nav>
    <div class="sp"></div>
    <button class="pill" id="healthPill" title="System status" aria-controls="sysDrawer" aria-expanded="false"><span class="dot" id="healthDot"></span><span id="healthText">Checking…</span></button>
    <div class="hud">
      <button class="hudbtn" id="hudBtn" title="Settings — layout, transparency, always on top" aria-haspopup="dialog">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/></svg>
      </button>
      <div class="hudpop" id="hudPop" role="dialog" aria-label="Settings">
        <button type="button" class="toggle" id="compactBtn" role="switch" aria-checked="false"><span>Compact layout<small>Chat only — hides the rail and the orb</small></span><span class="sw"></span></button>
        <button type="button" class="toggle" id="onTopToggle" role="switch" aria-checked="false"><span>Always on top<small>Desktop app only</small></span><span class="sw"></span></button>
        <button type="button" class="toggle" id="sttServer" role="switch" aria-checked="false"><span>Server speech<small id="sttNote">Checking…</small></span><span class="sw"></span></button>
        <button type="button" class="toggle" id="typeAnim" role="switch" aria-checked="true"><span>Typing animation<small>Reveal local answers gradually</small></span><span class="sw"></span></button>
        <div class="row">
          <div class="lbl"><span>Transparency</span><span id="opacityVal">100%</span></div>
          <input type="range" id="opacityRange" min="35" max="100" step="1" value="100" aria-label="Window transparency">
        </div>
        <div class="hint2" id="hudHint">Window effects apply in the desktop app.</div>
      </div>
    </div>
  </header>

  <aside class="left" id="leftPanel">
    <button class="newchat" id="newChat"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg><span>New chat</span></button>
    <button class="newchat ghost" id="newGhost" title="A chat that is never saved to this browser"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0-6 6v11l2-2 2 2 2-2 2 2 2-2 2 2V9a6 6 0 0 0-6-6z"/><path d="M9.5 10h.01M14.5 10h.01"/></svg><span>Incognito chat</span></button>
    <div class="seclbl">Recent</div>
    <div id="sessions"></div>
    <div class="railfoot">
      <button class="sysbtn" id="railStatus" title="System status" aria-controls="sysDrawer" aria-expanded="false"><span class="dot"></span><span id="railText">Checking…</span><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
    </div>
  </aside>

  <section class="stage" aria-label="Assistant presence">
    <canvas id="core"></canvas>
    <div class="corestate" id="corestate"><span class="blip"></span><b>Ready</b></div>
    <div class="voice" id="voice" data-state="listening">
      <div class="bars"><i></i><i></i><i></i><i></i><i></i></div>
      <div class="vstate" id="vstate">Listening</div>
      <div class="vtrans" id="vtrans">Say something…</div>
      <div id="vdbg" class="vdbg"></div>
      <div class="vbtns">
        <button class="vbtn primary" id="vint" title="Stop speaking and listen (Space)">Interrupt</button>
        <button class="vbtn" id="vend">End voice</button>
      </div>
    </div>
  </section>

  <main class="chatcol">
    <div class="chat" id="chat">
      <div class="empty" id="empty">
        <h1>How can I help, Jeevan?</h1>
        <p>Ask a question, attach a supported file, or use Voice. Files and memory work offline; connected models add chat and reasoning.</p>
        <div class="setupcard" id="setupCard" style="display:none"></div>
        <div class="suggest" id="suggest"></div>
      </div>
    </div>
    <div class="composer">
      <div class="chips" id="chips"></div>
      <div class="box">
        <button class="iconbtn" id="attachBtn" title="Attach a file"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M21.4 11.05l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.65 5.65l-9.2 9.2a2 2 0 0 1-2.83-2.83l8.5-8.5"/></svg></button>
        <button class="iconbtn" id="agentBtn" title="Agent mode — let J.A.R.V.I.S plan and act over multiple steps"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/></svg></button>
        <textarea id="ta" rows="1" placeholder="Message J.A.R.V.I.S…  (drop a file, or tap the mic)"></textarea>
        <button class="iconbtn" id="micBtn" title="Dictate"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>
        <button class="voicetoggle" id="voiceBtn" title="Voice mode — talk to J.A.R.V.I.S hands-free (tap again to stop)">
          <span class="vico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/></svg></span>
          <span class="vlabel off-label">Voice</span>
          <span class="vlabel on-label">Listening</span>
        </button>
        <button class="sendbtn" id="sendBtn" title="Send" aria-label="Send"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"/></svg></button>
      </div>
      <div class="hint" id="hint">Guarded mode — high-risk actions blocked here. Enter to send · Shift+Enter newline · Esc to stop · Ctrl+K new chat.</div>
    </div>
    <input type="file" id="file" multiple style="display:none" />
    <div class="drop" id="drop">Drop files to attach</div>
  </main>

  <section class="page" id="page-overview">
    <div class="pagehead"><h2>Overview</h2><span class="sub" id="ovSub"></span></div>
    <div class="statcards" id="ovCards"></div>
    <div class="charts">
      <div class="chartcard"><div class="t">System usage</div><div id="ovMetrics"></div></div>
      <div class="chartcard"><div class="t">Job pipeline</div><div id="ovFunnel"></div></div>
    </div>
  </section>

  <section class="page" id="page-jobs">
    <div class="pagehead"><h2>Job tracker</h2><span class="sub" id="jobSub"></span></div>
    <div class="statcards" id="jobStats"></div>
    <div class="charts">
      <div class="chartcard"><div class="t">Pipeline funnel</div><div id="jobFunnel"></div></div>
      <div class="chartcard"><div class="t">Applications per week</div><div id="jobTrend"></div></div>
    </div>
    <div class="jobform">
      <input class="company" id="jobCompany" type="text" placeholder="Company" autocomplete="off">
      <input id="jobRole" type="text" placeholder="Role (optional)" autocomplete="off">
      <select id="jobStage"></select>
      <button id="jobAdd" type="button">Add</button>
    </div>
    <div id="jobList"></div>
    <div class="chartcard" id="tailorCard" style="margin-top:18px">
      <div class="t">Tailor an application — paste your resume and the job description</div>
      <textarea id="tlResume" class="tlarea" placeholder="Paste your resume text…"></textarea>
      <textarea id="tlJD" class="tlarea" placeholder="Paste the job description…"></textarea>
      <div class="jobform" style="margin:8px 0 0">
        <input id="tlCompany" type="text" placeholder="Company (optional)">
        <input id="tlRole" type="text" placeholder="Role (optional)">
        <button id="tlGo" type="button">Tailor</button>
      </div>
      <div class="mapmsg" id="tlMsg"></div>
      <div class="md" id="tlResult" style="margin-top:10px;font-size:14px"></div>
    </div>
  </section>

  <section class="page" id="page-pipeline">
    <div class="pagehead"><h2>Pipeline</h2><span class="sub" id="pipeSub"></span></div>
    <div class="statcards" id="pipeStats"></div>
    <div class="chartcard resumebox" id="resumeCard">
      <div class="t">Base resume — used for live ATS scoring and tailoring</div>
      <textarea id="rsText" placeholder="Paste your resume text here, then Save…"></textarea>
      <div class="rrow">
        <button id="rsSave" type="button">Save resume</button>
        <input id="rsPath" type="text" placeholder="…or an absolute path to a PDF / DOCX / TXT">
        <button id="rsLoad" type="button">Load file</button>
        <span class="rstat" id="rsStat"></span>
      </div>
    </div>
    <details class="chartcard resumebox">
      <summary>Resume contact and certifications</summary>
      <p class="rstat">Optional overrides. Otherwise email, phone and profile links are read from the top of your base resume.</p>
      <div class="profilegrid">
        <label>Contact line<input id="rsContact" placeholder="Email · phone · profile URLs"></label>
        <label>Certifications<input id="rsCerts" placeholder="Certifications already earned"></label>
        <label>GitHub username<input id="rsGithub" placeholder="Username for repository links"></label>
      </div>
      <div><button id="rsProfileSave" type="button">Save profile</button></div>
    </details>
    <div class="pipebar">
      <button id="pullBtn" type="button">Pull from Jobright</button>
      <button id="clearBtn" type="button">Clear leads</button>
      <label class="rstat"><input type="checkbox" id="autoRef" checked> Auto-refresh</label>
      <span class="grow"></span>
      <span class="mapmsg" id="pipeMsg"></span>
    </div>
    <div class="board" id="pipeBoard"></div>
    <div class="chartcard" id="pkgCard" style="display:none;margin-top:14px">
      <div class="t" id="pkgTitle">Tailored package</div>
      <div class="md" id="pkgBody" style="font-size:14px"></div>
    </div>
  </section>
</div>

<div class="scrim" id="scrim"></div>
<aside class="drawer" id="sysDrawer" aria-label="System status" aria-hidden="true">
  <div class="drawer-head">
    <h2>System status</h2>
    <button class="hudbtn" id="drawerClose" title="Close"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg></button>
  </div>
  <div class="drawer-body">
    <div class="dsec">
      <h3>Models and connections</h3>
      <div id="connlist"></div>
      <h3>Usage</h3>
      <div id="metrics"></div>
      <h3>Memory vault</h3>
      <div class="vstat" id="vstat"><span class="d off"></span><span id="vtext">checking…</span></div>
      <input id="vaultSearch" class="vaultsearch" type="text" placeholder="Search notes…" autocomplete="off">
      <div id="notes"></div>
    </div>
    <div class="dsec">
      <h3>Tools</h3>
      <details class="conn" open>
        <summary>Tool activity</summary>
        <div id="agentSummary"></div>
        <div id="agentList"></div>
      </details>
      <details class="conn" id="schedPanel">
        <summary>Scheduled jobs</summary>
        <div class="schedform">
          <select id="schedKind"><option value="command">command</option><option value="agent">agent</option></select>
          <input id="schedWhen" type="text" placeholder="When — e.g. daily at 08:00" autocomplete="off">
          <input id="schedSpec" type="text" placeholder="Command or goal to run" autocomplete="off">
          <button id="schedAdd" type="button">Schedule it</button>
          <div class="schedmsg" id="schedMsg"></div>
        </div>
        <div id="schedList"></div>
      </details>
      <details class="conn" id="runsPanel">
        <summary>Agent runs</summary>
        <div id="runsList"></div>
      </details>
      <details class="conn" id="mapPanel">
        <summary>Map</summary>
        <div class="mapform">
          <input id="mapQuery" type="text" placeholder="Place — or  origin to destination" autocomplete="off">
          <button id="mapGo" type="button">Show</button>
        </div>
        <div class="mapmsg" id="mapMsg"></div>
        <div id="mapView"></div>
      </details>
      <details class="conn" id="tripPanel">
        <summary>Trip planner</summary>
        <div class="mapform">
          <input id="tripStop" type="text" placeholder="Add a stop — e.g. Austin, TX" autocomplete="off">
          <button id="tripAdd" type="button">Add</button>
        </div>
        <div id="tripStops"></div>
        <div class="mapform" style="margin-top:2px">
          <button id="tripGo" type="button" style="flex:1">Plan trip</button>
        </div>
        <div class="mapmsg" id="tripMsg"></div>
        <div id="tripView"></div>
      </details>
    </div>
  </div>
</aside>

<div class="noteviewer" id="noteViewer" role="dialog" aria-label="Note">
  <div class="nv-head">
    <span class="nv-title" id="nvTitle">Note</span>
    <button class="nv-close" id="nvClose" title="Close">&times;</button>
  </div>
  <div class="nv-body md" id="nvBody"></div>
  <div class="nv-links" id="nvLinks"></div>
</div>
<script nonce="{{NONCE}}">
  const nativeFetch=window.fetch.bind(window);
  window.fetch=(input,options={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    const same=url.origin===location.origin;
    if(same){
      const headers=new Headers(options.headers||(input instanceof Request?input.headers:undefined));
      headers.set('X-Jarvis-Token','{{API_TOKEN}}');
      options={...options,headers};
    }
    const p=nativeFetch(input,options);
    if(!same)return p;
    // The API token is per server process. If the server was restarted this tab's token
    // goes stale and same-origin calls 403 — reload once to pick up a fresh token rather
    // than dead-ending. A 5s guard prevents a reload loop if the 403 is something else.
    return p.then(r=>{
      if(r.status===403){
        let last=0; try{last=+sessionStorage.getItem('jarvisTokReload')||0;}catch(e){}
        if(Date.now()-last>5000){try{sessionStorage.setItem('jarvisTokReload',String(Date.now()));}catch(e){}location.reload();}
      }
      return r;
    });
  };
  const chat=document.getElementById('chat'), ta=document.getElementById('ta'), sendBtn=document.getElementById('sendBtn'),
        attachBtn=document.getElementById('attachBtn'), fileIn=document.getElementById('file'), chips=document.getElementById('chips'),
        micBtn=document.getElementById('micBtn'), reactor=document.getElementById('reactor'), drop=document.getElementById('drop'),
        voiceBtn=document.getElementById('voiceBtn'), voice=document.getElementById('voice'), vstate=document.getElementById('vstate'),
        vtrans=document.getElementById('vtrans'), vend=document.getElementById('vend'), vint=document.getElementById('vint'),
        sessionsEl=document.getElementById('sessions'), agentBtn=document.getElementById('agentBtn'),
        hint=document.getElementById('hint');
  let attachments=[], busy=false, voiceActive=false, currentAbort=null, agentMode=false;
  const SEND_ICON=sendBtn.innerHTML, STOP_ICON='<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2.5"/></svg>';

  /* ---- AI core animation (Iron-Man / Jarvis style) ---- */
  const coreCanvas=document.getElementById('core'), cctx=coreCanvas.getContext('2d'), corestate=document.getElementById('corestate');
  let coreState='idle', activeTier='fast';
  const cssVar=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const TIER_COLORS={fast:cssVar('--accent'),smart:cssVar('--violet'),ultra:cssVar('--violet-2')};   // cyan -> indigo -> violet, from the CSS palette
  const VOICE_COLOR=cssVar('--voice');                                  // violet shift in voice mode
  // planner.model can also be 'openrouter' / 'unavailable' (cross-provider fallback, nothing reachable)
  const TIER_NAME={fast:'fast model',smart:'smart model',ultra:'reasoning model',openrouter:'backup model',unavailable:'no model reachable'};
  const tierName=t=>TIER_NAME[t]||t||'';
  function coreColor(){
    if(coreState==='listening')return VOICE_COLOR;
    if(coreState==='thinking'||coreState==='speaking')return TIER_COLORS[activeTier]||TIER_COLORS.fast;
    return voiceActive?VOICE_COLOR:TIER_COLORS.fast;
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
    if(s==='thinking')label= activeTier==='ultra' ? '<b>Reasoning</b> · this can take a moment' : '<b>Thinking</b> · '+tierName(activeTier);
    else if(s==='speaking')label='<b>Speaking</b> · '+tierName(activeTier);
    else if(s==='listening')label='<b>Listening</b>';
    else label='<b>Ready</b>';
    document.body.dataset.core=s;   // drives the ambient glow behind the orb
    document.body.style.setProperty('--core-color',col);   // .corestate and the voice overlay both read it
    corestate.className='corestate';corestate.innerHTML='<span class="blip"></span>'+label;
    pulseCore();            // ripple the particle sphere on every state change
  }
  /* ---- 3D particle sphere: a cloud of light points that rotates, breathes,
        energises while J.A.R.V.I.S works, and scatters under the cursor ---- */
  const NP=760, pts=[];
  (function(){const gold=Math.PI*(3-Math.sqrt(5));for(let i=0;i<NP;i++){const y=1-(i/(NP-1))*2,rr=Math.sqrt(1-y*y),th=gold*i;
    pts.push({x:Math.cos(th)*rr,y:y,z:Math.sin(th)*rr,ph:Math.random()*6.283,dx:0,dy:0});}})();
  const MAG=[208,74,255], CYAN=[95,208,230];      // two-tone gradient like the reference orb
  const VOICE_RGB=hex2rgb(VOICE_COLOR), ACCENT_RGB=hex2rgb(TIER_COLORS.fast);
  let energy=0.14, rot=0, rotX=-0.32, shock=0, mx=-999, my=-999, hover=false, expand=0;
  function pulseCore(){shock=Math.min(1.6,shock+1);}   // hoisted; called by setCore + send
  function targetEnergy(){
    if(busy||coreState==='thinking')return 1.0;
    if(coreState==='speaking')return 0.74;
    if(coreState==='listening')return 0.6;
    if(voiceActive)return 0.42;
    return hover?0.36:0.15;
  }
  function hex2rgb(h){h=h.replace('#','');return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)];}
  function fitCanvas(){const r=coreCanvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);coreCanvas.width=Math.max(1,r.width*dpr);coreCanvas.height=Math.max(1,r.height*dpr);cctx.setTransform(dpr,0,0,dpr,0,0);if(matchMedia('(prefers-reduced-motion: reduce)').matches)drawSphere(0);}
  window.addEventListener('resize',fitCanvas);
  coreCanvas.addEventListener('mousemove',e=>{const r=coreCanvas.getBoundingClientRect();mx=e.clientX-r.left;my=e.clientY-r.top;hover=true;});
  coreCanvas.addEventListener('mouseleave',()=>{hover=false;mx=my=-999;});
  function drawSphere(t){
    const r=coreCanvas.getBoundingClientRect(),w=r.width,h=r.height; if(!w)return;
    cctx.clearRect(0,0,w,h);
    const cx=w/2,cy=h/2,R=Math.min(w,h)*0.34;
    energy+=(targetEnergy()-energy)*0.06; shock*=0.92;
    const e=Math.min(1.8,energy+shock*0.7);
    // "searching the internet": while the agent works, the globe expands, spins up,
    // and a bright scan band sweeps across it. expand eases in/out so it's smooth.
    const searching = busy || coreState==='thinking' || coreState==='listening';
    expand += ((searching?1:0)-expand)*0.05;
    rot+=0.0015+e*0.011+expand*0.013;
    const scanLat = searching ? Math.sin(t*1.6) : 99;
    // voice mode shifts the whole core to violet so the theme reads as "listening"
    const VRGB=VOICE_RGB, acc=voiceActive?VRGB:ACCENT_RGB;
    const tint=voiceActive?VRGB:((coreState==='thinking'||coreState==='speaking')?hex2rgb(coreColor()):null);
    const cosY=Math.cos(rot),sinY=Math.sin(rot),cosX=Math.cos(rotX),sinX=Math.sin(rotX);
    const breathe=R*(1+Math.sin(t*1.5)*0.018*(1+e))*(1+0.22*expand);   // puff outward while searching
    // soft ambient bloom
    const amb=cctx.createRadialGradient(cx,cy,0,cx,cy,R*1.8);
    amb.addColorStop(0,'rgba('+acc[0]+','+acc[1]+','+acc[2]+','+(0.05+e*0.09).toFixed(3)+')');amb.addColorStop(.55,'rgba(120,80,220,0.025)');amb.addColorStop(1,'transparent');
    cctx.fillStyle=amb;cctx.fillRect(0,0,w,h);
    cctx.globalCompositeOperation='lighter';
    for(let i=0;i<NP;i++){const p=pts[i];
      const jit=e*0.05*Math.sin(t*3+p.ph), s=1+jit;
      const bx=p.x*s,by=p.y*s,bz=p.z*s;
      const x1=bx*cosY+bz*sinY, z1=-bx*sinY+bz*cosY;
      const y2=by*cosX-z1*sinX, z2=by*sinX+z1*cosX;
      const persp=1/(1.85-z2*0.6);
      let sx=cx+x1*breathe*persp+p.dx, sy=cy+y2*breathe*persp+p.dy;
      if(hover){const ax=sx-mx,ay=sy-my,d2=ax*ax+ay*ay; if(d2<10000){const d=Math.sqrt(d2)||1,f=(1-d/100)*2.6;p.dx+=ax/d*f;p.dy+=ay/d*f;}}
      p.dx*=0.85;p.dy*=0.85;
      sx=cx+x1*breathe*persp+p.dx; sy=cy+y2*breathe*persp+p.dy;
      const mix=(x1+1)/2; let cr=MAG[0]+(CYAN[0]-MAG[0])*mix,cg=MAG[1]+(CYAN[1]-MAG[1])*mix,cb=MAG[2]+(CYAN[2]-MAG[2])*mix;
      if(tint){cr=(cr+tint[0])/2;cg=(cg+tint[1])/2;cb=(cb+tint[2])/2;}
      const depth=(z2+1)/2, tw=0.62+0.38*Math.sin(t*2.2+p.ph);
      let a=Math.min(1,(0.1+depth*0.8)*(0.55+e*0.55)*tw), sz=(0.5+depth*1.8)*persp*(1+e*0.35);
      // scan band: particles the sweep crosses flare brighter and whiter
      const near=expand*Math.max(0,1-Math.abs(by-scanLat)/0.16);
      if(near>0){a=Math.min(1,a+near*0.55);sz*=1+near*1.3;cr=cr+(235-cr)*near;cg=cg+(248-cg)*near;cb=cb+(255-cb)*near;}
      cctx.fillStyle='rgba('+(cr|0)+','+(cg|0)+','+(cb|0)+','+a.toFixed(3)+')';
      cctx.beginPath();cctx.arc(sx,sy,sz,0,6.283);cctx.fill();
    }
    const ccr=R*0.12*(0.8+e*0.45+Math.sin(t*3)*0.06);
    const cg2=cctx.createRadialGradient(cx,cy,0,cx,cy,ccr*3.2);
    cg2.addColorStop(0,'rgba(255,255,255,'+(0.45+e*0.4).toFixed(3)+')');cg2.addColorStop(.4,'rgba('+acc[0]+','+acc[1]+','+acc[2]+',0.45)');cg2.addColorStop(1,'transparent');
    cctx.fillStyle=cg2;cctx.beginPath();cctx.arc(cx,cy,ccr*3.2,0,6.283);cctx.fill();
    cctx.globalCompositeOperation='source-over';
  }
  const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
  function coreLoop(){if(coreCanvas.offsetParent!==null&&!document.hidden)drawSphere(performance.now()/1000);if(!reducedMotion.matches)requestAnimationFrame(coreLoop);}
  reducedMotion.addEventListener('change',()=>{if(reducedMotion.matches)fitCanvas();else coreLoop();});
  fitCanvas();setTimeout(fitCanvas,60);coreLoop();

  /* markdown */
  function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
  /* ---- Maths ----------------------------------------------------------------------
     Models write arithmetic in LaTeX, and the renderer showed it raw: a division came out
     as "\[ \frac{754}{86982} \approx 0.008668 \]". No KaTeX or MathJax — the CSP is
     script-src 'nonce-...' with no 'self', so nothing extra can load, the same reason the
     diagrams are drawn by hand. This covers what chat arithmetic actually uses and leaves
     anything else as plain text rather than backslashes. */
  const MATH_SYMBOLS = {
    approx:'≈', times:'×', div:'÷', cdot:'·', pm:'±', mp:'∓',
    le:'≤', leq:'≤', ge:'≥', geq:'≥', ne:'≠', neq:'≠',
    equiv:'≡', sim:'∼', propto:'∝', infty:'∞', sum:'∑',
    prod:'∏', int:'∫', partial:'∂', nabla:'∇', deg:'°',
    alpha:'α', beta:'β', gamma:'γ', delta:'δ', epsilon:'ε',
    theta:'θ', lambda:'λ', mu:'μ', pi:'π', rho:'ρ', sigma:'σ',
    tau:'τ', phi:'φ', omega:'ω', Delta:'Δ', Sigma:'Σ',
    Omega:'Ω', rightarrow:'→', leftarrow:'←', Rightarrow:'⇒',
    leftrightarrow:'↔', in:'∈', notin:'∉', subset:'⊂', cup:'∪',
    cap:'∩', forall:'∀', exists:'∃', ldots:'…', cdots:'⋯',
  };
  const SUPERS = {'0':'⁰','1':'¹','2':'²','3':'³','4':'⁴','5':'⁵',
    '6':'⁶','7':'⁷','8':'⁸','9':'⁹','+':'⁺','-':'⁻','n':'ⁿ'};
  const SUBS = {'0':'₀','1':'₁','2':'₂','3':'₃','4':'₄','5':'₅',
    '6':'₆','7':'₇','8':'₈','9':'₉','+':'₊','-':'₋'};

  // Take {...} after an index, honouring nesting, so \frac{a{b}}{c} does not split early.
  function mathGroup(src, at){
    if (src[at] !== '{') return null;
    let depth = 0;
    for (let i = at; i < src.length; i++){
      if (src[i] === '{') depth++;
      else if (src[i] === '}' && --depth === 0) return {body: src.slice(at + 1, i), end: i + 1};
    }
    return null;
  }

  function mathScript(body, table){
    const mapped = [...body].map(ch => table[ch]);
    return mapped.every(Boolean) ? mapped.join('') : null;   // fall back to a real tag
  }

  // `tex` arrives already HTML-escaped, and every tag below is one we add ourselves.
  function mathToHtml(tex){
    let out = '', i = 0;
    const src = String(tex || '');
    while (i < src.length){
      const ch = src[i];
      if (ch === '\\'){
        const name = /^[a-zA-Z]+/.exec(src.slice(i + 1));
        if (name){
          const word = name[0];
          if (word === 'frac' || word === 'dfrac' || word === 'tfrac'){
            const a = mathGroup(src, i + 1 + word.length);
            const b = a && mathGroup(src, a.end);
            if (b){
              out += '<span class="frac"><span class="num">' + mathToHtml(a.body)
                   + '</span><span class="den">' + mathToHtml(b.body) + '</span></span>';
              i = b.end;
              continue;
            }
          }
          if (word === 'sqrt'){
            const a = mathGroup(src, i + 1 + word.length);
            if (a){ out += '√(' + mathToHtml(a.body) + ')'; i = a.end; continue; }
          }
          if (word === 'text' || word === 'mathrm' || word === 'operatorname'){
            const a = mathGroup(src, i + 1 + word.length);
            if (a){ out += mathToHtml(a.body); i = a.end; continue; }
          }
          if (MATH_SYMBOLS[word]){ out += MATH_SYMBOLS[word]; i += 1 + word.length; continue; }
          if (word === 'left' || word === 'right' || word === 'displaystyle'){ i += 1 + word.length; continue; }
          out += word; i += 1 + word.length; continue;          // unknown macro: its name is closer than a backslash
        }
        if (src[i + 1] === '\\'){ out += '<br>'; i += 2; continue; }
        i += 1; continue;                                        // a lone escape adds nothing
      }
      if (ch === '^' || ch === '_'){
        const table = ch === '^' ? SUPERS : SUBS;
        const tag = ch === '^' ? 'sup' : 'sub';
        const group = mathGroup(src, i + 1);
        const body = group ? group.body : (src[i + 1] || '');
        const next = group ? group.end : i + 2;
        const unicode = mathScript(body, table);
        out += unicode !== null ? unicode : ('<' + tag + '>' + mathToHtml(body) + '</' + tag + '>');
        i = next;
        continue;
      }
      if (ch === '{' || ch === '}' || ch === '$'){ i += 1; continue; }
      if (ch === '&'){                                           // keep &amp; and friends whole
        const entity = /^&[a-z]+;|^&#\d+;/i.exec(src.slice(i));
        if (entity){ out += entity[0]; i += entity[0].length; continue; }
      }
      out += ch; i += 1;
    }
    return out;
  }

  function inline(s){s=esc(s);
    // before the emphasis rules, whose braces and underscores would chew up TeX
    s=s.replace(/\\\(([\s\S]*?)\\\)/g,(m,tex)=>'<span class="math">'+mathToHtml(tex)+'</span>');
    s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
    s=s.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
    s=s.replace(/(^|[^\w])\*([^*]+)\*/g,'$1<em>$2</em>');
    // Same-origin paths only. A model that invents a data:image/...;base64 blob is
    // fabricating a picture, and one arrived as a 40KB blob of noise.
    s=s.replace(/!\[([^\]]*)\]\((\/[^)\s"]+)\)/g,'<img src="$2" alt="$1" loading="lazy" />');
    s=s.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    // Our own routes are links too — a generated document is handed back as one, and
    // without this it rendered as literal "[Title](/api/document?name=…)" text.
    s=s.replace(/\[([^\]]+)\]\((\/[^)\s"]*)\)/g,'<a href="$2">$1</a>');
    return s;}
  /* Actions on rendered output: save a generated picture, copy or export a table.
     Added as DOM nodes rather than markup so nothing user- or model-authored is ever
     interpolated into HTML. Idempotent — a re-render decorates only what is new. */
  function download(name,type,body){
    const url=URL.createObjectURL(new Blob([body],{type}));
    const a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();
    a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function tableToRows(table){
    return [...table.rows].map(r=>[...r.cells].map(c=>c.innerText.trim()));
  }
  function toCSV(rows){
    // Excel opens this directly; a field is quoted when it holds a comma, quote or newline.
    return rows.map(r=>r.map(v=>/[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v).join(',')).join('\r\n');
  }
  function actionBtn(label,title,fn){
    const b=document.createElement('button');b.type='button';b.className='oact';
    b.textContent=label;b.title=title;b.setAttribute('aria-label',title);
    b.onclick=ev=>{ev.preventDefault();fn(b);};return b;
  }
  function flash(btn,text){const was=btn.textContent;btn.textContent=text;setTimeout(()=>{btn.textContent=was;},1200);}
  function decorate(root){
    root.querySelectorAll('img:not([data-dec])').forEach(img=>{
      img.dataset.dec='1';
      // A model sometimes writes its own ![...](/api/image?name=...) pointing at a file that
      // was never generated. That rendered as a zero-height image with a Save control and
      // nothing to save, which reads as a broken feature — drop the whole figure instead.
      img.addEventListener('error',()=>{const fig=img.closest('.figure');(fig||img).remove();});
      const src=img.getAttribute('src')||'';
      const name=(/[?&]name=([^&]+)/.exec(src)||[])[1]||'image.png';
      const wrap=document.createElement('div');wrap.className='figure';
      img.parentNode.insertBefore(wrap,img);wrap.appendChild(img);
      const bar=document.createElement('div');bar.className='oactions';
      bar.appendChild(actionBtn('Save','Download this picture',async b=>{
        try{const r=await fetch(src);download(decodeURIComponent(name),r.headers.get('Content-Type')||'image/png',await r.blob());flash(b,'Saved');}
        catch(e){flash(b,'Failed');}
      }));
      wrap.appendChild(bar);
    });
    root.querySelectorAll('a[href^="/api/document"]:not([data-dec])').forEach(a=>{
      a.dataset.dec='1';
      // Name the saved file after the document, not "document", and mark it as a download.
      const name=(/[?&]name=([^&]+)/.exec(a.getAttribute('href')||'')||[])[1];
      if(name)a.setAttribute('download',decodeURIComponent(name));
      a.classList.add('doclink');
      const kind=(name||'').split('.').pop().toUpperCase();
      if(kind&&!a.querySelector('.dockind')){
        const tag=document.createElement('span');tag.className='dockind';tag.textContent=kind;
        a.appendChild(tag);
      }
    });
    root.querySelectorAll('table:not([data-dec])').forEach(table=>{
      table.dataset.dec='1';
      const bar=document.createElement('div');bar.className='oactions tableacts';
      const stamp=()=>new Date().toISOString().slice(0,10);
      bar.appendChild(actionBtn('Copy','Copy the table as tab-separated text',async b=>{
        const text=tableToRows(table).map(r=>r.join('\t')).join('\n');
        try{await navigator.clipboard.writeText(text);flash(b,'Copied');}
        catch(e){flash(b,'Blocked');}
      }));
      bar.appendChild(actionBtn('CSV','Download the table as a CSV file, ready for Excel',b=>{
        download('table-'+stamp()+'.csv','text/csv;charset=utf-8',toCSV(tableToRows(table)));flash(b,'Saved');
      }));
      const holder=table.closest('.tw')||table;
      holder.parentNode.insertBefore(bar,holder.nextSibling);
    });
  }
  function setMd(el,text){el.innerHTML=mdToHtml(text);decorate(el);return el;}
  /* ---- Diagrams -------------------------------------------------------------------
     A ```mermaid block is drawn as real inline SVG. Not the Mermaid library: the CSP is
     script-src 'nonce-...' with no 'self', so no extra script can load, and vendoring 3MB
     for this would fight the "no chart CDN, offline-friendly" rule the charts already
     follow. This covers the two shapes that actually come up in conversation — entity
     relationships and node/edge flows — and falls back to the code block for anything else,
     so an unsupported diagram is still readable rather than broken. */
  const MM_FONT = 12, MM_PAD = 10, MM_GAPX = 54, MM_GAPY = 34;

  function mmText(s){return String(s||'').replace(/^["'`]|["'`]$/g,'').trim();}
  function mmWidth(s,size){return Math.ceil(String(s).length*size*0.62);}

  // Split "A --|label|--> B" style edges without a real parser: good enough for the
  // flowchart/state syntax models actually emit.
  const MM_EDGE = /^\s*([A-Za-z0-9_.-]+)\s*(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})?\s*(-{1,3}>|-{2,3}|==>|\.\.>|-->)\s*(?:\|([^|]*)\|)?\s*([A-Za-z0-9_.-]+)\s*(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})?\s*$/;
  const MM_NODE = /^\s*([A-Za-z0-9_.-]+)\s*(?:\[([^\]]*)\]|\(([^)]*)\)|\{([^}]*)\})\s*$/;

  function mmParseFlow(lines){
    const nodes = new Map(), edges = [];
    const label = (id, ...alts) => {
      const text = alts.find(a => a != null);
      if (text != null && text !== '') nodes.set(id, mmText(text));
      else if (!nodes.has(id)) nodes.set(id, id);
    };
    for (const raw of lines){
      const line = raw.trim();
      if (!line || /^(flowchart|graph|stateDiagram(-v2)?|direction|classDef|class |click |%%)/i.test(line)) continue;
      let m = MM_EDGE.exec(line);
      if (m){
        const [, a, a1, a2, a3, , elabel, b, b1, b2, b3] = m;
        label(a, a1, a2, a3); label(b, b1, b2, b3);
        edges.push({from: a, to: b, label: mmText(elabel || '')});
        continue;
      }
      m = MM_NODE.exec(line);
      if (m) label(m[1], m[2], m[3], m[4]);
    }
    return {nodes, edges};
  }

  // Breadth-first from the entry point, ignoring edges that lead back to a node already
  // placed. Longest-path layering looks right until the flow has a cycle: TCP's timeout
  // edge back to Slow Start pushed the entry state to the bottom of the picture.
  function mmLayers(nodes, edges){
    const order = [...nodes.keys()];
    const indegree = new Map(order.map(id => [id, 0]));
    edges.forEach(e => { if (indegree.has(e.to)) indegree.set(e.to, indegree.get(e.to) + 1); });
    const depth = new Map(), queue = [];
    order.forEach(id => { if (indegree.get(id) === 0){ depth.set(id, 0); queue.push(id); } });
    // Every node in a cycle has an incoming edge, so start from the one declared first —
    // which is how whoever wrote the diagram reads it.
    if (!queue.length){ depth.set(order[0], 0); queue.push(order[0]); }
    while (queue.length){
      const id = queue.shift();
      edges.forEach(e => {
        if (e.from === id && !depth.has(e.to)){ depth.set(e.to, depth.get(id) + 1); queue.push(e.to); }
      });
    }
    order.forEach(id => { if (!depth.has(id)) depth.set(id, 0); });   // unreachable: top row
    const rows = [];
    order.forEach(id => { const d = depth.get(id); (rows[d] = rows[d] || []).push(id); });
    return rows.filter(Boolean);
  }

  function mmFlowSvg(code, horizontal){
    const {nodes, edges} = mmParseFlow(code.split('\n'));
    if (!nodes.size) return null;
    const rows = mmLayers(nodes, edges);
    const box = new Map();
    let y = MM_PAD, maxX = 0;
    rows.forEach(row => {
      const h = 30;
      let x = MM_PAD;
      row.forEach(id => {
        const w = Math.max(64, mmWidth(nodes.get(id), MM_FONT) + 26);
        box.set(id, {x, y, w, h, label: nodes.get(id)});
        x += w + MM_GAPX;
      });
      maxX = Math.max(maxX, x - MM_GAPX);
      y += h + MM_GAPY;
    });
    // centre each row so the drawing reads as a column rather than a left-aligned ragged list
    rows.forEach(row => {
      const width = row.reduce((t, id) => t + box.get(id).w, 0) + MM_GAPX * (row.length - 1);
      const shift = (maxX - MM_PAD - width) / 2;
      row.forEach(id => { box.get(id).x += shift; });
    });
    const W = maxX + MM_PAD, H = y - MM_GAPY + MM_PAD;
    let out = '';
    edges.forEach(e => {
      const a = box.get(e.from), b = box.get(e.to);
      if (!a || !b) return;
      const x1 = a.x + a.w / 2, y1 = a.y + a.h, x2 = b.x + b.w / 2, y2 = b.y;
      const mid = (y1 + y2) / 2;
      const path = (y2 > y1)
        ? 'M' + x1 + ' ' + y1 + ' C' + x1 + ' ' + mid + ' ' + x2 + ' ' + mid + ' ' + x2 + ' ' + y2
        : 'M' + (a.x + a.w) + ' ' + (a.y + a.h / 2) + ' C' + (a.x + a.w + 30) + ' ' + (a.y + a.h / 2)
          + ' ' + (b.x + b.w + 30) + ' ' + (b.y + b.h / 2) + ' ' + (b.x + b.w) + ' ' + (b.y + b.h / 2);
      out += '<path d="' + path + '" fill="none" stroke="var(--hair-3)" stroke-width="1.2" marker-end="url(#mmArrow)"/>';
      if (e.label){
        const lx = (x1 + x2) / 2, ly = (y2 > y1) ? mid : a.y - 6;
        out += '<text x="' + lx + '" y="' + ly + '" text-anchor="middle" font-size="10.5" fill="var(--muted)">' + esc(e.label) + '</text>';
      }
    });
    box.forEach(b => {
      out += '<rect x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="8" '
           + 'fill="var(--surface-2)" stroke="var(--hair-2)"/>'
           + '<text x="' + (b.x + b.w / 2) + '" y="' + (b.y + b.h / 2 + 4) + '" text-anchor="middle" '
           + 'font-size="' + MM_FONT + '" fill="var(--text)">' + esc(b.label) + '</text>';
    });
    return mmWrap(out, W, H);
  }

  // erDiagram: USERS { int id PK } and USERS ||--o{ ORDERS : places
  function mmErSvg(code){
    const lines = code.split('\n');
    const entities = new Map(), rels = [];
    let current = null;
    for (const raw of lines){
      const line = raw.trim();
      if (!line || /^erDiagram/i.test(line) || line.startsWith('%%')) continue;
      if (current){
        if (line === '}'){ current = null; continue; }
        const parts = line.replace(/"[^"]*"/g, '').split(/\s+/).filter(Boolean);
        if (parts.length >= 2) entities.get(current).push(parts[1] + ' : ' + parts[0] + (parts[2] ? ' ' + parts[2] : ''));
        else if (parts.length === 1) entities.get(current).push(parts[0]);
        continue;
      }
      const open = /^([A-Za-z0-9_.-]+)\s*\{$/.exec(line);
      if (open){ current = open[1]; if (!entities.has(current)) entities.set(current, []); continue; }
      const rel = /^([A-Za-z0-9_.-]+)\s*([|{}o<>-]{2,})[-.]*([|{}o<>-]{2,})?\s*([A-Za-z0-9_.-]+)\s*:\s*(.*)$/.exec(line);
      if (rel){
        if (!entities.has(rel[1])) entities.set(rel[1], []);
        if (!entities.has(rel[4])) entities.set(rel[4], []);
        rels.push({from: rel[1], to: rel[4], label: mmText(rel[5])});
      }
    }
    if (!entities.size) return null;
    const ids = [...entities.keys()];
    const perRow = Math.min(3, Math.max(1, Math.ceil(Math.sqrt(ids.length))));
    const box = new Map();
    let x = MM_PAD, y = MM_PAD, rowH = 0, maxX = 0, col = 0;
    ids.forEach(id => {
      const rowsText = entities.get(id);
      const w = Math.max(140, mmWidth(id, MM_FONT + 1) + 30,
                         ...rowsText.map(r => mmWidth(r, MM_FONT - 1) + 24));
      const h = 26 + rowsText.length * 17 + 8;
      box.set(id, {x, y, w, h, rows: rowsText});
      x += w + MM_GAPX; rowH = Math.max(rowH, h); maxX = Math.max(maxX, x - MM_GAPX);
      if (++col % perRow === 0){ x = MM_PAD; y += rowH + MM_GAPY; rowH = 0; }
    });
    const H = (rowH ? y + rowH : y - MM_GAPY) + MM_PAD, W = maxX + MM_PAD;
    let out = '';
    rels.forEach(r => {
      const a = box.get(r.from), b = box.get(r.to);
      if (!a || !b) return;
      const x1 = a.x + a.w / 2, y1 = a.y + a.h / 2, x2 = b.x + b.w / 2, y2 = b.y + b.h / 2;
      out += '<path d="M' + x1 + ' ' + y1 + ' L' + x2 + ' ' + y2 + '" stroke="var(--accent-line)" '
           + 'stroke-width="1.2" fill="none" marker-end="url(#mmArrow)"/>';
      if (r.label){
        out += '<text x="' + ((x1 + x2) / 2) + '" y="' + ((y1 + y2) / 2 - 4) + '" text-anchor="middle" '
             + 'font-size="10" fill="var(--muted)">' + esc(r.label) + '</text>';
      }
    });
    box.forEach((b, id) => {
      out += '<rect x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="' + b.h + '" rx="9" '
           + 'fill="var(--surface-2)" stroke="var(--hair-2)"/>'
           + '<rect x="' + b.x + '" y="' + b.y + '" width="' + b.w + '" height="26" rx="9" fill="var(--surface-3)"/>'
           + '<text x="' + (b.x + 11) + '" y="' + (b.y + 17) + '" font-size="' + (MM_FONT + 1) + '" '
           + 'font-weight="600" fill="var(--accent-2)">' + esc(id) + '</text>';
      b.rows.forEach((r, i) => {
        out += '<text x="' + (b.x + 11) + '" y="' + (b.y + 26 + 16 + i * 17) + '" font-size="'
             + (MM_FONT - 1) + '" fill="var(--text-2)" font-family="var(--mono)">' + esc(r) + '</text>';
      });
    });
    return mmWrap(out, W, H);
  }

  function mmWrap(body, w, h){
    return '<div class="dgm"><svg viewBox="0 0 ' + Math.ceil(w) + ' ' + Math.ceil(h) + '" '
         + 'width="100%" preserveAspectRatio="xMidYMid meet" role="img" '
         + 'style="font-family:var(--sans);max-height:' + Math.ceil(h) + 'px">'
         + '<defs><marker id="mmArrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" '
         + 'markerHeight="7" orient="auto-start-reverse">'
         + '<path d="M0 0 L8 4 L0 8 z" fill="var(--hair-3)"/></marker></defs>'
         + body + '</svg></div>';
  }

  function mermaidSvg(code){
    try{
      const head = (code || '').trim().split('\n')[0] || '';
      if (/^erDiagram/i.test(head)) return mmErSvg(code);
      if (/^(flowchart|graph|stateDiagram)/i.test(head)) return mmFlowSvg(code, /\bLR\b|\bRL\b/i.test(head));
      return null;
    }catch(e){ return null; }      // a diagram we cannot draw must not break the message
  }

  function mdToHtml(src){
    // Display maths is lifted out first for the same reason as a fence: it spans lines.
    const maths=[]; src=String(src)
      .replace(/\\\[([\s\S]*?)\\\]/g,(m,x)=>{maths.push(x);return '@@M'+(maths.length-1)+'@@';})
      .replace(/\$\$([\s\S]*?)\$\$/g,(m,x)=>{maths.push(x);return '@@M'+(maths.length-1)+'@@';});
    const fences=[]; src=String(src).replace(/```(\w*)\n?([\s\S]*?)```/g,(m,l,c)=>{fences.push({lang:(l||'').toLowerCase(),code:c});return '@@F'+(fences.length-1)+'@@';});
    let html='',list=null; const close=()=>{if(list){html+='</'+list+'>';list=null;}};
    const rows=src.split('\n');
    const cells=r=>r.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
    for(let i=0;i<rows.length;i++){
      const raw=rows[i], t=raw.trim();
      let m;
      // | a | b | over |---|---| becomes a real table, so data stops arriving as prose
      if(t.startsWith('|')&&/^\|[\s:|-]+\|?$/.test((rows[i+1]||'').trim())){
        close();
        const head=cells(t); i++;
        let body='';
        while(i+1<rows.length&&rows[i+1].trim().startsWith('|')){
          body+='<tr>'+cells(rows[++i]).map(c=>'<td>'+inline(c)+'</td>').join('')+'</tr>';
        }
        html+='<div class="tw"><table><thead><tr>'+head.map(c=>'<th>'+inline(c)+'</th>').join('')+'</tr></thead><tbody>'+body+'</tbody></table></div>';
        continue;
      }
      if(/^@@M\d+@@$/.test(t)){close();html+='<div class="mathblock">'+mathToHtml(esc(maths[+t.slice(3,-2)]))+'</div>';continue;}
      if(/^@@F\d+@@$/.test(t)){close();const f=fences[+t.slice(3,-2)];
        const drawn=f.lang==='mermaid'?mermaidSvg(f.code):null;
        // An unsupported diagram stays a readable code block rather than vanishing.
        html+=drawn||('<pre><code>'+esc(f.code)+'</code></pre>');continue;}
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
  SUG.forEach(([t,q])=>{const c=document.createElement('button');c.type='button';c.className='scard';c.title=t;c.textContent=q;c.onclick=()=>send(q);suggest.appendChild(c);});

  /* sessions (localStorage) */
  let sessions=[], current=null;
  try{const saved=JSON.parse(localStorage.getItem('jarvis_sessions')||'[]');
    if(Array.isArray(saved))sessions=saved.filter(s=>s&&typeof s.id==='string'&&Array.isArray(s.msgs)).slice(0,40);
  }catch(e){hint.textContent='Saved chat history could not be read. You can still start a new chat.';}
  function saveSessions(){sessions=sessions.slice(0,40);
    // Incognito sessions stay in memory: they are filtered out of everything written to disk.
    const keep=sessions.filter(s=>!s.ghost);
    try{localStorage.setItem('jarvis_sessions',JSON.stringify(keep));}
    catch(e){
      // Over quota: the tool-data digests are the expendable part — drop them and retry once.
      sessions.forEach(s=>s.msgs.forEach(m=>{delete m.extra;}));
      try{localStorage.setItem('jarvis_sessions',JSON.stringify(keep));}
      catch(e2){hint.textContent='Chat could not be saved: browser storage is full or unavailable.';}
    }}
  function renderSessions(){
    sessionsEl.innerHTML='';
    sessions.forEach(s=>{
      const row=document.createElement('div');row.className='sessrow'+(s.id===current?' active':'');
      const b=document.createElement('button');b.className='sess';
      b.innerHTML=(s.ghost?'<span class="ghosttag">incognito</span>':'')+esc(s.title||'New chat');
      b.title=s.title||'New chat';
      b.onclick=()=>{loadSession(s.id);closeChats();};
      const del=document.createElement('button');del.className='sessdel';del.type='button';
      del.setAttribute('aria-label','Delete this chat');del.title='Delete this chat';
      del.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>';
      del.onclick=ev=>{ev.stopPropagation();deleteSession(s.id);};
      row.appendChild(b);row.appendChild(del);sessionsEl.appendChild(row);
    });
  }
  function deleteSession(id){
    const i=sessions.findIndex(s=>s.id===id); if(i<0)return;
    const wasCurrent=sessions[i].id===current;
    sessions.splice(i,1); saveSessions();
    // Deleting the open chat leaves nothing on screen, so open the next one or start fresh.
    if(wasCurrent){ if(sessions.length){loadSession(sessions[0].id);} else {newSession();} }
    else renderSessions();
  }
  // Incognito: the session lives in memory only. saveSessions() never writes it, so it is
  // gone on reload and never reaches localStorage.
  function newSession(ghost){
    const s={id:crypto.randomUUID(),title:'',msgs:[]};
    if(ghost)s.ghost=true;
    sessions.unshift(s);current=s.id;
    document.body.classList.toggle('ghosting',!!ghost);
    saveSessions();renderSessions();chat.innerHTML='';chat.appendChild(emptyEl());
  }
  function curSession(){return sessions.find(s=>s.id===current);}
  // The whole session goes to the server (it chunks and budgets the context), capped at
  // the API's 100-turn limit and a sane per-message size so a pasted document can't
  // balloon the request.
  // A reply's `extra` is a bounded digest of the tool data behind it (the "details" pane),
  // so "summarize this" after `read file …` has the file text, not just the status line.
  function sessionHistory(s){return s?s.msgs.slice(-80).map(m=>({role:m.role==='bot'?'assistant':'user',text:(String(m.text||'')+(m.extra?'\n'+m.extra:'')).slice(0,40000)})):[];}
  function dataDigest(data){try{const d=Object.assign({},data||{});['planner','messages','sources','fields','fill_preview','field_mappings','results'].forEach(k=>delete d[k]);return Object.keys(d).length?('[tool result data, context only - not a format to imitate] '+JSON.stringify(d).slice(0,2000)):'';}catch(e){return '';}}
  function loadSession(id){current=id;const s=curSession();document.body.classList.toggle('ghosting',!!(s&&s.ghost));chat.innerHTML='';if(!s||!s.msgs.length){chat.appendChild(emptyEl());}else{s.msgs.forEach(m=>renderMsg(m.role,m.text,m.atts));}renderSessions();}
  let emptyNode=document.getElementById('empty');
  function emptyEl(){const el=emptyNode.cloneNode(true);el.querySelectorAll('.scard').forEach((b,i)=>b.onclick=()=>send(SUG[i][1]));return el;}
  function closeChats(){document.body.classList.remove('showChats');document.getElementById('mobileChats').setAttribute('aria-expanded','false');}
  document.getElementById('mobileChats').onclick=()=>{const open=document.body.classList.toggle('showChats');document.getElementById('mobileChats').setAttribute('aria-expanded',String(open));document.querySelector('.left').style.top=document.querySelector('header').getBoundingClientRect().bottom+'px';if(open)document.getElementById('newChat').focus();};
  document.getElementById('newChat').onclick=()=>{newSession();closeChats();};
  document.getElementById('newGhost').onclick=()=>{newSession(true);closeChats();hint.textContent='Incognito chat — this conversation is not saved in this browser.';};

  /* messages */
  function clearEmpty(){const e=chat.querySelector('.empty');if(e)e.remove();}
  function copyOut(text,btn){
    const done=ok=>{if(btn){btn.textContent=ok?'✓ Copied':'Copy failed';setTimeout(()=>btn.textContent='⧉ Copy',1200);}};
    if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(()=>done(true),()=>done(false));}
    else{try{const t=document.createElement('textarea');t.value=text;t.style.cssText='position:fixed;opacity:0';document.body.appendChild(t);t.select();document.execCommand('copy');t.remove();done(true);}catch(e){done(false);}}
  }
  function renderMsg(role,text,atts){
    clearEmpty();
    const m=document.createElement('div');m.className='msg '+role;
    m.innerHTML=(role==='user'?'':'<div class="av">J</div>')+'<div class="content"><div class="who">'+(role==='user'?'You':'J.A.R.V.I.S')+'</div><div class="md"></div></div>';
    if(role==='user')m.querySelector('.md').innerHTML=esc(text).replace(/\n/g,'<br>');else setMd(m.querySelector('.md'),text);
    if(atts&&atts.length){const box=document.createElement('div');atts.forEach(a=>{const s=document.createElement('span');s.className='att';s.innerHTML='<span class="ic">&#128196;</span>'+esc(a);box.appendChild(s);});m.querySelector('.content').appendChild(box);}
    if(role!=='user'){const cp=document.createElement('button');cp.type='button';cp.className='copybtn';cp.textContent='⧉ Copy';cp.setAttribute('aria-label','Copy this reply');cp.onclick=()=>copyOut(m.querySelector('.md').innerText,cp);m.querySelector('.content').appendChild(cp);}
    chat.appendChild(m);chat.scrollTop=chat.scrollHeight;return m;
  }
  function thinking(tier){clearEmpty();const m=document.createElement('div');m.className='msg bot';const note=tier==='ultra'?'<span class="tiernote">reasoning model — this can take a moment</span>':tier==='smart'?'<span class="tiernote">smart model</span>':'';m.innerHTML='<div class="av">J</div><div class="content"><div class="who">J.A.R.V.I.S</div><div class="md"><span class="think" role="status" aria-label="Working"><i></i></span>'+note+'</div></div>';chat.appendChild(m);chat.scrollTop=chat.scrollHeight;return m;}
  function setBusy(b,tier){busy=b;reactor.classList.toggle('busy',b);setCore(b?'thinking':(voiceActive?'listening':'idle'),tier);
    sendBtn.innerHTML=b?STOP_ICON:SEND_ICON; sendBtn.title=b?'Stop':'Send';sendBtn.setAttribute('aria-label',sendBtn.title); sendBtn.classList.toggle('stop',b);}
  let currentRequest=null;
  function stopGen(){twCancel=true;if(currentRequest){fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:currentRequest})}).catch(()=>{hint.textContent='Could not reach the server to confirm stopping.';});}if(currentAbort){currentAbort.abort();}}
  // Typewriter reveal for instant (non-streamed) results — local command output
  // arrives as one block, so animate it like a streamed reply for a consistent feel.
  let twCancel=false;
  function typewriter(el,text){
    twCancel=false;
    const total=text.length;
    if(total>4000||reducedMotion.matches||!typeAnim){setMd(el,text);return;}   // long output, reduced motion, or turned off
    // 160 ticks at 16ms was ~2.5s of deliberate delay on every local result, which is
    // most of what a fast command costs. ~28 ticks reads as alive without being a wait.
    const step=Math.max(2,Math.ceil(total/28));
    let i=0;
    (function tick(){
      if(twCancel){setMd(el,text);return;}
      i=Math.min(total,i+step);
      el.innerHTML=mdToHtml(text.slice(0,i));
      chat.scrollTop=chat.scrollHeight;
      if(i<total)setTimeout(tick,16); else setMd(el,text);
    })();
  }

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
    if(e.key==='Escape'){closeChats();
      const nv=document.getElementById('noteViewer'); if(nv.classList.contains('open')){nv.classList.remove('open');return;}
      if(sysDrawer.classList.contains('open')){setDrawer(false);return;}
      if(busy)stopGen(); else if(voiceActive)endVoice(); }
    if(e.key===' '&&voiceActive&&document.activeElement!==ta&&document.activeElement.tagName!=='INPUT'){e.preventDefault();interruptNow();}  // Space: stop speaking, listen
    if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){ e.preventDefault(); newSession(); ta.focus(); }
  });

  async function uploadFile(file){
    const data=await new Promise(r=>{const fr=new FileReader();fr.onload=()=>r(fr.result);fr.onerror=()=>r('');fr.readAsDataURL(file);});
    const res=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,data})});
    const d=await res.json(); if(d.ok){attachments.push(d);renderChips();}else{hint.textContent=d.message||'Upload failed';}
  }
  function renderChips(){chips.innerHTML='';attachments.forEach((a,i)=>{const c=document.createElement('div');c.className='chip';c.innerHTML='<span class="ic">&#128196;</span>'+esc(a.name)+' <button class="rm" aria-label="Remove attachment">&times;</button>';c.querySelector('.rm').onclick=()=>{attachments.splice(i,1);renderChips();};chips.appendChild(c);});}
  attachBtn.onclick=()=>fileIn.click();
  fileIn.onchange=()=>{[...fileIn.files].forEach(uploadFile);fileIn.value='';};
  ['dragenter','dragover'].forEach(e=>document.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('on');}));
  document.addEventListener('dragleave',ev=>{if(ev.clientX===0&&ev.clientY===0)drop.classList.remove('on');});
  document.addEventListener('drop',ev=>{ev.preventDefault();drop.classList.remove('on');if(ev.dataTransfer&&ev.dataTransfer.files)[...ev.dataTransfer.files].forEach(uploadFile);});
  // Paste images straight from the clipboard (e.g. a screenshot) into the composer;
  // typed/copied text still pastes normally because we only intercept image items.
  ta.addEventListener('paste',ev=>{
    const items=(ev.clipboardData&&ev.clipboardData.items)||[]; const imgs=[];
    for(const it of items){if(it.kind==='file'&&it.type.indexOf('image/')===0){const f=it.getAsFile();if(f)imgs.push(f);}}
    if(!imgs.length)return;
    ev.preventDefault();
    imgs.forEach((f,n)=>{const ext=(f.type.split('/')[1]||'png').replace('jpeg','jpg');
      const nm=(f.name&&f.name!=='image.png')?f.name:('pasted-'+Date.now()+(n?'-'+n:'')+'.'+ext);
      uploadFile(new File([f],nm,{type:f.type}));});
  });

  async function send(text){
    text=(text||'').trim(); if((!text&&!attachments.length)||busy)return;
    if(agentMode&&text){return runAgent(text);}
    if(!current)newSession();
    const sent=attachments.slice(), attNames=sent.map(a=>a.name);
    const s=curSession();
    const history=sessionHistory(s);
    renderMsg('user',text||'(sent attachment)',attNames);
    if(s){s.msgs.push({role:'user',text:text||'(sent attachment)',atts:attNames});if(!s.title)s.title=(text||'Attachment').slice(0,32);saveSessions();renderSessions();}
    const predicted=estimateTier(text); activeTier=predicted;
    ta.value='';auto();attachments=[];renderChips();setBusy(true,predicted);
    const node=renderMsg('bot',''); const md=node.querySelector('.md');
    md.innerHTML='<span class="think" role="status" aria-label="Working"><i></i></span>'+(predicted==='ultra'?'<span class="tiernote">reasoning model — this can take a moment</span>':predicted==='smart'?'<span class="tiernote">smart model</span>':'');
    let reply='', streamed='';
    const t0=performance.now(); let tFirst=0;
    currentAbort=new AbortController();currentRequest=crypto.randomUUID();
    try{
      const speakStream=voiceActive; if(speakStream)voiceTurnReset();
      const r=await fetch('/api/stream',{method:'POST',headers:{'Content-Type':'application/json','X-Jarvis-Request':currentRequest},signal:currentAbort.signal,body:JSON.stringify({command:text,attachments:sent.map(a=>a.path),history,voice:speakStream})});
      if(!r.ok)throw new Error((await r.json()).message||'Request failed');
      const reader=r.body.getReader(), dec=new TextDecoder(); let buf='', done=null;
      while(true){
        const {done:fin,value}=await reader.read(); if(fin)break;
        buf+=dec.decode(value,{stream:true}); let i;
        while((i=buf.indexOf('\n\n'))>=0){
          const line=buf.slice(0,i); buf=buf.slice(i+2);
          if(!line.startsWith('data:'))continue;
          let ev; try{ev=JSON.parse(line.slice(5).trim());}catch(e){continue;}
          if(ev.type==='token'){if(!tFirst)tFirst=performance.now();if(speakStream&&!streamed)vmark('reply');streamed+=ev.text;md.innerHTML=mdToHtml(streamed);chat.scrollTop=chat.scrollHeight;}
          else if(ev.type==='reset'){streamed='';md.innerHTML='';ttsQueue=[];}
          else if(ev.type==='tts'){if(speakStream)enqueueTTS(ev.text);}
          else if(ev.type==='done'){if(speakStream)vmark('done');done=ev;}
        }
      }
      const d=done||{ok:false,message:(streamed?streamed+'\n\n':'')+'Connection ended before the reply completed.',data:{}};
      reply=d.message||streamed||'(no output)';
      if(!d.ok)node.classList.add('err');
      // Chat already revealed itself token-by-token; a local command result arrives
      // whole (streamed==''), so give it the same live feel with a typewriter pass.
      if(streamed)setMd(md,reply); else typewriter(md,reply);
      activeTier=(d.data&&d.data.planner&&d.data.planner.model)||predicted;
      const data=Object.assign({},d.data||{});['planner','messages','sources','fields','fill_preview','field_mappings','results'].forEach(k=>delete data[k]);
      if(Object.keys(data).length){const det=document.createElement('details');det.className='det';det.innerHTML='<summary>details</summary>';const pre=document.createElement('div');pre.className='data';pre.textContent=JSON.stringify(data,null,2);det.appendChild(pre);node.querySelector('.content').appendChild(det);}
      // Show which model answered and how long it took, so tier/latency is visible.
      const planner=d.data&&d.data.planner, totalS=((performance.now()-t0)/1000).toFixed(1), bits=[];
      if(planner&&planner.model){const nm={fast:PLANNER,smart:SMART,ultra:ULTRA,openrouter:'backup model',unavailable:'no model reachable'}[planner.model]||planner.model;bits.push(nm);if(tFirst)bits.push('first token '+((tFirst-t0)/1000).toFixed(1)+'s');}
      else bits.push('local');
      bits.push(totalS+'s'+(planner&&planner.model?' total':''));
      const meta=document.createElement('div');meta.className='meta';meta.textContent='⚡ '+bits.join(' · ');node.querySelector('.content').appendChild(meta);
      const ss=s;if(ss){ss.msgs.push({role:'bot',text:reply,extra:dataDigest(d.data)});saveSessions();}
      loadVault();
    }catch(err){
      if(err&&err.name==='AbortError'){reply=streamed;setMd(md,streamed||'_(stopped)_');const ss=s;if(ss&&streamed){ss.msgs.push({role:'bot',text:streamed});saveSessions();}}
      else{md.innerHTML='';node.classList.add('err');md.textContent='Connection error: '+err;}
    }
    finally{currentAbort=null;currentRequest=null;setBusy(false);ta.focus();loadAgents();if(voiceActive)voiceTurnDone(reply);}
    return reply;
  }

  /* autonomous agent mode — streams plan/act/observe steps into a live trace */
  async function runAgent(goal){
    if(!current)newSession();
    const s=curSession();
    const history=sessionHistory(s);   // captured before this goal is added, like send()
    renderMsg('user',goal); if(s){s.msgs.push({role:'user',text:goal});if(!s.title)s.title=goal.slice(0,32);saveSessions();renderSessions();}
    ta.value='';auto();setBusy(true,'smart');
    const node=renderMsg('bot',''); const md=node.querySelector('.md');
    const trace=document.createElement('div');trace.className='trace';
    trace.innerHTML='<div class="thead"><span class="gdot"></span> Agent · planning…</div>';
    md.innerHTML='';md.appendChild(trace);
    let reply='';
    currentAbort=new AbortController();currentRequest=crypto.randomUUID();
    try{
      const r=await fetch('/api/agent',{method:'POST',headers:{'Content-Type':'application/json','X-Jarvis-Request':currentRequest},signal:currentAbort.signal,body:JSON.stringify({goal,history})});
      if(!r.ok)throw new Error((await r.json()).message||'Request failed');
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
            trace.querySelector('.thead').innerHTML='<span class="gdot"></span> Agent · '+(st.index+1)+' step(s)…';}
          else if(ev.type==='done'){reply=ev.message||'';trace.classList.add(ev.ok?'done':'fail');
            trace.querySelector('.thead').innerHTML='<span class="gdot"></span> Agent · '+(ev.ok?'done':'stopped');
            const ans=document.createElement('div');ans.className='md';ans.style.marginTop='8px';setMd(ans,reply);
            node.querySelector('.content').appendChild(ans);}
        }
      }
      const ss=s;if(ss&&reply){ss.msgs.push({role:'bot',text:reply});saveSessions();}
      loadVault();
    }catch(err){
      if(err&&err.name==='AbortError'){trace.classList.add('fail');trace.querySelector('.thead').innerHTML='<span class="gdot"></span> Agent · stopped';}
      else{node.classList.add('err');const e=document.createElement('div');e.className='md';e.textContent='Agent error: '+err;node.querySelector('.content').appendChild(e);}
    }
    finally{currentAbort=null;currentRequest=null;setBusy(false);ta.focus();loadAgents();if(voiceActive&&reply)speak(reply);}
    return reply;
  }

  /* connections dropdown */
  const PLANNER='{{PLANNER}}', SMART='{{SMART}}', ULTRA='{{ULTRA}}', VISION='{{VISION}}';
  const conn={fast:[PLANNER!=='heuristic'?'ok':'off',PLANNER],smart:[SMART!=='—'?'ok':'off',SMART],ultra:[ULTRA!=='—'?'ok':'off',ULTRA],vision:[VISION!=='—'?'ok':'off',VISION],vault:['off','checking…'],gpu:['off','n/a']};
  function renderConn(){
    const cap=s=>s.charAt(0).toUpperCase()+s.slice(1);
    const rows=[[cap(TIER_NAME.fast),conn.fast],[cap(TIER_NAME.smart),conn.smart],[cap(TIER_NAME.ultra),conn.ultra],['Vision model',conn.vision],['Obsidian vault',conn.vault],['GPU',conn.gpu]];
    document.getElementById('connlist').innerHTML=rows.map(([k,[s,v]])=>'<div class="crow"><span class="d '+(s==='ok'?'':s)+'"></span><span class="k">'+k+'</span><span class="v" title="'+esc(String(v))+'">'+esc(String(v))+'</span></div>').join('');
  }
  renderConn();

  /* metrics */
  function bar(label,val,unit,cls){return '<div class="metric"><div class="top"><span>'+label+'</span><b>'+(val==null?'n/a':val+unit)+'</b></div><div class="bar '+(cls||'')+'"><i style="width:'+(val==null?0:Math.min(val,100))+'%"></i></div></div>';}
  async function loadMetrics(){try{const m=await (await fetch('/api/metrics')).json();let h=bar('CPU',m.cpu_percent,'%');h+=bar('Memory',m.ram_percent,'%');(m.gpus||[]).forEach(g=>{h+=bar('GPU · '+g.name.replace(/NVIDIA |GeForce /g,''),g.util_percent,'%','g');h+=bar('VRAM',g.mem_total_mb?Math.round(g.mem_used_mb/g.mem_total_mb*100):null,'%','g');});document.getElementById('metrics').innerHTML=h;
    if(m.gpus&&m.gpus.length){conn.gpu=['ok',m.gpus[0].name.replace(/NVIDIA |GeForce /g,'')];}else{conn.gpu=['off','metrics unavailable'];}renderConn();}catch(e){}}
  const pollWhenVisible=(fn,ms)=>setInterval(()=>{if(!document.hidden)fn();},ms);
  const drawerOpen=()=>document.getElementById('sysDrawer').classList.contains('open');
  pollWhenVisible(()=>{if(drawerOpen())loadMetrics();},5000);loadMetrics();

  /* vault + note browser */
  function renderNoteList(names){
    const notes=document.getElementById('notes');notes.innerHTML='';
    if(!names.length){const e=document.createElement('div');e.className='note';e.textContent='no notes';notes.appendChild(e);return;}
    names.forEach(name=>{const e=document.createElement('button');e.className='note clk';e.textContent=name;e.title=name;e.onclick=()=>openNote(name);notes.appendChild(e);});
  }
  let allNotes=[];
  async function loadVault(){try{const v=await (await fetch('/api/vault')).json();const d=document.querySelector('#vstat .d');const t=document.getElementById('vtext');if(v.ok){d.classList.remove('off');t.textContent=(v.status.note_count||0)+' notes connected';conn.vault=['ok',(v.status.note_count||0)+' notes'];}else{d.classList.add('off');t.textContent='not connected';conn.vault=['off','not connected'];}renderConn();allNotes=(v.notes||[]).map(n=>n.name);renderNoteList(allNotes.slice(0,12));}catch(e){}}
  async function openNote(name){
    const nv=document.getElementById('noteViewer');document.body.appendChild(nv);
    document.getElementById('nvTitle').textContent=name;
    document.getElementById('nvBody').innerHTML='<div style="color:var(--muted)">Loading…</div>';
    document.getElementById('nvLinks').innerHTML='';
    nv.classList.add('open');
    try{
      const r=await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'read',name})});
      const d=await r.json();
      if(!d.ok){document.getElementById('nvBody').innerHTML='<div style="color:var(--warn)">'+esc(d.message||'Could not open note.')+'</div>';return;}
      document.getElementById('nvTitle').textContent=d.name||name;
      setMd(document.getElementById('nvBody'),d.text||'');
      const links=document.getElementById('nvLinks');links.innerHTML='';
      const group=(label,arr)=>{if(!arr||!arr.length)return;const l=document.createElement('span');l.className='lbl';l.textContent=label;links.appendChild(l);arr.forEach(nm=>{const c=document.createElement('button');c.className='lk';c.textContent=nm;c.onclick=()=>openNote(nm);links.appendChild(c);});};
      group('links to',d.outlinks);group('linked from',d.backlinks);
    }catch(e){document.getElementById('nvBody').innerHTML='<div style="color:var(--warn)">Could not reach the vault.</div>';}
  }
  document.getElementById('nvClose').onclick=()=>document.getElementById('noteViewer').classList.remove('open');
  let vaultSearchTimer=null;
  document.getElementById('vaultSearch').addEventListener('input',function(){
    clearTimeout(vaultSearchTimer);const q=this.value.trim();
    vaultSearchTimer=setTimeout(async()=>{
      if(!q){renderNoteList(allNotes.slice(0,12));return;}
      try{const r=await fetch('/api/notes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'search',query:q})});
        const d=await r.json();renderNoteList((d.results||[]).map(h=>h.name));}catch(e){}
    },220);
  });
  loadVault();

  /* agent control room */
  async function loadAgents(){try{const r=await fetch('/api/agents');const d=await r.json();if(!d.ok)return;const s=d.control_room.summary;
    document.getElementById('agentSummary').innerHTML='<div class="agentSummary"><div class="agentStat"><b>'+s.working+'</b><span>working</span></div><div class="agentStat"><b>'+s.idle+'</b><span>idle</span></div><div class="agentStat"><b>'+s.available+'</b><span>registered</span></div></div>';
    document.getElementById('agentList').innerHTML=d.control_room.agents.map(a=>{const done=(a.completed||0)>0?' · ✓'+a.completed:'';const fail=(a.failed||0)>0?' ✗'+a.failed:'';return '<button class="agentcard '+a.status+'" data-agent="'+esc(a.id)+'"><div class="top"><span class="dot"></span><b>'+esc(a.name)+'</b><span class="status">'+esc(a.status)+done+fail+'</span></div><div class="role">'+esc(a.role)+'</div><div class="task">'+esc(a.current_task||a.last_message||'Ready.')+'</div></button>';}).join('');
    document.querySelectorAll('.agentcard').forEach(btn=>{btn.onclick=()=>send('agent '+btn.dataset.agent);});
  }catch(e){document.getElementById('agentSummary').textContent='Could not refresh tool activity.';}}
  pollWhenVisible(()=>{if(drawerOpen())loadAgents();},5000);loadAgents();

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
  async function loadSchedule(){try{const d=await (await fetch('/api/schedule')).json();renderSchedule(d.jobs);}catch(e){document.getElementById('schedMsg').textContent='Could not load schedules. Check the local server.';}}
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
  pollWhenVisible(()=>{if(document.getElementById('schedPanel').open)loadSchedule();},15000);

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
  pollWhenVisible(()=>{if(document.getElementById('runsPanel').open)loadAgentRuns();},10000);

  /* map */
  async function loadMap(query){
    const msg=document.getElementById('mapMsg'),view=document.getElementById('mapView');
    msg.className='mapmsg';msg.textContent='Locating…';
    try{
      const r=await fetch('/api/map',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});
      const d=await r.json();
      if(!d.ok){msg.textContent=d.message||'Not found.';msg.className='mapmsg err';view.innerHTML='';return;}
      msg.textContent=d.message||'';
      let h='';
      if(d.embed)h+='<iframe class="mapframe" src="'+esc(d.embed)+'" loading="lazy" title="map"></iframe>';
      (d.points||[]).forEach(p=>{h+='<div class="mappt"><b>'+esc(String(p.role||'place'))+'</b>'+esc(String(p.label||''))+'</div>';});
      if(d.directions)h+='<a class="maplink" href="'+esc(d.directions)+'" target="_blank" rel="noopener">Open route on OpenStreetMap &#8599;</a>';
      view.innerHTML=h;
    }catch(e){msg.textContent='Could not reach the map service.';msg.className='mapmsg err';}
  }
  document.getElementById('mapGo').onclick=()=>{const q=document.getElementById('mapQuery').value.trim();if(q)loadMap(q);};
  document.getElementById('mapQuery').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();document.getElementById('mapGo').click();}});

  /* trip planner */
  let tripStops=[];
  function renderTripStops(){
    const box=document.getElementById('tripStops');box.innerHTML='';
    tripStops.forEach((s,i)=>{
      const row=document.createElement('div');row.className='tripstop';
      row.innerHTML='<span class="seq">'+(i+1)+'</span><span class="nm">'+esc(s)+'</span>';
      const up=document.createElement('button');up.textContent='↑';up.title='move up';up.onclick=()=>{if(i>0){[tripStops[i-1],tripStops[i]]=[tripStops[i],tripStops[i-1]];renderTripStops();}};
      const dn=document.createElement('button');dn.textContent='↓';dn.title='move down';dn.onclick=()=>{if(i<tripStops.length-1){[tripStops[i+1],tripStops[i]]=[tripStops[i],tripStops[i+1]];renderTripStops();}};
      const rm=document.createElement('button');rm.textContent='✕';rm.title='remove';rm.onclick=()=>{tripStops.splice(i,1);renderTripStops();};
      row.appendChild(up);row.appendChild(dn);row.appendChild(rm);box.appendChild(row);
    });
  }
  function addTripStop(){const inp=document.getElementById('tripStop');const v=inp.value.trim();if(v){tripStops.push(v);inp.value='';renderTripStops();inp.focus();}}
  document.getElementById('tripAdd').onclick=addTripStop;
  document.getElementById('tripStop').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addTripStop();}});
  function tripSvg(d){
    const box=d.bbox; if(!box||box.length!==4)return '';
    const W=300,H=150,pad=14;
    const lon2x=lon=>pad+(box[2]===box[0]?W/2:(lon-box[0])/(box[2]-box[0])*(W-2*pad));
    const lat2y=lat=>pad+(box[3]===box[1]?H/2:(box[3]-lat)/(box[3]-box[1])*(H-2*pad)); // lat inverted for screen
    const line=(d.geometry&&d.geometry.length>1)?d.geometry:(d.points||[]).map(p=>[p.longitude,p.latitude]);
    let path='';line.forEach((c,i)=>{path+=(i?'L':'M')+lon2x(c[0]).toFixed(1)+' '+lat2y(c[1]).toFixed(1)+' ';});
    let dots='';(d.points||[]).forEach((p,i)=>{const x=lon2x(p.longitude),y=lat2y(p.latitude);dots+='<circle class="stopdot" cx="'+x.toFixed(1)+'" cy="'+y.toFixed(1)+'" r="7"/><text class="stopnum" x="'+x.toFixed(1)+'" y="'+(y+3).toFixed(1)+'" text-anchor="middle">'+(i+1)+'</text>';});
    return '<svg class="tripsvg" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet"><path class="route" d="'+path+'"/>'+dots+'</svg>';
  }
  function fmtDur(sec){if(sec==null)return '';const m=Math.round(sec/60);if(m<60)return m+' min';return Math.floor(m/60)+'h '+String(m%60).padStart(2,'0')+'m';}
  document.getElementById('tripGo').onclick=async()=>{
    const msg=document.getElementById('tripMsg'),view=document.getElementById('tripView');
    if(tripStops.length<2){msg.className='mapmsg err';msg.textContent='Add at least two stops.';return;}
    msg.className='mapmsg';msg.textContent='Planning…';view.innerHTML='';
    try{
      const r=await fetch('/api/trip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stops:tripStops})});
      const d=await r.json();
      if(!d.ok){msg.className='mapmsg err';msg.textContent=d.message||'Could not plan the trip.';return;}
      msg.textContent='';
      let h=tripSvg(d);
      (d.legs||[]).forEach(l=>{h+='<div class="tripleg">'+esc(l.from)+' → '+esc(l.to)+' <span class="d">'+(l.miles!=null?Math.round(l.miles)+' mi':'')+(l.seconds!=null?' · ~'+fmtDur(l.seconds):'')+'</span></div>';});
      if(d.total_mi!=null)h+='<div class="triptotal">Total: '+Math.round(d.total_mi)+' mi'+(d.total_seconds!=null?' · ~'+fmtDur(d.total_seconds):'')+'</div>';
      if(d.directions)h+='<a class="maplink" href="'+esc(d.directions)+'" target="_blank" rel="noopener">Open full route on OpenStreetMap ↗</a>';
      view.innerHTML=h;
    }catch(e){msg.className='mapmsg err';msg.textContent='Could not reach the trip service.';}
  };

  /* adaptive HUD: transparency, compact layout, always-on-top */
  const hudPop=document.getElementById('hudPop'),hudBtn=document.getElementById('hudBtn');
  const opRange=document.getElementById('opacityRange'),opVal=document.getElementById('opacityVal');
  const onTopToggle=document.getElementById('onTopToggle'),compactBtn=document.getElementById('compactBtn');
  async function postWindow(body){try{const r=await fetch('/api/window',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});return await r.json();}catch(e){return {native:false};}}
  async function applyOpacity(pct,post){
    const o=pct/100; opVal.textContent=pct+'%';
    // Live visual fade; if a real desktop window handled it, drop the CSS fade.
    document.querySelector('.app').style.opacity=String(o);
    if(post){const d=await postWindow({opacity:o}); if(d&&d.applied&&d.applied.opacity!=null){document.querySelector('.app').style.opacity='';}}
  }
  opRange.addEventListener('input',()=>applyOpacity(+opRange.value,false));
  opRange.addEventListener('change',()=>{localStorage.setItem('hudOpacity',opRange.value);applyOpacity(+opRange.value,true);});
  function setCompact(on){document.body.classList.toggle('compact',on);compactBtn.classList.toggle('on',on);compactBtn.setAttribute('aria-checked',String(on));localStorage.setItem('hudCompact',on?'1':'0');if(!on)requestAnimationFrame(fitCanvas);}
  compactBtn.onclick=()=>setCompact(!document.body.classList.contains('compact'));
  async function setOnTop(on,post){onTopToggle.classList.toggle('on',on);onTopToggle.setAttribute('aria-checked',String(on));localStorage.setItem('hudOnTop',on?'1':'0');if(post){const d=await postWindow({on_top:on});if(!(d&&d.applied&&d.applied.on_top!=null))document.getElementById('hudHint').textContent='Always-on-top works only in the desktop app.';}}
  onTopToggle.onclick=()=>setOnTop(!onTopToggle.classList.contains('on'),true);
  hudBtn.onclick=e=>{e.stopPropagation();hudPop.classList.toggle('open');hudBtn.classList.toggle('on',hudPop.classList.contains('open'));};
  document.addEventListener('click',e=>{if(!hudPop.contains(e.target)&&e.target!==hudBtn){hudPop.classList.remove('open');hudBtn.classList.remove('on');}});
  (function restoreHud(){
    const op=localStorage.getItem('hudOpacity'); if(op){opRange.value=op;applyOpacity(+op,true);}
    if(localStorage.getItem('hudCompact')==='1')setCompact(true);
    if(localStorage.getItem('hudOnTop')==='1')setOnTop(true,true);
  })();

  /* system status drawer: models, usage, vault and the tool panels live here so the
     main view stays quiet; opened from the status pill or the rail footer */
  const sysDrawer=document.getElementById('sysDrawer'),scrim=document.getElementById('scrim'),healthPill=document.getElementById('healthPill'),
        healthText=document.getElementById('healthText'),railStatus=document.getElementById('railStatus'),railText=document.getElementById('railText');
  let drawerOpener=null;
  const appRoot=document.querySelector('.app');
  function setDrawer(open,opener){
    if(open)drawerOpener=opener||document.activeElement;
    else{appRoot.inert=false;if(sysDrawer.contains(document.activeElement))(drawerOpener&&drawerOpener.focus?drawerOpener:healthPill).focus();}   // move focus out before hiding
    sysDrawer.classList.toggle('open',open);scrim.classList.toggle('open',open);
    sysDrawer.setAttribute('aria-hidden',String(!open));
    healthPill.setAttribute('aria-expanded',String(open));railStatus.setAttribute('aria-expanded',String(open));
    if(open){appRoot.inert=true;loadMetrics();loadAgents();document.getElementById('drawerClose').focus();} else drawerOpener=null;   // inert keeps Tab inside the drawer
  }
  function setStatus(cls,label){   // one health state painted on both drawer triggers
    healthPill.className='pill '+cls;healthText.textContent=label;
    railStatus.className='sysbtn '+cls;railText.textContent=label;
    const name='System status: '+label;healthPill.setAttribute('aria-label',name);railStatus.setAttribute('aria-label',name);
  }
  healthPill.onclick=()=>setDrawer(!sysDrawer.classList.contains('open'),healthPill);
  railStatus.onclick=()=>setDrawer(true,railStatus);
  document.getElementById('drawerClose').onclick=()=>setDrawer(false);
  scrim.onclick=()=>setDrawer(false);

  /* multi-page router */
  const VIEWS=['chat','overview','jobs','pipeline'];
  function setView(v){
    if(VIEWS.indexOf(v)<0)v='chat';
    document.body.dataset.view=v;
    if(v==='chat')requestAnimationFrame(fitCanvas);
    document.querySelectorAll('#nav .navbtn').forEach(b=>b.classList.toggle('on',b.dataset.view===v));
    if(v==='jobs')loadJobs();
    if(v==='overview')loadOverview();
    if(v==='pipeline')loadPipeline();
    pipeAutoRefresh(v==='pipeline');
  }
  document.querySelectorAll('#nav .navbtn[data-view]').forEach(b=>{b.onclick=()=>{location.hash='#/'+b.dataset.view;};});
  window.addEventListener('hashchange',()=>setView(location.hash.replace('#/','')));

  /* inline SVG charts (no CDN — works offline) */
  const STAGES=['lead','applied','screen','interview','final','offer','rejected'];
  function svgFunnel(funnel){
    const max=Math.max(1,...funnel.map(f=>f.count));
    const rowH=26, W=300, padL=78, barW=W-padL-30;
    let h=funnel.length*rowH+8, y=4, out='<svg width="100%" viewBox="0 0 '+W+' '+h+'" preserveAspectRatio="xMidYMid meet" style="font-family:var(--sans)">';
    funnel.forEach(f=>{
      const w=Math.max(2,Math.round(f.count/max*barW));
      out+='<text x="0" y="'+(y+12)+'" style="fill:var(--muted)" font-size="10">'+esc(f.stage)+'</text>';
      out+='<rect x="'+padL+'" y="'+y+'" width="'+w+'" height="16" rx="4" style="fill:var(--accent);fill-opacity:.85"/>';
      out+='<text x="'+(padL+w+5)+'" y="'+(y+12)+'" style="fill:var(--text-2)" font-size="10">'+f.count+'</text>';
      y+=rowH;
    });
    return out+'</svg>';
  }
  function svgTrend(byWeek){
    if(!byWeek||!byWeek.length)return '<div style="color:var(--muted);font-size:11px">No applications yet.</div>';
    const max=Math.max(1,...byWeek.map(w=>w.count));
    const W=300,H=120,padB=22,n=byWeek.length,bw=Math.min(34,(W-20)/n-6);
    let out='<svg width="100%" viewBox="0 0 '+W+' '+H+'" preserveAspectRatio="xMidYMid meet" style="font-family:var(--sans)">';
    byWeek.forEach((wk,i)=>{
      const x=12+i*((W-20)/n), bh=Math.round(wk.count/max*(H-padB-10));
      out+='<rect x="'+x+'" y="'+(H-padB-bh)+'" width="'+bw+'" height="'+Math.max(2,bh)+'" rx="3" style="fill:var(--accent);fill-opacity:.7"/>';
      out+='<text x="'+(x+bw/2)+'" y="'+(H-padB-bh-4)+'" style="fill:var(--text-2)" font-size="9" text-anchor="middle">'+wk.count+'</text>';
      out+='<text x="'+(x+bw/2)+'" y="'+(H-7)+'" style="fill:var(--muted)" font-size="8" text-anchor="middle">'+esc(wk.week.split('-W')[1]||'')+'</text>';
    });
    return out+'</svg>';
  }
  function statCard(k,v,sub){return '<div class="statcard"><div class="k">'+esc(k)+'</div><div class="v">'+v+(sub?' <small>'+esc(sub)+'</small>':'')+'</div></div>';}

  /* job tracker page */
  function fillStageSelect(sel,current){sel.innerHTML='';STAGES.forEach(s=>{const o=document.createElement('option');o.value=s;o.textContent=s;if(s===current)o.selected=true;sel.appendChild(o);});}
  async function loadJobs(){
    try{const d=await (await fetch('/api/jobs')).json();renderJobs(d);}catch(e){document.getElementById('jobList').innerHTML='<div style="color:var(--warn)">Could not load jobs.</div>';}
  }
  function renderJobs(d){
    const s=d.stats||{funnel:[],by_week:[]};
    document.getElementById('jobSub').textContent=(s.applications||0)+' application(s)';
    document.getElementById('jobStats').innerHTML=
      statCard('Applications',s.applications||0)+statCard('Interviews',s.interviews||0)+statCard('Offers',s.offers||0)+statCard('Response rate',Math.round((s.response_rate||0)*100)+'%');
    document.getElementById('jobFunnel').innerHTML=svgFunnel(s.funnel||[]);
    document.getElementById('jobTrend').innerHTML=svgTrend(s.by_week||[]);
    const list=document.getElementById('jobList');list.innerHTML='';
    if(!(d.jobs||[]).length){list.innerHTML='<div style="color:var(--muted);font-size:12px">No applications yet — add one above.</div>';return;}
    d.jobs.forEach(j=>{
      const row=document.createElement('div');row.className='jobrow';
      row.innerHTML='<div class="co">'+esc(j.company)+(j.role?'<small>'+esc(j.role)+'</small>':'')+'</div>';
      const sel=document.createElement('select');fillStageSelect(sel,j.stage);sel.setAttribute('aria-label','Stage for '+j.company);sel.onchange=()=>postJob({action:'update',id:j.id,stage:sel.value});
      row.appendChild(sel);
      const nd=document.createElement('span');nd.className='nd';nd.textContent=j.next_date||'';row.appendChild(nd);
      const rm=document.createElement('button');rm.className='rm';rm.innerHTML='&times;';rm.title='remove';rm.onclick=()=>postJob({action:'remove',id:j.id});
      row.appendChild(rm);list.appendChild(row);
    });
  }
  async function postJob(body){try{const r=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!d.ok)throw new Error(d.message||'Job update failed');renderJobs(d);return true;}catch(e){document.getElementById('jobSub').textContent=String(e);return false;}}
  (function initJobForm(){
    fillStageSelect(document.getElementById('jobStage'),'applied');
    document.getElementById('jobAdd').onclick=async()=>{
      const c=document.getElementById('jobCompany'),r=document.getElementById('jobRole'),s=document.getElementById('jobStage');
      if(!c.value.trim())return;
      if(await postJob({action:'add',company:c.value.trim(),role:r.value.trim(),stage:s.value})){c.value='';r.value='';c.focus();}
    };
    document.getElementById('jobCompany').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('jobAdd').click();});
  })();

  /* CoPilot — tailor a resume to a JD (ATS score + grounded bullets/cover letter) */
  document.getElementById('tlGo').onclick=async()=>{
    const resume=document.getElementById('tlResume').value.trim(),jd=document.getElementById('tlJD').value.trim();
    const msg=document.getElementById('tlMsg'),res=document.getElementById('tlResult');
    if(!resume||!jd){msg.className='mapmsg err';msg.textContent='Paste both your resume and the job description.';return;}
    msg.className='mapmsg';msg.textContent='Tailoring… this can take a moment.';res.innerHTML='';
    try{
      const r=await fetch('/api/copilot',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resume_text:resume,job_text:jd,company:document.getElementById('tlCompany').value.trim(),role:document.getElementById('tlRole').value.trim()})});
      const d=await r.json();
      if(!d.ok){msg.className='mapmsg err';msg.textContent=d.message||'Could not tailor.';return;}
      msg.textContent=(d.ats&&typeof d.ats.score==='number')?('ATS match '+d.ats.score+'%'+(d.used_llm?'':' · add a model key for the written sections')):'';
      setMd(res,d.message||'');
    }catch(e){msg.className='mapmsg err';msg.textContent='Could not reach the copilot.';}
  };

  /* live pipeline page */
  const PIPE_STAGES=['lead','applied','screen','interview','final','offer','rejected'];
  let pipeTimer=null, pipeBusy=false;
  function scoreBadge(ats){
    if(!ats||typeof ats.score!=='number')return '<span class="score s-na">no score</span>';
    const c=ats.score>=70?'s-hi':(ats.score>=45?'s-mid':'s-lo');
    return '<span class="score '+c+'" title="'+(ats.hit_count||0)+'/'+((ats.hit_count||0)+(ats.miss_count||0))+' keywords">ATS '+ats.score+'%</span>';
  }
  async function loadPipeline(){
    try{const d=await (await fetch('/api/pipeline')).json();renderPipeline(d);}
    catch(e){document.getElementById('pipeBoard').innerHTML='<div style="color:var(--warn)">Could not load pipeline.</div>';}
  }
  let profileLoaded=false;
  function renderPipeline(d){
    const s=d.stats||{}, r=d.resume||{};
    if(!profileLoaded){const p=r.profile||{};document.getElementById('rsContact').value=p.contact_links||'';document.getElementById('rsCerts').value=p.cert_links||'';document.getElementById('rsGithub').value=p.github_user||'';profileLoaded=true;}
    document.getElementById('pipeSub').textContent=(s.leads||0)+' lead(s) · '+(s.total||0)+' tracked';
    document.getElementById('pipeStats').innerHTML=
      statCard('Leads',s.leads||0)+statCard('Applications',s.applications||0)+statCard('Interviews',s.interviews||0)+statCard('Offers',s.offers||0);
    document.getElementById('rsStat').textContent=r.present?('resume set · '+(r.chars||0)+' chars'+(r.source?(' · '+r.source):'')):'no base resume yet — paste or load a file to enable scoring';
    const byStage={};PIPE_STAGES.forEach(st=>byStage[st]=[]);
    (d.jobs||[]).forEach(j=>{(byStage[j.stage]||(byStage[j.stage]=[])).push(j);});
    const board=document.getElementById('pipeBoard');board.innerHTML='';
    PIPE_STAGES.forEach(st=>{
      const col=document.createElement('div');col.className='col';
      col.innerHTML='<h4>'+esc(st)+' <b>'+byStage[st].length+'</b></h4>';
      byStage[st].forEach(j=>col.appendChild(pipeCard(j)));
      board.appendChild(col);
    });
  }
  function pipeCard(j){
    const card=document.createElement('div');card.className='pcard';
    let h='<div class="pco">'+esc(j.company||'')+'</div>';
    if(j.role)h+='<div class="prole">'+esc(j.role)+'</div>';
    h+='<div class="prow">'+scoreBadge(j.ats)+(j.tailored?'<span class="badge t-on">tailored</span>':'<span class="badge">not tailored</span>')+'</div>';
    card.innerHTML=h;
    const row=document.createElement('div');row.className='prow';row.style.marginTop='7px';
    const sel=document.createElement('select');fillStageSelect(sel,j.stage);sel.setAttribute('aria-label','Stage for '+j.company);sel.onchange=()=>postPipe({action:'stage',id:j.id,stage:sel.value});row.appendChild(sel);
    const tb=document.createElement('button');tb.textContent=j.tailored?'Re-tailor':'Tailor';tb.onclick=()=>tailorJob(j.id,tb);row.appendChild(tb);
    if(j.tailored_pdf){const pb=document.createElement('button');pb.textContent='PDF';pb.title='Download tailored resume PDF';pb.onclick=()=>window.open('/api/resume-pdf?id='+j.id,'_blank');row.appendChild(pb);}
    if(j.tailored&&j.tailored_package){const vb=document.createElement('button');vb.textContent='Preview';vb.onclick=()=>showPackage(j);row.appendChild(vb);}
    if(j.url&&/^https?:\/\//i.test(j.url)){const lb=document.createElement('button');lb.textContent='Open';lb.onclick=()=>window.open(j.url,'_blank','noopener');row.appendChild(lb);}
    card.appendChild(row);
    return card;
  }
  function showPackage(j){
    document.getElementById('pkgTitle').textContent='Tailored resume — '+(j.company||'')+(j.role?(' · '+j.role):'');
    const frame=document.createElement('iframe');
    frame.style.cssText='width:100%;height:760px;border:1px solid var(--hair-2);border-radius:8px;background:#fff';
    frame.setAttribute('sandbox','');frame.title='Tailored resume preview';
    frame.srcdoc=j.tailored_package||'';
    const body=document.getElementById('pkgBody');body.innerHTML='';body.appendChild(frame);
    document.getElementById('pkgCard').style.display='block';
    document.getElementById('pkgCard').scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  async function postPipe(body){
    const msg=document.getElementById('pipeMsg');
    try{pipeBusy=true;const r=await fetch('/api/pipeline',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json();if(d.message){msg.className='mapmsg'+(d.ok?'':' err');msg.textContent=d.message;}renderPipeline(d);return d;}
    catch(e){msg.className='mapmsg err';msg.textContent='Could not reach the server.';}
    finally{pipeBusy=false;}
  }
  async function tailorJob(id,btn){
    const msg=document.getElementById('pipeMsg');msg.className='mapmsg';msg.textContent='Tailoring #'+id+'… this can take a moment.';
    if(btn){btn.disabled=true;btn.textContent='…';}
    const d=await postPipe({action:'tailor',id:id});
    if(btn)btn.disabled=false;
    if(d&&d.ok){const j=(d.jobs||[]).find(x=>x.id===id);if(j)showPackage(j);}
  }
  (function initPipe(){
    document.getElementById('rsProfileSave').onclick=()=>postPipe({action:'profile',profile:{contact_links:document.getElementById('rsContact').value,cert_links:document.getElementById('rsCerts').value,github_user:document.getElementById('rsGithub').value}});
    document.getElementById('pullBtn').onclick=async()=>{
      const b=document.getElementById('pullBtn'),msg=document.getElementById('pipeMsg');
      b.disabled=true;b.textContent='⟳ Pulling…';msg.className='mapmsg';msg.textContent='Pulling leads from Jobright…';
      await postPipe({action:'pull'});b.disabled=false;b.textContent='⟳ Pull from Jobright';
    };
    document.getElementById('rsSave').onclick=async()=>{
      const t=document.getElementById('rsText').value.trim();if(!t)return;
      await postPipe({action:'resume',text:t});
    };
    document.getElementById('rsLoad').onclick=async()=>{
      const p=document.getElementById('rsPath').value.trim();if(!p)return;
      await postPipe({action:'resume_file',path:p});
    };
    document.getElementById('clearBtn').onclick=async()=>{
      if(!confirm('Remove all sourced leads? Your tracked applications are kept.'))return;
      await postPipe({action:'clear_leads'});
    };
    document.getElementById('autoRef').onchange=e=>pipeAutoRefresh(document.body.dataset.view==='pipeline');
  })();
  function pipeAutoRefresh(on){
    if(pipeTimer){clearInterval(pipeTimer);pipeTimer=null;}
    if(on&&document.getElementById('autoRef').checked){
      pipeTimer=setInterval(()=>{if(!document.hidden&&!pipeBusy&&document.body.dataset.view==='pipeline')loadPipeline();},20000);
    }
  }

  /* overview page */
  const HEALTH_LABEL={ok:'Online',degraded:'AI unreachable',setup:'Offline tools',checking:'Checking AI'};
  async function loadOverview(){
    document.getElementById('ovSub').textContent='Loading overview…';
    try{
      const [h,m,j]=await Promise.all([fetch('/api/health').then(r=>r.json()),fetch('/api/metrics').then(r=>r.json()),fetch('/api/jobs').then(r=>r.json())]);
      const tiers=(h.llm&&h.llm.tiers)||{};
      const busy=Object.values(tiers).filter(v=>v==='degraded').length;
      document.getElementById('ovSub').textContent=new Date().toLocaleString();
      document.getElementById('ovCards').innerHTML=
        statCard('AI status',HEALTH_LABEL[h.overall]||'Unknown',busy?busy+' tier busy':'')+
        statCard('Applications',(j.stats&&j.stats.applications)||0,((j.stats&&j.stats.offers)||0)+' offers')+
        statCard('CPU',Math.round(m.cpu_percent||0)+'%')+
        statCard('Memory',Math.round(m.ram_percent||0)+'%');
      let mh='';mh+=bar('CPU',m.cpu_percent,'%');mh+=bar('Memory',m.ram_percent,'%');(m.gpus||[]).forEach(g=>{mh+=bar('GPU',g.util_percent,'%','g');});
      document.getElementById('ovMetrics').innerHTML=mh;
      document.getElementById('ovFunnel').innerHTML=svgFunnel((j.stats&&j.stats.funnel)||[]);
    }catch(e){document.getElementById('ovSub').textContent='Overview could not load. Check the local server and retry.';}
  }
  setView(location.hash.replace('#/','')||'chat');

  /* health / first-run */
  async function loadHealth(){try{const h=await (await fetch('/api/health')).json();
    setSttEngine(h.stt&&h.stt.engine);
    const pill=healthPill;
    let label=HEALTH_LABEL[h.overall]||'Unknown';
    const busy=Object.entries((h.llm&&h.llm.tiers)||{}).filter(([k,v])=>v==='degraded').map(([k])=>k);
    if(h.overall==='ok'&&busy.length){label=busy.join('/')+' model busy';}
    setStatus(h.overall+(h.overall==='ok'&&busy.length?' busy':''),label);
    const tierNote=busy.length?` · busy: ${busy.join(', ')} (using a faster model)`:'';
    pill.title=`AI: ${h.llm.configured?(h.llm.reachable===false?'configured but unreachable':h.llm.reachable===true?'connected':'configured, checking'):'not configured'} · vault: ${h.vault.connected?'connected':'off'} · email: ${h.email.configured?'on':'off'}${tierNote}`;
    railStatus.title=pill.title;
    if(h.storage_warnings?.length){hint.textContent=h.storage_warnings.join(' ');}
    const card=document.getElementById('setupCard');
    if(card){
      if(h.overall==='setup'){card.style.display='';card.innerHTML='<b>Connect your AI</b>No language model is configured, so I can only use offline routing. Set <code>LAPTOP_AGENT_LLM_PROVIDER=openai-compatible</code> and your model connection in <code>.env</code> (e.g. <code>OPENAI_API_KEY</code>, <code>OPENAI_MODEL</code>, <code>OPENAI_BASE_URL</code>) and restart. File, search, and memory commands still work without it.';}
      else if(h.overall==='degraded'){card.style.display='';card.innerHTML='<b>AI endpoint unreachable</b>Your model is configured but I can\'t reach it right now — check your network or API key. Offline commands (files, search, memory) still work.';}
      else card.style.display='none';
    }
  }catch(e){}}
  pollWhenVisible(loadHealth,12000);loadHealth();
  document.addEventListener('visibilitychange',()=>{if(document.hidden)return;if(drawerOpen()){loadMetrics();loadAgents();}loadHealth();if(document.getElementById('schedPanel').open)loadSchedule();if(document.getElementById('runsPanel').open)loadAgentRuns();if(document.body.dataset.view==='pipeline')loadPipeline();});

  /* voice */
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition; let rec=null,dictating=false;
  // Native app window (pywebview): ?app=1 is set by run_desktop. There is no Web Speech
  // API, so voice records audio and uses the server (/api/transcribe + /api/tts).
  const NATIVE=location.search.indexOf('app=1')>=0;
  // Server speech: record here, transcribe on the server (hosted Parakeet ~1s, or a local
  // engine). More accurate than the browser recognizer and it cannot hear the reply,
  // because the microphone is only open while we choose to record — but it gives up
  // spoken barge-in, so Space and Interrupt are how you cut in. Off means the browser's
  // own recognizer, which is flakier but can be interrupted by voice.
  let typeAnim=true;
  try{typeAnim=localStorage.getItem('jarvis_typeanim')!=='off';}catch(e){}
  let sttServer=false, sttEngine=null;
  try{const saved=localStorage.getItem('jarvis_stt');if(saved)sttServer=saved==='server';}catch(e){}
  let sttChosen=false;
  try{sttChosen=!!localStorage.getItem('jarvis_stt');}catch(e){}
  function useServerStt(){return NATIVE||(sttServer&&!!sttEngine);}
  function setSttEngine(name){
    sttEngine=name||null;
    // Nothing was chosen yet: prefer the server whenever the server has an engine, since
    // that is the accurate path. An explicit choice always wins.
    if(!sttChosen)sttServer=!!sttEngine;
    const box=document.getElementById('sttServer');
    if(box){
      box.setAttribute('aria-checked',String(useServerStt()));
      box.classList.toggle('on',useServerStt());
      box.disabled=!sttEngine||NATIVE;
    }
    const note=document.getElementById('sttNote');
    if(note)note.textContent=NATIVE?'The app window always transcribes on the server.'
      :!sttEngine?'No server engine installed — using the browser recognizer.'
      :sttServer?(sttEngine+' — accurate, and it cannot hear itself. Press Space to cut in.')
      :(sttEngine+' available. The browser recognizer is flakier but can be interrupted by voice.');
  }
  (function(){
    const box=document.getElementById('typeAnim');
    const paint=()=>{box.setAttribute('aria-checked',String(typeAnim));box.classList.toggle('on',typeAnim);};
    paint();
    box.onclick=()=>{typeAnim=!typeAnim;try{localStorage.setItem('jarvis_typeanim',typeAnim?'on':'off');}catch(e){}paint();};
  })();
  document.getElementById('sttServer').onclick=()=>{
    if(!sttEngine||NATIVE)return;
    sttServer=!sttServer; sttChosen=true;
    try{localStorage.setItem('jarvis_stt',sttServer?'server':'browser');}catch(e){}
    setSttEngine(sttEngine);
  };
  if(!SR)micBtn.style.display='none';                   // dictation needs Web Speech; voice mode uses the server in NATIVE
  // pick the most human-sounding installed voice (Edge "Natural"/"Online" neural voices)
  let ttsVoice=null;
  function pickVoice(){
    if(!window.speechSynthesis)return;const vs=speechSynthesis.getVoices(); if(!vs.length)return;
    const prefer=['Natural','Online','Aria','Jenny','Ava','Emma','Sonia','Libby','Michelle','Andrew','Guy','Ryan','Google US English'];
    for(const p of prefer){const v=vs.find(x=>x.name.includes(p)&&/^en/i.test(x.lang));if(v){ttsVoice=v;return;}}
    ttsVoice=vs.find(x=>/^en/i.test(x.lang))||vs[0];
  }
  if(window.speechSynthesis)speechSynthesis.onvoiceschanged=pickVoice; pickVoice();
  window.addEventListener('pagehide',()=>{endVoice();stopGen();});
  micBtn.onclick=()=>{if(!SR)return;if(dictating){rec&&rec.stop();return;}rec=new SR();rec.lang='en-US';rec.interimResults=true;dictating=true;micBtn.classList.add('live');const base=ta.value?ta.value+' ':'';rec.onresult=e=>{let t='';for(let i=e.resultIndex;i<e.results.length;i++)t+=e.results[i][0].transcript;ta.value=base+t;auto();};rec.onend=()=>{dictating=false;micBtn.classList.remove('live');};rec.start();};
  voiceBtn.onclick=()=>{if(!SR&&!NATIVE){alert('Speech recognition is not available here.');return;}voiceActive?endVoice():startVoice();};
  vend.onclick=endVoice;
  if(vint)vint.onclick=interruptNow;
  function vSet(st,l){voice.dataset.state=st;vstate.textContent=l;}
  // Voice mode is signalled by a violet theme shift (body.voicing) — no full-screen
  // written overlay. The conversation itself still streams into the chat panel.
  let captureStop=null,activeAudio=null,activeAudioURL=null,voiceGeneration=0;
  function releaseAudio(){if(activeAudio){activeAudio.onended=activeAudio.onerror=null;activeAudio.pause();activeAudio.src='';activeAudio=null;}if(activeAudioURL){URL.revokeObjectURL(activeAudioURL);activeAudioURL=null;}}
  function startVoice(){voiceGeneration++;voiceActive=true;spokenRecent=[];bargeReset();document.body.classList.add('voicing');voiceBtn.classList.add('on');listen();}
  function endVoice(){voiceGeneration++;voiceActive=false;if(captureStop){captureStop();captureStop=null;}releaseAudio();recognizing=false;bargeStop();document.body.classList.remove('voicing');voiceBtn.classList.remove('on');setCore('idle');ttsQueue=[];speaking=false;streamComplete=true;try{rec&&rec.stop();}catch(e){}try{speechSynthesis.cancel();}catch(e){}}
  let recognizing=false, speaking=false;
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
  // What is actually worth saying out loud. A picture, a link target or a code block has
  // nothing speakable in it, and reading a URL aloud used to feed a garbled "slash api
  // slash image question mark name equals…" back into the microphone — which the echo
  // guard could not match, so the agent answered itself and drew again. Mirrors
  // voice.clean_for_speech on the server.
  function speakable(text){
    let t=String(text||'');
    t=t.replace(/```[\s\S]*?```/g,' ');
    t=t.replace(/!\[[^\]]*\]\([^)]*\)/g,' ');       // an embedded picture
    t=t.replace(/\[([^\]]+)\]\([^)]*\)/g,'$1');     // a link reads as its label
    t=t.replace(/(?:https?:\/\/|www\.)\S+/gi,' ');
    t=t.replace(/(^|\s)\/\S*\/\S+/g,' ');           // bare paths like /api/image?name=…
    t=t.replace(/[`*#_>\[\]()|~]+/g,' ');
    return t.replace(/\s+/g,' ').replace(/ ([.,!?;:])/g,'$1').trim();
  }
  // The last few things we actually said. Compared one at a time rather than as one
  // long blob: the old accumulator grew to 600 characters, so its word set matched
  // almost any real sentence and the user's own interruptions were rejected as echo.
  let spokenRecent=[], speechEndedAt=0;
  function rememberSpoken(t){spokenRecent.push(t);if(spokenRecent.length>6)spokenRecent.shift();}
  // reject recognized speech that is really the agent hearing its own voice
  function isEcho(q){
    const norm=s=>s.toLowerCase().replace(/[^a-z0-9 ]/g,'').replace(/\s+/g,' ').trim();
    const a=norm(q); if(!a)return false;
    const aw=a.split(' ');
    for(const spoken of spokenRecent){
      const b=norm(spoken); if(!b)continue;
      if(b.includes(a)||a.includes(b))return true;
      const bw=new Set(b.split(' '));
      if(aw.length>1 && aw.filter(w=>bw.has(w)).length/aw.length>0.7)return true;
    }
    return false;
  }
  // --- barge-in: keep a recognizer alive while J.A.R.V.I.S speaks; if the user says a
  // real phrase (not the TTS echo) it stops talking and answers the new input with the
  // prior context. Best with headphones — open speakers can feed the voice back into
  // the mic. The Interrupt button / Space bar are the always-reliable manual fallback.
  let barge=null, barged=false;
  // Loop breaker. On open speakers the microphone hears the reply, and a mis-classified
  // echo starts a turn that speaks, is heard again, and starts another — which is how a
  // single "draw a city" became four images. Two spoken interruptions inside 25s are
  // allowed; a third means we are hearing ourselves, so spoken barge-in switches off for
  // the rest of the session and the manual Interrupt stays available.
  let bargeOff=false, bargeCount=0, bargeWindow=0;
  function bargeAllowed(){
    const now=performance.now();
    if(now-bargeWindow>25000){bargeWindow=now;bargeCount=0;}
    if(++bargeCount>2){
      bargeOff=true; bargeStop();
      vtrans.textContent='I kept hearing my own voice, so voice interruption is off. Press Space or Interrupt to cut in.';
      return false;
    }
    return true;
  }
  function bargeReset(){bargeOff=false;bargeCount=0;bargeWindow=performance.now();}
  function bargeStop(){if(barge){try{barge.onresult=barge.onerror=barge.onend=null;barge.abort();}catch(e){}barge=null;}}
  function bargeStart(){
    if(!voiceActive||useServerStt()||!SR||bargeOff)return; bargeStop(); barged=false;
    try{barge=new SR();}catch(e){return;}
    barge.lang='en-US';barge.interimResults=true;barge.continuous=true;
    barge.onresult=e=>{if(barged)return;let t='';for(let i=0;i<e.results.length;i++)t+=e.results[i][0].transcript;
      // Three words, not two: "a city" is echo, "add a spaceship to it" is a person.
      const q=t.trim(); if(q.split(/\s+/).filter(Boolean).length>=3 && !isEcho(q) && bargeAllowed()){barged=true;userInterrupt(q);}};
    barge.onerror=()=>{};
    barge.onend=()=>{if(barge&&voiceActive&&speaking&&!barged){try{barge.start();}catch(e){}}};
    try{barge.start();}catch(e){}
  }
  function stopSpeaking(){try{speechSynthesis.cancel();}catch(e){}ttsQueue=[];speaking=false;streamComplete=true;bargeStop();}
  function interruptNow(){if(!voiceActive)return;bargeReset();stopSpeaking();vSet('listening','Listening');listen();}  // manual: stop speaking, listen (and trust the mic again)
  function userInterrupt(q){if(!voiceActive)return;stopSpeaking();if(busy)stopGen();vSet('thinking','Thinking');setTimeout(()=>{if(voiceActive)send(q);},200);}  // spoken barge-in: abort any in-flight turn, then answer with context
  // --- voice timing HUD: marks where each turn spends time so latency is visible ---
  let vT0=0, vMarks=[];
  function vstart(){vT0=performance.now();vMarks=[];}
  function vmark(label){const dt=((performance.now()-vT0)/1000).toFixed(1);vMarks.push(label+' '+dt);const e=document.getElementById('vdbg');if(e)e.textContent='⏱ '+vMarks.join(' · ')+'s';try{console.log('[voice]',label,dt+'s');}catch(_){}}
  function listen(){
    if(useServerStt())return nativeListen();             // record here, transcribe on the server
    if(!voiceActive||recognizing||speaking)return;      // never listen while speaking
    setCore('listening');vSet('listening','Listening');vtrans.textContent='Listening — speak now';
    vstart();
    // continuous + our own silence timer: Chrome's built-in end-of-speech detection can
    // wait many seconds before firing onend, which feels like a long "thinking" pause.
    // We finalize ~1.2s after the user stops talking so the turn snaps to the reply.
    rec=new SR();rec.lang='en-US';rec.interimResults=true;rec.continuous=true;recognizing=true;
    let fin='',heard=false,silence=null,handled=false;
    // Act on the transcript we already have the moment the user pauses, and abort()
    // immediately — waiting for Chrome's stop()/onend stalled ~28s in testing.
    const handle=async(raw)=>{
      if(handled)return; handled=true; recognizing=false; clearTimeout(silence);
      try{rec.abort();}catch(e){}
      if(!voiceActive||speaking)return;
      const q=(raw||'').trim();
      // The tail of an utterance is still in the air right after it ends; anything the
      // recognizer reports in that window is ours, not the user's.
      if(performance.now()-speechEndedAt<400){listen();return;}
      if(q.length<2||isEcho(q)){listen();return;}      // ignore noise, empty, or our own echo
      vSet('thinking','Thinking');
      await send(q);                                    // send() streams sentences back via voiceTurnDone; it drives speech, not us
    };
    const finalize=()=>{vmark('settle');handle(fin||vtrans.textContent);};
    rec.onstart=()=>vmark('mic-on');
    rec.onspeechstart=()=>vmark('speech');
    rec.onresult=e=>{let t='';for(let i=0;i<e.results.length;i++)t+=e.results[i][0].transcript;if(!heard)vmark('heard');heard=true;fin=t;vtrans.textContent=t;clearTimeout(silence);silence=setTimeout(finalize,1000);};
    rec.onerror=(e)=>{const err=(e&&e.error)||'?';vmark('err:'+err);
      if(err==='no-speech'||err==='aborted')return;         // benign — silence timer / restart handles it
      const M={'not-allowed':'Microphone blocked. Allow mic access for this site (click the camera/lock icon by the address bar), then start Voice again.','service-not-allowed':'Microphone is blocked by the browser or OS. Allow mic access, then retry.','audio-capture':'No microphone found. Connect or enable a mic, then retry.','network':'Speech recognition needs an internet connection in this browser, and it appears offline or blocked.'};
      vtrans.textContent=M[err]||('Voice error: '+err);vSet('idle','Voice error');
      if(err==='not-allowed'||err==='service-not-allowed'||err==='audio-capture'){recognizing=false;endVoice();}};
    rec.onend=()=>{vmark('rec-end');if(!handled)handle(fin||(heard?vtrans.textContent:''));};  // fallback only
    try{rec.start();}catch(e){recognizing=false;vtrans.textContent='Could not start the microphone: '+((e&&e.message)||e);vSet('idle','Voice error');}
  }
  // --- native (app-window) voice: record -> /api/transcribe, play /api/tts ---
  // Capture raw PCM and encode a 16kHz mono 16-bit WAV in the browser, so the server
  // can transcribe with a lightweight engine (Vosk) using only the stdlib — no ffmpeg.
  function flattenF32(chunks){let n=0;for(const c of chunks)n+=c.length;const out=new Float32Array(n);let o=0;for(const c of chunks){out.set(c,o);o+=c.length;}return out;}
  function encodeWavB64(samples,inRate){
    const outRate=16000, ratio=inRate/outRate, outLen=Math.max(1,Math.floor(samples.length/ratio));
    const bytes=outLen*2, buf=new ArrayBuffer(44+bytes), dv=new DataView(buf);
    const w=(o,s)=>{for(let i=0;i<s.length;i++)dv.setUint8(o+i,s.charCodeAt(i));};
    w(0,'RIFF');dv.setUint32(4,36+bytes,true);w(8,'WAVE');w(12,'fmt ');dv.setUint32(16,16,true);
    dv.setUint16(20,1,true);dv.setUint16(22,1,true);dv.setUint32(24,outRate,true);dv.setUint32(28,outRate*2,true);dv.setUint16(32,2,true);dv.setUint16(34,16,true);
    w(36,'data');dv.setUint32(40,bytes,true);
    for(let i=0;i<outLen;i++){let s=samples[Math.floor(i*ratio)]||0;s=Math.max(-1,Math.min(1,s));dv.setInt16(44+i*2,s<0?s*0x8000:s*0x7FFF,true);}
    let bin='';const u8=new Uint8Array(buf);for(let i=0;i<u8.length;i++)bin+=String.fromCharCode(u8[i]);
    return 'data:audio/wav;base64,'+btoa(bin);
  }
  async function nativeListen(){
    if(!voiceActive||recognizing||speaking)return;
    setCore('listening');vSet('listening','Listening');vtrans.textContent='Listening — speak now';vstart();recognizing=true;
    const generation=voiceGeneration;
    let stream;
    try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});}
    catch(e){recognizing=false;vSet('idle','Mic blocked');vtrans.textContent='Microphone permission is needed for voice.';return;}
    if(!voiceActive||generation!==voiceGeneration){stream.getTracks().forEach(t=>t.stop());recognizing=false;return;}
    const ac=new (window.AudioContext||window.webkitAudioContext)();
    const srcN=ac.createMediaStreamSource(stream), proc=ac.createScriptProcessor(4096,1,1), sink=ac.createGain();
    sink.gain.value=0;  // route through a muted sink so the graph runs without speaker feedback
    const samples=[]; let spoke=false,lastLoud=performance.now(),stopped=false,t0=performance.now();
    const cleanup=()=>{try{proc.disconnect();}catch(e){}try{srcN.disconnect();}catch(e){}try{ac.close();}catch(e){}stream.getTracks().forEach(t=>t.stop());};
    captureStop=()=>{stopped=true;cleanup();recognizing=false;};
    const finish=async()=>{
      if(stopped)return; stopped=true; cleanup();captureStop=null; recognizing=false; vmark('rec-end');
      if(!voiceActive||speaking)return;
      if(!spoke||!samples.length){listen();return;}
      vSet('thinking','Transcribing…');
      let q='';
      try{const b64=encodeWavB64(flattenF32(samples),ac.sampleRate||48000);
        const r=await fetch('/api/transcribe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({audio:b64,ext:'wav'})});
        const d=await r.json();vmark('stt');q=(d.text||'').trim();
        if(!d.ok&&d.message)vtrans.textContent=d.message;
      }catch(e){}
      if(!voiceActive||generation!==voiceGeneration)return;
      if(q.length<2){listen();return;}
      vtrans.textContent=q;vSet('thinking','Thinking');
      await send(q);
    };
    proc.onaudioprocess=e=>{
      if(stopped)return;
      const ch=e.inputBuffer.getChannelData(0); samples.push(new Float32Array(ch));
      let peak=0;for(let i=0;i<ch.length;i+=8){const v=Math.abs(ch[i]);if(v>peak)peak=v;}
      const now=performance.now();
      if(peak>0.035){if(!spoke){spoke=true;vmark('speech');}lastLoud=now;}
      else if(spoke&&now-lastLoud>1000){vmark('settle');finish();return;}   // ~1s silence after speech
      if(now-t0>12000)finish();                                              // hard cap
    };
    try{srcN.connect(proc);proc.connect(sink);sink.connect(ac.destination);vmark('mic-on');}catch(e){recognizing=false;cleanup();}
  }
  async function playTTS(text){
    const generation=voiceGeneration;
    speaking=true;
    const clean=speakable(text);
    if(!clean){speaking=false;pumpTTS();return;}
    rememberSpoken(clean);
    setCore('speaking');vSet('speaking','Speaking');if(voiceActive)vtrans.textContent=clean.slice(0,240);
    try{
      const r=await fetch('/api/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:clean})});
      if(!r.ok)throw new Error('tts '+r.status);
      const bytes=await r.arrayBuffer();if(!voiceActive||generation!==voiceGeneration)return;
      releaseAudio();activeAudioURL=URL.createObjectURL(new Blob([bytes],{type:'audio/wav'}));const a=activeAudio=new Audio(activeAudioURL);
      a.onended=a.onerror=()=>{releaseAudio();speechEndedAt=performance.now();speaking=false;pumpTTS();};
      vmark('speak');await a.play();
    }catch(e){speaking=false;pumpTTS();}
  }
  function speakChunk(text){
    if(NATIVE)return playTTS(text);   // the app window has no Web Speech API
    try{
    speaking=true; try{rec&&rec.stop();}catch(e){}      // stop the main turn recognizer
    bargeStart();                                       // …but keep a barge recognizer alive so speech can be interrupted
    try{speechSynthesis.resume();}catch(e){}            // defeat Chrome's "paused engine" bug that silently swallows speak()
    if(!ttsVoice)pickVoice();
    const clean=speakable(text);
    if(!clean){speaking=false;pumpTTS();return;}
    rememberSpoken(clean);
    const u=new SpeechSynthesisUtterance(clean.slice(0,800));if(ttsVoice)u.voice=ttsVoice;u.rate=1.0;u.pitch=1.0;
    setCore('speaking');vSet('speaking','Speaking');
    if(voiceActive)vtrans.textContent=clean.slice(0,240);                  // static, readable subtitles
    u.onstart=()=>vmark('speak');
    u.onboundary=(e)=>{if(voiceActive&&e.charIndex!=null){const end=e.charIndex+(e.charLength||0);const start=Math.max(0,end-240);vtrans.textContent=(start>0?'…':'')+clean.slice(start,start+240);}};
    u.onend=()=>{if(barged)return;bargeStop();speechEndedAt=performance.now();speaking=false;pumpTTS();};   // next sentence, or resume listening when the queue drains
    u.onerror=()=>{if(barged)return;bargeStop();speechEndedAt=performance.now();speaking=false;pumpTTS();};
    speechSynthesis.speak(u);
  }catch(e){bargeStop();speaking=false;pumpTTS();}}

  renderSessions(); ta.focus();
  document.querySelectorAll('textarea,input,select,button').forEach(el=>{
    if(!el.getAttribute('aria-label')&&!el.labels?.length){const name=el.title||el.placeholder||el.textContent.trim()||el.id;if(name)el.setAttribute('aria-label',name);}
  });
</script>
</body>
</html>
"""
