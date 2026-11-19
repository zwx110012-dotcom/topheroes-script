@echo off
chcp 65001 >nul
pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0启动专用代理试验.ps1"
echo.
echo Please keep this window open until the game exits. If an error appears, close the game and use the restore script.
pause