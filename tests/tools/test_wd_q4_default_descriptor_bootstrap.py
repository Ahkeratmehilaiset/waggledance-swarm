"""Synthetic post-deploy descriptor pins; never modify installed launchers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_wd_startup_recovery import load, q


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@pytest.fixture
def staged(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    helper = bundle / HELPER.name
    helper.write_bytes(HELPER.read_bytes())
    resolver = bundle / "Get-WdQ4DefaultDescriptor.ps1"
    resolver.write_bytes((ROOT / "ops/windows/reboot/Get-WdQ4DefaultDescriptor.ps1").read_bytes())
    recall = bundle / "Invoke-WdQ4LaneRecall.ps1"
    recall.write_bytes(b"# synthetic pinned helper: never executed\n")
    limited = bundle / "WdBridgeLimitedBootstrap.ps1"
    limited.write_text("function Invoke-WdBridgeWrapperBootstrap {\n"
        "param($BundleRoot,$FleetManifestPath,$SupervisorScript,$HostPath,$Q4SharedDescriptorPath,$Q4SharedDescriptorSha256)\n"
        "$global:capturedQ4=@{path=$Q4SharedDescriptorPath;hash=$Q4SharedDescriptorSha256}\n"
        "return [pscustomobject]@{ok=$true;stage='done';reasons=@()}\n}\n", encoding="utf-8")
    target = bundle / "start-wd-all.ps1"
    target.write_text("param([string]$Q4SharedDescriptorPath='', [string]$Q4SharedDescriptorSha256='', [switch]$DryRun)\n"
                      "@{path=$Q4SharedDescriptorPath;hash=$Q4SharedDescriptorSha256}|ConvertTo-Json -Compress\n",
                      encoding="utf-8")
    fleet = {"lanes": [{"agent": lane} for lane in LANES if lane != "codex-tools-1"],
             "tools_supervisor": {"agent": "codex-tools-1"}}
    fleet_raw = canon(fleet)
    (bundle / "wd-fleet.json").write_bytes(fleet_raw)
    manifest_raw = canon({"schema_version": 1, "files": {
        "wd-fleet.json": sha(fleet_raw), HELPER.name: sha(helper.read_bytes()),
        resolver.name: sha(resolver.read_bytes()), recall.name: sha(recall.read_bytes()),
        limited.name: sha(limited.read_bytes()).upper(),
        target.name: sha(target.read_bytes())}})
    manifest = bundle / "deployment-manifest.json"
    manifest.write_bytes(manifest_raw)
    binding_fields = {name: "a" * 64 for name in (
        "pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
        "public_policy_sha256", "descriptor_sha256", "projection_sha256")}
    binding = {"schema": "wd.q4-lane-binding.v1", "bundle_commit": "b" * 40,
        "manifest_sha256": sha(manifest_raw), "wrapper_sha256": "c" * 64, "public_plan_sha256": "d" * 64,
        "binding": binding_fields, "lanes": LANES}
    generation = tmp_path / "generations" / ("b" * 40)
    generation.mkdir(parents=True)
    binding_path = generation / "lane-binding.v1.json"
    binding_raw = canon(binding)
    binding_path.write_bytes(binding_raw)
    runtime = tmp_path / "runtime"
    descriptor = {"schema": "wd.q4-shared-session-config.v2", "lanes": LANES,
        "bundle_root": str(bundle), "public_plan": str(tmp_path / "public.json"),
        "interpreter": str(tmp_path / "python.exe"), "cwd": str(tmp_path / "work"),
        "binding_path": str(binding_path), "fixed_plan_path": str(tmp_path / "fixed.json"),
        "snapshot_path": str(tmp_path / "snapshot.json"), "runtime_manifest_path": str(tmp_path / "runtime.json"),
        "fleet_sha256": sha(fleet_raw), "interpreter_sha256": "e" * 64,
        "binding_sha256": sha(binding_raw), "fixed_plan_sha256": "f" * 64,
        "snapshot_sha256": "1" * 64, "runtime_manifest_sha256": "2" * 64,
        "interpreter_max_bytes": 1024, "timeout_seconds": 30, "max_output_bytes": 1024,
        "outer": {"outer-path": str(runtime / "assets/code/tools/wd_q4_session_outer_synthetic.py"),
          "outer-sha256": "3" * 64, "runtime-root": str(runtime), "runtime-generation": "4" * 40,
          "runtime-revision": "5" * 40, "verifier-sha256": "6" * 64, "public-outer-sha256": "7" * 64,
          "public-child-sha256": "8" * 64, "public-query-sha256": "9" * 64, "worker-sha256": "0" * 64},
        "expected_binding": binding_fields}
    descriptor_path = generation / "shared-session-config.v2.json"
    descriptor_raw = canon(descriptor)
    descriptor_path.write_bytes(descriptor_raw)
    return {"bundle": bundle, "manifest": manifest, "manifest_sha": sha(manifest_raw),
            "binding": binding_path, "binding_doc": binding, "descriptor": descriptor_path,
            "descriptor_doc": descriptor, "descriptor_sha": sha(descriptor_raw), "target": target,
            "resolver": resolver, "recall": recall}


@pytest.mark.parametrize("case", ["off", "half_path", "half_hash", "wrong_descriptor", "wrong_manifest",
                                   "wrong_bundle", "wrong_binding_manifest", "ok"])
def test_postdeploy_default_is_independently_pinned_and_matches_active_bundle(staged, case):
    item = staged
    path, digest, manifest_hash = str(item["descriptor"]), item["descriptor_sha"], item["manifest_sha"]
    if case == "off": path = digest = ""
    if case == "half_path": digest = ""
    if case == "half_hash": path = ""
    if case == "wrong_descriptor": digest = "0" * 64
    if case == "wrong_manifest": manifest_hash = "0" * 64
    if case in ("wrong_bundle", "wrong_binding_manifest"):
        descriptor = dict(item["descriptor_doc"])
        if case == "wrong_bundle":
            descriptor["bundle_root"] = str(item["bundle"].parent / "different-bundle")
        else:
            binding = dict(item["binding_doc"])
            binding["manifest_sha256"] = "0" * 64
            binding_raw = canon(binding)
            item["binding"].write_bytes(binding_raw)
            descriptor["binding_sha256"] = sha(binding_raw)
        raw = canon(descriptor)
        item["descriptor"].write_bytes(raw)
        digest = sha(raw)  # A deliberately supplied test pin, never a production trust decision.
    code = "\n".join(load(INSTALLER, name) for name in (
        "Resolve-FullPath", "Assert-WdPathWithoutReparse", "Read-Utf8DeploymentSnapshot",
        "Get-WdPinnedQ4InstallDefault")) + f'''
try {{
 $r=Get-WdPinnedQ4InstallDefault -DescriptorPath {q(path)} -DescriptorSha256 {q(digest)} `
   -BundleRoot {q(item["bundle"])} -ExpectedManifestHash {q(manifest_hash)} -ExpectedCommit {'b' * 40}
 @{{ok=$true;configured=($null -ne $r);hash=$(if($null -ne $r){{$r.DescriptorSha256}}else{{''}})}}|ConvertTo-Json -Compress
}} catch {{ @{{ok=$false}}|ConvertTo-Json -Compress }}
'''
    completed = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", code],
                               capture_output=True, text=True, timeout=20, check=False)
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout)
    if case == "off":
        assert actual == {"ok": True, "configured": False, "hash": ""}
    elif case == "ok":
        assert actual == {"ok": True, "configured": True, "hash": digest}
    else:
        assert actual == {"ok": False}


@pytest.mark.parametrize("case", ["off", "ready", "missing_pointer", "tampered_pointer", "tampered_helper",
                                   "tampered_resolver", "manual", "conflict"])
def test_generated_wrapper_forwards_same_validated_default_or_stays_off(staged, case):
    """Generated wrapper and its target are synthetic; never elevate or launch lanes."""
    item = staged
    machine = item["bundle"].parent
    state = {"source_commit": "b" * 40, "final_commit": "b" * 40,
             "manifest_sha256": item["manifest_sha"].upper(),
             "final_manifest_sha256": item["manifest_sha"].upper(), "active_bundle": str(item["bundle"])}
    (machine / "WD_REBOOT_STATE_CURRENT.json").write_bytes(canon(state))
    pointer = {"schema": "wd.q4-descriptor-pointer.v1", "active_bundle": str(item["bundle"]),
        "manifest_sha256": item["manifest_sha"].upper(), "bundle_commit": "b" * 40,
        "descriptor_path": str(item["descriptor"]), "descriptor_sha256": item["descriptor_sha"],
        "generation": "b" * 40}
    pointer_path = machine / "WD_Q4_DESCRIPTOR_CURRENT.json"
    pointer_raw = canon(pointer)
    pin = "" if case in ("off", "manual") else sha(pointer_raw)
    if pin:
        pointer_path.write_bytes(pointer_raw)
    wrapper = machine / "synthetic-start.ps1"
    generator = load(INSTALLER, "New-ForwardingWrapper") + f'''
$script = New-ForwardingWrapper -Target {q(item["target"])} -ExpectedHash {q(sha(item["target"].read_bytes()).upper())} `
 -ExpectedManifestHash {q(item["manifest_sha"].upper())} -WrapperKind fleet -ExpectedFinalCommit {'b' * 40} `
 -ExpectedFinalManifestHash {q(item["manifest_sha"].upper())} -ExpectedQ4PointerSha256 {q(pin)}
[IO.File]::WriteAllText({q(wrapper)}, $script, [Text.UTF8Encoding]::new($false))
'''
    built = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", generator],
                           capture_output=True, text=True, timeout=20, check=False)
    assert built.returncode == 0 and wrapper.exists(), built.stderr
    if case == "missing_pointer": pointer_path.unlink()
    if case == "tampered_pointer": pointer_path.write_bytes(pointer_raw + b" ")
    if case == "tampered_helper": item["recall"].write_bytes(b"# tampered\n")
    if case == "tampered_resolver": item["resolver"].write_bytes(b"# tampered\n")
    args = [SHELL, "-NoProfile", "-NonInteractive", "-File", str(wrapper), "-DryRun"]
    if case in ("manual", "conflict"):
        args += ["-Q4SharedDescriptorPath", str(item["descriptor"]), "-Q4SharedDescriptorSha256",
                 item["descriptor_sha"] if case == "manual" else "0" * 64]
    completed = subprocess.run(args, capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    actual = json.loads(completed.stdout.strip().splitlines()[-1])
    if case in ("ready", "manual"):
        assert actual == {"path": str(item["descriptor"]), "hash": item["descriptor_sha"]}
    else:
        assert actual == {"path": "", "hash": ""}
    if case in ("missing_pointer", "tampered_pointer", "tampered_helper", "tampered_resolver", "conflict"):
        assert "Q4 default unavailable" in completed.stderr
    code = wrapper.read_text(encoding="utf-8")
    assert "-Q4SharedDescriptorPath $Q4SharedDescriptorPath" in code
    assert "-Q4SharedDescriptorSha256 $Q4SharedDescriptorSha256" in code


@pytest.mark.parametrize("case", ["ready", "tampered_pointer"])
def test_auto_sends_identical_default_to_fake_elevation_and_limited_bootstrap(staged, case):
    """Instrumented wrapper: fake elevation and bootstrap, no process is launched."""
    item = staged
    machine = item["bundle"].parent
    state = {"source_commit": "b" * 40, "final_commit": "b" * 40,
             "manifest_sha256": item["manifest_sha"].upper(),
             "final_manifest_sha256": item["manifest_sha"].upper(), "active_bundle": str(item["bundle"])}
    (machine / "WD_REBOOT_STATE_CURRENT.json").write_bytes(canon(state))
    pointer = {"schema": "wd.q4-descriptor-pointer.v1", "active_bundle": str(item["bundle"]),
        "manifest_sha256": item["manifest_sha"].upper(), "bundle_commit": "b" * 40,
        "descriptor_path": str(item["descriptor"]), "descriptor_sha256": item["descriptor_sha"],
        "generation": "b" * 40}
    raw = canon(pointer)
    pointer_path = machine / "WD_Q4_DESCRIPTOR_CURRENT.json"
    pointer_path.write_bytes(raw if case == "ready" else raw + b" ")
    wrapper = machine / "synthetic-auto.ps1"
    script = load(INSTALLER, "New-ForwardingWrapper") + f'''
$body=New-ForwardingWrapper -Target {q(item["target"])} -ExpectedHash {q(sha(item["target"].read_bytes()).upper())} `
 -ExpectedManifestHash {q(item["manifest_sha"].upper())} -WrapperKind fleet -ExpectedFinalCommit {'b' * 40} `
 -ExpectedFinalManifestHash {q(item["manifest_sha"].upper())} -ExpectedQ4PointerSha256 {q(sha(raw))}
$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($body,[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'generated parse error'}}
$f=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-WdWrapperAdministrator'}},$true)
$body=$body.Replace($f.Extent.Text,'function Test-WdWrapperAdministrator {{ return $false }}')
[IO.File]::WriteAllText({q(wrapper)},$body,[Text.UTF8Encoding]::new($false))
$global:capturedQ4=$null;$global:capturedStart=@()
function Start-Process {{
 param($FilePath,$Verb,$ArgumentList,$WindowStyle,[switch]$PassThru,$ErrorAction)
 $global:capturedStart=@($ArgumentList)
 $child=[pscustomobject]@{{Handle=1;ExitCode=0}}
 $child|Add-Member -MemberType ScriptMethod -Name WaitForExit -Value {{if($args.Count){{return $true}}}}
 $child|Add-Member -MemberType ScriptMethod -Name Refresh -Value {{}}
 return $child
}}
& {q(wrapper)} -Auto -SkipCliUpdate
$i=[array]::IndexOf([object[]]$global:capturedStart,'-EncodedCommand')
$command=[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String([string]$global:capturedStart[$i+1]))
@{{bootstrap=$global:capturedQ4;command=$command}}|ConvertTo-Json -Compress -Depth 5
'''
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout.strip().splitlines()[-1])
    if case == "ready":
        assert observed["bootstrap"] == {"path": str(item["descriptor"]), "hash": item["descriptor_sha"]}
        assert str(item["descriptor"]) in observed["command"] and item["descriptor_sha"] in observed["command"]
    else:
        assert observed["bootstrap"] == {"path": "", "hash": ""}
        assert "-Q4SharedDescriptorPath" not in observed["command"]
