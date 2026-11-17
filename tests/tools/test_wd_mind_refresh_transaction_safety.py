"""Synthetic refusal injection into the real owner/adapter/rotator refresh."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_wd_mind_full_runtime import synthetic_bundle
from test_wd_mind_incremental_refresh_integration import SCRIPT, _prepared_bundle


ROOT = Path(__file__).resolve().parents[2]


INJECTION = r'''    if case == 'lock_acquire_race':
        import threading
        old_lock = old_handler._lock
        original_running_owner = runtime._FlatRequestHandler._running_owner
        paused, release_view = threading.Event(), threading.Event()
        calls, output, errors = [], [], []
        request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':'codex-lead-1',
                   'scope':'own', 'query':'current mission overview'}
        class ProxyLock:
            def acquire(self, *args, **kwargs):
                if threading.current_thread() is threading.main_thread():
                    raise RuntimeError('synthetic-acquire-error')
                return old_lock.acquire(*args, **kwargs)
            def release(self): return old_lock.release()
        def intercept_running(handler):
            original_running_owner(handler)
            if handler is old_handler and threading.current_thread() is view_thread:
                calls.append('running')
                if len(calls) == 2:
                    paused.set()
                    if not release_view.wait(60): raise RuntimeError('test-barrier-timeout')
        def invoke_view():
            try: output.append(old_view.handle(request))
            except BaseException as error: errors.append(error)
        view_thread = threading.Thread(target=invoke_view, daemon=False)
        old_handler._lock = ProxyLock()
        runtime._FlatRequestHandler._running_owner = intercept_running
        try:
            view_thread.start()
            assert paused.wait(60), 'view did not reach pre-response barrier'
            try: owner._refresh_generation(enabled=True)
            except runtime.FullRuntimeRefused: pass
            else: raise AssertionError('lock failure was accepted')
        finally:
            release_view.set()
            view_thread.join(timeout=30)
            runtime._FlatRequestHandler._running_owner = original_running_owner
            old_handler._lock = old_lock
        assert not view_thread.is_alive() and calls == ['running', 'running']
        assert owner._generation is generation
        assert owner.status()['state'] == 'HOLD' and owner.status()['owned'] is True
        assert not output and len(errors) == 1 and isinstance(errors[0], runtime.FullRuntimeRefused), (
            len(output), [type(error).__name__ for error in errors])
    elif case in ('lock_acquire_error', 'lock_acquire_input'):
        old_lock = old_handler._lock
        class FaultyLock:
            def acquire(self, **kwargs):
                if case == 'lock_acquire_input':
                    raise runtime._GenerationInputRefused('synthetic-acquire-refusal')
                raise RuntimeError('synthetic-acquire-error')
        old_handler._lock = FaultyLock()
        try:
            try: owner._refresh_generation(enabled=True)
            except runtime.FullRuntimeRefused: pass
            else: raise AssertionError('lock failure was accepted')
        finally:
            old_handler._lock = old_lock
        assert owner._generation is generation
        assert owner.status()['state'] == 'HOLD' and owner.status()['owned'] is True
        request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':'codex-lead-1',
                   'scope':'own', 'query':'current mission overview'}
        try: old_view.handle(request)
        except runtime.FullRuntimeRefused: pass
        else: raise AssertionError('view returned content after lock failure')
    elif case in ('stage_refusal', 'post_swap_input'):
        old_rotation_snapshot = owner._client._snapshot
        old_adapter_snapshot = owner._adapter._snapshot
        original_stage = runtime._stage_refresh_generation
        original_static_check = adapter._RotatingFlatAdapter._static_check
        injected = []
        def refuse_stage(current_owner):
            injected.append('stage')
            raise runtime._GenerationInputRefused('synthetic-stage-refusal')
        def refuse_after_swap(current_adapter):
            if owner._generation is not generation:
                injected.append('post_swap')
                raise runtime._GenerationInputRefused('synthetic-after-swap')
            return original_static_check(current_adapter)
        if case == 'stage_refusal': runtime._stage_refresh_generation = refuse_stage
        else: adapter._RotatingFlatAdapter._static_check = refuse_after_swap
        try:
            try: owner._refresh_generation(enabled=True)
            except runtime.FullRuntimeRefused: pass
            else: raise AssertionError('internal refusal was accepted')
        finally:
            runtime._stage_refresh_generation = original_stage
            adapter._RotatingFlatAdapter._static_check = original_static_check
        assert injected == [('stage' if case == 'stage_refusal' else 'post_swap')], injected
        assert owner._generation is generation
        assert owner._client._snapshot == old_rotation_snapshot
        assert owner._adapter._snapshot == old_adapter_snapshot
        assert owner.status()['state'] == 'HOLD' and owner.status()['owned'] is True
        request = {'schema':'wd.mind-flat-owner-request.v1', 'lane':'codex-lead-1',
                   'scope':'own', 'query':'current mission overview'}
        try: old_view.handle(request)
        except runtime.FullRuntimeRefused: pass
        else: raise AssertionError('view returned content after internal refusal')
    elif case == 'busy':
'''


@pytest.mark.parametrize('case', ['stage_refusal', 'post_swap_input', 'lock_acquire_error',
                                 'lock_acquire_input', 'lock_acquire_race'])
def test_refresh_internal_refusal_restores_generation_and_fences_stale_view(synthetic_bundle, case):
    digest, policy_sha = _prepared_bundle(synthetic_bundle)
    source = synthetic_bundle['source']
    source.write_bytes(b'\n'.join(source.read_bytes().splitlines()[:5]) + b'\n')
    old_view = "    old_view = old_handler.lane_view('codex-lead-1') if case == 'stale' else None"
    expanded_view = "    old_view = old_handler.lane_view('codex-lead-1') if case in ('stale','stage_refusal','post_swap_input','lock_acquire_error','lock_acquire_input','lock_acquire_race') else None"
    branch = "    if case == 'busy':\n"
    assert SCRIPT.count(old_view) == SCRIPT.count(branch) == 1
    script = SCRIPT.replace(old_view, expanded_view).replace(branch, INJECTION)
    result = subprocess.run([sys.executable, '-B', '-c', script, str(synthetic_bundle['bundle']),
        digest, policy_sha, str(ROOT / 'tests/tools'), str(source), case],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'status':'ok', 'case':case}
