# SPDX-License-Identifier: BUSL-1.1
"""Offline replay engine — Phase 9 §J.

Replays a directory of cached ConsultationRecord JSONL entries to
exercise downstream distillation without making live provider calls.
This is the resilience guarantee: WD remains operational even if all
external providers disappear.
"""
from __future__ import annotations

import json
from pathlib import Path

from .api_consultant import ConsultationRecord


def load_cache(path: Path | str) -> list[ConsultationRecord]:
    p = Path(path)
    if not p.exists():
        return []
    out: list[ConsultationRecord] = []
    text = p.read_text(encoding="utf-8") if p.is_file() else ""
    if p.is_dir():
        for f in sorted(p.rglob("*.jsonl")):
            text = f.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rec = _from_dict(d)
                if rec is not None:
                    out.append(rec)
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            rec = _from_dict(d)
            if rec is not None:
                out.append(rec)
    return out


def _from_dict(d: dict) -> ConsultationRecord | None:
    try:
        return ConsultationRecord(
            schema_version=int(d.get("schema_version") or 1),
            consultation_id=str(d["consultation_id"]),
            request_id=str(d["request_id"]),
            response_id=str(d["response_id"]),
            trust_layer_reached=str(d["trust_layer_reached"]),
            extracted_facts=tuple(d.get("extracted_facts") or ()),
            extracted_solver_specs=tuple(
                d.get("extracted_solver_specs") or ()
            ),
            extracted_lessons=tuple(d.get("extracted_lessons") or ()),
            ts_iso=str(d.get("ts_iso") or ""),
        )
    except (KeyError, ValueError):
        return None


def replay_summary(records: list[ConsultationRecord]) -> dict:
    """Aggregated stats over a cache replay."""
    counts_by_layer: dict[str, int] = {}
    facts_total = 0
    solvers_total = 0
    lessons_total = 0
    for r in records:
        counts_by_layer[r.trust_layer_reached] = (
            counts_by_layer.get(r.trust_layer_reached, 0) + 1
        )
        facts_total += len(r.extracted_facts)
        solvers_total += len(r.extracted_solver_specs)
        lessons_total += len(r.extracted_lessons)
    return {
        "records_total": len(records),
        "counts_by_trust_layer": dict(sorted(counts_by_layer.items())),
        "facts_total": facts_total,
        "solver_specs_total": solvers_total,
        "lessons_total": lessons_total,
    }
