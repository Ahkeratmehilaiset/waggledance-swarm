"""Read-only Mind config derivation against caller-created synthetic fleet files.

The helper does not authenticate a manifest; these tests supply a matching
digest and check only the helper contract. No installed runtime is accessed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdMindReadConfig.ps1"
PS5 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
pytestmark = pytest.mark.skipif(os.name != "nt" or not PS5.is_file(), reason="Windows PowerShell 5.1 required")


def _run(tmp_path: Path, fleet_bytes: bytes, digest: str | None = None) -> dict:
    fleet = tmp_path / "synthetic-fleet.json"
    fleet.write_bytes(fleet_bytes)
    system_root = os.environ["SystemRoot"]
    env = {"SystemRoot": system_root, "WINDIR": system_root, "PATH": str(Path(system_root) / "System32"),
           "TEMP": str(tmp_path), "TMP": str(tmp_path), "USERPROFILE": str(tmp_path),
           "WD_TEST_HELPER": str(HELPER), "WD_TEST_FLEET": str(fleet),
           "WD_TEST_DIGEST": digest if digest is not None else hashlib.sha256(fleet_bytes).hexdigest()}
    script = r'''
$ErrorActionPreference='Stop'
. $env:WD_TEST_HELPER
try {
    $value=Get-WdMindReadConfig -FleetPath $env:WD_TEST_FLEET -FleetSha256 $env:WD_TEST_DIGEST
    if ($null -eq $value) { '{"state":"off"}' }
    else { ConvertTo-Json -InputObject @{state='enabled';value=$value} -Compress }
} catch { '{"state":"refused"}' }
'''
    result = subprocess.run([str(PS5), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                             "-Command", script], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0 and not result.stderr, result.stderr
    assert fleet.read_bytes() == fleet_bytes
    return json.loads(result.stdout)


def _json(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


@pytest.mark.parametrize("config", [{}, {"mind_read": {"enabled": False}}], ids=["missing", "disabled"])
def test_missing_or_disabled_config_is_off(tmp_path, config):
    assert _run(tmp_path, _json(config)) == {"state": "off"}


@pytest.mark.parametrize("variant", ["wrong_digest", "relative_path", "absolute_parent", "drive_root", "non_boolean", "case_variant"])
def test_invalid_input_refuses_without_partial_settings(tmp_path, variant):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    live = audit / "mind-live"
    config = {"mind_read": {"enabled": True, "live_dir": str(live)}}
    digest = None
    if variant == "wrong_digest":
        digest = "0" * 64
    elif variant == "relative_path":
        config["mind_read"]["live_dir"] = "relative/mind-live"
    elif variant == "absolute_parent":
        config["mind_read"]["live_dir"] = str(audit / "nested" / ".." / "mind-live")
    elif variant == "drive_root":
        config["mind_read"]["live_dir"] = tmp_path.anchor
    elif variant == "non_boolean":
        config["mind_read"]["enabled"] = "true"
    else:
        config["Mind_Read"] = config.pop("mind_read")
    assert _run(tmp_path, _json(config), digest) == {"state": "refused"}


@pytest.mark.parametrize("mode", ["explicit", "shared_runtime"])
def test_verified_fixture_derives_only_read_settings_without_creating_live_dir(tmp_path, mode):
    runtime = tmp_path / "runtime"
    audit = runtime / "shared" / ".codex-audit"
    audit.mkdir(parents=True)
    live = audit / "mind-live"
    config = {"runtime_root": str(runtime), "mind_read": {"enabled": True}}
    if mode == "explicit":
        config["mind_read"]["live_dir"] = str(live)
    else:
        config["mind_read"]["live_dir_mode"] = "shared_runtime_audit"
    raw = _json(config)
    result = _run(tmp_path, raw)
    assert result["state"] == "enabled"
    settings = result["value"]
    assert set(settings) == {"WD_MIND_READ_ENABLED", "WD_MIND_LATEST", "WD_MIND_AUDIT_ROOT",
                             "WD_MIND_FLEET", "WD_MIND_FLEET_SHA256"}
    assert settings["WD_MIND_READ_ENABLED"] == "1"
    assert Path(settings["WD_MIND_LATEST"]) == live / "latest.json"
    assert Path(settings["WD_MIND_AUDIT_ROOT"]) == audit
    assert Path(settings["WD_MIND_FLEET"]) == tmp_path / "synthetic-fleet.json"
    assert settings["WD_MIND_FLEET_SHA256"] == hashlib.sha256(raw).hexdigest()
    assert not live.exists()
