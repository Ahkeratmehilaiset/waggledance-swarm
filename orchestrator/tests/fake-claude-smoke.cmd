@echo off
rem Phase 2A-1 P5 wrapper: pretends to be the `claude` binary for the
rem orchestrator's print-mode call. The orchestrator's preflight probes
rem `--version` and `auth status` first; we answer those without invoking
rem fake-claude.ps1 (which would otherwise block on stdin EOF).
if "%1"=="--version" (
    echo fake-claude 0.0.0-test
    exit /b 0
)
if "%1"=="auth" (
    echo fake-claude: logged in ^(test mode^)
    exit /b 0
)
setlocal
set "WAGGLE_FAKE_SCENARIO=success_with_smoke_artifact"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-claude.ps1"
exit /b %ERRORLEVEL%
