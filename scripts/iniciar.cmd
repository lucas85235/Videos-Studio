@echo off
rem Abre o Videos Studio (duplo clique). Feche esta janela para encerrar o app.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
