from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from tools.operator_path_exception import PATH_REASON, apply_operator_path_exception
from waggledance.core.magma.canonical import sha256_digest

NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)


def inputs():
    grant = dict(schema="wd.operator-path-exception.v1", repo="a/b", pr_number=1,
                 head="a" * 40, base="b" * 40, diff_digest=sha256_digest("diff"),
                 paths=["docs/offlist.md"], approval_reference="operator instruction",
                 issued_at=NOW.isoformat(), expires_at=(NOW + timedelta(hours=1)).isoformat())
    gate = dict(ok=False, decision="operator_review_required", reasons=[PATH_REASON],
                path_gate=dict(allowed=False, reason="paths not on allowlist",
                               blocked_paths=[], code_pattern_hits=[], unmatched_paths=grant["paths"]),
                bridge_consensus=dict(ok=True), rco_pass_gate=dict(ok=True),
                bridge_peer_gate=dict(clear_to_merge=True),
                accepted_queue_preflight=dict(complete=True), diff_gate=dict(allowed=True),
                base_gate=dict(allowed=True), rate_gate=dict(allowed=True))
    return gate, grant


def call(gate, grant, now=NOW):
    return apply_operator_path_exception(gate, grant=grant,
        pr_status=dict(pr_number=1, diff_text="diff"), repo="a/b",
        head="a" * 40, base="b" * 40, now=now)


def test_original_gate_is_preserved_and_default_remains_closed():
    gate, grant = inputs()
    original = deepcopy(gate)
    assert call(gate, None) == original
    result = call(gate, grant)
    assert gate == original
    assert result["original_gate"] == original
    assert result["path_gate"]["allowed"] is False
    assert result["ok"] is True
    assert result["operator_path_exception"]["cryptographic_authentication"] is False
    grant["paths"].append("other")
    assert result["operator_path_exception"]["grant"]["paths"] == ["docs/offlist.md"]


@pytest.mark.parametrize("field,value", [
    ("schema", "other"), ("repo", "a/c"), ("pr_number", True), ("pr_number", 2),
    ("head", "c" * 40), ("base", "c" * 40), ("diff_digest", "forged"),
    ("paths", []), ("paths", ["*"]), ("paths", ["docs/offlist.md"] * 2),
    ("paths", "docs/offlist.md"), ("approval_reference", " "),
    ("expires_at", NOW.isoformat()), ("expires_at", (NOW + timedelta(days=2)).isoformat()),
    ("issued_at", (NOW + timedelta(minutes=1)).isoformat()),
    ("issued_at", "2026-09-25T00:00:00"), ("issued_at", None),
])
def test_invalid_grants_fail_closed(field, value):
    gate, grant = inputs()
    grant[field] = value
    with pytest.raises(ValueError):
        call(gate, grant)


@pytest.mark.parametrize("reason", ["status checks not green", "missing exact-head RCO_PASS",
    "unresolved peer bridge block", "accepted queue incomplete", "daily rate limit exceeded",
    "diff gate failed", "base changed", "exact head mismatch"])
def test_any_additional_gate_failure_blocks_exception(reason):
    gate, grant = inputs()
    gate["reasons"].append(reason)
    with pytest.raises(ValueError):
        call(gate, grant)


@pytest.mark.parametrize("name,flag", [("bridge_consensus", "ok"), ("rco_pass_gate", "ok"),
    ("bridge_peer_gate", "clear_to_merge"), ("accepted_queue_preflight", "complete"),
    ("diff_gate", "allowed"), ("base_gate", "allowed"), ("rate_gate", "allowed")])
def test_missing_verified_subgate_blocks_even_if_reason_list_lies(name, flag):
    gate, grant = inputs()
    gate[name][flag] = False
    with pytest.raises(ValueError):
        call(gate, grant)


def test_denied_paths_and_code_patterns_are_not_exempted():
    for field in ("blocked_paths", "code_pattern_hits"):
        gate, grant = inputs()
        gate["path_gate"][field] = ["denied"]
        with pytest.raises(ValueError):
            call(gate, grant)


def test_unknown_fields_and_expiry_at_execution_are_rejected():
    gate, grant = inputs()
    with pytest.raises(ValueError):
        call(gate, dict(grant, waive_ci=True))
    with pytest.raises(ValueError):
        call(gate, grant, NOW + timedelta(hours=1))
