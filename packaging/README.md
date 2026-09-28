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
- **STT** is selected by `LAPTOP_AGENT_STT` (`auto` default), with optional hosted
  Parakeet through Riva and two local engines:
  - **Riva / Parakeet (hosted, optional)** — requires the `riva` extra and a key.
    A function id is built in; `RIVA_ASR_FUNCTION_ID` overrides it. The key is selected
    from `RIVA_API_KEY`, then `NVIDIA_API_KEY`, then `OPENAI_API_KEY`, then the app's
    configured model key. With the client installed and a normal chat key, `auto`
    attempts to send WAV audio to NVIDIA without any Riva-specific setting.
    Pin `LAPTOP_AGENT_STT=vosk` or `whisper` for local transcription.
  - **Vosk (lightweight, recommended for distribution)** — ~50MB model, **no PyTorch,
    no ffmpeg**. `pip install vosk`, download a small model from
    https://alphacephei.com/vosk/models and unzip it into a `models\` folder (or set
    `VOSK_MODEL`). Build with **`build_app_small.ps1`** — a fraction of the Whisper size.
  - **Whisper (accurate, heavy)** — `pip install openai-whisper`, needs ffmpeg on PATH;
    pulls in PyTorch (multi-GB). Build with `build_app.ps1`.
  - For WAV input, `auto` first tries Riva when configured and available. If that call
    fails, or for other media, it selects Vosk when a model is present, else Whisper.
    The small bundle does not include Whisper; a usable Vosk model is required for
    offline speech input. Neither build script explicitly bundles `riva` or `grpc`;
    hosted speech in the packaged executable is unverified.
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
- The native pywebview window uses server-side speech (`/api/transcribe` and
  `/api/tts`), selected by `?app=1`; it does not depend on Web Speech recognition.
  A regular browser also defaults to server speech when the server reports an engine
  and no preference has been saved; an explicit browser/server choice is retained.
  Validate microphone permission, the selected engine and speech playback in the
  actual packaged build.
- Automatic Riva failures are recorded and fall back to Vosk when available, otherwise
  Whisper. Pinning `LAPTOP_AGENT_STT=riva` reports a cloud failure instead of switching
  engines. The current Riva call has no application deadline: a stalled connection can
  delay fallback until the call raises. Timeout policy is separate follow-up work.
