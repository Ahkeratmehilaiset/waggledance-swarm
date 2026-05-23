# SPDX-License-Identifier: BUSL-1.1
"""Operator-runnable WaggleDance V12 substrate proof and competitor-axis summary.

This tool produces a single-shot, human-readable highlight reel of the V12
substrate state plus a reference to the bridge-consensus-sealed competitor-axis
pilot. It is intentionally read-only: it spawns existing read-only tools,
collects their JSON output, and prints a synthesis.

Designed for operator demos and management presentations:
    python tools/show_v12_proof.py

It prints:
    - MAGMA receipt-adoption gap counts (HIGH / MEDIUM / target classifications)
    - Synthetic adversarial corpus pass rate
    - Governance throughput metric availability
    - Bridge-consensus-sealed competitor-axis pilot reference
    - A4 SolverProvenance lifecycle receipt proof, separated from the
      synthetic A4 axis proof
    - A4 autogrowth scheduler lifecycle receipt proof, separated from the
      SolverProvenance lifecycle proof
    - Today's merged-PR count from `git log` (substrate velocity)

Independently verifiable: each row cites the underlying tool that produced
it, so an auditor can re-run the source command and cross-check the value.

It is NOT a network benchmark. Live rival comparisons (Asqav, JamJet, AGT,
Preloop) require their own SDK-local smoke tests per
docs/benchmarks/2026_05_20_competitor_axis_pilot.md "Rival-Side Local Checks
Required" section.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_OK_STATUSES = frozenset({"receipt_bound", "receipt_capable_opt_in"})
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PILOT_MD_PATH = ROOT / "docs" / "benchmarks" / "2026_05_20_competitor_axis_pilot.md"
PILOT_JSON_PATH = ROOT / "docs" / "benchmarks" / "2026_05_20_competitor_axis_pilot.json"

ADOPTION_REPORT = ROOT / "tools" / "magma_receipt_adoption_report.py"
ADVERSARIAL_EVAL = ROOT / "tools" / "run_magma_adversarial_eval.py"
A3_COUNTERFACTUAL_PROOF = ROOT / "tools" / "run_v12_a3_counterfactual_axis_proof.py"
A4_SOLVER_GROWTH_PROOF = ROOT / "tools" / "run_v12_a4_solver_growth_axis_proof.py"
A4_SOLVER_LIFECYCLE_PROOF = (
    ROOT / "tools" / "run_solver_provenance_receipt_emission_proof.py"
)
A4_AUTOGROWTH_LIFECYCLE_PROOF = (
    ROOT / "tools" / "run_autogrowth_promotion_receipt_emission_proof.py"
)
GOVERNANCE_REPORT = ROOT / "tools" / "governance_throughput_report.py"
RIVAL_LOCAL_CHECK_MATRIX = ROOT / "tools" / "run_v12_rival_local_check_matrix.py"
RIVAL_LOCAL_CHECKS_DIR = ROOT / "docs" / "benchmarks" / "rival_local_checks"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print a V12 substrate proof + competitor-axis summary.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of formatted text.",
    )
    parser.add_argument(
        "--since-utc-days",
        type=int,
        default=1,
        help="Window in days for the substrate-velocity merged-PR count.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Repository root for git log queries.",
    )
    parser.add_argument(
        "--governance-events",
        type=Path,
        default=None,
        help=(
            "Optional bridge events JSONL path forwarded to "
            "tools/governance_throughput_report.py."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect_proof(
        repo_root=args.repo_root,
        since_days=args.since_utc_days,
        governance_events=args.governance_events,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_proof(report))
    return 0 if report["ok"] else 1


def collect_proof(
    *,
    repo_root: Path,
    since_days: int,
    governance_events: Path | None = None,
) -> dict[str, Any]:
    adoption = _run_tool_json(["--json"], ADOPTION_REPORT)
    eval_report = _run_tool_json(["--json"], ADVERSARIAL_EVAL)
    # A3 and A4 proof tools both build a verified MAGMA receipt bundle when
    # given --out-dir. Without it they emit evaluation digests but report
    # receipt_chain_verified=False. We run them with a per-invocation
    # tempdir so the verifier output is captured and the receipt-chain claim
    # in the summary actually reflects an end-to-end-verified run. The
    # tempdir is cleaned up immediately after; auditors who want the bundle
    # on disk should run the proof tools directly with their own --out-dir.
    a3_report = _run_proof_tool_with_receipt(A3_COUNTERFACTUAL_PROOF)
    a4_report = _run_proof_tool_with_receipt(A4_SOLVER_GROWTH_PROOF)
    a4_lifecycle_report = _run_proof_tool_with_receipt(A4_SOLVER_LIFECYCLE_PROOF)
    a4_autogrowth_report = _run_proof_tool_with_receipt(
        A4_AUTOGROWTH_LIFECYCLE_PROOF
    )
    governance_args = ["--json"]
    if governance_events is not None:
        governance_args.extend(["--events", str(governance_events)])
    governance = _run_tool_json(governance_args, GOVERNANCE_REPORT, optional=True)
    pilot = _read_pilot_summary()
    velocity = _read_substrate_velocity(repo_root=repo_root, since_days=since_days)

    high_gap = (
        int(adoption.get("high_criticality_gap_count", -1))
        if adoption.get("ok") is not False
        else -1
    )
    medium_gap_targets = (
        [
            entry
            for entry in adoption.get("entries", [])
            if entry.get("criticality") == "medium"
            and entry.get("status") not in RECEIPT_OK_STATUSES
        ]
        if adoption.get("ok") is not False
        else []
    )

    ok = (
        adoption.get("ok") is not False
        and eval_report.get("ok") is True
        and a3_report.get("ok") is True
        and a4_report.get("ok") is True
        and a4_lifecycle_report.get("ok") is True
        and a4_autogrowth_report.get("ok") is True
        and high_gap == 0
    )

    return {
        "report_version": "waggledance.v12_substrate_proof.v0",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "ok": ok,
        "adoption": {
            "high_criticality_gap_count": high_gap,
            "target_count": adoption.get("target_count"),
            "status_counts": adoption.get("status_counts"),
            "medium_gap_targets": [
                {
                    "label": entry.get("label"),
                    "path": entry.get("path"),
                    "status": entry.get("status"),
                    "accepted_exception": (
                        (entry.get("accepted_exception") or {}).get("status")
                    ),
                }
                for entry in medium_gap_targets
            ],
            "action_required_gap_count": adoption.get("action_required_gap_count"),
            "accepted_exception_count": adoption.get("accepted_exception_count"),
            "available": adoption.get("ok") is not False,
        },
        "adversarial_eval": {
            "case_count": eval_report.get("case_count"),
            "pass_count": eval_report.get("pass_count"),
            "fail_count": eval_report.get("fail_count"),
            "gate_accuracy": eval_report.get("gate_accuracy"),
            "verdict_accuracy": eval_report.get("verdict_accuracy"),
            "reason_code_accuracy": eval_report.get("reason_code_accuracy"),
            "coverage": eval_report.get("coverage", {}),
            "ok": eval_report.get("ok"),
            "available": eval_report.get("ok") is not None,
        },
        "a3_counterfactual_axis": _summarize_a3_counterfactual_axis(a3_report),
        "a4_solver_growth_axis": _summarize_a4_solver_growth_axis(a4_report),
        "a4_solver_lifecycle": _summarize_a4_solver_lifecycle(
            a4_lifecycle_report
        ),
        "a4_autogrowth_lifecycle": _summarize_a4_autogrowth_lifecycle(
            a4_autogrowth_report
        ),
        "governance_throughput": _summarize_governance_throughput(governance),
        "competitor_pilot": pilot,
        "substrate_velocity": velocity,
    }


def format_proof(report: dict[str, Any]) -> str:
    lines: list[str] = []
    bar = "=" * 72
    lines.append(bar)
    lines.append(
        f"WaggleDance V12 substrate proof  -  generated {report['generated_at_utc']}"
    )
    lines.append(bar)

    adoption = report["adoption"]
    if adoption["available"]:
        hcg = adoption["high_criticality_gap_count"]
        sc = adoption.get("status_counts") or {}
        lines.append("")
        lines.append("AUTHORITY-RECEIPT ADOPTION  (tools/magma_receipt_adoption_report.py)")
        marker = "OK " if hcg == 0 else "** "
        lines.append(f"  {marker}HIGH-criticality gaps           : {hcg}")
        lines.append(f"     target count                    : {adoption.get('target_count')}")
        lines.append(
            "     status counts                   : "
            + ", ".join(f"{k}={v}" for k, v in sorted(sc.items()))
        )
        if adoption.get("medium_gap_targets"):
            lines.append("     medium non-receipt paths        :")
            for entry in adoption["medium_gap_targets"]:
                exception = (
                    f", exception={entry['accepted_exception']}"
                    if entry.get("accepted_exception")
                    else ""
                )
                lines.append(
                    f"       - {entry['label']} ({entry['path']}) "
                    f"[{entry['status']}{exception}]"
                )
    else:
        lines.append("")
        lines.append("AUTHORITY-RECEIPT ADOPTION       : tool unavailable")

    adv = report["adversarial_eval"]
    if adv["available"]:
        lines.append("")
        lines.append("ADVERSARIAL CORPUS  (tools/run_magma_adversarial_eval.py)")
        marker = "OK " if adv.get("ok") else "** "
        lines.append(
            f"  {marker}cases                           : {adv['case_count']}"
        )
        lines.append(
            f"     demo policy match               : {adv['pass_count']}/{adv['case_count']} pass"
        )
        lines.append(
            f"     accuracy (gate / verdict / codes): {adv['gate_accuracy']} / "
            f"{adv['verdict_accuracy']} / {adv['reason_code_accuracy']}"
        )
        coverage = adv.get("coverage") or {}
        if coverage:
            lines.append(
                "     coverage                       : "
                f"receipt_binding={coverage.get('receipt_binding_case_count')}, "
                f"evaluation_result={coverage.get('evaluation_result_case_count')}, "
                f"counterfactual={coverage.get('counterfactual_case_count')}"
            )
    else:
        lines.append("")
        lines.append("ADVERSARIAL CORPUS               : tool unavailable")

    a3 = report["a3_counterfactual_axis"]
    if a3["available"]:
        delta = a3["delta"]
        lines.append("")
        lines.append("A3 COUNTERFACTUAL AXIS  (tools/run_v12_a3_counterfactual_axis_proof.py)")
        marker = "OK " if a3["counterfactual_delta_proven"] else "** "
        lines.append(
            f"  {marker}delta proven                    : {a3['counterfactual_delta_proven']}"
        )
        lines.append(f"     claim label                    : {a3['claim_label']}")
        lines.append(f"     variants                       : {a3['variant_count']}")
        lines.append(
            f"     action kind                    : {delta['kind'][0]} -> {delta['kind'][1]}"
        )
        lines.append(
            f"     actual gate                    : {delta['actual_gate'][0]} -> {delta['actual_gate'][1]}"
        )
        lines.append(
            f"     receipt chain in this command  : {a3['receipt_chain_verified']}"
        )
        lines.append("     receipt-backed pack            : tools/run_v12_supervisor_demo_pack.py")
    else:
        lines.append("")
        lines.append("A3 COUNTERFACTUAL AXIS           : tool unavailable")

    a4 = report["a4_solver_growth_axis"]
    if a4["available"]:
        dispatch = a4["dispatch"]
        registration = a4["registration"]
        lines.append("")
        lines.append("A4 SOLVER-GROWTH AXIS  (tools/run_v12_a4_solver_growth_axis_proof.py)")
        marker = "OK " if a4["solver_growth_proven"] else "** "
        lines.append(
            f"  {marker}solver growth proven           : {a4['solver_growth_proven']}"
        )
        lines.append("     evidence scope                 : synthetic Phase 18C dispatch fixture")
        lines.append(f"     claim label                    : {a4['claim_label']}")
        lines.append(
            f"     registered solvers             : {registration['registered_solver_count']}"
        )
        lines.append(
            f"     dispatch success               : {dispatch['dispatch_success_count']}/{dispatch['dispatch_case_count']}"
        )
        lines.append(
            f"     families covered               : {dispatch['families_covered']}"
        )
        lines.append(
            f"     receipt chain in this command  : {a4['receipt_chain_verified']}"
        )
        lines.append("     receipt-backed pack            : tools/run_v12_supervisor_demo_pack.py")
    else:
        lines.append("")
        lines.append("A4 SOLVER-GROWTH AXIS            : tool unavailable")

    lifecycle = report["a4_solver_lifecycle"]
    if lifecycle["available"]:
        lines.append("")
        lines.append(
            "A4 SOLVER LIFECYCLE RECEIPTS  "
            "(tools/run_solver_provenance_receipt_emission_proof.py)"
        )
        marker = "OK " if lifecycle["ok"] else "** "
        lines.append(
            f"  {marker}opt-in lifecycle receipts      : {lifecycle['ok']}"
        )
        lines.append(f"     evidence scope                 : {lifecycle['evidence_scope']}")
        lines.append(f"     claim label                    : {lifecycle['claim_label']}")
        lines.append(f"     runtime path                   : {lifecycle['runtime_path']}")
        lines.append(
            "     transitions                    : "
            + " -> ".join(lifecycle["transitions"])
        )
        lines.append(f"     receipt count                  : {lifecycle['receipt_count']}")
        lines.append(
            f"     receipt chain verified         : {lifecycle['receipt_chain_verified']}"
        )
    else:
        lines.append("")
        lines.append("A4 SOLVER LIFECYCLE RECEIPTS     : tool unavailable")

    autogrowth = report["a4_autogrowth_lifecycle"]
    if autogrowth["available"]:
        lines.append("")
        lines.append(
            "A4 AUTOGROWTH LIFECYCLE RECEIPTS  "
            "(tools/run_autogrowth_promotion_receipt_emission_proof.py)"
        )
        marker = "OK " if autogrowth["ok"] else "** "
        lines.append(
            f"  {marker}scheduler-path receipts    : {autogrowth['ok']}"
        )
        lines.append(f"     evidence scope                 : {autogrowth['evidence_scope']}")
        lines.append(f"     claim label                    : {autogrowth['claim_label']}")
        lines.append(f"     runtime path                   : {autogrowth['runtime_path']}")
        lines.append(
            "     transitions                    : "
            + " -> ".join(autogrowth["transitions"])
        )
        lines.append(f"     receipt count                  : {autogrowth['receipt_count']}")
        lines.append(
            f"     receipt chain verified         : {autogrowth['receipt_chain_verified']}"
        )
        lines.append(
            f"     sink=None preserved            : {autogrowth['sink_none_preserved']}"
        )
    else:
        lines.append("")
        lines.append("A4 AUTOGROWTH LIFECYCLE RECEIPTS : tool unavailable")

    gov = report["governance_throughput"]
    lines.append("")
    if gov["available"]:
        lines.append("GOVERNANCE THROUGHPUT  (tools/governance_throughput_report.py)")
        lines.append(f"  OK metrics available             : {gov['metric_count']}")
        lines.append(f"     event count in window         : {gov['event_count_in_window']}")
        lines.append(f"     task count in window          : {gov['task_count_in_window']}")
        lines.append(
            "     metric statuses               : "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(gov["status_counts"].items())
            )
        )
    else:
        lines.append("GOVERNANCE THROUGHPUT            : tool unavailable")

    pilot = report["competitor_pilot"]
    lines.append("")
    lines.append("COMPETITOR-AXIS PILOT  (docs/benchmarks/2026_05_20_competitor_axis_pilot.md)")
    lines.append(f"  bridge-consensus-sealed          : {pilot['bridge_consensus_sealed']}")
    lines.append(f"  pilot status                     : {pilot['pilot_status']}")
    lines.append(f"  consensus grade                  : {pilot['consensus_grade']}")
    lines.append(f"  must-win axes                    : {', '.join(pilot['must_win_axes'])}")
    lines.append(f"  ceded axes                       : {', '.join(pilot['ceded_axes'])}")
    lines.append(f"  rivals in scope                  : {', '.join(pilot['rivals'])}")
    lines.append(
        f"  rival-local checks status        : {pilot['rival_local_checks_status']}"
    )
    if pilot.get("rival_local_check_matrix_available"):
        lines.append(
            "  rival-local checks passed        : "
            f"{pilot['rival_local_check_pass_count']}/"
            f"{pilot['rival_local_check_required_count']}"
        )
    lines.append(
        "  rival evidence templates         : "
        f"{pilot['rival_evidence_template_count']} safe non-passing templates via supervisor demo pack"
    )
    lines.append(
        f"  demo pack command                : {pilot['supervisor_demo_pack_command']}"
    )

    velocity = report["substrate_velocity"]
    lines.append("")
    lines.append("SUBSTRATE VELOCITY  (git log on main)")
    lines.append(
        f"  window                           : last {velocity['since_days']} UTC day(s)"
    )
    lines.append(
        f"  merged commits                   : {velocity['merged_commits']}"
    )
    lines.append(
        f"  merged feat commits              : {velocity['feat_commits']}"
    )
    lines.append(
        f"  merged PRs (#NNN)                : {', '.join(velocity['pr_numbers']) or 'none'}"
    )

    lines.append("")
    lines.append(bar)
    lines.append("VERIFICATION (every line above is independently re-runnable)")
    lines.append("-" * 72)
    lines.append("  python tools/magma_receipt_adoption_report.py --json")
    lines.append("  python tools/run_magma_adversarial_eval.py --json")
    lines.append("  python tools/run_v12_a3_counterfactual_axis_proof.py --json")
    lines.append("  python tools/run_v12_a4_solver_growth_axis_proof.py --json")
    lines.append(
        "  python tools/run_solver_provenance_receipt_emission_proof.py "
        "--out-dir <new-output-dir> --json"
    )
    lines.append(
        "  python tools/run_autogrowth_promotion_receipt_emission_proof.py "
        "--out-dir <new-output-dir> --json"
    )
    lines.append("  python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>")
    lines.append(
        "  python tools/run_v12_rival_local_check_matrix.py "
        "--evidence-dir docs/benchmarks/rival_local_checks --json"
    )
    lines.append("  python tools/governance_throughput_report.py --json")
    lines.append("  cat docs/benchmarks/2026_05_20_competitor_axis_pilot.md")
    lines.append(
        "  git log --first-parent --since='1 day ago' origin/main"
    )
    lines.append(bar)
    return "\n".join(lines)


def _summarize_a3_counterfactual_axis(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("ok") is None:
        return {
            "available": False,
            "counterfactual_delta_proven": False,
            "claim_label": "unknown",
            "delta": {
                "kind": ["unknown", "unknown"],
                "actual_gate": ["unknown", "unknown"],
            "verdict": ["unknown", "unknown"],
            },
            "receipt_chain_verified": False,
            "variant_count": 0,
            "variants_with_kind_delta": 0,
            "variants_with_gate_delta": 0,
        }
    return {
        "available": report.get("ok") is not None,
        "counterfactual_delta_proven": bool(report.get("counterfactual_delta_proven")),
        "claim_label": report.get("claim_label", "unknown"),
        "variant_count": int(report.get("variant_count") or 0),
        "variants_with_kind_delta": int(report.get("variants_with_kind_delta") or 0),
        "variants_with_gate_delta": int(report.get("variants_with_gate_delta") or 0),
        "delta": report.get("delta") or {
            "kind": ["unknown", "unknown"],
            "actual_gate": ["unknown", "unknown"],
            "verdict": ["unknown", "unknown"],
        },
        "receipt_chain_verified": bool(report.get("receipt_chain_verified")),
    }


def _summarize_a4_solver_growth_axis(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("ok") is None:
        return {
            "available": False,
            "solver_growth_proven": False,
            "claim_label": "unknown",
            "registration": {
                "registered_solver_count": 0,
                "rejected_registration_count": 0,
            },
            "dispatch": {
                "dispatch_case_count": 0,
                "dispatch_success_count": 0,
                "dispatch_failure_count": 0,
                "families_covered": 0,
            },
            "receipt_chain_verified": False,
        }
    return {
        "available": report.get("ok") is not None,
        "solver_growth_proven": bool(report.get("solver_growth_proven")),
        "claim_label": report.get("claim_label", "unknown"),
        "registration": report.get("registration") or {
            "registered_solver_count": 0,
            "rejected_registration_count": 0,
        },
        "dispatch": report.get("dispatch") or {
            "dispatch_case_count": 0,
            "dispatch_success_count": 0,
            "dispatch_failure_count": 0,
            "families_covered": 0,
        },
        "receipt_chain_verified": bool(report.get("receipt_chain_verified")),
    }


def _summarize_a4_solver_lifecycle(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("ok") is None:
        return {
            "available": False,
            "ok": False,
            "claim_label": "unknown",
            "runtime_path": "unknown",
            "transitions": [],
            "receipt_count": 0,
            "receipt_chain_verified": False,
            "risk_class": "unknown",
            "operator_gate_required": "unknown",
            "external_writes_applied": "unknown",
            "receipt_emission_mode": "unknown",
            "evidence_scope": "unavailable",
        }
    transitions = report.get("transitions") or []
    return {
        "available": True,
        "ok": report.get("ok") is True,
        "claim_label": report.get("claim_label", "unknown"),
        "runtime_path": report.get("runtime_path", "unknown"),
        "transitions": list(transitions) if isinstance(transitions, list) else [],
        "receipt_count": int(report.get("receipt_count") or 0),
        "receipt_chain_verified": report.get("verifier_ok") is True,
        "risk_class": report.get("risk_class", "unknown"),
        "operator_gate_required": report.get("operator_gate_required"),
        "external_writes_applied": report.get("external_writes_applied"),
        "receipt_emission_mode": report.get("receipt_emission_mode", "unknown"),
        "evidence_scope": (
            "real opt-in SolverProvenance sign/activate/revoke path; "
            "not production auto-promotion authority"
        ),
    }


def _summarize_a4_autogrowth_lifecycle(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("ok") is None:
        return {
            "available": False,
            "ok": False,
            "claim_label": "unknown",
            "runtime_path": "unknown",
            "transitions": [],
            "receipt_count": 0,
            "receipt_chain_verified": False,
            "risk_class": "unknown",
            "operator_gate_required": "unknown",
            "external_writes_applied": "unknown",
            "receipt_emission_mode": "unknown",
            "default_sink_required": "unknown",
            "sink_none_preserved": False,
            "evidence_scope": "unavailable",
        }
    transitions = report.get("transitions") or []
    return {
        "available": True,
        "ok": report.get("ok") is True,
        "claim_label": report.get("claim_label", "unknown"),
        "runtime_path": report.get("runtime_path", "unknown"),
        "transitions": list(transitions) if isinstance(transitions, list) else [],
        "receipt_count": int(report.get("receipt_count") or 0),
        "receipt_chain_verified": report.get("verifier_ok") is True,
        "risk_class": report.get("risk_class", "unknown"),
        "operator_gate_required": report.get("operator_gate_required"),
        "external_writes_applied": report.get("external_writes_applied"),
        "receipt_emission_mode": report.get("receipt_emission_mode", "unknown"),
        "default_sink_required": report.get("default_sink_required"),
        "sink_none_preserved": report.get("sink_none_preserved") is True,
        "evidence_scope": (
            "real opt-in AutogrowthScheduler queue->grower->engine path; "
            "proof fixture only; not long-running production auto-promotion authority"
        ),
    }


def _summarize_governance_throughput(
    report: dict[str, Any] | None,
) -> dict[str, Any]:
    if not report:
        return {
            "available": False,
            "metric_count": 0,
            "event_count_in_window": 0,
            "task_count_in_window": 0,
            "window_label": "unknown",
            "status_counts": {},
        }
    metrics = report.get("metrics") or []
    status_counts: dict[str, int] = {}
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        status = str(metric.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return {
        "available": True,
        "metric_count": len(metrics),
        "event_count_in_window": int(report.get("event_count_in_window") or 0),
        "task_count_in_window": int(report.get("task_count_in_window") or 0),
        "window_label": report.get("window_label") or "unknown",
        "status_counts": status_counts,
    }


def _run_proof_tool_with_receipt(tool: Path) -> dict[str, Any]:
    """Run an axis-proof tool with --out-dir <tempdir> so the receipt bundle
    is built and verifier-checked, then return the parsed JSON report. The
    tempdir is removed after the JSON is parsed; the receipt-chain-verified
    flag in the returned dict reflects the verifier's pass/fail.
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="v12-proof-receipt-"))
    out_dir = tmp_root / "proof-out"
    try:
        result = _run_tool_json(["--json", "--out-dir", str(out_dir)], tool)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    return result


def _run_tool_json(args: list[str], tool: Path, optional: bool = False) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, str(tool), *args],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        if optional:
            return {}
        return {"ok": False, "error": str(exc)}
    if completed.returncode != 0:
        if optional:
            return {}
        try:
            data = json.loads(completed.stdout) if completed.stdout else {}
        except json.JSONDecodeError:
            data = {}
        data.setdefault("ok", False)
        data.setdefault("error", completed.stderr.strip())
        return data
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if optional:
            return {}
        return {"ok": False, "error": f"non-JSON output: {exc}"}


def _read_pilot_summary() -> dict[str, Any]:
    if not PILOT_JSON_PATH.exists():
        return {
            "available": False,
            "bridge_consensus_sealed": "unknown",
            "consensus_grade": "unknown",
            "pilot_status": "unknown",
            "must_win_axes": [],
            "ceded_axes": [],
            "rivals": [],
            "rival_local_checks_status": "unknown",
            "rival_evidence_template_count": 0,
            "rival_evidence_template_status": "unknown",
            "supervisor_demo_pack_command": (
                "python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>"
            ),
        }
    try:
        data = json.loads(PILOT_JSON_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "available": False,
            "error": str(exc),
            "bridge_consensus_sealed": "unknown",
            "consensus_grade": "unknown",
            "pilot_status": "unknown",
            "must_win_axes": [],
            "ceded_axes": [],
            "rivals": [],
            "rival_local_checks_status": "unknown",
            "rival_evidence_template_count": 0,
            "rival_evidence_template_status": "unknown",
            "supervisor_demo_pack_command": (
                "python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>"
            ),
        }
    bridge = data.get("bridge_consensus") or {}
    axes = data.get("axes") or []

    def _axis_label(a: dict[str, Any]) -> str:
        return f"{a.get('id', '?')} {a.get('name', '')}".strip()

    must_win = [_axis_label(a) for a in axes if a.get("declared_position") == "must_win"]
    ceded = [
        _axis_label(a)
        for a in axes
        if str(a.get("declared_position", "")).startswith("ceded")
    ]
    rivals: list[str] = []
    seen_rivals: set[str] = set()
    for source in data.get("sources") or []:
        name = source.get("rival")
        if name and name not in seen_rivals:
            rivals.append(name)
            seen_rivals.add(name)
    rival_local_required = data.get("rival_side_local_checks_required") or []
    if rival_local_required:
        statuses = [c.get("status", "?") for c in rival_local_required]
        ran = sum(1 for s in statuses if s not in {"not_run", "required", "pending"})
        if ran == 0:
            rival_status = (
                f"all public_doc_claim, 0/{len(statuses)} rival local checks run yet"
            )
        elif ran < len(statuses):
            rival_status = f"partial: {ran}/{len(statuses)} rival local checks run"
        else:
            rival_status = f"all {ran}/{len(statuses)} rival local checks run"
    else:
        rival_status = "no rival-local-checks declared"
    rival_template_count = len(rival_local_required)
    rival_local_summary = _read_rival_local_check_summary(
        fallback_status=rival_status,
        fallback_required_count=rival_template_count,
    )
    sealed = bool(
        bridge.get("round_1_agent")
        and bridge.get("round_2_agent")
        and bridge.get("round_5_agent")
    )
    return {
        "available": True,
        "bridge_consensus_sealed": sealed,
        "consensus_grade": bool(data.get("consensus_grade", False)),
        "pilot_status": data.get("status", "unknown"),
        "must_win_axes": must_win,
        "ceded_axes": ceded,
        "rivals": rivals,
        "rival_local_checks_status": rival_local_summary["rival_local_checks_status"],
        "rival_local_check_matrix_available": rival_local_summary[
            "rival_local_check_matrix_available"
        ],
        "rival_local_check_pass_count": rival_local_summary[
            "rival_local_check_pass_count"
        ],
        "rival_local_check_required_count": rival_local_summary[
            "rival_local_check_required_count"
        ],
        "rival_local_check_blocked_count": rival_local_summary[
            "rival_local_check_blocked_count"
        ],
        "rival_local_check_consensus_grade": rival_local_summary[
            "rival_local_check_consensus_grade"
        ],
        "rival_evidence_template_count": rival_template_count,
        "rival_evidence_template_status": (
            "safe non-passing templates are generated by the supervisor demo pack; "
            "they do not count as rival-local passes until artifact digests validate"
        ),
        "supervisor_demo_pack_command": (
            "python tools/run_v12_supervisor_demo_pack.py --out-dir <new-output-dir>"
        ),
    }


def _read_rival_local_check_summary(
    *,
    fallback_status: str,
    fallback_required_count: int,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "rival_local_checks_status": fallback_status,
        "rival_local_check_matrix_available": False,
        "rival_local_check_pass_count": 0,
        "rival_local_check_required_count": fallback_required_count,
        "rival_local_check_blocked_count": fallback_required_count,
        "rival_local_check_consensus_grade": False,
    }
    if not RIVAL_LOCAL_CHECKS_DIR.exists():
        return summary

    matrix = _run_tool_json(
        ["--json", "--evidence-dir", str(RIVAL_LOCAL_CHECKS_DIR)],
        RIVAL_LOCAL_CHECK_MATRIX,
        optional=True,
    )
    if matrix.get("ok") is not True:
        return summary

    required_count = int(matrix.get("required_count") or fallback_required_count)
    passed_count = int(matrix.get("passed_count") or 0)
    return {
        "rival_local_checks_status": (
            matrix.get("rival_local_checks_status") or fallback_status
        ),
        "rival_local_check_matrix_available": True,
        "rival_local_check_pass_count": passed_count,
        "rival_local_check_required_count": required_count,
        "rival_local_check_blocked_count": int(
            matrix.get("blocked_count") or max(required_count - passed_count, 0)
        ),
        "rival_local_check_consensus_grade": bool(
            matrix.get("consensus_grade", False)
        ),
    }


def _read_substrate_velocity(*, repo_root: Path, since_days: int) -> dict[str, Any]:
    since_dt = datetime.now(timezone.utc) - timedelta(days=since_days)
    since_iso = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    ref = _resolve_main_ref(repo_root)
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--first-parent",
                "--pretty=format:%H|%s",
                f"--since={since_iso}",
                ref,
            ],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        return {
            "available": False,
            "error": str(exc),
            "since_days": since_days,
            "merged_commits": 0,
            "feat_commits": 0,
            "pr_numbers": [],
        }
    if result.returncode != 0:
        return {
            "available": False,
            "error": result.stderr.strip(),
            "since_days": since_days,
            "merged_commits": 0,
            "feat_commits": 0,
            "pr_numbers": [],
        }
    rows = [line for line in result.stdout.splitlines() if line.strip()]
    feat_count = sum(
        1
        for row in rows
        if row.split("|", 1)[-1].startswith("feat")
    )
    pr_numbers: list[str] = []
    for row in rows:
        match = re.search(r"\(#(\d+)\)", row)
        if match:
            pr_numbers.append(f"#{match.group(1)}")
    return {
        "available": True,
        "since_days": since_days,
        "merged_commits": len(rows),
        "feat_commits": feat_count,
        "pr_numbers": pr_numbers,
        "ref": ref,
    }


def _resolve_main_ref(repo_root: Path) -> str:
    """Prefer origin/main when available, fall back to main."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "origin/main"],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        return "main"
    return "origin/main" if result.returncode == 0 else "main"


if __name__ == "__main__":
    raise SystemExit(main())
