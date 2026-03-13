#!/usr/bin/env python3
"""
WaggleDance Backup & Test Tool v6.0
=====================================
Runs full component tests, generates AI report, creates backup.
Supports 75-agent profile system (gadget/cottage/home/factory).
Supports incremental backups (only changed files since last backup).

Usage:
    python tools/waggle_backup.py              # Full: tests + report + backup
    python tools/waggle_backup.py --skip-tests # Report + backup (no tests)
    python tools/waggle_backup.py --tests-only # Tests + report only (no zip)
    python tools/waggle_backup.py --incremental # Incremental backup (changed files only)
    python tools/waggle_backup.py --incremental --skip-tests  # Fast incremental
"""

import argparse
import dataclasses
import hashlib
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

# ── Auto-install dependencies ──
try:
    _project = Path(__file__).resolve().parent
    if _project.name == "tools":
        _project = _project.parent
    sys.path.insert(0, str(_project))
    from core.auto_install import ensure_dependencies
    ensure_dependencies()
except Exception:
    pass  # Graceful — backup tool should never crash on dep check

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
    {"file": "tests/test_all.py",       "name": "General Diagnostics",    "phase": "1-2",   "args": ["--offline"], "timeout": 60},
    {"file": "tests/test_pipeline.py",  "name": "Translation Pipeline",   "phase": "2",     "args": [],            "timeout": 600},
    {"file": "tests/test_phase3.py",    "name": "Social Learning",        "phase": "3",     "args": [],            "timeout": 120},
    {"file": "tests/test_phase4.py",    "name": "Advanced Learning",      "phase": "4",     "args": [],            "timeout": 600},
    {"file": "tests/test_phase4ijk.py", "name": "Bilingual/Cache/Enrich", "phase": "4ijk",  "args": [],            "timeout": 300},
    {"file": "tests/test_phase8.py",    "name": "External Data Feeds",    "phase": "8",     "args": [],            "timeout": 60},
    {"file": "tests/test_phase9.py",    "name": "Autonomous Learning",    "phase": "9",     "args": [],            "timeout": 60},
    {"file": "tests/test_phase10.py",   "name": "Micro-Model Training",   "phase": "10",    "args": [],            "timeout": 60},
    {"file": "tests/test_normalizer.py",        "name": "Normalizer",              "phase": "4-norm", "args": [], "timeout": 30},
    {"file": "tests/test_corrections.py",       "name": "Corrections Memory",      "phase": "4-corr", "args": [], "timeout": 300},
    {"file": "tests/test_routing_centroids.py", "name": "Routing Centroids",       "phase": "4-cent", "args": [], "timeout": 120},
    {"file": "tests/test_seasonal_guard.py",    "name": "Seasonal Guard",          "phase": "4-seas", "args": [], "timeout": 30},
    {"file": "tests/test_night_enricher.py",    "name": "Night Enricher",          "phase": "4-enr",  "args": [], "timeout": 120},
    # Phase B — Reliability
    {"file": "tests/test_b2_eviction.py",         "name": "Memory Eviction",         "phase": "B2", "args": [], "timeout": 60},
    {"file": "tests/test_b3_circuit_breaker.py",  "name": "Circuit Breaker",         "phase": "B3", "args": [], "timeout": 60},
    {"file": "tests/test_b4_error_handling.py",   "name": "Error Handling",          "phase": "B4", "args": [], "timeout": 60},
    # Phase C — Performance
    {"file": "tests/test_c1_hotcache.py",         "name": "HotCache Auto-fill",      "phase": "C1", "args": [], "timeout": 60},
    {"file": "tests/test_c2_lru_cache.py",        "name": "LRU Cache",               "phase": "C2", "args": [], "timeout": 60},
    {"file": "tests/test_c3_batch_pipeline.py",   "name": "Batch Pipeline",          "phase": "C3", "args": [], "timeout": 60},
    {"file": "tests/test_c4_readiness.py",        "name": "Readiness Check",         "phase": "C4", "args": [], "timeout": 60},
    {"file": "tests/test_c5_structured_logging.py","name": "Structured Logging",     "phase": "C5", "args": [], "timeout": 60},
    # Phase D — Autonomy
    {"file": "tests/test_d1_d2_d3_autonomy.py",  "name": "Autonomy (D1-D3)",        "phase": "D1-D3","args": [], "timeout": 60},
    # Phase 5 — Smart Home Sensors
    {"file": "tests/test_smart_home.py",          "name": "Smart Home Sensors",       "phase": "5",    "args": [], "timeout": 60},
    # Core module unit tests
    {"file": "tests/test_core_token_economy.py",  "name": "Token Economy",            "phase": "core", "args": [], "timeout": 30},
    {"file": "tests/test_core_llm_provider.py",   "name": "LLM Provider",             "phase": "core", "args": [], "timeout": 30},
    {"file": "tests/test_core_normalizer.py",     "name": "Finnish Normalizer",       "phase": "core", "args": [], "timeout": 30},
    {"file": "tests/test_core_yaml_bridge.py",    "name": "YAML Bridge",              "phase": "core", "args": [], "timeout": 60},
    {"file": "tests/test_core_fast_memory.py",    "name": "Fast Memory (HotCache)",   "phase": "core", "args": [], "timeout": 30},
    {"file": "tests/test_core_ops_agent.py",      "name": "OpsAgent",                 "phase": "core", "args": [], "timeout": 30},
    {"file": "tests/test_core_learning_engine.py","name": "Learning Engine",          "phase": "core", "args": [], "timeout": 30},
    # Phase 6 — Audio Sensors
    {"file": "tests/test_phase6_audio.py",       "name": "Audio Sensors",            "phase": "6",    "args": [], "timeout": 60},
    # Phase 10 — Training Data Collector
    {"file": "tests/test_training_collector.py", "name": "Training Collector",       "phase": "10",   "args": [], "timeout": 30},
    # Swarm Routing
    {"file": "tests/test_swarm_routing.py",      "name": "Swarm Routing",            "phase": "core", "args": [], "timeout": 30},
    # Phase 11 — Elastic Scaler
    {"file": "tests/test_elastic_scaler.py",     "name": "Elastic Scaler",           "phase": "11",   "args": [], "timeout": 30},
    # Phase 7 — Voice Interface
    {"file": "tests/test_phase7_voice.py",       "name": "Voice Interface",          "phase": "7",    "args": [], "timeout": 60},
    # Phase 8 — External Data Feeds
    {"file": "tests/test_phase8_feeds.py",       "name": "Data Feeds",               "phase": "8",    "args": [], "timeout": 60},
    # Layer 1 — MAGMA Memory Architecture
    {"file": "tests/test_memory_proxy.py",      "name": "Memory Proxy",             "phase": "L1",   "args": [], "timeout": 60},
    {"file": "tests/test_replay_engine.py",    "name": "Replay Engine",            "phase": "L2",   "args": [], "timeout": 60},
    {"file": "tests/test_layer3_wiring.py",   "name": "Layer 3 Wiring",           "phase": "L3",   "args": [], "timeout": 60},
    # Layer 4 — Cross-Agent Memory Sharing
    {"file": "tests/test_layer4_cross_agent.py", "name": "Layer 4 Cross-Agent",  "phase": "L4",   "args": [], "timeout": 60},
    # Layer 5 — Trust & Reputation Engine
    {"file": "tests/test_layer5_trust.py",        "name": "Layer 5 Trust Engine", "phase": "L5",   "args": [], "timeout": 60},
    # Cognitive Graph
    {"file": "tests/test_cognitive_graph.py",     "name": "Cognitive Graph",      "phase": "CG",   "args": [], "timeout": 60},
    # Overlay System Expansion
    {"file": "tests/test_overlay_system.py",      "name": "Overlay System",       "phase": "OV",   "args": [], "timeout": 60},
    # Agent YAML Validation
    {"file": "tests/test_agent_yaml_validation.py", "name": "Agent YAML Validation", "phase": "YAML", "args": [], "timeout": 60},
    # Phase 6-8: Migration, Auth, Training
    {"file": "tests/test_migrate_db.py", "name": "Migration + Auth + Training", "phase": "P6-8", "args": [], "timeout": 60},
    # Week 11 Sprint tests
    {"file": "tests/test_v1_training.py",    "name": "V1 Training",          "phase": "W11", "args": [], "timeout": 60},
    {"file": "tests/test_dedup_gate.py",     "name": "Dedup Gate",           "phase": "W11", "args": [], "timeout": 60},
    {"file": "tests/test_metrics_fields.py", "name": "Metrics Fields",       "phase": "W11", "args": [], "timeout": 60},
    {"file": "tests/test_quality_scoring.py","name": "Quality Scoring",      "phase": "W11", "args": [], "timeout": 60},
    {"file": "tests/test_rss_activation.py", "name": "RSS Activation",       "phase": "W11", "args": [], "timeout": 60},
    # Axiom Engine (S1-S5)
    {"file": "tests/test_symbolic_solver.py",   "name": "Symbolic Solver",   "phase": "AX",  "args": [], "timeout": 30},
    {"file": "tests/test_constraint_engine.py", "name": "Constraint Engine", "phase": "AX",  "args": [], "timeout": 30},
    {"file": "tests/test_explainability.py",    "name": "Explainability",    "phase": "AX",  "args": [], "timeout": 30},
    # v1.0.0 — Domain Capsule + Model E2E
    {"file": "tests/test_domain_capsule.py",   "name": "Domain Capsule",    "phase": "v1",  "args": [], "timeout": 30},
    {"file": "tests/test_model_e2e.py",        "name": "Model E2E Chain",   "phase": "v1",  "args": [], "timeout": 30},
    # v1.1.0 — FAISS vector store
    {"file": "tests/test_faiss_store.py",      "name": "FAISS Store",       "phase": "v1",  "args": [], "timeout": 30},
]

# Backup exclusions
# NOTE: chroma_db intentionally NOT excluded — it is the primary knowledge base (CRITICAL backup)
EXCLUDE_DIRS = {
    "__pycache__", ".git", "backups", "node_modules", "dist",
    ".claude", ".venv", "venv", "chromadb_data", "chroma",
    "logs", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "test_consciousness_v2",
    "unsloth_compiled_cache",
}

EXCLUDE_EXTENSIONS = {".pyc", ".pyo", ".pyd", ".pt", ".onnx", ".egg-info"}

EXCLUDE_PATTERNS = [
    "*_backup_*.py", "fix_*.py", "patch_*.py", "hotfix_*.py", "*_mega_patch.py",
]

# Files excluded for security or irrelevance
EXCLUDE_FILES = {".env"}

# Root-level log files (large, not useful in restore)
EXCLUDE_ROOT_EXTENSIONS = {".log"}


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
            encoding="utf-8",
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

    # Count agents per profile
    profile_counts = {"gadget": 0, "cottage": 0, "home": 0, "factory": 0}
    for agent_name in agents:
        core_yaml = project_root / "agents" / agent_name / "core.yaml"
        if core_yaml.exists():
            try:
                import yaml
                data = yaml.safe_load(core_yaml.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    for p in data.get("profiles", []):
                        if p in profile_counts:
                            profile_counts[p] += 1
            except Exception:
                pass

    # Read active profile from settings
    active_profile = "cottage"
    settings_path = project_root / "configs" / "settings.yaml"
    if settings_path.exists():
        try:
            import yaml
            cfg = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
            active_profile = cfg.get("profile", "cottage") if isinstance(cfg, dict) else "cottage"
        except Exception:
            pass

    return {
        "files": counts,
        "total_files": sum(counts.values()),
        "total_lines": total_lines,
        "agent_count": len(agents),
        "agents": sorted(agents),
        "profile_counts": profile_counts,
        "active_profile": active_profile,
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
    cm = project_root / "configs" / "confusion_memory.json"
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

    # Corrections count (ChromaDB collection)
    stats["corrections_count"] = 0
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(project_root / "data" / "chroma_db"))
        coll = client.get_collection("corrections")
        stats["corrections_count"] = coll.count()
    except Exception:
        pass

    # Seasonal rules count
    stats["seasonal_rules_count"] = 0
    sr = project_root / "configs" / "seasonal_rules.yaml"
    if sr.exists():
        try:
            import yaml
            data = yaml.safe_load(sr.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "rules" in data and isinstance(data["rules"], dict):
                stats["seasonal_rules_count"] = len(data["rules"])
        except Exception:
            pass

    # Voikko normalizer available
    stats["voikko_available"] = (project_root / "core" / "normalizer.py").exists()

    # MicroModel V1 promoted patterns
    stats["micro_v1_promoted"] = 0
    mv1 = project_root / "configs" / "micro_v1_patterns.json"
    if mv1.exists():
        try:
            data = json.loads(mv1.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "promoted" in data and isinstance(data["promoted"], dict):
                stats["micro_v1_promoted"] = len(data["promoted"])
        except Exception:
            pass

    # Structured logging metrics (C5)
    stats["metrics_log_lines"] = 0
    stats["metrics_last_ts"] = None
    stats["metrics_avg_ms"] = None
    stats["metrics_cache_hit_rate"] = None
    stats["metrics_halluc_rate"] = None
    ml = project_root / "data" / "learning_metrics.jsonl"
    if ml.exists():
        try:
            lines = [l.strip() for l in ml.read_text(encoding="utf-8").splitlines() if l.strip()]
            stats["metrics_log_lines"] = len(lines)
            chat_lines = [json.loads(l) for l in lines if json.loads(l).get("method") == "chat"]
            if chat_lines:
                stats["metrics_last_ts"] = chat_lines[-1].get("ts", "")
                times = [r.get("response_time_ms", 0) for r in chat_lines if r.get("response_time_ms")]
                if times:
                    stats["metrics_avg_ms"] = round(sum(times) / len(times), 1)
                hits = sum(1 for r in chat_lines if r.get("cache_hit"))
                stats["metrics_cache_hit_rate"] = round(hits / len(chat_lines), 3)
                hallucs = sum(1 for r in chat_lines if r.get("was_hallucination"))
                stats["metrics_halluc_rate"] = round(hallucs / len(chat_lines), 3)
        except Exception:
            pass

    # Weekly report (D3)
    stats["weekly_report"] = None
    wr = project_root / "data" / "weekly_report.json"
    if wr.exists():
        try:
            data = json.loads(wr.read_text(encoding="utf-8"))
            stats["weekly_report"] = {
                "generated_at": data.get("generated_at", ""),
                "total_queries": data.get("total_queries", 0),
                "avg_response_ms": data.get("avg_response_ms", 0),
                "cache_hit_rate": data.get("cache_hit_rate", 0),
                "hallucination_rate": data.get("hallucination_rate", 0),
            }
        except Exception:
            pass

    # ChromaDB size (informational)
    stats["chroma_db_mb"] = 0
    chroma_path = project_root / "data" / "chroma_db"
    if chroma_path.exists():
        try:
            total_bytes = sum(f.stat().st_size for f in chroma_path.rglob("*") if f.is_file())
            stats["chroma_db_mb"] = round(total_bytes / (1024 * 1024), 1)
        except Exception:
            pass

    # Morning report (last session)
    stats["morning_report"] = None
    mr = project_root / "data" / "morning_reports.jsonl"
    if mr.exists():
        try:
            last_line = ""
            for line in mr.read_text(encoding="utf-8").strip().splitlines():
                if line.strip():
                    last_line = line.strip()
            if last_line:
                data = json.loads(last_line)
                stats["morning_report"] = {
                    "total_stored": data.get("total_stored", 0),
                    "total_checked": data.get("total_checked", 0),
                    "session_duration_min": data.get("session_duration_min", 0),
                    "overall_pass_rate": data.get("overall_pass_rate", 0),
                }
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
    pc = codebase.get("profile_counts", {})
    if pc:
        lines.append(f"- Profile breakdown: gadget={pc.get('gadget',0)}, cottage={pc.get('cottage',0)}, home={pc.get('home',0)}, factory={pc.get('factory',0)}")
    lines.append(f"- Active profile: {codebase.get('active_profile', 'cottage')}")
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

    # Phase 4 Components
    lines.append("## Phase 4 Components")
    voikko_status = "installed" if data_stats.get("voikko_available") else "not found"
    lines.append(f"- Voikko Normalizer: {voikko_status}")

    # Hot cache size from settings.yaml
    hot_cache_size = "unknown"
    try:
        import yaml as _yaml  # noqa: F811
        _settings_path = PROJECT_ROOT / "configs" / "settings.yaml"
        if _settings_path.exists():
            _settings = _yaml.safe_load(_settings_path.read_text(encoding="utf-8"))
            if isinstance(_settings, dict):
                _al = _settings.get("advanced_learning", {})
                if isinstance(_al, dict):
                    hot_cache_size = _al.get("hot_cache_size", "unknown")
    except Exception:
        pass
    lines.append(f"- Hot Cache + fi_fast: {hot_cache_size}")

    lines.append(f"- Corrections Memory: {data_stats.get('corrections_count', 0)} corrections in ChromaDB")
    lines.append(f"- Specialty Centroids: {data_stats.get('confusion_entries', 0)} routing entries")
    lines.append(f"- Seasonal Guard: {data_stats.get('seasonal_rules_count', 0)} rules loaded")
    lines.append(f"- MicroModel V1: {data_stats.get('micro_v1_promoted', 0)} promoted patterns")

    mr = data_stats.get("morning_report")
    if mr:
        lines.append(
            f"- NightEnricher: last session {mr['session_duration_min']:.0f}min, "
            f"{mr['total_stored']} stored / {mr['total_checked']} checked, "
            f"pass rate {mr['overall_pass_rate']:.0%}"
        )
    else:
        lines.append("- NightEnricher: no sessions yet")
    lines.append("")

    # Structured Logging & Autonomy (C5, D3)
    ml_lines = data_stats.get("metrics_log_lines", 0)
    if ml_lines > 0:
        lines.append("## Structured Logging (C5) & Autonomy (D1–D3)")
        lines.append(f"- Metrics log: {ml_lines} entries in learning_metrics.jsonl")
        if data_stats.get("metrics_avg_ms") is not None:
            lines.append(f"- Avg response time: {data_stats['metrics_avg_ms']} ms")
        if data_stats.get("metrics_cache_hit_rate") is not None:
            lines.append(f"- Cache hit rate: {data_stats['metrics_cache_hit_rate']:.1%}")
        if data_stats.get("metrics_halluc_rate") is not None:
            lines.append(f"- Hallucination rate: {data_stats['metrics_halluc_rate']:.1%}")
        wr = data_stats.get("weekly_report")
        if wr:
            lines.append(f"- Weekly report: {wr['total_queries']} queries, {wr['generated_at'][:10]}")
        else:
            lines.append("- Weekly report: not yet generated")
        chroma_mb = data_stats.get("chroma_db_mb", 0)
        lines.append(f"- ChromaDB size: {chroma_mb} MB (included in backup)")
        lines.append("")

    lines.append("## Current Phase Status")
    lines.append("- Phase 1: COMPLETE (consciousness v2, dual embed, smart router)")
    lines.append("- Phase 2: COMPLETE (94% benchmark, 3148 facts)")
    lines.append("- Phase 3: COMPLETE (Round Table, agent levels, night mode)")
    lines.append("- Phase 4: COMPLETE (normalizer, corrections, centroids, seasonal guard, night enricher)")
    lines.append("- Phase B: COMPLETE (pipeline wiring, priority lock, circuit breaker, error handling)")
    lines.append("- Phase C: COMPLETE (hotcache, LRU cache, batch pipeline, readiness check, structured logging)")
    lines.append("- Phase D: COMPLETE (web/RSS/distill sources, convergence detection, meta-learning weekly report)")
    lines.append("- Agent Restructuring: COMPLETE (50 agents renamed FI->EN, profile system, 25 new agents)")
    lines.append("- Phase 5-11: SPECIFIED (Camera, Audio, Voice, Weather, Auto-learning, MicroModel, Scaling)")
    lines.append("")

    lines.append("---")
    lines.append(f"*Generated by waggle_backup.py v5.0 | {now.strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


# ── Backup Engine ────────────────────────────────────────────────
def should_include(path: Path, project_root: Path) -> bool:
    """Check if a file should be included in backup."""
    rel = path.relative_to(project_root)

    # Check excluded directories
    for part in rel.parts:
        if part in EXCLUDE_DIRS:
            return False

    # Check excluded specific filenames (e.g. .env with secrets)
    if path.name in EXCLUDE_FILES:
        return False

    # Check root-level log files (large, not useful in restore)
    if len(rel.parts) == 1 and path.suffix.lower() in EXCLUDE_ROOT_EXTENSIONS:
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


def checkpoint_sqlite_databases(project_root: Path):
    """Checkpoint all SQLite WAL files before backup to ensure consistency."""
    import sqlite3
    data_dir = project_root / "data"
    if not data_dir.exists():
        return
    checkpointed = []
    for db_file in data_dir.glob("*.db"):
        try:
            conn = sqlite3.connect(str(db_file), timeout=5)
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            checkpointed.append(db_file.name)
        except Exception:
            pass  # DB may be locked by running process — skip gracefully
    if checkpointed:
        print(f"  {G}WAL checkpoint:{W} {', '.join(checkpointed)}")


MANIFEST_FILE = "backup_manifest.json"


def _file_hash(path: Path) -> str:
    """Fast hash: for files >1MB use size+mtime, else SHA-256."""
    st = path.stat()
    if st.st_size > 1_048_576:
        return f"fast:{st.st_size}:{st.st_mtime_ns}"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(backup_dir: Path) -> dict:
    """Load the previous backup manifest (file→hash mapping).

    Checks backup_dir first, then falls back to EXTRA_BACKUP_LOCATIONS
    (C:/D: persistent drives) — needed after RAM drive (U:) power loss.
    """
    # Try local first
    p = backup_dir / MANIFEST_FILE
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Fallback to persistent drives (U: is RAM, manifest lost on reboot)
    for loc in EXTRA_BACKUP_LOCATIONS:
        fp = loc / MANIFEST_FILE
        if fp.exists():
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                # Restore to RAM drive for this session
                try:
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    p.write_text(fp.read_text(encoding="utf-8"), encoding="utf-8")
                    print(f"  {G}Manifest recovered from {loc}{W}")
                except Exception:
                    pass
                return data
            except Exception:
                pass
    return {}


def save_manifest(backup_dir: Path, manifest: dict):
    """Save the current backup manifest atomically."""
    p = backup_dir / MANIFEST_FILE
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def do_backup(
    project_root: Path,
    backup_dir: Path,
    test_results: list,
    ai_brief: str,
    timestamp: str,
    incremental: bool = False,
) -> Path:
    """Create timestamped zip backup. If incremental=True, only changed files."""
    backup_dir.mkdir(parents=True, exist_ok=True)

    # Disk space check before backup
    try:
        sys.path.insert(0, str(project_root))
        from core.disk_guard import check_disk_space
        check_disk_space(str(backup_dir), label="Backup")
    except Exception as e:
        if "critically low" in str(e).lower() or "REFUSED" in str(e):
            print(f"\n{R}BACKUP ABORTED: {e}{W}")
            return None
        # Non-critical errors (ImportError etc.) → continue

    # Checkpoint SQLite WAL files for consistent backup
    checkpoint_sqlite_databases(project_root)

    # Load previous manifest for incremental comparison
    old_manifest = load_manifest(backup_dir) if incremental else {}
    new_manifest = {}

    prefix = "waggle_incr_" if incremental else "waggle_"
    zip_name = f"{prefix}{timestamp}.zip"
    zip_path = backup_dir / zip_name
    file_count = 0
    skipped = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(project_root.rglob("*")):
            if not p.is_file():
                continue
            if not should_include(p, project_root):
                continue
            rel = str(p.relative_to(project_root))
            try:
                h = _file_hash(p)
                new_manifest[rel] = h
                # Incremental: skip unchanged files
                if incremental and old_manifest.get(rel) == h:
                    skipped += 1
                    continue
                zf.write(p, rel)
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

    # Save updated manifest (always — so next incremental knows the baseline)
    save_manifest(backup_dir, new_manifest)

    if incremental:
        print(f"  {G}Incremental:{W} {file_count} changed, {skipped} unchanged (skipped)")

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
        "version": "5.0",
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
            "profile_counts": codebase.get("profile_counts", {}),
            "active_profile": codebase.get("active_profile", "cottage"),
        },
        "ollama_models": ollama_models,
        "data_stats": {
            "scan_facts": data_stats["scan_facts"],
            "confusion_entries": data_stats["confusion_entries"],
            "routing_accuracy": data_stats.get("routing_accuracy"),
            "corrections_count": data_stats.get("corrections_count", 0),
            "seasonal_rules_count": data_stats.get("seasonal_rules_count", 0),
            "voikko_available": data_stats.get("voikko_available", False),
            "micro_v1_promoted": data_stats.get("micro_v1_promoted", 0),
            "metrics_log_lines": data_stats.get("metrics_log_lines", 0),
            "metrics_avg_ms": data_stats.get("metrics_avg_ms"),
            "metrics_cache_hit_rate": data_stats.get("metrics_cache_hit_rate"),
            "metrics_halluc_rate": data_stats.get("metrics_halluc_rate"),
            "chroma_db_mb": data_stats.get("chroma_db_mb", 0),
            "weekly_report": data_stats.get("weekly_report"),
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
    """Copy backup + manifest to persistent locations (C:, D:) and rotate."""
    manifest_src = BACKUP_DIR / MANIFEST_FILE
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
            # Copy manifest to persistent drive (U: is RAM — survives only here)
            if manifest_src.exists():
                shutil.copy2(manifest_src, loc / MANIFEST_FILE)
            # Copy restore.bat to persistent drive so it survives RAM loss
            restore_bat = PROJECT_ROOT / "restore.bat"
            if restore_bat.exists():
                shutil.copy2(restore_bat, loc / "restore.bat")
            # Rotate old backups on persistent drives too
            rotate_backups(loc)
            print(f"  {G}Copied to {loc} (rotated to {MAX_BACKUPS}){W}")
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
    print(f"  WAGGLEDANCE BACKUP & TEST v5.0")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}{W}")


def print_test_summary(test_results: list, data_stats: dict = None):
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

    health = calculate_health_score(test_results, data_stats or {})
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
    parser = argparse.ArgumentParser(description="WaggleDance Backup & Test Tool v6.0")
    parser.add_argument("--skip-tests", action="store_true", help="Skip test execution")
    parser.add_argument("--tests-only", action="store_true", help="Run tests + report only, no zip")
    parser.add_argument("--incremental", action="store_true", help="Incremental backup (only changed files)")
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
    pc = codebase.get("profile_counts", {})
    if pc:
        print(f"  Profiles: gadget={pc.get('gadget',0)} cottage={pc.get('cottage',0)} home={pc.get('home',0)} factory={pc.get('factory',0)}")
    print(f"  Active profile: {codebase.get('active_profile', 'cottage')}")
    print(f"  GPU: {hardware.get('gpu', 'unknown')}")
    if data_stats.get("routing_accuracy"):
        print(f"  Routing: {data_stats['routing_accuracy']}%")

    # 2. Run tests
    test_results = None
    if not args.skip_tests:
        print(f"\n{C}Running {len(TESTS)} test suites...{W}")
        test_results = run_all_tests(PROJECT_ROOT)
        print_test_summary(test_results, data_stats)

    # 3. Generate AI brief
    ai_brief = generate_ai_brief(
        test_results or [], codebase, hardware, data_stats, ollama_models,
    )

    # AI brief goes to drive root (U:\) — not inside project
    brief_path = PROJECT_ROOT.parent / "WAGGLEDANCE_AI_BRIEF.md" if PROJECT_ROOT.name == "project" else PROJECT_ROOT / "WAGGLEDANCE_AI_BRIEF.md"
    brief_path.write_text(ai_brief, encoding="utf-8")

    # 4. Create backup
    zip_path = None
    file_count = 0
    if not args.tests_only:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        print(f"\n{C}Creating backup...{W}")

        zip_path, file_count = do_backup(
            PROJECT_ROOT, BACKUP_DIR, test_results, ai_brief, timestamp,
            incremental=args.incremental,
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
