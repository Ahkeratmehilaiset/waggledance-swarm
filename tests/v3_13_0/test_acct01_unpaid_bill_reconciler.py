# SPDX-License-Identifier: BUSL-1.1
"""Tests for ACCT-01 unpaid-bill reconciliation first slice."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from waggledance.adapters.cli.acct01_reconcile_bills import main
from waggledance.core.v3_13_0.acct01_unpaid_bill_reconciler import (
    CASE_ID,
    MATCH_AMOUNT_DATE_WINDOW,
    MATCH_MESSAGE_REFERENCE,
    MATCH_NONE,
    MATCH_REFERENCE_EXACT,
    MATCH_STATUS_EXCLUDED,
    MATCH_STATUS_MARKED_PAID,
    OK,
    STATUS_EXCLUDED,
    STATUS_PAID,
    STATUS_UNPAID,
    Acct01UnpaidBillReconcilerError,
    reconcile_acct01_unpaid_bills,
)


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "acct01" / "unpaid_bill_reconciliation_sample.json"


def _payload() -> dict:
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def test_sample_reconciliation_summary() -> None:
    result = reconcile_acct01_unpaid_bills(_payload()).to_payload()

    assert result["case_id"] == CASE_ID
    assert result["result_marker"] == OK
    assert result["write_intent"] == "none"
    assert result["summary"] == {
        "total_invoices": 9,
        "paid": 5,
        "unpaid": 3,
        "excluded": 1,
        "total_unpaid_eur": 208.4,
        "overdue_unpaid": 3,
    }


def test_reference_message_and_amount_window_matches() -> None:
    result = reconcile_acct01_unpaid_bills(_payload()).to_payload()
    by_id = {item["invoice_id"]: item for item in result["reconciliations"]}

    assert by_id["inv-001"]["payment_status"] == STATUS_PAID
    assert by_id["inv-001"]["match_type"] == MATCH_REFERENCE_EXACT
    assert by_id["inv-001"]["matched_transaction_id"] == "tx-001"

    assert by_id["inv-002"]["payment_status"] == STATUS_PAID
    assert by_id["inv-002"]["match_type"] == MATCH_MESSAGE_REFERENCE
    assert by_id["inv-002"]["matched_transaction_id"] == "tx-002"

    assert by_id["inv-003"]["payment_status"] == STATUS_PAID
    assert by_id["inv-003"]["match_type"] == MATCH_AMOUNT_DATE_WINDOW
    assert by_id["inv-003"]["matched_transaction_id"] == "tx-003"


def test_status_paid_and_excluded_do_not_require_bank_match() -> None:
    result = reconcile_acct01_unpaid_bills(_payload()).to_payload()
    by_id = {item["invoice_id"]: item for item in result["reconciliations"]}

    assert by_id["inv-005"]["payment_status"] == STATUS_PAID
    assert by_id["inv-005"]["match_type"] == MATCH_STATUS_MARKED_PAID
    assert "matched_transaction_id" not in by_id["inv-005"]

    assert by_id["inv-006"]["payment_status"] == STATUS_EXCLUDED
    assert by_id["inv-006"]["match_type"] == MATCH_STATUS_EXCLUDED


def test_unpaid_when_tolerance_or_date_window_do_not_match() -> None:
    result = reconcile_acct01_unpaid_bills(_payload()).to_payload()
    by_id = {item["invoice_id"]: item for item in result["reconciliations"]}

    assert by_id["inv-004"]["payment_status"] == STATUS_UNPAID
    assert by_id["inv-004"]["match_type"] == MATCH_NONE
    assert by_id["inv-008"]["payment_status"] == STATUS_UNPAID
    assert by_id["inv-009"]["payment_status"] == STATUS_UNPAID


def test_transaction_free_text_is_not_echoed_to_output() -> None:
    payload = _payload()
    payload["bank_transactions"][1]["message"] = (
        "Utility invoice ref 9876 5432 1098 internal note"
    )

    result = reconcile_acct01_unpaid_bills(payload).to_payload()
    encoded = json.dumps(result, sort_keys=True)

    assert "internal note" not in encoded
    assert "Utility invoice ref" not in encoded


def test_duplicate_invoice_and_transaction_ids_refuse() -> None:
    payload = _payload()
    payload["invoices"][1]["invoice_id"] = "inv-001"
    with pytest.raises(Acct01UnpaidBillReconcilerError,
                       match="duplicate invoice_id: inv-001"):
        reconcile_acct01_unpaid_bills(payload)

    payload = _payload()
    payload["bank_transactions"][1]["transaction_id"] = "tx-001"
    with pytest.raises(Acct01UnpaidBillReconcilerError,
                       match="duplicate transaction_id: tx-001"):
        reconcile_acct01_unpaid_bills(payload)


def test_bool_numeric_and_invalid_reference_refuse() -> None:
    payload = _payload()
    payload["invoices"][0]["amount_eur"] = True
    with pytest.raises(Acct01UnpaidBillReconcilerError,
                       match="amount_eur must be numeric"):
        reconcile_acct01_unpaid_bills(payload)

    payload = _payload()
    payload["invoices"][0]["reference_number"] = "ABC-123"
    with pytest.raises(Acct01UnpaidBillReconcilerError,
                       match="reference_number must contain"):
        reconcile_acct01_unpaid_bills(payload)


def test_custom_tolerance_can_match_amount_difference() -> None:
    payload = _payload()
    payload["amount_tolerance_eur"] = 0.05

    result = reconcile_acct01_unpaid_bills(payload).to_payload()
    by_id = {item["invoice_id"]: item for item in result["reconciliations"]}

    assert by_id["inv-008"]["payment_status"] == STATUS_PAID
    assert by_id["inv-008"]["match_type"] == MATCH_AMOUNT_DATE_WINDOW


def test_cli_prints_compact_json() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(EXAMPLE_INPUT)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == OK
    assert output["summary"]["unpaid"] == 3


def test_cli_pretty_output(tmp_path: Path) -> None:
    input_path = tmp_path / "acct01.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        ["--input", str(input_path), "--pretty"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert "\n  " in stdout.getvalue()


def test_cli_refuses_invalid_input_with_json_error(tmp_path: Path) -> None:
    input_path = tmp_path / "acct01.json"
    input_path.write_text(json.dumps({"invoices": []}), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "as_of_date must be a non-empty string" in output["error"]
