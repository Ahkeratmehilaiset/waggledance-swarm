#requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string] $Root = (Get-Location).Path,
    [switch] $RepairAcl
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Resolve-AbsolutePath {
    param([Parameter(Mandatory)] [string] $Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Path))
}

function Test-IsSubPath {
    param(
        [Parameter(Mandatory)] [string] $Candidate,
        [Parameter(Mandatory)] [string] $RootPath
    )
    $rootNorm = $RootPath.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $candidateNorm = $Candidate.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    return $candidateNorm.StartsWith($rootNorm, [System.StringComparison]::OrdinalIgnoreCase)
}

function Test-IsAllowedPytestTempTarget {
    param(
        [Parameter(Mandatory)] [string] $Target,
        [Parameter(Mandatory)] [string] $RepoRoot
    )
    if (-not (Test-IsSubPath -Candidate $Target -RootPath $RepoRoot)) {
        return $false
    }

    $leaf = Split-Path -Leaf $Target
    if ($leaf -like '.pytest_tmp*') {
        return $true
    }

    $parent = Split-Path -Parent $Target
    $parentLeaf = Split-Path -Leaf $parent
    $grandParent = Split-Path -Parent $parent
    $grandParentLeaf = Split-Path -Leaf $grandParent
    return ($parentLeaf -eq 'pytest_tmp' -and $grandParentLeaf -eq '.codex-audit')
}

$rootPath = Resolve-AbsolutePath $Root
Push-Location $rootPath
try {
    $repoRootRaw = (& git rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $repoRootRaw) {
        throw "clean_pytest_temp: not inside a git worktree: $rootPath"
    }
    $repoRoot = Resolve-AbsolutePath ([string]$repoRootRaw)
} finally {
    Pop-Location
}

if (-not (Test-IsSubPath -Candidate $rootPath -RootPath $repoRoot) -and
    -not ($rootPath -ieq $repoRoot)) {
    throw "clean_pytest_temp: Root must be the repo root or a subdirectory: $rootPath"
}

$targets = @()
$targets += @(Get-ChildItem -LiteralPath $repoRoot -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like '.pytest_tmp*' })

$auditPytestRoot = Join-Path (Join-Path $repoRoot '.codex-audit') 'pytest_tmp'
if (Test-Path -LiteralPath $auditPytestRoot -PathType Container) {
    $targets += @(Get-ChildItem -LiteralPath $auditPytestRoot -Force -Directory -ErrorAction SilentlyContinue)
}

$removed = 0
foreach ($target in $targets) {
    $targetPath = Resolve-AbsolutePath $target.FullName
    if (-not (Test-IsAllowedPytestTempTarget -Target $targetPath -RepoRoot $repoRoot)) {
        throw "clean_pytest_temp: refusing non-pytest-temp target: $targetPath"
    }

    $isWindows = [System.Environment]::OSVersion.Platform -eq [System.PlatformID]::Win32NT
    if ($RepairAcl -and $isWindows) {
        if ($PSCmdlet.ShouldProcess($targetPath, 'repair ACL ownership')) {
            & takeown /F $targetPath /R /D Y | Out-Null
            & icacls $targetPath /grant "$($env:USERNAME):F" /T /C | Out-Null
        }
    }

    if ($PSCmdlet.ShouldProcess($targetPath, 'remove pytest temp directory')) {
        Remove-Item -LiteralPath $targetPath -Recurse -Force
        $removed += 1
    }
}

[pscustomobject]@{
    repo_root = $repoRoot
    removed = $removed
    repair_acl = [bool]$RepairAcl
}
