#requires -Version 5.1

$script:WdMindSharedOwnerHelperText = $MyInvocation.MyCommand.ScriptBlock.Ast.Extent.Text

function Read-WdMindSharedPinnedBytes {
    param([string] $Path, [string] $Sha256, [int] $MaxBytes)
    if (-not [IO.Path]::IsPathRooted($Path) -or $Sha256 -cnotmatch '\A[0-9a-fA-F]{64}\z') { throw 'mind_shared_pin' }
    $pathFull = [IO.Path]::GetFullPath($Path)
    $current = $pathFull
    while ($current) {
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'mind_shared_path' }
        $parent = [IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break }
        $current = $parent
    }
    $stream = [IO.File]::Open($pathFull,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $MaxBytes) { throw 'mind_shared_size' }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $offset = 0
        while ($offset -lt $bytes.Length) {
            $n = $stream.Read($bytes,$offset,$bytes.Length-$offset)
            if ($n -le 0) { throw 'mind_shared_read' }
            $offset += $n
        }
        if ($stream.ReadByte() -ne -1) { throw 'mind_shared_changed' }
    } finally { $stream.Dispose() }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actual -cne $Sha256.ToLowerInvariant()) { throw 'mind_shared_pin' }
    return ,$bytes
}

function Get-WdMindSharedOwnerRecord {
    param([Parameter(Mandatory)] $Owner)
    if ($null -eq $Owner -or $Owner.Token -cnotmatch '\A[0-9a-f]{32}\z' -or
        -not (Test-Path variable:global:WdMindSharedOwnerRecords) -or
        -not $global:WdMindSharedOwnerRecords.ContainsKey([string]$Owner.Token)) { throw 'mind_shared_owner_refused' }
    return $global:WdMindSharedOwnerRecords[[string]$Owner.Token]
}

function Update-WdMindSharedOwner {
    param([Parameter(Mandatory)] $Record)
    try {
        foreach ($which in @('Out','Err')) {
            $task=$Record[($which+'Read')]
            if ($null -eq $task -or -not $task.IsCompleted) { continue }
            $count=$task.GetAwaiter().GetResult()
            if ($count -eq 0) { $Record[($which+'Done')]=$true; $Record[($which+'Read')]=$null; continue }
            if ($which -ceq 'Err') { throw 'mind_shared_stderr' }
            $buffer=$Record.OutBuffer
            for($i=0;$i -lt $count;$i++) {
                if ($buffer[$i] -eq 10) {
                    if ($Record.Line.Length -eq 0 -or $Record.Line.Length -gt 1024) { throw 'mind_shared_status' }
                    $lineBytes=$Record.Line.ToArray()
                    $lineLength=$lineBytes.Length
                    if($lineLength -gt 0 -and $lineBytes[$lineLength-1] -eq 13){$lineLength--}
                    $line=[Text.Encoding]::ASCII.GetString($lineBytes,0,$lineLength)
                    $Record.Line.SetLength(0)
                    $matched=$false
                    foreach($state in @('STARTING','READY','HOLD','STOPPED')) {
                        $expected='{"authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED",'+
                            '"manifest_sha256":"'+$Record.Manifest+'","schema":"wd.mind-local-owner-status.v1",'+
                            '"source_authentication":"UNKNOWN","state":"'+$state+'"}'
                        if($line -ceq $expected){$Record.ReportedState=$state;$matched=$true;break}
                    }
                    if(-not $matched){throw 'mind_shared_status'}
                } else {
                    if($buffer[$i] -gt 127 -or $Record.Line.Length -ge 1024){throw 'mind_shared_status'}
                    $Record.Line.WriteByte($buffer[$i])
                }
            }
            if($which -ceq 'Out') {
                $Record.OutRead=$Record.Process.StandardOutput.BaseStream.ReadAsync($Record.OutBuffer,0,$Record.OutBuffer.Length)
            } else {
                $Record.ErrRead=$Record.Process.StandardError.BaseStream.ReadAsync($Record.ErrBuffer,0,$Record.ErrBuffer.Length)
            }
        }
        if($Record.Process.HasExited) {
            if($Record.Process.ExitCode -eq 0 -and $Record.OutDone -and $Record.ErrDone -and
               $Record.Line.Length -eq 0 -and $Record.ReportedState -ceq 'STOPPED') {$Record.State='STOPPED'}
            elseif($Record.OutDone -and $Record.ErrDone) {$Record.State='FAILED'}
            else {$Record.State='HOLD'}
        } elseif($Record.Stopping) {$Record.State='HOLD'}
        elseif($Record.ReportedState -ceq 'READY') {$Record.State='READY'}
        elseif($Record.ReportedState -ceq 'HOLD' -or $Record.ReportedState -ceq 'STOPPED') {$Record.State='HOLD'}
        else {$Record.State='STARTING'}
    } catch { $Record.State='HOLD'; $Record.Invalid=$true }
}

function Start-WdMindSharedOwner {
    <# Opt-in persistent owner. The creating PowerShell session owns the process;
       closing that session is not claimed to preserve it. No lane identity is authenticated. #>
    [CmdletBinding()]
    param([Parameter(Mandatory)][string] $BundleRoot, [Parameter(Mandatory)][string] $ExpectedManifestHash,
          [Parameter(Mandatory)][string] $OperatorPolicyPath, [Parameter(Mandatory)][string] $OperatorPolicySha256,
          [Parameter(Mandatory)][string] $OperatorApprovalReferenceSha256,
          [Parameter(Mandatory)][string] $BridgePythonPath,
          [Parameter(Mandatory)][string] $BridgePythonSha256)
    $policy=$null; $record=$null
    try {
        if(-not [IO.Path]::IsPathRooted($BundleRoot) -or $BundleRoot -match '(^|[\\/])\.\.([\\/]|$)' -or
           $OperatorApprovalReferenceSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $BridgePythonSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z'){throw 'input'}
        $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $manifestPath=Join-Path $root 'deployment-manifest.json'
        $manifestBytes=Read-WdMindSharedPinnedBytes $manifestPath $ExpectedManifestHash 4194304
        $utf8=New-Object Text.UTF8Encoding($false,$true)
        $manifest=$utf8.GetString($manifestBytes).TrimStart([char]0xFEFF)|ConvertFrom-Json -ErrorAction Stop
        if($manifest.schema_version -ne 1 -or $null -eq $manifest.files){throw 'manifest'}
        $descriptorName='wd-mind-identifier-policy-approval.json'
        $descriptorPin=$manifest.files.PSObject.Properties[$descriptorName]
        if($null -eq $descriptorPin -or $descriptorPin.Value -isnot [string]){throw 'descriptor'}
        $descriptor=Read-WdMindSharedPinnedBytes (Join-Path $root $descriptorName) $descriptorPin.Value 65536
        $expected='{"approval_reference_sha256":"'+$OperatorApprovalReferenceSha256.ToLowerInvariant()+'",'+
            '"identifier_policy_sha256":"'+$OperatorPolicySha256.ToLowerInvariant()+'",'+
            '"schema":"wd.mind-identifier-policy-approval.v1"}'
        if($utf8.GetString($descriptor) -cne $expected){throw 'descriptor'}
        $policy=Read-WdMindSharedPinnedBytes $OperatorPolicyPath $OperatorPolicySha256 65536
        [void](Read-WdMindSharedPinnedBytes $BridgePythonPath $BridgePythonSha256 134217728)
        $wrapper=Join-Path $root 'Invoke-WdBridgePython.ps1'
        $helperBytes=$null
        foreach($relative in @('Invoke-WdBridgePython.ps1','Invoke-WdMindStartupRead.ps1',
                                'tools-bootstrap/tools/wd_mind_runtime_child.py')) {
            $pin=$manifest.files.PSObject.Properties[$relative]
            if($null -eq $pin -or $pin.Value -isnot [string]){throw 'entry'}
            $entryBytes=Read-WdMindSharedPinnedBytes (Join-Path $root $relative) $pin.Value 8388608
            if($relative -ceq 'Invoke-WdMindStartupRead.ps1'){$helperBytes=$entryBytes}
        }
        if($null -eq $helperBytes -or $utf8.GetString($helperBytes).TrimStart([char]0xFEFF) -cne
           $script:WdMindSharedOwnerHelperText){throw 'helper_source'}
        [void](Read-WdMindSharedPinnedBytes $manifestPath $ExpectedManifestHash 4194304)
        [void](Read-WdMindSharedPinnedBytes (Join-Path $root $descriptorName) $descriptorPin.Value 65536)
        $wrapperPin=[string]$manifest.files.PSObject.Properties['Invoke-WdBridgePython.ps1'].Value
        $quote={param([string]$v) "'"+$v.Replace("'","''")+"'"}
        $command='$ErrorActionPreference="Stop"; $ProgressPreference="SilentlyContinue"; $f=[IO.File]::OpenRead('+(& $quote $wrapper)+
            ');$s=[Security.Cryptography.SHA256]::Create();try{$h=[BitConverter]::ToString($s.ComputeHash($f)).Replace("-","").ToLowerInvariant()}finally{$f.Dispose();$s.Dispose()}; if($h -cne '+
            (& $quote $wrapperPin.ToLowerInvariant())+' ){exit 2}; & '+(& $quote $wrapper)+
            ' tools/wd_mind_runtime_child.py -VerifyPackage --mind-local-candidate-owner --policy-path '+
            (& $quote ([IO.Path]::GetFullPath($OperatorPolicyPath)))+' --policy-sha256 '+
            (& $quote $OperatorPolicySha256.ToLowerInvariant())+' --approval-reference-sha256 '+
            (& $quote $OperatorApprovalReferenceSha256.ToLowerInvariant())+'; exit $LASTEXITCODE'
        $info=New-Object Diagnostics.ProcessStartInfo
        $shell=if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'}
        $info.FileName=Join-Path $PSHOME $shell
        $info.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $info.WorkingDirectory=$root; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
        $info.RedirectStandardInput=$true; $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
        $environmentAllow=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP',
                            'USERPROFILE','LOCALAPPDATA','APPDATA')
        foreach($name in @($info.EnvironmentVariables.Keys)) {
            if($environmentAllow -cnotcontains ([string]$name).ToUpperInvariant()){
                $info.EnvironmentVariables.Remove([string]$name)
            }
        }
        $info.EnvironmentVariables['PSModulePath']=(Join-Path $PSHOME 'Modules')+';'+
            (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules')
        $info.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedManifestHash.ToUpperInvariant()
        # Explicit caller-provided handshake measurement; not a manifest-approved interpreter identity.
        $info.EnvironmentVariables['WD_BRIDGE_PYTHON_SHA256']=$BridgePythonSha256.ToUpperInvariant()
        $info.EnvironmentVariables['WD_BRIDGE_PYTHON']=[IO.Path]::GetFullPath($BridgePythonPath)
        if(-not (Test-Path variable:global:WdMindSharedOwnerRecords)){$global:WdMindSharedOwnerRecords=@{}}
        $token=[Guid]::NewGuid().ToString('N')
        $proc=New-Object Diagnostics.Process; $proc.StartInfo=$info
        $record=@{Process=$proc;Manifest=$ExpectedManifestHash.ToLowerInvariant();State='HOLD';ReportedState='';
            Stopping=$false;Invalid=$false;
            OutBuffer=(New-Object byte[] 4096);ErrBuffer=(New-Object byte[] 4096);Line=(New-Object IO.MemoryStream);
            OutRead=$null;ErrRead=$null;OutDone=$false;ErrDone=$false}
        $global:WdMindSharedOwnerRecords[$token]=$record
        $handle=[pscustomobject]@{schema='wd.mind-shared-owner-handle.v1';Token=$token;authority='none'}
        if(-not $proc.Start()){return $handle}
        $record.OutRead=$proc.StandardOutput.BaseStream.ReadAsync($record.OutBuffer,0,$record.OutBuffer.Length)
        $record.ErrRead=$proc.StandardError.BaseStream.ReadAsync($record.ErrBuffer,0,$record.ErrBuffer.Length)
        $record.State='STARTING'
        return $handle
    } catch {
        if($null -ne $record){return $handle}
        throw 'mind_shared_owner_refused'
    } finally {
        if($null -ne $policy){[Array]::Clear($policy,0,$policy.Length)}
    }
}

function Get-WdMindSharedOwnerStatus {
    [CmdletBinding()] param([Parameter(Mandatory)] $Owner)
    $record=Get-WdMindSharedOwnerRecord $Owner
    if(-not $record.Invalid){Update-WdMindSharedOwner $record}
    else {
        try { $record.State=if($record.Process.HasExited){'FAILED'}else{'HOLD'} }
        catch { $record.State='HOLD' }
    }
    return [pscustomobject]@{schema='wd.mind-shared-owner-status.v1';state=$record.State;
        authority='none';caller_authentication='UNKNOWN';index_refresh='NOT_SCHEDULED'}
}

function Stop-WdMindSharedOwner {
    [CmdletBinding()] param([Parameter(Mandatory)] $Owner)
    $record=Get-WdMindSharedOwnerRecord $Owner
    if(-not $record.Invalid){Update-WdMindSharedOwner $record}
    if($record.State -in @('STOPPED','FAILED')){return Get-WdMindSharedOwnerStatus $Owner}
    if(-not $record.Stopping -and -not $record.Process.HasExited) {
        try {
            $record.Stopping=$true
            $record.Process.StandardInput.Close()
        } catch { $record.Invalid=$true; $record.State='HOLD' }
    }
    return Get-WdMindSharedOwnerStatus $Owner
}

function Invoke-WdMindStartupRead {
    <# One optional, bounded status read through the manifest-pinned session CLI.
       This helper defines no writer/producer route and never starts an agent.
       Success means a locally validated status projection, not Mind adoption,
       authenticated origin, a gate result, or proof of current swarm state. #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string] $ExpectedManifestHash,
        [Parameter(Mandatory)] [string] $Lane,
        [string] $Worktree = '',
        [ValidateRange(1, 90)] [int] $TimeoutSeconds = 15,
        [switch] $ParentBoundContent,
        [string] $OperatorPolicySha256 = '',
        [string] $OperatorApprovalReferenceSha256 = '',
        [string] $OperatorPolicyPath = '',
        [byte[]] $ApprovedIdentifierPolicyBytes = $null
    )
    if ($ParentBoundContent -and ([string]::IsNullOrEmpty($OperatorPolicySha256) -or
        [string]::IsNullOrEmpty($OperatorApprovalReferenceSha256) -or
        ($null -eq $ApprovedIdentifierPolicyBytes -and [string]::IsNullOrEmpty($OperatorPolicyPath)) -or
        ($null -ne $ApprovedIdentifierPolicyBytes -and -not [string]::IsNullOrEmpty($OperatorPolicyPath)))) {
        # Parent-controlled bootstrap, an independently approved policy pin,
        # retained HOLD ownership and one-shot inherited handles are not yet
        # available. Refuse before any I/O or child process, including receipts.
        return [pscustomobject][ordered]@{
            schema='wd.mind-startup-status.v1';outcome='refused';reason='parent_content_unavailable'
            authority='none';view_json='';view_sha256='';cleanup='not_started';record_written=$false
        }
    }
    $result = [ordered]@{schema='wd.mind-startup-status.v1';outcome='refused';reason='package_refused';
        authority='none';view_json='';view_sha256='';cleanup='not_started';record_written=$false}
    $requestClock=[Diagnostics.Stopwatch]::StartNew()
    $process = $null
    function Read-WdMindPinnedSnapshot {
        param([string] $Path, [string] $Pin, [int] $Limit=1048576)
        if ($Pin -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or -not [IO.Path]::IsPathRooted($Path)) { throw 'pin' }
        $current = [IO.Path]::GetFullPath($Path)
        while ($current) {
            if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'path' }
            $parent = [IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }
            $current = $parent
        }
        $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            if ($stream.Length -gt $Limit) { throw 'size' }
            $bytes = New-Object byte[] ([int]$stream.Length)
            $offset = 0
            while ($offset -lt $bytes.Length) {
                $n = $stream.Read($bytes, $offset, $bytes.Length-$offset)
                if ($n -le 0) { throw 'read' }
                $offset += $n
            }
            if ($stream.ReadByte() -ne -1) { throw 'changed' }
        } finally { $stream.Dispose() }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $digest = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($digest -cne $Pin.ToLowerInvariant()) { throw 'pin' }
        $utf8 = New-Object Text.UTF8Encoding($false,$true)
        return [pscustomobject]@{Text=$utf8.GetString($bytes).TrimStart([char]0xFEFF);Hash=$digest;
            HasUtf8Bom=($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf)}
    }
    function Test-WdMindParentAdmission {
        <# Compare operator-supplied independent pins to one pinned descriptor.
           A match is not cryptographic proof of operator identity, a parent
           handle, policy schema validity, an approved child result, or
           permission to start a process. #>
        [CmdletBinding()]
        param(
            [switch] $Enabled,
            [string] $BundleRoot,
            [string] $ExpectedManifestHash,
            [string] $OperatorPolicySha256,
            [string] $OperatorApprovalReferenceSha256,
            [byte[]] $ApprovedPolicyBytes
        )
        $matched=$false
        try {
            if(-not $Enabled -or [string]::IsNullOrEmpty($BundleRoot) -or
               $BundleRoot -match '(^|[\\/])\.\.([\\/]|$)' -or
               $ExpectedManifestHash -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
               $OperatorPolicySha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
               $OperatorApprovalReferenceSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
               $null -eq $ApprovedPolicyBytes -or $ApprovedPolicyBytes.Length -lt 1 -or
               $ApprovedPolicyBytes.Length -gt 65536){throw 'input'}
            $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
            $manifestPath=Join-Path $root 'deployment-manifest.json'
            $manifest=Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304
            $deployment=$manifest.Text | ConvertFrom-Json -ErrorAction Stop
            if(($deployment.schema_version -isnot [int] -and $deployment.schema_version -isnot [long]) -or
                $deployment.schema_version -ne 1 -or $null -eq $deployment.files){throw 'manifest'}
            $name='wd-mind-identifier-policy-approval.json'
            $pin=$deployment.files.PSObject.Properties[$name]
            if($null -eq $pin -or $pin.Value -isnot [string]){throw 'descriptor'}
            $descriptor=Read-WdMindPinnedSnapshot -Path (Join-Path $root $name) -Pin $pin.Value -Limit 65536
            $expected='{"approval_reference_sha256":"' + $OperatorApprovalReferenceSha256.ToLowerInvariant() +
                '","identifier_policy_sha256":"' + $OperatorPolicySha256.ToLowerInvariant() +
                '","schema":"wd.mind-identifier-policy-approval.v1"}'
            # Exact canonical text prevents duplicate keys and unreviewed fields.
            if($descriptor.HasUtf8Bom -or $descriptor.Text -cne $expected){throw 'descriptor'}
            if($ApprovedPolicyBytes.Length -ge 3 -and $ApprovedPolicyBytes[0] -eq 0xef -and
                $ApprovedPolicyBytes[1] -eq 0xbb -and $ApprovedPolicyBytes[2] -eq 0xbf){throw 'policy'}
            $utf8=New-Object Text.UTF8Encoding($false,$true)
            [void]$utf8.GetCharCount($ApprovedPolicyBytes)
            $sha=[Security.Cryptography.SHA256]::Create()
            try {$policyHash=[BitConverter]::ToString($sha.ComputeHash($ApprovedPolicyBytes)).Replace('-','').ToLowerInvariant()}
            finally {$sha.Dispose()}
            if($policyHash -cne $OperatorPolicySha256.ToLowerInvariant()){throw 'policy'}
            foreach($relative in @('Invoke-WdBridgePython.ps1', 'tools-bootstrap/tools/wd_mind_runtime_child.py',
                'tools-bootstrap/tools/wd_mind_full_runtime.py', 'tools-bootstrap/tools/wd_bridge_mind_session.py')){
                $entryPin=$deployment.files.PSObject.Properties[$relative]
                if($null -eq $entryPin -or $entryPin.Value -isnot [string]){throw 'entry'}
                [void](Read-WdMindPinnedSnapshot -Path (Join-Path $root $relative) -Pin $entryPin.Value -Limit 8388608)
            }
            [void](Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304)
            [void](Read-WdMindPinnedSnapshot -Path (Join-Path $root $name) -Pin $pin.Value -Limit 65536)
            $sha=[Security.Cryptography.SHA256]::Create()
            try {$lastHash=[BitConverter]::ToString($sha.ComputeHash($ApprovedPolicyBytes)).Replace('-','').ToLowerInvariant()}
            finally {$sha.Dispose()}
            if($lastHash -cne $policyHash){throw 'policy_changed'}
            $matched=$true
        } catch { } finally {
            $ApprovedPolicyBytes=$null
            [void]$PSBoundParameters.Remove('ApprovedPolicyBytes')
        }
        if(-not $matched){throw 'mind_parent_admission_refused'}
        return [pscustomobject]@{state='PINS_MATCH_POLICY_SCHEMA_UNVERIFIED';authority='none';operator_authentication='UNKNOWN'}
    }
    function Read-WdMindOperatorPolicySnapshot {
        <# The selected path is operator input, never inferred from a bundle.
           Returns private bytes only in process memory; this is not a proof of
           operator identity, same-user race resistance, or restart durability. #>
        param([string] $Path, [string] $ExpectedSha256)
        if ($Path -cnotmatch '\A[Cc]:\\' -or $ExpectedSha256 -cnotmatch '\A[0-9A-Fa-f]{64}\z') { throw 'policy_input' }
        foreach ($part in $Path.Substring(3).Split('\')) {
            if (-not $part -or $part -in @('.', '..') -or $part -match '[<>:"|?*\x00-\x1f]' -or
                $part.EndsWith('.') -or $part.EndsWith(' ') -or
                $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') { throw 'policy_path' }
        }
        $full = [IO.Path]::GetFullPath($Path)
        $current = $full
        while ($current) {
            if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'policy_path' }
            $parent = [IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }
            $current = $parent
        }
        $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            if (-not ('WaggleDance.WdMindDescriptorFileV1' -as [type])) {
                Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
namespace WaggleDance {
    public static class WdMindDescriptorFileV1 {
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern uint GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
        public static string FinalPath(SafeFileHandle handle) {
            var path = new StringBuilder(32768);
            uint length = GetFinalPathNameByHandleW(handle, path, (uint)path.Capacity, 0);
            if (length == 0 || length >= path.Capacity) throw new IOException("Mind policy handle unavailable");
            return path.ToString();
        }
    }
}
'@
            }
            $opened = [WaggleDance.WdMindDescriptorFileV1]::FinalPath($stream.SafeFileHandle)
            if (-not $opened.Equals(('\\?\' + $full), [StringComparison]::OrdinalIgnoreCase)) { throw 'policy_handle' }
            if ($stream.Length -lt 1 -or $stream.Length -gt 65536) { throw 'policy_size' }
            $bytes = New-Object byte[] ([int]$stream.Length)
            $offset = 0
            while ($offset -lt $bytes.Length) {
                $count = $stream.Read($bytes, $offset, $bytes.Length-$offset)
                if ($count -le 0) { throw 'policy_read' }
                $offset += $count
            }
            if ($stream.ReadByte() -ne -1) { throw 'policy_changed' }
            if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf) { throw 'policy_encoding' }
            $utf8 = New-Object Text.UTF8Encoding($false, $true)
            [void]$utf8.GetCharCount($bytes)
            $sha = [Security.Cryptography.SHA256]::Create()
            try { $hash = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
            finally { $sha.Dispose() }
            if ($hash -cne $ExpectedSha256.ToLowerInvariant()) { throw 'policy_hash' }
            $current = $full
            while ($current) {
                if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'policy_path' }
                $parent = [IO.Path]::GetDirectoryName($current)
                if (-not $parent -or $parent -ceq $current) { break }
                $current = $parent
            }
            return [pscustomobject]@{Bytes=$bytes;Hash=$hash}
        } finally { $stream.Dispose() }
    }
    function ConvertTo-WdMindLiteral([string] $Value) { return "'" + $Value.Replace("'","''") + "'" }
    function Update-WdMindParentBootstrapPumps {
        param([Parameter(Mandatory)] $Owner)
        if(-not (Test-Path variable:global:WdMindParentBootstrapIO) -or
           -not $global:WdMindParentBootstrapIO.ContainsKey($Owner.Token) -or
           -not $global:WdMindParentBootstrapProcesses.ContainsKey($Owner.Token)){throw 'mind_parent_bootstrap_refused'}
        $io=$global:WdMindParentBootstrapIO[$Owner.Token]
        $process=$global:WdMindParentBootstrapProcesses[$Owner.Token].Process
        if(-not $io.ContainsKey('OutBlocked')){$io.OutBlocked=$false}
        if(-not $io.ContainsKey('ErrBlocked')){$io.ErrBlocked=$false}
        $overflow=($io.OutBlocked -or $io.ErrBlocked)
        if(-not $io.OutDone -and -not $io.OutBlocked -and $io.OutRead.IsCompleted){
            $count=$io.OutRead.GetAwaiter().GetResult()
            if($count -eq 0){$io.OutDone=$true}
            else{
                if($io.OutBytes.Length+$count -gt 65536){
                    [Array]::Clear($io.OutBuffer,0,$io.OutBuffer.Length)
                    $io.OutBlocked=$true;$overflow=$true
                } else {
                    $io.OutBytes.Write($io.OutBuffer,0,$count)
                    [Array]::Clear($io.OutBuffer,0,$io.OutBuffer.Length)
                    $io.OutRead=$process.StandardOutput.BaseStream.ReadAsync($io.OutBuffer,0,$io.OutBuffer.Length)
                }
            }
        }
        if(-not $io.ErrDone -and -not $io.ErrBlocked -and $io.ErrRead.IsCompleted){
            $count=$io.ErrRead.GetAwaiter().GetResult()
            if($count -eq 0){$io.ErrDone=$true}
            else{
                $io.ErrBytes+=$count
                [Array]::Clear($io.ErrBuffer,0,$io.ErrBuffer.Length)
                if($io.ErrBytes -gt 4096){$io.ErrBlocked=$true;$overflow=$true}
                else {$io.ErrRead=$process.StandardError.BaseStream.ReadAsync($io.ErrBuffer,0,$io.ErrBuffer.Length)}
            }
        }
        if($overflow){$Owner.State='HOLD';return $false}
        return $true
    }
    function Wait-WdMindParentBootstrap {
        <# Drain within the original budget. Output is private and is not a
           validated Mind result. No resource is released on HOLD or exit. #>
        param([Parameter(Mandatory)] $Owner)
        try {
            if($Owner.State -cne 'BOOTSTRAP_SENT'){return $Owner}
            while($Owner.Clock.ElapsedMilliseconds -lt $Owner.TimeoutMilliseconds){
                if(-not (Update-WdMindParentBootstrapPumps -Owner $Owner)){return $Owner}
                if($Owner.Clock.ElapsedMilliseconds -ge $Owner.TimeoutMilliseconds){break}
                $io=$global:WdMindParentBootstrapIO[$Owner.Token]
                $process=$global:WdMindParentBootstrapProcesses[$Owner.Token].Process
                if($io.OutDone -and $io.ErrDone -and $process.HasExited){
                    $Owner.State=if($process.ExitCode -eq 0){'EXITED'}else{'EXIT_FAILED'}
                    return $Owner
                }
                [Threading.Thread]::Sleep(5)
            }
        } catch { }
        $Owner.State='HOLD'
        return $Owner
    }
    function ConvertFrom-WdMindCompletedLocalBootstrap {
        <# A completed private buffer is not a lane result until the caller
           validates its full content contract. No bytes are returned on HOLD,
           failed exit, incomplete pipes, invalid UTF-8 or incomplete JSON. #>
        param([Parameter(Mandatory)] $Owner, [Parameter(Mandatory)] $Process,
              [Parameter(Mandatory)] $IO)
        try {
            if ($Owner.State -cne 'EXITED' -or -not $Process.HasExited -or $Process.ExitCode -ne 0 -or
                -not $IO.OutDone -or -not $IO.ErrDone -or
                ($IO.ContainsKey('OutBlocked') -and $IO.OutBlocked) -or
                ($IO.ContainsKey('ErrBlocked') -and $IO.ErrBlocked) -or
                $IO.OutBytes -isnot [IO.MemoryStream] -or
                $IO.OutBytes.Length -lt 1 -or $IO.OutBytes.Length -gt 8192 -or
                -not ('WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator' -as [type])) { throw 'local_output' }
            $bytes=$IO.OutBytes.ToArray()
            if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xef -and $bytes[1] -eq 0xbb -and $bytes[2] -eq 0xbf) {
                throw 'local_output'
            }
            $utf8=New-Object Text.UTF8Encoding($false,$true)
            $text=$utf8.GetString($bytes)
            if (-not $text.TrimStart().StartsWith('{') -or
                -not [WaggleDance.BridgeSnapshotDeltaV1.JsonContractValidator]::IsValid($text)) { throw 'local_output' }
            $jsonArgs=@{InputObject=$text;ErrorAction='Stop'}
            if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonArgs.DateKind='String' }
            $parsed=ConvertFrom-Json @jsonArgs
            if ($null -eq $parsed -or $parsed.GetType() -ne [Management.Automation.PSCustomObject]) { throw 'local_output' }
            $sha=[Security.Cryptography.SHA256]::Create()
            try {$digest=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}
            finally {$sha.Dispose()}
            return [pscustomobject]@{view_json=$text;view_sha256=$digest}
        } catch { return $null }
    }
    function Start-WdMindLocalCandidate {
        param([string]$Root,[string]$Manifest,[string]$PolicyPath,[string]$PolicySha,
              [string]$ApprovalSha,[string]$LaneName,[Diagnostics.Stopwatch]$Clock,[int]$Budget)
        # Launcher supplies the interpreter hash measured during its verified
        # BridgeCodeContext handshake; standalone callers must arrange the same.
        $bridgePythonPin=[string]$env:WD_BRIDGE_PYTHON_SHA256
        $bridgePythonPath=[string]$env:WD_BRIDGE_PYTHON
        if($bridgePythonPin -cnotmatch '\A[0-9a-fA-F]{64}\z'){throw 'local_interpreter_pin'}
        [void](Read-WdMindSharedPinnedBytes $bridgePythonPath $bridgePythonPin 134217728)
        $manifestPath=Join-Path $Root 'deployment-manifest.json'
        $deployment=(Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $Manifest -Limit 4194304).Text | ConvertFrom-Json -ErrorAction Stop
        $wrapper=Join-Path $Root 'Invoke-WdBridgePython.ps1'
        $entry=Join-Path $Root 'tools-bootstrap/tools/wd_bridge_mind_session.py'
        $wrapperPin=$deployment.files.PSObject.Properties['Invoke-WdBridgePython.ps1']
        $entryPin=$deployment.files.PSObject.Properties['tools-bootstrap/tools/wd_bridge_mind_session.py']
        if($null -eq $wrapperPin -or $null -eq $entryPin){throw 'local_pin'}
        [void](Read-WdMindPinnedSnapshot -Path $wrapper -Pin $wrapperPin.Value -Limit 8388608)
        [void](Read-WdMindPinnedSnapshot -Path $entry -Pin $entryPin.Value -Limit 8388608)
        $remaining=[int][Math]::Floor(($Budget*1000-$Clock.ElapsedMilliseconds)/1000)
        if($remaining -lt 1){throw 'local_timeout'}
        $command='$ErrorActionPreference="Stop";if((Get-FileHash -LiteralPath '+(ConvertTo-WdMindLiteral $wrapper)+
            ' -Algorithm SHA256).Hash.ToLowerInvariant() -cne '+(ConvertTo-WdMindLiteral $wrapperPin.Value.ToLowerInvariant())+
            '){exit 2}; & '+(ConvertTo-WdMindLiteral $wrapper)+' tools/wd_bridge_mind_session.py -VerifyPackage --mind-local-candidate-read --policy-path '+
            (ConvertTo-WdMindLiteral $PolicyPath)+' --policy-sha256 '+(ConvertTo-WdMindLiteral $PolicySha.ToLowerInvariant())+
            ' --approval-reference-sha256 '+(ConvertTo-WdMindLiteral $ApprovalSha.ToLowerInvariant())+
            ' --lane '+(ConvertTo-WdMindLiteral $LaneName)+' --timeout-seconds '+$remaining+'; exit $LASTEXITCODE'
        $start=New-Object Diagnostics.ProcessStartInfo
        $shell=if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'}
        $start.FileName=Join-Path $PSHOME $shell
        $start.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $start.WorkingDirectory=$Root;$start.UseShellExecute=$false;$start.CreateNoWindow=$true
        $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
        $allowed=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA')
        foreach($name in @($start.EnvironmentVariables.Keys)){
            if($allowed -cnotcontains ([string]$name).ToUpperInvariant()){$start.EnvironmentVariables.Remove([string]$name)}
        }
        $start.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$Manifest.ToUpperInvariant()
        $start.EnvironmentVariables['WD_BRIDGE_PYTHON_SHA256']=$bridgePythonPin.ToUpperInvariant()
        $start.EnvironmentVariables['WD_BRIDGE_PYTHON']=[IO.Path]::GetFullPath($bridgePythonPath)
        foreach($name in @('WdMindLocalCandidateOwners','WdMindParentBootstrapIO','WdMindParentBootstrapProcesses')){
            if(-not (Test-Path ('variable:global:'+$name))){Set-Variable -Scope Global -Name $name -Value @{} }
        }
        $token=[Guid]::NewGuid().ToString('N')
        $proc=New-Object Diagnostics.Process;$proc.StartInfo=$start
        $owner=[pscustomobject]@{State='HOLD';Token=$token;Approval='UNVERIFIED';Authority='none';
            Clock=$Clock;TimeoutMilliseconds=([long]$Budget*1000)}
        $global:WdMindLocalCandidateOwners[$token]=$owner
        $global:WdMindParentBootstrapProcesses[$token]=@{Process=$proc;WriteTask=$null}
        if(-not $proc.Start()){return $owner}
        $io=@{OutBuffer=(New-Object byte[] 4096);ErrBuffer=(New-Object byte[] 4096);
            OutRead=$null;ErrRead=$null;OutDone=$false;ErrDone=$false;ErrBytes=0;OutBytes=(New-Object IO.MemoryStream)}
        $global:WdMindParentBootstrapIO[$token]=$io
        $io.OutRead=$proc.StandardOutput.BaseStream.ReadAsync($io.OutBuffer,0,$io.OutBuffer.Length)
        $io.ErrRead=$proc.StandardError.BaseStream.ReadAsync($io.ErrBuffer,0,$io.ErrBuffer.Length)
        $owner.State='BOOTSTRAP_SENT'
        return Wait-WdMindParentBootstrap -Owner $owner
    }
    function Start-WdMindParentBootstrap {
        <# Default-off primitive: a caller must independently authorize the
           policy hash. This does not establish a parent entrypoint, prove a
           child's identity, or return a Mind answer. The caller retains the
           returned owner and must drain bounded output and preserve HOLD. #>
        [CmdletBinding()]
        param(
            [switch] $Enabled,
            [Parameter(Mandatory)] [string] $BundleRoot,
            [Parameter(Mandatory)] [string] $ExpectedManifestHash,
            [Parameter(Mandatory)] [byte[]] $ApprovedPolicyBytes,
            [Parameter(Mandatory)] [string] $ApprovedPolicySha256,
            [Parameter(Mandatory)] [string] $ApprovalReferenceSha256,
            [Parameter(Mandatory)] [string] $Lane,
            [ValidateRange(1,90)] [int] $TimeoutSeconds=15
        )
        if (-not $Enabled -or $ApprovedPolicyBytes.Length -lt 1 -or $ApprovedPolicyBytes.Length -gt 65536 -or
            $ApprovedPolicySha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
            $ApprovalReferenceSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
            $Lane -cnotin @('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')) { throw 'mind_parent_bootstrap_refused' }
        $clock=[Diagnostics.Stopwatch]::StartNew()
        $sha=[Security.Cryptography.SHA256]::Create()
        try { $policyHash=[BitConverter]::ToString($sha.ComputeHash($ApprovedPolicyBytes)).Replace('-','').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($policyHash -cne $ApprovedPolicySha256.ToLowerInvariant()) { throw 'mind_parent_bootstrap_refused' }
        $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $manifestPath=Join-Path $root 'deployment-manifest.json'
        $manifest=Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304
        $deployment=$manifest.Text | ConvertFrom-Json -ErrorAction Stop
        $wrapperPath=Join-Path $root 'Invoke-WdBridgePython.ps1'
        $entryPath=Join-Path $root 'tools-bootstrap/tools/wd_mind_runtime_child.py'
        $wrapperPin=$deployment.files.PSObject.Properties['Invoke-WdBridgePython.ps1']
        $entryPin=$deployment.files.PSObject.Properties['tools-bootstrap/tools/wd_mind_runtime_child.py']
        if ($null -eq $wrapperPin -or $null -eq $entryPin -or
            $wrapperPin.Value -isnot [string] -or $entryPin.Value -isnot [string]) { throw 'mind_parent_bootstrap_refused' }
        [void](Read-WdMindPinnedSnapshot -Path $wrapperPath -Pin $wrapperPin.Value -Limit 8388608)
        [void](Read-WdMindPinnedSnapshot -Path $entryPath -Pin $entryPin.Value -Limit 8388608)
        $command='$ErrorActionPreference="Stop"; $ProgressPreference="SilentlyContinue"; if((Get-FileHash -LiteralPath ' +
            (ConvertTo-WdMindLiteral $wrapperPath) + ' -Algorithm SHA256).Hash.ToLowerInvariant() -cne ' +
            (ConvertTo-WdMindLiteral $wrapperPin.Value.ToLowerInvariant()) + '){exit 2}; $global:LASTEXITCODE=0; & ' +
            (ConvertTo-WdMindLiteral $wrapperPath) + ' tools/wd_mind_runtime_child.py -VerifyPackage --parent-control; exit $LASTEXITCODE'
        $start=New-Object Diagnostics.ProcessStartInfo
        $shell=if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'}
        $start.FileName=Join-Path $PSHOME $shell
        $start.Arguments='-NoProfile -NonInteractive -EncodedCommand ' +
            [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $start.WorkingDirectory=$root;$start.UseShellExecute=$false;$start.CreateNoWindow=$true
        $start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
        $environmentAllow=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP',
                            'USERPROFILE','LOCALAPPDATA','APPDATA')
        foreach($name in @($start.EnvironmentVariables.Keys)){
            if($environmentAllow -cnotcontains ([string]$name).ToUpperInvariant()){
                $start.EnvironmentVariables.Remove([string]$name)
            }
        }
        $start.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedManifestHash.ToUpperInvariant()
        $laneBytes=[Text.Encoding]::ASCII.GetBytes($Lane)
        $innerOffset=101+$laneBytes.Length
        $control=New-Object byte[] ($innerOffset+136+$ApprovedPolicyBytes.Length)
        $nonce=New-Object byte[] 32;$key=New-Object byte[] 32;$owner=$null
        try {
            $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
            try { $rng.GetBytes($nonce);$rng.GetBytes($key) } finally { $rng.Dispose() }
            [Array]::Copy([Text.Encoding]::ASCII.GetBytes('MCP1'),0,$control,0,4)
            $control[100]=[byte]$laneBytes.Length
            [Array]::Copy($laneBytes,0,$control,101,$laneBytes.Length)
            [Array]::Copy([Text.Encoding]::ASCII.GetBytes('MCT1'),0,$control,$innerOffset,4)
            $length=[BitConverter]::GetBytes([uint32]$ApprovedPolicyBytes.Length)
            if([BitConverter]::IsLittleEndian){[Array]::Reverse($length)}
            [Array]::Copy($length,0,$control,($innerOffset+4),4)
            for($i=0;$i -lt 32;$i++){
                $manifestByte=[Convert]::ToByte($ExpectedManifestHash.Substring(2*$i,2),16)
                $policyByte=[Convert]::ToByte($ApprovedPolicySha256.Substring(2*$i,2),16)
                $control[4+$i]=$manifestByte;$control[36+$i]=$policyByte
                $control[68+$i]=[Convert]::ToByte($ApprovalReferenceSha256.Substring(2*$i,2),16)
                $control[$innerOffset+8+$i]=$manifestByte;$control[$innerOffset+104+$i]=$policyByte
            }
            [Array]::Copy($nonce,0,$control,($innerOffset+40),32);[Array]::Copy($key,0,$control,($innerOffset+72),32)
            [Array]::Copy($ApprovedPolicyBytes,0,$control,($innerOffset+136),$ApprovedPolicyBytes.Length)
            [void](Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304)
            [void](Read-WdMindPinnedSnapshot -Path $wrapperPath -Pin $wrapperPin.Value -Limit 8388608)
            [void](Read-WdMindPinnedSnapshot -Path $entryPath -Pin $entryPin.Value -Limit 8388608)
            if($clock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){throw 'mind_parent_bootstrap_refused'}
            if(-not (Test-Path variable:global:WdMindParentBootstrapOwners)){
                $global:WdMindParentBootstrapOwners=New-Object Collections.ArrayList
            }
            if(-not (Test-Path variable:global:WdMindParentBootstrapBuffers)){
                $global:WdMindParentBootstrapBuffers=@{}
            }
            if(-not (Test-Path variable:global:WdMindParentBootstrapIO)){
                $global:WdMindParentBootstrapIO=@{}
            }
            if(-not (Test-Path variable:global:WdMindParentBootstrapProcesses)){
                $global:WdMindParentBootstrapProcesses=@{}
            }
            $process=New-Object Diagnostics.Process
            $process.StartInfo=$start
            $token=[Guid]::NewGuid().ToString('N')
            $owner=[pscustomobject]@{State='HOLD';Token=$token;Approval='UNVERIFIED';Authority='none';
                Clock=$clock;TimeoutMilliseconds=([long]$TimeoutSeconds*1000)}
            # The process and any pending write remain strongly owned on every
            # post-reservation failure; no process termination is attempted.
            [void]$global:WdMindParentBootstrapOwners.Add($owner)
            $global:WdMindParentBootstrapBuffers[$token]=$control
            $global:WdMindParentBootstrapProcesses[$token]=@{Process=$process;WriteTask=$null}
            if(-not $process.Start()){return $owner}
            if($clock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){
                [Array]::Clear($control,0,$control.Length)
                $global:WdMindParentBootstrapBuffers.Remove($token)
                $process.StandardInput.Close()
                return $owner
            }
            # Output remains in private same-runspace memory for later separate
            # validation; this is not a memory-wipe or same-user isolation claim.
            $io=@{OutBuffer=(New-Object byte[] 4096);ErrBuffer=(New-Object byte[] 4096);
                OutRead=$null;ErrRead=$null;OutDone=$false;ErrDone=$false;ErrBytes=0;
                OutBytes=(New-Object IO.MemoryStream)}
            $global:WdMindParentBootstrapIO[$token]=$io
            $io.OutRead=$process.StandardOutput.BaseStream.ReadAsync($io.OutBuffer,0,$io.OutBuffer.Length)
            $io.ErrRead=$process.StandardError.BaseStream.ReadAsync($io.ErrBuffer,0,$io.ErrBuffer.Length)
            $writeTask=$process.StandardInput.BaseStream.WriteAsync($control,0,$control.Length)
            $global:WdMindParentBootstrapProcesses[$token].WriteTask=$writeTask
            while(-not $writeTask.IsCompleted -and $clock.ElapsedMilliseconds -lt ([long]$TimeoutSeconds*1000)){
                if(-not (Update-WdMindParentBootstrapPumps -Owner $owner)){return $owner}
                [Threading.Thread]::Sleep(5)
            }
            if(-not (Update-WdMindParentBootstrapPumps -Owner $owner)){return $owner}
            if(-not $writeTask.IsCompleted -or $clock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){
                return $owner
            }
            [void]$writeTask.GetAwaiter().GetResult()
            [Array]::Clear($control,0,$control.Length)
            $global:WdMindParentBootstrapBuffers.Remove($token)
            $process.StandardInput.Close()
            if($clock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){return $owner}
            $owner.State='BOOTSTRAP_SENT'
            return $owner
        } catch {
            if($null -ne $owner){return $owner}
            [Array]::Clear($control,0,$control.Length)
            throw 'mind_parent_bootstrap_refused'
        } finally {
            [Array]::Clear($nonce,0,$nonce.Length);[Array]::Clear($key,0,$key.Length)
        }
    }
    # Closed shapes apply recursively: a known outer object is not permission to
    # forward arbitrary nested message/payload fields or free-form strings.
    function Assert-WdMindObject($Value, [string[]] $Required, [string[]] $Optional=@()) {
        if ($Value -isnot [pscustomobject]) { throw 'shape' }
        $names=@($Value.PSObject.Properties.Name)
        foreach ($name in $Required) { if ($names -cnotcontains $name) { throw 'shape' } }
        foreach ($name in $names) { if (($Required+$Optional) -cnotcontains $name) { throw 'shape' } }
    }
    function Assert-WdMindString($Value, [string] $Pattern) {
        if ($Value -isnot [string] -or $Value -cnotmatch $Pattern) { throw 'shape' }
    }
    function Assert-WdMindNumber($Value, [switch] $Integer) {
        if (($Value -isnot [int] -and $Value -isnot [long] -and
             ($Integer -or $Value -isnot [double])) -or $Value -lt 0 -or
            [double]::IsNaN([double]$Value) -or [double]::IsInfinity([double]$Value)) { throw 'shape' }
    }
    function Assert-WdMindDirectStatus($View, [string] $ExpectedLane, [string] $FleetHash, $Fleet) {
        # This is a separate observational schema; it cannot impersonate a
        # verified publication or carry free-form event bodies into prompts.
        Assert-WdMindObject $View @('schema','authority','scope','lane','fleet_sha256','coverage','generation','continuity','sender_authentication','as_of_utc','observation_status','facts')
        foreach ($name in @('schema','authority','scope','lane','fleet_sha256','coverage','generation','continuity','sender_authentication','as_of_utc','observation_status')) {
            if ($View.PSObject.Properties[$name].Value -isnot [string]) { throw 'shape' }
        }
        if ($View.schema -cne 'wd.mind-direct-status.v1' -or $View.authority -cne 'none' -or
            $View.scope -cne 'bounded_bridge_status_not_gate' -or $View.lane -cne $ExpectedLane -or
            $View.fleet_sha256 -cne $FleetHash -or $View.coverage -cne 'bounded_tail' -or
            $View.generation -cne 'UNKNOWN' -or $View.continuity -cne 'UNKNOWN' -or
            $View.sender_authentication -cne 'UNKNOWN' -or
            $View.facts -isnot [array] -or $View.facts.Count -gt 1) { throw 'shape' }
        Assert-WdMindString $View.fleet_sha256 '\A[0-9a-f]{64}\z'
        Assert-WdMindString $View.as_of_utc '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\z'
        $asOf=[DateTimeOffset]::MinValue
        if (-not [DateTimeOffset]::TryParse($View.as_of_utc, [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::None, [ref]$asOf) -or $asOf.Offset -ne [TimeSpan]::Zero) { throw 'shape' }
        if (@('OBSERVED','UNKNOWN') -cnotcontains $View.observation_status -or
            ($View.facts.Count -eq 0 -and $View.observation_status -cne 'UNKNOWN')) { throw 'shape' }
        if ($Fleet -isnot [pscustomobject] -or $Fleet.lanes -isnot [array] -or
            $Fleet.tools_supervisor -isnot [pscustomobject]) { throw 'shape' }
        $knownLanes=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
        $roster=@($Fleet.lanes) + @($Fleet.tools_supervisor)
        $agents=@()
        foreach ($entry in $roster) {
            if ($entry -isnot [pscustomobject] -or $entry.agent -isnot [string] -or
                $knownLanes -cnotcontains $entry.agent -or $agents -ccontains $entry.agent) { throw 'shape' }
            $agents+=@($entry.agent)
        }
        if ($agents.Count -ne $knownLanes.Count -or $agents -cnotcontains $ExpectedLane) { throw 'shape' }
        foreach ($fact in $View.facts) {
            Assert-WdMindObject $fact @('agent','event_type','observed_utc','event_ref','age_seconds')
            foreach ($name in @('agent','event_type','observed_utc','event_ref')) {
                if ($fact.PSObject.Properties[$name].Value -isnot [string]) { throw 'shape' }
            }
            if ($fact.agent -cne $ExpectedLane -or $agents -cnotcontains $fact.agent -or
                @('heartbeat','liveness') -cnotcontains $fact.event_type) { throw 'shape' }
            Assert-WdMindString $fact.observed_utc '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?(?:Z|\+00:00)\z'
            $observed=[DateTimeOffset]::MinValue
            if (-not [DateTimeOffset]::TryParse($fact.observed_utc, [Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::None, [ref]$observed) -or $observed.Offset -ne [TimeSpan]::Zero) { throw 'shape' }
            Assert-WdMindString $fact.event_ref '\A[0-9a-f]{64}\z'
            Assert-WdMindNumber $fact.age_seconds -Integer
            $calculatedAge=($asOf-$observed).TotalSeconds
            if ($calculatedAge -lt 0 -or [Math]::Abs($calculatedAge-[double]$fact.age_seconds) -gt 1 -or
                ($calculatedAge -le 900 -and $View.observation_status -cne 'OBSERVED') -or
                ($calculatedAge -gt 900 -and $View.observation_status -cne 'UNKNOWN')) { throw 'shape' }
        }
    }
    function Assert-WdMindNestedView($View) {
        $lanes=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
        $identity='\A(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]{1,16}:[0-9a-f]{1,16})\z'
        $generation='\A[A-Za-z0-9][A-Za-z0-9._-]{0,127}\z'
        $time='\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00)\z'
        $unknownTime='\A(?:UNKNOWN|[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|\+00:00))\z'
        Assert-WdMindString $View.as_of_utc $time
        Assert-WdMindObject $View.binding @('file_identity','generation','origin') @('generation_status','rotation_continuity')
        Assert-WdMindString $View.binding.file_identity $identity
        if ($null -eq $View.binding.generation) {
            if ($View.binding.origin -cne 'UNKNOWN' -or $View.binding.generation_status -cne 'UNKNOWN' -or
                $View.binding.rotation_continuity -cne 'UNKNOWN') { throw 'shape' }
        } else {
            Assert-WdMindString $View.binding.generation $generation
            if (@('UNKNOWN','lane_reader_view_not_authenticated') -cnotcontains $View.binding.origin -or
                @($View.binding.PSObject.Properties).Count -ne 3) { throw 'shape' }
        }
        Assert-WdMindObject $View.pointer @('cycle_dir','cycle_generation') @('state_read_mode','state_head_origin','head_to_state_content')
        Assert-WdMindString $View.pointer.cycle_dir '\A[0-9]{8}T[0-9]{6}Z\z'
        Assert-WdMindNumber $View.pointer.cycle_generation -Integer
        if ($View.pointer.cycle_generation -lt 1) { throw 'shape' }
        if (@($View.pointer.PSObject.Properties).Count -ne 2) {
            if (@($View.pointer.PSObject.Properties).Count -ne 5 -or $View.pointer.state_read_mode -cne 'compact_head' -or
                $View.pointer.state_head_origin -cne 'UNVERIFIED' -or $View.pointer.head_to_state_content -cne 'UNVERIFIED' -or
                $View.binding.origin -cne 'UNKNOWN') { throw 'shape' }
        }
        Assert-WdMindObject $View.freshness @('age_seconds','max_age_seconds','status')
        Assert-WdMindObject $View.identity @('roster','bound_lanes')
        if (@('caller_declared','none') -cnotcontains $View.identity.roster -or
            $View.identity.bound_lanes -isnot [array]) { throw 'shape' }
        foreach ($boundLane in $View.identity.bound_lanes) { if ($lanes -cnotcontains $boundLane) { throw 'shape' } }
        Assert-WdMindObject $View.source @('log_identity','scope','generation','as_of_utc','max_event_utc',
            'event_count','unique_event_count','duplicate_count','window','blocked')
        if ($View.source.log_identity -cne ('capture-window:'+$View.binding.file_identity) -or
            $View.source.scope -cne 'bounded_window_of_bridge_log_not_whole_log' -or
            $View.source.generation -cne $View.binding.generation) { throw 'shape' }
        Assert-WdMindString $View.source.as_of_utc $time
        Assert-WdMindString $View.source.max_event_utc $unknownTime
        foreach ($name in @('event_count','unique_event_count','duplicate_count')) {
            Assert-WdMindNumber $View.source.PSObject.Properties[$name].Value -Integer
        }
        Assert-WdMindObject $View.source.window @('cursor_start','cursor_end','coverage')
        Assert-WdMindNumber $View.source.window.cursor_start -Integer
        Assert-WdMindNumber $View.source.window.cursor_end -Integer
        if ($View.source.window.coverage -cne 'complete_over_window_only') { throw 'shape' }
        if ($null -ne $View.source.blocked) {
            Assert-WdMindObject $View.source.blocked @('active','reason')
            $reasons=@('canonical_event_invalid','complete_coverage_required','cursor_discontinuity_rebuild_required',
                'file_identity_changed','generation_changed','guarded_word_refused','head_binding_conflict','log_truncated',
                'partial_record','provenance_envelope_required','provenance_hash_mismatch','source_cursor_count_invalid','source_cursor_required')
            if ($View.source.blocked.active -isnot [bool] -or -not $View.source.blocked.active -or
                $reasons -cnotcontains $View.source.blocked.reason) { throw 'shape' }
        }
        Assert-WdMindObject $View.lanes $lanes
        foreach ($laneName in $lanes) {
            $row=$View.lanes.PSObject.Properties[$laneName].Value
            if ($row.observed_in_window -is [bool] -and $row.observed_in_window) {
                Assert-WdMindObject $row @('observed_in_window','identity_verification','last_event_utc','observation_age_seconds','claims')
                if ($row.identity_verification -cne 'roster_bound' -or $row.claims -isnot [array]) { throw 'shape' }
                Assert-WdMindString $row.last_event_utc $time
                Assert-WdMindNumber $row.observation_age_seconds
                foreach ($claim in $row.claims) {
                    Assert-WdMindObject $claim @('task_id','lease_expires_utc','expired')
                    Assert-WdMindString $claim.task_id '\A[A-Za-z0-9][A-Za-z0-9._-]{0,120}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,120})?\z'
                    Assert-WdMindString $claim.lease_expires_utc $unknownTime
                    if ($claim.expired -isnot [bool] -and $claim.expired -cne 'UNKNOWN') { throw 'shape' }
                }
            } elseif ($row.observed_in_window -is [bool]) {
                Assert-WdMindObject $row @('observed_in_window','status')
                if (@('not_seen_in_window','unverified_identity_rows_only') -cnotcontains $row.status) { throw 'shape' }
            } else {
                Assert-WdMindObject $row @('observed_in_window','status','identity_verification')
                if ($row.observed_in_window -cne 'UNKNOWN' -or $row.status -cne 'roster_unbound' -or
                    $row.identity_verification -cne 'declared_not_authenticated') { throw 'shape' }
            }
        }
        if ($View.prs -isnot [pscustomobject]) { throw 'shape' }
        foreach ($property in $View.prs.PSObject.Properties) {
            Assert-WdMindString $property.Name '\A[0-9]+\z'
            $pr=$property.Value
            Assert-WdMindObject $pr @('pr','head','base','class','reported_ci_status','missing')
            Assert-WdMindNumber $pr.pr -Integer
            if ([string]$pr.pr -cne $property.Name) { throw 'shape' }
            Assert-WdMindString $pr.head '\A(?:UNKNOWN|[0-9a-f]{40})\z'
            Assert-WdMindString $pr.base '\A(?:UNKNOWN|[0-9a-f]{40})\z'
            if (@('UNKNOWN','a','b','on-allowlist') -cnotcontains $pr.class -or
                @('UNKNOWN','reported_green','reported_not_green') -cnotcontains $pr.reported_ci_status -or
                $pr.missing -isnot [array]) { throw 'shape' }
            foreach ($missing in $pr.missing) { if ($missing -cne 'gate_eligibility_UNKNOWN') { throw 'shape' } }
        }
    }
    function Assert-WdMindReceiptDirectory([string] $Path) {
        $current=$Path
        while ($current) {
            $attributes=[IO.File]::GetAttributes($current)
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                ($attributes -band [IO.FileAttributes]::Directory) -eq 0) { throw 'receipt_path' }
            $parent=[IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }
            $current=$parent
        }
    }
    function Assert-WdMindReceiptTarget([string] $Path) {
        try { $attributes=[IO.File]::GetAttributes($Path) }
        catch [IO.FileNotFoundException] { return }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($attributes -band [IO.FileAttributes]::Directory) -ne 0) { throw 'receipt_target' }
    }
    function Get-WdMindReceiptAudit([string] $Path) {
        # Receipt paths are persistent fully qualified C: directories. Refuse
        # lexical aliases before normalization, including parent hops and ADS.
        if ($Path -cnotmatch '\A[Cc]:[\\/]' -or $Path.Substring(3) -match '[:*?"<>|]' -or
            @($Path.Substring(3).Split([char[]]'\/') | Where-Object {
                $_ -eq '.' -or $_ -eq '..' -or $_ -match '[. ]$'
            }).Count -ne 0) { throw 'receipt_path' }
        $directory=[IO.Path]::GetFullPath($Path)
        Assert-WdMindReceiptDirectory $directory
        return Join-Path $directory '.codex-audit'
    }
    if ($ParentBoundContent) {
        $result.reason='parent_approval_refused'
        $operatorPolicySnapshot=$null
        $owner=$null;$temp=$null;$receiptPublished=$false
        try {
            if (@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5') -cnotcontains $Lane) {
                throw 'lane'
            }
            if (-not [string]::IsNullOrEmpty($OperatorPolicyPath)) {
                $operatorPolicySnapshot=Read-WdMindOperatorPolicySnapshot -Path $OperatorPolicyPath -ExpectedSha256 $OperatorPolicySha256
                $ApprovedIdentifierPolicyBytes=$operatorPolicySnapshot.Bytes
            }
            [void](Test-WdMindParentAdmission -Enabled -BundleRoot $BundleRoot -ExpectedManifestHash $ExpectedManifestHash `
                -OperatorPolicySha256 $OperatorPolicySha256 -OperatorApprovalReferenceSha256 $OperatorApprovalReferenceSha256 `
                -ApprovedPolicyBytes $ApprovedIdentifierPolicyBytes)
            $result.reason='parent_content_unavailable'
            # The live local client rereads the operator-selected pinned file;
            # a byte-only admission has no persistent file to recheck.
            if ($null -ne $operatorPolicySnapshot -and $Worktree) {
                $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
                [void](Get-WdMindReceiptAudit $Worktree)
                $owner=Start-WdMindLocalCandidate -Root $root -Manifest $ExpectedManifestHash `
                    -PolicyPath ([IO.Path]::GetFullPath($OperatorPolicyPath)) -PolicySha $OperatorPolicySha256 `
                    -ApprovalSha $OperatorApprovalReferenceSha256 -LaneName $Lane -Clock $requestClock -Budget $TimeoutSeconds
                if($owner.State -ceq 'HOLD'){
                    $result.outcome='unknown'
                    $result.reason=if($requestClock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){'timeout'}else{'reader_unknown'}
                }
                elseif($owner.State -ceq 'EXIT_FAILED'){$result.reason='reader_refused';$result.cleanup='wrapper_exited'}
                elseif($owner.State -ceq 'EXITED'){
                    $result.cleanup='wrapper_exited'
                    $io=$global:WdMindParentBootstrapIO[$owner.Token]
                    $completed=ConvertFrom-WdMindCompletedLocalBootstrap -Owner $owner `
                        -Process $global:WdMindParentBootstrapProcesses[$owner.Token].Process -IO $io
                    if($null -eq $completed){throw 'local_output'}
                    $jsonArgs=@{InputObject=$completed.view_json;ErrorAction='Stop'}
                    if((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')){$jsonArgs.DateKind='String'}
                    $view=ConvertFrom-Json @jsonArgs
                    $manifest=(Read-WdMindPinnedSnapshot -Path (Join-Path $root 'deployment-manifest.json') `
                        -Pin $ExpectedManifestHash -Limit 4194304).Text | ConvertFrom-Json -ErrorAction Stop
                    $fleetPin=$manifest.files.PSObject.Properties['wd-fleet.json']
                    if($null -eq $fleetPin -or $fleetPin.Value -isnot [string]){throw 'fleet_pin'}
                    $fleet=Read-WdMindPinnedSnapshot -Path (Join-Path $root 'wd-fleet.json') -Pin $fleetPin.Value
                    $keys=@('schema','authority','lane','fleet_sha256','sender_authentication','index_match',
                        'same_user_aba','current_status_freshness','results')
                    if($view -isnot [pscustomobject] -or @($view.PSObject.Properties).Count -ne $keys.Count -or
                        @($view.PSObject.Properties.Name | Where-Object {$_ -cnotin $keys}).Count -ne 0 -or
                        $view.schema -cne 'wd.mind-flat-verified-session.v1' -or $view.authority -cne 'none' -or
                        $view.lane -cne $Lane -or $view.fleet_sha256 -cne $fleet.Hash -or
                        $view.sender_authentication -cne 'UNKNOWN' -or $view.index_match -cne 'UNKNOWN' -or
                        $view.same_user_aba -cne 'UNKNOWN' -or $view.current_status_freshness -cne 'UNKNOWN' -or
                        $view.results -isnot [array] -or $view.results.Count -lt 1 -or $view.results.Count -gt 3){throw 'local_shape'}
                    $refs=@()
                    foreach($hit in $view.results){
                        $hitKeys=@('text','source_event_sha256','event_time_utc_floor','source_agent','event_age_seconds_upper_bound')
                        if($hit -isnot [pscustomobject] -or @($hit.PSObject.Properties).Count -ne $hitKeys.Count -or
                            @($hit.PSObject.Properties.Name | Where-Object {$_ -cnotin $hitKeys}).Count -ne 0 -or
                            $hit.text -isnot [string] -or [string]::IsNullOrWhiteSpace($hit.text) -or $hit.text.Length -gt 8192 -or
                            $hit.source_event_sha256 -isnot [string] -or $hit.source_event_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
                            $hit.source_agent -cnotin @('codex-lead-1','codex-tools-1','fable-5') -or
                            ($hit.event_age_seconds_upper_bound -isnot [int] -and $hit.event_age_seconds_upper_bound -isnot [long]) -or
                            $hit.event_age_seconds_upper_bound -lt 0 -or $hit.event_time_utc_floor -isnot [string] -or
                            $hit.event_time_utc_floor -cnotmatch '\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\z'){throw 'local_hit'}
                        $stamp=[DateTimeOffset]::MinValue
                        if(-not [DateTimeOffset]::TryParse($hit.event_time_utc_floor,[Globalization.CultureInfo]::InvariantCulture,
                            [Globalization.DateTimeStyles]::AssumeUniversal,[ref]$stamp) -or $stamp -gt [DateTimeOffset]::UtcNow){throw 'local_time'}
                        $refs+=@($hit.source_event_sha256)
                    }
                    [void](Read-WdMindOperatorPolicySnapshot -Path $OperatorPolicyPath -ExpectedSha256 $OperatorPolicySha256)
                    [void](Test-WdMindParentAdmission -Enabled -BundleRoot $BundleRoot -ExpectedManifestHash $ExpectedManifestHash `
                        -OperatorPolicySha256 $OperatorPolicySha256 -OperatorApprovalReferenceSha256 $OperatorApprovalReferenceSha256 `
                        -ApprovedPolicyBytes $ApprovedIdentifierPolicyBytes)
                    if($requestClock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){throw 'local_timeout'}
                    $result.view_json=$completed.view_json;$result.view_sha256=$completed.view_sha256
                    $result.outcome='success';$result.reason='content_projection'
                    $audit=Get-WdMindReceiptAudit $Worktree
                    if(-not [IO.Directory]::Exists($audit)){[void][IO.Directory]::CreateDirectory($audit)}
                    Assert-WdMindReceiptDirectory $audit
                    $path=Join-Path $audit 'wd-mind-startup-view.json';Assert-WdMindReceiptTarget $path
                    # Publication itself can overrun the deadline. A durable
                    # record cannot claim that delivery finished on time.
                    $record=[ordered]@{schema=$result.schema;lane=$Lane;outcome='unconfirmed';reason='content_result_pending_delivery';
                        authority='none';view_sha256=$result.view_sha256;cleanup=$result.cleanup;
                        manifest_sha256=$ExpectedManifestHash.ToLowerInvariant();observed_utc=[DateTime]::UtcNow.ToString('o');
                        fleet_sha256=$fleet.Hash;source_event_refs=$refs;observation_status='OBSERVED'}
                    $temp=$path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
                    $stream=[IO.File]::Open($temp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                    try{$bytes=[Text.Encoding]::ASCII.GetBytes(($record|ConvertTo-Json -Compress));$stream.Write($bytes,0,$bytes.Length)}finally{$stream.Dispose()}
                    Assert-WdMindReceiptDirectory $audit;Assert-WdMindReceiptTarget $path
                    if($requestClock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){throw 'local_timeout'}
                    if([IO.File]::Exists($path)){[IO.File]::Replace($temp,$path,$null)}else{[IO.File]::Move($temp,$path)}
                    $receiptPublished=$true
                    $result.record_written=$true
                }
            }
        } catch {
            if($result.reason -cne 'parent_approval_refused'){
                if($requestClock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){$result.outcome='unknown';$result.reason='timeout'}
                else{$result.outcome='refused';$result.reason='startup_read_refused'}
            }
            $result.view_json='';$result.view_sha256='';$result.record_written=$false
        } finally {
            if($null -ne $temp -and -not $receiptPublished){$result.cleanup='receipt_temp_unconfirmed'}
            # Completed children and fully drained pipes can be released. Any
            # unfinished or uncertain child remains reachable in HOLD.
            if($null -ne $owner -and $owner.State -cin @('EXITED','EXIT_FAILED')){
                $io=$global:WdMindParentBootstrapIO[$owner.Token]
                $proc=$global:WdMindParentBootstrapProcesses[$owner.Token].Process
                if($null -ne $io -and $io.OutDone -and $io.ErrDone -and $proc.HasExited){
                    $io.OutBytes.Dispose();$proc.Dispose()
                    $global:WdMindParentBootstrapIO.Remove($owner.Token)
                    $global:WdMindParentBootstrapProcesses.Remove($owner.Token)
                    $global:WdMindLocalCandidateOwners.Remove($owner.Token)
                }
            }
        }
        $ApprovedIdentifierPolicyBytes=$null
        $operatorPolicySnapshot=$null
        [void]$PSBoundParameters.Remove('ApprovedIdentifierPolicyBytes')
        if($result.outcome -ceq 'success' -and $requestClock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)){
            # A pathname may have been replaced by another writer after any
            # comparison. Without an identity-bound delete do not remove it.
            $result.outcome='unknown';$result.reason='receipt_expired_cleanup_failed'
            $result.cleanup='receipt_unconfirmed';$result.view_json='';$result.view_sha256='';$result.record_written=$false
        }
        return [pscustomobject]$result
    }
    try {
        if ($Worktree) { [void](Get-WdMindReceiptAudit $Worktree) }
        if (@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5') -cnotcontains $Lane) { throw 'lane' }
        $root = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $manifestPath = Join-Path $root 'deployment-manifest.json'
        $manifest = Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304
        $deployment = $manifest.Text | ConvertFrom-Json -ErrorAction Stop
        $paths = @('wd-fleet.json','Get-WdMindReadConfig.ps1','Invoke-WdBridgePython.ps1',
            'tools-bootstrap/tools/wd_bridge_mind_session.py')
        $snapshots = @{}
        foreach ($relative in $paths) {
            $pin = $deployment.files.PSObject.Properties[$relative]
            if ($null -eq $pin -or $pin.Value -isnot [string]) { throw 'pin' }
            $snapshots[$relative] = Read-WdMindPinnedSnapshot -Path (Join-Path $root $relative) -Pin $pin.Value
        }
        # Evaluate the very helper bytes whose digest was checked above, not a second file read.
        . ([scriptblock]::Create([string]$snapshots['Get-WdMindReadConfig.ps1'].Text))
        $fleetPath = Join-Path $root 'wd-fleet.json'
        $settings = Get-WdMindReadConfig -FleetPath $fleetPath -FleetSha256 $snapshots['wd-fleet.json'].Hash
        if ($null -eq $settings) {
            $result.outcome='off';$result.reason='not_configured'
        } else {
            if ([string]$settings.WD_MIND_READ_ENABLED -cne '1' -or
                [string]$settings.WD_MIND_FLEET -cne $fleetPath -or
                [string]$settings.WD_MIND_FLEET_SHA256 -cne $snapshots['wd-fleet.json'].Hash) { throw 'config' }
            $wrapper = Join-Path $root 'Invoke-WdBridgePython.ps1'
            $wrapperPin = $snapshots['Invoke-WdBridgePython.ps1'].Hash
            # Recheck the external manifest anchor before crossing into another process.
            [void](Read-WdMindPinnedSnapshot -Path $manifestPath -Pin $ExpectedManifestHash -Limit 4194304)
            $command = '$ErrorActionPreference="Stop"; if((Get-FileHash -LiteralPath ' +
                (ConvertTo-WdMindLiteral $wrapper) + ' -Algorithm SHA256).Hash.ToLowerInvariant() -cne ' +
                (ConvertTo-WdMindLiteral $wrapperPin) + '){exit 2}; $global:LASTEXITCODE=0; & ' +
                (ConvertTo-WdMindLiteral $wrapper) + ' tools/wd_bridge_mind_session.py -VerifyPackage --latest ' +
                (ConvertTo-WdMindLiteral ([string]$settings.WD_MIND_LATEST)) + ' --audit-root ' +
                (ConvertTo-WdMindLiteral ([string]$settings.WD_MIND_AUDIT_ROOT)) + ' --fleet ' +
                (ConvertTo-WdMindLiteral $fleetPath) + ' --fleet-sha256 ' +
                (ConvertTo-WdMindLiteral $snapshots['wd-fleet.json'].Hash) + '; exit $LASTEXITCODE'
            $start = New-Object Diagnostics.ProcessStartInfo
            $shell = if ($PSVersionTable.PSEdition -ceq 'Core') { 'pwsh.exe' } else { 'powershell.exe' }
            $start.FileName = Join-Path $PSHOME $shell
            $start.Arguments = '-NoProfile -NonInteractive -EncodedCommand ' +
                [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
            $start.WorkingDirectory = if ($Worktree) { [IO.Path]::GetFullPath($Worktree) } else { $root }
            $start.UseShellExecute=$false;$start.CreateNoWindow=$true
            $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
            $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false,$true)
            $start.StandardErrorEncoding = New-Object Text.UTF8Encoding($false,$true)
            foreach ($name in @($start.EnvironmentVariables.Keys)) {
                if (([string]$name).StartsWith('WD_MIND_',[StringComparison]::OrdinalIgnoreCase)) {
                    $start.EnvironmentVariables.Remove([string]$name)
                }
            }
            # A read-only projection does not need the bridge writer's ownership secret.
            $start.EnvironmentVariables.Remove('AGENT_BRIDGE_OWNER_TOKEN')
            $start.EnvironmentVariables['WD_MIND_READ_ENABLED']='1'
            $start.EnvironmentVariables['WD_BRIDGE_BUNDLE_ROOT']=$root
            $start.EnvironmentVariables['WD_BRIDGE_PYTHON_WRAPPER']=$wrapper
            $start.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedManifestHash.ToUpperInvariant()
            $start.EnvironmentVariables['AGENT_BRIDGE_AGENT']=$Lane
            $clock = [Diagnostics.Stopwatch]::StartNew()
            $process = [Diagnostics.Process]::Start($start)
            if ($null -eq $process) { throw 'start' }
            # Each pending read owns at most 4096 chars; accumulated stdout <=64 KiB,
            # stderr <=4 KiB. Never ReadToEndAsync on a potentially unbounded child.
            $outBuffer=New-Object char[] 4096;$errBuffer=New-Object char[] 4096
            $outText=New-Object Text.StringBuilder;$errChars=0
            $outRead=$process.StandardOutput.ReadAsync($outBuffer,0,$outBuffer.Length)
            $errRead=$process.StandardError.ReadAsync($errBuffer,0,$errBuffer.Length)
            $outDone=$false;$errDone=$false;$completed=$false
            $result.cleanup='unknown'
            while ($true) {
                if ($clock.ElapsedMilliseconds -ge ([long]$TimeoutSeconds*1000)) {
                    $result.outcome='unknown';$result.reason='timeout';break
                }
                if (-not $outDone -and $outRead.IsCompleted) {
                    $count=$outRead.Result
                    if ($count -eq 0) { $outDone=$true }
                    else {
                        if ($outText.Length+$count -gt 65536) { $result.reason='output_over_budget';break }
                        [void]$outText.Append($outBuffer,0,$count)
                        $outRead=$process.StandardOutput.ReadAsync($outBuffer,0,$outBuffer.Length)
                    }
                }
                if (-not $errDone -and $errRead.IsCompleted) {
                    $count=$errRead.Result
                    if ($count -eq 0) { $errDone=$true }
                    else {
                        $errChars+=$count
                        if ($errChars -gt 4096) { $result.reason='output_over_budget';break }
                        $errRead=$process.StandardError.ReadAsync($errBuffer,0,$errBuffer.Length)
                    }
                }
                if ($outDone -and $errDone -and $process.HasExited) { $completed=$true;break }
                [Threading.Thread]::Sleep(5)
            }
            if ($completed) {
                $result.cleanup='wrapper_exited'
                if ($process.ExitCode -ne 0) { $result.reason='reader_refused' }
                else {
                    $json = $outText.ToString().Trim()
                    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 65536) { throw 'output' }
                    $jsonArgs=@{InputObject=$json;ErrorAction='Stop'}
                    if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) { $jsonArgs.DateKind='String' }
                    $view=ConvertFrom-Json @jsonArgs
                    if ($view -isnot [pscustomobject]) { throw 'shape' }
                    if ($view.PSObject.Properties['schema'] -and $view.schema -ceq 'wd.mind-direct-status.v1') {
                        $fleet = $snapshots['wd-fleet.json'].Text | ConvertFrom-Json -ErrorAction Stop
                        Assert-WdMindDirectStatus $view $Lane $snapshots['wd-fleet.json'].Hash $fleet
                    } else {
                    $keys=@('schema','authority','scope','lane','as_of_utc','binding','pointer','freshness',
                        'identity','source','lanes','prs','refusal')
                    $extraKeys=@('fleet_sha256','source_event_refs','status_observation')
                    $presentExtra=@($extraKeys | Where-Object { $null -ne $view.PSObject.Properties[$_] })
                    if ($presentExtra.Count -ne 0) {
                        if ($presentExtra.Count -ne $extraKeys.Count) { throw 'shape' }
                        $keys += $extraKeys
                    }
                    if ($view -isnot [pscustomobject] -or @($view.PSObject.Properties).Count -ne $keys.Count -or
                        @($view.PSObject.Properties.Name | Where-Object {$_ -cnotin $keys}).Count -ne 0 -or
                        $view.schema -cne 'wd.bridge-mind-lane-read.v1' -or $view.authority -cne 'none' -or
                        $view.scope -cne 'read_only_lane_view_not_gate' -or $view.lane -cne $Lane -or
                        $null -ne $view.refusal -or $view.freshness -isnot [pscustomobject]) { throw 'shape' }
                    foreach ($name in @('schema','authority','scope','lane','as_of_utc')) {
                        if ($view.PSObject.Properties[$name].Value -isnot [string]) { throw 'shape' }
                    }
                    foreach ($name in @('binding','pointer','identity','source','lanes','prs')) {
                        if ($view.PSObject.Properties[$name].Value -isnot [pscustomobject]) { throw 'shape' }
                    }
                    Assert-WdMindNestedView $view
                    if ($presentExtra.Count -ne 0) {
                        $fleet = $snapshots['wd-fleet.json'].Text | ConvertFrom-Json -ErrorAction Stop
                        Assert-WdMindDirectStatus $view.status_observation $Lane $snapshots['wd-fleet.json'].Hash $fleet
                        $nestedRefs=@($view.status_observation.facts | ForEach-Object { $_.event_ref })
                        if ($view.fleet_sha256 -isnot [string] -or
                            $view.fleet_sha256 -cne $view.status_observation.fleet_sha256 -or
                            $view.source_event_refs -isnot [array] -or
                            $view.source_event_refs.Count -ne $nestedRefs.Count) { throw 'shape' }
                        for ($i=0; $i -lt $nestedRefs.Count; $i++) {
                            if ($view.source_event_refs[$i] -isnot [string] -or
                                $view.source_event_refs[$i] -cne $nestedRefs[$i]) { throw 'shape' }
                        }
                    }
                    $age=$view.freshness.age_seconds;$maxAge=$view.freshness.max_age_seconds
                    if (($age -isnot [int] -and $age -isnot [long] -and $age -isnot [double]) -or
                        ($maxAge -isnot [int] -and $maxAge -isnot [long] -and $maxAge -isnot [double]) -or
                        $view.freshness.status -cne 'fresh' -or $age -lt 0 -or $maxAge -le 0 -or
                        $maxAge -gt 900 -or $age -gt $maxAge -or [double]::IsNaN([double]$age) -or
                        [double]::IsNaN([double]$maxAge)) { throw 'shape' }
                    }
                    # Forward only the validated object. The original text can
                    # retain duplicate members that the parser already discarded.
                    $json=ConvertTo-Json -InputObject $view -Depth 16 -Compress
                    if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 65536) { throw 'output' }
                    $sha=[Security.Cryptography.SHA256]::Create()
                    try {$result.view_sha256=[BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($json))).Replace('-','').ToLowerInvariant()}
                    finally {$sha.Dispose()}
                    $result.view_json=$json;$result.outcome='success';$result.reason='status_projection'
                }
            }
        }
    } catch {
        $result.outcome='refused';$result.reason='startup_read_refused'
        $result.view_json='';$result.view_sha256=''
    } finally {
        # Never kill or retry. An unfinished wrapper/pipe remains explicitly UNKNOWN.
        if ($null -ne $process) { $process.Dispose() }
    }
    if ($Worktree) {
        try {
            $audit=Get-WdMindReceiptAudit $Worktree
            if (-not [IO.Directory]::Exists($audit)) { [void][IO.Directory]::CreateDirectory($audit) }
            Assert-WdMindReceiptDirectory $audit
            $path=Join-Path $audit 'wd-mind-startup-view.json'
            Assert-WdMindReceiptTarget $path
            $record=[ordered]@{schema=$result.schema;lane=$Lane;outcome=$result.outcome;reason=$result.reason;
                authority='none';view_sha256=$result.view_sha256;cleanup=$result.cleanup;
                manifest_sha256=$ExpectedManifestHash.ToLowerInvariant();observed_utc=[DateTime]::UtcNow.ToString('o')}
            if ($result.outcome -ceq 'success' -and $view.schema -ceq 'wd.mind-direct-status.v1') {
                $record.fleet_sha256=$view.fleet_sha256
                $record.source_event_refs=@($view.facts | ForEach-Object { $_.event_ref })
                $record.observation_status=$view.observation_status
            } elseif ($result.outcome -ceq 'success' -and $null -ne $view.PSObject.Properties['status_observation']) {
                $record.fleet_sha256=$view.fleet_sha256
                $record.source_event_refs=@($view.source_event_refs)
                $record.observation_status=$view.status_observation.observation_status
            }
            $temp=$path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
            $stream=[IO.File]::Open($temp,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
            try {
                $bytes=[Text.Encoding]::ASCII.GetBytes(($record|ConvertTo-Json -Compress))
                $stream.Write($bytes,0,$bytes.Length)
            } finally { $stream.Dispose() }
            Assert-WdMindReceiptDirectory $audit
            Assert-WdMindReceiptTarget $path
            # File APIs cannot reinterpret a directory target as a destination
            # folder, unlike Move-Item. Replace atomically only for a plain file.
            if ([IO.File]::Exists($path)) { [IO.File]::Replace($temp,$path,$null) }
            else { [IO.File]::Move($temp,$path) }
            $result.record_written=$true
        } catch {
            $result.outcome='unknown';$result.reason='record_write';$result.view_json='';$result.view_sha256=''
        }
    }
    return [pscustomobject]$result
}
