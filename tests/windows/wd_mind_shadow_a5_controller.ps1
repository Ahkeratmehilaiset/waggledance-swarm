#requires -Version 5.1
<#
Source-only opt-in A5 separate controller. Dot-source defines functions only.
The controller owns the INNER job. A future parent owns this controller in an
OUTER safety job and must StopRootOnly(), wait for controller exit, and observe
all member witness releases BEFORE outer Stop/Dispose. This file gives no
native/install authority and never returns whole A5 PASS. No handles escape.
#>

function Get-WdA5Sha256 {
    param([byte[]]$Bytes)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-','')}finally{$hash.Dispose()}
}

function ConvertFrom-WdA5StrictUtc {
    param($Value)
    $format="yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'"
    $parsed=[DateTime]::MinValue
    $style=[Globalization.DateTimeStyles]::AssumeUniversal-bor[Globalization.DateTimeStyles]::AdjustToUniversal
    if($Value-isnot[string]-or$Value-cnotmatch'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z\z'-or
       -not[DateTime]::TryParseExact($Value,$format,[Globalization.CultureInfo]::InvariantCulture,$style,[ref]$parsed)-or
       $parsed.ToString($format,[Globalization.CultureInfo]::InvariantCulture)-cne$Value){throw 'refused: exact invariant roundtrip UTC required'}
    return $parsed
}

function Get-WdA5ReadyField {
    param($Object,[string]$Name)
    if($Object-is[Collections.IDictionary]){if($Object.Contains($Name)){return ,$Object[$Name]}}
    elseif($null-ne$Object){$property=$Object.PSObject.Properties[$Name];if($null-ne$property){return ,$property.Value}}
    throw ('missing A5 ready field '+$Name)
}

function Test-WdA5ReadyKillWindow {
    # PURE timing/binding prerequisite only. No clock/file/process reads and no
    # native authority: caller supplies fresh NowUtc immediately before its call.
    param($Ready,[hashtable]$ExpectedContext,[hashtable]$ExpectedController,$NowUtc,$SafetyMarginSeconds)
    try{
        Assert-WdA5Context $ExpectedContext
        if($null-eq$ExpectedController-or$ExpectedController.Count-ne2-or$ExpectedController.pid-isnot[string]-or
           $ExpectedController.pid-cnotmatch'^[1-9][0-9]{0,9}\z'){throw 'unknown expected owned controller'}
        [void](ConvertFrom-WdA5StrictUtc $ExpectedController.creation_utc)
        $context=Get-WdA5ReadyField $Ready 'context'
        $keys=if($context-is[Collections.IDictionary]){@($context.Keys)}else{@($context.PSObject.Properties.Name)}
        if($keys.Count-ne$ExpectedContext.Count){throw 'foreign ready context shape'}
        foreach($key in $ExpectedContext.Keys){if((Get-WdA5ReadyField $context $key)-isnot[string]-or(Get-WdA5ReadyField $context $key)-cne$ExpectedContext[$key]){throw 'foreign ready context'}}
        $controller=Get-WdA5ReadyField $Ready 'controller'
        foreach($key in @('pid','creation_utc')){if((Get-WdA5ReadyField $controller $key)-isnot[string]-or(Get-WdA5ReadyField $controller $key)-cne$ExpectedController[$key]){throw 'foreign ready controller'}}
        if((Get-WdA5ReadyField $Ready 'schema')-cne'wd.mind-shadow-a5-controller-ready.v1'-or
           (Get-WdA5ReadyField $Ready 'status')-cne'INNER_JOB_READY_NOT_A5_PASS'-or(Get-WdA5ReadyField $Ready 'subcase')-cne'A5/controller_death'-or
           (Get-WdA5ReadyField $Ready 'whole_A5_PASS')-isnot[bool]-or(Get-WdA5ReadyField $Ready 'whole_A5_PASS')){throw 'not an A5 ready prerequisite'}
        $hold=Get-WdA5ReadyField $Ready 'hold_seconds'
        if(($hold-isnot[int]-and$hold-isnot[long])-or$hold-lt1-or$hold-gt120){throw 'unknown hold duration'}
        if(($SafetyMarginSeconds-isnot[int]-and$SafetyMarginSeconds-isnot[long]-and$SafetyMarginSeconds-isnot[double]-and$SafetyMarginSeconds-isnot[decimal])-or
           [double]::IsNaN([double]$SafetyMarginSeconds)-or[double]::IsInfinity([double]$SafetyMarginSeconds)-or$SafetyMarginSeconds-le0-or$SafetyMarginSeconds-ge$hold){throw 'positive finite safety margin required'}
        $start=ConvertFrom-WdA5StrictUtc (Get-WdA5ReadyField $Ready 'ready_utc')
        $deadline=ConvertFrom-WdA5StrictUtc (Get-WdA5ReadyField $Ready 'deadline_utc')
        $now=ConvertFrom-WdA5StrictUtc $NowUtc
        if($start.AddSeconds($hold).Ticks-ne$deadline.Ticks){throw 'inconsistent absolute hold deadline'}
        $cutoff=$deadline.AddSeconds(-[double]$SafetyMarginSeconds)
        if($cutoff.Ticks-ge$deadline.Ticks){throw 'safety margin not representable by host clock arithmetic'}
        if($now.Ticks-lt$start.Ticks-or$now.Ticks-ge$cutoff.Ticks){throw 'kill window not open'}
        return @{status='WINDOW_OPEN_TIMING_ONLY';within_window=$true;native_authority=$false;kill_before_utc=$cutoff.ToString('o',[Globalization.CultureInfo]::InvariantCulture)}
    }catch{return @{status='UNKNOWN';within_window=$false;native_authority=$false;reason=$_.Exception.Message}}
}

function Read-WdA5PinnedFile {
    param([string]$Path,[string]$Sha256,[switch]$Text)
    if(-not[IO.Path]::IsPathRooted($Path)-or$Sha256-cnotmatch'^[A-F0-9]{64}\z'){throw 'refused: complete explicit A5 file pin required'}
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if($item.PSIsContainer){throw 'refused: A5 pinned regular file required'}
    for($cursor=$item;$null-ne$cursor;){
        if($cursor.Attributes-band[IO.FileAttributes]::ReparsePoint){throw 'refused: A5 pin reparse ancestry'}
        if($cursor-is[IO.FileInfo]){$cursor=$cursor.Directory}else{$cursor=$cursor.Parent}
    }
    $bytes=[IO.File]::ReadAllBytes($item.FullName)
    if((Get-WdA5Sha256 $bytes)-cne$Sha256){throw 'refused: A5 file pin mismatch'}
    $content=$null
    if($Text){$content=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes).Replace("`r`n","`n")}
    return @{path=$item.FullName;sha256=$Sha256;text=$content}
}

function Assert-WdA5Context {
    param([hashtable]$Context)
    $keys=@('schema','harness_run_id','launch_id','source_commit','manifest_sha256','parent_pid','parent_creation_utc')
    if($null-eq$Context-or$Context.Count-ne$keys.Count){throw 'refused: complete A5 context required'}
    foreach($key in $keys){if(-not$Context.ContainsKey($key)-or$Context[$key]-isnot[string]-or$Context[$key]-cnotmatch'^\S+\z'){throw 'refused: complete typed A5 context required'}}
    if($Context.schema-cne'wd.mind-shadow-a5-controller-context.v1'-or$Context.launch_id-cnotmatch'^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\z'-or
       $Context.source_commit-cnotmatch'^[a-f0-9]{40}\z'-or$Context.manifest_sha256-cnotmatch'^[A-F0-9]{64}\z'-or$Context.parent_pid-cnotmatch'^[1-9][0-9]{0,9}\z'){
        throw 'refused: invalid A5 context identity'
    }
    [void](ConvertFrom-WdA5StrictUtc $Context.parent_creation_utc)
}

function Write-WdA5Once {
    param([string]$Path,[string]$Text)
    $stream=[IO.File]::Open($Path,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try{$bytes=[Text.Encoding]::UTF8.GetBytes($Text);$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
}

function Get-WdA5Identity {
    param([int]$OwnedPid)
    $process=Get-Process -Id $OwnedPid -ErrorAction Stop
    return @{pid=[string]$OwnedPid;creation_utc=$process.StartTime.ToUniversalTime().ToString('o',[Globalization.CultureInfo]::InvariantCulture)}
}

function Test-WdA5MemberHeld {
    param([string]$Path)
    if(-not[IO.File]::Exists($Path)){return $false}
    try{$stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None);$stream.Dispose();return $false}
    catch [IO.IOException] {if(($_.Exception.GetBaseException().HResult-band65535)-in@(32,33)){return $true};throw}
}

function Get-WdA5Literal {
    param([string]$Value)
    if($Value-cnotmatch'^[\x20-\x7E]+\z'){throw 'refused: A5 literal must be printable ASCII'}
    return "'"+$Value.Replace("'","''")+"'"
}

function Get-WdA5MemberCommand {
    param([string]$MemberPath,[string]$Directory,[string]$LaunchId,[int]$LifetimeSeconds)
    # Only this fixed member tree, never a caller command. Opt-in is child-local.
    return '$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER = ''opt-in''; & '+(Get-WdA5Literal $MemberPath)+
        ' -OptInSyntheticMember -LaunchId '+(Get-WdA5Literal $LaunchId)+
        ' -Member root -Subcase ''A5/controller_death'' -WitnessDirectory '+(Get-WdA5Literal $Directory)+
        ' -LifetimeSeconds '+$LifetimeSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
}

function New-WdA5InnerProcess {
    param($HostPath,$Arguments,$Directory,$InputPath,$OutputPath,$ErrorPath)
    return [WdManagedTurnProcess]::new($HostPath,[string[]]$Arguments,$Directory,$InputPath,$OutputPath,$ErrorPath)
}

function Invoke-WdA5ControllerCore {
    # Internal mechanics, after public full pin/context/fresh-root checks.
    param([hashtable]$Context,[string]$HostPath,[string]$MemberPath,[string]$RunRoot,[hashtable]$Pins,
          [ValidateRange(1,30)][int]$ReadyTimeoutSeconds=10,[ValidateRange(1,120)][int]$HoldSeconds=30)
    Assert-WdA5Context $Context
    $directory=Join-Path $RunRoot $Context.launch_id
    if([IO.Directory]::Exists($directory)-or[IO.File]::Exists($directory)){throw 'refused: stale A5 launch directory'}
    [void][IO.Directory]::CreateDirectory($directory)
    $members=@('root','child','grandchild')
    Write-WdA5Once (Join-Path $directory 'members.registration') ((($members|ForEach-Object{$Context.launch_id+' '+$_})-join"`n")+"`n")
    $controller=Get-WdA5Identity $PID
    if($controller.pid-cne[string]$PID-or-not$controller.creation_utc){throw 'refused: actual A5 controller identity unknown'}
    $witness=$null;$inner=$null;$root=$null;$ready=$false;$readyAttempted=$false;$holdClock=$null;$result=$null
    $failures=New-Object System.Collections.ArrayList
    try{
        $witness=[IO.File]::Open((Join-Path $directory 'controller.witness'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
        $line=[Text.Encoding]::ASCII.GetBytes($Context.launch_id+' controller '+$controller.pid+' '+$controller.creation_utc+"`n")
        $witness.Write($line,0,$line.Length);$witness.Flush($true)
        $input=Join-Path $directory 'stdin.once';Write-WdA5Once $input ''
        $command=Get-WdA5MemberCommand $MemberPath $directory $Context.launch_id ($ReadyTimeoutSeconds+$HoldSeconds+60)
        $arguments=[string[]]@('-NoProfile','-NonInteractive','-Command',$command)
        $inner=New-WdA5InnerProcess $HostPath $arguments $directory $input (Join-Path $directory 'stdout.once') (Join-Path $directory 'stderr.once')
        $root=Get-WdA5Identity $inner.Pid
        if($root.pid-cne[string]$inner.Pid-or-not$root.creation_utc){throw 'refused: actual A5 inner root identity unknown'}
        $clock=[Diagnostics.Stopwatch]::StartNew()
        do{
            if($inner.Wait(0)){throw 'refused: A5 inner root exited before ready'}
            $held=@($members|Where-Object{Test-WdA5MemberHeld (Join-Path $directory ($_+'.witness'))})
            if($held.Count-eq3){break}
            if($clock.Elapsed.TotalSeconds-ge$ReadyTimeoutSeconds){throw 'refused: A5 member ready timeout'}
            Start-Sleep -Milliseconds 10
        }while($true)
        $active=$inner.ActiveProcesses;$total=$inner.TotalProcesses
        if($active-isnot[uint32]-or$total-isnot[uint32]-or$active-ne3-or$total-ne3){throw 'refused: A5 fixed-tree accounting unknown or unexpected'}
        # Held witnesses cannot yet be read: the launch id is expected, not
        # observed. Parent must verify actual contents after release.
        $observed=@{};foreach($name in $members){$observed[$name]=@{start_witness='present';released=$false;expected_launch_id=$Context.launch_id;launch_id_verified=$false}}
        # UTC is anchored BEFORE the monotonic hold. Do not restart after a slow
        # publication: cleanup cannot precede the original advertised deadline.
        $readyUtc=[DateTime]::UtcNow;$holdClock=[Diagnostics.Stopwatch]::StartNew()
        $receipt=[ordered]@{schema='wd.mind-shadow-a5-controller-ready.v1';status='INNER_JOB_READY_NOT_A5_PASS';context=$Context;
            controller=$controller;root=$root;subcase='A5/controller_death';directory=$directory;pins=$Pins;
            observation=@{expected=$members;observed=$observed};inner_accounting=@{active=$active;total=$total};
            command_sha256=(Get-WdA5Sha256 ([Text.Encoding]::UTF8.GetBytes($command)));whole_A5_PASS=$false;
            ready_utc=$readyUtc.ToString('o',[Globalization.CultureInfo]::InvariantCulture);hold_seconds=$HoldSeconds;
            deadline_utc=$readyUtc.AddSeconds($HoldSeconds).ToString('o',[Globalization.CultureInfo]::InvariantCulture);
            cleanup_marker_path=(Join-Path $directory 'cleanup-started.json');
            required_parent_order='Require pure bound kill window with explicit positive margin; StopRootOnly strictly before cutoff; confirm forced exit124 and no cleanup marker/errors; observe releases BEFORE outer cleanup'}
        $readyAttempted=$true
        Write-WdA5Once (Join-Path $directory 'ready.json') ($receipt|ConvertTo-Json -Depth 12 -Compress)
        $ready=$true
        while($holdClock.Elapsed.TotalSeconds-lt$HoldSeconds){
            if($inner.Wait(0)){throw 'A5 inner root ended while controller should be held'}
            Start-Sleep -Milliseconds 10
        }
        $result=@{status='CONTROLLER_EXPIRED_NOT_A5_PASS';ready_written=$ready;whole_A5_PASS=$false}
    }catch{[void]$failures.Add('primary: '+$_.Exception.Message)}finally{
        # Normal/error cleanup is NOT controller-death evidence. Abrupt controller
        # death relies on OS closing INNER job handle; parent retains OUTER safety.
        try{if($null-ne$inner){
            # An expiry/error cleanup is never controller-death evidence. Future
            # parent must positively verify this marker absent AFTER forced exit,
            # and fail UNKNOWN on unreadable state, errors or marker write failure.
            $cleanup=@{schema='wd.mind-shadow-a5-controller-cleanup.v1';context=$Context;controller=$controller;root=$root;ready_written=$ready;whole_A5_PASS=$false}
            $durable=$false
            try{Write-WdA5Once (Join-Path $directory 'cleanup-started.json') ($cleanup|ConvertTo-Json -Depth 8 -Compress);$durable=$true}
            catch{[void]$failures.Add('cleanup marker: '+$_.Exception.Message)}
            if($readyAttempted-and-not$durable){
                # Even an early ordinary error cannot close INNER inside the
                # parent's open kill window. No retry; one bounded original hold.
                while($holdClock.Elapsed.TotalSeconds-lt$HoldSeconds){Start-Sleep -Milliseconds 10}
            }
            try{$inner.Stop()}catch{[void]$failures.Add('inner stop: '+$_.Exception.Message)}
            try{$inner.Dispose()}catch{[void]$failures.Add('inner dispose: '+$_.Exception.Message)}
        }}finally{if($null-ne$witness){try{$witness.Dispose()}catch{[void]$failures.Add('witness dispose: '+$_.Exception.Message)}}}
    }
    if($failures.Count){throw ($failures-join'; ')}
    return $result
}

function Invoke-WdMindShadowA5Controller {
    [CmdletBinding()]
    param([switch]$ApprovedNativeExecution,[hashtable]$Context,[string]$HostPath,[string]$LoopPath,[string]$MemberPath,[string]$RunRoot,
          [string]$ExpectedHostSha256,[string]$ExpectedLoopSha256,[string]$ExpectedMemberSha256,[string]$ExpectedControllerSha256,
          [ValidateRange(1,30)][int]$ReadyTimeoutSeconds=10,[ValidateRange(1,120)][int]$HoldSeconds=30)
    if(-not$ApprovedNativeExecution){throw 'refused: explicit approved native opt-in required; not runtime authority'}
    $ErrorActionPreference='Stop';Set-StrictMode -Version Latest
    if($null-eq$Context-or-not$HostPath-or-not$LoopPath-or-not$MemberPath-or-not$RunRoot){throw 'refused: complete approved A5 context and pins required'}
    Assert-WdA5Context $Context
    $hostPin=Read-WdA5PinnedFile $HostPath $ExpectedHostSha256
    $loopPin=Read-WdA5PinnedFile $LoopPath $ExpectedLoopSha256 -Text
    $memberPin=Read-WdA5PinnedFile $MemberPath $ExpectedMemberSha256 -Text
    $selfPin=Read-WdA5PinnedFile $MyInvocation.MyCommand.ScriptBlock.File $ExpectedControllerSha256
    if((Get-WdA5Sha256 ([Text.Encoding]::UTF8.GetBytes($loopPin.text)))-cne'FEF4D7F152D7BBA9A345C6213E02C51A218E8BF61999A8058AF6FB20CACC84E9'){throw 'refused: exact A5 owned-loop Source required'}
    if((Get-WdA5Sha256 ([Text.Encoding]::UTF8.GetBytes($memberPin.text)))-cne'C99B7757FCDE68B9F6E03C02769C9ACE4536AB99179EB10647E8DBC3A0747425'){throw 'refused: exact fixed A5 member Source required'}
    $root=[IO.Path]::GetFullPath($RunRoot).TrimEnd('\')
    [void](Get-WdA5Literal $memberPin.path);[void](Get-WdA5Literal $root)
    if($root-notmatch'^C:\\.*\\\.codex-audit\\[^\\]+\z'-or-not[IO.Directory]::Exists($root)-or[IO.Directory]::GetFileSystemEntries($root).Length){throw 'refused: existing fresh C audit run folder required'}
    for($item=Get-Item -LiteralPath $root;$null-ne$item;$item=$item.Parent){if($item.Attributes-band[IO.FileAttributes]::ReparsePoint){throw 'refused: A5 run root reparse ancestry'}}
    $drive=[IO.DriveInfo]::new('C')
    if($drive.DriveFormat-cne'NTFS'-or$drive.AvailableFreeSpace-lt3221225472){throw 'refused: A5 NTFS/disk floor'}
    if('WdManagedTurnProcess'-as[type]){throw 'refused: A5 controller needs fresh non-preloaded type'}
    $tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($loopPin.text,[ref]$tokens,[ref]$errors)
    $found=@($ast.FindAll({param($node)$node-is[Management.Automation.Language.FunctionDefinitionAst]-and$node.Name-ceq'Initialize-WdNativeTurnType'},$true))
    if($errors.Count-or$found.Count-ne1){throw 'refused: A5 loop initializer shape'}
    . ([scriptblock]::Create($found[0].Extent.Text));Initialize-WdNativeTurnType
    $type='WdManagedTurnProcess'-as[type]
    if($null-eq$type.GetMethod('StopRootOnly',[type[]]@())){throw 'refused: A5 owned-root-only API absent'}
    return Invoke-WdA5ControllerCore $Context $hostPin.path $memberPin.path $root @{host_sha256=$hostPin.sha256;loop_sha256=$loopPin.sha256;member_sha256=$memberPin.sha256;controller_sha256=$selfPin.sha256} $ReadyTimeoutSeconds $HoldSeconds
}
