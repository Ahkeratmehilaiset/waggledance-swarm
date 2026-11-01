"""Synthetic session descriptors only: no launcher, query, or live data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def run(tmp_path, mutate=None, lane="codex-lead-1", enabled=True, fleet_pin=None, descriptor_pin=None, raw=None):
    fleet = tmp_path / "fleet.json"
    fleet_raw = json.dumps({"lanes": [{"agent": x} for x in LANES if x != "codex-tools-1"],
                            "tools_supervisor": {"agent": "codex-tools-1"}}).encode()
    fleet.write_bytes(fleet_raw)
    cfg = {"schema": "wd.q4-shared-session-config.v1", "fleet_sha256": sha(fleet_raw),
           "lanes": LANES, "bundle_root": str(tmp_path / "bundle"),
           "public_plan": str(tmp_path / "public.json"), "interpreter": str(tmp_path / "python.exe"),
           "interpreter_sha256": "a" * 64, "interpreter_max_bytes": 10000, "cwd": str(tmp_path / "code"),
           "timeout_seconds": 30, "max_output_bytes": 32000}
    for name in ("binding", "fixed_plan", "snapshot", "runtime_manifest"):
        cfg[name + "_path"] = str(tmp_path / (name + ".json"))
        cfg[name + "_sha256"] = "b" * 64
    if mutate: mutate(cfg)
    data = raw(cfg) if raw else json.dumps(cfg).encode()
    desc = tmp_path / "descriptor.json"
    desc.write_bytes(data)
    script = ("$ErrorActionPreference='Stop'\ntry {\n"
              f". {quote(HELPER)}\n"
              f"$v=Get-WdQ4SharedSessionConfig -FleetPath {quote(fleet)} -FleetSha256 {quote(fleet_pin or sha(fleet_raw))} "
              f"-DescriptorPath {quote(desc)} -DescriptorSha256 {quote(descriptor_pin or sha(data))} -Lane {quote(lane)}"
              + (" -Enabled" if enabled else "") + "\n"
              "[ordered]@{ok=$true;value=$v}|ConvertTo-Json -Depth 8 -Compress\n"
              "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n")
    code = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, (process.stdout, process.stderr)
    return json.loads(process.stdout)


def test_disabled_does_not_read_or_dispatch(tmp_path):
    assert run(tmp_path, enabled=False, fleet_pin="bad", descriptor_pin="bad") == {"ok": True, "value": None}


def test_disabled_accepts_nonexistent_files_without_reading_them(tmp_path):
    script = ("$ErrorActionPreference='Stop'\n" + f". {quote(HELPER)}\n"
              + f"$v=Get-WdQ4SharedSessionConfig -FleetPath {quote(tmp_path / 'missing-fleet')} "
              + f"-DescriptorPath {quote(tmp_path / 'missing-descriptor')} -Lane 'bad'\n"
              + "[ordered]@{value=$v}|ConvertTo-Json -Compress\n")
    code = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0 and json.loads(process.stdout) == {"value": None}


@pytest.mark.parametrize("lane", LANES)
def test_pinned_complete_descriptor_yields_exact_lane_and_cli_inputs(tmp_path, lane):
    result = run(tmp_path, lane=lane)
    assert result["ok"] and result["value"]["lane"] == lane
    assert result["value"]["fleet_sha256"] and result["value"]["fixed_plan_sha256"] == "b" * 64
    assert "query" not in result["value"]


@pytest.mark.parametrize("lane", LANES)
def test_one_lane_pilot_allows_only_the_selected_lane(tmp_path, lane):
    change = lambda d: d.update(lanes=[lane])
    other = "fable-5" if lane != "fable-5" else "codex-lead-1"
    assert run(tmp_path, mutate=change, lane=lane)["ok"]
    assert not run(tmp_path, mutate=change, lane=other)["ok"]


@pytest.mark.parametrize("change", [lambda d: d.pop("binding_sha256"),
                                    lambda d: d.update(query="no"),
                                    lambda d: d.update(lanes=["fable-5"]),
                                    lambda d: d.update(lanes=[]),
                                    lambda d: d.update(lanes=["codex-lead-1", "codex-lead-1"]),
                                    lambda d: d.update(lanes=["codex-lead-1", "unknown"]),
                                    lambda d: d.update(timeout_seconds=1801),
                                    lambda d: d.update(interpreter_sha256="x" * 64),
                                    lambda d: d.update(snapshot_path="relative"),
                                    lambda d: d.update(fleet_sha256="0" * 64)])
def test_invalid_descriptor_fails_closed(tmp_path, change):
    assert not run(tmp_path, mutate=change)["ok"]


@pytest.mark.parametrize("kwargs", [{"lane": "other"}, {"fleet_pin": "0" * 64}, {"descriptor_pin": "0" * 64}])
def test_wrong_lane_or_external_hash_refuses(tmp_path, kwargs):
    assert not run(tmp_path, **kwargs)["ok"]


def test_case_colliding_json_key_refuses(tmp_path):
    def duplicate(d):
        text = json.dumps(d)
        return (text[:-1] + ',"SCHEMA":"wd.q4-shared-session-config.v1"}').encode()
    assert not run(tmp_path, raw=duplicate)["ok"]


def test_escaped_duplicate_json_key_refuses(tmp_path):
    def duplicate(d):
        return (json.dumps(d)[:-1] + r',"\u0073chema":"wd.q4-shared-session-config.v1"}').encode()
    assert not run(tmp_path, raw=duplicate)["ok"]


def test_helper_is_required_in_deployment_and_fleet():
    reboot = ROOT / "ops/windows/reboot"
    name = "Get-WdQ4SharedSessionConfig.ps1"
    assert "'" + name + "'" in (reboot / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    fleet = json.loads((reboot / "wd-fleet.json").read_text(encoding="utf-8"))
    assert name in fleet["deployment"]["required_bundle_files"]
