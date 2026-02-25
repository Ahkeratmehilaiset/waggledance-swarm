#!/usr/bin/env python3
"""
WaggleDance Backup & Test Tool v3.0
=====================================
Runs full component tests, generates AI report, creates backup.

Usage:
    python tools/waggle_backup.py              # Full: tests + report + backup
    python tools/waggle_backup.py --skip-tests # Report + backup (no tests)
    python tools/waggle_backup.py --tests-only # Tests + report only (no zip)
"""

import argparse
import dataclasses
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from fnmatch import fnmatch
from pathlib import Path

# ── Windows UTF-8 ────────────────────────────────────────────────
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

# ── Constants ────────────────────────────────────────────────────
# Support running from tools/ or from project root (U:\)
_script_dir = Path(__file__).resolve().parent
if _script_dir.name == "tools":
    PROJECT_ROOT = _script_dir.parent
else:
    # Script copied to U:\ root — project is in U:\project
    PROJECT_ROOT = _script_dir / "project" if (_script_dir / "project").exists() else _script_dir

BACKUP_DIR = PROJECT_ROOT.parent / "backups" if PROJECT_ROOT.name == "project" else PROJECT_ROOT / "backups"
MAX_BACKUPS = 7
TEST_TIMEOUT = 300  # seconds per test (some load Opus-MT models)

# Extra backup locations (copy zip + meta if available)
EXTRA_BACKUP_LOCATIONS = [
    Path("C:/WaggleDance_Backups"),
    Path("D:/WaggleDance_Backups"),  # CORSAIR external
]

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; B = "\033[94m"
C = "\033[96m"; W = "\033[0m"; BOLD = "\033[1m"
os.system("")  # Enable ANSI on Windows

# ── Test Registry ────────────────────────────────────────────────
TESTS = [
    {"file": "test_all.py",       "name": "General Diagnostics",    "phase": "1-2",   "args": ["--offline"], "timeout": 60},
    {"file": "test_pipeline.py",  "name": "Translation Pipeline",   "phase": "2",     "args": [],            "timeout": 600},
    {"file": "test_phase3.py",    "name": "Social Learning",        "phase": "3",     "args": [],            "timeout": 120},
    {"file": "test_phase4.py",    "name": "Advanced Learning",      "phase": "4",     "args": [],            "timeout": 600},
    {"file": "test_phase4ijk.py", "name": "Bilingual/Cache/Enrich", "phase": "4ijk",  "args": [],            "timeout": 300},
    {"file": "test_phase8.py",    "name": "External Data Feeds",    "phase": "8",     "args": [],            "timeout": 60},
    {"file": "test_phase9.py",    "name": "Autonomous Learning",    "phase": "9",     "args": [],            "timeout": 60},
    {"file": "test_phase10.py",   "name": "Micro-Model Training",   "phase": "10",    "args": [],            "timeout": 60},
]

# Backup exclusions
EXCLUDE_DIRS = {
    "__pycache__", ".git", "backups", "node_modules", "dist",
    ".claude", ".venv", "venv", "chromadb_data", "chroma",
    "chroma_db", "logs", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "test_consciousness_v2",
}

EXCLUDE_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".pt", ".onnx", ".egg-info"}

EXCLUDE_PATTERNS = [
    "*_backup_*.py", "fix_*.py", "patch_*.py", "hotfix_*.py", "*_mega_patch.py",
]


# ── Data Classes ─────────────────────────────────────────────────
@dataclasses.dataclass
class TestResult:
    file: str
    name: str
    phase: str
    passed: int = 0
    failed: int = 0
    warned: int = 0
    duration_s: float = 0.0
    status: str = "PENDING"  # PASS | FAIL | TIMEOUT | CRASH
    raw_output: str = ""
    errors: list = dataclasses.field(default_factory=list)


# ── ANSI Utilities ───────────────────────────────────────────────
def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[0-9;]*m", "", text)


# ── Test Runner ──────────────────────────────────────────────────
def parse_test_output(stdout: str, test_file: str) -> dict:
    """Parse test output for pass/fail/warn counts."""
    clean = strip_ansi(stdout)

    # Strategy 1: Standard pattern (PASS: N / FAIL: N / WARN: N)
    p = re.search(r"PASS:\s*(\d+)", clean)
    f = re.search(r"FAIL:\s*(\d+)", clean)
    w = re.search(r"WARN:\s*(\d+)", clean)

    # Strategy 1b: Finnish labels (test_all.py uses "Läpäisseet:", "Virheet:", "Varoitukset:")
    if not p:
        p = re.search(r"L.p.isseet:\s*(\d+)", clean)
    if not f:
        f = re.search(r"Virheet:\s*(\d+)", clean)
    if not w:
        w = re.search(r"Varoitukset:\s*(\d+)", clean)

    if p or f:
        return {
            "passed": int(p.group(1)) if p else 0,
            "failed": int(f.group(1)) if f else 0,
            "warned": int(w.group(1)) if w else 0,
        }

    # Strategy 2: Pipeline format — count emoji occurrences in summary table
    if "test_pipeline" in test_file:
        ok_count = len(re.findall(r"\d+\u2705", clean))  # N✅
        fail_count = len(re.findall(r"\d+\u274c", clean))  # N❌
        warn_count = len(re.findall(r"\d+\u26a0", clean))  # N⚠
        # Also try extracting actual numbers
        ok_nums = re.findall(r"(\d+)\u2705", clean)
        fail_nums = re.findall(r"(\d+)\u274c", clean)
        warn_nums = re.findall(r"(\d+)\u26a0", clean)
        return {
            "passed": sum(int(x) for x in ok_nums) if ok_nums else ok_count,
            "failed": sum(int(x) for x in fail_nums) if fail_nums else fail_count,
            "warned": sum(int(x) for x in warn_nums) if warn_nums else warn_count,
        }

    # Strategy 3: Fallback — count OK/FAIL lines
    ok_lines = len(re.findall(r"(?:OK |✅)", clean))
    fail_lines = len(re.findall(r"(?:FAIL |❌)", clean))
    warn_lines = len(re.findall(r"(?:WARN |⚠)", clean))
    return {"passed": ok_lines, "failed": fail_lines, "warned": warn_lines}


def extract_errors(stdout: str) -> list:
    """Extract failure messages from test output."""
    clean = strip_ansi(stdout)
    errors = []
    for line in clean.splitlines():
        line = line.strip()
        if re.match(r"^(❌|FAIL|•\s)", line):
            msg = re.sub(r"^[❌•]\s*", "", line).strip()
            msg = re.sub(r"^FAIL\s*", "", msg).strip()
            if msg and len(msg) > 3:
                errors.append(msg[:200])
    return errors[:20]  # cap at 20


def run_single_test(entry: dict, project_root: Path) -> TestResult:
    """Run a single test file and collect results."""
    result = TestResult(
        file=entry["file"],
        name=entry["name"],
        phase=entry["phase"],
    )

    test_path = project_root / entry["file"]
    if not test_path.exists():
        result.status = "MISSING"
        result.raw_output = f"File not found: {test_path}"
        return result

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    timeout = entry.get("timeout", TEST_TIMEOUT)
    args = [sys.executable, str(test_path)] + entry.get("args", [])
    start = time.time()

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            cwd=str(project_root),
            env=env,
            text=True,
            errors="replace",
        )
        result.duration_s = round(time.time() - start, 1)
        result.raw_output = proc.stdout + ("\n--- STDERR ---\n" + proc.stderr if proc.stderr else "")

        parsed = parse_test_output(proc.stdout, entry["file"])
        result.passed = parsed["passed"]
        result.failed = parsed["failed"]
        result.warned = parsed["warned"]
        result.errors = extract_errors(proc.stdout)

        if proc.returncode != 0 and result.failed == 0 and result.passed == 0:
            result.status = "CRASH"
        elif result.failed > 0:
            result.status = "FAIL"
        else:
            result.status = "PASS"

    except subprocess.TimeoutExpired:
        result.duration_s = timeout
        result.status = "TIMEOUT"
        result.raw_output = f"TIMEOUT after {timeout}s"

    except Exception as e:
        result.duration_s = round(time.time() - start, 1)
        result.status = "CRASH"
        result.raw_output = f"Exception: {e}"

    return result


def run_all_tests(project_root: Path) -> list:
    """Run all registered tests sequentially."""
    results = []
    total = len(TESTS)

    for i, entry in enumerate(TESTS, 1):
        print(f"\n{C}[{i}/{total}]{W} {entry['file']} ({entry['name']})...")
        result = run_single_test(entry, project_root)

        # Print result line
        if result.status == "PASS":
            badge = f"{G}PASS{W}"
        elif result.status == "FAIL":
            badge = f"{R}FAIL{W}"
        elif result.status == "TIMEOUT":
            badge = f"{R}TIMEOUT{W}"
        elif result.status == "MISSING":
            badge = f"{Y}MISSING{W}"
        else:
            badge = f"{R}CRASH{W}"

        parts = [f"{result.passed} ok"]
        if result.failed:
            parts.append(f"{result.failed} fail")
        if result.warned:
            parts.append(f"{result.warned} warn")
        counts = ", ".join(parts)

        print(f"       {badge}  {counts}  ({result.duration_s}s)")

        if result.errors:
            for err in result.errors[:3]:
                print(f"       {R}  - {err[:100]}{W}")

        results.append(result)

    return results


# ── Stats Collectors ─────────────────────────────────────────────
def collect_codebase_stats(project_root: Path) -> dict:
    """Count files, lines, agents."""
    counts = {"py": 0, "yaml": 0, "jsx": 0, "tsx": 0, "json": 0, "other": 0}
    total_lines = 0
    agents = set()

    for p in project_root.rglob("*"):
        if any(part in EXCLUDE_DIRS for part in p.parts):
            continue
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext == ".py":
            counts["py"] += 1
        elif ext in (".yaml", ".yml"):
            counts["yaml"] += 1
        elif ext == ".jsx":
            counts["jsx"] += 1
        elif ext == ".tsx":
            counts["tsx"] += 1
        elif ext == ".json":
            counts["json"] += 1
        else:
            counts["other"] += 1

        if ext in (".py", ".yaml", ".yml", ".jsx", ".tsx", ".json", ".md"):
            try:
                total_lines += len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass

    # Count agents
    for d in ["agents", "knowledge"]:
        agent_dir = project_root / d
        if agent_dir.exists():
            for sub in agent_dir.iterdir():
                if sub.is_dir() and (sub / "core.yaml").exists():
                    agents.add(sub.name)

    return {
        "files": counts,
        "total_files": sum(counts.values()),
        "total_lines": total_lines,
        "agent_count": len(agents),
        "agents": sorted(agents),
    }


def collect_hardware_info() -> dict:
    """Detect GPU, RAM, OS."""
    info = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu": platform.processor() or "unknown",
        "ram_gb": "unknown",
        "gpu": "unknown",
        "gpu_vram": "unknown",
    }

    # RAM
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory", "/value"],
                capture_output=True, text=True, timeout=5,
            )
            m = re.search(r"TotalPhysicalMemory=(\d+)", r.stdout)
            if m:
                info["ram_gb"] = f"{int(m.group(1)) / (1024**3):.1f} GB"
    except Exception:
        pass

    # GPU
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            parts = r.stdout.strip().split(", ")
            if len(parts) >= 2:
                info["gpu"] = parts[0].strip()
                info["gpu_vram"] = f"{int(parts[1].strip())} MB"
    except Exception:
        pass

    return info


def collect_data_stats(project_root: Path) -> dict:
    """Collect stats from data files."""
    stats = {
        "mass_test": None,
        "confusion_entries": 0,
        "routing_accuracy": None,
        "scan_facts": 0,
    }

    # Mass test results
    mt = project_root / "data" / "mass_test_results.json"
    if mt.exists():
        try:
            data = json.loads(mt.read_text(encoding="utf-8"))
            stats["mass_test"] = {
                "timestamp": data.get("timestamp", ""),
                "total": data.get("total", 0),
                "correct_agent_pct": data.get("correct_agent_pct", 0),
                "wrong_agent_pct": data.get("wrong_agent_pct", 0),
                "fallback_pct": data.get("fallback_pct", 0),
            }
            stats["routing_accuracy"] = data.get("correct_agent_pct", 0)
        except Exception:
            pass

    # Confusion memory
    cm = project_root / "data" / "confusion_memory.json"
    if cm.exists():
        try:
            data = json.loads(cm.read_text(encoding="utf-8"))
            stats["confusion_entries"] = len(data)
        except Exception:
            pass

    # Scan progress
    sp = project_root / "data" / "scan_progress.json"
    if sp.exists():
        try:
            data = json.loads(sp.read_text(encoding="utf-8"))
            stats["scan_facts"] = data.get("total_facts", 0)
        except Exception:
            pass

    return stats


def collect_ollama_models() -> list:
    """List installed Ollama models."""
    try:
        r = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            models = []
            for line in r.stdout.strip().splitlines()[1:]:  # skip header
                name = line.split()[0] if line.strip() else ""
                if name:
                    models.append(name)
            return models
    except Exception:
        pass
    return []


# ── Health Score ─────────────────────────────────────────────────
def calculate_health_score(test_results: list, data_stats: dict) -> int:
    """Calculate 0-100 health score."""
    score = 100

    if test_results:
        for tr in test_results:
            if tr.status in ("TIMEOUT", "CRASH", "MISSING"):
                score -= 10
            elif tr.failed > 0:
                score -= 5

        total_fails = sum(tr.failed for tr in test_results)
        score -= min(total_fails, 20)  # cap at -20

    if data_stats.get("mass_test") is None:
        score -= 5
    elif data_stats.get("routing_accuracy") is not None:
        acc = data_stats["routing_accuracy"]
        if acc < 90:
            score -= 10
        elif acc < 95:
            score -= 5

    return max(0, score)


# ── AI Brief Generator ──────────────────────────────────────────
def generate_ai_brief(
    test_results: list,
    codebase: dict,
    hardware: dict,
    data_stats: dict,
    ollama_models: list,
) -> str:
    """Generate markdown AI brief with test results."""
    now = datetime.now()
    health = calculate_health_score(test_results, data_stats)

    lines = []
    lines.append("# WAGGLEDANCE SWARM AI -- PROJECT BRIEF")
    lines.append(f"# Auto-generated: {now.strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"# Health Score: {health}/100")
    lines.append("")

    # Hardware
    lines.append("## Hardware")
    lines.append(f"- GPU: {hardware.get('gpu', 'unknown')} ({hardware.get('gpu_vram', '?')})")
    lines.append(f"- RAM: {hardware.get('ram_gb', 'unknown')}")
    lines.append(f"- OS: {hardware.get('os', 'unknown')}")
    lines.append(f"- Python: {hardware.get('python', 'unknown')}")
    if ollama_models:
        lines.append(f"- Ollama models: {', '.join(ollama_models[:10])}")
    lines.append("")

    # Codebase
    lines.append("## Codebase Stats")
    fc = codebase["files"]
    lines.append(f"- Files: {codebase['total_files']} ({fc['py']} .py, {fc['yaml']} .yaml, {fc['jsx']+fc['tsx']} .jsx/.tsx)")
    lines.append(f"- Total lines: {codebase['total_lines']:,}")
    lines.append(f"- Agents: {codebase['agent_count']}")
    lines.append("")

    # Test Results
    if test_results:
        total_dur = sum(tr.duration_s for tr in test_results)
        total_p = sum(tr.passed for tr in test_results)
        total_f = sum(tr.failed for tr in test_results)
        total_w = sum(tr.warned for tr in test_results)
        suites_pass = sum(1 for tr in test_results if tr.status == "PASS")

        lines.append("## Component Test Results")
        lines.append(f"Test run: {now.strftime('%Y-%m-%d %H:%M')} | Duration: {total_dur:.1f}s")
        lines.append("")
        lines.append("| Phase | Test | Pass | Fail | Warn | Status | Time |")
        lines.append("|-------|------|------|------|------|--------|------|")
        for tr in test_results:
            lines.append(
                f"| {tr.phase} | {tr.name} | {tr.passed} | {tr.failed} | "
                f"{tr.warned} | {tr.status} | {tr.duration_s}s |"
            )
        lines.append(
            f"| **Total** | **{len(test_results)} suites** | **{total_p}** | "
            f"**{total_f}** | **{total_w}** | **{suites_pass}/{len(test_results)}** | "
            f"**{total_dur:.1f}s** |"
        )
        lines.append("")

        # Failures
        all_errors = []
        for tr in test_results:
            if tr.errors:
                for e in tr.errors[:5]:
                    all_errors.append(f"- **{tr.name}**: {e}")
        if all_errors:
            lines.append("### Failures")
            lines.extend(all_errors[:15])
            lines.append("")

    # Routing Performance
    lines.append("## Routing Performance")
    mt = data_stats.get("mass_test")
    if mt:
        lines.append(f"- Accuracy: {mt['correct_agent_pct']}% ({mt['total']} eval_questions)")
        lines.append(f"- Wrong agent: {mt['wrong_agent_pct']}%")
        lines.append(f"- Fallback: {mt['fallback_pct']}%")
        lines.append(f"- Confusion memory entries: {data_stats['confusion_entries']}")
        lines.append(f"- Last tested: {mt['timestamp']}")
    else:
        lines.append("- No mass test results available. Run: `python tools/mass_chat_test.py`")
    lines.append("")

    # Knowledge
    if data_stats["scan_facts"]:
        lines.append("## Knowledge Base")
        lines.append(f"- Scanned facts: {data_stats['scan_facts']}")
        lines.append("")

    # Health Score
    lines.append(f"## Health Score: {health}/100")
    if health >= 90:
        lines.append("System is healthy.")
    elif health >= 70:
        lines.append("Some issues detected. Check test failures above.")
    else:
        lines.append("Significant issues. Review test results and fix failures.")
    lines.append("")

    # Status
    lines.append("## Current Phase Status")
    lines.append("- Phase 1: COMPLETE (consciousness v2, dual embed, smart router)")
    lines.append("- Phase 2: COMPLETE (94% benchmark, 3148 facts)")
    lines.append("- Phase 3: COMPLETE (Round Table, agent levels, night mode)")
    lines.append("- Phase 4: IN PROGRESS (contrastive, active, bilingual index)")
    lines.append("- Phase 5-11: SPECIFIED (Camera, Audio, Voice, Weather, Auto-learning, MicroModel, Scaling)")
    lines.append("")

    lines.append("---")
    lines.append(f"*Generated by waggle_backup.py v3.0 | {now.strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


# ── Backup Engine ────────────────────────────────────────────────
def should_include(path: Path, project_root: Path) -> bool:
    """Check if a file should be included in backup."""
    rel = path.relative_to(project_root)

    # Check excluded directories
    for part in rel.parts:
        if part in EXCLUDE_DIRS:
            return False

    # Check extensions
    if path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return False

    # Check patterns
    name = path.name
    for pattern in EXCLUDE_PATTERNS:
        if fnmatch(name, pattern):
            return False

    return True


def do_backup(
    project_root: Path,
    backup_dir: Path,
    test_results: list,
    ai_brief: str,
    timestamp: str,
) -> Path:
    """Create timestamped zip backup."""
    backup_dir.mkdir(parents=True, exist_ok=True)

    zip_name = f"waggle_{timestamp}.zip"
    zip_path = backup_dir / zip_name
    file_count = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(project_root.rglob("*")):
            if not p.is_file():
                continue
            if not should_include(p, project_root):
                continue
            rel = p.relative_to(project_root)
            try:
                zf.write(p, str(rel))
                file_count += 1
            except (PermissionError, OSError):
                pass

        # Add AI brief
        zf.writestr("WAGGLEDANCE_AI_BRIEF.md", ai_brief)

        # Add raw test output
        if test_results:
            test_output = []
            for tr in test_results:
                test_output.append(f"{'='*70}")
                test_output.append(f"  {tr.file} — {tr.name} (Phase {tr.phase})")
                test_output.append(f"  Status: {tr.status} | {tr.passed} pass, {tr.failed} fail, {tr.warned} warn | {tr.duration_s}s")
                test_output.append(f"{'='*70}")
                test_output.append(strip_ansi(tr.raw_output))
                test_output.append("")
            zf.writestr("test_output.txt", "\n".join(test_output))

    return zip_path, file_count


def create_meta_json(
    timestamp: str,
    zip_path: Path,
    hardware: dict,
    codebase: dict,
    data_stats: dict,
    test_results: list,
    ollama_models: list,
) -> dict:
    """Create metadata JSON alongside the zip."""
    meta = {
        "version": "3.0",
        "timestamp": timestamp,
        "datetime": datetime.now().isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "hardware": hardware,
        "codebase": {
            "total_files": codebase["total_files"],
            "total_lines": codebase["total_lines"],
            "agent_count": codebase["agent_count"],
            "file_types": codebase["files"],
        },
        "ollama_models": ollama_models,
        "data_stats": {
            "scan_facts": data_stats["scan_facts"],
            "confusion_entries": data_stats["confusion_entries"],
            "routing_accuracy": data_stats.get("routing_accuracy"),
        },
    }

    if zip_path and zip_path.exists():
        meta["zip_size_bytes"] = zip_path.stat().st_size

    if test_results:
        meta["test_results"] = {
            "total_pass": sum(tr.passed for tr in test_results),
            "total_fail": sum(tr.failed for tr in test_results),
            "total_warn": sum(tr.warned for tr in test_results),
            "health_score": calculate_health_score(test_results, data_stats),
            "suites": {
                tr.file: {
                    "pass": tr.passed,
                    "fail": tr.failed,
                    "warn": tr.warned,
                    "status": tr.status,
                    "duration_s": tr.duration_s,
                }
                for tr in test_results
            },
        }

    meta_path = zip_path.with_name(zip_path.stem + "_meta.json") if zip_path else None
    if meta_path:
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return meta


def rotate_backups(backup_dir: Path, keep: int = MAX_BACKUPS):
    """Keep only the most recent backups."""
    if not backup_dir.exists():
        return
    zips = sorted(backup_dir.glob("waggle_*.zip"), key=lambda p: p.stem)
    while len(zips) > keep:
        oldest = zips.pop(0)
        oldest.unlink(missing_ok=True)
        # Remove companion files
        for suffix in ["_meta.json", "_tests.txt"]:
            companion = oldest.with_name(oldest.stem + suffix)
            if companion.exists():
                companion.unlink(missing_ok=True)


def copy_to_extra_locations(zip_path: Path, meta_path: Path):
    """Copy backup to extra locations (C:, CORSAIR D:, etc.)."""
    for loc in EXTRA_BACKUP_LOCATIONS:
        drive = loc.parts[0] if loc.parts else ""
        drive_path = Path(drive + "/") if drive else loc
        if not drive_path.exists():
            continue  # Drive not connected
        try:
            loc.mkdir(parents=True, exist_ok=True)
            shutil.copy2(zip_path, loc / zip_path.name)
            if meta_path and meta_path.exists():
                shutil.copy2(meta_path, loc / meta_path.name)
            print(f"  {G}Copied to {loc}{W}")
        except (PermissionError, OSError) as e:
            print(f"  {Y}Copy to {loc} failed: {e}{W}")


def archive_mass_test_results(project_root: Path, timestamp: str):
    """Copy mass_test_results.json to backups with timestamp."""
    src = project_root / "data" / "mass_test_results.json"
    if not src.exists():
        return
    try:
        dest = BACKUP_DIR / f"mass_test_results_{timestamp}.json"
        shutil.copy2(src, dest)
        print(f"  Mass test results archived: {dest.name}")
    except (PermissionError, OSError) as e:
        print(f"  {Y}Mass test archive failed: {e}{W}")


# ── Console Output ───────────────────────────────────────────────
def print_header():
    print(f"\n{B}{'='*55}")
    print(f"  WAGGLEDANCE BACKUP & TEST v3.0")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}{W}")


def print_test_summary(test_results: list):
    total_p = sum(tr.passed for tr in test_results)
    total_f = sum(tr.failed for tr in test_results)
    total_w = sum(tr.warned for tr in test_results)
    suites_pass = sum(1 for tr in test_results if tr.status == "PASS")
    total_dur = sum(tr.duration_s for tr in test_results)

    print(f"\n{B}{'='*55}")
    print(f"  TEST SUMMARY")
    print(f"{'='*55}{W}")
    print(f"  Suites: {G}{suites_pass}{W}/{len(test_results)} passed")
    print(f"  Tests:  {G}{total_p} ok{W}, ", end="")
    if total_f:
        print(f"{R}{total_f} fail{W}, ", end="")
    else:
        print(f"0 fail, ", end="")
    print(f"{Y}{total_w} warn{W}")
    print(f"  Time:   {total_dur:.1f}s")

    health = calculate_health_score(test_results, {"mass_test": None})
    if total_f == 0:
        print(f"\n  {G}Health Score: {health}/100{W}")
    else:
        print(f"\n  {Y}Health Score: {health}/100{W}")


def print_final_summary(zip_path, file_count, brief_path, elapsed):
    print(f"\n{B}{'='*55}")
    print(f"  BACKUP COMPLETE")
    print(f"{'='*55}{W}")
    if zip_path:
        size_mb = zip_path.stat().st_size / (1024 * 1024)
        print(f"  Zip:    {zip_path.name} ({size_mb:.1f} MB, {file_count} files)")
    print(f"  Brief:  {brief_path.name}")
    print(f"  Done in {elapsed:.1f}s")


# ── Main ─────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="WaggleDance Backup & Test Tool v3.0")
    parser.add_argument("--skip-tests", action="store_true", help="Skip test execution")
    parser.add_argument("--tests-only", action="store_true", help="Run tests + report only, no zip")
    args = parser.parse_args()

    start_time = time.time()

    print_header()

    # 1. Collect stats
    print(f"\n{C}Collecting project stats...{W}")
    codebase = collect_codebase_stats(PROJECT_ROOT)
    hardware = collect_hardware_info()
    data_stats = collect_data_stats(PROJECT_ROOT)
    ollama_models = collect_ollama_models()

    print(f"  Files: {codebase['total_files']} ({codebase['total_lines']:,} lines)")
    print(f"  Agents: {codebase['agent_count']}")
    print(f"  GPU: {hardware.get('gpu', 'unknown')}")
    if data_stats.get("routing_accuracy"):
        print(f"  Routing: {data_stats['routing_accuracy']}%")

    # 2. Run tests
    test_results = None
    if not args.skip_tests:
        print(f"\n{C}Running {len(TESTS)} test suites...{W}")
        test_results = run_all_tests(PROJECT_ROOT)
        print_test_summary(test_results)

    # 3. Generate AI brief
    ai_brief = generate_ai_brief(
        test_results or [], codebase, hardware, data_stats, ollama_models,
    )

    brief_path = PROJECT_ROOT / "WAGGLEDANCE_AI_BRIEF.md"
    brief_path.write_text(ai_brief, encoding="utf-8")

    # 4. Create backup
    zip_path = None
    file_count = 0
    if not args.tests_only:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"\n{C}Creating backup...{W}")

        zip_path, file_count = do_backup(
            PROJECT_ROOT, BACKUP_DIR, test_results, ai_brief, timestamp,
        )

        create_meta_json(
            timestamp, zip_path, hardware, codebase,
            data_stats, test_results, ollama_models,
        )

        archive_mass_test_results(PROJECT_ROOT, timestamp)

        # Save raw test output separately
        if test_results:
            tests_path = BACKUP_DIR / f"waggle_{timestamp}_tests.txt"
            test_lines = []
            for tr in test_results:
                test_lines.append(f"{'='*70}")
                test_lines.append(f"  {tr.file} — {tr.name} (Phase {tr.phase})")
                test_lines.append(f"  Status: {tr.status} | {tr.passed}p {tr.failed}f {tr.warned}w | {tr.duration_s}s")
                test_lines.append(f"{'='*70}")
                test_lines.append(strip_ansi(tr.raw_output))
                test_lines.append("")
            tests_path.write_text("\n".join(test_lines), encoding="utf-8")

        rotate_backups(BACKUP_DIR)

        # Copy to extra locations
        meta_path = zip_path.with_name(zip_path.stem + "_meta.json")
        copy_to_extra_locations(zip_path, meta_path)

    elapsed = time.time() - start_time
    print_final_summary(zip_path, file_count, brief_path, elapsed)


if __name__ == "__main__":
    main()
