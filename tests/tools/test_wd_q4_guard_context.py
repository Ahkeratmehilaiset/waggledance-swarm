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
