"""Synthetic contract of the A1-A7 case evaluators in the opt-in native acceptance harness.

The harness is never executed here. A test host parses it with the PowerShell parser, defines ONLY its top-level
functions (the script body with its refusal and every native step stays unrun), replaces the process, job and
expression cmdlets with mocks that throw on any call, and feeds the pure case evaluators strict FAKE observations.
A verdict here is synthetic author proof of the refusal, outcome and record logic, never native acceptance: native
A1-A7 stays NOT_RUN until an approved Medium executor runs the harness. Case contract: RCO1 (A1-A7, proposal v2 as
locked by Lead), RCO2 (G1-G4, the member witness and the A1 pipe-holding grandchild), Lead (the guard cases, the A4
before-resume rule and this Source-preparation GO); fable-5 wrote this test and the evaluators.
"""

import json
import os
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
A7_SUBCASES = ("integrity_mismatch", "preloaded_type", "lane_loop_zero_functions", "lane_loop_two_functions",
               "off_switch_before_start", "off_switch_denied_read", "disk_floor_before_start", "reparse_ancestry")


def _a7():
    return {name: dict(REFUSED) for name in A7_SUBCASES}


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
    (_case("a7_pass", "A7", _a7()), "PASS"),
    (_case("a7_switch_but_started", "A7", _with(_a7(), "off_switch_before_start", process_started=True)), "FAIL"),
    (_case("a7_denied_read_wrote_run_dir", "A7", _with(_a7(), "off_switch_denied_read", run_dir_created=True)),
     "FAIL"),
    (_case("a7_refusal_exit_zero", "A7", _with(_a7(), "reparse_ancestry", exit_code=0)), "FAIL"),
    (_case("a7_no_refusal_seen", "A7", _with(_a7(), "disk_floor_before_start", refusal_seen=None)), "UNKNOWN"),
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
