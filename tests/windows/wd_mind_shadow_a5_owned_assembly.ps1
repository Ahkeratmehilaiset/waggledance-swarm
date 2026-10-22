#requires -Version 5.1
<#
Internal A5 owned assembly, Source only: dot-sourcing defines functions, direct execution refuses. It runs the
actual New-WdA5OwnedFactorySeams and the actual Invoke-WdA5OwnedParentCore exactly once on ONE admitted Config, from
named function closures extracted out of the verified bytes of the pinned factory and parent (never a dot-source of
unverified files, a global function replacement or a caller-provided seam). The opt-in switch is an accident guard,
never authority: this is NOT the public A5 entry (which stays NOT_READY), not wired into any harness or runtime and
not a native admission. Its receipt keeps the executed Source hashes, the Config context, the factory launch record
and errors and the original Core result; only the Core's OBSERVATIONS_COMPLETE_NOT_A5_PASS is reported, whole A5 PASS
is always false, and anything else is UNKNOWN. No retry, no verdict.
#>
function Read-WdA5AssemblyPinnedText {
    # One explicit full C:\ printable path without reparse ancestry, an uppercase raw SHA-256 pin, bytes read once,
    # strict UTF-8 text with CRLF read as LF (the parent's rule).
    param([string] $Path, [string] $Sha256, [string] $What)
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
    if ($Sha256 -cnotmatch '^[A-F0-9]{64}\z') { throw "refused: the $What pin is not an uppercase SHA-256" }
    if (-not [IO.File]::Exists($Path)) { throw "refused: the pinned $What file is missing" }
    $bytes = [IO.File]::ReadAllBytes($Path)
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($hash.ComputeHash($bytes)).Replace('-', '') } finally { $hash.Dispose() }
    if ($actual -cne $Sha256) { throw "refused: the $What file is not its pinned bytes" }
    $text = ([Text.UTF8Encoding]::new($false, $true)).GetString($bytes).Replace("`r`n", "`n")
    return [ordered]@{ path = $Path; sha256 = $actual; text = $text }
}

function Import-WdA5AssemblyClosure {
    # Exactly the named top-level functions of one verified Source text, into one private module; nothing else runs.
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

function Invoke-WdA5OwnedAssemblyCore {
    # Internal only. Before any write: the opt-in switch, one Config table and the three Source pins (parent,
    # factory, runner); the factory then admits Config, its four file pins, Context, roots and bounds before its
    # first write, and the Core validates again. The factory seams and the Core run exactly once each.
    param([switch] $ApprovedNativeExecution, [hashtable] $Config, [string] $ParentPath,
        [string] $ExpectedParentSha256, [string] $FactoryPath, [string] $ExpectedFactorySha256, [string] $RunnerPath,
        [string] $ExpectedRunnerSha256, [long] $MaxOutputBytes = 32768)
    if (-not $ApprovedNativeExecution) {
        throw 'refused: the internal A5 assembly needs the explicit opt-in (an accident guard, never authority)'
    }
    if ($null -eq $Config) { throw 'refused: one explicit Config table is required' }
    $parent = Read-WdA5AssemblyPinnedText $ParentPath $ExpectedParentSha256 'parent'
    $factory = Read-WdA5AssemblyPinnedText $FactoryPath $ExpectedFactorySha256 'factory'
    $runner = Read-WdA5AssemblyPinnedText $RunnerPath $ExpectedRunnerSha256 'runner'
    $parentModule = Import-WdA5AssemblyClosure $parent.text @('Get-WdA5ParentSha256', 'Read-WdA5ParentPin',
        'Import-WdA5ParentPure', 'Read-WdA5ParentJsonSnapshot', 'Read-WdA5ParentMarker',
        'Read-WdA5ParentReleasedWitness', 'Get-WdA5ParentLiteral', 'Get-WdA5ParentMemberCommand',
        'Get-WdA5ParentControllerCommand', 'Get-WdA5ParentField', 'Assert-WdA5ParentKeys', 'Assert-WdA5ParentString',
        'Assert-WdA5ParentReady', 'Assert-WdA5ParentConfig', 'Invoke-WdA5OwnedParentCore') 'parent'
    $factoryModule = Import-WdA5AssemblyClosure $factory.text @('Read-WdA5FactoryPinnedText', 'Test-WdA5FactoryPath',
        'Import-WdA5FactoryClosure', 'Read-WdA5FactoryOutputSnapshot', 'New-WdA5OwnedFactorySeams') 'factory'
    $record = @{}
    $seams = & $factoryModule {
        param($Given, $Parent, $ParentSha, $Runner, $RunnerSha, $Into, $Bound)
        New-WdA5OwnedFactorySeams -ApprovedNativeExecution -Config $Given -ParentPath $Parent `
            -ExpectedParentSha256 $ParentSha -RunnerPath $Runner -ExpectedRunnerSha256 $RunnerSha -Record $Into `
            -MaxOutputBytes $Bound
    } $Config $ParentPath $ExpectedParentSha256 $RunnerPath $ExpectedRunnerSha256 $record $MaxOutputBytes
    $pure = & $parentModule {
        param($Path, $Sha) Import-WdA5ParentPure (Read-WdA5ParentPin $Path $Sha -Text).text
    } $Config.ControllerPath $Config.Pins.controller_sha256
    $core = $null
    $coreError = $null
    try {
        $core = & $parentModule {
            param($Given, $Module, $Seams) Invoke-WdA5OwnedParentCore -Config $Given -PureModule $Module -Seams $Seams
        } $Config $pure $seams
    } catch { $coreError = [string]$_.Exception.Message }
    $factoryErrors = [string[]]@($record['errors'])
    $status = 'UNKNOWN'
    if ($null -eq $coreError -and $core -is [hashtable] -and [string]$core['status'] -ceq
        'OBSERVATIONS_COMPLETE_NOT_A5_PASS' -and $core['whole_A5_PASS'] -eq $false -and $factoryErrors.Count -eq 0) {
        $status = 'OBSERVATIONS_COMPLETE_NOT_A5_PASS'
    }
    return [ordered]@{
        schema = 'wd.mind-shadow-a5-owned-assembly-receipt.v1'; status = $status; whole_A5_PASS = $false
        native_authority = 'none: internal Source assembly, not the public A5 entry or a native admission'
        sources = [ordered]@{
            parent = [ordered]@{ path = $parent.path; sha256 = $parent.sha256 }
            factory = [ordered]@{ path = $factory.path; sha256 = $factory.sha256 }
            runner = [ordered]@{ path = $runner.path; sha256 = $runner.sha256 }
        }
        config = [ordered]@{ context = $Config.Context; pins = $Config.Pins; host_path = $Config.HostPath
            run_root = $Config.RunRoot; evidence_root = $Config.EvidenceRoot }
        factory = [ordered]@{ launch = $record['launch']; errors = $factoryErrors }
        core = $core; core_error = $coreError
    }
}

if ($MyInvocation.InvocationName -cne '.') {
    [Console]::Error.WriteLine('refused: the internal A5 assembly is Source only; dot-source it, never run it')
    exit 3
}
