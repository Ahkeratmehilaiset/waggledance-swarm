"""Synthetic contract of the one-shot public-status publisher; no live bridge, starter or scheduler.

Every source, pin and lane here is synthetic. Environment pins, lane identity and the publisher are caller
assertions, never authentication; the published coverage is the last anchored delta only.
"""
import hashlib
import json
import os
from pathlib import Path

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event, read_fixture
from tools import wd_mind_public_publish_cli as publisher
from tools import wd_mind_public_session as session
from tools import wd_mind_public_session_cli as reader

PINS = ("anchor", "policy", "roster", "public_policy")
OUTPUT_KEYS = {"schema", "outcome", "publication_sha256", "records", "lag_bytes_observed", "coverage", "authority",
               "source_authentication", "source_freshness", "publisher_identity"}
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
                      "publisher_identity": "caller_declared_not_authenticated"}


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
    monkeypatch.setenv("WD_MIND_PUBLIC_PREVIOUS_SHA256", first["publication_sha256"])
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
