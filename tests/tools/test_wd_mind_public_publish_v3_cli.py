"""Proposed durable v3 CLI contract, using only synthetic source and pins.

The proposed CLI has the v2 main/env interface, plus explicitly pinned root
and previous-window anchors; its v3 lock/pending/next files are disjoint from
v2. This test exercises the real v3 session prepare/publish/recover APIs; it
does not claim production source authenticity, power-loss durability or ACLs.
"""
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event, read_fixture
from tools import wd_mind_public_publish_cli as v2
from tools import wd_mind_public_session as session


def digest(data):
    return hashlib.sha256(data).hexdigest()


def eligible(agent):
    return event(agent=agent, agent_uuid=ROSTER[agent], message="", paths=[], write_scope=[])


def set_pin(monkeypatch, name, path, data):
    path.write_bytes(data)
    monkeypatch.setenv("WD_MIND_PUBLIC_" + name.upper() + "_PATH", str(path))
    monkeypatch.setenv("WD_MIND_PUBLIC_" + name.upper() + "_SHA256", digest(data))


def result(capsys):
    captured = capsys.readouterr()
    assert captured.err == "" and captured.out.count("\n") == 1
    return json.loads(captured.out)


@pytest.fixture
def v3_world(tmp_path, monkeypatch):
    cli = importlib.import_module("tools.wd_mind_public_publish_v3_cli")
    for key in tuple(os.environ):
        if key.startswith("WD_MIND_PUBLIC_"):
            monkeypatch.delenv(key, raising=False)
    source_root = tmp_path / "synthetic-source"
    source_root.mkdir()
    private = event(message="synthetic-secret-message-729", to="fable-5",
                    payload={"public_status": True, "detail": "synthetic-secret-payload-729"})
    _, pins, source = read_fixture(source_root, [private, eligible("codex-lead-1")])
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    for name in ("anchor", "policy", "roster", "public_policy"):
        set_pin(monkeypatch, name, tmp_path / (name + ".json"), pins[name + "_bytes"])
    for name in ("root_anchor", "previous_anchor"):
        set_pin(monkeypatch, name, tmp_path / (name + ".json"), pins["anchor_bytes"])
    monkeypatch.setenv("WD_MIND_PUBLIC_AUDIT_ROOT", str(audit))
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", "none")
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_ENABLED", "1")
    return cli, audit, source


def test_v3_two_windows_recover_after_replace_without_touching_v2(tmp_path, monkeypatch, capsys):
    # Proposed interface intentionally requires a missing module rather than a
    # substitute or skip; its absence is the initial, explicit RED.
    cli = importlib.import_module("tools.wd_mind_public_publish_v3_cli")
    assert cli.PENDING_FILENAME != v2.PENDING_FILENAME
    assert cli.LOCK_FILENAME != v2.LOCK_FILENAME
    assert cli.NEXT_ANCHOR_FILENAME != v2.NEXT_ANCHOR_FILENAME
    for key in tuple(__import__("os").environ):
        if key.startswith("WD_MIND_PUBLIC_"):
            monkeypatch.delenv(key, raising=False)
    source_root = tmp_path / "synthetic-source"
    source_root.mkdir()
    _, pins, source = read_fixture(source_root, [eligible("codex-lead-1")])
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    for name in ("anchor", "policy", "roster", "public_policy"):
        set_pin(monkeypatch, name, tmp_path / (name + ".json"), pins[name + "_bytes"])
    root_anchor = pins["anchor_bytes"]
    set_pin(monkeypatch, "root_anchor", tmp_path / "root-anchor.json", root_anchor)
    set_pin(monkeypatch, "previous_anchor", tmp_path / "previous-anchor.json", root_anchor)
    monkeypatch.setenv("WD_MIND_PUBLIC_AUDIT_ROOT", str(audit))
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", "none")
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_ENABLED", "1")

    # Deliberately invalid v2 bytes prove v3 neither consults nor changes v2
    # publication, pending and continuation artifacts.
    v2_paths = [audit / session.FILENAME, audit / v2.PENDING_FILENAME, audit / v2.NEXT_ANCHOR_FILENAME]
    v2_contents = [b"v2-publication-sentinel", b"v2-pending-sentinel", b"v2-next-sentinel"]
    for path, data in zip(v2_paths, v2_contents):
        path.write_bytes(data)

    calls = {"prepare": 0, "publish": 0, "recover": 0}
    for label, name in (("prepare", "prepare_status_attempt_v3"), ("publish", "publish_status_v3"),
                        ("recover", "recover_status_attempt_v3")):
        real = getattr(session, name)

        def tracked(*args, _real=real, _label=label, **kwargs):
            calls[_label] += 1
            return _real(*args, **kwargs)

        monkeypatch.setattr(session, name, tracked)

    assert cli.main([]) == 0
    first = result(capsys)
    v3_file = audit / session.V3_FILENAME
    first_bytes = v3_file.read_bytes()
    assert first["publication_sha256"] == digest(first_bytes)
    assert json.loads(first_bytes)["revision"] == 1
    first_next = (audit / cli.NEXT_ANCHOR_FILENAME).read_bytes()
    first_next_doc = json.loads(first_next)
    assert first["next_anchor_sha256"] == digest(first_next)
    assert first_next_doc["cursor_start"]["offset"] == source.stat().st_size

    # The prior publication and issued next anchor are caller-pinned; the
    # previous window anchor remains the original root during window two.
    with source.open("ab") as stream:
        stream.write(enc(eligible("fable-5")) + b"\n")
    set_pin(monkeypatch, "anchor", tmp_path / "second-anchor.json", first_next)
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", first["publication_sha256"])
    real_replace = session.os.replace

    def crash_after_replace(src, dst):
        real_replace(src, dst)
        if str(dst) == str(v3_file):
            raise SystemExit("synthetic process interruption after replace")

    monkeypatch.setattr(session.os, "replace", crash_after_replace)
    try:
        cli.main([])
    except SystemExit as exc:
        assert str(exc) == "synthetic process interruption after replace"
    else:
        raise AssertionError("interruption was not injected after the v3 replace")
    capsys.readouterr()
    pending_path = audit / cli.PENDING_FILENAME
    saved = json.loads(pending_path.read_bytes())
    landed = v3_file.read_bytes()
    assert saved["prior_sha256"] == first["publication_sha256"]
    assert saved["attempt_sha256"] == digest(landed) != saved["prior_sha256"]
    assert saved["cursor_start"] == first_next_doc["cursor_start"]
    assert saved["candidate_cursor"]["offset"] == source.stat().st_size
    assert json.loads(landed)["revision"] == 2

    # Recovery may observe only the saved prior/attempt and must not republish.
    monkeypatch.setattr(session.os, "replace", real_replace)
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_MODE", "recover")
    before = calls["publish"]
    assert cli.main([]) == 0
    recovered = result(capsys)
    assert recovered["outcome"] == "landed_recovered"
    assert recovered["publication_sha256"] == saved["attempt_sha256"]
    assert calls["publish"] == before and calls["recover"] >= 1
    assert v3_file.read_bytes() == landed
    second_next = (audit / cli.NEXT_ANCHOR_FILENAME).read_bytes()
    assert recovered["next_anchor_sha256"] == digest(second_next)
    assert json.loads(second_next)["cursor_start"] == saved["candidate_cursor"]
    assert pending_path.exists()  # consume only at a verified continuation
    assert calls["prepare"] >= 2 and calls["publish"] >= 2
    assert [path.read_bytes() for path in v2_paths] == v2_contents
    doc = json.loads(session.read_status_v3(audit, lane="fable-5", agent_uuid=ROSTER["fable-5"],
        as_of=datetime.now(timezone.utc), max_age_seconds=900, enabled=True, **pins))
    assert [row["agent"] for row in doc["records"]] == ["fable-5"]


def test_before_replace_recovery_is_unchanged_and_fails_closed(v3_world, monkeypatch, capsys):
    cli, audit, source = v3_world
    real_replace = session.os.replace
    seen_recovery = []
    real_recover = session.recover_status_attempt_v3

    def before_replace(src, dst):
        if str(dst) == str(audit / session.V3_FILENAME):
            raise OSError("synthetic-failure-before-replace")
        return real_replace(src, dst)

    def tracked_recovery(*args, **kwargs):
        state = real_recover(*args, **kwargs)
        seen_recovery.append(state)
        return state

    monkeypatch.setattr(session.os, "replace", before_replace)
    monkeypatch.setattr(session, "recover_status_attempt_v3", tracked_recovery)
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error"] == "public_publish_v3_refused"
    assert seen_recovery == ["unchanged"]
    assert not (audit / session.V3_FILENAME).exists()
    assert not (audit / cli.PENDING_FILENAME).exists()
    assert not (audit / cli.NEXT_ANCHOR_FILENAME).exists()
    assert "synthetic-secret" not in captured.err and str(source) not in captured.err


def test_third_publication_value_is_not_adopted_or_deleted(v3_world, monkeypatch, capsys):
    cli, audit, source = v3_world
    real_replace = session.os.replace
    target = audit / session.V3_FILENAME
    third = b"synthetic-third-publication"

    def third_writer(src, dst):
        if str(dst) == str(target):
            target.write_bytes(third)
            raise OSError("synthetic-third-writer")
        return real_replace(src, dst)

    monkeypatch.setattr(session.os, "replace", third_writer)
    assert cli.main([]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and json.loads(captured.err)["error"] == "public_publish_v3_refused"
    assert target.read_bytes() == third
    assert (audit / cli.PENDING_FILENAME).exists()
    assert not (audit / cli.NEXT_ANCHOR_FILENAME).exists()
    assert "synthetic-secret" not in captured.err and str(source) not in captured.err


@pytest.mark.parametrize("tamper", ["pending", "next", "pin"])
def test_recovery_refuses_tampered_state_without_publishing(v3_world, monkeypatch, capsys, tamper):
    cli, audit, source = v3_world
    assert cli.main([]) == 0
    emitted = capsys.readouterr()
    assert emitted.err == "" and json.loads(emitted.out)["outcome"] == "published"
    target = audit / session.V3_FILENAME
    before = target.read_bytes()
    assert b"synthetic-secret" not in before and "synthetic-secret" not in emitted.out
    assert str(source) not in emitted.out and str(audit) not in emitted.out
    pending = audit / cli.PENDING_FILENAME
    next_path = audit / cli.NEXT_ANCHOR_FILENAME
    assert pending.exists() and next_path.exists()
    if tamper == "pending":
        saved = json.loads(pending.read_bytes())
        saved["candidate_cursor"]["offset"] += 1
        pending.write_bytes(enc(saved))
    elif tamper == "next":
        next_path.write_bytes(b"synthetic-third-anchor")
    else:
        monkeypatch.setenv("WD_MIND_PUBLIC_ROOT_ANCHOR_SHA256", "0" * 64)
    pending_before = pending.read_bytes()
    next_before = next_path.read_bytes()
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_MODE", "recover")
    assert cli.main([]) == 2
    refused = capsys.readouterr()
    assert refused.out == "" and json.loads(refused.err)["error"] == "public_publish_v3_refused"
    assert target.read_bytes() == before
    assert pending.read_bytes() == pending_before and next_path.read_bytes() == next_before
    assert "synthetic-secret" not in refused.err and str(source) not in refused.err and str(audit) not in refused.err
