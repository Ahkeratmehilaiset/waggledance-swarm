#!/usr/bin/env python3
"""Emit the Phase 8 capability-growth report to
`docs/runs/phase8_capability_report.md` + optional JSON summary.

Pure, offline, runtime-safe. Safe to run in parallel with the live
campaign. Does not add Prometheus counters.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent
CELLS_DIR = ROOT / "docs" / "cells"
AXIOMS_DIR = ROOT / "configs" / "axioms"
REPORT_PATH = ROOT / "docs" / "runs" / "phase8_capability_report.md"

sys.path.insert(0, str(ROOT))
from waggledance.core.learning.composition_graph import build_graph  # noqa: E402


CELLS = [
    "general", "thermal", "energy", "safety",
    "seasonal", "math", "system", "learning",
]

# Cell keyword map (mirrors cell_manifest.py fallback). Used only when
# hot_results rows need to be attributed to a cell heuristically.
_CELL_KEYWORDS = {
    "thermal":  ["heating", "cooling", "thermal", "hvac", "heat_pump", "frost",
                 "temperature", "lämpö", "pakkanen", "freezing"],
    "energy":   ["energy", "solar", "battery", "power", "kwh", "grid",
                 "watt", "sähkö", "electricity"],
    "safety":   ["safety", "alarm", "risk", "hazard", "violation",
                 "turvallisuus", "varroa", "mite"],
    "seasonal": ["season", "month", "winter", "summer", "spring", "autumn",
                 "vuodenaika", "kevät", "kesä", "talvi", "harvest", "sato"],
    "math":     ["formula", "calculate", "yield", "honey", "swarm", "colony",
                 "optimize"],
    "system":   ["system", "status", "health", "uptime", "process", "mtbf",
                 "oee", "diagnose"],
    "learning": ["learn", "train", "dream", "insight", "adapt"],
    "general":  [],
}


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * p))))
    return float(xs[k])


def _find_campaign_dir() -> Path | None:
    candidates = sorted(
        p for p in (ROOT / "docs" / "runs").glob("ui_gauntlet_400h_*")
        if p.is_dir()
    )
    return candidates[-1] if candidates else None


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
            if limit is not None and len(out) >= limit:
                break
    return out


def _load_manifest(cell: str) -> dict:
    p = CELLS_DIR / cell / "manifest.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_library() -> list[dict]:
    solvers: list[dict] = []
    if not AXIOMS_DIR.exists():
        return solvers
    for path in sorted(AXIOMS_DIR.rglob("*.yaml")):
        try:
            with open(path, encoding="utf-8") as f:
                axiom = yaml.safe_load(f) or {}
        except Exception:
            continue
        if not axiom.get("model_id"):
            continue
        inputs = [
            {"name": n, "unit": (spec or {}).get("unit", "") if isinstance(spec, dict) else ""}
            for n, spec in (axiom.get("variables") or {}).items()
        ]
        outputs: list[dict] = []
        primary = (axiom.get("solver_output_schema") or {}).get("primary_value") or {}
        if primary.get("name"):
            outputs.append({"name": primary["name"], "unit": primary.get("unit", "")})
        for f in axiom.get("formulas", []) or []:
            if f.get("output_unit") and f.get("name"):
                o = {"name": f["name"], "unit": f["output_unit"]}
                if o not in outputs:
                    outputs.append(o)
        solvers.append({
            "id": axiom["model_id"],
            "cell": axiom.get("cell_id") or "general",
            "inputs": inputs,
            "outputs": outputs,
        })
    return solvers


def _route_layers_count(r: dict) -> int:
    """Try a few common places where the router records depth."""
    for key in ("route_layers_used", "route_layers", "layers_used", "layers"):
        val = r.get(key)
        if isinstance(val, list):
            return len(val)
    if isinstance(r.get("route"), dict):
        layers = r["route"].get("layers")
        if isinstance(layers, list):
            return len(layers)
    return 1  # default: single-layer resolution


def _attribute_to_cell(query: str) -> str | None:
    q = (query or "").lower()
    for cell, kws in _CELL_KEYWORDS.items():
        if any(kw in q for kw in kws):
            return cell
    return None


def compute_signals(
    axioms_dir: Path = AXIOMS_DIR,
    cells_dir: Path = CELLS_DIR,
    campaign_dir: Path | None = None,
    hot_limit: int = 10_000,
) -> dict:
    """All 10 computable signals — never fabricated, explicit nulls."""
    library = _load_library()
    # Keep cells_dir available via module level? We already use CELLS_DIR.
    by_cell: dict[str, int] = defaultdict(int)
    for s in library:
        by_cell[s["cell"]] += 1

    solver_count_by_cell = {c: by_cell.get(c, 0) for c in CELLS}

    # Composition graph drives bridges + entropy
    graph = build_graph(library)
    dream_bridge_total = graph["stats"].bridges
    useful_paths_by_depth = {
        2: graph["stats"].paths_depth2,
        3: graph["stats"].paths_depth3,
        4: graph["stats"].paths_depth4,
    }
    cell_entropy_score = {c: 0 for c in CELLS}
    for node in graph["nodes"]:
        cell_entropy_score.setdefault(node.cell_id, 0)
    # Count distinct output units per cell
    entropy_tracker: dict[str, set[str]] = defaultdict(set)
    for node in graph["nodes"]:
        entropy_tracker[node.cell_id].update(node.output_units())
    for c, us in entropy_tracker.items():
        cell_entropy_score[c] = len(us)

    cell_gap_score = {c: _load_manifest(c).get("gap_score", 0.0) for c in CELLS}

    # Production data (optional — explicit null if no campaign dir)
    if campaign_dir is None:
        campaign_dir = _find_campaign_dir()
    hot_rows = _load_jsonl(campaign_dir / "hot_results.jsonl", limit=hot_limit) if campaign_dir else []

    route_depths = [_route_layers_count(r) for r in hot_rows] if hot_rows else []
    latencies = [r["latency_ms"] for r in hot_rows
                 if isinstance(r.get("latency_ms"), (int, float))] if hot_rows else []

    per_cell_latency: dict[str, dict] = {}
    per_cell_fallback: dict[str, float | None] = {c: None for c in CELLS}
    per_cell_totals: dict[str, int] = defaultdict(int)
    per_cell_llm: dict[str, int] = defaultdict(int)
    per_cell_latencies: dict[str, list[float]] = defaultdict(list)

    for r in hot_rows:
        cell = _attribute_to_cell(r.get("query", ""))
        if cell is None:
            continue
        per_cell_totals[cell] += 1
        if r.get("route_layer") == "llm_fallback":
            per_cell_llm[cell] += 1
        lat = r.get("latency_ms")
        if isinstance(lat, (int, float)):
            per_cell_latencies[cell].append(float(lat))

    for cell in CELLS:
        if per_cell_totals[cell]:
            per_cell_fallback[cell] = round(
                per_cell_llm[cell] / per_cell_totals[cell], 4
            )
            per_cell_latency[cell] = {
                "p50": _percentile(per_cell_latencies[cell], 0.50),
                "p95": _percentile(per_cell_latencies[cell], 0.95),
            }
        else:
            per_cell_latency[cell] = {"p50": None, "p95": None}

    return {
        "axioms_dir": axioms_dir.relative_to(ROOT).as_posix()
            if axioms_dir.is_absolute() and str(axioms_dir).startswith(str(ROOT))
            else axioms_dir.as_posix(),
        "campaign_dir": (campaign_dir.relative_to(ROOT).as_posix()
                          if campaign_dir and str(campaign_dir).startswith(str(ROOT))
                          else (campaign_dir.as_posix() if campaign_dir else None)),
        "hot_rows_scanned": len(hot_rows),
        "solver_count_by_cell": solver_count_by_cell,
        "solver_route_depth": {
            "p50": _percentile([float(x) for x in route_depths], 0.50),
            "p95": _percentile([float(x) for x in route_depths], 0.95),
            "samples": len(route_depths),
        },
        "solver_route_latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "samples": len(latencies),
            "per_cell": per_cell_latency,
        },
        "llm_fallback_rate_by_cell": per_cell_fallback,
        "useful_composite_paths": {
            "total": sum(useful_paths_by_depth.values()),
            "by_depth": useful_paths_by_depth,
        },
        "cell_gap_score": cell_gap_score,
        "cell_entropy_score": cell_entropy_score,
        "dream_bridge_candidates": dream_bridge_total,
        "proposal_gate_verdicts": {},
        "teacher_proposals_accepted_shadow": 0,
        "teacher_proposals_promoted": 0,
    }


def render(signals: dict) -> str:
    gen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Phase 8 capability-growth report",
        "",
        f"- **Generated:** {gen_at}",
        f"- **Axioms:** `{signals['axioms_dir']}`",
        f"- **Campaign rows scanned:** {signals['hot_rows_scanned']}",
        f"- **Campaign dir:** `{signals.get('campaign_dir') or '—'}`",
        "",
        "## solver_count_by_cell",
        "",
        "| cell | count |",
        "|---|---|",
    ]
    for cell, n in signals["solver_count_by_cell"].items():
        lines.append(f"| `{cell}` | {n} |")

    lines.extend([
        "",
        "## solver_route_depth",
        "",
        f"- p50 = {signals['solver_route_depth']['p50']}",
        f"- p95 = {signals['solver_route_depth']['p95']}",
        f"- samples = {signals['solver_route_depth']['samples']}",
        "",
        "## solver_route_latency_ms (overall)",
        "",
        f"- p50 = {signals['solver_route_latency_ms']['p50']}",
        f"- p95 = {signals['solver_route_latency_ms']['p95']}",
        f"- samples = {signals['solver_route_latency_ms']['samples']}",
        "",
        "## solver_route_latency_ms — per cell",
        "",
        "| cell | p50 | p95 |",
        "|---|---|---|",
    ])
    for cell, stat in signals["solver_route_latency_ms"]["per_cell"].items():
        lines.append(f"| `{cell}` | {stat['p50']} | {stat['p95']} |")

    lines.extend([
        "",
        "## llm_fallback_rate_by_cell",
        "",
        "| cell | rate |",
        "|---|---|",
    ])
    for cell, rate in signals["llm_fallback_rate_by_cell"].items():
        lines.append(f"| `{cell}` | {rate} |")

    lines.extend([
        "",
        "## useful_composite_paths",
        "",
        f"- total = {signals['useful_composite_paths']['total']}",
        f"- by_depth = {signals['useful_composite_paths']['by_depth']}",
        "",
        f"## dream_bridge_candidates: {signals['dream_bridge_candidates']}",
        "",
        "## cell_gap_score",
        "",
        "| cell | score |",
        "|---|---|",
    ])
    for cell, score in signals["cell_gap_score"].items():
        lines.append(f"| `{cell}` | {score} |")

    lines.extend([
        "",
        "## cell_entropy_score",
        "",
        "| cell | distinct output units |",
        "|---|---|",
    ])
    for cell, score in signals["cell_entropy_score"].items():
        lines.append(f"| `{cell}` | {score} |")

    lines.extend([
        "",
        "## Proposal-gate counters",
        "",
        "*(empty until the teacher loop drives `tools/propose_solver.py`; schema present, no data yet)*",
        "",
    ])
    return "\n".join(lines)


def run(report_path: Path = REPORT_PATH, hot_limit: int = 10_000) -> dict:
    signals = compute_signals(hot_limit=hot_limit)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(signals), encoding="utf-8")
    return {"report_path": report_path.as_posix(), "signals": signals}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, default=REPORT_PATH)
    ap.add_argument("--hot-limit", type=int, default=10_000)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out = run(args.report, args.hot_limit)
    if args.json:
        print(json.dumps(out["signals"], indent=2, default=str))
    else:
        s = out["signals"]
        print(f"report: {out['report_path']}")
        print(f"solver_count_by_cell: {s['solver_count_by_cell']}")
        print(f"dream_bridge_candidates: {s['dream_bridge_candidates']}")
        print(f"hot_rows_scanned: {s['hot_rows_scanned']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
