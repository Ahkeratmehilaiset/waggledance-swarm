#requires -Version 5.1
<#
Future opt-in OUTER-owned A5 actor, Source only; dot-source has no workload.
No whole A5 PASS. Frozen controller bfeb1b8f is required; old234 is refused.
An open timing window is NOT native authority. All observations precede OUTER
cleanup; cleanup cannot repair failed evidence. No PID reopen or broad kill.
#>
function Get-WdA5ParentSha256 {
    param([byte[]]$Bytes)
    $hash=[Security.Cryptography.SHA256]::Create()
    try{return [BitConverter]::ToString($hash.ComputeHash($Bytes)).Replace('-','')}finally{$hash.Dispose()}
}
function Read-WdA5ParentPin {
    param([string]$Path,[string]$Sha256,[switch]$Text)
    if(-not[IO.Path]::IsPathRooted($Path)-or$Sha256-cnotmatch'^[A-F0-9]{64}\z'){throw 'complete explicit parent file pin required'}
    $item=Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if($item.PSIsContainer){throw 'parent pin not a file'}
    for($cursor=$item;$null-ne$cursor;){if($cursor.Attributes-band[IO.FileAttributes]::ReparsePoint){throw 'parent pin reparse ancestry'};if($cursor-is[IO.FileInfo]){$cursor=$cursor.Directory}else{$cursor=$cursor.Parent}}
    $bytes=[IO.File]::ReadAllBytes($item.FullName)
    if((Get-WdA5ParentSha256 $bytes)-cne$Sha256){throw 'parent raw pin mismatch'}
    $content=$null;if($Text){$content=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes).Replace("`r`n","`n")}
    return @{path=$item.FullName;sha256=$Sha256;text=$content}
}
function Import-WdA5ParentPure {
    param([string]$Text)
    if((Get-WdA5ParentSha256 ([Text.Encoding]::UTF8.GetBytes($Text)))-cne'7A36976DAF6D73BA98F65B139680C56A8F77486B322E7E5BD83AE41521B2A8E9'){throw 'frozen bfeb controller Source required'}
    $t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseInput($Text,[ref]$t,[ref]$e)
    if($e.Count){throw 'controller parse'}
    $parts=@()
    foreach($name in @('ConvertFrom-WdA5StrictUtc','Get-WdA5ReadyField','Test-WdA5ReadyKillWindow','Assert-WdA5Context')){
        $found=@($ast.EndBlock.Statements|Where-Object{$_-is[Management.Automation.Language.FunctionDefinitionAst]-and$_.Name-ceq$name})
        if($found.Count-ne1){throw 'exact pure controller function required'};$parts+=$found[0].Extent.Text
    }
    return New-Module -ScriptBlock ([scriptblock]::Create($parts-join"`n"))
}
function Read-WdA5ParentJsonSnapshot {
    param([string]$Path)
    try{$bytes=[IO.File]::ReadAllBytes($Path)}catch [IO.FileNotFoundException]{return @{state='pending'}}catch [IO.DirectoryNotFoundException]{return @{state='pending'}}
    if($bytes.Length-eq0-or$bytes.Length-gt32768){throw 'partial/oversized ready JSON'}
    $text=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes)
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$value=ConvertFrom-Json -InputObject $text -DateKind String -ErrorAction Stop}
    else{$value=ConvertFrom-Json -InputObject $text -ErrorAction Stop}
    return @{state='ready';value=$value;sha256=(Get-WdA5ParentSha256 $bytes)}
}
function Read-WdA5ParentMarker {
    param([string]$Path)
    try{[void][IO.File]::GetAttributes($Path);return 'present'}
    catch [IO.FileNotFoundException]{return 'absent'}
    # Directory-not-found, denied and every other unknown answer THROW.
}
function Read-WdA5ParentReleasedWitness {
    param([string]$Path)
    try{$stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)}
    catch [IO.IOException]{if(($_.Exception.GetBaseException().HResult-band65535)-in@(32,33)){return @{released=$false;text=$null}};throw}
    try{
        if($stream.Length-lt1-or$stream.Length-gt1024){throw 'unknown released witness size'}
        $bytes=New-Object byte[] ([int]$stream.Length);$offset=0
        while($offset-lt$bytes.Length){$read=$stream.Read($bytes,$offset,$bytes.Length-$offset);if($read-le0){throw 'partial witness'};$offset+=$read}
        return @{released=$true;text=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes)}
    }finally{$stream.Dispose()}
}
function Get-WdA5ParentLiteral {
    param([string]$Value)
    if($Value-cnotmatch'^[\x20-\x7E]+\z'){throw 'parent literal is not printable ASCII'}
    return "'"+$Value.Replace("'","''")+"'"
}
function Get-WdA5ParentMemberCommand {
    param([string]$MemberPath,[string]$Directory,[string]$LaunchId,[int]$LifetimeSeconds)
    return '$env:WD_MIND_SHADOW_SYNTHETIC_MEMBER = ''opt-in''; & '+(Get-WdA5ParentLiteral $MemberPath)+
        ' -OptInSyntheticMember -LaunchId '+(Get-WdA5ParentLiteral $LaunchId)+
        ' -Member root -Subcase ''A5/controller_death'' -WitnessDirectory '+(Get-WdA5ParentLiteral $Directory)+
        ' -LifetimeSeconds '+$LifetimeSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
}
function Get-WdA5ParentControllerCommand {
    param([hashtable]$Config)
    $values=@();foreach($key in @('schema','harness_run_id','launch_id','source_commit','manifest_sha256','parent_pid','parent_creation_utc')){$values+=($key+'='+(Get-WdA5ParentLiteral $Config.Context[$key]))}
    $command='. '+(Get-WdA5ParentLiteral $Config.ControllerPath)+'; $context=@{'+($values-join';')+'}; Invoke-WdMindShadowA5Controller -ApprovedNativeExecution -Context $context'
    foreach($key in @('HostPath','LoopPath','MemberPath','RunRoot')){$command+=' -'+$key+' '+(Get-WdA5ParentLiteral $Config[$key])}
    foreach($pair in @(@('ExpectedHostSha256','host_sha256'),@('ExpectedLoopSha256','loop_sha256'),@('ExpectedMemberSha256','member_sha256'),@('ExpectedControllerSha256','controller_sha256'))){$command+=' -'+$pair[0]+' '+(Get-WdA5ParentLiteral $Config.Pins[$pair[1]])}
    return $command+' -ReadyTimeoutSeconds '+[string]$Config.ControllerReadySeconds+' -HoldSeconds '+[string]$Config.HoldSeconds
}
function Get-WdA5ParentField {
    param($Value,[string]$Name)
    if($Value-is[Collections.IDictionary]){if($Value.Contains($Name)){return ,$Value[$Name]}}
    elseif($null-ne$Value){$p=$Value.PSObject.Properties[$Name];if($null-ne$p){return ,$p.Value}}
    throw ('unknown parent receipt field '+$Name)
}
function Assert-WdA5ParentReady {
    param($Ready,[hashtable]$Config,$PureModule)
    $directory=Join-Path $Config.RunRoot $Config.Context.launch_id
    if((Get-WdA5ParentField $Ready 'directory')-cne$directory-or(Get-WdA5ParentField $Ready 'cleanup_marker_path')-cne(Join-Path $directory 'cleanup-started.json')){throw 'foreign ready paths'}
    $pins=Get-WdA5ParentField $Ready 'pins';foreach($key in $Config.Pins.Keys){if((Get-WdA5ParentField $pins $key)-cne$Config.Pins[$key]){throw 'foreign ready pins'}}
    $root=Get-WdA5ParentField $Ready 'root'
    if((Get-WdA5ParentField $root 'pid')-isnot[string]-or(Get-WdA5ParentField $root 'pid')-cnotmatch'^[1-9][0-9]{0,9}\z'){throw 'unknown inner root identity'}
    & $PureModule {param($time)[void](ConvertFrom-WdA5StrictUtc $time)} (Get-WdA5ParentField $root 'creation_utc')
    $account=Get-WdA5ParentField $Ready 'inner_accounting'
    foreach($key in @('active','total')){$count=Get-WdA5ParentField $account $key;if(($count-isnot[int]-and$count-isnot[long]-and$count-isnot[uint32])-or$count-ne3){throw 'unknown inner fixed-three accounting'}}
    $observation=Get-WdA5ParentField $Ready 'observation';$expected=Get-WdA5ParentField $observation 'expected'
    if(($expected-join',')-cne'root,child,grandchild'){throw 'foreign expected member set'}
    $observed=Get-WdA5ParentField $observation 'observed'
    foreach($name in @('root','child','grandchild')){
        $row=Get-WdA5ParentField $observed $name
        if((Get-WdA5ParentField $row 'start_witness')-cne'present'-or(Get-WdA5ParentField $row 'released')-isnot[bool]-or(Get-WdA5ParentField $row 'released')-or
           (Get-WdA5ParentField $row 'expected_launch_id')-cne$Config.Context.launch_id-or(Get-WdA5ParentField $row 'launch_id_verified')-isnot[bool]-or(Get-WdA5ParentField $row 'launch_id_verified')){throw 'ready launch labels are not expected-only held observations'}
    }
    $command=Get-WdA5ParentMemberCommand $Config.MemberPath $directory $Config.Context.launch_id ($Config.ControllerReadySeconds+$Config.HoldSeconds+60)
    if((Get-WdA5ParentField $Ready 'command_sha256')-cne(Get-WdA5ParentSha256 ([Text.Encoding]::UTF8.GetBytes($command)))){throw 'foreign fixed member command'}
}
function Invoke-WdA5OwnedParentCore {
    # Internal actor: public admission owns fixed seams; tests pass strict fakes.
    param([hashtable]$Config,$PureModule,[hashtable]$Seams)
    $failures=New-Object System.Collections.ArrayList;$owned=$null;$observation=$null;$counts=$null;$snapshot=$null
    try{
        foreach($key in @('ControllerReadySeconds','HoldSeconds','ReadyWaitSeconds','ExitWaitSeconds','DrainSeconds')){
            $bound=$Config[$key];$maximum=if($key-ceq'HoldSeconds'){120}elseif($key-ceq'ReadyWaitSeconds'){35}elseif($key-ceq'ControllerReadySeconds'){30}else{10}
            if(($bound-isnot[int]-and$bound-isnot[long])-or$bound-lt1-or$bound-gt$maximum){throw 'unknown/unbounded parent wait configuration'}
        }
        foreach($key in @('NewOwned','Identity','NowUtc','Ready','Marker','Errors','Released')){if($Seams[$key]-isnot[scriptblock]){throw 'complete fixed parent dependency seams required'}}
        & $PureModule {param($context)Assert-WdA5Context $context} $Config.Context
        $self=& $Seams.Identity $PID
        if($self.pid-cne$Config.Context.parent_pid-or$self.creation_utc-cne$Config.Context.parent_creation_utc){throw 'foreign actual parent identity'}
        $owned=& $Seams.NewOwned $Config (Get-WdA5ParentControllerCommand $Config)
        $controller=& $Seams.Identity $owned.Pid
        if($controller.pid-cne[string]$owned.Pid){throw 'unknown owned controller identity'}
        $directory=Join-Path $Config.RunRoot $Config.Context.launch_id;$marker=Join-Path $directory 'cleanup-started.json'
        $watch=[Diagnostics.Stopwatch]::StartNew()
        do{
            if($owned.Wait(0)){throw 'controller exited before ready'}
            $snapshot=& $Seams.Ready (Join-Path $directory 'ready.json')
            if($snapshot.state-ceq'ready'){break};if($snapshot.state-cne'pending'){throw 'unknown ready state'}
            if($watch.Elapsed.TotalSeconds-ge$Config.ReadyWaitSeconds){throw 'ready timeout'};Start-Sleep -Milliseconds 10
        }while($true)
        $ready=$snapshot.value;Assert-WdA5ParentReady $ready $Config $PureModule
        $active=$owned.ActiveProcesses;$total=$owned.TotalProcesses
        # Conservative containment prerequisite: controller plus three held
        # members must be visible; compiler/history can raise total above four.
        # This minimum is NOT a measured Windows/nested-job compatibility claim.
        if($active-isnot[uint32]-or$total-isnot[uint32]-or$active-lt4-or$total-lt$active){throw 'unknown outer accounting'}
        $counts=@{active=$active;total=$total;scope='OUTER contained history incl controller/compiler/members, NOT inner exact3 or image/global process proof'}
        if((& $Seams.Marker $marker)-cne'absent'){throw 'cleanup already present/unknown before kill'}
        if($owned.Wait(0)){throw 'controller ended before kill window'}
        $now=& $Seams.NowUtc
        $window=& $PureModule {param($r,$c,$id,$n,$m)Test-WdA5ReadyKillWindow -Ready $r -ExpectedContext $c -ExpectedController $id -NowUtc $n -SafetyMarginSeconds $m} $ready $Config.Context $controller $now $Config.SafetyMarginSeconds
        if($window.within_window-isnot[bool]-or-not$window.within_window-or$window.native_authority-isnot[bool]-or$window.native_authority){throw 'bound kill window not open'}
        $owned.StopRootOnly() # ONCE, owned handle; no job close or intervening reads.
        $watch.Restart();while(-not$owned.Wait(0)){if($watch.Elapsed.TotalSeconds-ge$Config.ExitWaitSeconds){throw 'forced controller exit unknown'};Start-Sleep -Milliseconds 10}
        if($owned.ExitCode-isnot[int]-or$owned.ExitCode-ne124){throw 'controller did not have forced exit124'}
        if((& $Seams.Marker $marker)-cne'absent'){throw 'cleanup marker present/unknown after exit'}
        $errors=& $Seams.Errors
        if($errors.stdout-isnot[string]-or$errors.stderr-isnot[string]-or$errors.stdout.Length-or$errors.stderr.Length){throw 'controller output errors/unknown'}
        $observed=@{}
        foreach($name in @('root','child','grandchild','controller')){
            $line=$Config.Context.launch_id+' '+$name
            if($name-ceq'controller'){$line+=' '+$controller.pid+' '+$controller.creation_utc};$line+="`n"
            $actual=& $Seams.Released (Join-Path $directory ($name+'.witness')) $line
            if($actual.released-isnot[bool]-or-not$actual.released-or$actual.text-isnot[string]-or$actual.text-cne$line){throw ('released witness missing/foreign/held: '+$name)}
            if($name-cne'controller'){$observed[$name]=@{start_witness='present';released=$true;launch_id=$Config.Context.launch_id;launch_id_verified=$true}}
        }
        $observation=@{expected=@('root','child','grandchild');observed=$observed;observed_before_outer_cleanup=$true}
    }catch{[void]$failures.Add('primary: '+$_.Exception.Message)}finally{
        if($null-ne$owned){
            try{$owned.Stop()}catch{[void]$failures.Add('outer stop: '+$_.Exception.Message)}
            try{$watch=[Diagnostics.Stopwatch]::StartNew();do{$active=$owned.ActiveProcesses;if($active-isnot[uint32]){throw 'outer drain accounting unknown'};if($active-eq0-and$owned.Wait(0)){break};if($watch.Elapsed.TotalSeconds-ge$Config.DrainSeconds){throw 'outer drain timeout'};Start-Sleep -Milliseconds 10}while($true)}catch{[void]$failures.Add('outer drain: '+$_.Exception.Message)}
            try{$owned.Dispose()}catch{[void]$failures.Add('outer dispose: '+$_.Exception.Message)}
        }
    }
    $status=if($failures.Count-eq0-and$null-ne$observation){'OBSERVATIONS_COMPLETE_NOT_A5_PASS'}else{'UNKNOWN'}
    return @{status=$status;whole_A5_PASS=$false;native_permission_inferred=$false;errors=@($failures);observation=$observation;outer_accounting=$counts;ready_snapshot_sha256=if($null-ne$snapshot-and$snapshot.ContainsKey('sha256')){$snapshot.sha256}else{$null}}
}

function Invoke-WdMindShadowA5OwnedParent {
    [CmdletBinding()]
    param([switch]$ApprovedNativeExecution,[hashtable]$Context,[string]$HostPath,[string]$LoopPath,[string]$MemberPath,[string]$ControllerPath,[string]$RunRoot,[string]$EvidenceRoot,
          [string]$ExpectedHostSha256,[string]$ExpectedLoopSha256,[string]$ExpectedMemberSha256,[string]$ExpectedControllerSha256,[string]$ExpectedParentSha256)
    if(-not$ApprovedNativeExecution){throw 'refused: explicit approved native opt-in required; no authority inferred'}
    if($null-eq$Context-or-not$HostPath-or-not$LoopPath-or-not$MemberPath-or-not$ControllerPath-or-not$RunRoot-or-not$EvidenceRoot){throw 'refused: complete parent admission required'}
    # Public positive execution deliberately stays NOT_READY until the exact
    # frozen delivery/outer-context/observer integration is independently admitted.
    # The functional actor above is exercised ONLY with managed fakes in Source.
    [void](Read-WdA5ParentPin $HostPath $ExpectedHostSha256)
    [void](Read-WdA5ParentPin $LoopPath $ExpectedLoopSha256 -Text)
    [void](Read-WdA5ParentPin $MemberPath $ExpectedMemberSha256 -Text)
    $controller=Read-WdA5ParentPin $ControllerPath $ExpectedControllerSha256 -Text
    [void](Read-WdA5ParentPin $MyInvocation.MyCommand.ScriptBlock.File $ExpectedParentSha256)
    [void](Import-WdA5ParentPure $controller.text)
    throw 'NOT_READY: approved exact delivery, outer-context and real observation seams are not provisioned; public native execution disabled'
}
