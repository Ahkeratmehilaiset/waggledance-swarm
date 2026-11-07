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

@pytest.mark.parametrize('flag',[False,0,1,'true',None,[]])
def test_installer_v2_refuses_nonliteral_opt_in_before_task_api(tmp_path,flag):
    result=plan(tmp_path,'v2',flag);assert result.returncode!=0 and not result.stdout

@pytest.mark.parametrize('mode',['missing','unknown','downgrade','duplicate'])
def test_installer_v2_refuses_ambiguous_policy_before_task_api(tmp_path,mode):
    result=plan(tmp_path,mode);assert result.returncode!=0 and not result.stdout
