"""Recovery tests use synthetic generations and never touch the active fleet."""
import json
from pathlib import Path

import pytest

from tools import wd_q4_live_snapshot_publish as live
from tests.tools.test_wd_q4_live_snapshot_publish import (  # pytest fixtures
    case, cas_case, _cas_publish, do_select, sha)
from tests.tools.test_wd_q4_card_audit_orchestrator import (  # pytest fixtures
    attested, materialize, real_fixture, staged_producer)


def recover(c, **overrides):
    from tools import wd_q4_live_publication_recovery as recovery
    pending = c['root'] / live._PUBLICATION_PENDING
    args = dict(enabled=True, baseline_path=c['base_path'], baseline_sha256=c['base'][2],
                baseline=c['base'], candidate_path=c['candidate_path'], candidate_sha256=c['candidate'][2],
                candidate=c['candidate'], manifest_sha256=c['manifest_sha'],
                pending_sha256=sha(pending.read_bytes()), previous_selector_sha256=None)
    args.update(overrides)
    return recovery.recover_once(**args)


def interrupted(c, monkeypatch, after):
    old = live.pointer_io._atomic_replace
    def replace(*args):
        if after:
            old(*args)
        raise OSError('simulated crash')
    with monkeypatch.context() as m:
        m.setattr(live.pointer_io, '_atomic_replace', replace)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(c, expected_previous_selector_sha256=None)


def test_default_off():
    from tools import wd_q4_live_publication_recovery as recovery
    assert recovery.recover_once() == {'status': 'OFF'}


def test_owned_temp_is_cleaned_on_pre_replace_error_and_invalid_candidate_is_not_published(cas_case, monkeypatch):
    interrupted(cas_case, monkeypatch, False)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    temp = cas_case['root'] / json.loads(pending.read_bytes())['temporary_name']
    assert not temp.exists()
    Path(cas_case['candidate_path']).write_bytes(b'broken candidate')
    assert recover(cas_case)['status'] == 'NOT_PUBLISHED'
    assert not pending.exists()


def test_partial_temp_write_is_cleaned_by_its_live_owner(cas_case, monkeypatch):
    original = live.os.fdopen
    count = 0
    class FailWrite:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def fileno(self):
            return self.stream.fileno()
        def write(self, data):
            self.stream.write(data[:1])
            raise OSError('synthetic partial write')
    def open_stream(fd, mode):
        nonlocal count
        count += 1
        stream = original(fd, mode)
        return FailWrite(stream) if count == 2 else stream
    with monkeypatch.context() as patch:
        patch.setattr(live.os, 'fdopen', open_stream)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=None)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    assert not (cas_case['root'] / json.loads(pending.read_bytes())['temporary_name']).exists()
    assert recover(cas_case)['status'] == 'NOT_PUBLISHED'


def test_candidate_may_disappear_after_proven_prewrite_failure(cas_case, monkeypatch):
    real_open = live.os.open
    def fail_temp(path, *args, **kwargs):
        if str(path).endswith('.tmp'):
            raise OSError('simulated failure before create')
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(live.os, 'open', fail_temp)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=None)
    Path(cas_case['candidate_path']).unlink()
    assert recover(cas_case)['status'] == 'NOT_PUBLISHED'


def test_temp_fdopen_failure_closes_and_cleans_owned_temp(cas_case, monkeypatch):
    original = live.os.fdopen
    count = 0
    def fail_second(fd, mode):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError('synthetic stream setup failure')
        return original(fd, mode)
    with monkeypatch.context() as patch:
        patch.setattr(live.os, 'fdopen', fail_second)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=None)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    assert not (cas_case['root'] / json.loads(pending.read_bytes())['temporary_name']).exists()
    assert recover(cas_case)['status'] == 'NOT_PUBLISHED'


def test_after_replace_validates_and_unblocks(cas_case, monkeypatch):
    interrupted(cas_case, monkeypatch, True)
    assert recover(cas_case)['status'] == 'PUBLISHED'
    assert not (cas_case['root'] / live._PUBLICATION_PENDING).exists()
    assert do_select(cas_case)['status'] == 'SELECTED'


def test_before_replace_with_unproven_temp_stays_unknown(cas_case, monkeypatch):
    # Simulate an uncompleted cleanup (including abrupt process death).
    original_unlink = Path.unlink
    def fail_temp_unlink(path, *args, **kwargs):
        if str(path).endswith('.tmp'):
            raise OSError('synthetic cleanup interruption')
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'unlink', fail_temp_unlink)
        interrupted(cas_case, patch, False)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    marker = pending.read_bytes()
    temp = cas_case['root'] / json.loads(marker)['temporary_name']
    before = temp.read_bytes()
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert pending.read_bytes() == marker and temp.read_bytes() == before


def test_foreign_replacement_is_not_cleaned_by_publisher(cas_case, monkeypatch):
    def replace_with_foreign(temp, target):
        temp = Path(temp)
        temp.rename(temp.with_suffix('.owned-synthetic'))
        temp.write_bytes(b'foreign')
        raise OSError('synthetic foreign replacement')
    with monkeypatch.context() as patch:
        patch.setattr(live.pointer_io, '_atomic_replace', replace_with_foreign)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=None)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    temp = cas_case['root'] / json.loads(pending.read_bytes())['temporary_name']
    assert temp.read_bytes() == b'foreign'
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert temp.read_bytes() == b'foreign' and pending.exists()


def test_changed_selector_blocks_owner_cleanup(cas_case, monkeypatch):
    target = cas_case['root'] / live._POINTER
    def change_target(temp, _target):
        target.write_bytes(b'unrelated selector')
        raise OSError('synthetic concurrent change')
    with monkeypatch.context() as patch:
        patch.setattr(live.pointer_io, '_atomic_replace', change_target)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=None)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    temp = cas_case['root'] / json.loads(pending.read_bytes())['temporary_name']
    assert temp.exists() and target.read_bytes() == b'unrelated selector'
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)


def test_wrong_pending_pin_is_unknown_and_preserved(cas_case, monkeypatch):
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    before = pending.read_bytes()
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case, pending_sha256='0' * 64)
    assert pending.read_bytes() == before


def test_before_temp_creation_preserves_missing_selector(cas_case, monkeypatch):
    real_open = live.os.open
    def fail_temp(path, *args, **kwargs):
        if str(path).endswith('.tmp'):
            raise OSError('simulated failure before exclusive create')
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(live.os, 'open', fail_temp)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=None)
    assert recover(cas_case)['status'] == 'NOT_PUBLISHED'
    assert not (cas_case['root'] / live._POINTER).exists()


def test_before_temp_creation_preserves_pinned_previous_selector(cas_case, monkeypatch):
    prior = {'schema': live._SCHEMA, 'baseline_sha256': cas_case['base'][2],
             'generation': 'd' * 40, 'descriptor_sha256': 'a' * 64, 'index_pointer_sha256': 'b' * 64}
    raw = json.dumps(prior, sort_keys=True, separators=(',', ':')).encode('ascii')
    target = cas_case['root'] / live._POINTER
    target.write_bytes(raw)
    real_open = live.os.open
    def fail_temp(path, *args, **kwargs):
        if str(path).endswith('.tmp'):
            raise OSError('simulated failure before exclusive create')
        return real_open(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(live.os, 'open', fail_temp)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            _cas_publish(cas_case, expected_previous_selector_sha256=sha(raw))
    assert recover(cas_case, previous_selector_sha256=sha(raw))['status'] == 'NOT_PUBLISHED'
    assert target.read_bytes() == raw


def test_malformed_marker_remains_unknown_even_with_its_exact_pin(cas_case, monkeypatch):
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    item = json.loads(pending.read_bytes())
    item['unexpected'] = True
    bad = json.dumps(item).encode('ascii')
    pending.write_bytes(bad)
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert pending.read_bytes() == bad


def test_real_publication_lock_contention_preserves_pending(cas_case, monkeypatch):
    import multiprocessing
    from tests.tools.test_wd_q4_live_snapshot_publish import _hold_publication_lock
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    marker = pending.read_bytes()
    ctx = multiprocessing.get_context('spawn')
    ready, release = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_publication_lock,
                        args=(str(cas_case['root'] / live._PUBLICATION_LOCK), ready, release))
    child.start()
    try:
        assert ready.wait(15)
        with pytest.raises(live.LiveSnapshotOutcomeUnknown):
            recover(cas_case)
        assert pending.read_bytes() == marker
    finally:
        release.set()
        child.join(15)
    assert child.exitcode == 0


def test_foreign_selector_and_foreign_temp_are_never_removed(cas_case, monkeypatch):
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    marker = pending.read_bytes()
    target = cas_case['root'] / live._POINTER
    good = target.read_bytes()
    target.write_bytes(b'foreign')
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert target.read_bytes() == b'foreign' and pending.read_bytes() == marker
    target.write_bytes(good)
    temp = cas_case['root'] / json.loads(marker)['temporary_name']
    temp.write_bytes(b'foreign')
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert temp.read_bytes() == b'foreign' and pending.read_bytes() == marker


@pytest.mark.parametrize('field', ['baseline_sha256', 'candidate_sha256', 'manifest_sha256',
                                   'previous_selector_sha256'])
def test_wrong_caller_pins_preserve_pending(cas_case, monkeypatch, field):
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    marker = pending.read_bytes()
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case, **{field: '0' * 64})
    assert pending.read_bytes() == marker


def test_changed_pointer_and_interleaved_temp_leave_pending(cas_case, monkeypatch):
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    marker = pending.read_bytes()
    pointer = cas_case['index'] / 'q4-latest.json'
    original = pointer.read_bytes()
    pointer.write_bytes(b'changed')
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    pointer.write_bytes(original)
    temp = cas_case['root'] / json.loads(marker)['temporary_name']
    checked = live._checked_pair
    count = 0
    def race(*args, **kwargs):
        nonlocal count
        count += 1
        result = checked(*args, **kwargs)
        if count == 2:
            temp.write_bytes(b'other writer')
        return result
    monkeypatch.setattr(live, '_checked_pair', race)
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert temp.read_bytes() == b'other writer' and pending.read_bytes() == marker


def test_busy_lock_does_not_remove_marker(cas_case, monkeypatch):
    from tools import wd_q4_live_publication_recovery as recovery
    from sandbox.mind.step2.reader_slot import ReaderBusy
    interrupted(cas_case, monkeypatch, True)
    pending = cas_case['root'] / live._PUBLICATION_PENDING
    marker = pending.read_bytes()
    def busy(*args, **kwargs):
        raise ReaderBusy()
    monkeypatch.setattr(recovery, 'reader_slot', busy)
    with pytest.raises(live.LiveSnapshotOutcomeUnknown):
        recover(cas_case)
    assert pending.read_bytes() == marker


def test_actual_p1_binding_is_rechecked_and_legacy_is_unchanged(attested, tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    from tests.tools.test_wd_q4_two_card_snapshot_refresh import (
        test_newer_card_snapshot_from_appended_prefix_is_published_and_selected as real_chain)
    from tools import wd_q4_live_publication_recovery as recovery
    actual_publish = live.publish
    actual_replace = live.pointer_io._atomic_replace
    reconciled = []
    def publish_with_one_crash(**kwargs):
        if kwargs.get('p1_enabled') is not True or reconciled:
            return actual_publish(**kwargs)
        def crash_after_replace(*args):
            actual_replace(*args)
            raise OSError('synthetic after replace')
        with monkeypatch.context() as patch:
            patch.setattr(live.pointer_io, '_atomic_replace', crash_after_replace)
            with pytest.raises(live.LiveSnapshotOutcomeUnknown):
                actual_publish(**kwargs)
        pending = Path(kwargs['baseline_path']).parent.parent / live._PUBLICATION_PENDING
        args = {key: kwargs[key] for key in ('baseline_path', 'baseline_sha256', 'baseline',
                'candidate_path', 'candidate_sha256', 'candidate', 'manifest_sha256')}
        result = recovery.recover_once(enabled=True, **args, pending_sha256=sha(pending.read_bytes()),
            previous_selector_sha256=kwargs['expected_previous_selector_sha256'])
        reconciled.append(result)
        return result
    monkeypatch.setattr(live, 'publish', publish_with_one_crash)
    real_chain(attested, tmp_path, monkeypatch)
    assert [item['status'] for item in reconciled] == ['PUBLISHED']
