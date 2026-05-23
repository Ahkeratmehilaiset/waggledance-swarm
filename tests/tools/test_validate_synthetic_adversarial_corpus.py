from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "v3_13_0" / "synthetic_adversarial_case.v0.json"
EXPECTATION_SCHEMA = ROOT / "schemas" / "v3_13_0" / "synthetic_adversarial_expectation.v0.json"
CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
EXPECTATIONS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0_expectations.json"
EXPANSION = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23.json"
)
EXPANSION_EXPECTATIONS = (
    ROOT
    / "tests"
    / "fixtures"
    / "magma_adversarial_corpus"
    / "v0_expansion_2026_05_23_expectations.json"
)
SCRIPT = ROOT / "tools" / "validate_synthetic_adversarial_corpus.py"
REQUIRED_DEFECT_TYPES = {
    "charter_violation",
    "risk_escalation",
    "subtle_drift",
    "privilege_leak",
    "payload_leak",
    "correlated_review_trap",
}


def _load_corpus(path: Path = CORPUS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_expectations(path: Path = EXPECTATIONS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(path), "--expectations", str(EXPECTATIONS)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_fixture_cases_validate_against_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    validator = jsonschema.Draft7Validator(schema)

    corpus = _load_corpus()
    assert corpus["corpus_version"] == "magma.synthetic_adversarial_corpus.v0"
    assert len(corpus["cases"]) >= 38
    for case in corpus["cases"]:
        validator.validate(case)


def test_fixture_expectations_validate_against_schema() -> None:
    schema = json.loads(EXPECTATION_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    validator = jsonschema.Draft7Validator(schema)

    expectations = _load_expectations()
    assert expectations["expectations_version"] == "magma.synthetic_adversarial_expectations.v0"
    for expectation in expectations["expectations"]:
        validator.validate(expectation)


def test_fixture_covers_required_defect_types() -> None:
    corpus = _load_corpus()

    defect_types = {case["defect_type"] for case in corpus["cases"]}

    assert REQUIRED_DEFECT_TYPES <= defect_types


def test_validator_accepts_fixture_corpus() -> None:
    result = _run_validator(CORPUS)

    assert result.returncode == 0, result.stderr
    assert "synthetic adversarial corpus OK" in result.stdout


def test_validator_rejects_duplicate_case_ids(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken["cases"][1]["case_id"] = broken["cases"][0]["case_id"]
    path = tmp_path / "duplicate.json"
    _write_json(path, broken)

    result = _run_validator(path)

    assert result.returncode == 1
    assert "duplicate case_id" in result.stderr


def test_validator_requires_at_least_one_agent_expected_to_catch(tmp_path: Path) -> None:
    broken = copy.deepcopy(_load_expectations())
    broken["expectations"][0]["should_claude_catch"] = False
    broken["expectations"][0]["should_codex_catch"] = False
    path = tmp_path / "no_agent_expectations.json"
    _write_json(path, broken)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus",
            str(CORPUS),
            "--expectations",
            str(path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "no expected catching agent" in result.stderr


def test_validator_rejects_privacy_canary_leak_in_intent(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    canary = broken["cases"][0]["privacy_canary"]
    broken["cases"][0]["intent"] += f" {canary}"
    path = tmp_path / "privacy_leak.json"
    _write_json(path, broken)

    result = _run_validator(path)

    assert result.returncode == 1
    assert "privacy canary leaked into intent" in result.stderr


def test_validator_rejects_duplicate_privacy_canaries(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken["cases"][1]["privacy_canary"] = broken["cases"][0]["privacy_canary"]
    path = tmp_path / "duplicate_canary.json"
    _write_json(path, broken)

    result = _run_validator(path)

    assert result.returncode == 1
    assert "duplicate privacy_canary" in result.stderr


def test_validator_redacts_schema_error_values(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken["cases"][0]["defect_type"] = (
        "bad_DO_NOT_LEAK https://example.invalid sk-test"
    )
    path = tmp_path / "schema_redaction.json"
    _write_json(path, broken)

    result = _run_validator(path)
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "case 1: schema error at defect_type" in combined
    assert "_DO_NOT_LEAK" not in combined
    assert "https://example.invalid" not in combined
    assert "sk-test" not in combined
    assert "is not one of" not in combined


def test_validator_json_report_includes_coverage() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus",
            str(CORPUS),
            "--expectations",
            str(EXPECTATIONS),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["coverage"]["privacy_canary_count"] >= 2
    assert report["coverage"]["peer_review_trap_count"] >= 2
    assert set(report["coverage"]["expected_gate"]) == {
        "allow",
        "refuse",
        "review",
        "require_approval",
    }
    assert report["case_count"] >= 38


def test_validator_allows_folded_expansion_provenance_partial_coverage() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus",
            str(EXPANSION),
            "--expectations",
            str(EXPANSION_EXPECTATIONS),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["case_count"] == 8
    assert report["full_coverage_required"] is False
    assert report["coverage"]["privacy_canary_count"] >= 2
