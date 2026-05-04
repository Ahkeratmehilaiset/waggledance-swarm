"""Dream curriculum planner — selects what WD should dream about.

Reads Session B's self_model_snapshot, self_model_delta, tensions,
calibration_corrections, and Session A curiosity outputs, then
emits a deterministic 7-night curriculum.

Priority formula (c.txt §C1):

    dream_priority =
        tension_severity
      × calibration_gap
      × recurrence_factor
      × expected_value
      × (1 + blind_spot_bonus + exploration_bonus)

with bootstrap exploration_bonus = 0 (no prior dream history).

Crown-jewel area per c.txt §BUSL.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import (
    DREAMING_SCHEMA_VERSION, DREAM_MODES, HISTORY_WINDOW,
    SEVERITY_TO_FLOAT,
)


# ── Data classes ──────────────────────────────────────────────────

@dataclass(frozen=True)
class DreamableItem:
    """One candidate item the curriculum considers — typically a
    tension, blind spot, or curiosity item with a deferred_to_dream
    resolution path."""
    source_id: str          # tension_id or curiosity_id or blind_spot domain
    source_kind: str        # "tension" | "blind_spot" | "curiosity" | "calibration_oscillation"
    candidate_cell: str | None
    severity: float         # in [0, 1]
    calibration_gap: float
    recurrence: int
    expected_value: float
    blind_spot_bonus: float
    exploration_bonus: float
    dream_priority: float
    suggested_mode: str     # one of DREAM_MODES
    evidence_refs: tuple[str, ...]
    rationale: str


@dataclass(frozen=True)
class DreamNight:
    """One night in the curriculum."""
    night_index: int        # 1..N
    target_items: tuple[DreamableItem, ...]
    primary_cells: tuple[str, ...]
    secondary_cells: tuple[str, ...]
    dream_objective: str
    supporting_evidence: tuple[str, ...]
    why_now: str
    uncertainty: str        # "low" | "medium" | "high"
    mode: str               # one of DREAM_MODES
    primary_source: str     # "deferred_to_dream" | "secondary_fallback"


@dataclass(frozen=True)
class DreamCurriculum:
    """A 7-night curriculum (default) plus its provenance."""
    schema_version: int
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    primary_source: str       # "deferred_to_dream" | "secondary_fallback"
    nights: tuple[DreamNight, ...]
    counts_by_mode: dict[str, int]
    counts_by_kind: dict[str, int]
    secondary_fallback_reason: str | None


# ── Severity normalization ────────────────────────────────────────

def normalize_severity(value: Any) -> float:
    """Map string severities ('low'/'medium'/'high') to floats; clamp
    numeric severities into [0, 1]."""
    if isinstance(value, str):
        return SEVERITY_TO_FLOAT.get(value.lower(), 0.0)
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.0


def blind_spot_bonus_for_severity(severity_label: str) -> float:
    """c.txt §C1: 0.25 for high, 0.10 for medium, 0.0 otherwise."""
    return {"high": 0.25, "medium": 0.10}.get(severity_label, 0.0)


# ── Calibration gap ───────────────────────────────────────────────

def load_calibration_corrections(corrections_path: Path | None) -> list[dict]:
    """Read Session B's calibration_corrections.jsonl. Returns [] if
    missing — caller decides whether to log a documented blocker."""
    if corrections_path is None or not corrections_path.exists():
        return []
    rows: list[dict] = []
    for line in corrections_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def calibration_gap_for_dimension(corrections: list[dict],
                                     dimension: str) -> float:
    """Return abs(prior - evidence_implied) for the most recent
    correction on `dimension`, or 0.0 if no correction exists."""
    matches = [c for c in corrections if c.get("dimension") == dimension]
    if not matches:
        return 0.0
    last = matches[-1]
    try:
        return abs(float(last["prior_score"]) - float(last["evidence_implied_score"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


# ── Priority formula ──────────────────────────────────────────────

def dream_priority(tension_severity: float, calibration_gap: float,
                     recurrence_count: int, expected_value: float,
                     blind_spot_bonus: float = 0.0,
                     exploration_bonus: float = 0.0) -> float:
    """c.txt §C1 default priority formula."""
    recurrence_factor = 1.0 + min(1.0, recurrence_count / 3.0)
    return round(
        tension_severity
        * calibration_gap
        * recurrence_factor
        * expected_value
        * (1.0 + blind_spot_bonus + exploration_bonus),
        6,
    )


def suggest_mode(item_kind: str, candidate_cell: str | None,
                   has_bridge: bool, has_subdivision: bool) -> str:
    """Heuristic mapping from item kind to a DREAM_MODE per c.txt §C1."""
    if has_subdivision:
        return "subdivision_pressure"
    if has_bridge:
        return "bridge_composition"
    if item_kind == "calibration_oscillation":
        return "introspection"
    if item_kind == "blind_spot":
        return "base_solver_growth"
    if item_kind == "curiosity":
        return "solver_refinement"
    if item_kind == "tension":
        return "introspection"
    return "wait"


# ── Item construction ─────────────────────────────────────────────

def build_dreamable_items(
    self_model: dict,
    curiosity_log: list[dict],
    calibration_corrections: list[dict],
    history_entries: list[dict] | None = None,
) -> list[DreamableItem]:
    """Build the candidate-item list from upstream artifacts. Sorted
    by `dream_priority` desc, with deterministic tie-break by
    source_id."""
    items: list[DreamableItem] = []

    # Tensions (priority 1)
    tensions = self_model.get("workspace_tensions") or []
    for t in tensions:
        sev = normalize_severity(t.get("severity"))
        cal_gap = 0.0
        if t.get("type") == "scorecard_drift":
            # The claim string starts with "<dimension> score = ..."
            m = re.match(r"^([a-z_]+)\s+score", t.get("claim", ""))
            if m:
                cal_gap = calibration_gap_for_dimension(
                    calibration_corrections, m.group(1)
                )
        recurrence = 0
        if t.get("lifecycle_status") == "persisting":
            recurrence = 1
        ev = sev   # tensions don't carry expected_value; use severity proxy
        bs_bonus = 0.0
        item_kind = "tension"
        if t.get("resolution_path") == "deferred_to_dream":
            item_kind = "tension"
        elif t.get("type") == "calibration_oscillation":
            item_kind = "calibration_oscillation"

        prio = dream_priority(
            tension_severity=sev,
            calibration_gap=max(cal_gap, 0.05),  # avoid zero-product collapse
            recurrence_count=recurrence,
            expected_value=ev,
            blind_spot_bonus=bs_bonus,
        )
        # Determine cell from evidence_refs if any
        candidate_cell = None
        for ref in t.get("evidence_refs") or []:
            if isinstance(ref, str) and ref.startswith("cell:"):
                candidate_cell = ref.split(":", 1)[1]
                break

        mode = suggest_mode(item_kind, candidate_cell, False, False)
        items.append(DreamableItem(
            source_id=t.get("tension_id", ""),
            source_kind=item_kind,
            candidate_cell=candidate_cell,
            severity=sev,
            calibration_gap=cal_gap,
            recurrence=recurrence,
            expected_value=ev,
            blind_spot_bonus=bs_bonus,
            exploration_bonus=0.0,
            dream_priority=prio,
            suggested_mode=mode,
            evidence_refs=tuple(t.get("evidence_refs") or []),
            rationale=f"tension {t.get('type')}: {t.get('claim', '')[:80]}",
        ))

    # Blind spots (priority 2)
    for bs in self_model.get("blind_spots") or []:
        sev_label = bs.get("severity", "low")
        sev = normalize_severity(sev_label)
        bs_bonus = blind_spot_bonus_for_severity(sev_label)
        ev = sev   # use severity as expected-value proxy
        item_kind = "blind_spot"
        mode = suggest_mode(item_kind, None, False, False)
        prio = dream_priority(
            tension_severity=sev,
            calibration_gap=0.05,
            recurrence_count=0,
            expected_value=ev,
            blind_spot_bonus=bs_bonus,
        )
        items.append(DreamableItem(
            source_id=bs.get("domain", ""),
            source_kind=item_kind,
            candidate_cell=bs.get("domain"),
            severity=sev,
            calibration_gap=0.0,
            recurrence=0,
            expected_value=ev,
            blind_spot_bonus=bs_bonus,
            exploration_bonus=0.0,
            dream_priority=prio,
            suggested_mode=mode,
            evidence_refs=tuple(bs.get("detectors") or []),
            rationale=f"blind_spot {bs.get('domain')} (sev={sev_label})",
        ))

    # Top curiosity items (priority 3)
    sorted_cur = sorted(
        curiosity_log,
        key=lambda c: (-(c.get("estimated_value") or 0.0),
                        c.get("curiosity_id", "")),
    )
    for cur in sorted_cur[:3]:
        ev = float(cur.get("estimated_value") or 0.0)
        if ev <= 0:
            continue
        sev = min(1.0, ev / 10.0)   # rough mapping
        cell = cur.get("candidate_cell")
        has_bridge = bool(cur.get("bridge_candidate_refs"))
        has_subdiv = bool(cur.get("subdivision_pressure_hint"))
        mode = suggest_mode("curiosity", cell, has_bridge, has_subdiv)
        prio = dream_priority(
            tension_severity=sev,
            calibration_gap=0.05,
            recurrence_count=int(cur.get("count") or 0) // 5,
            expected_value=ev,
            blind_spot_bonus=0.0,
        )
        items.append(DreamableItem(
            source_id=cur.get("curiosity_id", ""),
            source_kind="curiosity",
            candidate_cell=cell,
            severity=sev,
            calibration_gap=0.0,
            recurrence=int(cur.get("count") or 0) // 5,
            expected_value=ev,
            blind_spot_bonus=0.0,
            exploration_bonus=0.0,
            dream_priority=prio,
            suggested_mode=mode,
            evidence_refs=(cur.get("curiosity_id", ""),),
            rationale=f"curiosity {cur.get('suspected_gap_type')}",
        ))

    # Deterministic order: priority desc, then source_id
    items.sort(key=lambda it: (-it.dream_priority, it.source_id))
    return items


# ── Curriculum construction ─────────────────────────────────────-

def has_deferred_to_dream(self_model: dict) -> bool:
    for t in self_model.get("workspace_tensions") or []:
        if t.get("resolution_path") == "deferred_to_dream":
            return True
    return False


def build_curriculum(
    self_model: dict,
    curiosity_log: list[dict],
    calibration_corrections: list[dict],
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    top_nights: int = 7,
    history_entries: list[dict] | None = None,
) -> DreamCurriculum:
    items = build_dreamable_items(
        self_model, curiosity_log, calibration_corrections, history_entries,
    )

    deferred = has_deferred_to_dream(self_model)
    if deferred:
        primary_source = "deferred_to_dream"
        primary_items = [it for it in items
                          if it.source_kind in ("tension", "calibration_oscillation")]
        secondary_fallback_reason = None
    else:
        primary_source = "secondary_fallback"
        # Fall back per spec: high-severity blind spots,
        # calibration_oscillation tensions, unresolved_tension blind spots
        primary_items = [it for it in items
                          if it.source_kind in ("blind_spot",
                                                  "calibration_oscillation")]
        secondary_fallback_reason = (
            "No tensions with resolution_path == 'deferred_to_dream' were "
            "found in the pinned self-model snapshot. Curriculum fell back "
            "to high-severity blind spots and calibration_oscillation "
            "tensions per c.txt §C1 bootstrap fallback rule."
        )

    if not primary_items:
        primary_items = items[: max(1, top_nights)]

    nights: list[DreamNight] = []
    for night_index in range(1, top_nights + 1):
        # Each night targets the next item; "wait" if exhausted.
        if night_index <= len(primary_items):
            target = primary_items[night_index - 1]
            cells = (target.candidate_cell,) if target.candidate_cell else ()
            mode = target.suggested_mode
            night = DreamNight(
                night_index=night_index,
                target_items=(target,),
                primary_cells=cells,
                secondary_cells=(),
                dream_objective=(
                    f"Resolve {target.source_kind} '{target.source_id}' "
                    f"in cell {target.candidate_cell or '(unattributed)'}"
                ),
                supporting_evidence=target.evidence_refs,
                why_now=(
                    f"dream_priority={target.dream_priority:.4f}; "
                    f"severity={target.severity:.2f}; "
                    f"recurrence={target.recurrence}"
                ),
                uncertainty=(
                    "high" if target.dream_priority < 0.05 else
                    "medium" if target.dream_priority < 0.20 else
                    "low"
                ),
                mode=mode,
                primary_source=primary_source,
            )
        else:
            night = DreamNight(
                night_index=night_index,
                target_items=(),
                primary_cells=(),
                secondary_cells=(),
                dream_objective="No further dreamable items in this cycle.",
                supporting_evidence=(),
                why_now="curriculum exhausted",
                uncertainty="high",
                mode="wait",
                primary_source=primary_source,
            )
        nights.append(night)

    counts_by_mode: dict[str, int] = {}
    counts_by_kind: dict[str, int] = {}
    for n in nights:
        counts_by_mode[n.mode] = counts_by_mode.get(n.mode, 0) + 1
        for it in n.target_items:
            counts_by_kind[it.source_kind] = counts_by_kind.get(it.source_kind, 0) + 1

    return DreamCurriculum(
        schema_version=DREAMING_SCHEMA_VERSION,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        primary_source=primary_source,
        nights=tuple(nights),
        counts_by_mode=dict(sorted(counts_by_mode.items())),
        counts_by_kind=dict(sorted(counts_by_kind.items())),
        secondary_fallback_reason=secondary_fallback_reason,
    )


def curriculum_to_dict(c: DreamCurriculum) -> dict:
    """Stable, byte-identical-ready dict form."""
    return {
        "schema_version": c.schema_version,
        "continuity_anchor": {
            "branch_name": c.branch_name,
            "base_commit_hash": c.base_commit_hash,
            "pinned_input_manifest_sha256": c.pinned_input_manifest_sha256,
        },
        "primary_source": c.primary_source,
        "secondary_fallback_reason": c.secondary_fallback_reason,
        "counts_by_mode": dict(sorted(c.counts_by_mode.items())),
        "counts_by_kind": dict(sorted(c.counts_by_kind.items())),
        "nights": [
            {
                "night_index": n.night_index,
                "primary_source": n.primary_source,
                "target_items": [asdict(it) for it in n.target_items],
                "primary_cells": list(n.primary_cells),
                "secondary_cells": list(n.secondary_cells),
                "dream_objective": n.dream_objective,
                "supporting_evidence": list(n.supporting_evidence),
                "why_now": n.why_now,
                "uncertainty": n.uncertainty,
                "mode": n.mode,
            }
            for n in c.nights
        ],
    }


def render_curriculum_md(c_dict: dict) -> str:
    lines = [
        "# Dream curriculum",
        "",
        f"- **Schema version:** {c_dict['schema_version']}",
        f"- **Branch:** `{c_dict['continuity_anchor']['branch_name']}`",
        f"- **Base commit:** `{c_dict['continuity_anchor']['base_commit_hash']}`",
        f"- **Pin manifest:** `{c_dict['continuity_anchor']['pinned_input_manifest_sha256']}`",
        f"- **Primary source:** {c_dict['primary_source']}",
        f"- **Counts by mode:** {c_dict['counts_by_mode']}",
        f"- **Counts by kind:** {c_dict['counts_by_kind']}",
    ]
    if c_dict.get("secondary_fallback_reason"):
        lines.append("")
        lines.append("**Secondary fallback reason:**")
        lines.append(f"> {c_dict['secondary_fallback_reason']}")
    lines.extend(["", "## Nights", "",
                   "| # | mode | source_id | objective | uncertainty |",
                   "|---|---|---|---|---|"])
    for n in c_dict["nights"]:
        items = n.get("target_items") or []
        sid = items[0]["source_id"] if items else "—"
        lines.append(
            f"| {n['night_index']} | `{n['mode']}` | `{sid}` | "
            f"{n['dream_objective'][:80]} | {n['uncertainty']} |"
        )
    lines.append("")
    return "\n".join(lines)
