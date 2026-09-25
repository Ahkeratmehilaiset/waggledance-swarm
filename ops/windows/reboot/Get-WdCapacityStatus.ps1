#requires -Version 5.1
<# Read-only installed capacity locator. Never collects, repairs or starts agents. #>
[CmdletBinding()]
param(
    [string]$InstallRoot='C:\Python\wd-capacity-observer',
    [switch]$Summary,
    # Opt-in only. Absent, the emitted status is byte-identical to today and
    # nothing about models, schedules or default output changes.
    [switch]$Attribution,
    [ValidateSet('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')]
    [string]$Agent='',
    [switch]$Json
)
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
function Get-CapacityReadHash([string]$Path) {
    $stream=[IO.File]::OpenRead($Path);$sha=[Security.Cryptography.SHA256]::Create()
    try{return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','')}
    finally{$sha.Dispose();$stream.Dispose()}
}
function Assert-CapacityReadPath([string]$Path,[string]$Root) {
    $full=[IO.Path]::GetFullPath($Path)
    if(-not $full.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Path escaped observer root'}
    $walk=$full
    while($walk -and $walk.Length -ge $Root.Length){
        try{$attributes=[IO.File]::GetAttributes($walk)}
        catch [IO.FileNotFoundException]{$attributes=0}
        catch [IO.DirectoryNotFoundException]{$attributes=0}
        if($attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Observer path contains a reparse point'}
        $walk=Split-Path $walk -Parent
    }
    return $full
}
function Get-CapacityField($Object,[string]$Name) {
    if($null -ne $Object -and $null -ne $Object.PSObject.Properties[$Name]) { return ,$Object.PSObject.Properties[$Name].Value }
    return $null
}
function Get-CapacityLaneSummary($Status) {
    # An observed process association is NOT an authenticated account/quota binding.
    # Native Codex telemetry is projected by a bounded reader. No conversation
    # content is returned, no credentials/provider/canonical bridge log is read.
    $processes=@();$processReason='process_query_unavailable'
    try { $processes=@(Get-CimInstance Win32_Process -ErrorAction Stop);$processReason='no_unique_runtime_process' }
    catch {
        # Fixed diagnostic labels only: never disclose raw exception text or
        # interpret a failed query as exhausted quota or absent processes.
        if($_.CategoryInfo.Category -eq [System.Management.Automation.ErrorCategory]::PermissionDenied -or
           $_.Exception -is [System.UnauthorizedAccessException]){
            $processReason='process_query_access_denied'
        }
    }
    $now=[datetimeoffset]::Parse([string]$Status.observed_at,[Globalization.CultureInfo]::InvariantCulture)
    foreach($lane in @('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')) {
        if($Agent -and $Agent -cne $lane){continue}
        $provider=if($lane -like 'codex-*'){'codex'}else{'claude'}
        $candidates=@()
        foreach($native in $processes) {
            if([string]$native.Name -ine ($provider+'.exe')){continue}
            # Anchor to real native resume arguments, never text inside its prompt.
            $resume=[regex]::Match([string]$native.CommandLine,'^(?:"[^"]+"|\S+)\s+(?:--resume|resume)\s+([a-fA-F0-9-]{36})(?:\s|$)')
            if(-not $resume.Success){continue}
            $parents=@($processes|Where-Object ProcessId -EQ $native.ParentProcessId)
            if($parents.Count -ne 1){continue}
            $parent=$parents[0]
            if($parent.Name -notin @('powershell.exe','pwsh.exe')){continue}
            $file=[regex]::Match([string]$parent.CommandLine,'(?i)(?:^|\s)-File\s+(?:"([^"]+)"|(\S+))')
            if(-not $file.Success){continue}
            $scriptPath=if($file.Groups[1].Success){$file.Groups[1].Value}else{$file.Groups[2].Value}
            # The installed forwarding entry point dot-sources its pinned bundle;
            # its process command line retains the machine path, not the target.
            # This associates observed process ancestry only, NOT authenticated
            # account identity, bundle attestation, quota ownership or authority.
            $machineWrapper=$scriptPath -ieq 'C:\Python\start-wd-agent.ps1'
            $bundleLauncher=$scriptPath -match '(?i)\\wd-reboot-bundles\\[a-f0-9]{40}\\start-wd-(agent|tools-consumer)\.ps1$'
            if(-not $machineWrapper -and -not $bundleLauncher){continue}
            if($lane -ceq 'codex-tools-1'){
                if($scriptPath -notlike '*\start-wd-tools-consumer.ps1'){continue}
            }elseif($scriptPath -notlike '*\start-wd-agent.ps1' -or
                [string]$parent.CommandLine -notmatch ('(?i)(?:^|\s)-Agent\s+"?'+[regex]::Escape($lane)+'"?(?:\s|$)')){continue}
            if($null -eq $parent.CreationDate -or $null -eq $native.CreationDate -or $parent.CreationDate -gt $native.CreationDate){continue}
            $cwdMatch=[regex]::Match([string]$native.CommandLine,'(?:^|\s)(?:--cd|-C)\s+(?:"([^"]+)"|(\S+))')
            $nativeCwd=if($cwdMatch.Groups[1].Success){$cwdMatch.Groups[1].Value}elseif($cwdMatch.Success){$cwdMatch.Groups[2].Value}else{$null}
            $candidates+=@{thread=$resume.Groups[1].Value;pid=[int]$native.ProcessId;start=$native.CreationDate.ToUniversalTime().ToString('o');cwd=$nativeCwd}
        }
        $binding=if($candidates.Count -eq 1){$candidates[0]}else{$null}
        $activityRows=Get-CapacityField $Status 'native_activity'
        $rows=@($activityRows|Where-Object {
            $null -ne $binding -and $_.provider -ceq $provider -and $_.native_thread_id -ceq $binding.thread
        })
        $activity=if($rows.Count -eq 1){$rows[0]}else{$null}
        $observations=Get-CapacityField $Status 'observations'
        $quotaRows=@($observations|Where-Object {
            $null -ne $binding -and (Get-CapacityField $_ 'native_thread_id') -ceq $binding.thread -and $_.provider -ceq $provider
        })
        $quota=if($quotaRows.Count -eq 1){$quotaRows[0]}else{$null}
        $nativeReason=$null
        if($provider -ceq 'codex' -and $binding -and $binding.cwd){
            try {
                $nativeHome=if($env:CODEX_HOME){$env:CODEX_HOME}else{Join-Path $env:USERPROFILE '.codex'}
                $nativeText=& $m.python -E -s -S -B (Join-Path $release 'tools\bridge_capacity_collector.py') --store $store --native-codex-home $nativeHome --native-codex-thread $binding.thread
                $nativeCode=$LASTEXITCODE
                $nativeRow=$nativeText|ConvertFrom-Json @jsonArgs
                if($nativeCode -ne 0 -or $nativeRow.native_thread_id -cne $binding.thread -or
                   -not ([IO.Path]::GetFullPath([string]$nativeRow.cwd).Equals([IO.Path]::GetFullPath($binding.cwd),[StringComparison]::OrdinalIgnoreCase))){throw 'Native telemetry identity unavailable'}
                $nativeReason=Get-CapacityField $nativeRow 'reason'
                $start=[datetimeoffset]::Parse($binding.start,[Globalization.CultureInfo]::InvariantCulture)
                if($nativeRow.observed_at -and [datetimeoffset]::Parse($nativeRow.observed_at,[Globalization.CultureInfo]::InvariantCulture) -ge $start){$activity=$nativeRow}
                if($nativeRow.quota_observed_at -and [datetimeoffset]::Parse($nativeRow.quota_observed_at,[Globalization.CultureInfo]::InvariantCulture) -ge $start){
                    $quota=[pscustomobject]@{model=$nativeRow.model;effort=$nativeRow.effort;quota_state=$nativeRow.quota_state;quota_windows=$nativeRow.quota_windows;freshness=$nativeRow.quota_freshness;observed_at=$nativeRow.quota_observed_at}
                }
            }catch{$nativeReason='native_telemetry_unavailable'}
        }
        $windows=Get-CapacityField $quota 'quota_windows'
        $stamp=Get-CapacityField $activity 'observed_at'
        $age=$null
        if($stamp){try{$age=($now-[datetimeoffset]::Parse([string]$stamp,[Globalization.CultureInfo]::InvariantCulture)).TotalSeconds}catch{}}
        $fresh=($null -ne $age -and $age -ge 0 -and $age -le 300)
        $auth=Get-CapacityField $activity 'auth_state'
        $availability=Get-CapacityField $activity 'availability_state'
        $work=Get-CapacityField $activity 'activity_state'
        $reason=if(-not $binding){$processReason}elseif(-not $activity){if($nativeReason){$nativeReason}else{'native_activity_unavailable'}}elseif(-not $fresh){'native_activity_stale_or_future'}else{$null}
        $blocked=($availability -cin @('rate_limited','auth_required','access_denied','account_on_hold'))
        $quotaState=Get-CapacityField $quota 'quota_state'
        $resets=@($windows|Where-Object {$null -ne $_ -and $_.used_percent -is [valuetype] -and $_.used_percent -isnot [bool] -and $_.used_percent -ge 100 -and $_.resets_at -is [valuetype] -and $_.resets_at -isnot [bool] -and $_.resets_at -gt $now.ToUnixTimeSeconds() -and $_.resets_at -le 253402300799}|ForEach-Object {[long]$_.resets_at})
        $recheck=if($resets.Count){[datetimeoffset]::FromUnixTimeSeconds(($resets|Measure-Object -Maximum).Maximum).ToString('o')}else{$null}
        [pscustomobject]@{
            agent=$lane;provider=$provider;native_thread_id=$(if($binding){$binding.thread}else{$null});
            native_pid=$(if($binding){$binding.pid}else{$null});
            native_process_start_utc=$(if($binding){$binding.start}else{$null});
            identity_state=$(if($binding){'observed_process_ancestry'}else{'unknown'});
            auth_state=$(if($auth){$auth}else{'unknown'});
            quota_state=$(if($availability -ceq 'rate_limited'){'rate_limit_reported'}else{'unknown'});
            observed_quota_state=$(if($quota){Get-CapacityField $quota 'quota_state'}else{'unknown'});
            activity_state=$(if($work){$work}else{'unknown'});
            availability_state=$(if($availability){$availability}else{'unknown'});
            observed_at=$stamp;observation_age_seconds=$age;
            freshness=$(if($fresh){'fresh'}elseif($stamp){'stale_or_future'}else{'unknown'});
            source=$(Get-CapacityField $activity 'source');reason=$reason;
            alert_id=$(Get-CapacityField $activity 'alert_id');
            first_error_at=$(Get-CapacityField $activity 'first_error_at');
            last_successful_turn_at=$(Get-CapacityField $activity 'last_successful_turn_at');
            observed_model=$(Get-CapacityField $quota 'model');
            observed_effort=$(Get-CapacityField $quota 'effort');
            quota_observed_at=$(Get-CapacityField $quota 'observed_at');
            quota_windows=@($windows|Where-Object {$null -ne $_});
            quota_freshness=$(if($quota){Get-CapacityField $quota 'freshness'}else{'unknown'});
            quota_pool_binding='unverified';next_turn_success_verified=$false;
            quota_accounting_group=('shared_or_unknown_'+$provider);
            independent_capacity=$false;capacity_recheck_at=$recheck;
            next_action=$(if($blocked -or $quotaState -ceq 'exhausted'){'preserve_work_and_reconcile_capacity'}elseif($fresh -and $quotaState -ceq 'observed_headroom'){'consider_existing_lane_at_safe_boundary'}else{'verify_before_dispatch'});
            automatic_handoff_allowed=$false;execution_allowed=$false
        }
    }
}
$reason='unverified_capacity_locator'
try {
    $root=[IO.Path]::GetFullPath($InstallRoot).TrimEnd('\','/')
    $pointer=Assert-CapacityReadPath (Join-Path $root 'current.json') $root
    $jsonArgs=@{ErrorAction='Stop'}
    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$jsonArgs.DateKind='String'}
    $current=Get-Content -LiteralPath $pointer -Raw|ConvertFrom-Json @jsonArgs
    if($current.mode -cne 'metadata_only' -or $current.source_commit -cnotmatch '^[a-f0-9]{40}$' -or $current.manifest_sha256 -cnotmatch '^[A-Fa-f0-9]{64}$'){throw 'Invalid observer pointer'}
    $manifestPath=Assert-CapacityReadPath $current.manifest $root
    if((Get-CapacityReadHash $manifestPath) -ine $current.manifest_sha256){throw 'Observer manifest changed'}
    $m=Get-Content -LiteralPath $manifestPath -Raw|ConvertFrom-Json @jsonArgs
    if($m.schema -cne 'wd.capacity-observer-install.v1' -or $m.execution_mode -cne 'metadata_only' -or $m.source_commit -cne $current.source_commit){throw 'Observer source mismatch'}
    $release=Split-Path $manifestPath -Parent
    $requiredLeaves=@('tools\bridge_capacity_collector.py','tools\bridge_capacity_advisor.py','ops\windows\reboot\Get-WdCapacityStatus.ps1')
    if($Attribution){$requiredLeaves+='tools\bridge_capacity_attribution.py'}
    foreach($leaf in $requiredLeaves){
        $field=$m.files.PSObject.Properties[$leaf]
        if($null -eq $field){throw 'Required reader file is not pinned'}
    }
    foreach($property in $m.files.PSObject.Properties){
        $path=Assert-CapacityReadPath (Join-Path $release $property.Name) $release
        if((Get-CapacityReadHash $path) -ine $property.Value){throw 'Observer source changed'}
    }
    if((Get-CapacityReadHash $PSCommandPath) -ine $m.files.'ops\windows\reboot\Get-WdCapacityStatus.ps1'){throw 'Reader is not the installed version'}
    if(-not [IO.Path]::IsPathRooted($m.python) -or [IO.Path]::GetExtension($m.python) -ine '.exe' -or
        (Get-CapacityReadHash $m.python) -ine $m.python_sha256){throw 'Reader Python changed'}
    $store=Assert-CapacityReadPath $m.store $root
    if($store -ine (Join-Path $root 'observations.sqlite')){throw 'Unrecognized observation store'}
    $reason='status_unavailable'
    # Forward the read-only attribution switch only when explicitly asked.
    $statusArgs=@('--store',$store,'--status')
    if($Attribution){$statusArgs+='--attribution'}
    $text=& $m.python -E -s -S -B (Join-Path $release 'tools\bridge_capacity_collector.py') @statusArgs
    $code=$LASTEXITCODE
    $result=$text|ConvertFrom-Json @jsonArgs
    if($result.schema -cne 'wd.capacity-status.v1' -or $result.execution_allowed -ne $false){throw 'Invalid read-only status result'}
    $result|Add-Member -NotePropertyName installation -NotePropertyValue ([ordered]@{
        source_commit=$current.source_commit;manifest=$manifestPath;manifest_sha256=$current.manifest_sha256;
        status_command=$PSCommandPath;store=$store;source_verified=$true;agent_quota_binding='unverified'})
    if($Summary -or $Agent) {
        $lanes=@(Get-CapacityLaneSummary $result)
        $view=[ordered]@{schema='wd.capacity-summary.v1';observed_at=$result.observed_at;
            installation=$result.installation;agents=$lanes;collection=(Get-CapacityField $result 'collection');
            execution_allowed=$false;note='Process identity, authentication history, quota and activity are separate. Pool bindings remain unverified: never sum same-provider lanes as independent capacity. Reset times are recheck times, not readiness promises. No next-turn success or automatic handoff is established.'}
        if($Attribution){
            $view['attribution']=$result.attribution
            $view['attribution_scope']='all_observations_not_agent_entitlement'
        }
        if($Json){$view|ConvertTo-Json -Depth 32}
        else {
            Write-Output ('WD CAPACITY | observed='+$result.observed_at+' | source='+$current.source_commit+' | read-only')
            $lanes|Select-Object agent,identity_state,auth_state,quota_state,observed_quota_state,activity_state,freshness,observation_age_seconds,quota_pool_binding|Format-Table -AutoSize -Wrap|Out-String -Width 240|Write-Output
            Write-Output $view.note
            if($Attribution){
                Write-Output 'ATTRIBUTION | all observations, not agent entitlement'
                $view.attribution|ConvertTo-Json -Depth 32|Write-Output
            }
        }
    }else{$result|ConvertTo-Json -Depth 32}
    exit $code
} catch {
    [ordered]@{schema='wd.capacity-status.v1';state='unknown';reason=$reason;observed_at=[datetime]::UtcNow.ToString('o');
        execution_allowed=$false;observations=@();source_verified=$false}|ConvertTo-Json -Depth 4
    exit 2
}
