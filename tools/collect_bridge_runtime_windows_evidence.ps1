#requires -Version 5.1
# SPDX-License-Identifier: BUSL-1.1
<#
.SYNOPSIS
    Read-only A/B collector for bridge runtime deployment attestation.

.DESCRIPTION
    Reads local Win32_Process state through System.Management and recursively
    reads Task Scheduler definitions through the Schedule.Service COM API.
    It performs no file writes and does not start, stop, register, enable,
    disable, or update a process or Scheduled Task. Output is one JSON object
    written to stdout only after both complete samples have been collected.
#>
[CmdletBinding()]
param(
    [ValidateRange(50, 5000)]
    [int] $SampleGapMilliseconds = 500
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-StrictMode -Version Latest

function Convert-WmiDateToUtc {
    param([AllowNull()] [object] $Value)

    if ($null -eq $Value -or -not [string]$Value) {
        return $null
    }
    $local = [System.Management.ManagementDateTimeConverter]::ToDateTime(
        [string]$Value
    )
    return $local.ToUniversalTime().ToString('o')
}

function Get-CollectorHost {
    $machineGuid = [string](Get-ItemPropertyValue -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name 'MachineGuid' -ErrorAction Stop)
    $productSearcher = New-Object System.Management.ManagementObjectSearcher(
        'SELECT UUID FROM Win32_ComputerSystemProduct'
    )
    $product = @($productSearcher.Get())
    if ($product.Count -ne 1 -or -not [string]$product[0].UUID) {
        throw 'could not obtain one SMBIOS UUID'
    }

    $osSearcher = New-Object System.Management.ManagementObjectSearcher(
        'SELECT LastBootUpTime FROM Win32_OperatingSystem'
    )
    $operatingSystems = @($osSearcher.Get())
    if ($operatingSystems.Count -ne 1) {
        throw 'could not obtain one operating-system record'
    }

    $systemDrive = [string]$env:SystemDrive
    if (-not $systemDrive) {
        throw 'SystemDrive is unavailable'
    }
    $escapedDrive = $systemDrive.Replace('\', '\\').Replace("'", "''")
    $volumeSearcher = New-Object System.Management.ManagementObjectSearcher(
        "SELECT VolumeSerialNumber FROM Win32_LogicalDisk WHERE DeviceID='$escapedDrive'"
    )
    $volumes = @($volumeSearcher.Get())
    if ($volumes.Count -ne 1 -or -not [string]$volumes[0].VolumeSerialNumber) {
        throw 'could not obtain the system volume serial'
    }

    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($null -eq $identity.User) {
        throw 'collector account SID is unavailable'
    }
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    $administrator = [Security.Principal.WindowsBuiltInRole]::Administrator

    return [ordered]@{
        machine_guid         = $machineGuid
        smbios_uuid          = [string]$product[0].UUID
        system_volume_serial = [string]$volumes[0].VolumeSerialNumber
        boot_time_utc        = Convert-WmiDateToUtc $operatingSystems[0].LastBootUpTime
        collector_sid        = [string]$identity.User.Value
        is_elevated          = [bool]$principal.IsInRole($administrator)
    }
}

function Get-ProcessOwnerSid {
    param([Parameter(Mandatory)] [System.Management.ManagementObject] $Process)

    try {
        $result = $Process.InvokeMethod('GetOwnerSid', $null, $null)
        if ($null -ne $result -and [int]$result.ReturnValue -eq 0) {
            return [ordered]@{
                sid   = [string]$result.Sid
                error = $null
            }
        }
    } catch {
        return [ordered]@{
            sid   = $null
            error = [string]$_.Exception.Message
        }
    }
    return [ordered]@{
        sid   = $null
        error = 'GetOwnerSid returned no successful result'
    }
}

function Get-AllProcesses {
    $query = @'
SELECT ProcessId, ParentProcessId, CreationDate, ExecutablePath, CommandLine
FROM Win32_Process
'@
    $searcher = New-Object System.Management.ManagementObjectSearcher($query)
    $records = New-Object System.Collections.Generic.List[object]
    foreach ($process in @($searcher.Get())) {
        $owner = Get-ProcessOwnerSid -Process $process
        [void]$records.Add([ordered]@{
            pid               = [int]$process.ProcessId
            parent_pid        = [int]$process.ParentProcessId
            creation_time_utc = Convert-WmiDateToUtc $process.CreationDate
            executable_path   = if ($null -eq $process.ExecutablePath) {
                $null
            } else {
                [string]$process.ExecutablePath
            }
            command_line      = if ($null -eq $process.CommandLine) {
                $null
            } else {
                [string]$process.CommandLine
            }
            owner_sid         = $owner.sid
            owner_error       = $owner.error
        })
    }
    return @($records.ToArray())
}

function Read-TaskFolder {
    param([Parameter(Mandatory)] [object] $Folder)

    $records = New-Object System.Collections.Generic.List[object]
    foreach ($task in @($Folder.GetTasks(1))) {
        $definition = $task.Definition
        $actions = New-Object System.Collections.Generic.List[object]
        foreach ($action in @($definition.Actions)) {
            [void]$actions.Add([ordered]@{
                type              = [int]$action.Type
                path              = if ($action.PSObject.Properties['Path']) {
                    [string]$action.Path
                } else {
                    ''
                }
                arguments         = if ($action.PSObject.Properties['Arguments']) {
                    [string]$action.Arguments
                } else {
                    ''
                }
                working_directory = if (
                    $action.PSObject.Properties['WorkingDirectory']
                ) {
                    [string]$action.WorkingDirectory
                } else {
                    ''
                }
            })
        }
        $principalId = [string]$definition.Principal.UserId
        if (-not $principalId) {
            $principalId = [string]$definition.Principal.GroupId
        }
        [void]$records.Add([ordered]@{
            task_path      = [string]$Folder.Path
            task_name      = [string]$task.Name
            enabled        = [bool]$task.Enabled
            state          = [int]$task.State
            principal_sid  = $principalId
            run_level      = [string]$definition.Principal.RunLevel
            actions        = @($actions.ToArray())
            definition_xml = [string]$task.Xml
        })
    }
    foreach ($child in @($Folder.GetFolders(0))) {
        foreach ($record in @(Read-TaskFolder -Folder $child)) {
            [void]$records.Add($record)
        }
    }
    return @($records.ToArray())
}

function Get-AllScheduledTasks {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $root = $service.GetFolder('\')
    return @(Read-TaskFolder -Folder $root)
}

function Get-OneSample {
    param([Parameter(Mandatory)] [ValidateSet('A', 'B')] [string] $Label)

    $hostRecord = Get-CollectorHost
    $processRecords = @(Get-AllProcesses)
    $taskRecords = @(Get-AllScheduledTasks)
    return [ordered]@{
        label           = $Label
        captured_at_utc = [DateTime]::UtcNow.ToString('o')
        monotonic_ticks = [Diagnostics.Stopwatch]::GetTimestamp()
        host            = $hostRecord
        processes       = $processRecords
        scheduled_tasks = $taskRecords
    }
}

$outerStartUtc = [DateTime]::UtcNow.ToString('o')
$outerStartTicks = [Diagnostics.Stopwatch]::GetTimestamp()
$sampleA = Get-OneSample -Label 'A'
Start-Sleep -Milliseconds $SampleGapMilliseconds
$sampleB = Get-OneSample -Label 'B'
$outerEndTicks = [Diagnostics.Stopwatch]::GetTimestamp()
$outerEndUtc = [DateTime]::UtcNow.ToString('o')

$result = [ordered]@{
    schema                       = 'wd.bridge_runtime.windows_raw.v2'
    collector_pid                = $PID
    stopwatch_frequency          = [Diagnostics.Stopwatch]::Frequency
    collector_started_at_utc     = $outerStartUtc
    collector_completed_at_utc   = $outerEndUtc
    collector_started_ticks      = $outerStartTicks
    collector_completed_ticks    = $outerEndTicks
    samples                      = @($sampleA, $sampleB)
}
$json = $result | ConvertTo-Json -Depth 16 -Compress
[Console]::Out.Write($json)
