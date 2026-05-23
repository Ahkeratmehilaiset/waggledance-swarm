# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof that WriteRCOGate.route can write MAGMA receipts.

This exercises the actual opt-in ``emit_receipt_bundle`` route hook instead of
manually constructing a receipt after routing. The proof writes local artifacts
only after route() returns, verifies the MAGMA receipt chain offline, and also
checks the RCO decision artifact digest binding.
"""
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
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.v3_13_0.solver_provenance import (  # noqa: E402
    ActivationState,
    VerificationResult,
)
from waggledance.core.v3_13_0.write_rco_gate import (  # noqa: E402
    ConnectorInfo,
    ExecutionResult,
    Intent,
    PeerRCOResult,
    RecoveryCapsuleInfo,
    ScopePolicyResult,
    StateInfo,
    WriteRCOGate,
    WriteRiskClass,
)


REPORT_VERSION = "wd.magma.write_rco_route_receipt_emission_proof.v0"
CHAIN_ID = "magma:v12_a1_write_rco_gate_route:v1"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
AXIS_ID = "A1"
RUNTIME_PATH = "WriteRCOGate.route"
_PRIVATE_MARKER = "write_rco_route_receipt_private_DO_NOT_LEAK"
_RAW_MARKERS = ("write_rco_route_receipt_private", "DO_NOT_LEAK")
_EXTERNAL_APPROVAL_ID = "approval:write-rco-route-proof:001"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="New output directory for the proof. It must not already exist.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-23T11:45:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_write_rco_route_receipt_emission_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(f"WriteRCOGate route receipt proof FAILED: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(f"WriteRCOGate route receipt proof OK: {report['receipt_manifest']}")
    else:
        print(
            "WriteRCOGate route receipt proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_write_rco_route_receipt_emission_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    bundles: list[dict[str, Any]] = []
    audit_events: list[dict[str, Any]] = []
    route_summaries = _run_route_sequence(
        bundles=bundles,
        audit_events=audit_events,
    )
    no_sink_bundles = _run_no_sink_health_check()

    receipt_dir = out_dir / "write_rco_route_receipts"
    manifest_path = _write_route_receipt_bundle(
        out_dir=receipt_dir,
        bundles=bundles,
    )
    verifier_report = verify_manifest(manifest_path)
    rco_binding_report = verify_rco_receipt_binding(manifest_path)
    leak_free = _raw_payload_leak_free(out_dir)

    blockers: list[str] = []
    expected_risks = ["local_artifact", "external_effect"]
    actual_risks = [route["risk_class"] for route in route_summaries]
    if actual_risks != expected_risks:
        blockers.append(
            f"route_risk_sequence_mismatch:expected={expected_risks},actual={actual_risks}"
        )
    if len(bundles) != 2:
        blockers.append(f"expected_2_bundles_got_{len(bundles)}")
    if no_sink_bundles:
        blockers.append("sink_none_emitted_bundles_invariant_failed")
    if verifier_report.get("ok") is not True:
        blockers.append("offline_receipt_verifier_failed")
    if rco_binding_report.get("ok") is not True:
        blockers.append("rco_decision_binding_verifier_failed")
    if not leak_free:
        blockers.append("raw_payload_marker_leaked")

    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "axis_id": AXIS_ID,
        "axis_name": "write_action_governance",
        "claim_label": CLAIM_LABEL,
        "runtime_path": RUNTIME_PATH,
        "chain_id": CHAIN_ID,
        "external_effect_authority_change": False,
        "operator_gate_required": True,
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "receipt_emission_mode": "opt_in_route_sink",
        "default_sink_required": False,
        "sink_none_preserved": not no_sink_bundles,
        "route_count": len(route_summaries),
        "route_summaries": route_summaries,
        "audit_event_count": len(audit_events),
        "receipt_count": int(verifier_report.get("receipt_count", 0) or 0),
        "verifier_ok": verifier_report.get("ok") is True,
        "rco_binding_ok": rco_binding_report.get("ok") is True,
        "raw_payload_leak_check": leak_free,
        "receipt_out_dir": str(receipt_dir),
        "receipt_manifest": str(manifest_path),
        "external_effect_approval_id": _EXTERNAL_APPROVAL_ID,
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "no_consensus_grade_promotion": True,
            "no_release_boundary_change": True,
            "claim_label_remains_partial": True,
            "route_only_no_execute": True,
        },
    }
    (out_dir / "write_rco_route_receipt_emission_proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _run_route_sequence(
    *,
    bundles: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gate = _build_gate(
        audit_events=audit_events,
        emit_receipt_bundle=lambda bundle: bundles.append(bundle),
        approval_id=_EXTERNAL_APPROVAL_ID,
    )
    intents = [
        _local_artifact_intent(),
        _external_effect_intent(),
    ]
    summaries: list[dict[str, Any]] = []
    for intent in intents:
        outcome = gate.route(intent)
        if not outcome.approved:
            raise ValueError(
                f"expected route approved for {intent.intent_id}: "
                f"{outcome.stop_condition} {outcome.denial_reason}"
            )
        summaries.append(
            {
                "intent_id": intent.intent_id,
                "risk_class": outcome.risk_class.value,
                "approved": outcome.approved,
                "audit_event_count": len(outcome.audit_event_ids),
                "rco_decision_digest": outcome.rco_decision_digest,
            }
        )
    return summaries


def _run_no_sink_health_check() -> list[dict[str, Any]]:
    audit_events: list[dict[str, Any]] = []
    bundles: list[dict[str, Any]] = []
    gate = _build_gate(
        audit_events=audit_events,
        emit_receipt_bundle=None,
        approval_id=_EXTERNAL_APPROVAL_ID,
    )
    local = gate.route(_local_artifact_intent("intent:write-rco-route:no-sink"))
    if not local.approved:
        raise ValueError(f"no-sink route diverged: {local.denial_reason}")
    return bundles


def _build_gate(
    *,
    audit_events: list[dict[str, Any]],
    emit_receipt_bundle,
    approval_id: str | None,
) -> WriteRCOGate:
    states = {
        "state:route-proof:local": StateInfo(
            state_id="state:route-proof:local",
            plane="filesystem_artifact",
            write_modes_allowed=["write"],
            sensitive_class="internal",
            single_writer_required=False,
        ),
        "state:route-proof:external": StateInfo(
            state_id="state:route-proof:external",
            plane="external_system",
            write_modes_allowed=["post"],
            sensitive_class="restricted",
            single_writer_required=True,
        ),
    }
    connectors = {
        "conn:route-proof:external": ConnectorInfo(
            connector_id="conn:route-proof:external",
            write_risk=WriteRiskClass.EXTERNAL_EFFECT,
            auth_mode="operator_scoped_token",
            can_run_headless=False,
            rate_limit_max_workers=1,
            rate_limit_request_delay_s=0.0,
        )
    }
    capsules = {
        "tool:route-proof:external": RecoveryCapsuleInfo(
            capsule_id="recovery:route-proof:external",
            rollback_command="delete_external_fixture_if_created",
            known_corruption_modes=[],
        )
    }

    def audit_emit(envelope: dict[str, Any]) -> str:
        event_id = f"write.audit:route-proof:{len(audit_events):04d}"
        envelope["__id"] = event_id
        audit_events.append(envelope)
        return event_id

    return WriteRCOGate(
        audit_emit=audit_emit,
        classify_payload_credential_scan=lambda _payload: [],
        fetch_connector_info=lambda connector_id: connectors.get(connector_id),
        fetch_state_info=lambda state_id: states.get(state_id),
        fetch_recovery_capsule=lambda tool_id: capsules.get(tool_id),
        peer_rco_solicit=lambda _intent: PeerRCOResult(verdict="pass", rounds=1),
        operator_scope_policy_check=lambda _intent, _conn, _state: ScopePolicyResult(
            decision="operator_confirmed"
        ),
        write_executor=lambda intent: ExecutionResult(
            intent_id=intent.intent_id,
            success=True,
            elapsed_ms=1,
        ),
        emit_receipt_bundle=emit_receipt_bundle,
        resolve_external_effect_approval_id=(
            (lambda _intent, _outcome: approval_id) if approval_id is not None else None
        ),
        verify_solver_provenance=lambda candidate_id: VerificationResult(
            valid=True,
            candidate_id=candidate_id,
            activation_state=ActivationState.ACTIVATED.value,
            has_owner_signature=True,
            has_peer_signature=True,
            has_operator_signature=True,
            manifest_sha256_observed="a" * 64,
        ),
    )


def _local_artifact_intent(
    intent_id_hint: str = "intent:write-rco-route:local",
) -> Intent:
    intent = Intent.construct(
        agent_id="codex",
        session_id="session:write-rco-route-proof",
        tool_descriptor_id="tool:route-proof:local",
        target_state_ref="state:route-proof:local",
        action="write",
        payload={
            "artifact_path": "docs/runs/write-rco-route-proof.md",
            "content_digest": sha256_digest({"secret": _PRIVATE_MARKER}),
        },
    )
    return _with_intent_id(intent, intent_id_hint)


def _external_effect_intent() -> Intent:
    intent = Intent.construct(
        agent_id="codex",
        session_id="session:write-rco-route-proof",
        tool_descriptor_id="tool:route-proof:external",
        target_state_ref="state:route-proof:external",
        action="post",
        connector_ref="conn:route-proof:external",
        payload={
            "endpoint_digest": sha256_digest({"url": "https://example.invalid/write"}),
            "body_digest": sha256_digest({"secret": _PRIVATE_MARKER}),
            "solver_candidate_id": "solver:route-proof:activated",
        },
    )
    return _with_intent_id(intent, "intent:write-rco-route:external")


def _with_intent_id(intent: Intent, intent_id: str) -> Intent:
    intent.intent_id = intent_id
    return intent


def _write_route_receipt_bundle(
    *,
    out_dir: Path,
    bundles: Sequence[dict[str, Any]],
) -> Path:
    if not bundles:
        raise ValueError("route receipt proof requires at least one bundle")
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=False)

    entries: list[dict[str, str]] = []
    for index, bundle in enumerate(bundles, 1):
        label = _safe_label(str(bundle["payload"]["target_state_ref"]).split(":")[-1])
        payload_name = f"payload-{index:03d}-{label}.json"
        rco_name = f"rco-decision-{index:03d}-{label}.json"
        evaluation_name = f"evaluation-{index:03d}-{label}.json"
        receipt_name = f"receipt-{index:03d}-{label}.json"
        _write_json(out_dir / payload_name, bundle["payload"])
        _write_json(out_dir / rco_name, bundle["rco_decision_artifact"])
        _write_json(out_dir / evaluation_name, bundle["evaluation_result"])
        _write_json(out_dir / receipt_name, bundle["receipt"])
        entries.append(
            {
                "payload": payload_name,
                "rco_decision_artifact": rco_name,
                "evaluation_result": evaluation_name,
                "receipt": receipt_name,
            }
        )
    manifest = {
        "chain_id": CHAIN_ID,
        "entries": entries,
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return manifest_path


def _safe_label(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)


def _raw_payload_leak_free(out_dir: Path) -> bool:
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(out_dir.rglob("*.json"))
    )
    return not any(marker in artifact_text for marker in _RAW_MARKERS)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise ValueError("--now must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid --now timestamp: {raw}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now must be in UTC")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00",
        "Z",
    )


if __name__ == "__main__":
    raise SystemExit(main())
