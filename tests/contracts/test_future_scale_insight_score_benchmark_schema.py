"""Contract tests for the future-scale insight_score benchmark schema.

The slice intentionally adds only a schema, this executable contract, and docs.
The three repro fields use positive schema allowlists. Runtime leak and finite
guards are shared through tools.future_scale_contract_safety so future producer
harnesses and tests use the same policy.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.future_scale_contract_safety import (  # noqa: E402
    validate_exact_false_fields,
    validate_scalar_safety,
)

SCHEMA_PATH = ROOT / "schemas" / "future_scale_insight_score_benchmark.v1.json"

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


def validate_insight_benchmark_artifact(artifact: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    try:
        _validator().validate(artifact)
    except jsonschema.ValidationError as exc:
        path = list(exc.absolute_path) or "$"
        errors.append(f"schema_validation: {exc.message} (path: {path})")

    errors.extend(validate_exact_false_fields(artifact, GATE_FIELDS))

    if artifact.get("measurement_scope") != "local":
        errors.append("measurement_scope must be 'local'")
    if artifact.get("no_cloud_api_calls") is not True:
        errors.append("no_cloud_api_calls must be true")
    if artifact.get("no_model_pull_or_download") is not True:
        errors.append("no_model_pull_or_download must be true")

    errors.extend(validate_scalar_safety(artifact))
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
                "No claim that insight_score predicts production performance.",
                "No claim of future scaling safety or autonomous improvement.",
                "../sneaky/parent/escape",
            ],
        ),
        (
            "not_claimed",
            [
                "No claim that insight_score predicts production performance.",
                "No claim of future scaling safety or autonomous improvement.",
                "/mnt/data/synth_adversarial_15case.json",
            ],
        ),
        (
            "not_claimed",
            [
                "No claim that insight_score predicts production performance.",
                "No claim of future scaling safety or autonomous improvement.",
                "C:tmp",
            ],
        ),
    ],
)
def test_rejects_free_text_secrets_and_paths(field: str, value: Any):
    fixture = _good_fixture()
    fixture[field] = value

    errors = validate_insight_benchmark_artifact(fixture)
    assert errors


@pytest.mark.parametrize(
    "field,value",
    [
        ("deterministic_seed", "insight-bench-20260601-seed-7f3a9c-extra"),
        ("deterministic_seed", "insight-bench-20260601-seed-zzzzzz"),
        (
            "reproduce_command",
            "python tools/run_future_scale_insight_bench.py "
            "--corpus v12.a3.synth_adversarial.v0 --offline --deterministic --input ../x",
        ),
        (
            "reproduce_command",
            "python tools/run_future_scale_insight_bench.py "
            "--corpus /mnt/data/x --offline --deterministic",
        ),
        ("source_branch", "feature/foo"),
        ("source_branch", "1insight-slice-3"),
        ("source_branch", r"insight\..\escape"),
    ],
)
def test_rejects_repro_field_allowlist_near_misses(field: str, value: str):
    fixture = _good_fixture()
    fixture[field] = value

    with pytest.raises(jsonschema.ValidationError):
        _validator().validate(fixture)

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("schema_validation" in error for error in errors), errors


def test_recursive_leak_walk_remains_defense_in_depth_for_other_scalars():
    fixture = _good_fixture()
    fixture["not_claimed"].append("No raw path such as /tmp/synth_adversarial_15case.json.")

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("forbidden secret/path-like" in error for error in errors), errors


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


@pytest.mark.parametrize(
    "value",
    [
        "supergrok2026",
        "xxllama",
        "mygpt4o",
        "safe_gpt4o_hit",
    ],
)
def test_rejects_glued_provider_aliases_in_run_id(value: str):
    fixture = _good_fixture()
    fixture["insight_runs"][0]["run_id"] = value

    errors = validate_insight_benchmark_artifact(fixture)
    assert any("forbidden secret/path-like string" in error for error in errors), errors


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
