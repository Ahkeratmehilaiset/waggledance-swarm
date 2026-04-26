"""Meta-learner — Phase 8.5 Session D, deliverable D2.

Produces bounded, auditable self-proposals for WD by aggregating
evidence from up to four planes (curiosity, self_model, dream,
resilience) under a deterministic join key.

CRITICAL SCOPE RULE (D.txt §D2):
This module may PROPOSE change. It may NEVER ENACT change.
- no axiom YAML write
- no runtime registration
- no automatic merge / apply
- no code generation / self-rewrite

Every proposal is structurally promising only insofar as a future
human reviewer chooses to act on it. Session D is a recommender,
not an actor.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from . import (
    CROSS_PLANE_SUPPORT_FACTOR_CAP,
    EVIDENCE_PLANES,
    LIFECYCLE_STATUSES,
    META_SCHEMA_VERSION,
    PRIMARY_PLANES,
    PROPOSAL_TYPES,
    RESOLUTION_REASONS,
    SCOPE_CLASSES,
)


# ── Data classes ──────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceItem:
    """One piece of evidence from one plane, keyed by a stable join
    key (canonical_target)."""
    plane: str                        # one of EVIDENCE_PLANES
    canonical_target: str
    source_id: str                    # e.g. tension_id, curiosity_id
    cell_id: str | None
    severity: float                   # in [0, 1]
    rationale: str


@dataclass(frozen=True)
class MetaProposal:
    schema_version: int
    meta_proposal_id: str
    proposal_type: str
    scope_class: str
    impacted_cells: tuple[str, ...]
    evidence_planes: tuple[str, ...]
    evidence_strength: float
    expected_value: float
    confidence: float
    proposal_priority: float
    cross_plane_support_factor: float
    urgency_factor: float
    uncertainty: str                  # low | medium | high
    risk: str                         # low | medium | high
    why_now: str
    why_human_review_required: str
    no_mutation_in_session: bool
    source_curiosity_ids: tuple[str, ...]
    source_tension_ids: tuple[str, ...]
    source_dream_meta_proposal_ids: tuple[str, ...]
    source_resilience_refs: tuple[str, ...]
    canonical_target: str
    provenance: dict
    lifecycle_status: str
    resolution_reason: str


# ── Severity / claim parsing helpers ─────────────────────────────-

_SEVERITY_MAP = {"low": 0.33, "medium": 0.66, "high": 1.0}


def _normalize_severity(value: Any) -> float:
    if isinstance(value, str):
        return _SEVERITY_MAP.get(value.lower(), 0.0)
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def _cell_from_evidence_refs(refs: Iterable[Any]) -> str | None:
    for r in refs or ():
        if isinstance(r, str) and r.startswith("cell:"):
            return r.split(":", 1)[1]
    return None


# ── Evidence plane builders ───────────────────────────────────────

def gather_curiosity_evidence(curiosity_summary: dict | None,
                                 curiosity_log: list[dict]) -> list[EvidenceItem]:
    """Curiosity plane: each curiosity row keyed by candidate_cell."""
    items: list[EvidenceItem] = []
    for cur in curiosity_log:
        cell = cur.get("candidate_cell") or "_unattributed"
        target = cell
        ev_strength = float(cur.get("estimated_value") or 0.0) / 10.0
        items.append(EvidenceItem(
            plane="curiosity",
            canonical_target=target,
            source_id=cur.get("curiosity_id", ""),
            cell_id=cell,
            severity=min(1.0, ev_strength),
            rationale=f"curiosity {cur.get('suspected_gap_type')}",
        ))
    return items


def gather_self_model_evidence(self_model: dict | None,
                                  calibration_corrections: list[dict]) -> list[EvidenceItem]:
    """Self-model plane: tensions + blind spots, keyed by cell or
    tension_id."""
    items: list[EvidenceItem] = []
    if not self_model:
        return items
    for t in self_model.get("workspace_tensions") or []:
        cell = _cell_from_evidence_refs(t.get("evidence_refs") or [])
        target = cell or t.get("tension_id", "")
        sev = _normalize_severity(t.get("severity"))
        items.append(EvidenceItem(
            plane="self_model",
            canonical_target=target,
            source_id=t.get("tension_id", ""),
            cell_id=cell,
            severity=sev,
            rationale=f"tension {t.get('type')}",
        ))
    for bs in self_model.get("blind_spots") or []:
        sev = _normalize_severity(bs.get("severity"))
        domain = bs.get("domain", "")
        items.append(EvidenceItem(
            plane="self_model",
            canonical_target=domain,
            source_id=f"blind_spot:{domain}",
            cell_id=domain,
            severity=sev,
            rationale=f"blind_spot {domain}",
        ))
    return items


def gather_dream_evidence(dream_meta_proposals: list[dict]) -> list[EvidenceItem]:
    """Dream plane: each accepted meta-proposal contributes one
    evidence item keyed by cell_id."""
    items: list[EvidenceItem] = []
    for mp in dream_meta_proposals:
        if not mp.get("structurally_promising"):
            continue
        sel = mp.get("selected_proposal") or {}
        cell = sel.get("cell_id") or "_unattributed"
        # Severity proxy: confidence from the dream meta-proposal
        sev = float(mp.get("confidence") or 0.0)
        for tid in mp.get("source_tension_ids") or []:
            items.append(EvidenceItem(
                plane="dream",
                canonical_target=cell,
                source_id=tid,
                cell_id=cell,
                severity=sev,
                rationale=(
                    f"dream meta-proposal {sel.get('proposal_id')} "
                    f"with confidence={sev:.2f}"
                ),
            ))
        if not (mp.get("source_tension_ids") or []):
            items.append(EvidenceItem(
                plane="dream",
                canonical_target=cell,
                source_id=sel.get("proposal_id", ""),
                cell_id=cell,
                severity=sev,
                rationale=(
                    f"dream meta-proposal {sel.get('proposal_id')} "
                    "without source_tension_ids"
                ),
            ))
    return items


def gather_resilience_evidence(resilience_doc: str | None) -> list[EvidenceItem]:
    """R7.5 plane (optional). Each best_effort row in the resilience
    doc becomes one evidence item keyed by 'infra:<short>'.
    Missing R7.5 doc → empty list (and must not penalize)."""
    if not resilience_doc:
        return []
    items: list[EvidenceItem] = []
    # Look for table rows containing 'best_effort'
    for line in resilience_doc.splitlines():
        if "best_effort" not in line:
            continue
        # Extract the failure point label: first table cell after #
        m = re.match(r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|", line)
        if m:
            label = m.group(2).strip().lower().replace(" ", "_")[:40]
            target = f"infra:{label}"
        else:
            target = "infra:resilience_best_effort"
        items.append(EvidenceItem(
            plane="resilience",
            canonical_target=target,
            source_id=target,
            cell_id=None,
            severity=0.5,
            rationale="R7.5 best_effort boundary",
        ))
    return items


# ── Aggregation by canonical_target ──────────────────────────────-

def aggregate_by_target(items: list[EvidenceItem]) -> dict[str, list[EvidenceItem]]:
    out: dict[str, list[EvidenceItem]] = {}
    for it in items:
        out.setdefault(it.canonical_target, []).append(it)
    # Stable per-target ordering
    for k in out:
        out[k].sort(key=lambda x: (x.plane, x.source_id))
    return dict(sorted(out.items()))


# ── Formulas (D.txt §D5) ─────────────────────────────────────────-

def cross_plane_support_factor(num_supporting_planes: int) -> float:
    if num_supporting_planes <= 0:
        return 1.0
    raw = 1.0 + 0.25 * (num_supporting_planes - 1)
    return min(raw, CROSS_PLANE_SUPPORT_FACTOR_CAP)


def urgency_factor(proposal_type: str,
                     under_pressure_persistent: bool = False,
                     calibration_oscillation_active: bool = False,
                     r7_5_blocks_safe_scaling: bool = False) -> float:
    if proposal_type == "infrastructure_followup" and r7_5_blocks_safe_scaling:
        return 1.2
    if proposal_type == "introspection_gap" and calibration_oscillation_active:
        return 1.15
    if proposal_type == "topology_subdivision" and under_pressure_persistent:
        return 1.1
    return 1.0


def confidence_score(*, primary_plane_supports: bool,
                          second_plane_supports: bool,
                          dream_replay_positive_gain: bool,
                          no_major_contradiction: bool) -> float:
    """D.txt §D5 default formula."""
    c = 0.0
    if primary_plane_supports:
        c += 0.40
    if second_plane_supports:
        c += 0.20
    if dream_replay_positive_gain:
        c += 0.20
    if no_major_contradiction:
        c += 0.20
    return max(0.0, min(1.0, round(c, 6)))


def proposal_priority_score(*, expected_value: float, confidence: float,
                                  cross_plane_factor: float,
                                  urgency: float) -> float:
    """D.txt §D5 formula. Intentionally unclamped (ranking score)."""
    return round(expected_value * confidence * cross_plane_factor * urgency, 6)


# ── Proposal-type inference (D.txt §D4) ──────────────────────────-

def infer_proposal_type(planes_present: set[str],
                          severity_max: float,
                          calibration_oscillation: bool,
                          dream_subdivision_hint: bool,
                          r7_5_blocks: bool) -> str | None:
    """Decide which proposal_type the aggregated evidence supports.
    Returns None if evidence is too weak / single-plane non-allowed."""
    primary_count = len(planes_present & set(PRIMARY_PLANES))
    if dream_subdivision_hint and primary_count >= 2:
        return "topology_subdivision"
    if "dream" in planes_present and (
        "curiosity" in planes_present or "self_model" in planes_present
    ):
        return "solver_family_growth"
    if calibration_oscillation:
        return "policy_gate_adjustment"
    if "self_model" in planes_present and severity_max >= 0.66:
        return "introspection_gap"
    if "resilience" in planes_present and r7_5_blocks:
        return "infrastructure_followup"
    return None


def scope_class_for_type(proposal_type: str) -> str:
    return {
        "topology_subdivision": "topology",
        "solver_family_growth": "solver_library",
        "solver_family_consolidation": "solver_library",
        "policy_gate_adjustment": "policy",
        "introspection_gap": "introspection",
        "archival_cleanup": "archival",
        "infrastructure_followup": "infrastructure",
    }.get(proposal_type, "review_only")


# ── Identity (D.txt §D6) ─────────────────────────────────────────-

def compute_meta_proposal_id(proposal_type: str, scope_class: str,
                                  impacted_cells: Iterable[str],
                                  canonical_target: str) -> str:
    """Deterministic, structural — no volatile evidence refs."""
    cells = sorted({c for c in impacted_cells if c})
    canonical = json.dumps({
        "proposal_type": proposal_type,
        "scope_class": scope_class,
        "impacted_cells": cells,
        "canonical_target": canonical_target,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Lifecycle (D.txt §D6) ────────────────────────────────────────-

def lifecycle_for(meta_proposal_id: str,
                    history_ids_seen: set[str],
                    history_ids_in_immediate_prev: set[str]) -> str:
    if meta_proposal_id in history_ids_in_immediate_prev:
        return "persisting"
    if meta_proposal_id in history_ids_seen:
        return "persisting"
    return "new"


def resolved_proposals(prev_run_ids: set[str],
                          current_run_ids: set[str]) -> list[str]:
    """IDs that appeared previously but not in the current run."""
    return sorted(prev_run_ids - current_run_ids)


# ── Synthesis ────────────────────────────────────────────────────-

def _planes_for_target(items: list[EvidenceItem]) -> set[str]:
    return {it.plane for it in items}


def _evidence_strength(items: list[EvidenceItem]) -> float:
    if not items:
        return 0.0
    avg = sum(it.severity for it in items) / len(items)
    return round(min(1.0, avg), 6)


def _impacted_cells_for(items: list[EvidenceItem]) -> tuple[str, ...]:
    return tuple(sorted({it.cell_id for it in items if it.cell_id}))


def _source_id_collect(items: list[EvidenceItem], plane: str,
                          prefix: str | None = None) -> tuple[str, ...]:
    out: list[str] = []
    for it in items:
        if it.plane != plane:
            continue
        sid = it.source_id
        if prefix and not sid.startswith(prefix):
            continue
        out.append(sid)
    return tuple(sorted(set(out)))


@dataclass(frozen=True)
class SynthesisResult:
    proposals: tuple[MetaProposal, ...]
    insufficient_evidence: tuple[dict, ...]
    rejected_candidates: tuple[dict, ...]


def synthesize_proposals(
    *,
    items: list[EvidenceItem],
    self_model: dict | None,
    dream_meta_proposals: list[dict],
    resilience_doc: str | None,
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    consumed_hook_contracts: list[dict],
    fixture_fallback_used: bool = False,
    history_ids_seen: set[str] | None = None,
    history_ids_in_immediate_prev: set[str] | None = None,
    min_evidence: float = 0.10,
) -> SynthesisResult:
    """Aggregate items by canonical_target, run the trigger rules,
    emit MetaProposal records or downgrade to insufficient_evidence."""
    history_ids_seen = history_ids_seen or set()
    history_ids_in_immediate_prev = history_ids_in_immediate_prev or set()

    aggregated = aggregate_by_target(items)
    proposals: list[MetaProposal] = []
    insufficient: list[dict] = []
    rejected: list[dict] = []

    # Compute global flags from upstream artifacts
    calibration_oscillation = any(
        t.get("type") == "calibration_oscillation"
        for t in (self_model or {}).get("workspace_tensions") or []
    )
    dream_subdivision_hint = any(
        (mp.get("selected_proposal") or {})
            .get("solver_name", "").endswith("_subdivision")
        or "subdivision" in (mp.get("structural_gains") or {})
        for mp in dream_meta_proposals
    )
    r7_5_blocks = bool(resilience_doc and "best_effort" in resilience_doc)
    under_pressure_persistent = any(
        t.get("lifecycle_status") == "persisting"
        for t in (self_model or {}).get("workspace_tensions") or []
    )

    for target, target_items in aggregated.items():
        planes = _planes_for_target(target_items)
        sev_max = max((it.severity for it in target_items), default=0.0)
        ev_strength = _evidence_strength(target_items)

        proposal_type = infer_proposal_type(
            planes_present=planes, severity_max=sev_max,
            calibration_oscillation=calibration_oscillation,
            dream_subdivision_hint=dream_subdivision_hint,
            r7_5_blocks=r7_5_blocks,
        )

        # Multi-plane requirement check (D.txt §D4)
        if proposal_type is None:
            if ev_strength < min_evidence:
                rejected.append({
                    "candidate_target": target,
                    "rejection_reason": (
                        f"evidence_strength {ev_strength:.3f} < min_evidence "
                        f"{min_evidence:.3f}"
                    ),
                })
            else:
                insufficient.append({
                    "candidate_target": target,
                    "missing_planes": sorted(set(EVIDENCE_PLANES) - planes),
                    "evidence_strength_seen": ev_strength,
                    "why_below_threshold": (
                        "no proposal_type rule fires for the planes present"
                    ),
                })
            continue

        # Allow single-plane only for explicitly-permitted types
        single_plane_allowed = proposal_type in (
            "introspection_gap", "infrastructure_followup",
        )
        if len(planes) < 2 and not single_plane_allowed:
            insufficient.append({
                "candidate_target": target,
                "missing_planes": sorted(set(EVIDENCE_PLANES) - planes),
                "evidence_strength_seen": ev_strength,
                "why_below_threshold": (
                    "single-plane evidence is only allowed for "
                    "introspection_gap or infrastructure_followup"
                ),
            })
            continue

        # Build the proposal
        scope = scope_class_for_type(proposal_type)
        impacted = _impacted_cells_for(target_items)
        cps_factor = cross_plane_support_factor(len(planes))
        urg = urgency_factor(
            proposal_type=proposal_type,
            under_pressure_persistent=under_pressure_persistent,
            calibration_oscillation_active=calibration_oscillation,
            r7_5_blocks_safe_scaling=r7_5_blocks,
        )
        primary_supports = bool(planes & set(PRIMARY_PLANES))
        second_supports = len(planes & set(PRIMARY_PLANES)) >= 2
        # dream replay positive gain proxy: any dream meta-proposal
        # touching this target had structural_gain_count > 0
        dream_pos = any(
            (mp.get("structurally_promising")
             and (mp.get("replay_metrics") or {}).get("structural_gain_count", 0) > 0
             and (mp.get("selected_proposal") or {}).get("cell_id") in impacted)
            for mp in dream_meta_proposals
        )
        # contradiction proxy: a tension flagged contradictory
        no_contradiction = not any(
            t.get("type") == "evidence_contradicts_score"
            for t in (self_model or {}).get("workspace_tensions") or []
        )
        conf = confidence_score(
            primary_plane_supports=primary_supports,
            second_plane_supports=second_supports,
            dream_replay_positive_gain=dream_pos,
            no_major_contradiction=no_contradiction,
        )
        ev = ev_strength
        priority = proposal_priority_score(
            expected_value=ev, confidence=conf,
            cross_plane_factor=cps_factor, urgency=urg,
        )
        mid = compute_meta_proposal_id(
            proposal_type=proposal_type, scope_class=scope,
            impacted_cells=impacted, canonical_target=target,
        )
        lifecycle = lifecycle_for(
            mid, history_ids_seen=history_ids_seen,
            history_ids_in_immediate_prev=history_ids_in_immediate_prev,
        )

        proposals.append(MetaProposal(
            schema_version=META_SCHEMA_VERSION,
            meta_proposal_id=mid,
            proposal_type=proposal_type,
            scope_class=scope,
            impacted_cells=impacted,
            evidence_planes=tuple(sorted(planes)),
            evidence_strength=ev_strength,
            expected_value=ev,
            confidence=conf,
            proposal_priority=priority,
            cross_plane_support_factor=cps_factor,
            urgency_factor=urg,
            uncertainty=("high" if conf < 0.4 else
                          "medium" if conf < 0.7 else "low"),
            risk=("high" if proposal_type == "topology_subdivision" else
                   "medium" if proposal_type in (
                       "policy_gate_adjustment",
                       "solver_family_consolidation") else "low"),
            why_now=(
                f"{len(planes)} evidence planes converged on target "
                f"'{target}' with severity_max={sev_max:.2f} "
                f"(cps_factor={cps_factor:.2f}, urgency={urg:.2f})"
            ),
            why_human_review_required=(
                "Session D code never enacts a proposal. The selected "
                "evidence is structurally suggestive but the merge / "
                "apply decision must be made by a human reviewer with "
                "full context (downstream effects, schedule, risk)."
            ),
            no_mutation_in_session=True,
            source_curiosity_ids=_source_id_collect(target_items, "curiosity"),
            source_tension_ids=_source_id_collect(target_items, "self_model"),
            source_dream_meta_proposal_ids=_source_id_collect(target_items, "dream"),
            source_resilience_refs=_source_id_collect(target_items, "resilience"),
            canonical_target=target,
            provenance={
                "branch_name": branch_name,
                "base_commit_hash": base_commit_hash,
                "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
                "consumed_hook_contracts": list(consumed_hook_contracts),
                "fixture_fallback_used": fixture_fallback_used,
            },
            lifecycle_status=lifecycle,
            resolution_reason="n/a",
        ))

    # Stable ordering: by -priority, then meta_proposal_id
    proposals.sort(key=lambda p: (-p.proposal_priority, p.meta_proposal_id))
    insufficient.sort(key=lambda x: x["candidate_target"])
    rejected.sort(key=lambda x: x["candidate_target"])
    return SynthesisResult(
        proposals=tuple(proposals),
        insufficient_evidence=tuple(insufficient),
        rejected_candidates=tuple(rejected),
    )


# ── Serialization ────────────────────────────────────────────────-

def proposal_to_dict(p: MetaProposal) -> dict:
    d = asdict(p)
    # Tuples → lists
    for k in ("impacted_cells", "evidence_planes",
               "source_curiosity_ids", "source_tension_ids",
               "source_dream_meta_proposal_ids", "source_resilience_refs"):
        d[k] = list(d[k])
    return d
