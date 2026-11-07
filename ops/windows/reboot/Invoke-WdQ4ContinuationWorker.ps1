#requires -Version 5.1
# Fixed job child. Its caller hash-pins this script and interpreter before start.
param([string]$Manifest,[string]$ManifestSha256,[string]$Bootstrap,[string]$BootstrapSha256,
      [string]$Python,[string]$PythonSha256,[string]$Config,[string]$ConfigSha256,
      [string]$Runtime,[string]$RuntimeSha256,[string]$ExpectedHostSha256='')
$ErrorActionPreference='Stop'
$entered=$false
try {
    function ExactHash([string]$Path,[string]$Pin) {
        if ($Pin -cnotmatch '^[0-9a-f]{64}$') { throw 'pin' }
        $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try { $h=[Security.Cryptography.SHA256]::Create(); try { $actual=[BitConverter]::ToString($h.ComputeHash($stream)).Replace('-','') } finally { $h.Dispose() } }
        finally { $stream.Dispose() }
        if ($actual -cne $Pin.ToUpperInvariant()) { throw 'pin' }
    }
    ExactHash $Manifest $ManifestSha256
    ExactHash $Bootstrap $BootstrapSha256
    ExactHash $Python $PythonSha256
    ExactHash $Config $ConfigSha256
    ExactHash $Runtime $RuntimeSha256
    if ($ExpectedHostSha256) {
        ExactHash (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') $ExpectedHostSha256
    }
    $env:WD_REBOOT_EXPECTED_MANIFEST_HASH=$ManifestSha256
    # A nested native child does not reliably inherit the outer Job pipe via
    # PowerShell's native pipeline. Bind explicit OS pipes and discard stderr.
    function Quoted([string]$Value) {
        if (-not $Value -or $Value.Contains('"') -or $Value.EndsWith('\') -or $Value -match '[\x00-\x1f\x7f]') { throw 'argument' }
        return '"' + $Value + '"'
    }
    $psi=[Diagnostics.ProcessStartInfo]::new()
    $psi.FileName=$Python
    $psi.Arguments=('-I -S -B {0} --enable --config {1} --sha256 {2} --manifest-sha256 {3} --runtime {4} --runtime-sha256 {5}' -f `
        (Quoted $Bootstrap),(Quoted $Config),$ConfigSha256,$ManifestSha256,(Quoted $Runtime),$RuntimeSha256)
    if ($ExpectedHostSha256) { $psi.Arguments+=' --host-sha256 ' + $ExpectedHostSha256 }
    $psi.UseShellExecute=$false; $psi.CreateNoWindow=$true
    $psi.RedirectStandardOutput=$true; $psi.RedirectStandardError=$true
    $proc=[Diagnostics.Process]::new(); $proc.StartInfo=$psi
    try {
        $entered=$true
        if (-not $proc.Start()) { throw 'start' }
        $drain=$proc.StandardError.BaseStream.CopyToAsync([IO.Stream]::Null)
        $buffer=New-Object byte[] 1024; $total=0
        $output=[Console]::OpenStandardOutput()
        while (($n=$proc.StandardOutput.BaseStream.Read($buffer,0,$buffer.Length)) -gt 0) {
            $total+=$n; if ($total -gt 4096) { throw 'output' }
            $output.Write($buffer,0,$n)
        }
        $proc.WaitForExit()
        if (-not $drain.Wait(2000)) { throw 'stderr' }
        $exitCode=$proc.ExitCode
    } finally { $proc.Dispose() }
    exit $exitCode
} catch {
    if ($entered) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}
