"""Strict cooperative consumer of one call's private cleanup witness.

This protocol candidate does not launch a caller. The transport owner can now
contribute after its own cleanup; attestation and adapter owners remain unwired.
There is no public proof factory. Synthetic tests do not prove native cleanup.

The real chain cannot release until all owners are separately wired and tested. Result dicts,
hashes, process disappearance and same-field objects do not supply a witness.
Private Python objects are cooperative consistency, not same-user isolation or
authentication. Introspection/monkeypatching can defeat private boundaries.

Only the old primitive's exclusive acquire is reused. Its caller-trusting
complete_confirmed API is NEVER called. The new release checks the original
marker identity and bytes, the fleet-derived root, and a one-use registered
call/lane/identity/descriptor witness. Errors retain a pending/UNKNOWN outcome;
there is no automatic reconciliation, retry, timeout or stale-PID deletion.
Path revalidation is not a hostile TOCTOU or power-loss durability guarantee.
"""
import json
import os
import re
import stat
import threading

from tools import wd_q4_guard_state_binding as roots
from tools import wd_q4_lane_quarantine as primitive


_OWNERS = ("transport", "attestation", "adapter")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX_RECORD = 4096
_RECORD_KEYS = {"schema", "lane", "call_id", "started_utc", "pid",
                "identity_sha256", "descriptor_sha256"}


class GuardCompletionUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("guard_completion_unavailable")


def _check(condition):
    if not condition:
        raise GuardCompletionUnavailable()


def _regular(info):
    return (stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode)
            and not (getattr(info, "st_file_attributes", 0) & _REPARSE))


class _Witness:
    # A same-type newly constructed object still is not the registered object.
    __slots__ = ()


class _CancelUnstartedWitness:
    """Separate from a cleanup witness; does not assert any owner completed."""
    __slots__ = ()


class _Registry:
    """Private per-call seam; future real owner hooks are deliberately absent."""

    def __init__(self, binding):
        self._binding = binding
        self._owners = {owner: object() for owner in _OWNERS}
        self._done = set()
        self._issued = None
        self._used = False
        self._uncertain = False
        self._lock = threading.Lock()
        self._transport_issued = False
        self._context = None
        self._outer_done = False
        self._handed_off = False
        self._cancel_issued = None

    def _complete_owner(self, capability):
        # Only a retained capability object contributes, never an owner-name,
        # digest, bool or arbitrary mapping. This method observes no resources.
        with self._lock:
            _check(not self._uncertain and not self._used)
            owners = [name for name, known in self._owners.items() if capability is known]
            _check(len(owners) == 1 and owners[0] not in self._done)
            self._done.add(owners[0])

    def _issue(self):
        with self._lock:
            _check(not self._uncertain and not self._used and self._done == set(_OWNERS)
                   and (self._context is None or self._outer_done))
            if self._issued is None:
                self._issued = _Witness()
            return self._issued

    def _unknown(self):
        with self._lock:
            self._uncertain = True
            self._issued = None
            self._cancel_issued = None

    def _transport_owner(self):
        with self._lock:
            _check(not self._uncertain and not self._used and self._context is None and not self._transport_issued
                   and "transport" not in self._done)
            self._transport_issued = True
            return _TransportOwner(self, self._owners["transport"])

    def _consume(self, witness, binding, release):
        # Hold the registry lock across verification/delete: simultaneous
        # consumers cannot both consume a token. File bytes/id still fence a
        # newer process's marker; this does not supply OS-user authentication.
        with self._lock:
            normal = (type(witness) is _Witness and witness is self._issued
                      and (self._context is None or self._outer_done)
                      and self._done == set(_OWNERS))
            ctx = self._context
            cancelled = (type(witness) is _CancelUnstartedWitness
                         and witness is self._cancel_issued and ctx is not None
                         and ctx._state == "cancelled" and not self._handed_off
                         and not self._done and not self._outer_done
                         and all(ticket._state == "new" for ticket in ctx._tickets.values()))
            _check(not self._uncertain and not self._used and binding == self._binding
                   and (normal or cancelled))
            try:
                release()
            except BaseException:
                self._uncertain = True
                self._issued = None
                self._cancel_issued = None
                raise
            self._used = True
            self._issued = None
            self._cancel_issued = None


class _TransportOwner:
    """One call-bound transport contribution; not an authentication boundary."""

    __slots__ = ("_registry", "_capability", "_state")

    def __init__(self, registry, capability):
        self._registry, self._capability, self._state = registry, capability, "new"

    def _begin(self):
        # Linearize admission against all registry uncertainty and completion
        # transitions. An already-unknown call must not enter the native chain.
        with self._registry._lock:
            if (self._registry._context is not None
                    and self._registry._context._state == "cancelled"
                    and not self._registry._used):
                self._registry._uncertain = True
                self._registry._cancel_issued = None
            _check(self._state == "new" and not self._registry._uncertain
                   and not self._registry._used and "transport" not in self._registry._done
                   and (self._registry._context is None or
                        (self._registry._context._state == "active" and
                         self._registry._context._tickets["transport"] is self)))
            self._registry._handed_off = True
            self._state = "started"

    def _complete(self):
        _check(self._state == "started")
        self._registry._complete_owner(self._capability)
        self._state = "done"

    def _unknown(self):
        self._registry._unknown()
        self._state = "unknown"


class _NamedOwner:
    """Private, per-context cooperative owner; not a native cleanup proof."""

    __slots__ = ("_registry", "_capability", "_state")
    _name = None

    def __init__(self, registry, capability):
        self._registry, self._capability, self._state = registry, capability, "new"

    def _begin(self):
        with self._registry._lock:
            ctx = self._registry._context
            if ctx is not None and ctx._state == "cancelled" and not self._registry._used:
                self._registry._uncertain = True
                self._registry._cancel_issued = None
            _check(self._state == "new" and ctx is not None and ctx._state == "active"
                   and ctx._tickets[self._name] is self and not self._registry._uncertain
                   and not self._registry._used and self._name not in self._registry._done)
            self._registry._handed_off = True
            self._state = "started"

    def _complete(self):
        _check(self._state == "started")
        self._registry._complete_owner(self._capability)
        self._state = "done"

    def _unknown(self):
        self._registry._unknown()
        self._state = "unknown"


class _AttestationOwner(_NamedOwner):
    _name = "attestation"


class _AdapterOwner(_NamedOwner):
    _name = "adapter"


class _OwnerContext:
    """One guard's outer-call barrier and three distinct private tickets.

    The outer owner must call _finish only after its real operation and all
    cleanup have ended, and _abort for uncertain completion. This class cannot
    observe native cleanup or authenticate a same-user caller.
    """

    __slots__ = ("_guard", "_registry", "_binding", "_tickets", "_state")

    def __init__(self, guard, registry, binding):
        self._guard, self._registry, self._binding, self._state = guard, registry, binding, "active"
        self._tickets = {
            "transport": _TransportOwner(registry, registry._owners["transport"]),
            "attestation": _AttestationOwner(registry, registry._owners["attestation"]),
            "adapter": _AdapterOwner(registry, registry._owners["adapter"]),
        }

    def _cancelled_access_locked(self):
        # A late user of a cancellation candidate makes the no-entry claim
        # uncertain if it has not already been consumed.
        if self._state == "cancelled" and not self._registry._used:
            self._registry._uncertain = True
            self._registry._cancel_issued = None

    def _ticket(self, name):
        with self._registry._lock:
            self._cancelled_access_locked()
            _check(self._registry._context is self and self._state == "active"
                   and not self._registry._uncertain and not self._registry._used
                   and type(name) is str and name in self._tickets)
            return self._tickets[name]

    def _handoff(self):
        """Cooperatively seal before exposing this context or tickets to a callee."""
        with self._registry._lock:
            self._cancelled_access_locked()
            _check(self._registry._context is self and self._state == "active"
                   and self._guard._registry is self._registry
                   and self._binding == self._registry._binding
                   and not self._registry._uncertain and not self._registry._used)
            self._registry._handed_off = True

    def _accept_ticket(self, name, ticket):
        with self._registry._lock:
            self._cancelled_access_locked()
            _check(self._registry._context is self and self._state == "active"
                   and not self._registry._uncertain and not self._registry._used
                   and type(name) is str and name in self._tickets
                   and ticket is self._tickets[name]
                   and type(ticket) is {"transport": _TransportOwner, "attestation": _AttestationOwner,
                                        "adapter": _AdapterOwner}[name])
            return ticket

    def _require_done(self, names):
        """Check exact inner-owner tickets without completing the outer call."""
        with self._registry._lock:
            self._cancelled_access_locked()
            _check(self._registry._context is self and self._state == "active"
                   and self._guard._registry is self._registry and self._binding == self._registry._binding
                   and not self._registry._uncertain and not self._registry._used
                   and type(names) is tuple and 0 < len(names) <= len(_OWNERS)
                   and all(type(name) is str and name in _OWNERS for name in names)
                   and len(set(names)) == len(names))
            types = {"transport": _TransportOwner, "attestation": _AttestationOwner, "adapter": _AdapterOwner}
            for name in names:
                ticket = self._tickets[name]
                _check(type(ticket) is types[name] and ticket._registry is self._registry
                       and ticket._capability is self._registry._owners[name]
                       and ticket._state == "done" and name in self._registry._done)

    def _finish(self):
        with self._registry._lock:
            self._cancelled_access_locked()
            _check(self._registry._context is self and self._state == "active"
                   and not self._registry._uncertain and not self._registry._used
                   and self._registry._done == set(_OWNERS)
                   and all(ticket._state == "done" for ticket in self._tickets.values()))
            self._registry._outer_done = True
            self._state = "finished"

    def _abort(self):
        with self._registry._lock:
            _check(self._registry._context is self and not self._registry._used)
            self._registry._uncertain = True
            self._registry._issued = None
            self._registry._cancel_issued = None
            self._state = "unknown"


class _Guard:
    def __init__(self, root, marker, raw, info, binding):
        self._root, self._marker, self._expected = root, marker, raw
        self._marker_info, self._binding = info, binding
        self._registry = _Registry(binding)
        self._closed = False

    def mark_unknown(self):
        self._registry._unknown()

    def _transport_owner(self):
        _check(not self._closed)
        self._root.revalidate()
        return self._registry._transport_owner()

    def _owner_context(self):
        _check(not self._closed)
        self._root.revalidate()
        with self._registry._lock:
            _check(not self._registry._uncertain and not self._registry._used
                   and self._registry._context is None and not self._registry._transport_issued
                   and not self._registry._done and self._registry._issued is None)
            ctx = _OwnerContext(self, self._registry, self._binding)
            self._registry._context = ctx
            self._registry._transport_issued = True
            return ctx

    def _accept_owner_context(self, ctx):
        _check(type(ctx) is _OwnerContext)
        with self._registry._lock:
            if self._registry._context is ctx:
                ctx._cancelled_access_locked()
            _check(self._registry._context is ctx and ctx._guard is self
                   and ctx._registry is self._registry and ctx._binding == self._binding
                   and ctx._state == "active" and not self._registry._uncertain
                   and not self._registry._used and not self._closed)
            return ctx

    def _cancel_unstarted(self, ctx):
        """Issue a distinct one-use witness only before cooperative handoff/entry.

        The caller must mark handoff before publishing any context or ticket;
        Python object privacy cannot enforce that contract against hostile callers.
        """
        with self._registry._lock:
            try:
                _check(type(ctx) is _OwnerContext and self._registry._context is ctx
                       and ctx._guard is self and ctx._registry is self._registry
                       and ctx._binding == self._binding == self._registry._binding
                       and ctx._state == "active" and not self._closed
                       and not self._registry._uncertain and not self._registry._used
                       and not self._registry._handed_off and not self._registry._done
                       and not self._registry._outer_done and self._registry._issued is None
                       and self._registry._cancel_issued is None
                       and set(ctx._tickets) == set(_OWNERS))
                types = {"transport": _TransportOwner, "attestation": _AttestationOwner,
                         "adapter": _AdapterOwner}
                for name in _OWNERS:
                    ticket = ctx._tickets[name]
                    _check(type(ticket) is types[name] and ticket._registry is self._registry
                           and ticket._capability is self._registry._owners[name]
                           and ticket._state == "new")
                witness = _CancelUnstartedWitness()
                ctx._state = "cancelled"
                self._registry._cancel_issued = witness
                return witness
            except BaseException:
                if not self._registry._used:
                    self._registry._uncertain = True
                    self._registry._issued = None
                    self._registry._cancel_issued = None
                raise

    def _clear_own_marker(self):
        self._root.revalidate()
        before = self._marker.lstat()
        _check(_regular(before) and os.path.samestat(before, self._marker_info))
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(self._marker, flags)
        try:
            opened = os.fstat(fd)
            _check(_regular(opened) and os.path.samestat(before, opened))
            with os.fdopen(fd, "rb", closefd=False) as stream:
                actual = stream.read(_MAX_RECORD + 1)
        finally:
            os.close(fd)
        after = self._marker.lstat()
        _check(_regular(after) and os.path.samestat(before, after)
               and actual == self._expected)
        self._root.revalidate()
        self._marker.unlink()
        self._closed = True

    def release(self, witness):
        try:
            _check(not self._closed)
            self._registry._consume(witness, self._binding, self._clear_own_marker)
        except GuardCompletionUnavailable:
            raise
        except (KeyboardInterrupt, SystemExit):
            self.mark_unknown()
            raise
        except Exception:
            self.mark_unknown()
            raise GuardCompletionUnavailable() from None


def acquire_guard(*, fleet_bytes, expected_fleet_sha256, lane,
                  identity_sha256, descriptor_sha256):
    """Create one pending marker from an already externally pinned fleet root.

    No native capability is inferred by acquiring this guard. Callers must not
    enter a real chain that lacks the separately reviewed completion protocol.
    Acquisition failures never remove a partial/old marker. No query, token,
    answer or caller-selected path is accepted by this API or put in the state.
    """
    try:
        root = roots.bind_state_root(fleet_bytes=fleet_bytes,
                                    expected_fleet_sha256=expected_fleet_sha256, lane=lane)
        state = root.revalidate()
        lease = primitive.acquire(state, lane, identity_sha256=identity_sha256,
                                  descriptor_sha256=descriptor_sha256)
        # This exact frozen primitive returns only closed file metadata, with
        # no live handles. Copy its acquisition record; do not retain or invoke
        # its legacy completion method. A changed primitive is a new tuple.
        _check(type(lease) is primitive._Lease and not lease._closed)
        raw, marker = lease._expected, lease._marker
        _check(type(raw) is bytes and 0 < len(raw) <= _MAX_RECORD
               and marker == state / (lane + ".pending.json"))
        record = json.loads(raw.decode("ascii", "strict"))
        _check(type(record) is dict and set(record) == _RECORD_KEYS
               and record["schema"] == "wd.q4-caller-pending.v1"
               and record["lane"] == lane and record["identity_sha256"] == identity_sha256
               and record["descriptor_sha256"] == descriptor_sha256
               and type(record["call_id"]) is str
               and re.fullmatch(r"[0-9a-f]{32}", record["call_id"]) is not None
               and type(record["pid"]) is int and record["pid"] == os.getpid())
        root.revalidate()
        info = marker.lstat()
        _check(_regular(info))
        binding = (record["call_id"], lane, identity_sha256, descriptor_sha256)
        return _Guard(root, marker, raw, info, binding)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise GuardCompletionUnavailable() from None
