#requires -Version 5.1
<#
.SYNOPSIS
    F28 dry plan: roll back to a previously installed reboot bundle, or HOLD.

.DESCRIPTION
    Plans, never performs, a cold-switch rollback to C:\Python\wd-reboot-bundles\<sha>.
    Every check is read-only; the verdict is 'plan' only when all of them pass, else 'hold':

    * provenance: the target bundle's deployment-manifest.json must hash to the digest the
      operator supplies out of band (-ExpectedTargetManifestSha256), and its source_commit must
      equal -TargetCommit. The current pointer is read only to refuse a no-op rollback.
    * integrity: every file the target manifest lists must exist in the bundle with its hash,
      no other file may be in the bundle (deployment-manifest.json aside), and nothing in the
      bundle may be a reparse point; the walk never follows a link.
    * state compatibility: the target relay's own accepted relay statuses are read from the
      target bundle's start-wd-tools-consumer.ps1 (a regular file of at most 4 MiB); every lane
      journal given with -LaneJournals must hold a relay record the target accepts, and no
      named wake snapshot or refusal receipt that a legacy relay cannot own. Unknown means
      HOLD: an unrecognized relay contract, no -LaneJournals at all, anything at a journal's
      record path that is not a regular non-link file or that cannot be inspected (only a provably
      absent record means no record; relay_state_unreadable), or a journal that cannot be listed
      (lane_journal_unreadable) is a HOLD.
    * outstanding intents: any file at any depth in -IntentDirectory, or any link in it, is a
      HOLD; empty folders are not intents. No -IntentDirectory (intent_directory_not_given), a
      path that is not there (intent_directory_missing), or one that is not a real folder
      (intent_directory_invalid) is a HOLD too.

    The plan never restores old runtime data, never touches a lane worktree (WIP), never
    removes a bundle, and keeps Supervisor OFF and the merge-driver HOLD. A rollback is a new
    exact pair that needs its own operator signature. -Apply refuses (exit 3): this wave
    prepares source only. A failed or refused rollback is a HOLD, never a retry loop.

    Exit codes: 0 plan, 2 hold (reasons in the JSON), 3 -Apply refused.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $TargetCommit,
    [Parameter(Mandatory)] [string] $ExpectedTargetManifestSha256,
    [string] $BundlesRoot = 'C:\Python\wd-reboot-bundles',
    [string] $StatePointerPath = 'C:\Python\WD_REBOOT_STATE_CURRENT.json',
    [string[]] $LaneJournals = @(),
    [string] $IntentDirectory = '',
    [switch] $Apply
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$MaxListedReasons = 40

function Write-Plan {
    param([System.Collections.Specialized.OrderedDictionary] $Plan, [int] $Code)
    Write-Output ($Plan | ConvertTo-Json -Depth 6 -Compress)
    exit $Code
}

function Read-JsonFile {
    # Strict enough for a plan: a regular, non-link file of bounded size, parsed as one object.
    param([string] $Path, [int] $Limit = 4194304)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt $Limit) {
        throw 'not a bounded regular file'
    }
    $text = [IO.File]::ReadAllText($item.FullName, [Text.Encoding]::UTF8)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) { $text = $text.Substring(1) }
    $value = $text | ConvertFrom-Json
    if ($null -eq $value -or $value -isnot [Management.Automation.PSCustomObject]) { throw 'not a JSON object' }
    return $value
}

function Get-Sha256Hex {
    # .NET directly, not Get-FileHash: a Windows PowerShell 5.1 child that inherits a pwsh 7
    # PSModulePath cannot autoload Get-FileHash, and the hash must not depend on module paths.
    param([string] $Path)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '') } finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}
function Get-WdTreeEntry {
    # Every entry under $Root, depth first. A reparse point is returned, never descended into.
    param([string] $Root)
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $pending = New-Object 'System.Collections.Generic.Stack[IO.DirectoryInfo]'
    $pending.Push((New-Object IO.DirectoryInfo $rootFull))
    while ($pending.Count -gt 0) {
        foreach ($info in $pending.Pop().EnumerateFileSystemInfos()) {
            $isLink = ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
            [pscustomobject]@{ Full = $info.FullName; Relative = $info.FullName.Substring($rootFull.Length).Replace('\', '/')
                IsLink = $isLink; IsDirectory = $info -is [IO.DirectoryInfo] }
            if (-not $isLink -and $info -is [IO.DirectoryInfo]) { $pending.Push($info) }
        }
    }
}
function Get-ExactField {
    # Exact-case property lookup: PSObject.Properties[...] ignores case.
    param($Object, [string] $Name)
    foreach ($property in @($Object.PSObject.Properties)) { if ($property.Name -ceq $Name) { return ,$property.Value } }
    return $null
}

$plan = [ordered]@{
    schema = 'wd.bundle-rollback-plan.v1'
    authority_effect = 'none'
    applied = $false
    verdict = 'hold'
    reasons = @()
    target_commit = $TargetCommit
    target_bundle = $null
    target_manifest_sha256 = $null
    target_relay_statuses = @()
    actions = @()
    never = @('restore old runtime data', 'touch a lane worktree or its WIP', 'remove any bundle',
              'enable WD-Supervisor or release the merge-driver HOLD', 'retry a failed rollback')
}
if ($Apply) {
    $plan['reasons'] = @('apply_requires_signed_activation')
    Write-Plan $plan 3
}

$reasons = New-Object System.Collections.Generic.List[string]
function Add-Reason([string] $Reason) { if ($reasons.Count -lt $MaxListedReasons) { $reasons.Add($Reason) } }

# 1. Inputs.
if ($TargetCommit -cnotmatch '^[0-9a-f]{40}$') { Add-Reason 'target_commit_invalid' }
if ($ExpectedTargetManifestSha256 -notmatch '^[0-9A-Fa-f]{64}$') { Add-Reason 'target_manifest_digest_invalid' }
if ($reasons.Count -gt 0) { $plan['reasons'] = @($reasons); Write-Plan $plan 2 }

# 2. The current pointer: only to refuse a rollback onto the running pair.
try {
    $pointer = Read-JsonFile -Path $StatePointerPath -Limit 65536
    $current = [string](Get-ExactField $pointer 'final_commit')
    if ($current -ceq $TargetCommit) { Add-Reason 'target_is_current' }
} catch {
    Add-Reason 'state_pointer_unreadable'
}

# 3. Provenance and integrity of the target bundle.
$bundle = Join-Path $BundlesRoot $TargetCommit
$plan['target_bundle'] = $bundle
$manifestPath = Join-Path $bundle 'deployment-manifest.json'
$bundleItem = Get-Item -LiteralPath $bundle -Force -ErrorAction SilentlyContinue
if ($null -eq $bundleItem -or -not $bundleItem.PSIsContainer -or ($bundleItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    Add-Reason 'target_bundle_missing'
} elseif (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    Add-Reason 'target_manifest_missing'
} else {
    $manifestHash = Get-Sha256Hex -Path $manifestPath
    $plan['target_manifest_sha256'] = $manifestHash
    if ($manifestHash -ne $ExpectedTargetManifestSha256.ToUpperInvariant()) { Add-Reason 'target_manifest_unanchored' }
    try {
        $manifest = Read-JsonFile -Path $manifestPath
        if ([string](Get-ExactField $manifest 'source_commit') -cne $TargetCommit) { Add-Reason 'target_manifest_commit_mismatch' }
        $files = Get-ExactField $manifest 'files'
        if ($null -eq $files -or $files -isnot [Management.Automation.PSCustomObject] -or @($files.PSObject.Properties).Count -eq 0) {
            Add-Reason 'target_manifest_files_missing'
        } else {
            $bundleFull = [IO.Path]::GetFullPath($bundle).TrimEnd('\') + '\'
            $listed = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
            foreach ($entry in @($files.PSObject.Properties)) {
                $file = [IO.Path]::GetFullPath((Join-Path $bundle $entry.Name))
                [void]$listed.Add($file)
                if (-not $file.StartsWith($bundleFull, [StringComparison]::OrdinalIgnoreCase)) { Add-Reason ('bundle_file_outside:' + $entry.Name); continue }
                if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { Add-Reason ('bundle_file_missing:' + $entry.Name); continue }
                if ((Get-Sha256Hex -Path $file) -ne ([string]$entry.Value).ToUpperInvariant()) {
                    Add-Reason ('bundle_file_changed:' + $entry.Name)
                }
            }
            # Nothing unlisted and no link anywhere in the bundle; the walk never follows a link.
            try {
                foreach ($node in @(Get-WdTreeEntry -Root $bundle)) {
                    if ($node.IsLink) { Add-Reason ('bundle_reparse:' + $node.Relative); continue }
                    if ($node.IsDirectory -or $node.Relative -eq 'deployment-manifest.json') { continue }
                    if (-not $listed.Contains($node.Full)) { Add-Reason ('bundle_file_unexpected:' + $node.Relative) }
                }
            } catch {
                Add-Reason 'bundle_unenumerable'
            }
        }
    } catch {
        Add-Reason 'target_manifest_unreadable'
    }
}

# 4. State compatibility: the target relay's OWN accepted statuses, read from its source.
$accepted = @()
$relaySource = Join-Path $bundle 'start-wd-tools-consumer.ps1'
$relayItem = Get-Item -LiteralPath $relaySource -Force -ErrorAction SilentlyContinue
# A missing, linked, non-file or oversized relay source is an unknown contract.
if ($null -ne $relayItem -and -not $relayItem.PSIsContainer -and
    ($relayItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and $relayItem.Length -le 4194304) {
    $pattern = "wd\.native-tools-wake\.v1' -or \`$previous\.status -cnotin @\(([^)]*)\)"
    $match = [regex]::Match([IO.File]::ReadAllText($relayItem.FullName), $pattern)
    if ($match.Success) {
        $accepted = @([regex]::Matches($match.Groups[1].Value, "'([a-z_]+)'") | ForEach-Object { $_.Groups[1].Value })
    }
}
$plan['target_relay_statuses'] = @($accepted)
if ($accepted.Count -eq 0) { Add-Reason 'target_relay_contract_unknown' }
if ($LaneJournals.Count -eq 0) { Add-Reason 'lane_journals_not_given' }
foreach ($journal in $LaneJournals) {
    if (-not (Test-Path -LiteralPath $journal -PathType Container)) { Add-Reason ('lane_journal_missing:' + $journal); continue }
    $record = Join-Path $journal 'native-bridge-wake.json'
    # Anything at the record path is read as a regular non-link file or is unreadable state: a folder or a
    # link there is unknown, never 'no record' (claude-rco-1 F28-2). Only a provably absent record (every
    # lookup error is ItemNotFound) means 'no record'; an access failure is unknown, never a missing record.
    $recordErrors = $null
    $recordItem = Get-Item -LiteralPath $record -Force -ErrorAction SilentlyContinue -ErrorVariable recordErrors
    $recordAbsent = $null -eq $recordItem -and @($recordErrors).Count -gt 0 -and
        @($recordErrors | Where-Object { $_.Exception -isnot [Management.Automation.ItemNotFoundException] }).Count -eq 0
    if ($null -ne $recordItem) {
        try {
            $status = [string](Get-ExactField (Read-JsonFile -Path $record -Limit 32768) 'status')
            if ($accepted.Count -gt 0 -and $accepted -cnotcontains $status) { Add-Reason ('relay_state_incompatible:' + $journal + ':' + $status) }
        } catch {
            Add-Reason ('relay_state_unreadable:' + $journal)
        }
    } elseif (-not $recordAbsent) {
        Add-Reason ('relay_state_unreadable:' + $journal)
    }
    $legacyOnly = $accepted -cnotcontains 'claiming'
    # A journal the plan cannot list is unknown state: a HOLD with its reason, never a crash without a plan.
    try { $journalFiles = @(Get-ChildItem -LiteralPath $journal -Force -File -ErrorAction Stop) }
    catch { Add-Reason ('lane_journal_unreadable:' + $journal); continue }
    foreach ($item in $journalFiles) {
        $name = $item.Name
        if ($name.StartsWith('native-bridge-wake.json.wake.legacy-', [StringComparison]::Ordinal)) { continue }  # operator-owned, never sent
        if ($name.StartsWith('native-bridge-wake.json.wake.', [StringComparison]::Ordinal) -and $legacyOnly) {
            Add-Reason ('named_snapshot_incompatible:' + $journal + ':' + $name)
        } elseif ($name.StartsWith('native-bridge-wake.json.refusal-', [StringComparison]::Ordinal) -and $legacyOnly) {
            Add-Reason ('refusal_receipt_incompatible:' + $journal + ':' + $name)
        }
    }
}

# 5. Outstanding intents. Not given or not there is unknown, so a HOLD, as for lane journals (claude-rco-1 F28-1).
if (-not $IntentDirectory) {
    Add-Reason 'intent_directory_not_given'
} else {
    $intentItem = Get-Item -LiteralPath $IntentDirectory -Force -ErrorAction SilentlyContinue
    if ($null -eq $intentItem) {
        Add-Reason 'intent_directory_missing'
    } elseif ($intentItem.PSIsContainer -and ($intentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
        try {
            $intentEntries = @(Get-WdTreeEntry -Root $IntentDirectory)
            if (@($intentEntries | Where-Object { $_.IsLink -or -not $_.IsDirectory }).Count -gt 0) { Add-Reason 'outstanding_intents' }
        } catch {
            Add-Reason 'intent_directory_invalid'
        }
    } else {
        Add-Reason 'intent_directory_invalid'
    }
}

$plan['reasons'] = @($reasons)
if ($reasons.Count -gt 0) { Write-Plan $plan 2 }

$plan['verdict'] = 'plan'
$plan['actions'] = @(
    'Obtain the operator signature for the exact pair (target commit, target manifest sha256); an approval of another pair never carries over.',
    'Fence every lane (cold switch): no lane may keep running with the current generation pins while the pointer changes.',
    ('Reinstall from a worktree at ' + $TargetCommit + ' with Deploy-WdRebootBundle.ps1 -ExpectedFinalCommit ' + $TargetCommit + ' -ExpectedFinalManifestHash ' + $plan['target_manifest_sha256'] + ' (machine wrappers and the state pointer only).'),
    'Verify the pointer and every wrapper pin, then a non-elevated start-wd-all.ps1 -Auto; any failure is a HOLD, never a retry loop.'
)
Write-Plan $plan 0
