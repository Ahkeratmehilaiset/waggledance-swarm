"""Strict cooperative consumer of one call's private cleanup witness.

This unconnected protocol candidate neither launches a caller nor observes
native cleanup. There is no public proof factory. The private registry gives
each fixed resource owner a distinct in-process capability; future trusted
owner code must complete ONLY its own capability AFTER its actual cleanup.
Synthetic completions prove this protocol only, never resource cleanup.

The existing real chain has no such owner contributions. It therefore cannot
release through this consumer until separately wired and tested. Result dicts,
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
            _check(not self._uncertain and not self._used and self._done == set(_OWNERS))
            if self._issued is None:
                self._issued = _Witness()
            return self._issued

    def _unknown(self):
        with self._lock:
            self._uncertain = True
            self._issued = None

    def _consume(self, witness, binding, release):
        # Hold the registry lock across verification/delete: simultaneous
        # consumers cannot both consume a token. File bytes/id still fence a
        # newer process's marker; this does not supply OS-user authentication.
        with self._lock:
            _check(not self._uncertain and not self._used
                   and self._done == set(_OWNERS) and binding == self._binding
                   and type(witness) is _Witness and witness is self._issued)
            try:
                release()
            except BaseException:
                self._uncertain = True
                self._issued = None
                raise
            self._used = True
            self._issued = None


class _Guard:
    def __init__(self, root, marker, raw, info, binding):
        self._root, self._marker, self._expected = root, marker, raw
        self._marker_info, self._binding = info, binding
        self._registry = _Registry(binding)
        self._closed = False

    def mark_unknown(self):
        self._registry._unknown()

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
