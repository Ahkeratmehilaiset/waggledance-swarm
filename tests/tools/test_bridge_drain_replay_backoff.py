"""Opportunistic accepted-queue drains back off on a WAL that keeps failing.

2026-10-11: ready WAL 88DFBB16 failed targeted replay on every attempt. Every
non-Compact Read-AgentBridge, consumer loop and session start retried it, and
each retry validated the whole canonical history while holding the global
publication fence, so writers timed out ("accepted queue publication fence
timed out before WAL creation").

Opportunistic callers now pass -HonorReplayBackoff. A failed mutating replay
records an advisory memo (60 s doubling to 900 s) bound to the WAL digest and
the drain/replay code digest, outside spool/accepted-v1. While it is active an
opportunistic drain skips the WAL and still counts it as failed. Explicit
drains and DryRun ignore it, and the receipt keys stay the preflight's v1 set.

Every run uses an isolated bin with Local mutex names; the live fence, WAL and
canonical log are never touched.

Named mutants: MUT_BACKOFF_IGNORES_CODE_BINDING, MUT_BACKOFF_HONORED_WITHOUT_SWITCH,
MUT_BACKOFF_SKIP_COUNTS_AS_HEALTHY.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Callable

import pytest

from tools.bridge_accepted_queue_preflight import READY_STATUSES, TOP_LEVEL_KEYS

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge" / "bin"
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
LEAF = "bridge-wal-v1-" + "5b" * 16 + ".jsonl"
CALLERS = ("Read-AgentBridge.ps1", "Start-AgentBridgeConsumerLoop.ps1", "Start-AgentBridgeSession.ps1")
STUB_REPLAY = """param(
    [string] $BridgeRoot, [string] $AcceptedWalLeaf, [string] $ExpectedWalSha256, [switch] $DryRun
)
Add-Content -LiteralPath (Join-Path $BridgeRoot 'replay-calls.txt') -Value "$AcceptedWalLeaf dry=$($DryRun.IsPresent)"
if (Test-Path -LiteralPath (Join-Path $BridgeRoot 'replay-succeeds')) { 'spool replay complete' }
else { throw 'stub targeted replay failure' }
"""

pytestmark = pytest.mark.skipif(not SHELLS, reason="needs PowerShell (pwsh or powershell.exe)")


def _run(engine: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_"))}
    return subprocess.run(
        [engine, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        check=False, env=env,
    )


def _isolated_bin(
    tmp_path: Path,
    *,
    stub: str | None = None,
    mutate: Callable[[str], str] | None = None,
) -> Path:
    # Same isolation as test_bridge_event_writer: Local mutex names only.
    isolated = tmp_path / "isolated-bin"
    isolated.mkdir(exist_ok=True)
    suffix = hashlib.sha256(os.fspath(tmp_path).encode()).hexdigest()[:16]
    for name in ("Drain-AcceptedBridgeQueue.ps1", "Restore-BridgeSpool.ps1"):
        text = (BIN / name).read_text(encoding="utf-8")
        for mutex in ("WaggleDanceBridgeAcceptedQueuePublicationV1", "WaggleDanceBridgeAppendV1",
                      "WaggleDanceBridgeSpoolReplayV1"):
            text = text.replace(rf"Global\{mutex}", rf"Local\{mutex}-{suffix}")
        assert "Global\\WaggleDance" not in text, name
        if name.startswith("Restore") and stub is not None:
            text = stub
        if name.startswith("Drain") and mutate is not None:
            mutated = mutate(text)
            assert mutated != text, "mutant did not apply"
            text = mutated
        (isolated / name).write_text(text, encoding="utf-8")
    shutil.copy2(BIN / "BridgeNamedMutex.ps1", isolated)
    return isolated


def _line(row: dict[str, object]) -> bytes:
    return (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")


def _row(event_type: str, ts: str) -> dict[str, object]:
    return {"ts_utc": ts, "agent": "codex-tools-1", "agent_uuid": "tools-uuid",
            "session_id": "tools-session", "run_id": "tools-run", "type": event_type,
            "status": "progress", "task_id": "codex-tools-1/history", "to": "",
            "message": "historical row", "payload": {}}


def _seed(root: Path) -> tuple[Path, Path, bytes]:
    """A ready WAL whose real targeted replay always fails: the canonical
    history holds a type no writer accepts (fails closed before and after the
    writer/replay type-parity fix)."""
    events = root / "shared" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_bytes(_line(_row("status", "2026-10-08T05:19:41.0000000Z"))
                       + _line(_row("not_a_writer_type", "2026-10-08T05:19:42.0000000Z")))
    accepted = root / "spool" / "accepted-v1"
    for state in ("pending", "ready", "replayed", "quarantine"):
        (accepted / state).mkdir(parents=True)
    wal = accepted / "ready" / LEAF
    wal_bytes = _line({**_row("message", "2026-10-11T04:00:00.0000000Z"), "status": "answered"})
    wal.write_bytes(wal_bytes)
    marker = {"schema": "waggledance.bridge.accepted-pending-block.v1", "wal_leaf": LEAF,
              "expected_sha256": hashlib.sha256(wal_bytes).hexdigest(),
              "created_at_utc": "2026-10-11T04:00:01.0000000Z"}
    (accepted / "ready" / f".{LEAF}.pending-recovery-blocked").write_bytes(_line(marker))
    return events, wal, wal_bytes


def _drain(engine: str, bin_dir: Path, root: Path, *extra: str) -> dict[str, object]:
    proc = _run(engine, ["-File", os.fspath(bin_dir / "Drain-AcceptedBridgeQueue.ps1"),
                         "-BridgeRoot", os.fspath(root), "-PendingMinAgeSeconds", "0",
                         "-ReceiptJson", *extra])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


def _memo_path(root: Path) -> Path:
    return root / "state" / "accepted-drain-backoff" / f"{LEAF}.json"


def _memo(root: Path) -> dict[str, object]:
    return json.loads(_memo_path(root).read_text(encoding="utf-8"))


def _code_sha256(bin_dir: Path) -> str:
    parts = [hashlib.sha256((bin_dir / name).read_bytes()).hexdigest()
             for name in ("Drain-AcceptedBridgeQueue.ps1", "Restore-BridgeSpool.ps1")]
    return hashlib.sha256("\n".join(parts).encode("ascii")).hexdigest()


def _calls(root: Path) -> int:
    calls = root / "replay-calls.txt"
    return len(calls.read_text(encoding="utf-8").splitlines()) if calls.exists() else 0


def _status(receipt: dict[str, object]) -> str:
    ready = [r for r in receipt["results"] if r["namespace"] == "ready" and r["leaf"] == LEAF]
    assert len(ready) == 1, receipt
    return ready[0]["status"]


def _write_memo(root: Path, bin_dir: Path, wal_bytes: bytes, **fields: object) -> None:
    now = int(time.time())
    memo: dict[str, object] = {
        "schema": "waggledance.bridge.accepted-drain-backoff.v1", "wal_leaf": LEAF,
        "wal_sha256": hashlib.sha256(wal_bytes).hexdigest(), "code_sha256": _code_sha256(bin_dir),
        "failures": 1, "last_failure_unix": now, "next_attempt_unix": now + 60, "last_error": "x",
    }
    memo.update(fields)
    _memo_path(root).parent.mkdir(parents=True, exist_ok=True)
    _memo_path(root).write_text(json.dumps(memo), encoding="utf-8")


# --- real replay ----------------------------------------------------------

@pytest.mark.parametrize("engine", SHELLS)
def test_failed_real_replay_backs_off_for_opportunistic_drains_only(tmp_path: Path, engine: str) -> None:
    root = tmp_path / "bridge"
    events, wal, wal_bytes = _seed(root)
    before = events.read_bytes()
    bin_dir = _isolated_bin(tmp_path)

    first = _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    assert (_status(first), first["failed"], first["drained"]) == ("failed", 1, 0)
    memo = _memo(root)
    assert memo["wal_sha256"] == hashlib.sha256(wal_bytes).hexdigest()
    assert memo["code_sha256"] == _code_sha256(bin_dir)
    assert memo["failures"] == 1
    assert memo["next_attempt_unix"] - memo["last_failure_unix"] == 60
    assert "unknown event type" in memo["last_error"]

    skipped = _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    assert (_status(skipped), skipped["failed"], skipped["drained"]) == ("replay_backoff", 1, 0)
    assert _memo(root) == memo

    explicit = _drain(engine, bin_dir, root)
    assert _status(explicit) == "failed"
    assert _memo(root)["failures"] == 2
    assert _memo(root)["next_attempt_unix"] - _memo(root)["last_failure_unix"] == 120

    assert wal.read_bytes() == wal_bytes
    assert events.read_bytes() == before


@pytest.mark.parametrize("engine", SHELLS)
def test_receipt_keeps_the_preflight_v1_shape(tmp_path: Path, engine: str) -> None:
    root = tmp_path / "bridge"
    _, _, wal_bytes = _seed(root)
    bin_dir = _isolated_bin(tmp_path, stub=STUB_REPLAY)
    _write_memo(root, bin_dir, wal_bytes)

    mutating = _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    dry = _drain(engine, bin_dir, root, "-DryRun", "-HonorReplayBackoff")

    assert frozenset(mutating) == TOP_LEVEL_KEYS == frozenset(dry)
    assert _status(mutating) == "replay_backoff"
    # The preflight reads DryRun receipts: the backoff never shows there.
    assert _status(dry) in READY_STATUSES


# --- stub replay: count every invocation -------------------------------------

def _assert_backoff_cycle(engine: str, tmp_path: Path, bin_dir: Path) -> None:
    root = tmp_path / "bridge"
    _seed(root)
    _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    assert _calls(root) == 1
    skipped = _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    assert _calls(root) == 1
    assert (_status(skipped), skipped["failed"]) == ("replay_backoff", 1)

    memo = _memo(root)
    now = int(time.time())
    _memo_path(root).write_text(json.dumps({**memo, "last_failure_unix": now - 61,
                                            "next_attempt_unix": now - 1}), encoding="utf-8")
    _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    assert _calls(root) == 2
    assert _memo(root)["failures"] == 2

    (root / "replay-succeeds").write_text("", encoding="utf-8")
    _drain(engine, bin_dir, root, "-HonorReplayBackoff")
    assert _calls(root) == 2
    drained = _drain(engine, bin_dir, root)
    assert _calls(root) == 3
    assert (_status(drained), drained["drained"], drained["failed"]) == ("drained", 1, 0)
    assert not _memo_path(root).exists()


@pytest.mark.parametrize("engine", SHELLS)
def test_backoff_expires_doubles_and_clears_on_success(tmp_path: Path, engine: str) -> None:
    _assert_backoff_cycle(engine, tmp_path, _isolated_bin(tmp_path, stub=STUB_REPLAY))


def _assert_code_change_retries_at_once(engine: str, tmp_path: Path, mutate=None) -> None:
    root = tmp_path / "bridge"
    _seed(root)
    _drain(engine, _isolated_bin(tmp_path, stub=STUB_REPLAY, mutate=mutate), root, "-HonorReplayBackoff")
    assert _calls(root) == 1
    # An installed replay fix changes the code digest: retry immediately.
    fixed = _isolated_bin(tmp_path, stub=STUB_REPLAY + "# fixed\n", mutate=mutate)
    _drain(engine, fixed, root, "-HonorReplayBackoff")
    assert _calls(root) == 2
    assert _memo(root)["failures"] == 1
    assert _memo(root)["code_sha256"] == _code_sha256(fixed)


@pytest.mark.parametrize("engine", SHELLS)
def test_changed_drain_or_replay_code_retries_at_once(tmp_path: Path, engine: str) -> None:
    _assert_code_change_retries_at_once(engine, tmp_path)


@pytest.mark.parametrize(
    ("label", "fields", "raw"),
    [
        ("other_wal_digest", {"wal_sha256": "0" * 64}, None),
        ("other_code_digest", {"code_sha256": "0" * 64}, None),
        ("other_schema", {"schema": "other"}, None),
        ("window_beyond_cap", {"next_attempt_unix": int(time.time()) + 100_000}, None),
        ("failure_in_future", {"last_failure_unix": int(time.time()) + 3600,
                               "next_attempt_unix": int(time.time()) + 3660}, None),
        ("zero_failures", {"failures": 0}, None),
        ("malformed", {}, "{not json"),
    ],
)
@pytest.mark.parametrize("engine", SHELLS)
def test_unbound_or_out_of_range_memo_is_ignored(
    tmp_path: Path, engine: str, label: str, fields: dict[str, object], raw: str | None,
) -> None:
    root = tmp_path / "bridge"
    _, _, wal_bytes = _seed(root)
    bin_dir = _isolated_bin(tmp_path, stub=STUB_REPLAY)
    _write_memo(root, bin_dir, wal_bytes, **fields)
    if raw is not None:
        _memo_path(root).write_text(raw, encoding="utf-8")

    receipt = _drain(engine, bin_dir, root, "-HonorReplayBackoff")

    assert _calls(root) == 1, label
    assert _status(receipt) == "failed"
    rewritten = _memo(root)
    assert rewritten["failures"] == 1, label
    assert rewritten["wal_sha256"] == hashlib.sha256(wal_bytes).hexdigest()
    assert rewritten["code_sha256"] == _code_sha256(bin_dir)


@pytest.mark.parametrize("engine", SHELLS)
def test_dry_run_neither_honors_nor_writes_a_memo(tmp_path: Path, engine: str) -> None:
    root = tmp_path / "bridge"
    _, _, wal_bytes = _seed(root)
    bin_dir = _isolated_bin(tmp_path, stub=STUB_REPLAY)

    dry = _drain(engine, bin_dir, root, "-DryRun", "-HonorReplayBackoff")
    assert _calls(root) == 1
    assert _status(dry) == "failed"
    assert not (root / "state").exists()

    _write_memo(root, bin_dir, wal_bytes)
    memo_bytes = _memo_path(root).read_bytes()
    _drain(engine, bin_dir, root, "-DryRun", "-HonorReplayBackoff")
    assert _calls(root) == 2
    assert _memo_path(root).read_bytes() == memo_bytes


@pytest.mark.parametrize("engine", SHELLS)
def test_mutating_drain_removes_orphan_memos_and_temporaries(tmp_path: Path, engine: str) -> None:
    root = tmp_path / "bridge"
    _, _, wal_bytes = _seed(root)
    bin_dir = _isolated_bin(tmp_path, stub=STUB_REPLAY)
    _write_memo(root, bin_dir, wal_bytes)
    backoff = _memo_path(root).parent
    orphan = backoff / ("bridge-wal-v1-" + "6c" * 16 + ".jsonl.json")
    temporary = backoff / f"{LEAF}.json.4242.tmp"
    unrelated = backoff / "README.txt"
    for path in (orphan, temporary, unrelated):
        path.write_text("{}", encoding="utf-8")

    _drain(engine, bin_dir, root, "-HonorReplayBackoff")

    assert not orphan.exists() and not temporary.exists()
    assert unrelated.exists()
    assert _memo_path(root).exists()  # its ready WAL is still retained


def test_opportunistic_callers_pass_the_switch_and_explicit_ones_do_not() -> None:
    drain_call = re.compile(r"&\s*\$acceptedDrain\s*`\s*\r?\n\s*-BridgeRoot\s+\$\w+\s+-ReceiptJson(?P<rest>[^)]*)\)")
    for name in CALLERS:
        calls = list(drain_call.finditer((BIN / name).read_text(encoding="utf-8")))
        assert len(calls) == 1, name
        assert calls[0].group("rest").strip() == "-HonorReplayBackoff", name
    drain = (BIN / "Drain-AcceptedBridgeQueue.ps1").read_text(encoding="utf-8")
    assert "[switch] $HonorReplayBackoff" in drain
    # The machine preflight runs a DryRun drain and must stay unaffected.
    preflight = (ROOT / "tools" / "bridge_accepted_queue_preflight.py").read_text(encoding="utf-8")
    assert "HonorReplayBackoff" not in preflight


# --- named mutants ---------------------------------------------------------

@pytest.mark.parametrize("engine", SHELLS[:1])
def test_MUT_BACKOFF_IGNORES_CODE_BINDING_is_caught(tmp_path: Path, engine: str) -> None:
    def mutant(text: str) -> str:
        return text.replace("[string]$memo.code_sha256 -cne $CodeSha256", "$false", 1)

    with pytest.raises(AssertionError):
        _assert_code_change_retries_at_once(engine, tmp_path, mutate=mutant)


@pytest.mark.parametrize("engine", SHELLS[:1])
def test_MUT_BACKOFF_HONORED_WITHOUT_SWITCH_is_caught(tmp_path: Path, engine: str) -> None:
    bin_dir = _isolated_bin(tmp_path, stub=STUB_REPLAY, mutate=lambda t: t.replace(
        "if ($HonorReplayBackoff -and $null -ne $priorBackoff", "if ($true -and $null -ne $priorBackoff", 1))
    with pytest.raises(AssertionError):
        _assert_backoff_cycle(engine, tmp_path, bin_dir)


@pytest.mark.parametrize("engine", SHELLS[:1])
def test_MUT_BACKOFF_SKIP_COUNTS_AS_HEALTHY_is_caught(tmp_path: Path, engine: str) -> None:
    def mutant(text: str) -> str:
        return re.sub(r"\$failed\+\+(\s+\$results\.Add\(\[pscustomobject\]\[ordered\]@\{\s+namespace = 'ready'\s+"
                      r"leaf = \$file\.Name\s+sha256 = \$sha256\s+status = 'replay_backoff')", r"\1", text, count=1)

    bin_dir = _isolated_bin(tmp_path, stub=STUB_REPLAY, mutate=mutant)
    with pytest.raises(AssertionError):
        _assert_backoff_cycle(engine, tmp_path, bin_dir)
