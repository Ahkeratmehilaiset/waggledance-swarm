@echo off
REM ═══════════════════════════════════════════════════════════
REM WaggleDance Swarm AI — Windows Launcher
REM Asettaa UTF-8 koodauksen ENNEN Pythonin käynnistystä.
REM ═══════════════════════════════════════════════════════════

REM Konsolin koodisivu UTF-8:ksi
chcp 65001 > nul 2>&1

REM Python UTF-8 Mode (PEP 540)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

REM PHASE1 TASK7: Ollama model keep-alive and max loaded models
set OLLAMA_KEEP_ALIVE=24h
set OLLAMA_MAX_LOADED_MODELS=4

REM Käynnistä
python main.py %*
