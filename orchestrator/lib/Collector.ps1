# Collector.ps1
# Gathers iteration artifacts. PS 5.1 + Set-StrictMode Latest -compatible:
# uses straight code, no inner scriptblocks, defensive Count via @(...).

Set-StrictMode -Version Latest

function Get-LastNLines {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path, [int] $LineCount = 1000)
    if (-not (Test-Path $Path)) { return @() }
    return @(Get-Content -Path $Path -Tail $LineCount)
}

function Get-GitMetadata {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $RepoPath)

    $result = [ordered]@{
        is_git_repo    = $false
        branch         = $null
        commit         = $null
        commit_message = $null
        commit_at      = $null
        modified_files = @()
        is_dirty       = $false
        diff_summary   = @()
        error          = $null
    }

    if (-not (Test-Path (Join-Path $RepoPath '.git'))) {
        return [pscustomobject]$result
    }

    try {
        Push-Location $RepoPath
        $result.is_git_repo    = $true
        $result.branch         = (& git rev-parse --abbrev-ref HEAD 2>$null) | Select-Object -First 1
        $result.commit         = (& git rev-parse HEAD 2>$null)              | Select-Object -First 1
        $result.commit_message = (& git log -1 --pretty=%s 2>$null)          | Select-Object -First 1
        $result.commit_at      = (& git log -1 --pretty=%cI 2>$null)         | Select-Object -First 1
        $modifiedRaw           = & git status --porcelain 2>$null
        $modifiedArr = @()
        if ($null -ne $modifiedRaw) { $modifiedArr = @($modifiedRaw) }
        $result.modified_files = $modifiedArr
        $result.is_dirty       = (@($modifiedArr).Count -gt 0)
        $diffRaw = & git diff --stat 2>$null
        if ($null -ne $diffRaw) { $result.diff_summary = @($diffRaw) }
    }
    catch {
        $result.error = $_.Exception.Message
    }
    finally {
        Pop-Location
    }
    return [pscustomobject]$result
}

function New-IterationFolder {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationsRoot,
        [Parameter(Mandatory)] [string] $IterationId
    )
    $folder = Join-Path $IterationsRoot $IterationId
    if (-not (Test-Path $folder)) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
    }
    return $folder
}

function Save-IterationArtifacts {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationFolder,
        [string] $TranscriptFile = '',
        [string] $ReportFile = '',
        [int]    $TailLineCount = 1000,
        [int]    $FullTranscriptMaxBytes = 10MB,
        [string] $ProjectRoot = '',
        $StateObject = $null,
        $RunnerResult = $null,
        [switch] $Force
    )

    # 1) tail
    if ($TranscriptFile -and (Test-Path $TranscriptFile)) {
        $tailPath = Join-Path $IterationFolder 'powershell_tail.txt'
        if (-not ((Test-Path $tailPath) -and -not $Force)) {
            $tail = Get-LastNLines -Path $TranscriptFile -LineCount $TailLineCount
            $tailCount = 0
            if ($null -ne $tail) { $tailCount = @($tail).Count }
            if ($tailCount -gt 0) {
                Set-Content -Path $tailPath -Value $tail -Encoding UTF8
            } else {
                Set-Content -Path $tailPath -Value '(transcript was empty)' -Encoding UTF8
            }
        }

        # 2) full transcript copy under cap
        $size = (Get-Item $TranscriptFile).Length
        if ($size -le $FullTranscriptMaxBytes) {
            $fullPath = Join-Path $IterationFolder 'transcript_full.log'
            if (-not ((Test-Path $fullPath) -and -not $Force)) {
                Copy-Item -Path $TranscriptFile -Destination $fullPath -Force
            }
        } else {
            $skipPath = Join-Path $IterationFolder 'transcript_full.log.SKIPPED.txt'
            if (-not ((Test-Path $skipPath) -and -not $Force)) {
                $note = "Full transcript not copied: size $size bytes exceeds FullTranscriptMaxBytes $FullTranscriptMaxBytes.`nOriginal location: $TranscriptFile"
                Set-Content -Path $skipPath -Value $note -Encoding UTF8
            }
        }
    }

    # 3) raportti.md snapshot
    if ($ReportFile -and (Test-Path $ReportFile)) {
        $repPath = Join-Path $IterationFolder 'raportti.md'
        if (-not ((Test-Path $repPath) -and -not $Force)) {
            Copy-Item -Path $ReportFile -Destination $repPath -Force
        }
    }

    # 4) git metadata
    if ($ProjectRoot) {
        $gmPath = Join-Path $IterationFolder 'git_metadata.json'
        if (-not ((Test-Path $gmPath) -and -not $Force)) {
            (Get-GitMetadata -RepoPath $ProjectRoot) | ConvertTo-Json -Depth 6 | Set-Content -Path $gmPath -Encoding UTF8
        }
    }

    # 5) runner metadata (print mode)
    if ($null -ne $RunnerResult) {
        $rmPath = Join-Path $IterationFolder 'run_metadata.json'
        if (-not ((Test-Path $rmPath) -and -not $Force)) {
            $RunnerResult | ConvertTo-Json -Depth 6 | Set-Content -Path $rmPath -Encoding UTF8
        }
    }

    # 6) state.json (always written, this is the source of truth)
    if ($null -ne $StateObject) {
        $StateObject | ConvertTo-Json -Depth 12 |
            Set-Content -Path (Join-Path $IterationFolder 'state.json') -Encoding UTF8
    }
}

function Update-CurrentStatePointer {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $StateDir,
        [Parameter(Mandatory)] $State
    )
    if (-not (Test-Path $StateDir)) {
        New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
    }
    $current = Join-Path $StateDir 'current.json'
    $tmp = "$current.tmp"
    $State | ConvertTo-Json -Depth 12 | Set-Content -Path $tmp -Encoding UTF8
    Move-Item -Force $tmp $current
}
