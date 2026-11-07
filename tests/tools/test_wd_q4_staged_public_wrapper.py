"""Staged card plans must preserve pins before they reach the Windows job."""
import hashlib
import json
import sys

import pytest

from test_lane_card_decision_forward import BASE, WRAPPER, ROOT, ps, quote, windows


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_file(path, data):
    path.write_bytes(data)
    return str(path), digest(data)


@windows
def test_staged_plan_loads_pinned_inputs_and_rejects_a_changed_pointer(tmp_path):
    root = tmp_path / 'snapshot'
    root.mkdir()
    generation, _ = write_file(root / 'generation.json', b'{"generation":"g"}')
    plan = {'schema': 'wd.q4-public-wrapper-plan.v3', 'root': str(root), 'config': {},
            'pointer_sha256': 'a' * 64, 'source_anchor_sha256': 'b' * 64,
            'encoder_sha256': 'c' * 64, 'generation_path': str(generation),
            'end_cursor': {'offset': 1, 'generation': 'g', 'file_identity': 'x'}}
    for name in ('policy', 'roster', 'public_policy', 'card_decision', 'card_policy', 'pointer'):
        plan[name + '_path'], plan[name + '_sha256'] = write_file(tmp_path / (name + '.json'), b'{}')
    plan['source_path'], _ = write_file(tmp_path / 'source.jsonl', b'{}\n')
    path, pin = write_file(tmp_path / 'plan.json', json.dumps(plan, separators=(',', ':')).encode())
    script = (f". '{quote(WRAPPER)}'\n" + BASE +
              f"$plan=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}'\n"
              f"[IO.File]::WriteAllText('{quote(plan['pointer_path'])}','changed')\n"
              f"$tampered=try {{$null=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}';'accepted'}}catch{{'refused'}}\n"
              "@{staged=$plan.Staged;pointer=$plan.Inputs.pointer.Length;tampered=$tampered} | ConvertTo-Json -Compress")
    assert ps(script) == {'staged': True, 'pointer': 2, 'tampered': 'refused'}


@windows
def test_staged_plan_reaches_real_windows_job_as_framed_inputs(tmp_path):
    """The real outer Windows Job carries the staged tuple, while this child is synthetic."""
    root = tmp_path / 'code'
    tools = root / 'tools'
    tools.mkdir(parents=True)
    (tools / '__init__.py').write_text('')
    (tools / 'wd_q4_agent_query.py').write_text('class VerifiedQ4Config:\n def __init__(self, **kw): pass\n')
    bridge = root / 'waggledance' / 'core'
    bridge.mkdir(parents=True)
    (root / 'waggledance' / '__init__.py').write_text('')
    (bridge / '__init__.py').write_text('')
    (bridge / 'bridge_log_reader.py').write_text(
        'class BridgeCursor:\n def __init__(self, **kw): self.__dict__.update(kw)\n')
    binding = dict(source_mode='card-snapshot-v1', pointer_sha256='a'*64,
                   source_anchor_sha256='b'*64, encoder_sha256='c'*64,
                   policy_sha256='d'*64, roster_sha256='e'*64,
                   card_decision_sha256='9'*64, card_policy_sha256='f'*64,
                   descriptor_sha256='1'*64, memory_sha256='2'*64, projection_sha256='3'*64)
    output = dict(schema='wd.q4-card-snapshot-query.v1', status='ANSWER', binding=binding,
                  publisher_authentication='UNKNOWN', source_filter_attestation='UNKNOWN',
                  freshness='UNKNOWN', same_user_aba='UNKNOWN', completeness='UNKNOWN',
                  results=[dict(stored_text='synthetic', ref_sha256='5'*64, doc_id_sha256='6'*64,
                                ref_kind='sha256_of_projected_metadata', canonical_event=False)])
    (tools / 'wd_q4_public_outer.py').write_text(
        'import json,sys,types\n'
        'def bootstrap_public_imports(**kwargs):\n'
        ' def run_staged_card(config,stream,stdout,**pins):\n'
        "  assert stream.read()==b'query-secret'\n"
        "  assert pins['pointer_bytes']==b'pointer-secret' and pins['card_policy_bytes']==b'card-policy-secret'\n"
        "  assert pins['card_decision_bytes']==b'decision-secret' and pins['end_cursor'].offset==19\n"
        "  assert pins['end_cursor'].file_identity=='synthetic-id' and pins['end_cursor'].generation=='g1'\n"
        "  assert all('secret' not in item for item in sys.argv)\n"
        '  stdout.write(json.dumps(' + repr(output) + '));stdout.flush();return 0\n'
        " return {'status':'MODULES_IMPORTED','child':types.SimpleNamespace(run_staged_card=run_staged_card)}\n")
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    helper = ROOT / 'ops/windows/reboot/WdQ4ProcessJob.ps1'
    script = f"""
$ErrorActionPreference='Stop'
. '{quote(WRAPPER)}'
$h=[IO.File]::ReadAllBytes('{quote(helper)}');$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash($h)).Replace('-','');$sha.Dispose()
$base=@{{CodeRoot='{quote(root)}';JobHelperBytes=$h;JobHelperSha256=$hash;
 Config=@{{interpreter='{quote(sys.executable)}';timeout=10;scratch_root='{quote(scratch)}';verifier_sha256=('7'*64);descriptor_sha256=('1'*64);runtime_root='C:\\r';runtime_manifest='C:\\m';runtime_manifest_sha256=('8'*64);runtime_generation=('9'*40);runtime_revision=('8'*40)}};
 PublicPins=@{{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}}}
$doc=@{{pointer_sha256=('a'*64);source_anchor_sha256=('b'*64);encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);public_policy_sha256=('0'*64);card_decision_sha256=('9'*64);card_policy_sha256=('f'*64);source_path='C:\\source';generation_path='C:\\generation';end_cursor=@{{offset=19;file_identity='synthetic-id';generation='g1'}}}}
$plan=@{{Base=$base;Document=$doc;Root='C:\\public';Staged=$true;Inputs=@{{policy=[Text.Encoding]::UTF8.GetBytes('policy-secret');roster=[Text.Encoding]::UTF8.GetBytes('roster-secret');public_policy=[Text.Encoding]::UTF8.GetBytes('public-secret');card_decision=[Text.Encoding]::UTF8.GetBytes('decision-secret');card_policy=[Text.Encoding]::UTF8.GetBytes('card-policy-secret');pointer=[Text.Encoding]::UTF8.GetBytes('pointer-secret')}}}}
Invoke-WdQ4PublicTransport -Plan $plan -Query ([Text.Encoding]::UTF8.GetBytes('query-secret')) | ConvertTo-Json -Depth 8 -Compress
"""
    assert ps(script)['status'] == 'ANSWER'


@windows
@pytest.mark.parametrize('fault', ['none', 'decision', 'policy', 'false_freshness', 'extra_result'])
def test_staged_result_requires_pins_unknowns_and_exact_result_shape(fault):
    binding = dict(source_mode='card-snapshot-v1', pointer_sha256='a'*64,
                   source_anchor_sha256='b'*64, encoder_sha256='c'*64,
                   policy_sha256='d'*64, roster_sha256='e'*64,
                   card_decision_sha256='9'*64, card_policy_sha256='f'*64,
                   descriptor_sha256='1'*64, memory_sha256='2'*64, projection_sha256='3'*64)
    value = dict(schema='wd.q4-card-snapshot-query.v1', status='ANSWER', binding=binding,
                 publisher_authentication='UNKNOWN', source_filter_attestation='UNKNOWN',
                 freshness='UNKNOWN', same_user_aba='UNKNOWN', completeness='UNKNOWN',
                 results=[dict(stored_text='synthetic', ref_sha256='5'*64, doc_id_sha256='6'*64,
                               ref_kind='sha256_of_projected_metadata', canonical_event=False)])
    if fault == 'decision': binding['card_decision_sha256'] = '8'*64
    if fault == 'policy': binding['card_policy_sha256'] = '8'*64
    if fault == 'false_freshness': value['freshness'] = 'VERIFIED'
    if fault == 'extra_result': value['results'][0]['untrusted'] = True
    literal = json.dumps(value, separators=(',', ':')).replace("'", "''")
    script = (f". '{quote(WRAPPER)}'\n$v=ConvertFrom-WdQ4StrictJson -Text '{literal}'\n"
              "$p=@{pointer_sha256=('a'*64);source_anchor_sha256=('b'*64);encoder_sha256=('c'*64);"
              "policy_sha256=('d'*64);roster_sha256=('e'*64);descriptor_sha256=('1'*64);"
              "card_decision_sha256=('9'*64);card_policy_sha256=('f'*64)}\n"
              "try {ConvertFrom-WdQ4StagedCardResult -Result $v -Expected $p | ConvertTo-Json -Depth 8 -Compress} "
              "catch {'{\"status\":\"REFUSED\"}'}")
    assert ps(script)['status'] == ('ANSWER' if fault == 'none' else 'REFUSED')
