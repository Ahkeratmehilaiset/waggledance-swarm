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

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "windows" / "wd_mind_shadow_job_native_acceptance.ps1"
HELPER_LAUNCH = ROOT / "tests" / "windows" / "wd_mind_shadow_owned_member_launch.ps1"

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


LT_SCHEMA = "wd.mind-shadow-lifetime-subcase-binding.v1"
LT_REQUIRED = {"A1": ("deadline", "early_root_exit_orphan", "pipe_holding_grandchild", "off_switch_during_run",
                      "disk_floor_hit", "single_confirmed_stop"),
               "A3": ("outer_no_limits", "outer_kill_on_close", "planned_launch_context")}


def _lt_guid(index):
    return f"1a1a1a1a-0000-4000-8000-{index:012d}"  # synthetic, never a real launch id


def _lt_pins(case_id, harness="SYNTHETIC-HARNESS-RUN", deadline="1800"):
    return {"schema": LT_SCHEMA, "harness_run_id": harness, "case_id": case_id, "controller_pid": "4141",
            "controller_creation_utc": "SYNTHETIC-CONTROLLER-START", "source_commit": SYNTHETIC_COMMIT,
            "manifest_sha256": SYNTHETIC_MANIFEST, "adapter_sha256": SYNTHETIC_ADAPTER, "deadline_seconds": deadline}


def _per_subcase(case_id, subcases, seen, expected):
    # Lead's v8 decision: A1 and A3 carry a versioned per-sub-case binding, one launch per sub-case. The old-style
    # fixture bindings are mapped onto it: the observed launch is the expected one unless the fixture names a foreign
    # launch, each root id is distinct (the old root id plus the sub-case index) and missing old fields stay missing.
    names = LT_REQUIRED[case_id]
    want = dict(_lt_pins(case_id, expected.get("harness_run_id"), expected.get("deadline_seconds")),
                launches={name: _lt_guid(i) for i, name in enumerate(names)})
    pins = {key: value for key, value in want.items() if key != "launches"}
    for key in ("harness_run_id", "case_id", "deadline_seconds"):
        if key in seen:
            pins[key] = seen[key]
        else:
            pins.pop(key)
    default = _binding(case_id)["launch_id"]
    wrapped = {}
    for index, (name, observation) in enumerate(subcases.items()):
        launch = {}
        if "launch_id" in seen:
            launch["launch_id"] = want["launches"].get(name, _lt_guid(90 + index)) if seen["launch_id"] == default \
                else seen["launch_id"]
        if "root_pid" in seen:
            launch["root_pid"] = str(int(seen["root_pid"]) + index) if seen["root_pid"].isdigit() else seen["root_pid"]
        if "root_creation_utc" in seen:
            launch["root_creation_utc"] = seen["root_creation_utc"]
        wrapped[name] = {"launch": launch, "observation": observation}
    return want, {"binding": pins, "subcases": wrapped}


def _case(name, case_id, subcases, binding=None, extra=None, expected=None, legacy=False):
    seen = binding if binding is not None else _binding(case_id)
    want = expected if expected is not None else _binding(case_id)
    if case_id in LT_REQUIRED and not legacy:
        want, observation = _per_subcase(case_id, subcases, seen, want)
    else:
        observation = {"binding": seen, "subcases": subcases}
    if extra:
        observation.update(extra)
    return {"name": name, "kind": "case", "case_id": case_id, "binding": want, "observation": observation}


def _ps_case(name, case_id, subcases, change):
    item = _case(name, case_id, subcases)
    change(item["binding"], item["observation"])
    return item


def _set(path, value):
    def change(want, seen):
        target = {"want": want, "seen": seen}[path[0]]
        for key in path[1:-1]:
            target = target[key]
        target[path[-1]] = value
    return change


def _reuse_root(want, seen):
    first, second = list(seen["subcases"].values())[:2]
    second["launch"]["root_pid"] = first["launch"]["root_pid"]


def _swap_launches(want, seen):
    first, second = list(seen["subcases"].values())[:2]
    first["launch"]["launch_id"], second["launch"]["launch_id"] = (second["launch"]["launch_id"],
                                                                  first["launch"]["launch_id"])


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


# Lead's v8 decision: the versioned A1/A3 per-sub-case binding (one unique launch per preregistered sub-case, its own
# measured root, all bound to the same harness run, controller and Source, manifest and adapter identity).
FIXTURES += [
    (_case("a1_legacy_single_binding", "A1", _a1(), legacy=True), "UNKNOWN"),
    (_case("a3_legacy_single_binding", "A3", _a3(), legacy=True), "UNKNOWN"),
    (_ps_case("a1_reused_root", "A1", _a1(), _reuse_root), "FAIL"),
    (_ps_case("a3_reused_root", "A3", _a3(), _reuse_root), "FAIL"),
    (_ps_case("a1_swapped_launches", "A1", _a1(), _swap_launches), "FAIL"),
    (_ps_case("a1_duplicate_expected_launch", "A1", _a1(),
              _set(("want", "launches", "disk_floor_hit"), _lt_guid(0))), "UNKNOWN"),
    (_ps_case("a1_expected_extra_launch", "A1", _a1(), _set(("want", "launches", "bonus"), _lt_guid(77))), "UNKNOWN"),
    (_ps_case("a1_foreign_controller", "A1", _a1(), _set(("seen", "binding", "controller_pid"), "4242")), "FAIL"),
    (_ps_case("a3_foreign_source", "A3", _a3(), _set(("seen", "binding", "source_commit"), "7" * 40)), "FAIL"),
    (_ps_case("a3_wrong_schema", "A3", _a3(), _set(("want", "schema"), "wd.mind-shadow-lifetime-subcase-binding.v0")),
     "UNKNOWN"),
    (_ps_case("a1_launch_extra_key", "A1", _a1(), _set(("seen", "subcases", "deadline", "launch", "verdict"), "PASS")),
     "UNKNOWN"),
    (_ps_case("a1_root_pid_zero", "A1", _a1(), _set(("seen", "subcases", "deadline", "launch", "root_pid"), "0")),
     "UNKNOWN"),
    (_ps_case("a3_controller_blank", "A3", _a3(), _set(("want", "controller_pid"), "")), "UNKNOWN"),
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
                $row.command = Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $entry['adapter'] `
                    -ExpectedLoopSha256 ('AB' * 32)
                $row.nopin = $(try { [void](Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $entry['adapter'])
                    'RAN' } catch { [string]$_.Exception.Message })
                $row.lowpin = $(try { [void](Get-WdShadowA7ChildCommand -Plan $plan `
                    -AdapterPath $entry['adapter'] -ExpectedLoopSha256 ('ab' * 32)); 'RAN' } catch {
                    [string]$_.Exception.Message })
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
                -ListChildren $list -ExpectedLoopSha256 ('AB' * 32)
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
        pin = "'" + "AB" * 32 + "'"
        assert rows["nopin"].startswith("refused: ") and rows["lowpin"].startswith("refused: "), rows
        assert command.count("ReadAllBytes(") == 1 and command.count("ParseInput(") == 1, command
        assert "ParseFile(" not in command and "UTF8Encoding($false, $true)" in command, command
        assert (command.index("ReadAllBytes(") < command.index(pin) < command.index("exit 4")
                < command.index("ParseInput(") < command.rindex("Initialize-WdNativeTurnType")), command
    else:
        assert rows["nopin"] == "RAN" and "ReadAllBytes(" not in command, rows
    for word in ("Add-Type", "Start-Process", "Invoke-Expression", "Stop-Process"):
        assert word not in command, word


# Checkpoint 4 (Lead's v7 GO, 2026-10-22T20:06:21Z): the A1, A2, A3, A5 and A6 lifetime drivers. The host defines
# the harness functions only, makes every native cmdlet and the real driver seams throw, and runs each driver body
# on a strict fake owned job: a fake managed root (its id, the job counts, Wait, Stop, ExitCode, Dispose) on a fake
# clock, and fake members that hold real CreateNew witness files with FileShare.None in the test's temporary
# .codex-audit folder. The observation is the driver's own: its loop, its single Stop, its witness reads and its lock
# attempt. A verdict here is synthetic author proof of the driver sequencing, never native evidence.
LIFETIME_HOST = r"""
param([string] $Harness, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject', 'New-Item', 'Set-Acl', 'Get-Acl',
        'Start-Sleep')) {
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
function Open-FakeHeld([string] $Path, [string] $Line) {
    $stream = New-Object IO.FileStream($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    if ($Line) { $bytes = [Text.Encoding]::ASCII.GetBytes($Line + "`n"); $stream.Write($bytes, 0, $bytes.Length) }
    $stream.Flush()
    return $stream
}
function Close-FakeMembers($State) {
    foreach ($member in @($State.streams.Keys)) {
        if (@($State.held) -cnotcontains $member) { $State.streams[$member].Dispose(); $State.streams.Remove($member) }
    }
    if ($null -ne $State.lock -and -not $State.lock_held) { $State.lock.Dispose(); $State.lock = $null }
}
function New-FakeRun($fake, [string] $Launch) {
    $state = @{ now = 0.0; streams = @{}; lock = $null; exited = $false; active = 1; created = 0; stops = 0
        kills = 0; waits = 0; disposed = 0; contexts = 0; root_exit_at = $fake['root_exit_at']
        active_after_root_exit = [int]$fake['active_after_root_exit']
        active_after_stop = [int]$fake['active_after_stop']
        stop_throws = [bool]$fake['stop_throws']; held = @($fake['held']); started = @($fake['started'])
        foreign = @($fake['foreign']); late = [string]$fake['late']; total = [int]$fake['total']
        free = [long]$fake['free']; inherited = @($fake['inherited']); lock_held = [bool]$fake['lock_held']
        create_throws = [bool]$fake['create_throws']; launch = $Launch; dir = ''; pid = [int]$fake['pid']
        creation = [string]$fake['creation'] }
    $managed = New-Object PSObject
    Add-Member -InputObject $managed -MemberType NoteProperty -Name State -Value $state
    Add-Member -InputObject $managed -MemberType ScriptProperty -Name Pid -Value { [int]$this.State.pid }
    Add-Member -InputObject $managed -MemberType ScriptProperty -Name ActiveProcesses -Value { [int]$this.State.active }
    Add-Member -InputObject $managed -MemberType ScriptProperty -Name TotalProcesses -Value { [int]$this.State.total }
    Add-Member -InputObject $managed -MemberType ScriptProperty -Name ExitCode -Value {
        if (-not $this.State.exited) { throw 'synthetic: the root has not exited' }; 0 }
    Add-Member -InputObject $managed -MemberType ScriptMethod -Name Wait -Value {
        param([int] $Milliseconds)
        $s = $this.State; $s.waits++
        if ($s.exited) { return $true }
        $at = $s.root_exit_at
        if ($null -ne $at -and $s.now + $Milliseconds / 1000.0 -ge [double]$at) {
            $s.now = [double]$at; $s.exited = $true; $s.active = $s.active_after_root_exit
            if ($s.streams.ContainsKey('root') -and @($s.held) -cnotcontains 'root') {
                $s.streams['root'].Dispose(); $s.streams.Remove('root')
            }
            return $true
        }
        $s.now += $Milliseconds / 1000.0
        return $false
    }
    Add-Member -InputObject $managed -MemberType ScriptMethod -Name Stop -Value {
        $s = $this.State; $s.stops++
        if ($s.stop_throws) { throw 'synthetic stop failure' }
        if ($s.late -cne 'none') {
            $s.streams['late_child'] = Open-FakeHeld (Join-Path $s.dir 'late_child.witness') ($s.launch + ' late_child')
            if ($s.late -ceq 'survives') { $s.held = @($s.held) + @('late_child') }
        }
        Close-FakeMembers $s
        $s.active = $s.active_after_stop
        if ($s.active -eq 0) { $s.exited = $true }
    }
    Add-Member -InputObject $managed -MemberType ScriptMethod -Name Dispose -Value { $this.State.disposed++ }
    $seams = @{
        NewManaged = {
            param($Plan)
            $state.created++
            $state.dir = [string]$Plan['witness_directory']
            if ($state.create_throws) { throw 'synthetic: the job refused the start' }
            foreach ($member in $state.started) {
                $launch = $state.launch
                if ($state.foreign -ccontains $member) { $launch = '99999999-9999-4999-8999-999999999999' }
                $path = Join-Path $state.dir ($member + '.witness')
                $state.streams[$member] = Open-FakeHeld $path ($launch + ' ' + $member)
            }
            if ($Plan['case_id'] -ceq 'A6') { $state.lock = Open-FakeHeld ([string]$Plan['lock_path']) '' }
            return $managed
        }.GetNewClosure()
        RootCreation = { param($Managed) return [string]$state.creation }.GetNewClosure()
        Clock = { return [double]$state.now }.GetNewClosure()
        Sleep = { param([int] $Milliseconds) $state.now += $Milliseconds / 1000.0 }.GetNewClosure()
        FreeBytes = { param([string] $Path) return [long]$state.free }.GetNewClosure()
        Context = { param($Plan) $state.contexts++; return [string]$Plan['name'] }.GetNewClosure()
        KillController = {
            param($Managed)
            $state.kills++; Close-FakeMembers $state; $state.active = $state.active_after_stop
        }.GetNewClosure()
        InheritedHandles = { param($Managed) return @($state.inherited) }.GetNewClosure()
    }
    return @{ seams = $seams; state = $state }
}
$rows = @()
foreach ($item in (ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture)))) {
    $entry = ConvertTo-HostValue $item
    $made = New-FakeRun $entry['fake'] ([string]$entry['run']['launch_id'])
    $state = $made['state']
    $seams = $made['seams']
    if ($entry.ContainsKey('extra_seam')) { $seams['Verdict'] = { 'PASS' } }
    if ($entry.ContainsKey('real_seams')) { $seams = Get-WdShadowLifetimeSeams }
    $row = [ordered]@{ name = $entry['name'] }
    try {
        if ($entry['kind'] -ceq 'compose') {
            # One fake job and one launch per sub-case run; the case observation is composed from all of them.
            $results = @()
            foreach ($one in @($entry['runs'])) {
                $each = New-FakeRun $one['fake'] ([string]$one['launch'])
                $run = @{} + $entry['run']
                $run['launch_id'] = [string]$one['launch']
                try {
                    $results += , (Invoke-WdShadowLifetimeDriver -CaseId $entry['case_id'] -Name $one['sub'] `
                        -Run $run -Seams $each['seams'])
                } finally {
                    foreach ($stream in @($each['state'].streams.Values)) { $stream.Dispose() }
                    if ($null -ne $each['state'].lock) { $each['state'].lock.Dispose() }
                }
            }
            $case = ConvertTo-WdShadowLifetimeCaseObservation -CaseId $entry['case_id'] -Results $results `
                -Pins $entry['pins']
            $row.result = [string](Get-WdShadowCaseVerdict -CaseId $entry['case_id'] -Binding $entry['expected'] `
                -Observation $case)
        } else {
            $result = Invoke-WdShadowLifetimeDriver -CaseId $entry['case_id'] -Name $entry['sub'] -Run $entry['run'] `
                -Seams $seams
            $row.result = [string](Get-WdShadowSubcaseVerdict -CaseId $entry['case_id'] -Name $entry['sub'] `
                -Observation $result['observation'] -Binding $result['binding'])
            $row.observation = $result['observation']
            $row.binding = $result['binding']
        }
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message }
    foreach ($stream in @($state.streams.Values)) { $stream.Dispose() }
    if ($null -ne $state.lock) { $state.lock.Dispose() }
    $row.seams = [ordered]@{ created = $state.created; stops = $state.stops; kills = $state.kills
        disposed = $state.disposed; contexts = $state.contexts }
    $row.files = @()
    if ($state.dir -and [IO.Directory]::Exists($state.dir)) {
        $row.files = @([IO.Directory]::GetFileSystemEntries($state.dir) |
            ForEach-Object { [IO.Path]::GetFileName($_) } | Sort-Object)
    }
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 8
"""

LT_LAUNCH = "7a7a7a7a-0000-4000-8000-000000000071"  # synthetic, never a real launch id


def _lt_run(folder, name, deadline="60", audit=True):
    root = folder / (".codex-audit" if audit else "elsewhere") / name
    return {"harness_run_id": "SYNTHETIC-HARNESS-RUN", "launch_id": LT_LAUNCH, "deadline_seconds": deadline,
            "witness_root": str(root), "host_path": "C:\\SYNTHETIC\\pwsh.exe",
            "member_path": "C:\\SYNTHETIC\\wd_mind_shadow_acceptance_member.ps1"}


def _lt_fake(**change):
    fake = {"root_exit_at": None, "active_after_root_exit": 0, "active_after_stop": 0, "stop_throws": False,
            "held": [], "started": ["root"], "foreign": [], "late": "none", "total": 1, "free": 1 << 40,
            "inherited": [], "lock_held": False, "create_throws": False, "pid": 4242,
            "creation": "SYNTHETIC-ROOT-CREATION-UTC"}
    fake.update(change)
    return fake


LIFETIME_FIXTURES = [
    ("lt_a1_deadline", "A1", "deadline", {}, "PASS"),
    ("lt_a1_deadline_unconfirmed", "A1", "deadline", {"active_after_stop": 1}, "FAIL"),
    ("lt_a1_deadline_stop_throws", "A1", "deadline", {"stop_throws": True, "active_after_stop": 1}, "FAIL"),
    ("lt_a1_deadline_completed_early", "A1", "deadline", {"root_exit_at": 10}, "FAIL"),
    ("lt_a1_orphan", "A1", "early_root_exit_orphan",
     {"started": ["root", "orphan"], "root_exit_at": 10, "active_after_root_exit": 1}, "PASS"),
    ("lt_a1_orphan_never_started", "A1", "early_root_exit_orphan",
     {"root_exit_at": 10, "active_after_root_exit": 1}, "UNKNOWN"),
    ("lt_a1_pipe_grandchild", "A1", "pipe_holding_grandchild", {"started": ["root", "child", "pipe_grandchild"]},
     "PASS"),
    # PR1918 bot P2: every expected member must have started, been released and be bound to this launch; a missing,
    # still-held or foreign child or pipe grandchild leaves the sub-case without evidence, never PASS.
    ("lt_a1_pipe_child_never_started", "A1", "pipe_holding_grandchild", {"started": ["root", "pipe_grandchild"]},
     "UNKNOWN"),
    ("lt_a1_pipe_grandchild_never_started", "A1", "pipe_holding_grandchild", {"started": ["root", "child"]},
     "UNKNOWN"),
    ("lt_a1_pipe_grandchild_held", "A1", "pipe_holding_grandchild",
     {"started": ["root", "child", "pipe_grandchild"], "held": ["pipe_grandchild"]}, "UNKNOWN"),
    ("lt_a1_pipe_child_foreign", "A1", "pipe_holding_grandchild",
     {"started": ["root", "child", "pipe_grandchild"], "foreign": ["child"]}, "UNKNOWN"),
    ("lt_a1_off_switch", "A1", "off_switch_during_run", {}, "PASS"),
    ("lt_a1_disk_floor", "A1", "disk_floor_hit", {}, "PASS"),
    ("lt_a1_single_stop", "A1", "single_confirmed_stop", {}, "PASS"),
    ("lt_a1_single_stop_unconfirmed", "A1", "single_confirmed_stop", {"active_after_stop": 1}, "FAIL"),
    ("lt_a2_clean", "A2", "survivors", {"started": ["root", "child", "grandchild"], "late": "dies"}, "PASS"),
    # Tools PR1918: released registered witnesses do not exhaust the owned job. An unknown surviving member or
    # invalid accounting cannot become no-survivor PASS just because none of those witness handles is held.
    ("lt_a2_positive_active_without_held_witness", "A2", "survivors",
     {"started": ["root", "child", "grandchild"], "late": "dies", "active_after_stop": 1}, "UNKNOWN"),
    ("lt_a2_negative_active_without_held_witness", "A2", "survivors",
     {"started": ["root", "child", "grandchild"], "late": "dies", "active_after_stop": -1}, "UNKNOWN"),
    ("lt_a2_stop_throws_without_held_witness", "A2", "survivors",
     {"started": ["root", "child", "grandchild"], "late": "dies", "active_after_stop": 1,
      "stop_throws": True}, "UNKNOWN"),
    ("lt_a2_independent_survivor", "A2", "survivors",
     {"started": ["root", "child", "grandchild"], "late": "dies", "held": ["grandchild"]}, "FAIL"),
    ("lt_a2_live_descendant", "A2", "survivors",
     {"started": ["root", "child", "grandchild"], "late": "dies", "held": ["child"], "active_after_stop": 1}, "FAIL"),
    ("lt_a2_late_child_survived", "A2", "survivors", {"started": ["root", "child", "grandchild"], "late": "survives"},
     "FAIL"),
    ("lt_a2_late_child_never_started", "A2", "survivors", {"started": ["root", "child", "grandchild"]}, "UNKNOWN"),
    ("lt_a2_grandchild_never_started", "A2", "survivors", {"started": ["root", "child"], "late": "dies"}, "UNKNOWN"),
    ("lt_a3_outer_no_limits", "A3", "outer_no_limits", {}, "PASS"),
    ("lt_a3_kill_on_close", "A3", "outer_kill_on_close", {}, "PASS"),
    ("lt_a3_planned_context", "A3", "planned_launch_context", {}, "PASS"),
    ("lt_a3_refused_unwitnessed", "A3", "outer_no_limits", {"create_throws": True}, "UNKNOWN"),
    ("lt_a3_started_unassigned", "A3", "planned_launch_context", {"total": 0}, "FAIL"),
    ("lt_a3_root_never_started", "A3", "outer_kill_on_close", {"started": []}, "UNKNOWN"),
    ("lt_a5_controller_death", "A5", "controller_death", {"started": ["root", "child", "grandchild"]}, "PASS"),
    ("lt_a5_member_survives", "A5", "controller_death",
     {"started": ["root", "child", "grandchild"], "held": ["grandchild"]}, "FAIL"),
    ("lt_a5_foreign_witness", "A5", "controller_death",
     {"started": ["root", "child", "grandchild"], "foreign": ["child"]}, "FAIL"),
    ("lt_a5_member_never_started", "A5", "controller_death", {"started": ["root", "child"]}, "FAIL"),
    ("lt_a6_clean", "A6", "inherited_handles", {}, "PASS"),
    ("lt_a6_root_never_started", "A6", "inherited_handles", {"started": []}, "UNKNOWN"),
    ("lt_a6_root_held", "A6", "inherited_handles", {"held": ["root"]}, "UNKNOWN"),
    ("lt_a6_root_foreign", "A6", "inherited_handles", {"foreign": ["root"]}, "UNKNOWN"),
    ("lt_a6_inherited_handle", "A6", "inherited_handles", {"inherited": ["SYNTHETIC-HANDLE File C:\\x"]}, "FAIL"),
    ("lt_a6_lock_still_held", "A6", "inherited_handles", {"lock_held": True}, "FAIL"),
    ("lt_a6_stop_unconfirmed", "A6", "inherited_handles", {"active_after_stop": 1}, "UNKNOWN"),
]
LIFETIME_ERRORS = [
    ("lt_real_seams_refuse", "A2", "survivors", {"real_seams": True}),
    ("lt_unknown_subcase", "A1", "before_resume", {}),
    ("lt_a4_has_no_driver", "A4", "publication_kill", {}),
    ("lt_a7_has_no_lifetime_driver", "A7", "integrity_mismatch", {}),
    ("lt_extra_seam", "A6", "inherited_handles", {"extra_seam": True}),
    ("lt_outside_codex_audit", "A6", "inherited_handles", {"audit": False}),
    ("lt_witness_not_fresh", "A6", "inherited_handles", {"not_fresh": True}),
    ("lt_bad_deadline", "A1", "deadline", {"deadline": "0"}),
]
LT_A1_RUNS = [("deadline", {}), ("early_root_exit_orphan", {"started": ["root", "orphan"], "root_exit_at": 10,
                                                             "active_after_root_exit": 1}),
              ("pipe_holding_grandchild", {"started": ["root", "child", "pipe_grandchild"]}),
              ("off_switch_during_run", {}), ("disk_floor_hit", {}), ("single_confirmed_stop", {})]
LT_A3_RUNS = [("outer_no_limits", {}), ("outer_kill_on_close", {}), ("planned_launch_context", {})]


def _distinct(runs, first):
    return [(sub, dict(fake, pid=first + index, creation=f"SYNTHETIC-ROOT-CREATION-{first + index}"))
            for index, (sub, fake) in enumerate(runs)]


LIFETIME_COMPOSE = [
    ("ltc_a2", "A2", [("survivors", {"started": ["root", "child", "grandchild"], "late": "dies"})], "PASS"),
    ("ltc_a5", "A5", [("controller_death", {"started": ["root", "child", "grandchild"]})], "PASS"),
    ("ltc_a6", "A6", [("inherited_handles", {})], "PASS"),
    ("ltc_a6_foreign_root", "A6", [("inherited_handles", {})], "FAIL"),
    ("ltc_a1", "A1", _distinct(LT_A1_RUNS, 5001), "PASS"),
    ("ltc_a3", "A3", _distinct(LT_A3_RUNS, 5011), "PASS"),
    ("ltc_a3_reused_root", "A3", LT_A3_RUNS, "FAIL"),
    ("ltc_a1_missing_subcase", "A1", _distinct(LT_A1_RUNS[:5], 5021), "UNKNOWN"),
    ("ltc_a3_foreign_launch", "A3", _distinct(LT_A3_RUNS, 5031), "FAIL"),
    ("ltc_a3_duplicate_subcase", "A3", _distinct(LT_A3_RUNS + LT_A3_RUNS[:1], 5041), "ERROR"),
    ("ltc_a3_foreign_harness", "A3", _distinct(LT_A3_RUNS, 5051), "ERROR"),
]


def _lt_items(folder):
    items = []
    for name, case_id, sub, fake, _ in LIFETIME_FIXTURES:
        items.append({"name": name, "kind": "driver", "case_id": case_id, "sub": sub, "run": _lt_run(folder, name),
                      "fake": _lt_fake(**fake)})
    for name, case_id, sub, change in LIFETIME_ERRORS:
        item = {"name": name, "kind": "driver", "case_id": case_id, "sub": sub, "fake": _lt_fake(),
                "run": _lt_run(folder, name, deadline=change.get("deadline", "60"), audit=change.get("audit", True))}
        for key in ("real_seams", "extra_seam"):
            if key in change:
                item[key] = True
        if change.get("not_fresh"):
            (Path(item["run"]["witness_root"]) / LT_LAUNCH).mkdir(parents=True)
        items.append(item)
    for name, case_id, runs, _ in LIFETIME_COMPOSE:
        pins = None
        if case_id in LT_REQUIRED:
            # One unique launch per sub-case (the expected binding's), each run on its own fake job and root.
            pins = _lt_pins(case_id, deadline="60")
            expected = dict(pins, launches={sub: _lt_guid(i) for i, sub in enumerate(LT_REQUIRED[case_id])})
            launches = [expected["launches"][sub] for sub, _ in runs]
            if name.endswith("foreign_launch"):
                launches[1] = _lt_guid(66)
            if name.endswith("foreign_harness"):
                pins = dict(pins, harness_run_id="SYNTHETIC-OTHER-RUN")
        else:
            expected = _binding(case_id, launch=LT_LAUNCH, deadline="60")
            if name.endswith("foreign_root"):
                expected["root_pid"] = "4343"
            launches = [LT_LAUNCH for _ in runs]
        items.append({"name": name, "kind": "compose", "case_id": case_id, "run": _lt_run(folder, name),
                      "fake": _lt_fake(), "expected": expected, "pins": pins,
                      "runs": [{"sub": sub, "fake": _lt_fake(**fake), "launch": launch}
                               for (sub, fake), launch in zip(runs, launches)]})
    return items


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic lifetime drivers need pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def lt_rows(request, tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the lifetime drivers plan Windows paths for the Windows-only owned job: non-Windows is untested, "
                    "not evidence")
    _, executable = request.param
    folder = tmp_path_factory.mktemp("lifetime")
    items = _lt_items(folder)
    host = folder / "lt_host.ps1"
    host.write_text(LIFETIME_HOST, encoding="utf-8")
    fixture = folder / "lt_fixture.json"
    fixture.write_text(json.dumps(items), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(HARNESS),
                           str(fixture)], capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row for row in rows}, {item["name"]: item for item in items}


@WINDOWS_ONLY
@pytest.mark.parametrize("name,case_id,sub,fake,expected", LIFETIME_FIXTURES,
                         ids=[row[0] for row in LIFETIME_FIXTURES])
def test_lifetime_driver_observation_comes_from_the_job_the_clock_and_the_witnesses(lt_rows, name, case_id, sub,
                                                                                    fake, expected):
    row = lt_rows[0][name]
    assert row["result"] == expected, row
    seams = row["seams"]
    assert seams["created"] == 1 and seams["disposed"] == (0 if fake.get("create_throws") else 1), seams
    assert seams["contexts"] == (1 if case_id == "A3" else 0), seams
    # One Stop at most, and none for A5 (the controller is killed instead) or a drained early completion.
    stops = 0 if case_id == "A5" or fake.get("create_throws") or fake.get("root_exit_at") == 10 and not fake.get(
        "active_after_root_exit") else 1
    assert seams["stops"] == stops and seams["kills"] == (1 if case_id == "A5" else 0), seams
    files = row["files"]
    assert "members.registration" in files, files
    if sub == "off_switch_during_run":
        assert "mind-shadow.off" in files
    binding = row["binding"]
    assert binding == {"harness_run_id": "SYNTHETIC-HARNESS-RUN", "case_id": case_id, "launch_id": LT_LAUNCH,
                       "root_pid": "UNKNOWN" if fake.get("create_throws") else "4242",
                       "root_creation_utc": "UNKNOWN" if fake.get("create_throws") else "SYNTHETIC-ROOT-CREATION-UTC",
                       "deadline_seconds": "60"}


@WINDOWS_ONLY
def test_lifetime_driver_stop_outcomes_and_times_are_measured(lt_rows):
    rows = lt_rows[0]
    assert rows["lt_a1_deadline"]["observation"] == {"outcome": "stopped_at_deadline", "stop_confirmed": True,
                                                     "active_processes_after": 0, "elapsed_seconds": 60}
    assert rows["lt_a1_off_switch"]["observation"]["outcome"] == "stopped_by_off_switch"
    assert rows["lt_a1_off_switch"]["observation"]["elapsed_seconds"] == 30
    assert rows["lt_a1_disk_floor"]["observation"]["outcome"] == "stopped_at_disk_floor"
    assert rows["lt_a1_deadline_unconfirmed"]["observation"]["outcome"] == "stop_unconfirmed"
    assert rows["lt_a1_deadline_completed_early"]["observation"]["outcome"] == "completed"
    assert rows["lt_a1_orphan"]["observation"] == {"outcome": "stopped_at_deadline", "orphan_alive_at_root_exit": True}
    assert rows["lt_a1_orphan_never_started"]["observation"]["orphan_alive_at_root_exit"] is None
    assert rows["lt_a1_single_stop"]["observation"] == {"stop_calls": 1, "stop_confirmed": True}
    assert rows["lt_a2_independent_survivor"]["observation"]["independent_survivors"] == ["grandchild"]
    assert rows["lt_a2_live_descendant"]["observation"]["live_descendants"] == ["child"]
    assert rows["lt_a2_late_child_never_started"]["observation"] is None
    assert rows["lt_a3_outer_no_limits"]["observation"] == {"result": "assigned_before_resume"}
    assert rows["lt_a3_refused_unwitnessed"]["observation"] == {"result": "refused_unwitnessed"}
    assert rows["lt_a5_controller_death"]["observation"]["expected"] == ["root", "child", "grandchild"]
    assert rows["lt_a6_clean"]["observation"]["lock_reacquire"] == {"attempted": True, "acquired": True,
                                                                    "released": True, "error": None}
    assert rows["lt_a6_stop_unconfirmed"]["observation"]["lock_reacquire"]["attempted"] is False


@WINDOWS_ONLY
@pytest.mark.parametrize("name,case_id,sub,change", LIFETIME_ERRORS, ids=[row[0] for row in LIFETIME_ERRORS])
def test_lifetime_driver_refuses_invalid_input_and_the_disabled_real_seams(lt_rows, name, case_id, sub, change):
    row = lt_rows[0][name]
    assert row["result"] == "ERROR" and row["error"].startswith("refused: "), row
    assert row["seams"]["created"] == 0 and row["seams"]["stops"] == 0, row
    if name == "lt_real_seams_refuse":
        assert "disabled" in row["error"], row


@WINDOWS_ONLY
@pytest.mark.parametrize("name,case_id,runs,expected", LIFETIME_COMPOSE, ids=[row[0] for row in LIFETIME_COMPOSE])
def test_lifetime_case_observation_binds_each_launch(lt_rows, name, case_id, runs, expected):
    # A2, A5 and A6 keep the old one-launch binding; A1 and A3 compose Lead's v8 per-sub-case binding from one driver
    # run per sub-case (a reused root fails, a missing sub-case is UNKNOWN, a duplicate or foreign run refuses).
    row = lt_rows[0][name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: "), row


# Lead's v7: the one synthetic lifetime member. Its default invocation (no switch, no environment opt-in, or only
# one of them) refuses before any witness or child exists; its functions run here on a host that makes the real child
# start throw, so only witness and registration files in the test's temporary .codex-audit folder are written.
MEMBER = ROOT / "tests" / "windows" / "wd_mind_shadow_acceptance_member.ps1"
MEMBER_HOST = r"""
param([string] $Member, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject', 'New-Item', 'Start-Sleep')) {
    Set-Item -Path "function:global:$name" -Value ([scriptblock]::Create("throw 'unexpected native call: $name'"))
}
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile($Member, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'the member does not parse' }
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($statement.Extent.Text))
    }
}
Set-Item -Path 'function:Start-WdShadowMemberProcess' -Value {
    throw 'unexpected native call: Start-WdShadowMemberProcess'
}
$rows = @()
foreach ($item in (ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture)))) {
    $row = [ordered]@{ name = [string]$item.name }
    try {
        if ([string]$item.kind -ceq 'children') {
            $row.result = @(Get-WdShadowMemberChildren -Subcase $item.subcase -Member $item.member)
        } else {
            $directory = Test-WdShadowMemberRun -LaunchId $item.launch -Member $item.member -Subcase $item.subcase `
                -WitnessDirectory $item.directory -LifetimeSeconds ([int]$item.lifetime)
            $stream = Open-WdShadowMemberWitness -Directory $directory -LaunchId $item.launch -Member $item.member
            try {
                $held = $false
                try {
                    $probe = New-Object IO.FileStream($stream.Name, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                        [IO.FileShare]::ReadWrite)
                    $probe.Dispose()
                } catch [IO.IOException] { $held = $true }
                $row.held = $held
            } finally { $stream.Dispose() }
            $row.line = [IO.File]::ReadAllText($stream.Name)
            $row.result = 'WITNESS'
        }
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message }
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 4
"""


def _member_dir(folder, name, members=("root", "child", "grandchild", "late_child"), launch=LT_LAUNCH, audit=True):
    directory = folder / (".codex-audit" if audit else "elsewhere") / name / launch
    directory.mkdir(parents=True)
    (directory / "members.registration").write_text("".join(f"{LT_LAUNCH} {m}\n" for m in members), encoding="ascii",
                                                    newline="\n")
    return str(directory)


MEMBER_FIXTURES = [
    ("member_root_witness", {}, "WITNESS"),
    ("member_child_witness", {"member": "child"}, "WITNESS"),
    ("member_unknown_name", {"member": "producer"}, "ERROR"),
    ("member_not_in_subcase", {"member": "orphan"}, "ERROR"),
    ("member_unregistered", {"members": ("root", "child", "grandchild")}, "ERROR"),
    ("member_foreign_launch", {"leaf": "88888888-0000-4000-8000-000000000088"}, "ERROR"),
    ("member_outside_codex_audit", {"audit": False}, "ERROR"),
    ("member_witness_exists", {"exists": True}, "ERROR"),
    ("member_unknown_subcase", {"subcase": "A4/publication_kill"}, "ERROR"),
    ("member_lifetime_zero", {"lifetime": 0}, "ERROR"),
]


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic member needs pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def member_rows(request, tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the synthetic member writes Windows witness paths: non-Windows is untested, not evidence")
    _, executable = request.param
    folder = tmp_path_factory.mktemp("member")
    items = []
    for name, change, _ in MEMBER_FIXTURES:
        directory = _member_dir(folder, name, members=change.get("members", ("root", "child", "grandchild",
                                                                             "late_child")),
                                launch=change.get("leaf", LT_LAUNCH), audit=change.get("audit", True))
        member = change.get("member", "root")
        if change.get("exists"):
            Path(directory, member + ".witness").write_text("", encoding="ascii")
        items.append({"name": name, "kind": "witness", "launch": LT_LAUNCH, "member": member,
                      "subcase": change.get("subcase", "A2/survivors"), "directory": directory,
                      "lifetime": change.get("lifetime", 120)})
    for subcase, member in (("A2/survivors", "child"), ("A1/pipe_holding_grandchild", "child"),
                            ("A1/early_root_exit_orphan", "root"), ("A5/controller_death", "grandchild"),
                            ("A6/inherited_handles", "root")):
        items.append({"name": f"children_{subcase}_{member}", "kind": "children", "subcase": subcase,
                      "member": member})
    host = folder / "member_host.ps1"
    host.write_text(MEMBER_HOST, encoding="utf-8")
    fixture = folder / "member_fixture.json"
    fixture.write_text(json.dumps(items), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(MEMBER),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row for row in rows}, executable, folder


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change,expected", MEMBER_FIXTURES, ids=[row[0] for row in MEMBER_FIXTURES])
def test_member_writes_one_held_run_bound_witness_only_for_a_preregistered_member(member_rows, name, change,
                                                                                  expected):
    row = member_rows[0][name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: "), row
        return
    assert row["held"] is True, row
    assert row["line"] == f"{LT_LAUNCH} {change.get('member', 'root')}\n", row


@WINDOWS_ONLY
def test_member_children_are_the_fixed_preregistered_tree(member_rows):
    rows = member_rows[0]
    assert rows["children_A2/survivors_child"]["result"] == ["grandchild", "late_child"]
    assert rows["children_A1/pipe_holding_grandchild_child"]["result"] == ["pipe_grandchild"]
    assert rows["children_A1/early_root_exit_orphan_root"]["result"] == ["orphan"]
    assert rows["children_A5/controller_death_grandchild"]["result"] == []
    assert rows["children_A6/inherited_handles_root"]["result"] == []


@WINDOWS_ONLY
@pytest.mark.parametrize("switch,opt_in", [(False, False), (True, False), (False, True)])
def test_member_default_invocation_refuses_before_any_witness(member_rows, switch, opt_in):
    _, executable, folder = member_rows
    directory = _member_dir(folder, f"default_{switch}_{opt_in}")
    env = {key: value for key, value in os.environ.items() if key != "WD_MIND_SHADOW_SYNTHETIC_MEMBER"}
    if opt_in:
        env["WD_MIND_SHADOW_SYNTHETIC_MEMBER"] = "opt-in"
    arguments = ["-OptInSyntheticMember"] if switch else []
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(MEMBER)] + arguments +
                          ["-LaunchId", LT_LAUNCH, "-Member", "root", "-Subcase", "A2/survivors", "-WitnessDirectory",
                           directory, "-LifetimeSeconds", "5"], capture_output=True, text=True, timeout=120, env=env)
    assert done.returncode == 3, (done.returncode, done.stderr[-2000:])
    assert "refused: the synthetic member runs only on an explicit opt-in" in done.stderr
    assert sorted(os.listdir(directory)) == ["members.registration"]


# Lead's v8: the future owned wiring through Root's frozen runner (Invoke-WdMindShadowA7OwnedProbe and its type,
# managed-process and identity functions). The host loads a SYNTHETIC runner file in place of Root's, with the same
# function names as recording fakes, so nothing real is loaded, typed, started or killed; every pinned file is a
# synthetic file in the test's temporary folder and its pin is computed here.
FAKE_RUNNER = r"""
function Read-WdA7PinnedFile {
    param([string]$Path, [string]$ExpectedSha256, [switch]$AsText)
    return @{ text = [IO.File]::ReadAllText($Path); sha256 = $ExpectedSha256 }
}
function Initialize-WdA7OwnedType { param([string]$LoopText) $global:WdFakeCalls += , ('init ' + $LoopText.Length) }
function New-WdA7ManagedProcess {
    param($HostPath, $Arguments, $WorkingDirectory, $InputPath, $OutputPath, $ErrorPath)
    $text = @($HostPath, ($Arguments -join ' '), $WorkingDirectory, $InputPath, $OutputPath, $ErrorPath) -join '|'
    $global:WdFakeCalls += , ('new ' + $text)
    [IO.File]::WriteAllBytes($OutputPath, [byte[]]@())
    [IO.File]::WriteAllBytes($ErrorPath, [byte[]]@())
    foreach ($member in @($global:WdFakeMembers)) {  # released synthetic witnesses, never a member process
        [IO.File]::WriteAllText($WorkingDirectory + '\' + $member + '.witness', $global:WdFakeLaunch + ' ' + $member)
    }
    $fake = [pscustomobject]@{ Pid = 5151; TotalProcesses = 1; ExitCode = 0 }
    $fake | Add-Member -MemberType ScriptProperty -Name ActiveProcesses -Value { $global:WdFakeActive }
    $fake | Add-Member -MemberType ScriptMethod -Name Stop -Value {
        $global:WdFakeCalls += , 'stop'
        if ($global:WdFakeStopThrows) { throw 'synthetic owned stop failure' }
        $global:WdFakeActive = 0; $global:WdFakeRootExited = $true
    }
    $fake | Add-Member -MemberType ScriptMethod -Name Wait -Value {
        param($Milliseconds) $global:WdClock += $Milliseconds / 1000.0
        if ($global:WdFakeMode -ceq 'witness_before_stop') { $global:WdFakeRootExited = $true }
        return [bool]$global:WdFakeRootExited
    }
    $fake | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        $global:WdFakeCalls += , 'dispose'
        if ($global:WdFakeMode -like '*dispose_throws*') { throw 'synthetic owned dispose failure' }
    }
    return $fake
}
function Get-WdA7ObservedIdentity {
    param([int]$ProbePid)
    if ($global:WdFakeMode -like 'identity_throws*') { throw 'synthetic identity failure' }
    return @{ pid = [string]$ProbePid; creation_utc = 'SYNTHETIC-CREATION-' + $ProbePid }
}
function Invoke-WdMindShadowA7OwnedProbe {
    [CmdletBinding()]
    param([switch]$ApprovedNativeExecution, [Collections.IDictionary]$Plan, [hashtable]$ApprovedInputs,
        [string]$CaseRoot, [string]$LiteralAdapterCommand, [hashtable]$CaseBinding, [string]$HostPath,
        [string]$AdapterPath, [string]$LoopPath, [string]$PlannerPath, [string]$EvidenceRoot,
        [string]$ExpectedHostSha256, [string]$ExpectedAdapterSha256, [string]$ExpectedLoopSha256,
        [string]$ExpectedPlannerSha256, [string]$ExpectedRunnerSha256)
    $global:WdFakeCount++
    $global:WdFakeA7 = [ordered]@{ approved = [bool]$ApprovedNativeExecution; plan_keys = @($Plan.Keys | Sort-Object)
        command = $LiteralAdapterCommand; case_root = $CaseRoot; host = $HostPath; evidence = $EvidenceRoot
        planner = $PlannerPath; runner_sha = $ExpectedRunnerSha256; loop_sha = $ExpectedLoopSha256
        inputs_same = ($ApprovedInputs.Count -eq 8) }
    $attempt = @{ attempt_id = [string]$CaseBinding.attempts[[string]$Plan.name]
        probe_pid = [string](5300 + $global:WdFakeCount); probe_creation_utc = 'SYNTHETIC-PROBE-START' }
    $entry = @{ attempt = $attempt
        refusal = @{ exit_code = 2; refusal_seen = $true; run_dir_created = $false; process_started = $false } }
    if ($global:WdFakeMode -ceq 'extra_entry_key') { $entry['verdict'] = 'PASS' }
    $result = [pscustomobject]@{ status = 'OWNED_GUARD_REFUSAL_OBSERVED'; whole_A7_PASS = $false; entry = $entry
        owned_job = @{ total_processes = 2; expected_probe_processes = 2 }; raw = @{ error_text = 'synthetic' } }
    return [pscustomobject]@{ schema = 'wd.a7-owned-probe.v1'; binding = $CaseBinding; name = [string]$Plan.name
        result = $result; whole_A7_PASS = ($global:WdFakeMode -ceq 'whole_pass') }
}
"""

WIRING_HOST = r"""
param([string] $Harness, [string] $Fixture)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
foreach ($name in @('Start-Process', 'Stop-Process', 'Start-Job', 'Invoke-Expression', 'Invoke-Command', 'Add-Type',
        'Wait-Process', 'Get-Process', 'Get-CimInstance', 'Get-WmiObject', 'New-Item', 'Set-Acl', 'Get-Acl',
        'Start-Sleep')) {
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
foreach ($item in (ConvertFrom-Json -InputObject ([IO.File]::ReadAllText($Fixture)))) {
    $entry = ConvertTo-HostValue $item
    $inputs = $entry['inputs']
    if ($inputs -is [hashtable]) {
        $inputs['ExpectedPeakAdditionalBytes'] = [long]$inputs['ExpectedPeakAdditionalBytes']
    }
    $global:WdFakeCalls = @(); $global:WdFakeA7 = $null; $global:WdFakeMode = [string]$entry['mode']
    $global:WdFakeCount = 0
    $global:WdFakeActive = 0; $global:WdFakeRootExited = $true; $global:WdFakeMembers = @(); $global:WdFakeLaunch = ''
    $global:WdClock = 0.0; $global:WdFree = [long]10737418240; $global:WdFakeStopThrows = $false
    $row = [ordered]@{ name = $entry['name'] }
    try {
        if ($entry['kind'] -ceq 'a7') {
            $attempts = @()
            foreach ($sub in @($entry['subs'])) {
                $attempts += , (Invoke-WdShadowA7OwnedAttempt -ApprovedNativeExecution:([bool]$entry['approved']) `
                    -Name $sub -Inputs $inputs -CaseRoot ($entry['case_root'] + '-' + $sub) `
                    -CaseBinding $entry['binding'] -Files $entry['files'] -Expected $entry['expected'] `
                    -EvidenceRoot $entry['evidence'])
            }
            # the fake runner keeps its last call, so the last sub-case's command is the one compared
            $plan = Get-WdShadowA7Plan -Name $entry['subs'][-1] -Inputs $inputs -CaseRoot ($entry['case_root'] + '-' +
                $entry['subs'][-1])
            $row.command_matches = ($null -ne $global:WdFakeA7 -and $global:WdFakeA7.command -ceq
                (Get-WdShadowA7ChildCommand -Plan $plan -AdapterPath $entry['files']['adapter'] `
                    -ExpectedLoopSha256 $entry['expected']['loop']))
            $row.recorded = $global:WdFakeA7
            $row.attempt = $attempts[0]
            $row.result = 'ATTEMPTS'
            if (@($entry['subs']).Count -eq 7) {
                $case = ConvertTo-WdShadowA7CaseObservation -CaseBinding $entry['binding'] -Attempts $attempts
                $row.result = [string](Get-WdShadowA7CaseVerdict -Binding $entry['binding'] -Observation $case)
            }
        } elseif ($entry['kind'] -ceq 'receipt') {
            $failure = $null
            try { throw 'synthetic original failure' } catch { $failure = $_ }
            $row.receipt = ConvertTo-WdShadowOwnedLifetimeReceipt -Result $null -Record @{ launch = @{} } `
                -Failure $failure
            $row.result = 'RECEIPT'
        } elseif ($entry['kind'] -ceq 'whole') {
            # The ACTUAL public wrapper and generic driver on the synthetic runner: a synthetic clock, sleep and
            # free-space reading (the plain reads only), released synthetic witnesses, optional injected failures.
            $directory = [string]$entry['run']['witness_root'] + '\' + [string]$entry['run']['launch_id']
            $global:WdFakeActive = 1; $global:WdFakeRootExited = $false
            $global:WdFakeMembers = @((Get-WdShadowLifetimeMembers)[$entry['case'] + '/' + $entry['sub']])
            $global:WdFakeLaunch = [string]$entry['run']['launch_id']
            $global:WdFakeStopThrows = [bool]$entry['stop_throws']
            $savedSeams = ${function:Get-WdShadowLifetimeSeams}
            $savedStopOnce = ${function:New-WdShadowOwnedStopOnce}
            $savedWitness = ${function:Get-WdShadowMemberObservation}
            $global:WdWitnessReads = 0; $global:WdClockReads = 0
            try {
                ${function:script:Get-WdShadowLifetimeSeams} = {
                    $seams = & $savedSeams
                    $seams['Clock'] = {
                        $global:WdClockReads++
                        if ($entry['inject'] -ceq 'clock_throw') { throw 'synthetic post-acquisition clock failure' }
                        if ($entry['inject'] -ceq 'observer_then_clock_throw') {
                            if ($global:WdClockReads -ge 2) { throw 'synthetic post-observer clock failure' }
                            [IO.File]::WriteAllBytes($directory + '\root.stdout.log', [byte[]]::new(200))
                        }
                        return [double]$global:WdClock
                    }.GetNewClosure()
                    $seams['Sleep'] = { param([int] $Milliseconds) $global:WdClock += $Milliseconds / 1000.0 }
                    $seams['FreeBytes'] = {
                        param([string] $Path)
                        if ($entry['inject'] -ceq 'free_throw') { throw 'synthetic post-acquisition free failure' }
                        return [long]$global:WdFree
                    }.GetNewClosure()
                    return $seams
                }.GetNewClosure()
                if ($entry['inject'] -ceq 'setup') {
                    ${function:script:New-WdShadowOwnedStopOnce} = { throw 'synthetic setup failure' }
                }
                ${function:script:Get-WdShadowMemberObservation} = {
                    param([string] $WitnessDirectory, [object[]] $Expected)
                    $global:WdWitnessReads++
                    if ($entry['inject'] -ceq 'witness_throw') { throw 'synthetic post-acquisition witness failure' }
                    if ($global:WdFakeActive -eq 0 -and $entry['inject'] -ceq 'witness_growth') {
                        [IO.File]::WriteAllBytes($WitnessDirectory + '\root.stdout.log', [byte[]]::new(200))
                    }
                    if ($global:WdFakeActive -eq 0 -and $entry['inject'] -ceq 'witness_log_gone') {
                        [IO.File]::Delete($WitnessDirectory + '\root.stdout.log')
                    }
                    return (& $savedWitness -WitnessDirectory $WitnessDirectory -Expected $Expected)
                }.GetNewClosure()
                $row.receipt = Invoke-WdShadowOwnedLifetimeDriver -ApprovedNativeExecution -CaseId $entry['case'] `
                    -Name $entry['sub'] -Run $entry['run'] -Files $entry['files'] -Expected $entry['expected'] `
                    -MemberPath $entry['member'] -ExpectedMemberSha256 $entry['member_pin'] `
                    -HelperPath $entry['helper'] -ExpectedHelperSha256 $entry['helper_pin'] `
                    -EvidenceRoot $entry['evidence'] -MaxOutputBytes ([long]$entry['max'])
                $row.result = 'RAN'
            } finally {
                ${function:script:Get-WdShadowLifetimeSeams} = $savedSeams
                ${function:script:New-WdShadowOwnedStopOnce} = $savedStopOnce
                ${function:script:Get-WdShadowMemberObservation} = $savedWitness
                $row.registration = [IO.File]::Exists($directory + '\members.registration')
                $row.input = [IO.File]::Exists($directory + '\root.input')
                $row.calls = @($global:WdFakeCalls)
                $row.witness_reads = $global:WdWitnessReads
                $row.env_after = [string][Environment]::GetEnvironmentVariable('WD_MIND_SHADOW_SYNTHETIC_MEMBER')
            }
        } elseif ($entry['kind'] -ceq 'driver') {
            $directory = [string]$entry['run']['witness_root'] + '\' + [string]$entry['run']['launch_id']
            try {
                [void](Invoke-WdShadowOwnedLifetimeDriver -ApprovedNativeExecution -CaseId $entry['case'] `
                    -Name $entry['sub'] -Run $entry['run'] -Files $entry['files'] -Expected $entry['expected'] `
                    -MemberPath $entry['member'] -ExpectedMemberSha256 $entry['member_pin'] `
                    -HelperPath $entry['helper'] -ExpectedHelperSha256 $entry['helper_pin'] `
                    -EvidenceRoot $entry['evidence'])
                $row.result = 'RAN'
            } finally {
                $row.registration = [IO.File]::Exists($directory + '\members.registration')
                $row.input = [IO.File]::Exists($directory + '\root.input')
                $row.calls = @($global:WdFakeCalls)
            }
        } else {
            $record = @{}
            $global:WdFakeStopThrows = [bool]$entry['stop_throws']
            $seams = New-WdShadowOwnedLifetimeSeams -ApprovedNativeExecution:([bool]$entry['approved']) `
                -CaseId $entry['case'] -Name $entry['sub'] -Run $entry['run'] -Files $entry['files'] `
                -Expected $entry['expected'] -MemberPath $entry['member'] `
                -ExpectedMemberSha256 $entry['member_pin'] -HelperPath $entry['helper'] `
                -ExpectedHelperSha256 $entry['helper_pin'] -Record $record -EvidenceRoot $entry['evidence'] `
                -MaxOutputBytes ([long]$entry['max'])
            $row.keys = @($seams.Keys | Sort-Object)
            $plan = Get-WdShadowLifetimePlan -CaseId $entry['case'] -Name $entry['sub'] -Run $entry['run']
            if ($entry['substitute'] -ceq 'host') { $plan['host_path'] = 'C:\FOREIGN\pwsh.exe' }
            if ($entry['substitute'] -ceq 'argument') {
                $plan['arguments'] = [string[]]@($plan['arguments'] | ForEach-Object { $_ }) + @('-Foreign')
            }
            if ($entry['substitute'] -ceq 'directory') { $plan['witness_directory'] = $entry['evidence'] }
            [void][IO.Directory]::CreateDirectory([string]$plan['witness_directory'])
            $managed = $null
            $row.launch = $(try { $managed = & $seams['NewManaged'] $plan; 'RAN' } catch {
                [string]$_.Exception.Message })
            $dir = [string]$plan['witness_directory']
            $row.input = [IO.File]::Exists($dir + '\root.input')
            if ($null -ne $managed) {
                if ($entry['grow']) {
                    [IO.File]::WriteAllBytes($dir + '\root.stdout.log', [byte[]]::new(200)); [void]$managed.Wait(0)
                }
                $row.stop_errors = @(foreach ($n in 1..2) { try { $managed.Stop(); 'ok' } catch { 'threw' } })
                $row.receipt = ConvertTo-WdShadowOwnedLifetimeReceipt -Record $record -Result @{
                    case_id = $entry['case']; name = $entry['sub']; binding = @{}
                    observation = @{ synthetic = $true } }
                $row.observations_before = [int]$record['budget']['observations']
                [void](& $seams['Clock']); [void](& $seams['Sleep'] 0); [void](& $seams['FreeBytes'] $dir)
                $row.observations_after = [int]$record['budget']['observations']
            }
            $row.record = $record
            $row.env_after = [string][Environment]::GetEnvironmentVariable('WD_MIND_SHADOW_SYNTHETIC_MEMBER')
            $row.calls = @($global:WdFakeCalls)
            $row.creation = & $seams['RootCreation'] ([pscustomobject]@{ Pid = 5151 })
            $row.refused = @(foreach ($seam in @('Context', 'KillController', 'InheritedHandles')) {
                try { [void](& $seams[$seam] $plan); 'RAN' } catch { [string]$_.Exception.Message }
            })
            $row.result = 'SEAMS'
        }
    } catch { $row.result = 'ERROR'; $row.error = [string]$_.Exception.Message; $row.recorded = $global:WdFakeA7 }
    $rows += $row
}
ConvertTo-Json -InputObject @($rows) -Compress -Depth 8
"""


def _sha(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


WIRING_A7 = [
    ("w7_owned_attempt", {}, "ATTEMPTS"),
    ("w7_all_native_compose", {"subs": list(A7_NATIVE)}, "PASS"),
    ("w7_not_approved", {"approved": False}, "ERROR"),
    ("w7_runner_pin_mismatch", {"pin": ("runner", "0" * 64)}, "ERROR"),
    ("w7_lowercase_pin", {"lower": "loop"}, "ERROR"),
    ("w7_relative_path", {"file": ("host", "pins\\host.exe")}, "ERROR"),
    ("w7_not_c_drive", {"file": ("adapter", "D:\\pins\\adapter.ps1")}, "ERROR"),
    ("w7_missing_file", {"file": ("planner", "missing")}, "ERROR"),
    ("w7_evidence_outside_audit", {"evidence": "elsewhere"}, "ERROR"),
    ("w7_structural_case", {"subs": ["lane_loop_zero_functions"]}, "ERROR"),
    ("w7_whole_pass_receipt", {"mode": "whole_pass"}, "ERROR"),
    ("w7_extra_entry_key", {"mode": "extra_entry_key"}, "ERROR"),
    ("w7_preload_pins_loop", {"subs": ["preloaded_type"]}, "ATTEMPTS"),
]
WIRING_LIFETIME = [
    ("wl_owned_factory", {}, "SEAMS"),
    ("wl_not_approved", {"approved": False}, "ERROR"),
    ("wl_loop_pin_mismatch", {"pin": ("loop", "F" * 64)}, "ERROR"),
    ("wl_member_pin_mismatch", {"member_pin": "0" * 64}, "ERROR"),
    ("wl_member_lowercase_pin", {"member_lower": True}, "ERROR"),
    ("wl_member_relative", {"member": "pins\\member.ps1"}, "ERROR"),
    ("wl_foreign_run_host", {"run": ("host_path", "C:\\FOREIGN\\pwsh.exe")}, "ERROR"),
    ("wl_foreign_run_member", {"run": ("member_path", "C:\\FOREIGN\\member.ps1")}, "ERROR"),
    ("wl_escaped_witness_root", {"run": ("witness_root", "OTHER")}, "ERROR"),
    ("wl_substituted_host", {"substitute": "host"}, "SEAMS"),
    ("wl_substituted_argument", {"substitute": "argument"}, "SEAMS"),
    ("wl_substituted_directory", {"substitute": "directory"}, "SEAMS"),
    ("wl_observer_over_bound", {"max": 100, "grow": True}, "SEAMS"),
    ("wl_stop_failure", {"stop_throws": True}, "SEAMS"),
    ("wl_a2_survivors", {"case": ("A2", "survivors")}, "SEAMS"),
    ("wl_helper_pin_mismatch", {"helper_pin": "0" * 64}, "ERROR"),
    ("wl_helper_relative", {"helper": "pins\\helper.ps1"}, "ERROR"),
    ("wl_a3_refused", {"case": ("A3", "outer_no_limits")}, "ERROR"),
    ("wl_a5_refused", {"case": ("A5", "controller_death")}, "ERROR"),
    ("wl_a6_refused", {"case": ("A6", "inherited_handles")}, "ERROR"),
    ("wld_foreign_host", {"kind": "driver", "run": ("host_path", "C:\\FOREIGN\\pwsh.exe")}, "ERROR"),
    ("wld_a3_refused", {"kind": "driver", "case": ("A3", "outer_no_limits")}, "ERROR"),
    ("wl_bound_zero", {"max": 0}, "ERROR"),
    ("wl_bound_over", {"max": 8388609}, "ERROR"),
]
# Lead's v12: the ACTUAL public wrapper through the ACTUAL generic driver on the synthetic runner (both hosts).
WHOLE_SUBS = [("A1", "deadline", "stopped_at_deadline"), ("A1", "early_root_exit_orphan", "stopped_at_deadline"),
              ("A1", "pipe_holding_grandchild", "stopped_at_deadline"),
              ("A1", "off_switch_during_run", "stopped_by_off_switch"),
              ("A1", "disk_floor_hit", "stopped_at_disk_floor"), ("A1", "single_confirmed_stop", None),
              ("A2", "survivors", None)]
WIRING_WHOLE = [("wh_" + sub, {"kind": "whole", "case": (case, sub)}, outcome) for case, sub, outcome in WHOLE_SUBS]
WIRING_WHOLE_FAIL = [
    ("whf_setup_failure", {"kind": "whole", "inject": "setup"}),
    ("whf_setup_cleanup_errors", {"kind": "whole", "inject": "setup", "stop_throws": True, "mode": "dispose_throws"}),
    ("whf_identity_failure", {"kind": "whole", "mode": "identity_throws"}),
    ("whf_exception_clock", {"kind": "whole", "inject": "clock_throw"}),
    ("whf_exception_free", {"kind": "whole", "inject": "free_throw"}),
    ("whf_exception_clock_cleanup_errors", {"kind": "whole", "inject": "clock_throw", "stop_throws": True,
                                             "mode": "dispose_throws"}),
    ("whf_exception_identity_cleanup_errors", {"kind": "whole", "mode": "identity_throws_dispose_throws",
                                                "stop_throws": True}),
    ("whf_exception_witness_after_stop", {"kind": "whole", "inject": "witness_throw"}),
    ("whf_exception_witness_before_stop", {"kind": "whole", "inject": "witness_throw",
                                          "mode": "witness_before_stop", "case": ("A1", "early_root_exit_orphan")}),
    ("whf_exception_witness_after_failed_stop", {"kind": "whole", "inject": "witness_throw",
                                                "stop_throws": True}),
    ("whf_exception_clock_after_observer", {"kind": "whole", "inject": "observer_then_clock_throw", "max": 100}),
    ("whf_dispose_failure", {"kind": "whole", "mode": "dispose_throws"}),
    ("whf_stop_failure", {"kind": "whole", "stop_throws": True}),
    ("whf_witness_growth", {"kind": "whole", "inject": "witness_growth", "max": 100}),
    ("whf_witness_log_gone", {"kind": "whole", "inject": "witness_log_gone"}),
    ("whf_bound_zero", {"kind": "whole", "max": 0}),
    ("whf_bound_negative", {"kind": "whole", "max": -1}),
    ("whf_bound_over", {"kind": "whole", "max": 8388609}),
    ("whf_partial_record_receipt", {"kind": "receipt"}),
]
WHOLE_FAIL_ROWS = [row + (None,) for row in WIRING_WHOLE_FAIL]


@pytest.fixture(scope="module", params=_hosts() or [pytest.param(None, marks=pytest.mark.skip(
    reason="no PowerShell host on this runner: the synthetic wiring needs pwsh or Windows PowerShell"))],
    ids=lambda host: host[0] if host else "no-host")
def wiring_rows(request, tmp_path_factory):
    if os.name != "nt":
        pytest.skip("the owned wiring admits only local C: NTFS paths: non-Windows is untested, not evidence")
    _, executable = request.param
    folder = tmp_path_factory.mktemp("wiring")
    pins = folder / "pins"
    pins.mkdir()
    files = {"host": pins / "host.exe", "adapter": pins / "adapter.ps1", "loop": pins / "loop.ps1",
             "planner": pins / "planner.ps1", "runner": pins / "runner.ps1"}
    for key, path in files.items():
        path.write_bytes(FAKE_RUNNER.encode("utf-8") if key == "runner" else f"SYNTHETIC {key}\n".encode("ascii"))
    member = pins / "member.ps1"
    member.write_bytes(b"SYNTHETIC member\n")
    evidence = folder / ".codex-audit" / "evidence"
    evidence.mkdir(parents=True)
    (folder / "elsewhere").mkdir()
    items = []
    for name, change, _ in WIRING_A7 + WIRING_LIFETIME + WIRING_WHOLE + WHOLE_FAIL_ROWS:
        paths = {key: str(path) for key, path in files.items()}
        expected = {key: _sha(path) for key, path in files.items()}
        if "file" in change:
            key, value = change["file"]
            paths[key] = str(pins / value) if value == "missing" else value
        if "pin" in change:
            expected[change["pin"][0]] = change["pin"][1]
        if "lower" in change:
            expected[change["lower"]] = expected[change["lower"]].lower()
        item = {"name": name, "approved": change.get("approved", True), "files": paths, "expected": expected,
                "evidence": str(folder / "elsewhere") if change.get("evidence") else str(evidence),
                "mode": change.get("mode", "")}
        if name.startswith("w7_"):
            item.update(kind="a7", subs=change.get("subs", ["integrity_mismatch"]), binding=_a7_binding(),
                        inputs=_a7_inputs(str(folder / "approved"), A7_FAKE_SID),
                        case_root=str(folder / ("case-" + name)))
        else:
            own = folder / ".codex-audit" / name
            own.mkdir(parents=True)
            run = {"harness_run_id": "SYNTHETIC-HARNESS-RUN", "launch_id": LT_LAUNCH, "deadline_seconds": "60",
                   "witness_root": str(own), "host_path": paths["host"], "member_path": str(member)}
            if "run" in change:
                key, value = change["run"]
                run[key] = str(folder / ".codex-audit" / "other") if value == "OTHER" else value
            pin = change.get("member_pin", _sha(member))
            case, sub = change.get("case", ("A1", "deadline"))
            item.update(case=case, sub=sub, helper=change.get("helper", str(HELPER_LAUNCH)),
                        helper_pin=change.get("helper_pin", _sha(HELPER_LAUNCH)), max=change.get("max", 1048576),
                        stop_throws=change.get("stop_throws", False), grow=change.get("grow", False))
            item.update(kind=change.get("kind", "lifetime"), run=run, evidence=str(own),
                        member=change.get("member", str(member)),
                        member_pin=pin.lower() if change.get("member_lower") else pin,
                        substitute=change.get("substitute", ""), inject=change.get("inject", ""))
        items.append(item)
    host = folder / "wiring_host.ps1"
    host.write_text(WIRING_HOST, encoding="utf-8")
    fixture = folder / "wiring_fixture.json"
    fixture.write_text(json.dumps(items), encoding="utf-8")
    done = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-File", str(host), str(HARNESS),
                           str(fixture)], capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    rows = json.loads(done.stdout.strip().splitlines()[-1])
    return {row["name"]: row for row in rows}, {item["name"]: item for item in items}


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change,expected", WIRING_A7, ids=[row[0] for row in WIRING_A7])
def test_a7_owned_attempt_calls_roots_runner_once_with_the_pinned_plan(wiring_rows, name, change, expected):
    row, item = wiring_rows[0][name], wiring_rows[1][name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: "), row
        if name not in ("w7_whole_pass_receipt", "w7_extra_entry_key"):
            assert row["recorded"] is None, row  # refused before the runner was called
        return
    recorded = row["recorded"]
    assert recorded["approved"] is True and recorded["inputs_same"] is True and row["command_matches"] is True
    assert recorded["plan_keys"] == ["inputs", "name", "preload", "prepare", "refusal", "run_root"]
    assert recorded["runner_sha"] == item["expected"]["runner"] and recorded["evidence"] == item["evidence"]
    if name == "w7_preload_pins_loop":  # the attempt passes the admitted loop pin to the preload child
        assert "'" + item["expected"]["loop"] + "'" in recorded["command"], recorded
    attempt = row["attempt"]
    assert sorted(attempt) == ["entry", "name", "owned_job", "raw", "status"]
    assert sorted(attempt["entry"]) == ["attempt", "refusal"]


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change,expected", WIRING_LIFETIME, ids=[row[0] for row in WIRING_LIFETIME])
def test_owned_lifetime_seams_bind_host_member_and_run_and_refuse_the_launch(wiring_rows, name, change, expected):
    row, item = wiring_rows[0][name], wiring_rows[1][name]
    assert row["result"] == expected, row
    if expected == "ERROR":
        assert row["error"].startswith("refused: "), row
        if item["kind"] == "driver":  # the public wrapper: admission and binding before its first write
            launched = name == "wld_launch_refused"
            assert row["registration"] is launched and row["input"] is False and row["calls"] == [], row
            if launched:
                assert "child-local opt-in" in row["error"], row
        return
    assert row["keys"] == sorted(["NewManaged", "RootCreation", "Clock", "Sleep", "FreeBytes", "Context",
                                  "KillController", "InheritedHandles"])
    # Lead's v11 GO: a substituted plan still refuses before any write, type or process call; the approved A1/A2
    # plan launches through Root's six-argument API with the v10 fixed plan argv (child-only opt-in, literal
    # member), behind ONE stop-once facade shared with the output observer, which observes at every seam.
    if item["substitute"]:
        assert row["launch"].startswith("refused: the plan is not the approved lifetime plan"), row
        assert row["input"] is False and row["calls"] == [] and row["record"]["launch"]["started"] is False, row
    else:
        assert row["launch"] == "RAN", row
        directory = item["run"]["witness_root"] + "\\" + LT_LAUNCH
        launch = row["record"]["launch"]
        assert row["calls"][0] == "init " + str(len("SYNTHETIC loop\n"))
        # the fixed command holds a pipe itself, so the host is the first field and the four paths the last four
        host, rest = row["calls"][1][len("new "):].split("|", 1)
        arguments, cwd, stdin, stdout, stderr = rest.rsplit("|", 4)
        assert host == item["files"]["host"] == launch["host_path"] and launch["started"] is True
        assert arguments == " ".join(launch["argv"]) and launch["argv"][:3] == ["-NoProfile", "-NonInteractive",
                                                                               "-Command"]
        assert launch["command_sha256"] == hashlib.sha256(launch["argv"][3].encode("utf-8")).hexdigest().upper()
        assert launch["plan_schema"] == "wd.mind-shadow-fixed-member-launch-plan.v1" and "-File" not in arguments
        assert "$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER = 'opt-in'" in launch["argv"][3]
        assert "-Subcase '" + item["case"] + "/" + item["sub"] + "'" in launch["argv"][3]
        assert cwd == directory and stdin == directory + "\\root.input" and row["input"] is True
        assert stdout == directory + "\\root.stdout.log" and stderr == directory + "\\root.stderr.log"
        assert row["env_after"] == ""  # the parent environment never changes
        stop, receipt = row["record"]["stop"], row["receipt"]
        assert row["calls"].count("stop") == 1 and stop["inner_calls"] == 1, row  # one owned stop, never repeated
        assert row["observations_after"] == row["observations_before"] + 3  # the clock, sleep and free seams observe
        assert receipt["schema"] == "wd.mind-shadow-owned-lifetime-receipt.v2" and receipt["native_pass"] is False
        if name == "wl_observer_over_bound":
            assert row["record"]["budget"]["trigger"] == "OVER_BOUND" and stop["origin"] == "observer", row
            assert stop["requests"] == 3 and receipt["observation"] is None and receipt["budget_or_stop_blocked"]
        elif name == "wl_stop_failure":
            assert stop["failed"] is True and row["stop_errors"] == ["threw", "ok"] and receipt["observation"] is None
        else:
            assert stop["origin"] == "driver" and stop["requests"] == 2 and row["stop_errors"] == ["ok", "ok"], row
            assert receipt["observation"] == {"synthetic": True} and row["record"]["budget"]["trigger"] is None
    assert row["creation"] == "SYNTHETIC-CREATION-5151"
    assert len(row["refused"]) == 3 and all(text.startswith("refused: ") for text in row["refused"]), row


def _whole_launch_ok(row, item):
    launch = row["receipt"]["launch"]
    assert launch["started"] is True and launch["argv"][:3] == ["-NoProfile", "-NonInteractive", "-Command"], row
    assert launch["command_sha256"] == hashlib.sha256(launch["argv"][3].encode("utf-8")).hexdigest().upper()
    assert "-Subcase '" + item["case"] + "/" + item["sub"] + "'" in launch["argv"][3] and row["env_after"] == ""
    assert row["calls"][0] == "init " + str(len("SYNTHETIC loop\n")) and row["calls"][1].startswith("new ")
    assert row["calls"][1][len("new "):].split("|", 1)[0] == item["files"]["host"] and row["input"] is True
    assert row["registration"] is True


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change,outcome", WIRING_WHOLE, ids=[row[0] for row in WIRING_WHOLE])
def test_whole_owned_wrapper_runs_the_generic_driver_on_the_synthetic_runner(wiring_rows, name, change, outcome):
    row, item = wiring_rows[0][name], wiring_rows[1][name]
    assert row["result"] == "RAN", row
    receipt = row["receipt"]
    assert receipt["schema"] == "wd.mind-shadow-owned-lifetime-receipt.v2" and receipt["native_pass"] is False
    assert receipt["failure"] is None and receipt["errors"] == [] and receipt["budget_or_stop_blocked"] is False
    _whole_launch_ok(row, item)
    # one owned stop at the deadline or guard, Dispose last, nothing after it
    assert row["calls"][2:] == ["stop", "dispose"], row
    stop = receipt["stop"]
    assert stop["inner_calls"] == 1 and stop["origin"] == "driver" and stop["dispose"] == "ok", row
    assert receipt["binding"]["root_pid"] == "5151" and receipt["binding"]["root_creation_utc"] == (
        "SYNTHETIC-CREATION-5151")
    assert receipt["binding"]["launch_id"] == LT_LAUNCH and receipt["case_id"] == item["case"]
    budget = receipt["budget"]
    assert budget["trigger"] is None and budget["last"]["status"] == "WITHIN_OBSERVED_BUDGET", row
    # observed immediately before and after every direct witness read, plus the clock, sleep, free and wait seams
    assert row["witness_reads"] >= 1 and budget["observations"] >= 2 * row["witness_reads"], row
    observation = receipt["observation"]
    if outcome:
        assert observation["outcome"] == outcome, row
    elif item["sub"] == "single_confirmed_stop":
        assert observation == {"stop_calls": 1, "stop_confirmed": True}, row
    else:
        assert observation["live_descendants"] == [] and observation["independent_survivors"] == [], row


@WINDOWS_ONLY
@pytest.mark.parametrize("name,change", WIRING_WHOLE_FAIL, ids=[row[0] for row in WIRING_WHOLE_FAIL])
def test_whole_owned_wrapper_keeps_failures_and_cleanup_errors_in_an_unknown_receipt(wiring_rows, name, change):
    row, item = wiring_rows[0][name], wiring_rows[1][name]
    if name.startswith("whf_bound_"):  # refused before any directory, input, registration, type or process
        assert row["result"] == "ERROR" and row["error"].startswith("refused: the output bound"), row
        assert row["registration"] is False and row["input"] is False and row["calls"] == [], row
        return
    if name == "whf_partial_record_receipt":  # a partial Record never throws over the original failure
        receipt = row["receipt"]
        assert row["result"] == "RECEIPT" and receipt["observation"] is None and receipt["budget_or_stop_blocked"]
        assert "synthetic original failure" in receipt["failure"]["message"] and receipt["stop"] is None, row
        return
    assert row["result"] == "RAN", row
    receipt = row["receipt"]
    assert receipt["schema"] == "wd.mind-shadow-owned-lifetime-receipt.v2" and receipt["native_pass"] is False
    assert receipt["observation"] is None and receipt["budget_or_stop_blocked"] is True, row
    _whole_launch_ok(row, item)
    stop, errors = receipt["stop"], receipt["errors"]
    assert row["calls"].count("stop") <= 1 and row["calls"][-1] == "dispose", row  # never a second owned stop
    if name.startswith("whf_setup_"):
        # the owned object is cleaned up before the driver ever received it: one stop, one Dispose
        assert row["calls"][2:] == ["stop", "dispose"] and stop["origin"] == "setup_cleanup", row
        assert "synthetic setup failure" in receipt["failure"]["message"] and errors[0].startswith("setup: "), row
        assert "cleanup" not in receipt["failure"]["message"], row  # the original error keeps precedence
        if name == "whf_setup_cleanup_errors":
            assert [text.split(":")[0] for text in errors] == ["setup", "cleanup stop", "cleanup dispose"], row
            assert stop["failed"] is True and stop["dispose"] == "failed", row
    elif name == "whf_identity_failure" or name.startswith("whf_exception_"):
        assert row["calls"][2:] == ["stop", "dispose"], row
        assert receipt["failure"] is not None and receipt["binding"] is None, row
        assert stop["inner_calls"] == 1 and stop["requests"] == 1, row
        if name == "whf_identity_failure" or "identity" in name:
            assert "synthetic identity failure" in receipt["failure"]["message"], row
        elif "witness" in name:
            assert "synthetic post-acquisition witness failure" in receipt["failure"]["message"], row
        elif "after_observer" in name:
            assert "synthetic post-observer clock failure" in receipt["failure"]["message"], row
        else:
            token = "free" if name.endswith("_free") else "clock"
            assert "synthetic post-acquisition " + token + " failure" in receipt["failure"]["message"], row
        if "after_observer" in name:
            assert stop["origin"] == "observer", row
        elif "after_stop" in name or "after_failed_stop" in name:
            assert stop["origin"] == "driver", row
        else:
            assert stop["origin"] == "driver_exception_cleanup", row
        if "cleanup_errors" in name:
            assert stop["failed"] is True and stop["dispose"] == "failed", row
            assert "synthetic owned stop failure" in stop["error"], row
            assert "synthetic owned dispose failure" in stop["dispose_error"], row
            assert [text.split(":")[0] for text in errors] == ["driver cleanup stop", "driver cleanup dispose"], row
    elif name == "whf_dispose_failure":
        assert receipt["failure"] is None and stop["dispose"] == "failed", row
        assert "synthetic owned dispose failure" in stop["dispose_error"], row
    elif name == "whf_stop_failure":  # the generic driver catches the owned stop error itself (its stop line)
        assert receipt["failure"] is None and stop["failed"] is True and stop["inner_calls"] == 1, row
        assert "synthetic owned stop failure" in stop["error"] and row["calls"][2:] == ["stop", "dispose"], row
    elif name == "whf_witness_growth":  # growth inside the last witness read: only the after-read hook sees it
        assert receipt["failure"] is None and receipt["budget"]["trigger"] == "OVER_BOUND", row
    else:  # a log lost inside the last witness read: UNKNOWN or a kept failure, never PASS
        assert receipt["budget"]["trigger"] == "UNKNOWN" or receipt["failure"] is not None, row
