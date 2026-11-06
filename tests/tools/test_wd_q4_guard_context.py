"""Synthetic guard-context protocol only; callbacks do not prove OS cleanup."""
import hashlib
import json
import threading

import pytest

from tools import wd_q4_guard_completion as completion


def new_guard(root, lane="codex-tools-1", identity="a" * 64, descriptor="b" * 64):
    runtime = root / "runtime"
    state = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state"
    state.mkdir(parents=True)
    raw = json.dumps({"runtime_root": str(runtime)}).encode("ascii")
    guard = completion.acquire_guard(fleet_bytes=raw, expected_fleet_sha256=hashlib.sha256(raw).hexdigest(),
        lane=lane, identity_sha256=identity, descriptor_sha256=descriptor)
    return guard, state / (lane + ".pending.json")


def all_done(ctx):
    for name in ("adapter", "attestation", "transport"):
        ticket = ctx._ticket(name)
        assert ctx._accept_ticket(name, ticket) is ticket
        ticket._begin()
        ticket._complete()


def test_same_guard_exact_context_and_distinct_owner_types(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    assert type(ctx) is completion._OwnerContext
    assert guard._accept_owner_context(ctx) is ctx
    assert ctx._binding == guard._binding
    assert type(ctx._ticket("transport")) is completion._TransportOwner
    assert type(ctx._ticket("attestation")) is completion._AttestationOwner
    assert type(ctx._ticket("adapter")) is completion._AdapterOwner
    assert len({id(ctx._ticket(name)) for name in ("adapter", "attestation", "transport")}) == 3
    assert marker.exists()


def test_outer_barrier_refuses_witness_until_all_owners_and_finish(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._finish()
    all_done(ctx)
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()  # completion callbacks alone are insufficient.
    ctx._finish()
    witness = guard._registry._issue()
    guard.release(witness)
    assert not marker.exists()
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._finish()


def test_cross_guard_forged_context_and_wrong_ticket_rejected(tmp_path):
    first, marker = new_guard(tmp_path / "one")
    second, other_marker = new_guard(tmp_path / "two", identity="c" * 64)
    ctx, other = first._owner_context(), second._owner_context()
    with pytest.raises(completion.GuardCompletionUnavailable):
        second._accept_owner_context(ctx)
    for value in (None, {}, object(), "context"):
        with pytest.raises(completion.GuardCompletionUnavailable):
            first._accept_owner_context(value)
    forged = object.__new__(completion._OwnerContext)
    with pytest.raises(completion.GuardCompletionUnavailable):
        first._accept_owner_context(forged)
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._accept_ticket("adapter", ctx._ticket("transport"))
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._accept_ticket("transport", other._ticket("transport"))
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._ticket("invented")
    assert marker.exists() and other_marker.exists()


def test_same_lane_and_pins_still_cannot_cross_call_boundaries(tmp_path):
    first, _ = new_guard(tmp_path / "one")
    second, _ = new_guard(tmp_path / "two")
    ctx, other = first._owner_context(), second._owner_context()
    assert ctx._binding[1:] == other._binding[1:]  # Lane and pins identical.
    assert ctx._binding[0] != other._binding[0]  # Distinct call identifiers.
    with pytest.raises(completion.GuardCompletionUnavailable):
        first._accept_owner_context(other)
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._accept_ticket("attestation", other._ticket("attestation"))


@pytest.mark.parametrize("name,owner_type", [
    ("transport", completion._TransportOwner), ("attestation", completion._AttestationOwner),
    ("adapter", completion._AdapterOwner)])
def test_same_type_forged_ticket_cannot_enter(name, owner_type, tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    forged = owner_type(guard._registry, guard._registry._owners[name])
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._accept_ticket(name, forged)
    with pytest.raises(completion.GuardCompletionUnavailable):
        forged._begin()
    assert marker.exists()


def test_replay_and_legacy_second_owner_issuance_refused(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._owner_context()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._transport_owner()
    ticket = ctx._ticket("transport")
    ticket._begin()
    with pytest.raises(completion.GuardCompletionUnavailable):
        ticket._begin()
    ticket._complete()
    with pytest.raises(completion.GuardCompletionUnavailable):
        ticket._complete()
    assert marker.exists()


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_outer_interruption_sets_irreversible_unknown_before_release(tmp_path, signal):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    all_done(ctx)
    try:
        raise signal()
    except signal:
        ctx._abort()
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._finish()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()
    assert marker.exists()


def test_abort_after_finish_before_release_invalidates_issued_witness(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    all_done(ctx)
    ctx._finish()
    witness = guard._registry._issue()
    ctx._abort()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard.release(witness)
    assert marker.exists()


def test_owner_unknown_after_finish_invalidates_witness(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    ticket = ctx._ticket("transport")
    all_done(ctx)
    ctx._finish()
    witness = guard._registry._issue()
    ticket._unknown()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard.release(witness)
    assert marker.exists()


def test_begin_is_single_winner_under_concurrent_replay(tmp_path):
    guard, marker = new_guard(tmp_path)
    ticket = guard._owner_context()._ticket("transport")
    barrier = threading.Barrier(9)
    results = []
    def attempt():
        barrier.wait()
        try:
            ticket._begin()
        except completion.GuardCompletionUnavailable:
            results.append("refused")
        else:
            results.append("started")
    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert results.count("started") == 1 and results.count("refused") == 7
    assert marker.exists()


def test_abort_racing_admission_always_blocks_completion(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    ticket = ctx._ticket("adapter")
    barrier = threading.Barrier(3)
    observed = []
    def begin():
        barrier.wait()
        try: ticket._begin()
        except completion.GuardCompletionUnavailable: observed.append("refused")
        else: observed.append("started")
    def abort():
        barrier.wait()
        ctx._abort()
    first, second = threading.Thread(target=begin), threading.Thread(target=abort)
    first.start(); second.start(); barrier.wait()
    first.join(timeout=5); second.join(timeout=5)
    assert not first.is_alive() and not second.is_alive() and observed in (["refused"], ["started"])
    with pytest.raises(completion.GuardCompletionUnavailable):
        ticket._complete()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()
    assert marker.exists()


def test_cancel_unstarted_releases_only_its_own_distinct_witness(tmp_path):
    guard, marker = new_guard(tmp_path / "one")
    other, other_marker = new_guard(tmp_path / "two")
    ctx = guard._owner_context()
    witness = guard._cancel_unstarted(ctx)
    assert type(witness) is completion._CancelUnstartedWitness
    assert guard._registry._done == set()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()
    with pytest.raises(completion.GuardCompletionUnavailable):
        other.release(witness)
    guard.release(witness)
    assert not marker.exists() and other_marker.exists()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard.release(witness)
    with pytest.raises(completion.GuardCompletionUnavailable):
        ctx._ticket("transport")


@pytest.mark.parametrize("stage", ["handoff", "adapter", "attestation", "transport", "unknown"])
def test_cancel_after_handoff_entry_or_unknown_is_fail_closed(tmp_path, stage):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    if stage == "handoff":
        ctx._handoff()
    elif stage == "unknown":
        ctx._abort()
    else:
        ctx._ticket(stage)._begin()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._cancel_unstarted(ctx)
    assert guard._registry._uncertain
    assert marker.exists()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._registry._issue()


def test_cancel_rejects_foreign_context_and_unregistered_witness(tmp_path):
    first, marker = new_guard(tmp_path / "one")
    second, other_marker = new_guard(tmp_path / "two")
    first_ctx, second_ctx = first._owner_context(), second._owner_context()
    with pytest.raises(completion.GuardCompletionUnavailable):
        first._cancel_unstarted(second_ctx)
    assert marker.exists() and other_marker.exists()
    witness = second._cancel_unstarted(second_ctx)
    with pytest.raises(completion.GuardCompletionUnavailable):
        second.release(completion._CancelUnstartedWitness())
    second.release(witness)
    assert not other_marker.exists()


def test_handoff_and_cancel_race_has_at_most_one_winner(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    barrier = threading.Barrier(3)
    outcomes = []
    def handoff():
        barrier.wait()
        try: ctx._handoff()
        except completion.GuardCompletionUnavailable: outcomes.append(("handoff", False))
        else: outcomes.append(("handoff", True))
    def cancel():
        barrier.wait()
        try: witness = guard._cancel_unstarted(ctx)
        except completion.GuardCompletionUnavailable: outcomes.append(("cancel", False))
        else: outcomes.append(("cancel", witness))
    threads = [threading.Thread(target=handoff), threading.Thread(target=cancel)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert sum(bool(value) for _, value in outcomes) == 1
    assert marker.exists()
    assert guard._registry._uncertain
    for kind, value in outcomes:
        if kind == "cancel" and value:
            with pytest.raises(completion.GuardCompletionUnavailable):
                guard.release(value)
            assert marker.exists()


def test_begin_after_cancel_invalidates_pending_cancellation(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    ticket = ctx._ticket("transport")
    witness = guard._cancel_unstarted(ctx)
    with pytest.raises(completion.GuardCompletionUnavailable):
        ticket._begin()
    assert guard._registry._uncertain
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard.release(witness)
    assert marker.exists()


def test_cancel_after_owner_completion_does_not_fake_cleanup(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    ticket = ctx._ticket("adapter")
    ticket._begin()
    ticket._complete()
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._cancel_unstarted(ctx)
    assert guard._registry._done == {"adapter"}
    assert guard._registry._uncertain and marker.exists()


def test_cancel_cannot_be_reissued_or_used_as_normal_witness(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    witness = guard._cancel_unstarted(ctx)
    assert not isinstance(witness, completion._Witness)
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard._cancel_unstarted(ctx)
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard.release(witness)
    assert guard._registry._uncertain and marker.exists()


def test_begin_and_cancel_race_fails_closed(tmp_path):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    ticket = ctx._ticket("adapter")
    barrier = threading.Barrier(3)
    results = []
    def begin():
        barrier.wait()
        try: ticket._begin()
        except completion.GuardCompletionUnavailable: results.append(("begin", False))
        else: results.append(("begin", True))
    def cancel():
        barrier.wait()
        try: witness = guard._cancel_unstarted(ctx)
        except completion.GuardCompletionUnavailable: results.append(("cancel", False))
        else: results.append(("cancel", witness))
    threads = [threading.Thread(target=begin), threading.Thread(target=cancel)]
    for thread in threads: thread.start()
    barrier.wait()
    for thread in threads: thread.join(timeout=5)
    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 2 and sum(bool(value) for _, value in results) == 1
    assert guard._registry._uncertain and marker.exists()
    for name, value in results:
        if name == "cancel" and value:
            with pytest.raises(completion.GuardCompletionUnavailable):
                guard.release(value)


@pytest.mark.parametrize("access", ["ticket", "accept_ticket", "accept_context", "require_done", "finish"])
def test_late_context_access_invalidates_pending_cancel(tmp_path, access):
    guard, marker = new_guard(tmp_path)
    ctx = guard._owner_context()
    ticket = ctx._ticket("adapter")
    witness = guard._cancel_unstarted(ctx)
    with pytest.raises(completion.GuardCompletionUnavailable):
        if access == "ticket": ctx._ticket("adapter")
        elif access == "accept_ticket": ctx._accept_ticket("adapter", ticket)
        elif access == "accept_context": guard._accept_owner_context(ctx)
        elif access == "require_done": ctx._require_done(("adapter",))
        else: ctx._finish()
    assert guard._registry._uncertain
    with pytest.raises(completion.GuardCompletionUnavailable):
        guard.release(witness)
    assert marker.exists()
