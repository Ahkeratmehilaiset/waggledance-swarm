"""Synthetic retained query tests. No real operator policy or bridge content."""
import hashlib
import json
from pathlib import Path

import pytest

from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_content_snapshot_package as package
import test_wd_mind_content_query_readonly as fixtures
import test_wd_mind_content_snapshot_package as producer_tests


def _make(tmp_path, *, generationless=False):
    from tools import wd_mind_retained_query as retained
    case, codec, output, snapshot, bundle, deploy, _, run = producer_tests._narrow_fixture(
        package, fixtures, tmp_path, generationless=generationless)
    encoder, index = reader.adapt_complete_readonly_providers(
        encoder=case['encoder_provider'], index_decoder=codec.decode,
        **{key: output[key] for key in ('encoder_binding_bytes', 'encoder_binding_sha256',
                                        'index_binding_bytes', 'index_binding_sha256')})
    snapshot = dict(snapshot)
    snapshot.pop('enabled')
    owner = retained.create_retained_query_owner(enabled=True, bundle_root=str(bundle),
        expected_deployment_manifest_sha256=fixtures._sha(deploy), **snapshot, **output,
        encoder_provider=encoder, index_provider=index)
    raw = fixtures._raw({'situation': 'retry network outage', 'time': fixtures.QUERY_TIME})
    return retained, owner, case, codec, snapshot, bundle, raw, run


def test_retained_two_queries_reuse_decoded_entries_and_source_refs(tmp_path):
    retained, owner, _, codec, _, _, raw, run = _make(tmp_path)
    baseline = run()
    decodes = len(codec.decoded)
    first = owner.query(raw)
    second = owner.query(raw)
    assert first == second == baseline
    assert first['authority'] == 'none' and first['same_user_aba'] == 'UNKNOWN'
    assert first['results'] and all(x['source_reference']['verified'] == 'MATCH_SELECTED_ROW' for x in first['results'])
    assert len(codec.decoded) == decodes


@pytest.mark.parametrize('fault', ['append', 'rewrite', 'truncate', 'replace', 'sidecar', 'manifest'])
def test_retained_source_or_control_deviation_refuses_and_drops_state(tmp_path, fault):
    retained, owner, _, _, snapshot, bundle, raw, _ = _make(tmp_path, generationless=True)
    path = Path(snapshot['messages']['source_path'])
    original = path.read_bytes()
    sidecar = Path(snapshot['messages']['generation_path'])
    manifest = bundle / 'deployment-manifest.json'
    manifest_original = manifest.read_bytes()
    if fault == 'append': path.write_bytes(original + b'{}\n')
    elif fault == 'rewrite': path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    elif fault == 'truncate': path.write_bytes(original[:-1])
    elif fault == 'replace':
        replacement = path.with_name('synthetic-replacement.jsonl')
        replacement.write_bytes(original)
        replacement.replace(path)
    elif fault == 'sidecar': sidecar.write_bytes(b'{}')
    elif fault == 'manifest': manifest.write_bytes(manifest_original + b' ')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    if fault in ('append', 'rewrite', 'truncate'): path.write_bytes(original)
    elif fault == 'sidecar': sidecar.unlink()
    elif fault == 'manifest': manifest.write_bytes(manifest_original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_rechecks_source_after_encoder_and_drops_state(tmp_path):
    retained, owner, case, _, snapshot, _, raw, _ = _make(tmp_path)
    path = Path(snapshot['messages']['source_path'])
    original = path.read_bytes()
    case['encoder_provider'].on_query = lambda *_: path.write_bytes(original + b'{}\n')
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    path.write_bytes(original)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_rejects_other_process_and_stays_closed(tmp_path, monkeypatch):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    monkeypatch.setattr(retained, '_pid', lambda: -1)
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
    monkeypatch.undo()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)


def test_retained_disabled_and_closed_refuse(tmp_path):
    retained, owner, _, _, _, _, raw, _ = _make(tmp_path)
    with pytest.raises(reader.ContentQueryRefused):
        retained.create_retained_query_owner(enabled=False)
    owner.close()
    owner.close()
    with pytest.raises(reader.ContentQueryRefused): owner.query(raw)
