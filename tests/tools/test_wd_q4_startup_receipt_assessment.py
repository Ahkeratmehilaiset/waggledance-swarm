"""Synthetic startup receipt assessment; never starts, deploys or restores lanes."""
import copy
import os

import pytest


LANES = ("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2")
MANIFEST = "a" * 64
DESCRIPTOR = "b" * 64
HELPER = "e" * 64
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
        "helper_sha256": HELPER, "helper_env": "verified", "helper_status": "EXITED",
        "cleanup": "CONFIRMED", "reason": ("content_delivered_unknown_freshness"
                                              if lane == "codex-tools-1" else "content_delivered"),
        "route": "default_caller_cli",
        "ts_utc": "2026-11-11T10:00:30Z", "outcome": "success", "exit_code": 0,
        "inner_status": "ANSWER", "result_count": 1, "answer_sha256": "f" * 64,
        "call_id": "f" * 32, "delivered_items": 1, "delivered_chars": 5,
        "delivered_sha256": "c" * 64, "refs": ["d" * 64],
    }


def assess(records, *, now=BEFORE, runs=RUNS, manifest=MANIFEST, descriptor=DESCRIPTOR,
           observed=OBSERVED, started=START, helper=HELPER):
    from tools.wd_q4_startup_receipt_assessment import assess_startup_receipts

    return assess_startup_receipts(
        expected_runs=runs, expected_manifest_sha256=manifest,
        expected_descriptor_sha256=descriptor, expected_helper_sha256=helper, records=records,
        observed_at_utc=observed, started_at_utc=started, deadline_utc=DEADLINE, now_utc=now,
    )


def all_records():
    return {lane: receipt(lane) for lane in LANES}


def test_five_current_receipts_pass_but_do_not_claim_context_consumption():
    result = assess(all_records())
    assert result["decision"] == "RECEIPTS_OK"
    assert result["agent_context_verified"] is False
    assert result["source_refs_verified"] is False
    assert result["observation_times_authenticated"] is False
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
    ("helper_sha256", "0" * 64), ("helper_env", "unknown"), ("helper_status", "UNKNOWN"),
    ("cleanup", "UNKNOWN"), ("reason", "other"), ("route", "other"),
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


def test_invalid_helper_expectation_never_turns_into_a_pass():
    with pytest.raises(ValueError):
        assess(all_records(), helper="invalid")


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


@pytest.mark.parametrize("observation,expected", [
    ("2026-11-11T10:00:30.500000Z", "ROLLBACK_REQUIRED"),
    ("2026-11-11T10:00:30.900000Z", "RECEIPTS_OK"),
])
def test_observation_must_not_precede_precise_launch_start(observation, expected):
    observed = dict(OBSERVED)
    observed["codex-lead-1"] = observation
    assert assess(all_records(), started="2026-11-11T10:00:30.900000Z", observed=observed)["decision"] == expected


@pytest.mark.parametrize("field,value", [
    ("result_count", None), ("result_count", True), ("result_count", 0), ("result_count", 2),
    ("result_count", 101), ("answer_sha256", None), ("answer_sha256", "invalid"),
    ("call_id", None), ("call_id", "invalid"), ("delivered_items", 2),
    ("delivered_chars", 24001),
])
def test_receipt_requires_complete_consistent_bounded_answer_metadata(field, value):
    records = all_records()
    records["codex-lead-1"][field] = value
    assert assess(records)["decision"] == "ROLLBACK_REQUIRED"


@pytest.mark.parametrize("timestamp", ["9999-12-31T23:59:59Z", "9999-12-31T23:59:59.999999Z"])
def test_extreme_future_timestamp_is_a_refusal_not_an_exception(timestamp):
    records = all_records()
    records["codex-lead-1"]["ts_utc"] = timestamp
    assert assess(records)["decision"] == "ROLLBACK_REQUIRED"


def test_fractional_receipt_from_previous_second_is_not_a_valid_launcher_receipt():
    records = all_records()
    records["codex-lead-1"]["ts_utc"] = "2026-11-11T10:00:29.999999Z"
    observed = dict(OBSERVED)
    observed["codex-lead-1"] = "2026-11-11T10:00:30.900000Z"
    assert assess(records, started="2026-11-11T10:00:30.900000Z", observed=observed)["decision"] == "ROLLBACK_REQUIRED"


def test_tools_accepts_its_actual_launcher_success_reason():
    records = all_records()
    records["codex-tools-1"]["reason"] = "content_delivered_unknown_freshness"
    assert assess(records)["decision"] == "RECEIPTS_OK"


def test_tools_does_not_accept_another_lanes_success_reason():
    records = all_records()
    records["codex-tools-1"]["reason"] = "content_delivered"
    assert assess(records)["decision"] == "ROLLBACK_REQUIRED"


@pytest.mark.parametrize("lane", ["codex-lead-1", "codex-tools-1"])
@pytest.mark.skipif(os.name != "nt", reason="Synthetic launcher execution requires Windows PowerShell")
def test_assessment_accepts_real_launcher_receipt_from_synthetic_content(tmp_path, monkeypatch, lane):
    """Exercise launcher receipt format only; this is not evidence of a live startup."""
    import hashlib
    import json
    from datetime import datetime, timedelta, timezone

    if lane == "codex-tools-1":
        # The repository's tests directory is a namespace package; an unrelated
        # installed ``tests`` package can otherwise shadow it on Windows.
        import sys
        import types
        from pathlib import Path
        tests_package = types.ModuleType("tests")
        tests_package.__path__ = [str(Path(__file__).resolve().parents[1])]
        tools_package = types.ModuleType("tests.tools")
        tools_package.__path__ = [str(Path(__file__).resolve().parent)]
        monkeypatch.setitem(sys.modules, "tests", tests_package)
        monkeypatch.setitem(sys.modules, "tests.tools", tools_package)
        import test_wd_q4_shared_tools_startup as launcher

        fx = launcher.startup_read_fixture(tmp_path, lane, mode="producer_bytes")
        packet = launcher._content_delivery_packet(fx["descriptor_pin"])
        packet["answer_sha256"] = hashlib.sha256(launcher._content_canonical(packet["answer"])).hexdigest()
        (fx["bundle"] / "producer-response.bin").write_bytes(launcher._content_canonical(packet) + b"\n")
        fx["env_prelude"] += ("\n[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('fi-FI');"
                              "$env:AGENT_BRIDGE_AGENT='codex-tools-1';$env:AGENT_BRIDGE_OWNER_TOKEN='synthetic-owner-token'")
        original = launcher.startup_read_call
        monkeypatch.setattr(launcher, "startup_read_call", lambda *a, **kw:
                            "[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('fi-FI')\n" +
                            original(*a, **kw).replace(" -TimeoutSeconds", " -StartupRunId 'run-20261111' -TimeoutSeconds", 1))
        launcher._content_observation(fx, launcher.LANE_TEST_SHELLS[0])
    else:
        import test_wd_q4_shared_agent_startup as launcher

        raw = launcher.delivery_bytes(launcher.delivery_envelope(lane, texts=("synthetic-content",)))
        original = launcher.startup_read_call
        monkeypatch.setattr(launcher, "startup_read_call", lambda *a, **kw:
                            "[Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('fi-FI')\n" +
                            original(*a, **kw).replace(" -TimeoutSeconds", " -StartupRunId 'run-20261111' -TimeoutSeconds", 1))
        fx, _ = launcher.run_startup_read(tmp_path, lane=lane, mode="producer_bytes", producer_bytes=raw,
                                          owner_token=launcher.OWNER_TOKEN)

    import re
    actual = json.loads(fx["record"].read_bytes())
    seen = json.loads(fx["seen"].read_text(encoding="utf-8-sig"))
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", actual["ts_utc"]), actual["ts_utc"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", json.loads(seen["query"])["time"])
    assert actual["outcome"] == "success"
    now = datetime.now(timezone.utc)
    stamp = lambda value: value.isoformat().replace("+00:00", "Z")
    records = all_records()
    for record in records.values():
        record.update(ts_utc=actual["ts_utc"], expected_manifest_sha256=actual["expected_manifest_sha256"],
                      descriptor_sha256=actual["descriptor_sha256"], helper_sha256=actual["helper_sha256"])
    records[lane] = actual
    from tools.wd_q4_startup_receipt_assessment import assess_startup_receipts
    result = assess_startup_receipts(
        expected_runs=RUNS, expected_manifest_sha256=actual["expected_manifest_sha256"],
        expected_descriptor_sha256=actual["descriptor_sha256"], expected_helper_sha256=actual["helper_sha256"],
        records=records, observed_at_utc={name: stamp(now) for name in LANES},
        started_at_utc=stamp(now - timedelta(minutes=1)), deadline_utc=stamp(now + timedelta(minutes=1)), now_utc=stamp(now))
    assert result["decision"] == "RECEIPTS_OK", result
    assert result["agent_context_verified"] is False
