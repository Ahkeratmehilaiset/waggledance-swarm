"""A Grok-only update failure is recorded and shown, and the native lanes still launch.

Grok is an optional advisory helper. Codex and Claude update failures still abort
the cold start, and cancellation is never turned into a continued launch.
"""
import json
import re

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell
from test_wd_startup_recovery import load

START_ALL = REBOOT / "start-wd-all.ps1"

# The fake wrapper stands in for Invoke-WdBridgePython.ps1 -Tool tools/wd_grok_helper.py
# -VerifyPackage --update-cli. No real helper, updater or provider runs.
FAKE_WRAPPER = r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$script:calls=0
function Get-CimInstance { param($ClassName,$ErrorAction) }
function Fake-Wrapper {
 param($Tool,[switch]$VerifyPackage,[Parameter(ValueFromRemainingArguments)][string[]]$ToolArguments)
 if($Tool -cne 'tools/wd_grok_helper.py' -or -not $VerifyPackage -or
    ($ToolArguments -join ' ') -cne '--update-cli'){throw 'Wrong update invocation'}
 $script:calls++
 $receipt=@{schema='wd.grok-cli-update.v1';update_status='updated';before='grok old';after='grok new';
            update_command='grok update'}|ConvertTo-Json -Compress
 $blocked={param($text) $global:LASTEXITCODE=2; @{status='blocked';error=$text}|ConvertTo-Json -Compress}
 switch($case){
  'updated'      {$global:LASTEXITCODE=0; $receipt}
  'missing'      {& $blocked 'Grok update requires the installed user executable'}
  'unresolved'   {& $blocked 'Grok update blocked: unresolved_attempt'}
  'busy'         {& $blocked '[Errno 13] Permission denied: grok lock'}
  'network'      {& $blocked 'grok update failed with exit code 1: network unreachable'}
  'timeout'      {& $blocked "Command '['grok.exe', 'update']' timed out after 300 seconds"}
  'nonzero_text' {$global:LASTEXITCODE=7; 'not json'}
  'empty'        {$global:LASTEXITCODE=0}
  'malformed'    {$global:LASTEXITCODE=0; '{"schema": "wd.grok-cli-update.v1", '}
  'not_updated'  {$global:LASTEXITCODE=0; @{schema='wd.grok-cli-update.v1';update_status='pending'}|ConvertTo-Json -Compress}
  'wrong_schema' {$global:LASTEXITCODE=0; @{schema='other';update_status='updated'}|ConvertTo-Json -Compress}
  'array'        {$global:LASTEXITCODE=0; '[1,2]'}
  'wrapper_throw'{throw 'package verification failed'}
  'anchor_refused'{throw 'pinned bridge invocation found a deployment manifest that differs from its external anchor'}
  'integrity_refused'{throw 'pinned bridge code hash mismatch: tools/wd_grok_helper.py'}
  'stopped'      {throw [System.Management.Automation.PipelineStoppedException]::new()}
  'canceled'     {throw [System.OperationCanceledException]::new('operator cancelled')}
  default        {throw "unknown case $case"}
 }
}
'''

ORDINARY_FAILURES = {
    # case: (error_kind, exit_code)
    "missing": ("update_failed_or_blocked", 2),
    "unresolved": ("update_failed_or_blocked", 2),
    "busy": ("update_failed_or_blocked", 2),
    "network": ("update_failed_or_blocked", 2),
    "timeout": ("update_failed_or_blocked", 2),
    "nonzero_text": ("update_failed_or_blocked", 7),
    "empty": ("invalid_receipt", 0),
    "malformed": ("invalid_receipt", 0),
    "not_updated": ("invalid_receipt", 0),
    "wrong_schema": ("invalid_receipt", 0),
    "array": ("invalid_receipt", 0),
}
# RCO1 cold-start F1: a throw from the pinned wrapper (-VerifyPackage manifest, anchor, package integrity or pin
# refusal) is the cold start's package check failing, never an optional Grok failure: it must stop the cold start.
FATAL_REFUSALS = ("anchor_refused", "integrity_refused", "wrapper_throw")
# A stopping pipeline cannot be caught by the test harness either (None); the
# proof is that no record is returned and the caller never continues.
CANCELLATIONS = {"stopped": None, "canceled": "OperationCanceledException"}


def functions(*names):
    return "".join(load(START_ALL, name) for name in names)


def run_case(ps, case, body):
    script = (functions("Test-WdCliUpdateDeferred", "Get-WdCliUpdateStatus", "Invoke-WdGrokCliUpdate",
                        "Invoke-WdGrokCliUpdateOptional")
              + f"$case='{case}'\n" + FAKE_WRAPPER + body)
    completed = _run_powershell(script, executable=ps, check=False)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    assert lines, completed.stdout + completed.stderr
    return json.loads(lines[-1]), completed


# A stopping pipeline skips catch blocks, so the result is written from finally
# straight to the console; "aborted" is cleared only after a normal return.
OPTIONAL_BODY = r'''
$out=[ordered]@{aborted=$true;exception=$null;record=$null;calls=0}
try {$out.record=Invoke-WdGrokCliUpdateOptional -Wrapper 'Fake-Wrapper'; $out.aborted=$false}
catch {$out.exception=$_.Exception.GetType().Name}
finally {$out.calls=$script:calls; [Console]::Out.WriteLine(($out|ConvertTo-Json -Compress -Depth 4))}
'''


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", sorted(ORDINARY_FAILURES))
def test_an_ordinary_grok_failure_is_recorded_not_fatal(ps, case):
    result, completed = run_case(ps, case, OPTIONAL_BODY)
    kind, exit_code = ORDINARY_FAILURES[case]
    assert result["aborted"] is False and result["calls"] == 1
    record = result["record"]
    assert record["schema"] == "wd.grok-cli-update.v1"
    assert record["update_status"] == "failed"  # never "updated" or a readiness claim
    assert (record["error_kind"], record["exit_code"]) == (kind, exit_code)
    assert record["before"] is None and record["after"] is None
    assert record["update_command"] == "grok update"
    assert record["error"] and len(record["error"]) <= 480 and "\n" not in record["error"]
    if exit_code == 2:  # the helper's own refusal text is kept for the operator
        assert "blocked" in record["error"]
    assert "Grok stays optional" in completed.stdout + completed.stderr  # the visible warning


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
def test_a_successful_update_keeps_the_helper_receipt(ps):
    result, completed = run_case(ps, "updated", OPTIONAL_BODY)
    assert result["aborted"] is False and result["calls"] == 1
    assert result["record"] == {"schema": "wd.grok-cli-update.v1", "update_status": "updated",
                                "before": "grok old", "after": "grok new", "update_command": "grok update"}
    assert "Grok stays optional" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", sorted(CANCELLATIONS))
def test_cancellation_still_stops_the_cold_start(ps, case):
    result, _ = run_case(ps, case, OPTIONAL_BODY)
    assert result["aborted"] is True and result["record"] is None
    assert result["exception"] == CANCELLATIONS[case]


INNER_BODY = r'''
$out=[ordered]@{failed=$false;kind=$null;exit_code=$null}
try {[void](Invoke-WdGrokCliUpdate -Wrapper 'Fake-Wrapper')}
catch {$out.failed=$true; $out.kind=$_.Exception.Data['error_kind']; $out.exit_code=$_.Exception.Data['exit_code']}
$out|ConvertTo-Json -Compress
'''


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", ["missing", "nonzero_text", "malformed", "not_updated"])
def test_the_inner_update_stays_fail_closed_and_says_why(ps, case):
    result, _ = run_case(ps, case, INNER_BODY)
    assert result == {"failed": True, "kind": ORDINARY_FAILURES[case][0], "exit_code": ORDINARY_FAILURES[case][1]}


def apply_block(start, end):
    source = START_ALL.read_text(encoding="utf-8-sig")
    first = source.index(start)
    return source[first:source.index(end, first) + len(end)]


GROK_CALLER = ("Write-Host 'Updating Grok Build once...'", "Write-Host (\"  grok update: {0}\" -f $grokUpdateStatus)")


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", ["updated", "network", "malformed", "stopped", "canceled", *FATAL_REFUSALS])
def test_the_cold_start_caller_reaches_the_lane_launch_only_on_success_or_ordinary_failure(ps, case):
    caller = apply_block(*GROK_CALLER)
    # Load whatever update function the real caller names (the throwing one at the
    # unfixed base, the optional one after the fix) plus the throwing one it wraps.
    called = list(dict.fromkeys(["Invoke-WdGrokCliUpdate", *re.findall(r"Invoke-WdGrokCliUpdate\w*", caller)]))
    script = (functions("Test-WdCliUpdateDeferred", "Get-WdCliUpdateStatus", *called)
              + f"$case='{case}'\n$SkipCliUpdate=$false\n" + FAKE_WRAPPER
              + "function Join-Path { param($Path,$ChildPath) 'Fake-Wrapper' }\n" + r'''
$out=[ordered]@{reached_lane_launch=$false;status=$null;exception=$null}
try {
''' + caller + r'''
 $out.status=$grokUpdateStatus
 $out.reached_lane_launch=$true
} catch {$out.exception=$_.Exception.GetType().Name}
finally {[Console]::Out.WriteLine(($out|ConvertTo-Json -Compress))}
''')
    completed = _run_powershell(script, executable=ps, check=False)
    result = json.loads([line for line in completed.stdout.splitlines() if line.startswith("{")][-1])
    if case in CANCELLATIONS:
        assert result == {"reached_lane_launch": False, "status": None, "exception": CANCELLATIONS[case]}
    elif case in FATAL_REFUSALS:
        assert result == {"reached_lane_launch": False, "status": None, "exception": "RuntimeException"}
    else:
        assert result == {"reached_lane_launch": True, "exception": None,
                          "status": "updated" if case == "updated" else "failed"}


MANDATORY = {
    "codex": ("[void](Invoke-CheckedNative -Path $codexUpdateCurrentPath", "codexUpdateStatus"),
    "claude": ("[void](Invoke-CheckedNative -Path $claudeUpdateCurrentPath", "claudeUpdateStatus"),
}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("provider", sorted(MANDATORY))
@pytest.mark.parametrize("outcome", ["ok", "failure", "canceled"])
def test_codex_and_claude_update_failures_still_abort(ps, provider, outcome):
    marker, variable = MANDATORY[provider]
    source = START_ALL.read_text(encoding="utf-8-sig")
    # The whole try/catch, through the catch block's rethrow and closing brace.
    found = re.search(r"\n( *)try \{\n *" + re.escape(marker) + r".*?\n\1  throw\n\1\}", source, re.DOTALL)
    assert found, "mandatory update block not found"
    script = f"$outcome='{outcome}'\n" + r'''
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
$codexUpdateCurrentPath='codex.cmd'; $claudeUpdateCurrentPath='claude.cmd'
function Invoke-CheckedNative { param($Path,$Arguments,$Label)
 if($outcome -eq 'failure'){throw "$Label failed"}
 if($outcome -eq 'canceled'){throw [System.OperationCanceledException]::new()}
}
$out=[ordered]@{aborted=$false;status=$null}
try {
''' + found.group(0) + f"\n $out.status=${variable}\n" + r'''} catch {$out.aborted=$true}
$out|ConvertTo-Json -Compress
'''
    completed = _run_powershell(script, executable=ps, check=False)
    result = json.loads([line for line in completed.stdout.splitlines() if line.startswith("{")][-1])
    assert result == {"aborted": outcome != "ok", "status": "updated" if outcome == "ok" else None}


# The fake resolver stands in for Resolve-WdGrokModel.ps1, which itself either returns
# its verified live/cache record or throws ("Refusing to guess or use a hard-coded model").
FAKE_RESOLVER = r'''
function Fake-Resolver { param([switch]$DryRun,$OutputDirectory)
 $script:resolverCalls++
 $live=$(if($DryRun){'verified_dry_run'}else{'verified_persisted'})
 switch($resolverCase){
  'live'       {'probe text'; [pscustomobject]@{Status=$live;Model='grok-test'}}
  'cache'      {[pscustomobject]@{Status='verified_cache_fallback';Model='grok-cached'}}
  'missing_cli'{throw 'Live Grok model discovery failed (Could not resolve the Grok CLI to an exact executable path.) and no valid cache is available (No Grok model cache exists). Refusing to guess or use a hard-coded model.'}
  'no_record'  {'text only'; [pscustomobject]@{Status='verified_dry_run'}}
  'stopped'    {throw [System.Management.Automation.PipelineStoppedException]::new()}
  'canceled'   {throw [System.OperationCanceledException]::new('operator cancelled')}
  default      {throw "unknown resolver case $resolverCase"}
 }
}
$script:resolverCalls=0
'''
RESOLVER_ORDINARY = {"live": "grok-test", "cache": "grok-cached", "missing_cli": None, "no_record": None}
PREFLIGHT = ("$grokPreflightRecord = Invoke-WdGrokModelResolutionOptional",
             "Write-Host '    model: unavailable (Grok is optional; the native lanes still launch)'\n}")
POST_UPDATE = ("Write-Host 'Resolving the current Grok model...'",
               "Write-Host '  Grok model: unavailable (Grok is optional; the native lanes still launch)'\n  }")


def resolution_script(tmp_path, resolver_case, update_case, blocks, guide=True, preamble=""):
    guide_path = tmp_path / "WD_GROK_MODEL_CURRENT.md"
    if guide:
        guide_path.write_text("# guide\n", encoding="utf-8")
    body = "\n".join(apply_block(*block) for block in blocks)
    called = list(dict.fromkeys(["Invoke-WdGrokCliUpdate", "Invoke-WdGrokModelResolutionOptional",
                                 *re.findall(r"Invoke-WdGrok\w+", body)]))
    return (functions("Test-WdCliUpdateDeferred", "Get-WdCliUpdateStatus", *called)
            + f"$case='{update_case}'\n$resolverCase='{resolver_case}'\n$SkipCliUpdate=$false\n"
            + "$resolver='Fake-Resolver'\n"
            + f"$manifest=[pscustomobject]@{{grok_output_directory='{tmp_path}';grok_markdown='{guide_path}'}}\n"
            + FAKE_WRAPPER + FAKE_RESOLVER
            + "function Join-Path { param($Path,$ChildPath) 'Fake-Wrapper' }\n" + preamble + r'''
$out=[ordered]@{reached_lane_launch=$false;exception=$null;update=$null;preflight=$null;model=$null;resolver_calls=0}
try {
''' + body + r'''
 $out.reached_lane_launch=$true
} catch {$out.exception=$_.Exception.GetType().Name}
finally {
 $out.resolver_calls=$script:resolverCalls
 foreach($pair in @(@('update','grokUpdateRecord'),@('preflight','grokPreflightRecord'),@('model','grokModelRecord'))){
  $v=Get-Variable -Name $pair[1] -ValueOnly -ErrorAction SilentlyContinue
  if($null -ne $v){$out[$pair[0]]=$v}
 }
 [Console]::Out.WriteLine(($out|ConvertTo-Json -Compress -Depth 4))
}
''')


def run_resolution(ps, tmp_path, resolver_case, update_case, blocks, guide=True, preamble=""):
    completed = _run_powershell(resolution_script(tmp_path, resolver_case, update_case, blocks, guide, preamble),
                                executable=ps, check=False)
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    assert lines, completed.stdout + completed.stderr
    return json.loads(lines[-1]), completed.stdout + completed.stderr


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("phase", ["preflight", "model"])
@pytest.mark.parametrize("resolver_case", sorted(RESOLVER_ORDINARY))
def test_model_resolution_failure_leaves_grok_unavailable_and_the_lanes_launch(ps, tmp_path, phase, resolver_case):
    block = PREFLIGHT if phase == "preflight" else POST_UPDATE
    result, text = run_resolution(ps, tmp_path, resolver_case, "updated", [block])
    assert result["reached_lane_launch"] is True and result["exception"] is None
    assert result["resolver_calls"] == 1
    record = result[phase]
    model = RESOLVER_ORDINARY[resolver_case]
    assert record["schema"] == "wd.grok-model-resolution.v1"
    assert record["model"] == model  # never a guessed or hard-coded model
    if model:
        assert (record["status"], record["error"]) == ("verified", None)
        assert record["resolver_status"] == {"cache": "verified_cache_fallback", "live": (
            "verified_dry_run" if phase == "preflight" else "verified_persisted")}[resolver_case]
        assert "Grok stays optional" not in text and f"model: {model}" in text
    else:
        assert record["status"] == "unavailable" and record["resolver_status"] is None
        assert record["error"] and len(record["error"]) <= 480
        assert "Grok stays optional" in text and "unavailable (Grok is optional" in text


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
def test_a_verified_model_without_its_generated_guide_is_unavailable(ps, tmp_path):
    result, _ = run_resolution(ps, tmp_path, "live", "updated", [POST_UPDATE], guide=False)
    assert result["reached_lane_launch"] is True
    assert result["model"]["status"] == "unavailable" and result["model"]["model"] is None
    assert "guide is missing or empty" in result["model"]["error"]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("phase", ["preflight", "model"])
@pytest.mark.parametrize("resolver_case", sorted(CANCELLATIONS))
def test_cancellation_during_model_resolution_still_stops_the_cold_start(ps, tmp_path, phase, resolver_case):
    block = PREFLIGHT if phase == "preflight" else POST_UPDATE
    result, _ = run_resolution(ps, tmp_path, resolver_case, "updated", [block])
    assert result["reached_lane_launch"] is False and result[phase] is None
    assert result["exception"] == CANCELLATIONS[resolver_case]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
def test_a_machine_without_grok_still_reaches_the_native_lane_launch(ps, tmp_path):
    # preflight, update and post-update resolution in their real order, all Grok-only failures
    result, text = run_resolution(ps, tmp_path, "missing_cli", "missing", [PREFLIGHT, GROK_CALLER, POST_UPDATE])
    assert result["reached_lane_launch"] is True and result["exception"] is None
    assert result["preflight"]["status"] == "unavailable" and result["model"]["status"] == "unavailable"
    assert result["update"]["update_status"] == "failed"
    assert result["update"]["error_kind"] == "update_failed_or_blocked"
    assert "requires the installed user executable" in result["update"]["error"]
    assert result["resolver_calls"] == 2


# W1 (RCO2 8fc3, Lead 15:15Z): a caller or profile may set WarningPreference to Stop. The optional
# notices pass -WarningAction Continue, so the notice is still shown and never becomes an abort.
STOP_PREFERENCE = "$WarningPreference='Stop'\n"


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", ["network", "malformed"])
def test_a_stop_warning_preference_keeps_an_update_failure_optional(ps, case):
    result, completed = run_case(ps, case, STOP_PREFERENCE + OPTIONAL_BODY)
    assert result["aborted"] is False and result["exception"] is None and result["calls"] == 1
    assert result["record"]["update_status"] == "failed"
    assert result["record"]["error_kind"] == ORDINARY_FAILURES[case][0]
    assert "Grok stays optional" in completed.stdout + completed.stderr


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
def test_a_stop_warning_preference_keeps_a_successful_update(ps):
    result, completed = run_case(ps, "updated", STOP_PREFERENCE + OPTIONAL_BODY)
    assert result["aborted"] is False and result["record"]["update_status"] == "updated"
    assert "Grok stays optional" not in completed.stdout + completed.stderr


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", sorted(CANCELLATIONS))
def test_a_stop_warning_preference_still_lets_cancellation_stop(ps, case):
    result, _ = run_case(ps, case, STOP_PREFERENCE + OPTIONAL_BODY)
    assert result["aborted"] is True and result["record"] is None
    assert result["exception"] == CANCELLATIONS[case]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("phase", ["preflight", "model"])
@pytest.mark.parametrize("resolver_case", ["missing_cli", "no_record", "live", "canceled"])
def test_a_stop_warning_preference_keeps_model_resolution_optional(ps, tmp_path, phase, resolver_case):
    block = PREFLIGHT if phase == "preflight" else POST_UPDATE
    result, text = run_resolution(ps, tmp_path, resolver_case, "updated", [block], preamble=STOP_PREFERENCE)
    if resolver_case == "canceled":
        assert result["reached_lane_launch"] is False and result["exception"] == "OperationCanceledException"
        return
    assert result["reached_lane_launch"] is True and result["exception"] is None
    expected = "verified" if resolver_case == "live" else "unavailable"
    assert result[phase]["status"] == expected
    assert ("Grok stays optional" in text) is (expected == "unavailable")


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
def test_a_stop_warning_preference_machine_without_grok_still_reaches_the_lane_launch(ps, tmp_path):
    result, _ = run_resolution(ps, tmp_path, "missing_cli", "missing", [PREFLIGHT, GROK_CALLER, POST_UPDATE],
                               preamble=STOP_PREFERENCE)
    assert result["reached_lane_launch"] is True and result["exception"] is None
    assert result["preflight"]["status"] == "unavailable" and result["model"]["status"] == "unavailable"
    assert result["update"]["update_status"] == "failed"


def test_every_optional_grok_notice_passes_warning_action_continue():
    source = START_ALL.read_text(encoding="utf-8-sig")
    notices = [m.start() for m in re.finditer(r"Grok stays optional and the native lanes still launch", source)]
    assert len(notices) == 2
    for position in notices:
        line_start = source.rindex("Write-Warning", 0, position)
        assert source[line_start:position].startswith("Write-Warning -WarningAction Continue (")


def test_the_apply_path_uses_the_optional_update_before_the_record_and_the_lanes():
    source = START_ALL.read_text(encoding="utf-8-sig")
    caller = apply_block(*GROK_CALLER)
    assert "$grokUpdateRecord = Invoke-WdGrokCliUpdateOptional" in caller
    assert re.search(r"Invoke-WdGrokCliUpdate\b(?!Optional)", caller) is None
    update = source.index(GROK_CALLER[0])
    assert update < source.index("$cliVersionRecord =") < source.index("Start-Process -FilePath $wtPath")
    assert "grok_build = $grokUpdateRecord" in source
    # Both resolver calls go through the optional wrapper; no direct call can abort the fleet.
    assert source.index(PREFLIGHT[0]) < update < source.index(POST_UPDATE[0]) < source.index(
        "Start-Process -FilePath $wtPath")
    assert re.findall(r"& \$resolver\b", source) == []
    assert "throw 'Grok preflight returned no verified model record'" not in source

@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("preference", ["", "stop"], ids=["default", "stop_warning_preference"])
@pytest.mark.parametrize("case", FATAL_REFUSALS)
def test_a_wrapper_package_refusal_stops_the_cold_start_and_is_not_recorded_as_optional(ps, case, preference):
    body = (STOP_PREFERENCE if preference else "") + OPTIONAL_BODY
    result, completed = run_case(ps, case, body)
    assert result["aborted"] is True and result["record"] is None and result["calls"] == 1
    assert result["exception"] == "RuntimeException"
    assert "Grok stays optional" not in completed.stdout + completed.stderr   # never shown as an optional failure


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: p.rsplit("\\", 1)[-1])
@pytest.mark.parametrize("case", ["network", "busy", "nonzero_text", "malformed", "array"])
def test_success_twin_helper_reported_failures_stay_optional_beside_the_fatal_refusals(ps, case):
    result, _ = run_case(ps, case, OPTIONAL_BODY)
    assert result["aborted"] is False and result["record"]["update_status"] == "failed"
    assert result["record"]["error_kind"] in ("update_failed_or_blocked", "invalid_receipt")
