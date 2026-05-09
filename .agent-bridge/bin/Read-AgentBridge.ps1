#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('codex','claude','operator','system','')] [string] $Agent = '',
    [int] $Tail = 40,
    [switch] $OtherOnly,
    [switch] $ShowClaims,
    [switch] $ShowLiveness,
    [switch] $NoContinuity,
    [switch] $Raw
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$bridgeRoot = Split-Path -Parent $PSScriptRoot
$eventsPath = Join-Path (Join-Path $bridgeRoot 'shared') 'events.jsonl'

function Read-BridgeEventObjects {
    param([Parameter(Mandatory)] [string] $Path, [int] $MaxLines = 5000)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }
    $allLines = @(Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8)
    foreach ($line in $allLines) {
        if (-not $line) { continue }
        try {
            [void]$items.Add(($line | ConvertFrom-Json))
        } catch {}
    }
    return $items
}

if ($ShowClaims) {
    $claimsDir = Join-Path (Join-Path $bridgeRoot 'work_queue') 'claims'
    Write-Host 'ACTIVE CLAIMS' -ForegroundColor Cyan
    if (Test-Path -LiteralPath $claimsDir) {
        $claims = @(Get-ChildItem -Path $claimsDir -Filter '*.json' -File | Sort-Object LastWriteTime)
        if ($claims.Count -eq 0) {
            Write-Host '  (none)'
        } else {
            foreach ($c in $claims) {
                try {
                    $obj = Get-Content -Raw -Path $c.FullName -Encoding UTF8 | ConvertFrom-Json
                    Write-Host ("  {0} [{1}] by {2}: {3}" -f $obj.task_id, $obj.mode, $obj.agent, $obj.summary)
                    if ($obj.write_scope -and @($obj.write_scope).Count -gt 0) {
                        Write-Host ("    scope: {0}" -f ((@($obj.write_scope)) -join ', '))
                    }
                } catch {
                    Write-Host ("  unreadable claim: {0}" -f $c.Name) -ForegroundColor Yellow
                }
            }
        }
    } else {
        Write-Host '  (none)'
    }
    Write-Host ''
}

if ($Agent -and -not $NoContinuity) {
    Write-Host ("CONTINUITY FOR {0}" -f $Agent) -ForegroundColor Cyan
    if (-not (Test-Path -LiteralPath $eventsPath)) {
        Write-Host '  (no events.jsonl yet)'
        Write-Host ''
    } else {
        $allEvents = Read-BridgeEventObjects -Path $eventsPath
        $requestTypes = @('message','handoff','blocked','finding','decision','done')
        $requestStatuses = @(
            'request','ready','blocked','open','proposal',
            'fix-pushed','fix-branch-pushed','pushed',
            'ready_for_implementation'
        )
        $requests = @(
            $allEvents |
                Where-Object {
                    $_.PSObject.Properties['to'] -and
                    [string]$_.to -eq $Agent -and
                    [string]$_.agent -ne $Agent -and
                    $requestTypes -contains [string]$_.type -and
                    $requestStatuses -contains [string]$_.status -and
                    [string]$_.task_id
                } |
                Sort-Object ts_utc
        )

        if ($requests.Count -eq 0) {
            Write-Host '  incoming: (none)'
        } else {
            Write-Host '  incoming:'
            $latestByTask = @{}
            foreach ($r in $requests) {
                $latestByTask[[string]$r.task_id] = $r
            }
            foreach ($taskId in ($latestByTask.Keys | Sort-Object)) {
                $req = $latestByTask[$taskId]
                $reply = @(
                    $allEvents |
                        Where-Object {
                            [string]$_.agent -eq $Agent -and
                            [string]$_.task_id -eq $taskId -and
                            [string]$_.ts_utc -gt [string]$req.ts_utc
                        } |
                        Sort-Object ts_utc |
                        Select-Object -Last 1
                )
                if ($reply.Count -gt 0) {
                    $last = $reply[-1]
                    Write-Host ("  answered {0}: request {1}/{2} -> {3}/{4}" -f `
                        $taskId, $req.type, $req.status, $last.type, $last.status)
                } else {
                    Write-Host ("  OPEN {0}: {1}/{2} from {3}: {4}" -f `
                        $taskId, $req.type, $req.status, $req.agent, $req.message) `
                        -ForegroundColor Yellow
                }
            }
        }

        $sentRequests = @(
            $allEvents |
                Where-Object {
                    $_.PSObject.Properties['to'] -and
                    [string]$_.agent -eq $Agent -and
                    [string]$_.to -and
                    [string]$_.to -ne $Agent -and
                    $requestTypes -contains [string]$_.type -and
                    $requestStatuses -contains [string]$_.status -and
                    [string]$_.task_id
                } |
                Sort-Object ts_utc
        )
        if ($sentRequests.Count -eq 0) {
            Write-Host '  outgoing: (none)'
        } else {
            Write-Host '  outgoing:'
            $sentLatestByTask = @{}
            foreach ($r in $sentRequests) {
                $sentLatestByTask[[string]$r.task_id] = $r
            }
            foreach ($taskId in ($sentLatestByTask.Keys | Sort-Object)) {
                $req = $sentLatestByTask[$taskId]
                $reply = @(
                    $allEvents |
                        Where-Object {
                            [string]$_.agent -eq [string]$req.to -and
                            [string]$_.task_id -eq $taskId -and
                            [string]$_.ts_utc -gt [string]$req.ts_utc
                        } |
                        Sort-Object ts_utc |
                        Select-Object -Last 1
                )
                if ($reply.Count -gt 0) {
                    $last = $reply[-1]
                    Write-Host ("  answered-by-{0} {1}: request {2}/{3} -> {4}/{5}" -f `
                        $req.to, $taskId, $req.type, $req.status, $last.type, $last.status)
                } else {
                    Write-Host ("  WAITING-FOR-{0} {1}: {2}/{3}: {4}" -f `
                        $req.to, $taskId, $req.type, $req.status, $req.message) `
                        -ForegroundColor Yellow
                }
            }
        }
        Write-Host ''
    }
}

if ($ShowLiveness -and -not $NoContinuity) {
    Write-Host 'AGENT LIVENESS' -ForegroundColor Cyan
    if (Test-Path -LiteralPath $eventsPath) {
        $latest = @{}
        $opens = New-Object System.Collections.Generic.List[object]
        $allLines = Get-Content -Path $eventsPath -Encoding UTF8
        foreach ($line in $allLines) {
            if (-not $line) { continue }
            try {
                $e = $line | ConvertFrom-Json
            } catch { continue }
            if ($e.type -eq 'liveness' -or $e.type -eq 'heartbeat') {
                $key = "{0}/{1}" -f $e.agent, $e.type
                $latest[$key] = $e
            }
            if ($e.type -eq 'wake_request' -and $e.status -ne 'closed') {
                $opens.Add($e) | Out-Null
            }
        }
        $now = (Get-Date).ToUniversalTime()
        foreach ($agent in @('claude','codex')) {
            foreach ($k in @('liveness','heartbeat')) {
                $key = "{0}/{1}" -f $agent, $k
                if ($latest.ContainsKey($key)) {
                    $e = $latest[$key]
                    $ts = [DateTime]::Parse($e.ts_utc).ToUniversalTime()
                    $age = ($now - $ts).TotalSeconds
                    $stale = if ($age -gt 300) { ' (STALE)' } else { '' }
                    Write-Host ("  {0,-7} {1,-10} {2}s ago{3}: {4}/{5}" -f `
                        $agent, $k, [int]$age, $stale, $e.type, $e.status)
                } else {
                    Write-Host ("  {0,-7} {1,-10} (no event)" -f $agent, $k)
                }
            }
        }
        Write-Host ''
        Write-Host 'OPEN WAKE_REQUESTS' -ForegroundColor Cyan
        if ($opens.Count -eq 0) {
            Write-Host '  (none)'
        } else {
            foreach ($e in $opens) {
                Write-Host ("  {0} [{1} -> {2}] sev={3}: {4}" -f `
                    $e.ts_utc, $e.agent, $e.to, $e.severity, $e.message)
            }
        }
        Write-Host ''
    } else {
        Write-Host '  (no events.jsonl yet)'
        Write-Host ''
    }
}

Write-Host 'RECENT EVENTS' -ForegroundColor Cyan
if (-not (Test-Path -LiteralPath $eventsPath)) {
    Write-Host '  (none)'
    exit 0
}

$lines = @(Get-Content -Path $eventsPath -Tail $Tail -Encoding UTF8)
$events = New-Object System.Collections.Generic.List[object]
foreach ($line in $lines) {
    if (-not $line) { continue }
    try {
        $e = $line | ConvertFrom-Json
        if ($OtherOnly -and $Agent -and [string]$e.agent -eq $Agent) { continue }
        [void]$events.Add($e)
    } catch {}
}

if ($Raw) {
    $events | ConvertTo-Json -Depth 12
    exit 0
}

foreach ($e in $events) {
    $scope = ''
    if ($e.PSObject.Properties['write_scope'] -and @($e.write_scope).Count -gt 0) {
        $scope = ' scope=' + ((@($e.write_scope)) -join ',')
    }
    $target = ''
    if ($e.PSObject.Properties['to'] -and [string]$e.to) {
        $target = ' -> ' + [string]$e.to
    }
    Write-Host ("{0} [{1}{2}] {3}/{4}: {5}{6}" -f $e.ts_utc, $e.agent, $target, $e.type, $e.status, $e.message, $scope)
}
