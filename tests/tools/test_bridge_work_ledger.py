# SPDX-License-Identifier: BUSL-1.1
"""Acceptance tests for the dormant pure work ledger."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from itertools import permutations

import pytest

from tools.bridge_work_ledger import (
    INPUT_SCHEMA,
    InputError,
    build_report,
    load_document,
    strict_loads,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_work_ledger.py"


def _usage(**over):
    item = {
        "provider": "provider-a",
        "session_id": "session-a",
        "turn_id": "turn-a",
        "attempt_id": "attempt-a",
        "observed_at": "2026-09-25T07:00:00Z",
        "cumulative_tokens": 140,
        "baseline_tokens": 100,
    }
    item.update(over)
    return item


def _accepted(**over):
    item = {
        "contract_id": "contract-a",
        "revision": "1",
        "artifact_id": "artifact-a",
        "evaluation_id": "evaluation-a",
        "state": "accepted",
        "observed_at": "2026-09-25T07:01:00Z",
    }
    item.update(over)
    return item


def _document(*, usage=None, accepted=None):
    return {
        "schema": INPUT_SCHEMA,
        "usage_attempts": usage if usage is not None else [_usage()],
        "accepted_work": accepted if accepted is not None else [_accepted()],
    }


def test_usage_attempt_dedupe_is_separate_from_accepted_receipt_identity():
    report = build_report(
        _document(
            usage=[_usage(), _usage(observed_at="2026-09-25T07:05:00Z")],
            accepted=[_accepted(), _accepted(artifact_id="artifact-b", evaluation_id="evaluation-b")],
        )
    )
    assert report["usage"]["unique_attempts"] == 1
    assert report["usage"]["duplicate_attempts_ignored"] == 1
    assert report["usage"]["token_delta_total"] == 40
    assert report["accepted_work"]["unique_acceptance_receipts"] == 2
    assert report["accepted_work"]["historical_accepted_contract_revisions"] == 1


def test_conflicting_duplicate_usage_attempt_is_refused():
    with pytest.raises(InputError, match="conflicting duplicate usage attempt"):
        build_report(_document(usage=[_usage(), _usage(cumulative_tokens=141)]))


def test_cumulative_missing_and_reset_baselines_report_partial_coverage():
    report = build_report(
        _document(
            usage=[
                _usage(attempt_id="complete", cumulative_tokens=130, baseline_tokens=100),
                _usage(attempt_id="missing", cumulative_tokens=20, baseline_tokens=None),
                _usage(attempt_id="reset", cumulative_tokens=5, baseline_tokens=50),
            ]
        )
    )
    usage = report["usage"]
    assert usage["coverage_state"] == "partial_observation"
    assert usage["attempts_with_observed_delta"] == 1
    assert usage["attempts_missing_baseline"] == 1
    assert usage["attempts_with_counter_reset"] == 1
    assert usage["token_delta_total"] is None
    assert usage["observed_partial_token_delta_total"] == 30
    assert {row["delta_state"] for row in usage["rows"]} == {
        "observed_delta", "missing_baseline", "counter_reset"
    }


def test_complete_cumulative_coverage_has_a_total():
    report = build_report(
        _document(
            usage=[
                _usage(attempt_id="a", cumulative_tokens=11, baseline_tokens=1),
                _usage(attempt_id="b", cumulative_tokens=30, baseline_tokens=10),
            ]
        )
    )
    assert report["usage"]["coverage_state"] == "complete_observation"
    assert report["usage"]["token_delta_total"] == 30
    assert report["usage"]["observed_partial_token_delta_total"] is None


def test_late_observed_attempt_is_retained_without_rewriting_coverage():
    report = build_report(
        _document(
            usage=[
                _usage(attempt_id="current", observed_at="2026-09-25T07:10:00Z", cumulative_tokens=30, baseline_tokens=10),
                _usage(attempt_id="late", observed_at="2026-09-25T06:00:00Z", cumulative_tokens=14, baseline_tokens=4),
            ]
        )
    )
    assert report["usage"]["unique_attempts"] == 2
    assert report["usage"]["coverage_state"] == "complete_observation"
    assert report["usage"]["token_delta_total"] == 30
    assert {row["attempt_id"] for row in report["usage"]["rows"]} == {"current", "late"}


def test_reopened_revision_is_not_active_accepted_work():
    report = build_report(
        _document(
            accepted=[
                _accepted(observed_at="2026-09-25T07:01:00Z"),
                _accepted(state="reopened", observed_at="2026-09-25T07:02:00Z"),
                _accepted(revision="2", artifact_id="artifact-b", evaluation_id="evaluation-b", observed_at="2026-09-25T07:03:00Z"),
            ]
        )
    )
    accepted = report["accepted_work"]
    assert accepted["unique_acceptance_receipts"] == 2
    assert accepted["historical_accepted_contract_revisions"] == 2
    assert accepted["active_accepted_contract_revisions"] == 1
    assert accepted["active_reopened_contract_revisions"] == 1
    assert {(row["revision"], row["state"]) for row in accepted["active_rows"]} == {
        ("1", "reopened"), ("2", "accepted")
    }


def test_later_duplicate_acceptance_cannot_resurrect_a_reopened_revision():
    report = build_report(
        _document(
            accepted=[
                _accepted(observed_at="2026-09-25T07:01:00Z"),
                _accepted(state="reopened", observed_at="2026-09-25T07:02:00Z"),
                _accepted(observed_at="2026-09-25T07:03:00Z"),
            ]
        )
    )
    accepted = report["accepted_work"]
    assert accepted["duplicate_acceptance_receipts_ignored"] == 1
    assert accepted["active_accepted_contract_revisions"] == 0
    assert accepted["active_reopened_contract_revisions"] == 1
    assert accepted["active_rows"] == [
        {
            "contract_id": "contract-a",
            "revision": "1",
            "state": "reopened",
            "artifact_id": "artifact-a",
            "evaluation_id": "evaluation-a",
            "observed_at": "2026-09-25T07:02:00Z",
        }
    ]


def test_acceptance_replay_order_cannot_resurrect_reopened_work():
    rows = [_accepted(observed_at="2026-09-25T07:01:00Z"),
            _accepted(state="reopened", observed_at="2026-09-25T07:02:00Z"),
            _accepted(observed_at="2026-09-25T07:03:00Z")]
    for ordering in permutations(rows):
        report = build_report(_document(accepted=list(ordering)))
        assert report['accepted_work']['active_accepted_contract_revisions'] == 0


def test_money_is_null_when_unobserved_and_currencies_never_combine():
    unobserved = build_report(_document())
    assert unobserved["money"]["observed_by_currency"] is None
    assert unobserved["money"]["combined_total"] is None
    observed = build_report(
        _document(
            usage=[
                _usage(attempt_id="usd", monetary={"amount": 1.5, "currency": "USD"}),
                _usage(attempt_id="eur", monetary={"amount": 2, "currency": "EUR"}),
            ]
        )
    )
    assert observed["money"]["observed_by_currency"] == {"EUR": 2.0, "USD": 1.5}
    assert observed["money"]["combined_total"] is None
    assert observed["ranking"] is None
    assert observed["execution"]["performed"] is False


def test_money_aggregate_overflow_is_refused_before_a_nonfinite_report():
    with pytest.raises(InputError, match="monetary aggregate must remain finite"):
        build_report(
            _document(
                usage=[
                    _usage(attempt_id="one", monetary={"amount": 1.7e308, "currency": "USD"}),
                    _usage(attempt_id="two", monetary={"amount": 1.7e308, "currency": "USD"}),
                ]
            )
        )


@pytest.mark.parametrize(
    "text",
    [
        '{"schema":"wd.work-ledger-input.v1","schema":"other"}',
        '{"schema":"wd.work-ledger-input.v1","usage_attempts":[],"accepted_work":[],"x":NaN}',
    ],
)
def test_strict_json_refuses_duplicate_and_nonfinite_values(text):
    with pytest.raises(InputError):
        strict_loads(text)


def test_invalid_json_is_a_safe_input_error():
    with pytest.raises(InputError, match="invalid JSON input"):
        strict_loads('{"schema":')


@pytest.mark.parametrize(
    "mutation, error",
    [
        (lambda item: item.update(observed_at="2026-09-25T07:00:00"), "timezone"),
        (lambda item: item.update(cumulative_tokens=True), "non-negative integer"),
        (lambda item: item.update(extra="no"), "unsupported fields"),
    ],
)
def test_usage_schema_is_strict(mutation, error):
    item = _usage()
    mutation(item)
    with pytest.raises(InputError, match=error):
        build_report(_document(usage=[item]))


def test_unknown_field_error_does_not_echo_untrusted_field_names():
    marker = "secret-field-name-must-not-appear"
    item = _usage(**{marker: "value"})
    with pytest.raises(InputError) as raised:
        build_report(_document(usage=[item]))
    assert str(raised.value) == "usage_attempts[0] has unsupported fields"
    assert marker not in str(raised.value)


def test_load_document_reads_no_more_than_the_bounded_limit(monkeypatch):
    payload = json.dumps(_document()).encode("utf-8")
    observed_sizes = []

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *unused):
            return False

        def read(self, size):
            observed_sizes.append(size)
            return payload

    monkeypatch.setattr(Path, "open", lambda *unused, **kwargs: Probe())
    assert load_document(Path("ignored.json"), max_bytes=len(payload)) == _document()
    assert observed_sizes == [len(payload) + 1]


def test_coverage_is_explicitly_limited_to_supplied_rows():
    usage = build_report(_document())["usage"]
    assert usage["observation_scope"] == "supplied_rows_only"
    assert "cannot establish that all work is accounted for" in usage["coverage_note"]


def test_cli_is_read_only_and_returns_a_report(tmp_path):
    source = tmp_path / "ledger.json"
    source.write_text(json.dumps(_document()), encoding="utf-8")
    before = sorted(path.name for path in tmp_path.iterdir())
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(source)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["schema"] == "wd.work-ledger-report.v1"
    assert sorted(path.name for path in tmp_path.iterdir()) == before
