"""AST-extracted Source functions on toy records; NEVER run the installer."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_bridge_rco_enrollment import GENERATION, record

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"


@pytest.fixture(params=["powershell", "pwsh"])
def shell(request):
    if os.name != "nt":
        pytest.skip("PowerShell fixture requires Windows")
    command = shutil.which(request.param)
    if not command:
        pytest.skip(f"{request.param} unavailable; NOT parity evidence")
    return command


def invoke(shell, tmp_path, records, *, existing="same", omit=False):
    fixture = tmp_path / "records.json"
    fixture.write_text(json.dumps(dict(records=records, existing=existing, omit=omit)), encoding="utf-8")
    q = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
        f". {q(ROOT / '.agent-bridge/bin/BridgeLogReader.ps1')}\n"
        f"$f=Get-Content -Raw -LiteralPath {q(fixture)} | ConvertFrom-Json\n"
        "$t=$null;$e=$null\n"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({q(SOURCE)},[ref]$t,[ref]$e)\n"
        "if($e.Count){throw 'SOURCE_PARSE_ERROR'}\n"
        "foreach($name in @('ConvertFrom-WdRcoEnrollmentRecord','Add-WdRcoEnrollmentToManifest','Assert-WdRcoEnrollmentManifestBinding')){\n"
        " $defs=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true)|Where-Object Name -CEQ $name)\n"
        " if($defs.Count -ne 1){throw ('MISSING_SOURCE_HELPER:'+ $name)}\n"
        " . ([scriptblock]::Create($defs[0].Extent.Text))\n}\n"
        "$rows=@();foreach($text in $f.records){\n"
        " try {\n"
        f"  $r=ConvertFrom-WdRcoEnrollmentRecord -Text $text -SourceCommit '{GENERATION}'\n"
        "  $m=[ordered]@{source_commit=('a'*40)};$before=$m|ConvertTo-Json -Compress\n"
        "  if($f.omit){$r=$null}\n"
        "  Add-WdRcoEnrollmentToManifest -Manifest $m -Enrollment $r\n"
        "  $after=$m|ConvertTo-Json -Depth 12 -Compress\n"
        "  $argsJson=@{InputObject=$after};if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$argsJson['DateKind']='String'}\n"
        "  $d=ConvertFrom-Json @argsJson\n"
        "  if($f.existing -ceq 'absent'){$d=[pscustomobject]@{source_commit=('a'*40)}}\n"
        "  if($f.existing -ceq 'different'){$d.rco_enrollments.'claude-rco-3'.session_id='changed'}\n"
        f"  Assert-WdRcoEnrollmentManifestBinding -Deployment $d -ExpectedEnrollment $r -SourceCommit '{GENERATION}'\n"
        "  $rows+=@{ok=$true;record=$r;manifest=$m;omitted_unchanged=($before -ceq $after)}\n"
        " }catch{$rows+=@{ok=$false;error=$_.Exception.Message}}\n}\n"
        "ConvertTo-Json -InputObject @($rows) -Depth 16 -Compress\n", encoding="utf-8")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
                            capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_record_contract_roundtrip(shell, tmp_path):
    result = invoke(shell, tmp_path, [json.dumps(record())])[0]
    assert result["ok"], result
    assert result["record"] == record()
    assert result["manifest"]["rco_enrollments"] == {"claude-rco-3": record()}


def test_omission_preserves_original_manifest_bytes(shell, tmp_path):
    result = invoke(shell, tmp_path, [json.dumps(record())], omit=True)[0]
    assert result["ok"], result
    assert result["omitted_unchanged"] is True
    assert "rco_enrollments" not in result["manifest"]


def test_closed_typed_attestation_contract_rejects_all_bad_records(shell, tmp_path):
    variants = []
    for key, value in [
        ("generation", "b" * 40), ("agent", "claude-rco-1"), ("agent_uuid", "x"),
        ("native_conversation_id", record()["agent_uuid"]), ("native_conversation_id", "x"),
        ("session_id", " "), ("run_id", None), ("native_pid", True), ("native_pid", "4242"),
        ("native_pid", 1.5), ("launcher_pid", 0), ("token_integrity", "High"),
        ("elevated", 0), ("elevated", True), ("fresh_history_free", False),
        ("no_mind_authorship", 1), ("no_measurement_case_access", None),
        ("cli_kind", "claude.cmd"), ("native_process_start_utc", "2026-02-30T01:00:00Z"),
        ("native_process_start_utc", "2026-10-21T01:00:00.1234568Z"),
    ]:
        bad = copy.deepcopy(record())
        bad[key] = value
        if key == "native_process_start_utc" and "1234568" in value:
            bad["observed_at_utc"] = "2026-10-21T01:00:00.1234567Z"
        variants.append(json.dumps(bad))
    missing = record()
    missing.pop("observed_at_utc")
    variants += [json.dumps(missing), json.dumps({**record(), "extra": True}),
                 json.dumps(record())[:-1] + ',"agent":"claude-rco-3"}', "[]", "null"]
    results = invoke(shell, tmp_path, variants)
    assert len(results) == len(variants)
    assert all(item["ok"] is False for item in results), results


@pytest.mark.parametrize("existing", ["absent", "different"])
def test_existing_manifest_cannot_drop_or_change_explicit_enrollment(shell, tmp_path, existing):
    result = invoke(shell, tmp_path, [json.dumps(record())], existing=existing)[0]
    assert result["ok"] is False


def test_explicit_emission_is_wired_before_dependency_writes():
    text = SOURCE.read_text(encoding="utf-8-sig")
    assert "[string] $RcoEnrollmentRecordPath = ''" in text
    assert text.index("$rcoEnrollmentText = Read-WdManualExecutionApprovalText") < text.index("$bridgePythonStage =")
    assert text.index("$rcoEnrollment = ConvertFrom-WdRcoEnrollmentRecord") < text.index("$bridgePythonStage =")
    assert "Add-WdRcoEnrollmentToManifest -Manifest $manifestObject -Enrollment $rcoEnrollment" in text
    assert "Assert-WdRcoEnrollmentManifestBinding -Deployment $installedManifest" in text
