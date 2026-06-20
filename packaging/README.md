# Packaging J.A.R.V.I.S as a desktop app

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
