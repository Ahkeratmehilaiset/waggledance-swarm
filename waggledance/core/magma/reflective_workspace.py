"""Reflective workspace — integrates curiosity findings with the
self-model snapshot to surface tensions, blind spots, and the next
question worth asking.

Per B.txt §B5:
- integrate_curiosity(self_model, curiosity_items) → unified state
- Workspace.load(snapshot, curiosity, previous=None)
- Workspace.attention_priorities()
- Workspace.tensions()
- Workspace.next_question()

All deterministic. Tensions are mechanically calculated, not
inferred from natural language. tension_id is structural (sha256 of
type + claim_canonical + observation_canonical), so evidence
updates do NOT change identity.

Crown-jewel area per B.txt §BUSL: any non-trivial logic edit here
requires the LICENSE-BUSL.txt Change Date update to 2030-03-19 in
the same commit.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import self_model as sm


def default_expected_domains(cells: list[str]) -> list[dict]:
    """Spec §B4 fallback when expected_domains.yaml does not exist.
    Derives one domain per cell with the cell's manifest as the
    expected capability signal."""
    out = []
    for cell in sorted(cells):
        out.append({
            "domain_id": cell,
            "description": f"Capability region attached to the {cell} hex cell.",
            "expected_capability_signals": [
                f"docs/cells/{cell}/manifest.json",
                f"configs/axioms/cottage/*.yaml",
            ],
            "related_cells": [cell],
        })
    return out


# ── Blind spot detector taxonomy ──────────────────────────────────

# Per B.txt §B4: detector names that may flag a domain.
BLIND_SPOT_DETECTORS = ("coverage_negative_space", "curiosity_silence")


def _severity_label(num_detectors: int, has_structural_evidence: bool) -> str:
    """Per B.txt §B4 severity matrix."""
    if num_detectors >= 2:
        return "high"
    if num_detectors == 1 and has_structural_evidence:
        return "medium"
    return "low"


# ── Helpers ───────────────────────────────────────────────────────

def _canonicalize(s: str) -> str:
    """Normalize a free-text claim/observation for stable tension IDs.
    Lowercased, whitespace-collapsed, trimmed."""
    return " ".join(s.lower().split())


def _tension_id(t_type: str, claim: str, observation: str) -> str:
    """Per B.txt §B5: sha256(type + claim_canonical + observation_canonical)[:12]."""
    blob = "|".join([t_type, _canonicalize(claim), _canonicalize(observation)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


# ── Detectors ─────────────────────────────────────────────────────

def detect_coverage_negative_space(
    expected_domains: list[dict],
    cell_states: dict[str, str],
    artifact_signals: dict[str, bool],
) -> list[str]:
    """Return the list of expected domain_ids that have ZERO artifact
    evidence. `artifact_signals[domain_id]` is True iff some artifact
    indicates the domain is populated."""
    flagged: list[str] = []
    for entry in expected_domains:
        domain_id = entry.get("domain_id")
        if not domain_id:
            continue
        if not artifact_signals.get(domain_id, False):
            flagged.append(domain_id)
    return sorted(flagged)


def detect_curiosity_silence(
    expected_domains: list[dict],
    curiosity_per_domain: dict[str, int],
    cell_strength: dict[str, str],
) -> list[str]:
    """Domains where curiosity is unexpectedly absent despite the cell
    showing structural presence (e.g. cell is 'strong' but curiosity
    items found ZERO clusters in it)."""
    flagged: list[str] = []
    for entry in expected_domains:
        domain_id = entry.get("domain_id")
        if not domain_id:
            continue
        # Map domain → cells (multi-cell domains supported)
        related_cells = entry.get("related_cells") or []
        n_curiosity = sum(
            curiosity_per_domain.get(c, 0) for c in related_cells
        ) if related_cells else curiosity_per_domain.get(domain_id, 0)
        any_strong = any(
            cell_strength.get(c) == "strong" for c in related_cells
        ) if related_cells else (cell_strength.get(domain_id) == "strong")
        if any_strong and n_curiosity == 0:
            flagged.append(domain_id)
    return sorted(flagged)


def build_blind_spots(
    expected_domains: list[dict],
    cell_states: dict[str, str],
    artifact_signals: dict[str, bool],
    curiosity_per_domain: dict[str, int],
) -> list[sm.BlindSpot]:
    """Combine the two detectors into a sorted list of BlindSpot
    instances."""
    flag_neg = set(detect_coverage_negative_space(
        expected_domains, cell_states, artifact_signals
    ))
    flag_silence = set(detect_curiosity_silence(
        expected_domains, curiosity_per_domain, cell_states
    ))
    out: list[sm.BlindSpot] = []
    for entry in expected_domains:
        domain_id = entry.get("domain_id")
        if not domain_id:
            continue
        detectors: list[str] = []
        if domain_id in flag_neg:
            detectors.append("coverage_negative_space")
        if domain_id in flag_silence:
            detectors.append("curiosity_silence")
        if not detectors:
            continue
        has_structural = artifact_signals.get(domain_id, False)
        sev = _severity_label(len(detectors), has_structural)
        out.append(sm.BlindSpot(
            domain=domain_id,
            severity=sev,
            detectors=tuple(sorted(detectors)),
            description=entry.get("description") or "",
            provenance={
                "detectors": sorted(detectors),
                "has_structural_evidence": has_structural,
                "expected_capability_signals": list(
                    entry.get("expected_capability_signals") or []
                ),
            },
        ))
    return sorted(out, key=lambda b: (-_sev_weight(b.severity), b.domain))


def _sev_weight(s: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(s, 0)


# ── Tension calculator ────────────────────────────────────────────

def detect_tensions(
    scorecard: list[sm.ScorecardDimension],
    cells: list[sm.CellClassification],
    previous_tensions: list[sm.WorkspaceTension] | None = None,
) -> list[sm.WorkspaceTension]:
    """Spec §B5 mechanical heuristic:
    if a dimension/cell claim implies strength, but evidence_implied
    score differs by >= 0.2, emit a tension."""
    tensions: list[sm.WorkspaceTension] = []
    prev_ids = {t.tension_id for t in (previous_tensions or [])}

    # Scorecard tensions
    for dim in scorecard:
        if dim.calibration_evidence is None:
            continue
        eis = dim.calibration_evidence.evidence_implied_score
        if eis is None:
            continue
        if abs(dim.score - eis) < sm.CALIBRATION_DRIFT_THRESHOLD:
            continue
        t_type = "scorecard_drift"
        claim = f"{dim.name} score = {dim.score}"
        observation = f"evidence implies {dim.name} = {eis}"
        tid = _tension_id(t_type, claim, observation)
        lifecycle = "persisting" if tid in prev_ids else "new"
        tensions.append(sm.WorkspaceTension(
            tension_id=tid,
            type=t_type,
            claim=claim,
            observation=observation,
            severity="medium" if abs(dim.score - eis) < 0.4 else "high",
            resolution_path="calibration_correction",
            lifecycle_status=lifecycle,
            evidence_refs=tuple(dim.calibration_evidence.evidence_refs),
        ))

    # Cell tensions: if a cell is classified strong but its data also
    # carries fallback_rate >= 0.3 (which would normally drive weak)
    for cell in cells:
        if cell.state != "strong":
            continue
        if cell.fallback_rate is not None and cell.fallback_rate >= 0.3:
            t_type = "cell_classification_drift"
            claim = f"cell {cell.cell_id} classified strong"
            observation = (f"fallback_rate {cell.fallback_rate:.2f} "
                            f">= 0.30 typically implies weak")
            tid = _tension_id(t_type, claim, observation)
            lifecycle = "persisting" if tid in prev_ids else "new"
            tensions.append(sm.WorkspaceTension(
                tension_id=tid,
                type=t_type,
                claim=claim,
                observation=observation,
                severity="medium",
                resolution_path="calibration_correction",
                lifecycle_status=lifecycle,
                evidence_refs=(f"cell:{cell.cell_id}",),
            ))

    return sorted(tensions, key=lambda t: t.tension_id)


def resolve_tensions_lifecycle(
    current: list[sm.WorkspaceTension],
    previous: list[sm.WorkspaceTension],
) -> tuple[list[sm.WorkspaceTension], list[str]]:
    """Mark current tensions as 'persisting' if their id was in the
    previous snapshot; new ones stay 'new'. Returns (lifecycle-tagged
    current list, list of resolved tension_ids that disappeared)."""
    prev_ids = {t.tension_id for t in previous}
    cur_ids = {t.tension_id for t in current}
    resolved = sorted(prev_ids - cur_ids)
    tagged: list[sm.WorkspaceTension] = []
    for t in current:
        new_status = "persisting" if t.tension_id in prev_ids else "new"
        if t.lifecycle_status == new_status:
            tagged.append(t)
        else:
            tagged.append(sm.WorkspaceTension(
                tension_id=t.tension_id,
                type=t.type,
                claim=t.claim,
                observation=t.observation,
                severity=t.severity,
                resolution_path=t.resolution_path,
                lifecycle_status=new_status,
                evidence_refs=t.evidence_refs,
            ))
    return tagged, resolved


# ── Public API ────────────────────────────────────────────────────

def integrate_curiosity(self_model: dict,
                          curiosity_items: list[dict]) -> dict:
    """Spec §B5 minimum API: produce a unified reflective state dict.

    The returned dict is intentionally simple: it surfaces the joint
    view that downstream consumers (Session C dream curriculum) read
    without having to walk the snapshot AND the curiosity log
    separately."""
    if not isinstance(curiosity_items, list):
        curiosity_items = list(curiosity_items)

    # Top attention by estimated_value (already sorted in curiosity log
    # but we resort defensively)
    sorted_items = sorted(
        curiosity_items,
        key=lambda c: (-(c.get("estimated_value") or 0.0),
                        c.get("curiosity_id", "")),
    )
    attention = sorted_items[:3]

    # Cell distribution
    by_cell: Counter = Counter()
    for c in curiosity_items:
        by_cell[c.get("candidate_cell") or "_unattributed"] += 1

    return {
        "schema_version": 1,
        "snapshot_id": self_model.get("snapshot_id"),
        "attention_focus_ids": [a.get("curiosity_id") for a in attention],
        "curiosity_count": len(curiosity_items),
        "curiosity_by_cell": dict(sorted(by_cell.items())),
        "tensions": list(self_model.get("workspace_tensions") or []),
        "blind_spots": list(self_model.get("blind_spots") or []),
        "scorecard_dimensions": [
            d.get("name") for d in (self_model.get("scorecard") or [])
        ],
    }


def next_question(
    tensions: list[sm.WorkspaceTension],
    blind_spots: list[sm.BlindSpot],
    meta_curiosity: sm.MetaCuriosity | None,
) -> str:
    """Spec §B5: derive next_question from tensions / blind spots,
    not from random templates."""
    # Highest-priority tension wins
    if tensions:
        tens_sorted = sorted(
            tensions,
            key=lambda t: (-_sev_weight(t.severity), t.tension_id),
        )
        t = tens_sorted[0]
        return (f"How does WD reconcile its claim that {t.claim} "
                f"with the observation that {t.observation}?")
    # Otherwise highest-severity blind spot
    if blind_spots:
        bs_sorted = sorted(
            blind_spots,
            key=lambda b: (-_sev_weight(b.severity), b.domain),
        )
        b = bs_sorted[0]
        return (f"What capability is WD missing in the '{b.domain}' "
                f"domain that the {', '.join(b.detectors)} detector "
                f"flagged?")
    # Otherwise meta_curiosity question
    if meta_curiosity is not None and meta_curiosity.question:
        return meta_curiosity.question
    return "What does WD not yet know about itself?"


@dataclass
class Workspace:
    """Minimal Workspace abstraction per spec §B5."""
    snapshot: dict
    curiosity_items: list[dict]
    previous_snapshot: dict | None = None

    @classmethod
    def load(cls,
             snapshot_path: Path | str,
             curiosity_path: Path | str | None = None,
             previous_snapshot_path: Path | str | None = None) -> "Workspace":
        snap = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
        items: list[dict] = []
        if curiosity_path:
            p = Path(curiosity_path)
            if p.exists():
                if p.suffix == ".jsonl":
                    for line in p.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            items.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                else:
                    items = json.loads(p.read_text(encoding="utf-8"))
        prev = None
        if previous_snapshot_path:
            pp = Path(previous_snapshot_path)
            if pp.exists():
                prev = json.loads(pp.read_text(encoding="utf-8"))
        return cls(snapshot=snap, curiosity_items=items,
                   previous_snapshot=prev)

    def attention_priorities(self) -> list[dict]:
        """Top 3 curiosity items by estimated_value."""
        sorted_items = sorted(
            self.curiosity_items,
            key=lambda c: (-(c.get("estimated_value") or 0.0),
                            c.get("curiosity_id", "")),
        )
        return sorted_items[:3]

    def tensions(self) -> list[dict]:
        """Surface tensions from the loaded snapshot."""
        return list(self.snapshot.get("workspace_tensions") or [])

    def next_question(self) -> str:
        # Reconstruct minimal types from dicts so the helper above works
        tens_dicts = list(self.snapshot.get("workspace_tensions") or [])
        bs_dicts = list(self.snapshot.get("blind_spots") or [])
        # Build typed objects from dicts
        tens_typed = [
            sm.WorkspaceTension(
                tension_id=t.get("tension_id", ""),
                type=t.get("type", ""),
                claim=t.get("claim", ""),
                observation=t.get("observation", ""),
                severity=t.get("severity", "low"),
                resolution_path=t.get("resolution_path", "requires_human_review"),
                lifecycle_status=t.get("lifecycle_status", "new"),
                evidence_refs=tuple(t.get("evidence_refs") or []),
            )
            for t in tens_dicts
        ]
        bs_typed = [
            sm.BlindSpot(
                domain=b.get("domain", ""),
                severity=b.get("severity", "low"),
                detectors=tuple(b.get("detectors") or []),
                description=b.get("description", ""),
                provenance=b.get("provenance") or {},
            )
            for b in bs_dicts
        ]
        meta_dict = self.snapshot.get("meta_curiosity") or {}
        meta = sm.MetaCuriosity(
            question=meta_dict.get("question", ""),
            derivation_strength=meta_dict.get("derivation_strength", "canonical"),
            source_refs=tuple(meta_dict.get("source_refs") or []),
        ) if meta_dict else None
        return next_question(tens_typed, bs_typed, meta)
