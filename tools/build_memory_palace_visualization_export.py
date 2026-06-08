# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only Memory Palace visualization export.

The export consumes an existing Memory Palace projection and emits a compact
node/edge graph for operator visualization. It is projection-only: it does not
mutate memory, dispatch runtime routes, call solvers, enqueue scheduler work,
append bridge events, access the network, promote shortcuts, or grant gate
authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.memory_palace import (  # noqa: E402
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
    MemoryPalaceProjectionError,
    build_memory_palace_navigation_index,
    build_memory_palace_projection,
)


EXPORT_VERSION = "wd.v12.memory_palace_visualization_export.v0"
CLAIM_LABEL = "READ_ONLY_MEMORY_PALACE_VISUALIZATION_EXPORT"

_PROJECTION_FALSE_FIELDS = (
    "runtime_authority",
    "storage_write_authority",
    "bridge_write_authority",
    "gate_skip_authority",
    "promotion_authority",
)
_AUTHORITY_FIELDS = frozenset(
    {
        "runtime_authority",
        "storage_write_authority",
        "bridge_write_authority",
        "gate_skip_authority",
        "promotion_authority",
        "solver_call_authority",
        "runtime_route_changed",
        "storage_write_performed",
        "bridge_append_performed",
        "solver_call_performed",
        "scheduler_enqueue_performed",
        "promotion_performed",
        "gate_skip_performed",
        "network_access_performed",
        "runtime_authority_granted",
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
        "direct_bridge_write_performed",
    }
)
_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "content",
        "filename",
        "filepath",
        "localpath",
        "path",
        "payload",
        "raw",
    }
)
_PATH_MARKER_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|"
    r"(?<![:/])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]*)"
)


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
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        projection = _load_json(args.projection_json)
    except ValueError as exc:
        report = _failure_export(str(exc))
    else:
        report = build_memory_palace_visualization_export(projection)

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    else:
        if report["ok"]:
            print(render_markdown(report), end="")
        else:
            print(encoded)
            print(
                "Memory Palace visualization export FAILED: "
                + ", ".join(report["blockers"]),
                file=sys.stderr,
            )
    return 0 if report["ok"] else 1


def build_memory_palace_visualization_export(projection: Any) -> dict[str, Any]:
    """Return a deterministic, authority-free node/edge visualization export."""

    if not isinstance(projection, Mapping):
        return _failure_export("projection_not_object")

    blockers: list[str] = []
    if _contains_non_finite(projection):
        blockers.append("projection_contains_non_finite")
    if _contains_forbidden_key_or_path_marker(projection):
        blockers.append("projection_contains_forbidden_payload_or_path_marker")
    if projection.get("schema_version") != MEMORY_PALACE_PROJECTION_SCHEMA_VERSION:
        blockers.append("projection_schema_version_mismatch")
    if projection.get("source_of_truth") != "projection_only":
        blockers.append("projection_source_of_truth_not_projection_only")

    for field in _PROJECTION_FALSE_FIELDS:
        if projection.get(field) is not False:
            blockers.append(f"projection_{field}_not_false")

    authority_violations = _authority_violations(projection)
    if authority_violations:
        blockers.extend(
            f"authority_effect_flag_not_false:{field_name}"
            for field_name in sorted(set(authority_violations))[:10]
        )

    nodes = _sequence(projection.get("nodes"))
    placements = _sequence(projection.get("placements"))
    shortcuts = _sequence(projection.get("shortcuts"))
    if nodes is None:
        blockers.append("projection_nodes_not_list")
        nodes = ()
    if placements is None:
        blockers.append("projection_placements_not_list")
        placements = ()
    if shortcuts is None:
        blockers.append("projection_shortcuts_not_list")
        shortcuts = ()

    if blockers:
        return _failure_export(*blockers)

    try:
        validated_projection = build_memory_palace_projection(
            nodes,
            placements=placements,
            shortcuts=shortcuts,
        )
        navigation_index = build_memory_palace_navigation_index(
            validated_projection["nodes"]
        )
    except MemoryPalaceProjectionError as exc:
        return _failure_export(f"projection_validation_failed:{_safe_code(str(exc))}")

    entries = _navigation_entries(navigation_index)
    validated_placements = _sequence(validated_projection.get("placements")) or ()
    validated_shortcuts = _sequence(validated_projection.get("shortcuts")) or ()
    placements_by_node = _counter(
        _string_field(item, "palace_node_id") for item in validated_placements
    )
    shortcut_sources = _counter(
        _string_field(item, "source_node_id") for item in validated_shortcuts
    )
    shortcut_targets = _counter(
        _string_field(item, "target_node_id") for item in validated_shortcuts
    )
    visual_nodes = [
        _visual_node(entry, placements_by_node, shortcut_sources, shortcut_targets)
        for entry in entries
    ]
    hierarchy_edges = [_hierarchy_edge(entry) for entry in entries if entry.get("parent_id")]
    shortcut_edges = [_shortcut_edge(shortcut) for shortcut in validated_shortcuts]
    edges = sorted(
        [*hierarchy_edges, *shortcut_edges],
        key=lambda item: (
            _string_field(item, "edge_kind"),
            _string_field(item, "source_node_id"),
            _string_field(item, "target_node_id"),
            _string_field(item, "edge_id"),
        ),
    )
    report = {
        "export_version": EXPORT_VERSION,
        "ok": True,
        "claim_label": CLAIM_LABEL,
        "source_projection_schema_version": MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
        "source_of_truth": "projection_only",
        "layout_hints": {
            "graph_kind": "hierarchy_with_read_side_shortcuts",
            "rank_direction": "top_down",
            "hierarchy_edge_style": "solid",
            "shortcut_edge_style": "dashed",
            "coordinates_included": False,
            "visualization_only": True,
        },
        "nodes": visual_nodes,
        "edges": edges,
        "aggregate": {
            "node_count": len(visual_nodes),
            "edge_count": len(edges),
            "hierarchy_edge_count": len(hierarchy_edges),
            "shortcut_edge_count": len(shortcut_edges),
            "placement_count": len(validated_placements),
            "root_count": len(_string_sequence(navigation_index.get("root_node_ids"))),
            "max_depth": _int_field(navigation_index, "max_depth"),
            "node_ids_with_placements": sorted(
                node_id for node_id, count in placements_by_node.items() if count > 0
            ),
        },
        "authority_boundary": {
            "read_side_projection_only": True,
            "runtime_route_changed": False,
            "storage_write_performed": False,
            "bridge_append_performed": False,
            "solver_call_performed": False,
            "scheduler_enqueue_performed": False,
            "promotion_performed": False,
            "gate_skip_performed": False,
            "network_access_performed": False,
            "runtime_authority_granted": False,
            "coordinates_generated": False,
            "memory_payload_included": False,
            "selectors_included": False,
            "matched_values_included": False,
            "source_refs_included": False,
            "metadata_included": False,
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
        "blockers": [],
        "operator_interpretation": (
            "This export helps render Memory Palace wings, rooms, and read-side "
            "shortcut hints as a node/edge graph. It is not a runtime route, "
            "solver dispatch, storage mutation, bridge append, scheduler "
            "enqueue, promotion, gate decision, or generated coordinate layout."
        ),
    }
    if _contains_path_marker(json.dumps(report, sort_keys=True, allow_nan=False)):
        return _failure_export("visualization_export_would_include_path_marker")
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    aggregate = _mapping(report.get("aggregate"))
    layout = _mapping(report.get("layout_hints"))
    lines = [
        "# Memory Palace Visualization Export",
        "",
        f"- export_version: `{report['export_version']}`",
        f"- source_projection_schema_version: `{report['source_projection_schema_version']}`",
        f"- source_of_truth: `{report['source_of_truth']}`",
        f"- node_count: `{aggregate.get('node_count', 0)}`",
        f"- edge_count: `{aggregate.get('edge_count', 0)}`",
        f"- hierarchy_edge_count: `{aggregate.get('hierarchy_edge_count', 0)}`",
        f"- shortcut_edge_count: `{aggregate.get('shortcut_edge_count', 0)}`",
        f"- rank_direction: `{layout.get('rank_direction', '')}`",
        "",
        "## Authority Boundary",
        "",
    ]
    for key, value in sorted(_mapping(report.get("authority_boundary")).items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(["", str(report["operator_interpretation"]), ""])
    return "\n".join(lines)


def _load_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except OSError as exc:
        raise ValueError(f"projection_json_read_failed:{exc.__class__.__name__}") from exc
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


def _failure_export(*blockers: str) -> dict[str, Any]:
    safe_blockers = sorted({_safe_code(blocker) for blocker in blockers if blocker})
    if not safe_blockers:
        safe_blockers = ["unknown_failure"]
    return {
        "export_version": EXPORT_VERSION,
        "ok": False,
        "claim_label": CLAIM_LABEL,
        "source_projection_schema_version": "",
        "source_of_truth": "",
        "layout_hints": {
            "graph_kind": "",
            "rank_direction": "",
            "hierarchy_edge_style": "",
            "shortcut_edge_style": "",
            "coordinates_included": False,
            "visualization_only": False,
        },
        "nodes": [],
        "edges": [],
        "aggregate": {
            "node_count": 0,
            "edge_count": 0,
            "hierarchy_edge_count": 0,
            "shortcut_edge_count": 0,
            "placement_count": 0,
            "root_count": 0,
            "max_depth": 0,
            "node_ids_with_placements": [],
        },
        "authority_boundary": {
            "read_side_projection_only": False,
            "runtime_route_changed": False,
            "storage_write_performed": False,
            "bridge_append_performed": False,
            "solver_call_performed": False,
            "scheduler_enqueue_performed": False,
            "promotion_performed": False,
            "gate_skip_performed": False,
            "network_access_performed": False,
            "runtime_authority_granted": False,
            "coordinates_generated": False,
            "memory_payload_included": False,
            "selectors_included": False,
            "matched_values_included": False,
            "source_refs_included": False,
            "metadata_included": False,
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
            "The Memory Palace visualization export failed closed and grants no "
            "runtime authority."
        ),
    }


def _navigation_entries(index: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = _sequence(index.get("nodes")) or ()
    return sorted(
        (entry for entry in entries if isinstance(entry, Mapping)),
        key=lambda item: (_int_field(item, "depth"), _string_field(item, "node_id")),
    )


def _visual_node(
    entry: Mapping[str, Any],
    placements_by_node: Counter[str],
    shortcut_sources: Counter[str],
    shortcut_targets: Counter[str],
) -> dict[str, Any]:
    node_id = _string_field(entry, "node_id")
    return {
        "node_id": node_id,
        "kind": _string_field(entry, "kind"),
        "label": _string_field(entry, "label"),
        "parent_id": _nullable_string(entry.get("parent_id")),
        "depth": _int_field(entry, "depth"),
        "child_count": len(_string_sequence(entry.get("child_node_ids"))),
        "placement_count": int(placements_by_node[node_id]),
        "shortcut_source_count": int(shortcut_sources[node_id]),
        "shortcut_target_count": int(shortcut_targets[node_id]),
        "path_node_ids": _string_sequence(entry.get("path_node_ids")),
    }


def _hierarchy_edge(entry: Mapping[str, Any]) -> dict[str, Any]:
    source = _string_field(entry, "parent_id")
    target = _string_field(entry, "node_id")
    return {
        "edge_id": f"hierarchy:{source}->{target}",
        "edge_kind": "hierarchy",
        "source_node_id": source,
        "target_node_id": target,
        "directed": True,
        "hierarchy_hops": 1,
    }


def _shortcut_edge(shortcut: Any) -> dict[str, Any]:
    item = _mapping(shortcut)
    return {
        "edge_id": "shortcut:" + _string_field(item, "shortcut_id"),
        "edge_kind": "shortcut",
        "source_node_id": _string_field(item, "source_node_id"),
        "target_node_id": _string_field(item, "target_node_id"),
        "directed": True,
        "hierarchy_hops": _int_field(item, "hierarchy_hops"),
        "shortcut_confidence": _float_field(item, "confidence"),
        "no_runtime_mutation": item.get("no_runtime_mutation") is True,
    }


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(child) for child in value)
    return False


def _contains_forbidden_key_or_path_marker(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _forbidden_key(str(key)) or _contains_path_marker(str(key)):
                return True
            if _contains_forbidden_key_or_path_marker(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_forbidden_key_or_path_marker(child) for child in value)
    if isinstance(value, str):
        return _contains_path_marker(value)
    return False


def _contains_path_marker(value: str) -> bool:
    return bool(_PATH_MARKER_RE.search(value))


def _forbidden_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    collapsed = normalized.replace("_", "")
    return (
        normalized in _FORBIDDEN_KEY_TOKENS
        or collapsed in _FORBIDDEN_KEY_TOKENS
        or any(token in _FORBIDDEN_KEY_TOKENS for token in tokens)
    )


def _authority_violations(value: Any) -> list[str]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _AUTHORITY_FIELDS and child is not False:
                violations.append(str(key))
            violations.extend(_authority_violations(child))
    elif isinstance(value, list):
        for child in value:
            violations.extend(_authority_violations(child))
    return violations


def _counter(values: Any) -> Counter[str]:
    return Counter(value for value in values if value)


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


def _nullable_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _int_field(value: Any, key: str) -> int:
    if not isinstance(value, Mapping):
        return 0
    raw = value.get(key)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0


def _float_field(value: Any, key: str) -> float:
    if not isinstance(value, Mapping):
        return 0.0
    raw = value.get(key)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    if not math.isfinite(float(raw)):
        return 0.0
    return float(raw)


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
