# SPDX-License-Identifier: BUSL-1.1
"""Build a clean V12 A4 solver-growth proof row."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.run_phase18c_mined_solver_runtime_dispatch_proof import (  # noqa: E402
    build_proof as build_phase18c_proof,
    render_md as render_phase18c_markdown,
)
from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.evaluation_result import build_evaluation_result  # noqa: E402
from waggledance.core.magma.receipt import build_magma_receipt  # noqa: E402
from waggledance.core.magma.receipt_bundle import (  # noqa: E402
    ReceiptBundleEntry,
    write_receipt_bundle,
)


REPORT_VERSION = "wd.v12.a4_solver_growth_axis_proof.v0"
POLICY_VERSION = "policy:v12_a4_solver_growth_axis:v0"
CHARTER_VERSION = "charter:authority_boundary:v0"
DOMAIN_THRESHOLD_VERSION = "threshold:phase18c_six_family:v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a clean A4 solver-growth proof row from the local Phase 18C "
            "mined-solver runtime-dispatch proof."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Optional new output directory for the wrapper report, raw Phase "
            "18C proof, and A4 MAGMA receipt bundle."
        ),
    )
    parser.add_argument(
        "--markdown-out",
        type=Path,
        default=None,
        help="Optional markdown report path to write.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic receipt output.",
    )
    parser.add_argument(
        "--recorded-base-main-sha",
        default=None,
        help=(
            "Optional 40-character git SHA to record as base_main_sha for a "
            "previously observed pilot row. The report marks the value as a "
            "recorded_override so audit reruns do not drift with current HEAD."
        ),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_a4_solver_growth_axis_proof(
            out_dir=args.out_dir,
            now_utc=_parse_utc(args.now) if args.now else None,
            recorded_base_main_sha=args.recorded_base_main_sha,
        )
    except ValueError as exc:
        print(f"A4 solver-growth axis proof FAILED: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.markdown_out:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(markdown, end="")
    return 0 if report["ok"] else 1


def build_a4_solver_growth_axis_proof(
    *,
    out_dir: Path | None = None,
    now_utc: datetime | None = None,
    recorded_base_main_sha: str | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace("+00:00", "Z")

    phase18c_dir: Path | None = None
    if out_dir is not None:
        out_dir = out_dir.resolve()
        if out_dir.exists():
            raise ValueError(f"out_dir must not exist: {out_dir}")
        out_dir.mkdir(parents=True, exist_ok=False)
        phase18c_dir = out_dir / "phase18c"
        receipt_out_dir = out_dir / "a4_solver_growth_receipts"
        phase18c = build_phase18c_proof(out_dir=phase18c_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="wd_v12_a4_solver_growth_") as tmp:
            phase18c = build_phase18c_proof(out_dir=Path(tmp) / "phase18c")
        receipt_out_dir = None

    base_main_sha_source = "git_head"
    if recorded_base_main_sha is not None:
        phase18c = dict(phase18c)
        phase18c["base_main_sha"] = _validate_git_sha(recorded_base_main_sha)
        base_main_sha_source = "recorded_override"
    phase18c["base_main_sha_source"] = base_main_sha_source

    if phase18c_dir is not None:
        _write_json(
            phase18c_dir / "mined_solver_runtime_dispatch_proof.json",
            phase18c,
        )
        _write_text(
            phase18c_dir / "mined_solver_runtime_dispatch_proof.md",
            render_phase18c_markdown(proof=phase18c),
        )

    payload = _receipt_payload(phase18c)
    evaluation_result = _evaluation_result(
        payload=payload,
        phase18c=phase18c,
    )
    receipt = build_magma_receipt(
        event_id="magma:v12_a4_solver_growth_axis:phase18c",
        ts_utc=generated_at_utc,
        risk_class="local_artifact",
        payload=payload,
        evaluation_result=evaluation_result,
        policy_digest=sha256_digest({"policy_version": POLICY_VERSION}),
        charter_digest=sha256_digest({"charter_version": CHARTER_VERSION}),
        rco_decision_digest=sha256_digest({
            "axis_id": "A4",
            "release_gates": phase18c["release_gates"],
            "claim_label": "MEASURED_LOCAL_SYNTHETIC",
        }),
        world_snapshot_digest=sha256_digest({
            "phase": phase18c["phase"],
            "base_main_sha": phase18c["base_main_sha"],
            "base_main_sha_source": phase18c["base_main_sha_source"],
            "fixture_size": phase18c["fixture_size"],
        }),
        solver_contract_digest=sha256_digest({
            "registered_solver_count": phase18c["registered_solver_count"],
            "families": sorted(phase18c["per_family_dispatch_counts"].keys()),
        }),
    )

    receipt_bundle = _write_receipts(
        receipt_out_dir=receipt_out_dir,
        payload=payload,
        evaluation_result=evaluation_result,
        receipt=receipt,
    )
    receipt_chain_verified = bool(
        receipt_bundle and receipt_bundle["verifier_report"]["ok"]
    )

    solver_growth_proven = _solver_growth_proven(phase18c)
    report = {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": bool(solver_growth_proven),
        "axis_id": "A4",
        "axis_name": "solver_growth_runtime_dispatch",
        "claim_label": "MEASURED_LOCAL_SYNTHETIC",
        "source_phase": phase18c["phase"],
        "source_benchmark_version": phase18c["benchmark_version"],
        "source_prerelease": phase18c["source_prerelease"],
        "candidate_prerelease": phase18c["candidate_prerelease"],
        "base_main_sha": phase18c["base_main_sha"],
        "base_main_sha_source": phase18c["base_main_sha_source"],
        "solver_growth_proven": bool(solver_growth_proven),
        "fixture": {
            "is_synthetic_fixture": phase18c["is_synthetic_fixture"],
            "signals_total": phase18c["signals_total"],
            "candidates_total": phase18c["candidates_total"],
            "allowlisted_candidate_count": phase18c["allowlisted_candidate_count"],
        },
        "registration": {
            "registered_solver_count": phase18c["registered_solver_count"],
            "rejected_registration_count": phase18c["rejected_registration_count"],
            "registered_candidate_ids": (
                phase18c["registration_summary"].get("registered_candidate_ids", [])
            ),
        },
        "dispatch": {
            "dispatch_case_count": phase18c["dispatch_case_count"],
            "dispatch_success_count": phase18c["dispatch_success_count"],
            "dispatch_failure_count": phase18c["dispatch_failure_count"],
            "families_covered": phase18c["families_covered"],
            "per_family_dispatch_counts": phase18c["per_family_dispatch_counts"],
        },
        "release_gate_pass": phase18c["release_gate_pass"],
        "release_gates": dict(phase18c["release_gates"]),
        "receipt_chain_verified": receipt_chain_verified,
        "receipt_bundle": _receipt_summary(receipt_bundle),
        "evaluation_result_digest": sha256_digest(evaluation_result),
        "receipt_digest": sha256_digest(receipt),
        "evidence_sources": [
            "tools/run_phase18c_mined_solver_runtime_dispatch_proof.py",
            "waggledance/core/autonomy_growth/gap_mining.py",
            "waggledance/core/autonomy_growth/mined_solver_runtime.py",
            "waggledance/core/autonomy_growth/solver_dispatcher.py",
            "schemas/v3_13_0/evaluation_result.v0.json",
            "tools/verify_magma_receipt.py",
        ],
        "no_overclaim_guardrails": {
            "not_a_rival_benchmark": True,
            "does_not_claim_frontier_model_superiority": True,
            "does_not_claim_learned_authority": True,
            "does_not_touch_production_control_plane": True,
            "does_not_execute_live_builder": phase18c["no_live_builder_execution"],
            "does_not_pull_model": phase18c["no_model_pull_or_download"],
            "does_not_call_cloud": phase18c["no_cloud_api_calls"],
            "does_not_collect_human_approval": phase18c["no_human_approval"],
            "no_stage2_atomic_flip": phase18c["no_stage2_flip"],
            "measures_synthetic_fixture": phase18c["is_synthetic_fixture"],
        },
    }

    if out_dir is not None:
        _write_json(out_dir / "a4_solver_growth_axis_proof.json", report)
        _write_text(out_dir / "a4_solver_growth_axis_proof.md", render_markdown(report))

    return report


def render_markdown(report: dict[str, Any]) -> str:
    fixture = report["fixture"]
    registration = report["registration"]
    dispatch = report["dispatch"]
    receipt_state = str(report["receipt_chain_verified"]).lower()
    lines = [
        "# V12 A4 Solver-Growth Axis Proof",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- axis: `{report['axis_id']} {report['axis_name']}`",
        f"- claim_label: `{report['claim_label']}`",
        f"- solver_growth_proven: `{str(report['solver_growth_proven']).lower()}`",
        f"- release_gate_pass: `{str(report['release_gate_pass']).lower()}`",
        f"- receipt_chain_verified: `{receipt_state}`",
        "",
        "| Measure | Value |",
        "|---|---:|",
        f"| synthetic signals | {fixture['signals_total']} |",
        f"| mined candidates | {fixture['candidates_total']} |",
        f"| allowlisted candidates | {fixture['allowlisted_candidate_count']} |",
        f"| registered solvers | {registration['registered_solver_count']} |",
        f"| rejected registrations | {registration['rejected_registration_count']} |",
        f"| dispatch successes | {dispatch['dispatch_success_count']}/{dispatch['dispatch_case_count']} |",
        f"| families covered | {dispatch['families_covered']} |",
        "",
        "This is one local synthetic solver-growth row. It proves the existing",
        "Phase 18C path can mine low-risk candidates, register six allowlisted",
        "runtime-dispatchable solvers in a temporary ControlPlaneDB, and hit all",
        f"{dispatch['dispatch_case_count']} capability dispatch cases. It is not "
        "a rival benchmark, does not change production runtime authority, and",
        "does not claim learned policy authority.",
        "",
    ]
    return "\n".join(lines)


def _solver_growth_proven(phase18c: dict[str, Any]) -> bool:
    return bool(
        phase18c["release_gate_pass"]
        and phase18c["registered_solver_count"] == 6
        and phase18c["rejected_registration_count"] >= 1
        and phase18c["dispatch_case_count"] >= 18
        and phase18c["dispatch_success_count"] == phase18c["dispatch_case_count"]
        and phase18c["dispatch_failure_count"] == 0
        and phase18c["families_covered"] == 6
        and phase18c["provider_jobs_delta"] == 0
        and phase18c["builder_jobs_delta"] == 0
        and phase18c["no_live_builder_execution"]
        and phase18c["no_model_pull_or_download"]
        and phase18c["no_cloud_api_calls"]
        and phase18c["no_stage2_flip"]
        and phase18c["no_human_approval"]
        and phase18c["forbidden_claims_absent"]
    )


def _receipt_payload(phase18c: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v12_a4_solver_growth_payload.v0",
        "axis_id": "A4",
        "source_phase": phase18c["phase"],
        "source_benchmark_version": phase18c["benchmark_version"],
        "base_main_sha": phase18c["base_main_sha"],
        "base_main_sha_source": phase18c["base_main_sha_source"],
        "is_synthetic_fixture": phase18c["is_synthetic_fixture"],
        "signals_total": phase18c["signals_total"],
        "candidates_total": phase18c["candidates_total"],
        "allowlisted_candidate_count": phase18c["allowlisted_candidate_count"],
        "registered_solver_count": phase18c["registered_solver_count"],
        "rejected_registration_count": phase18c["rejected_registration_count"],
        "dispatch_case_count": phase18c["dispatch_case_count"],
        "dispatch_success_count": phase18c["dispatch_success_count"],
        "dispatch_failure_count": phase18c["dispatch_failure_count"],
        "families_covered": phase18c["families_covered"],
        "per_family_dispatch_counts": phase18c["per_family_dispatch_counts"],
        "release_gate_pass": phase18c["release_gate_pass"],
        "release_gates": phase18c["release_gates"],
        "guardrails": {
            "provider_jobs_delta": phase18c["provider_jobs_delta"],
            "builder_jobs_delta": phase18c["builder_jobs_delta"],
            "no_live_builder_execution": phase18c["no_live_builder_execution"],
            "no_model_pull_or_download": phase18c["no_model_pull_or_download"],
            "no_cloud_api_calls": phase18c["no_cloud_api_calls"],
            "no_stage2_flip": phase18c["no_stage2_flip"],
            "no_human_approval": phase18c["no_human_approval"],
            "no_high_risk_autonomy": phase18c["no_high_risk_autonomy"],
        },
    }


def _evaluation_result(
    *,
    payload: dict[str, Any],
    phase18c: dict[str, Any],
) -> dict[str, Any]:
    candidate_ids = (
        phase18c["registration_summary"].get("registered_candidate_ids", [])
    )
    solver_selection = [
        f"solver:phase18c:{candidate_id}" for candidate_id in candidate_ids[:6]
    ]
    return build_evaluation_result(
        case_id="case:v12_a4_solver_growth:phase18c",
        subject_type="solver",
        target_payload=payload,
        risk_class="local_artifact",
        expected_gate="allow",
        actual_gate="allow" if phase18c["release_gate_pass"] else "review",
        verifier_path=[
            "phase18b_gap_miner",
            "phase18c_mined_solver_runtime_dispatch",
            "low_risk_solver_dispatcher",
            "magma_receipt_v1",
        ],
        solver_selection=solver_selection,
        policy_version=POLICY_VERSION,
        charter_version=CHARTER_VERSION,
        domain_threshold_version=DOMAIN_THRESHOLD_VERSION,
        verdict="pass" if _solver_growth_proven(phase18c) else "review",
        reason_codes=[
            "a4:phase18c:release_gate_pass",
            "a4:solver_growth:six_allowlisted_registered",
            "a4:dispatch:all_cases_hit",
            "a4:authority:production_unchanged",
        ],
        confidence_score=0.9 if _solver_growth_proven(phase18c) else 0.65,
        uncertainty_sources=[
            {
                "kind": "limited_evidence",
                "detail": (
                    "A4 proof uses a local synthetic six-family fixture; it "
                    "does not prove live production promotion quality."
                ),
            }
        ],
    )


def _write_receipts(
    *,
    receipt_out_dir: Path | None,
    payload: dict[str, Any],
    evaluation_result: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any] | None:
    if receipt_out_dir is None:
        return None
    return write_receipt_bundle(
        out_dir=receipt_out_dir,
        chain_id="wd-v12-a4-solver-growth-axis",
        entries=[
            ReceiptBundleEntry(
                label="solver-growth-axis",
                payload=payload,
                evaluation_result=evaluation_result,
                receipt=receipt,
            )
        ],
        verify_manifest=verify_manifest,
    )


def _receipt_summary(receipt_bundle: dict[str, Any] | None) -> dict[str, Any]:
    if not receipt_bundle:
        return {
            "available": False,
            "receipt_count": 0,
            "verifier_ok": False,
        }
    verifier = receipt_bundle["verifier_report"]
    return {
        "available": True,
        "out_dir": receipt_bundle["out_dir"],
        "manifest": receipt_bundle["manifest"],
        "receipt_count": receipt_bundle["receipt_count"],
        "verifier_ok": bool(verifier["ok"]),
        "verifier_error_count": len(verifier["errors"]),
    }


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("--now requires a UTC timestamp with Z or +00:00 suffix")
    return parsed.astimezone(timezone.utc)


def _validate_git_sha(value: str) -> str:
    candidate = value.strip().lower()
    if len(candidate) != 40 or any(ch not in "0123456789abcdef" for ch in candidate):
        raise ValueError(
            "--recorded-base-main-sha must be a 40-character hexadecimal git SHA"
        )
    return candidate


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
