# SPDX-License-Identifier: BUSL-1.1
"""Tests for FIN-10 receipt tag classifier first slice."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from waggledance.adapters.cli.fin10_classify_receipts import main
from waggledance.core.v3_13_0.fin10_receipt_classifier import (
    CASE_ID,
    Fin10ReceiptClassifierError,
    OK,
    TAG_COTTAGE,
    TAG_HOME,
    TAG_MIXED,
    classify_fin10_receipts,
)


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "fin10" / "receipts_sample.json"


def _payload() -> dict:
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def test_sample_receipts_match_expected_summary() -> None:
    result = classify_fin10_receipts(_payload()).to_payload()

    assert result["case_id"] == CASE_ID
    assert result["result_marker"] == OK
    assert result["summary"] == {
        "total_receipts": 10,
        "clear_classifications": 6,
        "ambiguous_classifications": 4,
        "cottage": 3,
        "home": 3,
        "mixed": 4,
    }


def test_clear_cottage_and_home_geo_signals_are_classified() -> None:
    result = classify_fin10_receipts(_payload()).to_payload()
    by_id = {
        item["receipt_id"]: item
        for item in result["classifications"]
    }

    assert by_id["r-001"]["suggested_tag"] == TAG_COTTAGE
    assert by_id["r-001"]["confidence"] >= 0.87
    assert by_id["r-001"]["matched_cottage_signals"] == [
        "cottage road",
        "lake supply",
    ]
    assert by_id["r-002"]["suggested_tag"] == TAG_HOME
    assert by_id["r-002"]["confidence"] >= 0.87
    assert by_id["r-002"]["matched_home_signals"] == [
        "home street",
        "urban grocery",
    ]


def test_receipt_with_both_signal_sets_is_mixed() -> None:
    result = classify_fin10_receipts(_payload()).to_payload()
    mixed = next(
        item for item in result["classifications"]
        if item["receipt_id"] == "r-007"
    )

    assert mixed["suggested_tag"] == TAG_MIXED
    assert mixed["confidence"] == 0.5
    assert mixed["matched_cottage_signals"] == ["cottage road"]
    assert mixed["matched_home_signals"] == ["home street"]


def test_receipt_without_signals_is_mixed_with_zero_confidence() -> None:
    result = classify_fin10_receipts(_payload()).to_payload()
    unknown = next(
        item for item in result["classifications"]
        if item["receipt_id"] == "r-008"
    )

    assert unknown["suggested_tag"] == TAG_MIXED
    assert unknown["confidence"] == 0.0
    assert unknown["rationale_summary"] == "no configured geo signal matched"


def test_duplicate_receipt_id_refuses() -> None:
    payload = _payload()
    payload["receipts"][1]["receipt_id"] = "r-001"

    with pytest.raises(Fin10ReceiptClassifierError,
                       match="duplicate receipt_id: r-001"):
        classify_fin10_receipts(payload)


def test_bool_text_field_refuses() -> None:
    payload = _payload()
    payload["receipts"][0]["description"] = True

    with pytest.raises(Fin10ReceiptClassifierError,
                       match="description must be a string"):
        classify_fin10_receipts(payload)


def test_empty_signal_list_refuses() -> None:
    payload = _payload()
    payload["home_signals"] = []

    with pytest.raises(Fin10ReceiptClassifierError,
                       match="home_signals must be a non-empty list"):
        classify_fin10_receipts(payload)


def test_cli_prints_compact_json() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(EXAMPLE_INPUT)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == OK
    assert output["summary"]["clear_classifications"] == 6


def test_cli_pretty_output(tmp_path: Path) -> None:
    input_path = tmp_path / "receipts.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        ["--input", str(input_path), "--pretty"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert "\n  " in stdout.getvalue()


def test_cli_refuses_invalid_input_with_json_error(tmp_path: Path) -> None:
    input_path = tmp_path / "receipts.json"
    input_path.write_text(json.dumps({"receipts": []}), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "cottage_signals must be a non-empty list" in output["error"]
