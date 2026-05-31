# SPDX-License-Identifier: BUSL-1.1
"""Contracts for machine-checkable Grok review freshness proof."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from waggledance.core.bridge_event_schema import (
    BridgeEvent,
    validate_event,
    validate_event_file,
    validate_event_line,
)


MAIN_SHA = "a" * 40
PR_HEAD_SHA = "b" * 40
REVIEWED_HEAD_SHA = "c" * 40


def _freshness(**overrides: object) -> dict[str, object]:
    freshness: dict[str, object] = {
        "freshness_ok": True,
        "remote_main_sha": MAIN_SHA,
        "local_origin_main_sha": MAIN_SHA,
        "worktree_head": MAIN_SHA,
        "pr_head_sha": PR_HEAD_SHA,
    }
    freshness.update(overrides)
    return freshness


def _grok_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": "2026-05-31T18:12:00.000000Z",
        "agent": "grok-scout-1",
        "type": "message",
        "task_id": "dream-mode-wd-sub-area-contracts-regression-gap-audit-2026-05-31",
        "status": "grok_response",
        "severity": "info",
        "to": "codex-lead-1",
        "message": "advisory review",
        "paths": [],
        "write_scope": [],
        "run_id": "grok-scout-1-20260531T181509Z",
        "role": "scout-strategy",
        "agent_uuid": "2a45a162-0a6d-4cae-a1a5-4159e3a737e7",
        "session_id": "grok-scout-1-20260531T181509Z",
        "capabilities": [
            "competitor_scout",
            "strategy",
            "bridge_event",
        ],
        "pid": 12345,
        "cwd": "C:\\Windows\\System32",
        "payload": {"freshness": _freshness()},
    }
    event.update(overrides)
    return event


def test_grok_response_requires_and_accepts_freshness_proof() -> None:
    model = validate_event(_grok_event())

    assert isinstance(model, BridgeEvent)
    assert model.payload["freshness"]["freshness_ok"] is True
    assert model.payload["freshness"]["remote_main_sha"] == MAIN_SHA


def test_grok_alias_response_uses_same_freshness_contract() -> None:
    model = validate_event(_grok_event(agent="grok-1"))

    assert model.agent == "grok-1"
    assert model.payload["freshness"]["pr_head_sha"] == PR_HEAD_SHA


def test_grok_response_accepts_optional_review_head_when_full_sha() -> None:
    model = validate_event(
        _grok_event(
            payload={
                "freshness": _freshness(
                    reviewed_head_sha=REVIEWED_HEAD_SHA,
                    target_head_sha=PR_HEAD_SHA,
                ),
            },
        )
    )

    assert model.payload["freshness"]["reviewed_head_sha"] == REVIEWED_HEAD_SHA


def test_grok_response_allows_absent_pr_head_for_non_pr_plan_review() -> None:
    freshness = _freshness()
    freshness.pop("pr_head_sha")

    model = validate_event(_grok_event(payload={"freshness": freshness}))

    assert "pr_head_sha" not in model.payload["freshness"]


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({}, "grok freshness proof required"),
        ([], "grok freshness proof requires payload object"),
        ({"freshness": []}, "grok freshness proof required"),
        ({"freshness": {"freshness_ok": True}}, "remote_main_sha"),
        (
            {"freshness": _freshness(freshness_ok=False)},
            "grok freshness_ok must be true",
        ),
        (
            {"freshness": _freshness(freshness_ok="true")},
            "grok freshness_ok must be true",
        ),
        (
            {"freshness": _freshness(remote_main_sha="abc1234")},
            "remote_main_sha",
        ),
        (
            {"freshness": _freshness(remote_main_sha="A" * 40)},
            "remote_main_sha",
        ),
        (
            {"freshness": _freshness(local_origin_main_sha="c" * 40)},
            "grok freshness main sha mismatch",
        ),
        (
            {"freshness": _freshness(worktree_head="HEAD")},
            "worktree_head",
        ),
        (
            {"freshness": _freshness(worktree_head=PR_HEAD_SHA)},
            "grok freshness worktree sha mismatch",
        ),
        (
            {"freshness": _freshness(pr_head_sha="abc1234")},
            "pr_head_sha",
        ),
        (
            {"freshness": _freshness(reviewed_head_sha="not-a-sha")},
            "reviewed_head_sha",
        ),
    ],
)
def test_grok_response_rejects_missing_or_stale_freshness_proof(
    payload: object,
    match: str,
) -> None:
    with pytest.raises(Exception, match=match):
        validate_event(_grok_event(payload=payload))


def test_non_grok_events_remain_permissive_for_payload_shape() -> None:
    model = validate_event(
        _grok_event(
            agent="codex-tools-1",
            status="grok_response",
            role="tools-review",
            payload={},
        )
    )

    assert model.agent == "codex-tools-1"
    assert model.payload == {}


def test_non_response_grok_message_does_not_require_review_freshness() -> None:
    model = validate_event(_grok_event(status="research_finding", payload={}))

    assert model.agent == "grok-scout-1"
    assert model.status == "research_finding"


def test_validate_event_line_reports_missing_grok_freshness() -> None:
    line = json.dumps(_grok_event(payload={}))

    with pytest.raises(ValueError, match="grok freshness proof required"):
        validate_event_line(line, line_no=17)


def test_validate_event_file_reports_missing_grok_freshness(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join([
            json.dumps(_grok_event()),
            json.dumps(_grok_event(payload={})),
        ])
        + "\n",
        encoding="utf-8",
    )

    result = validate_event_file(events_path)

    assert result.checked == 2
    assert result.valid == 1
    assert result.invalid == 1
    assert result.issues[0].line_no == 2
    assert "grok freshness proof required" in result.issues[0].error
