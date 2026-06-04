from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import jsonschema
from tools.generate_magma_adversarial_candidates import (
    ASI_DEFECT_TYPE_MAP,
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
SENSITIVE_CANARY = "_DO" + "_NOT" + "_LEAK"


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
    assert report["source"]["corpus"] == "<redacted>"
    assert report["source"]["expectations"] == "<redacted>"
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
        assert case["privacy_canary"] is None

        case_validator.validate(case)
        expectation_validator.validate(expectation)


def test_generation_profiles_cover_required_defect_types() -> None:
    assert set(PROFILE_BY_DEFECT_TYPE) == set(REQUIRED_DEFECT_TYPES)


def test_asi_map_uses_known_defect_types() -> None:
    assert set(ASI_DEFECT_TYPE_MAP) == {f"asi{number:02d}" for number in range(1, 11)}
    for defect_types in ASI_DEFECT_TYPE_MAP.values():
        assert set(defect_types) <= set(REQUIRED_DEFECT_TYPES)


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


def test_requested_asi_id_selects_mapped_lowest_count_defects() -> None:
    report = build_candidate_report(limit=2, asi_ids=["ASI04"])
    counts = report["metrics"]["defect_type_counts"]
    expected = sorted(
        ASI_DEFECT_TYPE_MAP["asi04"],
        key=lambda item: (counts[item], item),
    )[:2]

    assert report["ok"] is True
    assert report["selection"]["requested_asi_ids"] == ["asi04"]
    assert report["selection"]["asi_defect_type_candidates"] == list(
        ASI_DEFECT_TYPE_MAP["asi04"]
    )
    assert report["selection"]["selected_defect_types"] == expected
    for candidate in report["candidates"]:
        assert candidate["case"]["defect_type"] in ASI_DEFECT_TYPE_MAP["asi04"]
        assert candidate["asi_targets"] == ["asi04"]
        assert "asi04" in candidate["case"]["tags"]
        assert "asi_targets=asi04" in candidate["selection_reason"]


def test_requested_asi_id_rejects_explicit_defect_outside_mapping() -> None:
    report = build_candidate_report(
        limit=1,
        asi_ids=["ASI04"],
        defect_types=["path_escape"],
    )

    assert report["ok"] is False
    assert report["candidate_count"] == 0
    assert report["selection"]["requested_asi_ids"] == ["asi04"]
    assert report["selection"]["requested_defect_types"] == ["path_escape"]
    assert report["selection"]["selected_defect_types"] == []
    assert report["errors"] == [
        "selection: defect_type outside requested ASI mapping: path_escape"
    ]


def test_requested_asi_id_rejects_mixed_explicit_defects_without_partial_candidates() -> None:
    report = build_candidate_report(
        limit=3,
        asi_ids=["ASI04"],
        defect_types=["governance_bypass", "path_escape"],
    )

    assert report["ok"] is False
    assert report["candidate_count"] == 0
    assert report["selection"]["requested_asi_ids"] == ["asi04"]
    assert report["selection"]["requested_defect_types"] == [
        "governance_bypass",
        "path_escape",
    ]
    assert report["selection"]["selected_defect_types"] == []
    assert report["errors"] == [
        "selection: defect_type outside requested ASI mapping: path_escape"
    ]


def test_requested_asi_id_accepts_explicit_defect_inside_mapping() -> None:
    report = build_candidate_report(
        limit=1,
        asi_ids=["ASI04"],
        defect_types=["governance_bypass"],
    )

    assert report["ok"] is True
    assert report["candidate_count"] == 1
    assert report["selection"]["selected_defect_types"] == ["governance_bypass"]
    candidate = report["candidates"][0]
    assert candidate["case"]["defect_type"] == "governance_bypass"
    assert candidate["asi_targets"] == ["asi04"]
    assert "asi04" in candidate["case"]["tags"]


def test_requested_multi_asi_union_accepts_defect_inside_any_mapping() -> None:
    report = build_candidate_report(
        limit=1,
        asi_ids=["ASI04", "ASI05"],
        defect_types=["path_escape"],
    )

    assert report["ok"] is True
    assert report["candidate_count"] == 1
    assert report["selection"]["requested_asi_ids"] == ["asi04", "asi05"]
    assert report["selection"]["selected_defect_types"] == ["path_escape"]
    candidate = report["candidates"][0]
    assert candidate["case"]["defect_type"] == "path_escape"
    assert candidate["asi_targets"] == ["asi05"]
    assert "asi05" in candidate["case"]["tags"]
    assert "asi04" not in candidate["case"]["tags"]


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
    assert str(ROOT) not in result.stdout
    assert SENSITIVE_CANARY not in result.stdout


def test_cli_asi_json_output_is_machine_readable() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--asi", "05", "--limit", "2", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["selection"]["requested_asi_ids"] == ["asi05"]
    assert set(report["selection"]["selected_defect_types"]) <= set(
        ASI_DEFECT_TYPE_MAP["asi05"]
    )
    assert all("asi05" in candidate["case"]["tags"] for candidate in report["candidates"])
    assert str(ROOT) not in result.stdout
    assert SENSITIVE_CANARY not in result.stdout


def test_cli_rejects_asi_defect_type_outside_mapping() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--asi",
            "ASI04",
            "--defect-type",
            "path_escape",
            "--limit",
            "1",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["candidate_count"] == 0
    assert report["selection"]["selected_defect_types"] == []
    assert report["errors"] == [
        "selection: defect_type outside requested ASI mapping: path_escape"
    ]


def test_cli_rejects_mixed_asi_defect_types_without_partial_candidates() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--asi",
            "ASI04",
            "--defect-type",
            "governance_bypass",
            "--defect-type",
            "path_escape",
            "--limit",
            "3",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["ok"] is False
    assert report["candidate_count"] == 0
    assert report["selection"]["selected_defect_types"] == []
    assert report["errors"] == [
        "selection: defect_type outside requested ASI mapping: path_escape"
    ]
    assert "Traceback" not in result.stderr
    assert str(ROOT) not in result.stdout
    assert str(ROOT) not in result.stderr
