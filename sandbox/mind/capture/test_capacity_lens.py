"""Offline cases for the capacity lens: synthetic observer status only, no observer, no process."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_lens as lens  # noqa: E402

NOW = "2026-10-13T11:00:00+00:00"


def status(observations=(), activity=(), collection=None):
    return {"schema": lens.STATUS_SCHEMA, "execution_allowed": False, "observed_at": NOW,
            "observations": list(observations), "native_activity": list(activity), "collection": collection or {}}


def observation(thread, *windows):
    return {"provider": "claude", "native_thread_id": thread, "observation_age_seconds": 1.0,
            "quota_windows": list(windows)}


def render(value, declared=None):
    return lens.text_lines(*lens.project(value, declared or {}))


def test_windows_list_each_thread_never_sum_and_flag_risk_at_80():
    reset = 1792000800  # 2026-10-14T18:00:00Z
    lines = render(status([observation("9f375967-aaaa", {"name": "seven_day", "used_percent": 80, "resets_at": reset}),
                           observation("8abcb653-bbbb", {"name": "seven_day", "used_percent": 79, "resets_at": reset})]),
                   {"9f375967": "fable-5"})
    row = [line for line in lines if line.startswith("LIMITS claude seven_day")]
    assert row == ["LIMITS claude seven_day: RISK 9f375967 (fable-5, declared) 80%, 8abcb653 79%; "
                   "resets 2026-10-14T18:00Z (in 1860 min)"]


def test_a_window_without_an_observed_reset_says_unknown_and_no_risk_below_80():
    lines = render(status([observation("9f375967-aaaa", {"name": "five_hour", "used_percent": 7})]))
    assert "LIMITS claude five_hour: 9f375967 7%; reset UNKNOWN (not observed)" in lines


def test_a_provider_never_collected_is_unknown_with_the_observer_state():
    lines = render(status(collection={"codex": {"last_success": None, "collection_state": "pending_or_interrupted",
                                                "last_attempt": "2026-09-29T20:24:17+00:00"}}))
    assert ("LIMITS codex: UNKNOWN (observer has no successful collection; state pending_or_interrupted, "
            "last attempt 2026-09-29T20:24)") in lines


def test_old_idle_threads_fold_but_errors_and_current_work_stay():
    def act(thread, state, last, error=None):
        return {"provider": "claude", "native_thread_id": thread, "activity_state": state,
                "availability_state": "x", "last_successful_turn_at": last, "error_type": error,
                "first_error_at": "2026-10-10T08:00:00+00:00" if error else None}
    lines = render(status(activity=[act("11111111-x", "idle_observed", "2026-10-11T10:00:00+00:00"),
                                    act("22222222-x", "idle_observed", "2026-10-10T10:00:00+00:00", "rate_limited"),
                                    act("33333333-x", "work_requested", "2026-10-13T10:50:00+00:00")]))
    assert "ACTIVITY claude thread 33333333: work_requested; last success 10:50Z (10 min ago)" in lines
    assert any(line.startswith("ACTIVITY claude thread 22222222: idle_observed") and "error rate_limited" in line
               for line in lines)
    assert lines[-1] == "ACTIVITY: 1 more threads idle with no error, last success over 12 h ago (oldest 49 h)"
