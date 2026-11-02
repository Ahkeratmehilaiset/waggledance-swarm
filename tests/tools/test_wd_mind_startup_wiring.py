"""Synthetic startup config -> published bridge-status session, not fleet startup.

The PowerShell config helper and Python producer/session functions are real;
bridge transport and PR lookup are simulated. Launcher process execution,
deployment-manifest authentication and real agent sessions are not tested.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from sandbox.mind import mind_capture_producer as producer
from tools import bridge_compact_view, bridge_mind_workspace, wd_bridge_mind
from tools import wd_bridge_mind_session, wd_bridge_mind_shadow
from waggledance.core.bridge_log_reader import parse_bridge_json_object

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "windows" / "reboot"
ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}


@pytest.mark.skipif(os.name != "nt", reason="actual startup configuration helper requires Windows")
def test_synthetic_fleet_loader_publication_and_five_session_reads(tmp_path, monkeypatch, capsys):
    # Test the original launchers' configuration and prompt wiring statically;
    # never run either launcher or impersonate a live agent.
    source_fleet = json.loads((OPS / "wd-fleet.json").read_bytes())
    assert source_fleet["mind_read"]["live_dir_mode"] == "shared_runtime_audit"
    assert type(source_fleet["mind_read"]["enabled"]) is bool
    for name in ("start-wd-agent.ps1", "start-wd-tools-consumer.ps1"):
        script = (OPS / name).read_text(encoding="utf-8")
        for required in ("Get-WdMindReadConfig", "WD_MIND_READ_ENABLED", "WD_MIND_LATEST",
                         "WD_MIND_AUDIT_ROOT", "WD_MIND_FLEET_SHA256", "tools/wd_bridge_mind_session.py"):
            assert required in script
    assert "Get-WdMindReadConfig.ps1" in (OPS / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    assert "tools/wd_bridge_mind_session.py" in (OPS / "bridge-code-files.json").read_text(encoding="utf-8")

    runtime = tmp_path / "synthetic-runtime"
    shared = runtime / "shared"
    audit = shared / ".codex-audit"
    live = audit / "mind-live"
    (live / "cycles").mkdir(parents=True)
    (live / "files-cache").mkdir()
    fleet = tmp_path / "synthetic-fleet.json"
    config = {"runtime_root": str(runtime), "mind_read": {"enabled": False, "live_dir_mode": "shared_runtime_audit"},
              "lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
              "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}}

    def load_config(enabled):
        config["mind_read"]["enabled"] = enabled
        raw = json.dumps(config, sort_keys=True).encode()
        fleet.write_bytes(raw)
        env = dict(os.environ, TASK_MIND_HELPER=str(OPS / "Get-WdMindReadConfig.ps1"),
                   TASK_MIND_FLEET=str(fleet), TASK_MIND_FLEET_SHA=hashlib.sha256(raw).hexdigest())
        command = ("$ErrorActionPreference='Stop'; . $env:TASK_MIND_HELPER; "
                   "$v=Get-WdMindReadConfig -FleetPath $env:TASK_MIND_FLEET -FleetSha256 $env:TASK_MIND_FLEET_SHA; "
                   "if ($null -eq $v) { 'null' } else { ConvertTo-Json -InputObject $v -Compress }")
        binary = shutil.which("powershell.exe")
        assert binary is not None
        result = subprocess.run([binary, "-NoProfile", "-NonInteractive", "-Command", command], env=env,
                                capture_output=True, text=True, timeout=30, check=True)
        assert result.stderr == ""
        return json.loads(result.stdout)

    assert load_config(False) is None
    values = load_config(True)  # synthetic fixture only; does not modify the source fleet
    assert values["WD_MIND_READ_ENABLED"] == "1"
    assert Path(values["WD_MIND_LATEST"]) == live / "latest.json"
    assert Path(values["WD_MIND_AUDIT_ROOT"]) == audit
    assert values["WD_MIND_FLEET_SHA256"] == hashlib.sha256(fleet.read_bytes()).hexdigest()

    event = {"agent": "codex-lead-1", "agent_uuid": ROSTER["codex-lead-1"], "type": "message",
             "status": "milestone", "task_id": "synthetic-task", "message": "synthetic private body",
             "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    log = shared / "events.jsonl"
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    (shared / "events.generation.json").write_text('{"generation":"synthetic-g1"}', encoding="utf-8")
    _, cursor, _ = bridge_compact_view._read_view_rows(log, 1, "")
    ref = bridge_compact_view.event_id(event)
    row = {"event": event, "ref": ref, "row_sha256": producer.row_digest(event), "source": "detail"}
    for key, value in {"core": wd_bridge_mind, "shadow": wd_bridge_mind_shadow, "ws": bridge_mind_workspace,
                       "event_id": bridge_compact_view.event_id, "parse_bridge_json_object": parse_bridge_json_object,
                       "live": live, "audit_root": audit, "args": SimpleNamespace(window=50, keep=2)}.items():
        monkeypatch.setattr(producer, key, value, raising=False)
    monkeypatch.setattr(producer, "compact_list", lambda *args: ([ref], cursor, {}))
    monkeypatch.setattr(producer, "details", lambda *args: [row])
    monkeypatch.setattr(producer, "pr_context", lambda *args: (None, {"ms": 0, "status": "UNKNOWN"}))
    monkeypatch.setattr(producer.OutputBudget, "allowance", 16 * 1024 * 1024)
    monkeypatch.setattr(producer.OutputBudget, "exhausted", False)
    committed = producer.one_cycle(1, None, {})
    capsys.readouterr()
    assert committed is not None and json.loads((live / "latest.json").read_bytes())["last_ok"] == committed

    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime))
    args = ["--latest", values["WD_MIND_LATEST"], "--audit-root", values["WD_MIND_AUDIT_ROOT"],
            "--fleet", values["WD_MIND_FLEET"], "--fleet-sha256", values["WD_MIND_FLEET_SHA256"]]
    for lane, uuid in ROSTER.items():
        monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
        monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", uuid)
        assert wd_bridge_mind_session.main(args) == 0
        output = capsys.readouterr()
        result = json.loads(output.out)
        assert output.err == "" and result["lane"] == lane and result["authority"] == "none"
        assert result["freshness"]["status"] == "fresh" and "synthetic private body" not in output.out

    monkeypatch.delenv("WD_MIND_READ_ENABLED")
    assert wd_bridge_mind_session.main(args) == 2
    output = capsys.readouterr()
    assert output.out == "" and "mind_session_read_refused" in output.err
    monkeypatch.setenv("WD_MIND_READ_ENABLED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", ROSTER["codex-lead-1"])
    assert wd_bridge_mind_session.main(args) == 2  # fable-5 with another lane's UUID
    output = capsys.readouterr()
    assert output.out == "" and "mind_session_read_refused" in output.err
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", ROSTER["fable-5"])
    bad_args = [*args[:-1], "0" * 64]
    assert wd_bridge_mind_session.main(bad_args) == 2
    output = capsys.readouterr()
    assert output.out == "" and "mind_session_read_refused" in output.err
