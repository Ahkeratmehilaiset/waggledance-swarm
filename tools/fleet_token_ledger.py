# SPDX-License-Identifier: BUSL-1.1
"""Deterministic shadow token ledger over normalized provider usage records.

The ledger never estimates usage from bridge traffic.  It counts only native
totals produced by :mod:`tools.provider_session_usage`.  Any incomplete,
conflicting, partial, decreasing, future-dated, or unconfigured input sets the
affected lane to ``telemetry_unknown`` and disables automatic model turns.

This first slice is pure/shadow-only: it does not launch or stop processes,
write bridge events, consume reserves, or alter governance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from tools.provider_session_usage import MODEL_TASK_CLASSES, UsageRecord


@dataclass(frozen=True)
class LaneBudget:
    daily_cap: int
    weekly_cap: int
    release_reserve: int


DEFAULT_BUDGETS: dict[str, LaneBudget] = {
    "codex-lead-1": LaneBudget(600_000, 3_000_000, 150_000),
    "codex-tools-1": LaneBudget(250_000, 1_250_000, 50_000),
    "fable-5": LaneBudget(1_200_000, 6_000_000, 300_000),
    "claude-rco-1": LaneBudget(400_000, 2_000_000, 100_000),
    "claude-rco-2": LaneBudget(400_000, 2_000_000, 100_000),
    # Direct strongest review is recorded under this same combined lane.
    "grok-scout-1": LaneBudget(300_000, 1_500_000, 100_000),
}


def build_fleet_token_ledger(
    records: Iterable[UsageRecord],
    *,
    now: datetime,
    budgets: dict[str, LaneBudget] | None = None,
) -> dict[str, Any]:
    """Build a daily/rolling-seven-day ledger with fail-closed lane states."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(timezone.utc)
    configured = dict(DEFAULT_BUDGETS if budgets is None else budgets)
    materialized = list(records)
    all_lanes = sorted(set(configured) | {record.lane for record in materialized})
    by_lane: dict[str, list[UsageRecord]] = {lane: [] for lane in all_lanes}
    lane_errors: dict[str, set[str]] = {lane: set() for lane in all_lanes}
    duplicate_counts: dict[str, int] = {lane: 0 for lane in all_lanes}

    dedupe: dict[tuple[str, str, str], UsageRecord] = {}
    for record in materialized:
        errors = _validate_record(record, now=now)
        lane_errors[record.lane].update(errors)
        key = (record.provider, record.session_id, record.source_event_id)
        previous = dedupe.get(key)
        if previous is not None:
            if previous == record:
                duplicate_counts[record.lane] += 1
            else:
                lane_errors[record.lane].add("source_event_reuse_conflict")
                lane_errors[previous.lane].add("source_event_reuse_conflict")
            continue
        dedupe[key] = record
        by_lane[record.lane].append(record)

    for lane, lane_records in by_lane.items():
        if lane not in configured:
            lane_errors[lane].add("unconfigured_lane")
        if not lane_records:
            lane_errors[lane].add("no_native_usage_records")
        if any(not record.session_complete for record in lane_records):
            lane_errors[lane].add("incomplete_session")
        _validate_accounting_sequences(lane_records, lane_errors[lane])

    utc_day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rolling_week_start = now - timedelta(days=7)
    lanes: dict[str, dict[str, Any]] = {}
    for lane in all_lanes:
        lane_records = by_lane[lane]
        timestamped_records = [
            (record, occurred)
            for record in lane_records
            if (occurred := _try_parse_utc(record.occurred_at_utc)) is not None
        ]
        daily_records = [
            record
            for record, occurred in timestamped_records
            if utc_day_start <= occurred <= now
        ]
        weekly_records = [
            record
            for record, occurred in timestamped_records
            if rolling_week_start <= occurred <= now
        ]
        daily_consumed = sum(record.total_tokens for record in daily_records)
        weekly_consumed = sum(record.total_tokens for record in weekly_records)
        task_classes: dict[str, int] = {}
        for record in daily_records:
            task_classes[record.task_class] = (
                task_classes.get(record.task_class, 0) + record.total_tokens
            )

        budget = configured.get(lane)
        errors = sorted(lane_errors[lane])
        if budget is None:
            daily_cap = weekly_cap = release_reserve = 0
            daily_remaining = weekly_remaining = 0
            state = "telemetry_unknown"
        else:
            daily_cap = budget.daily_cap
            weekly_cap = budget.weekly_cap
            release_reserve = budget.release_reserve
            daily_remaining = max(0, daily_cap - daily_consumed)
            weekly_remaining = max(0, weekly_cap - weekly_consumed)
            if errors:
                state = "telemetry_unknown"
            elif weekly_consumed >= weekly_cap:
                state = "weekly_hard_stop"
            elif daily_consumed >= (daily_cap * 80 + 99) // 100:
                state = "budget_idle"
            else:
                state = "within_budget"

        lanes[lane] = {
            "daily_cap": daily_cap,
            "weekly_cap": weekly_cap,
            "release_reserve": release_reserve,
            "daily_consumed": daily_consumed,
            "weekly_consumed": weekly_consumed,
            "daily_remaining": daily_remaining,
            "weekly_remaining": weekly_remaining,
            "task_classes": dict(sorted(task_classes.items())),
            "records": len(lane_records),
            "deduplicated_records": duplicate_counts[lane],
            "telemetry_errors": errors,
            "enforcement_state": state,
            "automatic_turns_allowed": state == "within_budget",
        }

    return {
        "schema": "wd.fleet-token-ledger.v1",
        "generated_at_utc": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "daily_window_start_utc": utc_day_start.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "rolling_week_start_utc": rolling_week_start.strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        ),
        "shadow_only": True,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "unconfigured_lanes": sorted(set(all_lanes) - set(configured)),
        "lanes": lanes,
    }


def _validate_record(record: UsageRecord, *, now: datetime) -> set[str]:
    errors: set[str] = set()
    if record.task_class not in MODEL_TASK_CLASSES:
        errors.add("unknown_task_class")
    if record.accounting_mode not in {"per_event", "session_cumulative_delta"}:
        errors.add("unknown_accounting_mode")
    numeric = (
        record.input_tokens,
        record.output_tokens,
        record.cached_input_tokens,
        record.cache_write_input_tokens,
        record.reasoning_output_tokens,
        record.total_tokens,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in numeric):
        errors.add("invalid_token_count")
    if record.provider == "openai_codex":
        if record.total_tokens != record.input_tokens + record.output_tokens:
            errors.add("native_total_mismatch")
        if record.cached_input_tokens > record.input_tokens:
            errors.add("cache_detail_exceeds_input")
        if record.reasoning_output_tokens > record.output_tokens:
            errors.add("reasoning_detail_exceeds_output")
    elif record.provider == "anthropic_claude":
        expected = (
            record.input_tokens
            + record.cached_input_tokens
            + record.cache_write_input_tokens
            + record.output_tokens
        )
        if record.total_tokens != expected:
            errors.add("native_total_mismatch")
        if record.reasoning_output_tokens > record.output_tokens:
            errors.add("reasoning_detail_exceeds_output")
    else:
        errors.add("unsupported_provider")
    try:
        occurred = _parse_utc(record.occurred_at_utc)
        if occurred > now:
            errors.add("future_usage_record")
    except ValueError:
        errors.add("invalid_usage_timestamp")
    return errors


def _validate_accounting_sequences(
    records: list[UsageRecord], errors: set[str]
) -> None:
    by_session: dict[tuple[str, str], list[UsageRecord]] = {}
    for record in records:
        by_session.setdefault((record.provider, record.session_id), []).append(record)
    for session_records in by_session.values():
        valid_session_records = [
            record
            for record in session_records
            if _try_parse_utc(record.occurred_at_utc) is not None
        ]
        if not valid_session_records:
            continue
        modes = {record.accounting_mode for record in valid_session_records}
        if len(modes) != 1:
            errors.add("mixed_session_accounting_modes")
            continue
        if modes == {"per_event"}:
            if any(
                record.cumulative_total_tokens is not None
                for record in valid_session_records
            ):
                errors.add("unexpected_cumulative_total")
            continue
        ordered = sorted(
            valid_session_records,
            key=lambda record: (
                _parse_utc(record.occurred_at_utc),
                record.source_event_id,
            ),
        )
        prior = 0
        for index, record in enumerate(ordered):
            cumulative = record.cumulative_total_tokens
            if cumulative is None or cumulative < 0:
                errors.add("missing_cumulative_usage")
                continue
            if index == 0 and cumulative != record.total_tokens:
                errors.add("partial_cumulative_session")
            if cumulative < prior:
                errors.add("non_monotonic_cumulative_usage")
            elif cumulative - prior != record.total_tokens:
                errors.add("cumulative_delta_mismatch")
            prior = cumulative


def _parse_utc(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _try_parse_utc(value: str) -> datetime | None:
    try:
        return _parse_utc(value)
    except (TypeError, ValueError):
        return None
