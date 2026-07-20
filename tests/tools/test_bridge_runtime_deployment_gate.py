# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_runtime_deployment_gate.py"
COLLECTOR = ROOT / "tools" / "collect_bridge_runtime_windows_evidence.ps1"
sys.path.insert(0, str(ROOT))

import tools.bridge_runtime_deployment_gate as gate  # noqa: E402

from tools.bridge_runtime_deployment_gate import (  # noqa: E402
    ACTIVATION_HOLD,
    EVIDENCE_SCHEMA,
    EXIT_MATCH_TEST_ONLY,
    MANIFEST_PATH,
    RefusalError,
    _discover_runtime_dependencies,
    _evaluate_bridge_runtime_deployment,
    _gate_python_executable,
    _git_environment,
    _normalize_windows_evidence,
    _strict_relative_path,
    _stat_is_reparse,
    _validate_manifest,
    audit_bridge_runtime_deployment,
    audit_bridge_runtime_deployment_for_test,
    build_parser,
    command_digest,
    dependency_closure_digest,
    windows_command_line_to_argv,
)


GIT_EXECUTABLE = Path(shutil.which("git") or "")
REMOTE_URL = "https://example.invalid/waggledance.git"
HOST_ID = "a" * 64
COLLECTOR_SID = "S-1-5-21-1000"
DEFINITION_SHA = "d" * 64
NOW = datetime(2026, 7, 20, 12, 0, 1, 500000, tzinfo=timezone.utc)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        [str(GIT_EXECUTABLE), "-C", str(repo), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _file_definition(item_id: str, path: Path) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "id": item_id,
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _blob_definition(
    *,
    item_id: str,
    source_path: str,
    runtime_path: str,
    payload: bytes,
    dependency_ids: list[str],
) -> dict[str, object]:
    return {
        "id": item_id,
        "source_path": source_path,
        "runtime_path": runtime_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "dependency_ids": dependency_ids,
    }


@dataclass
class GateFixture:
    repo: Path
    runtime_root: Path
    interpreter: Path
    commit: str
    manifest: dict[str, Any]
    evidence: dict[str, Any]

    def audit(
        self,
        *,
        collector: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        now: datetime = NOW,
        expected_commit: str | None = None,
    ) -> dict[str, object]:
        selected = collector if collector is not None else (lambda _: self.evidence)
        return audit_bridge_runtime_deployment_for_test(
            expected_commit=expected_commit or self.commit,
            collector=selected,
            repo=self.repo,
            runtime_root=self.runtime_root,
            git_executable=GIT_EXECUTABLE,
            now_utc=now,
        )


def _ready_manifest(
    *,
    repo: Path,
    runtime_root: Path,
    interpreter: Path,
    entry_payload: bytes,
    writer_payload: bytes,
    collector_payload: bytes,
) -> dict[str, Any]:
    git_tool = _file_definition("git", GIT_EXECUTABLE)
    powershell_tool = _file_definition("powershell", interpreter)
    python_gate_tool = _file_definition("python-gate", _gate_python_executable())
    entry_blob = _blob_definition(
        item_id="entry",
        source_path=".agent-bridge/bin/Entry.ps1",
        runtime_path="bin/Entry.ps1",
        payload=entry_payload,
        dependency_ids=["writer"],
    )
    writer_blob = _blob_definition(
        item_id="writer",
        source_path=".agent-bridge/bin/Writer.ps1",
        runtime_path="bin/Writer.ps1",
        payload=writer_payload,
        dependency_ids=[],
    )
    runtime_entry = runtime_root / "bin" / "Entry.ps1"
    command_tokens = [
        str(interpreter.resolve()),
        "-NoProfile",
        "-File",
        str(runtime_entry.resolve()),
        "-RuntimeRoot",
        str(runtime_root.resolve()),
    ]
    selected_tools = [powershell_tool]
    selected_blobs = [entry_blob, writer_blob]
    closure = dependency_closure_digest(
        command_tokens=command_tokens,
        toolchain=selected_tools,
        runtime_blobs=selected_blobs,
    )
    process_action = {
        "id": "consumer",
        "required_count": 1,
        "command_tokens": command_tokens,
        "command_sha256": command_digest(command_tokens),
        "executable_toolchain_id": "powershell",
        "toolchain_ids": ["powershell"],
        "entrypoint_blob_id": "entry",
        "dependency_blob_ids": ["entry", "writer"],
        "closure_sha256": closure,
        "owner_sid": COLLECTOR_SID,
    }
    task_action = {
        "id": "watcher",
        "task_path": "\\",
        "task_name": "wd-fixture-watcher",
        "enabled": True,
        "principal_sid": COLLECTOR_SID,
        "run_level": "1",
        "working_directory": str(runtime_root.resolve()),
        "action_tokens": command_tokens,
        "action_sha256": command_digest(command_tokens),
        "executable_toolchain_id": "powershell",
        "toolchain_ids": ["powershell"],
        "entrypoint_blob_id": "entry",
        "dependency_blob_ids": ["entry", "writer"],
        "closure_sha256": closure,
        "definition_sha256": DEFINITION_SHA,
    }
    return {
        "schema": "wd.bridge_runtime_deployment.v2",
        "activation_state": "ready",
        "protocol_stage": "v1_fail_closed",
        "canonical": {
            "source_root": str(repo.resolve()),
            "git_common_dir": str((repo / ".git").resolve()),
            "runtime_root": str(runtime_root.resolve()),
        },
        "git_policy": {
            "origin_remote_url": REMOTE_URL,
            "require_head_equals_origin_main": True,
            "reject_replace_refs": True,
            "reject_grafts": True,
            "reject_alternates": True,
            "reject_shallow_or_promisor": True,
        },
        "host_policy": {
            "expected_host_identity_sha256": HOST_ID,
            "expected_collector_sid": COLLECTOR_SID,
            "require_elevated": True,
            "sample_gap_min_ms": 250,
            "sample_gap_max_ms": 2000,
            "collection_max_ms": 5000,
            "evidence_max_age_ms": 10000,
        },
        "collector": {
            "source_path": "tools/collect_bridge_runtime_windows_evidence.ps1",
            "sha256": hashlib.sha256(collector_payload).hexdigest(),
            "size": len(collector_payload),
            "powershell_toolchain_id": "powershell",
        },
        "toolchain": [git_tool, powershell_tool, python_gate_tool],
        "runtime_blobs": [entry_blob, writer_blob],
        "actions": {
            "processes": [process_action],
            "scheduled_tasks": [task_action],
        },
        "pending_blockers": [],
    }


def _hold_manifest(repo: Path, runtime_root: Path) -> dict[str, Any]:
    return {
        "schema": "wd.bridge_runtime_deployment.v2",
        "activation_state": ACTIVATION_HOLD,
        "protocol_stage": "v1_fail_closed",
        "canonical": {
            "source_root": str(repo.resolve()),
            "git_common_dir": str((repo / ".git").resolve()),
            "runtime_root": str(runtime_root.resolve()),
        },
        "git_policy": {
            "origin_remote_url": REMOTE_URL,
            "require_head_equals_origin_main": True,
            "reject_replace_refs": True,
            "reject_grafts": True,
            "reject_alternates": True,
            "reject_shallow_or_promisor": True,
        },
        "host_policy": {
            "expected_host_identity_sha256": None,
            "expected_collector_sid": None,
            "require_elevated": True,
            "sample_gap_min_ms": 250,
            "sample_gap_max_ms": 2000,
            "collection_max_ms": 10000,
            "evidence_max_age_ms": 10000,
        },
        "collector": {
            "source_path": "tools/collect_bridge_runtime_windows_evidence.ps1",
            "sha256": None,
            "size": None,
            "powershell_toolchain_id": None,
        },
        "toolchain": [],
        "runtime_blobs": [],
        "actions": {"processes": [], "scheduled_tasks": []},
        "pending_blockers": ["fixture activation is intentionally on hold"],
    }


def _evidence(manifest: dict[str, Any], interpreter: Path) -> dict[str, Any]:
    process_action = manifest["actions"]["processes"][0]
    task_action = manifest["actions"]["scheduled_tasks"][0]
    executable_sha = next(
        item["sha256"]
        for item in manifest["toolchain"]
        if item["id"] == "powershell"
    )
    host = {
        "host_identity_sha256": HOST_ID,
        "boot_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "boot_time_utc": "2026-07-20T08:00:00.000000Z",
        "collector_sid": COLLECTOR_SID,
        "is_elevated": True,
    }
    process = {
        "pid": 4242,
        "parent_pid": 100,
        "parent_creation_time_utc": "2026-07-20T10:00:00.000000Z",
        "creation_time_utc": "2026-07-20T11:00:00.000000Z",
        "executable_path": str(interpreter.resolve()),
        "executable_sha256": executable_sha,
        "command_tokens": process_action["command_tokens"],
        "command_sha256": process_action["command_sha256"],
        "owner_sid": COLLECTOR_SID,
    }
    task = {
        "task_path": task_action["task_path"],
        "task_name": task_action["task_name"],
        "enabled": task_action["enabled"],
        "principal_sid": task_action["principal_sid"],
        "run_level": task_action["run_level"],
        "working_directory": task_action["working_directory"],
        "action_tokens": task_action["action_tokens"],
        "action_sha256": task_action["action_sha256"],
        "definition_sha256": task_action["definition_sha256"],
    }
    return {
        "schema": EVIDENCE_SCHEMA,
        "collection": {
            "started_at_utc": "2026-07-20T11:59:59.900000Z",
            "completed_at_utc": "2026-07-20T12:00:00.600000Z",
            "started_monotonic_ns": 900_000_000,
            "completed_monotonic_ns": 1_600_000_000,
        },
        "samples": [
            {
                "label": "A",
                "captured_at_utc": "2026-07-20T12:00:00.000000Z",
                "monotonic_ns": 1_000_000_000,
                "host": deepcopy(host),
                "processes": [deepcopy(process)],
                "scheduled_tasks": [deepcopy(task)],
            },
            {
                "label": "B",
                "captured_at_utc": "2026-07-20T12:00:00.500000Z",
                "monotonic_ns": 1_500_000_000,
                "host": deepcopy(host),
                "processes": [deepcopy(process)],
                "scheduled_tasks": [deepcopy(task)],
            },
        ],
    }


def _raw_evidence(fixture: GateFixture) -> dict[str, Any]:
    command = fixture.manifest["actions"]["processes"][0]["command_tokens"]
    task = fixture.manifest["actions"]["scheduled_tasks"][0]
    parent_image = _gate_python_executable()
    parent = {
        "pid": 100,
        "parent_pid": 0,
        "creation_time_utc": "2026-07-20T10:00:00.000000Z",
        "executable_path": str(parent_image),
        "command_line": subprocess.list2cmdline([str(parent_image)]),
        "owner_sid": COLLECTOR_SID,
        "owner_error": None,
    }
    child = {
        "pid": 4242,
        "parent_pid": 100,
        "creation_time_utc": "2026-07-20T11:00:00.000000Z",
        "executable_path": str(fixture.interpreter.resolve()),
        "command_line": subprocess.list2cmdline(command),
        "owner_sid": COLLECTOR_SID,
        "owner_error": None,
    }
    raw_task = {
        "task_path": task["task_path"],
        "task_name": task["task_name"],
        "enabled": task["enabled"],
        "state": 3,
        "principal_sid": task["principal_sid"],
        "run_level": task["run_level"],
        "actions": [
            {
                "type": 0,
                "path": task["action_tokens"][0],
                "arguments": subprocess.list2cmdline(task["action_tokens"][1:]),
                "working_directory": task["working_directory"],
            }
        ],
        "definition_xml": "<Task fixture='true'/>",
    }
    host = {
        "machine_guid": "machine-guid",
        "smbios_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "system_volume_serial": "ABCD1234",
        "boot_time_utc": "2026-07-20T08:00:00.000000Z",
        "collector_sid": COLLECTOR_SID,
        "is_elevated": True,
    }
    samples = []
    for label, captured, ticks in (
        ("A", "2026-07-20T12:00:00.000000Z", 1_000_000_000),
        ("B", "2026-07-20T12:00:00.500000Z", 1_500_000_000),
    ):
        samples.append(
            {
                "label": label,
                "captured_at_utc": captured,
                "monotonic_ticks": ticks,
                "host": deepcopy(host),
                "processes": [deepcopy(parent), deepcopy(child)],
                "scheduled_tasks": [deepcopy(raw_task)],
            }
        )
    return {
        "schema": "wd.bridge_runtime.windows_raw.v2",
        "collector_pid": 777,
        "stopwatch_frequency": 1_000_000_000,
        "collector_started_at_utc": "2026-07-20T11:59:59.900000Z",
        "collector_completed_at_utc": "2026-07-20T12:00:00.600000Z",
        "collector_started_ticks": 900_000_000,
        "collector_completed_ticks": 1_600_000_000,
        "samples": samples,
    }


def _commit_manifest(fixture: GateFixture, manifest: dict[str, Any]) -> None:
    _write_json(fixture.repo / MANIFEST_PATH, manifest)
    _git(fixture.repo, "add", MANIFEST_PATH)
    _git(fixture.repo, "commit", "-m", "update fixture manifest")
    fixture.commit = _git(fixture.repo, "rev-parse", "HEAD")
    _git(fixture.repo, "update-ref", "refs/remotes/origin/main", fixture.commit)
    fixture.manifest = manifest


def _fixture(tmp_path: Path, *, hold: bool = False) -> GateFixture:
    if not GIT_EXECUTABLE.is_file():
        pytest.skip("git executable is required")
    repo = tmp_path / "source"
    runtime_root = tmp_path / "runtime"
    interpreter = tmp_path / "host-bin" / "powershell.exe"
    repo.mkdir()
    runtime_root.mkdir()
    interpreter.parent.mkdir()
    interpreter.write_bytes(b"fixture powershell binary\n")
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "Bridge Gate Tests")
    _git(repo, "remote", "add", "origin", REMOTE_URL)

    entry_payload = b"& (Join-Path $PSScriptRoot 'Writer.ps1')\n"
    writer_payload = b"Write-Output 'fixture writer'\n"
    collector_payload = b"# fixture collector; never executed\n"
    source_entry = repo / ".agent-bridge" / "bin" / "Entry.ps1"
    source_writer = repo / ".agent-bridge" / "bin" / "Writer.ps1"
    collector_path = repo / "tools" / "collect_bridge_runtime_windows_evidence.ps1"
    for path, payload in (
        (source_entry, entry_payload),
        (source_writer, writer_payload),
        (collector_path, collector_payload),
        (runtime_root / "bin" / "Entry.ps1", entry_payload),
        (runtime_root / "bin" / "Writer.ps1", writer_payload),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    manifest = (
        _hold_manifest(repo, runtime_root)
        if hold
        else _ready_manifest(
            repo=repo,
            runtime_root=runtime_root,
            interpreter=interpreter,
            entry_payload=entry_payload,
            writer_payload=writer_payload,
            collector_payload=collector_payload,
        )
    )
    _write_json(repo / MANIFEST_PATH, manifest)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "fixture bridge deployment")
    commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", commit)
    evidence = (
        {"schema": EVIDENCE_SCHEMA, "samples": []}
        if hold
        else _evidence(manifest, interpreter)
    )
    return GateFixture(
        repo=repo,
        runtime_root=runtime_root,
        interpreter=interpreter,
        commit=commit,
        manifest=manifest,
        evidence=evidence,
    )


def _codes(report: dict[str, object]) -> set[str]:
    return {str(item["code"]) for item in report["blockers"]}


def test_exact_fixture_match_is_never_authoritative(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    report = fixture.audit()

    assert report["decision"] == "MATCH_TEST_ONLY"
    assert report["exit_code"] == EXIT_MATCH_TEST_ONLY
    assert report["ok"] is False
    assert report["matches_expected"] is True
    assert report["blockers"] == []
    assert all(value is False for value in report["authority"].values())
    live = report["observations"]["live_evidence"]
    assert live["process_identity_count"] == 1
    assert live["scheduled_task_count"] == 1
    assert live["valid_until_utc"].endswith("Z")


def test_injected_exception_is_still_match_test_only_exit_three(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def broken(_: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("synthetic collector failure")

    report = fixture.audit(collector=broken)

    assert report["decision"] == "MATCH_TEST_ONLY"
    assert report["exit_code"] == 3
    assert report["ok"] is False
    assert report["matches_expected"] is False
    assert "unexpected_audit_error" in _codes(report)


def test_cli_has_no_snapshot_fixture_or_path_override() -> None:
    parser = build_parser()

    for option in (
        "--process-snapshot",
        "--task-snapshot",
        "--fixture",
        "--repo",
        "--runtime-root",
    ):
        with pytest.raises(SystemExit):
            parser.parse_args(["--expected-commit", "a" * 40, option, "x"])


def test_production_api_rejects_injected_evidence() -> None:
    with pytest.raises(TypeError):
        audit_bridge_runtime_deployment(  # type: ignore[call-arg]
            expected_commit="a" * 40,
            collector=lambda _: {},
        )


def test_production_wrapper_forces_structural_match_to_live_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structural_match = {
        "schema": "wd.bridge_runtime_deployment_gate_report.v2",
        "ok": False,
        "matches_expected": True,
        "decision": "MATCH_TEST_ONLY",
        "exit_code": EXIT_MATCH_TEST_ONLY,
        "mode": "read_only",
        "blockers": [],
        "observations": {},
        "authority": gate.authority_flags(),
    }
    monkeypatch.setattr(
        gate,
        "_evaluate_bridge_runtime_deployment",
        lambda **_: deepcopy(structural_match),
    )
    monkeypatch.setattr(gate, "_gate_python_executable", lambda: GIT_EXECUTABLE)

    report = gate.audit_bridge_runtime_deployment(expected_commit="a" * 40)

    assert report["decision"] == "REFUSE"
    assert report["exit_code"] == 3
    assert report["ok"] is False
    assert report["matches_expected"] is False
    assert _codes(report) == {"live_authority_hold"}
    assert all(value is False for value in report["authority"].values())


def test_direct_private_injection_cannot_emit_authoritative_success(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    report = _evaluate_bridge_runtime_deployment(
        expected_commit=fixture.commit,
        collector=lambda _: fixture.evidence,
        repo=fixture.repo,
        runtime_root=fixture.runtime_root,
        git_executable=GIT_EXECUTABLE,
        python_executable=_gate_python_executable(),
        now_utc=NOW,
        verify_gate_source=False,
        enforce_canonical_production_paths=False,
        evidence_source="live_windows",
    )

    assert report["matches_expected"] is True
    assert report["decision"] == "MATCH_TEST_ONLY"
    assert report["exit_code"] == EXIT_MATCH_TEST_ONLY
    assert report["ok"] is False


def test_post_collection_clock_is_sampled_after_slow_collector(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    manifest = deepcopy(fixture.manifest)
    manifest["host_policy"]["evidence_max_age_ms"] = 250
    _commit_manifest(fixture, manifest)

    def slow_collector(_: dict[str, Any]) -> dict[str, Any]:
        evidence = deepcopy(fixture.evidence)
        completed = datetime.now(timezone.utc)
        start = completed - timedelta(milliseconds=700)
        sample_a = completed - timedelta(milliseconds=600)
        sample_b = completed - timedelta(milliseconds=100)
        evidence["collection"]["started_at_utc"] = start.isoformat()
        evidence["collection"]["completed_at_utc"] = completed.isoformat()
        evidence["samples"][0]["captured_at_utc"] = sample_a.isoformat()
        evidence["samples"][1]["captured_at_utc"] = sample_b.isoformat()
        boot = completed - timedelta(hours=4)
        parent = completed - timedelta(hours=2)
        child = completed - timedelta(hours=1)
        for sample in evidence["samples"]:
            sample["host"]["boot_time_utc"] = boot.isoformat()
            sample["processes"][0]["parent_creation_time_utc"] = parent.isoformat()
            sample["processes"][0]["creation_time_utc"] = child.isoformat()
        time.sleep(1.05)
        return evidence

    report = _evaluate_bridge_runtime_deployment(
        expected_commit=fixture.commit,
        collector=slow_collector,
        repo=fixture.repo,
        runtime_root=fixture.runtime_root,
        git_executable=GIT_EXECUTABLE,
        python_executable=_gate_python_executable(),
        now_utc=None,
        verify_gate_source=False,
        enforce_canonical_production_paths=False,
        evidence_source="injected_fixture",
    )

    assert "evidence_stale" in _codes(report)
    assert report["exit_code"] == EXIT_MATCH_TEST_ONLY


def test_hold_manifest_skips_collector(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, hold=True)
    calls = 0

    def forbidden(_: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise AssertionError("collector must not run while manifest is HOLD")

    report = fixture.audit(collector=forbidden)

    assert calls == 0
    assert report["decision"] == "MATCH_TEST_ONLY"
    assert report["exit_code"] == 3
    assert "activation_hold" in _codes(report)


def test_repository_manifest_is_intentionally_hashless_hold() -> None:
    manifest = json.loads((ROOT / MANIFEST_PATH).read_text(encoding="utf-8"))

    source_root = PureWindowsPath(r"C:\Python\project2")
    assert manifest["canonical"] == {
        "source_root": str(source_root),
        "git_common_dir": str(source_root / ".git"),
        "runtime_root": str(
            PureWindowsPath(r"C:\Python\project2-master\.agent-bridge")
        ),
    }

    validation_manifest = deepcopy(manifest)
    if os.name != "nt":
        validation_manifest["canonical"] = {
            "source_root": str(ROOT.resolve()),
            "git_common_dir": str((ROOT / ".git").resolve()),
            "runtime_root": str((ROOT / ".agent-bridge").resolve()),
        }
    _validate_manifest(validation_manifest)

    assert manifest["activation_state"] == ACTIVATION_HOLD
    assert manifest["host_policy"]["expected_host_identity_sha256"] is None
    assert manifest["collector"]["sha256"] is None
    assert manifest["toolchain"] == []
    assert manifest["runtime_blobs"] == []
    assert manifest["actions"] == {"processes": [], "scheduled_tasks": []}


def test_dirty_canonical_repo_is_refused(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    report = fixture.audit()

    assert report["matches_expected"] is False
    assert "deployment_definition_refused" in _codes(report)
    assert "not clean" in report["blockers"][0]["detail"]


def test_ignored_untracked_dirt_is_not_hidden(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    ignore = fixture.repo / ".gitignore"
    ignore.write_text("ignored.txt\n", encoding="utf-8", newline="\n")
    _git(fixture.repo, "add", ".gitignore")
    _git(fixture.repo, "commit", "-m", "add fixture ignore rule")
    fixture.commit = _git(fixture.repo, "rev-parse", "HEAD")
    _git(fixture.repo, "update-ref", "refs/remotes/origin/main", fixture.commit)
    (fixture.repo / "ignored.txt").write_text(
        "must remain visible\n", encoding="utf-8", newline="\n"
    )

    report = fixture.audit()

    assert "deployment_definition_refused" in _codes(report)
    assert "ignored.txt" in report["blockers"][0]["detail"]


def test_filter_normalized_worktree_bytes_cannot_hide_drift(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest_path = fixture.repo / MANIFEST_PATH
    payload = manifest_path.read_bytes()
    assert b"\r\n" not in payload
    manifest_path.write_bytes(payload.replace(b"\n", b"\r\n"))

    with pytest.raises(RefusalError, match="bytes differ from index blob"):
        gate._audit_index_worktree_bytes(
            fixture.repo, git_executable=GIT_EXECUTABLE
        )


def test_origin_main_must_equal_exact_head(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    previous = fixture.commit
    _git(fixture.repo, "commit", "--allow-empty", "-m", "local-only head")
    new_head = _git(fixture.repo, "rev-parse", "HEAD")
    assert new_head != previous

    report = fixture.audit(expected_commit=new_head)

    assert report["matches_expected"] is False
    assert "deployment_definition_refused" in _codes(report)
    assert "origin/main" in report["blockers"][0]["detail"]


@pytest.mark.parametrize(
    "hostile",
    [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_REPLACE_REF_BASE",
        "GIT_CONFIG_COUNT",
        "GIT_NAMESPACE",
        "GIT_SHALLOW_FILE",
    ],
)
def test_git_environment_strips_hostile_ambient_variables(
    monkeypatch: pytest.MonkeyPatch, hostile: str
) -> None:
    monkeypatch.setenv(hostile, "attacker-controlled")

    environment = _git_environment()

    if hostile in {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_REPLACE_REF_BASE",
        "GIT_CONFIG_COUNT",
        "GIT_NAMESPACE",
        "GIT_SHALLOW_FILE",
    }:
        assert hostile not in environment
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize("kind", ["alternates", "grafts", "replace"])
def test_git_indirections_are_refused(tmp_path: Path, kind: str) -> None:
    fixture = _fixture(tmp_path)
    common = fixture.repo / ".git"
    if kind == "alternates":
        target = common / "objects" / "info" / "alternates"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(fixture.repo / ".git" / "objects"), encoding="utf-8")
    elif kind == "grafts":
        target = common / "info" / "grafts"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"{fixture.commit}\n", encoding="utf-8")
    else:
        _git(
            fixture.repo,
            "update-ref",
            f"refs/replace/{fixture.commit}",
            fixture.commit,
        )

    report = fixture.audit()

    assert report["matches_expected"] is False
    assert "deployment_definition_refused" in _codes(report)


@pytest.mark.parametrize("location", ["refs", "objects"])
def test_git_metadata_reparse_junctions_are_refused(
    tmp_path: Path, location: str
) -> None:
    fixture = _fixture(tmp_path)
    common = fixture.repo / ".git"
    target = tmp_path / f"outside-{location}"
    target.mkdir()
    junction = common / location / "attacker-junction"
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr}")
    try:
        report = fixture.audit()
    finally:
        os.rmdir(junction)

    assert "deployment_definition_refused" in _codes(report)
    assert "link/reparse" in report["blockers"][0]["detail"]


def test_windows_reparse_attribute_is_always_recognized() -> None:
    details = SimpleNamespace(
        st_mode=stat.S_IFREG,
        st_file_attributes=0x400,
    )

    assert _stat_is_reparse(details) is True


def test_git_config_include_is_refused_before_effective_partial_clone(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    included = tmp_path / "attacker.cfg"
    included.write_text(
        "[extensions]\n\tpartialClone = origin\n", encoding="utf-8"
    )
    config = fixture.repo / ".git" / "config"
    with config.open("a", encoding="utf-8") as stream:
        stream.write(f"\n[include]\n\tpath = {included.as_posix()}\n")

    report = fixture.audit()

    assert "deployment_definition_refused" in _codes(report)
    assert "config" in report["blockers"][0]["detail"].casefold()
    assert "forbidden" in report["blockers"][0]["detail"]


@pytest.mark.parametrize(
    ("snippet", "expected_key"),
    [
        ("[core]\n\tattributesFile = C:/attacker/attributes\n", "attributesfile"),
        ("[core]\n\texcludesFile = C:/attacker/excludes\n", "excludesfile"),
        ("[core]\n\thooksPath = C:/attacker/hooks\n", "hookspath"),
        ("[filter \"evil\"]\n\tclean = cmd.exe /c evil\n", "filter.evil.clean"),
        ("[diff \"evil\"]\n\ttextconv = cmd.exe /c evil\n", "diff.evil.textconv"),
        ("[diff]\n\texternal = C:/attacker/diff.exe\n", "diff.external"),
        ("[merge \"evil\"]\n\tdriver = cmd.exe /c evil %O\n", "merge.evil.driver"),
        ("[alias]\n\tstatus = !cmd.exe /c evil\n", "alias.status"),
        ("[fsck]\n\tskipList = C:/attacker/skip-list\n", "fsck.skiplist"),
        ("[fsck]\n\tmissingEmail = ignore\n", "fsck.missingemail"),
        ("[include]\n\tpath = C:/attacker/config\n", "include.path"),
        ("[extensions]\n\tpartialClone = origin\n", "extensions.partialclone"),
        ("[remote \"origin\"]\n\tpromisor = true\n", "remote.origin.promisor"),
        ("[extensions]\n\tworktreeConfig = true\n", "extensions.worktreeconfig"),
        ("[core]\n\tpager = C:/attacker/pager.exe\n", "core.pager"),
    ],
)
def test_hostile_local_config_refuses_before_any_git_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snippet: str,
    expected_key: str,
) -> None:
    fixture = _fixture(tmp_path)
    config = fixture.repo / ".git" / "config"
    with config.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"\n{snippet}")
    git_calls: list[tuple[str, ...]] = []

    def forbidden_git(_repo: Path, *args: str, **_kwargs: object) -> object:
        git_calls.append(args)
        raise AssertionError("hostile config must refuse before invoking Git")

    monkeypatch.setattr(gate, "_git", forbidden_git)

    report = fixture.audit()

    assert git_calls == []
    assert "deployment_definition_refused" in _codes(report)
    assert expected_key in report["blockers"][0]["detail"].casefold()


def test_local_config_is_byte_compared_after_git_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    original_git = gate._git
    changed = False

    def mutate_after_fsck(
        repo: Path, *args: str, **kwargs: object
    ) -> gate.GitResult:
        nonlocal changed
        result = original_git(repo, *args, **kwargs)
        if args and args[0] == "fsck" and not changed:
            changed = True
            config = fixture.repo / ".git" / "config"
            with config.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("\n# changed during audit\n")
        return result

    monkeypatch.setattr(gate, "_git", mutate_after_fsck)

    report = fixture.audit()

    assert changed is True
    assert "deployment_definition_refused" in _codes(report)
    assert "config changed" in report["blockers"][0]["detail"]


@pytest.mark.parametrize(
    ("section", "preserved_key"),
    [
        (
            '[remote "Origin"]\n\turl = https://example.invalid/other.git\n'
            "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
            "remote.Origin.url",
        ),
        (
            '[remote "oRiGiN"]\n\turl = https://example.invalid/other.git\n'
            "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
            "remote.oRiGiN.url",
        ),
        (
            '[branch "MAIN"]\n\tremote = origin\n\tmerge = refs/heads/main\n',
            "branch.MAIN.remote",
        ),
        (
            '[branch "Main"]\n\tremote = origin\n\tmerge = refs/heads/main\n',
            "branch.Main.remote",
        ),
    ],
)
def test_git_config_subsection_identity_is_case_sensitive(
    tmp_path: Path, section: str, preserved_key: str
) -> None:
    fixture = _fixture(tmp_path)
    config = fixture.repo / ".git" / "config"
    with config.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"\n{section}")

    report = fixture.audit()

    assert "deployment_definition_refused" in _codes(report)
    assert preserved_key in report["blockers"][0]["detail"]


def test_git_config_section_and_key_names_casefold_with_exact_main_subsection(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    config = fixture.repo / ".git" / "config"
    with config.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            '\n[BRANCH "main"]\n\tREMOTE = origin\n'
            "\tMERGE = refs/heads/main\n"
        )

    report = fixture.audit()

    assert report["matches_expected"] is True
    assert report["decision"] == "MATCH_TEST_ONLY"


def test_ready_collector_config_comment_mutation_is_refused_after_reduction(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate_config(_: dict[str, Any]) -> dict[str, Any]:
        config = fixture.repo / ".git" / "config"
        with config.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write("\n# harmless-looking collector-time mutation\n")
        return fixture.evidence

    report = fixture.audit(collector=mutate_config)

    assert report["matches_expected"] is False
    assert "deployment_definition_refused" in _codes(report)
    assert "config changed" in report["blockers"][0]["detail"]


def test_ab_pid_reuse_or_restart_is_refused(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.evidence["samples"][1]["processes"][0][
        "creation_time_utc"
    ] = "2026-07-20T12:00:00.250000Z"

    report = fixture.audit()

    assert "ab_process_inventory_drift" in _codes(report)
    assert report["matches_expected"] is False


def test_ab_parent_exit_or_pid_reuse_is_refused(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.evidence["samples"][1]["processes"][0][
        "parent_creation_time_utc"
    ] = "2026-07-20T10:00:01.000000Z"

    report = fixture.audit()

    assert "ab_process_inventory_drift" in _codes(report)
    assert report["matches_expected"] is False


def test_process_creation_must_be_within_boot_and_sample_window(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    for sample in fixture.evidence["samples"]:
        sample["processes"][0][
            "creation_time_utc"
        ] = "2026-07-20T07:59:59.000000Z"

    report = fixture.audit()

    assert "process_creation_outside_boot_sample_window" in _codes(report)


def test_ab_scheduled_task_definition_drift_is_refused(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.evidence["samples"][1]["scheduled_tasks"][0][
        "definition_sha256"
    ] = "e" * 64

    report = fixture.audit()

    assert "scheduled_task_definition_sha256_mismatch" in _codes(report)
    assert "ab_task_inventory_drift" in _codes(report)


@pytest.mark.parametrize(
    ("mutator", "expected_code"),
    [
        (
                lambda evidence: evidence["samples"][1]["host"].__setitem__(
                    "boot_id", "ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee"
                ),
            "ab_host_boot_id_drift",
        ),
        (
            lambda evidence: evidence["samples"][1].__setitem__(
                "monotonic_ns", 1_100_000_000
            ),
            "ab_sample_gap_out_of_bounds",
        ),
        (
            lambda evidence: evidence["samples"][1].__setitem__(
                "captured_at_utc", "2026-07-20T11:59:59.000000Z"
            ),
            "ab_time_not_monotonic",
        ),
    ],
)
def test_host_boot_and_freshness_drift_refuses(
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
    expected_code: str,
) -> None:
    fixture = _fixture(tmp_path)
    mutator(fixture.evidence)

    report = fixture.audit()

    assert expected_code in _codes(report)
    assert report["matches_expected"] is False


def test_stale_evidence_refuses(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)

    report = fixture.audit(now=NOW + timedelta(seconds=30))

    assert "evidence_stale" in _codes(report)


def test_unknown_bridge_process_and_task_refuse(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    unknown_process = deepcopy(fixture.evidence["samples"][0]["processes"][0])
    unknown_process["pid"] = 9898
    unknown_process["command_tokens"] = [
        str(fixture.interpreter.resolve()),
        "-File",
        str(fixture.runtime_root / "bin" / "Unknown.ps1"),
    ]
    unknown_process["command_sha256"] = command_digest(
        unknown_process["command_tokens"]
    )
    unknown_task = deepcopy(
        fixture.evidence["samples"][0]["scheduled_tasks"][0]
    )
    unknown_task["task_name"] = "wd-unknown-bridge-task"
    for sample in fixture.evidence["samples"]:
        sample["processes"].append(deepcopy(unknown_process))
        sample["scheduled_tasks"].append(deepcopy(unknown_task))

    report = fixture.audit()

    assert "unknown_bridge_process" in _codes(report)
    assert "unknown_bridge_scheduled_task" in _codes(report)


def test_raw_normalizer_binds_parent_creation_and_exact_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    raw = _raw_evidence(fixture)
    monkeypatch.setattr(gate, "_revalidate_windows_process", lambda *a, **k: None)

    normalized = _normalize_windows_evidence(
        raw,
        manifest=fixture.manifest,
        boot_before="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        boot_after="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    process = normalized["samples"][0]["processes"][0]
    assert process["pid"] == 4242
    assert process["parent_pid"] == 100
    assert process["parent_creation_time_utc"] == (
        "2026-07-20T10:00:00.000000Z"
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: raw.__setitem__("collector_pid", "777"),
        lambda raw: raw["samples"][0]["host"].__setitem__("is_elevated", 1),
        lambda raw: raw["samples"][0]["processes"][0].__setitem__("pid", "100"),
        lambda raw: raw["samples"][0]["scheduled_tasks"][0].__setitem__(
            "enabled", 1
        ),
        lambda raw: raw["samples"][0]["host"].__setitem__("unexpected", True),
    ],
)
def test_raw_normalizer_rejects_coercible_or_unknown_fields(
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
) -> None:
    fixture = _fixture(tmp_path)
    raw = _raw_evidence(fixture)
    mutator(raw)

    with pytest.raises(RefusalError):
        _normalize_windows_evidence(
            raw,
            manifest=fixture.manifest,
            boot_before="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            boot_after="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


@pytest.mark.parametrize("kind", ["pid", "task"])
def test_raw_normalizer_rejects_duplicate_identities(
    tmp_path: Path, kind: str
) -> None:
    fixture = _fixture(tmp_path)
    raw = _raw_evidence(fixture)
    if kind == "pid":
        raw["samples"][0]["processes"].append(
            deepcopy(raw["samples"][0]["processes"][0])
        )
    else:
        raw["samples"][0]["scheduled_tasks"].append(
            deepcopy(raw["samples"][0]["scheduled_tasks"][0])
        )

    with pytest.raises(RefusalError, match="duplicate raw"):
        _normalize_windows_evidence(
            raw,
            manifest=fixture.manifest,
            boot_before="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            boot_after="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


@pytest.mark.parametrize(
    "tokens",
    [
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-EncodedCommand", "QQA="],
        [r"C:\Windows\System32\cmd.exe", "/c", "echo bridge"],
    ],
)
def test_raw_normalizer_rejects_encoded_and_generic_descendant_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tokens: list[str],
) -> None:
    fixture = _fixture(tmp_path)
    raw = _raw_evidence(fixture)
    monkeypatch.setattr(gate, "_revalidate_windows_process", lambda *a, **k: None)
    descendant = {
        "pid": 5000,
        "parent_pid": 4242,
        "creation_time_utc": "2026-07-20T11:30:00.000000Z",
        "executable_path": tokens[0],
        "command_line": subprocess.list2cmdline(tokens),
        "owner_sid": COLLECTOR_SID,
        "owner_error": None,
    }
    for sample in raw["samples"]:
        sample["processes"].append(deepcopy(descendant))

    with pytest.raises(RefusalError, match="encoded|generic"):
        _normalize_windows_evidence(
            raw,
            manifest=fixture.manifest,
            boot_before="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            boot_after="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )


def test_raw_normalizer_includes_unknown_task_with_explicit_scoped_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _fixture(tmp_path)
    raw = _raw_evidence(fixture)
    monkeypatch.setattr(gate, "_revalidate_windows_process", lambda *a, **k: None)
    unknown = deepcopy(raw["samples"][0]["scheduled_tasks"][0])
    unknown["task_name"] = "unknown-explicit-scope"
    for sample in raw["samples"]:
        sample["scheduled_tasks"].append(deepcopy(unknown))

    normalized = _normalize_windows_evidence(
        raw,
        manifest=fixture.manifest,
        boot_before="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        boot_after="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    )

    assert len(normalized["samples"][0]["scheduled_tasks"]) == 2


def test_dependency_closure_must_be_complete_and_exact(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest = deepcopy(fixture.manifest)
    manifest["actions"]["processes"][0]["dependency_blob_ids"] = ["entry"]

    with pytest.raises(ValueError, match="dependency closure mismatch"):
        _validate_manifest(manifest)


def test_ready_manifest_must_pin_gate_python(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    manifest = deepcopy(fixture.manifest)
    manifest["toolchain"] = [
        item for item in manifest["toolchain"] if item["id"] != "python-gate"
    ]

    with pytest.raises(RefusalError, match="Python gate interpreter"):
        _validate_manifest(manifest)


@pytest.mark.parametrize(
    "suffix", ["psm1", "psd1", "dll", "config", "no_suffix", "embedded_config"]
)
def test_every_path_bearing_action_input_must_be_declared(
    tmp_path: Path, suffix: str
) -> None:
    fixture = _fixture(tmp_path)
    manifest = deepcopy(fixture.manifest)
    name = (
        "Hidden"
        if suffix == "no_suffix"
        else "Hidden.config"
        if suffix == "embedded_config"
        else f"Hidden.{suffix}"
    )
    action = manifest["actions"]["processes"][0]
    hidden_path = str(fixture.runtime_root / "bin" / name)
    action["command_tokens"].append(
        f"--config={hidden_path}" if suffix == "embedded_config" else hidden_path
    )
    action["command_sha256"] = command_digest(action["command_tokens"])
    tool_ids = action["toolchain_ids"]
    blob_ids = action["dependency_blob_ids"]
    action["closure_sha256"] = dependency_closure_digest(
        command_tokens=action["command_tokens"],
        toolchain=[
            item for item in manifest["toolchain"] if item["id"] in tool_ids
        ],
        runtime_blobs=[
            item for item in manifest["runtime_blobs"] if item["id"] in blob_ids
        ],
    )

    with pytest.raises(RefusalError, match="undeclared runtime dependency"):
        _validate_manifest(manifest)


def test_independent_dependency_discovery_rejects_unlisted_literal(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    definitions = {
        item["id"]: item for item in fixture.manifest["runtime_blobs"]
    }

    with pytest.raises(RefusalError, match="undeclared literal input"):
        _discover_runtime_dependencies(
            "entry", b"Import-Module './Hidden.psm1'\n", definitions
        )


@pytest.mark.parametrize(
    "value",
    [
        "C:relative/file.ps1",
        "C:/absolute/file.ps1",
        "folder/name:stream",
        "folder/CON",
        "folder/NUL.txt",
        "folder/name.",
        "folder/name ",
        "//server/share/file",
        ".",
        "../escape",
        "folder//file",
    ],
)
def test_windows_unsafe_relative_paths_are_refused(value: str) -> None:
    with pytest.raises(RefusalError):
        _strict_relative_path(value, "fixture path")


def test_runtime_dependency_hash_drift_refuses(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    (fixture.runtime_root / "bin" / "Writer.ps1").write_bytes(b"changed\n")

    report = fixture.audit()

    assert "runtime_blob_mismatch" in _codes(report)
    assert report["matches_expected"] is False


def test_interpreter_hash_drift_refuses(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.interpreter.write_bytes(b"changed host binary\n")

    report = fixture.audit()

    assert "toolchain_hash_mismatch" in _codes(report)
    assert report["matches_expected"] is False


def test_windows_command_line_tokenization_preserves_quoted_arguments() -> None:
    raw = (
        '"C:\\Program Files\\PowerShell\\7\\pwsh.exe" -NoProfile -File '
        '"C:\\Python\\project2-master\\.agent-bridge\\bin\\Entry.ps1" '
        '-Message "hello bridge"'
    )

    assert windows_command_line_to_argv(raw) == [
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        "-NoProfile",
        "-File",
        r"C:\Python\project2-master\.agent-bridge\bin\Entry.ps1",
        "-Message",
        "hello bridge",
    ]


def test_collector_source_has_read_only_contract() -> None:
    source = COLLECTOR.read_text(encoding="utf-8")
    forbidden = (
        "Register-ScheduledTask",
        "Unregister-ScheduledTask",
        "Set-ScheduledTask",
        "Enable-ScheduledTask",
        "Disable-ScheduledTask",
        "Stop-Process",
        "Start-Process",
        "Set-Content",
        "Add-Content",
        "Out-File",
        "Remove-Item",
        "Move-Item",
        "Copy-Item",
    )

    assert "Win32_Process" in source
    assert "Schedule.Service" in source
    assert "Get-OneSample -Label 'A'" in source
    assert "Get-OneSample -Label 'B'" in source
    for token in forbidden:
        assert token not in source
