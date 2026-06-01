# SPDX-License-Identifier: BUSL-1.1
"""Emit a local benchmark artifact for the future-scale contradiction_rate axis.

The axis is defined in docs/architecture/HONEYCOMB_SOLVER_SCALING.md as the
fraction of proposals rejected because they conflict with an existing in-cell
solver. This tool exercises the existing proposal gate with deterministic local
fixtures. It is not a production baseline and never upgrades a claim gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import propose_solver as proposal_gate  # noqa: E402


REPORT_VERSION = "wd.future_scale_contradiction_rate_benchmark.v1"
AXIS_ID = "contradiction_rate"
JSON_ARTIFACT_NAME = "future_scale_contradiction_rate_benchmark.json"
MARKDOWN_ARTIFACT_NAME = "future_scale_contradiction_rate_benchmark.md"
BENCHMARK_SCOPE = "local_deterministic_fixture_only"
MEASUREMENT_LABEL = "MEASURED_LOCAL_ONLY"
FORBIDDEN_CLAIM_LABELS = {"PROVEN", "IMPLEMENTED", "CLAIM_SAFE"}
SAFE_FALSE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "runtime_authority_changed",
    "runtime_authority_granted",
    "controls_present",
    "operator_gate_required",
    "external_writes_applied",
)
LEAK_PATTERNS = (
    re.compile(r"[A-Za-z]:\\(?:Users|Python|Program Files)", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(r"^/(?:home|root|etc|var|opt|Users)/", re.MULTILINE),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic local benchmark artifact for the "
            "future-scale contradiction_rate axis."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Optional output directory for JSON and markdown artifacts.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override, e.g. 2026-06-01T20:55:00Z.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_future_scale_contradiction_rate_benchmark(
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"future scale contradiction_rate benchmark FAILED: {exc}", file=sys.stderr)
        return 1

    markdown = render_markdown(report)
    if args.out_dir is not None:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / JSON_ARTIFACT_NAME).write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        (args.out_dir / MARKDOWN_ARTIFACT_NAME).write_text(markdown, encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print(markdown, end="")
    return 0 if report["ok"] else 1


def build_future_scale_contradiction_rate_benchmark(
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cases = _run_fixture_cases()
    total = len(cases)
    contradiction_rejections = sum(1 for case in cases if case["detected_contradiction"])
    expected_contradictions = sum(1 for case in cases if case["expected_contradiction"])
    false_positive_count = sum(
        1
        for case in cases
        if case["detected_contradiction"] and not case["expected_contradiction"]
    )
    false_negative_count = sum(
        1
        for case in cases
        if case["expected_contradiction"] and not case["detected_contradiction"]
    )
    contradiction_rate = contradiction_rejections / total if total else 0.0

    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "schema_version": "future_scale_contradiction_rate_benchmark.v1",
        "generated_at_utc": _format_utc(generated_at),
        "ok": False,
        "axis_id": AXIS_ID,
        "axis_definition": (
            "fraction of proposals rejected because they conflict with an "
            "existing in-cell solver"
        ),
        "axis_definition_source": "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        "measurement_label": MEASUREMENT_LABEL,
        "benchmark_scope": BENCHMARK_SCOPE,
        "not_a_production_baseline": True,
        "not_a_runtime_scorecard_update": True,
        "benchmark_result": {
            "proposal_count": total,
            "expected_contradictions": expected_contradictions,
            "contradiction_rejections": contradiction_rejections,
            "non_contradiction_cases": total - expected_contradictions,
            "false_positive_count": false_positive_count,
            "false_negative_count": false_negative_count,
            "contradiction_rate": round(contradiction_rate, 6),
            "rate_denominator": "deterministic_fixture_proposals",
            "rate_is_production_baseline": False,
        },
        "cases": cases,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "runtime_authority_changed": False,
        "runtime_authority_granted": False,
        "controls_present": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "provider_jobs_delta": 0,
        "builder_jobs_delta": 0,
        "network_access": "not_used",
        "cloud_api_calls": 0,
        "artifact_write_scope": [
            JSON_ARTIFACT_NAME,
            MARKDOWN_ARTIFACT_NAME,
        ],
        "source_paths": [
            "tools/propose_solver.py",
            "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        ],
        "reproduce_command": (
            "python tools/run_future_scale_contradiction_rate_benchmark.py "
            "--out-dir <out-dir> --now 2026-06-01T20:55:00Z --json"
        ),
        "git": {
            "sha": _git_text("rev-parse", "HEAD"),
            "branch": _git_text("branch", "--show-current"),
        },
        "blockers_to_full_claim": [
            "proposal-gate verdicts are not yet exported as production metrics",
            "fixture denominator is intentionally small and local-only",
            "shared manifest aggregation is deferred to a serialized follow-up",
        ],
        "non_claims": [
            "does not claim contradiction handling is solved",
            "does not claim production future-scale performance",
            "does not grant runtime authority",
            "does not apply external writes",
        ],
    }
    errors = validate_benchmark_report(report)
    report["contract_validation"] = {
        "ok": not errors,
        "error_count": len(errors),
        "errors": errors,
    }
    report["ok"] = not errors
    return report


def validate_benchmark_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("axis_id") != AXIS_ID:
        errors.append("axis_id must be contradiction_rate")
    if report.get("measurement_label") in FORBIDDEN_CLAIM_LABELS:
        errors.append("measurement_label must not upgrade to a proven claim")
    if report.get("measurement_label") != MEASUREMENT_LABEL:
        errors.append("measurement_label must be MEASURED_LOCAL_ONLY")
    if report.get("benchmark_scope") != BENCHMARK_SCOPE:
        errors.append("benchmark_scope must be local_deterministic_fixture_only")
    for field in SAFE_FALSE_FIELDS:
        if report.get(field) is not False:
            errors.append(f"{field} must be exact false bool")
    if report.get("provider_jobs_delta") != 0:
        errors.append("provider_jobs_delta must be 0")
    if report.get("builder_jobs_delta") != 0:
        errors.append("builder_jobs_delta must be 0")
    if report.get("cloud_api_calls") != 0:
        errors.append("cloud_api_calls must be 0")

    result = report.get("benchmark_result")
    if not isinstance(result, dict):
        errors.append("benchmark_result must be an object")
    else:
        total = result.get("proposal_count")
        rejected = result.get("contradiction_rejections")
        rate = result.get("contradiction_rate")
        if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
            errors.append("benchmark_result.proposal_count must be a positive int")
        if not isinstance(rejected, int) or isinstance(rejected, bool) or rejected < 0:
            errors.append("benchmark_result.contradiction_rejections must be a non-negative int")
        if not _is_finite_number(rate):
            errors.append("benchmark_result.contradiction_rate must be finite")
        if (
            isinstance(total, int)
            and not isinstance(total, bool)
            and total > 0
            and isinstance(rejected, int)
            and not isinstance(rejected, bool)
            and _is_finite_number(rate)
        ):
            expected_rate = round(rejected / total, 6)
            if abs(rate - expected_rate) > 0.000001:
                errors.append("benchmark_result.contradiction_rate does not match counts")
        for field in ("false_positive_count", "false_negative_count"):
            value = result.get(field)
            if value != 0:
                errors.append(f"benchmark_result.{field} must be 0 for fixture contract")
        if result.get("rate_is_production_baseline") is not False:
            errors.append("benchmark_result.rate_is_production_baseline must be false")

    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases must be a non-empty list")
    else:
        for index, case in enumerate(cases):
            if not isinstance(case, dict):
                errors.append(f"cases[{index}] must be an object")
                continue
            for field in ("expected_contradiction", "detected_contradiction", "gate_ok"):
                if not isinstance(case.get(field), bool):
                    errors.append(f"cases[{index}].{field} must be exact bool")
            if (
                isinstance(case.get("expected_contradiction"), bool)
                and isinstance(case.get("detected_contradiction"), bool)
                and case["expected_contradiction"] != case["detected_contradiction"]
            ):
                errors.append(f"cases[{index}] detection does not match fixture expectation")
            if case.get("proposal_gate_verdict") not in {
                proposal_gate.V_REJECT_CONTRADICTION,
                proposal_gate.V_ACCEPT_CANDIDATE,
                proposal_gate.V_ACCEPT_SHADOW_ONLY,
                proposal_gate.V_REJECT_LOW_VALUE,
                proposal_gate.V_REJECT_SCHEMA,
                proposal_gate.V_REJECT_DUPLICATE,
            }:
                errors.append(f"cases[{index}].proposal_gate_verdict is not recognized")

    for path, value in _walk_scalars(report):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path} contains a non-finite number")
        if isinstance(value, str) and _looks_like_leak(value):
            errors.append(f"{path} contains a forbidden secret/path-like string")
    return errors


def render_markdown(report: dict[str, Any]) -> str:
    result = report["benchmark_result"]
    lines = [
        "# Future-Scale Contradiction Rate Benchmark",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- axis_id: `{report['axis_id']}`",
        f"- measurement_label: `{report['measurement_label']}`",
        f"- benchmark_scope: `{report['benchmark_scope']}`",
        f"- contradiction_rate: `{result['contradiction_rate']}`",
        f"- contradiction_rejections: `{result['contradiction_rejections']}`",
        f"- proposal_count: `{result['proposal_count']}`",
        f"- claim_gate_satisfied: `{str(report['claim_gate_satisfied']).lower()}`",
        f"- claim_safe: `{str(report['claim_safe']).lower()}`",
        f"- literal_future_claim_safe: `{str(report['literal_future_claim_safe']).lower()}`",
        f"- runtime_authority_changed: `{str(report['runtime_authority_changed']).lower()}`",
        f"- external_writes_applied: `{str(report['external_writes_applied']).lower()}`",
        "",
        "This artifact is local deterministic fixture evidence only. It is not a "
        "production baseline and not a claim that contradiction handling is solved.",
        "",
        "| Case | Expected contradiction | Detected | Proposal verdict |",
        "|---|---:|---:|---|",
    ]
    for case in report["cases"]:
        lines.append(
            "| `{case_id}` | `{expected}` | `{detected}` | `{verdict}` |".format(
                case_id=case["case_id"],
                expected=str(case["expected_contradiction"]).lower(),
                detected=str(case["detected_contradiction"]).lower(),
                verdict=case["proposal_gate_verdict"],
            )
        )
    lines.extend([
        "",
        "## Blockers To Full Claim",
        "",
    ])
    for blocker in report["blockers_to_full_claim"]:
        lines.append(f"- {blocker}")
    lines.extend([
        "",
        "## Reproduce",
        "",
        f"`{report['reproduce_command']}`",
        "",
    ])
    return "\n".join(lines)


def _run_fixture_cases() -> list[dict[str, Any]]:
    case_specs = [
        {
            "case_id": "thermal_conflicting_existing_invariant",
            "proposal_cell": "thermal",
            "existing_cell": "thermal",
            "existing_invariant": "out < 0",
            "proposal_invariant": "out >= 0",
            "expected_contradiction": True,
        },
        {
            "case_id": "thermal_consistent_existing_invariant",
            "proposal_cell": "thermal",
            "existing_cell": "thermal",
            "existing_invariant": "out >= 0",
            "proposal_invariant": "out >= 0",
            "expected_contradiction": False,
        },
        {
            "case_id": "other_cell_conflict_ignored",
            "proposal_cell": "thermal",
            "existing_cell": "energy",
            "existing_invariant": "out < 0",
            "proposal_invariant": "out >= 0",
            "expected_contradiction": False,
        },
    ]
    cases: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="wd-contradiction-rate-") as scratch:
        scratch_root = Path(scratch)
        for spec in case_specs:
            case_dir = scratch_root / spec["case_id"] / "axioms"
            _write_existing_axiom(
                case_dir=case_dir,
                case_id=spec["case_id"],
                cell_id=spec["existing_cell"],
                invariant=spec["existing_invariant"],
            )
            proposal = _proposal_fixture(
                case_id=spec["case_id"],
                cell_id=spec["proposal_cell"],
                invariant=spec["proposal_invariant"],
            )
            gate = proposal_gate.gate_contradiction(proposal, case_dir)
            evaluation = proposal_gate.evaluate_proposal(proposal, axioms_dir=case_dir)
            detected = evaluation["verdict"] == proposal_gate.V_REJECT_CONTRADICTION
            cases.append({
                "case_id": spec["case_id"],
                "cell_id": spec["proposal_cell"],
                "expected_contradiction": bool(spec["expected_contradiction"]),
                "detected_contradiction": bool(detected),
                "proposal_gate_verdict": evaluation["verdict"],
                "gate_ok": bool(gate["ok"]),
                "contradiction_gate_error_count": len(gate.get("errors") or []),
                "existing_invariant_shape": spec["existing_invariant"],
                "proposal_invariant_shape": spec["proposal_invariant"],
                "axiom_path_scope": "ephemeral_temp_fixture_not_reported",
            })
    return cases


def _proposal_fixture(*, case_id: str, cell_id: str, invariant: str) -> dict[str, Any]:
    return {
        "proposal_id": f"future-scale-{case_id}",
        "cell_id": cell_id,
        "solver_name": f"future_scale_{case_id}_solver",
        "purpose": "Deterministic fixture for contradiction-rate benchmark.",
        "inputs": [
            {
                "name": "a",
                "unit": "m",
                "description": "first fixture input",
                "range": [0, 100],
                "type": "number",
            },
            {
                "name": "b",
                "unit": "m",
                "description": "second fixture input",
                "range": [0, 100],
                "type": "number",
            },
        ],
        "outputs": [
            {
                "name": "out",
                "unit": "m",
                "description": "primary fixture output",
                "type": "number",
                "primary": True,
            }
        ],
        "formula_or_algorithm": {
            "kind": "formula_chain",
            "steps": [
                {
                    "name": "out",
                    "formula": "a + b",
                    "output_unit": "m",
                    "description": "fixture sum",
                },
            ],
        },
        "assumptions": ["fixture inputs are deterministic"],
        "invariants": [invariant],
        "units": {"system": "SI"},
        "tests": [
            {
                "name": "basic",
                "inputs": {"a": 1, "b": 2},
                "expected": 3,
                "tolerance": 0.000001,
            },
        ],
        "expected_failure_modes": [
            {"condition": "invalid fixture input", "behavior": "rejects proposal"}
        ],
        "examples": [
            {"description": "fixture example", "inputs": {"a": 1, "b": 2}, "expected": 3}
        ],
        "provenance_note": "local deterministic fixture",
        "estimated_latency_ms": 0.1,
        "expected_coverage_lift": {
            "value": 0.1,
            "uncertainty": "low",
            "rationale": "fixture only; not a production lift claim",
        },
        "risk_level": "low",
        "uncertainty_declaration": "small deterministic fixture, not production data",
    }


def _write_existing_axiom(
    *,
    case_dir: Path,
    case_id: str,
    cell_id: str,
    invariant: str,
) -> None:
    axiom_dir = case_dir / cell_id
    axiom_dir.mkdir(parents=True, exist_ok=True)
    axiom = {
        "model_id": f"existing_{case_id}",
        "cell_id": cell_id,
        "formulas": [{"name": "out", "formula": "a + b", "output_unit": "m"}],
        "variables": {"out": {"unit": "m"}},
        "validation": [{"check": invariant}],
    }
    (axiom_dir / "existing.yaml").write_text(
        json.dumps(axiom, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_utc(value: str) -> datetime:
    if not value.endswith("Z"):
        raise ValueError("--now requires a UTC timestamp ending with Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid --now timestamp: {value}") from exc
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now requires a UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _git_text(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _walk_scalars(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    scalars: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            scalars.extend(_walk_scalars(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scalars.extend(_walk_scalars(child, f"{path}[{index}]"))
    else:
        scalars.append((path, value))
    return scalars


def _looks_like_leak(value: str) -> bool:
    return any(pattern.search(value) for pattern in LEAK_PATTERNS)


if __name__ == "__main__":
    raise SystemExit(main())
