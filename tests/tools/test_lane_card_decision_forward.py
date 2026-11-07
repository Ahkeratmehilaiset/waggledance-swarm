"""Card decision lane forwarding (lead-mind-card-lane-forward-fable-20261107-v1). Synthetic fixtures only.

Continues C1 (4512244c) from the lane side: an optional public wrapper plan v2 (``wd.q4-public-wrapper-plan.v2``)
pins ``card_decision_path`` and ``card_decision_sha256``. ``wd_q4_lane_link`` reads that pin as a plan pin and
requires it in the reply binding; the lane-side caller ``wd_q4_lane_query`` takes it in ``expected_binding``, must
see the same pin in the plan and in the reply, validates a v7 card projection instead of the v6 one, and accepts and
outputs only the head-masked card text (``head:unknown``) re-derived from its own pinned projection. The public wrapper
``Invoke-WdQ4Query.ps1`` reads the decision bytes under the plan pin, frames them to the fixed public outer and passes
them to C1 ``run_public``; its result check requires the pin exactly when the plan carries it. A v1 plan and an
expected binding without the pin are the v6 path, unchanged; any partial or different pin refuses.

Not provided here: the session config, the lane caller adapter and CLI still use the closed v6 binding key set and
refuse a card answer (C4); the checkpoint still validates v6 only (C2), so the in-process end-to-end test substitutes
the checkpoint view (as in C1) and replaces the Windows PowerShell wrapper process by an in-process stand-in that
does what Get-WdQ4PublicPlan and Invoke-WdQ4PublicTransport do (the wrapper itself is tested below with Windows
PowerShell); the lane does not verify the decision itself, the child and the resolver do; nothing is enabled.
"""
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import wd_q4_lane_link as link
from tools import wd_q4_lane_query as lane_query
from tools import wd_q4_public_child as child

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / 'ops/windows/reboot/Invoke-WdQ4Query.ps1'
WINDOWS_POWERSHELL = shutil.which('powershell.exe')
_spec = importlib.util.spec_from_file_location('card_c1_fixture', ROOT / 'tests/tools/test_public_card_decision_forward.py')
c1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(c1)
HEX40 = re.compile(r'[0-9a-f]{40}')
PLAN_PINS = ('pointer_sha256', 'source_anchor_sha256', 'encoder_sha256', 'policy_sha256', 'roster_sha256',
             'public_policy_sha256')
WHEN = datetime(2026, 11, 2, tzinfo=timezone.utc)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def plan_document(**changes):
    document = {'schema': 'wd.q4-public-wrapper-plan.v1', 'config': {'descriptor_sha256': 'a' * 64},
                'root': 'synthetic-root', 'policy_path': 'p', 'roster_path': 'r', 'public_policy_path': 'q',
                'pointer_sha256': '1' * 64, 'source_anchor_sha256': '2' * 64, 'encoder_sha256': '3' * 64,
                'policy_sha256': '4' * 64, 'roster_sha256': '5' * 64, 'public_policy_sha256': '6' * 64}
    document.update(changes)
    return {key: value for key, value in document.items() if value is not None}


def write_plan(tmp_path, document, name='plan.json'):
    raw = json.dumps(document, sort_keys=True).encode()
    path = tmp_path / name
    path.write_bytes(raw)
    return str(path), sha(raw)


# --- lane_link: the plan pin and the reply binding --------------------------------------------------------------------

def test_a_v2_plan_carries_the_card_decision_pin_and_a_v1_plan_is_unchanged(tmp_path):
    _, _, pins = link._public_plan(*write_plan(tmp_path, plan_document(), 'v1.json'))
    assert 'card_decision_sha256' not in pins and len(pins) == 7
    v2 = plan_document(schema='wd.q4-public-wrapper-plan.v2', card_decision_path='d', card_decision_sha256='7' * 64)
    _, _, pins = link._public_plan(*write_plan(tmp_path, v2, 'v2.json'))
    assert pins['card_decision_sha256'] == '7' * 64 and len(pins) == 8


@pytest.mark.parametrize('changes', [
    {'card_decision_path': 'd', 'card_decision_sha256': '7' * 64},
    {'schema': 'wd.q4-public-wrapper-plan.v2'},
    {'schema': 'wd.q4-public-wrapper-plan.v2', 'card_decision_path': 'd'},
    {'schema': 'wd.q4-public-wrapper-plan.v2', 'card_decision_sha256': '7' * 64},
    {'schema': 'wd.q4-public-wrapper-plan.v2', 'card_decision_path': '', 'card_decision_sha256': '7' * 64},
    {'schema': 'wd.q4-public-wrapper-plan.v2', 'card_decision_path': 'd', 'card_decision_sha256': '7' * 63},
    {'schema': 'wd.q4-public-wrapper-plan.v2', 'card_decision_path': 'd', 'card_decision_sha256': 'A' * 64},
    {'schema': 'wd.q4-public-wrapper-plan.v2', 'card_decision_path': ['d'], 'card_decision_sha256': '7' * 64},
    {'schema': 'wd.q4-public-wrapper-plan.v3', 'card_decision_path': 'd', 'card_decision_sha256': '7' * 64},
], ids=lambda c: '-'.join(sorted(c)) + ('-' + str(c.get('schema', ''))[-2:]))
def test_a_partial_or_mixed_plan_is_refused(tmp_path, changes):
    with pytest.raises(ValueError):
        link._public_plan(*write_plan(tmp_path, plan_document(**changes)))


def reply_binding(**extra):
    binding = {key: str(i) * 64 for i, key in enumerate(PLAN_PINS, 1)}
    binding.update(descriptor_sha256='a' * 64, memory_sha256='b' * 64, projection_sha256='c' * 64,
                   receipt_sha256='d' * 64, **extra)
    return binding


def public_reply(binding):
    return json.dumps({'schema': 'wd.q4-public-query.v2', 'status': 'ABSTAIN', 'binding': binding, 'results': [],
                       'freshness': 'UNKNOWN', 'publisher_authentication': 'UNKNOWN',
                       'source_filter_attestation': 'UNKNOWN', 'same_user_aba': 'UNKNOWN'}).encode()


def test_the_reply_binding_carries_the_card_pin_exactly_when_the_plan_does():
    v6_pins = {key: value for key, value in reply_binding().items() if key in PLAN_PINS or key == 'descriptor_sha256'}
    card_pins = dict(v6_pins, card_decision_sha256='7' * 64)
    assert link._public_result(public_reply(reply_binding()), 0, v6_pins)['status'] == 'ABSTAIN'
    assert link._public_result(public_reply(reply_binding(card_decision_sha256='7' * 64)), 0, card_pins)
    for raw, pins in ((public_reply(reply_binding(card_decision_sha256='7' * 64)), v6_pins),
                      (public_reply(reply_binding()), card_pins),
                      (public_reply(reply_binding(card_decision_sha256='8' * 64)), card_pins)):
        with pytest.raises(ValueError):
            link._public_result(raw, 0, pins)


# --- the lane-side caller end to end (in process) ---------------------------------------------------------------------

def lane_card(tmp_path, monkeypatch, *, plan_card=True):
    """C1's synthetic card generation (checkpoint view SUBSTITUTED) behind a v2 plan, a bundle binding and a wrapper
    stand-in transport. The transport does what the wrapper does: read the pinned plan, its pinned inputs and the
    pinned decision, then call the public child once with the framed query."""
    state = c1.card_public(tmp_path, monkeypatch)
    args, decision = state['args'], state['decision']
    files = tmp_path / 'lane'
    files.mkdir()
    paths = {}
    for name, data in (('policy', args['policy_bytes']), ('roster', args['roster_bytes']),
                       ('public_policy', args['public_policy_bytes']), ('card_decision', decision['card_decision_bytes'])):
        (files / (name + '.bin')).write_bytes(data)
        paths[name] = str(files / (name + '.bin'))
    document = {'schema': 'wd.q4-public-wrapper-plan.v2' if plan_card else 'wd.q4-public-wrapper-plan.v1',
                'config': {'descriptor_sha256': state['config'].descriptor_sha256}, 'root': args['root'],
                'policy_path': paths['policy'], 'roster_path': paths['roster'],
                'public_policy_path': paths['public_policy'],
                'pointer_sha256': args['expected_pointer_sha256'], 'encoder_sha256': args['expected_encoder_sha256'],
                'source_anchor_sha256': args['expected_source_sha256'], 'policy_sha256': args['policy_sha256'],
                'roster_sha256': args['roster_sha256'], 'public_policy_sha256': args['public_policy_sha256']}
    if plan_card:
        document.update(card_decision_path=paths['card_decision'], card_decision_sha256=decision['card_decision_sha256'])
    plan_path, plan_sha = write_plan(files, document)
    bundle = tmp_path / ('e' * 40)
    bundle.mkdir()
    wrapper = bundle / 'Invoke-WdQ4Query.ps1'
    wrapper.write_bytes(b'synthetic wrapper stand-in; the transport below runs in process')
    manifest = json.dumps({'schema_version': 1, 'source_commit': 'e' * 40,
                           'files': {'Invoke-WdQ4Query.ps1': sha(wrapper.read_bytes())}}, sort_keys=True).encode()
    (bundle / 'deployment-manifest.json').write_bytes(manifest)
    calls = []

    def transport(*, wrapper, arguments, stdin_bytes):
        calls.append(arguments)
        plan = json.loads(Path(arguments[arguments.index('-PublicPlanPath') + 1]).read_bytes())
        extra = {}
        if 'card_decision_path' in plan:
            raw = Path(plan['card_decision_path']).read_bytes()
            assert sha(raw) == plan['card_decision_sha256']
            extra = {'card_decision_bytes': raw, 'card_decision_sha256': plan['card_decision_sha256']}
        out = io.StringIO()
        code = child.run_public(state['config'], io.BytesIO(stdin_bytes), out, root=plan['root'],
                                expected_pointer_sha256=plan['pointer_sha256'],
                                expected_source_sha256=plan['source_anchor_sha256'],
                                expected_encoder_sha256=plan['encoder_sha256'],
                                policy_bytes=Path(plan['policy_path']).read_bytes(), policy_sha256=plan['policy_sha256'],
                                roster_bytes=Path(plan['roster_path']).read_bytes(), roster_sha256=plan['roster_sha256'],
                                public_policy_bytes=Path(plan['public_policy_path']).read_bytes(),
                                public_policy_sha256=plan['public_policy_sha256'], enabled=True, **extra)
        return out.getvalue().encode('utf-8'), code

    projection_bytes = (Path(state['config'].memory) / 'projection.json').read_bytes()
    binding = {key: document[key] for key in PLAN_PINS}
    binding.update(descriptor_sha256=state['config'].descriptor_sha256, projection_sha256=sha(projection_bytes),
                   card_decision_sha256=decision['card_decision_sha256'])
    call = {'lane': 'fable-5', 'situation': c1.SITUATION, 'time': WHEN, 'bundle_root': bundle,
            'expected_commit': 'e' * 40, 'expected_manifest_sha256': sha(manifest),
            'expected_wrapper_sha256': sha(wrapper.read_bytes()), 'public_plan_path': plan_path,
            'expected_public_plan_sha256': plan_sha, 'expected_binding': binding,
            'projection_bytes': projection_bytes, 'policy_bytes': args['policy_bytes'],
            'roster_bytes': args['roster_bytes'], 'transport': transport, 'enabled': True}
    return {'state': state, 'call': call, 'calls': calls, 'document': document}


@pytest.fixture
def lane(tmp_path, monkeypatch):
    return lane_card(tmp_path, monkeypatch)


def refused(reason, call):
    with pytest.raises(lane_query.LaneQueryRefused, match='^' + reason + '$'):
        lane_query.query_public(**call)


def test_the_lane_caller_answers_masked_cards_through_the_v2_plan_and_c1(lane):
    answer = lane_query.query_public(**lane['call'])
    assert answer['status'] == 'ANSWER' and answer['lane'] == 'fable-5' and answer['results']
    assert answer['binding']['card_decision_sha256'] == lane['call']['expected_binding']['card_decision_sha256']
    assert set(answer['binding']) == set(lane['call']['expected_binding'])
    texts = [item['stored_text'] for item in answer['results']]
    assert any(' head:unknown ' in text for text in texts) and not any(HEX40.search(text) for text in texts)
    records = {record['stored_text'] for record in lane['state']['route']['projection']['records']}
    assert all(text in records or ' head:unknown ' in text for text in texts)
    assert len(lane['calls']) == 1 and lane['state']['route']['calls'] == [True]


def test_an_expected_card_pin_with_a_v1_plan_is_refused_before_the_transport(tmp_path, monkeypatch):
    setup = lane_card(tmp_path, monkeypatch, plan_card=False)
    refused('binding_refused', setup['call'])
    assert setup['calls'] == []


def test_a_v2_plan_without_the_expected_card_pin_is_refused_before_the_transport(lane, monkeypatch):
    del lane['call']['expected_binding']['card_decision_sha256']
    refused('projection_refused', lane['call'])  # v6 mode first refuses the card projection itself
    # the plan rule on its own: let the v6 projection layer pass, the v2 plan still needs the expected pin
    monkeypatch.setattr(lane_query, '_records', lambda data, pin, tasks, roster: lane_query._card_records(data, pin, tasks))
    refused('binding_refused', lane['call'])
    assert lane['calls'] == []


def test_a_different_expected_card_pin_is_refused_before_the_transport(lane):
    lane['call']['expected_binding']['card_decision_sha256'] = '7' * 64
    refused('binding_refused', lane['call'])
    assert lane['calls'] == []


@pytest.mark.parametrize('value', ['A' * 64, '7' * 63, None, 7])
def test_a_malformed_expected_card_pin_is_invalid_input(lane, value):
    lane['call']['expected_binding']['card_decision_sha256'] = value
    refused('invalid_input', lane['call'])
    assert lane['calls'] == []


def test_card_mode_needs_the_card_projection_and_v6_mode_refuses_it(lane, tmp_path):
    spec = importlib.util.spec_from_file_location('lane_v6_fixture', ROOT / 'tests/tools/test_wd_q4_lane_query.py')
    v6 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v6)
    _, v6_bytes = v6._projection()
    call = dict(lane['call'], projection_bytes=v6_bytes,
                expected_binding=dict(lane['call']['expected_binding'], projection_sha256=sha(v6_bytes)))
    refused('projection_refused', call)
    v6_call = dict(lane['call'], expected_binding={key: value for key, value in lane['call']['expected_binding'].items()
                                                   if key != 'card_decision_sha256'})
    refused('projection_refused', v6_call)
    assert lane['calls'] == []


@pytest.mark.parametrize('change', ['raw head text', 'binding without pin', 'other pin in binding'])
def test_a_reply_that_is_not_the_masked_card_answer_for_this_pin_is_refused(lane, change):
    real = lane['call']['transport']

    def tamper(**kwargs):
        raw, code = real(**kwargs)
        value = json.loads(raw)
        if change == 'raw head text':
            projection = lane['state']['route']['projection']
            heads = {record['stored_text'].replace(' head:' + projection['sources'][
                record['provenance']['source_event_ref']]['payload']['public_knowledge']['head'] + ' ', ' head:unknown ', 1):
                record['stored_text'] for record in projection['records']
                if 'head' in projection['sources'][record['provenance']['source_event_ref']]['payload']['public_knowledge']}
            for item in value['results']:
                item['stored_text'] = heads.get(item['stored_text'], item['stored_text'])
        elif change == 'binding without pin':
            del value['binding']['card_decision_sha256']
        else:
            value['binding']['card_decision_sha256'] = '7' * 64
        return json.dumps(value).encode(), code

    lane['call']['transport'] = tamper
    reason = 'result_refused' if change == 'raw head text' else 'transport_refused'
    refused(reason, lane['call'])


# --- Invoke-WdQ4Query.ps1 public wrapper (Windows PowerShell 5.1) -------------------------------------------------------

windows = pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason='Windows PowerShell executable is unavailable')


def ps(script, timeout=45):
    run = subprocess.run([WINDOWS_POWERSHELL, '-NoProfile', '-NonInteractive', '-Command', script],
                         capture_output=True, text=True, timeout=timeout)
    assert run.returncode == 0, (run.stdout, run.stderr)
    return json.loads(run.stdout)


def quote(value):
    return str(value).replace("'", "''")


def wrapper_plan(tmp_path, *, schema='wd.q4-public-wrapper-plan.v2', card=True, decision=b'{"synthetic":1}'):
    root = tmp_path / 'public'
    root.mkdir(exist_ok=True)
    document = {'schema': schema, 'root': str(root), 'config': {}, 'pointer_sha256': 'a' * 64,
                'source_anchor_sha256': 'b' * 64, 'encoder_sha256': 'c' * 64}
    for name in ('policy', 'roster', 'public_policy'):
        path = tmp_path / (name + '.json')
        path.write_bytes(b'{}')
        document[name + '_path'] = str(path)
        document[name + '_sha256'] = sha(b'{}')
    if card:
        path = tmp_path / 'decision.json'
        path.write_bytes(decision)
        document.update(card_decision_path=str(path), card_decision_sha256=sha(decision))
    raw = json.dumps(document, separators=(',', ':')).encode()
    path = tmp_path / 'plan.json'
    path.write_bytes(raw)
    return path, sha(raw), document


BASE = ("$base=@{Enabled=$true;PublicPins=@{'tools/wd_q4_public_outer.py'=('1'*64);"
        "'tools/wd_q4_public_child.py'=('2'*64);'tools/wd_q4_public_query.py'=('3'*64)}}\n")


@windows
def test_the_wrapper_reads_the_pinned_decision_of_a_v2_plan_and_refuses_partial_or_tampered_plans(tmp_path):
    path, pin, document = wrapper_plan(tmp_path)
    script = (f". '{quote(WRAPPER)}'\n" + BASE +
              f"$good=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}'\n"
              f"[IO.File]::WriteAllText('{quote(document['card_decision_path'])}','{{\"changed\":1}}')\n"
              f"$tampered=try {{$null=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}';'accepted'}}catch{{'refused'}}\n"
              "@{good=$good.Inputs.card_decision.Length;tampered=$tampered} | ConvertTo-Json -Compress")
    assert ps(script) == {'good': len(b'{"synthetic":1}'), 'tampered': 'refused'}
    statuses = {}
    for name, (schema, card) in {'v1': ('wd.q4-public-wrapper-plan.v1', False),
                                 'v1 with card': ('wd.q4-public-wrapper-plan.v1', True),
                                 'v2 without card': ('wd.q4-public-wrapper-plan.v2', False)}.items():
        folder = tmp_path / name.replace(' ', '-')
        folder.mkdir()
        plan_path, plan_pin, _ = wrapper_plan(folder, schema=schema, card=card)
        script = (f". '{quote(WRAPPER)}'\n" + BASE +
                  f"try {{$p=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(plan_path)}' -ExpectedSha256 '{plan_pin}';"
                  "@{status='accepted';card=$p.Inputs.ContainsKey('card_decision')} | ConvertTo-Json -Compress}"
                  "catch{@{status='refused'} | ConvertTo-Json -Compress}")
        statuses[name] = ps(script)
    assert statuses == {'v1': {'status': 'accepted', 'card': False}, 'v1 with card': {'status': 'refused'},
                        'v2 without card': {'status': 'refused'}}


def card_envelope(**binding_changes):
    binding = dict(pointer_sha256='a' * 64, source_anchor_sha256='b' * 64, encoder_sha256='c' * 64,
                   policy_sha256='d' * 64, roster_sha256='e' * 64, public_policy_sha256='f' * 64,
                   descriptor_sha256='1' * 64, memory_sha256='2' * 64, projection_sha256='3' * 64,
                   receipt_sha256='4' * 64, card_decision_sha256='9' * 64)
    binding.update(binding_changes)
    binding = {key: value for key, value in binding.items() if value is not None}
    return dict(schema='wd.q4-public-query.v2', status='ANSWER', binding=binding,
                publisher_authentication='UNKNOWN', source_filter_attestation='UNKNOWN', freshness='UNKNOWN',
                same_user_aba='UNKNOWN', results=[dict(stored_text='synthetic card line', ref_sha256='5' * 64,
                                                       doc_id_sha256='6' * 64,
                                                       ref_kind='sha256_of_projected_metadata', canonical_event=False)])


@windows
@pytest.mark.parametrize('case,expected_card,changes,status', [
    ('card bound', True, {}, 'ANSWER'),
    ('card missing', True, {'card_decision_sha256': None}, 'REFUSED'),
    ('card other', True, {'card_decision_sha256': '8' * 64}, 'REFUSED'),
    ('card unexpected', False, {}, 'REFUSED'),
    ('v6', False, {'card_decision_sha256': None}, 'ANSWER'),
])
def test_the_wrapper_result_check_requires_the_card_pin_exactly_when_the_plan_carries_it(case, expected_card,
                                                                                          changes, status):
    literal = json.dumps(card_envelope(**changes), separators=(',', ':')).replace("'", "''")
    card = ";card_decision_sha256=('9'*64)" if expected_card else ''
    script = (f". '{quote(WRAPPER)}'\n$v=ConvertFrom-WdQ4StrictJson -Text '{literal}'\n"
              "$p=@{pointer_sha256=('a'*64);source_anchor_sha256=('b'*64);encoder_sha256=('c'*64);"
              "policy_sha256=('d'*64);roster_sha256=('e'*64);public_policy_sha256=('f'*64);"
              f"descriptor_sha256=('1'*64){card}}}\n"
              "try {ConvertFrom-WdQ4PublicResult -Result $v -Expected $p | ConvertTo-Json -Depth 8 -Compress} "
              "catch {'{\"status\":\"REFUSED\"}'}")
    out = ps(script)
    assert out['status'] == status
    if status == 'ANSWER' and expected_card:
        assert out['binding']['card_decision_sha256'] == '9' * 64


@windows
@pytest.mark.parametrize('card', [True, False])
def test_the_real_process_job_frames_the_decision_to_run_public_only_for_a_card_plan(tmp_path, card):
    """Synthetic outer/child (as test_wd_q4_public_wrapper_transport.py); the real Windows process and stdin frame."""
    root = tmp_path / 'code'
    tools = root / 'tools'
    tools.mkdir(parents=True)
    (tools / '__init__.py').write_text('', encoding='utf-8')
    (tools / 'wd_q4_agent_query.py').write_text(
        'class VerifiedQ4Config:\n def __init__(self,**kwargs): self.kwargs=kwargs\n', encoding='utf-8')
    output = card_envelope() if card else card_envelope(card_decision_sha256=None)
    expected_extra = ("{'card_decision_bytes': b'decision-bytes', 'card_decision_sha256': '" + '9' * 64 + "'}"
                      if card else '{}')
    (tools / 'wd_q4_public_outer.py').write_text(
        'import json,sys,types\n'
        'def bootstrap_public_imports(**kwargs):\n'
        ' def run_public(config,stream,stdout,**pins):\n'
        "  assert stream.read()==b'query-secret'\n"
        "  assert pins['public_policy_bytes']==b'public-secret'\n"
        "  extra={k:v for k,v in pins.items() if k.startswith('card_')}\n"
        '  assert extra==' + expected_extra + ', extra\n'
        "  assert all('secret' not in item and 'decision-bytes' not in item for item in sys.argv)\n"
        '  stdout.write(json.dumps(' + repr(output) + '));stdout.flush();return 0\n'
        " return {'status':'MODULES_IMPORTED','child':types.SimpleNamespace(run_public=run_public)}\n",
        encoding='utf-8')
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    helper = ROOT / 'ops/windows/reboot/WdQ4ProcessJob.ps1'
    doc_card = ";card_decision_sha256=('9'*64)" if card else ''
    input_card = ";card_decision=[Text.Encoding]::UTF8.GetBytes('decision-bytes')" if card else ''
    script = f"""
$ErrorActionPreference='Stop'
. '{quote(WRAPPER)}'
$h=[IO.File]::ReadAllBytes('{quote(helper)}');$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash($h)).Replace('-','');$sha.Dispose()
$base=@{{CodeRoot='{quote(root)}';JobHelperBytes=$h;JobHelperSha256=$hash;
 Config=@{{interpreter='{quote(sys.executable)}';timeout=10;scratch_root='{quote(scratch)}';verifier_sha256=('7'*64);descriptor_sha256=('1'*64);runtime_root='C:\\r';runtime_manifest='C:\\m';runtime_manifest_sha256=('8'*64);runtime_generation=('9'*40);runtime_revision=('8'*40)}};
 PublicPins=@{{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}}}
$doc=@{{pointer_sha256=('a'*64);source_anchor_sha256=('b'*64);encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);public_policy_sha256=('f'*64){doc_card}}}
$plan=@{{Base=$base;Document=$doc;Root='C:\\public';Inputs=@{{policy=[Text.Encoding]::UTF8.GetBytes('policy-secret');roster=[Text.Encoding]::UTF8.GetBytes('roster-secret');public_policy=[Text.Encoding]::UTF8.GetBytes('public-secret'){input_card}}}}}
$result=Invoke-WdQ4PublicTransport -Plan $plan -Query ([Text.Encoding]::UTF8.GetBytes('query-secret'))
$result | ConvertTo-Json -Depth 8 -Compress
"""
    out = ps(script)
    assert out['status'] == 'ANSWER'
    assert ('card_decision_sha256' in out['binding']) is card


# --- each lane check on its own (not only through the hop behind it) --------------------------------------------------

def card_inputs(lane):
    call = lane['call']
    tasks, _ = lane_query.adapter.strict_pins(call['policy_bytes'], call['expected_binding']['policy_sha256'],
                                              call['roster_bytes'], call['expected_binding']['roster_sha256'])
    return call['projection_bytes'], call['expected_binding']['projection_sha256'], tasks


def test_the_card_records_need_the_pin_the_policy_tasks_and_a_re_derivable_projection(lane):
    data, pin, tasks = card_inputs(lane)
    records = lane_query._card_records(data, pin, tasks)
    assert records and all(' head:unknown ' in r['stored_text'] or 'head:' not in r['stored_text']
                           for r in records.values())
    with pytest.raises(ValueError):
        lane_query._card_records(data, '7' * 64, tasks)
    with pytest.raises(ValueError):
        lane_query._card_records(data, pin, frozenset({'another-task'}))
    projection = json.loads(data)
    projection['records'][0]['summary'] = projection['records'][0]['summary'] + ' tag:x'
    tampered = json.dumps(projection, sort_keys=True, indent=1).encode('utf-8')
    with pytest.raises(ValueError):
        lane_query._card_records(tampered, sha(tampered), tasks)


def test_a_wrong_projection_pin_in_card_mode_is_refused_before_the_transport(lane):
    lane['call']['expected_binding']['projection_sha256'] = '7' * 64
    refused('projection_refused', lane['call'])
    assert lane['calls'] == []


@pytest.mark.parametrize('change', ['dropped', 'other', 'added'])
def test_the_answer_needs_exactly_the_expected_card_pin_in_the_reply_binding(lane, change):
    data, pin, tasks = card_inputs(lane)
    records = lane_query._card_records(data, pin, tasks)
    expected = dict(lane['call']['expected_binding'])
    binding = dict(expected, memory_sha256='b' * 64, receipt_sha256='c' * 64)
    if change == 'dropped':
        del binding['card_decision_sha256']
    elif change == 'other':
        binding['card_decision_sha256'] = '7' * 64
    else:
        del expected['card_decision_sha256']
    value = {'status': 'ABSTAIN', 'binding': binding, 'results': []}
    assert lane_query._answer('fable-5', value, expected, records, '2026-11-02T00:00:00Z') == (None, 'binding_refused')


@windows
def test_the_wrapper_refuses_a_v2_plan_with_an_extra_key(tmp_path):
    path, _, document = wrapper_plan(tmp_path)
    document['extra'] = 'x'
    raw = json.dumps(document, separators=(',', ':')).encode()
    path.write_bytes(raw)
    script = (f". '{quote(WRAPPER)}'\n" + BASE +
              f"try {{$null=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{sha(raw)}';"
              "@{status='accepted'} | ConvertTo-Json -Compress} catch {@{status='refused'} | ConvertTo-Json -Compress}")
    assert ps(script) == {'status': 'refused'}
