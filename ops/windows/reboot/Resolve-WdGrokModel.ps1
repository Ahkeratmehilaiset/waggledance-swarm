#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$GrokCommand,
    [string]$OutputDirectory = 'C:\Python',
    [Nullable[DateTimeOffset]]$NowUtc,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$cacheMaxAge = [TimeSpan]::FromDays(7)
$jsonFileName = 'WD_GROK_MODEL_CURRENT.json'
$markdownFileName = 'WD_GROK_MODEL_CURRENT.md'

function Remove-AnsiEscape {
    param([AllowNull()][string]$Text)

    if ($null -eq $Text) {
        return ''
    }

    return [Regex]::Replace($Text, '\x1B(?:[@-_][0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))', '')
}

function Resolve-GrokExecutable {
    param([AllowEmptyString()][string]$RequestedCommand)

    $candidates = New-Object 'System.Collections.Generic.List[string]'
    if (-not [string]::IsNullOrWhiteSpace($RequestedCommand)) {
        $candidates.Add($RequestedCommand)
    }
    else {
        if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
            $candidates.Add((Join-Path $env:USERPROFILE '.grok\bin\grok.exe'))
        }
        $candidates.Add('grok.exe')
        $candidates.Add('grok')
    }

    $failures = New-Object 'System.Collections.Generic.List[string]'
    foreach ($candidate in $candidates) {
        try {
            $command = Get-Command -Name $candidate -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application -or
                    $_.CommandType -eq [Management.Automation.CommandTypes]::ExternalScript
                } |
                Select-Object -First 1

            if ($null -eq $command) {
                $failures.Add(('{0}: not an application or external script' -f $candidate))
                continue
            }

            $resolvedPath = $command.Path
            if ([string]::IsNullOrWhiteSpace($resolvedPath)) {
                $resolvedPath = $command.Source
            }
            if ([string]::IsNullOrWhiteSpace($resolvedPath)) {
                $failures.Add(('{0}: command path is empty' -f $candidate))
                continue
            }

            return [IO.Path]::GetFullPath($resolvedPath)
        }
        catch {
            $failures.Add(('{0}: {1}' -f $candidate, $_.Exception.Message))
        }
    }

    throw ('Could not resolve the Grok CLI to an exact executable path. Probes: {0}' -f
        ($failures -join '; '))
}

function Invoke-GrokProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$ProbeName
    )

    $LASTEXITCODE = 0
    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $rawOutput = @(& $Executable @ArgumentList 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $lines = @(
        $rawOutput |
            ForEach-Object { Remove-AnsiEscape -Text ([string]$_) }
    )

    if ($exitCode -ne 0) {
        $safeSummary = ($lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
                Select-Object -First 5) -join ' | '
        throw ('Grok CLI {0} probe failed with exit code {1}. Output: {2}' -f
            $ProbeName, $exitCode, $safeSummary)
    }

    return $lines
}

function ConvertFrom-GrokModelsOutput {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [AllowEmptyCollection()]
        [string[]]$Lines
    )

    $defaultMatches = New-Object 'System.Collections.Generic.List[string]'
    $availableModels = New-Object 'System.Collections.Generic.List[string]'
    $insideAvailableModels = $false

    foreach ($line in $Lines) {
        $defaultMatch = [Regex]::Match(
            $line,
            '^\s*Default model:\s*(?<model>[A-Za-z0-9][A-Za-z0-9._-]*)\s*$',
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if ($defaultMatch.Success) {
            $defaultMatches.Add($defaultMatch.Groups['model'].Value)
        }

        if ([Regex]::IsMatch(
                $line,
                '^\s*Available models:\s*$',
                [Text.RegularExpressions.RegexOptions]::CultureInvariant
            )) {
            $insideAvailableModels = $true
            continue
        }

        if (-not $insideAvailableModels) {
            continue
        }

        $availableMatch = [Regex]::Match(
            $line,
            '^\s*(?:\*|-)\s+(?<model>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\s+\([^)]*\))?\s*$',
            [Text.RegularExpressions.RegexOptions]::CultureInvariant
        )
        if ($availableMatch.Success) {
            $model = $availableMatch.Groups['model'].Value
            if (-not ($availableModels | Where-Object { $_ -ceq $model })) {
                $availableModels.Add($model)
            }
        }
    }

    if ($defaultMatches.Count -ne 1) {
        throw ('Expected exactly one "Default model:" entry from "grok models"; found {0}.' -f
            $defaultMatches.Count)
    }
    if ($availableModels.Count -eq 0) {
        throw 'The "grok models" response did not contain any parseable available models.'
    }

    $defaultModel = $defaultMatches[0]
    $defaultIsAvailable = @($availableModels | Where-Object { $_ -ceq $defaultModel }).Count -eq 1
    if (-not $defaultIsAvailable) {
        throw ('Provider default model "{0}" is not present in the available-model list.' -f
            $defaultModel)
    }

    return [pscustomobject][ordered]@{
        Model = $defaultModel
        AvailableModels = @($availableModels)
    }
}

function ConvertTo-PowerShellSingleQuotedLiteral {
    param([Parameter(Mandatory = $true)][string]$Value)

    return "'{0}'" -f $Value.Replace("'", "''")
}

function New-UsageExamples {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$Model
    )

    $quotedExecutable = ConvertTo-PowerShellSingleQuotedLiteral -Value $Executable
    $quotedModel = ConvertTo-PowerShellSingleQuotedLiteral -Value $Model
    $quotedProject = ConvertTo-PowerShellSingleQuotedLiteral -Value 'C:\Python\project2'

    return [pscustomobject][ordered]@{
        single_turn = ('& {0} -p ''<prompt>'' --always-approve --no-alt-screen --cwd {1} --model {2} --effort high' -f
            $quotedExecutable, $quotedProject, $quotedModel)
        interactive = ('& {0} --no-alt-screen --cwd {1} --model {2} --effort high' -f
            $quotedExecutable, $quotedProject, $quotedModel)
    }
}

function Get-CacheRecord {
    param(
        [Parameter(Mandatory = $true)][string]$JsonPath,
        [Parameter(Mandatory = $true)][DateTimeOffset]$EffectiveNow
    )

    if (-not (Test-Path -LiteralPath $JsonPath -PathType Leaf)) {
        throw ('No Grok model cache exists at {0}.' -f $JsonPath)
    }

    try {
        $cache = Get-Content -LiteralPath $JsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw ('Grok model cache is not valid JSON: {0}' -f $_.Exception.Message)
    }

    foreach ($requiredProperty in @(
            'model',
            'available_models',
            'cli_version',
            'grok_command',
            'discovered_utc',
            'source',
            'selection_method',
            'usage'
        )) {
        if ($null -eq $cache.PSObject.Properties[$requiredProperty]) {
            throw ('Grok model cache is missing required property "{0}".' -f $requiredProperty)
        }
    }

    $model = [string]$cache.model
    if ($model -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
        throw 'Grok model cache contains an invalid model identifier.'
    }

    $availableModels = @($cache.available_models | ForEach-Object { [string]$_ })
    if (@($availableModels | Where-Object { $_ -ceq $model }).Count -ne 1) {
        throw 'Cached provider default is not present exactly once in cached available models.'
    }

    $discoveredUtc = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            [string]$cache.discovered_utc,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind,
            [ref]$discoveredUtc
        )) {
        throw 'Grok model cache has an invalid discovered_utc timestamp.'
    }
    $discoveredUtc = $discoveredUtc.ToUniversalTime()
    $age = $EffectiveNow - $discoveredUtc
    if ($age -lt [TimeSpan]::FromMinutes(-5)) {
        throw ('Grok model cache timestamp is in the future ({0:o}).' -f $discoveredUtc)
    }
    if ($age -gt $cacheMaxAge) {
        throw ('Grok model cache is stale: age {0:N2} days exceeds the 7-day limit.' -f
            $age.TotalDays)
    }

    if ($null -eq $cache.usage.PSObject.Properties['single_turn'] -or
        $null -eq $cache.usage.PSObject.Properties['interactive']) {
        throw 'Grok model cache is missing exact usage examples.'
    }

    return [pscustomobject][ordered]@{
        Cache = $cache
        Age = $age
        DiscoveredUtc = $discoveredUtc
    }
}

function Write-TextAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Content
    )

    $directory = Split-Path -Parent $Path
    $leaf = Split-Path -Leaf $Path
    $temporaryPath = Join-Path $directory ('.{0}.tmp.{1}.{2}' -f
        $leaf, $PID, [Guid]::NewGuid().ToString('N'))
    $utf8WithoutBom = New-Object Text.UTF8Encoding($false)

    try {
        [IO.File]::WriteAllText($temporaryPath, $Content, $utf8WithoutBom)
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            # Bare $null is coerced to an empty string for this overload.
            [IO.File]::Replace(
                $temporaryPath,
                $Path,
                [System.Management.Automation.Language.NullString]::Value,
                $true)
        }
        else {
            [IO.File]::Move($temporaryPath, $Path)
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath -PathType Leaf) {
            Remove-Item -LiteralPath $temporaryPath -Force
        }
    }
}

function New-MarkdownDocument {
    param([Parameter(Mandatory = $true)]$Record)

    $available = @($Record.available_models | ForEach-Object { '- `{0}`' -f [string]$_ })
    $lines = @(
        '# Current verified Grok model',
        '',
        'This file is generated by `Resolve-WdGrokModel.ps1` from the authenticated Grok CLI provider default.',
        'The provider default is used as the authoritative accessible choice; no model name is hard-coded or guessed from version-like names.',
        '',
        ('- Model: `{0}`' -f [string]$Record.model),
        ('- Grok CLI: `{0}`' -f [string]$Record.cli_version),
        ('- Executable: `{0}`' -f [string]$Record.grok_command),
        ('- Verified (UTC): `{0}`' -f [string]$Record.discovered_utc),
        ('- Source: `{0}`' -f [string]$Record.source),
        ('- Selection: `{0}`' -f [string]$Record.selection_method),
        '',
        '## Available models reported by the provider',
        ''
    ) + $available + @(
        '',
        '## Exact usage',
        '',
        'Single-turn:',
        '',
        '```powershell',
        [string]$Record.usage.single_turn,
        '```',
        '',
        'Interactive:',
        '',
        '```powershell',
        [string]$Record.usage.interactive,
        '```',
        ''
    )

    return ($lines -join [Environment]::NewLine)
}

$effectiveNow = if ($null -ne $NowUtc) {
    ([DateTimeOffset]$NowUtc).ToUniversalTime()
}
else {
    [DateTimeOffset]::UtcNow
}
$jsonPath = Join-Path $OutputDirectory $jsonFileName
$markdownPath = Join-Path $OutputDirectory $markdownFileName
$liveDiscoveryComplete = $false

try {
    $resolvedExecutable = Resolve-GrokExecutable -RequestedCommand $GrokCommand
    $versionLines = @(Invoke-GrokProbe `
            -Executable $resolvedExecutable `
            -ArgumentList @('--version') `
            -ProbeName 'version')
    $cliVersion = @($versionLines | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        } | Select-Object -First 1)
    if ($cliVersion.Count -ne 1) {
        throw 'The Grok CLI version probe returned no version line.'
    }

    $modelsLines = @(Invoke-GrokProbe `
            -Executable $resolvedExecutable `
            -ArgumentList @('models') `
            -ProbeName 'models')
    $modelSelection = ConvertFrom-GrokModelsOutput -Lines $modelsLines
    $usage = New-UsageExamples `
        -Executable $resolvedExecutable `
        -Model $modelSelection.Model

    $record = [pscustomobject][ordered]@{
        schema_version = 1
        model = $modelSelection.Model
        available_models = @($modelSelection.AvailableModels)
        cli_version = [string]$cliVersion[0]
        grok_command = $resolvedExecutable
        discovered_utc = $effectiveNow.ToString('o')
        source = 'Authenticated Grok CLI: grok models'
        selection_method = 'Authenticated CLI provider default; authoritative accessible choice, verified present exactly once in Available models; no local version-name ranking'
        cache_max_age_days = [int]$cacheMaxAge.TotalDays
        usage = $usage
    }
    $liveDiscoveryComplete = $true

    if (-not $DryRun) {
        if (-not (Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
            $null = New-Item -ItemType Directory -Path $OutputDirectory -Force
        }

        $markdown = New-MarkdownDocument -Record $record
        $json = $record | ConvertTo-Json -Depth 8

        # Each public file is replaced atomically in place. JSON is written last
        # and is the canonical cache/commit marker for this generation.
        Write-TextAtomic -Path $markdownPath -Content $markdown
        Write-TextAtomic -Path $jsonPath -Content ($json + [Environment]::NewLine)
    }

    [pscustomobject][ordered]@{
        Status = if ($DryRun) { 'verified_dry_run' } else { 'verified_persisted' }
        Model = $record.model
        AvailableModels = @($record.available_models)
        CliVersion = $record.cli_version
        GrokCommand = $record.grok_command
        DiscoveredUtc = $effectiveNow
        Source = $record.source
        SelectionMethod = $record.selection_method
        Usage = $record.usage
        JsonPath = $jsonPath
        MarkdownPath = $markdownPath
        Persisted = -not $DryRun
        CacheAge = [TimeSpan]::Zero
        Warning = $null
    }
}
catch {
    if ($liveDiscoveryComplete) {
        throw
    }

    $liveFailure = $_.Exception.Message
    try {
        $cached = Get-CacheRecord -JsonPath $jsonPath -EffectiveNow $effectiveNow
    }
    catch {
        throw ((
                'Live Grok model discovery failed ({0}) and no valid cache is available ({1}). ' +
                'Refusing to guess or use a hard-coded model.'
            ) -f $liveFailure, $_.Exception.Message)
    }

    $warning = ((
            'LIVE GROK MODEL DISCOVERY FAILED: {0} Using verified cache from {1:o} ' +
            '({2:N2} days old; maximum 7 days).'
        ) -f $liveFailure, $cached.DiscoveredUtc, $cached.Age.TotalDays)
    Write-Warning $warning

    [pscustomobject][ordered]@{
        Status = 'verified_cache_fallback'
        Model = [string]$cached.Cache.model
        AvailableModels = @($cached.Cache.available_models | ForEach-Object { [string]$_ })
        CliVersion = [string]$cached.Cache.cli_version
        GrokCommand = [string]$cached.Cache.grok_command
        DiscoveredUtc = $cached.DiscoveredUtc
        Source = [string]$cached.Cache.source
        SelectionMethod = [string]$cached.Cache.selection_method
        Usage = $cached.Cache.usage
        JsonPath = $jsonPath
        MarkdownPath = $markdownPath
        Persisted = $false
        CacheAge = $cached.Age
        Warning = $warning
    }
}
