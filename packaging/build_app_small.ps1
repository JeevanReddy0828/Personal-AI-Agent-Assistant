# Build a SMALL standalone J.A.R.V.I.S app that uses Vosk for speech-to-text
# (~50MB model, no PyTorch, no ffmpeg) instead of Whisper. The result is a fraction
# of the size of build_app.ps1's Whisper bundle.
#
# Usage (from the repo root):
#   pip install pyinstaller pywebview pyttsx3 vosk
#   # download a small model and unzip it into  models\  e.g.:
#   #   https://alphacephei.com/vosk/models  ->  models\vosk-model-small-en-us-0.15\
#   ./packaging/build_app_small.ps1
#
# The app auto-detects the model in models\ (or set VOSK_MODEL). Set
# LAPTOP_AGENT_STT=vosk to force it; 'auto' (default) already prefers Vosk when a
# model is present.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$addModel = @()
$models = Join-Path $root "models"
if (Test-Path $models) { $addModel = @("--add-data", "$models;models") }

python -m PyInstaller `
    --noconfirm --clean --onefile --noconsole `
    --name JARVIS `
    --paths "$root\src" `
    --collect-submodules laptop_agent `
    --collect-all webview `
    --collect-all pyttsx3 `
    --hidden-import pyttsx3.drivers `
    --hidden-import pyttsx3.drivers.sapi5 `
    --collect-all vosk `
    @addModel `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\packaging\jarvis_app.py"

Write-Host ""
Write-Host "Built (small / Vosk STT): $root\dist\JARVIS.exe"
Write-Host "No PyTorch, no ffmpeg. Put a .env next to the .exe and run it."
