# SPDX-License-Identifier: Apache-2.0
"""Cold approval emission and admission fixtures, never an installer/executor run.

During file-disjoint development WD_TEST_MANUAL_EMITTER_SOURCE may select the
owner's exact Source file. CI defaults to this repository's installer. A selected
Source digest is emitted with every result; a missing helper fails setup, never
counts as a successful negative. WD_TEST_EMITTER_MUTANT selects one in-memory
binding mutation for a separate RED run of the same unmodified test assertions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops/windows/reboot"
SOURCE = "a" * 40
DEFINITION_HASH = "B" * 64
TOOLS = ["tools/merge_with_bridge_receipt.py", "tools/write_bridge_consensus_merge_receipt.py"]
ACTIVATION = dict(schema="wd.bridge-manual-execution-activation.v1", enabled=True,
                  source_commit=SOURCE, definition_sha256=DEFINITION_HASH,
                  agent="codex-tools-1", tools=TOOLS, approval_reference_sha256="C" * 64)
DEFINITION = dict(schema="wd.bridge-code-package.v1", python_files=TOOLS, manual_execution_capability=dict(
    schema="wd.bridge-manual-execution.v1", invocation_marker="--wd-manual-execution",
    entrypoints=dict(merge=TOOLS[0], receipt=TOOLS[1])))

# Each replacement removes precisely one binding guard, without altering
# fixtures or test assertions. These are opt-in mutation inputs, not fallbacks.
MUTANTS = {
    "MUT_SOURCE_BINDING": ("$record.source_commit -cne $SourceCommit", "$false"),
    "MUT_DEFINITION_BINDING": ("$record.definition_sha256 -cne $DefinitionHash", "$false"),
    "MUT_APPROVAL_REFERENCE": ("$record.approval_reference_sha256 -cnotmatch '^[0-9A-Fa-f]{64}$'", "$false"),
    "MUT_ENABLED_NATIVE_BOOL": ("$record.enabled -isnot [bool] -or $record.enabled -ne $true", "$false"),
    "MUT_TOOL_BINDING": ("(@($record.tools | Sort-Object) -join ',') -cne ($manualPaths -join ',')", "$false"),
    "MUT_FLEET_ACTOR_BINDING": ("$record.agent -cnotin $FleetAgentNames", "$false"),
    "MUT_AGENT_ALLOWLIST": ("$record.agent -cnotin $agents", "$false"),
    "MUT_SCHEMA_BINDING": ("$record.schema -cne 'wd.bridge-manual-execution-activation.v1'", "$false"),
    "MUT_CLOSED_FIELDS": ("($names -join ',') -cne 'agent,approval_reference_sha256,definition_sha256,enabled,schema,source_commit,tools'", "$false"),
    "MUT_DUPLICATE_JSON": ("-not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($Text)", "$false"),
}


@pytest.fixture(scope="session", params=["powershell", "pwsh"])
def shell(request):
    if os.name != "nt":
        pytest.skip("PowerShell admission parity needs Windows")
    path = (os.environ.get("WD_TEST_PWSH") if request.param == "pwsh" else None) or shutil.which(request.param)
    if path is None and request.param == "pwsh":
        installed = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PowerShell/7/pwsh.exe"
        path = str(installed) if installed.is_file() else None
    if path is None and request.param == "pwsh":
        installed = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps/Microsoft.PowerShell_8wekyb3d8bbwe/pwsh.exe"
        path = str(installed) if installed.is_file() else None
    if path is None:
        pytest.skip(f"{request.param} unavailable")
    try:
        version = subprocess.run([path, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                                  "$PSVersionTable.PSVersion.Major"], capture_output=True, text=True, timeout=15)
    except OSError as exc:
        # Host absence/access denial is NOT parity proof. Report it visibly;
        # reviewers must run the required shell in an eligible environment.
        pytest.skip(f"{request.param} host cannot execute: {type(exc).__name__}")
    assert version.returncode == 0 and version.stdout.strip() == ("7" if request.param == "pwsh" else "5")
    return path


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _invoke(shell, tmp_path, *, text=None, admit=False, omit=False, tail=None,
            anchor="D" * 64, actor="codex-tools-1", runtime_alias=None, fleet=None,
            existing="same"):
    source = Path(os.environ.get("WD_TEST_MANUAL_EMITTER_SOURCE", str(OPS / "Deploy-WdRebootBundle.ps1")))
    before = source.read_bytes()
    fixture = tmp_path / "fixture.json"
    runtime = tmp_path / "runtime"
    runtime.mkdir(exist_ok=True)
    audit = tmp_path / ".codex-audit"
    audit.mkdir(exist_ok=True)
    fixture.write_text(json.dumps(dict(text=json.dumps(ACTIVATION) if text is None else text,
        definition=DEFINITION, admit=admit, omit=omit, tail=tail or [], existing=existing,
        fleet=fleet or ["codex-tools-1"], mutant=os.environ.get("WD_TEST_EMITTER_MUTANT", ""))), encoding="utf-8")
    script = tmp_path / "probe.ps1"
    script.write_text("$ErrorActionPreference='Stop'\n" +
        f"$source={_quote(source)}\n$fixture={_quote(fixture)}\n" +
        f". {_quote(ROOT / '.agent-bridge/bin/BridgeLogReader.ps1')}\n" +
        f". {_quote(OPS / 'BridgeCodeContext.ps1')}\n" +
        "$f=Get-Content -LiteralPath $fixture -Raw|ConvertFrom-Json\n"
        "$tokens=$null;$errors=$null\n"
        "$ast=[Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'EMITTER_PARSE_FAILED'}\n"
        "$defs=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true))\n"
        "$helper=@($defs|Where-Object {$_.Name -ceq 'ConvertFrom-WdManualExecutionApprovalRecord'})\n"
        "$binding=@($defs|Where-Object {$_.Name -ceq 'Assert-WdManualExecutionManifestBinding'})\n"
        "if($helper.Count -ne 1 -or $binding.Count -ne 1){throw 'EMITTER_HELPER_MISSING'}\n"
        "$body=$helper[0].Extent.Text\n" +
        "$mutations=ConvertFrom-Json -InputObject " + _quote(json.dumps(MUTANTS)) + "\n"
        "if($f.mutant){\n"
        " $m=$mutations.PSObject.Properties[[string]$f.mutant]\n"
        " if($null -eq $m -or ([regex]::Matches($body,[regex]::Escape([string]$m.Value[0]))).Count -ne 1){throw 'MUTATION_NOT_EXACT'}\n"
        " $body=$body.Replace([string]$m.Value[0],[string]$m.Value[1])\n}\n"
        ". ([scriptblock]::Create($body))\n"
        ". ([scriptblock]::Create($binding[0].Extent.Text))\n"
        "$manifestObject=[ordered]@{source_commit='" + SOURCE + "'}\n"
        "$head='" + SOURCE + "';$branch='synthetic';$upstream='synthetic';$sourceHashes=@{}\n"
        "$bridgeCodeDefinition=$f.definition;$bridgeCodePackage=@{Hash='" + DEFINITION_HASH + "'}\n"
        "$bridgePython=@{Path='inert';Sha256=('E'*64)};$bridgePythonStage=@{Wheels=@{};Site=@{}}\n"
        "$definitionHash='" + DEFINITION_HASH + "';$manifestHash=('D'*64)\n"
        "$before=@(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File|Select-Object -ExpandProperty FullName)\n"
        "try {\n"
        " $manualActivation=$null\n"
        " if(-not $f.omit){$manualActivation=ConvertFrom-WdManualExecutionApprovalRecord -Text $f.text -SourceCommit $head -DefinitionHash $definitionHash -Definition $f.definition -FleetAgentNames @($f.fleet)}\n"
        # Execute only the real manifest assignment and its adjacent activation
        # conditional from the installer AST, never its top-level install flow.
        " $assign=@($ast.EndBlock.Statements|Where-Object {$_.Extent.Text -match '^\\$manifestObject\\s*='})\n"
        " $wire=@($ast.EndBlock.Statements|Where-Object {$_.Extent.Text -match '^if\\s*\\(\\$null -ne \\$manualActivation\\)'})\n"
        # Main code lives in a top-level try block, so FindAll is required.
        " if($assign.Count -eq 0){$assign=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$manifestObject'},$true))}\n"
        " if($wire.Count -eq 0){$wire=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text -match '^if\\s*\\(\\$null -ne \\$manualActivation\\)'},$true))}\n"
        " if($assign.Count -ne 1 -or $wire.Count -ne 1){throw 'MANIFEST_WIRING_MISSING'}\n"
        " . ([scriptblock]::Create($assign[0].Extent.Text));. ([scriptblock]::Create($wire[0].Extent.Text))\n"
        " $deployment=$manifestObject|ConvertTo-Json -Depth 10|ConvertFrom-Json\n"
        " $existing=$deployment|ConvertTo-Json -Depth 10|ConvertFrom-Json\n"
        " if($f.existing -ceq 'missing'){$existing.PSObject.Properties.Remove('manual_execution')}\n"
        " if($f.existing -ceq 'different'){$existing.manual_execution.approval_reference_sha256=('F'*64)}\n"
        " Assert-WdManualExecutionManifestBinding -Deployment $existing -ExpectedActivation $manualActivation -SourceCommit $head -DefinitionHash $definitionHash -Definition $f.definition -FleetAgentNames @($f.fleet)\n"
        " if($f.admit){\n"
        "  $argv=@('--wd-manual-execution','17','--expected-head',('a'*40),'--expected-base-sha',('b'*40),'--from-agent','codex-tools-1','--repo','Ahkeratmehilaiset/waggledance-swarm','--out-dir',(Join-Path $PSScriptRoot '.codex-audit/receipt'),'--consensus-proposal-id','synthetic','--bridge-task-id','synthetic/task','--json')+@($f.tail)\n"
        "  $null=Assert-WdBridgeManualExecution -Deployment $deployment -Definition $f.definition -DefinitionHash $definitionHash -ManifestHash $manifestHash -Tool 'tools/merge_with_bridge_receipt.py' -Arguments $argv -RuntimeRoot (Join-Path $PSScriptRoot 'runtime')\n"
        " }\n"
        " $out=@{accepted=$true;record=$manualActivation;has_activation=($null -ne $deployment.PSObject.Properties['manual_execution'])}\n"
        "}catch{$out=@{accepted=$false;reason=$_.Exception.Message}}\n"
        "$after=@(Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -File|Select-Object -ExpandProperty FullName)\n"
        "if(($before -join '|') -cne ($after -join '|')){throw 'UNEXPECTED_WRITE'}\n"
        "$out.source_sha256=(Get-FileHash -LiteralPath $source).Hash;$out.executor_ran=$false;$out.ps_version=$PSVersionTable.PSVersion.ToString()\n"
        "$out|ConvertTo-Json -Depth 10 -Compress\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(WD_REBOOT_EXPECTED_MANIFEST_HASH=anchor, AGENT_BRIDGE_AGENT=actor,
               AGENT_BRIDGE_RUNTIME_ROOT=str(runtime), AGENT_BRIDGE_ROOT=runtime_alias or str(runtime))
    run = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                          "-File", str(script)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stdout + run.stderr
    assert source.read_bytes() == before
    result = json.loads(run.stdout)
    assert result["source_sha256"] == hashlib.sha256(before).hexdigest().upper()
    assert result["executor_ran"] is False
    assert int(result["ps_version"].split(".")[0]) == (7 if Path(shell).stem.lower() == "pwsh" else 5)
    return result


def test_emitted_approval_reaches_unchanged_admission_without_executor(shell, tmp_path):
    result = _invoke(shell, tmp_path, admit=True)
    assert result["accepted"], result
    assert result["record"] == ACTIVATION and result["has_activation"]


def test_default_omission_keeps_manifest_shape_and_admission_refuses(shell, tmp_path):
    plain = _invoke(shell, tmp_path, omit=True)
    assert plain["accepted"] and not plain["has_activation"]
    denied = _invoke(shell, tmp_path, omit=True, admit=True)
    assert not denied["accepted"] and "not activated" in denied["reason"]


INVALID = ["wrong_source", "wrong_definition", "wrong_actor", "fleet_actor", "wrong_tool", "bad_reference",
           "extra", "missing", "duplicate", "case_alias", "boolean_alias", "string_alias", "non_array_tools", "wrong_schema"]


@pytest.mark.parametrize("case", INVALID)
def test_invalid_record_refuses_before_manifest_or_machine_write(shell, tmp_path, case):
    record = copy.deepcopy(ACTIVATION)
    fleet = None
    if case == "wrong_source": record["source_commit"] = "b" * 40
    elif case == "wrong_definition": record["definition_sha256"] = "A" * 64
    elif case == "wrong_actor": record["agent"] = "operator"; fleet = ["operator"]
    elif case == "fleet_actor": fleet = ["codex-lead-1"]
    elif case == "wrong_tool": record["tools"][0] = "tools/idle_consensus_auto_merge.py"
    elif case == "bad_reference": record["approval_reference_sha256"] = "not-an-approval"
    elif case == "extra": record["allow_anything"] = True
    elif case == "missing": del record["approval_reference_sha256"]
    elif case == "case_alias": record["Enabled"] = record.pop("enabled")
    elif case == "boolean_alias": record["enabled"] = "true"
    elif case == "string_alias": record["source_commit"] = 1
    elif case == "non_array_tools": record["tools"] = ",".join(TOOLS)
    elif case == "wrong_schema": record["schema"] = "wrong-schema"
    text = json.dumps(record)
    if case == "duplicate": text = text[:-1] + ',"enabled":true}'
    result = _invoke(shell, tmp_path, text=text, fleet=fleet)
    assert not result["accepted"], (case, result)
    assert "manual execution approval" in result["reason"]


@pytest.mark.parametrize("case", ["anchor", "actor", "runtime", "duplicate_option", "unknown_option", "flag_alias"])
def test_actual_emitted_record_cannot_weaken_admission(shell, tmp_path, case):
    kwargs = dict(admit=True)
    if case == "anchor": kwargs["anchor"] = "A" * 64
    elif case == "actor": kwargs["actor"] = "codex-lead-1"
    elif case == "runtime": kwargs["runtime_alias"] = str(tmp_path / "other-runtime")
    elif case == "duplicate_option": kwargs["tail"] = ["--expected-head=" + SOURCE]
    elif case == "unknown_option": kwargs["tail"] = ["--review-policy", "rule12"]
    else: kwargs["tail"] = ["--json=true"]
    result = _invoke(shell, tmp_path, **kwargs)
    assert not result["accepted"], (case, result)


@pytest.mark.parametrize("existing", ["missing", "different"])
def test_existing_manifest_requires_the_exact_requested_activation(shell, tmp_path, existing):
    result = _invoke(shell, tmp_path, existing=existing)
    assert not result["accepted"] and "existing manifest" in result["reason"]


def test_invalid_approval_is_validated_before_dependency_staging_and_machine_writes():
    source = Path(os.environ.get("WD_TEST_MANUAL_EMITTER_SOURCE", str(OPS / "Deploy-WdRebootBundle.ps1"))).read_text(encoding="utf-8")
    parse_call = source.index("$manualActivation = ConvertFrom-WdManualExecutionApprovalRecord")
    assert parse_call < source.index("$bridgePythonStage = Install-WdBridgePythonSite")
    assert parse_call < source.index("$machineMutationStarted = $true")
    assert ". (Join-Path $toolsBootstrapSource 'BridgeLogReader.ps1')" in source[:parse_call]
