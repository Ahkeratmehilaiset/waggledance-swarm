"""Generation-aware stale-release proof tests for Write-AgentEvent.ps1."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_BIN = ROOT / ".agent-bridge" / "bin"
TASK_ID = "stale-generation-proof"
ARCHIVED_GENERATION = {
    "claimed_at_utc": "2026-07-31T00:00:00Z",
    "run_id": "archived-run",
    "owner_session_id": "archived-owner-session",
    "owner_token_sha256": "a" * 64,
}


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for stale generation proof tests")
    return executable


def _powershells() -> list[str]:
    executables: list[str] = []
    seen: set[str] = set()
    for name in ("pwsh", "powershell.exe", "powershell"):
        executable = shutil.which(name)
        if executable is None:
            continue
        key = os.path.normcase(str(Path(executable).resolve())).lower()
        if key in seen:
            continue
        seen.add(key)
        executables.append(executable)
    return executables


POWERSHELLS = _powershells() or [
    pytest.param("", marks=pytest.mark.skip(reason="PowerShell is required"))
]


def _isolated_internal_writer(
    tmp_path: Path,
    *,
    instrument_before_shared_append: bool = False,
    include_claim_writer: bool = False,
) -> Path:
    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    script_names = [
        "AgentBridgeSessionIdentity.ps1",
        "Write-AgentEvent.ps1",
    ]
    if include_claim_writer:
        script_names.append("Claim-AgentTask.ps1")
    for name in script_names:
        shutil.copy2(BRIDGE_BIN / name, isolated_bin / name)
    if instrument_before_shared_append:
        writer_path = isolated_bin / "Write-AgentEvent.ps1"
        source = writer_path.read_text(encoding="utf-8")
        append_source = "Add-LineWithRetry -Path $eventsPath -Line $line"
        injected = """
if ($env:WD_TEST_STALE_EVENT_READY) {
    [System.IO.File]::WriteAllText(
        [string]$env:WD_TEST_STALE_EVENT_READY,
        'ready'
    )
    while (-not (
            Test-Path -LiteralPath $env:WD_TEST_STALE_EVENT_RELEASE
        )) {
        Start-Sleep -Milliseconds 20
    }
}
""".strip()
        assert source.count(append_source) == 1
        writer_path.write_text(
            source.replace(
                append_source,
                f"{injected}\n{append_source}",
                1,
            ),
            encoding="utf-8",
        )
    if include_claim_writer:
        claim_path = isolated_bin / "Claim-AgentTask.ps1"
        claim_source = claim_path.read_text(encoding="utf-8")
        lock_source = (
            "$mutationLock = Enter-AgentBridgeMutationLock "
            "-BridgeRoot $bridgeRoot"
        )
        claim_injected = """
if ($env:WD_TEST_CLAIM_ATTEMPT) {
    [System.IO.File]::WriteAllText(
        [string]$env:WD_TEST_CLAIM_ATTEMPT,
        'attempting-lock'
    )
}
""".strip()
        assert claim_source.count(lock_source) == 1
        claim_path.write_text(
            claim_source.replace(
                lock_source,
                f"{claim_injected}\n{lock_source}",
                1,
            ),
            encoding="utf-8",
        )
    (isolated_bin / "Invoke-StaleClaimSweep.ps1").write_text(
        """
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $TaskId,
    [Parameter(Mandatory)] [string] $ArchivePath,
    [Parameter(Mandatory)] [string] $PayloadJson
)

& (Join-Path $PSScriptRoot 'Write-AgentEvent.ps1') `
    -Agent system `
    -Type release `
    -Status stale_lease `
    -TaskId $TaskId `
    -Message 'generation-aware stale release proof' `
    -PayloadJson $PayloadJson `
    -InternalStaleLeaseArchivePath $ArchivePath
""".strip(),
        encoding="utf-8",
    )
    return isolated_bin


def _write_generation_fixture(
    runtime_root: Path,
    *,
    active_generation: dict[str, object],
    archived_generation: dict[str, object] | None = None,
) -> tuple[Path, str]:
    claims_dir = runtime_root / "work_queue" / "claims"
    done_dir = runtime_root / "work_queue" / "done"
    claims_dir.mkdir(parents=True)
    done_dir.mkdir(parents=True)

    archive_generation = dict(
        ARCHIVED_GENERATION
        if archived_generation is None
        else archived_generation
    )
    released_at = "2026-07-31T00:15:00Z"
    archive_path = (
        done_dir / f"{TASK_ID}.20260731T001500Z.stale_lease.json"
    )
    archive_payload: dict[str, object] = {
        "agent": "codex",
        "task_id": TASK_ID,
        "summary": "archived stale generation",
        "mode": "read-only",
        "write_scope": [],
        "released_at_utc": released_at,
        "release_status": "stale_lease",
        **archive_generation,
    }
    archive_path.write_text(
        json.dumps(archive_payload, sort_keys=True),
        encoding="utf-8",
    )

    active_payload: dict[str, object] = {
        "agent": "codex",
        "task_id": TASK_ID,
        "summary": "active generation after stale release",
        "mode": "read-only",
        "write_scope": [],
        **active_generation,
    }
    (claims_dir / f"{TASK_ID}.json").write_text(
        json.dumps(active_payload, sort_keys=True),
        encoding="utf-8",
    )

    proof_payload = json.dumps(
        {
            "task_id": TASK_ID,
            "claim_claimed_at_utc": archive_generation.get(
                "claimed_at_utc",
                "",
            ),
            "claim_run_id": archive_generation.get("run_id", ""),
            "archive_released_at_utc": released_at,
            "archived_path": str(archive_path),
            "archive_state_semantics": "verified_before_event_append",
        },
        sort_keys=True,
    )
    return archive_path, proof_payload


def _run_internal_writer(
    runtime_root: Path,
    isolated_bin: Path,
    archive_path: Path,
    proof_payload: str,
    *,
    powershell: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    for name in (
        "AGENT_BRIDGE_AGENT",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        "AGENT_BRIDGE_OWNER_TOKEN",
        "AGENT_BRIDGE_OWNER_PID",
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
    ):
        env.pop(name, None)
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    return subprocess.run(
        [
            powershell or _powershell(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(isolated_bin / "Invoke-StaleClaimSweep.ps1"),
            "-TaskId",
            TASK_ID,
            "-ArchivePath",
            str(archive_path),
            "-PayloadJson",
            proof_payload,
        ],
        cwd=runtime_root.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _wait_for_path(path: Path, *, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for synchronization path: {path}")


@pytest.mark.parametrize(
    ("changed_field", "new_value"),
    [
        ("claimed_at_utc", "2026-07-31T00:20:00Z"),
        ("run_id", "new-run"),
        ("owner_session_id", "new-owner-session"),
        ("owner_token_sha256", "b" * 64),
    ],
)
def test_demonstrably_new_generation_appends_truthful_stale_event(
    tmp_path: Path,
    changed_field: str,
    new_value: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation = dict(ARCHIVED_GENERATION)
    active_generation[changed_field] = new_value
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
    )

    assert completed.returncode == 0, completed.stderr
    events_path = runtime_root / "shared" / "events.jsonl"
    event_lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(event_lines) == 1
    event = json.loads(event_lines[0])
    assert event["agent"] == "system"
    assert event["task_id"] == TASK_ID
    assert event["status"] == "stale_lease"
    assert event["payload"]["archived_path"] == str(archive_path)


def test_same_generation_active_claim_still_rejects_stale_event(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=dict(ARCHIVED_GENERATION),
    )
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
    )

    assert completed.returncode != 0
    assert "conflicts with an active claim" in (
        completed.stdout + completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize(
    ("malformed_field", "malformed_value"),
    [
        ("claimed_at_utc", 123),
        ("run_id", ["archived-run"]),
        ("owner_session_id", {"value": "archived-owner-session"}),
        ("owner_token_sha256", None),
    ],
)
def test_nonstring_active_generation_field_fails_closed(
    tmp_path: Path,
    malformed_field: str,
    malformed_value: object,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation: dict[str, object] = dict(ARCHIVED_GENERATION)
    active_generation[malformed_field] = malformed_value
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "active claim generation field" in combined
    assert malformed_field in combined
    assert "must be a string" in combined
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("invalid_entry", ["directory", "invalid-utf8"])
def test_unverifiable_json_entry_fails_closed(
    tmp_path: Path,
    invalid_entry: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation = dict(ARCHIVED_GENERATION)
    active_generation["owner_session_id"] = "new-owner-session"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    active_path = (
        runtime_root / "work_queue" / "claims" / f"{TASK_ID}.json"
    )
    if invalid_entry == "directory":
        active_path.unlink()
        active_path.mkdir()
    else:
        active_path.write_bytes(
            (
                '{"task_id":"stale-generation-proof",'
                '"claimed_at_utc":"2026-07-31T00:00:00Z",'
                '"run_id":"archived-run",'
                '"owner_session_id":"new-owner-'
            ).encode("utf-8")
            + b"\xff"
            + (
                '","owner_token_sha256":"'
                + ("a" * 64)
                + '"}'
            ).encode("utf-8")
        )
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
    )

    assert completed.returncode != 0
    assert "cannot verify active claims" in (
        completed.stdout + completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize("fault", ["invalid-utf8", "utf8-bom"])
def test_stale_archive_proof_rejects_untrusted_utf8(
    tmp_path: Path,
    powershell: str,
    fault: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation = dict(ARCHIVED_GENERATION)
    active_generation["owner_session_id"] = "new-owner-session"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    archive_bytes = archive_path.read_bytes()
    if fault == "utf8-bom":
        archive_bytes = b"\xef\xbb\xbf" + archive_bytes
    else:
        marker = b"archived stale generation"
        assert archive_bytes.count(marker) == 1
        archive_bytes = archive_bytes.replace(
            marker,
            b"archived stale \xff generation",
            1,
        )
    archive_path.write_bytes(archive_bytes)
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
        powershell=powershell,
    )

    assert completed.returncode != 0
    assert "archive proof is not valid JSON" in (
        completed.stdout + completed.stderr
    )
    assert archive_path.read_bytes() == archive_bytes
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_missing_legacy_generation_fields_compare_as_empty(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    legacy_generation = {
        "claimed_at_utc": ARCHIVED_GENERATION["claimed_at_utc"],
        "run_id": ARCHIVED_GENERATION["run_id"],
    }
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=dict(legacy_generation),
        archived_generation=dict(legacy_generation),
    )
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
    )

    assert completed.returncode != 0
    assert "conflicts with an active claim" in (
        completed.stdout + completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize(
    ("field_name", "case_variant"),
    [
        ("task_id", "Task_Id"),
        ("task_id", ""),
    ],
)
def test_unknown_active_task_identity_fails_closed(
    tmp_path: Path,
    field_name: str,
    case_variant: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation = dict(ARCHIVED_GENERATION)
    active_generation["owner_session_id"] = "new-owner-session"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    active_path = (
        runtime_root / "work_queue" / "claims" / f"{TASK_ID}.json"
    )
    active_payload = json.loads(active_path.read_text(encoding="utf-8"))
    if case_variant:
        value = active_payload.pop(field_name)
        active_payload[case_variant] = value
    else:
        active_payload[field_name] = ""
    active_path.write_text(
        json.dumps(active_payload, sort_keys=True),
        encoding="utf-8",
    )
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_payload,
    )

    assert completed.returncode != 0
    assert "cannot verify active claims" in (
        completed.stdout + completed.stderr
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize(
    ("record", "field_name", "case_variant"),
    [
        ("archive", "task_id", "Task_Id"),
        ("payload", "archived_path", "Archived_Path"),
    ],
)
def test_case_variant_proof_fields_are_not_promoted(
    tmp_path: Path,
    record: str,
    field_name: str,
    case_variant: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation = dict(ARCHIVED_GENERATION)
    active_generation["owner_session_id"] = "new-owner-session"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    if record == "archive":
        payload = json.loads(archive_path.read_text(encoding="utf-8"))
        payload[case_variant] = payload.pop(field_name)
        archive_path.write_text(
            json.dumps(payload, sort_keys=True),
            encoding="utf-8",
        )
        proof_text = proof_payload
    else:
        payload = json.loads(proof_payload)
        payload[case_variant] = payload.pop(field_name)
        proof_text = json.dumps(payload, sort_keys=True)
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        proof_text,
    )

    assert completed.returncode != 0
    combined = completed.stdout + completed.stderr
    assert (
        "archive proof does not match" in combined
        or "payload must identify the archived claim proof" in combined
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize(
    ("field_name", "replacement", "expected_message"),
    [
        (
            "archived_path",
            ["archive-path-must-not-coerce"],
            "must identify the archived claim proof",
        ),
        (
            "archive_released_at_utc",
            ["2026-07-31T00:15:00Z"],
            "released_at generation must be a string",
        ),
        (
            "archive_state_semantics",
            ["verified_before_event_append"],
            "semantics do not match",
        ),
    ],
)
def test_nonstring_payload_proof_fields_fail_closed(
    tmp_path: Path,
    field_name: str,
    replacement: object,
    expected_message: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    active_generation = dict(ARCHIVED_GENERATION)
    active_generation["owner_session_id"] = "new-owner-session"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=active_generation,
    )
    payload = json.loads(proof_payload)
    payload[field_name] = replacement
    isolated_bin = _isolated_internal_writer(tmp_path)

    completed = _run_internal_writer(
        runtime_root,
        isolated_bin,
        archive_path,
        json.dumps(payload, sort_keys=True),
    )

    assert completed.returncode != 0
    assert expected_message in completed.stdout + completed.stderr
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize(
    "powershell",
    _powershells()
    or [
        pytest.param(
            "",
            marks=pytest.mark.skip(reason="PowerShell is required"),
        )
    ],
)
def test_verified_stale_event_holds_claim_lock_through_shared_append(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    archive_path, proof_payload = _write_generation_fixture(
        runtime_root,
        active_generation=dict(ARCHIVED_GENERATION),
    )
    (
        runtime_root / "work_queue" / "claims" / f"{TASK_ID}.json"
    ).unlink()
    isolated_bin = _isolated_internal_writer(
        tmp_path,
        instrument_before_shared_append=True,
        include_claim_writer=True,
    )
    event_ready = tmp_path / "event-ready"
    event_release = tmp_path / "event-release"
    claim_attempt = tmp_path / "claim-attempt"

    writer_env = os.environ.copy()
    for name in (
        "AGENT_BRIDGE_AGENT",
        "AGENT_BRIDGE_AGENT_UUID",
        "AGENT_BRIDGE_CAPABILITIES",
        "AGENT_BRIDGE_ROLE",
        "AGENT_BRIDGE_RUN_ID",
        "AGENT_BRIDGE_SESSION_ID",
        "AGENT_BRIDGE_OWNER_SESSION_ID",
        "AGENT_BRIDGE_OWNER_TOKEN",
        "AGENT_BRIDGE_OWNER_PID",
        "AGENT_BRIDGE_OWNER_PROCESS_START_UTC",
    ):
        writer_env.pop(name, None)
    writer_env.update(
        {
            "AGENT_BRIDGE_RUNTIME_ROOT": str(runtime_root),
            "WD_TEST_STALE_EVENT_READY": str(event_ready),
            "WD_TEST_STALE_EVENT_RELEASE": str(event_release),
        }
    )
    writer = subprocess.Popen(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(isolated_bin / "Invoke-StaleClaimSweep.ps1"),
            "-TaskId",
            TASK_ID,
            "-ArchivePath",
            str(archive_path),
            "-PayloadJson",
            proof_payload,
        ],
        cwd=runtime_root.parent,
        env=writer_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    claim: subprocess.Popen[str] | None = None
    try:
        _wait_for_path(event_ready)
        assert writer.poll() is None

        claim_task_id = "cooperative-claim-during-stale-event"
        claim_env = os.environ.copy()
        claim_env.update(
            {
                "AGENT_BRIDGE_RUNTIME_ROOT": str(runtime_root),
                "AGENT_BRIDGE_AGENT": "codex",
                "AGENT_BRIDGE_RUN_ID": "race-run",
                "AGENT_BRIDGE_SESSION_ID": "race-session",
                "AGENT_BRIDGE_OWNER_SESSION_ID": "race-owner-session",
                "AGENT_BRIDGE_OWNER_TOKEN": "b" * 64,
                "AGENT_BRIDGE_OWNER_PID": str(os.getpid()),
                "AGENT_BRIDGE_OWNER_PROCESS_START_UTC": (
                    "2026-07-31T00:00:00Z"
                ),
                "WD_TEST_CLAIM_ATTEMPT": str(claim_attempt),
            }
        )
        claim = subprocess.Popen(
            [
                powershell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(isolated_bin / "Claim-AgentTask.ps1"),
                "-Agent",
                "codex",
                "-TaskId",
                claim_task_id,
                "-Summary",
                "must wait for stale event append",
            ],
            cwd=runtime_root.parent,
            env=claim_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_for_path(claim_attempt)
        time.sleep(0.2)
        assert claim.poll() is None
        assert not (
            runtime_root
            / "work_queue"
            / "claims"
            / f"{claim_task_id}.json"
        ).exists()

        event_release.write_text("release", encoding="utf-8")
        writer_stdout, writer_stderr = writer.communicate(timeout=20)
        claim_stdout, claim_stderr = claim.communicate(timeout=20)
    finally:
        if writer.poll() is None:
            writer.terminate()
            writer.communicate(timeout=10)
        if claim is not None and claim.poll() is None:
            claim.terminate()
            claim.communicate(timeout=10)

    assert writer.returncode == 0, f"{writer_stdout}\n{writer_stderr}"
    assert claim.returncode == 0, f"{claim_stdout}\n{claim_stderr}"
    events = [
        json.loads(line)
        for line in (
            runtime_root / "shared" / "events.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [(event["agent"], event["type"]) for event in events] == [
        ("system", "release"),
        ("codex", "claim"),
    ]
