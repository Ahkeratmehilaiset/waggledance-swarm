#requires -Version 5.1
<# Passive recovery only: never starts Grok, enables a task, or edits a worktree. #>
[CmdletBinding()]
param([switch] $Apply)
$ErrorActionPreference = 'Stop'
$root = 'C:\Python\grok-scout-reports'
$state = Join-Path $root 'hourly-state.json'
$taskNames = @('WD-GrokDispatcher','WD-GrokRedteam','WD-GrokResearch','WD-GrokScout-Daily','WD-GrokWatchdog')
$legacyNames = @('Invoke-GrokBuilder.ps1','Invoke-GrokDispatcher.ps1','Invoke-GrokRedteam.ps1',
    'Invoke-GrokResearch.ps1','Invoke-GrokScout.ps1','Invoke-GrokWatchdog.ps1','Update-GrokWorktree.ps1')
$tasks = @(Get-ScheduledTask | Where-Object { $_.TaskName -in $taskNames })
if ($Apply) {
    # Never stop an invocation. Fail before changing launch paths if one exists.
    $active = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'grok.exe' -or ($_.Name -match '^(powershell|pwsh)\.exe$' -and
        $_.CommandLine -match '(?i)-File\s+"?[^"\r\n]*[\\/](Invoke-Grok[^\\/\s"]*|Update-GrokWorktree)\.ps1(?:"|\s|$)')
    })
    if ($active.Count -or @($tasks | Where-Object { [string]$_.State -eq 'Running' }).Count) {
        throw 'Grok invocation is active; migration will not interrupt it'
    }
    $backup = Join-Path 'C:\Python\wd-reboot-backups' ('grok-' + [guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $backup)
    foreach ($task in $tasks) {
        Export-ScheduledTask -TaskName $task.TaskName -TaskPath $task.TaskPath |
            Out-File (Join-Path $backup ($task.TaskName + '.xml')) -Encoding utf8
    }
    foreach ($name in $legacyNames) {
        $path = Join-Path 'C:\Python' $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backup $name) }
    }
    if (Test-Path -LiteralPath $state) { Copy-Item -LiteralPath $state -Destination (Join-Path $backup 'hourly-state.json') }
    foreach ($task in $tasks) { $task | Disable-ScheduledTask | Out-Null }
    # A trigger could have fired during export. Do not replace an active script.
    if (@(Get-ScheduledTask | Where-Object { $_.TaskName -in $taskNames -and [string]$_.State -eq 'Running' }).Count) {
        throw "Grok started during migration; schedules disabled, scripts untouched; backup: $backup"
    }
    $stub = "# Retired: hourly-budget bypass and worktree reset are disabled.`r`nthrow 'Use C:\Python\Invoke-WdGrok.ps1 for lead-requested advisory access; no autonomous calls or worktree resets.'`r`n"
    foreach ($name in $legacyNames) {
        $path = Join-Path 'C:\Python' $name
        if (Test-Path -LiteralPath $path) { [IO.File]::WriteAllText($path, $stub, [Text.UTF8Encoding]::new($false)) }
    }
    Write-Host "Grok legacy schedules disabled; prior files and task definitions saved: $backup"
}
$enabled = @(Get-ScheduledTask | Where-Object { $_.TaskName -in $taskNames -and $_.Settings.Enabled })
if ($enabled.Count) { throw 'Legacy autonomous Grok schedules must be disabled before recovery' }
if (-not (Test-Path -LiteralPath $state)) {
    if (-not $Apply) {
        throw 'Grok recovery state is missing; run the controlled installation first'
    }
    [void](New-Item -ItemType Directory -Path $root -Force)
    # CreateNew refuses to overwrite any concurrently initialized state.
    $stream = [IO.File]::Open($state, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $previousReport = Get-ChildItem -LiteralPath $root -File | Where-Object {
            $_.Name -match '^grok-.*\.(md|json)$'
        } | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
        $record = [ordered]@{
            schema = 'wd.grok-hourly.v1'
            last_attempt_utc = [DateTimeOffset]::UtcNow.ToString('o')
            status = 'initialized_conservative_cooldown'
            task_id = ''
            previous_report = $(if ($previousReport) { $previousReport.FullName } else { $null })
            legacy_history_imported = $true
        }
        $bytes = [Text.Encoding]::UTF8.GetBytes(($record | ConvertTo-Json))
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    } finally { $stream.Dispose() }
}
$saved = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
if ($saved.schema -cne 'wd.grok-hourly.v1') { throw 'Invalid Grok recovery state' }
$last = [DateTimeOffset]::Parse([string]$saved.last_attempt_utc)
[pscustomobject]@{
    schema = 'wd.grok-recovery.v1'
    agent = 'grok-scout-1'
    role = 'lead-requested advisory helper; no independent task execution'
    state_path = $state
    state_status = [string]$saved.status
    previous_task = [string]$saved.task_id
    next_eligible_utc = $last.AddHours(1).ToString('o')
    model_started = $false
    invocation = 'C:\Python\Invoke-WdGrok.ps1 -PromptPath <evidence-request.md> -TaskId <task-id>'
}
