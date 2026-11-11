"""Synthetic read-only bridge snapshots, never the installed bridge."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from waggledance.core import bridge_log_reader as bridge


def fixture(tmp_path):
    source_dir=tmp_path/'fake-bridge';source_dir.mkdir()
    source=source_dir/'events.jsonl'
    raw=b'{"task_id":"synthetic-a","message":"private-canary"}\n{"task_id":"synthetic-b"}\n'
    source.write_bytes(raw)
    output=tmp_path/'.codex-audit'/'private-audit';output.mkdir(parents=True)
    return source,output,raw,source_dir/'events.generation.json'


def test_output_without_audit_component_is_refused(tmp_path):
    if '.codex-audit' in tmp_path.parts:
        pytest.skip('negative case requires a temporary path outside .codex-audit')
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin')
    source,_,raw,generation=fixture(tmp_path)
    output=tmp_path/'private-audit';output.mkdir()
    with pytest.raises(mod.Refused):
        mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(output))
    assert not list(output.iterdir()) and source.read_bytes()==raw


def test_default_off_does_not_inspect_arguments():
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin')
    class Untouchable:
        def __getattribute__(self,_):raise AssertionError('OFF touched input')
    assert mod.capture(enabled=False,source_path=Untouchable())=={'status':'OFF'}


def test_receipt_contains_only_private_cursor_and_hash(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin');monkeypatch.setattr(mod,'_PAGE',64)
    source,out,raw,generation=fixture(tmp_path)
    result=mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(out))
    saved=(out/'prefix-pin.json').read_bytes();receipt=json.loads(saved)
    assert result['status']=='PINNED' and result['receipt_sha256']==hashlib.sha256(saved).hexdigest()
    assert receipt['cursor']['offset']==len(raw) and receipt['cursor']['generation'] is None
    assert receipt['prefix_sha256']==hashlib.sha256(raw).hexdigest()
    assert b'private-canary' not in saved and b'private-canary' not in json.dumps(result).encode()
    assert source.read_bytes()==raw and not generation.exists()


def test_append_after_frozen_tail_does_not_change_prefix(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin');source,out,raw,generation=fixture(tmp_path)
    original=bridge.read_bridge_log_tail_lines
    def append(*args,**kwargs):
        result=original(*args,**kwargs)
        with source.open('ab') as f:f.write(b'{"task_id":"later"}\n')
        return result
    monkeypatch.setattr(bridge,'read_bridge_log_tail_lines',append)
    mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(out))
    receipt=json.loads((out/'prefix-pin.json').read_bytes())
    assert receipt['cursor']['offset']==len(raw) and receipt['prefix_sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('fault',['rotation','rewrite','generation_appears','partial_only'])
def test_unstable_or_unusable_source_refused_without_receipt(tmp_path,monkeypatch,fault):
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin');source,out,raw,generation=fixture(tmp_path)
    if fault=='partial_only':source.write_bytes(b'{"incomplete":')
    if fault=='rewrite':
        monkeypatch.setattr(mod,'_PAGE',64)
        original_read=bridge.read_bridge_log;calls=[]
        def rewrite(*args,**kwargs):
            result=original_read(*args,**kwargs);calls.append(True)
            if len(calls)==1:source.write_bytes(raw.replace(b'synthetic-a',b'synthetic-c'))
            return result
        monkeypatch.setattr(bridge,'read_bridge_log',rewrite)
    original=bridge.read_bridge_log_tail_lines
    def mutate(*args,**kwargs):
        result=original(*args,**kwargs)
        if fault=='rotation':source.rename(tmp_path/'old.jsonl');source.write_bytes(raw)
        if fault=='generation_appears':generation.write_bytes(b'{"generation":"new"}')
        return result
    monkeypatch.setattr(bridge,'read_bridge_log_tail_lines',mutate)
    with pytest.raises(mod.Refused):mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(out))
    assert not list(out.iterdir())


def test_interrupt_after_receipt_write_is_unknown(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin');source,out,_,generation=fixture(tmp_path)
    original=mod._write_new
    def interrupt(path,raw):original(path,raw);raise KeyboardInterrupt()
    monkeypatch.setattr(mod,'_write_new',interrupt)
    with pytest.raises(mod.Interrupted):
        mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(out))
    assert (out/'prefix-pin.json').exists()


def test_rotation_during_receipt_write_is_unknown(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin');source,out,raw,generation=fixture(tmp_path)
    original=mod._write_new
    def rotate(path,data):
        original(path,data);source.rename(tmp_path/'old.jsonl');source.write_bytes(raw)
    monkeypatch.setattr(mod,'_write_new',rotate)
    with pytest.raises(mod.Unknown):
        mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(out))
    assert (out/'prefix-pin.json').exists()


def test_more_than_100000_rows_use_bounded_pagination(tmp_path):
    mod=importlib.import_module('tools.wd_q4_bridge_prefix_pin');source,out,_,generation=fixture(tmp_path)
    row=b'{"task_id":"synthetic"}\n';raw=row*100005;source.write_bytes(raw)
    result=mod.capture(enabled=True,source_path=str(source),generation_path=str(generation),output_root=str(out))
    receipt=json.loads((out/'prefix-pin.json').read_bytes())
    assert result['status']=='PINNED' and receipt['cursor']['offset']==len(raw)
    assert receipt['prefix_sha256']==hashlib.sha256(raw).hexdigest()
