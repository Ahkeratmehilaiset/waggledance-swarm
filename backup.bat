@echo off
chcp 65001 >nul 2>&1
title WaggleDance Full Backup

echo.
echo  =============================================
echo   WAGGLEDANCE ONE-CLICK BACKUP
echo  =============================================
echo.

:: ── Check Python ─────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    pause
    exit /b 1
)

:: ── Run backup ───────────────────────────────────────────────
cd /d "%~dp0"
python -X utf8 tools/waggle_backup.py

echo.
echo  Backup complete. Check output above for details.
echo.
pause
