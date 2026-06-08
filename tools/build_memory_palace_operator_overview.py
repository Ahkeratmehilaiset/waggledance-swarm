# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only Memory Palace operator overview.

The overview composes the existing hierarchy-map summary and read-path preview
surfaces into a compact operator view. It consumes an already-built Memory
Palace projection and one or more memory ids. It does not mutate memory,
dispatch runtime routes, call solvers, enqueue schedulers, append bridge
events, access the network, promote shortcuts, or grant gate authority.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_memory_palace_hierarchy_map_summary import (  # noqa: E402
    SUMMARY_VERSION,
    build_memory_palace_hierarchy_map_summary,
)
from tools.build_memory_palace_read_path_preview import (  # noqa: E402
    PREVIEW_VERSION,
    build_memory_palace_read_path_preview,
)
from waggledance.core.memory_palace import (  # noqa: E402
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
)


OVERVIEW_VERSION = "wd.v12.memory_palace_operator_overview.v0"
CLAIM_LABEL = "READ_ONLY_MEMORY_PALACE_OPERATOR_OVERVIEW"


class DuplicateKeyError(ValueError):
    """Raised when a JSON object repeats a key."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--projection-json",
        required=True,
        type=Path,
        help="Path to an existing Memory Palace projection JSON file.",
    )
    parser.add_argument(
        "--memory-id",
        action="append",
        dest="memory_ids",
        required=True,
        help="Memory id to include. Repeat for multiple operator rows.",
    )
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--min-rank-score", type=float, default=0.0)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        projection = _load_json(args.projection_json)
    except ValueError as exc:
        report = _failure_overview(str(exc))
    else:
        report = build_memory_palace_operator_overview(
            projection,
            args.memory_ids,
            max_candidates=args.max_candidates,
            min_rank_score=args.min_rank_score,
        )

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    else:
        if report["ok"]:
            print(render_markdown(report), end="")
        else:
            print(encoded)
            print(
                "Memory Palace operator overview FAILED: "
                + ", ".join(report["blockers"]),
                file=sys.stderr,
            )
    return 0 if report["ok"] else 1


def build_memory_palace_operator_overview(
    projection: Any,
    memory_ids: Sequence[str],
    *,
    max_candidates: int = 3,
    min_rank_score: float = 0.0,
) -> dict[str, Any]:
    """Return a deterministic, authority-free operator overview."""

    normalized_memory_ids = _normalize_memory_ids(memory_ids)
    if normalized_memory_ids is None:
        return _failure_overview("memory_ids_not_non_empty_string_sequence")
    if len(set(normalized_memory_ids)) != len(normalized_memory_ids):
        return _failure_overview("memory_ids_not_unique")

    previews: list[Mapping[str, Any]] = []
    blockers: list[str] = []
    for index, memory_id in enumerate(normalized_memory_ids, start=1):
        preview = build_memory_palace_read_path_preview(
            projection,
            memory_id,
            max_candidates=max_candidates,
            min_rank_score=min_rank_score,
        )
        if not preview.get("ok"):
            blockers.extend(_component_blockers("read_path_preview", index, preview))
        else:
            previews.append(preview)
    if blockers:
        return _failure_overview(*blockers)

    hierarchy = build_memory_palace_hierarchy_map_summary(projection)
    if not hierarchy.get("ok"):
        return _failure_overview(
            "hierarchy_map_summary_failed",
            *_component_blockers("hierarchy_map_summary", 0, hierarchy),
        )

    read_paths = [_read_path_row(preview) for preview in previews]
    report = {
        "overview_version": OVERVIEW_VERSION,
        "ok": True,
        "claim_label": CLAIM_LABEL,
        "component_versions": {
            "hierarchy_map_summary": SUMMARY_VERSION,
            "read_path_preview": PREVIEW_VERSION,
        },
        "source_projection_schema_version": MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
        "source_of_truth": "projection_only",
        "memory_ids": normalized_memory_ids,
        "hierarchy": _hierarchy_row(hierarchy),
        "read_path_overview": read_paths,
        "aggregate": _aggregate(read_paths, hierarchy),
        "authority_boundary": _authority_boundary(hierarchy, previews),
        "no_overclaim_guardrails": {
            "not_router_dispatch": True,
            "not_solver_call": True,
            "not_storage_write": True,
            "not_bridge_append": True,
            "not_scheduler_enqueue": True,
            "not_promotion_authority": True,
            "not_gate_skip": True,
            "not_networked_retrieval": True,
            "not_production_memory_migration": True,
            "projection_reader_only": True,
        },
        "blockers": [],
        "operator_interpretation": (
            "This overview helps a human or agent scan Memory Palace wings, "
            "coverage, and distant read-path shortcuts from an existing "
            "projection. It is not a runtime route, solver dispatch, storage "
            "mutation, bridge append, scheduler enqueue, promotion, or gate "
            "decision."
        ),
    }
    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    hierarchy = _mapping(report.get("hierarchy"))
    aggregate = _mapping(report.get("aggregate"))
    lines = [
        "# Memory Palace Operator Overview",
        "",
        f"- overview_version: `{report['overview_version']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- source_of_truth: `{report['source_of_truth']}`",
        "",
        "## Hierarchy",
        "",
        f"- node_count: `{hierarchy.get('node_count', 0)}`",
        f"- root_count: `{hierarchy.get('root_count', 0)}`",
        f"- max_depth: `{hierarchy.get('max_depth', 0)}`",
        (
            "- placement_count: `"
            + str(_mapping(hierarchy.get("coverage")).get("placement_count", 0))
            + "`"
        ),
        (
            "- shortcut_hint_count: `"
            + str(_mapping(hierarchy.get("coverage")).get("shortcut_hint_count", 0))
            + "`"
        ),
        "",
        "## Read Paths",
        "",
        (
            "| memory | source | candidates | top target | score | "
            "skipped intermediate hops |"
        ),
        "| --- | --- | ---: | --- | ---: | ---: |",
    ]
    for row in _sequence(report.get("read_path_overview")) or ():
        row_map = _mapping(row)
        lines.append(
            "| `{memory}` | `{source}` | {candidates} | `{target}` | "
            "{score} | {skipped} |".format(
                memory=row_map.get("memory_id", ""),
                source=row_map.get("source_node_id", ""),
                candidates=row_map.get("candidate_count", 0),
                target=row_map.get("top_target_node_id", ""),
                score=row_map.get("top_rank_score", 0.0),
                skipped=row_map.get("max_intermediate_hops_skipped", 0),
            )
        )

    lines.extend(
        [
            "",
            "## Aggregate",
            "",
            f"- memory_count: `{aggregate.get('memory_count', 0)}`",
            f"- total_candidate_count: `{aggregate.get('total_candidate_count', 0)}`",
            (
                "- unique_source_node_ids: `"
                + ", ".join(_string_sequence(aggregate.get("unique_source_node_ids")))
                + "`"
            ),
            (
                "- unique_target_node_ids: `"
                + ", ".join(_string_sequence(aggregate.get("unique_target_node_ids")))
                + "`"
            ),
            "",
            "## Boundary",
            "",
        ]
    )
    for key, value in sorted(_mapping(report.get("authority_boundary")).items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(["", report["operator_interpretation"], ""])
    return "\n".join(lines)


def _read_path_row(preview: Mapping[str, Any]) -> dict[str, Any]:
    summary = _mapping(preview.get("summary"))
    source = _mapping(preview.get("source"))
    targets = [_target_row(target) for target in _sequence(preview.get("ranked_read_paths")) or ()]
    return {
        "memory_id": _string_field(preview, "memory_id"),
        "source_node_id": _string_field(source, "node_id"),
        "source_path_node_ids": _string_sequence(source.get("path_node_ids")),
        "source_path_labels": _string_sequence(source.get("path_labels")),
        "candidate_count": int(summary.get("candidate_count", 0)),
        "top_target_node_id": _string_field(summary, "top_target_node_id"),
        "top_rank_score": _floatish(summary.get("top_rank_score")),
        "max_hierarchy_hops": max(
            (int(target.get("hierarchy_hops", 0)) for target in targets),
            default=0,
        ),
        "max_intermediate_hops_skipped": max(
            (int(target.get("intermediate_hops_skipped", 0)) for target in targets),
            default=0,
        ),
        "ranked_targets": targets,
        "authority_boundary": {
            "runtime_route_changed": False,
            "storage_write_performed": False,
            "bridge_append_performed": False,
            "solver_call_performed": False,
            "scheduler_enqueue_performed": False,
            "promotion_performed": False,
            "gate_skip_performed": False,
            "network_access_performed": False,
            "memory_payload_included": False,
            "matched_values_included": False,
            "local_paths_recorded": False,
        },
    }


def _target_row(candidate: Mapping[str, Any]) -> dict[str, Any]:
    target = _mapping(candidate.get("target"))
    return {
        "rank": _intish(candidate.get("rank")),
        "shortcut_id": _string_field(candidate, "shortcut_id"),
        "target_node_id": _string_field(candidate, "target_node_id"),
        "target_path_node_ids": _string_sequence(target.get("path_node_ids")),
        "target_path_labels": _string_sequence(target.get("path_labels")),
        "rank_score": _floatish(candidate.get("rank_score")),
        "placement_confidence": _floatish(candidate.get("placement_confidence")),
        "shortcut_confidence": _floatish(candidate.get("shortcut_confidence")),
        "hierarchy_hops": _intish(candidate.get("hierarchy_hops")),
        "projected_shortcut_hops": _intish(candidate.get("projected_shortcut_hops")),
        "intermediate_hops_skipped": _intish(
            candidate.get("intermediate_hops_skipped")
        ),
        "matched_selector_keys": _string_sequence(candidate.get("matched_selector_keys")),
        "matched_value_count_by_key": {
            str(key): _intish(value)
            for key, value in sorted(
                _mapping(candidate.get("matched_value_count_by_key")).items(),
                key=lambda item: str(item[0]),
            )
        },
    }


def _hierarchy_row(hierarchy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_count": _intish(hierarchy.get("node_count")),
        "root_count": _intish(hierarchy.get("root_count")),
        "max_depth": _intish(hierarchy.get("max_depth")),
        "kind_counts": {
            str(key): _intish(value)
            for key, value in sorted(
                _mapping(hierarchy.get("kind_counts")).items(),
                key=lambda item: str(item[0]),
            )
        },
        "roots": [
            _root_row(root) for root in _sequence(hierarchy.get("roots")) or ()
        ],
        "coverage": {
            "placement_count": _intish(
                _mapping(hierarchy.get("coverage")).get("placement_count")
            ),
            "shortcut_hint_count": _intish(
                _mapping(hierarchy.get("coverage")).get("shortcut_hint_count")
            ),
            "long_shortcut_hint_count": _intish(
                _mapping(hierarchy.get("coverage")).get("long_shortcut_hint_count")
            ),
            "node_ids_with_placements": _string_sequence(
                _mapping(hierarchy.get("coverage")).get("node_ids_with_placements")
            ),
            "node_ids_with_shortcuts": _string_sequence(
                _mapping(hierarchy.get("coverage")).get("node_ids_with_shortcuts")
            ),
        },
    }


def _root_row(root: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": _string_field(root, "node_id"),
        "kind": _string_field(root, "kind"),
        "label": _string_field(root, "label"),
        "child_count": _intish(root.get("child_count")),
        "descendant_count": _intish(root.get("descendant_count")),
        "placement_count": _intish(root.get("placement_count")),
        "shortcut_source_count": _intish(root.get("shortcut_source_count")),
        "shortcut_target_count": _intish(root.get("shortcut_target_count")),
        "direct_child_node_ids": _string_sequence(root.get("direct_child_node_ids")),
        "sample_descendant_node_ids": _string_sequence(
            root.get("sample_descendant_node_ids")
        ),
    }


def _aggregate(
    read_paths: Sequence[Mapping[str, Any]],
    hierarchy: Mapping[str, Any],
) -> dict[str, Any]:
    target_ids = sorted(
        {
            _string_field(target, "target_node_id")
            for row in read_paths
            for target in _sequence(row.get("ranked_targets")) or ()
            if _string_field(target, "target_node_id")
        }
    )
    source_ids = sorted(
        {
            _string_field(row, "source_node_id")
            for row in read_paths
            if _string_field(row, "source_node_id")
        }
    )
    return {
        "memory_count": len(read_paths),
        "hierarchy_node_count": _intish(hierarchy.get("node_count")),
        "hierarchy_root_count": _intish(hierarchy.get("root_count")),
        "total_candidate_count": sum(
            _intish(row.get("candidate_count")) for row in read_paths
        ),
        "unique_source_node_ids": source_ids,
        "unique_target_node_ids": target_ids,
        "max_hierarchy_hops": max(
            (_intish(row.get("max_hierarchy_hops")) for row in read_paths),
            default=0,
        ),
        "max_intermediate_hops_skipped": max(
            (
                _intish(row.get("max_intermediate_hops_skipped"))
                for row in read_paths
            ),
            default=0,
        ),
    }


def _authority_boundary(
    hierarchy: Mapping[str, Any],
    previews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    hierarchy_boundary = _mapping(hierarchy.get("authority_boundary"))
    preview_boundaries = [
        _mapping(preview.get("authority_boundary")) for preview in previews
    ]
    false_fields = (
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "gate_skip_performed",
        "network_access_performed",
        "runtime_authority_granted",
    )
    return {
        "read_side_projection_only": True,
        "hierarchy_summary_ok": hierarchy.get("ok") is True,
        "read_path_preview_count": len(previews),
        "component_authority_boundaries_false": all(
            hierarchy_boundary.get(field) is False
            and all(boundary.get(field) is False for boundary in preview_boundaries)
            for field in false_fields
        ),
        "runtime_route_changed": False,
        "storage_write_performed": False,
        "bridge_append_performed": False,
        "solver_call_performed": False,
        "scheduler_enqueue_performed": False,
        "promotion_performed": False,
        "gate_skip_performed": False,
        "network_access_performed": False,
        "runtime_authority_granted": False,
        "memory_payload_included": False,
        "matched_values_included": False,
        "local_paths_recorded": False,
    }


def _component_blockers(
    component: str,
    index: int,
    report: Mapping[str, Any],
) -> list[str]:
    prefix = component
    if index:
        prefix = f"{prefix}:memory_index_{index}"
    blockers = _string_sequence(report.get("blockers"))
    if not blockers:
        blockers = ["unknown_failure"]
    return [f"{prefix}:{_safe_code(blocker)}" for blocker in blockers]


def _normalize_memory_ids(memory_ids: Sequence[str]) -> list[str] | None:
    if isinstance(memory_ids, (str, bytes)) or not isinstance(memory_ids, Sequence):
        return None
    result: list[str] = []
    for memory_id in memory_ids:
        if not isinstance(memory_id, str) or not memory_id.strip():
            return None
        result.append(memory_id)
    return result or None


def _load_json(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"projection_json_read_failed:{exc.__class__.__name__}") from exc
    try:
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except DuplicateKeyError as exc:
        raise ValueError("projection_json_duplicate_key") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"projection_json_decode_failed:{exc.__class__.__name__}") from exc


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(key)
        result[key] = value
    return result


def _failure_overview(*blockers: str) -> dict[str, Any]:
    safe_blockers = sorted({_safe_code(blocker) for blocker in blockers if blocker})
    if not safe_blockers:
        safe_blockers = ["unknown_failure"]
    return {
        "overview_version": OVERVIEW_VERSION,
        "ok": False,
        "claim_label": CLAIM_LABEL,
        "component_versions": {
            "hierarchy_map_summary": SUMMARY_VERSION,
            "read_path_preview": PREVIEW_VERSION,
        },
        "source_projection_schema_version": "",
        "source_of_truth": "",
        "memory_ids": [],
        "hierarchy": {
            "node_count": 0,
            "root_count": 0,
            "max_depth": 0,
            "kind_counts": {},
            "roots": [],
            "coverage": {
                "placement_count": 0,
                "shortcut_hint_count": 0,
                "long_shortcut_hint_count": 0,
                "node_ids_with_placements": [],
                "node_ids_with_shortcuts": [],
            },
        },
        "read_path_overview": [],
        "aggregate": {
            "memory_count": 0,
            "hierarchy_node_count": 0,
            "hierarchy_root_count": 0,
            "total_candidate_count": 0,
            "unique_source_node_ids": [],
            "unique_target_node_ids": [],
            "max_hierarchy_hops": 0,
            "max_intermediate_hops_skipped": 0,
        },
        "authority_boundary": {
            "read_side_projection_only": False,
            "hierarchy_summary_ok": False,
            "read_path_preview_count": 0,
            "component_authority_boundaries_false": False,
            "runtime_route_changed": False,
            "storage_write_performed": False,
            "bridge_append_performed": False,
            "solver_call_performed": False,
            "scheduler_enqueue_performed": False,
            "promotion_performed": False,
            "gate_skip_performed": False,
            "network_access_performed": False,
            "runtime_authority_granted": False,
            "memory_payload_included": False,
            "matched_values_included": False,
            "local_paths_recorded": False,
        },
        "no_overclaim_guardrails": {
            "not_router_dispatch": True,
            "not_solver_call": True,
            "not_storage_write": True,
            "not_bridge_append": True,
            "not_scheduler_enqueue": True,
            "not_promotion_authority": True,
            "not_gate_skip": True,
            "not_networked_retrieval": True,
            "not_production_memory_migration": True,
            "projection_reader_only": True,
        },
        "blockers": safe_blockers,
        "operator_interpretation": (
            "The Memory Palace operator overview failed closed and grants no "
            "runtime authority."
        ),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return value
    return None


def _string_sequence(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in (str(item) for item in value) if item]


def _string_field(value: Any, key: str) -> str:
    if not isinstance(value, Mapping):
        return ""
    raw = value.get(key)
    return raw if isinstance(raw, str) else ""


def _intish(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _floatish(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _safe_code(value: str) -> str:
    cleaned = []
    for char in str(value).strip()[:180]:
        if char.isalnum() or char in "_.:-[]$":
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("_")
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "invalid"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
