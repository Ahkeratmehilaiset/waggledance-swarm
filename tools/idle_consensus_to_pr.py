# SPDX-License-Identifier: BUSL-1.1
"""Dry-run gate for converting idle consensus toward a pull request.

This v1 slice deliberately performs no GitHub, git, bridge, or filesystem
write action. It only decides whether a completed idle-protocol convergence
plus a candidate diff would pass the autonomy charter's path/content gates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from waggledance.core.idle_consensus_charter import (  # noqa: E402
    DEFAULT_CHARTER_PATH,
    evaluate_diff_content,
    evaluate_paths,
    load_charter,
)
from waggledance.core.idle_protocol import (  # noqa: E402
    detect_idle_convergence,
    validate_idle_proposal,
)


PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
ELIGIBLE_DECISION = "eligible_for_draft_pr_gate"
OPERATOR_REVIEW_DECISION = "operator_review_required"


class ConsensusToPrGateError(ValueError):
    """Raised when gate input cannot be evaluated safely."""

    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run idle consensus to PR autonomy gate.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--charter", type=Path, default=DEFAULT_CHARTER_PATH)
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        help="Candidate changed path. Repeat for multiple paths.",
    )
    parser.add_argument(
        "--diff-file",
        type=Path,
        default=None,
        help="Candidate unified diff text. Omit for an empty diff.",
    )
    parser.add_argument(
        "--utc-date",
        default=None,
        help="UTC date for daily rate-limit evaluation. Defaults to today.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    diff_text = ""
    if args.diff_file is not None:
        diff_text = args.diff_file.read_text(encoding="utf-8")

    try:
        report = evaluate_consensus_to_pr_gate(
            events_path=args.events,
            changed_paths=args.changed_path,
            diff_text=diff_text,
            charter_path=args.charter,
            utc_date=args.utc_date,
        )
    except ConsensusToPrGateError as exc:
        report = exc.report
        exit_code = int(report.get("exit_code", 2))
    else:
        exit_code = 0

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        for reason in report.get("reasons", []):
            print(f"- {reason}")
        for error in report.get("errors", []):
            print(f"- {error}", file=sys.stderr)
    return exit_code


def evaluate_consensus_to_pr_gate(
    *,
    events_path: Path,
    changed_paths: Sequence[str],
    diff_text: str,
    charter_path: Path = DEFAULT_CHARTER_PATH,
    utc_date: str | None = None,
) -> dict[str, Any]:
    """Evaluate only the local consensus + charter gates for a candidate PR."""
    _assert_no_private_markers(changed_paths, where="changed paths")
    _assert_no_private_markers(diff_text, where="diff text")

    events = _read_bridge_events(events_path)
    payloads = _idle_payloads_from_events(events)
    if not payloads:
        return _base_report(
            decision="no_consensus",
            reasons=["no idle-protocol payloads found"],
        )

    for payload in payloads:
        ok, errors = validate_idle_proposal(payload)
        if not ok:
            raise ConsensusToPrGateError(
                {
                    **_base_report(decision="invalid_transcript"),
                    "errors": errors,
                    "exit_code": 2,
                }
            )

    convergence = detect_idle_convergence(payloads)
    if convergence is None:
        return _base_report(
            decision="no_consensus",
            reasons=["soft or hard convergence has not been reached"],
        )

    status = str(convergence["status"])
    if status == "charter_violation":
        return {
            **_base_report(
                decision="charter_violation",
                operator_review_required=True,
                reasons=["idle instance is terminated by charter violation"],
            ),
            "convergence": convergence,
        }
    if status not in {"soft_convergence", "hard_convergence"}:
        return {
            **_base_report(
                decision=status,
                operator_review_required=True,
                reasons=[f"unsupported convergence status: {status}"],
            ),
            "convergence": convergence,
        }

    charter = load_charter(charter_path)
    rate_date = utc_date or _today_utc()
    quota_used = _count_daily_auto_merges(events, rate_date)
    quota_total = int(charter.daily_quota)
    rate_gate = {
        "allowed": quota_used < quota_total,
        "utc_date": rate_date,
        "quota_used": quota_used,
        "quota_total": quota_total,
    }
    path_gate = evaluate_paths(charter, changed_paths)
    diff_gate = evaluate_diff_content(charter, diff_text)

    reasons: list[str] = []
    if not rate_gate["allowed"]:
        reasons.append(
            f"daily rate limit exceeded: {quota_used}/{quota_total} for {rate_date}"
        )
    if not path_gate.allowed:
        reasons.append(f"path gate failed: {path_gate.reason}")
    if not diff_gate.allowed:
        reasons.append(f"diff gate failed: {diff_gate.reason}")

    eligible = bool(rate_gate["allowed"]) and path_gate.allowed and diff_gate.allowed
    decision = ELIGIBLE_DECISION if eligible else OPERATOR_REVIEW_DECISION
    if not rate_gate["allowed"]:
        decision = "rate_limited"
    return {
        **_base_report(
            decision=decision,
            operator_review_required=not eligible,
            reasons=reasons,
        ),
        "eligible": eligible,
        "convergence": convergence,
        "rate_gate": rate_gate,
        "path_gate": _gate_to_dict(path_gate),
        "diff_gate": _gate_to_dict(diff_gate),
        "next_required_gates": [
            "forensic_artifact_receipt",
            "draft_pr_creation",
            "ci_green",
            "mergeable_clean",
            "daily_rate_limit",
            "exact_head_merge",
        ],
    }


def _base_report(
    *,
    decision: str,
    operator_review_required: bool = False,
    reasons: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "decision": decision,
        "dry_run": True,
        "external_effect": False,
        "would_create_pr": False,
        "would_merge": False,
        "auto_execute": False,
        "operator_review_required": operator_review_required,
        "reasons": list(reasons),
    }


def _gate_to_dict(gate: Any) -> dict[str, Any]:
    return {
        "allowed": bool(gate.allowed),
        "reason": gate.reason,
        "blocked_paths": list(gate.blocked_paths),
        "unmatched_paths": list(gate.unmatched_paths),
        "code_pattern_hits": list(gate.code_pattern_hits),
    }


def _read_bridge_events(events_path: Path) -> list[dict[str, Any]]:
    if not events_path.exists():
        raise ConsensusToPrGateError(
            {
                **_base_report(decision="missing_events"),
                "errors": [f"missing bridge events file: {events_path}"],
                "exit_code": 2,
            }
        )
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConsensusToPrGateError(
                {
                    **_base_report(decision="invalid_events"),
                    "errors": [f"line {line_no}: {exc.msg}"],
                    "exit_code": 2,
                }
            ) from exc
        if isinstance(event, dict):
            events.append(event)
    return events


def _idle_payloads_from_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line_no, event in enumerate(events, 1):
        payload = event.get("payload") if isinstance(event, Mapping) else None
        if isinstance(payload, dict) and payload.get("protocol_version") == "idle-protocol.v1":
            _assert_no_private_markers(payload, where=f"idle payload line {line_no}")
            payloads.append(payload)
    return payloads


def _count_daily_auto_merges(events: Sequence[Mapping[str, Any]], utc_date: str) -> int:
    count = 0
    for event in events:
        if not str(event.get("ts_utc", "")).startswith(utc_date):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
        if _is_auto_merge_event(event, payload):
            count += 1
    return count


def _is_auto_merge_event(
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    if bool(payload.get("auto_merged")):
        return True
    return event.get("type") == "done" and "auto_merge" in str(event.get("status", ""))


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _assert_no_private_markers(value: object, *, where: str) -> None:
    marker = _find_private_marker(value)
    if marker is not None:
        raise ConsensusToPrGateError(
            {
                **_base_report(decision="privacy_marker_refused"),
                "errors": [f"{where}: privacy marker refused: {marker}"],
                "exit_code": 2,
            }
        )


def _find_private_marker(value: object) -> str | None:
    if isinstance(value, str):
        for marker in PRIVATE_MARKERS:
            if marker in value:
                return marker
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            marker = _find_private_marker(key)
            if marker is not None:
                return marker
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
        return None
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            marker = _find_private_marker(item)
            if marker is not None:
                return marker
    return None


if __name__ == "__main__":
    raise SystemExit(main())
