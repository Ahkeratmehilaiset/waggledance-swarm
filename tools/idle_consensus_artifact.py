# SPDX-License-Identifier: BUSL-1.1
"""Write an operator-review artifact for idle-protocol consensus.

The tool is deliberately manual and local. It never creates work-queue tasks,
branches, pull requests, or bridge events. It converts a completed soft/hard
idle convergence into evidence for an operator decision.
"""
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

from tools.idle_check import DEFAULT_EVENTS_PATH
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.idle_protocol import detect_idle_convergence, validate_idle_proposal
from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.magma.receipt_bundle import (
    ReceiptBundleEntry,
    write_receipt_bundle,
)


DEFAULT_OUT_DIR = Path("docs") / "architecture" / "consensus_artifacts"
PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")
IMPLEMENTATION_HINTS = (
    "create pr",
    "open pr",
    "git checkout",
    "git switch",
    "new branch",
    "implement next",
    "scaffold code",
    "work_queue",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write an operator-review artifact for idle consensus.",
    )
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--receipt-out-dir",
        type=Path,
        default=None,
        help="Optional non-existing output directory for a local MAGMA receipt bundle.",
    )
    parser.add_argument("--now", default=None)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = write_idle_consensus_artifact(
            events_path=args.events,
            out_dir=args.out_dir,
            receipt_out_dir=args.receipt_out_dir,
            now_utc=_parse_utc(args.now) if args.now else datetime.now(timezone.utc),
        )
    except ArtifactError as exc:
        if args.json:
            print(json.dumps(exc.report, sort_keys=True))
        else:
            print(f"idle consensus artifact FAILED: {exc}", file=sys.stderr)
            for error in exc.report.get("errors", []):
                print(f"- {error}", file=sys.stderr)
        return int(exc.report.get("exit_code", 2))

    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(report["decision"])
        print(f"json: {report['json_path']}")
        print(f"markdown: {report['markdown_path']}")
    return 0


def write_idle_consensus_artifact(
    *,
    events_path: Path,
    out_dir: Path,
    receipt_out_dir: Path | None = None,
    now_utc: datetime,
) -> dict[str, Any]:
    events = _read_events(events_path)
    payloads = _idle_payloads(events)
    if not payloads:
        raise ArtifactError(
            "no idle-protocol payloads found",
            {
                "decision": "no_consensus",
                "errors": ["no idle-protocol payloads found"],
                "exit_code": 3,
            },
        )
    _refuse_private_markers(payloads)

    for payload in payloads:
        ok, errors = validate_idle_proposal(payload)
        if not ok:
            raise ArtifactError(
                "idle transcript contains invalid payload",
                {
                    "decision": "invalid_payload",
                    "errors": errors,
                    "exit_code": 2,
                },
            )

    convergence = detect_idle_convergence(payloads)
    if convergence is None:
        raise ArtifactError(
            "no idle consensus to convert into operator artifact",
            {
                "decision": "no_consensus",
                "errors": ["soft or hard convergence has not been reached"],
                "exit_code": 3,
            },
        )
    status = str(convergence["status"])
    if status == "charter_violation":
        raise ArtifactError(
            "charter violation terminates the idle instance",
            {
                "decision": "charter_violation",
                "errors": ["terminated idle instances are not eligible for artifacts"],
                "convergence": convergence,
                "exit_code": 4,
            },
        )
    if status not in {"soft_convergence", "hard_convergence"}:
        raise ArtifactError(
            "idle convergence is not artifact eligible",
            {
                "decision": status,
                "errors": [f"unsupported convergence status: {status}"],
                "convergence": convergence,
                "exit_code": 4,
            },
        )

    artifact_id = _artifact_id(convergence, payloads)
    json_path = out_dir / f"{artifact_id}.json"
    markdown_path = out_dir / f"{artifact_id}.md"
    if json_path.exists() or markdown_path.exists():
        raise ArtifactError(
            "artifact output already exists",
            {
                "decision": "refuse_overwrite",
                "errors": [f"artifact already exists: {artifact_id}"],
                "exit_code": 5,
            },
        )
    if receipt_out_dir is not None and receipt_out_dir.exists():
        raise ArtifactError(
            "receipt output already exists",
            {
                "decision": "refuse_receipt_overwrite",
                "errors": [f"receipt output already exists: {receipt_out_dir}"],
                "exit_code": 5,
            },
        )

    artifact = {
        "artifact_version": "idle_consensus_operator_review.v1",
        "artifact_id": artifact_id,
        "created_at_utc": _iso(now_utc),
        "decision": "operator_review_required",
        "auto_execute": False,
        "operator_gate_required": True,
        "convergence": convergence,
        "transcript": payloads,
        "prohibited_actions": [
            "no_task_creation",
            "no_branch_creation",
            "no_pull_request_creation",
            "no_external_effect",
        ],
    }
    markdown = _markdown(artifact)
    _assert_no_implementation_hints(markdown)
    receipt_bundle = None
    if receipt_out_dir is not None:
        try:
            receipt_bundle = _emit_receipt_bundle(
                artifact=artifact,
                out_dir=receipt_out_dir,
                now_utc=now_utc,
            )
        except ValueError as exc:
            raise ArtifactError(
                "idle consensus artifact receipt bundle failed",
                {
                    "decision": "invalid_receipt_bundle",
                    "errors": [str(exc)],
                    "exit_code": 2,
                },
            ) from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(markdown, encoding="utf-8")
    report = {
        "decision": "operator_review_required",
        "artifact_id": artifact_id,
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "convergence_status": status,
        "auto_execute": False,
        "operator_gate_required": True,
    }
    if receipt_bundle is not None:
        report["receipt_bundle"] = receipt_bundle
    return report


class ArtifactError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ArtifactError(
            f"missing bridge events file: {path}",
            {
                "decision": "missing_events",
                "errors": [f"missing bridge events file: {path}"],
                "exit_code": 2,
            },
        )
    events: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ArtifactError(
                "bridge events contain invalid JSON",
                {
                    "decision": "invalid_events",
                    "errors": [f"line {line_no}: {exc.msg}"],
                    "exit_code": 2,
                },
            ) from exc
        if not isinstance(event, dict):
            raise ArtifactError(
                "bridge events contain non-object JSON",
                {
                    "decision": "invalid_events",
                    "errors": [f"line {line_no}: event must be an object"],
                    "exit_code": 2,
                },
            )
        events.append(event)
    return events


def _idle_payloads(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.get("protocol_version") == "idle-protocol.v1":
            payloads.append(dict(event))
            continue
        payload = event.get("payload")
        if isinstance(payload, Mapping) and payload.get("protocol_version") == "idle-protocol.v1":
            payloads.append(dict(payload))
    return payloads


def _refuse_private_markers(payloads: Sequence[Mapping[str, Any]]) -> None:
    text = json.dumps(payloads, sort_keys=True)
    for marker in PRIVATE_MARKERS:
        if marker in text:
            raise ArtifactError(
                "privacy marker detected in idle transcript",
                {
                    "decision": "privacy_marker_detected",
                    "errors": [f"idle transcript contains {marker}"],
                    "exit_code": 2,
                },
            )


def _artifact_id(
    convergence: Mapping[str, Any],
    payloads: Sequence[Mapping[str, Any]],
) -> str:
    target = (
        convergence.get("target_proposal_id")
        or "-".join(convergence.get("finalist_proposal_ids", [])[:3])
        or payloads[-1].get("proposal_id")
        or "unknown"
    )
    return _slug(f"idle-consensus-{target}")


def _slug(value: object) -> str:
    text = str(value).lower()
    text = re.sub(r"[^a-z0-9_.:-]+", "-", text)
    return text.strip("-")[:120] or "idle-consensus-unknown"


def _markdown(artifact: Mapping[str, Any]) -> str:
    convergence = artifact["convergence"]
    lines = [
        "# Idle Consensus Operator Review",
        "",
        "Operator review required before any work begins.",
        "",
        f"- Artifact: `{artifact['artifact_id']}`",
        f"- Created UTC: `{artifact['created_at_utc']}`",
        f"- Convergence: `{convergence['status']}`",
        f"- Auto execute: `{str(artifact['auto_execute']).lower()}`",
        f"- Operator gate required: `{str(artifact['operator_gate_required']).lower()}`",
        "",
        "This artifact is evidence only. It authorizes no task creation, no branch creation, no pull request creation, and no external effect.",
        "",
        "## Transcript",
    ]
    for payload in artifact["transcript"]:
        lines.extend(
            [
                "",
                f"### Round {payload['round_number']}: {payload['event_type']}",
                "",
                f"- Proposal id: `{payload['proposal_id']}`",
                f"- Problem: {payload['problem_statement']}",
                f"- Tradeoff: {payload['tradeoff_axis']}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _emit_receipt_bundle(
    *,
    artifact: Mapping[str, Any],
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    artifact_id = str(artifact["artifact_id"])
    evaluation = build_evaluation_result(
        case_id=f"case:idle_consensus_artifact:{artifact_id}",
        subject_type="peer_review",
        target_payload=dict(artifact),
        risk_class="local_artifact",
        expected_gate="review",
        actual_gate="review",
        verifier_path=[
            "idle_protocol_quality_validator",
            "idle_protocol_convergence_detector",
            "idle_consensus_artifact_guard",
            "magma_receipt_verifier_v1",
        ],
        solver_selection=[
            "idle_protocol_convergence_detector",
            "idle_consensus_artifact_writer",
        ],
        policy_version="policy:idle_consensus_artifact:v1",
        charter_version="charter:v1",
        domain_threshold_version="threshold:idle_protocol:v1",
        verdict="review",
        reason_codes=[
            "idle_consensus_artifact:operator_review_required",
            f"convergence:{artifact['convergence']['status']}",
            "receipt_bundle:opt_in",
        ],
        confidence_score=1.0,
        uncertainty_sources=[],
    )
    evaluation["operator_required"] = True
    receipt = build_magma_receipt(
        event_id=f"magma:idle_consensus_artifact:{artifact_id}",
        ts_utc=_iso(now_utc),
        risk_class="local_artifact",
        payload=artifact,
        evaluation_result=evaluation,
        previous_receipt=None,
        policy_digest=sha256_digest({"policy_version": evaluation["policy_version"]}),
        charter_digest=sha256_digest({
            "charter_version": evaluation["charter_version"],
            "operator_gate_required": artifact["operator_gate_required"],
            "auto_execute": artifact["auto_execute"],
        }),
        rco_decision_digest=sha256_digest({
            "actual_gate": evaluation["actual_gate"],
            "decision": artifact["decision"],
            "prohibited_actions": artifact["prohibited_actions"],
        }),
        world_snapshot_digest=sha256_digest({
            "artifact_id": artifact_id,
            "created_at_utc": artifact["created_at_utc"],
            "convergence_status": artifact["convergence"]["status"],
        }),
        solver_contract_digest=sha256_digest({
            "solver_selection": evaluation["solver_selection"],
            "policy_version": evaluation["policy_version"],
        }),
    )
    receipt["operator_gate_required"] = True
    return write_receipt_bundle(
        out_dir=out_dir,
        chain_id=f"magma:idle_consensus_artifact:{artifact_id}:v0",
        entries=[
            ReceiptBundleEntry(
                label="artifact",
                payload=dict(artifact),
                evaluation_result=evaluation,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _assert_no_implementation_hints(markdown: str) -> None:
    lowered = markdown.lower()
    for hint in IMPLEMENTATION_HINTS:
        if hint in lowered:
            raise ArtifactError(
                "artifact contains prohibited implementation hint",
                {
                    "decision": "artifact_hint_refused",
                    "errors": [f"prohibited phrase: {hint}"],
                    "exit_code": 2,
                },
            )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
