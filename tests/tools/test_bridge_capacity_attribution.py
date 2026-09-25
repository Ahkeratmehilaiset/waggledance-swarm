# SPDX-License-Identifier: BUSL-1.1
"""Regression tests for capacity attribution, recency, readiness and wiring.

Revision 2. The revision-1 suite is carried forward; the tests whose semantics
changed are marked REVISED with the reason, because the change was a correction
to my own overstatement rather than a drift in expectations:

* auth_context_id equality is a machine-local credential-store fingerprint, not
  a shared-pool candidate, so the vocabulary changed.
* an unauthenticated attestation no longer makes anything usable, so the
  attested-denominator tests now assert the opposite.

The acceptance cases requested for this slice are grouped under ACCEPTANCE.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import pytest
from pathlib import Path
import subprocess
import sys

from tools.bridge_capacity_attribution import (
    ATTESTATION_SCHEMA,
    COST_UNMEASURED,
    OBSERVATION_SCHEMA,
    POOL_DIFFERENT_LOCAL_STORE,
    POOL_INSUFFICIENT,
    POOL_MEMBERSHIP_UNKNOWN,
    POOL_SAME_LOCAL_STORE,
    POOL_SINGLE_UNKNOWN,
    READY_NOT,
    READY_UNKNOWN,
    VALIDITY_CLOSED,
    VALIDITY_OPEN,
    VALIDITY_UNPROVABLE,
    InputError,
    attribute,
    attribution_block,
    build_report,
    classify_local_recency,
    classify_readiness,
    classify_validity,
    load_attestation,
    load_document,
    provider_windows,
    strict_loads,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_capacity_attribution.py"
COLLECTOR = ROOT / "tools" / "bridge_capacity_collector.py"

NOW = datetime(2026, 9, 24, 15, 0, 0, tzinfo=timezone.utc)
_N = {"i": 0}


def _obs(**over):
    _N["i"] += 1
    base = {
        "schema": OBSERVATION_SCHEMA,
        "provider": "codex",
        "source_ref": "codex:account/rateLimits/read",
        "observed_at": (NOW - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
        "account_pool": None,
        "pool_identity_state": "unverified_auth_context",
        "auth_context_id": "ctx-a",
        "payload": {"rate_limits": {
            "five_hour": {"used_percentage": 1, "resets_at": 1790277600},
            "seven_day": {"used_percentage": 5, "resets_at": 1790791200}}},
    }
    base.update(over)
    return base


def _attestation(**over):
    entry = {"auth_context_id": "ctx-a", "pool_id": "pool-1", "attester": "operator",
             "source": "console note", "attested_at": "2026-09-24T14:00:00Z"}
    entry.update(over)
    return {"schema": ATTESTATION_SCHEMA, "entries": [entry]}


# --- ACCEPTANCE 1: missing identity -> unknown ------------------------------


def test_acceptance_missing_identity_is_unknown():
    row = attribute([_obs(auth_context_id=None)], now=NOW)["rows"][0]
    assert row["pool"]["local_store_relation"] == POOL_INSUFFICIENT
    assert row["pool"]["provider_pool_membership"] == POOL_MEMBERSHIP_UNKNOWN
    assert row["pool"]["pool_id"] is None


# --- ACCEPTANCE 2: same lane name, different context ------------------------


def test_acceptance_same_lane_different_context_infers_nothing():
    """REVISED: each context appears once, so there is no comparator at all.

    Revision 2 returned different_local_credential_store here, which asserted a
    relation nothing supported. The honest label is the single-observation one.
    """
    rows = attribute(
        [_obs(agent="fable-5", lane="fable-5", auth_context_id="ctx-a"),
         _obs(agent="fable-5", lane="fable-5", auth_context_id="ctx-b")],
        now=NOW,
    )["rows"]
    assert {r["pool"]["local_store_relation"] for r in rows} == {POOL_SINGLE_UNKNOWN}
    for r in rows:
        assert r["pool"]["provider_pool_membership"] == POOL_MEMBERSHIP_UNKNOWN
        assert "independent" not in r["pool"]["local_store_relation"]


def test_lane_labels_never_change_attribution():
    plain = attribute([_obs()], now=NOW)["rows"][0]["pool"]
    labelled = attribute([_obs(agent="x", lane="y", role="z", session_id="s",
                               native_thread_id="t", task_id="q")], now=NOW)["rows"][0]["pool"]
    assert plain == labelled


# --- ACCEPTANCE 3: stale or future observations -> no availability ----------


def test_acceptance_stale_or_future_observation_is_not_ready():
    stale = _obs(observed_at=(NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"))
    future = _obs(observed_at=(NOW + timedelta(minutes=9)).isoformat().replace("+00:00", "Z"))
    for obs in (stale, future):
        row = attribute([obs], now=NOW)["rows"][0]
        assert row["readiness"]["readiness"] == READY_NOT
        assert any(r.startswith("usage_") for r in row["readiness"]["reasons"])


# --- ACCEPTANCE 4: future reset with stale usage -> not ready ---------------


def test_acceptance_future_reset_with_stale_usage_is_not_ready():
    obs = _obs(observed_at=(NOW - timedelta(hours=3)).isoformat().replace("+00:00", "Z"))
    validity = classify_validity(obs, NOW)
    assert validity["provider_validity_state"] == VALIDITY_OPEN, "window is still open"
    row = attribute([obs], now=NOW)["rows"][0]
    assert row["readiness"]["readiness"] == READY_NOT
    assert "usage_stale_local_observation" in row["readiness"]["reasons"]
    assert "NOT evidence of remaining headroom" in validity["headroom_note"]


def test_open_window_alone_never_yields_ready():
    row = attribute([_obs()], now=NOW)["rows"][0]
    # Even fully recent, readiness stays unknown: no provider timestamp exists.
    assert row["readiness"]["readiness"] == READY_UNKNOWN
    assert row["readiness"]["reasons"] == ["no_provider_timestamp_for_present_usage"]


# --- ACCEPTANCE 5: conflicting, expired or forged attestation ---------------


def test_acceptance_attestation_never_enables_dispatch_or_cost():
    att = load_attestation(_attestation(), now=NOW)
    result = attribute([_obs()], now=NOW, attestation=att)
    assert result["dispatch_enabled"] is False
    assert result["cost_denominator_available"] is False
    declaration = result["rows"][0]["pool"]["declaration"]
    assert declaration["authentication"] == "none"
    assert declaration["authorises_dispatch"] is False
    assert declaration["establishes_cost_denominator"] is False
    assert "forged entry is indistinguishable" in declaration["why"]


def test_acceptance_conflicting_attestation_is_not_accepted():
    doc = {"schema": ATTESTATION_SCHEMA, "entries": [
        dict(auth_context_id="ctx-a", pool_id="pool-1", attester="a", source="s",
             attested_at="2026-09-24T14:00:00Z"),
        dict(auth_context_id="ctx-a", pool_id="pool-2", attester="a", source="s",
             attested_at="2026-09-24T14:05:00Z")]}
    att = load_attestation(doc, now=NOW)
    assert att["ctx-a"]["conflicting"] is True
    row = attribute([_obs()], now=NOW, attestation=att)["rows"][0]
    assert row["pool"]["declaration"]["accepted_as_provenance"] is False
    assert row["pool"]["pool_id"] is None


def test_acceptance_expired_attestation_is_not_accepted():
    old = _attestation(attested_at="2026-01-01T00:00:00Z")
    att = load_attestation(old, now=NOW)
    assert att["ctx-a"]["expired"] is True
    row = attribute([_obs()], now=NOW, attestation=att)["rows"][0]
    assert row["pool"]["declaration"]["accepted_as_provenance"] is False
    assert row["pool"]["pool_id"] is None


def test_acceptance_future_dated_attestation_is_expired():
    att = load_attestation(_attestation(attested_at="2027-01-01T00:00:00Z"), now=NOW)
    assert att["ctx-a"]["expired"] is True


def test_forged_attestation_is_indistinguishable_so_none_authorise():
    """There is no authentication, so a well-formed forgery parses identically."""
    genuine = load_attestation(_attestation(attester="operator"), now=NOW)
    forged = load_attestation(_attestation(attester="operator", source="fabricated"), now=NOW)
    assert genuine["ctx-a"]["authentication"] == forged["ctx-a"]["authentication"] == "none"
    for att in (genuine, forged):
        assert attribute([_obs()], now=NOW, attestation=att)["dispatch_enabled"] is False


# --- ACCEPTANCE 6: zero accepted units -> no cost per unit ------------------


def test_acceptance_zero_accepted_units_gives_no_cost_per_unit():
    for units in (0, -1, None, True, 2.5):
        row = attribute([_obs(accepted_work_units=units,
                              cost={"monetary": {"amount": 5, "currency": "USD"}})],
                        now=NOW)["rows"][0]
        assert row["cost"]["cost_per_accepted_unit"] is None
        assert row["cost"]["cost_state"] == COST_UNMEASURED
        assert "accepted_work_denominator" in row["cost"]["missing_elements"]


def test_cost_elements_stay_separate_and_any_gap_leaves_unmeasured():
    row = attribute([_obs(accepted_work_units=10,
                          cost={"monetary": {"amount": 5, "currency": "USD"}})],
                    now=NOW)["rows"][0]["cost"]
    assert row["cost_amount"] == 5.0 and row["currency"] == "USD"
    assert row["accepted_work_denominator"] == 10
    # Pool attribution is still missing, so cost remains unmeasured.
    assert row["pool_window_attribution"] is None
    assert row["missing_elements"] == ["pool_window_attribution"]
    assert row["cost_state"] == COST_UNMEASURED


def test_negative_cost_amount_is_dropped():
    row = attribute([_obs(cost={"monetary": {"amount": -5, "currency": "USD"}})],
                    now=NOW)["rows"][0]["cost"]
    assert row["cost_amount"] is None
    assert "cost_amount" in row["missing_elements"]


# --- ACCEPTANCE 7: status error path performs no DB write ------------------


def test_acceptance_status_error_path_creates_no_store_file(tmp_path):
    store = tmp_path / "absent.sqlite"
    proc = subprocess.run(
        [sys.executable, str(COLLECTOR), "--status", "--attribution", "--store", str(store)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    payload = json.loads(proc.stdout)
    assert payload["schema"] == "wd.capacity-status.v1"
    assert payload["execution_allowed"] is False
    assert not store.exists(), "the status error path must not create the store"
    assert list(tmp_path.iterdir()) == [], "no file may be written on the status error path"


def test_status_without_attribution_flag_is_unchanged(tmp_path):
    """Default behaviour must stay byte-identical to the unwired collector."""
    store = tmp_path / "absent.sqlite"
    plain = subprocess.run(
        [sys.executable, str(COLLECTOR), "--status", "--store", str(store)],
        capture_output=True, text=True, cwd=str(ROOT))
    payload = json.loads(plain.stdout)
    assert "attribution" not in payload
    assert not store.exists()


# --- wiring -----------------------------------------------------------------


def test_attribution_block_is_additive_and_never_raises():
    status_doc = {"schema": "wd.capacity-status.v1", "observations": [_obs()]}
    block = attribution_block(status_doc, now=NOW)
    assert block["state"] == "available"
    assert block["dispatch_enabled"] is False
    # original keys untouched
    assert status_doc["schema"] == "wd.capacity-status.v1"


def test_attribution_block_degrades_instead_of_raising():
    for bad in ({"observations": "not-a-list"}, {}, {"observations": [object()]}):
        block = attribution_block(bad, now=NOW)
        assert block["state"] in {"available", "unavailable"}


# --- REVISED from revision 1 ------------------------------------------------


def test_revised_shared_context_is_local_store_not_pool_candidate():
    """REVISED: rev-1 called this shared_pool_candidate, which overstated it.

    auth_context_id is sha256 over the resolved CODEX_HOME path plus an account
    shape, so equality proves a shared local credential directory only.
    """
    rows = attribute([_obs(), _obs()], now=NOW)["rows"]
    assert {r["pool"]["local_store_relation"] for r in rows} == {POOL_SAME_LOCAL_STORE}
    for r in rows:
        assert r["pool"]["provider_pool_membership"] == POOL_MEMBERSHIP_UNKNOWN
        assert "NOT shared provider quota membership" in r["pool"]["proves"]


def test_revised_attestation_does_not_create_a_denominator():
    """REVISED: rev-1 set cost_attribution_usable true on attestation. Wrong."""
    att = load_attestation(_attestation(), now=NOW)
    result = attribute([_obs(accepted_work_units=10,
                             cost={"monetary": {"amount": 5, "currency": "USD"}})],
                       now=NOW, attestation=att)
    assert result["cost_denominator_available"] is False
    assert result["rows"][0]["cost"]["cost_state"] == COST_UNMEASURED


# --- carried forward from revision 1 ---------------------------------------


def test_provider_resets_at_is_used_as_real_evidence():
    windows = provider_windows(_obs())
    # Verified against GNU date and a bare datetime call.
    assert windows[0]["provider_resets_at"] == "2026-09-24T19:20:00Z"
    assert windows[1]["provider_resets_at"] == "2026-09-30T18:00:00Z"


def test_used_percent_never_gets_an_observation_timestamp():
    for w in provider_windows(_obs()):
        assert w["used_percent_observed_at"] is None
        assert "does not establish present consumption" in w["used_percent_note"]


def test_validity_closed_after_boundary_and_unprovable_without_one():
    assert classify_validity(_obs(), NOW + timedelta(days=30))[
        "provider_validity_state"] == VALIDITY_CLOSED
    bare = _obs(payload={"rate_limits": {"w": {"used_percentage": 3}}})
    assert classify_validity(bare, NOW)["provider_validity_state"] == VALIDITY_UNPROVABLE


def test_absurd_epoch_boundaries_are_rejected():
    for bad in (0, -1, 10**18, float("nan"), float("inf"), "soon", True):
        obs = _obs(payload={"rate_limits": {"w": {"used_percentage": 1, "resets_at": bad}}})
        assert provider_windows(obs)[0]["provider_resets_at"] is None, bad


def test_local_recency_states_and_skew():
    assert classify_local_recency(_obs(), NOW)["local_recency_state"] == "recent_local_observation"
    old = _obs(observed_at=(NOW - timedelta(hours=2)).isoformat().replace("+00:00", "Z"))
    assert classify_local_recency(old, NOW)["local_recency_state"] == "stale_local_observation"
    ahead = _obs(observed_at=(NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"))
    assert classify_local_recency(ahead, NOW)[
        "local_recency_state"] == "future_observed_at_clock_skew"
    assert classify_local_recency(_obs(observed_at=None), NOW)[
        "local_recency_state"] == "unknown_no_observed_at"


def test_recent_local_record_can_carry_a_closed_window():
    late = NOW + timedelta(days=30)
    obs = _obs(observed_at=(late - timedelta(seconds=5)).isoformat().replace("+00:00", "Z"))
    assert classify_local_recency(obs, late)["local_recency_state"] == "recent_local_observation"
    assert classify_validity(obs, late)["provider_validity_state"] == VALIDITY_CLOSED
    assert classify_readiness(classify_validity(obs, late),
                              classify_local_recency(obs, late))["readiness"] == READY_NOT


def test_attestation_requires_full_provenance_and_parsable_date():
    for field in ("attester", "source", "pool_id", "auth_context_id"):
        try:
            load_attestation(_attestation(**{field: "  "}), now=NOW)
        except InputError as exc:
            assert field in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"expected InputError for blank {field}")
    try:
        load_attestation(_attestation(attested_at="nope"), now=NOW)
    except InputError as exc:
        assert "attested_at" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected InputError")


def test_unsupported_schemas_are_rejected():
    try:
        load_attestation({"schema": "other.v1", "entries": []}, now=NOW)
    except InputError as exc:
        assert "unsupported attestation schema" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected InputError")
    result = attribute([_obs(schema="other.v1"), _obs()], now=NOW)
    assert result["rejected"] == [{"index": 0, "reasons": ["unsupported_observation_schema"]}]


def test_strict_json_and_bounded_read(tmp_path):
    for text, fragment in (('{"a":1,"a":2}', "duplicate JSON key"),
                           ('{"x": NaN}', "non-finite JSON constant")):
        try:
            strict_loads(text)
        except InputError as exc:
            assert fragment in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"expected InputError for {text}")
    path = tmp_path / "in.json"
    path.write_text(json.dumps({"observations": [_obs()]}), encoding="utf-8")
    try:
        load_document(path, max_bytes=10)
    except InputError as exc:
        assert "exceeds the 10-byte bound" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected InputError")


def test_report_documents_meaning_and_absent_evidence():
    report = build_report({"observations": [_obs()]}, base_commit="d25e185e", now=NOW)
    assert "machine-local credential-store fingerprint" in report["auth_context_id_meaning"]
    assert any("no provider endpoint states quota-pool membership" in s
               for s in report["absent_supported_evidence"])
    assert any("emits no auth context at all" in s for s in report["absent_supported_evidence"])
    assert "lane" in report["never_inferred_from"]


# --- CLI --------------------------------------------------------------------


def test_cli_reports_no_dispatch_and_no_denominator(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"observations": [_obs()]}), encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path), "--json"],
                          capture_output=True, text=True, cwd=str(ROOT), check=True)
    report = json.loads(proc.stdout)
    assert report["dispatch_enabled"] is False
    assert report["cost_denominator_available"] is False


def test_cli_rejects_duplicate_keys_with_exit_two(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"observations": [], "observations": []}', encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--input", str(path), "--json"],
                          capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 2
    assert "duplicate JSON key" in proc.stderr


# --- REVISION 3 regressions: defects the Lead reproduced via runpy ----------


def test_rev3_overflowing_float_literal_is_rejected_recursively():
    """1e999 parses to inf in stock json; the strict claim must actually hold."""
    for text in ('{"x": 1e999}', '{"x": -1e999}', '{"a": {"b": [1e999]}}',
                 '{"a": [[[{"deep": 1e999}]]]}'):
        try:
            strict_loads(text)
        except InputError as exc:
            assert "non-finite" in str(exc)
        else:  # pragma: no cover - guard
            raise AssertionError(f"expected InputError for {text}")


def test_rev3_overflow_sized_integer_is_rejected():
    try:
        strict_loads('{"x": ' + "9" * 400 + "}")
    except InputError as exc:
        assert "too large" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected InputError for an overflow-sized integer")


def test_rev3_finite_numbers_still_parse():
    assert strict_loads('{"a": 1.5, "b": -2, "c": 0}') == {"a": 1.5, "b": -2, "c": 0}


def test_rev3_naive_timestamp_is_refused_not_assumed_utc():
    from tools.bridge_capacity_attribution import _parse_utc, _timestamp_state
    assert _parse_utc("2026-09-24T12:00:00") is None
    assert _timestamp_state("2026-09-24T12:00:00") == "timezone_unknown"
    assert _parse_utc("2026-09-24T12:00:00Z") is not None
    assert _parse_utc("2026-09-24T14:00:00+02:00") is not None


def test_rev3_timezone_missing_is_reported_distinctly():
    row = classify_local_recency(_obs(observed_at="2026-09-24T12:00:00"), NOW)
    assert row["local_recency_state"] == "unknown_timezone_missing"
    assert "assumed to be UTC" in row["clock_note"]
    assert classify_local_recency(_obs(observed_at="nonsense"), NOW)[
        "local_recency_state"] == "unknown_unparsable_observed_at"


def test_rev3_single_observation_claims_no_relation():
    from tools.bridge_capacity_attribution import classify_pool
    row = classify_pool({"auth_context_id": "only"}, context_counts={"only": 1},
                        attestation={})
    assert row["local_store_relation"] == POOL_SINGLE_UNKNOWN
    assert "no comparator" in row["basis"]
    assert "no relation to any other store can be established" in row["proves"]


def test_rev3_shared_context_still_reports_same_store():
    rows = attribute([_obs(), _obs()], now=NOW)["rows"]
    assert {r["pool"]["local_store_relation"] for r in rows} == {POOL_SAME_LOCAL_STORE}


def test_rev3_attestation_file_read_is_bounded(tmp_path):
    from tools.bridge_capacity_attribution import load_json_file
    path = tmp_path / "att.json"
    path.write_text(json.dumps(_attestation()), encoding="utf-8")
    try:
        load_json_file(path, max_bytes=10)
    except InputError as exc:
        assert "exceeds the 10-byte bound" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected InputError")
    assert load_json_file(path)["schema"] == ATTESTATION_SCHEMA


def test_rev3_errors_are_concise_and_do_not_echo_content(tmp_path):
    """A malformed document may hold values we must not repeat back."""
    from tools.bridge_capacity_attribution import load_json_file, load_document
    secret = "SUPERSECRETVALUE"
    path = tmp_path / "bad.json"
    path.write_text('{"observations": [' + secret + "]}", encoding="utf-8")
    for loader in (load_json_file, load_document):
        try:
            loader(path)
        except InputError as exc:
            assert secret not in str(exc)
            assert "line" in str(exc) and "column" in str(exc)
        else:  # pragma: no cover - guard
            raise AssertionError("expected InputError")


def test_rev3_attribution_block_reason_is_sanitised():
    block = attribution_block({"observations": [{"schema": OBSERVATION_SCHEMA,
                                                 "auth_context_id": "x"}]}, now=NOW)
    assert block["state"] in {"available", "unavailable"}
    if block["state"] == "unavailable":
        assert block["reason"] in {"input_rejected_by_strict_validation"} or             block["reason"].startswith("classifier_error:")


def test_rev3_no_dispatch_and_no_cost_readiness_preserved():
    att = load_attestation(_attestation(), now=NOW)
    result = attribute([_obs(), _obs()], now=NOW, attestation=att)
    assert result["dispatch_enabled"] is False
    assert result["cost_denominator_available"] is False
    for row in result["rows"]:
        assert row["cost"]["cost_state"] == COST_UNMEASURED
        assert row["cost"]["cost_per_accepted_unit"] is None


def test_lead_cli_attestation_path_executes(tmp_path):
    source = tmp_path / "status.json"
    declaration = tmp_path / "attestation.json"
    source.write_text(json.dumps({"observations": [_obs()]}), encoding="utf-8")
    declaration.write_text(json.dumps(_attestation()), encoding="utf-8")
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source),
                             "--attestation", str(declaration), "--json"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["dispatch_enabled"] is False


def test_lead_duplicate_key_error_does_not_echo_key():
    secret = "PRIVATE_KEY_MARKER"
    try:
        strict_loads(json.dumps({secret: 1})[:-1] + ',"' + secret + '":2}')
    except InputError as exc:
        assert secret not in str(exc)
    else:
        raise AssertionError("duplicate key accepted")


def test_lead_collector_module_import_supports_attribution(monkeypatch, capsys):
    from tools import bridge_capacity_collector as collector
    monkeypatch.setattr(collector, "status", lambda path: {"observations": []})
    assert collector.main(["--status", "--attribution", "--store", "unused.sqlite"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["attribution"]["state"] == "available"


@pytest.mark.parametrize("contents", [b'\xff', b'[' * 2000 + b']' * 2000,
                                     b'{"n":' + b'9' * 5000 + b'}'],
                         ids=["invalid-utf8", "deep-nesting", "huge-integer"])
def test_lead_malformed_input_has_safe_cli_error(tmp_path, contents):
    source = tmp_path / "status.json"
    source.write_bytes(contents)
    result = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--json"],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_lead_schema_error_does_not_echo_value():
    with pytest.raises(InputError) as error:
        load_attestation({"schema": "SYNTHETIC_SECRET"})
    assert "SYNTHETIC_SECRET" not in str(error.value)
