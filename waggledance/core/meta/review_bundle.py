# SPDX-License-Identifier: BUSL-1.1
"""Review bundle + handoff artifact emission — Phase 8.5 Session D,
deliverables D7 + D8.

Emits:
- hive_proposals.{json,md}
- meta_evidence_map.json
- review_bundle.{json,md}

CRITICAL HUMAN-IN-THE-LOOP RULE (D.txt §D8):
Both the JSON and MD bundle artifacts include the boundary text
HUMAN_REVIEW_BOUNDARY_TEXT in their preamble. This is the explicit
shadow-only / no-runtime-promotion contract.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import (
    HUMAN_REVIEW_BOUNDARY_TEXT,
    META_SCHEMA_VERSION,
    RECOMMENDED_NEXT_HUMAN_ACTIONS,
)
from .meta_learner import EvidenceItem, MetaProposal, proposal_to_dict


# ── Recommended-next-human-action classification ────────────────-

def recommend_action_for(p: MetaProposal) -> str:
    """Decide which of the four allowed actions a human reviewer
    should take next for this proposal. Deterministic from priority
    + confidence + risk + scope_class."""
    if p.confidence >= 0.6 and p.proposal_priority >= 0.05 \
            and p.scope_class in ("topology", "solver_library", "policy"):
        return "post_campaign_runtime_review_candidate"
    if p.confidence >= 0.4 and p.proposal_priority >= 0.02:
        return "review_for_future_PR"
    if p.confidence < 0.30 and p.proposal_priority < 0.01:
        return "archive_as_low_value"
    return "wait_for_more_evidence"


# ── hive_proposals.json + .md ────────────────────────────────────

def emit_hive_proposals(
    proposals: list[MetaProposal],
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    out_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": META_SCHEMA_VERSION,
        "human_review_boundary": HUMAN_REVIEW_BOUNDARY_TEXT,
        "provenance": {
            "branch_name": branch_name,
            "base_commit_hash": base_commit_hash,
            "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
        },
        "proposals": [proposal_to_dict(p) for p in proposals],
    }
    json_path = out_dir / "hive_proposals.json"
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path = out_dir / "hive_proposals.md"
    md_path.write_text(_render_proposals_md(payload), encoding="utf-8")
    return {"hive_json": json_path, "hive_md": md_path}


def _render_proposals_md(payload: dict) -> str:
    lines = [
        "# Hive proposals",
        "",
        f"> {payload['human_review_boundary']}",
        "",
        f"- **Branch:** `{payload['provenance']['branch_name']}`",
        f"- **Base commit:** `{payload['provenance']['base_commit_hash']}`",
        f"- **Pin manifest:** `{payload['provenance']['pinned_input_manifest_sha256']}`",
        f"- **Proposals:** {len(payload['proposals'])}",
        "",
        "| id | type | scope | priority | confidence | cells | lifecycle |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in payload["proposals"]:
        lines.append(
            f"| `{p['meta_proposal_id']}` | `{p['proposal_type']}` | "
            f"`{p['scope_class']}` | {p['proposal_priority']:.4f} | "
            f"{p['confidence']:.2f} | {','.join(p['impacted_cells']) or '—'} | "
            f"`{p['lifecycle_status']}` |"
        )
    lines.append("")
    return "\n".join(lines)


# ── meta_evidence_map.json ───────────────────────────────────────

def emit_meta_evidence_map(
    items_by_target: dict[str, list[EvidenceItem]],
    proposals: list[MetaProposal],
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    out_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    proposal_index = {p.canonical_target: p.meta_proposal_id for p in proposals}
    payload = {
        "schema_version": META_SCHEMA_VERSION,
        "human_review_boundary": HUMAN_REVIEW_BOUNDARY_TEXT,
        "provenance": {
            "branch_name": branch_name,
            "base_commit_hash": base_commit_hash,
            "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
        },
        "evidence_by_target": {
            target: {
                "proposal_id": proposal_index.get(target),
                "items": [
                    {
                        "plane": it.plane,
                        "source_id": it.source_id,
                        "cell_id": it.cell_id,
                        "severity": it.severity,
                        "rationale": it.rationale,
                    }
                    for it in sorted(its, key=lambda x: (x.plane, x.source_id))
                ],
            }
            for target, its in sorted(items_by_target.items())
        },
    }
    path = out_dir / "meta_evidence_map.json"
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


# ── review_bundle.{json,md} ──────────────────────────────────────-

def build_review_bundle(
    proposals: list[MetaProposal],
    insufficient_evidence: list[dict],
    rejected_candidates: list[dict],
    resolved_proposal_ids: list[str],
    branch_name: str,
    base_commit_hash: str,
    pinned_input_manifest_sha256: str,
    consumed_hook_contracts: list[dict],
    fixture_fallback_used: dict[str, Any],
) -> dict:
    counts: dict[str, int] = {a: 0 for a in RECOMMENDED_NEXT_HUMAN_ACTIONS}
    proposal_blocks: list[dict] = []
    for p in proposals:
        action = recommend_action_for(p)
        counts[action] = counts.get(action, 0) + 1
        proposal_blocks.append({
            "meta_proposal_id": p.meta_proposal_id,
            "proposal_type": p.proposal_type,
            "scope_class": p.scope_class,
            "impacted_cells": list(p.impacted_cells),
            "proposal_priority": p.proposal_priority,
            "confidence": p.confidence,
            "evidence_planes": list(p.evidence_planes),
            "why_now": p.why_now,
            "why_human_review_required": p.why_human_review_required,
            "recommended_next_human_action": action,
            "lifecycle_status": p.lifecycle_status,
        })
    return {
        "schema_version": META_SCHEMA_VERSION,
        "human_review_boundary": HUMAN_REVIEW_BOUNDARY_TEXT,
        "provenance": {
            "branch_name": branch_name,
            "base_commit_hash": base_commit_hash,
            "pinned_input_manifest_sha256": pinned_input_manifest_sha256,
        },
        "consumed_hook_contracts": list(consumed_hook_contracts),
        "summary_text": (
            f"{len(proposals)} bounded self-proposals; "
            f"{len(insufficient_evidence)} insufficient-evidence "
            f"candidates; {len(rejected_candidates)} rejected; "
            f"{len(resolved_proposal_ids)} resolved since last run."
        ),
        "proposals": proposal_blocks,
        "insufficient_evidence": list(insufficient_evidence),
        "rejected_candidates": list(rejected_candidates),
        "counts_by_recommended_next_human_action": counts,
        "why_human_review_required": (
            "Session D code is a recommender, never an actor. Every "
            "proposal here is structurally suggestive evidence; the "
            "merge / apply decision rests with a human reviewer who "
            "can weigh schedule, downstream effects, and risk."
        ),
        "why_no_runtime_mutation_occurred": (
            "Session D's allowed touch surface excludes runtime "
            "registries, axiom YAML, FAISS roots, and port 8002. No "
            "code path in waggledance/core/meta/* writes to those "
            "locations. Runtime flip is out of scope until a later "
            "gated session."
        ),
        "fixture_fallback_used": dict(fixture_fallback_used),
        "resolved_proposals": [
            {"meta_proposal_id": mid, "resolution_reason": "unknown"}
            for mid in resolved_proposal_ids
        ],
    }


def emit_review_bundle(bundle: dict, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "review_bundle.json"
    json_path.write_text(
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    md_path = out_dir / "review_bundle.md"
    md_path.write_text(_render_bundle_md(bundle), encoding="utf-8")
    return {"bundle_json": json_path, "bundle_md": md_path}


def _render_bundle_md(b: dict) -> str:
    lines = [
        "# Review bundle (shadow-only)",
        "",
        f"> {b['human_review_boundary']}",
        "",
        f"- **Branch:** `{b['provenance']['branch_name']}`",
        f"- **Base commit:** `{b['provenance']['base_commit_hash']}`",
        f"- **Pin manifest:** `{b['provenance']['pinned_input_manifest_sha256']}`",
        "",
        "## Summary",
        "",
        b["summary_text"],
        "",
        "## Counts by recommended next human action",
        "",
    ]
    for action in sorted(b["counts_by_recommended_next_human_action"]):
        n = b["counts_by_recommended_next_human_action"][action]
        lines.append(f"- `{action}`: {n}")
    lines.extend(["", "## Proposals", "",
                   "| id | type | priority | confidence | "
                   "next_human_action | lifecycle |",
                   "|---|---|---|---|---|---|"])
    for p in b["proposals"]:
        lines.append(
            f"| `{p['meta_proposal_id']}` | `{p['proposal_type']}` | "
            f"{p['proposal_priority']:.4f} | {p['confidence']:.2f} | "
            f"`{p['recommended_next_human_action']}` | "
            f"`{p['lifecycle_status']}` |"
        )
    if b["insufficient_evidence"]:
        lines.extend(["", "## Insufficient evidence", ""])
        for e in b["insufficient_evidence"]:
            lines.append(
                f"- `{e['candidate_target']}` (strength="
                f"{e['evidence_strength_seen']:.2f}, missing="
                f"{','.join(e['missing_planes'])})"
            )
    if b["rejected_candidates"]:
        lines.extend(["", "## Rejected candidates", ""])
        for r in b["rejected_candidates"]:
            lines.append(f"- `{r['candidate_target']}`: {r['rejection_reason']}")
    lines.extend([
        "",
        "## Why human review is required",
        "",
        f"> {b['why_human_review_required']}",
        "",
        "## Why no runtime mutation occurred",
        "",
        f"> {b['why_no_runtime_mutation_occurred']}",
        "",
    ])
    return "\n".join(lines)
