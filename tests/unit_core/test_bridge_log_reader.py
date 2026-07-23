from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from waggledance.core import bridge_log_reader as reader
from waggledance.core.bridge_log_reader import (
    BridgeCursor,
    BridgeReadStatus,
    read_bridge_log,
    read_bridge_log_tail_lines,
)


CORPUS_PATH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "bridge_snapshot_delta_v1.json"
)
REQUIRED_ADVERSARIAL_CASES = frozenset(
    {
        "non_finite_overflow_blocks",
        "safe_integer_positive_boundary_is_accepted",
        "safe_integer_negative_boundary_is_accepted",
        "positive_integer_just_outside_blocks",
        "negative_integer_just_outside_blocks",
        "nested_unsafe_integer_blocks",
        "hundred_digit_integer_blocks",
        "exact_duplicate_keys_block",
        "case_colliding_keys_block",
        "single_non_ascii_key_blocks",
        "nested_non_ascii_keys_block",
        "escaped_high_surrogate_blocks",
        "escaped_low_surrogate_blocks",
        "escaped_surrogate_pair_blocks",
        "literal_escaped_backslash_is_accepted",
        "direct_supplementary_utf8_is_accepted",
        "nesting_depth_32_is_accepted",
        "nesting_depth_33_blocks",
    }
)


def _identity(path: Path) -> str:
    with reader._open_log(path) as stream:
        return reader._file_identity(stream)


def _corpus_cases() -> list[dict[str, object]]:
    document = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    cases = document["cases"]
    assert isinstance(cases, list) and cases
    names = [case["name"] for case in cases]
    assert len(names) == len(set(names)), "shared corpus case names must be unique"
    declared = document.get("required_adversarial_cases")
    assert isinstance(declared, list)
    assert len(declared) == len(set(declared)), "required case names must be unique"
    assert set(declared) == REQUIRED_ADVERSARIAL_CASES
    assert REQUIRED_ADVERSARIAL_CASES <= set(names)
    return cases


CORPUS_CASES = _corpus_cases()


@pytest.mark.parametrize("case", CORPUS_CASES, ids=lambda case: case["name"])
def test_shared_snapshot_delta_conformance_corpus(
    tmp_path: Path, case: dict[str, object]
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(base64.b64decode(str(case["data_b64"]), validate=True))

    generation_path = None
    if "generation_document_b64" in case:
        generation_path = tmp_path / "events.generation.json"
        generation_path.write_bytes(
            base64.b64decode(str(case["generation_document_b64"]), validate=True)
        )

    cursor = None
    if "cursor_offset" in case:
        identity = (
            _identity(path)
            if case.get("cursor_identity") == "actual"
            else "wrong-v1:identity"
        )
        cursor = BridgeCursor(
            offset=int(case["cursor_offset"]),
            file_identity=identity,
            generation=case.get("cursor_generation"),
        )

    result = read_bridge_log(
        path,
        cursor=cursor,
        max_bytes=int(case["max_bytes"]),
        generation_path=generation_path,
    )
    expected = case["expected"]
    assert result.status.value == expected["status"]
    assert result.reason == expected["reason"]
    assert list(result.rows) == expected["rows"]
    assert result.bytes_read == expected["bytes_read"]
    assert result.bytes_consumed == expected["bytes_consumed"]
    candidate_offset = (
        None if result.candidate_cursor is None else result.candidate_cursor.offset
    )
    assert candidate_offset == expected["candidate_offset"]
    if "candidate_generation" in expected:
        assert result.candidate_cursor is not None
        assert result.candidate_cursor.generation == expected["candidate_generation"]


def test_shared_corpus_adversarial_cases_are_collected_once() -> None:
    names = [str(case["name"]) for case in CORPUS_CASES]
    for required_name in REQUIRED_ADVERSARIAL_CASES:
        assert names.count(required_name) == 1


def test_delta_io_is_instrumented_and_independent_of_historical_prefix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    historical_row = b'{"history":true}\n'
    with path.open("wb") as stream:
        for _ in range((8 * 1024 * 1024) // len(historical_row) + 1):
            stream.write(historical_row)
    offset = path.stat().st_size
    cursor = BridgeCursor(offset=offset, file_identity=_identity(path))
    delta = b'{"task_id":"delta"}\n'
    with path.open("ab") as stream:
        stream.write(delta)

    result = read_bridge_log(path, cursor=cursor, max_bytes=4096)

    assert result.status is BridgeReadStatus.OK
    assert result.rows == ({"task_id": "delta"},)
    assert result.bytes_read == len(delta) + 1
    assert result.bytes_consumed == len(delta)
    assert result.read_calls == 2
    assert result.requested_offset == offset
    assert result.snapshot_length == offset + len(delta)


def test_partial_record_cursor_stays_put_then_advances_when_lf_arrives(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"task_id":"partial"')

    partial = read_bridge_log(path, max_bytes=1024)
    assert partial.status is BridgeReadStatus.IDLE
    assert partial.candidate_cursor is not None
    assert partial.candidate_cursor.offset == 0

    with path.open("ab") as stream:
        stream.write(b'}\n')
    complete = read_bridge_log(path, cursor=partial.candidate_cursor, max_bytes=1024)
    assert complete.status is BridgeReadStatus.OK
    assert complete.rows == ({"task_id": "partial"},)
    assert complete.candidate_cursor is not None
    assert complete.candidate_cursor.offset == path.stat().st_size


def test_generation_change_during_read_retries_without_rows_or_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_text('{"x":1}\n', encoding="utf-8", newline="")
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
    seen = iter(("g1", "g2"))
    monkeypatch.setattr(reader, "_read_generation", lambda _path: next(seen))

    result = read_bridge_log(path, generation_path=generation_path)

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_changed_during_read"
    assert result.rows == ()
    assert result.candidate_cursor is None
    assert result.bytes_read == len(b'{"x":1}\n')


def test_generation_instability_supersedes_row_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_bytes(b"[]\n")
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
    seen = iter(("g1", "g2"))
    monkeypatch.setattr(reader, "_read_generation", lambda _path: next(seen))

    result = read_bridge_log(path, generation_path=generation_path)

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_changed_during_read"
    assert result.rows == ()
    assert result.candidate_cursor is None


def test_atomic_replacement_is_rejected_by_handle_identity(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"old":1}\n', encoding="utf-8", newline="")
    cursor = BridgeCursor(path.stat().st_size, _identity(path))
    replacement = tmp_path / "replacement.jsonl"
    replacement.write_text('{"new":1}\n', encoding="utf-8", newline="")
    os.replace(replacement, path)

    result = read_bridge_log(path, cursor=cursor)

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "file_identity_changed"
    assert result.rows == ()
    assert result.candidate_cursor is None
    assert cursor.offset == len(b'{"old":1}\n')


def test_open_reader_permits_atomic_replacement(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    replacement = tmp_path / "replacement.jsonl"
    rotated = tmp_path / "events.rotated.jsonl"
    path.write_bytes(b'{"old":1}\n')
    replacement.write_bytes(b'{"new":1}\n')

    stream = reader._open_log(path)
    try:
        old_identity = reader._file_identity(stream)
        os.replace(path, rotated)
        os.replace(replacement, path)
        assert reader._file_identity(stream) == old_identity
        assert _identity(path) != old_identity
    finally:
        stream.close()


def test_delta_cursor_must_follow_lf(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"first":1}\n{"second":2}\n')
    cursor = BridgeCursor(offset=5, file_identity=_identity(path))

    result = read_bridge_log(path, cursor=cursor)

    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "cursor_not_lf_boundary"
    assert result.bytes_read == 1
    assert result.candidate_cursor is None
    assert result.rows == ()


def test_log_is_opened_once_and_identity_comes_from_that_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"x":1}\n', encoding="utf-8", newline="")
    original_open = reader._open_log
    opened = []

    def counted_open(candidate: Path):
        opened.append(candidate)
        return original_open(candidate)

    monkeypatch.setattr(reader, "_open_log", counted_open)
    result = read_bridge_log(path)

    assert result.status is BridgeReadStatus.OK
    assert opened == [path]


def test_file_identity_is_rechecked_after_read_and_change_is_cursor_neutral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"x":1}\n', encoding="utf-8", newline="")
    stable_identity = _identity(path)
    identities = iter((stable_identity, stable_identity + "-changed"))
    monkeypatch.setattr(reader, "_file_identity", lambda _stream: next(identities))

    result = read_bridge_log(path)

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "file_identity_changed_during_read"
    assert result.rows == ()
    assert result.candidate_cursor is None
    assert result.bytes_read == len(b'{"x":1}\n')


def test_failures_never_offer_a_candidate_cursor(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"x":"\xff"}\n')
    original = BridgeCursor(offset=0, file_identity=_identity(path))

    blocked = read_bridge_log(path, cursor=original)

    assert blocked.status is BridgeReadStatus.BLOCKED
    assert blocked.candidate_cursor is None
    assert blocked.rows == ()
    assert original.offset == 0


def test_missing_log_is_idle_only_for_initial_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "missing.jsonl"
    snapshot = read_bridge_log(path)
    delta = read_bridge_log(path, cursor=BridgeCursor(0, "windows-v1:1:2"))

    assert (snapshot.status, snapshot.reason) == (
        BridgeReadStatus.IDLE,
        "log_missing",
    )
    assert (delta.status, delta.reason) == (
        BridgeReadStatus.RETRY,
        "log_disappeared",
    )
    assert snapshot.candidate_cursor is None
    assert delta.candidate_cursor is None


def test_generation_configuration_cannot_silently_disappear(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"x":1}\n', encoding="utf-8", newline="")
    cursor = BridgeCursor(0, _identity(path), "g1")

    result = read_bridge_log(path, cursor=cursor)

    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "generation_configuration_changed"
    assert result.candidate_cursor is None


def test_generation_document_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_bytes(b'{"x":1}\n')
    generation_path.write_bytes(b" " * 513)

    result = read_bridge_log(path, generation_path=generation_path)

    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "generation_invalid"
    assert result.bytes_read == 0
    assert result.candidate_cursor is None


@pytest.mark.parametrize(
    "max_bytes",
    (0, -1, True, 1.5, "8", reader.MAX_MAX_BYTES + 1),
)
def test_invalid_max_bytes_blocks_without_io(
    tmp_path: Path, max_bytes: object
) -> None:
    result = read_bridge_log(tmp_path / "missing.jsonl", max_bytes=max_bytes)  # type: ignore[arg-type]
    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "max_bytes_invalid"
    assert result.candidate_cursor is None


def test_cursor_is_immutable() -> None:
    cursor = BridgeCursor(1, "identity")
    with pytest.raises((AttributeError, TypeError)):
        cursor.offset = 2  # type: ignore[misc]


def test_max_rows_chunks_are_bounded_and_delivered_exactly_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n{"row":2}\n{"row":3}\n')

    first = read_bridge_log(path, max_rows=2)
    assert first.status is BridgeReadStatus.OK
    assert first.rows == ({"row": 1}, {"row": 2})
    assert first.candidate_cursor is not None

    second = read_bridge_log(path, cursor=first.candidate_cursor, max_rows=2)
    assert second.status is BridgeReadStatus.OK
    assert second.rows == ({"row": 3},)
    assert second.candidate_cursor is not None

    third = read_bridge_log(path, cursor=second.candidate_cursor, max_rows=2)
    assert third.status is BridgeReadStatus.IDLE
    assert third.rows == ()
    assert third.candidate_cursor == second.candidate_cursor


def test_independent_readers_do_not_share_cursor_state(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n{"row":2}\n')

    left = read_bridge_log(path, max_rows=1)
    right = read_bridge_log(path, max_rows=1)

    assert left.rows == right.rows == ({"row": 1},)
    assert left.candidate_cursor == right.candidate_cursor
    assert left.candidate_cursor is not None
    left_next = read_bridge_log(path, cursor=left.candidate_cursor, max_rows=1)
    assert left_next.rows == ({"row": 2},)
    assert right.rows == ({"row": 1},)


def test_larger_replacement_with_fewer_rows_is_not_treated_as_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n{"row":2}\n')
    initial = read_bridge_log(path)
    assert initial.candidate_cursor is not None

    replacement = tmp_path / "replacement.jsonl"
    replacement.write_bytes(b'{"replacement":"' + (b"x" * 256) + b'"}\n')
    os.replace(replacement, path)
    result = read_bridge_log(path, cursor=initial.candidate_cursor)

    assert path.stat().st_size > initial.candidate_cursor.offset
    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "file_identity_changed"
    assert result.rows == ()
    assert result.candidate_cursor is None


def test_tail_lines_are_row_and_byte_bounded(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n{"row":2}\n{"row":3}\n')

    result = read_bridge_log_tail_lines(path, tail_rows=2, max_bytes=128)

    assert result.status is BridgeReadStatus.OK
    assert result.lines == ('{"row":2}', '{"row":3}')
    assert result.bytes_read <= 128


def test_tail_lines_ignore_unterminated_final_fragment(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n{"row":2}')

    result = read_bridge_log_tail_lines(path, tail_rows=2)

    assert result.status is BridgeReadStatus.OK
    assert result.lines == ('{"row":1}',)


def test_tail_lines_block_partial_only_bounded_window(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n' + (b"x" * 40))

    result = read_bridge_log_tail_lines(path, tail_rows=1, max_bytes=16)

    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "tail_exceeds_max_bytes"
    assert result.lines == ()


def test_tail_lines_reject_zero_row_bound_without_io(tmp_path: Path) -> None:
    result = read_bridge_log_tail_lines(
        tmp_path / "missing.jsonl", tail_rows=0
    )

    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "max_rows_invalid"
    assert result.lines == ()


def test_tail_lines_accept_exact_lf_aligned_max_byte_window(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    selected = b'{"row":2}\n{"row":3}\n'
    path.write_bytes(b'{"row":1}\n' + selected)

    result = read_bridge_log_tail_lines(
        path, tail_rows=2, max_bytes=len(selected)
    )

    assert result.status is BridgeReadStatus.OK
    assert result.lines == ('{"row":2}', '{"row":3}')
    assert result.bytes_read == len(selected)


def test_tail_lines_fail_closed_on_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":1}\n{"row":"\xff"}\n')

    result = read_bridge_log_tail_lines(path, tail_rows=2)

    assert result.status is BridgeReadStatus.BLOCKED
    assert result.reason == "invalid_utf8"
    assert result.lines == ()


def test_tail_generation_change_is_result_neutral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_bytes(b'{"row":1}\n')
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
    seen = iter(("g1", "g2"))
    monkeypatch.setattr(reader, "_read_generation", lambda _path: next(seen))

    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_changed_during_read"
    assert result.lines == ()


def test_tail_identity_change_supersedes_invalid_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"row":"\xff"}\n')
    stable_identity = _identity(path)
    identities = iter((stable_identity, stable_identity + "-changed"))
    monkeypatch.setattr(reader, "_file_identity", lambda _stream: next(identities))

    result = read_bridge_log_tail_lines(path, tail_rows=1)

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "file_identity_changed_during_read"
    assert result.lines == ()


def test_tail_identity_change_supersedes_window_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(b'{"value":"' + (b"x" * 128) + b'"}\n')
    stable_identity = _identity(path)
    identities = iter((stable_identity, stable_identity + "-changed"))
    monkeypatch.setattr(reader, "_file_identity", lambda _stream: next(identities))

    result = read_bridge_log_tail_lines(path, tail_rows=1, max_bytes=32)

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "file_identity_changed_during_read"
    assert result.lines == ()


def test_tail_generation_change_supersedes_invalid_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_bytes(b'{"row":"\xff"}\n')
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
    seen = iter(("g1", "g2"))
    monkeypatch.setattr(reader, "_read_generation", lambda _path: next(seen))

    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_changed_during_read"
    assert result.lines == ()


def test_tail_optional_generation_appearance_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_bytes(b'{"row":1}\n')
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
    original_stat = Path.stat
    checks = iter((False, True))

    def changing_stat(
        candidate: Path, *args: object, **kwargs: object
    ) -> os.stat_result:
        if candidate == generation_path:
            if not next(checks):
                raise FileNotFoundError(str(candidate))
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", changing_stat)
    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_configuration_changed"
    assert result.lines == ()


def test_tail_missing_log_generation_appearance_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"

    def missing_after_generation_appears(_path: Path) -> object:
        generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
        raise FileNotFoundError(str(path))

    monkeypatch.setattr(reader, "_open_log", missing_after_generation_appears)
    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_configuration_changed"
    assert result.lines == ()


def test_tail_missing_log_generation_token_change_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")

    def missing_after_generation_changes(_path: Path) -> object:
        generation_path.write_text('{"generation":"g2"}', encoding="utf-8")
        raise FileNotFoundError(str(path))

    monkeypatch.setattr(reader, "_open_log", missing_after_generation_changes)
    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_changed_during_read"
    assert result.lines == ()


def test_tail_unavailable_log_generation_appearance_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"

    def unavailable_after_generation_appears(_path: Path) -> object:
        generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
        raise PermissionError(str(path))

    monkeypatch.setattr(reader, "_open_log", unavailable_after_generation_appears)
    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_configuration_changed"
    assert result.lines == ()


@pytest.mark.parametrize("failure_phase", ("before", "after"))
def test_tail_generation_existence_oserror_is_structured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
) -> None:
    path = tmp_path / "events.jsonl"
    generation_path = tmp_path / "events.generation.json"
    path.write_bytes(b'{"row":1}\n')
    generation_path.write_text('{"generation":"g1"}', encoding="utf-8")
    original_stat = Path.stat
    generation_stat_calls = 0

    def failing_stat(candidate: Path, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal generation_stat_calls
        if candidate == generation_path:
            generation_stat_calls += 1
            if failure_phase == "before" or generation_stat_calls == 2:
                raise PermissionError("generation existence probe denied")
        return original_stat(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    result = read_bridge_log_tail_lines(
        path, tail_rows=1, generation_path=generation_path
    )

    assert result.status is BridgeReadStatus.RETRY
    assert result.reason == "generation_unavailable"
    assert result.lines == ()


@pytest.mark.parametrize(
    "raw",
    (
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"key":1,"key":2}',
        '{"Key":1,"key":2}',
        '{"value":9007199254740992}',
        '{"nonascii_å":1}',
        ("[" * 33) + "0" + ("]" * 33),
    ),
)
def test_public_bridge_object_parser_enforces_canonical_contract(
    raw: str,
) -> None:
    with pytest.raises(ValueError):
        reader.parse_bridge_json_object(raw)
