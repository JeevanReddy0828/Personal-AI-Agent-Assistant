# Packaging J.A.R.V.I.S as a desktop app

## ANALYTICS-04 update — 2026-10-01

ANALYTICS-04 uses only stdlib in analytics/diagnostics.py. No new bundled asset, provider, DLL or optional dependency is needed. Preserve null diagnostics and warnings in later consumers; this slice adds no packaged command.


This turns the agent into a **standalone Windows application** the user can
download and double-click — no Python install, no terminal, no browser tab. It
opens the frameless desktop window directly.

## Build

```powershell
# from the repo root
pip install pyinstaller pywebview pyttsx3
./packaging/build_app.ps1
```

Output: `dist/JARVIS.exe` (a single windowless executable).

## The window

The app opens a **true native window** via pywebview (no Edge browser, its own
taskbar entry). On Windows it renders through the WebView2 runtime, which ships
with Windows 11. If pywebview isn't bundled, it falls back to a frameless
Chrome/Edge `--app` window.

## Voice

Because WebView2 has no Web Speech API, the native window does voice **server-side**:
it records the mic, transcribes via `/api/transcribe`, and plays replies from
`/api/tts`.

- **TTS** works out of the box (offline `pyttsx3`, bundled).
- **STT** has two engines, picked by `LAPTOP_AGENT_STT` (`auto` default):
  - **Vosk (lightweight, recommended for distribution)** — ~50MB model, **no PyTorch,
    no ffmpeg**. `pip install vosk`, download a small model from
    https://alphacephei.com/vosk/models and unzip it into a `models\` folder (or set
    `VOSK_MODEL`). Build with **`build_app_small.ps1`** — a fraction of the Whisper size.
  - **Whisper (accurate, heavy)** — `pip install openai-whisper`, needs ffmpeg on PATH;
    pulls in PyTorch (multi-GB). Build with `build_app.ps1`.
  - `auto` prefers Vosk when a model is present, else falls back to Whisper.
  - Without any engine, voice output still speaks but voice *input* returns an install hint.

## What the user needs

- **WebView2 runtime** (preinstalled on Windows 11; the app falls back to Edge/Chrome
  `--app` otherwise).
- A **`.env` file next to `JARVIS.exe`** with their model credentials, e.g.:
  ```
  OPENAI_API_KEY=...
  OPENAI_MODEL=meta/llama-3.1-8b-instruct
  OPENAI_BASE_URL=https://integrate.api.nvidia.com/v1
  ```
  `jarvis_app.py` loads this `.env` (and `config.py` also auto-loads `.env`).

## Run

Double-click `JARVIS.exe`. The engine starts in the background and the J.A.R.V.I.S
window opens. Closing the window quits the app.

## Notes

- The build is per-OS: build on Windows for a Windows `.exe`, on macOS for a macOS
  app, etc. PyInstaller is not a cross-compiler.
- `dist/` and `build/` are gitignored — the executable is a build artifact, not
  committed to the repo.
- Voice still uses the browser's Web Speech API inside the app window; in
  environments where that engine is slow, prefer a server-side transcription
  backend (the `transcribe` extra) as a follow-up.

## Voice recording update (REC-01, 2026-09-28)

The current page supports requested voice notes through browser MediaDevices/Web Audio
and the existing PCM WAV encoder; it does not require Web Speech recognition to record.
`record 20` opens a visible countdown. Stop/Space keeps the partial WAV, saved beneath
`data_dir/recordings`, and the chat offers playback, Save WAV and optional transcription.
Transcription uses the configured backend, which may be hosted. This supplements the
older voice-engine note above; recording adds no Python dependency. Restart the server
or rebuild the package after page-asset changes. Validate permission, capture, playback
and Stop on the physical microphone in the packaged native window before release.

## Hosted speech wait bound (VOICE-03, 2026-09-28)

The optional Riva SDK is unchanged. Its async future is used to bound recognition waits
and cancel the RPC when Stop or the deadline occurs. The default scales with WAV duration
from 10 to 120 seconds; `RIVA_ASR_TIMEOUT_SECONDS` overrides it with a finite value in
(0,600]. Auto mode then tries its local engine; an explicit Riva selection reports failure.
This does not bound local-model loading or offline transcription. Rebuild the app after
runtime changes; test a real configured provider separately from the synthetic SDK checks.

### Google identity in a packaged app

AUTH-01 phase 2a adds `webui_assets/google_auth.js`; keep packaging the whole asset directory
(the existing wildcard already includes it). The helper is inlined into the sign-in and
main documents with the existing CSP nonce. No extra Python runtime dependency is added.

Configure the owner's Google OAuth Desktop client with GOOGLE_CLIENT_ID and
GOOGLE_CLIENT_SECRET. Sign-in derives its 127.0.0.1 callback from the app's listening port;
GOOGLE_REDIRECT_URI is still only the older email command's setting. The native window
opens the system browser and polls with its own HttpOnly proof cookie. Only that original
window receives the app session. Test real consent and a return to the packaged window
before distribution; automated checks use two independent Chromium cookie jars and a fake
provider. Google sign-in grants no Gmail access in this slice.


### GPU counters without administrator rights (GPU-01)

Rebuild the executable after the metrics runtime change. No new Python package or bundled
DLL is needed: Windows supplies PowerShell performance counters and DXGI. NVIDIA's utility
remains preferred when it works; counters are the non-elevated fallback. The subprocess
has no console window and runs behind the metrics cache, so the status drawer can return
its previous values while sampling. The first read can be temporarily unavailable.

The fallback reports 3D utilization and dedicated memory usage; an unmatched or powered-down
adapter may have a generic name and unknown memory capacity. Missing or localized counter
sets degrade to unavailable metrics with one failure record per cause. Check a rebuilt
native window without elevation before distribution; the source HTTP endpoint and DXGI
path were verified non-elevated on the laptop on 2026-10-01.


### GPU-01 review follow-up (2026-10-01)

GPU review follow-up also updates app.js: rebuild to get the explicit 3D label and
unknown VRAM display. One-shot status/briefing calls wait for a fresh measurement.
