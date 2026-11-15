import datetime as dt
import hashlib
import json

import pytest

from tools import wd_mind_capacity_status_readonly as target


SOURCE = b"synthetic independently pinned observer source"
PIN = hashlib.sha256(SOURCE).hexdigest()
MANIFEST = "a" * 64
NOW = dt.datetime(2026, 11, 14, 7, 28, 35, tzinfo=dt.timezone.utc)


def summary():
    return {"schema": "wd.capacity-summary.v1", "observed_at": "2026-11-14T07:28:26Z",
        "execution_allowed": False, "collection": None, "note": "synthetic",
        "installation": {"source_commit": "b" * 40, "manifest_sha256": MANIFEST,
                         "source_verified": True, "agent_quota_binding": "unverified"},
        "agents": [{"agent": "codex-lead-1", "provider": "codex",
                    "identity_state": "observed_process_ancestry", "auth_state": "unknown",
                    "native_process_start_utc": "2026-11-14T07:00:00Z",
                    "activity_state": "turn_started", "availability_state": "unknown",
                    "observed_at": "2026-11-14T07:28:29Z", "freshness": "fresh",
                    "source": "codex_native_rollout", "quota_observed_at": "2026-11-14T07:28:29Z",
                    "quota_freshness": "fresh", "observed_quota_state": "observed_headroom",
                    "quota_windows": [{"limit_id": "codex", "name": "primary", "used_percent": 42,
                        "resets_at": 1794644915, "window_duration_minutes": 300, "window_duration_state": "valid"}],
                    "quota_pool_binding": "unverified", "execution_allowed": False}]}


def raw(doc):
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode()


def normalize(doc=None, **kwargs):
    return target.normalize(enabled=True, observer_source_bytes=SOURCE, expected_observer_sha256=PIN,
        expected_manifest_sha256=MANIFEST, summary_bytes=raw(summary() if doc is None else doc),
        now_utc=NOW, **kwargs)


def lane(result, name="codex-lead-1"):
    return next(item for item in result["lanes"] if item["lane"] == name)


def test_observations_are_separate_and_quota_pool_stays_unknown():
    result = normalize()
    lead = lane(result)
    assert result["authority"] == "none" and result["execution_allowed"] is False
    assert result["observer_output_authentication"] == "UNKNOWN"
    assert result["summary_sha256"] == hashlib.sha256(raw(summary())).hexdigest()
    assert lead["activity"]["value"] == "turn_started"
    assert lead["activity"]["observed_at_utc"] == "2026-11-14T07:28:29.000000Z"
    assert lead["activity"]["age_seconds"] == 6
    assert lead["auth"]["value"] == "UNKNOWN"
    assert lead["quota"]["value"] == "observed_headroom" and lead["quota"]["age_seconds"] == 6
    assert lead["quota"]["windows"][0]["used_percent"] == 42
    assert lead["quota"]["windows"][0]["limit_id"] == "codex"
    assert lead["quota"]["windows"][0]["limit_id_sha256"] is None
    assert lead["quota"]["pool_binding"] == "UNKNOWN" and lead["quota"]["independent_capacity"] is False
    assert lead["activity"]["source_label"] == "codex_native_rollout"
    assert "event_ref" not in json.dumps(result)
    assert len(result["lanes"]) == 5
    assert lane(result, "fable-5")["activity"]["value"] == "UNKNOWN"


@pytest.mark.parametrize("stamp,freshness", [("2026-11-14T07:23:34Z", "fresh"),
    ("2026-11-14T07:28:36Z", "fresh"), (None, "fresh"), ("2026-11-14T07:28:29Z", "stale_or_future")])
def test_stale_future_missing_or_observer_stale_is_unknown(stamp, freshness):
    doc = summary()
    doc["agents"][0].update(observed_at=stamp, freshness=freshness)
    assert lane(normalize(doc))["activity"]["value"] == "UNKNOWN"


def test_provider_timestamp_unknown_and_unverified_identity_do_not_become_capacity():
    doc = summary()
    row = doc["agents"][0]
    row.update(agent="fable-5", provider="claude", source="claude_native_hook", activity_state="idle_observed",
               auth_state="authenticated_at_successful_turn", last_successful_turn_at="2026-11-14T07:28:20Z",
               quota_freshness="provider_timestamp_unknown")
    result = lane(normalize(doc), "fable-5")
    assert result["activity"]["value"] == "idle_observed"
    assert result["auth"]["value"] == "authenticated_at_successful_turn"
    assert result["quota"]["value"] == "UNKNOWN"
    row["identity_state"] = "unknown"
    result = lane(normalize(doc), "fable-5")
    assert result["activity"]["value"] == result["auth"]["value"] == "UNKNOWN"


def test_recent_activity_does_not_refresh_old_auth_or_availability():
    doc = summary()
    row = doc["agents"][0]
    row.update(agent="fable-5", provider="claude", source="claude_native_hook", activity_state="work_requested",
        auth_state="authenticated_at_successful_turn", availability_state="successful_turn_observed",
        last_successful_turn_at="2026-11-14T06:00:00Z")
    result = lane(normalize(doc), "fable-5")
    assert result["activity"]["value"] == "work_requested"
    assert result["auth"]["value"] == result["availability"]["value"] == "UNKNOWN"


def test_auth_and_availability_use_distinct_success_timestamp():
    doc = summary()
    row = doc["agents"][0]
    row.update(agent="fable-5", provider="claude", source="claude_native_hook", activity_state="work_requested",
        auth_state="authenticated_at_successful_turn", availability_state="successful_turn_observed",
        observed_at="2026-11-14T07:28:29Z", last_successful_turn_at="2026-11-14T07:27:50Z")
    result = lane(normalize(doc), "fable-5")
    assert result["activity"]["age_seconds"] == 6
    assert result["auth"]["age_seconds"] == result["availability"]["age_seconds"] == 45


def test_even_claimed_verified_pool_has_no_independently_verified_binding():
    doc = summary()
    doc["agents"][0]["quota_pool_binding"] = "verified"
    quota = lane(normalize(doc))["quota"]
    assert quota["value"] == "observed_headroom" and quota["pool_binding"] == "UNKNOWN"


def test_unknown_limit_identifier_is_hashed_without_echoing_raw_text():
    doc = summary()
    synthetic_secret = "synthetic-private-token-abcdefgh9876543210"
    doc["agents"][0]["quota_windows"][0]["limit_id"] = synthetic_secret
    result = normalize(doc)
    window = lane(result)["quota"]["windows"][0]
    assert window["limit_id"] == "UNKNOWN"
    assert window["limit_id_sha256"] == hashlib.sha256(synthetic_secret.encode()).hexdigest()
    assert synthetic_secret not in json.dumps(result)


def test_unrecognized_window_name_is_not_echoed():
    doc = summary()
    doc["agents"][0]["quota_windows"][0]["name"] = "synthetic-private-token-abcdefgh9876543210"
    result = normalize(doc)
    assert lane(result)["quota"]["value"] == "UNKNOWN"
    assert "synthetic-private-token" not in json.dumps(result)


@pytest.mark.parametrize("stamp,freshness", [("2026-11-14T07:23:34Z", "fresh"),
    ("2026-11-14T07:28:36Z", "fresh"), (None, "fresh"), ("2026-11-14T07:28:29Z", "provider_timestamp_unknown")])
def test_stale_future_or_unverifiable_provider_time_hides_quota(stamp, freshness):
    doc = summary()
    doc["agents"][0].update(quota_observed_at=stamp, quota_freshness=freshness)
    quota = lane(normalize(doc))["quota"]
    assert quota["value"] == "UNKNOWN" and quota["windows"] == []


@pytest.mark.parametrize("fault", ["boolean_percent", "negative_percent", "bad_reset", "bad_window", "inconsistent_state"])
def test_malformed_or_contradictory_quota_does_not_show_windows(fault):
    doc = summary()
    window = doc["agents"][0]["quota_windows"][0]
    if fault == "boolean_percent": window["used_percent"] = True
    elif fault == "negative_percent": window["used_percent"] = -1
    elif fault == "bad_reset": window["resets_at"] = -1
    elif fault == "bad_window": window["window_duration_minutes"] = "ten"
    else: window["used_percent"] = 100
    quota = lane(normalize(doc))["quota"]
    assert quota["value"] == "UNKNOWN" and quota["windows"] == []


def test_stale_summary_and_mismatched_source_do_not_create_fresh_observations():
    doc = summary()
    doc["observed_at"] = "2026-11-14T07:00:00Z"
    assert lane(normalize(doc))["activity"]["value"] == "UNKNOWN"
    doc = summary()
    doc["agents"][0]["source"] = "claude_native_hook"
    assert lane(normalize(doc))["activity"]["value"] == "UNKNOWN"


@pytest.mark.parametrize("fault", ["script_pin", "manifest_pin", "source_unverified", "execute_true", "duplicate_lane", "wrong_provider"])
def test_wrong_pin_or_inconsistent_provenance_refuses(fault):
    doc = summary()
    source, pin, manifest = SOURCE, PIN, MANIFEST
    if fault == "script_pin": pin = "0" * 64
    elif fault == "manifest_pin": manifest = "0" * 64
    elif fault == "source_unverified": doc["installation"]["source_verified"] = False
    elif fault == "execute_true": doc["execution_allowed"] = True
    elif fault == "duplicate_lane": doc["agents"].append(dict(doc["agents"][0]))
    else: doc["agents"][0]["provider"] = "claude"
    with pytest.raises(target.CapacityRefused):
        target.normalize(enabled=True, observer_source_bytes=source, expected_observer_sha256=pin,
            expected_manifest_sha256=manifest, summary_bytes=raw(doc), now_utc=NOW)


def test_duplicate_json_keys_default_off_and_no_io(monkeypatch):
    with pytest.raises(target.CapacityRefused): target.normalize()
    def forbidden(*args, **kwargs):
        pytest.fail("normalizer must not open files")
    monkeypatch.setattr("builtins.open", forbidden)
    with pytest.raises(target.CapacityRefused):
        target.normalize(enabled=True, observer_source_bytes=SOURCE, expected_observer_sha256=PIN,
            expected_manifest_sha256=MANIFEST, summary_bytes=b'{"schema":1,"schema":2}', now_utc=NOW)
    assert lane(normalize())["activity"]["value"] == "turn_started"


def test_missing_activity_keeps_quota_separate_from_readiness():
    doc = summary()
    row = doc["agents"][0]
    for key in ("observed_at", "source", "activity_state"):
        row.pop(key)
    result = normalize(doc)
    lead = lane(result)
    assert lead["activity"]["value"] == "UNKNOWN"
    assert lead["auth"]["value"] == "UNKNOWN"
    assert lead["availability"]["value"] == "UNKNOWN"
    assert lead["quota"]["value"] == "observed_headroom"
    assert lead["quota"]["pool_binding"] == "UNKNOWN"
    assert lead["quota"]["lane_quota_attribution"] == "UNKNOWN"
    assert result["execution_allowed"] is False
    assert result["independent_capacity"] is False


def test_stale_snapshot_hides_all_independently_fresh_fields():
    doc = summary()
    doc["observed_at"] = "2026-11-14T07:23:34Z"
    row = doc["agents"][0]
    row.update(
        auth_state="authenticated_at_successful_turn",
        availability_state="successful_turn_observed",
        last_successful_turn_at="2026-11-14T07:28:29Z",
    )
    result = lane(normalize(doc))
    for name in ("activity", "auth", "availability", "quota"):
        assert result[name]["value"] == "UNKNOWN"
    assert result["quota"]["windows"] == []


@pytest.mark.parametrize("field,value", [
    ("auth", "authenticated_at_successful_turn"),
    ("availability", "successful_turn_observed"),
])
def test_success_after_activity_observation_is_unknown(field, value):
    doc = summary()
    row = doc["agents"][0]
    row.update(auth_state="authenticated_at_successful_turn",
               availability_state="successful_turn_observed",
               last_successful_turn_at="2026-11-14T07:28:30Z")
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "turn_started"
    assert result[field]["value"] == "UNKNOWN"


@pytest.mark.parametrize("field,value", [
    ("auth", "auth_required"),
    ("availability", "auth_required"),
    ("availability", "rate_limited"),
    ("availability", "access_denied"),
    ("availability", "account_on_hold"),
])
def test_error_after_activity_observation_is_unknown(field, value):
    doc = summary()
    doc["agents"][0].update(
        first_error_at="2026-11-14T07:28:30Z",
        **{("auth_state" if field == "auth" else "availability_state"): value},
    )
    assert lane(normalize(doc))[field]["value"] == "UNKNOWN"


@pytest.mark.parametrize("origin", ["last_successful_turn_at", "first_error_at"])
def test_state_origin_before_process_start_is_unknown(origin):
    doc = summary()
    row = doc["agents"][0]
    row.update(native_process_start_utc="2026-11-14T07:28:20Z")
    if origin == "last_successful_turn_at":
        row.update(auth_state="authenticated_at_successful_turn",
                   availability_state="successful_turn_observed")
    else:
        row.update(auth_state="auth_required", availability_state="rate_limited")
    row[origin] = "2026-11-14T07:28:19Z"
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "turn_started"
    assert result["auth"]["value"] == "UNKNOWN"
    assert result["availability"]["value"] == "UNKNOWN"


@pytest.mark.parametrize("stamp", [None, "not-a-timestamp", "2026-11-14T07:28:36Z"])
def test_missing_invalid_or_future_process_start_is_unknown(stamp):
    doc = summary()
    doc["agents"][0].update(
        native_process_start_utc=stamp,
        auth_state="authenticated_at_successful_turn",
        availability_state="successful_turn_observed",
        last_successful_turn_at="2026-11-14T07:28:29Z",
    )
    result = lane(normalize(doc))
    for name in ("activity", "auth", "availability", "quota"):
        assert result[name]["value"] == "UNKNOWN"
    assert result["quota"]["windows"] == []


def test_observations_before_current_process_start_are_unknown():
    doc = summary()
    doc["agents"][0].update(native_process_start_utc="2026-11-14T07:28:30Z")
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "UNKNOWN"
    assert result["quota"]["value"] == "UNKNOWN"
    assert result["quota"]["windows"] == []


def test_success_without_activity_timestamp_is_unknown():
    doc = summary()
    doc["agents"][0].update(
        observed_at=None,
        auth_state="authenticated_at_successful_turn",
        availability_state="successful_turn_observed",
        last_successful_turn_at="2026-11-14T07:28:29Z",
    )
    result = lane(normalize(doc))
    assert result["auth"]["value"] == "UNKNOWN"
    assert result["availability"]["value"] == "UNKNOWN"
    assert result["quota"]["value"] == "observed_headroom"


def test_process_start_equal_to_observation_is_valid():
    doc = summary()
    doc["agents"][0].update(
        native_process_start_utc="2026-11-14T07:28:29Z",
        auth_state="authenticated_at_successful_turn",
        availability_state="successful_turn_observed",
        last_successful_turn_at="2026-11-14T07:28:29Z",
    )
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "turn_started"
    assert result["auth"]["value"] == "authenticated_at_successful_turn"
    assert result["availability"]["value"] == "successful_turn_observed"
    assert result["quota"]["value"] == "observed_headroom"


def test_independent_quota_may_be_newer_than_activity_and_store_snapshot():
    doc = summary()
    doc["agents"][0]["quota_observed_at"] = "2026-11-14T07:28:31Z"
    result = lane(normalize(doc))
    assert result["quota"]["value"] == "observed_headroom"
    assert result["quota"]["age_seconds"] == 4
    assert result["quota"]["pool_binding"] == "UNKNOWN"


@pytest.mark.parametrize("fraction", ["0000001", "000000001"])
@pytest.mark.parametrize("suffix", ["Z", "+00:00", "+02:00"])
def test_submicrosecond_future_quota_is_unknown(fraction, suffix):
    doc = summary()
    hour = "09" if suffix == "+02:00" else "07"
    doc["agents"][0]["quota_observed_at"] = "2026-11-14T" + hour + ":28:35." + fraction + suffix
    quota = lane(normalize(doc))["quota"]
    assert quota["value"] == "UNKNOWN"
    assert quota["windows"] == []


@pytest.mark.parametrize("key,value,field", [
    ("last_successful_turn_at", "authenticated_at_successful_turn", "auth"),
    ("last_successful_turn_at", "successful_turn_observed", "availability"),
    ("first_error_at", "auth_required", "auth"),
    ("first_error_at", "rate_limited", "availability"),
])
def test_submicrosecond_state_after_activity_is_unknown(key, value, field):
    doc = summary()
    row = doc["agents"][0]
    row[key] = "2026-11-14T07:28:29.0000001Z"
    row["auth_state" if field == "auth" else "availability_state"] = value
    assert lane(normalize(doc))[field]["value"] == "UNKNOWN"


def test_submicrosecond_process_start_after_observation_is_unknown():
    doc = summary()
    doc["agents"][0]["native_process_start_utc"] = "2026-11-14T07:28:29.0000001Z"
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "UNKNOWN"
    assert result["quota"]["value"] == "UNKNOWN"


def test_submicrosecond_future_snapshot_hides_all_fields():
    doc = summary()
    doc["observed_at"] = "2026-11-14T07:28:35.0000001Z"
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "UNKNOWN"
    assert result["quota"]["value"] == "UNKNOWN"


def test_submicrosecond_age_over_five_minutes_is_stale():
    doc = summary()
    row = doc["agents"][0]
    row.update(observed_at="2026-11-14T07:23:34.9999999Z",
               quota_observed_at="2026-11-14T07:23:34.9999999Z")
    result = lane(normalize(doc))
    assert result["activity"]["value"] == "UNKNOWN"
    assert result["quota"]["value"] == "UNKNOWN"


def test_submicrosecond_timestamp_is_preserved_without_rounding():
    doc = summary()
    doc["agents"][0]["quota_observed_at"] = "2026-11-14T07:28:29.1234567Z"
    quota = lane(normalize(doc))["quota"]
    assert quota["value"] == "observed_headroom"
    assert quota["observed_at_utc"] == "2026-11-14T07:28:29.1234567Z"
    assert quota["age_seconds"] == pytest.approx(5.8765433)


def test_offset_conversion_preserves_submicrosecond_timestamp():
    doc = summary()
    doc["agents"][0]["quota_observed_at"] = "2026-11-14T09:28:29.1234567+02:00"
    quota = lane(normalize(doc))["quota"]
    assert quota["observed_at_utc"] == "2026-11-14T07:28:29.1234567Z"
    assert quota["age_seconds"] == pytest.approx(5.8765433)


def test_submicrosecond_future_activity_is_unknown():
    doc = summary()
    doc["agents"][0]["observed_at"] = "2026-11-14T07:28:35.0000001Z"
    assert lane(normalize(doc))["activity"]["value"] == "UNKNOWN"
