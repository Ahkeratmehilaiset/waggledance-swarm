@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title WaggleDance Restore

echo.
echo  =============================================
echo   WAGGLEDANCE ONE-CLICK RESTORE
echo  =============================================
echo.
echo  Place this .bat in the target folder and double-click.
echo  Finds the newest backup from known locations automatically.
echo.

:: ── Find the zip: 1) same folder, 2) C:\WaggleDance_Backups, 3) D:\WaggleDance_Backups
set "ZIPFILE="

:: Check same folder first (user may have copied zip here)
for /f "delims=" %%f in ('dir /b /o-n "%~dp0waggle_*.zip" 2^>nul') do (
    if not defined ZIPFILE set "ZIPFILE=%~dp0%%f"
)

:: Check C:\WaggleDance_Backups (newest first)
if not defined ZIPFILE (
    if exist "C:\WaggleDance_Backups" (
        for /f "delims=" %%f in ('dir /b /o-n "C:\WaggleDance_Backups\waggle_*.zip" 2^>nul') do (
            if not defined ZIPFILE set "ZIPFILE=C:\WaggleDance_Backups\%%f"
        )
    )
)

:: Check D:\WaggleDance_Backups (newest first)
if not defined ZIPFILE (
    if exist "D:\WaggleDance_Backups" (
        for /f "delims=" %%f in ('dir /b /o-n "D:\WaggleDance_Backups\waggle_*.zip" 2^>nul') do (
            if not defined ZIPFILE set "ZIPFILE=D:\WaggleDance_Backups\%%f"
        )
    )
)

if not defined ZIPFILE (
    echo  [ERROR] No waggle_*.zip found in:
    echo    - %~dp0
    echo    - C:\WaggleDance_Backups\
    echo    - D:\WaggleDance_Backups\
    echo.
    echo  Place a backup .zip next to this .bat, or check your drives.
    pause
    exit /b 1
)

echo  Found backup: !ZIPFILE!
echo  Target folder: %~dp0
echo.

:: ── Check Python ───────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found. Install Python 3.13+ from python.org
    echo  Make sure "Add Python to PATH" is checked during install.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo  Python: %PYVER%

:: ── Step 1: Extract zip ────────────────────────────────────────
echo.
echo  [1/4] Extracting backup...
python -c "import zipfile,sys; zf=zipfile.ZipFile(sys.argv[1]); zf.extractall(sys.argv[2]); print(f'     {len(zf.namelist())} files extracted')" "!ZIPFILE!" "%~dp0."
if errorlevel 1 (
    echo  [ERROR] Failed to extract zip file.
    pause
    exit /b 1
)

:: ── Step 2: Install pip dependencies ───────────────────────────
echo.
echo  [2/4] Installing Python dependencies...
if exist "%~dp0requirements.txt" (
    python -m pip install -r "%~dp0requirements.txt" --quiet --disable-pip-version-check 2>nul
    if errorlevel 1 (
        echo  [WARN] Some pip packages failed. Retrying verbose...
        python -m pip install -r "%~dp0requirements.txt" --disable-pip-version-check
    )
    echo       Dependencies installed.
) else (
    echo  [WARN] requirements.txt not found in backup.
)

:: ── Step 3: Create .env from example if missing ────────────────
echo.
echo  [3/4] Checking configuration...
if not exist "%~dp0.env" (
    if exist "%~dp0.env.example" (
        copy "%~dp0.env.example" "%~dp0.env" >nul
        echo       Created .env from .env.example — edit with your API keys.
    ) else (
        echo       No .env — WAGGLE_API_KEY will be auto-generated on first start.
    )
) else (
    echo       .env already exists.
)

:: ── Step 4: Validate environment ───────────────────────────────
echo.
echo  [4/4] Validating environment...
if exist "%~dp0tools\waggle_restore.py" (
    python "%~dp0tools\waggle_restore.py" --target "%~dp0."
) else (
    echo  [WARN] waggle_restore.py not found — skipping validation.
)

:: ── Check Ollama ───────────────────────────────────────────────
echo.
ollama --version >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Ollama not found. Install from https://ollama.com
    echo  Then run:
    echo    ollama pull phi4-mini
    echo    ollama pull llama3.2:1b
    echo    ollama pull nomic-embed-text
    echo    ollama pull all-minilm
) else (
    echo  Ollama found. Checking required models...
    set "NEED_PULL=0"
    for %%m in (phi4-mini llama3.2:1b nomic-embed-text all-minilm) do (
        ollama show %%m >nul 2>&1
        if errorlevel 1 (
            echo    Pulling %%m...
            ollama pull %%m
            set "NEED_PULL=1"
        )
    )
    if "!NEED_PULL!"=="0" echo    All 4 required models present.
)

:: ── Done ───────────────────────────────────────────────────────
echo.
echo  =============================================
echo   RESTORE COMPLETE
echo  =============================================
echo.
echo  Start WaggleDance:
echo    cd /d "%~dp0"
echo    python main.py
echo.
echo  Or use the launcher:
echo    python start.py
echo.
pause
