# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Tests for EMAIL-02 vendor email indexing first slice."""
from __future__ import annotations

from io import StringIO
import json
from pathlib import Path

import pytest

from waggledance.adapters.cli.email02_index_vendor_emails import main
from waggledance.core.v3_13_0.email02_vendor_email_indexer import (
    CASE_ID,
    Email02VendorEmailIndexerError,
    MATCH_SENDER_DOMAIN_BILLING,
    MATCH_SIGNAL_BILLING,
    OK,
    index_email02_vendor_messages,
)


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "email02" / "vendor_email_index_sample.json"


def _payload() -> dict:
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def test_sample_vendor_email_index_matches_expected_summary() -> None:
    result = index_email02_vendor_messages(_payload()).to_payload()

    assert result["case_id"] == CASE_ID
    assert result["result_marker"] == OK
    assert result["write_intent"] == "none"
    assert result["summary"] == {
        "total_vendors": 3,
        "vendors_with_matches": 3,
        "total_messages": 6,
        "matched_messages": 5,
        "unmatched_messages": 1,
        "billing_messages": 4,
    }


def test_sample_vendor_counts_and_match_types_are_deterministic() -> None:
    result = index_email02_vendor_messages(_payload()).to_payload()
    by_vendor = {item["vendor_id"]: item for item in result["vendor_indexes"]}

    assert by_vendor["helen"]["matched_messages"] == 2
    assert by_vendor["helen"]["billing_messages"] == 1
    assert by_vendor["helen"]["latest_message_date"] == "2026-05-01"
    assert by_vendor["helen"]["latest_messages"][0]["match_type"] == (
        MATCH_SENDER_DOMAIN_BILLING
    )

    assert by_vendor["dna"]["matched_messages"] == 2
    assert by_vendor["dna"]["billing_messages"] == 2
    assert by_vendor["dna"]["latest_message_date"] == "2026-05-06"
    assert by_vendor["dna"]["latest_messages"][0]["match_type"] == (
        MATCH_SIGNAL_BILLING
    )

    assert by_vendor["insureco"]["matched_messages"] == 1
    assert by_vendor["insureco"]["latest_messages"][0]["sender_domain"] == (
        "mail.example"
    )


def test_email_free_text_and_sender_local_part_are_not_echoed() -> None:
    payload = _payload()
    payload["messages"][0]["subject"] = "Internal private billing note"
    payload["messages"][0]["snippet"] = "Operator-only context should stay local"
    payload["messages"][0]["body_text"] = (
        "Do not leak this message body or personal details"
    )

    result = index_email02_vendor_messages(payload).to_payload()
    encoded = json.dumps(result, sort_keys=True)

    assert "Internal private billing note" not in encoded
    assert "Operator-only context" not in encoded
    assert "Do not leak this message body" not in encoded
    assert "billing@helen.fi" not in encoded
    assert "helen.fi" in encoded


def test_sender_domain_match_precedes_cross_vendor_signal_match() -> None:
    payload = {
        "as_of_date": "2026-05-16",
        "vendors": [
            {
                "vendor_id": "helen",
                "display_name": "Helen",
                "domains": ["helen.fi"],
                "name_signals": ["helen"],
                "billing_keywords": ["invoice"],
            },
            {
                "vendor_id": "dna",
                "display_name": "DNA",
                "domains": ["dna.fi"],
                "name_signals": ["dna"],
                "billing_keywords": ["invoice"],
            },
        ],
        "messages": [
            {
                "message_id": "m-1",
                "thread_id": "t-1",
                "date": "2026-05-01",
                "from": "Billing <billing@helen.fi>",
                "subject": "DNA invoice",
            }
        ],
    }

    result = index_email02_vendor_messages(payload).to_payload()
    by_vendor = {item["vendor_id"]: item for item in result["vendor_indexes"]}

    assert by_vendor["helen"]["matched_messages"] == 1
    assert by_vendor["dna"]["matched_messages"] == 0
    evidence = by_vendor["helen"]["latest_messages"][0]
    assert evidence["match_type"] == MATCH_SENDER_DOMAIN_BILLING
    assert evidence["ambiguous_vendor_count"] == 2


def test_duplicate_vendor_and_message_ids_refuse() -> None:
    payload = _payload()
    payload["vendors"][1]["vendor_id"] = "helen"
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="duplicate vendor_id: helen"):
        index_email02_vendor_messages(payload)

    payload = _payload()
    payload["messages"][1]["message_id"] = "msg-001"
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="duplicate message_id: msg-001"):
        index_email02_vendor_messages(payload)


def test_fail_closed_for_invalid_dates_domains_and_bool_text() -> None:
    payload = _payload()
    payload["messages"][0]["date"] = "2026-05-01T12:00:00"
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="date must be an ISO date"):
        index_email02_vendor_messages(payload)

    payload = _payload()
    payload["vendors"][0]["domains"] = ["billing@helen.fi"]
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="must be a domain name"):
        index_email02_vendor_messages(payload)

    payload = _payload()
    payload["messages"][0]["subject"] = True
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="subject must be a string"):
        index_email02_vendor_messages(payload)


def test_vendor_config_secret_markers_refuse() -> None:
    payload = _payload()
    payload["vendors"][0]["display_name"] = "Helen token"
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="display_name must not contain secrets"):
        index_email02_vendor_messages(payload)


def test_max_messages_per_vendor_bounds_and_limits_output() -> None:
    payload = _payload()
    payload["max_messages_per_vendor"] = 1
    result = index_email02_vendor_messages(payload).to_payload()
    helen = {
        item["vendor_id"]: item for item in result["vendor_indexes"]
    }["helen"]

    assert result["max_messages_per_vendor"] == 1
    assert helen["matched_messages"] == 2
    assert len(helen["latest_messages"]) == 1

    payload["max_messages_per_vendor"] = 0
    with pytest.raises(Email02VendorEmailIndexerError,
                       match="max_messages_per_vendor must be between 1 and 50"):
        index_email02_vendor_messages(payload)


def test_cli_pretty_prints_json() -> None:
    stdout = StringIO()
    stderr = StringIO()

    exit_code = main([
        "--input",
        str(EXAMPLE_INPUT),
        "--pretty",
    ], stdout=stdout, stderr=stderr)

    assert exit_code == 0
    assert stderr.getvalue() == ""
    payload = json.loads(stdout.getvalue())
    assert payload["summary"]["matched_messages"] == 5


def test_cli_invalid_input_writes_error_to_stderr(tmp_path: Path) -> None:
    input_path = tmp_path / "vendor_email_index.json"
    input_path.write_text(json.dumps({"messages": []}), encoding="utf-8")
    stdout = StringIO()
    stderr = StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "as_of_date must be a non-empty string" in error["error"]
