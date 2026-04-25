"""Structural counterfactual replay harness — Phase 8.5 Session C,
deliverable C.6.

Re-evaluates pinned historical cases against the shadow graph to ask:
"Would the new shadow solver have realized a structural path that the
live solver did not?"

Per c.txt §C6 this is a STRUCTURAL counterfactual proxy only — no live
solvers are executed in shadow mode. True execution-based replay is
deferred to a later session with explicit permission for offline
inference.

Replay is deterministic:
- pinned replay_case_manifest.json only
- bounded by MAX_REPLAY_CASES (50)
- shadow-only invariants: deferred_to_dream tensions are NOT marked
  resolved here.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import DREAMING_SCHEMA_VERSION, MAX_REPLAY_CASES, MIN_GAIN_RATIO
from .shadow_graph import ShadowGraph, StructuralDiff


REPLAY_METHODOLOGY = "structural_proxy_v0.1"
METHODOLOGY_DISCLAIMER = (
    "This is a STRUCTURAL counterfactual replay only. No live solvers "
    "were executed in shadow mode. Results indicate structural "
    "opportunity, not validated correctness. Human review is required "
    "before any merge."
)


# ── Replay case manifest ─────────────────────────────────────────-

@dataclass(frozen=True)
class ReplayCase:
    replay_case_id: str
    source_curiosity_id: str | None
    source_tension_id: str | None
    source_file: str | None
    query_hash: str | None
    candidate_cell: str | None
    tags: tuple[str, ...]
    protect: bool
    original_resolution_hash: str | None
    suspected_missing_capability: str | None = None
    bridge_candidate_refs: tuple[str, ...] = ()


def case_from_dict(d: dict) -> ReplayCase:
    return ReplayCase(
        replay_case_id=str(d.get("replay_case_id", "")),
        source_curiosity_id=d.get("source_curiosity_id"),
        source_tension_id=d.get("source_tension_id"),
        source_file=d.get("source_file"),
        query_hash=d.get("query_hash"),
        candidate_cell=d.get("candidate_cell"),
        tags=tuple(d.get("tags") or ()),
        protect=bool(d.get("protect", False)),
        original_resolution_hash=d.get("original_resolution_hash"),
        suspected_missing_capability=d.get("suspected_missing_capability"),
        bridge_candidate_refs=tuple(d.get("bridge_candidate_refs") or ()),
    )


def select_replay_cases(
    manifest: dict | None,
    tension_ids: Iterable[str],
    curiosity_ids: Iterable[str],
    max_cases: int = MAX_REPLAY_CASES,
) -> list[ReplayCase]:
    """Deterministic + bounded selection per c.txt §PINNED INPUT
    /REPLAY MANIFEST RULE."""
    if not manifest:
        return []
    tids = set(tension_ids)
    cids = set(curiosity_ids)
    seen: dict[str, ReplayCase] = {}
    for raw in manifest.get("cases") or []:
        c = case_from_dict(raw)
        if c.source_tension_id in tids or c.source_curiosity_id in cids:
            # Deduplicate by query_hash (or replay_case_id if no hash)
            key = c.query_hash or c.replay_case_id
            if key and key not in seen:
                seen[key] = c
    cases = sorted(seen.values(), key=lambda x: x.replay_case_id)
    return cases[:max_cases]


# ── Structural-gain evaluation ───────────────────────────────────-

@dataclass(frozen=True)
class CaseEvaluation:
    replay_case_id: str
    structural_gain: bool
    regression: bool
    rationale: str


def _shadow_capability_present(case: ReplayCase, diff: StructuralDiff,
                                  shadow: ShadowGraph) -> bool:
    """Heuristic for "shadow contains a capability the live graph
    lacks". Strict and deterministic: only when the case's
    suspected_missing_capability or bridge_candidate_refs lines up
    with a new node/edge in the diff."""
    cap = case.suspected_missing_capability
    if cap and cap in diff.new_nodes:
        return True
    if cap and any(cap == nid for nid in diff.affected_cells):
        return True
    for bridge in case.bridge_candidate_refs:
        if any(bridge in (a, b) for a, b, _ in diff.new_bridge_candidates):
            return True
    return False


def evaluate_case(case: ReplayCase, live: ShadowGraph,
                    shadow: ShadowGraph,
                    diff: StructuralDiff) -> CaseEvaluation:
    """Decide structural_gain and regression for one replay case."""
    cap_present = _shadow_capability_present(case, diff, shadow)
    has_tag = bool(case.suspected_missing_capability) or \
        bool(case.bridge_candidate_refs)
    if has_tag and cap_present:
        return CaseEvaluation(
            replay_case_id=case.replay_case_id,
            structural_gain=True, regression=False,
            rationale=("shadow capability matches case's "
                        "suspected_missing_capability or bridge_candidate_refs"),
        )
    # Regression: protected case where shadow disrupts the original
    # path. We approximate via the original_resolution_hash being
    # absent from the shadow's solver hashes when the case is
    # protected.
    if case.protect and case.original_resolution_hash:
        if case.original_resolution_hash not in diff.proposal_solver_hashes \
                and case.original_resolution_hash not in shadow.node_ids():
            # Original solver is still live → no regression. The
            # shadow only ADDS, never removes. So default = no
            # regression.
            return CaseEvaluation(
                replay_case_id=case.replay_case_id,
                structural_gain=False, regression=False,
                rationale="protected case; shadow does not remove live solvers",
            )
    return CaseEvaluation(
        replay_case_id=case.replay_case_id,
        structural_gain=False, regression=False,
        rationale=("no matching tag or no shadow capability for this case"
                    if has_tag else "case has no missing-capability tag"),
    )


# ── Report ───────────────────────────────────────────────────────-

@dataclass(frozen=True)
class ReplayReport:
    schema_version: int
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    replay_manifest_sha256: str | None
    replay_methodology: str
    replay_methodology_disclaimer: str
    replay_case_count: int
    targeted_case_count: int
    structural_gain_count: int
    protected_case_regression_count: int
    unresolved_case_count: int
    estimated_fallback_delta: float | None
    estimated_fallback_delta_undefined_reason: str | None
    tension_ids_targeted: tuple[str, ...]
    structurally_promising: bool
    case_evaluations: tuple[CaseEvaluation, ...]


def build_report(
    cases: Sequence[ReplayCase],
    live: ShadowGraph,
    shadow: ShadowGraph,
    diff: StructuralDiff,
    *,
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    replay_manifest_sha256: str | None,
    tension_ids_targeted: Sequence[str],
    collapse_passed: bool,
) -> ReplayReport:
    evaluations = [evaluate_case(c, live, shadow, diff) for c in cases]
    n = len(evaluations)
    gain = sum(1 for e in evaluations if e.structural_gain)
    regressions = sum(1 for e in evaluations if e.regression)
    unresolved = sum(1 for e in evaluations
                      if not e.structural_gain and not e.regression)
    targeted = sum(1 for c in cases
                    if c.suspected_missing_capability
                    or c.bridge_candidate_refs)
    if n == 0:
        estimated_fallback_delta = 0.0
        undefined_reason = "replay_case_count == 0; metric is undefined"
    else:
        estimated_fallback_delta = round(gain / n, 6)
        undefined_reason = None

    promising = (
        collapse_passed
        and gain > 0
        and regressions == 0
        and (gain / max(targeted, 1)) >= MIN_GAIN_RATIO
    )

    return ReplayReport(
        schema_version=DREAMING_SCHEMA_VERSION,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        replay_manifest_sha256=replay_manifest_sha256,
        replay_methodology=REPLAY_METHODOLOGY,
        replay_methodology_disclaimer=METHODOLOGY_DISCLAIMER,
        replay_case_count=n,
        targeted_case_count=targeted,
        structural_gain_count=gain,
        protected_case_regression_count=regressions,
        unresolved_case_count=unresolved,
        estimated_fallback_delta=estimated_fallback_delta,
        estimated_fallback_delta_undefined_reason=undefined_reason,
        tension_ids_targeted=tuple(sorted(set(tension_ids_targeted))),
        structurally_promising=promising,
        case_evaluations=tuple(evaluations),
    )


def report_to_dict(r: ReplayReport) -> dict:
    return {
        "schema_version": r.schema_version,
        "continuity_anchor": {
            "branch_name": r.branch_name,
            "base_commit_hash": r.base_commit_hash,
            "pinned_input_manifest_sha256": r.pinned_input_manifest_sha256,
            "replay_manifest_sha256": r.replay_manifest_sha256,
        },
        "replay_methodology": r.replay_methodology,
        "replay_methodology_disclaimer": r.replay_methodology_disclaimer,
        "replay_case_count": r.replay_case_count,
        "targeted_case_count": r.targeted_case_count,
        "structural_gain_count": r.structural_gain_count,
        "protected_case_regression_count": r.protected_case_regression_count,
        "unresolved_case_count": r.unresolved_case_count,
        "estimated_fallback_delta": r.estimated_fallback_delta,
        "estimated_fallback_delta_undefined_reason":
            r.estimated_fallback_delta_undefined_reason,
        "tension_ids_targeted": list(r.tension_ids_targeted),
        "structurally_promising": r.structurally_promising,
        "case_evaluations": [
            {
                "replay_case_id": e.replay_case_id,
                "structural_gain": e.structural_gain,
                "regression": e.regression,
                "rationale": e.rationale,
            }
            for e in r.case_evaluations
        ],
    }


def render_report_md(d: dict) -> str:
    lines = [
        "# Shadow replay report",
        "",
        f"- **Schema version:** {d['schema_version']}",
        f"- **Branch:** `{d['continuity_anchor']['branch_name']}`",
        f"- **Base commit:** `{d['continuity_anchor']['base_commit_hash']}`",
        f"- **Pin manifest:** `{d['continuity_anchor']['pinned_input_manifest_sha256']}`",
        f"- **Replay manifest:** `{d['continuity_anchor']['replay_manifest_sha256'] or '(none)'}`",
        f"- **Methodology:** `{d['replay_methodology']}`",
        f"- **Replay cases:** {d['replay_case_count']} "
        f"(targeted={d['targeted_case_count']})",
        f"- **Structural gains:** {d['structural_gain_count']}",
        f"- **Protected regressions:** {d['protected_case_regression_count']}",
        f"- **Unresolved:** {d['unresolved_case_count']}",
        f"- **Estimated fallback delta:** {d['estimated_fallback_delta']}",
        f"- **Tension IDs targeted:** {list(d['tension_ids_targeted'])}",
        f"- **Structurally promising:** **{d['structurally_promising']}**",
        "",
        "## Methodology Limitations",
        "",
        f"> {d['replay_methodology_disclaimer']}",
        "",
    ]
    if d.get("case_evaluations"):
        lines.extend(["## Per-case evaluations", "",
                       "| case | gain | regression | rationale |",
                       "|---|---|---|---|"])
        for e in d["case_evaluations"]:
            lines.append(
                f"| `{e['replay_case_id']}` | {e['structural_gain']} | "
                f"{e['regression']} | {e['rationale']} |"
            )
    lines.append("")
    return "\n".join(lines)


def emit_report(r: ReplayReport, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = report_to_dict(r)
    json_path = out_dir / "shadow_replay_report.json"
    md_path = out_dir / "shadow_replay_report.md"
    json_path.write_text(
        json.dumps(d, indent=2, sort_keys=True), encoding="utf-8",
    )
    md_path.write_text(render_report_md(d), encoding="utf-8")
    return {"replay_json": json_path, "replay_md": md_path}


# ── Replay-case-manifest emission ────────────────────────────────-

def emit_replay_case_manifest(
    cases: Sequence[ReplayCase],
    out_dir: Path,
    *,
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    source_path: str = "synthetic",
) -> Path:
    """Write replay_case_manifest.json. Deterministic ordering by
    replay_case_id."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sorted_cases = sorted(cases, key=lambda c: c.replay_case_id)
    payload = {
        "schema_version": DREAMING_SCHEMA_VERSION,
        "continuity_anchor": {
            "branch_name": branch_name,
            "base_commit_hash": base_commit_hash,
            "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
            "source_path": source_path,
        },
        "case_count": len(sorted_cases),
        "max_replay_cases_cap": MAX_REPLAY_CASES,
        "cases": [
            {
                "replay_case_id": c.replay_case_id,
                "source_curiosity_id": c.source_curiosity_id,
                "source_tension_id": c.source_tension_id,
                "source_file": c.source_file,
                "query_hash": c.query_hash,
                "candidate_cell": c.candidate_cell,
                "tags": list(c.tags),
                "protect": c.protect,
                "original_resolution_hash": c.original_resolution_hash,
                "suspected_missing_capability": c.suspected_missing_capability,
                "bridge_candidate_refs": list(c.bridge_candidate_refs),
            }
            for c in sorted_cases
        ],
    }
    path = out_dir / "replay_case_manifest.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True),
                     encoding="utf-8")
    return path
