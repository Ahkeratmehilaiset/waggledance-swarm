"""S15 containment contract. Only AST-selected functions/actual finally run, with strict fakes.

Never execute the adapter body, native type, process/lock/CIM seams or installed code.
These synthetic author tests establish control flow only, not complete native membership.
"""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT, _run_powershell

ADAPTER = REBOOT / "Start-WdMindShadowJob.ps1"
FUNCTIONS = (
    "New-OwnedShadowWatchState", "Add-OwnedShadowDescendant",
    "Update-OwnedShadowTree", "Get-OwnedShadowCutReason", "Stop-OwnedShadowTree",
    "Open-OwnedShadowChildRecord", "Complete-OwnedShadowContainment",
    "New-WindowsOwnedShadowSeams",
)


def _source_functions():
    path = str(ADAPTER).replace("'", "''")
    names = ",".join("'" + name + "'" for name in FUNCTIONS)
    return r"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
foreach($name in @('Get-CimInstance','Get-WmiObject','Get-Process','Stop-Process',
 'Start-Process','Start-Job','Invoke-Command','Invoke-Expression','Add-Type')){
 Set-Item -Path "function:$name" -Value ([scriptblock]::Create("throw 'unexpected native seam: $name'"))
}
function Test-WdShadowLiveDirLock {throw 'unexpected real lock seam'}
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('ADAPTER',[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'adapter_parse_error'}
$selected=@(NAMES)
foreach($node in $ast.EndBlock.Statements){
 if($node-is[Management.Automation.Language.FunctionDefinitionAst]-and$node.Name-cin$selected){
  . ([scriptblock]::Create($node.Extent.Text))
 }
}
function Add-WdShadowUnknown {
 param([Collections.IList]$Unknown,[string]$Field,[string]$Reason)
 [void]$Unknown.Add(@{field=$Field;reason=$Reason})
}
""".replace("ADAPTER", path).replace("NAMES", names)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_retained_tree_stop_and_identity_refusals_with_only_fake_seams(ps):
    driver = r'''
SOURCE_FUNCTIONS
function Invoke-Case([string]$Name) {
    $root=@{pid=10;birth_ticks=100;parent_pid=1;name='controller';live=$true}
    $python=@{pid=20;birth_ticks=200;parent_pid=10;name='python';live=$true}
    $native=@{pid=15;birth_ticks=150;parent_pid=10;name='native';live=$true}
    if($Name-eq'three_level_survivor'){$python.parent_pid=15}
    $trace=@{killed=[Collections.Generic.List[int]]::new();clock=0.0;discover=$false}
    $state=New-OwnedShadowWatchState $root
    $seams=@{
        alive={param($r) if($Name-eq'observe_error'){throw 'fake_query_failure'};return $r.live}.GetNewClosure()
        exit_ticks={param($r) return 500L}
        children={param($r)
            if($Name-eq'three_level_survivor'){
                if($r.pid-eq10){$native}elseif($r.pid-eq15){$python}
            }elseif($r.pid-eq10-and($Name-ne'cleanup_late_child'-or$trace.discover)){
                $python;if($Name-eq'partial_child_batch'){throw 'late_child_query_failure'}
            }
        }.GetNewClosure()
        same_identity={param($r) return ($Name-ne'identity_changed')}.GetNewClosure()
        kill_tree={param($r) $trace.killed.Add($r.pid);if($Name-eq'kill_error'){throw 'fake_kill_failure'};if($Name-ne'still_live'){$r.live=$false};if($Name-eq'budget'){$trace.clock+=2}}.GetNewClosure()
        pause={$trace.clock+=1}.GetNewClosure()
        now_seconds={return $trace.clock}.GetNewClosure()
        lock={return @{acquired=($Name-ne'lock_busy');released=($Name-ne'lock_busy');error=$null}}.GetNewClosure()
    }
    if($Name-eq'pid_reuse'){
        Add-OwnedShadowDescendant $state $root $python
        try{Add-OwnedShadowDescendant $state $root @{pid=20;birth_ticks=300;parent_pid=10}}catch{return @{refusal=$_.Exception.Message}}
        throw 'reuse_not_refused'
    }
    if($Name-eq'wrong_parent'){$python.parent_pid=99}
    if($Name-eq'birth_after_dead_parent'){$root.live=$false;$python.birth_ticks=600}
    Update-OwnedShadowTree $state $seams
    if($Name-eq'root_exited_survivor'){$root.live=$false}
    if($Name-eq'three_level_survivor'){$root.live=$false;$native.live=$false}
    if($Name-eq'cleanup_late_child'){$root.live=$false;$trace.discover=$true}
    if($Name-eq'already_gone'){$root.live=$false;$python.live=$false}
    $elapsed=if($Name-eq'deadline'){600.0}else{1.0}
    $external=if($Name-eq'writer_error'){'first_writer_error'}else{''}
    $reason=Get-OwnedShadowCutReason $state $seams $elapsed 600 $external
    if(-not$reason){$state.cut_reason='test_cleanup'}
    $result=Stop-OwnedShadowTree $state $seams 1
    return @{reason=$reason;result=$result;killed=$trace.killed.ToArray()}
}
$cases=@{}
foreach($name in @('root_exited_survivor','deadline','writer_error','pid_reuse','wrong_parent',
 'birth_after_dead_parent','identity_changed','observe_error','kill_error','still_live','lock_busy','already_gone','budget','partial_child_batch','three_level_survivor','cleanup_late_child')){
 $cases[$name]=Invoke-Case $name
}
$cases|ConvertTo-Json -Depth 12 -Compress
'''.replace("SOURCE_FUNCTIONS", _source_functions())
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    rows = json.loads(done.stdout)
    survivor = rows["root_exited_survivor"]
    assert survivor["reason"] == "controller_exit_cleanup"
    assert survivor["killed"] == [20]
    assert survivor["result"]["known_tree_and_lock_clean"]
    assert not survivor["result"]["complete_job_membership_proven"]
    assert survivor["result"]["unobserved_descendants"] == "UNKNOWN"
    assert rows["deadline"]["reason"] == "deadline_cut"
    assert rows["writer_error"]["reason"] == "first_writer_error"
    assert rows["pid_reuse"]["refusal"] == "observed_pid_reuse"
    for key in ("wrong_parent", "birth_after_dead_parent", "observe_error"):
        assert rows[key]["reason"] == "owned_observation_unknown", rows[key]
        assert not rows[key]["result"]["known_tree_and_lock_clean"], rows[key]
    for key in ("identity_changed", "kill_error", "still_live", "lock_busy"):
        assert not rows[key]["result"]["known_tree_and_lock_clean"], rows[key]
    assert rows["identity_changed"]["killed"] == []
    assert rows["already_gone"]["killed"] == []
    assert rows["already_gone"]["result"]["known_tree_and_lock_clean"]
    assert rows["budget"]["killed"] == [10]
    assert rows["budget"]["result"]["known_live_pids"] == [20]
    assert not rows["budget"]["result"]["known_tree_and_lock_clean"]
    assert rows["budget"]["result"]["lock"]["error"] == "cleanup_budget_before_lock"
    assert rows["partial_child_batch"]["reason"] == "owned_observation_unknown"
    assert rows["partial_child_batch"]["killed"] == [10, 20]
    assert not rows["partial_child_batch"]["result"]["known_tree_and_lock_clean"]
    assert rows["three_level_survivor"]["killed"] == [20]
    assert rows["three_level_survivor"]["result"]["known_tree_and_lock_clean"]
    assert rows["cleanup_late_child"]["killed"] == [20]
    assert rows["cleanup_late_child"]["result"]["known_tree_and_lock_clean"]
    assert rows["wrong_parent"]["killed"] == [10]
    assert rows["birth_after_dead_parent"]["killed"] == []



@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("cleanup_throws", [False, True])
def test_actual_finally_reaches_known_survivor_and_disposes_job_even_on_cleanup_error(ps, cleanup_throws):
    driver = _source_functions() + r"""
# Select the top-level owner try, not its nested Dispose-protecting finally.
$nodes=@($ast.EndBlock.Statements|Where-Object{
 $_-is[Management.Automation.Language.TryStatementAst]-and$_.Finally-and
 $_.Finally.Extent.Text.Contains('$managed.Dispose()')})
if($nodes.Count-ne1){throw 'managed_finally_not_unique'}
$text=$nodes[0].Finally.Extent.Text
$body=[scriptblock]::Create($text.Substring(1,$text.Length-2))
$trace=@{killed=[Collections.Generic.List[int]]::new();disposed=$false;time=0.0}
$root=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$false;exit_ticks=500;final_scan_done=$true}
$child=@{pid=20;birth_ticks=200;parent_pid=10;name='python';live=$true;exit_ticks=$null;final_scan_done=$false}
$ownedState=@{root=$root;records=[Collections.Generic.List[object]]::new()
 errors=[Collections.Generic.List[string]]::new();retired=[Collections.Generic.List[object]]::new();cut_reason='test'}
$ownedState.records.Add($root);$ownedState.records.Add($child)
$ownedSeams=@{
 alive={param($r) $r.live};exit_ticks={param($r) 500L};children={param($r)}
 same_identity={param($r) $true}
 kill_tree={param($r) $trace.killed.Add($r.pid);$r.live=$false}.GetNewClosure()
 pause={$trace.time+=1}.GetNewClosure();now_seconds={$trace.time}.GetNewClosure()
 lock={@{attempted=$true;acquired=$true;released=$true;error=$null}}
}
if(CLEANUP_THROWS){$ownedSeams.lock={throw 'synthetic_lock_error'}}
$ownedCleanup=$null;$unknown=[Collections.ArrayList]::new()
$clock=[pscustomobject]@{};$clock|Add-Member ScriptProperty Elapsed {[TimeSpan]::Zero}
$stopAt=$null;$StopConfirmationWaitSeconds=30
$managed=[pscustomobject]@{}
$managed|Add-Member ScriptMethod Dispose {$trace.disposed=$true}.GetNewClosure()
. $body
@{live=$child.live;killed=$trace.killed.ToArray();disposed=$trace.disposed;
 cleanup=$ownedCleanup;unknown=@($unknown)}|ConvertTo-Json -Depth 10 -Compress
""".replace("CLEANUP_THROWS", "$true" if cleanup_throws else "$false")
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    assert row["killed"] == [20], row
    assert row["live"] is False and row["disposed"] is True, row
    assert row["cleanup"]["complete_job_membership_proven"] is False, row
    assert row["cleanup"]["unobserved_descendants"] == "UNKNOWN", row
    if cleanup_throws:
        assert row["cleanup"]["known_tree_and_lock_clean"] is False, row
        assert row["unknown"], row


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_held_child_identity_is_checked_again_and_unadopted_handle_disposed(ps):
    driver = _source_functions() + r"""
$birth=[datetime]::new(2026,10,27,12,0,0,[DateTimeKind]::Utc)
$trace=@{held=$false;disposed=$false;steps=[Collections.Generic.List[string]]::new()}
$fake=[pscustomobject]@{Id=20;trace=$trace;birth=$birth}
$fake|Add-Member ScriptProperty SafeHandle {$this.trace.held=$true;$this.trace.steps.Add('handle');1}
$fake|Add-Member ScriptProperty StartTime {if(-not$this.trace.held){throw 'birth_before_handle'};$this.trace.steps.Add('birth');$this.birth}
$fake|Add-Member ScriptProperty ProcessName {if(-not$this.trace.held){throw 'name_before_handle'};'python'}
$fake|Add-Member ScriptMethod Dispose {$this.trace.disposed=$true}
$initial=[pscustomobject]@{ProcessId=20;ParentProcessId=10;CreationDate=$birth;Name='python.exe'}
$current=[pscustomobject]@{ProcessId=20;ParentProcessId=99;CreationDate=$birth;Name='python.exe'}
$reason=''
try{
 Open-OwnedShadowChildRecord $initial {$fake}.GetNewClosure() {
  param($ownedProcessId)
  if(-not$trace.held){throw 'query_before_handle'};$trace.steps.Add('fresh_row');$current
 }.GetNewClosure()|Out-Null
}catch{$reason=$_.Exception.Message}
@{reason=$reason;disposed=$trace.disposed;steps=$trace.steps.ToArray()}|ConvertTo-Json -Compress
"""
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    assert row == {"reason": "CIM_process_identity_mismatch", "disposed": True,
                   "steps": ["handle", "birth", "fresh_row"]}, row


def test_adapter_wires_observation_and_reports_only_known_membership():
    text = ADAPTER.read_text(encoding="utf-8")
    intent = text.index("Publish-WdShadowRecord -Directory $runDir -Name 'run_intent'")
    assert text.index("$ownedSeams = New-WindowsOwnedShadowSeams") < intent
    loop = text[text.index("while (-not $forceStop"):text.index("if (-not $drained) {")]
    assert "Update-OwnedShadowTree $ownedState $ownedSeams" in loop
    assert loop.index("Update-OwnedShadowTree") < loop.index("$managed.Wait($slice)")
    assert "owned_containment = $ownedCleanup" in text
    assert "complete_job_membership_proven=$false" in text
    assert "unobserved_descendants='UNKNOWN'" in text
    assert "if (-not $ownedCleanup.known_tree_and_lock_clean)" in text


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_factory_captures_fixed_lock_protocol_without_calling_any_os_seam(ps):
    # Reflection only checks capability. All process/CIM/kill callbacks remain
    # uncalled, and the lock function is replaced before constructing closures.
    driver = _source_functions() + r"""
function Test-WdShadowLiveDirLock {
 param([string]$Path)
 return @{path=$Path;lock_byte=$LockByte;acquired=$true;released=$true}
}
$factoryError='';$lockResult=$null
try{
 $seams=New-WindowsOwnedShadowSeams -ProducerLock 'synthetic-producer.lock' -LockByte 1073741824
 $lockResult=& $seams.lock
}catch{$factoryError=$_.Exception.Message}
@{error=$factoryError;lock=$lockResult;capable=($null-ne[Diagnostics.Process].GetMethod('Kill',[type[]]@()))}|
 ConvertTo-Json -Depth 5 -Compress
"""
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    if row["capable"]:
        assert row["error"] == "", row
        assert row["lock"] == {"path": "synthetic-producer.lock",
                               "lock_byte": 1073741824,
                               "acquired": True, "released": True}, row
    else:
        assert row["error"] == "Kill_verified_handle_unavailable" and row["lock"] is None, row


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_factory_kill_callback_does_not_authorize_unverified_os_descendants(ps):
    driver = _source_functions() + r"""
function Test-WdShadowLiveDirLock {param([string]$Path) throw 'lock_must_not_run'}
$errorText='';$row=$null
try{
 $seams=New-WindowsOwnedShadowSeams -ProducerLock 'synthetic-producer.lock' -LockByte 1073741824
 $fake=[pscustomobject]@{kill_calls=0;tree_flag=$null;unverified_child_touched=$false}
 $fake|Add-Member ScriptMethod Kill {
  param([object]$EntireProcessTree=$null)
  $this.kill_calls++
  $this.tree_flag=$EntireProcessTree
  if($EntireProcessTree-eq$true){$this.unverified_child_touched=$true}
 }
 & $seams.kill_tree @{pid=20;birth_ticks=200;process=$fake}
 $row=@{kill_calls=$fake.kill_calls;tree_flag=$fake.tree_flag;unverified_child_touched=$fake.unverified_child_touched}
}catch{$errorText=$_.Exception.Message}
@{error=$errorText;row=$row}|ConvertTo-Json -Depth 5 -Compress
"""
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    assert row["error"] == "", row
    assert row["row"]["kill_calls"] == 1, row
    assert row["row"]["tree_flag"] is None, row
    assert row["row"]["unverified_child_touched"] is False, row


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_cleanup_rescans_exited_parent_before_claiming_known_chain_clean(ps):
    driver = _source_functions() + r"""
$root=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$true}
$child=@{pid=20;birth_ticks=200;parent_pid=10;name='late-child';live=$true}
$trace=@{revealed=$false;time=0.0;killed=[Collections.Generic.List[int]]::new()}
$state=New-OwnedShadowWatchState $root
$seams=@{
 alive={param($r) $r.live};exit_ticks={param($r) 500L}
 children={param($r) if($r.pid-eq10-and$trace.revealed){$child}}.GetNewClosure()
 same_identity={param($r) $true}
 kill_tree={param($r)
  $trace.killed.Add($r.pid);$r.live=$false
  if($r.pid-eq10){$trace.revealed=$true}
 }.GetNewClosure()
 pause={$trace.time+=0.1}.GetNewClosure()
 now_seconds={$trace.time}.GetNewClosure()
 lock={@{acquired=$true;released=$true;error=$null}}
}
$state.cut_reason='synthetic_cleanup'
$result=Stop-OwnedShadowTree $state $seams 10
@{child_live=$child.live;killed=$trace.killed.ToArray();result=$result}|ConvertTo-Json -Depth 8 -Compress
"""
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    assert row["child_live"] is False, row
    assert row["killed"] == [10, 20], row
    assert row["result"]["known_tree_and_lock_clean"] is True, row
    assert row["result"]["complete_job_membership_proven"] is False, row
    assert row["result"]["unobserved_descendants"] == "UNKNOWN", row
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_record_budget_counts_simultaneous_handles_not_sequential_history(ps):
    # Real adapter functions, fake processes only. Nine non-overlapping batches
    # exceed the old cumulative cap while never retaining >257 live identities.
    driver = _source_functions() + r"""
function New-FakeOwnedChild([int]$Id,[long]$Birth,[int]$Parent,$Trace,[bool]$Live=$true) {
 $p=[pscustomobject]@{trace=$Trace;disposed=$false;fail_dispose=$false}
 $p|Add-Member ScriptMethod Dispose {
  if($this.fail_dispose){throw 'fake_dispose_failure'}
  if($this.disposed){throw 'fake_double_dispose'}
  $this.disposed=$true;$this.trace.disposed++
 }
 return @{pid=$Id;birth_ticks=$Birth;parent_pid=$Parent;name='synthetic';live=$Live;process=$p}
}
$trace=@{children=@();disposed=0;time=0.0}
$root=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$true}
$state=New-OwnedShadowWatchState $root
$seams=@{
 alive={param($r) $r.live};exit_ticks={param($r) ($r.birth_ticks+100000L)}
 children={param($r) if($r.pid-eq10){$trace.children}}.GetNewClosure()
 same_identity={param($r) $true}
 kill_tree={param($r) $r.live=$false}
 pause={$trace.time+=0.1}.GetNewClosure()
 now_seconds={$trace.time}.GetNewClosure()
 lock={@{acquired=$true;released=$true;error=$null}}
}
$maximum=0
for($batch=0;$batch-lt9;$batch++){
 $children=@()
 for($i=0;$i-lt256;$i++){
  $number=$batch*256+$i
  $children+=New-FakeOwnedChild (1000+$number) (200L+$number) 10 $trace
 }
 $trace.children=$children
 Update-OwnedShadowTree $state $seams
 $maximum=[Math]::Max($maximum,$state.records.Count)
 $trace.children=@()
 foreach($child in $children){$child.live=$false}
 Update-OwnedShadowTree $state $seams
}
$sequential=@{errors=$state.errors.ToArray();retained=$state.records.Count;maximum=$maximum;
 disposed=$trace.disposed;retired=$state['retired_owned_count']}
$result=Stop-OwnedShadowTree $state $seams 30
$sequential.cleanup=$result

# Simultaneous pressure still refuses: 2048 children plus the fixed root.
$trace2=@{children=@();disposed=0}
$root2=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$true}
$state2=New-OwnedShadowWatchState $root2
for($i=0;$i-lt2048;$i++){
 $trace2.children+=@{pid=(1000+$i);birth_ticks=(200L+$i);parent_pid=10;name='synthetic';live=$true}
}
$seams2=@{alive={param($r) $r.live};exit_ticks={param($r) ($r.birth_ticks+100000L)}
 children={param($r) if($r.pid-eq10){$trace2.children}}.GetNewClosure()}
Update-OwnedShadowTree $state2 $seams2

# Retire a final-scanned dead parent only AFTER adopting its still-live child.
$trace3=@{children=@();disposed=0;time=0.0}
$root3=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$true}
$parent=New-FakeOwnedChild 20 200 10 $trace3 $false
$survivor=New-FakeOwnedChild 30 300 20 $trace3
$state3=New-OwnedShadowWatchState $root3
$seams3=@{alive={param($r) $r.live};exit_ticks={param($r) 10000L}
 children={param($r) if($r.pid-eq10){$parent}elseif($r.pid-eq20){$survivor}}.GetNewClosure()}
Update-OwnedShadowTree $state3 $seams3
$parentCase=@{ids=@($state3.records|ForEach-Object{$_.pid});disposed=$trace3.disposed;
 errors=$state3.errors.ToArray();child_disposed=$survivor.process.disposed}

# Failed final scan and failed handle disposal must retain the record and error.
$failedCases=@{}
foreach($mode in @('scan','dispose')){
 $tr=@{disposed=0}
 $rt=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$true}
 $dead=New-FakeOwnedChild 20 200 10 $tr $false
 if($mode-eq'dispose'){$dead.process.fail_dispose=$true}
 $st=New-OwnedShadowWatchState $rt
 $sm=@{alive={param($r) $r.live};exit_ticks={param($r) 10000L}
 children={param($r)
  if($r.pid-eq10){$dead}elseif($mode-eq'scan'){throw 'fake_final_scan_failed'}
 }.GetNewClosure()}
 Update-OwnedShadowTree $st $sm
 $failedCases[$mode]=@{ids=@($st.records|ForEach-Object{$_.pid});disposed=$tr.disposed;errors=$st.errors.ToArray()}
}
@{sequential=$sequential;simultaneous=@{retained=$state2.records.Count;errors=$state2.errors.ToArray()};
 parent=$parentCase;failed=$failedCases}|ConvertTo-Json -Depth 12 -Compress
"""
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    sequential = row["sequential"]
    assert sequential["errors"] == [], sequential
    assert sequential["retained"] == 1 and sequential["maximum"] <= 257, sequential
    assert sequential["disposed"] == 2304 and sequential["retired"] == 2304, sequential
    assert sequential["cleanup"]["known_tree_and_lock_clean"] is True, sequential
    assert sequential["cleanup"]["complete_job_membership_proven"] is False
    assert sequential["cleanup"]["unobserved_descendants"] == "UNKNOWN"
    assert row["simultaneous"]["retained"] == 2048, row
    assert any("owned_record_budget" in error for error in row["simultaneous"]["errors"]), row
    assert row["parent"] == {"ids": [10, 30], "disposed": 1,
                             "errors": [], "child_disposed": False}, row
    for mode in ("scan", "dispose"):
        assert row["failed"][mode]["ids"] == [10, 20], row
        assert row["failed"][mode]["disposed"] == 0, row
        assert row["failed"][mode]["errors"], row
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_gone_history_is_bounded_without_cumulative_stop_or_hidden_errors(ps):
    driver = _source_functions() + r"""
$root=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=$true}
$trace=@{rows=@();time=0.0}
for($i=0;$i-lt2304;$i++){
 $trace.rows+=@{pid=(1000+$i);birth_ticks=(200L+$i);parent_pid=10;name='synthetic';gone=$true}
}
$state=New-OwnedShadowWatchState $root
$seams=@{alive={param($r) $r.live};exit_ticks={param($r) 10000L}
 children={param($r) if($r.pid-eq10){$trace.rows}}.GetNewClosure()
 same_identity={param($r) $true};kill_tree={param($r) $r.live=$false}
 pause={$trace.time+=0.1}.GetNewClosure();now_seconds={$trace.time}.GetNewClosure()
 lock={@{acquired=$true;released=$true;error=$null}}}
Update-OwnedShadowTree $state $seams
$observed=@{errors=$state.errors.ToArray();count=$state['retired_gone_count'];
 retained=$state.retired.Count;first=$state.retired[0].pid;last=$state.retired[$state.retired.Count-1].pid}
$trace.rows=@()
$observed.cleanup=Stop-OwnedShadowTree $state $seams 30

$bad=@{}
foreach($mode in @('wrong_parent','after_parent_exit','partial_OS_error')){
 $rt=@{pid=10;birth_ticks=100;parent_pid=1;name='root';live=($mode-ne'after_parent_exit')}
 $candidate=@{pid=20;birth_ticks=200;parent_pid=10;name='synthetic';gone=$true}
 if($mode-eq'wrong_parent'){$candidate.parent_pid=99}
 if($mode-eq'after_parent_exit'){$candidate.birth_ticks=600}
 $st=New-OwnedShadowWatchState $rt
 $sm=@{alive={param($r) $r.live};exit_ticks={param($r) 500L}
 children={param($r)
  if($r.pid-eq10){$candidate;if($mode-eq'partial_OS_error'){throw 'fake_CIM_error_after_row'}}
 }.GetNewClosure()}
 Update-OwnedShadowTree $st $sm
 $bad[$mode]=@{errors=$st.errors.ToArray();retained=$st.retired.Count}
}
@{observed=$observed;bad=$bad}|ConvertTo-Json -Depth 12 -Compress
"""
    done = _run_powershell(driver, executable=ps, check=False)
    assert done.returncode == 0, done.stderr
    row = json.loads(done.stdout)
    observed = row["observed"]
    assert observed["errors"] == [], observed
    assert observed["count"] == 2304 and observed["retained"] == 2048, observed
    assert observed["first"] == 1256 and observed["last"] == 3303, observed
    assert observed["cleanup"]["positively_gone_observation_count"] == 2304, observed
    assert observed["cleanup"]["positively_gone_observations_truncated"] is True, observed
    assert observed["cleanup"]["known_tree_and_lock_clean"] is True, observed
    assert observed["cleanup"]["complete_job_membership_proven"] is False
    assert observed["cleanup"]["unobserved_descendants"] == "UNKNOWN"
    for mode in ("wrong_parent", "after_parent_exit", "partial_OS_error"):
        assert row["bad"][mode]["errors"], row
    assert row["bad"]["wrong_parent"]["retained"] == 0, row
    assert row["bad"]["after_parent_exit"]["retained"] == 0, row
    assert row["bad"]["partial_OS_error"]["retained"] == 1, row
