# SPDX-License-Identifier: BUSL-1.1
"""External evidence collector — Phase 9 §I.

Reads pinned upstream artifacts (curiosity log, dream replay reports,
mentor packs) and extracts EXTERNAL evidence — facts about the world,
not WD itself. Strictly not self_model: tensions / blind_spots /
calibration corrections from Session B are about WD's INTERNAL state
and are filtered out.
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from .world_model_snapshot import ExternalFact


def _fact_id(*, claim: str, source_kind: str) -> str:
    canonical = f"{source_kind}|{claim}".encode("utf-8")
    return "fact_" + hashlib.sha256(canonical).hexdigest()[:10]


def from_curiosity_log(rows: Iterable[dict]) -> list[ExternalFact]:
    """Each curiosity row implies an EXTERNAL observation about query
    distribution. The CURIOSITY itself is internal (self), but the
    pattern it observes (e.g. 'thermal cell answers low-confidence on
    cold-day queries') is external."""
    out: list[ExternalFact] = []
    for r in rows:
        cell = r.get("candidate_cell") or "_unattributed"
        gap = r.get("suspected_gap_type", "unknown")
        ev_value = float(r.get("estimated_value") or 0.0)
        # Confidence proxy: ev_value normalized
        conf = max(0.0, min(1.0, ev_value / 10.0))
        claim = (
            f"observed pattern: cell={cell}, gap_kind={gap}, "
            f"estimated_value={ev_value:.2f}"
        )
        out.append(ExternalFact(
            fact_id=_fact_id(claim=claim, source_kind="curiosity_log"),
            kind="observation", claim=claim, confidence=conf,
            source_refs=(r.get("curiosity_id", ""),),
        ))
    return out


def from_dream_replay_report(report: dict) -> list[ExternalFact]:
    """Replay reports describe EXTERNAL outcomes: how many cases
    showed structural gain under shadow vs live."""
    out: list[ExternalFact] = []
    rc = int(report.get("replay_case_count") or 0)
    if rc <= 0:
        return out
    gain = int(report.get("structural_gain_count") or 0)
    delta = float(report.get("estimated_fallback_delta") or 0.0)
    claim = (
        f"replay observed structural_gain on {gain}/{rc} cases; "
        f"estimated_fallback_delta={delta:.3f}"
    )
    out.append(ExternalFact(
        fact_id=_fact_id(claim=claim, source_kind="shadow_replay_report"),
        kind="report", claim=claim,
        confidence=min(1.0, gain / max(rc, 1)),
        source_refs=tuple(report.get("tension_ids_targeted") or ()),
    ))
    return out


def from_mentor_context_pack(pack: dict) -> list[ExternalFact]:
    """Mentor design notes describe EXTERNAL design constraints /
    patterns. They are reports about the world of software design,
    not statements about WD itself."""
    out: list[ExternalFact] = []
    for item in pack.get("items") or []:
        kind = str(item.get("kind", ""))
        # Exclude self-referential items (anti-pattern: items about WD's
        # own internals would belong to self_model, not world_model)
        if kind in ("anti_pattern", "open_question"):
            continue
        content = str(item.get("content", ""))
        if not content:
            continue
        out.append(ExternalFact(
            fact_id=_fact_id(claim=content[:200],
                              source_kind="mentor_context_pack"),
            kind="report", claim=content[:500],
            confidence=0.6,
            source_refs=(str(item.get("item_id", "")),),
        ))
    return out
