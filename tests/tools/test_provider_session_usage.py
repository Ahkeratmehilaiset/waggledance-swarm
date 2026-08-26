# SPDX-License-Identifier: BUSL-1.1
"""Provider-native token extraction stays exact and fail closed."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tools.provider_session_usage import (
    TelemetryUnknownError,
    extract_claude_interactive_usage,
    extract_codex_session_usage,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _codex_rows() -> list[dict]:
    # Captured shape: codex-cli 0.149.0 session JSONL, 2026-08-26.
    return [
        {
            "timestamp": "2026-08-26T14:39:22.817Z",
            "type": "session_meta",
            "payload": {
                "id": "01a03e82-e919-7d60-943e-85ee804f1823",
                "cli_version": "0.149.0",
                "originator": "codex_exec",
                "source": "exec",
            },
        },
        {
            "timestamp": "2026-08-26T14:39:24.342Z",
            "type": "turn_context",
            "payload": {"model": "gpt-5.6-terra", "effort": "high"},
        },
        {
            "timestamp": "2026-08-26T14:41:06.161Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 430_370,
                        "cached_input_tokens": 374_016,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 3_768,
                        "reasoning_output_tokens": 1_813,
                        "total_tokens": 434_138,
                    },
                    "last_token_usage": {
                        "input_tokens": 430_370,
                        "cached_input_tokens": 374_016,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 3_768,
                        "reasoning_output_tokens": 1_813,
                        "total_tokens": 434_138,
                    },
                    "model_context_window": 258_400,
                },
            },
        },
        {
            "timestamp": "2026-08-26T14:41:25.840Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": 489_611,
                        "cached_input_tokens": 431_104,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 4_677,
                        "reasoning_output_tokens": 2_049,
                        "total_tokens": 494_288,
                    },
                    "last_token_usage": {
                        "input_tokens": 59_241,
                        "cached_input_tokens": 57_088,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 909,
                        "reasoning_output_tokens": 236,
                        "total_tokens": 60_150,
                    },
                    "model_context_window": 258_400,
                },
            },
        },
    ]


def _extract_codex(rows: list[dict], *, complete: bool = True):
    return extract_codex_session_usage(
        rows,
        lane="codex-lead-1",
        task_class="lead_triage_design",
        invocation_argv_sha256=SHA_A,
        source_log_sha256=SHA_B,
        session_complete=complete,
    )


def test_codex_0149_captured_shape_uses_native_last_total_once():
    records = _extract_codex(_codex_rows())
    assert [record.total_tokens for record in records] == [434_138, 60_150]
    assert [record.cumulative_total_tokens for record in records] == [
        434_138,
        494_288,
    ]
    assert records[1].cached_input_tokens == 57_088
    assert records[1].reasoning_output_tokens == 236
    assert records[1].model == "gpt-5.6-terra"
    assert records[1].provider == "openai_codex"
    assert records[1].accounting_mode == "session_cumulative_delta"


def test_codex_cache_and_reasoning_are_details_not_readded():
    record = _extract_codex(_codex_rows())[1]
    assert record.total_tokens == record.input_tokens + record.output_tokens
    assert record.total_tokens != (
        record.input_tokens
        + record.cached_input_tokens
        + record.output_tokens
        + record.reasoning_output_tokens
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda usage: usage.__setitem__("input_tokens", -1),
        lambda usage: usage.__setitem__("cached_input_tokens", 500_000),
        lambda usage: usage.__setitem__("reasoning_output_tokens", 10_000),
        lambda usage: usage.__setitem__("total_tokens", 1),
    ],
)
def test_codex_invalid_or_overlapping_counts_fail_closed(mutation):
    rows = _codex_rows()
    mutation(rows[2]["payload"]["info"]["last_token_usage"])
    with pytest.raises(TelemetryUnknownError):
        _extract_codex(rows)


def test_codex_unknown_cli_version_and_missing_metadata_fail_closed():
    rows = _codex_rows()
    rows[0]["payload"]["cli_version"] = "0.150.0"
    with pytest.raises(TelemetryUnknownError, match="unsupported Codex CLI"):
        _extract_codex(rows)

    with pytest.raises(TelemetryUnknownError, match="session_meta"):
        _extract_codex(_codex_rows()[1:])


def test_codex_active_session_is_recorded_but_not_claimed_complete():
    records = _extract_codex(_codex_rows(), complete=False)
    assert records
    assert all(record.session_complete is False for record in records)


def _claude_row() -> dict:
    # Captured shape: Claude interactive session JSONL, 2026-08-26.
    return {
        "type": "assistant",
        "timestamp": "2026-08-26T05:28:14.944Z",
        "sessionId": "34bd49c2-8180-4975-b762-11c0cdf9ab71",
        "uuid": "e3d011fa-cccb-4b9e-b763-df0fcc3a2019",
        "message": {
            "id": "msg_01NativeFixture",
            "model": "claude-fable-5",
            "usage": {
                "input_tokens": 2,
                "cache_creation_input_tokens": 478,
                "cache_read_input_tokens": 302_230,
                "output_tokens": 1_462,
                "output_tokens_details": {"thinking_tokens": 150},
            },
        },
    }


def _extract_claude(rows: list[dict], *, complete: bool = True):
    return extract_claude_interactive_usage(
        rows,
        lane="fable-5",
        task_class="production_code_tests",
        invocation_argv_sha256=SHA_A,
        source_log_sha256=SHA_B,
        session_complete=complete,
    )


def test_claude_captured_shape_sums_nonoverlapping_input_components():
    record = _extract_claude([_claude_row()])[0]
    assert record.input_tokens == 2
    assert record.cache_write_input_tokens == 478
    assert record.cached_input_tokens == 302_230
    assert record.output_tokens == 1_462
    assert record.reasoning_output_tokens == 150
    assert record.total_tokens == 304_172
    assert record.accounting_mode == "per_event"


def test_claude_thinking_detail_is_not_readded():
    record = _extract_claude([_claude_row()])[0]
    assert record.total_tokens == (
        record.input_tokens
        + record.cache_write_input_tokens
        + record.cached_input_tokens
        + record.output_tokens
    )


def test_claude_duplicate_native_message_is_deduplicated():
    first = _claude_row()
    duplicate = deepcopy(first)
    duplicate["uuid"] = "44e6d115-d97e-4609-bd9a-1fe889e109c9"
    assert len(_extract_claude([first, duplicate])) == 1


def test_claude_conflicting_reuse_of_native_message_id_fails_closed():
    first = _claude_row()
    conflict = deepcopy(first)
    conflict["message"]["usage"]["output_tokens"] += 1
    with pytest.raises(TelemetryUnknownError, match="conflicting Claude message"):
        _extract_claude([first, conflict])


def test_unknown_task_class_digest_and_schema_fail_closed():
    row = _claude_row()
    with pytest.raises(TelemetryUnknownError, match="task class"):
        extract_claude_interactive_usage(
            [row],
            lane="fable-5",
            task_class="make_something_up",
            invocation_argv_sha256=SHA_A,
            source_log_sha256=SHA_B,
            session_complete=True,
        )
    with pytest.raises(TelemetryUnknownError, match="SHA-256"):
        extract_claude_interactive_usage(
            [row],
            lane="fable-5",
            task_class="production_code_tests",
            invocation_argv_sha256="not-a-digest",
            source_log_sha256=SHA_B,
            session_complete=True,
        )
    row["message"]["usage"]["mystery_tokens"] = 9
    with pytest.raises(TelemetryUnknownError, match="unknown Claude usage"):
        _extract_claude([row])

