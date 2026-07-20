# SPDX-License-Identifier: Apache-2.0
"""Focused AppendV1 tests for the direct Python bridge writer."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

import tools.bridge_event_writer as bridge_writer
from tools.bridge_event_writer import (
    APPEND_MUTEX_NAME,
    APPEND_MUTEX_NAMES,
    APPEND_MUTEX_TIMEOUT_MS,
    APPEND_V1_MUTEX_NAME,
    APPEND_V2_MUTEX_NAME,
    CHECKPOINT_SUFFIX,
    BridgeEventWriteError,
    WindowsAppendV1Backend,
    _PortableTestBackend,
    _acquire_mutex_bundle,
    _checkpoint_bytes,
    _close_mutex_bundle,
    write_bridge_event,
)


def _event(index: int = 1, *, agent: str = "codex") -> dict[str, object]:
    return {
        "ts_utc": f"2026-07-20T12:00:{index:02d}Z",
        "agent": agent,
        "type": "message",
        "task_id": f"python-append-v1-{index}",
        "status": "note",
        "severity": "",
        "to": "claude",
        "message": f"strict UTF-8 handoff {index}: ääkkönen",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2",
        "payload": {"index": index},
    }


def _canonical(root: Path) -> Path:
    return root / "shared" / "events.jsonl"


def _rows(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_lexical_guard_refuses_custom_target_before_creation(tmp_path: Path) -> None:
    root = tmp_path / "bridge-never-created"
    custom = tmp_path / "custom-events.jsonl"

    with pytest.raises(BridgeEventWriteError, match="non-canonical"):
        write_bridge_event(
            bridge_root=root,
            events_path=custom,
            event=_event(),
            backend=_PortableTestBackend(),
        )

    assert not root.exists()
    assert not custom.exists()


@pytest.mark.parametrize(
    ("case", "updates", "missing"),
    [
        ("invalid-agent", {"agent": "Codex"}, None),
        ("unknown-type", {"type": "custom"}, None),
        ("non-string-type", {"type": 7}, None),
        ("non-string-agent", {"agent": 7}, None),
        ("non-string-task-id", {"task_id": None}, None),
        ("non-string-status", {"status": []}, None),
        ("missing-agent", {}, "agent"),
        ("missing-type", {}, "type"),
        ("missing-task-id", {}, "task_id"),
        ("missing-status", {}, "status"),
    ],
)
def test_invalid_replayer_shape_refuses_before_root_or_wal_creation(
    tmp_path: Path,
    case: str,
    updates: dict[str, object],
    missing: str | None,
) -> None:
    del case
    root = tmp_path / "bridge-never-created"
    event = _event()
    event.update(updates)
    if missing is not None:
        event.pop(missing)

    with pytest.raises(BridgeEventWriteError):
        write_bridge_event(
            bridge_root=root,
            event=event,
            backend=_PortableTestBackend(),
        )

    assert not root.exists()
    assert not _canonical(root).exists()


@pytest.mark.skipif(os.name == "nt", reason="production backend is supported on Windows")
def test_production_backend_fails_closed_off_windows_before_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge-never-created"

    with pytest.raises(BridgeEventWriteError, match="require Windows"):
        write_bridge_event(bridge_root=root, event=_event())

    assert not root.exists()


class _WalObservationBackend(_PortableTestBackend):
    saw_durable_pending_before_wait = False

    def acquire_mutex(self, name: str, timeout_ms: int):
        if self.mutex_acquisitions == 0:
            pending = list(self._root.glob("spool/.*.pending"))
            assert len(pending) == 1
            assert pending[0].read_bytes().endswith(b"\n")
            self.saw_durable_pending_before_wait = True
        return super().acquire_mutex(name, timeout_ms)


def test_pending_wal_is_durable_before_wait_and_clean_success_removes_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    backend = _WalObservationBackend()
    backend._root = root

    result = write_bridge_event(
        bridge_root=root,
        event=_event(),
        backend=backend,
    )

    assert backend.saw_durable_pending_before_wait is True
    assert [name for name, _timeout in backend.mutex_requests] == [
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
    ]
    assert all(
        0 <= timeout <= APPEND_MUTEX_TIMEOUT_MS
        for _name, timeout in backend.mutex_requests
    )
    assert backend.mutex_releases == [
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
    ]
    assert APPEND_MUTEX_NAMES == (APPEND_V1_MUTEX_NAME, APPEND_V2_MUTEX_NAME)
    assert APPEND_MUTEX_NAME == r"Global\WaggleDanceBridgeAppendV1"
    assert APPEND_V1_MUTEX_NAME == r"Global\WaggleDanceBridgeAppendV1"
    assert APPEND_V2_MUTEX_NAME == r"Global\WaggleDanceBridgeAppendV2"
    assert APPEND_MUTEX_TIMEOUT_MS == 10_000
    assert result.canonical_durable is True
    assert result.checkpoint_advanced is True
    assert result.retained_wal_path is None
    assert list((root / "spool").iterdir()) == []
    assert _rows(_canonical(root)) == [_event()]
    raw = _canonical(root).read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    assert "ääkkönen".encode() in raw
    outbox = root / "outbox" / "codex" / "2026-07-20.jsonl"
    assert not Path(os.fspath(outbox) + CHECKPOINT_SUFFIX).exists()


def test_mutex_bundle_uses_one_monotonic_budget_and_releases_reverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter([1_000_000_000, 1_000_000_000, 3_500_000_000])
    monkeypatch.setattr(bridge_writer.time, "monotonic_ns", lambda: next(ticks))
    backend = _PortableTestBackend()

    bundle = _acquire_mutex_bundle(backend=backend)
    _close_mutex_bundle(bundle, warning_messages=[], context="test")

    assert backend.mutex_requests == [
        (APPEND_V1_MUTEX_NAME, 10_000),
        (APPEND_V2_MUTEX_NAME, 7_500),
    ]
    assert backend.mutex_releases == [
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
    ]
    assert backend.mutex_closes == [
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
    ]


@pytest.mark.parametrize(
    "outcome", ["error", "timeout", "abandoned", "unexpected"]
)
def test_v2_failure_releases_v1_promotes_wal_and_never_mutates_canonical(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / outcome
    backend = _PortableTestBackend(mutex_outcomes=["clean", outcome])

    with pytest.raises(BridgeEventWriteError) as excinfo:
        write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=backend,
        )

    assert not _canonical(root).exists()
    assert not (root / "shared").exists()
    assert excinfo.value.wal_path is not None
    assert excinfo.value.wal_path.read_bytes().endswith(b"\n")
    assert [name for name, _timeout in backend.mutex_requests] == [
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
    ]
    assert all(0 <= timeout <= 10_000 for _name, timeout in backend.mutex_requests)
    expected_releases = [APPEND_V1_MUTEX_NAME]
    if outcome == "abandoned":
        expected_releases.insert(0, APPEND_V2_MUTEX_NAME)
    assert backend.mutex_releases == expected_releases
    if outcome == "error":
        assert backend.mutex_closes == [APPEND_V1_MUTEX_NAME]
    else:
        assert backend.mutex_closes == [
            APPEND_V2_MUTEX_NAME,
            APPEND_V1_MUTEX_NAME,
        ]


@pytest.mark.parametrize("outcome", ["timeout", "abandoned"])
def test_unclean_mutex_never_mutates_canonical_and_promotes_wal(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend(mutex_outcomes=[outcome])

    with pytest.raises(BridgeEventWriteError) as excinfo:
        write_bridge_event(bridge_root=root, event=_event(), backend=backend)

    assert not _canonical(root).exists()
    assert not (root / "shared").exists()
    assert excinfo.value.wal_path is not None
    assert excinfo.value.wal_path.name.startswith("failed-append-")
    assert excinfo.value.wal_path.read_bytes().endswith(b"\n")
    if outcome == "abandoned":
        assert "dirty ownership" in str(excinfo.value)


def test_checkpoint_has_exact_order_schema_decimal_lengths_identity_and_tail_sha(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()

    write_bridge_event(
        bridge_root=root,
        event=_event(),
        write_sidecars=False,
        backend=backend,
    )

    events = _canonical(root)
    checkpoint = Path(os.fspath(events) + CHECKPOINT_SUFFIX)
    payload = checkpoint.read_bytes()
    parsed = json.loads(payload)
    assert list(parsed) == [
        "schema",
        "version",
        "file_identity",
        "validated_length",
        "tail_anchor_length",
        "tail_anchor_sha256",
    ]
    assert parsed["schema"] == "waggledance.bridge.append-v1-validation"
    assert parsed["version"] == 1
    assert parsed["file_identity"].startswith("test-file-id-v1:")
    assert parsed["validated_length"] == str(events.stat().st_size)
    assert parsed["tail_anchor_length"] == str(min(4096, events.stat().st_size))
    assert len(parsed["tail_anchor_sha256"]) == 64
    assert payload == _checkpoint_bytes(
        file_identity=parsed["file_identity"],
        length=int(parsed["validated_length"]),
        anchor_length=int(parsed["tail_anchor_length"]),
        anchor_sha256=parsed["tail_anchor_sha256"],
    )


def test_corrupt_checkpoint_forces_full_scan_then_is_replaced(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=backend,
    )
    checkpoint = Path(os.fspath(_canonical(root)) + CHECKPOINT_SUFFIX)
    checkpoint.write_bytes(b'{"schema":"forged"}\n')

    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=backend,
    )

    assert _rows(_canonical(root)) == [_event(1), _event(2)]
    parsed = json.loads(checkpoint.read_bytes())
    assert parsed["validated_length"] == str(_canonical(root).stat().st_size)


def _encode_checkpoint_object(value: dict[str, object]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
        + "\n"
    ).encode("utf-8")


def _mutated_checkpoint(kind: str, exact: bytes) -> bytes:
    checkpoint = json.loads(exact)
    if kind == "duplicate-key":
        return exact.replace(
            b'{"schema":',
            b'{"schema":"duplicate","schema":',
            1,
        )
    if kind == "order":
        return _encode_checkpoint_object(
            {
                "version": checkpoint["version"],
                "schema": checkpoint["schema"],
                "file_identity": checkpoint["file_identity"],
                "validated_length": checkpoint["validated_length"],
                "tail_anchor_length": checkpoint["tail_anchor_length"],
                "tail_anchor_sha256": checkpoint["tail_anchor_sha256"],
            }
        )
    if kind == "key-case":
        checkpoint["Schema"] = checkpoint.pop("schema")
        return _encode_checkpoint_object(checkpoint)
    if kind == "wrong-type":
        checkpoint["validated_length"] = int(checkpoint["validated_length"])
        return _encode_checkpoint_object(checkpoint)
    if kind == "trailing-byte":
        return exact + b"x"
    if kind == "stale-identity":
        checkpoint["file_identity"] = "test-file-id-v1:stale:identity"
        return _encode_checkpoint_object(checkpoint)
    if kind == "stale-length":
        checkpoint["validated_length"] = str(int(checkpoint["validated_length"]) + 1)
        return _encode_checkpoint_object(checkpoint)
    if kind == "stale-tail":
        checkpoint["tail_anchor_sha256"] = "0" * 64
        return _encode_checkpoint_object(checkpoint)
    raise AssertionError(f"unknown checkpoint mutation: {kind}")


@pytest.mark.parametrize(
    "kind",
    [
        "duplicate-key",
        "order",
        "key-case",
        "wrong-type",
        "trailing-byte",
        "stale-identity",
        "stale-length",
        "stale-tail",
    ],
)
def test_invalid_checkpoint_corpus_forces_exactly_one_full_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root = tmp_path / kind
    backend = _PortableTestBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=backend,
    )
    checkpoint_path = Path(os.fspath(_canonical(root)) + CHECKPOINT_SUFFIX)
    checkpoint_path.write_bytes(_mutated_checkpoint(kind, checkpoint_path.read_bytes()))

    full_scans: list[Path] = []
    original = bridge_writer._assert_strict_utf8_target

    def instrumented_full_scan(stream, path: Path, length: int) -> None:
        full_scans.append(path)
        original(stream, path, length)

    monkeypatch.setattr(
        bridge_writer,
        "_assert_strict_utf8_target",
        instrumented_full_scan,
    )

    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=backend,
    )

    assert full_scans == [_canonical(root)]
    assert _rows(_canonical(root)) == [_event(1), _event(2)]


def test_exact_checkpoint_performs_zero_full_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "exact"
    backend = _PortableTestBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=backend,
    )

    def refuse_full_scan(*_args, **_kwargs) -> None:
        raise AssertionError("exact checkpoint unexpectedly fell back to full scan")

    monkeypatch.setattr(
        bridge_writer,
        "_assert_strict_utf8_target",
        refuse_full_scan,
    )
    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=backend,
    )

    assert _rows(_canonical(root)) == [_event(1), _event(2)]


def test_full_scan_refuses_invalid_utf8_and_preserves_canonical(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    events = _canonical(root)
    events.parent.mkdir(parents=True)
    original = b'{"old":"\xff"}\n'
    events.write_bytes(original)

    with pytest.raises(BridgeEventWriteError) as excinfo:
        write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_PortableTestBackend(),
        )

    assert events.read_bytes() == original
    assert excinfo.value.wal_path is not None
    assert excinfo.value.wal_path.is_file()


class _PartialFailureFile:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.path = inner.path
        self._failed = False

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def write_at(self, offset: int, payload: bytes) -> None:
        if not self._failed:
            self._failed = True
            self._inner.write_at(offset, payload[: max(1, len(payload) // 3)])
            raise OSError("simulated partial canonical write")
        self._inner.write_at(offset, payload)


class _PartialFailureBackend(_PortableTestBackend):
    def open_or_create_shared_read(self, path: Path):
        return _PartialFailureFile(super().open_or_create_shared_read(path))


def test_preflush_partial_append_rolls_back_and_promotes_wal(tmp_path: Path) -> None:
    root = tmp_path / "bridge"

    with pytest.raises(BridgeEventWriteError) as excinfo:
        write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_PartialFailureBackend(),
        )

    assert _canonical(root).read_bytes() == b""
    assert excinfo.value.wal_path is not None
    assert excinfo.value.wal_path.is_file()


class _AfterCanonicalCheckpointFailureBackend(_PortableTestBackend):
    def __init__(self) -> None:
        super().__init__()
        self.checkpoint_replacements = 0

    def move(self, source: Path, destination: Path, *, replace: bool, write_through: bool) -> None:
        if destination.name.endswith(CHECKPOINT_SUFFIX):
            self.checkpoint_replacements += 1
            if self.checkpoint_replacements == 2:
                raise OSError("simulated postflush checkpoint failure")
        super().move(
            source,
            destination,
            replace=replace,
            write_through=write_through,
        )


def test_postflush_checkpoint_failure_returns_success_and_retains_wal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="checkpoint advance failed"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_AfterCanonicalCheckpointFailureBackend(),
        )

    assert result.canonical_durable is True
    assert result.checkpoint_advanced is False
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.is_file()
    assert _rows(_canonical(root)) == [_event()]


def test_warning_filter_cannot_turn_postflush_success_into_retryable_failure(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_AfterCanonicalCheckpointFailureBackend(),
        )

    assert result.canonical_durable is True
    assert result.checkpoint_advanced is False
    assert _rows(_canonical(root)) == [_event()]


class _WalCleanupFailureBackend(_PortableTestBackend):
    def delete(self, path: Path) -> None:
        if path.name.endswith(".pending"):
            raise OSError("simulated WAL cleanup failure")
        super().delete(path)


def test_postflush_wal_cleanup_failure_returns_success_and_retains_wal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="WAL cleanup failed"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_WalCleanupFailureBackend(),
        )

    assert result.canonical_durable is True
    assert result.retained_wal_path is not None
    assert result.retained_wal_path.is_file()
    assert _rows(_canonical(root)) == [_event()]


def test_sidecar_uses_fresh_clean_mutex_and_failure_is_best_effort(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend(
        mutex_outcomes=["clean", "clean", "clean", "abandoned"]
    )

    with pytest.warns(RuntimeWarning, match="auxiliary outbox append was skipped"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=backend,
        )

    assert result.canonical_durable is True
    assert result.outbox_written is False
    assert result.last_file_written is True
    assert backend.mutex_acquisitions == 4
    assert [name for name, _timeout in backend.mutex_requests] == [
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
    ]
    assert backend.mutex_releases == [
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
    ]
    assert not (root / "outbox").exists()
    assert json.loads((root / "shared" / "last_codex.json").read_text("utf-8")) == _event()


def test_sidecars_can_be_disabled_for_canonical_only_callers(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()

    result = write_bridge_event(
        bridge_root=root,
        event=_event(),
        write_sidecars=False,
        backend=backend,
    )

    assert result.outbox_written is False
    assert result.last_file_written is False
    assert backend.mutex_acquisitions == 2
    assert not (root / "outbox").exists()
    assert not (root / "shared" / "last_codex.json").exists()


@pytest.mark.parametrize(
    "outcome", ["error", "timeout", "abandoned", "unexpected"]
)
def test_auxiliary_v2_failure_is_best_effort_and_releases_partial_set(
    tmp_path: Path,
    outcome: str,
) -> None:
    root = tmp_path / outcome
    backend = _PortableTestBackend(
        mutex_outcomes=["clean", "clean", "clean", outcome]
    )

    with pytest.warns(RuntimeWarning, match="auxiliary outbox append was skipped"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            backend=backend,
        )

    assert result.canonical_durable is True
    assert result.outbox_written is False
    assert _rows(_canonical(root)) == [_event()]
    assert not (root / "outbox").exists()
    # Canonical V2/V1, followed by the auxiliary partial-set unwind.
    expected_tail = [APPEND_V1_MUTEX_NAME]
    if outcome == "abandoned":
        expected_tail.insert(0, APPEND_V2_MUTEX_NAME)
    assert backend.mutex_releases == [
        APPEND_V2_MUTEX_NAME,
        APPEND_V1_MUTEX_NAME,
        *expected_tail,
    ]


def test_mutex_release_failure_warns_without_negating_durable_success(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bridge"

    with pytest.warns(RuntimeWarning, match="release failed"):
        result = write_bridge_event(
            bridge_root=root,
            event=_event(),
            write_sidecars=False,
            backend=_PortableTestBackend(fail_release=True),
        )

    assert result.canonical_durable is True
    assert _rows(_canonical(root)) == [_event()]


def test_portable_injected_backend_serializes_concurrent_rows(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    backend = _PortableTestBackend()

    def emit(index: int) -> None:
        write_bridge_event(
            bridge_root=root,
            event=_event(index),
            write_sidecars=False,
            backend=backend,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(emit, range(1, 9)))

    rows = _rows(_canonical(root))
    assert len(rows) == 8
    assert {row["task_id"] for row in rows} == {f"python-append-v1-{i}" for i in range(1, 9)}


class _RecordingWindowsBackend(WindowsAppendV1Backend):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, int]] = []

    def acquire_mutex(self, name: str, timeout_ms: int):
        self.requests.append((name, timeout_ms))
        return super().acquire_mutex(name, timeout_ms)


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel32 integration probe")
def test_windows_python_and_powershell_handoff_uses_both_append_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is unavailable")
    root = tmp_path / "bridge"
    first_backend = _RecordingWindowsBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(1),
        write_sidecars=False,
        backend=first_backend,
    )
    assert [name for name, _timeout in first_backend.requests] == [
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
    ]
    script = Path(__file__).resolve().parents[2] / ".agent-bridge" / "bin" / "Write-AgentEvent.ps1"
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    env["AGENT_BRIDGE_TEST_FAIL_ON_FULL_VALIDATION"] = "1"
    completed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-File",
            str(script),
            "-Agent",
            "codex",
            "-Type",
            "message",
            "-TaskId",
            "powershell-append-v1-handoff",
            "-Status",
            "note",
            "-To",
            "claude",
            "-Message",
            "PowerShell continued the Python-created canonical stream.",
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr

    def refuse_python_full_scan(*_args, **_kwargs) -> None:
        raise AssertionError("PowerShell checkpoint was not byte-compatible with Python")

    monkeypatch.setattr(
        bridge_writer,
        "_assert_strict_utf8_target",
        refuse_python_full_scan,
    )
    return_backend = _RecordingWindowsBackend()
    write_bridge_event(
        bridge_root=root,
        event=_event(2),
        write_sidecars=False,
        backend=return_backend,
    )
    assert [name for name, _timeout in return_backend.requests] == [
        APPEND_V1_MUTEX_NAME,
        APPEND_V2_MUTEX_NAME,
    ]
    rows = _rows(_canonical(root))
    assert [row["task_id"] for row in rows] == [
        "python-append-v1-1",
        "powershell-append-v1-handoff",
        "python-append-v1-2",
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows kernel32 concurrency probe")
def test_windows_backend_serializes_two_python_writers(tmp_path: Path) -> None:
    root = tmp_path / "bridge"

    def emit(index: int) -> None:
        write_bridge_event(
            bridge_root=root,
            event=_event(index),
            write_sidecars=False,
            backend=WindowsAppendV1Backend(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(emit, [1, 2]))

    rows = _rows(_canonical(root))
    assert {row["task_id"] for row in rows} == {
        "python-append-v1-1",
        "python-append-v1-2",
    }
