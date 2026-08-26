<#
.SYNOPSIS
    Rule 9b activation construction library. Apply is NOT IMPLEMENTED YET.

.DESCRIPTION
    Implemented so far: ConvertTo-CanonicalJson (v5 slice 1) and the sealed
    runtime manifest byte contract (v5 rollout step 5, bounded first slice).
    the handwritten RFC 8259 encoder that produces the bytes the ConfirmDigest
    is taken over. Its counterpart is canonical_json_bytes in
    ops/windows/reboot/check_rule9b_activation_receipt.py, and the two are held
    byte-identical by tests/tools/test_wd_rule9b_activation.py across both
    Windows PowerShell 5.1 and PowerShell 7.

    Everything else - GitHub-source materialization, object protection, the
    protected broker, the
    dedicated service principal, the GitHub App credential, the three rulesets,
    the ACL/MIC table, the scheduled-task transaction and rollback - lands in
    later slices. Until then this script REFUSES TO RUN rather than performing
    a partial activation. Running an activation that is half-built is exactly
    the failure the whole design exists to prevent.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function ConvertTo-CanonicalJson {
    <#
        Emit exactly the bytes Python's
        json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False)
        emits, so the ConfirmDigest has one value regardless of which producer
        computed it and which PowerShell host ran it.

        This function is deliberately SELF-CONTAINED. It calls no string
        helper, because a canonicalizer that depends on a second function can
        be changed by substituting that function, and because the parity test
        exercises this definition in isolation.

        ConvertTo-Json must never appear in the digest path. Its escaping
        policy differs between the Windows PowerShell 5.1 JavaScriptSerializer
        and the PowerShell 7 System.Text.Json encoder - measured 2026-08-26,
        the two hosts disagreed on the apostrophe, the ampersand and the angle
        brackets - which would make a signed digest depend on which host the
        operator happened to run.

        Escaping is exactly what RFC 8259 requires and nothing more: the
        quotation mark, the reverse solidus, and control characters below
        U+0020. The solidus is NOT escaped. U+007F is NOT escaped. Non-ASCII
        and astral characters are emitted literally.

        AGREEING ON WHAT TO REFUSE IS PART OF THE CONTRACT. Three inputs used
        to be accepted here and refused by Python, and in each case this side
        silently produced something that was not the payload:

        * A lone UTF-16 surrogate. .NET's UTF-8 encoder substitutes U+FFFD
          while Python raises, so the bytes signed here would not be the text
          anybody wrote. Surrogate pairs are now validated and lone halves
          are refused.
        * A non-string dictionary key. Casting the key to a string and then
          indexing the dictionary with that string missed the original entry
          and emitted null for its value - silent data loss, not a type
          mismatch. Non-string keys are now refused before any cast.
        * An integer wider than signed 64-bit. Python encodes it; this side
          cannot represent it. It is now refused explicitly rather than
          reaching a vague unsupported-type error.

        Unsupported types throw rather than falling back to a string
        conversion. A canonicalizer that silently accepts what it does not
        understand signs something nobody reviewed.
    #>
    param([Parameter(Mandatory)] [AllowNull()] $Value)

    if ($null -eq $Value) { return 'null' }

    if ($Value -is [bool]) {
        if ($Value) { return 'true' }
        return 'false'
    }

    if ($Value -is [string]) {
        $sb = [System.Text.StringBuilder]::new()
        [void]$sb.Append('"')
        $chars = $Value.ToCharArray()
        for ($i = 0; $i -lt $chars.Length; $i++) {
            $ch = $chars[$i]
            $code = [int]$ch

            if ($code -ge 0xD800 -and $code -le 0xDBFF) {
                if (($i + 1) -ge $chars.Length) {
                    throw ('canonical JSON: lone high surrogate U+{0:X4} at index {1}; ' -f $code, $i) +
                        'this is not valid Unicode text'
                }
                $low = [int]$chars[$i + 1]
                if ($low -lt 0xDC00 -or $low -gt 0xDFFF) {
                    throw ('canonical JSON: high surrogate U+{0:X4} at index {1} is not ' -f $code, $i) +
                        ('followed by a low surrogate (found U+{0:X4})' -f $low)
                }
                [void]$sb.Append($ch)
                [void]$sb.Append($chars[$i + 1])
                $i++
                continue
            }
            if ($code -ge 0xDC00 -and $code -le 0xDFFF) {
                throw ('canonical JSON: lone low surrogate U+{0:X4} at index {1}; ' -f $code, $i) +
                    'this is not valid Unicode text'
            }

            if ($ch -eq '"') { [void]$sb.Append('\"') }
            elseif ($ch -eq '\') { [void]$sb.Append('\\') }
            elseif ($code -eq 8) { [void]$sb.Append('\b') }
            elseif ($code -eq 9) { [void]$sb.Append('\t') }
            elseif ($code -eq 10) { [void]$sb.Append('\n') }
            elseif ($code -eq 12) { [void]$sb.Append('\f') }
            elseif ($code -eq 13) { [void]$sb.Append('\r') }
            elseif ($code -lt 32) { [void]$sb.Append(('\u{0:x4}' -f $code)) }
            else { [void]$sb.Append($ch) }
        }
        [void]$sb.Append('"')
        return $sb.ToString()
    }

    if ($Value -is [System.Numerics.BigInteger] -or $Value -is [uint64]) {
        # Refused by RANGE, not by type. An in-range [uint64]5 is a value the
        # Python side encodes without complaint, so rejecting it merely for
        # its .NET type would be a divergence in the other direction.
        $asBig = [System.Numerics.BigInteger]$Value
        if ($asBig -lt [System.Numerics.BigInteger]::Parse('-9223372036854775808') -or
            $asBig -gt [System.Numerics.BigInteger]::Parse('9223372036854775807')) {
            throw ('canonical JSON: integer ' + $Value.ToString() + ' is outside signed ' +
                '64-bit range and cannot be represented on this side')
        }
        return $asBig.ToString()
    }

    if ($Value -is [int] -or $Value -is [long] -or $Value -is [int16] -or
        $Value -is [byte] -or $Value -is [uint16] -or $Value -is [uint32]) {
        return [string]([int64]$Value)
    }

    if ($Value -is [double] -or $Value -is [single] -or $Value -is [decimal]) {
        throw 'canonical JSON: floating point has no single reproducible form'
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $keys = [System.Collections.Generic.List[string]]::new()
        foreach ($rawKey in $Value.Keys) {
            if ($rawKey -isnot [string]) {
                throw ('canonical JSON: object key of type ' + $rawKey.GetType().FullName +
                    ' is refused; casting it to a string would look the value up under a ' +
                    'key the dictionary does not hold and silently emit null')
            }
            foreach ($kc in $rawKey.ToCharArray()) {
                $kcode = [int]$kc
                if ($kcode -lt 32 -or $kcode -gt 126) {
                    throw ("canonical JSON: object key '$rawKey' contains a " +
                        'non-printable-ASCII character, whose ordering differs ' +
                        'between Python and .NET')
                }
            }
            [void]$keys.Add($rawKey)
        }
        $keys.Sort([System.StringComparer]::Ordinal)
        $parts = New-Object System.Collections.Generic.List[string]
        foreach ($key in $keys) {
            [void]$parts.Add((ConvertTo-CanonicalJson -Value $key) + ':' +
                (ConvertTo-CanonicalJson -Value $Value[$key]))
        }
        return '{' + ($parts -join ',') + '}'
    }

    if ($Value -is [System.Collections.IEnumerable]) {
        $items = New-Object System.Collections.Generic.List[string]
        foreach ($item in $Value) {
            [void]$items.Add((ConvertTo-CanonicalJson -Value $item))
        }
        return '[' + ($items -join ',') + ']'
    }

    throw ('canonical JSON: unsupported type ' + $Value.GetType().FullName)
}

function Compare-WdUtf8Bytes {
    param(
        [Parameter(Mandatory)] [string] $Left,
        [Parameter(Mandatory)] [string] $Right
    )

    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $a = $utf8.GetBytes($Left)
    $b = $utf8.GetBytes($Right)
    $count = [Math]::Min($a.Length, $b.Length)
    for ($i = 0; $i -lt $count; $i++) {
        if ($a[$i] -lt $b[$i]) { return -1 }
        if ($a[$i] -gt $b[$i]) { return 1 }
    }
    if ($a.Length -lt $b.Length) { return -1 }
    if ($a.Length -gt $b.Length) { return 1 }
    return 0
}

function Assert-WdRuntimeManifestPath {
    param([Parameter(Mandatory)] [string] $Path)

    if (-not $Path -or $Path.StartsWith('/') -or $Path.Contains('\') -or
        $Path.Contains(':')) {
        throw 'runtime manifest path must be repository-relative POSIX text without colon'
    }
    $reserved = @('con','prn','aux','nul','com1','com2','com3','com4','com5',
        'com6','com7','com8','com9','lpt1','lpt2','lpt3','lpt4','lpt5','lpt6',
        'lpt7','lpt8','lpt9')
    foreach ($part in $Path.Split('/')) {
        if (-not $part -or $part -eq '.' -or $part -eq '..') {
            throw 'runtime manifest path contains an empty or traversal component'
        }
        if ($part.EndsWith(' ') -or $part.EndsWith('.')) {
            throw 'runtime manifest path component has a trailing dot or space'
        }
        foreach ($ch in $part.ToCharArray()) {
            if ([int]$ch -lt 32) {
                throw 'runtime manifest path component contains a control character'
            }
        }
        $stem = $part.Split('.')[0].ToLowerInvariant()
        if ($reserved -ccontains $stem) {
            throw 'runtime manifest path component is a reserved Windows device name'
        }
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    try { [void]$strictUtf8.GetBytes($Path) }
    catch { throw 'runtime manifest path is not valid Unicode text' }
}

function New-WdRule9bRuntimeManifestBytes {
    <#
        Construct the exact bytes later written outside a sealed generation.
        This function performs no filesystem, Git, ACL, task or credential
        mutation. The elevated materializer will supply entries obtained from
        pinned git ls-tree/cat-file after that later slice lands.
    #>
    param(
        [Parameter(Mandatory)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string] $ActivationHead,

        [Parameter(Mandatory)]
        [ValidatePattern('^[0-9a-f]{40}$')]
        [string] $ActivationTreeSha,

        [Parameter(Mandatory)]
        [ValidatePattern('^[!-~]{1,128}$')]
        [string] $RuntimeGenerationId,

        [Parameter(Mandatory)]
        [System.Collections.IDictionary[]] $Files
    )

    if ($Files.Count -eq 0) {
        throw 'runtime manifest files must be nonempty'
    }
    $requiredFields = @('path','git_blob_sha1','byte_length','sha256')
    $seen = @{}
    $normalized = New-Object System.Collections.Generic.List[System.Collections.IDictionary]
    foreach ($entry in $Files) {
        if ($null -eq $entry) { throw 'runtime manifest file entry must be an object' }
        $keys = @($entry.Keys | ForEach-Object { [string]$_ })
        if (@($keys | Where-Object { $requiredFields -cnotcontains $_ }).Count -ne 0 -or
            @($requiredFields | Where-Object { $keys -cnotcontains $_ }).Count -ne 0) {
            throw 'runtime manifest file entry schema is not exact'
        }
        $path = [string]$entry['path']
        Assert-WdRuntimeManifestPath -Path $path
        $folded = $path.ToLowerInvariant()
        if ($seen.ContainsKey($folded)) {
            throw ("runtime manifest path collision: '{0}' and '{1}'" -f
                $seen[$folded], $path)
        }
        $seen[$folded] = $path
        if ([string]$entry['git_blob_sha1'] -cnotmatch '^[0-9a-f]{40}$') {
            throw 'runtime manifest git_blob_sha1 is malformed'
        }
        $length = $entry['byte_length']
        if ($length -is [bool] -or $length -isnot [ValueType] -or
            [int64]$length -lt 0 -or [decimal]$length -ne [int64]$length) {
            throw 'runtime manifest byte_length must be a nonnegative integer'
        }
        if ([string]$entry['sha256'] -cnotmatch '^[0-9a-f]{64}$') {
            throw 'runtime manifest sha256 is malformed'
        }
        [void]$normalized.Add([ordered]@{
            path = $path
            git_blob_sha1 = [string]$entry['git_blob_sha1']
            byte_length = [int64]$length
            sha256 = [string]$entry['sha256']
        })
    }

    $sorted = @($normalized)
    [Array]::Sort($sorted, [System.Collections.Generic.Comparer[object]]::Create({
        param($left, $right)
        return Compare-WdUtf8Bytes -Left ([string]$left['path']) -Right ([string]$right['path'])
    }))
    $manifest = [ordered]@{
        schema = 'wd.rule9b.runtime_manifest.v1'
        activation_head = $ActivationHead
        activation_tree_sha = $ActivationTreeSha
        runtime_generation_id = $RuntimeGenerationId
        files = $sorted
    }
    $json = ConvertTo-CanonicalJson -Value $manifest
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    return $utf8.GetBytes($json + "`n")
}

if ($MyInvocation.InvocationName -ne '.') {
    Write-Host 'FAIL-CLOSED: Invoke-WdRule9bActivation.ps1 is not implemented yet.'
    Write-Host 'Only pure construction contracts have landed; Apply remains absent.'
    Write-Host 'No activation, task, credential, ruleset or receipt action is available.'
    exit 2
}
