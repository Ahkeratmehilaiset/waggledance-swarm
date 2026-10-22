#requires -Version 5.1
<#
Future opt-in A5 fixed owned-factory provider, Source only: dot-sourcing defines functions, direct execution refuses.
It supplies Invoke-WdA5OwnedParentCore's seven seams (NewOwned, Identity, NowUtc, Ready, Marker, Errors, Released)
for ONE admitted Config: the frozen parent and the Root runner are read once by explicit C:\ path and raw pin, and
only their named fixed functions are imported into private modules. The controller command is the frozen parent's
Get-WdA5ParentControllerCommand output, never rebuilt here. The opt-in switch is an accident guard, never authority.
Bounded synchronous reads only: no OS preemption, no timeout guarantee; the Core owns all timing and order.
#>
function Read-WdA5FactoryPinnedText {
    # One explicit full C:\ printable path, no reparse ancestry, raw bytes read once and pinned (uppercase SHA-256),
    # strict UTF-8 text.
    param([string] $Path, [string] $Sha256, [string] $What)
    Test-WdA5FactoryPath $Path $What
    if ($Sha256 -cnotmatch '^[A-F0-9]{64}\z') { throw "refused: the $What pin is not an uppercase SHA-256" }
    if (-not [IO.File]::Exists($Path)) { throw "refused: the pinned $What file is missing" }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-', '') } finally { $hash.Dispose() }
    if ($actual -cne $Sha256) { throw "refused: the $What file is not its pinned bytes" }
    return ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes).Replace("`r`n", "`n")
}

function Test-WdA5FactoryPath {
    param([string] $Path, [string] $What)
    if ($Path -cnotmatch '^C:\\[\x20-\x7E]+\z' -or $Path.Contains('"') -or $Path.EndsWith('\') -or
        [IO.Path]::GetFullPath($Path) -cne $Path) { throw "refused: the $What path is not a full printable C:\ path" }
    $current = $Path
    while ($current.Length -gt 3) {
        if (([IO.File]::Exists($current) -or [IO.Directory]::Exists($current)) -and
            ([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint)) {
            throw "refused: the $What path has reparse ancestry"
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Import-WdA5FactoryClosure {
    # Exactly the named top-level functions of a pinned Source text, into one private module; nothing else runs.
    param([string] $Text, [string[]] $Names, [string] $What)
    $tokens = $null; $errors = $null
    $ast = [Management.Automation.Language.Parser]::ParseInput($Text, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "refused: the pinned $What Source does not parse" }
    $parts = @()
    foreach ($name in $Names) {
        $found = @($ast.EndBlock.Statements | Where-Object {
                $_ -is [Management.Automation.Language.FunctionDefinitionAst] -and $_.Name -ceq $name })
        if ($found.Count -ne 1) { throw "refused: the pinned $What Source lacks exactly one $name" }
        $parts += $found[0].Extent.Text
    }
    return New-Module -ScriptBlock ([scriptblock]::Create($parts -join "`n"))
}

function Read-WdA5FactoryOutputSnapshot {
    # One bounded strict immutable snapshot of a fixed synthetic output file: no writer may hold it (FileShare.Read),
    # at most MaxBytes, read exactly once to its length, strict UTF-8. Missing, held, oversized, partial, invalid or
    # reparse files throw; never an inherited-pipe ReadToEnd, EOF wait or Task.Wait.
    param([string] $Path, [long] $MaxBytes)
    try { $attributes = [IO.File]::GetAttributes($Path) }
    catch [IO.FileNotFoundException] { throw 'refused: the output snapshot file is missing' }
    if ($attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'refused: the output snapshot file is foreign' }
    try { $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read) }
    catch [IO.IOException] {
        if (($_.Exception.GetBaseException().HResult -band 65535) -in @(32, 33)) {
            throw 'refused: the output snapshot file is still held'
        }
        throw
    }
    try {
        if ($stream.Length -gt $MaxBytes) { throw 'refused: the output snapshot is over its bound' }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $read = $stream.Read($bytes, $offset, $bytes.Length - $offset)
            if ($read -le 0) { throw 'refused: the output snapshot is partial' }
            $offset += $read
        }
    } finally { $stream.Dispose() }
    try { return ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes) }
    catch { throw 'refused: the output snapshot is not valid UTF-8' }
}

function New-WdA5OwnedFactorySeams {
    # The seven Core seams for ONE admitted Config. Before any write: the opt-in switch, one empty Record, the output
    # bound (1 to 32768, the Core's own limit), the frozen parent and Root runner pins and their fixed function
    # closures, the parent's own Config check, the four file pins (host, loop, member, controller; the controller's
    # canonical identity through the parent's pure import), the Context identity, local C: NTFS with 3 GiB free,
    # EvidenceRoot an existing folder and RunRoot a folder directly in a .codex-audit folder, a fresh run directory
    # and absent fixed output files. NewOwned launches ONCE (never retried) through Root's initializer and six-argument
    # owned constructor with the admitted host and the frozen parent's literal controller command.
    param([switch] $ApprovedNativeExecution, [hashtable] $Config, [string] $ParentPath, [string] $ExpectedParentSha256,
        [string] $RunnerPath, [string] $ExpectedRunnerSha256, [hashtable] $Record, [long] $MaxOutputBytes = 32768)
    if (-not $ApprovedNativeExecution) {
        throw 'refused: the A5 owned factory needs the explicit opt-in (an accident guard, never authority)'
    }
    if ($Record -isnot [hashtable] -or $Record.Count -ne 0) {
        throw 'refused: the factory record is not one empty table'
    }
    if ($MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 32768) {
        throw 'refused: the output bound is not 1 to 32768 bytes'
    }
    $parentText = Read-WdA5FactoryPinnedText $ParentPath $ExpectedParentSha256 'parent'
    $runnerText = Read-WdA5FactoryPinnedText $RunnerPath $ExpectedRunnerSha256 'runner'
    $parent = Import-WdA5FactoryClosure $parentText @('Get-WdA5ParentSha256', 'Read-WdA5ParentPin',
        'Import-WdA5ParentPure', 'Read-WdA5ParentJsonSnapshot', 'Read-WdA5ParentMarker',
        'Read-WdA5ParentReleasedWitness', 'Get-WdA5ParentLiteral', 'Get-WdA5ParentControllerCommand',
        'Assert-WdA5ParentKeys', 'Assert-WdA5ParentConfig') 'parent'
    $runner = Import-WdA5FactoryClosure $runnerText @('Get-WdA7Sha256', 'Initialize-WdA7OwnedType',
        'New-WdA7ManagedProcess', 'Get-WdA7ObservedIdentity') 'runner'
    & $parent { param($c) Assert-WdA5ParentConfig $c } $Config
    foreach ($key in @('HostPath', 'LoopPath', 'MemberPath', 'ControllerPath', 'RunRoot', 'EvidenceRoot')) {
        Test-WdA5FactoryPath $Config[$key] $key
    }
    $pins = $Config.Pins
    $loopText = [string](& $parent {
        param($p, $s) (Read-WdA5ParentPin $p $s -Text).text
    } $Config.LoopPath $pins.loop_sha256)
    [void](& $parent { param($p, $s) Read-WdA5ParentPin $p $s } $Config.HostPath $pins.host_sha256)
    [void](& $parent { param($p, $s) Read-WdA5ParentPin $p $s -Text } $Config.MemberPath $pins.member_sha256)
    $pure = & $parent {
        param($p, $s) Import-WdA5ParentPure (Read-WdA5ParentPin $p $s -Text).text
    } $Config.ControllerPath $pins.controller_sha256
    & $pure { param($context) Assert-WdA5Context $context } $Config.Context
    $drive = [IO.DriveInfo]::new('C')
    if ($drive.DriveFormat -cne 'NTFS') { throw 'refused: C: is not NTFS' }
    if ($drive.AvailableFreeSpace -lt 3221225472) { throw 'refused: C: free space is below 3 GiB' }
    foreach ($key in @('RunRoot', 'EvidenceRoot')) {
        if ([IO.Path]::GetFileName([IO.Path]::GetDirectoryName($Config[$key])) -cne '.codex-audit') {
            throw "refused: $key is not a folder directly in a .codex-audit folder"
        }
    }
    if (-not [IO.Directory]::Exists($Config.EvidenceRoot)) { throw 'refused: EvidenceRoot is not an existing folder' }
    $directory = $Config.RunRoot + '\' + $Config.Context.launch_id
    if ([IO.Directory]::Exists($directory) -or [IO.File]::Exists($directory)) {
        throw 'refused: the run directory is not fresh'
    }
    $evidence = $Config.EvidenceRoot
    $inputPath = $evidence + '\a5-controller.input'
    $outPath = $evidence + '\a5-controller.stdout.log'
    $errPath = $evidence + '\a5-controller.stderr.log'
    foreach ($path in @($inputPath, $outPath, $errPath)) {
        if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
            throw 'refused: a fixed output path already exists (never overwritten)'
        }
    }
    $command = [string](& $parent { param($c) Get-WdA5ParentControllerCommand $c } $Config)
    $argv = [string[]]@('-NoProfile', '-NonInteractive', '-Command', $command)
    $hostPath = $Config.HostPath
    $config = $Config
    $Record['launch'] = [ordered]@{ host_path = $hostPath; argv = $argv; calls = 0; acquired = $false; pid = $null }
    $Record['errors'] = New-Object Collections.Generic.List[string]
    $launch = $Record['launch']
    $errors = $Record['errors']
    $fixed = @{
        ready = $directory + '\ready.json'; marker = $directory + '\cleanup-started.json'
        witnesses = [string[]]@('root', 'child', 'grandchild', 'controller' | ForEach-Object {
                $directory + '\' + $_ + '.witness' })
    }
    $snapshot = ${function:Read-WdA5FactoryOutputSnapshot}
    $seams = @{}
    $seams['NewOwned'] = {
        param($Given, $GivenCommand)
        if (-not [object]::ReferenceEquals($Given, $config) -or $GivenCommand -isnot [string] -or
            $GivenCommand -cne $command) { throw 'refused: not the admitted Config and frozen parent command' }
        if ([int]$launch['calls'] -ne 0) { throw 'refused: one owned launch only, never retried' }
        $launch['calls'] = 1
        foreach ($path in @($inputPath, $outPath, $errPath)) {
            if ([IO.File]::Exists($path) -or [IO.Directory]::Exists($path)) {
                throw 'refused: a fixed output path already exists (never overwritten)'
            }
        }
        $stream = New-Object IO.FileStream($inputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
            [IO.FileShare]::None)
        $stream.Dispose()
        try {
            $inner = & $runner {
                param($Text, $HostPath, $Arguments, $Directory, $InputPath, $OutputPath, $ErrorPath)
                Initialize-WdA7OwnedType $Text
                New-WdA7ManagedProcess $HostPath $Arguments $Directory $InputPath $OutputPath $ErrorPath
            } $loopText $hostPath $argv $evidence $inputPath $outPath $errPath
        } catch {
            # From the loop constructor's code (not observed): on a failed construction it terminates and disposes
            # what it created before throwing; nothing reached this factory, so nothing is cleaned here.
            $errors.Add('launch: ' + $_.Exception.Message)
            throw
        }
        $launch['acquired'] = $true
        try {
            if ($inner.Pid -isnot [int] -or $inner.Pid -le 0) { throw 'the owned object has no positive Pid' }
            foreach ($name in @('Wait', 'Stop', 'StopRootOnly', 'Dispose')) {
                if ($null -eq $inner.PSObject.Methods[$name]) { throw ('the owned object lacks ' + $name) }
            }
            foreach ($name in @('ActiveProcesses', 'TotalProcesses', 'ExitCode')) {
                if ($null -eq $inner.PSObject.Properties[$name]) { throw ('the owned object lacks ' + $name) }
            }
            $launch['pid'] = [string]$inner.Pid
        } catch {
            # Acquired but not handed over: ONE owned Stop and ONE Dispose, every error kept, the original rethrown.
            $primary = $_
            $errors.Add('setup: ' + $primary.Exception.Message)
            try { $inner.Stop() } catch { $errors.Add('cleanup stop: ' + $_.Exception.Message) }
            try { $inner.Dispose() } catch { $errors.Add('cleanup dispose: ' + $_.Exception.Message) }
            throw $primary
        }
        return $inner
    }.GetNewClosure()
    $seams['Identity'] = {
        param($Id)
        return (& $runner { param($i) Get-WdA7ObservedIdentity $i } ([int]$Id))
    }.GetNewClosure()
    $seams['NowUtc'] = {
        return [DateTime]::UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fffffff'Z'",
            [Globalization.CultureInfo]::InvariantCulture)
    }
    $seams['Ready'] = {
        param($Path)
        if ($Path -isnot [string] -or $Path -cne $fixed['ready']) { throw 'refused: not the fixed ready path' }
        return (& $parent { param($p) Read-WdA5ParentJsonSnapshot $p } $Path)
    }.GetNewClosure()
    $seams['Marker'] = {
        param($Path)
        if ($Path -isnot [string] -or $Path -cne $fixed['marker']) { throw 'refused: not the fixed marker path' }
        return (& $parent { param($p) Read-WdA5ParentMarker $p } $Path)
    }.GetNewClosure()
    $seams['Released'] = {
        param($Path, $Line)
        if ($Path -isnot [string] -or $fixed['witnesses'] -cnotcontains $Path) {
            throw 'refused: not a fixed witness path'
        }
        return (& $parent { param($p) Read-WdA5ParentReleasedWitness $p } $Path)
    }.GetNewClosure()
    $seams['Errors'] = {
        return @{ stdout = (& $snapshot $outPath $MaxOutputBytes); stderr = (& $snapshot $errPath $MaxOutputBytes) }
    }.GetNewClosure()
    return $seams
}

if ($MyInvocation.InvocationName -cne '.') {
    [Console]::Error.WriteLine('refused: the A5 owned factory is Source only; dot-source it, never run it')
    exit 3
}
