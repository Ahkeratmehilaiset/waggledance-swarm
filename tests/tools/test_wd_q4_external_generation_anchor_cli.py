"""The anchor entrypoint uses only synthetic logs and pinned receipts."""
import hashlib
import importlib
import json

import pytest

from tests.tools.test_wd_q4_external_generation_anchor import fixture


def _case(tmp_path):
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    source, output, cursor, prefix = fixture(audit)
    receipt = {
        'schema': 'wd.q4-private-bridge-prefix-pin.v1',
        'cursor': {'offset': cursor.offset, 'file_identity': cursor.file_identity, 'generation': None},
        'prefix_sha256': prefix, 'source_authentication': 'UNKNOWN', 'freshness': 'UNKNOWN',
        'completeness': 'UNKNOWN', 'scope': 'bounded_observed_prefix',
    }
    raw = json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode('ascii')
    pin = audit / 'prefix-pin.json'
    pin.write_bytes(raw)
    return source, output, pin, hashlib.sha256(raw).hexdigest()


def test_off_never_reads_input():
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    assert mod.run(enabled=False, source_path=object()) == {'status': 'OFF'}


def test_creates_and_verifies_from_pinned_prefix(tmp_path):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    result = mod.run(enabled=True, source_path=str(source), pin_path=str(pin),
                     pin_sha256=digest, output_root=str(output))
    assert result['status'] == 'PREPARED'
    assert result['source_authentication'] == 'UNKNOWN'
    assert (output / 'generation.json').exists() and (output / 'binding.json').exists()
    assert not (output / 'pending.json').exists()


def test_cli_runs_from_pinned_prefix_without_emitting_source(tmp_path, capsys):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    code = mod.main(['--enabled', '--source', str(source), '--pin', str(pin),
                     '--pin-sha256', digest, '--output-root', str(output)])
    result = capsys.readouterr()
    assert code == 0 and json.loads(result.out)['status'] == 'PREPARED'
    assert str(source) not in result.out and 'synthetic' not in result.out


def test_pin_changed_after_prepare_is_unknown(tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    original = mod.anchor.prepare
    def change_pin(**kwargs):
        result = original(**kwargs)
        pin.write_bytes(b'{}')
        return result
    monkeypatch.setattr(mod.anchor, 'prepare', change_pin)
    with pytest.raises(mod.Unknown):
        mod.run(enabled=True, source_path=str(source), pin_path=str(pin),
                pin_sha256=digest, output_root=str(output))
    assert (output / 'generation.json').exists()


def test_interrupt_during_post_write_check_is_unknown(tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    def interrupt(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(mod.anchor, 'verify', interrupt)
    with pytest.raises(mod.Unknown):
        mod.run(enabled=True, source_path=str(source), pin_path=str(pin),
                pin_sha256=digest, output_root=str(output))
    assert (output / 'generation.json').exists()


def test_interrupt_after_prepare_before_return_is_unknown(tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    original = mod.anchor.prepare
    def interrupt(**kwargs):
        original(**kwargs)
        raise KeyboardInterrupt()
    monkeypatch.setattr(mod.anchor, 'prepare', interrupt)
    with pytest.raises(mod.Unknown):
        mod.run(enabled=True, source_path=str(source), pin_path=str(pin),
                pin_sha256=digest, output_root=str(output))
    assert (output / 'generation.json').exists()


def test_pin_changed_during_verify_is_unknown(tmp_path, monkeypatch):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    original = mod.anchor.verify
    def change_pin(**kwargs):
        result = original(**kwargs)
        pin.write_bytes(b'{}')
        return result
    monkeypatch.setattr(mod.anchor, 'verify', change_pin)
    with pytest.raises(mod.Unknown):
        mod.run(enabled=True, source_path=str(source), pin_path=str(pin),
                pin_sha256=digest, output_root=str(output))


@pytest.mark.parametrize('fault', ['pin', 'source', 'occupied'])
def test_refuses_before_writes(tmp_path, fault):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    source, output, pin, digest = _case(tmp_path)
    if fault == 'pin': digest = '0' * 64
    if fault == 'source': source.write_bytes(b'{"changed":true}\n')
    if fault == 'occupied': (output / 'occupied').write_text('untouched')
    with pytest.raises(mod.Refused):
        mod.run(enabled=True, source_path=str(source), pin_path=str(pin),
                pin_sha256=digest, output_root=str(output))
    assert not (output / 'generation.json').exists()


def test_cli_default_off(capsys):
    mod = importlib.import_module('tools.wd_q4_external_generation_anchor_cli')
    assert mod.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}
