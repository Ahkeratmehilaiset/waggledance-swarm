from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import jsonschema
from tools.generate_magma_adversarial_candidates import (
    PROFILE_BY_DEFECT_TYPE,
    build_candidate_report,
)
from tools.validate_synthetic_adversarial_corpus import (
    CASE_SCHEMA,
    DEFAULT_CORPUS,
    EXPECTATION_SCHEMA,
)
from waggledance.core.magma.adversarial_corpus_eval import REQUIRED_DEFECT_TYPES


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "generate_magma_adversarial_candidates.py"


def _load_corpus() -> dict:
    return json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))


def _case_validator() -> jsonschema.Draft7Validator:
    return jsonschema.Draft7Validator(json.loads(CASE_SCHEMA.read_text(encoding="utf-8")))


def _expectation_validator() -> jsonschema.Draft7Validator:
    return jsonschema.Draft7Validator(
        json.loads(EXPECTATION_SCHEMA.read_text(encoding="utf-8"))
    )


def test_default_report_produces_schema_valid_non_colliding_candidates() -> None:
    report = build_candidate_report(limit=6)
    existing_ids = {case["case_id"] for case in _load_corpus()["cases"]}
    case_validator = _case_validator()
    expectation_validator = _expectation_validator()

    assert report["ok"] is True
    assert report["candidate_count"] == 6
    assert report["source"]["source_validation_ok"] is True
    assert report["metrics"]["diversity"]["required_defect_type_coverage_ratio"] == 1.0

    generated_ids: set[str] = set()
    for candidate in report["candidates"]:
        case = candidate["case"]
        expectation = candidate["expectation"]

        assert candidate["schema_valid"] is True
        assert candidate["schema_errors"] == []
        assert case["case_id"] == expectation["case_id"]
        assert case["case_id"] not in existing_ids
        assert case["case_id"] not in generated_ids
        generated_ids.add(case["case_id"])
        assert case["defect_type"] in REQUIRED_DEFECT_TYPES
        assert case["privacy_canary"] not in case["intent"]

        case_validator.validate(case)
        expectation_validator.validate(expectation)


def test_generation_profiles_cover_required_defect_types() -> None:
    assert set(PROFILE_BY_DEFECT_TYPE) == set(REQUIRED_DEFECT_TYPES)


def test_default_selection_prioritizes_lowest_defect_counts() -> None:
    report = build_candidate_report(limit=6)
    counts = report["metrics"]["defect_type_counts"]
    selected = report["selection"]["selected_defect_types"]
    expected = sorted(sorted(REQUIRED_DEFECT_TYPES), key=lambda item: (counts[item], item))[:6]

    assert selected == expected


def test_requested_defect_type_is_respected() -> None:
    report = build_candidate_report(limit=2, defect_types=["path_escape", "fail-open"])

    assert report["ok"] is True
    assert report["selection"]["requested_defect_types"] == ["path_escape", "fail-open"]
    assert report["selection"]["selected_defect_types"] == ["path_escape", "fail-open"]
    assert [candidate["case"]["defect_type"] for candidate in report["candidates"]] == [
        "path_escape",
        "fail-open",
    ]


def test_cli_json_output_is_machine_readable() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--limit", "2", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["candidate_count"] == 2
    assert len(report["candidates"]) == 2
