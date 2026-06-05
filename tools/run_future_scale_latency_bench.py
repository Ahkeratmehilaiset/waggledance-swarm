#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Emit a local benchmark artifact for the future-scale latency axis.

The latency contract was introduced before this producer. This tool stays
inside that contract: deterministic local fixtures only, no network, no model
pulls, no runtime authority, and no claim that the numbers are production
latency evidence.
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
from typing import Any, Mapping, Sequence

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.future_scale_contract_safety import (  # noqa: E402
    validate_exact_false_fields,
    validate_scalar_safety,
)

BENCHMARK_VERSION = "future_scale_latency.v1"
SCHEMA_VERSION = "latency_benchmark.v1"
DEFAULT_FIXTURES_ALIAS = "v3.latency_fixtures.local.v1"
JSON_ARTIFACT_NAME = "future_scale_latency_benchmark.json"
MARKDOWN_ARTIFACT_NAME = "future_scale_latency_benchmark.md"
SCHEMA_PATH = ROOT / "schemas" / "future_scale_latency_benchmark.v1.json"
LATENCY_METRIC_FAMILY = "waggledance_route_stage_request_latency_histogram_ms"
LATENCY_BUCKET_METRIC = "waggledance_route_stage_request_latency_histogram_ms_bucket"
LATENCY_SUM_METRIC = "waggledance_route_stage_request_latency_histogram_ms_sum"
LATENCY_COUNT_METRIC = "waggledance_route_stage_request_latency_histogram_ms_count"
SAFE_FALSE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)
NOT_CLAIMED = (
    "No claim that measured latency predicts production performance at scale.",
    "No claim of future scaling safety or autonomous latency reduction.",
    "All measurements local and offline only.",
)
DEFAULT_STAGE_LATENCY_SAMPLES_MS: dict[str, tuple[float, ...]] = {
    "language_detection": (1.0, 1.2, 1.4, 1.6, 1.8),
    "hot_cache": (0.2, 0.3, 0.4, 0.5, 0.6),
    "deterministic_solver": (4.0, 5.0, 6.0, 7.0, 8.0),
    "hybrid_retrieval_8_cell": (9.0, 11.0, 13.0, 15.0, 17.0),
}
_SAFE_BRANCH_CHARS = re.compile(r"[^a-z0-9._-]+")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic local benchmark artifact for the "
            "future-scale latency axis."
        ),
    )
    parser.add_argument(
        "--fixtures",
        default=DEFAULT_FIXTURES_ALIAS,
        help="Stable synthetic fixtures alias. No paths or raw fixture IDs.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Required safety flag: do not call network services.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Required safety flag: use deterministic fixture latencies.",
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
        help="Optional UTC timestamp override, e.g. 2026-06-04T20:15:00Z.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.offline or not args.deterministic:
        print(
            "future scale latency benchmark requires --offline --deterministic",
            file=sys.stderr,
        )
        return 2

    try:
        report = build_future_scale_latency_benchmark(
            fixtures_alias=args.fixtures,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"future scale latency benchmark FAILED: {exc}", file=sys.stderr)
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
    return 0


def build_future_scale_latency_benchmark(
    *,
    fixtures_alias: str = DEFAULT_FIXTURES_ALIAS,
    stage_latency_samples_ms: Mapping[str, Sequence[float]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    samples = _normalize_stage_samples(
        stage_latency_samples_ms or DEFAULT_STAGE_LATENCY_SAMPLES_MS
    )
    observations = _build_latency_observations(samples)
    p95_values = [float(item["p95_ms"]) for item in observations]
    p99_values = [float(item["p99_ms"]) for item in observations]
    fixture_case_count = sum(len(values) for values in samples.values())
    artifact: dict[str, Any] = {
        "benchmark_version": BENCHMARK_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "git_sha": _git_text("rev-parse", "HEAD"),
        "source_branch": _source_branch_alias(),
        "measurement_scope": "local",
        "stage_aliases_used": list(samples),
        "synthetic_fixtures_alias": fixtures_alias,
        "fixture_case_count": fixture_case_count,
        "fixtures_sha256": _canonical_digest(
            {
                "fixtures_alias": fixtures_alias,
                "stage_latency_samples_ms": samples,
            }
        ),
        "latency_metric_family": LATENCY_METRIC_FAMILY,
        "latency_bucket_metric": LATENCY_BUCKET_METRIC,
        "latency_sum_metric": LATENCY_SUM_METRIC,
        "latency_count_metric": LATENCY_COUNT_METRIC,
        "latency_observations": observations,
        "aggregate": {
            "mean_p95_ms": _round(_mean(p95_values)),
            "median_p95_ms": _round(_median(p95_values)),
            "p95_of_p99_ms": _round(_percentile(p99_values, 0.95)),
            "p99_of_p95_ms": _round(_percentile(p95_values, 0.99)),
            "finite": True,
        },
        "internal_controls": {
            "positive_control_p95_ms": 3.0,
            "negative_control_p95_ms": 18.0,
            "control_delta_ms": 15.0,
            "controls_measured": True,
        },
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "required_runtime_evidence_present": False,
        "no_cloud_api_calls": True,
        "no_model_pull_or_download": True,
        "deterministic_seed": (
            f"latency-bench-{generated_at.strftime('%Y%m%d')}-"
            f"seed-{_git_text('rev-parse', '--short=8', 'HEAD')}"
        ),
        "reproduce_command": (
            "python tools/run_future_scale_latency_bench.py "
            f"--fixtures {fixtures_alias} --offline --deterministic"
        ),
        "not_claimed": list(NOT_CLAIMED),
    }
    errors = validate_latency_benchmark_report(artifact)
    if errors:
        raise ValueError("; ".join(errors))
    return artifact


def validate_latency_benchmark_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        jsonschema.Draft202012Validator(_load_schema()).validate(report)
    except jsonschema.ValidationError as exc:
        path = list(exc.absolute_path) or "$"
        errors.append(f"schema_validation: {exc.message} (path: {path})")

    errors.extend(validate_exact_false_fields(report, SAFE_FALSE_FIELDS))
    if report.get("measurement_scope") != "local":
        errors.append("measurement_scope must be local")
    if report.get("no_cloud_api_calls") is not True:
        errors.append("no_cloud_api_calls must be true")
    if report.get("no_model_pull_or_download") is not True:
        errors.append("no_model_pull_or_download must be true")

    observations = report.get("latency_observations")
    if not isinstance(observations, list) or not observations:
        errors.append("latency_observations must be a non-empty list")
    else:
        p95_values: list[float] = []
        p99_values: list[float] = []
        for index, observation in enumerate(observations):
            if not isinstance(observation, dict):
                errors.append(f"latency_observations[{index}] must be an object")
                continue
            for field in ("p50_ms", "p95_ms", "p99_ms"):
                value = observation.get(field)
                if not _is_finite_number(value):
                    errors.append(
                        f"latency_observations[{index}].{field} must be finite"
                    )
                elif float(value) < 0:
                    errors.append(
                        f"latency_observations[{index}].{field} must be non-negative"
                    )
            if _is_finite_number(observation.get("p95_ms")):
                p95_values.append(float(observation["p95_ms"]))
            if _is_finite_number(observation.get("p99_ms")):
                p99_values.append(float(observation["p99_ms"]))
            samples = observation.get("samples")
            if (
                not isinstance(samples, int)
                or isinstance(samples, bool)
                or samples <= 0
            ):
                errors.append(
                    f"latency_observations[{index}].samples must be a positive int"
                )
            if observation.get("finite") is not True:
                errors.append(f"latency_observations[{index}].finite must be true")

        aggregate = report.get("aggregate")
        if not isinstance(aggregate, dict):
            errors.append("aggregate must be an object")
        elif p95_values and p99_values:
            expected = {
                "mean_p95_ms": _round(_mean(p95_values)),
                "median_p95_ms": _round(_median(p95_values)),
                "p95_of_p99_ms": _round(_percentile(p99_values, 0.95)),
                "p99_of_p95_ms": _round(_percentile(p95_values, 0.99)),
            }
            for field, expected_value in expected.items():
                actual = aggregate.get(field)
                if (
                    not _is_finite_number(actual)
                    or abs(float(actual) - expected_value) > 0.000001
                ):
                    errors.append(
                        f"aggregate.{field} does not match latency_observations"
                    )
            if aggregate.get("finite") is not True:
                errors.append("aggregate.finite must be true")

    controls = report.get("internal_controls")
    if not isinstance(controls, dict):
        errors.append("internal_controls must be an object")
    else:
        positive = controls.get("positive_control_p95_ms")
        negative = controls.get("negative_control_p95_ms")
        delta = controls.get("control_delta_ms")
        if not all(_is_finite_number(value) for value in (positive, negative, delta)):
            errors.append("internal_controls latency values must be finite")
        elif abs(float(negative) - float(positive) - float(delta)) > 0.000001:
            errors.append("internal_controls.control_delta_ms must match controls")
        if controls.get("controls_measured") is not True:
            errors.append("internal_controls.controls_measured must be true")

    errors.extend(validate_scalar_safety(report))
    try:
        json.dumps(report, allow_nan=False)
    except ValueError as exc:
        errors.append(f"json serialization failed: {exc}")
    return errors


def render_markdown(report: Mapping[str, Any]) -> str:
    aggregate = report["aggregate"]
    lines = [
        "# Future Scale Latency Benchmark",
        "",
        f"- benchmark_version: `{report['benchmark_version']}`",
        f"- schema_version: `{report['schema_version']}`",
        f"- measurement_scope: `{report['measurement_scope']}`",
        f"- synthetic_fixtures_alias: `{report['synthetic_fixtures_alias']}`",
        f"- fixture_case_count: `{report['fixture_case_count']}`",
        f"- mean_p95_ms: `{aggregate['mean_p95_ms']}`",
        f"- median_p95_ms: `{aggregate['median_p95_ms']}`",
        f"- p95_of_p99_ms: `{aggregate['p95_of_p99_ms']}`",
        "",
        "This is a local deterministic latency fixture artifact, not "
        "production evidence and not a future-scaling claim.",
        "",
        "Stage observations:",
    ]
    for observation in report["latency_observations"]:
        lines.append(
            "- "
            f"{observation['stage_alias']}: p50 `{observation['p50_ms']}` ms, "
            f"p95 `{observation['p95_ms']}` ms, "
            f"p99 `{observation['p99_ms']}` ms, "
            f"samples `{observation['samples']}`"
        )
    lines.extend(["", "Claim gates remain false:"])
    for field in SAFE_FALSE_FIELDS:
        lines.append(f"- {field}: `{report[field]}`")
    return "\n".join(lines) + "\n"


def _build_latency_observations(
    samples_by_stage: Mapping[str, Sequence[float]],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for index, (stage_alias, samples) in enumerate(samples_by_stage.items(), start=1):
        values = [float(value) for value in samples]
        observations.append(
            {
                "run_id": f"run-{index:03d}",
                "stage_alias": stage_alias,
                "p50_ms": _round(_percentile(values, 0.50)),
                "p95_ms": _round(_percentile(values, 0.95)),
                "p99_ms": _round(_percentile(values, 0.99)),
                "samples": len(values),
                "finite": True,
                "delta_vs_baseline_p95": None,
            }
        )
    return observations


def _normalize_stage_samples(
    samples_by_stage: Mapping[str, Sequence[float]],
) -> dict[str, tuple[float, ...]]:
    if not samples_by_stage:
        raise ValueError("stage_latency_samples_ms must be non-empty")
    normalized: dict[str, tuple[float, ...]] = {}
    for stage_alias, samples in samples_by_stage.items():
        if not stage_alias:
            raise ValueError("stage alias must be non-empty")
        values = tuple(float(value) for value in samples)
        if not values:
            raise ValueError(f"{stage_alias} must include at least one sample")
        for value in values:
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{stage_alias} samples must be finite non-negative")
        normalized[str(stage_alias)] = values
    return normalized


def _load_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _format_utc(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise ValueError("--now requires a UTC timestamp ending in Z")
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("--now must match YYYY-MM-DDTHH:MM:SSZ") from exc


def _git_text(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        if args[:2] == ("rev-parse", "--short=8"):
            return "000000"
        if args[:2] == ("rev-parse", "HEAD"):
            return "0" * 40
        return "unknown"


def _source_branch_alias() -> str:
    branch = _git_text("branch", "--show-current").lower()
    alias = _SAFE_BRANCH_CHARS.sub("-", branch).strip(".-_")
    if not alias or not alias[0].isalpha():
        alias = f"branch-{alias}" if alias else "branch-unknown"
    return alias[:80]


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _round(value: float) -> float:
    if not math.isfinite(float(value)):
        raise ValueError("value must be finite")
    return round(float(value), 6)


def _mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("values must be non-empty")
    return sum(float(value) for value in values) / len(values)


def _median(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("values must be non-empty")
    ordered = sorted(float(value) for value in values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        raise ValueError("values must be non-empty")
    if not 0 <= quantile <= 1:
        raise ValueError("quantile must be between 0 and 1")
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


if __name__ == "__main__":
    raise SystemExit(main())
