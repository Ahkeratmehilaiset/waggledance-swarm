@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title WaggleDance One-Click Restore
cd /d "%~dp0"

echo.
echo  =============================================
echo   WAGGLEDANCE ONE-CLICK RESTORE v4.0
echo  =============================================
echo.
echo  Tiputa tama .bat kohdekansioon ja tuplaklikkaa.
echo  Etsii uusimman backupin, purkaa, luo venv:n,
echo  asentaa kaiken ja ajaa smoke-testin.
echo.

:: ── Find Python ────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python ei loydy PATHista.
    echo  Asenna Python 3.13+ osoitteesta python.org
    echo  Ruksaa "Add Python to PATH" asennuksessa.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
echo  Python: %PYVER%

:: ── Find backup zip ────────────────────────────────────────────
set "ZIPFILE="

:: 1) Same folder
for /f "delims=" %%f in ('dir /b /o-n "%~dp0waggle_*.zip" 2^>nul') do (
    if not defined ZIPFILE set "ZIPFILE=%~dp0%%f"
)

:: 2) C:\WaggleDance_Backups
if not defined ZIPFILE (
    if exist "C:\WaggleDance_Backups" (
        for /f "delims=" %%f in ('dir /b /o-n "C:\WaggleDance_Backups\waggle_*.zip" 2^>nul') do (
            if not defined ZIPFILE set "ZIPFILE=C:\WaggleDance_Backups\%%f"
        )
    )
)

:: 3) D:\WaggleDance_Backups
if not defined ZIPFILE (
    if exist "D:\WaggleDance_Backups" (
        for /f "delims=" %%f in ('dir /b /o-n "D:\WaggleDance_Backups\waggle_*.zip" 2^>nul') do (
            if not defined ZIPFILE set "ZIPFILE=D:\WaggleDance_Backups\%%f"
        )
    )
)

:: ── Route: with zip or without ─────────────────────────────────
if defined ZIPFILE (
    echo  Backup: !ZIPFILE!
    echo  Target: %~dp0
    echo.

    :: Extract zip first with Python (handles unicode paths)
    echo  [1/2] Puretaan backup...
    python -c "import zipfile,sys; zf=zipfile.ZipFile(sys.argv[1]); zf.extractall(sys.argv[2]); print(f'       {len(zf.namelist())} tiedostoa purettu')" "!ZIPFILE!" "%~dp0."
    if errorlevel 1 (
        echo  [ERROR] Zip-purku epaonnistui.
        pause
        exit /b 1
    )
    echo.
    echo  [2/2] Asennetaan ymparisto...
) else (
    echo  [INFO] Ei waggle_*.zip tiedostoa - asennetaan ymparisto nykyisista tiedostoista.
    echo.
)

:: ── Run restore.py (the real logic) ────────────────────────────
if exist "%~dp0tools\restore.py" (
    python "%~dp0tools\restore.py" --target "%~dp0."
) else if exist "%~dp0restore.py" (
    python "%~dp0restore.py" --target "%~dp0."
) else (
    echo.
    echo  [WARN] restore.py ei loydy - tehdaan perus-asennus...
    echo.

    :: Fallback: basic venv + pip install
    echo  Luodaan .venv...
    if not exist ".venv" python -m venv .venv
    if errorlevel 1 (
        echo  [ERROR] venv-luonti epaonnistui
        pause
        exit /b 1
    )

    echo  Asennetaan riippuvuudet...
    call .venv\Scripts\activate.bat

    if exist "requirements.lock.txt" (
        pip install -r requirements.lock.txt --quiet --disable-pip-version-check
    ) else if exist "requirements.txt" (
        pip install -r requirements.txt --quiet --disable-pip-version-check
    ) else (
        echo  [WARN] Ei requirements-tiedostoa.
    )

    :: Create runtime dirs
    if not exist data mkdir data
    if not exist logs mkdir logs
    if not exist chroma_data mkdir chroma_data

    :: .env
    if not exist ".env" (
        if exist "env.template" (
            copy "env.template" ".env" >nul
            echo  .env luotu env.template:sta
        ) else (
            echo WAGGLE_PROFILE=COTTAGE> .env
            echo WAGGLE_API_KEY=>> .env
            echo OLLAMA_HOST=http://localhost:11434>> .env
            echo  .env luotu oletusarvoilla
        )
    )

    echo.
    echo  Perus-asennus valmis.
)

:: ── Check Ollama ───────────────────────────────────────────────
echo.
ollama --version >nul 2>&1
if errorlevel 1 (
    echo  [INFO] Ollama ei loydy. Asenna: https://ollama.com
    echo  Aja sitten:
    echo    ollama pull phi4-mini
    echo    ollama pull llama3.2:1b
    echo    ollama pull nomic-embed-text
    echo    ollama pull all-minilm
) else (
    echo  Ollama loytyy. Tarkistetaan mallit...
    set "NEED_PULL=0"
    for %%m in (phi4-mini llama3.2:1b nomic-embed-text all-minilm) do (
        ollama show %%m >nul 2>&1
        if errorlevel 1 (
            echo    Puuttuu: %%m
            set "NEED_PULL=1"
        )
    )
    if "!NEED_PULL!"=="0" echo    Kaikki 4 mallia loytyvat.
)

:: ── Done ───────────────────────────────────────────────────────
echo.
echo  =============================================
echo   RESTORE VALMIS
echo  =============================================
echo.

:: Detect which entrypoint exists
if exist "%~dp0waggledance\adapters\cli\start_runtime.py" (
    echo  Kaynnista WaggleDance:
    echo    cd /d "%~dp0"
    echo    .venv\Scripts\activate
    echo    python -m waggledance.adapters.cli.start_runtime
) else (
    echo  Kaynnista WaggleDance:
    echo    cd /d "%~dp0"
    echo    python main.py
)
echo.
pause
