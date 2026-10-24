"""Synthetic contract of the fixed-member launch plan builder and the owned output observer.

tests/windows/wd_mind_shadow_owned_member_launch.ps1 only defines functions. A test host dot-sources it and calls the
PURE plan builder (it never starts a process or runs the member) and the observer on a strict fake managed object
whose Stop only counts, over in-process synthetic log files in pytest temp .codex-audit folders. Nothing here starts
a process from the helper, runs the member or touches a job or native handle; a within-budget result is observed
bytes, never native PASS. Lead's v10 GO (2026-10-22T21:22:07Z) set the API; fable-5 wrote the helper and this test.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tests" / "windows" / "wd_mind_shadow_owned_member_launch.ps1"
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="the helper admits only local C: paths: non-Windows is "
                                  "untested, not evidence")
LAUNCH = "1a1a1a1a-0000-4000-8000-000000000001"
PIN = "AB" * 32
BANNED = ("Add-Type", "Start-Process", "Process]::Start", "Invoke-Expression", "Stop-Process", "Get-Process",
          ".Kill(", "Get-Cim", "Get-WmiObject", "New-Item", "Remove-Item", "Set-Content", "Clear-Content")


def _hosts():
    hosts = []
    pwsh = shutil.which("pwsh")
    if pwsh:
        hosts.append(("ps7", pwsh))
    if os.name == "nt":
        system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        ps5 = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if ps5.is_file():
            hosts.append(("ps5", str(ps5)))
    return hosts


HOST = r"""
param([string] $Helper, [string] $FixturePath)
$ErrorActionPreference = 'Stop'
. $Helper
$items = [IO.File]::ReadAllText($FixturePath) | ConvertFrom-Json
$rows = @()
foreach ($item in @($items)) {
    $row = [ordered]@{ name = [string]$item.name }
    try {
        if ($item.kind -ceq 'plan') {
            $a = $item.args
            $life = $a.LifetimeSeconds
            if ($a.life_text) { $life = [string]$a.life_text }
            $plan = Get-WdShadowFixedMemberLaunchPlan -HostPath $a.HostPath -MemberPath $a.MemberPath `
                -WitnessDirectory $a.WitnessDirectory -LaunchId $a.LaunchId -Subcase $a.Subcase -LifetimeSeconds $life `
                -ExpectedMemberSha256 $a.ExpectedMemberSha256
            $row.plan = $plan
            $tokens = $null; $errors = $null
            $ast = [Management.Automation.Language.Parser]::ParseInput([string]$plan['argv'][3], [ref]$tokens,
                [ref]$errors)
            $row.parse_errors = @($errors).Count
            $assign = @($ast.FindAll({ param($x)
                    $x -is [Management.Automation.Language.AssignmentStatementAst] }, $true) |
                ForEach-Object { $_.Left.Extent.Text + '=' + $_.Right.Extent.Text })
            $row.assignments = $assign
            $row.invocations = @($ast.FindAll({ param($x) $x -is [Management.Automation.Language.CommandAst] -and
                    $x.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Ampersand }, $true) |
                ForEach-Object { [string]$_.CommandElements[0].SafeGetValue() })
            $row.commands = @($ast.FindAll({ param($x) $x -is [Management.Automation.Language.CommandAst] }, $true) |
                ForEach-Object { [string]$_.GetCommandName() })
            $row.member_args = @($ast.FindAll({ param($x) $x -is [Management.Automation.Language.CommandAst] -and
                    $x.InvocationOperator -eq [Management.Automation.Language.TokenKind]::Ampersand }, $true) |
                ForEach-Object { , @($_.CommandElements | Select-Object -Skip 1 | ForEach-Object {
                    if ($_ -is [Management.Automation.Language.CommandParameterAst]) { '-' + $_.ParameterName }
                    else { [string]$_.SafeGetValue() } }) })
            $row.env_after = [string][Environment]::GetEnvironmentVariable('WD_MIND_SHADOW_SYNTHETIC_MEMBER')
            $row.result = 'PLAN'
        } else {
            $global:WdStopCalls = 0
            $managed = [pscustomobject]@{ Pid = 4242 }
            $mode = [string]$item.stop_mode
            $managed | Add-Member -MemberType ScriptMethod -Name Stop -Value {
                $global:WdStopCalls++
                if ($global:WdStopMode -ceq 'throw') { throw 'synthetic stop failure' }
            }.GetNewClosure()
            $global:WdStopMode = $mode
            $target = $managed
            if ($item.managed -ceq 'none') { $target = $null }
            if ($item.managed -ceq 'two') { $target = @($managed, $managed) }
            $floor = [long]3221225472
            if ($item.floor) { $floor = [long]$item.floor }
            $observe = New-WdShadowOwnedOutputObserver -ApprovedObservation:([bool]$item.approved) -Managed $target `
                -EvidenceDirectory ([string]$item.evidence) -MaxOutputBytes ([long]$item.max) -FloorBytes $floor
            $results = @()
            foreach ($step in @($item.steps)) {
                if ($step.stdout -ne $null) {
                    [IO.File]::WriteAllBytes([string]$item.evidence + '\root.stdout.log',
                        [byte[]]::new([int]$step.stdout))
                }
                if ($step.stderr -ne $null) {
                    [IO.File]::WriteAllBytes([string]$item.evidence + '\root.stderr.log',
                        [byte[]]::new([int]$step.stderr))
                }
                $results += , (& $observe)
            }
            $row.results = $results
            $row.stop_calls = $global:WdStopCalls
            $row.result = 'OBSERVED'
        }
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message; $row.stop_calls = $global:WdStopCalls }
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 10
"""


def _good(folder):
    witness = folder / ".codex-audit" / "run" / LAUNCH
    return {"HostPath": "C:\\SYNTHETIC\\pwsh.exe", "MemberPath": "C:\\SYNTHETIC\\member.ps1",
            "WitnessDirectory": str(witness), "LaunchId": LAUNCH, "Subcase": "A1/deadline", "LifetimeSeconds": 120,
            "ExpectedMemberSha256": PIN}


PLAN_ROWS = [
    ("lp_good", {}, "PLAN"),
    ("lp_apostrophe_member", {"MemberPath": "C:\\SYN'THETIC\\member.ps1"}, "PLAN"),
    ("lp_lifetime_cap", {"LifetimeSeconds": 86400}, "PLAN"),
    ("lp_relative_member", {"MemberPath": "SYNTHETIC\\member.ps1"}, "ERROR"),
    ("lp_other_drive_host", {"HostPath": "D:\\SYNTHETIC\\pwsh.exe"}, "ERROR"),
    ("lp_quote_in_member", {"MemberPath": "C:\\SYN\"THETIC\\member.ps1"}, "ERROR"),
    ("lp_newline_in_member", {"MemberPath": "C:\\SYNTHETIC\\member.ps1\nWrite-Host"}, "ERROR"),
    ("lp_not_normalized", {"MemberPath": "C:\\SYNTHETIC\\x\\..\\member.ps1"}, "ERROR"),
    ("lp_unknown_subcase", {"Subcase": "A9/anything"}, "ERROR"),
    ("lp_uppercase_launch", {"LaunchId": LAUNCH.upper()}, "ERROR"),
    ("lp_lifetime_zero", {"LifetimeSeconds": 0}, "ERROR"),
    ("lp_lifetime_over", {"LifetimeSeconds": 86401}, "ERROR"),
    ("lp_lifetime_text", {"life_text": "120"}, "ERROR"),
    ("lp_lowercase_pin", {"ExpectedMemberSha256": PIN.lower()}, "ERROR"),
    ("lp_short_pin", {"ExpectedMemberSha256": "AB" * 31}, "ERROR"),
    ("lp_foreign_witness_leaf", {"witness_leaf": "other"}, "ERROR"),
    ("lp_witness_outside_audit", {"witness_audit": False}, "ERROR"),
]

OBSERVER_ROWS = [
    ("ob_below", {"max": 100, "steps": [{"stdout": 10, "stderr": 5}]}, "OBSERVED"),
    ("ob_at_bound", {"max": 100, "steps": [{"stdout": 60, "stderr": 40}]}, "OBSERVED"),
    ("ob_above_then_again", {"max": 100, "steps": [{"stdout": 60, "stderr": 41}, {"stdout": 1, "stderr": 1}]},
     "OBSERVED"),
    ("ob_missing_stderr", {"max": 100, "steps": [{"stdout": 10}]}, "OBSERVED"),
    ("ob_directory_as_log", {"max": 100, "dir_log": True, "steps": [{"stdout": 10}]}, "OBSERVED"),
    ("ob_stop_failure_no_retry", {"max": 100, "stop_mode": "throw",
                                  "steps": [{"stdout": 200, "stderr": 0}, {"stdout": 300, "stderr": 0}]}, "OBSERVED"),
    ("ob_disk_floor", {"max": 100, "floor": 9 * 10 ** 18, "steps": [{"stdout": 1, "stderr": 1}]}, "OBSERVED"),
    ("ob_cap_accepted", {"max": 8388608, "steps": [{"stdout": 1, "stderr": 1}]}, "OBSERVED"),
    ("ob_not_approved", {"approved": False, "max": 100, "steps": []}, "ERROR"),
    ("ob_zero_bound", {"max": 0, "steps": []}, "ERROR"),
    ("ob_over_cap", {"max": 8388609, "steps": []}, "ERROR"),
    ("ob_no_managed", {"managed": "none", "max": 100, "steps": []}, "ERROR"),
    ("ob_two_managed", {"managed": "two", "max": 100, "steps": []}, "ERROR"),
    ("ob_low_floor", {"floor": 1024, "max": 100, "steps": []}, "ERROR"),
    ("ob_evidence_outside_audit", {"outside": True, "max": 100, "steps": []}, "ERROR"),
]


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner"))], ids=lambda host: host[0] if host else "no-host")
def rows(request, tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the helper admits only local C: paths: non-Windows is untested, not evidence")
    _, executable = request.param
    folder = tmp_path_factory.mktemp("launch")
    items = []
    for name, change, _ in PLAN_ROWS:
        args = _good(folder)
        args.update({key: value for key, value in change.items() if key in args or key == "life_text"})
        if "witness_leaf" in change:
            args["WitnessDirectory"] = str(folder / ".codex-audit" / "run" / change["witness_leaf"])
        if change.get("witness_audit") is False:
            args["WitnessDirectory"] = str(folder / "elsewhere" / "run" / LAUNCH)
        items.append({"name": name, "kind": "plan", "args": args})
    for name, change, _ in OBSERVER_ROWS:
        evidence = (folder / ("elsewhere" if change.get("outside") else ".codex-audit") / name / LAUNCH)
        evidence.mkdir(parents=True)
        if change.get("dir_log"):
            (evidence / "root.stderr.log").mkdir()
        items.append({"name": name, "kind": "observer", "approved": change.get("approved", True),
                      "managed": change.get("managed", "one"), "evidence": str(evidence), "max": change["max"],
                      "floor": change.get("floor"), "stop_mode": change.get("stop_mode", ""),
                      "steps": change["steps"]})
    host = folder / "launch_host.ps1"
    host.write_text(HOST, encoding="utf-8")
    fixture = folder / "launch_fixture.json"
    fixture.write_text(json.dumps(items), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(HELPER),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    out = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row for row in out}, {item["name"]: item for item in items}


def test_helper_text_is_ascii_inert_and_has_no_process_or_mutation_surface():
    text = HELPER.read_text(encoding="ascii")
    for word in BANNED:
        assert word not in text, word
    assert text.count("$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER") == 1  # only inside the child command literal
    assert "function Get-WdShadowFixedMemberLaunchPlan" in text and "function New-WdShadowOwnedOutputObserver" in text


@WINDOWS_ONLY
@pytest.mark.parametrize("name", [host[0] for host in _hosts()] or ["none"])
def test_running_the_helper_directly_refuses(name):
    hosts = dict(_hosts())
    if name not in hosts:
        pytest.skip("no PowerShell host")
    done = subprocess.run([hosts[name], "-NoProfile", "-NonInteractive", "-File", str(HELPER)], capture_output=True,
                          text=True, timeout=60)
    assert done.returncode == 3 and "refused: " in done.stderr, (done.returncode, done.stderr)


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change,expected", PLAN_ROWS, ids=[row[0] for row in PLAN_ROWS])
def test_fixed_member_launch_plan_is_one_literal_child_command(rows, name, change, expected):
    row, item = rows[0][name], rows[1][name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: "), row
        return
    plan, args = row["plan"], item["args"]
    assert plan["schema"] == "wd.mind-shadow-fixed-member-launch-plan.v1"
    assert plan["host_path"] == args["HostPath"] and plan["member_path"] == args["MemberPath"]
    assert plan["witness_directory"] == args["WitnessDirectory"] and plan["launch_id"] == LAUNCH
    assert plan["subcase"] == args["Subcase"] and plan["member"] == "root"
    assert plan["lifetime_seconds"] == args["LifetimeSeconds"] and plan["member_sha256"] == PIN
    argv = plan["argv"]
    assert argv[:3] == ["-NoProfile", "-NonInteractive", "-Command"] and len(argv) == 4
    command = argv[3]
    assert plan["command_sha256"] == hashlib.sha256(command.encode("utf-8")).hexdigest().upper()
    assert plan["child_only_opt_in"] == {"variable": "WD_MIND_SHADOW_SYNTHETIC_MEMBER", "value": "opt-in",
                                         "scope": "child_process_only"}
    assert row["parse_errors"] == 0
    assert row["invocations"] == [args["MemberPath"]]  # one invocation, the literal member path, parsed back exactly
    assert row["member_args"] == [["-OptInSyntheticMember", "-LaunchId", LAUNCH, "-Member", "root", "-Subcase",
                                   args["Subcase"], "-WitnessDirectory", args["WitnessDirectory"],
                                   "-LifetimeSeconds", str(args["LifetimeSeconds"])]]
    assert "$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER='opt-in'" in row["assignments"]
    assert "'" + PIN + "'" in command and "exit 4" in command
    assert (command.index("ReadAllBytes(") < command.index("'" + PIN + "'") < command.index("$env:")
            < command.index("& '"))
    for word in ("Invoke-Expression", "iex", "Start-Process", "Add-Type", "[scriptblock]", "$input"):
        assert word not in command, word
    assert row["env_after"] == ""  # building the plan never sets the variable in the parent
    assert "TOCTOU" in plan["limits"] and plan["authority"].startswith("none")


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change,expected", OBSERVER_ROWS, ids=[row[0] for row in OBSERVER_ROWS])
def test_owned_output_observer_requests_one_stop_and_never_retries(rows, name, change, expected):
    row = rows[0][name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: ") and row["stop_calls"] == 0, row
        return
    results = row["results"]
    assert all(result["schema"] == "wd.mind-shadow-owned-output-observation.v1" for result in results)
    assert all(result["native_pass"] is False for result in results)
    first = results[0]
    if name in ("ob_below", "ob_at_bound", "ob_cap_accepted"):
        assert first["status"] == "WITHIN_OBSERVED_BUDGET" and first["stop"] == "not_requested", first
        assert first["total_bytes"] == first["stdout_bytes"] + first["stderr_bytes"] and row["stop_calls"] == 0
        return
    want = {"ob_above_then_again": "OVER_BOUND", "ob_missing_stderr": "UNKNOWN", "ob_directory_as_log": "UNKNOWN",
            "ob_stop_failure_no_retry": "OVER_BOUND", "ob_disk_floor": "DISK_FLOOR"}[name]
    assert first["status"] == want, first
    assert row["stop_calls"] == 1, row  # one stop request on the owned object, never repeated
    failed = name == "ob_stop_failure_no_retry"
    assert first["stop"] == ("failed" if failed else "requested"), first
    for later in results[1:]:  # sticky: a later observation reports the first trigger and never stops again
        assert later["status"] == want and later["stop"] == first["stop"], later
