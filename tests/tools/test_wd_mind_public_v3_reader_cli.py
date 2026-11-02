"""Synthetic contract of the opt-in v3 route in the public reader CLI; no live bridge, starter or model.

The v3 publications are written with the existing v3 session API from synthetic files only. The reader runs as
the actual CLI (subprocess for the five-lane series, in-process main() for the refusal matrix). Identity stays
caller-declared; the anchor pin of a v3 reader is the fixed ROOT anchor of the series.
"""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event, read_fixture
from tools import wd_mind_public_session as session
from tools import wd_mind_public_session_cli as reader

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools" / "wd_mind_public_session_cli.py"
PINS = ("anchor", "policy", "roster", "public_policy")
LANES = sorted(ROSTER)
PRIVATE_TEXT = "synthetic-private-text"
REFUSAL = {"error": "public_session_refused", "authority": "none"}
VERSION = "WD_MIND_PUBLIC_READ_VERSION"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def eligible(agent="codex-lead-1", **changes):
    return event(**{"agent": agent, "agent_uuid": ROSTER[agent], "message": "", "paths": [], "write_scope": [],
                    **changes})


class Series:
    """A synthetic source, its fixed root pins as files, and v3 publications under one audit root."""

    def __init__(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        self.first, self.root_pins, self.log = read_fixture(source, [
            eligible(), event(to="fable-5", message=PRIVATE_TEXT, task_id="nonreview-private", payload={}),
            eligible(to="fable-5", message=PRIVATE_TEXT)])
        self.audit = tmp_path / ".codex-audit"
        self.audit.mkdir()
        self.root = self.root_pins["anchor_bytes"]
        self.root_doc = json.loads(self.root)
        self.paths = {}
        for name in PINS:
            path = tmp_path / ("pin-" + name + ".json")
            path.write_bytes(self.root_pins[name + "_bytes"])
            self.paths[name] = path
        self.tmp = tmp_path
        self.state = None

    def _base(self, previous, candidate, pins):
        return dict(enabled=True, as_of=datetime.now(timezone.utc), expected_previous_sha256=previous,
                    root_anchor_bytes=self.root, root_anchor_sha256=self.root_pins["anchor_sha256"],
                    previous_anchor_bytes=self.root, previous_anchor_sha256=self.root_pins["anchor_sha256"],
                    expected_previous_candidate=candidate, **pins)

    def publish_first(self):
        args = self._base(None, self.root_doc["cursor_start"], self.root_pins)
        attempt = session.prepare_status_attempt_v3(self.audit, self.first, **args)
        self.state = session.publish_status_v3(self.audit, self.first, attempt_sha256=attempt, **args)
        return self

    def publish_second(self):
        with self.log.open("ab") as stream:
            stream.write(enc(eligible("fable-5")) + b"\n")
        candidate = asdict(self.first["candidate_cursor"])
        self.anchor2 = enc(dict(self.root_doc, cursor_start=candidate))
        pins2 = dict(self.root_pins, anchor_bytes=self.anchor2, anchor_sha256=sha(self.anchor2))
        second = snapshot.read_anchored_delta(self.log, self.first["candidate_cursor"],
            frozenset({"public-task", "nonreview-private"}), generation_path=self.root_doc["generation_path"],
            max_bytes=65536, max_rows=100, **pins2)
        args = self._base(self.state, candidate, pins2)
        attempt = session.prepare_status_attempt_v3(self.audit, second, **args)
        self.state = session.publish_status_v3(self.audit, second, attempt_sha256=attempt, **args)
        return self

    def publish_v2(self):
        session.publish_status(self.audit, self.first, as_of=datetime.now(timezone.utc), enabled=True,
                               expected_previous_sha256=None, **self.root_pins)
        return self

    def env(self, lane, version="v3", max_age="900"):
        values = {"WD_MIND_PUBLIC_ENABLED": "1", "WD_MIND_PUBLIC_MAX_AGE_SECONDS": max_age,
                  "WD_MIND_PUBLIC_AUDIT_ROOT": str(self.audit), "AGENT_BRIDGE_AGENT": lane,
                  "AGENT_BRIDGE_AGENT_UUID": ROSTER.get(lane, "")}
        for name in PINS:
            values["WD_MIND_PUBLIC_" + name.upper() + "_PATH"] = str(self.paths[name])
            values["WD_MIND_PUBLIC_" + name.upper() + "_SHA256"] = self.root_pins[name + "_sha256"]
        if version is not None:
            values[VERSION] = version
        return values


def run_cli(values):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("WD_MIND", "AGENT_BRIDGE", "PYTEST"))}
    env.update(values, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, str(CLI)], cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=60)
    return result.returncode, result.stdout, result.stderr


def in_process(monkeypatch, capsys, values):
    for key in list(os.environ):
        if key.upper().startswith(("WD_MIND", "AGENT_BRIDGE")):
            monkeypatch.delenv(key)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    code = reader.main([])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ok(result, lane):
    code, out, err = result
    assert (code, err) == (0, ""), (code, err)
    view = json.loads(out)
    assert view["lane"] == lane and view["authority"] == "none"
    assert view["identity_verification"] == "caller_declared_not_authenticated"
    assert view["source_freshness"] == "UNKNOWN" and view["coverage"] == "last_delta_only"
    for text in (PRIVATE_TEXT, "nonreview-private", *ROSTER.values()):
        assert text not in out
    return view


def refused(result):
    code, out, err = result
    assert (code, out) == (2, "") and json.loads(err) == REFUSAL


# 1 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane", LANES)
def test_one_unchanged_v3_configuration_reads_two_consecutive_publications(tmp_path, lane):
    series = Series(tmp_path).publish_first()
    values = series.env(lane)  # fixed once, as at session start
    assert [r["agent"] for r in ok(run_cli(values), lane)["records"]] == ["codex-lead-1"]
    series.publish_second()
    assert json.loads((series.audit / session.V3_FILENAME).read_bytes())["window_anchor_sha256"] != \
        series.root_pins["anchor_sha256"]
    assert [r["agent"] for r in ok(run_cli(values), lane)["records"]] == ["fable-5"]


# 2 --------------------------------------------------------------------------------------------------------------

def test_without_the_opt_in_the_v2_route_is_unchanged(tmp_path, monkeypatch, capsys):
    series = Series(tmp_path).publish_first()
    # Only v3 exists: the default route reads v2 and refuses.
    refused(in_process(monkeypatch, capsys, series.env("fable-5", version=None)))
    series.publish_second().publish_v2()
    v2 = ok(in_process(monkeypatch, capsys, series.env("fable-5", version=None)), "fable-5")
    v3 = ok(in_process(monkeypatch, capsys, series.env("fable-5")), "fable-5")
    assert [r["agent"] for r in v2["records"]] == ["codex-lead-1"]  # the v2 window (first delta)
    assert [r["agent"] for r in v3["records"]] == ["fable-5"]  # the v3 second window


@pytest.mark.parametrize("value", ["", "V3", "3", " v3", "v3 ", "v2", "true", "1", "v3,v2", "v4"])
def test_an_incorrect_opt_in_refuses_without_reading_pins(tmp_path, monkeypatch, capsys, value):
    series = Series(tmp_path).publish_first().publish_v2()
    ok(in_process(monkeypatch, capsys, series.env("codex-lead-1", version=None)), "codex-lead-1")
    monkeypatch.setattr(reader, "_bounded_pin", lambda *a: pytest.fail("an invalid opt-in must not read pins"))
    refused(in_process(monkeypatch, capsys, series.env("codex-lead-1", version=value)))


def test_the_opt_in_never_enables_the_reader_by_itself(tmp_path, monkeypatch, capsys):
    series = Series(tmp_path).publish_first()
    values = series.env("fable-5")
    for enabled in (None, "0", "true", " 1"):
        changed = dict(values)
        if enabled is None:
            del changed["WD_MIND_PUBLIC_ENABLED"]
        else:
            changed["WD_MIND_PUBLIC_ENABLED"] = enabled
        refused(in_process(monkeypatch, capsys, changed))
    ok(in_process(monkeypatch, capsys, values), "fable-5")


# 3 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pin", PINS)
@pytest.mark.parametrize("how", ["missing", "mismatched"])
def test_a_missing_or_mismatched_pin_refuses_the_v3_route(tmp_path, monkeypatch, capsys, pin, how):
    series = Series(tmp_path).publish_first()
    values = series.env("claude-rco-1")
    ok(in_process(monkeypatch, capsys, values), "claude-rco-1")
    key = "WD_MIND_PUBLIC_" + pin.upper() + "_SHA256"
    if how == "missing":
        del values[key]
    else:
        values[key] = sha(b"synthetic-wrong")
    refused(in_process(monkeypatch, capsys, values))


def test_a_window_anchor_is_not_a_v3_reader_pin(tmp_path, monkeypatch, capsys):
    series = Series(tmp_path).publish_first().publish_second()
    path = tmp_path / "pin-anchor-window2.json"
    path.write_bytes(series.anchor2)
    values = series.env("codex-tools-1")
    values.update(WD_MIND_PUBLIC_ANCHOR_PATH=str(path), WD_MIND_PUBLIC_ANCHOR_SHA256=sha(series.anchor2))
    refused(in_process(monkeypatch, capsys, values))
    ok(in_process(monkeypatch, capsys, series.env("codex-tools-1")), "codex-tools-1")


@pytest.mark.parametrize("lane,agent_uuid", [
    ("fable-5", ROSTER["codex-lead-1"]), ("codex-lead-1", ROSTER["fable-5"]),
    ("claude-rco-3", ROSTER["claude-rco-1"]), ("codex-lead-1", "")])
def test_a_forged_or_unknown_identity_refuses_the_v3_route(tmp_path, monkeypatch, capsys, lane, agent_uuid):
    series = Series(tmp_path).publish_first()
    values = dict(series.env("codex-lead-1"), AGENT_BRIDGE_AGENT=lane, AGENT_BRIDGE_AGENT_UUID=agent_uuid)
    refused(in_process(monkeypatch, capsys, values))


# 4 --------------------------------------------------------------------------------------------------------------

def test_a_stale_v3_publication_refuses(tmp_path, monkeypatch, capsys):
    series = Series(tmp_path).publish_first()
    time.sleep(2.2)
    refused(in_process(monkeypatch, capsys, series.env("claude-rco-2", max_age="1")))
    ok(in_process(monkeypatch, capsys, series.env("claude-rco-2")), "claude-rco-2")


def test_private_rows_never_reach_the_v3_publication_or_output(tmp_path, monkeypatch, capsys):
    series = Series(tmp_path).publish_first()
    raw = (series.audit / session.V3_FILENAME).read_bytes()
    assert PRIVATE_TEXT.encode() not in raw and b"nonreview-private" not in raw
    view = ok(in_process(monkeypatch, capsys, series.env("fable-5")), "fable-5")
    assert len(view["records"]) == 1


def test_a_tampered_v3_publication_refuses(tmp_path, monkeypatch, capsys):
    series = Series(tmp_path).publish_first()
    target = series.audit / session.V3_FILENAME
    doc = json.loads(target.read_bytes())
    doc["body"]["records"][0]["agent"] = "fable-5"
    target.write_bytes(enc(doc))
    refused(in_process(monkeypatch, capsys, series.env("fable-5")))
