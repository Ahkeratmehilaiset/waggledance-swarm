#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-012): import a Codex Scout
    findings.json file from a separate `git worktree` into the
    main project's iteration tree. The orchestrator does NOT
    install or run Codex; it only validates + redacts + stores.

    Refuses on:
      - schema-invalid file
      - scope.epoch_id mismatch with the -EpochId argument
      - missing required fields
      - completed != true

    Apply Phase 2A-1 redactor to the entire JSON text BEFORE
    parsing to keep contemporary credentials out of disk storage
    even if the scout's evidence text accidentally captured one.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $EpochId = '',
    [string] $IterationId = '',
    [string] $FindingsFile = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/Redactor.ps1')
. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')

$Script:CodexAllowedTools = @('codex_cli','codex_cloud','codex_app','other')
$Script:CodexAllowedCategories = @('bug','security','reliability','test_gap','architecture','performance','other')
$Script:CodexAllowedSeverities = @('critical','high','medium','low','info')
$Script:CodexAllowedEfforts = @('small','medium','large')

function _Cdx-NowUtc { return (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH-mm-ssZ') }

function _Cdx-Has { param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $false }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        return ($null -ne $p)
    }
    if ($Obj -is [System.Collections.IDictionary]) { return $Obj.Contains($Name) }
    return $false
}
function _Cdx-Get { param($Obj, [string] $Name)
    if ($null -eq $Obj) { return $null }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        if ($p) { return $p.Value }
        return $null
    }
    if ($Obj -is [System.Collections.IDictionary]) { if ($Obj.Contains($Name)) { return $Obj[$Name] }; return $null }
    return $null
}
function _Cdx-NonEmpty { param($v); return ($null -ne $v -and $v -is [string] -and $v.Length -gt 0) }

function Test-CodexFindingsObject {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowNull()] $Object)
    $errors = New-Object System.Collections.Generic.List[string]
    if ($null -eq $Object) {
        $errors.Add('codex findings object is null') | Out-Null
        return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() }
    }
    foreach ($k in 'format_version','scout_self_id','scope','findings','proposals','completed') {
        if (-not (_Cdx-Has -Obj $Object -Name $k)) {
            $errors.Add("missing top-level field: $k") | Out-Null
        }
    }
    if ($errors.Count -gt 0) { return [pscustomobject]@{ ok = $false; errors = $errors.ToArray() } }

    if ([string](_Cdx-Get -Obj $Object -Name 'format_version') -ne '1.0') {
        $errors.Add("format_version must be '1.0'") | Out-Null
    }
    $self = _Cdx-Get -Obj $Object -Name 'scout_self_id'
    if ($null -eq $self) {
        $errors.Add('scout_self_id is null') | Out-Null
    } else {
        $tool = [string](_Cdx-Get -Obj $self -Name 'tool')
        if ($Script:CodexAllowedTools -notcontains $tool) {
            $errors.Add("scout_self_id.tool must be one of: $($Script:CodexAllowedTools -join ',')") | Out-Null
        }
        if (-not (_Cdx-NonEmpty -v (_Cdx-Get -Obj $self -Name 'ran_at_utc'))) {
            $errors.Add('scout_self_id.ran_at_utc must be a non-empty string') | Out-Null
        }
    }
    $scope = _Cdx-Get -Obj $Object -Name 'scope'
    if ($null -eq $scope) {
        $errors.Add('scope is null') | Out-Null
    } else {
        if (-not (_Cdx-NonEmpty -v (_Cdx-Get -Obj $scope -Name 'epoch_id'))) {
            $errors.Add('scope.epoch_id must be a non-empty string') | Out-Null
        }
        $tids = _Cdx-Get -Obj $scope -Name 'target_iteration_ids'
        if ($null -eq $tids -or @($tids).Count -eq 0) {
            $errors.Add('scope.target_iteration_ids must be a non-empty array') | Out-Null
        }
    }
    $findings = _Cdx-Get -Obj $Object -Name 'findings'
    if ($null -ne $findings -and -not ($findings -is [string])) {
        $idx = 0
        foreach ($f in @($findings)) {
            $fp = "findings[$idx]"
            foreach ($req in 'id','severity','category','title','where','evidence','why_it_matters','recommended_action') {
                if (-not (_Cdx-Has -Obj $f -Name $req)) {
                    $errors.Add("$fp missing field: $req") | Out-Null
                }
            }
            $sev = _Cdx-Get -Obj $f -Name 'severity'
            if ($null -ne $sev -and $Script:CodexAllowedSeverities -notcontains $sev) {
                $errors.Add("$fp.severity must be one of $($Script:CodexAllowedSeverities -join ',')") | Out-Null
            }
            $cat = _Cdx-Get -Obj $f -Name 'category'
            if ($null -ne $cat -and $Script:CodexAllowedCategories -notcontains $cat) {
                $errors.Add("$fp.category must be one of $($Script:CodexAllowedCategories -join ',')") | Out-Null
            }
            $findId = _Cdx-Get -Obj $f -Name 'id'
            if ($null -ne $findId -and $findId -notmatch '^CDEX-\d{3,}$') {
                $errors.Add("$fp.id must match ^CDEX-\d{3,}$") | Out-Null
            }
            $idx++
        }
    }
    $props = _Cdx-Get -Obj $Object -Name 'proposals'
    if ($null -ne $props -and -not ($props -is [string])) {
        $idx = 0
        foreach ($p in @($props)) {
            $pp = "proposals[$idx]"
            foreach ($req in 'id','title','rationale','approach','estimated_effort','risks','expected_payoff') {
                if (-not (_Cdx-Has -Obj $p -Name $req)) {
                    $errors.Add("$pp missing field: $req") | Out-Null
                }
            }
            $eff = _Cdx-Get -Obj $p -Name 'estimated_effort'
            if ($null -ne $eff -and $Script:CodexAllowedEfforts -notcontains $eff) {
                $errors.Add("$pp.estimated_effort must be one of $($Script:CodexAllowedEfforts -join ',')") | Out-Null
            }
            $propId = _Cdx-Get -Obj $p -Name 'id'
            if ($null -ne $propId -and $propId -notmatch '^CDEX-PROP-\d{3,}$') {
                $errors.Add("$pp.id must match ^CDEX-PROP-\d{3,}$") | Out-Null
            }
            $idx++
        }
    }
    $completed = _Cdx-Get -Obj $Object -Name 'completed'
    if (-not ($completed -is [bool]) -or -not $completed) {
        $errors.Add('completed must be the boolean true') | Out-Null
    }
    return [pscustomobject]@{ ok = ($errors.Count -eq 0); errors = $errors.ToArray() }
}

function Import-WaggleCodexFindings {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $IterationId,
        [Parameter(Mandatory)] [string] $FindingsFile
    )
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    if (-not (Test-Path -LiteralPath $FindingsFile)) { throw "findings file not found: $FindingsFile" }

    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $projectRoot = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterFolder = Join-Path (Join-Path $projectRoot $iterationsDir) $IterationId
    if (-not (Test-Path -LiteralPath $iterFolder)) {
        throw "iteration folder missing: $iterFolder"
    }
    $codexDir = Join-Path $iterFolder 'codex'
    if (-not (Test-Path -LiteralPath $codexDir)) {
        New-Item -ItemType Directory -Path $codexDir -Force | Out-Null
    }

    $importId = (_Cdx-NowUtc) + '_codex'

    # 1. Read and apply Phase 2A-1 redactor up-front.
    $rawText = Get-Content -Raw -Path $FindingsFile -Encoding UTF8
    if ($null -eq $rawText) { $rawText = '' }
    $red = Invoke-WaggleRedaction -Text $rawText
    $redactedText = $red.text
    $redactionReport = [pscustomobject]@{
        import_id = $importId; epoch_id = $EpochId; target_iteration_id = $IterationId
        findings_file = $FindingsFile
        report = $red.report
        applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $redactionReportPath = Join-Path $codexDir ($importId + '.redaction_report.json')

    # 2. Parse JSON (may fail if redactor mangled syntax — synthetic
    # credentials in source-context-aware fields shouldn't, but be safe).
    function _writeInvalid {
        param([string] $reason, $extras = @())
        $invalidJson = Join-Path $codexDir ($importId + '.invalid.txt')
        $invalidMeta = Join-Path $codexDir ($importId + '.invalid.metadata.json')
        Set-Content -Path $invalidJson -Value $redactedText -Encoding UTF8
        $meta = [ordered]@{
            import_id = $importId; ok = $false; reason = $reason; extras = $extras
            epoch_id = $EpochId; target_iteration_id = $IterationId
            findings_file = $FindingsFile
            redaction_report = $redactionReport
            applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
        }
        Set-Content -Path $invalidMeta -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 16) -Encoding UTF8
        Set-Content -Path $redactionReportPath -Value (([pscustomobject]$redactionReport) | ConvertTo-Json -Depth 16) -Encoding UTF8
    }

    $obj = $null
    try {
        $obj = $redactedText | ConvertFrom-Json -ErrorAction Stop
    } catch {
        _writeInvalid -reason 'json_parse_failed' -extras @($_.Exception.Message)
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'json_parse_failed'; errors = @($_.Exception.Message) }
    }
    $schema = Test-CodexFindingsObject -Object $obj
    if (-not $schema.ok) {
        _writeInvalid -reason 'schema_invalid' -extras $schema.errors
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'schema_invalid'; errors = $schema.errors }
    }

    # 3. epoch_id cross-check
    $scopeEpoch = [string]$obj.scope.epoch_id
    if ($scopeEpoch -ne $EpochId) {
        _writeInvalid -reason 'epoch_id_mismatch' -extras @("scope.epoch_id=$scopeEpoch", "expected=$EpochId")
        return [pscustomobject]@{ ok = $false; import_id = $importId; reason = 'epoch_id_mismatch'; errors = @("scope.epoch_id=$scopeEpoch expected=$EpochId") }
    }

    # 4. Write valid records.
    $jsonOut = Join-Path $codexDir ($importId + '_findings.json')
    $mdOut   = Join-Path $codexDir ($importId + '_findings.md')
    $metaOut = Join-Path $codexDir ($importId + '_findings.metadata.json')

    Set-Content -Path $jsonOut -Value (([pscustomobject]$obj) | ConvertTo-Json -Depth 16) -Encoding UTF8

    # Render markdown grouped by category, severity descending.
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Codex Scout findings')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Tool: ' + [string]$obj.scout_self_id.tool)
    [void]$sb.AppendLine('Ran at: ' + [string]$obj.scout_self_id.ran_at_utc)
    [void]$sb.AppendLine('Scope epoch: ' + $scopeEpoch)
    [void]$sb.AppendLine('Iterations: ' + (((@($obj.scope.target_iteration_ids))) -join ', '))
    [void]$sb.AppendLine('')

    $sevOrder = @{ 'critical' = 0; 'high' = 1; 'medium' = 2; 'low' = 3; 'info' = 4 }
    $byCat = @($obj.findings) | Group-Object category
    foreach ($g in ($byCat | Sort-Object Name)) {
        [void]$sb.AppendLine('## findings — ' + $g.Name + '  (' + $g.Count + ')')
        [void]$sb.AppendLine('')
        foreach ($f in @($g.Group | Sort-Object @{Expression={ $sevOrder[[string]$_.severity] }})) {
            [void]$sb.AppendLine('- **' + [string]$f.id + ' [' + [string]$f.severity + '] ' + [string]$f.title + '**  ')
            [void]$sb.AppendLine('  - where: ' + [string]$f.where)
            [void]$sb.AppendLine('  - why: ' + [string]$f.why_it_matters)
            [void]$sb.AppendLine('  - action: ' + [string]$f.recommended_action)
        }
        [void]$sb.AppendLine('')
    }
    if (@($obj.proposals).Count -gt 0) {
        [void]$sb.AppendLine('## proposals (' + @($obj.proposals).Count + ')')
        [void]$sb.AppendLine('')
        foreach ($p in @($obj.proposals)) {
            [void]$sb.AppendLine('- **' + [string]$p.id + ' [' + [string]$p.estimated_effort + '] ' + [string]$p.title + '**  ')
            [void]$sb.AppendLine('  - rationale: ' + [string]$p.rationale)
            [void]$sb.AppendLine('  - approach: ' + [string]$p.approach)
            [void]$sb.AppendLine('  - payoff: ' + [string]$p.expected_payoff)
            [void]$sb.AppendLine('  - risks: ' + [string]$p.risks)
        }
    }
    Set-Content -Path $mdOut -Value $sb.ToString() -Encoding UTF8

    $meta = [ordered]@{
        import_id = $importId
        ok = $true
        epoch_id = $EpochId
        target_iteration_id = $IterationId
        findings_file = $FindingsFile
        json_path = $jsonOut
        md_path = $mdOut
        redaction_report_path = $redactionReportPath
        finding_count = @($obj.findings).Count
        proposal_count = @($obj.proposals).Count
        applied_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    Set-Content -Path $metaOut -Value (([pscustomobject]$meta) | ConvertTo-Json -Depth 16) -Encoding UTF8
    Set-Content -Path $redactionReportPath -Value (([pscustomobject]$redactionReport) | ConvertTo-Json -Depth 16) -Encoding UTF8

    # Also drop a stable findings.json symlink-ish copy for the
    # proposal-matrix builder default lookup. (Copy, not symlink —
    # Windows symlinks need elevation.)
    $stableJson = Join-Path $codexDir 'findings.json'
    Copy-Item -LiteralPath $jsonOut -Destination $stableJson -Force

    return [pscustomobject]@{
        ok = $true
        import_id = $importId
        json_path = $jsonOut
        md_path = $mdOut
        metadata_path = $metaOut
        finding_count = @($obj.findings).Count
        proposal_count = @($obj.proposals).Count
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $EpochId -and $IterationId -and $FindingsFile) {
    $r = Import-WaggleCodexFindings -ConfigPath $ConfigPath -EpochId $EpochId -IterationId $IterationId -FindingsFile $FindingsFile
    if ($r.ok) {
        Write-Host ('Codex findings imported: ' + $r.import_id)
        Write-Host ('  findings  : ' + $r.finding_count)
        Write-Host ('  proposals : ' + $r.proposal_count)
        Write-Host ('  json      : ' + $r.json_path)
        exit 0
    } else {
        Write-Host ('Codex import failed: ' + $r.reason) -ForegroundColor Red
        foreach ($e in $r.errors) { Write-Host ('  ' + $e) -ForegroundColor Red }
        exit 1
    }
}
