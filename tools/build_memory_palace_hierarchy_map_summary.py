# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only Memory Palace hierarchy map summary.

The summary consumes an existing Memory Palace projection and emits a compact
review view of wings, rooms, placements, and shortcut coverage. It is intended
for human/agent orientation only: it does not mutate memory, append bridge
events, dispatch solvers, enqueue schedulers, change routes, or grant runtime,
promotion, storage, bridge, or gate-skip authority.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.memory_palace import (  # noqa: E402
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
    MemoryPalaceProjectionError,
    build_memory_palace_navigation_index,
)


SUMMARY_VERSION = "wd.v12.memory_palace_hierarchy_map_summary.v0"
CLAIM_LABEL = "READ_ONLY_HIERARCHY_MAP"

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
        report = _failure_summary(str(exc))
    else:
        report = build_memory_palace_hierarchy_map_summary(projection)

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    else:
        if report["ok"]:
            print(render_markdown(report), end="")
        else:
            print(encoded)
            print(
                "Memory Palace hierarchy map summary FAILED: "
                + ", ".join(report["blockers"]),
                file=sys.stderr,
            )
    return 0 if report["ok"] else 1


def build_memory_palace_hierarchy_map_summary(
    projection: Any,
) -> dict[str, Any]:
    """Return a deterministic, authority-free hierarchy summary."""

    if not isinstance(projection, Mapping):
        return _failure_summary("projection_not_object")

    blockers: list[str] = []
    if _contains_non_finite(projection):
        blockers.append("projection_contains_non_finite")
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
            f"authority_effect_flag_not_false:{path}"
            for path in authority_violations[:10]
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

    navigation_index: Mapping[str, Any] | None = None
    if not blockers:
        try:
            navigation_index = build_memory_palace_navigation_index(nodes)
        except MemoryPalaceProjectionError as exc:
            blockers.append(f"navigation_index_failed:{_safe_code(str(exc))}")

    if blockers or navigation_index is None:
        return _failure_summary(*blockers)

    entries = _navigation_entries(navigation_index)
    children_by_node = _mapping(navigation_index.get("children_by_node"))
    placements_by_node = _counter(
        _string_field(item, "palace_node_id") for item in placements
    )
    shortcut_sources = _counter(
        _string_field(item, "source_node_id") for item in shortcuts
    )
    shortcut_targets = _counter(
        _string_field(item, "target_node_id") for item in shortcuts
    )
    kind_counts = dict(sorted(Counter(_string_field(item, "kind") for item in entries).items()))
    long_shortcut_count = sum(
        1 for item in shortcuts if _int_field(item, "hierarchy_hops") >= 2
    )
    node_ids_with_placements = sorted(
        node_id for node_id, count in placements_by_node.items() if count > 0
    )
    node_ids_with_shortcuts = sorted(
        {
            node_id
            for counter in (shortcut_sources, shortcut_targets)
            for node_id, count in counter.items()
            if count > 0
        }
    )

    report = {
        "summary_version": SUMMARY_VERSION,
        "ok": True,
        "claim_label": CLAIM_LABEL,
        "source_projection_schema_version": MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
        "source_of_truth": "projection_only",
        "node_count": len(entries),
        "root_count": len(navigation_index["root_node_ids"]),
        "max_depth": int(navigation_index["max_depth"]),
        "kind_counts": kind_counts,
        "roots": [
            _node_summary(
                entry,
                children_by_node,
                placements_by_node,
                shortcut_sources,
                shortcut_targets,
                entries,
            )
            for entry in entries
            if int(entry["depth"]) == 0
        ],
        "coverage": {
            "placement_count": len(placements),
            "node_ids_with_placements": node_ids_with_placements,
            "shortcut_hint_count": len(shortcuts),
            "long_shortcut_hint_count": long_shortcut_count,
            "node_ids_with_shortcuts": node_ids_with_shortcuts,
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
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "blockers": [],
        "operator_interpretation": (
            "This is a read-only hierarchy map of an existing Memory Palace "
            "projection. It can orient humans and agents to wings, rooms, "
            "placements, and shortcut coverage, but it is not router dispatch, "
            "solver execution, storage mutation, bridge append, promotion, or "
            "gate authority."
        ),
    }
    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Memory Palace Hierarchy Map Summary",
        "",
        f"- summary_version: `{report['summary_version']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- node_count: `{report['node_count']}`",
        f"- root_count: `{report['root_count']}`",
        f"- max_depth: `{report['max_depth']}`",
        "",
        "## Kinds",
        "",
    ]
    for kind, count in report["kind_counts"].items():
        lines.append(f"- {kind}: `{count}`")

    lines.extend(["", "## Roots", ""])
    for root in report["roots"]:
        lines.append(
            "- `{node_id}` ({kind}) children=`{child_count}` "
            "descendants=`{descendant_count}` placements=`{placement_count}` "
            "shortcut_sources=`{shortcut_source_count}` "
            "shortcut_targets=`{shortcut_target_count}`".format(**root)
        )

    coverage = report["coverage"]
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            f"- placement_count: `{coverage['placement_count']}`",
            f"- shortcut_hint_count: `{coverage['shortcut_hint_count']}`",
            (
                "- long_shortcut_hint_count: `"
                + str(coverage["long_shortcut_hint_count"])
                + "`"
            ),
            (
                "- node_ids_with_placements: `"
                + ", ".join(coverage["node_ids_with_placements"])
                + "`"
            ),
            (
                "- node_ids_with_shortcuts: `"
                + ", ".join(coverage["node_ids_with_shortcuts"])
                + "`"
            ),
            "",
            "## Boundary",
            "",
        ]
    )
    for key, value in sorted(report["authority_boundary"].items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(["", report["operator_interpretation"], ""])
    return "\n".join(lines)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"projection_json_read_failed:{exc.__class__.__name__}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"projection_json_decode_failed:{exc.__class__.__name__}") from exc


def _failure_summary(*blockers: str) -> dict[str, Any]:
    safe_blockers = sorted({_safe_code(blocker) for blocker in blockers if blocker})
    if not safe_blockers:
        safe_blockers = ["unknown_failure"]
    return {
        "summary_version": SUMMARY_VERSION,
        "ok": False,
        "claim_label": CLAIM_LABEL,
        "source_projection_schema_version": "",
        "source_of_truth": "",
        "node_count": 0,
        "root_count": 0,
        "max_depth": 0,
        "kind_counts": {},
        "roots": [],
        "coverage": {
            "placement_count": 0,
            "node_ids_with_placements": [],
            "shortcut_hint_count": 0,
            "long_shortcut_hint_count": 0,
            "node_ids_with_shortcuts": [],
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
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "blockers": safe_blockers,
        "operator_interpretation": (
            "The hierarchy map summary failed closed and grants no runtime "
            "authority."
        ),
    }


def _navigation_entries(index: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    entries = _sequence(index.get("nodes")) or ()
    return sorted(
        (entry for entry in entries if isinstance(entry, Mapping)),
        key=lambda item: (_int_field(item, "depth"), _string_field(item, "node_id")),
    )


def _node_summary(
    entry: Mapping[str, Any],
    children_by_node: Mapping[str, Any],
    placements_by_node: Counter[str],
    shortcut_sources: Counter[str],
    shortcut_targets: Counter[str],
    entries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    node_id = _string_field(entry, "node_id")
    descendants = _descendants(node_id, children_by_node)
    subtree = {node_id, *descendants}
    return {
        "node_id": node_id,
        "kind": _string_field(entry, "kind"),
        "label": _string_field(entry, "label"),
        "child_count": len(_string_sequence(children_by_node.get(node_id))),
        "descendant_count": len(descendants),
        "placement_count": sum(placements_by_node[item] for item in subtree),
        "shortcut_source_count": sum(shortcut_sources[item] for item in subtree),
        "shortcut_target_count": sum(shortcut_targets[item] for item in subtree),
        "ancestry_node_ids": _string_sequence(entry.get("path_node_ids")),
        "direct_child_node_ids": _string_sequence(children_by_node.get(node_id)),
        "sample_descendant_node_ids": sorted(descendants)[:8],
        "node_count_in_subtree": 1
        + sum(1 for item in entries if _string_field(item, "node_id") in descendants),
    }


def _descendants(node_id: str, children_by_node: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    stack = list(_string_sequence(children_by_node.get(node_id)))
    while stack:
        child = stack.pop()
        if child in result:
            continue
        result.add(child)
        stack.extend(_string_sequence(children_by_node.get(child)))
    return result


def _authority_violations(value: Any, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key) in _AUTHORITY_FIELDS and child is not False:
                violations.append(child_path)
            violations.extend(_authority_violations(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(_authority_violations(child, f"{path}[{index}]"))
    return violations


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(child) for child in value)
    return False


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


def _int_field(value: Any, key: str) -> int:
    if not isinstance(value, Mapping):
        return 0
    raw = value.get(key)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0


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
