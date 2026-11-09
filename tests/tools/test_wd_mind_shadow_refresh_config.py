"""Run only the real refresh config block on synthetic fleet and directories.

This never runs the installer, accesses a deployed fleet, or touches a task.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "ops/windows/reboot/Install-WdMindShadowRefresh.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell path checks")


@pytest.mark.parametrize("case,expected", [("file", "PASS"), ("directory", "PASS"),
                                           ("wrong_type", "REFUSED"), ("reparse", "REFUSED")])
def test_refresh_path_parent_walk_preserves_type_and_reparse_checks(tmp_path, case, expected):
    directory = tmp_path / "synthetic-directory"
    directory.mkdir()
    file = directory / "synthetic-file"
    file.write_bytes(b"synthetic")
    if case == "reparse":
        link = tmp_path / "synthetic-link"
        try:
            os.symlink(directory, link, target_is_directory=True)
        except OSError:
            pytest.skip("cannot create a synthetic Windows symlink")
        path, directory_switch = link / file.name, ""
    elif case == "directory":
        path, directory_switch = directory, " -Directory"
    elif case == "wrong_type":
        path, directory_switch = file, " -Directory"
    else:
        path, directory_switch = file, ""
    text = SOURCE.read_text(encoding="utf-8")
    helper = "function Assert-WdRefreshPath {" + text.split("function Assert-WdRefreshPath {", 1)[1].split("function ConvertTo-WdRefreshLiteral", 1)[0]
    script = ("$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; " + helper +
              "; try { $null=Assert-WdRefreshPath -Path $env:SYNTHETIC_PATH" + directory_switch +
              "; 'PASS' } catch { 'REFUSED' }")
    result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
                            env=dict(os.environ, SYNTHETIC_PATH=str(path)), capture_output=True,
                            text=True, timeout=30, check=True)
    assert result.stderr == ""
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("case,expected", [
    ("shared", "PASS"), ("explicit", "PASS"), ("absent_default", "PASS"),
    ("disabled_default", "PASS"), ("disabled_shared", "PASS"),
    ("mind_case", "REFUSED"), ("enabled_case", "REFUSED"), ("mode_key_case", "REFUSED"),
    ("runtime_case", "REFUSED"), ("explicit_key_case", "REFUSED"),
    ("mode_value_case", "REFUSED"), ("missing_mode_enabled", "REFUSED"),
    ("conflicting_modes", "REFUSED"), ("explicit_mismatch", "REFUSED"),
    ("absent_mismatch", "REFUSED"), ("disabled_mismatch", "REFUSED"),
    ("non_bool", "REFUSED"), ("relative_explicit", "REFUSED"),
])
def test_shadow_config_is_exact_and_location_bound(tmp_path, case, expected):
    runtime = tmp_path / "synthetic-runtime"
    canonical = runtime / "shared" / ".codex-audit" / "mind-live"
    alternate = tmp_path / "synthetic-other"
    canonical.mkdir(parents=True)
    alternate.mkdir()
    fleet = {"runtime_root": str(runtime), "mind_read": {"enabled": True, "live_dir_mode": "shared_runtime_audit"}}
    live = canonical
    cfg = fleet["mind_read"]
    if case == "explicit":
        cfg.pop("live_dir_mode")
        cfg["live_dir"] = str(canonical)
    elif case == "absent_default":
        fleet.pop("mind_read")
    elif case == "disabled_default":
        fleet["mind_read"] = {"enabled": False}
    elif case == "disabled_shared":
        cfg["enabled"] = False
    elif case == "mind_case":
        fleet["Mind_Read"] = fleet.pop("mind_read")
    elif case == "enabled_case":
        cfg["Enabled"] = cfg.pop("enabled")
    elif case == "mode_key_case":
        cfg["Live_Dir_Mode"] = cfg.pop("live_dir_mode")
    elif case == "runtime_case":
        fleet["Runtime_Root"] = fleet.pop("runtime_root")
    elif case == "explicit_key_case":
        cfg.pop("live_dir_mode")
        cfg["Live_Dir"] = str(canonical)
    elif case == "mode_value_case":
        cfg["live_dir_mode"] = "Shared_Runtime_Audit"
        live = alternate
    elif case == "missing_mode_enabled":
        cfg.pop("live_dir_mode")
        live = alternate
    elif case == "conflicting_modes":
        cfg["live_dir"] = str(canonical)
    elif case == "explicit_mismatch":
        cfg.pop("live_dir_mode")
        cfg["live_dir"] = str(canonical)
        live = alternate
    elif case == "absent_mismatch":
        fleet.pop("mind_read")
        live = alternate
    elif case == "disabled_mismatch":
        fleet["mind_read"] = {"enabled": False}
        live = alternate
    elif case == "non_bool":
        cfg["enabled"] = "true"
    elif case == "relative_explicit":
        cfg.pop("live_dir_mode")
        cfg["live_dir"] = "relative"
    text = SOURCE.read_text(encoding="utf-8")
    helper = "function Assert-WdRefreshPath {" + text.split("function Assert-WdRefreshPath {", 1)[1].split("function ConvertTo-WdRefreshLiteral", 1)[0]
    start = "$runtimeProperty = $fleet.PSObject.Properties['runtime_root']"
    if start not in text:  # the original block, for RED against the base
        start = "$runtimeRoot = Assert-WdRefreshPath"
    block = start + text.split(start, 1)[1].split("$state = Get-Content", 1)[0]
    script = ("$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest; " + helper +
              "; $fleet=ConvertFrom-Json -InputObject $env:SYNTHETIC_FLEET; $liveRoot=$env:SYNTHETIC_LIVE; "
              "try { " + block + "; 'PASS' } catch { 'REFUSED' }")
    env = dict(os.environ, SYNTHETIC_FLEET=json.dumps(fleet), SYNTHETIC_LIVE=str(live))
    result = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
                            env=env, capture_output=True, text=True, timeout=30, check=True)
    assert result.stderr == ""
    assert result.stdout.strip() == expected
