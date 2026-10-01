"""F23a: a stale sweep whose release event cannot publish leaves a durable, visible receipt.

The sweep archives first and publishes after every lock is released. When the writer throws, is
absent, or is cancelled, the archive stays and a bounded receipt lands under
work_queue/sweep_emit_failures; Get-AgentBridgeStatus shows it in -Json and human output without
changing anything. A receipt that cannot be persisted fails the sweep conspicuously.
"""

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge/bin"
RECEIPT_SCHEMA = "wd.bridge-sweep-emit-failure.v1"
TASK = "codex-lead-1/f23a-sweep-visibility"

RECORDING_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
      [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson)
$ErrorActionPreference = 'Stop'
$record = [ordered]@{ agent=$Agent; type=$Type; status=$Status; task_id=$TaskId;
                      payload=($PayloadJson | ConvertFrom-Json -ErrorAction Stop) }
$path = Join-Path (Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'shared') 'events.jsonl'
[IO.File]::AppendAllText($path, (($record | ConvertTo-Json -Depth 16 -Compress) + "`n"),
    (New-Object System.Text.UTF8Encoding($false)))
[pscustomobject]@{ type = $Type; _bridge_delivery = [pscustomobject]@{
    delivery_status = 'canonical'; canonical_durable = $true } }
"""

# The writer's own receipt shapes (Write-AgentEvent New-BridgeDeliveryReceipt): only a real boolean
# canonical_durable=true is a published release.
DELIVERY_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
      [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson)
[pscustomobject]@{ type = $Type; _bridge_delivery = [pscustomobject]@{
    delivery_status = '%(status)s'; canonical_durable = %(durable)s } }
"""

SILENT_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
      [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson)
"""

THROWING_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
      [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson)
throw 'fixture writer refused the append'
"""

CANCELLING_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
      [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson)
throw (New-Object System.OperationCanceledException('fixture cancellation'))
"""

SCRUB_PREFIXES = ("AGENT_BRIDGE_", "WD_")


def _shell(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.skip(f"{name} is not installed")
    return executable


REAL_WRITER = object()


def _fixture(tmp_path: Path, writer) -> tuple[Path, Path]:
    """An isolated runtime with one expired, provably not-live claim and a copied bin."""
    code = tmp_path / "fixture/.agent-bridge/bin"
    shutil.copytree(BIN, code)
    configs = tmp_path / "fixture/configs"
    configs.mkdir()
    shutil.copy2(ROOT / "configs/bridge_identity_registry.json", configs)
    if writer is REAL_WRITER:
        # The real writer, kept off production's named kernel mutexes (as test_bridge_stale_routing).
        prefix = "Local\\WdF23aFixture-" + uuid.uuid4().hex + "-"
        for script in code.glob("*.ps1"):
            source = script.read_text(encoding="utf-8-sig")
            if "Global\\WaggleDanceBridge" in source:
                script.write_text(source.replace("Global\\WaggleDanceBridge", prefix), encoding="utf-8-sig")
    elif writer is None:
        (code / "Write-AgentEvent.ps1").unlink()
    else:
        (code / "Write-AgentEvent.ps1").write_text(writer, encoding="utf-8")
    runtime = tmp_path / "runtime"
    (runtime / "work_queue/claims").mkdir(parents=True)
    (runtime / "shared").mkdir(parents=True)
    (runtime / "shared/events.jsonl").write_text("", encoding="utf-8")
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    (runtime / "work_queue/claims/f23a-sweep-visibility.json").write_text(json.dumps({
        "task_id": TASK, "agent": "codex-tools-1",
        "claimed_at_utc": past.isoformat(), "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60, "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none", "write_scope": ["tests/tools/x.py"],
    }), encoding="utf-8")
    return code, runtime


def _env(runtime: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(SCRUB_PREFIXES)}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return env


def _run(shell: str, script: Path, runtime: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_shell(shell), "-NoProfile", "-NonInteractive", "-File", str(script), *args],
        cwd=ROOT, env=_env(runtime), capture_output=True, text=True, timeout=120, check=False,
    )


def _sweep(shell: str, code: Path, runtime: Path) -> subprocess.CompletedProcess:
    return _run(shell, code / "Invoke-StaleClaimSweep.ps1", runtime, "-StaleSeconds", "1", "-Quiet")


def _receipts(runtime: Path) -> list[dict]:
    folder = runtime / "work_queue/sweep_emit_failures"
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8-sig")) for p in sorted(folder.glob("*.json"))]


def _archives(runtime: Path) -> list[Path]:
    return list((runtime / "work_queue/done").glob("*.stale_lease.json"))


def _tree_digest(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        digest.update(str(path.relative_to(folder)).encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(("writer", "phase", "publication"), [
    (THROWING_WRITER, "writer_failed", "publication_unknown"),
    (None, "writer_absent", "failed"),
    (DELIVERY_WRITER % {"status": "queued", "durable": "$false"}, "writer_queued", "publication_unknown"),
    (DELIVERY_WRITER % {"status": "suppressed", "durable": "$false"}, "writer_suppressed", "failed"),
    (DELIVERY_WRITER % {"status": "canonical", "durable": "'True'"}, "writer_canonical", "publication_unknown"),
    (SILENT_WRITER, "writer_unconfirmed", "publication_unknown"),
], ids=["writer_throws", "writer_absent", "writer_queued", "writer_suppressed", "durable_as_string",
        "no_receipt"])
def test_unpublished_release_leaves_a_durable_receipt(tmp_path, shell, writer, phase, publication):
    code, runtime = _fixture(tmp_path, writer)
    completed = _sweep(shell, code, runtime)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    archives = _archives(runtime)
    assert len(archives) == 1                                   # the archive is kept, never rolled back
    assert not list((runtime / "work_queue/claims").glob("*.json"))  # and the claim is not restored
    receipts = _receipts(runtime)
    assert len(receipts) == 1, receipts
    receipt = receipts[0]
    assert receipt["schema"] == RECEIPT_SCHEMA
    assert receipt["task_id"] == TASK
    assert receipt["claim_agent"] == "codex-tools-1"
    assert receipt["archived_name"] == archives[0].name
    assert Path(receipt["archived_path"]).resolve() == archives[0].resolve()
    assert receipt["phase"] == phase
    assert receipt["publication"] == publication
    assert receipt["publication"] != "published"
    assert receipt["error"] and len(receipt["error"]) <= 1000
    datetime.fromisoformat(receipt["observed_at_utc"].replace("Z", "+00:00"))
    assert (runtime / "shared/events.jsonl").read_text(encoding="utf-8") == ""
    # No temporary receipt file survives the atomic rename.
    assert not [p for p in (runtime / "work_queue/sweep_emit_failures").iterdir()
                if not p.name.endswith(".json")]


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_published_release_leaves_no_receipt(tmp_path, shell):
    code, runtime = _fixture(tmp_path, RECORDING_WRITER)
    completed = _sweep(shell, code, runtime)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(_archives(runtime)) == 1
    rows = [json.loads(line) for line in
            (runtime / "shared/events.jsonl").read_text(encoding="utf-8-sig").splitlines()]
    assert [(r["type"], r["status"], r["task_id"]) for r in rows] == [("release", "stale_lease", TASK)]
    assert _receipts(runtime) == []
    assert not (runtime / "work_queue/sweep_emit_failures").exists()


@pytest.mark.skipif(os.name != "nt", reason="the real writer appends only on Windows")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_real_writer_durable_release_leaves_no_receipt(tmp_path, shell):
    code, runtime = _fixture(tmp_path, REAL_WRITER)
    completed = _sweep(shell, code, runtime)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(_archives(runtime)) == 1
    rows = [json.loads(line) for line in
            (runtime / "shared/events.jsonl").read_text(encoding="utf-8-sig").splitlines() if line]
    assert [(r["type"], r["status"], r["task_id"]) for r in rows if r.get("type") == "release"] == [
        ("release", "stale_lease", TASK)]
    assert _receipts(runtime) == []


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_receipt_storage_failure_fails_the_sweep_conspicuously(tmp_path, shell):
    code, runtime = _fixture(tmp_path, THROWING_WRITER)
    # A file where the receipt directory belongs: the receipt cannot be persisted.
    (runtime / "work_queue/sweep_emit_failures").write_text("not a directory", encoding="utf-8")
    completed = _sweep(shell, code, runtime)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert "sweep_emit_failures" in completed.stderr + completed.stdout
    assert TASK in completed.stderr + completed.stdout
    assert len(_archives(runtime)) == 1                         # still archived, never restored


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_cancellation_is_recorded_and_propagates(tmp_path, shell):
    code, runtime = _fixture(tmp_path, CANCELLING_WRITER)
    completed = _sweep(shell, code, runtime)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert len(_archives(runtime)) == 1
    receipts = _receipts(runtime)
    assert [(r["phase"], r["publication"]) for r in receipts] == [("cancelled", "publication_unknown")]


FIRST_CALL_THROWS_WRITER = r"""
[CmdletBinding()]
param([string]$Agent, [string]$Type, [string]$Status, [string]$Severity,
      [string]$TaskId, [string]$To, [string]$Message, [string]$PayloadJson)
$ErrorActionPreference = 'Stop'
$marker = Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'writer-called'
if (-not (Test-Path -LiteralPath $marker)) {
    [IO.File]::WriteAllText($marker, 'x')
    throw 'fixture writer refused the first append'
}
$record = [ordered]@{ type=$Type; status=$Status; task_id=$TaskId }
$path = Join-Path (Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'shared') 'events.jsonl'
[IO.File]::AppendAllText($path, (($record | ConvertTo-Json -Compress) + "`n"))
[pscustomobject]@{ type = $Type; _bridge_delivery = [pscustomobject]@{
    delivery_status = 'canonical'; canonical_durable = $true } }
"""

# Fault injection into the COPIED sweep only: the first receipt write is cancelled once.
RECEIPT_WRITE = "[System.IO.File]::WriteAllText($tmp, ($receipt"
CANCEL_ONCE = (
    "$fired = Join-Path $BridgeRoot 'receipt-cancel-fired'\n"
    "        if (-not (Test-Path -LiteralPath $fired)) {\n"
    "            [IO.File]::WriteAllText($fired, 'x')\n"
    "            throw (New-Object System.OperationCanceledException('fixture cancel during receipt write'))\n"
    "        }\n"
    "        " + RECEIPT_WRITE
)


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_cancellation_during_a_receipt_write_stops_later_publication(tmp_path, shell):
    code, runtime = _fixture(tmp_path, FIRST_CALL_THROWS_WRITER)
    sweep = code / "Invoke-StaleClaimSweep.ps1"
    source = sweep.read_text(encoding="utf-8-sig")
    assert source.count(RECEIPT_WRITE) == 1
    sweep.write_text(source.replace(RECEIPT_WRITE, CANCEL_ONCE), encoding="utf-8-sig")
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    (runtime / "work_queue/claims/zz-second.json").write_text(json.dumps({
        "task_id": TASK + "-second", "agent": "codex-tools-1",
        "claimed_at_utc": past.isoformat(), "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60, "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none", "write_scope": ["tests/tools/y.py"],
    }), encoding="utf-8")
    completed = _sweep(shell, code, runtime)
    assert completed.returncode != 0, completed.stdout + completed.stderr
    assert len(_archives(runtime)) == 2                         # both archived, neither restored
    # The cancellation stops publication: the second release is never sent to the writer.
    assert (runtime / "shared/events.jsonl").read_text(encoding="utf-8") == ""
    assert [(r["task_id"], r["phase"], r["publication"]) for r in _receipts(runtime)] == [
        (TASK + "-second", "cancelled", "failed")]


def _second_claim(runtime: Path) -> str:
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    task = TASK + "-second"
    (runtime / "work_queue/claims/zz-second.json").write_text(json.dumps({
        "task_id": task, "agent": "codex-tools-1",
        "claimed_at_utc": past.isoformat(), "last_heartbeat_utc": past.isoformat(),
        "lease_seconds": 60, "claim_lease_expires_utc": (past + timedelta(minutes=1)).isoformat(),
        "owner_identity": "none", "write_scope": ["tests/tools/y.py"],
    }), encoding="utf-8")
    return task


def _sweep_with_warning_stop(shell: str, code: Path, runtime: Path) -> subprocess.CompletedProcess:
    """The sweep run by a caller whose $WarningPreference is Stop (RCO2 d1bb3944 S1)."""
    sweep = str(code / "Invoke-StaleClaimSweep.ps1").replace("'", "''")
    command = "$WarningPreference = 'Stop'; & '" + sweep + "' -StaleSeconds 1 -Quiet"
    return _run_command(shell, command, runtime)


def _run_command(shell: str, command: str, runtime: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_shell(shell), "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=ROOT, env=_env(runtime), capture_output=True, text=True, timeout=120, check=False,
    )


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_warning_stop_caller_still_gets_a_receipt_for_every_failed_release(tmp_path, shell):
    code, runtime = _fixture(tmp_path, THROWING_WRITER)
    second = _second_claim(runtime)
    completed = _sweep_with_warning_stop(shell, code, runtime)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(_archives(runtime)) == 2
    assert sorted((r["task_id"], r["phase"], r["publication"]) for r in _receipts(runtime)) == [
        (TASK, "writer_failed", "publication_unknown"), (second, "writer_failed", "publication_unknown")]


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_warning_stop_caller_still_handles_every_archive_when_receipts_cannot_persist(tmp_path, shell):
    # Writer absent: the only warning reached is the receipt-persistence one.
    code, runtime = _fixture(tmp_path, None)
    second = _second_claim(runtime)
    (runtime / "work_queue/sweep_emit_failures").write_text("not a directory", encoding="utf-8")
    completed = _sweep_with_warning_stop(shell, code, runtime)
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0, output
    assert len(_archives(runtime)) == 2                         # archived, never restored
    # Both persistence failures are collected and reported together: the loop was not cut short.
    assert "release events were not published and whose sweep_emit_failures receipts" in output.replace("\n", "")
    assert TASK + " " in output.replace("\n", "") or TASK + " (" in output.replace("\n", "")
    assert second in output.replace("\n", "")


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_status_shows_receipts_and_malformed_ones_without_mutating(tmp_path, shell):
    code, runtime = _fixture(tmp_path, THROWING_WRITER)
    assert _sweep(shell, code, runtime).returncode == 0
    folder = runtime / "work_queue/sweep_emit_failures"
    (folder / "zz-malformed.json").write_text("{not json", encoding="utf-8")
    (folder / "zz-wrong-schema.json").write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    before = _tree_digest(runtime / "work_queue")
    status = code / "Get-AgentBridgeStatus.ps1"
    first = _run(shell, status, runtime, "-Json")
    second = _run(shell, status, runtime, "-Json")
    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    assert _tree_digest(runtime / "work_queue") == before    # reading status twice changes nothing
    view = json.loads(first.stdout)["sweep_emit_failures"]
    assert view == json.loads(second.stdout)["sweep_emit_failures"]
    assert view["count"] == 1
    assert [(r["task_id"], r["publication"]) for r in view["receipts"]] == [(TASK, "publication_unknown")]
    assert sorted(m["name"] for m in view["malformed"]) == ["zz-malformed.json", "zz-wrong-schema.json"]
    human = _run(shell, status, runtime)
    assert human.returncode == 0, human.stdout + human.stderr
    assert "STALE-SWEEP RELEASE EVENTS NOT PUBLISHED" in human.stdout
    assert TASK in human.stdout and "publication_unknown" in human.stdout
    assert "zz-malformed.json" in human.stdout


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_status_reports_a_failed_sweep_instead_of_swallowing_it(tmp_path, shell):
    code, runtime = _fixture(tmp_path, THROWING_WRITER)
    (runtime / "work_queue/sweep_emit_failures").write_text("not a directory", encoding="utf-8")
    completed = _run(shell, code / "Get-AgentBridgeStatus.ps1", runtime, "-Json")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    view = json.loads(completed.stdout)["sweep_emit_failures"]
    assert view["sweep_error"] and "sweep_emit_failures" in view["sweep_error"]
    assert view["readable"] is False
