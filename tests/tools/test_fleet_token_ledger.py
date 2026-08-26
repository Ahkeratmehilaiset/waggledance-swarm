# SPDX-License-Identifier: BUSL-1.1
"""Deterministic token ledger tests: ambiguity always disables auto turns."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from tools.fleet_token_ledger import DEFAULT_BUDGETS, build_fleet_token_ledger
from tools.provider_session_usage import UsageRecord


NOW = datetime(2026, 8, 26, 15, 0, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64


def _record(
    *,
    lane: str = "codex-lead-1",
    event: str = "line:1",
    ts: str = "2026-08-26T14:00:00.000000Z",
    total: int = 100,
    cumulative: int | None = 100,
    complete: bool = True,
    provider: str = "openai_codex",
    session: str = "session-1",
) -> UsageRecord:
    return UsageRecord(
        lane=lane,
        provider=provider,
        model="gpt-5.6-sol" if provider == "openai_codex" else "claude-fable-5",
        task_class=(
            "lead_triage_design"
            if lane == "codex-lead-1"
            else "production_code_tests"
        ),
        session_id=session,
        source_event_id=event,
        occurred_at_utc=ts,
        invocation_argv_sha256=SHA_A,
        source_log_sha256=SHA_B,
        input_tokens=total - 10,
        output_tokens=10,
        cached_input_tokens=0,
        cache_write_input_tokens=0,
        reasoning_output_tokens=0,
        total_tokens=total,
        accounting_mode=(
            "session_cumulative_delta" if cumulative is not None else "per_event"
        ),
        cumulative_total_tokens=cumulative,
        session_complete=complete,
    )


def test_default_budgets_match_reviewed_caps():
    assert DEFAULT_BUDGETS["codex-lead-1"].daily_cap == 600_000
    assert DEFAULT_BUDGETS["codex-lead-1"].weekly_cap == 3_000_000
    assert DEFAULT_BUDGETS["fable-5"].daily_cap == 1_200_000
    assert DEFAULT_BUDGETS["fable-5"].release_reserve == 300_000
    assert DEFAULT_BUDGETS["claude-rco-1"].weekly_cap == 2_000_000


def test_green_complete_sequence_counts_native_total_once():
    records = [
        _record(total=100, cumulative=100, event="line:1"),
        _record(total=50, cumulative=150, event="line:2"),
    ]
    result = build_fleet_token_ledger(records, now=NOW)
    lane = result["lanes"]["codex-lead-1"]
    assert lane["daily_consumed"] == 150
    assert lane["weekly_consumed"] == 150
    assert lane["daily_remaining"] == 599_850
    assert lane["automatic_turns_allowed"] is True
    assert lane["enforcement_state"] == "within_budget"
    assert lane["task_classes"] == {"lead_triage_design": 150}


def test_cache_and_reasoning_are_never_added_to_total_again():
    record = replace(
        _record(total=100, cumulative=100),
        cached_input_tokens=80,
        reasoning_output_tokens=5,
    )
    result = build_fleet_token_ledger([record], now=NOW)
    assert result["lanes"]["codex-lead-1"]["daily_consumed"] == 100


def test_daily_80_percent_goes_sentinel_idle():
    record = _record(total=480_000, cumulative=480_000)
    lane = build_fleet_token_ledger([record], now=NOW)["lanes"]["codex-lead-1"]
    assert lane["enforcement_state"] == "budget_idle"
    assert lane["automatic_turns_allowed"] is False


def test_weekly_cap_is_hard_stop():
    record = _record(total=3_000_000, cumulative=3_000_000)
    lane = build_fleet_token_ledger([record], now=NOW)["lanes"]["codex-lead-1"]
    assert lane["enforcement_state"] == "weekly_hard_stop"
    assert lane["automatic_turns_allowed"] is False


def test_active_or_incomplete_session_is_telemetry_unknown():
    lane = build_fleet_token_ledger(
        [_record(complete=False)], now=NOW
    )["lanes"]["codex-lead-1"]
    assert lane["enforcement_state"] == "telemetry_unknown"
    assert lane["automatic_turns_allowed"] is False
    assert "incomplete_session" in lane["telemetry_errors"]


def test_decreasing_or_partial_cumulative_sequence_fails_closed():
    decreasing = [
        _record(total=100, cumulative=100, event="line:1"),
        _record(total=10, cumulative=90, event="line:2"),
    ]
    lane = build_fleet_token_ledger(decreasing, now=NOW)["lanes"][
        "codex-lead-1"
    ]
    assert lane["enforcement_state"] == "telemetry_unknown"
    assert "non_monotonic_cumulative_usage" in lane["telemetry_errors"]

    partial = [_record(total=10, cumulative=100, event="line:20")]
    lane = build_fleet_token_ledger(partial, now=NOW)["lanes"]["codex-lead-1"]
    assert lane["enforcement_state"] == "telemetry_unknown"
    assert "partial_cumulative_session" in lane["telemetry_errors"]


def test_cumulative_delta_must_equal_native_last_total():
    records = [
        _record(total=100, cumulative=100, event="line:1"),
        _record(total=40, cumulative=150, event="line:2"),
    ]
    lane = build_fleet_token_ledger(records, now=NOW)["lanes"]["codex-lead-1"]
    assert lane["enforcement_state"] == "telemetry_unknown"
    assert "cumulative_delta_mismatch" in lane["telemetry_errors"]


def test_identical_duplicate_is_counted_once_but_conflict_fails_closed():
    record = _record()
    lane = build_fleet_token_ledger([record, record], now=NOW)["lanes"][
        "codex-lead-1"
    ]
    assert lane["daily_consumed"] == 100
    assert lane["deduplicated_records"] == 1

    conflict = replace(record, total_tokens=101, output_tokens=11)
    lane = build_fleet_token_ledger([record, conflict], now=NOW)["lanes"][
        "codex-lead-1"
    ]
    assert lane["enforcement_state"] == "telemetry_unknown"
    assert "source_event_reuse_conflict" in lane["telemetry_errors"]


def test_rolling_seven_day_and_utc_day_boundaries():
    records = [
        _record(event="today", ts="2026-08-26T00:00:00.000000Z", total=10),
        _record(
            event="week",
            ts="2026-08-19T15:00:00.000001Z",
            total=20,
            cumulative=None,
            provider="anthropic_claude",
            session="session-2",
        ),
        _record(
            event="old",
            ts="2026-08-19T14:59:59.999999Z",
            total=30,
            cumulative=None,
            provider="anthropic_claude",
            session="session-3",
        ),
    ]
    lane = build_fleet_token_ledger(records, now=NOW)["lanes"]["codex-lead-1"]
    assert lane["daily_consumed"] == 10
    assert lane["weekly_consumed"] == 30


def test_unknown_lane_is_not_silently_assigned_a_budget():
    record = replace(_record(), lane="new-lane")
    result = build_fleet_token_ledger([record], now=NOW)
    assert result["unconfigured_lanes"] == ["new-lane"]
    assert result["lanes"]["new-lane"]["enforcement_state"] == "telemetry_unknown"
    assert result["lanes"]["new-lane"]["automatic_turns_allowed"] is False

