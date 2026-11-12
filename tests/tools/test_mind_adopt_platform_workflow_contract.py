"""Offline CI must not run untrusted source or alter a persistent host firewall."""
from pathlib import Path

import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/mind-adopt-platform-coverage.yml'
HOSTED_GUARD = 'if [ "${RUNNER_ENVIRONMENT:-}" != \'github-hosted\' ]; then echo \'Expected disposable hosted Linux\' >&2; exit 1; fi'


def _linux_steps():
    workflow = yaml.safe_load(WORKFLOW.read_bytes())
    return {step['name']: step['run'] for step in workflow['jobs']['adopt-platform-linux']['steps']
            if 'name' in step and 'run' in step}


def _assert_hosted_before_firewall(script):
    assert HOSTED_GUARD in script
    assert script.index(HOSTED_GUARD) < script.index('sudo iptables')


def test_firewall_test_and_cleanup_require_disposable_host():
    steps = _linux_steps()
    _assert_hosted_before_firewall(steps['Portable tests with outbound denial'])
    _assert_hosted_before_firewall(steps['Cleanup firewall'])


def test_named_pytest_invocations_use_source_only_archive():
    script = _linux_steps()['Portable tests with outbound denial']
    lines = [line.strip() for line in script.splitlines() if 'python -m pytest' in line]
    assert len(lines) == 3
    assert all(line.startswith('( cd "$scratch/source"; env -i ') and line.endswith(' )') for line in lines)
    assert sum('--collect-only' in line for line in lines) == 1
    assert sum('--junitxml' in line for line in lines) == 2
    assert all('PYTEST_DISABLE_PLUGIN_AUTOLOAD=1' in line for line in lines)
    assert 'trap cleanup EXIT' in script
