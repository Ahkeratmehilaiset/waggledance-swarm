# SPDX-License-Identifier: BUSL-1.1
"""Verify a Memory Palace hierarchy map summary.

The verifier is read-only and path-free. It checks that a hierarchy map summary
emitted by ``build_memory_palace_hierarchy_map_summary`` remains a review-only
artifact with internally consistent counts and no runtime, solver, scheduler,
storage, bridge, promotion, gate-skip, network, path, or payload authority.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_memory_palace_hierarchy_map_summary import (  # noqa: E402
    CLAIM_LABEL as SOURCE_CLAIM_LABEL,
    SUMMARY_VERSION as SOURCE_SUMMARY_VERSION,
)
from waggledance.core.memory_palace import (  # noqa: E402
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
)


VERIFICATION_VERSION = "wd.v12.memory_palace_hierarchy_map_summary.verification.v0"
SUMMARY_ARTIFACT_ID = "memory_palace_hierarchy_map_summary"

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
    "artifact_payloads_included",
    "local_paths_recorded",
)
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "raw_payload",
        "payload",
        "artifact_payload",
        "artifact_payloads",
        "source_payload",
    }
)
_FORBIDDEN_PATH_KEYS = frozenset(
    {
        "path",
        "local_path",
        "source_path",
        "file_path",
        "artifact_path",
    }
)
_PATH_MARKER_RE = re.compile(
    r"([A-Za-z]:[\\/]|\\\\|file://|(?<![:/])/(?:[A-Za-z0-9._-]+/)+[A-Za-z0-9._-]*)",
    re.IGNORECASE,
)


class HierarchyMapSummaryVerificationError(ValueError):
    """Raised when verifier inputs cannot be safely loaded."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-json",
        required=True,
        type=Path,
        help="Path to a JSON report emitted by the hierarchy map summary tool.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = _load_json_report(args.summary_json)
        verification = verify_memory_palace_hierarchy_map_summary(summary)
    except HierarchyMapSummaryVerificationError as exc:
        verification = _failure_report(
            f"{SUMMARY_ARTIFACT_ID}_verification_failed:{exc.code}"
        )

    encoded = json.dumps(verification, indent=2, sort_keys=True, allow_nan=False)
    if args.json or verification["ok"]:
        print(encoded)
    else:
        print(
            "Memory Palace hierarchy map summary verification FAILED: "
            + ", ".join(verification["blockers"]),
            file=sys.stderr,
        )
    return 0 if verification["ok"] else 1


def verify_memory_palace_hierarchy_map_summary(
    summary: Any,
) -> dict[str, Any]:
    """Return an action-free verification report for a hierarchy map summary."""

    if not isinstance(summary, Mapping):
        return _failure_report(f"{SUMMARY_ARTIFACT_ID}_not_object")

    blockers: list[str] = []
    if _contains_non_finite(summary):
        blockers.append("summary_contains_non_finite")
    if _contains_forbidden_payload_key(summary):
        blockers.append("forbidden_payload_key_present")
    if _contains_forbidden_path_key(summary):
        blockers.append("forbidden_path_key_present")
    if _contains_path_marker(summary):
        blockers.append("forbidden_path_marker_present")

    _collect_header_blockers(summary, blockers)
    counts_check = _collect_count_blockers(summary, blockers)
    roots_check = _collect_root_blockers(summary, blockers)
    coverage_check = _collect_coverage_blockers(summary, blockers)
    authority_boundary_check = _collect_authority_boundary_blockers(
        summary,
        blockers,
    )
    path_free_check = (
        "match"
        if not {
            "forbidden_payload_key_present",
            "forbidden_path_key_present",
            "forbidden_path_marker_present",
        }
        & set(blockers)
        else "mismatch"
    )

    verification = {
        "ok": not blockers,
        "verification_version": VERIFICATION_VERSION,
        "source_summary_version_check": (
            "match"
            if summary.get("summary_version") == SOURCE_SUMMARY_VERSION
            else "mismatch"
        ),
        "source_claim_label_check": (
            "match"
            if summary.get("claim_label") == SOURCE_CLAIM_LABEL
            else "mismatch"
        ),
        "source_summary_ok": summary.get("ok") is True,
        "source_of_truth_check": (
            "match"
            if summary.get("source_of_truth") == "projection_only"
            else "mismatch"
        ),
        "counts_check": counts_check,
        "roots_check": roots_check,
        "coverage_check": coverage_check,
        "authority_boundary_check": authority_boundary_check,
        "path_free_check": path_free_check,
        "node_count_checked": _int(summary.get("node_count")),
        "root_count_checked": _int(summary.get("root_count")),
        "max_depth_checked": _int(summary.get("max_depth")),
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


def _collect_header_blockers(
    summary: Mapping[str, Any],
    blockers: list[str],
) -> None:
    if summary.get("summary_version") != SOURCE_SUMMARY_VERSION:
        blockers.append("summary_version_mismatch")
    if summary.get("claim_label") != SOURCE_CLAIM_LABEL:
        blockers.append("claim_label_mismatch")
    if summary.get("ok") is not True:
        blockers.append("source_summary_not_ok")
    if summary.get("source_projection_schema_version") != (
        MEMORY_PALACE_PROJECTION_SCHEMA_VERSION
    ):
        blockers.append("source_projection_schema_version_mismatch")
    if summary.get("source_of_truth") != "projection_only":
        blockers.append("source_of_truth_not_projection_only")
    source_blockers = summary.get("blockers")
    if source_blockers != []:
        blockers.append("source_summary_blockers_present")


def _collect_count_blockers(
    summary: Mapping[str, Any],
    blockers: list[str],
) -> str:
    node_count = summary.get("node_count")
    root_count = summary.get("root_count")
    max_depth = summary.get("max_depth")
    kind_counts = summary.get("kind_counts")
    roots = summary.get("roots")
    ok = True
    for field, value in (
        ("node_count", node_count),
        ("root_count", root_count),
        ("max_depth", max_depth),
    ):
        if not _nonnegative_int(value):
            blockers.append(f"{field}_not_nonnegative_int")
            ok = False
    if isinstance(node_count, int) and isinstance(root_count, int):
        if root_count > node_count:
            blockers.append("root_count_exceeds_node_count")
            ok = False
    if not isinstance(kind_counts, Mapping):
        blockers.append("kind_counts_not_object")
        ok = False
    else:
        kind_total = 0
        for key, value in kind_counts.items():
            if not isinstance(key, str) or not key:
                blockers.append("kind_counts_key_invalid")
                ok = False
                continue
            if not _nonnegative_int(value):
                blockers.append("kind_count_not_nonnegative_int")
                ok = False
                continue
            kind_total += value
        if isinstance(node_count, int) and kind_total != node_count:
            blockers.append("kind_counts_total_mismatch")
            ok = False
    if isinstance(roots, list) and isinstance(root_count, int):
        if len(roots) != root_count:
            blockers.append("root_count_mismatch")
            ok = False
    return "match" if ok else "mismatch"


def _collect_root_blockers(
    summary: Mapping[str, Any],
    blockers: list[str],
) -> str:
    roots = summary.get("roots")
    if not isinstance(roots, list):
        blockers.append("roots_not_list")
        return "mismatch"
    ok = True
    seen: set[str] = set()
    for index, root in enumerate(roots):
        if not isinstance(root, Mapping):
            blockers.append(f"root_{index}_not_object")
            ok = False
            continue
        node_id = root.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            blockers.append(f"root_{index}_node_id_invalid")
            ok = False
            continue
        if node_id in seen:
            blockers.append(f"root_{index}_duplicate_node_id")
            ok = False
        seen.add(node_id)
        for field in (
            "child_count",
            "descendant_count",
            "placement_count",
            "shortcut_source_count",
            "shortcut_target_count",
            "node_count_in_subtree",
        ):
            if not _nonnegative_int(root.get(field)):
                blockers.append(f"root_{index}_{field}_not_nonnegative_int")
                ok = False
        direct_children = root.get("direct_child_node_ids")
        descendants = root.get("sample_descendant_node_ids")
        ancestry = root.get("ancestry_node_ids")
        if not _string_list(direct_children):
            blockers.append(f"root_{index}_direct_child_node_ids_invalid")
            ok = False
        elif root.get("child_count") != len(direct_children):
            blockers.append(f"root_{index}_child_count_mismatch")
            ok = False
        if not _string_list(descendants):
            blockers.append(f"root_{index}_sample_descendant_node_ids_invalid")
            ok = False
        if not _string_list(ancestry) or list(ancestry) != [node_id]:
            blockers.append(f"root_{index}_ancestry_node_ids_invalid")
            ok = False
        if (
            _nonnegative_int(root.get("node_count_in_subtree"))
            and _nonnegative_int(root.get("descendant_count"))
            and root["node_count_in_subtree"] != root["descendant_count"] + 1
        ):
            blockers.append(f"root_{index}_subtree_count_mismatch")
            ok = False
    return "match" if ok else "mismatch"


def _collect_coverage_blockers(
    summary: Mapping[str, Any],
    blockers: list[str],
) -> str:
    coverage = summary.get("coverage")
    if not isinstance(coverage, Mapping):
        blockers.append("coverage_not_object")
        return "mismatch"
    ok = True
    for field in (
        "placement_count",
        "shortcut_hint_count",
        "long_shortcut_hint_count",
    ):
        if not _nonnegative_int(coverage.get(field)):
            blockers.append(f"coverage_{field}_not_nonnegative_int")
            ok = False
    if (
        _nonnegative_int(coverage.get("long_shortcut_hint_count"))
        and _nonnegative_int(coverage.get("shortcut_hint_count"))
        and coverage["long_shortcut_hint_count"] > coverage["shortcut_hint_count"]
    ):
        blockers.append("coverage_long_shortcut_count_exceeds_shortcut_count")
        ok = False
    for field in ("node_ids_with_placements", "node_ids_with_shortcuts"):
        values = coverage.get(field)
        if not _sorted_unique_string_list(values):
            blockers.append(f"coverage_{field}_invalid")
            ok = False
    placement_nodes = coverage.get("node_ids_with_placements")
    if (
        _nonnegative_int(coverage.get("placement_count"))
        and _sorted_unique_string_list(placement_nodes)
        and coverage["placement_count"] < len(placement_nodes)
    ):
        blockers.append("coverage_placement_count_less_than_placement_nodes")
        ok = False
    shortcut_nodes = coverage.get("node_ids_with_shortcuts")
    if (
        _nonnegative_int(coverage.get("shortcut_hint_count"))
        and _sorted_unique_string_list(shortcut_nodes)
        and coverage["shortcut_hint_count"] * 2 < len(shortcut_nodes)
    ):
        blockers.append("coverage_shortcut_hint_count_too_low_for_shortcut_nodes")
        ok = False
    return "match" if ok else "mismatch"


def _collect_authority_boundary_blockers(
    summary: Mapping[str, Any],
    blockers: list[str],
) -> str:
    boundary = summary.get("authority_boundary")
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


def _load_json_report(path: Path) -> Any:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except OSError as exc:
        raise HierarchyMapSummaryVerificationError(
            f"{SUMMARY_ARTIFACT_ID}_unreadable",
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise HierarchyMapSummaryVerificationError(
            f"{SUMMARY_ARTIFACT_ID}_json_error",
        ) from exc


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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
        "source_summary_version_check": "not_checked",
        "source_claim_label_check": "not_checked",
        "source_summary_ok": False,
        "source_of_truth_check": "not_checked",
        "counts_check": "not_checked",
        "roots_check": "not_checked",
        "coverage_check": "not_checked",
        "authority_boundary_check": "not_checked",
        "path_free_check": "not_checked",
        "node_count_checked": 0,
        "root_count_checked": 0,
        "max_depth_checked": 0,
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
            if str(key) in _FORBIDDEN_PAYLOAD_KEYS:
                return True
            if _contains_forbidden_payload_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_payload_key(child) for child in value)
    return False


def _contains_forbidden_path_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _FORBIDDEN_PATH_KEYS:
                return True
            if _contains_forbidden_path_key(child):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_path_key(child) for child in value)
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


def _nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _int(value: Any) -> int:
    return value if _nonnegative_int(value) else 0


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
