# tools/savepoint.ps1
#
# Safe checkpoint helper. Added after the 2026-04-11 recovery incident
# to make it impossible to leave a green checkpoint sitting on a single
# machine overnight.
#
# What it does:
#   1. Refuses to run off the C: drive (RAM-disks, temp folders, etc.
#      are banned as sources of truth - see docs/RECOVERY_POLICY.md).
#   2. Shows `git status` so the operator sees what will be included.
#   3. Runs the tests you pass via -TestPath (default: the Phase 7
#      regression suite). A test failure aborts the checkpoint.
#   4. Commits the currently-staged changes with the message you pass.
#      You must stage the files yourself first - the script will not
#      blanket `git add -A` to avoid pulling in secrets or gitignored
#      runtime data.
#   5. Pushes the current branch to `origin` so the green state is
#      anchored on GitHub before you do anything else.
#
# Usage:
#   .\tools\savepoint.ps1 -Message "fix(foo): bar"
#   .\tools\savepoint.ps1 -Message "..." -TestPath "tests/test_foo.py"
#   .\tools\savepoint.ps1 -Message "..." -SkipTests       # emergency only
#   .\tools\savepoint.ps1 -MessageFile "C:\path\commit-msg.txt"
#
# -MessageFile reads the commit message from a UTF-8 file (a leading BOM is
# tolerated) and commits it with `git commit -F`, so multi-line messages and
# special characters never pass through command-line argument parsing (a
# nested `powershell -File` invocation mangles multi-line -Message values).
# Exactly one of -Message / -MessageFile must be given.

[CmdletBinding()]
param(
    [string]$Message = "",

    [string]$MessageFile = "",

    [string]$TestPath = "tests/test_phase7_hologram_news_wire.py",

    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"

# --- 0. Commit message source --------------------------------------------
# Exactly one of -Message / -MessageFile. Validated before any git work so a
# bad invocation fails before it can touch the tree.
$hasMessage = -not [string]::IsNullOrWhiteSpace($Message)
$hasMessageFile = -not [string]::IsNullOrWhiteSpace($MessageFile)
if ($hasMessage -eq $hasMessageFile) {
    Write-Error "savepoint: pass exactly one of -Message or -MessageFile."
    exit 2
}
[byte[]]$messageBytes = @()
if ($hasMessageFile) {
    if (-not (Test-Path -LiteralPath $MessageFile -PathType Leaf)) {
        Write-Error "savepoint: -MessageFile is not an existing file: $MessageFile"
        exit 2
    }
    $messageBytes = [System.IO.File]::ReadAllBytes(
        (Resolve-Path -LiteralPath $MessageFile).Path
    )
    # Tolerate a UTF-8 BOM (Windows PowerShell 5.1 Set-Content -Encoding UTF8
    # writes one); git would otherwise keep it as the first message bytes.
    if ($messageBytes.Length -ge 3 -and
        $messageBytes[0] -eq 0xEF -and
        $messageBytes[1] -eq 0xBB -and
        $messageBytes[2] -eq 0xBF) {
        if ($messageBytes.Length -eq 3) {
            $messageBytes = @()
        } else {
            $messageBytes = $messageBytes[3..($messageBytes.Length - 1)]
        }
    }
    $messageText = [System.Text.Encoding]::UTF8.GetString($messageBytes)
    if ([string]::IsNullOrWhiteSpace($messageText)) {
        Write-Error "savepoint: -MessageFile is empty: $MessageFile"
        exit 2
    }
}

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
    Write-Host "savepoint: running tests - $python -m pytest -q $TestPath" -ForegroundColor Cyan
    & $python -m pytest -q $TestPath
    if ($LASTEXITCODE -ne 0) {
        Write-Error "savepoint: tests failed - aborting checkpoint."
        exit 1
    }
} else {
    Write-Host "savepoint: -SkipTests set - skipping tests (emergency mode)." -ForegroundColor Yellow
}

# --- 6. Commit -----------------------------------------------------------
Write-Host "savepoint: committing" -ForegroundColor Cyan
if ($hasMessageFile) {
    # Hand git an exact BOM-free copy through -F (inside the git dir, next to
    # COMMIT_EDITMSG) so the message bytes never pass through argument parsing
    # and a caller edit between validation and commit cannot change them. The
    # name is PID+GUID suffixed so two concurrent invocations in the same
    # worktree can never read or delete each other's scratch copy.
    $gitDirRaw = (& git rev-parse --absolute-git-dir)
    if ($LASTEXITCODE -ne 0 -or -not $gitDirRaw) {
        Write-Error "savepoint: could not resolve the git dir for the commit message."
        exit 1
    }
    $commitMessageName = "SAVEPOINT_COMMIT_MSG.$PID." + [guid]::NewGuid().ToString("N")
    $commitMessagePath = Join-Path ([string]$gitDirRaw).Trim() $commitMessageName
    [System.IO.File]::WriteAllBytes($commitMessagePath, $messageBytes)
    try {
        & git commit -F $commitMessagePath
        $commitExit = $LASTEXITCODE
    } finally {
        Remove-Item -LiteralPath $commitMessagePath -Force -ErrorAction SilentlyContinue
    }
} else {
    & git commit -m $Message
    $commitExit = $LASTEXITCODE
}
if ($commitExit -ne 0) {
    Write-Error "savepoint: commit failed."
    exit 1
}

# --- 7. Push -------------------------------------------------------------
Write-Host "savepoint: pushing $branch to origin" -ForegroundColor Cyan
& git push -u origin $branch
if ($LASTEXITCODE -ne 0) {
    Write-Error "savepoint: push failed - the commit exists locally but is NOT anchored on GitHub. Resolve and re-run 'git push -u origin $branch' before doing any other work."
    exit 1
}

Write-Host "savepoint: OK - $branch pushed to origin" -ForegroundColor Green
