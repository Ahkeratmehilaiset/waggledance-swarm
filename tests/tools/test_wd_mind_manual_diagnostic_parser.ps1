param([Parameter(Mandatory)][string]$HelperPath, [switch]$SelfTestV2, [switch]$SelfTestAdmission)
$ErrorActionPreference = 'Stop'
. $HelperPath
if ($SelfTestAdmission) {
    # These exact bytes are also asserted by the Python guard tests. The real
    # parser receives synthetic completed reads, never a child process.
    $status='{"authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED",'+
        '"manifest_sha256":"'+('a'*64)+'","schema":"wd.mind-local-owner-status.v1",'+
        '"source_authentication":"UNKNOWN","state":"HOLD"}'
    $v1='{"authority":"none","exception_type":"LaunchRefused","reason":"ADMISSION",'+
        '"schema":"wd.mind-local-owner-start-diagnostic.v1"}'
    $v2='{"authority":"none","exception_type":"LaunchRefused","reason":"ADMISSION",'+
        '"schema":"wd.mind-local-owner-start-diagnostic.v2","worker_exit_code":null,'+
        '"worker_exit_stage_hint":"UNKNOWN"}'
    foreach ($mode in @('off','v1','v2')) {
        $wire = if ($mode -ceq 'off') { '' } elseif ($mode -ceq 'v1') { $status+"`n"+$v1+"`n" } else { $status+"`n"+$v2+"`n" }
        $buffer=[Text.Encoding]::ASCII.GetBytes($wire)
        $out=New-Object IO.MemoryStream
        $err=New-Object IO.MemoryStream
        $dummy=[pscustomobject]@{HasExited=$true;ExitCode=2;
            StandardOutput=[pscustomobject]@{BaseStream=$out};StandardError=[pscustomobject]@{BaseStream=$err}}
        $record=@{Process=$dummy;Manifest=('a'*64);State='STARTING';ReportedState='';Stopping=$false;Invalid=$false;
            DiagnosticsEnabled=($mode -cne 'off');WorkerDiagnosticsEnabled=($mode -ceq 'v2');DiagnosticCount=0;StartDiagnostic=$null;
            OutBuffer=$buffer;ErrBuffer=(New-Object byte[] 8);Line=(New-Object IO.MemoryStream);
            OutRead=[Threading.Tasks.Task]::FromResult([int]$buffer.Length);ErrRead=[Threading.Tasks.Task]::FromResult([int]0);
            OutDone=$false;ErrDone=$false}
        Update-WdMindManualOwner $record
        if ($record.Invalid) { throw ('admission parser rejected: '+$mode) }
        if ($mode -cne 'off') {
            if ($record.State -cne 'HOLD' -or $record.OutDone -or -not $record.ErrDone) { throw ('premature exit: '+$mode) }
            Update-WdMindManualOwner $record
        }
        if ($record.Invalid -or -not $record.OutDone -or -not $record.ErrDone -or $record.State -cne 'FAILED') {
            throw ('EOF or exit handling: '+$mode)
        }
        if ($mode -ceq 'off') {
            if ($null -ne $record.StartDiagnostic) { throw 'unexpected diagnostic' }
        } elseif ($record.ReportedState -cne 'HOLD' -or $record.StartDiagnostic.reason -cne 'ADMISSION' -or
                  $record.StartDiagnostic.exception_type -cne 'LaunchRefused' -or
                  $record.StartDiagnostic.schema -cne ('wd.mind-local-owner-start-diagnostic.'+$mode)) {
            throw ('admission diagnostic mismatch: '+$mode)
        }
    }
    Write-Output 'admission parser: off/v1/v2, EOF, exit 2 passed'
    return
}
if ($SelfTestV2) {
    # No child is started: update the real parser with completed synthetic
    # MemoryStream tasks in this PowerShell session.
    $cases = @(
        @{name='known';hint='model_load';number='null';enabled=$true;valid=$true},
        @{name='unknown_bootstrap';hint='UNKNOWN';number='2';enabled=$true;valid=$true},
        @{name='unapproved';hint='model_load';number='null';enabled=$false;valid=$false},
        @{name='contradictory';hint='UNKNOWN';number='20';enabled=$true;valid=$false}
    )
    foreach ($case in $cases) {
        $status='{"authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED",'+
            '"manifest_sha256":"'+('a'*64)+'","schema":"wd.mind-local-owner-status.v1",'+
            '"source_authentication":"UNKNOWN","state":"HOLD"}'
        $diag='{"authority":"none","exception_type":"FullRuntimeRefused","reason":"HANDLER_BUILD",'+
            '"schema":"wd.mind-local-owner-start-diagnostic.v2","worker_exit_code":'+$case.number+
            ',"worker_exit_stage_hint":"'+$case.hint+'"}'
        $buffer=[Text.Encoding]::ASCII.GetBytes($status+"`n"+$diag+"`n")
        $memory=New-Object IO.MemoryStream
        $dummy=[pscustomobject]@{HasExited=$false;StandardOutput=[pscustomobject]@{BaseStream=$memory}}
        $record=@{Process=$dummy;Manifest=('a'*64);State='STARTING';ReportedState='';Stopping=$false;Invalid=$false;
            DiagnosticsEnabled=$true;WorkerDiagnosticsEnabled=$case.enabled;DiagnosticCount=0;StartDiagnostic=$null;
            OutBuffer=$buffer;ErrBuffer=(New-Object byte[] 8);Line=(New-Object IO.MemoryStream);
            OutRead=[Threading.Tasks.Task]::FromResult([int]$buffer.Length);ErrRead=$null;OutDone=$false;ErrDone=$false}
        Update-WdMindManualOwner $record
        if ($record.Invalid -ne (-not $case.valid)) { throw ('v2 parser case failed: '+$case.name) }
        if ($case.valid -and ($record.StartDiagnostic.worker_exit_stage_hint -cne $case.hint -or
            $record.StartDiagnostic.schema -cne 'wd.mind-local-owner-start-diagnostic.v2')) {
            throw ('v2 parser result failed: '+$case.name)
        }
        if ($case.valid -and $case.number -cne 'null' -and
            [long]$record.StartDiagnostic.worker_exit_code -ne [long]$case.number) {
            throw ('v2 parser number failed: '+$case.name)
        }
    }
    Write-Output ('v2 parser: '+$cases.Count+' synthetic cases passed')
    return
}
$inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
$buffer = [Text.Encoding]::ASCII.GetBytes([string]$inputData.wire)
$memory = New-Object IO.MemoryStream
$process = [pscustomobject]@{HasExited=$false;StandardOutput=[pscustomobject]@{BaseStream=$memory}}
$record = @{Process=$process;Manifest=('a'*64);State='STARTING';ReportedState='';Stopping=$false;Invalid=$false;
    DiagnosticsEnabled=[bool]$inputData.optin;
    OutBuffer=$buffer;ErrBuffer=(New-Object byte[] 8);Line=(New-Object IO.MemoryStream);
    OutRead=[Threading.Tasks.Task]::FromResult([int]$buffer.Length);ErrRead=$null;OutDone=$false;ErrDone=$false}
if ($inputData.legacyRecord) { $record.Remove('DiagnosticsEnabled') }
$token = 'b'*32
$global:WdMindManualOwnerRecords = @{}
$global:WdMindManualOwnerRecords[$token] = $record
$owner = [pscustomobject]@{Token=$token}
$status = Get-WdMindManualOwnerStatus -Owner $owner
$diagnostic = $null
if ($inputData.optin -and -not $record.Invalid) {
    $diagnostic = Get-WdMindManualOwnerStartDiagnostic -Owner $owner
}
[pscustomobject]@{status=$status;diagnostic=$diagnostic;invalid=$record.Invalid;reported=$record.ReportedState} |
    ConvertTo-Json -Compress -Depth 8
