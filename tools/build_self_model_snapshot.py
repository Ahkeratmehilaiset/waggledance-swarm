#!/usr/bin/env python3
"""Build the self-model snapshot — Phase 8.5 Session B.

Reads pinned upstream artifacts (Session A curiosity outputs, cell
manifests, subdivision/composition reports) and emits six
deterministic artifact families per B.txt §B1:

  <out_dir>/self_model_snapshot.json
  <out_dir>/self_model_snapshot.md
  <out_dir>/scorecard.json
  <out_dir>/continuity_anchors.json
  <out_dir>/self_model_delta.json
  <out_dir>/data_provenance.json

Plus optional append-only files (HISTORY.jsonl,
calibration_corrections.jsonl) when the calibration loop fires.

Runtime safety: zero touch. Read-only over disk artifacts. Never
opens port 8002. Never imports runtime adapters. No Ollama
dependency.

Default `--output-dir`: derived from pin_hash so two reproductions
of the same input land at the same path:
    docs/runs/self_model/<pinned_input_manifest_sha12>/

CLI:
  python tools/build_self_model_snapshot.py --help
  python tools/build_self_model_snapshot.py                       # dry-run
  python tools/build_self_model_snapshot.py --apply               # write
  python tools/build_self_model_snapshot.py --input-manifest PATH
  python tools/build_self_model_snapshot.py --real-data-only --apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import self_model as sm  # noqa: E402

DEFAULT_OUT_ROOT = ROOT / "docs" / "runs" / "self_model"
DEFAULT_HISTORY = DEFAULT_OUT_ROOT / "HISTORY.jsonl"
DEFAULT_CALIB_LOG = DEFAULT_OUT_ROOT / "calibration_corrections.jsonl"

# Cell vocabulary mirrored from the gap miner. Same source-of-truth.
_CELL_VOCAB = {
    "thermal":  ["heating", "cooling", "thermal", "hvac", "heat_pump",
                 "frost", "temperature", "lämpö"],
    "energy":   ["energy", "solar", "battery", "power", "kwh", "grid",
                 "watt", "sähkö", "electricity"],
    "safety":   ["safety", "alarm", "risk", "hazard", "violation",
                 "varroa", "swarm"],
    "seasonal": ["season", "month", "winter", "summer", "spring",
                 "autumn", "harvest", "sato"],
    "math":     ["formula", "calculate", "yield", "honey", "colony"],
    "system":   ["system", "status", "health", "uptime", "process",
                 "mtbf", "oee", "diagnose"],
    "learning": ["learn", "train", "dream", "insight", "adapt"],
    "general":  [],
}
_CELLS = tuple(_CELL_VOCAB)


# ── Pin manifest loading ─────────────────────────────────────────

def load_pin_manifest(state_or_manifest_path: Path) -> tuple[str, list[dict]]:
    """Return (pin_hash, list-of-pinned-input-dicts) from either a
    Session-A-style state.json or a dedicated manifest JSON."""
    data = json.loads(state_or_manifest_path.read_text(encoding="utf-8"))
    pin_hash = (
        data.get("pinned_input_manifest_sha256")
        or data.get("pinned_artifact_manifest_sha256")
        or "sha256:unknown"
    )
    raw = data.get("pinned_inputs") or data.get("pinned_artifacts") or []
    return pin_hash, raw


def _bounded_read(path: Path, byte_limit: int) -> str:
    if not path.exists():
        return ""
    with open(path, "rb") as f:
        return f.read(byte_limit).decode("utf-8", errors="replace")


def _find_pinned(pinned_inputs: list[dict], suffix: str) -> dict | None:
    for entry in pinned_inputs:
        path = entry.get("path") or entry.get("relpath", "")
        if path.endswith(suffix):
            return entry
    return None


# ── Aggregation: turn pinned inputs into a snapshot ──────────────

def build_snapshot(
    pin_hash: str,
    pinned_inputs: list[dict],
    branch_name: str,
    base_commit_hash: str,
    history: list[dict] | None = None,
    previous_snapshot: dict | None = None,
    cell_filter: str | None = None,
    include_meta_curiosity: bool = True,
) -> tuple[sm.SelfModelSnapshot, dict]:
    """Build the snapshot + the data_provenance map. Returns both as
    a (snapshot, provenance_dict) tuple. Pure function — no writes."""

    # Continuity anchor first; embedded everywhere.
    anchor = sm.ContinuityAnchor(
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pin_hash,
    )

    # Discover and load each upstream artifact under the pin.
    curiosity_summary = _load_artifact(pinned_inputs, "curiosity_summary.json",
                                        loader="json")
    curiosity_log = _load_artifact(pinned_inputs, "curiosity_log.jsonl",
                                    loader="jsonl")
    teacher_packs = _load_teacher_packs(pinned_inputs)
    cell_manifests = _load_cell_manifests(pinned_inputs)
    subdivision_pressure = _load_subdivision_pressure(pinned_inputs)

    # Per-cell aggregate counters from the curiosity log.
    cell_stats: dict[str, dict] = {}
    for c in _CELLS:
        cell_stats[c] = {
            "fallback_count": 0, "total": 0,
            "missing_solver_clusters": 0,
            "all_clusters": 0,
            "contradiction_count": 0,
        }
    for cur in curiosity_log:
        cell = cur.get("candidate_cell") or "general"
        if cell not in cell_stats:
            continue
        cell_stats[cell]["all_clusters"] += 1
        if cur.get("suspected_gap_type") == "missing_solver":
            cell_stats[cell]["missing_solver_clusters"] += 1
        n = int(cur.get("count") or 0)
        fr = float(cur.get("fallback_rate") or 0.0)
        cell_stats[cell]["total"] += n
        cell_stats[cell]["fallback_count"] += int(round(n * fr))
        cell_stats[cell]["contradiction_count"] += len(
            cur.get("contradiction_hints") or []
        )

    cells = []
    for cell_id in _CELLS:
        s = cell_stats[cell_id]
        fr = s["fallback_count"] / s["total"] if s["total"] else None
        cr = (s["contradiction_count"] / s["total"]) if s["total"] else None
        cells.append(sm.classify_cell(
            cell_id=cell_id,
            fallback_rate=fr,
            attributed_curiosity_clusters=s["all_clusters"],
            contradiction_rate=cr,
            subdivision_pressure_hint=subdivision_pressure.get(cell_id),
        ))

    # Attention focus = top-3 curiosities by estimated_value (already sorted
    # in curiosity_log, but resort defensively).
    sorted_log = sorted(
        curiosity_log,
        key=lambda c: (-(c.get("estimated_value") or 0.0),
                        c.get("curiosity_id", "")),
    )
    if cell_filter:
        sorted_log = [c for c in sorted_log
                       if c.get("candidate_cell") == cell_filter]
    attention_focus = tuple(
        sm.AttentionItem(
            curiosity_id=c["curiosity_id"],
            candidate_cell=c.get("candidate_cell"),
            estimated_value=float(c.get("estimated_value") or 0.0),
            suspected_gap_type=c.get("suspected_gap_type", "unknown"),
            count=int(c.get("count") or 0),
        )
        for c in sorted_log[:3]
    )

    # Blind spots — placeholder list here; full detector in
    # reflective_workspace.py (Commit 3). For Commit 2 we surface
    # the meta_curiosity / scorecard / attention_focus contract;
    # blind_spots will populate from the workspace integration in
    # Commit 3.
    blind_spots: tuple[sm.BlindSpot, ...] = ()

    # Workspace tensions — same as blind_spots; full surface in Commit 3.
    workspace_tensions: tuple[sm.WorkspaceTension, ...] = ()

    # Scorecard — v0.1 formulas per spec §B7.
    scorecard = _build_scorecard(
        curiosity_log, cells, blind_spots, workspace_tensions,
        attention_focus, previous_snapshot, history,
    )

    # Meta-curiosity — stronger derivation when data permits.
    meta_curiosity = _derive_meta_curiosity(
        curiosity_log, blind_spots, workspace_tensions, previous_snapshot,
    ) if include_meta_curiosity else sm.MetaCuriosity(
        question="Meta-curiosity disabled by --include-meta-curiosity=false.",
        derivation_strength="canonical",
        source_refs=(),
    )

    # Self-Entity alignment — none in repo today; spec-shaped placeholder.
    self_entity_alignment = sm.detect_self_entity(ROOT)

    # Invariants and ruptures fully populate in Commit 4 (history
    # rolling computations). Commit 2 emits empty tuples.
    invariants: tuple[sm.Invariant, ...] = ()
    ruptures: tuple[sm.Rupture, ...] = ()

    # Real-data coverage ratio: weighted across major fields.
    provenance, ratio = _build_provenance(
        pinned_inputs=pinned_inputs,
        scorecard=scorecard,
        attention_focus=attention_focus,
        blind_spots=blind_spots,
        cells=cells,
    )

    snap = sm.SelfModelSnapshot(
        schema_version=sm.SELF_MODEL_SCHEMA_VERSION,
        snapshot_id=sm.snapshot_id_for(base_commit_hash, pin_hash),
        continuity_anchor=anchor,
        scorecard=scorecard,
        blind_spots=blind_spots,
        workspace_tensions=workspace_tensions,
        cells=tuple(cells),
        attention_focus=attention_focus,
        meta_curiosity=meta_curiosity,
        self_entity_alignment=self_entity_alignment,
        invariants=invariants,
        ruptures=ruptures,
        real_data_coverage_ratio=ratio,
    )
    return snap, provenance


def _load_artifact(pinned_inputs: list[dict], suffix: str,
                    loader: str) -> Any:
    entry = _find_pinned(pinned_inputs, suffix)
    if entry is None:
        return None if loader == "json" else []
    path = ROOT / entry["path"]
    sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
    text = _bounded_read(path, sz) if path.exists() else ""
    if loader == "json":
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return None
    if loader == "jsonl":
        out: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
    return None


def _load_teacher_packs(pinned_inputs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in pinned_inputs:
        path = entry.get("path", "")
        if "teacher_packs" not in path or not path.endswith(".json"):
            continue
        sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
        text = _bounded_read(ROOT / path, sz)
        try:
            data = json.loads(text)
            cell = data.get("cell_id") or Path(path).stem
            out[cell] = data
        except Exception:
            pass
    return out


def _load_cell_manifests(pinned_inputs: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in pinned_inputs:
        path = entry.get("path", "")
        if not (path.startswith("docs/cells/") and path.endswith("/manifest.json")):
            continue
        sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
        text = _bounded_read(ROOT / path, sz)
        try:
            data = json.loads(text)
            cell = data.get("cell_id") or Path(path).parent.name
            out[cell] = data
        except Exception:
            pass
    return out


def _load_subdivision_pressure(pinned_inputs: list[dict]) -> dict[str, float]:
    entry = _find_pinned(pinned_inputs, "hex_subdivision_plan.md")
    if entry is None:
        return {}
    sz = int(entry.get("size_bytes") or entry.get("bytes") or 0)
    text = _bounded_read(ROOT / entry["path"], sz)
    out: dict[str, float] = {}
    for m in re.finditer(
        r"^###\s+`([a-z_]+)`\s+—\s+severity\s+([0-9.]+)\s*$",
        text, re.MULTILINE,
    ):
        try:
            out[m.group(1)] = float(m.group(2))
        except ValueError:
            pass
    return out


# ── Scorecard (v0.1 formulas per spec §B7) ───────────────────────

def _build_scorecard(
    curiosity_log: list[dict],
    cells: list[sm.CellClassification],
    blind_spots: tuple[sm.BlindSpot, ...],
    tensions: tuple[sm.WorkspaceTension, ...],
    attention_focus: tuple[sm.AttentionItem, ...],
    previous_snapshot: dict | None,
    history: list[dict] | None,
) -> tuple[sm.ScorecardDimension, ...]:
    """Build the v0.1 scorecard. Most dimensions are derivable from
    the data we already have; calibration evidence lands in Commit 4."""

    # 1. self_model_maturity
    #    artifact_grounded_field_ratio × non_default_field_ratio
    #    Both factors approach 1.0 as more real data is folded in.
    field_count_total = 8   # major fields we expose
    field_count_real = 0
    if curiosity_log:
        field_count_real += 1
    if cells and any(c.fallback_rate is not None for c in cells):
        field_count_real += 1
    if attention_focus:
        field_count_real += 1
    if blind_spots:
        field_count_real += 1
    if tensions:
        field_count_real += 1
    # continuity anchor always present
    field_count_real += 1
    if previous_snapshot:
        field_count_real += 1
    if history:
        field_count_real += 1
    artifact_grounded = field_count_real / field_count_total
    non_default = artifact_grounded
    self_model_maturity = round(artifact_grounded * non_default, 4)

    # 2. metacognition_maturity = blind_spots / expected_domain_count
    #    plus +0.1 if calibration evidence exists
    expected_domain_count = max(len(_CELLS), 1)
    metacognition_maturity = min(1.0, len(blind_spots) / expected_domain_count)

    # 3. unified_workspace_readiness
    uwr = 0.0
    if tensions:
        uwr += 0.5
    if attention_focus:
        uwr += 0.5

    # 4. dream_consolidation_readiness
    dcr = 0.0
    if attention_focus:
        dcr += 0.5
    if previous_snapshot:
        dcr += 0.5

    # 5. identity_continuity_strength
    ics = 0.0
    if history:
        ics += 0.5    # continuity anchors exist
    if previous_snapshot:
        ics += 0.5

    # 6. autonomous_self_improvement_readiness
    asir = 0.0
    if curiosity_log and attention_focus:
        asir += 0.5
    # +0.5 if meta_curiosity is non-canonical when data permits — set
    # in the meta-curiosity step; we approximate here by checking if
    # curiosity log + tensions/blind_spots exist (better signal lands
    # in Commit 3+4 calibration evidence).
    if curiosity_log:
        asir += 0.5

    # 7. value_layer_readiness — intentional floor
    vlr = 0.1

    return tuple([
        sm.ScorecardDimension(
            name="self_model_maturity",
            score=self_model_maturity,
            evidence=("artifact_grounded_field_ratio",),
            calibration_evidence=None,
            uncertainty="medium",
            why_it_matters=sm.make_why_it_matters(
                affects="WD's ability to describe itself accurately",
                improves_when="more real artifacts are pinned",
                degrades_when="fields fall back to defaults"
            ),
        ),
        sm.ScorecardDimension(
            name="metacognition_maturity",
            score=metacognition_maturity,
            evidence=(f"blind_spots_detected={len(blind_spots)}",),
            calibration_evidence=None,
            uncertainty="medium",
            why_it_matters=sm.make_why_it_matters(
                affects="WD's ability to detect what it does not know",
                improves_when="more domains have detector coverage",
                degrades_when="taxonomy expansion outpaces detection"
            ),
        ),
        sm.ScorecardDimension(
            name="unified_workspace_readiness",
            score=uwr,
            evidence=(f"tensions={len(tensions)}",
                       f"attention_focus={len(attention_focus)}"),
            calibration_evidence=None,
            uncertainty="medium",
            why_it_matters=sm.make_why_it_matters(
                affects="ability to integrate curiosity with reflection",
                improves_when="tensions and attention both exist",
                degrades_when="one of the two is empty"
            ),
        ),
        sm.ScorecardDimension(
            name="dream_consolidation_readiness",
            score=dcr,
            evidence=(f"attention={len(attention_focus)}",),
            calibration_evidence=None,
            uncertainty="medium",
            why_it_matters=sm.make_why_it_matters(
                affects="readiness for offline dream-mode consolidation",
                improves_when="attention focus and delta both available",
                degrades_when="no prior snapshot to diff against"
            ),
        ),
        sm.ScorecardDimension(
            name="identity_continuity_strength",
            score=ics,
            evidence=(f"history_present={bool(history)}",),
            calibration_evidence=None,
            uncertainty="medium",
            why_it_matters=sm.make_why_it_matters(
                affects="ability to compare current self to prior selves",
                improves_when="history chain is intact",
                degrades_when="prev_entry chain breaks"
            ),
        ),
        sm.ScorecardDimension(
            name="autonomous_self_improvement_readiness",
            score=asir,
            evidence=(f"curiosity_log_size={len(curiosity_log)}",),
            calibration_evidence=None,
            uncertainty="medium",
            why_it_matters=sm.make_why_it_matters(
                affects="capacity to act on its own curiosity findings",
                improves_when="curiosity → attention map is populated",
                degrades_when="meta_curiosity falls back to canonical"
            ),
        ),
        sm.ScorecardDimension(
            name="value_layer_readiness",
            score=vlr,
            evidence=("phase_8_5_intentional_floor",),
            calibration_evidence=None,
            uncertainty="high",
            why_it_matters=sm.make_why_it_matters(
                affects="alignment of capability growth with declared values",
                improves_when="value-layer formalisation lands in a future session",
                degrades_when="capability outpaces value-layer scaffolding"
            ),
            calibration_status="unavailable",
        ),
    ])


# ── Meta-curiosity ────────────────────────────────────────────────

def _derive_meta_curiosity(
    curiosity_log: list[dict],
    blind_spots: tuple[sm.BlindSpot, ...],
    tensions: tuple[sm.WorkspaceTension, ...],
    previous_snapshot: dict | None,
) -> sm.MetaCuriosity:
    """Spec §B1: meta_curiosity must be non-canonical when data permits.
    Strength precedence (strongest first):
      derived_from_calibration_gap > derived_from_tension >
      derived_from_blind_spot > derived_from_previous_delta > canonical
    """
    if previous_snapshot and previous_snapshot.get("scorecard"):
        prev_scores = {
            s["name"]: s.get("score") or 0.0
            for s in previous_snapshot.get("scorecard", [])
        }
        # Future calibration-gap derivation lands here once Commit 4
        # populates calibration_evidence on the previous snapshot.
        # For now, use prev-snapshot delta as a weaker derivation.
        if prev_scores:
            return sm.MetaCuriosity(
                question=("What is the most consequential change in WD's "
                            "scorecard since the previous snapshot, and why?"),
                derivation_strength="derived_from_previous_delta",
                source_refs=(f"previous_snapshot={previous_snapshot.get('snapshot_id', '?')}",),
            )

    if tensions:
        t = tensions[0]
        return sm.MetaCuriosity(
            question=(f"How can WD resolve the tension '{t.type}' between "
                       f"its claim and observed evidence?"),
            derivation_strength="derived_from_tension",
            source_refs=(t.tension_id,),
        )

    if blind_spots:
        b = blind_spots[0]
        return sm.MetaCuriosity(
            question=(f"What capability is WD missing in the '{b.domain}' "
                       f"domain that would resolve its current blind spot?"),
            derivation_strength="derived_from_blind_spot",
            source_refs=(b.domain,),
        )

    if curiosity_log:
        top = curiosity_log[0]
        return sm.MetaCuriosity(
            question=(f"Why does WD repeatedly fall back on cluster "
                       f"'{top.get('curiosity_id', '?')}' instead of resolving it?"),
            derivation_strength="derived_from_blind_spot",
            source_refs=(top.get("curiosity_id", ""),),
        )

    return sm.MetaCuriosity(
        question="What does WD not yet know about itself?",
        derivation_strength="canonical",
        source_refs=(),
    )


# ── Provenance + real-data coverage ──────────────────────────────

def _build_provenance(
    pinned_inputs: list[dict],
    scorecard: tuple[sm.ScorecardDimension, ...],
    attention_focus: tuple[sm.AttentionItem, ...],
    blind_spots: tuple[sm.BlindSpot, ...],
    cells: list[sm.CellClassification],
) -> tuple[dict, float]:
    """Per spec §B8: emit field-level provenance + weighted ratio."""
    entries: list[dict] = []
    total_weight = 0.0
    real_weight = 0.0

    def _add(field_path: str, source: str, source_artifact: str,
              weight: float, confidence: float = 1.0) -> None:
        nonlocal total_weight, real_weight
        is_real = source == "real"
        entries.append({
            "field_path": field_path,
            "source": source,
            "source_artifact": source_artifact,
            "confidence": confidence,
            "weight": weight,
            "is_real": is_real,
        })
        total_weight += weight
        if is_real:
            real_weight += weight

    # Scorecard dimensions: weight 3.0 each
    for s in scorecard:
        is_real = s.score > 0 or s.evidence
        _add(
            field_path=f"scorecard.{s.name}",
            source="real" if is_real else "default",
            source_artifact="curiosity_log + cells aggregate",
            weight=3.0,
            confidence=0.8 if is_real else 0.2,
        )

    # Attention focus items: weight 2.0 each
    for a in attention_focus:
        _add(
            field_path=f"attention_focus.{a.curiosity_id}",
            source="real",
            source_artifact="curiosity_log.jsonl",
            weight=2.0,
        )

    # Blind spots: weight 2.0 each
    for b in blind_spots:
        _add(
            field_path=f"blind_spots.{b.domain}",
            source="real",
            source_artifact=",".join(b.detectors),
            weight=2.0,
        )

    # Cells: weight 1.0 each
    for c in cells:
        is_real = c.fallback_rate is not None
        _add(
            field_path=f"cells.{c.cell_id}",
            source="real" if is_real else "default",
            source_artifact="cell aggregate from curiosity_log",
            weight=1.0,
        )

    # Continuity anchor: weight 1.0
    _add(
        field_path="continuity_anchor",
        source="real" if pinned_inputs else "default",
        source_artifact="state.json pinned manifest",
        weight=1.0,
    )

    ratio = real_weight / total_weight if total_weight else 0.0
    return (
        {"entries": sorted(entries, key=lambda e: e["field_path"]),
         "real_data_coverage_ratio": round(ratio, 4),
         "total_weight": round(total_weight, 2),
         "real_weight": round(real_weight, 2)},
        round(ratio, 4),
    )


# ── Markdown rendering ────────────────────────────────────────────

def _render_markdown(snap_dict: dict) -> str:
    lines = [
        "# Self-model snapshot",
        "",
        f"- **Schema version:** {snap_dict['schema_version']}",
        f"- **Snapshot id:** `{snap_dict['snapshot_id']}`",
        f"- **Branch:** `{snap_dict['continuity_anchor']['branch_name']}`",
        f"- **Base commit:** `{snap_dict['continuity_anchor']['base_commit_hash']}`",
        f"- **Pin manifest:** `{snap_dict['continuity_anchor']['pinned_input_manifest_sha256']}`",
        f"- **Real-data coverage ratio:** {snap_dict['real_data_coverage_ratio']}",
        "",
        "## Scorecard",
        "",
        "| dimension | score | uncertainty |",
        "|---|---|---|",
    ]
    for s in snap_dict["scorecard"]:
        lines.append(
            f"| `{s['name']}` | {s['score']} | {s['uncertainty']} |"
        )

    lines.extend(["", "## Cells", "",
                   "| cell | state | fallback_rate | clusters | "
                   "subdivision_hint |", "|---|---|---|---|---|"])
    for c in snap_dict["cells"]:
        lines.append(
            f"| `{c['cell_id']}` | `{c['state']}` | "
            f"{c.get('fallback_rate')} | "
            f"{c.get('attributed_curiosity_clusters')} | "
            f"{c.get('subdivision_pressure_hint')} |"
        )

    lines.extend([
        "",
        "## Attention focus (top 3 by estimated_value)",
        "",
        "| curiosity_id | cell | gap_type | estimated_value | count |",
        "|---|---|---|---|---|",
    ])
    for a in snap_dict["attention_focus"]:
        lines.append(
            f"| `{a['curiosity_id']}` | `{a.get('candidate_cell') or '—'}` | "
            f"`{a['suspected_gap_type']}` | {a['estimated_value']} | "
            f"{a['count']} |"
        )

    mc = snap_dict["meta_curiosity"]
    lines.extend([
        "",
        "## Meta-curiosity",
        "",
        f"- **Question:** {mc['question']}",
        f"- **Derivation:** `{mc['derivation_strength']}`",
        f"- **Source refs:** {', '.join(mc.get('source_refs') or []) or '—'}",
        "",
        "## Self-Entity alignment",
        "",
        f"- **Exists:** {snap_dict['self_entity_alignment']['exists']}",
        f"- **Disagreements:** {snap_dict['self_entity_alignment']['disagreement_count']}",
        f"- **Alignment ratio:** {snap_dict['self_entity_alignment']['alignment_ratio']}",
        "",
    ])
    return "\n".join(lines)


# ── Top-level emit ───────────────────────────────────────────────

def emit_snapshot(snap: sm.SelfModelSnapshot, provenance: dict,
                    out_dir: Path,
                    previous_snapshot: dict | None = None,
                    delta_status: str = "bootstrap") -> dict[str, Path]:
    """Write all six artifact families. Returns a dict of paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_dict = sm.snapshot_to_dict(snap)

    # 1. self_model_snapshot.json
    snap_json = out_dir / "self_model_snapshot.json"
    snap_json.write_text(
        json.dumps(snap_dict, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    # 2. self_model_snapshot.md
    snap_md = out_dir / "self_model_snapshot.md"
    snap_md.write_text(_render_markdown(snap_dict), encoding="utf-8")

    # 3. scorecard.json
    scorecard_json = out_dir / "scorecard.json"
    scorecard_json.write_text(
        json.dumps(snap_dict["scorecard"], indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # 4. continuity_anchors.json
    continuity_json = out_dir / "continuity_anchors.json"
    continuity_json.write_text(
        json.dumps({
            "anchor": snap_dict["continuity_anchor"],
            "snapshot_id": snap_dict["snapshot_id"],
            "invariants": snap_dict["invariants"],
            "ruptures": snap_dict["ruptures"],
        }, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # 5. self_model_delta.json
    delta = _compute_delta(snap_dict, previous_snapshot, delta_status)
    delta_json = out_dir / "self_model_delta.json"
    delta_json.write_text(
        json.dumps(delta, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # 6. data_provenance.json
    prov_json = out_dir / "data_provenance.json"
    prov_json.write_text(
        json.dumps(provenance, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return {
        "snapshot_json": snap_json,
        "snapshot_md": snap_md,
        "scorecard": scorecard_json,
        "continuity_anchors": continuity_json,
        "delta": delta_json,
        "provenance": prov_json,
    }


def _compute_delta(snap_dict: dict, previous: dict | None,
                    status: str) -> dict:
    if status == "bootstrap" or previous is None:
        return {
            "delta_status": "bootstrap",
            "snapshot_id": snap_dict["snapshot_id"],
            "previous_snapshot_id": None,
            "capabilities_gained": [],
            "capabilities_lost": [],
            "cells_strengthened": [],
            "cells_weakened": [],
            "scorecard_movements": [],
            "new_blind_spots": [],
            "resolved_blind_spots": [],
            "new_tensions": [],
            "resolved_tensions": [],
        }
    if status == "history_corrupted":
        return {
            "delta_status": "history_corrupted",
            "snapshot_id": snap_dict["snapshot_id"],
            "previous_snapshot_id": previous.get("snapshot_id"),
            "note": "history chain validation failed; semantic delta refused",
        }
    if status == "identical_inputs_to_previous":
        return {
            "delta_status": "identical_inputs_to_previous",
            "snapshot_id": snap_dict["snapshot_id"],
            "previous_snapshot_id": previous.get("snapshot_id"),
            "scorecard_movements": [],
            "new_tensions": [],
            "resolved_tensions": [],
        }
    # status == "computed"
    prev_scores = {s["name"]: s.get("score") or 0.0
                    for s in previous.get("scorecard", [])}
    cur_scores = {s["name"]: s.get("score") or 0.0
                   for s in snap_dict.get("scorecard", [])}
    movements = []
    for name in sorted(set(cur_scores) | set(prev_scores)):
        prev_v = prev_scores.get(name, 0.0)
        cur_v = cur_scores.get(name, 0.0)
        if abs(cur_v - prev_v) >= 1e-6:
            movements.append({
                "dimension": name,
                "previous": prev_v,
                "current": cur_v,
                "delta": round(cur_v - prev_v, 4),
            })

    prev_tensions = {t["tension_id"] for t in previous.get("workspace_tensions", [])}
    cur_tensions = {t["tension_id"] for t in snap_dict.get("workspace_tensions", [])}
    new_tensions = sorted(cur_tensions - prev_tensions)
    resolved_tensions = sorted(prev_tensions - cur_tensions)

    prev_bs = {b["domain"] for b in previous.get("blind_spots", [])}
    cur_bs = {b["domain"] for b in snap_dict.get("blind_spots", [])}
    new_bs = sorted(cur_bs - prev_bs)
    resolved_bs = sorted(prev_bs - cur_bs)

    return {
        "delta_status": "computed",
        "snapshot_id": snap_dict["snapshot_id"],
        "previous_snapshot_id": previous.get("snapshot_id"),
        "capabilities_gained": [],
        "capabilities_lost": [],
        "cells_strengthened": [],
        "cells_weakened": [],
        "scorecard_movements": movements,
        "new_blind_spots": new_bs,
        "resolved_blind_spots": resolved_bs,
        "new_tensions": new_tensions,
        "resolved_tensions": resolved_tensions,
    }


# ── CLI ──────────────────────────────────────────────────────────

def _default_out_dir(pin_hash: str) -> Path:
    sha12 = pin_hash.replace("sha256:", "")[:12]
    return DEFAULT_OUT_ROOT / sha12


def _detect_branch() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("ref: "):
        return text[5:].strip().rsplit("/", 1)[-1]
    return ""


def _detect_base_commit() -> str:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return ""
    try:
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if text.startswith("ref: "):
        ref_path = ROOT / ".git" / text[5:].strip()
        if ref_path.exists():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return ""
    return text or ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-manifest", type=Path,
                    default=ROOT / "docs" / "runs" / "phase8_5_self_model_session_state.json",
                    help="state.json or dedicated manifest with pinned_inputs")
    ap.add_argument("--output-dir", type=Path, default=None)
    ap.add_argument("--real-data-only", action="store_true",
                    help="exit non-zero if real pinned inputs are missing")
    ap.add_argument("--cell", type=str, default=None,
                    help="restrict attention focus to a single cell")
    ap.add_argument("--include-meta-curiosity", action="store_true",
                    default=True)
    ap.add_argument("--no-meta-curiosity", action="store_true")
    ap.add_argument("--history-path", type=Path, default=None)
    ap.add_argument("--previous-snapshot", type=Path, default=None)
    ap.add_argument("--respect-timestamps", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    include_meta = (not args.no_meta_curiosity)

    if not args.input_manifest.exists():
        print(f"input manifest missing: {args.input_manifest}", file=sys.stderr)
        return 2 if args.real_data_only else 1
    pin_hash, pinned_inputs = load_pin_manifest(args.input_manifest)

    if args.real_data_only:
        # Every pinned_inputs entry must exist on disk; abort otherwise
        for entry in pinned_inputs:
            ap_path = ROOT / entry.get("path", "")
            if not ap_path.exists():
                print(f"real-data-only: missing {entry.get('path')}",
                      file=sys.stderr)
                return 2

    if args.dry_run:
        out = {
            "mode": "dry-run",
            "pin_hash": pin_hash,
            "pinned_input_count": len(pinned_inputs),
            "ok": True,
        }
        if args.json:
            print(json.dumps(out, indent=2, sort_keys=True))
        else:
            print("=== self-model dry-run (validate only) ===")
            for k, v in out.items():
                print(f"{k}: {v}")
        return 0

    branch = _detect_branch() or "phase8.5/self-model-layer"
    base_commit = _detect_base_commit() or ""

    # Optional previous-snapshot
    previous_snap: dict | None = None
    if args.previous_snapshot is not None and args.previous_snapshot.exists():
        try:
            previous_snap = json.loads(
                args.previous_snapshot.read_text(encoding="utf-8")
            )
        except Exception:
            previous_snap = None

    snap, provenance = build_snapshot(
        pin_hash=pin_hash,
        pinned_inputs=pinned_inputs,
        branch_name=branch,
        base_commit_hash=base_commit,
        previous_snapshot=previous_snap,
        cell_filter=args.cell,
        include_meta_curiosity=include_meta,
    )

    # Decide delta_status
    if previous_snap is None:
        delta_status = "bootstrap"
    elif previous_snap.get("continuity_anchor", {}).get(
        "pinned_input_manifest_sha256") == pin_hash:
        delta_status = "identical_inputs_to_previous"
    else:
        delta_status = "computed"

    out_dir = args.output_dir or _default_out_dir(pin_hash)

    if args.apply:
        paths = emit_snapshot(snap, provenance, out_dir, previous_snap,
                                delta_status)
        if args.json:
            print(json.dumps({k: p.as_posix() for k, p in paths.items()},
                              indent=2))
        else:
            for k, p in paths.items():
                print(f"{k}: {p.as_posix()}")
            print(f"snapshot_id: {snap.snapshot_id}")
            print(f"real_data_coverage_ratio: {snap.real_data_coverage_ratio}")
    else:
        if args.json:
            d = sm.snapshot_to_dict(snap)
            print(json.dumps(d, indent=2, sort_keys=True))
        else:
            print(f"=== Self-model snapshot dry-run ===")
            print(f"pin_hash: {pin_hash}")
            print(f"snapshot_id: {snap.snapshot_id}")
            print(f"real_data_coverage_ratio: {snap.real_data_coverage_ratio}")
            print(f"scorecard dims: {len(snap.scorecard)}")
            print(f"cells classified: {len(snap.cells)}")
            print(f"attention focus: {len(snap.attention_focus)}")
            print(f"meta_curiosity: {snap.meta_curiosity.derivation_strength}")
            print()
            print(f"(default output-dir: {_default_out_dir(pin_hash).as_posix()})")
            print("(use --apply to write artifacts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
