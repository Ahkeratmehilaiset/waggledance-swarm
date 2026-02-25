#!/usr/bin/env python3
"""
WaggleDance / OpenClaw — Smart Backup & Restore Tool v2.0
==========================================================
Toiminnot:
  backup   — Älykkäästi pakkaa VAIN projektin tiedostot (dependency scan)
  restore  — Palauttaa + tarkistaa raudan + asentaa puuttuvat osat
  check    — Täysi ympäristö + rauta -analyysi
  list     — Listaa olemassa olevat varmuuskopiot

Älykkäät ominaisuudet:
  • Backup: Skannaa Python-importit → sisällyttää vain oikeat tiedostot
  • Backup: Tunnistaa settings.yaml:sta mallit ja riippuvuudet
  • Restore: Tarkistaa CPU, RAM, GPU VRAM automaattisesti
  • Restore: Asettaa OLLAMA_MAX_LOADED_MODELS ym. ympäristömuuttujat
  • Restore: Lataa puuttuvat Ollama-mallit automaattisesti
  • Restore: Asentaa puuttuvat pip-paketit

Käyttö:
  python openclaw_backup.py backup
  python openclaw_backup.py restore
  python openclaw_backup.py check
  python openclaw_backup.py list

Arkistot: ./backups/waggle_YYYYMMDD_HHMMSS.zip
"""
import sys
import os
import re
import ast
import shutil
import subprocess
import json
import zipfile
import platform
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# ═══════════════════════════════════════════════════════════════
# KONFIGURAATIO
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).resolve().parent

# Älykkäästi tunnista projektin sijainti:
#   Ajetaan juuresta (U:\openclaw_backup.py)  → projekti = U:\project\
#   Ajetaan projektista (U:\project\openclaw_backup.py) → projekti = U:\project\
if (SCRIPT_DIR / "project" / "main.py").exists():
    PROJECT_DIR = SCRIPT_DIR / "project"    # Skripti juuressa, projekti ./project/
elif (SCRIPT_DIR / "main.py").exists():
    PROJECT_DIR = SCRIPT_DIR                # Skripti projektin sisällä
else:
    PROJECT_DIR = SCRIPT_DIR                # Fallback

BACKUP_DIR = PROJECT_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

# AI-tiivistelmä tallennetaan AINA skriptin kansioon (juuri)
AI_BRIEF_PATH = SCRIPT_DIR / "WAGGLEDANCE_AI_BRIEF.md"

# Vaaditut Python-paketit (import-nimi → pip-nimi)
PIP_MAP = {
    "yaml": "pyyaml",
    "uvicorn": "uvicorn[standard]",
    "fastapi": "fastapi",
    "pydantic": "pydantic",
    "httpx": "httpx",
    "requests": "requests",
    "aiosqlite": "aiosqlite",
    "websockets": "websockets",
    "starlette": "starlette",
    "psutil": "psutil",
    # v0.0.3 — Tietoisuuskerros + käännöspipeline
    "chromadb": "chromadb",
    "transformers": "transformers",
    "ctranslate2": "ctranslate2",
    "sentencepiece": "sentencepiece",
    "sacremoses": "sacremoses",
    "torch": "torch",
    "voikko": "voikko",
    # v0.1.8 — Ulkoiset datasyötteet
    "feedparser": "feedparser",
    "aiohttp": "aiohttp",
}

# Kansiot jotka AINA pois backupista
ALWAYS_EXCLUDE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "backups",  # Ei backupin sisään
    ".claude",  # Claude Code -session data
}

# Tiedostopäätteet jotka AINA pois
ALWAYS_EXCLUDE_EXTENSIONS = {
    ".pyc", ".pyo", ".egg-info", ".whl",
    ".log",  # Logit eivät kuulu backuppiin
    ".tmp", ".bak", ".swp", ".swo",
}

# Vanhentuneet tiedostot — nimetty pattern-perusteisesti
STALE_FILE_PATTERNS = {
    "hivemind_backup_",       # Vanhat manuaaliset backupit
    "fix_consciousness_",     # Kertaluontoiset fix-skriptit
    "fix_en_to_fi_",
    "fix_yaml_hook",
    "patch_all_",
    "patch_chat_",
    "patch_consciousness_",
    "patch_final",
    "patch_opus_",
    "patch_yaml_",
    "hotfix_",
    "waggledance_mega_patch",
    "hivemind_debug",
    "en_validator_patch",
}

# Vanhat benchmark-tulokset (säilytetään vain uusin per tyyppi)
STALE_BENCHMARK_KEEP_LATEST = True

# Tiedostopäätteet jotka KUULUVAT projektiin
PROJECT_EXTENSIONS = {
    ".py", ".yaml", ".yml", ".json", ".md", ".txt",
    ".html", ".css", ".js", ".jsx", ".bat", ".sh",
    ".toml", ".cfg", ".ini",
    ".ino",  # ESP32 firmware
    ".db",   # SQLite databases
    ".jsonl", # Newline-delimited JSON logs
}

# Projektin kriittiset tiedostot (PITÄÄ löytyä)
CRITICAL_FILES = [
    "main.py",
    "hivemind.py",
    "consciousness.py",
    "translation_proxy.py",
    "configs/settings.yaml",
    "core/yaml_bridge.py",
    "core/llm_provider.py",
    "web/dashboard.py",
    "backend/main.py",
    "backend/routes/chat.py",
    "dashboard/src/App.jsx",
    "dashboard/package.json",
    "dashboard/vite.config.js",
    "openclaw_backup.py",
]


# ═══════════════════════════════════════════════════════════════
# VÄRIT (Windows-yhteensopiva)
# ═══════════════════════════════════════════════════════════════

def _init_colors():
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
    # Fix Windows console encoding for Unicode characters
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_init_colors()

G = "\033[92m"   # Vihreä
R = "\033[91m"   # Punainen
Y = "\033[93m"   # Keltainen
B = "\033[94m"   # Sininen
C = "\033[96m"   # Syaani
W = "\033[97m"   # Valkoinen
DIM = "\033[90m" # Himmeä
X = "\033[0m"    # Reset


def ok(msg):   print(f"  {G}✅ {msg}{X}")
def warn(msg): print(f"  {Y}⚠️  {msg}{X}")
def err(msg):  print(f"  {R}❌ {msg}{X}")
def info(msg): print(f"  {B}ℹ️  {msg}{X}")


def banner(title):
    print(f"\n{W}{'═' * 55}")
    print(f"  {title}")
    print(f"{'═' * 55}{X}\n")


# ═══════════════════════════════════════════════════════════════
# RAUTA-ANALYYSI (Hardware Detection)
# ═══════════════════════════════════════════════════════════════

def detect_hardware() -> dict:
    """
    Tunnistaa CPU:n, RAM:n, GPU:n ja VRAM:n.
    Palauttaa dict jossa kaikki tiedot.
    """
    hw = {
        "cpu": "?",
        "cpu_cores": 0,
        "cpu_threads": 0,
        "ram_gb": 0,
        "ram_free_gb": 0,
        "gpu_name": None,
        "gpu_vram_gb": 0,
        "gpu_vram_free_gb": 0,
        "os": f"{platform.system()} {platform.release()}",
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }

    # ── CPU ────────────────────────────────────────────
    hw["cpu_cores"] = os.cpu_count() or 0
    try:
        import psutil
        hw["cpu_threads"] = psutil.cpu_count(logical=True) or 0
        hw["cpu_cores"] = psutil.cpu_count(logical=False) or 0
        mem = psutil.virtual_memory()
        hw["ram_gb"] = round(mem.total / (1024 ** 3), 1)
        hw["ram_free_gb"] = round(mem.available / (1024 ** 3), 1)
    except ImportError:
        # Fallback ilman psutil
        pass

    # CPU-nimi
    try:
        if platform.system() == "Windows":
            result = subprocess.run(
                ["wmic", "cpu", "get", "name"],
                capture_output=True, text=True, timeout=5
            )
            lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip() and l.strip() != "Name"]
            if lines:
                hw["cpu"] = lines[0]
        else:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if "model name" in line:
                        hw["cpu"] = line.split(":")[1].strip()
                        break
    except Exception:
        pass

    # ── GPU (nvidia-smi) ───────────────────────────────
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(",")
            if len(parts) >= 3:
                hw["gpu_name"] = parts[0].strip()
                hw["gpu_vram_gb"] = round(int(parts[1].strip()) / 1024, 1)
                hw["gpu_vram_free_gb"] = round(int(parts[2].strip()) / 1024, 1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return hw


def print_hardware(hw: dict):
    """Tulosta rauta-analyysi kauniisti."""
    banner("RAUTA-ANALYYSI")

    ok(f"Käyttöjärjestelmä: {hw['os']}")
    ok(f"Python: {hw['python']}")
    print()
    ok(f"CPU: {hw['cpu']}")
    ok(f"Ytimet: {hw['cpu_cores']} fyysistä, {hw['cpu_threads']} säiettä")
    print()

    if hw["ram_gb"] > 0:
        ram_pct = ((hw["ram_gb"] - hw["ram_free_gb"]) / hw["ram_gb"]) * 100
        bar = _make_bar(ram_pct, 30)
        ok(f"RAM: {hw['ram_gb']} GB (vapaa: {hw['ram_free_gb']:.1f} GB)")
        print(f"       {bar}")
    else:
        warn("RAM: ei tietoa (asenna psutil → pip install psutil)")

    print()
    if hw["gpu_name"]:
        vram_used = hw["gpu_vram_gb"] - hw["gpu_vram_free_gb"]
        vram_pct = (vram_used / hw["gpu_vram_gb"]) * 100 if hw["gpu_vram_gb"] > 0 else 0
        bar = _make_bar(vram_pct, 30)
        ok(f"GPU: {hw['gpu_name']}")
        ok(f"VRAM: {hw['gpu_vram_gb']} GB (vapaa: {hw['gpu_vram_free_gb']:.1f} GB)")
        print(f"       {bar}")
    else:
        warn("GPU: ei NVIDIA-GPU:ta tai nvidia-smi puuttuu")

    return hw


def _make_bar(pct: float, width: int = 30) -> str:
    """Visuaalinen palkkikuvaaja."""
    filled = int(pct / 100 * width)
    color = G if pct < 60 else Y if pct < 85 else R
    return f"  {color}[{'█' * filled}{'░' * (width - filled)}] {pct:.0f}%{X}"


# ═══════════════════════════════════════════════════════════════
# SUOSITUKSET raudan perusteella
# ═══════════════════════════════════════════════════════════════

def recommend_settings(hw: dict) -> dict:
    """Ehdota asetuksia raudan perusteella."""
    rec = {
        "max_loaded_models": 2,
        "chat_model": "qwen2.5:32b",
        "heartbeat_model": "jobautomation/OpenEuroLLM-Finnish",
        "heartbeat_num_gpu": 0,
        "max_concurrent": 3,
        "notes": [],
    }

    vram = hw.get("gpu_vram_gb", 0)
    ram = hw.get("ram_gb", 0)

    # GPU-suositukset — WaggleDance v0.0.3
    # Nykyinen setup: phi4-mini (2.5G) + llama3.2:1b (0.7G) + nomic-embed (0.3G) + Opus-MT (0.6G) = 4.1G
    if vram >= 20:
        rec["chat_model"] = "qwen2.5:32b"
        rec["heartbeat_model"] = "llama3.2:1b"
        rec["notes"].append("32b mahtuu GPU:lle (20GB+)")
    elif vram >= 8:
        rec["chat_model"] = "phi4-mini"
        rec["heartbeat_model"] = "llama3.2:1b"
        rec["heartbeat_num_gpu"] = None  # Kaikki GPU:lle (4.1G/8G)
        rec["max_loaded_models"] = 3
        rec["notes"].append("8GB GPU: phi4-mini+llama1b+nomic-embed = 3.5G + Opus-MT 0.6G")
    elif vram >= 4:
        rec["chat_model"] = "phi4-mini"
        rec["heartbeat_model"] = "llama3.2:1b"
        rec["heartbeat_num_gpu"] = 0
        rec["notes"].append("4GB GPU: vain chat GPU:lle, heartbeat CPU")
    elif vram > 0:
        rec["chat_model"] = "llama3.2:1b"
        rec["notes"].append("Pieni GPU → vain 1b")
    else:
        rec["chat_model"] = "llama3.2:1b"
        rec["heartbeat_num_gpu"] = 0
        rec["notes"].append("Ei GPU:ta → kaikki CPU:lla")

    # RAM-suositukset
    if ram < 16:
        rec["max_concurrent"] = 1
        rec["notes"].append("Vähän RAM:ia (<16GB) → conc=1")
    elif ram < 32:
        rec["max_concurrent"] = 2
        rec["notes"].append("Kohtalaisesti RAM:ia → conc=2")

    # Heartbeat CPU:lle tarvitsee ~5GB RAM:ia
    if rec["heartbeat_num_gpu"] == 0 and ram < 12:
        rec["notes"].append("⚠️ RAM alle 12GB — CPU heartbeat voi olla hidas")

    return rec


# ═══════════════════════════════════════════════════════════════
# DEPENDENCY SCANNER (Älykäs backup)
# ═══════════════════════════════════════════════════════════════

def scan_python_imports(filepath: Path) -> set:
    """
    Pura Python-tiedoston import-lauseet.
    Palauttaa joukon moduulinimiä (lokaalit, ei stdlib).
    """
    imports = set()
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
    except (SyntaxError, ValueError):
        # Fallback: regex
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(
                r'^\s*(?:from|import)\s+([\w.]+)', source, re.MULTILINE
            ):
                imports.add(match.group(1).split(".")[0])
        except Exception:
            pass
    return imports


def scan_yaml_references(filepath: Path) -> set:
    """
    Skannaa YAML-tiedostosta viittaukset muihin tiedostoihin ja malleihin.
    """
    refs = set()
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
        # Tiedostopolut
        for match in re.finditer(r'["\']?([/\\]?[\w./\\-]+\.(?:py|yaml|json|db))["\']?', text):
            refs.add(match.group(1))
        # Ollama-mallit
        for match in re.finditer(r'model:\s*["\']?([^\s"\'#]+)', text):
            refs.add(f"ollama:{match.group(1)}")
        # agents_dir
        for match in re.finditer(r'agents_dir:\s*["\']?([^\s"\'#]+)', text):
            refs.add(f"dir:{match.group(1)}")
    except Exception:
        pass
    return refs


def build_dependency_tree() -> dict:
    """
    Rakennetaan riippuvuuspuu projektista.
    Palauttaa:
      {
        "project_files": set,     # Tiedostot jotka kuuluvat projektiin
        "python_deps": set,       # Ulkoiset Python-paketit
        "ollama_models": set,     # Vaaditut Ollama-mallit
        "data_dirs": set,         # Data-kansiot
      }
    """
    project_files = set()
    python_deps = set()
    ollama_models = set()
    data_dirs = set()

    # Stdlib-moduulit (ei sisällytetä riippuvuuksiin)
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else {
        "os", "sys", "re", "ast", "json", "time", "math", "pathlib",
        "datetime", "collections", "typing", "dataclasses", "asyncio",
        "logging", "shutil", "subprocess", "platform", "hashlib",
        "zipfile", "glob", "io", "abc", "functools", "itertools",
        "contextlib", "enum", "statistics", "copy", "random",
        "socket", "threading", "queue", "struct", "base64",
        "urllib", "http", "email", "html", "csv", "sqlite3",
        "signal", "traceback", "inspect", "textwrap", "string",
        "operator", "secrets", "tempfile", "unittest", "pdb",
    }

    # Lokaalit moduulit (projektin omat)
    local_modules = set()
    for py in PROJECT_DIR.rglob("*.py"):
        rel = py.relative_to(PROJECT_DIR)
        parts = rel.parts
        if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS):
            continue
        # Moduulinimi: "core/llm_provider.py" → "core"
        local_modules.add(parts[0].replace(".py", ""))
        if len(parts) > 1:
            local_modules.add(parts[0])

    # 1. Skannaa kaikki Python-tiedostot
    for py_file in PROJECT_DIR.rglob("*.py"):
        rel = py_file.relative_to(PROJECT_DIR)
        parts = rel.parts

        # Ohita excludet
        if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS):
            continue
        if any(str(rel).endswith(ext) for ext in ALWAYS_EXCLUDE_EXTENSIONS):
            continue
        # Ohita vanhentuneet kertaluontoiset skriptit
        if any(rel.name.startswith(pat) for pat in STALE_FILE_PATTERNS):
            continue

        project_files.add(rel)
        imports = scan_python_imports(py_file)

        for imp in imports:
            if imp in stdlib:
                continue
            elif imp in local_modules:
                continue  # Oma moduuli → sisällytetään jo
            else:
                python_deps.add(imp)

    # 2. Skannaa YAML/config-tiedostot
    for yaml_file in PROJECT_DIR.rglob("*.yaml"):
        rel = yaml_file.relative_to(PROJECT_DIR)
        parts = rel.parts
        if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS):
            continue
        project_files.add(rel)
        refs = scan_yaml_references(yaml_file)
        for ref in refs:
            if ref.startswith("ollama:"):
                ollama_models.add(ref[7:])
            elif ref.startswith("dir:"):
                data_dirs.add(ref[4:])

    for yml_file in PROJECT_DIR.rglob("*.yml"):
        rel = yml_file.relative_to(PROJECT_DIR)
        parts = rel.parts
        if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS):
            continue
        project_files.add(rel)

    # 3. Muut projektitiedostot (configs, docs, templates)
    for ext in PROJECT_EXTENSIONS - {".py", ".yaml", ".yml"}:
        for f in PROJECT_DIR.rglob(f"*{ext}"):
            rel = f.relative_to(PROJECT_DIR)
            parts = rel.parts
            if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS):
                continue
            # Ohita isot tiedostot (>5MB) paitsi .json data
            if f.stat().st_size > 5 * 1024 * 1024 and ext != ".json":
                continue
            project_files.add(rel)

    # 4. Data-kansion tiedostot (ChromaDB, micromodel, logs)
    data_dir = PROJECT_DIR / "data"
    if data_dir.exists():
        # Ohita vanhat testi-/väliaikais-kansiot
        data_exclude_dirs = {"test_consciousness_v2", "hacker_workspace"}
        for data_file in data_dir.rglob("*"):
            if not data_file.is_file():
                continue
            rel = data_file.relative_to(PROJECT_DIR)
            parts = rel.parts
            if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS | data_exclude_dirs):
                continue
            # Max 50MB per tiedosto
            if data_file.stat().st_size > 50 * 1024 * 1024:
                info(f"Ohitetaan iso datatiedosto: {rel} "
                     f"({data_file.stat().st_size / (1024*1024):.0f} MB)")
                continue
            project_files.add(rel)

    # 5. start.bat / Modelfile / requirements.txt ym.
    for special in ["start.bat", "Modelfile", "requirements.txt",
                    "README.md", "CHANGELOG.md", "LICENSE",
                    "CLAUDE.md", "openclaw_backup.py"]:
        p = PROJECT_DIR / special
        if p.exists():
            project_files.add(Path(special))

    # 6. Voikko-sanakirjat (suomen kielen morfologia)
    voikko_dir = PROJECT_DIR / "voikko"
    if voikko_dir.exists():
        for vf in voikko_dir.rglob("*"):
            if vf.is_file():
                project_files.add(vf.relative_to(PROJECT_DIR))

    # 7. Suodata vanhentuneet benchmark-tulokset (säilytä vain uusin per tyyppi)
    if STALE_BENCHMARK_KEEP_LATEST:
        benchmark_groups = defaultdict(list)
        for rel in list(project_files):
            name = rel.name
            if name.startswith("benchmark_") and name.endswith(".json") and len(rel.parts) == 1:
                # Ryhmittele: benchmark_v3_*.json → "benchmark_v3"
                prefix = "_".join(name.split("_")[:2]) if "_" in name else name
                benchmark_groups[prefix].append(rel)
        for prefix, files in benchmark_groups.items():
            if len(files) > 1:
                # Säilytä uusin, poista vanhat
                sorted_files = sorted(files, key=lambda r: r.name, reverse=True)
                for old_file in sorted_files[1:]:
                    project_files.discard(old_file)

    return {
        "project_files": project_files,
        "python_deps": python_deps,
        "ollama_models": ollama_models,
        "data_dirs": data_dirs,
    }


# ═══════════════════════════════════════════════════════════════
# OLLAMA-TOIMINNOT
# ═══════════════════════════════════════════════════════════════

def get_ollama_models() -> set:
    """Hae Ollamasta ladatut mallit."""
    models = set()
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n")[1:]:  # Skip header
                parts = line.split()
                if parts:
                    models.add(parts[0])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return models


def pull_model(model: str) -> bool:
    """Lataa Ollama-malli. Näyttää progressin."""
    print(f"\n  {C}📥 Ladataan: {model}{X}")
    try:
        result = subprocess.run(
            ["ollama", "pull", model],
            timeout=600,  # 10min max
        )
        if result.returncode == 0:
            ok(f"Ladattu: {model}")
            return True
        else:
            err(f"Lataus epäonnistui: {model}")
            return False
    except subprocess.TimeoutExpired:
        err(f"Timeout: {model} (>10min)")
        return False
    except FileNotFoundError:
        err("Ollama ei löydy — asenna ensin: https://ollama.ai")
        return False


# ═══════════════════════════════════════════════════════════════
# YMPÄRISTÖN ASETTAMINEN
# ═══════════════════════════════════════════════════════════════

def ensure_environment(hw: dict, rec: dict):
    """
    Tarkista ja aseta ympäristömuuttujat.
    Luo start.bat joka asettaa ne pysyvästi.
    """
    banner("YMPÄRISTÖN KONFIGUROINTI")

    changes = []

    # 1. OLLAMA_MAX_LOADED_MODELS
    current = os.environ.get("OLLAMA_MAX_LOADED_MODELS", "")
    if current != str(rec["max_loaded_models"]):
        os.environ["OLLAMA_MAX_LOADED_MODELS"] = str(rec["max_loaded_models"])
        changes.append(f"OLLAMA_MAX_LOADED_MODELS={rec['max_loaded_models']}")
        ok(f"OLLAMA_MAX_LOADED_MODELS → {rec['max_loaded_models']}")
    else:
        ok(f"OLLAMA_MAX_LOADED_MODELS = {current} (jo asetettu)")

    # 2. OLLAMA_KEEP_ALIVE (pidä mallit GPU:lla)
    if os.environ.get("OLLAMA_KEEP_ALIVE", "") != "24h":
        os.environ["OLLAMA_KEEP_ALIVE"] = "24h"
        changes.append("OLLAMA_KEEP_ALIVE=24h")
        ok("OLLAMA_KEEP_ALIVE → 24h (embedding ei unloadaa)")

    # 3. PYTHONUTF8
    if os.environ.get("PYTHONUTF8") != "1":
        os.environ["PYTHONUTF8"] = "1"
        changes.append("PYTHONUTF8=1")
        ok("PYTHONUTF8 → 1")

    # 4. PYTHONIOENCODING
    if os.environ.get("PYTHONIOENCODING") != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        changes.append("PYTHONIOENCODING=utf-8")
        ok("PYTHONIOENCODING → utf-8")

    # 5. Luo/päivitä start.bat
    if platform.system() == "Windows":
        bat_path = PROJECT_DIR / "start.bat"
        bat_content = f"""@echo off
chcp 65001 > nul 2>&1
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set OLLAMA_MAX_LOADED_MODELS={rec['max_loaded_models']}
set OLLAMA_KEEP_ALIVE=24h

echo.
echo  WaggleDance Swarm AI — Kaynnistys
echo  ══════════════════════════════════
echo  Chat:      {rec['chat_model']} (GPU)
echo  Heartbeat: {rec['heartbeat_model']} (CPU)
echo  Max mallit: {rec['max_loaded_models']}
echo.

python main.py %*
"""
        bat_path.write_text(bat_content, encoding="utf-8")
        ok(f"start.bat päivitetty ({len(changes)} muutosta)")

    # 6. Luo .env ohje
    if changes:
        print(f"\n  {Y}Huom: Ympäristömuuttujat asetettu TÄHÄN sessioon.{X}")
        print(f"  {Y}Pysyvä asetus Windowsissa:{X}")
        for c in changes:
            k, v = c.split("=")
            print(f"    {DIM}[System] > setx {k} {v}{X}")


# ═══════════════════════════════════════════════════════════════
# TÄYSI YMPÄRISTÖTARKISTUS
# ═══════════════════════════════════════════════════════════════

def check_environment(verbose=True):
    """Tarkistaa koko ympäristön + raudan. Palauttaa (ok, errors, warnings)."""
    errors = []
    warnings = []

    # ── RAUTA ──────────────────────────────────────────
    hw = detect_hardware()
    if verbose:
        print_hardware(hw)

    # ── PYTHON ─────────────────────────────────────────
    if verbose:
        banner("OHJELMISTOYMPÄRISTÖ")

    v = sys.version_info
    if v.major >= 3 and v.minor >= 10:
        if verbose: ok(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        errors.append(f"Python {v.major}.{v.minor} — vaaditaan >= 3.10")
        if verbose: err(errors[-1])

    # ── PIP-PAKETIT ────────────────────────────────────
    if verbose:
        print(f"\n  {W}Python-paketit:{X}")

    # Skannaa mitä projekti oikeasti tarvitsee
    deps = build_dependency_tree()
    needed_imports = deps["python_deps"]

    # Lisää tunnetut paketit
    for imp_name in PIP_MAP:
        needed_imports.add(imp_name)

    missing_pkgs = []
    for pkg in sorted(needed_imports):
        try:
            __import__(pkg)
            if verbose: ok(f"  {pkg}")
        except ImportError:
            pip_name = PIP_MAP.get(pkg, pkg)
            missing_pkgs.append(pip_name)
            if verbose: err(f"  {pkg} puuttuu → pip install {pip_name}")

    if missing_pkgs:
        errors.append(f"{len(missing_pkgs)} pakettia puuttuu")

    # ── OLLAMA ─────────────────────────────────────────
    if verbose:
        print(f"\n  {W}Ollama:{X}")

    ollama_ok = False
    installed_models = set()
    try:
        installed_models = get_ollama_models()
        if installed_models:
            ollama_ok = True
            if verbose: ok(f"Ollama käynnissä ({len(installed_models)} mallia)")
            for m in sorted(installed_models):
                if verbose: ok(f"  {m}")
        else:
            if verbose: ok("Ollama käynnissä (ei malleja)")
            ollama_ok = True
    except Exception:
        errors.append("Ollama ei käynnissä tai puuttuu")
        if verbose: err("Ollama ei löydy")

    # Tarkista vaaditut mallit (settings.yaml:sta)
    required_models = deps.get("ollama_models", set())
    if ollama_ok and required_models:
        if verbose: print(f"\n  {W}Vaaditut mallit (settings.yaml):{X}")
        for model in sorted(required_models):
            # Tarkista onko asennettu (osittainen match)
            model_base = model.split(":")[0]
            found = any(model_base in m for m in installed_models)
            if found:
                if verbose: ok(f"  {model}")
            else:
                warnings.append(f"Malli puuttuu: {model}")
                if verbose: warn(f"  {model} → ollama pull {model}")

    # ── KRIITTISET TIEDOSTOT ───────────────────────────
    if verbose:
        print(f"\n  {W}Kriittiset tiedostot:{X}")
    for f in CRITICAL_FILES:
        p = PROJECT_DIR / f
        if p.exists():
            if verbose: ok(f"  {f}")
        else:
            warnings.append(f"Puuttuu: {f}")
            if verbose: warn(f"  {f} puuttuu")

    # ── YMPÄRISTÖMUUTTUJAT ─────────────────────────────
    if verbose:
        print(f"\n  {W}Ympäristömuuttujat:{X}")
    max_models = os.environ.get("OLLAMA_MAX_LOADED_MODELS", "")
    if max_models:
        if verbose: ok(f"  OLLAMA_MAX_LOADED_MODELS = {max_models}")
    else:
        warnings.append("OLLAMA_MAX_LOADED_MODELS ei asetettu")
        if verbose: warn("  OLLAMA_MAX_LOADED_MODELS ei asetettu (oletus: 1 = mallinvaihto!)")

    utf8 = os.environ.get("PYTHONUTF8", "")
    if utf8 == "1":
        if verbose: ok("  PYTHONUTF8 = 1")
    else:
        if verbose: warn("  PYTHONUTF8 ei asetettu → ääkkösongelma mahdollinen")

    # ── LEVYTILA ───────────────────────────────────────
    if verbose:
        print(f"\n  {W}Levytila:{X}")
    try:
        usage = shutil.disk_usage(str(PROJECT_DIR))
        free_gb = usage.free / (1024 ** 3)
        if free_gb > 5:
            if verbose: ok(f"  Vapaa: {free_gb:.1f} GB")
        elif free_gb > 1:
            if verbose: warn(f"  Vähän tilaa: {free_gb:.1f} GB")
        else:
            errors.append(f"Kriittinen: vain {free_gb:.2f} GB vapaana")
            if verbose: err(f"  KRIITTINEN: {free_gb:.2f} GB!")
    except Exception:
        pass

    # ── TIETOISUUSKERROS ────────────────────────────────
    if verbose:
        print(f"\n  {W}Tietoisuuskerros (v2):{X}")

    consciousness_ok = True
    consciousness_path = PROJECT_DIR / "consciousness.py"
    if consciousness_path.exists():
        if verbose: ok("  consciousness.py")
    else:
        consciousness_ok = False
        if verbose: warn("  consciousness.py puuttuu")

    translation_path = PROJECT_DIR / "translation_proxy.py"
    if translation_path.exists():
        if verbose: ok("  translation_proxy.py")
    else:
        if verbose: warn("  translation_proxy.py puuttuu")

    # nomic-embed-text
    if any("nomic-embed" in m for m in installed_models):
        if verbose: ok("  nomic-embed-text (Ollama)")
    else:
        warnings.append("nomic-embed-text puuttuu Ollamasta")
        if verbose: warn("  nomic-embed-text puuttuu → ollama pull nomic-embed-text")

    # ChromaDB
    chroma_path = PROJECT_DIR / "data" / "chroma_db"
    if chroma_path.exists():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(chroma_path))
            cols = client.list_collections()
            total = sum(c.count() for c in cols)
            if verbose: ok(f"  ChromaDB: {total} muistoa ({len(cols)} collectionia)")
        except Exception as e:
            if verbose: warn(f"  ChromaDB: {e}")
    else:
        if verbose: info("  ChromaDB tyhja (tayttyy automaattisesti)")

    # ── AGENTIT ────────────────────────────────────────
    if verbose:
        print(f"\n  {W}Agentit:{X}")
    # Tarkista sekä agents/ että knowledge/
    for agents_dir_name in ["agents", "knowledge"]:
        agents_dir = PROJECT_DIR / agents_dir_name
        if agents_dir.exists():
            agent_dirs = [d for d in agents_dir.iterdir() if d.is_dir()
                          and d.name not in ALWAYS_EXCLUDE_DIRS]
            agents_yaml = [d for d in agent_dirs if (d / "core.yaml").exists()]
            if verbose: ok(f"  {agents_dir_name}/: {len(agent_dirs)} kansiota, "
                           f"{len(agents_yaml)} core.yaml")

    # ── SUOSITUKSET ────────────────────────────────────
    rec = recommend_settings(hw)
    if verbose:
        print(f"\n  {W}Suositukset tälle raudalle:{X}")
        ok(f"  Chat-malli: {rec['chat_model']}")
        ok(f"  Heartbeat: {rec['heartbeat_model']} "
           f"({'CPU' if rec['heartbeat_num_gpu'] == 0 else 'GPU'})")
        ok(f"  Max rinnakkaiset: {rec['max_concurrent']}")
        for note in rec["notes"]:
            info(f"  {note}")

    # Yhteenveto
    if verbose:
        print(f"\n{W}{'═' * 55}{X}")
        if not errors and not warnings:
            print(f"  {G}✅ KAIKKI OK — ympäristö valmis käynnistykseen{X}")
        elif not errors:
            print(f"  {Y}⚠️  OK varoituksilla ({len(warnings)}){X}")
        else:
            print(f"  {R}❌ VIRHEITÄ: {len(errors)} | Varoituksia: {len(warnings)}{X}")
        print(f"{W}{'═' * 55}{X}\n")

    return (len(errors) == 0, errors, warnings, hw, rec)


# ═══════════════════════════════════════════════════════════════
# SMART BACKUP
# ═══════════════════════════════════════════════════════════════

def do_backup():
    """Älykkäästi pakkaa VAIN projektin tiedostot."""
    banner("VARMUUSKOPIOINTI (Smart)")

    # 1. Skannaa riippuvuudet
    info("Analysoidaan projektia...")
    deps = build_dependency_tree()
    project_files = deps["project_files"]

    # 2. Raportoi
    by_type = defaultdict(int)
    total_size = 0
    for rel in project_files:
        full = PROJECT_DIR / rel
        if full.exists():
            by_type[full.suffix or "(ei päätettä)"] += 1
            total_size += full.stat().st_size

    # Laske koodirivit tekstitiedostoista
    total_lines = 0
    lines_by_type = defaultdict(int)
    text_extensions = {
        ".py", ".yaml", ".yml", ".json", ".md", ".txt",
        ".html", ".css", ".js", ".jsx", ".bat", ".sh",
        ".toml", ".cfg", ".ini", ".ino", ".jsonl",
    }
    for rel in project_files:
        full = PROJECT_DIR / rel
        if full.exists() and full.suffix in text_extensions:
            try:
                n = full.read_text(encoding="utf-8", errors="replace").count("\n")
                total_lines += n
                lines_by_type[full.suffix] += n
            except Exception:
                pass

    print(f"  {W}Löydetyt tiedostot:{X}")
    for ext, count in sorted(by_type.items(), key=lambda x: -x[1]):
        line_info = f"  ({lines_by_type[ext]:,} riviä)" if ext in lines_by_type else ""
        print(f"    {ext:<12} {count:>4} kpl{line_info}")
    print(f"    {'─' * 20}")
    print(f"    {'Yhteensä':<12} {len(project_files):>4} kpl "
          f"({total_size / (1024*1024):.1f} MB, {total_lines:,} riviä koodia)")

    # 3. Ollama-mallit (tallennetaan metaan)
    ollama_models = deps["ollama_models"]
    if ollama_models:
        print(f"\n  {W}Ollama-mallit (settings.yaml):{X}")
        for m in sorted(ollama_models):
            print(f"    📦 {m}")

    # 4. Python-riippuvuudet (tallennetaan metaan)
    ext_deps = deps["python_deps"]
    if ext_deps:
        print(f"\n  {W}Python-riippuvuudet (skannattu):{X}")
        for d in sorted(ext_deps):
            pip_name = PIP_MAP.get(d, d)
            print(f"    📚 {pip_name}")

    # 5. Pakkaa
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"waggle_{timestamp}.zip"
    zip_path = BACKUP_DIR / zip_name

    print()
    info(f"Pakataan {len(project_files)} tiedostoa...")

    packed = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in sorted(project_files):
            full = PROJECT_DIR / rel
            if full.exists():
                try:
                    zf.write(full, str(rel))
                    packed += 1
                except (PermissionError, OSError) as e:
                    warn(f"Ohitettu: {rel} ({e})")

    zip_size = zip_path.stat().st_size
    ratio = (1 - zip_size / total_size) * 100 if total_size > 0 else 0

    # 6. Meta
    hw = detect_hardware()
    meta = {
        "version": "2.0",
        "timestamp": timestamp,
        "datetime": datetime.now().isoformat(),
        "files": packed,
        "size_bytes": total_size,
        "zip_size_bytes": zip_size,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "hardware": {
            "cpu": hw["cpu"],
            "ram_gb": hw["ram_gb"],
            "gpu": hw["gpu_name"],
            "vram_gb": hw["gpu_vram_gb"],
        },
        "ollama_models": sorted(ollama_models),
        "python_deps": sorted(PIP_MAP.get(d, d) for d in ext_deps),
        "file_types": dict(by_type),
    }
    meta_path = BACKUP_DIR / f"waggle_{timestamp}_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    ok(f"Varmuuskopio: {zip_name}")
    ok(f"Koko: {zip_size / (1024*1024):.1f} MB (pakkaus {ratio:.0f}%)")
    ok(f"Tiedostoja: {packed}")
    ok(f"Sijainti: {zip_path}")

    # ── Kopioi mass_test_results.json backups-kansioon ──
    _copy_mass_test_results(timestamp)

    # ── Kopioi backup 3 sijaintiin ──────────────────────
    _copy_backup_to_extra_locations(zip_path, meta_path)

    # ── Generoi AI Brief automaattisesti ─────────────
    print()
    do_ai_brief()

    print(f"\n  {G}Backup valmis!{X}\n")
    return zip_path


def _copy_mass_test_results(timestamp: str):
    """Kopioi mass_test_results.json backups-kansioon aikaleimatulla nimellä."""
    src = PROJECT_DIR / "data" / "mass_test_results.json"
    if not src.exists():
        return
    try:
        dest = BACKUP_DIR / f"mass_test_results_{timestamp}.json"
        shutil.copy2(src, dest)
        ok(f"Mass test tulokset: {dest.name}")
    except (PermissionError, OSError) as e:
        warn(f"Mass test tulosten kopiointi epäonnistui: {e}")


def _find_corsair_drive() -> str | None:
    """Etsi CORSAIR-niminen siirrettävä kovalevy Windowsissa."""
    if platform.system() != "Windows":
        return None
    try:
        result = subprocess.run(
            ["wmic", "logicaldisk", "get", "caption,volumename,drivetype"],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.strip().split("\n")[1:]:
            line = line.strip()
            if not line:
                continue
            # Parsitaan: "D:     2     CORSAIR"
            # DriveType 2 = removable, 3 = local fixed
            parts = line.split()
            if len(parts) >= 3:
                drive_letter = parts[0]
                drive_type = parts[1]
                vol_name = " ".join(parts[2:])
                # Hyväksy sekä removable (2) että fixed (3) jos nimi on CORSAIR
                if "CORSAIR" in vol_name.upper():
                    return drive_letter
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _copy_backup_to_extra_locations(zip_path: Path, meta_path: Path):
    r"""Kopioi backup kolmeen sijaintiin: .\backups, C:\, ja CORSAIR-levy."""
    extra_targets = []

    # Kohde 2: C:\python\WaggleDanceAiSwarm\backups
    c_drive = Path("C:/")
    if c_drive.exists():
        c_target = Path("C:/python/WaggleDanceAiSwarm/backups")
        extra_targets.append(("C:-asema", c_target))

    # Kohde 3: CORSAIR-siirrettävä kovalevy
    corsair = _find_corsair_drive()
    if corsair:
        corsair_target = Path(f"{corsair}/python/WaggleDanceAiSwarm/backups")
        extra_targets.append((f"CORSAIR ({corsair})", corsair_target))

    if not extra_targets:
        info("Lisäsijainteja ei löytynyt (C:-asema tai CORSAIR-levy)")
        return

    print(f"\n  {W}Kopioidaan backup lisäsijainteihin:{X}")
    for label, target_dir in extra_targets:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_zip = target_dir / zip_path.name
            target_meta = target_dir / meta_path.name
            shutil.copy2(zip_path, target_zip)
            shutil.copy2(meta_path, target_meta)
            ok(f"{label}: {target_zip}")

            # Kopioi itse backup-skripti yksi taso ylemmäksi (ei backups-kansioon)
            parent_dir = target_dir.parent  # .../WaggleDanceAiSwarm/
            script_src = SCRIPT_DIR / "openclaw_backup.py"
            if script_src.exists():
                shutil.copy2(script_src, parent_dir / "openclaw_backup.py")
                ok(f"{label}: {parent_dir / 'openclaw_backup.py'}")
        except (PermissionError, OSError) as e:
            warn(f"{label}: kopioiminen epäonnistui — {e}")


# ═══════════════════════════════════════════════════════════════
# SMART RESTORE
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# AI BRIEF — Keinoälyn ymmärtämä projektitiivistelmä
# ═══════════════════════════════════════════════════════════════

def do_ai_brief():
    """Generoi tiivis AI-luettava projektitiivistelmä juurikansioon."""
    banner("AI BRIEF — Projektitiivistelmä")

    info("Skannataan projektia...")
    deps = build_dependency_tree()
    hw = detect_hardware()

    # Kerää tiedot
    n_py = sum(1 for f in deps["project_files"] if str(f).endswith(".py"))
    n_yaml = sum(1 for f in deps["project_files"] if str(f).endswith((".yaml", ".yml")))
    n_jsx = sum(1 for f in deps["project_files"] if str(f).endswith(".jsx"))
    n_total = len(deps["project_files"])

    # Laske koodirivit
    total_lines = 0
    text_exts = {".py", ".yaml", ".yml", ".json", ".md", ".txt",
                 ".html", ".css", ".js", ".jsx", ".bat", ".sh",
                 ".toml", ".cfg", ".ini", ".ino", ".jsonl"}
    for rel in deps["project_files"]:
        full = PROJECT_DIR / rel
        if full.exists() and full.suffix in text_exts:
            try:
                total_lines += full.read_text(encoding="utf-8", errors="replace").count("\n")
            except Exception:
                pass

    # Laske agentit
    agent_count = 0
    agent_names = []
    for d in ["agents", "knowledge"]:
        agent_dir = PROJECT_DIR / d
        if agent_dir.exists():
            for sub in sorted(agent_dir.iterdir()):
                if sub.is_dir() and (sub / "core.yaml").exists():
                    agent_count += 1
                    agent_names.append(sub.name)
    agent_names = sorted(set(agent_names))

    # ChromaDB-koko
    chroma_facts = 0
    chroma_path = PROJECT_DIR / "data" / "chroma_db"
    if chroma_path.exists():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(chroma_path))
            cols = client.list_collections()
            chroma_facts = sum(c.count() for c in cols)
        except Exception:
            pass

    # Lue CHANGELOG.md muutokset
    changelog_summary = ""
    changelog_path = PROJECT_DIR / "CHANGELOG.md"
    if changelog_path.exists():
        try:
            text = changelog_path.read_text(encoding="utf-8", errors="replace")
            # Ota viimeiset ~2000 merkkiä
            changelog_summary = text[:2000]
        except Exception:
            pass

    # Kerää backend/routes/chat.py -tilastot
    chat_py = PROJECT_DIR / "backend" / "routes" / "chat.py"
    chat_stats = ""
    if chat_py.exists():
        try:
            content = chat_py.read_text(encoding="utf-8", errors="replace")
            n_responses = content.count("_RESPONSES")
            n_yaml_index = "YAML_INDEX" in content
            n_layers = content.count("Layer")
            chat_stats = (
                f"3-layer routing (Layer2A YAML high-conf -> Layer1 keywords -> "
                f"Layer2B YAML low-thresh -> fallback), "
                f"bidirectional F1 scoring, agent name bonus, "
                f"97.7% routing accuracy across {len(agent_names)} agents"
            )
        except Exception:
            pass

    # Kerää settings.yaml -mallit
    models_info = ""
    settings_path = PROJECT_DIR / "configs" / "settings.yaml"
    if settings_path.exists():
        try:
            import yaml
            with open(settings_path, encoding="utf-8") as f:
                settings = yaml.safe_load(f) or {}
            models_info = json.dumps(
                {k: v for k, v in settings.items()
                 if "model" in str(k).lower() or "ollama" in str(k).lower()},
                indent=2, ensure_ascii=False, default=str
            )[:500]
        except Exception:
            pass

    # Generoi tiivistelmä
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    brief = f"""# WAGGLEDANCE SWARM AI — PROJECT BRIEF FOR AI AGENTS
# Auto-generated: {timestamp}
# Purpose: Paste this into any AI prompt to get improvement suggestions.

## WHAT IS THIS
WaggleDance is a local-first, self-improving multi-agent AI system for Finnish
beekeeping (mehiläishoito). {agent_count} specialized agents communicate through a
HiveMind orchestrator with translation pipeline, consciousness layer, and
autonomous learning. All user I/O in Finnish, all LLM processing in English.

## HARDWARE
- GPU: {hw.get('gpu_name', 'N/A')} ({hw.get('gpu_vram_gb', 0)} GB VRAM)
- RAM: {hw.get('ram_gb', 0)} GB
- OS: {hw.get('os', 'N/A')}
- Python: {hw.get('python', 'N/A')}

## CODEBASE STATS
- Files: {n_total} ({n_py} .py, {n_yaml} .yaml, {n_jsx} .jsx) — {total_lines:,} lines of code
- Agents: {agent_count} ({', '.join(agent_names[:10])}, ...)
- ChromaDB facts: {chroma_facts}
- Ollama models: {', '.join(sorted(deps.get('ollama_models', set())))}
- Python deps: {', '.join(sorted(deps.get('python_deps', set()))[:20])}

## ARCHITECTURE
```
User (Finnish) -> FastAPI backend (port 8000)
  +-- Chat Router: 3-layer matching system
  |   Layer 2A: YAML eval_questions (high confidence, F1 >= 0.5, overlap >= 2)
  |   Layer 1:  Hand-crafted Finnish keyword matching (47 entries)
  |   Layer 2B: YAML eval_questions (lower threshold, F1 >= 0.4)
  |   Layer 3:  Fallback messages
  +-- HiveMind orchestrator (hivemind.py ~1400 lines)
  |   +-- Priority Lock: chat always wins, pauses background
  |   +-- Heartbeat: llama3.2:1b guided learning tasks
  |   +-- Round Table: 6 agents discuss + cross-validate
  +-- Consciousness v2 (consciousness.py ~500 lines)
  |   +-- MathSolver pre-filter (0ms)
  |   +-- ChromaDB vector memory (bilingual)
  |   +-- Dual embedding: nomic (search) + minilm (eval)
  |   +-- Hallucination detection (contrastive + keyword)
  +-- Translation: Opus-MT fi<->en (force_opus for chat)
  +-- Dashboard: Vite + React (port 5173)
  +-- 50 YAML agent knowledge bases (knowledge/ + agents/)
```

## CURRENT ROUTING PERFORMANCE
{chat_stats}

## COMPLETED PHASES
- Phase 1: Foundation (consciousness v2, dual embed, smart router)
- Phase 2: Batch Pipeline (94% benchmark, 3147+ facts in ChromaDB)
- Chat routing: 97.7% accuracy across 1235 eval_questions from 50 agents

## IN PROGRESS
- Phase 3: Social Learning (Round Table, agent levels, night mode)
- Phase 4: Advanced Learning (contrastive, active, RAG, episodic)

## PLANNED (not started)
- Phase 5: Frigate Camera Integration (MQTT, PTZ, visual learning)
- Phase 6: Environmental Audio (ESP32, BirdNET, BeeMonitor)
- Phase 7: Voice Interface (Whisper STT + Piper TTS)
- Phase 8: External Data Feeds (FMI weather, electricity spot, RSS)
- Phase 9: Autonomous Learning Engine (6 layers: cache, enrichment, web, distill, meta, code-review)
- Phase 10: Micro-Model Training (pattern match -> classifier -> LoRA fine-tune)
- Phase 11: Elastic Hardware Scaling (auto-detect GPU/RAM, tier configuration)

## KEY DESIGN PRINCIPLES
1. Speed + Memory replaces model size (phi4-mini 3.8B with good memory > 32B without)
2. Batch background, stream foreground (Theater Pipe: batch hidden, 300ms delays)
3. Cross-validation > model intelligence (6 agents checking each other)
4. Chat always wins (PriorityLock pauses all background)
5. Learn from everything (YAML, conversations, corrections, web, cameras, audio)
6. Autonomous evolution (identify gaps, fill, validate, optimize, suggest code changes)

## KEY FILES
- main.py: Entry point
- hivemind.py: Core orchestrator (~1400 lines)
- consciousness.py: Memory + learning engine (~500 lines)
- translation_proxy.py: FI<->EN translation (~400 lines)
- backend/routes/chat.py: Chat endpoint with 3-layer routing
- dashboard/src/App.jsx: React dashboard UI
- configs/settings.yaml: System configuration
- core/yaml_bridge.py, core/llm_provider.py, core/en_validator.py
- agents/*/core.yaml: Agent knowledge bases (50 agents)
- knowledge/*/core.yaml: Domain knowledge bases

## RECENT IMPROVEMENTS (latest session)
- Chat routing accuracy: 73.5% -> 80.6% -> 92.3% -> 97.7% (4 rounds)
- Bidirectional F1 scoring with agent name bonus and depth bonus
- _resolve_ref fallback: tries parent paths and alternative fields
- Dedup fix: only mark question as seen after valid answer resolves
- Generic cross-agent question filtering (skip identical questions)
- Concurrent mass testing (10 workers, 5 q/s)
- YAML index: 5-tuple with agent name tokens for better matching
- Stop words expanded for Finnish action verbs

## CONSTRAINTS
- All LLM internally ENGLISH, Finnish only for user I/O
- Opus-MT for translation (force_opus=True for chat)
- nomic-embed-text: "search_document:"/"search_query:" prefixes
- phi4-mini ONLY for chat, llama3.2:1b for ALL background
- TranslationResult.text — never concatenate directly
- UTF-8 everywhere, Windows 11 compatible
- GPU budget: 4.3G/8.0G (54%) — 3.7G free

## WHAT WOULD MAKE THIS BETTER?
Think about:
1. How to improve agent routing beyond 97.7%? (remaining failures are shared-knowledge overlaps)
2. How to make the Round Table protocol produce higher-quality cross-validated wisdom?
3. How to implement autonomous fact enrichment that generates AND validates new knowledge?
4. How to make the system learn from its own mistakes (contrastive learning from corrections)?
5. How to reduce response latency (currently: cache 5ms, chromadb 55ms, llm 500-3000ms)?
6. How to implement micro-model training that progressively replaces LLM calls?
7. What architectural changes would enable true autonomous self-improvement?
8. How to handle Finnish morphology better in keyword matching (14 cases, compound words)?

Suggest specific code changes, new algorithms, or architectural improvements.
"""

    AI_BRIEF_PATH.write_text(brief, encoding="utf-8")
    ok(f"AI Brief: {AI_BRIEF_PATH}")
    ok(f"Koko: {len(brief)} merkkia")
    info("Kopioi tama tiedosto mihin tahansa AI-promptiin parannusehdotuksia varten")
    print()
    return AI_BRIEF_PATH


def list_backups():
    """Listaa kaikki varmuuskopiot kaikista sijainneista."""
    # Kerää backupit kaikista tunnetuista sijainneista
    search_dirs = [BACKUP_DIR]
    c_backup = Path("C:/python/WaggleDanceAiSwarm/backups")
    if c_backup.exists():
        search_dirs.append(c_backup)
    corsair = _find_corsair_drive()
    if corsair:
        corsair_backup = Path(f"{corsair}/python/WaggleDanceAiSwarm/backups")
        if corsair_backup.exists():
            search_dirs.append(corsair_backup)

    # Tukee sekä vanhaa (openclaw_*) että uutta (waggle_*) nimeämistä
    all_zips = []
    for search_dir in search_dirs:
        all_zips.extend(search_dir.glob("openclaw_*.zip"))
        all_zips.extend(search_dir.glob("waggle_*.zip"))

    # Dedup by filename (sama backup voi olla useassa paikassa)
    seen_names = set()
    zips = []
    for z in sorted(all_zips, key=lambda z: z.stat().st_mtime, reverse=True):
        if z.name not in seen_names:
            seen_names.add(z.name)
            zips.append(z)
    if not zips:
        warn("Ei varmuuskopioita löytynyt")
        return []

    banner(f"VARMUUSKOPIOT ({BACKUP_DIR})")
    print(f"  {'#':<4} {'Päivämäärä':<22} {'Koko':>10}  {'Tiedosto'}")
    print(f"  {'─' * 65}")

    for i, z in enumerate(zips, 1):
        size_mb = z.stat().st_size / (1024 * 1024)
        # Parse datetime
        name = z.stem
        for prefix in ["waggle_", "openclaw_"]:
            name = name.replace(prefix, "")
        parts = name.split("_")
        date_str = "?"
        if len(parts) >= 2:
            try:
                dt = datetime.strptime(f"{parts[0]}_{parts[1]}", "%Y%m%d_%H%M%S")
                date_str = dt.strftime("%d.%m.%Y %H:%M:%S")
            except ValueError:
                pass

        # Meta
        meta_str = ""
        for meta_name in [f"{z.stem}_meta.json"]:
            meta_path = z.parent / meta_name
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    files = meta.get("files", "?")
                    meta_str = f"  ({files} tiedostoa)"
                    if meta.get("version") == "2.0":
                        meta_str += f" {G}[Smart]{X}"
                except Exception:
                    pass

        print(f"  {i:<4} {date_str:<22} {size_mb:>8.1f} MB  {z.name}{meta_str}")

    print()
    return zips


def do_restore():
    """Älykkäästi palauttaa + asentaa ympäristön."""
    banner("PALAUTUS VARMUUSKOPIOSTA")

    # ── 1. Rauta-analyysi ──────────────────────────────
    print(f"  {W}Vaihe 1/5: Rauta-analyysi{X}")
    hw = detect_hardware()
    print_hardware(hw)
    rec = recommend_settings(hw)

    # ── 2. Valitse arkisto ─────────────────────────────
    print(f"  {W}Vaihe 2/5: Valitse arkisto{X}")
    zips = list_backups()
    if not zips:
        return

    try:
        choice = input(f"  {W}Valitse numero (Enter = uusin): {X}").strip()
        idx = 0 if choice == "" else int(choice) - 1
        selected = zips[idx]
    except (ValueError, IndexError):
        err("Virheellinen valinta")
        return

    # Lue meta
    meta = {}
    meta_path = selected.parent / f"{selected.stem}_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    print(f"\n  {Y}⚠️  VAROITUS: Korvaa nykyiset projektitiedostot!{X}")
    print(f"  Arkisto: {selected.name}")
    if meta.get("files"):
        print(f"  Tiedostoja: {meta['files']}")
    confirm = input(f"  {W}Jatketaanko? (KYLLÄ/ei): {X}").strip()
    if confirm != "KYLLÄ":
        print("  Keskeytetty.\n")
        return

    # ── 3. Varmuuskopioi nykyinen ──────────────────────
    print(f"\n  {W}Vaihe 3/5: Nykyisen tilan varmuuskopio{X}")
    current_backup = do_backup()

    # ── 4. Pura arkisto ────────────────────────────────
    print(f"\n  {W}Vaihe 4/5: Puretaan {selected.name}{X}\n")

    with zipfile.ZipFile(selected, "r") as zf:
        file_list = zf.namelist()
        info(f"Puretaan {len(file_list)} tiedostoa...")

        for member in file_list:
            if "backups/" in member or "backups\\" in member:
                continue
            target = PROJECT_DIR / member
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(member) as src, open(target, "wb") as dst:
                    dst.write(src.read())
            except (PermissionError, OSError) as e:
                warn(f"Ohitettu: {member} ({e})")

    # Tyhjennä välimuisti
    cleared = 0
    for p in PROJECT_DIR.rglob("__pycache__"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            cleared += 1
    ok(f"Purettu + välimuisti tyhjennetty ({cleared} __pycache__)")

    # ── 5. Ympäristön setup (tuetaan täysin vierasta konetta) ──
    print(f"\n  {W}Vaihe 5/7: Python-riippuvuudet{X}")

    # 5a. Generoi requirements.txt puretusta koodista
    _generate_requirements_txt()

    # 5b. Pip-paketit — skannaa mitä oikeasti puuttuu
    missing_pkgs = []
    # Ensin meta-tiedoston paketit
    meta_deps = meta.get("python_deps", [])
    # Sitten skannaa purettu koodi
    deps = build_dependency_tree()
    all_deps = set(meta_deps)
    for imp in deps["python_deps"]:
        all_deps.add(PIP_MAP.get(imp, imp))
    for imp in PIP_MAP:
        all_deps.add(PIP_MAP[imp])

    for pkg in sorted(all_deps):
        import_name = pkg.replace("-", "_")
        try:
            __import__(import_name)
        except ImportError:
            missing_pkgs.append(pkg)

    if missing_pkgs:
        print(f"\n  {W}Asennetaan {len(missing_pkgs)} puuttuvaa pakettia:{X}")
        # Yritä ensin requirements.txt:stä kerralla
        req_path = PROJECT_DIR / "requirements.txt"
        if req_path.exists():
            info("Asennetaan requirements.txt:stä...")
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", str(req_path)],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                ok("requirements.txt asennettu")
            else:
                warn("requirements.txt osittain epäonnistui, yritetään yksittäin...")
                for pkg in missing_pkgs:
                    _pip_install(pkg)
        else:
            for pkg in missing_pkgs:
                _pip_install(pkg)
    else:
        ok("Kaikki Python-paketit asennettu")

    # ── 6. Node.js / Dashboard ────────────────────────
    print(f"\n  {W}Vaihe 6/7: Dashboard (Node.js){X}")
    dashboard_dir = PROJECT_DIR / "dashboard"
    if dashboard_dir.exists() and (dashboard_dir / "package.json").exists():
        node_modules = dashboard_dir / "node_modules"
        if node_modules.exists():
            ok("Dashboard node_modules löytyy")
        else:
            # Tarkista onko Node.js asennettu
            node_ok = False
            try:
                result = subprocess.run(
                    ["node", "--version"],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    ok(f"Node.js {result.stdout.strip()}")
                    node_ok = True
            except FileNotFoundError:
                pass

            if node_ok:
                info("Asennetaan dashboard-riippuvuudet (npm install)...")
                try:
                    result = subprocess.run(
                        ["npm", "install"],
                        cwd=str(dashboard_dir),
                        capture_output=True, text=True, timeout=120
                    )
                    if result.returncode == 0:
                        ok("npm install valmis")
                    else:
                        warn(f"npm install epäonnistui: {result.stderr[:200]}")
                except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                    warn(f"npm install: {e}")
            else:
                warn("Node.js ei asennettu — dashboard ei toimi")
                info("Asenna: https://nodejs.org/ (LTS-versio)")
                info("Sen jälkeen: cd dashboard && npm install")
    else:
        info("Dashboard-kansiota ei löytynyt")

    # ── 7. Ympäristömuuttujat ────────────────────────
    print(f"\n  {W}Vaihe 7/7: Ympäristö + Ollama + tietoisuuskerros{X}")
    ensure_environment(hw, rec)

    # 7a. Ollama-asennus ja mallit
    required = set(meta.get("ollama_models", []))
    if not required:
        required = deps.get("ollama_models", set())

    # Tarkista onko Ollama asennettu
    ollama_ok = False
    try:
        result = subprocess.run(
            ["ollama", "--version"], capture_output=True, text=True, timeout=5
        )
        ollama_ok = result.returncode == 0
    except FileNotFoundError:
        pass

    if not ollama_ok:
        warn("Ollama ei löydy — asenna ensin: https://ollama.ai")
        info("Käynnistä Ollama ja aja restore uudelleen mallien lataamiseksi")
    else:
        ok("Ollama asennettu")
        if required:
            print(f"\n  {W}Ollama-mallit:{X}")
            installed = get_ollama_models()
            for model in sorted(required):
                model_base = model.split(":")[0]
                found = any(model_base in m for m in installed)
                if found:
                    ok(f"  {model} (jo ladattu)")
                else:
                    answer = input(f"  {Y}Ladataanko {model}? (k/e): {X}").strip().lower()
                    if answer in ("k", ""):
                        pull_model(model)
                    else:
                        warn(f"  Ohitettu: {model}")

        # nomic-embed-text
        installed = get_ollama_models()
        if not any("nomic-embed" in m for m in installed):
            answer = input(f"  {Y}Ladataanko nomic-embed-text (embedding-malli)? (k/e): {X}").strip().lower()
            if answer in ("k", ""):
                pull_model("nomic-embed-text")
        else:
            ok("nomic-embed-text (jo ladattu)")

    # 7b. Opus-MT HuggingFace
    print(f"\n  {W}Tietoisuuskerros:{X}")
    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    opus_fi_en = any("opus-mt-fi-en" in str(d) for d in hf_cache.glob("*")) if hf_cache.exists() else False
    opus_en_fi = any("opus-mt-en-fi" in str(d) for d in hf_cache.glob("*")) if hf_cache.exists() else False

    if opus_fi_en and opus_en_fi:
        ok("Opus-MT fi<->en (HuggingFace-cachessa)")
    else:
        info("Opus-MT fi<->en ladataan automaattisesti ensimmaisella kaynnistyksella")
        info("(Helsinki-NLP/opus-mt-fi-en + opus-mt-en-fi)")

    # 7c. ChromaDB-tarkistus
    chroma_path = PROJECT_DIR / "data" / "chroma_db"
    if chroma_path.exists():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(chroma_path))
            cols = client.list_collections()
            total = sum(c.count() for c in cols)
            ok(f"ChromaDB: {len(cols)} collectionia, {total} muistoa")
        except Exception as e:
            warn(f"ChromaDB: {e}")
    else:
        info("ChromaDB tyhja — tayttyy automaattisesti kayton myota")

    # ── VALMIS ─────────────────────────────────────────
    print(f"\n{W}{'═' * 55}{X}")
    print(f"  {G}PALAUTUS VALMIS!{X}")
    print()
    ok(f"Arkisto: {selected.name}")
    ok(f"Edellinen tila: {current_backup.name}")
    ok(f"Chat: {rec['chat_model']} (GPU)")
    ok(f"Heartbeat: {rec['heartbeat_model']} "
       f"({'CPU' if rec['heartbeat_num_gpu'] == 0 else 'GPU'})")
    ok(f"OLLAMA_MAX_LOADED_MODELS = {rec['max_loaded_models']}")
    print()
    if not ollama_ok:
        warn("Muista asentaa Ollama: https://ollama.ai")
    print(f"  {G}Kaynnista: python main.py{X}")
    print(f"  {G}      tai: start.bat{X}")
    print(f"{W}{'═' * 55}{X}\n")


def _pip_install(pkg: str):
    """Asenna yksittäinen pip-paketti (yhteensopiva kaikille ympäristöille)."""
    info(f"  Asennetaan {pkg}...")
    try:
        # Yritä ensin ilman --break-system-packages
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            ok(f"  {pkg} asennettu")
            return
        # Fallback: --break-system-packages (Linux/uusi pip)
        if "externally-managed" in result.stderr:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--break-system-packages", pkg],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                ok(f"  {pkg} asennettu (--break-system-packages)")
                return
        err(f"  {pkg}: {result.stderr[:200]}")
    except Exception as e:
        err(f"  {pkg}: {e}")


def _generate_requirements_txt():
    """Generoi requirements.txt projektin riippuvuuksista."""
    req_path = PROJECT_DIR / "requirements.txt"
    all_pkgs = set()
    for imp_name, pip_name in PIP_MAP.items():
        all_pkgs.add(pip_name)
    # Skannaa projektin tiedostoista
    deps = build_dependency_tree()
    for imp in deps["python_deps"]:
        all_pkgs.add(PIP_MAP.get(imp, imp))

    lines = sorted(all_pkgs)
    req_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok(f"requirements.txt generoitu ({len(lines)} pakettia)")


# ═══════════════════════════════════════════════════════════════
# AUTO BACKUP — Ajetaan ajastimella, tekee backupin vain jos muutoksia
# ═══════════════════════════════════════════════════════════════

def _get_last_backup_time() -> float:
    """Palauttaa viimeisimmän backupin aikaleiman (epoch seconds), tai 0."""
    zips = list(BACKUP_DIR.glob("waggle_*.zip"))
    if not zips:
        return 0.0
    newest = max(zips, key=lambda z: z.stat().st_mtime)
    return newest.stat().st_mtime


def _get_newest_project_mtime() -> float:
    """Palauttaa projektin uusimman tiedoston muokkausajan."""
    newest = 0.0
    text_exts = PROJECT_EXTENSIONS
    for ext in text_exts:
        for f in PROJECT_DIR.rglob(f"*{ext}"):
            rel = f.relative_to(PROJECT_DIR)
            parts = rel.parts
            if any(ex in parts for ex in ALWAYS_EXCLUDE_DIRS):
                continue
            if any(rel.name.startswith(pat) for pat in STALE_FILE_PATTERNS):
                continue
            try:
                mtime = f.stat().st_mtime
                if mtime > newest:
                    newest = mtime
            except OSError:
                pass
    return newest


def do_auto_backup():
    """Automaattinen backup — tekee backupin vain jos projektia on muokattu."""
    last_backup = _get_last_backup_time()
    newest_file = _get_newest_project_mtime()

    if last_backup > 0 and newest_file <= last_backup:
        # Ei muutoksia — ohitetaan hiljaisesti
        timestamp = datetime.fromtimestamp(last_backup).strftime("%d.%m.%Y %H:%M")
        print(f"  {DIM}Auto-backup: ei muutoksia viime backupin jälkeen ({timestamp}){X}")
        return None

    # Muutoksia löytyi — ajetaan backup
    if last_backup > 0:
        from_time = datetime.fromtimestamp(last_backup).strftime("%H:%M")
        to_time = datetime.fromtimestamp(newest_file).strftime("%H:%M")
        info(f"Muutoksia havaittu ({from_time} -> {to_time}), ajetaan backup...")
    else:
        info("Ensimmäinen backup, ajetaan...")

    return do_backup()


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def print_usage():
    print(f"""
{W}WaggleDance — Smart Backup & Restore v3.0{X}

Käyttö:
  python openclaw_backup.py {G}backup{X}    Pakkaa projekti + generoi AI Brief
  python openclaw_backup.py {G}auto{X}      Automaattinen backup (vain jos muutoksia)
  python openclaw_backup.py {G}restore{X}   Palauta + asenna ympäristö
  python openclaw_backup.py {G}check{X}     Rauta + ympäristöanalyysi
  python openclaw_backup.py {G}list{X}      Listaa varmuuskopiot
  python openclaw_backup.py {G}brief{X}     Generoi AI-tiivistelmä (mille tahansa AI:lle)
""")


def main():
    if len(sys.argv) < 2:
        print_usage()
        return

    cmd = sys.argv[1].lower()

    if cmd == "backup":
        do_backup()
    elif cmd == "auto":
        do_auto_backup()
    elif cmd == "restore":
        do_restore()
    elif cmd == "check":
        check_environment(verbose=True)
    elif cmd == "list":
        list_backups()
    elif cmd == "brief":
        do_ai_brief()
    else:
        print_usage()


if __name__ == "__main__":
    main()
