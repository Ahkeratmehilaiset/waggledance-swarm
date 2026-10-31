"""Synthetic tests for the default-off deterministic ``wd.q4-roster-snapshot.v1`` producer.

Every fleet document here is synthetic. No real fleet file, identity, secret, environment or live data is read: the
producer under test receives bytes and an independently supplied digest from the caller, nothing else.
"""
import hashlib
import importlib
import json
import re

import pytest

from sandbox.mind.step2 import shared_projection as sp
from tools import wd_q4_lane_link as link


LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
PROJECTION = ("codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5")
SUPERVISOR = "codex-tools-1"
ROLES = {"codex-lead-1": "lead-impl", "claude-rco-1": "rco-security", "claude-rco-2": "rco-security",
         "fable-5": "fable-producer"}
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Unlisted fleet fields carry synthetic markers; none of them may ever reach the snapshot bytes.
NOISE = {"branch": "synthetic-branch-marker", "worktree": "C:/synthetic/worktree-marker", "prompt": "synthetic-prompt-marker",
         "head": "f" * 40, "model": "synthetic-model-marker", "cli": "synthetic-cli-marker"}


def _module():
    return importlib.import_module("tools.wd_q4_roster_from_fleet")


def _uuid(index):
    return f"{index:08x}-abcd-4ef0-8abc-00000000{index:04x}"  # hex letters so case mutations are observable


def _lane(agent, index, role=True):
    entry = {"agent": agent, "agent_uuid": _uuid(index)}
    entry.update(NOISE)
    if role:
        entry["role"] = ROLES[agent]
    return entry


def _fleet():
    lanes = [_lane(agent, index) for index, agent in enumerate(PROJECTION, 1)]
    supervisor = {"agent": SUPERVISOR, "agent_uuid": _uuid(5), "supervisor_script": "synthetic-supervisor-marker",
                  "snapshot_path": "C:/synthetic/snapshot-marker"}
    return {"schema_version": 2, "fleet_name": "synthetic-fleet-marker", "lanes": lanes, "tools_supervisor": supervisor,
            "q4_read": {"schema": "synthetic-q4-read-marker"}}


def _bytes(document):
    return json.dumps(document).encode("utf-8")


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


_AUTO = object()  # "pin the bytes as supplied"; an explicit None is passed through as an invalid pin


def _build(document_or_bytes, pin=_AUTO, **kwargs):
    raw = _bytes(document_or_bytes) if isinstance(document_or_bytes, dict) else document_or_bytes  # others verbatim
    kwargs.setdefault("enabled", True)
    return _module().build_roster_snapshot(raw, _digest(raw) if pin is _AUTO else pin, **kwargs)


def _refused(reason, document_or_bytes, pin=_AUTO, **kwargs):
    with pytest.raises(_module().RosterProducerRefused) as info:
        _build(document_or_bytes, pin, **kwargs)
    text = str(info.value)
    assert text == reason
    assert "marker" not in text and "abcd-4ef0-8abc" not in text
    return info.value


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "yes", "True"])
def test_default_off_refuses_before_reading_any_byte(enabled):
    raw = _bytes(_fleet())
    kwargs = {} if enabled is None else {"enabled": enabled}
    with pytest.raises(_module().RosterProducerRefused, match=r"\Adisabled\Z"):
        _module().build_roster_snapshot(raw, _digest(raw), **kwargs)
    with pytest.raises(_module().RosterProducerRefused, match=r"\Adisabled\Z"):
        _module().build_roster_snapshot(object(), object(), **kwargs)


def test_deterministic_canonical_bytes_independent_of_fleet_ordering():
    first = _fleet()
    raw_first, sha_first = _build(first)
    assert type(raw_first) is bytes and sha_first == _digest(raw_first)
    assert raw_first.decode("ascii") == json.dumps(json.loads(raw_first), sort_keys=True, separators=(",", ":"))
    assert b"\n" not in raw_first and b" " not in raw_first
    permuted = {key: first[key] for key in reversed(list(first))}
    permuted["lanes"] = [dict(reversed(list(entry.items()))) for entry in reversed(first["lanes"])]
    permuted["tools_supervisor"] = dict(reversed(list(first["tools_supervisor"].items())))
    raw_second, sha_second = _build(permuted)
    assert raw_second == raw_first and sha_second == sha_first
    assert _build(first) == (raw_first, sha_first)
    snapshot = json.loads(raw_first)
    assert [entry["agent"] for entry in snapshot["lanes"]] == sorted(PROJECTION)


def test_snapshot_shape_matches_the_wrapper_and_builder_contracts():
    raw, _ = _build(_fleet())
    snapshot = json.loads(raw)
    assert sorted(snapshot) == ["lanes", "schema", "tools_supervisor"]
    assert snapshot["schema"] == "wd.q4-roster-snapshot.v1"
    assert isinstance(snapshot["lanes"], list) and isinstance(snapshot["tools_supervisor"], dict)
    entries = snapshot["lanes"] + [snapshot["tools_supervisor"]]
    assert len(entries) == 5
    assert {entry["agent"] for entry in entries} == set(LANES)
    assert len({entry["agent_uuid"] for entry in entries}) == 5
    for entry in entries:
        assert sorted(entry) in (["agent", "agent_uuid"], ["agent", "agent_uuid", "role"])
        assert UUID.fullmatch(entry["agent_uuid"]) is not None
    for entry in snapshot["lanes"]:
        assert entry["role"] == ROLES[entry["agent"]]
    assert "role" not in snapshot["tools_supervisor"]
    assert snapshot["tools_supervisor"] == {"agent": SUPERVISOR, "agent_uuid": _uuid(5)}


def test_role_is_carried_only_when_the_fleet_entry_has_a_string_role():
    fleet = _fleet()
    fleet["lanes"][0].pop("role")
    fleet["tools_supervisor"]["role"] = "tools-supervisor"
    snapshot = json.loads(_build(fleet)[0])
    by_agent = {entry["agent"]: entry for entry in snapshot["lanes"]}
    assert "role" not in by_agent["codex-lead-1"]
    assert by_agent["claude-rco-1"]["role"] == "rco-security"
    assert snapshot["tools_supervisor"]["role"] == "tools-supervisor"


def test_round_trips_through_the_lead_roster_reader_and_pins_the_shared_lane_set():
    module = _module()
    raw, sha = _build(_fleet())
    roster = sp.roster_from_fleet(json.loads(raw))
    assert set(roster) == sp.LANES == set(module.LANES) == set(link._LANES) == set(LANES)
    assert roster == {agent: _uuid(index) for index, agent in enumerate(PROJECTION, 1)} | {SUPERVISOR: _uuid(5)}
    assert module.SCHEMA == "wd.q4-roster-snapshot.v1" and module.FLEET_BUDGET == sp.ROSTER_BUDGET
    assert len(raw) <= sp.ROSTER_BUDGET and sp.UUID.pattern == module.UUID.pattern
    builder = pytest.importorskip("sandbox.mind.step2.shared_index_builder")
    snapshot = json.loads(raw)
    assert set(snapshot) == builder.ROSTER_SNAPSHOT_KEYS and snapshot["schema"] == builder.ROSTER_SNAPSHOT_SCHEMA
    assert all(set(entry) <= builder.ROSTER_ENTRY_KEYS for entry in snapshot["lanes"] + [snapshot["tools_supervisor"]])


def test_unlisted_fleet_fields_never_reach_the_snapshot_bytes():
    raw, _ = _build(_fleet())
    text = raw.decode("ascii")
    assert "marker" not in text and "schema_version" not in text and "q4_read" not in text and "f" * 40 not in text
    assert "C:/" not in text and "synthetic" not in text


@pytest.mark.parametrize("value", ["not bytes", bytearray(b"{}"), None, 7, memoryview(b"{}")])
def test_fleet_bytes_must_be_bytes(value):
    _refused("fleet_bytes_invalid", value, "0" * 64)


def test_fleet_over_budget_refuses_even_with_a_matching_pin():
    module = _module()
    fleet = _fleet()
    fleet["padding"] = ""
    fleet["padding"] = "x" * (module.FLEET_BUDGET - len(_bytes(fleet)) + 1)
    raw = _bytes(fleet)
    assert len(raw) == module.FLEET_BUDGET + 1
    _refused("fleet_over_budget", raw)
    fleet["padding"] = fleet["padding"][:-1]
    raw = _bytes(fleet)
    assert len(raw) == module.FLEET_BUDGET
    assert _build(raw)[1] == _build(_fleet())[1]  # the padding is an unlisted field; the snapshot is identical


@pytest.mark.parametrize("pin", ["A" * 64, "0" * 63, "0" * 65, None, b"0" * 64, 0, " " + "0" * 63])
def test_pin_must_be_lowercase_sha256_hex(pin):
    _refused("fleet_pin_invalid", _fleet(), pin)


def test_pin_mismatch_refuses_before_parsing():
    raw = _bytes(_fleet())
    _refused("fleet_pin_mismatch", raw, _digest(raw + b" "))
    _refused("fleet_pin_mismatch", b"not json at all", _digest(raw))  # the pin is checked before the content


def _malformed():
    base = _fleet()

    def mutate(apply):
        fleet = _fleet()
        apply(fleet)
        return _bytes(fleet)

    def set_lane(index, key, value):
        return mutate(lambda fleet: fleet["lanes"][index].__setitem__(key, value))

    def del_lane(index, key):
        return mutate(lambda fleet: fleet["lanes"][index].__delitem__(key))

    cases = {
        "invalid utf-8": b"\xff\xfe" + _bytes(base),
        "invalid json": _bytes(base)[:-1],
        "json array": b"[]",
        "json string": b'"fleet"',
        "empty": b"",
        "lanes dict": mutate(lambda fleet: fleet.__setitem__("lanes", {})),
        "lanes missing": mutate(lambda fleet: fleet.__delitem__("lanes")),
        "supervisor list": mutate(lambda fleet: fleet.__setitem__("tools_supervisor", [fleet["tools_supervisor"]])),
        "supervisor missing": mutate(lambda fleet: fleet.__delitem__("tools_supervisor")),
        "entry string": mutate(lambda fleet: fleet["lanes"].__setitem__(0, "codex-lead-1")),
        "entry list": mutate(lambda fleet: fleet["lanes"].__setitem__(0, [])),
        "agent int": set_lane(0, "agent", 1),
        "agent missing": del_lane(0, "agent"),
        "uuid missing": del_lane(1, "agent_uuid"),
        "uuid int": set_lane(1, "agent_uuid", 1),
        "uuid uppercase": set_lane(1, "agent_uuid", _uuid(2).upper()),
        "uuid short": set_lane(1, "agent_uuid", _uuid(2)[:-1]),
        "uuid trailing newline": set_lane(1, "agent_uuid", _uuid(2) + "\n"),
        "uuid braces": set_lane(1, "agent_uuid", "{" + _uuid(2) + "}"),
        "role int": set_lane(2, "role", 3),
        "role empty": set_lane(2, "role", ""),
        "role space": set_lane(2, "role", "rco security"),
        "role uppercase": set_lane(2, "role", "RCO"),
        "role long": set_lane(2, "role", "r" * 41),
        "role none": set_lane(2, "role", None),
        "supervisor role int": mutate(lambda fleet: fleet["tools_supervisor"].__setitem__("role", 1)),
        "duplicate entry key": b'{"lanes":[{"agent":"codex-lead-1","agent":"codex-lead-1","agent_uuid":"' + _uuid(1).encode()
                               + b'"}],"tools_supervisor":{}}',
        "duplicate top key": b'{"lanes":[],"lanes":[],"tools_supervisor":{}}',
        "deep nesting": b"[" * 200000,
        "nul byte": _bytes(base) + b"\x00",
    }
    return cases


@pytest.mark.parametrize("name", sorted(_malformed()))
def test_malformed_fleet_refuses_with_one_token(name):
    _refused("fleet_malformed", _malformed()[name])


@pytest.mark.parametrize("agent", ["claude-rco-3", "codex-lead-2", "Codex-Lead-1", "codex-lead-1 ", "", "fable5"])
def test_unknown_lane_refuses(agent):
    fleet = _fleet()
    fleet["lanes"].append({"agent": agent, "agent_uuid": _uuid(9)})
    _refused("fleet_unknown_lane", fleet)
    fleet = _fleet()
    fleet["tools_supervisor"]["agent"] = agent
    _refused("fleet_unknown_lane", fleet)


def test_unknown_lane_is_never_silently_dropped_unlike_the_projection_reader():
    fleet = _fleet()
    fleet["lanes"].append({"agent": "claude-rco-3", "agent_uuid": _uuid(9)})
    assert set(sp.roster_from_fleet(fleet)) == sp.LANES  # the reader ignores it; the producer refuses it
    _refused("fleet_unknown_lane", fleet)


def test_duplicate_agent_or_uuid_refuses():
    fleet = _fleet()
    fleet["lanes"].append(dict(fleet["lanes"][0]))
    _refused("fleet_duplicate", fleet)
    fleet = _fleet()
    fleet["lanes"][1]["agent_uuid"] = fleet["lanes"][0]["agent_uuid"]
    _refused("fleet_duplicate", fleet)
    fleet = _fleet()
    fleet["tools_supervisor"]["agent_uuid"] = fleet["lanes"][3]["agent_uuid"]
    _refused("fleet_duplicate", fleet)
    fleet = _fleet()
    fleet["lanes"].append({"agent": SUPERVISOR, "agent_uuid": _uuid(9)})
    _refused("fleet_duplicate", fleet)


def test_missing_or_misplaced_lane_refuses():
    fleet = _fleet()
    fleet["lanes"].pop()
    _refused("fleet_lane_set_mismatch", fleet)
    fleet = _fleet()
    fleet["lanes"] = []
    _refused("fleet_lane_set_mismatch", fleet)
    fleet = _fleet()  # the Tools lane under lanes and fable-5 as the supervisor: five distinct known lanes, wrong slots
    fleet["lanes"][3]["agent"] = SUPERVISOR
    fleet["tools_supervisor"]["agent"] = "fable-5"
    _refused("fleet_lane_set_mismatch", fleet)


def test_refusal_text_never_carries_fleet_content():
    fleet = _fleet()
    fleet["lanes"][0]["agent_uuid"] = "SECRET-marker-" + _uuid(1)
    error = _refused("fleet_malformed", fleet)
    assert "SECRET" not in repr(error) and error.__cause__ is None and error.__context__ is None


def test_module_reads_nothing_but_its_arguments():
    source = open(_module().__file__, encoding="utf-8").read()
    for forbidden in ("os.environ", "sys.argv", "import os", "import sys", "import logging", "print(", "subprocess",
                      "open(", "Path(", "import pathlib", "import random", "import time"):
        assert forbidden not in source, forbidden
    assert "__all__" in source and "build_roster_snapshot" in _module().__all__
