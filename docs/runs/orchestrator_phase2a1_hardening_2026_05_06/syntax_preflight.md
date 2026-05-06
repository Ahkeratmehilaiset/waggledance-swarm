# Phase 2A-1 — PowerShell 5.1 syntax preflight (P4)

## Tool

`orchestrator\Test-Syntax.ps1` (flat layout). It enumerates every `.ps1`
and `.psm1` file under `orchestrator\` (recursive) and parses each via:

```
[System.Management.Automation.Language.Parser]::ParseFile(
    $f.FullName, [ref]$tokens, [ref]$errors)
```

The preflight does NOT execute any file; it only inspects the AST.

## Run

```
powershell -NoProfile -ExecutionPolicy Bypass -File ".\orchestrator\Test-Syntax.ps1"
```

## Result

```
Scanning 30 PowerShell files under C:\Python\project2-master\orchestrator
PASS  ...\lib\ArtifactValidator.ps1
PASS  ...\lib\Checkpoint.ps1
PASS  ...\lib\ClaudeRunner.ps1
PASS  ...\lib\Collector.ps1
PASS  ...\lib\CompletionVerifier.ps1
PASS  ...\lib\ConfigValidator.ps1
PASS  ...\lib\Detector.ps1
PASS  ...\lib\EnvSanitize.ps1
PASS  ...\lib\Lockfile.ps1
PASS  ...\lib\Packager.ps1
PASS  ...\lib\PathValidation.ps1
PASS  ...\lib\Preflight.ps1
PASS  ...\lib\Redactor.ps1
PASS  ...\lib\Signals.ps1
PASS  ...\lib\State.ps1
PASS  ...\tests\fake-claude.ps1
PASS  ...\Invoke-WaggleIteration.ps1
PASS  ...\Start-WaggleSession.ps1
PASS  ...\Test-ArtifactValidator.ps1
PASS  ...\Test-ClaudeRunner.ps1
PASS  ...\Test-ConfigValidator.ps1
PASS  ...\Test-Detector.ps1
PASS  ...\Test-Integration.ps1
PASS  ...\Test-Lockfile.ps1
PASS  ...\Test-PathValidation.ps1
PASS  ...\Test-Redaction.ps1
PASS  ...\Test-Redactor.ps1
PASS  ...\Test-SmokeValidation.ps1
PASS  ...\Test-Syntax.ps1
PASS  ...\Watch-ClaudeCode.ps1

Result: 30/30 files parsed clean
```

All 30 files parse cleanly under Windows PowerShell 5.1.

## Parser caveats

- `ParseFile` runs the v5.1 parser; behaviour matches what
  `powershell.exe` would see at script load time. It does NOT run any
  cmdlets, so a syntactically valid but semantically broken construct
  (e.g. calling a non-existent function) cannot be caught here. That
  remains the job of Test-Integration / unit tests / smoke runs.
- `Set-StrictMode -Version Latest` errors do not surface at parse time;
  they fire at runtime. Phase 2A-1 deliberately did NOT introduce new
  StrictMode Latest patterns into the new test files (per the Phase 1.6
  lesson "avoid fragile StrictMode Latest patterns in new scripts");
  preserving the existing scripts' StrictMode is fine.
- `[ref]$errors` returns either `$null` or a `ParseError[]`. The new
  script handles both via `@($errors)`; the original ParseFile API in
  PS 5.1 is otherwise stable.
- The preflight DOES include subdirectories (`-Recurse`), so
  `orchestrator\lib\*` and `orchestrator\tests\*` are scanned.

## Why this matters

The Phase 1.6 lessons explicitly called out:
- "Do not rely on Claude Code Write tool for large files."
- "After writing files, verify byte count and parse/syntax where possible."
- "Layered verification is preferred over one brittle must-pass marker."

A parse-only preflight is the cheapest layer: it catches a stray
backtick, an unterminated here-string, or a malformed `param()` block
before any iteration starts touching state files or signals.
Recommended to run as the first gate of any future hardening session.
