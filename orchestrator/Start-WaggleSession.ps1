#requires -Version 5.1
<#
.SYNOPSIS
    Bootstraps a Claude Code session: enables Start-Transcript, sets window
    title to "WaggleDanceAi", prints the transcript path.
.DESCRIPTION
    Run this AT THE START of the WaggleDanceAi PowerShell window, BEFORE you
    launch claude / claude-code. The orchestrator (Watch-ClaudeCode.ps1) will
    later read the transcript file this command produces.

    Do not run this from inside the orchestrator's own window. Run it in the
    terminal where Claude Code will execute.
.PARAMETER ConfigPath
    Path to orchestrator.config.json (or config.example.json copy).
.EXAMPLE
    . .\orchestrator\Start-WaggleSession.ps1 -ConfigPath .\orchestrator.config.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $ConfigPath
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $ConfigPath)) {
    throw "Config file not found: $ConfigPath"
}
$cfg = Get-Content -Raw -Path $ConfigPath | ConvertFrom-Json

$projectRoot   = $cfg.projectRoot
$transcriptDir = Join-Path $projectRoot $cfg.transcriptDir

if (-not (Test-Path $transcriptDir)) {
    New-Item -ItemType Directory -Path $transcriptDir -Force | Out-Null
}

$timestamp      = Get-Date -Format 'yyyy-MM-dd_HH-mm-ss'
$transcriptName = "waggledance_${timestamp}.log"
$transcriptPath = Join-Path $transcriptDir $transcriptName

# Stop any existing transcript silently before starting a new one
try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}

Start-Transcript -Path $transcriptPath -Append -IncludeInvocationHeader | Out-Null

$Host.UI.RawUI.WindowTitle = 'WaggleDanceAi'

Write-Host ''
Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  WaggleDance session started' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host "  Transcript : $transcriptPath" -ForegroundColor Green
Write-Host "  Window     : WaggleDanceAi" -ForegroundColor Green
Write-Host "  Project    : $projectRoot" -ForegroundColor Green
Write-Host ''
Write-Host 'Now you can launch Claude Code in this window.' -ForegroundColor Yellow
Write-Host 'In a separate window, start the watcher:' -ForegroundColor Yellow
Write-Host "  pwsh .\orchestrator\Watch-ClaudeCode.ps1 -ConfigPath $ConfigPath" -ForegroundColor Gray
Write-Host ''
