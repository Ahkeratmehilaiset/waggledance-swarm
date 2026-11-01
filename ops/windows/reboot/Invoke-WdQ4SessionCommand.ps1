#requires -Version 7.0
# Default-OFF synthetic user-command candidate, callable by dot-sourcing.
# Caller independently authenticates this command, host, entry, all tuple
# values and lane identity, and supplies only non-query arguments. Neither
# authorization nor a real agent-session connection is established here.
# Input is read only from this process's stdin, never deliberately placed in
# argv, environment or a file. The caller must not hide query text in tuple
# values. The pinned host provides framing, not answer-schema or provenance
# validation; UNVALIDATED is not a successful or production Mind answer.
# An external whole-process watchdog and persistent cross-process quarantine
# are absent. UNKNOWN prevents another call only in this PowerShell scope.
# Same-user replacement, caller-frame memory and compromised caller remain out
# of scope. Never invoke a shell with query text or automatically retry UNKNOWN.
if ($null -eq (Get-Variable WdQ4SessionCommandUnknown -Scope Script -ErrorAction SilentlyContinue)) {
    $script:WdQ4SessionCommandUnknown = $false
}
if ($null -eq (Get-Variable WdQ4SessionCommandHostPin -Scope Script -ErrorAction SilentlyContinue)) {
    $script:WdQ4SessionCommandHostPin = $null
}

function Invoke-WdQ4SessionCommand {
    [CmdletBinding()]
    param(
        [switch] $Enabled,
        [Parameter(Mandatory)][string] $HostScript,
        [Parameter(Mandatory)][string] $HostSha256,
        [Parameter(Mandatory)][hashtable] $HostArguments
    )
    if (-not $Enabled) { return [pscustomobject]@{Status='OFF';Output=$null} }
    if ($script:WdQ4SessionCommandUnknown) { return [pscustomobject]@{Status='UNKNOWN';Output=$null} }
    $entered = $false
    $document = $null
    try {
        $required = @('JobHelper','JobHelperSha256','Interpreter','InterpreterSha256','Bootstrap',
                      'BootstrapSha256','BootstrapArguments','WorkingDirectory','ScratchRoot',
                      'TimeoutSeconds','MaxOutputBytes')
        if ($HostArguments.Count -ne $required.Count) { throw 'refused' }
        foreach ($key in $HostArguments.Keys) {
            if ($key -isnot [string] -or $required -cnotcontains $key) { throw 'refused' }
        }
        $allowed = @('lane','bundle-root','public-plan','interpreter','interpreter-sha256','interpreter-max-bytes',
            'cwd','timeout-seconds','max-output-bytes','fleet','fleet-sha256','binding','binding-sha256',
            'fixed-plan','fixed-plan-sha256','snapshot','snapshot-sha256','runtime-manifest',
            'runtime-manifest-sha256','outer-path','outer-sha256','runtime-root','runtime-generation',
            'runtime-revision','verifier-sha256','public-outer-sha256','public-child-sha256',
            'public-query-sha256','worker-sha256','expected-pointer-sha256','expected-source-anchor-sha256',
            'expected-encoder-sha256','expected-policy-sha256','expected-roster-sha256',
            'expected-public-policy-sha256','expected-descriptor-sha256','expected-projection-sha256')
        $arguments = $HostArguments.BootstrapArguments
        if ($arguments -isnot [array] -or $arguments.Count -notin @((1 + 2 * $allowed.Count), (3 + 2 * $allowed.Count)) -or
            $arguments[0] -cne '--enabled') { throw 'refused' }
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        for ($i = 1; $i -lt $arguments.Count; $i += 2) {
            $name = $arguments[$i]; $value = $arguments[$i + 1]
            if ($name -isnot [string] -or -not $name.StartsWith('--') -or
                ($allowed -cnotcontains $name.Substring(2) -and $name -cne '--query-utc') -or -not $seen.Add($name) -or
                $value -isnot [string] -or $value.Length -lt 1 -or $value.Length -gt 32767 -or
                $value.Contains([char]0)) { throw 'refused' }
            if ($name -ceq '--query-utc' -and
                $value -cnotmatch '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.000000Z\z') { throw 'refused' }
        }
        foreach ($name in $allowed) { if (-not $seen.Contains('--' + $name)) { throw 'refused' } }
        if (-not [IO.Path]::IsPathFullyQualified($HostScript) -or
            $HostSha256 -cnotmatch '^[a-f0-9]{64}$' -or
            ($null -ne $script:WdQ4SessionCommandHostPin -and
             $script:WdQ4SessionCommandHostPin -cne $HostSha256)) { throw 'refused' }
        $current = [IO.Path]::GetFullPath($HostScript)
        while ($current) {
            if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'refused' }
            $next = [IO.Path]::GetDirectoryName($current)
            if (-not $next -or $next -ceq $current) { break }
            $current = $next
        }
        $stream = [IO.File]::Open($HostScript,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($stream.Length -lt 1 -or $stream.Length -gt 1048576) { throw 'refused' }
            $buffer = [byte[]]::new(1048577)
            $offset = 0
            while ($offset -lt $buffer.Length) {
                $n = $stream.Read($buffer,$offset,$buffer.Length-$offset)
                if ($n -eq 0) { break }
                $offset += $n
            }
            if ($offset -gt 1048576 -or $offset -ne $stream.Length) { throw 'refused' }
            $hostBytes = [byte[]]::new($offset)
            [array]::Copy($buffer,$hostBytes,$offset)
        } finally { $stream.Dispose() }
        if ([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($hostBytes)).ToLowerInvariant() -cne
            $HostSha256) { throw 'refused' }
        $hostText = [Text.UTF8Encoding]::new($false,$true).GetString($hostBytes).TrimStart([char]0xfeff)
        # Shadow any same-named caller function; the authenticated host bytes
        # must actually replace this local sentinel before they can be invoked.
        function Invoke-WdQ4SessionHost { throw 'unbound' }
        $sentinel = (Get-Item Function:Invoke-WdQ4SessionHost).ScriptBlock
        . ([scriptblock]::Create($hostText))
        $hostCommand = Get-Item Function:Invoke-WdQ4SessionHost -ErrorAction Stop
        if ([object]::ReferenceEquals($sentinel,$hostCommand.ScriptBlock)) { throw 'refused' }
        $script:WdQ4SessionCommandHostPin = $HostSha256
        $inputStream = [Console]::OpenStandardInput()
        $inputBuffer = [byte[]]::new(16001)
        $count = 0
        while ($count -lt $inputBuffer.Length) {
            $n = $inputStream.Read($inputBuffer,$count,$inputBuffer.Length-$count)
            if ($n -eq 0) { break }
            $count += $n
        }
        if ($count -lt 1 -or $count -gt 16000) { throw 'refused' }
        $inputBytes = [byte[]]::new($count)
        [array]::Copy($inputBuffer,$inputBytes,$count)
        $entered = $true
        $answer = & $hostCommand.ScriptBlock -Enabled @HostArguments -InputBytes $inputBytes
        if (@($answer).Count -ne 1 -or $answer -isnot [pscustomobject]) { throw 'refused' }
        if ($answer.Status -ceq 'UNKNOWN') {
            $script:WdQ4SessionCommandUnknown = $true
            return [pscustomobject]@{Status='UNKNOWN';Output=$null}
        }
        if ($answer.Status -ceq 'TIMEOUT') { return [pscustomobject]@{Status='TIMEOUT';Output=$null} }
        if ($answer.Status -ceq 'REFUSED') { return [pscustomobject]@{Status='REFUSED';Output=$null} }
        if ($answer.Status -cne 'OK' -or $answer.Output -isnot [byte[]] -or
            $answer.Output.Length -lt 3 -or $answer.Output.Length -gt $HostArguments.MaxOutputBytes -or
            $answer.Output[-1] -ne 10) { throw 'refused' }
        $text = [Text.UTF8Encoding]::new($false,$true).GetString($answer.Output,0,$answer.Output.Length-1)
        if ($text.Contains("`r") -or $text.Contains("`n")) { throw 'refused' }
        $document = [System.Text.Json.JsonDocument]::Parse([string]$text)
        if ($document.RootElement.ValueKind -ne [System.Text.Json.JsonValueKind]::Object) { throw 'refused' }
        $entered = $false
        return [pscustomobject]@{Status='UNVALIDATED';Output=$answer.Output}
    } catch {
        if ($entered) {
            $script:WdQ4SessionCommandUnknown = $true
            return [pscustomobject]@{Status='UNKNOWN';Output=$null}
        }
        return [pscustomobject]@{Status='REFUSED';Output=$null}
    } finally {
        if ($null -ne $document) { $document.Dispose() }
        $inputBytes = $inputBuffer = $text = $null
    }
}
