"""Synthetic recall routing; no live bridge, runtime, or model."""
import hashlib
import json
import os

import pytest

from tests.tools.test_wd_q4_lane_recall_helper import SHELLS, bundle, invoke

pytestmark = pytest.mark.skipif(os.name != 'nt' or not SHELLS, reason='Windows PowerShell required')


@pytest.fixture
def routing_bundle(bundle):
    env, root = bundle
    wrapper = root/'Invoke-WdBridgePython.ps1'
    wrapper.write_text(
        "param([Parameter(Position=0)][string]$Tool,[Parameter(ValueFromRemainingArguments)][string[]]$Rest)\n"
        "[IO.File]::WriteAllText($env:RECALL_ROUTED_TOOL,$Tool)\n"
        "[IO.File]::WriteAllText($env:RECALL_ROUTED_LIVE,[string]$env:WD_Q4_LIVE_MODE)\n"
        "[IO.File]::WriteAllText($env:RECALL_ROUTED_P1,[string]$env:WD_Q4_LIVE_P1)\n"
        "$m=[IO.MemoryStream]::new(); [Console]::OpenStandardInput().CopyTo($m)\n"
        "$h=[Security.Cryptography.SHA256]::Create(); try { "
        "$v=[BitConverter]::ToString($h.ComputeHash($m.ToArray())).Replace('-','').ToLowerInvariant() "
        "} finally { $h.Dispose(); $m.Dispose() }; Write-Output $v; $global:LASTEXITCODE=0\n",
        encoding='utf-8')
    manifest=root/'deployment-manifest.json'
    manifest.write_text(json.dumps({'schema_version':1,'files':{
        'Invoke-WdBridgePython.ps1':hashlib.sha256(wrapper.read_bytes()).hexdigest()}}),encoding='utf-8')
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    env['RECALL_ROUTED_TOOL']=str(root/'routed-tool.txt')
    env['RECALL_ROUTED_LIVE']=str(root/'routed-live.txt')
    env['RECALL_ROUTED_P1']=str(root/'routed-p1.txt')
    return env, root


@pytest.mark.parametrize('runtime,expected',[(False,'tools/wd_q4_lane_caller_cli.py'),
    (True,'tools/wd_q4_lane_caller_runtime_bootstrap.py')])
def test_explicit_runtime_selects_only_its_fixed_tool(routing_bundle,runtime,expected):
    env, root=routing_bundle
    raw=b'{"situation":"synthetic","time":"2026-11-06T00:00:00Z"}'
    result=invoke(SHELLS[0],env,raw,*(['-Runtime'] if runtime else []))
    assert result.returncode==0, result.stderr
    assert (root/'routed-tool.txt').read_text()==expected
    assert result.stdout.strip().decode()==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('live,inherited,expected', [(True,'','1'), (False,'1','')])
def test_runtime_live_is_explicit_and_confined_to_child(routing_bundle,live,inherited,expected):
    env, root=routing_bundle
    env['WD_Q4_LIVE_MODE']=inherited
    raw=b'{"situation":"synthetic","time":"2026-11-06T00:00:00Z"}'
    result=invoke(SHELLS[0],env,raw,'-Runtime',*(['-Live'] if live else []))
    assert result.returncode==0, result.stderr
    assert (root/'routed-tool.txt').read_text()=='tools/wd_q4_lane_caller_runtime_bootstrap.py'
    assert (root/'routed-live.txt').read_text()==expected
    assert result.stdout.strip().decode()==hashlib.sha256(raw).hexdigest()
    assert env['WD_Q4_LIVE_MODE']==inherited


@pytest.mark.parametrize('flags',[['-Runtime','-WithCoverage'],['-Runtime','-Live','-WithCoverage']])
def test_staged_runtime_refuses_unimplemented_live_combinations_before_child(routing_bundle,flags):
    env, root=routing_bundle
    result=invoke(SHELLS[0],env,b'{"situation":"x","time":"2026-11-06T00:00:00Z"}',*flags)
    assert result.returncode!=0 and not (root/'routed-tool.txt').exists()


def test_runtime_requires_bundle_manifest_pin_before_child(routing_bundle):
    env, root=routing_bundle
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH']='0'*64
    result=invoke(SHELLS[0],env,b'{"situation":"x","time":"2026-11-06T00:00:00Z"}','-Runtime')
    assert result.returncode!=0 and not (root/'routed-tool.txt').exists()


@pytest.mark.parametrize('shell',SHELLS)
@pytest.mark.parametrize('opt_in,inherited,expected', [(False,'1',''), (True,'bad','1')])
def test_live_p1_is_explicit_and_confined_to_runtime_child(routing_bundle,shell,opt_in,inherited,expected):
    env,root=routing_bundle
    env['WD_Q4_LIVE_P1']=inherited
    raw=b'{"situation":"synthetic","time":"2026-11-06T00:00:00Z"}'
    result=invoke(shell,env,raw,'-Runtime','-Live',*(['-LiveP1'] if opt_in else []))
    assert result.returncode==0, result.stderr
    assert (root/'routed-p1.txt').read_text()==expected
    assert (root/'routed-live.txt').read_text()=='1'
    assert result.stdout.strip().decode()==hashlib.sha256(raw).hexdigest()
    assert env['WD_Q4_LIVE_P1']==inherited


@pytest.mark.parametrize('flags',[['-LiveP1'],['-Runtime','-LiveP1'],['-Live','-LiveP1'],
    ['-Runtime','-Live','-LiveP1','-WithCoverage']])
def test_live_p1_requires_runtime_live_without_coverage_before_child(routing_bundle,flags):
    env,root=routing_bundle
    result=invoke(SHELLS[0],env,b'{"situation":"x","time":"2026-11-06T00:00:00Z"}',*flags)
    assert result.returncode!=0 and not (root/'routed-tool.txt').exists()
