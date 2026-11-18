param([Parameter(Mandatory)][string]$HelperPath)
$ErrorActionPreference = 'Stop'
. $HelperPath
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
