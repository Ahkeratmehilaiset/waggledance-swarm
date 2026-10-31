#requires -Version 5.1
# Configuration only. The caller independently anchors fleet/helper/CLI bytes.
# No environment mutation, subprocess, model, network, or connection proof.
function ConvertFrom-WdMindPublicStrictJson {
    param([string] $Text)
    if (-not $Text -or $Text.Length -gt 1048576 -or
        -not $Text.TrimStart().StartsWith('{') -or -not $Text.TrimEnd().EndsWith('}')) {
        throw 'mind_public_config_json_refused'
    }
    $parsed = ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    if ($parsed -isnot [pscustomobject]) { throw 'mind_public_config_json_refused' }
    # Same bounded raw-key collision guard as the existing Q4 config helper.
    $pattern = '"(?:\\["\\/bfnrt]|\\u[0-9a-fA-F]{4}|[^"\\\x00-\x1f])*"|[{}\[\],:]|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|[ \t\r\n]+|(?<bad>.)'
    $stack = [Collections.Generic.List[object]]::new()
    $count = 0
    foreach ($token in [regex]::Matches($Text, $pattern, [Text.RegularExpressions.RegexOptions]::Singleline)) {
        $count++
        if ($count -gt 131072 -or $token.Groups['bad'].Success) { throw 'mind_public_config_json_refused' }
        $value = $token.Value
        if ($value -ceq '{' -or $value -ceq '[') {
            if ($stack.Count -ge 64) { throw 'mind_public_config_json_refused' }
            $stack.Add([pscustomobject]@{Object=($value -ceq '{');ExpectKey=$true;
                Keys=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)})
        } elseif ($value -ceq '}' -or $value -ceq ']') {
            if ($stack.Count -eq 0 -or $stack[$stack.Count-1].Object -ne ($value -ceq '}')) {
                throw 'mind_public_config_json_refused'
            }
            $stack.RemoveAt($stack.Count-1)
        } elseif ($value -ceq ',') {
            if ($stack.Count -eq 0) { throw 'mind_public_config_json_refused' }
            if ($stack[$stack.Count-1].Object) { $stack[$stack.Count-1].ExpectKey=$true }
        } elseif ($value.StartsWith('"') -and $stack.Count -gt 0) {
            $frame = $stack[$stack.Count-1]
            if ($frame.Object -and $frame.ExpectKey) {
                $decoded = ConvertFrom-Json -InputObject ('{"key":'+$value+'}') -ErrorAction Stop
                if ($decoded.key -isnot [string] -or -not $frame.Keys.Add($decoded.key)) {
                    throw 'mind_public_config_json_duplicate_key'
                }
                $frame.ExpectKey=$false
            }
        }
    }
    if ($stack.Count -ne 0) { throw 'mind_public_config_json_refused' }
    return $parsed
}

function Get-WdMindPublicReadConfig {
    <#
    FleetSha256 is supplied only after the launcher checks its external manifest
    anchor. A self-computed digest is consistency, not authorization. Pin-file
    validation is bounded and read-only; it does not authenticate the same-user
    publisher, guarantee freshness or pin later CLI reads against replacement.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $FleetPath,
        [Parameter(Mandatory)] [string] $FleetSha256
    )

    function Assert-PublicPath {
        param([string] $Value, [ValidateSet('File','Directory')] [string] $Kind='File')
        if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Length -gt 4096 -or
            $Value -cnotmatch '^[A-Za-z]:\\' -or $Value.Substring(2).Contains(':') -or
            $Value.Contains('/') -or $Value.IndexOfAny([char[]]'<>|"*?') -ge 0 -or
            $Value -match '[\x00-\x1f]') { throw 'mind_public_config_path_invalid' }
        foreach ($segment in $Value.Substring(3).Split('\')) {
            if (-not $segment -or $segment -ceq '.' -or $segment -ceq '..' -or
                $segment.Length -gt 255 -or $segment.StartsWith(' ') -or
                $segment.EndsWith(' ') -or $segment.EndsWith('.') -or
                $segment -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
                throw 'mind_public_config_path_invalid'
            }
        }
        try {
            $full = [IO.Path]::GetFullPath($Value)
            $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($full))
            if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) {
                throw 'mind_public_config_drive_invalid'
            }
            $current = $full
            while ($current) {
                $attributes = [IO.File]::GetAttributes($current)
                if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'mind_public_config_reparse_refused'
                }
                $isDirectory = ($attributes -band [IO.FileAttributes]::Directory) -ne 0
                if (($current -ceq $full -and (($Kind -ceq 'Directory') -ne $isDirectory)) -or
                    ($current -cne $full -and -not $isDirectory)) {
                    throw 'mind_public_config_path_kind_invalid'
                }
                $parent = [IO.Path]::GetDirectoryName($current)
                if (-not $parent -or $parent -ceq $current) { break }
                $current = $parent
            }
            return $full
        } catch { throw 'mind_public_config_path_refused' }
    }

    function Read-PublicPinnedBytes {
        param([string] $Path, [string] $Sha256, [int] $Limit)
        if ($Sha256 -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'mind_public_config_digest_invalid' }
        $full = Assert-PublicPath -Value $Path
        try {
            $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
            try {
                if ($stream.Length -gt $Limit) { throw 'mind_public_config_file_too_large' }
                $buffer = New-Object byte[] ([int]$stream.Length)
                $offset = 0
                while ($offset -lt $buffer.Length) {
                    $read = $stream.Read($buffer, $offset, $buffer.Length-$offset)
                    if ($read -le 0) { throw 'mind_public_config_file_short_read' }
                    $offset += $read
                }
                if ($stream.ReadByte() -ne -1) { throw 'mind_public_config_file_changed' }
            } finally { $stream.Dispose() }
            $hasher = [Security.Cryptography.SHA256]::Create()
            try { $actual=[BitConverter]::ToString($hasher.ComputeHash($buffer)).Replace('-','').ToLowerInvariant() }
            finally { $hasher.Dispose() }
            if ($actual -cne $Sha256.ToLowerInvariant()) { throw 'mind_public_config_digest_mismatch' }
            return ,$buffer
        } catch { throw 'mind_public_config_pinned_file_refused' }
    }

    try {
        $raw = Read-PublicPinnedBytes -Path $FleetPath -Sha256 $FleetSha256 -Limit 1048576
        $encoding = New-Object Text.UTF8Encoding($false,$true)
        $fleet = ConvertFrom-WdMindPublicStrictJson -Text ($encoding.GetString($raw).TrimStart([char]0xFEFF))
    } catch { throw 'mind_public_config_fleet_refused' }
    $property = $fleet.PSObject.Properties['mind_public_read']
    if ($null -eq $property) { return $null }
    if ($property.Name -cne 'mind_public_read' -or $property.Value -isnot [pscustomobject]) {
        throw 'mind_public_config_invalid'
    }
    $config = $property.Value
    $enabled = $config.PSObject.Properties['enabled']
    if ($null -eq $enabled -or $enabled.Name -cne 'enabled' -or $enabled.Value -isnot [bool]) {
        throw 'mind_public_config_invalid'
    }
    if (-not $enabled.Value) { return $null }
    $legacy = $fleet.PSObject.Properties['mind_read']
    if ($null -ne $legacy) {
        if ($legacy.Name -cne 'mind_read' -or $legacy.Value -isnot [pscustomobject]) {
            throw 'mind_public_config_legacy_invalid'
        }
        $legacyEnabled = $legacy.Value.PSObject.Properties['enabled']
        if ($null -eq $legacyEnabled -or $legacyEnabled.Name -cne 'enabled' -or
            $legacyEnabled.Value -isnot [bool]) { throw 'mind_public_config_legacy_invalid' }
        if ($legacyEnabled.Value) { throw 'mind_public_config_mode_conflict' }
    }
    $names = @('anchor','policy','roster','public_policy')
    $required = @('enabled','audit_root','max_age_seconds')
    foreach ($name in $names) { $required += @(($name+'_path'),($name+'_sha256')) }
    foreach ($name in @($config.PSObject.Properties.Name)) {
        if ($required -cnotcontains $name) { throw 'mind_public_config_unknown_field' }
    }
    foreach ($name in $required) {
        if ($null -eq $config.PSObject.Properties[$name]) { throw 'mind_public_config_field_missing' }
    }
    if ($config.audit_root -isnot [string]) { throw 'mind_public_config_path_invalid' }
    $auditRoot = Assert-PublicPath -Value $config.audit_root -Kind Directory
    $age = $config.max_age_seconds
    if (($age -isnot [int] -and $age -isnot [long]) -or $age -lt 1 -or $age -gt 900) {
        throw 'mind_public_config_age_invalid'
    }
    $limits = @{anchor=16384;policy=65536;roster=1048576;public_policy=65536}
    $out = [ordered]@{
        WD_MIND_PUBLIC_ENABLED='1'
        WD_MIND_PUBLIC_AUDIT_ROOT=$auditRoot
        WD_MIND_PUBLIC_MAX_AGE_SECONDS=$age.ToString([Globalization.CultureInfo]::InvariantCulture)
    }
    foreach ($name in $names) {
        $pathKey=$name+'_path';$hashKey=$name+'_sha256'
        if ($config.$pathKey -isnot [string] -or $config.$hashKey -isnot [string]) {
            throw 'mind_public_config_pin_invalid'
        }
        $path = Assert-PublicPath -Value $config.$pathKey
        [void](Read-PublicPinnedBytes -Path $path -Sha256 $config.$hashKey -Limit $limits[$name])
        $out['WD_MIND_PUBLIC_'+$name.ToUpperInvariant()+'_PATH']=$path
        $out['WD_MIND_PUBLIC_'+$name.ToUpperInvariant()+'_SHA256']=$config.$hashKey.ToLowerInvariant()
    }
    return [pscustomobject]$out
}
