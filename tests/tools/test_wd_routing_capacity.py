"""Bridge v2 F19/F25 capacity adapter: measured evidence to one router capacity block, or unknown."""

from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.wd_routing_capacity as rc
import tools.wd_task_router as tr
from tools.wd_capacity_pacing import pace_windows
from tools.wd_composer_select import digest

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)
SUBJECT = "a" * 64
SESSION = "sess-claude-1"
RESET_PRIMARY = int((NOW + timedelta(hours=3)).timestamp())
RESET_SECONDARY = int((NOW + timedelta(days=4)).timestamp())
RESET_FIVE = int((NOW + timedelta(hours=2)).timestamp())
RESET_SEVEN = int((NOW + timedelta(days=5)).timestamp())
BOUND = {"kind": "auth_context", "id": SUBJECT}                         # the pool-binding receipt's subject shape
CLAUDE_BOUND = {"kind": "native_session", "id": SESSION}
WORKER = {"worker": "codex-tools-1", "profile_id": "codex-sol-high", "subject": BOUND}
CLAUDE_WORKER = {"worker": "fable-5", "profile_id": "claude-strong", "subject": CLAUDE_BOUND}
CODEX_WINDOWS = (("primary", 30.0, 31.0, RESET_PRIMARY, 300), ("secondary", 20.0, 20.1, RESET_SECONDARY, 10080))
CLAUDE_WINDOWS = (("five_hour", 11.0, 12.0, RESET_FIVE, 300), ("seven_day", 29.9, 30.0, RESET_SEVEN, 10080))
BOTH_FRESHNESS = {"codex": ["fresh"], "claude": ["fresh", "provider_timestamp_unknown"]}


def policy_body(**over):
    body = {"schema": rc.POLICY_SCHEMA, "max_observation_age_seconds": 300,
            "accepted_freshness": {"codex": ["fresh"], "claude": ["fresh"]},
            "pools": {"codex-pro-a": {"billing": "included", "mode": "normal"},
                      "claude-max-a": {"billing": "included", "mode": "normal"}},
            "profile_providers": {"codex-sol-high": "codex", "claude-strong": "claude"}}
    body.update(over)
    return body


def signed(**over):
    body = policy_body(**over)
    return {"policy": body, "sha256": digest(body)}


def binding(expires=NOW + timedelta(hours=12)):
    return {"receipt_id": "b" * 32, "receipt_sha256": "c" * 64, "provenance_kind": "operator_reading",
            "expires_at_utc": expires.isoformat()}


def codex_row(primary=31.0, secondary=20.1, **over):
    row = {"schema": "wd.capacity-observation.v1", "provider": "codex",
           "observed_at": (NOW - timedelta(seconds=30)).isoformat(), "auth_context_id": SUBJECT,
           "account_pool": "codex-pro-a", "pool_identity_state": "verified_binding", "pool_binding": binding(),
           "freshness": "fresh", "execution_allowed": False,
           "payload": {"rateLimits": {"limitId": "codex",
                                      "primary": {"usedPercent": primary, "resetsAt": RESET_PRIMARY,
                                                  "windowDurationMins": 300},
                                      "secondary": {"usedPercent": secondary, "resetsAt": RESET_SECONDARY,
                                                    "windowDurationMins": 10080}}}}
    row.update(over)
    return row


def claude_row(**over):
    row = {"schema": "wd.capacity-observation.v1", "provider": "claude", "source_ref": "claude:statusline",
           "observed_at": (NOW - timedelta(seconds=30)).isoformat(), "native_thread_id": SESSION,
           "account_pool": "claude-max-a", "pool_identity_state": "verified_binding", "pool_binding": binding(),
           "freshness": "provider_timestamp_unknown", "execution_allowed": False,
           "payload": {"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": RESET_FIVE},
                                       "seven_day": {"used_percentage": 30, "resets_at": RESET_SEVEN}}}}
    row.update(over)
    return row


OWN = {"codex": (SUBJECT, "codex-pro-a"), "claude": (SESSION, "claude-max-a")}


def samples(provider="codex", limit="codex", windows=CODEX_WINDOWS, ages=(41, 1), identity=None):
    """The pacer's samples as read_samples gives them: each names its row's own subject and verified pool."""
    subject, pool = OWN[provider] if identity is None else identity
    rows = []
    for name, first, last, reset, duration in windows:
        for used, age in zip((first, last), ages):
            rows.append({"provider": provider, "limit_id": limit, "window": name, "used_percent": used,
                         "resets_at": float(reset), "duration_minutes": duration,
                         "observed_at": NOW - timedelta(minutes=age), "subject": subject, "account_pool": pool})
    return rows


PACED = pace_windows(samples(), now=NOW)
CLAUDE_PACED = pace_windows(samples("claude", "claude", CLAUDE_WINDOWS), now=NOW)


def evidence(worker=None, row=None, paced=None, policy=None, now=NOW):
    return rc.capacity_evidence(copy.deepcopy(WORKER if worker is None else worker),
                                codex_row() if row is None else row,
                                copy.deepcopy(PACED if paced is None else paced),
                                signed() if policy is None else policy, now)


# --- the known block -------------------------------------------------------------------------------

def test_measured_verified_capacity_becomes_one_router_block():
    record = evidence()
    assert (record["verdict"], record["reasons"], record["authority"], record["execution_allowed"]) == (
        "known", [], "none", False)
    assert (record["worker"], record["profile_id"], record["policy_sha256"]) == (
        "codex-tools-1", "codex-sol-high", signed()["sha256"])
    assert record["capacity"] == {
        "observed_utc": (NOW - timedelta(seconds=60)).isoformat(),        # the OLDEST evidence: a paced sample
        "valid_until_utc": (NOW + timedelta(seconds=270)).isoformat(),    # the row age limit comes first
        "state": "available", "billing": "included", "projected_used_percent": 35.5,
        "profile_id": "codex-sol-high", "pool": "codex-pro-a",
        "windows": ["codex/codex/primary", "codex/codex/secondary"], "policy_sha256": signed()["sha256"]}
    assert len(record["evidence_digest"]) == 64 and record == evidence()   # deterministic


def test_the_earliest_limit_bounds_validity():
    # The binding expires first.
    early = evidence(row=codex_row(pool_binding=binding(NOW + timedelta(seconds=100))))
    assert early["capacity"]["valid_until_utc"] == (NOW + timedelta(seconds=100)).isoformat()
    # The pacer's sample age limit after the oldest paced sample comes first (samples 14 min old).
    old_samples = pace_windows(samples(ages=(55, 14)), now=NOW)
    paced = evidence(paced=old_samples, policy=signed(max_observation_age_seconds=3600))
    assert paced["capacity"]["valid_until_utc"] == (NOW + timedelta(seconds=60)).isoformat()


def test_the_age_bound_is_inclusive_but_a_zero_length_validity_is_expired():
    at_bound = evidence(row=codex_row(observed_at=(NOW - timedelta(seconds=300)).isoformat()))
    assert (at_bound["verdict"], at_bound["reasons"]) == ("unknown", ["evidence_expired"])
    inside = evidence(row=codex_row(observed_at=(NOW - timedelta(seconds=299)).isoformat()))
    assert inside["verdict"] == "known"                                  # success twin
    over = evidence(row=codex_row(observed_at=(NOW - timedelta(seconds=301)).isoformat()))
    assert over["reasons"] == ["observation_stale"]


def test_claude_statusline_capacity_needs_a_signed_acceptance_of_its_unknown_provider_timestamp():
    refused = rc.capacity_evidence(CLAUDE_WORKER, claude_row(), CLAUDE_PACED, signed(), NOW)
    assert (refused["verdict"], refused["reasons"]) == ("unknown", ["freshness_not_accepted"])
    accepted = rc.capacity_evidence(CLAUDE_WORKER, claude_row(), CLAUDE_PACED,
                                    signed(accepted_freshness=BOTH_FRESHNESS), NOW)
    assert accepted["verdict"] == "known"
    assert (accepted["capacity"]["windows"], accepted["capacity"]["projected_used_percent"]) == (
        ["claude/claude/five_hour", "claude/claude/seven_day"], 48.0)


def test_exhausted_conserve_and_paid_pools_are_reported_as_such():
    spent = pace_windows(samples(windows=(("primary", 99.0, 100.0, RESET_PRIMARY, 300),
                                          CODEX_WINDOWS[1])), now=NOW)
    exhausted = evidence(row=codex_row(primary=100.0), paced=spent)
    assert (exhausted["capacity"]["state"], exhausted["capacity"]["projected_used_percent"]) == ("exhausted", 100.0)
    conserve = evidence(policy=signed(pools={"codex-pro-a": {"billing": "included", "mode": "conserve"}}))
    assert conserve["capacity"]["state"] == "conserve"
    paid = evidence(policy=signed(pools={"codex-pro-a": {"billing": "paid", "mode": "normal"}}))
    assert (paid["capacity"]["billing"], paid["capacity"]["state"]) == ("paid", "available")


# --- unknown, with a stable reason -------------------------------------------------------------------

def _without(paced, suffix):
    return {key: value for key, value in paced.items() if not key.endswith(suffix)}


def _shifted(paced, key, seconds):
    changed = copy.deepcopy(paced)
    changed[key]["resets_at"] += seconds
    return changed


UNKNOWN_CASES = [
    ("clock_naive", dict(now=NOW.replace(tzinfo=None)), ["clock_invalid"]),
    ("clock_not_datetime", dict(now="2026-09-30T18:00:00Z"), ["clock_invalid"]),
    ("not_plain_data", dict(row=codex_row(extra=(1, 2))), ["input_not_plain_data"]),
    ("worker_extra_key", dict(worker=dict(WORKER, role="producer")), ["worker_invalid"]),
    ("worker_not_a_member", dict(worker=dict(WORKER, worker="grok")), ["worker_invalid"]),
    ("worker_blank_subject", dict(worker=dict(WORKER, subject=" ")), ["subject_unbound"]),
    # RCO2 V3: a subject must name its provider ({kind, id}); the row, the kind and the signed profile must agree.
    ("text_subject", dict(worker=dict(WORKER, subject=SUBJECT)), ["subject_unbound"]),
    ("subject_kind_unknown", dict(worker=dict(WORKER, subject={"kind": "account", "id": SUBJECT})),
     ["subject_unbound"]),
    ("subject_extra_key", dict(worker=dict(WORKER, subject=dict(BOUND, provider="codex"))), ["subject_unbound"]),
    ("subject_id_not_hex", dict(worker=dict(WORKER, subject={"kind": "auth_context", "id": "A" * 64})),
     ["subject_unbound"]),
    ("subject_other_provider", dict(worker=dict(WORKER, subject=CLAUDE_BOUND)), ["provider_mismatch"]),
    ("profile_unsigned", dict(worker=dict(WORKER, profile_id="codex-other")), ["profile_provider_unsigned"]),
    ("profile_signed_for_other", dict(policy=signed(profile_providers={"codex-sol-high": "claude"})),
     ["provider_mismatch"]),
    ("policy_profile_provider", dict(policy=signed(profile_providers={"codex-sol-high": "openai"})),
     ["policy_invalid"]),
    ("policy_profiles_not_a_map", dict(policy=signed(profile_providers=[["codex-sol-high", "codex"]])),
     ["policy_invalid"]),
    ("policy_without_profiles", dict(policy={
        "policy": {k: v for k, v in policy_body().items() if k != "profile_providers"},
        "sha256": digest({k: v for k, v in policy_body().items() if k != "profile_providers"})}), ["policy_invalid"]),
    ("policy_pin", dict(policy=dict(signed(), sha256="0" * 64)), ["policy_digest_mismatch"]),
    ("policy_extra_key", dict(policy={"policy": dict(policy_body(), extra=1),
                                      "sha256": digest(dict(policy_body(), extra=1))}), ["policy_invalid"]),
    ("policy_age_zero", dict(policy=signed(max_observation_age_seconds=0)), ["policy_invalid"]),
    ("policy_age_over", dict(policy=signed(max_observation_age_seconds=3601)), ["policy_invalid"]),
    ("policy_label", dict(policy=signed(accepted_freshness={"codex": ["stale"], "claude": ["fresh"]})),
     ["policy_invalid"]),
    ("policy_no_labels", dict(policy=signed(accepted_freshness={"codex": [], "claude": ["fresh"]})),
     ["policy_invalid"]),
    ("policy_billing", dict(policy=signed(pools={"codex-pro-a": {"billing": "free", "mode": "normal"}})),
     ["policy_invalid"]),
    ("policy_mode", dict(policy=signed(pools={"codex-pro-a": {"billing": "included", "mode": "eco"}})),
     ["policy_invalid"]),
    ("subject", dict(worker=dict(WORKER, subject={"kind": "auth_context", "id": "f" * 64})), ["subject_mismatch"]),
    ("failed", dict(row=codex_row(reason="collection_failed")), ["observation_failed"]),
    ("stale_label", dict(row=codex_row(freshness="unknown_or_stale")), ["freshness_not_accepted"]),
    ("superseded", dict(row=codex_row(freshness="superseded_by_collection_failure")), ["freshness_not_accepted"]),
    ("future", dict(row=codex_row(observed_at=(NOW + timedelta(seconds=1)).isoformat())),
     ["observation_from_the_future"]),
    ("bad_time", dict(row=codex_row(observed_at="yesterday")), ["observation_time_invalid"]),
    ("unverified", dict(row=codex_row(pool_identity_state="binding_expired", account_pool=None)),
     ["pool_unverified"]),
    ("collector_state", dict(row=codex_row(pool_identity_state="unverified_auth_context")), ["pool_unverified"]),
    ("binding_expired", dict(row=codex_row(pool_binding=binding(NOW))), ["pool_binding_expired"]),
    ("binding_missing", dict(row={k: v for k, v in codex_row().items() if k != "pool_binding"}),
     ["pool_binding_expired"]),
    ("pool_unpriced", dict(row=codex_row(account_pool="codex-pro-b")), ["pool_not_in_policy"]),
    ("quota_unknown", dict(row=codex_row(payload={})), ["quota_unknown"]),
    ("paced_not_a_map", dict(paced=[]), ["paced_invalid"]),
    ("window_not_paced", dict(paced=_without(PACED, "secondary")), ["window_not_paced:codex/codex/secondary"]),
    ("rate_unknown", dict(paced=pace_windows(samples(ages=(1, 1)), now=NOW)),
     ["window_rate_unknown:codex/codex/primary", "window_rate_unknown:codex/codex/secondary"]),
    ("other_instance", dict(paced=_shifted(PACED, "codex/codex/primary", 3600)),
     ["window_instance_mismatch:codex/codex/primary"]),
]


@pytest.mark.parametrize("change, reasons", [case[1:] for case in UNKNOWN_CASES], ids=[c[0] for c in UNKNOWN_CASES])
def test_any_gap_in_the_evidence_is_unknown_never_a_capacity(change, reasons):
    record = evidence(**change)
    assert (record["verdict"], record["reasons"], record["capacity"]) == ("unknown", reasons, None)
    assert (record["authority"], record["execution_allowed"]) == ("none", False)


@pytest.mark.parametrize("args", [
    (None, None, None, None, None),
    (object(), object(), object(), object(), NOW),
    ([], "row", 7, {"policy": None, "sha256": None}, NOW),
])
def test_hostile_or_missing_input_never_raises(args):
    record = rc.capacity_evidence(*args)
    assert (record["verdict"], record["capacity"], record["execution_allowed"]) == ("unknown", None, False)


# --- end to end with the F19 router -------------------------------------------------------------------

def _router_inputs(capacity):
    stamp = (NOW - timedelta(minutes=1)).isoformat()
    worker = {"schema": tr.WORKER_SCHEMA, "worker": "codex-tools-1", "kind": "lane", "profile_id": "codex-sol-high",
              "role": {"worker": "codex-tools-1", "roles": ["producer"], "verified": True, "observed_utc": stamp},
              "qualification": [{"task_class": "implementation", "profile_id": "codex-sol-high", "qualified": True,
                                 "observed_utc": stamp, "valid_until_utc": (NOW + timedelta(days=1)).isoformat(),
                                 "receipt_sha256": "d" * 64}],
              "load": {"state": "idle", "observed_utc": stamp}}
    if capacity is not None:
        worker["capacity"] = capacity
    policy = {"schema": tr.POLICY_SCHEMA, "max_evidence_age_seconds": 900, "budget_mode": "steady",
              "class_roles": {c: ["producer"] for c in tr.TASK_CLASSES},
              "class_profiles": {c: ["codex-sol-high"] for c in tr.TASK_CLASSES}}
    task = {"schema": tr.TASK_SCHEMA, "task_id": "t", "revision": "1", "input_digest": "e" * 64,
            "task_class": "implementation", "scope": ["repo:tools/x.py"], "author": "codex-lead-1",
            "created_utc": (NOW - timedelta(hours=1)).isoformat()}
    return task, [worker], [], policy, NOW.isoformat()                # the router takes the ISO string


def test_the_router_routes_on_the_adapted_block_and_keeps_the_worker_unknown_without_it():
    advice = tr.decide(*_router_inputs(evidence()["capacity"]))
    assert (advice["verdict"], advice["recommended"]) == (
        tr.ROUTE, {"worker": "codex-tools-1", "profile_id": "codex-sol-high", "route": "direct"})
    unknown = tr.decide(*_router_inputs(evidence(row=codex_row(freshness="unknown_or_stale"))["capacity"]))
    assert (unknown["verdict"], unknown["unknown"]) == (tr.UNKNOWN, {"codex-tools-1": ["capacity_unknown_or_stale"]})


def test_the_router_waits_on_an_exhausted_or_conserving_pool_and_refuses_a_paid_one():
    spent = pace_windows(samples(windows=(("primary", 99.0, 100.0, RESET_PRIMARY, 300),
                                          CODEX_WINDOWS[1])), now=NOW)
    exhausted = tr.decide(*_router_inputs(evidence(row=codex_row(primary=100.0), paced=spent)["capacity"]))
    assert (exhausted["verdict"], exhausted["unavailable"]) == (tr.WAIT, {"codex-tools-1": ["pool_exhausted"]})
    conserve = evidence(policy=signed(pools={"codex-pro-a": {"billing": "included", "mode": "conserve"}}))
    waiting = tr.decide(*_router_inputs(conserve["capacity"]))
    assert (waiting["verdict"], waiting["unavailable"]) == (tr.WAIT, {"codex-tools-1": ["pool_conserve"]})
    paid = evidence(policy=signed(pools={"codex-pro-a": {"billing": "paid", "mode": "normal"}}))
    refused = tr.decide(*_router_inputs(paid["capacity"]))
    assert (refused["verdict"], refused["ineligible"]) == (tr.HOLD, {"codex-tools-1": ["paid_capacity_not_requestable"]})


def test_a_projection_over_the_trip_line_waits():
    busy = pace_windows(samples(windows=(("primary", 30.0, 50.0, RESET_PRIMARY, 300), CODEX_WINDOWS[1])), now=NOW)
    record = evidence(row=codex_row(primary=50.0), paced=busy)
    assert record["capacity"]["projected_used_percent"] == 140.0         # 30 %/h for 3 h after 50 %
    advice = tr.decide(*_router_inputs(record["capacity"]))
    assert (advice["verdict"], advice["unavailable"]) == (tr.WAIT, {"codex-tools-1": ["budget_over_trip_line"]})


# --- compose: the one pure entry ---------------------------------------------------------------------

def _compose(workers=None, subjects=None, rows=None, now=NOW, task=None):
    base_task, base_workers, attempts, routing, _ = _router_inputs(None)
    return rc.compose(base_task if task is None else task, base_workers if workers is None else workers,
                      {"codex-tools-1": BOUND} if subjects is None else subjects,
                      [codex_row()] if rows is None else rows, copy.deepcopy(PACED), signed(), attempts, routing, now)


def test_compose_gives_a_lane_only_the_capacity_the_adapter_proves_then_routes():
    workers = _router_inputs(None)[1]
    out = _compose(workers=workers)
    assert (out["schema"], out["reasons"], out["authority"], out["execution_allowed"]) == (
        rc.COMPOSED_SCHEMA, [], "none", False)
    assert (out["advice"]["verdict"], out["advice"]["recommended"]["worker"]) == (tr.ROUTE, "codex-tools-1")
    assert out["capacity"] == [evidence()]  # the same record the adapter gives on its own
    assert "capacity" not in workers[0] and out == _compose(workers=workers)  # inputs unchanged; deterministic


def test_compose_drops_a_supplied_capacity_block():
    workers = _router_inputs(evidence()["capacity"])[1]  # a block the caller brought itself
    out = _compose(workers=workers, rows=[])
    assert (out["advice"]["verdict"], out["advice"]["unknown"]) == (
        tr.UNKNOWN, {"codex-tools-1": ["capacity_unknown_or_stale"]})
    assert out["capacity"][0]["reasons"] == ["observation_missing"] and "capacity" in workers[0]


ROW_CASES = [
    ("two_rows", {"codex-tools-1": BOUND}, [codex_row(), codex_row()], ["observation_ambiguous"]),
    ("no_subject", {}, None, ["subject_unknown"]),
    ("blank_subject", {"codex-tools-1": " "}, None, ["subject_unknown"]),
    ("subjects_not_a_map", ["codex-tools-1"], None, ["subject_unknown"]),
    ("other_subject", {"codex-tools-1": BOUND}, [codex_row(auth_context_id="f" * 64)], ["observation_missing"]),
    ("row_without_subject", {"codex-tools-1": BOUND},
     [{k: v for k, v in codex_row().items() if k != "auth_context_id"}], ["observation_missing"]),
    ("rows_not_a_list", {"codex-tools-1": BOUND}, "rows", ["observations_invalid"]),
    # RCO2 V3: a text subject still finds its row but never becomes a capacity; a typed subject ignores other providers.
    ("text_subject", {"codex-tools-1": SUBJECT}, None, ["subject_unbound"]),
    ("only_another_providers_row", {"codex-tools-1": BOUND}, [claude_row(native_thread_id=SUBJECT)],
     ["observation_missing"]),
    ("another_provider_row_with_the_same_field", {"codex-tools-1": BOUND}, [claude_row(auth_context_id=SUBJECT)],
     ["observation_missing"]),
    ("typed_subject_without_id", {"codex-tools-1": {"kind": "auth_context"}}, None, ["subject_unknown"]),
]


@pytest.mark.parametrize("subjects, rows, reasons", [case[1:] for case in ROW_CASES], ids=[c[0] for c in ROW_CASES])
def test_compose_needs_exactly_one_row_of_the_lanes_own_subject(subjects, rows, reasons):
    out = _compose(subjects=subjects, rows=rows)
    assert (out["capacity"][0]["verdict"], out["capacity"][0]["reasons"]) == ("unknown", reasons)
    # RCO2 N-V3a (21:01:10Z): every unknown record still names the lane and profile it is about.
    assert (out["capacity"][0]["worker"], out["capacity"][0]["profile_id"]) == ("codex-tools-1", "codex-sol-high")
    assert out["advice"]["unknown"] == {"codex-tools-1": ["capacity_unknown_or_stale"]}


@pytest.mark.parametrize("subject", [SUBJECT, " ", {"kind": "account", "id": SUBJECT}, dict(BOUND, provider="codex")],
                         ids=["text", "blank", "kind_unknown", "extra_key"])
def test_an_unbound_subject_keeps_the_adapter_record_attributed(subject):
    record = evidence(worker=dict(WORKER, subject=subject))
    assert (record["worker"], record["profile_id"], record["reasons"], record["capacity"]) == (
        "codex-tools-1", "codex-sol-high", ["subject_unbound"], None)


def test_v3_a_lane_never_takes_capacity_from_another_providers_quota_row():
    # RCO2 V3 (20:21:19Z, log ba092a8f): a Codex-profile lane whose subject equals a Claude statusline session id got
    # capacity KNOWN from pool claude-max-a and was ROUTED direct: compose matched rows of ANY provider.
    task, workers, attempts, routing, _ = _router_inputs(None)
    paced = {**copy.deepcopy(PACED), **copy.deepcopy(CLAUDE_PACED)}
    for subject, reasons in ((SESSION, ["subject_unbound"]), (CLAUDE_BOUND, ["provider_mismatch"])):
        out = rc.compose(task, workers, {"codex-tools-1": subject}, [claude_row()], paced,
                         signed(accepted_freshness=BOTH_FRESHNESS), attempts, routing, NOW)
        assert out["advice"]["verdict"] != tr.ROUTE
        assert (out["capacity"][0]["verdict"], out["capacity"][0]["reasons"], out["capacity"][0]["capacity"]) == (
            "unknown", reasons, None)


def test_v3_the_positive_twin_routes_on_its_own_providers_row_beside_a_colliding_foreign_one():
    task, workers, attempts, routing, _ = _router_inputs(None)
    paced = {**copy.deepcopy(PACED), **copy.deepcopy(CLAUDE_PACED)}
    rows = [claude_row(native_thread_id=SUBJECT, auth_context_id=SUBJECT), codex_row()]   # a colliding Claude row
    out = rc.compose(task, workers, {"codex-tools-1": BOUND}, rows, paced,
                     signed(accepted_freshness=BOTH_FRESHNESS), attempts, routing, NOW)
    assert (out["advice"]["verdict"], out["advice"]["recommended"]["worker"]) == (tr.ROUTE, "codex-tools-1")
    assert out["capacity"][0]["capacity"]["pool"] == "codex-pro-a"


def test_compose_never_gives_grok_a_capacity_so_the_router_never_ranks_it():
    lane = _router_inputs(None)[1][0]
    grok = dict(copy.deepcopy(lane), worker="grok", kind="grok", profile_id="grok-default",
                capacity=dict(evidence()["capacity"], profile_id="grok-default"),
                single_flight={"state": "idle", "observed_utc": lane["load"]["observed_utc"]})
    grok["role"]["worker"] = "grok"
    out = _compose(workers=[lane, grok])
    assert (out["advice"]["verdict"], out["advice"]["recommended"]["worker"]) == (tr.ROUTE, "codex-tools-1")
    records = {record["worker"]: record for record in out["capacity"]}
    assert (records["grok"]["verdict"], records["grok"]["reasons"]) == ("unknown", ["no_measured_grok_capacity"])
    assert "capacity_unknown_or_stale" in {**out["advice"]["ineligible"], **out["advice"]["unknown"]}["grok"]


def test_compose_takes_one_aware_clock_and_holds_without_it():
    out = _compose(now=NOW.replace(tzinfo=None))
    assert (out["advice"]["verdict"], out["advice"]["reasons"]) == (tr.HOLD, ["now_invalid"])
    assert out["capacity"][0]["reasons"] == ["clock_invalid"]


@pytest.mark.parametrize("workers", [None, "workers", [None], [{"worker": 7, "kind": "lane"}]],
                         ids=["none", "text", "none_item", "bad_name"])
def test_compose_never_raises_and_leaves_malformed_workers_to_the_router(workers):
    task, _, attempts, routing, _ = _router_inputs(None)
    out = rc.compose(task, workers, {"codex-tools-1": SUBJECT}, [codex_row()], PACED, signed(), attempts, routing, NOW)
    assert (out["advice"]["verdict"], out["capacity"], out["execution_allowed"]) == (tr.HOLD, [], False)


def test_compose_turns_an_unexpected_error_into_a_hold_with_nothing_unproven(monkeypatch):
    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(rc, "_row_for", broken)
    out = _compose()
    assert (out["reasons"], out["capacity"], out["advice"]["verdict"]) == (["compose_error:RuntimeError"], [], tr.HOLD)


# --- RCO1 F19C-1/F19C-2 (afa7d14c review 04:40:23Z) ---------------------------------------------------------

def test_another_accounts_paced_series_with_the_same_reset_is_not_this_rows_rate():
    record = evidence(row=codex_row(primary=95.0, secondary=90.0))  # PACED is the 30->31 / 20->20.1 series
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert len(record["reasons"]) == 2 and all(reason.startswith("window_account_mismatch:codex/")
                                               for reason in record["reasons"])
    assert evidence()["verdict"] == rc.KNOWN  # the row whose own used_percent the pacer saw stays known


class _HostileList(list):
    def __iter__(self):
        raise AssertionError("a hostile container method ran")


class _PlainListSubclass(list):
    pass


class _DictSubclass(dict):
    pass


@pytest.mark.parametrize("wrap", [_PlainListSubclass, _HostileList], ids=["list_subclass", "hostile_list"])
def test_a_worker_list_subclass_never_carries_its_own_capacity_to_the_router(wrap):
    workers = wrap(_router_inputs(evidence()["capacity"])[1])  # a block the caller brought itself
    out = _compose(workers=workers)
    assert (out["reasons"], out["capacity"], out["advice"]["verdict"]) == ([], [], tr.HOLD)


def test_a_dict_subclass_worker_record_never_carries_its_own_capacity_to_the_router():
    workers = [_DictSubclass(worker) for worker in _router_inputs(evidence()["capacity"])[1]]
    out = _compose(workers=workers, rows=[])
    assert out["advice"]["verdict"] == tr.HOLD and out["advice"]["verdict"] != tr.ROUTE
    assert out["capacity"] == []


# --- purity ------------------------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {"os", "sys", "subprocess", "socket", "pathlib", "time", "random", "urllib", "http",
                     "requests", "shutil", "io", "tempfile", "threading", "asyncio", "sqlite3"}
FORBIDDEN_CALLS = {"open", "print", "exec", "eval", "compile", "__import__", "input", "now", "utcnow",
                   "today", "getenv", "system", "read_samples", "status"}


def test_module_is_pure_by_construction():
    tree = ast.parse(Path(rc.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & FORBIDDEN_IMPORTS
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in FORBIDDEN_IMPORTS
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            assert name not in FORBIDDEN_CALLS, name


# --- the paced series must be this row's own subject and pool (F19C-1 binding) -------------------------------

def test_another_subjects_series_with_the_same_reset_and_used_percent_is_never_borrowed():
    # Another account reports exactly this row's reset and used_percent: the numbers agree, the identity does not.
    borrowed = pace_windows(samples(identity=("d" * 64, "codex-pro-a")), now=NOW)
    record = evidence(paced=borrowed)
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert record["reasons"] == ["window_subject_mismatch:codex/codex/primary",
                                 "window_subject_mismatch:codex/codex/secondary"]


def test_another_pools_series_of_the_same_subject_is_never_borrowed():
    record = evidence(paced=pace_windows(samples(identity=(SUBJECT, "codex-pro-b")), now=NOW))
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert record["reasons"] == ["window_pool_mismatch:codex/codex/primary",
                                 "window_pool_mismatch:codex/codex/secondary"]


@pytest.mark.parametrize("identity", [(None, None), (SUBJECT, None), (None, "codex-pro-a")],
                         ids=["unbound", "no-pool", "no-subject"])
def test_an_unbound_paced_series_is_unknown_never_guessed(identity):
    record = evidence(paced=pace_windows(samples(identity=identity), now=NOW))
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert record["reasons"] == ["window_unbound:codex/codex/primary", "window_unbound:codex/codex/secondary"]


def test_an_entry_without_identity_fields_or_with_non_text_identity_is_unbound():
    # Without an identity index (an older pacer), the entry itself must name this row's pair.
    for change in ({"subject": None}, {"account_pool": ["codex-pro-a"]}, {"subject": 7}):
        paced = copy.deepcopy(PACED)
        paced["codex/codex/primary"].pop("identities")
        paced["codex/codex/primary"].update(change)
        assert evidence(paced=paced)["reasons"] == ["window_unbound:codex/codex/primary"], change
    paced = copy.deepcopy(PACED)
    paced["codex/codex/primary"].pop("identities")
    paced["codex/codex/primary"].pop("subject")
    assert evidence(paced=paced)["reasons"] == ["window_unbound:codex/codex/primary"]


def test_two_index_series_naming_this_rows_pair_are_ambiguous_and_never_read():
    mixed = pace_windows(samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0)), now=NOW)
    for entry in mixed.values():
        own = next(series for series in entry["identities"] if series["subject"] == SUBJECT)
        entry["identities"].append(dict(own, forecast_percent_at_reset=1.0))
    record = evidence(paced=mixed)
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert all(reason.startswith("window_subject_mismatch:") for reason in record["reasons"]), record["reasons"]


def test_an_index_series_with_non_text_identity_is_never_this_rows():
    mixed = pace_windows(samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0)), now=NOW)
    for entry in mixed.values():
        for series in entry["identities"]:
            if series["subject"] == SUBJECT:
                series["subject"] = [SUBJECT]
    record = evidence(paced=mixed)
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert all(reason.startswith("window_subject_mismatch:") for reason in record["reasons"]), record["reasons"]


def test_the_own_series_stays_known_when_another_subject_shares_the_key():
    # Interleaved samples of another subject under the same key and reset change neither the rate nor the verdict.
    other = (("primary", 80.0, 81.0, RESET_PRIMARY, 300), ("secondary", 70.0, 70.1, RESET_SECONDARY, 10080))
    mixed = samples() + samples(windows=other, identity=("d" * 64, "codex-pro-b"), ages=(30, 20))
    record = evidence(paced=pace_windows(mixed, now=NOW))
    assert (record["verdict"], record["reasons"]) == (rc.KNOWN, [])
    assert record["capacity"] == evidence()["capacity"]


def test_a_claude_row_needs_its_own_session_series():
    policy = signed(accepted_freshness=BOTH_FRESHNESS)
    own = evidence(worker=CLAUDE_WORKER, row=claude_row(), paced=CLAUDE_PACED, policy=policy)
    assert own["verdict"] == rc.KNOWN
    other = pace_windows(samples("claude", "claude", CLAUDE_WINDOWS, identity=("sess-claude-2", "claude-max-a")),
                         now=NOW)
    record = evidence(worker=CLAUDE_WORKER, row=claude_row(), paced=other, policy=policy)
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert len(record["reasons"]) == 2 and all(reason.startswith("window_subject_mismatch:claude/")
                                               for reason in record["reasons"])


# --- every account keeps its own series when another account sampled last (RCO1 P55-1) ----------------------

OTHER_ID = ("d" * 64, "codex-pro-b")
OTHER_WINDOWS = (("primary", 80.0, 81.0, RESET_PRIMARY, 300), ("secondary", 70.0, 70.1, RESET_SECONDARY, 10080))


def test_an_own_series_stays_known_when_another_account_sampled_last():
    mixed = samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0))
    record = evidence(paced=pace_windows(mixed, now=NOW))
    assert (record["verdict"], record["reasons"]) == (rc.KNOWN, [])
    assert record["capacity"] == evidence()["capacity"]


def test_both_accounts_are_known_from_one_paced_map_each_on_its_own_series():
    paced = pace_windows(samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0)), now=NOW)
    other_worker = dict(WORKER, worker="claude-rco-2", subject={"kind": "auth_context", "id": "d" * 64})
    other_row = codex_row(primary=81.0, secondary=70.1, auth_context_id="d" * 64, account_pool="codex-pro-b")
    pools = dict(policy_body()["pools"], **{"codex-pro-b": {"billing": "included", "mode": "normal"}})
    own = evidence(paced=paced, policy=signed(pools=pools))
    other = evidence(worker=other_worker, row=other_row, paced=paced, policy=signed(pools=pools))
    assert own["verdict"] == rc.KNOWN and own["capacity"]["projected_used_percent"] == 35.5
    assert other["verdict"] == rc.KNOWN, other["reasons"]
    assert other["capacity"]["projected_used_percent"] != own["capacity"]["projected_used_percent"]


def test_an_unbound_newest_series_never_hides_the_own_series():
    unbound = samples(windows=OTHER_WINDOWS, identity=(None, None), ages=(30, 0))
    record = evidence(paced=pace_windows(samples() + unbound, now=NOW))
    assert (record["verdict"], record["reasons"]) == (rc.KNOWN, [])


def test_an_old_pool_series_of_the_same_subject_is_not_the_new_pools_series():
    old_pool = samples(identity=(SUBJECT, "codex-pro-b"), ages=(30, 0))
    record = evidence(paced=pace_windows(samples(identity=(SUBJECT, "codex-pro-b")) + old_pool, now=NOW))
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert all(reason.startswith("window_pool_mismatch:") for reason in record["reasons"]), record["reasons"]


def test_an_own_series_of_another_reset_is_never_this_window_even_beside_a_foreign_series_of_this_reset():
    own_old_reset = [dict(s, resets_at=s["resets_at"] - 7200.0) for s in samples()]
    foreign_same_reset = samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0))
    record = evidence(paced=pace_windows(own_old_reset + foreign_same_reset, now=NOW))
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert any(reason.startswith("window_instance_mismatch:") for reason in record["reasons"]), record["reasons"]


class _HostileList(list):
    pass


class _HostileDict(dict):
    pass


def _with_series(paced, wrap):
    paced = copy.deepcopy(paced)
    for entry in paced.values():
        entry["identities"] = wrap(entry["identities"])
    return paced


@pytest.mark.parametrize("wrap,prefix", [(lambda s: _HostileList(s), "window_subject_mismatch:"),
                                         (lambda s: tuple(s), "input_not_plain_data"),
                                         (lambda s: [_HostileDict(e) for e in s], "window_subject_mismatch:"),
                                         (lambda s: {"x": s}, "window_subject_mismatch:")],
                         ids=["list-subclass", "tuple", "dict-subclass-entries", "not-a-list"])
def test_a_hostile_identity_index_is_never_read(wrap, prefix):
    mixed = pace_windows(samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0)), now=NOW)
    record = evidence(paced=_with_series(mixed, wrap))
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert record["reasons"] and all(reason.startswith(prefix) for reason in record["reasons"]), record["reasons"]


# --- RCO2 2d846 T1/T2: the own-pair match needs the exact pool AND an exact str identity ----------------------

def test_t1_the_same_subject_in_another_pool_sampled_last_never_hides_this_pools_series():
    # Same subject, another verified pool, newest: the top entry names this subject but not this pool.
    other_pool = samples(windows=OTHER_WINDOWS, identity=(SUBJECT, "codex-pro-b"), ages=(30, 0))
    paced = pace_windows(samples() + other_pool, now=NOW)
    assert (paced["codex/codex/primary"]["subject"], paced["codex/codex/primary"]["account_pool"]) == (
        SUBJECT, "codex-pro-b")
    record = evidence(paced=paced)
    assert (record["verdict"], record["reasons"]) == (rc.KNOWN, [])
    assert record["capacity"] == evidence()["capacity"]


class _AnyText(str):
    """A str subclass that claims to equal anything: never an exact identity."""

    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


def test_t2_an_exact_dict_index_series_with_a_lying_str_subclass_identity_is_never_borrowed():
    mixed = pace_windows(samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0)), now=NOW)
    for entry in mixed.values():
        own = next(series for series in entry["identities"] if series["subject"] == SUBJECT)
        # Only a lying series is left that numerically equals the row (same used_percent) but would forecast 1.0.
        entry["identities"] = [series for series in entry["identities"] if series is not own] + [
            dict(own, subject=_AnyText("x"), account_pool=_AnyText("y"), forecast_percent_at_reset=1.0)]
    record = evidence(paced=mixed)
    assert (record["verdict"], record["capacity"]) == (rc.UNKNOWN, None)
    assert record["reasons"] and all(reason.startswith("window_subject_mismatch:")
                                     for reason in record["reasons"]), record["reasons"]


def test_t2_control_the_same_series_with_its_exact_identity_is_read():
    mixed = pace_windows(samples() + samples(windows=OTHER_WINDOWS, identity=OTHER_ID, ages=(30, 0)), now=NOW)
    record = evidence(paced=mixed)
    assert (record["verdict"], record["reasons"]) == (rc.KNOWN, [])
