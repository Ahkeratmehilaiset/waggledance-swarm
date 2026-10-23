# DORMANT canonical JSON, version 2. Nothing calls this file: no bridge writer, reader, request contract, digest,
# nonce, gate or consumer uses it, and it does not replace BridgeRequestContract.ps1. Legacy v1 keeps its known
# PS5/PS7 host variants. Using this format for live requests needs a separately reviewed wiring change and an
# install decision.
#
# ConvertTo-WdBridgeCanonicalV2 returns one canonical JSON string. Accepted input, and nothing else:
#   $null; [bool]; the signed integer types [sbyte], [int16], [int32] and [int64] (invariant decimal digits);
#   [string]; IDictionary with [string] keys; PSCustomObject note properties; IList arrays.
# Every other type is REFUSED, never converted: [double], [single], [decimal], NaN and infinities, [datetime],
# [datetimeoffset], [char], unsigned integers, enums and any unknown type throw.
# Strings escape only quote, backslash and U+0000..U+001F (\b \f \n \r \t, other controls as lowercase \u00xx);
# every other character stays literal, with no Unicode normalization. Unpaired UTF-16 surrogates are refused.
# Object keys sort with the ordinal UTF-16 comparer; arrays keep their order. Nesting is bounded by -MaxDepth and
# output length (UTF-16 chars) by -MaxChars; a caller may lower both but not raise them. The input is only read.
# No cmdlet is called, so neither a culture-dependent sort nor the v1 serializer's scalar path is involved.
# Strict UTF-8 encoding of the result is the caller's job. This file defines functions only.

function ConvertTo-WdBridgeCanonicalV2 {
    [CmdletBinding()]
    param(
        [AllowNull()] $Value,
        [ValidateRange(1, 32)] [int] $MaxDepth = 32,
        [ValidateRange(1, 1048576)] [int] $MaxChars = 1048576
    )
    $builder = [System.Text.StringBuilder]::new()
    Add-WdBridgeCanonicalV2Value -Builder $builder -Value $Value -Depth 0 -MaxDepth $MaxDepth -MaxChars $MaxChars
    return $builder.ToString()
}

function Add-WdBridgeCanonicalV2Value {
    param([System.Text.StringBuilder] $Builder, $Value, [int] $Depth, [int] $MaxDepth, [int] $MaxChars)
    if ([object]::ReferenceEquals($null, $Value)) {
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text 'null' -MaxChars $MaxChars
        return
    }
    # GetType() sees through a PSObject wrapper (a PSCustomObject reports its own type); a method, unlike a
    # property, is never shadowed by a dictionary key.
    $base = $Value
    $type = $base.GetType()
    if ($type -eq [string]) {
        Add-WdBridgeCanonicalV2String -Builder $Builder -Text $base -MaxChars $MaxChars
        return
    }
    if ($type -eq [bool]) {
        $word = 'false'
        if ($base) { $word = 'true' }
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text $word -MaxChars $MaxChars
        return
    }
    if ($type -eq [sbyte] -or $type -eq [int16] -or $type -eq [int32] -or $type -eq [int64]) {
        $digits = $base.ToString([System.Globalization.CultureInfo]::InvariantCulture)
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text $digits -MaxChars $MaxChars
        return
    }
    $isDictionary = $base -is [System.Collections.IDictionary]
    $isObject = $base -is [System.Management.Automation.PSCustomObject]
    $isList = $base -is [System.Collections.IList]
    if (-not ($isDictionary -or $isObject -or $isList)) {
        throw "canonical-v2: unsupported type $($type.FullName)"
    }
    $inner = $Depth + 1
    if ($inner -gt $MaxDepth) { throw "canonical-v2: nesting depth exceeds $MaxDepth" }
    if ($isList) {
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text '[' -MaxChars $MaxChars
        for ($i = 0; $i -lt $base.Count; $i++) {
            if ($i -gt 0) { Add-WdBridgeCanonicalV2Text -Builder $Builder -Text ',' -MaxChars $MaxChars }
            Add-WdBridgeCanonicalV2Value -Builder $Builder -Value $base[$i] -Depth $inner -MaxDepth $MaxDepth -MaxChars $MaxChars
        }
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text ']' -MaxChars $MaxChars
        return
    }
    $names = [System.Collections.Generic.List[string]]::new()
    $values = [System.Collections.Generic.Dictionary[string, object]]::new([System.StringComparer]::Ordinal)
    if ($isDictionary) {
        foreach ($entry in $base.GetEnumerator()) {
            $key = $entry.Key
            if ([object]::ReferenceEquals($null, $key) -or $key.GetType() -ne [string]) {
                $keyType = 'null'
                if (-not [object]::ReferenceEquals($null, $key)) { $keyType = $key.GetType().FullName }
                throw "canonical-v2: non-string key $keyType"
            }
            if ($values.ContainsKey($key)) { throw 'canonical-v2: duplicate key' }
            $names.Add($key)
            $values[$key] = $entry.Value
        }
    } else {
        foreach ($property in $Value.PSObject.Properties) {
            if ($property.MemberType -ne [System.Management.Automation.PSMemberTypes]::NoteProperty) {
                throw "canonical-v2: unsupported member type $($property.MemberType)"
            }
            if ($values.ContainsKey($property.Name)) { throw 'canonical-v2: duplicate key' }
            $names.Add($property.Name)
            $values[$property.Name] = $property.Value
        }
    }
    $names.Sort([System.StringComparer]::Ordinal)
    Add-WdBridgeCanonicalV2Text -Builder $Builder -Text '{' -MaxChars $MaxChars
    for ($i = 0; $i -lt $names.Count; $i++) {
        if ($i -gt 0) { Add-WdBridgeCanonicalV2Text -Builder $Builder -Text ',' -MaxChars $MaxChars }
        Add-WdBridgeCanonicalV2String -Builder $Builder -Text $names[$i] -MaxChars $MaxChars
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text ':' -MaxChars $MaxChars
        Add-WdBridgeCanonicalV2Value -Builder $Builder -Value $values[$names[$i]] -Depth $inner -MaxDepth $MaxDepth -MaxChars $MaxChars
    }
    Add-WdBridgeCanonicalV2Text -Builder $Builder -Text '}' -MaxChars $MaxChars
}

function Add-WdBridgeCanonicalV2String {
    param([System.Text.StringBuilder] $Builder, [string] $Text, [int] $MaxChars)
    if ($Builder.Length + $Text.Length + 2 -gt $MaxChars) { throw "canonical-v2: output exceeds $MaxChars characters" }
    if (-not [regex]::IsMatch($Text, '[\x00-\x1F"\\\uD800-\uDFFF]')) {
        Add-WdBridgeCanonicalV2Text -Builder $Builder -Text ('"' + $Text + '"') -MaxChars $MaxChars
        return
    }
    $out = [System.Text.StringBuilder]::new($Text.Length + 16)
    [void]$out.Append('"')
    for ($i = 0; $i -lt $Text.Length; $i++) {
        $code = [int]$Text[$i]
        if ($code -ge 0xD800 -and $code -le 0xDBFF) {
            if ($i + 1 -ge $Text.Length -or [int]$Text[$i + 1] -lt 0xDC00 -or [int]$Text[$i + 1] -gt 0xDFFF) {
                throw 'canonical-v2: unpaired UTF-16 surrogate'
            }
            [void]$out.Append($Text[$i]).Append($Text[$i + 1])
            $i++
            continue
        }
        if ($code -ge 0xDC00 -and $code -le 0xDFFF) { throw 'canonical-v2: unpaired UTF-16 surrogate' }
        if ($code -eq 0x22) { [void]$out.Append('\"') }
        elseif ($code -eq 0x5C) { [void]$out.Append('\\') }
        elseif ($code -eq 0x08) { [void]$out.Append('\b') }
        elseif ($code -eq 0x09) { [void]$out.Append('\t') }
        elseif ($code -eq 0x0A) { [void]$out.Append('\n') }
        elseif ($code -eq 0x0C) { [void]$out.Append('\f') }
        elseif ($code -eq 0x0D) { [void]$out.Append('\r') }
        elseif ($code -lt 0x20) { [void]$out.Append('\u').Append($code.ToString('x4', [System.Globalization.CultureInfo]::InvariantCulture)) }
        else { [void]$out.Append($Text[$i]) }
    }
    [void]$out.Append('"')
    Add-WdBridgeCanonicalV2Text -Builder $Builder -Text $out.ToString() -MaxChars $MaxChars
}

function Add-WdBridgeCanonicalV2Text {
    param([System.Text.StringBuilder] $Builder, [string] $Text, [int] $MaxChars)
    if ($Builder.Length + $Text.Length -gt $MaxChars) { throw "canonical-v2: output exceeds $MaxChars characters" }
    [void]$Builder.Append($Text)
}
