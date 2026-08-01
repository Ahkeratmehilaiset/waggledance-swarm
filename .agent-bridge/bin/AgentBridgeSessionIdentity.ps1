#requires -Version 5.1

$script:AgentBridgeSessionIdentityContract = 'v1'
$script:AgentBridgeClaimOwnerContract = 'v1'

function Resolve-AgentBridgeRoot {
    <#
    .SYNOPSIS
        Resolve the shared runtime bridge with cross-runtime precedence.

    .DESCRIPTION
        Python bridge consumers and producers accept the current
        AGENT_BRIDGE_RUNTIME_ROOT name first, the legacy AGENT_BRIDGE_ROOT
        name second, and finally their repo-local default. Keep every
        PowerShell entry point on that exact ordering so reads and writes
        cannot silently split across two bridge trees.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $DefaultRoot
    )

    foreach ($environmentName in @(
            'AGENT_BRIDGE_RUNTIME_ROOT',
            'AGENT_BRIDGE_ROOT'
        )) {
        $environmentValue = [Environment]::GetEnvironmentVariable(
            $environmentName,
            'Process'
        )
        if (-not [string]::IsNullOrWhiteSpace($environmentValue)) {
            return ([string]$environmentValue).Trim()
        }
    }
    return $DefaultRoot
}

function Format-AgentBridgeIdentityDisplay {
    [CmdletBinding()]
    param(
        [AllowEmptyString()] [string] $Value
    )

    $builder = New-Object System.Text.StringBuilder
    foreach ($character in $Value.ToCharArray()) {
        $code = [int][char]$character
        if ($code -eq 0x5c) {
            [void]$builder.Append('\\')
            continue
        }
        if ($code -eq 0x27) {
            [void]$builder.Append("\'")
            continue
        }
        if ($code -eq 0x09) {
            [void]$builder.Append('\t')
            continue
        }
        if ($code -eq 0x0a) {
            [void]$builder.Append('\n')
            continue
        }
        if ($code -eq 0x0d) {
            [void]$builder.Append('\r')
            continue
        }
        if ([char]::IsControl([char]$character) -or
            $code -eq 0x0085 -or
            $code -eq 0x2028 -or
            $code -eq 0x2029) {
            [void]$builder.Append(('\u{0:X4}' -f $code))
            continue
        }
        [void]$builder.Append($character)
    }
    return $builder.ToString()
}

function Assert-AgentBridgeSessionIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $RequestedAgent,
        [switch] $AllowInternalStaleLeaseRelease
    )

    $boundAgent = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_AGENT',
        'Process'
    )
    $agentPattern = '^[a-z][a-z0-9_-]{1,32}\z'
    if ([string]::IsNullOrWhiteSpace($RequestedAgent) -or
        $RequestedAgent -cnotmatch $agentPattern) {
        $requestedDisplay = Format-AgentBridgeIdentityDisplay -Value $RequestedAgent
        throw "identity_mismatch: requested agent '$requestedDisplay' is malformed"
    }
    if ([string]::IsNullOrWhiteSpace($boundAgent)) {
        if ($AllowInternalStaleLeaseRelease -and $RequestedAgent -ceq 'system') {
            return
        }
        if ($RequestedAgent -cin @('operator', 'system')) {
            throw (
                "identity_mismatch: reserved agent '{0}' requires a verified bound or internal caller" -f
                $RequestedAgent
            )
        }
        return
    }
    if ($boundAgent -cnotmatch $agentPattern) {
        $boundDisplay = Format-AgentBridgeIdentityDisplay -Value $boundAgent
        throw "identity_mismatch: AGENT_BRIDGE_AGENT '$boundDisplay' is malformed"
    }
    if ($AllowInternalStaleLeaseRelease -and $RequestedAgent -ceq 'system') {
        return
    }
    if ($RequestedAgent -ceq 'system') {
        throw 'identity_mismatch: system agent has no public bridge authority'
    }
    if ($boundAgent -cne $RequestedAgent) {
        throw (
            "identity_mismatch: session agent '{0}' cannot act as requested agent '{1}'" -f
            $boundAgent,
            $RequestedAgent
        )
    }
}

function ConvertFrom-AgentBridgeCanonicalUtc {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Value
    )

    # PowerShell 7.6+ materializes canonical ISO JSON strings as DateTime
    # values during ConvertFrom-Json. Preserve compatibility with Windows
    # PowerShell (which leaves them as strings) while still rejecting
    # unspecified DateTime values that carry no emitted UTC/offset marker.
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTime]) {
        $dateTimeValue = [DateTime]$Value
        if ($dateTimeValue.Kind -eq [DateTimeKind]::Unspecified) {
            return $null
        }
        return [DateTimeOffset]::new($dateTimeValue.ToUniversalTime())
    }
    if ($Value -isnot [string]) {
        return $null
    }

    $text = [string]$Value
    if ($text -cnotmatch (
            '^[0-9]{4}-[0-9]{2}-[0-9]{2}T' +
            '[0-9]{2}:[0-9]{2}:[0-9]{2}' +
            '(?:\.[0-9]{1,7})?' +
            '(?:Z|[+-][0-9]{2}:[0-9]{2})\z'
        )) {
        return $null
    }

    $formats = [string[]]@(
        "yyyy-MM-dd'T'HH:mm:ssK",
        "yyyy-MM-dd'T'HH:mm:ss.FFFFFFFK"
    )
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParseExact(
            $text,
            $formats,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::None,
            [ref]$parsed
        )) {
        return $null
    }

    return $parsed.ToUniversalTime()
}

function Assert-AgentBridgeFiniteJsonNumbers {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Json
    )

    $inString = $false
    $escaped = $false
    for ($index = 0; $index -lt $Json.Length; $index++) {
        $character = $Json[$index]
        if ($inString) {
            if ($escaped) {
                $escaped = $false
                continue
            }
            if ($character -eq '\') {
                $escaped = $true
                continue
            }
            if ($character -eq '"') {
                $inString = $false
            }
            continue
        }
        if ($character -eq '"') {
            $inString = $true
            continue
        }

        foreach ($token in @('-Infinity', 'Infinity', 'NaN')) {
            if ($index + $token.Length -gt $Json.Length) {
                continue
            }
            if (
                $Json.Substring($index, $token.Length) -cne $token
            ) {
                continue
            }
            $beforeIsIdentifier = (
                $index -gt 0 -and
                [char]::IsLetterOrDigit($Json[$index - 1])
            )
            $afterIndex = $index + $token.Length
            $afterIsIdentifier = (
                $afterIndex -lt $Json.Length -and
                [char]::IsLetterOrDigit($Json[$afterIndex])
            )
            if (-not $beforeIsIdentifier -and -not $afterIsIdentifier) {
                throw "non-finite JSON number is not permitted: $token"
            }
        }
    }
}

function Assert-AgentBridgeStrictJsonLexemes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Json
    )

    $numberPattern = (
        '^-?(?:0|[1-9][0-9]*)' +
        '(?:\.[0-9]+)?' +
        '(?:[eE][+-]?[0-9]+)?'
    )
    for ($index = 0; $index -lt $Json.Length; $index++) {
        $character = $Json[$index]
        $codePoint = [int]$character
        $isJsonWhitespace = (
            $codePoint -eq 0x20 -or
            $codePoint -eq 0x09 -or
            $codePoint -eq 0x0A -or
            $codePoint -eq 0x0D
        )
        if ($isJsonWhitespace) {
            continue
        }
        if ($character -eq '"') {
            $stringClosed = $false
            for ($index++; $index -lt $Json.Length; $index++) {
                $stringCharacter = $Json[$index]
                $stringCodePoint = [int]$stringCharacter
                if ($stringCharacter -eq '"') {
                    $stringClosed = $true
                    break
                }
                if ($stringCodePoint -le 0x1F) {
                    throw (
                        "unescaped JSON string control character at " +
                        "index $index"
                    )
                }
                if ($stringCharacter -ne '\') {
                    continue
                }
                $index++
                if ($index -ge $Json.Length) {
                    throw 'unterminated JSON string escape'
                }
                $escapeCharacter = $Json[$index]
                if ($escapeCharacter -cin @('"', '\', '/', 'b', 'f', 'n', 'r', 't')) {
                    continue
                }
                if ($escapeCharacter -cne 'u') {
                    throw (
                        "invalid JSON string escape '\{0}' at index {1}" -f
                        $escapeCharacter,
                        ($index - 1)
                    )
                }
                if ($index + 4 -ge $Json.Length) {
                    throw 'incomplete JSON Unicode escape'
                }
                for ($offset = 1; $offset -le 4; $offset++) {
                    if ($Json[$index + $offset] -cnotmatch '[0-9A-Fa-f]') {
                        throw 'invalid JSON Unicode escape'
                    }
                }
                $index += 4
            }
            if (-not $stringClosed) {
                throw 'unterminated JSON string'
            }
            continue
        }
        if ($character -cin @('{', '}', '[', ']', ':', ',')) {
            continue
        }

        $literal = $null
        foreach ($candidate in @('true', 'false', 'null')) {
            if (
                $index + $candidate.Length -le $Json.Length -and
                $Json.Substring($index, $candidate.Length) -ceq $candidate
            ) {
                $literal = $candidate
                break
            }
        }
        if ($null -ne $literal) {
            $afterLiteral = $index + $literal.Length
            if ($afterLiteral -lt $Json.Length) {
                $after = $Json[$afterLiteral]
                $afterCodePoint = [int]$after
                $afterIsJsonWhitespace = (
                    $afterCodePoint -eq 0x20 -or
                    $afterCodePoint -eq 0x09 -or
                    $afterCodePoint -eq 0x0A -or
                    $afterCodePoint -eq 0x0D
                )
                if (
                    -not $afterIsJsonWhitespace -and
                    $after -notin @(',', ']', '}')
                ) {
                    throw "invalid JSON literal boundary at index $index"
                }
            }
            $index = $afterLiteral - 1
            continue
        }

        if ($character -ne '-' -and -not [char]::IsDigit($character)) {
            throw "invalid JSON token at index $index"
        }

        if ($index -gt 0) {
            $before = $Json[$index - 1]
            $beforeCodePoint = [int]$before
            $beforeIsJsonWhitespace = (
                $beforeCodePoint -eq 0x20 -or
                $beforeCodePoint -eq 0x09 -or
                $beforeCodePoint -eq 0x0A -or
                $beforeCodePoint -eq 0x0D
            )
            if (
                -not $beforeIsJsonWhitespace -and
                $before -notin @(':', '[', ',')
            ) {
                throw "invalid JSON number boundary before index $index"
            }
        }
        $numberMatch = [regex]::Match(
            $Json.Substring($index),
            $numberPattern
        )
        if (-not $numberMatch.Success) {
            throw "invalid JSON number token at index $index"
        }
        $afterIndex = $index + $numberMatch.Length
        if ($afterIndex -lt $Json.Length) {
            $after = $Json[$afterIndex]
            $afterCodePoint = [int]$after
            $afterIsJsonWhitespace = (
                $afterCodePoint -eq 0x20 -or
                $afterCodePoint -eq 0x09 -or
                $afterCodePoint -eq 0x0A -or
                $afterCodePoint -eq 0x0D
            )
            if (
                -not $afterIsJsonWhitespace -and
                $after -notin @(',', ']', '}')
            ) {
                throw "invalid JSON number boundary after index $index"
            }
        }
        $index = $afterIndex - 1
    }
}

function Read-AgentBridgeJsonStringToken {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Json,
        [Parameter(Mandatory)] [int] $StartIndex
    )

    $builder = [System.Text.StringBuilder]::new()
    for ($index = $StartIndex + 1; $index -lt $Json.Length; $index++) {
        $character = $Json[$index]
        if ($character -ceq '"') {
            return [pscustomobject]@{
                value = $builder.ToString()
                end_index = $index
            }
        }
        if ($character -cne '\') {
            [void]$builder.Append($character)
            continue
        }

        $index++
        if ($index -ge $Json.Length) {
            throw 'unterminated JSON string escape'
        }
        $escape = $Json[$index]
        if ($escape -ceq '"' -or $escape -ceq '\' -or $escape -ceq '/') {
            [void]$builder.Append($escape)
            continue
        }
        if ($escape -ceq 'b') {
            [void]$builder.Append([char]0x08)
            continue
        }
        if ($escape -ceq 'f') {
            [void]$builder.Append([char]0x0c)
            continue
        }
        if ($escape -ceq 'n') {
            [void]$builder.Append([char]0x0a)
            continue
        }
        if ($escape -ceq 'r') {
            [void]$builder.Append([char]0x0d)
            continue
        }
        if ($escape -ceq 't') {
            [void]$builder.Append([char]0x09)
            continue
        }
        if ($escape -cne 'u' -or $index + 4 -ge $Json.Length) {
            throw 'invalid JSON string escape'
        }
        $codeUnit = [Convert]::ToInt32(
            $Json.Substring($index + 1, 4),
            16
        )
        [void]$builder.Append([char]$codeUnit)
        $index += 4
    }
    throw 'unterminated JSON string'
}

function Assert-AgentBridgeUniqueJsonObjectNames {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Json
    )

    $containers = [System.Collections.Generic.Stack[object]]::new()
    for ($index = 0; $index -lt $Json.Length; $index++) {
        $character = $Json[$index]
        if ($character -ceq '"') {
            $token = Read-AgentBridgeJsonStringToken `
                -Json $Json `
                -StartIndex $index
            $lookahead = [int]$token.end_index + 1
            while (
                $lookahead -lt $Json.Length -and
                $Json[$lookahead] -in @(' ', "`t", "`r", "`n")
            ) {
                $lookahead++
            }
            if (
                $lookahead -lt $Json.Length -and
                $Json[$lookahead] -ceq ':'
            ) {
                if (
                    $containers.Count -eq 0 -or
                    [string]$containers.Peek().kind -cne 'object'
                ) {
                    throw 'JSON property name is outside an object'
                }
                foreach ($nameCharacter in ([string]$token.value).ToCharArray()) {
                    if ([int]$nameCharacter -gt 0x7f) {
                        throw (
                            'bridge JSON object property names must be ASCII'
                        )
                    }
                }
                $names = $containers.Peek().names
                if (-not $names.Add([string]$token.value)) {
                    throw 'duplicate JSON object property name is not permitted'
                }
            }
            $index = [int]$token.end_index
            continue
        }
        if ($character -ceq '{') {
            $containers.Push([pscustomobject]@{
                kind = 'object'
                names = [System.Collections.Generic.HashSet[string]]::new(
                    [System.StringComparer]::OrdinalIgnoreCase
                )
            })
            continue
        }
        if ($character -ceq '[') {
            $containers.Push([pscustomobject]@{
                kind = 'array'
                names = $null
            })
            continue
        }
        if ($character -ceq '}' -or $character -ceq ']') {
            if ($containers.Count -eq 0) {
                throw 'unbalanced JSON container'
            }
            [void]$containers.Pop()
        }
    }
    if ($containers.Count -ne 0) {
        throw 'unterminated JSON container'
    }
}

function Assert-AgentBridgeFiniteParsedJsonValue {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Value
    )

    if ($null -eq $Value) {
        return
    }
    if ($Value -is [double]) {
        $doubleValue = [double]$Value
        if (
            [double]::IsNaN($doubleValue) -or
            [double]::IsInfinity($doubleValue)
        ) {
            throw 'non-finite parsed JSON number is not permitted'
        }
        return
    }
    if ($Value -is [single]) {
        $singleValue = [single]$Value
        if (
            [single]::IsNaN($singleValue) -or
            [single]::IsInfinity($singleValue)
        ) {
            throw 'non-finite parsed JSON number is not permitted'
        }
        return
    }
    if ($Value -is [System.Array]) {
        foreach ($item in $Value) {
            Assert-AgentBridgeFiniteParsedJsonValue -Value $item
        }
        return
    }
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in @($Value.PSObject.Properties)) {
            Assert-AgentBridgeFiniteParsedJsonValue -Value $property.Value
        }
    }
}

function ConvertFrom-AgentBridgeJson {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Json
    )

    Assert-AgentBridgeFiniteJsonNumbers -Json $Json
    Assert-AgentBridgeStrictJsonLexemes -Json $Json
    Assert-AgentBridgeUniqueJsonObjectNames -Json $Json
    $testJson = Get-Command Test-Json -ErrorAction SilentlyContinue
    if ($null -ne $testJson) {
        $isStrictJson = Test-Json -Json $Json -ErrorAction Stop
        if (-not $isStrictJson) {
            throw 'bridge JSON is not strict RFC JSON'
        }
    }

    $convertFromJson = Get-Command ConvertFrom-Json
    $parameters = @{}
    if ($convertFromJson.Parameters.ContainsKey('DateKind')) {
        # Keep the exact wire text so security validation cannot be weakened
        # by PowerShell 7.6 auto-materializing noncanonical ISO-like strings.
        $parameters['DateKind'] = 'String'
    }
    if ($convertFromJson.Parameters.ContainsKey('NoEnumerate')) {
        # Without -NoEnumerate, pwsh collapses a singleton top-level JSON
        # array into its sole element. Stored-record callers must be able to
        # reject that array instead of mistaking it for a JSON object.
        $parameters['NoEnumerate'] = $true
    }
    $parsed = $Json | ConvertFrom-Json @parameters
    Assert-AgentBridgeFiniteParsedJsonValue -Value $parsed
    if ($parsed -is [System.Array]) {
        # Windows PowerShell already preserves arrays, but a normal function
        # return would enumerate them again. Keep the root container intact on
        # both engines while leaving scalar/object return semantics unchanged.
        Write-Output -NoEnumerate $parsed
        return
    }
    return $parsed
}

function Get-AgentBridgeSha256Hex {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [byte[]] $Bytes
    )

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return [System.BitConverter]::ToString(
            $sha256.ComputeHash($Bytes)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Read-AgentBridgeStrictUtf8JsonSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath
    )

    $capture = Get-AgentBridgeExclusiveRawFileSnapshot `
        -LiteralPath $LiteralPath `
        -Context 'bridge JSON record'
    $bytes = [byte[]]$capture.bytes
    if (
        $bytes.Length -ge 3 -and
        $bytes[0] -eq 0xEF -and
        $bytes[1] -eq 0xBB -and
        $bytes[2] -eq 0xBF
    ) {
        # Python's utf-8 + json.loads claim reader rejects an initial BOM.
        # Reject it explicitly instead of letting PowerShell's file cmdlets
        # silently consume it and create a cross-runtime trust difference.
        throw "UTF-8 BOM is not permitted in bridge JSON: $LiteralPath"
    }

    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $text = $strictUtf8.GetString($bytes)
    return [pscustomobject]@{
        # Keep the exact authorized bytes as one property value. Returning a
        # bare byte[] from a PowerShell function would enumerate it.
        bytes = $bytes
        text = $text
        sha256 = [string]$capture.sha256
        length = [long]$capture.length
    }
}

function Read-AgentBridgeStrictUtf8JsonText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath
    )

    return [string](
        Read-AgentBridgeStrictUtf8JsonSnapshot -LiteralPath $LiteralPath
    ).text
}

function Get-AgentBridgeExactProperty {
    <#
    .SYNOPSIS
        Resolve one stored JSON property without PowerShell case folding.

    .DESCRIPTION
        PSCustomObject member lookup is case-insensitive, while the Python
        work-queue schema is case-sensitive. Enumerating the property bag and
        comparing names ordinally prevents an unknown case variant from being
        promoted into an identity, authority, or persistence field.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $InputObject,
        [Parameter(Mandatory)] [string] $Name
    )

    foreach ($property in @($InputObject.PSObject.Properties)) {
        if ([string]$property.Name -ceq $Name) {
            return $property
        }
    }
    return $null
}

function ConvertTo-BridgePositiveInt32 {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Value,
        [switch] $RejectStringWhitespace
    )

    if ($null -eq $Value -or $Value -is [bool]) { return 0 }
    $text = ''
    if ($Value -is [string]) {
        $text = if ($RejectStringWhitespace) {
            [string]$Value
        } else {
            ([string]$Value).Trim()
        }
        if ($text -cnotmatch '^[0-9]+\z') { return 0 }
    } elseif (
        $Value -is [byte] -or
        $Value -is [sbyte] -or
        $Value -is [int16] -or
        $Value -is [uint16] -or
        $Value -is [int32] -or
        $Value -is [uint32] -or
        $Value -is [int64] -or
        $Value -is [uint64]
    ) {
        $text = [Convert]::ToString(
            $Value,
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    } else {
        return 0
    }

    $parsed = 0
    if (-not [int]::TryParse(
            $text,
            [System.Globalization.NumberStyles]::None,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$parsed
        ) -or $parsed -le 0) {
        return 0
    }
    return $parsed
}

function Get-AgentBridgeClaimText {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Claim,
        [Parameter(Mandatory)] [string] $Name
    )

    $property = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name $Name
    if ($null -eq $property -or $property.Value -isnot [string]) {
        return ''
    }
    return [string]$property.Value
}

function ConvertTo-AgentBridgeClaimStringArray {
    [CmdletBinding()]
    param(
        [AllowNull()] [object] $Value,
        [switch] $SplitComma
    )

    if (
        $Value -isnot [string] -and
        $Value -isnot [System.Array] -and
        $Value -isnot [System.Collections.IList]
    ) {
        return @()
    }
    $result = New-Object System.Collections.Generic.List[string]
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' (
        [System.StringComparer]::Ordinal
    )
    # Enumerate the supplied collection directly so a nested list remains one
    # non-string entry and is rejected instead of being pipeline-flattened.
    foreach ($entry in $Value) {
        if ($entry -isnot [string]) { continue }
        $parts = if ($SplitComma) {
            @(([string]$entry).Split(','))
        } else {
            @([string]$entry)
        }
        foreach ($part in $parts) {
            $normalized = ([string]$part).Trim()
            if (-not $normalized -or -not $seen.Add($normalized)) {
                continue
            }
            [void]$result.Add($normalized)
        }
    }
    return @($result)
}

function ConvertTo-AgentBridgeCanonicalClaim {
    <#
    .SYNOPSIS
        Return a pure, allowlisted Claim persistence projection.

    .DESCRIPTION
        This helper only normalizes fields after callers have completed every
        raw authorization, identity, privilege, and lease-eligibility check.
        It deliberately has no authority semantics of its own.

        The coercions mirror waggledance.core.work_queue._read_claim_file:
        identity/text scalars must already be strings, write_scope accepts
        comma-packed string entries, capabilities accepts string entries
        without comma splitting, and integer metadata must be positive Int32.
        Unknown fields, raw owner_token, and the legacy pid field are omitted.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Claim,
        [switch] $SparseOptionalFields
    )

    $writeScopeValue = $null
    $writeScopeProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'write_scope'
    if ($null -ne $writeScopeProperty) {
        $writeScopeValue = $writeScopeProperty.Value
    }
    $leaseSecondsValue = $null
    $leaseSecondsProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'lease_seconds'
    if ($null -ne $leaseSecondsProperty) {
        $leaseSecondsValue = $leaseSecondsProperty.Value
    }
    $payload = [ordered]@{
        agent = Get-AgentBridgeClaimText -Claim $Claim -Name 'agent'
        task_id = Get-AgentBridgeClaimText -Claim $Claim -Name 'task_id'
        summary = Get-AgentBridgeClaimText -Claim $Claim -Name 'summary'
        mode = Get-AgentBridgeClaimText -Claim $Claim -Name 'mode'
        write_scope = @(
            ConvertTo-AgentBridgeClaimStringArray `
                -Value $writeScopeValue `
                -SplitComma
        )
        run_id = Get-AgentBridgeClaimText -Claim $Claim -Name 'run_id'
        claimed_at_utc = Get-AgentBridgeClaimText `
            -Claim $Claim `
            -Name 'claimed_at_utc'
        last_heartbeat_utc = Get-AgentBridgeClaimText `
            -Claim $Claim `
            -Name 'last_heartbeat_utc'
        lease_seconds = ConvertTo-BridgePositiveInt32 `
            -Value $leaseSecondsValue
        claim_lease_expires_utc = Get-AgentBridgeClaimText `
            -Claim $Claim `
            -Name 'claim_lease_expires_utc'
    }

    foreach ($field in @(
            'session_id',
            'owner_session_id',
            'owner_token_sha256'
        )) {
        $value = Get-AgentBridgeClaimText -Claim $Claim -Name $field
        if (-not $SparseOptionalFields -or $value) {
            $payload[$field] = $value
        }
    }

    $ownerPidValue = $null
    $ownerPidProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_pid'
    if ($null -ne $ownerPidProperty) {
        $ownerPidValue = $ownerPidProperty.Value
    }
    $ownerPid = ConvertTo-BridgePositiveInt32 `
        -Value $ownerPidValue `
        -RejectStringWhitespace
    if (-not $SparseOptionalFields -or $ownerPid -gt 0) {
        $payload['owner_pid'] = $ownerPid
    }

    $ownerProcessStartUtc = Get-AgentBridgeClaimText `
        -Claim $Claim `
        -Name 'owner_process_start_utc'
    if (-not $SparseOptionalFields -or $ownerProcessStartUtc) {
        $payload['owner_process_start_utc'] = $ownerProcessStartUtc
    }

    foreach ($field in @(
            'role',
            'agent_uuid',
            'writer_pid_semantics',
            'cwd',
            'git_branch'
        )) {
        $value = Get-AgentBridgeClaimText -Claim $Claim -Name $field
        if ($value) {
            $payload[$field] = $value
        }
    }

    $capabilitiesValue = $null
    $capabilitiesProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'capabilities'
    if ($null -ne $capabilitiesProperty) {
        $capabilitiesValue = $capabilitiesProperty.Value
    }
    $capabilities = @(
        ConvertTo-AgentBridgeClaimStringArray -Value $capabilitiesValue
    )
    if ($capabilities.Count -gt 0) {
        $payload['capabilities'] = $capabilities
    }

    $writerPidValue = $null
    $writerPidProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'writer_pid'
    if ($null -ne $writerPidProperty) {
        $writerPidValue = $writerPidProperty.Value
    }
    $writerPid = ConvertTo-BridgePositiveInt32 -Value $writerPidValue
    if ($writerPid -gt 0) {
        $payload['writer_pid'] = $writerPid
    }

    return [pscustomobject]$payload
}

function Assert-AgentBridgeActiveClaimRawAuthorityFields {
    <#
    .SYNOPSIS
        Validate the raw fields that authorize an active-claim mutation.

    .DESCRIPTION
        Call this before owner checks or canonical persistence projection.
        Stale-lease recovery deliberately does not use this helper because it
        must remain able to archive malformed legacy claims fail-safely.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [object] $Record,
        [Parameter(Mandatory)] [string] $ClaimPath
    )

    $modeProperty = Get-AgentBridgeExactProperty `
        -InputObject $Record `
        -Name 'mode'
    if (
        $null -eq $modeProperty -or
        $modeProperty.Value -isnot [string] -or
        [string]$modeProperty.Value -cnotin @('read-only', 'write')
    ) {
        throw "claim field 'mode' must be exact and canonical: $ClaimPath"
    }

    $scopeProperty = Get-AgentBridgeExactProperty `
        -InputObject $Record `
        -Name 'write_scope'
    if (
        $null -eq $scopeProperty -or
        $scopeProperty.Value -isnot [System.Array]
    ) {
        throw (
            "claim field 'write_scope' must be an array of strings: " +
            $ClaimPath
        )
    }
    $normalizedScope = New-Object System.Collections.Generic.List[string]
    foreach ($scopeEntry in @($scopeProperty.Value)) {
        if ($scopeEntry -isnot [string]) {
            throw (
                "claim field 'write_scope' must be an array of strings: " +
                $ClaimPath
            )
        }
        foreach ($scopePart in ([string]$scopeEntry).Split(',')) {
            $scope = $scopePart.Trim()
            if ([string]::IsNullOrWhiteSpace($scope)) {
                throw ((
                    "claim field 'write_scope' is malformed: {0}: " +
                    "write_scope entries must be non-empty paths"
                ) -f
                    $ClaimPath
                )
            }
            if ($scope -cnotmatch '^[\x20-\x7E]*\z') {
                throw "claim field 'write_scope' is malformed: $ClaimPath"
            }
            $normalized = ($scope -replace '\\', '/').ToLowerInvariant()
            if ($normalized.StartsWith('/') -or $normalized.Contains(':')) {
                throw "claim field 'write_scope' is malformed: $ClaimPath"
            }
            $segments = @(
                $normalized.Split(
                    [char[]]@('/'),
                    [System.StringSplitOptions]::None
                )
            )
            foreach ($segment in $segments) {
                if (
                    $segment -ceq '' -or
                    $segment -ceq '.' -or
                    $segment -ceq '..' -or
                    $segment.EndsWith('.') -or
                    $segment.EndsWith(' ')
                ) {
                    throw "claim field 'write_scope' is malformed: $ClaimPath"
                }
            }
            [void]$normalizedScope.Add($normalized)
        }
    }
    if (
        [string]$modeProperty.Value -ceq 'write' -and
        $normalizedScope.Count -eq 0
    ) {
        throw "active write claim requires a usable write_scope: $ClaimPath"
    }

    $leaseProperty = Get-AgentBridgeExactProperty `
        -InputObject $Record `
        -Name 'lease_seconds'
    if (
        $null -eq $leaseProperty -or
        $leaseProperty.Value -is [bool] -or
        (
            $leaseProperty.Value -isnot [int] -and
            $leaseProperty.Value -isnot [long]
        ) -or
        [long]$leaseProperty.Value -le 0 -or
        [long]$leaseProperty.Value -gt [int]::MaxValue
    ) {
        throw (
            "claim field 'lease_seconds' must be a positive Int32: " +
            $ClaimPath
        )
    }

    foreach ($timestampField in @(
            'claimed_at_utc',
            'last_heartbeat_utc'
        )) {
        $timestampProperty = Get-AgentBridgeExactProperty `
            -InputObject $Record `
            -Name $timestampField
        if (
            $null -eq $timestampProperty -or
            $null -eq (
                ConvertFrom-AgentBridgeCanonicalUtc `
                    -Value $timestampProperty.Value
            )
        ) {
            throw (
                "claim field '{0}' must be canonical UTC: {1}" -f
                $timestampField,
                $ClaimPath
            )
        }
    }
    $leaseExpiryProperty = Get-AgentBridgeExactProperty `
        -InputObject $Record `
        -Name 'claim_lease_expires_utc'
    if (
        $null -ne $leaseExpiryProperty -and
        $null -eq (
            ConvertFrom-AgentBridgeCanonicalUtc `
                -Value $leaseExpiryProperty.Value
        )
    ) {
        throw (
            "claim field 'claim_lease_expires_utc' must be canonical UTC: " +
            $ClaimPath
        )
    }

    $ownerFields = @(
        'owner_session_id',
        'owner_token_sha256',
        'owner_pid',
        'owner_process_start_utc'
    )
    $ownerProperties = @{}
    foreach ($ownerField in $ownerFields) {
        $property = Get-AgentBridgeExactProperty `
            -InputObject $Record `
            -Name $ownerField
        if ($null -ne $property) {
            $ownerProperties[$ownerField] = $property
        }
    }
    if ($ownerProperties.Count -gt 0) {
        if ($ownerProperties.Count -ne $ownerFields.Count) {
            throw "claim owner generation fields must be complete: $ClaimPath"
        }
        if (
            $ownerProperties['owner_session_id'].Value -isnot [string] -or
            [string]$ownerProperties['owner_session_id'].Value -cnotmatch
                '^[A-Za-z0-9._:-]{1,128}\z'
        ) {
            throw "claim field 'owner_session_id' is malformed: $ClaimPath"
        }
        if (
            $ownerProperties['owner_token_sha256'].Value -isnot [string] -or
            [string]$ownerProperties['owner_token_sha256'].Value -cnotmatch
                '^[0-9a-f]{64}\z'
        ) {
            throw "claim field 'owner_token_sha256' is malformed: $ClaimPath"
        }
        $ownerPid = $ownerProperties['owner_pid'].Value
        if (
            $ownerPid -is [bool] -or
            ($ownerPid -isnot [int] -and $ownerPid -isnot [long]) -or
            [long]$ownerPid -le 0 -or
            [long]$ownerPid -gt [int]::MaxValue
        ) {
            throw (
                "claim field 'owner_pid' must be a positive Int32: " +
                $ClaimPath
            )
        }
        if (
            $null -eq (
                ConvertFrom-AgentBridgeCanonicalUtc `
                    -Value (
                        $ownerProperties[
                            'owner_process_start_utc'
                        ].Value
                    )
            )
        ) {
            throw (
                "claim field 'owner_process_start_utc' must be canonical " +
                "UTC: $ClaimPath"
            )
        }
    }
}

function Assert-AgentBridgePlainDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $Context
    )

    try {
        $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    } catch {
        throw "$Context is missing or unreadable: $LiteralPath"
    }
    if (-not [bool]$item.PSIsContainer) {
        throw "$Context must be a directory: $LiteralPath"
    }
    $linkTypeProperty = $item.PSObject.Properties['LinkType']
    if (
        ($null -ne $linkTypeProperty -and
            $null -ne $linkTypeProperty.Value) -or
        (
            ([System.IO.FileAttributes]$item.Attributes -band
                [System.IO.FileAttributes]::ReparsePoint) -ne 0
        )
    ) {
        throw "$Context must not be a reparse link: $LiteralPath"
    }
}

function Ensure-AgentBridgePlainDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $Context
    )

    if (-not (Test-Path -LiteralPath $LiteralPath)) {
        $fullPath = [System.IO.Path]::GetFullPath($LiteralPath)
        $parentPath = [System.IO.Path]::GetDirectoryName($fullPath)
        if ([string]::IsNullOrWhiteSpace($parentPath)) {
            throw "$Context parent path is unavailable: $LiteralPath"
        }
        Assert-AgentBridgePlainDirectory `
            -LiteralPath $parentPath `
            -Context "$Context parent"
        $parentPin = $null
        try {
            $parentPin = Enter-AgentBridgeParentDirectoryPin `
                -ChildPath $fullPath `
                -Context "$Context creation"
            Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
            if (-not (Test-Path -LiteralPath $fullPath)) {
                try {
                    [void](New-Item `
                        -ItemType Directory `
                        -Path $fullPath `
                        -ErrorAction Stop)
                } catch {
                    $createError = $_.Exception
                    Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
                    # Another cooperating writer may have won CreateNew.
                    # Suppress only that benign collision; the plain-directory
                    # gate below still rejects a file, junction, or reparse
                    # object created by a non-cooperating process.
                    if (-not (Test-Path -LiteralPath $fullPath)) {
                        throw $createError
                    }
                }
            }
            Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
            Assert-AgentBridgePlainDirectory `
                -LiteralPath $fullPath `
                -Context $Context
        } finally {
            Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
        }
        return
    }
    Assert-AgentBridgePlainDirectory `
        -LiteralPath $LiteralPath `
        -Context $Context
}

function Assert-AgentBridgeExistingQueueDirectories {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $WorkQueueDir
    )

    foreach ($directory in @(
            [pscustomobject]@{
                name = 'claims'
                context = 'active claims directory'
            },
            [pscustomobject]@{
                name = 'done'
                context = 'claim archive directory'
            }
        )) {
        $path = Join-Path $WorkQueueDir ([string]$directory.name)
        if (Test-Path -LiteralPath $path) {
            Assert-AgentBridgePlainDirectory `
                -LiteralPath $path `
                -Context ([string]$directory.context)
        }
    }
}

function Enter-AgentBridgeMutationLock {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BridgeRoot,
        [int] $TimeoutMilliseconds = 30000
    )

    Ensure-AgentBridgePlainDirectory `
        -LiteralPath $BridgeRoot `
        -Context 'bridge root'
    $workQueueDir = Join-Path $BridgeRoot 'work_queue'
    Ensure-AgentBridgePlainDirectory `
        -LiteralPath $workQueueDir `
        -Context 'work queue directory'
    Assert-AgentBridgeExistingQueueDirectories -WorkQueueDir $workQueueDir
    $lockPath = Join-Path $workQueueDir '.claims.mutation.lock'
    if (Test-Path -LiteralPath $lockPath) {
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $lockPath `
            -Context 'claim mutation lock'
    }
    $parentPin = $null
    $stream = $null
    $deadline = [DateTime]::UtcNow.AddMilliseconds($TimeoutMilliseconds)
    try {
        $parentPin = Enter-AgentBridgeParentDirectoryPin `
            -ChildPath $lockPath `
            -Context 'claim mutation lock'
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        $stream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::ReadWrite
        )
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $parentPin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $lockPath `
            -Context 'claim mutation lock'
        while ($true) {
            $locked = $false
            try {
                $stream.Lock(0, 1)
                $locked = $true
                Assert-AgentBridgeExclusiveHandleIdentity `
                    -Stream $stream `
                    -Context 'claim mutation lock'
                Assert-AgentBridgeRegularUnlinkedFile `
                    -LiteralPath $lockPath `
                    -Context 'claim mutation lock'
                Assert-AgentBridgePlainDirectory `
                    -LiteralPath $BridgeRoot `
                    -Context 'bridge root'
                Assert-AgentBridgePlainDirectory `
                    -LiteralPath $workQueueDir `
                    -Context 'work queue directory'
                Assert-AgentBridgeExistingQueueDirectories `
                    -WorkQueueDir $workQueueDir
                Assert-AgentBridgeChildHandleParentPin `
                    -Pin $parentPin `
                    -ChildHandle $stream.SafeFileHandle
                Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
                return [pscustomobject]@{
                    stream = $stream
                    parent_pin = $parentPin
                }
            } catch [System.IO.IOException] {
                if ($locked) {
                    try { $stream.Unlock(0, 1) } catch {}
                }
                if ([DateTime]::UtcNow -ge $deadline) {
                    throw "timed out acquiring claim mutation lock: $lockPath"
                }
                Start-Sleep -Milliseconds 25
            }
        }
    } catch {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
        Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
        throw
    }
}

function Exit-AgentBridgeMutationLock {
    [CmdletBinding()]
    param(
        [AllowNull()] $Lock
    )

    if ($null -eq $Lock) { return }
    $stream = if ($Lock -is [System.IO.FileStream]) {
        $Lock
    } else {
        $Lock.stream
    }
    $parentPin = if ($Lock -is [System.IO.FileStream]) {
        $null
    } else {
        $Lock.parent_pin
    }
    try {
        $stream.Unlock(0, 1)
    } finally {
        try {
            $stream.Dispose()
        } finally {
            Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
        }
    }
}

function Write-AgentBridgeNonThrowingWarning {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Message
    )

    try {
        Write-Warning -Message $Message -WarningAction Continue
    } catch {
        try {
            [Console]::Error.WriteLine("WARNING: $Message")
        } catch {}
    }
}

function Get-AgentBridgeRawFileSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath
    )

    $bytes = [System.IO.File]::ReadAllBytes($LiteralPath)
    return [pscustomobject]@{
        bytes = $bytes
        sha256 = Get-AgentBridgeSha256Hex -Bytes $bytes
        length = [long]$bytes.Length
    }
}

function Test-AgentBridgeRawFileSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength
    )

    try {
        if (
            -not (Test-Path `
                -LiteralPath $LiteralPath `
                -PathType Leaf `
                -ErrorAction Stop)
        ) {
            return $false
        }
        $snapshot = Get-AgentBridgeRawFileSnapshot -LiteralPath $LiteralPath
    } catch {
        return $false
    }
    return (
        [long]$snapshot.length -eq $ExpectedLength -and
        [string]$snapshot.sha256 -ceq $ExpectedSha256
    )
}

function New-AgentBridgeCasArtifactPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BasePath,
        [Parameter(Mandatory)] [string] $Label
    )

    return (
        "$BasePath.$Label.$PID." +
        "$([guid]::NewGuid().ToString('N'))"
    )
}

function Assert-AgentBridgeRegularUnlinkedFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [string] $Context = 'claim transaction artifact'
    )

    try {
        $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
    } catch {
        throw "$Context is missing or unreadable: $LiteralPath"
    }
    if (
        $item -isnot [System.IO.FileInfo] -or
        [bool]$item.PSIsContainer
    ) {
        throw "$Context is not a regular file: $LiteralPath"
    }

    # LinkType is available on both supported Windows PowerShell engines.
    # Refuse to publish when the engine cannot prove that the file has no
    # hard-link, symbolic-link, or junction identity.
    $linkTypeProperty = $item.PSObject.Properties['LinkType']
    if ($null -eq $linkTypeProperty) {
        throw "$Context link identity is unavailable: $LiteralPath"
    }
    if ($null -ne $linkTypeProperty.Value) {
        throw (
            (
                "$Context must not be a hard link or reparse link: {0} " +
                "(LinkType={1})"
            ) -f
            $LiteralPath,
            [string]$linkTypeProperty.Value
        )
    }
    if (
        ([System.IO.FileAttributes]$item.Attributes -band
            [System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "$Context must not be a reparse point: $LiteralPath"
    }
}

function Restore-AgentBridgeAppendLength {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [Parameter(Mandatory)] [long] $OriginalLength,
        [string] $Context = 'bridge append target'
    )

    $Stream.SetLength($OriginalLength)
    $Stream.Flush($true)
    if ([long]$Stream.Length -ne $OriginalLength) {
        throw (
            "$Context rollback length mismatched: expected " +
            "$OriginalLength; actual $($Stream.Length)"
        )
    }
}

function Add-AgentBridgeBytesToRegularUnlinkedFile {
    <#
    .SYNOPSIS
        Append trusted bytes without following a linked bridge file.

    .DESCRIPTION
        The direct parent is pinned without delete sharing and the opened child
        handle must resolve inside that exact directory generation before any
        byte is written. The original length is recorded; any write or
        post-write identity failure truncates and durably flushes the same open
        inode before the error is surfaced, so a raced hard-link alias is not
        left with a committed append. Callers may retry IOException failures
        for ordinary writer contention.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [string] $Context = 'bridge append target'
    )

    $parent = [System.IO.Path]::GetDirectoryName(
        [System.IO.Path]::GetFullPath($LiteralPath)
    )
    Assert-AgentBridgePlainDirectory `
        -LiteralPath $parent `
        -Context "$Context parent"
    if (Test-Path -LiteralPath $LiteralPath) {
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $LiteralPath `
            -Context $Context
    }

    $parentPin = $null
    $stream = $null
    $originalLength = [long]-1
    $mutationStarted = $false
    $appendCommitted = $false
    $finalizationFailures = @()
    try {
        $parentPin = Enter-AgentBridgeParentDirectoryPin `
            -ChildPath $LiteralPath `
            -Context $Context
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        $stream = [System.IO.File]::Open(
            $LiteralPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        # APPEND V2 MARKER: bind opened child to pinned parent before write.
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $parentPin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context $Context
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $LiteralPath `
            -Context $Context
        Assert-AgentBridgePlainDirectory `
            -LiteralPath $parent `
            -Context "$Context parent"
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        $originalLength = [long]$stream.Length
        [void]$stream.Seek(0, [System.IO.SeekOrigin]::End)
        $mutationStarted = $true
        $stream.Write($Bytes, 0, $Bytes.Length)
        $stream.Flush($true)
        # APPEND V2 MARKER: no failure after this point may retain the append.
        Assert-AgentBridgeExclusiveHandleIdentity `
            -Stream $stream `
            -Context $Context
        Assert-AgentBridgeRegularUnlinkedFile `
            -LiteralPath $LiteralPath `
            -Context $Context
        Assert-AgentBridgePlainDirectory `
            -LiteralPath $parent `
            -Context "$Context parent"
        Assert-AgentBridgeChildHandleParentPin `
            -Pin $parentPin `
            -ChildHandle $stream.SafeFileHandle
        Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        # This is the canonical commit boundary. Resource-finalization errors
        # after every durable write and identity gate has passed must never be
        # surfaced as an ordinary append failure: a caller retry would append
        # the same logical record a second time.
        $appendCommitted = $true
    } catch {
        $appendError = $_.Exception
        if (
            $mutationStarted -and
            $null -ne $stream -and
            $originalLength -ge 0
        ) {
            try {
                Restore-AgentBridgeAppendLength `
                    -Stream $stream `
                    -OriginalLength $originalLength `
                    -Context $Context
            } catch {
                $rollbackError = $_.Exception
                $ambiguousMessage = (
                    "{0} append failed and rollback failed; append " +
                    "outcome is ambiguous (append_error={1}; " +
                    "rollback_error={2})"
                ) -f
                    $Context,
                    $appendError.Message,
                    $rollbackError.Message
                $ambiguousError = [System.IO.IOException]::new(
                    $ambiguousMessage,
                    $appendError
                )
                $ambiguousError.Data['AgentBridgeAppendAmbiguous'] = $true
                throw $ambiguousError
            }
            $rolledBackMessage = (
                "{0} append was rejected and durably rolled back to " +
                "length {1}: {2}"
            ) -f
                $Context,
                $originalLength,
                $appendError.Message
            $rolledBackError = [System.IO.IOException]::new(
                $rolledBackMessage,
                $appendError
            )
            $rolledBackError.Data['AgentBridgeAppendRolledBack'] = $true
            throw $rolledBackError
        }
        throw $appendError
    } finally {
        if ($null -ne $stream) {
            try {
                $stream.Dispose()
            } catch {
                $finalizationFailures += $_.Exception
            }
        }
        try {
            Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
        } catch {
            $finalizationFailures += $_.Exception
        }
        if ($finalizationFailures.Count -gt 0) {
            $finalizationMessages = @(
                $finalizationFailures | ForEach-Object { $_.Message }
            ) -join '; '
            if ($appendCommitted) {
                $finalizationWarning = (
                    "{0} canonical append committed and verified, but " +
                    "resource finalization reported: {1}; treating the " +
                    "append as committed without retry or spool"
                ) -f $Context, $finalizationMessages
            } else {
                $finalizationWarning = (
                    "{0} resource finalization also reported: {1}"
                ) -f $Context, $finalizationMessages
            }
            Write-AgentBridgeNonThrowingWarning -Message $finalizationWarning
        }
    }
}

function Get-AgentBridgeExclusiveRawFileCapture {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [string] $Context = 'claim transaction artifact'
    )

    $stream = $null
    try {
        # CAS V2 EXISTING MARKER: open quarantined path exclusively.
        $stream = [System.IO.File]::Open(
            $LiteralPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::None
        )
        if ([long]$stream.Length -gt [int]::MaxValue) {
            throw "$Context is too large to snapshot safely: $LiteralPath"
        }
        $length = [int]$stream.Length
        $bytes = [byte[]]::new($length)
        $offset = 0
        while ($offset -lt $length) {
            $read = $stream.Read($bytes, $offset, $length - $offset)
            if ($read -le 0) {
                throw "$Context handle ended before expected bytes: $LiteralPath"
            }
            $offset += $read
        }

        # Preserve a completed same-handle byte capture even when the final
        # pathname/link identity gate rejects the object. Callers that moved a
        # source into quarantine must restore this captured generation, never
        # an older pre-Move authorization snapshot.
        $identityError = $null
        try {
            # FileShare.None prevents rename/replacement. A concurrent
            # hard-link creation is detected from this same handle's native
            # link count before it closes (including Windows PowerShell 5.1).
            # CAS V2 EXISTING MARKER: final quarantined identity gate.
            Assert-AgentBridgeExclusiveHandleIdentity `
                -Stream $stream `
                -Context $Context
            Assert-AgentBridgeRegularUnlinkedFile `
                -LiteralPath $LiteralPath `
                -Context $Context
        } catch {
            $identityError = $_.Exception
        }
        return [pscustomobject]@{
            bytes = $bytes
            length = [long]$bytes.Length
            sha256 = Get-AgentBridgeSha256Hex -Bytes $bytes
            identity_verified = [bool]($null -eq $identityError)
            identity_error = $identityError
        }
    } catch {
        throw "$Context is missing, unreadable, or changed: $LiteralPath`: $($_.Exception.Message)"
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Get-AgentBridgeExclusiveRawFileSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [string] $Context = 'claim transaction artifact'
    )

    $capture = Get-AgentBridgeExclusiveRawFileCapture `
        -LiteralPath $LiteralPath `
        -Context $Context
    if (-not [bool]$capture.identity_verified) {
        throw (
            "$Context is missing, unreadable, or changed: " +
            "$LiteralPath`: $($capture.identity_error.Message)"
        )
    }
    return $capture
}

function Initialize-AgentBridgeExactDeleteType {
    if ($null -ne ('AgentBridgeExactFileDeleteV3' -as [type])) {
        return
    }

    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class AgentBridgeExactFileDeleteV3
{
    public sealed class Result
    {
        public bool Succeeded { get; set; }
        public string Error { get; set; }
    }

    public sealed class LinkIdentityResult
    {
        public bool Succeeded { get; set; }
        public string Error { get; set; }
        public uint NumberOfLinks { get; set; }
        public bool IsDirectory { get; set; }
        public bool IsReparsePoint { get; set; }
    }

    public sealed class DirectoryPinResult
    {
        public bool Succeeded { get; set; }
        public string Error { get; set; }
        public SafeFileHandle Handle { get; set; }
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FILETIME
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct BY_HANDLE_FILE_INFORMATION
    {
        public uint FileAttributes;
        public FILETIME CreationTime;
        public FILETIME LastAccessTime;
        public FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct FILE_DISPOSITION_INFO
    {
        [MarshalAs(UnmanagedType.Bool)]
        public bool DeleteFile;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GetFileInformationByHandle(
        SafeFileHandle file,
        out BY_HANDLE_FILE_INFORMATION information);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetFileInformationByHandle(
        SafeFileHandle file,
        int informationClass,
        ref FILE_DISPOSITION_INFO information,
        uint informationSize);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle file,
        StringBuilder filePath,
        uint filePathLength,
        uint flags);

    private const uint GENERIC_READ = 0x80000000;
    private const uint DELETE = 0x00010000;
    private const uint FILE_READ_ATTRIBUTES = 0x00000080;
    private const uint FILE_SHARE_READ = 0x00000001;
    private const uint FILE_SHARE_WRITE = 0x00000002;
    private const uint OPEN_EXISTING = 3;
    private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const uint FILE_ATTRIBUTE_DIRECTORY = 0x00000010;
    private const uint FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400;
    private const int FILE_DISPOSITION_INFO_CLASS = 4;

    private static Result Failure(string message)
    {
        return new Result { Succeeded = false, Error = message };
    }

    private static string Win32Error(string operation)
    {
        int code = Marshal.GetLastWin32Error();
        return operation + " failed (win32=" + code + "): " +
            new Win32Exception(code).Message;
    }

    private static SafeFileHandle OpenPinnedDirectoryHandle(string path)
    {
        return CreateFile(
            path,
            FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            IntPtr.Zero,
            OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
    }

    private static bool SameFileIdentity(
        BY_HANDLE_FILE_INFORMATION left,
        BY_HANDLE_FILE_INFORMATION right)
    {
        return left.VolumeSerialNumber == right.VolumeSerialNumber &&
            left.FileIndexHigh == right.FileIndexHigh &&
            left.FileIndexLow == right.FileIndexLow;
    }

    private static Result ValidatePlainDirectoryHandle(
        SafeFileHandle handle,
        string operation)
    {
        if (handle == null || handle.IsInvalid || handle.IsClosed)
            return Failure(operation + " handle is unavailable");
        BY_HANDLE_FILE_INFORMATION information;
        if (!GetFileInformationByHandle(handle, out information))
            return Failure(Win32Error(operation + " metadata read"));
        if ((information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0)
            return Failure(operation + " target is not a directory");
        if ((information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
            return Failure(operation + " target is a reparse point");
        return new Result { Succeeded = true, Error = String.Empty };
    }

    public static DirectoryPinResult PinPlainDirectory(string path)
    {
        SafeFileHandle handle = OpenPinnedDirectoryHandle(path);
        if (handle == null || handle.IsInvalid)
        {
            if (handle != null) handle.Dispose();
            return new DirectoryPinResult {
                Succeeded = false,
                Error = Win32Error("parent directory pin open"),
                Handle = null
            };
        }
        Result validation = ValidatePlainDirectoryHandle(
            handle,
            "parent directory pin");
        if (!validation.Succeeded)
        {
            handle.Dispose();
            return new DirectoryPinResult {
                Succeeded = false,
                Error = validation.Error,
                Handle = null
            };
        }
        Result pathValidation = ValidateDirectoryLexicalPath(path, handle);
        if (!pathValidation.Succeeded)
        {
            handle.Dispose();
            return new DirectoryPinResult {
                Succeeded = false,
                Error = pathValidation.Error,
                Handle = null
            };
        }
        return new DirectoryPinResult {
            Succeeded = true,
            Error = String.Empty,
            Handle = handle
        };
    }

    public static Result ValidatePinnedDirectory(
        string path,
        SafeFileHandle pinnedHandle)
    {
        Result pinnedValidation = ValidatePlainDirectoryHandle(
            pinnedHandle,
            "pinned parent directory");
        if (!pinnedValidation.Succeeded) return pinnedValidation;

        SafeFileHandle currentHandle = OpenPinnedDirectoryHandle(path);
        if (currentHandle == null || currentHandle.IsInvalid)
        {
            if (currentHandle != null) currentHandle.Dispose();
            return Failure(Win32Error("pinned parent path re-open"));
        }
        try
        {
            Result currentValidation = ValidatePlainDirectoryHandle(
                currentHandle,
                "current parent directory");
            if (!currentValidation.Succeeded) return currentValidation;
            Result pinnedPathValidation = ValidateDirectoryLexicalPath(
                path,
                pinnedHandle);
            if (!pinnedPathValidation.Succeeded)
                return pinnedPathValidation;
            Result currentPathValidation = ValidateDirectoryLexicalPath(
                path,
                currentHandle);
            if (!currentPathValidation.Succeeded)
                return currentPathValidation;
            BY_HANDLE_FILE_INFORMATION pinnedInformation;
            BY_HANDLE_FILE_INFORMATION currentInformation;
            if (!GetFileInformationByHandle(
                    pinnedHandle,
                    out pinnedInformation))
                return Failure(Win32Error("pinned parent identity read"));
            if (!GetFileInformationByHandle(
                    currentHandle,
                    out currentInformation))
                return Failure(Win32Error("current parent identity read"));
            if (!SameFileIdentity(pinnedInformation, currentInformation))
                return Failure("parent directory generation changed");
            return new Result { Succeeded = true, Error = String.Empty };
        }
        finally
        {
            currentHandle.Dispose();
        }
    }

    private static string FinalPath(SafeFileHandle handle)
    {
        StringBuilder buffer = new StringBuilder(32768);
        uint length = GetFinalPathNameByHandle(
            handle,
            buffer,
            (uint)buffer.Capacity,
            0);
        if (length == 0)
            throw new Win32Exception(
                Marshal.GetLastWin32Error(),
                "final handle path read failed");
        if (length >= buffer.Capacity)
            throw new IOException("final handle path exceeded safe buffer");
        return buffer.ToString();
    }

    private static string TrimDirectoryPath(string path)
    {
        string root = Path.GetPathRoot(path);
        if (String.Equals(path, root, StringComparison.OrdinalIgnoreCase))
            return path;
        return path.TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
    }

    private static string NormalizeFinalPath(string path)
    {
        string normalized = path;
        if (normalized.StartsWith(
                @"\\?\UNC\",
                StringComparison.OrdinalIgnoreCase))
            normalized = @"\\" + normalized.Substring(8);
        else if (normalized.StartsWith(
                @"\\?\",
                StringComparison.OrdinalIgnoreCase))
            normalized = normalized.Substring(4);
        return TrimDirectoryPath(Path.GetFullPath(normalized));
    }

    private static Result ValidateDirectoryLexicalPath(
        string expectedPath,
        SafeFileHandle directoryHandle)
    {
        try
        {
            string expected = TrimDirectoryPath(
                Path.GetFullPath(expectedPath));
            string actual = NormalizeFinalPath(FinalPath(directoryHandle));
            if (!String.Equals(
                    expected,
                    actual,
                    StringComparison.OrdinalIgnoreCase))
                return Failure(
                    "parent directory resolved through a different " +
                    "filesystem path (expected=" + expected +
                    "; actual=" + actual + ")");
            return new Result { Succeeded = true, Error = String.Empty };
        }
        catch (Exception exception)
        {
            return Failure(
                exception.GetType().Name + ": " + exception.Message);
        }
    }

    public static Result ValidateChildInPinnedDirectory(
        SafeFileHandle childHandle,
        SafeFileHandle pinnedDirectoryHandle)
    {
        try
        {
            string pinnedPath = NormalizeFinalPath(
                FinalPath(pinnedDirectoryHandle));
            string childPath = NormalizeFinalPath(FinalPath(childHandle));
            string childParent = TrimDirectoryPath(
                Path.GetDirectoryName(childPath));
            if (!String.Equals(
                    childParent,
                    pinnedPath,
                    StringComparison.OrdinalIgnoreCase))
                return Failure(
                    "child handle resolved outside pinned parent directory");
            return new Result { Succeeded = true, Error = String.Empty };
        }
        catch (Exception exception)
        {
            return Failure(
                exception.GetType().Name + ": " + exception.Message);
        }
    }

    public static LinkIdentityResult GetLinkIdentity(SafeFileHandle handle)
    {
        BY_HANDLE_FILE_INFORMATION information;
        if (!GetFileInformationByHandle(handle, out information))
        {
            return new LinkIdentityResult {
                Succeeded = false,
                Error = Win32Error("handle link identity read")
            };
        }
        return new LinkIdentityResult {
            Succeeded = true,
            Error = String.Empty,
            NumberOfLinks = information.NumberOfLinks,
            IsDirectory = (
                information.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0,
            IsReparsePoint = (
                information.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0
        };
    }

    public static Result DeleteIfMatches(
        string path,
        string expectedSha256,
        long expectedLength,
        SafeFileHandle pinnedParentHandle)
    {
        SafeFileHandle handle = CreateFile(
            path,
            GENERIC_READ | DELETE,
            0,
            IntPtr.Zero,
            OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
        if (handle == null || handle.IsInvalid)
        {
            if (handle != null) handle.Dispose();
            return Failure(Win32Error("exclusive exact-delete open"));
        }

        try
        {
            Result parentValidation = ValidateChildInPinnedDirectory(
                handle,
                pinnedParentHandle);
            if (!parentValidation.Succeeded)
                return Failure(
                    "exact-delete parent binding failed: " +
                    parentValidation.Error);
            BY_HANDLE_FILE_INFORMATION before;
            if (!GetFileInformationByHandle(handle, out before))
                return Failure(Win32Error("initial handle metadata read"));
            if ((before.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0)
                return Failure("exact-delete target is a directory");
            if ((before.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
                return Failure("exact-delete target is a reparse point");
            if (before.NumberOfLinks != 1)
                return Failure("exact-delete target has multiple hard links");

            long length = ((long)before.FileSizeHigh << 32) |
                (long)before.FileSizeLow;
            if (length != expectedLength)
                return Failure(
                    "exact-delete length mismatched; expected=" +
                    expectedLength + "; actual=" + length);

            string actualSha256;
            using (FileStream stream = new FileStream(
                handle,
                FileAccess.Read,
                4096,
                false))
            {
                using (SHA256 sha256 = SHA256.Create())
                {
                    actualSha256 = BitConverter.ToString(
                        sha256.ComputeHash(stream)).Replace("-", "")
                        .ToLowerInvariant();
                }
                if (!String.Equals(
                        actualSha256,
                        expectedSha256,
                        StringComparison.Ordinal))
                    return Failure(
                        "exact-delete sha256 mismatched; expected=" +
                        expectedSha256 + "; actual=" + actualSha256);

                BY_HANDLE_FILE_INFORMATION after;
                if (!GetFileInformationByHandle(handle, out after))
                    return Failure(Win32Error("final handle metadata read"));
                if ((after.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0)
                    return Failure("exact-delete target became a reparse point");
                if (after.NumberOfLinks != 1)
                    return Failure("exact-delete target gained a hard link");
                long finalLength = ((long)after.FileSizeHigh << 32) |
                    (long)after.FileSizeLow;
                if (finalLength != expectedLength)
                    return Failure("exact-delete target length changed");

                parentValidation = ValidateChildInPinnedDirectory(
                    handle,
                    pinnedParentHandle);
                if (!parentValidation.Succeeded)
                    return Failure(
                        "exact-delete parent binding changed: " +
                        parentValidation.Error);

                FILE_DISPOSITION_INFO disposition =
                    new FILE_DISPOSITION_INFO { DeleteFile = true };
                if (!SetFileInformationByHandle(
                        handle,
                        FILE_DISPOSITION_INFO_CLASS,
                        ref disposition,
                        (uint)Marshal.SizeOf(typeof(FILE_DISPOSITION_INFO))))
                    return Failure(Win32Error("handle delete disposition"));
            }
            return new Result { Succeeded = true, Error = String.Empty };
        }
        catch (Exception exception)
        {
            return Failure(exception.GetType().Name + ": " + exception.Message);
        }
        finally
        {
            handle.Dispose();
        }
    }
}
'@
}

function Initialize-AgentBridgeHeldFileType {
    if ($null -ne ('AgentBridgeHeldFileV1' -as [type])) {
        return
    }

    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

public static class AgentBridgeHeldFileV1
{
    public sealed class OpenResult
    {
        public bool Succeeded { get; set; }
        public bool Collision { get; set; }
        public string Error { get; set; }
        public SafeFileHandle Handle { get; set; }
    }

    public sealed class RenameResult
    {
        public bool Succeeded { get; set; }
        public string Error { get; set; }
        public string FinalPath { get; set; }
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFile(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool SetFileInformationByHandle(
        SafeFileHandle file,
        int informationClass,
        IntPtr information,
        uint informationSize);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool FlushFileBuffers(SafeFileHandle file);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern uint GetFinalPathNameByHandle(
        SafeFileHandle file,
        StringBuilder filePath,
        uint filePathLength,
        uint flags);

    private const uint GENERIC_READ = 0x80000000;
    private const uint GENERIC_WRITE = 0x40000000;
    private const uint DELETE = 0x00010000;
    private const uint CREATE_NEW = 1;
    private const uint FILE_ATTRIBUTE_NORMAL = 0x00000080;
    private const uint FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000;
    private const int FILE_RENAME_INFO_CLASS = 3;

    private static string Win32Error(string operation, int code)
    {
        return operation + " failed (win32=" + code + "): " +
            new Win32Exception(code).Message;
    }

    private static string FinalPath(SafeFileHandle handle)
    {
        StringBuilder buffer = new StringBuilder(32768);
        uint length = GetFinalPathNameByHandle(
            handle,
            buffer,
            (uint)buffer.Capacity,
            0);
        if (length == 0)
            throw new Win32Exception(
                Marshal.GetLastWin32Error(),
                "held final path read failed");
        if (length >= buffer.Capacity)
            throw new IOException("held final path exceeded safe buffer");
        string path = buffer.ToString();
        if (path.StartsWith(@"\\?\UNC\", StringComparison.OrdinalIgnoreCase))
            path = @"\\" + path.Substring(8);
        else if (path.StartsWith(@"\\?\", StringComparison.OrdinalIgnoreCase))
            path = path.Substring(4);
        return Path.GetFullPath(path).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
    }

    public static OpenResult CreateNewRenameCapable(string path)
    {
        SafeFileHandle handle = CreateFile(
            path,
            GENERIC_READ | GENERIC_WRITE | DELETE,
            0,
            IntPtr.Zero,
            CREATE_NEW,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
            IntPtr.Zero);
        if (handle == null || handle.IsInvalid)
        {
            int code = Marshal.GetLastWin32Error();
            if (handle != null) handle.Dispose();
            return new OpenResult {
                Succeeded = false,
                Collision = code == 80 || code == 183,
                Error = Win32Error("held create-new", code),
                Handle = null
            };
        }
        return new OpenResult {
            Succeeded = true,
            Collision = false,
            Error = String.Empty,
            Handle = handle
        };
    }

    public static RenameResult RenameInPinnedDirectory(
        SafeFileHandle fileHandle,
        SafeFileHandle pinnedParentHandle,
        string destinationPath)
    {
        if (fileHandle == null || fileHandle.IsInvalid || fileHandle.IsClosed)
            return new RenameResult {
                Succeeded = false,
                Error = "held file handle is unavailable"
            };
        if (pinnedParentHandle == null ||
            pinnedParentHandle.IsInvalid ||
            pinnedParentHandle.IsClosed)
            return new RenameResult {
                Succeeded = false,
                Error = "pinned parent handle is unavailable"
            };
        if (String.IsNullOrWhiteSpace(destinationPath) ||
            !Path.IsPathRooted(destinationPath))
            return new RenameResult {
                Succeeded = false,
                Error = "rollback retention destination must be absolute",
                FinalPath = String.Empty
            };

        string expectedPath = Path.GetFullPath(destinationPath).TrimEnd(
            Path.DirectorySeparatorChar,
            Path.AltDirectorySeparatorChar);
        byte[] nameBytes = Encoding.Unicode.GetBytes(expectedPath);
        int rootOffset = IntPtr.Size;
        int lengthOffset = rootOffset + IntPtr.Size;
        int nameOffset = lengthOffset + sizeof(uint);
        int bufferSize = checked(nameOffset + nameBytes.Length + sizeof(char));
        IntPtr buffer = Marshal.AllocHGlobal(bufferSize);
        try
        {
            for (int index = 0; index < bufferSize; index++)
                Marshal.WriteByte(buffer, index, 0);
            Marshal.WriteByte(buffer, 0, 0);
            Marshal.WriteIntPtr(
                buffer,
                rootOffset,
                IntPtr.Zero);
            Marshal.WriteInt32(buffer, lengthOffset, nameBytes.Length);
            Marshal.Copy(nameBytes, 0, IntPtr.Add(buffer, nameOffset), nameBytes.Length);
            if (!SetFileInformationByHandle(
                    fileHandle,
                    FILE_RENAME_INFO_CLASS,
                    buffer,
                    (uint)bufferSize))
            {
                int code = Marshal.GetLastWin32Error();
                return new RenameResult {
                    Succeeded = false,
                    Error = Win32Error("held rollback rename", code),
                    FinalPath = String.Empty
                };
            }
            if (!FlushFileBuffers(fileHandle))
            {
                int code = Marshal.GetLastWin32Error();
                return new RenameResult {
                    Succeeded = false,
                    Error = Win32Error("held rollback rename flush", code),
                    FinalPath = String.Empty
                };
            }
            string actualPath = FinalPath(fileHandle);
            if (!String.Equals(
                    actualPath,
                    expectedPath,
                    StringComparison.OrdinalIgnoreCase))
                return new RenameResult {
                    Succeeded = false,
                    Error = "held rollback rename final path mismatched; " +
                        "expected=" + expectedPath + "; actual=" + actualPath,
                    FinalPath = actualPath
                };
            return new RenameResult {
                Succeeded = true,
                Error = String.Empty,
                FinalPath = actualPath
            };
        }
        catch (Exception exception)
        {
            return new RenameResult {
                Succeeded = false,
                Error = exception.GetType().Name + ": " + exception.Message,
                FinalPath = String.Empty
            };
        }
        finally
        {
            Marshal.FreeHGlobal(buffer);
        }
    }
}
'@
}


function Enter-AgentBridgeParentDirectoryPin {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ChildPath,
        [string] $Context = 'bridge child publication'
    )

    Initialize-AgentBridgeExactDeleteType
    $fullChildPath = [System.IO.Path]::GetFullPath($ChildPath)
    $parentPath = [System.IO.Path]::GetDirectoryName($fullChildPath)
    if ([string]::IsNullOrWhiteSpace($parentPath)) {
        throw "$Context parent path is unavailable: $ChildPath"
    }
    $pinResult = [AgentBridgeExactFileDeleteV3]::PinPlainDirectory(
        $parentPath
    )
    if (-not [bool]$pinResult.Succeeded) {
        throw "$Context parent pin failed: $parentPath`: $($pinResult.Error)"
    }
    $pin = [pscustomobject]@{
        child_path = $fullChildPath
        parent_path = $parentPath
        handle = $pinResult.Handle
        context = $Context
    }
    try {
        Assert-AgentBridgeParentDirectoryPin -Pin $pin
    } catch {
        $pin.handle.Dispose()
        throw
    }
    return $pin
}

function Assert-AgentBridgeParentDirectoryPin {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Pin
    )

    $validation = [AgentBridgeExactFileDeleteV3]::ValidatePinnedDirectory(
        [string]$Pin.parent_path,
        $Pin.handle
    )
    if (-not [bool]$validation.Succeeded) {
        throw (
            "{0} parent generation validation failed: {1}: {2}" -f
            [string]$Pin.context,
            [string]$Pin.parent_path,
            [string]$validation.Error
        )
    }
}

function Assert-AgentBridgeChildHandleParentPin {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Pin,
        [Parameter(Mandatory)] [Microsoft.Win32.SafeHandles.SafeFileHandle] $ChildHandle
    )

    $validation = (
        [AgentBridgeExactFileDeleteV3]::ValidateChildInPinnedDirectory(
            $ChildHandle,
            $Pin.handle
        )
    )
    if (-not [bool]$validation.Succeeded) {
        throw (
            "{0} child parent validation failed: {1}" -f
            [string]$Pin.context,
            [string]$validation.Error
        )
    }
}

function Exit-AgentBridgeParentDirectoryPin {
    [CmdletBinding()]
    param(
        [AllowNull()] $Pin
    )

    if ($null -eq $Pin) { return }
    if ($null -ne $Pin.handle) {
        $Pin.handle.Dispose()
    }
}

function Remove-AgentBridgeExactFile {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength,
        [string] $Context = 'transaction artifact cleanup'
    )

    if (
        $ExpectedSha256 -cnotmatch '^[0-9a-f]{64}\z' -or
        $ExpectedLength -lt 0
    ) {
        throw "$Context has an invalid expected identity: $LiteralPath"
    }

    # A default-stream handle, byte hash, file ID, and change time still do
    # not make deletion safe on NTFS. Another process can add an alternate
    # data stream while the default stream is open without sharing; that ADS
    # can arrive after the final metadata check and would then be destroyed by
    # a handle delete. Bridge recovery artifacts are deliberately retained
    # instead. This wrapper remains fail-closed so no caller can accidentally
    # reintroduce pathname cleanup under a misleading "exact" identity check.
    throw (
        "$Context retained $LiteralPath`: destructive cleanup is disabled " +
        "because NTFS alternate-stream ownership cannot be bound atomically"
    )
}

function Assert-AgentBridgeExclusiveHandleIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [System.IO.FileStream] $Stream,
        [string] $Context = 'exclusive file handle'
    )

    Initialize-AgentBridgeExactDeleteType
    $identity = [AgentBridgeExactFileDeleteV3]::GetLinkIdentity(
        $Stream.SafeFileHandle
    )
    if (-not [bool]$identity.Succeeded) {
        throw "$Context link identity is unavailable: $($identity.Error)"
    }
    if ([bool]$identity.IsDirectory) {
        throw "$Context is a directory"
    }
    if ([bool]$identity.IsReparsePoint) {
        throw "$Context is a reparse point"
    }
    if ([uint32]$identity.NumberOfLinks -ne 1) {
        throw (
            "$Context must have exactly one filesystem link; actual={0}" -f
            [uint32]$identity.NumberOfLinks
        )
    }
}

function Assert-AgentBridgeExpectedRegularFileSnapshot {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LiteralPath,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength,
        [string] $Context = 'claim transaction artifact'
    )

    if (
        $ExpectedSha256 -cnotmatch '^[0-9a-f]{64}\z' -or
        $ExpectedLength -lt 0
    ) {
        throw "$Context has an invalid expected snapshot identity: $LiteralPath"
    }

    try {
        $snapshot = Get-AgentBridgeExclusiveRawFileSnapshot `
            -LiteralPath $LiteralPath `
            -Context $Context
    } catch {
        throw
    }
    if (
        [long]$snapshot.length -ne $ExpectedLength -or
        [string]$snapshot.sha256 -cne $ExpectedSha256
    ) {
        throw ((
            "{0} changed (path={1}; expected length={2} sha256={3}; " +
            "actual length={4} sha256={5})"
        ) -f
            $Context,
            $LiteralPath,
            $ExpectedLength,
            $ExpectedSha256,
            [long]$snapshot.length,
            [string]$snapshot.sha256
        )
    }
    return $snapshot
}

function Test-AgentBridgeDestinationCollisionException {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [System.Exception] $Exception
    )

    $current = $Exception
    while ($null -ne $current) {
        if (
            $current -is [System.IO.IOException] -and
            (($current.HResult -band 0xffff) -in @(80, 183))
        ) {
            return $true
        }
        $current = $current.InnerException
    }
    return $false
}

function Assert-AgentBridgeTrustedBytesIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [byte[]] $Bytes,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength,
        [string] $Context = 'trusted claim bytes'
    )

    if (
        $ExpectedSha256 -cnotmatch '^[0-9a-f]{64}\z' -or
        $ExpectedLength -lt 0
    ) {
        throw "$Context has an invalid expected snapshot identity"
    }
    $actualSha256 = Get-AgentBridgeSha256Hex -Bytes $Bytes
    if (
        [long]$Bytes.Length -ne $ExpectedLength -or
        $actualSha256 -cne $ExpectedSha256
    ) {
        throw ((
            "{0} changed (expected length={1} sha256={2}; " +
            "actual length={3} sha256={4})"
        ) -f
            $Context,
            $ExpectedLength,
            $ExpectedSha256,
            [long]$Bytes.Length,
            $actualSha256
        )
    }
}

function Invoke-AgentBridgeTrustedBytesCreateNew {
    <#
    .SYNOPSIS
        Write caller-authorized bytes directly to a create-new canonical path.

    .DESCRIPTION
        The byte array is the authority. No mutable temp or stage pathname is
        verified and then moved. Once FileMode.CreateNew creates the canonical
        name, every failure leaves that name untouched for fail-closed audit.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $DestinationPath,
        [Parameter(Mandatory)] [byte[]] $PublishBytes,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength,
        [string] $Context = 'claim canonical publication',
        [switch] $KeepOpenForRollback
    )

    try {
        Assert-AgentBridgeTrustedBytesIdentity `
            -Bytes $PublishBytes `
            -ExpectedSha256 $ExpectedSha256 `
            -ExpectedLength $ExpectedLength `
            -Context "$Context input"
    } catch {
        return [pscustomobject]@{
            succeeded = $false
            created = $false
            collision = $false
            error = $_.Exception
            held_lease = $null
        }
    }

    $parentPin = $null
    $stream = $null
    try {
        try {
            $parentPin = Enter-AgentBridgeParentDirectoryPin `
                -ChildPath $DestinationPath `
                -Context $Context
            Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
        } catch {
            return [pscustomobject]@{
                succeeded = $false
                created = $false
                collision = $false
                error = $_.Exception
                held_lease = $null
            }
        }

        try {
        # CAS V2 DIRECT MARKER: create canonical path.
            if ($KeepOpenForRollback) {
                Initialize-AgentBridgeHeldFileType
                $nativeOpen = (
                    [AgentBridgeHeldFileV1]::CreateNewRenameCapable(
                        $DestinationPath
                    )
                )
                if (-not [bool]$nativeOpen.Succeeded) {
                    return [pscustomobject]@{
                        succeeded = $false
                        created = $false
                        collision = [bool]$nativeOpen.Collision
                        error = [System.IO.IOException]::new(
                            [string]$nativeOpen.Error
                        )
                        held_lease = $null
                    }
                }
                try {
                    $stream = [System.IO.FileStream]::new(
                        $nativeOpen.Handle,
                        [System.IO.FileAccess]::ReadWrite
                    )
                    $nativeOpen.Handle = $null
                } catch {
                    $streamCreateError = $_.Exception
                    $heldLease = [pscustomobject]@{
                        stream = $null
                        handle = $nativeOpen.Handle
                        parent_pin = $parentPin
                        canonical_path = [System.IO.Path]::GetFullPath(
                            $DestinationPath
                        )
                        retained_path = $null
                    }
                    $nativeOpen.Handle = $null
                    $parentPin = $null
                    return [pscustomobject]@{
                        succeeded = $false
                        created = $true
                        collision = $false
                        error = $streamCreateError
                        held_lease = $heldLease
                    }
                }
            } else {
                $stream = [System.IO.File]::Open(
                    $DestinationPath,
                    [System.IO.FileMode]::CreateNew,
                    [System.IO.FileAccess]::ReadWrite,
                    [System.IO.FileShare]::None
                )
            }
        } catch {
            $openError = $_.Exception
            return [pscustomobject]@{
                succeeded = $false
                created = $false
                collision = [bool](
                    Test-AgentBridgeDestinationCollisionException `
                        -Exception $openError
                )
                error = $openError
                held_lease = $null
            }
        }

        $writeError = $null
        try {
            # Bind both the parent generation and opened child handle before
            # the first trusted byte is written.
            Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
            Assert-AgentBridgeChildHandleParentPin `
                -Pin $parentPin `
                -ChildHandle $stream.SafeFileHandle
            Assert-AgentBridgeRegularUnlinkedFile `
                -LiteralPath $DestinationPath `
                -Context $Context
            Assert-AgentBridgeExclusiveHandleIdentity `
                -Stream $stream `
                -Context $Context
            Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
            # CAS V2 DIRECT MARKER: write trusted canonical bytes.
            $stream.Write($PublishBytes, 0, $PublishBytes.Length)
            # CAS V2 DIRECT MARKER: durably flush canonical bytes.
            $stream.Flush($true)
        } catch {
            $writeError = $_.Exception
        }

        $verificationError = $null
        if ($null -eq $writeError) {
            try {
            # Verify the persisted bytes through the still-exclusive handle.
            # The canonical pathname cannot be replaced while this handle is
            # open without delete sharing.
            if ([long]$stream.Length -ne $ExpectedLength) {
                throw (
                    "$Context handle length changed: expected " +
                    "$ExpectedLength; actual $($stream.Length)"
                )
            }
            [void]$stream.Seek(0, [System.IO.SeekOrigin]::Begin)
            $verifiedBytes = [byte[]]::new($PublishBytes.Length)
            $offset = 0
            while ($offset -lt $verifiedBytes.Length) {
                $read = $stream.Read(
                    $verifiedBytes,
                    $offset,
                    $verifiedBytes.Length - $offset
                )
                if ($read -le 0) {
                    throw "$Context handle ended before expected bytes"
                }
                $offset += $read
            }
            Assert-AgentBridgeTrustedBytesIdentity `
                -Bytes $verifiedBytes `
                -ExpectedSha256 $ExpectedSha256 `
                -ExpectedLength $ExpectedLength `
                -Context "$Context handle verification"

            # CAS V2 DIRECT MARKER: final canonical identity gate.
            Assert-AgentBridgeExclusiveHandleIdentity `
                -Stream $stream `
                -Context $Context
            Assert-AgentBridgeRegularUnlinkedFile `
                -LiteralPath $DestinationPath `
                -Context $Context
            Assert-AgentBridgeChildHandleParentPin `
                -Pin $parentPin `
                -ChildHandle $stream.SafeFileHandle
            Assert-AgentBridgeParentDirectoryPin -Pin $parentPin
            } catch {
                $verificationError = $_.Exception
            }
        }

        if (
            $KeepOpenForRollback -and
            $null -eq $writeError -and
            $null -eq $verificationError
        ) {
            # Transfer both handles to the caller. Keeping the rename-capable
            # child and its exact parent generation continuously open prevents
            # replacement until commit close or handle-bound rollback rename.
            $heldLease = [pscustomobject]@{
                stream = $stream
                handle = $null
                parent_pin = $parentPin
                canonical_path = [System.IO.Path]::GetFullPath(
                    $DestinationPath
                )
                retained_path = $null
            }
            $stream = $null
            $parentPin = $null
            return [pscustomobject]@{
                succeeded = $true
                created = $true
                collision = $false
                error = $null
                held_lease = $heldLease
            }
        }

        if (
            $KeepOpenForRollback -and
            ($null -ne $writeError -or $null -ne $verificationError)
        ) {
            $innerError = if ($null -ne $writeError) {
                $writeError
            } else {
                $verificationError
            }
            $failureMessage = if ($null -ne $writeError) {
                "$Context write or flush failed: $($writeError.Message)"
            } else {
                "$Context exclusive verification failed: " +
                    $verificationError.Message
            }
            $heldLease = [pscustomobject]@{
                stream = $stream
                handle = $null
                parent_pin = $parentPin
                canonical_path = [System.IO.Path]::GetFullPath(
                    $DestinationPath
                )
                retained_path = $null
            }
            $stream = $null
            $parentPin = $null
            return [pscustomobject]@{
                succeeded = $false
                created = $true
                collision = $false
                error = [System.IO.IOException]::new(
                    $failureMessage,
                    $innerError
                )
                held_lease = $heldLease
            }
        }

        $closeError = $null
        try {
        # CAS V2 DIRECT MARKER: close canonical handle.
        $stream.Dispose()
        } catch {
            $closeError = $_.Exception
            try { $stream.Dispose() } catch {}
        }
        $stream = $null

        if (
            $null -ne $writeError -or
            $null -ne $verificationError -or
            $null -ne $closeError
        ) {
        $failureMessage = if ($null -ne $writeError) {
            "$Context write or flush failed: $($writeError.Message)"
        } elseif ($null -ne $verificationError) {
            "$Context exclusive verification failed: " +
                $verificationError.Message
        } else {
            "$Context close failed: $($closeError.Message)"
        }
        if (
            ($null -ne $writeError -or $null -ne $verificationError) -and
            $null -ne $closeError
        ) {
            $failureMessage += "; close also failed: $($closeError.Message)"
        }
        $innerError = if ($null -ne $writeError) {
            $writeError
        } elseif ($null -ne $verificationError) {
            $verificationError
        } else {
            $closeError
        }
            return [pscustomobject]@{
                succeeded = $false
                created = $true
                collision = $false
                error = [System.IO.IOException]::new(
                    $failureMessage,
                    $innerError
                )
                held_lease = $null
            }
        }

        return [pscustomobject]@{
            succeeded = $true
            created = $true
            collision = $false
            error = $null
            held_lease = $null
        }
    } finally {
        if ($null -ne $stream) {
            try { $stream.Dispose() } catch {}
        }
        Exit-AgentBridgeParentDirectoryPin -Pin $parentPin
    }
}

function Move-AgentBridgeHeldFileToRollbackRetention {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Lease,
        [Parameter(Mandatory)] [string] $RetentionPath,
        [string] $Context = 'held bridge rollback archive'
    )

    $childHandle = if ($null -ne $Lease.stream) {
        $Lease.stream.SafeFileHandle
    } else {
        $Lease.handle
    }
    if ($null -eq $childHandle -or $null -eq $Lease.parent_pin) {
        throw "$Context lease is unavailable"
    }
    $fullRetentionPath = [System.IO.Path]::GetFullPath($RetentionPath)
    $retentionParent = [System.IO.Path]::GetDirectoryName(
        $fullRetentionPath
    )
    if (-not [string]::Equals(
            $retentionParent,
            [string]$Lease.parent_pin.parent_path,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw "$Context destination is outside the pinned parent"
    }
    $leafName = [System.IO.Path]::GetFileName($fullRetentionPath)
    if ([string]::IsNullOrWhiteSpace($leafName)) {
        throw "$Context destination leaf is unavailable"
    }

    Assert-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
    Assert-AgentBridgeChildHandleParentPin `
        -Pin $Lease.parent_pin `
        -ChildHandle $childHandle
    Initialize-AgentBridgeHeldFileType
    # HELD ROLLBACK V1 MARKER: rename the continuously owned inode itself.
    $renameResult = [AgentBridgeHeldFileV1]::RenameInPinnedDirectory(
        $childHandle,
        $Lease.parent_pin.handle,
        $fullRetentionPath
    )
    if (-not [bool]$renameResult.Succeeded) {
        throw "$Context rename failed: $($renameResult.Error)"
    }
    if (-not [string]::Equals(
            [string]$renameResult.FinalPath,
            $fullRetentionPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
        throw (
            "$Context rename final path mismatched: expected " +
            "$fullRetentionPath; actual $($renameResult.FinalPath)"
        )
    }
    Assert-AgentBridgeChildHandleParentPin `
        -Pin $Lease.parent_pin `
        -ChildHandle $childHandle
    Assert-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
    $Lease.retained_path = $fullRetentionPath
}

function Close-AgentBridgeHeldFileLease {
    [CmdletBinding()]
    param(
        [AllowNull()] $Lease,
        [string] $Context = 'held bridge file'
    )

    if ($null -eq $Lease) { return }
    $closeErrors = New-Object System.Collections.Generic.List[string]
    if ($null -ne $Lease.stream) {
        try {
            $Lease.stream.Dispose()
        } catch {
            [void]$closeErrors.Add(
                "child handle close failed: $($_.Exception.Message)"
            )
        } finally {
            $Lease.stream = $null
        }
    }
    if ($null -ne $Lease.handle) {
        try {
            $Lease.handle.Dispose()
        } catch {
            [void]$closeErrors.Add(
                "native child handle close failed: $($_.Exception.Message)"
            )
        } finally {
            $Lease.handle = $null
        }
    }
    if ($null -ne $Lease.parent_pin) {
        try {
            Exit-AgentBridgeParentDirectoryPin -Pin $Lease.parent_pin
        } catch {
            [void]$closeErrors.Add(
                "parent pin close failed: $($_.Exception.Message)"
            )
        } finally {
            $Lease.parent_pin = $null
        }
    }
    if ($closeErrors.Count -gt 0) {
        throw "$Context release failed: $($closeErrors -join '; ')"
    }
}

function Publish-AgentBridgeNewFileFromBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [byte[]] $PublishBytes,
        [Parameter(Mandatory)] [string] $DestinationPath,
        [Parameter(Mandatory)] [string] $ExpectedSha256,
        [Parameter(Mandatory)] [long] $ExpectedLength
    )

    $publishResult = Invoke-AgentBridgeTrustedBytesCreateNew `
        -DestinationPath $DestinationPath `
        -PublishBytes $PublishBytes `
        -ExpectedSha256 $ExpectedSha256 `
        -ExpectedLength $ExpectedLength `
        -Context 'published new claim'
    if ([bool]$publishResult.succeeded) {
        return
    }
    if ([bool]$publishResult.collision) {
        throw "claim_destination_collision: $DestinationPath"
    }
    throw ((
        "claim direct publication failed (canonical_created={0}): {1}"
    ) -f
        [bool]$publishResult.created,
        $publishResult.error.Message
    )
}

function Publish-AgentBridgeFileFromSnapshot {
    <#
    .SYNOPSIS
        Publish trusted bytes after quarantining the authorized source bytes.

    .DESCRIPTION
        ExpectedSourceBytes is eligibility evidence before the source Move.
        After Move, only the completed same-handle quarantine capture may
        authorize a restore. The quarantined name is never reread, moved, or
        deleted; publication and restore use captured byte arrays with
        FileMode.CreateNew.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [byte[]] $PublishBytes,
        [Parameter(Mandatory)] [string] $SourcePath,
        [Parameter(Mandatory)] [string] $PublishPath,
        [Parameter(Mandatory)] [byte[]] $ExpectedSourceBytes,
        [Parameter(Mandatory)] [string] $ExpectedSourceSha256,
        [Parameter(Mandatory)] [long] $ExpectedSourceLength,
        [Parameter(Mandatory)] [string] $ExpectedPublishSha256,
        [Parameter(Mandatory)] [long] $ExpectedPublishLength
    )

    Assert-AgentBridgeTrustedBytesIdentity `
        -Bytes $PublishBytes `
        -ExpectedSha256 $ExpectedPublishSha256 `
        -ExpectedLength $ExpectedPublishLength `
        -Context 'authorized claim publication bytes'
    Assert-AgentBridgeTrustedBytesIdentity `
        -Bytes $ExpectedSourceBytes `
        -ExpectedSha256 $ExpectedSourceSha256 `
        -ExpectedLength $ExpectedSourceLength `
        -Context 'authorized claim source bytes'

    $quarantinePath = New-AgentBridgeCasArtifactPath `
        -BasePath $SourcePath `
        -Label 'cas-quarantine'
    $sourceParentPin = Enter-AgentBridgeParentDirectoryPin `
        -ChildPath $SourcePath `
        -Context 'claim source quarantine'
    try {
    Assert-AgentBridgeParentDirectoryPin -Pin $sourceParentPin
    [System.IO.File]::Move($SourcePath, $quarantinePath)
    Assert-AgentBridgeParentDirectoryPin -Pin $sourceParentPin
    try {
        $quarantineSnapshot = Get-AgentBridgeExclusiveRawFileCapture `
            -LiteralPath $quarantinePath `
            -Context 'authorized quarantined claim source'
    } catch {
        # The Move revoked the older authorization snapshot's restore
        # authority. If no complete post-Move capture exists, retain quarantine
        # and fail without publishing any bytes to the active source path.
        throw (
            "claim source could not be captured after quarantine; artifact " +
            "retained at $quarantinePath; active source was not restored " +
            "from pre-move authorization: $($_.Exception.Message)"
        )
    }
    if (-not [bool]$quarantineSnapshot.identity_verified) {
        # A hard-link/reparse/path identity rejection happened only after a
        # complete same-handle byte capture. Restore exactly that captured
        # generation create-new, while retaining the rejected Q and aliases.
        $initialCaptureRestore = Invoke-AgentBridgeTrustedBytesCreateNew `
            -DestinationPath $SourcePath `
            -PublishBytes ([byte[]]$quarantineSnapshot.bytes) `
            -ExpectedSha256 ([string]$quarantineSnapshot.sha256) `
            -ExpectedLength ([long]$quarantineSnapshot.length) `
            -Context 'restored source after quarantine identity failure'
        $captureFailureMessage = (
            "claim source identity was rejected after complete quarantine " +
            "capture; artifact retained at $quarantinePath`: " +
            $quarantineSnapshot.identity_error.Message
        )
        if (-not [bool]$initialCaptureRestore.succeeded) {
            $captureFailureMessage += (
                "; create-new captured-generation restore failed: " +
                $initialCaptureRestore.error.Message
            )
        }
        throw $captureFailureMessage
    }
    if (
        [long]$quarantineSnapshot.length -ne $ExpectedSourceLength -or
        [string]$quarantineSnapshot.sha256 -cne $ExpectedSourceSha256
    ) {
        # The source generation changed before the atomic quarantine Move.
        # Restore the actual captured generation create-new, never the older
        # authorized bytes, while retaining quarantine for audit.
        $actualRestore = Invoke-AgentBridgeTrustedBytesCreateNew `
            -DestinationPath $SourcePath `
            -PublishBytes ([byte[]]$quarantineSnapshot.bytes) `
            -ExpectedSha256 ([string]$quarantineSnapshot.sha256) `
            -ExpectedLength ([long]$quarantineSnapshot.length) `
            -Context 'restored changed claim generation'
        $mismatchMessage = (
            "claim source changed after authorization; quarantine retained " +
            "at $quarantinePath"
        )
        if (-not [bool]$actualRestore.succeeded) {
            $mismatchMessage += (
                "; create-new restore of changed generation failed: " +
                $actualRestore.error.Message
            )
        }
        throw $mismatchMessage
    }
    try {
        # CAS V2 TRANSACTION MARKER: verify captured quarantine path.
        $null = Assert-AgentBridgeExpectedRegularFileSnapshot `
            -LiteralPath $quarantinePath `
            -ExpectedSha256 $ExpectedSourceSha256 `
            -ExpectedLength $ExpectedSourceLength `
            -Context 'captured quarantined claim source'
    } catch {
        # The captured array remains the exact generation read through the
        # exclusive quarantine handle even if its name changed afterward.
        $capturedRestore = Invoke-AgentBridgeTrustedBytesCreateNew `
            -DestinationPath $SourcePath `
            -PublishBytes ([byte[]]$quarantineSnapshot.bytes) `
            -ExpectedSha256 ([string]$quarantineSnapshot.sha256) `
            -ExpectedLength ([long]$quarantineSnapshot.length) `
            -Context 'restored captured claim generation'
        $quarantineChangeMessage = (
            "claim quarantine changed after capture; current artifact " +
            "retained at $quarantinePath`: $($_.Exception.Message)"
        )
        if (-not [bool]$capturedRestore.succeeded) {
            $quarantineChangeMessage += (
                "; create-new captured source restore failed: " +
                $capturedRestore.error.Message
            )
        }
        throw $quarantineChangeMessage
    }

    $publishResult = Invoke-AgentBridgeTrustedBytesCreateNew `
        -DestinationPath $PublishPath `
        -PublishBytes $PublishBytes `
        -ExpectedSha256 $ExpectedPublishSha256 `
        -ExpectedLength $ExpectedPublishLength `
        -Context 'published claim transaction'
    if ([bool]$publishResult.succeeded) {
        # The exact, verified quarantine is the committed recovery artifact.
        return
    }

    $restoreError = $null
    $shouldRestoreSource = (
        $PublishPath -cne $SourcePath -or
        -not [bool]$publishResult.created
    )
    if ($shouldRestoreSource) {
        # CAS V2 DIRECT MARKER: restore captured source bytes create-new.
        $restoreResult = Invoke-AgentBridgeTrustedBytesCreateNew `
            -DestinationPath $SourcePath `
            -PublishBytes ([byte[]]$quarantineSnapshot.bytes) `
            -ExpectedSha256 ([string]$quarantineSnapshot.sha256) `
            -ExpectedLength ([long]$quarantineSnapshot.length) `
            -Context 'restored active claim source'
        if (-not [bool]$restoreResult.succeeded) {
            $restoreError = $restoreResult.error
        }
    }

    $publishFailure = ((
        "claim direct publication failed (canonical_created={0}; " +
        "recovery_artifact={1}): {2}"
    ) -f
        [bool]$publishResult.created,
        $quarantinePath,
        $publishResult.error.Message
    )
    if ($null -ne $restoreError) {
        throw (
            "$publishFailure; create-new source restore failed: " +
            $restoreError.Message
        )
    }
    throw $publishFailure
    } finally {
        Exit-AgentBridgeParentDirectoryPin -Pin $sourceParentPin
    }
}

function Update-AgentBridgeFileFromBytes {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [byte[]] $PublishBytes,
        [Parameter(Mandatory)] [string] $DestinationPath,
        [Parameter(Mandatory)] [byte[]] $ExpectedSourceBytes,
        [Parameter(Mandatory)] [string] $ExpectedSourceSha256,
        [Parameter(Mandatory)] [long] $ExpectedSourceLength,
        [Parameter(Mandatory)] [string] $ExpectedPublishSha256,
        [Parameter(Mandatory)] [long] $ExpectedPublishLength
    )

    Publish-AgentBridgeFileFromSnapshot `
        -PublishBytes $PublishBytes `
        -SourcePath $DestinationPath `
        -PublishPath $DestinationPath `
        -ExpectedSourceBytes $ExpectedSourceBytes `
        -ExpectedSourceSha256 $ExpectedSourceSha256 `
        -ExpectedSourceLength $ExpectedSourceLength `
        -ExpectedPublishSha256 $ExpectedPublishSha256 `
        -ExpectedPublishLength $ExpectedPublishLength
}

function Get-AgentBridgeClaimOwnerContext {
    [CmdletBinding()]
    param()

    $sessionId = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_SESSION_ID',
        'Process'
    )
    $ownerToken = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_TOKEN',
        'Process'
    )
    $ownerPidText = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PID',
        'Process'
    )
    $ownerProcessStartUtc = [string][Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC',
        'Process'
    )

    if ($sessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_SESSION_ID is missing or malformed'
    }
    if ($ownerToken -cnotmatch '^[0-9a-f]{64}\z') {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_TOKEN is missing or malformed'
    }
    $ownerPid = 0
    if (
        $ownerPidText -cnotmatch '^[0-9]+\z' -or
        -not [int]::TryParse(
            $ownerPidText,
            [System.Globalization.NumberStyles]::None,
            [System.Globalization.CultureInfo]::InvariantCulture,
            [ref]$ownerPid
        ) -or
        $ownerPid -le 0
    ) {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_PID is missing or malformed'
    }
    $ownerStarted = ConvertFrom-AgentBridgeCanonicalUtc `
        -Value $ownerProcessStartUtc
    if ($null -eq $ownerStarted) {
        throw 'claim_owner_mismatch: AGENT_BRIDGE_OWNER_PROCESS_START_UTC is missing or malformed'
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $tokenBytes = [System.Text.Encoding]::UTF8.GetBytes($ownerToken)
        $tokenHash = [System.BitConverter]::ToString(
            $sha256.ComputeHash($tokenBytes)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }

    return [pscustomobject]@{
        session_id = $sessionId
        token_sha256 = $tokenHash
        owner_pid = $ownerPid
        owner_process_start_utc = $ownerStarted.ToUniversalTime().ToString(
            'o',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    }
}

function Initialize-AgentBridgeClaimOwnerContext {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $SessionId
    )

    if ($SessionId -notmatch '^[A-Za-z0-9._:-]{1,128}\z') {
        throw 'claim_owner_mismatch: owner session id is malformed'
    }

    $ownerTokenBytes = New-Object byte[] 32
    $ownerRandom = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $ownerRandom.GetBytes($ownerTokenBytes)
    } finally {
        $ownerRandom.Dispose()
    }

    $env:AGENT_BRIDGE_OWNER_SESSION_ID = $SessionId
    $env:AGENT_BRIDGE_OWNER_TOKEN = [System.BitConverter]::ToString(
        $ownerTokenBytes
    ).Replace('-', '').ToLowerInvariant()
    $env:AGENT_BRIDGE_OWNER_PID = [string]$PID
    $env:AGENT_BRIDGE_OWNER_PROCESS_START_UTC = (
        Get-Process -Id $PID -ErrorAction Stop
    ).StartTime.ToUniversalTime().ToString(
        'o',
        [System.Globalization.CultureInfo]::InvariantCulture
    )

    return Get-AgentBridgeClaimOwnerContext
}

function Test-AgentBridgeStoredClaimOwnerComplete {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim
    )

    $ownerSessionProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_session_id'
    $ownerTokenProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_token_sha256'
    $ownerPidProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_pid'
    $ownerStartedProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_process_start_utc'
    if (
        $null -eq $ownerSessionProperty -or
        $null -eq $ownerTokenProperty -or
        $null -eq $ownerPidProperty -or
        $null -eq $ownerStartedProperty
    ) {
        return $false
    }

    $storedOwnerSessionId = $ownerSessionProperty.Value
    if (
        $storedOwnerSessionId -isnot [string] -or
        [string]$storedOwnerSessionId -notmatch
            '^[A-Za-z0-9._:-]{1,128}\z'
    ) {
        return $false
    }
    $storedOwnerTokenSha256 = $ownerTokenProperty.Value
    if (
        $storedOwnerTokenSha256 -isnot [string] -or
        [string]$storedOwnerTokenSha256 -cnotmatch '^[0-9a-f]{64}\z'
    ) {
        return $false
    }

    $storedOwnerPidValue = $ownerPidProperty.Value
    if (
        $storedOwnerPidValue -is [bool] -or
        (
            $storedOwnerPidValue -isnot [int] -and
            $storedOwnerPidValue -isnot [long]
        ) -or
        [long]$storedOwnerPidValue -le 0 -or
        [long]$storedOwnerPidValue -gt [int]::MaxValue
    ) {
        return $false
    }

    $storedOwnerStarted = ConvertFrom-AgentBridgeCanonicalUtc `
        -Value $ownerStartedProperty.Value
    if ($null -eq $storedOwnerStarted) {
        return $false
    }

    return $true
}

function Assert-AgentBridgeTaskId {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $TaskId
    )

    if ($TaskId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{1,120}\z') {
        throw "task_id invalid: '$TaskId'"
    }
    foreach ($segment in [regex]::Split($TaskId, '/')) {
        if (
            $segment -ceq '' -or
            $segment -ceq '.' -or
            $segment -ceq '..'
        ) {
            throw "task_id invalid: '$TaskId'"
        }
    }
}

function Get-AgentBridgeClaimBaseName {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $TaskId
    )

    Assert-AgentBridgeTaskId -TaskId $TaskId
    $safeValue = (($TaskId -replace '[^A-Za-z0-9._-]', '_').Trim('_'))
    if (-not $safeValue) {
        throw "task_id does not produce a safe claim filename: '$TaskId'"
    }
    if ($safeValue -ceq $TaskId) {
        return $safeValue
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $valueBytes = [System.Text.Encoding]::UTF8.GetBytes($TaskId)
        $digest = [System.BitConverter]::ToString(
            $sha256.ComputeHash($valueBytes)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
    return '{0}-{1}' -f $safeValue, $digest.Substring(0, 12)
}

function Assert-AgentBridgePreferredClaimPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ClaimsDir,
        [Parameter(Mandatory)] [string] $TaskId
    )

    $baseName = Get-AgentBridgeClaimBaseName -TaskId $TaskId
    $preferredPath = Join-Path $ClaimsDir ($baseName + '.json')
    if (-not (Test-Path -LiteralPath $preferredPath)) {
        return $preferredPath
    }
    if (-not (Test-Path -LiteralPath $preferredPath -PathType Leaf)) {
        throw ((
            "claim filename collision at preferred path for task_id " +
            "'{0}': non-file entry {1}"
        ) -f $TaskId, $preferredPath
        )
    }

    try {
        $preferredClaim = ConvertFrom-AgentBridgeJson -Json (
            Read-AgentBridgeStrictUtf8JsonText `
                -LiteralPath $preferredPath
        )
    } catch {
        throw ((
            "claim filename collision at preferred path for task_id " +
            "'{0}': unreadable record {1}"
        ) -f $TaskId, $preferredPath
        )
    }
    $preferredTaskProperty = if (
        $null -ne $preferredClaim -and
        $preferredClaim -is
            [System.Management.Automation.PSCustomObject]
    ) {
        Get-AgentBridgeExactProperty `
            -InputObject $preferredClaim `
            -Name 'task_id'
    } else {
        $null
    }
    if (
        $null -eq $preferredTaskProperty -or
        $preferredTaskProperty.Value -isnot [string] -or
        [string]$preferredTaskProperty.Value -cne $TaskId
    ) {
        $storedTaskId = if (
            $null -ne $preferredTaskProperty -and
            $preferredTaskProperty.Value -is [string]
        ) {
            "'$([string]$preferredTaskProperty.Value)'"
        } else {
            '<missing-or-nonstring>'
        }
        throw ((
            "claim filename collision at preferred path for task_id " +
            "'{0}': stored task_id {1} in {2}"
        ) -f
            $TaskId,
            $storedTaskId,
            $preferredPath
        )
    }
    return $preferredPath
}

function Test-AgentBridgeClaimOwner {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] $OwnerContext
    )

    if (-not (Test-AgentBridgeStoredClaimOwnerComplete -Claim $Claim)) {
        return $false
    }

    $ownerSessionProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_session_id'
    $ownerTokenProperty = Get-AgentBridgeExactProperty `
        -InputObject $Claim `
        -Name 'owner_token_sha256'
    return (
        $null -ne $ownerSessionProperty -and
        $null -ne $ownerTokenProperty -and
        [string]$ownerSessionProperty.Value -ceq
            [string]$OwnerContext.session_id -and
        [string]$ownerTokenProperty.Value -ceq
            [string]$OwnerContext.token_sha256
    )
}

function Assert-AgentBridgeClaimOwner {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Claim,
        [Parameter(Mandatory)] $OwnerContext,
        [Parameter(Mandatory)] [string] $Operation
    )

    if (-not (Test-AgentBridgeStoredClaimOwnerComplete -Claim $Claim)) {
        throw (
            "claim_owner_legacy_tokenless: current session cannot {0} a legacy tokenless claim" -f
            $Operation
        )
    }
    if (-not (Test-AgentBridgeClaimOwner -Claim $Claim -OwnerContext $OwnerContext)) {
        throw (
            "claim_owner_wrong_generation: current session cannot {0} claim owned by another generation" -f
            $Operation
        )
    }
}
