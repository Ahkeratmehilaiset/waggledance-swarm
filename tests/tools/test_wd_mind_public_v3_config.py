"""Synthetic v3 reader selection through the pinned public read config helper; never a live launcher run.

The actual helper Get-WdMindPublicReadConfig.ps1 and the actual export loop text of both launchers run in one
Windows PowerShell process against synthetic fleet and pin files; the actual reader CLI then reads synthetic v3
publications with exactly the exported values. Neither complete launcher is executed (they need an anchored
deployment bundle). Lane identity stays caller-declared; the fleet digest is a synthetic consistency pin.
"""
import base64
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2.test_status_public_filter import ROSTER, enc, event, read_fixture
from tools import wd_mind_public_session as session

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "windows" / "reboot"
HELPER = OPS / "Get-WdMindPublicReadConfig.ps1"
CLI = ROOT / "tools" / "wd_mind_public_session_cli.py"
LAUNCHERS = ("start-wd-agent.ps1", "start-wd-tools-consumer.ps1")
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell synthetic helper test")
PINS = ("anchor", "policy", "roster", "public_policy")
LANES = sorted(ROSTER)
VERSION = "WD_MIND_PUBLIC_READ_VERSION"
PRIVATE_TEXT = "synthetic-private-text"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def eligible(agent="codex-lead-1", **changes):
    return event(**{"agent": agent, "agent_uuid": ROSTER[agent], "message": "", "paths": [], "write_scope": [],
                    **changes})


def export_block(launcher):
    """The launcher's own loop that copies helper output into the process environment, verbatim."""
    text = (OPS / launcher).read_text(encoding="utf-8-sig")
    start = text.index("foreach ($publicMindValue in $publicMindValues.PSObject.Properties) {")
    depth, index = 0, start
    while True:
        char = text[index]
        depth += char == "{"
        depth -= char == "}"
        index += 1
        if char == "}" and depth == 0:
            break
    block = text[start:index]
    assert not re.search(r"(?im)\b(?:Start-Process|Start-Job|Invoke-Expression|Remove-Item)\b", block)
    return block


class World:
    """Synthetic source, root pin files, a v3 series and an optional v2 publication."""

    def __init__(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        self.first, self.pins, self.log = read_fixture(source, [
            eligible(), event(to="fable-5", message=PRIVATE_TEXT, task_id="nonreview-private", payload={})])
        self.audit = tmp_path / ".codex-audit"
        self.audit.mkdir()
        self.root = self.pins["anchor_bytes"]
        self.root_doc = json.loads(self.root)
        self.tmp = tmp_path
        self.paths = {}
        for name in PINS:
            path = tmp_path / ("pin-" + name + ".json")
            path.write_bytes(self.pins[name + "_bytes"])
            self.paths[name] = path
        self.state = None

    def _args(self, previous, candidate, pins):
        return dict(enabled=True, as_of=datetime.now(timezone.utc), expected_previous_sha256=previous,
                    root_anchor_bytes=self.root, root_anchor_sha256=self.pins["anchor_sha256"],
                    previous_anchor_bytes=self.root, previous_anchor_sha256=self.pins["anchor_sha256"],
                    expected_previous_candidate=candidate, **pins)

    def publish_first(self):
        args = self._args(None, self.root_doc["cursor_start"], self.pins)
        attempt = session.prepare_status_attempt_v3(self.audit, self.first, **args)
        self.state = session.publish_status_v3(self.audit, self.first, attempt_sha256=attempt, **args)
        return self

    def publish_second(self):
        with self.log.open("ab") as stream:
            stream.write(enc(eligible("fable-5")) + b"\n")
        candidate = asdict(self.first["candidate_cursor"])
        anchor2 = enc(dict(self.root_doc, cursor_start=candidate))
        pins2 = dict(self.pins, anchor_bytes=anchor2, anchor_sha256=sha(anchor2))
        second = snapshot.read_anchored_delta(self.log, self.first["candidate_cursor"],
            frozenset({"public-task", "nonreview-private"}), generation_path=self.root_doc["generation_path"],
            max_bytes=65536, max_rows=100, **pins2)
        args = self._args(self.state, candidate, pins2)
        attempt = session.prepare_status_attempt_v3(self.audit, second, **args)
        self.state = session.publish_status_v3(self.audit, second, attempt_sha256=attempt, **args)
        return self

    def publish_v2(self):
        session.publish_status(self.audit, self.first, as_of=datetime.now(timezone.utc), enabled=True,
                               expected_previous_sha256=None, **self.pins)
        return self

    def config(self, *, enabled=True, lanes=LANES, **extra):
        value = {"enabled": enabled, "lanes": list(lanes), "audit_root": str(self.audit), "max_age_seconds": 900}
        for name in PINS:
            value[name + "_path"] = str(self.paths[name])
            value[name + "_sha256"] = self.pins[name + "_sha256"]
        value.update(extra)
        return value

    def fleet(self, config, legacy=None):
        fleet = {"mind_public_read": config}
        if legacy is not None:
            fleet["mind_read"] = {"enabled": legacy}
        data = json.dumps(fleet, sort_keys=True).encode("ascii")
        path = self.tmp / ("fleet-" + sha(data)[:12] + ".json")
        path.write_bytes(data)
        return path, sha(data)


def powershell(script):
    script = "$ProgressPreference='SilentlyContinue'\n$ErrorActionPreference='Stop'\n" + script
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=60)
    assert (result.returncode, result.stderr) == (0, ""), (result.returncode, result.stderr)
    return json.loads(result.stdout.strip())


def helper(world, config, agent, *, launcher=None, legacy=None):
    """Helper alone, or helper followed by one launcher's verbatim export loop; returns what was exported."""
    path, digest = world.fleet(config, legacy)
    export = export_block(launcher) + "\n" if launcher else ""
    return powershell(
        "foreach ($n in @([Environment]::GetEnvironmentVariables('Process').Keys)) {\n"
        "  if (([string]$n).StartsWith('WD_MIND_', [StringComparison]::OrdinalIgnoreCase)) {"
        " Remove-Item -LiteralPath \"Env:$n\" } }\n"
        "try {\n"
        f". {quote(HELPER)}\n"
        f"$publicMindValues=Get-WdMindPublicReadConfig -FleetPath {quote(path)} -FleetSha256 {quote(digest)}"
        f" -Agent {quote(agent)}\n"
        "if ($null -ne $publicMindValues) {\n" + export + "}\n"
        "$exported=[ordered]@{}\n"
        "foreach ($n in @([Environment]::GetEnvironmentVariables('Process').Keys | Sort-Object)) {\n"
        "  if (([string]$n).StartsWith('WD_MIND_PUBLIC_', [StringComparison]::Ordinal)) {"
        " $exported[[string]$n]=[Environment]::GetEnvironmentVariable([string]$n,'Process') } }\n"
        "[ordered]@{ok=$true;value=$publicMindValues;exported=$exported}|ConvertTo-Json -Depth 8 -Compress\n"
        "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n")


def read(values, lane):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("WD_MIND", "AGENT_BRIDGE", "PYTEST"))}
    env.update(values, AGENT_BRIDGE_AGENT=lane, AGENT_BRIDGE_AGENT_UUID=ROSTER[lane],
               PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(ROOT))
    result = subprocess.run([sys.executable, str(CLI)], cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=60)
    return result.returncode, result.stdout, result.stderr


def agents(result, lane):
    code, out, err = result
    assert (code, err) == (0, ""), (code, err)
    view = json.loads(out)
    assert view["lane"] == lane and view["identity_verification"] == "caller_declared_not_authenticated"
    assert PRIVATE_TEXT not in out and "nonreview-private" not in out
    return [record["agent"] for record in view["records"]]


# 1 Five-lane config coverage through the actual helper, both launcher export loops and the reader CLI --------

@pytest.mark.parametrize("launcher", LAUNCHERS)
@pytest.mark.parametrize("lane", LANES)
def test_v3_selection_reaches_the_reader_through_each_launcher_export(tmp_path, lane, launcher):
    world = World(tmp_path).publish_first()
    answer = helper(world, world.config(version="v3"), lane, launcher=launcher)
    assert answer["ok"] is True, answer
    exported = answer["exported"]
    assert exported[VERSION] == "v3"
    assert exported["WD_MIND_PUBLIC_ANCHOR_SHA256"] == world.pins["anchor_sha256"]  # the series root
    assert set(exported) == set(answer["value"])
    assert agents(read(exported, lane), lane) == ["codex-lead-1"]
    world.publish_second()  # same exported values, as in a running session
    assert agents(read(exported, lane), lane) == ["fable-5"]


@pytest.mark.parametrize("lane", LANES)
def test_without_a_version_the_export_and_v2_read_are_unchanged(tmp_path, lane):
    world = World(tmp_path).publish_first().publish_v2()
    answer = helper(world, world.config(), lane, launcher="start-wd-agent.ps1")
    assert answer["ok"] is True and VERSION not in answer["exported"] and VERSION not in answer["value"]
    assert set(answer["exported"]) == {"WD_MIND_PUBLIC_ENABLED", "WD_MIND_PUBLIC_AUDIT_ROOT",
                                       "WD_MIND_PUBLIC_MAX_AGE_SECONDS",
                                       *("WD_MIND_PUBLIC_" + n.upper() + "_" + k for n in PINS
                                         for k in ("PATH", "SHA256"))}
    assert agents(read(answer["exported"], lane), lane) == ["codex-lead-1"]


# 2 Refusals and no-read modes --------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["V3", "v2", "", " v3", "v3 ", "3", 3, None, True, ["v3"], {"v": "v3"}, "v4"])
def test_an_invalid_version_refuses_and_exports_nothing(tmp_path, value):
    world = World(tmp_path)
    answer = helper(world, world.config(version=value), "fable-5", launcher="start-wd-tools-consumer.ps1")
    assert answer == {"ok": False, "reason": "mind_public_config_version_invalid"}


def test_a_differently_cased_version_key_is_an_unknown_field(tmp_path):
    world = World(tmp_path)
    config = world.config()
    config["Version"] = "v3"
    assert helper(world, config, "fable-5") == {"ok": False, "reason": "mind_public_config_unknown_field"}


def test_disabled_mode_and_unlisted_lanes_stay_no_read_with_a_version(tmp_path):
    world = World(tmp_path)
    for lane in LANES:
        answer = helper(world, world.config(enabled=False, version="v3"), lane, launcher="start-wd-agent.ps1")
        assert answer["ok"] is True and answer["value"] is None and answer["exported"] == {}
        others = [item for item in LANES if item != lane]
        answer = helper(world, world.config(lanes=others, version="v3"), lane, launcher="start-wd-agent.ps1")
        assert answer["ok"] is True and answer["value"] is None and answer["exported"] == {}


def test_a_version_never_relaxes_the_existing_checks(tmp_path):
    world = World(tmp_path)
    wrong = world.config(version="v3", anchor_sha256=sha(b"synthetic-wrong"))
    assert helper(world, wrong, "fable-5") == {"ok": False, "reason": "mind_public_config_pinned_file_refused"}
    conflict = helper(world, world.config(version="v3"), "fable-5", legacy=True)
    assert conflict == {"ok": False, "reason": "mind_public_config_mode_conflict"}
    assert helper(world, world.config(version="v3"), "claude-rco-3") == {
        "ok": False, "reason": "mind_public_config_lanes_invalid"}


# 3 Static launcher invariants (both export sites) -------------------------------------------------------------

@pytest.mark.parametrize("launcher", LAUNCHERS)
def test_each_launcher_exports_the_whole_pinned_helper_output_after_the_scrub(launcher):
    text = (OPS / launcher).read_text(encoding="utf-8-sig")
    block = export_block(launcher)
    assert "StartsWith('WD_MIND_PUBLIC_', [StringComparison]::Ordinal)" in block
    assert "SetEnvironmentVariable($publicMindValue.Name, [string]$publicMindValue.Value, 'Process')" in block
    assert re.search(r"PSObject\.Properties\['Get-WdMindPublicReadConfig\.ps1'\]", text)
    assert re.search(r"PSObject\.Properties\['tools-bootstrap/tools/wd_mind_public_session_cli\.py'\]", text)
    assert VERSION not in text  # only the pinned helper decides the version
    scrub = text.index("StartsWith('WD_MIND_', [StringComparison]::OrdinalIgnoreCase)")
    assert scrub < text.index(block)
