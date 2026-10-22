"""Synthetic contract of the A1-A7 case evaluators in the opt-in native acceptance harness.

The harness is never executed here. A test host parses it with the PowerShell parser, defines ONLY its top-level
functions (the script body with its refusal and every native step stays unrun), replaces the process, job and
expression cmdlets with mocks that throw on any call, and feeds the pure case evaluators strict FAKE observations.
A verdict here is synthetic author proof of the refusal, outcome and record logic, never native acceptance: native
A1-A7 stays NOT_RUN until an approved Medium executor runs the harness. Case contract: RCO1 (A1-A7, proposal v2 as
locked by Lead), RCO2 (G1-G4, the member witness and the A1 pipe-holding grandchild), Lead (the guard cases, the A4
publication-kill rule, the A7 pre-start binding and this Source-preparation GO); fable-5 wrote this test and the
evaluators.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "windows" / "wd_mind_shadow_job_native_acceptance.ps1"

HOST = r"""
param([string] $Harness, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Harness, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'the harness does not parse' }
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
function ConvertTo-HostValue($Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [Management.Automation.PSCustomObject]) {
        $table = @{}
        foreach ($property in $Value.PSObject.Properties) {
            $table[$property.Name] = ConvertTo-HostValue $property.Value
        }
        return $table
    }
    if ($Value -is [array]) { return , @($Value | ForEach-Object { ConvertTo-HostValue $_ }) }
    return $Value
}
$rows = @()
$items = ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture))
foreach ($item in $items) {
    $entry = ConvertTo-HostValue $item
    if ($entry['kind'] -ceq 'case') {
        $verdict = Get-WdShadowCaseVerdict -CaseId $entry['case_id'] -Binding $entry['binding'] `
            -Observation $entry['observation']
    } else {
        $verdict = Get-WdShadowRunVerdict -Verdicts $entry['verdicts']
    }
    $rows += [ordered]@{ name = $entry['name']; verdict = [string]$verdict }
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 4
"""


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


def _binding(case_id, launch="11111111-2222-4333-8444-555555555555", deadline="1800"):
    # Text values only: PowerShell 7 ConvertFrom-Json would turn an ISO date string into a DateTime, which the strict
    # binding check refuses (the evaluators compare binding values as exact strings).
    return {"harness_run_id": "SYNTHETIC-HARNESS-RUN", "case_id": case_id, "launch_id": launch, "root_pid": "4242",
            "root_creation_utc": "SYNTHETIC-ROOT-CREATION-UTC", "deadline_seconds": deadline}


def _stopped(outcome):
    return {"outcome": outcome, "stop_confirmed": True, "active_processes_after": 0, "elapsed_seconds": 1801}


def _a1():
    return {"deadline": _stopped("stopped_at_deadline"),
            "early_root_exit_orphan": {"outcome": "stopped_at_deadline", "orphan_alive_at_root_exit": True},
            "pipe_holding_grandchild": _stopped("stopped_at_deadline"),
            "off_switch_during_run": _stopped("stopped_by_off_switch"),
            "disk_floor_hit": _stopped("stopped_at_disk_floor"),
            "single_confirmed_stop": {"stop_calls": 1, "stop_confirmed": True}}


def _a1_at(elapsed):
    return {name: (dict(sub, elapsed_seconds=elapsed) if "elapsed_seconds" in sub else sub)
            for name, sub in _a1().items()}


def _a2():
    return {"survivors": {"live_descendants": [], "independent_survivors": [],
                          "child_started_during_stop_survived": False}}


def _a3():
    return {name: {"result": "assigned_before_resume"} for name in
            ("outer_no_limits", "outer_kill_on_close", "planned_launch_context")}


def _a5(launch="11111111-2222-4333-8444-555555555555", **member):
    seen = {"start_witness": "present", "released": True, "launch_id": launch}
    seen.update(member)
    return {"controller_death": {"expected": ["root", "child", "grandchild"],
                                 "observed": {"root": dict(seen), "child": dict(seen), "grandchild": dict(seen)}}}


def _a6():
    return {"inherited_handles": {"unexpected_inherited_handles": [],
                                  "lock_reacquire": {"attempted": True, "acquired": True, "released": True,
                                                     "error": None}}}


REFUSED = {"exit_code": 1, "refusal_seen": True, "run_dir_created": False, "process_started": False}
# Lead's v5 contract delta (2026-10-22T19:07:50Z): RCO1's 10-17 refinement names the A7 integrity AND user variants,
# so the user SID mismatch is a required sub-case too; the old eight-sub-case set is never complete.
A7_OLD_EIGHT = ("integrity_mismatch", "preloaded_type", "lane_loop_zero_functions", "lane_loop_two_functions",
                "off_switch_before_start", "off_switch_denied_read", "disk_floor_before_start", "reparse_ancestry")
A7_SUBCASES = A7_OLD_EIGHT[:1] + ("user_sid_mismatch",) + A7_OLD_EIGHT[1:]
# Lead's v6 decision (2026-10-22T19:41:59Z): the native A7 scope is the seven reachable pre-start guards; the zero- and
# two-function loop cases are structural fixture coverage only. A7 binds to its pre-start probe attempts, never to a
# managed launch id or root PID. Every identity below is synthetic and labelled so; a real run must observe them.
A7_STRUCTURAL = ("lane_loop_zero_functions", "lane_loop_two_functions")
A7_NATIVE = tuple(name for name in A7_SUBCASES if name not in A7_STRUCTURAL)
A7_SCHEMA = "wd.mind-shadow-a7-prestart-binding.v1"
SYNTHETIC_COMMIT = "5" * 40  # synthetic, never a real commit
SYNTHETIC_MANIFEST = "5" * 64  # synthetic, never a real manifest hash
SYNTHETIC_ADAPTER = "6" * 64  # synthetic, never a real adapter hash


def _a7_attempts():
    return {name: f"5a7a7a7a-0000-4000-8000-{index:012d}" for index, name in enumerate(A7_NATIVE, 1)}


def _a7_binding(**change):
    binding = {"schema": A7_SCHEMA, "harness_run_id": "SYNTHETIC-HARNESS-RUN", "case_id": "A7",
               "controller_pid": "4141", "controller_creation_utc": "SYNTHETIC-CONTROLLER-START",
               "source_commit": SYNTHETIC_COMMIT, "manifest_sha256": SYNTHETIC_MANIFEST,
               "adapter_sha256": SYNTHETIC_ADAPTER, "attempts": _a7_attempts()}
    binding.update(change)
    return binding


def _a7_seen(**change):
    seen = {key: value for key, value in _a7_binding().items() if key != "attempts"}
    seen.update(change)
    return seen


def _a7(names=A7_NATIVE):
    attempts = _a7_attempts()
    return {name: {"attempt": {"attempt_id": attempts.get(name, "5a7a7a7a-0000-4000-8000-999999999999"),
                               "probe_pid": str(5000 + index), "probe_creation_utc": f"SYNTHETIC-PROBE-START-{index}"},
                   "refusal": dict(REFUSED)} for index, name in enumerate(names, 1)}


def _a7_case(name, subcases, seen=None, expected=None):
    return {"name": name, "kind": "case", "case_id": "A7",
            "binding": expected if expected is not None else _a7_binding(),
            "observation": {"binding": seen if seen is not None else _a7_seen(), "subcases": subcases}}


def _a7_with(subcases, name, part, **change):
    copy = json.loads(json.dumps(subcases))
    copy[name][part].update(change)
    return copy


def _case(name, case_id, subcases, binding=None, extra=None, expected=None):
    observation = {"binding": binding if binding is not None else _binding(case_id), "subcases": subcases}
    if extra:
        observation.update(extra)
    return {"name": name, "kind": "case", "case_id": case_id,
            "binding": expected if expected is not None else _binding(case_id), "observation": observation}


def _with(subcases, name, **change):
    copy = json.loads(json.dumps(subcases))
    copy[name].update(change)
    return copy


ALL_PASS = {f"A{i}": "PASS" for i in range(1, 8)}
BLANK_A6 = {key: ("A6" if key == "case_id" else "") for key in _binding("A6")}
INVALID_A6 = (("harness_run_id", "", "blank"), ("harness_run_id", "  ", "space"), ("launch_id", "", "blank"),
              ("launch_id", "launch-1", "not_a_guid"), ("root_pid", "", "blank"), ("root_pid", "0", "zero"),
              ("root_creation_utc", "", "blank"), ("deadline_seconds", "", "blank"),
              ("deadline_seconds", "abc", "text"))

FIXTURES = [
    (_case("a1_pass", "A1", _a1()), "PASS"),
    (_case("a1_deadline_completed", "A1", _with(_a1(), "deadline", outcome="completed")), "FAIL"),
    (_case("a1_survivor_after_stop", "A1", _with(_a1(), "deadline", active_processes_after=1)), "FAIL"),
    (_case("a1_past_the_bound", "A1", _with(_a1(), "deadline", elapsed_seconds=1831)), "FAIL"),
    (_case("a1_early_exit_completed", "A1", _with(_a1(), "early_root_exit_orphan", outcome="completed")), "FAIL"),
    (_case("a1_switch_wrong_outcome", "A1", _with(_a1(), "off_switch_during_run", outcome="stopped_at_deadline")),
     "FAIL"),
    (_case("a1_two_stops", "A1", _with(_a1(), "single_confirmed_stop", stop_calls=2)), "FAIL"),
    (_case("a1_unconfirmed_stop", "A1", _with(_a1(), "deadline", stop_confirmed=None)), "UNKNOWN"),
    (_case("a1_text_count", "A1", _with(_a1(), "deadline", active_processes_after="0")), "UNKNOWN"),
    (_case("a1_missing_subcase", "A1", {k: v for k, v in _a1().items() if k != "disk_floor_hit"}), "UNKNOWN"),
    (_case("a1_extra_key", "A1", _with(_a1(), "deadline", note="x")), "UNKNOWN"),
    (_case("a1_extra_subcase", "A1", dict(_a1(), bonus={"stop_calls": 1, "stop_confirmed": True})), "UNKNOWN"),
    (_case("a1_extra_top_key", "A1", _a1(), extra={"verdict": "PASS"}), "UNKNOWN"),
    (_case("a1_foreign_launch", "A1", _a1(), binding=_binding("A1", launch="99999999-2222-4333-8444-555555555555")),
     "FAIL"),
    (_case("a1_partial_binding", "A1", _a1(), binding={k: v for k, v in _binding("A1").items() if k != "root_pid"}),
     "UNKNOWN"),
    (_case("a2_pass", "A2", _a2()), "PASS"),
    (_case("a2_descendant_survivor", "A2", _with(_a2(), "survivors", live_descendants=[{"pid": 7}])), "FAIL"),
    (_case("a2_independent_survivor", "A2", _with(_a2(), "survivors", independent_survivors=[{"pid": 7}])), "FAIL"),
    (_case("a2_stop_child_survived", "A2", _with(_a2(), "survivors", child_started_during_stop_survived=True)),
     "FAIL"),
    (_case("a2_unmeasured_list", "A2", _with(_a2(), "survivors", independent_survivors=None)), "UNKNOWN"),
    (_case("a3_pass", "A3", _a3()), "PASS"),
    (_case("a3_refused_counts", "A3", _with(_a3(), "outer_kill_on_close", result="refused_before_start")), "PASS"),
    (_case("a3_started_unassigned", "A3", _with(_a3(), "planned_launch_context", result="started_unassigned")),
     "FAIL"),
    (_case("a3_unknown_result", "A3", _with(_a3(), "outer_no_limits", result="maybe")), "UNKNOWN"),
    (_case("a4_never_ready", "A4", {"publication_kill": {"before_replace": "old_complete",
                                                         "after_replace": "new_complete"}}), "NOT_READY"),
    (_case("a5_pass", "A5", _a5()), "PASS"),
    (_case("a5_missing_witness", "A5", _a5(start_witness="missing")), "FAIL"),
    (_case("a5_survivor_holds_witness", "A5", _a5(released=False)), "FAIL"),
    (_case("a5_unknown_release", "A5", _a5(released="UNKNOWN")), "UNKNOWN"),
    (_case("a5_foreign_witness", "A5", _a5(launch="99999999-2222-4333-8444-555555555555")), "FAIL"),
    (_case("a5_empty_member_set", "A5", {"controller_death": {"expected": [], "observed": {}}}), "UNKNOWN"),
    (_case("a6_pass", "A6", _a6()), "PASS"),
    (_case("a6_unexpected_handle", "A6", _with(_a6(), "inherited_handles",
                                               unexpected_inherited_handles=[{"type": "File"}])), "FAIL"),
    (_case("a6_lock_not_reacquired", "A6", _with(_a6(), "inherited_handles",
                                                 lock_reacquire={"attempted": True, "acquired": False,
                                                                 "released": False, "error": "IOException"})), "FAIL"),
    (_case("a6_lock_unmeasured", "A6", _with(_a6(), "inherited_handles", lock_reacquire=None)), "UNKNOWN"),
    (_a7_case("a7_pass", _a7()), "PASS"),
    (_a7_case("a7_switch_but_started", _a7_with(_a7(), "off_switch_before_start", "refusal", process_started=True)),
     "FAIL"),
    (_a7_case("a7_denied_read_wrote_run_dir", _a7_with(_a7(), "off_switch_denied_read", "refusal",
                                                       run_dir_created=True)), "FAIL"),
    (_a7_case("a7_refusal_exit_zero", _a7_with(_a7(), "reparse_ancestry", "refusal", exit_code=0)), "FAIL"),
    (_a7_case("a7_no_refusal_seen", _a7_with(_a7(), "disk_floor_before_start", "refusal", refusal_seen=None)),
     "UNKNOWN"),
    (_a7_case("a7_old_eight_without_user_variant", _a7(A7_OLD_EIGHT)), "UNKNOWN"),
    (_a7_case("a7_user_sid_but_started", _a7_with(_a7(), "user_sid_mismatch", "refusal", process_started=True)),
     "FAIL"),
    (_a7_case("a7_user_sid_no_refusal_seen", _a7_with(_a7(), "user_sid_mismatch", "refusal", refusal_seen=False)),
     "UNKNOWN"),
    # Lead's v6 decision: the pre-start attempt binding, no no-start inference, loop cases structural only.
    (_a7_case("a7_no_start_evidence", _a7_with(_a7(), "preloaded_type", "refusal", process_started=None)), "UNKNOWN"),
    (_case("a7_managed_launch_binding", "A7", _a7()), "UNKNOWN"),
    (_a7_case("a7_wrong_schema", _a7(), seen=_a7_seen(schema="wd.mind-shadow-a7-prestart-binding.v0"),
              expected=_a7_binding(schema="wd.mind-shadow-a7-prestart-binding.v0")), "UNKNOWN"),
    (_a7_case("a7_foreign_source_pin", _a7(), seen=_a7_seen(source_commit="6" * 40)), "FAIL"),
    (_a7_case("a7_foreign_controller", _a7(), seen=_a7_seen(controller_creation_utc="SYNTHETIC-OTHER-START")),
     "FAIL"),
    (_a7_case("a7_controller_pid_blank", _a7(), seen=_a7_seen(controller_pid=""),
              expected=_a7_binding(controller_pid="")), "UNKNOWN"),
    (_a7_case("a7_foreign_attempt", _a7_with(_a7(), "integrity_mismatch", "attempt",
                                             attempt_id="99999999-0000-4000-8000-000000000001")), "FAIL"),
    (_a7_case("a7_replayed_attempt", _a7_with(_a7(), "user_sid_mismatch", "attempt",
                                              attempt_id=_a7_attempts()["integrity_mismatch"])), "FAIL"),
    (_a7_case("a7_replayed_probe", _a7_with(_a7(), "user_sid_mismatch", "attempt", probe_pid="5001",
                                            probe_creation_utc="SYNTHETIC-PROBE-START-1")), "FAIL"),
    (_a7_case("a7_missing_attempt", {name: ({"refusal": sub["refusal"]} if name == "reparse_ancestry" else sub)
                                     for name, sub in _a7().items()}), "UNKNOWN"),
    (_a7_case("a7_attempt_extra_key", _a7_with(_a7(), "reparse_ancestry", "attempt", verdict="PASS")), "UNKNOWN"),
    (_a7_case("a7_probe_pid_zero", _a7_with(_a7(), "reparse_ancestry", "attempt", probe_pid="0")), "UNKNOWN"),
    (_a7_case("a7_duplicate_expected_attempts", _a7(), expected=_a7_binding(attempts=dict(
        _a7_attempts(), user_sid_mismatch=_a7_attempts()["integrity_mismatch"]))), "UNKNOWN"),
    (_a7_case("a7_expected_attempt_missing", _a7(), expected=_a7_binding(attempts={
        name: value for name, value in _a7_attempts().items() if name != "preloaded_type"})), "UNKNOWN"),
    (_a7_case("a7_structural_loop_cases_included", _a7(A7_SUBCASES)), "UNKNOWN"),
    # Lead's boundary probe of edf55c95: three false-PASS categories, RED first, positive controls kept.
    (_case("a1_deadline_negative_elapsed", "A1", _with(_a1(), "deadline", elapsed_seconds=-1)), "UNKNOWN"),
    (_case("a1_deadline_zero_elapsed", "A1", _with(_a1(), "deadline", elapsed_seconds=0)), "FAIL"),
    (_case("a1_deadline_before_bound", "A1", _with(_a1(), "deadline", elapsed_seconds=1799)), "FAIL"),
    (_case("a1_grandchild_before_bound", "A1", _with(_a1(), "pipe_holding_grandchild", elapsed_seconds=0)),
     "FAIL"),
    (_case("a1_switch_negative_elapsed", "A1", _with(_a1(), "off_switch_during_run", elapsed_seconds=-1)),
     "UNKNOWN"),
    (_case("a1_deadline_at_bound", "A1", _with(_a1(), "deadline", elapsed_seconds=1800)), "PASS"),
    (_case("a1_deadline_tolerance_edge", "A1", _with(_a1(), "deadline", elapsed_seconds=1830)), "PASS"),
    (_case("a1_early_switch_and_floor", "A1", _with(_with(_a1(), "off_switch_during_run", elapsed_seconds=5),
                                                    "disk_floor_hit", elapsed_seconds=0)), "PASS"),
    (_case("a1_bound_deadline_600", "A1", _a1_at(601), binding=_binding("A1", deadline="600"),
           expected=_binding("A1", deadline="600")), "PASS"),
    (_case("a1_beyond_bound_deadline_600", "A1", _a1_at(631), binding=_binding("A1", deadline="600"),
           expected=_binding("A1", deadline="600")), "FAIL"),
    (_case("a6_lock_error_with_true_flags", "A6", _with(_a6(), "inherited_handles", lock_reacquire={
        "attempted": True, "acquired": True, "released": True, "error": "synthetic-access-denied"})), "UNKNOWN"),
    (_case("a6_lock_error_mistyped", "A6", _with(_a6(), "inherited_handles", lock_reacquire={
        "attempted": True, "acquired": True, "released": True, "error": 5})), "UNKNOWN"),
    (_case("a6_blank_expected_and_seen_binding", "A6", _a6(), binding=dict(BLANK_A6), expected=dict(BLANK_A6)),
     "UNKNOWN"),
    (_case("a6_seen_blank_root_pid", "A6", _a6(), binding=dict(_binding("A6"), root_pid="")), "FAIL"),
    (_case("unknown_case_id", "A8", _a7()), "UNKNOWN"),
    ({"name": "run_a4_not_ready", "kind": "run", "verdicts": dict(ALL_PASS, A4="NOT_READY")}, "NOT_READY"),
    ({"name": "run_fake_a4_pass", "kind": "run", "verdicts": ALL_PASS}, "NOT_READY"),
    ({"name": "run_one_fail", "kind": "run", "verdicts": dict(ALL_PASS, A2="FAIL", A4="NOT_READY")}, "FAIL"),
    ({"name": "run_missing_case", "kind": "run", "verdicts": {k: v for k, v in ALL_PASS.items() if k != "A3"}},
     "UNKNOWN"),
    ({"name": "run_unknown_case", "kind": "run", "verdicts": dict(ALL_PASS, A6="UNKNOWN", A4="NOT_READY")},
     "NOT_READY"),
] + [
    (_case(f"a6_invalid_expected_{key}_{label}", "A6", _a6(), binding=dict(_binding("A6"), **{key: value}),
           expected=dict(_binding("A6"), **{key: value})), "UNKNOWN")
    for key, value, label in INVALID_A6
]


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic case evaluators need pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def verdicts(request, tmp_path_factory):
    _, executable = request.param
    folder = tmp_path_factory.mktemp("cases")
    host = folder / "host.ps1"
    host.write_text(HOST, encoding="utf-8")
    fixture = folder / "fixture.json"
    fixture.write_text(json.dumps([item for item, _ in FIXTURES]), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(HARNESS),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row["verdict"] for row in rows}


@pytest.mark.parametrize("item,expected", FIXTURES, ids=[item["name"] for item, _ in FIXTURES])
def test_case_and_run_verdicts_from_strict_fake_observations(verdicts, item, expected):
    assert verdicts[item["name"]] == expected


def test_every_fixture_has_exactly_one_verdict(verdicts):
    assert sorted(verdicts) == sorted(item["name"] for item, _ in FIXTURES)


# Checkpoint 3 (Lead A7 driver GO, 2026-10-22): the A7 pre-start refusal drivers. The host defines the harness
# functions only, makes every native and filesystem-changing cmdlet and the three real driver seams throw, and hands
# each driver fake seams: a preparation that only creates the planned run root in the test's temporary folder, a child
# runner that returns a recorded child result without starting anything, and a child lister. The refusal a fake child
# prints is rendered from the adapter's own throw template, so each plan is tied to the actual adapter refusal text.
ADAPTER = ROOT / "ops" / "windows" / "reboot" / "Start-WdMindShadowJob.ps1"
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="the A7 drivers plan Windows paths for the Windows-only "
                                  "adapter: non-Windows is an untested platform for these synthetic runs, not evidence")

A7_HOST = r"""
param([string] $Harness, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject', 'New-Item', 'Set-Acl', 'Get-Acl')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Harness, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'the harness does not parse' }
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
foreach ($name in @('Start-WdShadowA7Child', 'Get-WdShadowA7ChildProcesses', 'New-WdShadowA7CaseRoot')) {
    Set-Item -Path "function:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
function ConvertTo-HostValue($Value) {
    if ($null -eq $Value) { return $null }
    if ($Value -is [Management.Automation.PSCustomObject]) {
        $table = @{}
        foreach ($property in $Value.PSObject.Properties) {
            $table[$property.Name] = ConvertTo-HostValue $property.Value
        }
        return $table
    }
    if ($Value -is [array]) { return , @($Value | ForEach-Object { ConvertTo-HostValue $_ }) }
    return $Value
}
$rows = @()
foreach ($item in (ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture)))) {
    $entry = ConvertTo-HostValue $item
    $inputs = $entry['inputs']
    if ($inputs -is [hashtable] -and $inputs['ExpectedPeakAdditionalBytes'] -is [int]) {
        $inputs['ExpectedPeakAdditionalBytes'] = [long]$inputs['ExpectedPeakAdditionalBytes']
    }
    $row = [ordered]@{ name = $entry['name'] }
    try {
        if ($entry['kind'] -ceq 'plan') {
            $plan = Get-WdShadowA7Plan -Name $entry['sub'] -Inputs $inputs -CaseRoot $entry['case_root']
            if ($null -eq $plan) { $row.result = 'NO_PLAN' } else {
                $row.result = 'PLAN'; $row.plan = $plan
                $row.command = Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $entry['adapter']
            }
        } else {
            $child = $entry['child']; $kids = @($entry['children']); $made = [bool]$entry['create_entry']
            $runRoot = [string]$entry['run_root']
            $seen = [ordered]@{ prepared = 0; ran = 0; listed = 0 }
            $prepare = {
                param($Plan)
                $seen.prepared++
                [void][IO.Directory]::CreateDirectory([string]$Plan['run_root'])
            }.GetNewClosure()
            $run = {
                param([string] $HostPath, [string] $Command)
                $seen.ran++
                if ($made) { [void][IO.Directory]::CreateDirectory((Join-Path $runRoot 'SYNTHETIC-LAUNCH-DIR')) }
                return @{ pid = [int]$child['pid']; exit_code = $child['exit_code']
                    error_text = [string]$child['error_text'] }
            }.GetNewClosure()
            $list = { param([int] $ParentId) $seen.listed++; return , @($kids) }.GetNewClosure()
            $observation = Invoke-WdShadowA7Driver -Name $entry['sub'] -Inputs $inputs -CaseRoot $entry['case_root'] `
                -AdapterPath 'SYNTHETIC-ADAPTER.ps1' -HostPath 'SYNTHETIC-HOST.exe' -Prepare $prepare -RunChild $run `
                -ListChildren $list
            $row.seams = $seen
            if ($null -eq $observation) { $row.result = 'NO_PLAN' } else {
                $row.result = [string](Get-WdShadowSubcaseVerdict -CaseId 'A7' -Name $entry['sub'] `
                    -Observation $observation -Binding $null)
                $row.observation = $observation
            }
        }
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message }
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 6
"""

A7_REACHABLE = ("integrity_mismatch", "user_sid_mismatch", "preloaded_type", "off_switch_before_start",
                "off_switch_denied_read", "disk_floor_before_start", "reparse_ancestry")
A7_LOOP = ("lane_loop_zero_functions", "lane_loop_two_functions")
# A well-formed fake domain user SID (the adapter refuses any other form): the user variant derives its deviation from
# it. Synthetic, never a real account.
A7_FAKE_SID = "S-1-5-21-1000000001-1000000002-1000000003-1001"


def _adapter_template(needle):
    # The exact throw (or return) text of the adapter that holds the needle, once.
    texts = re.findall(r"(?:throw|return) [\"']([^\"']*" + re.escape(needle) + r"[^\"']*)[\"']",
                       ADAPTER.read_text(encoding="utf-8"))
    assert len(texts) == 1, (needle, texts)
    return texts[0]


def _a7_refusal(sub, case_root):
    if sub == "integrity_mismatch":
        return _adapter_template("the actual integrity level").replace("$integrity", "medium").replace(
            "$ApprovedIntegrityLevel", "high")
    if sub == "user_sid_mismatch":
        return _adapter_template("the token user SID is not the approved user SID")
    if sub == "preloaded_type":
        return _adapter_template("is already loaded in this process (R1)")
    if sub in ("off_switch_before_start", "off_switch_denied_read"):
        return _adapter_template("the operator off switch").replace("$offSwitchPath", case_root + "\\mind-shadow.off")
    if sub == "disk_floor_before_start":
        return _adapter_template("the disk budget is not met before the start").replace(
            "$shortfall", "C:\\ free=1, additional=9223372036854775807, floor=3221225472")
    reparse = _adapter_template("$current is a reparse point").replace("$current", case_root + "\\link")
    return _adapter_template("so its textual root would not measure").replace("$reparse", reparse)


def _a7_inputs(base, sid="SYNTHETIC-USER-SID"):
    return {"ExpectedSourceCommit": "SYNTHETIC-SOURCE-COMMIT", "ExpectedManifestSha256": "SYNTHETIC-MANIFEST-SHA",
            "ExpectedAdapterSha256": "SYNTHETIC-ADAPTER-SHA", "ApprovedRunRoot": base,
            "LiveDir": base + "-live", "ApprovedUserSid": sid, "ApprovedIntegrityLevel": "medium",
            "ExpectedPeakAdditionalBytes": 4096}


def _a7_run_root(sub, case_root):
    return case_root + "\\link\\run" if sub == "reparse_ancestry" else case_root


# Lead's v6 decision: an empty live-child listing is never no-start evidence, so a correct refusal without an owned-job
# witness stays UNKNOWN; only a seen start, a run-root entry or a zero exit decides (FAIL).
A7_DRIVER_FIXTURES = [(f"a7d_{sub}_no_start_witness", "UNKNOWN") for sub in A7_REACHABLE] + [
    ("a7d_off_switch_wrong_refusal", "UNKNOWN"),
    ("a7d_integrity_exit_zero", "FAIL"),
    ("a7d_disk_floor_run_dir_created", "FAIL"),
    ("a7d_reparse_child_left", "FAIL"),
    ("a7d_preloaded_no_exit", "UNKNOWN"),
    ("a7d_denied_read_two_markers", "UNKNOWN"),
    ("a7d_integrity_unmarked_refusal", "UNKNOWN"),
    ("a7d_user_sid_earlier_guard_refusal", "UNKNOWN"),
    ("a7d_user_sid_child_left", "FAIL"),
] + [(f"a7d_{sub}_no_plan", "NO_PLAN") for sub in A7_LOOP] + [
    ("a7d_user_sid_malformed", "ERROR"),
    ("a7d_unknown_subcase", "ERROR"),
    ("a7d_missing_input", "ERROR"),
    ("a7d_extra_input", "ERROR"),
    ("a7d_nonpositive_bound", "ERROR"),
    ("a7d_unknown_level", "ERROR"),
    ("a7d_control_character", "ERROR"),
]


def _a7_items(folder):
    items = {}

    def driver(name, sub, exit_code=2, text=None, created=False, children=(), inputs=None):
        case_root = str(folder / name)
        error = "WD-A7-REFUSAL " + _a7_refusal(sub, case_root) if text is None else text
        sid = A7_FAKE_SID if sub == "user_sid_mismatch" else "SYNTHETIC-USER-SID"
        items[name] = {"name": name, "kind": "driver", "sub": sub, "case_root": case_root,
                       "run_root": _a7_run_root(sub, case_root),
                       "inputs": inputs if inputs is not None else _a7_inputs(str(folder / (name + "-approved")), sid),
                       "child": {"pid": 4343, "exit_code": exit_code, "error_text": error},
                       "create_entry": created, "children": list(children)}

    for sub in A7_REACHABLE:
        driver(f"a7d_{sub}_no_start_witness", sub)
    driver("a7d_off_switch_wrong_refusal", "off_switch_before_start",
           text="WD-A7-REFUSAL " + _a7_refusal("disk_floor_before_start", ""))
    driver("a7d_integrity_exit_zero", "integrity_mismatch", exit_code=0)
    driver("a7d_disk_floor_run_dir_created", "disk_floor_before_start", created=True)
    driver("a7d_reparse_child_left", "reparse_ancestry", children=[5151])
    driver("a7d_preloaded_no_exit", "preloaded_type", exit_code=None)
    marked = "WD-A7-REFUSAL " + _a7_refusal("off_switch_denied_read", str(folder / "a7d_denied_read_two_markers"))
    driver("a7d_denied_read_two_markers", "off_switch_denied_read", text=marked + "\n" + marked)
    driver("a7d_integrity_unmarked_refusal", "integrity_mismatch",
           text=_a7_refusal("integrity_mismatch", ""))
    # The exact path reached: the integrity guard runs before the user SID check, so its refusal is not user evidence.
    driver("a7d_user_sid_earlier_guard_refusal", "user_sid_mismatch",
           text="WD-A7-REFUSAL " + _a7_refusal("integrity_mismatch", ""))
    driver("a7d_user_sid_child_left", "user_sid_mismatch", children=[6161])
    for sub in A7_LOOP:
        driver(f"a7d_{sub}_no_plan", sub)
    driver("a7d_unknown_subcase", "lane_loop_three_functions")
    base = _a7_inputs(str(folder / "approved"))
    driver("a7d_user_sid_malformed", "user_sid_mismatch", inputs=dict(base))
    driver("a7d_missing_input", "integrity_mismatch", inputs={k: v for k, v in base.items() if k != "LiveDir"})
    driver("a7d_extra_input", "integrity_mismatch", inputs=dict(base, Verdict="PASS"))
    driver("a7d_nonpositive_bound", "disk_floor_before_start", inputs=dict(base, ExpectedPeakAdditionalBytes=0))
    driver("a7d_unknown_level", "integrity_mismatch", inputs=dict(base, ApprovedIntegrityLevel="system"))
    driver("a7d_control_character", "off_switch_before_start", inputs=dict(base, ApprovedUserSid="S-1\nS-2"))
    for sub in A7_REACHABLE + A7_LOOP:
        name = f"a7p_{sub}"
        sid = A7_FAKE_SID if sub == "user_sid_mismatch" else "SYNTHETIC-O'SID"
        items[name] = {"name": name, "kind": "plan", "sub": sub, "case_root": str(folder / name),
                       "inputs": dict(base, ApprovedUserSid=sid),
                       "adapter": str(folder / "adapter's.ps1")}
    return items


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic A7 drivers need pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def a7_rows(request, tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the A7 drivers plan Windows paths for the Windows-only adapter: non-Windows is untested, "
                    "not evidence")
    _, executable = request.param
    folder = tmp_path_factory.mktemp("a7")
    items = _a7_items(folder)
    host = folder / "a7_host.ps1"
    host.write_text(A7_HOST, encoding="utf-8")
    fixture = folder / "a7_fixture.json"
    fixture.write_text(json.dumps(list(items.values())), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(HARNESS),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row for row in rows}, items


@WINDOWS_ONLY
@pytest.mark.parametrize("name,expected", A7_DRIVER_FIXTURES, ids=[name for name, _ in A7_DRIVER_FIXTURES])
def test_a7_driver_observations_come_from_the_child_and_the_run_root(a7_rows, name, expected):
    rows, _ = a7_rows
    row = rows[name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: "), row
        return
    seams = row["seams"]
    if expected == "NO_PLAN":
        assert seams == {"prepared": 0, "ran": 0, "listed": 0}
        return
    assert seams == {"prepared": 1, "ran": 1, "listed": 1}
    assert sorted(row["observation"]) == ["exit_code", "process_started", "refusal_seen", "run_dir_created"]
    # A seen live child is a start; no listed child is no evidence ($null), never a no-start observation.
    assert row["observation"]["process_started"] is (True if a7_rows[1][name]["children"] else None)


@WINDOWS_ONLY
@pytest.mark.parametrize("sub", A7_REACHABLE + A7_LOOP)
def test_a7_plan_is_the_approved_inputs_with_exactly_one_deviation(a7_rows, sub):
    rows, items = a7_rows
    row, item = rows[f"a7p_{sub}"], items[f"a7p_{sub}"]
    if sub in A7_LOOP:
        # Unreachable in an actual run: the adapter reads the fixed reboot state pointer and bundle store and checks
        # the loop bytes against the manifest pin before it counts the functions.
        adapter = ADAPTER.read_text(encoding="utf-8")
        assert "$StatePath = 'C:\\Python\\WD_REBOOT_STATE_CURRENT.json'" in adapter
        assert "$BundleStore = 'C:\\Python\\wd-reboot-bundles'" in adapter
        assert adapter.index("the lane loop bytes do not match the manifest") < adapter.index(
            "expected one Initialize-WdNativeTurnType, found")
        assert row["result"] == "NO_PLAN", row
        return
    plan = row["plan"]
    assert sorted(plan) == ["inputs", "name", "preload", "prepare", "refusal", "run_root"]
    assert plan["name"] == sub and plan["run_root"] == _a7_run_root(sub, item["case_root"])
    assert plan["inputs"]["ApprovedRunRoot"] == plan["run_root"]
    changed = sorted(k for k in item["inputs"] if k != "ApprovedRunRoot" and plan["inputs"][k] != item["inputs"][k])
    want = {"integrity_mismatch": ["ApprovedIntegrityLevel"], "user_sid_mismatch": ["ApprovedUserSid"],
            "disk_floor_before_start": ["ExpectedPeakAdditionalBytes"]}
    assert changed == want.get(sub, []), changed
    if sub == "user_sid_mismatch":
        # The foreign SID must pass the adapter's own form check, so the refusal reached is the T1 user check.
        form = re.search(r"-Name 'ApprovedUserSid' -Value \$ApprovedUserSid -Pattern '([^']+)'",
                         ADAPTER.read_text(encoding="utf-8")).group(1)
        assert re.fullmatch(form.replace("\\d", "[0-9]"), plan["inputs"]["ApprovedUserSid"])
    assert sorted(plan["inputs"]) == sorted(item["inputs"])
    want_prepare = {"off_switch_before_start": "off_switch", "off_switch_denied_read": "off_switch_denied",
                    "reparse_ancestry": "junction"}
    assert plan["prepare"] == want_prepare.get(sub, "none")
    assert plan["preload"] == ("lane_loop_type" if sub == "preloaded_type" else "none")
    assert re.fullmatch(plan["refusal"].replace("\\z", "$").replace("(?-i)", ""),
                        "WD-A7-REFUSAL " + _a7_refusal(sub, item["case_root"]))


@WINDOWS_ONLY
@pytest.mark.parametrize("sub", A7_REACHABLE)
def test_a7_child_command_runs_the_adapter_once_with_literal_inputs(a7_rows, sub):
    rows, item = a7_rows[0][f"a7p_{sub}"], a7_rows[1][f"a7p_{sub}"]
    command = rows["command"]
    adapter = item["adapter"].replace("'", "''")
    assert command.count("& '" + adapter + "'") == 1
    sid = rows["plan"]["inputs"]["ApprovedUserSid"] if sub == "user_sid_mismatch" else "SYNTHETIC-O'SID"
    assert "-ApprovedUserSid '" + sid.replace("'", "''") + "'" in command
    assert command.index("try {") < command.index("& '" + adapter + "'") < command.index("} catch {")
    assert "[Console]::Error.WriteLine('WD-A7-REFUSAL ' +" in command and "exit 2" in command
    preload = "Initialize-WdNativeTurnType" in command
    assert preload == (sub == "preloaded_type")
    if preload:
        assert command.index("Initialize-WdNativeTurnType") < command.index("try {")
        assert "C:\\Python\\wd-reboot-bundles\\SYNTHETIC-SOURCE-COMMIT\\Invoke-WdLaneTurnLoop.ps1" in command
    for word in ("Add-Type", "Start-Process", "Invoke-Expression", "Stop-Process"):
        assert word not in command, word
