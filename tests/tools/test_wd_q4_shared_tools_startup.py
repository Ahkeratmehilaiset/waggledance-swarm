"""Synthetic Tools descriptor propagation; no fleet, process or query launched."""
import hashlib
import json
import os
from pathlib import Path
import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load

BOOT = REBOOT / "WdBridgeLimitedBootstrap.ps1"
SUPERVISOR = REBOOT / "wd_supervisor.ps1"
TOOLS = REBOOT / "start-wd-tools-consumer.ps1"
HASH = "a" * 64


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_bootstrap_forwards_only_complete_pair(ps):
    body = load(BOOT, "Get-WdBridgeSupervisorArguments") + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$off=@(Get-WdBridgeSupervisorArguments -SupervisorScript 'C:\\a.ps1' -ConfigPath 'C:\\x.json' -Apply $true)
$on=@(Get-WdBridgeSupervisorArguments -SupervisorScript 'C:\\a.ps1' -ConfigPath 'C:\\x.json' -Apply $true `
 -Q4SharedDescriptorPath 'C:\\spaced dir\\descriptor.json' -Q4SharedDescriptorSha256 '{HASH}')
$half=$false; try {{ $null=Get-WdBridgeSupervisorArguments -SupervisorScript 'C:\\a.ps1' -ConfigPath 'C:\\x.json' -Apply $true `
 -Q4SharedDescriptorPath 'C:\\descriptor.json' }} catch {{ $half=$true }}
@{{off=$off;on=$on;half=$half}} | ConvertTo-Json -Compress
"""
    out = json.loads(_run_powershell(body, executable=ps).stdout)
    assert "-Q4SharedDescriptorPath" not in out["off"]
    idx = out["on"].index("-Q4SharedDescriptorPath")
    assert out["on"][idx + 1] == r"C:\spaced dir\descriptor.json"
    assert out["on"][idx + 2:idx + 4] == ["-Q4SharedDescriptorSha256", HASH]
    assert out["half"] is True


def test_supervisor_and_tools_have_opt_in_pair_and_no_query_arguments():
    sup = SUPERVISOR.read_text(encoding="utf-8-sig")
    tool = TOOLS.read_text(encoding="utf-8-sig")
    for source in (sup, tool):
        assert "[string] $Q4SharedDescriptorPath = ''" in source
        assert "[string] $Q4SharedDescriptorSha256 = ''" in source
    assert "Get-WdToolsSharedQ4Reference" in tool
    assert tool.index("Get-WdToolsSharedQ4Reference -DescriptorPath") < tool.index("if ($ValidateOnly) {", tool.index("$validation = [pscustomobject]"))
    assert "-Q4SharedDescriptorPath" in sup and "-Q4SharedDescriptorSha256" in sup
    assert "WD_Q4_SHARED_DESCRIPTOR_PATH" in tool and "WD_Q4_SHARED_DESCRIPTOR_SHA256" in tool
    block = sup[sup.index("$toolsArguments = @("):sup.index("# Elevated shells", sup.index("$toolsArguments = @("))]
    assert "query" not in block.lower() and "secret" not in block.lower()


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("mode", ["APPLY", "dry-run"])
def test_unattested_shared_descriptor_is_not_bootstrap_success(ps, mode):
    body = load(BOOT, "Get-WdBridgeReconcileVerdict") + f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$base='[2026-10-31T00:00:00Z] [{mode}] host=fixture; age :: '
$off=Get-WdBridgeReconcileVerdict -Run ([pscustomobject]@{{exit_code=0;lines=@($base+'all helpers healthy')}}) -Mode '{mode}'
$on=Get-WdBridgeReconcileVerdict -Run ([pscustomobject]@{{exit_code=0;lines=@($base+'WARN Tools shared descriptor is not attested by existing readiness')}}) -Mode '{mode}'
@{{off=$off;on=$on}} | ConvertTo-Json -Depth 5 -Compress
"""
    out = json.loads(_run_powershell(body, executable=ps).stdout)
    assert out["off"]["ok"] is True
    assert out["on"]["ok"] is False
    assert any("shared_descriptor_unattested" in reason for reason in out["on"]["reasons"])


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("phase", ["existing", "after_launch"])
def test_unattested_tools_yields_explicit_not_ok_without_stopping_process(ps, phase):
    from test_wd_bridge_limited_bootstrap import run, observation, READY, CONSUMER, NATIVE, APPLY, VERIFY
    body = f"""
$script:calls=New-Object System.Collections.Generic.List[string]
$observe={{ return {observation()} }}
$reconcile={{ param($Apply) $script:calls.Add([string]$Apply)
  $line=if($Apply){{'{APPLY}'}}else{{'{VERIFY}'}}
  if (('{phase}' -ceq 'existing' -and $Apply) -or ('{phase}' -ceq 'after_launch' -and -not $Apply)) {{
    $line += '; WARN Tools shared descriptor is not attested by existing readiness'
  }}
  return [pscustomobject]@{{exit_code=0;lines=@($line)}} }}
$read={{ return {READY} }}
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {CONSUMER} }}; if([int]$ProcessId -eq 42){{ return {NATIVE} }}; return $null }}
$sleep={{ param($Seconds) }}
$result=Invoke-WdBridgeLimitedBootstrap -Observe $observe -RunSupervisorOnce $reconcile -ReadToolsReadiness $read `
 -GetProcess $procs -Sleep $sleep -ToolsWaitSeconds 1
@{{result=$result;calls=@($script:calls)}} | ConvertTo-Json -Depth 6 -Compress
"""
    out = run(ps, body)
    assert out["result"]["ok"] is False
    assert out["result"]["stage"] == ("apply" if phase == "existing" else "verify")
    assert "shared_descriptor_unattested" in out["result"]["reasons"]
    assert out["calls"] == (["True"] if phase == "existing" else ["True", "False"])


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["match", "missing", "wrong_digest", "wrong_pid", "wrong_start", "wrong_generation", "wrong_start_subsecond", "wrong_native_start_subsecond", "old_off"])
def test_shared_readiness_requires_the_live_exact_descriptor_binding(ps, case):
    from test_wd_bridge_limited_bootstrap import run, READY, CONSUMER, NATIVE, THREAD, GEN
    record = READY
    if case not in ("missing", "old_off"):
        record = record[:-1] + ";q4_shared_descriptor_sha256='" + ("b" * 64 if case == "wrong_digest" else HASH) + "'}"
    if case == "wrong_pid":
        record = record.replace(";pid=41;", ";pid=43;")
    if case == "wrong_start":
        record = record.replace("process_start_utc='2026-09-30T10:00:01Z'", "process_start_utc='2026-09-30T10:00:08Z'")
    if case == "wrong_generation":
        record = record.replace("generation='" + GEN + "'", "generation='" + "b" * 40 + "'")
    consumer = CONSUMER if case in ("old_off", "wrong_start_subsecond") else CONSUMER.replace("10:00:01.4Z", "10:00:01Z")
    native = NATIVE if case in ("old_off", "wrong_native_start_subsecond") else NATIVE.replace("10:00:02.3Z", "10:00:02Z")
    expected = "" if case == "old_off" else HASH
    body = f"""
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {consumer} }}; if([int]$ProcessId -eq 42){{ return {native} }}; return $null }}
$ok=Test-WdBridgeToolsReadinessPinned -Record ({record}) -Generation '{GEN}' -ThreadId '{THREAD}' `
 -ExpectedSharedDescriptorSha256 '{expected}' -GetProcess $procs
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    out = run(ps, body)
    assert out["ok"] is (case in ("match", "old_off"))


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["same_microsecond", "different_owner_microsecond", "different_native_microsecond"])
def test_shared_readiness_normalizes_to_common_microseconds(ps, case):
    from test_wd_bridge_limited_bootstrap import run, READY, CONSUMER, NATIVE, THREAD, GEN
    record = READY.replace("10:00:01Z", "10:00:01.4938046Z").replace("10:00:02Z", "10:00:02.4938046Z")
    record = record[:-1] + ";q4_shared_descriptor_sha256='" + HASH + "'}"
    consumer = CONSUMER.replace("10:00:01.4Z", "10:00:01." + ("4938050Z" if case == "different_owner_microsecond" else "4938040Z"))
    native = NATIVE.replace("10:00:02.3Z", "10:00:02." + ("4938050Z" if case == "different_native_microsecond" else "4938040Z"))
    body = f"""
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {consumer} }}; if([int]$ProcessId -eq 42){{ return {native} }}; return $null }}
$ok=Test-WdBridgeToolsReadinessPinned -Record ({record}) -Generation '{GEN}' -ThreadId '{THREAD}' `
 -ExpectedSharedDescriptorSha256 '{HASH}' -GetProcess $procs
@{{ok=$ok}} | ConvertTo-Json -Compress
"""
    assert run(ps, body)["ok"] is (case == "same_microsecond")


def test_readiness_hash_is_opt_in_and_checked_without_replacing_process():
    tool = TOOLS.read_text(encoding="utf-8-sig")
    supervisor = SUPERVISOR.read_text(encoding="utf-8-sig")
    assert "q4_shared_descriptor_sha256" in tool
    assert "-ExpectedSharedDescriptorSha256 $Q4SharedDescriptorSha256" in supervisor
    assert "shared_descriptor_unattested" in BOOT.read_text(encoding="utf-8-sig")


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["match", "missing", "wrong_digest", "old_off"])
def test_bootstrap_binds_requested_descriptor_before_success(ps, case):
    from test_wd_bridge_limited_bootstrap import run, observation, READY, CONSUMER, NATIVE, APPLY, VERIFY
    record = READY
    if case not in ("missing", "old_off"):
        record = record[:-1] + ";q4_shared_descriptor_sha256='" + ("b" * 64 if case == "wrong_digest" else HASH) + "'}"
    consumer = CONSUMER if case == "old_off" else CONSUMER.replace("10:00:01.4Z", "10:00:01Z")
    native = NATIVE if case == "old_off" else NATIVE.replace("10:00:02.3Z", "10:00:02Z")
    expected = "" if case == "old_off" else HASH
    body = f"""
$observe={{ return {observation()} }}
$reconcile={{ param($Apply) $line=if($Apply){{'{APPLY}'}}else{{'{VERIFY}'}}; return [pscustomobject]@{{exit_code=0;lines=@($line)}} }}
$read={{ return {record} }}
$procs={{ param($ProcessId) if([int]$ProcessId -eq 41){{ return {consumer} }}; if([int]$ProcessId -eq 42){{ return {native} }}; return $null }}
$result=Invoke-WdBridgeLimitedBootstrap -Observe $observe -RunSupervisorOnce $reconcile -ReadToolsReadiness $read `
 -GetProcess $procs -Sleep {{ param($Seconds) }} -ToolsWaitSeconds 1 -ExpectedSharedDescriptorSha256 '{expected}'
$result | ConvertTo-Json -Depth 6 -Compress
"""
    out = run(ps, body)
    assert out["ok"] is (case in ("match", "old_off"))
    if case in ("missing", "wrong_digest"):
        assert out["stage"] == "tools"
        assert "shared_descriptor_unattested" in out["reasons"]
    if case == "match":
        assert out["tools_binding"]["q4_shared_descriptor_sha256"] == HASH
    if case == "old_off":
        assert "q4_shared_descriptor_sha256" not in out["tools_binding"]


def _q(value):
    return "'" + str(value).replace("'", "''") + "'"


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["ok", "off", "half", "descriptor", "fleet", "manifest", "helper", "identity", "runtime", "old_q4", "missing_recall", "tampered_recall", "missing_resolver", "missing_pin", "invalid_pin"])
def test_tools_reference_uses_pinned_fleet_identity_and_descriptor(tmp_path, ps, case):
    helper = tmp_path / "Get-WdQ4SharedSessionConfig.ps1"
    helper.write_bytes((REBOOT / helper.name).read_bytes())
    extras = {}
    for name in ("Invoke-WdQ4LaneRecall.ps1", "Get-WdQ4DefaultDescriptor.ps1"):
        path = tmp_path / name
        path.write_text("# synthetic helper\n", encoding="utf-8")
        extras[name] = _sha(path.read_bytes()).upper()
    if case == "missing_pin":
        extras.pop("Invoke-WdQ4LaneRecall.ps1")
    if case == "invalid_pin":
        extras["Invoke-WdQ4LaneRecall.ps1"] = "bad"
    lanes = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]
    identity = "11111111-1111-1111-1111-111111111111"
    fleet = {"lanes": [{"agent": name} for name in lanes if name != "codex-tools-1"],
             "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": identity}, "runtime_root": str(tmp_path)}
    if case == "old_q4":
        fleet["q4_read"] = {"enabled": True}
    fleet_raw = json.dumps(fleet).encode()
    fleet_path = tmp_path / "wd-fleet.json"
    fleet_path.write_bytes(fleet_raw)
    document = {"schema": "wd.q4-shared-session-config.v1", "fleet_sha256": _sha(fleet_raw), "lanes": lanes,
                "bundle_root": str(tmp_path), "public_plan": str(tmp_path / "plan"),
                "interpreter": str(tmp_path / "python.exe"), "interpreter_sha256": "a" * 64,
                "interpreter_max_bytes": 1000, "cwd": str(tmp_path / "code"),
                "timeout_seconds": 30, "max_output_bytes": 1000}
    for name in ("binding", "fixed_plan", "snapshot", "runtime_manifest"):
        document[name + "_path"] = str(tmp_path / (name + ".json"))
        document[name + "_sha256"] = "b" * 64
    raw = json.dumps(document).encode()
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_bytes(raw)
    deployment = tmp_path / "deployment-manifest.json"
    deployment.write_bytes(json.dumps({"files": {"wd-fleet.json": _sha(fleet_raw).upper(),
        helper.name: _sha(helper.read_bytes()).upper(), **extras}}).encode())
    manifest_pin, descriptor_pin = _sha(deployment.read_bytes()), _sha(raw)
    agent_uuid = identity if case != "identity" else "22222222-2222-2222-2222-222222222222"
    runtime = tmp_path if case != "runtime" else tmp_path / "wrong"
    if case == "off":
        descriptor, descriptor_pin = "", ""
        for name in extras:
            (tmp_path / name).unlink()
    elif case == "half":
        descriptor_pin = ""
    elif case == "descriptor":
        descriptor_pin = "0" * 64
    elif case == "fleet":
        fleet_path.write_bytes(fleet_raw + b" ")
    elif case == "manifest":
        manifest_pin = "0" * 64
    elif case == "helper":
        helper.write_bytes(helper.read_bytes() + b"\n# changed")
    elif case in ("missing_recall", "tampered_recall"):
        path = tmp_path / "Invoke-WdQ4LaneRecall.ps1"
        path.unlink() if case == "missing_recall" else path.write_bytes(b"changed")
    elif case == "missing_resolver":
        (tmp_path / "Get-WdQ4DefaultDescriptor.ps1").unlink()
    names = ("Test-PathAtOrBelow", "Assert-DirectoryPathWithoutReparse", "Assert-FilePathWithoutReparse", "Read-Utf8FileSnapshot",
             "Get-WdToolsSharedQ4Reference", "Clear-WdInheritedQ4ReadSettings")
    script = "\n".join(load(TOOLS, name) for name in names)
    script += f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$env:WD_Q4_SHARED_DESCRIPTOR_PATH='spoof_secret'
Clear-WdInheritedQ4ReadSettings
try {{ $value=Get-WdToolsSharedQ4Reference -DescriptorPath {_q(descriptor)} -DescriptorSha256 {_q(descriptor_pin)} `
 -FleetPath {_q(fleet_path)} -DeploymentManifestPath {_q(deployment)} -ExpectedManifestSha256 {_q(manifest_pin)} `
 -BundleRoot {_q(tmp_path)} -TrustedRoot {_q(Path(tmp_path.anchor))} -Lane 'codex-tools-1' `
 -AgentUuid {_q(agent_uuid)} -RuntimeRoot {_q(runtime)}
 @{{ok=$true;value=$value;spoof=(Test-Path Env:WD_Q4_SHARED_DESCRIPTOR_PATH)}} | ConvertTo-Json -Depth 5 -Compress
}} catch {{ @{{ok=$false;reason=$_.Exception.Message;spoof=(Test-Path Env:WD_Q4_SHARED_DESCRIPTOR_PATH)}} | ConvertTo-Json -Compress }}
"""
    out = json.loads(_run_powershell(script, executable=ps).stdout)
    assert out["spoof"] is False
    # Production accepts only fixed Windows paths. POSIX tmp_path is not a
    # valid substitute; exercise its typed refusal instead of a false success.
    if case == "ok" and os.name == "nt":
        assert out["ok"] and set(out["value"]) == {"WD_Q4_SHARED_DESCRIPTOR_PATH", "WD_Q4_SHARED_DESCRIPTOR_SHA256"}, out
    elif case == "off":
        assert out["ok"] and out["value"] is None
    else:
        assert out["ok"] is False and out["reason"] == "q4_shared_reference_refused", out
