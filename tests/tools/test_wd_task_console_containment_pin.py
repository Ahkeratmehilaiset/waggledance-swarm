"""Scheduled-task containment accepts a verified bridge pin on WD-AgentValue-Weekly (cold-boot rehearsal, 2026-09-27)."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

SCRIPT = REBOOT / "Set-WdTaskConsoleContainment.ps1"
FLEET_PYTHON = "C:\\Users\\janik\\AppData\\Local\\Programs\\Python\\Python313\\python.exe"   # wd-fleet.json bridge_python
BASE = f'"{FLEET_PYTHON}" "C:\\Python\\wd-agent-value-metric.py" --days 7 --post-bridge'
ORIGINAL = "C:\\Python\\wd-agent-value-metric.py --days 7 --post-bridge"
GEN = "d26357e14885e3d9b6d316de1806bdfab6d41026"


def bundle(store: Path, generation: str = GEN, content: bytes = b'{"source_commit":"x"}') -> str:
    root = store / generation
    root.mkdir(parents=True)
    (root / "deployment-manifest.json").write_bytes(content)
    return hashlib.sha256(content).hexdigest().upper()


def pin(store: Path, generation: str, sha: str) -> str:
    return f' --bridge-bundle "{store}\\{generation}" --bridge-manifest-sha256 {sha}'


def suffix_of(ps: str, arguments: str, store: Path, base: str = BASE):
    script = load(SCRIPT, "Get-VerifiedBridgePinSuffix") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$r = Get-VerifiedBridgePinSuffix -Arguments {q(arguments)} -Base {q(base)} -BundleStore {q(store)}
@{{ is_null = ($null -eq $r); value = [string]$r }} | ConvertTo-Json -Compress
"""
    record = json.loads(_run_powershell(script, executable=ps).stdout)
    return None if record["is_null"] else record["value"]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_bare_arguments_carry_no_pin(ps, tmp_path):
    assert suffix_of(ps, BASE, tmp_path) == ""


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_pin_to_a_deployed_bundle_with_its_manifest_hash_is_accepted(ps, tmp_path):
    sha = bundle(tmp_path)
    assert suffix_of(ps, BASE + pin(tmp_path, GEN, sha), tmp_path) == pin(tmp_path, GEN, sha)
    assert suffix_of(ps, ORIGINAL + pin(tmp_path, GEN, sha), tmp_path, base=ORIGINAL) == pin(tmp_path, GEN, sha)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["wrong_hash", "lowercase_hash", "no_manifest", "short_generation", "other_store",
                                  "trailing_argument", "base_mismatch", "unquoted_bundle", "extra_space"])
def test_anything_but_an_exact_verified_pin_is_refused(ps, tmp_path, case):
    store = tmp_path / "store"
    sha = bundle(store)
    arguments = BASE + pin(store, GEN, sha)
    if case == "wrong_hash":
        arguments = BASE + pin(store, GEN, "0" * 64)
    elif case == "lowercase_hash":
        arguments = BASE + pin(store, GEN, sha.lower())
    elif case == "no_manifest":
        (store / GEN / "deployment-manifest.json").unlink()
    elif case == "short_generation":
        arguments = BASE + pin(store, GEN[:39], sha)
    elif case == "other_store":
        other = tmp_path / "other"
        other_sha = bundle(other)
        arguments = BASE + pin(other, GEN, other_sha)
    elif case == "trailing_argument":
        arguments += " --days 30"
    elif case == "base_mismatch":
        arguments = BASE.replace("--days 7", "--days 8") + pin(store, GEN, sha)
    elif case == "unquoted_bundle":
        arguments = BASE + f" --bridge-bundle {store}\\{GEN} --bridge-manifest-sha256 {sha}"
    elif case == "extra_space":
        arguments = BASE + " " + pin(store, GEN, sha)
    assert suffix_of(ps, arguments, store) is None


@pytest.mark.skipif(os.name != "nt", reason="junctions are Windows reparse points")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_bundle_that_is_a_junction_is_refused(ps, tmp_path):
    real = tmp_path / "real"
    sha = bundle(real)
    store = tmp_path / "store"
    store.mkdir()
    _run_powershell(f"New-Item -ItemType Junction -Path {q(store / GEN)} -Target {q(real / GEN)} | Out-Null", executable=ps)
    assert (store / GEN / "deployment-manifest.json").is_file()        # the junction resolves
    assert suffix_of(ps, BASE + pin(store, GEN, sha), store) is None


def task_pin(ps: str, arguments: list[str], bridge_pin: bool | None, store: Path) -> str:
    actions = ",".join(f"[pscustomobject]@{{Arguments={q(a)}}}" for a in arguments)
    script = load(SCRIPT, "Get-VerifiedBridgePinSuffix") + load(SCRIPT, "Get-TaskBridgePin") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$task = [pscustomobject]@{{ Actions = @({actions}) }}
$job = [pscustomobject]@{{ hidden_arguments = {q(BASE)}; original_arguments = {q(ORIGINAL)} }}
if ({'$null' if bridge_pin is None else '$true'}) {{ $job | Add-Member -NotePropertyName bridge_pin -NotePropertyValue ${str(bool(bridge_pin)).lower()} }}
@{{ pin = [string](Get-TaskBridgePin -Task $task -Job $job -BundleStore {q(store)}) }} | ConvertTo-Json -Compress
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)["pin"]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_task_pin_comes_from_either_base_form_and_only_for_a_pinned_job(ps, tmp_path):
    sha = bundle(tmp_path)
    good = pin(tmp_path, GEN, sha)
    assert task_pin(ps, [BASE + good], True, tmp_path) == good
    assert task_pin(ps, [ORIGINAL + good], True, tmp_path) == good
    assert task_pin(ps, [BASE + good], False, tmp_path) == ""               # a job with bridge_pin false never takes one
    assert task_pin(ps, [BASE + good], None, tmp_path) == ""                # nor does a job without the property (StrictMode)
    assert task_pin(ps, [BASE + good, BASE], True, tmp_path) == ""         # not a single action
    assert task_pin(ps, [BASE + pin(tmp_path, GEN, "F" * 64)], True, tmp_path) == ""


def test_exactly_the_two_pinned_writer_jobs_take_a_pin_and_apply_keeps_it():
    text = SCRIPT.read_text(encoding="utf-8")
    weekly = text.index("name = 'WD-AgentValue-Weekly'")
    stall = text.index("name = 'WD-ConsensusStallDetector'")
    assert "bridge_pin = $true" in text[weekly:text.index("}", weekly)]
    assert "bridge_pin = $true" in text[stall:text.index("}", stall)]
    assert text.count("\n    bridge_pin = $true\n") == 2                    # job level; legacy forms are indented deeper
    apply = text.index("# Wrapping keeps the verified pin the plan saw")
    assert text.index("$pins[[string]$job.name] = $pin") < apply
    assert text.count("-Arguments $hiddenArguments") == 2                  # the wrap check and the postcondition
    assert "Argument = $hiddenArguments" in text
    assert "-Arguments ([string]$job.hidden_arguments + $pin)" in text
    assert "-Arguments ([string]$job.original_arguments + $pin)" in text


# The whole script under -Apply, with Task Scheduler mocked. Only four exact
# substitutions are made: the launcher path and hash, the bundle store, and
# the Windows principal block. The test hook remains the point between plan
# and apply where a test can change a task, including under Linux pwsh.
WEEKLY = "WD-AgentValue-Weekly"
STALL = "WD-ConsensusStallDetector"
LEGACY = "WD-BridgeMergeDriver"
WEEKLY_EXECUTE = FLEET_PYTHON
WEEKLY_COPY = "C:\\Python\\project2-master\\.python\\Python313\\python.exe"          # the untracked runtime copy
WEEKLY_COPY_HIDDEN = f'"{WEEKLY_COPY}" "C:\\Python\\wd-agent-value-metric.py" --days 7 --post-bridge'
STALL_EXECUTE = FLEET_PYTHON
STALL_ORIGINAL = "C:\\Python\\wd_consensus_stall_detector.py --alert"
STALL_HIDDEN = f'"{STALL_EXECUTE}" "C:\\Python\\wd_consensus_stall_detector.py" --alert'
STALL_ALIAS = "C:\\Users\\janik\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe"
STALL_ALIAS_HIDDEN = f'"{STALL_ALIAS}" "C:\\Python\\wd_consensus_stall_detector.py" --alert'
STALL_OTHER = "C:\\Python\\other.py"
STALL_WD = "C:\\Python"
OTHER_EXECUTE = "C:\\Python\\other.exe"
LEGACY_ARGUMENTS = "-NoProfile -ExecutionPolicy Bypass -File C:\\Python\\Invoke-BridgeMergeDriver.ps1 -Loop -PollSeconds 120"
SUBSTITUTED = {
    "$silentLauncher = 'C:\\Python\\wd_silent_launch.exe'": "$silentLauncher = {launcher}",
    "$silentLauncherSha256 = '4CD4FBED01E3EAD1C999493212F7499137C0937F597BDD5172C0EFEEDA3F509F'": "$silentLauncherSha256 = {launcher_sha}",
    "$bundleStore = 'C:\\Python\\wd-reboot-bundles'": "$bundleStore = {store}",
    "$identity = [Security.Principal.WindowsIdentity]::GetCurrent()\n"
    "$principal = New-Object Security.Principal.WindowsPrincipal($identity)\n"
    "if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {":
        "if (-not (Test-WdTestAdministrator)) {{",
}
LAUNCHER_BYTES = b"not a real launcher"


def task_literal(execute: str, arguments: str, working_directory: str = "", enabled: bool = False) -> str:
    return f"(New-WdTestTask {q(execute)} {q(arguments)} {q(working_directory)} ${str(enabled).lower()})"


def run_apply(ps: str, tmp_path: Path, tasks: dict, change: str = "", at: str = "admin", apply: bool = True,
              set_enables: bool = False) -> dict:
    """Runs the script over mocked tasks; `change` is PowerShell that runs once, at `at`."""
    launcher = tmp_path / "wd_silent_launch.exe"
    launcher.write_bytes(LAUNCHER_BYTES)
    text = SCRIPT.read_text(encoding="utf-8")
    values = {"launcher": q(launcher), "launcher_sha": q(hashlib.sha256(LAUNCHER_BYTES).hexdigest().upper()),
              "store": q(tmp_path / "store")}
    for old, new in SUBSTITUTED.items():
        assert text.count(old) == 1, old
        text = text.replace(old, new.format(**values))
    script_path = tmp_path / "containment-under-test.ps1"
    script_path.write_text(text, encoding="utf-8-sig")
    seeds = "\n".join(f"$global:WdTasks[{q(name)}] = {literal}" for name, literal in tasks.items())
    # Like load(): Windows PowerShell must not inherit a PowerShell 7 module path, or
    # Get-FileHash does not resolve when pytest itself runs under pwsh.
    harness = f"""
if ($PSVersionTable.PSEdition -eq 'Desktop') {{ $env:PSModulePath = Join-Path $PSHOME 'Modules' }}
$ErrorActionPreference = 'Stop'
$global:WdTasks = @{{}}
$global:WdCalls = New-Object 'System.Collections.Generic.List[string]'
$global:WdChanged = $false
$global:WdSetEnables = ${str(set_enables).lower()}
function global:New-WdTestTask($execute, $arguments, $workingDirectory, $enabled) {{
  [pscustomobject]@{{
    Actions = @([pscustomobject]@{{ Execute = $execute; Arguments = $arguments; WorkingDirectory = $workingDirectory }})
    Settings = [pscustomobject]@{{ Enabled = [bool]$enabled }}
    State = 'Ready'
  }}
}}
function global:Invoke-WdTestChange([string] $at) {{
  if ($at -ne {q(at)} -or $global:WdChanged) {{ return }}
  $global:WdChanged = $true
  {change}
}}
function global:Test-WdTestAdministrator {{ Invoke-WdTestChange 'admin'; $true }}
function global:Get-ScheduledTask {{
  [CmdletBinding()] param($TaskPath, $TaskName)
  if ($global:WdTasks.ContainsKey($TaskName)) {{ $global:WdTasks[$TaskName] }}
}}
function global:New-ScheduledTaskAction {{
  [CmdletBinding()] param($Execute, $Argument, $WorkingDirectory)
  [pscustomobject]@{{ Execute = $Execute; Arguments = $Argument; WorkingDirectory = $WorkingDirectory }}
}}
function global:Set-ScheduledTask {{
  [CmdletBinding()] param($TaskPath, $TaskName, $Action)
  $global:WdCalls.Add("set $TaskName")
  $old = $global:WdTasks[$TaskName]
  $global:WdTasks[$TaskName] = [pscustomobject]@{{
    Actions = @($Action)
    Settings = [pscustomobject]@{{ Enabled = ([bool]$old.Settings.Enabled -or $global:WdSetEnables) }}
    State = 'Ready'
  }}
}}
function global:Disable-ScheduledTask {{
  [CmdletBinding()] param($TaskPath, $TaskName)
  $global:WdCalls.Add("disable $TaskName")
  $global:WdTasks[$TaskName].Settings.Enabled = $false
  Invoke-WdTestChange 'hold'
}}
function global:Stop-ScheduledTask {{
  [CmdletBinding()] param($TaskPath, $TaskName)
  $global:WdCalls.Add("stop $TaskName")
}}
{seeds}
$errorText = ''
$result = $null
try {{
  $result = & {q(script_path)} {'-Apply' if apply else ''}
}} catch {{
  $errorText = $_.Exception.Message
}}
$final = @{{}}
foreach ($name in @($global:WdTasks.Keys)) {{
  $t = $global:WdTasks[$name]
  $a = @($t.Actions)[0]
  $final[$name] = @{{ execute = [string]$a.Execute; arguments = [string]$a.Arguments;
    working_directory = [string]$a.WorkingDirectory; enabled = [bool]$t.Settings.Enabled }}
}}
@{{ error = $errorText; result = $result; calls = [string[]]$global:WdCalls.ToArray(); tasks = $final }} |
  ConvertTo-Json -Depth 6 -Compress
"""
    record = json.loads(_run_powershell(harness, executable=ps).stdout)
    record["launcher"] = str(launcher)
    return record


def weekly_original(suffix: str = "", enabled: bool = False) -> str:
    return task_literal(WEEKLY_EXECUTE, ORIGINAL + suffix, "", enabled)


def legacy_enabled() -> str:
    return task_literal("powershell.exe", LEGACY_ARGUMENTS, "", enabled=True)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_apply_wraps_a_pinned_disabled_task_keeping_its_pin_and_disabled_state(ps, tmp_path):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    record = run_apply(ps, tmp_path, {
        WEEKLY: weekly_original(good, enabled=False),
        STALL: task_literal(STALL_EXECUTE, STALL_ORIGINAL, "C:\\Python", enabled=True),
        LEGACY: legacy_enabled(),
    })
    assert record["error"] == ""
    assert record["calls"] == [f"disable {LEGACY}", f"stop {LEGACY}", f"set {STALL}", f"set {WEEKLY}"]
    assert record["tasks"][WEEKLY] == {"execute": record["launcher"], "arguments": BASE + good,
                                       "working_directory": "", "enabled": False}
    assert record["tasks"][STALL] == {"execute": record["launcher"], "arguments": STALL_HIDDEN,
                                      "working_directory": "C:\\Python", "enabled": True}
    assert record["tasks"][LEGACY]["enabled"] is False
    assert record["result"]["applied"] is True
    assert record["result"]["legacy"] == "hold-exact"
    jobs = {job["name"]: job for job in record["result"]["jobs"]}
    assert jobs[WEEKLY] == {"name": WEEKLY, "action": "hidden-exact", "enabled": False}
    assert jobs[STALL] == {"name": STALL, "action": "hidden-exact", "enabled": True}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_apply_leaves_an_already_hidden_pinned_task_untouched(ps, tmp_path):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    launcher = tmp_path / "wd_silent_launch.exe"
    record = run_apply(ps, tmp_path, {WEEKLY: task_literal(str(launcher), BASE + good, "", enabled=False)})
    assert record["error"] == ""
    assert record["calls"] == []
    assert record["tasks"][WEEKLY]["arguments"] == BASE + good


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["wrong_hash", "trailing_argument", "no_bundle"])
def test_apply_with_an_unverified_pin_changes_nothing(ps, tmp_path, case):
    store = tmp_path / "store"
    sha = bundle(store)
    arguments = {"wrong_hash": pin(store, GEN, "0" * 64), "trailing_argument": pin(store, GEN, sha) + " --days 30",
                 "no_bundle": pin(store, "e" * 40, sha)}[case]
    record = run_apply(ps, tmp_path, {WEEKLY: weekly_original(arguments), LEGACY: legacy_enabled()})
    assert "scheduled console task action drifted: WD-AgentValue-Weekly" in record["error"]
    assert record["calls"] == []                                   # not even the legacy HOLD
    assert record["tasks"][WEEKLY]["arguments"] == ORIGINAL + arguments
    assert record["tasks"][LEGACY]["enabled"] is True


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("at", ["admin", "hold"])
@pytest.mark.parametrize("case", ["other_pin", "other_action", "other_execute", "enabled", "bundle_removed", "appeared",
                                  "vanished", "unpinned_other_arguments"])
def test_a_task_that_changes_between_plan_and_apply_is_not_overwritten(ps, tmp_path, case, at):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    other_generation = "f" * 40
    other = pin(store, other_generation, bundle(store, other_generation, b'{"source_commit":"y"}'))
    change = {
        "other_pin": f"$global:WdTasks[{q(WEEKLY)}] = {weekly_original(other)}",
        "other_action": f"$global:WdTasks[{q(WEEKLY)}] = {weekly_original(good + ' --days 30')}",
        "other_execute": f"$global:WdTasks[{q(WEEKLY)}] = {task_literal(OTHER_EXECUTE, ORIGINAL + good)}",
        "enabled": f"$global:WdTasks[{q(WEEKLY)}].Settings.Enabled = $true",
        "bundle_removed": f"Remove-Item -LiteralPath {q(store / GEN / 'deployment-manifest.json')}",
        "appeared": f"$global:WdTasks[{q(STALL)}] = {task_literal(STALL_EXECUTE, STALL_OTHER, STALL_WD, True)}",
        "vanished": f"$global:WdTasks.Remove({q(WEEKLY)})",
        "unpinned_other_arguments":
            f"$global:WdTasks[{q(STALL)}] = {task_literal(STALL_EXECUTE, STALL_OTHER, STALL_WD, True)}",
    }[case]
    tasks = {WEEKLY: weekly_original(good), LEGACY: legacy_enabled()}
    if case == "unpinned_other_arguments":
        tasks[STALL] = task_literal(STALL_EXECUTE, STALL_ORIGINAL, STALL_WD, True)
    record = run_apply(ps, tmp_path, tasks, change=change, at=at)
    changed_task = STALL if case in ("appeared", "unpinned_other_arguments") else WEEKLY
    assert f"scheduled console task changed between plan and apply: {changed_task}" in record["error"]
    assert not [call for call in record["calls"] if call.startswith("set ")]
    if at == "admin":
        assert record["calls"] == []                               # refused before the legacy HOLD
    expected = {
        "other_pin": ORIGINAL + other,
        "other_action": ORIGINAL + good + " --days 30",
        "other_execute": ORIGINAL + good,
        "enabled": ORIGINAL + good,
        "bundle_removed": ORIGINAL + good,
        "appeared": ORIGINAL + good,
        "unpinned_other_arguments": ORIGINAL + good,
    }
    if case in expected:
        assert record["tasks"][WEEKLY]["execute"] == (OTHER_EXECUTE if case == "other_execute" else WEEKLY_EXECUTE)
        assert record["tasks"][WEEKLY]["arguments"] == expected[case]
    if case in ("appeared", "unpinned_other_arguments"):
        assert record["tasks"][STALL]["arguments"] == STALL_OTHER


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_apply_postcondition_catches_a_changed_enabled_state(ps, tmp_path):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    record = run_apply(ps, tmp_path, {WEEKLY: weekly_original(good)}, set_enables=True)
    assert "scheduled console task postcondition failed: WD-AgentValue-Weekly" in record["error"]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_dry_run_changes_nothing(ps, tmp_path):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    record = run_apply(ps, tmp_path, {WEEKLY: weekly_original(good), LEGACY: legacy_enabled()}, apply=False)
    assert record["error"] == ""
    assert record["calls"] == []
    assert record["result"]["applied"] is False
    assert record["result"]["legacy"] == "would-hold"
    assert record["result"]["jobs"] == [{"name": STALL, "action": "absent-skip", "enabled": False},
                                        {"name": WEEKLY, "action": "wrap-hidden", "enabled": False}]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_stall_detector_takes_a_verified_pin_and_apply_keeps_it(ps, tmp_path):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    record = run_apply(ps, tmp_path, {
        STALL: task_literal(STALL_EXECUTE, STALL_ORIGINAL + good, STALL_WD, enabled=False),
        LEGACY: legacy_enabled(),
    })
    assert record["error"] == ""
    assert record["tasks"][STALL] == {"execute": record["launcher"], "arguments": STALL_HIDDEN + good,
                                      "working_directory": STALL_WD, "enabled": False}
    jobs = {job["name"]: job for job in record["result"]["jobs"]}
    assert jobs[STALL] == {"name": STALL, "action": "hidden-exact", "enabled": False}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_an_already_hidden_pinned_stall_detector_is_left_untouched(ps, tmp_path):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    launcher = tmp_path / "wd_silent_launch.exe"
    record = run_apply(ps, tmp_path, {STALL: task_literal(str(launcher), STALL_HIDDEN + good, STALL_WD, enabled=True)})
    assert record["error"] == ""
    assert record["calls"] == []
    assert record["tasks"][STALL]["arguments"] == STALL_HIDDEN + good


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_an_unverified_stall_detector_pin_changes_nothing(ps, tmp_path):
    store = tmp_path / "store"
    bundle(store)
    bad = pin(store, GEN, "0" * 64)
    record = run_apply(ps, tmp_path, {STALL: task_literal(STALL_EXECUTE, STALL_ORIGINAL + bad, STALL_WD, True),
                                      LEGACY: legacy_enabled()})
    assert "scheduled console task action drifted: WD-ConsensusStallDetector" in record["error"]
    assert record["calls"] == []
    assert record["tasks"][STALL]["arguments"] == STALL_ORIGINAL + bad


def alias_task(form: str, launcher: Path, suffix: str = "", working_directory: str = STALL_WD,
               enabled: bool = True) -> str:
    if form == "bare":
        return task_literal(STALL_ALIAS, STALL_ORIGINAL + suffix, working_directory, enabled)
    return task_literal(str(launcher), STALL_ALIAS_HIDDEN + suffix, working_directory, enabled)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("form", ["bare", "hidden"])
@pytest.mark.parametrize("enabled", [True, False])
def test_the_exact_windowsapps_alias_forms_migrate_to_the_explicit_interpreter(ps, tmp_path, form, enabled):
    launcher = tmp_path / "wd_silent_launch.exe"
    tasks = {STALL: alias_task(form, launcher, enabled=enabled), LEGACY: legacy_enabled()}
    plan = run_apply(ps, tmp_path, tasks, apply=False)
    assert plan["error"] == ""
    assert plan["calls"] == []
    assert {job["name"]: job for job in plan["result"]["jobs"]}[STALL] == {
        "name": STALL, "action": "migrate-hidden", "enabled": enabled}
    record = run_apply(ps, tmp_path, tasks)
    assert record["error"] == ""
    assert f"set {STALL}" in record["calls"]
    assert record["tasks"][STALL] == {"execute": record["launcher"], "arguments": STALL_HIDDEN,
                                      "working_directory": STALL_WD, "enabled": enabled}
    assert {job["name"]: job for job in record["result"]["jobs"]}[STALL] == {
        "name": STALL, "action": "hidden-exact", "enabled": enabled}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("form", ["bare", "hidden"])
@pytest.mark.parametrize("case", ["pinned", "other_working_directory", "extra_argument"])
def test_any_other_windowsapps_alias_form_still_drifts(ps, tmp_path, form, case):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    launcher = tmp_path / "wd_silent_launch.exe"
    task = {"pinned": alias_task(form, launcher, good),
            "other_working_directory": alias_task(form, launcher, working_directory=""),
            "extra_argument": alias_task(form, launcher, " --verbose")}[case]
    record = run_apply(ps, tmp_path, {STALL: task, LEGACY: legacy_enabled()})
    assert "scheduled console task action drifted: WD-ConsensusStallDetector" in record["error"]
    assert record["calls"] == []
    assert record["tasks"][STALL]["execute"] in (STALL_ALIAS, str(launcher))


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_an_alias_task_that_changes_between_plan_and_apply_is_not_migrated(ps, tmp_path):
    launcher = tmp_path / "wd_silent_launch.exe"
    change = f"$global:WdTasks[{q(STALL)}] = {alias_task('hidden', launcher)}"
    record = run_apply(ps, tmp_path, {STALL: alias_task("bare", launcher), LEGACY: legacy_enabled()},
                       change=change, at="hold")
    assert "scheduled console task changed between plan and apply: WD-ConsensusStallDetector" in record["error"]
    assert not [call for call in record["calls"] if call.startswith("set ")]
    assert record["tasks"][STALL]["arguments"] == STALL_ALIAS_HIDDEN


def test_both_reporters_run_on_the_fleet_python_and_declare_only_their_live_legacy_forms():
    text = SCRIPT.read_text(encoding="utf-8")
    stall = text.index("name = 'WD-ConsensusStallDetector'")
    weekly = text.index("name = 'WD-AgentValue-Weekly'")
    fleet = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    assert fleet["bridge_python"]["executable"] == FLEET_PYTHON
    assert f"$reporterPython = '{FLEET_PYTHON}'" in text
    assert text.count("original_execute = $reporterPython") == 2
    assert text.count("legacy_actions = @(") == 2
    assert text.count(r"WindowsApps\python.exe") == 2                 # the stall detector's two alias forms
    assert text.count(r"project2-master\.python\Python313\python.exe") == 2   # the weekly job's two copy forms
    assert text[stall:weekly].count("bridge_pin = $false") == 2         # the alias forms never carry a pin
    assert text[weekly:].count("bridge_pin = $true") == 3               # the job and both copy forms


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("form", ["bare", "hidden"])
@pytest.mark.parametrize("pinned", [True, False])
def test_the_weekly_copy_forms_migrate_to_the_fleet_python_keeping_their_pin(ps, tmp_path, form, pinned):
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store)) if pinned else ""
    launcher = tmp_path / "wd_silent_launch.exe"
    task = (task_literal(WEEKLY_COPY, ORIGINAL + good, "", False) if form == "bare"
            else task_literal(str(launcher), WEEKLY_COPY_HIDDEN + good, "", False))
    plan = run_apply(ps, tmp_path, {WEEKLY: task, LEGACY: legacy_enabled()}, apply=False)
    assert plan["error"] == ""
    assert {job["name"]: job for job in plan["result"]["jobs"]}[WEEKLY]["action"] == "migrate-hidden"
    record = run_apply(ps, tmp_path, {WEEKLY: task, LEGACY: legacy_enabled()})
    assert record["error"] == ""
    assert record["tasks"][WEEKLY] == {"execute": record["launcher"], "arguments": BASE + good,
                                       "working_directory": "", "enabled": False}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["unverified_pin", "other_working_directory", "extra_argument"])
def test_any_other_weekly_copy_form_still_drifts(ps, tmp_path, case):
    store = tmp_path / "store"
    bundle(store)
    launcher = tmp_path / "wd_silent_launch.exe"
    task = {"unverified_pin": task_literal(str(launcher), WEEKLY_COPY_HIDDEN + pin(store, GEN, "0" * 64), "", False),
            "other_working_directory": task_literal(str(launcher), WEEKLY_COPY_HIDDEN, "C:\\Python", False),
            "extra_argument": task_literal(WEEKLY_COPY, ORIGINAL + " --days 30", "", False)}[case]
    record = run_apply(ps, tmp_path, {WEEKLY: task, LEGACY: legacy_enabled()})
    assert "scheduled console task action drifted: WD-AgentValue-Weekly" in record["error"]
    assert record["calls"] == []


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_the_ancillary_installer_final_actions_are_accepted_unchanged(ps, tmp_path):
    # Install-AncillaryRepair.ps1 writes wd_silent_launch.exe + '"<fleet python>" "<script>" ...' + pin,
    # disabled, with the stall detector in C:\Python and the weekly job without a working directory.
    store = tmp_path / "store"
    good = pin(store, GEN, bundle(store))
    launcher = tmp_path / "wd_silent_launch.exe"
    tasks = {WEEKLY: task_literal(str(launcher), BASE + good, "", False),
             STALL: task_literal(str(launcher), STALL_HIDDEN + good, STALL_WD, False),
             LEGACY: task_literal("powershell.exe", LEGACY_ARGUMENTS, "", enabled=False)}
    plan = run_apply(ps, tmp_path, tasks, apply=False)
    assert plan["error"] == ""
    assert [job["action"] for job in plan["result"]["jobs"]] == ["hidden-exact", "hidden-exact"]
    record = run_apply(ps, tmp_path, tasks)
    assert record["error"] == ""
    assert not [call for call in record["calls"] if call.startswith("set ")]
    assert record["tasks"][WEEKLY]["arguments"] == BASE + good
    assert record["tasks"][STALL]["arguments"] == STALL_HIDDEN + good
