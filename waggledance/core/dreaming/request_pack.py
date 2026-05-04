"""Dream teacher input pack — Phase 8.5 Session C, deliverable C.2.

Builds deterministic dream_request_pack.json/md artifacts from a
selected DreamNight, a cell manifest, attention_focus slices, and a
replay_case_manifest slice. The pack is the structured input the
external dream teacher prompt instructs an LLM to consume; this code
NEVER calls an LLM itself.

dream_request_pack_sha12 rule (c.txt §C2):
- compute as sha256 of canonical JSON of the pack with the sha12 field
  omitted
- truncate to 12 chars
- compute it last; write it last
- never include the sha field itself in the hash input
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import DREAMING_SCHEMA_VERSION
from .curriculum import DreamNight, DreamableItem


SHA_FIELD = "dream_request_pack_sha12"


# ── Data class ───────────────────────────────────────────────────-

@dataclass(frozen=True)
class DreamRequestPack:
    schema_version: int
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str
    replay_manifest_sha256: str | None
    night_index: int
    candidate_cell: str
    requested_action_type: str
    source_tension_ids: tuple[str, ...]
    source_curiosity_ids: tuple[str, ...]
    replay_case_ids: tuple[str, ...]
    structural_context: dict
    cell_manifest_summary: dict
    self_model_snippet: dict
    attention_focus: tuple[dict, ...]
    why_now: str
    uncertainty: str
    mode: str
    primary_source: str
    dream_request_pack_sha12: str = ""


# ── Serialization helpers ────────────────────────────────────────-

def _canonical_json(obj: Any) -> str:
    """Stable canonical serializer used for hashing. Sorts keys, uses
    compact separators, and forbids time-based fields."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False)


def _pack_to_dict(p: DreamRequestPack, *, with_sha: bool) -> dict:
    d = {
        "schema_version": p.schema_version,
        "continuity_anchor": {
            "branch_name": p.branch_name,
            "base_commit_hash": p.base_commit_hash,
            "pinned_input_manifest_sha256": p.pinned_input_manifest_sha256,
            "replay_manifest_sha256": p.replay_manifest_sha256,
        },
        "night_index": p.night_index,
        "candidate_cell": p.candidate_cell,
        "requested_action_type": p.requested_action_type,
        "source_tension_ids": list(p.source_tension_ids),
        "source_curiosity_ids": list(p.source_curiosity_ids),
        "replay_case_ids": list(p.replay_case_ids),
        "structural_context": p.structural_context,
        "cell_manifest_summary": p.cell_manifest_summary,
        "self_model_snippet": p.self_model_snippet,
        "attention_focus": list(p.attention_focus),
        "why_now": p.why_now,
        "uncertainty": p.uncertainty,
        "mode": p.mode,
        "primary_source": p.primary_source,
    }
    if with_sha:
        d[SHA_FIELD] = p.dream_request_pack_sha12
    return d


def compute_pack_sha12(pack_dict_without_sha: dict) -> str:
    """Per c.txt §C2: sha256 of canonical JSON, truncated to 12 chars.
    Caller must ensure the SHA_FIELD is NOT present in the input."""
    if SHA_FIELD in pack_dict_without_sha:
        raise ValueError(f"{SHA_FIELD} must not be in the hash input")
    canonical = _canonical_json(pack_dict_without_sha)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ── Pack construction ────────────────────────────────────────────-

_MODE_TO_ACTION = {
    "base_solver_growth": "new_solver",
    "solver_refinement": "solver_improvement",
    "bridge_composition": "bridge_composition",
    "subdivision_pressure": "subdivision_recommendation",
    "introspection": "self_inspection",
    "wait": "wait",
}


def _slice_self_model(self_model: dict, tension_ids: Iterable[str]) -> dict:
    """Return a small snippet of the self-model relevant to the given
    tension IDs — never the full snapshot, never secrets."""
    ids = set(tension_ids)
    tensions = [
        t for t in (self_model.get("workspace_tensions") or [])
        if t.get("tension_id") in ids
    ]
    return {
        "schema_version": self_model.get("schema_version"),
        "workspace_tensions": tensions,
        "scorecard": self_model.get("scorecard") or {},
        "blind_spots": self_model.get("blind_spots") or [],
    }


def _replay_case_ids_for_night(replay_case_manifest: dict | None,
                                  tension_ids: Iterable[str],
                                  curiosity_ids: Iterable[str],
                                  max_cases: int = 50) -> list[str]:
    """Pick replay_case_ids whose source_tension_id or
    source_curiosity_id matches the night. Bounded and deterministic."""
    if not replay_case_manifest:
        return []
    cases = replay_case_manifest.get("cases") or []
    tids = set(tension_ids)
    cids = set(curiosity_ids)
    picked: list[str] = []
    for case in cases:
        if case.get("source_tension_id") in tids or \
            case.get("source_curiosity_id") in cids:
            picked.append(case.get("replay_case_id", ""))
    picked = sorted({rid for rid in picked if rid})
    return picked[:max_cases]


def build_request_pack(
    night: DreamNight,
    self_model: dict,
    cell_manifest: dict,
    attention_focus: list[dict],
    replay_case_manifest: dict | None,
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    replay_manifest_sha256: str | None = None,
    structural_context_extra: dict | None = None,
) -> DreamRequestPack:
    """Build a single DreamRequestPack for one night."""
    items: list[DreamableItem] = list(night.target_items)
    tension_ids = tuple(it.source_id for it in items
                          if it.source_kind == "tension")
    curiosity_ids = tuple(it.source_id for it in items
                            if it.source_kind == "curiosity")
    candidate_cell = (night.primary_cells[0]
                       if night.primary_cells else "general")
    action = _MODE_TO_ACTION.get(night.mode, "wait")

    structural_context = {
        "primary_cells": list(night.primary_cells),
        "secondary_cells": list(night.secondary_cells),
        "dream_objective": night.dream_objective,
    }
    if structural_context_extra:
        structural_context.update(structural_context_extra)

    snippet = _slice_self_model(self_model, tension_ids)
    replay_ids = _replay_case_ids_for_night(
        replay_case_manifest, tension_ids, curiosity_ids,
    )

    pack = DreamRequestPack(
        schema_version=DREAMING_SCHEMA_VERSION,
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
        replay_manifest_sha256=replay_manifest_sha256,
        night_index=night.night_index,
        candidate_cell=candidate_cell,
        requested_action_type=action,
        source_tension_ids=tension_ids,
        source_curiosity_ids=curiosity_ids,
        replay_case_ids=tuple(replay_ids),
        structural_context=structural_context,
        cell_manifest_summary={
            "cell_id": cell_manifest.get("cell_id"),
            "solver_count": cell_manifest.get("solver_count"),
        },
        self_model_snippet=snippet,
        attention_focus=tuple(attention_focus[:3]),
        why_now=night.why_now,
        uncertainty=night.uncertainty,
        mode=night.mode,
        primary_source=night.primary_source,
    )

    # Compute sha12 LAST, with sha field omitted
    pack_no_sha = _pack_to_dict(pack, with_sha=False)
    sha12 = compute_pack_sha12(pack_no_sha)
    return DreamRequestPack(
        **{**asdict(pack), "dream_request_pack_sha12": sha12},
    )


# ── Emission ─────────────────────────────────────────────────────-

def pack_to_dict(p: DreamRequestPack) -> dict:
    """Stable dict form including the sha12 field."""
    return _pack_to_dict(p, with_sha=True)


def render_pack_md(pack: DreamRequestPack) -> str:
    lines = [
        "# Dream request pack",
        "",
        f"- **Schema version:** {pack.schema_version}",
        f"- **Branch:** `{pack.branch_name}`",
        f"- **Base commit:** `{pack.base_commit_hash}`",
        f"- **Pin manifest:** `{pack.pinned_input_manifest_sha256}`",
        f"- **Replay manifest:** `{pack.replay_manifest_sha256 or '(none)'}`",
        f"- **Pack sha12:** `{pack.dream_request_pack_sha12}`",
        f"- **Night index:** {pack.night_index}",
        f"- **Candidate cell:** `{pack.candidate_cell}`",
        f"- **Mode:** `{pack.mode}`",
        f"- **Requested action:** `{pack.requested_action_type}`",
        f"- **Primary source:** {pack.primary_source}",
        f"- **Uncertainty:** {pack.uncertainty}",
        "",
        "## Source signals",
        "",
        f"- **Tensions:** {list(pack.source_tension_ids) or '(none)'}",
        f"- **Curiosities:** {list(pack.source_curiosity_ids) or '(none)'}",
        f"- **Replay cases:** {len(pack.replay_case_ids)} bounded by MAX_REPLAY_CASES",
        "",
        "## Why now",
        "",
        f"> {pack.why_now}",
        "",
        "## Linkage rule",
        "",
        ("Any LLM-generated proposal responding to this pack must include "
         f"`{pack.dream_request_pack_sha12}` in the sidecar field "
         "`responding_to_dream_request_pack_sha12`. Proposals without a "
         "matching sidecar (or matching schema-allowed inline field) are "
         "rejected as orphans."),
        "",
    ]
    return "\n".join(lines)


def emit_pack(pack: DreamRequestPack, out_dir: Path) -> dict[str, Path]:
    """Write dream_request_pack.{json,md} under out_dir. Filenames are
    indexed by night_index to disambiguate when more than one pack is
    written into the same directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"dream_request_pack_night_{pack.night_index:02d}"
    json_path = out_dir / f"{base}.json"
    md_path = out_dir / f"{base}.md"
    json_path.write_text(
        json.dumps(pack_to_dict(pack), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    md_path.write_text(render_pack_md(pack), encoding="utf-8")
    return {"pack_json": json_path, "pack_md": md_path}
