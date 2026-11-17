"""Synthetic-only lifecycle tests; no process is killed or terminated."""
import subprocess

import pytest

from mind_cooperative_subprocess import CooperativeChild


def _forbid_termination(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('forced termination is not permitted')
    monkeypatch.setattr(subprocess.Popen, 'kill', forbidden)
    monkeypatch.setattr(subprocess.Popen, 'terminate', forbidden)


def test_framed_program_receives_cooperative_stop_and_exits(monkeypatch):
    _forbid_termination(monkeypatch)
    program = b"import sys\nassert COOP_STOP.wait(2)\nprint('synthetic-stop', sys.argv[1])\n"
    child = CooperativeChild(program=program, args=('synthetic-argument',))
    child.request_stop()
    outcome = child.wait(timeout=5)
    assert outcome == {'state': 'STOPPED', 'returncode': 0, 'output_valid': True, 'stop_sent': True}
    assert child.output() == (b'synthetic-stop synthetic-argument\r\n' if __import__('os').name == 'nt'
                              else b'synthetic-stop synthetic-argument\n', b'')


def test_timeout_retains_live_child_until_natural_exit(monkeypatch):
    _forbid_termination(monkeypatch)
    child = CooperativeChild(program=b"import time\ntime.sleep(0.4)\nprint('synthetic-done')\n")
    assert child.wait(timeout=0)['state'] == 'HOLD'
    with pytest.raises(ValueError, match='child still retained'):
        child.output()
    child.request_stop()  # This program deliberately does not consume STOP.
    outcome = child.wait(timeout=5)
    assert outcome['state'] == 'STOPPED' and outcome['returncode'] == 0
    assert child.output()[0].strip() == b'synthetic-done'


def test_output_limit_is_drained_but_not_published(monkeypatch):
    _forbid_termination(monkeypatch)
    child = CooperativeChild(program=b"import sys\nsys.stdout.buffer.write(b'x' * 70000)\n")
    result = child.wait(timeout=5)
    assert result['state'] == 'STOPPED' and result['returncode'] == 0
    assert result['output_valid'] is False
    with pytest.raises(ValueError, match='output unavailable'):
        child.output()


def test_failed_program_reports_real_exit_and_never_forces_stop(monkeypatch):
    _forbid_termination(monkeypatch)
    child = CooperativeChild(program=b"raise ValueError('synthetic failure')\n")
    outcome = child.wait(timeout=5)
    assert outcome['state'] == 'STOPPED' and outcome['returncode'] != 0
    assert b'synthetic failure' in child.output()[1]


@pytest.mark.parametrize('program', [b'', 'not-bytes', b'x' * (2 * 1024 * 1024 + 1)],
                         ids=['empty', 'wrong-type', 'oversize'])
def test_invalid_frame_is_refused_before_spawn(monkeypatch, program):
    def forbidden(*_args, **_kwargs):
        raise AssertionError('invalid input must not spawn')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='invalid synthetic program'):
        CooperativeChild(program=program)
