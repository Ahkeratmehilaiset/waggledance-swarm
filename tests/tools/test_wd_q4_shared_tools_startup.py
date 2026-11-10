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

_WINDOWS_HELPER_LAUNCH = pytest.mark.skipif(
    os.name != "nt", reason="Windows helper launch uses pwsh.exe or powershell.exe"
)


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


# --- startup Mind read (lead-mind-4h-startup-read-fable-20261108-v1): synthetic, never a real recall ---------------
CONTENT_BINDING = {key: "c" * 64 for key in ("encoder_sha256", "policy_sha256", "roster_sha256",
                                              "content_decision_sha256", "descriptor_sha256", "memory_sha256",
                                              "projection_sha256")}
CARD_BINDING = {key: "c" * 64 for key in ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                                           "roster_sha256", "public_policy_sha256", "descriptor_sha256",
                                           "projection_sha256")}
RECORD_NAME = "wd-mind-startup-read.json"
# A stand-in for Invoke-WdQ4LaneRecall.ps1: records what it was given, then answers, refuses or hangs by mode file.
FAKE_RECALL = r"""param([int]$TimeoutSeconds = 120, [switch]$WithStatus, [switch]$Runtime, [switch]$Live, [switch]$WithCoverage, [switch]$LiveP1, [switch]$InternalWorker)
$ErrorActionPreference = 'Stop'
$query = [Console]::In.ReadToEnd()
$mode = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'fake-mode.txt') -Raw).Trim()
$seen = @{ query = $query; switches = @($PSBoundParameters.Keys | Sort-Object); timeout = $TimeoutSeconds
  env = @{ bundle = [string]$env:WD_BRIDGE_BUNDLE_ROOT; wrapper = [string]$env:WD_BRIDGE_PYTHON_WRAPPER; pin = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH } }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'fake-seen.json'), ($seen | ConvertTo-Json -Compress))
if ($mode -eq 'hang') { Start-Sleep -Seconds 4; exit 0 }
if ($mode -eq 'refuse') { if ($WithStatus) { [Console]::Error.WriteLine('{"status":"UNKNOWN","exit_code":3,"cleanup":"unknown"}') }; exit 3 }
if ($mode -eq 'garbage') { [Console]::Out.Write('{"schema":"other"}' + "`n"); exit 0 }
if ($mode -like 'withheld*') {
  $descPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'descriptor.json'
  $descriptor = Get-Content -LiteralPath $descPath -Raw | ConvertFrom-Json
  $result = @{schema='wd.q4-lane-content-withheld.v1';status='CONTENT_WITHHELD';reason='SOURCE_FRESHNESS_UNVERIFIED';freshness='UNKNOWN';authority='none';
    lane='LANE';lane_binding='DECLARED_CONSISTENT';lane_authentication='UNKNOWN';cleanup='chain_confirmed';identity_sha256=('9'*64);
    descriptor_sha256=((Get-FileHash -LiteralPath $descPath -Algorithm SHA256).Hash.ToLowerInvariant());call_id=('e'*32);
    original_inner_status='ANSWER';original_result_count=1;original_answer_sha256=('d'*64);binding=$descriptor.expected_binding;
    trust=@{lane_authentication='UNKNOWN';freshness='UNKNOWN';publisher_authentication='UNKNOWN';projection_public_attestation='UNKNOWN';transport='caller_injected';projection='caller_pinned'};
    original_result_refs=@(@{ref_sha256=('a'*64);doc_id_sha256=('b'*64)}) }
  switch ($mode) {
    'withheld_degraded' {$result.original_inner_status='DEGRADED'}
    'withheld_multiple' {$result.original_result_count=2; $result.original_result_refs+=@{ref_sha256=('2'*64);doc_id_sha256=('3'*64)}}
    'withheld_missing' {$result.Remove('authority')}
    'withheld_extra' {$result['stored_text']='STORED-TEXT-SECRET'}
    'withheld_wrong_lane' {$result.lane='wrong-lane'}
    'withheld_wrong_descriptor' {$result.descriptor_sha256=('1'*64)}
    'withheld_wrong_binding' {$result.binding.policy_sha256=('1'*64)}
    'withheld_wrong_freshness' {$result.freshness='FRESH'}
    'withheld_wrong_trust' {$result.trust.freshness='FRESH'}
    'withheld_wrong_cleanup' {$result.cleanup='unknown'}
    'withheld_wrong_count' {$result.original_result_count=2}
    'withheld_bad_ref' {$result.original_result_refs[0].doc_id_sha256='invalid'}
    'withheld_extra_ref' {$result.original_result_refs[0]['stored_text']='STORED-TEXT-SECRET'}
  }
  if ($WithStatus) { if ($mode -eq 'withheld_outer_unknown') { [Console]::Error.WriteLine('{"status":"UNKNOWN","exit_code":0,"cleanup":"UNKNOWN"}') } else { [Console]::Error.WriteLine('{"status":"EXITED","exit_code":0,"cleanup":"CONFIRMED"}') } }
  [Console]::Out.Write(($result | ConvertTo-Json -Depth 8 -Compress) + "`n")
  exit 0
}
if ($mode -eq 'producer_bytes') {
  if ($WithStatus) { [Console]::Error.WriteLine('{"status":"EXITED","exit_code":0,"cleanup":"CONFIRMED"}') }
  $bytes = [IO.File]::ReadAllBytes((Join-Path $PSScriptRoot 'producer-response.bin'))
  $out = [Console]::OpenStandardOutput(); $out.Write($bytes, 0, $bytes.Length); $out.Flush(); exit 0
}
if ($mode -eq 'orphan') {
  # a process started by the helper keeps the inherited stdout/stderr pipes open after the helper itself exited
  $shell = if ($PSVersionTable.PSEdition -eq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
  Start-Process -FilePath (Join-Path $PSHOME $shell) -ArgumentList @('-NoProfile', '-NonInteractive', '-Command', 'Start-Sleep -Seconds 6') -NoNewWindow | Out-Null
}
$innerStatus = if ($mode -eq 'abstain') { 'ABSTAIN' } elseif ($mode -eq 'degraded') { 'DEGRADED' } else { 'ANSWER' }
$results = @(@{ stored_text = ('STORED-TEXT-SECRET ' + $query); ref_sha256 = ('a' * 64); doc_id_sha256 = ('b' * 64) })
if ($mode -in @('abstain', 'zero')) { $results = @() }
$answer = @{ schema = 'wd.q4-lane-answer.v1'; lane = 'LANE'; status = $innerStatus; result_count = $results.Count
  results = $results }
if ($mode -eq 'trust_unknown') { $answer.trust = @{ freshness = 'UNKNOWN' } }
if ($mode -eq 'trust_stale') { $answer.trust = @{ freshness = 'STALE' } }
if ($mode -eq 'trust_forged_fresh') { $answer.trust = @{ freshness = 'FRESH' } }
$result = @{ schema = 'wd.q4-lane-caller-result.v1'; status = 'VALIDATED'; lane = 'LANE'; inner_status = $innerStatus
  answer = $answer; answer_sha256 = ('d' * 64); call_id = ('e' * 32); result_count = $results.Count; descriptor_sha256 = ('f' * 64)
  cleanup = 'chain_confirmed' }
if ($WithStatus) { [Console]::Error.WriteLine('{"status":"OK","exit_code":0,"cleanup":"chain_confirmed"}') }
[Console]::Out.Write(($result | ConvertTo-Json -Depth 6 -Compress) + "`n")
exit 0
"""
STARTUP_READ_FUNCTIONS = ("Test-WdQ4ContentDescriptorBinding", "Invoke-WdQ4StartupMindRead",
                          "Get-WdMindStartupReadGuidance")


def startup_read_fixture(tmp_path, lane, *, mode="answer", binding=CONTENT_BINDING, descriptor_case="ok",
                         manifest_case="ok", env_case="ok", audit_case="ok", producer_bytes=None):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    if audit_case == "file":  # S3: the record directory cannot be created
        (worktree / ".codex-audit").write_text("not a directory", encoding="utf-8")
    if audit_case == "stale":
        (worktree / ".codex-audit").mkdir()
        (worktree / ".codex-audit" / RECORD_NAME).write_text("prior-receipt", encoding="ascii")
    helper = bundle / "Invoke-WdQ4LaneRecall.ps1"
    helper.write_text(FAKE_RECALL.replace("LANE", lane), encoding="utf-8")
    (bundle / "fake-mode.txt").write_text(mode, encoding="utf-8")
    if producer_bytes is not None:
        (bundle / "producer-response.bin").write_bytes(producer_bytes)
    wrapper = bundle / "Invoke-WdBridgePython.ps1"  # V1: the helper default route runs this pinned wrapper
    wrapper.write_text("exit 0\n", encoding="utf-8")
    wrapper_pin = hashlib.sha256(wrapper.read_bytes()).hexdigest().upper()
    if manifest_case == "wrapper_pin":
        wrapper_pin = "3" * 64
    helper_pin = hashlib.sha256(helper.read_bytes()).hexdigest().upper()
    if manifest_case == "helper_pin":
        helper_pin = "0" * 64
    manifest = bundle / "deployment-manifest.json"
    manifest.write_bytes(json.dumps({"files": {"Invoke-WdQ4LaneRecall.ps1": helper_pin,
                                              "Invoke-WdBridgePython.ps1": wrapper_pin}}).encode())
    manifest_pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
    if manifest_case == "manifest":
        manifest_pin = "1" * 64
    descriptor = tmp_path / "descriptor.json"
    raw = json.dumps({"schema": "wd.q4-shared-session-config.v2", "expected_binding": binding}).encode()
    descriptor.write_bytes(raw)
    descriptor_pin = hashlib.sha256(raw).hexdigest()
    if descriptor_case == "sha":
        descriptor_pin = "2" * 64
    return {"bundle": bundle, "worktree": worktree, "helper": helper, "helper_pin": helper_pin,
            "manifest": manifest, "manifest_pin": manifest_pin, "descriptor": descriptor,
            "descriptor_pin": descriptor_pin, "seen": bundle / "fake-seen.json", "wrapper": wrapper,
            "record": worktree / ".codex-audit" / RECORD_NAME,
            "env_prelude": ("function Move-Item { throw 'synthetic_record_move' }\n" if audit_case == "stale" else "")
                           + _env_prelude(env_case, bundle, wrapper, manifest_pin)}


def _env_prelude(env_case, bundle, wrapper, manifest_pin):
    """The helper environment the launcher process inherits: complete, absent, or disagreeing with the bundle."""
    names = ("WD_BRIDGE_BUNDLE_ROOT", "WD_BRIDGE_PYTHON_WRAPPER", "WD_REBOOT_EXPECTED_MANIFEST_HASH")
    if env_case == "unset":
        return "\n".join(f"Remove-Item -LiteralPath Env:\\{name} -ErrorAction SilentlyContinue" for name in names)
    bundle_value = bundle.parent if env_case == "mismatch" else bundle
    values = (str(bundle_value), str(wrapper), manifest_pin.upper())
    return "\n".join(f"$env:{name} = {_quote(value)}" for name, value in zip(names, values))


def startup_read_call(fx, lane, *, timeout=5, grace=1):
    return (fx["env_prelude"] + "\n"
            f"$r = Invoke-WdQ4StartupMindRead -DescriptorPath {_quote(fx['descriptor'])} -DescriptorSha256 "
            f"{_quote(fx['descriptor_pin'])} -DeploymentManifestPath {_quote(fx['manifest'])} -ExpectedManifestSha256 "
            f"{_quote(fx['manifest_pin'])} -BundleRoot {_quote(fx['bundle'])} -Lane {_quote(lane)} -Worktree "
            f"{_quote(fx['worktree'])} -TimeoutSeconds {timeout} -GraceSeconds {grace}\n"
            "if ($null -eq $r) { 'null' } else { $r | ConvertTo-Json -Depth 5 -Compress }")


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def assert_no_secret(text):
    lowered = text.lower()
    assert "stored-text-secret" not in lowered and "situation" not in lowered and "startup read:" not in lowered


@pytest.mark.parametrize("mode", ["answer", "trust_unknown", "trust_stale", "trust_forged_fresh"])
@_WINDOWS_HELPER_LAUNCH
def test_tools_startup_read_never_delivers_unverified_freshness(tmp_path, mode):
    fx = startup_read_fixture(tmp_path, "codex-tools-1", mode=mode)
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
    record = json.loads(_run_powershell(script).stdout.strip())
    assert record["outcome"] == "unknown" and record["reason"] == "freshness_unverified"
    assert record["delivered_text"] == "" and record["delivered_items"] == 0
    assert record["delivered_chars"] == 0 and record["delivered_sha256"] == ""
    assert_no_secret(fx["record"].read_text(encoding="ascii"))


def test_inherited_tools_startup_recall_is_cleared_before_launch():
    script = load(TOOLS, "Clear-WdInheritedMindReadSettings") + "\n" + r"""
$env:WD_MIND_STARTUP_RECALL='synthetic stale content'
$env:WD_MIND_TEST='synthetic'
Clear-WdInheritedMindReadSettings
@{recall=(Test-Path Env:WD_MIND_STARTUP_RECALL); mind=(Test-Path Env:WD_MIND_TEST)} | ConvertTo-Json -Compress
"""
    assert json.loads(_run_powershell(script).stdout) == {"recall": False, "mind": False}



@pytest.mark.parametrize("mode", ["withheld", "withheld_degraded", "withheld_multiple", "withheld_missing", "withheld_outer_unknown", "withheld_extra", "withheld_wrong_lane", "withheld_wrong_descriptor",
    "withheld_wrong_binding", "withheld_wrong_freshness", "withheld_wrong_trust", "withheld_wrong_cleanup",
    "withheld_wrong_count", "withheld_bad_ref", "withheld_extra_ref"])
@_WINDOWS_HELPER_LAUNCH
def test_withheld_content_metadata_is_strict_and_never_delivered(tmp_path, mode):
    fx = startup_read_fixture(tmp_path, "codex-tools-1", mode=mode)
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
    record = json.loads(_run_powershell(script).stdout.strip())
    if mode in ("withheld", "withheld_degraded", "withheld_multiple"):
        assert record["outcome"] == "unknown" and record["reason"] == "freshness_unverified"
        assert record["refs"] == (["a" * 64, "2" * 64] if mode == "withheld_multiple" else ["a" * 64])
        assert record["result_count"] == (2 if mode == "withheld_multiple" else 1)
        assert record["inner_status"] == ("DEGRADED" if mode == "withheld_degraded" else "ANSWER")
        assert record["answer_sha256"] == "d" * 64
        assert record["call_id"] == "e" * 32 and record["cleanup"] == "CONFIRMED"
    else:
        assert record["outcome"] == "refused" and record["reason"] == "result_shape"
        assert record["refs"] == [] and record["answer_sha256"] == ""
    assert record["delivered_text"] == "" and record["delivered_items"] == 0
    assert record["delivered_chars"] == 0 and record["delivered_sha256"] == ""
    assert_no_secret(fx["record"].read_text(encoding="ascii"))


@pytest.mark.parametrize("mutation", ["none", "authority"])
@_WINDOWS_HELPER_LAUNCH
def test_real_producer_withheld_bytes_reach_startup_parser_without_text(tmp_path, mutation):
    from tools import wd_q4_lane_caller_cli as producer
    from tests.tools.test_wd_q4_ondemand_freshness_20261109 import result
    lane = "codex-tools-1"
    descriptor = json.dumps({"schema": "wd.q4-shared-session-config.v2", "expected_binding": CONTENT_BINDING}).encode()
    descriptor_pin = hashlib.sha256(descriptor).hexdigest()
    value = result(text="SYNTHETIC-PRODUCER-SECRET")
    value["lane"] = value["answer"]["lane"] = lane
    value["descriptor_sha256"] = descriptor_pin
    value["answer"]["binding"] = dict(CONTENT_BINDING)
    value["answer_sha256"] = hashlib.sha256(producer._canon(value["answer"])).hexdigest()
    raw = producer._output(value, lane, value["identity_sha256"], descriptor_pin,
                           CONTENT_BINDING, 65536, "synthetic-owner-token").encode("ascii")
    assert b'"schema":"wd.q4-lane-content-withheld.v1"' in raw
    assert b"SYNTHETIC-PRODUCER-SECRET" not in raw
    if mutation == "authority":
        assert raw.count(b'"authority":"none"') == 1
        raw = raw.replace(b'"authority":"none"', b'"authority":"root"')
    fx = startup_read_fixture(tmp_path, lane, mode="producer_bytes", producer_bytes=raw)
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, lane)
    record = json.loads(_run_powershell(script).stdout.strip())
    if mutation == "none":
        assert record["outcome"] == "unknown" and record["reason"] == "freshness_unverified"
        assert record["refs"] == ["b" * 64] and record["answer_sha256"] == value["answer_sha256"]
        assert record["result_count"] == 1 and record["inner_status"] == "ANSWER"
        assert "caller-reported" in record["note"]
    else:
        assert record["outcome"] == "refused" and record["reason"] == "result_shape"
        assert record["refs"] == [] and record["answer_sha256"] == ""
    assert record["delivered_text"] == "" and record["delivered_items"] == 0
    assert record["delivered_chars"] == 0 and record["delivered_sha256"] == ""
    stored = fx["record"].read_text(encoding="ascii")
    assert "SYNTHETIC-PRODUCER-SECRET" not in stored
    assert_no_secret(stored)


def test_startup_record_note_shape_is_stable_across_outcomes(tmp_path):
    shapes = []
    for mode in ("answer", "garbage", "withheld"):
        folder = tmp_path / mode
        folder.mkdir()
        fx = startup_read_fixture(folder, "codex-tools-1", mode=mode)
        script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
        script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
        record = json.loads(_run_powershell(script).stdout.strip())
        stored = json.loads(fx["record"].read_bytes())
        shapes.append(set(stored))
        assert stored["note"] == record["note"] and isinstance(stored["note"], str)
    assert shapes[0] == shapes[1] == shapes[2]

def test_on_demand_prompt_does_not_treat_unknown_freshness_as_current():
    text = TOOLS.read_text(encoding="utf-8-sig")
    start = text.index("' Q4 on-demand recall:")
    block = text[start:text.index("\n)", start)]
    assert "Neither exit code 0 nor VALIDATED proves source freshness." in block
    assert "CONTENT_WITHHELD is a validated hit whose text is withheld because freshness is unverified; check its references in the bridge." in block
    assert "UNKNOWN, STALE or missing freshness must not be presented as current state." in block
    assert "Verify current state independently against the live bridge before making a current claim." in block


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("mode", ["answer", "abstain", "degraded", "zero", "refuse", "garbage"])
@_WINDOWS_HELPER_LAUNCH
def test_tools_startup_read_records_only_outcome_digests_and_refs(tmp_path, ps, mode):
    fx = startup_read_fixture(tmp_path, "codex-tools-1", mode=mode)
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
    out = _run_powershell(script, executable=ps).stdout.strip()
    record = json.loads(out)
    assert record["lane"] == "codex-tools-1" and record["route"] == "default_caller_cli"
    if mode == "answer":
        assert record["outcome"] == "unknown" and record["reason"] == "freshness_unverified"
        assert record["refs"] == ["a" * 64] and record["exit_code"] == 0
    elif mode in ("abstain", "degraded", "zero"):
        assert record["outcome"] == "empty" and record["reason"] == ("no_result" if mode == "zero" else mode)
        assert record["inner_status"] == ("ANSWER" if mode == "zero" else mode.upper())
    elif mode == "refuse":
        assert record["outcome"] == "refused" and record["exit_code"] == 3 and record["reason"] == "cleanup_unknown"
    else:
        assert record["outcome"] == "refused" and record["reason"] == "result_shape"
    assert json.loads(fx["record"].read_bytes()) == {key: value for key, value in record.items()
                                                     if key not in ("delivered_text", "record_written")}
    assert record["record_written"] is True and isinstance(record["duration_ms"], int)
    assert record["delivered_items"] == 0 and record["delivered_text"] == ""
    assert_no_secret(fx["record"].read_text(encoding="ascii"))
    seen = json.loads(fx["seen"].read_text(encoding="utf-8-sig"))
    assert seen["switches"] == ["TimeoutSeconds", "WithStatus"]
    assert set(json.loads(seen["query"])) == {"situation", "time"}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_tools_startup_read_is_off_for_a_card_descriptor(tmp_path, ps):
    fx = startup_read_fixture(tmp_path, "codex-tools-1", binding=CARD_BINDING)
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
    assert _run_powershell(script, executable=ps).stdout.strip() == "null"
    assert not fx["record"].exists() and not fx["seen"].exists()


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@_WINDOWS_HELPER_LAUNCH
def test_tools_startup_read_survives_a_record_write_failure_and_sets_the_helper_environment(tmp_path, ps):
    """S3 + V1 on the Tools copy: no throw when the record directory is a file; the child gets the verified env."""
    fx = startup_read_fixture(tmp_path, "codex-tools-1", audit_case="file", env_case="unset")
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
    record = json.loads(_run_powershell(script, executable=ps).stdout.strip())
    assert record["outcome"] == "unknown" and record["reason"] == "record_write"
    assert record["record_written"] is False and not fx["record"].exists()
    assert record["delivered_items"] == 0 and record["delivered_text"] == ""
    assert record["helper_env"] == "verified"
    seen = json.loads(fx["seen"].read_text(encoding="utf-8-sig"))
    assert seen["env"] == {"bundle": str(fx["bundle"]), "wrapper": str(fx["wrapper"]),
                           "pin": fx["manifest_pin"].upper()}


def test_tools_consumer_runs_the_startup_read_after_the_export_and_never_in_validate_only():
    source = TOOLS.read_text(encoding="utf-8-sig")
    call = source.index("Invoke-WdQ4StartupMindRead -DescriptorPath")
    assert source.index("SetEnvironmentVariable('WD_Q4_SHARED_DESCRIPTOR_SHA256'") < call
    guard = source.rindex("if (", 0, call)
    assert "-not $ValidateOnly" in source[guard:call] and "$null -ne $toolsSharedQ4Values" in source[guard:call]
    assert "$prompt += Get-WdMindStartupReadGuidance" in source
    delivery = source.index("SetEnvironmentVariable('WD_MIND_STARTUP_RECALL'")
    launch = source.index("if ($conversationSurface -cin @('local_window','native_terminal')) {",
                          source.rindex("Invoke-WdLaneProfileShadowRead -BundleRoot"))
    # set immediately before the conversation or consumer start; never argv or the prompt
    assert source.index("$prompt += Get-WdMindStartupReadGuidance") < delivery < launch
    assert source.count("SetEnvironmentVariable('WD_MIND_STARTUP_RECALL'") == 1 and source.count("delivered_text") == 2
    assert "-RecordWritten ([bool]$toolsStartupMindRead.record_written)" in source
    assert "-DeliveredChars ([int]$toolsStartupMindRead.delivered_chars)" in source
    assert source.count("Invoke-WdQ4StartupMindRead -DescriptorPath") == 1
    body = source[source.index("function Invoke-WdQ4StartupMindRead"):source.index("function Get-WdMindStartupReadGuidance")]
    assert ".Kill(" not in body and "Start-Process" not in body and "-Runtime" not in body and "-Live" not in body
    assert "WaitAll(" in body and "io_timeout" in body and "output_over_budget" in body


@_WINDOWS_HELPER_LAUNCH
def test_startup_failed_record_replace_never_delivers_against_a_prior_receipt(tmp_path):
    """An old receipt is not the matching digest for this recall; no real ACL or startup is used."""
    fx = startup_read_fixture(tmp_path, "codex-tools-1", audit_case="stale")
    script = "\n".join(load(TOOLS, name) for name in STARTUP_READ_FUNCTIONS)
    script += "\n$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + startup_read_call(fx, "codex-tools-1")
    record = json.loads(_run_powershell(script, executable=LANE_TEST_SHELLS[0]).stdout.strip())
    assert record["outcome"] == "unknown" and record["reason"] == "record_write"
    assert record["record_written"] is False
    assert record["delivered_text"] == "" and record["delivered_items"] == 0
    assert record["delivered_chars"] == 0 and record["delivered_sha256"] == ""
    assert fx["record"].read_text(encoding="ascii") == "prior-receipt"


def test_startup_missing_record_never_promises_digest_verification():
    command = "Get-WdMindStartupReadGuidance -RecordPath 'synthetic-receipt' -Outcome success -RecordWritten $false -DeliveredChars 20 -DeliveredItems 1"
    script = load(TOOLS, "Get-WdMindStartupReadGuidance") + "\n" + command
    output = _run_powershell(script, executable=LANE_TEST_SHELLS[0]).stdout
    assert "No recalled content was delivered." in output
    assert "WD_MIND_STARTUP_RECALL" not in output and "delivered_sha256" not in output
    assert "record could not be written" in output



@pytest.mark.parametrize("fault", [
    pytest.param("own_session", marks=_WINDOWS_HELPER_LAUNCH),
    "starter_refuses",
    pytest.param("missing_identity", marks=_WINDOWS_HELPER_LAUNCH),
    pytest.param("wrong_lane", marks=_WINDOWS_HELPER_LAUNCH),
    pytest.param("outside_cwd", marks=_WINDOWS_HELPER_LAUNCH),
])
def test_startup_uses_its_own_session_and_native_worktree_in_real_statement_order(tmp_path, monkeypatch, fault):
    """Execute only the real session/recall statements, with an isolated session seam.

    The seam uses the actual owner initializer and actual session assignments; it
    never drains a queue, writes a heartbeat, or copies an inherited owner token.
    No native agent, live bridge, model or real content is used.
    """
    starter_fails = fault == "starter_refuses"
    root = ROOT if "ROOT" in globals() else REBOOT.parents[2]
    launcher = LAUNCHER if "LAUNCHER" in globals() else TOOLS
    lane = "codex-lead-1" if "LAUNCHER" in globals() else "codex-tools-1"
    shell = SHELL if "SHELL" in globals() else LANE_TEST_SHELLS[0]
    from test_wd_startup_recovery import load
    guard = r"""
$expected = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'identity-expected.json') -Raw | ConvertFrom-Json
if ($env:AGENT_BRIDGE_AGENT -cne $expected.lane -or $env:AGENT_BRIDGE_AGENT_UUID -cne $expected.uuid -or
    $env:AGENT_BRIDGE_RUN_ID -cne $expected.run -or $env:AGENT_BRIDGE_OWNER_SESSION_ID -cne $expected.run -or
    [string]$env:AGENT_BRIDGE_OWNER_TOKEN -cnotmatch '^[0-9a-f]{64}$' -or
    -not [Environment]::CurrentDirectory.Equals([string]$expected.worktree, [StringComparison]::OrdinalIgnoreCase)) { exit 2 }
"""
    marker = "if ($mode -eq 'hang')"
    assert FAKE_RECALL.count(marker) == 1
    monkeypatch.setitem(globals(), "FAKE_RECALL", FAKE_RECALL.replace(marker, guard + "\n" + marker))
    fx = startup_read_fixture(tmp_path, lane)
    expected = {"lane": lane, "uuid": "11111111-1111-4111-8111-111111111111", "run": "synthetic-own-startup",
                "worktree": str(fx["worktree"])}
    (fx["bundle"] / "identity-expected.json").write_bytes(json.dumps(expected).encode("ascii"))
    # Reuse the real identity mint and the exact env/cwd assignments, not the
    # whole starter (which has initial heartbeat and accepted-queue side effects).
    starter_source = (root / ".agent-bridge/bin/Start-AgentBridgeSession.ps1").read_text(encoding="utf-8-sig")
    begin = starter_source.index("$env:AGENT_BRIDGE_RUNTIME_ROOT = $runtimeFull")
    end = starter_source.index("Set-Location -LiteralPath $repoFull", begin) + len("Set-Location -LiteralPath $repoFull")
    starter = fx["bundle"] / "synthetic-session.ps1"
    starter_text = r"""param($Agent,$RuntimeRoot,$RepoRoot,$PrimaryRepoRoot,$RunId,$Role,$AgentUuid,$Capabilities,
 [switch]$RequireDedicatedWorktree,[switch]$SkipBridgeRead,[switch]$SkipGitStatus,[switch]$SkipWakeWatcher,[switch]$SkipHeartbeatJob)
if ($env:WD_SYNTHETIC_STARTER_FAIL -ceq '1') { throw 'synthetic_starter_refusal' }
"""
    starter_text += "\n. " + _quote(root / ".agent-bridge/bin/AgentBridgeSessionIdentity.ps1")
    starter_text += "\n$runtimeFull=$RuntimeRoot; $repoFull=$RepoRoot\n" + starter_source[begin:end]
    starter.write_bytes(starter_text.encode("utf-8"))
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    functions = "\n".join(load(launcher, name) for name in STARTUP_READ_FUNCTIONS)
    quote = _quote
    driver = functions + r"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
Get-ChildItem Env: | Where-Object { $_.Name.StartsWith('AGENT_BRIDGE_', [StringComparison]::OrdinalIgnoreCase) } |
 ForEach-Object { [Environment]::SetEnvironmentVariable($_.Name,$null,'Process') }
function Assert-LaneBootstrapIntegrity {}
function Assert-LanePathWithoutReparse {}
"""
    driver += "\n" + fx["env_prelude"] + "\n"
    values = {"worktree": fx["worktree"], "runtimeRoot": tmp_path / "isolated-bridge", "primaryRepoRoot": tmp_path,
              "primaryRepo": tmp_path, "agent": lane, "runId": expected["run"], "runIdPrefix": "synthetic",
              "role": "tools-tests", "agentUuid": expected["uuid"], "starter": starter, "sessionScript": starter,
              "bootstrapRoot": fx["bundle"], "laneTrustedDrive": "C:\\", "fixedDeploymentManifest": fx["manifest"],
              "toolsDeploymentManifestPath": fx["manifest"], "toolsExpectedManifestHash": fx["manifest_pin"]}
    driver += "\n".join("$" + k + "=" + quote(v) for k, v in values.items())
    driver += "\n$script:LaneManifestAnchor=" + quote(fx["manifest_pin"])
    driver += "\n$env:WD_SYNTHETIC_STARTER_FAIL=" + quote("1" if starter_fails else "0")
    driver += "\nSet-Location -LiteralPath " + quote(unrelated)
    driver += r"""
$capabilities=@('tests'); $requireDedicatedWorktree=$false; $ValidateOnly=$false
$startupMindRead=$null; $toolsStartupMindRead=$null; $startupPrompt=''; $continuationPrompt=''; $prompt=''
$sessionArgs=@{Agent=$agent;RuntimeRoot=$runtimeRoot;RepoRoot=$worktree;RunId=$runId;Role=$role;AgentUuid=$agentUuid;Capabilities=$capabilities}
"""
    ref = "@{WD_Q4_SHARED_DESCRIPTOR_PATH=" + quote(fx["descriptor"]) + ";WD_Q4_SHARED_DESCRIPTOR_SHA256=" + quote(fx["descriptor_pin"]) + "}"
    driver += "\n$q4SharedValues=" + ref + ";$toolsSharedQ4Values=" + ref
    # Created scriptblocks have no file-owned PSScriptRoot; bind only that file context explicitly.
    driver += "\n$syntheticBundleRoot=" + quote(fx["bundle"])
    driver += "\n$sourcePath=" + quote(launcher)
    driver += "\n$fault=" + quote(fault)
    driver += "\n$outside=" + quote(unrelated)
    driver += r"""
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($sourcePath,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'source_parse'}
$reads=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.IfStatementAst] -and
 $n.Extent.Text.Contains('Invoke-WdQ4StartupMindRead -DescriptorPath')},$true) | Sort-Object { $_.Extent.Text.Length })
$sessions=@($ast.FindAll({param($n)
 ($n -is [Management.Automation.Language.TryStatementAst] -and $n.Extent.Text.Contains('$session = . $starter @sessionArgs')) -or
 ($n -is [Management.Automation.Language.PipelineAst] -and $n.Extent.Text.StartsWith('. $sessionScript '))
},$true) | Sort-Object { $_.Extent.Text.Length })
if($reads.Count -lt 1 -or $sessions.Count -lt 1){throw 'missing_real_statement'}
$parts=@($reads[0],$sessions[0])
if(@($parts | Where-Object {$_.Extent.Text.Length -gt 4000}).Count){throw 'unsafe_statement_selection'}
$driverFailed=$false;$driverError=''
try { foreach($part in @($parts | Sort-Object { $_.Extent.StartOffset })) {
 if($part -eq $reads[0]) {
  if($fault -ceq 'missing_identity'){[Environment]::SetEnvironmentVariable('AGENT_BRIDGE_OWNER_TOKEN',$null,'Process')}
  if($fault -ceq 'wrong_lane'){$env:AGENT_BRIDGE_AGENT='fable-5'}
  if($fault -ceq 'outside_cwd'){$worktree=$outside}
 }
 . ([scriptblock]::Create($part.Extent.Text.Replace('$PSScriptRoot','$syntheticBundleRoot')))
} }
catch { $driverFailed=$true;$driverError=$_.Exception.GetType().FullName+':'+$_.FullyQualifiedErrorId }
$read=if($null -ne $startupMindRead){$startupMindRead}else{$toolsStartupMindRead}
@{starter_failed=$driverFailed;driver_error=$driverError;read=$read} | ConvertTo-Json -Depth 8 -Compress
"""
    script = fx["bundle"] / "isolated-order-driver.ps1"
    script.write_bytes(driver.encode("utf-8"))
    import subprocess
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
                            cwd=unrelated, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    observed = json.loads(result.stdout.strip())
    assert observed["starter_failed"] is starter_fails, observed["driver_error"]
    if starter_fails:
        assert observed["read"] is None and not fx["seen"].exists() and not fx["record"].exists()
    elif fault in ("missing_identity", "wrong_lane", "outside_cwd"):
        assert observed["read"]["outcome"] in ("refused", "unknown")
        assert observed["read"]["delivered_text"] == "" and observed["read"]["delivered_items"] == 0
    else:
        assert observed["read"]["outcome"] == "unknown", observed["read"]["reason"]
        assert observed["read"]["reason"] == "freshness_unverified"
        assert observed["read"]["record_written"] is True and observed["read"]["delivered_items"] == 0


@pytest.mark.parametrize("case", ["unset", "null", "unwritten", "non_success", "empty", "success",
                                 "missing_receipt", "missing_outcome"])
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell execution required")
def test_startup_delivery_guard_is_strict_mode_safe(tmp_path, case):
    """Execute the real delivery statement with optional result absent, refused, or authorized."""
    import subprocess

    variable = "toolsStartupMindRead"
    source_path = Path(__file__).resolve().parents[2] / "ops/windows/reboot/start-wd-tools-consumer.ps1"
    quoted_path = "'" + str(source_path).replace("'", "''") + "'"
    record = {"record_written": True, "outcome": "success", "delivered_chars": 19,
              "delivered_text": "SYNTHETIC_RECALL_123"}
    if case == "unwritten":
        record["record_written"] = False
    elif case == "non_success":
        record["outcome"] = "refused"
    elif case == "empty":
        record["delivered_chars"] = 0
    elif case == "missing_receipt":
        del record["record_written"]
    elif case == "missing_outcome":
        del record["outcome"]
    assignment = ""
    if case == "null":
        assignment = "$" + variable + "=$null"
    elif case != "unset":
        assignment = "$" + variable + "='" + json.dumps(record).replace("'", "''") + "'|ConvertFrom-Json"
    driver = tmp_path / "delivery-guard.ps1"
    driver.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
        "[Environment]::SetEnvironmentVariable('WD_MIND_STARTUP_RECALL',$null,'Process')\n"
        "$tokens=$null;$parseErrors=$null\n"
        "$ast=[Management.Automation.Language.Parser]::ParseFile(" + quoted_path
        + ",[ref]$tokens,[ref]$parseErrors)\n"
        "if($parseErrors.Count){throw 'source_parse'}\n"
        '$needle="[Environment]::SetEnvironmentVariable(\'WD_MIND_STARTUP_RECALL\'"\n'
        "$matches=@($ast.FindAll({param($node) "
        "$node -is [Management.Automation.Language.IfStatementAst] -and "
        "$node.Extent.Text.Contains($needle)},$true)|Sort-Object {$_.Extent.Text.Length})\n"
        "if(-not $matches.Count -or $matches[0].Extent.Text.Length -gt 1000){throw 'delivery_statement_scope'}\n"
        + assignment + "\n. ([scriptblock]::Create($matches[0].Extent.Text))\n"
        "$value=[Environment]::GetEnvironmentVariable('WD_MIND_STARTUP_RECALL','Process')\n"
        "@{present=($null -ne $value);value=$value}|ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(driver)],
                            capture_output=True, text=True, timeout=20)
    if case in ("missing_receipt", "missing_outcome"):
        # Defined malformed records still fail closed; the guard must not suppress schema errors.
        assert result.returncode != 0
        assert not result.stdout.strip()
        return
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["present"] is (case == "success")
    assert observed["value"] == (record["delivered_text"] if case == "success" else None)
