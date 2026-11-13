"""Candidate fleet's read-only status config, tested only with synthetic paths.

The helper and Python session run locally against test-created files. This is
not an installed bundle, an actual agent, or semantic content-memory recall.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from tools import wd_bridge_mind_session as session


ROOT = Path(__file__).resolve().parents[2]
FLEET = ROOT / "ops/windows/reboot/wd-fleet.json"
HELPER = ROOT / "ops/windows/reboot/Get-WdMindReadConfig.ps1"
PS5 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
UUIDS = {name: str(uuid.UUID(int=index + 1)) for index, name in enumerate(LANES)}
pytestmark = pytest.mark.skipif(os.name != "nt" or not PS5.is_file(), reason="Windows PowerShell 5.1 required")


def test_candidate_fleet_has_one_common_enabled_read_mode_for_five_lanes():
    data = json.loads(FLEET.read_text(encoding="utf-8"))
    assert data["mind_read"] == {"enabled": True, "live_dir_mode": "shared_runtime_audit"}
    entries = [*data["lanes"], data["tools_supervisor"]]
    assert len(entries) == 5 and {row["agent"] for row in entries} == set(LANES)
    identifiers = [str(uuid.UUID(row["agent_uuid"])) for row in entries]
    assert len(set(identifiers)) == 5


def test_absent_publication_refuses_for_all_synthetic_lanes(tmp_path, monkeypatch, capsys):
    # Only the intended mode is copied. Fleet paths, roster and publication
    # bytes here are synthetic, not copied from the installed configuration.
    mode = json.loads(FLEET.read_text(encoding="utf-8"))["mind_read"]
    runtime = tmp_path / "synthetic-runtime"
    shared = runtime / "shared"
    audit = shared / ".codex-audit"
    audit.mkdir(parents=True)
    (audit / "mind-live").mkdir()
    (shared / "events.jsonl").write_text('{"type":"heartbeat"}\n', encoding="utf-8")
    (shared / "events.generation.json").write_text('{"generation":"fixture-g1"}', encoding="ascii")
    synthetic = {"runtime_root": str(runtime), "mind_read": mode,
                 "lanes": [{"agent": name, "agent_uuid": UUIDS[name]} for name in LANES if name != "codex-tools-1"],
                 "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": UUIDS["codex-tools-1"]}}
    raw = json.dumps(synthetic, sort_keys=True).encode("utf-8")
    fleet = tmp_path / "synthetic-fleet.json"
    fleet.write_bytes(raw)
    system_root = os.environ["SystemRoot"]
    env = {"SystemRoot": system_root, "WINDIR": system_root, "PATH": str(Path(system_root) / "System32"),
           "TEMP": str(tmp_path), "TMP": str(tmp_path), "USERPROFILE": str(tmp_path),
           "WD_TEST_HELPER": str(HELPER), "WD_TEST_FLEET": str(fleet),
           "WD_TEST_DIGEST": hashlib.sha256(raw).hexdigest()}
    script = r'''
$ErrorActionPreference='Stop'
. $env:WD_TEST_HELPER
$value=Get-WdMindReadConfig -FleetPath $env:WD_TEST_FLEET -FleetSha256 $env:WD_TEST_DIGEST
if ($null -eq $value) { throw 'synthetic_config_disabled' }
ConvertTo-Json -InputObject $value -Compress
'''
    process = subprocess.run([str(PS5), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                              "-Command", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert process.returncode == 0 and not process.stderr, process.stderr
    derived = json.loads(process.stdout)
    assert derived["WD_MIND_READ_ENABLED"] == "1"
    assert Path(derived["WD_MIND_LATEST"]) == audit / "mind-live" / "latest.json"
    assert not Path(derived["WD_MIND_LATEST"]).exists()
    for key, value in derived.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime))
    args = ["--latest", derived["WD_MIND_LATEST"], "--audit-root", derived["WD_MIND_AUDIT_ROOT"],
            "--fleet", derived["WD_MIND_FLEET"], "--fleet-sha256", derived["WD_MIND_FLEET_SHA256"]]
    for agent in LANES:
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", agent)
        monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[agent])
        assert session.main(args) == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert json.loads(captured.err)["error"] == "pointer_invalid"
    assert fleet.read_bytes() == raw and not Path(derived["WD_MIND_LATEST"]).exists()
