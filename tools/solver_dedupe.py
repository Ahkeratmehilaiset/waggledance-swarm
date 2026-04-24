#!/usr/bin/env python3
"""Scan axiom YAMLs, compute both legacy and strict solver hashes, and
report duplicates.

Two reports are emitted (stdout and `docs/runs/solver_dedupe_report.md`):

1. **Strict duplicates** — `solver_hash()` collisions. Same formula shape
   AND same outputs/units AND same invariants/tags. A collision is
   almost certainly a real duplicate that should be removed.
2. **Core-semantic collisions** — `canonical_hash()` collisions that are
   NOT strict duplicates. These are cases where two axioms share the
   formula / variable / condition shape but diverge on output unit,
   tags, or invariants. Worth flagging for human review (often the
   result of copy-paste).

The report is deterministic: same inputs produce byte-identical output
(the only non-deterministic field is `generated_at` in a section header).
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
REPORT_PATH = ROOT / "docs" / "runs" / "solver_dedupe_report.md"

sys.path.insert(0, str(ROOT))
from waggledance.core.learning.solver_hash import (  # noqa: E402
    canonical_hash, solver_hash,
)


def _iter_axiom_files(root: Path):
    for path in sorted(root.rglob("*.yaml")):
        yield path


def _load_axiom(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None


def _rel_path(path: Path, base: Path) -> str:
    """Return the path relative to `base` (preferred) or ROOT, falling back
    to the absolute path if it lies outside both. POSIX separator so
    reports are portable across platforms."""
    for anchor in (base, ROOT):
        try:
            return path.relative_to(anchor).as_posix()
        except ValueError:
            continue
    return path.as_posix()


def build_hash_index(axioms_dir: Path) -> dict:
    """Map both hash types → list of axiom file paths."""
    strict: dict[str, list[str]] = defaultdict(list)
    legacy: dict[str, list[str]] = defaultdict(list)
    scanned = 0
    parseable = 0
    for path in _iter_axiom_files(axioms_dir):
        scanned += 1
        axiom = _load_axiom(path)
        if not axiom or not axiom.get("model_id"):
            continue
        parseable += 1
        rel = _rel_path(path, axioms_dir)
        strict[solver_hash(axiom)].append(rel)
        legacy[canonical_hash(axiom)].append(rel)
    return {
        "strict": strict,
        "legacy": legacy,
        "scanned": scanned,
        "parseable": parseable,
        "axioms_dir": axioms_dir,
    }


def find_duplicates(index: dict) -> dict:
    strict_dups = {h: paths for h, paths in index["strict"].items() if len(paths) > 1}
    axioms_dir: Path = index["axioms_dir"]

    def _resolve(rel: str) -> Path:
        p = Path(rel)
        if p.is_absolute():
            return p
        # Try axioms_dir first, then ROOT
        for base in (axioms_dir, ROOT):
            candidate = base / p
            if candidate.exists():
                return candidate
        return axioms_dir / p

    # legacy-collision-not-strict: paths where legacy hash has >1 file,
    # but strict hashes differ → candidate for review
    legacy_split: list[dict] = []
    for h, paths in index["legacy"].items():
        if len(paths) <= 1:
            continue
        buckets: dict[str, list[str]] = defaultdict(list)
        for p in paths:
            axiom = _load_axiom(_resolve(p))
            if axiom is None:
                continue
            buckets[solver_hash(axiom)].append(p)
        if len(buckets) > 1:
            legacy_split.append({
                "legacy_hash": h[:12],
                "subgroups": [
                    {"strict_hash": sh[:12], "paths": sorted(ps)}
                    for sh, ps in sorted(buckets.items())
                ],
            })
    return {"strict_duplicates": strict_dups, "legacy_collisions": legacy_split}


def render_report(index: dict, dups: dict, axioms_dir: Path) -> str:
    gen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    strict_count = len(dups["strict_duplicates"])
    legacy_count = len(dups["legacy_collisions"])
    axioms_display = _rel_path(axioms_dir, ROOT) if axioms_dir.is_absolute() else axioms_dir.as_posix()
    lines = [
        "# Solver dedupe report",
        "",
        f"- **Generated:** {gen_at}",
        f"- **Axiom dir:** `{axioms_display}`",
        f"- **Files scanned:** {index['scanned']}",
        f"- **Parseable axioms:** {index['parseable']}",
        f"- **Strict duplicate groups:** {strict_count}",
        f"- **Legacy-collision groups (strict-distinct):** {legacy_count}",
        "",
        "## 1. Strict duplicates",
        "",
        "These share *every* semantic dimension — formulas, inputs, outputs,",
        "units, conditions, tags, invariants. A match here is almost certainly",
        "a genuine duplicate and should be collapsed.",
        "",
    ]
    if not dups["strict_duplicates"]:
        lines.append("*(none — every axiom is uniquely hashed)*")
    else:
        for h, paths in sorted(dups["strict_duplicates"].items()):
            lines.append(f"### `{h[:12]}`")
            lines.append("")
            for p in sorted(paths):
                lines.append(f"- `{p}`")
            lines.append("")

    lines.extend([
        "",
        "## 2. Core-semantic collisions (strict-distinct)",
        "",
        "These share the legacy `canonical_hash` shape (formulas + variables +",
        "conditions) but diverge on outputs, tags, or invariants. Common",
        "cause: copy-paste adaptation with modified output unit or tag.",
        "",
    ])
    if not dups["legacy_collisions"]:
        lines.append("*(none)*")
    else:
        for group in dups["legacy_collisions"]:
            lines.append(f"### legacy `{group['legacy_hash']}`")
            lines.append("")
            for sg in group["subgroups"]:
                lines.append(f"- strict `{sg['strict_hash']}`:")
                for p in sg["paths"]:
                    lines.append(f"  - `{p}`")
            lines.append("")

    return "\n".join(lines)


def _summary_json(index: dict, dups: dict) -> dict:
    """Deterministic machine-readable summary (no timestamps)."""
    return {
        "scanned": index["scanned"],
        "parseable": index["parseable"],
        "strict_duplicate_groups": len(dups["strict_duplicates"]),
        "legacy_collision_groups": len(dups["legacy_collisions"]),
        "strict_duplicates": {
            h[:12]: sorted(paths)
            for h, paths in sorted(dups["strict_duplicates"].items())
        },
    }


def summary_hash(index: dict, dups: dict) -> str:
    """Hash of the summary — stable across runs when nothing changed."""
    payload = _summary_json(index, dups)
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def run_dedupe(axioms_dir: Path = AXIOMS_DIR,
               report_path: Path = REPORT_PATH) -> dict:
    index = build_hash_index(axioms_dir)
    dups = find_duplicates(index)
    md = render_report(index, dups, axioms_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(md, encoding="utf-8")
    return {
        "report_path": report_path.as_posix(),
        "summary": _summary_json(index, dups),
        "summary_hash": summary_hash(index, dups),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--axioms-dir", type=Path, default=AXIOMS_DIR)
    ap.add_argument("--report", type=Path, default=REPORT_PATH)
    ap.add_argument("--json", action="store_true",
                    help="Print JSON summary to stdout")
    args = ap.parse_args()

    result = run_dedupe(args.axioms_dir, args.report)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"report written: {result['report_path']}")
        s = result["summary"]
        print(f"  scanned: {s['scanned']}  parseable: {s['parseable']}")
        print(f"  strict duplicates: {s['strict_duplicate_groups']}")
        print(f"  legacy collisions: {s['legacy_collision_groups']}")
        print(f"  summary_hash: {result['summary_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
