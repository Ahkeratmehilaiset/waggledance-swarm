"""Synthetic logs only; never touch the installed bridge."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from waggledance.core import bridge_log_reader as bridge
from tests.tools.test_wd_q4_real_card_generation import real_fixture


def fixture(tmp_path):
    source_dir=tmp_path/'source';source_dir.mkdir()
    source=source_dir/'synthetic-events.jsonl';source.write_bytes(b'{"task_id":"synthetic"}\n')
    output=tmp_path/'.codex-audit'/'anchor';output.mkdir(parents=True)
    result=bridge.read_bridge_log(source)
    assert result.candidate_cursor.generation is None
    raw=source.read_bytes()
    return source,output,result.candidate_cursor,hashlib.sha256(raw).hexdigest()


def test_default_off_no_input_access():
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    class Untouchable:
        def __getattribute__(self,_):raise AssertionError('OFF input touched')
    assert mod.prepare(enabled=False,source_path=Untouchable())=={'status':'OFF'}


def test_fixture_output_contains_its_own_audit_directory(tmp_path):
    _, output, _, _ = fixture(tmp_path)
    assert '.codex-audit' in output.relative_to(tmp_path).parts


def test_external_sidecar_is_usable_by_existing_prefix_reader(tmp_path):
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    from tools import wd_q4_public_card_prefix_verifier as verifier
    source,output,cursor,pin=fixture(tmp_path)
    result=mod.prepare(enabled=True,source_path=str(source),original_cursor=cursor,
                       expected_prefix_sha256=pin,output_root=str(output))
    sidecar=output/'generation.json';binding=output/'binding.json'
    assert result['status']=='PREPARED' and not (output/'pending.json').exists()
    assert result['source_authentication']==result['freshness']=='UNKNOWN'
    saved=json.loads(binding.read_bytes())
    assert saved['file_identity']==cursor.file_identity and saved['offset']==cursor.offset
    adapted=bridge.BridgeCursor(cursor.offset,cursor.file_identity,saved['generation'])
    assert verifier._read(str(source),str(sidecar),adapted,pin)=={}
    checks=dict(enabled=True,source_path=str(source),original_cursor=cursor,expected_prefix_sha256=pin,
                output_root=str(output),expected_generation_sha256=result['generation_sha256'],
                expected_binding_sha256=result['binding_sha256'])
    assert mod.verify(**checks)['status']=='VERIFIED_WITHIN_PREFIX'
    with source.open('ab') as stream:stream.write(b'{"task_id":"later"}\n')
    assert verifier._read(str(source),str(sidecar),adapted,pin)=={}
    assert mod.verify(**checks)['status']=='VERIFIED_WITHIN_PREFIX'
    sidecar.write_bytes(b'{"generation":"forged"}')
    with pytest.raises(mod.Refused):mod.verify(**checks)


def test_external_anchor_connects_to_real_decision_preparer(real_fixture,tmp_path):
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    from tests.tools.test_wd_q4_real_card_decision_prepare import arguments
    from tools import wd_q4_real_card_decision_prepare as decisions
    args=arguments(real_fixture)
    Path(args['generation_path']).unlink()  # synthetic fixture only
    old=args['end_cursor'];original=bridge.BridgeCursor(old.offset,old.file_identity,None)
    output=tmp_path/'.codex-audit'/'external-anchor';output.mkdir(parents=True)
    result=mod.prepare(enabled=True,source_path=args['source_path'],original_cursor=original,
                       expected_prefix_sha256=args['expected_prefix_sha256'],output_root=str(output))
    args['generation_path']=str(output/'generation.json')
    args['end_cursor']=bridge.BridgeCursor(old.offset,old.file_identity,result['generation'])
    prepared=decisions.prepare(**args)
    assert prepared['status']=='PREPARED' and prepared['source_authentication']=='UNKNOWN'


@pytest.mark.parametrize('fault',['rotation','wrong_binding_pin','pending'])
def test_existing_anchor_refuses_stale_or_uncertain_state(tmp_path,fault):
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    source,output,cursor,pin=fixture(tmp_path)
    result=mod.prepare(enabled=True,source_path=str(source),original_cursor=cursor,
                       expected_prefix_sha256=pin,output_root=str(output))
    checks=dict(enabled=True,source_path=str(source),original_cursor=cursor,expected_prefix_sha256=pin,
                output_root=str(output),expected_generation_sha256=result['generation_sha256'],
                expected_binding_sha256=result['binding_sha256'])
    if fault=='rotation':
        source.rename(tmp_path/'old.jsonl');source.write_bytes(b'{"task_id":"synthetic"}\n')
    if fault=='wrong_binding_pin':checks['expected_binding_sha256']='0'*64
    if fault=='pending':(output/'pending.json').write_bytes(b'{"status":"UNKNOWN"}')
    with pytest.raises(mod.Refused):mod.verify(**checks)


@pytest.mark.parametrize('fault',['bad_pin','wrong_id','generation_present','truncated','rotation'])
def test_mismatch_or_rotation_refused_before_sidecar(tmp_path,fault):
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    source,output,cursor,pin=fixture(tmp_path)
    if fault=='bad_pin':pin='0'*64
    if fault=='wrong_id':cursor=bridge.BridgeCursor(cursor.offset,'wrong',None)
    if fault=='generation_present':cursor=bridge.BridgeCursor(cursor.offset,cursor.file_identity,'already')
    if fault=='truncated':source.write_bytes(b'')
    if fault=='rotation':
        source.rename(tmp_path/'original.jsonl');source.write_bytes(b'{"task_id":"synthetic"}\n')
    with pytest.raises(mod.Refused):
        mod.prepare(enabled=True,source_path=str(source),original_cursor=cursor,
                    expected_prefix_sha256=pin,output_root=str(output))
    assert not list(output.iterdir())


def test_rotation_after_sidecar_keeps_unknown_pending(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    source,output,cursor,pin=fixture(tmp_path)
    from tools import wd_q4_public_card_prefix_verifier as verifier
    original=verifier._read
    def rotate(*args,**kwargs):
        source.rename(tmp_path/'old.jsonl');source.write_bytes(b'{"task_id":"synthetic"}\n')
        return original(*args,**kwargs)
    monkeypatch.setattr(verifier,'_read',rotate)
    with pytest.raises(mod.Unknown):
        mod.prepare(enabled=True,source_path=str(source),original_cursor=cursor,
                    expected_prefix_sha256=pin,output_root=str(output))
    assert (output/'pending.json').exists()


def test_interrupt_after_sidecar_keeps_unknown_pending(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_external_generation_anchor')
    source,output,cursor,pin=fixture(tmp_path)
    from tools import wd_q4_public_card_prefix_verifier as verifier
    def stop(*_args,**_kwargs):raise KeyboardInterrupt()
    monkeypatch.setattr(verifier,'_read',stop)
    with pytest.raises(mod.Interrupted):
        mod.prepare(enabled=True,source_path=str(source),original_cursor=cursor,
                    expected_prefix_sha256=pin,output_root=str(output))
    assert (output/'pending.json').exists()
