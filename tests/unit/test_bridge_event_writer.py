# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import warnings

import pytest

import waggledance.core.bridge_event_writer as writer
from waggledance.core.bridge_event_writer import (
    BridgeEventMutexError,
    BridgeEventProjectionWarning,
    CanonicalAppendError,
    CanonicalAppendOutcome,
    write_bridge_event,
)


def _event(message: str = "writer test") -> dict[str, object]:
    return {
        "ts_utc": "2026-08-01T08:00:00Z",
        "agent": "codex",
        "type": "status",
        "task_id": "python-writer-test",
        "status": "audit",
        "severity": "",
        "to": "claude",
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": os.getpid(),
        "cwd": str(Path.cwd()),
        "payload": {},
    }


def _event_lines(bridge_root: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (bridge_root / "shared" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]


def test_validates_before_any_filesystem_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    invalid = _event()
    del invalid["pid"]
    mutex_calls = 0

    def unexpected_mutex(_timeout_ms: int) -> object:
        nonlocal mutex_calls
        mutex_calls += 1
        raise AssertionError("invalid event reached append mutex")

    monkeypatch.setattr(writer, "_acquire_append_mutex", unexpected_mutex)

    with pytest.raises(Exception):
        write_bridge_event(bridge_root=bridge_root, event=invalid)

    assert mutex_calls == 0
    assert not bridge_root.exists()


def test_serializes_before_any_filesystem_mutation(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    unserializable = _event()
    unserializable["payload"] = {"bad": object()}

    with pytest.raises(TypeError):
        write_bridge_event(bridge_root=bridge_root, event=unserializable)

    assert not bridge_root.exists()


def test_one_binary_canonical_append_and_projections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original_write = writer._write_once

    def counted_write(fd: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        return original_write(fd, payload)

    monkeypatch.setattr(writer, "_write_once", counted_write)
    bridge_root = tmp_path / "bridge"

    result = write_bridge_event(bridge_root=bridge_root, event=_event())

    assert calls == 1
    assert result.warnings == ()
    assert _event_lines(bridge_root) == [_event()]
    assert json.loads(result.outbox_path.read_text(encoding="utf-8")) == _event()
    assert json.loads(result.last_agent_path.read_text(encoding="utf-8")) == _event()


def test_unterminated_canonical_tail_is_rejected_without_mutation_or_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    original = b'{"existing":true}'
    events_path.write_bytes(original)
    write_calls = 0

    def unexpected_write(fd: int, payload: bytes) -> int:
        nonlocal write_calls
        write_calls += 1
        return os.write(fd, payload)

    monkeypatch.setattr(writer, "_write_once", unexpected_write)

    with pytest.raises(OSError, match="canonical event log does not end with LF"):
        write_bridge_event(bridge_root=bridge_root, event=_event())

    assert write_calls == 0
    assert events_path.read_bytes() == original
    assert not (bridge_root / "outbox").exists()
    assert not (bridge_root / "shared" / "last_codex.json").exists()


def test_partial_append_is_rolled_back_once_without_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    prefix = b'{"existing":true}\n'
    events_path.write_bytes(prefix)
    calls = 0

    def partial_write(fd: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        return os.write(fd, payload[:7])

    monkeypatch.setattr(writer, "_write_once", partial_write)

    with pytest.raises(CanonicalAppendError) as excinfo:
        write_bridge_event(bridge_root=bridge_root, event=_event())

    assert calls == 1
    assert excinfo.value.outcome is CanonicalAppendOutcome.ROLLED_BACK
    assert events_path.read_bytes() == prefix
    assert not (bridge_root / "outbox").exists()
    assert not (bridge_root / "shared" / "last_codex.json").exists()


def test_failed_rollback_reports_ambiguous_and_never_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"
    calls = 0

    def partial_write(fd: int, payload: bytes) -> int:
        nonlocal calls
        calls += 1
        return os.write(fd, payload[:5])

    def failed_truncate(fd: int, length: int) -> None:
        raise OSError(f"injected rollback failure for {fd} at {length}")

    monkeypatch.setattr(writer, "_write_once", partial_write)
    monkeypatch.setattr(writer, "_truncate", failed_truncate)

    with pytest.raises(CanonicalAppendError) as excinfo:
        write_bridge_event(bridge_root=bridge_root, event=_event())

    assert calls == 1
    assert excinfo.value.outcome is CanonicalAppendOutcome.AMBIGUOUS
    assert excinfo.value.rollback_error is not None
    assert (bridge_root / "shared" / "events.jsonl").stat().st_size == 5
    assert not (bridge_root / "outbox").exists()


def test_postcommit_handle_finalizer_failure_is_success_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_close = writer._close_canonical

    def close_then_fail(fd: int) -> None:
        original_close(fd)
        raise OSError("injected canonical handle finalizer failure")

    monkeypatch.setattr(writer, "_close_canonical", close_then_fail)
    bridge_root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="handle finalization failed"):
        result = write_bridge_event(bridge_root=bridge_root, event=_event())

    assert len(_event_lines(bridge_root)) == 1
    assert len(result.finalization_warnings) == 1
    assert "no retry is permitted" in result.finalization_warnings[0]


def test_postcommit_mutex_finalizer_failure_is_success_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedFinalizerLease:
        def release(self) -> None:
            raise OSError("injected mutex finalizer failure")

    monkeypatch.setattr(
        writer,
        "_acquire_append_mutex",
        lambda _timeout_ms: FailedFinalizerLease(),
    )
    bridge_root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="mutex finalization failed"):
        result = write_bridge_event(bridge_root=bridge_root, event=_event())

    assert len(_event_lines(bridge_root)) == 1
    assert len(result.finalization_warnings) == 1
    assert "no retry is permitted" in result.finalization_warnings[0]


def test_projection_failures_warn_accumulate_and_later_projection_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"

    def fail_outbox(path: Path, payload: bytes) -> None:
        raise PermissionError(f"injected outbox failure at {path}")

    monkeypatch.setattr(writer, "_append_projection", fail_outbox)

    with pytest.warns(BridgeEventProjectionWarning, match="outbox projection failed"):
        result = write_bridge_event(bridge_root=bridge_root, event=_event())

    assert len(_event_lines(bridge_root)) == 1
    assert not result.outbox_path.exists()
    assert json.loads(result.last_agent_path.read_text(encoding="utf-8")) == _event()
    assert len(result.projection_warnings) == 1


def test_both_projection_failures_are_nonfatal_even_when_warnings_are_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bridge_root = tmp_path / "bridge"

    def fail_outbox(path: Path, payload: bytes) -> None:
        raise PermissionError("injected outbox failure")

    def fail_last(path: Path, event: object) -> None:
        raise PermissionError("injected last-agent failure")

    monkeypatch.setattr(writer, "_append_projection", fail_outbox)
    monkeypatch.setattr(writer, "_write_last_agent_projection", fail_last)

    with pytest.warns(BridgeEventProjectionWarning) as warning_records:
        result = write_bridge_event(bridge_root=bridge_root, event=_event())

    assert len(warning_records) == 2
    assert len(result.projection_warnings) == 2
    assert len(_event_lines(bridge_root)) == 1

    with warnings.catch_warnings():
        warnings.simplefilter("error", BridgeEventProjectionWarning)
        second = write_bridge_event(bridge_root=bridge_root, event=_event("second"))
    assert len(second.projection_warnings) == 2
    assert len(_event_lines(bridge_root)) == 2

@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex contract")
@pytest.mark.parametrize("failure", ("create", "wait", "timeout"))
def test_windows_mutex_create_and_wait_fail_closed_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    bridge_root = tmp_path / "bridge"

    class FakeKernel32:
        def CreateMutexW(self, *_args: object) -> int:
            return 0 if failure == "create" else 123

        def WaitForSingleObject(self, *_args: object) -> int:
            return 0xFFFFFFFF if failure == "wait" else 0x00000102

        def CloseHandle(self, _handle: int) -> int:
            return 1

    monkeypatch.setattr(writer, "_windows_kernel32", lambda: FakeKernel32())

    with pytest.raises(BridgeEventMutexError):
        write_bridge_event(bridge_root=bridge_root, event=_event())

    assert not bridge_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex contract")
def test_windows_named_mutex_actually_blocks_writer_until_release(tmp_path: Path) -> None:
    lease = writer._acquire_append_mutex(1_000)
    bridge_root = tmp_path / "bridge"
    started = threading.Event()
    finished = threading.Event()
    failures: list[BaseException] = []

    def run_writer() -> None:
        started.set()
        try:
            write_bridge_event(bridge_root=bridge_root, event=_event())
        except BaseException as exc:
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=run_writer, daemon=True)
    thread.start()
    released = False
    try:
        assert started.wait(1)
        assert not finished.wait(0.15)
        assert not bridge_root.exists()
        lease.release()
        released = True
        assert finished.wait(5)
    finally:
        if not released:
            lease.release()
        thread.join(timeout=5)

    assert failures == []
    assert len(_event_lines(bridge_root)) == 1


def test_precommit_rejection_happens_under_lock_before_mutation(tmp_path: Path) -> None:
    bridge_root = tmp_path / "bridge"
    observed: list[Path] = []

    def reject(events_path: Path) -> None:
        observed.append(events_path)
        raise LookupError("state changed before commit")

    with pytest.raises(LookupError, match="state changed"):
        write_bridge_event(bridge_root=bridge_root, event=_event(), precommit=reject)

    assert observed == [bridge_root / "shared" / "events.jsonl"]
    assert not bridge_root.exists()
