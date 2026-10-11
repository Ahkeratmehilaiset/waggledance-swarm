"""Replay event-type allowlists stay equal to the writer contract.

2026-10-10T21:58Z: a valid bound reply was retained in an accepted-v1 WAL and
never drained. Full canonical validation in Drain-AcceptedBridgeQueue.ps1 and
Restore-BridgeSpool.ps1 refused a historical ``consumer_tick`` row that
Write-AgentEvent.ps1 had accepted (Lead proposal 1382489D), so every
non-Compact reader retried the drain and failed.

Named mutant: MUT_REPLAY_ALLOWLIST_PARITY_BROKEN (``consumer_tick`` removed from
the replay allowlists of an isolated copy) - the regression twin must fail.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from tools.bridge_event_writer import V1_EVENT_TYPES

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge" / "bin"
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
REPLAY_SCRIPTS = ("Drain-AcceptedBridgeQueue.ps1", "Restore-BridgeSpool.ps1")
WRITER_ONLY_BEFORE_FIX = ("triage_disposition", "consumer_tick")

pytestmark = pytest.mark.skipif(os.name != "nt", reason="PowerShell bridge helpers are Windows-only")

_PARSE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$result = [ordered]@{}
foreach ($name in $args) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($name, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "parse errors in ${name}: $($errors[0].Message)" }
    if ($name -like '*Write-AgentEvent.ps1') {
        $param = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -ceq 'Type' })
        $set = @($param[0].Attributes | Where-Object { $_.TypeName.Name -ceq 'ValidateSet' })
        if ($param.Count -ne 1 -or $set.Count -ne 1) { throw 'writer -Type ValidateSet not found exactly once' }
        $values = @($set[0].PositionalArguments | ForEach-Object { $_.Value })
    } else {
        $assignments = @($ast.FindAll({
            param($node)
            $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
            $node.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
            $node.Left.VariablePath.UserPath -ceq 'knownEventTypes'
        }, $true))
        if ($assignments.Count -ne 1) { throw "knownEventTypes assigned $($assignments.Count) times in $name" }
        $values = @($assignments[0].Right.FindAll({
            param($node) $node -is [System.Management.Automation.Language.StringConstantExpressionAst]
        }, $true) | ForEach-Object { $_.Value })
    }
    $result[[IO.Path]::GetFileName($name)] = $values
}
$result | ConvertTo-Json -Compress
"""


def _run(engine: str, argv: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [engine, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False, **kwargs,
    )


def _declared_type_lists(engine: str, tmp_path: Path, bin_dir: Path) -> dict[str, list[str]]:
    script = tmp_path / "parse-type-lists.ps1"
    script.write_text(_PARSE_SCRIPT, encoding="utf-8-sig")
    paths = [os.fspath(bin_dir / name) for name in ("Write-AgentEvent.ps1", *REPLAY_SCRIPTS)]
    proc = _run(engine, ["-File", os.fspath(script), *paths])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("engine", SHELLS)
def test_replay_allowlists_equal_writer_contract(tmp_path: Path, engine: str) -> None:
    lists = _declared_type_lists(engine, tmp_path, BIN)
    writer = lists["Write-AgentEvent.ps1"]
    assert len(writer) == len(set(writer))
    assert set(writer) == set(V1_EVENT_TYPES)
    for name in REPLAY_SCRIPTS:
        assert len(lists[name]) == len(set(lists[name])), name
        # Both directions: a writer-accepted type never blocks replay, and a
        # type replay accepts is already refused at the writer if unknown there.
        assert set(lists[name]) == set(writer), name


def _isolated_bin(tmp_path: Path, *, drop_type: str | None = None) -> Path:
    # Same isolation as test_bridge_event_writer: Local mutex names, so the
    # live fleet's Global publication fence is never touched.
    isolated = tmp_path / "isolated-bin"
    isolated.mkdir()
    suffix = hashlib.sha256(os.fspath(tmp_path).encode()).hexdigest()[:16]
    replacements = {
        rf"Global\{name}": rf"Local\{name}-{suffix}"
        for name in (
            "WaggleDanceBridgeAcceptedQueuePublicationV1",
            "WaggleDanceBridgeAppendV1",
            "WaggleDanceBridgeSpoolReplayV1",
        )
    }
    for name in REPLAY_SCRIPTS:
        text = (BIN / name).read_text(encoding="utf-8")
        for original, replacement in replacements.items():
            text = text.replace(original, replacement)
        assert "Global\\WaggleDance" not in text, name
        if drop_type is not None:
            # MUT_REPLAY_ALLOWLIST_PARITY_BROKEN
            mutated = text.replace(f", '{drop_type}'", "", 1)
            assert mutated != text, name
            text = mutated
        (isolated / name).write_text(text, encoding="utf-8")
    shutil.copy2(BIN / "BridgeNamedMutex.ps1", isolated)
    return isolated


def _history_row(event_type: str) -> dict[str, object]:
    row: dict[str, object] = {
        "ts_utc": "2026-10-08T05:19:41.5209858Z", "agent": "codex-tools-1",
        "agent_uuid": "tools-uuid", "session_id": "tools-session", "run_id": "tools-run",
        "type": event_type, "task_id": "codex-tools-1/consumer-loop", "to": "",
        "message": "historical row the writer accepted", "payload": {},
    }
    if event_type == "consumer_tick":
        row["status"] = "consumer_tick_finished"
    elif event_type == "triage_disposition":
        row.update(status="recorded", payload={"disposition": "ack_dispatch", "target_event_id": "evt-1"})
    else:
        row["status"] = "progress"
    return row


def _reply_row_2158z() -> dict[str, object]:
    # Synthetic copy of the shape of the retained 21:58:32Z row (a bound relay
    # reply); never the live WAL file.
    return {
        "ts_utc": "2026-10-10T21:58:32.0000000Z", "agent": "claude-rco-1",
        "agent_uuid": "rco1-uuid", "session_id": "rco1-session", "run_id": "rco1-run",
        "type": "message", "status": "answered", "severity": "",
        "task_id": "codex-tools-1/pr1827-9397-build-review-20261011", "to": "codex-tools-1",
        "message": "RCO1 relay done: report path and sha in payload",
        "paths": [], "write_scope": [],
        "in_reply_to_request_id": "request-relay-v1", "in_reply_to_request_digest": "digest-relay-v1",
        "in_reply_to_requester": {"agent": "codex-tools-1", "agent_uuid": "tools-uuid",
                                  "session_id": "tools-session", "run_id": "tools-run"},
        "payload": {"report_path": "C:\\reports\\x-response.md", "report_sha256": "0" * 64,
                    "prompt_sha256": "1" * 64},
    }


def _line(row: dict[str, object]) -> bytes:
    return (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")


def _seed(root: Path, history_type: str) -> tuple[Path, Path, bytes]:
    events = root / "shared" / "events.jsonl"
    events.parent.mkdir(parents=True)
    events.write_bytes(_line(_history_row("status")) + _line(_history_row(history_type)))
    accepted = root / "spool" / "accepted-v1"
    for state in ("pending", "ready", "replayed", "quarantine"):
        (accepted / state).mkdir(parents=True)
    leaf = "bridge-wal-v1-" + "4a" * 16 + ".jsonl"
    wal = accepted / "ready" / leaf
    wal_bytes = _line(_reply_row_2158z())
    wal.write_bytes(wal_bytes)
    marker = {"schema": "waggledance.bridge.accepted-pending-block.v1", "wal_leaf": leaf,
              "expected_sha256": hashlib.sha256(wal_bytes).hexdigest(),
              "created_at_utc": "2026-10-10T21:58:33.0000000Z"}
    (accepted / "ready" / f".{leaf}.pending-recovery-blocked").write_bytes(_line(marker))
    return events, wal, wal_bytes


def _drain(engine: str, bin_dir: Path, root: Path) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_"))}
    proc = _run(engine, ["-File", os.fspath(bin_dir / "Drain-AcceptedBridgeQueue.ps1"),
                         "-BridgeRoot", os.fspath(root), "-PendingMinAgeSeconds", "0", "-ReceiptJson"], env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.parametrize("engine", SHELLS)
@pytest.mark.parametrize("history_type", WRITER_ONLY_BEFORE_FIX)
def test_retained_2158z_reply_drains_over_writer_accepted_history(
    tmp_path: Path, engine: str, history_type: str
) -> None:
    root = tmp_path / "bridge"
    events, wal, wal_bytes = _seed(root, history_type)
    before = events.read_bytes()

    receipt = _drain(engine, _isolated_bin(tmp_path), root)

    assert (receipt["drained"], receipt["failed"]) == (1, 0), receipt
    assert not wal.exists()
    assert (wal.parent.parent / "replayed" / wal.name).read_bytes() == wal_bytes
    assert events.read_bytes() == before + wal_bytes


@pytest.mark.parametrize("engine", SHELLS)
def test_mutant_replay_allowlist_parity_broken_keeps_reply_retained(tmp_path: Path, engine: str) -> None:
    # MUT_REPLAY_ALLOWLIST_PARITY_BROKEN: the twin above is discriminating.
    root = tmp_path / "bridge"
    events, wal, wal_bytes = _seed(root, "consumer_tick")
    before = events.read_bytes()
    bin_dir = _isolated_bin(tmp_path, drop_type="consumer_tick")
    assert "consumer_tick" not in _declared_type_lists(engine, tmp_path, _with_writer(bin_dir))["Drain-AcceptedBridgeQueue.ps1"]

    receipt = _drain(engine, bin_dir, root)

    assert receipt["drained"] == 0 and receipt["failed"] == 1, receipt
    assert wal.read_bytes() == wal_bytes
    assert events.read_bytes() == before
    assert re.search("unknown event type", json.dumps(receipt)), receipt


@pytest.mark.parametrize("engine", SHELLS)
def test_unknown_history_type_still_blocks_replay(tmp_path: Path, engine: str) -> None:
    # Parity widens nothing: a type the writer refuses still fails closed.
    root = tmp_path / "bridge"
    events, wal, wal_bytes = _seed(root, "not_a_writer_type")
    before = events.read_bytes()

    receipt = _drain(engine, _isolated_bin(tmp_path), root)

    assert receipt["drained"] == 0 and receipt["failed"] == 1, receipt
    assert wal.read_bytes() == wal_bytes
    assert events.read_bytes() == before


def _with_writer(bin_dir: Path) -> Path:
    shutil.copy2(BIN / "Write-AgentEvent.ps1", bin_dir)
    return bin_dir
