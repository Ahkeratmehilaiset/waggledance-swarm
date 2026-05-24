from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_solver_provenance_receipt_emission_proof import (
    AXIS_ID,
    CHAIN_ID,
    CLAIM_LABEL,
    REPORT_VERSION,
    build_solver_provenance_receipt_emission_proof,
)
from tools.verify_magma_receipt import verify_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_solver_provenance_receipt_emission_proof.py"
FIXED_NOW = datetime(2026, 5, 23, 10, 30, tzinfo=timezone.utc)


def test_proof_emits_chained_receipt_bundle(tmp_path: Path) -> None:
    out_dir = tmp_path / "a4-proof"

    report = build_solver_provenance_receipt_emission_proof(
        out_dir=out_dir, now_utc=FIXED_NOW
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert report["chain_id"] == CHAIN_ID
    assert report["risk_class"] == "local_artifact"
    assert report["candidate_id"] == "cand:a4_proof_demo"
    assert report["target_domain"] == "DOM-011"
    assert report["transitions"] == [
        "activation_authorised",
        "activation_revoked",
    ]
    assert report["receipt_count"] == 2
    assert report["verifier_ok"] is True
    assert report["raw_payload_leak_check"] is True
    assert report["sink_none_preserved"] is True
    assert report["external_effect_authority_change"] is False
    assert report["operator_gate_required"] is False
    assert report["external_writes_applied"] is False
    assert report["local_artifacts_written"] is True
    assert report["receipt_emission_mode"] == "opt_in_disk_bundle_sink"
    assert report["default_sink_required"] is False
    guardrails = report["no_overclaim_guardrails"]
    assert guardrails["not_a_competitor_benchmark"] is True
    assert guardrails["no_consensus_grade_promotion"] is True
    assert guardrails["no_release_boundary_change"] is True
    assert guardrails["claim_label_remains_partial"] is True

    manifest_path = Path(report["receipt_manifest"])
    assert manifest_path.exists()
    verifier = verify_manifest(manifest_path)
    assert verifier["ok"] is True
    assert verifier["receipt_count"] == 2


def test_proof_emits_chain_with_linked_prev_hash(tmp_path: Path) -> None:
    """The second emission's receipt has prev_receipt_hash set to the first
    receipt's sha256, exercising the chain head tracking in
    SolverProvenance._emit_transition_receipt_bundle (additive in this PR)."""
    out_dir = tmp_path / "a4-proof"

    report = build_solver_provenance_receipt_emission_proof(
        out_dir=out_dir, now_utc=FIXED_NOW
    )
    receipt_dir = Path(report["receipt_out_dir"])
    receipt_1 = json.loads(
        (receipt_dir / "receipt-001-activation_authorised.json").read_text(
            encoding="utf-8"
        )
    )
    receipt_2 = json.loads(
        (receipt_dir / "receipt-002-activation_revoked.json").read_text(
            encoding="utf-8"
        )
    )

    assert receipt_1["prev_receipt_hash"] is None
    assert receipt_2["prev_receipt_hash"] is not None
    from waggledance.core.magma.canonical import sha256_digest

    assert receipt_2["prev_receipt_hash"] == sha256_digest(receipt_1)


def test_proof_rejects_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "a4-proof"
    out_dir.mkdir()

    try:
        build_solver_provenance_receipt_emission_proof(
            out_dir=out_dir, now_utc=FIXED_NOW
        )
    except ValueError as exc:
        assert "out_dir must not exist" in str(exc)
        return
    raise AssertionError("expected ValueError on pre-existing out_dir")


def test_proof_detects_tampered_evaluation_metadata(tmp_path: Path) -> None:
    """Mutating one receipt entry's evaluation_result on disk must cause
    verify_manifest to reject the chain, demonstrating the fail-closed
    tamper-detection contract on the A4 lifecycle."""
    out_dir = tmp_path / "a4-proof"

    report = build_solver_provenance_receipt_emission_proof(
        out_dir=out_dir, now_utc=FIXED_NOW
    )
    receipt_dir = Path(report["receipt_out_dir"])
    evaluation_path = receipt_dir / "evaluation-001-activation_authorised.json"
    evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
    evaluation["verdict"] = "review"  # was "pass"
    evaluation_path.write_text(
        json.dumps(evaluation, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verifier = verify_manifest(Path(report["receipt_manifest"]))
    assert verifier["ok"] is False
    assert any(
        "evaluation_result_digest mismatch" in str(err)
        for err in verifier.get("errors", [])
    )


def test_proof_detects_tampered_payload(tmp_path: Path) -> None:
    """Mutating one receipt entry's payload on disk must cause verify_manifest
    to reject the chain."""
    out_dir = tmp_path / "a4-proof"

    report = build_solver_provenance_receipt_emission_proof(
        out_dir=out_dir, now_utc=FIXED_NOW
    )
    receipt_dir = Path(report["receipt_out_dir"])
    payload_path = receipt_dir / "payload-002-activation_revoked.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["transition"] = "tampered_transition"
    payload_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    verifier = verify_manifest(Path(report["receipt_manifest"]))
    assert verifier["ok"] is False
    assert verifier.get("errors")


def test_proof_does_not_leak_private_revocation_reason(tmp_path: Path) -> None:
    """The proof injects a private revocation reason marker; receipts and
    artifacts must not contain that marker substring anywhere on disk."""
    out_dir = tmp_path / "a4-proof"

    build_solver_provenance_receipt_emission_proof(
        out_dir=out_dir, now_utc=FIXED_NOW
    )

    combined = ""
    for path in out_dir.rglob("*.json"):
        combined += path.read_text(encoding="utf-8")

    assert "operator_revocation_reason_private_DO_NOT_LEAK" not in combined
    assert "DO_NOT_LEAK" not in combined


def test_cli_json_reports_a4_proof(tmp_path: Path) -> None:
    out_dir = tmp_path / "a4-proof"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-05-23T10:30:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["axis_id"] == AXIS_ID
    assert payload["claim_label"] == CLAIM_LABEL
    assert payload["transitions"] == [
        "activation_authorised",
        "activation_revoked",
    ]
    assert payload["receipt_count"] == 2


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "a4-proof"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_failed_sink_preserves_chain_head_after_durable_activation() -> None:
    """Per Codex RCO BLOCK on #614 head ae5427c8: if emit_receipt_bundle
    raises, the chain head must NOT advance (bundle was never persisted).
    Because activation is already durable at that point, a subsequent retry is
    idempotent and must not emit a duplicate transition receipt.
    """
    from waggledance.core.v3_13_0.solver_provenance import (
        ActivationState,
        SigningRole,
        SolverCandidateRecord,
        SolverProvenance,
        canonicalize_manifest,
    )

    canonical, digest = canonicalize_manifest(
        {"candidate_id": "cand:failed_sink", "template_family": "X", "version": 1}
    )
    candidate = SolverCandidateRecord(
        candidate_id="cand:failed_sink",
        manifest_canonical_json=canonical,
        manifest_sha256=digest,
        target_domain="DOM-011",
        target_write_risk="local_artifact",
    )
    store: dict[str, SolverCandidateRecord] = {candidate.candidate_id: candidate}
    audit_events: list[dict] = []
    bridge_events: list[dict] = []

    def failing_sink(_bundle: dict) -> None:
        raise RuntimeError("sink boom")

    prov = SolverProvenance(
        fetch_candidate=lambda cid: store.get(cid),
        update_candidate=lambda rec: store.__setitem__(rec.candidate_id, rec),
        emit_magma_event=lambda env: (
            env.__setitem__("__id", f"evt_{len(audit_events):04d}")
            or audit_events.append(env)
            or env["__id"]
        ),
        emit_bridge_event=lambda env: bridge_events.append(env),
        operator_scope_policy_active=lambda _ref: True,
        emit_receipt_bundle=failing_sink,
    )

    # Owner + peer signing must succeed; sign() does not emit a transition
    # receipt, so the failing sink is not yet invoked.
    prov.sign(
        candidate_id=candidate.candidate_id,
        signing_agent_id="claude",
        signing_role=SigningRole.OWNER.value,
        bridge_event_ref="bridge:owner",
        operator_scope_policy_ref="policy:home",
    )
    prov.sign(
        candidate_id=candidate.candidate_id,
        signing_agent_id="codex",
        signing_role=SigningRole.PEER.value,
        bridge_event_ref="bridge:peer",
        operator_scope_policy_ref="policy:home",
    )
    assert store[candidate.candidate_id].activation_state == ActivationState.SIGNED.value
    assert prov._last_emitted_receipt is None

    # First activate(): candidate update is durable before receipt emission.
    # A failing post-update sink must still leave the receipt chain head untouched.
    raised = False
    try:
        prov.activate(candidate_id=candidate.candidate_id)
    except RuntimeError as exc:
        raised = True
        assert "sink boom" in str(exc)
    assert raised, "expected RuntimeError from failing sink"
    assert store[candidate.candidate_id].activation_state == ActivationState.ACTIVATED.value
    assert prov._last_emitted_receipt is None, (
        "chain head must NOT advance on failed sink emission"
    )

    # Retry with a working sink: activate() is idempotent for the durable state
    # and must not emit a duplicate transition receipt.
    captured_bundles: list[dict] = []
    prov.emit_receipt_bundle = lambda bundle: captured_bundles.append(bundle)

    final_state = prov.activate(candidate_id=candidate.candidate_id)
    assert final_state == ActivationState.ACTIVATED
    assert store[candidate.candidate_id].activation_state == ActivationState.ACTIVATED.value
    assert captured_bundles == []
    assert prov._last_emitted_receipt is None


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(tmp_path / "a4-proof"),
            "--now",
            "2026-05-23T13:30:00+03:00",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr
