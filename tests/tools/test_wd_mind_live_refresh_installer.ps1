$installer = Join-Path $PSScriptRoot '..\..\ops\windows\reboot\Install-WdMindLiveRefresh.ps1'
Describe 'Mind live refresh installer' {
    BeforeEach {
        $root = Join-Path $TestDrive ("synthetic's space " + [guid]::NewGuid().ToString('N'))
        $bundle = Join-Path $root 'bundle'
        $private = Join-Path $root 'private'
        $moduleDir = Join-Path $bundle 'tools-bootstrap\tools'
        New-Item -ItemType Directory -Path $moduleDir, $private -Force | Out-Null
        $wrapper = Join-Path $bundle 'Invoke-WdBridgePython.ps1'
        $module = Join-Path $moduleDir 'wd_q4_live_update_once.py'
        $wrapperText = @'
param([string]$Tool, [switch]$VerifyPackage, [string[]]$ToolArguments)
if ($Tool -cne 'tools/wd_q4_live_update_once.py' -or -not $VerifyPackage -or $ToolArguments[0] -cne '--once') { throw 'bad invocation' }
[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'synthetic-invoked.txt'), 'called')
$global:LASTEXITCODE = 0
'@
        [IO.File]::WriteAllText($wrapper, $wrapperText)
        [IO.File]::WriteAllText($module, '# synthetic module')
        $commit = 'a' * 40
        $files = @{'Invoke-WdBridgePython.ps1' = (Get-FileHash $wrapper -Algorithm SHA256).Hash.ToLowerInvariant();
            'tools-bootstrap/tools/wd_q4_live_update_once.py' = (Get-FileHash $module -Algorithm SHA256).Hash.ToLowerInvariant()}
        $manifestPath = Join-Path $bundle 'deployment-manifest.json'
        [IO.File]::WriteAllText($manifestPath, (@{schema_version=1; source_commit=$commit; files=$files} | ConvertTo-Json -Compress))
        $manifestHash = (Get-FileHash $manifestPath -Algorithm SHA256).Hash
        $statePath = Join-Path $root 'state.json'
        [IO.File]::WriteAllText($statePath, (@{source_commit=$commit; final_commit=$commit; active_bundle=$bundle;
            manifest_sha256=$manifestHash; final_manifest_sha256=$manifestHash} | ConvertTo-Json -Compress))
        $routePath = Join-Path $root 'route.json'
        [IO.File]::WriteAllText($routePath, (@{private_state_root=$private} | ConvertTo-Json -Compress))
        $routeHash = (Get-FileHash $routePath -Algorithm SHA256).Hash.ToLowerInvariant()
        $configPath = Join-Path $root 'config.json'
        [IO.File]::WriteAllText($configPath, (@{schema='wd.q4-live-update-once.v1'; route_path=$routePath;
            route_sha256=$routeHash; manifest_sha256=$manifestHash.ToLowerInvariant()} | ConvertTo-Json -Compress))
        $configHash = (Get-FileHash $configPath -Algorithm SHA256).Hash
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $invokeParams = @{BundleRoot=$bundle; StatePath=$statePath; ConfigPath=$configPath; ExpectedCommit=$commit;
            ExpectedManifestSha256=$manifestHash; ExpectedConfigSha256=$configHash; ApprovedUserSid=$sid}
    }
    It 'plans without registering or enabling any task' {
        Mock Register-ScheduledTask { throw 'must not register' }
        $plan = & $installer @invokeParams | ConvertFrom-Json
        $plan.registered | Should Be $false
        $plan.enabled | Should Be $false
        $plan.multiple_instances | Should Be 'IgnoreNew'
        Assert-MockCalled Register-ScheduledTask -Times 0 -Exactly -Scope It
    }
    It 'refuses a modified config before registration' {
        [IO.File]::AppendAllText($configPath, ' ')
        $threw = $false
        try { & $installer @invokeParams | Out-Null } catch { $threw = $true }
        $threw | Should Be $true
    }
    It 'refuses an existing uncertain attempt before registration' {
        [IO.File]::WriteAllText((Join-Path $private 'q4-live-update.pending.json'), '{}')
        $threw = $false
        try { & $installer @invokeParams | Out-Null } catch { $threw = $true }
        $threw | Should Be $true
    }
    It 'registers only a disabled limited one-instance task when explicitly applied' {
        Mock Get-ScheduledTask { if ($TaskName -eq 'WD-MindLiveUpdate') { $global:wdSyntheticRegisteredTask } else { @() } }
        Mock Register-ScheduledTask {
            $global:wdSyntheticTaskAction = $Action
            $global:wdSyntheticRegisteredTask = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal
            [pscustomobject]@{ Stub = 'registered' }
        }
        Mock Start-ScheduledTask { throw 'must not start' }
        Mock Enable-ScheduledTask { throw 'must not enable' }
        $plan = & $installer @invokeParams -Apply | ConvertFrom-Json
        $plan.registered | Should Be $true
        $plan.enabled | Should Be $false
        Assert-MockCalled Register-ScheduledTask -Times 1 -Exactly -Scope It -ParameterFilter {
            -not $Settings.Enabled -and $Settings.MultipleInstances -eq 'IgnoreNew' -and
            $Principal.LogonType -eq 'Interactive' -and $Principal.RunLevel -eq 'Limited' }
        Assert-MockCalled Start-ScheduledTask -Times 0 -Exactly -Scope It
        Assert-MockCalled Enable-ScheduledTask -Times 0 -Exactly -Scope It
        $encoded = [string]$global:wdSyntheticTaskAction.Arguments -replace '^.*-EncodedCommand ', ''
        $powershell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 0
        Test-Path -LiteralPath (Join-Path $bundle 'synthetic-invoked.txt') | Should Be $true
        [IO.File]::Delete((Join-Path $bundle 'synthetic-invoked.txt'))
        [IO.File]::WriteAllText((Join-Path $private 'q4-live-update.pending.json'), '{}')
        & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 3
        Test-Path -LiteralPath (Join-Path $bundle 'synthetic-invoked.txt') | Should Be $false
        [IO.File]::Delete((Join-Path $private 'q4-live-update.pending.json'))
        [IO.File]::AppendAllText($configPath, ' ')
        & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 3
        Test-Path -LiteralPath (Join-Path $bundle 'synthetic-invoked.txt') | Should Be $false
    }
    It 'does not replace an existing scheduled task' {
        Mock Get-ScheduledTask { [pscustomobject]@{TaskName='WD-MindLiveUpdate'} }
        Mock Register-ScheduledTask { throw 'must not register' }
        $threw = $false
        try { & $installer @invokeParams -Apply | Out-Null } catch { $threw = $true }
        $threw | Should Be $true
        Assert-MockCalled Register-ScheduledTask -Times 0 -Exactly -Scope It
    }
}
