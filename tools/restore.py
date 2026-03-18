#!/usr/bin/env python3
"""
WaggleDance Restore & Environment Builder v4.0
================================================
One-click restore: creates venv, installs dependencies, builds
dashboard, creates runtime folders, and runs smoke test.

Can be called by restore.bat or run directly.

Usage:
    python tools/restore.py                    # Validate + setup
    python tools/restore.py --from-zip FILE    # Extract zip first, then setup
    python tools/restore.py --skip-dashboard   # Skip npm install/build
    python tools/restore.py --skip-smoke       # Skip smoke test
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# ── Windows UTF-8 ─────────────────────────────────────────────
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

# ── Colors ────────────────────────────────────────────────────
G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"
C = "\033[96m"; W = "\033[0m"; BOLD = "\033[1m"
os.system("")  # Enable ANSI on Windows

# ── Constants ─────────────────────────────────────────────────
_script_dir = Path(__file__).resolve().parent
ROOT = _script_dir.parent if _script_dir.name == "tools" else _script_dir

REQUIRED_PYTHON = (3, 13)
REQUIRED_MODELS = ["phi4-mini", "llama3.2:1b", "nomic-embed-text", "all-minilm"]
RUNTIME_DIRS = ["data", "logs", "chroma_data"]

VENV_DIR = ROOT / ".venv"
if os.name == "nt":
    VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
    VENV_PIP = VENV_DIR / "Scripts" / "pip.exe"
else:
    VENV_PYTHON = VENV_DIR / "bin" / "python"
    VENV_PIP = VENV_DIR / "bin" / "pip"


# ── Helpers ───────────────────────────────────────────────────

class RestoreResult:
    def __init__(self):
        self.steps: list[tuple[str, str, str]] = []  # (status, name, detail)

    def ok(self, name: str, detail: str = ""):
        self.steps.append(("OK", name, detail))
        print(f"  {G}✓{W} {name}" + (f"  {detail}" if detail else ""))

    def warn(self, name: str, detail: str = ""):
        self.steps.append(("WARN", name, detail))
        print(f"  {Y}⚠{W} {name}" + (f"  {detail}" if detail else ""))

    def fail(self, name: str, detail: str = ""):
        self.steps.append(("FAIL", name, detail))
        print(f"  {R}✗{W} {name}" + (f"  {detail}" if detail else ""))

    @property
    def has_failures(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.steps)


def run(cmd: list[str], cwd: Path | None = None, check: bool = True,
        quiet: bool = False) -> subprocess.CompletedProcess:
    """Run a command, print it, optionally check return code."""
    if not quiet:
        print(f"    {B}>{W} {' '.join(str(c) for c in cmd)}")
    return subprocess.run(
        cmd, cwd=cwd or ROOT,
        capture_output=quiet, text=True,
        check=check,
    )


def find_requirements() -> Path | None:
    """Find best requirements file: lock > requirements.txt."""
    for name in ("requirements.lock.txt", "requirements.txt"):
        p = ROOT / name
        if p.exists():
            return p
    return None


def read_manifest() -> dict | None:
    """Read manifest.json if it exists in backup."""
    p = ROOT / "manifest.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


# ── Steps ─────────────────────────────────────────────────────

def step_extract_zip(zip_path: Path, result: RestoreResult) -> None:
    """Extract backup zip to ROOT."""
    if not zip_path.exists():
        result.fail("Extract zip", f"File not found: {zip_path}")
        return
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(ROOT)
            result.ok("Extract zip", f"{len(zf.namelist())} files")
    except Exception as e:
        result.fail("Extract zip", str(e))


def step_check_python(result: RestoreResult) -> None:
    """Verify Python version."""
    v = sys.version_info
    if (v.major, v.minor) >= REQUIRED_PYTHON:
        result.ok("Python version", f"{v.major}.{v.minor}.{v.micro}")
    else:
        result.fail("Python version",
                     f"{v.major}.{v.minor} < {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}")


def step_create_venv(result: RestoreResult) -> None:
    """Create .venv if it doesn't exist."""
    if VENV_DIR.exists() and VENV_PYTHON.exists():
        result.ok("Virtual environment", "already exists")
        return
    try:
        run([sys.executable, "-m", "venv", str(VENV_DIR)], quiet=True)
        if VENV_PYTHON.exists():
            result.ok("Virtual environment", "created")
        else:
            result.fail("Virtual environment", "venv created but python not found")
    except Exception as e:
        result.fail("Virtual environment", str(e))


def step_upgrade_pip(result: RestoreResult) -> None:
    """Upgrade pip/setuptools/wheel in venv."""
    try:
        run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade",
             "pip", "setuptools", "wheel", "--quiet"], quiet=True)
        result.ok("Upgrade pip/setuptools")
    except Exception as e:
        result.warn("Upgrade pip/setuptools", str(e))


def step_install_python_deps(result: RestoreResult) -> None:
    """Install Python dependencies into venv."""
    req = find_requirements()
    if not req:
        result.fail("Python dependencies", "No requirements file found")
        return
    try:
        run([str(VENV_PIP), "install", "-r", str(req),
             "--disable-pip-version-check"], quiet=True)
        result.ok("Python dependencies", req.name)
    except subprocess.CalledProcessError:
        # Retry verbose so user sees the error
        result.warn("Python dependencies", "Some packages failed, retrying verbose...")
        try:
            run([str(VENV_PIP), "install", "-r", str(req),
                 "--disable-pip-version-check"], check=True)
            result.ok("Python dependencies", f"{req.name} (retry OK)")
        except Exception as e2:
            result.fail("Python dependencies", str(e2))


def step_create_env_file(result: RestoreResult) -> None:
    """Create .env from template if missing."""
    env_file = ROOT / ".env"
    if env_file.exists():
        result.ok(".env file", "already exists")
        return

    template = ROOT / "env.template"
    if template.exists():
        shutil.copy2(template, env_file)
        result.ok(".env file", "created from env.template")
    else:
        # Write minimal default
        env_file.write_text(
            "WAGGLE_PROFILE=COTTAGE\n"
            "WAGGLE_API_KEY=\n"
            "OLLAMA_HOST=http://localhost:11434\n",
            encoding="utf-8",
        )
        result.ok(".env file", "created with defaults (edit WAGGLE_API_KEY if needed)")


def step_install_dashboard(result: RestoreResult) -> None:
    """Install and build dashboard if package.json exists."""
    dashboard_dir = ROOT / "dashboard"
    pkg_json = dashboard_dir / "package.json"

    if not pkg_json.exists():
        result.ok("Dashboard", "no package.json — skipped")
        return

    npm_cmd = "npm.cmd" if os.name == "nt" else "npm"
    try:
        subprocess.run([npm_cmd, "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        result.warn("Dashboard", "npm not found in PATH — skipped")
        return

    try:
        lock = dashboard_dir / "package-lock.json"
        if lock.exists():
            run([npm_cmd, "ci"], cwd=dashboard_dir, quiet=True)
        else:
            run([npm_cmd, "install"], cwd=dashboard_dir, quiet=True)
        result.ok("Dashboard deps", "installed")
    except Exception as e:
        result.warn("Dashboard deps", str(e))
        return

    try:
        run([npm_cmd, "run", "build"], cwd=dashboard_dir, quiet=True)
        result.ok("Dashboard build", "OK")
    except Exception as e:
        result.warn("Dashboard build", str(e))


def step_create_runtime_dirs(result: RestoreResult) -> None:
    """Create runtime directories that aren't in backup."""
    created = []
    for d in RUNTIME_DIRS:
        p = ROOT / d
        if not p.exists():
            p.mkdir(parents=True, exist_ok=True)
            created.append(d)
    if created:
        result.ok("Runtime dirs", ", ".join(created))
    else:
        result.ok("Runtime dirs", "all exist")


def step_check_ollama(result: RestoreResult) -> None:
    """Check Ollama and required models."""
    try:
        r = subprocess.run(["ollama", "--version"], capture_output=True, text=True)
        if r.returncode != 0:
            raise FileNotFoundError
    except FileNotFoundError:
        result.warn("Ollama", "not found — install from https://ollama.com")
        return

    missing = []
    for model in REQUIRED_MODELS:
        try:
            subprocess.run(["ollama", "show", model],
                           capture_output=True, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            missing.append(model)

    if missing:
        result.warn("Ollama models",
                     f"missing: {', '.join(missing)} — run: ollama pull <model>")
    else:
        result.ok("Ollama models", f"all {len(REQUIRED_MODELS)} present")


def step_smoke_test(result: RestoreResult) -> None:
    """Run smoke test: import + stub container build."""
    # Try new architecture first, fall back to old
    smoke_new = (
        "from waggledance.adapters.config.settings_loader import WaggleSettings; "
        "from waggledance.bootstrap.container import Container; "
        "s=WaggleSettings.from_env(); "
        "c=Container(settings=s, stub=True); "
        "app=c.build_app(); "
        "print('NEW_ARCH_OK', app.title)"
    )
    smoke_old = (
        "import sys; sys.path.insert(0,'.'); "
        "from backend.main import app; "
        "print('OLD_ARCH_OK')"
    )

    # Try new architecture
    try:
        r = subprocess.run(
            [str(VENV_PYTHON), "-c", smoke_new],
            capture_output=True, text=True, cwd=ROOT, timeout=30,
        )
        if r.returncode == 0 and "NEW_ARCH_OK" in r.stdout:
            result.ok("Smoke test", "new architecture (stub container + app)")
            return
    except Exception:
        pass

    # Try old architecture
    try:
        r = subprocess.run(
            [str(VENV_PYTHON), "-c", smoke_old],
            capture_output=True, text=True, cwd=ROOT, timeout=30,
        )
        if r.returncode == 0 and "OLD_ARCH_OK" in r.stdout:
            result.ok("Smoke test", "old architecture (backend.main)")
            return
    except Exception:
        pass

    # Both failed — try basic import
    try:
        r = subprocess.run(
            [str(VENV_PYTHON), "-c", "import fastapi; import chromadb; print('IMPORTS_OK')"],
            capture_output=True, text=True, cwd=ROOT, timeout=15,
        )
        if r.returncode == 0:
            result.warn("Smoke test", "imports OK but app build failed")
            return
    except Exception:
        pass

    result.fail("Smoke test", "could not import or build app")


# ── Main ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="WaggleDance Restore v4.0")
    parser.add_argument("--from-zip", type=str, help="Extract from zip before setup")
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--target", type=str, help="Target directory (default: project root)")
    args = parser.parse_args()

    global ROOT, VENV_DIR, VENV_PYTHON, VENV_PIP
    if args.target:
        ROOT = Path(args.target).resolve()
        VENV_DIR = ROOT / ".venv"
        if os.name == "nt":
            VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"
            VENV_PIP = VENV_DIR / "Scripts" / "pip.exe"
        else:
            VENV_PYTHON = VENV_DIR / "bin" / "python"
            VENV_PIP = VENV_DIR / "bin" / "pip"

    print()
    print(f"  {BOLD}WaggleDance Restore v4.0{W}")
    print(f"  Target: {ROOT}")
    print()

    result = RestoreResult()

    # Step 0: Extract zip if provided
    if args.from_zip:
        step_extract_zip(Path(args.from_zip), result)
        if result.has_failures:
            print(f"\n  {R}Restore aborted: zip extraction failed.{W}\n")
            sys.exit(1)

    # Read manifest if available
    manifest = read_manifest()
    if manifest:
        print(f"  Project: {manifest.get('project_name', '?')}")
        print(f"  Backup: {manifest.get('created_at', '?')}")
        print()

    # Sequential steps
    step_check_python(result)
    if result.has_failures:
        print(f"\n  {R}Restore aborted: Python version too old.{W}\n")
        sys.exit(1)

    step_create_venv(result)
    if result.has_failures:
        print(f"\n  {R}Restore aborted: cannot create venv.{W}\n")
        sys.exit(1)

    step_upgrade_pip(result)
    step_install_python_deps(result)
    step_create_env_file(result)
    step_create_runtime_dirs(result)

    if not args.skip_dashboard:
        step_install_dashboard(result)

    step_check_ollama(result)

    if not args.skip_smoke:
        step_smoke_test(result)

    # Summary
    print()
    ok_count = sum(1 for s, _, _ in result.steps if s == "OK")
    warn_count = sum(1 for s, _, _ in result.steps if s == "WARN")
    fail_count = sum(1 for s, _, _ in result.steps if s == "FAIL")

    if fail_count == 0:
        print(f"  {G}{BOLD}RESTORE OK{W}  ({ok_count} ok, {warn_count} warnings)")
        print()
        print(f"  Start WaggleDance:")
        if (ROOT / "waggledance" / "adapters" / "cli" / "start_runtime.py").exists():
            activate = str(VENV_DIR / "Scripts" / "activate") if os.name == "nt" else f"source {VENV_DIR / 'bin' / 'activate'}"
            print(f"    {activate}")
            print(f"    python -m waggledance.adapters.cli.start_runtime")
        else:
            print(f"    python main.py")
        print()
    else:
        print(f"  {R}{BOLD}RESTORE INCOMPLETE{W}  ({ok_count} ok, {warn_count} warnings, {fail_count} failures)")
        print()

    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
