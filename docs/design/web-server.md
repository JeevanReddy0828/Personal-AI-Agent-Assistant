# The web server

Routes, caching, ports, LAN mode, the request body rule and the Setup report.

Moved unchanged from `CLAUDE.md` on 2026-10-06, when CLAUDE.md was cut to its core. Section headings below are the original ones.

---

Routing uses few-shot **message turns** for reliability. The 8B alone won't route
without them. The web app (`webui.py`) streams chat via `/api/stream` (SSE) —
which, when the request sets `voice:true`, also emits incremental `tts` sentence
events (carved by `voice.SpeechChunker`) so the browser voice loop starts speaking
the first sentence before generation finishes — streams autonomous-agent traces
via `/api/agent`, exposes `/api/health`, serves a
Scheduled-jobs panel via `/api/schedule` (GET lists jobs; POST add/remove/enable/
disable, routed through the same `schedule …` orchestrator commands), exposes
read-only autonomous-agent run history via `/api/agent-runs`, serves a Map panel
via `/api/map` (POST a place or `A to B` -> OpenStreetMap embed/bbox/directions,
routed through the `map …` orchestrator command), serves a memory-vault browser
via `/api/notes` (POST `read` -> Markdown + outlinks/backlinks, or `search`;
rendered in a click-through note-viewer overlay with wiki-link chips), serves a
Trip-planner panel via `/api/trip` (POST `stops[]` -> per-leg breakdown + totals +
route geometry/bbox + multi-waypoint directions, routed through `trip …`; the panel
adds/reorders stops and draws the route as an inline SVG), serves server-side
voice for the native window (`/api/transcribe` STT, `/api/tts` offline TTS), and runs a 60s
background `_schedule_ticker` for due scheduled jobs; it keeps the model warm to
avoid cold-start latency. Chat replies stream token-by-token; instant local command
results (which arrive whole) are revealed with a JS `typewriter()` pass so both feel
alive — skipped for >4k-char output and cancelled by Stop/Esc. Every render point goes
through `setMd(el, text)` (render + `decorate()`), so a generated picture gets a **Save**
control and a table gets **Copy**/**CSV**; both are built as DOM nodes, never markup, so
nothing model-authored is interpolated into HTML. Chats in the rail have a delete control,
and **Incognito chat** creates a session `saveSessions()` filters out of `localStorage` on
both the normal and the over-quota retry path.

**Two instances must never share a port.** `allow_reuse_address` is needed so TIME_WAIT
does not block a restart, but on Windows it also lets a second process bind a port that is
already being served. Two J.A.R.V.I.S ran at once, which one answered a request was luck,
and because they hold separate approval state and LAN passcode sessions it presented as
random flakiness (a phone unlocking, then being asked again). This happened twice in one
session. `_refuse_if_running()` probes the port at both entry points and exits with a
message naming `LAPTOP_AGENT_PORT`.

**A rejected POST must have its body read before it is answered.** Every rejecting path -
403 untrusted, 401 locked, 404 unknown path, 429 too many attempts - used to answer without
touching the body the client had already sent, and closing a socket that still holds unread
data makes the OS reset the connection: the client's pending read fails instead of seeing
the status. Measured on Windows, a 1MB POST to an unknown path raised
`ConnectionAbortedError` **[WinError 10053] 6 times in 12**, and a bad token 3 in 12; a
2-byte body never tripped it locally, so it only ever surfaced as an intermittently red CI
test. `_drain_request_body()` runs at the single `_send` choke point and counts **bytes
read, not a boolean** - `_pair` reads only the first 4096 bytes of a passcode POST, and a
flag would call the rest consumed and reset exactly the path a phone uses to be told
"Wrong passcode.". It is capped at `MAX_REQUEST_BYTES`, times out at 5s so a body that
never arrives cannot hold a thread, and records to `failures.py` rather than swallowing.

**The page is rendered once and revalidated, not resent.** It is 179KB and every
placeholder is fixed for the life of the process, yet it was re-rendered and sent in full
on every load — and `Cache-Control: no-store` (added so a cached copy could not outlive its
script nonce) made that unavoidable. `_rendered_page()` builds it once with an ETag over
the bytes; the route answers `If-None-Match` with a 304. The ETag still changes on restart,
which is exactly when the cached copy stops working.

**That saved nothing at all until #121, and the measurement is why nobody noticed.**
`end_headers` sent `Cache-Control: no-store` on **every** response, on top of whatever the
route had chosen, so the page went out with two Cache-Control headers. Folded, `no-store`
wins — and a browser forbidden to *store* the page has nothing to revalidate, so it never
sends `If-None-Match` and the 304 can never fire. "183,536 bytes -> 0" was measured with
curl passing the ETag by hand, which proves the server answers a conditional request and
says nothing about whether a browser ever makes one. Measured in Chromium on a warm
reload: no `If-None-Match`, 200, the full 196KB, every time. The same blanket also ate
`private, max-age=86400` on `/api/image`, so every generated picture was re-fetched on
every render. **Measure the thing the user's client actually does, not the thing your
tool can be told to do.** A response now picks its caching through `_cache()` and
`end_headers` fills in `no-store` only when nothing did, so the safe default still covers
every dynamic API answer.

**Everything that opts out of `no-store` says `private`.** The page embeds the
per-process API token — shell, files and mail on this laptop — and the two SSE streams
carry the conversation. None of them were storable by anything while the blanket was
winning, so `no-cache` alone cost nothing; the moment the route's own choice took effect
it became a real exposure, on plain HTTP, with a phone on the same wifi.
`test_nothing_user_specific_is_offered_to_a_shared_cache` reads the `_cache()` call sites
out of the source rather than listing routes, so an opt-out added later is already
covered.

**Reaching it from a phone (`LAN_MODE`).** The app refused any bind but loopback, and
`_trusted_request` refused any Host but loopback, so a phone got a connection refused or a
403 — measured: `Host: localhost:8770` 200, `Host: 192.168.4.68:8770` 403. Both now open
**only together with a passcode**, because the page carries the API token and that token is
shell, files and mail on this laptop:

```powershell
$env:LAPTOP_AGENT_HOST="0.0.0.0"; $env:LAPTOP_AGENT_LAN_PASSCODE="something-long"
python -m laptop_agent.webui        # then http://<laptop-ip>:8770 on the phone
```

A bind outside loopback without an 8+ character passcode raises at import rather than
starting. Any client that is not this machine gets a lock screen (deliberately plain — it
must not say what it guards), exchanges the passcode at `/api/pair` for an HttpOnly
`SameSite=Strict` session cookie held **in the process** (a restart re-asks), and is rate
limited to 10 attempts with a 1s delay each. `/api/pair` is the one endpoint that runs
before the API-token check, since a new device cannot have the token until it has the page.
In LAN mode the Host may be **an IP literal only, never a name** (`_is_address_literal`):
DNS rebinding needs a domain the attacker controls, so refusing names is what makes
widening this safe. Loopback keeps its old behaviour and is never asked for a passcode.

**Setup says what is on and what to do next** (`health.setup_report`, `GET /api/setup`, the
Setup panel in the System status drawer). One row per capability: `ready`, `off` (optional,
not set up), `missing` (a package or engine it needs is absent), `busy` (a tier loaded or
unreachable) or `broken` (a tier misconfigured, with its reason), and for anything not
ready the next step as an environment variable *name* or an install command, never a value,
a path or a model id (a test puts secrets in every config field and asserts none reach the
report). Offline and cheap: packages are checked with `find_spec` and programs with `which`,
both injected, so nothing heavy is imported and nothing goes over the network; Tesseract's
package without its program counts as `missing`, since the engine probe only checks the
package. Two rules from Codex's review: Playwright is ready only when the Chromium revision
its own `browsers.json` names is in its browsers directory, finished (its `INSTALLATION_COMPLETE`
marker and a browser executable inside: an interrupted install leaves the folders empty) — the
package alone said ready with no browser, and an upgrade leaves the old revision behind — and a
broken tier's advice is
rebuilt from the HTTP status, never passed through, because the stored reason names the
model id. Developer-only by the route allow-list and `.devonly`.
