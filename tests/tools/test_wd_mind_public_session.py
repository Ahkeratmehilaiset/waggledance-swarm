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
