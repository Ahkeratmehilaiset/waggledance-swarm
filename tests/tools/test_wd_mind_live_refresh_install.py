"""Passive synthetic installer tests. Never Apply/register/enable/launch."""
import json,os,pathlib,secrets,subprocess,sys
import pytest
REPO=pathlib.Path(__file__).resolve().parents[2]
@pytest.fixture
def tmp_path(tmp_path_factory):
    path=tmp_path_factory.getbasetemp()/('p'+secrets.token_hex(3));path.mkdir()
    return path

PS_RUNNER = r'''param([string]$InstallerPath,[string]$FixturePath,[string]$FixtureDataRoot,[string]$CasePath)
$ErrorActionPreference='Stop'
# The parent may be PowerShell 7; explicitly load the inbox Windows
# PowerShell module instead of autoloading an incompatible PS7 module.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
$tokens=$null;$parseErrors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($FixturePath,[ref]$tokens,[ref]$parseErrors)
if($parseErrors.Count){throw 'fixture parse failed'}
$function=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq 'Resolve-SyntheticTestPython'},$true))
$before=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.CommandAst] -and $node.GetCommandName() -ceq 'BeforeEach'},$true))
if($function.Count -ne 1 -or $before.Count -ne 1){throw 'fixture shape changed'}
. ([scriptblock]::Create($function[0].Extent.Text))
$block=@($before[0].CommandElements|Where-Object {$_ -is [Management.Automation.Language.ScriptBlockExpressionAst]})
if($block.Count -ne 1){throw 'fixture body missing'}
$fixtureTestRoot=Split-Path -Parent $FixturePath
$TestDrive=$FixtureDataRoot
# Only the known setup body is evaluated, never Describe/It or an Apply block.
# Retain its quoted-space paths, shorten random suffix under our short case root.
$text=$block[0].ScriptBlock.Extent.Text
$text=$text.Substring(1,$text.Length-2).Replace('$PSScriptRoot','$fixtureTestRoot').Replace("ToString('N')","ToString('N').Substring(0,4)")
. ([scriptblock]::Create($text))
function Register-ScheduledTask {throw 'registration forbidden in plan test'}
function Start-ScheduledTask {throw 'launch forbidden in plan test'}
function Enable-ScheduledTask {throw 'enable forbidden in plan test'}
$case=Get-Content -LiteralPath $CasePath -Raw -Encoding UTF8|ConvertFrom-Json
$config=Get-Content -LiteralPath $configPath -Raw -Encoding UTF8|ConvertFrom-Json
if($case.mode -cne 'v1'){
    $config.schema='wd.q4-live-update-once.v2'
    $config|Add-Member -NotePropertyName p1_enabled -NotePropertyValue $case.flag
    foreach($key in @('plan_path','anchor_path','policy_path','public_policy_path')){
        $part=Join-Path $pins ($key+'.json');[IO.File]::WriteAllText($part,'{}')
        $config|Add-Member -NotePropertyName $key -NotePropertyValue $part
    }
}
if($case.mode -clike 'card_v2*'){
    $shared=Join-Path $root 'shared-generations'
    $private=Join-Path $root 'shared-generations.q4-lifecycle-private-v2'
    $cardIndex=Join-Path $root 'card-index'; $lifecycleDir=Join-Path $root 'lifecycle-inputs'
    $jobDir=Join-Path $root 'job-inputs'; $returnDir=Join-Path $root 'job-return-inputs'
    $baseGen=Join-Path $shared ('b'*40); $candidateGen=Join-Path $shared ('c'*40)
    New-Item -ItemType Directory -Path $baseGen,$candidateGen,$private,$cardIndex,$lifecycleDir,$jobDir,$returnDir -Force|Out-Null
    $newBaseline=Join-Path $baseGen 'shared-session-config.v2.json'
    [IO.File]::Copy($baselinePath,$newBaseline)
    $candidate=Join-Path $candidateGen 'shared-session-config.v2.json'
    $candidatePlan=Join-Path $candidateGen 'public-plan.json'
    [IO.File]::WriteAllText($candidatePlan,(@{schema='wd.q4-public-wrapper-plan.v3';root=$cardIndex}|ConvertTo-Json -Compress))
    $candidateBinding=Join-Path $candidateGen 'lane-binding.v1.json'
    [IO.File]::WriteAllText($candidateBinding,(@{schema='wd.q4-lane-binding.v1';public_plan_sha256=(Get-FileHash $candidatePlan -Algorithm SHA256).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress))
    [IO.File]::WriteAllText($candidate,(@{public_plan=$candidatePlan;binding_path=$candidateBinding;binding_sha256=(Get-FileHash $candidateBinding -Algorithm SHA256).Hash.ToLowerInvariant()}|ConvertTo-Json -Compress))
    $lifecycle=Join-Path $lifecycleDir 'lifecycle.json'; $job=Join-Path $jobDir 'card-job.json'; $stdout=Join-Path $returnDir 'job-return.json'
    foreach($file in @($lifecycle,$job,$stdout)){[IO.File]::WriteAllText($file,'{}')}
    [IO.File]::WriteAllText($lifecycle,(@{schema='wd.q4-lifecycle-binding.v2';generation_root=$shared;private_root=$private}|ConvertTo-Json -Compress))
    $pin={param($p) (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLowerInvariant()}
    $config.baseline_path=$newBaseline; $config.baseline_sha256=& $pin $newBaseline
    $route=[ordered]@{schema='wd.q4-card-refresh-tuple.v2';p1_enabled=$true;manifest_sha256=$config.manifest_sha256;
      baseline=@{path=$newBaseline;sha256=$config.baseline_sha256};candidate=@{path=$candidate;sha256=(& $pin $candidate)};
      previous_selector_sha256=$null;private_root=$private;job=@{path=$job;sha256=(& $pin $job)};
      job_return=@{exit_code=0;stdout=@{path=$stdout;sha256=(& $pin $stdout)}};
      source=@{path=$sourceFile;generation_path=$sidecar;generation_sha256=(& $pin $sidecar);
          file_identity='synthetic';bytes=2;sha256=(& $pin $sourceFile)};
      lifecycle=@{path=$lifecycle;sha256=(& $pin $lifecycle)}}
    switch ($case.mode) {
        'card_v2_candidate_pin' { $route.candidate.sha256='0'*64 }
        'card_v2_lifecycle_pin' { $route.lifecycle.sha256='0'*64 }
        'card_v2_lifecycle_binding' { [IO.File]::WriteAllText($lifecycle,'{}'); $route.lifecycle.sha256=& $pin $lifecycle }
        'card_v2_plan_tamper' { [IO.File]::WriteAllText($candidatePlan,(@{root=$source}|ConvertTo-Json -Compress)) }
        'card_v2_plan_schema' {
            [IO.File]::WriteAllText($candidatePlan,(@{schema='wrong';root=$cardIndex}|ConvertTo-Json -Compress))
            [IO.File]::WriteAllText($candidateBinding,(@{schema='wd.q4-lane-binding.v1';public_plan_sha256=(& $pin $candidatePlan)}|ConvertTo-Json -Compress))
            [IO.File]::WriteAllText($candidate,(@{public_plan=$candidatePlan;binding_path=$candidateBinding;binding_sha256=(& $pin $candidateBinding)}|ConvertTo-Json -Compress))
            $route.candidate.sha256=& $pin $candidate
        }
        'card_v2_foreign_binding' {
            $foreign=Join-Path $pins 'foreign-binding.json'; [IO.File]::Copy($candidateBinding,$foreign)
            [IO.File]::WriteAllText($candidate,(@{public_plan=$candidatePlan;binding_path=$foreign;binding_sha256=(& $pin $foreign)}|ConvertTo-Json -Compress))
            $route.candidate.sha256=& $pin $candidate
        }
        'card_v2_foreign_plan' {
            $foreign=Join-Path $pins 'foreign-plan.json'; [IO.File]::Copy($candidatePlan,$foreign)
            [IO.File]::WriteAllText($candidate,(@{public_plan=$foreign;binding_path=$candidateBinding;binding_sha256=(& $pin $candidateBinding)}|ConvertTo-Json -Compress))
            $route.candidate.sha256=& $pin $candidate
        }
        'card_v2_job_pin' { $route.job.sha256='0'*64 }
        'card_v2_return_pin' { $route.job_return.stdout.sha256='0'*64 }
        'card_v2_baseline_pin' { $route.baseline.sha256='0'*64 }
        'card_v2_source_generation_pin' { $route.source.generation_sha256='0'*64 }
        'card_v2_private_pending' { [IO.File]::WriteAllText((Join-Path $private 'q4-card-refresh.pending.json'),'{}') }
        'card_v2_generation_pending' { [IO.File]::WriteAllText((Join-Path $shared 'q4-lifecycle-v2.pending.json'),'{}') }
        'card_v2_publication_pending' { [IO.File]::WriteAllText((Join-Path $shared 'q4-live-publication.pending.json'),'{}') }
        'card_v2_dangling_pending' {
            try { New-Item -ItemType SymbolicLink -Path (Join-Path $shared 'q4-lifecycle-v2.pending.json') -Target (Join-Path $shared 'does-not-exist') -ErrorAction Stop|Out-Null }
            catch { exit 77 }
        }
        'card_v2_scratch_overlap' { $invokeParams.ScratchRoot=$private }
        'card_v2_scratch_index' { $invokeParams.ScratchRoot=$cardIndex }
        'card_v2_scratch_lifecycle' { $invokeParams.ScratchRoot=$lifecycleDir }
        'card_v2_scratch_job' { $invokeParams.ScratchRoot=$jobDir }
        'card_v2_scratch_return' { $invokeParams.ScratchRoot=$returnDir }
    }
    [IO.File]::WriteAllText($routePath,($route|ConvertTo-Json -Depth 8 -Compress))
    $config.route_sha256=& $pin $routePath
}
if($case.mode -ceq 'missing'){$config.PSObject.Properties.Remove('p1_enabled')}
if($case.mode -ceq 'unknown'){$config|Add-Member -NotePropertyName unexpected -NotePropertyValue 'invalid'}
if($case.mode -ceq 'downgrade'){$config.schema='wd.q4-live-update-once.v1'}
$raw=$config|ConvertTo-Json -Depth 8 -Compress
if($case.mode -ceq 'duplicate'){$raw=$raw.Substring(0,$raw.Length-1)+',"p1_enabled":true}'}
[IO.File]::WriteAllText($configPath,$raw)
$invokeParams.ExpectedConfigSha256=(Get-FileHash -LiteralPath $configPath -Algorithm SHA256).Hash
$plan=& $InstallerPath @invokeParams|ConvertFrom-Json
if($plan.registered -ne $false -or $plan.enabled -ne $false -or $plan.execution -cne 'NOT_RUN'){throw 'not a passive plan'}
if(Test-Path -LiteralPath (Join-Path $bundle 'synthetic-invoked.txt')){throw 'child unexpectedly ran'}
@{ok=$true;plan=$plan}|ConvertTo-Json -Depth 8 -Compress
'''

def plan(tmp_path,mode,flag=True):
    script=tmp_path/'plan_runner.ps1';script.write_text(PS_RUNNER,encoding='utf-8')
    case=tmp_path/'case.json';case.write_text(json.dumps({'mode':mode,'flag':flag}))
    return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script),
        str(REPO/'ops/windows/reboot/Install-WdMindLiveRefresh.ps1'),
        str(REPO/'tests/tools/test_wd_mind_live_refresh_installer.ps1'),str(tmp_path),str(case)],
        env=dict(os.environ,WD_TEST_Q4_PYTHON=sys.executable),capture_output=True,text=True,timeout=45)

def test_installer_legacy_read_only_plan_control(tmp_path):
    result=plan(tmp_path,'v1');assert result.returncode==0,result.stderr
    value=json.loads(result.stdout);assert value['ok'] and not value['plan']['registered']

def test_installer_v2_read_only_plan_control(tmp_path):
    result=plan(tmp_path,'v2');assert result.returncode==0,result.stderr
    value=json.loads(result.stdout);assert value['ok'] and not value['plan']['enabled']

def test_card_v2_installer_plans_disabled_one_shot_without_running(tmp_path):
    result=plan(tmp_path,'card_v2');assert result.returncode==0,result.stderr
    value=json.loads(result.stdout);assert value['plan']['route_kind']=='card_v2'
    assert value['plan']['trigger_kind']=='one_shot' and not value['plan']['enabled']

@pytest.mark.parametrize('fault',['candidate_pin','lifecycle_pin','lifecycle_binding','plan_tamper','plan_schema',
    'foreign_binding','foreign_plan','job_pin','return_pin','baseline_pin',
    'source_generation_pin','private_pending','generation_pending','publication_pending','scratch_overlap',
    'scratch_index','scratch_lifecycle','scratch_job','scratch_return'])
def test_card_v2_installer_refuses_invalid_pins_or_pending_before_task_api(tmp_path,fault):
    result=plan(tmp_path,'card_v2_'+fault);assert result.returncode!=0 and not result.stdout

def test_card_v2_installer_refuses_dangling_pending_entry(tmp_path):
    result=plan(tmp_path,'card_v2_dangling_pending')
    if result.returncode==77: pytest.skip('Windows symlink creation unavailable')
    assert result.returncode!=0 and not result.stdout

def test_pending_entry_counts_a_dangling_directory_entry_and_unknown_parent(tmp_path):
    script=tmp_path/'pending_probe.ps1'
    script.write_text(r'''param($Installer)
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Installer,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'parse'}
$fn=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Test-PendingEntry'},$true))
if($fn.Count -ne 1){throw 'shape'}
. ([scriptblock]::Create($fn[0].Extent.Text))
function Get-ChildItem {param($LiteralPath,[switch]$Force,$ErrorAction) [pscustomobject]@{Name='q4-lifecycle-v2.pending.json'} }
if(-not (Test-PendingEntry 'C:\synthetic\q4-lifecycle-v2.pending.json')){throw 'missed directory entry'}
if(Test-PendingEntry 'C:\synthetic\other.json'){throw 'invented entry'}
function Get-ChildItem {param($LiteralPath,[switch]$Force,$ErrorAction) throw 'unreadable'}
if(-not (Test-PendingEntry 'C:\synthetic\other.json')){throw 'unknown parent accepted'}
''',encoding='utf-8')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script),
        str(REPO/'ops/windows/reboot/Install-WdMindLiveRefresh.ps1')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr

@pytest.mark.parametrize('flag',[False,0,1,'true',None,[]])
def test_installer_v2_refuses_nonliteral_opt_in_before_task_api(tmp_path,flag):
    result=plan(tmp_path,'v2',flag);assert result.returncode!=0 and not result.stdout

@pytest.mark.parametrize('mode',['missing','unknown','downgrade','duplicate'])
def test_installer_v2_refuses_ambiguous_policy_before_task_api(tmp_path,mode):
    result=plan(tmp_path,mode);assert result.returncode!=0 and not result.stdout
