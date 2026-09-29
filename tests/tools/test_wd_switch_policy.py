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

import tools.bridge_v2_activation as activation
import tools.wd_switch_policy as policy
from tools.lane_profile_catalog import load_catalog
from tools.wd_switch_policy import decide, inputs_digest

ROOT = Path(__file__).resolve().parents[2]
CATALOG, DIGEST = load_catalog(ROOT / "tests" / "fixtures" / "lane_profile_catalog_frozen_20260927.json")
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
POOL = "claude/operator-claude-subscription"
HEAD, TREE = "1" * 40, "2" * 40
PARAMETERS = {"tick_seconds": 300, "hysteresis_percent": 5, "evidence_max_age_seconds": 600, "budget_mode": "steady"}
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


def f0_inputs(parameters: dict | None = None) -> dict:
    """The caller-verified F0 inputs that enable F15: the shipped policy with F15 switched on."""
    config = json.loads((ROOT / "configs" / "bridge_v2_activation.json").read_text(encoding="utf-8"))
    signed = config["policy"]
    signed["features"]["F15"].update(enabled=True, stage=1)
    signed.update(expires_utc=iso(NOW + timedelta(days=10)), revocation_max_age_seconds=3600,
                  parameters={"F15": dict(PARAMETERS if parameters is None else parameters)})
    digest = activation.canonical_sha256(signed)
    return {
        "pins": {"trusted_policy_sha256": digest, "expected_head": HEAD, "expected_tree": TREE,
                 "min_revocation_version": 3},
        "document": {"policy": signed,
                     "signature": {"schema": activation.SIGNATURE_SCHEMA, "policy_sha256": digest, "head": HEAD,
                                   "tree": TREE, "signed_utc": ago(hours=1),
                                   "expires_utc": iso(NOW + timedelta(days=5))}},
        "revocation": {"schema": activation.REVOCATION_SCHEMA, "version": 3, "policy_sha256": digest,
                       "frozen": False, "revoked": [], "updated_utc": ago(minutes=5)},
        "decision": {"feature": "F15", "enabled": True, "reason": "enabled by the signed policy",
                     "policy_sha256": digest, "revocation_version": 3},
    }


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
        "f0": f0_inputs(),
        "catalog": signed_catalog(),
        "catalog_sha256": CATALOG_SHA,
        "catalog_signature_verified": True,
        "lane": lane,
        "target_profile": target,
        "requester": {"agent": "codex-lead-1", "channel": "member", "verified": True},
        "binding": {"session_identity": "valid", "lane": lane, "observed_model_raw": current[0],
                    "observed_effort": current[1], "observed_utc": ago(minutes=1)},
        "active_reviews": {"observed_utc": ago(minutes=1), "items": []},
        "auth": {"lane": lane, "state": "authenticated", "observed_utc": ago(minutes=1)},
        "turn": {"lane": lane, "state": "successful_turn_observed", "observed_utc": ago(minutes=2)},
        "pool_bindings": {POOL: {"binding": "verified", "observed_utc": ago(minutes=1)}},
        # seven_day: 40 -> 41 in 55 min, 21 h to reset: forecast about 63.9 (under 70 - 5).
        "quota_samples": samples(40.0, 41.0),
        "pool_reservations": {POOL: 0},
        "pool_changes_this_tick": {POOL: 0},
        "relaunch_history": {"observed_utc": ago(minutes=1), "entries": []},
        "boundary_state": {"lane": lane, "is_supervisor": False, "observed_at": ago(seconds=30),
                           "current_session_id": "session-1", "idle": True, "pending_effects": False,
                           "previous_turn_blocker": False, "open_claims": [], "unresolved_requests": [],
                           "session_lineage": []},
        "intent": {"intent_id": "c" * 32, "created_utc": ago(seconds=20)},
        "competing_intents": {"observed_utc": ago(seconds=10), "items": []},
    }


CATALOG_SHA = inputs_digest(signed_catalog())


def verdict(ev: dict) -> tuple[str, list[str]]:
    """Decide; a catalog the test mutated is re-bound first unless the test changed the digest itself."""
    if ev.get("catalog_sha256") == CATALOG_SHA and isinstance(ev.get("catalog"), dict):
        ev["catalog_sha256"] = inputs_digest(ev["catalog"])
    record = decide(ev)
    assert record["execution_allowed"] is False and record["authority"] == "none"
    return record["verdict"], record["reasons"]


def test_success_twin_switches():
    record = decide(evidence())
    assert record["verdict"] == "switch"
    assert record["reasons"] == ["all_gates_passed", "transition:stronger_profile"]
    assert record["current_profile"] == MEDIUM and record["target_profile"] == XHIGH
    assert record["intent_class"] == "raise" and record["pool"] == POOL
    assert record["catalog_sha256"] == CATALOG_SHA == inputs_digest(evidence()["catalog"])


def test_deterministic_and_recomputable():
    ev = evidence()
    first, second = decide(ev), decide(json.loads(json.dumps(ev)))
    assert first == second
    assert first["inputs_digest"] == inputs_digest(ev) and len(first["inputs_digest"]) == 64


@pytest.mark.parametrize("mutate, reason", [
    (lambda e: e.pop("f0"), "f0_inputs_missing"),
    (lambda e: e["f0"]["decision"].update(enabled=False), "f0_decision_disabled"),
    (lambda e: e["f0"]["decision"].update(feature="F16"), "f0_feature_mismatch"),
    (lambda e: e["f0"]["decision"].update(revocation_version=2), "f0_decision_unbound"),
    (lambda e: e["f0"]["pins"].update(trusted_policy_sha256="d" * 64), "f0_policy_unbound"),
    (lambda e: e["f0"]["pins"].update(expected_head="3" * 40), "f0_signature_invalid"),
    (lambda e: e["f0"]["pins"].update(min_revocation_version=4), "f0_revocation_rollback"),
    (lambda e: e["f0"]["revocation"].update(frozen=True), "f0_frozen"),
    (lambda e: e["f0"]["revocation"].update(revoked=["F15"]), "f0_revoked"),
    (lambda e: e["f0"]["revocation"].update(updated_utc=ago(hours=2)), "f0_revocation_stale"),
    (lambda e: e.update(f0=f0_inputs({k: v for k, v in PARAMETERS.items() if k != "hysteresis_percent"})),
     "f0_parameters_unknown"),
    (lambda e: e.update(f0=f0_inputs(dict(PARAMETERS, budget_mode="unlimited"))), "parameter_invalid:budget_mode"),
    (lambda e: e.update(catalog_sha256="b" * 64), "catalog_digest_mismatch"),
    (lambda e: e["binding"].update(observed_utc=ago(hours=1)), "binding_stale"),
    (lambda e: e.update(active_reviews=[]), "active_reviews_unknown"),
    (lambda e: e.update(competing_intents=[]), "competing_intents_unknown"),
    (lambda e: e.update(relaunch_history=[]), "relaunch_history_unknown"),
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
    (lambda e: e["relaunch_history"]["entries"].append({"lane": "fable-5", "ts_utc": ago(minutes=10)}),
     "rate:lane_cooldown"),
    (lambda e: e["relaunch_history"].update(entries="unknown"), "relaunch_history_unknown"),
    (lambda e: e["boundary_state"].update(idle=False), "boundary:not_idle"),
    (lambda e: e.update(active_reviews=None), "active_reviews_unknown"),
    (lambda e: e.update(competing_intents=None), "competing_intents_unknown"),
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
    ev["active_reviews"]["items"] = [{"reviewer": "claude-rco-1", "author": "fable-5"}]
    assert verdict(ev) == ("operator_required", ["requester_under_review_by_lane"])


def test_grok_target_parks():
    ev = evidence()
    ev["catalog"]["capacity_policy"]["profiles"][XHIGH]["provider"] = "grok"
    assert verdict(ev) == ("park", ["grok_pool_unreadable"])


def receipt(**over) -> dict:
    """An observed relaunch receipt: fable-5 was switched from xhigh to medium 10 minutes ago."""
    entry = {"lane": "fable-5", "ts_utc": ago(minutes=10), "outcome": "switched", "from_profile": XHIGH,
             "to_profile": MEDIUM}
    entry.update(over)
    return entry


def test_revert_skips_the_dwell_only():
    ev = evidence()
    ev["relaunch_history"]["entries"] = [receipt()]
    record = decide(ev)
    assert record["verdict"] == "switch" and record["intent_class"] == "revert"
    # The hourly cap still counts a revert: two switches in the last hour exhaust fable-5's budget of 2.
    ev["relaunch_history"]["entries"].append(receipt(ts_utc=ago(minutes=40), from_profile=MEDIUM, to_profile=XHIGH))
    got, reasons = verdict(ev)
    assert got == "park" and "rate:lane_budget_exhausted" in reasons


@pytest.mark.parametrize("entries", [
    [receipt(outcome="rolled_back")],
    [receipt(outcome=None)],
    [receipt(from_profile=SONNET)],
    [receipt(to_profile=XHIGH)],
    [{"lane": "fable-5", "ts_utc": ago(minutes=10)}],
    [receipt(), receipt(outcome="rolled_back")],  # two receipts at the latest time: ambiguous
    [receipt(ts_utc=ago(minutes=20)), receipt(from_profile=SONNET)],  # the latest receipt is not the revert
])
def test_revert_needs_the_latest_attested_receipt(entries):
    # RCO2 B2: the revert is derived from the observed history. A free label is ignored.
    ev = evidence()
    ev["last_verified_profile"] = XHIGH
    ev["relaunch_history"]["entries"] = entries
    got, reasons = verdict(ev)
    assert got == "park" and "rate:lane_cooldown" in reasons


def test_contest_equal_precedence_keeps_the_incumbent():
    ev = evidence()
    ev["competing_intents"]["items"] = [{"lane": "fable-5", "intent_id": "e" * 32, "created_utc": ago(seconds=5),
                                         "precedence": 1, "target_profile": MEDIUM}]
    assert verdict(ev) == ("stay", ["contested_incumbent_stays"])


def test_contest_lower_precedence_does_not_block():
    ev = evidence()
    ev["competing_intents"]["items"] = [{"lane": "fable-5", "intent_id": "e" * 32, "created_utc": ago(seconds=5),
                                         "precedence": 2, "target_profile": MEDIUM}]
    assert verdict(ev)[0] == "switch"


def test_duplicate_intent_only_the_earliest_carries():
    ev = evidence()
    ev["competing_intents"]["items"] = [{"lane": "fable-5", "intent_id": "0" * 32, "created_utc": ago(minutes=1),
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
