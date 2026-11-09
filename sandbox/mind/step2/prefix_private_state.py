"""Default-off private prefix continuity state for cooperating publishers.

The caller separately authenticates directory ownership and access control.
This is not protection from a hostile same-user process or filesystem TOCTOU.
An unclosed pending marker always requires external reconciliation.

Opt-in recoverable intent (begin_intent/resolve_intent, each enabled=True, under the
same private-state lock): the pending slot holds, instead of the generic marker, an
intent binding the exact prior pointer digest (None = genesis), the prepared attempt
pointer digest (e.g. a prepare_prefix_* attempt_sha256 later passed as
expected_attempt_sha256), the prior and the candidate prefix pin. It is created
crash-safe (fsynced unique stage, then a hard link that fails if the slot exists), so a
torn write never leaves a partial intent. begin_intent and resolve_intent hold the
cooperating checkpoint lock (lock order: private-state lock, then checkpoint lock, as
in publication) from the shared pointer observation through every decision mutation
(slot creation; state move and slot removal). Resolution answers "landed" (pointer ==
attempt; the private state is moved to the candidate and the intent closed) or
"unknown" (pointer == prior; NOTHING changes and the intent stays open, because an
ordinary checkpoint-only writer that never takes the private lock could still land the
same attempt later and cannot be fenced from here; the open intent then still catches
it); a third pointer, a legacy marker, a malformed intent or a private state matching
neither side refuses and keeps the slot. Clearing an attempt that never lands needs
external reconciliation, as with the legacy marker. Legacy methods refuse while an
intent is open. A no_change empty advance (attempt == prior) cannot use an intent.

Opt-in fenced intent (begin_intent(..., fence=True), candidate, unwired): the intent is
bound to an open checkpoint fence (shared_index_checkpoint.open_prefix_fence) and resolved
through resolve_prefix_fence; "not_landed" keeps the intent open (old writers are not
fenced and power-loss durability of the fence metadata is unproven). The publishing
caller must commit with fenced=True; the producer does not do that yet.
"""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets

from sandbox.mind.step2 import shared_index_checkpoint as cp
from sandbox.mind.step2.reader_slot import reader_slot
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

_STATE = "prefix-private-state.json"
_PENDING = "prefix-private-state.pending"
_LOCK = "prefix-private-state.lock"
_MARK = b"wd-prefix-private-pending-v1\n"


class StateRefused(ValueError):
    """Fixed, path-free refusal; state bytes are private."""


def _fail():
    raise StateRefused("private_state_refused") from None


def _pin(value):
    if type(value) is not BridgePrefixPin or type(value.sha256) is not str or cp.HEX.fullmatch(value.sha256) is None:
        _fail()
    cursor = cp._cursor(value.cursor)
    return {"cursor": cursor, "sha256": value.sha256}


def _value(pointer, prefix):
    if type(pointer) is not str or cp.HEX.fullmatch(pointer) is None:
        _fail()
    return {"schema": "wd.q4-private-prefix-state.v1", "pointer_sha256": pointer, "prefix": _pin(prefix)}


def _load(path):
    raw = cp._read(path, max_bytes=8192)
    value = cp._parse(raw)
    if (set(value) != {"schema", "pointer_sha256", "prefix"} or value["schema"] != "wd.q4-private-prefix-state.v1"
            or type(value["prefix"]) is not dict or set(value["prefix"]) != {"cursor", "sha256"}
            or type(value["prefix"]["cursor"]) is not dict
            or set(value["prefix"]["cursor"]) != {"offset", "file_identity", "generation"}):
        _fail()
    prefix = BridgePrefixPin(BridgeCursor(**value["prefix"]["cursor"]), value["prefix"]["sha256"])
    if cp._canonical(_value(value["pointer_sha256"], prefix)) != raw:
        _fail()
    return value


_INTENT_SCHEMA = "wd.q4-private-prefix-intent.v1"
_FENCED_INTENT_SCHEMA = "wd.q4-private-prefix-intent.fenced.v1"
_INTENT_KEYS = frozenset({"schema", "prior_pointer_sha256", "attempt_pointer_sha256", "prior_prefix",
                          "candidate_prefix"})


def _hex(value):
    return type(value) is str and cp.HEX.fullmatch(value) is not None


def _intent_value(prior, prior_prefix, attempt, candidate_prefix, fenced=False):
    if (prior is not None and not _hex(prior)) or not _hex(attempt) or prior == attempt or type(fenced) is not bool:
        _fail()
    return {"schema": _FENCED_INTENT_SCHEMA if fenced else _INTENT_SCHEMA,
            "prior_pointer_sha256": prior, "attempt_pointer_sha256": attempt,
            "prior_prefix": _pin(prior_prefix), "candidate_prefix": _pin(candidate_prefix)}


def _prefix_from(value):
    if (type(value) is not dict or set(value) != {"cursor", "sha256"} or type(value["cursor"]) is not dict
            or set(value["cursor"]) != {"offset", "file_identity", "generation"}):
        _fail()
    return BridgePrefixPin(BridgeCursor(**value["cursor"]), value["sha256"])


def _load_intent(raw):
    value = cp._parse(raw)
    if set(value) != _INTENT_KEYS or value["schema"] not in (_INTENT_SCHEMA, _FENCED_INTENT_SCHEMA):
        _fail()
    fenced = value["schema"] == _FENCED_INTENT_SCHEMA
    prior, attempt = value["prior_pointer_sha256"], value["attempt_pointer_sha256"]
    prior_prefix, candidate = _prefix_from(value["prior_prefix"]), _prefix_from(value["candidate_prefix"])
    if cp._canonical(_intent_value(prior, prior_prefix, attempt, candidate, fenced)) != raw:
        _fail()
    return prior, prior_prefix, attempt, candidate, fenced


def _stage(root, raw):
    stage = root / (".prefix-private-" + secrets.token_hex(16) + ".staging")
    cp._write_new(stage, raw)
    if cp._read(stage, max_bytes=8192) != raw:
        _fail()
    return stage


def _discard(path):
    try:
        os.unlink(path)
    except OSError:
        pass  # An orphan stage is never read as state.


def _create_slot(root, raw):
    """Exclusive crash-safe slot creation: the hard link is atomic and fails if the slot exists."""
    stage = None
    try:
        stage = _stage(root, raw)
        os.link(stage, cp._secure(root / _PENDING, missing_leaf=True))
    finally:
        if stage is not None:
            _discard(stage)


def _write_state(root, pointer, prefix):
    raw = cp._canonical(_value(pointer, prefix))
    target = root / _STATE
    stage = _stage(root, raw)
    cp._secure(target, missing_leaf=True)
    os.replace(stage, target)
    if cp._read(target, max_bytes=8192) != raw:
        _fail()


@contextmanager
def _checkpoint_lock(shared, timeout):
    """The cooperating checkpoint writer lock, always taken INSIDE the private-state lock (publication order)."""
    lock = cp._secure(shared / cp.LOCK, missing_leaf=True)
    with reader_slot(lock, timeout_seconds=timeout):
        cp._secure(lock)
        yield


def _shared_pointer(shared):
    """Digest of the shared pointer bytes (None when absent); the caller holds the checkpoint lock."""
    pointer = cp._secure(shared / cp.POINTER, missing_leaf=True)
    if not os.path.lexists(pointer):
        return None
    return cp._sha(cp._read(pointer, max_bytes=cp._MAX_POINTER_BYTES))


def _was_pointer(shared, attempt):
    """Evidence that the attempt was once the pointer: the immutable archive a later replacement leaves."""
    archive = cp._secure(shared / ("q4-pointer-" + attempt + ".json"), missing_leaf=True)
    return os.path.lexists(archive) and cp._sha(cp._read(archive, max_bytes=cp._MAX_POINTER_BYTES)) == attempt


def _require_candidate_binding(shared, attempt, candidate, *, current):
    """Bind to pointer bytes under the lock; this is not authentication of the publisher."""
    name = cp.POINTER if current == attempt else "q4-pointer-" + attempt + ".json"
    data = cp._read(cp._secure(shared / name), max_bytes=cp._MAX_POINTER_BYTES)
    if cp._sha(data) != attempt:
        _fail()
    pointer = cp._parse(data)
    if (pointer.get("schema") not in (cp.PREFIX_SCHEMA, cp.PREFIX_HISTORY_SCHEMA)
            or pointer.get("watermark") != cp._cursor(candidate.cursor)
            or pointer.get("prefix_watermark_sha256") != candidate.sha256):
        _fail()


def _root_disjoint(private, shared):
    """Compare root identities against the other's ancestors, including aliases."""
    return not (any(os.path.samefile(ancestor, shared) for ancestor in (private, *private.parents))
                or any(os.path.samefile(ancestor, private) for ancestor in (shared, *shared.parents)))


class _LockedState:
    __slots__ = ("_root", "_owns_pending", "_shared", "_timeout")

    def __init__(self, root, shared=None, timeout=0.2):
        self._root = root
        self._owns_pending = False
        self._shared = shared
        self._timeout = timeout

    def begin_intent(self, prior_pointer, prior_prefix, attempt_pointer, candidate_prefix, *, enabled=False,
                     fence=False):
        """Open a durable intent for the private state and an observed shared pointer.

        fence=True (opt-in, candidate): first open the checkpoint fence for exactly prior -> attempt, then the
        slot. The fence call takes the checkpoint lock itself (private lock first, never re-entered). An open
        fence for the same binding left by an interrupted begin is adopted only while the pointer is still the
        prior (or already the attempt, subject to resolution's candidate-binding check). The checkpoint lock
        is released before slot creation: the fence restricts cooperating new writers, but an older writer can
        advance in that gap. Resolution must recheck; neither this observation nor the fence authenticates
        the publisher or proves a result against noncooperating writers."""
        try:
            if enabled is not True or self._shared is None or type(fence) is not bool:
                _fail()
            raw = cp._canonical(_intent_value(prior_pointer, prior_prefix, attempt_pointer, candidate_prefix, fence))
            pending, target = self._root / _PENDING, self._root / _STATE
            cp._secure(pending, missing_leaf=True)
            cp._secure(target, missing_leaf=True)
            if os.path.lexists(pending):
                _fail()
            if prior_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target) != _value(prior_pointer, prior_prefix):
                _fail()
            if fence:
                try:
                    cp.open_prefix_fence(self._shared, expected_prior_pointer_sha256=prior_pointer,
                                         expected_attempt_sha256=attempt_pointer, enabled=True, timeout=self._timeout)
                except cp.CheckpointRefused:
                    with _checkpoint_lock(self._shared, self._timeout):
                        active = cp._active_fence(self._shared)
                        if (active is None or active[:2] != (prior_pointer, attempt_pointer)
                                or _shared_pointer(self._shared) not in (prior_pointer, attempt_pointer)):
                            _fail()
                # New-code writers are fenced; older writers may move the pointer before the slot is created.
                _create_slot(self._root, raw)
                return
            # No cooperating checkpoint writer can move the pointer between this check and the slot.
            with _checkpoint_lock(self._shared, self._timeout):
                if _shared_pointer(self._shared) != prior_pointer:
                    _fail()
                _create_slot(self._root, raw)
            return
        except Exception:
            pass
        _fail()

    def resolve_intent(self, *, enabled=False):
        """Close the open intent only when it LANDED; "unknown" keeps it open; anything else refuses.

        The checkpoint lock is held from the pointer observation through the state move and the slot removal,
        so no cooperating checkpoint writer acts inside the decision. A pointer still equal to the prior is
        "unknown", never "unchanged": an ordinary checkpoint-only writer holding the prepared inputs could still
        land the same attempt later and nothing here can fence it, so the intent stays open to catch that.

        A fenced intent first asks the checkpoint fence (outside the checkpoint lock, which the fence takes
        itself), then rechecks under the checkpoint lock. "landed" needs the pointer to be the attempt, or a
        landed record plus the attempt's pointer archive (the pointer moved on); a landed record against the
        prior pointer refuses. "not_landed" changes nothing and KEEPS the intent: only new-code writers are
        fenced and the fence metadata is not proven durable across power loss, so an old writer may still land
        it; a later pointer contradicting the not-landed record refuses and keeps the intent. A checkpoint
        refusal falls back to the unfenced rule only when the lock-protected fence and both records are absent;
        ambiguous, invalid or contradictory fence evidence refuses and keeps everything."""
        try:
            if enabled is not True or self._shared is None:
                _fail()
            pending, target = self._root / _PENDING, self._root / _STATE
            raw = cp._read(pending, max_bytes=8192)
            if raw == _MARK:
                _fail()  # a legacy marker stays unknown
            prior, prior_prefix, attempt, candidate, fenced = _load_intent(raw)
            cp._secure(target, missing_leaf=True)
            state = _load(target) if os.path.lexists(target) else None
            outcome = None
            if fenced:
                try:
                    outcome = cp.resolve_prefix_fence(self._shared, expected_prior_pointer_sha256=prior,
                                                      expected_attempt_sha256=attempt, enabled=True,
                                                      timeout=self._timeout)["status"]
                except cp.CheckpointRefused:
                    outcome = None
            with _checkpoint_lock(self._shared, self._timeout):
                current = _shared_pointer(self._shared)
                if fenced:
                    active = cp._active_fence(self._shared)
                    records = {name: cp._fence_record(self._shared, attempt, name)
                               for name in cp._FENCE_OUTCOMES}
                    present = [name for name, path in records.items() if os.path.lexists(path)]
                    if active is not None:
                        _fail()
                    if outcome is None:
                        if present:
                            _fail()
                    elif (outcome not in cp._FENCE_OUTCOMES or present != [outcome]
                          or cp._read(records[outcome], max_bytes=cp._MAX_FENCE_BYTES)
                          != cp._fence_bytes(prior, attempt)):
                        _fail()
                    if outcome == "not_landed" and current != prior:
                        _fail()
                landed = current == attempt or (outcome == "landed" and current != prior
                                                and _was_pointer(self._shared, attempt))
                if landed:
                    _require_candidate_binding(self._shared, attempt, candidate, current=current)
                if state == _value(attempt, candidate):
                    if not landed:
                        _fail()  # the state move already happened before an interruption
                elif (state is None and prior is None) or (prior is not None and state == _value(prior, prior_prefix)):
                    if not landed:
                        if current != prior or outcome == "landed":
                            _fail()
                        return "not_landed" if outcome == "not_landed" else "unknown"
                    _write_state(self._root, attempt, candidate)
                else:
                    _fail()
                if cp._read(pending, max_bytes=8192) != raw:
                    _fail()
                os.unlink(pending)
            return "landed"
        except Exception:
            pass
        _fail()

    def load(self, expected_pointer):
        """Return the private in-process start pin, never a public report."""
        try:
            pending, target = self._root / _PENDING, self._root / _STATE
            cp._secure(pending, missing_leaf=True)
            if os.path.lexists(pending):
                _fail()
            value = _load(target)
            if (type(expected_pointer) is not str or cp.HEX.fullmatch(expected_pointer) is None
                    or value["pointer_sha256"] != expected_pointer):
                _fail()
            prefix = value["prefix"]
            return BridgePrefixPin(BridgeCursor(**prefix["cursor"]), prefix["sha256"])
        except Exception:
            pass
        _fail()

    def require(self, expected_pointer, prefix):
        try:
            _pin(prefix)
            pending, target = self._root / _PENDING, self._root / _STATE
            cp._secure(pending, missing_leaf=True)
            cp._secure(target, missing_leaf=True)
            if os.path.lexists(pending):
                _fail()
            if expected_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target) != _value(expected_pointer, prefix):
                _fail()
            return
        except Exception:
            pass
        _fail()

    def begin(self, expected_pointer):
        try:
            target = self._root / _STATE
            cp._secure(target, missing_leaf=True)
            if expected_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target)["pointer_sha256"] != expected_pointer:
                _fail()
            cp._write_new(self._root / _PENDING, _MARK)
            self._owns_pending = True
            return
        except Exception:
            pass
        _fail()

    def commit(self, expected_pointer, pointer, prefix):
        try:
            if not self._owns_pending or cp._read(self._root / _PENDING, max_bytes=128) != _MARK:
                _fail()
            target = self._root / _STATE
            cp._secure(target, missing_leaf=True)
            if expected_pointer is None:
                if os.path.lexists(target):
                    _fail()
            elif not os.path.lexists(target) or _load(target)["pointer_sha256"] != expected_pointer:
                _fail()
            raw = cp._canonical(_value(pointer, prefix))
            stage = self._root / (".prefix-private-" + secrets.token_hex(16) + ".staging")
            cp._write_new(stage, raw)
            if cp._read(stage, max_bytes=8192) != raw:
                _fail()
            cp._secure(target, missing_leaf=True)
            os.replace(stage, target)
            if cp._read(target, max_bytes=8192) != raw:
                _fail()
            self.finish()
            return
        except Exception:
            pass
        _fail()

    def finish(self):
        try:
            pending = self._root / _PENDING
            if not self._owns_pending or cp._read(pending, max_bytes=128) != _MARK:
                _fail()
            os.unlink(pending)
            self._owns_pending = False
            return
        except Exception:
            pass
        _fail()


@contextmanager
def locked_state(private_root, shared_root, *, enabled=False, timeout=0.2):
    if enabled is not True:
        _fail()
    entered = False
    try:
        private, shared = cp._secure(private_root), cp._secure(shared_root)
        if not private.is_dir() or not shared.is_dir():
            _fail()
        left, right = os.path.normcase(str(private)), os.path.normcase(str(shared))
        try:
            overlap = os.path.commonpath((left, right)) in (left, right)
        except ValueError:  # distinct fixed drives
            overlap = False
        if overlap or not _root_disjoint(private, shared):
            _fail()
        lock = cp._secure(private / _LOCK, missing_leaf=True)
        with reader_slot(lock, timeout_seconds=timeout):
            cp._secure(lock)
            entered = True
            yield _LockedState(Path(private), Path(shared), timeout)
    except Exception:
        if entered:
            raise
    if not entered:
        _fail()
