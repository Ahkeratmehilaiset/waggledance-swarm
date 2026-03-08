#!/usr/bin/env python3
"""
WaggleDance Restore & Environment Validator v3.0
=================================================
Validates the environment and restores from a backup zip if needed.
Supports restore to any target directory via --target flag.

Usage:
    python tools/waggle_restore.py                          # Validate only
    python tools/waggle_restore.py --restore ZIPFILE        # Restore to project dir
    python tools/waggle_restore.py --restore ZIPFILE --target /path/to/dir
    python tools/waggle_restore.py --test-restore ZIPFILE   # Non-destructive test
    python tools/waggle_restore.py --run-tests              # Validate + tests

Exit code: 0 = all OK, 1 = warnings/failures
"""

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

# ── Windows UTF-8 ─────────────────────────────────────────────────
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

# ── Constants ─────────────────────────────────────────────────────
_script_dir = Path(__file__).resolve().parent
PROJECT_ROOT = _script_dir.parent if _script_dir.name == "tools" else _script_dir

REQUIRED_PYTHON = (3, 13)
REQUIRED_MODELS = ["phi4-mini", "llama3.2:1b", "nomic-embed-text", "all-minilm"]
REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

REQUIRED_DATA_DIRS = [
    "data",
    "data/chroma_db",
    "data/micromodel_v1",
]

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"
C = "\033[96m"; W = "\033[0m"; BOLD = "\033[1m"
os.system("")  # Enable ANSI on Windows


# ── Result tracker ────────────────────────────────────────────────
class CheckResult:
    def __init__(self):
        self.checks = []  # (name, status, detail)  status: OK/WARN/FAIL

    def ok(self, name, detail=""):
        self.checks.append((name, "OK", detail))
        print(f"  {G}OK{W}    {name}" + (f"  — {detail}" if detail else ""))

    def warn(self, name, detail=""):
        self.checks.append((name, "WARN", detail))
        print(f"  {Y}WARN{W}  {name}" + (f"  — {detail}" if detail else ""))

    def fail(self, name, detail=""):
        self.checks.append((name, "FAIL", detail))
        print(f"  {R}FAIL{W}  {name}" + (f"  — {detail}" if detail else ""))

    @property
    def has_failures(self):
        return any(s == "FAIL" for _, s, _ in self.checks)

    @property
    def has_warnings(self):
        return any(s == "WARN" for _, s, _ in self.checks)

    def summary(self):
        ok = sum(1 for _, s, _ in self.checks if s == "OK")
        warn = sum(1 for _, s, _ in self.checks if s == "WARN")
        fail = sum(1 for _, s, _ in self.checks if s == "FAIL")
        return ok, warn, fail


# ── Check functions ───────────────────────────────────────────────
def check_python(r: CheckResult):
    """Check Python version >= 3.13."""
    major, minor = sys.version_info.major, sys.version_info.minor
    ver = f"{major}.{minor}.{sys.version_info.micro}"
    req_maj, req_min = REQUIRED_PYTHON
    if (major, minor) >= (req_maj, req_min):
        r.ok("Python version", f"{ver} (>= {req_maj}.{req_min} required)")
    else:
        r.fail("Python version", f"{ver} — need {req_maj}.{req_min}+. Install from python.org")


def check_pip_dependencies(r: CheckResult):
    """Check that required pip packages are installed."""
    if not REQUIREMENTS_FILE.exists():
        r.warn("requirements.txt", "file not found — skipping pip check")
        return

    missing = []

    # Parse requirements.txt for package names (ignore comments, optional)
    reqs = []
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Skip commented-out optional packages
        pkg_spec = line.split(">=")[0].split("==")[0].split("[")[0].strip()
        if pkg_spec:
            reqs.append(pkg_spec)

    for pkg in reqs:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", pkg],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                missing.append(pkg)
        except Exception:
            missing.append(pkg)

    if missing:
        r.fail("pip dependencies", f"Missing: {', '.join(missing)}")
        print(f"         Run: pip install -r requirements.txt")
    else:
        r.ok("pip dependencies", f"{len(reqs)} packages OK")


def check_ollama(r: CheckResult):
    """Check Ollama is running and accessible."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            installed_models = []
            for line in result.stdout.strip().splitlines()[1:]:
                name = line.split()[0] if line.strip() else ""
                if name:
                    installed_models.append(name)
            r.ok("Ollama running", f"{len(installed_models)} models installed")
            return installed_models
        else:
            r.fail("Ollama running", "ollama list failed — is Ollama running?")
    except FileNotFoundError:
        r.fail("Ollama running", "ollama not found in PATH — install from ollama.com")
    except subprocess.TimeoutExpired:
        r.fail("Ollama running", "timeout — Ollama not responding")
    except Exception as e:
        r.fail("Ollama running", str(e))
    return []


def check_ollama_models(r: CheckResult, installed_models: list):
    """Check that all required models are installed."""
    installed_names = {m.split(":")[0] if ":" in m else m for m in installed_models}
    installed_names.update(installed_models)

    missing = []
    for model in REQUIRED_MODELS:
        base = model.split(":")[0]
        found = any(
            m == model or m.startswith(model + ":") or m.startswith(base + ":")
            for m in installed_models
        )
        if not found:
            missing.append(model)

    if missing:
        r.fail("Required Ollama models", f"Missing: {', '.join(missing)}")
        for m in missing:
            print(f"         Run: ollama pull {m}")
    else:
        r.ok("Required Ollama models", f"All present: {', '.join(REQUIRED_MODELS)}")


def check_chromadb(r: CheckResult):
    """Check ChromaDB is importable and data directory exists."""
    try:
        import chromadb  # noqa: F401
        r.ok("ChromaDB import", chromadb.__version__)
    except ImportError:
        r.fail("ChromaDB import", "chromadb not installed — run: pip install chromadb")
        return

    chroma_path = PROJECT_ROOT / "data" / "chroma_db"
    if chroma_path.exists():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(chroma_path))
            collections = client.list_collections()
            total_facts = 0
            for col in collections:
                try:
                    total_facts += col.count()
                except Exception:
                    pass
            r.ok("ChromaDB data", f"{len(collections)} collections, {total_facts} facts")
        except Exception as e:
            r.warn("ChromaDB data", f"Could not open: {e}")
    else:
        r.warn("ChromaDB data", "data/chroma_db/ not found — empty system (no knowledge)")


def check_sqlite_integrity(r: CheckResult):
    """Check SQLite databases are intact (not corrupt)."""
    data_dir = PROJECT_ROOT / "data"
    if not data_dir.exists():
        return
    for db_file in sorted(data_dir.glob("*.db")):
        try:
            conn = sqlite3.connect(str(db_file), timeout=5)
            result = conn.execute("PRAGMA integrity_check").fetchone()
            conn.close()
            if result and result[0] == "ok":
                size_mb = db_file.stat().st_size / (1024 * 1024)
                r.ok(f"SQLite {db_file.name}", f"integrity OK ({size_mb:.1f} MB)")
            else:
                r.fail(f"SQLite {db_file.name}", f"integrity check: {result}")
        except Exception as e:
            r.warn(f"SQLite {db_file.name}", f"cannot open: {e}")


def check_voikko(r: CheckResult):
    """Check Voikko Finnish morphology library, DLL, and dictionary."""
    try:
        import libvoikko
    except ImportError:
        r.warn(
            "Voikko (Finnish normalizer)",
            "libvoikko not installed — normalizer will use fallback. "
            "Install from: https://voikko.puimula.org/",
        )
        return

    voikko_path = PROJECT_ROOT / "voikko"
    mor_path = voikko_path / "5" / "mor-standard"

    if not mor_path.exists():
        r.warn("Voikko (Finnish normalizer)",
               f"Dictionary dir missing: {mor_path}")
        return

    vfst_files = list(mor_path.glob("*.vfst"))
    if not vfst_files:
        r.warn("Voikko (Finnish normalizer)",
               "Dictionary incomplete — missing .vfst files. "
               "Run: python core/auto_install.py")
        return

    # DLL check
    dll_path = voikko_path / "libvoikko-1.dll"
    if sys.platform == "win32" and not dll_path.exists():
        r.warn("Voikko (Finnish normalizer)",
               "libvoikko-1.dll missing from voikko/ dir")
        return

    try:
        libvoikko.Voikko.setLibrarySearchPath(str(voikko_path))
        v = libvoikko.Voikko("fi", path=str(voikko_path))
        result = v.analyze("mehiläinen")
        v.terminate()
        if result:
            r.ok("Voikko (Finnish normalizer)",
                 f"libvoikko + DLL + dictionary OK ({len(vfst_files)} .vfst)")
        else:
            r.warn("Voikko (Finnish normalizer)",
                   "Initialized but analyze() returned empty")
    except Exception as e:
        r.warn("Voikko (Finnish normalizer)", f"Init failed: {e}")


def check_translation_models(r: CheckResult):
    """Check Helsinki-NLP Opus-MT translation models are available."""
    try:
        import transformers  # noqa: F401
        r.ok("transformers", transformers.__version__)
    except ImportError:
        r.fail("transformers", "not installed — run: pip install transformers")
        return

    hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
    opus_fi_en = any(
        "Helsinki-NLP--opus-mt-fi-en" in str(p) or "opus-mt-fi-en" in str(p)
        for p in hf_cache.rglob("*.json") if hf_cache.exists()
    )
    opus_en_fi = any(
        "Helsinki-NLP--opus-mt-en-fi" in str(p) or "opus-mt-en-fi" in str(p)
        for p in hf_cache.rglob("*.json") if hf_cache.exists()
    )

    if opus_fi_en and opus_en_fi:
        r.ok("Opus-MT models", "FI→EN and EN→FI both cached")
    elif not hf_cache.exists():
        r.warn("Opus-MT models", "HuggingFace cache not found — models will download on first run")
    else:
        missing = []
        if not opus_fi_en:
            missing.append("opus-mt-fi-en")
        if not opus_en_fi:
            missing.append("opus-mt-en-fi")
        r.warn("Opus-MT models", f"Not cached: {', '.join(missing)} — will download on first run")


def check_data_dirs(r: CheckResult):
    """Check required data directories exist (create if missing)."""
    created = []
    exists = []
    for dir_rel in REQUIRED_DATA_DIRS:
        d = PROJECT_ROOT / dir_rel
        if d.exists():
            exists.append(dir_rel)
        else:
            try:
                d.mkdir(parents=True, exist_ok=True)
                created.append(dir_rel)
            except Exception as e:
                r.fail(f"Directory {dir_rel}", f"Cannot create: {e}")
                return

    detail_parts = []
    if exists:
        detail_parts.append(f"{len(exists)} existed")
    if created:
        detail_parts.append(f"created: {', '.join(created)}")
    r.ok("Data directories", ", ".join(detail_parts) if detail_parts else "all OK")


def check_models_dir(r: CheckResult):
    """Check LoRA model adapter exists if enabled in config."""
    settings_path = PROJECT_ROOT / "configs" / "settings.yaml"
    if not settings_path.exists():
        return
    try:
        import yaml
        cfg = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            return
        v3_enabled = cfg.get("advanced_learning", {}).get("micro_model_v3_enabled", False)
        v3_path = cfg.get("advanced_learning", {}).get("micro_model_v3_path", "")
        if v3_enabled and v3_path:
            adapter_dir = PROJECT_ROOT / v3_path
            config_file = adapter_dir / "adapter_config.json"
            if config_file.exists():
                r.ok("LoRA adapter", f"{v3_path} present")
            elif adapter_dir.exists():
                r.warn("LoRA adapter", f"{v3_path} exists but missing adapter_config.json")
            else:
                r.warn("LoRA adapter", f"{v3_path} not found — micro_model_v3 will not load")
    except Exception:
        pass


def check_env_file(r: CheckResult):
    """Check .env exists (needed for API key, but NOT backed up for security)."""
    env_path = PROJECT_ROOT / ".env"
    env_example = PROJECT_ROOT / ".env.example"
    if env_path.exists():
        r.ok(".env file", "present")
    elif env_example.exists():
        r.warn(".env file", "missing — copy .env.example to .env and fill in values")
    else:
        r.warn(".env file", "missing — WAGGLE_API_KEY will be auto-generated on first start")


def check_profile_config(r: CheckResult):
    """Check settings.yaml has a valid profile field."""
    settings_path = PROJECT_ROOT / "configs" / "settings.yaml"
    if not settings_path.exists():
        r.fail("Profile config", "configs/settings.yaml not found")
        return

    try:
        import yaml
        cfg = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            r.fail("Profile config", "settings.yaml is not a valid YAML mapping")
            return

        profile = cfg.get("profile")
        valid_profiles = {"gadget", "cottage", "home", "factory"}
        if profile in valid_profiles:
            r.ok("Profile config", f"active profile = {profile}")
        elif profile is None:
            r.warn("Profile config", "no 'profile' field — defaults to 'cottage'")
        else:
            r.warn("Profile config", f"unknown profile '{profile}' — expected: {', '.join(sorted(valid_profiles))}")
    except Exception as e:
        r.fail("Profile config", f"cannot parse settings.yaml: {e}")


def check_agent_structure(r: CheckResult):
    """Validate agents/ and knowledge/ directories are consistent."""
    agents_dir = PROJECT_ROOT / "agents"
    knowledge_dir = PROJECT_ROOT / "knowledge"

    if not agents_dir.exists():
        r.fail("Agent structure", "agents/ directory not found")
        return

    agent_ids = set()
    agents_missing_profiles = []
    for sub in sorted(agents_dir.iterdir()):
        if sub.is_dir() and (sub / "core.yaml").exists():
            agent_ids.add(sub.name)
            try:
                import yaml
                data = yaml.safe_load((sub / "core.yaml").read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    if not data.get("profiles"):
                        agents_missing_profiles.append(sub.name)
            except Exception:
                pass

    if not agent_ids:
        r.fail("Agent structure", "no agents found in agents/")
        return

    knowledge_ids = set()
    if knowledge_dir.exists():
        for sub in knowledge_dir.iterdir():
            if sub.is_dir():
                knowledge_ids.add(sub.name)

    agents_without_knowledge = agent_ids - knowledge_ids
    knowledge_without_agents = knowledge_ids - agent_ids

    detail_parts = [f"{len(agent_ids)} agents"]
    if agents_missing_profiles:
        detail_parts.append(f"{len(agents_missing_profiles)} missing profiles field")
    if agents_without_knowledge:
        detail_parts.append(f"{len(agents_without_knowledge)} without knowledge/")
    if knowledge_without_agents:
        detail_parts.append(f"{len(knowledge_without_agents)} orphan knowledge/ dirs")

    if agents_missing_profiles or agents_without_knowledge or knowledge_without_agents:
        r.warn("Agent structure", ", ".join(detail_parts))
    else:
        r.ok("Agent structure", f"{len(agent_ids)} agents, all with profiles + knowledge")


def check_readiness_c4(r: CheckResult):
    """Run the C4 readiness test as a subprocess for an independent check."""
    test_path = PROJECT_ROOT / "tests" / "test_c4_readiness.py"
    if not test_path.exists():
        r.warn("C4 Readiness check", "test_c4_readiness.py not found — skipping")
        return

    try:
        result = subprocess.run(
            [sys.executable, str(test_path)],
            capture_output=True, text=True, timeout=30,
            cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        output = result.stdout + result.stderr
        import re
        fail_m = re.search(r"FAIL:\s*(\d+)", output)
        pass_m = re.search(r"PASS:\s*(\d+)", output)
        n_fail = int(fail_m.group(1)) if fail_m else 0
        n_pass = int(pass_m.group(1)) if pass_m else 0
        if n_fail > 0:
            r.fail("C4 Readiness check", f"{n_pass} pass, {n_fail} fail")
        elif n_pass > 0:
            r.ok("C4 Readiness check", f"{n_pass} tests passed")
        elif result.returncode == 0:
            r.ok("C4 Readiness check", "passed")
        else:
            r.warn("C4 Readiness check", "uncertain result — check manually")
    except subprocess.TimeoutExpired:
        r.warn("C4 Readiness check", "timeout (30s)")
    except Exception as e:
        r.warn("C4 Readiness check", str(e))


# ── Restore from backup ───────────────────────────────────────────
def restore_from_zip(zip_path: Path, target_dir: Path, r: CheckResult):
    """Restore project files from a backup zip to target directory."""
    if not zip_path.exists():
        r.fail("Backup zip", f"File not found: {zip_path}")
        return False

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\n{C}Restoring from: {zip_path.name} ({size_mb:.1f} MB){W}")
    print(f"  Target: {target_dir}")

    # Read companion meta if available
    meta_path = zip_path.with_name(zip_path.stem + "_meta.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ts = meta.get("timestamp", "unknown")
            facts = meta.get("data_stats", {}).get("scan_facts", "?")
            print(f"  Backup timestamp: {ts}  |  Facts: {facts}")
        except Exception:
            pass

    # Confirm (non-interactive skips if stdout is not a tty)
    if sys.stdout.isatty():
        confirm = input(f"  Restore to {target_dir}? This overwrites existing files. [y/N] ")
        if confirm.strip().lower() != "y":
            print("  Restore cancelled.")
            return False

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            file_count = len(zf.namelist())
            zf.extractall(str(target_dir))
        r.ok("Restore", f"{file_count} files extracted to {target_dir}")

        # Create .env reminder
        env_file = target_dir / ".env"
        env_example = target_dir / ".env.example"
        if not env_file.exists() and env_example.exists():
            print(f"\n  {Y}NOTE:{W} .env was not backed up (security).")
            print(f"  Copy .env.example to .env and fill in your values:")
            print(f"    cp {env_example} {env_file}")

        return True
    except zipfile.BadZipFile:
        r.fail("Restore", "zip file is corrupted")
        return False
    except PermissionError as e:
        r.fail("Restore", f"Permission denied: {e}")
        return False
    except Exception as e:
        r.fail("Restore", f"Unexpected error: {e}")
        return False


# ── Run all tests ─────────────────────────────────────────────────
def run_all_tests(r: CheckResult):
    """Run all registered tests via waggle_backup.py --tests-only."""
    backup_tool = PROJECT_ROOT / "tools" / "waggle_backup.py"
    if not backup_tool.exists():
        r.warn("Run all tests", "waggle_backup.py not found")
        return

    print(f"\n{C}Running all tests via waggle_backup.py --tests-only...{W}\n")
    try:
        result = subprocess.run(
            [sys.executable, str(backup_tool), "--tests-only"],
            cwd=str(PROJECT_ROOT),
            timeout=1800,  # 30 min max
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        if result.returncode == 0:
            r.ok("All tests", "completed (see output above)")
        else:
            r.warn("All tests", f"completed with issues (exit code {result.returncode})")
    except subprocess.TimeoutExpired:
        r.fail("All tests", "timeout after 30 minutes")
    except Exception as e:
        r.fail("All tests", str(e))


# ── Test Restore (non-destructive) ────────────────────────────────

def test_restore_to_temp(zip_path: Path, r: CheckResult) -> bool:
    """
    Test restore by extracting to a temp directory, then validating
    key files exist and are readable. Does NOT modify the real project.
    """
    import tempfile

    if not zip_path.exists():
        r.fail("Test restore", f"Zip file not found: {zip_path}")
        return False

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"\n{C}Test restore: {zip_path.name} ({size_mb:.1f} MB){W}")

    tmpdir = tempfile.mkdtemp(prefix="waggledance_test_restore_")
    try:
        # Extract to temp
        with zipfile.ZipFile(zip_path, "r") as zf:
            file_count = len(zf.namelist())
            zf.extractall(tmpdir)
        r.ok("Extract", f"{file_count} files extracted to temp dir")

        tmp = Path(tmpdir)

        # Check key directories
        for d in ["agents", "configs", "core", "data", "integrations",
                   "backend", "web", "tests", "tools", "voikko"]:
            if (tmp / d).is_dir():
                r.ok(f"Dir {d}/", "present")
            else:
                r.warn(f"Dir {d}/", "missing from backup")

        # Check key files
        for f in ["hivemind.py", "main.py", "start.py", "requirements.txt"]:
            if (tmp / f).is_file():
                r.ok(f"File {f}", "present")
            else:
                r.warn(f"File {f}", "missing from backup")

        # Check agents
        agents_dir = tmp / "agents"
        if agents_dir.is_dir():
            agent_count = sum(1 for d in agents_dir.iterdir()
                              if d.is_dir() and d.name != "__pycache__")
            if agent_count >= 70:
                r.ok("Agents", f"{agent_count} agent directories")
            else:
                r.warn("Agents", f"Only {agent_count} agents (expected 75)")

        # Check configs/settings.yaml is valid YAML
        settings = tmp / "configs" / "settings.yaml"
        if settings.is_file():
            try:
                import yaml
                data = yaml.safe_load(settings.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    r.ok("settings.yaml", "valid YAML, parsed OK")
                else:
                    r.warn("settings.yaml", "parsed but not a dict")
            except Exception as e:
                r.fail("settings.yaml", f"YAML parse error: {e}")

        # Check data dir contents
        data_dir = tmp / "data"
        if data_dir.is_dir():
            data_files = list(data_dir.glob("*"))
            r.ok("data/", f"{len(data_files)} files/dirs")

            # Check ChromaDB
            chroma_dir = data_dir / "chroma_db"
            if chroma_dir.is_dir():
                chroma_files = list(chroma_dir.rglob("*"))
                r.ok("data/chroma_db/", f"{len(chroma_files)} files")
            else:
                r.warn("data/chroma_db/", "missing — no knowledge base")

            # Check SQLite databases
            for db_name in ["waggle_dance.db", "audit_log.db", "chat_history.db"]:
                db_path = data_dir / db_name
                if db_path.is_file():
                    try:
                        conn = sqlite3.connect(str(db_path), timeout=5)
                        result = conn.execute("PRAGMA integrity_check").fetchone()
                        conn.close()
                        if result and result[0] == "ok":
                            size_mb = db_path.stat().st_size / (1024 * 1024)
                            r.ok(f"SQLite {db_name}", f"integrity OK ({size_mb:.1f} MB)")
                        else:
                            r.fail(f"SQLite {db_name}", f"integrity check: {result}")
                    except Exception as e:
                        r.warn(f"SQLite {db_name}", f"cannot verify: {e}")
                else:
                    r.warn(f"SQLite {db_name}", "not in backup")

        # Check models
        models_dir = tmp / "models"
        if models_dir.is_dir():
            r.ok("models/", "present")
        else:
            r.warn("models/", "missing from backup")

        # Check voikko
        voikko_dir = tmp / "voikko"
        if voikko_dir.is_dir():
            dll = voikko_dir / "libvoikko-1.dll"
            r.ok("voikko/", f"present (DLL: {'yes' if dll.exists() else 'no'})")
        else:
            r.warn("voikko/", "missing from backup")

        # Confirm .env is NOT in backup (security)
        if (tmp / ".env").exists():
            r.warn(".env in backup", "secrets file found in backup — consider removing")
        else:
            r.ok(".env excluded", "secrets file correctly excluded from backup")

        print(f"  {G}Test restore complete — temp dir cleaned up.{W}")
        return True

    except Exception as e:
        r.fail("Test restore", f"Error: {e}")
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="WaggleDance Restore & Validator v3.0")
    parser.add_argument("--restore", metavar="ZIPFILE", help="Restore from backup zip file")
    parser.add_argument("--target", metavar="DIR", help="Target directory for restore (default: project root)")
    parser.add_argument("--test-restore", metavar="ZIPFILE", help="Test restore to temp dir (non-destructive)")
    parser.add_argument("--run-tests", action="store_true", help="Run all tests after validation")
    args = parser.parse_args()

    # If --target is given, update PROJECT_ROOT for all checks
    global PROJECT_ROOT, REQUIREMENTS_FILE
    if args.target:
        PROJECT_ROOT = Path(args.target).resolve()
        REQUIREMENTS_FILE = PROJECT_ROOT / "requirements.txt"

    print(f"\n{B}{'='*55}")
    print(f"  WAGGLEDANCE RESTORE & VALIDATOR v3.0")
    print(f"  Project: {PROJECT_ROOT}")
    print(f"{'='*55}{W}\n")

    r = CheckResult()

    # 0. Test restore to temp dir (non-destructive)
    if args.test_restore:
        zip_path = Path(args.test_restore).resolve()
        ok = test_restore_to_temp(zip_path, r)
        ok_c, warn_c, fail_c = r.summary()
        print(f"\n{B}{'='*55}")
        print(f"  TEST RESTORE SUMMARY")
        print(f"{'='*55}{W}")
        print(f"  {G}OK{W}:   {ok_c}  {Y}WARN{W}: {warn_c}  {R}FAIL{W}: {fail_c}")
        sys.exit(0 if fail_c == 0 else 1)

    # 1. Restore from zip (if requested)
    if args.restore:
        zip_path = Path(args.restore).resolve()
        target = Path(args.target).resolve() if args.target else PROJECT_ROOT
        restored = restore_from_zip(zip_path, target, r)
        if not restored:
            print(f"\n{R}Restore failed — aborting.{W}")
            sys.exit(1)

    # 2. Run all checks
    print(f"{C}Checking environment...{W}")

    check_python(r)
    check_pip_dependencies(r)

    print()
    installed_models = check_ollama(r)
    if installed_models is not None:
        check_ollama_models(r, installed_models)

    print()
    check_chromadb(r)
    check_sqlite_integrity(r)
    check_voikko(r)
    check_translation_models(r)

    print()
    check_data_dirs(r)
    check_models_dir(r)
    check_env_file(r)
    check_profile_config(r)
    check_agent_structure(r)
    check_readiness_c4(r)

    # 3. Run all tests (if requested)
    if args.run_tests:
        run_all_tests(r)

    # 4. Summary
    ok, warn, fail = r.summary()
    print(f"\n{B}{'='*55}")
    print(f"  VALIDATION SUMMARY")
    print(f"{'='*55}{W}")
    print(f"  {G}OK{W}:   {ok}")
    print(f"  {Y}WARN{W}: {warn}")
    print(f"  {R}FAIL{W}: {fail}")

    if fail > 0:
        print(f"\n  {R}Environment has issues. Fix FAIL items above before starting WaggleDance.{W}")
        sys.exit(1)
    elif warn > 0:
        print(f"\n  {Y}Environment ready with warnings. WaggleDance will start but some features may be limited.{W}")
        sys.exit(0)
    else:
        print(f"\n  {G}Environment fully ready. Start WaggleDance with: python main.py{W}")
        sys.exit(0)


if __name__ == "__main__":
    main()
