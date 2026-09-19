# SPDX-License-Identifier: BUSL-1.1
"""Offline acceptance tests: no peers, credentials or inference are involved."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.bridge_capacity_advisor import (  # noqa: E402
    InputError,
    build_report,
    example_input,
    normalize_capacity,
)

NOW = datetime(2026, 9, 18, 20, tzinfo=timezone.utc)
STAMP = NOW.isoformat()
RESET = (NOW + timedelta(hours=1)).timestamp()


def observation(provider="codex", used=25, account="account-a", limit="codex"):
    if provider == "codex":
        payload = {"rateLimitsByLimitId": {limit: {
            "limitId": limit,
            "primary": {"usedPercent": used, "resetsAt": RESET},
            "secondary": None,
        }}}
    else:
        payload = {"rate_limits": {
            "five_hour": {"used_percentage": used, "resets_at": RESET},
            "seven_day": {"used_percentage": 20, "resets_at": RESET + 86400},
        }}
    return {"provider": provider, "account_pool": account,
            "observed_at": STAMP, "source_ref": "fixture:provider-response",
            "payload": payload}


def capacity(obs, **kw):
    provider = obs[0]["provider"] if obs else "codex"
    limits = [{"id": "codex", "windows": ["primary"]}] if provider == "codex" else [
        {"id": "claude", "windows": ["five_hour", "seven_day"]}]
    return normalize_capacity(obs, provider=provider, account_pool="account-a",
                              limits=kw.pop("limits", limits), now=NOW,
                              max_age_seconds=300, **kw)


@pytest.fixture
def inputs():
    doc = example_input(NOW)
    return doc["policy"], doc["snapshot"]


def decision(inputs):
    return build_report(*inputs, now=NOW)["tasks"][0]


def exhaust(snapshot):
    snapshot["observations"][0]["payload"]["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] = 100


def test_available_capacity_preserves_observation_and_source():
    result = capacity([observation()])
    assert result["state"] == "available"
    assert result["observed_at"] == STAMP
    assert result["source_ref"] == "fixture:provider-response"
    assert result["windows"][0]["used_percent"] == 25


@pytest.mark.parametrize("used", [100, 100.5, 120])
def test_at_or_above_limit_is_exhausted(used):
    assert capacity([observation(used=used)])["state"] == "exhausted"


@pytest.mark.parametrize("used", [None, True, "25", -1, float("nan"), float("inf")])
def test_bad_percentage_never_becomes_available(used):
    assert capacity([observation(used=used)])["state"] == "unknown"


@pytest.mark.parametrize("offset", [-301, 1])
def test_stale_or_future_observation_is_unknown(offset):
    obs = observation()
    obs["observed_at"] = (NOW + timedelta(seconds=offset)).isoformat()
    assert capacity([obs])["state"] == "unknown"


def test_reset_requires_new_observation_not_invented_allowance():
    obs = observation(used=100)
    obs["payload"]["rateLimitsByLimitId"]["codex"]["primary"]["resetsAt"] = NOW.timestamp()
    assert capacity([obs])["state"] == "unknown"


def test_missing_capacity_is_not_zero():
    assert capacity([])["state"] == "unknown"
    obs = observation()
    obs["payload"] = {"usage": {"totalTokens": 0}, "credits": {"unlimited": True}}
    assert capacity([obs])["state"] == "unknown"


def test_multi_bucket_never_falls_back_to_legacy_wrong_bucket():
    obs = observation()
    obs["payload"]["rateLimits"] = obs["payload"]["rateLimitsByLimitId"]["codex"]
    obs["payload"]["rateLimitsByLimitId"] = {}
    assert capacity([obs])["state"] == "unknown"


def test_legacy_bucket_requires_exact_limit_id():
    obs = observation()
    bucket = obs["payload"].pop("rateLimitsByLimitId")["codex"]
    obs["payload"]["rateLimits"] = bucket
    assert capacity([obs])["state"] == "available"
    bucket["limitId"] = "other"
    assert capacity([obs])["state"] == "unknown"


def test_all_present_and_required_windows_apply():
    obs = observation()
    bucket = obs["payload"]["rateLimitsByLimitId"]["codex"]
    bucket["secondary"] = {"usedPercent": 100, "resetsAt": RESET}
    assert capacity([obs])["state"] == "exhausted"
    bucket["secondary"] = None
    limits = [{"id": "codex", "windows": ["primary", "secondary"]}]
    assert capacity([obs], limits=limits)["state"] == "unknown"


def test_all_declared_buckets_apply():
    obs = observation()
    obs["payload"]["rateLimitsByLimitId"]["separate"] = {
        "limitId": "separate", "primary": {"usedPercent": 100, "resetsAt": RESET}}
    assert capacity([obs], limits=[{"id": "codex", "windows": ["primary"]},
                                 {"id": "separate", "windows": ["primary"]}])["state"] == "exhausted"


def test_provider_reached_flag_is_not_ignored():
    obs = observation()
    obs["payload"]["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = "usageLimit"
    assert capacity([obs])["state"] == "exhausted"


def test_newest_observation_wins_not_input_order():
    older = observation(used=100)
    older["observed_at"] = (NOW - timedelta(seconds=1)).isoformat()
    for rows in ([observation(), older], [older, observation()]):
        assert capacity(rows)["state"] == "available"


def test_equal_time_conflicting_observations_fail_closed():
    assert capacity([observation(), observation(used=100)])["state"] == "unknown"
    assert capacity([observation(), observation()])["state"] == "available"


def test_wrong_account_is_not_independent_available_capacity():
    assert capacity([observation(account="different-account")])["state"] == "unknown"


def test_claude_optional_spend_limit_blocks_and_tokens_are_not_quota():
    obs = observation(provider="claude")
    assert capacity([obs])["state"] == "available"
    obs["payload"]["rate_limits"]["spend_limit"] = {"used_percentage": 105, "resets_at": RESET}
    assert capacity([obs])["state"] == "exhausted"
    obs["payload"] = {"context_window": {"used_percentage": 2}, "cost": {"total_cost_usd": 0}}
    assert capacity([obs])["state"] == "unknown"


def test_shadow_report_is_pure_and_never_execution_authority(inputs):
    before = deepcopy(inputs)
    report = build_report(*inputs, now=NOW)
    assert inputs == before
    assert report["mode"] == "shadow"
    assert report["execution_allowed"] is False
    assert report["tasks"][0]["action"] == "keep_current"
    assert report["tasks"][0]["release_allowed"] is False


def test_same_exhausted_pool_cannot_be_a_fallback(inputs):
    policy, snapshot = inputs
    exhaust(snapshot)
    policy["profiles"]["qualified-b"]["limits"] = deepcopy(policy["profiles"]["qualified-a"]["limits"])
    row = decision(inputs)
    assert row["action"] == "wait_capacity"
    assert row["proposed_profile"] is None


def test_qualified_independent_pool_proposes_but_does_not_switch(inputs):
    exhaust(inputs[1])
    row = decision(inputs)
    assert row["action"] == "switch_proposed"
    assert row["proposed_profile"] == "qualified-b"
    assert row["execution_allowed"] is False
    assert row["preserved"]["required_reviewers"] == ["rco1", "rco2"]
    assert row["preserved"]["claim_id"] == "fixture-claim"


@pytest.mark.parametrize("field,value", [
    ("qualification_ref", ""), ("qualified_for", ["other-quality-level"]),
    ("billing", "api"), ("approved", False), ("approved", "true"),
])
def test_unqualified_unfunded_or_wrong_provider_fallback_is_rejected(inputs, field, value):
    inputs[0]["profiles"]["qualified-b"][field] = value
    exhaust(inputs[1])
    assert decision(inputs)["proposed_profile"] is None


def test_cross_provider_is_handoff_not_same_session_model_switch(inputs):
    profile = inputs[0]["profiles"]["qualified-b"]
    profile.update(provider="claude", limits=[{"id": "claude", "windows": ["five_hour", "seven_day"]}])
    inputs[1]["observations"].append(observation(provider="claude"))
    exhaust(inputs[1])
    row = decision(inputs)
    assert row["proposed_profile"] is None
    assert "cross_provider_session_transfer_not_supported" in row["candidates"][1]["reasons"]


def test_cross_account_is_not_a_supported_model_switch(inputs):
    profile = inputs[0]["profiles"]["qualified-b"]
    profile["account_pool"] = "account-b"
    inputs[1]["observations"].append(observation(account="account-b", limit=profile["limits"][0]["id"]))
    exhaust(inputs[1])
    row = decision(inputs)
    assert row["proposed_profile"] is None
    assert "cross_account_session_transfer_not_supported" in row["candidates"][1]["reasons"]


@pytest.mark.parametrize("field", ["agent_id", "kind", "reason"])
def test_non_string_task_fields_fail_closed(inputs, field):
    inputs[1]["tasks"][0][field] = {"invalid": True}
    assert decision(inputs)["action"] == "blocked"


def test_non_string_current_profile_fails_closed(inputs):
    inputs[1]["agents"][0]["current_profile"] = []
    assert decision(inputs)["action"] == "blocked"


def test_non_string_role_is_structured_policy_error(inputs):
    inputs[0]["agents"]["example-lead"]["role"] = {}
    with pytest.raises(InputError):
        build_report(*inputs, now=NOW)


def test_extremely_large_number_does_not_overflow():
    assert capacity([observation(used=10 ** 400)])["state"] == "exhausted"


def test_agent_report_separates_requested_observed_and_missing(inputs):
    inputs[1]["agents"][0]["effort_observed"] = None
    result = build_report(*inputs, now=NOW)["agents"]["example-lead"]
    assert result["effort_requested"] == "high"
    assert result["effort_observed"] is None
    assert result["model_observed"] == "example-model-a"
    assert result["identity_state"] == "observed"


def test_report_does_not_leak_unrelated_provider_payload_fields(inputs):
    inputs[1]["observations"][0]["payload"]["access_token"] = "DO-NOT-ECHO"
    inputs[1]["agents"][0]["unrelated_transcript"] = "DO-NOT-ECHO"
    assert "DO-NOT-ECHO" not in json.dumps(build_report(*inputs, now=NOW))


@pytest.mark.parametrize("field", ["hold", "cancelled"])
def test_task_hold_or_cancellation_stops_routing(inputs, field):
    inputs[1]["tasks"][0][field] = True
    exhaust(inputs[1])
    assert decision(inputs)["action"] == "blocked"


@pytest.mark.parametrize("field", ["hold", "cancelled"])
def test_missing_task_stop_flags_fail_closed(inputs, field):
    del inputs[1]["tasks"][0][field]
    assert decision(inputs)["action"] == "blocked"


@pytest.mark.parametrize("reason", ["auth", "billing", "safety", "transport", "tool", "invented"])
def test_non_capacity_failures_are_not_silent_model_fallback(inputs, reason):
    inputs[1]["tasks"][0]["reason"] = reason
    assert decision(inputs)["action"] == "blocked"


@pytest.mark.parametrize("field,value", [
    ("idle", False), ("pending_effects", True), ("hold", True),
    ("model_observed", "different-model"), ("effort_observed", None),
    ("switches_this_task", 2), ("last_switch_at", STAMP),
])
def test_unsafe_session_blocks_switch(inputs, field, value):
    inputs[1]["agents"][0][field] = value
    exhaust(inputs[1])
    assert decision(inputs)["action"] == "blocked"


@pytest.mark.parametrize("field", ["session_id", "native_thread_id", "task_id", "head", "claim_id", "request_id", "scope_digest", "profile_id"])
def test_checkpoint_must_match_exact_task_and_identity(inputs, field):
    inputs[1]["agents"][0]["checkpoint"][field] = "mismatch"
    exhaust(inputs[1])
    assert decision(inputs)["action"] == "blocked"


def test_stale_catalog_does_not_authorize_a_model(inputs):
    inputs[1]["agents"][0]["catalog"][1]["observed_at"] = "2020-01-01T00:00:00Z"
    exhaust(inputs[1])
    assert decision(inputs)["proposed_profile"] is None


def test_reviewer_cannot_review_own_change(inputs):
    inputs[0]["agents"]["example-lead"]["role"] = "rco1"
    inputs[1]["tasks"][0]["kind"] = "review"
    inputs[1]["tasks"][0]["author_agent"] = "example-lead"
    assert decision(inputs)["action"] == "blocked"


def test_input_cannot_enable_live_mode(inputs):
    inputs[0]["mode"] = "auto"
    with pytest.raises(InputError):
        build_report(*inputs, now=NOW)


def test_duplicate_agent_identity_is_rejected(inputs):
    inputs[1]["agents"].append(deepcopy(inputs[1]["agents"][0]))
    with pytest.raises(InputError):
        build_report(*inputs, now=NOW)


def test_grok_failures_count_toward_hour_budget(inputs):
    inputs[1]["grok"] = {"observed_at": STAMP, "source_ref": "fixture:shared-status",
                         "last_attempt_at": (NOW - timedelta(minutes=59)).isoformat(),
                         "in_flight": False, "last_outcome": "failed"}
    result = build_report(*inputs, now=NOW)["grok"]
    assert result["state"] == "cooldown"
    assert result["execution_allowed"] is False
    assert result["seconds_until_eligible"] == 60


def test_cli_example_and_report_need_no_files_or_processes():
    script = str(ROOT / "tools" / "bridge_capacity_advisor.py")
    sample = subprocess.run([sys.executable, script, "--example", "--now", STAMP],
                            capture_output=True, text=True, check=True)
    doc = json.loads(sample.stdout)
    result = subprocess.run([sys.executable, script, "--stdin", "--now", STAMP],
                            input=json.dumps(doc), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["execution_allowed"] is False


@pytest.mark.parametrize("raw", ['{"policy":NaN}', '{"policy":{},"policy":{}}', '{}', 'not json',
                                 '{"policy":1e999}', '{"policy":' + '9' * 5000 + '}'],
                         ids=["nan", "duplicate", "missing", "non-json", "overflow-float", "oversized-integer"])
def test_cli_invalid_input_returns_structured_error_without_echoing_payload(raw):
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "bridge_capacity_advisor.py"), "--stdin"],
                            input=raw, capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["execution_allowed"] is False
    assert result.stderr == ""


def test_cli_rejects_oversized_input():
    from tools.bridge_capacity_advisor import MAX_INPUT_BYTES

    result = subprocess.run([sys.executable, str(ROOT / "tools" / "bridge_capacity_advisor.py"), "--stdin"],
                            input=' ' * (MAX_INPUT_BYTES + 1), capture_output=True, text=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"] == "input exceeds bounded size"


def test_missing_catalog_and_agent_are_visible_not_invented(inputs):
    inputs[1]["agents"] = []
    report = build_report(*inputs, now=NOW)
    assert report["agents"]["example-lead"]["identity_state"] == "unknown_or_stale"
    assert report["tasks"][0]["action"] == "blocked"


def test_schema_is_role_and_effort_specific_not_model_name_only(inputs):
    inputs[0]["profiles"]["qualified-b"]["effort"] = "low"
    exhaust(inputs[1])
    row = decision(inputs)
    assert row["proposed_profile"] is None
    assert "catalog_unknown_or_stale" in row["candidates"][1]["reasons"]


def test_allowed_preference_does_not_cause_unnecessary_switch(inputs):
    inputs[0]["agents"]["example-lead"]["profiles"].reverse()
    assert decision(inputs)["action"] == "keep_current"


def test_quality_issue_selects_only_prequalified_alternative(inputs):
    inputs[1]["tasks"][0]["reason"] = "quality"
    assert decision(inputs)["action"] == "switch_proposed"
    inputs[0]["profiles"]["qualified-b"]["qualified_for"] = ["routine-only"]
    assert decision(inputs)["action"] == "blocked"


@pytest.mark.parametrize("role", ["lead", "tools", "fable", "rco1", "rco2"])
def test_each_worker_role_uses_its_own_qualified_profile(inputs, role):
    inputs[0]["agents"]["example-lead"]["role"] = role
    for profile in inputs[0]["profiles"].values():
        profile["roles"] = [role]
    if role.startswith("rco"):
        inputs[1]["tasks"][0].update(kind="review", author_agent="independent-author",
                                      required_reviewers=["example-lead", "other-reviewer"])
    assert decision(inputs)["action"] == "keep_current"


def test_grok_is_not_an_ordinary_fallback_worker(inputs):
    inputs[0]["agents"]["example-lead"]["role"] = "grok"
    assert decision(inputs)["action"] == "blocked"
    assert "grok_requires_existing_budget_helper_not_routing" in decision(inputs)["reasons"]


@pytest.mark.parametrize("minutes,in_flight,expected", [(60, False, "recheck_shared_budget"),
                                                      (61, True, "in_flight"), (-1, False, "unknown")])
def test_grok_clock_and_inflight_do_not_grant_execution(inputs, minutes, in_flight, expected):
    inputs[1]["grok"] = {"observed_at": STAMP, "source_ref": "fixture:shared-status",
                         "last_attempt_at": (NOW - timedelta(minutes=minutes)).isoformat(),
                         "in_flight": in_flight}
    result = build_report(*inputs, now=NOW)["grok"]
    assert result["state"] == expected
    assert result["execution_allowed"] is False
