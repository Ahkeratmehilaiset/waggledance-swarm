#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Phase 16B P3 — bounded proof soak / repeatability driver.

Runs each canonical proof N times into a unique temp output directory
per iteration. Detects flakes. Records per-iteration pass/fail and
aggregate runtime.

Honors the master prompt's SOAK ARTIFACT ISOLATION RULE:
* every iteration writes to a unique temp output directory and unique
  scratch DB,
* no iteration overwrites canonical docs/runs artifacts.

Default iteration count: 5 per proof (3 proofs × 5 = 15 runs).
This stays well within the prompt's bounded-timebox while being a
real soak (the master prompt allows 3 iterations as the minimum
acceptable; 5 is a comfortable margin).
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent


PROOFS = [
    {
        "id": "phase15_runtime_hint",
        "tool": "tools/run_automatic_runtime_hint_proof.py",
        "json_artifact": "automatic_runtime_hint_proof.json",
        "json_invariants": [
            ("manual_low_risk_hint_in_input_detected", False),
            ("proof_constructed_runtime_query_objects", False),
            ("corpus_total", lambda v: v >= 100),
            ("hints_derived_total", lambda v: v >= 100),
        ],
        "json_kpi_invariants": [
            ("provider_jobs_delta_during_proof", 0),
            ("builder_jobs_delta_during_proof", 0),
        ],
        "json_after_invariants": [
            ("served_total", lambda v: v >= 100),
            ("served_via_capability_lookup_total", lambda v: v >= 100),
        ],
    },
    {
        "id": "phase16a_upstream",
        "tool": "tools/run_upstream_structured_request_proof.py",
        "json_artifact": "upstream_structured_request_proof.json",
        "json_invariants": [
            ("manual_structured_request_in_input_detected", False),
            ("manual_low_risk_hint_in_input_detected", False),
            ("proof_constructed_runtime_query_objects", False),
            ("proof_bypassed_selected_caller", False),
            ("proof_bypassed_handle_query", False),
            ("corpus_total", lambda v: v >= 100),
            ("structured_request_derived_total", lambda v: v >= 100),
            ("low_risk_hint_derived_total", lambda v: v >= 100),
        ],
        "json_kpi_invariants": [
            ("provider_jobs_delta_during_proof", 0),
            ("builder_jobs_delta_during_proof", 0),
        ],
        "json_after_invariants": [
            ("served_total", lambda v: v >= 100),
            ("served_via_capability_lookup_total", lambda v: v >= 100),
        ],
        "json_negative_invariants": [
            ("negative_cases_total", lambda v: v == 7),
            ("negative_cases_passed_total", lambda v: v == 7),
        ],
    },
    {
        "id": "phase16b_full_restart",
        "tool": "tools/run_full_restart_continuity_proof.py",
        "json_artifact": "full_restart_continuity_proof.json",
        "json_invariants": [
            ("manual_structured_request_in_input_detected", False),
            ("manual_low_risk_hint_in_input_detected", False),
            ("corpus_total", lambda v: v >= 100),
        ],
        "json_kpi_invariants": [
            ("provider_jobs_delta_during_proof", 0),
            ("builder_jobs_delta_during_proof", 0),
        ],
        "json_restart_invariants": [
            ("served_unchanged_across_restart", True),
            (
                "served_via_capability_lookup_unchanged_across_restart",
                True,
            ),
            ("solver_count_unchanged_across_reopen", True),
            ("capability_features_unchanged_across_reopen", True),
            ("provider_jobs_delta_across_restart", 0),
            ("builder_jobs_delta_across_restart", 0),
            ("cache_rebuild_success", True),
        ],
    },
]


def _check_invariants(
    obj: dict, invariants: list,
) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for key, expected in invariants:
        actual = obj.get(key)
        if callable(expected):
            ok = expected(actual)
        else:
            ok = actual == expected
        if not ok:
            failures.append({
                "key": key,
                "expected": (
                    "<callable>" if callable(expected) else expected
                ),
                "actual": actual,
            })
    return failures


def _run_one_iteration(proof_def: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / (proof_def["id"] + ".db")
    cmd = [
        sys.executable,
        proof_def["tool"],
        "--out-dir", str(out_dir),
        "--db", str(db_path),
    ]
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        return {
            "passed": False,
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "blocker": "timeout_300s",
        }
    elapsed = round(time.perf_counter() - t0, 2)

    if result.returncode != 0:
        return {
            "passed": False,
            "elapsed_s": elapsed,
            "blocker": f"non_zero_exit_{result.returncode}",
            "stderr_tail": (result.stderr or "")[-400:],
        }

    json_path = out_dir / proof_def["json_artifact"]
    if not json_path.exists():
        return {
            "passed": False,
            "elapsed_s": elapsed,
            "blocker": "json_artifact_missing",
        }
    try:
        proof = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "passed": False,
            "elapsed_s": elapsed,
            "blocker": f"json_parse_error:{exc}",
        }

    failures: List[Dict[str, Any]] = []
    failures += _check_invariants(
        proof, proof_def.get("json_invariants", []),
    )
    if "json_kpi_invariants" in proof_def:
        failures += _check_invariants(
            proof.get("kpis", {}),
            proof_def["json_kpi_invariants"],
        )
    if "json_after_invariants" in proof_def:
        failures += _check_invariants(
            proof.get("after", {}),
            proof_def["json_after_invariants"],
        )
    if "json_restart_invariants" in proof_def:
        failures += _check_invariants(
            proof.get("restart_invariants", {}),
            proof_def["json_restart_invariants"],
        )
    if "json_negative_invariants" in proof_def:
        failures += _check_invariants(
            proof, proof_def["json_negative_invariants"],
        )

    return {
        "passed": len(failures) == 0,
        "elapsed_s": elapsed,
        "invariant_failures": failures,
    }


def run(iterations: int, soak_root: Path) -> dict:
    soak_root.mkdir(parents=True, exist_ok=True)
    summary: Dict[str, Any] = {
        "phase": "16B P3 — proof soak",
        "iterations_per_proof": iterations,
        "proofs": [],
    }
    for proof_def in PROOFS:
        per_proof: Dict[str, Any] = {
            "id": proof_def["id"],
            "tool": proof_def["tool"],
            "iterations_run": 0,
            "iterations_passed": 0,
            "iterations_failed": 0,
            "elapsed_seconds": [],
            "first_failure": None,
        }
        for i in range(1, iterations + 1):
            iter_dir = soak_root / proof_def["id"] / f"iter_{i:02d}"
            outcome = _run_one_iteration(proof_def, iter_dir)
            per_proof["iterations_run"] += 1
            per_proof["elapsed_seconds"].append(outcome["elapsed_s"])
            if outcome["passed"]:
                per_proof["iterations_passed"] += 1
            else:
                per_proof["iterations_failed"] += 1
                if per_proof["first_failure"] is None:
                    per_proof["first_failure"] = {
                        "iteration": i,
                        "outcome": outcome,
                    }
        if per_proof["elapsed_seconds"]:
            per_proof["elapsed_min_s"] = min(per_proof["elapsed_seconds"])
            per_proof["elapsed_max_s"] = max(per_proof["elapsed_seconds"])
            per_proof["elapsed_mean_s"] = round(
                statistics.mean(per_proof["elapsed_seconds"]), 2,
            )
        per_proof["flake_detected"] = (
            per_proof["iterations_failed"] > 0
        )
        summary["proofs"].append(per_proof)

    summary["overall_flake_detected"] = any(
        p["flake_detected"] for p in summary["proofs"]
    )
    summary["overall_pass"] = all(
        p["iterations_passed"] == iterations for p in summary["proofs"]
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument(
        "--soak-root", type=Path,
        default=None,
    )
    parser.add_argument(
        "--report", type=Path,
        default=ROOT / "docs" / "runs"
        / "phase16b_stabilization_release_gate_2026_05_01"
        / "proof_soak_report.json",
    )
    args = parser.parse_args()
    if args.soak_root is None:
        args.soak_root = Path(tempfile.mkdtemp(prefix="phase16b_soak_"))
    summary = run(args.iterations, args.soak_root)
    summary["soak_root"] = str(args.soak_root)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    # Print compact summary
    print(f"=== Phase 16B P3 proof soak — {args.iterations} iterations ===")
    for p in summary["proofs"]:
        print(
            f"{p['id']:30s} pass={p['iterations_passed']}/"
            f"{p['iterations_run']} "
            f"elapsed_mean={p.get('elapsed_mean_s', 'n/a')}s "
            f"flake={p['flake_detected']}"
        )
    print(f"overall_pass={summary['overall_pass']}")
    print(f"overall_flake_detected={summary['overall_flake_detected']}")
    print(f"soak_root={args.soak_root}")
    print(f"report={args.report}")

    # Cleanup soak temp dir to keep workspace tidy unless KEEP env is set
    if "PHASE16B_SOAK_KEEP" not in __import__("os").environ:
        try:
            shutil.rmtree(args.soak_root, ignore_errors=True)
        except Exception:
            pass
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
