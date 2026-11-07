$installer = Join-Path $PSScriptRoot '..\..\ops\windows\reboot\Install-WdMindLiveRefresh.ps1'
function Resolve-SyntheticTestPython([string[]] $Candidates) {
    foreach ($candidate in $Candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
            if ($item.PSIsContainer -or $item.Length -le 0 -or
                ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            [void](Get-FileHash -LiteralPath $candidate -Algorithm SHA256 -ErrorAction Stop)
            return $item.FullName
        } catch { continue }
    }
    throw 'No readable non-alias Python executable is available for the synthetic test.'
}
Describe 'Mind live refresh installer' {
    BeforeEach {
        $root = Join-Path $TestDrive ("synthetic's space " + [guid]::NewGuid().ToString('N'))
        $bundle = Join-Path $root 'bundle'
        $private = Join-Path $root 'private'
        $scratch = Join-Path $root 'scratch'
        $checkpoint = Join-Path $root 'checkpoint'
        $source = Join-Path $root 'source'
        $model = Join-Path $root 'model'
        $pins = Join-Path $root 'pins'
        $moduleDir = Join-Path $bundle 'tools-bootstrap\tools'
        New-Item -ItemType Directory -Path $moduleDir, $private, $scratch, $checkpoint, $source, $model, $pins -Force | Out-Null
        $module = Join-Path $moduleDir 'wd_q4_live_update_once.py'
        $bootstrap = Join-Path $moduleDir 'wd_q4_live_update_bootstrap.py'
        $bootstrapText = @'
import hashlib, json, pathlib, sys
assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
assert len(sys.argv) == 9 and sys.argv[1] == '--config' and sys.argv[3] == '--sha256' and sys.argv[5] == '--manifest-sha256' and sys.argv[7] == '--host-attempt'
bundle = pathlib.Path(__file__).parents[2]
if bundle.joinpath('synthetic-crash').exists():
    attempt = sys.argv[8]
    if bundle.joinpath('synthetic-crash').read_text() == 'foreign': attempt = 'f' * 64
    config = json.loads(pathlib.Path(sys.argv[2]).read_bytes())
    route = json.loads(pathlib.Path(config['route_path']).read_bytes())
    private = pathlib.Path(route['private_state_root'])
    encode = lambda x: json.dumps(x, sort_keys=True, separators=(',', ':')).encode('ascii')
    intent = encode({'schema':'wd.q4-live-update-intent.v1','attempt':attempt,'config_sha256':sys.argv[4], 'previous_pointer_sha256':'a'*64, 'route':{}, 'plan':{}, 'anchor':{}})
    intent_sha = hashlib.sha256(intent).hexdigest()
    private.joinpath('q4-live-update-intent-' + attempt + '.json').write_bytes(intent)
    reservation = encode({'schema':'wd.q4-live-runner-reservation.v1','attempt':attempt,'token':'b'*64,'intent_sha256':intent_sha})
    private.joinpath('q4-live-runner-reservation-' + attempt + '.json').write_bytes(reservation)
    private.joinpath('q4-live-update.pending.json').write_bytes(encode({'schema':'wd.q4-live-update-pending.v1','attempt':attempt,'config_sha256':sys.argv[4],'previous_pointer_sha256':'a'*64,'intent_sha256':intent_sha}))
    sys.exit(3)
pathlib.Path(__file__).parents[2].joinpath('synthetic-invoked.txt').write_text('called')
print('{"status":"PUBLISHED","pointer_sha256":"' + 'a'*64 + '","receipt_sha256":"' + 'b'*64 + '","receipt_file":"q4-live-update-result-' + 'c'*64 + '.json"}')
'@
        [IO.File]::WriteAllText($bootstrap, $bootstrapText)
        [IO.File]::WriteAllText($module, '# synthetic module')
        $jobHelper = Join-Path $bundle 'WdQ4ProcessJob.ps1'
        [IO.File]::Copy((Join-Path $PSScriptRoot '..\..\ops\windows\reboot\WdQ4ProcessJob.ps1'), $jobHelper)
        $pythonCandidates = @($env:WD_TEST_Q4_PYTHON, $env:WD_BRIDGE_PYTHON)
        $pythonCandidates += @(Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object { $_.Source })
        $pythonHome = Join-Path $env:LOCALAPPDATA 'Programs\Python'
        if (Test-Path -LiteralPath $pythonHome -PathType Container) {
            $pythonCandidates += @(Get-ChildItem -LiteralPath $pythonHome -Directory | Sort-Object Name -Descending |
                ForEach-Object { Join-Path $_.FullName 'python.exe' })
        }
        $python = Resolve-SyntheticTestPython $pythonCandidates
        $pythonHash = (Get-FileHash $python -Algorithm SHA256).Hash.ToLowerInvariant()
        $runtime = Join-Path $root 'runtime'
        New-Item -ItemType Directory -Path $runtime | Out-Null
        $runtimeManifest = Join-Path $pins 'runtime-manifest.json'
        [IO.File]::WriteAllText($runtimeManifest, '{"synthetic":true}')
        $runtimeHash = (Get-FileHash $runtimeManifest -Algorithm SHA256).Hash.ToLowerInvariant()
        $fixedPath = Join-Path $pins 'fixed-plan.json'
        [IO.File]::WriteAllText($fixedPath, (@{runtime_root=$runtime; interpreter_sha256=$pythonHash;
            runtime_manifest_path=$runtimeManifest; runtime_manifest_sha256=$runtimeHash} | ConvertTo-Json -Compress))
        $baselinePath = Join-Path $pins 'baseline.json'
        [IO.File]::WriteAllText($baselinePath, (@{interpreter=$python; interpreter_sha256=$pythonHash;
            runtime_manifest_path=$runtimeManifest; runtime_manifest_sha256=$runtimeHash;
            fixed_plan_path=$fixedPath; fixed_plan_sha256=(Get-FileHash $fixedPath -Algorithm SHA256).Hash.ToLowerInvariant()} | ConvertTo-Json -Compress))
        $commit = 'a' * 40
        $files = @{'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py' = (Get-FileHash $bootstrap -Algorithm SHA256).Hash.ToLowerInvariant();
            'tools-bootstrap/tools/wd_q4_live_update_once.py' = (Get-FileHash $module -Algorithm SHA256).Hash.ToLowerInvariant();
            'WdQ4ProcessJob.ps1' = (Get-FileHash $jobHelper -Algorithm SHA256).Hash.ToLowerInvariant()}
        $manifestPath = Join-Path $bundle 'deployment-manifest.json'
        [IO.File]::WriteAllText($manifestPath, (@{schema_version=1; source_commit=$commit; files=$files} | ConvertTo-Json -Compress))
        $manifestHash = (Get-FileHash $manifestPath -Algorithm SHA256).Hash
        $statePath = Join-Path $pins 'state.json'
        [IO.File]::WriteAllText($statePath, (@{source_commit=$commit; final_commit=$commit; active_bundle=$bundle;
            manifest_sha256=$manifestHash; final_manifest_sha256=$manifestHash} | ConvertTo-Json -Compress))
        $routePath = Join-Path $pins 'route.json'
        $sourceFile = Join-Path $source 'events.jsonl'
        $sidecar = Join-Path $source 'generation.json'
        [IO.File]::WriteAllText($sourceFile, '{}')
        [IO.File]::WriteAllText($sidecar, '{}')
        [IO.File]::WriteAllText($routePath, (@{private_state_root=$private; checkpoint_root=$checkpoint;
            trusted_model_root=$model; source=$sourceFile; generation_path=$sidecar} | ConvertTo-Json -Compress))
        $routeHash = (Get-FileHash $routePath -Algorithm SHA256).Hash.ToLowerInvariant()
        $configPath = Join-Path $pins 'config.json'
        [IO.File]::WriteAllText($configPath, (@{schema='wd.q4-live-update-once.v1'; route_path=$routePath;
            route_sha256=$routeHash; manifest_sha256=$manifestHash.ToLowerInvariant(); baseline_path=$baselinePath;
            baseline_sha256=(Get-FileHash $baselinePath -Algorithm SHA256).Hash.ToLowerInvariant()} | ConvertTo-Json -Compress))
        $configHash = (Get-FileHash $configPath -Algorithm SHA256).Hash
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        $invokeParams = @{BundleRoot=$bundle; StatePath=$statePath; ConfigPath=$configPath; ScratchRoot=$scratch; ExpectedCommit=$commit;
            ExpectedManifestSha256=$manifestHash; ExpectedConfigSha256=$configHash; ApprovedUserSid=$sid}
    }
    It 'plans without registering or enabling any task' {
        Mock Register-ScheduledTask { throw 'must not register' }
        $plan = & $installer @invokeParams | ConvertFrom-Json
        $plan.registered | Should Be $false
        $plan.enabled | Should Be $false
        $plan.multiple_instances | Should Be 'IgnoreNew'
        $plan.execution_limit | Should Be '30 minutes'
        $plan.process_tree_exit | Should Be 'NOT_PROVEN'
        $plan.job_containment | Should Be 'kill-on-close; owned Job only'
        $plan.python_pin_scope | Should Be 'python.exe only; stdlib and native DLLs not independently verified'
        Assert-MockCalled Register-ScheduledTask -Times 0 -Exactly -Scope It
    }
    It 'skips an unreadable Python alias and chooses a readable executable' {
        $missingAlias = Join-Path $root 'missing-alias.exe'
        (Resolve-SyntheticTestPython @($missingAlias, $python)) | Should Be $python
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
    It 'refuses scratch overlapping the private state' {
        $invokeParams.ScratchRoot = $private
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
            -not $Settings.Enabled -and $Settings.MultipleInstances -eq 'IgnoreNew' -and $Settings.ExecutionTimeLimit -eq 'PT30M' -and
            $Principal.LogonType -eq 'Interactive' -and $Principal.RunLevel -eq 'Limited' }
        Assert-MockCalled Start-ScheduledTask -Times 0 -Exactly -Scope It
        Assert-MockCalled Enable-ScheduledTask -Times 0 -Exactly -Scope It
        $encoded = [string]$global:wdSyntheticTaskAction.Arguments -replace '^.*-EncodedCommand ', ''
        $powershell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        $jobOutput = & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 0
        ($jobOutput | ConvertFrom-Json).job_cleanup | Should Be 'confirmed'
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
    It 'plans and registers a disabled one-shot card task under mocks and accepts its synthetic receipt' {
        $shared = Join-Path $root 'card-generations'
        $private = Join-Path $root 'card-generations.q4-lifecycle-private-v2'
        $baseGen = Join-Path $shared ('b'*40); $candidateGen = Join-Path $shared ('c'*40)
        $cardIndex = Join-Path $root 'card-index'
        New-Item -ItemType Directory -Path $baseGen,$candidateGen,$private,$cardIndex -Force | Out-Null
        $newBaseline = Join-Path $baseGen 'shared-session-config.v2.json'
        [IO.File]::Copy($baselinePath, $newBaseline)
        $candidate = Join-Path $candidateGen 'shared-session-config.v2.json'
        $candidatePlan=Join-Path $candidateGen 'public-plan.json'
        [IO.File]::WriteAllText($candidatePlan, (@{root=$cardIndex}|ConvertTo-Json -Compress))
        $candidateBinding=Join-Path $candidateGen 'lane-binding.v1.json'
        [IO.File]::WriteAllText($candidateBinding, (@{schema='wd.q4-lane-binding.v1';public_plan_sha256=(Get-FileHash $candidatePlan -Algorithm SHA256).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress))
        [IO.File]::WriteAllText($candidate, (@{public_plan=$candidatePlan;binding_path=$candidateBinding;binding_sha256=(Get-FileHash $candidateBinding -Algorithm SHA256).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress))
        $candidateHash=(Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        $badFlag=Join-Path $root 'synthetic-bad-card-receipt'
        $badFlagLiteral=ConvertTo-Json -InputObject $badFlag -Compress
        $bootstrapCode = 'import os,json; p=' + $badFlagLiteral + '; m=open(p).read() if os.path.isfile(p) else ""; r={"status":"PUBLISHED","pointer_sha256":"' + ('a'*64) + '","descriptor_sha256":("' + ('b'*64) + '" if m=="wrong" else "' + $candidateHash + '"),"freshness":"UNKNOWN"}; r.update({"extra":1} if m=="extra" else {}); s=json.dumps(r); print(s[:-1]+",\"status\":\"PUBLISHED\"}" if m=="duplicate" else s)'
        [IO.File]::WriteAllText($bootstrap, $bootstrapCode)
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $manifest.files.'tools-bootstrap/tools/wd_q4_live_update_bootstrap.py' = (Get-FileHash $bootstrap -Algorithm SHA256).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 8 -Compress))
        $newHash = (Get-FileHash $manifestPath -Algorithm SHA256).Hash
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $state.manifest_sha256 = $newHash; $state.final_manifest_sha256 = $newHash
        [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json -Depth 8 -Compress))
        $lifecycle = Join-Path $pins 'lifecycle.json'; $cardJob = Join-Path $pins 'card-job.json'; $returnFile = Join-Path $pins 'return.json'
        foreach ($path in @($lifecycle,$cardJob,$returnFile)) { [IO.File]::WriteAllText($path, '{}') }
        [IO.File]::WriteAllText($lifecycle, (@{schema='wd.q4-lifecycle-binding.v2';generation_root=$shared;private_root=$private} | ConvertTo-Json -Compress))
        $pin = { param($p) (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant() }
        $route = @{schema='wd.q4-card-refresh-tuple.v2'; p1_enabled=$true; manifest_sha256=$newHash.ToLowerInvariant();
            baseline=@{path=$newBaseline;sha256=(& $pin $newBaseline)}; candidate=@{path=$candidate;sha256=(& $pin $candidate)};
            previous_selector_sha256=$null;private_root=$private;job=@{path=$cardJob;sha256=(& $pin $cardJob)};
            job_return=@{exit_code=0;stdout=@{path=$returnFile;sha256=(& $pin $returnFile)}};
            source=@{path=$sourceFile;generation_path=$sidecar;generation_sha256=(& $pin $sidecar);file_identity='synthetic';bytes=2;sha256=(& $pin $sourceFile)};
            lifecycle=@{path=$lifecycle;sha256=(& $pin $lifecycle)}}
        [IO.File]::WriteAllText($routePath, ($route | ConvertTo-Json -Depth 8 -Compress))
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $config.schema='wd.q4-live-update-once.v2'; $config.route_sha256=& $pin $routePath
        $config.baseline_path=$newBaseline; $config.baseline_sha256=& $pin $newBaseline; $config.manifest_sha256=$newHash.ToLowerInvariant()
        $config | Add-Member -NotePropertyName p1_enabled -NotePropertyValue $true
        foreach ($key in @('plan_path','anchor_path','policy_path','public_policy_path')) {
            $part=Join-Path $pins ($key+'.json');[IO.File]::WriteAllText($part,'{}')
            $config | Add-Member -NotePropertyName $key -NotePropertyValue $part
        }
        [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 8 -Compress))
        $invokeParams.ExpectedManifestSha256=$newHash; $invokeParams.ExpectedConfigSha256=(Get-FileHash $configPath -Algorithm SHA256).Hash
        Mock Get-ScheduledTask { if ($TaskName -eq 'WD-MindLiveUpdate') { $global:wdSyntheticRegisteredTask } else { @() } }
        Mock Register-ScheduledTask {
            $global:wdSyntheticTaskAction=$Action
            $global:wdSyntheticRegisteredTask=New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal
        }
        Mock Start-ScheduledTask { throw 'must not start' }
        Mock Enable-ScheduledTask { throw 'must not enable' }
        $plan=& $installer @invokeParams -Apply | ConvertFrom-Json
        $plan.route_kind | Should Be 'card_v2'
        $plan.trigger_kind | Should Be 'one_shot'
        $plan.enabled | Should Be $false
        Assert-MockCalled Register-ScheduledTask -Times 1 -Exactly -Scope It
        Assert-MockCalled Start-ScheduledTask -Times 0 -Exactly -Scope It
        Assert-MockCalled Enable-ScheduledTask -Times 0 -Exactly -Scope It
        $encoded=[string]$global:wdSyntheticTaskAction.Arguments -replace '^.*-EncodedCommand ', ''
        $powershell=Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        $result=(& $powershell -NoProfile -NonInteractive -EncodedCommand $encoded | ConvertFrom-Json)
        $LASTEXITCODE | Should Be 0
        $result.status | Should Be 'PUBLISHED'
        $result.job_cleanup | Should Be 'confirmed'
        try { foreach ($kind in @('wrong','extra','duplicate')) {
            [IO.File]::WriteAllText($badFlag, $kind)
            & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
            $LASTEXITCODE | Should Be 3
        } } finally { [IO.File]::Delete($badFlag) }
        $originalPlan=[IO.File]::ReadAllText($candidatePlan)
        try {
            [IO.File]::AppendAllText($candidatePlan, ' ')
            & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
            $LASTEXITCODE | Should Be 3
        } finally { [IO.File]::WriteAllText($candidatePlan, $originalPlan) }
        [IO.File]::WriteAllText((Join-Path $shared 'q4-lifecycle-v2.pending.json'),'{}')
        & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 3
    }
    It 'does not replace an existing scheduled task' {
        Mock Get-ScheduledTask { [pscustomobject]@{TaskName='WD-MindLiveUpdate'} }
        Mock Register-ScheduledTask { throw 'must not register' }
        $threw = $false
        try { & $installer @invokeParams -Apply | Out-Null } catch { $threw = $true }
        $threw | Should Be $true
        Assert-MockCalled Register-ScheduledTask -Times 0 -Exactly -Scope It
    }
    It 'records a bound Job cleanup receipt after a synthetic child failure' {
        Mock Get-ScheduledTask { if ($TaskName -eq 'WD-MindLiveUpdate') { $global:wdSyntheticRegisteredTask } else { @() } }
        Mock Register-ScheduledTask {
            $global:wdSyntheticTaskAction = $Action
            $global:wdSyntheticRegisteredTask = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal
            [pscustomobject]@{ Stub = 'registered' }
        }
        & $installer @invokeParams -Apply | Out-Null
        [IO.File]::WriteAllText((Join-Path $bundle 'synthetic-crash'), '')
        $encoded = [string]$global:wdSyntheticTaskAction.Arguments -replace '^.*-EncodedCommand ', ''
        $powershell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 3
        $pending = Get-Content -LiteralPath (Join-Path $private 'q4-live-update.pending.json') -Raw | ConvertFrom-Json
        $jobReceipt = Get-Content -LiteralPath (Join-Path $private ('q4-live-job-result-' + $pending.attempt + '.json')) -Raw | ConvertFrom-Json
        $jobReceipt.attempt | Should Be $pending.attempt
        $jobReceipt.intent_sha256 | Should Be $pending.intent_sha256
        $jobReceipt.cleanup | Should Be 'confirmed'
        $jobReceipt.child_outcome | Should Be 'completed'
        $jobReceipt.exit_code | Should Be 3
    }
    It 'does not attest a foreign attempt after a synthetic child failure' {
        Mock Get-ScheduledTask { if ($TaskName -eq 'WD-MindLiveUpdate') { $global:wdSyntheticRegisteredTask } else { @() } }
        Mock Register-ScheduledTask {
            $global:wdSyntheticTaskAction = $Action
            $global:wdSyntheticRegisteredTask = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal
            [pscustomobject]@{ Stub = 'registered' }
        }
        & $installer @invokeParams -Apply | Out-Null
        [IO.File]::WriteAllText((Join-Path $bundle 'synthetic-crash'), 'foreign')
        $encoded = [string]$global:wdSyntheticTaskAction.Arguments -replace '^.*-EncodedCommand ', ''
        $powershell = Join-Path ([Environment]::SystemDirectory) 'WindowsPowerShell\v1.0\powershell.exe'
        & $powershell -NoProfile -NonInteractive -EncodedCommand $encoded
        $LASTEXITCODE | Should Be 3
        @(Get-ChildItem -LiteralPath $private -Filter 'q4-live-job-result-*.json').Count | Should Be 0
        Test-Path -LiteralPath (Join-Path $private 'q4-live-update.pending.json') | Should Be $true
    }
    It 'terminates an owned synthetic descendant on a job timeout' {
        . $jobHelper
        $pidFile = Join-Path $scratch 'synthetic-child.pid'
        $code = 'import pathlib,subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)'
        $job = Invoke-WdQ4ProcessJob -Executable $python -WorkingDirectory $bundle -ScratchRoot $scratch `
            -Arguments @('-I','-S','-B','-c',$code,$pidFile) -InputBytes ([byte[]]@()) `
            -TimeoutSeconds 4 -MaxOutputBytes 65536 -DetailedOutcome
        $job.Outcome | Should Be 'timeout'
        $job.Cleanup | Should Be 'confirmed'
        Test-Path -LiteralPath $pidFile | Should Be $true
        $childPid = [int][IO.File]::ReadAllText($pidFile)
        @(Get-Process -Id $childPid -ErrorAction SilentlyContinue).Count | Should Be 0
    }
}
