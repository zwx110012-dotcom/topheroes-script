@echo off
chcp 65001 >nul
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0恢复游戏原模块.ps1"
pause