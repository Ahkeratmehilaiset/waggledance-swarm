"""Synthetic inspection of generated wrappers; no deployment or real launcher."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")


def test_generated_fleet_and_agent_wrappers_forward_descriptor_pair():
    quoted = str(DEPLOY).replace("'", "''")
    code = rf"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{quoted}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'deployer parse error' }}
$node=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'New-ForwardingWrapper'}},$true)
if ($null -eq $node) {{ throw 'generator missing' }}
. ([scriptblock]::Create($node.Extent.Text))
$out=[ordered]@{{}}
foreach ($kind in @('fleet','agent')) {{
  $text=New-ForwardingWrapper -Target 'C:\synthetic\target.ps1' -ExpectedHash ('A'*64) -ExpectedManifestHash ('B'*64) -WrapperKind $kind
  $t=$null; $e=$null; $wrapper=[Management.Automation.Language.Parser]::ParseInput($text,[ref]$t,[ref]$e)
  if ($e.Count) {{ throw 'generated wrapper parse error' }}
  $names=@($wrapper.ParamBlock.Parameters | ForEach-Object {{ $_.Name.VariablePath.UserPath }})
  $out[$kind]=[ordered]@{{params=$names; pair_guard=$text.Contains('q4_shared_descriptor_pair_refused');
    forwarding=$text.Contains('Q4SharedDescriptorPath'',''Q4SharedDescriptorSha256') -or
      ($text.Contains("'Q4SharedDescriptorPath',") -and $text.Contains("'Q4SharedDescriptorSha256'"))}}
}}
$out | ConvertTo-Json -Depth 5 -Compress
"""
    done = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-Command", code],
                          capture_output=True, timeout=30, check=True)
    value = json.loads(done.stdout.decode("utf-8-sig"))
    for kind in ("fleet", "agent"):
        assert {"Q4SharedDescriptorPath", "Q4SharedDescriptorSha256"} <= set(value[kind]["params"])
    assert value["fleet"]["pair_guard"]
    assert value["fleet"]["forwarding"]
