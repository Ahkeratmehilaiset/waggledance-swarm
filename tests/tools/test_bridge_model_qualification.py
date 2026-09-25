# SPDX-License-Identifier: BUSL-1.1
"""Regression tests for the offline model-qualification harness.

Each test pins one refusal the harness must keep making. The point of the tool
is what it declines to compute, so the negative cases are the important ones.

The ``test_rev2_*`` tests reproduce the four blockers the Lead found in the
first revision, plus the hardening it asked for. Each fails against rev 1.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest

from tools.bridge_model_qualification import (
    INPUT_SCHEMA,
    REPORT_SCHEMA,
    VERDICT_ELIGIBLE,
    VERDICT_INSUFFICIENT,
    VERDICT_UNKNOWN_PROFILE,
    InputError,
    aggregate,
    build_report,
    finite_non_negative,
    load_document,
    strict_loads,
    validate_observation,
)

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_model_qualification.py"

_COUNTER = {"n": 0}


def test_lead_zero_opportunities_rejects_false_alarms():
    assert validate_observation(_obs(false_alarm_opportunities=0), 0) is not None


def test_lead_finite_inputs_cannot_overflow_report():
    rows = [_obs(latency_ms={'model': 1e308}, cost={
        'monetary': {'amount': 1e308, 'currency': 'USD'},
        'denominator': 'd'}) for _ in range(3)]
    with pytest.raises(InputError):
        build_report(_doc(rows))


def test_lead_quota_identifiers_cannot_alias():
    rows = [_obs(cost={'quota': {'amount': 1, 'pool': p, 'unit': u},
                      'denominator': 'd'}) for p, u in [('a/b', 'c'), ('a', 'b/c')]]
    cost = aggregate(rows)['strata'][0]['cost']['quota_by_pool_unit']
    assert len(cost) == 2


@pytest.mark.parametrize('threshold', [0, -1, True, 1.5])
def test_lead_invalid_threshold_rejected(threshold):
    with pytest.raises(InputError):
        aggregate([_obs()], min_observations=threshold)


def test_lead_exponent_overflow_is_not_strict_json():
    with pytest.raises(InputError):
        strict_loads('{"x": 1e999}')


def _obs(**over):
    """A valid observation. Each call gets fresh ids unless they are pinned."""
    _COUNTER["n"] += 1
    n = _COUNTER["n"]
    base = {
        "observation_id": f"obs-{n}",
        "evidence_id": f"ev-{n}",
        "observed_model": "claude-opus-5",
        "observed_effort": "high",
        "risk_stratum": "critical",
        "model_provenance": "transcript",
        "evidence_source": "seeded_defect",
        "independent_oracle": {"present": True, "oracle_id": "corpus-v1"},
        "author_reviewer": {"author": "lane-a", "reviewer": "lane-b"},
        "defects_total": 10,
        "critical_misses": 1,
        "false_alarms": 2,
        "false_alarm_opportunities": 40,
        "latency_ms": {"queue": 100, "model": 2000, "tool": 30000},
        "cost": None,
    }
    base.update(over)
    return base


def _doc(observations):
    return {"schema": INPUT_SCHEMA, "observations": observations}


def _reasons(obs):
    rejection = validate_observation(obs, 0)
    return set(rejection.reasons) if rejection else set()


# --- acceptance ------------------------------------------------------------


def test_valid_observation_is_accepted():
    assert validate_observation(_obs(), 0) is None


def test_strata_are_keyed_by_exact_model_effort_and_stratum():
    rows = aggregate(
        [
            _obs(),
            _obs(observed_effort="low"),
            _obs(observed_model="other-model"),
            _obs(risk_stratum="routine"),
        ]
    )["strata"]
    keys = {(r["observed_model"], r["observed_effort"], r["risk_stratum"]) for r in rows}
    assert len(keys) == 4, "differing model/effort/stratum must not be merged"


# --- rev-2 blocker 1: negative numerics ------------------------------------


def test_rev2_negative_latency_is_rejected():
    assert "invalid_latency_queue" in _reasons(
        _obs(latency_ms={"queue": -2, "model": 2, "tool": 3})
    )


def test_rev2_negative_cost_is_not_accepted():
    rows = aggregate(
        [
            _obs(cost={"monetary": {"amount": -3, "currency": "USD"},
                       "quota": {"amount": -4, "pool": "p", "unit": "tok"},
                       "denominator": "d1"})
            for _ in range(3)
        ]
    )["strata"]
    cost = rows[0]["cost"]
    assert cost["monetary_by_currency"] is None
    assert cost["quota_by_pool_unit"] is None
    assert "monetary_dropped_invalid_amount" in cost["notes"]
    assert "quota_dropped_invalid_amount" in cost["notes"]


def test_rev2_finite_non_negative_helper():
    assert finite_non_negative(-1) is None
    assert finite_non_negative(float("nan")) is None
    assert finite_non_negative(float("inf")) is None
    assert finite_non_negative(True) is None
    assert finite_non_negative(0) == 0.0
    assert finite_non_negative(2.5) == 2.5


# --- rev-2 blocker 2: duplicated evidence must not confer eligibility ------


def test_rev2_repeated_evidence_does_not_become_eligible():
    rows = aggregate([_obs(observation_id=f"o{i}", evidence_id="same-evidence") for i in range(3)])[
        "strata"
    ]
    assert rows[0]["distinct_evidence"] == 1
    assert rows[0]["qualification"]["verdict"] == VERDICT_INSUFFICIENT


def test_rev2_duplicate_observation_id_is_rejected():
    result = aggregate([_obs(observation_id="dup"), _obs(observation_id="dup")])
    assert result["rejected"] == [{"index": 1, "reasons": ["duplicate_observation_id"]}]
    assert result["strata"][0]["accepted_observations"] == 1


def test_rev2_missing_identity_is_rejected():
    assert "missing_observation_id" in _reasons(_obs(observation_id=""))
    assert "missing_evidence_id" in _reasons(_obs(evidence_id=None))


def test_rev2_unknown_effort_or_model_is_preserved_but_never_eligible():
    for over in ({"observed_effort": "unknown"}, {"observed_model": "unknown"}):
        rows = aggregate([_obs(**over) for _ in range(5)])["strata"]
        assert rows[0]["qualification"]["verdict"] == VERDICT_UNKNOWN_PROFILE
        assert "unknown_model_or_effort_cannot_be_qualified" in rows[0]["qualification"]["reasons"]
        # Preserved, not discarded.
        assert rows[0]["quality"]["defects_total"] == 50


# --- rev-2 blocker 3: incompatible cost units ------------------------------


def test_rev2_currencies_and_pools_are_never_summed_together():
    rows = aggregate(
        [
            _obs(cost={"monetary": {"amount": 1, "currency": "USD"},
                       "quota": {"amount": 10, "pool": "pool-a", "unit": "tok"},
                       "denominator": "acct-1"}),
            _obs(cost={"monetary": {"amount": 2, "currency": "EUR"},
                       "quota": {"amount": 20, "pool": "pool-b", "unit": "tok"},
                       "denominator": "acct-2"}),
            _obs(cost={"monetary": {"amount": 4, "currency": "USD"},
                       "quota": {"amount": 40, "pool": "pool-a", "unit": "tok"},
                       "denominator": "acct-1"}),
        ]
    )["strata"]
    cost = rows[0]["cost"]
    assert cost["monetary_by_currency"] == {"EUR": 2.0, "USD": 5.0}
    assert cost["quota_by_pool_unit"] == {'["pool-a","tok"]': 50.0, '["pool-b","tok"]': 20.0}
    assert 7.0 not in cost["monetary_by_currency"].values(), "USD and EUR must not be added"


def test_rev2_monetary_without_currency_is_dropped():
    rows = aggregate([_obs(cost={"monetary": {"amount": 5}, "denominator": "acct-1"}) for _ in range(3)])["strata"]
    assert rows[0]["cost"]["monetary_by_currency"] is None
    assert "monetary_dropped_missing_currency" in rows[0]["cost"]["notes"]


def test_rev2_quota_without_pool_or_unit_is_dropped():
    rows = aggregate(
        [_obs(cost={"quota": {"amount": 5, "unit": "tok"}, "denominator": "acct-1"}) for _ in range(3)]
    )["strata"]
    assert rows[0]["cost"]["quota_by_pool_unit"] is None
    assert "quota_dropped_missing_pool" in rows[0]["cost"]["notes"]


# --- rev-2 blocker 4: overflow --------------------------------------------


def test_rev2_huge_int_latency_does_not_raise_and_is_rejected():
    # float(10**400) raises OverflowError; rev 1 crashed here.
    assert "invalid_latency_queue" in _reasons(
        _obs(latency_ms={"queue": 10**400, "model": 2, "tool": 3})
    )


def test_rev2_huge_int_cost_does_not_raise():
    rows = aggregate(
        [_obs(cost={"monetary": {"amount": 10**400, "currency": "USD"}, "denominator": "d"}) for _ in range(3)]
    )["strata"]
    assert rows[0]["cost"]["monetary_by_currency"] is None


# --- rev-2: false-alarm denominator ---------------------------------------


def test_rev2_false_alarm_rate_requires_explicit_opportunities():
    rows = aggregate([_obs(false_alarm_opportunities=None) for _ in range(3)])["strata"]
    quality = rows[0]["quality"]
    assert quality["false_alarm_rate"] is None
    assert quality["false_alarm_rate_note"] == "false_alarm_rate_requires_explicit_opportunities"
    # The misleading rev-1 value was false_alarms / defects_total.
    assert quality["false_alarm_rate"] != 0.2


def test_rev2_false_alarm_rate_uses_supplied_opportunities():
    rows = aggregate([_obs() for _ in range(3)])["strata"]
    quality = rows[0]["quality"]
    assert quality["false_alarm_opportunities"] == 120
    assert quality["false_alarm_rate"] == round(6 / 120, 4)


def test_rev2_false_alarms_cannot_exceed_opportunities():
    assert "false_alarms_exceed_opportunities" in _reasons(
        _obs(false_alarms=5, false_alarm_opportunities=4)
    )


# --- rev-2: strict JSON ----------------------------------------------------


def test_rev2_strict_json_rejects_duplicate_keys():
    try:
        strict_loads('{"a": 1, "a": 2}')
    except InputError as exc:
        assert "duplicate JSON key" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected InputError for duplicate keys")


def test_rev2_strict_json_rejects_nonfinite_constants():
    for text in ('{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}'):
        try:
            strict_loads(text)
        except InputError as exc:
            assert "non-finite JSON constant" in str(exc)
        else:  # pragma: no cover - guard
            raise AssertionError(f"expected InputError for {text}")


def test_rev2_bounded_read_enforced(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(_doc([_obs() for _ in range(5)])), encoding="utf-8")
    try:
        load_document(path, max_bytes=10)
    except InputError as exc:
        assert "exceeds the 10-byte bound" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected InputError")


# --- original refusals, still enforced -------------------------------------


def test_chat_or_advisory_evidence_cannot_qualify():
    for source in ("chat", "advisory", "Brainstorm", "discussion"):
        assert "chat_or_advisory_evidence_cannot_qualify" in _reasons(
            _obs(evidence_source=source)
        )


def test_unknown_model_provenance_is_rejected():
    assert "unknown_model_provenance" in _reasons(_obs(model_provenance="operator_said_so"))


def test_independent_oracle_is_required():
    assert "independent_oracle_required" in _reasons(_obs(independent_oracle=None))
    assert "independent_oracle_id_required" in _reasons(
        _obs(independent_oracle={"present": True, "oracle_id": "  "})
    )


def test_author_must_not_be_reviewer():
    assert "author_must_not_be_reviewer" in _reasons(
        _obs(author_reviewer={"author": "same", "reviewer": "same"})
    )


def test_invalid_counts_are_rejected():
    assert "invalid_defects_total" in _reasons(_obs(defects_total=float("nan")))
    assert "invalid_false_alarms" in _reasons(_obs(false_alarms=-1))
    assert "invalid_critical_misses" in _reasons(_obs(critical_misses=True))
    assert "invalid_defects_total" in _reasons(_obs(defects_total=10.5))
    assert "critical_misses_exceed_defects_total" in _reasons(
        _obs(defects_total=2, critical_misses=3)
    )


def test_unknown_cost_is_null_never_zero():
    cost = aggregate([_obs() for _ in range(3)])["strata"][0]["cost"]
    assert cost["monetary_by_currency"] is None
    assert cost["quota_by_pool_unit"] is None
    assert "cost_unknown" in cost["notes"]


def test_cost_claim_without_denominator_is_dropped_but_quality_is_kept():
    rows = aggregate([_obs(cost={"monetary": {"amount": 12.5, "currency": "USD"}}) for _ in range(3)])["strata"]
    assert rows[0]["cost"]["monetary_by_currency"] is None
    assert "cost_claim_dropped_missing_denominator" in rows[0]["cost"]["notes"]
    assert rows[0]["quality"]["critical_miss_rate"] == 0.1


def test_pooled_critical_miss_average_is_never_reported():
    result = aggregate([_obs(), _obs(risk_stratum="routine"), _obs(risk_stratum="bookkeeping")])
    assert result["pooled_critical_miss_rate"] is None
    assert build_report(_doc([_obs()]))["pooled_critical_miss_rate"] is None


def test_quality_and_latency_are_reported_separately():
    row = aggregate([_obs() for _ in range(3)])["strata"][0]
    assert set(row["latency_ms"]) == {"queue_mean", "model_mean", "tool_mean"}
    assert not set(row["quality"]) & set(row["latency_ms"])


def test_no_automatic_approval_and_no_qualified_verdict():
    report = build_report(_doc([_obs() for _ in range(5)]))
    assert report["approval"]["automatic"] is False
    verdicts = {r["qualification"]["verdict"] for r in report["strata"]}
    assert verdicts <= {VERDICT_ELIGIBLE, VERDICT_INSUFFICIENT, VERDICT_UNKNOWN_PROFILE}
    for bad in ("approved", "qualified\"", "auto_approved"):
        assert bad not in json.dumps(report["strata"])


def test_eligible_verdict_still_demands_review():
    row = aggregate([_obs() for _ in range(3)])["strata"][0]
    assert row["qualification"]["verdict"] == VERDICT_ELIGIBLE
    assert "operator_and_gate_review_still_required" in row["qualification"]["reasons"]


def test_rejected_observations_are_reported_not_silently_dropped():
    result = aggregate([_obs(), _obs(evidence_source="chat")])
    assert result["rejected"] == [
        {"index": 1, "reasons": ["chat_or_advisory_evidence_cannot_qualify"]}
    ]


def test_unsupported_schema_and_malformed_input_are_rejected(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"schema": "other.v9", "observations": []}), encoding="utf-8")
    try:
        load_document(path)
    except InputError as exc:
        assert "unsupported input schema" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected InputError")

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    try:
        load_document(bad)
    except InputError as exc:
        assert "not valid JSON" in str(exc)
    else:  # pragma: no cover - guard
        raise AssertionError("expected InputError")


# --- CLI -------------------------------------------------------------------


def test_cli_json_output(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(json.dumps(_doc([_obs() for _ in range(3)])), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(path), "--json", "--base", "abc123"],
        capture_output=True, text=True, cwd=str(ROOT), check=True,
    )
    report = json.loads(proc.stdout)
    assert report["schema"] == REPORT_SCHEMA
    assert report["base"] == "abc123"
    assert report["pooled_critical_miss_rate"] is None
    assert report["approval"]["automatic"] is False


def test_cli_rejects_bad_input_with_exit_code_two(tmp_path):
    path = tmp_path / "input.json"
    path.write_text('{"schema": "wd.model-qualification-input.v1", "observations": [], "observations": []}', encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(path), "--json"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 2
    assert "duplicate JSON key" in proc.stderr
    assert "observations" not in proc.stderr


def test_duplicate_key_cli_does_not_disclose_input(tmp_path):
    marker = "SYNTHETIC_SENSITIVE_FIELD"
    source = tmp_path / "duplicate.json"
    source.write_text('{"' + marker + '":1,"' + marker + '":2}', encoding="utf-8")
    proc = subprocess.run([sys.executable, str(SCRIPT), "--input", str(source), "--json"],
                          capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 2
    assert "duplicate JSON key" in proc.stderr
    assert marker not in proc.stderr
