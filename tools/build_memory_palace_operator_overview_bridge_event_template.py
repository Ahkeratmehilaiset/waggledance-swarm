# SPDX-License-Identifier: BUSL-1.1
"""Build an inert bridge-event template for Memory Palace operator overviews.

The template consumes an already-built read-only operator overview and emits a
schema-valid handoff event for manual review. It does not append bridge events,
enqueue scheduler work, dispatch routes, call solvers, promote shortcuts, write
storage, include memory payloads, record local paths, access the network, skip
gates, or grant runtime authority.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_memory_palace_operator_overview import (  # noqa: E402
    CLAIM_LABEL,
    OVERVIEW_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


TEMPLATE_VERSION = "wd.v12.memory_palace_operator_overview.bridge_event_template.v0"
EVENT_STATUS = "memory_palace_operator_overview_ready"
PROOF_ID = "memory_palace_operator_overview_bridge_event_template_v0"


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


def _slash_wrapped(segment: str) -> str:
    return _joined("/", segment, "/")


AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,191}$")
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")
BACKSLASH = _chars(92)
SENSITIVE_TOKEN_PREFIX = _chars(80, 82, 73, 86, 65, 84, 69, 95)
PATH_MARKERS = (
    _joined("file", ":", "/", "/"),
    _slash_wrapped("home"),
    _slash_wrapped("python"),
    _slash_wrapped("users"),
    _slash_wrapped("workspace"),
    _slash_wrapped("workspaces"),
    _slash_wrapped("tmp"),
    _joined("waggledance", "-", "agent", "-", "worktrees"),
)
FORBIDDEN_OUTPUT_MARKERS = (
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
    _joined("C", ":", "/"),
    _joined("C", ":", BACKSLASH),
    _joined(BACKSLASH, BACKSLASH),
    _slash_wrapped("home"),
    _slash_wrapped("Users"),
    SENSITIVE_TOKEN_PREFIX,
    _joined("Author", "ization"),
    _joined("Bear", "er", " "),
    _joined("sec", "ret"),
    _joined("pass", "word"),
    _joined("generator", "URL"),
)
AUTHORITY_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "gate_skip_performed",
    "network_access_performed",
    "runtime_authority_granted",
    "memory_payload_included",
    "matched_values_included",
    "local_paths_recorded",
)
SHORTCUT_AUTHORITY_FALSE_FIELDS = (
    "runtime_route_changed",
    "storage_write_performed",
    "bridge_append_performed",
    "solver_call_performed",
    "scheduler_enqueue_performed",
    "promotion_performed",
    "gate_skip_performed",
    "network_access_performed",
)
READ_PATH_AUTHORITY_FALSE_FIELDS = (
    *SHORTCUT_AUTHORITY_FALSE_FIELDS,
    "memory_payload_included",
    "matched_values_included",
    "local_paths_recorded",
)
REQUIRED_TRUE_BOUNDARY_FIELDS = (
    "read_side_projection_only",
    "hierarchy_summary_ok",
    "component_authority_boundaries_false",
)
NO_OVERCLAIM_GUARDRAILS = (
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
EXTRA_TEMPLATE_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "artifact_payloads_included",
    "transport_added",
    "external_fetch_performed",
    "external_writes_applied",
)
ALLOWED_OVERVIEW_FIELDS = frozenset(
    {
        "overview_version",
        "ok",
        "claim_label",
        "component_versions",
        "source_projection_schema_version",
        "source_of_truth",
        "memory_ids",
        "hierarchy",
        "read_path_overview",
        "aggregate",
        "authority_boundary",
        "no_overclaim_guardrails",
        "blockers",
        "operator_interpretation",
    }
)
FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "matched_values",
        "payload",
        "raw_payload",
        "memory_payload",
        "path",
        "paths",
        "local_path",
        "local_paths",
        "source_refs",
        "url",
        "uri",
        "href",
    }
)
FORBIDDEN_INPUT_KEY_SUFFIXES = (
    "_payload",
    "_payloads",
    "_path",
    "_paths",
    "_url",
    "_urls",
    "_uri",
    "_uris",
    "_href",
    "_hrefs",
)
FORBIDDEN_INPUT_KEY_PREFIXES = (
    "raw_payload",
    "payload_",
)
SHORTCUT_NUMERIC_FIELDS = (
    "candidate_count",
    "shortcut_jump_candidate_count",
    "total_hierarchy_hops",
    "total_projected_shortcut_hops",
    "total_intermediate_hops_skipped",
    "max_intermediate_hops_skipped",
)


class SafeInputError(ValueError):
    """Raised when a local overview cannot be trusted."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operator-overview-json",
        "--overview-json",
        dest="operator_overview_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--to",
        default="operator,codex-lead-1,codex-tools-1,claude-rco-1,claude-rco-2",
        help="Comma-separated bridge targets for the template.",
    )
    parser.add_argument(
        "--severity",
        default="medium",
        choices=("", "low", "medium", "high"),
    )
    parser.add_argument("--role", default="lead-impl")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-06-09T00:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        overview = _load_json_report(args.operator_overview_json)
        report = build_memory_palace_operator_overview_bridge_event_template(
            overview=overview,
            agent_id=args.agent,
            task_id=args.task_id,
            to=args.to,
            severity=args.severity,
            role=args.role,
            run_id=args.run_id,
            session_id=args.session_id,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except SafeInputError as exc:
        report = _bridge_template_error_report(exc.code)
    except ValueError:
        report = _bridge_template_error_report("bridge_event_template_invalid")

    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False)
    if args.json:
        print(encoded)
    elif report["ok"]:
        print(
            json.dumps(
                report["bridge_event_template"],
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "Memory Palace operator overview bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_memory_palace_operator_overview_bridge_event_template(
    *,
    overview: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str = "operator,codex-lead-1,codex-tools-1,claude-rco-1,claude-rco-2",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a schema-valid bridge-event template without appending it."""

    if not isinstance(overview, Mapping):
        return _bridge_template_error_report("operator_overview_not_object")
    try:
        overview_text = json.dumps(overview, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return _bridge_template_error_report("operator_overview_not_json_safe")
    if _contains_non_finite(overview):
        return _bridge_template_error_report("operator_overview_contains_non_finite")
    if (
        _contains_path_marker(overview)
        or _forbidden_output_markers(overview_text)
        or _contains_forbidden_input_key(overview)
    ):
        return _bridge_template_error_report("operator_overview_not_path_free")

    input_error = _bridge_template_input_error(
        agent_id=agent_id,
        task_id=task_id,
        to=to,
        severity=severity,
        role=role,
        run_id=run_id,
        session_id=session_id,
    )
    if input_error is not None:
        return _bridge_template_error_report(input_error)
    targets, _ = _validate_bridge_targets(to)

    contract_blockers = _operator_overview_contract_blockers(overview)
    if contract_blockers:
        return _bridge_template_error_report(contract_blockers[0])

    aggregate = _mapping(overview.get("aggregate"))
    hierarchy = _mapping(overview.get("hierarchy"))
    shortcut_summary = _mapping(aggregate.get("shortcut_jump_summary"))
    overview_boundary = _mapping(overview.get("authority_boundary"))
    no_overclaim = _mapping(overview.get("no_overclaim_guardrails"))
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "source_overview_version": OVERVIEW_VERSION,
        "source_claim_label": CLAIM_LABEL,
        "memory_palace_operator_overview": {
            "overview_ok": True,
            "source_of_truth": "projection_only",
            "memory_count": _as_nonnegative_int(aggregate.get("memory_count")),
            "hierarchy_node_count": _as_nonnegative_int(
                aggregate.get("hierarchy_node_count")
            ),
            "hierarchy_root_count": _as_nonnegative_int(
                aggregate.get("hierarchy_root_count")
            ),
            "hierarchy_max_depth": _as_nonnegative_int(hierarchy.get("max_depth")),
            "total_candidate_count": _as_nonnegative_int(
                aggregate.get("total_candidate_count")
            ),
            "max_hierarchy_hops": _as_nonnegative_int(
                aggregate.get("max_hierarchy_hops")
            ),
            "max_intermediate_hops_skipped": _as_nonnegative_int(
                aggregate.get("max_intermediate_hops_skipped")
            ),
            "shortcut_jump_summary": {
                name: _as_nonnegative_int(shortcut_summary.get(name))
                for name in SHORTCUT_NUMERIC_FIELDS
            }
            | {
                "average_intermediate_hops_skipped": _nonnegative_float(
                    shortcut_summary.get("average_intermediate_hops_skipped")
                ),
                "hop_reduction_ratio": _nonnegative_float(
                    shortcut_summary.get("hop_reduction_ratio")
                ),
                "authority_boundary": {
                    name: _mapping(shortcut_summary.get("authority_boundary")).get(
                        name
                    )
                    is False
                    for name in SHORTCUT_AUTHORITY_FALSE_FIELDS
                },
            },
            "authority_boundary": {
                name: overview_boundary.get(name) is False
                for name in AUTHORITY_FALSE_FIELDS
            }
            | {
                name: overview_boundary.get(name) is True
                for name in REQUIRED_TRUE_BOUNDARY_FIELDS
            },
            "no_overclaim_guardrails": {
                name: no_overclaim.get(name) is True for name in NO_OVERCLAIM_GUARDRAILS
            },
            "blocker_count": 0,
            "payload_included": False,
            "local_paths_recorded": False,
            "matched_values_included": False,
        },
        "operator_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "runtime_authority_granted": False,
            "runtime_route_changed": False,
            "scheduler_enqueue_performed": False,
            "gate_skip_performed": False,
        },
        "template_only": True,
        "manual_review_required": True,
        **{name: False for name in AUTHORITY_FALSE_FIELDS},
        **{name: False for name in EXTRA_TEMPLATE_FALSE_FIELDS},
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": EVENT_STATUS,
        "severity": severity,
        "to": targets,
        "message": (
            "Memory Palace operator overview bridge-event template ready; "
            "manual_review_required=true; approval_granted=false; "
            "release_decision_made=false; template_only=true; no bridge write, "
            "scheduler enqueue, route change, solver call, promotion, storage "
            "write, gate skip, payload inclusion, local path recording, network "
            "access, or runtime authority grant."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "implementation",
            "memory_palace",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    if _contains_path_marker(event):
        return _bridge_template_error_report("bridge_event_template_path_marker")
    try:
        _assert_no_forbidden_output(json.dumps(event, allow_nan=False, sort_keys=True))
        validate_event(event)
    except Exception:
        return _bridge_template_error_report("bridge_event_schema_invalid")
    return {
        "proof_id": PROOF_ID,
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        **{name: False for name in AUTHORITY_FALSE_FIELDS},
        **{name: False for name in EXTRA_TEMPLATE_FALSE_FIELDS},
        "blockers": [],
        "warnings": [],
    }


def _operator_overview_contract_blockers(overview: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if set(overview) - ALLOWED_OVERVIEW_FIELDS:
        blockers.append("operator_overview_unexpected_field_present")
    if overview.get("overview_version") != OVERVIEW_VERSION:
        blockers.append("operator_overview_version_mismatch")
    if overview.get("claim_label") != CLAIM_LABEL:
        blockers.append("operator_overview_claim_label_mismatch")
    if overview.get("ok") is not True:
        blockers.append("operator_overview_not_ok")
    if overview.get("source_of_truth") != "projection_only":
        blockers.append("operator_overview_source_of_truth_mismatch")

    raw_blockers = overview.get("blockers")
    if not isinstance(raw_blockers, list):
        blockers.append("operator_overview_blockers_invalid")
    elif any(not isinstance(item, str) for item in raw_blockers):
        blockers.append("operator_overview_blockers_invalid")
    elif raw_blockers:
        blockers.append("operator_overview_blockers_present")

    if not _non_empty_string_sequence(overview.get("memory_ids")):
        blockers.append("operator_overview_memory_ids_invalid")
    if not isinstance(overview.get("read_path_overview"), list):
        blockers.append("operator_overview_read_path_overview_invalid")

    hierarchy = _mapping(overview.get("hierarchy"))
    aggregate = _mapping(overview.get("aggregate"))
    shortcut_summary = _mapping(aggregate.get("shortcut_jump_summary"))
    for name in ("node_count", "root_count", "max_depth"):
        if _as_nonnegative_int(hierarchy.get(name)) < 0:
            blockers.append(f"operator_overview_hierarchy_{name}_invalid")
    for name in (
        "memory_count",
        "hierarchy_node_count",
        "hierarchy_root_count",
        "total_candidate_count",
        "max_hierarchy_hops",
        "max_intermediate_hops_skipped",
    ):
        if _as_nonnegative_int(aggregate.get(name)) < 0:
            blockers.append(f"operator_overview_aggregate_{name}_invalid")
    for name in SHORTCUT_NUMERIC_FIELDS:
        if _as_nonnegative_int(shortcut_summary.get(name)) < 0:
            blockers.append(f"operator_overview_shortcut_{name}_invalid")
    ratio = _nonnegative_float(shortcut_summary.get("hop_reduction_ratio"))
    if ratio < 0.0 or ratio > 1.0:
        blockers.append("operator_overview_shortcut_hop_reduction_ratio_invalid")
    if _nonnegative_float(shortcut_summary.get("average_intermediate_hops_skipped")) < 0:
        blockers.append("operator_overview_shortcut_average_skipped_invalid")
    if (
        _as_nonnegative_int(aggregate.get("total_candidate_count"))
        != _as_nonnegative_int(shortcut_summary.get("candidate_count"))
    ):
        blockers.append("operator_overview_candidate_count_mismatch")

    boundary = _mapping(overview.get("authority_boundary"))
    for name in AUTHORITY_FALSE_FIELDS:
        if boundary.get(name) is not False:
            blockers.append(f"operator_overview_{name}_not_false")
    for name in REQUIRED_TRUE_BOUNDARY_FIELDS:
        if boundary.get(name) is not True:
            blockers.append(f"operator_overview_{name}_not_true")

    shortcut_boundary = _mapping(shortcut_summary.get("authority_boundary"))
    for name in SHORTCUT_AUTHORITY_FALSE_FIELDS:
        if shortcut_boundary.get(name) is not False:
            blockers.append(f"operator_overview_shortcut_{name}_not_false")

    no_overclaim = _mapping(overview.get("no_overclaim_guardrails"))
    for name in NO_OVERCLAIM_GUARDRAILS:
        if no_overclaim.get(name) is not True:
            blockers.append(f"operator_overview_{name}_not_true")

    for index, row in enumerate(_sequence(overview.get("read_path_overview")) or ()):
        row_boundary = _mapping(_mapping(row).get("authority_boundary"))
        for name in READ_PATH_AUTHORITY_FALSE_FIELDS:
            if row_boundary.get(name) is not False:
                blockers.append(f"operator_overview_row_{index}_{name}_not_false")

    return sorted(set(blockers))


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("operator_overview_unreadable") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SafeInputError("operator_overview_decode_error") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SafeInputError("operator_overview_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("operator_overview_not_object")
    if _contains_non_finite(parsed):
        raise SafeInputError("operator_overview_json_error")
    return parsed


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _bridge_template_error_report(reason: str) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "manual_review_required": True,
        **{name: False for name in AUTHORITY_FALSE_FIELDS},
        **{name: False for name in EXTRA_TEMPLATE_FALSE_FIELDS},
        "blockers": [
            "memory_palace_operator_overview_bridge_event_template_failed:"
            f"{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any] | None:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return value
    return None


def _non_empty_string_sequence(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return -1
    return value if value >= 0 else -1


def _nonnegative_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return -1.0
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return -1.0
    return number


def _validate_bridge_agent_id(label: str, value: Any) -> str | None:
    if isinstance(value, str) and AGENT_ID_PATTERN.fullmatch(value):
        return None
    return f"{label}_unsafe"


def _validate_bridge_targets(raw_targets: Any) -> tuple[str, str | None]:
    if not isinstance(raw_targets, str):
        return "", "to_unsafe"
    targets = [item.strip() for item in raw_targets.split(",") if item.strip()]
    if not targets:
        return "", "to_unsafe"
    for target in targets:
        error = _validate_bridge_agent_id("to", target)
        if error is not None:
            return "", error
    return ",".join(targets), None


def _bridge_template_input_error(
    *,
    agent_id: str,
    task_id: str,
    to: str,
    severity: str,
    role: str,
    run_id: str,
    session_id: str,
) -> str | None:
    error = _validate_bridge_agent_id("agent", agent_id)
    if error is not None:
        return error
    if not isinstance(task_id, str) or not SAFE_REF_PATTERN.fullmatch(task_id):
        return "task_id_unsafe"
    _, target_error = _validate_bridge_targets(to)
    if target_error is not None:
        return target_error
    if severity not in {"", "low", "medium", "high"}:
        return "severity_unsafe"
    if role:
        error = _validate_bridge_agent_id("role", role)
        if error is not None:
            return error
    if run_id and not SAFE_REF_PATTERN.fullmatch(run_id):
        return "run_id_unsafe"
    if session_id and not SESSION_ID_PATTERN.fullmatch(session_id):
        return "session_id_unsafe"
    return None


def _contains_non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, Mapping):
        return any(_contains_non_finite(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_non_finite(item) for item in value)
    return False


def _contains_forbidden_input_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _is_forbidden_input_key(key) or _contains_forbidden_input_key(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_input_key(item) for item in value)
    return False


def _is_forbidden_input_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    normalized = key.strip().lower().replace("-", "_")
    if normalized in FORBIDDEN_INPUT_KEYS:
        return True
    if normalized.startswith(FORBIDDEN_INPUT_KEY_PREFIXES):
        return True
    return normalized.endswith(FORBIDDEN_INPUT_KEY_SUFFIXES)


def _contains_path_marker(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        return (
            WINDOWS_DRIVE_PATH_PATTERN.search(value) is not None
            or normalized.startswith("//")
            or any(marker in normalized for marker in PATH_MARKERS)
        )
    if isinstance(value, Mapping):
        return any(
            _contains_path_marker(key) or _contains_path_marker(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_path_marker(item) for item in value)
    return False


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    found = _forbidden_output_markers(text)
    if found:
        raise ValueError(
            "sanitized bridge-event template contains forbidden markers: "
            + ", ".join(found)
        )


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise SafeInputError("now_utc_unsafe")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise SafeInputError("now_utc_unsafe") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise SafeInputError("now_utc_unsafe")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
