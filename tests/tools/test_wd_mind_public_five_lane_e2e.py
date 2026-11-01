"""Synthetic five-lane public-status route through the actual entry pieces; never a live run.

Per lane: the actual publisher CLI (subprocess) writes the publication, the actual pure configuration helper
Get-WdMindPublicReadConfig.ps1 (Windows PowerShell) turns a synthetic fleet into the session values, and the
actual reader CLI (subprocess, no arguments) reads with exactly those values plus the declared lane identity.
The complete launchers (start-wd-agent.ps1, start-wd-tools-consumer.ps1) and the bundle Python wrapper are NOT
executed: they need an anchored deployment bundle. Lane identity is caller-declared, never authenticated; the
fleet digest is a synthetic consistency pin, not deployment authorization. No live bridge, model or install.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event
from tools import wd_mind_public_publish_cli as publisher
from tools import wd_mind_public_session as session
from waggledance.core.bridge_log_reader import read_bridge_log

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops" / "windows" / "reboot" / "Get-WdMindPublicReadConfig.ps1"
READER = ROOT / "tools" / "wd_mind_public_session_cli.py"
PUBLISHER = ROOT / "tools" / "wd_mind_public_publish_cli.py"
LEGACY = ROOT / "tools" / "wd_bridge_mind_session.py"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell synthetic helper test")
PINS = ("anchor", "policy", "roster", "public_policy")
LANES = sorted(ROSTER)
PRIVATE_TEXT = "synthetic-private-text"
PRIVATE_CWD = "C:\\synthetic-private-cwd"
REFUSED = {"error": "public_session_refused", "authority": "none"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def eligible(agent="codex-lead-1", **changes):
    return event(**{"agent": agent, "agent_uuid": ROSTER[agent], "message": "", "paths": [], "write_scope": [],
                    **changes})


def rows():
    return [eligible(cwd=PRIVATE_CWD),
            # Non-public task, private body: excluded by policy.
            event(to="fable-5", message=PRIVATE_TEXT, task_id="nonreview-private", payload={}),
            # Public task but addressed with a private body: excluded, never redacted into the output.
            eligible(to="fable-5", message=PRIVATE_TEXT),
            # Public task, non-public type: excluded.
            eligible(type="decision"),
            eligible("fable-5")]


class World:
    """One synthetic bridge source, its pin files and an audit root, all under tmp_path."""

    def __init__(self, tmp_path, items):
        self.tmp = tmp_path
        source = tmp_path / "src"
        source.mkdir()
        self.log, self.generation = source / "events.jsonl", source / "generation.json"
        self.log.write_bytes(b"")
        self.generation.write_bytes(enc({"generation": "synthetic-generation"}))
        start = read_bridge_log(self.log, generation_path=self.generation).candidate_cursor
        tasks = ["nonreview-private", "public-task"]
        self.data = {
            "policy": enc({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": tasks}),
            "roster": enc({"lanes": [{"agent": k, "agent_uuid": v} for k, v in ROSTER.items() if k != "codex-tools-1"],
                           "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]}}),
            "public_policy": enc({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                                  "public_types": ["message"]}),
        }
        self.data["anchor"] = self.anchor_for(asdict(start))
        self.pins = tmp_path / "pins"
        self.pins.mkdir()
        self.paths = {}
        for name in PINS:
            self.write_pin(name, self.data[name])
        self.audit = tmp_path / ".codex-audit"
        self.audit.mkdir()
        self.append(items)

    def anchor_for(self, cursor, **digests):
        values = {name: sha(self.data[name]) for name in ("policy", "roster", "public_policy")}
        values.update(digests)
        return enc({"schema": "wd.q4-source-anchor.v1", "source_path": str(self.log),
                    "generation_path": str(self.generation), "cursor_start": cursor,
                    "policy_sha256": values["policy"], "roster_sha256": values["roster"],
                    "status_mode": "public-v1", "public_policy_sha256": values["public_policy"]})

    def write_pin(self, name, data, label=""):
        path = self.pins / (name + label + ".json")
        path.write_bytes(data)
        self.paths[name + label] = path
        return path

    def append(self, items):
        with self.log.open("ab") as stream:
            stream.write(b"".join(enc(item) + b"\n" for item in items))

    def publication(self):
        target = self.audit / session.FILENAME
        return target.read_bytes() if target.exists() else None

    def fleet(self, *, lanes=LANES, max_age=300, anchor=None, override=None, drop=None, legacy=None):
        config = {"enabled": True, "lanes": list(lanes), "audit_root": str(self.audit), "max_age_seconds": max_age}
        pins = {name: (self.paths[name], sha(self.data[name])) for name in PINS}
        if anchor is not None:
            pins["anchor"] = anchor
        pins.update(override or {})
        for name, (path, digest) in pins.items():
            config[name + "_path"], config[name + "_sha256"] = str(path), digest
        for key in drop or ():
            del config[key]
        fleet = {"mind_public_read": config}
        if legacy is not None:
            fleet["mind_read"] = {"enabled": legacy}
        data = json.dumps(fleet, sort_keys=True).encode("ascii")
        path = self.tmp / ("fleet-" + sha(data)[:12] + ".json")
        path.write_bytes(data)
        return path, sha(data)


def child_env(extra):
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("WD_MIND", "AGENT_BRIDGE", "PYTEST", "WD_D15"))}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    env.update(extra)
    return env


def run(script, extra):
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, env=child_env(extra), capture_output=True,
                            text=True, timeout=60)
    return result.returncode, result.stdout, result.stderr


def publish(world, previous="none", anchor=None):
    extra = {"WD_MIND_PUBLIC_PUBLISH_ENABLED": "1", "WD_MIND_PUBLIC_AUDIT_ROOT": str(world.audit),
             "WD_MIND_PUBLIC_PREVIOUS_SHA256": previous}
    pins = {name: (world.paths[name], sha(world.data[name])) for name in PINS}
    if anchor is not None:
        pins["anchor"] = anchor
    for name, (path, digest) in pins.items():
        extra["WD_MIND_PUBLIC_" + name.upper() + "_PATH"] = str(path)
        extra["WD_MIND_PUBLIC_" + name.upper() + "_SHA256"] = digest
    return run(PUBLISHER, extra)


def published(world, previous="none", anchor=None):
    code, out, err = publish(world, previous, anchor)
    assert (code, err) == (0, ""), (code, err)
    result = json.loads(out)
    assert result["outcome"] == "published"
    assert result["publication_sha256"] == sha(world.publication())
    return result


def helper(fleet, agent):
    path, digest = fleet
    script = ("$ProgressPreference='SilentlyContinue'\n$ErrorActionPreference='Stop'\ntry {\n"
              f". '{HELPER}'\n"
              f"$v=Get-WdMindPublicReadConfig -FleetPath '{path}' -FleetSha256 '{digest}' -Agent '{agent}'\n"
              "[ordered]@{ok=$true;value=$v}|ConvertTo-Json -Depth 8 -Compress\n"
              "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=60)
    assert (result.returncode, result.stderr) == (0, ""), (result.returncode, result.stderr)
    return json.loads(result.stdout.strip())


def session_values(world, lane, **fleet):
    answer = helper(world.fleet(**fleet), lane)
    assert answer["ok"] is True and isinstance(answer["value"], dict), answer
    return answer["value"]


def read(values, lane, agent_uuid=None):
    extra = dict(values, AGENT_BRIDGE_AGENT=lane,
                 AGENT_BRIDGE_AGENT_UUID=ROSTER.get(lane, "") if agent_uuid is None else agent_uuid)
    return run(READER, extra)


def view(values, lane):
    code, out, err = read(values, lane)
    assert (code, err) == (0, ""), (code, err)
    parsed = json.loads(out)
    assert parsed["lane"] == lane and parsed["authority"] == "none"
    assert parsed["identity_verification"] == "caller_declared_not_authenticated"
    assert parsed["source_freshness"] == "UNKNOWN" and parsed["coverage"] == "last_delta_only"
    for text in (PRIVATE_TEXT, "synthetic-private-cwd", "nonreview-private", *ROSTER.values()):
        assert text not in out
    return parsed


def refused(code, out, err):
    assert (code, out) == (2, "") and json.loads(err) == REFUSED


def body_records(world):
    return json.loads(world.publication())["body"]["records"]


# 1 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane", LANES)
def test_each_lane_reads_the_publication_through_the_helper_values_and_reader_cli(tmp_path, lane):
    world = World(tmp_path, rows())
    result = published(world)
    assert result["records"] == 2
    parsed = view(session_values(world, lane), lane)
    assert parsed["records"] == body_records(world)
    assert [item["agent"] for item in parsed["records"]] == ["codex-lead-1", "fable-5"]
    for text in (PRIVATE_TEXT.encode(), b"synthetic-private-cwd", b"nonreview-private"):
        assert text not in world.publication()


# 2 (Lead priority 23:20:55Z) -------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane", LANES)
def test_a_running_reader_configuration_reads_two_consecutive_publications(tmp_path, lane):
    """Observed contract: the second window must use the ISSUED continuation anchor (a new anchor digest), and
    every publication binds the anchor digest it was made with. A session's values are fixed when it starts."""
    world = World(tmp_path, rows())
    first = published(world)
    running = session_values(world, lane)  # computed once, as at session start
    assert view(running, lane)["records"] == body_records(world)

    world.append([eligible("codex-lead-1", status="info")])
    issued_bytes = (world.audit / publisher.NEXT_ANCHOR_FILENAME).read_bytes()
    assert sha(issued_bytes) == first["next_anchor_sha256"]
    # A stable pinned copy: the publisher replaces its issued file after every landed window.
    issued = world.write_pin("anchor", issued_bytes, "-window2")
    # The publisher cannot continue under the original anchor pin...
    refused_publish = publish(world, first["publication_sha256"])
    assert refused_publish[0] == 2 and sha(world.publication()) == first["publication_sha256"]
    # ...only under the issued one.
    second = published(world, first["publication_sha256"], (issued, first["next_anchor_sha256"]))
    assert second["records"] == 1
    assert second["next_anchor_sha256"] != first["next_anchor_sha256"]
    doc = json.loads(world.publication())
    assert doc["anchor_sha256"] == first["next_anchor_sha256"] != running["WD_MIND_PUBLIC_ANCHOR_SHA256"]

    # A NEW session configuration pinned to the issued anchor reads the second window.
    fresh = session_values(world, lane, anchor=(issued, first["next_anchor_sha256"]))
    assert view(fresh, lane)["records"] == body_records(world)

    # The unchanged running configuration: RED until the reader accepts an anchor continuation (or the
    # publication binds a stable base pin). Today it refuses the second publication.
    code, out, err = read(running, lane)
    assert (code, err) == (0, ""), ("running reader refused the second publication", code, err)
    assert json.loads(out)["records"] == body_records(world)


# 3 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("lane,agent_uuid", [
    ("fable-5", ROSTER["codex-lead-1"]),
    ("codex-lead-1", ROSTER["fable-5"]),
    ("claude-rco-1", ROSTER["claude-rco-2"]),
    ("codex-tools-1", "00000000-0000-4000-8000-000000000000"),
    ("claude-rco-3", ROSTER["claude-rco-1"]),
    (" fable-5", ROSTER["fable-5"]),
    ("codex-lead-1", ""),
])
def test_a_forged_or_unknown_lane_identity_is_refused(tmp_path, lane, agent_uuid):
    world = World(tmp_path, rows())
    published(world)
    values = session_values(world, "codex-lead-1")
    refused(*read(values, lane, agent_uuid))
    answer = helper(world.fleet(), lane)
    if lane.strip() in ROSTER and lane == lane.strip():
        assert answer["ok"] is True  # the helper scopes lanes; it never checks identity
    else:
        assert answer == {"ok": False, "reason": "mind_public_config_lanes_invalid"}


def test_a_lane_outside_the_configured_lanes_gets_no_values(tmp_path):
    world = World(tmp_path, rows())
    for lane in LANES:
        others = [item for item in LANES if item != lane]
        assert helper(world.fleet(lanes=others), lane) == {"ok": True, "value": None}


# 4 --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("pin", PINS)
@pytest.mark.parametrize("how", ["missing", "mismatched"])
def test_a_missing_or_mismatched_pin_is_refused_by_helper_and_reader(tmp_path, pin, how):
    world = World(tmp_path, rows())
    published(world)
    if how == "missing":
        answer = helper(world.fleet(drop=[pin + "_sha256"]), "fable-5")
        assert answer == {"ok": False, "reason": "mind_public_config_field_missing"}
    else:
        answer = helper(world.fleet(override={pin: (world.paths[pin], sha(b"synthetic-wrong"))}), "fable-5")
        assert answer == {"ok": False, "reason": "mind_public_config_pinned_file_refused"}
    values = session_values(world, "fable-5")
    key = "WD_MIND_PUBLIC_" + pin.upper() + "_SHA256"
    if how == "missing":
        del values[key]
    else:
        values[key] = sha(b"synthetic-wrong")
    refused(*read(values, "fable-5"))


# 5 --------------------------------------------------------------------------------------------------------------

def test_a_stale_publication_is_refused(tmp_path):
    world = World(tmp_path, rows())
    published(world)
    values = session_values(world, "claude-rco-2", max_age=1)
    time.sleep(2.2)
    refused(*read(values, "claude-rco-2"))
    assert view(session_values(world, "claude-rco-2", max_age=900), "claude-rco-2")["records"] == body_records(world)


@pytest.mark.parametrize("pin", ["policy", "roster", "public_policy"])
def test_rotated_pins_refuse_the_publication_made_under_the_old_pins(tmp_path, pin):
    world = World(tmp_path, rows())
    published(world)
    old = json.loads(world.data[pin])
    if pin == "policy":
        old["approved_tasks"] = ["public-task"]
    elif pin == "roster":
        old["lanes"] = list(reversed(old["lanes"]))
    else:  # still a valid policy (subset of the approved tasks), so only the pin can refuse
        old["public_tasks"] = ["nonreview-private", "public-task"]
    rotated = enc(old)
    assert rotated != world.data[pin]
    rotated_path = world.write_pin(pin, rotated, "-rotated")
    start = json.loads(world.data["anchor"])["cursor_start"]
    anchor = world.anchor_for(start, **{pin: sha(rotated)})
    anchor_path = world.write_pin("anchor", anchor, "-rotated")
    values = session_values(world, "codex-tools-1", anchor=(anchor_path, sha(anchor)),
                            override={pin: (rotated_path, sha(rotated))})
    refused(*read(values, "codex-tools-1"))


@pytest.mark.parametrize("when", ["first", "next"])
def test_a_rotated_or_mismatched_generation_publishes_nothing(tmp_path, when):
    world = World(tmp_path, rows())
    first = None
    if when == "next":
        first = published(world)
    before = world.publication()
    world.generation.write_bytes(enc({"generation": "synthetic-generation-rotated"}))
    world.append([eligible("codex-lead-1", status="info")])
    if first is None:
        code, out, err = publish(world)
    else:
        issued = world.audit / publisher.NEXT_ANCHOR_FILENAME
        code, out, err = publish(world, first["publication_sha256"], (issued, first["next_anchor_sha256"]))
    assert (code, out) == (2, "") and json.loads(err)["error"] == "public_publish_refused"
    assert world.publication() == before


# 6 --------------------------------------------------------------------------------------------------------------

def test_legacy_and_public_pointers_are_incompatible_both_ways(tmp_path):
    world = World(tmp_path, rows())
    published(world)
    fleet = world.tmp / "legacy-fleet.json"
    fleet.write_bytes(world.data["roster"])
    # The legacy route binds to <runtime root>/shared/events.jsonl before it reads the pointer.
    shared = world.tmp / "legacy-root" / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_bytes(world.log.read_bytes())
    (shared / "events.generation.json").write_bytes(world.generation.read_bytes())
    result = subprocess.run(
        [sys.executable, str(LEGACY), "--latest", str(world.audit / session.FILENAME), "--audit-root",
         str(world.audit), "--fleet", str(fleet), "--fleet-sha256", sha(world.data["roster"])],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
        env=child_env({"WD_MIND_READ_ENABLED": "1", "AGENT_BRIDGE_RUNTIME_ROOT": str(shared.parent),
                       "AGENT_BRIDGE_AGENT": "fable-5", "AGENT_BRIDGE_AGENT_UUID": ROSTER["fable-5"]}))
    assert result.returncode == 2 and result.stdout == ""
    assert json.loads(result.stderr)["error"] == "pointer_invalid"

    # A v1-schema document at the public path is refused by the public reader.
    values = session_values(world, "fable-5")
    doc = json.loads(world.publication())
    assert doc["schema"] == "wd.mind-public-status-publication.v2"
    doc["schema"] = "wd.mind-public-status-publication.v1"
    (world.audit / session.FILENAME).write_bytes(enc(doc))
    refused(*read(values, "fable-5"))

    # Both modes enabled at once is a configuration conflict, never a silent pick.
    assert helper(world.fleet(legacy=True), "fable-5") == {"ok": False, "reason": "mind_public_config_mode_conflict"}
