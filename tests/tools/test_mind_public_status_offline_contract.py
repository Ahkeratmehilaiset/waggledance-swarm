"""Static contracts for isolated public-status wrapper coverage; no wrapper execution."""
import ast
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/mind-adopt-platform-coverage.yml'
TARGET = ROOT / 'tests/tools/test_wd_mind_public_status_wrapper.py'
TEST_FILE = 'tests/tools/test_wd_mind_public_status_wrapper.py'
FUNCTIONS = (
    'test_default_off_without_any_pins',
    'test_separate_candidate_and_active_anchors_reach_fixed_entrypoint',
    'test_launcher_copy_outside_candidate_refuses_before_entrypoint',
    'test_wrong_or_changed_anchor_refuses_before_entrypoint',
    'test_entered_outcome_is_not_retried_or_mistaken_for_canonical',
)
EXPECTED_NAMES = {
    *FUNCTIONS[:3],
    *(f'{FUNCTIONS[3]}[{x}]' for x in (
        'active_pin', 'candidate_pin', 'active_helper', 'candidate_wrapper',
        'candidate_launcher', 'candidate_code', 'candidate_fleet')),
    *(f'{FUNCTIONS[4]}[{x}]' for x in ('unknown', 'queued')),
}


def script():
    workflow = yaml.safe_load(WORKFLOW.read_bytes())
    return next(step['run'] for step in workflow['jobs']['adopt-platform-windows']['steps']
                if step.get('name') == 'Run exact synthetic coverage in offline Windows container')


def test_only_twelve_windows_cases_are_skipped_on_nonwindows():
    tree = ast.parse(TARGET.read_text(encoding='utf-8'))
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name in FUNCTIONS:
        assert any('skipif' in ast.unparse(d) and 'os.name' in ast.unparse(d)
                   for d in functions[name].decorator_list), name
    writer = functions['test_real_bridge_wrapper_with_synthetic_active_helpers_and_two_manifest_pins']
    assert not any('skipif' in ast.unparse(d) and 'os.name' in ast.unparse(d)
                   for d in writer.decorator_list)


def test_archive_contains_exact_test_file_and_wrapper():
    text = script()
    source = text.split('$sourcePaths = @(', 1)[1].split('\n', 1)[0]
    assert f"'{TEST_FILE}'" in source
    assert "':(glob)ops/windows/reboot/*.ps1'" in source
    assert "'tests/conftest.py'" in source
    assert "':(glob)tools/*.py'" in source


def test_only_five_named_function_selectors_execute():
    text = script()
    selected = text.split('$publicStatusTests=@(', 1)[1].split(')', 1)[0]
    ids = re.findall(r"'([^']+)'", selected)
    assert ids == [f'{TEST_FILE}::{name}' for name in FUNCTIONS]
    assert 'test_real_bridge_wrapper_with_synthetic_active_helpers_and_two_manifest_pins' not in selected
    assert 'python.exe -m pytest @publicStatusTests' in text
    assert "throw 'Public status synthetic tests failed'" in text


def test_pytest_environment_is_cleared_before_execution():
    text = script()
    clean = "$inheritedPythonInputs=@(Get-ChildItem Env: | Where-Object { $_.Name -match '^(?i:PYTEST_|PYTHON)' })"
    assert clean in text
    assert "foreach ($pythonInputVariable in $inheritedPythonInputs) { Remove-Item -LiteralPath ('Env:' + $pythonInputVariable.Name) -ErrorAction Stop }" in text
    assert "throw 'Inherited pytest or Python environment could not be removed'" in text
    assert text.index(clean) < text.index('python.exe -m pytest @publicStatusTests')
    assert "$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'" in text
    assert "$env:PYTHONNOUSERSITE='1'" in text
    assert "sys.flags.optimize == 0" in text


def test_junit_requires_exact_twelve_unique_successes():
    text = script()
    section = text.split('$publicStatusExpected=@(', 1)[1].split(')', 1)[0]
    names = re.findall(r"'([^']+)'", section)
    assert len(names) == 12 and set(names) == EXPECTED_NAMES
    assert "$publicStatusCases.Count -ne 12" in text
    assert '@($publicStatusCases.name | Sort-Object -Unique).Count -ne 12' in text
    assert "$_.classname -cne 'tests.tools.test_wd_mind_public_status_wrapper'" in text
    for token in ("SelectSingleNode('skipped')", "SelectSingleNode('failure')", "SelectSingleNode('error')"):
        assert token in text.split('$publicStatusExpected=@(', 1)[1]


def _powershell_hosts():
    names = ('powershell.exe', 'pwsh.exe') if os.name == 'nt' else ('pwsh',)
    return [host for name in names if (host := shutil.which(name))]


@pytest.mark.parametrize('host', _powershell_hosts())
@pytest.mark.parametrize('fault', ('valid', 'duplicate', 'other_file', 'hidden_skip', 'hidden_failure', 'hidden_error', 'wrong_name'))
def test_junit_gate_with_synthetic_xml(tmp_path, host, fault):
    text = script()
    block = '$publicStatusSuite=' + text.split('$publicStatusSuite=', 1)[1].split("Write-Output 'Public status Windows coverage:", 1)[0]
    root = ET.Element('testsuites')
    suite = ET.SubElement(root, 'testsuite', skipped='0', failures='0', errors='0')
    cases = [ET.SubElement(suite, 'testcase', classname='tests.tools.test_wd_mind_public_status_wrapper', name=name)
             for name in sorted(EXPECTED_NAMES)]
    if fault == 'duplicate': cases[1].set('name', cases[0].attrib['name'])
    if fault == 'other_file': cases[0].set('classname', 'tests.tools.other')
    if fault == 'wrong_name': cases[0].set('name', 'test_real_bridge_wrapper_with_synthetic_active_helpers_and_two_manifest_pins')
    if fault.startswith('hidden_'): ET.SubElement(cases[0], fault.removeprefix('hidden_').replace('skip', 'skipped'))
    path = tmp_path / 'public-status-synthetic.xml'
    ET.ElementTree(root).write(path, encoding='utf-8')
    env = {'PATH': '', 'WD_TEST_JUNIT': str(path)}
    if os.name == 'nt': env['SystemRoot'] = os.environ['SystemRoot']
    code = "$ErrorActionPreference='Stop'\n[xml]$publicStatusResult=Get-Content -Raw -LiteralPath $env:WD_TEST_JUNIT\n" + block
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-Command', code], env=env,
                            capture_output=True, text=True, timeout=20)
    assert (result.returncode == 0) is (fault == 'valid'), result.stdout + result.stderr


@pytest.mark.parametrize('host', _powershell_hosts())
def test_environment_scrub_with_fake_pytest_inputs(host):
    text = script()
    start = '$inheritedPythonInputs=@(Get-ChildItem Env:'
    block = start + text.split(start, 1)[1].split("Remove-Item Env:PYTHONOPTIMIZE -ErrorAction SilentlyContinue", 1)[0]
    env = {'PATH': '', 'PYTEST_ADDOPTS': '-p malicious_synthetic_plugin',
           'PYTEST_PLUGINS': 'malicious_synthetic_plugin', 'PYTEST_UNKNOWN': 'x',
           'PYTHONOPTIMIZE': '2', 'PYTHONPATH': 'synthetic', 'PYTHONSTARTUP': 'synthetic'}
    if os.name == 'nt': env['SystemRoot'] = os.environ['SystemRoot']
    code = ("$ErrorActionPreference='Stop'\n" + block + "\n"
            "@(Get-ChildItem Env: | Where-Object { $_.Name -match '^(?i:PYTEST_|PYTHON)' } | "
            "ForEach-Object { $_.Name.ToUpperInvariant() } | Sort-Object) | ConvertTo-Json -Compress")
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-Command', code], env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert sorted(values) == ['PYTEST_DISABLE_PLUGIN_AUTOLOAD', 'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE']

