"""Synthetic retained-only backend contract."""
import importlib.util
import hashlib
import io
import json
import struct
import sys
from pathlib import Path

import pytest
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_retained_query as retained

ROOT = Path(__file__).resolve().parents[2]

@pytest.fixture
def case(tmp_path):
    path = ROOT / "tests/tools/test_wd_mind_content_snapshot_package.py"
    spec = importlib.util.spec_from_file_location("retained_test_package", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    fpath = ROOT / "tests/tools/test_wd_mind_content_query_readonly.py"
    fspec = importlib.util.spec_from_file_location("retained_test_fixtures", fpath)
    fixtures = importlib.util.module_from_spec(fspec)
    sys.modules[fspec.name] = fixtures
    fspec.loader.exec_module(fixtures)
    c = fixtures._semantic_case(reader, tmp_path, all_tasks=True)
    codec = mod.Codec(fixtures)
    from tools import wd_mind_content_snapshot_package as package
    output = package.package_content_snapshot(**mod._arguments(c, codec, mod.FULL_PREFIX_PROFILE))
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=c['encoder_provider'], index_decoder=codec.decode,
        **{k: output[k] for k in ('encoder_binding_bytes','encoder_binding_sha256',
                                   'index_binding_bytes','index_binding_sha256')})
    cells = {k: {**v, 'artifacts': {n: b for n,b in v['artifacts'].items() if n != 'index'}}
             for k,v in output['cell_snapshots'].items()}
    inputs = {k:c[k] for k in reader._Q4_SOURCE_KEYS}
    inputs.update({k:v for k,v in output.items() if k not in ('cell_snapshots', 'index_binding_bytes','index_binding_sha256')})
    inputs.update(enabled=True, cell_snapshots=cells, encoder_provider=encoder,
                  query_bytes=fixtures._raw({'situation':'retry network outage','time':fixtures.QUERY_TIME}))
    return inputs, c, output, codec, encoder, index, fixtures

def test_retained_backend_matches_canonical_reference(case):
    inputs,c,output,codec,encoder,index,fixtures = case
    result = retained.query_retained_content_snapshot(**inputs)
    old = reader.query_complete_content_snapshot(**{k:c[k] for k in reader._Q4_SOURCE_KEYS}, **output,
        encoder_provider=encoder, index_provider=index, stream=io.BytesIO(inputs['query_bytes']))
    assert result['results'] == old['results']
    assert result['index_match'] == 'NOT_APPLICABLE'
    assert result['authority'] == 'none'
    assert result['status'] == 'ANSWER'
    assert all(hit['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX' for hit in result['results'])


def test_cross_language_candidate_uses_single_verified_query(case):
    inputs, _, _, _, encoder, _, fixtures = case
    class Counting:
        identity = encoder.identity
        calls = 0
        def query(self, text):
            self.calls += 1
            return encoder.query(text)
    provider = Counting()
    inputs['encoder_provider'] = provider
    inputs['query_bytes'] = fixtures._raw({'situation':'verkkoyhteys palautui', 'time':fixtures.QUERY_TIME})
    result = retained.query_retained_content_snapshot(**inputs)
    assert result['status'] == 'SEMANTIC_CANDIDATE'
    assert result['rule'] == 'q4-complete-semantic-candidate-v1'
    assert result['relevance'] == 'UNVERIFIED'
    assert result['index_match'] == 'NOT_APPLICABLE'
    assert result['authority'] == 'none'
    assert all(hit['source_reference']['verified'] == 'MATCH_WITHIN_PREFIX' for hit in result['results'])
    assert provider.calls == 1


@pytest.mark.parametrize('age', [True, False, 0, -1, 901, 1.0, '60', None])
def test_fresh_query_rejects_invalid_age_bound(case, age):
    inputs, _, _, _, _, _, fixtures = case
    inputs['query_bytes'] = fixtures._raw({'situation':'verkkoyhteys palautui',
                                          'time':fixtures.QUERY_TIME, 'max_age_seconds':age})
    with pytest.raises(reader.ContentQueryRefused):
        retained.query_retained_content_snapshot(**inputs)


@pytest.mark.parametrize(('query_time', 'age', 'expected'), [
    ('2026-01-01T00:02:00Z', 120, 'SEMANTIC_CANDIDATE'),
    ('2026-01-01T00:02:00Z', 119, 'ABSTAIN'),
    ('2026-01-01T00:14:59Z', 900, 'SEMANTIC_CANDIDATE'),
    ('2026-01-01T00:14:59.000001Z', 900, 'ABSTAIN'),
    ('2026-01-01T00:15:00Z', 900, 'ABSTAIN'),
    ('2025-12-31T23:59:00Z', 900, 'ABSTAIN'),
])
def test_fresh_query_has_conservative_minute_boundary(case, query_time, age, expected):
    inputs, _, _, _, _, _, fixtures = case
    inputs['query_bytes'] = fixtures._raw({'situation':'verkkoyhteys palautui',
                                          'time':query_time, 'max_age_seconds':age})
    result = retained.query_retained_content_snapshot(**inputs)
    assert result['status'] == expected
    assert bool(result['results']) == (expected != 'ABSTAIN')


@pytest.mark.parametrize('count', [1, 2, 3])
def test_small_fresh_candidates_with_fewer_than_four_eligible(count):
    when = reader._utc('2026-01-01T00:02:00Z')
    records = {f'r{i}': {'stored_text': reader.encode_text(f'other event {i}'),
               'provenance': {'available_utc':'2026-01-01T00:01:00Z'}} for i in range(count)}
    vector = struct.pack('<384d', 1.0, *([0.0]*383))
    entries = {key:{'w':vector,'d':vector} for key in records}
    class Encoder:
        def query(self, text): return (1.0,) + (0.0,)*383
    assert len(retained._small_fresh_candidates(records, entries, Encoder(), 'cross language', when)) == count


def test_small_fallback_eligibility_excludes_expired_and_self_matches():
    when = reader._utc('2026-01-01T00:02:00Z')
    records = {f'r{i}': {'stored_text': reader.encode_text(f'other event {i}'),
               'provenance': {'available_utc':'2026-01-01T00:01:00Z'}} for i in range(5)}
    records['r0']['stored_text'] = reader.encode_text('cross language')
    records['r1']['provenance']['valid_until_utc'] = '2026-01-01T00:01:59Z'
    assert retained._fresh_eligible_count(records, 'cross language', when) == 3
    vector = struct.pack('<384d', 1.0, *([0.0]*383))
    entries = {key:{'w':vector,'d':vector} for key in records}
    class Encoder:
        def query(self, text): return (1.0,) + (0.0,)*383
    assert set(retained._small_fresh_candidates(records, entries, Encoder(), 'cross language', when)) == {'r2','r3','r4'}


def test_fresh_lower_score_wins_over_stale_higher_score(case, tmp_path):
    inputs, source_case, _, codec, encoder, _, fixtures = case
    from tools import wd_mind_content_snapshot_package as package
    rows = [fixtures._row('old network event', ts_utc='2026-01-01T00:00:00.000000Z',
                          task_id=f'synthetic-old-{i}') for i in range(4)]
    rows += [fixtures._row('new network event', ts_utc='2026-01-01T00:19:00.000000Z',
                           task_id='synthetic-new')]
    source = fixtures._all_tasks_case(reader, tmp_path, rows)
    source_case.update(fixtures._snapshot(source, reader.rederive_complete_message_projection(**source)))
    def passage(texts):
        return b''.join(struct.pack('<384f', *([0.6, 0.8] if 'new network' in text else [1.0, 0.0]),
                                    *([0.0] * 382)) for text in texts)
    args = dict(enabled=True, snapshot={k:source_case[k] for k in reader._Q4_SOURCE_KEYS},
                passage_encoder=passage, index_serializer=codec.serialize, index_decoder=codec.decode,
                capacity_profile='full_prefix_stream_v1',
                **{k: source_case[k] for k in ('encoder_binding_bytes','encoder_binding_sha256',
                                                'index_binding_bytes','index_binding_sha256')})
    output = package.package_content_snapshot(**args)
    fresh = {k:source_case[k] for k in reader._Q4_SOURCE_KEYS}
    fresh.update({k:v for k,v in output.items() if k not in ('cell_snapshots','index_binding_bytes','index_binding_sha256')})
    fresh.update(enabled=True, encoder_provider=encoder,
        cell_snapshots={k:{**v,'artifacts':{n:b for n,b in v['artifacts'].items() if n != 'index'}}
                        for k,v in output['cell_snapshots'].items()},
        query_bytes=fixtures._raw({'situation':'verkko palautui','time':'2026-01-01T00:21:00Z',
                                    'max_age_seconds':180}))
    result = retained.query_retained_content_snapshot(**fresh)
    assert result['status'] == 'SEMANTIC_CANDIDATE'
    assert len(result['results']) == 1
    assert reader.decode_text(result['results'][0]['stored_text']) == 'new network event'


def test_q4_answer_never_enters_fallback(case, monkeypatch):
    inputs, _, _, _, encoder, _, _ = case
    class Counting:
        identity = encoder.identity
        calls = 0
        def query(self, text):
            self.calls += 1
            return encoder.query(text)
    provider = Counting()
    inputs['encoder_provider'] = provider
    def forbidden(*args, **kwargs):
        raise AssertionError('fallback must not execute')
    monkeypatch.setattr(reader, '_q4_complete_semantic_candidates', forbidden)
    result = retained.query_retained_content_snapshot(**inputs)
    assert result['status'] == 'ANSWER' and 'relevance' not in result
    assert result['rule'] == 'q4-rule-v1.2-canonical-f32' and provider.calls == 1


@pytest.mark.parametrize('mutation', ['source', 'policy'])
def test_cross_language_candidate_rechecks_after_provider(case, mutation):
    inputs, _, _, _, encoder, _, fixtures = case
    class Mutating:
        identity = encoder.identity
        def query(self, text):
            if mutation == 'source':
                path = Path(inputs['messages']['source_path'])
                path.write_bytes(path.read_bytes().replace(b'network retry', b'changed retry', 1))
            else:
                inputs['messages']['policy_sha256'] = '0' * 64
            return encoder.query(text)
    inputs['encoder_provider'] = Mutating()
    inputs['query_bytes'] = fixtures._raw({'situation':'verkkoyhteys palautui', 'time':fixtures.QUERY_TIME})
    with pytest.raises(reader.ContentQueryRefused, match='^content_query_refused$'):
        retained.query_retained_content_snapshot(**inputs)


@pytest.mark.parametrize('fault', ['orthogonal', 'too_early', 'self_match'])
def test_candidate_negative_paths_abstain(case, fault):
    inputs, _, _, _, encoder, _, fixtures = case
    class Counting:
        identity = encoder.identity
        calls = 0
        def query(self, text):
            self.calls += 1
            return ([0.0, 0.0, 1.0] + [0.0]*381 if fault == 'orthogonal'
                    else encoder.query(text))
    provider = Counting()
    inputs['encoder_provider'] = provider
    situation = 'network retry restored transport' if fault == 'self_match' else 'verkkoyhteys palautui'
    timestamp = '2000-01-01T00:00:00Z' if fault == 'too_early' else fixtures.QUERY_TIME
    inputs['query_bytes'] = fixtures._raw({'situation':situation, 'time':timestamp})
    result = retained.query_retained_content_snapshot(**inputs)
    assert result['status'] == 'ABSTAIN' and result['results'] == []
    assert 'relevance' not in result
    assert provider.calls <= 1


def test_negative_zero_bits_survive_into_ranking(case, monkeypatch):
    inputs = case[0]
    cell = next(iter(inputs['cell_snapshots']))
    data = inputs['cell_snapshots'][cell]['artifacts']
    raw = bytearray(data['vectors'])
    raw[1532:1536] = struct.pack('<f', -0.0)
    data['vectors'] = bytes(raw)
    _rehash_cell(inputs, cell, recalculate_pairs=True)
    original = reader._q4_rank
    def check(records, entries, encoder, query, when):
        first_id = json.loads(data['meta'])['doc_ids'][0]
        record_id, view = first_id.rsplit('#', 1)
        assert entries[record_id][view][-8:] == struct.pack('<d', -0.0)
        return original(records, entries, encoder, query, when)
    monkeypatch.setattr(reader, '_q4_rank', check)
    assert retained.query_retained_content_snapshot(**inputs)['status'] == 'ANSWER'


def _rehash_cell(inputs, cell, *, recalculate_pairs=False):
    item = inputs['cell_snapshots'][cell]
    data = item['artifacts']
    receipt = json.loads(data['receipt'])
    content = receipt['content']
    for name in ('meta', 'provenance', 'vectors'):
        content['files'][name] = reader._sha(data[name])
    if recalculate_pairs:
        meta = json.loads(data['meta'])
        content['ids_sha256'] = reader._sha('\n'.join(meta['doc_ids']).encode())
        digest = hashlib.sha256()
        for i, doc_id in enumerate(meta['doc_ids']):
            digest.update(doc_id.encode() + b'\0' + data['vectors'][i*1536:(i+1)*1536])
        content['pairs_sha256'] = digest.hexdigest()
    receipt['content_sha256'] = reader._sha(reader._canonical(content))
    data['receipt'] = reader._canonical(receipt)
    sha = reader._sha(data['receipt'])
    pointer = json.loads(item['pointer']); pointer['receipt_sha256'] = sha
    item['pointer'] = reader._canonical(pointer)
    line = json.loads(item['log']); line['receipt_sha256'] = sha
    item['log'] = reader._canonical(line) + b'\n'
    memory = json.loads(inputs['memory_bytes']); memory['cells'][cell]['receipt_sha256'] = sha
    inputs['memory_bytes'] = reader._canonical(memory)
    inputs['memory_sha256'] = reader._sha(inputs['memory_bytes'])
    descriptor = json.loads(inputs['descriptor_bytes']); descriptor['memory_sha256'] = inputs['memory_sha256']
    inputs['descriptor_bytes'] = reader._canonical(descriptor)
    inputs['descriptor_sha256'] = reader._sha(inputs['descriptor_bytes'])


@pytest.mark.parametrize('fault', ['vector_hash', 'pair_hash', 'vector_pair', 'nan', 'wrong_norm',
                                   'duplicate', 'provenance', 'source_before'])
def test_refuses_tamper_without_partial_output(case, fault, capsys):
    inputs = case[0]
    cell = next(iter(inputs['cell_snapshots']))
    data = inputs['cell_snapshots'][cell]['artifacts']
    if fault == 'vector_hash':
        data['vectors'] = bytes([data['vectors'][0] ^ 1]) + data['vectors'][1:]
    if fault == 'pair_hash':
        receipt = json.loads(data['receipt']); receipt['content']['pairs_sha256'] = 'f' * 64
        data['receipt'] = reader._canonical(receipt)
        _rehash_cell(inputs, cell)
    if fault == 'vector_pair':
        v = struct.unpack('<384f', data['vectors'][:1536])
        data['vectors'] = struct.pack('<384f', *(-x for x in v)) + data['vectors'][1536:]
        _rehash_cell(inputs, cell)
    if fault in ('nan', 'wrong_norm'):
        data['vectors'] = struct.pack('<f', float('nan') if fault == 'nan' else 3.0) + data['vectors'][4:]
        _rehash_cell(inputs, cell, recalculate_pairs=True)
    if fault == 'duplicate':
        meta = json.loads(data['meta']); meta['doc_ids'][1] = meta['doc_ids'][0]
        data['meta'] = reader._canonical(meta)
        _rehash_cell(inputs, cell, recalculate_pairs=True)
    if fault == 'provenance':
        data['provenance'] = data['provenance'].replace(b'"view":"w"', b'"view":"d"', 1)
        _rehash_cell(inputs, cell)
    if fault == 'source_before':
        source = Path(inputs['messages']['source_path'])
        source.write_bytes(source.read_bytes().replace(b'network retry', b'changed retry', 1))
    with pytest.raises(reader.ContentQueryRefused, match='^content_query_refused$'):
        retained.query_retained_content_snapshot(**inputs)
    assert capsys.readouterr() == ('', '')


@pytest.mark.parametrize('fault', ['source', 'snapshot', 'control', 'exception'])
def test_provider_mutation_refuses_without_partial_result(case, fault, capsys):
    inputs, _, _, _, encoder, _, _ = case
    class BadProvider:
        identity = encoder.identity
        def query(self, text):
            if fault == 'source':
                path = Path(inputs['messages']['source_path'])
                path.write_bytes(path.read_bytes().replace(b'network retry', b'changed retry', 1))
            if fault == 'snapshot': inputs['messages']['policy_sha256'] = '0' * 64
            if fault == 'control':
                item = next(iter(inputs['cell_snapshots'].values()))
                item['artifacts']['vectors'] = b'changed'
            if fault == 'exception': raise RuntimeError('private test data')
            return encoder.query(text)
    inputs['encoder_provider'] = BadProvider()
    with pytest.raises(reader.ContentQueryRefused, match='^content_query_refused$'):
        retained.query_retained_content_snapshot(**inputs)
    assert capsys.readouterr() == ('', '')

