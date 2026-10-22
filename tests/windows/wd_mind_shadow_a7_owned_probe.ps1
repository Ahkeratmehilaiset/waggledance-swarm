#requires -Version 5.1
<#
Source-only future A7 owned-job runner. Dot-sourcing only defines functions.
Explicit opt-in prevents accidents; it grants NO native/Medium/install authority.
The measured cumulative count is contained job HISTORY, not image identity,
broker/OS isolation, or a global no-process proof. Counts1/2 qualify only with
the exact pinned planner command/adapter and intended refusal. Native calls may
block; bounded polling is not preemption or a whole-deadline guarantee.
#>

function Get-WdA7Sha256 {
    param([byte[]]$Bytes)
    $hash=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-','') }
    finally { $hash.Dispose() }
}

function Read-WdA7PinnedFile {
    param([string]$Path,[string]$ExpectedSha256,[switch]$AsText)
    if (-not [IO.Path]::IsPathRooted($Path) -or $ExpectedSha256 -cnotmatch '^[A-Fa-f0-9]{64}$') { throw 'refused: complete explicit file pins required' }
    $item=Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.PSIsContainer) { throw 'refused: expected regular pinned file' }
    $cursor=$item
    while ($null-ne$cursor) {
        if ($cursor.Attributes-band[IO.FileAttributes]::ReparsePoint) { throw 'refused: pinned file reparse ancestry' }
        if ($cursor-is[IO.FileInfo]) { $cursor=$cursor.Directory } else { $cursor=$cursor.Parent }
    }
    $bytes=[IO.File]::ReadAllBytes($item.FullName)
    $actual=Get-WdA7Sha256 $bytes
    if ($actual-ine$ExpectedSha256) { throw 'refused: pinned file hash' }
    $text=$null
    if ($AsText) { $text=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes).Replace("`r`n","`n") }
    return [pscustomobject]@{path=$item.FullName;sha256=$actual;text=$text}
}

function Get-WdA7ObservedIdentity {
    param([int]$ProbePid)
    $process=Get-Process -Id $ProbePid -ErrorAction Stop
    # Never replace a missing creation observation by wall-clock time.
    return @{pid=[string]$ProbePid;creation_utc=$process.StartTime.ToUniversalTime().ToString('o',[Globalization.CultureInfo]::InvariantCulture)}
}

function Get-WdA7Entries {
    param([string]$Path)
    if (-not [IO.Directory]::Exists($Path)) { throw 'refused: prepared A7 run root missing' }
    $names=[string[]]@([IO.Directory]::GetFileSystemEntries($Path)|ForEach-Object{[IO.Path]::GetFileName($_)})
    [Array]::Sort($names,[StringComparer]::Ordinal)
    return ,$names
}

function New-WdA7ManagedProcess {
    param($HostPath,$Arguments,$WorkingDirectory,$InputPath,$OutputPath,$ErrorPath)
    return [WdManagedTurnProcess]::new($HostPath,[string[]]$Arguments,$WorkingDirectory,$InputPath,$OutputPath,$ErrorPath)
}

function Test-WdA7Count {
    param($Value)
    return (($Value-is[int]-or$Value-is[uint32]-or$Value-is[long])-and$Value-ge0)
}

function Get-WdA7PreparationAttributes {
    param([string]$Path)
    return [IO.File]::GetAttributes($Path)
}

function Get-WdA7PreparationLinkTarget {
    param([string]$Path)
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if($item.LinkType-cne'Junction'-or@($item.Target).Count-ne1){throw 'not one junction target'}
    return [IO.Path]::GetFullPath([string]$item.Target[0]).TrimEnd('\')
}

function Assert-WdA7PreparedState {
    # Observe only: never create a switch, change ACLs or create/fix a junction.
    # These point-in-time checks do not prevent a same-user filesystem race.
    param($Plan,[string]$CaseRoot,[string]$LoopPath,[string]$ExpectedSourceCommit)
    try {
        $root=[IO.Path]::GetFullPath($CaseRoot).TrimEnd('\')
        if($root-notmatch'^C:\\.*\\\.codex-audit\\'){throw 'case root not synthetic audit-local'}
        for($current=$root;$current;$current=[IO.Path]::GetDirectoryName($current)){
            $attributes=Get-WdA7PreparationAttributes $current
            if(-not($attributes-band[IO.FileAttributes]::Directory)-or($attributes-band[IO.FileAttributes]::ReparsePoint)){throw 'case ancestry not plain directory'}
        }
        $expectedRoot=$root
        if($Plan.prepare-ceq'junction'){$expectedRoot=Join-Path $root 'link\run'}
        if(-not[string]::Equals([IO.Path]::GetFullPath($Plan.run_root).TrimEnd('\'),$expectedRoot,[StringComparison]::OrdinalIgnoreCase)){throw 'run root mismatch'}
        $names=Get-WdA7Entries $root
        switch -CaseSensitive ($Plan.prepare) {
            'none' {if($names.Count){throw 'plain case is not empty'}}
            'off_switch' {
                if($names.Count-ne1-or$names[0]-cne'mind-shadow.off'){throw 'switch entry missing or extra'}
                $path=Join-Path $root 'mind-shadow.off'
                $attributes=Get-WdA7PreparationAttributes $path
                if(($attributes-band([IO.FileAttributes]::Directory-bor[IO.FileAttributes]::ReparsePoint))-or[IO.FileInfo]::new($path).Length-ne0){throw 'switch not ordinary empty file'}
            }
            'off_switch_denied' {
                if($names.Count-ne1-or$names[0]-cne'mind-shadow.off'){throw 'denied switch entry missing or extra'}
                $denied=$false
                try {[void](Get-WdA7PreparationAttributes (Join-Path $root 'mind-shadow.off'))}
                catch {if($_.Exception.GetBaseException()-is[UnauthorizedAccessException]){$denied=$true}else{throw}}
                if(-not$denied){throw 'switch attributes are not denied'}
            }
            'junction' {
                if($names.Count-ne2-or$names-cnotcontains'link'-or$names-cnotcontains'target'){throw 'junction preparation entries'}
                $target=Join-Path $root 'target';$run=Join-Path $target 'run';$link=Join-Path $root 'link'
                foreach($plain in @($target,$run)){
                    $attributes=Get-WdA7PreparationAttributes $plain
                    if(-not($attributes-band[IO.FileAttributes]::Directory)-or($attributes-band[IO.FileAttributes]::ReparsePoint)){throw 'junction target not plain'}
                }
                $attributes=Get-WdA7PreparationAttributes $link
                if(-not($attributes-band[IO.FileAttributes]::ReparsePoint)-or-not($attributes-band[IO.FileAttributes]::Directory)){throw 'expected directory junction'}
                if(-not[string]::Equals((Get-WdA7PreparationLinkTarget $link),$target,[StringComparison]::OrdinalIgnoreCase)){throw 'foreign junction target'}
                $children=Get-WdA7Entries $target
                if($children.Count-ne1-or$children[0]-cne'run'-or(Get-WdA7Entries $run).Count){throw 'junction target not fresh'}
            }
            default {throw 'unknown preparation'}
        }
        if($Plan.preload-ceq'lane_loop_type'){
            $literal='C:\Python\wd-reboot-bundles\'+$ExpectedSourceCommit+'\Invoke-WdLaneTurnLoop.ps1'
            if(-not[string]::Equals([IO.Path]::GetFullPath($LoopPath),$literal,[StringComparison]::OrdinalIgnoreCase)){throw 'preloaded initializer path differs from pinned literal'}
        } elseif($Plan.preload-cne'none'){throw 'unknown preload'}
    } catch {throw ('refused: A7 preparation: '+$_.Exception.Message)}
}

function Invoke-WdA7OwnedCommand {
    # Internal mechanics: public entry validates all provenance/context first.
    # Synthetic tests replace ONLY this factory/identity boundary with strict fakes.
    param([string]$HostPath,[string]$LiteralAdapterCommand,[string]$WorkingDirectory,
          [string]$EvidenceDirectory,[string]$RunRoot,[string]$Name,[string]$RefusalPattern,[string]$AttemptId,
          [ValidateRange(1,30)][int]$WaitSeconds=10,[ValidateRange(1,10)][int]$DrainSeconds=5,
          [ValidateRange(1024,1048576)][int]$MaxOutputBytes=32768)
    $names=@('integrity_mismatch','user_sid_mismatch','preloaded_type','off_switch_before_start','off_switch_denied_read','disk_floor_before_start','reparse_ancestry')
    if ($Name-cnotin$names -or -not $LiteralAdapterCommand -or -not $HostPath -or -not $AttemptId -or -not $RefusalPattern -or
        -not [IO.Directory]::Exists($EvidenceDirectory)) { throw 'refused: complete internal A7 command context required' }
    # Closure hypothesis, NOT a measured host-independent baseline: the pinned
    # preloaded command exits before adapter whoami; other guards run probe+whoami.
    # PS5 Add-Type/csc/console may add processes: extra remains FAIL, lower UNKNOWN.
    $expected=if($Name-ceq'preloaded_type'){1}else{2}
    $before=Get-WdA7Entries $RunRoot
    $inputPath=Join-Path $EvidenceDirectory 'stdin.once'
    $stream=[IO.File]::Open($inputPath,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    $stream.Dispose()
    $stdoutPath=Join-Path $EvidenceDirectory 'stdout.once.log'
    $stderrPath=Join-Path $EvidenceDirectory 'stderr.once.log'
    $managed=$null;$identity=$null;$probePid=$null;$exit=$null;$total=$null;$active=$null
    $out=$null;$err=$null;$bytes=$null;$drained=$false;$timedOut=$false;$stopped=$false;$newEntries=$null
    $errors=New-Object System.Collections.ArrayList
    try {
        $arguments=[string[]]@('-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-Command',$LiteralAdapterCommand)
        $managed=New-WdA7ManagedProcess $HostPath $arguments $WorkingDirectory $inputPath $stdoutPath $stderrPath
        $probePid=$managed.Pid
        try { $identity=Get-WdA7ObservedIdentity $probePid } catch { [void]$errors.Add('probe_creation_unknown') }
        $clock=[Diagnostics.Stopwatch]::StartNew()
        while (-not $managed.Wait(0)) {
            $bytes=(Get-Item -LiteralPath $stdoutPath).Length+(Get-Item -LiteralPath $stderrPath).Length
            if ($bytes-gt$MaxOutputBytes) { [void]$errors.Add('output_budget');break }
            if ($clock.Elapsed.TotalSeconds-ge$WaitSeconds) { $timedOut=$true;[void]$errors.Add('timeout');break }
            Start-Sleep -Milliseconds 10
        }
        # Stop the SAME owned job on timeout/budget OR to drain helpers after root exit.
        $active=$managed.ActiveProcesses
        if (-not(Test-WdA7Count $active)) { throw 'active_count_unknown' }
        if ($timedOut -or $errors.Count -or $active-gt0) { $managed.Stop();$stopped=$true }
        $clock.Restart()
        do {
            $active=$managed.ActiveProcesses
            if (-not(Test-WdA7Count $active)) { throw 'active_count_unknown' }
            if ($active-eq0) { break }
            if ($clock.Elapsed.TotalSeconds-ge$DrainSeconds) { throw 'drain_timeout' }
            Start-Sleep -Milliseconds 10
        } while($true)
        $exited=$managed.Wait(0)
        $drained=($active-eq0-and$exited-is[bool]-and$exited)
        if (-not$drained) { throw 'drain_unknown' }
        # Cumulative accounting survives fast exits; never substitute live children.
        $total=$managed.TotalProcesses
        if (-not(Test-WdA7Count $total)) { throw 'total_count_unknown' }
        $exit=$managed.ExitCode
        if (-not(Test-WdA7Count $exit)) { throw 'exit_code_unknown' }
        $bytes=(Get-Item -LiteralPath $stdoutPath).Length+(Get-Item -LiteralPath $stderrPath).Length
        if ($bytes-gt$MaxOutputBytes) { throw 'output_budget' }
        $utf8=[Text.UTF8Encoding]::new($false,$true)
        # Read each captured stream once, only after a confirmed whole-job drain.
        $out=[IO.File]::ReadAllText($stdoutPath,$utf8)
        $err=[IO.File]::ReadAllText($stderrPath,$utf8)
        $after=Get-WdA7Entries $RunRoot
        $newEntries=@($after|Where-Object{$_-cnotin$before})
    } catch { [void]$errors.Add($_.Exception.Message) }
    finally {
        if ($null-ne$managed) {
            if (-not$drained) { try { $managed.Stop();$stopped=$true } catch { [void]$errors.Add('final_stop_unknown') } }
            try { $managed.Dispose() } catch { [void]$errors.Add('dispose_unknown') }
        }
    }
    $lines=@()
    if ($null-ne$err) { $lines=@($err.TrimEnd("`r","`n")-split'\r?\n') }
    $refusalSeen=($lines.Count-eq1-and$lines[0]-cmatch$RefusalPattern)
    $complete=($errors.Count-eq0-and$drained-and$null-ne$identity-and$null-ne$exit-and$null-ne$newEntries)
    $processStarted=$null
    if ($complete) { if ($total-gt$expected) { $processStarted=$true } elseif($total-eq$expected) { $processStarted=$false } }
    $status='UNKNOWN'
    if ($complete) {
        if ($exit-ne2 -or -not$refusalSeen -or $newEntries.Count -or $out.Length -or $total-gt$expected) { $status='FAIL' }
        elseif ($total-eq$expected) { $status='OWNED_GUARD_REFUSAL_OBSERVED' }
    }
    $created=$null
    if ($null-ne$identity) { $created=$identity.creation_utc }
    return [pscustomobject]@{
        status=$status;whole_A7_PASS=$false
        entry=@{attempt=@{attempt_id=$AttemptId;probe_pid=if($null-ne$probePid){[string]$probePid}else{$null};probe_creation_utc=$created};
                refusal=@{exit_code=$exit;refusal_seen=$refusalSeen;run_dir_created=if($null-ne$newEntries){[bool]$newEntries.Count}else{$null};process_started=$processStarted}}
        owned_job=@{total_processes=$total;active_processes_at_end=$active;expected_probe_processes=$expected;drain_confirmed=$drained;stop_called=$stopped;timed_out=$timedOut;
                    count_basis='Conditional pinned command closure hypothesis, NOT measured host-independent baseline; same cardinality is not image identity'}
        raw=@{stdout=$out;error_text=$err;output_bytes=$bytes;new_run_entries=$newEntries;errors=@($errors)}
        scope='Job-contained cumulative history only, conditional on exact command/adapter/refusal; NOT image proof or global/OS/broker isolation'
    }
}

function Initialize-WdA7OwnedType {
    param([string]$LoopText)
    $canonical=Get-WdA7Sha256 ([Text.Encoding]::UTF8.GetBytes($LoopText))
    if ($canonical-cne'C9CBC5DA5FB75527795F9ECBBE35ABE187298C2251240A5CC885CC331370C9CF') { throw 'refused: loop accounting Source identity' }
    $type='WdManagedTurnProcess'-as[type]
    $proof=Get-Variable -Name WdA7OwnedTypeProof -Scope Script -ErrorAction SilentlyContinue
    if ($null-ne$type) {
        if ($null-eq$proof-or$proof.Value.type-ne$type-or$proof.Value.sha256-cne$canonical) { throw 'refused: preloaded unverified owned-job type' }
        return
    }
    $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($LoopText,[ref]$t,[ref]$e)
    $found=@($ast.FindAll({param($n)$n-is[Management.Automation.Language.FunctionDefinitionAst]-and$n.Name-ceq'Initialize-WdNativeTurnType'},$true))
    if($e.Count-or$found.Count-ne1){throw 'refused: initializer shape'}
    . ([scriptblock]::Create($found[0].Extent.Text))
    Initialize-WdNativeTurnType
    $type='WdManagedTurnProcess'-as[type]
    $signature=[type[]]@([string],[string[]],[string],[string],[string],[string])
    $property=$type.GetProperty('TotalProcesses')
    if ($null-eq$type.GetConstructor($signature)-or$null-eq$property-or$property.PropertyType-ne[uint32]-or$null-ne$property.GetSetMethod()) { throw 'refused: owned accounting API' }
    $script:WdA7OwnedTypeProof=@{type=$type;sha256=$canonical}
}

function Import-WdA7PinnedPlanner {
    param([string]$Text)
    $pins=@{
        'Get-WdShadowA7Plan'='CCD57370DFED13BF3FC7B612B96AAE38A248209733ED5B1E4B0E0E42E25985C0'
        'Get-WdShadowA7ChildCommand'='6B28EBEFBBA89DF82A946143D9D895858F9E7DCE5FBFC0ABDAC2BB6BE3028072'
        'ConvertTo-WdShadowA7Literal'='2B085E3C0635E64FBB1C3C1D641C26988D85F3EA6E638FF3A71067E7864C49B1'
        'Test-WdShadowExactKeys'='92B5FCB906838F5CA04382094E829484370C0D6589AE718CA05DE7F61FC5B8F9'
        'Test-WdShadowCount'='27ECF6B8C09E4B56CE5B3137A44D72243F874EFB513CA887798B70C72D4F67E6'
    }
    $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$t,[ref]$e)
    if($e.Count){throw 'refused: planner parse'}
    $definitions=@()
    foreach($name in $pins.Keys){
        $found=@($ast.FindAll({param($n)$n-is[Management.Automation.Language.FunctionDefinitionAst]-and$n.Name-ceq$name},$true))
        if($found.Count-ne1-or(Get-WdA7Sha256 ([Text.Encoding]::UTF8.GetBytes($found[0].Extent.Text)))-cne$pins[$name]){throw 'refused: planner function identity'}
        $definitions+=$found[0].Extent.Text
    }
    # Return verified definitions only; caller dot-sources them in its own scope.
    return ($definitions-join"`n")
}

function Invoke-WdMindShadowA7OwnedProbe {
    [CmdletBinding()]
    param([switch]$ApprovedNativeExecution,[Collections.IDictionary]$Plan,[hashtable]$ApprovedInputs,[string]$CaseRoot,
          [string]$LiteralAdapterCommand,[hashtable]$CaseBinding,
          [string]$HostPath,[string]$AdapterPath,[string]$LoopPath,[string]$PlannerPath,[string]$EvidenceRoot,
          [string]$ExpectedHostSha256,[string]$ExpectedAdapterSha256,[string]$ExpectedLoopSha256,[string]$ExpectedPlannerSha256,[string]$ExpectedRunnerSha256,
          [ValidateRange(1,30)][int]$WaitSeconds=10,[ValidateRange(1,10)][int]$DrainSeconds=5,[ValidateRange(1024,1048576)][int]$MaxOutputBytes=32768)
    if(-not$ApprovedNativeExecution){throw 'refused: explicit approved native opt-in required; this grants no authority'}
    $ErrorActionPreference='Stop';Set-StrictMode -Version Latest
    if($null-eq$Plan-or$null-eq$ApprovedInputs-or$null-eq$CaseBinding-or-not$CaseRoot-or-not$LiteralAdapterCommand-or-not$EvidenceRoot){throw 'refused: complete A7 attempt context required'}
    foreach($key in @('schema','harness_run_id','case_id','controller_pid','controller_creation_utc','source_commit','manifest_sha256','adapter_sha256')){
        if($CaseBinding[$key]-isnot[string]){throw 'refused: complete typed case binding required'}
    }
    if($CaseBinding.controller_pid-cnotmatch'^[1-9][0-9]{0,9}$'-or$CaseBinding.controller_creation_utc-cnotmatch'^\S+$'){
        throw 'refused: complete typed case binding required'
    }
    $hostPin=Read-WdA7PinnedFile $HostPath $ExpectedHostSha256
    $adapterPin=Read-WdA7PinnedFile $AdapterPath $ExpectedAdapterSha256
    $loopPin=Read-WdA7PinnedFile $LoopPath $ExpectedLoopSha256 -AsText
    $plannerPin=Read-WdA7PinnedFile $PlannerPath $ExpectedPlannerSha256 -AsText
    $runnerPin=Read-WdA7PinnedFile $MyInvocation.MyCommand.ScriptBlock.File $ExpectedRunnerSha256
    . ([scriptblock]::Create((Import-WdA7PinnedPlanner $plannerPin.text)))
    if(-not(Test-WdShadowExactKeys $CaseBinding @('schema','harness_run_id','case_id','controller_pid','controller_creation_utc','source_commit','manifest_sha256','adapter_sha256','attempts'))){throw 'refused: complete case binding required'}
    $names=@('integrity_mismatch','user_sid_mismatch','preloaded_type','off_switch_before_start','off_switch_denied_read','disk_floor_before_start','reparse_ancestry')
    if($CaseBinding.schema-cne'wd.mind-shadow-a7-prestart-binding.v1'-or$CaseBinding.case_id-cne'A7'-or$CaseBinding.harness_run_id-cnotmatch'^\S+$'-or
       -not(Test-WdShadowExactKeys $CaseBinding.attempts $names)){throw 'refused: complete versioned attempt binding required'}
    $ids=@{}
    foreach($name in $names){$id=$CaseBinding.attempts[$name];if($id-isnot[string]-or$id-cnotmatch'^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$'-or$ids.ContainsKey($id)){throw 'refused: unique attempt identity required'};$ids[$id]=$true}
    $controller=Get-WdA7ObservedIdentity $PID
    if($CaseBinding.controller_pid-cne$controller.pid-or$CaseBinding.controller_creation_utc-cne$controller.creation_utc){throw 'refused: foreign controller binding'}
    if($CaseBinding.source_commit-cnotmatch'^[a-f0-9]{40}$'-or$CaseBinding.manifest_sha256-cnotmatch'^[A-F0-9]{64}$'-or
       $CaseBinding.source_commit-cne$ApprovedInputs.ExpectedSourceCommit-or$CaseBinding.manifest_sha256-cne$ApprovedInputs.ExpectedManifestSha256-or
       $CaseBinding.adapter_sha256-cne$adapterPin.sha256-or$ExpectedAdapterSha256-ine$ApprovedInputs.ExpectedAdapterSha256){throw 'refused: foreign Source/manifest/adapter binding'}
    $expected=Get-WdShadowA7Plan -Name ([string]$Plan.name) -Inputs $ApprovedInputs -CaseRoot $CaseRoot
    if($null-eq$expected-or$Plan.Count-ne6-or@($Plan.Keys|Where-Object{$_-cnotin@('name','inputs','run_root','refusal','prepare','preload')}).Count){throw 'refused: complete native plan required'}
    foreach($key in @('name','run_root','refusal','prepare','preload')){if($Plan[$key]-isnot[string]-or$Plan[$key]-cne$expected[$key]){throw 'refused: foreign plan'}}
    if(-not(Test-WdShadowExactKeys $Plan.inputs @($ApprovedInputs.Keys))){throw 'refused: plan input shape'}
    foreach($key in $ApprovedInputs.Keys){if($Plan.inputs[$key]-cne$expected.inputs[$key]){throw 'refused: foreign planned input'}}
    if((Get-WdShadowA7ChildCommand $expected $AdapterPath)-cne$LiteralAdapterCommand){throw 'refused: literal planner command mismatch'}
    Assert-WdA7PreparedState $expected $CaseRoot $loopPin.path $ApprovedInputs.ExpectedSourceCommit
    $EvidenceRoot=[IO.Path]::GetFullPath($EvidenceRoot).TrimEnd('\')
    if($EvidenceRoot-notmatch'^C:\\.*\\\.codex-audit(?:\\|$)'-or-not[IO.Directory]::Exists($EvidenceRoot)){throw 'refused: existing C audit evidence root required'}
    $directoryCheck=Get-Item -LiteralPath $EvidenceRoot
    while($null-ne$directoryCheck){if($directoryCheck.Attributes-band[IO.FileAttributes]::ReparsePoint){throw 'refused: evidence reparse ancestry'};$directoryCheck=$directoryCheck.Parent}
    if([IO.DriveInfo]::new('C').DriveFormat-cne'NTFS'){throw 'refused: evidence needs local NTFS'}
    $live=[IO.Path]::GetFullPath([string]$ApprovedInputs.LiveDir)
    if($live-notmatch'^C:\\.*\\\.codex-audit(?:\\|$)'-or[IO.File]::Exists($live)-or([IO.Directory]::Exists($live)-and[IO.Directory]::GetFileSystemEntries($live).Length)){throw 'refused: fresh synthetic audit live directory required'}
    if([IO.DriveInfo]::new('C').AvailableFreeSpace-lt3221225472){throw 'refused: disk floor'}
    $attemptId=$CaseBinding.attempts[$expected.name]
    $directory=Join-Path $EvidenceRoot ('a7-'+$attemptId)
    if(Test-Path -LiteralPath $directory){throw 'refused: stale A7 attempt directory'}
    [void][IO.Directory]::CreateDirectory($directory)
    $claim=[IO.File]::Open((Join-Path $directory 'owner.once'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None);$claim.Dispose()
    Initialize-WdA7OwnedType $loopPin.text
    $result=Invoke-WdA7OwnedCommand $HostPath $LiteralAdapterCommand ([IO.Path]::GetDirectoryName($AdapterPath)) $directory $expected.run_root $expected.name $expected.refusal $attemptId $WaitSeconds $DrainSeconds $MaxOutputBytes
    $receipt=[ordered]@{schema='wd.a7-owned-probe.v1';binding=$CaseBinding;name=$expected.name;result=$result;host_sha256=$hostPin.sha256;adapter_sha256=$adapterPin.sha256;loop_sha256=$loopPin.sha256;planner_sha256=$plannerPin.sha256;runner_sha256=$runnerPin.sha256;command_sha256=(Get-WdA7Sha256 ([Text.Encoding]::UTF8.GetBytes($LiteralAdapterCommand)));native_permission_inferred=$false;whole_A7_PASS=$false}
    $bytes=[Text.Encoding]::UTF8.GetBytes(($receipt|ConvertTo-Json -Depth 12 -Compress))
    $temporary=Join-Path $directory 'result.pending';$final=Join-Path $directory 'result.json'
    $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
    [IO.File]::Move($temporary,$final)
    return [pscustomobject]$receipt
}
