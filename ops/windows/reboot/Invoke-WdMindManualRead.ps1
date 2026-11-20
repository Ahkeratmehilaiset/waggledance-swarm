#requires -Version 5.1
<# Synthetic WIP only, default off. A result is returned only after zero exit,
   both EOFs, deadline and JSON binding checks. No owner is started.

   OPEN: the inherited wrapper checks and reopens code paths; exact executed
   bytes and same-user code integrity are not proven atomic. OPEN: in-process
   ownership does not survive closure of the owning PowerShell session. A
   blocking native call can exceed the deadline; uncertain ownership is HOLD
   and no child is terminated. Byte buffers are cleared after observed exit
   and EOF where possible; immutable .NET strings cannot be reliably erased.
   Output already emitted by a downstream consumer cannot be retracted.
   This WIP is not approved for live use. #>
$script:WdMindManualReadHelperText = $MyInvocation.MyCommand.ScriptBlock.Ast.Extent.Text

function Invoke-WdMindManualRead {
    [CmdletBinding()]
    param(
        [switch] $Enabled,
        [string] $BundleRoot='', [string] $ExpectedManifestHash='', [string] $Lane='',
        [string] $OperatorPolicyPath='', [string] $OperatorPolicySha256='',
        [string] $OperatorApprovalReferenceSha256='', [int] $TimeoutSeconds=15,
        [string] $BridgePythonPath='', [string] $BridgePythonSha256='', [string] $ExpectedFleetSha256=''
    )
    $result=[ordered]@{schema='wd.mind-manual-read-result.v1';authority='none';
        state='DISABLED';reason='disabled';data=$null;ownership='NOT_STARTED';sender_authentication='UNKNOWN'}
    if(-not $Enabled){return [pscustomobject]$result}
    $result.state='REFUSED';$result.reason='mind_manual_read_refused'
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $process=$null;$output=$null;$raw=$null;$outBuffer=$null;$errBuffer=$null
    $childComplete=$false;$startAttempted=$false;$registered=$false;$policyBytes=$null
    function Read-SemanticPinned([string] $Path,[string] $Pin,[int] $Limit) {
        if(-not [IO.Path]::IsPathRooted($Path) -or $Pin -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $Path -match '(^|[\\/])\.\.([\\/]|$)'){throw 'pin'}
        $full=[IO.Path]::GetFullPath($Path);$part=$full
        while($part){
            if(([IO.File]::GetAttributes($part) -band [IO.FileAttributes]::ReparsePoint) -ne 0){throw 'path'}
            $parent=[IO.Path]::GetDirectoryName($part)
            if(-not $parent -or $parent -ceq $part){break};$part=$parent
        }
        $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if($stream.Length -lt 1 -or $stream.Length -gt $Limit){throw 'size'}
            $bytes=New-Object byte[] ([int]$stream.Length);$offset=0
            while($offset -lt $bytes.Length){$n=$stream.Read($bytes,$offset,$bytes.Length-$offset);if($n -le 0){throw 'short'};$offset+=$n}
            if($stream.ReadByte() -ne -1){throw 'changed'}
        } finally {$stream.Dispose()}
        $sha=[Security.Cryptography.SHA256]::Create()
        try {$actual=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','').ToLowerInvariant()}
        finally {$sha.Dispose()}
        if($actual -cne $Pin.ToLowerInvariant()){throw 'pin'}
        return ,$bytes
    }
    function Assert-SemanticUniqueJson([string] $Json) {
        Add-Type -AssemblyName System.Runtime.Serialization
        $strictUtf8=New-Object Text.UTF8Encoding($false,$true)
        $reader=[Runtime.Serialization.Json.JsonReaderWriterFactory]::CreateJsonReader(
            [Text.Encoding]::UTF8.GetBytes($Json),[Xml.XmlDictionaryReaderQuotas]::Max)
        $types=@{};$keys=@{};$root=$false
        try {
            while($reader.Read()) {
                if($reader.NodeType -eq [Xml.XmlNodeType]::Element) {
                    $depth=[int]$reader.Depth;$kind=$reader.GetAttribute('type')
                    if($depth -eq 0){if($root -or $kind -cne 'object'){throw 'json'};$root=$true}
                    elseif($types[$depth-1] -ceq 'object') {
                        $name=$reader.GetAttribute('item');if($null -eq $name){$name=$reader.LocalName}
                        if(-not $keys[$depth-1].Add([string]$name)){throw 'json'}
                    }
                    if($kind -in @('object','array')) {
                        $types[$depth]=$kind
                        if($kind -ceq 'object'){$keys[$depth]=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)}
                    }
                } elseif($reader.NodeType -eq [Xml.XmlNodeType]::Text) {
                    [void]$strictUtf8.GetByteCount($reader.Value)
                } elseif($reader.NodeType -eq [Xml.XmlNodeType]::EndElement) {
                    $depth=[int]$reader.Depth
                    if($types.ContainsKey($depth)){$types.Remove($depth);$keys.Remove($depth)}
                }
            }
            if(-not $root -or $types.Count -ne 0){throw 'json'}
        } finally {$reader.Close()}
    }
    function Assert-SemanticKeys($Object,[string[]] $Names) {
        if($Object -isnot [pscustomobject]){throw 'shape'}
        $actual=@($Object.PSObject.Properties.Name)
        if($actual.Count -ne $Names.Count){throw 'shape'}
        foreach($name in $Names){if($actual -cnotcontains $name){throw 'shape'}}
    }
    function Quote-Semantic([string] $Value) { return "'"+$Value.Replace("'","''")+"'" }
    try {
        $lanes=@('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')
        if(-not [IO.Path]::IsPathRooted($BundleRoot) -or $lanes -cnotcontains $Lane -or
           $TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 300 -or
           $ExpectedManifestHash -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $OperatorPolicySha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $OperatorApprovalReferenceSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $BridgePythonSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z' -or
           $ExpectedFleetSha256 -cnotmatch '\A[0-9a-fA-F]{64}\z'){throw 'inputs'}
        $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $manifestPath=Join-Path $root 'deployment-manifest.json'
        $utf8=New-Object Text.UTF8Encoding($false,$true)
        $manifestBytes=Read-SemanticPinned $manifestPath $ExpectedManifestHash 4194304
        $manifestText=$utf8.GetString($manifestBytes).TrimStart([char]0xFEFF)
        Assert-SemanticUniqueJson $manifestText
        $manifest=$manifestText|ConvertFrom-Json -ErrorAction Stop
        if($manifest -isnot [pscustomobject] -or
           ($manifest.schema_version -isnot [int] -and $manifest.schema_version -isnot [long]) -or
           $manifest.schema_version -ne 1 -or $manifest.files -isnot [pscustomobject]){throw 'manifest'}
        $pins=@{};$descriptorBytes=$null
        foreach($name in @('Invoke-WdMindManualRead.ps1','Invoke-WdBridgePython.ps1',
                            'tools-bootstrap/tools/wd_bridge_mind_session.py','wd-fleet.json',
                            'wd-mind-identifier-policy-approval.json')) {
            $entry=$manifest.files.PSObject.Properties[$name]
            if($null -eq $entry -or $entry.Value -isnot [string]){throw 'manifest'}
            $bytes=Read-SemanticPinned (Join-Path $root $name) ([string]$entry.Value) 8388608
            $pins[$name]=([string]$entry.Value).ToLowerInvariant()
            if($name -ceq 'Invoke-WdMindManualRead.ps1' -and
               $utf8.GetString($bytes).TrimStart([char]0xFEFF) -cne $script:WdMindManualReadHelperText){throw 'helper'}
            if($name -ceq 'wd-mind-identifier-policy-approval.json'){$descriptorBytes=$bytes}
        }
        if($pins['wd-fleet.json'] -cne $ExpectedFleetSha256.ToLowerInvariant()){throw 'fleet'}
        $descriptorText=$utf8.GetString($descriptorBytes)
        Assert-SemanticUniqueJson $descriptorText
        $descriptor=$descriptorText|ConvertFrom-Json -ErrorAction Stop
        Assert-SemanticKeys $descriptor @('schema','identifier_policy_sha256','approval_reference_sha256')
        if($descriptor.schema -isnot [string] -or $descriptor.identifier_policy_sha256 -isnot [string] -or
           $descriptor.approval_reference_sha256 -isnot [string] -or
           $descriptor.schema -cne 'wd.mind-identifier-policy-approval.v1' -or
           $descriptor.identifier_policy_sha256 -cne $OperatorPolicySha256.ToLowerInvariant() -or
           $descriptor.approval_reference_sha256 -cne $OperatorApprovalReferenceSha256.ToLowerInvariant()){throw 'approval'}
        $policyBytes=Read-SemanticPinned $OperatorPolicyPath $OperatorPolicySha256 65536
        [Array]::Clear($policyBytes,0,$policyBytes.Length);$policyBytes=$null
        [void](Read-SemanticPinned $BridgePythonPath $BridgePythonSha256 134217728)
        [void](Read-SemanticPinned $manifestPath $ExpectedManifestHash 4194304)
        if($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){throw 'timeout'}
        $wrapper=Join-Path $root 'Invoke-WdBridgePython.ps1'
        $command='$ProgressPreference="SilentlyContinue";$ErrorActionPreference="Stop";$global:LASTEXITCODE=0;$f=[IO.File]::OpenRead('+(Quote-Semantic $wrapper)+');'+
            '$s=[Security.Cryptography.SHA256]::Create();try{$h=[BitConverter]::ToString($s.ComputeHash($f)).Replace("-","").ToLowerInvariant()}'+
            'finally{$f.Dispose();$s.Dispose()};if($h -cne '+(Quote-Semantic $pins['Invoke-WdBridgePython.ps1'])+'){exit 2};& '+(Quote-Semantic $wrapper)+
            ' tools/wd_bridge_mind_session.py -VerifyPackage --mind-manual-candidate-read --policy-path '+
            (Quote-Semantic ([IO.Path]::GetFullPath($OperatorPolicyPath)))+' --policy-sha256 '+
            (Quote-Semantic $OperatorPolicySha256.ToLowerInvariant())+' --approval-reference-sha256 '+
            (Quote-Semantic $OperatorApprovalReferenceSha256.ToLowerInvariant())+' --lane '+(Quote-Semantic $Lane)+
            ' --timeout-seconds '+$TimeoutSeconds+';exit $LASTEXITCODE'
        $start=New-Object Diagnostics.ProcessStartInfo
        $shell=if($PSVersionTable.PSEdition -ceq 'Core'){'pwsh.exe'}else{'powershell.exe'}
        $start.FileName=Join-Path $PSHOME $shell
        $start.Arguments='-NoProfile -NonInteractive -EncodedCommand '+[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
        $start.WorkingDirectory=$root;$start.UseShellExecute=$false;$start.CreateNoWindow=$true
        $start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
        $allow=@('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA')
        foreach($name in @($start.EnvironmentVariables.Keys)) {
            if($allow -cnotcontains ([string]$name).ToUpperInvariant()){$start.EnvironmentVariables.Remove([string]$name)}
        }
        $start.EnvironmentVariables['PSModulePath']=(Join-Path $PSHOME 'Modules')+';'+(Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules')
        $start.EnvironmentVariables['WD_REBOOT_EXPECTED_MANIFEST_HASH']=$ExpectedManifestHash.ToUpperInvariant()
        $start.EnvironmentVariables['WD_BRIDGE_PYTHON']=[IO.Path]::GetFullPath($BridgePythonPath)
        $start.EnvironmentVariables['WD_BRIDGE_PYTHON_SHA256']=$BridgePythonSha256.ToUpperInvariant()
        # Retain the process object before the synchronous OS start call.
        # If start raises, whether a native child exists is unknown: the
        # owning worker stays held rather than discarding the process object.
        if(-not (Test-Path variable:global:WdMindManualReadRecords)){$global:WdMindManualReadRecords=@{}}
        if($global:WdMindManualReadRecords -isnot [hashtable] -or $global:WdMindManualReadRecords.Count -ne 0){throw 'owner'}
        $registry=$global:WdMindManualReadRecords;$token=[Guid]::NewGuid().ToString('N')
        $process=New-Object Diagnostics.Process;$process.StartInfo=$start
        $record=@{Process=$process;State='HOLD';OutDone=$false;ErrDone=$false}
        $registry[$token]=$record;$registered=$true
        if(-not [object]::ReferenceEquals($global:WdMindManualReadRecords,$registry) -or
           -not [object]::ReferenceEquals($registry[$token],$record)){throw 'owner'}
        $startAttempted=$true
        if(-not $process.Start()){throw 'start'}
        $process.StandardInput.Close()
        $outBuffer=New-Object byte[] 4096;$errBuffer=New-Object byte[] 4096
        $output=New-Object IO.MemoryStream
        $outRead=$process.StandardOutput.BaseStream.ReadAsync($outBuffer,0,4096)
        $errRead=$process.StandardError.BaseStream.ReadAsync($errBuffer,0,4096)
        $outDone=$false;$errDone=$false;$refused=$false;$reportedHold=$false;$errCount=0;$outCount=0
        # Even after timeout/refusal keep ownership and drain until actual exit.
        # The outer lane process can stop waiting without killing this helper.
        while($true){
            if($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){
                $refused=$true
                if(-not $reportedHold){[Console]::Error.WriteLine('Mind manual read HOLD: awaiting owned child and pipe EOFs.');$reportedHold=$true}
            }
            if(-not $outDone -and $outRead.IsCompleted){
                $n=$outRead.GetAwaiter().GetResult();if($n -eq 0){$outDone=$true}else{
                    $outCount+=$n;if($outCount -gt 16384){$refused=$true}
                    if(-not $refused){$output.Write($outBuffer,0,$n)}
                    $outRead=$process.StandardOutput.BaseStream.ReadAsync($outBuffer,0,4096)
                }
            }
            if(-not $errDone -and $errRead.IsCompleted){
                $n=$errRead.GetAwaiter().GetResult();if($n -eq 0){$errDone=$true}else{
                    $errCount+=$n;if($errCount -gt 0){$refused=$true}
                    $errRead=$process.StandardError.BaseStream.ReadAsync($errBuffer,0,4096)
                }
            }
            $record.OutDone=$outDone;$record.ErrDone=$errDone
            if($outDone -and $errDone -and $process.HasExited){$childComplete=$true;break}
            [Threading.Thread]::Sleep(5)
        }
        $result.ownership='COMPLETED'
        if($refused -or $process.ExitCode -ne 0 -or $clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){throw 'child'}
        $raw=$output.ToArray()
        if($raw.Length -eq 0 -or $raw.Length -gt 16384){throw 'output'}
        foreach($byte in $raw){if($byte -lt 32 -or $byte -gt 126){throw 'output'}}
        $text=[Text.Encoding]::ASCII.GetString($raw)
        Assert-SemanticUniqueJson $text
        $view=$text|ConvertFrom-Json -ErrorAction Stop
        Assert-SemanticKeys $view @('schema','authority','lane','fleet_sha256','sender_authentication',
            'index_match','same_user_aba','current_status_freshness','results')
        foreach($field in @('schema','authority','lane','fleet_sha256','sender_authentication',
                            'index_match','same_user_aba','current_status_freshness')) {
            if($view.$field -isnot [string]){throw 'shape'}
        }
        if($view.schema -cne 'wd.mind-flat-verified-session.v1' -or $view.authority -cne 'none' -or
           $view.lane -cne $Lane -or $view.fleet_sha256 -cne $ExpectedFleetSha256.ToLowerInvariant() -or
           $view.sender_authentication -cne 'UNKNOWN' -or $view.index_match -cne 'UNKNOWN' -or
           $view.same_user_aba -cne 'UNKNOWN' -or $view.current_status_freshness -cne 'UNKNOWN' -or
           $view.results -isnot [array] -or $view.results.Count -lt 1 -or $view.results.Count -gt 3){throw 'shape'}
        $refs=@();$times=@();$ages=@();$now=[DateTimeOffset]::UtcNow
        foreach($hit in $view.results){
            Assert-SemanticKeys $hit @('text','source_event_sha256','event_time_utc_floor','source_agent','event_age_seconds_upper_bound')
            if($hit.text -isnot [string] -or [string]::IsNullOrWhiteSpace($hit.text) -or $hit.text.Length -gt 8192 -or
               $hit.source_event_sha256 -isnot [string] -or $hit.source_event_sha256 -cnotmatch '\A[0-9a-f]{64}\z' -or
               $hit.source_agent -isnot [string] -or $hit.source_agent -cnotin @('codex-lead-1','codex-tools-1','fable-5') -or
               ($Lane -cin @('codex-lead-1','codex-tools-1','fable-5') -and $hit.source_agent -cne $Lane) -or
               ($hit.event_age_seconds_upper_bound -isnot [int] -and $hit.event_age_seconds_upper_bound -isnot [long]) -or
               $hit.event_age_seconds_upper_bound -lt 0 -or $hit.event_time_utc_floor -isnot [string] -or
               $hit.event_time_utc_floor -cnotmatch '\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z\z'){throw 'hit'}
            [void]$utf8.GetByteCount($hit.text)
            $when=[DateTimeOffset]::MinValue
            if(-not [DateTimeOffset]::TryParse($hit.event_time_utc_floor,[Globalization.CultureInfo]::InvariantCulture,
                    [Globalization.DateTimeStyles]::None,[ref]$when) -or $when -gt $now){throw 'time'}
            $observedAge=[long][Math]::Ceiling(($now-$when).TotalSeconds)
            $declaredAge=[long]$hit.event_age_seconds_upper_bound
            $hit.event_age_seconds_upper_bound=if($observedAge -gt $declaredAge){$observedAge}else{$declaredAge}
            $refs+=@([string]$hit.source_event_sha256);$times+=@([string]$hit.event_time_utc_floor);$ages+=@([long]$hit.event_age_seconds_upper_bound)
        }
        [void](Read-SemanticPinned $manifestPath $ExpectedManifestHash 4194304)
        $policyBytes=Read-SemanticPinned $OperatorPolicyPath $OperatorPolicySha256 65536
        [Array]::Clear($policyBytes,0,$policyBytes.Length);$policyBytes=$null
        foreach($name in $pins.Keys){[void](Read-SemanticPinned (Join-Path $root $name) $pins[$name] 8388608)}
        [void](Read-SemanticPinned $BridgePythonPath $BridgePythonSha256 134217728)
        if($clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){throw 'timeout'}
        $result.state='SUCCESS';$result.reason='validated_child_result';$result.data=$view
    } catch {$result.state='REFUSED';$result.reason='mind_manual_read_refused';$result.data=$null}
    finally {
        if($null -ne $policyBytes){[Array]::Clear($policyBytes,0,$policyBytes.Length)}
        if($null -ne $process){
            # Retain ownership on every uncertain path. The enclosing launcher
            # can continue at its deadline; no process is terminated here.
            if(-not $childComplete -and $startAttempted){
                # If an exception prevented observing both EOFs, neither an
                # exited PID nor a disposed Process proves the pipes settled.
                # Keep this worker and its resources held for reconciliation.
                try {[Console]::Error.WriteLine('Mind manual read HOLD: child ownership or pipe EOF is uncertain.')}catch{}
                while($true){[Threading.Thread]::Sleep(1000)}
            }
            $process.Dispose()
        }
        if($null -ne $output){try {[Array]::Clear($output.GetBuffer(),0,[int]$output.Length);$output.Dispose()}catch{}}
        if($null -ne $raw){[Array]::Clear($raw,0,$raw.Length)}
        if($null -ne $outBuffer){[Array]::Clear($outBuffer,0,$outBuffer.Length)}
        if($null -ne $errBuffer){[Array]::Clear($errBuffer,0,$errBuffer.Length)}
        if($registered){[void]$registry.Remove($token)}
    }
    if($result.state -ceq 'SUCCESS' -and $clock.Elapsed.TotalSeconds -ge $TimeoutSeconds){
        $result.state='REFUSED';$result.reason='mind_manual_read_refused';$result.data=$null
    }
    return [pscustomobject]$result
}
