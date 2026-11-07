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


@pytest.mark.parametrize('flags',[['-Runtime','-Live'],['-Runtime','-WithCoverage']])
def test_staged_runtime_refuses_unimplemented_live_combinations_before_child(routing_bundle,flags):
    env, root=routing_bundle
    result=invoke(SHELLS[0],env,b'{"situation":"x","time":"2026-11-06T00:00:00Z"}',*flags)
    assert result.returncode!=0 and not (root/'routed-tool.txt').exists()


def test_runtime_requires_bundle_manifest_pin_before_child(routing_bundle):
    env, root=routing_bundle
    env['WD_REBOOT_EXPECTED_MANIFEST_HASH']='0'*64
    result=invoke(SHELLS[0],env,b'{"situation":"x","time":"2026-11-06T00:00:00Z"}','-Runtime')
    assert result.returncode!=0 and not (root/'routed-tool.txt').exists()
