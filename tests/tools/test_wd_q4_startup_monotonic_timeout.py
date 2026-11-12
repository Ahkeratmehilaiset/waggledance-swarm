"""Synthetic contract for the launchers' recall wait budget; no Mind or lane is launched."""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / "ops/windows/reboot/start-wd-agent.ps1",
           ROOT / "ops/windows/reboot/start-wd-tools-consumer.ps1"]
SHELLS = [pytest.param(shutil.which("powershell.exe"), id="powershell51"),
          pytest.param(shutil.which("pwsh.exe"), id="pwsh7")]


@pytest.mark.parametrize("source", SOURCES, ids=["agent", "tools"])
def test_one_monotonic_budget_controls_all_recall_waits(source):
    text = source.read_text(encoding="utf-8-sig")
    start = text.index("$query = [Text.Encoding]::ASCII.GetBytes")
    end = text.index("$stdoutText = [string]$stdoutTask.Result", start)
    block = text[start:end]
    assert "$waitClock = [Diagnostics.Stopwatch]::StartNew()" in block
    assert "$waitBudgetMilliseconds" in block
    assert block.count("Get-WdQ4RemainingWaitMilliseconds") >= 4
    assert "$deadline - [DateTime]::UtcNow" not in block
    assert "$deadline = [DateTime]::UtcNow" not in block
    assert re.search(r"-or\s+\(Get-WdQ4RemainingWaitMilliseconds", block)


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("source", SOURCES, ids=["agent", "tools"])
def test_fake_elapsed_clock_never_grants_more_time_when_wall_clock_moves(shell, source):
    if not shell:
        pytest.skip("PowerShell host unavailable")
    quoted = "'" + str(source).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quoted},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'source_parse' }}
$definitions=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdQ4RemainingWaitMilliseconds'}},$true))
if ($definitions.Count -ne 1) {{ throw 'missing_budget_function' }}
. ([scriptblock]::Create($definitions[0].Extent.Text))
$wall=@('2026-11-12T12:00:00Z','2026-11-12T11:51:00Z','2026-11-12T13:59:00Z','2026-11-12T11:00:00Z','2026-11-12T12:00:00Z')
$elapsed=@(0,60000,119999,120000,120001)
$values=@(for($i=0;$i -lt $elapsed.Count;$i++) {{
  [void][datetime]::Parse($wall[$i]); Get-WdQ4RemainingWaitMilliseconds -BudgetMilliseconds 120000 -ElapsedMilliseconds $elapsed[$i]
}})
$negative=Get-WdQ4RemainingWaitMilliseconds -BudgetMilliseconds 120000 -ElapsedMilliseconds -1
@{{values=$values;negative=$negative}} | ConvertTo-Json -Compress
"""
    proc = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result == {"values": [120000, 60000, 1, 0, 0], "negative": 0}


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("source", SOURCES, ids=["agent", "tools"])
def test_actual_wait_conditions_refuse_expired_and_late_completion(shell, source):
    """Evaluate the real branch predicates with fake elapsed time and an inert process."""
    if not shell:
        pytest.skip("PowerShell host unavailable")
    quoted = "'" + str(source).replace("'", "''") + "'"
    script = f"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quoted},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'source_parse' }}
$functions=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdQ4RemainingWaitMilliseconds'}},$true))
if ($functions.Count -ne 1) {{ throw 'missing_budget_function' }}
. ([scriptblock]::Create($functions[0].Extent.Text))
$branches=@($ast.FindAll({{param($n) $n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ((Get-WdQ4RemainingWaitMilliseconds') -and $n.Extent.Text.Contains('$process.WaitForExit(')}},$true) | Sort-Object {{ $_.Extent.Text.Length }})
if ($branches.Count -ne 1 -or $branches[0].Clauses.Count -ne 2) {{ throw 'wait_branch_scope' }}
$exitPredicate=[scriptblock]::Create($branches[0].Clauses[0].Item1.Extent.Text)
$ioPredicate=[scriptblock]::Create($branches[0].Clauses[1].Item1.Extent.Text)
$waitBudgetMilliseconds=120000
$waitClock=[pscustomobject]@{{ElapsedMilliseconds=120000}}
$process=[pscustomobject]@{{calls=0;clock=$waitClock}}
$process | Add-Member ScriptMethod WaitForExit {{param([int]$ms) $this.calls++; $this.clock.ElapsedMilliseconds=120000; return $true}}
$stdoutTask=[Threading.Tasks.Task]::FromResult('synthetic')
$stderrTask=[Threading.Tasks.Task]::FromResult('synthetic')
$expired=& $exitPredicate
$expiredCalls=$process.calls
$waitClock.ElapsedMilliseconds=60000
$finished=& $exitPredicate
$late=& $ioPredicate
$waitClock=[pscustomobject]@{{clockReads=0}}
$waitClock | Add-Member ScriptProperty ElapsedMilliseconds {{ $this.clockReads++; if ($this.clockReads -ge 3) {{ return 120000 }} else {{ return 60000 }} }}
$outputLate=& $ioPredicate
@{{expired=$expired;expiredCalls=$expiredCalls;finished=$finished;late=$late;calls=$process.calls;outputLate=$outputLate;outputClockReads=$waitClock.clockReads}} | ConvertTo-Json -Compress
"""
    proc = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "expired": True, "expiredCalls": 0, "finished": False, "late": True, "calls": 1,
        "outputLate": True, "outputClockReads": 3,
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell required")
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("source,lane", [(SOURCES[0], "codex-lead-1"), (SOURCES[1], "codex-tools-1")],
                         ids=["agent", "tools"])
@pytest.mark.parametrize("mode,expected", [
    ("answer", "unknown"), ("refuse", "refused"), ("hang", "unknown"),
])
def test_recall_with_inert_fixture_preserves_outcomes(tmp_path, shell, source, lane, mode, expected):
    """Run each real read function only against an entirely synthetic helper bundle."""
    if not shell:
        pytest.skip("PowerShell host unavailable")
    from test_wd_q4_shared_agent_startup import startup_read_fixture, startup_read_call, STARTUP_READ_FUNCTIONS

    quoted = "'" + str(source).replace("'", "''") + "'"
    fx = startup_read_fixture(tmp_path, lane, mode=mode)
    imports = []
    for name in STARTUP_READ_FUNCTIONS:
        imports.append(f"""
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quoted},[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'source_parse' }}
$fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq '{name}'}},$true)
if ($null -eq $fn) {{ throw 'missing_function' }}
. ([scriptblock]::Create($fn.Extent.Text))
""")
    script = ("$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n" + "\n".join(imports)
              + "\n" + startup_read_call(fx, lane, timeout=1 if mode == "hang" else 5))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    result = json.loads(proc.stdout.strip())
    assert result["outcome"] == expected
    if mode == "hang":
        assert result["reason"] == "timeout" and result["cleanup"] == "unknown"
    if mode == "refuse":
        assert result["reason"] == "cleanup_unknown"
