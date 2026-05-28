# SPDX-License-Identifier: BUSL-1.1
"""Build a sanitized MAGMA alert-feed reviewer handoff summary."""
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

from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)


SUMMARY_VERSION = "magma_alert_feed_reviewer_handoff_summary.v1"

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,191}$")
_DIGEST_STATUS = frozenset({"match", "mismatch", "not_checked", "unknown"})


class SafeInputError(ValueError):
    """Raised when an operator-supplied reference is not safe to echo."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-json", required=True, type=Path)
    parser.add_argument("--validation-json", required=True, type=Path)
    parser.add_argument("--reviewer-agent", required=True)
    parser.add_argument("--bridge-event-ref", required=True)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-28T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        package = json.loads(args.package_json.read_text(encoding="utf-8"))
        validation_report = json.loads(
            args.validation_json.read_text(encoding="utf-8")
        )
    except OSError:
        summary = _failure_summary("input_unreadable")
    except json.JSONDecodeError:
        summary = _failure_summary("input_json_decode_error")
    else:
        try:
            summary = build_magma_alert_feed_reviewer_handoff_summary(
                package=package,
                validation_report=validation_report,
                reviewer_agent_id=args.reviewer_agent,
                bridge_event_ref=args.bridge_event_ref,
                now_utc=_parse_utc(args.now) if args.now else None,
            )
        except SafeInputError as exc:
            summary = _failure_summary(exc.code)
        except ValueError:
            summary = _failure_summary("summary_invalid")

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    elif summary["ok"]:
        print(render_reviewer_handoff_summary_markdown(summary))
    else:
        print(
            "MAGMA alert-feed reviewer handoff summary FAILED: "
            + ", ".join(summary["blockers"]),
            file=sys.stderr,
        )
    return 0 if summary["ok"] else 1


def build_magma_alert_feed_reviewer_handoff_summary(
    *,
    package: Mapping[str, Any],
    validation_report: Mapping[str, Any],
    reviewer_agent_id: str,
    bridge_event_ref: str,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return reviewer handoff context without making a release decision."""

    if not isinstance(package, Mapping):
        raise ValueError("package_not_mapping")
    if not isinstance(validation_report, Mapping):
        raise ValueError("validation_report_not_mapping")
    _validate_safe_ref("reviewer_agent_id", reviewer_agent_id)
    _validate_safe_ref("bridge_event_ref", bridge_event_ref)

    manual_gate = _mapping(package.get("manual_gate"))
    authority = _mapping(package.get("authority"))
    checks = manual_gate.get("checks")
    raw_checks = checks if isinstance(checks, list) else []
    check_ids = _safe_token_list([
        check.get("id")
        for check in raw_checks
        if isinstance(check, Mapping)
    ])
    hold_reasons = _safe_token_list(manual_gate.get("current_sample_hold_reasons"))
    missing_samples = _safe_token_list(manual_gate.get("missing_required_samples"))
    validation_blockers = _safe_token_list(validation_report.get("blockers"))
    validation_warnings = _safe_token_list(validation_report.get("warnings"))

    summary = {
        "ok": True,
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "release_ref": _safe_ref_or_invalid(package.get("release_ref")),
        "commit_sha": _commit_or_invalid(package.get("commit_sha")),
        "ci_run_ref": _safe_ref_or_invalid(package.get("ci_run_ref")),
        "reviewer_ownership": {
            "reviewer_agent_id": reviewer_agent_id,
            "bridge_event_ref": bridge_event_ref,
            "manual_review_required": True,
            "automatic_release_decision": False,
            "approval_granted": False,
        },
        "validated_evidence": {
            "package_validation_ok": validation_report.get("ok") is True,
            "digest_checks": _digest_checks(
                _mapping(validation_report.get("digest_checks"))
            ),
            "blockers": validation_blockers,
            "warnings": validation_warnings,
        },
        "manual_gate_snapshot": {
            "status": _safe_token(manual_gate.get("status"), "unknown"),
            "manual_review_required": True,
            "automatic_release_decision": False,
            "check_count": len(check_ids),
            "check_ids": check_ids,
            "hold_reason_count": len(hold_reasons),
            "hold_reasons": hold_reasons,
            "missing_required_samples": missing_samples,
        },
        "authority_boundary": {
            "observed_runtime_authority_granted": _as_bool(
                authority.get("runtime_authority_granted")
            ),
            "observed_payload_files_imported": _as_nonnegative_float(
                authority.get("payload_files_imported")
            )
            or 0.0,
            "observed_local_paths_recorded": _as_bool(
                authority.get("local_paths_recorded")
            ),
            "runtime_controls_added": False,
            "configuration_writes_applied": False,
            "import_or_replay_performed": False,
            "auto_merge_or_promotion_performed": False,
        },
        "reviewer_next_actions": [
            "review_manual_gate_checks",
            "compare_required_artifacts",
            "confirm_no_runtime_authority",
            "record_operator_decision_separately",
        ],
        "manual_review_required": True,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "blockers": [],
        "warnings": [],
    }
    _assert_no_forbidden_output(json.dumps(summary, sort_keys=True))
    return summary


def render_reviewer_handoff_summary_markdown(summary: Mapping[str, Any]) -> str:
    evidence = _mapping(summary.get("validated_evidence"))
    manual_gate = _mapping(summary.get("manual_gate_snapshot"))
    authority = _mapping(summary.get("authority_boundary"))
    blockers = evidence.get("blockers") or []
    warnings = evidence.get("warnings") or []
    hold_reasons = manual_gate.get("hold_reasons") or []
    lines = [
        "# MAGMA Alert Feed Reviewer Handoff Summary",
        "",
        f"- Summary version: `{summary.get('summary_version')}`",
        f"- Release ref: `{summary.get('release_ref')}`",
        f"- Commit SHA: `{summary.get('commit_sha')}`",
        f"- CI run ref: `{summary.get('ci_run_ref')}`",
        f"- Created at UTC: `{summary.get('created_at_utc')}`",
        "- Manual review required: `true`",
        "- Automatic release decision: `false`",
        "- Approval granted: `false`",
        "- Release decision made: `false`",
        "- Runtime controls added: `false`",
        "- External fetch performed: `false`",
        "",
        "## Validation",
        "",
        f"- Package validation OK: `{evidence.get('package_validation_ok')}`",
        "- Blockers:",
    ]
    lines.extend(_markdown_token_list(blockers))
    lines.append("- Warnings:")
    lines.extend(_markdown_token_list(warnings))
    lines.extend([
        "",
        "## Manual Gate Snapshot",
        "",
        f"- Status: `{manual_gate.get('status')}`",
        f"- Check count: `{manual_gate.get('check_count')}`",
        f"- Hold reason count: `{manual_gate.get('hold_reason_count')}`",
        "- Hold reasons:",
    ])
    lines.extend(_markdown_token_list(hold_reasons))
    lines.extend([
        "",
        "## Authority Boundary",
        "",
        "- Observed runtime authority granted: "
        f"`{authority.get('observed_runtime_authority_granted')}`",
        "- Observed payload files imported: "
        f"`{authority.get('observed_payload_files_imported')}`",
        "- Observed local paths recorded: "
        f"`{authority.get('observed_local_paths_recorded')}`",
        "",
        "This summary is operator-owned reviewer context only. It does not "
        "approve, merge, promote, write configuration, fetch endpoints, "
        "transport artifacts, import or replay payloads, control feeds, or "
        "grant runtime authority.",
        "",
    ])
    markdown = "\n".join(lines)
    _assert_no_forbidden_output(markdown)
    return markdown


def _failure_summary(reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "summary_version": SUMMARY_VERSION,
        "release_ref": "invalid_ref",
        "commit_sha": "invalid_commit",
        "ci_run_ref": "invalid_ref",
        "manual_review_required": True,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "blockers": [f"handoff_summary_failed:{reason}"],
        "warnings": [],
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_ref_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _SAFE_REF_RE.match(value)
        else "invalid_ref"
    )


def _commit_or_invalid(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and _COMMIT_RE.match(value)
        else "invalid_commit"
    )


def _validate_safe_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_REF_RE.match(value):
        raise SafeInputError(f"{label}_unsafe")


def _safe_token(value: Any, fallback: str = "unsafe_marker_redacted") -> str:
    if (
        isinstance(value, str)
        and _SAFE_TOKEN_RE.match(value)
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
    tokens = [_safe_token(item) for item in raw_values]
    return sorted(set(tokens))


def _digest_checks(raw: Mapping[str, Any]) -> dict[str, str]:
    checks: dict[str, str] = {}
    for artifact_id in ("ops_json", "metrics_scrape"):
        status = raw.get(artifact_id)
        checks[artifact_id] = status if status in _DIGEST_STATUS else "unknown"
    return checks


def _markdown_token_list(values: Any) -> list[str]:
    if not isinstance(values, list) or not values:
        return ["  - `none`"]
    return [f"  - `{_safe_token(value)}`" for value in values]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) != 0.0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    return False


def _as_nonnegative_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return numeric


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
            "sanitized handoff summary contains forbidden markers: "
            + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
