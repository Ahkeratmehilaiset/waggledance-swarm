"""F20 durable admission ledger through INJECTED fake ports (AUTHORED, NOT RUN).

The fakes stand in for the injected root mutex, clock and provenance verifier. Every test is
sequential in one process: the ledger re-reads under the injected mutex, but real
cross-process exclusion is the concrete mutex's property and is NOT exercised here. The
fixture Verifier only shows that the ledger consults the seam and refuses without a literal
True; it proves nothing about a real lock or caller (no trusted adapter exists). Nothing
calls Grok, the helper, F0, a real clock, a model or the network.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from tools import wd_grok_admission_ledger as ledger_mod
from tools.bridge_v2_activation import Decision, canonical_sha256
from tools.bridge_v2_grok_route import ADMISSION_SCHEMA, prepare_grok_consult
from tools.wd_grok_admission_ledger import AdmissionLedger, LedgerRefused, LedgerUnknown

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
REF = "c" * 40
LOCK = {"kind": ledger_mod.PLATFORM_LOCK, "reference": REF}
OTHER_LOCK = "posix_flock" if ledger_mod.PLATFORM_LOCK == "windows_named_mutex" else "windows_named_mutex"
CALLER = {"lane": "fable-5", "reference": "d" * 40}
OUTCOME = {"schema": "wd.grok-broker-result.v1", "verdict": "answered_bound", "reasons": [],
           "execution_allowed": False, "authority": "none"}
FAILED = dict(OUTCOME, verdict="blocked_unknown", reasons=["helper_outcome_unknown:OSError"])
SOURCE = Path(ledger_mod.__file__).read_text(encoding="utf-8")


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def admission(n=1, at=T0, **over):
    """The exact shape the pure route's admit returns (n in 1..15 picks the intent digest)."""
    value = {"schema": ADMISSION_SCHEMA, "verdict": "admit", "reasons": ["all_gates_passed"],
             "intent_sha256": format(n, "x") * 64, "policy_sha256": "e" * 64, "admitted_utc": stamp(at),
             "allowed_tools": [], "execution_allowed": False, "authority": "none"}
    value.update(over)
    return value


class Mutex:
    def __init__(self):
        self.calls, self.held = [], False

    @contextmanager
    def hold(self, name, timeout_seconds):
        assert not self.held
        self.calls.append((name, timeout_seconds))
        self.held = True
        try:
            yield
        finally:
            self.held = False


class TimeoutMutex(Mutex):
    def hold(self, name, timeout_seconds):
        self.calls.append((name, timeout_seconds))
        raise TimeoutError("mutex busy")


class Clock:
    def __init__(self, moment=T0):
        self.moment, self.reads = moment, 0

    def now(self):
        self.reads += 1
        return self.moment


class RaisingClock(Clock):
    def now(self):
        raise OSError("clock unreadable")


class _BadOffset(tzinfo):
    def utcoffset(self, dt):
        return "0"  # not a timedelta


class _Subclass(datetime):
    pass


class _Stateful(tzinfo):
    """+05:30 on the first utcoffset() read, None afterwards: a second read would look like LOCAL time."""

    def __init__(self):
        self.reads = 0

    def utcoffset(self, dt):
        self.reads += 1
        return timedelta(hours=5, minutes=30) if self.reads == 1 else None


class _Unimplemented(tzinfo):
    pass  # the base tzinfo.utcoffset raises NotImplementedError


class _Delta(timedelta):
    pass


class _SubclassOffset(tzinfo):
    def utcoffset(self, dt):
        return _Delta(0)


class _Truthy:
    def __bool__(self):
        return True


class Verifier:
    """A BOUND fixture verifier: exactly True only for the root, lock labels, caller and mutex INSTANCE it was
    built for. It shows that the ledger consults the seam; it proves nothing about a real lock or caller."""

    def __init__(self, root, mutex, *, lock=LOCK, caller=CALLER, answer=True, raises=None):
        self.expected = {"state_root": str(root), "lock": dict(lock), "caller": dict(caller)}
        self.mutex, self.answer, self.raises, self.claims, self.held = mutex, answer, raises, [], []

    def verify(self, claim, *, mutex):
        self.claims.append(claim)
        self.held.append(getattr(mutex, "held", None))
        if self.raises is not None:
            raise self.raises
        bound = mutex is self.mutex and {key: claim.get(key) for key in self.expected} == self.expected
        return self.answer if bound else False


class _Release:
    """A held lock whose release raises (fails) or returns True (suppress); it records what it was shown."""

    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        self.owner.held = True

    def __exit__(self, *exc_info):
        self.owner.held = False
        self.owner.exits.append(exc_info)
        if self.owner.fails:
            raise OSError("release failed")
        return self.owner.suppress


class ReleaseMutex(Mutex):
    def __init__(self, *, fails=False, suppress=False):
        super().__init__()
        self.fails, self.suppress, self.exits = fails, suppress, []

    def hold(self, name, timeout_seconds):
        self.calls.append((name, timeout_seconds))
        return _Release(self)


def make(root, *, mutex=None, clock=None, caller=CALLER, verifier=None):
    mutex = mutex if mutex is not None else Mutex()
    verifier = verifier if verifier is not None else Verifier(root, mutex, caller=caller)
    return AdmissionLedger(state_root=str(root), mutex=mutex, lock_provenance=dict(LOCK),
                           clock=clock or Clock(), caller=dict(caller), provenance_verifier=verifier)


def every_call(ledger):
    return (ledger.initialize, ledger.observe, lambda: ledger.reserve(admission()),
            lambda: ledger.finish(admission(), OUTCOME))


@pytest.fixture
def root(tmp_path):
    path = tmp_path / "state"
    path.mkdir()
    return path


@pytest.fixture
def ready(root):
    make(root, clock=Clock(T0 - timedelta(days=1))).initialize()
    return root


def ledger_bytes(root):
    return (root / ledger_mod.LEDGER_NAME).read_bytes()


def doc(root):
    return json.loads(ledger_bytes(root))


def entry(n=1, at=T0, state="finished"):
    value = {"admission_sha256": format(n, "x") * 64, "intent_sha256": "1" * 64, "policy_sha256": "e" * 64,
             "admitted_utc": stamp(at), "applied_utc": stamp(at), "caller": dict(CALLER), "state": "open",
             "outcome_sha256": None, "outcome_verdict": None, "finished_utc": None}
    if state == "finished":
        value.update(state="finished", outcome_sha256="f" * 64, outcome_verdict="refuse",
                     finished_utc=stamp(at + timedelta(seconds=1)))
    return value


def ledger_doc(identity, entries):
    return {"schema": ledger_mod.LEDGER_SCHEMA, "root_identity": identity,
            "revision": len(entries) + sum(e["state"] == "finished" for e in entries), "entries": entries}


# --- construction ------------------------------------------------------------------------

def test_constructor_is_keyword_only_without_defaults():
    params = inspect.signature(AdmissionLedger).parameters
    assert list(params) == ["state_root", "mutex", "lock_provenance", "clock", "caller", "provenance_verifier"]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty
               for p in params.values())


class _Text(str):
    pass


@pytest.mark.parametrize("over, code", [
    ({"mutex": None}, "mutex_port_missing"),
    ({"clock": None}, "clock_port_missing"),
    ({"provenance_verifier": None}, "provenance_verifier_missing"),
    ({"state_root": 7}, "state_root_invalid"),
    ({"state_root": ""}, "state_root_invalid"),
    ({"state_root": "C:\\state\x00"}, "state_root_invalid"),
    ({"state_root": _Text("C:\\state")}, "state_root_invalid"),  # a str subclass could override its methods
    ({"state_root": "relative/state"}, "state_root_not_local_absolute"),
    ({"state_root": "\\state"}, "state_root_not_local_absolute"),  # root-relative: the CURRENT drive decides
    ({"state_root": "C:state"}, "state_root_not_local_absolute"),  # drive-relative
    ({"state_root": "\\\\server\\share\\state"}, "state_root_not_local_absolute"),  # UNC: no cross-host mutex
    ({"state_root": "\\\\?\\C:\\state"}, "state_root_not_local_absolute"),  # device namespace
    ({"state_root": "C:\\state:stream"}, "state_root_not_local_absolute"),  # alternate data stream
    ({"state_root": "C:/state"}, "state_root_not_local_absolute"),  # forward slashes
    ({"lock_provenance": None}, "lock_untrusted"),
    ({"lock_provenance": {"kind": "fake", "reference": REF}}, "lock_untrusted"),
    ({"lock_provenance": {"kind": OTHER_LOCK, "reference": REF}}, "lock_untrusted"),
    ({"lock_provenance": {"kind": ledger_mod.PLATFORM_LOCK, "reference": "C" * 40}}, "lock_untrusted"),
    ({"lock_provenance": dict(LOCK, note="trust me")}, "lock_untrusted"),
    ({"caller": None}, "caller_untrusted"),
    ({"caller": {"lane": "Fable 5", "reference": REF}}, "caller_untrusted"),
    ({"caller": {"lane": "fable-5"}}, "caller_untrusted"),
    ({"caller": dict(CALLER, session_id="x")}, "caller_untrusted"),
])
def test_constructor_refuses_missing_ports_roots_and_provenance(root, over, code):
    mutex = Mutex()
    kwargs = {"state_root": str(root), "mutex": mutex, "lock_provenance": dict(LOCK), "clock": Clock(),
              "caller": dict(CALLER), "provenance_verifier": Verifier(root, mutex)}
    kwargs.update(over)
    with pytest.raises(LedgerRefused) as caught:
        AdmissionLedger(**kwargs)
    assert caught.value.code == code


def _root_aliases(root):
    """Other spellings of the SAME directory. Each would give another identity, mutex and hour: all refuse."""
    text = str(root)
    forms = [text + os.sep, text + os.sep + ".", os.path.join(text, "..", root.name), text + os.sep + os.sep]
    if os.name == "nt":
        forms += [text[2:],  # root-relative on the same drive (Python < 3.13 isabs accepts it)
                  "\\\\?\\" + text,  # the device namespace
                  "\\\\localhost\\" + text[0] + "$" + text[2:],  # the UNC admin share of the same directory
                  text + ":stream", text + "::$DATA",  # alternate data streams
                  text + ".", text + " ",  # Windows strips a trailing dot or space: an alias
                  text.replace("\\", "/")]
    else:
        forms += ["/" + text]  # a leading double slash is implementation-defined on POSIX
    return forms


def test_only_the_normalized_local_absolute_root_is_accepted(root):
    for form in _root_aliases(root):
        with pytest.raises(LedgerRefused) as caught:
            make(form)
        assert caught.value.code == "state_root_not_local_absolute", form
    ledger = make(root)  # the success twin: the normalized spelling itself
    assert ledger.root == root
    if os.name == "nt":  # another drive-letter case is the same normcase identity, mutex and hour
        text = str(root)
        assert make(text[0].swapcase() + text[1:]).root_identity == ledger.root_identity


def test_the_root_must_exist_and_be_a_directory(tmp_path):
    with pytest.raises(LedgerRefused, match="state_root_missing"):
        make(tmp_path / "missing")
    a_file = tmp_path / "file"
    a_file.write_text("x", encoding="ascii")
    with pytest.raises(LedgerRefused, match="state_root_invalid"):
        make(a_file)


def test_a_linked_root_or_a_root_under_a_link_refuses(tmp_path):
    target = tmp_path / "target"
    (target / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")
    with pytest.raises(LedgerRefused, match="state_root_invalid"):
        make(link)
    with pytest.raises(LedgerRefused, match="state_root_linked"):
        make(link / "sub")
    assert make(target / "sub").root == target / "sub"  # the unlinked twin


# --- the injected provenance verifier ------------------------------------------------------------

@pytest.mark.parametrize("answer", [False, None, 1, "True", [True], _Truthy()])
def test_only_a_literal_true_verifies_and_nothing_is_touched_otherwise(ready, answer):
    mutex, clock = Mutex(), Clock(T0)
    ledger = make(ready, mutex=mutex, clock=clock, verifier=Verifier(ready, mutex, answer=answer))
    before = ledger_bytes(ready)
    for call in every_call(ledger):
        with pytest.raises(LedgerRefused) as caught:
            call()
        assert caught.value.code == "provenance_unverified"
    assert (mutex.calls, clock.reads, ledger_bytes(ready)) == ([], 0, before)


@pytest.mark.parametrize("bind", [
    lambda root, mutex: Verifier(root.parent, mutex),  # another root
    lambda root, mutex: Verifier(root, Mutex()),  # another mutex INSTANCE with identical labels (a no-op lock)
    lambda root, mutex: Verifier(root, mutex, lock=dict(LOCK, reference="0" * 40)),  # another lock reference
    lambda root, mutex: Verifier(root, mutex, lock=dict(LOCK, kind=OTHER_LOCK)),  # another lock kind
    lambda root, mutex: Verifier(root, mutex, caller={"lane": "codex-tools-1", "reference": REF}),  # another caller
])
def test_a_verifier_bound_to_another_root_lock_or_caller_refuses_before_the_lock(ready, bind):
    mutex, clock = Mutex(), Clock(T0)
    verifier = bind(ready, mutex)
    ledger = make(ready, mutex=mutex, clock=clock, verifier=verifier)
    before = ledger_bytes(ready)
    for call in every_call(ledger):
        with pytest.raises(LedgerRefused, match="provenance_unverified"):
            call()
    assert (mutex.calls, clock.reads, ledger_bytes(ready), len(verifier.claims)) == ([], 0, before, 4)


class _NoVerify:
    pass


@pytest.mark.parametrize("verifier", [
    lambda root, mutex: Verifier(root, mutex, raises=OSError("provenance service down")),
    lambda root, mutex: Verifier(root, mutex, raises=RecursionError()),
    lambda root, mutex: _NoVerify(),  # no verify at all: an AttributeError, never "shape means trusted"
])
def test_an_unknown_provenance_refuses_before_the_lock(ready, verifier):
    mutex, clock = Mutex(), Clock(T0)
    ledger = make(ready, mutex=mutex, clock=clock, verifier=verifier(ready, mutex))
    before = ledger_bytes(ready)
    for call in every_call(ledger):
        with pytest.raises(LedgerRefused) as caught:
            call()
        assert caught.value.code == "provenance_unknown" and caught.value.__cause__ is None
    assert (mutex.calls, clock.reads, ledger_bytes(ready)) == ([], 0, before)


def test_the_bound_verifier_sees_the_exact_claim_before_the_lock_every_time(ready):
    """The success twin of every provenance refusal above."""
    mutex, clock = Mutex(), Clock(T0)
    verifier = Verifier(ready, mutex)
    ledger = make(ready, mutex=mutex, clock=clock, verifier=verifier)
    assert ledger.observe()["revision"] == 0
    assert ledger.reserve(admission()) is True
    ledger.finish(admission(), OUTCOME)
    with pytest.raises(LedgerRefused, match="ledger_exists"):
        ledger.initialize()
    assert [claim["operation"] for claim in verifier.claims] == ["observe", "reserve", "finish", "initialize"]
    assert verifier.held == [False] * 4  # verified BEFORE the lock is held, every time
    assert verifier.claims[0] == {"schema": ledger_mod.PROVENANCE_CLAIM_SCHEMA, "operation": "observe",
                                  "state_root": str(ready), "root_identity": ledger.root_identity,
                                  "mutex_name": ledger.mutex_name, "lock": LOCK, "caller": CALLER}
    verifier.claims[1]["caller"]["lane"] = "codex-tools-1"  # a verifier cannot rewrite the ledger's caller
    assert ledger.caller == CALLER


# --- initialize / observe -------------------------------------------------------------------

def test_initialize_creates_the_empty_ledger_exactly_once(root):
    ledger = make(root)
    assert ledger.initialize() == {"initialized_utc": stamp(T0), "revision": 0}
    assert doc(root) == {"schema": ledger_mod.LEDGER_SCHEMA, "root_identity": ledger.root_identity,
                         "revision": 0, "entries": []}
    with pytest.raises(LedgerRefused, match="ledger_exists"):
        ledger.initialize()
    assert ledger.observe() == {"observed_utc": stamp(T0), "revision": 0, "open": [], "last_admitted_utc": None}
    assert [p.name for p in root.iterdir()] == [ledger_mod.LEDGER_NAME]


def test_a_missing_ledger_is_unknown_and_nothing_is_created(root):
    ledger = make(root)
    for call in (ledger.observe, lambda: ledger.reserve(admission()), lambda: ledger.finish(admission(), OUTCOME)):
        with pytest.raises(LedgerUnknown, match="ledger_missing"):
            call()
    assert list(root.iterdir()) == []


# --- reserve ---------------------------------------------------------------------------------

def test_reserve_records_exactly_one_open_attempt(ready):
    applied = T0 + timedelta(seconds=5)
    ledger = make(ready, clock=Clock(applied))
    assert ledger.reserve(admission()) is True
    record = doc(ready)
    assert record["revision"] == 1
    assert record["entries"] == [{
        "admission_sha256": canonical_sha256(admission()), "intent_sha256": "1" * 64, "policy_sha256": "e" * 64,
        "admitted_utc": stamp(T0), "applied_utc": stamp(applied), "caller": CALLER, "state": "open",
        "outcome_sha256": None, "outcome_verdict": None, "finished_utc": None}]
    observed = ledger.observe()
    assert observed["open"] == [{"admission_sha256": canonical_sha256(admission()), "applied_utc": stamp(applied)}]
    assert observed["last_admitted_utc"] == stamp(applied)
    assert [p.name for p in ready.iterdir()] == [ledger_mod.LEDGER_NAME]  # no temp file left behind


def test_a_second_caller_loses_after_rereading_under_the_lock(ready):
    """Sequential: both callers saw an empty ledger; the second re-reads under the mutex and loses."""
    clock = Clock(T0)
    first = make(ready, clock=clock)
    second = make(ready, clock=clock, caller={"lane": "codex-lead-1", "reference": REF})
    assert first.observe()["open"] == [] and second.observe()["open"] == []
    assert first.reserve(admission(1)) is True
    before = ledger_bytes(ready)
    assert second.reserve(admission(2)) is False
    assert ledger_bytes(ready) == before


def test_the_hour_counts_every_attempt_including_failures(ready):
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1)) is True
    clock.moment = T0 + timedelta(seconds=5)
    ledger.finish(admission(1), FAILED)
    late = T0 + timedelta(minutes=59, seconds=59)
    clock.moment = late
    assert ledger.reserve(admission(2, at=late)) is False
    assert ledger.observe()["last_admitted_utc"] == stamp(T0)  # the failed attempt is never refunded
    hour = T0 + timedelta(hours=1)
    clock.moment = hour
    assert ledger.reserve(admission(2, at=hour)) is True  # the twin: exactly 60 minutes later
    assert len(doc(ready)["entries"]) == 2


def test_an_unfinished_attempt_stays_visible_and_blocks_forever(ready):
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1)) is True
    later = T0 + timedelta(days=3)
    clock.moment = later
    assert ledger.reserve(admission(2, at=later)) is False  # an interrupted attempt is never presumed done
    assert ledger.observe()["open"][0]["admission_sha256"] == canonical_sha256(admission(1))


def test_a_replayed_admission_never_reserves_twice(ready, monkeypatch):
    monkeypatch.setattr(ledger_mod, "BUDGET_WINDOW", timedelta(0))  # isolate the replay rule
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1)) is True
    clock.moment = T0 + timedelta(seconds=1)
    ledger.finish(admission(1), OUTCOME)
    clock.moment = T0 + timedelta(seconds=2)
    assert ledger.reserve(admission(1)) is False
    assert ledger.reserve(admission(2)) is True  # the twin: a new admission passes the same checks


@pytest.mark.parametrize("offset, wins", [(timedelta(0), True), (timedelta(seconds=60), True),
                                          (timedelta(seconds=61), False), (timedelta(seconds=-1), False)])
def test_the_admission_must_be_fresh_at_apply_time(ready, offset, wins):
    ledger = make(ready, clock=Clock(T0 + offset))
    before = ledger_bytes(ready)
    if wins:
        assert ledger.reserve(admission()) is True
    else:
        with pytest.raises(LedgerRefused, match="admission_stale_or_future"):
            ledger.reserve(admission())
        assert ledger_bytes(ready) == before


def test_a_clock_before_a_recorded_time_is_unknown(ready):
    first = T0 + timedelta(seconds=10)
    clock = Clock(first)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1, at=first)) is True
    clock.moment = T0 + timedelta(seconds=20)
    ledger.finish(admission(1, at=first), OUTCOME)
    clock.moment = T0 + timedelta(seconds=19)
    before = ledger_bytes(ready)
    for call in (ledger.observe, lambda: ledger.reserve(admission(2, at=T0 + timedelta(seconds=19))),
                 lambda: ledger.finish(admission(1, at=first), OUTCOME)):
        with pytest.raises(LedgerUnknown, match="clock_regressed"):
            call()
    assert ledger_bytes(ready) == before


def test_a_clock_running_ahead_admits_early_then_wedges_until_real_time_catches_up(ready):
    """The disclosed honest-clock limit (RCO2 Q1c): the ledger cannot tell a fast clock from real time."""
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1)) is True
    clock.moment = T0 + timedelta(seconds=5)
    ledger.finish(admission(1), OUTCOME)
    ahead = T0 + timedelta(hours=1, seconds=10)  # the clock jumps an hour ahead, 10 s after the first attempt
    clock.moment = ahead
    assert ledger.reserve(admission(2, at=ahead)) is True  # an early second attempt (the helper's state backstops)
    clock.moment = T0 + timedelta(seconds=20)  # the clock is corrected back to real time
    wedged = ledger_bytes(ready)
    for call in (ledger.observe, lambda: ledger.reserve(admission(3, at=clock.moment)),
                 lambda: ledger.finish(admission(2, at=ahead), OUTCOME)):
        with pytest.raises(LedgerUnknown, match="clock_regressed"):
            call()
    assert ledger_bytes(ready) == wedged  # nothing is repaired while wedged
    clock.moment = ahead  # real time reaches the recorded time: the ledger works again
    ledger.finish(admission(2, at=ahead), OUTCOME)
    assert [e["state"] for e in doc(ready)["entries"]] == ["finished", "finished"]


@pytest.mark.parametrize("clock", [
    Clock(datetime(2026, 9, 30, 12, 0)),  # naive
    Clock(_Subclass(2026, 9, 30, 12, 0, tzinfo=timezone.utc)),
    Clock("2026-09-30T12:00:00Z"),
    Clock(datetime(2026, 9, 30, 12, 0, tzinfo=_BadOffset())),
    Clock(datetime(2026, 9, 30, 12, 0, tzinfo=_Unimplemented())),  # NotImplementedError
    Clock(datetime(2026, 9, 30, 12, 0, tzinfo=_SubclassOffset())),  # a timedelta subclass offset
    Clock(datetime.max.replace(tzinfo=timezone(-timedelta(hours=23, minutes=59)))),  # past datetime.max in UTC
    Clock(datetime.min.replace(tzinfo=timezone(timedelta(hours=23, minutes=59)))),  # before datetime.min in UTC
    RaisingClock(),
])
def test_an_untrusted_clock_is_unknown(ready, clock):
    ledger = make(ready, clock=clock)
    with pytest.raises(LedgerUnknown, match="time_unknown"):
        ledger.observe()
    with pytest.raises(LedgerUnknown, match="time_unknown"):
        ledger.reserve(admission())
    assert doc(ready)["entries"] == []


def test_the_clock_offset_is_read_once_and_never_taken_as_local_time(ready):
    zone = _Stateful()
    ledger = make(ready, clock=Clock(datetime(2026, 9, 30, 17, 30, tzinfo=zone)))
    assert ledger.observe()["observed_utc"] == stamp(T0)  # 17:30+05:30, never 17:30 host-local
    assert zone.reads == 1
    ordinary = make(ready, clock=Clock(datetime(2026, 9, 30, 4, 30, 0, 999999,
                                                tzinfo=timezone(-timedelta(hours=7, minutes=30)))))
    assert ordinary.reserve(admission()) is True  # the success twin: an ordinary non-UTC clock
    assert doc(ready)["entries"][0]["applied_utc"] == stamp(T0)


@pytest.mark.parametrize("value", [
    None, [], "admit",
    admission(verdict="refuse"),
    admission(schema="wd.other"),
    admission(reasons=[]),
    admission(intent_sha256="1" * 63),
    admission(policy_sha256="E" * 64),
    admission(admitted_utc="2026-09-30T12:00:00+00:00"),
    admission(admitted_utc="٢٠٢٦-09-30T12:00:00Z"),  # non-ASCII digits
    admission(execution_allowed=True),
    admission(authority="operator"),
    admission(allowed_tools="read"),
    admission(allowed_tools=["x" * 129]),
    admission(allowed_tools=["t%d" % i for i in range(65)]),
    dict(admission(), exemption=True),
    {k: v for k, v in admission().items() if k != "reasons"},
])
def test_only_an_exact_admit_can_reserve_or_finish(ready, value):
    ledger = make(ready)
    before = ledger_bytes(ready)
    with pytest.raises(LedgerRefused, match="admission_invalid"):
        ledger.reserve(value)
    with pytest.raises(LedgerRefused, match="admission_invalid"):
        ledger.finish(value, OUTCOME)
    assert ledger_bytes(ready) == before


# --- finish ----------------------------------------------------------------------------------

def test_finish_is_idempotent_for_the_exact_admission_and_never_erases(ready):
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1)) is True
    clock.moment = T0 + timedelta(seconds=30)
    ledger.finish(admission(1), OUTCOME)
    finished = ledger_bytes(ready)
    closed = doc(ready)["entries"][0]
    assert (closed["state"], closed["outcome_sha256"], closed["outcome_verdict"], closed["finished_utc"]) == (
        "finished", canonical_sha256(OUTCOME), "answered_bound", stamp(T0 + timedelta(seconds=30)))
    assert doc(ready)["revision"] == 2
    clock.moment = T0 + timedelta(seconds=40)
    ledger.finish(admission(1), OUTCOME)  # the same finish again writes nothing
    assert ledger_bytes(ready) == finished
    with pytest.raises(LedgerRefused, match="finish_conflict"):
        ledger.finish(admission(1), FAILED)
    with pytest.raises(LedgerRefused, match="admission_not_reserved"):
        ledger.finish(admission(2), OUTCOME)
    with pytest.raises(LedgerRefused, match="admission_not_reserved"):
        ledger.finish(admission(1, allowed_tools=["read"]), OUTCOME)  # not the exact admission
    assert ledger_bytes(ready) == finished
    assert len(doc(ready)["entries"]) == 1


def test_only_the_reserving_caller_can_finish(ready):
    clock = Clock(T0)
    assert make(ready, clock=clock).reserve(admission(1)) is True
    other = make(ready, clock=clock, caller={"lane": "codex-tools-1", "reference": REF})
    with pytest.raises(LedgerRefused, match="finish_foreign_caller"):
        other.finish(admission(1), OUTCOME)
    assert doc(ready)["entries"][0]["state"] == "open"


@pytest.mark.parametrize("outcome", [None, [], {}, {"verdict": "Answered"}, {"verdict": ""},
                                     dict(OUTCOME, score=float("nan")), dict(OUTCOME, blob="x" * (64 * 1024))])
def test_a_malformed_outcome_is_refused_before_the_ledger_is_read(ready, outcome):
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission()) is True
    before, reads = ledger_bytes(ready), clock.reads
    with pytest.raises(LedgerRefused, match="outcome_invalid"):
        ledger.finish(admission(), outcome)
    assert ledger_bytes(ready) == before and clock.reads == reads


# --- unknown durable state: never repaired -----------------------------------------------------

def _dump(value):
    return json.dumps(value).encode("ascii")


def _corruptions(identity):
    good = ledger_doc(identity, [entry(1)])
    later = entry(2, at=T0 + timedelta(hours=2))
    return [
        (b"", "ledger_corrupt"),
        (b"{", "ledger_corrupt"),
        (b"\xff", "ledger_corrupt"),
        (b"\xef\xbb\xbf" + _dump(good), "ledger_corrupt"),  # a BOM
        (_dump(good)[:-1] + b', "schema": "' + ledger_mod.LEDGER_SCHEMA.encode() + b'"}', "ledger_corrupt"),
        (_dump(dict(good, revision=1)).replace(b'"revision": 1', b'"revision": NaN'), "ledger_corrupt"),
        (_dump([]), "ledger_invalid:schema"),
        (_dump(dict(good, schema="wd.other")), "ledger_invalid:schema"),
        (_dump(dict(good, note="edited")), "ledger_invalid:schema"),
        (_dump(dict(good, root_identity="0" * 64)), "ledger_invalid:foreign_root"),
        (_dump(dict(good, entries={})), "ledger_invalid:entries"),
        (_dump(dict(good, revision=0)), "ledger_invalid:revision"),
        (_dump(dict(good, revision=True)), "ledger_invalid:revision"),
        (_dump(ledger_doc(identity, [dict(entry(1), note="x")])), "ledger_invalid:entry"),
        (_dump(ledger_doc(identity, [entry(1), dict(later, admission_sha256=entry(1)["admission_sha256"])])),
         "ledger_invalid:entry"),
        (_dump(ledger_doc(identity, [later, entry(1)])), "ledger_invalid:entry"),  # out of order
        (_dump(ledger_doc(identity, [dict(entry(1), finished_utc=stamp(T0 - timedelta(seconds=1)))])),
         "ledger_invalid:entry"),
        (_dump(ledger_doc(identity, [dict(entry(1, state="open"), outcome_sha256="f" * 64)])),
         "ledger_invalid:entry"),
        (_dump(ledger_doc(identity, [dict(entry(1), state="refunded")])), "ledger_invalid:entry"),
        (_dump(ledger_doc(identity, [dict(entry(1), caller=dict(CALLER, run_id="x"))])), "ledger_invalid:entry"),
        (_dump(ledger_doc(identity, [entry(1, state="open"), later])), "ledger_invalid:open_entry"),
    ]


def test_every_corrupt_or_inconsistent_ledger_is_unknown_and_left_as_is(ready):
    path = ready / ledger_mod.LEDGER_NAME
    ledger = make(ready, clock=Clock(T0 + timedelta(hours=3)))
    for content, code in _corruptions(ledger.root_identity):
        path.write_bytes(content)
        for call in (ledger.observe, lambda: ledger.reserve(admission(9, at=T0 + timedelta(hours=3)))):
            with pytest.raises(LedgerUnknown) as caught:
                call()
            assert caught.value.code == code, content
        assert path.read_bytes() == content and [p.name for p in ready.iterdir()] == [ledger_mod.LEDGER_NAME]
    path.write_bytes(_dump(ledger_doc(ledger.root_identity, [entry(1)])))
    assert ledger.observe()["last_admitted_utc"] == stamp(T0)  # the valid twin of the fixtures


def test_the_size_bound_is_checked_before_parsing(ready):
    path = ready / ledger_mod.LEDGER_NAME
    path.write_bytes(b" " * (ledger_mod.MAX_LEDGER_BYTES + 1))
    with pytest.raises(LedgerUnknown, match="ledger_oversized"):
        make(ready).observe()
    path.write_bytes(b" " * ledger_mod.MAX_LEDGER_BYTES)  # at the bound it is parsed (and is corrupt)
    with pytest.raises(LedgerUnknown, match="ledger_corrupt"):
        make(ready).observe()


def test_a_ledger_copied_from_another_root_never_counts(ready, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / ledger_mod.LEDGER_NAME).write_bytes(ledger_bytes(ready))
    with pytest.raises(LedgerUnknown, match="ledger_invalid:foreign_root"):
        make(other).observe()


def test_a_directory_or_a_linked_ledger_is_unknown(ready, tmp_path):
    path = ready / ledger_mod.LEDGER_NAME
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(path.read_bytes())
    path.unlink()
    path.mkdir()
    with pytest.raises(LedgerUnknown, match="ledger_not_regular"):
        make(ready).observe()
    path.rmdir()
    try:
        os.symlink(elsewhere, path)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")
    with pytest.raises(LedgerUnknown, match="ledger_not_regular"):
        make(ready).observe()


def test_a_leftover_temp_file_is_visible_unknown_and_kept(ready):
    leftover = ready / (ledger_mod.LEDGER_NAME + ledger_mod.TEMP_MARK + "0" * 16)
    leftover.write_bytes(b'{"partial"')
    before = ledger_bytes(ready)
    ledger = make(ready)
    for call in (ledger.observe, lambda: ledger.reserve(admission())):
        with pytest.raises(LedgerUnknown, match="partial_write_leftover"):
            call()
    with pytest.raises(LedgerRefused, match="ledger_exists"):
        ledger.initialize()
    assert leftover.read_bytes() == b'{"partial"' and ledger_bytes(ready) == before


def test_a_failed_replace_is_unknown_and_leaves_its_temp_visible(ready, monkeypatch):
    before = ledger_bytes(ready)

    def refuse(src, dst):
        raise PermissionError("replace refused")

    monkeypatch.setattr(ledger_mod.os, "replace", refuse)
    ledger = make(ready)
    with pytest.raises(LedgerUnknown, match="write_unknown"):
        ledger.reserve(admission())
    monkeypatch.undo()
    assert ledger_bytes(ready) == before
    leftovers = [p.name for p in ready.iterdir() if p.name != ledger_mod.LEDGER_NAME]
    assert len(leftovers) == 1 and leftovers[0].startswith(ledger_mod.LEDGER_NAME + ledger_mod.TEMP_MARK)
    with pytest.raises(LedgerUnknown, match="partial_write_leftover"):
        ledger.observe()


def test_a_failed_root_sync_after_the_replace_is_unknown_and_possibly_durable(ready, monkeypatch):
    """write_unknown is never "failed": here the replace succeeded and only the directory sync failed."""
    def fail(self):
        raise OSError("directory fsync failed")

    monkeypatch.setattr(AdmissionLedger, "_sync_root", fail)
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    with pytest.raises(LedgerUnknown, match="write_unknown"):
        ledger.reserve(admission(1))
    monkeypatch.undo()
    entries = doc(ready)["entries"]  # the new ledger IS in place, and no temp is left behind
    assert [(e["state"], e["admission_sha256"]) for e in entries] == [("open", canonical_sha256(admission(1)))]
    assert [p.name for p in ready.iterdir()] == [ledger_mod.LEDGER_NAME]
    later = T0 + timedelta(hours=2)
    clock.moment = later
    assert ledger.reserve(admission(2, at=later)) is False  # it blocks as written: no retry, undo or refund


def test_a_full_ledger_refuses_and_never_erases(ready, monkeypatch):
    monkeypatch.setattr(ledger_mod, "MAX_ENTRIES", 1)
    clock = Clock(T0)
    ledger = make(ready, clock=clock)
    assert ledger.reserve(admission(1)) is True
    clock.moment = T0 + timedelta(seconds=1)
    ledger.finish(admission(1), OUTCOME)
    later = T0 + timedelta(hours=2)
    clock.moment = later
    before = ledger_bytes(ready)
    with pytest.raises(LedgerRefused, match="ledger_full"):
        ledger.reserve(admission(2, at=later))
    monkeypatch.setattr(ledger_mod, "MAX_ENTRIES", 10)
    monkeypatch.setattr(ledger_mod, "MAX_LEDGER_BYTES", len(before) + 10)  # the byte bound, too
    with pytest.raises(LedgerRefused, match="ledger_full"):
        ledger.reserve(admission(2, at=later))
    assert ledger_bytes(ready) == before and [p.name for p in ready.iterdir()] == [ledger_mod.LEDGER_NAME]


# --- the injected lock ---------------------------------------------------------------------------

def test_a_lock_that_cannot_be_held_reads_and_writes_nothing(ready):
    clock, mutex = Clock(), TimeoutMutex()
    ledger = make(ready, mutex=mutex, clock=clock)
    before = ledger_bytes(ready)
    for call in (ledger.observe, lambda: ledger.reserve(admission()), lambda: ledger.finish(admission(), OUTCOME),
                 ledger.initialize):
        with pytest.raises(LedgerUnknown, match="lock_unavailable"):
            call()
    assert ledger_bytes(ready) == before and clock.reads == 0 and len(mutex.calls) == 4


def test_a_failed_release_after_a_won_reservation_is_unknown_and_the_entry_stays(ready):
    clock, mutex = Clock(T0), ReleaseMutex(fails=True)
    ledger = make(ready, mutex=mutex, clock=clock)
    with pytest.raises(LedgerUnknown) as caught:
        ledger.reserve(admission(1))
    assert caught.value.code == "lock_release_unknown"  # never reported as a win
    assert [e["state"] for e in doc(ready)["entries"]] == ["open"]  # applied, possibly durable, never undone
    assert [p.name for p in ready.iterdir()] == [ledger_mod.LEDGER_NAME]
    with pytest.raises(LedgerUnknown, match="lock_release_unknown"):
        ledger.observe()  # a read-only call reports it as well, and changes nothing
    later = T0 + timedelta(hours=2)
    clock.moment = later
    assert make(ready, clock=clock).reserve(admission(2, at=later)) is False  # no retry, refund or cleanup
    assert mutex.exits == [(None, None, None)] * 2


def test_a_failed_release_never_replaces_the_primary_error(ready):
    late = T0 + timedelta(seconds=61)
    before = ledger_bytes(ready)
    with pytest.raises(LedgerRefused) as caught:
        make(ready, mutex=ReleaseMutex(fails=True), clock=Clock(late)).reserve(admission())
    assert caught.value.code == "admission_stale_or_future" and caught.value.lock_release_unknown is True
    assert caught.value.__notes__ == ["lock_release_unknown"]
    with pytest.raises(LedgerRefused) as clean:  # the twin: a clean release leaves no mark
        make(ready, clock=Clock(late)).reserve(admission())
    assert clean.value.lock_release_unknown is False and getattr(clean.value, "__notes__", []) == []
    assert ledger_bytes(ready) == before


def test_a_suppressing_release_can_never_hide_a_ledger_error(ready, monkeypatch):
    mutex = ReleaseMutex(suppress=True)
    before = ledger_bytes(ready)
    with pytest.raises(LedgerRefused, match="admission_stale_or_future"):  # never turned into a win
        make(ready, mutex=mutex, clock=Clock(T0 + timedelta(seconds=61))).reserve(admission())
    assert ledger_bytes(ready) == before

    def refuse(src, dst):
        raise PermissionError("replace refused")

    monkeypatch.setattr(ledger_mod.os, "replace", refuse)
    with pytest.raises(LedgerUnknown, match="write_unknown"):
        make(ready, mutex=mutex, clock=Clock(T0)).reserve(admission())
    monkeypatch.undo()
    assert mutex.exits == [(None, None, None)] * 2  # the port never saw the outcome it could suppress


def test_every_read_clock_and_write_happens_under_the_root_mutex(ready, tmp_path, monkeypatch):
    mutex = Mutex()
    ledger = make(ready, mutex=mutex, clock=Clock(T0))
    identity = hashlib.sha256(os.path.normcase(str(ready)).encode("utf-8")).hexdigest()
    assert ledger.root_identity == identity
    assert ledger.mutex_name == ledger_mod.MUTEX_PREFIX + identity[:32]
    for name in ("_read", "_now", "_write", "_sync_root"):
        real = getattr(AdmissionLedger, name)

        def guarded(self, *args, _real=real):
            assert mutex.held
            return _real(self, *args)

        monkeypatch.setattr(AdmissionLedger, name, guarded)
    ledger.observe()
    assert ledger.reserve(admission()) is True
    ledger.finish(admission(), OUTCOME)
    assert mutex.calls == [(ledger.mutex_name, 4.0)] * 3
    other = tmp_path / "other"
    other.mkdir()
    assert make(other).mutex_name != ledger.mutex_name


# --- dormancy and composition --------------------------------------------------------------------

def test_the_module_is_dormant_and_non_reflective():
    tree = ast.parse(SOURCE)
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports == {"__future__", "contextlib", "datetime", "hashlib", "json", "os", "pathlib", "re", "secrets",
                       "stat", "typing", "tools.bridge_v2_activation", "tools.bridge_v2_grok_route"}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"getattr", "setattr", "hasattr", "exec", "eval", "compile", "globals", "locals", "vars",
                        "__import__", "open", "print", "input"}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not attrs & {"utcnow", "today", "system", "popen", "run", "remove", "unlink", "rmdir", "rmtree"}
    assert {ast.unparse(node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr == "now"} == {"self.clock"}
    calls = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    assert "AdmissionLedger" not in calls  # never instantiated at import
    verifiers = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                 and any(isinstance(item, ast.FunctionDef) and item.name == "verify" for item in node.body)]
    assert verifiers == ["ProvenanceVerifierPort"]  # the Protocol only: no default or built-in verifier


def test_nothing_imports_the_ledger():
    repo = Path(__file__).resolve().parents[2]
    users = [p for top in ("tools", "waggledance", "scripts") if (repo / top).is_dir()
             for p in (repo / top).rglob("*.py") if p.name != "wd_grok_admission_ledger.py"
             and "wd_grok_admission_ledger" in p.read_text(encoding="utf-8", errors="replace")]
    assert users == []


def test_the_ledger_satisfies_the_exact_broker_ledger_port(ready):
    """The 49f14d92 broker with fake non-ledger ports and this ledger (sequential, one process)."""
    from tools import wd_grok_broker as broker

    policy = {"schema": "fixture-signed-policy", "parameters": {"F20": {
        "model": "grok-4", "efforts": ["high"], "allowed_tools": [], "max_prompt_bytes": 48000,
        "max_intent_ttl_seconds": 900}}}
    prompt, head, tree = "Review the admission ledger. COMPLETE, no tools.", "a" * 40, "b" * 40
    task = "codex-lead-1/bridge-v2-grok-durable-ledger-20260930"

    def intent(n, at):
        return prepare_grok_consult(task_id=task, request_id=format(n, "x") * 32, request_revision=1, prompt=prompt,
                                    snapshot={"head": head, "tree": tree}, model="grok-4", effort="high",
                                    budget_class="shared_hourly", authorization_ref=canonical_sha256(policy),
                                    nonce="2" * 32, ttl_seconds=600, now=at - timedelta(seconds=30))

    class Snapshot:
        def observe(self):
            return {"readonly": True, "head": head, "tree": tree}

    class Activation:
        def evaluate(self, feature, *, expected_head, expected_tree):
            return Decision("F20", True, "enabled", canonical_sha256(policy), 3), policy

    class Helper:
        def __init__(self):
            self.calls = 0

        def status(self):
            return {"schema": "wd.grok-hourly.v1", "status": "answered", "eligible": True,
                    "last_attempt_utc": (T0 - timedelta(hours=2)).isoformat()}

        def consult(self, task_id, text):
            self.calls += 1
            return {"schema": "wd.grok-hourly.v1", "status": "answered", "task_id": task_id, "request_id": "4" * 32,
                    "last_attempt_utc": (T0 + timedelta(seconds=1)).isoformat(), "report_sha256": "5" * 64}

        def read_answer(self, report):
            return {"text": "No blocker.", "tool_calls": [], "report_sha256": "5" * 64}

    clock, helper = Clock(T0), Helper()
    ledger = make(ready, clock=clock)
    grok = broker.GrokBroker({"schema": broker.CONFIG_SCHEMA, "enabled": True}, clock=clock, snapshot=Snapshot(),
                             activation=Activation(), helper=helper, ledger=ledger)
    result = grok.consult(intent(1, T0), prompt)
    assert (result["verdict"], result["reasons"], helper.calls) == ("answered_bound", [], 1)
    closed = doc(ready)["entries"]
    assert len(closed) == 1 and closed[0]["state"] == "finished"
    assert closed[0]["outcome_sha256"] == canonical_sha256(result)
    assert closed[0]["admission_sha256"] == canonical_sha256(result["admission"])
    clock.moment = T0 + timedelta(minutes=10)
    again = grok.consult(intent(2, clock.moment), prompt)
    assert (again["verdict"], again["reasons"], helper.calls) == ("refuse", ["hourly_budget_used"], 1)
    assert len(doc(ready)["entries"]) == 1
