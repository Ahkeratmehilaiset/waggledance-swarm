# SPDX-License-Identifier: BUSL-1.1
"""F3 pool-binding adapter tests (authored per operator directive; NOT executed yet).

Synthetic observations, receipts and a registry derived from the shipped v2 config.
Every refusal has the same-fixture success twin (test_a_complete_verified_receipt_binds),
and every refusal asserts that no pool is set.
"""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone, tzinfo
import json
from pathlib import Path

import pytest

from tools import bridge_pool_binding as binding
from tools.wd_model_registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 29, 22, 0, tzinfo=timezone.utc)
CONTEXT = "c" * 64
SESSION = "11111111-2222-3333-4444-555555555555"
REFERENCE = "operator reading 2026-09-29 of the plan page; contact ops@example.test"
SHIPPED, _ = load_registry(ROOT / "configs" / "model_registry.json")


def iso(value: datetime) -> str:
    return value.isoformat()


def pool_entry(verified: bool, measured_at: str = "2026-09-29", ttl_seconds: int = 30 * 86400, **values) -> dict:
    """A pool in the exact af1d0ef8 shape: a verified pool is a dated measurement with a TTL."""
    return dict(values, verification="verified" if verified else "unverified",
                measured_at=measured_at if verified else "unknown", ttl_seconds=ttl_seconds if verified else None)


def registry(**pools) -> dict:
    result = copy.deepcopy(SHIPPED)
    result["pools"].update({
        "codex-plus-weekly": pool_entry(True, provider="codex", limit_id="codex", window="weekly", tier="standard",
                                        provenance={"kind": "operator_reading", "reference": "plan page",
                                                    "observer": "operator"}),
        "claude-max-weekly": pool_entry(False, provider="claude", limit_id=None, window="weekly", tier="premium",
                                        provenance={"kind": "plan_transcription", "reference": "plan",
                                                    "observer": None}),
        "claude-max-verified": pool_entry(True, provider="claude", limit_id="claude", window="weekly", tier="premium",
                                          provenance={"kind": "local_measurement", "reference": "m",
                                                      "observer": None}),
    })
    result["pools"].update(pools)
    return result


def codex_observation(**changes) -> dict:
    value = {"schema": "wd.capacity-observation.v1", "provider": "codex", "observed_at": iso(NOW - timedelta(minutes=10)),
             "auth_context_id": CONTEXT, "account_pool": None, "pool_identity_state": "unverified_auth_context",
             "payload": {"rateLimits": {"limitId": "codex", "rateLimitReachedType": None,
                                        "primary": {"usedPercent": 20, "resetsAt": 1790328816}, "secondary": None}},
             "execution_allowed": False}
    value.update(changes)
    return value


def claude_observation(**changes) -> dict:
    value = {"schema": "wd.capacity-observation.v1", "provider": "claude", "observed_at": iso(NOW - timedelta(minutes=10)),
             "native_thread_id": SESSION, "account_pool": None, "pool_identity_state": "unknown",
             "payload": {"rate_limits": {"five_hour": {"used_percentage": 10, "resets_at": 1790328816}}},
             "execution_allowed": False}
    value.update(changes)
    return value


def receipt(**changes) -> dict:
    value = {"schema": "wd.pool-binding-receipt.v1", "receipt_id": "a" * 32, "provider": "codex",
             "pool": "codex-plus-weekly", "limit_ids": ["codex"], "subject": {"kind": "auth_context", "id": CONTEXT},
             "issued_at_utc": iso(NOW - timedelta(hours=1)), "expires_at_utc": iso(NOW + timedelta(hours=1)),
             "provenance": {"kind": "operator_reading", "reference": REFERENCE, "observer": "operator"}}
    value.update(changes)
    return value


def trusting(_receipt):
    return True


def decide(observation=None, body=None, reg=None, verifier=trusting, now=NOW) -> dict:
    return binding.bind_pool(codex_observation() if observation is None else observation,
                             receipt() if body is None else body, registry() if reg is None else reg,
                             verifier=verifier, now=now)


def assert_refused(decision: dict, reason: str) -> None:
    assert decision["reason"] == reason, decision
    assert decision["account_pool"] is None and decision["pool_identity_state"] == "unverified"
    assert decision["execution_allowed"] is False and decision["authority_effect"] == "none"


def test_a_complete_verified_receipt_binds():
    decision = decide()
    assert decision["account_pool"] == "codex-plus-weekly" and decision["pool_identity_state"] == "verified_binding"
    assert decision["reason"] is None and decision["subject_id"] == CONTEXT
    assert decision["receipt_sha256"] == binding.canonical_sha256(receipt())
    assert decision["provenance_kind"] == "operator_reading"
    assert decision["expires_at_utc"] == iso(NOW + timedelta(hours=1))  # receipt ends before the pool TTL
    assert decision["receipt_expires_at_utc"] == iso(NOW + timedelta(hours=1))
    assert decision["pool_fresh_until_utc"] == "2026-10-29T00:00:00+00:00"  # 2026-09-29 + 30 days
    text = json.dumps(decision)
    assert "ops@example.test" not in text and REFERENCE not in text  # free text is never copied
    assert decision["execution_allowed"] is False


def test_a_claude_session_binds_only_through_its_own_receipt():
    body = receipt(provider="claude", pool="claude-max-verified", limit_ids=["claude"],
                   subject={"kind": "native_session", "id": SESSION})
    decision = decide(claude_observation(), body)
    assert decision["account_pool"] == "claude-max-verified"
    assert_refused(decide(claude_observation(), receipt(provider="claude", pool="claude-max-weekly",
                                                        limit_ids=["claude"],
                                                        subject={"kind": "native_session", "id": SESSION})),
                   "pool_state_unverified")


def test_without_a_receipt_the_auth_context_is_never_a_pool():
    # The raw auth context names no pool, even when it looks like a pool id.
    assert_refused(binding.bind_pool(codex_observation(), None, registry(), verifier=trusting, now=NOW),
                   "receipt_missing")
    looks_like_a_pool = codex_observation(auth_context_id="codex-plus-weekly")
    assert_refused(binding.bind_pool(looks_like_a_pool, None, registry(), verifier=trusting, now=NOW),
                   "receipt_missing")


@pytest.mark.parametrize("verifier,reason", [
    (None, "verifier_missing"), ("not callable", "verifier_missing"),
    (lambda r: False, "verifier_refused"), (lambda r: 1, "verifier_refused"),
    (lambda r: "True", "verifier_refused"), (lambda r: None, "verifier_refused"),
])
def test_only_a_verifier_answering_exactly_true_binds(verifier, reason):
    assert_refused(decide(verifier=verifier), reason)


def test_a_failing_verifier_is_a_refusal():
    def broken(_receipt):
        raise RuntimeError("verifier offline")
    assert_refused(decide(verifier=broken), "verifier_failed")


def test_the_verifier_sees_a_copy_and_cannot_change_the_decision():
    seen = []

    def meddling(body):
        seen.append(body)
        body["pool"] = "someone-else"
        return True

    original = receipt()
    decision = decide(body=original, verifier=meddling)
    assert decision["account_pool"] == "codex-plus-weekly" and original["pool"] == "codex-plus-weekly"
    assert seen and seen[0] is not original


def test_a_verifier_closing_over_the_original_receipt_cannot_change_the_result():
    # Tools a673ecb4: the receipt is snapshotted before any check, so mutating the CALLER's
    # object from inside the verifier (not only the verifier's own copy) changes nothing.
    original = receipt()

    def aliasing(_copy):
        original["pool"] = "someone-else"
        original["subject"]["id"] = "d" * 64
        return True

    decision = decide(body=original, verifier=aliasing)
    assert decision["account_pool"] == "codex-plus-weekly" and decision["subject_id"] == CONTEXT
    assert decision["receipt_sha256"] == binding.canonical_sha256(receipt())   # hash of the snapshot


def test_a_pool_limit_the_observation_did_not_measure_is_never_lent_by_a_broader_receipt():
    spark = registry(**{"codex-plus-weekly": dict(registry()["pools"]["codex-plus-weekly"], limit_id="codex_spark")})
    broad = receipt(limit_ids=["codex", "codex_spark"])
    assert_refused(decide(body=broad, reg=spark), "pool_limit_not_observed")   # only "codex" was observed
    both = codex_observation(payload={"rateLimitsByLimitId": {
        "codex": {"limitId": "codex", "primary": None, "secondary": None},
        "codex_spark": {"limitId": "codex_spark", "primary": None, "secondary": None}}})
    assert decide(observation=both, body=broad, reg=spark)["account_pool"] == "codex-plus-weekly"   # twin

def test_the_verifier_is_consulted_only_after_every_other_check():
    calls = []
    decision = decide(body=receipt(limit_ids=["other"]), verifier=lambda r: calls.append(r) or True)
    assert_refused(decision, "limit_not_covered")
    assert calls == []


@pytest.mark.parametrize("body,reason", [
    (dict(receipt(), extra=1), "receipt_shape_invalid"),
    (receipt(schema="wd.pool-binding-receipt.v0"), "receipt_schema_invalid"),
    (receipt(receipt_id="A" * 32), "receipt_shape_invalid"),
    (receipt(provider="grok"), "receipt_provider_invalid"),
    (receipt(provider=["codex"]), "receipt_provider_invalid"),
    (receipt(pool="Codex Plus"), "receipt_shape_invalid"),
    (receipt(limit_ids=[]), "receipt_limits_invalid"),
    (receipt(limit_ids=["codex", "codex"]), "receipt_limits_invalid"),
    (receipt(limit_ids="codex"), "receipt_limits_invalid"),
    (receipt(subject={"kind": "native_session", "id": CONTEXT}), "receipt_subject_invalid"),
    (receipt(subject={"kind": "auth_context", "id": "not-a-digest"}), "receipt_subject_invalid"),
    (receipt(provenance={"kind": "plan_transcription", "reference": "p", "observer": None}),
     "receipt_provenance_not_measured"),
    (receipt(provenance={"kind": "external_benchmark", "reference": "p", "observer": None}),
     "receipt_provenance_not_measured"),
    (receipt(provenance={"kind": ["operator_reading"], "reference": "p", "observer": None}),
     "receipt_provenance_not_measured"),
    (receipt(provenance={"kind": "operator_reading", "reference": " ", "observer": None}),
     "receipt_provenance_invalid"),
    (receipt(issued_at_utc="2026-09-29T21:00:00"), "receipt_time_invalid"),
    (receipt(expires_at_utc=iso(NOW - timedelta(hours=1))), "receipt_lifetime_invalid"),
    (receipt(issued_at_utc=iso(NOW - timedelta(hours=1)), expires_at_utc=iso(NOW + timedelta(hours=24))),
     "receipt_lifetime_invalid"),
])
def test_malformed_receipts_are_refused(body, reason):
    assert_refused(decide(body=body), reason)


@pytest.mark.parametrize("body,observation,reason", [
    (receipt(provider="claude", limit_ids=["claude"], subject={"kind": "native_session", "id": SESSION}),
     None, "provider_mismatch"),
    (receipt(subject={"kind": "auth_context", "id": "d" * 64}), None, "subject_mismatch"),
    (receipt(issued_at_utc=iso(NOW + timedelta(minutes=10)), expires_at_utc=iso(NOW + timedelta(hours=2))),
     None, "receipt_not_yet_valid"),
    (receipt(issued_at_utc=iso(NOW - timedelta(hours=3)), expires_at_utc=iso(NOW - timedelta(minutes=1))),
     None, "receipt_expired"),
    (receipt(issued_at_utc=iso(NOW - timedelta(minutes=5))), None, "observation_outside_receipt_window"),
    (receipt(), codex_observation(observed_at=iso(NOW + timedelta(minutes=30))), "observation_from_the_future"),
    (receipt(limit_ids=["codex_other"]), None, "limit_not_covered"),
    (receipt(pool="no-such-pool"), None, "pool_not_in_registry"),
    (receipt(pool="grok-weekly-shared"), None, "pool_provider_mismatch"),
])
def test_binding_mismatches_are_refused(body, observation, reason):
    assert_refused(decide(observation=observation, body=body), reason)


def test_every_observed_limit_must_be_covered():
    multi = codex_observation(payload={"rateLimitsByLimitId": {
        "codex": {"limitId": "codex", "primary": None, "secondary": None},
        "codex_other": {"limitId": "codex_other", "primary": None, "secondary": None}}})
    assert_refused(decide(observation=multi), "limit_not_covered")
    assert decide(observation=multi, body=receipt(limit_ids=["codex", "codex_other"]))["account_pool"] \
        == "codex-plus-weekly"  # success twin


def test_the_registry_pool_limit_must_be_covered_and_verified():
    reg = registry(**{"codex-plus-weekly": dict(registry()["pools"]["codex-plus-weekly"], limit_id="codex_spark")})
    assert_refused(decide(reg=reg), "pool_limit_not_covered")
    unverified = registry(**{"codex-plus-weekly": pool_entry(
        False, provider="codex", limit_id=None, window="weekly", tier="unknown",
        provenance={"kind": "plan_transcription", "reference": "plan", "observer": None})})
    assert_refused(decide(reg=unverified), "pool_state_unverified")


def test_a_verified_pool_past_its_ttl_is_stale_and_never_binds():
    stale = registry(**{"codex-plus-weekly": pool_entry(
        True, measured_at="2026-08-01", ttl_seconds=86400, provider="codex", limit_id="codex", window="weekly",
        tier="standard", provenance={"kind": "operator_reading", "reference": "plan page", "observer": None})})
    assert_refused(decide(reg=stale), "pool_state_stale")
    future = registry(**{"codex-plus-weekly": pool_entry(
        True, measured_at="2026-10-05", provider="codex", limit_id="codex", window="weekly", tier="standard",
        provenance={"kind": "operator_reading", "reference": "plan page", "observer": None})})
    assert_refused(decide(reg=future), "pool_state_unknown")  # dated in the future: never verified


@pytest.mark.parametrize("reg,reason", [
    ({"schema": "wd.model-registry.v2"}, "registry_invalid"),
    ([], "registry_invalid"),
    ({key: copy.deepcopy(SHIPPED[key]) for key in ("updated_at", "benchmark", "coding_benchmark", "models")}
     | {"schema": "wd.model-registry.v1"}, "registry_has_no_pools"),
])
def test_an_invalid_or_v1_registry_never_binds(reg, reason):
    assert_refused(decide(reg=reg), reason)


@pytest.mark.parametrize("observation,reason", [
    (codex_observation(account_pool="codex-plus-weekly"), "observation_already_claims_a_pool"),
    (codex_observation(reason="collection_failed"), "observation_failed"),
    (codex_observation(pool_identity_state="verified_binding"), "observation_pool_state_unexpected"),
    (codex_observation(auth_context_id=None), "observation_subject_missing"),
    (codex_observation(observed_at="2026-09-29T21:50:00"), "observation_time_invalid"),
    (codex_observation(payload=None), "observation_limits_unknown"),
    (codex_observation(payload={"rateLimitsByLimitId": {}}), "observation_limits_unknown"),
    (codex_observation(payload={"rateLimitsByLimitId": {"codex": {"limitId": "other"}}}), "observation_limits_unknown"),
    (codex_observation(payload={"rateLimits": {"limitId": None}}), "observation_limits_unknown"),
    (claude_observation(payload={"rate_limits": {}}), "observation_limits_unknown"),
    (codex_observation(schema="wd.capacity-observation.v0"), "observation_invalid"),
    (codex_observation(provider="grok"), "observation_invalid"),
    ("not an observation", "observation_invalid"),
])
def test_only_a_successful_collector_observation_can_bind(observation, reason):
    assert_refused(decide(observation=observation), reason)


class Offsetless(tzinfo):
    def utcoffset(self, dt):
        return None   # tzinfo present but no offset: astimezone would silently read LOCAL time


class BrokenZone(tzinfo):
    def utcoffset(self, dt):
        raise RuntimeError("zone database unavailable")


class IntOffset(tzinfo):
    def utcoffset(self, dt):
        return 3600   # datetime.utcoffset() itself raises TypeError for a non-timedelta


class Moment(datetime):
    """A datetime subclass could override utcoffset/astimezone: it is never a clock value."""


class StatefulOffset(tzinfo):
    """An offset on the first read, None afterwards: a second read would fall back to LOCAL time."""

    def __init__(self, first):
        self.first, self.calls = first, 0

    def utcoffset(self, dt):
        self.calls += 1
        return self.first if self.calls == 1 else None


@pytest.mark.parametrize("now", [
    datetime(2026, 9, 29, 22, 0), "2026-09-29T22:00:00Z", None,
    datetime(2026, 9, 29, 22, 0, tzinfo=Offsetless()), datetime(2026, 9, 29, 22, 0, tzinfo=BrokenZone()),
    datetime(2026, 9, 29, 22, 0, tzinfo=IntOffset()),
    datetime(2026, 9, 29, 22, 0, tzinfo=tzinfo()),                # the base tzinfo raises NotImplementedError
    Moment(2026, 9, 29, 22, 0, tzinfo=timezone.utc),              # a subclass, even with a real UTC zone
    datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),   # not representable in UTC
], ids=["naive", "text", "none", "offsetless", "broken", "int_offset", "not_implemented", "subclass",
        "unrepresentable"])
def test_the_clock_must_be_timezone_aware_with_a_real_offset(now):
    assert_refused(decide(now=now), "clock_invalid")   # a code, never binding_error:<Type>


def test_an_aware_non_utc_clock_is_normalized_and_binds():
    decision = decide(now=NOW.astimezone(timezone(timedelta(hours=-7))))
    assert decision["account_pool"] == "codex-plus-weekly" and decision["reason"] is None


@pytest.mark.parametrize("first,reason", [(timedelta(0), None), (timedelta(hours=-2), "receipt_expired")])
def test_the_clock_offset_is_read_once_and_never_falls_back_to_local_time(first, reason):
    zone = StatefulOffset(first)
    decision = decide(now=datetime(2026, 9, 29, 22, 0, tzinfo=zone))   # -02:00 reads as 24:00Z, past 23:00Z
    assert zone.calls == 1
    if reason is None:
        assert decision["account_pool"] == "codex-plus-weekly"
    else:
        assert_refused(decision, reason)


@pytest.mark.parametrize("skew,accepted", [
    (timedelta(minutes=5), True),                      # the 5-minute future skew is INCLUSIVE
    (timedelta(minutes=5, seconds=1), False),          # one second more refuses
])
def test_the_future_skew_boundary_is_five_minutes_inclusive(skew, accepted):
    ahead = iso(NOW + skew)
    decision = decide(observation=codex_observation(observed_at=ahead), body=receipt(issued_at_utc=ahead))
    if accepted:
        assert decision["account_pool"] == "codex-plus-weekly"
    else:
        assert_refused(decision, "receipt_not_yet_valid")
    later = decide(observation=codex_observation(observed_at=ahead))   # receipt issued an hour ago
    if accepted:
        assert later["account_pool"] == "codex-plus-weekly"
    else:
        assert_refused(later, "observation_from_the_future")


def test_unhashable_and_hostile_values_refuse_without_raising():
    hostile = receipt(subject={"kind": ["auth_context"], "id": {"x": 1}}, limit_ids=[["codex"]])
    decision = decide(body=hostile)
    assert decision["account_pool"] is None and decision["reason"]
    assert decide(observation=codex_observation(provider={"codex": 1}))["reason"] == "observation_invalid"


def test_the_decision_is_deterministic():
    assert decide() == decide()


_DATETIME_FIELDS = frozenset({"year", "month", "day", "hour", "minute", "second", "microsecond", "tzinfo", "fold"})


def _call_names(tree: ast.AST) -> set:
    """Every call name, except a keyword-only ``.replace(<datetime fields>=...)``: that is a datetime value
    operation (``_aware_utc`` marks UTC with ``.replace(tzinfo=...)``), never a filesystem call.
    ``os.replace(src, dst)``, ``Path.replace(target)``, keyword ``os.replace(src=..., dst=...)`` and
    ``**kwargs`` calls still count. The same rule as tests/tools/test_bridge_wake_telemetry.py (9d794346):
    this check was red at 7779e9a2, whose composition did not run these fixtures."""
    return {node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(tree) if isinstance(node, ast.Call)
            and not (isinstance(node.func, ast.Attribute) and node.func.attr == "replace" and not node.args
                     and node.keywords and all(keyword.arg in _DATETIME_FIELDS for keyword in node.keywords))}


@pytest.mark.parametrize("snippet,counted", [
    ("os.replace(a, b)", True), ("path.replace(target)", True), ("os.replace(src=a, dst=b)", True),
    ("path.replace(**changes)", True), ("replace(a, b)", True), ("moment.replace(tzinfo=timezone.utc)", False),
    ("moment.replace(hour=0, minute=0)", False),
], ids=["os_replace", "path_replace", "keyword_os_replace", "star_star", "bare_name", "tzinfo", "fields"])
def test_the_purity_check_counts_every_filesystem_replace_and_only_exempts_datetime_fields(snippet, counted):
    assert ("replace" in _call_names(ast.parse(snippet))) is counted


def test_module_is_pure_by_construction():
    source = (ROOT / "tools" / "bridge_pool_binding.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])}
    assert imported <= {"__future__", "copy", "datetime", "hashlib", "json", "re", "typing", "tools"}, imported
    calls = _call_names(tree)
    assert not calls & {"open", "write", "write_text", "write_bytes", "unlink", "remove", "replace", "rename",
                        "system", "popen", "run", "now", "utcnow", "getenv"}, calls


def test_a_pool_ttl_ending_before_the_receipt_bounds_the_binding():
    short = registry(**{"codex-plus-weekly": pool_entry(
        True, measured_at="2026-09-29T21:30:00Z", ttl_seconds=3600, provider="codex", limit_id="codex",
        window="weekly", tier="standard",
        provenance={"kind": "operator_reading", "reference": "plan page", "observer": None})})
    decision = decide(reg=short)  # now 22:00, pool fresh until 22:30, receipt until 23:00
    assert decision["account_pool"] == "codex-plus-weekly"
    assert decision["expires_at_utc"] == "2026-09-29T22:30:00+00:00"  # MIN(receipt, pool freshness)
    assert decision["receipt_expires_at_utc"] == iso(NOW + timedelta(hours=1))
    # Negative twin: after the pool TTL but still inside the receipt window, nothing binds.
    assert_refused(decide(reg=short, now=datetime(2026, 9, 29, 22, 40, tzinfo=timezone.utc)), "pool_state_stale")


def test_an_undated_verified_pool_is_refused_by_the_registry_itself():
    undated = registry(**{"codex-plus-weekly": dict(registry()["pools"]["codex-plus-weekly"], measured_at="unknown")})
    assert_refused(decide(reg=undated), "registry_invalid")  # verified needs a date and a TTL (af1d0ef8)
    no_ttl = registry(**{"codex-plus-weekly": dict(registry()["pools"]["codex-plus-weekly"], ttl_seconds=None)})
    assert_refused(decide(reg=no_ttl), "registry_invalid")
    assert decide()["account_pool"] == "codex-plus-weekly"  # success twin

# -- Tools 7e: a plain-data entry snapshot BEFORE any caller code (the clock's tzinfo, the verifier) --

class MutatingZone(tzinfo):
    """utcoffset() is caller code: it edits the caller's observation, receipt and registry."""

    def __init__(self, observation, body, reg):
        self.targets, self.calls = (observation, body, reg), 0

    def utcoffset(self, dt):
        self.calls += 1
        observation, body, reg = self.targets
        observation["auth_context_id"] = "f" * 64
        body["pool"] = "another-pool"
        reg["pools"]["codex-plus-weekly"]["verification"] = "unverified"
        return timedelta(0)


def test_the_entry_snapshot_is_taken_before_the_clock_offset_runs():
    observation, body, reg = codex_observation(), receipt(), registry()
    zone = MutatingZone(observation, body, reg)
    decision = binding.bind_pool(observation, body, reg, verifier=trusting,
                                 now=datetime(2026, 9, 29, 22, 0, tzinfo=zone))
    assert zone.calls == 1 and body["pool"] == "another-pool"                   # the edits really happened
    assert decision["account_pool"] == "codex-plus-weekly" and decision["subject_id"] == CONTEXT   # the snapshot


class AliasingDict(dict):
    def __deepcopy__(self, memo):
        return self   # a "copy" that is the caller's own object


class Hooked:
    def __deepcopy__(self, memo):
        raise AssertionError("a caller copy hook must never run")


class CountingZone(tzinfo):
    def __init__(self):
        self.calls = 0

    def utcoffset(self, dt):
        self.calls += 1
        return timedelta(0)


@pytest.mark.parametrize("which,value", [
    ("observation", AliasingDict(codex_observation())),
    ("body", receipt(provenance=AliasingDict({"kind": "operator_reading", "reference": REFERENCE,
                                              "observer": "operator"}))),
    ("body", receipt(limit_ids=["codex", Hooked()])),
    ("reg", dict(registry(), note=float("nan"))),
    ("reg", dict(registry(), pools={1: "not a str key"})),
], ids=["aliasing_observation", "aliasing_nested_receipt", "hooked_value", "nan_in_registry", "int_key_in_registry"])
def test_hostile_or_custom_values_are_refused_before_any_callback(which, value):
    zone, verified = CountingZone(), []
    args = {"observation": codex_observation(), "body": receipt(), "reg": registry()}
    args[which] = value
    decision = binding.bind_pool(args["observation"], args["body"], args["reg"],
                                 verifier=lambda r: verified.append(r) or True,
                                 now=datetime(2026, 9, 29, 22, 0, tzinfo=zone))
    assert_refused(decision, "input_not_plain_data")
    assert zone.calls == 0 and verified == []                                   # no caller code ran


def test_a_plain_snapshot_is_equal_unaliased_and_the_plain_path_still_binds():
    original = {"a": [1, 2.5, None, True, ("t", {"k": "v"})], "b": {"c": "d"}}
    copied = binding.plain_snapshot(original)
    assert copied == original and copied is not original and copied["b"] is not original["b"]
    zone = CountingZone()
    decision = binding.bind_pool(codex_observation(), receipt(), registry(), verifier=trusting,
                                 now=datetime(2026, 9, 29, 22, 0, tzinfo=zone))
    assert decision["account_pool"] == "codex-plus-weekly" and zone.calls == 1   # success twin
