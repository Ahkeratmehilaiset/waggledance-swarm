"""Synthetic shared config preflight; does not launch a lane or query Mind."""
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
    extras = {}
    for name in ("Invoke-WdQ4LaneRecall.ps1", "Get-WdQ4DefaultDescriptor.ps1"):
        path = launch / name
        path.write_text("# synthetic helper\n", encoding="utf-8")
        extras[name] = sha(path.read_bytes()).upper()
    if case == "missing_pin":
        extras.pop("Invoke-WdQ4LaneRecall.ps1")
    if case == "invalid_pin":
        extras["Invoke-WdQ4LaneRecall.ps1"] = "bad"
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
        HELPER.name: sha(helper.read_bytes()).upper(), **extras}}).encode())
    manifest_pin, desc_pin = sha(manifest.read_bytes()), sha(raw)
    if case == "bad_helper":
        helper.write_bytes(helper.read_bytes() + b"\n# changed")
    if case in ("missing_recall", "tampered_recall"):
        path = launch / "Invoke-WdQ4LaneRecall.ps1"
        path.unlink() if case == "missing_recall" else path.write_bytes(b"changed")
    if case == "missing_resolver":
        (launch / "Get-WdQ4DefaultDescriptor.ps1").unlink()
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
        for name in extras:
            (launch / name).unlink()
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


@pytest.mark.parametrize("case", ["half_path", "half_hash", "bad_helper", "bad_fleet", "bad_manifest", "bad_descriptor", "missing_recall", "tampered_recall", "missing_resolver", "missing_pin", "invalid_pin"])
def test_unanchored_or_incomplete_input_refuses(tmp_path, case):
    assert invoke(tmp_path, case=case)["ok"] is False


def test_launcher_has_explicit_paired_parameters_and_scrubs_before_export():
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    assert "[string] $Q4SharedDescriptorPath = ''" in source
    assert "[string] $Q4SharedDescriptorSha256 = ''" in source
    assert source.index("Clear-WdInheritedQ4ReadSettings\n", source.index("if ($DryRun)")) < source.index("WD_Q4_SHARED_DESCRIPTOR_PATH", source.index("if ($DryRun)"))


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
$seen = @{ query = $query; switches = @($PSBoundParameters.Keys | Sort-Object); timeout = $TimeoutSeconds }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'fake-seen.json'), ($seen | ConvertTo-Json -Compress))
if ($mode -eq 'hang') { Start-Sleep -Seconds 4; exit 0 }
if ($mode -eq 'refuse') { if ($WithStatus) { [Console]::Error.WriteLine('{"status":"UNKNOWN","exit_code":3,"cleanup":"unknown"}') }; exit 3 }
if ($mode -eq 'garbage') { [Console]::Out.Write('{"schema":"other"}' + "`n"); exit 0 }
$answer = @{ schema = 'wd.q4-lane-answer.v1'; lane = 'LANE'; status = 'ANSWER'; result_count = 1
  results = @(@{ stored_text = ('STORED-TEXT-SECRET ' + $query); ref_sha256 = ('a' * 64); doc_id_sha256 = ('b' * 64) }) }
$result = @{ schema = 'wd.q4-lane-caller-result.v1'; status = 'VALIDATED'; lane = 'LANE'; inner_status = 'ANSWER'
  answer = $answer; answer_sha256 = ('d' * 64); call_id = ('e' * 32); result_count = 1; descriptor_sha256 = ('f' * 64)
  cleanup = 'chain_confirmed' }
if ($WithStatus) { [Console]::Error.WriteLine('{"status":"OK","exit_code":0,"cleanup":"chain_confirmed"}') }
[Console]::Out.Write(($result | ConvertTo-Json -Depth 6 -Compress) + "`n")
exit 0
"""
STARTUP_READ_FUNCTIONS = ("Test-WdQ4ContentDescriptorBinding", "Invoke-WdQ4StartupMindRead",
                          "Get-WdMindStartupReadGuidance")


def startup_read_fixture(tmp_path, lane, *, mode="answer", binding=CONTENT_BINDING, descriptor_case="ok",
                         manifest_case="ok"):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    helper = bundle / "Invoke-WdQ4LaneRecall.ps1"
    helper.write_text(FAKE_RECALL.replace("LANE", lane), encoding="utf-8")
    (bundle / "fake-mode.txt").write_text(mode, encoding="utf-8")
    helper_pin = hashlib.sha256(helper.read_bytes()).hexdigest().upper()
    if manifest_case == "helper_pin":
        helper_pin = "0" * 64
    manifest = bundle / "deployment-manifest.json"
    manifest.write_bytes(json.dumps({"files": {"Invoke-WdQ4LaneRecall.ps1": helper_pin}}).encode())
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
            "descriptor_pin": descriptor_pin, "seen": bundle / "fake-seen.json",
            "record": worktree / ".codex-audit" / RECORD_NAME}


def startup_read_call(fx, lane, *, timeout=5, grace=1):
    return (f"$r = Invoke-WdQ4StartupMindRead -DescriptorPath {_quote(fx['descriptor'])} -DescriptorSha256 "
            f"{_quote(fx['descriptor_pin'])} -DeploymentManifestPath {_quote(fx['manifest'])} -ExpectedManifestSha256 "
            f"{_quote(fx['manifest_pin'])} -BundleRoot {_quote(fx['bundle'])} -Lane {_quote(lane)} -Worktree "
            f"{_quote(fx['worktree'])} -TimeoutSeconds {timeout} -GraceSeconds {grace}\n"
            "if ($null -eq $r) { 'null' } else { $r | ConvertTo-Json -Depth 5 -Compress }")


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def assert_no_secret(text):
    lowered = text.lower()
    assert "stored-text-secret" not in lowered and "situation" not in lowered and "startup read:" not in lowered


def run_startup_read(tmp_path, lane="codex-lead-1", **changes):
    fx = startup_read_fixture(tmp_path, lane, **changes)
    script = "\n".join(f"""
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(LAUNCHER)},[ref]$tokens,[ref]$errors)
$fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq {q(name)}}},$true)
if ($null -eq $fn) {{ throw ('missing function ' + {q(name)}) }}
. ([scriptblock]::Create($fn.Extent.Text))""" for name in STARTUP_READ_FUNCTIONS)
    script = ("$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest\n"
              "if($PSVersionTable.PSEdition -eq 'Desktop'){$env:PSModulePath=Join-Path $PSHOME 'Modules'}\n" + script
              + "\n" + startup_read_call(fx, lane, timeout=(1 if changes.get("mode") == "hang" else 5)))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (result.stdout, result.stderr)
    out = result.stdout.strip()
    return fx, (None if out == "null" else json.loads(out))


def test_startup_read_runs_one_default_route_recall_and_records_only_outcome_digests_and_refs(tmp_path):
    fx, record = run_startup_read(tmp_path)
    assert record["schema"] == "wd.mind-startup-read.v1" and record["lane"] == "codex-lead-1"
    assert record["outcome"] == "success" and record["exit_code"] == 0 and record["route"] == "default_caller_cli"
    assert record["inner_status"] == "ANSWER" and record["result_count"] == 1 and record["refs"] == ["a" * 64]
    assert record["answer_sha256"] == "d" * 64 and record["call_id"] == "e" * 32
    assert record["helper_status"] == "OK" and record["cleanup"] == "chain_confirmed"
    assert record["helper_sha256"] == fx["helper_pin"].lower() and record["descriptor_sha256"] == fx["descriptor_pin"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["ts_utc"])
    stored = json.loads(fx["record"].read_bytes())
    assert stored == record
    assert_no_secret(fx["record"].read_text(encoding="ascii"))
    seen = json.loads(fx["seen"].read_text(encoding="utf-8-sig"))
    assert seen["switches"] == ["TimeoutSeconds", "WithStatus"] and seen["timeout"] == 5
    query = json.loads(seen["query"])
    assert set(query) == {"situation", "time"} and query["situation"].startswith("codex-lead-1 startup read")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", query["time"])
    assert "secret" not in seen["query"].lower() and "\\" not in seen["query"]


@pytest.mark.parametrize("binding", [CARD_BINDING, {}], ids=["card", "empty"])
def test_startup_read_is_off_for_a_descriptor_that_is_not_a_content_descriptor(tmp_path, binding):
    fx, record = run_startup_read(tmp_path, binding=binding)
    assert record is None and not fx["record"].exists() and not fx["seen"].exists()


def test_startup_read_refuses_without_running_when_a_pin_does_not_hold(tmp_path):
    for changes, reason in (({"descriptor_case": "sha"}, "descriptor"), ({"manifest_case": "manifest"}, "manifest"),
                            ({"manifest_case": "helper_pin"}, "helper_pin")):
        (tmp_path / reason).mkdir()
        fx, record = run_startup_read(tmp_path / reason, **changes)
        assert record["outcome"] == "refused" and record["reason"] == reason and record["refs"] == [], record
        assert record["exit_code"] is None and not fx["seen"].exists() and fx["record"].exists()


def test_startup_read_records_a_nonzero_exit_as_refused_and_exit_three_as_cleanup_unknown(tmp_path):
    fx, record = run_startup_read(tmp_path, mode="refuse")
    assert record["outcome"] == "refused" and record["exit_code"] == 3 and record["reason"] == "cleanup_unknown"
    assert record["cleanup"] == "unknown" and record["helper_status"] == "UNKNOWN" and record["refs"] == []
    assert fx["seen"].exists()  # exactly one call, no retry: the fake records one invocation
    assert_no_secret(fx["record"].read_text(encoding="ascii"))


def test_startup_read_records_a_timeout_as_unknown_without_killing_or_retrying(tmp_path):
    fx, record = run_startup_read(tmp_path, mode="hang")
    assert record["outcome"] == "unknown" and record["reason"] == "timeout" and record["exit_code"] is None
    assert record["cleanup"] == "unknown" and record["refs"] == []


def test_startup_read_refuses_an_answer_of_another_shape(tmp_path):
    fx, record = run_startup_read(tmp_path, mode="garbage")
    assert record["outcome"] == "refused" and record["reason"] == "result_shape" and record["exit_code"] == 0
    assert record["refs"] == [] and record["answer_sha256"] == ""


def test_launcher_runs_the_startup_read_after_dry_run_returns_and_before_the_cli_launch():
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    call = source.index("Invoke-WdQ4StartupMindRead -DescriptorPath")
    assert source.index("DRY RUN: bridge bootstrap, handshake write, and CLI launch suppressed.") < call
    assert source.index("SetEnvironmentVariable('WD_Q4_SHARED_DESCRIPTOR_SHA256'") < call
    assert call < source.index("$launchArguments += $startupPrompt")
    assert "$startupPrompt += $startupMindReadGuidance" in source
    assert "$continuationPrompt += $startupMindReadGuidance" in source
    assert source.count("Invoke-WdQ4StartupMindRead -DescriptorPath") == 1  # one call, no retry
    body = source[source.index("function Invoke-WdQ4StartupMindRead"):source.index("function Get-WdMindStartupReadGuidance")]
    assert ".Kill(" not in body and "Start-Process" not in body
    assert "' -WithStatus'" in body and "-Runtime" not in body and "-Live" not in body and "-WithCoverage" not in body
    assert "wd_q4_lane_caller_cli" not in body  # the route is the helper default, never a direct wrapper call
