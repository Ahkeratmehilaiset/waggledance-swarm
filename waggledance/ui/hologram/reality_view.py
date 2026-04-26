"""Reality View — Phase 9 §P.

Replaces the legacy hologram's placeholder/static internals with a
real adapter that pulls live state from actual artifacts.

CRITICAL CONTRACT (Prompt_1_Master §P):
- the view must NEVER show fake placeholder data in normal mode
- if a data source is unavailable, show explicit "unavailable" state
- never fabricate values

In legacy contexts this view is wired through `hologram_adapter`;
new docs/comments refer to it as Reality View or Cognitive Matrix.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


REALITY_VIEW_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RealityPanel:
    panel_id: str
    title: str
    available: bool
    items: tuple[dict, ...]
    rationale_if_unavailable: str = ""

    def to_dict(self) -> dict:
        return {
            "panel_id": self.panel_id,
            "title": self.title,
            "available": self.available,
            "items": list(self.items),
            "rationale_if_unavailable": self.rationale_if_unavailable,
        }


@dataclass(frozen=True)
class RealitySnapshot:
    schema_version: int
    produced_at_iso: str
    panels: tuple[RealityPanel, ...]
    branch_name: str
    base_commit_hash: str
    pinned_input_manifest_sha256: str

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "produced_at_iso": self.produced_at_iso,
            "panels": [p.to_dict() for p in self.panels],
            "provenance": {
                "branch_name": self.branch_name,
                "base_commit_hash": self.base_commit_hash,
                "pinned_input_manifest_sha256":
                    self.pinned_input_manifest_sha256,
            },
        }


# Standard panel ids (Prompt_1_Master §P "must show real, current,
# state-backed information from actual artifacts where available")
PANELS = (
    "mission_queue_top",
    "attention_focus",
    "top_tensions",
    "top_blind_spots",
    "self_model_summary",
    "world_model_summary",
    "dream_queue",
    "meta_proposal_queue",
    "vector_tier_heat",
    "cell_topology",
    "builder_lane_status",
)


def _unavailable_panel(panel_id: str, title: str,
                            reason: str) -> RealityPanel:
    return RealityPanel(
        panel_id=panel_id, title=title, available=False,
        items=(), rationale_if_unavailable=reason,
    )


def build_panels_from_state(*,
                                  mission_queue: list | None = None,
                                  self_model: dict | None = None,
                                  world_model_snapshot: dict | None = None,
                                  dream_curriculum: dict | None = None,
                                  hive_review_bundle: dict | None = None,
                                  vector_graph: dict | None = None,
                                  ) -> tuple[RealityPanel, ...]:
    """Construct panels from real artifact data. Each input is
    OPTIONAL; missing inputs yield an explicit unavailable panel.

    NEVER fabricate values."""
    panels: list[RealityPanel] = []

    # mission_queue_top
    if mission_queue:
        items = []
        for m in sorted(mission_queue,
                          key=lambda x: -float(x.get("priority", 0))
                                          if isinstance(x, dict) else 0)[:5]:
            if isinstance(m, dict):
                items.append({
                    "mission_id": m.get("mission_id"),
                    "kind": m.get("kind"),
                    "lane": m.get("lane"),
                    "priority": m.get("priority"),
                    "lifecycle_status": m.get("lifecycle_status"),
                })
        panels.append(RealityPanel(
            panel_id="mission_queue_top",
            title="Mission queue (top 5 by priority)",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "mission_queue_top", "Mission queue (top 5 by priority)",
            "no mission_queue data supplied to view builder",
        ))

    # attention_focus from self_model.attention_focus
    if self_model and self_model.get("attention_focus"):
        items = [
            {"curiosity_id": a.get("curiosity_id"),
             "candidate_cell": a.get("candidate_cell"),
             "estimated_value": a.get("estimated_value"),
             "rank": a.get("rank")}
            for a in (self_model.get("attention_focus") or [])[:5]
        ]
        panels.append(RealityPanel(
            panel_id="attention_focus",
            title="Attention focus (top 5)",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "attention_focus", "Attention focus (top 5)",
            "self_model snapshot or attention_focus[] missing",
        ))

    # top_tensions
    if self_model and self_model.get("workspace_tensions"):
        items = [
            {"tension_id": t.get("tension_id"),
             "type": t.get("type"),
             "severity": t.get("severity"),
             "lifecycle_status": t.get("lifecycle_status")}
            for t in (self_model.get("workspace_tensions") or [])[:5]
        ]
        panels.append(RealityPanel(
            panel_id="top_tensions",
            title="Top tensions",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "top_tensions", "Top tensions",
            "self_model.workspace_tensions[] missing",
        ))

    # top_blind_spots
    if self_model and self_model.get("blind_spots"):
        items = [
            {"domain": b.get("domain"), "severity": b.get("severity"),
             "detectors": list(b.get("detectors") or ())}
            for b in (self_model.get("blind_spots") or [])[:5]
        ]
        panels.append(RealityPanel(
            panel_id="top_blind_spots",
            title="Top blind spots",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "top_blind_spots", "Top blind spots",
            "self_model.blind_spots[] missing",
        ))

    # self_model_summary
    if self_model:
        sm_summary = {
            "schema_version": self_model.get("schema_version"),
            "snapshot_id": self_model.get("snapshot_id"),
            "cells": len(self_model.get("cells") or []),
            "tensions": len(self_model.get("workspace_tensions") or []),
            "blind_spots": len(self_model.get("blind_spots") or []),
            "real_data_coverage_ratio":
                self_model.get("real_data_coverage_ratio"),
        }
        panels.append(RealityPanel(
            panel_id="self_model_summary",
            title="Self-model summary",
            available=True, items=(sm_summary,),
        ))
    else:
        panels.append(_unavailable_panel(
            "self_model_summary", "Self-model summary",
            "self_model snapshot missing",
        ))

    # world_model_summary
    if world_model_snapshot:
        wm_summary = {
            "snapshot_id": world_model_snapshot.get("snapshot_id"),
            "facts": len(world_model_snapshot.get("external_facts") or []),
            "causes": len(world_model_snapshot.get("causal_relations") or []),
            "predictions":
                len(world_model_snapshot.get("predictions") or []),
            "uncertainty_summary":
                world_model_snapshot.get("uncertainty_summary") or {},
        }
        panels.append(RealityPanel(
            panel_id="world_model_summary",
            title="World-model summary",
            available=True, items=(wm_summary,),
        ))
    else:
        panels.append(_unavailable_panel(
            "world_model_summary", "World-model summary",
            "world_model snapshot missing",
        ))

    # dream_queue
    if dream_curriculum:
        items = []
        for n in (dream_curriculum.get("nights") or [])[:5]:
            tgt = (n.get("target_items") or [])
            items.append({
                "night_index": n.get("night_index"),
                "mode": n.get("mode"),
                "uncertainty": n.get("uncertainty"),
                "target_count": len(tgt),
            })
        panels.append(RealityPanel(
            panel_id="dream_queue", title="Dream queue (top 5 nights)",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "dream_queue", "Dream queue (top 5 nights)",
            "dream_curriculum missing",
        ))

    # meta_proposal_queue
    if hive_review_bundle:
        proposals = (hive_review_bundle.get("proposals") or [])[:5]
        items = [
            {"meta_proposal_id": p.get("meta_proposal_id"),
             "proposal_type": p.get("proposal_type"),
             "proposal_priority": p.get("proposal_priority"),
             "recommended_next_human_action":
                 p.get("recommended_next_human_action")}
            for p in proposals
        ]
        panels.append(RealityPanel(
            panel_id="meta_proposal_queue",
            title="Meta-proposal review queue (top 5)",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "meta_proposal_queue",
            "Meta-proposal review queue (top 5)",
            "hive review_bundle missing",
        ))

    # vector_tier_heat
    if vector_graph:
        nodes = vector_graph.get("nodes") or {}
        per_anchor: dict[str, int] = {}
        for n in nodes.values():
            per_anchor[n.get("anchor_status", "unknown")] = (
                per_anchor.get(n.get("anchor_status", "unknown"), 0) + 1
            )
        panels.append(RealityPanel(
            panel_id="vector_tier_heat",
            title="Vector graph heat (by anchor status)",
            available=True,
            items=({"counts": dict(sorted(per_anchor.items())),
                    "total_nodes": len(nodes)},),
        ))
    else:
        panels.append(_unavailable_panel(
            "vector_tier_heat", "Vector graph heat (by anchor status)",
            "vector_graph snapshot missing",
        ))

    # cell_topology
    if self_model and self_model.get("cells"):
        items = [
            {"cell_id": c.get("cell_id"),
             "scorecard": c.get("scorecard"),
             "solver_count": c.get("solver_count")}
            for c in (self_model.get("cells") or [])[:8]
        ]
        panels.append(RealityPanel(
            panel_id="cell_topology", title="Cell topology",
            available=True, items=tuple(items),
        ))
    else:
        panels.append(_unavailable_panel(
            "cell_topology", "Cell topology",
            "self_model.cells[] missing",
        ))

    # builder_lane_status (optional; placeholder for Phase U2)
    panels.append(_unavailable_panel(
        "builder_lane_status", "Builder lane status",
        "builder_lane status not yet wired (Phase U2)",
    ))

    return tuple(panels)


def make_reality_snapshot(*,
                                produced_at_iso: str,
                                panels: tuple[RealityPanel, ...],
                                branch_name: str,
                                base_commit_hash: str,
                                pinned_input_manifest_sha256: str,
                                ) -> RealitySnapshot:
    return RealitySnapshot(
        schema_version=REALITY_VIEW_SCHEMA_VERSION,
        produced_at_iso=produced_at_iso,
        panels=tuple(panels),
        branch_name=branch_name,
        base_commit_hash=base_commit_hash,
        pinned_input_manifest_sha256=pinned_input_manifest_sha256,
    )
