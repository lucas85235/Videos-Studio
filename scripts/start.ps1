# Inicia o Vulto (API + interface) em http://127.0.0.1:8765
param([switch]$NoBrowser, [switch]$Dev)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$data = if ($env:VS_DATA_DIR) { $env:VS_DATA_DIR } else { Join-Path $env:USERPROFILE "VideosStudioData" }
$env:UV_PROJECT_ENVIRONMENT = Join-Path $data ".venv-backend"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path (Join-Path $root "apps\web\dist\index.html"))) {
    Write-Host "Interface ainda não compilada; compilando..." -ForegroundColor Yellow
    npm run web:build
}

if ($Dev) {
    # Modo desenvolvimento: backend com recarga + Vite em http://127.0.0.1:5173
    Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$root'; npm run web:dev"
    uv run --no-sync --project backend python -m studio serve --reload
    exit
}

$uvArgs = @("run", "--no-sync", "--project", "backend", "python", "-m", "studio", "serve")
if (-not $NoBrowser) { $uvArgs += "--open" }
uv @uvArgs
