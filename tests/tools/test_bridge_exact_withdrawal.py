"""Exact unbound withdrawal closure in the next-action selector.

A requester closure that carries a ``withdraws`` descriptor closes exactly one
unbound request version, and only when the descriptor names that version and
its reader-row digest. Anything else closes nothing and never falls back to
generic same-task closure. Events without ``withdraws`` keep legacy behaviour.

The reader-row digest is the lowercase SHA-256 of the strict UTF-8 row exactly
as the canonical bridge reader yields it: split on LF, at most one trailing CR
removed, every other byte kept. It is not a physical-row hash.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

import tools.bridge_next_action as bridge_next_action
from tools.bridge_next_action import (
    _open_requests_for_agent,
    read_events,
    recommend_next_action,
)

FABLE = "fable-5"
LEAD = "codex-lead-1"
TOOLS = "codex-tools-1"
UUIDS = {
    FABLE: "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
    LEAD: "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    TOOLS: "7a8af68d-20bc-4598-9953-23c5dd98b102",
}
TASK = "fable-5/withdrawal-fixture"
V1_TS = "2026-10-06T17:20:00Z"
V2_TS = "2026-10-06T17:29:29.9136087Z"
CLOSE_TS = "2026-10-06T18:17:12Z"
_REAL_REGISTRY_LOADER = getattr(bridge_next_action, "_load_withdrawal_identity_registry", None)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        bridge_next_action,
        "_load_withdrawal_identity_registry",
        lambda: dict(UUIDS),
        raising=False,
    )


def _notice(ts: str, status: str, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts_utc": ts,
        "agent": FABLE,
        "agent_uuid": UUIDS[FABLE],
        "to": LEAD,
        "type": "message",
        "task_id": TASK,
        "status": status,
        "message": f"notice {status}",
    }
    event.update(extra)
    return event


def _row(event: dict[str, Any]) -> bytes:
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(row: bytes) -> str:
    return hashlib.sha256(row).hexdigest()


def _descriptor(target: dict[str, Any], row: bytes, **overrides: Any) -> dict[str, Any]:
    descriptor = {
        "agent": target["agent"],
        "type": target["type"],
        "status": target["status"],
        "task_id": target["task_id"],
        "ts_utc": target["ts_utc"],
        "raw_line_sha256": _digest(row),
    }
    descriptor.update(overrides)
    return descriptor


def _withdrawal(descriptor: Any, *, agent: str = FABLE, **extra: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts_utc": CLOSE_TS,
        "agent": agent,
        "agent_uuid": UUIDS.get(agent, ""),
        "to": LEAD,
        "type": "message",
        "task_id": TASK,
        "status": "withdrawn",
        "message": "withdrawing the notice",
        "payload": {"withdraws": descriptor},
    }
    event.update(extra)
    return event


def _write(tmp_path: Path, rows: list[bytes], terminators: list[bytes] | None = None) -> Path:
    path = tmp_path / "shared" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    ends = terminators or [b"\n"] * len(rows)
    path.write_bytes(b"".join(row + end for row, end in zip(rows, ends)))
    return path


def _open_statuses(
    tmp_path: Path,
    rows: list[bytes],
    terminators: list[bytes] | None = None,
    *,
    agent: str = LEAD,
) -> list[str]:
    events = read_events(_write(tmp_path, rows, terminators))
    return [str(event["status"]) for event in _open_requests_for_agent(agent=agent, events=events)]


def _report(tmp_path: Path, rows: list[bytes]) -> dict[str, Any]:
    return recommend_next_action(
        agent=LEAD,
        events=read_events(_write(tmp_path, rows)),
        claims=[],
        now_utc=datetime.fromisoformat("2026-10-06T18:20:00+00:00"),
    )


def _reasons(report: dict[str, Any]) -> list[str]:
    return [item["reason"] for item in report.get("withdrawal_diagnostics", [])]


# --- D1 / D2 reproductions -------------------------------------------------


def test_d1_two_versions_exact_withdrawal_closes_only_the_named_version(tmp_path: Path) -> None:
    v1, v2 = _notice(V1_TS, "requested"), _notice(V2_TS, "fix_pushed")
    v1_row, v2_row = _row(v1), _row(v2)
    closure = _row(_withdrawal(_descriptor(v2, v2_row)))

    assert _open_statuses(tmp_path, [v1_row, v2_row, closure]) == ["requested"]


def test_d1_twin_withdrawing_v1_leaves_v2_routed(tmp_path: Path) -> None:
    v1, v2 = _notice(V1_TS, "requested"), _notice(V2_TS, "fix_pushed")
    v1_row, v2_row = _row(v1), _row(v2)
    closure = _row(_withdrawal(_descriptor(v1, v1_row)))

    assert _open_statuses(tmp_path, [v1_row, v2_row, closure]) == ["fix_pushed"]


def test_d2_single_version_descriptor_timestamp_mismatch_stays_open(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row, ts_utc="2026-10-06T16:00:00Z")))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


def test_single_version_exact_withdrawal_closes(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == []


# --- reader-row digest: terminators and bytes -------------------------------


@pytest.mark.parametrize(
    ("suffix", "terminator", "hashed_suffix", "expected_open"),
    [
        pytest.param(b"", b"\n", b"", [], id="lf_reader_row"),
        pytest.param(b"", b"\r\n", b"", [], id="crlf_reader_row_without_cr"),
        pytest.param(b"", b"\r\n", b"\r", ["fix_pushed"], id="crlf_hash_including_cr"),
        pytest.param(b" \t", b"\n", b" \t", [], id="trailing_whitespace_kept"),
        pytest.param(b" \t", b"\n", b"", ["fix_pushed"], id="trailing_whitespace_trimmed_hash"),
        pytest.param(b" \t", b"\r\n", b" \t", [], id="trailing_whitespace_crlf"),
    ],
)
def test_reader_row_digest_terminator_and_whitespace(
    tmp_path: Path,
    suffix: bytes,
    terminator: bytes,
    hashed_suffix: bytes,
    expected_open: list[str],
) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row + hashed_suffix)))

    assert _open_statuses(
        tmp_path, [notice_row + suffix, closure], [terminator, b"\n"]
    ) == expected_open


def test_non_ascii_row_uses_strict_utf8_digest(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed", message="välitä leadille")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == []


@pytest.mark.parametrize(
    ("row_bytes", "terminator"),
    [
        pytest.param("interior", b"\n", id="bare_cr_inside_row"),
        pytest.param("trailing", b"\r\n", id="double_cr_terminator"),
    ],
)
def test_row_with_remaining_cr_is_non_addressable(
    tmp_path: Path, row_bytes: str, terminator: bytes
) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    compact = _row(notice)
    if row_bytes == "interior":
        # JSON allows CR as whitespace between tokens, so this is still one event.
        target = compact.replace(b',"agent"', b',\r"agent"', 1)
        if target == compact:
            target = compact.replace(b'{"', b'{\r"', 1)
        hashed = target
    else:
        target = compact + b"\r"
        hashed = target  # what the reader yields after removing one CR
    closure = _row(_withdrawal(_descriptor(notice, hashed)))

    rows = [target, closure]
    assert _open_statuses(tmp_path, rows, [terminator, b"\n"]) == ["fix_pushed"]
    report = _report_with_terminators(tmp_path / "report", rows, [terminator, b"\n"])
    assert "withdrawal_non_addressable" in _reasons(report)


def _report_with_terminators(tmp_path: Path, rows: list[bytes], terminators: list[bytes]) -> dict[str, Any]:
    return recommend_next_action(
        agent=LEAD,
        events=read_events(_write(tmp_path, rows, terminators)),
        claims=[],
        now_utc=datetime.fromisoformat("2026-10-06T18:20:00+00:00"),
    )


def test_historical_bare_cr_split_row_is_non_addressable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _notice(V1_TS, "requested")
    second = _notice(V2_TS, "fix_pushed")
    joined = _row(first) + b"\r" + _row(second)
    monkeypatch.setattr(
        bridge_next_action, "_LEGACY_BARE_CR_ROW_SHA256", _digest(joined)
    )
    monkeypatch.setattr(
        bridge_next_action,
        "_LEGACY_BARE_CR_EVENT_FINGERPRINTS",
        tuple(
            (e["agent"], e["task_id"], e["ts_utc"], e["type"], e["status"])
            for e in (first, second)
        ),
    )
    closure = _row(_withdrawal(_descriptor(second, joined)))

    assert _open_statuses(tmp_path, [joined, closure]) == ["requested", "fix_pushed"]


# --- descriptor mismatches never fall back to generic closure ---------------


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"raw_line_sha256": "0" * 64}, id="wrong_hash"),
        pytest.param({"agent": LEAD}, id="wrong_descriptor_agent"),
        pytest.param({"type": "decision"}, id="wrong_type"),
        pytest.param({"status": "requested"}, id="wrong_status"),
        pytest.param({"task_id": "fable-5/other-task"}, id="wrong_task"),
        pytest.param({"ts_utc": "2026-10-06T17:29:29.9136088Z"}, id="wrong_timestamp"),
        pytest.param({"agent": "Fable-5"}, id="case_folded_agent"),
    ],
)
def test_single_field_mismatch_closes_nothing(tmp_path: Path, overrides: dict[str, Any]) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row, **overrides)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


def test_equal_instant_in_other_spelling_is_a_mismatch(tmp_path: Path) -> None:
    # ts_utc compares as written, so Python and PowerShell cannot disagree on
    # sub-microsecond ticks or offset spellings.
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row, ts_utc="2026-10-06T17:29:29.9136087+00:00")))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


def test_withdrawal_by_non_owner_closes_nothing(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row), agent=TOOLS))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


def test_target_agent_answer_with_withdraws_does_not_generically_close(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    answer = _withdrawal(_descriptor(notice, notice_row, ts_utc="2026-10-06T16:00:00Z"), agent=LEAD, to=FABLE, status="answered")

    assert _open_statuses(tmp_path, [notice_row, _row(answer)]) == ["fix_pushed"]


def test_withdrawal_earlier_in_append_order_closes_nothing(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))

    assert _open_statuses(tmp_path, [closure, notice_row]) == ["fix_pushed"]


def test_non_terminal_closure_status_with_exact_descriptor_closes_nothing(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row), status="info"))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


# --- registered identity ----------------------------------------------------


@pytest.mark.parametrize(
    ("target_uuid", "closure_uuid"),
    [
        pytest.param(UUIDS[FABLE], "", id="closure_uuid_missing"),
        pytest.param(UUIDS[FABLE], UUIDS[LEAD], id="closure_uuid_foreign"),
        pytest.param("", UUIDS[FABLE], id="target_uuid_missing"),
        pytest.param(UUIDS[LEAD], UUIDS[FABLE], id="target_uuid_foreign"),
    ],
)
def test_unregistered_identity_closes_nothing(
    tmp_path: Path, target_uuid: str, closure_uuid: str
) -> None:
    notice = _notice(V2_TS, "fix_pushed", agent_uuid=target_uuid)
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row), agent_uuid=closure_uuid))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]
    assert "withdrawal_identity_unverified" in _reasons(_report(tmp_path / "r", [notice_row, closure]))


def test_missing_identity_registry_closes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bridge_next_action, "_load_withdrawal_identity_registry", lambda: {})
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


def test_unreadable_identity_registry_closes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(**_: Any) -> dict[str, str]:
        raise ValueError("registry: invalid JSON")

    assert _REAL_REGISTRY_LOADER is not None
    monkeypatch.setattr(bridge_next_action, "load_bridge_identity_registry", broken)
    monkeypatch.setattr(bridge_next_action, "_load_withdrawal_identity_registry", _REAL_REGISTRY_LOADER)
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["fix_pushed"]


# --- uniqueness and side table ----------------------------------------------


def test_byte_identical_duplicate_target_rows_close_nothing(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))
    rows = [notice_row, notice_row, closure]

    assert _open_statuses(tmp_path, rows) == ["fix_pushed", "fix_pushed"]
    assert "withdrawal_duplicate" in _reasons(_report(tmp_path / "r", rows))


def test_events_without_reader_side_table_are_unverifiable(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _withdrawal(_descriptor(notice, notice_row))
    plain = [json.loads(notice_row), closure]

    assert [e["status"] for e in _open_requests_for_agent(agent=LEAD, events=plain)] == ["fix_pushed"]
    report = recommend_next_action(
        agent=LEAD,
        events=plain,
        claims=[],
        now_utc=datetime.fromisoformat("2026-10-06T18:20:00+00:00"),
    )
    assert "withdrawal_unverifiable" in _reasons(report)


def test_copied_event_list_loses_side_table_and_fails_closed(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))
    events = list(read_events(_write(tmp_path, [notice_row, closure])))

    assert [e["status"] for e in _open_requests_for_agent(agent=LEAD, events=events)] == ["fix_pushed"]


# --- malformed descriptors --------------------------------------------------


@pytest.mark.parametrize(
    "withdraws",
    [
        pytest.param("not-an-object", id="non_object"),
        pytest.param(None, id="present_null"),
        pytest.param([], id="list"),
        pytest.param("missing_ts", id="missing_ts"),
        pytest.param("upper_hex", id="uppercase_hex"),
        pytest.param("short_hex", id="short_hex"),
        pytest.param("bad_ts", id="unparseable_ts"),
        pytest.param("naive_ts", id="naive_ts"),
        pytest.param("int_status", id="non_string_field"),
    ],
)
def test_malformed_descriptor_closes_nothing(tmp_path: Path, withdraws: Any) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    exact = _descriptor(notice, notice_row)
    mutations = {
        "missing_ts": {k: v for k, v in exact.items() if k != "ts_utc"},
        "upper_hex": {**exact, "raw_line_sha256": exact["raw_line_sha256"].upper()},
        "short_hex": {**exact, "raw_line_sha256": exact["raw_line_sha256"][:63]},
        "bad_ts": {**exact, "ts_utc": "yesterday"},
        "naive_ts": {**exact, "ts_utc": "2026-10-06T17:29:29.9136087"},
        "int_status": {**exact, "status": 7},
    }
    value = mutations.get(withdraws, withdraws) if isinstance(withdraws, str) else withdraws
    closure = _row(_withdrawal(value))
    rows = [notice_row, closure]

    assert _open_statuses(tmp_path, rows) == ["fix_pushed"]
    assert "malformed_withdrawal" in _reasons(_report(tmp_path / "r", rows))


def test_top_level_and_payload_withdraws_conflict_is_malformed(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    exact = _descriptor(notice, notice_row)
    closure = _row(_withdrawal(exact, withdraws={**exact, "status": "requested"}))
    rows = [notice_row, closure]

    assert _open_statuses(tmp_path, rows) == ["fix_pushed"]
    assert "malformed_withdrawal" in _reasons(_report(tmp_path / "r", rows))


def test_identical_top_level_and_payload_withdraws_is_exact(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    notice_row = _row(notice)
    exact = _descriptor(notice, notice_row)
    closure = _row(_withdrawal(exact, withdraws=dict(exact)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == []


# --- bound requests and legacy behaviour unchanged ---------------------------


def test_withdraws_never_closes_a_request_id_request(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "requested", payload={"request_id": "req-1"})
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row)))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["requested"]


@pytest.mark.parametrize(
    ("overrides", "expected_open"),
    [
        pytest.param({}, [], id="exact"),
        pytest.param({"ts_utc": "2026-10-06T16:00:00Z"}, ["rco_pass_requested"], id="ts_mismatch"),
    ],
)
def test_direct_rco_pass_request_uses_exact_withdrawal_only(
    tmp_path: Path, overrides: dict[str, Any], expected_open: list[str]
) -> None:
    rco = "claude-rco-1"
    notice = _notice(V2_TS, "rco_pass_requested", agent=LEAD, agent_uuid=UUIDS[LEAD], to=rco)
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row, **overrides), agent=LEAD, to=rco))

    assert _open_statuses(tmp_path, [notice_row, closure], agent=rco) == expected_open


IDLE_REQUEST = {"protocol_version": "idle-protocol.v1", "proposal_id": "p-1"}
IDLE_RESPONSE = {"protocol_version": "idle-protocol.v1", "responds_to": "p-1"}


@pytest.mark.parametrize(
    ("closure_agent", "descriptor_overrides", "expected_open"),
    [
        pytest.param(FABLE, {"ts_utc": "2026-10-06T16:00:00Z"}, ["proposal"], id="owner_ts_mismatch"),
        pytest.param(FABLE, {"raw_line_sha256": "0" * 64}, ["proposal"], id="owner_hash_mismatch"),
        pytest.param(TOOLS, {"ts_utc": "2026-10-06T16:00:00Z"}, ["proposal"], id="peer_mismatch"),
        pytest.param(FABLE, {}, [], id="owner_exact"),
    ],
)
def test_idle_protocol_progress_never_closes_with_a_withdraws_member(
    tmp_path: Path,
    closure_agent: str,
    descriptor_overrides: dict[str, Any],
    expected_open: list[str],
) -> None:
    notice = _notice(V2_TS, "proposal", payload=dict(IDLE_REQUEST))
    notice_row = _row(notice)
    closure = _withdrawal(_descriptor(notice, notice_row, **descriptor_overrides), agent=closure_agent)
    closure["payload"].update(IDLE_RESPONSE)

    assert _open_statuses(tmp_path, [notice_row, _row(closure)]) == expected_open


def test_idle_protocol_progress_without_withdraws_unchanged(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "proposal", payload=dict(IDLE_REQUEST))
    progress = {
        "ts_utc": CLOSE_TS,
        "agent": TOOLS,
        "agent_uuid": UUIDS[TOOLS],
        "to": FABLE,
        "type": "message",
        "task_id": TASK,
        "status": "info",
        "message": "idle progress",
        "payload": dict(IDLE_RESPONSE),
    }

    assert _open_statuses(tmp_path, [_row(notice), _row(progress)]) == []


def test_withdraws_never_closes_a_control_signal(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "changes_requested", type="decision")
    notice_row = _row(notice)
    closure = _row(_withdrawal(_descriptor(notice, notice_row), type="decision", status="withdrawn"))

    assert _open_statuses(tmp_path, [notice_row, closure]) == ["changes_requested"]


def test_bound_request_still_closes_by_its_exact_reply(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "requested", payload={"request_id": "req-1"})
    reply = {
        "ts_utc": CLOSE_TS,
        "agent": LEAD,
        "agent_uuid": UUIDS[LEAD],
        "to": FABLE,
        "type": "message",
        "task_id": TASK,
        "status": "answered",
        "message": "done",
        "payload": {
            "in_reply_to_request_id": "req-1",
            "in_reply_to_requester": {"agent": FABLE, "agent_uuid": UUIDS[FABLE]},
        },
    }

    assert _open_statuses(tmp_path, [_row(notice), _row(reply)]) == []


def test_legacy_single_version_closure_without_withdraws_unchanged(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    closure = _withdrawal(None)
    del closure["payload"]

    assert _open_statuses(tmp_path, [_row(notice), _row(closure)]) == []


def test_legacy_two_versions_closure_without_withdraws_unchanged(tmp_path: Path) -> None:
    v1, v2 = _notice(V1_TS, "requested"), _notice(V2_TS, "fix_pushed")
    closure = _withdrawal(None)
    del closure["payload"]

    assert _open_statuses(tmp_path, [_row(v1), _row(v2), _row(closure)]) == ["requested", "fix_pushed"]


def test_legacy_request_ts_correlated_closure_unchanged(tmp_path: Path) -> None:
    v1, v2 = _notice(V1_TS, "requested"), _notice(V2_TS, "fix_pushed")
    closure = _withdrawal(None)
    closure["payload"] = {"request_ts_utc": V2_TS}

    assert _open_statuses(tmp_path, [_row(v1), _row(v2), _row(closure)]) == ["requested"]


def test_unrelated_task_withdrawal_has_no_effect(tmp_path: Path) -> None:
    notice = _notice(V2_TS, "fix_pushed")
    other = _notice(V1_TS, "requested", task_id="fable-5/other-task")
    other_row = _row(other)
    closure = _row(_withdrawal(_descriptor(other, other_row), task_id="fable-5/other-task"))

    assert _open_statuses(tmp_path, [_row(notice), other_row, closure]) == ["fix_pushed"]
