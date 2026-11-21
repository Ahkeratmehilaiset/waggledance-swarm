"""Synthetic ownership assertions; never start or terminate an OS process."""
import subprocess
import pytest
import wd_mind_owned_compat as owner


def test_normal_result_is_returned_after_both_pipes_drain(monkeypatch):
    class Process:
        returncode = 0
        def communicate(self, *, input=None, timeout=None):
            assert input == 'synthetic' and 0 < timeout <= 3
            return 'out', 'err'
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: Process())
    result = owner.run_owned(['synthetic'], input='synthetic', text=True, timeout=3)
    assert (result.returncode, result.stdout, result.stderr) == (0, 'out', 'err')


def test_timeout_retains_child_until_eof_and_rejects_late_result(monkeypatch, capsys):
    class Process:
        returncode = 0
        calls = 0
        def communicate(self, *, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired('synthetic', timeout)
            if self.calls == 2:
                raise OSError('synthetic pipe uncertainty')
            return 'late output', ''
        def kill(self): raise AssertionError('kill forbidden')
        def terminate(self): raise AssertionError('terminate forbidden')
    process = Process()
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(owner.time, 'sleep', lambda _: None)
    with pytest.raises(AssertionError, match='late output discarded'):
        owner.run_owned(['synthetic'], text=True, timeout=.01)
    assert process.calls == 3 and 'HOLD' in capsys.readouterr().out


@pytest.mark.parametrize('finished', [101.0, 101.1])
def test_result_returned_after_absolute_deadline_is_not_accepted(monkeypatch, finished):
    clock = iter([100.0, 100.2, finished])
    monkeypatch.setattr(owner.time, 'monotonic', lambda: next(clock))
    class Process:
        returncode = 0
        def communicate(self, *, input=None, timeout=None):
            assert abs(timeout - 0.8) < 1e-9
            return 'late output', ''
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: Process())
    with pytest.raises(AssertionError, match='late output discarded'):
        owner.run_owned(['synthetic'], text=True, timeout=1)


@pytest.mark.parametrize('signal', [KeyboardInterrupt, SystemExit])
def test_cancel_reraised_only_after_exit_and_eof(monkeypatch, signal):
    failure = signal('synthetic cancellation')
    calls = []
    class Process:
        def communicate(self, *, input=None, timeout=None):
            calls.append('drain')
            if len(calls) == 1: raise failure
            return 'late', ''
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: Process())
    with pytest.raises(signal) as captured:
        owner.run_owned(['synthetic'], text=True, timeout=1)
    assert captured.value is failure and calls == ['drain', 'drain']


def test_input_failure_is_reraised_after_drain(monkeypatch):
    failure = TypeError('synthetic input type')
    calls = []
    class Process:
        def communicate(self, *, input=None, timeout=None):
            calls.append('drain')
            if len(calls) == 1: raise failure
            return '', ''
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: Process())
    with pytest.raises(TypeError) as captured:
        owner.run_owned(['synthetic'], input=object(), timeout=1)
    assert captured.value is failure and calls == ['drain', 'drain']


def test_cancellation_during_timeout_drain_is_not_swallowed(monkeypatch):
    failure = KeyboardInterrupt('synthetic cancellation')
    calls = []
    class Process:
        def communicate(self, *, input=None, timeout=None):
            calls.append('drain')
            if len(calls) == 1: raise subprocess.TimeoutExpired('synthetic', timeout)
            if len(calls) == 2: raise failure
            return '', ''
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: Process())
    monkeypatch.setattr(owner.time, 'sleep', lambda _: None)
    with pytest.raises(KeyboardInterrupt) as captured:
        owner.run_owned(['synthetic'], timeout=1)
    assert captured.value is failure and len(calls) == 3


def test_cancellation_during_hold_notification_waits_for_drain(monkeypatch):
    import builtins
    failure = KeyboardInterrupt('synthetic print cancellation')
    calls = []
    class Process:
        def communicate(self, *, input=None, timeout=None):
            calls.append('drain')
            if len(calls) == 1: raise subprocess.TimeoutExpired('synthetic', timeout)
            if len(calls) == 2: raise OSError('synthetic drain uncertainty')
            return '', ''
    def interrupted_print(*args, **kwargs): raise failure
    monkeypatch.setattr(owner.subprocess, 'Popen', lambda *args, **kwargs: Process())
    monkeypatch.setattr(builtins, 'print', interrupted_print)
    monkeypatch.setattr(owner.time, 'sleep', lambda _: None)
    with pytest.raises(KeyboardInterrupt) as captured:
        owner.run_owned(['synthetic'], timeout=1)
    assert captured.value is failure and len(calls) == 3


def test_ambiguous_spawn_enters_hold_without_faking_a_result(monkeypatch):
    class ObservedHold(BaseException): pass
    marker = ObservedHold('synthetic hold sentinel')
    def uncertain(*args, **kwargs): raise OSError('synthetic spawn failure')
    def inspect_hold(): raise marker
    monkeypatch.setattr(owner.subprocess, 'Popen', uncertain)
    monkeypatch.setattr(owner, '_hold_ambiguous_spawn', inspect_hold)
    with pytest.raises(ObservedHold) as captured:
        owner.run_owned(['synthetic'], timeout=1)
    assert captured.value is marker


@pytest.mark.parametrize('timeout', [0, -1, True, float('nan'), float('inf'), '1'])
def test_invalid_timeout_refuses_before_launch(monkeypatch, timeout):
    def unexpected(*args, **kwargs): raise AssertionError('spawned unexpectedly')
    monkeypatch.setattr(owner.subprocess, 'Popen', unexpected)
    with pytest.raises(ValueError):
        owner.run_owned(['synthetic'], timeout=timeout)
