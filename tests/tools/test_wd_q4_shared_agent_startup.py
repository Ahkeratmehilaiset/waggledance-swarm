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
$seen = @{ query = $query; switches = @($PSBoundParameters.Keys | Sort-Object); timeout = $TimeoutSeconds
  env = @{ bundle = [string]$env:WD_BRIDGE_BUNDLE_ROOT; wrapper = [string]$env:WD_BRIDGE_PYTHON_WRAPPER; pin = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH } }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'fake-seen.json'), ($seen | ConvertTo-Json -Compress))
if ($mode -eq 'hang') { Start-Sleep -Seconds 4; exit 0 }
if ($mode -eq 'refuse') { if ($WithStatus) { [Console]::Error.WriteLine('{"status":"UNKNOWN","exit_code":3,"cleanup":"unknown"}') }; exit 3 }
if ($mode -eq 'garbage') { [Console]::Out.Write('{"schema":"other"}' + "`n"); exit 0 }
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
                         manifest_case="ok", env_case="ok", audit_case="ok"):
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
              + "\n" + startup_read_call(fx, lane, timeout=(1 if changes.get("mode") in ("hang", "orphan") else 5)))
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
    assert stored == {key: value for key, value in record.items() if key not in ("delivered_text", "record_written")}
    assert record["record_written"] is True and record["helper_env"] == "verified"
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


def test_startup_read_delivers_the_approved_answer_in_memory_and_never_on_disk(tmp_path):
    """S1: the lane gets the recalled content itself (in memory); the record keeps only its digest and size."""
    fx, record = run_startup_read(tmp_path)
    assert record["outcome"] == "success" and record["delivered_items"] == 1
    delivered = record["delivered_text"]
    assert delivered.startswith("[1] STORED-TEXT-SECRET ") and record["delivered_chars"] == len(delivered)
    assert record["delivered_sha256"] == hashlib.sha256(delivered.encode("utf-8")).hexdigest()
    on_disk = fx["record"].read_text(encoding="ascii")
    assert_no_secret(on_disk)
    assert "delivered_text" not in on_disk and "record_written" not in on_disk
    assert json.loads(on_disk)["delivered_sha256"] == record["delivered_sha256"]


@pytest.mark.parametrize("mode", ["refuse", "garbage", "hang"])
def test_startup_read_delivers_nothing_unless_the_answer_validated(tmp_path, mode):
    fx, record = run_startup_read(tmp_path, mode=mode)
    assert record["outcome"] != "success"
    assert record["delivered_text"] == "" and record["delivered_items"] == 0 and record["delivered_chars"] == 0
    assert record["delivered_sha256"] == ""


@pytest.mark.parametrize("mode", ["abstain", "degraded", "zero"])
def test_startup_read_delivers_only_an_answer_with_results(tmp_path, mode):
    """Only a nonempty ANSWER is successful (Lead numbered A1 criteria 20:14:44Z)."""
    fx, record = run_startup_read(tmp_path, mode=mode)
    assert record["outcome"] == "empty" and record["reason"] == ("no_result" if mode == "zero" else mode)
    assert record["inner_status"] == ("ANSWER" if mode == "zero" else mode.upper())
    assert record["delivered_text"] == "" and record["delivered_items"] == 0 and record["delivered_sha256"] == ""
    assert record["result_count"] == (1 if mode == "degraded" else 0)


def test_startup_read_bounds_the_complete_operation_by_one_wall_clock_deadline(tmp_path):
    """The helper exits but a process it started holds the output pipes: the read ends at the deadline as UNKNOWN
    with nothing parsed, instead of waiting on the output read without bound (Lead 19:34:51Z)."""
    fx, record = run_startup_read(tmp_path, mode="orphan")
    assert record["outcome"] == "unknown" and record["reason"] == "io_timeout" and record["exit_code"] == 0
    assert record["cleanup"] == "unknown" and record["refs"] == [] and record["delivered_items"] == 0
    assert record["answer_sha256"] == "" and record["duration_ms"] < 5500


def test_startup_read_measures_its_duration(tmp_path):
    """S2: elapsed time is recorded, for a hung helper at least the wait it actually spent."""
    fx, record = run_startup_read(tmp_path)
    assert isinstance(record["duration_ms"], int) and record["duration_ms"] >= 0
    assert json.loads(fx["record"].read_bytes())["duration_ms"] == record["duration_ms"]
    fx, record = run_startup_read(tmp_path / "hang", mode="hang") if (tmp_path / "hang").mkdir() is None else None
    assert record["reason"] == "timeout" and record["duration_ms"] >= 1000


def test_startup_read_record_write_failure_does_not_block_the_start(tmp_path):
    """Startup continues after a record failure, with no unverifiable delivery."""
    fx, record = run_startup_read(tmp_path, audit_case="file")
    assert record["outcome"] == "unknown" and record["reason"] == "record_write"
    assert record["record_written"] is False
    assert record["delivered_items"] == 0 and record["delivered_text"] == ""
    assert (fx["worktree"] / ".codex-audit").is_file() and not fx["record"].exists()


def test_startup_read_sets_the_verified_helper_environment_for_the_child(tmp_path):
    """V1: the helper refuses without its three environment variables; the read sets them from verified values."""
    fx, record = run_startup_read(tmp_path, env_case="unset")
    assert record["outcome"] == "success" and record["helper_env"] == "verified"
    seen = json.loads(fx["seen"].read_text(encoding="utf-8-sig"))
    assert seen["env"] == {"bundle": str(fx["bundle"]), "wrapper": str(fx["wrapper"]),
                           "pin": fx["manifest_pin"].upper()}


def test_startup_read_refuses_without_running_when_the_helper_environment_cannot_be_verified(tmp_path):
    for changes in ({"env_case": "mismatch"}, {"manifest_case": "wrapper_pin"}):
        sub = tmp_path / "-".join(changes.values())
        sub.mkdir()
        fx, record = run_startup_read(sub, **changes)
        assert record["outcome"] == "refused" and record["reason"] == "helper_env", record
        assert record["helper_env"] == "" and not fx["seen"].exists() and record["delivered_items"] == 0


def test_launcher_runs_the_startup_read_after_dry_run_returns_and_before_the_cli_launch():
    source = LAUNCHER.read_text(encoding="utf-8-sig")
    call = source.index("Invoke-WdQ4StartupMindRead -DescriptorPath")
    assert source.index("DRY RUN: bridge bootstrap, handshake write, and CLI launch suppressed.") < call
    assert source.index("SetEnvironmentVariable('WD_Q4_SHARED_DESCRIPTOR_SHA256'") < call
    assert call < source.index("$launchArguments += $startupPrompt")
    assert "$startupPrompt += $startupMindReadGuidance" in source
    delivery = source.index("SetEnvironmentVariable('WD_MIND_STARTUP_RECALL'")
    # set immediately before the launch, so the lane CLI is the process that inherits it; never argv or the prompt
    assert source.index("$env:WD_GROK_MODEL_GUIDE = $grokMarkdown") < delivery < source.index("$launchArguments += $startupPrompt")
    assert source.count("SetEnvironmentVariable('WD_MIND_STARTUP_RECALL'") == 1 and source.count("delivered_text") == 2
    assert "-RecordWritten ([bool]$startupMindRead.record_written)" in source
    assert "-DeliveredChars ([int]$startupMindRead.delivered_chars)" in source
    assert "$continuationPrompt += $startupMindReadGuidance" in source
    assert source.count("Invoke-WdQ4StartupMindRead -DescriptorPath") == 1  # one call, no retry
    body = source[source.index("function Invoke-WdQ4StartupMindRead"):source.index("function Get-WdMindStartupReadGuidance")]
    assert ".Kill(" not in body and "Start-Process" not in body
    assert "' -WithStatus'" in body and "-Runtime" not in body and "-Live" not in body and "-WithCoverage" not in body
    assert "wd_q4_lane_caller_cli" not in body  # the route is the helper default, never a direct wrapper call
    assert "WaitAll(" in body and "io_timeout" in body and "output_over_budget" in body
    assert "$deadline" in body and ".BaseStream.Write($query" not in body


def test_startup_failed_record_replace_never_delivers_against_a_prior_receipt(tmp_path):
    """An old receipt is not the matching digest for this recall; no real ACL or startup is used."""
    fx, record = run_startup_read(tmp_path, audit_case="stale")
    assert record["outcome"] == "unknown" and record["reason"] == "record_write"
    assert record["record_written"] is False
    assert record["delivered_text"] == "" and record["delivered_items"] == 0
    assert record["delivered_chars"] == 0 and record["delivered_sha256"] == ""
    assert fx["record"].read_text(encoding="ascii") == "prior-receipt"


def test_startup_missing_record_never_promises_digest_verification():
    command = "Get-WdMindStartupReadGuidance -RecordPath 'synthetic-receipt' -Outcome success -RecordWritten $false -DeliveredChars 20 -DeliveredItems 1"
    from tests.tools.test_wd_startup_recovery import load
    script = load(LAUNCHER, "Get-WdMindStartupReadGuidance") + "\n" + command
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    output = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, check=True, timeout=20).stdout
    assert "No recalled content was delivered." in output
    assert "WD_MIND_STARTUP_RECALL" not in output and "delivered_sha256" not in output
    assert "record could not be written" in output



@pytest.mark.parametrize("fault", ["own_session", "starter_refuses", "missing_identity", "wrong_lane", "outside_cwd"])
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
        assert observed["read"]["outcome"] == "success", observed["read"]["reason"]
        assert observed["read"]["record_written"] is True and observed["read"]["delivered_items"] == 1


@pytest.mark.parametrize("case", ["unset", "null", "unwritten", "non_success", "empty", "success",
                                 "missing_receipt", "missing_outcome"])
@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell execution required")
def test_startup_delivery_guard_is_strict_mode_safe(tmp_path, case):
    """Execute the real delivery statement with optional result absent, refused, or authorized."""
    import subprocess

    variable = "startupMindRead"
    source_path = Path(__file__).resolve().parents[2] / "ops/windows/reboot/start-wd-agent.ps1"
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
