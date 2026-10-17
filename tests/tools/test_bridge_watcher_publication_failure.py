# SPDX-License-Identifier: BUSL-1.1
"""A failed wake publication must not end the watcher (operator 2026-10-12, #1870 follow-up).

Write-BridgeWakeObservation throws after its fifth IOException, and at once on any other exception.
Watch-Bridge.ps1 runs under ErrorActionPreference Stop, so one such throw used to end the watcher and
the lane stopped waking. Now the watcher warns once per failure streak, keeps the batch pending and
publishes it on a later poll, also on a poll that reads no new rows.
"""
import json
import os
import subprocess
import threading
import time

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT

AGENT = 'codex-lead-1'
FAILED = 'wake publication failed'
RECOVERED = 'wake published after'
ENV = dict(os.environ, WAGGLE_BRIDGE_WAKE_ENABLED='1')


def _request(request_id):
    return dict(ts_utc='2026-10-12T15:00:00.0000000Z', agent='operator', to=AGENT, type='message',
                status='review_requested', task_id='watcher-alive-fixture', request_id=request_id,
                session_id='operator-session')


def _runtime(tmp_path, *events):
    (tmp_path / 'shared').mkdir()
    _append(tmp_path, *events)
    return tmp_path


def _append(runtime, *events):
    with open(runtime / 'shared/events.jsonl', 'a', encoding='utf-8', newline='\n') as log:
        log.write(''.join(json.dumps(event) + '\n' for event in events))


def _blocked_wake(tmp_path):
    # A directory where the sentinel belongs: every publication attempt fails.
    wake = tmp_path / f'wake_{AGENT}'
    wake.mkdir()
    (wake / 'keep').write_text('x', encoding='utf-8')
    return wake


def _argv(ps, runtime, iterations, poll_ms):
    return [ps, '-NoProfile', '-NonInteractive', '-File', str(ROOT / '.agent-bridge/bin/Watch-Bridge.ps1'),
            '-Agent', AGENT, '-RuntimeRoot', str(runtime), '-StartLineCount', '0',
            '-MaxIterations', str(iterations), '-PollIntervalMs', str(poll_ms), '-DebounceMs', '1']


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_a_lasting_publication_failure_keeps_the_watcher_alive(tmp_path, ps):
    runtime = _runtime(tmp_path, _request('req-1'))
    wake = _blocked_wake(tmp_path)

    result = subprocess.run(_argv(ps, runtime, 4, 1), capture_output=True, text=True, timeout=120, env=ENV)

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count(FAILED) == 1, output  # one warning per failure streak, not one per poll
    assert RECOVERED not in output, output
    assert wake.is_dir()


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
@pytest.mark.parametrize('new_row', [False, True], ids=['idle-poll', 'new-row'])
def test_the_pending_wake_is_published_once_publication_works_again(tmp_path, ps, new_row):
    runtime = _runtime(tmp_path, _request('req-1'))
    wake = _blocked_wake(tmp_path)
    process = subprocess.Popen(_argv(ps, runtime, 6, 1500), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=ENV)
    lines = []
    failed = threading.Event()

    def pump():
        for line in process.stdout:
            lines.append(line)
            if FAILED in line:
                failed.set()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        assert failed.wait(60), ''.join(lines)
        if new_row:
            _append(runtime, _request('req-2'))
        (wake / 'keep').unlink()
        wake.rmdir()  # publication works again
        assert process.wait(60) == 0, ''.join(lines)
    finally:
        if process.poll() is None:
            process.kill()
    reader.join(10)

    output = ''.join(lines)
    assert output.count(FAILED) == 1, output
    assert RECOVERED in output, output
    observation = json.loads(wake.read_text(encoding='utf-8'))
    expected = ['req-1', 'req-2'] if new_row else ['req-1']
    assert [request['request_id'] for request in observation['requests']] == expected, observation


def _wait_for(predicate, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
def test_a_new_failure_streak_after_a_recovery_warns_again(tmp_path, ps):
    runtime = _runtime(tmp_path, _request('req-1'))
    wake = _blocked_wake(tmp_path)
    process = subprocess.Popen(_argv(ps, runtime, 10, 1000), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=ENV)
    lines = []
    failures = threading.Semaphore(0)

    def pump():
        for line in process.stdout:
            lines.append(line)
            if FAILED in line:
                failures.release()

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        assert failures.acquire(timeout=60), ''.join(lines)
        (wake / 'keep').unlink()
        wake.rmdir()
        assert _wait_for(wake.is_file, 30), ''.join(lines)  # the first streak recovered
        wake.unlink()
        _blocked_wake(tmp_path)
        _append(runtime, _request('req-2'))
        assert failures.acquire(timeout=30), ''.join(lines)  # the second streak warns again
        (wake / 'keep').unlink()
        wake.rmdir()
        assert process.wait(60) == 0, ''.join(lines)
    finally:
        if process.poll() is None:
            process.kill()
    reader.join(10)

    output = ''.join(lines)
    assert output.count(FAILED) == 2, output
    assert output.count(RECOVERED) == 2, output
    observation = json.loads(wake.read_text(encoding='utf-8'))
    # The first batch was cleared when it was published; only the second one is pending.
    assert [request['request_id'] for request in observation['requests']] == ['req-2'], observation
