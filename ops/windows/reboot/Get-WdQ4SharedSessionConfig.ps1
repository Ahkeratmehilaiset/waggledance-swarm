#requires -Version 5.1
# Configuration-only, OFF by default. The caller independently authenticates BOTH
# fleet and post-deployment descriptor hashes; matching hashes are not authority.
# No referenced data, pin source, caller identity, freshness or runtime is verified.
# No dispatch, installation, environment changes, query, or network operation.
function ConvertFrom-WdSharedSessionJson {
    param([string]$Text)
    if (-not $Text -or $Text.Length -gt 1048576 -or
        -not $Text.TrimStart().StartsWith('{') -or -not $Text.TrimEnd().EndsWith('}')) { throw 'shared_json_refused' }
    $parsed=ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    if ($parsed -isnot [pscustomobject]) { throw 'shared_json_refused' }
    $pattern='"(?:\\["\\/bfnrt]|\\u[0-9a-fA-F]{4}|[^"\\\x00-\x1f])*"|[{}\[\],:]|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|[ \t\r\n]+|(?<bad>.)'
    $tokens=[regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline)
    $stack=[Collections.Generic.List[object]]::new();$count=0
    foreach ($token in $tokens) {
        $count++; if ($count -gt 131072 -or $token.Groups['bad'].Success) { throw 'shared_json_refused' }
        $v=$token.Value
        if ($v -ceq '{' -or $v -ceq '[') {
            if ($stack.Count -ge 64) { throw 'shared_json_refused' }
            $stack.Add([pscustomobject]@{Object=($v -ceq '{');ExpectKey=$true;Keys=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)})
        } elseif ($v -ceq '}' -or $v -ceq ']') {
            if ($stack.Count -eq 0 -or $stack[$stack.Count-1].Object -ne ($v -ceq '}')) { throw 'shared_json_refused' }
            $stack.RemoveAt($stack.Count-1)
        } elseif ($v -ceq ',') {
            if ($stack.Count -eq 0) { throw 'shared_json_refused' }
            if ($stack[$stack.Count-1].Object) { $stack[$stack.Count-1].ExpectKey=$true }
        } elseif ($v.StartsWith('"') -and $stack.Count -gt 0) {
            $frame=$stack[$stack.Count-1]
            if ($frame.Object -and $frame.ExpectKey) {
                $decoded=ConvertFrom-Json -InputObject ('{"key":'+$v+'}') -ErrorAction Stop
                if ($decoded.key -isnot [string] -or -not $frame.Keys.Add($decoded.key)) { throw 'shared_json_refused' }
                $frame.ExpectKey=$false
            }
        }
    }
    if ($stack.Count -ne 0) { throw 'shared_json_refused' }
    return $parsed
}

function Get-WdQ4SharedSessionConfig {
    [CmdletBinding()]
    param([string]$FleetPath,[string]$FleetSha256,[string]$DescriptorPath,[string]$DescriptorSha256,
          [string]$Lane,[switch]$Enabled)
    if (-not $Enabled) { return $null }
    function Assert-WdSharedPath([string]$Value) {
        if (-not $Value -or $Value.Length -gt 4096 -or $Value -cnotmatch '^[A-Za-z]:\\' -or
            $Value.Substring(2).Contains(':') -or $Value.Contains('/') -or
            $Value.IndexOfAny([char[]]'<>|"*?') -ge 0 -or $Value -match '[\x00-\x1f]') { throw 'shared_path_refused' }
        foreach ($part in $Value.Substring(3).Split('\')) {
            if (-not $part -or $part -in @('.','..') -or $part.Length -gt 255 -or
                $part.StartsWith(' ') -or $part.EndsWith(' ') -or $part.EndsWith('.') -or
                $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') { throw 'shared_path_refused' }
        }
        $full=[IO.Path]::GetFullPath($Value)
        $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($full))
        if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) { throw 'shared_path_refused' }
        $current=$full
        while ($current) {
            try { if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'shared_path_refused' } }
            catch [IO.FileNotFoundException] {} catch [IO.DirectoryNotFoundException] {}
            $parent=[IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }; $current=$parent
        }
        return $full
    }
    function Read-WdSharedPinnedJson([string]$Path,[string]$Pin,[int]$Limit) {
        if ($Pin -cnotmatch '^[0-9a-f]{64}$') { throw 'shared_pin_refused' }
        $pathFull=Assert-WdSharedPath $Path
        $stream=[IO.File]::Open($pathFull,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($stream.Length -le 0 -or $stream.Length -gt $Limit) { throw 'shared_size_refused' }
            $bytes=New-Object byte[] ([int]$stream.Length);$offset=0
            while ($offset -lt $bytes.Length) { $n=$stream.Read($bytes,$offset,$bytes.Length-$offset);if ($n -le 0) { throw 'shared_read_refused' };$offset+=$n }
            if ($stream.ReadByte() -ne -1) { throw 'shared_read_refused' }
        } finally { $stream.Dispose() }
        $sha=[Security.Cryptography.SHA256]::Create()
        try { $actual=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() } finally { $sha.Dispose() }
        if ($actual -cne $Pin) { throw 'shared_pin_refused' }
        return ConvertFrom-WdSharedSessionJson ([Text.UTF8Encoding]::new($false,$true).GetString($bytes))
    }
    try {
        $known=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
        if ($known -cnotcontains $Lane) { throw 'shared_lane_refused' }
        $fleet=Read-WdSharedPinnedJson $FleetPath $FleetSha256 1048576
        $doc=Read-WdSharedPinnedJson $DescriptorPath $DescriptorSha256 65536
        $paths=@('bundle_root','public_plan','interpreter','cwd','binding_path','fixed_plan_path','snapshot_path','runtime_manifest_path')
        $hashes=@('fleet_sha256','interpreter_sha256','binding_sha256','fixed_plan_sha256','snapshot_sha256','runtime_manifest_sha256')
        $numbers=@('interpreter_max_bytes','timeout_seconds','max_output_bytes')
        $required=@('schema','lanes')+$paths+$hashes+$numbers
        if (@($doc.PSObject.Properties).Count -ne $required.Count) { throw 'shared_schema_refused' }
        foreach ($p in $doc.PSObject.Properties) { if ($required -cnotcontains $p.Name) { throw 'shared_schema_refused' } }
        foreach ($key in $required) { if ($null -eq $doc.PSObject.Properties[$key]) { throw 'shared_schema_refused' } }
        if ($doc.schema -isnot [string] -or $doc.schema -cne 'wd.q4-shared-session-config.v1' -or
            $doc.fleet_sha256 -cne $FleetSha256 -or $doc.lanes -isnot [Array] -or $doc.lanes.Count -ne 5) { throw 'shared_schema_refused' }
        $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($entry in $doc.lanes) { if ($entry -isnot [string] -or $known -cnotcontains $entry -or -not $seen.Add($entry)) { throw 'shared_lane_refused' } }
        if ($null -eq $fleet.PSObject.Properties['lanes'] -or $fleet.PSObject.Properties['lanes'].Name -cne 'lanes' -or
            $null -eq $fleet.PSObject.Properties['tools_supervisor'] -or $fleet.PSObject.Properties['tools_supervisor'].Name -cne 'tools_supervisor' -or
            $fleet.lanes -isnot [Array] -or $fleet.lanes.Count -ne 4 -or $fleet.tools_supervisor -isnot [pscustomobject]) { throw 'shared_lane_refused' }
        $seenFleet=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        foreach ($entry in @($fleet.lanes)+@($fleet.tools_supervisor)) {
            if ($entry -isnot [pscustomobject] -or $null -eq $entry.PSObject.Properties['agent'] -or
                $entry.PSObject.Properties['agent'].Name -cne 'agent' -or $entry.agent -isnot [string] -or
                $known -cnotcontains $entry.agent -or -not $seenFleet.Add($entry.agent)) { throw 'shared_lane_refused' }
        }
        if ($fleet.tools_supervisor.agent -cne 'codex-tools-1' -or $seenFleet.Count -ne 5) { throw 'shared_lane_refused' }
        foreach ($key in $hashes) { if ($doc.$key -isnot [string] -or $doc.$key -cnotmatch '^[0-9a-f]{64}$') { throw 'shared_pin_refused' } }
        foreach ($key in $paths) { if ($doc.$key -isnot [string]) { throw 'shared_path_refused' }; $null=Assert-WdSharedPath $doc.$key }
        foreach ($pair in @(@('interpreter_max_bytes',134217728),@('timeout_seconds',1800),@('max_output_bytes',65536))) {
            $n=$doc.($pair[0]); if (($n -isnot [int] -and $n -isnot [long]) -or $n -lt 1 -or $n -gt $pair[1]) { throw 'shared_limit_refused' }
        }
        $result=[ordered]@{lane=$Lane;fleet_path=(Assert-WdSharedPath $FleetPath);fleet_sha256=$FleetSha256}
        foreach ($key in $paths+$hashes+$numbers) { if ($key -cne 'fleet_sha256') { $result[$key]=$doc.$key } }
        return [pscustomobject]$result
    } catch { throw 'q4_shared_session_config_refused' }
}
