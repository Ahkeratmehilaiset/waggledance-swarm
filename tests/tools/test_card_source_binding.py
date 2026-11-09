"""Card route source binding (lead-mind-card-source-binding-fable-20261107-v1). Synthetic fixtures only.

RW1: the route opens only for an explicit, pinned, verified decision: canonical bytes under a caller pin, an exact key
set, the loaded card module's own source hash, and the same non-review policy, roster, card policy, vocabulary and
path pins the route runs with. There is no module-level switch any more.
RW3: before cards are accepted, every stored card must match within the exact source prefix the decision anchors
(prefix sha256, end offset, generation), with its author uuid equal to the pinned roster uuid; the manifest binds
that evidence and the resolver re-checks it against the decision and the roster.
RW2: a card head is accepted only when it is in the decision's observed head set; with no observed set the head is
UNKNOWN and the route answers ``head:unknown`` instead of the claimed hex.
What is not proven: who issued the decision (it is caller-pinned, not signed), that the anchored prefix is the live
log, freshness beyond the prefix, and that the observed head set came from a trusted git observation.
"""
import importlib.util
import json
import os
import re
from pathlib import Path, PurePosixPath

import pytest

from sandbox.mind.step2 import shared_card_projection as scp
from sandbox.mind.step2 import shared_result_resolver as resolver

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('card_route_fixture', ROOT / 'tests/tools/test_shared_card_route.py')
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)
fx = rt.fx
HEX40 = re.compile(r'head:[0-9a-f]{40}')


def verify(raw, sha, strict=None):
    strict = rt.pins() if strict is None else strict
    return scp.verify_route_decision(raw, sha, policy_sha256=strict['policy_sha256'],
                                     roster_sha256=strict['roster_sha256'])


# --- RW1: pinned verified decision -----------------------------------------------------------------------------------

def test_a_valid_decision_verifies_and_binds_the_loaded_card_module(tmp_path):
    raw, sha, _ = rt.anchor(tmp_path, fx.corpus())
    decision = verify(raw, sha)
    assert decision['card_module_sha256'] == scp.module_sha256() == fx.sha(rt.CARD_FILE.read_bytes())
    assert decision['decision'] == 'enable_synthetic_card_route' and decision['synthetic'] is True


@pytest.mark.parametrize('change', ['wrong_pin', 'non_canonical', 'extra_key', 'missing_key', 'schema', 'decision',
                                    'not_synthetic', 'projection_schema', 'scope', 'module_sha', 'policy_pin',
                                    'roster_pin', 'bad_prefix_sha', 'bad_offset', 'bool_offset', 'bad_generation',
                                    'unsorted_heads', 'duplicate_heads', 'bad_head', 'heads_not_list', 'non_ascii'])
def test_any_other_decision_is_refused(tmp_path, change):
    events = fx.corpus()
    overrides = {
        'extra_key': {'enabled': True}, 'schema': {'schema': 'wd.mind-card-route-decision.v2'},
        'decision': {'decision': 'enable'}, 'not_synthetic': {'synthetic': False},
        'projection_schema': {'projection_schema': resolver.PROJECTION_SCHEMA},
        'scope': {'scope': scp.SCOPE}, 'module_sha': {'card_module_sha256': '0' * 64},
        'policy_pin': {'nonreview_policy_sha256': '0' * 64}, 'roster_pin': {'roster_sha256': '0' * 64},
        'unsorted_heads': {'observed_heads': ['2' * 40, '1' * 40]},
        'duplicate_heads': {'observed_heads': ['1' * 40, '1' * 40]},
        'bad_head': {'observed_heads': ['1' * 39 + 'G']}, 'heads_not_list': {'observed_heads': '1' * 40},
    }.get(change, {})
    raw, sha, _ = rt.anchor(tmp_path, events, **overrides)
    if change == 'wrong_pin':
        sha = '0' * 64
    elif change in ('non_canonical', 'missing_key', 'bad_prefix_sha', 'bad_offset', 'bool_offset', 'bad_generation',
                    'non_ascii'):
        value = json.loads(raw)
        if change == 'missing_key':
            del value['observed_heads']
        elif change == 'bad_prefix_sha':
            value['source_prefix']['sha256'] = 'x' * 64
        elif change == 'bad_offset':
            value['source_prefix']['offset'] = 0
        elif change == 'bool_offset':
            value['source_prefix']['offset'] = True
        elif change == 'bad_generation':
            value['source_prefix']['generation'] = ''
        raw = (json.dumps(value, sort_keys=True, indent=1).encode() if change == 'non_canonical'
               else rt.canonical_decision(value) if change != 'non_ascii'
               else raw.replace(b'synthetic-g1', b'synthetic-g\xc3\xa4'))
        sha = fx.sha(raw)
    with pytest.raises(scp.SharedCardRefused, match='decision_invalid'):
        verify(raw, sha)


def test_a_decision_for_other_pins_than_the_route_runs_with_is_refused(tmp_path):
    raw, sha, _ = rt.anchor(tmp_path, fx.corpus())
    with pytest.raises(scp.SharedCardRefused, match='decision_invalid'):
        verify(raw, sha, rt.pins((rt.TASK, 'synthetic-task')))


def test_a_changed_card_module_invalidates_the_decision(tmp_path, monkeypatch):
    raw, sha, _ = rt.anchor(tmp_path, fx.corpus())
    monkeypatch.setattr(scp, 'module_sha256', lambda: '0' * 64)
    with pytest.raises(scp.SharedCardRefused, match='decision_invalid'):
        verify(raw, sha)


def test_the_child_and_the_resolver_refuse_a_decision_paired_with_a_v6_memory(monkeypatch, tmp_path):
    pytest.importorskip('faiss')
    spec = importlib.util.spec_from_file_location('resolved_child_fixture', ROOT / 'tests/tools/test_wd_q4_resolved_child.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data, result, record = module.fixture()
    raw, sha, _ = rt.anchor(tmp_path, fx.corpus())
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(memory_manifest=data['memory_bytes'], expected_memory_sha256=data['memory_sha256'],
                                 projection_bytes=data['projection_bytes'], results=[],
                                 query_utc='2026-10-31T13:00:00Z', policy_bytes=data['policy_bytes'],
                                 policy_sha256=data['policy_sha256'], roster_bytes=data['roster_bytes'],
                                 roster_sha256=data['roster_sha256'], card_decision_bytes=raw,
                                 card_decision_sha256=sha)


# --- RW3: author uuid and anchored source prefix before acceptance ---------------------------------------------------

def build_with(tmp_path, events, *, prefix_rows=None, end_cursor=None, decision_overrides=None, **extra):
    pytest.importorskip('faiss')
    projection = fx.project(events)
    root = tmp_path / 'out'
    root.mkdir(exist_ok=True)
    raw, sha, source = rt.anchor(tmp_path / 'prefix', events, prefix_rows=prefix_rows, **(decision_overrides or {}))
    if end_cursor is not None:
        source['end_cursor'] = end_cursor
    source.update(extra)
    return scp.build_shared_route_card_memory(projection, fx.encode, fx.IDENTITY, fx.DIM, root, 'cards', enabled=True,
                                              decision_bytes=raw, decision_sha256=sha, **source, **rt.pins()), root


def test_the_build_refuses_without_a_decision(tmp_path):
    pytest.importorskip('faiss')
    events = fx.corpus()
    projection = fx.project(events)
    root = tmp_path / 'out'
    root.mkdir()
    _, _, source = rt.anchor(tmp_path / 'prefix', events)
    with pytest.raises(scp.SharedCardRefused, match='decision_invalid'):
        scp.build_shared_route_card_memory(projection, fx.encode, fx.IDENTITY, fx.DIM, root, 'cards', enabled=True,
                                           **source, **rt.pins())
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('change', ['missing_row', 'other_prefix_pin', 'wrong_uuid_in_prefix', 'card_policy_bytes',
                                    'decision_vocabulary', 'cursor_offset', 'cursor_generation'])
def test_the_build_accepts_no_card_without_anchored_source_evidence(tmp_path, change):
    from waggledance.core import bridge_log_reader as bridge
    events = fx.corpus()
    kwargs = {}
    if change == 'missing_row':
        kwargs['prefix_rows'] = events[1:]
    elif change == 'other_prefix_pin':
        kwargs['decision_overrides'] = {'source_prefix': {'sha256': '0' * 64, 'offset': 1, 'generation': 'synthetic-g1'}}
    elif change == 'wrong_uuid_in_prefix':
        rows = json.loads(json.dumps(events))
        rows[2]['agent_uuid'] = fx.UUIDS[fx.LEAD]
        kwargs['prefix_rows'] = rows
    elif change == 'card_policy_bytes':
        kwargs['card_policy_bytes'] = fx.raw({'schema': 'wd.mind-public-card-policy.v1',
                                              'task_id': 'mind-public-knowledge-v1', 'writers': [fx.LEAD, fx.TOOLS]})
    elif change == 'decision_vocabulary':
        kwargs['decision_overrides'] = {'vocabulary_sha256': '0' * 64}
    else:
        _, _, source = rt.anchor(tmp_path / 'probe', events)
        cursor = source['end_cursor']
        kwargs['end_cursor'] = (bridge.BridgeCursor(cursor.offset - 1, cursor.file_identity, cursor.generation)
                                if change == 'cursor_offset'
                                else bridge.BridgeCursor(cursor.offset, cursor.file_identity, 'synthetic-g2'))
    with pytest.raises(scp.SharedCardRefused, match='source_unverified|source_unanchored|decision_mismatch'):
        build_with(tmp_path, events, **kwargs)
    assert list((tmp_path / 'out').iterdir()) == []


@pytest.mark.parametrize('change', ['author_uuid', 'author_missing', 'decision_sha', 'source_prefix', 'source_status',
                                    'completeness', 'heads_claim', 'evidence_missing', 'extra_key'])
def test_the_resolver_re_checks_the_bound_evidence(tmp_path, monkeypatch, change):
    route = rt.make_route(tmp_path, monkeypatch)
    first = next(iter(route['projection']['sources']))

    def edit(manifest):
        evidence = manifest['card_route']
        if change == 'author_uuid':
            evidence['authors'][first] = fx.UUIDS[fx.FABLE] if evidence['authors'][first] != fx.UUIDS[fx.FABLE] \
                else fx.UUIDS[fx.LEAD]
        elif change == 'author_missing':
            del evidence['authors'][first]
        elif change == 'decision_sha':
            evidence['decision_sha256'] = '0' * 64
        elif change == 'source_prefix':
            evidence['source_prefix']['offset'] += 1
        elif change == 'source_status':
            evidence['source_status'] = 'UNKNOWN'
        elif change == 'completeness':
            evidence['completeness'] = 'COMPLETE'
        elif change == 'heads_claim':
            evidence['heads'] = {}
        elif change == 'evidence_missing':
            del manifest['card_route']
        else:
            evidence['issuer'] = 'operator'
    with pytest.raises(resolver.ResolveRefused):
        rt.direct(route, edit_manifest=edit)


# --- RW2: heads against the decision's observed head set -------------------------------------------------------------

def test_a_head_outside_the_observed_set_is_refused_before_acceptance(tmp_path):
    with pytest.raises(scp.SharedCardRefused, match='head_unverified'):
        build_with(tmp_path, fx.corpus(), decision_overrides={'observed_heads': ['1' * 40, '2' * 40]})
    assert list((tmp_path / 'out').iterdir()) == []


def test_without_an_observed_set_heads_are_unknown_and_never_shown_as_hex(tmp_path, monkeypatch):
    route = rt.make_route(tmp_path, monkeypatch, heads=None)
    manifest = json.loads(route['data']['memory_bytes'])
    assert set(manifest['card_route']['heads'].values()) == {'UNKNOWN'}
    code, out = rt.run(route, situation='merged green pr genesis head test result needs review launcher cache')
    assert code == 0 and out['status'] == 'ANSWER'
    texts = [item['stored_text'] for item in out['results']]
    assert any(' head:unknown ' in text for text in texts)
    assert not any(HEX40.search(text) for text in texts)
    rule = fx.transport_regex()
    assert all(rule.fullmatch(text) for text in texts)


def test_with_an_observed_set_verified_heads_are_shown_as_observed(tmp_path, monkeypatch):
    route = rt.make_route(tmp_path, monkeypatch)
    code, out = rt.run(route, situation='merged green pr genesis head test result needs review launcher cache')
    assert code == 0 and out['status'] == 'ANSWER'
    shown = {match.group(0)[5:] for item in out['results'] for match in HEX40.finditer(item['stored_text'])}
    assert shown and shown <= set(rt.observed_heads(route['events']))


def test_a_manifest_claiming_verified_heads_without_an_observed_set_is_refused(tmp_path, monkeypatch):
    route = rt.make_route(tmp_path, monkeypatch, heads=None)

    def edit(manifest):
        manifest['card_route']['heads'] = {ref: 'VERIFIED' for ref in manifest['card_route']['heads']}
    with pytest.raises(resolver.ResolveRefused):
        rt.direct(route, edit_manifest=edit)


def test_a_same_content_prefix_under_another_generation_is_not_the_anchored_prefix(tmp_path):
    from waggledance.core import bridge_log_reader as bridge
    events = fx.corpus()
    _, _, probe = rt.anchor(tmp_path / 'probe', events)
    generation = tmp_path / 'other-generation.json'
    generation.write_bytes(fx.raw({'generation': 'synthetic-g2'}))
    cursor = bridge.read_bridge_log(probe['source_path'], generation_path=str(generation)).candidate_cursor
    assert cursor.generation == 'synthetic-g2'
    with pytest.raises(scp.SharedCardRefused, match='source_unanchored'):
        build_with(tmp_path, events, end_cursor=cursor, source_path=probe['source_path'],
                   generation_path=str(generation))
    assert list((tmp_path / 'out').iterdir()) == []


@pytest.mark.parametrize('change', ['module_changed', 'other_decision_bytes', 'policy_pin'])
def test_the_resolver_alone_verifies_the_decision_itself(tmp_path, monkeypatch, change):
    route = rt.make_route(tmp_path, monkeypatch)
    data = route['data']
    if change == 'module_changed':
        monkeypatch.setattr(scp, 'module_sha256', lambda: '0' * 64)
        decision = dict(card_decision_bytes=data['card_decision_bytes'],
                        card_decision_sha256=data['card_decision_sha256'])
    elif change == 'other_decision_bytes':
        raw = json.dumps(json.loads(data['card_decision_bytes']), sort_keys=True, indent=1).encode()
        decision = dict(card_decision_bytes=raw, card_decision_sha256=data['card_decision_sha256'])
    else:
        value = json.loads(data['card_decision_bytes'])
        value['nonreview_policy_sha256'] = '0' * 64
        raw = rt.canonical_decision(value)
        decision = dict(card_decision_bytes=raw, card_decision_sha256=data['card_decision_sha256'])
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(memory_manifest=data['memory_bytes'], expected_memory_sha256=data['memory_sha256'],
                                 projection_bytes=data['projection_bytes'],
                                 results=[rt._item(route['projection']['records'][0])],
                                 query_utc='2026-11-02T00:00:00Z', policy_bytes=data['policy_bytes'],
                                 policy_sha256=data['policy_sha256'], roster_bytes=data['roster_bytes'],
                                 roster_sha256=data['roster_sha256'], **decision)


# --- fixture binding on every CI platform (lead-ci-card-binding-fable-20261109-v1) ------------------------------------

def test_the_synthetic_config_passes_the_real_binding_check_on_this_platform(tmp_path):
    from tools import wd_q4_agent_query as q4
    cfg = rt.config(tmp_path, '7' * 64)
    q4._validate_config(cfg)  # the real check is unchanged: a refused tuple raises
    if os.name == 'nt':
        assert cfg.memory == str(tmp_path) and cfg.descriptor == str(tmp_path / 'descriptor.json')
    else:
        assert cfg.memory == rt.SYNTHETIC_MEMORY and cfg.descriptor == rt.SYNTHETIC_MEMORY + '\\descriptor.json'


def test_off_windows_the_synthetic_config_carries_a_drive_letter_path_and_binds(monkeypatch):
    from tools import wd_q4_agent_query as q4
    monkeypatch.setattr(rt.os, 'name', 'posix')
    posix_dir = PurePosixPath('/tmp/pytest-of-runner/pytest-0/test_without_an_observed_set_0')
    cfg = rt.config(posix_dir, '7' * 64)
    assert cfg.memory == rt.SYNTHETIC_MEMORY and cfg.descriptor == rt.SYNTHETIC_MEMORY + '\\descriptor.json'
    q4._validate_config(cfg)
    monkeypatch.setattr(rt.os, 'name', 'nt')
    with pytest.raises(q4.QueryRefused, match='invalid_binding'):
        q4._validate_config(rt.config(posix_dir, '7' * 64))  # a real POSIX string is still refused on Windows
