"""Opt-in staged plan file creation: synthetic pins, no runtime or installation."""
import hashlib
import json
import os

import pytest

from tests.tools.test_wd_q4_staged_fixed_plan import staged_args

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows fixed-plan path contract')


def test_default_off_does_not_touch_inputs():
    from tools import wd_q4_staged_fixed_plan_writer as writer
    assert writer.write() == {'status': 'OFF'}


def test_plan_written_once_and_matches_producer(tmp_path):
    from tools import wd_q4_staged_fixed_plan_writer as writer
    from tools import wd_q4_fixed_plan_producer as producer
    args = staged_args()
    out = writer.write(enabled=True, arguments=args, output_dir=str(tmp_path))
    raw = (tmp_path / 'fixed-plan.v3.json').read_bytes()
    expected = producer.build_staged_fixed_plan(enabled=True, **args)
    assert raw == expected['plan_bytes']
    assert out['sha256'] == hashlib.sha256(raw).hexdigest() == expected['plan_sha256']
    assert out['status'] == 'STAGED'
    with pytest.raises(writer.Refused):
        writer.write(enabled=True, arguments=args, output_dir=str(tmp_path))


@pytest.mark.parametrize('change', ['missing', 'extra', 'bad_pin'])
def test_invalid_arguments_refuse_before_writes(tmp_path, change):
    from tools import wd_q4_staged_fixed_plan_writer as writer
    args = staged_args()
    if change == 'missing': del args['card_policy_sha256']
    if change == 'extra': args['extra'] = 'unexpected'
    if change == 'bad_pin': args['card_policy_sha256'] = 'x' * 64
    with pytest.raises(writer.Refused):
        writer.write(enabled=True, arguments=args, output_dir=str(tmp_path))
    assert not list(tmp_path.iterdir())


def test_hash_pinned_request_and_output_refusal(tmp_path):
    from tools import wd_q4_staged_fixed_plan_writer as writer
    out = tmp_path / 'output'
    out.mkdir()
    request = {'schema': 'wd.q4-staged-fixed-plan-request.v1',
               'arguments': staged_args(), 'output_dir': str(out)}
    path = tmp_path / 'request.json'
    raw = json.dumps(request, sort_keys=True, separators=(',', ':')).encode()
    path.write_bytes(raw)
    assert writer.main(['--enabled', '--request', str(path), '--request-sha256', '0' * 64]) == 2
    assert not list(out.iterdir())
    assert writer.main(['--enabled', '--request', str(path), '--request-sha256', hashlib.sha256(raw).hexdigest()]) == 0
    assert (out / 'fixed-plan.v3.json').is_file()


def test_error_after_write_is_unknown_without_automatic_retry(tmp_path, monkeypatch):
    from tools import wd_q4_staged_fixed_plan_writer as writer
    def fail_sync(_fd):
        raise OSError('synthetic interruption')
    monkeypatch.setattr(writer.os, 'fsync', fail_sync)
    with pytest.raises(writer.Unknown):
        writer.write(enabled=True, arguments=staged_args(), output_dir=str(tmp_path))
    assert (tmp_path / 'fixed-plan.v3.json').exists()


def test_request_changed_after_write_is_unknown(tmp_path, monkeypatch):
    from tools import wd_q4_staged_fixed_plan_writer as writer
    out = tmp_path / 'output'
    out.mkdir()
    raw = json.dumps({'schema': writer.SCHEMA, 'arguments': staged_args(), 'output_dir': str(out)}).encode()
    request = tmp_path / 'request.json'
    request.write_bytes(raw)
    original = writer.common._read_pinned
    calls = []
    def read_once(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise ValueError('synthetic request mutation')
        return original(*args, **kwargs)
    monkeypatch.setattr(writer.common, '_read_pinned', read_once)
    assert writer.main(['--enabled', '--request', str(request), '--request-sha256', hashlib.sha256(raw).hexdigest()]) == 3
    assert (out / 'fixed-plan.v3.json').exists()
