"""Synthetic-only reconciliation of an interrupted adoption; no live state."""
import json
from pathlib import Path

import pytest
from tests.tools.test_wd_q4_continuation_adopt import synthetic, actual_continuation
from tools import wd_q4_genesis_to_composer_cli as composer


def paths(args):
    route = composer._tuple_part(json.loads(args['genesis_tuple_bytes']), 'route')
    private = Path(route['private_state_root'])
    pointer = json.loads(args['receipt_bytes'])['pointer_sha256']
    generation = Path(args['baseline_path']).parent.parent / pointer
    return private, generation


def test_default_off_does_not_read_inputs(monkeypatch):
    from tools import wd_q4_continuation_adopt_recovery as recovery
    monkeypatch.setattr(recovery.adopt, '_pinned', lambda *_a: pytest.fail('opened input'))
    assert recovery.recover_once() == {'status': 'OFF'}


def test_interruption_after_publication_can_be_verified_and_reconciled(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    args = actual_continuation
    private, generation = paths(args)
    original = adopt.update._publish
    def interrupt(*pos, **kw):
        original(*pos, **kw)
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', interrupt)
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args)
    assert generation.is_dir()
    assert (private / 'q4-continuation-adopt.pending.json').is_file()
    from tools import wd_q4_continuation_adopt_recovery as recovery
    outcome = recovery.recover_once(**args)
    assert outcome['status'] == 'ADOPTED'
    assert not (private / 'q4-continuation-adopt.pending.json').exists()
    assert generation.is_dir()
    assert recovery.recover_once(**args) == {'status': 'NOT_NEEDED'}


def test_interruption_before_generation_can_be_reconciled_and_readopted(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, generation = paths(args)
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args)
    assert not generation.exists()
    decoy = generation.parent / ('0' * 64)
    decoy.mkdir()
    (decoy / 'keep.txt').write_bytes(b'synthetic decoy')
    decoy_temp = generation.parent / 'unrelated-selector.tmp'
    decoy_temp.write_bytes(b'synthetic temp')
    assert recovery.recover_once(**args)['status'] == 'NOT_ADOPTED'
    assert not (private / 'q4-continuation-adopt.pending.json').exists()
    assert (decoy / 'keep.txt').read_bytes() == b'synthetic decoy'
    assert decoy_temp.read_bytes() == b'synthetic temp'
    assert adopt.run_once(**args)['status'] == 'PUBLISHED'


def test_unowned_partial_generation_stays_unknown_and_untouched(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, generation = paths(args)
    def interrupt(*_a, **_k):
        generation.mkdir()
        (generation / 'unowned.txt').write_bytes(b'synthetic unowned data')
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', interrupt)
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args)
    marker = private / 'q4-continuation-adopt.pending.json'
    original = marker.read_bytes()
    with pytest.raises(recovery.RecoveryUnknown):
        recovery.recover_once(**args)
    assert marker.read_bytes() == original
    assert (generation / 'unowned.txt').read_bytes() == b'synthetic unowned data'


@pytest.mark.parametrize('fault', ['marker', 'receipt', 'baseline', 'watermark', 'pointer', 'selector', 'outer_pending'])
def test_conflicting_evidence_is_unknown_and_preserved(actual_continuation, monkeypatch, fault):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = dict(actual_continuation)
    private, generation = paths(args)
    route = composer._tuple_part(json.loads(args['genesis_tuple_bytes']), 'route')
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args)
    marker = private / 'q4-continuation-adopt.pending.json'
    if fault == 'marker':
        marker.write_bytes(marker.read_bytes() + b' ')
    elif fault == 'receipt':
        args['receipt_bytes'] = b'{}'
    elif fault == 'baseline':
        args['baseline_sha256'] = '0' * 64
    elif fault == 'watermark':
        (private / 'q4-cutoff-current.json').write_bytes(b'{}')
    elif fault == 'pointer':
        pointer = Path(route['checkpoint_root']) / 'q4-latest.json'
        pointer.write_bytes(pointer.read_bytes() + b' ')
    elif fault == 'selector':
        (generation.parent / 'q4-live-current.json').write_bytes(b'{"foreign":true}')
    elif fault == 'outer_pending':
        (private / 'q4-continuation-orchestrator-job.pending.json').write_bytes(b'foreign pending')
    before = marker.read_bytes()
    with pytest.raises(recovery.RecoveryUnknown):
        recovery.recover_once(**args)
    assert marker.read_bytes() == before
    assert not generation.exists()


def test_busy_owner_is_refused_without_touching_pending(actual_continuation, monkeypatch):
    import threading
    from sandbox.mind.step2.reader_slot import reader_slot
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, _ = paths(args)
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args)
    marker = private / 'q4-continuation-adopt.pending.json'
    before = marker.read_bytes()
    locked, release = threading.Event(), threading.Event()
    def owner():
        with reader_slot(private / 'q4-cutoff-owner.lock', timeout_seconds=1):
            locked.set()
            release.wait(5)
    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert locked.wait(2)
        with pytest.raises(recovery.RecoveryRefused):
            recovery.recover_once(**args)
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert marker.read_bytes() == before


@pytest.mark.parametrize('fault', ['descriptor', 'selector', 'interrupt'])
def test_published_selector_must_still_verify_before_marker_removal(actual_continuation, monkeypatch, fault):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args = actual_continuation
    private, generation = paths(args)
    original_publish = adopt.update._publish
    def interrupt_after_publication(*pos, **kw):
        original_publish(*pos, **kw)
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', interrupt_after_publication)
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**args)
    marker = private / 'q4-continuation-adopt.pending.json'
    marker_before = marker.read_bytes()
    selector = generation.parent / 'q4-live-current.json'
    selector_before = selector.read_bytes()
    if fault == 'descriptor':
        descriptor = generation / 'shared-session-config.v2.json'
        descriptor.write_bytes(descriptor.read_bytes() + b' ')
    elif fault == 'selector':
        document = json.loads(selector_before)
        document['descriptor_sha256'] = '0' * 64
        selector.write_bytes(json.dumps(document).encode())
        selector_before = selector.read_bytes()
    if fault == 'interrupt':
        original_verify = recovery.runner._verify_own
        calls = []
        def stop_before_unlink(path, expected, info):
            calls.append(path)
            if len(calls) == 2:
                raise KeyboardInterrupt()
            return original_verify(path, expected, info)
        monkeypatch.setattr(recovery.runner, '_verify_own', stop_before_unlink)
        with pytest.raises(recovery.RecoveryInterrupted):
            recovery.recover_once(**args)
    else:
        with pytest.raises(recovery.RecoveryUnknown):
            recovery.recover_once(**args)
    assert marker.read_bytes() == marker_before
    assert selector.read_bytes() == selector_before
    assert generation.is_dir()

@pytest.mark.parametrize('late', [False, True])
def test_recovery_job_pending_blocks_direct_recovery_and_retains_adoption(actual_continuation, monkeypatch, late):
    from contextlib import contextmanager
    from sandbox.mind.step2 import prefix_private_state
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery as recovery
    args=actual_continuation
    private, generation=paths(args)
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update, '_publish', lambda *_a, **_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    old=private/'q4-continuation-adopt.pending.json'
    original=old.read_bytes()
    job=private/'q4-adopt-recovery-job.pending.json'
    if not late:
        job.write_bytes(b'synthetic pending')
    else:
        lock=prefix_private_state._checkpoint_lock
        @contextmanager
        def job_arrives(root,timeout):
            with lock(root,timeout):
                job.write_bytes(b'synthetic pending')
                yield
        monkeypatch.setattr(prefix_private_state,'_checkpoint_lock',job_arrives)
    with pytest.raises(recovery.RecoveryUnknown): recovery.recover_once(**args)
    assert old.read_bytes()==original and job.read_bytes()==b'synthetic pending'
    assert not generation.exists()
