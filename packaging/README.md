# Packaging J.A.R.V.I.S as a desktop app

This turns the agent into a **standalone Windows application** the user can
download and double-click — no Python install, no terminal, no browser tab. It
opens the frameless desktop window directly.

## Build

```powershell
# from the repo root
pip install pyinstaller
./packaging/build_app.ps1
```

Output: `dist/JARVIS.exe` (a single windowless executable).

## What the user needs

- **Chrome or Edge** installed. The app uses the system browser engine to render
  its window (kept out of the bundle to stay small and to keep the Web Speech API
  available for voice). Edge ships with Windows, so this is normally already met.
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
