# SPDX-License-Identifier: BUSL-1.1
"""Verify a Memory Palace visualization export artifact.

The verifier is read-only and path-free. It validates the graph export emitted
by ``build_memory_palace_visualization_export`` without granting runtime,
solver, scheduler, storage, bridge, promotion, gate-skip, network, payload, or
layout authority.
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

from tools.build_memory_palace_visualization_export import (  # noqa: E402
    CLAIM_LABEL as SOURCE_CLAIM_LABEL,
    EXPORT_VERSION as SOURCE_EXPORT_VERSION,
)
from waggledance.core.memory_palace import (  # noqa: E402
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
)


VERIFICATION_VERSION = "wd.v12.memory_palace_visualization_export.verification.v0"
EXPORT_ARTIFACT_ID = "memory_palace_visualization_export"

_FALSE_BOUNDARY_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "gate_skip_performed",
    "network_access_performed",
    "runtime_authority_granted",
    "coordinates_generated",
    "memory_payload_included",
    "selectors_included",
    "matched_values_included",
    "source_refs_included",
    "metadata_included",
    "local_paths_recorded",
)
_TRUE_GUARDRAIL_FIELDS = (
    "not_router_dispatch",
    "not_solver_call",
    "not_storage_write",
    "not_bridge_append",
    "not_scheduler_enqueue",
    "not_promotion_authority",
    "not_gate_skip",
    "not_networked_retrieval",
    "not_production_memory_migration",
    "projection_reader_only",
)
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "content",
        "filename",
        "filepath",
        "localpath",
        "matched_values",
        "metadata",
        "path",
        "payload",
        "raw",
        "selectors",
        "source_refs",
        "raw_payload",
        "source_path",
        "artifact_path",
    }
)
_FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "content",
        "filename",
        "filepath",
        "localpath",
        "matched",
        "metadata",
        "path",
        "payload",
        "raw",
        "selectors",
    }
)
_ALLOWED_INERT_KEY_NAMES = frozenset(_FALSE_BOUNDARY_FIELDS) | frozenset(
    {
        "path_node_ids",
    }
)
_PATH_MARKER_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\|file://|"
    r"(?<![:/])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]*)"
)


class VisualizationExportVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely loaded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export-json",
        required=True,
        type=Path,
        help="Path to a JSON report emitted by the visualization export tool.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        export = _load_json_report(args.export_json)
        verification = verify_memory_palace_visualization_export(export)
    except VisualizationExportVerificationError as exc:
        verification = _failure_report(
            f"{EXPORT_ARTIFACT_ID}_verification_failed:{exc.code}"
        )

    encoded = json.dumps(verification, indent=2, sort_keys=True, allow_nan=False)
    if args.json or verification["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace visualization export verification FAILED: "
            + ", ".join(verification["blockers"]),
            file=sys.stderr,
        )
    return 0 if verification["ok"] else 1


def verify_memory_palace_visualization_export(export: Any) -> dict[str, Any]:
    """Return an action-free verification report for a visualization export."""

    if not isinstance(export, Mapping):
        return _failure_report(f"{EXPORT_ARTIFACT_ID}_not_object")

    blockers: list[str] = []
    if _contains_non_finite(export):
        blockers.append("export_contains_non_finite")
    if _contains_forbidden_payload_key(export):
        blockers.append("forbidden_payload_key_present")
    if _contains_path_marker(export):
        blockers.append("forbidden_path_marker_present")

    _collect_header_blockers(export, blockers)
    layout_check = _collect_layout_blockers(export, blockers)
    authority_boundary_check = _collect_authority_boundary_blockers(export, blockers)
    guardrail_check = _collect_guardrail_blockers(export, blockers)
    graph_check, aggregate_check = _collect_graph_blockers(export, blockers)
    path_free_check = (
        "match"
        if not {
            "forbidden_payload_key_present",
            "forbidden_path_marker_present",
        }
        & set(blockers)
        else "mismatch"
    )

    aggregate = _mapping(export.get("aggregate"))
    verification = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "source_export_version_check": (
            "match"
            if export.get("export_version") == SOURCE_EXPORT_VERSION
            else "mismatch"
        ),
        "source_claim_label_check": (
            "match" if export.get("claim_label") == SOURCE_CLAIM_LABEL else "mismatch"
        ),
        "source_export_ok": export.get("ok") is True,
        "source_of_truth_check": (
            "match"
            if export.get("source_of_truth") == "projection_only"
            else "mismatch"
        ),
        "source_projection_schema_version_check": (
            "match"
            if export.get("source_projection_schema_version")
            == MEMORY_PALACE_PROJECTION_SCHEMA_VERSION
            else "mismatch"
        ),
        "layout_check": layout_check,
        "graph_check": graph_check,
        "aggregate_check": aggregate_check,
        "authority_boundary_check": authority_boundary_check,
        "guardrail_check": guardrail_check,
        "path_free_check": path_free_check,
        "node_count_checked": _int(aggregate.get("node_count")),
        "edge_count_checked": _int(aggregate.get("edge_count")),
        "shortcut_edge_count_checked": _int(aggregate.get("shortcut_edge_count")),
        "read_side_report_only": True,
        "manual_review_required": True,
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
        "blockers": sorted(set(blockers)),
        "warnings": [],
    }
    json.dumps(verification, sort_keys=True, allow_nan=False)
    return verification


def _collect_header_blockers(export: Mapping[str, Any], blockers: list[str]) -> None:
    if export.get("export_version") != SOURCE_EXPORT_VERSION:
        blockers.append("export_version_mismatch")
    if export.get("claim_label") != SOURCE_CLAIM_LABEL:
        blockers.append("claim_label_mismatch")
    if export.get("ok") is not True:
        blockers.append("source_export_not_ok")
    if export.get("blockers") != []:
        blockers.append("source_export_blockers_present")
    if export.get("source_projection_schema_version") != (
        MEMORY_PALACE_PROJECTION_SCHEMA_VERSION
    ):
        blockers.append("source_projection_schema_version_mismatch")
    if export.get("source_of_truth") != "projection_only":
        blockers.append("source_of_truth_not_projection_only")


def _collect_layout_blockers(
    export: Mapping[str, Any],
    blockers: list[str],
) -> str:
    layout = export.get("layout_hints")
    if not isinstance(layout, Mapping):
        blockers.append("layout_hints_not_object")
        return "mismatch"
    expected = {
        "graph_kind": "hierarchy_with_read_side_shortcuts",
        "rank_direction": "top_down",
        "hierarchy_edge_style": "solid",
        "shortcut_edge_style": "dashed",
        "coordinates_included": False,
        "visualization_only": True,
    }
    ok = True
    for field, expected_value in expected.items():
        if layout.get(field) != expected_value:
            blockers.append(f"layout_{field}_mismatch")
            ok = False
    return "match" if ok else "mismatch"


def _collect_authority_boundary_blockers(
    export: Mapping[str, Any],
    blockers: list[str],
) -> str:
    boundary = export.get("authority_boundary")
    if not isinstance(boundary, Mapping):
        blockers.append("authority_boundary_not_object")
        return "mismatch"
    ok = True
    if boundary.get("read_side_projection_only") is not True:
        blockers.append("authority_boundary_read_side_projection_only_not_true")
        ok = False
    for field in _FALSE_BOUNDARY_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"authority_boundary_{field}_not_false")
            ok = False
    return "match" if ok else "mismatch"


def _collect_guardrail_blockers(
    export: Mapping[str, Any],
    blockers: list[str],
) -> str:
    guardrails = export.get("no_overclaim_guardrails")
    if not isinstance(guardrails, Mapping):
        blockers.append("no_overclaim_guardrails_not_object")
        return "mismatch"
    ok = True
    for field in _TRUE_GUARDRAIL_FIELDS:
        if guardrails.get(field) is not True:
            blockers.append(f"guardrail_{field}_not_true")
            ok = False
    return "match" if ok else "mismatch"


def _collect_graph_blockers(
    export: Mapping[str, Any],
    blockers: list[str],
) -> tuple[str, str]:
    nodes = export.get("nodes")
    edges = export.get("edges")
    aggregate = export.get("aggregate")
    graph_ok = True
    aggregate_ok = True
    if not isinstance(nodes, list):
        blockers.append("nodes_not_list")
        graph_ok = False
        nodes = []
    if not isinstance(edges, list):
        blockers.append("edges_not_list")
        graph_ok = False
        edges = []
    if not isinstance(aggregate, Mapping):
        blockers.append("aggregate_not_object")
        aggregate_ok = False
        aggregate = {}

    node_stats = _collect_node_stats(nodes, blockers)
    if not node_stats["ok"]:
        graph_ok = False
    edge_stats = _collect_edge_stats(edges, node_stats, blockers)
    if not edge_stats["ok"]:
        graph_ok = False
    if not _collect_aggregate_blockers(
        aggregate,
        node_stats,
        edge_stats,
        blockers,
    ):
        aggregate_ok = False
    return ("match" if graph_ok else "mismatch", "match" if aggregate_ok else "mismatch")


def _collect_node_stats(nodes: Sequence[Any], blockers: list[str]) -> dict[str, Any]:
    ok = True
    node_ids: set[str] = set()
    roots = 0
    max_depth = 0
    placement_total = 0
    placement_nodes: list[str] = []
    parents: dict[str, str | None] = {}
    child_count_by_node: dict[str, int] = {}
    shortcut_source_counts: Counter[str] = Counter()
    shortcut_target_counts: Counter[str] = Counter()

    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            blockers.append(f"node_{index}_not_object")
            ok = False
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            blockers.append(f"node_{index}_node_id_invalid")
            ok = False
            continue
        if node_id in node_ids:
            blockers.append(f"node_{index}_duplicate_node_id")
            ok = False
        node_ids.add(node_id)
        parent_id = node.get("parent_id")
        if parent_id is not None and (not isinstance(parent_id, str) or not parent_id):
            blockers.append(f"node_{index}_parent_id_invalid")
            ok = False
            parent_id = None
        if parent_id is None:
            roots += 1
        parents[node_id] = parent_id

        for field in ("kind", "label"):
            if not isinstance(node.get(field), str) or not node.get(field):
                blockers.append(f"node_{index}_{field}_invalid")
                ok = False
        for field in (
            "depth",
            "child_count",
            "placement_count",
            "shortcut_source_count",
            "shortcut_target_count",
        ):
            if not _nonnegative_int(node.get(field)):
                blockers.append(f"node_{index}_{field}_not_nonnegative_int")
                ok = False
        path_node_ids = node.get("path_node_ids")
        if not _string_list(path_node_ids):
            blockers.append(f"node_{index}_path_node_ids_invalid")
            ok = False
        else:
            if path_node_ids[-1] != node_id:
                blockers.append(f"node_{index}_path_does_not_end_at_node")
                ok = False
            if _nonnegative_int(node.get("depth")) and len(path_node_ids) != node["depth"] + 1:
                blockers.append(f"node_{index}_path_depth_mismatch")
                ok = False
        if _nonnegative_int(node.get("depth")):
            max_depth = max(max_depth, int(node["depth"]))
        if _nonnegative_int(node.get("placement_count")):
            placement_total += int(node["placement_count"])
            if int(node["placement_count"]) > 0:
                placement_nodes.append(node_id)
        if _nonnegative_int(node.get("child_count")):
            child_count_by_node[node_id] = int(node["child_count"])
        if _nonnegative_int(node.get("shortcut_source_count")):
            shortcut_source_counts[node_id] = int(node["shortcut_source_count"])
        if _nonnegative_int(node.get("shortcut_target_count")):
            shortcut_target_counts[node_id] = int(node["shortcut_target_count"])

    for index, node in enumerate(nodes):
        if not isinstance(node, Mapping):
            continue
        node_id = node.get("node_id")
        parent_id = node.get("parent_id")
        if isinstance(node_id, str) and node_id and isinstance(parent_id, str):
            if parent_id not in node_ids:
                blockers.append(f"node_{index}_parent_id_unknown")
                ok = False

    return {
        "ok": ok,
        "node_ids": node_ids,
        "roots": roots,
        "max_depth": max_depth,
        "parents": parents,
        "child_count_by_node": child_count_by_node,
        "placement_total": placement_total,
        "placement_nodes": sorted(set(placement_nodes)),
        "shortcut_source_counts": shortcut_source_counts,
        "shortcut_target_counts": shortcut_target_counts,
    }


def _collect_edge_stats(
    edges: Sequence[Any],
    node_stats: Mapping[str, Any],
    blockers: list[str],
) -> dict[str, Any]:
    ok = True
    edge_ids: set[str] = set()
    hierarchy_edges = 0
    shortcut_edges = 0
    child_edges_by_source: Counter[str] = Counter()
    shortcut_sources: Counter[str] = Counter()
    shortcut_targets: Counter[str] = Counter()
    node_ids = node_stats.get("node_ids", set())
    parents = node_stats.get("parents", {})

    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            blockers.append(f"edge_{index}_not_object")
            ok = False
            continue
        edge_id = edge.get("edge_id")
        if not isinstance(edge_id, str) or not edge_id:
            blockers.append(f"edge_{index}_edge_id_invalid")
            ok = False
        elif edge_id in edge_ids:
            blockers.append(f"edge_{index}_duplicate_edge_id")
            ok = False
        elif _contains_path_marker(edge_id):
            blockers.append(f"edge_{index}_edge_id_path_marker")
            ok = False
        if isinstance(edge_id, str):
            edge_ids.add(edge_id)

        edge_kind = edge.get("edge_kind")
        source = edge.get("source_node_id")
        target = edge.get("target_node_id")
        if edge_kind not in {"hierarchy", "shortcut"}:
            blockers.append(f"edge_{index}_edge_kind_invalid")
            ok = False
            continue
        for field, value in (("source_node_id", source), ("target_node_id", target)):
            if not isinstance(value, str) or not value:
                blockers.append(f"edge_{index}_{field}_invalid")
                ok = False
            elif value not in node_ids:
                blockers.append(f"edge_{index}_{field}_unknown")
                ok = False
        if edge.get("directed") is not True:
            blockers.append(f"edge_{index}_directed_not_true")
            ok = False

        if edge_kind == "hierarchy":
            hierarchy_edges += 1
            if edge.get("hierarchy_hops") != 1:
                blockers.append(f"edge_{index}_hierarchy_hops_not_one")
                ok = False
            if isinstance(source, str) and isinstance(target, str):
                child_edges_by_source[source] += 1
                if isinstance(parents, Mapping) and parents.get(target) != source:
                    blockers.append(f"edge_{index}_hierarchy_parent_mismatch")
                    ok = False
                expected_edge_id = f"hierarchy:{source}->{target}"
                if edge_id != expected_edge_id:
                    blockers.append(f"edge_{index}_hierarchy_edge_id_mismatch")
                    ok = False
        else:
            shortcut_edges += 1
            if not _positive_int(edge.get("hierarchy_hops")):
                blockers.append(f"edge_{index}_shortcut_hierarchy_hops_invalid")
                ok = False
            if not _finite_ratio(edge.get("shortcut_confidence")):
                blockers.append(f"edge_{index}_shortcut_confidence_invalid")
                ok = False
            if edge.get("no_runtime_mutation") is not True:
                blockers.append(f"edge_{index}_no_runtime_mutation_not_true")
                ok = False
            if isinstance(source, str) and isinstance(target, str):
                shortcut_sources[source] += 1
                shortcut_targets[target] += 1
            if isinstance(edge_id, str) and not edge_id.startswith("shortcut:"):
                blockers.append(f"edge_{index}_shortcut_edge_id_invalid")
                ok = False

    for node_id, expected_count in _mapping(
        node_stats.get("child_count_by_node")
    ).items():
        if child_edges_by_source[node_id] != expected_count:
            blockers.append("node_child_count_hierarchy_edge_mismatch")
            ok = False
            break
    for node_id, expected_count in _mapping(
        node_stats.get("shortcut_source_counts")
    ).items():
        if shortcut_sources[node_id] != expected_count:
            blockers.append("node_shortcut_source_count_mismatch")
            ok = False
            break
    for node_id, expected_count in _mapping(
        node_stats.get("shortcut_target_counts")
    ).items():
        if shortcut_targets[node_id] != expected_count:
            blockers.append("node_shortcut_target_count_mismatch")
            ok = False
            break

    return {
        "ok": ok,
        "edge_count": len(edges),
        "hierarchy_edges": hierarchy_edges,
        "shortcut_edges": shortcut_edges,
    }


def _collect_aggregate_blockers(
    aggregate: Mapping[str, Any],
    node_stats: Mapping[str, Any],
    edge_stats: Mapping[str, Any],
    blockers: list[str],
) -> bool:
    ok = True
    for field in (
        "node_count",
        "edge_count",
        "hierarchy_edge_count",
        "shortcut_edge_count",
        "placement_count",
        "root_count",
        "max_depth",
    ):
        if not _nonnegative_int(aggregate.get(field)):
            blockers.append(f"aggregate_{field}_not_nonnegative_int")
            ok = False
    checks = {
        "node_count": len(node_stats.get("node_ids", set())),
        "edge_count": edge_stats.get("edge_count", 0),
        "hierarchy_edge_count": edge_stats.get("hierarchy_edges", 0),
        "shortcut_edge_count": edge_stats.get("shortcut_edges", 0),
        "placement_count": node_stats.get("placement_total", 0),
        "root_count": node_stats.get("roots", 0),
        "max_depth": node_stats.get("max_depth", 0),
    }
    for field, expected in checks.items():
        if _nonnegative_int(aggregate.get(field)) and aggregate.get(field) != expected:
            blockers.append(f"aggregate_{field}_mismatch")
            ok = False
    placement_nodes = aggregate.get("node_ids_with_placements")
    if not _sorted_unique_string_list(placement_nodes):
        blockers.append("aggregate_node_ids_with_placements_invalid")
        ok = False
    elif placement_nodes != node_stats.get("placement_nodes", []):
        blockers.append("aggregate_node_ids_with_placements_mismatch")
        ok = False
    return ok


def _load_json_report(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except OSError as exc:
        raise VisualizationExportVerificationError(
            f"{EXPORT_ARTIFACT_ID}_unreadable",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise VisualizationExportVerificationError(
            f"{EXPORT_ARTIFACT_ID}_json_error",
        ) from exc


def _reject_duplicate_json_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _failure_report(code: str) -> dict[str, Any]:
    safe_code = _safe_code(code)
    return {
        "ok": False,
        "verification_version": VERIFICATION_VERSION,
        "source_export_version_check": "not_checked",
        "source_claim_label_check": "not_checked",
        "source_export_ok": False,
        "source_of_truth_check": "not_checked",
        "source_projection_schema_version_check": "not_checked",
        "layout_check": "not_checked",
        "graph_check": "not_checked",
        "aggregate_check": "not_checked",
        "authority_boundary_check": "not_checked",
        "guardrail_check": "not_checked",
        "path_free_check": "not_checked",
        "node_count_checked": 0,
        "edge_count_checked": 0,
        "shortcut_edge_count_checked": 0,
        "read_side_report_only": True,
        "manual_review_required": True,
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
        "blockers": [safe_code],
        "warnings": [],
    }


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(child) for child in value)
    return False


def _contains_forbidden_payload_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower().replace("-", "_")
            collapsed = normalized.replace("_", "")
            tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
            if (
                normalized not in _ALLOWED_INERT_KEY_NAMES
                and (
                    normalized in _FORBIDDEN_PAYLOAD_KEYS
                    or collapsed in _FORBIDDEN_PAYLOAD_KEYS
                    or bool(set(tokens) & _FORBIDDEN_KEY_TOKENS)
                )
            ):
                return True
            if _contains_forbidden_payload_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_payload_key(child) for child in value)
    return False


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_PATH_MARKER_RE.search(value))
    if isinstance(value, Mapping):
        return any(
            _contains_path_marker(str(key)) or _contains_path_marker(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_path_marker(child) for child in value)
    return False


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _int(value: Any) -> int:
    return value if _nonnegative_int(value) else 0


def _finite_ratio(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and item for item in value
    )


def _sorted_unique_string_list(value: Any) -> bool:
    return _string_list(value) and list(value) == sorted(set(value))


def _safe_code(value: str) -> str:
    cleaned = []
    for char in str(value).strip()[:180]:
        if char.isalnum() or char in "_.:-[]":
            cleaned.append(char)
        elif char.isspace():
            cleaned.append("_")
        else:
            cleaned.append("_")
    return "".join(cleaned).strip("_") or "invalid"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
