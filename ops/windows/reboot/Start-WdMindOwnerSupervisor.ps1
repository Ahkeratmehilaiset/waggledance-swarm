#requires -Version 5.1
<# Opt-in supervisor.  Its caller must independently pin these supervisor bytes
   and the bundle manifest.  It never repairs a reservation or kills a process. #>
[CmdletBinding()]
param([switch] $Enabled, [string] $BundleRoot='', [string] $ExpectedManifestHash='',
      [string] $StopEventName='', [int] $ControllerPid=0, [string] $ControllerStartUtc='')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'

function Read-WdSupervisorPinnedBytes([string] $Path, [string] $Pin, [int] $Maximum) {
    if ($Pin -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or $Path -cnotmatch '\A[Cc]:[\\/]' -or
        $Path.Substring(3) -match '[:*?"<>|]' -or @($Path.Substring(3).Split([char[]]'\/') |
            Where-Object { $_ -eq '.' -or $_ -eq '..' -or $_ -match '[. ]$' }).Count) { throw 'path' }
    $full=[IO.Path]::GetFullPath($Path); $current=$full
    while ($current) {
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'path' }
        $parent=[IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break }
        $current=$parent
    }
    $file=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($file.Length -lt 1 -or $file.Length -gt $Maximum) { throw 'size' }
        $bytes=New-Object byte[] ([int]$file.Length); $pos=0
        while ($pos -lt $bytes.Length) { $n=$file.Read($bytes,$pos,$bytes.Length-$pos); if ($n -le 0) {throw 'read'}; $pos+=$n }
        if ($file.ReadByte() -ne -1) {throw 'changed'}
    } finally { $file.Dispose() }
    $hash=[Security.Cryptography.SHA256]::Create()
    try {$actual=[BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}
    finally {$hash.Dispose()}
    if ($actual -cne $Pin.ToLowerInvariant()) {throw 'pin'}
    return ,$bytes
}
function Get-WdSupervisorParentPid {
    $rows=@(CimCmdlets\Get-CimInstance -ClassName Win32_Process `
        -Filter ('ProcessId = ' + [string]$PID) -Property ProcessId,ParentProcessId -ErrorAction Stop)
    if ($rows.Count -ne 1 -or $null -eq $rows[0] -or [long]$rows[0].ProcessId -ne [long]$PID -or
        [long]$rows[0].ParentProcessId -le 0 -or [long]$rows[0].ParentProcessId -gt [int]::MaxValue) {throw 'parent_query'}
    return [int]$rows[0].ParentProcessId
}

# Reporting is bounded and best effort. A detached caller may close stdout;
# that must neither block lane startup nor revoke the owner's retained handle.
$script:out=[Console]::OpenStandardOutput(); $script:pending=$null; $script:buffer=$null
$script:queued=''; $script:last=''; $script:bytes=0; $script:broken=$false
function Set-WdSupervisorState([string] $state) {
    if ($state -cnotin @('DISABLED','STARTING','READY','HOLD','STOPPED','REFUSED','PRE_OWNER_REFUSED')) {throw 'state'}
    if ($state -cne $script:last) {$script:queued=$state}
}
function Flush-WdSupervisorState {
    if ($script:broken) {return}
    try {
        if ($null -ne $script:pending) {
            if (-not $script:pending.IsCompleted) {return}
            [void]$script:pending.GetAwaiter().GetResult(); $script:pending=$null; $script:buffer=$null
        }
        if ($script:queued) {
            $state=$script:queued; $script:queued=''; $script:last=$state
            $line='{"schema":"wd.mind-owner-supervisor-status.v1","state":"'+$state+'","authority":"none","owner_authentication":"UNKNOWN"}' + "`n"
            $script:buffer=[Text.Encoding]::ASCII.GetBytes($line)
            if ($script:bytes+$script:buffer.Length -gt 8192) {throw 'limit'}
            $script:bytes+=$script:buffer.Length
            $script:pending=$script:out.WriteAsync($script:buffer,0,$script:buffer.Length)
        }
    } catch {$script:broken=$true}
}
function Finish-WdSupervisorOutput {
    for ($i=0;$i -lt 3;$i++) {
        if ($null -ne $script:pending) {try {[void]$script:pending.Wait(250)} catch {$script:broken=$true}}
        Flush-WdSupervisorState
        if ($null -eq $script:pending -and -not $script:queued) {break}
    }
}
function Get-WdSupervisorHostFrame([string] $Line, [string] $Previous, [bool] $EverReady, [bool] $ProofSeen) {
    foreach ($candidate in @('STARTING','READY','HOLD','STOPPED','REFUSED','PRE_OWNER_REFUSED')) {
        $expected='{"schema":"wd.mind-owner-host-status.v1","state":"'+$candidate+'","authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"}'
        if ($Line -ceq $expected) {
            if ($ProofSeen -or ($candidate -ceq 'PRE_OWNER_REFUSED' -and
                ($EverReady -or $Previous -cnotin @('','STARTING','HOLD')))) {throw 'status_order'}
            return $candidate
        }
    }
    throw 'status'
}
if (-not $Enabled) { Set-WdSupervisorState 'DISABLED'; Flush-WdSupervisorState; Finish-WdSupervisorOutput; exit 0 }

$child=$null; $controller=$null; $internalStop=$null; $externalStop=$null; $hostDone=$false; $stop=$false
try {
    if ($StopEventName -cnotmatch '\ALocal\\wd-mind-supervisor-stop-[0-9a-f]{32}\z' -or
        $BundleRoot -cnotmatch '\A[Cc]:[\\/]' -or $BundleRoot -match '(^|[\\/])\.\.([\\/]|$)' -or
        $ControllerPid -le 0 -or [string]::IsNullOrWhiteSpace($ControllerStartUtc)) {throw 'input'}
    if ((Get-WdSupervisorParentPid) -ne $ControllerPid) {throw 'controller_parent'}
    $supervisorProcess=[Diagnostics.Process]::GetCurrentProcess()
    try {$supervisorStarted=$supervisorProcess.StartTime.ToUniversalTime()} finally {$supervisorProcess.Dispose()}
    $controller=[Diagnostics.Process]::GetProcessById($ControllerPid)
    [void]$controller.Handle # Keep the exact OS process handle across the full lifetime.
    $controllerStarted=$controller.StartTime.ToUniversalTime()
    if ($controllerStarted.ToString('o') -cne $ControllerStartUtc -or
        $controllerStarted -gt $supervisorStarted -or $controller.HasExited) {throw 'controller'}
    $externalStop=[Threading.EventWaitHandle]::OpenExisting($StopEventName)
    $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
    $manifestPath=Join-Path $root 'deployment-manifest.json'
    $utf8=New-Object Text.UTF8Encoding($false,$true)
    $manifestBytes=Read-WdSupervisorPinnedBytes $manifestPath $ExpectedManifestHash 4194304
    $manifest=$utf8.GetString($manifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop
    if ($manifest.schema_version -ne 1 -or $null -eq $manifest.files) {throw 'manifest'}
    $hostPin=$manifest.files.PSObject.Properties['Start-WdMindOwnerHost.ps1']
    $configPin=$manifest.files.PSObject.Properties['wd-mind-startup-config.json']
    $automationPin=$manifest.files.PSObject.Properties['wd-mind-owner-automation.json']
    if ($null -eq $hostPin -or $null -eq $configPin -or $null -eq $automationPin -or
        $manifest.source_commit -isnot [string] -or $manifest.source_commit -cnotmatch '\A[0-9a-f]{40}\z') {throw 'manifest'}
    $automationBytes=Read-WdSupervisorPinnedBytes (Join-Path $root 'wd-mind-owner-automation.json') $automationPin.Value 16384
    $automationText=$utf8.GetString($automationBytes)
    $automation=ConvertFrom-Json -InputObject $automationText -ErrorAction Stop
    $automationKeys=@($automation.PSObject.Properties.Name)
    $expectedKeys=@('schema','enabled','agent','source_commit','startup_config_sha256')
    if ($automationKeys.Count -ne $expectedKeys.Count -or @($automationKeys | Where-Object {$expectedKeys -cnotcontains $_}).Count -ne 0 -or
        $automation.schema -isnot [string] -or $automation.agent -isnot [string] -or $automation.source_commit -isnot [string] -or
               $automation.startup_config_sha256 -isnot [string] -or $automation.schema -cne 'wd.mind-owner-automation.v1' -or
               $automation.enabled -isnot [bool] -or -not $automation.enabled -or
        $automation.agent -cne 'codex-lead-1' -or $automation.source_commit -cne $manifest.source_commit -or
        $automation.startup_config_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        $configPin.Value -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or $automation.startup_config_sha256 -cne $configPin.Value.ToLowerInvariant()) {throw 'automation'}
    $canonical=[ordered]@{schema='wd.mind-owner-automation.v1';enabled=$true;agent='codex-lead-1';
        source_commit=[string]$automation.source_commit;startup_config_sha256=[string]$automation.startup_config_sha256}
    if ((ConvertTo-Json -InputObject $canonical -Compress) -cne $automationText) {throw 'automation'}
    $hostBytes=Read-WdSupervisorPinnedBytes (Join-Path $root 'Start-WdMindOwnerHost.ps1') $hostPin.Value 1048576
    $configBytes=Read-WdSupervisorPinnedBytes (Join-Path $root 'wd-mind-startup-config.json') $configPin.Value 16384
    $config=$utf8.GetString($configBytes) | ConvertFrom-Json -ErrorAction Stop
    $keys=@($config.PSObject.Properties.Name | Sort-Object)
    if (($keys -join ',') -cne 'approval_reference_sha256,authority,enabled,identifier_policy_path,identifier_policy_sha256,schema' -or
        $config.schema -cne 'wd.mind-startup-config.v1' -or $config.enabled -isnot [bool] -or
        $config.enabled -ne $true -or $config.authority -cne 'none' -or
        $config.identifier_policy_path -isnot [string] -or [string]::IsNullOrWhiteSpace($config.identifier_policy_path) -or
        $config.identifier_policy_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        $config.approval_reference_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
        $manifest.bridge_code_package.python_executable_sha256 -cnotmatch '\A[0-9A-Fa-f]{64}\z') {throw 'config'}
    [void](Read-WdSupervisorPinnedBytes $manifestPath $ExpectedManifestHash 4194304)
    if ((Get-WdSupervisorParentPid) -ne $ControllerPid -or $controller.HasExited -or
        $externalStop.WaitOne(0)) {throw 'cancelled'}
    $name='Local\wd-mind-owner-stop-'+[guid]::NewGuid().ToString('N')
    $created=$false
    $internalStop=New-Object Threading.EventWaitHandle($false,[Threading.EventResetMode]::ManualReset,$name,[ref]$created)
    if (-not $created) {throw 'event'}
    $me=[Diagnostics.Process]::GetCurrentProcess()
    try {$start=$me.StartTime.ToUniversalTime().ToString('o')} finally {$me.Dispose()}
    $payload=[ordered]@{source=[Convert]::ToBase64String($hostBytes);root=$root;manifest=$ExpectedManifestHash;
        policy=$config.identifier_policy_path;policy_sha=$config.identifier_policy_sha256;
        approval_sha=$config.approval_reference_sha256;python=$manifest.bridge_code_package.python_executable;
        python_sha=$manifest.bridge_code_package.python_executable_sha256;event=$name;controller=$PID;
        started=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($start))}
    $wire=$utf8.GetBytes((ConvertTo-Json -InputObject $payload -Compress))
    if ($wire.Length -gt 1500000) {throw 'size'}
    $trampoline='$ProgressPreference="SilentlyContinue"; $ErrorActionPreference="Stop"; $x=ConvertFrom-Json ([Console]::In.ReadToEnd()); $s=(New-Object Text.UTF8Encoding($false,$true)).GetString([Convert]::FromBase64String($x.source)).TrimStart([char]0xFEFF); $started=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($x.started)); . ([scriptblock]::Create($s)) -Enabled -SourceBoundScriptBlock -BundleRoot $x.root -ExpectedManifestHash $x.manifest -OperatorPolicyPath $x.policy -OperatorPolicySha256 $x.policy_sha -OperatorApprovalReferenceSha256 $x.approval_sha -BridgePythonPath $x.python -BridgePythonSha256 $x.python_sha -StopEventName $x.event -ControllerPid $x.controller -ControllerStartUtc $started'
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $PSHOME $(if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'})
    $info.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($trampoline))
    $info.WorkingDirectory=$root; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $allow=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA')
    foreach($key in @($info.EnvironmentVariables.Keys)) {if($allow -cnotcontains ([string]$key).ToUpperInvariant()) {$info.EnvironmentVariables.Remove([string]$key)}}
    # This refusal occurs before a Process exists or Start is called, so it
    # cannot orphan a child. A later failure while starting remains HOLD:
    # executable existence here does not eliminate replacement/removal races.
    if (-not [IO.File]::Exists($info.FileName)) {throw 'spawn_executable_unavailable'}
    $child=New-Object Diagnostics.Process; $child.StartInfo=$info
    if (-not $child.Start()) {throw 'spawn'}
    $write=$child.StandardInput.BaseStream.WriteAsync($wire,0,$wire.Length)
    $outbuf=New-Object byte[] 4096; $errbuf=New-Object byte[] 4096
    $outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)
    $errRead=$child.StandardError.BaseStream.ReadAsync($errbuf,0,$errbuf.Length)
    $line=New-Object IO.MemoryStream; $outDone=$false; $errDone=$false; $inputDone=$false
    $hostState=''; $proofSeen=$false; $everReady=$false; $bad=$false
    Set-WdSupervisorState 'STARTING'
    while ($true) {
        if (-not $inputDone -and $write.IsCompleted) {
            try {[void]$write.GetAwaiter().GetResult(); $child.StandardInput.Close()} catch {$bad=$true}
            $inputDone=$true; [Array]::Clear($wire,0,$wire.Length); $payload=$null
        }
        if ($null -ne $errRead -and $errRead.IsCompleted) {
            try {
                $n=$errRead.GetAwaiter().GetResult()
                if ($n -ne 0) {$bad=$true; $errRead=$child.StandardError.BaseStream.ReadAsync($errbuf,0,$errbuf.Length)}
                else {$errDone=$true; $errRead=$null}
            } catch {$bad=$true; $errRead=$null}
        }
        if ($null -ne $outRead -and $outRead.IsCompleted) {
            try {
                $n=$outRead.GetAwaiter().GetResult()
                if ($n -eq 0) {$outDone=$true; $outRead=$null}
                else {
                    for($i=0;$i -lt $n -and -not $bad;$i++) {
                        if ($outbuf[$i] -eq 10) {
                            $s=[Text.Encoding]::ASCII.GetString($line.ToArray()); $line.SetLength(0)
                            $candidate=Get-WdSupervisorHostFrame $s $hostState $everReady $proofSeen
                            if ($candidate -ceq 'PRE_OWNER_REFUSED') {$proofSeen=$true}
                            if ($candidate -ceq 'READY') {$everReady=$true}
                            $hostState=$candidate
                        } else {if($outbuf[$i] -gt 127 -or $line.Length -ge 1024) {throw 'status'}; $line.WriteByte($outbuf[$i])}
                    }
                    $outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)
                }
            } catch {
                $bad=$true
                try {$outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)} catch {$outRead=$null}
            }
        }
        if ($controller.HasExited -or $externalStop.WaitOne(0) -or $bad) {$stop=$true; [void]$internalStop.Set()}
        if ($child.HasExited) {
            if ($outDone -and $errDone -and $line.Length -eq 0 -and -not $bad) {
                if ($child.ExitCode -eq 0 -and $hostState -ceq 'STOPPED') {Set-WdSupervisorState 'STOPPED';$hostDone=$true;break}
                if ($child.ExitCode -eq 2 -and $hostState -ceq 'PRE_OWNER_REFUSED') {Set-WdSupervisorState 'PRE_OWNER_REFUSED';$hostDone=$true;break}
            }
            # Exit and pipe EOF are not atomic. Continue draining both pipes;
            # only a complete but invalid receipt is definitively bad.
            if ($outDone -and $errDone) {$bad=$true}
            $stop=$true; [void]$internalStop.Set()
        }
        if ($bad -or $stop -or $hostState -ceq 'HOLD') {Set-WdSupervisorState 'HOLD'}
        elseif ($hostState -ceq 'READY') {Set-WdSupervisorState 'READY'}
        Flush-WdSupervisorState
        [Threading.Thread]::Sleep(50)
    }
    Flush-WdSupervisorState; Finish-WdSupervisorOutput
    if($hostState -ceq 'STOPPED'){exit 0}; exit 2
} catch {
    if ($null -eq $child) {Set-WdSupervisorState 'REFUSED';Flush-WdSupervisorState;Finish-WdSupervisorOutput;exit 2}
    # Spawn happened: retain the child and continue cooperative STOP forever.
    try {[void]$internalStop.Set()} catch {}
    Set-WdSupervisorState 'HOLD'
    if ($null -eq $outbuf) {$outbuf=New-Object byte[] 4096}
    if ($null -eq $errbuf) {$errbuf=New-Object byte[] 4096}
    while ($true) {
        # Discard, never publish, data from a child whose protocol is already
        # uncertain. Continue draining even when cleanup cannot be proven.
        foreach ($which in @('Out','Err')) {
            try {
                if ($which -ceq 'Out') {
                    if ($null -eq $outRead -and -not $outDone) {$outRead=$child.StandardOutput.BaseStream.ReadAsync($outbuf,0,$outbuf.Length)}
                    if ($null -ne $outRead -and $outRead.IsCompleted) {
                        $n=$outRead.GetAwaiter().GetResult();$outRead=$null;if($n -eq 0){$outDone=$true}
                    }
                } else {
                    if ($null -eq $errRead -and -not $errDone) {$errRead=$child.StandardError.BaseStream.ReadAsync($errbuf,0,$errbuf.Length)}
                    if ($null -ne $errRead -and $errRead.IsCompleted) {
                        $n=$errRead.GetAwaiter().GetResult();$errRead=$null;if($n -eq 0){$errDone=$true}
                    }
                }
            } catch { }
        }
        Flush-WdSupervisorState; [Threading.Thread]::Sleep(100)
    }
} finally {
    if ($hostDone -or $null -eq $child) {
        if ($null -ne $child) {$child.Dispose()}
        if ($null -ne $internalStop) {$internalStop.Dispose()}
        if ($null -ne $externalStop) {$externalStop.Dispose()}
        if ($null -ne $controller) {$controller.Dispose()}
    }
}
