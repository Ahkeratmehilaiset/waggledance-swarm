# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only Memory Palace read-path preview.

The preview consumes an existing Memory Palace projection and a memory id,
then renders the source palace path plus ranked distant shortcut targets. It
is a product-facing read view only: it does not mutate memory, route runtime
traffic, call solvers, enqueue scheduler work, append bridge events, access
the network, promote shortcuts, or grant gate authority.
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

from waggledance.core.memory_palace import (  # noqa: E402
    MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
    MemoryPalaceProjectionError,
    build_memory_palace_navigation_index,
    build_memory_palace_projection,
    rank_shortcut_candidates_for_memory,
)


PREVIEW_VERSION = "wd.v12.memory_palace_read_path_preview.v0"
CLAIM_LABEL = "READ_ONLY_MEMORY_PALACE_READ_PATH"

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
    parser.add_argument("--memory-id", required=True)
    parser.add_argument("--max-candidates", type=int, default=3)
    parser.add_argument("--min-rank-score", type=float, default=0.0)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        projection = _load_json(args.projection_json)
    except ValueError as exc:
        report = _failure_preview(str(exc))
    else:
        report = build_memory_palace_read_path_preview(
            projection,
            args.memory_id,
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
                "Memory Palace read-path preview FAILED: "
                + ", ".join(report["blockers"]),
                file=sys.stderr,
            )
    return 0 if report["ok"] else 1


def build_memory_palace_read_path_preview(
    projection: Any,
    memory_id: str,
    *,
    max_candidates: int = 3,
    min_rank_score: float = 0.0,
) -> dict[str, Any]:
    """Return a deterministic, authority-free read-path preview."""

    blockers: list[str] = []
    if not isinstance(projection, Mapping):
        return _failure_preview("projection_not_object")
    if not isinstance(memory_id, str) or not memory_id.strip():
        return _failure_preview("memory_id_not_non_empty_string")
    if _contains_path_marker(memory_id):
        return _failure_preview("memory_id_contains_path_marker")

    if _contains_non_finite(projection):
        blockers.append("projection_contains_non_finite")
    if _contains_forbidden_key_or_path_marker(projection):
        blockers.append("projection_contains_forbidden_payload_or_path_marker")
    if projection.get("schema_version") != MEMORY_PALACE_PROJECTION_SCHEMA_VERSION:
        blockers.append("projection_schema_version_mismatch")
    if projection.get("source_of_truth") != "projection_only":
        blockers.append("projection_source_of_truth_not_projection_only")
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
        return _failure_preview(*blockers)

    try:
        validated_projection = build_memory_palace_projection(
            nodes,
            placements=placements,
            shortcuts=shortcuts,
        )
        navigation_index = build_memory_palace_navigation_index(
            validated_projection["nodes"]
        )
        candidates = rank_shortcut_candidates_for_memory(
            validated_projection,
            memory_id,
            max_candidates=max_candidates,
            min_rank_score=min_rank_score,
        )
    except MemoryPalaceProjectionError as exc:
        return _failure_preview(f"projection_validation_failed:{_safe_code(str(exc))}")

    matching_placements = [
        placement
        for placement in _sequence(validated_projection.get("placements")) or ()
        if _string_field(placement, "memory_id") == memory_id
    ]
    if not matching_placements:
        return _failure_preview("memory_id_not_placed")

    source_node_id = _source_node_id(matching_placements, candidates)
    source_path = _path_preview(navigation_index, source_node_id)
    read_paths = [
        _candidate_preview(index, candidate, navigation_index)
        for index, candidate in enumerate(candidates, start=1)
    ]
    top = read_paths[0] if read_paths else {}
    report = {
        "preview_version": PREVIEW_VERSION,
        "ok": True,
        "claim_label": CLAIM_LABEL,
        "source_projection_schema_version": MEMORY_PALACE_PROJECTION_SCHEMA_VERSION,
        "source_of_truth": "projection_only",
        "memory_id": memory_id,
        "source": source_path,
        "summary": {
            "placement_count_for_memory": len(matching_placements),
            "candidate_count": len(read_paths),
            "top_target_node_id": top.get("target_node_id", ""),
            "top_rank_score": top.get("rank_score", 0.0),
        },
        "ranked_read_paths": read_paths,
        "authority_boundary": _authority_boundary(
            validated_projection,
            navigation_index,
            read_paths,
        ),
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
            "This preview can orient a human or agent from an existing memory "
            "placement toward ranked distant Memory Palace rooms. It is not a "
            "runtime route, solver dispatch, storage mutation, bridge append, "
            "scheduler enqueue, promotion, or gate decision."
        ),
    }
    json.dumps(report, sort_keys=True, allow_nan=False)
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    source = _mapping(report.get("source"))
    summary = _mapping(report.get("summary"))
    lines = [
        "# Memory Palace Read Path Preview",
        "",
        f"- preview_version: `{report['preview_version']}`",
        f"- ok: `{str(report['ok']).lower()}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- source_of_truth: `{report['source_of_truth']}`",
        f"- memory_id: `{report['memory_id']}`",
        "",
        "## Source",
        "",
        f"- node_id: `{source.get('node_id', '')}`",
        "- path: `" + " / ".join(_string_sequence(source.get("path_node_ids"))) + "`",
        "",
        "## Ranked Read Paths",
        "",
        "| rank | target | score | hierarchy hops | skipped intermediate hops |",
        "| ---: | --- | ---: | ---: | ---: |",
    ]
    for candidate in _sequence(report.get("ranked_read_paths")) or ():
        candidate_map = _mapping(candidate)
        lines.append(
            "| {rank} | `{target}` | {score} | {hops} | {skipped} |".format(
                rank=candidate_map.get("rank", 0),
                target=candidate_map.get("target_node_id", ""),
                score=candidate_map.get("rank_score", 0.0),
                hops=candidate_map.get("hierarchy_hops", 0),
                skipped=candidate_map.get("intermediate_hops_skipped", 0),
            )
        )
    if not (_sequence(report.get("ranked_read_paths")) or ()):
        lines.append("| 0 | `none` | 0.0 | 0 | 0 |")

    lines.extend(
        [
            "",
            "## Summary",
            "",
            (
                "- placement_count_for_memory: `"
                + str(summary.get("placement_count_for_memory", 0))
                + "`"
            ),
            "- candidate_count: `" + str(summary.get("candidate_count", 0)) + "`",
            "- top_target_node_id: `" + str(summary.get("top_target_node_id", "")) + "`",
            "",
            "## Boundary",
            "",
        ]
    )
    for key, value in sorted(_mapping(report.get("authority_boundary")).items()):
        lines.append(f"- {key}: `{str(value).lower()}`")
    lines.extend(["", report["operator_interpretation"], ""])
    return "\n".join(lines)


def _candidate_preview(
    rank: int,
    candidate: Mapping[str, Any],
    navigation_index: Mapping[str, Any],
) -> dict[str, Any]:
    hierarchy_hops = _int_field(candidate, "hierarchy_hops")
    target_node_id = _string_field(candidate, "target_node_id")
    return {
        "rank": rank,
        "shortcut_id": _string_field(candidate, "shortcut_id"),
        "source_node_id": _string_field(candidate, "source_node_id"),
        "target_node_id": target_node_id,
        "target": _path_preview(navigation_index, target_node_id),
        "rank_score": _float_field(candidate, "rank_score"),
        "placement_confidence": _float_field(candidate, "placement_confidence"),
        "shortcut_confidence": _float_field(candidate, "shortcut_confidence"),
        "hierarchy_hops": hierarchy_hops,
        "projected_shortcut_hops": 1 if hierarchy_hops else 0,
        "intermediate_hops_skipped": max(0, hierarchy_hops - 1),
        "matched_selector_keys": _string_sequence(
            candidate.get("matched_selector_keys")
        ),
        "matched_value_count_by_key": _matched_value_counts(
            _mapping(candidate.get("matched_values"))
        ),
        "authority_boundary": {
            "no_runtime_mutation": candidate.get("no_runtime_mutation") is True,
            "runtime_authority": False,
            "storage_write_authority": False,
            "bridge_write_authority": False,
            "gate_skip_authority": False,
            "promotion_authority": False,
            "solver_call_authority": False,
        },
    }


def _path_preview(
    navigation_index: Mapping[str, Any],
    node_id: str,
) -> dict[str, Any]:
    entries = {
        _string_field(entry, "node_id"): entry
        for entry in _sequence(navigation_index.get("nodes")) or ()
        if isinstance(entry, Mapping)
    }
    entry = _mapping(entries.get(node_id))
    paths_by_node = _mapping(navigation_index.get("paths_by_node"))
    path_node_ids = _string_sequence(paths_by_node.get(node_id))
    path_labels = _string_sequence(entry.get("path_labels"))
    return {
        "node_id": node_id,
        "kind": _string_field(entry, "kind"),
        "label": _string_field(entry, "label"),
        "path_node_ids": path_node_ids,
        "path_labels": path_labels,
        "depth": _int_field(entry, "depth"),
    }


def _authority_boundary(
    projection: Mapping[str, Any],
    navigation_index: Mapping[str, Any],
    read_paths: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    projection_flags_false = all(
        projection.get(field_name) is False
        for field_name in (
            "runtime_authority",
            "storage_write_authority",
            "bridge_write_authority",
            "gate_skip_authority",
            "promotion_authority",
        )
    )
    navigation_flags_false = all(
        navigation_index.get(field_name) is False
        for field_name in (
            "runtime_authority",
            "storage_write_authority",
            "bridge_write_authority",
            "gate_skip_authority",
            "promotion_authority",
            "solver_call_authority",
        )
    )
    candidate_flags_false = all(
        _mapping(candidate.get("authority_boundary")).get(field_name) is False
        for candidate in read_paths
        for field_name in (
            "runtime_authority",
            "storage_write_authority",
            "bridge_write_authority",
            "gate_skip_authority",
            "promotion_authority",
            "solver_call_authority",
        )
    )
    return {
        "read_side_projection_only": True,
        "projection_authority_flags_false": projection_flags_false,
        "navigation_authority_flags_false": navigation_flags_false,
        "candidate_authority_flags_false": candidate_flags_false,
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


def _source_node_id(
    placements: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> str:
    if candidates:
        return _string_field(candidates[0], "source_node_id")
    ranked_placements = sorted(
        placements,
        key=lambda item: (
            -_float_field(item, "confidence"),
            _string_field(item, "palace_node_id"),
        ),
    )
    return _string_field(ranked_placements[0], "palace_node_id")


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


def _failure_preview(*blockers: str) -> dict[str, Any]:
    safe_blockers = sorted({_safe_code(blocker) for blocker in blockers if blocker})
    if not safe_blockers:
        safe_blockers = ["unknown_failure"]
    return {
        "preview_version": PREVIEW_VERSION,
        "ok": False,
        "claim_label": CLAIM_LABEL,
        "source_projection_schema_version": "",
        "source_of_truth": "",
        "memory_id": "",
        "source": {
            "node_id": "",
            "kind": "",
            "label": "",
            "path_node_ids": [],
            "path_labels": [],
            "depth": 0,
        },
        "summary": {
            "placement_count_for_memory": 0,
            "candidate_count": 0,
            "top_target_node_id": "",
            "top_rank_score": 0.0,
        },
        "ranked_read_paths": [],
        "authority_boundary": {
            "read_side_projection_only": False,
            "projection_authority_flags_false": False,
            "navigation_authority_flags_false": False,
            "candidate_authority_flags_false": False,
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
            "The read-path preview failed closed and grants no runtime authority."
        ),
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


def _authority_violations(value: Any, path: str = "$") -> list[str]:
    violations: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in _AUTHORITY_FIELDS and child is not False:
                violations.append(str(key))
            violations.extend(_authority_violations(child, path))
    elif isinstance(value, list):
        for child in value:
            violations.extend(_authority_violations(child, path))
    return violations


def _matched_value_counts(value: Mapping[str, Any]) -> dict[str, int]:
    return {
        str(key): len(_string_sequence(child))
        for key, child in sorted(value.items(), key=lambda item: str(item[0]))
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
