# SPDX-License-Identifier: BUSL-1.1
"""Disk-budget regressions: parse Deploy, execute ONLY its extracted pure helper.

No installer entrypoint, DriveInfo probe, archive, task or machine write is run.
The helper uses only its fake probe; pytest startup retains the real disk guard.
Cases name the mutant they kill. Strict CI binds each actual helper child.
"""

from __future__ import annotations

import base64
import json
import hashlib
import ntpath
import re
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
DEPLOY_SOURCE = DEPLOY.read_text(encoding="utf-8-sig")
FLOOR = 3 * 1024**3
MAX_LONG = 2**63 - 1


_PARITY_FUNCTIONS = (
    "test_budget_parameter_is_long_and_unknown_by_default",
    "test_three_checks_precede_each_write_boundary_including_dry_run",
    "test_temp_path_and_probe_require_nonredirected_fixed_storage",
    "test_extracted_helper_budget_matrix_and_distinct_volumes",
)


class _ParityHost(str):
    def __new__(cls, path, binding, raw, node, label):
        value = str.__new__(cls, path)
        value.binding, value.raw, value.node, value.label = binding, raw, node, label
        return value


def _case_directory(binding):
    token = binding["token"]
    expected = ROOT / ".codex-audit" / "windows-disk-parity" / token
    if ntpath.normcase(ntpath.normpath(binding["output"])) != ntpath.normcase(ntpath.normpath(str(expected))):
        raise ValueError("parity output root mismatch")
    folder = expected / "cases"
    cursor = folder
    while True:
        info = cursor.lstat()
        if cursor.is_symlink() or getattr(info, "st_file_attributes", 0) & 1024:
            raise ValueError("redirected parity output")
        if cursor == ROOT:
            break
        if cursor.parent == cursor:
            raise ValueError("parity output escape")
        cursor = cursor.parent
    return folder


def _binding(raw):
    if len(raw.encode("utf-8")) > 32768:
        raise ValueError("oversized parity binding")
    value = json.loads(raw)
    head, run, attempt = (os.environ.get(k, "") for k in
                          ("WD_PARITY_EXPECTED_HEAD", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT"))
    if (not re.fullmatch(r"[0-9a-f]{40}", head) or not re.fullmatch(r"[1-9][0-9]{0,19}", run)
            or not re.fullmatch(r"[1-9][0-9]{0,8}", attempt)
            or value.get("schema") != "wd.windows-disk-binding.v1"
            or value.get("head") != head or value.get("run_id") != run or value.get("run_attempt") != attempt
            or value.get("token") != f"wd-disk-{head}-{run}-{attempt}"
            or set(value.get("hosts", {})) != {"ps5", "ps7"}
            or set(value.get("inputs", {})) != {"tests/tools/test_wd_reboot_deploy_disk_floor.py",
                                               "ops/windows/reboot/Deploy-WdRebootBundle.ps1"}):
        raise ValueError("invalid parity context")
    for label, host in value["hosts"].items():
        path = host.get("path", "")
        if (not re.match(r"^[A-Za-z]:[\\/]", path) or not ntpath.isabs(path)
                or not re.fullmatch(r"[0-9a-f]{64}", host.get("sha256", ""))
                or type(host.get("major")) is not int or type(host.get("minor")) is not int
                or (label == "ps5" and (host["major"], host["minor"], host.get("edition")) != (5, 1, "Desktop"))
                or (label == "ps7" and (host["major"] != 7 or host.get("edition") != "Core"))):
            raise ValueError("invalid bound host")
    _case_directory(value)
    return value


@pytest.fixture(params=("powershell", "pwsh"), ids=("ps5", "ps7"))
def host(request: pytest.FixtureRequest) -> str:
    strict = os.environ.get("WD_PARITY_STRICT")
    raw = os.environ.get("WD_PARITY_BINDING_JSON")
    if strict is not None or raw is not None:
        if strict != "1" or not raw:
            raise ValueError("incomplete strict parity binding")
        binding = _binding(raw)
        label = {"powershell": "ps5", "pwsh": "ps7"}[request.param]
        node = request.node.name
        if node not in {f"{name}[{label}]" for name in _PARITY_FUNCTIONS}:
            raise ValueError("unexpected strict parity node")
        return _ParityHost(binding["hosts"][label]["path"], binding, raw, node, label)
    executable = shutil.which(request.param)
    if executable is None:
        pytest.skip(f"{request.param} is not available; parity NOT_RUN")
    return executable


def _ps(host: str, body: str, cases: list[dict] | None = None) -> dict:
    # Source is DATA on stdin, never -File/dot-sourced as an installer.
    prefix = r"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$packet = [Console]::In.ReadToEnd() | ConvertFrom-Json
$source = [string]$packet.source
$tokens = $null; $parseErrors = $null
$ast = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if (@($parseErrors).Count) { throw 'Deploy parse errors' }
$functions = @($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}, $true))
"""
    packet = json.dumps({"source": DEPLOY_SOURCE, "cases": cases or []})
    if not isinstance(host, _ParityHost):
        encoded = base64.b64encode((prefix + body).encode("utf-16-le")).decode("ascii")
        result = subprocess.run(
            [host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            input=packet, capture_output=True, text=True, encoding="utf-8",
            cwd=ROOT, timeout=30, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout.strip().lstrip("\ufeff"))
    expected = host.binding["hosts"][host.label]
    before = hashlib.sha256(Path(str(host)).read_bytes()).hexdigest()
    if before != expected["sha256"]:
        raise ValueError("fixture host bytes differ from parent probe")
    text_sha = hashlib.sha256(DEPLOY_SOURCE.encode("utf-8")).hexdigest()
    if text_sha != host.binding["inputs"]["ops/windows/reboot/Deploy-WdRebootBundle.ps1"]["text_sha256"]:
        raise ValueError("loaded Deploy Source differs from binding")
    envelope = r"""
# strict-parity-envelope
$values = @(& {
""" + body + r"""
})
if ($values.Count -ne 1) { throw 'one helper JSON result required' }
$data = [string]$values[0] | ConvertFrom-Json
$p = [Diagnostics.Process]::GetCurrentProcess()
$sha = [Security.Cryptography.SHA256]::Create()
try { $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($source))).Replace('-','').ToLowerInvariant() }
finally { $sha.Dispose() }
@{identity=@{platform=[Environment]::OSVersion.Platform.ToString(); executable=$p.MainModule.FileName;
 major=$PSVersionTable.PSVersion.Major; minor=$PSVersionTable.PSVersion.Minor;
 edition=$PSVersionTable.PSEdition; pid=$PID; source_text_sha256=$digest}; data=$data} | ConvertTo-Json -Depth 16 -Compress
"""
    encoded = base64.b64encode((prefix + envelope).encode("utf-16-le")).decode("ascii")
    child = subprocess.Popen(
        [str(host), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", cwd=ROOT,
    )
    try:
        stdout, stderr = child.communicate(packet, timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise
    assert child.returncode == 0, stdout + stderr
    result = json.loads(stdout.strip().lstrip("\ufeff"))
    identity = result["identity"]
    if (type(identity.get("pid")) is not int or identity["pid"] != child.pid
            or identity.get("platform") != "Win32NT"
            or ntpath.normcase(ntpath.normpath(identity.get("executable", ""))) != ntpath.normcase(ntpath.normpath(str(host)))
            or any(type(identity.get(k)) is not int or identity[k] != expected[k] for k in ("major", "minor"))
            or identity.get("edition") != expected["edition"]
            or identity.get("source_text_sha256") != text_sha):
        raise ValueError("fixture child identity mismatch")
    after = hashlib.sha256(Path(str(host)).read_bytes()).hexdigest()
    if after != before:
        raise ValueError("fixture host changed")
    record = dict(schema="wd.windows-disk-case.v1", token=host.binding["token"],
                  binding_sha256=hashlib.sha256(host.raw.encode("utf-8")).hexdigest(),
                  node=host.node, label=host.label, path=expected["path"], before_sha256=before, after_sha256=after,
                  launched_pid=child.pid, observed_pid=identity["pid"], platform=identity["platform"],
                  major=identity["major"], minor=identity["minor"], edition=identity["edition"],
                  source_text_sha256=text_sha, exit=child.returncode)
    data = json.dumps(record, sort_keys=True)
    if len(data.encode("utf-8")) > 65536:
        raise ValueError("oversized case receipt")
    filename = host.node.removesuffix(f"[{host.label}]") + f"__{host.label}.json"
    destination = _case_directory(host.binding) / filename
    with destination.open("x", encoding="utf-8") as stream:
        stream.write(data)
    return result["data"]


def test_budget_parameter_is_long_and_unknown_by_default(host: str) -> None:
    """MUTANT_UNKNOWN_PEAK_ALLOWED / MUTANT_BUDGET_INT32."""
    result = _ps(host, r"""
$p = @($ast.ParamBlock.Parameters | Where-Object { $_.Name.VariablePath.UserPath -ceq 'ExpectedPeakAdditionalBytes' })
if ($p.Count -ne 1) { throw 'ExpectedPeakAdditionalBytes parameter missing' }
@{type=$p[0].StaticType.FullName; default=$p[0].DefaultValue.Extent.Text} | ConvertTo-Json -Compress
""")
    assert result == {"type": "System.Int64", "default": "0"}


def test_three_checks_precede_each_write_boundary_including_dry_run(host: str) -> None:
    """MUTANT_LATE_MATERIALIZATION / MUTANT_NO_STAGE / MUTANT_NO_ACTIVATION.

    AST commands exclude comments, strings and function definitions; a printed
    promise or call inside an uncalled function cannot satisfy these checks.
    """
    result = _ps(host, r"""
$commands = @($ast.FindAll({param($n)
    if ($n -isnot [Management.Automation.Language.CommandAst]) { return $false }
    $parent = $n.Parent
    while ($null -ne $parent) {
        if ($parent -is [Management.Automation.Language.FunctionDefinitionAst]) { return $false }
        $parent = $parent.Parent
    }
    return $true
}, $true))
$rows = @($commands | ForEach-Object {
    $conditional = $false; $parent = $_.Parent
    while ($null -ne $parent) {
        if ($parent -is [Management.Automation.Language.IfStatementAst]) { $conditional = $true }
        $parent = $parent.Parent
    }
    @{name=$_.GetCommandName(); text=$_.Extent.Text; offset=$_.Extent.StartOffset; conditional=$conditional}
})
$dryRun = @($ast.FindAll({param($n)
    if ($n -isnot [Management.Automation.Language.IfStatementAst] -or
        $n.Clauses.Count -ne 1 -or $n.Clauses[0].Item1.Extent.Text -cne '$DryRun') { return $false }
    $parent = $n.Parent
    while ($null -ne $parent) {
        if ($parent -is [Management.Automation.Language.FunctionDefinitionAst]) { return $false }
        $parent = $parent.Parent
    }
    return $true
}, $true))
if ($dryRun.Count -ne 1) { throw 'MUTANT_MISSING_OR_EXTRA_REAL_DRY_RUN_BLOCK' }
@{commands=$rows; dry_run_offset=$dryRun[0].Extent.StartOffset} | ConvertTo-Json -Depth 10 -Compress
""")
    commands = result["commands"]
    checks = [c for c in commands if c["name"] == "Assert-WdDeploymentDiskBudget"]
    phases = ("before_materialization", "before_stage", "before_activation")
    assert len(checks) == 3, "MUTANT_MISSING_OR_EXTRA_PHASE_CHECK"
    by_phase = {}
    for phase in phases:
        matched = [c for c in checks if phase in c["text"]]
        assert len(matched) == 1, phase
        by_phase[phase] = matched[0]
        assert "-ExpectedPeakAdditionalBytes $ExpectedPeakAdditionalBytes" in matched[0]["text"]
        assert "-Paths" in matched[0]["text"]
        assert not matched[0]["conditional"], "MUTANT_PHASE_GUARD_CONDITIONAL_BYPASS"
        for required_path in ("$temporaryParent", "$storeFull", "$machineFull"):
            assert required_path in matched[0]["text"], "MUTANT_BOUNDARY_PATH_OMITTED"
    materialize = by_phase[phases[0]]["offset"]
    stage = by_phase[phases[1]]["offset"]
    activation = by_phase[phases[2]]["offset"]
    manual = [c for c in commands if c["name"] == "Read-WdManualExecutionApprovalText"]
    assert len(manual) == 1
    assert materialize < manual[0]["offset"], "MUTANT_PS5_ADD_TYPE_BEFORE_FLOOR"
    git_probes = [c for c in commands if c["name"] == "Invoke-GitCapture"]
    assert git_probes and materialize < min(c["offset"] for c in git_probes), "MUTANT_GIT_WRITE_BEFORE_FLOOR"
    writes = [c for c in commands if c["name"] == "New-Item"]
    assert writes and materialize < min(c["offset"] for c in writes)
    store_writes = [c for c in writes if "-Path $storeFull" in c["text"] or "-Path $stage" in c["text"]]
    assert store_writes and stage < min(c["offset"] for c in store_writes)
    # Select the actual invocation, not its nested Join-Path expression as a second call.
    migration = [c for c in commands if c["name"] is None and c["text"].startswith("& ")
                 and "$targetRoot" in c["text"] and "Initialize-WdGrokRecovery.ps1" in c["text"]]
    assert len(migration) == 1 and activation < migration[0]["offset"]
    pair_refusal = DEPLOY_SOURCE.index("refusing to activate: the operator-pinned final pair")
    assert pair_refusal < activation, "MUTANT_DISK_CHECK_BEFORE_PAIR_BINDING"
    # Generated wrapper text also contains "if ($DryRun) {"; only the real AST block counts.
    dry_run_return = result["dry_run_offset"]
    assert materialize < dry_run_return < stage < activation


def test_temp_path_and_probe_require_nonredirected_fixed_storage(host: str) -> None:
    """MUTANT_TEMP_REPARSE_UNCHECKED / MUTANT_NETWORK_DRIVE_ACCEPTED: Source only."""
    result = _ps(host, r"""
$probe = @($functions | Where-Object { $_.Name -ceq 'Get-WdDeploymentFreeBytes' })
if ($probe.Count -ne 1) { throw 'missing read-only disk probe' }
$commands = @($ast.FindAll({param($n)
    if ($n -isnot [Management.Automation.Language.CommandAst]) { return $false }
    $parent = $n.Parent
    while ($null -ne $parent) {
        if ($parent -is [Management.Automation.Language.FunctionDefinitionAst]) { return $false }
        $parent = $parent.Parent
    }
    return $true
}, $true))
$temp = @($commands | Where-Object {
    $_.GetCommandName() -ceq 'Assert-WdPathWithoutReparse' -and $_.Extent.Text -match '-Path \$temporaryParent\s'
})
$first = @($commands | Where-Object {
    $_.GetCommandName() -ceq 'Assert-WdDeploymentDiskBudget' -and $_.Extent.Text -match 'before_materialization'
})
if ($temp.Count -ne 1 -or $first.Count -ne 1) { throw 'missing precise temp guard/budget boundary' }
@{probe=$probe[0].Extent.Text; temp=$temp[0].Extent.Text; temp_offset=$temp[0].Extent.StartOffset;
  budget_offset=$first[0].Extent.StartOffset} | ConvertTo-Json -Depth 5 -Compress
""")
    probe = result["probe"]
    assert "$drive.DriveType -ne [IO.DriveType]::Fixed" in probe
    assert probe.index("$drive.IsReady") < probe.index("$drive.DriveType") < probe.index("$drive.AvailableFreeSpace")
    assert result["temp_offset"] < result["budget_offset"]
    assert "-TrustedRoot $temporaryDriveRoot" in result["temp"]
    assert "-ExpectedType Directory" in result["temp"] and "-AllowMissing" not in result["temp"]


@pytest.mark.skipif(os.name != "nt", reason="Windows local-volume semantics")
def test_extracted_helper_budget_matrix_and_distinct_volumes(host: str) -> None:
    """Synthetic helper only; every row names the boundary mutation it rejects."""
    # Expressions are fixed test literals, never caller input or live disk data.
    cases = [
        ("MUTANT_EQUALITY_REJECTED", FLOOR + 17, "[long]17", True),
        ("MUTANT_ONE_BYTE_SHORT_ACCEPTED", FLOOR + 16, "[long]17", False),
        ("MUTANT_FLOOR_OMITTED", FLOOR - 1, "[long]1", False),
        ("MUTANT_UNKNOWN_PEAK_ALLOWED", MAX_LONG, "$null", False),
        ("MUTANT_ZERO_PEAK_ALLOWED", MAX_LONG, "[long]0", False),
        ("MUTANT_NEGATIVE_PEAK_ALLOWED", MAX_LONG, "[long]-1", False),
        ("MUTANT_MIN_LONG_ALLOWED", MAX_LONG, "[long]::MinValue", False),
        ("MUTANT_ADDITION_OVERFLOW", MAX_LONG, "[long]::MaxValue", False),
        ("MUTANT_MAX_SAFE_BOUND_REJECTED", MAX_LONG, f"[long]{MAX_LONG - FLOOR}", True),
        ("MUTANT_INT64_BINDING_OVERFLOW", MAX_LONG, "[decimal]'9223372036854775808'", False),
    ]
    setup = r"""
$budget = @($functions | Where-Object { $_.Name -ceq 'Assert-WdDeploymentDiskBudget' })
if ($budget.Count -ne 1) { throw 'Assert-WdDeploymentDiskBudget missing' }
# Evaluate ONLY this function definition; the real disk probe is never defined.
. ([scriptblock]::Create($budget[0].Extent.Text))
$script:seen = [Collections.Generic.List[string]]::new()
function Get-WdDeploymentFreeBytes {
    param([string]$Root)
    $script:seen.Add($Root)
    if ($script:probeMode -ceq 'throw') { throw 'synthetic unreadable/not-ready volume' }
    if ($script:probeMode -ceq 'different' -and $Root -ieq 'E:\') { return [long]3221225488 }
    return $script:measurement
}
$rows = [Collections.Generic.List[object]]::new()
"""
    rows = []
    for name, free, peak, accepted in cases:
        for phase in ("before_materialization", "before_stage", "before_activation"):
            rows.append(dict(name=f"{name}/{phase}", free=f"[long]{free}", peak=peak,
                             phase=phase, mode="normal", expected=accepted))
    for name, expression in (
        ("MUTANT_NULL_MEASUREMENT", "$null"),
        ("MUTANT_NEGATIVE_MEASUREMENT", "[long]-1"),
        ("MUTANT_STRING_MEASUREMENT", "'9223372036854775807'"),
        ("MUTANT_FRACTIONAL_MEASUREMENT", "[double]9223372036854775807"),
        ("MUTANT_BOOL_MEASUREMENT", "$true"),
        ("MUTANT_MULTIPLE_MEASUREMENTS", "@([long]9223372036854775807,[long]9223372036854775807)"),
    ):
        rows.append(dict(name=name, free=expression, peak="[long]17",
                         phase="before_stage", mode="normal", expected=False))
    for mode in ("throw", "different"):
        rows.append(dict(name=f"MUTANT_VOLUME_{mode.upper()}_IGNORED",
                         free=f"[long]{MAX_LONG}", peak="[long]17",
                         phase="before_stage", mode=mode, expected=False))
    # Fixed literal expressions travel as data, keeping -EncodedCommand bounded.
    result = _ps(host, setup + r"""
foreach ($case in $packet.cases) {
    $script:measurement = & ([scriptblock]::Create([string]$case.free))
    $script:probeMode = [string]$case.mode
    $script:seen.Clear()
    $peak = & ([scriptblock]::Create([string]$case.peak))
    $ok = $true
    try {
        Assert-WdDeploymentDiskBudget -Paths @('D:\store','E:\temp','d:\other') -ExpectedPeakAdditionalBytes $peak -Phase $case.phase
    } catch { $ok = $false }
    $rows.Add(@{name=$case.name; accepted=$ok; expected=$case.expected; roots=$script:seen.ToArray()})
}
@{rows=$rows.ToArray()} | ConvertTo-Json -Depth 8 -Compress
""", rows)
    assert len(result["rows"]) == len(rows), "MUTANT_SILENT_EMPTY_MATRIX"
    for row in result["rows"]:
        assert row["accepted"] is row["expected"], row
        if row["expected"]:
            # Mandatory C:, both other volumes, and case-insensitive deduplication.
            assert sorted(r.upper() for r in row["roots"]) == ["C:\\", "D:\\", "E:\\"], row
