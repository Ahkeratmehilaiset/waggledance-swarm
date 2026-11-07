"""Real-format decisions over a fabricated source and local synthetic Git."""
import hashlib
import importlib
import json

import pytest
from tests.tools.test_wd_q4_real_card_generation import real_fixture


def sha(value): return hashlib.sha256(value).hexdigest()


def call_args(real_fixture):
    args, out, _, _ = real_fixture
    args = dict(args)
    args.pop('enabled')
    encode, identity, dim = (args.pop(name) for name in ('encode', 'identity', 'dimension'))
    return args, out, (encode, identity, dim, lambda: True)


def test_off_does_not_access_inputs():
    mod = importlib.import_module('tools.wd_q4_real_card_snapshot_producer')
    class Untouchable:
        def __getattribute__(self, _): raise AssertionError('OFF touched input')
    assert mod.produce(enabled=False, generation=Untouchable()) == {'status': 'OFF'}


def test_real_format_fixture_makes_verified_c2_pointer(real_fixture, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_real_card_snapshot_producer')
    args, out, encoder = call_args(real_fixture)
    monkeypatch.setattr(mod, '_load_pinned_e5', lambda **_kwargs: encoder)
    result = mod.produce(enabled=True, generation=args, encoder_config={}, trusted_model_root='synthetic')
    assert result['status'] == 'VALIDATED_STAGED'
    assert result['source_authentication'] == result['publisher_authentication'] == result['completeness'] == 'UNKNOWN'
    raw = (out / 'card-snapshot-candidate.json').read_bytes()
    assert sha(raw) == result['pointer_sha256']
    assert json.loads(raw)['generation'] == 'candidate'


@pytest.mark.parametrize('fault', ['synthetic_decision', 'bad_git_anchor', 'bad_source', 'pointer_exists'])
def test_refuse_bad_inputs_without_overwriting(real_fixture, monkeypatch, fault):
    mod = importlib.import_module('tools.wd_q4_real_card_snapshot_producer')
    args, out, encoder = call_args(real_fixture)
    monkeypatch.setattr(mod, '_load_pinned_e5', lambda **_kwargs: encoder)
    if fault == 'synthetic_decision':
        value = json.loads(args['decision_bytes']); value['synthetic'] = True
        args['decision_bytes'] = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        args['decision_sha256'] = sha(args['decision_bytes'])
    if fault == 'bad_git_anchor': args['real_approved_anchors'] = {'refs/heads/nope':'0' * 40}
    if fault == 'bad_source':
        from pathlib import Path
        Path(args['source_path']).write_bytes(b'changed synthetic')
    if fault == 'pointer_exists': (out / 'card-snapshot-candidate.json').write_bytes(b'unchanged')
    with pytest.raises((mod.Refused, mod.Unknown)):
        mod.produce(enabled=True, generation=args, encoder_config={}, trusted_model_root='synthetic')
    assert not (out / 'candidate').exists()
    if fault == 'pointer_exists': assert (out / 'card-snapshot-candidate.json').read_bytes() == b'unchanged'


def test_e5_pin_rejected_before_generation(real_fixture, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_real_card_snapshot_producer')
    args, out, _ = call_args(real_fixture)
    monkeypatch.setattr(mod, '_load_pinned_e5', lambda **_kwargs: None)
    with pytest.raises(mod.Refused):
        mod.produce(enabled=True, generation=args, encoder_config={}, trusted_model_root='synthetic')
    assert not list(out.iterdir())


def test_e5_loader_requires_assets_and_rechecks_them(monkeypatch, tmp_path):
    mod = importlib.import_module('tools.wd_q4_real_card_snapshot_producer')
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    assets = {'model.safetensors':'b' * 64}
    cfg = {'model':child.ENCODER_MODEL, 'revision':'a' * 40, 'model_dir':str(tmp_path),
           'weights_sha256':'b' * 64, 'dim':child.ENCODER_DIM, 'assets':assets}
    identity = {'model':child.ENCODER_MODEL, 'revision':'a' * 40, 'weights_sha256':'b' * 64}
    seen = []
    monkeypatch.setattr(child, '_model_root_bound', lambda *_: seen.append('root') or ['model.safetensors'])
    monkeypatch.setattr(child, '_assets_bound', lambda *_: seen.append('assets') or True)
    monkeypatch.setattr(child, '_identity', lambda *_: seen.append('identity') or identity)
    monkeypatch.setattr(child, '_encoder', lambda *_: seen.append('encoder') or (lambda _texts: None))
    loaded = mod._load_pinned_e5(encoder_config=cfg, trusted_model_root=tmp_path,
                                output_root=tmp_path, source_root=tmp_path)
    assert loaded is not None and loaded[1] == identity and loaded[2] == child.ENCODER_DIM
    assert seen == ['root', 'assets', 'identity', 'encoder']
    assert loaded[3]() is True
    assert seen[-3:] == ['root', 'assets', 'identity']
    monkeypatch.setattr(child, '_assets_bound', lambda *_: False)
    assert loaded[3]() is False
    assert mod._load_pinned_e5(encoder_config=cfg, trusted_model_root=tmp_path,
                               output_root=tmp_path, source_root=tmp_path) is None


def test_postwrite_c2_failure_is_unknown(real_fixture, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_real_card_snapshot_producer')
    args, out, encoder = call_args(real_fixture)
    monkeypatch.setattr(mod, '_load_pinned_e5', lambda **_kwargs: encoder)
    from sandbox.mind.step2 import shared_card_checkpoint as checkpoint
    monkeypatch.setattr(checkpoint, 'validate_card_snapshot', lambda **_: {'status':'INCOMPLETE'})
    with pytest.raises(mod.Unknown):
        mod.produce(enabled=True, generation=args, encoder_config={}, trusted_model_root='synthetic')
    assert (out / 'candidate').is_dir()
    assert not (out / 'card-snapshot-candidate.json').exists()
