"""Public-route tests use fabricated local cards and local Git, never a live source."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import wd_q4_card_generation as producer
from tools import wd_q4_public_git_heads as head_reader

_spec = importlib.util.spec_from_file_location('real_card_route_fixture', Path(__file__).with_name('test_shared_card_route.py'))
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)


@pytest.fixture
def real_fixture(tmp_path):
    git = shutil.which('git')
    if not git:
        pytest.skip('Git is required for synthetic local history')
    git = str(Path(git).resolve())
    repo = tmp_path / 'synthetic-git'
    repo.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
               GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    def run(*args):
        return subprocess.run([git, '-C', str(repo), *args], env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
    run('init', '-q')
    (repo / 'public.txt').write_text('synthetic', encoding='ascii')
    run('add', 'public.txt')
    run('commit', '-q', '-m', 'synthetic public commit')
    commit = run('rev-parse', 'HEAD')
    anchors = {'refs/heads/public-main': commit}
    observation = head_reader.observe(enabled=True, git_executable=git, repository=str(repo), anchors=anchors)
    event = rt.fx.card(rt.fx.LEAD, 0, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'], head=commit)
    event['payload']['public_knowledge']['tags'].remove('synthetic')
    vocab = rt.fx.raw({'schema': 'wd.mind-public-vocabulary.v1',
                       'components': ['mind.genesis'], 'tags': ['genesis']})
    decision, _, source = rt.anchor(tmp_path / 'prefix', [event], decision='enable_real_card_route',
                                    synthetic=False, observed_heads=observation['heads'],
                                    vocabulary_sha256=rt.fx.sha(vocab),
                                    head_observation_sha256=observation['sha256'])
    out = tmp_path / 'out'
    out.mkdir()
    args = dict(enabled=True, decision_bytes=decision, decision_sha256=rt.fx.sha(decision),
        vocabulary_bytes=vocab, vocabulary_sha256=rt.fx.sha(vocab),
        path_allowlist_bytes=rt.fx.PATHS, path_allowlist_sha256=rt.fx.sha(rt.fx.PATHS),
        now_utc=rt.fx.NOW, output_root=out, generation_name='candidate', encode=rt.fx.encode,
        identity=rt.fx.IDENTITY, dimension=rt.fx.DIM, real_git_executable=git,
        real_git_repository=str(repo), real_approved_anchors=anchors, **source, **rt.pins())
    return args, out, event, observation


def test_real_route_is_off_without_explicit_enable():
    class Untouchable:
        def __getattribute__(self, name): raise AssertionError('OFF accessed input')
    assert producer.prepare_card_generation(decision_bytes=Untouchable()) == {'status': 'OFF'}


def test_non_synthetic_card_builds_only_with_real_pinned_observation(real_fixture):
    pytest.importorskip('faiss')
    args, out, event, observation = real_fixture
    result = producer.prepare_card_generation(**args)
    assert result['status'] == 'STAGED' and result['completeness'] == 'UNKNOWN'
    projection = json.loads((out / 'candidate' / 'projection.json').read_bytes())
    memory = json.loads((out / 'candidate' / 'memory.json').read_bytes())
    assert projection['synthetic'] is False and memory['synthetic'] is False
    assert rt.fx.event_id(event) in projection['sources']
    assert memory['card_route']['decision_sha256'] == args['decision_sha256']
    assert observation['public_origin_authentication'] == 'UNKNOWN'
    with pytest.raises(rt.scp.SharedCardRefused):
        rt.scp.validate_shared_card_projection(projection)


@pytest.mark.parametrize('fault', ['null_heads', 'wrong_observation', 'unapproved_anchors',
                                   'synthetic_flag', 'source_mutated', 'unobserved_head',
                                   'foreign_source_head', 'wrong_author'])
def test_bad_real_bindings_refuse_before_output(real_fixture, fault):
    args, out, event, _ = real_fixture
    args = dict(args)
    decision = json.loads(args['decision_bytes'])
    if fault == 'null_heads': decision['observed_heads'] = None
    if fault == 'wrong_observation': decision['head_observation_sha256'] = '0' * 64
    if fault == 'unapproved_anchors': args['real_approved_anchors'] = {'refs/heads/other': '0' * 40}
    if fault == 'synthetic_flag': decision['synthetic'] = True
    if fault == 'unobserved_head': decision['observed_heads'] = []
    if fault == 'source_mutated': Path(args['source_path']).write_bytes(b'changed synthetic source')
    if fault in ('foreign_source_head', 'wrong_author'):
        source = Path(args['source_path'])
        data = source.read_bytes()
        if fault == 'foreign_source_head':
            old = event['payload']['public_knowledge']['head'].encode('ascii')
            new = b'f' * 40
        else:
            old = event['agent_uuid'].encode('ascii')
            new = b'ffffffff-1111-4111-8111-111111111111'
        assert len(old) == len(new) and data.count(old) == 1
        data = data.replace(old, new)
        source.write_bytes(data)
        decision['source_prefix']['sha256'] = hashlib.sha256(data).hexdigest()
    args['decision_bytes'] = rt.canonical_decision(decision)
    args['decision_sha256'] = hashlib.sha256(args['decision_bytes']).hexdigest()
    with pytest.raises(producer.CardGenerationRefused):
        producer.prepare_card_generation(**args)
    assert list(out.iterdir()) == []


def test_real_card_cannot_enter_synthetic_route(real_fixture):
    args, out, _, _ = real_fixture
    decision = json.loads(args['decision_bytes'])
    decision['decision'] = 'enable_synthetic_card_route'
    decision['synthetic'] = True
    del decision['head_observation_sha256']
    args['decision_bytes'] = rt.canonical_decision(decision)
    args['decision_sha256'] = rt.fx.sha(args['decision_bytes'])
    with pytest.raises(producer.CardGenerationRefused): producer.prepare_card_generation(**args)
    assert list(out.iterdir()) == []


def test_original_ref_cannot_be_paired_with_different_valid_card(real_fixture):
    args, _, event, _ = real_fixture
    decision = json.loads(args['decision_bytes'])
    forged = copy.deepcopy(event)
    forged['payload']['public_knowledge']['outcome'] = 'partial'
    ref = rt.fx.event_id(event)
    normalized = rt.scp._normalize_one(forged)
    derived = rt.scp._derive([(ref, normalized)], args['vocabulary_bytes'], args['vocabulary_sha256'],
                             args['path_allowlist_bytes'], args['path_allowlist_sha256'], args['now_utc'])
    assert len(derived['records']) == 1
    projection = rt.scp._assemble(derived, {}, args['vocabulary_bytes'].decode('ascii'), args['vocabulary_sha256'],
                                  args['path_allowlist_bytes'].decode('ascii'), args['path_allowlist_sha256'],
                                  args['now_utc'], synthetic=False)
    rt.scp.validate_shared_card_projection(projection, _real_decision=decision)
    with pytest.raises(rt.scp.SharedCardRefused, match='source_unverified'):
        rt.scp.verify_shared_card_sources(projection, enabled=True, source_path=args['source_path'],
            generation_path=args['generation_path'], end_cursor=args['end_cursor'],
            expected_prefix_sha256=decision['source_prefix']['sha256'],
            nonreview_policy_bytes=args['policy_bytes'], nonreview_policy_sha256=args['policy_sha256'],
            card_policy_bytes=args['card_policy_bytes'], card_policy_sha256=decision['card_policy_sha256'],
            roster_bytes=args['roster_bytes'], roster_sha256=args['roster_sha256'], _real_decision=decision)


def test_real_prefix_preserves_supersession_and_original_source_refs(real_fixture):
    pytest.importorskip('faiss')
    from waggledance.core import bridge_log_reader
    args, out, event, _ = real_fixture
    newer = rt.fx.card(rt.fx.LEAD, 20, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'],
                       head=event['payload']['public_knowledge']['head'], supersedes=rt.fx.event_id(event))
    newer['payload']['public_knowledge']['tags'].remove('synthetic')
    data = rt.fx.raw(event) + b'\n' + rt.fx.raw(newer) + b'\n'
    Path(args['source_path']).write_bytes(data)
    cursor = bridge_log_reader.read_bridge_log(args['source_path'], generation_path=args['generation_path']).candidate_cursor
    args['end_cursor'] = cursor
    decision = json.loads(args['decision_bytes'])
    decision['source_prefix'] = {'sha256': hashlib.sha256(data).hexdigest(), 'offset': cursor.offset,
                                 'generation': cursor.generation}
    args['decision_bytes'] = rt.canonical_decision(decision)
    args['decision_sha256'] = rt.fx.sha(args['decision_bytes'])
    result = producer.prepare_card_generation(**args)
    assert result['status'] == 'STAGED'
    projection = json.loads((out / 'candidate' / 'projection.json').read_bytes())
    assert set(projection['sources']) == {rt.fx.event_id(event), rt.fx.event_id(newer)}
    assert projection['superseded'] == [rt.fx.event_id(event)]
    assert projection['records'][0]['provenance']['source_event_ref'] == rt.fx.event_id(newer)
