# One-time (and repeatable) development setup on Windows.
#   powershell -ExecutionPolicy Bypass -File scripts\dev-setup.ps1
# Requires: Docker Desktop running, Python 3.12+, Node 22+.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python scripts/init-env.py
docker compose up -d db redis
if ($LASTEXITCODE -ne 0) { throw "docker compose failed. Is Docker Desktop running?" }

Set-Location "$root\backend"
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\python -m pip install -q -e ".[dev]"
& .\.venv\Scripts\alembic upgrade head

Set-Location "$root\frontend"
npm install

Set-Location $root
Write-Host ""
Write-Host "Setup done. Next:"
Write-Host "  1. Create your administrator (prompts for a password):"
Write-Host "       cd backend; .\.venv\Scripts\python -m app.cli create-admin --username <name>"
Write-Host "  2. Start the API:      cd backend;  .\.venv\Scripts\uvicorn app.main:app --reload"
Write-Host "  3. Start the frontend: cd frontend; npm run dev"
Write-Host "  4. Open http://localhost:5173"
