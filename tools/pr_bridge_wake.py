# SPDX-License-Identifier: BUSL-1.1
"""Read-only PR bridge wake evidence for headRefName-bound task ids.

The merge/review bridge keys PR gate evidence on the canonical
``task_id == headRefName`` convention. This helper builds a schema-valid
``wake_request`` template for that convention without appending the event,
calling GitHub, writing wake files, or granting any authority.
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

from waggledance.core.bridge_event_schema import validate_event  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.pr_bridge_wake_headsafe_evidence.v1"
PROOF_ID = "pr_bridge_wake_headsafe_evidence_v1"
EVENT_STATUS = "pr_bridge_wake_headsafe_template_ready"
DEFAULT_ROLE = "lead-impl"

AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,32}$")
HEAD_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,119}$")
KNOWN_SEVERITIES = {"", "low", "medium", "high"}

FORBIDDEN_HEAD_REF_TOKENS = (
    "\\",
    "://",
    "\r",
    "\n",
    "\t",
    "PRIVATE_MARKER",
    "_DO_NOT_LEAK",
)
FORBIDDEN_OUTPUT_MARKERS = (
    "http://",
    "https://",
    "C:/",
    "C:\\",
    "\\\\",
    "/home/",
    "/Users/",
    "PRIVATE_",
    "Authorization",
    "Bearer ",
    "secret",
    "password",
)


def build_pr_bridge_wake_headsafe_evidence(
    *,
    pr_number: int,
    head_ref_name: str,
    target_agent: str,
    requester_agent: str = "codex-lead-1",
    severity: str = "medium",
    role: str = DEFAULT_ROLE,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Return a validated wake-request template and no-authority evidence.

    The returned report is fail-closed: malformed inputs return ``ok=False``
    with blockers instead of a partial template. A successful report proves only
    that a PR headRefName can be represented as a bridge wake ``task_id`` with
    safe scalar metadata; it does not emit the wake or make a review decision.
    """

    blockers = _input_blockers(
        pr_number=pr_number,
        head_ref_name=head_ref_name,
        target_agent=target_agent,
        requester_agent=requester_agent,
        severity=severity,
        role=role,
    )
    if blockers:
        return _failure_report(blockers)

    head_ref = str(head_ref_name).strip()
    pr = int(pr_number)
    head_ref_digest = sha256_digest(
        {
            "headRefName": head_ref,
            "pr": pr,
            "schema_version": REPORT_VERSION,
        }
    )
    payload = {
        "schema_version": REPORT_VERSION,
        "pr_number": pr,
        "head_ref_name": head_ref,
        "head_ref_digest": head_ref_digest,
        "task_id_source": "headRefName",
        "task_id_matches_head_ref": True,
        "head_ref_safe": True,
        "template_only": True,
        "authority_boundary": {
            "manual_review_required": True,
            "approval_granted": False,
            "merge_decision_made": False,
            "release_decision_made": False,
            "wake_request_emitted": False,
            "bridge_event_written": False,
            "github_mutation_performed": False,
            "external_fetch_performed": False,
            "runtime_authority_granted": False,
            "claim_safe": False,
        },
        "local_paths_recorded": False,
        "raw_payloads_included": False,
    }
    event = {
        "ts_utc": _utc_iso(now_utc or datetime.now(timezone.utc)),
        "agent": requester_agent,
        "type": "wake_request",
        "task_id": head_ref,
        "status": EVENT_STATUS,
        "severity": severity,
        "to": target_agent,
        "message": (
            f"PR #{pr} headRefName wake template ready; "
            "task_id=headRefName; manual_review_required=true; "
            "wake_request_emitted=false; bridge_event_written=false."
        ),
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "role": role,
        "agent_uuid": "",
        "session_id": "",
        "capabilities": [
            "bridge_event",
            "work_queue",
            "wd_image1",
        ],
        "pid": 0,
        "cwd": "template_not_emitted",
        "payload": payload,
    }
    try:
        validate_event(event)
    except Exception:
        return _failure_report(["bridge_event_schema_invalid"])

    rendered = json.dumps(event, allow_nan=False, sort_keys=True)
    if _forbidden_output_markers(rendered):
        return _failure_report(["template_forbidden_output_marker"])

    return {
        "proof_id": PROOF_ID,
        "ok": True,
        "report_version": REPORT_VERSION,
        "wake_request_template_valid": True,
        "bridge_event_template": event,
        "pr_number": pr,
        "head_ref_name": head_ref,
        "head_ref_digest": head_ref_digest,
        "task_id": head_ref,
        "task_id_matches_head_ref": True,
        "head_ref_safe": True,
        "target_agent_valid": True,
        "requester_agent_valid": True,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "merge_decision_made": False,
        "release_decision_made": False,
        "wake_request_emitted": False,
        "bridge_event_written": False,
        "github_mutation_performed": False,
        "external_fetch_performed": False,
        "runtime_authority_granted": False,
        "claim_safe": False,
        "local_paths_recorded": False,
        "raw_payloads_included": False,
        "path_free_verified": True,
        "blockers": [],
    }


def _input_blockers(
    *,
    pr_number: int,
    head_ref_name: str,
    target_agent: str,
    requester_agent: str,
    severity: str,
    role: str,
) -> list[str]:
    blockers: list[str] = []
    if not isinstance(pr_number, int) or isinstance(pr_number, bool) or pr_number <= 0:
        blockers.append("pr_number_not_positive_int")
    head_ref = str(head_ref_name).strip()
    head_ref_error = _head_ref_error(head_ref)
    if head_ref_error:
        blockers.append(head_ref_error)
    if not AGENT_ID_PATTERN.fullmatch(str(target_agent or "")):
        blockers.append("target_agent_invalid")
    if not AGENT_ID_PATTERN.fullmatch(str(requester_agent or "")):
        blockers.append("requester_agent_invalid")
    if role and not AGENT_ID_PATTERN.fullmatch(str(role)):
        blockers.append("role_invalid")
    if severity not in KNOWN_SEVERITIES:
        blockers.append("severity_invalid")
    return blockers


def _head_ref_error(value: str) -> str:
    if not value:
        return "head_ref_empty"
    if len(value) > 120:
        return "head_ref_too_long"
    if value.startswith("/") or value.endswith("/") or "//" in value:
        return "head_ref_path_shape_invalid"
    if any(token in value for token in FORBIDDEN_HEAD_REF_TOKENS):
        return "head_ref_forbidden_token"
    if any(part in {"", ".", ".."} for part in value.split("/")):
        return "head_ref_path_shape_invalid"
    if not HEAD_REF_PATTERN.fullmatch(value):
        return "head_ref_pattern_invalid"
    return ""


def _failure_report(blockers: Sequence[str]) -> dict[str, Any]:
    return {
        "proof_id": PROOF_ID,
        "ok": False,
        "report_version": REPORT_VERSION,
        "wake_request_template_valid": False,
        "task_id_matches_head_ref": False,
        "head_ref_safe": False,
        "target_agent_valid": False,
        "requester_agent_valid": False,
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "merge_decision_made": False,
        "release_decision_made": False,
        "wake_request_emitted": False,
        "bridge_event_written": False,
        "github_mutation_performed": False,
        "external_fetch_performed": False,
        "runtime_authority_granted": False,
        "claim_safe": False,
        "local_paths_recorded": False,
        "raw_payloads_included": False,
        "path_free_verified": False,
        "blockers": list(blockers),
    }


def _forbidden_output_markers(rendered: str) -> list[str]:
    lowered = rendered.lower()
    found: list[str] = []
    for marker in FORBIDDEN_OUTPUT_MARKERS:
        if marker.lower() in lowered:
            found.append(marker)
    return found


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build read-only PR bridge wake headRefName evidence as JSON. "
            "No bridge event is appended."
        )
    )
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--head-ref-name", required=True)
    parser.add_argument("--target-agent", required=True)
    parser.add_argument("--requester-agent", default="codex-lead-1")
    parser.add_argument("--severity", default="medium")
    parser.add_argument("--role", default=DEFAULT_ROLE)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 when the evidence report is not ok.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_pr_bridge_wake_headsafe_evidence(
        pr_number=args.pr_number,
        head_ref_name=args.head_ref_name,
        target_agent=args.target_agent,
        requester_agent=args.requester_agent,
        severity=args.severity,
        role=args.role,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.strict and report.get("ok") is not True:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
