"""Synthetic-only checks for fleet shared-Q4 argument forwarding; no fleet launch."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/reboot/start-wd-all.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")


def psquote(value):
    return "'" + str(value).replace("'", "''") + "'"


def invoke_helper(path, digest):
    # Load only the function definition from the AST; no real launcher code executes.
    script = ("$ErrorActionPreference='Stop'; "
              f"$a=[System.Management.Automation.Language.Parser]::ParseFile({psquote(SCRIPT)},[ref]$null,[ref]$null); "
              "$f=@($a.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
              "-and $n.Name -ceq 'Get-WdQ4SharedForwardingArguments'},$true)); "
              "if($f.Count -ne 1){throw 'helper_missing'}; . ([scriptblock]::Create($f[0].Extent.Text)); "
              "$script:launched=$false; function Fake-Process { $script:launched=$true }; "
              f"try {{$r=Get-WdQ4SharedForwardingArguments -Path {psquote(path)} -Sha256 {psquote(digest)}; "
              "Fake-Process; [ordered]@{ok=$true;launched=$script:launched;direct=$r.direct;native=@($r.native)} "
              "| ConvertTo-Json -Compress -Depth 6} "
              "catch {[ordered]@{ok=$false;launched=$script:launched}|ConvertTo-Json -Compress}")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_off_passes_no_forwarding_arguments():
    result = invoke_helper("", "")
    assert result == {"ok": True, "launched": True, "direct": {}, "native": []}


def test_valid_pair_is_forwarded_as_separate_arguments_including_spaces():
    path, digest = r"C:\Python\audit folder\descriptor.json", "a" * 64
    result = invoke_helper(path, digest)
    assert result["ok"] and result["launched"]
    assert result["direct"] == {"Q4SharedDescriptorPath": path, "Q4SharedDescriptorSha256": digest}
    assert result["native"] == ["-Q4SharedDescriptorPath", '"' + path + '"', "-Q4SharedDescriptorSha256", digest]


@pytest.mark.parametrize("path,digest", [(r"C:\p\d.json", ""), ("", "a" * 64),
                                         (r"C:\p\d.json", "x" * 64),
                                         ('C:\\p\\bad".json', "a" * 64),
                                         (r"C:\p\bad;new-tab.json", "a" * 64),
                                         (r"C:\p;new-tab\descriptor.json", "a" * 64),
                                         (r"C:\p\..\d.json", "a" * 64)])
def test_invalid_pairs_refuse_before_fake_process(path, digest):
    assert invoke_helper(path, digest) == {"ok": False, "launched": False}


def test_actual_launcher_guards_and_routes_both_preflights_and_launch():
    text = SCRIPT.read_text(encoding="utf-8")
    guard = text.index("$q4SharedForward = Get-WdQ4SharedForwardingArguments")
    assert guard < text.index("Set-WdFleetWindowsPowerShellModulePath\n")
    assert text.count("@q4SharedDirect") == 3
    tools_preflight = text[text.index("$toolsValidationOutput = @("):text.index("$toolsValidation = @(")]
    assert "-ValidateOnly @q4SharedDirect" in tools_preflight
    assert "$wtArguments += $q4SharedForward.native" in text


@pytest.mark.parametrize("arguments", [["-Q4SharedDescriptorPath", r"C:\p\d.json"],
                                       ["-Q4SharedDescriptorSha256", "a" * 64]])
def test_real_script_rejects_partial_pair_at_entry_before_launcher_work(arguments):
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), *arguments],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0 and "q4_shared_descriptor_pair_refused" in result.stderr


def test_real_script_rejects_terminal_separator_at_entry():
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
                             "-Q4SharedDescriptorPath", r"C:\p\d;new-tab.json",
                             "-Q4SharedDescriptorSha256", "a" * 64],
                            text=True, capture_output=True, timeout=15)
    assert result.returncode != 0 and "q4_shared_descriptor_pair_refused" in result.stderr


def test_actual_ast_calls_forward_to_fake_four_lane_launchers():
    script = r'''
$ErrorActionPreference='Stop'
$ast=[System.Management.Automation.Language.Parser]::ParseFile(__SCRIPT__,[ref]$null,[ref]$null)
$function=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdQ4SharedForwardingArguments'},$true))
if($function.Count -ne 1){throw 'function_missing'}
. ([scriptblock]::Create($function[0].Extent.Text))
$q4SharedForward=Get-WdQ4SharedForwardingArguments -Path 'C:\Python\audit folder\descriptor.json' -Sha256 ('a'*64)
$q4SharedDirect=$q4SharedForward.direct
$script:calls=[Collections.Generic.List[object]]::new()
function Fake-Lane {
 param([string]$Agent,[switch]$DryRun,[switch]$CheckManagedAdmission,[string]$ExpectedManifestHash,
 [string]$ExternalSessionsPath,[string]$ExternalSessionsHash,[string]$Q4SharedDescriptorPath,
 [string]$Q4SharedDescriptorSha256)
 $script:calls.Add([pscustomobject]@{agent=$Agent;path=$Q4SharedDescriptorPath;hash=$Q4SharedDescriptorSha256})
 return [pscustomobject]@{dry_run=$true}
}
$agentLauncherTarget=${function:Fake-Lane}; $bundleManifestAnchor='manifest'
$ExternalSessionsPath=''; $ExternalSessionsHash=''
$assignments=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
 $n.Left.Extent.Text -in @('$admission','$resumePlan') -and $n.Extent.Text.Contains('$agentLauncherTarget')},$true))
if($assignments.Count -ne 2){throw 'preflight_assignments_missing'}
foreach($id in @('codex-lead-1','claude-rco-1','claude-rco-2','fable-5')) {
 $lane=[pscustomobject]@{agent=$id}
 foreach($assignment in $assignments) { . ([scriptblock]::Create($assignment.Extent.Text)) }
}
$state=[pscustomobject]@{lane=[pscustomobject]@{agent='fable-5';worktree='C:\Python\worktree'}}
$expectedSupervisorExecutable='fake';$agentLauncher='fake';$ManifestPath='fleet';$RunId='id';$handshakeDirectory='audit'
$wtPath='fake';$script:captured=$null
function Start-Process { param($FilePath,$ArgumentList,[switch]$PassThru); $script:captured=@($ArgumentList) }
$init=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
 $n.Left.Extent.Text -ceq '$wtArguments' -and $n.Extent.Text.Contains("'new-tab'")},$true))
$append=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
 $n.Left.Extent.Text -ceq '$wtArguments' -and $n.Extent.Text.Contains('$q4SharedForward.native')},$true))
$start=@($ast.FindAll({param($n) $n -is [System.Management.Automation.Language.CommandAst] -and
 $n.GetCommandName() -ceq 'Start-Process' -and $n.Extent.Text.Contains('$wtArguments')},$true))
if($init.Count -ne 1 -or $append.Count -ne 1 -or $start.Count -ne 1){throw 'launch_statements_missing'}
. ([scriptblock]::Create($init[0].Extent.Text)); . ([scriptblock]::Create($append[0].Extent.Text))
. ([scriptblock]::Create($start[0].Extent.Text))
[ordered]@{calls=@($script:calls);native=@($script:captured)}|ConvertTo-Json -Depth 8 -Compress
'''.replace("__SCRIPT__", psquote(SCRIPT))
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16-le")).decode("ascii")],
                            text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert len(output["calls"]) == 8
    assert {call["agent"] for call in output["calls"]} == {"codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5"}
    assert all(call["path"] == r"C:\Python\audit folder\descriptor.json" and call["hash"] == "a" * 64
               for call in output["calls"])
    assert output["native"][-4:] == ["-Q4SharedDescriptorPath", '"C:\\Python\\audit folder\\descriptor.json"',
                                     "-Q4SharedDescriptorSha256", "a" * 64]
