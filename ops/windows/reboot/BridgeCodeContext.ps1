#requires -Version 5.1
<#
.SYNOPSIS
    Pinned bridge communication-code context for WaggleDance reboot lanes.

.DESCRIPTION
    Dot-source this file; it only defines functions and constants. The
    installer uses Install-WdBridgePythonSite to stage the hash-pinned Python
    dependency closure inside a commit-addressed reboot bundle. The lane and
    Tools launchers use Initialize-WdBridgeCodeContext to verify that package
    against the externally anchored deployment manifest and to export ONLY the
    WD_BRIDGE_* discovery variables to the process their CLI children inherit.
    Invoke-WdBridgePython.ps1 uses Invoke-WdBridgePythonTool to run one
    packaged tool under scoped Python isolation (-S -B plus PYTHONPATH,
    PYTHONSAFEPATH, PYTHONNOUSERSITE, PYTHONDONTWRITEBYTECODE) and restores
    the process environment afterwards, so ordinary task-worktree Python,
    tests and development are never affected.

    Three roots stay separate: the task worktree remains the Git cwd, the
    bridge runtime root remains the data root (shared/, work_queue/, spool/),
    and the pinned code root (<bundle>\tools-bootstrap) is code only. Nothing
    here mutates Git, bridge data, scheduled tasks or authority. There is no
    fallback to unpinned local helpers or to global/user site-packages: a
    bundle without the package, a hash mismatch, an unlisted file or a
    bytecode cache fails closed.
#>

$script:WdBridgeCodePackageSchema = 'wd.bridge-code-package.v1'
$script:WdBridgeCodeContextSchema = 'wd.bridge-code-context.v1'
$script:WdBridgeCodePackageRoot = 'tools-bootstrap'
$script:WdBridgeCodeWrapperName = 'Invoke-WdBridgePython.ps1'
$script:WdBridgeCodeDefinitionName = 'bridge-code-files.json'
$script:WdBridgeCodePythonFlags = @('-S', '-B')
# Invoke-WdBridgePythonTool streams the packaged tool's own output to the
# caller, so its exit code travels here instead of on the output stream.
$script:WdBridgeCodeLastExitCode = $null
$script:WdBridgeCodeIsolationKeys = @(
    'PYTHONPATH',
    'PYTHONHOME',
    'PYTHONSTARTUP',
    'PYTHONDONTWRITEBYTECODE',
    'PYTHONNOUSERSITE',
    'PYTHONSAFEPATH'
)

function Get-WdBridgeCodeFileSha256 {
    param([Parameter(Mandatory)] [string] $Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Assert-WdBridgeCodePathWithoutReparse {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $TrustedRoot,
        [ValidateSet('Directory', 'Leaf')]
        [string] $ExpectedType = 'Leaf'
    )

    $candidate = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath($TrustedRoot)
    $rootTrimmed = $root.TrimEnd('\')
    $rootPrefix = $rootTrimmed + '\'
    if (
        -not $candidate.Equals($rootTrimmed, [StringComparison]::OrdinalIgnoreCase) -and
        -not $candidate.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "pinned bridge code path escaped its trusted root: $candidate"
    }
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "pinned bridge code trusted root cannot be a reparse point: $root"
    }
    $relative = if ($candidate.Equals($rootTrimmed, [StringComparison]::OrdinalIgnoreCase)) {
        ''
    } else {
        $candidate.Substring($rootPrefix.Length)
    }
    $current = $root
    foreach ($segment in @($relative -split '[\\/]')) {
        if (-not $segment) { continue }
        $current = Join-Path $current $segment
        if (-not (Test-Path -LiteralPath $current)) {
            throw "pinned bridge code path component is missing: $current"
        }
        $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "pinned bridge code path component cannot be a reparse point: $current"
        }
    }
    if (
        $ExpectedType -ceq 'Directory' -and
        -not (Test-Path -LiteralPath $candidate -PathType Container)
    ) {
        throw "pinned bridge code path is not a directory: $candidate"
    }
    if (
        $ExpectedType -ceq 'Leaf' -and
        -not (Test-Path -LiteralPath $candidate -PathType Leaf)
    ) {
        throw "pinned bridge code path is not a file: $candidate"
    }
    return $candidate
}

function Test-WdBridgeCodeRelativePath {
    param([AllowEmptyString()] [string] $Relative)

    if ([string]::IsNullOrWhiteSpace($Relative)) { return $false }
    if ([IO.Path]::IsPathRooted($Relative)) { return $false }
    if ($Relative.Contains('\')) { return $false }
    if ($Relative.StartsWith('/') -or $Relative.EndsWith('/') -or $Relative.Contains('//')) {
        return $false
    }
    foreach ($segment in $Relative.Split('/')) {
        if ($segment -ceq '.' -or $segment -ceq '..') { return $false }
        if ($segment -cnotmatch '^[A-Za-z0-9_.+-]+$') { return $false }
    }
    return $true
}

function ConvertTo-WdBridgeCodeArgument {
    param([AllowEmptyString()] [string] $Value)

    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') { return $Value }
    # Windows CRT quoting: only double backslashes before a quote or the
    # closing delimiter. Ordinary path separators must remain unchanged.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Read-WdBridgeCodeJsonSnapshot {
    param([Parameter(Mandatory)] [string] $Path)

    $fullPath = [IO.Path]::GetFullPath($Path)
    $bytes = [IO.File]::ReadAllBytes($fullPath)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        $text = $text.Substring(1)
    }
    return [pscustomobject]@{
        Path = $fullPath
        Hash = $hash
        Object = ($text | ConvertFrom-Json -ErrorAction Stop)
    }
}

function Invoke-WdBridgeCodePython {
    param(
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $Label,
        [hashtable] $Environment = @{},
        [string] $WorkingDirectory = '',
        [int] $TimeoutSeconds = 600
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $PythonExecutable
    $startInfo.Arguments = (@($Arguments | ForEach-Object {
        ConvertTo-WdBridgeCodeArgument -Value ([string]$_)
    }) -join ' ')
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.CreateNoWindow = $true
    $startInfo.WorkingDirectory = if ($WorkingDirectory) {
        $WorkingDirectory
    } else {
        [IO.Path]::GetTempPath()
    }
    foreach ($key in @($Environment.Keys)) {
        $value = [string]$Environment[$key]
        if ([string]::IsNullOrEmpty($value)) {
            [void]$startInfo.EnvironmentVariables.Remove([string]$key)
        }
        else {
            $startInfo.EnvironmentVariables[[string]$key] = $value
        }
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    try {
        [void]$process.Start()
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch { }
            throw "$Label timed out after $TimeoutSeconds seconds"
        }
        $process.WaitForExit()
        $stdout = [string]$stdoutTask.Result
        $stderr = [string]$stderrTask.Result
        $exitCode = [int]$process.ExitCode
    }
    finally {
        $process.Dispose()
    }
    if ($exitCode -ne 0) {
        $tail = (@(($stdout + "`n" + $stderr) -split "`r?`n" | Where-Object { $_ } | Select-Object -Last 12) -join ' | ')
        throw "$Label failed with exit code ${exitCode}: $tail"
    }
    return [pscustomobject]@{ ExitCode = $exitCode; StdOut = $stdout; StdErr = $stderr }
}

function Get-WdBridgeCodePackageDefinition {
    param([Parameter(Mandatory)] [string] $Path)

    $snapshot = Read-WdBridgeCodeJsonSnapshot -Path $Path
    $definition = $snapshot.Object
    if ([string]$definition.schema -cne $script:WdBridgeCodePackageSchema) {
        throw "unsupported bridge code package schema: $($definition.schema)"
    }
    if ([string]$definition.package_relative_root -cne $script:WdBridgeCodePackageRoot) {
        throw "bridge code package root must be $script:WdBridgeCodePackageRoot"
    }
    if ([string]$definition.invocation_wrapper_relative -cne $script:WdBridgeCodeWrapperName) {
        throw "bridge code package wrapper must be $script:WdBridgeCodeWrapperName"
    }
    $files = @($definition.python_files | ForEach-Object { [string]$_ })
    if ($files.Count -lt 1) {
        throw 'bridge code package lists no python files'
    }
    $seen = @{}
    foreach ($relative in $files) {
        if (-not (Test-WdBridgeCodeRelativePath -Relative $relative)) {
            throw "unsafe bridge code package path: $relative"
        }
        if ($relative -cnotmatch '\.(py|json)$') {
            throw "bridge code package file must be .py or .json: $relative"
        }
        if ($seen.ContainsKey($relative)) {
            throw "duplicate bridge code package path: $relative"
        }
        $seen[$relative] = $true
    }
    if ($null -eq $definition.python_entrypoints) {
        throw 'bridge code package lists no python entrypoints'
    }
    $entrypointCount = 0
    foreach ($property in @($definition.python_entrypoints.PSObject.Properties)) {
        $entrypoint = [string]$property.Value
        if (-not $seen.ContainsKey($entrypoint) -or $entrypoint -cnotmatch '\.py$') {
            throw "bridge code entrypoint is not a packaged python file: $entrypoint"
        }
        $entrypointCount++
    }
    if ($entrypointCount -lt 1) {
        throw 'bridge code package lists no python entrypoints'
    }
    $requirements = @($definition.python_requirements)
    if ($requirements.Count -lt 1) {
        throw 'bridge code package lists no python requirements'
    }
    $wheels = @{}
    foreach ($requirement in $requirements) {
        $name = [string]$requirement.name
        $version = [string]$requirement.version
        $wheel = [string]$requirement.wheel
        $sha256 = [string]$requirement.sha256
        $importPath = [string]$requirement.import_path
        if ($name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
            throw "unsafe requirement name: $name"
        }
        if ($version -cnotmatch '^[0-9][A-Za-z0-9.+!-]*$') {
            throw "unsafe requirement version for ${name}: $version"
        }
        if (
            $wheel -cnotmatch '^[A-Za-z0-9_.+!-]+\.whl$' -or
            $wheel.Contains('/') -or
            $wheel.Contains('\')
        ) {
            throw "unsafe wheel file name for ${name}: $wheel"
        }
        $normalizedName = ($name -replace '[-_.]+', '_')
        if (-not $wheel.StartsWith(
                ($normalizedName + '-' + $version + '-'),
                [StringComparison]::OrdinalIgnoreCase
            )) {
            throw "wheel file name does not match ${name}==${version}: $wheel"
        }
        if ($sha256 -cnotmatch '^[0-9A-Fa-f]{64}$') {
            throw "requirement $name has no sha256 pin"
        }
        if (-not (Test-WdBridgeCodeRelativePath -Relative $importPath)) {
            throw "unsafe import path for ${name}: $importPath"
        }
        $wheelKey = $wheel.ToLowerInvariant()
        if ($wheels.ContainsKey($wheelKey)) {
            throw "duplicate wheel: $wheel"
        }
        $wheels[$wheelKey] = $true
    }
    foreach ($directoryName in @('wheel_store_relative', 'python_site_relative')) {
        $value = [string]$definition.$directoryName
        if ($value -cnotmatch '^[A-Za-z0-9_-]+$') {
            throw "unsafe bridge code package directory name for ${directoryName}: $value"
        }
        if ($value -cin @('tools', 'waggledance', 'configs', 'bin')) {
            throw "bridge code package directory name is reserved: $value"
        }
    }
    if ([string]$definition.wheel_store_relative -ceq [string]$definition.python_site_relative) {
        throw 'wheel store and python site must differ'
    }
    $smoke = $definition.import_smoke
    if (
        $null -eq $smoke -or
        [string]$smoke.third_party_module -cnotmatch '^[A-Za-z_][A-Za-z0-9_]*$'
    ) {
        throw 'bridge code package import smoke is missing or unsafe'
    }
    $packageModules = @($smoke.package_modules | ForEach-Object { [string]$_ })
    if ($packageModules.Count -lt 1) {
        throw 'bridge code package import smoke lists no package modules'
    }
    foreach ($packageModule in $packageModules) {
        if ($packageModule -cnotmatch '^(tools|waggledance)(\.[A-Za-z_][A-Za-z0-9_]*)+$') {
            throw "unsafe bridge code package smoke module: $packageModule"
        }
        $modulePath = $packageModule.Replace('.', '/') + '.py'
        if (-not $seen.ContainsKey($modulePath)) {
            throw "bridge code package smoke module is not packaged: $packageModule"
        }
    }
    $isolation = $definition.isolation_environment
    if ($null -eq $isolation) {
        throw 'bridge code package isolation environment block is missing'
    }
    foreach ($property in @($isolation.PSObject.Properties)) {
        if (
            [string]$property.Name -cnotmatch '^PYTHON[A-Z0-9_]*$' -or
            [string]::IsNullOrWhiteSpace([string]$property.Value)
        ) {
            throw "unsafe bridge code package isolation entry: $($property.Name)"
        }
        if ([string]$property.Name -cin @('PYTHONPATH', 'PYTHONHOME')) {
            throw "bridge code package isolation entry is computed, not declared: $($property.Name)"
        }
    }
    foreach ($requiredKey in @('PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'PYTHONSAFEPATH')) {
        if ([string]$isolation.$requiredKey -cne '1') {
            throw "bridge code package must set ${requiredKey}=1"
        }
    }
    return [pscustomobject]@{
        Path = $snapshot.Path
        Hash = $snapshot.Hash
        Definition = $definition
    }
}

function Resolve-WdBridgePythonExecutable {
    param([Parameter(Mandatory)] [string] $ConfiguredPath)

    if (
        [string]::IsNullOrWhiteSpace($ConfiguredPath) -or
        -not [IO.Path]::IsPathRooted($ConfiguredPath)
    ) {
        throw 'bridge Python executable path must be absolute'
    }
    $full = [IO.Path]::GetFullPath($ConfiguredPath)
    if ([IO.Path]::GetExtension($full) -cne '.exe') {
        throw 'bridge Python executable must be an .exe application'
    }
    $localProgramsRoot = [IO.Path]::GetFullPath((Join-Path (
        [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    ) 'Programs\Python')).TrimEnd('\')
    if (-not $full.StartsWith($localProgramsRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "bridge Python is outside the trusted per-user Python root: $full"
    }
    foreach ($windowsApps in @(
            (Join-Path (
                [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)
            ) 'WindowsApps'),
            (Join-Path (
                [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
            ) 'Microsoft\WindowsApps')
        )) {
        $windowsAppsFull = [IO.Path]::GetFullPath($windowsApps).TrimEnd('\')
        if ($full.StartsWith($windowsAppsFull + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "bridge Python resolves inside WindowsApps and is not launchable: $full"
        }
    }
    [void](Assert-WdBridgeCodePathWithoutReparse `
        -Path $full `
        -TrustedRoot ([IO.Path]::GetPathRoot($full)) `
        -ExpectedType Leaf)
    $application = Get-Command -Name $full -CommandType Application -ErrorAction Stop
    if (-not [IO.Path]::GetFullPath([string]$application.Source).Equals(
            $full,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        throw 'bridge Python command is not the configured application'
    }
    return [pscustomobject]@{
        Path = $full
        Sha256 = (Get-WdBridgeCodeFileSha256 -Path $full)
    }
}

function Get-WdBridgeCodePackagePrefixes {
    param([Parameter(Mandatory)] $Definition)

    return @(
        'tools/',
        'waggledance/',
        'configs/',
        ([string]$Definition.wheel_store_relative + '/'),
        ([string]$Definition.python_site_relative + '/')
    )
}

function Get-WdBridgeCodePackageManifestEntries {
    param(
        [Parameter(Mandatory)] $Deployment,
        [Parameter(Mandatory)] $Definition
    )

    $prefixes = @(Get-WdBridgeCodePackagePrefixes -Definition $Definition)
    $rootPrefix = $script:WdBridgeCodePackageRoot + '/'
    $entries = [ordered]@{}
    foreach ($property in @($Deployment.files.PSObject.Properties)) {
        $name = [string]$property.Name
        if (-not $name.StartsWith($rootPrefix, [StringComparison]::Ordinal)) { continue }
        $inner = $name.Substring($rootPrefix.Length)
        $matched = $false
        foreach ($prefix in $prefixes) {
            if ($inner.StartsWith($prefix, [StringComparison]::Ordinal)) {
                $matched = $true
                break
            }
        }
        if (-not $matched) { continue }
        if (-not (Test-WdBridgeCodeRelativePath -Relative $inner)) {
            throw "unsafe pinned bridge code manifest path: $name"
        }
        $value = [string]$property.Value
        if ($value -cnotmatch '^[0-9A-Fa-f]{64}$') {
            throw "pinned bridge code manifest hash is malformed: $name"
        }
        $entries[$inner] = $value.ToUpperInvariant()
    }
    return $entries
}

function Assert-WdBridgeCodeManifestFile {
    param(
        [Parameter(Mandatory)] $Deployment,
        [Parameter(Mandatory)] [string] $RelativeName,
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [string] $TrustedRoot
    )

    $property = $Deployment.files.PSObject.Properties[$RelativeName]
    if ($null -eq $property -or [string]$property.Value -cnotmatch '^[0-9A-Fa-f]{64}$') {
        throw "pinned bridge code input is not covered by the anchored bundle: $RelativeName"
    }
    [void](Assert-WdBridgeCodePathWithoutReparse `
        -Path $Path `
        -TrustedRoot $TrustedRoot `
        -ExpectedType Leaf)
    if ((Get-WdBridgeCodeFileSha256 -Path $Path) -cne ([string]$property.Value).ToUpperInvariant()) {
        throw "pinned bridge code input hash mismatch: $RelativeName"
    }
    return [IO.Path]::GetFullPath($Path)
}

function Assert-WdBridgeCodePackageIntegrity {
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] $Deployment,
        [Parameter(Mandatory)] $Definition
    )

    $bundleFull = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
    $trustedRoot = [IO.Path]::GetPathRoot($bundleFull)
    $codeRoot = Join-Path $bundleFull $script:WdBridgeCodePackageRoot
    [void](Assert-WdBridgeCodePathWithoutReparse `
        -Path $codeRoot `
        -TrustedRoot $trustedRoot `
        -ExpectedType Directory)
    [void](Assert-WdBridgeCodeManifestFile `
        -Deployment $Deployment `
        -RelativeName $script:WdBridgeCodeWrapperName `
        -Path (Join-Path $bundleFull $script:WdBridgeCodeWrapperName) `
        -TrustedRoot $trustedRoot)
    $entries = Get-WdBridgeCodePackageManifestEntries `
        -Deployment $Deployment `
        -Definition $Definition
    if ($entries.Count -lt 1) {
        throw 'deployed bundle carries no pinned bridge code package; refusing unpinned local helpers'
    }
    foreach ($relative in @($Definition.python_files | ForEach-Object { [string]$_ })) {
        if (-not $entries.Contains($relative)) {
            throw "deployed bundle does not carry pinned bridge code file: $relative"
        }
    }
    $wheelStore = [string]$Definition.wheel_store_relative
    $site = [string]$Definition.python_site_relative
    $wheelCount = 0
    foreach ($requirement in @($Definition.python_requirements)) {
        $wheelRelative = $wheelStore + '/' + [string]$requirement.wheel
        if (-not $entries.Contains($wheelRelative)) {
            throw "deployed bundle does not carry pinned wheel: $wheelRelative"
        }
        if ([string]$entries[$wheelRelative] -cne ([string]$requirement.sha256).ToUpperInvariant()) {
            throw "pinned wheel hash differs from the package definition: $wheelRelative"
        }
        $importRelative = $site + '/' + [string]$requirement.import_path
        if (-not $entries.Contains($importRelative)) {
            throw "deployed bundle python site lacks the import path for $($requirement.name): $importRelative"
        }
        $wheelCount++
    }
    $siteCount = 0
    foreach ($relative in @($entries.Keys)) {
        $candidate = Join-Path $codeRoot ([string]$relative).Replace('/', '\')
        [void](Assert-WdBridgeCodePathWithoutReparse `
            -Path $candidate `
            -TrustedRoot $trustedRoot `
            -ExpectedType Leaf)
        if ((Get-WdBridgeCodeFileSha256 -Path $candidate) -cne [string]$entries[$relative]) {
            throw "pinned bridge code hash mismatch: $relative"
        }
        if (([string]$relative).StartsWith($site + '/', [StringComparison]::Ordinal)) {
            $siteCount++
        }
    }
    $codePrefix = $codeRoot.TrimEnd('\') + '\'
    foreach ($prefix in @(Get-WdBridgeCodePackagePrefixes -Definition $Definition)) {
        $directory = Join-Path $codeRoot $prefix.TrimEnd('/')
        if (-not (Test-Path -LiteralPath $directory -PathType Container)) { continue }
        foreach ($subdirectory in @(
                Get-ChildItem -LiteralPath $directory -Recurse -Directory -Force -ErrorAction Stop
            )) {
            if ($subdirectory.Name -ceq '__pycache__') {
                throw "bytecode cache inside pinned bridge code package: $($subdirectory.FullName)"
            }
            if (($subdirectory.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "reparse point inside pinned bridge code package: $($subdirectory.FullName)"
            }
        }
        foreach ($file in @(
                Get-ChildItem -LiteralPath $directory -Recurse -File -Force -ErrorAction Stop
            )) {
            $fullName = [IO.Path]::GetFullPath($file.FullName)
            if (-not $fullName.StartsWith($codePrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw "pinned bridge code enumeration escaped the code root: $fullName"
            }
            $relative = $fullName.Substring($codePrefix.Length).Replace('\', '/')
            if ($relative -cmatch '\.py[co]$') {
                throw "compiled bytecode inside pinned bridge code package: $relative"
            }
            if (-not $entries.Contains($relative)) {
                throw "unexpected file inside pinned bridge code package: $relative"
            }
        }
    }
    return [pscustomobject]@{
        CodeRoot = $codeRoot
        BridgeBin = (Join-Path $codeRoot '.agent-bridge\bin')
        Wrapper = (Join-Path $bundleFull $script:WdBridgeCodeWrapperName)
        FileCount = $entries.Count
        WheelCount = $wheelCount
        SiteFileCount = $siteCount
    }
}

function Get-WdBridgeCodeDiscoveryEnvironment {
    param(
        [Parameter(Mandatory)] $Definition,
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string] $PythonSha256,
        [Parameter(Mandatory)] [string] $Generation,
        [Parameter(Mandatory)] [string] $RuntimeRoot
    )

    $bundleFull = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
    $codeFull = Join-Path $bundleFull $script:WdBridgeCodePackageRoot
    return [ordered]@{
        WD_BRIDGE_BUNDLE_ROOT = $bundleFull
        WD_BRIDGE_CODE_ROOT = $codeFull
        WD_BRIDGE_BIN = (Join-Path $codeFull '.agent-bridge\bin')
        WD_BRIDGE_PYTHON = $PythonExecutable
        WD_BRIDGE_PYTHON_SHA256 = $PythonSha256
        WD_BRIDGE_PYTHON_SITE = (Join-Path $codeFull ([string]$Definition.python_site_relative))
        WD_BRIDGE_PYTHON_WRAPPER = (Join-Path $bundleFull $script:WdBridgeCodeWrapperName)
        WD_BRIDGE_GENERATION = $Generation
        WD_BRIDGE_PACKAGE_SCHEMA = [string]$Definition.schema
        WD_BRIDGE_RUNTIME_ROOT = ([IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\'))
    }
}

function Get-WdBridgeCodeIsolationEnvironment {
    param(
        [Parameter(Mandatory)] $Definition,
        [Parameter(Mandatory)] [string] $CodeRoot
    )

    $codeFull = [IO.Path]::GetFullPath($CodeRoot).TrimEnd('\')
    $site = Join-Path $codeFull ([string]$Definition.python_site_relative)
    $map = [ordered]@{
        PYTHONPATH = ($codeFull + ';' + $site)
        PYTHONHOME = ''
        PYTHONSTARTUP = ''
    }
    foreach ($property in @($Definition.isolation_environment.PSObject.Properties)) {
        $map[[string]$property.Name] = [string]$property.Value
    }
    return $map
}

function Test-WdBridgeCodeImports {
    param(
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string] $CodeRoot,
        [Parameter(Mandatory)] $Definition,
        [int] $TimeoutSeconds = 60
    )

    $codeFull = [IO.Path]::GetFullPath($CodeRoot).TrimEnd('\')
    $site = Join-Path $codeFull ([string]$Definition.python_site_relative)
    $thirdParty = [string]$Definition.import_smoke.third_party_module
    $packageModules = @($Definition.import_smoke.package_modules | ForEach-Object { [string]$_ })
    $moduleList = "['" + ($packageModules -join "','") + "']"
    $probe = (
        'import importlib,json,sys;' +
        "t=importlib.import_module('$thirdParty');" +
        "p={m:importlib.import_module(m).__file__ for m in $moduleList};" +
        'print(json.dumps(dict(third_party_file=t.__file__,package_files=p,' +
        'dont_write_bytecode=bool(sys.dont_write_bytecode),' +
        "safe_path=bool(getattr(sys.flags,'safe_path',0))," +
        'no_user_site=bool(sys.flags.no_user_site),no_site=bool(sys.flags.no_site),' +
        "site_loaded=('site' in sys.modules),version=sys.version.split()[0])))"
    )
    $environment = @{}
    $isolation = Get-WdBridgeCodeIsolationEnvironment -Definition $Definition -CodeRoot $codeFull
    foreach ($key in @($isolation.Keys)) {
        $environment[[string]$key] = [string]$isolation[$key]
    }
    $result = Invoke-WdBridgeCodePython `
        -PythonExecutable $PythonExecutable `
        -Arguments (@($script:WdBridgeCodePythonFlags) + @('-c', $probe)) `
        -Label 'pinned bridge code import smoke' `
        -Environment $environment `
        -TimeoutSeconds $TimeoutSeconds
    $report = [string]$result.StdOut | ConvertFrom-Json -ErrorAction Stop
    $sitePrefix = $site.TrimEnd('\') + '\'
    $codePrefix = $codeFull + '\'
    if (-not ([string]$report.third_party_file).StartsWith($sitePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "pinned bridge code smoke resolved $thirdParty outside the pinned site: $($report.third_party_file)"
    }
    foreach ($packageModule in $packageModules) {
        $expected = Join-Path $codeFull ($packageModule.Replace('.', '\') + '.py')
        $actual = [string]$report.package_files.$packageModule
        if (
            -not $actual.StartsWith($codePrefix, [StringComparison]::OrdinalIgnoreCase) -or
            -not [IO.Path]::GetFullPath($actual).Equals($expected, [StringComparison]::OrdinalIgnoreCase)
        ) {
            throw "pinned bridge code smoke resolved $packageModule outside the pinned code root: $actual"
        }
    }
    if (-not [bool]$report.dont_write_bytecode) {
        throw 'pinned bridge code smoke ran with bytecode writes enabled'
    }
    if (-not [bool]$report.safe_path) {
        throw 'pinned bridge code smoke ran without PYTHONSAFEPATH'
    }
    if (-not [bool]$report.no_user_site) {
        throw 'pinned bridge code smoke ran with the user site enabled'
    }
    if (-not [bool]$report.no_site -or [bool]$report.site_loaded) {
        throw 'pinned bridge code smoke ran with global site-packages enabled'
    }
    return $report
}

function Initialize-WdBridgeCodeContext {
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] $Deployment,
        [Parameter(Mandatory)] [string] $DefinitionPath,
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string] $Generation,
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [switch] $SkipImportSmoke
    )

    if ($Generation -cnotmatch '^[0-9a-f]{40}$') {
        throw 'bridge code generation must be a full lowercase Git commit'
    }
    $package = Get-WdBridgeCodePackageDefinition -Path $DefinitionPath
    $definitionProperty = $Deployment.files.PSObject.Properties[$script:WdBridgeCodeDefinitionName]
    if (
        $null -eq $definitionProperty -or
        ([string]$definitionProperty.Value).ToUpperInvariant() -cne $package.Hash
    ) {
        throw 'bridge code package definition is not covered by the anchored deployment manifest'
    }
    $python = Resolve-WdBridgePythonExecutable -ConfiguredPath $PythonExecutable
    $integrity = Assert-WdBridgeCodePackageIntegrity `
        -BundleRoot $BundleRoot `
        -Deployment $Deployment `
        -Definition $package.Definition
    $discovery = Get-WdBridgeCodeDiscoveryEnvironment `
        -Definition $package.Definition `
        -BundleRoot $BundleRoot `
        -PythonExecutable $python.Path `
        -PythonSha256 $python.Sha256 `
        -Generation $Generation `
        -RuntimeRoot $RuntimeRoot
    $isolation = Get-WdBridgeCodeIsolationEnvironment `
        -Definition $package.Definition `
        -CodeRoot $integrity.CodeRoot
    $smoke = $null
    if (-not $SkipImportSmoke) {
        $smoke = Test-WdBridgeCodeImports `
            -PythonExecutable $python.Path `
            -CodeRoot $integrity.CodeRoot `
            -Definition $package.Definition
    }
    # Only discovery variables reach the model shell. Python isolation is
    # applied per tool call by Invoke-WdBridgePythonTool and restored after.
    foreach ($key in @($discovery.Keys)) {
        [Environment]::SetEnvironmentVariable([string]$key, [string]$discovery[$key], 'Process')
    }
    return [pscustomobject]@{
        schema = $script:WdBridgeCodeContextSchema
        mode = 'deployed_pinned_package'
        bundle_root = [string]$discovery['WD_BRIDGE_BUNDLE_ROOT']
        code_root = $integrity.CodeRoot
        bridge_bin = $integrity.BridgeBin
        python_wrapper = $integrity.Wrapper
        python_executable = $python.Path
        python_executable_sha256 = $python.Sha256
        python_site = [string]$discovery['WD_BRIDGE_PYTHON_SITE']
        generation = $Generation
        definition_path = $package.Path
        definition_sha256 = $package.Hash
        package_file_count = $integrity.FileCount
        wheel_count = $integrity.WheelCount
        site_file_count = $integrity.SiteFileCount
        import_smoke = $smoke
        discovery_environment = $discovery
        isolation_environment_scope = 'per_tool_call_only'
        isolation_environment = $isolation
    }
}

function Invoke-WdBridgePythonTool {
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string] $Tool,
        [string[]] $ToolArguments = @(),
        [switch] $VerifyPackage
    )

    $bundleFull = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
    $trustedRoot = [IO.Path]::GetPathRoot($bundleFull)
    $deploymentPath = Join-Path $bundleFull 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $deploymentPath -PathType Leaf)) {
        throw "pinned bridge invocation requires a deployed bundle manifest: $deploymentPath"
    }
    [void](Assert-WdBridgeCodePathWithoutReparse `
        -Path $deploymentPath `
        -TrustedRoot $trustedRoot `
        -ExpectedType Leaf)
    $deploymentSnapshot = Read-WdBridgeCodeJsonSnapshot -Path $deploymentPath
    $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
    if (
        -not [string]::IsNullOrWhiteSpace($expectedManifestHash) -and
        [string]$deploymentSnapshot.Hash -cne $expectedManifestHash.ToUpperInvariant()
    ) {
        throw 'pinned bridge invocation found a deployment manifest that differs from its external anchor'
    }
    $deployment = $deploymentSnapshot.Object
    if ([int]$deployment.schema_version -ne 1) {
        throw "unsupported deployment manifest schema: $($deployment.schema_version)"
    }
    $definitionPath = Assert-WdBridgeCodeManifestFile `
        -Deployment $deployment `
        -RelativeName $script:WdBridgeCodeDefinitionName `
        -Path (Join-Path $bundleFull $script:WdBridgeCodeDefinitionName) `
        -TrustedRoot $trustedRoot
    $package = Get-WdBridgeCodePackageDefinition -Path $definitionPath
    $definition = $package.Definition
    $normalizedTool = $Tool.Replace('\', '/')
    $entrypoints = @($definition.python_entrypoints.PSObject.Properties | ForEach-Object { [string]$_.Value })
    if ($normalizedTool -cnotin $entrypoints) {
        throw "pinned bridge invocation refuses a tool outside the packaged entrypoints: $Tool"
    }
    $codeRoot = Join-Path $bundleFull $script:WdBridgeCodePackageRoot
    $toolPath = Assert-WdBridgeCodeManifestFile `
        -Deployment $deployment `
        -RelativeName ($script:WdBridgeCodePackageRoot + '/' + $normalizedTool) `
        -Path (Join-Path $codeRoot $normalizedTool.Replace('/', '\')) `
        -TrustedRoot $trustedRoot
    $configuredPython = [string]$env:WD_BRIDGE_PYTHON
    if ([string]::IsNullOrWhiteSpace($configuredPython)) {
        $fleetPath = Assert-WdBridgeCodeManifestFile `
            -Deployment $deployment `
            -RelativeName 'wd-fleet.json' `
            -Path (Join-Path $bundleFull 'wd-fleet.json') `
            -TrustedRoot $trustedRoot
        $fleet = (Read-WdBridgeCodeJsonSnapshot -Path $fleetPath).Object
        $configuredPython = [string]$fleet.bridge_python.executable
    }
    $python = Resolve-WdBridgePythonExecutable -ConfiguredPath $configuredPython
    $pinnedPythonHash = [string]$env:WD_BRIDGE_PYTHON_SHA256
    if (
        -not [string]::IsNullOrWhiteSpace($pinnedPythonHash) -and
        $python.Sha256 -cne $pinnedPythonHash.ToUpperInvariant()
    ) {
        throw 'pinned bridge Python changed after the lane handshake'
    }
    if ($VerifyPackage) {
        [void](Assert-WdBridgeCodePackageIntegrity `
            -BundleRoot $bundleFull `
            -Deployment $deployment `
            -Definition $definition)
    }
    $isolation = Get-WdBridgeCodeIsolationEnvironment -Definition $definition -CodeRoot $codeRoot
    $previous = @{}
    foreach ($key in @($script:WdBridgeCodeIsolationKeys)) {
        $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    }
    $previousPreference = $ErrorActionPreference
    $exitCode = 1
    $script:WdBridgeCodeLastExitCode = $null
    try {
        foreach ($key in @($isolation.Keys)) {
            $value = [string]$isolation[$key]
            if ([string]::IsNullOrEmpty($value)) {
                [Environment]::SetEnvironmentVariable([string]$key, $null, 'Process')
            }
            else {
                [Environment]::SetEnvironmentVariable([string]$key, $value, 'Process')
            }
        }
        $ErrorActionPreference = 'Continue'
        # No Out-Host: the packaged tools emit JSON that callers parse, so the
        # success stream must stay capturable. Interactive runs still display it.
        & $python.Path @($script:WdBridgeCodePythonFlags) $toolPath @ToolArguments
        $exitCode = [int]$LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
        foreach ($key in @($script:WdBridgeCodeIsolationKeys)) {
            [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
        }
        $script:WdBridgeCodeLastExitCode = $exitCode
    }
}

function Get-WdBridgeCodeLastExitCode {
    if ($null -eq $script:WdBridgeCodeLastExitCode) {
        throw 'no pinned bridge tool has run in this session'
    }
    return [int]$script:WdBridgeCodeLastExitCode
}

function Install-WdBridgePythonSite {
    param(
        [Parameter(Mandatory)] $Definition,
        [Parameter(Mandatory)] [string] $PythonExecutable,
        [Parameter(Mandatory)] [string] $WheelDirectory,
        [Parameter(Mandatory)] [string] $SiteDirectory,
        [Parameter(Mandatory)] [string] $WorkDirectory,
        [string] $WheelSource = ''
    )

    foreach ($directory in @($WheelDirectory, $SiteDirectory, $WorkDirectory)) {
        if (-not (Test-Path -LiteralPath $directory)) {
            [void](New-Item -ItemType Directory -Path $directory -Force -ErrorAction Stop)
        }
        elseif (@(Get-ChildItem -LiteralPath $directory -Force).Count -gt 0) {
            throw "bridge python staging directory is not empty: $directory"
        }
    }
    $wheelFull = [IO.Path]::GetFullPath($WheelDirectory).TrimEnd('\')
    $siteFull = [IO.Path]::GetFullPath($SiteDirectory).TrimEnd('\')
    $requirementLines = @()
    foreach ($requirement in @($Definition.python_requirements)) {
        $requirementLines += (
            '{0}=={1} --hash=sha256:{2}' -f
                [string]$requirement.name,
                [string]$requirement.version,
                ([string]$requirement.sha256).ToLowerInvariant()
        )
    }
    $requirementsPath = Join-Path $WorkDirectory 'bridge-python-requirements.txt'
    [IO.File]::WriteAllText(
        $requirementsPath,
        (($requirementLines -join "`n") + "`n"),
        (New-Object Text.UTF8Encoding($false))
    )
    $pipEnvironment = @{ PYTHONDONTWRITEBYTECODE = '1'; PIP_REQUIRE_VIRTUALENV = '' }
    $downloadArguments = @(
        '-m', 'pip', 'download',
        '--disable-pip-version-check',
        '--no-cache-dir',
        '--require-hashes',
        '--only-binary=:all:',
        '--no-deps',
        '--dest', $wheelFull,
        '--requirement', $requirementsPath
    )
    $platform = $Definition.python_platform
    if ($null -ne $platform) {
        $downloadArguments += @(
            '--python-version', [string]$platform.python_version,
            '--implementation', [string]$platform.implementation,
            '--abi', [string]$platform.abi,
            '--platform', [string]$platform.platform
        )
    }
    if ($WheelSource) {
        $downloadArguments += @('--no-index', '--find-links', ([IO.Path]::GetFullPath($WheelSource)))
    }
    [void](Invoke-WdBridgeCodePython `
        -PythonExecutable $PythonExecutable `
        -Arguments $downloadArguments `
        -Label 'pinned bridge wheel download' `
        -Environment $pipEnvironment)
    $wheelStore = [string]$Definition.wheel_store_relative
    $wheelMap = [ordered]@{}
    foreach ($requirement in @($Definition.python_requirements)) {
        $wheelPath = Join-Path $wheelFull ([string]$requirement.wheel)
        if (-not (Test-Path -LiteralPath $wheelPath -PathType Leaf)) {
            throw "pinned wheel was not downloaded: $($requirement.wheel)"
        }
        $actual = Get-WdBridgeCodeFileSha256 -Path $wheelPath
        if ($actual -cne ([string]$requirement.sha256).ToUpperInvariant()) {
            throw "pinned wheel hash mismatch after download: $($requirement.wheel)"
        }
        $wheelMap[$wheelStore + '/' + [string]$requirement.wheel] = $actual
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $wheelFull -Recurse -File -Force)) {
        if (-not $wheelMap.Contains($wheelStore + '/' + $file.Name)) {
            throw "unexpected file in the pinned wheel store: $($file.FullName)"
        }
    }
    $installArguments = @(
        '-m', 'pip', 'install',
        '--disable-pip-version-check',
        '--no-cache-dir',
        '--require-hashes',
        '--no-deps',
        '--no-index',
        '--no-compile',
        '--no-warn-script-location',
        '--find-links', $wheelFull,
        '--target', $siteFull,
        '--requirement', $requirementsPath
    )
    [void](Invoke-WdBridgeCodePython `
        -PythonExecutable $PythonExecutable `
        -Arguments $installArguments `
        -Label 'pinned bridge site extraction' `
        -Environment $pipEnvironment)
    $sitePrefix = $siteFull + '\'
    $siteRelative = [string]$Definition.python_site_relative
    $siteMap = [ordered]@{}
    foreach ($subdirectory in @(Get-ChildItem -LiteralPath $siteFull -Recurse -Directory -Force)) {
        if ($subdirectory.Name -ceq '__pycache__') {
            throw "pip left a bytecode cache in the pinned python site: $($subdirectory.FullName)"
        }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $siteFull -Recurse -File -Force | Sort-Object FullName)) {
        $fullName = [IO.Path]::GetFullPath($file.FullName)
        if (-not $fullName.StartsWith($sitePrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "pinned python site enumeration escaped its root: $fullName"
        }
        $relative = $fullName.Substring($sitePrefix.Length).Replace('\', '/')
        if ($relative -cmatch '\.py[co]$') {
            throw "compiled bytecode in the pinned python site: $relative"
        }
        if (-not (Test-WdBridgeCodeRelativePath -Relative $relative)) {
            throw "unsafe pinned python site path: $relative"
        }
        $siteMap[$siteRelative + '/' + $relative] = Get-WdBridgeCodeFileSha256 -Path $fullName
    }
    foreach ($requirement in @($Definition.python_requirements)) {
        $key = $siteRelative + '/' + [string]$requirement.import_path
        if (-not $siteMap.Contains($key)) {
            throw "pinned python site lacks the import path for $($requirement.name): $($requirement.import_path)"
        }
    }
    return [pscustomobject]@{
        Wheels = $wheelMap
        Site = $siteMap
        RequirementsPath = $requirementsPath
    }
}
