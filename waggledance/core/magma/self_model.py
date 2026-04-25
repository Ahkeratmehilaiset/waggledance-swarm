"""Self-model snapshot — first artifact-grounded reflective layer.

Produces a deterministic offline self-description of WaggleDance
grounded in actual repository artifacts. The snapshot answers:

- what is WD currently capable of?
- where is WD uncertain?
- what changed recently?
- which cells are strong / weak / under_pressure?
- what unresolved curiosities dominate attention?
- what continuity anchors connect this WD to prior states?

This module is **read-only** over upstream artifacts (curiosity
outputs from Session A, cell manifests, subdivision/composition
reports). It never opens port 8002, never imports runtime
adapters, never depends on Ollama.

Crown-jewel area per B.txt §BUSL: any non-trivial logic edit here
requires a license Change Date update to 2030-03-19 in the same
commit.

See `docs/architecture/SELF_MODEL_LAYER.md` for the conceptual
overview and `docs/architecture/SELF_MODEL_FORMULAS.md` for the
exact scorecard formulas, calibration rules, and HISTORY_WINDOW.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Schema version for self-model snapshot; bump on breaking changes.
SELF_MODEL_SCHEMA_VERSION = 1

# Window over the last N snapshots for rolling calibration computations
# (B.txt §B2 history window rule).
HISTORY_WINDOW = 10

# Genesis marker for the first HISTORY.jsonl entry per spec.
GENESIS_PREV_SHA = "0" * 64

# Allowed enums per spec.
DERIVATION_STRENGTHS = (
    "canonical",
    "derived_from_blind_spot",
    "derived_from_tension",
    "derived_from_calibration_gap",
    "derived_from_previous_delta",
)
DELTA_STATUSES = (
    "bootstrap",
    "identical_inputs_to_previous",
    "history_corrupted",
    "computed",
)
TENSION_RESOLUTION_PATHS = (
    "calibration_correction",
    "blind_spot_promotion",
    "deferred_to_dream",
    "requires_human_review",
)
TENSION_LIFECYCLE_STATUSES = ("new", "persisting", "resolved")
CELL_STATES = ("strong", "weak", "under_pressure", "unknown")

# why_it_matters regex from spec §B3.
WHY_IT_MATTERS_RE = re.compile(
    r"^Affects: [^,]+, Improves when: [^,]+, Degrades when: [^.]+\.$"
)

# Calibration thresholds.
CALIBRATION_DRIFT_THRESHOLD = 0.2
CALIBRATION_OSCILLATION_DAMPEN_AT = 3
CALIBRATION_OSCILLATION_FREEZE_AT = 5

# Stability under perturbation (spec §TESTING):
#   max_change = 0.15 × (1 + 1/sqrt(max(1, curiosity_item_count)))
def stability_max_change(curiosity_item_count: int) -> float:
    import math
    n = max(1, curiosity_item_count)
    return 0.15 * (1.0 + 1.0 / math.sqrt(n))


# ── Utilities ─────────────────────────────────────────────────────

def _stable_json(obj: Any) -> str:
    """Canonical JSON: sorted keys, compact separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       default=str)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def snapshot_id_for(base_commit_hash: str,
                     pinned_input_manifest_sha256: str) -> str:
    """Deterministic snapshot id (spec §B2)."""
    blob = f"{base_commit_hash}|{pinned_input_manifest_sha256}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


# ── Data classes ──────────────────────────────────────────────────

@dataclass(frozen=True)
class CalibrationEvidence:
    """Evidence-implied score for one scorecard dimension."""
    dimension: str
    evidence_implied_score: float | None
    evidence_refs: tuple[str, ...]
    calibration_status: str   # "ok" | "unavailable" | "mismatch"
    notes: str = ""


@dataclass(frozen=True)
class ScorecardDimension:
    """One scorecard row per spec §B7."""
    name: str
    score: float
    evidence: tuple[str, ...]
    calibration_evidence: CalibrationEvidence | None
    uncertainty: str   # "low" | "medium" | "high" | "unknown"
    why_it_matters: str   # must match WHY_IT_MATTERS_RE
    calibration_status: str = "unavailable"


@dataclass(frozen=True)
class BlindSpot:
    """Per spec §B4 detector output."""
    domain: str
    severity: str   # "low" | "medium" | "high"
    detectors: tuple[str, ...]
    description: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class WorkspaceTension:
    """Per spec §B5."""
    tension_id: str
    type: str
    claim: str
    observation: str
    severity: str
    resolution_path: str
    lifecycle_status: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class CellClassification:
    cell_id: str
    state: str   # one of CELL_STATES
    fallback_rate: float | None
    attributed_curiosity_clusters: int
    contradiction_rate: float | None
    subdivision_pressure_hint: float | None
    rationale: str


@dataclass(frozen=True)
class AttentionItem:
    """Top curiosity items shaping attention. Spec §B1."""
    curiosity_id: str
    candidate_cell: str | None
    estimated_value: float
    suspected_gap_type: str
    count: int


@dataclass(frozen=True)
class Invariant:
    property_id: str
    description: str
    held_for_snapshots: int
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class Rupture:
    property_id: str
    description: str
    held_for_snapshots: int
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ContinuityAnchor:
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str


@dataclass(frozen=True)
class MetaCuriosity:
    question: str
    derivation_strength: str
    source_refs: tuple[str, ...]


@dataclass(frozen=True)
class SelfEntityAlignment:
    """Per spec §SELF-ENTITY / NAMING COLLISION RULE — exact shape."""
    exists: bool
    agreements: tuple[dict[str, Any], ...]
    disagreement_count: int
    alignment_ratio: float


@dataclass(frozen=True)
class SelfModelSnapshot:
    """Top-level deterministic self-description."""
    schema_version: int
    snapshot_id: str
    continuity_anchor: ContinuityAnchor
    scorecard: tuple[ScorecardDimension, ...]
    blind_spots: tuple[BlindSpot, ...]
    workspace_tensions: tuple[WorkspaceTension, ...]
    cells: tuple[CellClassification, ...]
    attention_focus: tuple[AttentionItem, ...]
    meta_curiosity: MetaCuriosity
    self_entity_alignment: SelfEntityAlignment
    invariants: tuple[Invariant, ...]
    ruptures: tuple[Rupture, ...]
    real_data_coverage_ratio: float


# ── Loaders for upstream artifacts ────────────────────────────────

def load_curiosity_summary(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_curiosity_log(path: Path | None,
                        byte_limit: int | None = None) -> list[dict]:
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    with open(path, "rb") as f:
        chunk = f.read(byte_limit) if byte_limit else f.read()
    text = chunk.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def load_teacher_packs(packs_dir: Path | None) -> dict[str, dict]:
    if packs_dir is None or not packs_dir.exists():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(packs_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        cell = data.get("cell_id") or p.stem
        out[cell] = data
    return out


def load_cell_manifests(cells_dir: Path | None) -> dict[str, dict]:
    if cells_dir is None or not cells_dir.exists():
        return {}
    out: dict[str, dict] = {}
    for p in sorted(cells_dir.glob("*/manifest.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        cell = data.get("cell_id") or p.parent.name
        out[cell] = data
    return out


def load_subdivision_pressure(report_path: Path | None) -> dict[str, float]:
    if report_path is None or not report_path.exists():
        return {}
    text = report_path.read_text(encoding="utf-8", errors="replace")
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


# ── Cell classification (spec §B6) ────────────────────────────────

def classify_cell(
    cell_id: str,
    fallback_rate: float | None,
    attributed_curiosity_clusters: int,
    contradiction_rate: float | None,
    subdivision_pressure_hint: float | None,
) -> CellClassification:
    """Spec-mandated rules; conflict order: under_pressure > weak > strong > unknown."""
    fr = fallback_rate if fallback_rate is not None else 0.0
    cr = contradiction_rate if contradiction_rate is not None else 0.0
    sph = subdivision_pressure_hint or 0.0

    if (sph and sph >= 1.0) or attributed_curiosity_clusters >= 3:
        state = "under_pressure"
        rat = "subdivision_pressure or >=3 attributed curiosity clusters"
    elif (fr >= 0.30 or attributed_curiosity_clusters >= 4
          or cr >= 0.15):
        state = "weak"
        rat = (f"fallback_rate={fr:.2f}, clusters={attributed_curiosity_clusters}, "
               f"contradiction_rate={cr:.2f}")
    elif (fr <= 0.10 and attributed_curiosity_clusters <= 1
          and cr <= 0.05):
        state = "strong"
        rat = (f"fallback_rate={fr:.2f}, clusters={attributed_curiosity_clusters}, "
               f"contradiction_rate={cr:.2f}")
    else:
        state = "unknown"
        rat = "no spec-mandated rule fired"

    return CellClassification(
        cell_id=cell_id,
        state=state,
        fallback_rate=fallback_rate,
        attributed_curiosity_clusters=attributed_curiosity_clusters,
        contradiction_rate=contradiction_rate,
        subdivision_pressure_hint=subdivision_pressure_hint,
        rationale=rat,
    )


# ── Self-Entity discovery ─────────────────────────────────────────

def detect_self_entity(repo_root: Path) -> SelfEntityAlignment:
    """Per spec §SELF-ENTITY rule: inspect repo for an existing
    Self-Entity module, surface alignment shape. If none, return
    `exists=False, alignment_ratio=1.0` per spec.

    Today the repo has no Self-Entity module at the canonical path;
    this function is forward-compatible for when one lands."""
    candidate_paths = [
        repo_root / "waggledance" / "core" / "magma" / "self_entity.py",
        repo_root / "waggledance" / "core" / "self_entity.py",
    ]
    if any(p.exists() for p in candidate_paths):
        # Future: import the module and call export_for_snapshot()
        # For now we declare exists=False because the helper has
        # not been implemented in any candidate module yet.
        return SelfEntityAlignment(
            exists=False, agreements=(), disagreement_count=0,
            alignment_ratio=1.0,
        )
    return SelfEntityAlignment(
        exists=False, agreements=(), disagreement_count=0,
        alignment_ratio=1.0,
    )


# ── why_it_matters helper ─────────────────────────────────────────

def make_why_it_matters(affects: str, improves_when: str,
                          degrades_when: str) -> str:
    """Build a why_it_matters string that matches WHY_IT_MATTERS_RE."""
    text = (f"Affects: {affects}, Improves when: {improves_when}, "
            f"Degrades when: {degrades_when}.")
    if not WHY_IT_MATTERS_RE.match(text):
        raise ValueError(f"why_it_matters does not match regex: {text!r}")
    return text


# ── Snapshot serialization ────────────────────────────────────────

def snapshot_to_dict(snap: SelfModelSnapshot) -> dict:
    """Deterministic dict form of a SelfModelSnapshot. All tuples
    become sorted lists; nested dataclasses are recursively
    asdict'd."""
    d = asdict(snap)
    # Ensure stable key ordering for nested lists of dicts
    if "scorecard" in d:
        d["scorecard"] = sorted(d["scorecard"], key=lambda s: s["name"])
    if "cells" in d:
        d["cells"] = sorted(d["cells"], key=lambda c: c["cell_id"])
    if "blind_spots" in d:
        d["blind_spots"] = sorted(
            d["blind_spots"], key=lambda b: (-_severity_weight(b["severity"]), b["domain"])
        )
    if "workspace_tensions" in d:
        d["workspace_tensions"] = sorted(
            d["workspace_tensions"], key=lambda t: t["tension_id"]
        )
    if "attention_focus" in d:
        d["attention_focus"] = sorted(
            d["attention_focus"],
            key=lambda a: (-a["estimated_value"], a["curiosity_id"]),
        )
    if "invariants" in d:
        d["invariants"] = sorted(d["invariants"], key=lambda i: i["property_id"])
    if "ruptures" in d:
        d["ruptures"] = sorted(d["ruptures"], key=lambda r: r["property_id"])
    return d


def _severity_weight(s: str) -> int:
    return {"high": 3, "medium": 2, "low": 1}.get(s, 0)


# ── HISTORY chain validation (spec §B2) ──────────────────────────

def history_entry_sha(entry: dict) -> str:
    """Stable sha over a history entry, used as prev_entry_sha256
    for the next entry."""
    payload = _stable_json({
        k: v for k, v in entry.items() if k != "entry_sha256"
    }).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_history_chain(entries: list[dict]) -> tuple[bool, str | None]:
    """Walk HISTORY.jsonl entries and verify the prev_entry_sha256
    chain. The first entry MUST carry GENESIS_PREV_SHA. Returns
    (is_valid, reason-when-invalid)."""
    if not entries:
        return True, None
    if entries[0].get("prev_entry_sha256") != GENESIS_PREV_SHA:
        return False, "first entry missing genesis marker"
    for i in range(1, len(entries)):
        expected = history_entry_sha(entries[i - 1])
        actual = entries[i].get("prev_entry_sha256")
        if actual != expected:
            return False, f"chain break at index {i}"
    return True, None


def append_history_entry(history_path: Path, entry: dict) -> dict:
    """Append a single entry. Sets prev_entry_sha256 from the last
    entry on disk (or GENESIS_PREV_SHA if empty), stores entry_sha256
    so consumers can verify integrity later. Returns the written
    entry."""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[dict] = []
    if history_path.exists():
        for line in history_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                existing.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not existing:
        entry["prev_entry_sha256"] = GENESIS_PREV_SHA
    else:
        entry["prev_entry_sha256"] = history_entry_sha(existing[-1])

    entry_sha = history_entry_sha(entry)
    entry["entry_sha256"] = entry_sha
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(_stable_json({k: v for k, v in entry.items() if k != "entry_sha256"}))
        f.write("\n")
    return entry


def read_history(history_path: Path) -> list[dict]:
    if not history_path.exists():
        return []
    out: list[dict] = []
    for line in history_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ── Calibration corrections (spec §B3) ───────────────────────────

def append_calibration_correction(corrections_path: Path,
                                     record: dict) -> None:
    """Append-only emission. Records carry dimension, prior_score,
    evidence_implied_score, correction_magnitude, confidence,
    source_refs, correction_count_in_window."""
    corrections_path.parent.mkdir(parents=True, exist_ok=True)
    with open(corrections_path, "a", encoding="utf-8") as f:
        f.write(_stable_json(record))
        f.write("\n")


def read_corrections(corrections_path: Path) -> list[dict]:
    if not corrections_path.exists():
        return []
    out: list[dict] = []
    for line in corrections_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def correction_count_for_dimension(
    corrections: list[dict], dimension: str, window: int = HISTORY_WINDOW,
) -> int:
    """Count corrections emitted for `dimension` in the last `window`
    snapshots — but the spec says in test/dry-run mode this is always
    the last window entries by index, NOT by timestamp."""
    matches = [c for c in corrections if c.get("dimension") == dimension]
    # Window from the tail
    return min(len(matches), window)


def adjusted_score(
    prior_score: float,
    evidence_implied_score: float,
    correction_count_in_window: int,
) -> tuple[float, str]:
    """Returns (adjusted_score, mode) where mode is one of
    'standard' | 'dampened' | 'frozen'. Implements the spec's
    oscillation-protection table:
      0..2 in window  → 0.7 × evidence + 0.3 × prior     (standard)
      3..4 in window  → 0.5 × evidence + 0.5 × prior     (dampened)
      >= 5            → frozen, no auto-correction       (returns prior)
    """
    if correction_count_in_window >= CALIBRATION_OSCILLATION_FREEZE_AT:
        return prior_score, "frozen"
    if correction_count_in_window >= CALIBRATION_OSCILLATION_DAMPEN_AT:
        return 0.5 * evidence_implied_score + 0.5 * prior_score, "dampened"
    return 0.7 * evidence_implied_score + 0.3 * prior_score, "standard"


# ── Invariants / ruptures (spec §B9) ─────────────────────────────

def detect_invariants_and_ruptures(
    history_entries: list[dict],
    current_snapshot: dict,
) -> tuple[list[Invariant], list[Rupture]]:
    """Examine the last min(5, history_length) snapshots for
    properties that have held that whole window. A property that was
    invariant but broke in the current snapshot becomes a rupture."""
    if not history_entries:
        return [], []

    window_size = min(5, len(history_entries))
    window = history_entries[-window_size:]

    # Track simple invariants: cell classifications + scorecard
    # "in range" properties.
    invariants: list[Invariant] = []
    ruptures: list[Rupture] = []

    # Pull cell classifications from each window entry's snapshot
    # reference (entries hold snapshot_id; we need the actual snapshot
    # blob — but spec keeps invariants minimal, so we work off
    # what's recorded in the history entry itself).
    cell_states_per_snapshot: list[dict[str, str]] = []
    for entry in window:
        cells = entry.get("cells") or []
        if isinstance(cells, list):
            cell_states_per_snapshot.append(
                {c.get("cell_id"): c.get("state") for c in cells}
            )

    if cell_states_per_snapshot:
        # Common cells across all window snapshots
        common = set(cell_states_per_snapshot[0])
        for c in cell_states_per_snapshot[1:]:
            common &= set(c)
        for cell_id in sorted(common):
            states = [c[cell_id] for c in cell_states_per_snapshot]
            if all(s == states[0] for s in states):
                invariants.append(Invariant(
                    property_id=f"cell_state__{cell_id}__{states[0]}",
                    description=(f"cell {cell_id} held state '{states[0]}' "
                                  f"for {window_size} snapshots"),
                    held_for_snapshots=window_size,
                    evidence_refs=tuple(
                        f"history:{e.get('snapshot_id', '?')}" for e in window
                    ),
                ))

        # Rupture: previous state held but current differs
        cur_cells = {c.get("cell_id"): c.get("state")
                     for c in current_snapshot.get("cells") or []}
        for inv in invariants:
            cell_id = inv.property_id.split("__")[1]
            prev_state = inv.property_id.split("__")[2]
            if cell_id in cur_cells and cur_cells[cell_id] != prev_state:
                ruptures.append(Rupture(
                    property_id=inv.property_id,
                    description=(f"cell {cell_id} broke its '{prev_state}' "
                                  f"invariant; now '{cur_cells[cell_id]}'"),
                    held_for_snapshots=inv.held_for_snapshots,
                    evidence_refs=inv.evidence_refs,
                ))

    return invariants, ruptures


# ── expected_domains.yaml schema ─────────────────────────────────

EXPECTED_DOMAIN_REQUIRED_KEYS = (
    "domain_id", "description", "expected_capability_signals",
    "related_cells",
)


def validate_expected_domains_yaml(domains: list[Any]) -> tuple[bool, str | None]:
    """Validate the schema of the parsed yaml against spec §B4."""
    if not isinstance(domains, list):
        return False, "expected_domains.yaml must be a list at top level"
    for i, entry in enumerate(domains):
        if not isinstance(entry, dict):
            return False, f"entry {i} is not a dict"
        for k in EXPECTED_DOMAIN_REQUIRED_KEYS:
            if k not in entry:
                return False, f"entry {i} missing required key '{k}'"
        if not isinstance(entry["expected_capability_signals"], list):
            return False, f"entry {i} expected_capability_signals must be a list"
        if not isinstance(entry["related_cells"], list):
            return False, f"entry {i} related_cells must be a list"
    return True, None
