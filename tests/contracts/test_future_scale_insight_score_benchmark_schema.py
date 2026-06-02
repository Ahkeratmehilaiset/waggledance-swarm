"""Contract tests for the future-scale insight_score benchmark schema.

The slice intentionally adds only a schema, this executable contract, and docs.
Runtime leak and finite guards live here until a later producer-harness PR can
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
SCHEMA_PATH = ROOT / "schemas" / "future_scale_insight_score_benchmark.v1.json"

LEAK_PATTERNS = (
    re.compile(r"[A-Za-z]:\\(?:Users|Python|Program Files)", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9_.-])/(?:home|root|etc|var|opt|Users|tmp)(?:/|(?=$|\s|[\"'`;:,)\]]))",
        re.IGNORECASE,
    ),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:claude|deepseek|gemini|gpt|hf|huggingface|llama|mistral|mixtral|ollama|phi|qwen)[A-Za-z0-9_.:/-]*\b",
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


def validate_insight_benchmark_artifact(artifact: dict[str, Any]) -> list[str]:
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
        "benchmark_version": "future_scale_insight_score.v1",
        "schema_version": "insight_score_benchmark.v1",
        "generated_at_utc": "2026-06-01T12:00:00Z",
        "git_sha": "deadbeef" * 5,
        "source_branch": "insight-slice-3",
        "measurement_scope": "local",
        "solver_aliases_used": ["math.solver.v1", "domain.apiary.beekeeper"],
        "corpus_alias": "v12.a3.synth_adversarial.v0",
        "corpus_case_count": 15,
        "corpus_sha256": "a" * 64,
        "insight_runs": [
            {
                "run_id": "run-001",
                "solver_alias": "math.solver.v1",
                "insight_score": 0.42,
                "cases_evaluated": 15,
                "finite": True,
                "delta_vs_baseline": 0.11,
            },
            {
                "run_id": "run-002",
                "solver_alias": "domain.apiary.beekeeper",
                "insight_score": 0.19,
                "cases_evaluated": 15,
                "finite": True,
            },
        ],
        "aggregate": {
            "mean_insight_score": 0.305,
            "median_insight_score": 0.305,
            "scale_trend_slope": 0.0027,
            "p95_insight": 0.51,
            "finite": True,
        },
        "internal_controls": {
            "positive_control_score": 0.87,
            "negative_control_score": -0.12,
            "control_delta": 0.99,
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
        "deterministic_seed": "insight-bench-20260601-seed-7f3a9c",
        "reproduce_command": (
            "python tools/run_future_scale_insight_bench.py "
            "--corpus v12.a3.synth_adversarial.v0 --offline --deterministic"
        ),
        "not_claimed": [
            "No claim that insight_score predicts production performance.",
            "No claim of future scaling safety or autonomous improvement.",
            "All measurements local and offline only.",
        ],
    }


def test_good_fixture_validates():
    assert validate_insight_benchmark_artifact(_good_fixture()) == []
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

    errors = validate_insight_benchmark_artifact(fixture)
    assert errors
    assert any(gate in error for error in errors)


@pytest.mark.parametrize(
    "field,value",
    [
        ("deterministic_seed", "sk-1234567890abcdefREALHFTOKENORKEY"),
        ("deterministic_seed", "AKIA1234567890ABCDEFEXTRA"),
        ("reproduce_command", r'python foo.py --ckpt "C:\Users\janik\secret\model.bin"'),
        ("reproduce_command", "python foo.py --input /tmp/synth_adversarial_15case.json"),
        ("reproduce_command", "python foo.py --input /home/user/synth_adversarial_15case.json"),
        ("reproduce_command", 'python foo.py --token "Bearer abcdefghij1234567890"'),
        ("reproduce_command", 'python foo.py --token "bearer abcdefghij1234567890"'),
        ("source_branch", r"feature/C:\Users\evil\branch"),
        ("source_branch", "hf/meta-llama-model"),
    ],
)
def test_rejects_free_text_secrets_and_paths(field: str, value: str):
    fixture = _good_fixture()
    fixture[field] = value

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("forbidden secret" in error for error in errors), errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("solver_aliases_used", ["gpt-4o-secret"]),
        ("solver_aliases_used", ["/models/ollama/phi3.gguf"]),
        ("corpus_alias", "/tmp/synth_adversarial_15case.json"),
    ],
)
def test_rejects_raw_identifiers_in_alias_fields(field: str, value: Any):
    fixture = _good_fixture()
    fixture[field] = value

    errors = validate_insight_benchmark_artifact(fixture)
    assert errors


def test_rejects_non_finite_scores_even_when_schema_accepts_number():
    fixture = _good_fixture()
    fixture["insight_runs"][0]["insight_score"] = float("inf")
    fixture["aggregate"]["mean_insight_score"] = float("nan")

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("$.insight_runs[0].insight_score contains a non-finite number" in error for error in errors)
    assert any("$.aggregate.mean_insight_score contains a non-finite number" in error for error in errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f.pop("claim_safe"),
        lambda f: f.__setitem__("measurement_scope", "cluster"),
        lambda f: f.__setitem__("secret_backdoor", "enabled"),
        lambda f: f["insight_runs"][0].__setitem__("insight_score", "0.42"),
    ],
)
def test_rejects_malformed_wrong_scope_extra_properties_and_wrong_types(mutate):
    fixture = _good_fixture()
    mutate(fixture)

    with pytest.raises(jsonschema.ValidationError):
        _validator().validate(fixture)

    assert validate_insight_benchmark_artifact(fixture)


def test_rejects_type_confusion_on_gates():
    fixture = _good_fixture()
    fixture["claim_safe"] = "false"

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("claim_safe must be exact false bool" in error for error in errors)


def test_json_serialization_disallows_nan_payloads():
    fixture = _good_fixture()
    fixture["aggregate"]["scale_trend_slope"] = float("nan")

    with pytest.raises(ValueError):
        json.dumps(fixture, allow_nan=False)

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("$.aggregate.scale_trend_slope contains a non-finite number" in error for error in errors)
