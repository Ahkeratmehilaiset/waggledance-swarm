"""Synthetic startup-boundary tests; never execute either complete launcher.

PowerShell receives only a scrub block or the pure configuration helper.
Fleet digest and pin files are synthetic consistency fixtures, not deployment
authorization. Lead must run this file under the inherited D15 hook, retaining
RED before implementing production changes. No model, live bridge or restart.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "windows" / "reboot"
HELPER = OPS / "Get-WdMindPublicReadConfig.ps1"
LAUNCHERS = ("start-wd-agent.ps1", "start-wd-tools-consumer.ps1")
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
WINDOWS = pytest.mark.skipif(not SHELL, reason="Windows PowerShell synthetic helper test")
PINS = ("anchor", "policy", "roster", "public_policy")
MISSING = object()
POISON = (
    "WD_MIND_PUBLIC_ENABLED", "WD_MIND_PUBLIC_ANCHOR_PATH",
    "WD_MIND_PUBLIC_ANCHOR_SHA256", "WD_MIND_PUBLIC_FUTURE_SETTING",
    "WD_MIND_READ_ENABLED", "WD_MIND_READ_FUTURE_SETTING",
    "WD_MIND_LATEST", "WD_MIND_AUDIT_ROOT", "WD_MIND_FLEET",
    "WD_MIND_FLEET_SHA256", "wd_mind_unrecognized",
)


def test_public_config_helper_is_in_deployment_and_fleet_required_lists():
    helper = "Get-WdMindPublicReadConfig.ps1"
    deploy = (OPS / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8-sig")
    fleet = json.loads((OPS / "wd-fleet.json").read_text(encoding="utf-8-sig"))
    assert "'" + helper + "'" in deploy
    assert helper in fleet["deployment"]["required_bundle_files"]


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script):
    # Windows PowerShell can emit module-discovery progress on stderr even on
    # success; suppress progress only, while retaining all errors and warnings.
    script = "$ProgressPreference='SilentlyContinue'\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        [SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)
    assert result.stderr == "", result.stderr
    return json.loads(result.stdout.strip())


def startup_scrub(text):
    # The existing first scrub consists only of the five-name foreach. Executing
    # that exact block gives a behavioral RED: inherited public/future keys leak.
    start = text.index("# Inherited shell values")
    end = text.index("# A lane must never inherit", start)
    block = text[start:end]
    assert not re.search(r"(?im)^\s*(?:Start-Process|Start-Job|Invoke-Expression)\b", block)
    return block


@WINDOWS
@pytest.mark.parametrize("launcher", LAUNCHERS)
@pytest.mark.parametrize("key", POISON)
def test_actual_startup_scrub_drops_every_mind_namespace_shape(launcher, key):
    body = startup_scrub((OPS / launcher).read_text(encoding="utf-8-sig"))
    result = powershell(
        "$ErrorActionPreference='Stop'\n"
        + f"[Environment]::SetEnvironmentVariable({quote(key)},'synthetic-poison','Process')\n"
        + "$env:WD_SYNTHETIC_UNRELATED='retain'\n"
        + body
        + "\n[ordered]@{left=[Environment]::GetEnvironmentVariable("
        + quote(key) + ",'Process');unrelated=$env:WD_SYNTHETIC_UNRELATED}"
        + "|ConvertTo-Json -Compress\n"
    )
    assert result == {"left": None, "unrelated": "retain"}


def test_both_launchers_rescrub_identical_mind_namespace_before_export():
    bodies = []
    for launcher in LAUNCHERS:
        text = (OPS / launcher).read_text(encoding="utf-8-sig")
        match = re.search(r"(?ms)^function Clear-WdInheritedMindReadSettings \{.*?^\}", text)
        assert match, "whole Mind namespace scrub function missing"
        bodies.append(match.group(0))
        assert "StartsWith('WD_MIND_'" in match.group(0)
        assert text.count("\nClear-WdInheritedMindReadSettings\n") >= 2
    assert bodies[0] == bodies[1]


def fixture_config(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir(exist_ok=True)
    config = {"enabled": True, "audit_root": str(audit), "max_age_seconds": 300}
    for name in PINS:
        path = tmp_path / ("synthetic-" + name + ".json")
        data = json.dumps({"synthetic_pin": name}, sort_keys=True).encode("ascii")
        path.write_bytes(data)
        config[name + "_path"] = str(path)
        config[name + "_sha256"] = hashlib.sha256(data).hexdigest()
    return config


def invoke(tmp_path, config=MISSING, *, legacy=False, digest=None):
    fleet = {} if config is MISSING else {"mind_public_read": config}
    fleet["mind_read"] = {"enabled": legacy}
    path = tmp_path / "synthetic-fleet.json"
    data = json.dumps(fleet, sort_keys=True).encode("ascii")
    path.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest() if digest is None else digest
    # The pure helper must neither trust nor modify these inherited values.
    return powershell(
        "$ErrorActionPreference='Stop'\n"
        "$env:WD_MIND_PUBLIC_ENABLED='synthetic-inherited'\n"
        "try {\n"
        + f". {quote(HELPER)}\n"
        + f"$v=Get-WdMindPublicReadConfig -FleetPath {quote(path)} -FleetSha256 {quote(expected)}\n"
        + "[ordered]@{ok=$true;value=$v;inherited=$env:WD_MIND_PUBLIC_ENABLED}"
        + "|ConvertTo-Json -Depth 8 -Compress\n"
        + "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}"
        + "|ConvertTo-Json -Compress }\n"
    )


@WINDOWS
@pytest.mark.parametrize("mode", ("absent", "disabled"))
def test_absent_and_disabled_never_enable_inherited_public_route(tmp_path, mode):
    value = MISSING if mode == "absent" else {"enabled": False}
    assert invoke(tmp_path, value) == {
        "ok": True, "value": None, "inherited": "synthetic-inherited",
    }


@WINDOWS
@pytest.mark.parametrize("config", (None, [], "true", {}, {"enabled": 1},
                                    {"enabled": "false"}, {"Enabled": True}))
def test_non_boolean_or_wrong_case_configuration_refuses(tmp_path, config):
    result = invoke(tmp_path, config)
    assert result["ok"] is False
    assert result["reason"].startswith("mind_public_config_")


@WINDOWS
def test_verified_config_exports_exact_public_tuple_without_lane_impersonation(tmp_path):
    config = fixture_config(tmp_path)
    result = invoke(tmp_path, config)
    assert result["ok"] and result["inherited"] == "synthetic-inherited"
    expected = {
        "WD_MIND_PUBLIC_ENABLED": "1",
        "WD_MIND_PUBLIC_AUDIT_ROOT": config["audit_root"],
        "WD_MIND_PUBLIC_MAX_AGE_SECONDS": "300",
    }
    for name in PINS:
        expected["WD_MIND_PUBLIC_" + name.upper() + "_PATH"] = config[name + "_path"]
        expected["WD_MIND_PUBLIC_" + name.upper() + "_SHA256"] = config[name + "_sha256"]
    assert result["value"] == expected
    assert not any(key.startswith("AGENT_BRIDGE_") for key in result["value"])


@WINDOWS
@pytest.mark.parametrize("name", PINS)
@pytest.mark.parametrize("mode", ("missing", "malformed", "mismatch"))
def test_every_missing_or_unverified_pin_fails_closed(tmp_path, name, mode):
    config = fixture_config(tmp_path)
    if mode == "missing":
        del config[name + "_sha256"]
    elif mode == "malformed":
        config[name + "_sha256"] = "not-a-pin"
    else:
        config[name + "_sha256"] = "0" * 64
    result = invoke(tmp_path, config)
    assert result["ok"] is False
    assert result["reason"].startswith("mind_public_config_")


@WINDOWS
def test_enabled_legacy_and_public_modes_refuse_without_fallback(tmp_path):
    result = invoke(tmp_path, fixture_config(tmp_path), legacy=True)
    assert result["ok"] is False
    assert result["reason"] == "mind_public_config_mode_conflict"


@WINDOWS
def test_wrong_fleet_pin_refuses_even_when_public_is_disabled(tmp_path):
    result = invoke(tmp_path, {"enabled": False}, digest="0" * 64)
    assert result["ok"] is False
    assert result["reason"].startswith("mind_public_config_")


def test_both_launcher_public_blocks_require_anchor_and_cannot_launch_a_reader():
    for launcher in LAUNCHERS:
        text = (OPS / launcher).read_text(encoding="utf-8-sig")
        start = text.index("# Public Mind configuration only: begin")
        end = text.index("# Public Mind configuration only: end", start)
        block = text[start:end]
        assert "Get-WdMindPublicReadConfig.ps1" in block
        assert "Get-WdMindPublicReadConfig -FleetPath" in block
        assert "mind_public_config_helper_pin_mismatch" in block
        assert "mind_public_config_requires_anchored_bundle" in block
        assert "wd_mind_public_session_cli.py" in block
        assert "mind_public_config_cli_pin_mismatch" in block
        assert "[scriptblock]::Create" in block
        assert not re.search(r"(?im)^\s*(?:Start-Process|Start-Job|Invoke-Expression)\b", block)
        assert "tools/wd_mind_public_session_cli.py" in text
        assert "AGENT_BRIDGE_AGENT_UUID" in text
