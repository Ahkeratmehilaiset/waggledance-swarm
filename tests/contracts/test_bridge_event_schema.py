# SPDX-License-Identifier: BUSL-1.1
"""Contracts for the runtime bridge event schema validator."""
from __future__ import annotations

import json
from pathlib import Path
import hashlib

import pytest

from waggledance.core.bridge_event_schema import (
    BRIDGE_EVENT_SCHEMA_VERSION,
    BridgeEvent,
    validate_event,
    validate_event_file,
    validate_event_line,
)


def _good_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": "2026-05-16T05:39:57.9995496Z",
        "agent": "codex",
        "type": "handoff",
        "task_id": "claude-rco-pr420-sqlite-read-transport-2026-05-16",
        "status": "assigned_rco_review",
        "severity": "",
        "to": "claude",
        "message": "RCO review requested.",
        "paths": [
            "waggledance/core/v3_13_0/sqlite_read_transport.py",
            "tests/v3_13_0/test_sqlite_read_transport.py",
        ],
        "write_scope": [],
        "run_id": "",
        "pid": 23492,
        "cwd": "C:\\Python\\project2-master",
        "payload": {},
    }
    event.update(overrides)
    return event


def test_valid_write_agent_event_shape_validates() -> None:
    model = validate_event(_good_event(extra_future_field="allowed"))

    assert isinstance(model, BridgeEvent)
    assert model.agent == "codex"
    assert model.type == "handoff"
    assert model.paths == [
        "waggledance/core/v3_13_0/sqlite_read_transport.py",
        "tests/v3_13_0/test_sqlite_read_transport.py",
    ]
    assert model.model_extra == {"extra_future_field": "allowed"}


def test_comma_separated_targets_are_validated_per_agent() -> None:
    model = validate_event(_good_event(type="message", to="claude,operator"))

    assert model.to == "claude,operator"


def test_regex_agent_ids_are_valid_for_multi_agent_bridge() -> None:
    model = validate_event(
        _good_event(agent="codex-2", type="message", to="claude-1,gemini_1")
    )

    assert model.agent == "codex-2"
    assert model.to == "claude-1,gemini_1"


def test_role_uuid_session_and_capabilities_are_valid_optional_metadata() -> None:
    model = validate_event(
        _good_event(
            agent="codex-impl-1",
            to="claude-rco-1",
            role="impl",
            agent_uuid="11111111-2222-3333-4444-555555555555",
            session_id="codex-impl-1-20260523T140000Z",
            capabilities=["bridge_event", "work_queue", "magma.receipt:v1"],
        )
    )

    assert model.role == "impl"
    assert model.agent_uuid == "11111111-2222-3333-4444-555555555555"
    assert model.session_id == "codex-impl-1-20260523T140000Z"
    assert model.capabilities == ["bridge_event", "work_queue", "magma.receipt:v1"]


def test_custom_event_types_remain_valid_for_polymorphic_continuity() -> None:
    model = validate_event(_good_event(type="ownership_proposal", status="open"))

    assert model.type == "ownership_proposal"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"agent": "Gpt"}, "agent"),
        ({"agent": "x"}, "agent"),
        ({"agent": "gpt.5"}, "agent"),
        ({"type": ""}, "type"),
        ({"type": "bad\ntype"}, "type"),
        ({"to": "claude,Gpt"}, "to"),
        ({"to": "claude,gpt.5"}, "to"),
        ({"role": "Impl"}, "role"),
        ({"agent_uuid": "not-a-uuid"}, "agent_uuid"),
        ({"session_id": "bad session"}, "session_id"),
        ({"capabilities": ["Bad Capability"]}, "capabilities"),
        ({"paths": "not-a-list"}, "paths"),
        ({"pid": True}, "pid"),
        ({"ts_utc": "2026-05-16T05:39:57"}, "ts_utc"),
    ],
)
def test_invalid_event_fields_raise_clear_validation_errors(
    overrides: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(Exception, match=match):
        validate_event(_good_event(**overrides))


def test_wake_request_requires_explicit_target() -> None:
    with pytest.raises(Exception, match="wake_request requires to"):
        validate_event(_good_event(type="wake_request", to=""))


def test_claim_like_events_require_task_id() -> None:
    with pytest.raises(Exception, match="claim requires task_id"):
        validate_event(_good_event(type="claim", task_id="", to=""))


def test_payload_parse_error_objects_remain_valid() -> None:
    model = validate_event(
        _good_event(payload={"raw": "{not-json}", "parse_error": "bad JSON"})
    )

    assert model.payload["raw"] == "{not-json}"


def test_validate_event_line_rejects_non_object_json() -> None:
    with pytest.raises(ValueError, match="event must be a JSON object"):
        validate_event_line("[]", line_no=7)


def test_validate_event_file_reports_line_numbers_without_throwing(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join([
            json.dumps(_good_event()),
            "{not-json}",
            json.dumps(_good_event(type="wake_request", to="")),
        ])
        + "\n",
        encoding="utf-8",
    )

    result = validate_event_file(events_path)

    assert result.schema_version == BRIDGE_EVENT_SCHEMA_VERSION
    assert result.checked == 3
    assert result.valid == 1
    assert result.invalid == 2
    assert result.waived_invalid == 0
    assert result.issues[0].line_no == 2
    assert result.issues[1].line_no == 3
    assert result.issues[0].raw_sha256.startswith("sha256:")


def test_validate_event_file_tail_limits_physical_lines(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        "\n".join([
            "{not-json}",
            json.dumps(_good_event(type="done", to="", status="done")),
        ])
        + "\n",
        encoding="utf-8",
    )

    result = validate_event_file(events_path, tail=1)

    assert result.ok
    assert result.checked == 1
    assert result.valid == 1


def test_validate_event_file_applies_hash_bound_waiver(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        json.dumps(_good_event(type="handoff", task_id="")) + "\n",
        encoding="utf-8",
    )
    raw_line = events_path.read_text(encoding="utf-8").splitlines()[0]

    result = validate_event_file(
        events_path,
        waived_line_sha256={
            1: "sha256:" + hashlib.sha256(raw_line.encode("utf-8")).hexdigest(),
        },
        waived_line_errors={
            1: "line 1: <event>: Value error, handoff requires task_id",
        },
    )

    assert result.ok
    assert result.checked == 1
    assert result.valid == 0
    assert result.invalid == 0
    assert result.waived_invalid == 1
    assert result.issues == ()
    assert result.waived_issues[0].line_no == 1
