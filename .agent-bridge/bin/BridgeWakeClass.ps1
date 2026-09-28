#requires -Version 5.1
<#
.SYNOPSIS
    PowerShell port of the wd.wake-class.v1 wake classifier.

.DESCRIPTION
    Specification: docs/architecture/BRIDGE_WAKE_CLASS_CONTRACT_V1.md
    Reference:     tools/bridge_wake_class.py (classify(event, target_agent))
    Golden vectors: tests/fixtures/wake_class/v1/vectors.json

    Get-BridgeWakeClass -EventJson <raw row text> -TargetAgent <lane> returns
    an object with contract, class, wakes, reason and control_signal, equal to
    the Python reference applied to json.loads(<raw row text>).

    Input fidelity: the row is decoded here by a lossless JSON reader, never by
    ConvertFrom-Json. ConvertFrom-Json merges or rejects case-variant keys
    (Windows PowerShell 5.1 and PowerShell 7 differ) and does not keep the
    number/string/boolean distinctions the contract depends on, so a
    ConvertFrom-Json object cannot be classified faithfully and is refused.

    Raw-text rules beyond the reference (which starts from a decoded value):
      * text that Python json.loads would reject (invalid JSON, a BOM, a raw
        control character in a string, an integer over 4300 digits, extra
        data) classifies as ambiguous / malformed_event and wakes;
      * nesting deeper than 256 arrays/objects classifies as ambiguous /
        malformed_event and wakes (Python may decode deeper rows; this only
        ever adds a wake).

    Routing only: waking grants no authority and binds nothing. The file only
    defines functions; loading it has no other effect.
#>

function ConvertFrom-BridgeWakeEventJson {
    <#
    .SYNOPSIS
        Lossless JSON decoder with Python json.loads semantics.
    .OUTPUTS
        A hashtable: ok ($true/$false) and value. Objects decode to an
        ordinal (case-sensitive) OrderedDictionary in which a repeated exact
        key keeps its first position and its last value; arrays decode to
        List[object]; integers to Int64 or BigInteger; other numbers,
        NaN and Infinity to Double.
    #>
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Json)
    Set-StrictMode -Version Latest
    $fail = @{ ok = $false; value = $null }
    $maxDepth = 256
    # One .NET call tokenizes the whole row: PowerShell pays O(row length) for
    # every .NET call that takes the row as an argument, so a per-token
    # Match(row, position) would be quadratic. \G keeps the matches contiguous,
    # so the first character no token accepts ends the collection and the
    # coverage check below fails. The string token is atomic (its grammar is
    # unambiguous), so an unterminated string fails in linear time.
    $tokenRe = [regex]('\G(?:(?<ws>[ \t\n\r]+)' +
        '|"(?<str>(?>(?:[^"\\\x00-\x1f]+|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*))"' +
        '|(?<num>(?<int>-?(?:0|[1-9][0-9]*))(?<frac>\.[0-9]+)?(?<exp>[eE][-+]?[0-9]+)?)' +
        '|(?<lit>true|false|null|NaN|Infinity|-Infinity)' +
        '|(?<punct>[{}\[\]:,]))')
    $escRe = [regex]'\\(?:(["\\/bfnrt])|u([0-9a-fA-F]{4}))'
    $unescape = [System.Text.RegularExpressions.MatchEvaluator] {
        param($e)
        if ($e.Groups[1].Success) {
            switch -CaseSensitive ($e.Groups[1].Value) {
                'b' { return [string][char]8 }
                'f' { return [string][char]12 }
                'n' { return [string][char]10 }
                'r' { return [string][char]13 }
                't' { return [string][char]9 }
                default { return $e.Groups[1].Value }
            }
        }
        return [string][char][Convert]::ToInt32($e.Groups[2].Value, 16)
    }
    $inv = [System.Globalization.CultureInfo]::InvariantCulture
    $dictType = [System.Collections.Specialized.OrderedDictionary]
    $stack = New-Object System.Collections.Generic.List[object]
    $root = $null
    $state = 'value'
    $end = 0
    foreach ($m in $tokenRe.Matches($Json)) {
        $end = $m.Index + $m.Length
        if ($m.Groups['ws'].Success) { continue }
        $tok = $m.Value
        if ($state -ceq 'done') { return $fail }
        if ($state -ceq 'colon') {
            if (-not [string]::Equals($tok, ':')) { return $fail }
            $state = 'value'
            continue
        }
        $complete = $false
        $value = $null
        if ($state -ceq 'key' -or $state -ceq 'key_or_end') {
            if ($state -ceq 'key_or_end' -and [string]::Equals($tok, '}')) {
                $value = $stack[$stack.Count - 1].container
                $stack.RemoveAt($stack.Count - 1)
                $complete = $true
            } elseif ($m.Groups['str'].Success) {
                $key = $m.Groups['str'].Value
                if ($key.IndexOf('\') -ge 0) { $key = $escRe.Replace($key, $unescape) }
                $stack[$stack.Count - 1].key = $key
                $state = 'colon'
                continue
            } else { return $fail }
        } elseif ($state -ceq 'comma_or_end') {
            $top = $stack[$stack.Count - 1]
            $isObject = $top.container -is $dictType
            if ([string]::Equals($tok, ',')) {
                if ($isObject) { $state = 'key' } else { $state = 'value' }
                continue
            }
            if (($isObject -and [string]::Equals($tok, '}')) -or (-not $isObject -and [string]::Equals($tok, ']'))) {
                $value = $top.container
                $stack.RemoveAt($stack.Count - 1)
                $complete = $true
            } else { return $fail }
        } else {
            # state 'value' or 'value_or_end'
            if ($state -ceq 'value_or_end' -and [string]::Equals($tok, ']')) {
                $value = $stack[$stack.Count - 1].container
                $stack.RemoveAt($stack.Count - 1)
                $complete = $true
            } elseif ([string]::Equals($tok, '{') -or [string]::Equals($tok, '[')) {
                if ($stack.Count -ge $maxDepth) { return $fail }
                if ([string]::Equals($tok, '{')) {
                    $stack.Add(@{ container = (New-Object System.Collections.Specialized.OrderedDictionary ([System.StringComparer]::Ordinal)); key = $null })
                    $state = 'key_or_end'
                } else {
                    $stack.Add(@{ container = (New-Object System.Collections.Generic.List[object]); key = $null })
                    $state = 'value_or_end'
                }
                continue
            } elseif ($m.Groups['str'].Success) {
                $value = $m.Groups['str'].Value
                if ($value.IndexOf('\') -ge 0) { $value = $escRe.Replace($value, $unescape) }
                $complete = $true
            } elseif ($m.Groups['num'].Success) {
                if ($m.Groups['frac'].Success -or $m.Groups['exp'].Success) {
                    try {
                        $value = [double]::Parse($tok, [System.Globalization.NumberStyles]::Float, $inv)
                        # .NET Framework parses "-0.0" as +0.0; Python keeps the sign.
                        if ($value -eq 0 -and $tok.StartsWith('-')) {
                            $value = [BitConverter]::Int64BitsToDouble([long]::MinValue)
                        }
                    } catch [System.OverflowException] {
                        if ($tok.StartsWith('-')) { $value = [double]::NegativeInfinity }
                        else { $value = [double]::PositiveInfinity }
                    }
                } else {
                    $digits = $tok.TrimStart('-').Length
                    if ($digits -gt 4300) { return $fail }
                    if ($digits -le 18) { $value = [long]::Parse($tok, $inv) }
                    else { $value = [System.Numerics.BigInteger]::Parse($tok, $inv) }
                }
                $complete = $true
            } elseif ($m.Groups['lit'].Success) {
                switch -CaseSensitive ($tok) {
                    'true' { $value = $true }
                    'false' { $value = $false }
                    'null' { $value = $null }
                    'NaN' { $value = [double]::NaN }
                    'Infinity' { $value = [double]::PositiveInfinity }
                    '-Infinity' { $value = [double]::NegativeInfinity }
                }
                $complete = $true
            } else { return $fail }
        }
        if ($complete) {
            if ($stack.Count -eq 0) {
                $root = $value
                $state = 'done'
            } else {
                $top = $stack[$stack.Count - 1]
                if ($top.container -is $dictType) { $top.container[$top.key] = $value }
                else { $top.container.Add($value) }
                $state = 'comma_or_end'
            }
        }
    }
    if ($state -cne 'done' -or $end -ne $Json.Length) { return $fail }
    return @{ ok = $true; value = $root }
}

function ConvertTo-BridgeWakeAsciiLower {
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)
    Set-StrictMode -Version Latest
    if ($Text -cnotmatch '[A-Z]') { return $Text }
    $chars = $Text.ToCharArray()
    for ($i = 0; $i -lt $chars.Length; $i++) {
        $code = [int]$chars[$i]
        if ($code -ge 65 -and $code -le 90) {
            $chars[$i] = [char]($code + 32)
        }
    }
    return -join $chars
}

function ConvertTo-BridgeWakePythonJson {
    <#
    .SYNOPSIS
        Python json.dumps(value, ensure_ascii=False) text of a decoded value.
    #>
    param([AllowNull()] [object] $Value, [int] $Depth = 0)
    Set-StrictMode -Version Latest
    if ($Depth -gt 300) { throw 'BridgeWakeClass: value nested too deeply to render' }
    if ($null -eq $Value) { return 'null' }
    if ($Value -is [bool]) { if ($Value) { return 'true' } else { return 'false' } }
    if ($Value -is [string]) {
        $sb = New-Object System.Text.StringBuilder
        [void]$sb.Append('"')
        foreach ($ch in $Value.ToCharArray()) {
            $code = [int]$ch
            if ($code -eq 34) { [void]$sb.Append('\"') }
            elseif ($code -eq 92) { [void]$sb.Append('\\') }
            elseif ($code -eq 10) { [void]$sb.Append('\n') }
            elseif ($code -eq 13) { [void]$sb.Append('\r') }
            elseif ($code -eq 9) { [void]$sb.Append('\t') }
            elseif ($code -eq 8) { [void]$sb.Append('\b') }
            elseif ($code -eq 12) { [void]$sb.Append('\f') }
            elseif ($code -lt 32) { [void]$sb.Append('\u').Append($code.ToString('x4')) }
            else { [void]$sb.Append($ch) }
        }
        [void]$sb.Append('"')
        return $sb.ToString()
    }
    $inv = [System.Globalization.CultureInfo]::InvariantCulture
    if ($Value -is [long] -or $Value -is [System.Numerics.BigInteger]) { return $Value.ToString($inv) }
    if ($Value -is [double]) {
        if ([double]::IsNaN($Value)) { return 'NaN' }
        if ([double]::IsPositiveInfinity($Value)) { return 'Infinity' }
        if ([double]::IsNegativeInfinity($Value)) { return '-Infinity' }
        if ($Value -eq 0) {
            if ([BitConverter]::DoubleToInt64Bits($Value) -lt 0) { return '-0.0' } else { return '0.0' }
        }
        # Shortest round-trip digits, then Python repr layout.
        $e = $null
        for ($p = 1; $p -le 17; $p++) {
            $e = $Value.ToString('E' + ($p - 1), $inv)
            $back = $null
            try { $back = [double]::Parse($e, [System.Globalization.NumberStyles]::Float, $inv) }
            catch [System.OverflowException] { continue }
            if ($back -eq $Value) { break }
        }
        $negative = $e.StartsWith('-')
        $body = $e.TrimStart('-')
        $parts = $body.Split('E')
        $digits = $parts[0].Replace('.', '').TrimEnd('0')
        if ($digits.Length -eq 0) { $digits = '0' }
        $decpt = [int]::Parse($parts[1], $inv) + 1
        if ($decpt -le -4 -or $decpt -gt 16) {
            $mant = $digits.Substring(0, 1)
            if ($digits.Length -gt 1) { $mant = $mant + '.' + $digits.Substring(1) }
            $exp = $decpt - 1
            $sign = '+'
            if ($exp -lt 0) { $sign = '-'; $exp = -$exp }
            $out = $mant + 'e' + $sign + $exp.ToString('00', $inv)
        } elseif ($decpt -le 0) {
            $out = '0.' + ('0' * (-$decpt)) + $digits
        } elseif ($decpt -ge $digits.Length) {
            $out = $digits + ('0' * ($decpt - $digits.Length)) + '.0'
        } else {
            $out = $digits.Substring(0, $decpt) + '.' + $digits.Substring($decpt)
        }
        if ($negative) { $out = '-' + $out }
        return $out
    }
    if ($Value -is [System.Collections.Specialized.OrderedDictionary]) {
        $items = foreach ($k in $Value.Keys) {
            (ConvertTo-BridgeWakePythonJson -Value ([string]$k) -Depth ($Depth + 1)) + ': ' +
                (ConvertTo-BridgeWakePythonJson -Value $Value[$k] -Depth ($Depth + 1))
        }
        return '{' + (@($items) -join ', ') + '}'
    }
    if ($Value -is [System.Collections.Generic.List[object]]) {
        $items = foreach ($item in $Value) {
            ConvertTo-BridgeWakePythonJson -Value $item -Depth ($Depth + 1)
        }
        return '[' + (@($items) -join ', ') + ']'
    }
    throw 'BridgeWakeClass: value is not from the lossless decoder'
}

function Get-BridgeWakeClassFromDecoded {
    <#
    .SYNOPSIS
        wd.wake-class.v1 classify() on a value from ConvertFrom-BridgeWakeEventJson.
    .DESCRIPTION
        Accepts only the lossless decoder's value model. A ConvertFrom-Json
        object (PSCustomObject), a hashtable or any other type is refused with
        an exception rather than classified on lossy evidence.
    #>
    param(
        [AllowNull()] [object] $Event,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $TargetAgent
    )
    Set-StrictMode -Version Latest
    if ($TargetAgent -cnotmatch '\A[a-z0-9][a-z0-9._-]{0,127}\z') {
        throw 'target_agent must be a lowercase bridge agent name'
    }
    $dictType = [System.Collections.Specialized.OrderedDictionary]
    $listType = [System.Collections.Generic.List[object]]
    $known = ($null -eq $Event) -or ($Event -is [string]) -or ($Event -is [bool]) -or
        ($Event -is [long]) -or ($Event -is [System.Numerics.BigInteger]) -or
        ($Event -is [double]) -or ($Event -is $dictType) -or ($Event -is $listType)
    if (-not $known) { throw 'BridgeWakeClass: event is not from the lossless decoder' }

    # Every string comparison below is ordinal. PowerShell -ceq and -ccontains
    # compare with the culture, where ignorable code points (U+200B, U+180E,
    # U+00AD, ...) vanish: 'fable-5' + U+200B would equal 'fable-5'.
    $newSet = {
        param([string[]] $Items)
        $set = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
        foreach ($item in $Items) { [void]$set.Add($item) }
        return , $set
    }
    $nonWaking = @('notice', 'noise', 'not_addressed')
    $nonWakingSet = & $newSet $nonWaking
    $result = {
        param([string] $Class, [string] $Reason, [bool] $Control)
        [pscustomobject][ordered]@{
            contract = 'wd.wake-class.v1'
            class = $Class
            wakes = -not $nonWakingSet.Contains($Class)
            reason = $Reason
            control_signal = $Control
        }
    }
    if ($Event -isnot $dictType) { return & $result 'ambiguous' 'malformed_event' $false }

    $controlTypes = @('decision', 'finding', 'blocked', 'rco_review', 'test', 'done', 'release', 'wake_request')
    $noticeTypes = @('message', 'status', 'intent')
    $livenessTypes = @('heartbeat', 'liveness')
    $ackStatuses = @('received', 'seen', 'acknowledged')
    $ackPayloadKeys = @('request_ts_utc', 'request_agent', 'request_type', 'request_status', 'notification')
    $livenessPayloadKeys = @('head', 'notification')
    $bindingKeys = @('request_id', 'in_reply_to_request_id', 'result', 'result_contract')
    $envelopeKeys = @('agent', 'to', 'type', 'status', 'payload', 'request_id', 'in_reply_to_request_id', 'expected_responders')
    $benign = @('informational', 'info', 'notice', 'evidence', 'evidence_update', 'progress',
        'progress_summary', 'in_progress', 'planning')
    $roots = @('hold', 'held', 'veto', 'cancel', 'supersed', 'withdr',
        'retract', 'revok', 'revoc', 'reject', 'refus', 'deny', 'denied', 'nack',
        'fail', 'clos', 'stop', 'halt', 'abort', 'freez', 'frozen', 'quarantin',
        'rollback', 'revert', 'changesrequested', 'paus', 'suspend', 'kill',
        'incident', 'emergenc', 'escalat', 'error', 'timeout', 'expir', 'wedg',
        'unsafe', 'invalid', 'conflict', 'regress', 'broke', 'critical',
        'disapprov', 'nogo', 'embargo', 'lock', 'donot', 'notapprov', 'notpass',
        'notmerg', 'notready', 'wait')
    $controlTypeSet = & $newSet $controlTypes
    $noticeTypeSet = & $newSet $noticeTypes
    $livenessTypeSet = & $newSet $livenessTypes
    $ackStatusSet = & $newSet $ackStatuses
    $ackPayloadKeySet = & $newSet $ackPayloadKeys
    $livenessPayloadKeySet = & $newSet $livenessPayloadKeys
    $bindingKeySet = & $newSet $bindingKeys
    $benignSet = & $newSet $benign
    # Python str.strip() whitespace (str.isspace), all in the BMP.
    $pyWhitespace = [char[]]@(9, 10, 11, 12, 13, 0x1c, 0x1d, 0x1e, 0x1f, 0x20, 0x85, 0xa0, 0x1680,
        0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a,
        0x2028, 0x2029, 0x202f, 0x205f, 0x3000)

    $hasControlToken = {
        param([string] $Status)
        $joined = [regex]::Replace((ConvertTo-BridgeWakeAsciiLower $Status), '[^a-z0-9]+', '')
        foreach ($root in $roots) { if ($joined.Contains($root)) { return $true } }
        return $false
    }
    $hasVariant = {
        param($Obj, [string] $Name)
        foreach ($k in $Obj.Keys) {
            if (-not [string]::Equals([string]$k, $Name) -and [string]::Equals((ConvertTo-BridgeWakeAsciiLower ([string]$k)), $Name)) { return $true }
        }
        return $false
    }
    $isEmptyValue = {
        param($V)
        if ($null -eq $V) { return $true }
        if ($V -is [string]) { return $V.Length -eq 0 }
        if ($V -is $dictType) { return $V.Count -eq 0 }
        if ($V -is $listType) { return $V.Count -eq 0 }
        return $false
    }
    $idState = {
        param($Obj, [string] $Name)
        if (-not $Obj.Contains($Name)) { return 'absent' }
        $v = $Obj[$Name]
        if ($null -eq $v) { return 'absent' }
        if ($v -is [string]) {
            if ($v.Length -eq 0) { return 'absent' }
            if ($v -cmatch '\A[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\z') { return 'valid' }
        }
        return 'malformed'
    }
    $noisePayloadOk = {
        param($Allowed)
        if (-not $Event.Contains('payload')) { return $true }
        $p = $Event['payload']
        if ($null -eq $p) { return $true }
        if ($p -isnot $dictType) { return $false }
        foreach ($k in $p.Keys) {
            if (-not $Allowed.Contains([string]$k) -or $p[$k] -isnot [string]) { return $false }
        }
        if (-not $p.Contains('notification')) { return $true }
        return [string]::Equals($p['notification'], 'informational')
    }

    # Never assign through an if-expression: PowerShell would unroll a
    # one-element list into its element and lose the list type.
    $etype = $null
    if ($Event.Contains('type')) { $etype = $Event['type'] }
    $status = $null
    if ($Event.Contains('status')) { $status = $Event['status'] }
    $ctl = (($etype -is [string]) -and $controlTypeSet.Contains($etype)) -or
        (($status -is [string]) -and (& $hasControlToken $status))

    $agentPresent = $Event.Contains('agent')
    $agent = $null
    if ($agentPresent) { $agent = $Event['agent'] }
    $agentVariant = & $hasVariant $Event 'agent'
    if ($agentPresent -and ($agent -is [string]) -and [string]::Equals($agent, $TargetAgent) -and -not $agentVariant) {
        return & $result 'not_addressed' 'self_emission' $ctl
    }

    $addressLike = New-Object System.Collections.Generic.List[object]
    foreach ($k in $Event.Keys) {
        $lk = ConvertTo-BridgeWakeAsciiLower ([string]$k)
        if (([string]::Equals($lk, 'to') -or [string]::Equals($lk, 'expected_responders')) -and -not (& $isEmptyValue $Event[$k])) {
            $addressLike.Add($Event[$k])
        }
    }
    if ($addressLike.Count -eq 0) { return & $result 'not_addressed' 'no_target' $ctl }
    $to = $null
    if ($Event.Contains('to')) { $to = $Event['to'] }
    $exact = $false
    if (-not (& $hasVariant $Event 'to') -and ($to -is [string])) {
        foreach ($entry in $to.Split([char]',')) {
            $t = $entry.Trim($pyWhitespace)
            if ($t.Length -gt 0 -and [string]::Equals($t, $TargetAgent)) { $exact = $true; break }
        }
    }
    if (-not $exact) {
        $pattern = '(?<![a-z0-9])' + [regex]::Escape($TargetAgent) + '(?![a-z0-9])'
        foreach ($v in $addressLike) {
            $text = if ($v -is [string]) { $v } else { ConvertTo-BridgeWakePythonJson -Value $v }
            if ([regex]::IsMatch((ConvertTo-BridgeWakeAsciiLower $text), $pattern)) {
                return & $result 'ambiguous' 'ambiguous_target' $ctl
            }
        }
        return & $result 'not_addressed' 'not_targeted' $ctl
    }

    foreach ($key in $envelopeKeys) {
        if (& $hasVariant $Event $key) { return & $result 'ambiguous' 'case_variant_key' $ctl }
    }
    if (($agent -isnot [string]) -or $agent.Trim($pyWhitespace).Length -eq 0) {
        return & $result 'ambiguous' 'missing_sender' $ctl
    }
    if ([string]::Equals((ConvertTo-BridgeWakeAsciiLower $agent.Trim($pyWhitespace)), $TargetAgent)) {
        return & $result 'ambiguous' 'sender_case_variant' $ctl
    }

    $rid = & $idState $Event 'request_id'
    $irr = & $idState $Event 'in_reply_to_request_id'
    if ($rid -ceq 'malformed' -or $irr -ceq 'malformed') {
        return & $result 'ambiguous' 'malformed_request_id' $ctl
    }

    if (-not (($etype -is [string]) -and $etype.Length -gt 0 -and ($status -is [string]) -and $status.Length -gt 0)) {
        return & $result 'ambiguous' 'malformed_type_or_status' $ctl
    }
    if ($etype -cmatch '[^\x00-\x7F]' -or $status -cmatch '[^\x00-\x7F]') {
        return & $result 'ambiguous' 'non_ascii_field' $ctl
    }
    if ($etype.Length -gt 256 -or $status.Length -gt 256) {
        return & $result 'ambiguous' 'oversized_field' $ctl
    }
    $controlStatus = & $hasControlToken $status

    if ($livenessTypeSet.Contains($etype)) {
        if ($controlStatus -or $rid -cne 'absent' -or $irr -cne 'absent') {
            return & $result 'ambiguous' 'conflicting_noise_signal' $ctl
        }
        if (-not (& $noisePayloadOk $livenessPayloadKeySet)) {
            return & $result 'ambiguous' 'noise_payload_not_recognized' $ctl
        }
        return & $result 'noise' 'liveness' $false
    }
    if ($ackStatusSet.Contains($status)) {
        if (-not [string]::Equals($etype, 'message') -or $rid -cne 'absent') {
            return & $result 'ambiguous' 'conflicting_noise_signal' $ctl
        }
        if (-not (& $noisePayloadOk $ackPayloadKeySet)) {
            return & $result 'ambiguous' 'noise_payload_not_recognized' $ctl
        }
        return & $result 'noise' 'ack' $false
    }

    if ($irr -ceq 'valid' -and $rid -ceq 'valid') {
        return & $result 'ambiguous' 'conflicting_request_and_reply' $ctl
    }
    if ($irr -ceq 'valid') { return & $result 'bound_reply' 'claimed_reply' $ctl }
    if ($rid -ceq 'valid') { return & $result 'request' 'request_id' $ctl }

    if ($controlTypeSet.Contains($etype)) { return & $result 'control' 'control_type' $ctl }
    if (-not $noticeTypeSet.Contains($etype)) { return & $result 'ambiguous' 'unknown_type' $ctl }
    if ($controlStatus) { return & $result 'control' 'control_status' $ctl }

    $payload = $null
    if ($Event.Contains('payload')) { $payload = $Event['payload'] }
    if (-not $Event.Contains('payload') -or $null -eq $payload) {
        return & $result 'ambiguous' 'unhinted_notice' $ctl
    }
    if ($payload -isnot $dictType) { return & $result 'ambiguous' 'malformed_payload' $ctl }
    foreach ($k in $payload.Keys) {
        $v = $payload[$k]
        if ($bindingKeySet.Contains((ConvertTo-BridgeWakeAsciiLower ([string]$k))) -and
            $null -ne $v -and -not (($v -is [string]) -and $v.Length -eq 0)) {
            return & $result 'ambiguous' 'payload_binding_field' $ctl
        }
    }
    $notificationVariant = & $hasVariant $payload 'notification'
    $notificationPresent = $payload.Contains('notification')
    if ($notificationVariant -or ($notificationPresent -and -not ($payload['notification'] -is [string] -and
            [string]::Equals($payload['notification'], 'informational')))) {
        return & $result 'ambiguous' 'notification_variant' $ctl
    }
    if (-not $notificationPresent) { return & $result 'ambiguous' 'unhinted_notice' $ctl }
    if (-not $benignSet.Contains($status)) { return & $result 'ambiguous' 'unlisted_status' $ctl }
    return & $result 'notice' 'informational_hint' $false
}

function Get-BridgeWakeClass {
    <#
    .SYNOPSIS
        Classify one raw bridge row for a target lane's wake inbox (wd.wake-class.v1).
    .PARAMETER EventJson
        The raw row text exactly as read from the log. $null or text that
        Python json.loads would reject classifies as ambiguous / malformed_event.
        A non-string argument (a decoded object) is refused with an exception.
    .PARAMETER TargetAgent
        Canonical lowercase lane name; anything else throws.
    .OUTPUTS
        PSCustomObject: contract, class, wakes, reason, control_signal.
    #>
    param(
        [Parameter(Mandatory)] [AllowNull()] [AllowEmptyString()] [object] $EventJson,
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $TargetAgent
    )
    Set-StrictMode -Version Latest
    if ($TargetAgent -cnotmatch '\A[a-z0-9][a-z0-9._-]{0,127}\z') {
        throw 'target_agent must be a lowercase bridge agent name'
    }
    if ($null -ne $EventJson -and $EventJson -isnot [string]) {
        throw 'BridgeWakeClass: EventJson must be the raw row text, not a decoded object'
    }
    $decoded = ConvertFrom-BridgeWakeEventJson -Json ([string]$EventJson)
    if (-not $decoded.ok) {
        return [pscustomobject][ordered]@{
            contract = 'wd.wake-class.v1'; class = 'ambiguous'; wakes = $true
            reason = 'malformed_event'; control_signal = $false
        }
    }
    return Get-BridgeWakeClassFromDecoded -Event $decoded.value -TargetAgent $TargetAgent
}
