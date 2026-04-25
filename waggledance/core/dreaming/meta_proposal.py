"""Shadow-only dream meta-proposal artifact — Phase 8.5 Session C,
deliverable C.7.

Emits dream_meta_proposal.{json,md} only when the underlying
collapse + replay outcome is structurally promising (c.txt §C7).

CRITICAL BOUNDARY (c.txt §C7):
This artifact may RECOMMEND that a future human reviewer consider a
solver for runtime entry, but it MUST NOT perform that mutation. No
runtime flip happens here. No live registration. Shadow-only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from . import DREAMING_SCHEMA_VERSION, MIN_GAIN_RATIO
from .collapse import CollapseReport, CollapsedProposal
from .replay import ReplayReport
from .curriculum import normalize_severity


# ── Solver hash ──────────────────────────────────────────────────-

def solver_hash_for_proposal(proposal: dict) -> str:
    """Deterministic structural hash of a proposal's solver shape.
    Matches the spirit of the propose_solver gate's hash_duplicate
    logic without depending on its private state."""
    if not proposal:
        return "sha256:empty"
    fields = {
        "cell_id": proposal.get("cell_id"),
        "solver_name": proposal.get("solver_name"),
        "inputs": proposal.get("inputs"),
        "outputs": proposal.get("outputs"),
        "formula_or_algorithm": proposal.get("formula_or_algorithm"),
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Confidence + expected_value_of_merging ────────────────────────

def compute_confidence(*, gate_passed: bool, no_protected_regressions: bool,
                          structural_gain_ratio: float,
                          min_gain_ratio: float = MIN_GAIN_RATIO) -> float:
    """Per c.txt §C7 default formula:

        confidence =
            (gate_passed ? 0.5 : 0.0)
          + (no_protected_regressions ? 0.3 : 0.0)
          + (structural_gain_ratio >= MIN_GAIN_RATIO ? 0.2 : 0.0)

    Clamp to [0, 1]."""
    c = 0.0
    if gate_passed:
        c += 0.5
    if no_protected_regressions:
        c += 0.3
    if structural_gain_ratio >= min_gain_ratio:
        c += 0.2
    return max(0.0, min(1.0, round(c, 6)))


def compute_expected_value(*, estimated_fallback_delta: float,
                              tension_severity_max: float,
                              confidence: float) -> float:
    """Per c.txt §C7:

        expected_value_of_merging =
            estimated_fallback_delta
          × tension_severity_max
          × confidence
    """
    return round(estimated_fallback_delta
                  * tension_severity_max
                  * confidence, 6)


# ── Meta-proposal data class ─────────────────────────────────────-

@dataclass(frozen=True)
class DreamMetaProposal:
    schema_version: int
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    replay_manifest_sha256: str | None
    consumed_hook_contracts: tuple[dict, ...]
    source_tension_ids: tuple[str, ...]
    selected_proposal_path: str
    selected_proposal_id: str | None
    selected_proposal_solver_name: str | None
    cell_id: str | None
    solver_hash: str
    gate_provenance: dict[str, str]
    collapse_verdict: str
    raw_verdict: str
    replay_metrics: dict
    structural_gains: dict
    expected_value_of_merging: float
    confidence: float
    tension_severity_max: float
    uncertainty: str
    why_human_review_required: str
    why_runtime_flip_is_out_of_scope: str
    structurally_promising: bool


# ── Construction ─────────────────────────────────────────────────-

def is_structurally_promising(replay: ReplayReport) -> bool:
    return bool(replay.structurally_promising)


def select_proposal_for_meta(report: CollapseReport) -> CollapsedProposal | None:
    """Pick the first ACCEPT_CANDIDATE in deterministic order."""
    for c in report.proposals_evaluated:
        if c.collapse_verdict == "ACCEPT_CANDIDATE":
            return c
    return None


def build_meta_proposal(
    *,
    collapse: CollapseReport,
    replay: ReplayReport,
    self_model: dict,
    consumed_hook_contracts: Sequence[dict],
    selected_proposal: CollapsedProposal | None = None,
    proposal_data: dict | None = None,
) -> DreamMetaProposal | None:
    """Return a DreamMetaProposal iff the outcome is structurally
    promising AND a candidate proposal exists, else None."""
    if not is_structurally_promising(replay):
        return None
    chosen = selected_proposal or select_proposal_for_meta(collapse)
    if chosen is None:
        return None
    if proposal_data is None:
        try:
            proposal_data = json.loads(
                Path(chosen.proposal_path).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            proposal_data = {}

    # Tension severity max from self-model
    sev_max = 0.0
    targeted = set(replay.tension_ids_targeted)
    for t in self_model.get("workspace_tensions") or []:
        if t.get("tension_id") in targeted:
            sev_max = max(sev_max, normalize_severity(t.get("severity")))

    structural_gain_ratio = (
        replay.structural_gain_count / max(replay.targeted_case_count, 1)
    )
    gate_passed = chosen.collapse_verdict == "ACCEPT_CANDIDATE"
    no_regressions = replay.protected_case_regression_count == 0
    confidence = compute_confidence(
        gate_passed=gate_passed,
        no_protected_regressions=no_regressions,
        structural_gain_ratio=structural_gain_ratio,
    )
    estimated_delta = float(replay.estimated_fallback_delta or 0.0)
    expected_value = compute_expected_value(
        estimated_fallback_delta=estimated_delta,
        tension_severity_max=sev_max,
        confidence=confidence,
    )

    uncertainty = (
        "high" if confidence < 0.4 else
        "medium" if confidence < 0.7 else
        "low"
    )

    return DreamMetaProposal(
        schema_version=DREAMING_SCHEMA_VERSION,
        branch_name=collapse.branch_name,
        base_commit_hash=collapse.base_commit_hash,
        pinned_input_manifest_sha256=collapse.pinned_input_manifest_sha256,
        replay_manifest_sha256=replay.replay_manifest_sha256,
        consumed_hook_contracts=tuple(consumed_hook_contracts),
        source_tension_ids=tuple(sorted(replay.tension_ids_targeted)),
        selected_proposal_path=chosen.proposal_path,
        selected_proposal_id=chosen.proposal_id,
        selected_proposal_solver_name=chosen.solver_name,
        cell_id=chosen.cell_id,
        solver_hash=solver_hash_for_proposal(proposal_data),
        gate_provenance=dict(sorted(chosen.gate_provenance.items())),
        collapse_verdict=chosen.collapse_verdict,
        raw_verdict=chosen.raw_verdict,
        replay_metrics={
            "replay_methodology": replay.replay_methodology,
            "replay_case_count": replay.replay_case_count,
            "targeted_case_count": replay.targeted_case_count,
            "structural_gain_count": replay.structural_gain_count,
            "protected_case_regression_count":
                replay.protected_case_regression_count,
            "unresolved_case_count": replay.unresolved_case_count,
            "estimated_fallback_delta": replay.estimated_fallback_delta,
        },
        structural_gains={
            "structural_gain_ratio": round(structural_gain_ratio, 6),
            "min_gain_ratio_required": MIN_GAIN_RATIO,
        },
        expected_value_of_merging=expected_value,
        confidence=confidence,
        tension_severity_max=sev_max,
        uncertainty=uncertainty,
        why_human_review_required=(
            "This is a structural counterfactual proxy only. No live "
            "solver execution was performed. A human reviewer must "
            "validate correctness, run the proposal's tests, and "
            "confirm that no protected case regresses before any "
            "runtime entry."
        ),
        why_runtime_flip_is_out_of_scope=(
            "Session C is shadow-only by spec (c.txt §C5/§C7). No "
            "runtime mutation, no axiom write, and no live "
            "registration may occur in this session. Runtime flip is "
            "deferred to a later gated session with explicit "
            "permission."
        ),
        structurally_promising=replay.structurally_promising,
    )


# ── Hook-contract enforcement ────────────────────────────────────-

def validate_hook_contracts(consumed: Sequence[dict],
                                repo_root: Path) -> list[str]:
    """Each consumed hook contract entry must carry file/version/sha
    and the on-disk sha256 of the file must match. Returns a list of
    human-readable error strings; empty list = ok."""
    errors: list[str] = []
    for entry in consumed:
        path = entry.get("file")
        recorded_sha = entry.get("file_sha256")
        version = entry.get("version")
        if not path or not recorded_sha or version is None:
            errors.append(
                f"hook contract entry missing required fields: {entry}",
            )
            continue
        full = repo_root / path
        if not full.exists():
            errors.append(f"hook contract file missing on disk: {path}")
            continue
        actual = "sha256:" + hashlib.sha256(
            full.read_bytes()
        ).hexdigest()
        if actual != recorded_sha:
            errors.append(
                f"hook contract sha mismatch for {path}: "
                f"recorded={recorded_sha} actual={actual}",
            )
    return errors


# ── Serialization ────────────────────────────────────────────────-

def meta_proposal_to_dict(m: DreamMetaProposal) -> dict:
    return {
        "schema_version": m.schema_version,
        "continuity_anchor": {
            "branch_name": m.branch_name,
            "base_commit_hash": m.base_commit_hash,
            "pinned_input_manifest_sha256": m.pinned_input_manifest_sha256,
            "replay_manifest_sha256": m.replay_manifest_sha256,
        },
        "consumed_hook_contracts": list(m.consumed_hook_contracts),
        "source_tension_ids": list(m.source_tension_ids),
        "selected_proposal": {
            "path": m.selected_proposal_path,
            "proposal_id": m.selected_proposal_id,
            "solver_name": m.selected_proposal_solver_name,
            "cell_id": m.cell_id,
            "solver_hash": m.solver_hash,
        },
        "gate_provenance": dict(sorted(m.gate_provenance.items())),
        "collapse_results": {
            "collapse_verdict": m.collapse_verdict,
            "raw_verdict": m.raw_verdict,
        },
        "replay_metrics": dict(sorted(m.replay_metrics.items())),
        "structural_gains": dict(sorted(m.structural_gains.items())),
        "expected_value_of_merging": m.expected_value_of_merging,
        "confidence": m.confidence,
        "tension_severity_max": m.tension_severity_max,
        "uncertainty": m.uncertainty,
        "why_human_review_required": m.why_human_review_required,
        "why_runtime_flip_is_out_of_scope": m.why_runtime_flip_is_out_of_scope,
        "structurally_promising": m.structurally_promising,
    }


def render_meta_proposal_md(d: dict) -> str:
    lines = [
        "# Dream meta-proposal (shadow-only)",
        "",
        f"- **Schema version:** {d['schema_version']}",
        f"- **Branch:** `{d['continuity_anchor']['branch_name']}`",
        f"- **Base commit:** `{d['continuity_anchor']['base_commit_hash']}`",
        f"- **Pin manifest:** `{d['continuity_anchor']['pinned_input_manifest_sha256']}`",
        f"- **Tension IDs:** {list(d['source_tension_ids'])}",
        f"- **Selected proposal:** `{d['selected_proposal']['path']}`",
        f"- **Solver name:** `{d['selected_proposal']['solver_name']}`",
        f"- **Solver hash:** `{d['selected_proposal']['solver_hash']}`",
        f"- **Cell:** `{d['selected_proposal']['cell_id']}`",
        f"- **Collapse verdict:** **{d['collapse_results']['collapse_verdict']}** "
        f"(raw: {d['collapse_results']['raw_verdict']})",
        f"- **Confidence:** {d['confidence']}",
        f"- **Tension severity max:** {d['tension_severity_max']}",
        f"- **Expected value of merging:** {d['expected_value_of_merging']}",
        f"- **Uncertainty:** {d['uncertainty']}",
        f"- **Structurally promising:** **{d['structurally_promising']}**",
        "",
        "## Replay metrics",
        "",
    ]
    for k, v in d["replay_metrics"].items():
        lines.append(f"- `{k}`: {v}")
    lines.extend([
        "",
        "## Structural gains",
        "",
    ])
    for k, v in d["structural_gains"].items():
        lines.append(f"- `{k}`: {v}")
    lines.extend([
        "",
        "## Why human review is required",
        "",
        f"> {d['why_human_review_required']}",
        "",
        "## Why runtime flip is out of scope",
        "",
        f"> {d['why_runtime_flip_is_out_of_scope']}",
        "",
        "## Consumed hook contracts",
        "",
    ])
    for entry in d["consumed_hook_contracts"]:
        lines.append(
            f"- `{entry.get('file')}` v{entry.get('version')} "
            f"({entry.get('file_sha256')})"
        )
    lines.append("")
    return "\n".join(lines)


def emit_meta_proposal(m: DreamMetaProposal, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    d = meta_proposal_to_dict(m)
    json_path = out_dir / "dream_meta_proposal.json"
    md_path = out_dir / "dream_meta_proposal.md"
    json_path.write_text(
        json.dumps(d, indent=2, sort_keys=True), encoding="utf-8",
    )
    md_path.write_text(render_meta_proposal_md(d), encoding="utf-8")
    return {"meta_json": json_path, "meta_md": md_path}
