#requires -Version 7.0
# Default-OFF synthetic candidate. The caller owns authentication of this
# wrapper, the external commit/manifest anchor, lane identity and metadata.
# No whole-call preflight deadline, durable quarantine, same-user isolation or
# path-to-inode/TOCTOU guarantee is provided. UNVALIDATED is not a Mind answer.
if ($null -eq (Get-Variable WdQ4BoundSessionUnknown -Scope Script -ErrorAction SilentlyContinue)) {
    $script:WdQ4BoundSessionUnknown=$false
}

function Invoke-WdQ4BoundMetadataChild {
    # This pump accepts metadata ONLY. The caller must authenticate the child,
    # arguments, cwd and inputs and must isolate process creation if needed.
    # It does not prove descendants are gone, same-user isolation, OS call
    # deadlines, or durable quarantine. A caller must treat unknown as sticky.
    [CmdletBinding()]
    param([string]$Executable,[string[]]$Arguments,[string]$WorkingDirectory,
          [byte[]]$InputBytes,[int]$TimeoutSeconds,[int]$MaxOutputBytes)
    $process=$null;$started=$false;$output=$null
    try {
        if (-not [IO.Path]::IsPathFullyQualified($Executable) -or
            -not [IO.Path]::IsPathFullyQualified($WorkingDirectory) -or
            $null -eq $Arguments -or $Arguments.Count -gt 64 -or
            $null -eq $InputBytes -or $InputBytes.Length -gt 35000000 -or
            $TimeoutSeconds -lt 1 -or $TimeoutSeconds -gt 1800 -or
            $MaxOutputBytes -lt 1 -or $MaxOutputBytes -gt 524288) { throw 'invalid' }
        foreach ($item in $Arguments) {
            if ($null -eq $item -or $item.Length -gt 32767 -or $item.Contains([char]0)) { throw 'invalid' }
        }
        $start=[Diagnostics.ProcessStartInfo]::new()
        $start.FileName=$Executable;$start.WorkingDirectory=$WorkingDirectory
        $start.UseShellExecute=$false;$start.CreateNoWindow=$true
        $start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
        foreach ($item in $Arguments) { [void]$start.ArgumentList.Add($item) }
        $process=[Diagnostics.Process]::new();$process.StartInfo=$start
        # Conservatively treat even an exception during Start as uncertain.
        $started=$true
        if (-not $process.Start()) { throw 'invalid' }
        $clock=[Diagnostics.Stopwatch]::StartNew()
        $inputTask=$process.StandardInput.BaseStream.WriteAsync($InputBytes,0,$InputBytes.Length)
        $inputClosed=$false;$outDone=$false;$errDone=$false;$total=0
        $outBuf=[byte[]]::new(8192);$errBuf=[byte[]]::new(8192)
        $outTask=$process.StandardOutput.BaseStream.ReadAsync($outBuf,0,$outBuf.Length)
        $errTask=$process.StandardError.BaseStream.ReadAsync($errBuf,0,$errBuf.Length)
        $output=[IO.MemoryStream]::new()
        while ($true) {
            if ($clock.ElapsedMilliseconds -ge 1000L * $TimeoutSeconds) { throw 'unknown' }
            if (-not $inputClosed -and $inputTask.IsCompleted) {
                [void]$inputTask.GetAwaiter().GetResult();$process.StandardInput.Close();$inputClosed=$true
            }
            if (-not $outDone -and $outTask.IsCompleted) {
                $count=$outTask.GetAwaiter().GetResult()
                if ($count -eq 0) { $outDone=$true } else {
                    $total+=$count;if ($total -gt $MaxOutputBytes) { throw 'unknown' }
                    $output.Write($outBuf,0,$count)
                    $outTask=$process.StandardOutput.BaseStream.ReadAsync($outBuf,0,$outBuf.Length)
                }
            }
            if (-not $errDone -and $errTask.IsCompleted) {
                $count=$errTask.GetAwaiter().GetResult()
                if ($count -eq 0) { $errDone=$true } else {
                    $total+=$count;if ($total -gt $MaxOutputBytes) { throw 'unknown' }
                    $errTask=$process.StandardError.BaseStream.ReadAsync($errBuf,0,$errBuf.Length)
                }
            }
            if ($process.HasExited -and $inputClosed -and $outDone -and $errDone) { break }
            [Threading.Thread]::Sleep(5)
        }
        if ($process.ExitCode -ne 0) { return [pscustomobject]@{Outcome='unknown';Output=$null} }
        return [pscustomobject]@{Outcome='completed';Output=$output.ToArray()}
    } catch {
        if ($started) {
            try { if (-not $process.HasExited) { $process.Kill($true) } } catch {}
            return [pscustomobject]@{Outcome='unknown';Output=$null}
        }
        return [pscustomobject]@{Outcome='refused';Output=$null}
    } finally {
        if ($null -ne $output) { $output.Dispose() }
        if ($null -ne $process) { $process.Dispose() }
    }
}

function Assert-WdQ4BoundCodeRoot {
    # Deployment MUST already be authenticated against an independent anchor.
    # This detects point-in-time mismatches, not same-user replacement/TOCTOU.
    [CmdletBinding()]
    param([string]$BundleRoot,$Deployment)
    if (-not [IO.Path]::IsPathFullyQualified($BundleRoot) -or $null -eq $Deployment.files) { throw 'invalid' }
    $root=[IO.Path]::GetFullPath((Join-Path $BundleRoot 'tools-bootstrap')).TrimEnd('\')
    $current=$root
    while ($current) {
        $attrs=[IO.File]::GetAttributes($current)
        if (($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($attrs -band [IO.FileAttributes]::Directory) -eq 0) { throw 'invalid' }
        $parent=[IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break };$current=$parent
    }
    $expected=[Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
    $caseNames=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $dirs=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($property in $Deployment.files.PSObject.Properties) {
        $name=[string]$property.Name
        if (-not $name.StartsWith('tools-bootstrap/',[StringComparison]::Ordinal)) { continue }
        $relative=$name.Substring('tools-bootstrap/'.Length)
        if (-not $relative -or $relative.Contains('\') -or $relative.Contains(':') -or
            [string]$property.Value -cnotmatch '^[0-9a-fA-F]{64}$' -or -not $caseNames.Add($relative)) { throw 'invalid' }
        $parts=$relative.Split('/')
        foreach ($part in $parts) {
            if (-not $part -or $part -in @('.','..') -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
                $part -ieq '__pycache__' -or $part -imatch '\.py[co]$' -or $part -match '[\x00-\x1f<>|"?*]') { throw 'invalid' }
        }
        for ($i=1;$i -lt $parts.Count;$i++) { [void]$dirs.Add(($parts[0..($i-1)] -join '/')) }
        $expected.Add($relative,([string]$property.Value).ToLowerInvariant())
    }
    if ($expected.Count -eq 0) { throw 'invalid' }
    $seen=[Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    $stack=[Collections.Generic.Stack[string]]::new();$stack.Push($root)
    while ($stack.Count -gt 0) {
        $dir=$stack.Pop()
        foreach ($entry in @(Get-ChildItem -LiteralPath $dir -Force -ErrorAction Stop)) {
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'invalid' }
            $full=[IO.Path]::GetFullPath($entry.FullName)
            if (-not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'invalid' }
            $relative=$full.Substring($root.Length+1).Replace('\','/')
            if (($entry.Attributes -band [IO.FileAttributes]::Directory) -ne 0) {
                if (-not $dirs.Contains($relative)) { throw 'invalid' };$stack.Push($full)
            } else {
                if (-not $expected.ContainsKey($relative) -or -not $seen.Add($relative) -or
                    (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant() -cne $expected[$relative]) { throw 'invalid' }
            }
        }
    }
    if ($seen.Count -ne $expected.Count) { throw 'invalid' }
}

function Read-WdQ4BoundSnapshot {
    param([string]$Path,[int]$Limit)
    if ($Path -cnotmatch '^[A-Za-z]:\\' -or -not [IO.Path]::IsPathFullyQualified($Path)) { throw 'invalid' }
    $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($Path))
    if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) { throw 'invalid' }
    $current=[IO.Path]::GetFullPath($Path)
    while ($current) {
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'invalid' }
        $parent=[IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break };$current=$parent
    }
    $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $Limit) { throw 'invalid' }
        $bytes=[byte[]]::new([int]$stream.Length);$pos=0
        while ($pos -lt $bytes.Length) { $n=$stream.Read($bytes,$pos,$bytes.Length-$pos);if ($n -le 0) { throw 'invalid' };$pos+=$n }
        if ($stream.ReadByte() -ne -1) { throw 'invalid' }
    } finally { $stream.Dispose() }
    return [pscustomobject]@{Bytes=$bytes;Sha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()}
}

function Assert-WdQ4BoundJsonUnique {
    param([System.Text.Json.JsonElement]$Element)
    if ($Element.ValueKind -eq [System.Text.Json.JsonValueKind]::Object) {
        $names=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $Element.EnumerateObject()) {
            if (-not $names.Add($entry.Name)) { throw 'invalid' }
            Assert-WdQ4BoundJsonUnique -Element $entry.Value
        }
    } elseif ($Element.ValueKind -eq [System.Text.Json.JsonValueKind]::Array) {
        foreach ($item in $Element.EnumerateArray()) { Assert-WdQ4BoundJsonUnique -Element $item }
    }
}

function ConvertFrom-WdQ4BoundJson {
    param([byte[]]$Bytes)
    $text=[Text.UTF8Encoding]::new($false,$true).GetString($Bytes)
    $document=[System.Text.Json.JsonDocument]::Parse([string]$text)
    try { Assert-WdQ4BoundJsonUnique -Element $document.RootElement } finally { $document.Dispose() }
    return $text | ConvertFrom-Json -ErrorAction Stop
}

function Invoke-WdQ4BoundSession {
    [CmdletBinding()]
    param([switch]$Enabled,[string]$BundleRoot,[string]$ExpectedCommit,
          [string]$ExpectedManifestSha256,[string]$PlanMetadata,[string]$ScratchRoot)
    if (-not $Enabled) { return [pscustomobject]@{Status='OFF';Output=$null} }
    if ($script:WdQ4BoundSessionUnknown) { return [pscustomobject]@{Status='UNKNOWN';Output=$null} }
    $entered=$false
    try {
        if ($BundleRoot -cnotmatch '^[A-Za-z]:\\' -or -not [IO.Path]::IsPathFullyQualified($BundleRoot) -or
            $ExpectedCommit -cnotmatch '^[0-9a-f]{40}$' -or
            $ExpectedManifestSha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $ScratchRoot -cnotmatch '^[A-Za-z]:\\' -or -not [IO.Path]::IsPathFullyQualified($ScratchRoot) -or
            -not [IO.Directory]::Exists($ScratchRoot) -or -not $PlanMetadata) { throw 'invalid' }
        $root=[IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
        $scratch=[IO.Path]::GetFullPath($ScratchRoot).TrimEnd('\')
        foreach ($path in @($root,$scratch)) {
            $drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($path))
            if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) { throw 'invalid' }
        }
        $current=$scratch
        while ($current) {
            $attrs=[IO.File]::GetAttributes($current)
            if (($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
                ($attrs -band [IO.FileAttributes]::Directory) -eq 0) { throw 'invalid' }
            $parent=[IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break };$current=$parent
        }
        if ($scratch.Equals($root,[StringComparison]::OrdinalIgnoreCase) -or
            $scratch.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) -or
            $root.StartsWith($scratch+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'invalid' }
        $manifest=Read-WdQ4BoundSnapshot (Join-Path $root 'deployment-manifest.json') 8388608
        if ($manifest.Sha256 -cne $ExpectedManifestSha256) { throw 'invalid' }
        $deployment=ConvertFrom-WdQ4BoundJson $manifest.Bytes
        if ($deployment.schema_version -ne 1 -or $deployment.source_commit -cne $ExpectedCommit -or
            $null -eq $deployment.files -or $null -eq $deployment.bridge_code_package) { throw 'invalid' }
        $metadataBytes=[Text.UTF8Encoding]::new($false,$true).GetBytes($PlanMetadata)
        if ($metadataBytes.Length -gt 16777216) { throw 'invalid' }
        $metadata=ConvertFrom-WdQ4BoundJson $metadataBytes
        $metaKeys=@('fleet_bytes_b64','expected_fleet_sha256','binding_bytes_b64','expected_binding_sha256',
                    'lane','descriptor','runtime_identity','host','manifest_bytes_b64',
                    'expected_manifest_sha256','expected_manifest_commit')
        if (@($metadata.PSObject.Properties).Count -ne $metaKeys.Count) { throw 'invalid' }
        foreach ($p in $metadata.PSObject.Properties) { if ($metaKeys -cnotcontains $p.Name) { throw 'invalid' } }
        if ($metadata.expected_manifest_sha256 -cne $ExpectedManifestSha256 -or
            $metadata.expected_manifest_commit -cne $ExpectedCommit -or
            -not [IO.Path]::GetFullPath([string]$metadata.descriptor.bundle_root).Equals($root,[StringComparison]::OrdinalIgnoreCase) -or
            -not [IO.Path]::GetFullPath([string]$metadata.host.ScratchRoot).TrimEnd('\').Equals($scratch,[StringComparison]::OrdinalIgnoreCase)) { throw 'invalid' }
        $protected=@($metadata.descriptor.cwd,$metadata.descriptor.public_plan,$metadata.descriptor.fleet_path,
            $metadata.descriptor.binding_path,$metadata.descriptor.fixed_plan_path,$metadata.descriptor.snapshot_path,
            $metadata.descriptor.runtime_manifest_path,$metadata.descriptor.outer.'runtime-root',
            $metadata.descriptor.outer.'outer-path',$metadata.host.Interpreter)
        foreach ($item in $protected) {
            if ($item -isnot [string] -or $item -cnotmatch '^[A-Za-z]:\\' -or -not [IO.Path]::IsPathFullyQualified($item)) { throw 'invalid' }
            $path=[IO.Path]::GetFullPath($item).TrimEnd('\')
            if ($scratch.Equals($path,[StringComparison]::OrdinalIgnoreCase) -or
                $scratch.StartsWith($path+'\',[StringComparison]::OrdinalIgnoreCase) -or
                $path.StartsWith($scratch+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'invalid' }
        }
        $manifestFromMetadata=[Convert]::FromBase64String([string]$metadata.manifest_bytes_b64)
        if ([Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($manifestFromMetadata)).ToLowerInvariant() -cne $ExpectedManifestSha256) { throw 'invalid' }
        $snapshots=@{}
        foreach ($name in @('BridgeCodeContext.ps1','bridge-code-files.json','WdQ4ProcessJob.ps1','Invoke-WdQ4SessionCommand.ps1')) {
            $pin=$deployment.files.PSObject.Properties[$name]
            if ($null -eq $pin -or [string]$pin.Value -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'invalid' }
            $snapshot=Read-WdQ4BoundSnapshot (Join-Path $root $name) 1048576
            if ($snapshot.Sha256 -cne ([string]$pin.Value).ToLowerInvariant()) { throw 'invalid' }
            $snapshots[$name]=$snapshot
        }
        $context=[Text.UTF8Encoding]::new($false,$true).GetString($snapshots['BridgeCodeContext.ps1'].Bytes).TrimStart([char]0xfeff)
        . ([scriptblock]::Create($context))
        $definition=Get-WdBridgeCodePackageDefinition -Path (Join-Path $root 'bridge-code-files.json')
        if ($definition.Hash.ToLowerInvariant() -cne $snapshots['bridge-code-files.json'].Sha256) { throw 'invalid' }
        foreach ($name in @('tools/wd_q4_bound_session_plan_cli.py','tools/wd_q4_bound_session_plan.py',
                             'tools/wd_q4_descriptor_host_tuple.py','tools/wd_q4_manifest_host_binding.py')) {
            if (@($definition.Definition.python_files) -cnotcontains $name) { throw 'invalid' }
        }
        $integrity=Assert-WdBridgeCodePackageIntegrity -BundleRoot $root -Deployment $deployment -Definition $definition.Definition
        Assert-WdQ4BoundCodeRoot -BundleRoot $root -Deployment $deployment
        $python=[string]$deployment.bridge_code_package.python_executable
        $pythonPin=([string]$deployment.bridge_code_package.python_executable_sha256).ToLowerInvariant()
        if ($pythonPin -cnotmatch '^[0-9a-f]{64}$' -or
            (Read-WdQ4BoundSnapshot $python 134217728).Sha256 -cne $pythonPin -or
            -not [IO.Path]::GetFullPath([string]$metadata.host.Interpreter).Equals([IO.Path]::GetFullPath($python),[StringComparison]::OrdinalIgnoreCase) -or
            $metadata.host.InterpreterSha256 -cne $pythonPin) { throw 'invalid' }
        $plannerSource=@'
import sys
sys.path.append(sys.argv.pop(1))
from tools.wd_q4_bound_session_plan_cli import main
raise SystemExit(main())
'@
        $plannerShell=@'
$ErrorActionPreference='Stop'
try {
  $wire=[Console]::In.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
  $helper=[Text.UTF8Encoding]::new($false,$true).GetString([Convert]::FromBase64String($wire.helper)).TrimStart([char]0xfeff)
  . ([scriptblock]::Create($helper))
  $r=Invoke-WdQ4ProcessJob -DetailedOutcome -Executable $wire.python -Arguments @('-I','-S','-B','-c',$wire.source,$wire.root,'--enabled') -WorkingDirectory $wire.cwd -ScratchRoot $wire.scratch -InputBytes ([Convert]::FromBase64String($wire.metadata)) -TimeoutSeconds 120 -MaxOutputBytes 262144
  if ($r.Outcome -cne 'completed' -or $r.Cleanup -cne 'confirmed' -or $r.ExitCode -ne 0 -or $r.Stdout -isnot [byte[]]) { throw 'unknown' }
  [Console]::Out.WriteLine((@{status='completed';output=[Convert]::ToBase64String($r.Stdout)} | ConvertTo-Json -Compress))
} catch { [Console]::Out.WriteLine('{"status":"unknown"}') }
'@
        $wire=@{helper=[Convert]::ToBase64String($snapshots['WdQ4ProcessJob.ps1'].Bytes);python=$python;
                source=$plannerSource;root=[string]$integrity.CodeRoot;cwd=[string]$metadata.descriptor.cwd;
                scratch=$scratch;metadata=[Convert]::ToBase64String($metadataBytes)} | ConvertTo-Json -Compress
        $wireBytes=[Text.Encoding]::UTF8.GetBytes($wire)
        if ($wireBytes.Length -gt 35000000) { throw 'invalid' }
        $pwsh=Join-Path $PSHOME 'pwsh.exe'
        $encoded=[Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($plannerShell))
        $entered=$true
        $child=Invoke-WdQ4BoundMetadataChild -Executable $pwsh -Arguments @('-NoProfile','-NonInteractive','-EncodedCommand',$encoded) `
            -WorkingDirectory ([string]$metadata.descriptor.cwd) -InputBytes $wireBytes -TimeoutSeconds 150 -MaxOutputBytes 350000
        if ($child.Outcome -cne 'completed' -or $child.Output -isnot [byte[]]) { throw 'unknown' }
        $childResult=ConvertFrom-WdQ4BoundJson $child.Output
        if ($childResult.status -cne 'completed' -or @($childResult.PSObject.Properties).Count -ne 2) { throw 'unknown' }
        $planBytes=[Convert]::FromBase64String([string]$childResult.output)
        if ($planBytes.Length -lt 1 -or $planBytes.Length -gt 262144) { throw 'unknown' }
        $plan=ConvertFrom-WdQ4BoundJson $planBytes
        $planKeys=@('status','manifest_binding','command_script','command_sha256','host_script','host_sha256','host_arguments')
        if (@($plan.PSObject.Properties).Count -ne $planKeys.Count) { throw 'unknown' }
        foreach ($p in $plan.PSObject.Properties) { if ($planKeys -cnotcontains $p.Name) { throw 'unknown' } }
        if ($plan.status -cne 'PLAN_ONLY' -or $plan.manifest_binding -cne 'MANIFEST_PINS_ONLY' -or
            $plan.command_sha256 -cne $snapshots['Invoke-WdQ4SessionCommand.ps1'].Sha256 -or
            -not [IO.Path]::GetFullPath([string]$plan.command_script).Equals([IO.Path]::GetFullPath((Join-Path $root 'Invoke-WdQ4SessionCommand.ps1')),[StringComparison]::OrdinalIgnoreCase) -or
            $plan.host_script -cne $metadata.host.HostScript -or $plan.host_sha256 -cne $metadata.host.HostSha256) { throw 'unknown' }
        $argKeys=@('JobHelper','JobHelperSha256','Interpreter','InterpreterSha256','Bootstrap','BootstrapSha256',
                   'BootstrapArguments','WorkingDirectory','ScratchRoot','TimeoutSeconds','MaxOutputBytes')
        if (@($plan.host_arguments.PSObject.Properties).Count -ne $argKeys.Count) { throw 'unknown' }
        $hostArgs=@{}
        foreach ($p in $plan.host_arguments.PSObject.Properties) {
            if ($argKeys -cnotcontains $p.Name) { throw 'unknown' }
            if ($p.Name -ceq 'BootstrapArguments') {
                if ($p.Value -isnot [array]) { throw 'unknown' }
                $list=[Collections.Generic.List[string]]::new()
                foreach ($arg in $p.Value) { if ($arg -isnot [string]) { throw 'unknown' };$list.Add($arg) }
                $hostArgs[$p.Name]=[string[]]$list.ToArray()
            } else {
                if ($p.Value -cne $metadata.host.($p.Name)) { throw 'unknown' }
                $hostArgs[$p.Name]=$p.Value
            }
        }
        $command=[Text.UTF8Encoding]::new($false,$true).GetString($snapshots['Invoke-WdQ4SessionCommand.ps1'].Bytes).TrimStart([char]0xfeff)
        function Invoke-WdQ4SessionCommand { throw 'unbound' }
        $sentinel=(Get-Item Function:Invoke-WdQ4SessionCommand).ScriptBlock
        . ([scriptblock]::Create($command))
        $fn=Get-Item Function:Invoke-WdQ4SessionCommand -ErrorAction Stop
        if ([object]::ReferenceEquals($sentinel,$fn.ScriptBlock)) { throw 'unknown' }
        $answer=& $fn.ScriptBlock -Enabled -HostScript $plan.host_script -HostSha256 $plan.host_sha256 -HostArguments $hostArgs
        if (@($answer).Count -ne 1 -or $answer -isnot [pscustomobject]) { throw 'unknown' }
        if ($answer.Status -ceq 'UNKNOWN') { $script:WdQ4BoundSessionUnknown=$true }
        return $answer
    } catch {
        if ($entered) { $script:WdQ4BoundSessionUnknown=$true;return [pscustomobject]@{Status='UNKNOWN';Output=$null} }
        return [pscustomobject]@{Status='REFUSED';Output=$null}
    }
}
