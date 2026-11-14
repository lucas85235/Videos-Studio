# Instala tudo o que o Videos Studio precisa (sem Docker).
# Uso: clique com o botão direito > "Executar com o PowerShell", ou: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Faltando: $cmd. $hint" -ForegroundColor Red
        exit 1
    }
}
Need "node" "Instale o Node.js 20+ (https://nodejs.org)."
Need "uv" "Instale o uv: powershell -c `"irm https://astral.sh/uv/install.ps1 | iex`""
Need "ffmpeg" "Instale o FFmpeg (ex.: winget install Gyan.FFmpeg) e reabra o terminal."

$data = if ($env:VS_DATA_DIR) { $env:VS_DATA_DIR } else { Join-Path $env:USERPROFILE "VideosStudioData" }
New-Item -ItemType Directory -Force $data | Out-Null
$env:UV_PROJECT_ENVIRONMENT = Join-Path $data ".venv-backend"

Write-Host "1/3 Backend Python (venv fora do OneDrive em $env:UV_PROJECT_ENVIRONMENT)" -ForegroundColor Cyan
# --extra asr: Whisper local, que confere cada narração (instrução lida em voz alta, texto repetido) e alinha as legendas
uv sync --project backend --python 3.12 --extra asr

Write-Host "2/3 Dependências Node (renderer e interface)" -ForegroundColor Cyan
npm install --no-audit --no-fund

Write-Host "3/3 Compilando a interface web" -ForegroundColor Cyan
npm run web:build

Write-Host ""
Write-Host "Pronto! Para abrir o app: scripts\iniciar.cmd" -ForegroundColor Green
