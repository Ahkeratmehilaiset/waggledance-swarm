#requires -Version 5.1
<# One opt-in, direct child controller. Its caller must independently pin the
   controller bytes, start it asynchronously and retain its process/pipe handles.
   A timed-out read is discarded while this process retains and drains its child.
   Parent loss also discards status; no peer authentication is claimed. #>
[CmdletBinding()]
param([switch]$Enabled, [string]$BundleRoot='', [string]$ExpectedManifestHash='',
      [string]$Lane='', [int]$ControllerPid=0, [string]$ControllerStartUtc='',
      [int]$DeadlineMilliseconds=15000)
$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'

function Read-WdControllerPinned([string]$Path,[string]$Pin,[int]$Limit) {
    if ($Pin -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or $Path -cnotmatch '\A[Cc]:[\\/]' -or
        $Path.Substring(3) -match '[:*?"<>|]' -or @($Path.Substring(3).Split([char[]]'\/') |
          Where-Object {$_ -in @('.','..') -or $_ -match '[. ]$'}).Count) {throw 'path'}
    $full=[IO.Path]::GetFullPath($Path);$current=$full
    while($current) {
        if(([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'path'}
        $parent=[IO.Path]::GetDirectoryName($current)
        if(-not $parent -or $parent -ceq $current){break};$current=$parent
    }
    $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if($stream.Length -lt 1 -or $stream.Length -gt $Limit){throw 'size'}
        $raw=New-Object byte[] ([int]$stream.Length);$pos=0
        while($pos -lt $raw.Length){$n=$stream.Read($raw,$pos,$raw.Length-$pos);if($n -le 0){throw 'read'};$pos+=$n}
        if($stream.ReadByte() -ne -1){throw 'changed'}
    } finally {$stream.Dispose()}
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$actual=[BitConverter]::ToString($sha.ComputeHash($raw)).Replace('-','').ToLowerInvariant()}
    finally{$sha.Dispose()}
    if($actual -cne $Pin.ToLowerInvariant()){throw 'pin'}
    return ,$raw
}
function Get-WdStatusControllerParentPid {
    $rows=@(CimCmdlets\Get-CimInstance -ClassName Win32_Process `
        -Filter ('ProcessId = '+[string]$PID) -Property ProcessId,ParentProcessId -ErrorAction Stop)
    if($rows.Count -ne 1 -or $null -eq $rows[0] -or [long]$rows[0].ProcessId -ne [long]$PID -or
       [long]$rows[0].ParentProcessId -le 0 -or [long]$rows[0].ParentProcessId -gt [int]::MaxValue){throw 'parent'}
    return [int]$rows[0].ParentProcessId
}
function Test-WdControllerKeys($Value,[string[]]$Keys) {
    if($Value -isnot [pscustomobject]){return $false}
    $names=@($Value.PSObject.Properties.Name)
    if($names.Count -ne $Keys.Count){return $false}
    foreach($name in $names){if($Keys -cnotcontains $name){return $false}}
    return $true
}
function ConvertTo-WdControllerCanonical($Value,[int]$Depth=0) {
    if($Depth -gt 16 -or $null -eq $Value){throw 'json'}
    if($Value -is [string]){
        $builder=New-Object Text.StringBuilder;[void]$builder.Append('"')
        foreach($character in $Value.ToCharArray()){
            $code=[int]$character
            switch($code){
                34 {[void]$builder.Append('\"')};92 {[void]$builder.Append('\\')};8 {[void]$builder.Append('\b')}
                9 {[void]$builder.Append('\t')};10 {[void]$builder.Append('\n')};12 {[void]$builder.Append('\f')};13 {[void]$builder.Append('\r')}
                default {if($code -lt 32 -or $code -ge 127){[void]$builder.Append(('\u{0:x4}' -f $code))}else{[void]$builder.Append($character)}}
            }
        }
        [void]$builder.Append('"');return $builder.ToString()
    }
    if($Value -is [bool]){if($Value){return 'true'};return 'false'}
    if($Value -is [int] -or $Value -is [long]){return $Value.ToString([Globalization.CultureInfo]::InvariantCulture)}
    if($Value -is [array]){$items=@(foreach($item in $Value){ConvertTo-WdControllerCanonical $item ($Depth+1)});return '['+($items -join ',')+']'}
    if($Value -is [pscustomobject]){
        $names=[string[]]@($Value.PSObject.Properties.Name);[Array]::Sort($names,[StringComparer]::Ordinal)
        $items=@(foreach($name in $names){(ConvertTo-WdControllerCanonical $name ($Depth+1))+':' + (ConvertTo-WdControllerCanonical $Value.PSObject.Properties[$name].Value ($Depth+1))})
        return '{'+($items -join ',')+'}'
    }
    throw 'json'
}
function ConvertFrom-WdControllerCanonical([string]$Text){
    $options=@{InputObject=$Text;ErrorAction='Stop'}
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$options.DateKind='String'}
    $value=ConvertFrom-Json @options
    if((ConvertTo-WdControllerCanonical $value) -cne $Text){throw 'json'}
    return $value
}
function Test-WdStatusControllerParentAlive($Parent) {
    try {return ($null -ne $Parent -and -not $Parent.HasExited)} catch {return $false}
}
function Write-WdControllerFrame($Value) {
    $text=ConvertTo-Json -InputObject $Value -Compress -Depth 16
    $bytes=[Text.Encoding]::UTF8.GetBytes($text)
    if($bytes.Length -gt 65536){return $false}
    $stream=[Console]::OpenStandardOutput()
    try {
        $task=$stream.WriteAsync($bytes,0,$bytes.Length)
        # The parent consumes asynchronously. If it has disappeared, no result
        # is trusted by anyone; never make cleanup depend on a blocked pipe.
        if(-not $task.Wait(500)){return $false}
        [void]$task.GetAwaiter().GetResult();return $true
    } catch{return $false}
}
function New-WdControllerUnavailable([string]$Reason) {
    return [ordered]@{controller_schema='wd.mind-status-controller.v1';complete_verified=$false;
        outcome='not_ready';reason=$Reason;authority='none';delivery='NOT_PROVEN';
        caller_authentication='UNKNOWN';server_authentication='UNKNOWN'}
}
if(-not $Enabled){[void](Write-WdControllerFrame (New-WdControllerUnavailable 'disabled'));exit 2}

$clock=[Diagnostics.Stopwatch]::StartNew();$parent=$null;$child=$null;$started=$false
$done=$false;$abandoned=$false;$announced=$false;$captured=$null;$write=$null
try {
    if($DeadlineMilliseconds -lt 1 -or $DeadlineMilliseconds -gt 60000 -or
       $ExpectedManifestHash -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or
       $BundleRoot -cnotmatch '\A[Cc]:[\\/]' -or $BundleRoot -match '(^|[\\/])\.\.([\\/]|$)' -or
       @('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5') -cnotcontains $Lane -or
       $ControllerPid -le 0 -or [string]::IsNullOrWhiteSpace($ControllerStartUtc)){throw 'input'}
    if((Get-WdStatusControllerParentPid) -ne $ControllerPid){throw 'parent'}
    $self=[Diagnostics.Process]::GetCurrentProcess()
    try{$selfStarted=$self.StartTime.ToUniversalTime()}finally{$self.Dispose()}
    $parent=[Diagnostics.Process]::GetProcessById($ControllerPid);[void]$parent.Handle
    $parentStarted=$parent.StartTime.ToUniversalTime()
    if($parentStarted.ToString('o') -cne $ControllerStartUtc -or $parentStarted -gt $selfStarted -or $parent.HasExited){throw 'parent'}
    $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\');$utf8=New-Object Text.UTF8Encoding($false,$true)
    $manifestPath=Join-Path $root 'deployment-manifest.json'
    $manifestBytes=Read-WdControllerPinned $manifestPath $ExpectedManifestHash 4194304
    $manifest=$utf8.GetString($manifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop
    if($manifest.schema_version -ne 1 -or $null -eq $manifest.files){throw 'manifest'}
    $helperPin=$manifest.files.PSObject.Properties['Invoke-WdMindStartupRead.ps1']
    $fleetPin=$manifest.files.PSObject.Properties['wd-fleet.json']
    if($null -eq $helperPin -or $helperPin.Value -isnot [string] -or
       $null -eq $fleetPin -or $fleetPin.Value -isnot [string]){throw 'manifest'}
    $helperBytes=Read-WdControllerPinned (Join-Path $root 'Invoke-WdMindStartupRead.ps1') $helperPin.Value 1048576
    $fleetBytes=Read-WdControllerPinned (Join-Path $root 'wd-fleet.json') $fleetPin.Value 1048576
    $fleet=$utf8.GetString($fleetBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop
    if($fleet -isnot [pscustomobject] -or $fleet.lanes -isnot [array] -or
       $fleet.tools_supervisor -isnot [pscustomobject] -or $fleet.runtime_root -isnot [string]){throw 'fleet'}
    if($fleet.tools_supervisor.agent -cne 'codex-tools-1'){throw 'fleet'}
    $entries=@($fleet.lanes)+@($fleet.tools_supervisor)
    $known=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
    $seen=@{};$laneUuid=''
    foreach($entry in $entries){
        if($entry -isnot [pscustomobject] -or $entry.agent -isnot [string] -or
           $known -cnotcontains $entry.agent -or $seen.ContainsKey($entry.agent) -or
           $entry.agent_uuid -isnot [string] -or
           $entry.agent_uuid.ToLowerInvariant() -cnotmatch '\A[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\z'){throw 'fleet'}
        $seen[$entry.agent]=$true
        if($entry.agent -ceq $Lane){$laneUuid=$entry.agent_uuid.ToLowerInvariant()}
    }
    if($seen.Count -ne $known.Count -or -not $laneUuid){throw 'fleet'}
    # The bridge root is data from the hash-pinned fleet, never a caller-supplied
    # source path. Session code appends only shared/events.jsonl to this root.
    $bridgeRoot=$fleet.runtime_root
    if($bridgeRoot -cnotmatch '\A[Cc]:[\\/]' -or $bridgeRoot.Substring(3) -match '[:*?"<>|]' -or
       @($bridgeRoot.Substring(3).Split([char[]]'\/') | Where-Object {$_ -in @('.','..') -or $_ -match '[. ]$'}).Count){throw 'fleet'}
    $bridgeRoot=[IO.Path]::GetFullPath($bridgeRoot).TrimEnd('\')
    if([IO.Path]::GetFileName($bridgeRoot) -cne '.agent-bridge'){throw 'fleet'}
    $current=$bridgeRoot
    while($current){
        $attrs=[IO.File]::GetAttributes($current)
        if(($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
           ($attrs -band [IO.FileAttributes]::Directory) -eq 0){throw 'fleet'}
        $next=[IO.Path]::GetDirectoryName($current)
        if(-not $next -or $next -ceq $current){break};$current=$next
    }
    [void](Read-WdControllerPinned $manifestPath $ExpectedManifestHash 4194304)
    if($clock.ElapsedMilliseconds -ge $DeadlineMilliseconds -or $parent.HasExited -or
       (Get-WdStatusControllerParentPid) -ne $ControllerPid){throw 'cancelled'}
    $payload=[ordered]@{source=[Convert]::ToBase64String($helperBytes);root=$root;manifest=$ExpectedManifestHash;lane=$Lane}
    $wire=$utf8.GetBytes((ConvertTo-Json -InputObject $payload -Compress))
    if($wire.Length -gt 1500000){throw 'size'}
        $bootstrap='$ProgressPreference="SilentlyContinue";$ErrorActionPreference="Stop";$x=ConvertFrom-Json ([Console]::In.ReadToEnd());$s=(New-Object Text.UTF8Encoding($false,$true)).GetString([Convert]::FromBase64String($x.source)).TrimStart([char]0xFEFF);. ([scriptblock]::Create($s));$r=Invoke-WdMindStartupRead -BundleRoot $x.root -ExpectedManifestHash $x.manifest -Lane $x.lane -RetainUntilExit;$j=ConvertTo-Json -InputObject $r -Compress -Depth 16;$b=[Text.Encoding]::UTF8.GetBytes($j);[Console]::OpenStandardOutput().Write($b,0,$b.Length)'
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $PSHOME $(if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'})
    $info.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($bootstrap))
    $info.WorkingDirectory=$root;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
    $allow=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA')
    foreach($key in @($info.EnvironmentVariables.Keys)){if($allow -cnotcontains ([string]$key).ToUpperInvariant()){$info.EnvironmentVariables.Remove([string]$key)}}
    $info.EnvironmentVariables['AGENT_BRIDGE_RUNTIME_ROOT']=$bridgeRoot
    $info.EnvironmentVariables['AGENT_BRIDGE_AGENT_UUID']=$laneUuid
    if(-not [IO.File]::Exists($info.FileName)){throw 'executable'}
    $child=New-Object Diagnostics.Process;$child.StartInfo=$info
    if(-not $child.Start()){throw 'spawn'};$started=$true
    $write=$child.StandardInput.BaseStream.WriteAsync($wire,0,$wire.Length)
    $outbuf=New-Object byte[] 4096;$errbuf=New-Object byte[] 4096
    $outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)
    $errRead=$child.StandardError.BaseStream.ReadAsync($errbuf,0,$errbuf.Length)
    $outDone=$false;$errDone=$false;$inputDone=$false;$invalid=$false;$drainUnknown=$false
    $captured=New-Object IO.MemoryStream
    while($true){
        if(-not $abandoned -and ($parent.HasExited -or $clock.ElapsedMilliseconds -ge $DeadlineMilliseconds)) {
            $abandoned=$true;[Array]::Clear($captured.GetBuffer(),0,[int]$captured.Capacity);$captured.SetLength(0)
            if(-not $parent.HasExited){[void](Write-WdControllerFrame (New-WdControllerUnavailable 'timeout'));$announced=$true}
        }
        if(-not $inputDone -and $write.IsCompleted){
            try{[void]$write.GetAwaiter().GetResult();$child.StandardInput.Close()}catch{$invalid=$true}
            $inputDone=$true;[Array]::Clear($wire,0,$wire.Length);$payload=$null
        }
        foreach($which in @('out','err')){
            $task=if($which -ceq 'out'){$outRead}else{$errRead}
            $finished=if($which -ceq 'out'){$outDone}else{$errDone}
            if($finished -or $null -eq $task -or -not $task.IsCompleted){continue}
            try{$n=$task.GetAwaiter().GetResult()}catch{$invalid=$true;$drainUnknown=$true;$n=0}
            if($n -eq 0){if($which -ceq 'out'){$outDone=$true}else{$errDone=$true}}
            elseif($which -ceq 'err'){$invalid=$true;$errRead=$child.StandardError.BaseStream.ReadAsync($errbuf,0,$errbuf.Length)}
            else{
                if($captured.Length+$n -gt 131072){$invalid=$true;[Array]::Clear($captured.GetBuffer(),0,[int]$captured.Capacity);$captured.SetLength(0)}
                elseif(-not $invalid -and -not $abandoned){$captured.Write($outbuf,0,$n)}
                $outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)
            }
        }
        if($child.HasExited -and $outDone -and $errDone -and -not $drainUnknown){$done=$true;break}
        [Threading.Thread]::Sleep(10)
    }
    if($abandoned -or $parent.HasExited){exit 3}
    if($invalid -or $child.ExitCode -ne 0 -or $clock.ElapsedMilliseconds -ge $DeadlineMilliseconds){throw 'child'}
    $bytes=$captured.ToArray()
    if($bytes.Length -eq 0){throw 'output'}
    $text=$utf8.GetString($bytes)
    $jsonArgs=@{InputObject=$text;ErrorAction='Stop'}
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$jsonArgs.DateKind='String'}
    $result=ConvertFrom-Json @jsonArgs
    if((ConvertTo-Json -InputObject $result -Compress -Depth 16) -ceq $text -and
       (Test-WdControllerKeys $result @('schema','outcome','reason','authority','view_json','view_sha256','cleanup','record_written')) -and
       $result.schema -ceq 'wd.mind-startup-status.v1' -and $result.outcome -ceq 'unknown' -and
       $result.reason -ceq 'status_stale' -and $result.authority -ceq 'none' -and
       $result.cleanup -ceq 'wrapper_exited' -and $result.record_written -is [bool] -and
       -not $result.record_written -and $result.view_json -ceq '' -and $result.view_sha256 -ceq ''){
        $stale=New-WdControllerUnavailable 'status_stale';$stale.outcome='unknown';$stale['observation_status']='UNKNOWN'
        [void](Write-WdControllerFrame $stale);exit 2
    }
    if((ConvertTo-Json -InputObject $result -Compress -Depth 16) -cne $text -or
       -not (Test-WdControllerKeys $result @('schema','outcome','reason','authority','view_json','view_sha256','cleanup','record_written')) -or
       $result.schema -cne 'wd.mind-startup-status.v1' -or $result.outcome -cne 'success' -or
       $result.reason -cne 'status_projection' -or $result.authority -cne 'none' -or
       $result.cleanup -cne 'wrapper_exited' -or $result.record_written -isnot [bool] -or $result.record_written -or
       $result.view_json -isnot [string] -or $result.view_sha256 -cnotmatch '\A[0-9a-f]{64}\z'){throw 'shape'}
    $sha=[Security.Cryptography.SHA256]::Create()
    try{$actual=[BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($result.view_json))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
    if($actual -cne $result.view_sha256){throw 'hash'}
    $viewArgs=@{InputObject=$result.view_json;ErrorAction='Stop'}
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$viewArgs.DateKind='String'}
    $view=ConvertFrom-Json @viewArgs
    if($view -isnot [pscustomobject] -or $view.authority -cne 'none' -or $view.lane -cne $Lane){throw 'view'}
    if($view.schema -ceq 'wd.mind-direct-status.v1'){$status=$view}
    elseif($view.schema -ceq 'wd.bridge-mind-lane-read.v1' -and $null -ne $view.PSObject.Properties['status_observation']){
        $status=$view.status_observation
        if($view.fleet_sha256 -cne $status.fleet_sha256){throw 'view'}
    }else{throw 'view'}
    if($status.schema -cne 'wd.mind-direct-status.v1' -or $status.authority -cne 'none' -or
       $status.lane -cne $Lane -or $status.fleet_sha256 -cne $fleetPin.Value.ToLowerInvariant()){throw 'view'}
    [void](Read-WdControllerPinned $manifestPath $ExpectedManifestHash 4194304)
    [void](Read-WdControllerPinned (Join-Path $root 'wd-fleet.json') $fleetPin.Value 1048576)
    if($parent.HasExited -or $clock.ElapsedMilliseconds -ge $DeadlineMilliseconds){throw 'cancelled'}
    # Keep the source projection and its as_of-bound age unchanged.  Report a
    # separate age at this handoff, so an old bounded snapshot is never
    # presented as though it were collected at the later delivery time.
    $deliveryTime=[DateTimeOffset]::UtcNow
    $deliveryLaneAges=[ordered]@{}
    $deliverySourceMaxEvent=$null
    if($view.schema -ceq 'wd.bridge-mind-lane-read.v1'){
        $outerAsOf=[DateTimeOffset]::MinValue
        if(-not [DateTimeOffset]::TryParse($view.as_of_utc,[Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None,[ref]$outerAsOf) -or
           $outerAsOf.Offset -ne [TimeSpan]::Zero -or $outerAsOf -gt $deliveryTime){throw 'future_status'}
        if(([double]$view.freshness.age_seconds+($deliveryTime-$outerAsOf).TotalSeconds) -gt [double]$view.freshness.max_age_seconds){
            $stale=New-WdControllerUnavailable 'status_stale';$stale.outcome='unknown';$stale['observation_status']='UNKNOWN'
            [void](Write-WdControllerFrame $stale);exit 2
        }
        foreach($laneProperty in $view.lanes.PSObject.Properties){
            $laneStatus=$laneProperty.Value
            if($laneStatus.observed_in_window -is [bool] -and $laneStatus.observed_in_window){
                $laneObserved=[DateTimeOffset]::MinValue
                if(-not [DateTimeOffset]::TryParse($laneStatus.last_event_utc,[Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::None,[ref]$laneObserved) -or
                   $laneObserved.Offset -ne [TimeSpan]::Zero -or $laneObserved -gt $deliveryTime){throw 'future_status'}
                $laneAge=($deliveryTime-$laneObserved).TotalSeconds
                $deliveryLaneAges[$laneProperty.Name]=[ordered]@{event_utc=$laneStatus.last_event_utc;
                    age_seconds=[long][Math]::Floor($laneAge);freshness=$(if($laneAge -le 900){'OBSERVED'}else{'UNKNOWN'})}
            }
        }
        if($view.source.max_event_utc -cne 'UNKNOWN'){
            $maxEvent=[DateTimeOffset]::MinValue
            if(-not [DateTimeOffset]::TryParse($view.source.max_event_utc,[Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::None,[ref]$maxEvent) -or
               $maxEvent.Offset -ne [TimeSpan]::Zero -or $maxEvent -gt $deliveryTime){throw 'future_status'}
            $maxAge=($deliveryTime-$maxEvent).TotalSeconds
            $deliverySourceMaxEvent=[ordered]@{event_utc=$view.source.max_event_utc;
                age_seconds=[long][Math]::Floor($maxAge);freshness=$(if($maxAge -le 900){'OBSERVED'}else{'UNKNOWN'})}
        }
    }
    $sourceAsOf=[DateTimeOffset]::MinValue
    if(-not [DateTimeOffset]::TryParse($status.as_of_utc,[Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::None,[ref]$sourceAsOf) -or
       $sourceAsOf.Offset -ne [TimeSpan]::Zero -or $sourceAsOf -gt $deliveryTime){throw 'future_status'}
    if(($deliveryTime-$sourceAsOf).TotalSeconds -gt 900){
        $stale=New-WdControllerUnavailable 'status_stale';$stale.outcome='unknown';$stale['observation_status']='UNKNOWN'
        [void](Write-WdControllerFrame $stale);exit 2
    }
    $deliveryAge=$null
    if($status.facts -isnot [array] -or $status.facts.Count -gt 1){throw 'view'}
    if($status.facts.Count -eq 1){
        $observed=[DateTimeOffset]::MinValue
        if(-not [DateTimeOffset]::TryParse($status.facts[0].observed_utc,[Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None,[ref]$observed) -or
           $observed.Offset -ne [TimeSpan]::Zero -or $observed -gt $sourceAsOf -or $observed -gt $deliveryTime){throw 'future_status'}
        $ageNow=($deliveryTime-$observed).TotalSeconds
        if($ageNow -gt 900 -and $status.observation_status -ceq 'OBSERVED'){
            $stale=New-WdControllerUnavailable 'status_stale';$stale.outcome='unknown';$stale['observation_status']='UNKNOWN'
            [void](Write-WdControllerFrame $stale);exit 2
        }
        $deliveryAge=[long][Math]::Floor($ageNow)
    }
    $frame=[ordered]@{controller_schema='wd.mind-status-controller.v1';complete_verified=$true;
        manifest_sha256=$ExpectedManifestHash.ToLowerInvariant();fleet_sha256=$fleetPin.Value.ToLowerInvariant();
        lane=$Lane;outcome='success';reason='status_projection';authority='none';delivery='NOT_PROVEN';
        receipt='NOT_PROVEN';record_written=$false;view_json=$result.view_json;view_sha256=$result.view_sha256;
        delivery_as_of_utc=$deliveryTime.ToString("yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'",[Globalization.CultureInfo]::InvariantCulture);
        delivery_age_seconds=$deliveryAge;delivery_lane_ages=$deliveryLaneAges;delivery_source_max_event=$deliverySourceMaxEvent}
    if(-not (Write-WdControllerFrame $frame)){exit 3};exit 0
} catch {
    if($null -eq $child -or $done){
        if(-not $announced -and ($null -eq $parent -or (Test-WdStatusControllerParentAlive $parent))){
            [void](Write-WdControllerFrame (New-WdControllerUnavailable 'refused'))
        }
        exit 2
    }
    # Start may have succeeded even if its return or a later operation failed.
    # Retain the process and discard/drain until exit and both EOFs are proven.
    if(-not $announced -and ($null -eq $parent -or (Test-WdStatusControllerParentAlive $parent))){
        [void](Write-WdControllerFrame (New-WdControllerUnavailable 'hold'));$announced=$true
    }
    if($null -eq $outbuf){$outbuf=New-Object byte[] 4096};if($null -eq $errbuf){$errbuf=New-Object byte[] 4096}
    while($true){
        if(-not $inputDone -and ($null -eq $write -or $write.IsCompleted)){
            try{if($null -ne $write){[void]$write.GetAwaiter().GetResult()}}catch{}
            try{$child.StandardInput.Close()}catch{}
            $inputDone=$true
        }
        foreach($which in @('out','err')){
            try{
                if($which -ceq 'out'){
                    if($null -eq $outRead -and -not $outDone){$outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)}
                    if($null -ne $outRead -and $outRead.IsCompleted){$n=$outRead.GetAwaiter().GetResult();$outRead=$null;if($n -eq 0){$outDone=$true}}
                }else{
                    if($null -eq $errRead -and -not $errDone){$errRead=$child.StandardError.BaseStream.ReadAsync($errbuf,0,$errbuf.Length)}
                    if($null -ne $errRead -and $errRead.IsCompleted){$n=$errRead.GetAwaiter().GetResult();$errRead=$null;if($n -eq 0){$errDone=$true}}
                }
            }catch{}
        }
        try{if($child.HasExited -and $outDone -and $errDone){$done=$true;break}}catch{}
        [Threading.Thread]::Sleep(100)
    }
    exit 3
} finally {
    if($null -ne $captured){[Array]::Clear($captured.GetBuffer(),0,[int]$captured.Capacity);$captured.Dispose()}
    if($done -and $null -ne $child){$child.Dispose()}
    if($null -ne $parent){$parent.Dispose()}
}
