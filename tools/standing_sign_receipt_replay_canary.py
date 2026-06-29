# SPDX-License-Identifier: BUSL-1.1
"""Read-only canary for re-deriving a standing-sign merge receipt.

The canary consumes a PR status snapshot, bridge events, and the receipt payload
that was meant to justify a standing-consensus-sign merge. It replays the same
default-off standing-sign gate in dry-run mode and compares the receipt's
head/task/path/consensus material to the re-derived gate report. It never writes
receipt bundles, emits bridge events, invokes GitHub, or performs a merge.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.idle_check import DEFAULT_EVENTS_PATH  # noqa: E402
from tools.idle_consensus_auto_merge import (  # noqa: E402
    AutoMergeGateError,
    evaluate_auto_merge_gate,
)
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402


REPORT_VERSION = "wd.standing_sign_receipt_replay_canary.v0"


class StandingSignReceiptReplayError(ValueError):
    """Raised when the canary input cannot be replayed safely."""

    def __init__(self, report: dict[str, Any], exit_code: int = 2) -> None:
        super().__init__("; ".join(str(error) for error in report.get("errors", [])))
        self.report = report
        self.exit_code = exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-status-file", type=Path, required=True)
    parser.add_argument("--receipt-payload-file", type=Path, required=True)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-base-sha", default="")
    parser.add_argument("--consensus-proposal-id", required=True)
    parser.add_argument("--receipt-bundle-path", default="")
    parser.add_argument("--repo", default="")
    parser.add_argument("--from-agent", default="")
    parser.add_argument("--bridge-task-id", default="")
    parser.add_argument("--utc-date", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        pr_status = _read_json_object(args.pr_status_file, "pr_status")
        receipt_payload = _read_json_object(
            args.receipt_payload_file,
            "receipt_payload",
        )
        report = replay_standing_sign_receipt(
            pr_status=pr_status,
            receipt_payload=receipt_payload,
            events_path=args.events,
            expected_head=args.expected_head,
            expected_base_sha=args.expected_base_sha,
            consensus_proposal_id=args.consensus_proposal_id,
            receipt_bundle_path=args.receipt_bundle_path,
            repo=args.repo,
            from_agent=args.from_agent,
            bridge_task_id=args.bridge_task_id,
            utc_date=args.utc_date,
        )
    except (OSError, json.JSONDecodeError) as exc:
        report = {
            "ok": False,
            "decision": "standing_sign_receipt_replay_error",
            "errors": [exc.__class__.__name__],
            "authority_boundary": _authority_boundary(),
        }
        exit_code = 2
    except StandingSignReceiptReplayError as exc:
        report = exc.report
        exit_code = exc.exit_code
    else:
        exit_code = 0 if report.get("ok") else 3

    if args.json:
        print(json.dumps(report, sort_keys=True))
    elif report.get("ok"):
        print("standing-sign receipt replay PASS")
    else:
        print("standing-sign receipt replay BLOCKED", file=sys.stderr)
        for blocker in report.get("blockers", report.get("errors", [])):
            print(f"- {blocker}", file=sys.stderr)
    return exit_code


def replay_standing_sign_receipt(
    *,
    pr_status: Mapping[str, Any],
    receipt_payload: Mapping[str, Any],
    events_path: Path,
    expected_head: str,
    expected_base_sha: str = "",
    consensus_proposal_id: str,
    receipt_bundle_path: str = "",
    repo: str = "",
    from_agent: str = "",
    bridge_task_id: str = "",
    utc_date: str | None = None,
) -> dict[str, Any]:
    """Replay the standing-sign gate and compare receipt-bound material."""
    manifest_path = receipt_bundle_path or str(
        receipt_payload.get("receipt_manifest_planned") or ""
    )
    if not manifest_path:
        raise StandingSignReceiptReplayError(
            _error_report(["receipt_bundle_path or receipt_manifest_planned is required"])
        )

    try:
        gate_report = evaluate_auto_merge_gate(
            pr_status=pr_status,
            expected_head=expected_head,
            expected_base_sha=expected_base_sha,
            consensus_proposal_id=consensus_proposal_id,
            receipt_bundle_path=manifest_path,
            events_path=events_path,
            utc_date=utc_date,
            repo=repo,
            from_agent=from_agent,
            bridge_task_id=bridge_task_id,
            apply=False,
            require_bridge_consensus=True,
            standing_consensus_sign=True,
        )
    except AutoMergeGateError as exc:
        raise StandingSignReceiptReplayError(
            _error_report(list(exc.report.get("errors", [])) or [str(exc)])
        ) from exc

    blockers = _gate_blockers(gate_report)
    receipt_replay = _receipt_replay_report(
        pr_status=pr_status,
        receipt_payload=receipt_payload,
        gate_report=gate_report,
        expected_head=expected_head,
        expected_base_sha=expected_base_sha,
        consensus_proposal_id=consensus_proposal_id,
    )
    blockers.extend(receipt_replay["mismatches"])

    ok = not blockers
    return {
        "report_version": REPORT_VERSION,
        "ok": ok,
        "decision": (
            "standing_sign_receipt_replay_pass"
            if ok
            else "standing_sign_receipt_replay_blocked"
        ),
        "blockers": blockers,
        "expected_head": expected_head,
        "expected_base_sha": expected_base_sha,
        "consensus_proposal_id": consensus_proposal_id,
        "receipt_bundle_path": manifest_path,
        "gate_decision": gate_report.get("decision"),
        "standing_consensus_sign": dict(
            gate_report.get("standing_consensus_sign", {})
        ),
        "bridge_consensus_decision": _nested_str(
            gate_report,
            "bridge_consensus",
            "decision",
        ),
        "receipt_replay": receipt_replay,
        "rederived_material_digest": sha256_digest(
            _rederived_material(
                pr_status=pr_status,
                gate_report=gate_report,
                expected_head=expected_head,
                expected_base_sha=expected_base_sha,
                consensus_proposal_id=consensus_proposal_id,
            )
        ),
        "receipt_payload_digest": sha256_digest(dict(receipt_payload)),
        "authority_boundary": _authority_boundary(),
    }


def _gate_blockers(gate_report: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    if gate_report.get("decision") != "auto_merge_plan_ready":
        reasons = gate_report.get("reasons")
        if isinstance(reasons, Sequence) and not isinstance(reasons, str):
            blockers.extend(f"gate:{reason}" for reason in reasons)
        else:
            blockers.append(f"gate:{gate_report.get('decision')}")
    standing = gate_report.get("standing_consensus_sign")
    if not isinstance(standing, Mapping) or standing.get("admitted") is not True:
        reasons = standing.get("reasons") if isinstance(standing, Mapping) else []
        if isinstance(reasons, Sequence) and not isinstance(reasons, str):
            blockers.extend(f"standing:{reason}" for reason in reasons)
        else:
            blockers.append("standing:standing consensus sign was not admitted")
    return blockers


def _receipt_replay_report(
    *,
    pr_status: Mapping[str, Any],
    receipt_payload: Mapping[str, Any],
    gate_report: Mapping[str, Any],
    expected_head: str,
    expected_base_sha: str,
    consensus_proposal_id: str,
) -> dict[str, Any]:
    mismatches: list[str] = []
    _expect_equal(
        mismatches,
        "head_sha",
        str(receipt_payload.get("head_sha", "")),
        expected_head,
    )
    if expected_base_sha:
        _expect_equal(
            mismatches,
            "base_sha",
            str(receipt_payload.get("base_sha", "")),
            expected_base_sha,
        )
    _expect_equal(
        mismatches,
        "task_id",
        str(receipt_payload.get("task_id", "")),
        consensus_proposal_id,
    )
    _expect_equal(
        mismatches,
        "gate_decision",
        str(receipt_payload.get("gate_decision", "")),
        str(gate_report.get("decision", "")),
    )
    _expect_equal(
        mismatches,
        "changed_paths",
        list(receipt_payload.get("changed_paths", [])),
        list(pr_status.get("changed_paths", [])),
    )
    receipt_diff_digest = str(receipt_payload.get("diff_digest", ""))
    expected_diff_digest = sha256_digest(str(pr_status.get("diff_text", "")))
    if receipt_diff_digest:
        _expect_equal(
            mismatches,
            "diff_digest",
            receipt_diff_digest,
            expected_diff_digest,
        )

    receipt_consensus = receipt_payload.get("bridge_consensus")
    gate_consensus = gate_report.get("bridge_consensus")
    if not isinstance(receipt_consensus, Mapping):
        mismatches.append("bridge_consensus missing from receipt payload")
    elif not isinstance(gate_consensus, Mapping):
        mismatches.append("bridge_consensus missing from rederived gate report")
    else:
        _expect_equal(
            mismatches,
            "bridge_consensus.decision",
            str(receipt_consensus.get("decision", "")),
            str(gate_consensus.get("decision", "")),
        )
        _expect_equal(
            mismatches,
            "bridge_consensus.head_sha",
            str(receipt_consensus.get("head_sha", "")),
            str(gate_consensus.get("head_sha", "")),
        )
        _expect_equal(
            mismatches,
            "bridge_consensus.canonical_task_id",
            str(receipt_consensus.get("canonical_task_id", "")),
            str(gate_consensus.get("canonical_task_id", "")),
        )

    return {
        "matched": not mismatches,
        "mismatches": mismatches,
        "receipt_gate_decision": str(receipt_payload.get("gate_decision", "")),
        "rederived_gate_decision": str(gate_report.get("decision", "")),
    }


def _expect_equal(
    mismatches: list[str],
    label: str,
    actual: object,
    expected: object,
) -> None:
    if actual != expected:
        mismatches.append(f"{label} mismatch: receipt={actual!r} rederived={expected!r}")


def _rederived_material(
    *,
    pr_status: Mapping[str, Any],
    gate_report: Mapping[str, Any],
    expected_head: str,
    expected_base_sha: str,
    consensus_proposal_id: str,
) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "head_sha": expected_head,
        "base_sha": expected_base_sha,
        "task_id": consensus_proposal_id,
        "gate_decision": gate_report.get("decision"),
        "standing_consensus_sign": dict(
            gate_report.get("standing_consensus_sign", {})
        ),
        "bridge_consensus": dict(gate_report.get("bridge_consensus", {})),
        "changed_paths": list(pr_status.get("changed_paths", [])),
        "diff_digest": sha256_digest(str(pr_status.get("diff_text", ""))),
        "checks": list(pr_status.get("checks", [])),
    }


def _nested_str(value: Mapping[str, Any], *path: str) -> str:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key)
    return str(current or "")


def _read_json_object(path: Path, label: str) -> Mapping[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        raise StandingSignReceiptReplayError(_error_report([f"{label} must be a JSON object"]))
    return data


def _error_report(errors: Sequence[str]) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "ok": False,
        "decision": "standing_sign_receipt_replay_error",
        "errors": list(errors),
        "authority_boundary": _authority_boundary(),
    }


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only": True,
        "writes_receipt_bundle": False,
        "emits_bridge_events": False,
        "runs_gh_merge": False,
        "grants_runtime_authority": False,
        "skips_gate": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
