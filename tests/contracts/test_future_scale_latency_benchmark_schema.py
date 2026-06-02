"""Contract tests for the future-scale latency benchmark schema.

The slice intentionally adds only a schema, this executable contract, and docs.
The three repro fields use positive schema allowlists; runtime leak and finite
guards remain here as defense-in-depth until a later producer-harness PR can
share them from a common utility.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "schemas" / "future_scale_latency_benchmark.v1.json"

LEAK_PATTERNS = (
    re.compile(r"[A-Za-z]:[\\/]", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(
        r"(?:^|[\\/])(?:home|root|etc|var|opt|Users|tmp)(?:[\\/]|(?=$|\s|[\"'`;:,)\]]))",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:anthropic|claude|deepseek|gemini|gemma|google|gpt|hf|huggingface|llama|mistral|mixtral|ollama|openai|phi|poro|qwen)[A-Za-z0-9_.:/-]*\b",
        re.IGNORECASE,
    ),
)

GATE_FIELDS = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)


def _load_schema() -> dict[str, Any]:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    return schema


def _validator() -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(_load_schema())


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


def validate_latency_benchmark_artifact(artifact: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    try:
        _validator().validate(artifact)
    except jsonschema.ValidationError as exc:
        path = list(exc.absolute_path) or "$"
        errors.append(f"schema_validation: {exc.message} (path: {path})")

    for gate in GATE_FIELDS:
        if artifact.get(gate) is not False:
            errors.append(f"{gate} must be exact false bool")

    if artifact.get("measurement_scope") != "local":
        errors.append("measurement_scope must be 'local'")
    if artifact.get("no_cloud_api_calls") is not True:
        errors.append("no_cloud_api_calls must be true")
    if artifact.get("no_model_pull_or_download") is not True:
        errors.append("no_model_pull_or_download must be true")

    for path, value in _walk_scalars(artifact):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                errors.append(f"{path} contains a non-finite number")
        elif isinstance(value, str) and _looks_like_leak(value):
            errors.append(f"{path} contains a forbidden secret/path-like string")

    return errors


def _good_fixture() -> dict[str, Any]:
    return {
        "benchmark_version": "future_scale_latency.v1",
        "schema_version": "latency_benchmark.v1",
        "generated_at_utc": "2026-06-03T12:00:00Z",
        "git_sha": "deadbeef" * 5,
        "source_branch": "latency-slice-1",
        "measurement_scope": "local",
        "stage_aliases_used": ["language_detection", "hot_cache", "deterministic_solver", "hybrid_retrieval_8_cell"],
        "synthetic_fixtures_alias": "v3.latency_fixtures.local.v1",
        "fixture_case_count": 12,
        "fixtures_sha256": "b" * 64,
        "latency_metric_family": "waggledance_route_stage_request_latency_histogram_ms",
        "latency_bucket_metric": "waggledance_route_stage_request_latency_histogram_ms_bucket",
        "latency_sum_metric": "waggledance_route_stage_request_latency_histogram_ms_sum",
        "latency_count_metric": "waggledance_route_stage_request_latency_histogram_ms_count",
        "latency_observations": [
            {
                "run_id": "run-001",
                "stage_alias": "language_detection",
                "p50_ms": 1.2,
                "p95_ms": 2.8,
                "p99_ms": 3.1,
                "samples": 12,
                "finite": True,
                "delta_vs_baseline_p95": -0.3,
            },
            {
                "run_id": "run-002",
                "stage_alias": "deterministic_solver",
                "p50_ms": 4.5,
                "p95_ms": 7.2,
                "p99_ms": 8.9,
                "samples": 12,
                "finite": True,
            },
            {
                "run_id": "run-003",
                "stage_alias": "hybrid_retrieval_8_cell",
                "p50_ms": 11.0,
                "p95_ms": 14.7,
                "p99_ms": 18.2,
                "samples": 12,
                "finite": True,
            },
        ],
        "aggregate": {
            "mean_p95_ms": 8.23,
            "median_p95_ms": 7.2,
            "p95_of_p99_ms": 18.2,
            "p99_of_p95_ms": 14.7,
            "finite": True,
        },
        "internal_controls": {
            "positive_control_p95_ms": 6.5,
            "negative_control_p95_ms": 22.1,
            "control_delta_ms": 15.6,
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
        "deterministic_seed": "latency-bench-20260603-seed-a1b2c3",
        "reproduce_command": (
            "python tools/run_future_scale_latency_bench.py "
            "--fixtures v3.latency_fixtures.local.v1 --offline --deterministic"
        ),
        "not_claimed": [
            "No claim that measured latency predicts production performance at scale.",
            "No claim of future scaling safety or autonomous latency reduction.",
            "All measurements local and offline only.",
        ],
    }


def test_good_fixture_validates():
    assert validate_latency_benchmark_artifact(_good_fixture()) == []
    _validator().validate(_good_fixture())


@pytest.mark.parametrize(
    "gate",
    [
        "claim_gate_satisfied",
        "claim_safe",
        "literal_future_claim_safe",
        "controls_present",
        "runtime_authority_granted",
        "external_writes_applied",
        "required_runtime_evidence_present",
    ],
)
def test_rejects_claim_gate_upgrades(gate: str):
    fixture = _good_fixture()
    fixture[gate] = True

    with pytest.raises(jsonschema.ValidationError):
        _validator().validate(fixture)

    errors = validate_latency_benchmark_artifact(fixture)
    assert errors
    assert any(gate in error for error in errors)


@pytest.mark.parametrize(
    "field,value",
    [
        ("deterministic_seed", "sk-1234567890abcdefREALHFTOKENORKEY"),
        ("deterministic_seed", "AKIA1234567890ABCDEFEXTRA"),
        ("reproduce_command", r'python foo.py --ckpt "C:\Users\janik\secret\model.bin"'),
        ("reproduce_command", r"python foo.py --input C:\tmp\synth_adversarial_15case.json"),
        ("reproduce_command", "python foo.py --input /tmp/synth_adversarial_15case.json"),
        ("reproduce_command", "python foo.py --input /home/user/synth_adversarial_15case.json"),
        ("reproduce_command", "python foo.py --input ../synth_adversarial_15case.json"),
        ("reproduce_command", "python foo.py --input /mnt/data/synth_adversarial_15case.json"),
        ("reproduce_command", "python foo.py --input data/tmp/synth_adversarial_15case.json"),
        ("reproduce_command", 'python foo.py --token "Bearer abcdefghij1234567890"'),
        ("reproduce_command", 'python foo.py --token "bearer abcdefghij1234567890"'),
        ("source_branch", r"feature/C:\Users\evil\branch"),
        ("source_branch", r"feature\..\escape"),
        ("source_branch", "hf/meta-llama-model"),
        (
            "not_claimed",
            [
                "No claim that measured latency predicts production performance at scale.",
                "No claim of future scaling safety or autonomous latency reduction.",
                "../sneaky/parent/escape",
            ],
        ),
        (
            "not_claimed",
            [
                "No claim that measured latency predicts production performance at scale.",
                "No claim of future scaling safety or autonomous latency reduction.",
                "/mnt/data/synth_adversarial_15case.json",
            ],
        ),
        (
            "not_claimed",
            [
                "No claim that measured latency predicts production performance at scale.",
                "No claim of future scaling safety or autonomous latency reduction.",
                "C:tmp",
            ],
        ),
    ],
)
def test_rejects_free_text_secrets_and_paths(field: str, value: Any):
    fixture = _good_fixture()
    fixture[field] = value

    errors = validate_latency_benchmark_artifact(fixture)
    assert errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("deterministic_seed", "latency-bench-20260603-seed-a1b2c3-extra"),
        ("deterministic_seed", "latency-bench-20260603-seed-zzzzzz"),
        (
            "reproduce_command",
            "python tools/run_future_scale_latency_bench.py "
            "--fixtures v3.latency_fixtures.local.v1 --offline --deterministic --input ../x",
        ),
        (
            "reproduce_command",
            "python tools/run_future_scale_latency_bench.py "
            "--fixtures /mnt/data/x --offline --deterministic",
        ),
        ("source_branch", "feature/foo"),
        ("source_branch", "1latency-slice-1"),
        ("source_branch", r"latency\..\escape"),
    ],
)
def test_rejects_repro_field_allowlist_near_misses(field: str, value: str):
    fixture = _good_fixture()
    fixture[field] = value

    with pytest.raises(jsonschema.ValidationError):
        _validator().validate(fixture)

    errors = validate_latency_benchmark_artifact(fixture)
    assert any("schema_validation" in error for error in errors), errors


def test_recursive_leak_walk_remains_defense_in_depth_for_other_scalars():
    fixture = _good_fixture()
    fixture["not_claimed"].append("No raw path such as /tmp/synth_adversarial_15case.json.")

    errors = validate_latency_benchmark_artifact(fixture)
    assert any("forbidden secret/path-like" in error for error in errors), errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("stage_aliases_used", ["gpt-4o-secret"]),
        ("stage_aliases_used", ["openai"]),
        ("stage_aliases_used", ["anthropic"]),
        ("stage_aliases_used", ["google"]),
        ("stage_aliases_used", ["gemma4"]),
        ("stage_aliases_used", ["poro"]),
        ("stage_aliases_used", ["/models/ollama/phi3.gguf"]),
        ("synthetic_fixtures_alias", "/tmp/latency_fixtures_v3.json"),
    ],
)
def test_rejects_raw_identifiers_in_alias_fields(field: str, value: Any):
    fixture = _good_fixture()
    fixture[field] = value

    errors = validate_latency_benchmark_artifact(fixture)
    assert errors


def test_rejects_non_finite_scores_even_when_schema_accepts_number():
    fixture = _good_fixture()
    fixture["latency_observations"][0]["p95_ms"] = float("inf")
    fixture["aggregate"]["mean_p95_ms"] = float("nan")

    errors = validate_latency_benchmark_artifact(fixture)
    assert any("$.latency_observations[0].p95_ms contains a non-finite number" in error for error in errors)
    assert any("$.aggregate.mean_p95_ms contains a non-finite number" in error for error in errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.pop("claim_safe"),
        lambda f: f.__setitem__("measurement_scope", "cluster"),
        lambda f: f.__setitem__("secret_backdoor", "enabled"),
        lambda f: f["latency_observations"][0].__setitem__("p95_ms", "7.2"),
    ],
)
def test_rejects_malformed_wrong_scope_extra_properties_and_wrong_types(mutate):
    fixture = _good_fixture()
    mutate(fixture)

    with pytest.raises(jsonschema.ValidationError):
        _validator().validate(fixture)

    assert validate_latency_benchmark_artifact(fixture)


def test_rejects_type_confusion_on_gates():
    fixture = _good_fixture()
    fixture["claim_safe"] = "false"

    errors = validate_latency_benchmark_artifact(fixture)
    assert any("claim_safe must be exact false bool" in error for error in errors)


def test_json_serialization_disallows_nan_payloads():
    fixture = _good_fixture()
    fixture["aggregate"]["p95_of_p99_ms"] = float("nan")

    with pytest.raises(ValueError):
        json.dumps(fixture, allow_nan=False)

    errors = validate_latency_benchmark_artifact(fixture)
    assert any("$.aggregate.p95_of_p99_ms contains a non-finite number" in error for error in errors)
