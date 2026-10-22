#requires -Version 5.1
<#
Future opt-in A4 publication-fault probe. Loading this file only defines helpers.
This uses a hash-verified job primitive and an instrumented extracted publication
fixture, NOT the full adapter or an unmodified producer. Opt-in is an accidental
execution guard, NOT operator authority or Medium-token evidence. The executor,
delivery pins and native permission must be approved independently first.
Native calls/constructor may block: bounded polling is NOT an OS-isolation or
whole-deadline guarantee. No PID kill, before-resume witness or run_start proxy.
#>

function Assert-WdPublicationFilePin {
    param([string] $Path, [string[]] $Sha256, [switch] $ReturnInfo, [switch] $ReturnHash)
    if ($Sha256.Count -lt 1 -or $Sha256.Count -gt 2 -or -not [IO.Path]::IsPathRooted($Path)) { throw 'refused: file pin' }
    foreach ($pin in $Sha256) { if ($pin -cnotmatch '^[a-fA-F0-9]{64}$') { throw 'refused: file pin' } }
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($item.PSIsContainer) { throw 'refused: expected regular file' }
    $check = $item
    while ($null -ne $check) {
        if (($check.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'refused: reparse path' }
        if ($check -is [IO.FileInfo]) { $check = $check.Directory } else { $check = $check.Parent }
    }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($algorithm.ComputeHash($bytes)).Replace('-','') } finally { $algorithm.Dispose() }
    if ($actual -inotIn $Sha256) { throw 'refused: source hash' }
    if ($ReturnInfo) { return [pscustomobject]@{text=([Text.UTF8Encoding]::new($false,$true)).GetString($bytes);sha256=$actual.ToLowerInvariant()} }
    if ($ReturnHash) { return $actual.ToLowerInvariant() }
}

function Write-WdPublicationRecordOnce {
    param([string] $Path, $Record)
    $bytes = [Text.Encoding]::UTF8.GetBytes(($Record | ConvertTo-Json -Depth 8 -Compress))
    $pending = $Path + '.pending'
    $stream = [IO.File]::Open($pending, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
    [IO.File]::Move($pending, $Path)
}

function Get-WdPublicationReady {
    param([string] $FixtureRoot, $Binding, [int] $ExpectedPid)
    $path = Join-Path $FixtureRoot 'ready.json'
    $item = Get-Item -LiteralPath $path -ErrorAction Stop
    if ($item.Length -lt 2 -or $item.Length -gt 16384 -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'refused: ready file' }
    $raw = [IO.File]::ReadAllText($path)
    # The frozen fixture emits literal ASCII keys. Refuse escaped/duplicate
    # keys rather than trusting ConvertFrom-Json's duplicate-key behavior.
    if ($raw -match '\\u[0-9a-fA-F]{4}') { throw 'refused: noncanonical ready keys' }
    foreach ($key in @('record','binding','phase','pid','root','target','temporary','function_sha256','run_id','launch_id','producer_sha256','core_sha256','atomic_write','_sharing_retry')) {
        $expectedCount = if ($key -ceq 'phase') { 2 } else { 1 }
        if ([regex]::Matches($raw, ('"' + [regex]::Escape($key) + '"\s*:')).Count -ne $expectedCount) { throw 'refused: duplicate or missing ready key' }
    }
    $ready = $raw | ConvertFrom-Json -ErrorAction Stop
    $keys = @('record','binding','phase','pid','root','target','temporary','function_sha256')
    if (@(Compare-Object ($keys | Sort-Object) (@($ready.PSObject.Properties.Name) | Sort-Object)).Count) { throw 'refused: ready shape' }
    if ($ready.record -cne 'at_publication_replace' -or $ready.phase -cne $Binding.phase -or
        ($ready.pid -isnot [int] -and $ready.pid -isnot [long]) -or $ready.pid -ne $ExpectedPid -or $ExpectedPid -le 0 -or
        $ready.root -cne $FixtureRoot -or $ready.target -cne (Join-Path $FixtureRoot 'latest.json')) { throw 'refused: foreign ready' }
    $bindingKeys = @('run_id','launch_id','phase','producer_sha256','core_sha256')
    if (@(Compare-Object ($bindingKeys | Sort-Object) (@($ready.binding.PSObject.Properties.Name) | Sort-Object)).Count) { throw 'refused: binding shape' }
    foreach ($key in $bindingKeys) { if ($ready.binding.$key -isnot [string] -or $ready.binding.$key -cne $Binding.$key) { throw 'refused: foreign binding' } }
    $temporary = [IO.Path]::GetFullPath([string]$ready.temporary)
    if ([IO.Path]::GetDirectoryName($temporary) -cne $FixtureRoot -or
        [IO.Path]::GetFileName($temporary) -cnotmatch '^\.latest\.json\.[A-Za-z0-9_-]+\.tmp$') { throw 'refused: foreign temporary' }
    if (@(Compare-Object @('_sharing_retry','atomic_write') (@($ready.function_sha256.PSObject.Properties.Name) | Sort-Object)).Count) { throw 'refused: extraction shape' }
    foreach ($key in @('atomic_write','_sharing_retry')) { if ($ready.function_sha256.$key -cnotmatch '^[0-9a-f]{64}$') { throw 'refused: extraction hash' } }
    if ($ready.function_sha256.atomic_write -cne '803391e2cf8089a72f76a3434665d4296c412694903ddea476ec253c624dd28f' -or
        $ready.function_sha256._sharing_retry -cne '91f92938912f6865c9f59f2900517caa8316d957f74bb09da201f4ce3e2159f1') { throw 'refused: extraction hash' }
    return $ready
}

function Stop-WdPublicationOwnedJob {
    param($Managed, [ValidateRange(1,10)] [int] $DrainSeconds)
    $Managed.Stop()
    $clock = [Diagnostics.Stopwatch]::StartNew()
    do {
        $active = $Managed.ActiveProcesses
        if (($active -isnot [int] -and $active -isnot [uint32] -and $active -isnot [long]) -or $active -lt 0) { return $false }
        if ($active -eq 0) {
            $exited = $Managed.Wait(0)
            return ($exited -is [bool] -and $exited)
        }
        if ($clock.Elapsed.TotalSeconds -ge $DrainSeconds) { return $false }
        Start-Sleep -Milliseconds 10
    } while ($true)
}

function Test-WdPublicationSnapshot {
    param([string] $FixtureRoot, [string] $Phase, [string] $Temporary)
    $old = '{"fixture":"A4","last_ok":"old.capture"}' + "`n"
    $new = '{"fixture":"A4","last_ok":"new.capture"}' + "`n"
    $expected = if ($Phase -ceq 'before_replace') { $old } elseif ($Phase -ceq 'after_replace') { $new } else { throw 'refused: phase' }
    if ([IO.File]::ReadAllText((Join-Path $FixtureRoot 'latest.json')) -cne $expected) { throw 'refused: incomplete pointer' }
    foreach ($name in @('old','new')) {
        if ([IO.File]::ReadAllText((Join-Path $FixtureRoot ($name + '.capture'))) -cne ("synthetic $name capture`n")) { throw 'refused: last_ok changed' }
    }
    $leftovers = @()
    foreach ($item in @(Get-ChildItem -LiteralPath $FixtureRoot -Force)) {
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'refused: unexpected fixture path' }
        if ($item.Name -in @('latest.json','old.capture','new.capture','ready.json','.ready.json.pending')) { continue }
        if ($item.FullName -cne $Temporary -or [IO.File]::ReadAllText($item.FullName) -cne $new) { throw 'refused: unexpected fixture file' }
        $leftovers += $item.Name
    }
    if ($Phase -ceq 'after_replace' -and [IO.File]::Exists($Temporary)) { throw 'refused: replacement not observed' }
    return [pscustomobject]@{pointer_complete=$true;last_ok_intact=$true;temporary_files=@($leftovers | Sort-Object)}
}

function Invoke-WdMindShadowPublicationProbe {
    [CmdletBinding()]
    param(
        [switch] $ApprovedNativeExecution,
        [string] $CodeRoot, [string] $AuditRoot, [string] $PythonPath,
        [string] $ExpectedPythonSha256, [string] $ExpectedFixtureSha256, [string] $ExpectedProbeSha256,
        [string] $RunId, [string] $LaunchId,
        [ValidateSet('before_replace','after_replace')] [string] $Phase,
        [ValidateRange(1,30)] [int] $ReadySeconds = 10,
        [ValidateRange(1,10)] [int] $DrainSeconds = 5
    )
    if (-not $ApprovedNativeExecution) { throw 'refused: explicit approved native opt-in required; opt-in grants no authority' }
    $ErrorActionPreference = 'Stop'
    Set-StrictMode -Version Latest
    if ($RunId -cnotmatch '^[A-Za-z0-9_-]{1,80}$' -or $LaunchId -cnotmatch '^[A-Za-z0-9_-]{1,80}$' -or -not $Phase) { throw 'refused: run binding' }
    $CodeRoot = [IO.Path]::GetFullPath($CodeRoot).TrimEnd('\')
    $AuditRoot = [IO.Path]::GetFullPath($AuditRoot).TrimEnd('\')
    if ($AuditRoot -notmatch '^C:\\.*\\\.codex-audit(?:\\|$)' -or -not [IO.Directory]::Exists($AuditRoot)) { throw 'refused: existing local audit root required' }
    if ([IO.DriveInfo]::new('C').AvailableFreeSpace -lt 3221225472) { throw 'refused: disk floor' }
    if ('WdManagedTurnProcess' -as [type]) { throw 'refused: native type already loaded' }
    $loop = Join-Path $CodeRoot 'ops/windows/reboot/Invoke-WdLaneTurnLoop.ps1'
    $fixture = Join-Path $CodeRoot 'tests/windows/wd_mind_shadow_publication_fixture.py'
    $loopPin = Assert-WdPublicationFilePin $loop @('B6DC04884D298D3C676B7E2688B6AE30E17F157640F94367EAB9EF33446DF4F6','C9CBC5DA5FB75527795F9ECBBE35ABE187298C2251240A5CC885CC331370C9CF') -ReturnInfo
    $producerHash = Assert-WdPublicationFilePin (Join-Path $CodeRoot 'sandbox/mind/mind_capture_producer.py') @('E7719531D6E357407F9B2087FBFEB9D15A847AA7DF1296413C35280E21D26A21','441AD7B5BD9EF0F6BBB53466BD3EAE0A601EFFDC61EC380850CC8313672EA275') -ReturnHash
    $coreHash = Assert-WdPublicationFilePin (Join-Path $CodeRoot 'tools/wd_bridge_mind.py') @('CD009FBC890E15435919F31E52342E6C956825DB523898B2C349B887A64D9557','3AAF56219FDEF4AFB208BFBF7C659422FB9618F2DF95343BD6528148A389029A') -ReturnHash
    Assert-WdPublicationFilePin $fixture $ExpectedFixtureSha256
    Assert-WdPublicationFilePin $MyInvocation.MyCommand.ScriptBlock.File $ExpectedProbeSha256
    Assert-WdPublicationFilePin $PythonPath $ExpectedPythonSha256
    $tokens=$null;$errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseInput($loopPin.text,[ref]$tokens,[ref]$errors)
    $functions=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Initialize-WdNativeTurnType'},$true))
    if ($errors.Count -or $functions.Count -ne 1) { throw 'refused: native source shape' }
    . ([scriptblock]::Create($functions[0].Extent.Text))
    Initialize-WdNativeTurnType
    $type='WdManagedTurnProcess' -as [type]
    $signature=[type[]]@([string],[string[]],[string],[string],[string],[string])
    if ($null -eq $type -or $null -eq $type.GetConstructor($signature)) { throw 'refused: constructor' }
    $run = Join-Path $AuditRoot ('publication-' + $RunId + '-' + $LaunchId + '-' + $Phase)
    if (Test-Path -LiteralPath $run) { throw 'refused: stale run directory' }
    [void][IO.Directory]::CreateDirectory($run)
    # Directory creation itself is not exclusive. Claim it before any other
    # fixture write; a concurrent/stale owner cannot overwrite this marker.
    $claimStream = [IO.File]::Open((Join-Path $run 'owner.once'),[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
    $claimStream.Dispose()
    $fixtureRoot = Join-Path $run 'fixture'
    [void][IO.Directory]::CreateDirectory($fixtureRoot)
    $binding=[ordered]@{run_id=$RunId;launch_id=$LaunchId;phase=$Phase;producer_sha256=$producerHash;core_sha256=$coreHash}
    $inputPath=Join-Path $run 'empty.stdin'
    [IO.File]::WriteAllBytes($inputPath,[byte[]]@())
    $arguments=[string[]]@('-I',$fixture,'--approved-native-fixture','--root',$fixtureRoot,'--code-root',$CodeRoot,'--binding-json',($binding|ConvertTo-Json -Compress))
    $managed=$null;$ready=$null;$snapshot=$null;$reason='';$drained=$false
    try {
        $managed=[WdManagedTurnProcess]::new($PythonPath,$arguments,$CodeRoot,$inputPath,(Join-Path $run 'stdout.log'),(Join-Path $run 'stderr.log'))
        $clock=[Diagnostics.Stopwatch]::StartNew()
        while (-not [IO.File]::Exists((Join-Path $fixtureRoot 'ready.json'))) {
            if ($managed.Wait(0) -or $clock.Elapsed.TotalSeconds -ge $ReadySeconds) { throw 'refused: publication barrier missing' }
            Start-Sleep -Milliseconds 10
        }
        $ready=Get-WdPublicationReady $fixtureRoot $binding $managed.Pid
        # Stop the owned JOB at the parked replacement boundary, never a PID.
        $drained=Stop-WdPublicationOwnedJob $managed $DrainSeconds
        if (-not $drained) { throw 'refused: job drain unknown' }
        $snapshot=Test-WdPublicationSnapshot $fixtureRoot $Phase $ready.temporary
    } catch { $reason=$_.Exception.Message }
    finally {
        if ($null -ne $managed) {
            try { if (-not $drained) { $managed.Stop() } } catch { $reason='refused: stop unknown' }
            $managed.Dispose()
        }
    }
    $result=[ordered]@{schema='wd.publication-probe.v1';binding=$binding;phase=$Phase;ready=$ready;snapshot=$snapshot;job_drain_confirmed=$drained;status='NOT_ACCEPTED';reason=$reason;scope='instrumented extracted publication primitive and owned job, NOT full adapter/producer';whole_A4_PASS=$false;native_permission_inferred=$false;fixture_sha256=$ExpectedFixtureSha256;probe_sha256=$ExpectedProbeSha256;loop_sha256=$loopPin.sha256}
    if ($drained -and $null -ne $snapshot -and -not $reason) { $result.status='PUBLICATION_PRIMITIVE_OBSERVED' }
    Write-WdPublicationRecordOnce (Join-Path $run 'result.json') $result
    return [pscustomobject]$result
}
