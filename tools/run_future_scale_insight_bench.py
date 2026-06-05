#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Emit a local benchmark artifact for the future-scale insight_score axis.

The axis is defined in docs/architecture/HONEYCOMB_SOLVER_SCALING.md as the
dream-mode projected value of a candidate trajectory. This producer uses the
existing dream-mode ``compute_insight_score`` helper over deterministic local
synthetic outcomes. It is not a production baseline and never grants runtime
authority.
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
from waggledance.core.domain.autonomy import (  # noqa: E402
    CaseTrajectory,
    QualityGrade,
)
from waggledance.core.learning.dream_mode import (  # noqa: E402
    DreamSession,
    compute_insight_score,
)

BENCHMARK_VERSION = "future_scale_insight_score.v1"
SCHEMA_VERSION = "insight_score_benchmark.v1"
AXIS_ID = "insight_score"
DEFAULT_CORPUS_ALIAS = "v12.a3.synth_adversarial.v0"
JSON_ARTIFACT_NAME = "future_scale_insight_score_benchmark.json"
MARKDOWN_ARTIFACT_NAME = "future_scale_insight_score_benchmark.md"
SCHEMA_PATH = ROOT / "schemas" / "future_scale_insight_score_benchmark.v1.json"
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
    "No claim that insight_score predicts production performance.",
    "No claim of future scaling safety or autonomous improvement.",
    "All measurements local and offline only.",
)
SOURCE_PATHS = (
    "waggledance/core/learning/dream_mode.py",
    "schemas/future_scale_insight_score_benchmark.v1.json",
    "tests/contracts/test_future_scale_insight_score_benchmark_schema.py",
    "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
)
DEFAULT_RUN_FIXTURES: tuple[dict[str, Any], ...] = (
    {
        "run_id": "run-001",
        "solver_alias": "dream.math.v1",
        "outcomes": ("success", "success", "failure", "inconclusive"),
        "delta_vs_baseline": 0.25,
    },
    {
        "run_id": "run-002",
        "solver_alias": "dream.routing.v1",
        "outcomes": ("success", "success", "success", "failure"),
        "delta_vs_baseline": 0.5,
    },
    {
        "run_id": "run-003",
        "solver_alias": "dream.safety.v1",
        "outcomes": ("success", "inconclusive", "failure", "failure"),
        "delta_vs_baseline": -0.25,
    },
)
_SAFE_BRANCH_CHARS = re.compile(r"[^a-z0-9._-]+")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a deterministic local benchmark artifact for the "
            "future-scale insight_score axis."
        ),
    )
    parser.add_argument(
        "--corpus",
        default=DEFAULT_CORPUS_ALIAS,
        help="Stable corpus alias. No paths or raw corpus IDs.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Required safety flag: do not call network services.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Required safety flag: use deterministic fixture outcomes.",
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
        help="Optional UTC timestamp override, e.g. 2026-06-03T15:30:00Z.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.offline or not args.deterministic:
        print(
            "future scale insight_score benchmark requires --offline --deterministic",
            file=sys.stderr,
        )
        return 2

    try:
        report = build_future_scale_insight_benchmark(
            corpus_alias=args.corpus,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except ValueError as exc:
        print(f"future scale insight_score benchmark FAILED: {exc}", file=sys.stderr)
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


def build_future_scale_insight_benchmark(
    *,
    corpus_alias: str = DEFAULT_CORPUS_ALIAS,
    run_fixtures: Sequence[Mapping[str, Any]] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    fixtures = tuple(run_fixtures or DEFAULT_RUN_FIXTURES)
    if not fixtures:
        raise ValueError("run_fixtures must be non-empty")

    runs = [_build_run_record(fixture) for fixture in fixtures]
    scores = [run["insight_score"] for run in runs]
    case_count = sum(run["cases_evaluated"] for run in runs)
    artifact: dict[str, Any] = {
        "benchmark_version": BENCHMARK_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _format_utc(generated_at),
        "git_sha": _git_text("rev-parse", "HEAD"),
        "source_branch": _source_branch_alias(),
        "measurement_scope": "local",
        "solver_aliases_used": [run["solver_alias"] for run in runs],
        "corpus_alias": corpus_alias,
        "corpus_case_count": case_count,
        "corpus_sha256": _canonical_digest(
            {
                "corpus_alias": corpus_alias,
                "run_fixtures": [
                    {
                        "run_id": str(fixture.get("run_id", "")),
                        "solver_alias": str(fixture.get("solver_alias", "")),
                        "outcomes": list(fixture.get("outcomes", ())),
                    }
                    for fixture in fixtures
                ],
            }
        ),
        "insight_runs": runs,
        "aggregate": {
            "mean_insight_score": _round(_mean(scores)),
            "median_insight_score": _round(_median(scores)),
            "scale_trend_slope": _round(_trend_slope(scores)),
            "p95_insight": _round(_percentile(scores, 0.95)),
            "finite": True,
        },
        "internal_controls": {
            "positive_control_score": _score_for_outcomes(
                ("success", "success", "success")
            ),
            "negative_control_score": _score_for_outcomes(
                ("failure", "failure", "failure")
            ),
            "control_delta": 2.0,
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
            f"insight-bench-{generated_at.strftime('%Y%m%d')}-"
            f"seed-{_git_text('rev-parse', '--short=8', 'HEAD')}"
        ),
        "reproduce_command": (
            "python tools/run_future_scale_insight_bench.py "
            f"--corpus {corpus_alias} --offline --deterministic"
        ),
        "not_claimed": list(NOT_CLAIMED),
    }
    errors = validate_insight_benchmark_report(artifact)
    if errors:
        raise ValueError("; ".join(errors))
    return artifact


def validate_insight_benchmark_report(report: dict[str, Any]) -> list[str]:
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

    runs = report.get("insight_runs")
    if not isinstance(runs, list) or not runs:
        errors.append("insight_runs must be a non-empty list")
    else:
        scores = []
        for index, run in enumerate(runs):
            if not isinstance(run, dict):
                errors.append(f"insight_runs[{index}] must be an object")
                continue
            score = run.get("insight_score")
            if not _is_finite_number(score):
                errors.append(f"insight_runs[{index}].insight_score must be finite")
            else:
                scores.append(float(score))
            if run.get("finite") is not True:
                errors.append(f"insight_runs[{index}].finite must be true")
            cases = run.get("cases_evaluated")
            if not isinstance(cases, int) or isinstance(cases, bool) or cases <= 0:
                errors.append(
                    f"insight_runs[{index}].cases_evaluated must be a positive int"
                )

        aggregate = report.get("aggregate")
        if not isinstance(aggregate, dict):
            errors.append("aggregate must be an object")
        elif scores:
            expected = {
                "mean_insight_score": _round(_mean(scores)),
                "median_insight_score": _round(_median(scores)),
                "scale_trend_slope": _round(_trend_slope(scores)),
                "p95_insight": _round(_percentile(scores, 0.95)),
            }
            for field, value in expected.items():
                actual = aggregate.get(field)
                if (
                    not _is_finite_number(actual)
                    or abs(float(actual) - value) > 0.000001
                ):
                    errors.append(f"aggregate.{field} does not match insight_runs")
            if aggregate.get("finite") is not True:
                errors.append("aggregate.finite must be true")

    controls = report.get("internal_controls")
    if not isinstance(controls, dict):
        errors.append("internal_controls must be an object")
    else:
        pos = controls.get("positive_control_score")
        neg = controls.get("negative_control_score")
        delta = controls.get("control_delta")
        if not all(_is_finite_number(value) for value in (pos, neg, delta)):
            errors.append("internal_controls scores must be finite")
        elif abs(float(pos) - float(neg) - float(delta)) > 0.000001:
            errors.append("internal_controls.control_delta must match controls")
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
        "# Future Scale Insight Score Benchmark",
        "",
        f"- benchmark_version: `{report['benchmark_version']}`",
        f"- schema_version: `{report['schema_version']}`",
        f"- measurement_scope: `{report['measurement_scope']}`",
        f"- corpus_alias: `{report['corpus_alias']}`",
        f"- corpus_case_count: `{report['corpus_case_count']}`",
        f"- mean_insight_score: `{aggregate['mean_insight_score']}`",
        f"- median_insight_score: `{aggregate['median_insight_score']}`",
        f"- p95_insight: `{aggregate['p95_insight']}`",
        "",
        "This is a local deterministic benchmark artifact, not production "
        "evidence and not a future-scaling claim.",
        "",
        "Claim gates remain false:",
    ]
    for field in SAFE_FALSE_FIELDS:
        lines.append(f"- {field}: `{report[field]}`")
    return "\n".join(lines) + "\n"


def _build_run_record(fixture: Mapping[str, Any]) -> dict[str, Any]:
    run_id = str(fixture.get("run_id", ""))
    solver_alias = str(fixture.get("solver_alias", ""))
    outcomes = tuple(str(item) for item in fixture.get("outcomes", ()))
    if not run_id or not solver_alias or not outcomes:
        raise ValueError("each run fixture requires run_id, solver_alias, and outcomes")
    score = _score_for_outcomes(outcomes)
    record: dict[str, Any] = {
        "run_id": run_id,
        "solver_alias": solver_alias,
        "insight_score": score,
        "cases_evaluated": len(outcomes),
        "finite": True,
    }
    if "delta_vs_baseline" in fixture:
        delta = fixture["delta_vs_baseline"]
        if delta is not None and not _is_finite_number(delta):
            raise ValueError("delta_vs_baseline must be finite or null")
        record["delta_vs_baseline"] = None if delta is None else _round(float(delta))
    return record


def _score_for_outcomes(outcomes: Sequence[str]) -> float:
    session = DreamSession(simulations_run=len(outcomes))
    session.simulated_trajectories = [
        CaseTrajectory(
            quality_grade=QualityGrade.BRONZE,
            trajectory_origin="simulated",
            synthetic=True,
            verifier_result={"outcome": outcome, "simulated": True},
        )
        for outcome in outcomes
    ]
    return _round(compute_insight_score(session))


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
        raise ValueError("--now must use Zulu UTC format")
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
    alias = alias[:80]
    if validate_scalar_safety({"source_branch": alias}):
        return "branch-redacted"
    return alias


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


def _trend_slope(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    xs = [float(index + 1) for index in range(len(values))]
    ys = [float(value) for value in values]
    x_mean = _mean(xs)
    y_mean = _mean(ys)
    denominator = sum((x - x_mean) ** 2 for x in xs)
    if denominator == 0:
        return 0.0
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    return numerator / denominator


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
