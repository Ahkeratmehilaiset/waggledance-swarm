#!/usr/bin/env python3
"""Generate CURRENT_STATE.md from actual repo contents.

Run: python tools/generate_state.py
Output: CURRENT_STATE.md (repo root)

This script reads the actual files, counts lines, checks classes,
and produces a machine-readable project state file.
No hardcoded data — everything comes from the filesystem.
"""

import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def get_git_info():
    """Get current commit hash and branch."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, text=True).strip()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=ROOT, text=True).strip()
        return commit, branch
    except Exception:
        return "unknown", "unknown"


def count_tests():
    """Count test files and test functions."""
    test_files = 0
    test_functions = 0
    for py in ROOT.joinpath("tests").rglob("*.py"):
        content = py.read_text(errors="ignore")
        funcs = len(re.findall(r"def test_", content))
        if funcs > 0:
            test_files += 1
            test_functions += funcs
    return test_files, test_functions


def scan_modules(base_dir: Path, prefix: str = ""):
    """Scan Python modules and return list of (path, lines, classes, status)."""
    modules = []
    if not base_dir.exists():
        return modules
    for py in sorted(base_dir.rglob("*.py")):
        if py.name == "__init__.py":
            continue
        rel = py.relative_to(ROOT)
        lines = sum(1 for _ in py.open(errors="ignore"))
        content = py.read_text(errors="ignore")
        classes = re.findall(r"^class (\w+)", content, re.MULTILINE)
        # Detect stubs
        is_stub = lines < 20 or ("NotImplementedError" in content and lines < 50)
        status = "Stub" if is_stub else "Complete"
        # Detect projections
        if "projection" in str(rel).lower() or "projector" in str(rel).lower():
            status = "Projection (read-only)"
        modules.append({
            "path": str(rel).replace("\\", "/"),
            "lines": lines,
            "classes": classes,
            "status": status,
        })
    return modules


def scan_security():
    """Check security invariants."""
    checks = {}
    # eval() in symbolic solver
    solver = ROOT / "core" / "symbolic_solver.py"
    if solver.exists():
        content = solver.read_text(errors="ignore")
        raw_evals = len(re.findall(r"(?<!safe_)(?<!\w)eval\(", content))
        checks["raw_eval_in_solver"] = raw_evals == 0
    # safe_eval exists
    checks["safe_eval_exists"] = (ROOT / "core" / "safe_eval.py").exists()
    # MQTT TLS
    mqtt = ROOT / "core" / "mqtt_bridge.py"
    if mqtt.exists():
        content = mqtt.read_text(errors="ignore")
        checks["mqtt_tls_default"] = "mqtt_tls" in content and "True" in content
    # CI exists
    checks["ci_pipeline"] = (ROOT / ".github" / "workflows" / "ci.yml").exists()
    # Resource guard
    checks["resource_guard"] = (ROOT / "core" / "resource_guard.py").exists()
    # Tracing
    checks["otel_tracing"] = (ROOT / "core" / "tracing.py").exists()
    return checks


def scan_licenses():
    """Count BUSL vs Apache files."""
    busl = 0
    apache = 0
    for py in ROOT.rglob("*.py"):
        try:
            head = py.read_text(errors="ignore")[:500]
        except Exception:
            continue
        if "BUSL-1.1" in head:
            busl += 1
        elif "Apache-2.0" in head:
            apache += 1
    return busl, apache


def scan_presets():
    """Scan hardware presets."""
    presets = []
    preset_dir = ROOT / "configs" / "presets"
    if not preset_dir.exists():
        return presets
    for yml in sorted(preset_dir.glob("*.yaml")):
        try:
            import yaml
            data = yaml.safe_load(yml.read_text())
            presets.append({
                "name": yml.stem,
                "profile": data.get("profile", ""),
                "agents_max": data.get("agents_max", ""),
                "model": data.get("ollama_model", ""),
            })
        except Exception:
            presets.append({"name": yml.stem, "profile": "", "agents_max": "", "model": ""})
    return presets


def generate():
    commit, branch = get_git_info()
    test_files, test_funcs = count_tests()
    busl, apache = scan_licenses()
    security = scan_security()
    presets = scan_presets()

    # Scan module trees
    wg_core = scan_modules(ROOT / "waggledance" / "core")
    wg_adapters = scan_modules(ROOT / "waggledance" / "adapters")
    wg_app = scan_modules(ROOT / "waggledance" / "application")
    legacy_core = scan_modules(ROOT / "core")

    total_wg_lines = sum(m["lines"] for m in wg_core + wg_adapters + wg_app)
    total_legacy_lines = sum(m["lines"] for m in legacy_core)

    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    lines = []
    lines.append("# WaggleDance Swarm — Project State (auto-generated)")
    lines.append("")
    lines.append(f"**Generated**: {now}")
    lines.append(f"**Commit**: `{commit}` on `{branch}`")
    lines.append(f"**Generator**: `python tools/generate_state.py`")
    lines.append("")
    lines.append("> This file is auto-generated from actual code. Do not edit manually.")
    lines.append("> Re-run `python tools/generate_state.py` after any major change.")
    lines.append("")

    # Summary
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Hexagonal runtime** (`waggledance/`): {len(wg_core)} core modules, {total_wg_lines:,} lines")
    lines.append(f"- **Legacy core** (`core/`): {len(legacy_core)} modules, {total_legacy_lines:,} lines")
    lines.append(f"- **Tests**: {test_files} files, {test_funcs} test functions")
    lines.append(f"- **Licensing**: {busl} BUSL-protected files, {apache} Apache files")
    lines.append("")

    # Security
    lines.append("## Security Invariants")
    lines.append("")
    for check, passed in security.items():
        icon = "PASS" if passed else "FAIL"
        lines.append(f"- [{icon}] {check.replace('_', ' ')}")
    lines.append("")

    # Presets
    if presets:
        lines.append("## Hardware Presets")
        lines.append("")
        lines.append("| Preset | Profile | Max Agents | Model |")
        lines.append("|--------|---------|--------:|-------|")
        for p in presets:
            lines.append(f"| `{p['name']}` | {p['profile']} | {p['agents_max']} | {p['model']} |")
        lines.append("")

    # Core modules table
    lines.append("## Hexagonal Core Modules (`waggledance/core/`)")
    lines.append("")
    lines.append("| Module | Lines | Classes | Status |")
    lines.append("|--------|------:|---------|--------|")
    for m in sorted(wg_core, key=lambda x: x["path"]):
        cls_str = ", ".join(m["classes"][:3])
        if len(m["classes"]) > 3:
            cls_str += f" +{len(m['classes'])-3}"
        lines.append(f"| `{m['path']}` | {m['lines']} | {cls_str} | {m['status']} |")
    lines.append("")

    # Legacy core modules
    lines.append("## Legacy Core Modules (`core/`)")
    lines.append("")
    lines.append("| Module | Lines | Classes | Status |")
    lines.append("|--------|------:|---------|--------|")
    for m in sorted(legacy_core, key=lambda x: x["path"]):
        cls_str = ", ".join(m["classes"][:3])
        if len(m["classes"]) > 3:
            cls_str += f" +{len(m['classes'])-3}"
        lines.append(f"| `{m['path']}` | {m['lines']} | {cls_str} | {m['status']} |")
    lines.append("")

    # Verification commands
    lines.append("## Verification Commands")
    lines.append("")
    lines.append("```bash")
    lines.append("# Clone and verify:")
    lines.append("git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git")
    lines.append("cd waggledance-swarm")
    lines.append(f"git checkout {commit}")
    lines.append("")
    lines.append("# Count core modules (expect 40+):")
    lines.append('find waggledance/core -name "*.py" -not -name "__init__.py" | wc -l')
    lines.append("")
    lines.append("# Run tests:")
    lines.append("pip install -r requirements.txt")
    lines.append(f"pytest tests/ --collect-only -q | tail -1              # expect {test_funcs}+")
    lines.append("```")

    output = ROOT / "CURRENT_STATE.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Generated {output} ({len(lines)} lines)")


if __name__ == "__main__":
    generate()
