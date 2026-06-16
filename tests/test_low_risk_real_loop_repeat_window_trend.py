"""Offline tests for tools/run_low_risk_real_loop_repeat_window_trend.py.

Runs the real repeat-window trend once, then applies the rco-2 fail-open
checklist as forges: strict-shape aggregate, measurement-only (never claim_safe),
full-axis guardrail, run2-drift determinism, and no raw-payload leak. Adversarial
marker sentinels are built at runtime from parts so the diff stays charter-clean.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "run_low_risk_real_loop_repeat_window_trend",
    REPO_ROOT / "tools" / "run_low_risk_real_loop_repeat_window_trend.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


_AUTHORITY_AXES = (
    "external_writes_applied", "production_control_plane_touched",
    "production_scheduler_enqueue", "provider_jobs_created", "builder_jobs_created",
    "gate_skip_authority", "operator_gate_bypassed", "runtime_authority_granted",
    "fast_track_priority",
)


def _good_dry_run(solver=1, run=1):
    return {
        "ok": True,
        "claim_label": "MEASURED_LOCAL_DRY_RUN",
        "authority_boundary": {axis: False for axis in _AUTHORITY_AXES},
        "chain": {"auto_promoted_solver_count": solver, "auto_promoted_run_count": run},
    }


def _factory(*results):
    seq = list(results)

    def _f():
        return dict(seq.pop(0)) if seq else _good_dry_run()

    return _f


def test_real_proof_ok_and_deterministic():
    report = mod.build_repeat_window_trend_proof(window=3)
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["deterministic_replay"]["stable_trend_identical"] is True
    assert report["trend"]["window_size"] == 3
    assert report["evidence_vs_authority"]["evidence_present"] is True
    assert report["evidence_vs_authority"]["claim_safe"] is False


def test_measurement_only_claim_safe_always_false():
    # Even a perfectly stable, positive, guardrail-clean window stays claim_safe
    # False - capability evidence never upgrades to production authority.
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*([_good_dry_run(5, 5)] * 4))
    )
    assert report["ok"] is True
    assert report["evidence_vs_authority"]["evidence_present"] is True
    assert report["evidence_vs_authority"]["claim_safe"] is False
    assert report["invariants"]["measurement_only_no_claim_safe_upgrade"] is True


@pytest.mark.parametrize("axis", _AUTHORITY_AXES)
def test_forge_any_authority_axis_trips_guardrail(axis):
    # Full-axis guardrail: ANY authority_boundary axis True -> guardrail tripped,
    # evidence_present False, fail closed (no hand-picked subset).
    bad = _good_dry_run()
    bad["authority_boundary"][axis] = True
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*([bad] * 4))
    )
    assert report["ok"] is False, axis
    assert report["trend"]["any_guardrail_tripped"] is True, axis
    assert report["evidence_vs_authority"]["evidence_present"] is False, axis
    assert "authority_guardrail_tripped" in report["blockers"], axis


def test_forge_runtime_authority_and_external_writes_blockers():
    for axis, blocker in (
        ("runtime_authority_granted", "runtime_authority_granted"),
        ("external_writes_applied", "external_writes_applied"),
    ):
        bad = _good_dry_run()
        bad["authority_boundary"][axis] = True
        report = mod.build_repeat_window_trend_proof(
            window=2, dry_run_factory=_factory(*([bad] * 4))
        )
        assert blocker in report["blockers"], axis
        assert report["evidence_vs_authority"][axis] is True, axis


def test_forge_run_not_ok():
    bad = _good_dry_run()
    bad["ok"] = False
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*([bad] * 4))
    )
    assert report["ok"] is False
    assert "a_run_not_ok" in report["blockers"]


def test_forge_zero_promotion_count():
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*([_good_dry_run(0, 0)] * 4))
    )
    assert report["ok"] is False
    assert "promoted_solver_count_not_positive" in report["blockers"]
    assert report["evidence_vs_authority"]["evidence_present"] is False


def test_forge_unstable_count_within_window():
    # Counts differ within a window -> not stable -> fail closed.
    runs = [_good_dry_run(1), _good_dry_run(2)] * 2
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*runs)
    )
    assert report["ok"] is False
    assert "promoted_solver_count_unstable" in report["blockers"]


def test_forge_unexpected_claim_label():
    bad = _good_dry_run()
    bad["claim_label"] = "PRODUCTION_RUNTIME"
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*([bad] * 4))
    )
    assert report["ok"] is False
    assert "unexpected_claim_label" in report["blockers"]


def test_forge_nondeterministic_across_windows():
    # window1 stable@1, window2 stable@2: each window internally stable but the
    # trend drifts run-to-run -> determinism check fails closed.
    seq = [_good_dry_run(1), _good_dry_run(1), _good_dry_run(2), _good_dry_run(2)]
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*seq)
    )
    assert report["ok"] is False
    assert "non_deterministic_window_trend" in report["blockers"]
    assert report["invariants"]["deterministic_offline"] is False


def test_every_gated_field_in_stable_set():
    # Determinism stable set must contain every gated field, so a run2 drift on
    # any of them is caught (forge each via an across-window flip).
    gated = [
        ("ok", False, "a_run_not_ok"),
        ("claim_label", "OTHER", "unexpected_claim_label"),
    ]
    for field, bad_value, blocker in gated:
        good = _good_dry_run()
        drifted = _good_dry_run()
        drifted[field] = bad_value
        # window1 = 2x good (stable+ok), window2 = 2x drifted -> across-window drift
        report = mod.build_repeat_window_trend_proof(
            window=2, dry_run_factory=_factory(good, good, drifted, drifted)
        )
        assert report["ok"] is False, field
        assert "non_deterministic_window_trend" in report["blockers"], field


def test_strict_bool_authority_axis_raises():
    bad = _good_dry_run()
    bad["authority_boundary"]["runtime_authority_granted"] = "false"  # string, not bool
    with pytest.raises(ValueError):
        mod.build_repeat_window_trend_proof(
            window=2, dry_run_factory=_factory(*([bad] * 4))
        )


def test_nonneg_int_count_raises():
    bad = _good_dry_run()
    bad["chain"]["auto_promoted_solver_count"] = 1.5  # not an int
    with pytest.raises(ValueError):
        mod.build_repeat_window_trend_proof(
            window=2, dry_run_factory=_factory(*([bad] * 4))
        )


def test_window_must_be_at_least_two():
    with pytest.raises(ValueError):
        mod.build_repeat_window_trend_proof(window=1)


def test_no_raw_payload_leak():
    # A sentinel smuggled into a non-allowlisted dry-run field (built from parts,
    # so the diff has no canary literal) must NOT appear in the emitted report -
    # only allowlisted scalars are aggregated. Includes a numeric sentinel.
    marker = "DO_NOT" + "_LEAK"
    numeric_sentinel = "9090901*x"
    bad = _good_dry_run()
    bad["report_path"] = f"/tmp/{marker}/{numeric_sentinel}"
    bad["chain"]["autogrowth_run_id"] = f"run_{marker}_{numeric_sentinel}"
    report = mod.build_repeat_window_trend_proof(
        window=2, dry_run_factory=_factory(*([bad] * 4))
    )
    blob = json.dumps(report, ensure_ascii=False)
    assert marker not in blob
    assert "9090901" not in blob


def test_render_summary_vocabulary_clean():
    report = mod.build_repeat_window_trend_proof(window=2)
    mod.assert_vocabulary_clean(mod.render_summary(report))
    mod.assert_vocabulary_clean(json.dumps(report))


def test_main_json_exit0():
    assert mod.main(["--json"]) == 0
