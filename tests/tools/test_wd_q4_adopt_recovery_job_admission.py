"""Only synthetic job quarantine markers and pinned per-writer admission paths."""
import json
import os
from contextlib import contextmanager
from pathlib import Path
import pytest
from tests.tools.test_wd_q4_status_prefix_runner import case, runner, fake_child, child_report
from tests.tools.test_wd_q4_cutoff_watermark import fixture as watermark_fixture, route as wm_route, sha as wm_sha
from tests.tools.test_wd_q4_continuation_adopt import synthetic, actual_continuation
from tests.tools.test_wd_q4_continuation_orchestrator import real_synthetic, genesis_synthetic
from tools import wd_q4_cutoff_watermark as watermark
from tools import wd_q4_continuation_adopt as adopt
from tools import wd_q4_genesis_to_composer_cli as composer
from sandbox.mind.step2 import prefix_private_state
from tools import wd_q4_live_update_once as update

JOB = 'q4-adopt-recovery-job.pending.json'

@pytest.mark.skipif(os.name != 'nt', reason='pair-state admission requires fixed Windows volumes')
def test_runner_rejects_job_pending_before_reservation(case, monkeypatch):
    runner, args, _, shared, private, _ = case
    marker=private/JOB; marker.write_bytes(b'synthetic pending')
    calls=[]; fake_child(monkeypatch, lambda **kw: calls.append(kw) or child_report())
    with pytest.raises(runner.StatusRunnerRefused): runner.run_once(**args())
    assert not calls and marker.read_bytes()==b'synthetic pending'
    assert not (private/runner._PENDING).exists() and not (shared/runner._PENDING).exists()

@pytest.mark.skipif(os.name != 'nt', reason='pair-state admission requires fixed Windows volumes')
def test_runner_rechecks_job_marker_in_pair_lock(case, monkeypatch):
    runner, args, _, shared, private, _ = case
    marker=private/JOB; calls=[]; stages=[]
    fake_child(monkeypatch, lambda **kw: calls.append(kw) or child_report())
    original=prefix_private_state._checkpoint_lock
    @contextmanager
    def arrive_before_admission(root, timeout):
        with original(root, timeout):
            stages.append('entered')
            marker.write_bytes(b'synthetic pending')
            stages.append('injected')
            yield
    monkeypatch.setattr(prefix_private_state, '_checkpoint_lock', arrive_before_admission)
    with pytest.raises(runner.StatusRunnerRefused): runner.run_once(**args())
    assert stages == ['entered', 'injected']
    assert not calls and marker.read_bytes()==b'synthetic pending'
    assert not (private/runner._PENDING).exists() and not (shared/runner._PENDING).exists()

def test_watermark_rejects_job_pending_before_reservation(watermark_fixture):
    private, shared, prefix, pointer, pointer_raw=watermark_fixture
    marker=private/JOB; marker.write_bytes(b'synthetic pending')
    raw=wm_route(prefix,'2026-11-07T10:00:00Z',private,shared)
    with pytest.raises(watermark.WatermarkUnknown):
        with watermark.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=raw,
                expected_route_sha256=wm_sha(raw),expected_pointer_sha256=pointer,
                expected_previous_record_sha256=None):
            pytest.fail('recovery job marker ignored')
    assert marker.read_bytes()==b'synthetic pending' and not (private/'q4-cutoff.pending.json').exists()
    assert (shared/'q4-latest.json').read_bytes()==pointer_raw

def test_watermark_rechecks_job_marker_in_pair_lock(watermark_fixture, monkeypatch):
    private, shared, prefix, pointer, pointer_raw=watermark_fixture
    marker=private/JOB; raw=wm_route(prefix,'2026-11-07T10:00:00Z',private,shared)
    original=prefix_private_state._checkpoint_lock
    @contextmanager
    def arrive_before_admission(root, timeout):
        with original(root, timeout):
            marker.write_bytes(b'synthetic pending')
            yield
    monkeypatch.setattr(prefix_private_state, '_checkpoint_lock', arrive_before_admission)
    with pytest.raises(watermark.WatermarkUnknown):
        with watermark.reserve(enabled=True, private_root=private, shared_root=shared, route_bytes=raw,
                expected_route_sha256=wm_sha(raw), expected_pointer_sha256=pointer,
                expected_previous_record_sha256=None):
            pytest.fail('recovery job marker ignored')
    assert marker.read_bytes()==b'synthetic pending' and not (private/'q4-cutoff.pending.json').exists()
    assert (shared/'q4-latest.json').read_bytes()==pointer_raw

def test_adopt_rejects_job_pending_before_reservation(actual_continuation):
    args=actual_continuation
    route=composer._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    private=Path(route['private_state_root']); marker=private/JOB; marker.write_bytes(b'synthetic pending')
    with pytest.raises((adopt.AdoptRefused, adopt.AdoptOutcomeUnknown)): adopt.run_once(**args)
    assert marker.read_bytes()==b'synthetic pending'
    assert not (private/'q4-continuation-adopt.pending.json').exists()

def test_orchestrator_rejects_job_pending_before_reservation(real_synthetic):
    module, _, private, _, _, seen=real_synthetic
    marker=private/JOB; marker.write_bytes(b'synthetic pending')
    with pytest.raises((module.OrchestrationRefused,module.OrchestrationUnknown)):
        module.run_once(enabled=True,template_sha256='a'*64,cutoff_utc='2026-10-31T13:02:00Z')
    assert not seen and marker.read_bytes()==b'synthetic pending'
    assert not (private/'q4-continuation-orchestrator.pending.json').exists()


