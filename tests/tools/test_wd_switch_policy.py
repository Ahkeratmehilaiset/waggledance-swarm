# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F15 pure switch policy: one success twin, then one failing gate per test.

Authored under the operator's no-test-run directive (2026-09-29): not yet executed.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import tools.wd_switch_policy as policy
from tools.lane_profile_catalog import load_catalog
from tools.wd_switch_policy import decide, inputs_digest

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "tests" / "fixtures" / "lane_profile_catalog_frozen_20260927.json")
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
POOL = "claude/operator-claude-subscription"
ACTIVATION_SHA = "a" * 64
XHIGH, MEDIUM, SONNET = "claude-opus-5-5-xhigh", "claude-opus-5-5-medium", "claude-sonnet-5-xhigh"


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def ago(**delta) -> str:
    return iso(NOW - timedelta(**delta))


def signed_catalog() -> dict:
    catalog = json.loads(json.dumps(CATALOG))
    catalog["operator_signature"] = "operator-signature-fixture"
    catalog["capacity_policy"]["mode"] = "auto"
    catalog["fleet"]["mode"] = "auto"
    for number, profile in enumerate(catalog["capacity_policy"]["profiles"].values()):
        profile.update(approved=True, qualification_ref=f"qual-{number:03d}")
    return catalog


def samples(start: float, end: float, *, seven_day_reset_hours: float = 21.0) -> list[dict]:
    """Two samples per window, 55 min apart, the newest 5 min old."""
    rows = []
    for window, duration, reset_hours in (("five_hour", 300, 4.0), ("seven_day", 10080, seven_day_reset_hours)):
        resets = (NOW + timedelta(hours=reset_hours)).timestamp()
        for used, minutes_ago in ((start, 60), (end, 5)):
            rows.append({"provider": "claude", "limit_id": "claude", "window": window,
                         "used_percent": used, "resets_at": resets, "duration_minutes": duration,
                         "observed_at": ago(minutes=minutes_ago)})
    return rows


def evidence(lane: str = "fable-5", current: tuple[str, str] = ("claude-opus-5-5", "medium"),
             target: str = XHIGH) -> dict:
    """Every gate passes: a raise of fable-5 from opus medium to opus xhigh."""
    return {
        "schema": "wd.switch-evidence.v1",
        "now_utc": iso(NOW),
        "activation": {
            "schema": "wd.bridge-v2-activation.v1",
            "provenance": {"trusted": True, "sha256": ACTIVATION_SHA},
            "signed": True,
            "expires_utc": iso(NOW + timedelta(days=10)),
            "features": {"switch_policy": True},
            "parameters": {"tick_seconds": 300, "hysteresis_percent": 5, "evidence_max_age_seconds": 600,
                           "budget_mode": "steady"},
        },
        "revocation": {"activation_sha256": ACTIVATION_SHA, "version": 1, "state": "clear",
                       "observed_utc": ago(minutes=1)},
        "catalog": signed_catalog(),
        "catalog_sha256": "b" * 64,
        "catalog_signature_verified": True,
        "lane": lane,
        "target_profile": target,
        "requester": {"agent": "codex-lead-1", "channel": "member", "verified": True},
        "binding": {"session_identity": "valid", "lane": lane, "observed_model_raw": current[0],
                    "observed_effort": current[1]},
        "active_reviews": [],
        "auth": {"lane": lane, "state": "authenticated", "observed_utc": ago(minutes=1)},
        "turn": {"lane": lane, "state": "successful_turn_observed", "observed_utc": ago(minutes=2)},
        "pool_bindings": {POOL: {"binding": "verified", "observed_utc": ago(minutes=1)}},
        # seven_day: 40 -> 41 in 55 min, 21 h to reset: forecast about 63.9 (under 70 - 5).
        "quota_samples": samples(40.0, 41.0),
        "last_verified_profile": None,
        "pool_reservations": {POOL: 0},
        "pool_changes_this_tick": {POOL: 0},
        "relaunch_history": [],
        "boundary_state": {"lane": lane, "is_supervisor": False, "observed_at": ago(seconds=30),
                           "current_session_id": "session-1", "idle": True, "pending_effects": False,
                           "previous_turn_blocker": False, "open_claims": [], "unresolved_requests": [],
                           "session_lineage": []},
        "intent": {"intent_id": "c" * 32, "created_utc": ago(seconds=20)},
        "competing_intents": [],
    }


def verdict(ev: dict) -> tuple[str, list[str]]:
    record = decide(ev)
    assert record["execution_allowed"] is False and record["authority"] == "none"
    return record["verdict"], record["reasons"]


def test_success_twin_switches():
    record = decide(evidence())
    assert record["verdict"] == "switch"
    assert record["reasons"] == ["all_gates_passed", "transition:stronger_profile"]
    assert record["current_profile"] == MEDIUM and record["target_profile"] == XHIGH
    assert record["intent_class"] == "raise" and record["pool"] == POOL


def test_deterministic_and_recomputable():
    ev = evidence()
    first, second = decide(ev), decide(json.loads(json.dumps(ev)))
    assert first == second
    assert first["inputs_digest"] == inputs_digest(ev) and len(first["inputs_digest"]) == 64


@pytest.mark.parametrize("mutate, reason", [
    (lambda e: e.pop("activation"), "activation_unknown"),
    (lambda e: e["activation"]["provenance"].update(trusted=False), "activation_provenance_untrusted"),
    (lambda e: e["activation"].update(signed=False), "activation_unsigned"),
    (lambda e: e["activation"].update(expires_utc=ago(seconds=1)), "activation_expired"),
    (lambda e: e["activation"].update(expires_utc="2026-10-10T00:00:00"), "activation_expiry_unknown"),
    (lambda e: e["activation"]["features"].update(switch_policy=False), "feature_disabled"),
    (lambda e: e["activation"]["parameters"].pop("hysteresis_percent"), "parameter_unknown:hysteresis_percent"),
    (lambda e: e["activation"]["parameters"].update(budget_mode="unlimited"), "parameter_invalid:budget_mode"),
    (lambda e: e["revocation"].update(state="revoked"), "activation_revoked"),
    (lambda e: e["revocation"].update(state="frozen"), "operator_freeze"),
    (lambda e: e["revocation"].update(observed_utc=ago(hours=1)), "revocation_stale"),
    (lambda e: e["revocation"].update(activation_sha256="d" * 64), "revocation_binding_mismatch"),
    (lambda e: e["catalog"].update(operator_signature="UNSIGNED-DEFAULT"), "catalog_unsigned"),
    (lambda e: e.update(catalog_signature_verified="yes"), "catalog_signature_unverified"),
    (lambda e: e["catalog"]["capacity_policy"].update(mode="shadow"), "catalog_mode_shadow"),
    (lambda e: e.update(requester={"agent": "operator", "channel": "member", "verified": True}),
     "operator_channel_unverified"),
    (lambda e: e["requester"].update(verified=False), "requester_unverified"),
    (lambda e: e["requester"].update(agent="grok-scout-1"), "requester_not_a_member"),
    (lambda e: e["binding"].update(session_identity="unknown"), "current_profile_unverified"),
    (lambda e: e["auth"].update(state="expired"), "auth_not_proven"),
    (lambda e: e["auth"].update(observed_utc=ago(hours=2)), "auth_stale"),
    (lambda e: e["turn"].update(observed_utc=iso(NOW + timedelta(minutes=5))), "turn_from_the_future"),
    (lambda e: e["pool_bindings"][POOL].update(binding="unverified"), "pool_binding_unverified"),
    (lambda e: e.update(pool_bindings={}), "pool_binding_unknown"),
    (lambda e: e.update(quota_samples=[]), "quota_samples_unknown"),
    (lambda e: e["quota_samples"][0].update(used_percent="41"), "quota_sample_malformed"),
    (lambda e: e.update(quota_samples=samples(90.0, 95.0)), "target_pool_at_risk"),
    (lambda e: e.update(pool_reservations={POOL: 10}), "budget_trip_line"),
    (lambda e: e.update(pool_reservations={}), "pool_reservations_unknown"),
    (lambda e: e.update(pool_changes_this_tick={POOL: 1}), "pool_tick_used"),
    (lambda e: e.update(pool_changes_this_tick=None), "pool_tick_unknown"),
    (lambda e: e.update(relaunch_history=[{"lane": "fable-5", "ts_utc": ago(minutes=10)}]), "rate:lane_cooldown"),
    (lambda e: e.update(relaunch_history="unknown"), "rate:relaunch_history_unknown"),
    (lambda e: e["boundary_state"].update(idle=False), "boundary:not_idle"),
    (lambda e: e.update(active_reviews=None), "active_reviews_unknown"),
    (lambda e: e.update(competing_intents=None), "contention_unknown"),
    (lambda e: e.update(intent={"intent_id": "not-hex", "created_utc": ago(seconds=1)}), "intent_identity_unknown"),
])
def test_each_failing_gate_parks(mutate, reason):
    ev = evidence()
    mutate(ev)
    got, reasons = verdict(ev)
    assert got == "park"
    assert reason in reasons


def test_stale_quota_parks_with_the_pacer_reason():
    ev = evidence()
    for row in ev["quota_samples"]:
        row["observed_at"] = iso(datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
                                 - timedelta(hours=1))
    got, reasons = verdict(ev)
    assert got == "park" and any(r.startswith("quota_unknown:claude/claude/") and "measurement_stale" in r
                                 for r in reasons)


def test_forecast_alone_never_switches_without_a_verified_requester():
    ev = evidence()
    ev.pop("requester")
    assert verdict(ev) == ("park", ["requester_unverified"])


def test_raise_inside_hysteresis_stays():
    ev = evidence()
    ev["pool_reservations"] = {POOL: 3}  # about 66.9: under the 70 trip line, over 70 - 5
    assert verdict(ev) == ("stay", ["raise_within_hysteresis"])


def test_same_profile_stays():
    assert verdict(evidence(target=MEDIUM)) == ("stay", ["no_change"])


@pytest.mark.parametrize("change, reason", [
    (lambda e: e["catalog"]["capacity_policy"]["profiles"][XHIGH].update(approved=False), "target_not_qualified"),
    (lambda e: e["catalog"]["capacity_policy"]["profiles"][XHIGH].update(billing="pay_per_use"),
     "billing_outside_envelope"),
    (lambda e: e.update(target_profile=SONNET), "target_not_allowed"),
])
def test_operator_only_changes(change, reason):
    ev = evidence()
    change(ev)
    assert verdict(ev) == ("operator_required", [reason])


def test_reviewer_is_never_lowered_by_a_member():
    ev = evidence(lane="claude-rco-1", current=("claude-opus-5-5", "xhigh"), target=SONNET)
    assert verdict(ev) == ("operator_required", ["reviewer_lowering"])


def test_author_cannot_change_its_own_reviewer():
    ev = evidence(lane="claude-rco-1", current=("claude-sonnet-5", "xhigh"), target=XHIGH)
    ev["requester"] = {"agent": "fable-5", "channel": "member", "verified": True}
    ev["active_reviews"] = [{"reviewer": "claude-rco-1", "author": "fable-5"}]
    assert verdict(ev) == ("operator_required", ["requester_under_review_by_lane"])


def test_grok_target_parks():
    ev = evidence()
    ev["catalog"]["capacity_policy"]["profiles"][XHIGH]["provider"] = "grok"
    assert verdict(ev) == ("park", ["grok_pool_unreadable"])


def test_revert_skips_the_dwell_only():
    ev = evidence()
    ev["last_verified_profile"] = XHIGH
    ev["relaunch_history"] = [{"lane": "fable-5", "ts_utc": ago(minutes=10)}]
    record = decide(ev)
    assert record["verdict"] == "switch" and record["intent_class"] == "revert"
    # The hourly cap still counts a revert: two switches in the last hour exhaust fable-5's budget of 2.
    ev["relaunch_history"].append({"lane": "fable-5", "ts_utc": ago(minutes=40)})
    got, reasons = verdict(ev)
    assert got == "park" and "rate:lane_budget_exhausted" in reasons


def test_contest_equal_precedence_keeps_the_incumbent():
    ev = evidence()
    ev["competing_intents"] = [{"lane": "fable-5", "intent_id": "e" * 32, "created_utc": ago(seconds=5),
                                "precedence": 1, "target_profile": MEDIUM}]
    assert verdict(ev) == ("stay", ["contested_incumbent_stays"])


def test_contest_lower_precedence_does_not_block():
    ev = evidence()
    ev["competing_intents"] = [{"lane": "fable-5", "intent_id": "e" * 32, "created_utc": ago(seconds=5),
                                "precedence": 2, "target_profile": MEDIUM}]
    assert verdict(ev)[0] == "switch"


def test_duplicate_intent_only_the_earliest_carries():
    ev = evidence()
    ev["competing_intents"] = [{"lane": "fable-5", "intent_id": "0" * 32, "created_utc": ago(minutes=1),
                                "precedence": 1, "target_profile": XHIGH}]
    assert verdict(ev) == ("stay", ["duplicate_of_earlier_intent"])


def test_non_canonical_or_malformed_evidence_parks_without_raising():
    ev = evidence()
    ev["pool_reservations"] = {POOL: float("nan")}
    assert verdict(ev) == ("park", ["evidence_not_canonical_json"])
    ev = evidence()
    del ev["catalog"]["capacity_policy"]["profiles"][MEDIUM]["limits"]
    got, reasons = verdict(ev)
    assert got == "park" and reasons[0] == "evidence_malformed"
    assert decide(None)["verdict"] == "park"


def test_module_is_pure_by_construction():
    """No I/O, clock, environment, network or process module; no wall-clock call."""
    tree = ast.parse(Path(policy.__file__).read_text(encoding="utf-8"))
    forbidden = {"os", "sys", "pathlib", "subprocess", "socket", "time", "urllib", "sqlite3", "shutil"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {alias.name.split(".")[0] for alias in node.names} & forbidden
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in forbidden
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("now", "utcnow", "today", "open", "write_text", "environ")
        if isinstance(node, ast.Name):
            assert node.id not in ("open", "print", "input")
