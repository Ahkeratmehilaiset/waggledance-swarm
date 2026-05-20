# SPDX-License-Identifier: BUSL-1.1
"""Run a real WriteRCOGate route and bind its decision to a MAGMA receipt."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_rco_receipt_binding_demo import verify_rco_receipt_binding  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.v3_13_0.write_rco_gate import (  # noqa: E402
    ExecutionResult,
    Intent,
    PeerRCOResult,
    RecoveryCapsuleInfo,
    ScopePolicyResult,
    StateInfo,
    WriteRCOGate,
    WriteRiskClass,
    _artifact_intent_summary,
    build_rco_decision_artifact_for_gate,
)
from waggledance.core.v3_13_0.solver_provenance import (  # noqa: E402
    ActivationState,
    VerificationResult,
)


DEMO_VERSION = "magma.write_rco_gate_receipt_demo.v0"
PRIVATE_MARKER = "write_rco_route_secret_DO_NOT_LEAK"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a real WriteRCOGate route and emit a local receipt bundle.",
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--now",
        default="2026-05-20T12:30:00Z",
        help="UTC timestamp for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_write_rco_gate_receipt_demo(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now),
        )
    except ValueError as exc:
        print(f"WriteRCOGate receipt demo FAILED: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            "WriteRCOGate receipt demo OK: "
            f"{report['binding_report']['receipt_count']} receipt in {report['out_dir']}"
        )
    return 0


def build_write_rco_gate_receipt_demo(
    *,
    out_dir: Path,
    now_utc: datetime,
) -> dict[str, Any]:
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)

    # Privacy canary: this local value must not enter emitted artifacts.
    _private_operator_note = {"secret": PRIVATE_MARKER}
    audit_events: list[dict[str, Any]] = []
    gate = _build_local_artifact_gate(audit_events)
    intent = Intent.construct(
        agent_id="codex",
        session_id="session:v12-write-rco-route-demo",
        tool_descriptor_id="tool:local_artifact_writer",
        target_state_ref="state:local_artifact:demo",
        action="write",
        payload={
            "artifact_path": "docs/runs/example-local-report.md",
            "content_digest": sha256_digest({"redacted_content": "demo report"}),
        },
    )
    outcome = gate.route(intent)
    intent_payload = _artifact_intent_summary(intent)
    rco_decision = build_rco_decision_artifact_for_gate(
        intent,
        outcome,
        ts_utc=_iso(now_utc),
        scope_policy_decision="not_applicable",
        peer_rco_verdict="not_requested",
    )
    evaluation = build_evaluation_result(
        case_id="case:write_rco_gate:receipt-demo:001",
        subject_type="policy",
        target_payload=intent_payload,
        risk_class=outcome.risk_class.value,
        expected_gate="allow",
        actual_gate=rco_decision["gate_decision"],
        verifier_path=[
            "write_rco_gate_route",
            "rco_decision_artifact_v0",
            "magma_receipt_v1",
            "rco_receipt_binding_verifier",
        ],
        solver_selection=[],
        policy_version=rco_decision["policy_version"],
        charter_version=rco_decision["charter_version"],
        domain_threshold_version="threshold:write_rco_gate:v1",
        verdict="pass" if outcome.approved else "review",
        reason_codes=list(rco_decision["reason_codes"]),
        confidence_score=0.95,
        uncertainty_sources=[],
    )
    receipt = build_magma_receipt(
        event_id="magma:write_rco_gate_receipt_demo:001",
        ts_utc=_iso(now_utc),
        risk_class=outcome.risk_class.value,
        payload=intent_payload,
        evaluation_result=evaluation,
        policy_digest=sha256_digest({"policy_version": rco_decision["policy_version"]}),
        charter_digest=sha256_digest({"charter_version": rco_decision["charter_version"]}),
        rco_decision_digest=sha256_digest(rco_decision),
        world_snapshot_digest=sha256_digest({
            "state_ref": intent.target_state_ref,
            "route": "local_artifact",
        }),
        solver_contract_digest=sha256_digest({"solver_selection": []}),
    )

    _write_json(out_dir / "intent-001.json", intent_payload)
    _write_json(out_dir / "rco-decision-001.json", rco_decision)
    _write_json(out_dir / "evaluation-001.json", evaluation)
    _write_json(out_dir / "receipt-001.json", receipt)
    _write_json(out_dir / "audit-events.json", audit_events)
    manifest = {
        "chain_id": "magma:write_rco_gate_receipt_demo:v0",
        "entries": [
            {
                "payload": "intent-001.json",
                "rco_decision_artifact": "rco-decision-001.json",
                "evaluation_result": "evaluation-001.json",
                "receipt": "receipt-001.json",
            }
        ],
    }
    _write_json(out_dir / "manifest.json", manifest)
    binding_report = verify_rco_receipt_binding(out_dir / "manifest.json")

    return {
        "demo_version": DEMO_VERSION,
        "writes_applied": False,
        "out_dir": str(out_dir),
        "manifest": str(out_dir / "manifest.json"),
        "audit_event_count": len(audit_events),
        "gate_outcome": {
            "risk_class": outcome.risk_class.value,
            "approved": outcome.approved,
            "audit_event_ids": list(outcome.audit_event_ids),
        },
        "binding_report": binding_report,
    }


def _build_local_artifact_gate(audit_events: list[dict[str, Any]]) -> WriteRCOGate:
    def audit_emit(envelope: dict[str, Any]) -> str:
        event_id = f"write.audit:demo:{len(audit_events):04d}"
        envelope["__id"] = event_id
        audit_events.append(envelope)
        return event_id

    states = {
        "state:local_artifact:demo": StateInfo(
            state_id="state:local_artifact:demo",
            plane="filesystem_artifact",
            write_modes_allowed=["write"],
            sensitive_class="internal",
            single_writer_required=False,
        )
    }
    return WriteRCOGate(
        audit_emit=audit_emit,
        classify_payload_credential_scan=lambda _payload: [],
        fetch_connector_info=lambda _connector_id: None,
        fetch_state_info=lambda state_id: states.get(state_id),
        fetch_recovery_capsule=lambda _tool_id: RecoveryCapsuleInfo(
            capsule_id="recovery:local_artifact_demo",
            rollback_command="delete_artifact_if_created",
            known_corruption_modes=[],
        ),
        peer_rco_solicit=lambda _intent: PeerRCOResult(verdict="pass", rounds=1),
        operator_scope_policy_check=lambda _intent, _conn, _state: ScopePolicyResult(
            decision="auto_approved"
        ),
        write_executor=lambda intent: ExecutionResult(
            intent_id=intent.intent_id,
            success=True,
            elapsed_ms=1,
        ),
        verify_solver_provenance=lambda candidate_id: VerificationResult(
            valid=True,
            candidate_id=candidate_id,
            activation_state=ActivationState.ACTIVATED.value,
            has_owner_signature=True,
            has_peer_signature=True,
            has_operator_signature=True,
            manifest_sha256_observed="0" * 64,
        ),
    )


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
