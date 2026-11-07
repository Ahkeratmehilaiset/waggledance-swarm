#requires -Version 5.1
<# Fixed on-demand public card publication in the inherited lane session. Default OFF.
Candidate code and the active bridge have independent caller-pinned manifests; the card
request is a caller-pinned typed file. The publisher runs once in a bounded child that alone
sees the candidate pin; the calling session keeps its own bridge pins. Output is rebuilt from
validated fields only. UNKNOWN means do not retry blindly. Pins are caller assertions, not
authentication, policy authorization or proof that a card is true.
#>
[CmdletBinding(PositionalBinding=$false)]
param([switch]$Run, [string]$CandidateBundle='', [string]$ExpectedCandidateManifestSha256='',
      [string]$CardRequest='', [string]$ExpectedCardRequestSha256='', [int]$TimeoutSeconds=120)
$ErrorActionPreference='Stop'
if (-not $Run) { [Console]::Out.WriteLine('{"status":"OFF"}'); return }
$entered=$false
try {
    if ($PSVersionTable.PSEdition -ceq 'Desktop') {
        Import-Module -Name (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    }
    if ($TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 600) { throw 'timeout' }
    function CheckPath([string]$Path,[bool]$Directory=$false) {
        if ($Path -notmatch '^[Cc]:\\' -or $Path.Substring(2).Contains(':') -or $Path.Contains('%') -or
            ($Path -split '[\\/]' | Where-Object { $_ -in @('.','..') })) { throw 'path' }
        $full=[IO.Path]::GetFullPath($Path)
        $item=Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if ([bool]($item.Attributes -band [IO.FileAttributes]::Directory) -ne $Directory) { throw 'type' }
        while ($null -ne $item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
            if ($item -is [IO.DirectoryInfo]) { $item=$item.Parent } else { $item=$item.Directory }
        }
        return $full
    }
    function HashMatches([string]$Path,[string]$Pin) {
        if ($Pin -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'pin' }
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        $sha=[Security.Cryptography.SHA256]::Create()
        try { $actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','') }
        finally { $sha.Dispose(); $stream.Dispose() }
        return $actual -ceq $Pin.ToUpperInvariant()
    }
    function PinnedText([string]$Path,[string]$Pin,[int]$Limit) {
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($stream.Length -le 0 -or $stream.Length -gt $Limit) { throw 'size' }
            $bytes=New-Object byte[] ([int]$stream.Length); $offset=0
            while ($offset -lt $bytes.Length) {
                $n=$stream.Read($bytes,$offset,$bytes.Length-$offset); if ($n -le 0) { throw 'read' }; $offset+=$n
            }
            if ($stream.ReadByte() -ne -1) { throw 'size' }
        } finally { $stream.Dispose() }
        $sha=[Security.Cryptography.SHA256]::Create()
        try { $actual=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','') } finally { $sha.Dispose() }
        if ($actual -cne $Pin.ToUpperInvariant()) { throw 'hash' }
        if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) { throw 'bom' }
        return [Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    }
    function SameNames($Object,[string[]]$Names) {
        if ($Object -isnot [Management.Automation.PSCustomObject]) { return $false }
        $have=@($Object.PSObject.Properties.Name | Sort-Object)
        $want=@($Names | Sort-Object)
        return ($have.Count -eq $want.Count) -and ((Compare-Object $have $want -CaseSensitive -SyncWindow 0) -eq $null)
    }
    $activePin=[string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
    if ($activePin -cnotmatch '^[0-9a-fA-F]{64}$' -or
        $ExpectedCandidateManifestSha256 -cnotmatch '^[0-9a-fA-F]{64}$' -or
        $ExpectedCardRequestSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'pin' }
    $activeBin=CheckPath ([string]$env:WD_BRIDGE_BIN) $true
    $activeBundle=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $activeBin))
    if (-not [IO.Path]::GetFullPath($activeBin).Equals(
            (Join-Path $activeBundle 'tools-bootstrap\.agent-bridge\bin'),[StringComparison]::OrdinalIgnoreCase)) { throw 'active' }
    $activeManifest=CheckPath (Join-Path $activeBundle 'deployment-manifest.json')
    if (-not (HashMatches $activeManifest $activePin)) { throw 'active' }
    $active=Get-Content -LiteralPath $activeManifest -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    foreach ($name in @('Get-BridgeExecutionEvidence.ps1','Write-AgentEvent.ps1')) {
        $path=CheckPath (Join-Path $activeBin $name)
        $pin=[string]$active.files.PSObject.Properties[('tools-bootstrap/.agent-bridge/bin/'+$name)].Value
        if (-not (HashMatches $path $pin)) { throw 'active' }
    }
    $candidate=CheckPath $CandidateBundle $true
    $manifest=CheckPath (Join-Path $candidate 'deployment-manifest.json')
    if (-not (HashMatches $manifest $ExpectedCandidateManifestSha256)) { throw 'candidate' }
    $doc=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    # This catches changed bytes, but the caller must pin the launcher before
    # executing it; already-running code cannot authenticate itself.
    $self=CheckPath $PSCommandPath
    if (-not $self.Equals((Join-Path $candidate 'Invoke-WdQ4PublicCard.ps1'),
                          [StringComparison]::OrdinalIgnoreCase)) { throw 'launcher' }
    $required=@('Invoke-WdQ4PublicCard.ps1','Invoke-WdBridgePython.ps1','BridgeCodeContext.ps1','bridge-code-files.json','wd-fleet.json',
        'tools-bootstrap/tools/wd_q4_public_card_publish_cli.py',
        'tools-bootstrap/tools/wd_q4_public_card_publish.py',
        'tools-bootstrap/tools/wd_q4_public_card_prepare.py',
        'tools-bootstrap/tools/wd_q4_public_card_prefix_verifier.py',
        'tools-bootstrap/tools/wd_mind_public_event_writer.py',
        'tools-bootstrap/tools/wd_q4_real_card_snapshot_cli.py',
        'tools-bootstrap/configs/mind/pilot_non_review_policy.json',
        'tools-bootstrap/configs/mind/pilot_public_status_policy.json')
    foreach ($name in $required) {
        $path=CheckPath (Join-Path $candidate $name.Replace('/','\'))
        $pin=[string]$doc.files.PSObject.Properties[$name].Value
        if (-not (HashMatches $path $pin)) { throw 'candidate' }
    }
    # Typed card request: exact shape of wd.q4-public-card-publish-request.v1. The CLI re-reads
    # it and every input against these pins; claim content is never echoed here.
    $request=CheckPath $CardRequest
    $card=PinnedText $request $ExpectedCardRequestSha256 65536 | ConvertFrom-Json -ErrorAction Stop
    if (-not (SameNames $card @('schema','claim','inputs')) -or [string]$card.schema -cne 'wd.q4-public-card-publish-request.v1' -or
        $card.claim -isnot [Management.Automation.PSCustomObject] -or
        -not (SameNames $card.inputs @('policy','public_policy','card_policy','roster','vocabulary','path_allowlist'))) { throw 'request' }
    foreach ($spec in @($card.inputs.PSObject.Properties)) {
        if (-not (SameNames $spec.Value @('path','sha256')) -or [string]$spec.Value.sha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'request' }
        [void](CheckPath ([string]$spec.Value.path))
    }
    $wrapper=Join-Path $candidate 'Invoke-WdBridgePython.ps1'
    $hostPath=CheckPath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    function PSLiteral([string]$Value) {
        if ($Value -match '[^\x20-\x7e]') { throw 'argument' }
        return "'" + $Value.Replace("'","''") + "'"
    }
    $child="`$ErrorActionPreference='Stop'`n" +
        "try {`n" +
        " Import-Module -Name (Join-Path `$PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop`n" +
        " & $(PSLiteral $wrapper) -VerifyPackage -Tool 'tools/wd_q4_public_card_publish_cli.py' --enabled --request $(PSLiteral $request) --request-sha256 $(PSLiteral $ExpectedCardRequestSha256)`n" +
        " `$code=`$global:LASTEXITCODE; if (`$null -eq `$code) { exit 3 }; exit `$code`n" +
        "} catch { exit 3 }`n"
    $psi=[Diagnostics.ProcessStartInfo]::new()
    $psi.FileName=$hostPath
    $psi.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($child))
    $psi.WorkingDirectory=$candidate
    $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true
    $psi.RedirectStandardInput=$true; $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
    # Child-only pins: the publisher reads the active bridge from the first, the candidate
    # package from the second. This process environment is never modified.
    $psi.EnvironmentVariables['WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256']=$activePin
    $psi.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedCandidateManifestSha256
    $proc=[Diagnostics.Process]::new(); $proc.StartInfo=$psi
    $entered=$true
    try {
        if (-not $proc.Start()) { throw 'start' }
        $proc.StandardInput.Close()
        $outTask=$proc.StandardOutput.ReadToEndAsync()
        $errTask=$proc.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        if (-not $proc.WaitForExit($TimeoutSeconds*1000)) { throw 'timeout' }
        $proc.WaitForExit()
        if (-not $outTask.Wait(5000) -or -not $errTask.Wait(5000)) { throw 'drain' }
        $code=$proc.ExitCode; $text=[string]$outTask.Result
    } finally { $proc.Dispose() }
    if ($text.Length -gt 4096) { throw 'output' }
    if ($code -eq 2 -and $text.Trim().Length -eq 0) { [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2 }
    $lines=@($text -split "`r?`n" | Where-Object { $_.Length -gt 0 })
    if ($code -ne 0 -or $lines.Count -ne 1) { throw 'result' }
    $value=$lines[0] | ConvertFrom-Json -ErrorAction Stop
    $common=@('status','delivery','event_ref_sha256','truth','source_authentication','publisher_authentication','clock_authentication')
    $ref=[string]$value.event_ref_sha256
    if ($ref -cnotmatch '^[0-9a-f]{64}$' -or [string]$value.truth -cne 'UNVERIFIED' -or
        [string]$value.source_authentication -cne 'UNKNOWN' -or [string]$value.publisher_authentication -cne 'UNKNOWN' -or
        [string]$value.clock_authentication -cne 'UNKNOWN') { throw 'result' }
    $tail=',"truth":"UNVERIFIED","source_authentication":"UNKNOWN","publisher_authentication":"UNKNOWN","clock_authentication":"UNKNOWN"}'
    if ((SameNames $value $common) -and [string]$value.status -ceq 'WRITTEN' -and [string]$value.delivery -ceq 'canonical') {
        [Console]::Out.WriteLine('{"status":"WRITTEN","delivery":"canonical","event_ref_sha256":"'+$ref+'"'+$tail); exit 0
    }
    $wal=[string]$value.retained_wal_sha256
    if ((SameNames $value ($common+@('retained_wal_sha256'))) -and [string]$value.status -ceq 'QUEUED' -and
        [string]$value.delivery -ceq 'queued' -and $wal -cmatch '^[0-9a-f]{64}$') {
        [Console]::Out.WriteLine('{"status":"QUEUED","delivery":"queued","event_ref_sha256":"'+$ref+'","retained_wal_sha256":"'+$wal+'"'+$tail); exit 0
    }
    throw 'result'
} catch {
    if ($entered) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}
