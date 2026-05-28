# SPDX-License-Identifier: BUSL-1.1
"""Build a MAGMA alert-feed reviewer handoff bridge-event template."""
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

from tools.build_magma_alert_feed_reviewer_handoff_summary import (  # noqa: E402
    SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import (  # noqa: E402
    FORBIDDEN_OUTPUT_MARKERS,
)


TEMPLATE_VERSION = "magma_alert_feed_reviewer_bridge_event_template.v1"

_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DIGEST_STATUS = frozenset({"match", "mismatch", "not_checked", "unknown"})


class SafeInputError(ValueError):
    """Raised when an operator-supplied bridge template field is unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-json", required=True, type=Path)
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
        help="Optional UTC timestamp override such as 2026-05-28T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = json.loads(args.summary_json.read_text(encoding="utf-8"))
    except OSError:
        report = _failure_report("summary_json_unreadable")
    except json.JSONDecodeError:
        report = _failure_report("summary_json_decode_error")
    else:
        try:
            report = build_magma_alert_feed_reviewer_bridge_event_template(
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
        except ValueError:
            report = _failure_report("bridge_template_invalid")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(json.dumps(report["bridge_event_template"], indent=2, sort_keys=True))
    else:
        print(
            "MAGMA alert-feed reviewer bridge-event template FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_magma_alert_feed_reviewer_bridge_event_template(
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
        raise ValueError("summary_not_mapping")
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

    if summary.get("summary_version") != SUMMARY_VERSION:
        raise ValueError("summary_version_mismatch")
    if summary.get("ok") is not True:
        raise ValueError("summary_not_ok")

    release_ref = _safe_ref_or_invalid(summary.get("release_ref"))
    commit_sha = _commit_or_invalid(summary.get("commit_sha"))
    ci_run_ref = _safe_ref_or_invalid(summary.get("ci_run_ref"))
    evidence = _mapping(summary.get("validated_evidence"))
    manual_gate = _mapping(summary.get("manual_gate_snapshot"))
    authority = _mapping(summary.get("authority_boundary"))
    payload = {
        "schema_version": TEMPLATE_VERSION,
        "summary_version": summary.get("summary_version"),
        "release_ref": release_ref,
        "commit_sha": commit_sha,
        "ci_run_ref": ci_run_ref,
        "validated_evidence": {
            "package_validation_ok": evidence.get("package_validation_ok") is True,
            "digest_checks": _digest_checks(_mapping(evidence.get("digest_checks"))),
            "blocker_count": _list_count(evidence.get("blockers")),
            "warning_count": _list_count(evidence.get("warnings")),
        },
        "manual_gate": {
            "manual_review_required": True,
            "automatic_release_decision": False,
            "check_count": _as_nonnegative_int(manual_gate.get("check_count")),
            "hold_reason_count": _as_nonnegative_int(
                manual_gate.get("hold_reason_count")
            ),
            "missing_required_sample_count": _list_count(
                manual_gate.get("missing_required_samples")
            ),
        },
        "authority_boundary": {
            "observed_runtime_authority_granted": _as_bool(
                authority.get("observed_runtime_authority_granted")
            ),
            "observed_payload_files_imported": _as_nonnegative_float(
                authority.get("observed_payload_files_imported")
            )
            or 0.0,
            "observed_local_paths_recorded": _as_bool(
                authority.get("observed_local_paths_recorded")
            ),
            "runtime_controls_added": False,
            "configuration_writes_applied": False,
            "import_or_replay_performed": False,
            "auto_merge_or_promotion_performed": False,
        },
        "operator_decision": {
            "approval_granted": False,
            "release_decision_made": False,
            "automatic_release_decision": False,
            "decision_must_be_recorded_separately": True,
        },
        "template_only": True,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": agent_id,
        "type": "handoff",
        "task_id": task_id,
        "status": "reviewer_handoff_summary_ready",
        "severity": severity,
        "to": targets,
        "message": (
            "MAGMA alert-feed reviewer handoff summary template ready for "
            f"{release_ref} at {commit_sha}; manual_review_required=true; "
            "approval_granted=false; release_decision_made=false; "
            "automatic_release_decision=false; template_only=true; "
            "no bridge write, transport, endpoint fetch, runtime controls, "
            "merge, or promotion."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": run_id,
        "role": role,
        "session_id": session_id,
        "capabilities": [
            "wd_image1",
            "release_evidence",
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
        "blockers": [],
        "warnings": [],
    }


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
        "blockers": [f"bridge_event_template_failed:{reason}"],
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


def _validate_agent_id(label: str, value: str) -> None:
    if not isinstance(value, str) or not _AGENT_ID_RE.match(value):
        raise SafeInputError(f"{label}_unsafe")


def _validate_safe_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SAFE_REF_RE.match(value):
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


def _digest_checks(raw: Mapping[str, Any]) -> dict[str, str]:
    checks: dict[str, str] = {}
    for artifact_id in ("ops_json", "metrics_scrape"):
        status = raw.get(artifact_id)
        checks[artifact_id] = status if status in _DIGEST_STATUS else "unknown"
    return checks


def _list_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _as_bool(value: Any) -> bool:
    return value if isinstance(value, bool) else False


def _as_nonnegative_int(value: Any) -> int:
    numeric = _as_nonnegative_float(value)
    return int(numeric) if numeric is not None else 0


def _as_nonnegative_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")) or numeric < 0:
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
            "sanitized bridge-event template contains forbidden markers: "
            + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
