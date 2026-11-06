#requires -Version 7.0
# Default-off synthetic host seam, not a launcher or production authorization.
# The caller authenticates every supplied pin and the bootstrap's semantics.
# Checks are before launch; path replacement after hashing, interpreter DLLs,
# imported code, same-process tampering, full-host deadlines and durable
# Job/lease binding are NOT proved here. The bootstrap must be an externally
# pinned executable script that invokes the intended session outer; that
# connection is not inferred. OK proves only framing, not the outer's answer
# schema, provenance or safety; the caller must validate before any use.
# A trusted outer watchdog must kill the entire host tree if preflight or pipe
# handling hangs. The caller MUST NOT place query text in argv.
# UNKNOWN blocks further attempts in this PowerShell process only. Caller must
# provide persistent cross-process quarantine and must never auto-retry UNKNOWN.
# Dot-sourcing again in the same script scope must not reset quarantine or
# replace the already bound helper. This is not cross-process persistence or a
# defense against a caller able to tamper with script-scoped variables.
if ($null -eq (Get-Variable WdQ4SessionHostUnknown -Scope Script -ErrorAction SilentlyContinue)) {
    $script:WdQ4SessionHostUnknown = $false
}
if ($null -eq (Get-Variable WdQ4SessionHostJobPin -Scope Script -ErrorAction SilentlyContinue)) {
    $script:WdQ4SessionHostJobPin = $null
}
if ($null -eq (Get-Variable WdQ4SessionHostJobFunction -Scope Script -ErrorAction SilentlyContinue)) {
    $script:WdQ4SessionHostJobFunction = $null
}

function Invoke-WdQ4SessionHost {
    [CmdletBinding()]
    param(
        [switch] $Enabled,
        [Parameter(Mandatory)][string] $JobHelper,
        [Parameter(Mandatory)][string] $JobHelperSha256,
        [Parameter(Mandatory)][string] $Interpreter,
        [Parameter(Mandatory)][string] $InterpreterSha256,
        [Parameter(Mandatory)][string] $Bootstrap,
        [Parameter(Mandatory)][string] $BootstrapSha256,
        [Parameter(Mandatory)][AllowEmptyCollection()][string[]] $BootstrapArguments,
        [Parameter(Mandatory)][string] $WorkingDirectory,
        [Parameter(Mandatory)][string] $ScratchRoot,
        [Parameter(Mandatory)][AllowEmptyCollection()][byte[]] $InputBytes,
        [Parameter(Mandatory)][int] $TimeoutSeconds,
        [Parameter(Mandatory)][int] $MaxOutputBytes
    )
    if (-not $Enabled) { return [pscustomobject]@{Status='OFF';Output=$null} }
    if ($script:WdQ4SessionHostUnknown) { return [pscustomobject]@{Status='UNKNOWN';Output=$null} }
    $entered=$false
    $doc=$null
    try {
        if ($InputBytes.Length -gt 16000 -or $TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 1800 -or
            $MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 65536) { throw 'refused' }
        foreach ($item in @(@($JobHelper,$JobHelperSha256), @($Interpreter,$InterpreterSha256),
                            @($Bootstrap,$BootstrapSha256))) {
            $path=$item[0];$pin=$item[1]
            if (-not [IO.Path]::IsPathFullyQualified($path) -or $pin -cnotmatch '^[a-f0-9]{64}$' -or
                -not [IO.File]::Exists($path) -or
                ([IO.File]::GetAttributes($path) -band [IO.FileAttributes]::ReparsePoint)) { throw 'refused' }
        }
        if (-not [IO.Path]::IsPathFullyQualified($WorkingDirectory)) { throw 'refused' }
        # Read at most the documented limit + one byte, under a non-write/non-
        # delete share. Execute these exact verified bytes, not a later path read.
        $helperStream=$null
        try {
            $helperStream=[IO.File]::Open($JobHelper,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
            if ($helperStream.Length -gt 1048576) { throw 'refused' }
            $helperBuffer=[byte[]]::new(1048577)
            $offset=0
            while ($offset -lt $helperBuffer.Length) {
                $n=$helperStream.Read($helperBuffer,$offset,$helperBuffer.Length-$offset)
                if ($n -eq 0) { break }
                $offset+=$n
            }
            if ($offset -gt 1048576 -or $helperStream.Length -ne $offset) { throw 'refused' }
            $helperBytes=[byte[]]::new($offset)
            [array]::Copy($helperBuffer,$helperBytes,$offset)
        } finally {
            if ($null -ne $helperStream) { $helperStream.Dispose() }
        }
        if (
            [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($helperBytes)).ToLowerInvariant() -cne $JobHelperSha256 -or
            (Get-FileHash -LiteralPath $Interpreter -Algorithm SHA256).Hash.ToLowerInvariant() -cne $InterpreterSha256 -or
            (Get-FileHash -LiteralPath $Bootstrap -Algorithm SHA256).Hash.ToLowerInvariant() -cne $BootstrapSha256) { throw 'refused' }
        if ($null -eq $script:WdQ4SessionHostJobFunction) {
            $helperText=[Text.UTF8Encoding]::new($false,$true).GetString($helperBytes).TrimStart([char]0xfeff)
            . ([scriptblock]::Create($helperText))
            $script:WdQ4SessionHostJobFunction=(Get-Item Function:Invoke-WdQ4ProcessJob).ScriptBlock
            $script:WdQ4SessionHostJobPin=$JobHelperSha256
        } elseif ($script:WdQ4SessionHostJobPin -cne $JobHelperSha256) { throw 'refused' }
        $arguments=@('-I','-S','-B',$Bootstrap)+$BootstrapArguments
        $entered=$true
        $result=& $script:WdQ4SessionHostJobFunction -DetailedOutcome -Executable $Interpreter -Arguments $arguments `
            -WorkingDirectory $WorkingDirectory -ScratchRoot $ScratchRoot -InputBytes $InputBytes `
            -TimeoutSeconds $TimeoutSeconds -MaxOutputBytes $MaxOutputBytes
        $entered=$false
        if ($result.Outcome -ceq 'cleanup_unknown' -or $result.Cleanup -ceq 'unknown') {
            $script:WdQ4SessionHostUnknown=$true
            return [pscustomobject]@{Status='UNKNOWN';Output=$null}
        }
        # The pinned session worker reserves 3 for inner cleanup UNKNOWN and
        # 130 for interruption; outer Job cleanup cannot clear either state.
        if ($result.Outcome -ceq 'completed' -and $result.Cleanup -ceq 'confirmed' -and
            $result.ExitCode -in @(3,130)) {
            $script:WdQ4SessionHostUnknown=$true
            return [pscustomobject]@{Status='UNKNOWN';Output=$null}
        }
        if ($result.Cleanup -ceq 'confirmed' -and
            ($result.Outcome -ceq 'timeout' -or
             ($result.Outcome -ceq 'completed' -and $result.ExitCode -eq 4))) {
            return [pscustomobject]@{Status='TIMEOUT';Output=$null}
        }
        if ($result.Outcome -cne 'completed' -or $result.Cleanup -cne 'confirmed' -or
            $result.ExitCode -ne 0 -or $result.Stdout -isnot [byte[]] -or
            $result.Stdout.Length -lt 3 -or $result.Stdout.Length -gt $MaxOutputBytes -or
            $result.Stdout[-1] -ne 10) { throw 'refused' }
        $text=[Text.UTF8Encoding]::new($false,$true).GetString($result.Stdout,0,$result.Stdout.Length-1)
        if ($text.Contains("`n") -or $text.Contains("`r")) { throw 'refused' }
        $doc=[System.Text.Json.JsonDocument]::Parse([string]$text)
        if ($doc.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { throw 'refused' }
        return [pscustomobject]@{Status='OK';Output=$result.Stdout}
    } catch {
        if ($entered) { $script:WdQ4SessionHostUnknown=$true; return [pscustomobject]@{Status='UNKNOWN';Output=$null} }
        return [pscustomobject]@{Status='REFUSED';Output=$null}
    } finally {
        if ($null -ne $doc) { $doc.Dispose() }
    }
}
