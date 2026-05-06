#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-1 P4: parse-time syntax preflight for every .ps1 / .psm1 file
    under orchestrator\. Uses the language Parser API; does NOT execute any
    file. Catches the kind of "wrote a script with a stray here-string or
    backtick and only found out at run-time" surprise the Phase 1.6 lessons
    called out.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = $PSScriptRoot
$files = @(Get-ChildItem -Path $root -Recurse -File -Include '*.ps1','*.psm1')
Write-Host ("Scanning {0} PowerShell files under {1}" -f $files.Count, $root) -ForegroundColor Cyan

$failures = @()
foreach ($f in $files) {
    $tokens = $null
    $errors = $null
    try {
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            $f.FullName, [ref]$tokens, [ref]$errors)
    } catch {
        Write-Host ("FAIL  {0}  parser threw: {1}" -f $f.FullName, $_.Exception.Message) -ForegroundColor Red
        $failures += $f.FullName
        continue
    }
    if ($null -ne $errors -and @($errors).Count -gt 0) {
        Write-Host ("FAIL  {0}  ({1} parse errors)" -f $f.FullName, @($errors).Count) -ForegroundColor Red
        foreach ($e in @($errors)) {
            $line = if ($e.Extent) { $e.Extent.StartLineNumber } else { '?' }
            $col  = if ($e.Extent) { $e.Extent.StartColumnNumber } else { '?' }
            Write-Host ("       line {0}, col {1}: {2}" -f $line, $col, $e.Message) -ForegroundColor Red
        }
        $failures += $f.FullName
    } else {
        Write-Host ("PASS  {0}" -f $f.FullName) -ForegroundColor Green
    }
}

Write-Host ""
Write-Host ("Result: {0}/{1} files parsed clean" -f ($files.Count - $failures.Count), $files.Count) -ForegroundColor Cyan
if ($failures.Count -gt 0) { exit 1 }
exit 0
