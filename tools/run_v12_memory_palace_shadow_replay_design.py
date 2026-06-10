# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only V12 Memory Palace shortcut shadow-replay design (S6).

The runtime-promotion design
(tools/run_v12_memory_palace_shortcut_runtime_promotion_design.py) lists
``shadow_replay_before_runtime_route`` as a REQUIRED operator control but
does not specify what that shadow replay concretely is. This fixture
specifies it -- design-only.

For each verified runtime-promotion design row it emits a shadow-replay
plan: the two routes to compare (the incumbent full-hierarchy path vs the
candidate shortcut path), the agreement criterion (both must resolve to
the same target node), the pass/fail thresholds the shadow replay must
clear before an operator may even consider a runtime route change, and
what evidence the replay would record. It executes NOTHING: no route
change, no solver call, no storage/bridge write, no scheduler enqueue, no
gate skip, no promotion, no network. Every authority field is emitted as
a literal false and the design requires a separate operator gate.

The source runtime-promotion design is consumed read-only and must be
``ok`` with a clean authority boundary; otherwise the shadow-replay
design refuses (fail-closed) rather than designing off unverified rows.
Deterministic, offline; digest-bound; advisory only, never a runtime or
promotion trigger.

Exit codes: 0 ok, 1 design not ok (no designable source rows / blockers).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (  # noqa: E402
    REPORT_VERSION as RUNTIME_DESIGN_REPORT_VERSION,
    build_memory_palace_shortcut_runtime_promotion_design,
)

REPORT_VERSION = "wd.v12.memory_palace_shadow_replay_design.v0"
CLAIM_LABEL = "DESIGN_ONLY_OPERATOR_GATED_SHADOW_REPLAY"

# Authority fields, every one a literal False on every artifact (strict
# identity checks downstream; mirrors the runtime-promotion design boundary).
_AUTHORITY_FALSE_FIELDS = (
    "runtime_route_changed",
    "shadow_replay_executed",
    "solver_call_performed",
    "storage_write_performed",
    "bridge_append_performed",
    "scheduler_enqueue_performed",
    "gate_skip_performed",
    "promotion_performed",
    "promotion_action_allowed",
    "network_access_performed",
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
)

# Concrete pass criteria a real shadow replay must clear before an operator
# may consider a runtime route change (design-only constants).
_REPLAY_PASS_CRITERIA = (
    "shortcut_route_resolves_to_same_target_node",
    "no_intermediate_required_room_skipped_unsafely",
    "shortcut_hop_count_strictly_less_than_incumbent",
    "placement_and_shortcut_confidence_meet_source_thresholds",
)
_REQUIRED_OPERATOR_CONTROLS = (
    "verified_runtime_promotion_design",
    "operator_authorization",
    "shadow_replay_pass_on_all_criteria",
    "rollback_plan",
    "post_replay_verification_artifact",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a read-only, design-only shadow-replay plan for each "
            "verified Memory Palace runtime-promotion design row."
        ),
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--out", type=Path, default=None, help="Also write JSON here.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    now = _parse_utc(args.now) if args.now else None
    if args.now is not None and now is None:
        print(f"--now is not a valid ISO-8601 instant: {args.now!r}", file=sys.stderr)
        return 1
    report = build_memory_palace_shadow_replay_design(now_utc=now)
    payload = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.out is not None:
        args.out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_memory_palace_shadow_replay_design(
    *,
    now_utc: datetime | None = None,
    runtime_design: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a design-only shadow-replay plan derived from the runtime design.

    Fail-closed: if the source runtime-promotion design is not ``ok``, has
    the wrong version, or carries a non-clean authority boundary, no
    replay rows are emitted and a blocker is recorded.
    """
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    source = (
        build_memory_palace_shortcut_runtime_promotion_design(now_utc=generated_at)
        if runtime_design is None
        else runtime_design
    )

    blockers: list[str] = []
    if str(source.get("report_version", "")) != RUNTIME_DESIGN_REPORT_VERSION:
        blockers.append("source_runtime_design_version_mismatch")
    if source.get("ok") is not True:
        blockers.append("source_runtime_design_not_ok")
    if not _source_authority_boundary_clean(source):
        blockers.append("source_authority_boundary_not_clean")

    design_rows = list(source.get("runtime_promotion_designs") or [])
    replays: list[dict[str, Any]] = []
    if not blockers:
        for row in design_rows:
            if not isinstance(row, Mapping):
                blockers.append("source_design_row_not_mapping")
                continue
            replays.append(_replay_row(row))

    if not replays and not blockers:
        blockers.append("no_designable_runtime_promotion_rows")

    ok = not blockers and all(_replay_row_boundary_clean(r) for r in replays)

    core: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _utc_text(generated_at),
        "claim_label": CLAIM_LABEL,
        "advisory_only": True,
        "read_only": True,
        "ok": ok,
        "blockers": sorted(set(blockers)),
        "source_report_version": str(source.get("report_version", "")),
        "source_ok": source.get("ok") is True,
        "memory_id": str(source.get("memory_id", "")),
        "design_summary": {
            "source_design_count": len(design_rows),
            "shadow_replay_design_count": len(replays),
            "top_replay_target": (
                replays[0]["target_node_id"] if replays else "none"
            ),
        },
        "shadow_replay_designs": replays,
        "replay_pass_criteria": list(_REPLAY_PASS_CRITERIA),
        "required_operator_controls": list(_REQUIRED_OPERATOR_CONTROLS),
        "no_overclaim_guardrails": {
            "design_only": True,
            "shadow_replay_not_executed": True,
            "operator_gate_required_before_runtime_route": True,
            "not_router_dispatch": True,
            "not_solver_call": True,
            "not_storage_write": True,
            "not_bridge_append": True,
            "not_scheduler_enqueue": True,
            "not_gate_skip": True,
            "not_promotion_action": True,
            "not_networked_retrieval": True,
            "source_verification_required": True,
            "deterministic_local_fixture": True,
        },
        "operator_interpretation": (
            "These rows specify the shadow_replay_before_runtime_route control "
            "that the runtime-promotion design requires. They design a "
            "read-only route comparison to run before any operator-gated "
            "runtime promotion; they execute no replay and change no route."
        ),
    }
    for field in _AUTHORITY_FALSE_FIELDS:
        core[field] = False
    return {**core, "canonical_digest": sha256_digest(core)}


def _replay_row(row: Mapping[str, Any]) -> dict[str, Any]:
    source_node = str(row.get("source_node_id", ""))
    target_node = str(row.get("target_node_id", ""))
    hierarchy_hops = _int(row.get("hierarchy_hops"))
    shortcut_hops = _int(row.get("projected_shortcut_hops"))
    design_id = str(row.get("design_id", ""))
    replay: dict[str, Any] = {
        "shadow_replay_id": "shadow_replay." + sha256_digest(
            {"design_id": design_id, "source": source_node, "target": target_node}
        )[7:23],
        "source_design_id": design_id,
        "shortcut_id": str(row.get("shortcut_id", "")),
        "memory_id": str(row.get("memory_id", "")),
        "source_node_id": source_node,
        "target_node_id": target_node,
        # The two routes a real shadow replay would compare, read-only.
        "incumbent_route": {
            "kind": "full_hierarchy_path",
            "hop_count": hierarchy_hops,
        },
        "candidate_route": {
            "kind": "shortcut_path",
            "hop_count": shortcut_hops,
            "intermediate_hops_skipped": _int(row.get("intermediate_hops_skipped")),
        },
        "agreement_criterion": "both_routes_resolve_to_same_target_node",
        "hop_reduction": max(0, hierarchy_hops - shortcut_hops),
        "pass_criteria": list(_REPLAY_PASS_CRITERIA),
        "replay_status": "design_only_not_executed",
        "manual_review_required": True,
        "operator_gate_required": True,
    }
    for field in _AUTHORITY_FALSE_FIELDS:
        replay[field] = False
    return replay


def _replay_row_boundary_clean(row: Mapping[str, Any]) -> bool:
    return all(row.get(field) is False for field in _AUTHORITY_FALSE_FIELDS)


def _source_authority_boundary_clean(source: Mapping[str, Any]) -> bool:
    boundary = source.get("authority_boundary")
    if not isinstance(boundary, Mapping):
        return False
    # Every *_performed / *_allowed / *_changed / *_granted / *_made flag false.
    for key, value in boundary.items():
        if key.endswith(
            ("_performed", "_allowed", "_changed", "_granted", "_made")
        ) and value is not False:
            return False
    return True


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["design_summary"]
    lines = [
        "# V12 Memory Palace Shortcut Shadow-Replay Design",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- source_report_version: `{report['source_report_version']}`",
        f"- memory_id: `{report['memory_id']}`",
        "",
        "## Summary",
        "",
        f"- source_design_count: `{summary['source_design_count']}`",
        f"- shadow_replay_design_count: `{summary['shadow_replay_design_count']}`",
        f"- top_replay_target: `{summary['top_replay_target']}`",
        "",
        "## Shadow-Replay Rows",
        "",
        "| target_node_id | incumbent_hops | candidate_hops | hop_reduction | status |",
        "|---|---|---|---|---|",
    ]
    for row in report["shadow_replay_designs"]:
        lines.append(
            f"| `{row['target_node_id']}` | "
            f"`{row['incumbent_route']['hop_count']}` | "
            f"`{row['candidate_route']['hop_count']}` | "
            f"`{row['hop_reduction']}` | `{row['replay_status']}` |"
        )
    lines.extend(
        [
            "",
            "Design-only: specifies the shadow_replay_before_runtime_route "
            "control the runtime-promotion design requires. Executes no "
            "replay, changes no route, grants no authority.",
            "",
        ]
    )
    if report["blockers"]:
        lines.append("## Blockers")
        lines.append("")
        for blocker in report["blockers"]:
            lines.append(f"- `{blocker}`")
        lines.append("")
    return "\n".join(lines)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_utc(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
