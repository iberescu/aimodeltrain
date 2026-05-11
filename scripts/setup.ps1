# Bootstrap script for the project (Windows PowerShell).
# Run from C:\ibe\aimodel:  .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "==> Creating virtual environment in .venv"
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

Write-Host "==> Activating venv"
. .\.venv\Scripts\Activate.ps1

Write-Host "==> Upgrading pip"
python -m pip install --upgrade pip wheel setuptools

Write-Host "==> Installing requirements"
python -m pip install -r requirements.txt

Write-Host "==> Installing Playwright Chromium (one-time, ~300MB)"
python -m playwright install chromium

Write-Host ""
Write-Host "==> Done."
Write-Host ""
Write-Host "Next: set API keys in your shell (do NOT commit them):"
Write-Host '  $env:GOOGLE_API_KEY = "..."     # for Gemini 3.1 Pro generation'
Write-Host '  $env:ANTHROPIC_API_KEY = "..."  # optional, only if you use --provider anthropic for judging'
Write-Host '  $env:HUGGINGFACE_HUB_TOKEN = "..."  # for dataset/model push (Phase 4+)'
