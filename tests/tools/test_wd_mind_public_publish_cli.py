"""Synthetic contract of the one-shot public-status publisher; no live bridge, starter or scheduler.

Every source, pin and lane here is synthetic. Environment pins, lane identity and the publisher are caller
assertions, never authentication; the published coverage is the last anchored delta only.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event, read_fixture
from tools import wd_mind_public_publish_cli as publisher
from tools import wd_mind_public_session as session
from tools import wd_mind_public_session_cli as reader

PINS = ("anchor", "policy", "roster", "public_policy")
OUTPUT_KEYS = {"schema", "outcome", "publication_sha256", "records", "lag_bytes_observed", "coverage", "authority",
               "source_authentication", "source_freshness", "publisher_identity", "next_anchor_sha256"}
PRIVATE_TEXT = "synthetic-private-text"
RCO_CAPABILITIES = ["rco_review", "security_review", "adversarial_review", "bridge_event", "work_queue"]


def eligible(agent="codex-lead-1"):
    return event(agent=agent, agent_uuid=ROSTER[agent], message="", paths=[], write_scope=[])


def private_row():
    return event(to="fable-5", message=PRIVATE_TEXT, task_id="nonreview-private", payload={})


def reviewer_row():
    return event(agent="claude-rco-1", agent_uuid=ROSTER["claude-rco-1"], capabilities=RCO_CAPABILITIES,
                 message="", paths=[], write_scope=[])


@pytest.fixture
def world(tmp_path, monkeypatch):
    def build(rows, previous="none"):
        source = tmp_path / "source"
        source.mkdir()
        _, pins, log = read_fixture(source, rows)
        audit = tmp_path / ".codex-audit"
        audit.mkdir()
        for item in PINS:
            file = tmp_path / (item + ".json")
            file.write_bytes(pins[item + "_bytes"])
            monkeypatch.setenv("WD_MIND_PUBLIC_" + item.upper() + "_PATH", str(file))
            monkeypatch.setenv("WD_MIND_PUBLIC_" + item.upper() + "_SHA256", pins[item + "_sha256"])
        monkeypatch.setenv("WD_MIND_PUBLIC_AUDIT_ROOT", str(audit))
        monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", previous)
        monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_ENABLED", "1")
        monkeypatch.delenv("WD_MIND_PUBLIC_ENABLED", raising=False)
        return audit, log
    return build


def publication(audit):
    target = audit / session.FILENAME
    return target.read_bytes() if target.exists() else None


def next_anchor(audit):
    target = audit / publisher.NEXT_ANCHOR_FILENAME
    return target.read_bytes() if target.exists() else None


def pending(audit):
    target = audit / publisher.PENDING_FILENAME
    return target.read_bytes() if target.exists() else None


def follow(monkeypatch, audit, digest):
    """The caller pins the landed digest as prior and the issued next anchor for the next window."""
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", digest)
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_PATH", str(audit / publisher.NEXT_ANCHOR_FILENAME))
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_SHA256", hashlib.sha256(next_anchor(audit)).hexdigest())


def published(capsys):
    captured = capsys.readouterr()
    assert captured.err == ""
    line = captured.out
    assert line.endswith("\n") and line.count("\n") == 1
    return json.loads(line), line


def refused(capsys):
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"error": "public_publish_refused", "authority": "none"}
    return captured.err


# A1 ------------------------------------------------------------------------------------------------------------

def test_publisher_is_default_off_and_takes_no_arguments(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    monkeypatch.delenv("WD_MIND_PUBLIC_PUBLISH_ENABLED")
    monkeypatch.setattr(reader, "_bounded_pin", lambda *a: pytest.fail("disabled must not read pins"))
    assert publisher.main([]) == 2
    refused(capsys)
    for value in ("0", "true", "yes", " 1"):
        monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_ENABLED", value)
        assert publisher.main([]) == 2
        refused(capsys)
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_ENABLED", "1")
    assert publisher.main(["--anything"]) == 2
    refused(capsys)
    # The reader's own switch never enables publishing.
    monkeypatch.delenv("WD_MIND_PUBLIC_PUBLISH_ENABLED")
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) is None


# A2 + A8 -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane", sorted(ROSTER))
def test_existing_reader_cli_reads_the_same_records_for_each_lane_without_private_rows(world, monkeypatch, capsys,
                                                                                         lane):
    audit, log = world([eligible(), private_row(), eligible("fable-5")])
    assert publisher.main([]) == 0
    result, line = published(capsys)
    assert set(result) == OUTPUT_KEYS and result["outcome"] == "published" and result["records"] == 2
    assert result["publication_sha256"] == hashlib.sha256(publication(audit)).hexdigest()
    for text in (PRIVATE_TEXT, str(log), str(audit), "nonreview-private", ROSTER["codex-lead-1"]):
        assert text not in line
    monkeypatch.setenv("WD_MIND_PUBLIC_ENABLED", "1")
    monkeypatch.setenv("WD_MIND_PUBLIC_MAX_AGE_SECONDS", "900")
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", ROSTER[lane])
    assert reader.main([]) == 0
    captured = capsys.readouterr()
    view = json.loads(captured.out)
    assert captured.err == "" and view["lane"] == lane
    assert [(r["agent"], r["task_id"]) for r in view["records"]] == [("codex-lead-1", "public-task"),
                                                                     ("fable-5", "public-task")]
    assert PRIVATE_TEXT not in captured.out and view["source_freshness"] == "UNKNOWN"


def test_output_is_fixed_and_labels_identity_freshness_and_lag_honestly(world, monkeypatch, capsys):
    audit, log = world([eligible()])
    appended = enc(eligible()) + b"\n"
    real = session.publish_status

    def late_writer(*args, **kwargs):
        with open(log, "ab") as stream:  # a live writer appends after the window was read
            stream.write(appended)
        return real(*args, **kwargs)

    monkeypatch.setattr(session, "publish_status", late_writer)
    assert publisher.main([]) == 0
    result, _ = published(capsys)
    assert result == {"schema": "wd.mind-public-publish-result.v1", "outcome": "published",
                      "publication_sha256": result["publication_sha256"], "records": 1,
                      "lag_bytes_observed": len(appended), "coverage": "last_delta_only", "authority": "none",
                      "source_authentication": "UNKNOWN", "source_freshness": "UNKNOWN",
                      "publisher_identity": "caller_declared_not_authenticated",
                      "next_anchor_sha256": hashlib.sha256(next_anchor(audit)).hexdigest()}


# A3 ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pin", PINS)
def test_each_wrong_pin_digest_refuses_without_publication(world, monkeypatch, capsys, pin):
    audit, _ = world([eligible()])
    monkeypatch.setenv("WD_MIND_PUBLIC_" + pin.upper() + "_SHA256", "0" * 64)
    assert publisher.main([]) == 2
    err = refused(capsys)
    assert "0" * 64 not in err and publication(audit) is None


@pytest.mark.parametrize("name,value", [("WD_MIND_PUBLIC_PREVIOUS_SHA256", ""), ("WD_MIND_PUBLIC_PREVIOUS_SHA256", "NONE"),
                                        ("WD_MIND_PUBLIC_PREVIOUS_SHA256", "A" * 64),
                                        ("WD_MIND_PUBLIC_AUDIT_ROOT", "relative/.codex-audit"),
                                        ("WD_MIND_PUBLIC_ANCHOR_PATH", "relative-anchor.json")])
def test_invalid_configuration_refuses_without_publication(world, monkeypatch, capsys, name, value):
    audit, _ = world([eligible()])
    monkeypatch.setenv(name, value)
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) is None


def test_previous_digest_must_be_supplied_explicitly(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    monkeypatch.delenv("WD_MIND_PUBLIC_PREVIOUS_SHA256")
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) is None


# A4 ------------------------------------------------------------------------------------------------------------

def test_previous_digest_is_a_compare_and_swap(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    assert publisher.main([]) == 0
    first, _ = published(capsys)
    before = publication(audit)
    for stale in ("none", "1" * 64):
        monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", stale)
        assert publisher.main([]) == 2
        refused(capsys)
        assert publication(audit) == before
    follow(monkeypatch, audit, first["publication_sha256"])
    assert publisher.main([]) == 0
    second, _ = published(capsys)
    assert second["publication_sha256"] != first["publication_sha256"]
    assert json.loads(publication(audit))["revision"] == 2


# A5 ------------------------------------------------------------------------------------------------------------

def _fault(monkeypatch, audit, when):
    real = os.replace

    def replace(src, dst):
        if when == "after":
            real(src, dst)
        elif when == "other_writer":
            Path(dst).write_bytes(b"{}")
        raise OSError("synthetic ambiguous failure")

    monkeypatch.setattr(os, "replace", replace)


def test_failure_after_the_replace_recovers_as_landed(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    _fault(monkeypatch, audit, "after")
    assert publisher.main([]) == 0
    result, _ = published(capsys)
    assert result["outcome"] == "landed_after_ambiguous_failure"
    assert result["publication_sha256"] == hashlib.sha256(publication(audit)).hexdigest()


def test_failure_before_the_replace_recovers_as_unchanged_and_refuses(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    _fault(monkeypatch, audit, "before")
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) is None


def test_a_third_publication_after_a_failure_is_refused_not_adopted(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    _fault(monkeypatch, audit, "other_writer")
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) == b"{}"  # never repaired, removed or replaced by recovery
    assert pending(audit) is not None


# A6 ------------------------------------------------------------------------------------------------------------

def test_reviewer_capability_rows_are_not_published(world, capsys):
    audit, _ = world([reviewer_row(), eligible()])
    assert publisher.main([]) == 0
    result, _ = published(capsys)
    assert result["records"] == 1
    agents = [r["agent"] for r in json.loads(publication(audit))["body"]["records"]]
    assert agents == ["codex-lead-1"]


# A7 ------------------------------------------------------------------------------------------------------------

def test_a_window_over_the_bounds_refuses_instead_of_truncating(world, monkeypatch, capsys):
    audit, _ = world([eligible(), eligible("fable-5")])
    monkeypatch.setattr(publisher, "MAX_WINDOW_ROWS", 1)
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) is None
    monkeypatch.setattr(publisher, "MAX_WINDOW_ROWS", 2)
    assert publisher.main([]) == 0
    assert published(capsys)[0]["records"] == 2


def test_refusal_never_carries_paths_rows_or_pins(world, monkeypatch, capsys):
    audit, log = world([eligible(), private_row()])
    monkeypatch.setenv("WD_MIND_PUBLIC_POLICY_SHA256", "0" * 64)
    assert publisher.main([]) == 2
    err = refused(capsys)
    for text in (PRIVATE_TEXT, str(log), str(audit), "public-task"):
        assert text not in err


# --- v3: durable recovery artifact and bounded-window cursor continuity ------------------------------------------

def _after_publish_failure(monkeypatch, log, where):
    if where == "stat":
        real = os.stat
        landed = log.parent.parent / ".codex-audit" / session.FILENAME

        def stat(path, *args, **kwargs):
            if Path(path) == Path(log) and os.path.lexists(landed):  # only once the publication exists
                raise OSError("synthetic stat failure")
            return real(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", stat)
    else:
        class Broken:
            def write(self, text):
                if where == "write":
                    raise OSError("synthetic write failure")
                return len(text)

            def flush(self):
                raise OSError("synthetic flush failure")

        monkeypatch.setattr(publisher.sys, "stdout", Broken())


def _recover(monkeypatch, capsys):
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_MODE", "recover")
    code = publisher.main([])
    captured = capsys.readouterr()
    monkeypatch.delenv("WD_MIND_PUBLIC_PUBLISH_MODE")
    return code, captured


@pytest.mark.parametrize("where", ["stat", "write", "flush"])
def test_failure_after_publish_is_recovered_from_the_saved_artifact_after_restart(world, monkeypatch, capsys, where):
    audit, log = world([eligible(), private_row()])
    with monkeypatch.context() as broken:
        _after_publish_failure(broken, log, where)
        assert publisher.main([]) == 2
    capsys.readouterr()
    landed = publication(audit)
    assert landed is not None and pending(audit) is not None  # landed, but the digest only lives in the artifact
    assert PRIVATE_TEXT.encode() not in pending(audit) and b"public-task" not in pending(audit)
    outputs = []
    for _ in range(2):  # crash-equivalent restart, then a repeated recovery
        code, captured = _recover(monkeypatch, capsys)
        assert code == 0 and captured.err == ""
        outputs.append(json.loads(captured.out))
    assert outputs[0] == outputs[1] and set(outputs[0]) == OUTPUT_KEYS
    assert outputs[0]["outcome"] == "landed_recovered" and outputs[0]["records"] == 1
    assert outputs[0]["publication_sha256"] == hashlib.sha256(landed).hexdigest()
    assert publication(audit) == landed and json.loads(landed)["revision"] == 1  # never re-published
    issued = next_anchor(audit)
    assert outputs[0]["next_anchor_sha256"] == hashlib.sha256(issued).hexdigest()
    assert json.loads(issued)["cursor_start"]["offset"] == log.stat().st_size
    assert str(log) not in captured.out and PRIVATE_TEXT not in captured.out


def test_crash_before_publish_recovers_as_unchanged_without_publication(world, monkeypatch, capsys):
    audit, _ = world([eligible()])

    def crash(*args, **kwargs):
        raise KeyboardInterrupt  # the process dies after the artifact, before any publication write

    with monkeypatch.context() as broken:
        broken.setattr(session, "publish_status", crash)
        with pytest.raises(KeyboardInterrupt):
            publisher.main([])
    capsys.readouterr()
    assert pending(audit) is not None and publication(audit) is None
    code, captured = _recover(monkeypatch, capsys)
    result = json.loads(captured.out)
    assert code == 0 and result["outcome"] == "unchanged_recovered"
    assert result["publication_sha256"] is None and result["next_anchor_sha256"] is None
    assert pending(audit) is None and publication(audit) is None and next_anchor(audit) is None
    assert publisher.main([]) == 0  # the same prior can publish again once the attempt is resolved
    assert published(capsys)[0]["outcome"] == "published"


def test_failure_before_the_replace_removes_the_artifact_and_publishes_nothing(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    _fault(monkeypatch, audit, "before")
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) is None and pending(audit) is None and next_anchor(audit) is None


def test_a_third_digest_is_refused_by_recovery_and_the_artifact_is_kept(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    with monkeypatch.context() as broken:
        _fault(broken, audit, "other_writer")
        assert publisher.main([]) == 2
    refused(capsys)
    kept = pending(audit)
    assert kept is not None
    code, captured = _recover(monkeypatch, capsys)
    assert code == 2 and captured.out == ""
    assert json.loads(captured.err) == {"error": "public_publish_refused", "authority": "none"}
    assert pending(audit) == kept and publication(audit) == b"{}" and next_anchor(audit) is None


def test_recovery_is_bound_to_the_pins_and_needs_an_artifact(world, monkeypatch, capsys):
    audit, log = world([eligible()])
    code, captured = _recover(monkeypatch, capsys)  # nothing pending
    assert code == 2 and captured.out == ""
    with monkeypatch.context() as broken:
        _after_publish_failure(broken, log, "stat")
        assert publisher.main([]) == 2
    capsys.readouterr()
    kept, issued = pending(audit), next_anchor(audit)
    other = enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                 "public_types": ["message", "status"]})
    file = audit.parent / "other-public-policy.json"
    file.write_bytes(other)
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLIC_POLICY_PATH", str(file))
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLIC_POLICY_SHA256", hashlib.sha256(other).hexdigest())
    code, captured = _recover(monkeypatch, capsys)
    assert code == 2 and captured.out == ""
    assert kept is not None and pending(audit) == kept and next_anchor(audit) == issued  # nothing rewritten


def test_a_new_publish_needs_the_landed_digest_and_the_issued_continuation(world, monkeypatch, capsys):
    audit, log = world([eligible()])
    assert publisher.main([]) == 0
    first, _ = published(capsys)
    landed = publication(audit)
    # Same prior again, or the landed digest with the OLD anchor (a re-read, not a continuation): refused.
    for previous in ("none", first["publication_sha256"]):
        monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", previous)
        assert publisher.main([]) == 2
        refused(capsys)
        assert publication(audit) == landed
    # A caller-made anchor that starts past the issued cursor would skip rows: refused.
    with open(log, "ab") as stream:
        stream.write(enc(eligible("fable-5")) + b"\n")
    skipped = json.loads(next_anchor(audit))
    skipped["cursor_start"] = dict(skipped["cursor_start"], offset=log.stat().st_size)
    raw = enc(skipped)
    file = audit.parent / "skipping-anchor.json"
    file.write_bytes(raw)
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_PATH", str(file))
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_SHA256", hashlib.sha256(raw).hexdigest())
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) == landed


def test_bounded_windows_advance_without_skipping_and_without_private_rows(world, monkeypatch, capsys):
    audit, log = world([eligible(), private_row()])
    seen = []
    assert publisher.main([]) == 0
    result, _ = published(capsys)
    seen += [(r["agent"], r["task_id"]) for r in json.loads(publication(audit))["body"]["records"]]
    for agent in ("fable-5", "codex-tools-1"):
        with open(log, "ab") as stream:
            stream.write(enc(private_row()) + b"\n" + enc(eligible(agent)) + b"\n")
        follow(monkeypatch, audit, result["publication_sha256"])
        assert publisher.main([]) == 0
        result, line = published(capsys)
        body = json.loads(publication(audit))["body"]
        assert result["records"] == 1 and PRIVATE_TEXT not in line
        seen += [(r["agent"], r["task_id"]) for r in body["records"]]
        assert json.loads(next_anchor(audit))["cursor_start"]["offset"] == log.stat().st_size
    # Every eligible row exactly once, in order; each publication carries only its own window.
    assert seen == [("codex-lead-1", "public-task"), ("fable-5", "public-task"), ("codex-tools-1", "public-task")]
    assert json.loads(publication(audit))["revision"] == 3
    assert PRIVATE_TEXT.encode() not in publication(audit) and PRIVATE_TEXT.encode() not in next_anchor(audit)


def _crash_before_publish(monkeypatch):
    def crash(*args, **kwargs):
        raise KeyboardInterrupt

    with monkeypatch.context() as broken:
        broken.setattr(session, "publish_status", crash)
        with pytest.raises(KeyboardInterrupt):
            publisher.main([])


def test_an_unresolved_attempt_blocks_a_new_publish_and_is_kept(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    _crash_before_publish(monkeypatch)
    capsys.readouterr()
    kept = pending(audit)
    # Even the attempt digest itself as prior does not consume an attempt that never landed.
    for previous in ("none", json.loads(kept)["attempt_sha256"]):
        monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", previous)
        assert publisher.main([]) == 2
        refused(capsys)
        assert pending(audit) == kept and publication(audit) is None and next_anchor(audit) is None
    # ... not even with a caller-made anchor at the saved candidate cursor: the landed check refuses it.
    crafted = json.loads(Path(os.environ["WD_MIND_PUBLIC_ANCHOR_PATH"]).read_bytes())
    crafted["cursor_start"] = json.loads(kept)["candidate_cursor"]
    raw = enc(crafted)
    file = audit.parent / "crafted-anchor.json"
    file.write_bytes(raw)
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_PATH", str(file))
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_SHA256", hashlib.sha256(raw).hexdigest())
    assert publisher.main([]) == 2
    refused(capsys)
    assert pending(audit) == kept and publication(audit) is None and next_anchor(audit) is None


def test_after_an_unchanged_recovery_the_next_window_still_starts_at_the_issued_cursor(world, monkeypatch, capsys):
    audit, log = world([eligible()])
    assert publisher.main([]) == 0
    first, _ = published(capsys)
    landed = publication(audit)
    with open(log, "ab") as stream:
        stream.write(enc(eligible("fable-5")) + b"\n")
    follow(monkeypatch, audit, first["publication_sha256"])
    _crash_before_publish(monkeypatch)  # consumes the landed artifact, saves a new one, dies before publishing
    capsys.readouterr()
    code, captured = _recover(monkeypatch, capsys)
    assert code == 0 and json.loads(captured.out)["outcome"] == "unchanged_recovered" and pending(audit) is None
    issued = next_anchor(audit)
    skipped = json.loads(issued)
    skipped["cursor_start"] = dict(skipped["cursor_start"], offset=log.stat().st_size)
    raw = enc(skipped)
    file = audit.parent / "skipping-anchor.json"
    file.write_bytes(raw)
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_PATH", str(file))
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_SHA256", hashlib.sha256(raw).hexdigest())
    assert publisher.main([]) == 2
    refused(capsys)
    assert publication(audit) == landed and next_anchor(audit) == issued
    follow(monkeypatch, audit, first["publication_sha256"])
    assert publisher.main([]) == 0
    result, _ = published(capsys)
    assert result["records"] == 1 and json.loads(publication(audit))["revision"] == 2


# --- v4: no landed artifact consumed before its issued anchor exists; crash-safe artifact creation ---------------

def _raise(error):
    def fail(*args, **kwargs):
        raise error
    return fail


def _pin_anchor(monkeypatch, audit, value, name):
    raw = enc(value)
    file = audit.parent / name
    file.write_bytes(raw)
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_PATH", str(file))
    monkeypatch.setenv("WD_MIND_PUBLIC_ANCHOR_SHA256", hashlib.sha256(raw).hexdigest())


def test_a_landed_artifact_is_not_consumed_while_its_issued_anchor_is_missing(world, monkeypatch, capsys):
    audit, log = world([eligible()])
    original = {name: os.environ[name] for name in ("WD_MIND_PUBLIC_ANCHOR_PATH", "WD_MIND_PUBLIC_ANCHOR_SHA256")}
    with monkeypatch.context() as broken:
        broken.setattr(publisher, "_write_next_anchor", _raise(OSError("synthetic anchor failure")))
        assert publisher.main([]) == 2
    refused(capsys)
    kept = pending(audit)
    saved = json.loads(kept)
    assert publication(audit) is not None and next_anchor(audit) is None  # landed, anchor never issued
    # Lead reproduction: a caller-made anchor at the saved candidate cursor with the landed digest as prior, then
    # a failure before any publish. The artifact must survive, otherwise nothing can ever recover the anchor.
    crafted = json.loads(Path(original["WD_MIND_PUBLIC_ANCHOR_PATH"]).read_bytes())
    crafted["cursor_start"] = saved["candidate_cursor"]
    _pin_anchor(monkeypatch, audit, crafted, "crafted-anchor.json")
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", saved["attempt_sha256"])
    with open(log, "ab") as stream:
        stream.write(enc(eligible("fable-5")) + b"\n")
    with monkeypatch.context() as broken:
        broken.setattr(session, "prepare_status_attempt", _raise(OSError("synthetic pre-publish failure")))
        assert publisher.main([]) == 2
    refused(capsys)
    assert pending(audit) == kept and next_anchor(audit) is None
    # The crash-equivalent restart under the ORIGINAL pins issues the anchor; then the window continues.
    for name, value in original.items():
        monkeypatch.setenv(name, value)
    code, captured = _recover(monkeypatch, capsys)
    assert code == 0 and json.loads(captured.out)["outcome"] == "landed_recovered"
    follow(monkeypatch, audit, saved["attempt_sha256"])
    assert publisher.main([]) == 0
    result, _ = published(capsys)
    assert result["records"] == 1 and json.loads(publication(audit))["revision"] == 2
    assert [r["agent"] for r in json.loads(publication(audit))["body"]["records"]] == ["fable-5"]


def test_a_stale_issued_anchor_does_not_let_a_landed_artifact_be_consumed(world, monkeypatch, capsys):
    audit, log = world([eligible()])
    assert publisher.main([]) == 0
    first, _ = published(capsys)
    with open(log, "ab") as stream:
        stream.write(enc(eligible("fable-5")) + b"\n")
    follow(monkeypatch, audit, first["publication_sha256"])
    stale = next_anchor(audit)
    with monkeypatch.context() as broken:  # window 2 lands, its anchor is never issued: window 1's stays
        broken.setattr(publisher, "_write_next_anchor", _raise(OSError("synthetic anchor failure")))
        assert publisher.main([]) == 2
    refused(capsys)
    kept = pending(audit)
    saved = json.loads(kept)
    assert next_anchor(audit) == stale and json.loads(stale)["cursor_start"] != saved["candidate_cursor"]
    crafted = json.loads(stale)
    crafted["cursor_start"] = saved["candidate_cursor"]
    _pin_anchor(monkeypatch, audit, crafted, "crafted-anchor.json")
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", saved["attempt_sha256"])
    with monkeypatch.context() as broken:
        broken.setattr(session, "prepare_status_attempt", _raise(OSError("synthetic pre-publish failure")))
        assert publisher.main([]) == 2
    refused(capsys)
    assert pending(audit) == kept and next_anchor(audit) == stale


def _torn_artifact_write(monkeypatch, how):
    """The process dies (or the OS reports a short write) half-way through writing the recovery artifact."""
    real = Path.open

    class Torn:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()
            return False

        def write(self, data):
            self.stream.write(data[:len(data) // 2])
            self.stream.flush()
            if how == "crash":
                raise KeyboardInterrupt
            return len(data) // 2

        def flush(self):
            self.stream.flush()

        def fileno(self):
            return self.stream.fileno()

    def open_(self, mode="r", *args, **kwargs):
        stream = real(self, mode, *args, **kwargs)
        if "x" in mode and self.name.startswith(".public-publish-pending"):
            return Torn(stream)
        return stream

    monkeypatch.setattr(Path, "open", open_)


@pytest.mark.parametrize("how", ["crash", "short"])
def test_a_torn_artifact_write_never_leaves_a_partial_artifact_that_blocks_recovery(world, monkeypatch, capsys, how):
    audit, _ = world([eligible()])
    with monkeypatch.context() as broken:
        _torn_artifact_write(broken, how)
        if how == "crash":
            with pytest.raises(KeyboardInterrupt):
                publisher.main([])
        else:
            assert publisher.main([]) == 2
    capsys.readouterr()
    assert pending(audit) is None and publication(audit) is None  # the final artifact never appears torn
    code, captured = _recover(monkeypatch, capsys)
    assert code == 2 and captured.out == ""  # nothing attempted, nothing to recover
    assert publisher.main([]) == 0  # and nothing blocks the same prior
    assert published(capsys)[0]["outcome"] == "published" and pending(audit) is not None


def test_a_competing_artifact_is_never_overwritten(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    real = Path.open
    foreign = b"foreign-writer-artifact"

    def open_(self, mode="r", *args, **kwargs):
        if "x" in mode and self.name.startswith(".public-publish-pending"):
            target = audit / publisher.PENDING_FILENAME
            if not os.path.lexists(target):
                target.write_bytes(foreign)  # another writer wins the race for the final name
        return real(self, mode, *args, **kwargs)

    with monkeypatch.context() as racing:
        racing.setattr(Path, "open", open_)
        assert publisher.main([]) == 2
    refused(capsys)
    assert pending(audit) == foreign and publication(audit) is None
    code, captured = _recover(monkeypatch, capsys)
    assert code == 2 and pending(audit) == foreign
    assert publisher.main([]) == 2  # fail closed until a human resolves the foreign file; never overwritten
    refused(capsys)
    assert pending(audit) == foreign and publication(audit) is None


# --- v5: the whole transaction is serialized; barrier-controlled interleavings ------------------------------------

def _thread(name):
    outcome = {}

    def body():
        try:
            outcome["code"] = publisher.main([])
        except BaseException as exc:  # recorded, never swallowed silently
            outcome["error"] = exc

    thread = threading.Thread(target=body, name=name, daemon=True)
    thread.start()
    return thread, outcome


def _until(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)


def _in(name):
    return threading.current_thread().name == name


def test_a_concurrent_recover_cannot_drop_the_record_of_a_publication_that_then_lands(world, monkeypatch, capsys):
    """Lead interleaving: A saves its artifact and pauses before publishing; B (recover) sees unchanged and pauses
    before removing the artifact; A publishes and dies before the next anchor; B resumes."""
    audit, _ = world([eligible()])
    monkeypatch.setattr(publisher, "LOCK_TIMEOUT_SECONDS", 0.2, raising=False)
    a_paused, a_go, b_paused, b_go = (threading.Event() for _ in range(4))
    real_publish, real_recover, real_anchor = (session.publish_status, session.recover_status_attempt,
                                               publisher._write_next_anchor)

    def publish(*args, **kwargs):
        if _in("A"):
            a_paused.set()
            assert a_go.wait(10)
        return real_publish(*args, **kwargs)

    def recover(*args, **kwargs):
        state = real_recover(*args, **kwargs)
        if _in("B"):
            b_paused.set()
            assert b_go.wait(10)
        return state

    def anchor(*args, **kwargs):
        if _in("A"):
            raise OSError("synthetic crash before the next anchor")
        return real_anchor(*args, **kwargs)

    monkeypatch.setattr(session, "publish_status", publish)
    monkeypatch.setattr(session, "recover_status_attempt", recover)
    monkeypatch.setattr(publisher, "_write_next_anchor", anchor)
    a, a_out = _thread("A")
    assert a_paused.wait(10) and pending(audit) is not None
    monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_MODE", "recover")  # A read its mode before pausing
    b, b_out = _thread("B")
    _until(lambda: b_paused.is_set() or not b.is_alive())
    a_go.set()
    a.join(10)
    b_go.set()
    b.join(10)
    monkeypatch.delenv("WD_MIND_PUBLIC_PUBLISH_MODE")
    capsys.readouterr()
    assert not a.is_alive() and not b.is_alive() and a_out == {"code": 2} and "error" not in b_out
    landed = publication(audit)
    assert landed is not None  # A's publication landed ...
    kept = pending(audit)  # ... so its recovery record must still exist
    assert kept is not None and json.loads(kept)["attempt_sha256"] == hashlib.sha256(landed).hexdigest()
    monkeypatch.setattr(publisher, "_write_next_anchor", real_anchor)
    code, captured = _recover(monkeypatch, capsys)
    assert code == 0 and json.loads(captured.out)["outcome"] == "landed_recovered"


def test_two_concurrent_continuations_cannot_drop_a_landed_record(world, monkeypatch, capsys):
    """C1 passes the continuation checks and pauses before consuming the artifact; C2 continues, publishes and
    dies before its anchor; C1 resumes."""
    audit, log = world([eligible()])
    monkeypatch.setattr(publisher, "LOCK_TIMEOUT_SECONDS", 0.2, raising=False)
    assert publisher.main([]) == 0
    first, _ = published(capsys)
    with open(log, "ab") as stream:
        stream.write(enc(eligible("fable-5")) + b"\n")
    follow(monkeypatch, audit, first["publication_sha256"])
    c1_paused, c1_go = threading.Event(), threading.Event()
    real_recover, real_anchor = session.recover_status_attempt, publisher._write_next_anchor

    def recover(*args, **kwargs):
        state = real_recover(*args, **kwargs)
        if _in("C1"):
            c1_paused.set()
            assert c1_go.wait(10)
        return state

    def anchor(*args, **kwargs):
        if _in("C2"):
            raise OSError("synthetic crash before the next anchor")
        return real_anchor(*args, **kwargs)

    monkeypatch.setattr(session, "recover_status_attempt", recover)
    monkeypatch.setattr(publisher, "_write_next_anchor", anchor)
    c1, c1_out = _thread("C1")
    assert c1_paused.wait(10)
    c2, c2_out = _thread("C2")
    c2.join(10)
    c1_go.set()
    c1.join(10)
    capsys.readouterr()
    assert not c1.is_alive() and not c2.is_alive() and "error" not in c1_out and "error" not in c2_out
    landed = publication(audit)
    kept = pending(audit)
    assert json.loads(landed)["revision"] == 2
    assert kept is not None and json.loads(kept)["attempt_sha256"] == hashlib.sha256(landed).hexdigest()


def test_the_transaction_lock_is_bounded_and_refuses_when_busy(world, monkeypatch, capsys):
    audit, _ = world([eligible()])
    monkeypatch.setattr(publisher, "LOCK_TIMEOUT_SECONDS", 0.2, raising=False)
    from sandbox.mind.step2.reader_slot import reader_slot

    started = time.monotonic()
    with reader_slot(audit / publisher.LOCK_FILENAME, timeout_seconds=0):
        assert publisher.main([]) == 2
        refused(capsys)
        monkeypatch.setenv("WD_MIND_PUBLIC_PUBLISH_MODE", "recover")
        assert publisher.main([]) == 2
        refused(capsys)
        monkeypatch.delenv("WD_MIND_PUBLIC_PUBLISH_MODE")
    assert time.monotonic() - started < 5
    assert publication(audit) is None and pending(audit) is None
    assert publisher.main([]) == 0 and published(capsys)[0]["outcome"] == "published"
