# Build the standalone J.A.R.V.I.S desktop application (Windows .exe).
#
# Usage (from the repo root):
#   pip install pyinstaller
#   ./packaging/build_app.ps1
#
# Produces dist/JARVIS.exe — a single windowless executable. The end user runs it
# directly (no Python needed) and gets the frameless desktop app window. They must
# have Chrome or Edge installed (used to render the window) and a .env with their
# OPENAI_* keys placed next to the .exe.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# pywebview (native window), pyttsx3 (offline TTS), and whisper (STT) are imported
# lazily inside functions, so PyInstaller can't see them by static analysis — collect
# them explicitly. Whisper pulls in PyTorch, so the bundle is large (multi-GB) and the
# build is slow; drop the whisper/torch lines if you don't need in-app voice input.
# ffmpeg must be on the end user's PATH for Whisper to decode recorded audio.
python -m PyInstaller `
    --noconfirm --clean --onefile --noconsole `
    --name JARVIS `
    --paths "$root\src" `
    --collect-submodules laptop_agent `
    --collect-all webview `
    --collect-all pyttsx3 `
    --hidden-import pyttsx3.drivers `
    --hidden-import pyttsx3.drivers.sapi5 `
    --collect-all whisper `
    --collect-all torch `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\packaging\jarvis_app.py"

Write-Host ""
Write-Host "Built: $root\dist\JARVIS.exe"
Write-Host "Place a .env (with OPENAI_API_KEY etc.) next to the .exe, then double-click it."
