# tools/savepoint.ps1
#
# Safe checkpoint helper. Added after the 2026-04-11 recovery incident
# to make it impossible to leave a green checkpoint sitting on a single
# machine overnight.
#
# What it does:
#   1. Refuses to run off the C: drive (RAM-disks, temp folders, etc.
#      are banned as sources of truth — see docs/RECOVERY_POLICY.md).
#   2. Shows `git status` so the operator sees what will be included.
#   3. Runs the tests you pass via -TestPath (default: the Phase 7
#      regression suite). A test failure aborts the checkpoint.
#   4. Commits the currently-staged changes with the message you pass.
#      You must stage the files yourself first — the script will not
#      blanket `git add -A` to avoid pulling in secrets or gitignored
#      runtime data.
#   5. Pushes the current branch to `origin` so the green state is
#      anchored on GitHub before you do anything else.
#
# Usage:
#   .\tools\savepoint.ps1 -Message "fix(foo): bar"
#   .\tools\savepoint.ps1 -Message "..." -TestPath "tests/test_foo.py"
#   .\tools\savepoint.ps1 -Message "..." -SkipTests       # emergency only

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Message,

    [string]$TestPath = "tests/test_phase7_hologram_news_wire.py",

    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

# --- 1. Drive safety check ------------------------------------------------
$cwd = (Get-Location).Path
if (-not $cwd.StartsWith("C:\")) {
    Write-Error "savepoint: refusing to run off the C: drive (cwd=$cwd). See docs/RECOVERY_POLICY.md."
    exit 2
}
# Extra belt-and-braces: refuse anything that smells like a RAM-disk / temp.
$forbidden = @("U:\", "R:\", "$env:TEMP", "$env:TMP")
foreach ($f in $forbidden) {
    if ($f -and $cwd -like "${f}*") {
        Write-Error "savepoint: cwd=$cwd looks like a volatile path ($f). Refusing."
        exit 2
    }
}

# --- 2. Must be inside a git repo ----------------------------------------
$insideWorkTree = (& git rev-parse --is-inside-work-tree 2>$null)
if ($LASTEXITCODE -ne 0 -or $insideWorkTree -ne "true") {
    Write-Error "savepoint: not inside a git working tree (cwd=$cwd)."
    exit 2
}

$branch = (& git rev-parse --abbrev-ref HEAD).Trim()
Write-Host "savepoint: branch=$branch cwd=$cwd" -ForegroundColor Cyan

# --- 3. Show git status --------------------------------------------------
Write-Host "savepoint: git status --short" -ForegroundColor Cyan
& git status --short
Write-Host ""

# --- 4. Must have something staged ---------------------------------------
$staged = (& git diff --cached --name-only)
if (-not $staged) {
    Write-Error "savepoint: nothing staged. Stage files with 'git add <file>' first (never 'git add -A')."
    exit 2
}
Write-Host "savepoint: staged files:" -ForegroundColor Cyan
$staged | ForEach-Object { Write-Host "  $_" }
Write-Host ""

# --- 5. Run tests --------------------------------------------------------
if (-not $SkipTests) {
    $python = ".\.venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $python = "python"
    }
    Write-Host "savepoint: running tests — $python -m pytest -q $TestPath" -ForegroundColor Cyan
    & $python -m pytest -q $TestPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "savepoint: tests failed — aborting checkpoint."
        exit 1
    }
} else {
    Write-Host "savepoint: -SkipTests set — skipping tests (emergency mode)." -ForegroundColor Yellow
}

# --- 6. Commit -----------------------------------------------------------
Write-Host "savepoint: committing" -ForegroundColor Cyan
& git commit -m $Message
if ($LASTEXITCODE -ne 0) {
    Write-Error "savepoint: commit failed."
    exit 1
}

# --- 7. Push -------------------------------------------------------------
Write-Host "savepoint: pushing $branch to origin" -ForegroundColor Cyan
& git push -u origin $branch
if ($LASTEXITCODE -ne 0) {
    Write-Error "savepoint: push failed — the commit exists locally but is NOT anchored on GitHub. Resolve and re-run 'git push -u origin $branch' before doing any other work."
    exit 1
}

Write-Host "savepoint: OK — $branch pushed to origin" -ForegroundColor Green
