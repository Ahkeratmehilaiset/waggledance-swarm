#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F20: the DORMANT durable admission ledger behind the Grok broker's LedgerPort.

Nothing imports this module. It has no default state root, no default ports and no
wiring. ``AdmissionLedger`` implements exactly the ``LedgerPort`` that
``tools/wd_grok_broker.py`` (49f14d92) calls: ``observe()``, ``reserve(admission)`` and
``finish(admission, outcome)``, plus an explicit one-time ``initialize()``.

Every read and write happens under an INJECTED runtime-root mutex. Its port shape
``hold(name, timeout_seconds) -> ContextManager`` is structurally the frozen queue
``MutexPort`` (RCO2 bridge_v2_queue_transactions 377e7536/6fc76a4f). That module is not on
this base, so the shape is re-declared here; it is never imported or edited. The mutex
name is derived from the validated state root, so two roots never share a lock. The
apply time comes from an INJECTED clock, read under the lock.

Rules (fail-closed):
* reserve re-reads the LATEST ledger under the lock. It admits only if nothing is open,
  the last reservation is at least 60 minutes old (every reservation counts, whatever
  its outcome), the admission is fresh at apply time (admitted <= apply <= admitted +
  60 s) and its exact digest was never reserved. Losing any of these to another caller
  returns False. The entry is appended and the ledger replaced atomically.
* finish closes the one entry with the exact admission digest, only for the caller that
  reserved it. A repeat with the same outcome is a no-op; a different outcome is a
  conflict. Nothing is ever removed or refunded: the hour counts from the reservation,
  and an unfinished (interrupted) entry blocks every later reservation.
* A missing ledger, a leftover temp file (a crash between write and replace), an
  oversized, unparseable, duplicate-key, NaN, foreign-root, revision-inconsistent or
  schema-invalid ledger, and a clock earlier than a recorded time are UNKNOWN. They
  raise; the ledger never repairs, cleans up or deletes anything. An operator inspects.
* A missing port, an unvalidated state root (relative, missing, not a directory, a link
  or junction anywhere on the path), a lock kind other than this platform's, or missing
  lock/caller provenance refuses at construction. The provenance is RECORDED, not
  verified: that the injected mutex is the reviewed concrete lock is the wiring
  reviewer's responsibility.

Durability contract: each write is an exclusive temp file in the root, written in full,
os.fsync'ed, then os.replace'd over the ledger; on POSIX the root directory is fsync'ed
too. On Windows the directory cannot be fsync'ed, and FlushFileBuffers/MoveFileEx
crash-atomicity and disk write-cache behaviour are UNVERIFIED. A restored older copy of
a valid ledger is not detectable (no external anchor); the helper's own hourly state is
the independent second budget check. Not runtime-tested (the operator's no-runs
directive, 2026-09-29).
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
LEDGER_NAME = "grok-admission-ledger.json"
TEMP_MARK = ".tmp."
MUTEX_PREFIX = "Global\\WaggleDanceGrokAdmission-"
LOCK_TIMEOUT_SECONDS = 4.0  # the frozen queue ports' default
PLATFORM_LOCK = "windows_named_mutex" if os.name == "nt" else "posix_flock"
MAX_LEDGER_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 2000  # at most one reservation per hour: about 83 days, then ledger_full (never erased)
MAX_RECORD_BYTES = 64 * 1024  # one admission or outcome, canonical JSON
MAX_TOOLS = 64
BUDGET_WINDOW = timedelta(hours=1)
MAX_ADMISSION_AGE = timedelta(seconds=60)
LANE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
VERDICT_RE = re.compile(r"[a-z][a-z_]{0,63}")
STAMP_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")  # ASCII digits only
ADMISSION_KEYS = frozenset({"schema", "verdict", "reasons", "intent_sha256", "policy_sha256", "admitted_utc",
                            "allowed_tools", "execution_allowed", "authority"})
LEDGER_KEYS = frozenset({"schema", "root_identity", "revision", "entries"})
ENTRY_KEYS = frozenset({"admission_sha256", "intent_sha256", "policy_sha256", "admitted_utc", "applied_utc",
                        "caller", "state", "outcome_sha256", "outcome_verdict", "finished_utc"})
PROVENANCE_KEYS = frozenset({"kind", "reference"})
CALLER_KEYS = frozenset({"lane", "reference"})
REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class LedgerError(Exception):
    """``code`` is a stable reason. The broker maps any exception to blocked_unknown."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LedgerRefused(LedgerError):
    """The call is refused before anything is written."""


class LedgerUnknown(LedgerError):
    """The durable state cannot be trusted; nothing is written and nothing is repaired."""


class MutexPort(Protocol):
    def hold(self, name: str, timeout_seconds: float) -> ContextManager[None]: ...


class ClockPort(Protocol):
    def now(self) -> datetime: ...


def _hex(value: Any, pattern: re.Pattern) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(value: Any) -> datetime | None:
    if not _hex(value, STAMP_RE):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


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


def _validated_root(root: Any) -> Path:
    if not isinstance(root, (str, Path)):
        raise LedgerRefused("state_root_invalid")
    text = os.fspath(root)
    if not text or "\x00" in text or not os.path.isabs(text):
        raise LedgerRefused("state_root_invalid")
    absolute = os.path.abspath(text)
    try:
        info = os.lstat(absolute)
    except OSError:
        raise LedgerRefused("state_root_missing") from None
    if not _plain(info, stat.S_ISDIR):
        raise LedgerRefused("state_root_invalid")
    if os.path.normcase(os.path.realpath(absolute)) != os.path.normcase(absolute):
        raise LedgerRefused("state_root_linked")  # a link, junction or alias somewhere on the path
    return Path(absolute)


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


class AdmissionLedger:
    """Dormant. Implements the broker's LedgerPort; every argument is required and validated."""

    def __init__(self, *, state_root: Any, mutex: Any, lock_provenance: Any, clock: Any, caller: Any) -> None:
        if mutex is None:
            raise LedgerRefused("mutex_port_missing")
        if clock is None:
            raise LedgerRefused("clock_port_missing")
        self.root = _validated_root(state_root)
        if not (isinstance(lock_provenance, dict) and set(lock_provenance) == PROVENANCE_KEYS
                and lock_provenance["kind"] == PLATFORM_LOCK and _hex(lock_provenance["reference"], HEX40)):
            raise LedgerRefused("lock_untrusted")
        if not _caller_ok(caller):
            raise LedgerRefused("caller_untrusted")
        self.lock_provenance, self.caller = dict(lock_provenance), dict(caller)
        self.mutex, self.clock = mutex, clock
        self.root_identity = hashlib.sha256(os.path.normcase(str(self.root)).encode("utf-8")).hexdigest()
        self.mutex_name = MUTEX_PREFIX + self.root_identity[:32]

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with ExitStack() as stack:
            try:
                stack.enter_context(self.mutex.hold(self.mutex_name, LOCK_TIMEOUT_SECONDS))
            except Exception:  # noqa: BLE001 - a lock that cannot be held is unknown; nothing is read or written
                raise LedgerUnknown("lock_unavailable") from None
            yield

    def _now(self) -> datetime:
        """The apply time: exactly a datetime, aware, and representable in UTC (whole seconds)."""
        try:
            moment = self.clock.now()
            current = None
            if type(moment) is datetime and moment.utcoffset() is not None:
                current = moment.astimezone(timezone.utc).replace(microsecond=0)
        except Exception:  # noqa: BLE001 - an unreadable clock or offset is unknown
            current = None
        if current is None:
            raise LedgerUnknown("time_unknown")
        return current

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
            with os.fdopen(os.open(path, flags), "rb") as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise LedgerUnknown("ledger_changed_during_read")
                data = stream.read(MAX_LEDGER_BYTES + 1)
        except OSError:
            raise LedgerUnknown("ledger_unreadable") from None
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
        times = [_parse_stamp(e["applied_utc"]) for e in doc["entries"]]
        times += [_parse_stamp(e["finished_utc"]) for e in doc["entries"] if e["finished_utc"] is not None]
        if times and now < max(times):
            raise LedgerUnknown("clock_regressed")  # an apply time before a recorded one is never trusted

    def _write(self, doc: dict) -> None:
        """Exclusive temp file, full write, fsync, atomic replace. A failure leaves the temp VISIBLE."""
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
            if os.name != "nt":  # Windows cannot fsync a directory (disclosed above)
                root_fd = os.open(self.root, os.O_RDONLY)
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
        except OSError:
            raise LedgerUnknown("write_unknown") from None  # never claimed done, never cleaned up

    def initialize(self) -> dict:
        """Create the empty ledger exactly once. An explicit operator step: nothing calls it automatically."""
        with self._locked():
            if os.path.lexists(self.root / LEDGER_NAME) or self._leftovers():
                raise LedgerRefused("ledger_exists")
            now = self._now()
            self._write({"schema": LEDGER_SCHEMA, "root_identity": self.root_identity, "revision": 0,
                         "entries": []})
        return {"initialized_utc": _stamp(now), "revision": 0}

    def observe(self) -> dict:
        """The latest durable state, read under the lock; observed_utc is the ledger's own apply clock."""
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
        """True only for the one admission that wins the shared hour under the lock; False if it lost."""
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
            if entries and now - _parse_stamp(entries[-1]["applied_utc"]) < BUDGET_WINDOW:
                return False  # the shared hour is used, whatever that attempt's outcome
            if any(e["admission_sha256"] == record["admission_sha256"] for e in entries):
                return False  # a replayed admission never gets a second attempt (defence in depth)
            if len(entries) >= MAX_ENTRIES:
                raise LedgerRefused("ledger_full")  # never erase an attempt to make room
            entry = dict(record, applied_utc=_stamp(now), caller=dict(self.caller), state="open",
                         outcome_sha256=None, outcome_verdict=None, finished_utc=None)
            self._write(dict(doc, revision=doc["revision"] + 1, entries=entries + [entry]))
        return True

    def finish(self, admission: Any, outcome: Any) -> None:
        """Close the exact reserved entry. Idempotent for the same outcome; it never refunds the hour."""
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
                raise LedgerRefused("finish_foreign_caller")
            if entry["state"] == "finished":
                if entry["outcome_sha256"] == outcome_sha256:
                    return  # the same finish again: nothing changes
                raise LedgerRefused("finish_conflict")
            entries[index[0]] = dict(entry, state="finished", outcome_sha256=outcome_sha256,
                                     outcome_verdict=outcome["verdict"], finished_utc=_stamp(now))
            self._write(dict(doc, revision=doc["revision"] + 1, entries=entries))
