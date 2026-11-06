"""Lifecycle tests; only one explicitly marked case uses real Windows handles."""
import hashlib
import os
import threading
import pytest

from tools import wd_q4_fixed_parent_attestation as attestation


class Kernel:
    def __init__(self, failing=()):
        self.failing = set(failing)
        self.closed = []

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return handle not in self.failing


def make_owner(monkeypatch, *, failing=()):
    events = []
    kernel = Kernel(failing)
    handles = iter((101, 102, 103))

    def open_fake(path, expected_sha256, *, max_bytes, enabled):
        assert enabled is True
        return attestation.HeldFile(kernel, next(handles), expected_sha256, 1)

    monkeypatch.setattr(attestation, "attest_file", open_fake)
    monkeypatch.setattr(attestation, "_QUARANTINED", [])
    owner = attestation._AttestationOwner(
        on_complete=lambda: events.append("complete"),
        on_unknown=lambda: events.append("unknown"),
    )
    return owner, kernel, events


def acquire(owner):
    return owner.attest("synthetic", "a" * 64, max_bytes=10, enabled=True)


def test_only_completes_after_admission_sealed_and_all_three_leases_close(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    with acquire(owner), acquire(owner), acquire(owner):
        owner.finish()
        assert events == []
    assert kernel.closed == [103, 102, 101]
    assert events == ["complete"]
    with pytest.raises(attestation.AttestationRefused):
        acquire(owner)
    assert events == ["complete"]


def test_closed_leases_without_admission_seal_do_not_complete(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    with acquire(owner):
        pass
    assert kernel.closed == [101] and events == []
    owner.finish()
    assert events == ["complete"]


def test_partial_close_does_not_complete(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    first, second = acquire(owner), acquire(owner)
    owner.finish()
    first.close()
    assert kernel.closed == [101] and events == []
    second.close()
    assert kernel.closed == [101, 102] and events == ["complete"]


def test_abandoned_open_lease_is_unknown_and_never_completes(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    lease = acquire(owner)
    owner.finish()
    owner.abort()
    lease.close()
    assert events == ["unknown"]


def test_late_failure_revokes_previously_signalled_completion(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    with acquire(owner):
        owner.finish()
    assert events == ["complete"]
    owner.abort()
    assert events == ["complete", "unknown"]


def test_no_attestation_cannot_produce_completion(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    with pytest.raises(attestation.AttestationRefused):
        owner.finish()
    assert events == ["unknown"]


def test_failed_native_close_is_unknown_and_never_completes(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch, failing=(102,))
    first, second = acquire(owner), acquire(owner)
    owner.finish()
    first.close()
    with pytest.raises(attestation.AttestationCleanupUnknown):
        second.close()
    assert kernel.closed == [101, 102] and events == ["unknown"]


def test_unowned_external_close_is_not_counted_as_owner_completion(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    lease = acquire(owner)
    owner.finish()
    lease._lease.close()  # Simulate another owner stealing the close.
    with pytest.raises(attestation.AttestationRefused):
        lease.close()
    assert kernel.closed == [101] and events == ["unknown"]


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_body_exception_or_interrupt_is_unknown_but_closes_lease(monkeypatch, error):
    owner, kernel, events = make_owner(monkeypatch)
    with pytest.raises(error):
        with acquire(owner):
            owner.finish()
            raise error("synthetic")
    assert kernel.closed == [101] and events == ["unknown"]


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_interrupted_admission_is_unknown(monkeypatch, error):
    owner, _, events = make_owner(monkeypatch)
    def interrupted(*args, **kwargs):
        raise error("synthetic")
    monkeypatch.setattr(attestation, "attest_file", interrupted)
    with pytest.raises(error):
        acquire(owner)
    assert events == ["unknown"]


def test_completion_callback_failure_reports_unknown(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    def fail():
        raise RuntimeError("synthetic")
    owner._on_complete = fail
    with acquire(owner):
        pass
    with pytest.raises(RuntimeError):
        owner.finish()
    assert events == ["unknown"]


def test_ordinary_attestation_refusal_is_conservatively_unknown(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    def refuse(*args, **kwargs):
        raise attestation.AttestationRefused("attestation_refused")
    monkeypatch.setattr(attestation, "attest_file", refuse)
    with pytest.raises(attestation.AttestationRefused):
        acquire(owner)
    assert events == ["unknown"]
    with pytest.raises(attestation.AttestationRefused):
        owner.finish()


def test_cleanup_unknown_during_admission_is_unknown(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    lease = attestation.HeldFile(Kernel(), 901, "a" * 64, 1)
    def refuse(*args, **kwargs):
        raise attestation.AttestationCleanupUnknown(lease)
    monkeypatch.setattr(attestation, "attest_file", refuse)
    with pytest.raises(attestation.AttestationCleanupUnknown):
        acquire(owner)
    assert events == ["unknown"]


def test_wrapper_construction_failure_closes_and_marks_unknown(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic wrapper failure")
    monkeypatch.setattr(attestation, "_OwnedAttestationLease", fail)
    with pytest.raises(RuntimeError):
        acquire(owner)
    assert kernel.closed == [101] and events == ["unknown"]


def test_unknown_callback_failure_still_closes_held_lease(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    def fail():
        events.append("unknown_attempt")
        raise RuntimeError("unknown callback failed")
    owner._on_unknown = fail
    with pytest.raises(RuntimeError, match="unknown callback failed"):
        with acquire(owner):
            owner.finish()
            raise ValueError("body failed")
    assert kernel.closed == [101] and events == ["unknown_attempt"]
    with pytest.raises(attestation.AttestationRefused):
        owner.finish()


def test_abort_before_any_lease_is_unknown(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    owner.abort()
    with pytest.raises(attestation.AttestationRefused):
        acquire(owner)
    assert events == ["unknown"]


def test_cross_thread_last_close_completes_once(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    leases = [acquire(owner) for _ in range(3)]
    owner.finish()
    barrier = threading.Barrier(4)
    errors = []
    def close(lease):
        try:
            barrier.wait(timeout=5)
            lease.close()
        except BaseException as exc:
            errors.append(exc)
    threads = [threading.Thread(target=close, args=(lease,)) for lease in leases]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
    assert not any(thread.is_alive() for thread in threads)
    assert not errors and sorted(kernel.closed) == [101, 102, 103]
    assert events == ["complete"]


def test_blocked_completion_callback_does_not_hold_global_attestation_lock(monkeypatch):
    owner, _, events = make_owner(monkeypatch)
    entered, release, aborted = threading.Event(), threading.Event(), threading.Event()
    def blocking_complete():
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test release timed out")
    owner._on_complete = blocking_complete
    lease = acquire(owner)
    owner.finish()
    errors = []
    def close():
        try:
            lease.close()
        except BaseException as exc:
            errors.append(exc)
    def abort():
        try:
            owner.abort()
        finally:
            aborted.set()
    first = threading.Thread(target=close)
    second = threading.Thread(target=abort)
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        proceeded = aborted.wait(1)
    finally:
        release.set()
        first.join(timeout=5)
        if second.ident is not None:
            second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert proceeded, "abort was blocked by callback under global lock"
    assert events == ["unknown"]
    assert len(errors) == 1 and isinstance(errors[0], attestation.AttestationRefused)


def test_reentrant_abort_from_completion_refuses_completion(monkeypatch):
    owner, kernel, events = make_owner(monkeypatch)
    def complete():
        owner.abort()
    owner._on_complete = complete
    with pytest.raises(attestation.AttestationRefused):
        with acquire(owner):
            owner.finish()
    assert kernel.closed == [101] and events == ["unknown"]


def test_blocked_unknown_callback_does_not_hold_global_lock(monkeypatch):
    owner, _, _ = make_owner(monkeypatch)
    other, other_kernel, other_events = make_owner(monkeypatch)
    entered, release, other_finished = threading.Event(), threading.Event(), threading.Event()
    def blocking_unknown():
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test release timed out")
    owner._on_unknown = blocking_unknown
    errors = []
    def other_work():
        try:
            with acquire(other):
                other.finish()
        except BaseException as exc:
            errors.append(exc)
        finally:
            other_finished.set()
    first = threading.Thread(target=owner.abort)
    second = threading.Thread(target=other_work)
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        proceeded = other_finished.wait(1)
    finally:
        release.set()
        first.join(timeout=5)
        if second.ident is not None:
            second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive()
    assert proceeded, "unrelated owner was blocked by unknown callback under global lock"
    assert not errors and other_kernel.closed == [101] and other_events == ["complete"]


def test_unknown_wins_against_late_registry_completion(monkeypatch):
    from tools import wd_q4_guard_completion as guard

    owner, _, _ = make_owner(monkeypatch)
    registry = guard._Registry(("synthetic",))
    capability = registry._owners["attestation"]
    registry._complete_owner(registry._owners["transport"])
    registry._complete_owner(registry._owners["adapter"])
    entered, release = threading.Event(), threading.Event()
    def delayed_complete():
        entered.set()
        if not release.wait(5):
            raise RuntimeError("test release timed out")
        registry._complete_owner(capability)
    owner._on_complete = delayed_complete
    owner._on_unknown = registry._unknown
    lease = acquire(owner)
    owner.finish()
    errors = []
    def close():
        try:
            lease.close()
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=close)
    thread.start()
    try:
        assert entered.wait(5)
        owner.abort()
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], guard.GuardCompletionUnavailable)
    assert registry._uncertain is True
    with pytest.raises(guard.GuardCompletionUnavailable):
        registry._issue()


@pytest.mark.skipif(os.name != "nt", reason="real Windows handle admission")
def test_three_real_windows_leases_hold_and_release_files(tmp_path):
    events = []
    owner = attestation._AttestationOwner(
        on_complete=lambda: events.append("complete"),
        on_unknown=lambda: events.append("unknown"),
    )
    paths = [tmp_path / f"synthetic-{i}.bin" for i in range(3)]
    for path in paths:
        path.write_bytes(b"synthetic")
    digest = hashlib.sha256(b"synthetic").hexdigest()
    with owner.attest(str(paths[0]), digest, max_bytes=20, enabled=True), \
         owner.attest(str(paths[1]), digest, max_bytes=20, enabled=True), \
         owner.attest(str(paths[2]), digest, max_bytes=20, enabled=True):
        owner.finish()
        for path in paths:
            with pytest.raises(PermissionError):
                path.write_bytes(b"blocked")
        assert events == []
    assert events == ["complete"]
    for path in paths:
        path.write_bytes(b"closed")
