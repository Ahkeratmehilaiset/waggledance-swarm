"""Synthetic Q4 configuration only; never run either launcher or a model.

The fixture fleet digest simulates the externally pinned caller contract. It is
not production authorization, data validation or installed-runtime evidence.
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
HELPER = OPS / "Get-WdQ4ReadConfig.ps1"
LAUNCHERS = [OPS / "start-wd-agent.ps1", OPS / "start-wd-tools-consumer.ps1"]
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
WINDOWS = pytest.mark.skipif(not SHELL, reason="Windows PowerShell fixture runner required")
MISSING = object()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def powershell(script):
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    run = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    return json.loads(run.stdout.strip())


def enabled(tmp_path):
    scratch = tmp_path / "synthetic-scratch"
    scratch.mkdir(exist_ok=True)
    return {
        "scratch_root": str(scratch),
        "schema": "wd.q4-read-config.v1", "enabled": True,
        "memory_root": str(tmp_path / "synthetic-memory"),
        "runtime_root": str(tmp_path / "synthetic-runtime"),
        "runtime_generation": "b" * 64, "runtime_revision": "c" * 40,
        "descriptor_path": str(tmp_path / "synthetic-descriptor.json"),
        "descriptor_sha256": "1" * 64,
        "python_path": str(tmp_path / "synthetic-python.exe"), "python_sha256": "2" * 64,
        "runtime_manifest_path": str(tmp_path / "synthetic-runtime.json"),
        "runtime_manifest_sha256": "3" * 64,
        "code_manifest_path": str(tmp_path / "synthetic-code.json"),
        "code_manifest_sha256": "4" * 64, "code_commit": "a" * 40,
        "lock_file": str(tmp_path / "shared-reader.lock"), "lock_timeout_seconds": 0.25,
    }


def invoke(tmp_path, config=MISSING, digest=None):
    fleet = {} if config is MISSING else {"q4_read": config}
    path = tmp_path / "synthetic-fleet.json"
    data = json.dumps(fleet).encode("utf-8")
    path.write_bytes(data)
    pin = hashlib.sha256(data).hexdigest() if digest is None else digest
    return powershell(
        "$ErrorActionPreference='Stop'\n"
        "try {\n"
        f" . {quote(HELPER)}\n"
        f" $v=Get-WdQ4ReadConfig -FleetPath {quote(path)} -FleetSha256 {quote(pin)}\n"
        " [ordered]@{ok=$true;value=$v}|ConvertTo-Json -Depth 8 -Compress\n"
        "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n"
    )


@WINDOWS
@pytest.mark.parametrize("mode", ["absent", "disabled"])
def test_absent_or_disabled_produces_no_q4_values(tmp_path, mode):
    result = invoke(tmp_path, MISSING if mode == "absent" else {"enabled": False})
    assert result == {"ok": True, "value": None}


@WINDOWS
@pytest.mark.parametrize("config", [None, [], "true", {}, {"enabled": "false"}, {"enabled": 1}, {"Enabled": True}])
def test_non_boolean_or_non_object_configuration_refuses(tmp_path, config):
    result = invoke(tmp_path, config)
    assert result["ok"] is False and result["reason"].startswith("q4_config_")


@WINDOWS
def test_enabled_returns_only_pinned_configuration_not_connection_proof(tmp_path):
    cfg = enabled(tmp_path)
    result = invoke(tmp_path, cfg)
    assert result["ok"] is True
    out = result["value"]
    assert out["WD_Q4_READ_ENABLED"] == "1"
    assert out["WD_Q4_MEMORY_ROOT"] == cfg["memory_root"]
    assert out["WD_Q4_DESCRIPTOR_SHA256"] == cfg["descriptor_sha256"]
    assert out["WD_Q4_RUNTIME_MANIFEST_SHA256"] == cfg["runtime_manifest_sha256"]
    assert out["WD_Q4_RUNTIME_ROOT"] == cfg["runtime_root"]
    assert out["WD_Q4_RUNTIME_GENERATION"] == cfg["runtime_generation"]
    assert out["WD_Q4_RUNTIME_REVISION"] == cfg["runtime_revision"]
    assert out["WD_Q4_CODE_MANIFEST_SHA256"] == cfg["code_manifest_sha256"]
    assert out["WD_Q4_PYTHON_SHA256"] == cfg["python_sha256"]
    assert out["WD_Q4_CODE_COMMIT"] == cfg["code_commit"]
    assert out["WD_Q4_LOCK_FILE"] == cfg["lock_file"] and out["WD_Q4_LOCK_TIMEOUT"] == "0.25"
    assert out["WD_Q4_SCRATCH_ROOT"] == cfg["scratch_root"]
    assert len(out) == 19 and all(k.startswith("WD_Q4_") for k in out)
    assert not any("CONNECTED" in k or "QUERY" in k for k in out)


REQUIRED = [
    "schema", "memory_root", "runtime_root", "scratch_root", "runtime_generation", "runtime_revision", "descriptor_path", "descriptor_sha256", "python_path", "python_sha256",
    "runtime_manifest_path", "runtime_manifest_sha256", "code_manifest_path", "code_manifest_sha256",
    "code_commit", "lock_file", "lock_timeout_seconds",
]


@WINDOWS
@pytest.mark.parametrize("field", REQUIRED)
def test_every_enabled_tuple_field_is_required(tmp_path, field):
    cfg = enabled(tmp_path)
    del cfg[field]
    result = invoke(tmp_path, cfg)
    assert result["ok"] is False and result["reason"] == "q4_config_field_missing"


@WINDOWS
@pytest.mark.parametrize("field", ["descriptor_sha256", "python_sha256", "runtime_manifest_sha256",
                                  "code_manifest_sha256", "code_commit", "runtime_generation", "runtime_revision"])
@pytest.mark.parametrize("value", [None, 7, "", "not-a-pin"])
def test_every_malformed_identity_refuses(tmp_path, field, value):
    cfg = enabled(tmp_path)
    cfg[field] = value
    result = invoke(tmp_path, cfg)
    assert result["ok"] is False and result["reason"].startswith("q4_config_")


@WINDOWS
@pytest.mark.parametrize("path", ["relative", r"\\server\share\file", r"C:\x\..\y",
                                 r"C:\x:stream", r"C:\x\file.", r"C:\x\file ", "C:/x/y", r"C:\x\*.json"])
def test_untrusted_path_shapes_refuse(tmp_path, path):
    cfg = enabled(tmp_path)
    cfg["lock_file"] = path
    result = invoke(tmp_path, cfg)
    assert result["ok"] is False and result["reason"].startswith("q4_config_path_")


@WINDOWS
@pytest.mark.parametrize("value", [True, None, "1", -1, 30.1])
def test_timeout_must_be_numeric_and_inside_adapter_bound(tmp_path, value):
    cfg = enabled(tmp_path)
    cfg["lock_timeout_seconds"] = value
    assert invoke(tmp_path, cfg) == {"ok": False, "reason": "q4_config_timeout_invalid"}


@WINDOWS
@pytest.mark.parametrize("value", [0, 30])
def test_timeout_boundary_values_are_preserved(tmp_path, value):
    cfg = enabled(tmp_path)
    cfg["lock_timeout_seconds"] = value
    result = invoke(tmp_path, cfg)
    assert result["ok"] and result["value"]["WD_Q4_LOCK_TIMEOUT"] == str(value)


@WINDOWS
def test_unknown_enabled_field_and_wrong_fleet_pin_refuse(tmp_path):
    cfg = enabled(tmp_path)
    cfg["query"] = "synthetic-never-forward"
    assert invoke(tmp_path, cfg) == {"ok": False, "reason": "q4_config_unknown_field"}
    assert invoke(tmp_path, {"enabled": False}, digest="0" * 64) == {
        "ok": False, "reason": "q4_config_fleet_refused"}


@WINDOWS
def test_existing_path_of_wrong_kind_refuses(tmp_path):
    cfg = enabled(tmp_path)
    bad = tmp_path / "not-a-memory-directory"
    bad.write_bytes(b"synthetic")
    cfg["memory_root"] = str(bad)
    result = invoke(tmp_path, cfg)
    assert result["ok"] is False and result["reason"] == "q4_config_path_refused"


def clear_function(source):
    match = re.search(r"(?ms)^function Clear-WdInheritedQ4ReadSettings \{.*?^\}", source)
    assert match, "shared Q4 namespace scrub missing"
    return match.group(0)


def test_both_launcher_scrubs_are_identical_and_cover_unknown_keys():
    bodies = [clear_function(p.read_text(encoding="utf-8-sig")) for p in LAUNCHERS]
    assert bodies[0] == bodies[1]
    assert "StartsWith('WD_Q4_'" in bodies[0]
    for path in LAUNCHERS:
        source = path.read_text(encoding="utf-8-sig")
        assert source.count("\nClear-WdInheritedQ4ReadSettings\n") >= 2


@WINDOWS
@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_scrub_removes_known_and_unrecognized_stale_values_without_echo(launcher):
    body = clear_function(launcher.read_text(encoding="utf-8-sig"))
    result = powershell(
        body + "\n$env:WD_Q4_MEMORY_ROOT='synthetic-old'\n"
        "$env:WD_Q4_UNRECOGNIZED='synthetic-old'\n"
        "Clear-WdInheritedQ4ReadSettings\n"
        "$left=@([Environment]::GetEnvironmentVariables('Process').Keys |"
        " Where-Object {([string]$_).StartsWith('WD_Q4_',[StringComparison]::OrdinalIgnoreCase)})\n"
        "[ordered]@{remaining=$left.Count}|ConvertTo-Json -Compress\n")
    assert result == {"remaining": 0}


def test_startup_only_validates_configuration_and_requires_pinned_helper():
    for path in LAUNCHERS:
        text = path.read_text(encoding="utf-8-sig")
        start = text.index("# Q4 configuration only: begin")
        end = text.index("# Q4 configuration only: end", start)
        block = text[start:end]
        assert "q4_config_invalid" in block and "-isnot [bool]" in block
        assert "Get-WdQ4ReadConfig.ps1" in block and "q4_config_helper_pin_mismatch" in block
        assert "Get-WdQ4ReadConfig -FleetPath" in block
        assert "wd_q4_private_read" not in block and "Start-Process" not in block
        assert "q4_config_requires_anchored_bundle" in block


def test_shipped_config_is_disabled_and_helper_has_no_launch_or_network_route():
    fleet = json.loads((OPS / "wd-fleet.json").read_text(encoding="utf-8-sig"))
    assert fleet["q4_read"] == {"enabled": False}
    text = HELPER.read_text(encoding="utf-8-sig")
    assert "wd.q4-read-config.v1" in text
    assert not re.search(r"(?im)^\s*(?:Start-Process|Invoke-Expression|Invoke-WebRequest|Start-Job)\b", text)


@WINDOWS
@pytest.mark.parametrize("length", [40, 64])
def test_runtime_generation_and_revision_match_verifier_identity_lengths(tmp_path, length):
    cfg = enabled(tmp_path)
    cfg["runtime_generation"] = "b" * length
    cfg["runtime_revision"] = "c" * length
    out = invoke(tmp_path, cfg)
    assert out["ok"] and out["value"]["WD_Q4_RUNTIME_GENERATION"] == "b" * length
    assert out["value"]["WD_Q4_RUNTIME_REVISION"] == "c" * length


@WINDOWS
@pytest.mark.parametrize("path_field", ["memory_root", "runtime_root", "lock_file"])
def test_reserved_windows_device_paths_refuse_for_every_path_role(tmp_path, path_field):
    cfg = enabled(tmp_path)
    cfg[path_field] = str(tmp_path / "NUL.txt")
    out = invoke(tmp_path, cfg)
    assert out["ok"] is False and out["reason"].startswith("q4_config_path_")


@WINDOWS
def test_runtime_manifest_must_stay_outside_the_enumerated_runtime_tree(tmp_path):
    cfg = enabled(tmp_path)
    cfg["runtime_manifest_path"] = str(Path(cfg["runtime_root"]) / "manifest.json")
    assert invoke(tmp_path, cfg) == {"ok": False, "reason": "q4_config_manifest_inside_runtime"}


def test_deployer_requires_the_q4_helper_and_manifest_hash_map():
    text = (OPS / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8-sig")
    match = re.search(r"(?ms)foreach \(\$required in @\((.*?)\)\) \{", text)
    assert match and "'Get-WdQ4ReadConfig.ps1'," in match.group(1)
    assert "$sourceHashes = Get-RelativeFileHashMap -Root $materializedRebootRoot" in text
    assert re.search(r"(?m)^\s+files = \$sourceHashes\s*$", text)
    assert "Copy-Item -LiteralPath $sourcePaths[[string]$name] -Destination $destination" in text
    fleet = json.loads((OPS / "wd-fleet.json").read_text(encoding="utf-8-sig"))
    assert "Get-WdQ4ReadConfig.ps1" in fleet["deployment"]["required_bundle_files"]


@pytest.mark.parametrize("source", ["deployer", "fleet"])
def test_inactive_q4_query_wrapper_is_required_in_both_bundle_lists(source):
    name = "Invoke-WdQ4Query.ps1"
    if source == "deployer":
        text = (OPS / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8-sig")
        match = re.search(r"(?ms)foreach \(\$required in @\((.*?)\)\) \{", text)
        assert match and f"'{name}'," in match.group(1)
    else:
        fleet = json.loads((OPS / "wd-fleet.json").read_text(encoding="utf-8-sig"))
        assert name in fleet["deployment"]["required_bundle_files"]
        assert fleet["q4_read"] == {"enabled": False}


@WINDOWS
@pytest.mark.parametrize("name", ["Get-WdQ4ReadConfig.ps1", "Invoke-WdQ4Query.ps1"])
def test_real_deployer_hash_function_includes_synthetic_helper_in_manifest(tmp_path, name):
    text = (OPS / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8-sig")
    match = re.search(r"(?ms)^function Get-RelativeFileHashMap \{.*?^\}", text)
    assert match, "deployer hash function not found"
    content = b"# synthetic helper; never executed\n"
    (tmp_path / name).write_bytes(content)
    (tmp_path / "deployment-manifest.json").write_bytes(b"{}")
    result = powershell(
        "$ErrorActionPreference='Stop'\n"
        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility') -ErrorAction Stop\n"
        + match.group(0) + "\n"
        f"$files=Get-RelativeFileHashMap -Root {quote(tmp_path)}\n"
        "[ordered]@{files=$files}|ConvertTo-Json -Depth 8 -Compress\n")
    assert result["files"] == {name: hashlib.sha256(content).hexdigest().upper()}
