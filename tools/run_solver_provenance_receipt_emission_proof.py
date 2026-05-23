# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof that SolverProvenance transitions can write a MAGMA receipt bundle.

Exercises the production-shaped sign -> sign -> activate -> revoke lifecycle
with an opt-in receipt sink wired into SolverProvenance.emit_receipt_bundle.
Writes payload/evaluation/receipt triples to a local on-disk bundle and
verifies the chain offline. Mirrors tools/run_runtime_receipt_emission_proof.py
(#606) and the A3 v1 binding pattern (#610), tailored to A4 solver-growth
lifecycle.

This is the C4/A4 implementation slice per the 2026-05-23 100h sprint plan:
the SolverProvenance code path was already real, the receipt builder was
already real, and the optional sink hook was already real but had zero
production callers. This proof gives the substrate a verifiable on-disk
A4 receipt chain. claim_label stays MEASURED_LOCAL_PARTIAL.
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

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)
from waggledance.core.v3_13_0.solver_provenance import (  # noqa: E402
    ActivationState,
    RevocationActor,
    SigningRole,
    SolverCandidateRecord,
    SolverProvenance,
    canonicalize_manifest,
)


REPORT_VERSION = "wd.magma.solver_provenance_receipt_emission_proof.v0"
CHAIN_ID = "magma:v12_a4_solver_provenance_axis:v1"
CLAIM_LABEL = "MEASURED_LOCAL_PARTIAL"
AXIS_ID = "A4"
RUNTIME_PATH = "SolverProvenance.{sign,activate,revoke}"
_CANDIDATE_ID = "cand:a4_proof_demo"
_TARGET_DOMAIN = "DOM-011"
_TARGET_WRITE_RISK = "local_artifact"
_OPERATOR_SCOPE_REF = "policy:home_factory"
_BRIDGE_EVENT_REF_OWNER = "bridge:a4_owner_sign"
_BRIDGE_EVENT_REF_PEER = "bridge:a4_peer_sign"
_PRIVATE_REASON_MARKER = "operator_revocation_reason_private_DO_NOT_LEAK"
_RAW_MARKERS = (_PRIVATE_REASON_MARKER, "DO_NOT_LEAK")


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
        help="Optional UTC timestamp override such as 2026-05-23T10:30:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_solver_provenance_receipt_emission_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"solver provenance receipt emission proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "solver provenance receipt emission proof OK: "
            f"{report['receipt_manifest']}"
        )
    else:
        print(
            "solver provenance receipt emission proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_solver_provenance_receipt_emission_proof(
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
    bridge_events: list[dict[str, Any]] = []
    transitions = _run_lifecycle_sequence(
        bundles=bundles,
        audit_events=audit_events,
        bridge_events=bridge_events,
    )

    no_sink_bundles = _run_no_sink_health_check()

    receipt_dir = out_dir / "solver_provenance_receipts"
    entries: list[ReceiptBundleEntry] = []
    for bundle in bundles:
        transition = _extract_transition(bundle)
        entries.append(
            ReceiptBundleEntry(
                label=transition,
                payload=bundle["payload"],
                evaluation_result=bundle["evaluation_result"],
                receipt=bundle["receipt"],
            )
        )
    bundle_report = write_receipt_bundle(
        out_dir=receipt_dir,
        chain_id=CHAIN_ID,
        entries=entries,
        verify_manifest=verify_manifest,
    )

    manifest_path = Path(str(bundle_report["manifest"]))
    verifier_report = verify_manifest(manifest_path)
    leak_free = _raw_payload_leak_free(out_dir)

    blockers: list[str] = []
    # SolverProvenance emits a receipt bundle only on STATE TRANSITIONS, not
    # on signature inputs. sign() adds signatures to the chain but does not
    # emit a transition receipt (signatures are evidence; transitions are
    # gate decisions). Therefore sign->sign->activate->revoke emits TWO
    # bundles: activation_authorised on activate, activation_revoked on
    # revoke.
    expected_transitions = ["activation_authorised", "activation_revoked"]
    actual_transitions = [_extract_transition(b) for b in bundles]
    if actual_transitions != expected_transitions:
        blockers.append(
            "transition_sequence_mismatch:"
            f"expected={expected_transitions},actual={actual_transitions}"
        )
    if len(bundles) != 2:
        blockers.append(f"expected_2_bundles_got_{len(bundles)}")
    if no_sink_bundles:
        blockers.append("sink_none_emitted_bundles_invariant_failed")
    if verifier_report.get("ok") is not True:
        blockers.append("offline_receipt_verifier_failed")
    if not leak_free:
        blockers.append("raw_payload_marker_leaked")

    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "axis_id": AXIS_ID,
        "axis_name": "solver_growth_lifecycle",
        "claim_label": CLAIM_LABEL,
        "runtime_path": RUNTIME_PATH,
        "chain_id": CHAIN_ID,
        "risk_class": _TARGET_WRITE_RISK,
        "candidate_id": _CANDIDATE_ID,
        "target_domain": _TARGET_DOMAIN,
        "external_effect_authority_change": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "local_artifacts_written": True,
        "receipt_emission_mode": "opt_in_disk_bundle_sink",
        "default_sink_required": False,
        "sink_none_preserved": not no_sink_bundles,
        "transitions": transitions,
        "audit_event_count": len(audit_events),
        "bridge_event_count": len(bridge_events),
        "receipt_count": int(verifier_report.get("receipt_count", 0) or 0),
        "verifier_ok": verifier_report.get("ok") is True,
        "raw_payload_leak_check": leak_free,
        "receipt_out_dir": str(receipt_dir),
        "receipt_manifest": str(manifest_path),
        "evaluation_result_version": "magma.evaluation_result.v0",
        "no_overclaim_guardrails": {
            "not_a_competitor_benchmark": True,
            "no_consensus_grade_promotion": True,
            "no_release_boundary_change": True,
            "claim_label_remains_partial": True,
        },
    }
    (out_dir / "solver_provenance_receipt_emission_proof.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _run_lifecycle_sequence(
    *,
    bundles: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    bridge_events: list[dict[str, Any]],
) -> list[str]:
    candidate = _build_candidate()
    store: dict[str, SolverCandidateRecord] = {candidate.candidate_id: candidate}

    def emit_magma_event(envelope: dict[str, Any]) -> str:
        event_id = f"evt_{len(audit_events):04d}"
        envelope["__id"] = event_id
        audit_events.append(envelope)
        return event_id

    def emit_bridge_event(envelope: dict[str, Any]) -> None:
        bridge_events.append(envelope)

    def emit_receipt_bundle(bundle: dict[str, Any]) -> None:
        bundles.append(bundle)

    prov = SolverProvenance(
        fetch_candidate=lambda cid: store.get(cid),
        update_candidate=lambda rec: store.__setitem__(rec.candidate_id, rec),
        emit_magma_event=emit_magma_event,
        emit_bridge_event=emit_bridge_event,
        operator_scope_policy_active=lambda _ref: True,
        emit_receipt_bundle=emit_receipt_bundle,
    )

    prov.sign(
        candidate_id=candidate.candidate_id,
        signing_agent_id="claude",
        signing_role=SigningRole.OWNER.value,
        bridge_event_ref=_BRIDGE_EVENT_REF_OWNER,
        operator_scope_policy_ref=_OPERATOR_SCOPE_REF,
    )
    prov.sign(
        candidate_id=candidate.candidate_id,
        signing_agent_id="codex",
        signing_role=SigningRole.PEER.value,
        bridge_event_ref=_BRIDGE_EVENT_REF_PEER,
        operator_scope_policy_ref=_OPERATOR_SCOPE_REF,
    )
    final_state = prov.activate(candidate_id=candidate.candidate_id)
    if final_state != ActivationState.ACTIVATED:
        raise ValueError(
            f"expected ACTIVATED after sign+sign+activate, got {final_state}"
        )
    revocation = prov.revoke(
        candidate_id=candidate.candidate_id,
        reason=_PRIVATE_REASON_MARKER,
        revoked_by=RevocationActor.OPERATOR.value,
    )
    if not revocation.success:
        raise ValueError(f"revocation failed: {revocation.reason}")

    return [_extract_transition(b) for b in bundles]


def _run_no_sink_health_check() -> list[dict[str, Any]]:
    """Confirm sink=None preserves the lifecycle and emits zero receipts."""
    candidate = _build_candidate()
    store: dict[str, SolverCandidateRecord] = {candidate.candidate_id: candidate}
    audit_events: list[dict[str, Any]] = []
    bridge_events: list[dict[str, Any]] = []
    bundles: list[dict[str, Any]] = []

    def emit_magma_event(envelope: dict[str, Any]) -> str:
        envelope["__id"] = f"evt_{len(audit_events):04d}"
        audit_events.append(envelope)
        return envelope["__id"]

    prov = SolverProvenance(
        fetch_candidate=lambda cid: store.get(cid),
        update_candidate=lambda rec: store.__setitem__(rec.candidate_id, rec),
        emit_magma_event=emit_magma_event,
        emit_bridge_event=lambda env: bridge_events.append(env),
        operator_scope_policy_active=lambda _ref: True,
        emit_receipt_bundle=None,
    )

    prov.sign(
        candidate_id=candidate.candidate_id,
        signing_agent_id="claude",
        signing_role=SigningRole.OWNER.value,
        bridge_event_ref=_BRIDGE_EVENT_REF_OWNER,
        operator_scope_policy_ref=_OPERATOR_SCOPE_REF,
    )
    prov.sign(
        candidate_id=candidate.candidate_id,
        signing_agent_id="codex",
        signing_role=SigningRole.PEER.value,
        bridge_event_ref=_BRIDGE_EVENT_REF_PEER,
        operator_scope_policy_ref=_OPERATOR_SCOPE_REF,
    )
    activated_state = prov.activate(candidate_id=candidate.candidate_id)
    if activated_state != ActivationState.ACTIVATED:
        raise ValueError(
            f"no-sink activate diverged from sink-wired path: {activated_state}"
        )
    revocation = prov.revoke(
        candidate_id=candidate.candidate_id,
        reason="no_sink_health_check_reason",
        revoked_by=RevocationActor.OPERATOR.value,
    )
    if not revocation.success:
        raise ValueError(
            f"no-sink revoke diverged from sink-wired path: {revocation.reason}"
        )

    return bundles


def _build_candidate() -> SolverCandidateRecord:
    canonical, digest = canonicalize_manifest(
        {
            "candidate_id": _CANDIDATE_ID,
            "template_family": "A4ProofDemoSolver",
            "version": 1,
        }
    )
    return SolverCandidateRecord(
        candidate_id=_CANDIDATE_ID,
        manifest_canonical_json=canonical,
        manifest_sha256=digest,
        target_domain=_TARGET_DOMAIN,
        target_write_risk=_TARGET_WRITE_RISK,
    )


def _extract_transition(bundle: dict[str, Any]) -> str:
    event_id = str(bundle["receipt"]["event_id"])
    # Format: magma:solver_provenance:<transition>:<candidate_id...>
    parts = event_id.split(":", 3)
    return parts[2] if len(parts) >= 3 else event_id


def _raw_payload_leak_free(out_dir: Path) -> bool:
    artifact_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(out_dir.rglob("*.json"))
    )
    return not any(marker in artifact_text for marker in _RAW_MARKERS)


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"--now requires a UTC timestamp: {value}")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
