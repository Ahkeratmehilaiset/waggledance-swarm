# SPDX-License-Identifier: BUSL-1.1
"""Tests for PDF-01 invoice field extraction first slice."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from waggledance.adapters.cli.pdf01_extract_invoice import main
from waggledance.core.v3_13_0.pdf01_invoice_field_extractor import (
    CASE_ID,
    DOCUMENT_UNPARSEABLE_REFUSED,
    INVOICE_FIELDS_INCOMPLETE,
    OK,
    Pdf01InvoiceFieldExtractorError,
    extract_pdf01_invoice_fields,
)


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "pdf01" / "invoice_text_sample.json"


def _payload() -> dict:
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def test_sample_invoices_extract_expected_fields() -> None:
    result = extract_pdf01_invoice_fields(_payload()).to_payload()

    assert result["case_id"] == CASE_ID
    assert result["result_marker"] == OK
    assert result["summary"] == {
        "total_documents": 3,
        "complete_documents": 3,
        "incomplete_documents": 0,
        "refused_documents": 0,
    }

    by_id = {item["document_id"]: item for item in result["documents"]}
    first = by_id["invoice-001"]["extracted_fields"]
    assert first == {
        "invoice_number": "12604312657",
        "invoice_date": "2026-01-15",
        "due_date": "2026-01-29",
        "amount_eur": 65.0,
        "reference_number": "123456789012",
        "iban": "FI1234567890123456",
        "bic": "OKOYFIHH",
        "recipient_name": "Example Telco Oy",
    }


def test_ocr_lines_and_pages_are_supported() -> None:
    result = extract_pdf01_invoice_fields(_payload()).to_payload()
    by_id = {item["document_id"]: item for item in result["documents"]}

    utility = by_id["invoice-002"]["extracted_fields"]
    assert utility["invoice_number"] == "2026-00077"
    assert utility["amount_eur"] == 1234.56
    assert utility["reference_number"] == "9876543210987654"
    assert utility["iban"] == "FI9876543210987654"

    service = by_id["invoice-003"]["extracted_fields"]
    assert service["invoice_number"] == "A-2026-42"
    assert service["amount_eur"] == 88.4
    assert service["recipient_name"] == "Example Services Ltd"


def test_unparseable_document_is_refused_without_leaking_text() -> None:
    payload = _payload()
    payload["documents"] = [{
        "document_id": "bad-001",
        "text": "This document has no invoice payment fields.",
    }]

    result = extract_pdf01_invoice_fields(payload).to_payload()
    document = result["documents"][0]

    assert result["result_marker"] == INVOICE_FIELDS_INCOMPLETE
    assert document["result_marker"] == DOCUMENT_UNPARSEABLE_REFUSED
    assert document["confidence"] == 0.0
    assert "text" not in document
    assert document["extracted_fields"]["amount_eur"] is None


def test_missing_required_payment_field_is_incomplete() -> None:
    payload = _payload()
    payload["documents"][0]["text"] = (
        "Saaja: Example Telco Oy\n"
        "Laskun numero 12604312657\n"
        "Paivays 15.01.2026\n"
        "Erapaiva 29.01.2026\n"
        "Maksettava yhteensa EUR 65,00\n"
        "FI12 3456 7890 1234 56\n"
        "BIC OKOYFIHH"
    )
    payload["documents"] = [payload["documents"][0]]

    result = extract_pdf01_invoice_fields(payload).to_payload()
    document = result["documents"][0]

    assert result["result_marker"] == INVOICE_FIELDS_INCOMPLETE
    assert document["result_marker"] == INVOICE_FIELDS_INCOMPLETE
    assert document["missing_required_fields"] == ["reference_number"]


def test_impossible_calendar_date_is_not_accepted() -> None:
    payload = _payload()
    payload["documents"][0]["text"] = (
        "Saaja: Example Telco Oy\n"
        "Laskun numero 12604312657\n"
        "Paivays 31.02.2026\n"
        "Erapaiva 29.03.2026\n"
        "Maksettava yhteensa EUR 65,00\n"
        "Viitenumero 1234 5678 9012\n"
        "FI12 3456 7890 1234 56\n"
        "BIC OKOYFIHH"
    )
    payload["documents"] = [payload["documents"][0]]

    result = extract_pdf01_invoice_fields(payload).to_payload()
    document = result["documents"][0]

    assert document["result_marker"] == INVOICE_FIELDS_INCOMPLETE
    assert document["missing_required_fields"] == ["invoice_date"]


def test_duplicate_document_id_refuses() -> None:
    payload = _payload()
    payload["documents"][1]["document_id"] = "invoice-001"

    with pytest.raises(Pdf01InvoiceFieldExtractorError,
                       match="duplicate document_id: invoice-001"):
        extract_pdf01_invoice_fields(payload)


def test_bool_text_and_secret_source_name_refuse() -> None:
    payload = _payload()
    payload["documents"][0]["text"] = True
    with pytest.raises(Pdf01InvoiceFieldExtractorError,
                       match="document text"):
        extract_pdf01_invoice_fields(payload)

    payload = _payload()
    payload["documents"][0]["source_name"] = "tokenized_invoice.pdf"
    with pytest.raises(Pdf01InvoiceFieldExtractorError,
                       match="source_name must not contain secrets"):
        extract_pdf01_invoice_fields(payload)


def test_cli_prints_compact_json() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(EXAMPLE_INPUT)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == OK
    assert output["summary"]["complete_documents"] == 3


def test_cli_pretty_output(tmp_path: Path) -> None:
    input_path = tmp_path / "invoice_text.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        ["--input", str(input_path), "--pretty"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert "\n  " in stdout.getvalue()


def test_cli_refuses_invalid_input_with_json_error(tmp_path: Path) -> None:
    input_path = tmp_path / "invoice_text.json"
    input_path.write_text(json.dumps({"documents": []}), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "documents must be a non-empty list" in output["error"]
