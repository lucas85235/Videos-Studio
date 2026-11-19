# Inicia o Vulto (API + interface) em http://127.0.0.1:8765
param([switch]$NoBrowser, [switch]$Dev)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$data = if ($env:VS_DATA_DIR) { $env:VS_DATA_DIR } else { Join-Path $env:USERPROFILE "VideosStudioData" }
$env:UV_PROJECT_ENVIRONMENT = Join-Path $data ".venv-backend"
$env:PYTHONUTF8 = "1"

# App atualizado com dependência nova (ex.: Alembic): sincroniza o venv uma vez, quando o uv.lock muda.
# --inexact não remove o que já está instalado (ex.: o Whisper local).
$lock = Join-Path $root "backend\uv.lock"
$stamp = Join-Path $env:UV_PROJECT_ENVIRONMENT ".uv-lock-sincronizado"
if ((Test-Path $lock) -and (Test-Path $env:UV_PROJECT_ENVIRONMENT)) {
    $hash = (Get-FileHash $lock -Algorithm SHA256).Hash
    $last = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
    if ($hash -ne $last) {
        Write-Host "Atualizando as dependências do backend..." -ForegroundColor Yellow
        uv sync --project backend --extra asr --extra cutout --inexact
        if ($LASTEXITCODE -eq 0) { Set-Content -Path $stamp -Value $hash -Encoding ascii }
        else { Write-Host "Não deu para atualizar as dependências agora; rode scripts\setup.ps1." -ForegroundColor Red }
    }
}

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
