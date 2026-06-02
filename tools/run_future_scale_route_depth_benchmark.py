#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Emit a local benchmark artifact for the future-scale route_depth axis.

The axis is defined in docs/architecture/HONEYCOMB_SOLVER_SCALING.md as the
median number of hops from query to final answer. This tool measures that
proxy from deterministic, sanitized route-stage trace fixtures. It is not a
production baseline and never changes runtime routing, metrics, or authority.
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
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from waggledance.adapters.http.routes.chat import (  # noqa: E402
    CHAT_ROUTE_STAGE_ORDER,
)


REPORT_VERSION = "wd.future_scale_route_depth_benchmark.v1"
SCHEMA_VERSION = "future_scale_route_depth_benchmark.v1"
AXIS_ID = "route_depth"
JSON_ARTIFACT_NAME = "future_scale_route_depth_benchmark.json"
MARKDOWN_ARTIFACT_NAME = "future_scale_route_depth_benchmark.md"
BENCHMARK_SCOPE = "local_deterministic_sanitized_route_stage_trace_fixture"
MEASUREMENT_LABEL = "MEASURED_LOCAL_ONLY"
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
    re.compile(r"[A-Za-z]:[/\\](?:Users|Python|Program Files|tmp)\b", re.IGNORECASE),
    re.compile(r"[A-Za-z]:tmp\b", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(r"(?:^|[/\\])(?:home|root|etc|var|opt|Users|mnt|tmp)(?:[/\\]|$)", re.IGNORECASE),
    re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16,}\b"),
)
CASE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,80}$")
ALLOWED_STAGES = tuple(CHAT_ROUTE_STAGE_ORDER)
ALLOWED_STAGE_SET = set(ALLOWED_STAGES)


DEFAULT_TRACE_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "hot_cache_hit",
        "trace": (
            {"stage": "language_detection"},
            {"stage": "hot_cache"},
        ),
    },
    {
        "case_id": "deterministic_solver_answer",
        "trace": (
            {"stage": "language_detection"},
            {"stage": "hot_cache"},
            {"stage": "memory_context"},
            {"stage": "route_selection"},
            {"stage": "deterministic_solver"},
        ),
    },
    {
        "case_id": "authoritative_hybrid_answer",
        "trace": (
            {"stage": "language_detection"},
            {"stage": "hot_cache"},
            {"stage": "memory_context"},
            {"stage": "route_selection"},
            {"stage": "deterministic_solver"},
            {"stage": "hybrid_retrieval_8_cell"},
        ),
    },
    {
        "case_id": "hex_neighbor_answer",
        "trace": (
            {"stage": "language_detection"},
            {"stage": "hot_cache"},
            {"stage": "memory_context"},
            {"stage": "route_selection"},
            {"stage": "deterministic_solver"},
            {"stage": "hybrid_retrieval_8_cell"},
            {"stage": "hex_neighbor_assist_7_cell"},
        ),
    },
    {
        "case_id": "orchestrator_fallback_answer",
        "trace": (
            {"stage": "language_detection"},
            {"stage": "hot_cache"},
            {"stage": "memory_context"},
            {"stage": "route_selection"},
            {"stage": "deterministic_solver"},
            {"stage": "hybrid_retrieval_8_cell"},
            {"stage": "hex_neighbor_assist_7_cell"},
            {"stage": "orchestrator_llm_fallback"},
        ),
    },
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic local benchmark artifact for the "
            "future-scale route_depth axis."
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
        help="Optional UTC timestamp override, e.g. 2026-06-02T20:55:00Z.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_future_scale_route_depth_benchmark(
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"future scale route_depth benchmark FAILED: {exc}", file=sys.stderr)
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


def build_future_scale_route_depth_benchmark(
    *,
    trace_fixtures: Sequence[Mapping[str, Any]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cases = _build_case_records(trace_fixtures or DEFAULT_TRACE_FIXTURES)
    depths = [case["route_depth"] for case in cases if case["route_depth"] > 0]
    measured = bool(depths)
    benchmark_result = _build_benchmark_result(depths)
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": False,
        "axis_id": AXIS_ID,
        "axis_definition": "median number of hops from query to final answer",
        "axis_definition_source": "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        "measurement_label": MEASUREMENT_LABEL,
        "benchmark_scope": BENCHMARK_SCOPE,
        "not_a_production_baseline": True,
        "not_a_runtime_scorecard_update": True,
        "benchmark_artifact_present": True,
        "measured_value_present": measured,
        "evidence_status": "measured_local" if measured else "blocked_no_route_depth_samples",
        "trace_source": "sanitized_route_stage_trace_fixture",
        "trace_stage_policy": "allowlisted_stage_names_only_no_query_or_payload",
        "allowed_route_stage_order": list(ALLOWED_STAGES),
        "benchmark_result": benchmark_result,
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
            "waggledance/adapters/http/routes/chat.py",
            "waggledance/application/dto/chat_dto.py",
            "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        ],
        "reproduce_command": (
            "python tools/run_future_scale_route_depth_benchmark.py "
            "--out-dir <out-dir> --now 2026-06-02T20:55:00Z --json"
        ),
        "git": {
            "sha": _git_text("rev-parse", "HEAD"),
            "branch": _git_text("branch", "--show-current"),
        },
        "blockers_to_full_claim": [
            "fixture traces are deterministic local examples, not production traffic",
            "needs exported runtime route-depth histograms by route/profile",
            "needs repeated versioned benchmark windows before trend claims",
            "needs manifest aggregation with sibling future-scale axes",
        ],
        "non_claims": [
            "does not claim superior intelligence",
            "does not claim production route efficiency",
            "does not claim unlimited scalability",
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
        errors.append("axis_id must be route_depth")
    if report.get("measurement_label") != MEASUREMENT_LABEL:
        errors.append("measurement_label must be MEASURED_LOCAL_ONLY")
    if report.get("benchmark_scope") != BENCHMARK_SCOPE:
        errors.append(
            "benchmark_scope must be local_deterministic_sanitized_route_stage_trace_fixture"
        )
    for field in SAFE_FALSE_FIELDS:
        if report.get(field) is not False:
            errors.append(f"{field} must be exact false bool")
    if report.get("provider_jobs_delta") != 0:
        errors.append("provider_jobs_delta must be 0")
    if report.get("builder_jobs_delta") != 0:
        errors.append("builder_jobs_delta must be 0")
    if report.get("cloud_api_calls") != 0:
        errors.append("cloud_api_calls must be 0")
    if report.get("network_access") != "not_used":
        errors.append("network_access must be not_used")

    result = report.get("benchmark_result")
    if not isinstance(result, dict):
        errors.append("benchmark_result must be an object")
    else:
        _validate_benchmark_result(result, errors)

    cases = report.get("cases")
    if not isinstance(cases, list):
        errors.append("cases must be a list")
    else:
        for index, case in enumerate(cases):
            _validate_case_record(index, case, errors)

    allowed_order = report.get("allowed_route_stage_order")
    if allowed_order != list(ALLOWED_STAGES):
        errors.append("allowed_route_stage_order must match CHAT_ROUTE_STAGE_ORDER")

    for path, value in _walk_scalars(report):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path} contains a non-finite number")
        if isinstance(value, str) and _looks_like_leak(value):
            errors.append(f"{path} contains a forbidden secret/path-like string")
    return errors


def render_markdown(report: dict[str, Any]) -> str:
    result = report["benchmark_result"]
    lines = [
        "# Future-Scale Route Depth Benchmark",
        "",
        f"- report_version: `{report['report_version']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- axis_id: `{report['axis_id']}`",
        f"- measurement_label: `{report['measurement_label']}`",
        f"- benchmark_scope: `{report['benchmark_scope']}`",
        f"- sample_count: `{result['sample_count']}`",
        f"- p50_depth: `{result['p50_depth']}`",
        f"- p95_depth: `{result['p95_depth']}`",
        f"- p99_depth: `{result['p99_depth']}`",
        f"- claim_gate_satisfied: `{str(report['claim_gate_satisfied']).lower()}`",
        f"- claim_safe: `{str(report['claim_safe']).lower()}`",
        f"- literal_future_claim_safe: `{str(report['literal_future_claim_safe']).lower()}`",
        f"- runtime_authority_changed: `{str(report['runtime_authority_changed']).lower()}`",
        f"- external_writes_applied: `{str(report['external_writes_applied']).lower()}`",
        "",
        "This artifact measures local deterministic sanitized route-stage trace "
        "fixtures only. It is not a production baseline and not proof of "
        "superior intelligence, unlimited scalability, or runtime efficiency.",
        "",
        "| Case | Route depth | Final stage |",
        "|---|---:|---|",
    ]
    for case in report["cases"]:
        lines.append(
            "| `{case_id}` | `{route_depth}` | `{final_stage}` |".format(
                case_id=case["case_id"],
                route_depth=case["route_depth"],
                final_stage=case["final_stage"],
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


def _build_case_records(fixtures: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, fixture in enumerate(fixtures):
        case_id = str(fixture.get("case_id") or f"trace_case_{index:03d}")
        trace = fixture.get("trace")
        sanitized_stages = _sanitize_stage_names(trace)
        final_stage = sanitized_stages[-1] if sanitized_stages else "none"
        records.append(
            {
                "case_id": case_id,
                "route_depth": len(sanitized_stages),
                "final_stage": final_stage,
                "observed_stages": sanitized_stages,
                "ignored_event_count": _ignored_event_count(trace),
            }
        )
    return records


def _sanitize_stage_names(trace: Any) -> list[str]:
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes)):
        return []
    stages: list[str] = []
    for event in trace:
        if not isinstance(event, Mapping):
            continue
        stage = event.get("stage")
        if isinstance(stage, str) and stage in ALLOWED_STAGE_SET:
            stages.append(stage)
    return stages


def _ignored_event_count(trace: Any) -> int:
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes)):
        return 0
    return max(0, len(trace) - len(_sanitize_stage_names(trace)))


def _build_benchmark_result(depths: Sequence[int]) -> dict[str, Any]:
    sample_count = len(depths)
    histogram = {str(stage): 0 for stage in range(0, len(ALLOWED_STAGES) + 1)}
    for depth in depths:
        histogram[str(depth)] = histogram.get(str(depth), 0) + 1
    if not depths:
        return {
            "sample_count": 0,
            "min_depth": 0,
            "max_depth": 0,
            "mean_depth": 0.0,
            "p50_depth": 0.0,
            "p95_depth": 0.0,
            "p99_depth": 0.0,
            "depth_histogram": histogram,
            "percentile_method": "linear_interpolated_sorted_depths",
            "is_production_baseline": False,
        }
    return {
        "sample_count": sample_count,
        "min_depth": min(depths),
        "max_depth": max(depths),
        "mean_depth": round(sum(depths) / sample_count, 6),
        "p50_depth": _percentile(depths, 0.50),
        "p95_depth": _percentile(depths, 0.95),
        "p99_depth": _percentile(depths, 0.99),
        "depth_histogram": histogram,
        "percentile_method": "linear_interpolated_sorted_depths",
        "is_production_baseline": False,
    }


def _percentile(values: Sequence[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def _validate_benchmark_result(result: Mapping[str, Any], errors: list[str]) -> None:
    sample_count = result.get("sample_count")
    if not isinstance(sample_count, int) or isinstance(sample_count, bool) or sample_count < 0:
        errors.append("benchmark_result.sample_count must be a non-negative int")
    for field in ("min_depth", "max_depth"):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"benchmark_result.{field} must be a non-negative int")
    for field in ("mean_depth", "p50_depth", "p95_depth", "p99_depth"):
        if not _is_finite_number(result.get(field)):
            errors.append(f"benchmark_result.{field} must be finite")
    if result.get("percentile_method") != "linear_interpolated_sorted_depths":
        errors.append("benchmark_result.percentile_method is not recognized")
    if result.get("is_production_baseline") is not False:
        errors.append("benchmark_result.is_production_baseline must be false")
    histogram = result.get("depth_histogram")
    if not isinstance(histogram, dict):
        errors.append("benchmark_result.depth_histogram must be an object")
        return
    total = 0
    for key, value in histogram.items():
        if not isinstance(key, str) or not key.isdigit():
            errors.append("benchmark_result.depth_histogram keys must be numeric strings")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append("benchmark_result.depth_histogram values must be non-negative ints")
        else:
            total += value
    if isinstance(sample_count, int) and not isinstance(sample_count, bool) and total != sample_count:
        errors.append("benchmark_result.depth_histogram does not sum to sample_count")


def _validate_case_record(index: int, case: Any, errors: list[str]) -> None:
    if not isinstance(case, dict):
        errors.append(f"cases[{index}] must be an object")
        return
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not CASE_ID_PATTERN.match(case_id):
        errors.append(f"cases[{index}].case_id must be a stable lowercase alias")
    route_depth = case.get("route_depth")
    if not isinstance(route_depth, int) or isinstance(route_depth, bool) or route_depth < 0:
        errors.append(f"cases[{index}].route_depth must be a non-negative int")
    final_stage = case.get("final_stage")
    if not isinstance(final_stage, str) or final_stage not in set(ALLOWED_STAGES) | {"none"}:
        errors.append(f"cases[{index}].final_stage must be an allowed route stage")
    stages = case.get("observed_stages")
    if not isinstance(stages, list):
        errors.append(f"cases[{index}].observed_stages must be a list")
    else:
        for stage_index, stage in enumerate(stages):
            if not isinstance(stage, str) or stage not in ALLOWED_STAGE_SET:
                errors.append(
                    f"cases[{index}].observed_stages[{stage_index}] must be an allowed route stage"
                )
        if isinstance(route_depth, int) and not isinstance(route_depth, bool) and len(stages) != route_depth:
            errors.append(f"cases[{index}].route_depth does not match observed_stages")
    ignored = case.get("ignored_event_count")
    if not isinstance(ignored, int) or isinstance(ignored, bool) or ignored < 0:
        errors.append(f"cases[{index}].ignored_event_count must be a non-negative int")


def _parse_utc(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now requires a UTC timestamp")
    return parsed.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _git_text(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return "unavailable"
    if result.returncode != 0:
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _walk_scalars(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        found: list[tuple[str, Any]] = []
        for key, nested in value.items():
            found.extend(_walk_scalars(nested, f"{path}.{key}"))
        return found
    if isinstance(value, list):
        found = []
        for index, nested in enumerate(value):
            found.extend(_walk_scalars(nested, f"{path}[{index}]"))
        return found
    return [(path, value)]


def _looks_like_leak(value: str) -> bool:
    return any(pattern.search(value) for pattern in LEAK_PATTERNS)


if __name__ == "__main__":
    raise SystemExit(main())
