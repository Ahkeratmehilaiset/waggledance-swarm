#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F20: the DORMANT durable admission ledger behind the Grok broker's LedgerPort.

Nothing imports this module. It has no default state root, no default ports, no default
verifier and no wiring. ``AdmissionLedger`` implements exactly the ``LedgerPort`` that
``tools/wd_grok_broker.py`` calls (unchanged from 49f14d92 through d4b22177): ``observe()``,
``reserve(admission)`` and ``finish(admission, outcome)``, plus an explicit one-time
``initialize()``. There is no deadline or generation parameter: the admission's own
``admitted_utc`` + 60 s is the reserve deadline, and ``revision`` (in ``observe``) is the
generation, re-read under the lock by every reserve instead of being passed as a token.

Every read and write happens under an INJECTED runtime-root mutex. Its port shape
``hold(name, timeout_seconds) -> ContextManager`` is structurally the frozen queue
``MutexPort`` (RCO2 bridge_v2_queue_transactions 377e7536/6fc76a4f). That module is not on
this base, so the shape is re-declared here; it is never imported or edited. The mutex
name is derived from the validated state root, so two roots never share a lock. The
apply time comes from an INJECTED clock, read under the lock.

Trust (fail-closed; nothing here authenticates anything):
* The lock kind/reference and the caller {lane, reference} are DECLARED labels. Their shape
  is checked, but a shape is never verification. The ONLY provenance signal is the injected
  ``provenance_verifier``: before every initialize/observe/reserve/finish, and before the
  lock is held or anything is read or written, ``verify(claim, mutex=<the injected mutex>)``
  must return exactly ``True`` for a claim naming this exact state root, root identity,
  mutex name, lock labels, caller and operation. A missing verifier refuses at construction;
  an exception, or any answer that is not literally True (1, "True", a truthy object), refuses.
  The ledger never infers trust from a port's type, attributes or callability, and a fake or
  mock verifier in a test proves nothing about a real lock. No trusted concrete verifier or
  mutex adapter exists on this base (dormant): binding the verifier to the reviewed
  cross-process mutex instance and to an authenticated caller is the wiring review's job.
* finish closes an entry only for the SAME DECLARED caller label that reserved it. That is
  label equality, not authentication: any process constructing the ledger with the same
  label (and a verifier that accepts it) can finish that entry.
* The cross-process mutex is a hard wiring requirement. With a no-op or in-process-only
  lock, two reservers can both read "nothing open" and both replace the ledger: the second
  write ERASES the first entry, a double admission plus an erased attempt.
* Exactly-once admission is per ledger ROOT and is not globally bound: two roots are two
  ledgers, and other machines and roots are not serialized with this one. Every consumer of the
  reservation must use ONE root. There is NO local rate budget (Lead e3cc3fa3, direct operator):
  no hourly, weekly or per-agent cooldown; provider limits are the provider's and are not paced here.
* The clock must be honest. A clock earlier than a recorded time refuses (clock_regressed); a
  clock running AHEAD records future stamps and then wedges every call on clock_regressed until
  real time catches up.

Rules (fail-closed):
* reserve re-reads the LATEST ledger under the lock. It admits only if nothing is open, the
  admission is fresh at apply time (admitted <= apply <= admitted + 60 s: freshness, not a rate
  limit) and its exact digest was never reserved. A finished attempt, answered or failed, never
  delays the next distinct admission. Losing any of these to another caller returns False. The
  entry is appended and the ledger replaced atomically.
* finish closes the one entry with the exact admission digest (declared caller above).
  A repeat with the same outcome is a no-op; a different outcome is a conflict. Nothing is
  ever removed, rewritten or refunded: every attempt stays in the history, and an unfinished
  (interrupted) entry blocks every later reservation until an operator reconciles it.
* ``observe()["last_admitted_utc"]`` is the latest entry's ``applied_utc`` (its reservation
  time); the name is the broker port's and is kept. No rule here uses it as a cooldown.
* Time formats (Lead 7c8c3714): the ledger's OWN stamps (applied_utc, finished_utc, observed_utc,
  initialized_utc) are UTC at FULL precision, always six fraction digits (YYYY-MM-DDTHH:MM:SS.ffffffZ).
  An admission's admitted_utc is only ever the route's whole-second form (YYYY-MM-DDTHH:MM:SSZ). A
  persisted legacy whole-second applied/finished stamp is unknown within its second, so the
  clock-regression check reads it one second later (never early); it is never rewritten or migrated.
* A missing ledger, a leftover temp file (a crash between write and replace), an
  oversized, unparseable, duplicate-key, NaN, foreign-root, revision-inconsistent or
  schema-invalid ledger, and a clock earlier than a recorded time are UNKNOWN. They
  raise; the ledger never repairs, cleans up or deletes anything. An operator inspects.
* A missing port or verifier, a state root that is not a fully-qualified LOCAL path in its
  normalized spelling (Windows: a drive letter path only; root-relative ``\\x``, drive-relative
  ``C:x``, UNC, ``\\\\?\\`` device, alternate-data-stream, forward-slash and trailing dot/space
  forms refuse), a missing or non-directory root, a link, junction or alias anywhere on the
  path (8.3 names, subst and mapped drives are expected to resolve differently and refuse),
  a lock kind other than this platform's, or malformed lock/caller labels refuses at
  construction.
* The mutex release never sees the body's outcome, so a port can never suppress a ledger
  error. A release that fails after the body succeeded is LedgerUnknown("lock_release_unknown");
  whatever the body applied (a reservation, a finish, the initial ledger) may already be
  durable and is never undone, retried, refunded or cleaned up. A release that fails after
  the body raised never replaces that primary error: the primary is re-raised unchanged and
  marked, best effort, with a "lock_release_unknown" note (and, for a ledger error,
  ``lock_release_unknown = True``). An ordinary failure to mark it (a ``__notes__`` that is not
  a list, an attribute write that raises) is swallowed: the mark may then be missing, but the
  primary is never replaced. A BaseException (KeyboardInterrupt, SystemExit) raised by the
  release or while marking is not swallowed; it propagates with the primary as its context.
  For the release, _locked re-attaches that context (best effort, never a cycle), because
  ExitStack.close() clears it (RCO1 e932 S1); the primary is then not marked.
  One limit (RCO2 01:04:16Z): _locked is a @contextmanager, and CPython's wrapper assigns
  ``__traceback__`` on the primary it re-raises (contextlib ``_GeneratorContextManager.__exit__``).
  A primary whose ``__setattr__`` refuses that write is replaced there by its AttributeError,
  with the primary as the context; still fail-closed.

Durability contract: each write is an exclusive temp file in the root, written in full,
os.fsync'ed, then os.replace'd over the ledger; on POSIX the root directory is fsync'ed
too. ``write_unknown`` means UNKNOWN, POSSIBLY DURABLE, never "failed": before the replace
the ledger is unchanged and the temp stays visible (partial_write_leftover); after it (a
failed directory fsync) the new ledger IS in place, blocks as written, and may or may not
survive a crash. On Windows the directory cannot be fsync'ed, and FlushFileBuffers/MoveFileEx
crash-atomicity and disk write-cache behaviour are UNVERIFIED. A restored older copy of a
valid ledger is not detectable (no external anchor): a rollback can hide attempts, and nothing
here detects it. Not runtime-tested (the operator's no-runs directive,
2026-09-29).
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from typing import Any, ContextManager, Iterator, Protocol

from tools.bridge_v2_activation import canonical_sha256
from tools.bridge_v2_grok_route import ADMISSION_SCHEMA, ADMIT, HEX40, HEX64

LEDGER_SCHEMA = "wd.grok-admission-ledger.v1"
PROVENANCE_CLAIM_SCHEMA = "wd.grok-admission-ledger-provenance.v1"
LEDGER_NAME = "grok-admission-ledger.json"
TEMP_MARK = ".tmp."
MUTEX_PREFIX = "Global\\WaggleDanceGrokAdmission-"
LOCK_TIMEOUT_SECONDS = 4.0  # the frozen queue ports' default
PLATFORM_LOCK = "windows_named_mutex" if os.name == "nt" else "posix_flock"
MAX_LEDGER_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 2000  # a capacity bound, not a rate: then ledger_full (never erased; an operator archives)
MAX_RECORD_BYTES = 64 * 1024  # one admission or outcome, canonical JSON
MAX_TOOLS = 64
MAX_ADMISSION_AGE = timedelta(seconds=60)
LANE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
VERDICT_RE = re.compile(r"[a-z][a-z_]{0,63}")
# The ledger's own stamps carry six fraction digits (Lead 7c8c3714); a persisted legacy whole-second stamp still
# parses (see _latest), and an admission's admitted_utc keeps the route's whole-second form. ASCII digits only.
STAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:[.][0-9]{6})?Z")
# A drive letter, a colon, a backslash, then one or more plain components separated by single backslashes:
# no control character, no <>:"/\|?* inside a component (so no stream, device or UNC form fits).
WINDOWS_ROOT_RE = re.compile(r'[A-Za-z]:\\[^\x00-\x1f<>:"/\\|?*]+(?:\\[^\x00-\x1f<>:"/\\|?*]+)*')
ADMISSION_KEYS = frozenset({"schema", "verdict", "reasons", "intent_sha256", "policy_sha256", "admitted_utc",
                            "allowed_tools", "execution_allowed", "authority"})
LEDGER_KEYS = frozenset({"schema", "root_identity", "revision", "entries"})
ENTRY_KEYS = frozenset({"admission_sha256", "intent_sha256", "policy_sha256", "admitted_utc", "applied_utc",
                        "caller", "state", "outcome_sha256", "outcome_verdict", "finished_utc"})
PROVENANCE_KEYS = frozenset({"kind", "reference"})
CALLER_KEYS = frozenset({"lane", "reference"})
REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class LedgerError(Exception):
    """``code`` is a stable reason. The broker maps any exception to blocked_unknown.

    ``lock_release_unknown`` is True when the mutex release ALSO failed after this error (best effort:
    see _mark_release_unknown). ``descriptor_close_unknown`` is True when closing the read's own descriptor
    ALSO failed after this error (best effort: see _mark_descriptor_unclosed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.lock_release_unknown = False
        self.descriptor_close_unknown = False


class LedgerRefused(LedgerError):
    """The call is refused before anything is written."""


class LedgerUnknown(LedgerError):
    """The durable state cannot be trusted, or may already hold this call's write; nothing is repaired."""


class MutexPort(Protocol):
    def hold(self, name: str, timeout_seconds: float) -> ContextManager[None]: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


class ProvenanceVerifierPort(Protocol):
    """Answers exactly True only when the mutex instance and labels in the claim are the reviewed ones."""

    def verify(self, claim: dict, *, mutex: MutexPort) -> bool: ...


def _hex(value: Any, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _stamp(moment: datetime) -> str:
    """The canonical ledger stamp of an aware-UTC time at FULL precision: a four-digit year (strftime's %Y is not
    zero-padded on every platform) and always six fraction digits, even .000000."""
    return (f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}T{moment.hour:02d}:{moment.minute:02d}:"
            f"{moment.second:02d}.{moment.microsecond:06d}Z")


def _parse_stamp(value: Any) -> datetime | None:
    """The EARLIEST instant a stamp can mean: exact for a six-digit stamp, the start of its second for a legacy
    whole-second one (see _latest). Anything else, including a non-ASCII digit, is None."""
    if not _hex(value, STAMP_RE):
        return None
    try:
        whole = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return whole.replace(microsecond=int(value[20:26])) if len(value) == 27 else whole


def _latest(value: str) -> datetime:
    """The LATEST instant a valid stamp can mean. A persisted legacy whole-second stamp is unknown within its
    second, so it counts as one second later: the clock-regression check never reads it optimistically.
    Nothing is rewritten or migrated."""
    parsed = _parse_stamp(value)
    if parsed is None:
        raise LedgerUnknown("ledger_invalid:entry")
    if len(value) == 27:
        return parsed
    try:
        return parsed + timedelta(seconds=1)
    except OverflowError:  # the second after 9999-12-31T23:59:59Z is not representable: unknown
        raise LedgerUnknown("time_unknown") from None


def _pairs(pairs: list) -> dict:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _constant(name: str) -> Any:
    raise ValueError("non-finite number " + name)


def _plain(info: os.stat_result, kind: Any) -> bool:
    """Not a link, not a reparse point (junction), and of the expected kind."""
    reparse = info.st_file_attributes & REPARSE_POINT if os.name == "nt" else 0
    return not stat.S_ISLNK(info.st_mode) and not reparse and kind(info.st_mode)


def _digest(value: Any, code: str) -> str:
    """canonical_sha256 of a bounded, plain-JSON record."""
    try:
        size = len(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
        digest = canonical_sha256(value)
    except (TypeError, ValueError, RecursionError):
        raise LedgerRefused(code) from None
    if size > MAX_RECORD_BYTES:
        raise LedgerRefused(code)
    return digest


def _local_absolute(text: str) -> bool:
    """A fully-qualified LOCAL path already in its normalized spelling, so every process derives ONE root.

    abspath must not change it (no '.', '..', doubled or trailing separator, no relative form; a root-relative
    '\\x', which Python < 3.13 isabs accepts, resolves against the CURRENT drive and so changes). Windows then
    needs a drive letter path (WINDOWS_ROOT_RE: no UNC, device, stream or forward-slash form) whose components
    never end in a dot or a space (Windows strips those: an alias). POSIX needs exactly one leading slash."""
    try:
        if os.path.abspath(text) != text:
            return False
    except (OSError, ValueError):
        return False
    if os.name == "nt":
        return WINDOWS_ROOT_RE.fullmatch(text) is not None and not any(
            part.endswith((".", " ")) for part in text[3:].split("\\"))
    return text.startswith("/") and not text.startswith("//")


def _validated_root(root: Any) -> Path:
    if not isinstance(root, (str, Path)):
        raise LedgerRefused("state_root_invalid")
    text = os.fspath(root)
    if type(text) is not str or not text or "\x00" in text:
        raise LedgerRefused("state_root_invalid")
    if not _local_absolute(text):
        raise LedgerRefused("state_root_not_local_absolute")
    try:
        info = os.lstat(text)
    except OSError:
        raise LedgerRefused("state_root_missing") from None
    if not _plain(info, stat.S_ISDIR):
        raise LedgerRefused("state_root_invalid")
    try:
        resolved = os.path.realpath(text)
    except (OSError, ValueError):
        raise LedgerRefused("state_root_linked") from None
    if os.path.normcase(resolved) != os.path.normcase(text):
        raise LedgerRefused("state_root_linked")  # a link, junction, 8.3, subst or mapped-drive alias on the path
    return Path(text)


def _caller_ok(value: Any) -> bool:
    return isinstance(value, dict) and set(value) == CALLER_KEYS and _hex(value["lane"], LANE_RE) \
        and _hex(value["reference"], HEX40)


def _admission_record(admission: Any) -> dict:
    """The exact admission identity. Only an 'admit' of the pure route's exact shape qualifies."""
    tools = admission.get("allowed_tools") if isinstance(admission, dict) else None
    if not (isinstance(admission, dict) and set(admission) == ADMISSION_KEYS
            and admission["schema"] == ADMISSION_SCHEMA and admission["verdict"] == ADMIT
            and admission["reasons"] == ["all_gates_passed"] and _hex(admission["intent_sha256"], HEX64)
            and _hex(admission["policy_sha256"], HEX64) and _parse_stamp(admission["admitted_utc"]) is not None
            and len(admission["admitted_utc"]) == 20  # the route's whole-second form only (Lead 7c8c3714)
            and isinstance(tools, list) and len(tools) <= MAX_TOOLS
            and all(isinstance(tool, str) and 0 < len(tool) <= 128 for tool in tools)
            and admission["execution_allowed"] is False and admission["authority"] == "none"):
        raise LedgerRefused("admission_invalid")
    return {"admission_sha256": _digest(admission, "admission_invalid"),
            "intent_sha256": admission["intent_sha256"], "policy_sha256": admission["policy_sha256"],
            "admitted_utc": admission["admitted_utc"]}


def _entry_ok(entry: Any, last: datetime | None) -> bool:
    if not (isinstance(entry, dict) and set(entry) == ENTRY_KEYS):
        return False
    applied, admitted = _parse_stamp(entry["applied_utc"]), _parse_stamp(entry["admitted_utc"])
    if not (all(_hex(entry[key], HEX64) for key in ("admission_sha256", "intent_sha256", "policy_sha256"))
            and applied is not None and admitted is not None and admitted <= applied <= admitted + MAX_ADMISSION_AGE
            and (last is None or last <= applied) and _caller_ok(entry["caller"])):
        return False
    if entry["state"] == "open":
        return entry["outcome_sha256"] is None and entry["outcome_verdict"] is None and entry["finished_utc"] is None
    finished = _parse_stamp(entry["finished_utc"])
    return entry["state"] == "finished" and _hex(entry["outcome_sha256"], HEX64) \
        and _hex(entry["outcome_verdict"], VERDICT_RE) and finished is not None and applied <= finished


def _released(stack: ExitStack) -> bool:
    """Release WITHOUT the body's exception (close() passes none), so the port can neither see nor suppress it."""
    try:
        stack.close()
    except Exception:  # noqa: BLE001 - a failed release is reported by the caller, never swallowed silently
        return False
    return True


def _close_descriptor(descriptor: int) -> bool:
    """Close the caller-owned descriptor ONCE: True when it closed. An ORDINARY failure is False and the caller
    makes it visible; it is never retried (the number may already be reused). A BaseException propagates."""
    try:
        os.close(descriptor)
    except Exception:  # noqa: BLE001 - made visible by the caller, never hidden and never retried
        return False
    return True


def _mark_descriptor_unclosed(primary: BaseException) -> None:
    """Best effort, attribute only (the one note stays in _mark_release_unknown): flag ANY primary.
    An ORDINARY failure to flag it is swallowed, so the primary is always the error re-raised; never forged."""
    try:
        primary.descriptor_close_unknown = True
    except Exception:  # noqa: BLE001 - diagnostic only and must never replace the primary
        pass

def _mark_release_unknown(primary: BaseException) -> None:
    """Mark the primary, best effort: a "lock_release_unknown" note and, for a ledger error, the attribute.

    An ORDINARY failure to mark it (a hostile ``__notes__`` that is not a list, an attribute write that
    raises) is swallowed, so the primary is always the error re-raised (RCO2 347 nit). The mark may then be
    missing; it is never forged. A BaseException raised while marking is not swallowed."""
    try:
        primary.add_note("lock_release_unknown")
    except Exception:  # noqa: BLE001 - the note is diagnostic only and must never replace the primary
        pass
    try:
        if isinstance(primary, LedgerError):
            primary.lock_release_unknown = True
    except Exception:  # noqa: BLE001 - the same for the attribute write
        pass


def _reattach_primary(interrupt: BaseException, primary: BaseException) -> None:
    """Re-attach the primary at the TAIL of a release BaseException's context chain (RCO1 e932 S1 and N-A, RCO2 N1).

    _released runs the release through ExitStack.close(), i.e. __exit__(None, None, None). Its exception-context
    fix-up cuts the primary off the END of that BaseException's context chain: the BaseException itself when the
    port raised it directly, or the port's own last error when the port raised it while handling one. This links
    the primary at exactly that tail, so a context that is already set in the middle of the chain is never
    replaced. It is best effort: it never writes onto the primary itself and never makes a cycle (a chain that
    already reaches the primary's own chain is left alone, and so is a chain that loops). An ordinary failure is
    swallowed; the BaseException itself still propagates."""
    try:
        primary_chain, link = set(), primary
        while link is not None and id(link) not in primary_chain:
            primary_chain.add(id(link))
            link = link.__context__
        tail, walked = interrupt, set()
        while True:
            if id(tail) in primary_chain:
                return  # the chain already reaches the primary's chain: linking the tail would make a cycle
            walked.add(id(tail))
            following = tail.__context__
            if following is None:
                break
            if id(following) in walked:
                return  # the interrupt's own chain loops, so it has no tail: leave it alone
            tail = following
        tail.__context__ = primary  # the two chains are disjoint, so this cannot make a cycle
    except Exception:  # noqa: BLE001 - diagnostic only
        pass


class AdmissionLedger:
    """Dormant. Implements the broker's LedgerPort; every argument is required and validated."""

    def __init__(self, *, state_root: Any, mutex: Any, lock_provenance: Any, clock: Any, caller: Any,
                 provenance_verifier: Any) -> None:
        if mutex is None:
            raise LedgerRefused("mutex_port_missing")
        if clock is None:
            raise LedgerRefused("clock_port_missing")
        if provenance_verifier is None:
            raise LedgerRefused("provenance_verifier_missing")
        self.root = _validated_root(state_root)
        if not (isinstance(lock_provenance, dict) and set(lock_provenance) == PROVENANCE_KEYS
                and lock_provenance["kind"] == PLATFORM_LOCK and _hex(lock_provenance["reference"], HEX40)):
            raise LedgerRefused("lock_untrusted")
        if not _caller_ok(caller):
            raise LedgerRefused("caller_untrusted")
        self.lock_provenance, self.caller = dict(lock_provenance), dict(caller)
        self.mutex, self.clock, self.provenance_verifier = mutex, clock, provenance_verifier
        self.root_identity = hashlib.sha256(os.path.normcase(str(self.root)).encode("utf-8")).hexdigest()
        self.mutex_name = MUTEX_PREFIX + self.root_identity[:32]

    def _verify(self, operation: str) -> None:
        """Before the lock and before any read or write: the injected verifier must answer exactly True."""
        claim = {"schema": PROVENANCE_CLAIM_SCHEMA, "operation": operation, "state_root": str(self.root),
                 "root_identity": self.root_identity, "mutex_name": self.mutex_name,
                 "lock": dict(self.lock_provenance), "caller": dict(self.caller)}
        try:
            verdict = self.provenance_verifier.verify(claim, mutex=self.mutex)
        except Exception:  # noqa: BLE001 - an unverifiable provenance refuses; nothing is read or written
            raise LedgerRefused("provenance_unknown") from None
        if verdict is not True:
            raise LedgerRefused("provenance_unverified")  # False, None, 1, "True" or any other truthy object

    @contextmanager
    def _locked(self) -> Iterator[None]:
        stack = ExitStack()
        try:
            stack.enter_context(self.mutex.hold(self.mutex_name, LOCK_TIMEOUT_SECONDS))
        except Exception:  # noqa: BLE001 - a lock that cannot be held is unknown; nothing is read or written
            raise LedgerUnknown("lock_unavailable") from None
        try:
            yield
        except BaseException as primary:
            try:
                released = _released(stack)
            except BaseException as interrupt:  # only a BaseException escapes _released
                _reattach_primary(interrupt, primary)  # ExitStack.close() cut it off the tail (e932 S1, N-A)
                raise
            if not released:
                _mark_release_unknown(primary)  # best effort: the primary itself is never replaced
            raise
        if not _released(stack):
            raise LedgerUnknown("lock_release_unknown")  # what the body applied may be durable: never undone

    def _now(self) -> datetime:
        """The apply time: exactly a datetime whose offset is read ONCE as exactly a timedelta (UTC, FULL precision)."""
        try:
            moment = self.clock.now()
            if type(moment) is not datetime:
                raise TypeError("not exactly a datetime")
            offset = moment.utcoffset()
            if type(offset) is not timedelta:
                raise TypeError("offset unknown")  # naive (None), a subclass or anything else
            return (moment.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)  # never floored (Lead 7c8c3714)
        except Exception:  # noqa: BLE001 - an unreadable clock or offset, or one out of range, is unknown
            raise LedgerUnknown("time_unknown") from None

    def _leftovers(self) -> list:
        try:
            return sorted(p.name for p in self.root.iterdir() if p.name.startswith(LEDGER_NAME + TEMP_MARK))
        except OSError:
            raise LedgerUnknown("state_root_unreadable") from None

    def _read(self) -> dict:
        if self._leftovers():
            raise LedgerUnknown("partial_write_leftover")  # visible; never cleaned up or "recovered"
        path = self.root / LEDGER_NAME
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            raise LedgerUnknown("ledger_missing") from None
        except OSError:
            raise LedgerUnknown("ledger_unreadable") from None
        if not _plain(info, stat.S_ISREG):
            raise LedgerUnknown("ledger_not_regular")
        flags = os.O_RDONLY | (os.O_BINARY if os.name == "nt" else os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise LedgerUnknown("ledger_unreadable") from None
        try:
            # closefd=False: no wrapper-side path (a constructor failing after partial wrapping and cleaning up, the
            # stream's own closure, a finalizer) ever closes the descriptor. This function is its SOLE owner and
            # closes it exactly once below, whether the wrapper succeeded or failed.
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise LedgerUnknown("ledger_changed_during_read")
                data = stream.read(MAX_LEDGER_BYTES + 1)
        except BaseException as primary:
            closed = _close_descriptor(descriptor)  # once; a BaseException from the close itself propagates
            if isinstance(primary, OSError):
                unknown = LedgerUnknown("ledger_unreadable")  # the existing mapping: the primary's code is kept
                unknown.descriptor_close_unknown = not closed
                raise unknown from None
            if not closed:
                _mark_descriptor_unclosed(primary)  # best effort: the primary itself is never replaced
            raise
        if not _close_descriptor(descriptor):
            raise LedgerUnknown("ledger_descriptor_unclosed")  # read, but an unclosed descriptor is never success
        if len(data) > MAX_LEDGER_BYTES:
            raise LedgerUnknown("ledger_oversized")
        try:
            doc = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        except (ValueError, RecursionError):
            raise LedgerUnknown("ledger_corrupt") from None
        return self._validate(doc)

    def _validate(self, doc: Any) -> dict:
        if not (isinstance(doc, dict) and set(doc) == LEDGER_KEYS and doc["schema"] == LEDGER_SCHEMA):
            raise LedgerUnknown("ledger_invalid:schema")
        if doc["root_identity"] != self.root_identity:
            raise LedgerUnknown("ledger_invalid:foreign_root")  # a copy from another root never counts here
        entries = doc["entries"]
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise LedgerUnknown("ledger_invalid:entries")
        seen, last = set(), None
        for entry in entries:
            if not _entry_ok(entry, last) or entry["admission_sha256"] in seen:
                raise LedgerUnknown("ledger_invalid:entry")
            seen.add(entry["admission_sha256"])
            last = _parse_stamp(entry["applied_utc"])
        if any(entry["state"] == "open" for entry in entries[:-1]):
            raise LedgerUnknown("ledger_invalid:open_entry")  # reserve never appends behind an open entry
        finished = sum(entry["state"] == "finished" for entry in entries)
        if type(doc["revision"]) is not int or doc["revision"] != len(entries) + finished:
            raise LedgerUnknown("ledger_invalid:revision")  # one revision per reserve and per finish
        return doc

    def _check_clock(self, doc: dict, now: datetime) -> None:
        times = [_latest(e["applied_utc"]) for e in doc["entries"]]
        times += [_latest(e["finished_utc"]) for e in doc["entries"] if e["finished_utc"] is not None]
        if times and now < max(times):
            raise LedgerUnknown("clock_regressed")  # never before a recorded time (legacy: its whole second)

    def _write(self, doc: dict) -> None:
        """Exclusive temp file, full write, fsync, atomic replace, root sync. write_unknown is POSSIBLY DURABLE:
        a failure before the replace leaves the temp VISIBLE; one after it (the root sync) leaves the new
        ledger in place. Neither is retried, undone or cleaned up."""
        data = (json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
                + "\n").encode("ascii")
        if len(data) > MAX_LEDGER_BYTES:
            raise LedgerRefused("ledger_full")
        temp = self.root / (LEDGER_NAME + TEMP_MARK + secrets.token_hex(8))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | (os.O_BINARY if os.name == "nt" else os.O_NOFOLLOW)
        try:
            fd = os.open(temp, flags, 0o600)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temp, self.root / LEDGER_NAME)
            self._sync_root()
        except OSError:
            raise LedgerUnknown("write_unknown") from None  # unknown, possibly durable; never claimed done

    def _sync_root(self) -> None:
        """POSIX: fsync the root directory after the replace. Windows cannot fsync a directory (disclosed)."""
        if os.name == "nt":
            return
        root_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)

    def initialize(self) -> dict:
        """Create the empty ledger exactly once. An explicit operator step: nothing calls it automatically."""
        self._verify("initialize")
        with self._locked():
            if os.path.lexists(self.root / LEDGER_NAME) or self._leftovers():
                raise LedgerRefused("ledger_exists")
            now = self._now()
            self._write({"schema": LEDGER_SCHEMA, "root_identity": self.root_identity, "revision": 0,
                         "entries": []})
        return {"initialized_utc": _stamp(now), "revision": 0}

    def observe(self) -> dict:
        """The latest durable state, read under the lock; observed_utc is the ledger's own apply clock."""
        self._verify("observe")
        with self._locked():
            doc = self._read()
            now = self._now()
            self._check_clock(doc, now)
        entries = doc["entries"]
        return {"observed_utc": _stamp(now), "revision": doc["revision"],
                "open": [{"admission_sha256": e["admission_sha256"], "applied_utc": e["applied_utc"]}
                         for e in entries if e["state"] == "open"],
                "last_admitted_utc": entries[-1]["applied_utc"] if entries else None}

    def reserve(self, admission: Any) -> bool:
        """True only for the one admission that wins under the lock (nothing open, fresh, and neither it nor its
        INTENT ever reserved: exactly once per bound request); False if it lost. No time since an earlier attempt
        is required: there is no local rate budget."""
        self._verify("reserve")
        record = _admission_record(admission)
        with self._locked():
            doc = self._read()
            now = self._now()
            self._check_clock(doc, now)
            admitted = _parse_stamp(record["admitted_utc"])
            if not admitted <= now <= admitted + MAX_ADMISSION_AGE:
                raise LedgerRefused("admission_stale_or_future")
            entries = doc["entries"]
            if any(e["state"] == "open" for e in entries):
                return False  # another admission is in flight, or an interrupted one is unresolved
            if any(e["admission_sha256"] == record["admission_sha256"] or e["intent_sha256"] == record["intent_sha256"]
                   for e in entries):
                return False  # a replayed admission or INTENT never gets a second attempt: exactly once per request
            if len(entries) >= MAX_ENTRIES:
                raise LedgerRefused("ledger_full")  # never erase an attempt to make room
            entry = dict(record, applied_utc=_stamp(now), caller=dict(self.caller), state="open",
                         outcome_sha256=None, outcome_verdict=None, finished_utc=None)
            self._write(dict(doc, revision=doc["revision"] + 1, entries=entries + [entry]))
        return True

    def finish(self, admission: Any, outcome: Any) -> None:
        """Close the exact reserved entry. Idempotent for the same outcome; it never rewrites or refunds an attempt."""
        self._verify("finish")
        record = _admission_record(admission)
        if not (isinstance(outcome, dict) and _hex(outcome.get("verdict"), VERDICT_RE)):
            raise LedgerRefused("outcome_invalid")
        outcome_sha256 = _digest(outcome, "outcome_invalid")
        with self._locked():
            doc = self._read()
            now = self._now()
            self._check_clock(doc, now)
            entries = list(doc["entries"])
            index = [i for i, e in enumerate(entries) if e["admission_sha256"] == record["admission_sha256"]]
            if not index:
                raise LedgerRefused("admission_not_reserved")
            entry = entries[index[0]]
            if entry["caller"] != self.caller:
                raise LedgerRefused("finish_foreign_caller")  # the declared label differs (not authentication)
            if entry["state"] == "finished":
                if entry["outcome_sha256"] == outcome_sha256:
                    return  # the same finish again: nothing changes
                raise LedgerRefused("finish_conflict")
            entries[index[0]] = dict(entry, state="finished", outcome_sha256=outcome_sha256,
                                     outcome_verdict=outcome["verdict"], finished_utc=_stamp(now))
            self._write(dict(doc, revision=doc["revision"] + 1, entries=entries))
