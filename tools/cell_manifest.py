#!/usr/bin/env python3
"""Cell manifest generator — per-cell state cards for the Claude-teacher pipeline.

Context budget: each manifest is ONE cell only — hard limit so a teacher sees
~20-50 solvers max, never the whole library. This is the structural defense
against teacher confusion when scaling to thousands of solvers.

Determinism: the manifest payload (everything except `generated_at` and
`manifest_hash`) hashes to `manifest_hash`. Two runs with identical inputs
produce byte-identical `manifest.json` and identical `manifest_hash`. The
only non-deterministic field is the human-friendly `generated_at`.

Reads:
  - configs/axioms/<domain>/*.yaml      (registered symbolic solvers)
  - configs/capsules/<domain>.yaml      (key_decisions → routing keywords)
  - waggledance/core/hex_cell_topology.py (cell IDs, adjacency, intent map)
  - docs/runs/ui_gauntlet_400h_*/hot_results.jsonl (production gaps, latency)

Emits:
  - docs/cells/<cell_id>/MANIFEST.md     (human-readable)
  - docs/cells/<cell_id>/manifest.json   (teacher-feed structured input)

Usage:
    python tools/cell_manifest.py                # generate all cells
    python tools/cell_manifest.py --cell thermal # single cell
    python tools/cell_manifest.py --no-production-scan  # skip hot_results

Missing metric inputs produce explicit null / [] in the manifest, never a
fabricated value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
AXIOMS_DIR = ROOT / "configs" / "axioms"
CAPSULES_DIR = ROOT / "configs" / "capsules"
CELLS_OUT_DIR = ROOT / "docs" / "cells"

MANIFEST_SCHEMA_VERSION = 1

# Keys excluded from the deterministic hash (they are inherently per-run).
_HASH_EXCLUDED_KEYS = ("generated_at", "manifest_hash")


# Cell IDs and ring-1 adjacency are mirrored from
# waggledance/core/hex_cell_topology.py. Keeping a local copy avoids
# importing runtime code (and its dependencies) from a pure offline tool.
CELLS = [
    "general", "thermal", "energy", "safety",
    "seasonal", "math", "system", "learning",
]

_ADJACENCY = {
    "general":  {"safety", "seasonal", "math", "learning"},
    "thermal":  {"energy", "seasonal", "safety"},
    "energy":   {"thermal", "safety", "math"},
    "safety":   {"thermal", "energy", "system", "general"},
    "seasonal": {"thermal", "general", "learning"},
    "math":     {"energy", "general", "system"},
    "system":   {"safety", "math", "learning"},
    "learning": {"seasonal", "general", "system"},
}


def _ring2(cell: str) -> set[str]:
    """Ring-2 = neighbors-of-neighbors minus self minus ring-1."""
    siblings = _ADJACENCY.get(cell, set())
    out: set[str] = set()
    for s in siblings:
        out |= _ADJACENCY.get(s, set())
    out -= siblings
    out.discard(cell)
    return out


# Fallback heuristic for axioms that lack an explicit `cell_id` field.
# Modern axioms set cell_id directly (see tools/upgrade_axioms_for_v3.py).
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


def _canonical_json(obj) -> str:
    """Stable JSON serialization used both for hashing and for disk writes."""
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def _stable_signature(axiom: dict) -> str:
    """Short hash identifying the *semantic* shape of the solver.

    Includes formulas and variables in a stable form; excludes metadata like
    generated_at, reviewed_by, reviewed_at, description strings. Matching
    semantics → matching signature even if YAML keys are reordered or
    comments are edited.
    """
    core = {
        "formulas": [
            {
                "name": f.get("name"),
                "formula": (f.get("formula") or "").strip(),
                "output_unit": f.get("output_unit"),
            }
            for f in axiom.get("formulas", [])
        ],
        "variables": {
            name: {
                "unit": v.get("unit"),
                "range": v.get("range"),
            }
            for name, v in (axiom.get("variables") or {}).items()
        },
        "primary_output": (axiom.get("solver_output_schema") or {})
            .get("primary_value", {})
            .get("name"),
    }
    core["formulas"].sort(key=lambda x: x["name"] or "")
    return hashlib.sha256(
        json.dumps(core, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]


def _load_axiom(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_capsule(domain: str) -> dict:
    p = CAPSULES_DIR / f"{domain}.yaml"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _classify_axiom_to_cell(axiom: dict, domain_capsule: dict) -> str:
    # Prefer explicit cell_id set by tools/upgrade_axioms_for_v3.py
    explicit = axiom.get("cell_id")
    if explicit in CELLS:
        return explicit

    mid = axiom.get("model_id", "")
    name = axiom.get("model_name", "")
    desc = axiom.get("description", "")
    text = " ".join([mid, name, desc]).lower()
    for kd in domain_capsule.get("key_decisions", []):
        if kd.get("model") == mid:
            text += " " + " ".join(kd.get("keywords", [])).lower()

    scores = {}
    for cell, keywords in _CELL_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score:
            scores[cell] = score
    if not scores:
        return "general"
    return max(scores, key=scores.get)


def _axiom_tags(axiom: dict) -> list[str]:
    tags: set[str] = set()
    for kw in axiom.get("tags", []) or []:
        tags.add(str(kw).lower())
    # Derive from formula output units and range types
    for f in axiom.get("formulas", []):
        unit = f.get("output_unit")
        if unit:
            tags.add(f"unit:{unit}")
    review = (axiom.get("placement_review") or {}).get("status")
    if review:
        tags.add(f"placement:{review}")
    return sorted(tags)


def _quality_tier(axiom: dict) -> str | None:
    """Map `placement_review.status` to a coarse quality tier where known."""
    review = (axiom.get("placement_review") or {}).get("status")
    if review is None:
        return None
    mapping = {
        "approved": "GOLD",
        "auto_heuristic": "SILVER",
        "proposed": "BRONZE",
        "quarantined": "QUARANTINE",
    }
    return mapping.get(review, None)


def _last_validation_time(axiom: dict) -> str | None:
    return (axiom.get("placement_review") or {}).get("reviewed_at")


def _io_signature(axiom: dict) -> tuple[list[str], list[str]]:
    inputs = list((axiom.get("variables") or {}).keys())
    outputs: list[str] = []
    primary = (axiom.get("solver_output_schema") or {}).get("primary_value") or {}
    if primary.get("name"):
        outputs.append(primary["name"])
    for f in axiom.get("formulas", []):
        name = f.get("name")
        if name and name not in outputs and f.get("output_unit"):
            outputs.append(name)
    return inputs, outputs


def _collect_all_solvers() -> list[dict]:
    rows = []
    if not AXIOMS_DIR.exists():
        return rows
    for domain_dir in sorted(AXIOMS_DIR.iterdir()):
        if not domain_dir.is_dir():
            continue
        domain = domain_dir.name
        capsule = _load_capsule(domain)
        for ax_path in sorted(domain_dir.glob("*.yaml")):
            axiom = _load_axiom(ax_path)
            if not axiom.get("model_id"):
                continue
            cell = _classify_axiom_to_cell(axiom, capsule)
            inputs, outputs = _io_signature(axiom)
            rows.append({
                "id": axiom["model_id"],
                "name": axiom.get("model_name", ""),
                "description": axiom.get("description", "")[:120],
                "cell": cell,
                "domain": domain,
                "axiom_file": ax_path.relative_to(ROOT).as_posix(),
                "inputs": inputs,
                "outputs": outputs,
                "n_formulas": len(axiom.get("formulas", [])),
                "tags": _axiom_tags(axiom),
                "quality_tier": _quality_tier(axiom),
                "last_validation_time": _last_validation_time(axiom),
                "signature": _stable_signature(axiom),
            })
    return rows


def _find_campaign_dir() -> Path | None:
    candidates = sorted(
        p for p in (ROOT / "docs" / "runs").glob("ui_gauntlet_400h_*")
        if p.is_dir()
    )
    return candidates[-1] if candidates else None


def _load_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
            if limit is not None and len(rows) >= limit:
                break
    return rows


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round((len(xs) - 1) * p))))
    return float(xs[k])


def _production_signals(cell: str, hot_rows: list[dict]) -> dict:
    """Scan already-loaded hot_results rows for this cell.

    Emits explicit nulls if signal cannot be computed, never fabricated
    values.
    """
    keywords = set(_CELL_KEYWORDS.get(cell, []))
    if not keywords:
        return {
            "unresolved_examples": [],
            "unresolved_count": 0,
            "top_fallback_queries": [],
            "latency_p50_ms": None,
            "latency_p95_ms": None,
            "llm_fallback_rate": None,
            "training_pair_count": 0,
        }

    matched: list[dict] = []
    unresolved: list[dict] = []
    fallback_counter: dict[str, int] = defaultdict(int)
    latencies: list[float] = []
    llm_fallback_matches = 0

    for r in hot_rows:
        q = (r.get("query", "") or "").lower()
        if not any(kw in q for kw in keywords):
            continue
        matched.append(r)
        if not r.get("responded"):
            unresolved.append({
                "query_id": r.get("query_id"),
                "ts": (r.get("ts", "") or "")[:19],
                "bucket": r.get("bucket"),
                "error": (r.get("error") or "")[:80],
                "latency_ms": r.get("latency_ms"),
            })
        # Any query whose bucket is a fallback or whose route_layer is llm
        # counts toward LLM fallback rate
        route_layer = r.get("route_layer")
        if route_layer == "llm_fallback":
            llm_fallback_matches += 1
            fallback_counter[q[:80]] += 1
        lat = r.get("latency_ms")
        if isinstance(lat, (int, float)):
            latencies.append(float(lat))

    top_fallback = sorted(
        fallback_counter.items(), key=lambda kv: kv[1], reverse=True
    )[:5]

    return {
        "unresolved_examples": unresolved[:10],
        "unresolved_count": len(unresolved),
        "top_fallback_queries": [{"query": q, "count": c} for q, c in top_fallback],
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "llm_fallback_rate": (
            round(llm_fallback_matches / len(matched), 4) if matched else None
        ),
        "training_pair_count": len(matched),
    }


def _compute_gap_score(
    solvers_in_cell: list[dict],
    unresolved_count: int,
    total_queries_in_campaign: int,
    total_library_size: int,
) -> float:
    library_size = max(1, total_library_size)
    cell_share = len(solvers_in_cell) / library_size
    cell_underpopulation = max(0.0, 0.125 - cell_share)  # expected 1/8 in each
    unresolved_rate = (
        unresolved_count / total_queries_in_campaign
        if total_queries_in_campaign > 0 else 0.0
    )
    return round(min(1.0, cell_underpopulation * 5 + unresolved_rate * 3), 3)


def _manifest_payload(cell: str, all_solvers: list[dict],
                      hot_rows: list[dict]) -> dict:
    cell_solvers = sorted(
        [s for s in all_solvers if s["cell"] == cell],
        key=lambda s: s["id"],
    )
    prod = _production_signals(cell, hot_rows) if hot_rows else {
        "unresolved_examples": [],
        "unresolved_count": 0,
        "top_fallback_queries": [],
        "latency_p50_ms": None,
        "latency_p95_ms": None,
        "llm_fallback_rate": None,
        "training_pair_count": 0,
    }
    gap_score = _compute_gap_score(
        cell_solvers,
        prod["unresolved_count"],
        len(hot_rows),
        len(all_solvers),
    )

    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "cell_id": cell,
        "parent": None,           # flat topology today
        "level": 0,
        "siblings": sorted(_ADJACENCY.get(cell, set())),
        "neighbors": sorted(_ring2(cell)),
        "solver_count": len(cell_solvers),
        "solvers": cell_solvers,
        "top_open_gaps": prod["unresolved_examples"],
        "top_fallback_queries": prod["top_fallback_queries"],
        "recent_rejections": [],     # no proposal gate yet → [] is explicit
        "candidate_bridge_edges": [], # no composition graph yet → []
        "training_pair_count": prod["training_pair_count"],
        "contradiction_count": 0,    # no contradiction detector yet
        "latency_p50_ms": prod["latency_p50_ms"],
        "latency_p95_ms": prod["latency_p95_ms"],
        "llm_fallback_rate": prod["llm_fallback_rate"],
        "gap_score": gap_score,
        "teacher_protocol_reminder": (
            "This is the ONLY context you see. Propose 1-3 new solvers or "
            "one improvement. Return YAML matching the schema of axiom "
            "files in configs/axioms/<domain>/*.yaml plus the proposal "
            "schema at schemas/solver_proposal.schema.json. Tests REQUIRED. "
            "The quality gate (tools/propose_solver.py) verifies schema, "
            "determinism, hash uniqueness, and in-cell contradictions "
            "before merging. Duplicates rejected by hash."
        ),
    }


def _compute_hash(payload: dict) -> str:
    stripped = {k: v for k, v in payload.items() if k not in _HASH_EXCLUDED_KEYS}
    data = _canonical_json(stripped).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def generate_cell_manifest(
    cell: str,
    all_solvers: list[dict] | None = None,
    hot_rows: list[dict] | None = None,
) -> tuple[Path, Path]:
    if all_solvers is None:
        all_solvers = _collect_all_solvers()
    if hot_rows is None:
        hot_rows = []

    payload = _manifest_payload(cell, all_solvers, hot_rows)
    payload["manifest_hash"] = _compute_hash(payload)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out_dir = CELLS_OUT_DIR / cell
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / "manifest.json"
    json_path.write_text(_canonical_json(payload), encoding="utf-8")

    md_path = out_dir / "MANIFEST.md"
    md_path.write_text(_render_md(payload), encoding="utf-8")
    return json_path, md_path


def _render_md(m: dict) -> str:
    cell = m["cell_id"]
    lines = [
        f"# Cell manifest — `{cell}`",
        "",
        f"- **Schema version:** {m['schema_version']}",
        f"- **Generated:** {m['generated_at']}",
        f"- **Manifest hash:** `{m['manifest_hash']}`",
        f"- **Level:** {m['level']}   **Parent:** {m['parent'] or '—'}",
        f"- **Solver count:** {m['solver_count']}",
        f"- **Gap score:** {m['gap_score']:.3f}  *(0 = saturated, 1 = empty/failing)*",
        f"- **Siblings (ring-1):** {', '.join(m['siblings']) or '—'}",
        f"- **Neighbors (ring-2):** {', '.join(m['neighbors']) or '—'}",
        f"- **Latency p50/p95:** "
        f"{m['latency_p50_ms']} / {m['latency_p95_ms']} ms",
        f"- **LLM fallback rate:** {m['llm_fallback_rate']}",
        "",
        "## Existing solvers",
        "",
    ]
    if m["solvers"]:
        lines.extend([
            "| id | signature | domain | inputs | outputs | formulas | tier |",
            "|---|---|---|---|---|---|---|",
        ])
        for s in m["solvers"]:
            inputs_s = ", ".join(s["inputs"][:4]) + ("…" if len(s["inputs"]) > 4 else "")
            outputs_s = ", ".join(s["outputs"][:3]) + ("…" if len(s["outputs"]) > 3 else "")
            lines.append(
                f"| `{s['id']}` | `{s['signature']}` | {s['domain']} | "
                f"{inputs_s} | {outputs_s} | {s['n_formulas']} | "
                f"{s['quality_tier'] or '—'} |"
            )
    else:
        lines.append("*(none — cell is empty; high-priority for teaching)*")

    lines.extend([
        "",
        "## Open gaps from production",
        "",
        f"- Unresolved queries matching cell vocabulary: "
        f"**{len(m['top_open_gaps'])}** (shown) / total **{m['training_pair_count']}** matched",
    ])
    for ex in m["top_open_gaps"]:
        lines.append(
            f"  - `{ex['query_id']}` ({ex['bucket']}, {ex['latency_ms']}ms) at {ex['ts']}"
        )

    lines.extend(["", "## Top LLM-fallback queries", ""])
    if m["top_fallback_queries"]:
        for t in m["top_fallback_queries"]:
            lines.append(f"- [{t['count']}x] {t['query']}")
    else:
        lines.append("*(none in scanned campaign data)*")

    lines.extend([
        "",
        "## Teaching protocol",
        "",
        m["teacher_protocol_reminder"],
        "",
    ])
    return "\n".join(lines)


def generate_index(
    all_solvers: list[dict],
    hot_rows: list[dict],
) -> Path:
    """Top-level cross-cell index. Humans only — teacher never sees this."""
    cell_stats = defaultdict(lambda: {"solvers": 0, "unresolved": 0})
    for s in all_solvers:
        cell_stats[s["cell"]]["solvers"] += 1
    if hot_rows:
        for cell in CELLS:
            cell_stats[cell]["unresolved"] = _production_signals(cell, hot_rows)["unresolved_count"]

    lines = [
        "# Hex-cell library index",
        "",
        f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"**Total solvers:** {len(all_solvers)}",
        f"**Campaign rows analysed:** {len(hot_rows)}",
        "",
        "| cell | solvers | gap score | unresolved prod queries | siblings |",
        "|---|---|---|---|---|",
    ]
    for cell in CELLS:
        gs = _compute_gap_score(
            [s for s in all_solvers if s["cell"] == cell],
            cell_stats[cell]["unresolved"],
            len(hot_rows),
            len(all_solvers),
        )
        sibs = ", ".join(sorted(_ADJACENCY.get(cell, set())))
        lines.append(
            f"| `{cell}` | {cell_stats[cell]['solvers']} | {gs:.3f} | "
            f"{cell_stats[cell]['unresolved']} | {sibs} |"
        )

    lines.append("")
    idx_path = CELLS_OUT_DIR / "INDEX.md"
    CELLS_OUT_DIR.mkdir(parents=True, exist_ok=True)
    idx_path.write_text("\n".join(lines), encoding="utf-8")
    return idx_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell", help="Generate one cell only (default: all)")
    ap.add_argument("--no-production-scan", action="store_true",
                    help="Skip gap-signal scan of campaign jsonl")
    ap.add_argument("--campaign-limit", type=int, default=5000,
                    help="Max hot_results.jsonl rows to scan (default 5000)")
    args = ap.parse_args()

    all_solvers = _collect_all_solvers()

    hot_rows: list[dict] = []
    if not args.no_production_scan:
        campaign_dir = _find_campaign_dir()
        if campaign_dir:
            hot_rows = _load_jsonl(campaign_dir / "hot_results.jsonl",
                                   limit=args.campaign_limit)
            print(f"scanned {len(hot_rows)} rows from {campaign_dir.name}")

    if args.cell:
        if args.cell not in CELLS:
            print(f"unknown cell '{args.cell}'. Valid: {CELLS}", file=sys.stderr)
            return 1
        j, m = generate_cell_manifest(args.cell, all_solvers, hot_rows)
        print(f"generated {m.relative_to(ROOT)}")
    else:
        for cell in CELLS:
            j, m = generate_cell_manifest(cell, all_solvers, hot_rows)
            print(f"  {cell:9} -> {m.relative_to(ROOT)}")
        idx = generate_index(all_solvers, hot_rows)
        print(f"index: {idx.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
