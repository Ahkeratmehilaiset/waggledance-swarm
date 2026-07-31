"""Adversarial PowerShell stale-batch durability regressions."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
BRIDGE_BIN = ROOT / ".agent-bridge" / "bin"


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


def _write_stale_claim(runtime_root: Path, task_id: str) -> Path:
    claims_dir = runtime_root / "work_queue" / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    path = claims_dir / f"{task_id}.json"
    path.write_text(
        json.dumps(
            {
                "agent": "codex",
                "task_id": task_id,
                "summary": "stale batch hardening fixture",
                "mode": "read-only",
                "write_scope": [],
                "run_id": "stale-hardening-run",
                "claimed_at_utc": "2026-07-28T00:00:00Z",
                "last_heartbeat_utc": "2026-07-28T00:00:00Z",
                "lease_seconds": 1,
                "claim_lease_expires_utc": "2026-07-28T00:00:01Z",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _isolated_bin(tmp_path: Path) -> Path:
    isolated_bin = tmp_path / "isolated-bin"
    isolated_bin.mkdir()
    for name in (
        "AgentBridgeSessionIdentity.ps1",
        "Invoke-StaleClaimSweep.ps1",
        "Write-AgentEvent.ps1",
    ):
        shutil.copy2(BRIDGE_BIN / name, isolated_bin / name)
    return isolated_bin


def _run_sweep(
    *,
    powershell: str,
    runtime_root: Path,
    script_root: Path,
    warning_action_stop: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_root / "Invoke-StaleClaimSweep.ps1"),
        "-StaleSeconds",
        "1",
        "-Quiet",
    ]
    if warning_action_stop:
        command.extend(["-WarningAction", "Stop"])
    return subprocess.run(
        command,
        cwd=runtime_root.parent,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _inject_second_source_quarantine_failure(
    sweep_path: Path,
    *,
    failure_body: str,
) -> None:
    source = sweep_path.read_text(encoding="utf-8")
    quarantine_source = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    injected = (
        "            $injectedSourceQuarantineCount++\n"
        "            if ($injectedSourceQuarantineCount -eq 2) {\n"
        f"{failure_body}\n"
        "                throw 'injected second source quarantine failure'\n"
        "            }\n"
        f"{quarantine_source}"
    )
    assert source.count(quarantine_source) == 1
    assert source.count("$committedPlans = @()") == 1
    sweep_path.write_text(
        source.replace(
            "$committedPlans = @()",
            "$committedPlans = @()\n$injectedSourceQuarantineCount = 0",
            1,
        ).replace(quarantine_source, injected, 1),
        encoding="utf-8",
    )


def _inject_source_replacement_before_block(
    sweep_path: Path,
    *,
    target_block: str,
    replacement_json: str,
) -> None:
    source = sweep_path.read_text(encoding="utf-8")
    escaped_json = replacement_json.replace("'", "''")
    injection = (
        "            [System.IO.File]::WriteAllText(\n"
        "                [string]$plan.file.FullName,\n"
        f"                '{escaped_json}',\n"
        "                (New-Object System.Text.UTF8Encoding($false))\n"
        "            )\n"
    )
    assert source.count(target_block) == 1
    sweep_path.write_text(
        source.replace(target_block, injection + target_block, 1),
        encoding="utf-8",
    )


def _inject_after_marker(
    sweep_path: Path,
    *,
    marker: str,
    body: str,
) -> None:
    source = sweep_path.read_text(encoding="utf-8")
    assert source.count(marker) == 1
    sweep_path.write_text(
        source.replace(marker, f"{marker}\n{body}", 1),
        encoding="utf-8",
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_cleanup_warning_cannot_hide_committed_event_or_result(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "cleanup-warning-continues"
    claim_path = _write_stale_claim(runtime_root, task_id)
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    source = sweep_path.read_text(encoding="utf-8")
    cleanup_source = (
        "                    # STALE V2 MARKER: exact-handle committed "
        "backup cleanup."
    )
    injected = (
        f"{cleanup_source}\n"
        "                    throw 'injected source backup cleanup failure'"
    )
    assert source.count(cleanup_source) == 1
    sweep_path.write_text(
        source.replace(cleanup_source, injected, 1),
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
        warning_action_stop=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "committed but ancillary cleanup failed" in combined
    assert task_id in completed.stdout
    assert not claim_path.exists()
    assert len(
        list(claim_path.parent.glob(f"{claim_path.name}.stale-backup.*"))
    ) == 1
    events_path = runtime_root / "shared" / "events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [(event["task_id"], event["status"]) for event in events] == [
        (task_id, "stale_lease")
    ]


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_event_warning_cannot_hide_committed_result(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "event-warning-continues"
    claim_path = _write_stale_claim(runtime_root, task_id)
    isolated_bin = _isolated_bin(tmp_path)
    (isolated_bin / "Write-AgentEvent.ps1").write_text(
        "throw 'injected stale event append failure'\n",
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
        warning_action_stop=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "injected stale event append failure" in combined
    assert task_id in completed.stdout
    assert not claim_path.exists()
    assert len(
        list(
            (runtime_root / "work_queue" / "done").glob(
                f"{task_id}*.stale_lease.json"
            )
        )
    ) == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_rollback_retains_foreign_archive_replacement(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_ids = ("foreign-archive-first", "foreign-archive-second")
    claim_paths = [
        _write_stale_claim(runtime_root, task_id) for task_id in task_ids
    ]
    before = {path: path.read_bytes() for path in claim_paths}
    isolated_bin = _isolated_bin(tmp_path)
    _inject_second_source_quarantine_failure(
        isolated_bin / "Invoke-StaleClaimSweep.ps1",
        failure_body="""
                $foreignArchive = [string]$preparedPlans[0].done_path
                [System.IO.File]::Delete($foreignArchive)
                [System.IO.File]::WriteAllText(
                    $foreignArchive,
                    'FOREIGN-ARCHIVE-SENTINEL'
                )
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "archive rollback retained" in combined
    assert {path: path.read_bytes() for path in claim_paths} == before
    archives = list(
        (runtime_root / "work_queue" / "done").glob("*.stale_lease.json")
    )
    assert len(archives) == 1
    assert archives[0].read_text(encoding="utf-8") == (
        "FOREIGN-ARCHIVE-SENTINEL"
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()
    backups = list(claim_paths[0].parent.glob("*.stale-backup.*"))
    assert backups
    assert any(path.read_bytes() == before[claim_paths[0]] for path in backups)


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_rollback_does_not_trust_foreign_active_source(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_ids = ("foreign-source-first", "foreign-source-second")
    claim_paths = [
        _write_stale_claim(runtime_root, task_id) for task_id in task_ids
    ]
    before = {path: path.read_bytes() for path in claim_paths}
    isolated_bin = _isolated_bin(tmp_path)
    _inject_second_source_quarantine_failure(
        isolated_bin / "Invoke-StaleClaimSweep.ps1",
        failure_body="""
                [System.IO.File]::WriteAllText(
                    [string]$preparedPlans[0].file.FullName,
                    'FOREIGN-ACTIVE-SOURCE'
                )
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "active source ownership hash mismatched" in combined
    assert "archive rollback retained" in combined
    assert claim_paths[0].read_text(encoding="utf-8") == (
        "FOREIGN-ACTIVE-SOURCE"
    )
    assert claim_paths[1].read_bytes() == before[claim_paths[1]]
    backups = list(
        claim_paths[0].parent.glob(
            f"{claim_paths[0].name}.stale-backup.*"
        )
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == before[claim_paths[0]]
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_ids[0]}*.stale_lease.json"
        )
    )
    assert len(archives) == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_rollback_does_not_restore_foreign_backup(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_ids = ("foreign-backup-first", "foreign-backup-second")
    claim_paths = [
        _write_stale_claim(runtime_root, task_id) for task_id in task_ids
    ]
    before = {path: path.read_bytes() for path in claim_paths}
    isolated_bin = _isolated_bin(tmp_path)
    _inject_second_source_quarantine_failure(
        isolated_bin / "Invoke-StaleClaimSweep.ps1",
        failure_body="""
                [System.IO.File]::WriteAllText(
                    [string]$preparedPlans[0].source_backup_path,
                    'FOREIGN-SOURCE-BACKUP'
                )
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert {path: path.read_bytes() for path in claim_paths} == before
    backups = list(
        claim_paths[0].parent.glob(
            f"{claim_paths[0].name}.stale-backup.*"
        )
    )
    quarantines = list(
        claim_paths[0].parent.glob(
            f"{claim_paths[0].name}.stale-quarantine.*"
        )
    )
    assert len(backups) == 1
    assert len(quarantines) == 1
    assert backups[0].read_text(encoding="utf-8") == (
        "FOREIGN-SOURCE-BACKUP"
    )
    assert quarantines[0].read_bytes() == before[claim_paths[0]]
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_ids[0]}*.stale_lease.json"
        )
    )
    assert archives == []
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_snapshot_identity_rejects_fresh_replacement_before_backup(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "fresh-before-backup"
    claim_path = _write_stale_claim(runtime_root, task_id)
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh replacement before backup"
    fresh_payload["last_heartbeat_utc"] = "2099-07-31T00:00:00Z"
    fresh_payload["claim_lease_expires_utc"] = "2099-07-31T00:15:00Z"
    fresh_json = json.dumps(fresh_payload, sort_keys=True)
    isolated_bin = _isolated_bin(tmp_path)
    copy_block = (
        "            # STALE V2 MARKER: verify active source before trusted "
        "backup."
    )
    _inject_source_replacement_before_block(
        isolated_bin / "Invoke-StaleClaimSweep.ps1",
        target_block=copy_block,
        replacement_json=fresh_json,
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    assert completed.returncode != 0
    assert claim_path.read_text(encoding="utf-8") == fresh_json
    assert not list(
        (runtime_root / "work_queue" / "done").glob("*.stale_lease.json")
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_quarantine_restores_fresh_commit_time_replacement(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "fresh-before-quarantine"
    claim_path = _write_stale_claim(runtime_root, task_id)
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh replacement before quarantine"
    fresh_payload["last_heartbeat_utc"] = "2099-07-31T00:00:00Z"
    fresh_payload["claim_lease_expires_utc"] = "2099-07-31T00:15:00Z"
    fresh_json = json.dumps(fresh_payload, sort_keys=True)
    isolated_bin = _isolated_bin(tmp_path)
    move_block = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    _inject_source_replacement_before_block(
        isolated_bin / "Invoke-StaleClaimSweep.ps1",
        target_block=move_block,
        replacement_json=fresh_json,
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = " ".join((completed.stdout + completed.stderr).split())
    assert completed.returncode != 0
    assert "quarantined active" in combined
    assert "source identity mismatched" in combined
    assert claim_path.read_text(encoding="utf-8") == fresh_json
    assert list(claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*"))


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_quarantine_hardlink_restores_fresh_captured_generation(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "fresh-hardlink-before-quarantine"
    claim_path = _write_stale_claim(runtime_root, task_id)
    authorized = claim_path.read_bytes()
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh commit-time generation with hardlink"
    fresh_payload["last_heartbeat_utc"] = "2099-07-31T00:00:00Z"
    fresh_payload["claim_lease_expires_utc"] = "2099-07-31T00:15:00Z"
    fresh_json = json.dumps(fresh_payload, sort_keys=True)
    fresh = fresh_json.encode("utf-8")
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    source = sweep_path.read_text(encoding="utf-8")
    move_block = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    escaped_json = fresh_json.replace("'", "''")
    injected = (
        "            [System.IO.File]::WriteAllText(\n"
        "                [string]$plan.file.FullName,\n"
        f"                '{escaped_json}',\n"
        "                (New-Object System.Text.UTF8Encoding($false))\n"
        "            )\n"
        "            $freshAlias = (\n"
        "                [string]$plan.file.FullName + '.test-fresh-hardlink'\n"
        "            )\n"
        "            [void](New-Item -ItemType HardLink `\n"
        "                -Path $freshAlias `\n"
        "                -Target ([string]$plan.file.FullName) `\n"
        "                -ErrorAction Stop)\n"
        f"{move_block}"
    )
    assert source.count(move_block) == 1
    sweep_path.write_text(
        source.replace(move_block, injected, 1),
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    alias_path = claim_path.with_name(
        claim_path.name + ".test-fresh-hardlink"
    )
    quarantines = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*")
    )
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    assert completed.returncode != 0
    assert claim_path.read_bytes() == fresh
    assert claim_path.stat().st_nlink == 1
    assert len(quarantines) == 1
    assert alias_path.samefile(quarantines[0])
    assert quarantines[0].read_bytes() == fresh
    alias_path.write_bytes(b"MUTATED-STALE-REJECTED-HARDLINK")
    assert quarantines[0].read_bytes() == b"MUTATED-STALE-REJECTED-HARDLINK"
    assert claim_path.read_bytes() == fresh
    assert len(backups) == 1
    assert backups[0].read_bytes() == authorized
    assert not list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_rollback_retries_captured_fresh_generation_after_first_restore_failure(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "fresh-retry-after-restore-failure"
    claim_path = _write_stale_claim(runtime_root, task_id)
    authorized = claim_path.read_bytes()
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh generation restored by rollback retry"
    fresh_payload["last_heartbeat_utc"] = "2099-07-31T00:00:00Z"
    fresh_payload["claim_lease_expires_utc"] = "2099-07-31T00:15:00Z"
    fresh_json = json.dumps(fresh_payload, sort_keys=True)
    fresh = fresh_json.encode("utf-8")
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    move_block = (
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName,\n"
        "                [string]$plan.source_quarantine_path\n"
        "            )"
    )
    _inject_source_replacement_before_block(
        sweep_path,
        target_block=move_block,
        replacement_json=fresh_json,
    )
    identity_path = isolated_bin / "AgentBridgeSessionIdentity.ps1"
    identity = identity_path.read_text(encoding="utf-8")
    create_marker = "        # CAS V2 DIRECT MARKER: create canonical path."
    injected_failure = (
        "        if ($Context -ceq 'restored fresh stale-claim generation') {\n"
        "            return [pscustomobject]@{\n"
        "                succeeded = $false\n"
        "                created = $false\n"
        "                collision = $false\n"
        "                error = [System.IO.IOException]::new(\n"
        "                    'injected first fresh restore failure'\n"
        "                )\n"
        "            }\n"
        "        }"
    )
    assert identity.count(create_marker) == 1
    identity_path.write_text(
        identity.replace(
            create_marker,
            f"{injected_failure}\n{create_marker}",
            1,
        ),
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = " ".join((completed.stdout + completed.stderr).split())
    quarantines = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*")
    )
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    assert completed.returncode != 0
    assert "injected first fresh restore failure" in combined
    assert claim_path.read_bytes() == fresh
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == fresh
    assert len(backups) == 1
    assert backups[0].read_bytes() == authorized
    assert not list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_never_moved_source_does_not_replay_eligibility_backup(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "never-moved-backup-replay"
    claim_path = _write_stale_claim(runtime_root, task_id)
    authorized = claim_path.read_bytes()
    fresh_payload = json.loads(claim_path.read_text(encoding="utf-8"))
    fresh_payload["summary"] = "fresh external generation removed before Move"
    fresh_json = json.dumps(fresh_payload, sort_keys=True)
    fresh = fresh_json.encode("utf-8")
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    source = sweep_path.read_text(encoding="utf-8")
    marker = "            $plan.source_backup_prepared = $true"
    escaped_json = fresh_json.replace("'", "''")
    injected = (
        f"{marker}\n"
        "            $externalA = (\n"
        "                [string]$plan.file.FullName + '.test-external-A'\n"
        "            )\n"
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName, $externalA\n"
        "            )\n"
        "            [System.IO.File]::WriteAllText(\n"
        "                [string]$plan.file.FullName,\n"
        f"                '{escaped_json}',\n"
        "                (New-Object System.Text.UTF8Encoding($false))\n"
        "            )\n"
        "            $externalB = (\n"
        "                [string]$plan.file.FullName + '.test-external-B'\n"
        "            )\n"
        "            [System.IO.File]::Move(\n"
        "                [string]$plan.file.FullName, $externalB\n"
        "            )\n"
        "            throw 'injected failure after external B removal'"
    )
    assert source.count(marker) == 1
    sweep_path.write_text(
        source.replace(marker, injected, 1),
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = " ".join((completed.stdout + completed.stderr).split())
    external_a = claim_path.with_name(claim_path.name + ".test-external-A")
    external_b = claim_path.with_name(claim_path.name + ".test-external-B")
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    assert completed.returncode != 0
    assert "stale eligibility bytes were not republished" in combined
    assert not claim_path.exists()
    assert external_a.read_bytes() == authorized
    assert external_b.read_bytes() == fresh
    assert len(backups) == 1
    assert backups[0].read_bytes() == authorized
    assert not list(
        claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*")
    )
    assert not list((runtime_root / "work_queue" / "done").glob("*.json"))
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_post_move_capture_failure_does_not_replay_eligibility_bytes(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "post-move-capture-unavailable"
    claim_path = _write_stale_claim(runtime_root, task_id)
    authorized = claim_path.read_bytes()
    isolated_bin = _isolated_bin(tmp_path)
    identity_path = isolated_bin / "AgentBridgeSessionIdentity.ps1"
    identity = identity_path.read_text(encoding="utf-8")
    open_marker = (
        "        # CAS V2 EXISTING MARKER: open quarantined path exclusively."
    )
    injected = (
        "        if ($Context -ceq 'stale claim quarantined source') {\n"
        "            throw 'injected pre-byte quarantine capture failure'\n"
        "        }"
    )
    assert identity.count(open_marker) == 1
    identity_path.write_text(
        identity.replace(open_marker, f"{injected}\n{open_marker}", 1),
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = " ".join((completed.stdout + completed.stderr).split())
    quarantines = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*")
    )
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_id}*.stale_lease.json"
        )
    )
    assert completed.returncode != 0
    assert "injected pre-byte quarantine capture failure" in combined
    assert "stale eligibility bytes were not republished" in combined
    assert "active source restore suppressed for {0}" not in combined
    assert not claim_path.exists()
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == authorized
    assert len(backups) == 1
    assert backups[0].read_bytes() == authorized
    assert len(archives) == 1
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_case_variant_lease_fields_do_not_keep_claim_alive(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "case-variant-lease-fields"
    claim_path = _write_stale_claim(runtime_root, task_id)
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload.pop("last_heartbeat_utc")
    payload.pop("lease_seconds")
    payload.pop("claim_lease_expires_utc")
    payload.update(
        {
            "Last_Heartbeat_Utc": "2099-01-01T00:00:00Z",
            "Lease_Seconds": 2147483647,
            "Claim_Lease_Expires_Utc": "2099-01-01T00:00:00Z",
            "owner_session_id": "case-owner-session",
            "owner_token_sha256": hashlib.sha256(
                ("c" * 64).encode("utf-8")
            ).hexdigest(),
            "owner_pid": 4242,
            "owner_process_start_utc": "2026-07-28T00:00:00Z",
        }
    )
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    isolated_bin = _isolated_bin(tmp_path)

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not claim_path.exists()
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_id}*.stale_lease.json"
        )
    )
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archived["last_heartbeat_utc"] == ""
    assert archived["lease_seconds"] == 0
    assert archived["claim_lease_expires_utc"] == ""
    assert "Last_Heartbeat_Utc" not in archived
    assert "Lease_Seconds" not in archived
    assert "Claim_Lease_Expires_Utc" not in archived


@pytest.mark.parametrize("powershell", POWERSHELLS)
@pytest.mark.parametrize(
    ("fixture_name", "fixture_bytes"),
    [
        (
            "invalid-utf8",
            b'{"agent":"codex","task_id":"invalid-utf8",'
            b'"claimed_at_utc":"2026-07-28T00:00:00Z",'
            b'"value":"\xff"}',
        ),
        (
            "utf8-bom",
            b"\xef\xbb\xbf"
            b'{"agent":"codex","task_id":"utf8-bom",'
            b'"claimed_at_utc":"2026-07-28T00:00:00Z"}',
        ),
    ],
)
def test_stale_sweep_retains_untrusted_utf8_claim_bytes(
    tmp_path: Path,
    powershell: str,
    fixture_name: str,
    fixture_bytes: bytes,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    claim_path = (
        runtime_root
        / "work_queue"
        / "claims"
        / f"{fixture_name}.json"
    )
    claim_path.parent.mkdir(parents=True)
    claim_path.write_bytes(fixture_bytes)
    isolated_bin = _isolated_bin(tmp_path)

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert claim_path.read_bytes() == fixture_bytes
    assert not (runtime_root / "work_queue" / "done").exists()
    assert not (runtime_root / "shared").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_legacy_tokenless_claim_cannot_forge_long_lease(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "legacy-forged-long-lease"
    claim_path = _write_stale_claim(runtime_root, task_id)
    payload = json.loads(claim_path.read_text(encoding="utf-8"))
    payload["lease_seconds"] = 2147483647
    payload["claim_lease_expires_utc"] = "2099-01-01T00:00:00Z"
    claim_path.write_text(json.dumps(payload), encoding="utf-8")
    isolated_bin = _isolated_bin(tmp_path)

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not claim_path.exists()
    archives = list(
        (runtime_root / "work_queue" / "done").glob(
            f"{task_id}*.stale_lease.json"
        )
    )
    assert len(archives) == 1
    archived = json.loads(archives[0].read_text(encoding="utf-8"))
    assert archived["lease_seconds"] == 2147483647
    assert "lease threshold 1s" in archived["release_reason"]


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_rollback_retains_reappeared_foreign_archive_temp(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_ids = ("foreign-temp-first", "foreign-temp-second")
    claim_paths = [
        _write_stale_claim(runtime_root, task_id) for task_id in task_ids
    ]
    before = {path: path.read_bytes() for path in claim_paths}
    isolated_bin = _isolated_bin(tmp_path)
    _inject_second_source_quarantine_failure(
        isolated_bin / "Invoke-StaleClaimSweep.ps1",
        failure_body="""
                [System.IO.File]::WriteAllText(
                    [string]$preparedPlans[0].archive_temp_path,
                    'FOREIGN-ARCHIVE-TEMP'
                )
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    combined = completed.stdout + completed.stderr
    normalized = " ".join(combined.split())
    assert completed.returncode != 0
    assert (
        "path reappeared after the batch archive temp was moved" in normalized
    )
    assert {path: path.read_bytes() for path in claim_paths} == before
    foreign_temps = list(
        (runtime_root / "work_queue" / "done").glob("*.tmp.*")
    )
    assert len(foreign_temps) == 1
    assert foreign_temps[0].read_text(encoding="utf-8") == (
        "FOREIGN-ARCHIVE-TEMP"
    )


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_committed_cleanup_retains_reappeared_foreign_archive_temp(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "committed-foreign-temp"
    claim_path = _write_stale_claim(runtime_root, task_id)
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    source = sweep_path.read_text(encoding="utf-8")
    cleanup_start = (
        "    $cleanupFailures = New-Object "
        "System.Collections.Generic.List[string]"
    )
    injected = """
    [System.IO.File]::WriteAllText(
        [string]$preparedPlans[0].archive_temp_path,
        'FOREIGN-COMMITTED-TEMP'
    )
""".strip()
    assert source.count(cleanup_start) == 1
    sweep_path.write_text(
        source.replace(
            cleanup_start,
            f"{injected}\n{cleanup_start}",
            1,
        ),
        encoding="utf-8",
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
        warning_action_stop=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "archive temp cleanup failed" in combined
    assert not claim_path.exists()
    foreign_temps = list(
        (runtime_root / "work_queue" / "done").glob("*.tmp.*")
    )
    assert len(foreign_temps) == 1
    assert foreign_temps[0].read_text(encoding="utf-8") == (
        "FOREIGN-COMMITTED-TEMP"
    )
    assert len(
        list(
            (runtime_root / "work_queue" / "done").glob(
                f"{task_id}*.stale_lease.json"
            )
        )
    ) == 1


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_post_restore_active_replacement_keeps_exact_recovery(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_ids = ("postrestore-active-first", "postrestore-active-second")
    claim_paths = [
        _write_stale_claim(runtime_root, task_id) for task_id in task_ids
    ]
    before = {path: path.read_bytes() for path in claim_paths}
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    _inject_second_source_quarantine_failure(
        sweep_path,
        failure_body="",
    )
    _inject_after_marker(
        sweep_path,
        marker=(
            "            # STALE V2 MARKER: restored source verified before "
            "recovery retention."
        ),
        body="""
            if ([string]$plan.task_id -ceq 'postrestore-active-first') {
                [System.IO.File]::Delete([string]$plan.file.FullName)
                [System.IO.File]::WriteAllText(
                    [string]$plan.file.FullName,
                    'FOREIGN-ACTIVE-AFTER-RESTORE'
                )
            }
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    assert completed.returncode != 0
    assert claim_paths[0].read_text(encoding="utf-8") == (
        "FOREIGN-ACTIVE-AFTER-RESTORE"
    )
    assert claim_paths[1].read_bytes() == before[claim_paths[1]]
    recovery_paths = [
        *claim_paths[0].parent.glob(
            f"{claim_paths[0].name}.stale-backup.*"
        ),
        *claim_paths[0].parent.glob(
            f"{claim_paths[0].name}.stale-quarantine.*"
        ),
    ]
    assert any(
        path.read_bytes() == before[claim_paths[0]]
        for path in recovery_paths
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_committed_quarantine_replacement_is_retained_not_deleted(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "committed-foreign-quarantine"
    claim_path = _write_stale_claim(runtime_root, task_id)
    original = claim_path.read_bytes()
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    _inject_after_marker(
        sweep_path,
        marker=(
            "                    # STALE V2 MARKER: exact-handle committed "
            "quarantine cleanup."
        ),
        body="""
                    [System.IO.File]::Delete(
                        [string]$cleanupEntry.path
                    )
                    [System.IO.File]::WriteAllText(
                        [string]$cleanupEntry.path,
                        'FOREIGN-QUARANTINE-AFTER-VERIFY'
                    )
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
        warning_action_stop=True,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode == 0, combined
    assert "committed but ancillary cleanup failed" in combined
    assert not claim_path.exists()
    quarantines = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-quarantine.*")
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_text(encoding="utf-8") == (
        "FOREIGN-QUARANTINE-AFTER-VERIFY"
    )
    backups = list(
        claim_path.parent.glob(f"{claim_path.name}.stale-backup.*")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == original
    events = [
        json.loads(line)
        for line in (
            runtime_root / "shared" / "events.jsonl"
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert [(event["task_id"], event["status"]) for event in events] == [
        (task_id, "stale_lease")
    ]


@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_archive_temp_create_new_preserves_preexisting_foreign_file(
    tmp_path: Path,
    powershell: str,
) -> None:
    runtime_root = tmp_path / "bridge-runtime"
    task_id = "preexisting-foreign-archive-temp"
    claim_path = _write_stale_claim(runtime_root, task_id)
    original = claim_path.read_bytes()
    isolated_bin = _isolated_bin(tmp_path)
    sweep_path = isolated_bin / "Invoke-StaleClaimSweep.ps1"
    _inject_after_marker(
        sweep_path,
        marker=(
            "            # STALE V2 MARKER: create archive temp from trusted "
            "bytes."
        ),
        body="""
            [System.IO.File]::WriteAllText(
                [string]$plan.archive_temp_path,
                'FOREIGN-PREEXISTING-ARCHIVE-TEMP'
            )
""".strip(),
    )

    completed = _run_sweep(
        powershell=powershell,
        runtime_root=runtime_root,
        script_root=isolated_bin,
    )

    assert completed.returncode != 0
    assert claim_path.read_bytes() == original
    foreign_temps = list(
        (runtime_root / "work_queue" / "done").glob("*.tmp.*")
    )
    assert len(foreign_temps) == 1
    assert foreign_temps[0].read_text(encoding="utf-8") == (
        "FOREIGN-PREEXISTING-ARCHIVE-TEMP"
    )
    assert not (runtime_root / "shared" / "events.jsonl").exists()


def test_rollback_never_removes_done_directory_by_pathname() -> None:
    source = (
        BRIDGE_BIN / "Invoke-StaleClaimSweep.ps1"
    ).read_text(encoding="utf-8")

    assert "Remove-Item -LiteralPath $DoneDir" not in source
    assert "Remove-Item -Path $DoneDir" not in source
