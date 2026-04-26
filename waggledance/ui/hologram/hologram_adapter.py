"""Hologram adapter — Phase 9 §P.

Bridges legacy hologram entrypoints (if any) to the new Reality View.
The adapter never fabricates values; if a data source is unavailable
it returns the explicit unavailable panel from reality_view.

Per Prompt_1_Master §P CRITICAL LEGACY HOLOGRAM RULE:
- preserve compatibility if practical
- replace placeholder/static internals with real adapters
- in new docs/comments refer to it as Reality View / Cognitive Matrix
"""
from __future__ import annotations

import json
from pathlib import Path

from .reality_view import (
    RealitySnapshot,
    build_panels_from_state,
    make_reality_snapshot,
)


def _load_optional_json(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _load_optional_jsonl(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def render_from_artifacts(*,
                                produced_at_iso: str,
                                self_model_path: Path | None = None,
                                world_model_path: Path | None = None,
                                dream_curriculum_path: Path | None = None,
                                hive_review_bundle_path: Path | None = None,
                                vector_graph_path: Path | None = None,
                                missions_path: Path | None = None,
                                branch_name: str = "phase9/autonomy-fabric",
                                base_commit_hash: str = "",
                                pinned_input_manifest_sha256: str = "sha256:unknown",
                                ) -> RealitySnapshot:
    """Read all available pinned artifacts and render a RealitySnapshot.
    Missing artifacts produce explicit unavailable panels — no fabrication."""
    self_model = _load_optional_json(self_model_path)
    world_model = _load_optional_json(world_model_path)
    dream_curriculum = _load_optional_json(dream_curriculum_path)
    hive_bundle = _load_optional_json(hive_review_bundle_path)
    vector_graph = _load_optional_json(vector_graph_path)
    missions = _load_optional_jsonl(missions_path)

    panels = build_panels_from_state(
        mission_queue=missions,
        self_model=self_model,
        world_model_snapshot=world_model,
        dream_curriculum=dream_curriculum,
        hive_review_bundle=hive_bundle,
        vector_graph=vector_graph,
    )
    return make_reality_snapshot(
        produced_at_iso=produced_at_iso, panels=panels,
        branch_name=branch_name, base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
    )
