"""Synthetic public-status atomic and fail-closed session contract."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, event, read_fixture


@pytest.fixture
def state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    result, pins, _ = read_fixture(source, [event()])
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    return audit, result, pins


def test_default_off_and_atomic_failure_preserves_old(state, monkeypatch):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.publish_status(audit, result, as_of=now, **pins)
    assert list(audit.iterdir()) == []
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
    pointer = audit / "public-status.json"
    previous = pointer.read_bytes()
    def interrupted(*args):
        raise OSError("synthetic failure")
    monkeypatch.setattr(session.os, "replace", interrupted)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.publish_status(audit, result, as_of=now, enabled=True, **pins)
    assert pointer.read_bytes() == previous


def test_partial_staging_write_never_replaces_previous(state, monkeypatch):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
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
        session.publish_status(audit, result, as_of=now, enabled=True, **pins)
    assert pointer.read_bytes() == previous


def test_session_roster_freshness_hash_and_output_privacy(state):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
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
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
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
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
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
    session.publish_status(audit, result, as_of=now - timedelta(days=2), enabled=True, **pins)
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
    read = json.loads(session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
                                          as_of=now, max_age_seconds=900, enabled=True, **pins))
    assert read["publication_age_seconds"] == 0 and read["source_freshness"] == "UNKNOWN"


@pytest.mark.parametrize("content", [b'{"schema":"x","schema":"y"}', b" " * 1025])
def test_duplicate_or_over_budget_publication_refused(state, monkeypatch, content):
    from tools import wd_mind_public_session as session
    audit, result, pins = state
    now = datetime.now(timezone.utc)
    session.publish_status(audit, result, as_of=now, enabled=True, **pins)
    (audit / "public-status.json").write_bytes(content)
    monkeypatch.setattr(session, "MAX_BYTES", 1024)
    with pytest.raises(session.PublicStatusRefused, match="^public_status_refused$"):
        session.read_status(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"], as_of=now,
                            max_age_seconds=900, enabled=True, **pins)
