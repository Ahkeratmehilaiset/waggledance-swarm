#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a runtime gap scheduler-candidate bridge-event template.

The template consumes a no-authority scheduler-candidate preview artifact and
renders a bridge event that can be reviewed or copied by an operator. It never
appends to the bridge, enqueues candidates, runs the scheduler, or grants
runtime authority.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.future_scale_contract_safety import (  # noqa: E402
    validate_exact_false_fields,
    validate_scalar_safety,
)
from waggledance.core.autonomy_growth.low_risk_policy import (  # noqa: E402
    is_low_risk_family,
)
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


TEMPLATE_VERSION = "wd.runtime_gap_scheduler_candidate_bridge_event_template.v1"
EVENT_STATUS = "runtime_gap_scheduler_candidate_bridge_event_template_ready"
PROOF_ID = "runtime_gap_scheduler_candidate_bridge_event_template_v1"

ARTIFACT_VERSION = "wd.runtime_gap_scheduler_candidate_artifact.v1"
ARTIFACT_SCHEMA_VERSION = "runtime_gap_scheduler_candidate_artifact.v1"
ARTIFACT_MEASUREMENT_SCOPE = "local_read_only_scheduler_candidate_preview"
SOURCE_REPORT_VERSION = "wd.runtime_gap_detector_report.v1"
SOURCE_REPORT_SCHEMA_VERSION = "runtime_gap_detector_report.v1"
SOURCE_REPORT_MEASUREMENT_SCOPE = "local_read_only_gap_signal_report"

TOP_LEVEL_FALSE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
    "runtime_detector_record_called",
    "digest_signals_into_intents_called",
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "scheduler_tick_executed",
    "queue_writes_applied",
    "control_plane_writes_applied",
    "bridge_event_written",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "fast_track_priority",
)
CANDIDATE_FALSE_FIELDS = (
    "scheduler_enqueue_allowed",
    "scheduler_tick_allowed",
    "bridge_event_written",
    "runtime_authority_granted",
    "gate_skip_allowed",
    "promotion_gate_skip_allowed",
    "adversarial_gate_skip_allowed",
    "canary_gate_skip_allowed",
    "fast_track_priority",
    "raw_signal_payload_included",
    "raw_query_exported",
)

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
SAFE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"(?:^|[^A-Za-z0-9])(?:[A-Za-z]:[\\/])")


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


def _slash_wrapped(segment: str) -> str:
    return _joined("/", segment, "/")


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
    _joined("gpt", "-"),
    _joined("grok", "-"),
    _joined("hf", ":", "/", "/"),
)


class SafeInputError(ValueError):
    """Raised when local JSON or bridge template inputs are unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-json",
        "--candidate-artifact-json",
        dest="artifact_json",
        required=True,
        type=Path,
    )
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument(
        "--to",
        default="operator,claude-rco-1,codex-tools-1",
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
        help="Optional UTC timestamp override such as 2026-06-06T19:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = _load_json_report(args.artifact_json)
        report = build_runtime_gap_scheduler_candidate_bridge_event_template(
            artifact=artifact,
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
            "runtime gap scheduler-candidate bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_runtime_gap_scheduler_candidate_bridge_event_template(
    *,
    artifact: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str = "operator,claude-rco-1,codex-tools-1",
    severity: str = "medium",
    role: str = "lead-impl",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a valid bridge-event template without appending it."""

    if not isinstance(artifact, Mapping):
        return _bridge_template_error_report("scheduler_candidate_artifact_not_object")
    artifact_text = json.dumps(artifact, sort_keys=True, default=str)
    if _contains_path_marker(artifact) or _forbidden_output_markers(artifact_text):
        return _bridge_template_error_report("scheduler_candidate_artifact_not_path_free")

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

    artifact_dict = _plain_json_object(artifact)
    artifact_errors = validate_runtime_gap_scheduler_candidate_artifact(artifact_dict)
    if artifact_errors:
        return _bridge_template_error_report(artifact_errors[0])

    candidates = _mapping_list(artifact_dict.get("scheduler_candidates"))
    candidate_summaries = [_candidate_summary(candidate) for candidate in candidates]
    artifact_digest = sha256_digest(artifact_dict)
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "artifact_version": artifact_dict["artifact_version"],
        "artifact_schema_version": artifact_dict["schema_version"],
        "artifact_measurement_scope": artifact_dict["measurement_scope"],
        "artifact_digest": artifact_digest,
        "source_report_digest": artifact_dict["source_report_digest"],
        "source_report_version": artifact_dict["source_report_version"],
        "source_report_schema_version": artifact_dict[
            "source_report_schema_version"
        ],
        "source_report_measurement_scope": artifact_dict[
            "source_report_measurement_scope"
        ],
        "input_source_kind": _safe_token(artifact_dict.get("input_source_kind")),
        "scheduler_candidate_preview": {
            "artifact_verified": True,
            "artifact_path_free": True,
            "source_candidate_intent_count": _as_nonnegative_int(
                artifact_dict.get("source_candidate_intent_count")
            ),
            "source_scheduler_candidate_count": _as_nonnegative_int(
                artifact_dict.get("source_scheduler_candidate_count")
            ),
            "scheduler_candidate_count": len(candidate_summaries),
            "blocked_candidate_count": _as_nonnegative_int(
                artifact_dict.get("blocked_candidate_count")
            ),
            "candidate_digests": [
                candidate["candidate_digest"] for candidate in candidate_summaries
            ],
            "candidate_ids": [
                candidate["candidate_id"] for candidate in candidate_summaries
            ],
            "queue_priority_counts": _queue_priority_counts(candidates),
            "candidate_summaries": candidate_summaries,
            "raw_artifact_payload_included": False,
            "raw_signal_payload_included": False,
            "raw_query_exported": False,
        },
        "authority_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "claim_gate_satisfied": False,
            "claim_safe": False,
            "literal_future_claim_safe": False,
            "runtime_authority_granted": False,
            "required_runtime_evidence_present": False,
            "scheduler_enqueue_allowed": False,
            "scheduler_tick_allowed": False,
            "scheduler_tick_executed": False,
            "queue_writes_applied": False,
            "control_plane_writes_applied": False,
            "bridge_event_written": False,
            "gate_skip_allowed": False,
            "promotion_gate_skip_allowed": False,
            "adversarial_gate_skip_allowed": False,
            "canary_gate_skip_allowed": False,
            "fast_track_priority": False,
        },
        "template_only": True,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
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
            "Runtime gap scheduler-candidate bridge-event template ready; "
            f"scheduler_candidate_count={len(candidate_summaries)}; "
            "manual_review_required=true; approval_granted=false; "
            "scheduler_enqueue_allowed=false; scheduler_tick_allowed=false; "
            "bridge_event_written=false; fast_track_priority=false; "
            "gate_skip_allowed=false; template_only=true."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "autonomy_growth",
            "scheduler_candidate_preview",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    validate_event(event)
    _assert_no_forbidden_output(json.dumps(event, allow_nan=False, sort_keys=True))
    return {
        "proof_id": PROOF_ID,
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "manual_review_required": True,
        "direct_bridge_write_performed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "bridge_event_written": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_authority_granted": False,
        "fast_track_priority": False,
        "gate_skip_allowed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": [],
    }


def validate_runtime_gap_scheduler_candidate_artifact(
    artifact: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    artifact_dict = _plain_json_object_or_none(artifact)
    if artifact_dict is None:
        return ["artifact must be a JSON object"]

    errors.extend(validate_scalar_safety(artifact_dict))
    errors.extend(validate_exact_false_fields(artifact_dict, TOP_LEVEL_FALSE_FIELDS))
    if artifact_dict.get("artifact_version") != ARTIFACT_VERSION:
        errors.append("artifact_version mismatch")
    if artifact_dict.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    if artifact_dict.get("measurement_scope") != ARTIFACT_MEASUREMENT_SCOPE:
        errors.append("measurement_scope mismatch")
    if artifact_dict.get("source_report_version") != SOURCE_REPORT_VERSION:
        errors.append("source_report_version mismatch")
    if artifact_dict.get("source_report_schema_version") != SOURCE_REPORT_SCHEMA_VERSION:
        errors.append("source_report_schema_version mismatch")
    if artifact_dict.get("source_report_measurement_scope") != (
        SOURCE_REPORT_MEASUREMENT_SCOPE
    ):
        errors.append("source_report_measurement_scope mismatch")
    if not _is_sha256_ref(artifact_dict.get("source_report_digest")):
        errors.append("source_report_digest must be sha256 ref")
    if artifact_dict.get("artifact_path_free") is not True:
        errors.append("artifact_path_free must be true")
    if artifact_dict.get("no_cloud_api_calls") is not True:
        errors.append("no_cloud_api_calls must be true")
    if artifact_dict.get("no_model_pull_or_download") is not True:
        errors.append("no_model_pull_or_download must be true")

    candidates = artifact_dict.get("scheduler_candidates")
    if not isinstance(candidates, list):
        errors.append("scheduler_candidates must be a list")
        candidates = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, Mapping):
            errors.append(f"scheduler_candidates[{index}] must be an object")
            continue
        errors.extend(
            f"scheduler_candidates[{index}].{error}"
            for error in validate_exact_false_fields(
                _plain_json_object(candidate),
                CANDIDATE_FALSE_FIELDS,
            )
        )
        errors.extend(_candidate_contract_errors(index, candidate))

    if artifact_dict.get("scheduler_candidate_count") != len(candidates):
        errors.append("scheduler_candidate_count does not match scheduler_candidates")
    blocked_count = artifact_dict.get("blocked_candidate_count")
    if not isinstance(blocked_count, int) or isinstance(blocked_count, bool):
        errors.append("blocked_candidate_count must be int")
    elif blocked_count < 0:
        errors.append("blocked_candidate_count must be non-negative")
    source_count = artifact_dict.get("source_candidate_intent_count")
    if not isinstance(source_count, int) or isinstance(source_count, bool):
        errors.append("source_candidate_intent_count must be int")
    elif source_count < len(candidates):
        errors.append("source_candidate_intent_count below scheduler candidates")
    return errors


def _candidate_contract_errors(index: int, candidate: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if candidate.get("schema_version") != "runtime_gap_scheduler_candidate_preview.v1":
        errors.append(f"scheduler_candidates[{index}].schema_version mismatch")
    if candidate.get("candidate_kind") != "runtime_gap_signal_group":
        errors.append(f"scheduler_candidates[{index}].candidate_kind mismatch")
    if candidate.get("queue_priority") != "normal":
        errors.append(f"scheduler_candidates[{index}].queue_priority must be normal")
    if candidate.get("ready_for_scheduler_candidate") is not True:
        errors.append(
            f"scheduler_candidates[{index}].ready_for_scheduler_candidate must be true"
        )
    if candidate.get("blockers") != []:
        errors.append(f"scheduler_candidates[{index}] ready with blockers")
    if not is_low_risk_family(str(candidate.get("family_kind", ""))):
        errors.append(f"scheduler_candidates[{index}].family_kind not low-risk")
    for field in ("source_report_digest", "candidate_digest"):
        if not _is_sha256_ref(candidate.get(field)):
            errors.append(f"scheduler_candidates[{index}].{field} invalid")
    if not _is_digest_hex(candidate.get("spec_seed_digest")):
        errors.append(f"scheduler_candidates[{index}].spec_seed_digest invalid")
    if not _is_safe_candidate_id(candidate.get("candidate_id")):
        errors.append(f"scheduler_candidates[{index}].candidate_id invalid")
    for field in ("signal_count", "priority_weight"):
        value = candidate.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            errors.append(f"scheduler_candidates[{index}].{field} must be int")
        elif value <= 0:
            errors.append(f"scheduler_candidates[{index}].{field} must be positive")
    return errors


def _candidate_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": _safe_candidate_id(candidate.get("candidate_id")),
        "candidate_digest": _safe_digest_ref(candidate.get("candidate_digest")),
        "candidate_kind": "runtime_gap_signal_group",
        "intent_key": _safe_token(candidate.get("intent_key")),
        "family_kind": _safe_token(candidate.get("family_kind")),
        "cell_coord": _safe_token(candidate.get("cell_coord")),
        "signal_count": _as_nonnegative_int(candidate.get("signal_count")),
        "priority_weight": _as_nonnegative_int(candidate.get("priority_weight")),
        "queue_priority": "normal",
        "ready_for_scheduler_candidate": True,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "bridge_event_written": False,
        "runtime_authority_granted": False,
        "gate_skip_allowed": False,
        "fast_track_priority": False,
        "raw_signal_payload_included": False,
        "raw_query_exported": False,
    }


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("scheduler_candidate_artifact_unreadable") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non_finite_json_constant:{value}")

    try:
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=reject_constant,
        )
    except UnicodeDecodeError as exc:
        raise SafeInputError("scheduler_candidate_artifact_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise SafeInputError("scheduler_candidate_artifact_json_error") from exc
    except ValueError as exc:
        raise SafeInputError("scheduler_candidate_artifact_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("scheduler_candidate_artifact_not_object")
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
        "direct_bridge_write_performed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "bridge_event_written": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_authority_granted": False,
        "fast_track_priority": False,
        "gate_skip_allowed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "runtime_gap_scheduler_candidate_bridge_event_template_failed:"
            f"{_safe_reason(reason)}"
        ],
        "warnings": [],
    }


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


def _validate_bridge_targets(raw_targets: str) -> tuple[str, str | None]:
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


def _validate_bridge_agent_id(label: str, value: Any) -> str | None:
    if not isinstance(value, str) or not AGENT_ID_PATTERN.fullmatch(value):
        return f"{label}_unsafe"
    return None


def _queue_priority_counts(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for candidate in candidates:
        priority = _safe_token(candidate.get("queue_priority"))
        counts[priority] = counts.get(priority, 0) + 1
    return dict(sorted(counts.items()))


def _mapping_list(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _plain_json_object(value: Any) -> dict[str, Any]:
    result = _plain_json_object_or_none(value)
    if result is None:
        raise ValueError("value must be a JSON object")
    return result


def _plain_json_object_or_none(value: Any) -> dict[str, Any] | None:
    try:
        plain = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        return None
    if not isinstance(plain, dict):
        return None
    return plain


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


def _safe_token(value: Any, fallback: str = "invalid_token") -> str:
    if isinstance(value, str) and SAFE_TOKEN_PATTERN.fullmatch(value):
        return value
    return fallback


def _safe_reason(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9:._-]+", "_", str(value)).strip("_")
    if not text:
        return "invalid_reason"
    if not text[0].isalnum():
        text = "reason_" + text
    return text[:192]


def _safe_candidate_id(value: Any) -> str:
    if _is_safe_candidate_id(value):
        return str(value)
    return "invalid_candidate_id"


def _is_safe_candidate_id(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "runtime_gap_scheduler_candidate:"
    digest = value.removeprefix(prefix)
    return value.startswith(prefix) and _is_digest_hex(digest)


def _safe_digest_ref(value: Any) -> str:
    if _is_sha256_ref(value):
        return str(value)
    return "sha256:" + ("0" * 64)


def _is_sha256_ref(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == len("sha256:") + 64
        and value.startswith("sha256:")
        and _is_digest_hex(value[len("sha256:"):])
    )


def _is_digest_hex(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


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
