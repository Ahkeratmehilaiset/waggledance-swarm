#requires -Version 5.1
<#
.SYNOPSIS
    Answer ONE exact request by its request_id (F11).

.DESCRIPTION
    The caller names the request by id. This wrapper finds the request event itself with the
    pinned reader in this directory (Read-AgentBridge.ps1 -Raw -NoAckReceived -NoContinuity)
    and hands that exact event to Write-BridgeTaskReply.ps1 in this directory, so no agent
    assembles or edits a -RequestEventJson by hand; there is no parameter that accepts one.

    It refuses, before anything is written:
      - a malformed agent or request id (exact, case-sensitive patterns);
      - an id that no event in the scanned window carries (widen -Tail);
      - two or more DIFFERENT events carrying the id (a replayed identical copy is one request);
      - a request that is not addressed to -Agent;
      - an event that is itself a reply (it carries in_reply_to_request_id).

    Binding, nonce, result contract and execution evidence stay with Write-BridgeTaskReply.ps1
    and the pinned writer. A supersede or cancel is a separate event, never an edit here.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Agent,
    [Parameter(Mandatory)] [string] $RequestId,
    [Parameter(Mandatory)] [string] $ResultJson,
    [string] $Message = 'Task result supplied; see validation scope and evidence.',
    [string] $Status = 'answered',
    [ValidateRange(1, 20000)] [int] $Tail = 4000,
    [switch] $ReceiptJson
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ValidatePattern is case-insensitive, so the identifiers are checked with -cnotmatch.
if ($Agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}\z') { throw 'Reply-ToRequest: agent id malformed' }
if ($RequestId -cnotmatch '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\z') { throw 'Reply-ToRequest: request id malformed' }

$lines = @(& (Join-Path $PSScriptRoot 'Read-AgentBridge.ps1') -Agent $Agent -Raw -NoAckReceived -NoContinuity -Tail $Tail)
$text = ($lines | ForEach-Object { [string]$_ }) -join "`n"
$jsonArgs = @{ ErrorAction = 'Stop' }
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonArgs.DateKind = 'String' }
# Piping through ForEach-Object enumerates the array on Windows PowerShell 5.1 too.
$events = @(($text | ConvertFrom-Json @jsonArgs) | ForEach-Object { $_ })

function Get-ExactField {
    # PSObject.Properties[$name] and $object.$name ignore case; a contract field is matched exactly.
    param($Object, [string] $Name)
    if ($null -eq $Object) { return $null }
    foreach ($property in $Object.PSObject.Properties) { if ($property.Name -ceq $Name) { return [string]$property.Value } }
    return $null
}

function Get-JsonNestingDepth {
    # Containers only: each object or array is one level, a scalar adds none.
    param($Value)
    if ($null -eq $Value -or $Value -is [string] -or $Value -is [ValueType]) { return 0 }
    $deepest = 0
    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        foreach ($property in $Value.PSObject.Properties) {
            $depth = Get-JsonNestingDepth $property.Value
            if ($depth -gt $deepest) { $deepest = $depth }
        }
    } elseif ($Value -is [System.Collections.IEnumerable]) {
        foreach ($item in $Value) {
            $depth = Get-JsonNestingDepth $item
            if ($depth -gt $deepest) { $deepest = $depth }
        }
    } else {
        return 0
    }
    return 1 + $deepest
}

# C-F1 (RCO1 2026-09-30; Fable review 99897de5): the reader's -Raw view is ConvertTo-Json -Depth 12, which
# replaces anything nested deeper with its string form (Windows PowerShell silently, pwsh with a warning).
# Measured in both shells: an event 13 containers deep (the event itself counts one) comes back intact, a
# deeper one comes back exactly 13 deep with strings in place of its deepest containers. A reply binds the
# request as logged, so a request that reaches this depth is refused, never bound to a changed copy.
$script:ReaderRawIntactDepth = 13

$candidates = @($events | Where-Object { (Get-ExactField $_ 'request_id') -ceq $RequestId })
if ($candidates.Count -eq 0) {
    throw ('Reply-ToRequest: no event carries request id ' + $RequestId + ' in the last ' + $Tail + ' events')
}
$distinct = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::Ordinal)
foreach ($candidate in $candidates) { [void]$distinct.Add(($candidate | ConvertTo-Json -Depth 64 -Compress)) }
if ($distinct.Count -ne 1) {
    throw ('Reply-ToRequest: ' + $distinct.Count + ' different events carry request id ' + $RequestId + '; refusing an ambiguous reply')
}
$request = $candidates[0]
if ((Get-JsonNestingDepth $request) -ge $script:ReaderRawIntactDepth) {
    throw ('Reply-ToRequest: request ' + $RequestId + ' is nested ' + $script:ReaderRawIntactDepth +
        ' or more levels deep, where the reader view may have replaced values; it cannot be bound exactly here')
}
if (Get-ExactField $request 'in_reply_to_request_id') {
    throw ('Reply-ToRequest: the event carrying ' + $RequestId + ' is itself a reply')
}
$addressees = @(([string](Get-ExactField $request 'to')).Split(',') | ForEach-Object { $_.Trim() })
if ($addressees -cnotcontains $Agent) {
    throw ('Reply-ToRequest: request ' + $RequestId + ' is not addressed to ' + $Agent)
}

& (Join-Path $PSScriptRoot 'Write-BridgeTaskReply.ps1') -Agent $Agent -RequestEventJson ($request | ConvertTo-Json -Depth 64 -Compress) `
    -ResultJson $ResultJson -Message $Message -Status $Status -ReceiptJson:$ReceiptJson
