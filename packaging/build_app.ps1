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

python -m PyInstaller `
    --noconfirm --clean --onefile --noconsole `
    --name JARVIS `
    --paths "$root\src" `
    --collect-submodules laptop_agent `
    --distpath "$root\dist" `
    --workpath "$root\build" `
    --specpath "$root\build" `
    "$root\packaging\jarvis_app.py"

Write-Host ""
Write-Host "Built: $root\dist\JARVIS.exe"
Write-Host "Place a .env (with OPENAI_API_KEY etc.) next to the .exe, then double-click it."
