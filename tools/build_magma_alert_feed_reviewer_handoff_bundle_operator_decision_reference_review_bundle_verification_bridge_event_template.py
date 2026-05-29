# SPDX-License-Identifier: BUSL-1.1
"""Build a MAGMA operator decision-reference review bundle verification bridge-event template."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (  # noqa: E402
    BUNDLE_INDEX_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary import (  # noqa: E402
    SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)
from tools.verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (  # noqa: E402
    VERIFICATION_VERSION,
)


TEMPLATE_VERSION = "magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template.v1"

_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ARTIFACT_IDS = (
    "operator_decision_reference_validation",
    "operator_decision_reference_review_summary",
)
_AUTHORITY_FALSE_FIELDS = (
    "approval_granted",
    "release_decision_made",
    "automatic_release_decision",
    "direct_bridge_write_performed",
    "transport_added",
    "external_fetch_performed",
    "runtime_controls_added",
    "artifact_payloads_included",
    "local_paths_recorded",
)
_REFERENCE_FALSE_FIELDS = (
    "decision_reference_is_approval",
    "decision_reference_is_release_decision",
)


class SafeInputError(ValueError):
    """Raised when operator-provided bridge template fields are unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ContractError(ValueError):
    """Raised when the local verification summary is not template-safe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--review-bundle-verification-summary-json",
        "--verification-summary-json",
        dest="review_bundle_verification_summary_json",
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
    parser.add_argument("--role", default="reviewer")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-29T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = _load_json_report(args.review_bundle_verification_summary_json)
    except SafeInputError as exc:
        report = _failure_report(exc.code)
    else:
        try:
            report = build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template(
                summary=summary,
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
            report = _failure_report(exc.code)
        except ContractError as exc:
            report = _failure_report(exc.code)
        except ValueError:
            report = _failure_report(
                "operator_decision_reference_review_bundle_verification_bridge_event_template_invalid"
            )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report["bridge_event_template"], indent=2, sort_keys=True))
    else:
        print(
            "MAGMA operator decision-reference review bundle verification "
            "bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template(
    *,
    summary: Mapping[str, Any],
    agent_id: str,
    task_id: str,
    to: str,
    severity: str = "medium",
    role: str = "reviewer",
    run_id: str = "",
    session_id: str = "",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a valid bridge-event template without appending it."""

    if not isinstance(summary, Mapping):
        raise ContractError("verification_summary_not_mapping")
    _assert_no_forbidden_input("verification_summary", summary)
    _validate_agent_id("agent", agent_id)
    _validate_safe_ref("task_id", task_id)
    targets = _validate_targets(to)
    if severity not in {"", "low", "medium", "high"}:
        raise SafeInputError("severity_unsafe")
    if role:
        _validate_agent_id("role", role)
    if run_id:
        _validate_safe_ref("run_id", run_id)
    if session_id and not _SESSION_ID_RE.match(session_id):
        raise SafeInputError("session_id_unsafe")

    blockers = _summary_contract_blockers(summary)
    if blockers:
        raise ContractError(blockers[0])

    verification = _mapping(
        summary.get("operator_decision_reference_review_bundle_verification")
    )
    reference = _mapping(summary.get("operator_decision_reference_review"))
    reviewer = _mapping(summary.get("reviewer_ownership"))
    boundary = _mapping(summary.get("operator_boundary"))

    release_ref = _safe_ref_or_invalid(summary.get("release_ref"))
    commit_sha = _commit_or_invalid(summary.get("commit_sha"))
    ci_run_ref = _safe_ref_or_invalid(summary.get("ci_run_ref"))
    decision_ref = _safe_ref_or_invalid(reference.get("decision_reference"))
    expected_decision_ref = _safe_ref_or_invalid(
        reference.get("expected_decision_reference")
    )
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "summary_version": summary.get("summary_version"),
        "release_ref": release_ref,
        "commit_sha": commit_sha,
        "ci_run_ref": ci_run_ref,
        "reviewer_ownership": {
            "reviewer_agent_id": _safe_ref_or_invalid(
                reviewer.get("reviewer_agent_id")
            ),
            "handoff_ref": _safe_ref_or_invalid(reviewer.get("handoff_ref")),
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
        },
        "operator_decision_reference_review_bundle_verification": {
            "verification_ok": True,
            "verification_version": _safe_ref_or_invalid(
                verification.get("verification_version")
            ),
            "bundle_index_version": _safe_ref_or_invalid(
                verification.get("bundle_index_version")
            ),
            "artifact_count_checked": len(_ARTIFACT_IDS),
            "source_contract_check": "match",
            "rebuilt_index_check": "match",
            "digest_checks": _match_checks(verification.get("digest_checks")),
            "size_checks": _match_checks(verification.get("size_checks")),
            "schema_version_checks": _match_checks(
                verification.get("schema_version_checks")
            ),
            "blocker_count": 0,
            "warning_count": _as_nonnegative_int(verification.get("warning_count")),
        },
        "operator_decision_reference_review": {
            "decision_reference": decision_ref,
            "expected_decision_reference": expected_decision_ref,
            "decision_reference_verified": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "operator_boundary": {
            "verification_report_boundary_ok": bool(
                boundary.get("verification_report_boundary_ok") is True
            ),
            "manual_review_required": True,
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "direct_bridge_write_performed": False,
            "transport_added": False,
            "external_fetch_performed": False,
            "runtime_controls_added": False,
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "template_only": True,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": "decision_reference_review_bundle_verification_ready",
        "severity": severity,
        "to": targets,
        "message": (
            "MAGMA operator decision-reference review bundle verification "
            f"bridge-event template ready for {release_ref} at {commit_sha}; "
            "manual_review_required=true; approval_granted=false; "
            "release_decision_made=false; automatic_release_decision=false; "
            "template_only=true; decision_reference_is_approval=false; "
            "decision_reference_is_release_decision=false; no bridge write, "
            "transport, endpoint fetch, payload inclusion, local path "
            "recording, runtime controls, merge, or promotion."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "magma",
            "reviewer_handoff",
            "bridge_event",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    _assert_no_forbidden_output(json.dumps(event, allow_nan=False, sort_keys=True))
    return {
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": event,
        "template_only": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": _safe_token_list(verification.get("warnings")),
    }


def _summary_contract_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if summary.get("ok") is not True:
        blockers.append("verification_summary_not_ok")
    if summary.get("summary_version") != SUMMARY_VERSION:
        blockers.append("verification_summary_version_mismatch")
    if _safe_ref_or_invalid(summary.get("release_ref")) == "invalid_ref":
        blockers.append("verification_summary_release_ref_invalid")
    if _commit_or_invalid(summary.get("commit_sha")) == "invalid_commit":
        blockers.append("verification_summary_commit_sha_invalid")
    if _safe_ref_or_invalid(summary.get("ci_run_ref")) == "invalid_ref":
        blockers.append("verification_summary_ci_run_ref_invalid")
    if _safe_token_list(summary.get("blockers")):
        blockers.append("verification_summary_blockers_present")

    for field in _AUTHORITY_FALSE_FIELDS:
        if summary.get(field) is not False:
            blockers.append(f"verification_summary_{field}_not_false")

    reviewer = _mapping(summary.get("reviewer_ownership"))
    if reviewer.get("manual_review_required") is not True:
        blockers.append("reviewer_ownership_manual_review_required_not_true")
    for field in (
        "approval_granted",
        "release_decision_made",
        "automatic_release_decision",
    ):
        if reviewer.get(field) is not False:
            blockers.append(f"reviewer_ownership_{field}_not_false")
    if _safe_ref_or_invalid(reviewer.get("reviewer_agent_id")) == "invalid_ref":
        blockers.append("reviewer_ownership_reviewer_agent_id_invalid")
    if _safe_ref_or_invalid(reviewer.get("handoff_ref")) == "invalid_ref":
        blockers.append("reviewer_ownership_handoff_ref_invalid")

    verification = _mapping(
        summary.get("operator_decision_reference_review_bundle_verification")
    )
    if verification.get("verification_ok") is not True:
        blockers.append("review_bundle_verification_not_ok")
    if verification.get("verification_version") != VERIFICATION_VERSION:
        blockers.append("review_bundle_verification_version_mismatch")
    if verification.get("bundle_index_version") != BUNDLE_INDEX_VERSION:
        blockers.append("review_bundle_verification_bundle_index_version_mismatch")
    if verification.get("artifact_count_checked") != len(_ARTIFACT_IDS):
        blockers.append("review_bundle_verification_artifact_count_mismatch")
    if verification.get("source_contract_check") != "match":
        blockers.append("review_bundle_verification_source_contract_not_match")
    if verification.get("rebuilt_index_check") != "match":
        blockers.append("review_bundle_verification_rebuilt_index_not_match")
    if _as_nonnegative_int(verification.get("blocker_count")) != 0:
        blockers.append("review_bundle_verification_blocker_count_nonzero")
    if _safe_token_list(verification.get("blockers")):
        blockers.append("review_bundle_verification_blockers_present")
    for check_name in ("digest_checks", "size_checks", "schema_version_checks"):
        checks = _mapping(verification.get(check_name))
        for artifact_id in _ARTIFACT_IDS:
            if checks.get(artifact_id) != "match":
                blockers.append(
                    f"review_bundle_verification_{check_name}_{artifact_id}_not_match"
                )

    reference = _mapping(summary.get("operator_decision_reference_review"))
    decision_ref = _safe_ref_or_invalid(reference.get("decision_reference"))
    expected_ref = _safe_ref_or_invalid(reference.get("expected_decision_reference"))
    if decision_ref == "invalid_ref":
        blockers.append("operator_decision_reference_invalid")
    if expected_ref == "invalid_ref":
        blockers.append("operator_decision_reference_expected_invalid")
    if (
        decision_ref != "invalid_ref"
        and expected_ref != "invalid_ref"
        and decision_ref != expected_ref
    ):
        blockers.append("operator_decision_reference_mismatch")
    if reference.get("decision_reference_verified") is not True:
        blockers.append("operator_decision_reference_not_verified")
    for field in _REFERENCE_FALSE_FIELDS:
        if reference.get(field) is not False:
            blockers.append(f"operator_decision_reference_{field}_not_false")
    if reference.get("decision_must_be_recorded_separately") is not True:
        blockers.append(
            "operator_decision_reference_decision_must_be_recorded_separately_not_true"
        )
    if reference.get("review_context_only") is not True:
        blockers.append("operator_decision_reference_review_context_only_not_true")
    if reference.get("manual_review_required") is not True:
        blockers.append("operator_decision_reference_manual_review_required_not_true")

    boundary = _mapping(summary.get("operator_boundary"))
    if boundary.get("verification_report_boundary_ok") is not True:
        blockers.append("operator_boundary_verification_report_not_ok")
    if _safe_token_list(boundary.get("boundary_blockers")):
        blockers.append("operator_boundary_blockers_present")
    if boundary.get("manual_review_required") is not True:
        blockers.append("operator_boundary_manual_review_required_not_true")
    for field in _AUTHORITY_FALSE_FIELDS:
        if boundary.get(field) is not False:
            blockers.append(f"operator_boundary_{field}_not_false")

    return sorted(set(blockers))


def _load_json_report(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SafeInputError("verification_summary_unreadable") from exc

    def reject_constant(_value: str) -> None:
        raise ValueError("non_finite_json_constant")

    try:
        parsed = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
    except UnicodeDecodeError as exc:
        raise SafeInputError("verification_summary_decode_error") from exc
    except json.JSONDecodeError as exc:
        raise SafeInputError("verification_summary_json_error") from exc
    except ValueError as exc:
        raise SafeInputError("verification_summary_json_error") from exc
    if not isinstance(parsed, Mapping):
        raise SafeInputError("verification_summary_not_mapping")
    return parsed


def _failure_report(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "template_version": TEMPLATE_VERSION,
        "template_only": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [
            "operator_decision_reference_review_bundle_verification_"
            f"bridge_event_template_failed:{_safe_token(reason)}"
        ],
        "warnings": [],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _match_checks(value: Any) -> dict[str, str]:
    raw = _mapping(value)
    return {
        artifact_id: "match" if raw.get(artifact_id) == "match" else "unknown"
        for artifact_id in _ARTIFACT_IDS
    }


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str)
        and _SAFE_REF_RE.match(value)
        and not _forbidden_output_markers(value)
        else "invalid_ref"
    )


def _commit_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _COMMIT_RE.match(value)
        else "invalid_commit"
    )


def _validate_agent_id(label: str, value: str) -> None:
    if not isinstance(value, str) or not _AGENT_ID_RE.match(value):
        raise SafeInputError(f"{label}_unsafe")


def _validate_safe_ref(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not _SAFE_REF_RE.match(value)
        or _forbidden_output_markers(value)
    ):
        raise SafeInputError(f"{label}_unsafe")


def _validate_targets(raw: str) -> str:
    if not isinstance(raw, str):
        raise SafeInputError("to_unsafe")
    targets = [item.strip() for item in raw.split(",") if item.strip()]
    if not targets:
        raise SafeInputError("to_unsafe")
    for target in targets:
        _validate_agent_id("to", target)
    return ",".join(targets)


def _safe_token(value: Any, fallback: str = "unsafe_marker_redacted") -> str:
    if (
        isinstance(value, str)
        and _SAFE_REF_RE.match(value)
        and not _forbidden_output_markers(value)
    ):
        return value
    return fallback


def _safe_token_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, (set, tuple)):
        raw_values = list(value)
    else:
        raw_values = [value]
    return sorted({_safe_token(item) for item in raw_values})


def _as_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value >= 0:
        return value
    return 0


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


def _assert_no_forbidden_input(artifact_id: str, value: Mapping[str, Any]) -> None:
    try:
        serialized = json.dumps(value, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise SafeInputError(f"{artifact_id}_non_finite_json_value") from exc
    if _forbidden_output_markers(serialized):
        raise SafeInputError(f"{artifact_id}_forbidden_marker")


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    if _forbidden_output_markers(text):
        raise ValueError(
            "sanitized operator decision-reference review bundle verification "
            "bridge-event template contains forbidden markers"
        )


if __name__ == "__main__":
    raise SystemExit(main())
