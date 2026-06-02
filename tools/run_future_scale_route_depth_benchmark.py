# SPDX-License-Identifier: BUSL-1.1
"""Emit a local benchmark artifact for the future-scale route_depth axis.

The benchmark uses static route-stage trace fixtures and the existing chat route
sanitizer before measuring depth. It never calls ChatService, providers, or live
runtime routes, and it never upgrades a future-scale claim gate.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.adapters.http.routes.chat import (  # noqa: E402
    CHAT_ROUTE_STAGE_ORDER,
    _sanitize_route_stage_trace,
)


REPORT_VERSION = "wd.future_scale_route_depth_benchmark.v1"
SCHEMA_VERSION = "future_scale_route_depth_benchmark.v1"
AXIS_ID = "route_depth"
JSON_ARTIFACT_NAME = "future_scale_route_depth_benchmark.json"
MARKDOWN_ARTIFACT_NAME = "future_scale_route_depth_benchmark.md"
BENCHMARK_SCOPE = "local_deterministic_sanitized_route_trace_fixture_only"
MEASUREMENT_LABEL = "MEASURED_LOCAL_ONLY"
FORBIDDEN_CLAIM_LABELS = {"PROVEN", "IMPLEMENTED", "CLAIM_SAFE"}
SAFE_FALSE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "required_runtime_evidence_present",
    "runtime_authority_changed",
    "runtime_authority_granted",
    "controls_present",
    "operator_gate_required",
    "external_writes_applied",
)
SAFE_TRUE_FIELDS = (
    "benchmark_artifact_present",
    "measured_value_present",
    "no_cloud_api_calls",
    "no_model_pull_or_download",
)
PRIVATE_MARKERS = (
    "WD_ROUTE_DEPTH_PRIVATE_QUERY_MARKER",
    "WD_ROUTE_DEPTH_PRIVATE_PROFILE_MARKER",
    "WD_ROUTE_DEPTH_PRIVATE_TRACE_MARKER",
    "WD_ROUTE_DEPTH_PRIVATE_PATH_MARKER",
)
LEAK_PATTERNS = (
    re.compile(r"[A-Za-z]:\\(?:Users|Python|Program Files)(?:\\|$)", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(r"(?:^|[\s\"'=:/])/(?:home|root|etc|var|opt|Users|tmp)(?:/|$)"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:openai|anthropic|google|gpt-[A-Za-z0-9._-]+|claude-[A-Za-z0-9._-]+|gemini-[A-Za-z0-9._-]+)\b", re.IGNORECASE),
    re.compile(r"\b(?:hf://|huggingface/|[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+)\b", re.IGNORECASE),
)
ALLOWED_STAGES = set(CHAT_ROUTE_STAGE_ORDER)

ROUTE_DEPTH_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "case_id": "cache_hit_short_route",
        "expected_route_depth": 2,
        "raw_trace": [
            {
                "stage": "language_detection",
                "explicit_hint": False,
                "detected_language": "fi",
                "query": "WD_ROUTE_DEPTH_PRIVATE_QUERY_MARKER",
            },
            {
                "stage": "hot_cache",
                "hit": True,
                "profile": "WD_ROUTE_DEPTH_PRIVATE_PROFILE_MARKER",
            },
        ],
    },
    {
        "case_id": "deterministic_solver_route",
        "expected_route_depth": 5,
        "raw_trace": [
            {
                "stage": "language_detection",
                "detected_language": "en",
                "query": "WD_ROUTE_DEPTH_PRIVATE_QUERY_MARKER",
            },
            {"stage": "hot_cache", "hit": False},
            {"stage": "memory_context", "limit": 4, "result_count": 1, "memory_score": 0.2},
            {
                "stage": "route_selection",
                "route_type": "deterministic",
                "solver_intent": "solver_alias_alpha",
            },
            {"stage": "deterministic_solver", "intent": "solver_alias_alpha", "answered": True},
        ],
    },
    {
        "case_id": "hybrid_retrieval_then_fallback_route",
        "expected_route_depth": 6,
        "raw_trace": [
            {"stage": "language_detection", "detected_language": "sv"},
            {"stage": "hot_cache", "hit": False},
            {"stage": "memory_context", "limit": 6, "result_count": 0, "memory_score": 0.0},
            {"stage": "route_selection", "route_type": "hybrid", "solver_intent": "solver_alias_beta"},
            {
                "stage": "hybrid_retrieval_8_cell",
                "enabled": True,
                "authoritative": False,
                "answered": False,
                "retrieval_mode": "fixture_alias",
                "hit_count": 0,
                "cell_id": "cell_alias_001",
                "trace_dump": "WD_ROUTE_DEPTH_PRIVATE_TRACE_MARKER",
            },
            {
                "stage": "orchestrator_llm_fallback",
                "route_type": "fallback",
                "source": "offline_fixture",
                "confidence": 0.1,
                "round_table_used": False,
            },
        ],
    },
    {
        "case_id": "hex_neighbor_assist_long_route",
        "expected_route_depth": 7,
        "raw_trace": [
            {"stage": "language_detection", "detected_language": "custom"},
            {"stage": "hot_cache", "hit": False},
            {"stage": "memory_context", "limit": 8, "result_count": 2, "memory_score": 0.5},
            {"stage": "route_selection", "route_type": "hex", "solver_intent": "solver_alias_gamma"},
            {
                "stage": "hybrid_retrieval_8_cell",
                "enabled": True,
                "authoritative": False,
                "answered": False,
                "retrieval_mode": "fixture_alias",
                "hit_count": 1,
                "cell_id": "cell_alias_002",
            },
            {
                "stage": "hex_neighbor_assist_7_cell",
                "enabled": True,
                "answered": True,
                "confidence": 0.7,
                "source": "neighbor_alias",
                "path": "WD_ROUTE_DEPTH_PRIVATE_PATH_MARKER",
            },
            {"stage": "ignored_private_stage", "query": "WD_ROUTE_DEPTH_PRIVATE_QUERY_MARKER"},
            {
                "stage": "orchestrator_llm_fallback",
                "route_type": "not_used",
                "source": "offline_fixture",
                "confidence": 0.0,
                "round_table_used": False,
            },
        ],
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
        help="Optional UTC timestamp override, e.g. 2026-06-03T00:00:00Z.",
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
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cases = [_measure_route_depth_fixture(fixture) for fixture in ROUTE_DEPTH_FIXTURES]
    depth_values = [case["route_depth"] for case in cases]
    histogram = _depth_histogram(depth_values)
    trace_count = len(depth_values)
    mean_route_depth = round(sum(depth_values) / trace_count, 6)

    result = {
        "trace_count": trace_count,
        "route_depth_values": depth_values,
        "route_depth_histogram": histogram,
        "min_route_depth": min(depth_values),
        "max_route_depth": max(depth_values),
        "mean_route_depth": mean_route_depth,
        "p50_route_depth": _nearest_rank_percentile(depth_values, 50),
        "p95_route_depth": _nearest_rank_percentile(depth_values, 95),
        "p99_route_depth": _nearest_rank_percentile(depth_values, 99),
        "percentile_method": "nearest_rank",
        "denominator": "static_sanitized_route_stage_trace_fixtures",
        "route_depth_histogram_exported": True,
        "runtime_route_depth_histogram_exported": False,
        "result_is_production_baseline": False,
    }
    report: dict[str, Any] = {
        "report_version": REPORT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "ok": False,
        "axis_id": AXIS_ID,
        "axis_definition": (
            "number of sanitized route-stage hops observed from query entry "
            "to final answer path"
        ),
        "axis_definition_source": "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        "measurement_label": MEASUREMENT_LABEL,
        "benchmark_scope": BENCHMARK_SCOPE,
        "evidence_status": "measured_local",
        "benchmark_artifact_present": True,
        "measured_value_present": True,
        "required_runtime_evidence_present": False,
        "not_a_production_baseline": True,
        "not_a_runtime_scorecard_update": True,
        "benchmark_result": result,
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
        "no_cloud_api_calls": True,
        "no_model_pull_or_download": True,
        "artifact_write_scope": [
            JSON_ARTIFACT_NAME,
            MARKDOWN_ARTIFACT_NAME,
        ],
        "source": {
            "fixture_set_alias": "route_depth_static_trace_set_v1",
            "fixture_set_sha256": _canonical_digest(
                {
                    "schema_version": SCHEMA_VERSION,
                    "cases": [
                        {
                            "case_id": case["case_id"],
                            "route_depth": case["route_depth"],
                            "sanitized_stage_sequence": case["sanitized_stage_sequence"],
                        }
                        for case in cases
                    ],
                }
            ),
            "trace_fixture_policy": "static_raw_traces_sanitized_before_measurement",
            "sanitizer_api": "waggledance.adapters.http.routes.chat._sanitize_route_stage_trace",
        },
        "source_paths": [
            "waggledance/adapters/http/routes/chat.py",
            "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
        ],
        "reproduce_command": (
            "python tools/run_future_scale_route_depth_benchmark.py "
            "--out-dir <out-dir> --now 2026-06-03T00:00:00Z --json"
        ),
        "git": {
            "sha": _git_text("rev-parse", "HEAD"),
            "branch": _git_text("branch", "--show-current"),
        },
        "blockers_to_full_claim": [
            "route-depth histogram is not exported from production runtime metrics",
            "fixture denominator is intentionally small and local-only",
            "needs production time-window and load-benchmark baselines before trend claims",
        ],
        "non_claims": [
            "does not claim route depth is optimized",
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
        errors.append("axis_id must be route_depth")
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if report.get("measurement_label") in FORBIDDEN_CLAIM_LABELS:
        errors.append("measurement_label must not upgrade to a proven claim")
    if report.get("measurement_label") != MEASUREMENT_LABEL:
        errors.append("measurement_label must be MEASURED_LOCAL_ONLY")
    if report.get("benchmark_scope") != BENCHMARK_SCOPE:
        errors.append(
            "benchmark_scope must be local_deterministic_sanitized_route_trace_fixture_only"
        )
    if report.get("evidence_status") != "measured_local":
        errors.append("evidence_status must be measured_local")
    for field in SAFE_FALSE_FIELDS:
        if report.get(field) is not False:
            errors.append(f"{field} must be exact false bool")
    for field in SAFE_TRUE_FIELDS:
        if report.get(field) is not True:
            errors.append(f"{field} must be exact true bool")
    if report.get("provider_jobs_delta") != 0:
        errors.append("provider_jobs_delta must be 0")
    if report.get("builder_jobs_delta") != 0:
        errors.append("builder_jobs_delta must be 0")
    if report.get("cloud_api_calls") != 0:
        errors.append("cloud_api_calls must be 0")
    if report.get("network_access") != "not_used":
        errors.append("network_access must be not_used")

    cases = report.get("cases")
    case_depths = _validate_cases(cases, errors)
    result = report.get("benchmark_result")
    if not isinstance(result, dict):
        errors.append("benchmark_result must be an object")
    else:
        _validate_result(result, case_depths, errors)

    for path, value in _walk_scalars(report):
        if isinstance(value, float) and not math.isfinite(value):
            errors.append(f"{path} contains a non-finite number")
        if isinstance(value, str) and _looks_like_leak(value):
            errors.append(f"{path} contains a forbidden secret/path/model-like string")
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
        f"- trace_count: `{result['trace_count']}`",
        f"- p50_route_depth: `{result['p50_route_depth']}`",
        f"- p95_route_depth: `{result['p95_route_depth']}`",
        f"- p99_route_depth: `{result['p99_route_depth']}`",
        f"- route_depth_histogram: `{json.dumps(result['route_depth_histogram'], sort_keys=True)}`",
        f"- claim_gate_satisfied: `{str(report['claim_gate_satisfied']).lower()}`",
        f"- claim_safe: `{str(report['claim_safe']).lower()}`",
        f"- literal_future_claim_safe: `{str(report['literal_future_claim_safe']).lower()}`",
        f"- required_runtime_evidence_present: `{str(report['required_runtime_evidence_present']).lower()}`",
        f"- runtime_authority_changed: `{str(report['runtime_authority_changed']).lower()}`",
        f"- external_writes_applied: `{str(report['external_writes_applied']).lower()}`",
        "",
        "This artifact is local deterministic fixture evidence only. It is not a "
        "production baseline, not a runtime route-depth metric, and not a claim "
        "that route depth is optimized.",
        "",
        "| Case | Route depth | Sanitized stages |",
        "|---|---:|---|",
    ]
    for case in report["cases"]:
        lines.append(
            "| `{case_id}` | `{depth}` | `{stages}` |".format(
                case_id=case["case_id"],
                depth=case["route_depth"],
                stages=" > ".join(case["sanitized_stage_sequence"]),
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


def _measure_route_depth_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    case_id = fixture.get("case_id")
    expected_route_depth = fixture.get("expected_route_depth")
    raw_trace = fixture.get("raw_trace")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError("route depth fixture case_id must be a non-empty string")
    if (
        not isinstance(expected_route_depth, int)
        or isinstance(expected_route_depth, bool)
        or expected_route_depth <= 0
    ):
        raise ValueError(f"{case_id}: expected_route_depth must be a positive int")
    if not isinstance(raw_trace, list):
        raise ValueError(f"{case_id}: raw_trace must be a list")

    sanitized_trace = _sanitize_route_stage_trace(raw_trace)
    _assert_sanitized_trace_safe(case_id, sanitized_trace)
    stage_sequence = [event["stage"] for event in sanitized_trace]
    route_depth = len(stage_sequence)
    if route_depth <= 0:
        raise ValueError(f"{case_id}: route_depth must be positive after sanitization")
    if route_depth != expected_route_depth:
        raise ValueError(
            f"{case_id}: route_depth {route_depth} does not match expected "
            f"{expected_route_depth}"
        )

    raw_text = json.dumps(raw_trace, sort_keys=True, default=str)
    sanitized_text = json.dumps(sanitized_trace, sort_keys=True, allow_nan=False)
    raw_contains_private_marker = any(marker in raw_text for marker in PRIVATE_MARKERS)
    sanitized_private_markers_absent = all(
        marker not in sanitized_text for marker in PRIVATE_MARKERS
    )
    return {
        "case_id": case_id,
        "expected_route_depth": expected_route_depth,
        "route_depth": route_depth,
        "sanitized_stage_count": len(sanitized_trace),
        "sanitized_stage_sequence": stage_sequence,
        "dropped_event_count": len([item for item in raw_trace if isinstance(item, dict)])
        - len(sanitized_trace),
        "raw_private_markers_removed": (
            raw_contains_private_marker and sanitized_private_markers_absent
        ),
    }


def _assert_sanitized_trace_safe(case_id: str, sanitized_trace: list[dict[str, Any]]) -> None:
    for index, event in enumerate(sanitized_trace):
        stage = event.get("stage")
        if not isinstance(stage, str) or stage not in ALLOWED_STAGES:
            raise ValueError(f"{case_id}: sanitized_trace[{index}].stage is not allowed")
        for path, value in _walk_scalars(event, f"sanitized_trace[{index}]"):
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{case_id}: {path} contains a non-finite number")
            if isinstance(value, str) and _looks_like_leak(value):
                raise ValueError(f"{case_id}: {path} contains a forbidden string")


def _validate_cases(value: Any, errors: list[str]) -> list[int]:
    depths: list[int] = []
    if not isinstance(value, list) or not value:
        errors.append("cases must be a non-empty list")
        return depths
    for index, case in enumerate(value):
        if not isinstance(case, dict):
            errors.append(f"cases[{index}] must be an object")
            continue
        route_depth = case.get("route_depth")
        expected = case.get("expected_route_depth")
        stage_count = case.get("sanitized_stage_count")
        if (
            not isinstance(route_depth, int)
            or isinstance(route_depth, bool)
            or route_depth <= 0
        ):
            errors.append(f"cases[{index}].route_depth must be a positive int")
        else:
            depths.append(route_depth)
        if expected != route_depth:
            errors.append(f"cases[{index}].expected_route_depth must equal route_depth")
        if stage_count != route_depth:
            errors.append(f"cases[{index}].sanitized_stage_count must equal route_depth")
        if case.get("raw_private_markers_removed") is not True:
            errors.append(f"cases[{index}].raw_private_markers_removed must be exact true bool")
        sequence = case.get("sanitized_stage_sequence")
        if not isinstance(sequence, list) or not sequence:
            errors.append(f"cases[{index}].sanitized_stage_sequence must be non-empty list")
        else:
            if len(sequence) != route_depth:
                errors.append(
                    f"cases[{index}].sanitized_stage_sequence length must equal route_depth"
                )
            for stage_index, stage in enumerate(sequence):
                if not isinstance(stage, str) or stage not in ALLOWED_STAGES:
                    errors.append(
                        f"cases[{index}].sanitized_stage_sequence[{stage_index}] is not allowed"
                    )
    return depths


def _validate_result(
    result: dict[str, Any],
    case_depths: list[int],
    errors: list[str],
) -> None:
    trace_count = result.get("trace_count")
    values = result.get("route_depth_values")
    if (
        not isinstance(trace_count, int)
        or isinstance(trace_count, bool)
        or trace_count <= 0
    ):
        errors.append("benchmark_result.trace_count must be a positive int")
    if not isinstance(values, list) or not values:
        errors.append("benchmark_result.route_depth_values must be a non-empty list")
        values = []
    else:
        for index, value in enumerate(values):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                errors.append(f"benchmark_result.route_depth_values[{index}] must be a positive int")
    if case_depths and values and values != case_depths:
        errors.append("benchmark_result.route_depth_values must match case route depths")
    if isinstance(trace_count, int) and not isinstance(trace_count, bool) and values:
        if trace_count != len(values):
            errors.append("benchmark_result.trace_count must match route_depth_values length")

    int_values = [
        value for value in values if isinstance(value, int) and not isinstance(value, bool)
    ]
    if not int_values:
        return
    expected_histogram = _depth_histogram(int_values)
    if result.get("route_depth_histogram") != expected_histogram:
        errors.append("benchmark_result.route_depth_histogram does not match values")
    expected_mean = round(sum(int_values) / len(int_values), 6)
    if result.get("mean_route_depth") != expected_mean:
        errors.append("benchmark_result.mean_route_depth does not match values")
    expected_fields = {
        "min_route_depth": min(int_values),
        "max_route_depth": max(int_values),
        "p50_route_depth": _nearest_rank_percentile(int_values, 50),
        "p95_route_depth": _nearest_rank_percentile(int_values, 95),
        "p99_route_depth": _nearest_rank_percentile(int_values, 99),
    }
    for field, expected in expected_fields.items():
        if result.get(field) != expected:
            errors.append(f"benchmark_result.{field} does not match values")
    if result.get("percentile_method") != "nearest_rank":
        errors.append("benchmark_result.percentile_method must be nearest_rank")
    if result.get("denominator") != "static_sanitized_route_stage_trace_fixtures":
        errors.append("benchmark_result.denominator is not the fixture denominator")
    if result.get("route_depth_histogram_exported") is not True:
        errors.append("benchmark_result.route_depth_histogram_exported must be true")
    if result.get("runtime_route_depth_histogram_exported") is not False:
        errors.append("benchmark_result.runtime_route_depth_histogram_exported must be false")
    if result.get("result_is_production_baseline") is not False:
        errors.append("benchmark_result.result_is_production_baseline must be false")


def _depth_histogram(depth_values: Sequence[int]) -> dict[str, int]:
    histogram: dict[str, int] = {}
    for depth in sorted(depth_values):
        key = str(int(depth))
        histogram[key] = histogram.get(key, 0) + 1
    return histogram


def _nearest_rank_percentile(depth_values: Sequence[int], percentile: int) -> int:
    if not depth_values:
        raise ValueError("depth_values must not be empty")
    if percentile <= 0 or percentile > 100:
        raise ValueError("percentile must be in the range 1..100")
    ordered = sorted(int(value) for value in depth_values)
    index = max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1)
    return ordered[index]


def _parse_utc(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("--now requires a UTC timestamp with Z")
    parsed_utc = parsed.astimezone(timezone.utc)
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("--now requires a UTC timestamp with Z")
    return parsed_utc


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _git_text(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def _walk_scalars(value: Any, path: str = "$") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        scalars: list[tuple[str, Any]] = []
        for key, child in value.items():
            scalars.extend(_walk_scalars(child, f"{path}.{key}"))
        return scalars
    if isinstance(value, list):
        scalars = []
        for index, child in enumerate(value):
            scalars.extend(_walk_scalars(child, f"{path}[{index}]"))
        return scalars
    return [(path, value)]


def _looks_like_leak(value: str) -> bool:
    return any(pattern.search(value) for pattern in LEAK_PATTERNS)


if __name__ == "__main__":
    raise SystemExit(main())
