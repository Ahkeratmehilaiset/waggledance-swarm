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
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from waggledance.adapters.http.routes.chat import (  # noqa: E402
    CHAT_ROUTE_STAGE_ORDER,
    _sanitize_route_stage_trace,
)
from tools.future_scale_contract_safety import validate_scalar_safety  # noqa: E402
from waggledance.core.leak_policy import is_finite_number  # noqa: E402


REPORT_VERSION = "wd.future_scale_route_depth_benchmark.v1"
SCHEMA_VERSION = "future_scale_route_depth_benchmark.v1"
AXIS_ID = "route_depth"
JSON_ARTIFACT_NAME = "future_scale_route_depth_benchmark.json"
MARKDOWN_ARTIFACT_NAME = "future_scale_route_depth_benchmark.md"
PRODUCTION_HISTOGRAM_ARTIFACT_NAME = (
    "future_scale_route_depth_production_histogram_artifact.json"
)
PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME = (
    "future_scale_route_depth_production_capture_window_attachment.json"
)
PRODUCTION_HISTOGRAM_SCHEMA_VERSION = (
    "future_scale_route_depth_production_histogram_artifact.v1"
)
PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION = (
    "future_scale_route_depth_production_capture_window_attachment.v1"
)
PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION = (
    "future_scale_route_depth_production_capture_window.v1"
)
BENCHMARK_SCOPE = "local_deterministic_sanitized_route_stage_trace_fixture"
MEASUREMENT_LABEL = "MEASURED_LOCAL_ONLY"
SOURCE_PATHS = (
    "waggledance/adapters/http/routes/chat.py",
    "waggledance/application/dto/chat_dto.py",
    "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
)
ALLOWED_METADATA_PATH_VALUES = frozenset(SOURCE_PATHS)
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
ALLOWED_STAGES = tuple(CHAT_ROUTE_STAGE_ORDER)
ALLOWED_STAGE_SET = set(ALLOWED_STAGES)
ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES = (
    "waggledance_route_depth_histogram_bucket",
    "waggledance_route_depth_histogram_count",
    "waggledance_route_depth_histogram_sum",
    "waggledance_route_depth_observations_total",
)
ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES = (
    "route_profile",
    "final_stage",
    "le",
)
ROUTE_DEPTH_BUCKET_LABELS = tuple(
    str(depth) for depth in range(0, len(ALLOWED_STAGES) + 1)
) + ("+Inf",)
ALLOWED_CAPTURE_SOURCE_KINDS = ("operator_owned_metrics_export",)
CAPTURE_WINDOW_REQUIRED_FIELDS = (
    "schema_version",
    "capture_window_id",
    "source_kind",
    "operator_owned_export",
    "window_start_utc",
    "window_end_utc",
    "metric_names",
    "label_names",
    "bucket_labels",
    "sample_count",
    "route_depth_sum",
    "aggregate_cumulative_buckets",
    "route_profile_count",
    "route_profiles",
    "raw_payload_included",
    "query_text_included",
    "local_paths_recorded",
    "network_access",
    "cloud_api_calls",
)
CAPTURE_WINDOW_ALLOWED_FIELDS = set(CAPTURE_WINDOW_REQUIRED_FIELDS) | {
    "window_digest_sha256",
}


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
    parser.add_argument(
        "--production-capture-window-json",
        type=Path,
        default=None,
        help=(
            "Optional local operator-owned route-depth capture-window JSON export. "
            "The input path is not recorded in the emitted artifacts."
        ),
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        capture_window = (
            _load_capture_window_export(args.production_capture_window_json)
            if args.production_capture_window_json is not None
            else None
        )
        report = build_future_scale_route_depth_benchmark(
            now_utc=_parse_utc(args.now) if args.now else None,
            production_capture_window=capture_window,
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
        (args.out_dir / PRODUCTION_HISTOGRAM_ARTIFACT_NAME).write_text(
            json.dumps(
                report["production_route_depth_histogram_artifact"],
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (args.out_dir / PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME).write_text(
            json.dumps(
                report["production_route_depth_capture_window_attachment"],
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
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
    production_capture_window: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cases = _build_case_records(trace_fixtures or DEFAULT_TRACE_FIXTURES)
    depths = [case["route_depth"] for case in cases if case["route_depth"] > 0]
    measured = bool(depths)
    benchmark_result = _build_benchmark_result(depths)
    histogram_artifact = _build_production_route_depth_histogram_artifact(cases)
    capture_attachment = _build_production_capture_window_attachment(
        production_capture_window,
    )
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
        "required_runtime_evidence_present": False,
        "benchmark_artifact_present": True,
        "measured_value_present": measured,
        "evidence_status": "measured_local" if measured else "blocked_no_route_depth_samples",
        "trace_source": "sanitized_route_stage_trace_fixture",
        "trace_stage_policy": "allowlisted_stage_names_only_no_query_or_payload",
        "allowed_route_stage_order": list(ALLOWED_STAGES),
        "benchmark_result": benchmark_result,
        "production_route_depth_histogram_artifact": histogram_artifact,
        "production_route_depth_capture_window_attachment": capture_attachment,
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
            PRODUCTION_HISTOGRAM_ARTIFACT_NAME,
            PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_NAME,
            MARKDOWN_ARTIFACT_NAME,
        ],
        "source_paths": list(SOURCE_PATHS),
        "reproduce_command": (
            "python tools/run_future_scale_route_depth_benchmark.py "
            "--out-dir <out-dir> --now 2026-06-02T20:55:00Z --json"
        ),
        "git": {
            "sha": _git_text("rev-parse", "HEAD"),
            "branch": _git_text("branch", "--show-current"),
        },
        "source": {
            "fixture_set_alias": "route_depth_static_trace_set_v1",
            "fixture_set_sha256": _canonical_digest(
                {
                    "case_records": [
                        {
                            "case_id": case["case_id"],
                            "route_depth": case["route_depth"],
                            "observed_stages": case["observed_stages"],
                        }
                        for case in cases
                    ]
                }
            ),
            "sanitizer_api": "waggledance.adapters.http.routes.chat._sanitize_route_stage_trace",
            "trace_fixture_policy": "static_traces_sanitized_before_stage_count",
        },
        "blockers_to_full_claim": [
            "fixture traces are deterministic local examples, not production traffic",
            "needs repeated operator-owned live production route-depth capture "
            "windows validated through the attachment contract",
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
    artifact = report.get("production_route_depth_histogram_artifact")
    if not isinstance(artifact, dict):
        errors.append("production_route_depth_histogram_artifact must be an object")
    else:
        _validate_production_route_depth_histogram_artifact(artifact, errors)
    attachment = report.get("production_route_depth_capture_window_attachment")
    if not isinstance(attachment, dict):
        errors.append("production_route_depth_capture_window_attachment must be an object")
    else:
        _validate_production_capture_window_attachment(attachment, errors)

    cases = report.get("cases")
    if not isinstance(cases, list):
        errors.append("cases must be a list")
    else:
        for index, case in enumerate(cases):
            _validate_case_record(index, case, errors)

    allowed_order = report.get("allowed_route_stage_order")
    if allowed_order != list(ALLOWED_STAGES):
        errors.append("allowed_route_stage_order must match CHAT_ROUTE_STAGE_ORDER")

    errors.extend(
        validate_scalar_safety(
            report,
            allowed_metadata_path_values=ALLOWED_METADATA_PATH_VALUES,
        )
    )
    return errors


def render_markdown(report: dict[str, Any]) -> str:
    result = report["benchmark_result"]
    histogram_artifact = report["production_route_depth_histogram_artifact"]
    capture_attachment = report["production_route_depth_capture_window_attachment"]
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
        "- production_histogram_artifact: "
        f"`{histogram_artifact['artifact_status']}`",
        "- production_histogram_runtime_data_attached: "
        f"`{str(histogram_artifact['production_runtime_data_attached']).lower()}`",
        "- production_capture_window_attachment: "
        f"`{capture_attachment['attachment_status']}`",
        "- production_capture_window_runtime_data_attached: "
        f"`{str(capture_attachment['production_runtime_data_attached']).lower()}`",
        "- production_capture_window_count: "
        f"`{capture_attachment['capture_window_count']}`",
        f"- claim_gate_satisfied: `{str(report['claim_gate_satisfied']).lower()}`",
        f"- claim_safe: `{str(report['claim_safe']).lower()}`",
        f"- literal_future_claim_safe: `{str(report['literal_future_claim_safe']).lower()}`",
        f"- required_runtime_evidence_present: `{str(report['required_runtime_evidence_present']).lower()}`",
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


def _build_production_route_depth_histogram_artifact(
    cases: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    route_profiles = []
    aggregate_histogram = {label: 0 for label in ROUTE_DEPTH_BUCKET_LABELS}
    total_count = 0
    total_depth_sum = 0
    for case in cases:
        route_depth = case.get("route_depth")
        final_stage = case.get("final_stage")
        case_id = case.get("case_id")
        if (
            not isinstance(case_id, str)
            or not isinstance(route_depth, int)
            or isinstance(route_depth, bool)
            or route_depth < 0
            or not isinstance(final_stage, str)
            or final_stage not in set(ALLOWED_STAGES) | {"none"}
        ):
            continue
        profile_histogram = _cumulative_route_depth_buckets(route_depth)
        route_profiles.append(
            {
                "route_profile": case_id,
                "final_stage": final_stage,
                "sample_count": 1,
                "route_depth_sum": route_depth,
                "observed_route_depth": route_depth,
                "cumulative_buckets": profile_histogram,
            }
        )
        total_count += 1
        total_depth_sum += route_depth
        for label, value in profile_histogram.items():
            aggregate_histogram[label] += value

    digest_source = {
        "metric_names": list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(ROUTE_DEPTH_BUCKET_LABELS),
        "route_profiles": route_profiles,
    }
    return {
        "schema_version": PRODUCTION_HISTOGRAM_SCHEMA_VERSION,
        "artifact_status": "production_histogram_artifact_contract_available",
        "measurement_scope": (
            "production-shaped route-depth histogram artifact contract from "
            "sanitized route-stage depth samples; no live production corpus attached"
        ),
        "production_runtime_data_attached": False,
        "production_data_source": "not_attached",
        "required_runtime_evidence_present": False,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "runtime_authority_changed": False,
        "runtime_authority_granted": False,
        "controls_present": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "network_access": "not_used",
        "cloud_api_calls": 0,
        "metric_names": list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(ROUTE_DEPTH_BUCKET_LABELS),
        "route_profile_count": len(route_profiles),
        "sample_count": total_count,
        "route_depth_sum": total_depth_sum,
        "aggregate_cumulative_buckets": aggregate_histogram,
        "route_profiles": route_profiles,
        "artifact_digest_sha256": _canonical_digest(digest_source),
        "blockers_to_runtime_claim": [
            "artifact contract is production-shaped, not live production data",
            "needs operator-owned production scrape/export attached to this contract",
            "needs route/profile time-window retention before trend claims",
        ],
        "safe_conclusion": (
            "The artifact contract defines a sanitized route-depth histogram "
            "shape with route_profile, final_stage, and le labels. It does not "
            "attach live production runtime data or satisfy the future-scale "
            "claim gate."
        ),
    }


def _load_capture_window_export(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("could not read production capture-window JSON") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("production capture-window JSON is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("production capture-window JSON must be an object")
    return parsed


def _build_production_capture_window_attachment(
    capture_window: Mapping[str, Any] | None,
) -> dict[str, Any]:
    capture_windows = []
    if capture_window is not None:
        capture_windows.append(_normalize_production_capture_window(capture_window))
    data_attached = bool(capture_windows)
    digest_source = {
        "schema_version": PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION,
        "metric_names": list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(ROUTE_DEPTH_BUCKET_LABELS),
        "capture_windows": capture_windows,
    }
    return {
        "schema_version": PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION,
        "capture_window_schema_version": PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION,
        "attachment_status": (
            "operator_capture_window_attached"
            if data_attached
            else "capture_window_attachment_contract_available"
        ),
        "measurement_scope": (
            "operator-owned route-depth capture-window attachment verifier; "
            "one attached window is not enough to satisfy runtime evidence gates"
        ),
        "production_runtime_data_attached": data_attached,
        "production_data_source": (
            capture_windows[0]["source_kind"] if data_attached else "not_attached"
        ),
        "required_runtime_evidence_present": False,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "runtime_authority_changed": False,
        "runtime_authority_granted": False,
        "controls_present": False,
        "operator_gate_required": False,
        "external_writes_applied": False,
        "network_access": "not_used",
        "cloud_api_calls": 0,
        "metric_names": list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(ROUTE_DEPTH_BUCKET_LABELS),
        "allowed_source_kinds": list(ALLOWED_CAPTURE_SOURCE_KINDS),
        "capture_window_count": len(capture_windows),
        "capture_windows": capture_windows,
        "attachment_digest_sha256": _canonical_digest(digest_source),
        "blockers_to_runtime_claim": [
            "needs repeated operator-owned production capture windows",
            "needs retention policy for route/profile capture windows",
            "needs independent production benchmark-window correlation",
        ],
        "safe_conclusion": (
            "The attachment contract can validate a sanitized operator-owned "
            "route-depth capture window, but it does not by itself satisfy "
            "runtime evidence or future-scale claim gates."
        ),
    }


def _normalize_production_capture_window(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    keys = set(payload)
    missing = [field for field in CAPTURE_WINDOW_REQUIRED_FIELDS if field not in keys]
    if missing:
        errors.append("production capture window missing required fields")
    unknown = keys - CAPTURE_WINDOW_ALLOWED_FIELDS
    if unknown:
        errors.append("production capture window contains unsupported fields")

    if payload.get("schema_version") != PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION:
        errors.append("production capture window schema_version is not recognized")
    capture_window_id = payload.get("capture_window_id")
    if (
        not isinstance(capture_window_id, str)
        or not _is_stable_case_id(capture_window_id)
    ):
        errors.append(
            "production capture window capture_window_id must be a stable lowercase alias"
        )
    source_kind = payload.get("source_kind")
    if source_kind not in ALLOWED_CAPTURE_SOURCE_KINDS:
        errors.append("production capture window source_kind is not recognized")
    if payload.get("operator_owned_export") is not True:
        errors.append("production capture window operator_owned_export must be true")

    start = _parse_capture_timestamp(
        payload.get("window_start_utc"),
        "window_start_utc",
        errors,
    )
    end = _parse_capture_timestamp(
        payload.get("window_end_utc"),
        "window_end_utc",
        errors,
    )
    if start is not None and end is not None and end <= start:
        errors.append("production capture window window_end_utc must be after start")

    if payload.get("metric_names") != list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES):
        errors.append("production capture window metric_names mismatch")
    if payload.get("label_names") != list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES):
        errors.append("production capture window label_names mismatch")
    if payload.get("bucket_labels") != list(ROUTE_DEPTH_BUCKET_LABELS):
        errors.append("production capture window bucket_labels mismatch")

    sample_count = _positive_int(
        payload.get("sample_count"),
        "production capture window sample_count",
        errors,
    )
    route_depth_sum = _non_negative_int(
        payload.get("route_depth_sum"),
        "production capture window route_depth_sum",
        errors,
    )
    route_profile_count = _positive_int(
        payload.get("route_profile_count"),
        "production capture window route_profile_count",
        errors,
    )
    _validate_depth_sum_bounds(
        route_depth_sum,
        sample_count,
        "production capture window route_depth_sum",
        errors,
    )

    aggregate = payload.get("aggregate_cumulative_buckets")
    if not isinstance(aggregate, dict):
        errors.append(
            "production capture window aggregate_cumulative_buckets must be an object"
        )
        normalized_aggregate = {}
    else:
        normalized_aggregate = {
            label: aggregate.get(label) for label in ROUTE_DEPTH_BUCKET_LABELS
        }
        _validate_cumulative_buckets(
            aggregate,
            expected_sample_count=sample_count,
            errors=errors,
            prefix="production capture window aggregate_cumulative_buckets",
        )

    profiles_payload = payload.get("route_profiles")
    normalized_profiles: list[dict[str, Any]] = []
    recomputed_aggregate = {label: 0 for label in ROUTE_DEPTH_BUCKET_LABELS}
    total_samples = 0
    total_depth_sum = 0
    seen_profiles: set[str] = set()
    if not isinstance(profiles_payload, list):
        errors.append("production capture window route_profiles must be a list")
    else:
        if route_profile_count is not None and len(profiles_payload) != route_profile_count:
            errors.append("production capture window route_profile_count mismatch")
        for index, profile in enumerate(profiles_payload):
            normalized = _normalize_capture_profile(index, profile, errors)
            if normalized is None:
                continue
            route_profile = normalized["route_profile"]
            if route_profile in seen_profiles:
                errors.append(
                    "production capture window route_profiles route_profile "
                    "aliases must be unique"
                )
            seen_profiles.add(route_profile)
            normalized_profiles.append(normalized)
            total_samples += normalized["sample_count"]
            total_depth_sum += normalized["route_depth_sum"]
            for label in ROUTE_DEPTH_BUCKET_LABELS:
                value = normalized["cumulative_buckets"].get(label)
                if isinstance(value, int) and not isinstance(value, bool):
                    recomputed_aggregate[label] += value

    if sample_count is not None and total_samples != sample_count:
        errors.append("production capture window route_profiles sample_count mismatch")
    if route_depth_sum is not None and total_depth_sum != route_depth_sum:
        errors.append(
            "production capture window route_profiles route_depth_sum mismatch"
        )
    if isinstance(aggregate, dict) and recomputed_aggregate != normalized_aggregate:
        errors.append(
            "production capture window aggregate does not match route_profiles"
        )

    for field in (
        "raw_payload_included",
        "query_text_included",
        "local_paths_recorded",
    ):
        if payload.get(field) is not False:
            errors.append(f"production capture window {field} must be false")
    if payload.get("network_access") != "not_used":
        errors.append("production capture window network_access must be not_used")
    if payload.get("cloud_api_calls") != 0:
        errors.append("production capture window cloud_api_calls must be 0")

    if errors:
        raise ValueError("invalid production capture window: " + "; ".join(errors))

    normalized_window = {
        "schema_version": PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION,
        "capture_window_id": capture_window_id,
        "source_kind": source_kind,
        "operator_owned_export": True,
        "window_start_utc": _format_utc(start),
        "window_end_utc": _format_utc(end),
        "metric_names": list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES),
        "label_names": list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES),
        "bucket_labels": list(ROUTE_DEPTH_BUCKET_LABELS),
        "sample_count": sample_count,
        "route_depth_sum": route_depth_sum,
        "aggregate_cumulative_buckets": normalized_aggregate,
        "route_profile_count": route_profile_count,
        "route_profiles": normalized_profiles,
        "raw_payload_included": False,
        "query_text_included": False,
        "local_paths_recorded": False,
        "network_access": "not_used",
        "cloud_api_calls": 0,
    }
    digest = _canonical_digest(_capture_window_digest_source(normalized_window))
    supplied_digest = payload.get("window_digest_sha256")
    if supplied_digest is not None and supplied_digest != digest:
        raise ValueError("invalid production capture window: window_digest_sha256 mismatch")
    normalized_window["window_digest_sha256"] = digest
    return normalized_window


def _normalize_capture_profile(
    index: int,
    profile: Any,
    errors: list[str],
) -> dict[str, Any] | None:
    prefix = f"production capture window route_profiles[{index}]"
    if not isinstance(profile, dict):
        errors.append(f"{prefix} must be an object")
        return None
    route_profile = profile.get("route_profile")
    if not isinstance(route_profile, str) or not _is_stable_case_id(route_profile):
        errors.append(f"{prefix}.route_profile must be a stable lowercase alias")
    final_stage = profile.get("final_stage")
    if (
        not isinstance(final_stage, str)
        or final_stage not in set(ALLOWED_STAGES) | {"none"}
    ):
        errors.append(f"{prefix}.final_stage must be an allowed route stage")
    sample_count = _positive_int(
        profile.get("sample_count"),
        f"{prefix}.sample_count",
        errors,
    )
    route_depth_sum = _non_negative_int(
        profile.get("route_depth_sum"),
        f"{prefix}.route_depth_sum",
        errors,
    )
    _validate_depth_sum_bounds(
        route_depth_sum,
        sample_count,
        f"{prefix}.route_depth_sum",
        errors,
    )
    buckets = profile.get("cumulative_buckets")
    if not isinstance(buckets, dict):
        errors.append(f"{prefix}.cumulative_buckets must be an object")
        normalized_buckets = {}
    else:
        normalized_buckets = {
            label: buckets.get(label) for label in ROUTE_DEPTH_BUCKET_LABELS
        }
        _validate_cumulative_buckets(
            buckets,
            expected_sample_count=sample_count,
            errors=errors,
            prefix=f"{prefix}.cumulative_buckets",
        )
    if (
        not isinstance(route_profile, str)
        or not isinstance(final_stage, str)
        or sample_count is None
        or route_depth_sum is None
        or not isinstance(buckets, dict)
    ):
        return None
    return {
        "route_profile": route_profile,
        "final_stage": final_stage,
        "sample_count": sample_count,
        "route_depth_sum": route_depth_sum,
        "cumulative_buckets": normalized_buckets,
    }


def _cumulative_route_depth_buckets(route_depth: int) -> dict[str, int]:
    buckets: dict[str, int] = {}
    for label in ROUTE_DEPTH_BUCKET_LABELS:
        if label == "+Inf":
            buckets[label] = 1
        else:
            buckets[label] = 1 if route_depth <= int(label) else 0
    return buckets


def _sanitize_stage_names(trace: Any) -> list[str]:
    if not isinstance(trace, Sequence) or isinstance(trace, (str, bytes)):
        return []
    sanitizer_input = [event for event in trace if isinstance(event, dict)]
    sanitized_trace = _sanitize_route_stage_trace(sanitizer_input)
    stages: list[str] = []
    for event in sanitized_trace:
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
        if not is_finite_number(result.get(field)):
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


def _validate_production_route_depth_histogram_artifact(
    artifact: Mapping[str, Any],
    errors: list[str],
) -> None:
    if artifact.get("schema_version") != PRODUCTION_HISTOGRAM_SCHEMA_VERSION:
        errors.append("production histogram schema_version is not recognized")
    if (
        artifact.get("artifact_status")
        != "production_histogram_artifact_contract_available"
    ):
        errors.append("production histogram artifact_status is not recognized")
    for field in SAFE_FALSE_FIELDS:
        if artifact.get(field) is not False:
            errors.append(
                f"production histogram {field} must be exact false bool"
            )
    if artifact.get("production_runtime_data_attached") is not False:
        errors.append(
            "production histogram production_runtime_data_attached must be false"
        )
    if artifact.get("production_data_source") != "not_attached":
        errors.append("production histogram production_data_source must be not_attached")
    if artifact.get("network_access") != "not_used":
        errors.append("production histogram network_access must be not_used")
    if artifact.get("cloud_api_calls") != 0:
        errors.append("production histogram cloud_api_calls must be 0")
    if artifact.get("metric_names") != list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES):
        errors.append("production histogram metric_names mismatch")
    if artifact.get("label_names") != list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES):
        errors.append("production histogram label_names mismatch")
    if artifact.get("bucket_labels") != list(ROUTE_DEPTH_BUCKET_LABELS):
        errors.append("production histogram bucket_labels mismatch")
    sample_count = artifact.get("sample_count")
    if (
        not isinstance(sample_count, int)
        or isinstance(sample_count, bool)
        or sample_count < 0
    ):
        errors.append("production histogram sample_count must be a non-negative int")
    depth_sum = artifact.get("route_depth_sum")
    if (
        not isinstance(depth_sum, int)
        or isinstance(depth_sum, bool)
        or depth_sum < 0
    ):
        errors.append("production histogram route_depth_sum must be a non-negative int")
    profile_count = artifact.get("route_profile_count")
    if (
        not isinstance(profile_count, int)
        or isinstance(profile_count, bool)
        or profile_count < 0
    ):
        errors.append("production histogram route_profile_count must be a non-negative int")

    aggregate = artifact.get("aggregate_cumulative_buckets")
    if not isinstance(aggregate, dict):
        errors.append("production histogram aggregate_cumulative_buckets must be an object")
    else:
        _validate_cumulative_buckets(
            aggregate,
            expected_sample_count=sample_count if isinstance(sample_count, int) else None,
            errors=errors,
            prefix="production histogram aggregate_cumulative_buckets",
        )

    profiles = artifact.get("route_profiles")
    if not isinstance(profiles, list):
        errors.append("production histogram route_profiles must be a list")
    else:
        if isinstance(profile_count, int) and len(profiles) != profile_count:
            errors.append("production histogram route_profile_count mismatch")
        total_samples = 0
        total_depth_sum = 0
        recomputed_aggregate = {label: 0 for label in ROUTE_DEPTH_BUCKET_LABELS}
        for index, profile in enumerate(profiles):
            if not isinstance(profile, dict):
                errors.append(
                    f"production histogram route_profiles[{index}] must be an object"
                )
                continue
            route_profile = profile.get("route_profile")
            if (
                not isinstance(route_profile, str)
                or not _is_stable_case_id(route_profile)
            ):
                errors.append(
                    "production histogram route_profiles"
                    f"[{index}].route_profile must be a stable lowercase alias"
                )
            final_stage = profile.get("final_stage")
            if (
                not isinstance(final_stage, str)
                or final_stage not in set(ALLOWED_STAGES) | {"none"}
            ):
                errors.append(
                    "production histogram route_profiles"
                    f"[{index}].final_stage must be an allowed route stage"
                )
            profile_sample_count = profile.get("sample_count")
            if profile_sample_count != 1:
                errors.append(
                    f"production histogram route_profiles[{index}].sample_count must be 1"
                )
            observed_depth = profile.get("observed_route_depth")
            if (
                not isinstance(observed_depth, int)
                or isinstance(observed_depth, bool)
                or observed_depth < 0
            ):
                errors.append(
                    "production histogram route_profiles"
                    f"[{index}].observed_route_depth must be a non-negative int"
                )
            route_depth_sum = profile.get("route_depth_sum")
            if route_depth_sum != observed_depth:
                errors.append(
                    "production histogram route_profiles"
                    f"[{index}].route_depth_sum must match observed_route_depth"
                )
            buckets = profile.get("cumulative_buckets")
            if not isinstance(buckets, dict):
                errors.append(
                    "production histogram route_profiles"
                    f"[{index}].cumulative_buckets must be an object"
                )
            else:
                _validate_cumulative_buckets(
                    buckets,
                    expected_sample_count=1,
                    errors=errors,
                    prefix=f"production histogram route_profiles[{index}].cumulative_buckets",
                )
                for label in ROUTE_DEPTH_BUCKET_LABELS:
                    value = buckets.get(label)
                    if isinstance(value, int) and not isinstance(value, bool):
                        recomputed_aggregate[label] += value
            if isinstance(profile_sample_count, int) and not isinstance(
                profile_sample_count,
                bool,
            ):
                total_samples += profile_sample_count
            if isinstance(route_depth_sum, int) and not isinstance(route_depth_sum, bool):
                total_depth_sum += route_depth_sum
        if isinstance(sample_count, int) and total_samples != sample_count:
            errors.append("production histogram route_profiles sample_count mismatch")
        if isinstance(depth_sum, int) and total_depth_sum != depth_sum:
            errors.append("production histogram route_profiles route_depth_sum mismatch")
        if isinstance(aggregate, dict) and recomputed_aggregate != aggregate:
            errors.append("production histogram aggregate does not match route_profiles")

    digest = artifact.get("artifact_digest_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or not all(
        character in "0123456789abcdef" for character in digest
    ):
        errors.append("production histogram artifact_digest_sha256 must be lowercase sha256")
    else:
        digest_source = {
            "metric_names": artifact.get("metric_names"),
            "label_names": artifact.get("label_names"),
            "bucket_labels": artifact.get("bucket_labels"),
            "route_profiles": artifact.get("route_profiles"),
        }
        if _canonical_digest(digest_source) != digest:
            errors.append("production histogram artifact_digest_sha256 mismatch")


def _validate_production_capture_window_attachment(
    attachment: Mapping[str, Any],
    errors: list[str],
) -> None:
    if (
        attachment.get("schema_version")
        != PRODUCTION_CAPTURE_WINDOW_ATTACHMENT_SCHEMA_VERSION
    ):
        errors.append("production capture attachment schema_version is not recognized")
    if (
        attachment.get("capture_window_schema_version")
        != PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION
    ):
        errors.append(
            "production capture attachment capture_window_schema_version is not recognized"
        )
    status = attachment.get("attachment_status")
    if status not in {
        "capture_window_attachment_contract_available",
        "operator_capture_window_attached",
    }:
        errors.append("production capture attachment attachment_status is not recognized")
    for field in SAFE_FALSE_FIELDS:
        if attachment.get(field) is not False:
            errors.append(
                f"production capture attachment {field} must be exact false bool"
            )
    if attachment.get("network_access") != "not_used":
        errors.append("production capture attachment network_access must be not_used")
    if attachment.get("cloud_api_calls") != 0:
        errors.append("production capture attachment cloud_api_calls must be 0")
    if attachment.get("metric_names") != list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES):
        errors.append("production capture attachment metric_names mismatch")
    if attachment.get("label_names") != list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES):
        errors.append("production capture attachment label_names mismatch")
    if attachment.get("bucket_labels") != list(ROUTE_DEPTH_BUCKET_LABELS):
        errors.append("production capture attachment bucket_labels mismatch")
    if attachment.get("allowed_source_kinds") != list(ALLOWED_CAPTURE_SOURCE_KINDS):
        errors.append("production capture attachment allowed_source_kinds mismatch")

    capture_count = attachment.get("capture_window_count")
    if (
        not isinstance(capture_count, int)
        or isinstance(capture_count, bool)
        or capture_count < 0
    ):
        errors.append(
            "production capture attachment capture_window_count must be a non-negative int"
        )
    capture_windows = attachment.get("capture_windows")
    if not isinstance(capture_windows, list):
        errors.append("production capture attachment capture_windows must be a list")
        capture_windows = []
    if isinstance(capture_count, int) and len(capture_windows) != capture_count:
        errors.append("production capture attachment capture_window_count mismatch")

    attached = attachment.get("production_runtime_data_attached")
    source = attachment.get("production_data_source")
    if capture_windows:
        if attached is not True:
            errors.append(
                "production capture attachment production_runtime_data_attached "
                "must be true when capture_windows are attached"
            )
        if source not in ALLOWED_CAPTURE_SOURCE_KINDS:
            errors.append(
                "production capture attachment production_data_source is not recognized"
            )
        if status != "operator_capture_window_attached":
            errors.append(
                "production capture attachment attachment_status must mark attached window"
            )
    else:
        if attached is not False:
            errors.append(
                "production capture attachment production_runtime_data_attached "
                "must be false when no capture_windows are attached"
            )
        if source != "not_attached":
            errors.append(
                "production capture attachment production_data_source must be not_attached"
            )
        if status != "capture_window_attachment_contract_available":
            errors.append(
                "production capture attachment attachment_status must mark contract available"
            )

    for index, window in enumerate(capture_windows):
        if not isinstance(window, dict):
            errors.append(
                f"production capture attachment capture_windows[{index}] must be an object"
            )
            continue
        _validate_capture_window_record(index, window, errors)

    digest = attachment.get("attachment_digest_sha256")
    if not _is_lower_sha256(digest):
        errors.append(
            "production capture attachment attachment_digest_sha256 must be lowercase sha256"
        )
    else:
        digest_source = {
            "schema_version": attachment.get("schema_version"),
            "metric_names": attachment.get("metric_names"),
            "label_names": attachment.get("label_names"),
            "bucket_labels": attachment.get("bucket_labels"),
            "capture_windows": attachment.get("capture_windows"),
        }
        if _canonical_digest(digest_source) != digest:
            errors.append(
                "production capture attachment attachment_digest_sha256 mismatch"
            )


def _validate_capture_window_record(
    index: int,
    window: Mapping[str, Any],
    errors: list[str],
) -> None:
    prefix = f"production capture attachment capture_windows[{index}]"
    if window.get("schema_version") != PRODUCTION_CAPTURE_WINDOW_SCHEMA_VERSION:
        errors.append(f"{prefix}.schema_version is not recognized")
    window_id = window.get("capture_window_id")
    if not isinstance(window_id, str) or not _is_stable_case_id(window_id):
        errors.append(f"{prefix}.capture_window_id must be a stable lowercase alias")
    source_kind = window.get("source_kind")
    if source_kind not in ALLOWED_CAPTURE_SOURCE_KINDS:
        errors.append(f"{prefix}.source_kind is not recognized")
    if window.get("operator_owned_export") is not True:
        errors.append(f"{prefix}.operator_owned_export must be true")

    start = _parse_capture_timestamp(window.get("window_start_utc"), "start", errors)
    end = _parse_capture_timestamp(window.get("window_end_utc"), "end", errors)
    if start is not None and end is not None and end <= start:
        errors.append(f"{prefix}.window_end_utc must be after start")

    if window.get("metric_names") != list(ROUTE_DEPTH_HISTOGRAM_METRIC_NAMES):
        errors.append(f"{prefix}.metric_names mismatch")
    if window.get("label_names") != list(ROUTE_DEPTH_HISTOGRAM_LABEL_NAMES):
        errors.append(f"{prefix}.label_names mismatch")
    if window.get("bucket_labels") != list(ROUTE_DEPTH_BUCKET_LABELS):
        errors.append(f"{prefix}.bucket_labels mismatch")
    sample_count = _positive_int(window.get("sample_count"), f"{prefix}.sample_count", errors)
    route_depth_sum = _non_negative_int(
        window.get("route_depth_sum"),
        f"{prefix}.route_depth_sum",
        errors,
    )
    profile_count = _positive_int(
        window.get("route_profile_count"),
        f"{prefix}.route_profile_count",
        errors,
    )
    _validate_depth_sum_bounds(route_depth_sum, sample_count, f"{prefix}.route_depth_sum", errors)

    aggregate = window.get("aggregate_cumulative_buckets")
    if not isinstance(aggregate, dict):
        errors.append(f"{prefix}.aggregate_cumulative_buckets must be an object")
    else:
        _validate_cumulative_buckets(
            aggregate,
            expected_sample_count=sample_count,
            errors=errors,
            prefix=f"{prefix}.aggregate_cumulative_buckets",
        )

    profiles = window.get("route_profiles")
    if not isinstance(profiles, list):
        errors.append(f"{prefix}.route_profiles must be a list")
        profiles = []
    if profile_count is not None and len(profiles) != profile_count:
        errors.append(f"{prefix}.route_profile_count mismatch")
    total_samples = 0
    total_depth_sum = 0
    recomputed_aggregate = {label: 0 for label in ROUTE_DEPTH_BUCKET_LABELS}
    seen_profiles: set[str] = set()
    for profile_index, profile in enumerate(profiles):
        if not isinstance(profile, dict):
            errors.append(f"{prefix}.route_profiles[{profile_index}] must be an object")
            continue
        route_profile = profile.get("route_profile")
        if not isinstance(route_profile, str) or not _is_stable_case_id(route_profile):
            errors.append(
                f"{prefix}.route_profiles[{profile_index}].route_profile "
                "must be a stable lowercase alias"
            )
        elif route_profile in seen_profiles:
            errors.append(f"{prefix}.route_profiles route_profile aliases must be unique")
        else:
            seen_profiles.add(route_profile)
        final_stage = profile.get("final_stage")
        if (
            not isinstance(final_stage, str)
            or final_stage not in set(ALLOWED_STAGES) | {"none"}
        ):
            errors.append(
                f"{prefix}.route_profiles[{profile_index}].final_stage "
                "must be an allowed route stage"
            )
        profile_sample_count = _positive_int(
            profile.get("sample_count"),
            f"{prefix}.route_profiles[{profile_index}].sample_count",
            errors,
        )
        profile_depth_sum = _non_negative_int(
            profile.get("route_depth_sum"),
            f"{prefix}.route_profiles[{profile_index}].route_depth_sum",
            errors,
        )
        _validate_depth_sum_bounds(
            profile_depth_sum,
            profile_sample_count,
            f"{prefix}.route_profiles[{profile_index}].route_depth_sum",
            errors,
        )
        buckets = profile.get("cumulative_buckets")
        if not isinstance(buckets, dict):
            errors.append(
                f"{prefix}.route_profiles[{profile_index}].cumulative_buckets "
                "must be an object"
            )
        else:
            _validate_cumulative_buckets(
                buckets,
                expected_sample_count=profile_sample_count,
                errors=errors,
                prefix=f"{prefix}.route_profiles[{profile_index}].cumulative_buckets",
            )
            for label in ROUTE_DEPTH_BUCKET_LABELS:
                value = buckets.get(label)
                if isinstance(value, int) and not isinstance(value, bool):
                    recomputed_aggregate[label] += value
        if profile_sample_count is not None:
            total_samples += profile_sample_count
        if profile_depth_sum is not None:
            total_depth_sum += profile_depth_sum
    if sample_count is not None and total_samples != sample_count:
        errors.append(f"{prefix}.route_profiles sample_count mismatch")
    if route_depth_sum is not None and total_depth_sum != route_depth_sum:
        errors.append(f"{prefix}.route_profiles route_depth_sum mismatch")
    if isinstance(aggregate, dict) and recomputed_aggregate != aggregate:
        errors.append(f"{prefix}.aggregate does not match route_profiles")

    for field in (
        "raw_payload_included",
        "query_text_included",
        "local_paths_recorded",
    ):
        if window.get(field) is not False:
            errors.append(f"{prefix}.{field} must be false")
    if window.get("network_access") != "not_used":
        errors.append(f"{prefix}.network_access must be not_used")
    if window.get("cloud_api_calls") != 0:
        errors.append(f"{prefix}.cloud_api_calls must be 0")

    digest = window.get("window_digest_sha256")
    if not _is_lower_sha256(digest):
        errors.append(f"{prefix}.window_digest_sha256 must be lowercase sha256")
    else:
        if _canonical_digest(_capture_window_digest_source(window)) != digest:
            errors.append(f"{prefix}.window_digest_sha256 mismatch")


def _validate_cumulative_buckets(
    buckets: Mapping[str, Any],
    *,
    expected_sample_count: int | None,
    errors: list[str],
    prefix: str,
) -> None:
    if set(buckets) != set(ROUTE_DEPTH_BUCKET_LABELS):
        errors.append(f"{prefix} bucket labels mismatch")
        return
    previous = -1
    for label in ROUTE_DEPTH_BUCKET_LABELS:
        value = buckets.get(label)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            errors.append(f"{prefix}.{label} must be a non-negative int")
            continue
        if value < previous:
            errors.append(f"{prefix} must be monotonically non-decreasing")
        previous = value
    if expected_sample_count is not None and buckets.get("+Inf") != expected_sample_count:
        errors.append(f"{prefix}.+Inf must equal sample_count")


def _positive_int(value: Any, field_name: str, errors: list[str]) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{field_name} must be a positive int")
        return None
    return value


def _non_negative_int(value: Any, field_name: str, errors: list[str]) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append(f"{field_name} must be a non-negative int")
        return None
    return value


def _validate_depth_sum_bounds(
    route_depth_sum: int | None,
    sample_count: int | None,
    field_name: str,
    errors: list[str],
) -> None:
    if route_depth_sum is None or sample_count is None:
        return
    if route_depth_sum > sample_count * len(ALLOWED_STAGES):
        errors.append(f"{field_name} exceeds max route depth for sample_count")


def _parse_capture_timestamp(
    raw: Any,
    field_name: str,
    errors: list[str],
) -> datetime | None:
    if not isinstance(raw, str):
        errors.append(f"production capture window {field_name} must be a UTC Z string")
        return None
    if not raw.endswith("Z"):
        errors.append(f"production capture window {field_name} must use UTC Z")
        return None
    try:
        parsed = _parse_utc(raw)
    except ValueError:
        errors.append(f"production capture window {field_name} must be valid UTC")
        return None
    if _format_utc(parsed) != raw:
        errors.append(f"production capture window {field_name} must be canonical UTC")
        return None
    return parsed


def _validate_case_record(index: int, case: Any, errors: list[str]) -> None:
    if not isinstance(case, dict):
        errors.append(f"cases[{index}] must be an object")
        return
    case_id = case.get("case_id")
    if not isinstance(case_id, str) or not _is_stable_case_id(case_id):
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


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _capture_window_digest_source(window: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": window.get("schema_version"),
        "capture_window_id": window.get("capture_window_id"),
        "source_kind": window.get("source_kind"),
        "operator_owned_export": window.get("operator_owned_export"),
        "window_start_utc": window.get("window_start_utc"),
        "window_end_utc": window.get("window_end_utc"),
        "metric_names": window.get("metric_names"),
        "label_names": window.get("label_names"),
        "bucket_labels": window.get("bucket_labels"),
        "sample_count": window.get("sample_count"),
        "route_depth_sum": window.get("route_depth_sum"),
        "aggregate_cumulative_buckets": window.get("aggregate_cumulative_buckets"),
        "route_profile_count": window.get("route_profile_count"),
        "route_profiles": window.get("route_profiles"),
        "raw_payload_included": window.get("raw_payload_included"),
        "query_text_included": window.get("query_text_included"),
        "local_paths_recorded": window.get("local_paths_recorded"),
        "network_access": window.get("network_access"),
        "cloud_api_calls": window.get("cloud_api_calls"),
    }


def _is_lower_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_stable_case_id(value: str) -> bool:
    return (
        3 <= len(value) <= 81
        and value[0].islower()
        and all(character.islower() or character.isdigit() or character == "_" for character in value)
    )


if __name__ == "__main__":
    raise SystemExit(main())
