# SPDX-License-Identifier: BUSL-1.1
"""Emit a local proof for hex-cell activation preflight.

The proof links:

1. hex-cell competition result,
2. non-authority promotion acceptance,
3. operator-gate authorization,
4. SolverProvenance ``activation_authorised`` MAGMA receipt,
5. a receipt-bound activation preflight artifact.

It deliberately does not grant runtime authority, mutate runtime traffic, or
change candidate state. It proves the T2 boundary immediately before a later
runtime-authority commit step.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Callable, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.solver_synthesis.hex_cell_competition import (  # noqa: E402
    HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE,
    HEX_CELL_ACTIVATION_PREFLIGHT_STATUS,
    HEX_CELL_COMPETITION_AUTHORITY_STATUS,
    HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE,
    HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE,
    build_hex_cell_activation_preflight,
    build_hex_cell_competition_result,
    build_hex_cell_operator_gate_authorization,
    build_hex_cell_promotion_acceptance,
)
from waggledance.core.solver_synthesis.solver_candidate_store import (  # noqa: E402
    SolverCandidate,
)
from waggledance.core.v3_13_0.solver_provenance import (  # noqa: E402
    SolverCandidateRecord,
    VerificationResult,
    build_solver_provenance_transition_receipt,
    canonicalize_manifest,
)


REPORT_VERSION = "wd.hex_cell_activation_preflight_proof.v0"
AXIS_ID = "T2"
CLAIM_LABEL = "MEASURED_LOCAL_PREFLIGHT"
CHAIN_ID = "magma:v12_t2_hex_cell_activation_preflight:v1"
_CAPABILITY_ID = "frost-risk-detection"
_CELL_ID = "thermal"


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
        help="Optional UTC timestamp override such as 2026-05-31T12:30:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_hex_cell_activation_preflight_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"hex-cell activation preflight proof FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print(
            "hex-cell activation preflight proof OK: "
            f"{report['proof_path']}"
        )
    else:
        print(
            "hex-cell activation preflight proof FAILED: "
            f"{', '.join(report['blockers'])}",
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_hex_cell_activation_preflight_proof(
    *,
    out_dir: Path,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(
        timezone.utc
    )
    out_dir = out_dir.resolve()
    if out_dir.exists():
        raise ValueError(f"out_dir must not exist: {out_dir}")
    if not out_dir.parent.exists():
        raise ValueError(f"out_dir parent does not exist: {out_dir.parent}")
    out_dir.mkdir()

    competition = build_hex_cell_competition_result(
        candidates=_candidates(),
        capability_id=_CAPABILITY_ID,
        scores={
            "cand-alpha": 0.82,
            "cand-beta": 0.91,
            "cand-gamma": 0.87,
        },
        evidence_refs={
            "cand-alpha": ["shadow_eval:alpha"],
            "cand-beta": ["shadow_eval:beta", "counterfactual:beta"],
            "cand-gamma": ["shadow_eval:gamma"],
        },
    )
    acceptance = build_hex_cell_promotion_acceptance(
        competition=competition
    )
    authorization = build_hex_cell_operator_gate_authorization(
        acceptance=acceptance,
        operator_approval_id="approval:hexcell:thermal:preflight:001",
        approved_by="operator:jkh",
        acceptance_receipt_digest=_acceptance_receipt_digest(acceptance),
    )
    solver_bundle = _solver_provenance_activation_bundle(
        authorization.accepted_candidate_id
    )
    preflight = build_hex_cell_activation_preflight(
        authorization=authorization,
        solver_provenance_bundle=solver_bundle,
    )
    forge_probes = _forge_probe_results(
        authorization=authorization,
        solver_bundle=solver_bundle,
    )

    blockers: list[str] = []
    if competition.authority_status != HEX_CELL_COMPETITION_AUTHORITY_STATUS:
        blockers.append("competition_authority_status_drift")
    if authorization.required_next_gate != (
        HEX_CELL_OPERATOR_GATE_AUTHORIZATION_NEXT_GATE
    ):
        blockers.append("authorization_next_gate_drift")
    if preflight.activation_preflight_status != (
        HEX_CELL_ACTIVATION_PREFLIGHT_STATUS
    ):
        blockers.append("preflight_status_drift")
    if preflight.required_next_gate != HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE:
        blockers.append("preflight_next_gate_drift")
    if not preflight.receipt_bound_activation_verified:
        blockers.append("receipt_bound_activation_not_verified")
    if preflight.runtime_authority_granted:
        blockers.append("runtime_authority_granted_in_preflight")
    if preflight.runtime_traffic_mutation_applied:
        blockers.append("runtime_traffic_mutated_in_preflight")
    if preflight.candidate_state_mutation_applied:
        blockers.append("candidate_state_mutated_in_preflight")
    if not all(item["rejected"] for item in forge_probes.values()):
        blockers.append("forge_probe_unexpectedly_passed")

    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": not blockers,
        "blockers": blockers,
        "axis_id": AXIS_ID,
        "claim_label": CLAIM_LABEL,
        "chain_id": CHAIN_ID,
        "competition": competition.to_dict(),
        "promotion_acceptance": acceptance.to_dict(),
        "operator_gate_authorization": authorization.to_dict(),
        "activation_preflight": preflight.to_dict(),
        "forge_probes": forge_probes,
        "no_overclaim_guardrails": {
            "operator_gate_cleared_but_runtime_authority_not_granted": (
                authorization.operator_gate_cleared is True
                and preflight.runtime_authority_granted is False
            ),
            "no_runtime_traffic_mutation": (
                preflight.runtime_traffic_mutation_applied is False
            ),
            "no_candidate_state_mutation": (
                preflight.candidate_state_mutation_applied is False
            ),
            "next_gate_is_separate_runtime_commit": (
                preflight.required_next_gate
                == HEX_CELL_ACTIVATION_PREFLIGHT_NEXT_GATE
            ),
            "claim_label_remains_preflight": True,
        },
    }
    proof_path = out_dir / "hex_cell_activation_preflight_proof.json"
    report["proof_path"] = str(proof_path)
    proof_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def _candidates() -> list[SolverCandidate]:
    return [
        _candidate("cand-alpha", "frost_alpha"),
        _candidate("cand-beta", "frost_beta"),
        _candidate("cand-gamma", "frost_gamma"),
    ]


def _candidate(candidate_id: str, solver_name: str) -> SolverCandidate:
    return SolverCandidate(
        schema_version=1,
        candidate_id=candidate_id,
        state="shadow_only",
        solver_name=solver_name,
        cell_id=_CELL_ID,
        spec_or_code={
            "kind": "threshold_rule",
            "capability_id": _CAPABILITY_ID,
        },
        source_gap_ref="gap:t2-hexcell-activation-preflight",
        no_runtime_mutation=True,
        produced_by="tools/run_hex_cell_activation_preflight_proof.py",
        branch_name="local/t2-hexcell-activation-preflight",
        base_commit_hash="not-mutating",
        pinned_input_manifest_sha256="sha256:local-preflight",
        match_confidence=0.7,
    )


def _acceptance_receipt_digest(acceptance: Any) -> str:
    return sha256_digest({
        "event_type": HEX_CELL_PROMOTION_ACCEPTANCE_RECEIPT_EVENT_TYPE,
        "acceptance_id": acceptance.acceptance_id,
        "acceptance_digest": acceptance.acceptance_digest,
    })


def _solver_provenance_activation_bundle(candidate_id: str) -> dict[str, Any]:
    canonical, digest = canonicalize_manifest({
        "candidate_id": candidate_id,
        "template_family": "HexCellActivationPreflightDemoSolver",
        "version": 1,
    })
    candidate = SolverCandidateRecord(
        candidate_id=candidate_id,
        manifest_canonical_json=canonical,
        manifest_sha256=digest,
        target_domain="DOM-011",
        target_write_risk="local_artifact",
        activation_state="signed",
    )
    verification = VerificationResult(
        valid=True,
        candidate_id=candidate_id,
        activation_state="signed",
        has_owner_signature=True,
        has_peer_signature=True,
        manifest_sha256_observed=digest,
    )
    return build_solver_provenance_transition_receipt(
        candidate=candidate,
        transition="activation_authorised",
        audit_event_ref="evt_hexcell_activation_preflight",
        bridge_event_ref="bridge_hexcell_activation_preflight",
        verification=verification,
        new_state="activated",
    )


def _forge_probe_results(
    *,
    authorization: Any,
    solver_bundle: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    return {
        "candidate_mismatch": _expect_rejected(
            lambda: build_hex_cell_activation_preflight(
                authorization=authorization,
                solver_provenance_bundle=_solver_provenance_activation_bundle(
                    "other-candidate"
                ),
            )
        ),
        "transition_drift": _expect_rejected(
            lambda: build_hex_cell_activation_preflight(
                authorization=authorization,
                solver_provenance_bundle=_with_payload_field(
                    solver_bundle,
                    "transition",
                    "activation_revoked",
                ),
            )
        ),
        "evaluation_digest_drift": _expect_rejected(
            lambda: build_hex_cell_activation_preflight(
                authorization=authorization,
                solver_provenance_bundle=_with_evaluation_field(
                    solver_bundle,
                    "target_digest",
                    "sha256:" + "0" * 64,
                ),
            )
        ),
        "pregranted_runtime_authority": _expect_rejected(
            lambda: build_hex_cell_activation_preflight(
                authorization=_replace_dataclass_field(
                    authorization,
                    "runtime_authority_granted",
                    True,
                ),
                solver_provenance_bundle=solver_bundle,
            )
        ),
    }


def _expect_rejected(fn: Callable[[], Any]) -> dict[str, Any]:
    try:
        fn()
    except ValueError as exc:
        return {
            "rejected": True,
            "error_type": type(exc).__name__,
            "error_digest": sha256_digest({"error": str(exc)}),
        }
    return {
        "rejected": False,
        "error_type": None,
        "error_digest": None,
    }


def _with_payload_field(
    bundle: dict[str, Any],
    field: str,
    value: Any,
) -> dict[str, Any]:
    mutated = dict(bundle)
    mutated["payload"] = dict(bundle["payload"])
    mutated["payload"][field] = value
    return mutated


def _with_evaluation_field(
    bundle: dict[str, Any],
    field: str,
    value: Any,
) -> dict[str, Any]:
    mutated = dict(bundle)
    mutated["evaluation_result"] = dict(bundle["evaluation_result"])
    mutated["evaluation_result"][field] = value
    return mutated


def _replace_dataclass_field(obj: Any, field: str, value: Any) -> Any:
    from dataclasses import replace

    return replace(obj, **{field: value})


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
