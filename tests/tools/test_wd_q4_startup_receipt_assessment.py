"""Synthetic startup receipt assessment; never starts, deploys or restores lanes."""
import copy

import pytest


LANES = ("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2")
MANIFEST = "a" * 64
DESCRIPTOR = "b" * 64
RUNS = {lane: "run-20261111" for lane in LANES}
START = "2026-11-11T10:00:00Z"
BEFORE = "2026-11-11T10:01:00Z"
DEADLINE = "2026-11-11T10:02:00Z"
AFTER = "2026-11-11T10:03:00Z"
OBSERVED = {lane: "2026-11-11T10:00:45Z" for lane in LANES}


def receipt(lane):
    return {
        "schema": "wd.mind-startup-read.v1", "lane": lane, "run_id": RUNS[lane],
        "expected_manifest_sha256": MANIFEST, "descriptor_sha256": DESCRIPTOR,
        "ts_utc": "2026-11-11T10:00:30Z", "outcome": "success", "exit_code": 0,
        "inner_status": "ANSWER", "delivered_items": 1, "delivered_chars": 5,
        "delivered_sha256": "c" * 64, "refs": ["d" * 64],
    }


def assess(records, *, now=BEFORE, runs=RUNS, manifest=MANIFEST, descriptor=DESCRIPTOR,
           observed=OBSERVED, started=START):
    from tools.wd_q4_startup_receipt_assessment import assess_startup_receipts

    return assess_startup_receipts(
        expected_runs=runs, expected_manifest_sha256=manifest,
        expected_descriptor_sha256=descriptor, records=records,
        observed_at_utc=observed, started_at_utc=started, deadline_utc=DEADLINE, now_utc=now,
    )


def all_records():
    return {lane: receipt(lane) for lane in LANES}


def test_five_current_receipts_pass_but_do_not_claim_context_consumption():
    result = assess(all_records())
    assert result["decision"] == "RECEIPTS_OK"
    assert result["agent_context_verified"] is False
    assert all(result["lanes"][lane] == "PASS" for lane in LANES)


@pytest.mark.parametrize("missing", LANES)
def test_missing_receipt_waits_until_deadline_then_requires_rollback(missing):
    records = all_records()
    del records[missing]
    assert assess(records)["decision"] == "WAIT"
    assert assess(records, now=DEADLINE)["decision"] == "ROLLBACK_REQUIRED"


@pytest.mark.parametrize("field,old", [
    ("run_id", "previous-run"), ("ts_utc", "2026-11-11T09:59:59Z"),
])
def test_prior_boot_receipt_cannot_prove_current_start(field, old):
    records = all_records()
    records["codex-lead-1"][field] = old
    assert assess(records)["decision"] == "WAIT"
    assert assess(records, now=AFTER)["decision"] == "ROLLBACK_REQUIRED"


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("lane", "fable-5"), ("ts_utc", "invalid"),
    ("ts_utc", "2026-11-11T10:01:01Z"),
    ("expected_manifest_sha256", "0" * 64), ("descriptor_sha256", "0" * 64),
    ("outcome", "unknown"), ("exit_code", 1), ("exit_code", False),
    ("inner_status", "ABSTAIN"), ("delivered_items", 0), ("delivered_items", True),
    ("delivered_chars", 0), ("delivered_chars", True),
    ("delivered_sha256", ""), ("refs", []), ("refs", ["invalid"]),
])
def test_current_run_invalid_or_unsuccessful_receipt_requires_rollback(field, value):
    records = all_records()
    records["codex-tools-1"][field] = value
    assert assess(records)["decision"] == "ROLLBACK_REQUIRED"


def test_no_valid_first_page_or_unknown_lane_can_replace_all_five():
    unknown = receipt("codex-lead-1")
    unknown["lane"] = "unknown"
    records = {"codex-lead-1": receipt("codex-lead-1"), "unknown": unknown}
    assert assess(records)["decision"] == "WAIT"


@pytest.mark.parametrize("runs,manifest,descriptor", [
    ({"codex-lead-1": "run"}, MANIFEST, DESCRIPTOR),
    ({**RUNS, "codex-lead-1": ""}, MANIFEST, DESCRIPTOR),
    (RUNS, "invalid", DESCRIPTOR), (RUNS, MANIFEST, "invalid"),
])
def test_invalid_expectations_never_turn_into_a_pass(runs, manifest, descriptor):
    with pytest.raises(ValueError):
        assess(all_records(), runs=runs, manifest=manifest, descriptor=descriptor)


def test_assessment_does_not_mutate_input_records():
    records = all_records()
    original = copy.deepcopy(records)
    assess(records)
    assert records == original


def test_complete_receipts_written_before_deadline_remain_evaluable_after_it():
    assert assess(all_records(), now=AFTER)["decision"] == "RECEIPTS_OK"


def test_start_timestamp_alone_cannot_prove_receipt_was_observed_before_deadline():
    observed = dict(OBSERVED)
    observed["codex-lead-1"] = AFTER
    assert assess(all_records(), now=AFTER, observed=observed)["decision"] == "ROLLBACK_REQUIRED"


def test_unobserved_receipt_waits_then_requires_rollback():
    observed = dict(OBSERVED)
    del observed["fable-5"]
    assert assess(all_records(), observed=observed)["decision"] == "WAIT"
    assert assess(all_records(), now=AFTER, observed=observed)["decision"] == "ROLLBACK_REQUIRED"


def test_second_precision_receipt_can_match_subsecond_start_anchor():
    assert assess(all_records(), started="2026-11-11T10:00:30.500000Z")["decision"] == "RECEIPTS_OK"
