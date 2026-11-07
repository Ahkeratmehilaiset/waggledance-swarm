"""Exercise prompt construction only; no lanes, descriptors or bridge data are opened."""
import json
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
PS = str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


@pytest.mark.parametrize("relative,variable", [
    ("ops/windows/reboot/start-wd-agent.ps1", "startupPrompt"),
    ("ops/windows/reboot/start-wd-tools-consumer.ps1", "prompt"),
])
def test_p1_is_advertised_only_with_validated_descriptor(relative, variable):
    # Parse (do not execute) the launcher, and evaluate only its literal Q4
    # prompt expression and the pure prompt formatter in an isolated process.
    script = r'''
$file = $env:WD_TEST_LAUNCHER; $variable = $env:WD_TEST_PROMPT_VARIABLE
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($file, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'parse failed' }
$f = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdQ4P1StartupGuidance' }, $true))
if ($f.Count -ne 1) { throw 'formatter missing' }
. ([scriptblock]::Create($f[0].Extent.Text))
$a = @($ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq ('$' + $variable) -and $n.Operator -eq 'PlusEquals' -and $n.Right.Extent.Text.Contains('Q4 on-demand recall:') }, $true))
if ($a.Count -ne 1) { throw 'prompt missing' }
$original = & ([scriptblock]::Create($a[0].Right.Extent.Text))
$off = Get-WdQ4P1StartupGuidance -Text $original -DescriptorValidated $false
$on = Get-WdQ4P1StartupGuidance -Text $original -DescriptorValidated $true
[ordered]@{
 off_equal = ($off -ceq $original)
 on_p1 = $on.Contains('-Runtime -Live -LiveP1')
 on_legacy = $on.Contains('"Invoke-WdQ4LaneRecall.ps1") -Runtime;')
 on_version = $on.Contains('expected_binding.card_policy_sha256')
 on_provisioned = $on.Contains('P1 selector is provisioned')
 on_no_coverage = $on.Contains('do not add -WithCoverage')
 on_no_fallback = $on.Contains('do not retry or fall back')
 on_no_proof = $on.Contains('not proof of an installed or used Mind')
 on_old_unqualified = $on.Contains('do not combine -Runtime with -Live or -WithCoverage.')
} | ConvertTo-Json -Compress
'''
    env = dict(os.environ, WD_TEST_LAUNCHER=str(ROOT / relative), WD_TEST_PROMPT_VARIABLE=variable)
    result = subprocess.run([PS, "-NoProfile", "-NonInteractive", "-Command", script],
                            text=True, capture_output=True, timeout=30, env=env)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert all(observed[key] for key in observed if key != "on_old_unqualified"), observed
    assert not observed["on_old_unqualified"]


@pytest.mark.parametrize("relative,variable,reference", [
    ("ops/windows/reboot/start-wd-agent.ps1", "startupPrompt", "$q4SharedValues"),
    ("ops/windows/reboot/start-wd-tools-consumer.ps1", "prompt", "$toolsSharedQ4Values"),
])
def test_actual_lane_prompt_is_conditioned_on_validated_pair(relative, variable, reference):
    source = (ROOT / relative).read_text(encoding="utf-8")
    call = f"${variable} = Get-WdQ4P1StartupGuidance -Text ${variable} -DescriptorValidated ($null -ne {reference})"
    assert call in source
    assert source.index(call) > source.index(f"{reference} = Get-Wd")
