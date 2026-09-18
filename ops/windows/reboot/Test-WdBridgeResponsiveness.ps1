#requires -Version 5.1
[CmdletBinding()]
param(
    [string] $RuntimeRoot = 'C:\Python\project2-master\.agent-bridge',
    [string] $RuntimeDirectory = 'C:\Python\wd-reboot-runtime',
    [string] $BridgeBin = '',
    [string] $ReportPath = '',
    [ValidateRange(1, 600)] [int] $TimeoutSeconds = 300
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Test-WdProbeReply {
    param($Event, $Request, $Identity, [datetime] $Deadline)
    try {
        if ($Request.PSObject.Properties['event'] -and (Get-BridgeContractField $Request.event 'request_id') -and
            -not (Test-BridgeReplyBinding $Request.event $Event ([string]$Request.agent))) { return $false }
        return (
            [string]$Event.agent -ceq [string]$Request.agent -and
            [string]$Event.agent_uuid -ceq [string]$Identity.agent_uuid -and
            [string]$Event.session_id -ceq [string]$Identity.session_id -and
            [string]$Event.run_id -ceq [string]$Identity.run_id -and
            [string]$Event.type -ceq 'message' -and
            [string]$Event.status -ceq 'fleet_probe_pass' -and
            [string]$Event.to -ceq 'operator' -and
            [string]$Event.task_id -ceq [string]$Request.task_id -and
            [string]$Event.payload.nonce -ceq [string]$Request.nonce -and
            [string]$Event.payload.token -ceq [string]$Request.token -and
            [string]$Event.payload.request_stamp -ceq [string]$Request.request_stamp -and
            $Event.payload.sum -is [ValueType] -and $Event.payload.sum -isnot [bool] -and
            [double]$Event.payload.sum -eq [double]$Request.sum -and
            ([datetime]$Event.ts_utc).ToUniversalTime() -ge ([datetime]$Request.sent_at).ToUniversalTime() -and
            ([datetime]$Event.ts_utc).ToUniversalTime() -le $Deadline.ToUniversalTime()
        )
    } catch { return $false }
}

function Get-WdProbeIdentities {
    param([string] $Directory, [string] $Bin)
    $registry = Get-Content (Join-Path $Bin '..\..\configs\bridge_identity_registry.json') -Raw | ConvertFrom-Json
    $identities = @{}
    $records = @(Get-ChildItem (Join-Path $Directory 'handshakes') -Filter '*.json' -Recurse -File |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 200)
    foreach ($agent in @('claude-rco-1','claude-rco-2','fable-5','codex-tools-1')) {
        $candidates = if ($agent -ceq 'codex-tools-1') {
            @(Get-Item (Join-Path $Directory 'codex-tools-1-ready.json'))
        } else { @($records | Where-Object Name -CEQ ($agent + '.json')) }
        foreach ($file in $candidates) {
            $record = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json
            if ([string]$record.agent -cne $agent -or
                [string]$record.agent_uuid -cne [string]$registry.identities.$agent -or
                -not [string]$record.session_id -or -not [string]$record.run_id) { continue }
            $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.pid)
            $marker = if ($agent -ceq 'codex-tools-1') { 'start-wd-tools-consumer.ps1' } else { $agent }
            if ($null -eq $process -or [string]$process.CommandLine -notlike ('*' + $marker + '*')) { continue }
            $created = ([datetime]$process.CreationDate).ToUniversalTime()
            $recorded = if ($record.PSObject.Properties['process_start_utc']) {
                ([datetime]$record.process_start_utc).ToUniversalTime()
            } else { ([datetime]$record.created_at_utc).ToUniversalTime() }
            if ($created -gt $recorded.AddSeconds(1) -or ($recorded - $created).TotalMinutes -gt 15) { continue }
            $identities[$agent] = $record
            break
        }
        if (-not $identities.ContainsKey($agent)) { throw "No live session identity for probe: $agent" }
    }
    return $identities
}

function Invoke-WdBridgeResponsiveness {
    param([string] $Root, [string] $Directory, [string] $Bin, [string] $Report, [int] $Timeout)
    $identities = Get-WdProbeIdentities -Directory $Directory -Bin $Bin
    . (Join-Path $Bin 'BridgeIncrementalReader.ps1')
    . (Join-Path $Bin 'BridgeRequestContract.ps1')
    $events = Join-Path $Root 'shared\events.jsonl'
    Write-Host '  Validating the complete retained bridge history before response probes...'
    $history = Read-BridgeEventSnapshot -Path $events -MaxBytes 268435456
    if ($history.status -notin @('OK','IDLE')) { throw "Full bridge history failed validation: $($history.reason)" }
    $historyCount = @($history.rows).Count
    $history = $null
    $baseline = Read-BridgeEventTail -Path $events -MaxLines 1
    if ($baseline.status -notin @('OK','IDLE')) { throw "Probe reader blocked: $($baseline.reason)" }
    $cursor = $baseline.candidate_cursor
    $probeId = 'bridge-release-' + [guid]::NewGuid().ToString('N')
    $requests = @{}
    $answers = @{}
    $rejected = @{}
    $savedRoot = $env:AGENT_BRIDGE_RUNTIME_ROOT
    $env:AGENT_BRIDGE_RUNTIME_ROOT = $Root
    try {
        foreach ($agent in @($identities.Keys | Sort-Object)) {
            $nonce = [guid]::NewGuid().ToString('N')
            $stamp = 'utc:' + [datetime]::UtcNow.ToString('o')
            $a = Get-Random -Minimum 10 -Maximum 80
            $b = Get-Random -Minimum 10 -Maximum 80
            $request = [pscustomobject]@{agent=$agent;task_id=($probeId + '/' + $agent);nonce=$nonce;
                token=[guid]::NewGuid().ToString('N');request_stamp=$stamp;sum=($a+$b);sent_at=[datetime]::UtcNow}
            $payload = @{schema='wd.bridge-functionality-request.v1';nonce=$nonce;token=$request.token;
                request_stamp=$stamp;numbers=@($a,$b)} | ConvertTo-Json -Compress
            $message = 'Operator-authorized bridge startup/release check, one read-only bounded reply. ' +
                'Read this exact full message AND payload using your pinned Read-AgentBridge.ps1 -Raw -NoAckReceived -NoContinuity -Tail 1200. ' +
                'Reply ONCE to operator on this exact task with Type message, Status fleet_probe_pass, actual lane UUID/session/run, ' +
                'When this request has request_id, pass the full verified request to Write-AgentEvent.ps1 -ReplyToEventJson ($request | ConvertTo-Json -Depth 32 -Compress). ' +
                'PayloadJson must have keys nonce, token, request_stamp, sum. Copy nonce, token and request_stamp BYTE-FOR-BYTE from this request payload; ' +
                'do not add echo: or any other prefix. The numeric result goes under the exact key sum (not numbers_sum). Compute sum from payload.numbers. ' +
                'This grants no source edits, claim takeover, scheduler changes, release or merge authority. ' +
                'Do not answer with a generic ACK. A pending idle timer must not defer this new request.'
            $delivery = & (Join-Path $Bin 'Write-AgentEvent.ps1') -Agent operator -Role operator `
                -RunId $probeId -SessionId $probeId -Type wake_request -Status request -To $agent `
                -TaskId $request.task_id -Message $message -PayloadJson $payload -ReceiptJson | ConvertFrom-Json
            if (-not $delivery._bridge_delivery.canonical_durable) { throw "Probe request not canonical for $agent" }
            $request | Add-Member NoteProperty event $delivery
            $requests[$agent] = $request
        }
        $deadline = [datetime]::UtcNow.AddSeconds($Timeout)
        $nextProgress = [datetime]::UtcNow
        do {
            $delta = Read-BridgeEventDelta -Path $events -Cursor $cursor
            if ($delta.status -in @('RETRY','BLOCKED')) { throw "Probe reader failed: $($delta.reason)" }
            foreach ($event in @($delta.rows)) {
                $agent = [string]$event.agent
                if ($requests.ContainsKey($agent) -and
                    (Test-WdProbeReply -Event $event -Request $requests[$agent] -Identity $identities[$agent] -Deadline $deadline)) {
                    if ($answers.ContainsKey($agent)) { throw "Duplicate probe response from $agent" }
                    $answers[$agent] = $event
                } elseif ($requests.ContainsKey($agent) -and
                    [string]$event.task_id -ceq [string]$requests[$agent].task_id -and
                    [string]$event.type -ceq 'message') {
                    $rejected[$agent] = $event
                    Write-Host "  Reply from $agent rejected: identity, correlation, time or payload did not match the request."
                }
            }
            if ($null -ne $delta.candidate_cursor) { $cursor = $delta.candidate_cursor }
            if ([datetime]::UtcNow -ge $nextProgress) {
                Write-Host ("  Bridge response proof: {0}/4; pending={1}" -f $answers.Count, (($requests.Keys | Where-Object { -not $answers.ContainsKey($_) }) -join ', '))
                $nextProgress = [datetime]::UtcNow.AddSeconds(30)
            }
            if ($answers.Count -eq 4) { break }
            Start-Sleep -Seconds 2
        } while ([datetime]::UtcNow -lt $deadline)
        $result = [ordered]@{schema='wd.bridge-functionality.v1';probe_id=$probeId;created_at_utc=[datetime]::UtcNow.ToString('o');
            passed=($answers.Count -eq 4);history_validated_rows=$historyCount;scope='four worker lanes; Lead launch identity is checked separately';
            requests=$requests;answers=$answers;rejected_replies=$rejected;missing=@($requests.Keys | Where-Object { -not $answers.ContainsKey($_) })}
        $result | ConvertTo-Json -Depth 18 | Set-Content -LiteralPath $Report -Encoding UTF8
        if (-not $result.passed) { throw "Bridge functional readiness failed; report=$Report" }
        return $result
    } finally { $env:AGENT_BRIDGE_RUNTIME_ROOT = $savedRoot }
}

if ($MyInvocation.InvocationName -ne '.') {
    if (-not $BridgeBin) { $BridgeBin = Join-Path $PSScriptRoot 'tools-bootstrap\.agent-bridge\bin' }
    if (-not $ReportPath) { $ReportPath = Join-Path $RuntimeDirectory 'bridge-functionality-current.json' }
    @{schema='wd.bridge-functionality.v1';passed=$false;status='checking';created_at_utc=[datetime]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    try {
        Invoke-WdBridgeResponsiveness -Root $RuntimeRoot -Directory $RuntimeDirectory -Bin $BridgeBin `
            -Report $ReportPath -Timeout $TimeoutSeconds | Out-Null
    } catch {
        $failure = Get-Content -LiteralPath $ReportPath -Raw | ConvertFrom-Json
        $failure | Add-Member -Force NoteProperty passed $false
        $failure | Add-Member -Force NoteProperty error $_.Exception.Message
        $failure | ConvertTo-Json -Depth 18 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
        throw
    }
}
