# Roda as rotinas do dia uma vez, sem abrir o Vulto (backup, métricas, pendências, radar).
# É o que a tarefa agendada chama (ver scripts/agendar-rotinas.ps1). Grava a saída em <dados>\logs\rotinas.log.
param([string]$DataDir = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($DataDir) { $env:VS_DATA_DIR = $DataDir }
$data = if ($env:VS_DATA_DIR) { $env:VS_DATA_DIR } else { Join-Path $env:USERPROFILE "VideosStudioData" }
$env:UV_PROJECT_ENVIRONMENT = Join-Path $data ".venv-backend"
$env:PYTHONUTF8 = "1"

$logs = Join-Path $data "logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$log = Join-Path $logs "rotinas.log"
# o log não cresce para sempre: passa de 1 MB, recomeça
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Move-Item -Force $log "$log.old" }

Add-Content -Path $log -Encoding utf8 -Value "`n==== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===="
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Add-Content -Path $log -Encoding utf8 -Value "uv não encontrado no PATH; rode scripts\setup.ps1"
    exit 1
}
$ErrorActionPreference = "Continue"
# o Python escreve UTF-8 (PYTHONUTF8); sem isto o PowerShell 5.1 lê a saída como cp850 e embaralha os acentos
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
& $uv.Source run --no-sync --project backend python -m studio rotinas *>&1 |
    ForEach-Object { "$_" } | Add-Content -Path $log -Encoding utf8
exit $LASTEXITCODE
