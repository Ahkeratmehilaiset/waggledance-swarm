"""Synthetic install-default pin checks; never run the installer or change machine state."""
import json
import os
import subprocess

import pytest

from test_wd_q4_default_descriptor_bootstrap import INSTALLER, LANES, SHELL, canon, sha, staged
from test_wd_startup_recovery import load, q

pytestmark = pytest.mark.skipif(os.name != 'nt' or not SHELL, reason='Windows PowerShell required')
CONTENT = ('encoder_sha256', 'policy_sha256', 'roster_sha256', 'content_decision_sha256',
           'descriptor_sha256', 'memory_sha256', 'projection_sha256')
STATUS = ('pointer_sha256', 'source_anchor_sha256', 'encoder_sha256', 'policy_sha256',
          'roster_sha256', 'public_policy_sha256', 'descriptor_sha256', 'projection_sha256')


def install_default(item, *, descriptor_fields=None, binding_fields=None, raw_duplicate=None,
                    descriptor_duplicate=None):
    descriptor = dict(item['descriptor_doc'])
    binding = dict(item['binding_doc'])
    if descriptor_fields is not None:
        descriptor['expected_binding'] = descriptor_fields
    if binding_fields is not None:
        binding['binding'] = binding_fields
    binding_raw = canon(binding)
    if raw_duplicate is not None:
        token = canon(raw_duplicate)[1:-1]
        assert binding_raw.count(token) == 1
        binding_raw = binding_raw.replace(token, token + b',' + token, 1)
    item['binding'].write_bytes(binding_raw)
    descriptor['binding_sha256'] = sha(binding_raw)
    descriptor_raw = canon(descriptor)
    if descriptor_duplicate is not None:
        token = canon(descriptor_duplicate)[1:-1]
        assert descriptor_raw.count(token) == 1
        descriptor_raw = descriptor_raw.replace(token, token + b',' + token, 1)
    item['descriptor'].write_bytes(descriptor_raw)
    script = '$ErrorActionPreference="Stop"\nSet-StrictMode -Version Latest\n' + '\n'.join(
        load(INSTALLER, name) for name in ('Resolve-FullPath', 'Assert-WdPathWithoutReparse',
          'Read-Utf8DeploymentSnapshot', 'Get-WdPinnedQ4InstallDefault')) + f'''
try {{
 $result=Get-WdPinnedQ4InstallDefault -DescriptorPath {q(str(item['descriptor']))} -DescriptorSha256 {q(sha(descriptor_raw))} `
    -BundleRoot {q(str(item['bundle']))} -ExpectedManifestHash {q(item['manifest_sha'])} -ExpectedCommit {'b' * 40}
 @{{ok=$true;configured=($null -ne $result)}} | ConvertTo-Json -Compress
}} catch {{ @{{ok=$false}} | ConvertTo-Json -Compress }}
'''
    completed = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', script],
        capture_output=True, text=True, timeout=30, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def pins(names):
    return {name: format(i + 1, '064x') for i, name in enumerate(names)}


@pytest.mark.parametrize('names', [STATUS, STATUS + ('card_decision_sha256',),
                                  STATUS + ('card_decision_sha256', 'card_policy_sha256'), CONTENT],
                         ids=['status', 'card', 'staged', 'content'])
def test_accepts_valid_exact_pin_sets(staged, names):
    fields = pins(names)
    assert install_default(staged, descriptor_fields=fields, binding_fields=fields) == {'ok': True, 'configured': True}


@pytest.mark.parametrize('name', CONTENT)
def test_content_refuses_each_rehashed_binding_pin_mismatch(staged, name):
    expected = pins(CONTENT)
    changed = dict(expected)
    changed[name] = ('0' if expected[name][0] != '0' else '1') + expected[name][1:]
    assert install_default(staged, descriptor_fields=expected, binding_fields=changed) == {'ok': False}


@pytest.mark.parametrize('name', CONTENT)
@pytest.mark.parametrize('side', ['descriptor', 'binding'])
def test_content_refuses_each_missing_pin(staged, name, side):
    expected = pins(CONTENT)
    actual = dict(expected)
    target = expected if side == 'descriptor' else actual
    del target[name]
    assert install_default(staged, descriptor_fields=expected, binding_fields=actual) == {'ok': False}


@pytest.mark.parametrize('side', ['descriptor', 'binding'])
def test_content_refuses_extra_or_mixed_pin(staged, side):
    expected = pins(CONTENT)
    actual = dict(expected)
    target = expected if side == 'descriptor' else actual
    target['pointer_sha256'] = '1' * 64
    assert install_default(staged, descriptor_fields=expected, binding_fields=actual) == {'ok': False}


def test_content_refuses_duplicate_binding_property(staged):
    expected = pins(CONTENT)
    assert install_default(staged, descriptor_fields=expected, binding_fields=expected,
        raw_duplicate={'encoder_sha256': expected['encoder_sha256']}) == {'ok': False}


def test_content_refuses_duplicate_descriptor_property(staged):
    expected = pins(CONTENT)
    assert install_default(staged, descriptor_fields=expected, binding_fields=expected,
        descriptor_duplicate={'encoder_sha256': expected['encoder_sha256']}) == {'ok': False}


def test_content_refuses_case_changed_binding_name(staged):
    expected = pins(CONTENT)
    actual = dict(expected)
    actual['ENCODER_SHA256'] = actual.pop('encoder_sha256')
    assert install_default(staged, descriptor_fields=expected, binding_fields=actual) == {'ok': False}


def test_content_refuses_nonstring_binding_pin(staged):
    expected = pins(CONTENT)
    actual = dict(expected); actual['memory_sha256'] = 1
    assert install_default(staged, descriptor_fields=expected, binding_fields=actual) == {'ok': False}


@pytest.mark.parametrize('name', ['card_decision_sha256', 'card_policy_sha256'])
def test_staged_rejects_each_optional_card_pin_mismatch(staged, name):
    expected = pins(STATUS + ('card_decision_sha256', 'card_policy_sha256'))
    actual = dict(expected); actual[name] = 'f' * 64
    assert install_default(staged, descriptor_fields=expected, binding_fields=actual) == {'ok': False}
