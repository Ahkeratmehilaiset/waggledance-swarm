"""Synthetic shared config preflight; does not launch a lane or query Mind."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "ops/windows/reboot/start-wd-agent.ps1"
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]


def sha(value):
    return hashlib.sha256(value).hexdigest()


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def invoke(tmp_path, case="ok", lane="codex-lead-1"):
    launch = tmp_path / "bundle"
    launch.mkdir()
    helper = launch / HELPER.name
    helper.write_bytes(HELPER.read_bytes())
    fleet = launch / "wd-fleet.json"
    fleet_raw = json.dumps({"lanes": [{"agent": x} for x in LANES if x != "codex-tools-1"],
                            "tools_supervisor": {"agent": "codex-tools-1"}}).encode()
    fleet.write_bytes(fleet_raw)
    config = {"schema": "wd.q4-shared-session-config.v1", "fleet_sha256": sha(fleet_raw), "lanes": LANES,
              "bundle_root": str(launch), "public_plan": str(launch / "public.json"),
              "interpreter": str(launch / "python.exe"), "interpreter_sha256": "a" * 64,
              "interpreter_max_bytes": 1024, "cwd": str(launch / "code"),
              "timeout_seconds": 30, "max_output_bytes": 1000}
    for name in ("binding", "fixed_plan", "snapshot", "runtime_manifest"):
        config[name + "_path"] = str(launch / (name + ".json"))
        config[name + "_sha256"] = "b" * 64
    raw = json.dumps(config).encode()
    desc = tmp_path / "descriptor.json"
    desc.write_bytes(raw)
    manifest = launch / "deployment-manifest.json"
    manifest.write_bytes(json.dumps({"files": {"wd-fleet.json": sha(fleet_raw).upper(),
        HELPER.name: sha(helper.read_bytes()).upper()}}).encode())
    manifest_pin, desc_pin = sha(manifest.read_bytes()), sha(raw)
    if case == "bad_helper":
        helper.write_bytes(helper.read_bytes() + b"\n# changed")
    if case == "bad_fleet":
        fleet.write_bytes(fleet_raw + b" ")
    if case == "bad_manifest":
        manifest_pin = "0" * 64
    if case == "bad_descriptor":
        desc_pin = "0" * 64
    if case == "half_path":
        desc_pin = ""
    if case == "half_hash":
        desc = ""
    if case == "off":
        desc, desc_pin = "", ""
    script = f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
if($PSVersionTable.PSEdition -eq 'Desktop'){{$env:PSModulePath=Join-Path $PSHOME 'Modules'}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(LAUNCHER)},[ref]$tokens,[ref]$errors)
foreach ($name in @('Assert-LanePathWithoutReparse','Read-Utf8LaneSnapshot','Clear-WdInheritedQ4ReadSettings','Get-WdLaneSharedQ4Reference')) {{
  $fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}},$true)
  if ($null -eq $fn) {{ throw 'missing expected function' }}
  . ([scriptblock]::Create($fn.Extent.Text))
}}
$env:WD_Q4_SHARED_DESCRIPTOR_PATH='inherited_spoof_secret'
Clear-WdInheritedQ4ReadSettings
try {{
 $v=Get-WdLaneSharedQ4Reference -DescriptorPath {q(desc)} -DescriptorSha256 {q(desc_pin)} `
   -FleetPath {q(fleet)} -DeploymentManifestPath {q(manifest)} -ExpectedManifestSha256 {q(manifest_pin)} `
   -BundleRoot {q(launch)} -TrustedRoot {q(Path(tmp_path.anchor))} -Lane {q(lane)}
 @{{ok=$true;value=$v;spoof=(Test-Path Env:WD_Q4_SHARED_DESCRIPTOR_PATH)}}|ConvertTo-Json -Depth 8 -Compress
}} catch {{ @{{ok=$false;reason=$_.Exception.Message;spoof=(Test-Path Env:WD_Q4_SHARED_DESCRIPTOR_PATH)}}|ConvertTo-Json -Compress }}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


def test_off_never_promotes_inherited_q4(tmp_path):
    assert invoke(tmp_path, case="off") == {"ok": True, "value": None, "spoof": False}


@pytest.mark.parametrize("lane", [x for x in LANES if x != "codex-tools-1"])
def test_four_agent_lanes_receive_only_pinned_reference(tmp_path, lane):
    out = invoke(tmp_path, lane=lane)
    assert out["ok"] and not out["spoof"], out
    assert set(out["value"]) == {"WD_Q4_SHARED_DESCRIPTOR_PATH", "WD_Q4_SHARED_DESCRIPTOR_SHA256"}
    assert "query" not in json.dumps(out).lower() and "inherited_spoof_secret" not in json.dumps(out)


@pytest.mark.parametrize("case", ["half_path", "half_hash", "bad_helper", "bad_fleet", "bad_manifest", "bad_descriptor"])
def test_unanchored_or_incomplete_input_refuses(tmp_path, case):
    assert invoke(tmp_path, case=case)["ok"] is False


def test_launcher_has_explicit_paired_parameters_and_scrubs_before_export():
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    assert "[string] $Q4SharedDescriptorPath = ''" in source
    assert "[string] $Q4SharedDescriptorSha256 = ''" in source
    assert source.index("Clear-WdInheritedQ4ReadSettings\n", source.index("if ($DryRun)")) < source.index("WD_Q4_SHARED_DESCRIPTOR_PATH", source.index("if ($DryRun)"))
