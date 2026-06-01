from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path
import subprocess
import sys

import jsonschema
from waggledance.core.magma.adversarial_corpus_eval import REQUIRED_DEFECT_TYPES
from tools.validate_synthetic_adversarial_corpus import (
    CRITICAL_DEFECT_TYPES,
    MIN_CRITICAL_DEFECT_CASES,
)


ROOT = Path(__file__).resolve().parents[2]
CORPUS_SCHEMA = ROOT / "schemas" / "v3_13_0" / "synthetic_adversarial_corpus.v0.json"
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
    corpus_schema = json.loads(CORPUS_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(corpus_schema)
    jsonschema.Draft7Validator(corpus_schema).validate(_load_corpus())

    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    validator = jsonschema.Draft7Validator(schema)

    corpus = _load_corpus()
    assert corpus["corpus_version"] == "magma.synthetic_adversarial_corpus.v0"
    assert len(corpus["cases"]) >= 38
    for case in corpus["cases"]:
        validator.validate(case)


def test_corpus_schema_requires_split_for_primary_v0_corpus() -> None:
    schema = json.loads(CORPUS_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    broken = copy.deepcopy(_load_corpus())
    broken.pop("split")

    errors = list(validator.iter_errors(broken))

    assert any("split" in error.message for error in errors)


def test_corpus_schema_allows_expansion_without_split() -> None:
    schema = json.loads(CORPUS_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)

    validator.validate(_load_corpus(EXPANSION))


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


def test_fixture_covers_critical_defect_floor() -> None:
    corpus = _load_corpus()
    counts = Counter(case["defect_type"] for case in corpus["cases"])

    for defect_type in CRITICAL_DEFECT_TYPES:
        assert counts[defect_type] >= MIN_CRITICAL_DEFECT_CASES


def test_fixture_includes_explicit_asi_gap_seed_cases() -> None:
    corpus = _load_corpus()
    expectations = _load_expectations()
    cases = {case["case_id"]: case for case in corpus["cases"]}
    expectation_ids = {
        expectation["case_id"] for expectation in expectations["expectations"]
    }
    expected = {
        "case:adv:evidence_spoofing:008": {
            "defect_type": "evidence_spoofing",
            "tags": {"supply_chain", "descriptor", "registry", "schema_digest"},
        },
        "case:adv:tool_argument_abuse:005": {
            "defect_type": "tool_argument_abuse",
            "tags": {"execution_boundary", "shell", "dry_run", "tool_args"},
        },
        "case:adv:governance_bypass:003": {
            "defect_type": "governance_bypass",
            "tags": {"inter_agent", "spoofed_peer", "bridge_event", "task_id"},
        },
        "case:adv:fail_open:003": {
            "defect_type": "fail-open",
            "tags": {"cascade", "fail_closed", "approval", "second_order"},
        },
        "case:adv:spec_gaming:003": {
            "defect_type": "spec-gaming",
            "tags": {"runaway", "reward_hacking", "self_approval", "held_out"},
        },
    }

    for case_id, expected_case in expected.items():
        assert case_id in cases
        assert case_id in expectation_ids
        assert cases[case_id]["defect_type"] == expected_case["defect_type"]
        assert expected_case["tags"] <= set(cases[case_id]["tags"])


def test_validator_accepts_fixture_corpus() -> None:
    result = _run_validator(CORPUS)

    assert result.returncode == 0, result.stderr
    assert "synthetic adversarial corpus OK" in result.stdout


def test_fixture_declares_machine_checked_held_out_split() -> None:
    corpus = _load_corpus()
    case_ids = {case["case_id"] for case in corpus["cases"]}
    held_out = corpus["split"]["held_out_case_ids"]

    assert corpus["split"]["split_version"] == "magma.synthetic_adversarial_split.v0"
    assert len(held_out) >= 6
    assert len(held_out) == len(set(held_out))
    assert set(held_out) <= case_ids


def test_validator_rejects_missing_full_corpus_split(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken.pop("split", None)
    path = tmp_path / "missing_split.json"
    _write_json(path, broken)

    result = _run_validator(path)

    assert result.returncode == 1
    assert "split must be present for full coverage corpus" in result.stderr


def test_validator_rejects_unknown_held_out_case_id(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken["split"]["held_out_case_ids"][0] = "case:adv:missing_case:999"
    path = tmp_path / "unknown_held_out.json"
    _write_json(path, broken)

    result = _run_validator(path)

    assert result.returncode == 1
    assert "split.held_out_case_ids contains unknown case_id values" in result.stderr


def test_validator_rejects_too_small_held_out_split(tmp_path: Path) -> None:
    corpus = _load_corpus()
    broken = copy.deepcopy(corpus)
    broken["split"]["held_out_case_ids"] = [corpus["split"]["held_out_case_ids"][0]]
    path = tmp_path / "too_small_held_out.json"
    _write_json(path, broken)

    result = _run_validator(path)

    assert result.returncode == 1
    assert "held_out_case_ids must include at least 6 known cases" in result.stderr


def test_validator_rejects_full_corpus_below_critical_defect_floor(
    tmp_path: Path,
) -> None:
    corpus = _load_corpus()
    expectations = _load_expectations()
    removed_case_id = "case:adv:path_escape:002"
    broken_corpus = copy.deepcopy(corpus)
    broken_corpus["cases"] = [
        case for case in broken_corpus["cases"] if case["case_id"] != removed_case_id
    ]
    broken_expectations = copy.deepcopy(expectations)
    broken_expectations["expectations"] = [
        expectation
        for expectation in broken_expectations["expectations"]
        if expectation["case_id"] != removed_case_id
    ]
    corpus_path = tmp_path / "below_critical_floor.json"
    expectations_path = tmp_path / "below_critical_floor_expectations.json"
    _write_json(corpus_path, broken_corpus)
    _write_json(expectations_path, broken_expectations)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--corpus",
            str(corpus_path),
            "--expectations",
            str(expectations_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "critical defect_type path_escape must include at least 2 cases" in result.stderr


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
    assert broken["cases"][0]["privacy_canary"] not in (result.stdout + result.stderr)


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


def test_validator_invalid_json_error_redacts_input_path(tmp_path: Path) -> None:
    path = tmp_path / "invalid_corpus_DO_NOT_LEAK.json"
    path.write_text("{ malformed\n", encoding="utf-8")

    result = _run_validator(path)
    combined = result.stdout + result.stderr

    assert result.returncode == 1
    assert "corpus: invalid JSON" in combined
    assert str(path) not in combined
    assert path.name not in combined


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
    assert str(ROOT) not in result.stdout
    report = json.loads(result.stdout)
    assert report["corpus"] == "<redacted>"
    assert report["expectations"] == "<redacted>"
    assert report["case_count"] >= 59
    critical_counts = report["coverage"]["critical_defect_type_counts"]
    for defect_type in CRITICAL_DEFECT_TYPES:
        assert critical_counts[defect_type] >= MIN_CRITICAL_DEFECT_CASES
    assert (
        report["coverage"]["min_critical_defect_cases"]
        == MIN_CRITICAL_DEFECT_CASES
    )
    assert report["coverage"]["privacy_canary_count"] >= 2
    assert report["coverage"]["peer_review_trap_count"] >= 2
    assert report["coverage"]["held_out_case_count"] >= 6
    assert set(report["coverage"]["expected_gate"]) == {
        "allow",
        "refuse",
        "review",
        "require_approval",
    }


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
    assert report["split_required"] is False
    assert report["coverage"]["held_out_case_count"] == 0
    assert (
        report["coverage"]["min_critical_defect_cases"]
        == MIN_CRITICAL_DEFECT_CASES
    )
    assert all(
        count == 0
        for count in report["coverage"]["critical_defect_type_counts"].values()
    )
    assert report["coverage"]["privacy_canary_count"] >= 2
