# SPDX-License-Identifier: BUSL-1.1
"""D2 lane profile record and D3 session binding: fail closed, shadow never applies."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools.lane_profile_catalog import load_catalog
from tools.lane_profile_record import (
    RecordError,
    launch_decision,
    read_record,
    record_path,
    validate_record,
    write_record,
)
from tools.lane_profile_binding import bind_lane, process_instance_live

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
THREAD = "7a470a08-cca9-408f-ae5b-1cf9ae6103f9"
LEAD_UUID = "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def record(**overrides) -> dict:
    base = {
        "schema": "wd.lane-profile-record.v1", "lane": "claude-rco-1",
        "desired_profile": "claude-opus-5-5-xhigh", "previous_profile": "claude-sonnet-5-xhigh",
        "reason": "review load", "requested_by": {"agent": "codex-lead-1", "agent_uuid": LEAD_UUID,
                                                  "session_id": "wd-lane-codex-lead-1-x"},
        "request_id": "req-1", "transition_id": "tid-1",
        "created_at": iso(NOW - timedelta(minutes=5)), "expires_at": iso(NOW + timedelta(hours=1)),
        "catalog_sha256": DIGEST, "launched": None,
    }
    base.update(overrides)
    return base


def launched(pid=4242, started=NOW - timedelta(minutes=1), thread=THREAD) -> dict:
    return {"native_thread_id": thread, "pid": pid, "process_started_at": iso(started),
            "session_id": "wd-lane-claude-rco-1-y", "run_id": "wd-lane-claude-rco-1-y",
            "launched_at": iso(started)}


# ---------------------------------------------------------------- D2 record

def test_valid_raise_record_passes():
    assert validate_record(record(), CATALOG, DIGEST, now=NOW)["desired_profile"] == "claude-opus-5-5-xhigh"


@pytest.mark.parametrize("change,match", [
    (dict(schema="wd.lane-profile-record.v0"), "schema"),
    (dict(lane="grok-scout-1"), "not a catalog lane"),
    (dict(catalog_sha256="0" * 64), "different catalog"),
    (dict(catalog_sha256="ABC"), "lowercase sha256"),
    (dict(expires_at=iso(NOW - timedelta(seconds=1))), "expired"),
    (dict(created_at=iso(NOW + timedelta(minutes=1)), expires_at=iso(NOW + timedelta(hours=2))), "future"),
    (dict(created_at=iso(NOW - timedelta(minutes=5)), expires_at=iso(NOW + timedelta(hours=30))), "at most 24 h"),
    (dict(expires_at="2026-09-26T13:00:00"), "aware ISO"),
    (dict(desired_profile="codex-gpt-6-sol-high"), "not an allowed profile"),
    # A reviewer lowering is an operator decision, never a record.
    (dict(desired_profile="claude-sonnet-5-xhigh", previous_profile="claude-opus-5-5-xhigh"), "operator ack"),
    (dict(requested_by={"agent": "operator", "agent_uuid": LEAD_UUID, "session_id": "s"}), "requested_by"),
    (dict(reason=" "), "reason required"),
    (dict(launched={"pid": 1}), "launched"),
    (dict(launched=dict(launched(), pid=0)), "positive int"),
    (dict(launched=dict(launched(), native_thread_id="NOT-A-UUID")), "UUID"),
])
def test_unsafe_records_are_refused(change, match):
    with pytest.raises(RecordError, match=match):
        validate_record(record(**change), CATALOG, DIGEST, now=NOW)


def test_extra_field_is_refused():
    with pytest.raises(RecordError, match="exactly"):
        validate_record(dict(record(), extra=1), CATALOG, DIGEST, now=NOW)


def test_record_path_refuses_traversal_and_unknown_lanes(tmp_path):
    assert record_path(tmp_path, "fable-5") == tmp_path / "lane_profiles" / "fable-5.json"
    for lane in ("..\\x", "../x", "grok-scout-1", "fable-5.json"):
        with pytest.raises(RecordError):
            record_path(tmp_path, lane)


def test_write_is_atomic_and_round_trips(tmp_path):
    path = record_path(tmp_path, "claude-rco-1")
    write_record(path, record())
    assert read_record(path) == record()
    assert [p.name for p in path.parent.iterdir()] == ["claude-rco-1.json"]


def test_read_refuses_missing_oversize_and_nan(tmp_path):
    path = tmp_path / "r.json"
    with pytest.raises(RecordError, match="no record"):
        read_record(path)
    path.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(RecordError, match="size bound"):
        read_record(path)
    path.write_text('{"x": NaN}', encoding="utf-8")
    with pytest.raises(RecordError, match="non-finite"):
        read_record(path)


# ------------------------------------------------------- launcher decision

def catalog_with(fleet_mode: str, policy_mode: str = "shadow") -> dict:
    catalog = json.loads(json.dumps(CATALOG))
    catalog["fleet"]["mode"] = fleet_mode
    catalog["capacity_policy"]["mode"] = policy_mode
    return catalog


def test_no_record_launches_native_silently(tmp_path):
    decision = launch_decision(tmp_path, "claude-rco-1", CATALOG, DIGEST, now=NOW)
    assert decision["action"] == "native" and decision["fallback_event"] is None
    assert decision["would_apply"] is None


def test_shadow_logs_would_apply_and_never_applies(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-1"), record())
    decision = launch_decision(tmp_path, "claude-rco-1", catalog_with("auto"), DIGEST, now=NOW)
    assert decision["mode"] == "shadow"
    assert decision["action"] == "native" and decision["profile"] is None
    assert decision["would_apply"]["model"] == "claude-opus-5-5"


def test_approve_fails_closed_without_a_verifiable_ack(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-1"), record())
    decision = launch_decision(tmp_path, "claude-rco-1", catalog_with("approve", "approve"), DIGEST, now=NOW)
    assert decision["action"] == "native"
    assert decision["fallback_event"]["reason"] == "operator_ack_unverifiable"


def test_auto_applies_only_a_valid_record(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-1"), record())
    decision = launch_decision(tmp_path, "claude-rco-1", catalog_with("auto", "auto"), DIGEST, now=NOW)
    assert decision["action"] == "apply"
    assert decision["profile"] == {"profile_id": "claude-opus-5-5-xhigh", "provider": "claude",
                                   "model": "claude-opus-5-5", "effort": "xhigh", "transition_id": "tid-1"}


@pytest.mark.parametrize("change", [
    dict(expires_at=iso(NOW - timedelta(seconds=1))), dict(catalog_sha256="1" * 64),
    dict(desired_profile="claude-sonnet-5-xhigh", previous_profile="claude-opus-5-5-xhigh")])
def test_unusable_record_falls_back_to_native_with_an_event(tmp_path, change):
    write_record(record_path(tmp_path, "claude-rco-1"), record(**change))
    decision = launch_decision(tmp_path, "claude-rco-1", catalog_with("auto", "auto"), DIGEST, now=NOW)
    assert decision["action"] == "native"
    assert decision["fallback_event"]["reason"] == "record_unusable"


def test_record_filed_under_another_lane_is_not_applied(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-2"), record(lane="claude-rco-1"))
    decision = launch_decision(tmp_path, "claude-rco-2", catalog_with("auto", "auto"), DIGEST, now=NOW)
    assert decision["action"] == "native"
    assert decision["fallback_event"]["detail"] == "record names another lane"


# --------------------------------------------------------------- D3 binding

LIVE = {4242: iso(NOW - timedelta(minutes=1))}


def claude_obs(thread=THREAD, model="claude-opus-5-5[1m]", effort="xhigh", at=NOW):
    return [{"provider": "claude", "native_thread_id": thread, "model": model, "effort": effort,
             "observed_at": iso(at)}]


def test_process_instance_rules():
    rec = launched()
    assert process_instance_live(rec, LIVE) == (True, "process_instance_matches")
    assert process_instance_live(rec, {})[1] == "recorded_process_not_live"
    assert process_instance_live(rec, {4242: iso(NOW)})[1] == "pid_reused_by_another_process"
    assert process_instance_live(rec, {4242: "garbage"})[1] == "process_epoch_unparseable"


def test_claude_binding_valid_and_profile_match():
    result = bind_lane(record(launched=launched()), CATALOG, live_processes=LIVE,
                       claude_observations=claude_obs())
    assert result["session_identity"] == "valid"
    assert result["profile_observed"] == "match"
    assert result["observed_model_raw"] == "claude-opus-5-5[1m]"


@pytest.mark.parametrize("kwargs,identity,reason", [
    (dict(live_processes={}), "invalid", "recorded_process_not_live"),
    (dict(live_processes={4242: iso(NOW)}), "invalid", "pid_reused_by_another_process"),
    (dict(claude_observations=claude_obs(thread="2409ab15-35c3-4920-9938-a8df3903de6f")), "unbound",
     "no_observation_for_recorded_thread"),
    (dict(claude_observations=claude_obs(at=NOW - timedelta(minutes=2))), "unbound",
     "no_observation_since_launch"),
    (dict(claude_observations=[]), "unbound", "no_observation_for_recorded_thread"),
])
def test_claude_binding_fails_closed(kwargs, identity, reason):
    args = dict(live_processes=LIVE, claude_observations=claude_obs())
    args.update(kwargs)
    result = bind_lane(record(launched=launched()), CATALOG, **args)
    assert (result["session_identity"], result["session_reason"]) == (identity, reason)
    assert result["profile_observed"] == "unverified"


def test_unlaunched_record_is_unbound():
    result = bind_lane(record(), CATALOG, live_processes=LIVE, claude_observations=claude_obs())
    assert result["session_identity"] == "unbound"
    assert result["session_reason"] == "launcher_record_not_written"


def test_claude_profile_mismatch_is_reported():
    result = bind_lane(record(launched=launched()), CATALOG, live_processes=LIVE,
                       claude_observations=claude_obs(model="claude-sonnet-5"))
    assert result["session_identity"] == "valid" and result["profile_observed"] == "mismatch"


def codex_record() -> dict:
    return record(lane="codex-tools-1", desired_profile="codex-gpt-6-sol-high",
                  previous_profile="codex-gpt-5.6-terra-medium", launched=launched())


def test_codex_binding_requires_a_turn_after_launch():
    native = {"native_thread_id": THREAD, "observed_at": iso(NOW), "model": "gpt-6-sol", "effort": "high"}
    result = bind_lane(codex_record(), CATALOG, live_processes=LIVE, codex_native=native)
    assert (result["session_identity"], result["profile_observed"]) == ("valid", "match")
    stale = dict(native, observed_at=iso(NOW - timedelta(minutes=5)))
    result = bind_lane(codex_record(), CATALOG, live_processes=LIVE, codex_native=stale)
    assert (result["session_identity"], result["session_reason"]) == ("unbound", "no_turn_since_launch")
    other = dict(native, native_thread_id="2409ab15-35c3-4920-9938-a8df3903de6f")
    result = bind_lane(codex_record(), CATALOG, live_processes=LIVE, codex_native=other)
    assert result["session_reason"] == "no_rollout_for_recorded_thread"


def test_quota_binding_is_independent_of_session_binding():
    rows = [{"provider": "claude", "limit_id": "claude", "account_pool": "operator-claude-subscription"}]
    # No session evidence at all, yet the quota row binds: the two states never merge.
    result = bind_lane(record(), CATALOG, live_processes={}, quota_rows=rows)
    assert result["session_identity"] == "unbound"
    assert result["quota_pool_binding"] == "valid"
    other = [dict(rows[0], account_pool="someone-else")]
    assert bind_lane(record(), CATALOG, live_processes={}, quota_rows=other)["quota_pool_binding"] == "invalid"
    unknown = [dict(rows[0], account_pool=None)]
    assert bind_lane(record(), CATALOG, live_processes={}, quota_rows=unknown)["quota_pool_binding"] == "invalid"
    assert bind_lane(record(), CATALOG, live_processes={})["quota_pool_binding"] == "unverified"


def test_codex_quota_row_never_binds_a_claude_profile():
    rows = [{"provider": "codex", "limit_id": "codex", "account_pool": "operator-claude-subscription"}]
    assert bind_lane(record(), CATALOG, live_processes={}, quota_rows=rows)["quota_pool_binding"] == "unverified"


def test_same_model_with_a_different_effort_is_a_mismatch():
    result = bind_lane(record(launched=launched()), CATALOG, live_processes=LIVE,
                       claude_observations=claude_obs(effort="medium"))
    assert result["profile_observed"] == "mismatch"


def test_quota_binding_needs_every_limit_of_the_profile():
    catalog = json.loads(json.dumps(CATALOG))
    profile = catalog["capacity_policy"]["profiles"]["codex-gpt-6-sol-high"]
    profile["limits"] = [{"id": "codex", "windows": ["primary"]},
                         {"id": "codex-bonus", "windows": ["primary"]}]
    rec = codex_record()
    one = [{"provider": "codex", "limit_id": "codex", "account_pool": "operator-chatgpt-subscription"}]
    result = bind_lane(rec, catalog, live_processes={}, quota_rows=one)
    assert (result["quota_pool_binding"], result["quota_reason"]) == ("unverified", "not_every_limit_observed")
    both = one + [dict(one[0], limit_id="codex-bonus")]
    assert bind_lane(rec, catalog, live_processes={}, quota_rows=both)["quota_pool_binding"] == "valid"


def gap_launched() -> dict:
    """Lead PR1737-B1 reproducer: process started 11:50, launcher recorded 11:59."""
    rec = launched(started=NOW - timedelta(minutes=10))
    rec["launched_at"] = iso(NOW - timedelta(minutes=1))
    return rec


GAP_LIVE = {4242: iso(NOW - timedelta(minutes=10))}


def test_claude_evidence_between_process_start_and_launch_is_not_valid():
    rec = record(launched=gap_launched())
    mid = claude_obs(at=NOW - timedelta(minutes=5))
    result = bind_lane(rec, CATALOG, live_processes=GAP_LIVE, claude_observations=mid)
    assert (result["session_identity"], result["session_reason"]) == ("unbound", "no_observation_since_launch")
    assert result["profile_observed"] == "unverified"
    after = claude_obs(at=NOW)
    assert bind_lane(rec, CATALOG, live_processes=GAP_LIVE, claude_observations=after)["session_identity"] == "valid"


def test_codex_turn_between_process_start_and_launch_is_not_valid():
    rec = dict(codex_record(), launched=gap_launched())
    mid = {"native_thread_id": THREAD, "observed_at": iso(NOW - timedelta(minutes=5)),
           "model": "gpt-6-sol", "effort": "high"}
    result = bind_lane(rec, CATALOG, live_processes=GAP_LIVE, codex_native=mid)
    assert (result["session_identity"], result["session_reason"]) == ("unbound", "no_turn_since_launch")
    at_launch = dict(mid, observed_at=gap_launched()["launched_at"])
    assert bind_lane(rec, CATALOG, live_processes=GAP_LIVE, codex_native=at_launch)["session_identity"] == "unbound"
    after = dict(mid, observed_at=iso(NOW))
    assert bind_lane(rec, CATALOG, live_processes=GAP_LIVE, codex_native=after)["session_identity"] == "valid"


def test_inverted_launch_timestamps_bind_nothing():
    rec = launched(started=NOW - timedelta(minutes=1))
    rec["launched_at"] = iso(NOW - timedelta(minutes=5))
    result = bind_lane(record(launched=rec), CATALOG, live_processes=LIVE, claude_observations=claude_obs())
    assert result["session_identity"] == "unbound"


@pytest.mark.parametrize("launched_at,match", [
    (NOW - timedelta(minutes=20), "precedes the process start"),
    (NOW + timedelta(minutes=1), "in the future"),
])
def test_record_refuses_bad_launch_ordering(launched_at, match):
    rec = gap_launched()
    rec["launched_at"] = iso(launched_at)
    with pytest.raises(RecordError, match=match):
        validate_record(record(launched=rec), CATALOG, DIGEST, now=NOW)
