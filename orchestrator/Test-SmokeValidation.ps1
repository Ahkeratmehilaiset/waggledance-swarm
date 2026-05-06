#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-1 P3: negative + positive tests for the per-iteration unique
    smoke artifact validator. Stale or wrong-content files MUST fail; only
    the correct fresh file with exact path AND exact content passes.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'ArtifactValidator.ps1')

$script:tests = 0; $script:passes = 0; $script:fails = @()
function _t([string]$name, [bool]$cond, [string]$detail = '') {
    $script:tests++
    if ($cond) { $script:passes++; Write-Host "PASS  $name" -ForegroundColor Green }
    else { Write-Host "FAIL  $name $detail" -ForegroundColor Red; $script:fails += $name }
}

# -- isolated scratch dir
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("smokeval_" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

try {
    # -- Setup: simulate two iterations to expose the stale-artifact trap
    $oldIterId  = '2026-05-06_10-00-00'
    $oldIterDir = Join-Path $tmp ('iterations\' + $oldIterId + '\artifacts')
    New-Item -ItemType Directory -Path $oldIterDir -Force | Out-Null
    $oldArtifactPath = Join-Path $oldIterDir ('smoke_' + $oldIterId + '.txt')
    Set-Content -Path $oldArtifactPath -Value ("WaggleDance smoke artifact for iteration $oldIterId") -Encoding UTF8 -NoNewline
    $oldMtime = [datetime]::UtcNow.AddHours(-2)
    (Get-Item $oldArtifactPath).LastWriteTimeUtc = $oldMtime

    # -- The new iteration uses a DIFFERENT id and expects a DIFFERENT path.
    $newIterId  = '2026-05-06_16-00-00'
    $newIterDir = Join-Path $tmp ('iterations\' + $newIterId + '\artifacts')
    $newArtifactPath = Join-Path $newIterDir ('smoke_' + $newIterId + '.txt')
    $newBody = "WaggleDance smoke artifact for iteration $newIterId"
    $runStart = [datetime]::UtcNow.AddSeconds(-5)

    # -- Negative 1: new artifact is missing entirely.
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $runStart
    _t 'NEG: missing new artifact -> ok=false' (-not $r.ok)
    _t 'NEG: missing new artifact -> reports unique_artifact_present false' (
        ($r.errors -join '|') -match 'unique_artifact_present'
    )

    # -- Negative 2: same body, wrong path (stale-by-path attack)
    # Even if the OLD artifact has byte-identical content, validation against
    # the NEW path must fail because the stale file is at a different path.
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent ("WaggleDance smoke artifact for iteration $oldIterId") `
        -RunStartedUtc $runStart
    _t 'NEG: stale-only artifact at old path cannot satisfy new path' (-not $r.ok)

    # -- Negative 3: new path EXISTS but content is wrong
    New-Item -ItemType Directory -Path $newIterDir -Force | Out-Null
    Set-Content -Path $newArtifactPath -Value 'wrong body' -Encoding UTF8 -NoNewline
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $runStart
    _t 'NEG: wrong content -> ok=false' (-not $r.ok)
    _t 'NEG: wrong content -> content_exact failure' (
        ($r.errors -join '|') -match 'unique_artifact_content_exact'
    )

    # -- Negative 4: file at correct path with correct body but stale mtime
    Set-Content -Path $newArtifactPath -Value $newBody -Encoding UTF8 -NoNewline
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow.AddHours(-1)
    $freshRunStart = [datetime]::UtcNow.AddSeconds(-5)
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $freshRunStart
    _t 'NEG: stale mtime -> ok=false' (-not $r.ok)
    _t 'NEG: stale mtime -> reports unique_artifact_fresh' (
        ($r.errors -join '|') -match 'unique_artifact_fresh'
    )

    # -- Negative 5: file too large
    $bigBody = $newBody + ("`n" + ('x' * 8000))
    Set-Content -Path $newArtifactPath -Value $bigBody -Encoding UTF8 -NoNewline
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $freshRunStart `
        -MaxBytes 4096
    _t 'NEG: file too large -> ok=false' (-not $r.ok)
    _t 'NEG: file too large -> reports size_max' (
        ($r.errors -join '|') -match 'unique_artifact_size_max'
    )

    # -- Negative 6: file with NUL byte
    $bytesWithNul = [byte[]] @(0x57, 0x61, 0x67, 0x67, 0x6c, 0x65, 0x00, 0x65, 0x6e, 0x64)
    [System.IO.File]::WriteAllBytes($newArtifactPath, $bytesWithNul)
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent 'Waggle' `
        -RunStartedUtc $freshRunStart
    _t 'NEG: NUL byte -> ok=false' (-not $r.ok)
    _t 'NEG: NUL byte -> reports no_nul' (
        ($r.errors -join '|') -match 'unique_artifact_no_nul'
    )

    # -- POSITIVE: write the exact correct body now, with fresh mtime
    Set-Content -Path $newArtifactPath -Value $newBody -Encoding UTF8 -NoNewline
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $freshRunStart
    _t 'POS: exact path + exact content + fresh mtime -> ok=true' ($r.ok)
    _t 'POS: zero errors' ($r.errors.Count -eq 0)

    # -- POSITIVE 2: trailing single LF tolerated (Write tool may append)
    $bodyWithLf = $newBody + "`n"
    Set-Content -Path $newArtifactPath -Value $bodyWithLf -Encoding UTF8 -NoNewline
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $freshRunStart
    _t 'POS: single trailing LF tolerated' ($r.ok)

    # -- POSITIVE 3: leading UTF-8 BOM tolerated (Set-Content -Encoding UTF8
    # writes a BOM by default in PS 5.1; Claude's Write tool may also do it).
    $enc = New-Object System.Text.UTF8Encoding($true)  # WITH BOM
    [System.IO.File]::WriteAllText($newArtifactPath, $newBody, $enc)
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $freshRunStart
    _t 'POS: leading UTF-8 BOM tolerated' ($r.ok)

    # -- NEGATIVE 7: a non-BOM leading character MUST be rejected. This
    # guards against PowerShell `-eq` regressing into culture-sensitive
    # comparison that ignores zero-width characters.
    $bytesWithExtra = ([byte[]] @(0x58)) + [System.Text.Encoding]::UTF8.GetBytes($newBody)  # 'X' + body
    [System.IO.File]::WriteAllBytes($newArtifactPath, $bytesWithExtra)
    (Get-Item $newArtifactPath).LastWriteTimeUtc = [datetime]::UtcNow
    $r = Test-UniqueIterationArtifact `
        -ExpectedAbsolutePath $newArtifactPath `
        -ExpectedContent $newBody `
        -RunStartedUtc $freshRunStart
    _t 'NEG: leading non-BOM character rejected (ordinal compare)' (-not $r.ok)
}
finally {
    if (Test-Path $tmp) { Remove-Item -Recurse -Force -LiteralPath $tmp }
}

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
