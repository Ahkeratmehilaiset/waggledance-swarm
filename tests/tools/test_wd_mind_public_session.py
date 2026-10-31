"""Synthetic public-status atomic and fail-closed session contract."""
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
import hashlib
import json
import queue
import subprocess
import sys
import threading

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot
from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event, read_fixture


def _prior_hash(audit):
    path = audit / "public-status.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _expect_ready(process, expected, timeout=10):
    """Bound the read even when a child keeps its pipe open without speaking."""
    received = queue.Queue(maxsize=1)

    def read_line():
        try:
            received.put(process.stdout.readline())
        except (OSError, ValueError):
            received.put("")

    threading.Thread(target=read_line, daemon=True).start()
    try:
        line = received.get(timeout=timeout)
    except queue.Empty:
        pytest.fail("child readiness timed out")
    assert line.strip() == expected


def _reap(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def test_child_readiness_timeout_is_bounded():
    process = subprocess.Popen([sys.executable, "-B", "-c", "import time; time.sleep(30)"],
                               stdout=subprocess.PIPE, text=True)
    try:
        with pytest.raises(pytest.fail.Exception, match="child readiness timed out"):
            _expect_ready(process, "READY", timeout=0.1)
    finally:
        _reap(process)


@pytest.fixture
def state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    result, pins, _ = read_fixture(source, [event()])
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    return audit, result, pins


def test_publication_requires_explicit_genesis_and_cas(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    with pytest.raises(session.PublicStatusRefused):
        session.publish_status(audit, result, as_of=now, enabled=True, **pins)
    first = session.publish_status(audit, result, as_of=now, enabled=True,
                                   expected_previous_sha256=None, **pins)
    target = audit / session.FILENAME
    assert first == hashlib.sha256(target.read_bytes()).hexdigest()
    assert json.loads(target.read_bytes())["revision"] == 1
    with pytest.raises(session.PublicStatusRefused):
        session.publish_status(audit, result, as_of=now, enabled=True,
                               expected_previous_sha256=None, **pins)
    second = session.publish_status(audit, result, as_of=now, enabled=True,
                                    expected_previous_sha256=first, **pins)
    assert second != first
    assert json.loads(target.read_bytes())["revision"] == 2
    saved = target.read_bytes()
    with pytest.raises(session.PublicStatusRefused):
        session.publish_status(audit, result, as_of=now, enabled=True,
                               expected_previous_sha256=first, **pins)
    assert target.read_bytes() == saved


def test_v1_publication_refused_without_overwrite_or_automatic_migration(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True,
                           expected_previous_sha256=None, **pins)
    target = audit / session.FILENAME
    legacy = json.loads(target.read_bytes())
    legacy["schema"] = "wd.mind-public-status-publication.v1"
    del legacy["revision"]
    legacy_bytes = json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode("ascii")
    target.write_bytes(legacy_bytes)
    legacy_sha = hashlib.sha256(legacy_bytes).hexdigest()
    with pytest.raises(session.PublicStatusRefused):
        session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"], as_of=now,
                            max_age_seconds=900, enabled=True, **pins)
    for expected in (None, legacy_sha):
        with pytest.raises(session.PublicStatusRefused):
            session.publish_status(audit, result, as_of=now, enabled=True,
                                   expected_previous_sha256=expected, **pins)
        assert target.read_bytes() == legacy_bytes


_WORKER = r'''
import sys
from pathlib import Path
from datetime import datetime, timezone
from sandbox.mind.step2.test_status_public_filter import read_fixture, event
from tools.wd_mind_public_session import publish_status, PublicStatusRefused
source, audit, previous = map(Path, sys.argv[1:4])
source.mkdir()
result, pins, _ = read_fixture(source, [event()])
print("READY", flush=True)
sys.stdin.readline()
try:
    publish_status(audit, result, as_of=datetime.now(timezone.utc), enabled=True,
                   expected_previous_sha256=str(previous), timeout_seconds=5, **pins)
    print("WIN", flush=True)
except PublicStatusRefused:
    print("REFUSED", flush=True)
'''


def test_two_process_writers_use_one_persistent_lock_and_cas(state, tmp_path):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    first = session.publish_status(audit, result, as_of=datetime.now(timezone.utc), enabled=True,
                                   expected_previous_sha256=None, **pins)
    processes = [subprocess.Popen([sys.executable, "-B", "-c", _WORKER, str(tmp_path / f"worker{i}"),
                                   str(audit), first], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for i in range(2)]
    try:
        for process in processes:
            _expect_ready(process, "READY")
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        outcomes = []
        for process in processes:
            out, err = process.communicate(timeout=20)
            assert process.returncode == 0, err
            outcomes.append(out.strip())
        assert sorted(outcomes) == ["REFUSED", "WIN"]
        assert (audit / ".public-status.lock").is_file()
        assert json.loads((audit / session.FILENAME).read_bytes())["revision"] == 2
    finally:
        for process in processes:
            _reap(process)


def test_other_process_lock_contention_refuses_without_replacing(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    first = session.publish_status(audit, result, as_of=now, enabled=True,
                                   expected_previous_sha256=None, **pins)
    target = audit / session.FILENAME
    previous = target.read_bytes()
    script = ('import sys; from sandbox.mind.step2.reader_slot import reader_slot; '
              'p=reader_slot(sys.argv[1], timeout_seconds=1); p.__enter__(); '
              'print("LOCKED", flush=True); sys.stdin.readline(); p.__exit__(None,None,None)')
    process = subprocess.Popen([sys.executable, "-B", "-c", script, str(audit / ".public-status.lock")],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _expect_ready(process, "LOCKED")
        with pytest.raises(session.PublicStatusRefused):
            session.publish_status(audit, result, as_of=now, enabled=True,
                                   expected_previous_sha256=first, timeout_seconds=0.05, **pins)
        assert target.read_bytes() == previous
    finally:
        try:
            process.communicate("release\n", timeout=10)
        finally:
            _reap(process)


def test_open_reader_and_other_process_writer_remain_fail_closed(state, tmp_path, monkeypatch):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    first = session.publish_status(audit, result, as_of=now, enabled=True,
                                   expected_previous_sha256=None, **pins)
    target = audit / session.FILENAME
    before = target.read_bytes()
    process = subprocess.Popen([sys.executable, "-B", "-c", _WORKER, str(tmp_path / "held-reader-worker"),
                                str(audit), first], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    real_open = session.Path.open
    outcome = []

    class HeldReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size):
            out, err = process.communicate("go\n", timeout=20)
            assert process.returncode == 0, err
            outcome.append(out.strip())
            return self.stream.read(size)

    def held_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return HeldReader(stream) if path == target and mode == "rb" else stream

    try:
        _expect_ready(process, "READY")
        monkeypatch.setattr(session.Path, "open", held_open)
        read = json.loads(session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                                               as_of=now, max_age_seconds=900, enabled=True, **pins))
        assert read["source_freshness"] == "UNKNOWN"
        assert outcome[0] in ("WIN", "REFUSED")
        monkeypatch.setattr(session.Path, "open", real_open)
        if outcome[0] == "REFUSED":
            assert target.read_bytes() == before
        else:
            assert json.loads(target.read_bytes())["revision"] == 2
        print("OPEN_READER_WRITER_OUTCOME=" + outcome[0])
    finally:
        _reap(process)


def test_default_off_and_atomic_failure_preserves_old(state, monkeypatch):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.publish_status(audit, result, as_of=now, **pins)
    assert list(audit.iterdir()) == []
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    pointer = audit / "public-status.json"
    previous = pointer.read_bytes()
    def interrupted(*args):
        raise OSError("synthetic failure")
    monkeypatch.setattr(session.os, "replace", interrupted)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    assert pointer.read_bytes() == previous


def test_partial_staging_write_never_replaces_previous(state, monkeypatch):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    pointer = audit / "public-status.json"
    previous = pointer.read_bytes()
    real_open = session.Path.open
    class ShortWriter:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.stream.close()
        def write(self, data):
            self.stream.write(data[:-1])
            return len(data) - 1
        def flush(self):
            return self.stream.flush()
        def fileno(self):
            return self.stream.fileno()
    def short_open(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return ShortWriter(stream) if path.name.startswith(".public-status-") and mode == "xb" else stream
    monkeypatch.setattr(session.Path, "open", short_open)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    assert pointer.read_bytes() == previous


def test_session_roster_freshness_hash_and_output_privacy(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    kwargs = dict(lane="fable-5", agent_uuid=ROSTER["fable-5"], as_of=now, max_age_seconds=900, **pins)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.read_status(audit, **kwargs)
    data = session.read_status(audit, enabled=True, **kwargs)
    assert json.loads(data)["lane"] == "fable-5"
    assert b"sha256" not in data and b"cursor" not in data and b"source_path" not in data
    assert ROSTER["fable-5"].encode() not in data
    for change in ({"agent_uuid": ROSTER["codex-lead-1"]}, {"lane": "unknown"},
                   {"as_of": now + timedelta(seconds=901)}, {"as_of": now - timedelta(seconds=1)},
                   {"max_age_seconds": 0}, {"roster_sha256": "0" * 64}):
        with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
            session.read_status(audit, enabled=True, **dict(kwargs, **change))
    path = audit / "public-status.json"
    doc = json.loads(path.read_bytes())
    doc["body"]["records"][0]["task_id"] = "tampered"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.read_status(audit, enabled=True, **kwargs)


def test_recomputed_body_digest_does_not_allow_noncanonical_time_to_leak(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    path = audit / "public-status.json"
    doc = json.loads(path.read_bytes())
    doc["body"]["records"][0]["time_utc"] = "2026-10-31X12:00:00.000000Z"
    canonical = json.dumps(doc["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    doc["body_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"], as_of=now,
                            max_age_seconds=900, enabled=True, **pins)


def test_self_hash_is_not_writer_authentication(state):
    """A same-user writer can rehash an allowlisted forged body; this is NOT secure attribution."""
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    path = audit / "public-status.json"
    doc = json.loads(path.read_bytes())
    doc["body"]["records"][0]["status"] = "in_progress"
    canonical = json.dumps(doc["body"], sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    doc["body_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(json.dumps(doc), encoding="utf-8")
    read = json.loads(session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                                          as_of=now, max_age_seconds=900, enabled=True, **pins))
    assert read["records"][0]["status"] == "in_progress" and read["authority"] == "none"
    assert read["identity_verification"] == "caller_declared_not_authenticated"
    assert read["source_freshness"] == "UNKNOWN"


def test_republication_does_not_claim_source_freshness(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    # The same old source snapshot can be republished with a later caller time.
    session.publish_status(audit, result, as_of=now - timedelta(days=2), enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    read = json.loads(session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                                          as_of=now, max_age_seconds=900, enabled=True, **pins))
    assert read["publication_age_seconds"] == 0 and read["source_freshness"] == "UNKNOWN"


def test_empty_and_next_delta_are_labeled_as_last_delta_only(state):
    from tools import wd_mind_public_session as session
    audit, current, pins = state
    now = datetime.now(timezone.utc)

    def publish_and_read(result, current_pins):
        session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **current_pins)
        raw = json.loads((audit / "public-status.json").read_bytes())
        read = json.loads(session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                                              as_of=now, max_age_seconds=900, enabled=True, **current_pins))
        assert raw["coverage"] == "last_delta_only" and read["coverage"] == "last_delta_only"
        return read

    assert len(publish_and_read(current, pins)["records"]) == 1
    for candidate, count in ((event(to="fable-5"), 0), (event(status="info"), 1)):
        anchor = json.loads(pins["anchor_bytes"])
        source, gen = anchor["source_path"], anchor["generation_path"]
        start = current["candidate_cursor"]
        with open(source, "ab") as stream:
            stream.write(enc(candidate) + b"\n")
        anchor["cursor_start"] = asdict(start)
        anchor_bytes = enc(anchor)
        pins = dict(pins, anchor_bytes=anchor_bytes, anchor_sha256=hashlib.sha256(anchor_bytes).hexdigest())
        current = shared_bridge_snapshot.read_anchored_delta(source, start, frozenset({"public-task", "nonreview-private"}),
            anchor_bytes=anchor_bytes, anchor_sha256=pins["anchor_sha256"], generation_path=gen,
            max_bytes=65536, max_rows=100, policy_bytes=pins["policy_bytes"], policy_sha256=pins["policy_sha256"],
            roster_bytes=pins["roster_bytes"], roster_sha256=pins["roster_sha256"],
            public_policy_bytes=pins["public_policy_bytes"], public_policy_sha256=pins["public_policy_sha256"])
        # Zero records describes this delta, not total historical absence.
        assert len(publish_and_read(current, pins)["records"]) == count


@pytest.mark.parametrize("coverage", [None, "complete", "latest", True])
def test_missing_or_untruthful_coverage_refused(state, coverage):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    path = audit / "public-status.json"
    doc = json.loads(path.read_bytes())
    if coverage is None:
        doc.pop("coverage")
    else:
        doc["coverage"] = coverage
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                            as_of=now, max_age_seconds=900, enabled=True, **pins)


@pytest.mark.parametrize("content", [b'{"schema":"x","schema":"y"}', b" " * 1025])
def test_duplicate_or_over_budget_publication_refused(state, monkeypatch, content):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, expected_previous_sha256=_prior_hash(audit), **pins)
    (audit / "public-status.json").write_bytes(content)
    monkeypatch.setattr(session, "MAX_BYTES", 1024)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"], as_of=now,
                            max_age_seconds=900, enabled=True, **pins)

# ---- RCO1 U1 P3 v2 (Lead lead-mind-status-digest-fable-20261031-v2): recovery after an AMBIGUOUS publish failure is ----
# ---- BOUND to the caller's own prior digest and the exact intended attempt digest, obtained from the bounded ----
# ---- attempt-preparation contract BEFORE the write; it answers only landed / unchanged and refuses any third digest. ----
def _attempt_kwargs(pins, now, previous):
    return dict(as_of=now, enabled=True, expected_previous_sha256=previous, **pins)


def _refused(call, *args, **kwargs):
    with pytest.raises(Exception, match="^public_status_refused$") as info:
        call(*args, **kwargs)
    assert type(info.value).__name__ == "PublicStatusRefused"


def test_attempt_preparation_binds_the_publish_and_recovery_answers_landed_or_unchanged(state, monkeypatch, tmp_path):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    target = audit / session.FILENAME
    before = sorted(p.name for p in audit.iterdir() if p.name != session.LOCK_FILENAME)  # the shared lock slot
    intended = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, None))
    assert type(intended) is str and session._HEX.fullmatch(intended)
    assert intended == session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, None))
    assert intended != session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now + timedelta(minutes=1), None))
    assert sorted(p.name for p in audit.iterdir() if p.name != session.LOCK_FILENAME) == before  # preparation never writes
    # a publish bound to another intended digest refuses BEFORE any write
    _refused(session.publish_status, audit, result, attempt_sha256="0" * 64, **_attempt_kwargs(pins, now, None))
    assert sorted(p.name for p in audit.iterdir() if p.name != session.LOCK_FILENAME) == before
    first = session.publish_status(audit, result, attempt_sha256=intended, **_attempt_kwargs(pins, now, None))
    assert first == intended == hashlib.sha256(target.read_bytes()).hexdigest()
    # ambiguous failure where the replace LANDED
    second = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, first))
    real_replace = session.os.replace

    def landed_then_failed(src, dst):
        real_replace(src, dst)
        raise OSError("synthetic ambiguous failure after replace")

    monkeypatch.setattr(session.os, "replace", landed_then_failed)
    _refused(session.publish_status, audit, result, attempt_sha256=second, **_attempt_kwargs(pins, now, first))
    monkeypatch.setattr(session.os, "replace", real_replace)
    assert session.recover_status_attempt(audit, prior_sha256=first, attempt_sha256=second, enabled=True) == "landed"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == second
    assert json.loads(target.read_bytes())["revision"] == 2
    # the retry continues from the landed attempt; the stale prior refuses
    third = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, second))
    _refused(session.publish_status, audit, result, attempt_sha256=third, **_attempt_kwargs(pins, now, first))

    # ambiguous failure where the replace did NOT land
    def failed_before_replace(src, dst):
        raise OSError("synthetic failure before replace")

    monkeypatch.setattr(session.os, "replace", failed_before_replace)
    _refused(session.publish_status, audit, result, attempt_sha256=third, **_attempt_kwargs(pins, now, second))
    monkeypatch.setattr(session.os, "replace", real_replace)
    assert session.recover_status_attempt(audit, prior_sha256=second, attempt_sha256=third, enabled=True) == "unchanged"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == second
    assert session.publish_status(audit, result, attempt_sha256=third, **_attempt_kwargs(pins, now, second)) == third
    assert json.loads(target.read_bytes())["revision"] == 3
    assert not [p for p in audit.iterdir() if p.name.startswith(".public-status-")]  # no orphan staging
    # a genesis attempt that never landed is unchanged; the unbound publish path still works for existing callers
    other = tmp_path / "other" / ".codex-audit"
    other.mkdir(parents=True)
    genesis = session.prepare_status_attempt(other, result, **_attempt_kwargs(pins, now, None))
    assert session.recover_status_attempt(other, prior_sha256=None, attempt_sha256=genesis, enabled=True) == "unchanged"
    assert session.publish_status(other, result, **_attempt_kwargs(pins, now, None)) == genesis


def test_recovery_refuses_a_third_digest_from_an_interleaved_writer(state, monkeypatch):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    target = audit / session.FILENAME
    assert not hasattr(session, "current_publication_sha256")  # the unbound observer of v1 is gone
    # writer A attempts genesis; the replace lands but A sees a failure
    a_attempt = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, None))
    real_replace = session.os.replace

    def landed_then_failed(src, dst):
        real_replace(src, dst)
        raise OSError("synthetic ambiguous failure after replace")

    monkeypatch.setattr(session.os, "replace", landed_then_failed)
    _refused(session.publish_status, audit, result, attempt_sha256=a_attempt, **_attempt_kwargs(pins, now, None))
    monkeypatch.setattr(session.os, "replace", real_replace)
    # writer B publishes on top of the landed A publication through its own cooperation
    b_now = now + timedelta(minutes=1)
    b_attempt = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, b_now, a_attempt))
    assert session.publish_status(audit, result, attempt_sha256=b_attempt, **_attempt_kwargs(pins, b_now, a_attempt)) == b_attempt
    assert json.loads(target.read_bytes())["revision"] == 2
    # A recovers: neither its prior (genesis) nor its attempt is present -> refused; B's digest is never returned
    _refused(session.recover_status_attempt, audit, prior_sha256=None, attempt_sha256=a_attempt, enabled=True)
    # and A cannot republish its stale snapshot over B with any digest it legitimately knows
    for previous in (None, a_attempt):
        _refused(session.publish_status, audit, result, attempt_sha256=a_attempt, **_attempt_kwargs(pins, now, previous))
        _refused(session.prepare_status_attempt, audit, result, **_attempt_kwargs(pins, now, previous))
    assert hashlib.sha256(target.read_bytes()).hexdigest() == b_attempt
    assert json.loads(target.read_bytes())["revision"] == 2


def test_attempt_contract_is_bounded_and_fail_closed(state, tmp_path):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    target = audit / session.FILENAME
    before = sorted(p.name for p in audit.iterdir() if p.name != session.LOCK_FILENAME)  # the shared lock slot
    kw = _attempt_kwargs(pins, now, None)
    prepare, recover = session.prepare_status_attempt, session.recover_status_attempt
    _refused(prepare, audit, result, **dict(kw, enabled=False))  # OFF by default
    _refused(prepare, audit, result, **dict(kw, enabled=1))
    _refused(prepare, audit, result, **{k: v for k, v in kw.items() if k != "expected_previous_sha256"})  # explicit genesis required
    _refused(prepare, audit, result, **dict(kw, expected_previous_sha256="0" * 64))  # a prior claimed at genesis
    _refused(prepare, audit, result, **dict(kw, roster_sha256="0" * 64))  # pins validated first
    _refused(prepare, audit / "nested", result, **kw)
    assert sorted(p.name for p in audit.iterdir() if p.name != session.LOCK_FILENAME) == before
    intended = prepare(audit, result, **kw)
    first = session.publish_status(audit, result, attempt_sha256=intended, **kw)
    saved = target.read_bytes()
    _refused(prepare, audit, result, **kw)  # genesis claimed while a publication exists
    _refused(prepare, audit, result, **dict(kw, expected_previous_sha256="1" * 64))  # stale prior
    _refused(session.publish_status, audit, result, attempt_sha256=first.upper(), **dict(kw, expected_previous_sha256=first))
    _refused(session.publish_status, audit, result, attempt_sha256=b"x" * 64, **dict(kw, expected_previous_sha256=first))
    # a v1 publication on disk refuses preparation and is never migrated
    legacy = json.loads(saved)
    legacy["schema"] = "wd.mind-public-status-publication.v1"
    del legacy["revision"]
    legacy_bytes = json.dumps(legacy).encode("utf-8")
    target.write_bytes(legacy_bytes)
    _refused(prepare, audit, result, **dict(kw, expected_previous_sha256=hashlib.sha256(legacy_bytes).hexdigest()))
    assert target.read_bytes() == legacy_bytes
    target.write_bytes(saved)
    # recovery input bounds
    _refused(recover, audit, prior_sha256=None, attempt_sha256=first, enabled=False)
    _refused(recover, audit, prior_sha256=None, attempt_sha256=first, enabled=1)
    _refused(recover, audit, prior_sha256=first, attempt_sha256=first, enabled=True)  # degenerate pair
    _refused(recover, audit, prior_sha256="x", attempt_sha256=first, enabled=True)
    _refused(recover, audit, prior_sha256=None, attempt_sha256=first.upper(), enabled=True)
    _refused(recover, audit, prior_sha256=None, attempt_sha256=first.encode("ascii"), enabled=True)
    _refused(recover, audit / "nested", prior_sha256=None, attempt_sha256=first, enabled=True)
    # the present publication equals neither digest -> refused, although it is a valid publication
    _refused(recover, audit, prior_sha256="2" * 64, attempt_sha256="3" * 64, enabled=True)
    _refused(recover, audit, prior_sha256=None, attempt_sha256="3" * 64, enabled=True)
    # the answer is one of two words and only compares bytes the caller already named
    assert recover(audit, prior_sha256="2" * 64, attempt_sha256=first, enabled=True) == "landed"
    assert recover(audit, prior_sha256=first, attempt_sha256="3" * 64, enabled=True) == "unchanged"
    # a prior claimed while no publication exists -> refused, not unchanged
    other = tmp_path / "other" / ".codex-audit"
    other.mkdir(parents=True)
    _refused(recover, other, prior_sha256=first, attempt_sha256="3" * 64, enabled=True)
    assert recover(other, prior_sha256=None, attempt_sha256="3" * 64, enabled=True) == "unchanged"
    # tampered (same length), over-size and non-regular targets refuse and are left exactly as found
    tampered = bytearray(saved)
    tampered[-2] ^= 0x01
    for content in (bytes(tampered), saved + b" " * (session.MAX_BYTES + 1 - len(saved))):
        target.write_bytes(content)
        _refused(recover, audit, prior_sha256=first, attempt_sha256="3" * 64, enabled=True)
        assert target.read_bytes() == content
    target.write_bytes(saved)
    assert recover(audit, prior_sha256=first, attempt_sha256="3" * 64, enabled=True) == "unchanged"
    target.unlink()
    target.mkdir()
    _refused(recover, audit, prior_sha256=first, attempt_sha256="3" * 64, enabled=True)
    _refused(recover, audit, prior_sha256=None, attempt_sha256="3" * 64, enabled=True)


def test_attempt_contract_waits_on_the_same_lock_and_refuses_on_contention(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    target = audit / session.FILENAME
    first = session.publish_status(audit, result, **_attempt_kwargs(pins, now, None))
    second = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, first))
    saved = target.read_bytes()
    script = ('import sys; from sandbox.mind.step2.reader_slot import reader_slot; '
              'p=reader_slot(sys.argv[1], timeout_seconds=1); p.__enter__(); '
              'print("LOCKED", flush=True); sys.stdin.readline(); p.__exit__(None,None,None)')
    process = subprocess.Popen([sys.executable, "-B", "-c", script, str(audit / session.LOCK_FILENAME)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _expect_ready(process, "LOCKED")
        _refused(session.prepare_status_attempt, audit, result, timeout_seconds=0.05, **_attempt_kwargs(pins, now, first))
        _refused(session.recover_status_attempt, audit, prior_sha256=first, attempt_sha256=second, enabled=True,
                 timeout_seconds=0.05)
    finally:
        try:
            process.communicate("release\n", timeout=10)
        finally:
            _reap(process)
    assert target.read_bytes() == saved
    assert session.recover_status_attempt(audit, prior_sha256=first, attempt_sha256=second, enabled=True,
                                          timeout_seconds=1) == "unchanged"
    assert session.prepare_status_attempt(audit, result, timeout_seconds=1, **_attempt_kwargs(pins, now, first)) == second
    assert (audit / session.LOCK_FILENAME).is_file()

# ---- v3 (Lead lead-mind-status-digest-fable-20261031-v3): the preparation side effect is exactly the shared lock ----
# ---- slot, never a publication; a prior whose exact digest is supplied is still refused when it is structurally ----
# ---- incomplete, while a structurally complete old prior is never refused merely because it is stale. ----
def test_prepare_creates_only_the_shared_lock_slot_and_never_a_publication(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    assert sorted(p.name for p in audit.iterdir()) == []
    intended = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, None))
    assert sorted(p.name for p in audit.iterdir()) == [session.LOCK_FILENAME]  # the lock slot, nothing else
    assert (audit / session.LOCK_FILENAME).is_file() and (audit / session.LOCK_FILENAME).stat().st_size == 0
    assert not (audit / session.FILENAME).exists()
    assert session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, None)) == intended
    assert sorted(p.name for p in audit.iterdir()) == [session.LOCK_FILENAME]
    assert session.recover_status_attempt(audit, prior_sha256=None, attempt_sha256=intended, enabled=True) == "unchanged"
    assert sorted(p.name for p in audit.iterdir()) == [session.LOCK_FILENAME]
    assert session.publish_status(audit, result, attempt_sha256=intended, **_attempt_kwargs(pins, now, None)) == intended
    assert sorted(p.name for p in audit.iterdir()) == sorted([session.LOCK_FILENAME, session.FILENAME])


def _prior_variants(saved):
    """Priors that keep the v2 schema and a valid revision but are structurally incomplete or malformed."""
    doc = json.loads(saved)
    variants = {}
    no_body = dict(doc)
    del no_body["body"]
    variants["missing body"] = no_body
    no_digest = dict(doc)
    del no_digest["body_sha256"]
    variants["missing body digest"] = no_digest
    variants["empty body"] = dict(doc, body={})
    variants["body digest mismatch"] = dict(doc, body_sha256="0" * 64)
    variants["non-hex pin"] = dict(doc, anchor_sha256="x")
    variants["missing pin"] = {k: v for k, v in doc.items() if k != "roster_sha256"}
    variants["bad time"] = dict(doc, as_of_utc="not-a-time")
    variants["extra key"] = dict(doc, recovered=True)
    variants["wrong authority"] = dict(doc, authority="writer")
    variants["wrong coverage"] = dict(doc, coverage="complete")
    body = json.loads(json.dumps(doc["body"]))
    body["records"].append({"time_utc": "2026-01-01T00:00:00.000000Z", "agent": "codex-lead-1", "type": "status"})
    variants["record missing a key"] = dict(doc, body=body)
    body = json.loads(json.dumps(doc["body"]))
    body["records"].append({"time_utc": "2026-01-01T00:00:00.000000Z", "agent": "claude-rco-1", "type": "status",
                            "status": "active", "task_id": "lead-mind-production-20261031"})
    variants["review lane record"] = dict(doc, body=body)
    body = json.loads(json.dumps(doc["body"]))
    body["records"].append({"time_utc": "2026-01-01T00:00:00.000000Z", "agent": "codex-lead-1", "type": 7,
                            "status": "active", "task_id": "lead-mind-production-20261031"})
    variants["non-string record type"] = dict(doc, body=body)
    for name, value in list(variants.items()):
        if "body" in value and "body_sha256" in value and name not in ("body digest mismatch",):
            value["body_sha256"] = hashlib.sha256(json.dumps(value["body"], sort_keys=True, separators=(",", ":"),
                                                             ensure_ascii=True).encode("ascii")).hexdigest()
    return {name: json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") for name, value in variants.items()}


def test_structurally_incomplete_prior_is_refused_with_its_exact_digest_but_a_stale_prior_is_not(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    target = audit / session.FILENAME
    first = session.publish_status(audit, result, **_attempt_kwargs(pins, now, None))
    saved = target.read_bytes()
    assert json.loads(saved)["body"]["records"], "the fixture publication must carry at least one record"
    variants = _prior_variants(saved)
    assert len(variants) == 13
    for name, raw in variants.items():
        doc = json.loads(raw)
        assert doc["schema"] == session.SCHEMA and doc["revision"] == 1, name  # schema and revision alone pass
        target.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        _refused(session.prepare_status_attempt, audit, result, **_attempt_kwargs(pins, now, digest))
        _refused(session.publish_status, audit, result, **_attempt_kwargs(pins, now, digest))
        assert target.read_bytes() == raw, name  # never repaired, removed or replaced
        assert not [p for p in audit.iterdir() if p.name.startswith(".public-status-")], name
    target.write_bytes(saved)
    # a structurally complete prior is accepted although it is STALE by any reader bound
    old = now - timedelta(days=2)
    stale_first = session.publish_status(audit, result, **_attempt_kwargs(pins, old, first))
    assert json.loads(target.read_bytes())["as_of_utc"] == old.isoformat()
    _refused(session.read_status, audit, lane="codex-lead-1", agent_uuid=ROSTER["codex-lead-1"], as_of=now,
             max_age_seconds=900, enabled=True, **pins)
    second = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, stale_first))
    assert session.publish_status(audit, result, attempt_sha256=second, **_attempt_kwargs(pins, now, stale_first)) == second
    assert json.loads(target.read_bytes())["revision"] == 3
    # a complete prior under the SAME pins is also checked for policy membership of its records
    doc = json.loads(target.read_bytes())
    body = doc["body"]
    body["records"].append({"time_utc": "2026-01-01T00:00:00.000000Z", "agent": "codex-lead-1", "type": "status",
                            "status": "active", "task_id": "not-in-this-policy"})
    doc["body_sha256"] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                                   ensure_ascii=True).encode("ascii")).hexdigest()
    foreign_task = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    target.write_bytes(foreign_task)
    _refused(session.prepare_status_attempt, audit, result,
             **_attempt_kwargs(pins, now, hashlib.sha256(foreign_task).hexdigest()))
    assert target.read_bytes() == foreign_task
    # a complete prior under OTHER pins (pin rotation) is accepted on shape and digest only: its record membership
    # under its own policy cannot be checked here and is NOT claimed (reported, not inferred)
    rotated = dict(doc, anchor_sha256="1" * 64)
    rotated_raw = json.dumps(rotated, sort_keys=True, separators=(",", ":")).encode("ascii")
    target.write_bytes(rotated_raw)
    rotated_digest = hashlib.sha256(rotated_raw).hexdigest()
    third = session.prepare_status_attempt(audit, result, **_attempt_kwargs(pins, now, rotated_digest))
    assert session.publish_status(audit, result, attempt_sha256=third, **_attempt_kwargs(pins, now, rotated_digest)) == third
    assert json.loads(target.read_bytes())["revision"] == 4
    assert json.loads(target.read_bytes())["anchor_sha256"] == pins["anchor_sha256"]

