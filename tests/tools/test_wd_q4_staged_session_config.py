"""The installed lane descriptor must carry both staged-card pins, or neither."""
import copy
import json
import subprocess

import pytest

import test_wd_q4_shared_session_config as sc
from test_wd_q4_default_descriptor_bootstrap import staged, canon, sha  # noqa: F401
from test_wd_q4_default_descriptor_resolver import HELPER, SHELL
from test_wd_startup_recovery import load, q


@pytest.mark.parametrize('both', [True, False])
def test_session_config_accepts_both_staged_pins_and_refuses_an_orphan(tmp_path, both):
    def mutate(doc):
        doc['expected_binding']['card_policy_sha256'] = '8b' * 32
        if both:
            doc['expected_binding']['card_decision_sha256'] = '9a' * 32
    result, _ = sc.run_v2(tmp_path, mutate=mutate)
    assert result['ok'] is both
    if both:
        assert result['value']['expected_binding']['card_policy_sha256'] == '8b' * 32


@pytest.mark.skipif(not SHELL, reason='Windows PowerShell required')
@pytest.mark.parametrize('mismatch', [False, True])
def test_default_descriptor_checks_staged_binding_pins(staged, mismatch):
    item = staged
    binding = copy.deepcopy(item['binding_doc'])
    descriptor = copy.deepcopy(item['descriptor_doc'])
    pins = {'card_decision_sha256': '9' * 64, 'card_policy_sha256': '8' * 64}
    descriptor['expected_binding'].update(pins)
    binding['binding'].update(pins)
    if mismatch:
        binding['binding']['card_policy_sha256'] = '7' * 64
    binding_raw = canon(binding)
    item['binding'].write_bytes(binding_raw)
    descriptor['binding_sha256'] = sha(binding_raw)
    descriptor_raw = canon(descriptor)
    item['descriptor'].write_bytes(descriptor_raw)
    pointer = {'schema': 'wd.q4-descriptor-pointer.v1', 'active_bundle': str(item['bundle']),
               'manifest_sha256': item['manifest_sha'].upper(), 'bundle_commit': 'b' * 40,
               'descriptor_path': str(item['descriptor']), 'descriptor_sha256': sha(descriptor_raw),
               'generation': 'b' * 40}
    pointer_path = item['bundle'].parent / 'WD_Q4_DESCRIPTOR_CURRENT.json'
    pointer_raw = canon(pointer)
    pointer_path.write_bytes(pointer_raw)
    script = load(HELPER, 'Get-WdQ4DefaultDescriptor') + f'''
$r=Get-WdQ4DefaultDescriptor -PointerPath {q(pointer_path)} -ExpectedPointerSha256 {q(sha(pointer_raw))} `
 -ActiveBundle {q(item['bundle'])} -ExpectedManifestHash {q(item['manifest_sha'])} -ExpectedCommit {'b' * 40}
@{{state=$r.State}}|ConvertTo-Json -Compress
'''
    result = subprocess.run([SHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=20, check=True)
    assert json.loads(result.stdout)['state'] == ('UNAVAILABLE' if mismatch else 'READY')
