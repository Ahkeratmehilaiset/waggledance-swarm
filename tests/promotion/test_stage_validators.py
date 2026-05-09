# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.promotion.stage_validators.

Iteration N+2 scout pick: stage_validators is the autonomy-loop
promotion gate. Each named validator is a single-criterion check
that the ladder runs before allowing a stage transition. Silent
inversion or bypass of any of these validators would let an
unqualified solver climb past a safety gate (curiosity → tension →
... → limited_runtime → runtime). The 2026-05-09 PR #116 validators
property-gate incident showed how easily these gates can drift
from their docstring contract; pinning them with direct tests is
the cheapest defense.

Pinned invariants:

- `register` adds the validator to the module-level registry; only
  the registered name resolves via `run_criterion`.
- `run_criterion` returns (False, "unknown criterion: <name>") for
  unregistered names — never raises.
- `run_criterion` swallows raised exceptions and returns
  (False, "validator <name> raised: <exc>") so a buggy validator
  cannot crash the ladder.
- `run_all` preserves input order in both the satisfied and failed
  output lists; never reorders, dedups, or drops.
- Each concrete validator: pass case + fail case; default-False
  semantics for boolean validators (missing key == fail).
- Security-critical validators (`human_approval_id_present`,
  `no_critical_regressions`, `passes_proposal_gate`,
  `replay_methodology_acknowledged`) carry pinned error messages.
"""
from __future__ import annotations

import pytest

from waggledance.core.promotion import stage_validators
from waggledance.core.promotion.stage_validators import (
    register,
    run_all,
    run_criterion,
)


# --- run_criterion: unknown name and exception swallowing ---------

def test_run_criterion_unknown_name_returns_false_with_pinned_message():
    ok, msg = run_criterion("not_a_real_criterion", {})
    assert ok is False
    assert "unknown criterion" in msg
    assert "'not_a_real_criterion'" in msg


def test_run_criterion_swallows_validator_exceptions():
    """A validator that raises must NOT propagate. The ladder treats
    it as a failed criterion and reports the raised exception in the
    diagnostic message — otherwise a buggy validator could crash the
    whole promotion attempt."""
    @register("crashing_validator_for_test")
    def _crash(ctx: dict):
        raise RuntimeError("boom")

    try:
        ok, msg = run_criterion("crashing_validator_for_test", {})
        assert ok is False
        assert "raised" in msg
        assert "boom" in msg
    finally:
        # cleanup: remove the test-only validator from the module registry
        stage_validators._VALIDATORS.pop("crashing_validator_for_test",
                                            None)


def test_register_adds_validator_to_registry_under_given_name():
    @register("a_test_only_validator_2026_05_09")
    def _v(ctx: dict):
        return True, ""

    try:
        assert "a_test_only_validator_2026_05_09" in (
            stage_validators._VALIDATORS
        )
        ok, _ = run_criterion("a_test_only_validator_2026_05_09", {})
        assert ok is True
    finally:
        stage_validators._VALIDATORS.pop(
            "a_test_only_validator_2026_05_09", None,
        )


# --- run_all: order-preserving split ------------------------------

def test_run_all_preserves_input_order_in_both_outputs():
    """If the ladder asks for criteria in a specific order, run_all
    must return satisfied and failed in that same order — promotion
    rationale strings depend on stable ordering."""
    ctx = {
        "from_stage": "tension",
        "tension_resolution_path": "deferred_to_dream",
    }
    criteria = (
        "from_stage_is_tension",                         # pass
        "from_stage_is_curiosity",                       # fail
        "tension_resolution_path_deferred_to_dream",     # pass
        "passes_proposal_gate",                          # fail (no verdict in ctx)
    )
    satisfied, failed = run_all(criteria, ctx)
    assert satisfied == [
        "from_stage_is_tension",
        "tension_resolution_path_deferred_to_dream",
    ]
    assert failed == [
        "from_stage_is_curiosity",
        "passes_proposal_gate",
    ]


def test_run_all_unknown_criterion_lands_in_failed_list():
    """An unknown criterion must NEVER count as satisfied — silent
    promotion past a typo'd criterion would be a safety gate breach."""
    ctx = {"from_stage": "tension"}
    satisfied, failed = run_all(
        ("from_stage_is_tension", "not_a_real_criterion"), ctx,
    )
    assert satisfied == ["from_stage_is_tension"]
    assert failed == ["not_a_real_criterion"]


def test_run_all_empty_criteria_returns_two_empty_lists():
    satisfied, failed = run_all((), {"from_stage": "tension"})
    assert satisfied == []
    assert failed == []


# --- from_stage_* validators (parametrized) -----------------------

@pytest.mark.parametrize("criterion,from_stage_value", [
    ("from_stage_is_curiosity",                          "curiosity"),
    ("from_stage_is_tension",                            "tension"),
    ("from_stage_is_dream_target",                       "dream_target"),
    ("from_stage_is_stochastic",                         "stochastic_external_proposal"),
    ("from_stage_is_deterministic_collapse",             "deterministic_collapse"),
    ("from_stage_is_shadow_graph",                       "shadow_graph"),
    ("from_stage_is_replay",                             "replay"),
    ("from_stage_is_meta_proposal",                      "meta_proposal"),
    ("from_stage_is_human_review",                       "human_review"),
    ("from_stage_is_post_campaign_runtime_candidate",    "post_campaign_runtime_candidate"),
    ("from_stage_is_canary_cell",                        "canary_cell"),
    ("from_stage_is_limited_runtime",                    "limited_runtime"),
])
def test_from_stage_validators_pass_only_on_exact_match(
    criterion, from_stage_value,
):
    ok, _ = run_criterion(criterion, {"from_stage": from_stage_value})
    assert ok is True


@pytest.mark.parametrize("criterion", [
    "from_stage_is_curiosity",
    "from_stage_is_tension",
    "from_stage_is_dream_target",
    "from_stage_is_stochastic",
    "from_stage_is_deterministic_collapse",
    "from_stage_is_shadow_graph",
    "from_stage_is_replay",
    "from_stage_is_meta_proposal",
    "from_stage_is_human_review",
    "from_stage_is_post_campaign_runtime_candidate",
    "from_stage_is_canary_cell",
    "from_stage_is_limited_runtime",
])
def test_from_stage_validators_fail_on_wrong_stage(criterion):
    ok, msg = run_criterion(criterion, {"from_stage": "_completely_different_"})
    assert ok is False
    assert "from_stage must be" in msg


@pytest.mark.parametrize("criterion", [
    "from_stage_is_curiosity",
    "from_stage_is_tension",
    "from_stage_is_dream_target",
])
def test_from_stage_validators_fail_when_key_missing(criterion):
    """No `from_stage` in context must FAIL — never silently pass."""
    ok, _ = run_criterion(criterion, {})
    assert ok is False


# --- tension_resolution_path_deferred_to_dream --------------------

def test_tension_resolution_path_deferred_to_dream_pass_and_fail():
    ok, _ = run_criterion(
        "tension_resolution_path_deferred_to_dream",
        {"tension_resolution_path": "deferred_to_dream"},
    )
    assert ok is True

    ok2, msg2 = run_criterion(
        "tension_resolution_path_deferred_to_dream",
        {"tension_resolution_path": "resolved_inline"},
    )
    assert ok2 is False
    assert "deferred_to_dream" in msg2


def test_tension_resolution_path_missing_key_is_failure():
    ok, _ = run_criterion(
        "tension_resolution_path_deferred_to_dream", {},
    )
    assert ok is False


# --- passes_proposal_gate (security-critical) ---------------------

def test_passes_proposal_gate_only_accepts_ACCEPT_CANDIDATE():
    """The collapse verdict gate is the entry to runtime promotion;
    only the literal 'ACCEPT_CANDIDATE' may pass. SOFT_REJECT,
    HARD_REJECT, missing key, anything else MUST fail."""
    ok, _ = run_criterion(
        "passes_proposal_gate",
        {"collapse_verdict": "ACCEPT_CANDIDATE"},
    )
    assert ok is True

    for bad in ["SOFT_REJECT", "HARD_REJECT", "REJECT", "accept_candidate",
                "ACCEPT", "", None]:
        ok2, msg2 = run_criterion(
            "passes_proposal_gate",
            {"collapse_verdict": bad},
        )
        assert ok2 is False, f"unexpected pass for verdict={bad!r}"
        assert "ACCEPT_CANDIDATE" in msg2


def test_passes_proposal_gate_missing_key_is_failure():
    ok, _ = run_criterion("passes_proposal_gate", {})
    assert ok is False


# --- shadow_only_admit, structurally_promising --------------------

def test_shadow_only_admit_default_false_on_missing_key():
    """Boolean validators must default to False when the key is
    missing — promotion gates never silently grant on absent
    evidence."""
    ok, _ = run_criterion("shadow_only_admit", {})
    assert ok is False


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False),
    (1, True), (0, False),
    ("non-empty-string", True), ("", False),
])
def test_shadow_only_admit_truthiness(value, expected):
    ok, _ = run_criterion("shadow_only_admit", {"shadow_only": value})
    assert ok is expected


def test_structurally_promising_default_false_on_missing_key():
    ok, _ = run_criterion("structurally_promising", {})
    assert ok is False


def test_structurally_promising_pass_on_truthy():
    ok, _ = run_criterion(
        "structurally_promising",
        {"structurally_promising": True},
    )
    assert ok is True


# --- replay_methodology_acknowledged ------------------------------

def test_replay_methodology_acknowledged_only_pinned_value_passes():
    """The replay methodology string is a versioned contract; only
    the pinned 'structural_proxy_v0.1' may pass. A bare 'true' or
    a different version string must fail."""
    ok, _ = run_criterion(
        "replay_methodology_acknowledged",
        {"replay_methodology": "structural_proxy_v0.1"},
    )
    assert ok is True

    for bad in ["structural_proxy_v0.2", "true", "yes", "", None]:
        ok2, msg2 = run_criterion(
            "replay_methodology_acknowledged",
            {"replay_methodology": bad},
        )
        assert ok2 is False, f"unexpected pass for value={bad!r}"
        assert "structural_proxy_v0.1" in msg2


# --- human_approval_id_present (security-critical) ---------------

def test_human_approval_id_present_requires_truthy_id():
    """The runtime promotion gate REQUIRES an explicit human approval
    ID — a missing or empty string must NEVER pass. This is the same
    constitutional guard as solver_quarantine.admit_to_approved.
    """
    ok, _ = run_criterion(
        "human_approval_id_present",
        {"human_approval_id": "approval-2026-05-09"},
    )
    assert ok is True


@pytest.mark.parametrize("falsy", [None, "", 0, False])
def test_human_approval_id_present_rejects_falsy(falsy):
    ok, msg = run_criterion(
        "human_approval_id_present", {"human_approval_id": falsy},
    )
    assert ok is False
    assert "human_approval_id" in msg


def test_human_approval_id_present_rejects_missing_key():
    ok, _ = run_criterion("human_approval_id_present", {})
    assert ok is False


# --- campaign_finished_or_frozen, canary/limited windows ---------

def test_campaign_finished_or_frozen_default_false():
    ok, _ = run_criterion("campaign_finished_or_frozen", {})
    assert ok is False


def test_campaign_finished_or_frozen_pass_on_true():
    ok, _ = run_criterion(
        "campaign_finished_or_frozen",
        {"campaign_finished_or_frozen": True},
    )
    assert ok is True


def test_canary_observation_window_passed_default_false():
    ok, _ = run_criterion("canary_observation_window_passed", {})
    assert ok is False


def test_canary_observation_window_passed_on_true():
    ok, _ = run_criterion(
        "canary_observation_window_passed",
        {"canary_observation_window_passed": True},
    )
    assert ok is True


def test_limited_runtime_observation_window_passed_default_false():
    ok, _ = run_criterion(
        "limited_runtime_observation_window_passed", {},
    )
    assert ok is False


def test_limited_runtime_observation_window_passed_on_true():
    ok, _ = run_criterion(
        "limited_runtime_observation_window_passed",
        {"limited_runtime_observation_window_passed": True},
    )
    assert ok is True


# --- no_critical_regressions (security-critical) -----------------

def test_no_critical_regressions_passes_on_zero_default():
    """Missing key implies zero (default) — the int() cast over a
    .get(..., 0) makes that explicit. Either way, zero must pass."""
    ok, _ = run_criterion("no_critical_regressions", {})
    assert ok is True


def test_no_critical_regressions_passes_on_explicit_zero():
    ok, _ = run_criterion(
        "no_critical_regressions", {"critical_regressions": 0},
    )
    assert ok is True


@pytest.mark.parametrize("count", [1, 2, 7, 100])
def test_no_critical_regressions_fails_on_any_positive_count(count):
    ok, msg = run_criterion(
        "no_critical_regressions", {"critical_regressions": count},
    )
    assert ok is False
    assert str(count) in msg
    assert "must be 0" in msg


def test_no_critical_regressions_string_int_cast():
    """The validator uses int(...), so a string-encoded count must
    also be evaluated correctly. Promotion-gate inputs sometimes
    come from JSON without explicit type coercion."""
    ok_zero, _ = run_criterion(
        "no_critical_regressions", {"critical_regressions": "0"},
    )
    ok_three, _ = run_criterion(
        "no_critical_regressions", {"critical_regressions": "3"},
    )
    assert ok_zero is True
    assert ok_three is False


# --- registry coverage smoke --------------------------------------

def test_registry_contains_all_documented_validators():
    """All 21 validators documented in the module body must register
    on import. If a future refactor accidentally removes one, this
    test fails loudly instead of letting the ladder silently start
    accepting unqualified solvers."""
    expected = {
        "from_stage_is_curiosity",
        "from_stage_is_tension",
        "from_stage_is_dream_target",
        "from_stage_is_stochastic",
        "from_stage_is_deterministic_collapse",
        "from_stage_is_shadow_graph",
        "from_stage_is_replay",
        "from_stage_is_meta_proposal",
        "from_stage_is_human_review",
        "from_stage_is_post_campaign_runtime_candidate",
        "from_stage_is_canary_cell",
        "from_stage_is_limited_runtime",
        "tension_resolution_path_deferred_to_dream",
        "passes_proposal_gate",
        "shadow_only_admit",
        "replay_methodology_acknowledged",
        "structurally_promising",
        "human_approval_id_present",
        "campaign_finished_or_frozen",
        "canary_observation_window_passed",
        "limited_runtime_observation_window_passed",
        "no_critical_regressions",
    }
    actual = set(stage_validators._VALIDATORS.keys())
    missing = expected - actual
    assert not missing, f"missing registered validators: {sorted(missing)}"
