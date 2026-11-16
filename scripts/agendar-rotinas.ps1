# Agenda as rotinas do Vulto no Agendador de Tarefas do Windows, para rodarem com o app fechado.
#
#   powershell -ExecutionPolicy Bypass -File scripts\agendar-rotinas.ps1                  # instala (todo dia às 08:00 e ao entrar no Windows)
#   powershell -ExecutionPolicy Bypass -File scripts\agendar-rotinas.ps1 -Horario 07:30   # outro horário
#   powershell -ExecutionPolicy Bypass -File scripts\agendar-rotinas.ps1 -Remover         # tira a tarefa
#
# A tarefa chama scripts\rodar-rotinas.ps1 (uv + o ambiente do projeto, como o start.ps1), que roda
# "python -m studio rotinas". Se o computador estiver desligado no horário, ela roda assim que der.
# Com o Vulto aberto a execução da tarefa não faz nada: as rotinas ficam com o app. Nada roda duas vezes no mesmo dia.
# Use o mesmo horário da manhã da página Hoje: antes dele, só a cópia do banco roda.
param(
    [string]$Horario = "08:00",
    [switch]$Remover,
    [string]$Nome = "Vulto Rotinas"
)
$ErrorActionPreference = "Stop"

if ($Remover) {
    if (Get-ScheduledTask -TaskName $Nome -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Nome -Confirm:$false
        Write-Host "Tarefa '$Nome' removida." -ForegroundColor Green
    } else {
        Write-Host "A tarefa '$Nome' não existe." -ForegroundColor Yellow
    }
    exit
}

if ($Horario -notmatch '^([01]\d|2[0-3]):[0-5]\d$') {
    Write-Host "Horário inválido: use HH:MM, ex. 08:00" -ForegroundColor Red
    exit 1
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "uv não encontrado no PATH; rode scripts\setup.ps1 antes." -ForegroundColor Red
    exit 1
}

$runner = Join-Path $PSScriptRoot "rodar-rotinas.ps1"
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`""
# Pasta de dados fora do padrão: a tarefa não vê as variáveis desta janela, então vai como parâmetro.
if ($env:VS_DATA_DIR) { $arguments += " -DataDir `"$env:VS_DATA_DIR`"" }

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $user),
    (New-ScheduledTaskTrigger -Daily -At $Horario)
)
# -StartWhenAvailable: "executar o mais cedo possível se perder o horário"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
# Só com você logado (sem senha guardada): a tarefa usa o seu uv, o seu PATH e o seu cofre de chaves.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $Nome -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description "Rotinas do Vulto com o app fechado: cópia do banco, métricas, pendências e radar." -Force | Out-Null

Write-Host "Tarefa '$Nome' agendada: ao entrar no Windows e todo dia às $Horario." -ForegroundColor Green
Write-Host "Log: <pasta de dados>\logs\rotinas.log. Para tirar: scripts\agendar-rotinas.ps1 -Remover"
