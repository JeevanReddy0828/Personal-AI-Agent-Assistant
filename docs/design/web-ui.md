# The page

Rendering, design tokens, the orb, the desktop window, secure-context rules and the routed pages.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

**Maths is rendered, not printed raw.** `\( … \)`, `\[ … \]` and `$$ … $$` go through
`mathToHtml`: `\frac` becomes a stacked fraction, `^`/`_` become scripts (Unicode where it
exists, `<sup>`/`<sub>` otherwise), and a symbol table covers the operators and Greek that
chat arithmetic uses. No KaTeX or MathJax, for the same CSP reason as the diagrams. A
division once showed as literal `\[ \frac{754}{86982} \approx 0.008668 \]`.
Conversion is **confined to the delimiters on purpose**: applying it to bare prose would
eat a Windows path like `C:\new\table`, so the chat prompt asks the model to delimit
instead, and undelimited LaTeX is shown as typed.

**Diagrams are drawn, not described.** A fenced ```mermaid block is rendered to inline SVG
by `mermaidSvg` in `webui_page.py` — not the Mermaid library: the CSP is
`script-src 'nonce-…'` with **no `'self'`**, so no extra script can load, and vendoring
3MB would fight the same "no chart CDN, offline-friendly" rule the Overview charts follow.
It covers the two shapes that actually come up — `erDiagram` (entity boxes, columns,
labelled relationships) and `flowchart`/`graph`/`stateDiagram` (nodes and labelled edges) —
and anything else falls back to a readable code block rather than vanishing. Flow layering
is breadth-first **from the entry point, ignoring back-edges**: longest-path layering put
TCP's Slow Start at the bottom once the timeout edge closed the cycle. Markdown images are
restricted to same-origin paths — a model sent a fabricated `data:image/png;base64` blob.

**Nothing in the page may assume a secure context.** `http://<ip>` is not one, so the
browser removes `crypto.randomUUID`, `navigator.clipboard` and `navigator.mediaDevices`
outright. `send()` called `crypto.randomUUID()` on its first line, threw
`TypeError: crypto.randomUUID is not a function`, and the send button did nothing at all —
no request, no error, no clue — which is exactly how it was reported. `uuid()` falls back
to `crypto.getRandomValues` (which *is* available on http) and `copyText()` to the
`execCommand('copy')` selection trick; use those, never the originals. Note the test trap:
`randomUUID` lives on `Crypto.prototype`, so `delete crypto.randomUUID` does nothing and a
guard written that way passes against the bug — shadow it on the instance with
`Object.defineProperty`.

Voice still will not work: `getUserMedia` has no fallback, only HTTPS or `localhost`
qualify, and a self-signed certificate is not enough for the microphone. The failure now
says so instead of blaming permissions, which sent people to a settings screen that cannot
fix it. And both HTML pages are
sent `Cache-Control: no-store`, because they carry a per-process script nonce: a cached
copy outlives the process, and after a restart every script on the page is silently
blocked by the CSP — the unlock form simply stopped responding to Enter, with nothing in
the console but the request that never happened.

The desktop window prefers a true native **pywebview** window (`app` extra; no
Edge browser, its own taskbar entry) and falls back to a frameless Chrome/Edge
`--app` window when pywebview is absent. Because Edge WebView2 (pywebview's
Windows backend) ships no Web Speech API, the native window does voice
**server-side**: it sets `?app=1`, records the mic, transcribes via `/api/transcribe`
(local `TranscribeTool`/Whisper), and plays sentences from `/api/tts` (offline
pyttsx3). The Chrome/Edge fallback still uses the in-browser Web Speech API.
`packaging/` bundles all this into a standalone `JARVIS.exe` via PyInstaller.

**The page lives in `src/laptop_agent/webui_assets/` as `app.html` (15KB), `app.css` (47KB)
and `app.js` (121KB).** `webui_page.py` is now a 75-line loader that stitches them together
into `PAGE` at import (it was a 2529-line module holding all of it as one raw string, where
nothing could lint or highlight it and a stray backslash in a regex was indistinguishable
from a deliberate escape — a mistake that has cost real time here). The extraction was
verified **byte-identical** against a snapshot of the old string, which is the whole safety
argument for the refactor.

It is still served as **one inlined document** — that is deliberate, not unfinished work.
The CSP is `script-src 'nonce-…'` with no `'self'`, so a `<script src>` would be blocked
outright, and a linked stylesheet would need `style-src 'self'`; a test fails if someone
"completes" the split by linking them. So this is a source-level split only: the bytes on
the wire are unchanged.

Two things it added, both already trodden on once in this repo: a packaged build needs
`--add-data` for `webui_assets` (both `packaging/*.ps1` carry it, and `_asset_dir()` checks
`sys._MEIPASS` as well as beside the module — the same trap that hid the bundled Vosk
model), and a wheel needs `[tool.setuptools.package-data]`. The source-integrity guard now
scans `*.js`/`*.css`/`*.html` under `src/` too, since that is where the regex-heavy code
lives now.

The web UI (`PAGE`; `webui.py` keeps the server and routes
and imports it, and the server reads it at import, so CSS/JS
edits need a restart) is a calm dark workspace: a slim left rail (New chat, recent
conversations, a status row), an assistant-presence panel holding the animated particle
**orb** — the only glowing element; `setCore` stamps `body[data-core]` so the ambient
glow behind it brightens while listening/thinking/speaking — and a wide, quiet
conversation column with a rounded composer (attach · agent mode · text · dictate ·
**Voice** pill · send). Models, GPU/CPU/memory, the memory-vault browser and the tool
panels (tool activity, scheduled jobs, agent runs, map, trip planner) live in a
right-hand **System status** drawer (`#sysDrawer`, opened from the header status pill or
the rail footer; Esc closes). Design tokens are the CSS variables at the top of the
`<style>` block: one cyan accent for interactive/active states, green only for healthy or
positive status (health dots, high ATS scores), sans-serif body type (Segoe UI Variable → system stack; the CSP is
`font-src 'self'`, so no web fonts), monospace reserved for model names, timings and
diagnostics, 150–250 ms motion that honours `prefers-reduced-motion`. Third-party CSS
(e.g. uiverse.io elements, MIT) is **adapted, never pasted**: re-express its colours as
the tokens, drop any glow so the orb stays the only glowing element, size it for the
surface it lands on, and credit the author in a comment above the rule. Tailwind
variants are unusable here — no Tailwind, and the CSP blocks CDNs. Browser
regression tests depend on these ids/classes: `#nav [data-view]`, `#ta`, `#newChat`,
`#mobileChats`, `.scard`, `.msg`, `#rsContact`/`#rsCerts`/`#rsProfileSave`, `#pipeMsg`,
`#orbBtn`/`#orbFocusSw`/`#orbVoiceBtn`, `#vmeter`, `#core`.

The header gear popover holds the **adaptive-HUD** settings: a compact-layout toggle
(chat only — hides the rail and the presence panel), its mirror image **Focus the orb**
(orb only — hides the chat and the rail; also a button in the header, and Esc comes back),
an always-on-top switch and a transparency slider — all persisted in `localStorage`.

**Orb focus animates the sphere, not the layout.** The obvious implementation — transition
`grid-template-columns` — does not work: measured in a real page, the stage jumped 374px to
1440px in a single frame with a 500ms transition sitting on it, and every sampled frame read
the end value. So the layout snaps and the **canvas** does the animation. `focus` eases 0..1
over `--focus-ms` (CSS owns that number; `app.js` reads it, so the two cannot drift), and
`drawSphere` interpolates the sphere's **centre and radius** from the docked rect to the
window's. `dockRect()` measures the docked position by taking the class off and putting it
back inside one synchronous block, so nothing is painted in between and it stays correct
after a resize.

**Orb focus needs its own voice control.** It hides the whole chat column
(`body.orbfocus main.chatcol{opacity:0;pointer-events:none}`) and the Voice pill lives in
the composer, so voice could not be **started** while the orb was focused — a click at the
pill's own coordinates landed on `#core`. `#orbVoiceBtn` sits in `.stagedock`, the one
surface orb focus leaves standing, and toggles both ways rather than handing off to the
`.voice` panel's End voice / Interrupt, which are not on screen to hand off to (see the
voice section). It shares one click handler and one `paintVoiceButtons` with the composer
pill — the availability check (`!SR && !NATIVE`) is the part that must not be duplicated,
and both surfaces must show the same state, since switching view or leaving focus swaps
which one is visible mid-session.

**Two classes, and the split is what makes leaving smooth.** `orbstage` is the mechanism —
the stage as a fixed overlay — and must stay until the sphere has finished shrinking.
`orbfocus` is the **intent**, and flips on the click in both directions, so the chat and
the ambient glow move *with* the orb. Carrying both on one class meant leaving cost 1100ms
against 500ms to enter, with the chat still invisible for the first 520ms; and the glow,
sized as a percentage of a `.stage` whose box changes when the overlay drops, snapped
760px to 248px in a single frame. `.stage::before` is therefore sized off `--presence-w`
and `vw`, **never a percentage of `.stage`**. Measured after: 500ms each way, and the glow
reaches its docked 307px before the overlay is released. Three things learned by breaking them: **a focused orb needs more points AND bigger ones**
(measured at 1440x900: the focused sphere is 2.5x wider, so 6.7x the surface area. Scaling
the dots alone magnifies a point cloud but cannot restore the docked glow, which comes from
dots OVERLAPPING under `lighter` compositing; tripling the count alone leaves them small and
the golden-angle spiral visibly bands when subsampled. So `NP` is 2280, every third point is
the docked sphere and the rest fade in with `focus`, and `magnify` scales each dot by the
real ratio `R/(dockSpan*ORB_R)`. The docked orb still draws exactly its original 760); landing the layout must **not**
depend on a frame being drawn, because `requestAnimationFrame` is throttled to nothing when
the window is occluded (measured in an embedded pane: 0 frames in 300ms with
`visibilityState` still `'visible'`), so a `setTimeout` finishes it or the class sticks on
with the chat at `opacity:0` and no way back; and switching to a view that hides the stage
has to land it **immediately** for the same reason — the loop stops, so the easing never
would. `reduced_motion` takes the instant path by design, which is why the orb-focus tests
build their own Playwright context: the shared one is `reduced_motion="reduce"`. Real window effects
(alpha + topmost) run via `window_fx.apply_window_effects` (Windows `ctypes`,
targeting only a top-level window owned by *our own* process AND titled J.A.R.V.I.S
— so a same-named third-party app is never touched; graceful no-op elsewhere)
behind a desktop-gated `/api/window`
POST (`_DESKTOP_MODE`, set only by `run_desktop`, so a normal browser is never
touched). In a browser the slider still fades the app visually via CSS.

The web app is now **multi-page**: a header nav + hash router (`#/chat`, `#/overview`,
`#/jobs`, `#/pipeline`) toggles `body[data-view]` to swap full-width routed pages (Chat
stays default). **The nav shows only Chat and Overview** — Jobs and Pipeline keep their
pages, routes and APIs and stay reachable by hash, but have no buttons (the nav version is
preserved on `feature/jobs-pipeline-nav`), so browser tests drive those two views through
`location.hash` rather than a click. The **Overview** and **Job Tracker** pages render stat cards + **inline-SVG
charts** (funnel, apps/week — no chart CDN, offline-friendly) from `/api/jobs`/`/api/health`/
`/api/metrics`; the Job Tracker page adds/edits applications and changes stage inline.

The **Pipeline** page (`#/pipeline`, `/api/pipeline`) is the live job-search board: a
base-resume panel (paste text or load a PDF/DOCX/TXT path), a **Pull from Jobright** button,
a **Clear leads** button, a stage board (lead → applied → … → offer) whose cards show a
live **ATS score** (local, no LLM) and per-job **Tailor** → grounded one-page resume, then
**PDF** (download via `/api/resume-pdf?id=`) + **Preview** (inline iframe). Tailoring runs
on-demand through the resume CoPilot; PDFs render via Chromium under `data_dir/resumes/`.
