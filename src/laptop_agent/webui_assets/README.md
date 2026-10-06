# `webui_assets/` — the web page itself

## Purpose

The HTML, CSS and JavaScript of the J.A.R.V.I.S web app: the chat column and composer, the
animated orb, voice mode, approval cards, the System status drawer, the Overview, Jobs and
Pipeline pages, and the sign-in page. The same page runs in a browser tab, in the desktop
window and on a phone in LAN mode.

## How it connects

```
 app.html  ──┐
 app.css   ──┼─ webui_page.build_page() stitches them into ONE document at import:
 google_auth.js + app.js ┘   {{STYLE}} ← app.css, {{SCRIPT}} ← google_auth.js + app.js
                                  │
                                  ▼
 webui.py  _rendered_page() fills {{NONCE}}, {{API_TOKEN}}, {{PLANNER}} … once per
           process, sends it with an ETag, and serves every /api/... call the page makes
                                  │
 signin.html (+ google_auth.js) ──┘  sent instead of the app to anyone not signed in,
                                     so the API token never reaches them
```

The page is deliberately **one inlined document**. The Content Security Policy allows only
scripts carrying the per-process nonce (`script-src 'nonce-…'`, no `'self'`), so a
`<script src="app.js">` would be blocked. The split into files is for editing, linting and
review only; the browser still receives a single document.

## Usage

- Edit the files here, then **restart the server** (`python -m laptop_agent.webui`): the
  page is read once at import.
- Do not link these files from the HTML; a test fails if you do (see above).
- Do not assume a secure context: on `http://<laptop-ip>` the browser removes
  `crypto.randomUUID`, `navigator.clipboard` and the microphone. Use the page's own
  `uuid()` and `copyText()` helpers.
- Browser tests look elements up by id (`#ta`, `#newChat`, `#core`, `#vmeter`,
  `#orbVoiceBtn` …); keep those ids when restyling.
- Packaging copies this folder whole (`--add-data` in `packaging/*.ps1`, `package-data` in
  `pyproject.toml`), so a new asset file ships automatically. Only files `webui_page.py`
  reads by name are used.

## Contents

| File | What it does |
|---|---|
| `app.html` | The page skeleton: header and nav, the conversation rail, the orb stage and its dock, the chat column and composer, the System status drawer, the Overview / Jobs / Pipeline views, settings popover. |
| `app.css` | All styling. Design tokens (colours, spacing, motion) are the CSS variables at the top; dark theme, one cyan accent, motion that honours reduced-motion. |
| `app.js` | Everything that runs: sending and streaming chat, Markdown/maths/diagram rendering, approval cards, voice mode and barge-in, the orb animation, reminders tray, the panels and charts, sign-in state. |
| `google_auth.js` | The small "Sign in with Google" client shared by the app and the sign-in page. |
| `signin.html` | The sign-in page. Once any account exists, it is what a visitor without a session receives instead of the app. |

## Read next

- `docs/design/voice.md` (voice, barge-in, the dock) and `docs/design/web-ui.md` (orb focus,
  maths and diagrams, "Nothing in the page may assume a secure context").
- Tests: `test_page_assets.py`, `test_page_integrity.py`, `test_webui*.py`, and the opt-in
  browser suites `test_browser_*.py` (`JARVIS_BROWSER_TESTS=1`).
