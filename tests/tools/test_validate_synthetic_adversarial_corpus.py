from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "v3_13_0" / "synthetic_adversarial_case.v0.json"
CORPUS = ROOT / "tests" / "fixtures" / "magma_adversarial_corpus" / "v0.json"
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


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_validator(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(path)],
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
    assert len(corpus["cases"]) >= 8
    for case in corpus["cases"]:
        validator.validate(case)


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
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken["cases"][0]["should_claude_catch"] = False
    broken["cases"][0]["should_codex_catch"] = False
    path = tmp_path / "no_agent.json"
    _write_json(path, broken)

    result = _run_validator(path)

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
