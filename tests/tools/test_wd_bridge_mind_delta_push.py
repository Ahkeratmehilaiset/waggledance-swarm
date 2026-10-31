"""Bridge mind delta push v0: pushed changes equal the snapshot diff, deterministic, fail-closed, observe-only.

Probe ids: P1, P9, P11, P12 from the RCO2 adversarial probe plan; M9 (pushed deltas equal snapshot diffs).
The fixtures have the shape of a reflected snapshot: the projector core's ``snapshot()`` (``prs`` and ``lanes``
dictionaries, ``reported_ci``, votes, stale votes and veto observations) with the workspace's reflection fields.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import random

import pytest

from tools import wd_bridge_mind_delta_push as push

# Built from parts, as the repo's other guard tests do, so this file never carries the literal tokens.
GUARD_WORDS = ("PRIVATE_" + "MARKER", "_DO_NOT_" + "LEAK")
UNKNOWN_ENTRY = {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}


def ref(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _signal(agent, status, head, number):
    return {"agent": agent, "agent_uuid": "00000000-0000-4000-8000-000000000001", "head": head, "status": status,
            "source_refs": [ref(f"{number}:{agent}:{status}:{head}")], "verification": "declared"}


def _pr(number, head="a" * 40, ci="reported_green", klass="b", votes=(), stale=(), vetoes=()):
    reported = {"status": "UNKNOWN"} if ci == "UNKNOWN" else {"status": ci, "head": head,
                                                               "source_refs": [ref(f"ci:{number}")],
                                                               "verification": "declared"}
    return str(number), {
        "pr": number, "head": head, "base": "UNKNOWN", "reported_ci": reported, "source_refs": [ref(f"pr:{number}")],
        "class": klass, "votes": [_signal(agent, status, head, number) for agent, status in votes],
        "stale_votes": [_signal(agent, status, old, number) for agent, status, old in stale],
        "veto_observations": [{**_signal(agent, "changes_requested", head, number), "activity": "UNKNOWN"}
                              for agent in vetoes],
        "missing": ["gate_eligibility_UNKNOWN"]}


def _snapshot(as_of="2026-01-01T00:30:00Z", coverage="complete", prs=(), tensions=(), attention=(), lanes=None,
              global_question=None, log_identity="log-a", generation="g1", blocked=None):
    return {"schema": "wd.bridge-mind.v1", "authority": "none", "scope": "bounded_projection_not_gate",
            "source": {"log_identity": log_identity, "generation": generation, "cursor_start": 0, "cursor_end": 100,
                       "coverage": coverage, "event_count": 1, "as_of_utc": as_of, "unique_event_count": 1,
                       "duplicate_count": 0, "last_event_ref": ref("source"), "max_event_utc": as_of,
                       "blocked": blocked},
            "prs": dict(prs), "lanes": {}, "facts": [], "tensions": list(tensions), "attention": list(attention),
            "next_question": {"global": global_question or UNKNOWN_ENTRY, "lanes": lanes or {}}}


def _tension(tid, rule="stale_vote", subject="pr:1", actors=("claude-rco-2",)):
    return {"tension_id": tid, "rule": rule, "severity": "medium", "subject": subject, "actors": list(actors),
            "refs": [ref(tid)], "claim": "free text claim", "observation": "free text observation",
            "lifecycle": "new"}


def _item(kind, subject, rank, actors=()):
    return {"kind": kind, "subject": subject, "rank": rank, "actors": list(actors), "refs": [ref(subject)],
            "detail": "free text detail", "suggestion_only": True}


BUILDS = (("codex-lead-1", "build_consensus_pass"), ("codex-tools-1", "build_consensus_pass"))


def test_a_row_leaving_a_bounded_window_is_not_reported_as_removed_from_the_log():
    """A tail window cannot establish that an older PR disappeared from the bridge."""
    identity = "capture-window:windows-v1:12345678:0123456789abcdef"
    previous = _snapshot(log_identity=identity, prs=[_pr(1)])
    current = _snapshot(as_of="2026-01-01T00:31:00Z", log_identity=identity, prs=[_pr(2)])

    changes = push.delta(previous, current)

    assert changes[1:] == [{"schema": "wd.bridge-mind.delta.v0", "type": "rebuild_required",
                            "reason": "bounded_window_changed"}]
    assert all(change.get("type") != "removed" for change in changes)
    with pytest.raises(push.DeltaError, match="rebuild required"):
        push.apply_delta(push.view(previous), changes)


def test_a_nested_observation_change_in_a_bounded_window_requires_a_rebuild():
    identity = "capture-window:windows-v1:12345678:0123456789abcdef"
    previous = _snapshot(log_identity=identity, prs=[_pr(1, votes=BUILDS)])
    current = _snapshot(as_of="2026-01-01T00:31:00Z", log_identity=identity,
                        prs=[_pr(1, votes=BUILDS[:1])])

    changes = push.delta(previous, current)

    assert changes[1]["type"] == "rebuild_required"
    assert changes[1]["reason"] == "bounded_window_changed"


def test_an_unchanged_bounded_view_can_update_its_as_of_time():
    identity = "capture-window:windows-v1:12345678:0123456789abcdef"
    previous = _snapshot(log_identity=identity, prs=[_pr(1)])
    current = _snapshot(as_of="2026-01-01T00:31:00Z", log_identity=identity, prs=[_pr(1)])

    changes = push.delta(previous, current)

    assert push.apply_delta(push.view(previous), changes) == push.view(current)


def test_the_pushed_changes_turn_the_previous_view_into_the_current_view():
    """M9 / P9: applying the delta to the previous view gives exactly the current view."""
    previous = _snapshot(prs=[_pr(1895, ci="UNKNOWN", votes=BUILDS[:1]),
                              _pr(1890, klass="a", votes=BUILDS + (("claude-rco-1", "rco_pass"),))],
                         tensions=[_tension("t-old"), _tension("t-kept", rule="expired_claim")],
                         attention=[_item("signature_only", "pr:1890", 1, ["operator"])],
                         lanes={"claude-rco-1": UNKNOWN_ENTRY})
    current = _snapshot(as_of="2026-01-01T00:31:00Z", coverage="partial",
                        prs=[_pr(1895, votes=BUILDS), _pr(1890, head="c" * 40, klass="a", stale=[
                            ("claude-rco-1", "rco_pass", "a" * 40)])],
                        tensions=[_tension("t-kept", rule="expired_claim"), _tension("t-new", rule="claim_overlap")],
                        attention=[_item("last_missing_vote", "pr:1895", 1, ["claude-rco-1", "claude-rco-2"])],
                        lanes={"claude-rco-1": {"question": "PR1895 is reported CI-green; missing: rco.",
                                                "attention_rank": 1, "refs": [ref("pr:1895")]}},
                        global_question={"question": "PR1895 is reported CI-green; missing: rco.", "attention_rank": 1,
                                         "refs": [ref("pr:1895")]})
    changes = push.delta(previous, current)
    assert changes[0] == {"schema": "wd.bridge-mind.delta.v0", "type": "header",
                          "from_as_of_utc": "2026-01-01T00:30:00Z", "to_as_of_utc": "2026-01-01T00:31:00Z"}
    assert push.apply_delta(push.view(previous), changes) == push.view(current)
    keyed = {(change["section"], change["key"]): change for change in changes[1:]}
    assert keyed[("prs", "1895")] == {
        "schema": "wd.bridge-mind.delta.v0", "type": "changed", "section": "prs", "key": "1895",
        "value": {"head": "a" * 40, "ci": "reported_green", "ci_head": "a" * 40, "class": "b",
                  "votes": ["codex-lead-1:build_consensus_pass", "codex-tools-1:build_consensus_pass"],
                  "stale_votes": [], "vetoes": []}}
    assert keyed[("prs", "1890")]["value"]["stale_votes"] == ["claude-rco-1:rco_pass:aaaaaaaaaaaa"]
    assert keyed[("tensions", "t-old")]["type"] == "removed" and "value" not in keyed[("tensions", "t-old")]
    assert keyed[("tensions", "t-new")]["type"] == "added"
    assert ("tensions", "t-kept") not in keyed
    assert keyed[("attention", "last_missing_vote|pr:1895")]["value"]["rank"] == 1
    assert keyed[("attention", "signature_only|pr:1890")]["type"] == "removed"
    assert keyed[("source", "coverage")]["value"] == "partial"
    assert keyed[("next_question", "lane:claude-rco-1")]["value"]["refs"] == [ref("pr:1895")]
    # Free text is never part of a change: only identifiers, ranks, states and refs.
    assert "free text" not in push.render_lines(changes)


def test_a_block_and_its_release_are_pushed_as_source_changes():
    blocked = {"active": True, "offset": 4096, "reason": "canonical_event_invalid"}
    previous, current = _snapshot(), _snapshot(as_of="2026-01-01T00:31:00Z", blocked=blocked)
    keyed = {(change["section"], change["key"]): change for change in push.delta(previous, current)[1:]}
    assert keyed[("source", "blocked")]["value"] == blocked
    released = {(change["section"], change["key"]): change for change in push.delta(current, _snapshot(
        as_of="2026-01-01T00:32:00Z"))[1:]}
    assert released[("source", "blocked")] == {"schema": "wd.bridge-mind.delta.v0", "type": "changed",
                                                "section": "source", "key": "blocked", "value": None}


def _random_snapshot(rng: random.Random, as_of: str) -> dict:
    prs = [_pr(number, head=rng.choice(["a", "b"]) * 40, ci=rng.choice(["reported_green", "reported_not_green", "UNKNOWN"]),
               klass=rng.choice(["a", "b", "UNKNOWN"]), votes=rng.sample(BUILDS + (("claude-rco-2", "rco_pass"),),
                                                                         rng.randint(0, 3)),
               vetoes=rng.sample(["claude-rco-1"], rng.randint(0, 1)))
           for number in rng.sample(range(1, 9), rng.randint(0, 5))]
    tensions = [_tension(f"t{index}", rule=rng.choice(["stale_vote", "expired_claim"]), actors=rng.sample(
        ["fable-5", "claude-rco-2", "codex-lead-1"], rng.randint(0, 2))) for index in rng.sample(range(6), rng.randint(0, 4))]
    attention = [_item(rng.choice(["last_missing_vote", "tension"]), f"pr:{index}", rank)
                 for rank, index in enumerate(rng.sample(range(6), rng.randint(0, 4)), 1)]
    lanes = {agent: {"question": rng.choice(["UNKNOWN", f"Q {agent}"]), "attention_rank": None, "refs": ["UNKNOWN"]}
             for agent in rng.sample(["fable-5", "claude-rco-1", "codex-tools-1"], rng.randint(0, 3))}
    return _snapshot(as_of=as_of, coverage=rng.choice(["complete", "partial"]), prs=prs, tensions=tensions,
                     attention=attention, lanes=lanes)


def test_push_equals_the_snapshot_diff_on_random_pairs():
    rng = random.Random(1899)
    for _ in range(60):
        previous = _random_snapshot(rng, "2026-01-01T00:30:00Z")
        current = _random_snapshot(rng, "2026-01-01T00:30:00.5Z")
        changes = push.delta(previous, current)
        assert push.apply_delta(push.view(previous), changes) == push.view(current)
        assert push.delta(current, current)[1:] == []


def _shuffled(snapshot, rng):
    out = copy.deepcopy(snapshot)
    keys = list(out["prs"])
    rng.shuffle(keys)
    out["prs"] = {key: out["prs"][key] for key in keys}
    for pr in out["prs"].values():
        for name in ("votes", "stale_votes", "veto_observations"):
            rng.shuffle(pr[name])
    for name in ("tensions", "attention"):
        rng.shuffle(out[name])
    for item in out["tensions"] + out["attention"]:
        item["actors"].reverse()
        item["refs"].reverse()
    return out


def test_the_same_two_snapshots_give_the_same_sorted_bytes_whatever_their_order():
    """P1: lines are sorted by section and key; list, dictionary and actor order in the snapshots do not matter."""
    previous = _random_snapshot(random.Random(1), "2026-01-01T00:30:00Z")
    current = _random_snapshot(random.Random(2), "2026-01-01T00:40:00Z")
    # Always at least one two-actor tension, two-ref attention item and multi-vote PR, so order would show.
    current["tensions"].append(_tension("t-two", rule="claim_overlap", actors=("fable-5", "claude-rco-2")))
    current["attention"].append({**_item("tension_high", "tension:t-two", 9, ["fable-5", "claude-rco-2"]),
                                 "refs": [ref("b"), ref("a")]})
    current["prs"].update([_pr(99, votes=BUILDS + (("claude-rco-2", "rco_pass"),), vetoes=["claude-rco-1"])])
    first = push.render_lines(push.delta(previous, current))
    for seed in range(3, 9):
        assert push.render_lines(push.delta(previous, _shuffled(current, random.Random(seed)))) == first
    keys = [(line["section"], line["key"]) for line in map(json.loads, first.splitlines()[1:])]
    order = {section: index for index, section in enumerate(push.SECTIONS)}
    assert keys == sorted(keys, key=lambda pair: (order[pair[0]], pair[1]))


@pytest.mark.parametrize("change, reason", [({"log_identity": "log-b"}, "log_identity_changed"),
                                            ({"generation": "g2"}, "generation_changed")])
def test_a_replaced_log_gives_only_rebuild_required(change, reason):
    previous = _snapshot(prs=[_pr(1)])
    current = _snapshot(as_of="2026-01-01T00:31:00Z", prs=[_pr(2)], **change)
    changes = push.delta(previous, current)
    assert changes[1:] == [{"schema": "wd.bridge-mind.delta.v0", "type": "rebuild_required", "reason": reason}]
    with pytest.raises(push.DeltaError):
        push.apply_delta(push.view(previous), changes)


MISSING = object()


def _with_source(snapshot, **fields):
    out = copy.deepcopy(snapshot)
    for name, value in fields.items():
        if value is MISSING:
            out["source"].pop(name)
        else:
            out["source"][name] = value
    return out


@pytest.mark.parametrize("before, after, reason", [
    ({"log_identity": MISSING}, {"log_identity": MISSING}, "log_identity_unknown"),
    ({"log_identity": 1}, {"log_identity": 2}, "log_identity_unknown"),
    ({"log_identity": 1}, {"log_identity": 1}, "log_identity_unknown"),
    ({"log_identity": "UNKNOWN"}, {"log_identity": "UNKNOWN"}, "log_identity_unknown"),
    ({"log_identity": ""}, {"log_identity": ""}, "log_identity_unknown"),
    ({}, {"log_identity": {"rejected": "identity value"}}, "log_identity_unknown"),
    ({"log_identity": "rejected identity value"}, {}, "log_identity_unknown"),
    ({"generation": 1}, {"generation": 2}, "generation_unknown"),
    ({"generation": MISSING}, {"generation": MISSING}, "generation_unknown"),
    ({"generation": "UNKNOWN"}, {"generation": "UNKNOWN"}, "generation_unknown"),
    ({}, {"generation": ["g1"]}, "generation_unknown"),
], ids=["both_missing", "ints_differ", "ints_equal", "literal_unknown", "empty", "object", "not_a_token",
        "generation_ints", "generation_missing", "generation_literal_unknown", "generation_list"])
def test_an_unknown_log_identity_or_generation_is_never_continued_and_never_echoed(tmp_path, capsys, before, after,
                                                                                    reason):
    """RCO2 F1 on d621d55c: UNKNOWN == UNKNOWN must not continue a delta across a possibly replaced log."""
    previous = _with_source(_snapshot(prs=[_pr(1)]), **before)
    current = _with_source(_snapshot(as_of="2026-01-01T00:31:00Z", prs=[_pr(2)]), **after)
    changes = push.delta(previous, current)
    assert changes[1:] == [{"schema": "wd.bridge-mind.delta.v0", "type": "rebuild_required", "reason": reason}]
    old, new = tmp_path / "previous.json", tmp_path / "current.json"
    old.write_text(json.dumps(previous), encoding="utf-8")
    new.write_text(json.dumps(current), encoding="utf-8")
    assert push.main(["--previous", str(old), "--current", str(new)]) == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 2 and captured.err == ""
    assert "rejected" not in captured.out and "identity value" not in captured.out


@pytest.mark.parametrize("identity", [{"generation": None}, {"generation": "g1"}, {"log_identity": "log:a/b-1.2"}],
                         ids=["null_generation", "string_generation", "token_identity"])
def test_known_equal_identities_give_a_normal_delta(identity):
    """The positive twin of F1: equal known identities (a null generation is a known one) continue the delta."""
    previous = _with_source(_snapshot(prs=[_pr(1)]), **identity)
    current = _with_source(_snapshot(as_of="2026-01-01T00:31:00Z", prs=[_pr(2)]), **identity)
    changes = push.delta(previous, current)
    assert changes[0]["type"] == "header"
    assert {(change["type"], change["section"], change["key"]) for change in changes[1:]} == {
        ("changed", "source", "as_of_utc"), ("removed", "prs", "1"), ("added", "prs", "2")}
    assert push.apply_delta(push.view(previous), changes) == push.view(current)


@pytest.mark.parametrize("change", [
    {"schema": "wd.bridge-mind.v0"},
    {"authority": "merge"},
    {"scope": "gate"},
    {"source": {"log_identity": "log-a", "coverage": "complete"}},
    {"source": {"log_identity": "log-a", "as_of_utc": "2026-99-99T00:00:00Z"}},
    {"prs": {"7": _pr(8)[1]}},
    {"prs": [_pr(8)[1]]},
    {"prs": None},
    {"tensions": {"t1": _tension("t1")}},
    {"attention": None},
    {"next_question": [UNKNOWN_ENTRY]},
    {"next_question": {"global": UNKNOWN_ENTRY, "lanes": [UNKNOWN_ENTRY]}},
])
def test_a_bad_snapshot_is_refused(change):
    with pytest.raises(push.DeltaError):
        push.delta(_snapshot(), {**_snapshot(as_of="2026-01-01T00:31:00Z"), **change})


def _without(row, name):
    return {key: value for key, value in row.items() if key != name}


@pytest.mark.parametrize("change", [
    {"tensions": [_without(_tension("t1"), "tension_id")]},
    {"tensions": [{**_tension("t1"), "tension_id": 7}]},
    {"tensions": [{**_tension("t1"), "tension_id": ""}]},
    {"tensions": ["t1"]},
    {"tensions": [_tension("t1"), _tension("t1", rule="expired_claim")]},
    {"attention": [_without(_item("last_missing_vote", "pr:1", 1), "subject")]},
    {"attention": [{**_item("last_missing_vote", "pr:1", 1), "kind": None}]},
    {"attention": [_item("last_missing_vote", "pr:1", 1), _item("last_missing_vote", "pr:1", 2)]},
    {"next_question": {"global": None, "lanes": {}}},
    {"next_question": {"lanes": {}}},
    {"next_question": {"global": UNKNOWN_ENTRY, "lanes": {"fable-5": "UNKNOWN"}}},
])
def test_a_row_that_cannot_be_keyed_or_repeats_its_key_is_refused_never_dropped(tmp_path, capsys, change):
    """A dropped or overwritten row would be pushed as removed or changed, which the snapshot never said."""
    previous = _snapshot(tensions=[_tension("t1")], attention=[_item("last_missing_vote", "pr:1", 1)],
                         lanes={"fable-5": UNKNOWN_ENTRY})
    with pytest.raises(push.DeltaError):
        push.delta(previous, {**_snapshot(as_of="2026-01-01T00:31:00Z"), **change})
    old, new = tmp_path / "previous.json", tmp_path / "current.json"
    old.write_text(json.dumps(previous), encoding="utf-8")
    new.write_text(json.dumps({**_snapshot(as_of="2026-01-01T00:31:00Z"), **change}), encoding="utf-8")
    assert push.main(["--previous", str(old), "--current", str(new)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "row has no key or its key is not unique" in captured.err or "entry is not an object" in captured.err


@pytest.mark.parametrize("uuid", ["UNKNOWN", None, "not-a-uuid", 7])
def test_a_vote_without_a_declared_identity_is_marked_and_its_declaration_is_pushed(uuid):
    """The workspace never lets an undeclared vote fill a slot, so the view must not print it like a declared one."""
    undeclared = _pr(5, votes=BUILDS, stale=[("claude-rco-1", "rco_pass", "b" * 40)], vetoes=["claude-rco-2"])
    for name in ("votes", "stale_votes", "veto_observations"):
        undeclared[1][name][0]["agent_uuid"] = uuid
    previous = _snapshot(prs=[undeclared])
    current = _snapshot(as_of="2026-01-01T00:31:00Z", prs=[_pr(5, votes=BUILDS, stale=[
        ("claude-rco-1", "rco_pass", "b" * 40)], vetoes=["claude-rco-2"])])
    seen = push.view(previous)["prs"]["5"]
    assert seen["votes"] == ["codex-lead-1:build_consensus_pass:undeclared", "codex-tools-1:build_consensus_pass"]
    assert seen["stale_votes"] == ["claude-rco-1:rco_pass:bbbbbbbbbbbb:undeclared"]
    assert seen["vetoes"] == ["claude-rco-2:changes_requested:aaaaaaaaaaaa:undeclared"]
    changes = push.delta(previous, current)
    assert [(change["section"], change["key"], change["type"]) for change in changes[1:]] == [
        ("source", "as_of_utc", "changed"), ("prs", "5", "changed")]
    assert push.apply_delta(push.view(previous), changes) == push.view(current)


def _with_signal(number, field, agent, status):
    key, pr = _pr(number)
    row = _signal(agent, status, "a" * 40, number)
    pr[field] = [{**row, "activity": "UNKNOWN"} if field == "veto_observations" else row]
    return key, pr


@pytest.mark.parametrize("field, name", [("votes", "votes"), ("stale_votes", "stale_votes"),
                                         ("veto_observations", "vetoes")])
@pytest.mark.parametrize("verification", ["unverified", None, "missing"])
def test_signal_verification_change_is_visible_with_the_same_valid_uuid(tmp_path, capsys, field, name,
                                                                     verification):
    """Regression twin for the UUID-only declaration mutant (PR1905 discussion 4266509454)."""
    previous = _snapshot(prs=[_with_signal(7, field, "claude-rco-1", "rco_pass")])
    signal = previous["prs"]["7"][field][0]
    if verification == "missing":
        signal.pop("verification")
    else:
        signal["verification"] = verification
    current = copy.deepcopy(previous)
    current["prs"]["7"][field][0]["verification"] = "declared"
    declared = "claude-rco-1:rco_pass" + (":aaaaaaaaaaaa" if field != "votes" else "")
    assert push.view(previous)["prs"]["7"][name] == [declared + ":undeclared"]
    assert push.view(current)["prs"]["7"][name] == [declared]
    changes = push.delta(previous, current)
    assert len(changes) == 2
    assert changes[1]["type"] == "changed" and changes[1]["section"] == "prs" and changes[1]["key"] == "7"
    assert changes[1]["value"][name] == [declared]
    assert push.apply_delta(push.view(previous), changes) == push.view(current)
    old_path, new_path = tmp_path / "previous.json", tmp_path / "current.json"
    old_path.write_text(json.dumps(previous), encoding="utf-8")
    new_path.write_text(json.dumps(current), encoding="utf-8")
    assert push.main(["--previous", str(old_path), "--current", str(new_path)]) == 0
    captured = capsys.readouterr()
    printed = [json.loads(line) for line in captured.out.splitlines()]
    assert captured.err == "" and printed == changes


@pytest.mark.parametrize("field, name", [("votes", "votes"), ("stale_votes", "stale_votes"),
                                         ("veto_observations", "vetoes")])
def test_signal_fields_that_hold_the_separator_never_collide_and_the_replacement_is_pushed(field, name):
    """PR1901 Codex P2 thread PRRT_kwDORYf3RM6rxSIA: ``agent:a`` with ``hold`` and ``agent`` with ``a:hold`` at one
    head are two observations. Each field's ``%`` and ``:`` are percent-encoded, so ``:`` only separates fields and
    replacing one with the other is pushed; plain tokens keep their exact bytes."""
    previous = _snapshot(prs=[_with_signal(7, field, "agent:a", "hold")])
    current = _snapshot(as_of="2026-01-01T00:31:00Z", prs=[_with_signal(7, field, "agent", "a:hold")])
    before, after = push.view(previous)["prs"]["7"][name], push.view(current)["prs"]["7"][name]
    assert before != after
    changes = push.delta(previous, current)
    assert [(change["section"], change["key"], change["type"]) for change in changes[1:]] == [
        ("source", "as_of_utc", "changed"), ("prs", "7", "changed")]
    assert push.apply_delta(push.view(previous), changes) == push.view(current)
    # An encoded-looking token is not a separator either: the percent sign is encoded too.
    posing = push.view(_snapshot(prs=[_with_signal(7, field, "agent%3Aa", "hold")]))["prs"]["7"][name]
    assert posing != before
    plain = push.view(_snapshot(prs=[_with_signal(7, field, "claude-rco-1", "rco_pass")]))["prs"]["7"][name]
    assert plain == ["claude-rco-1:rco_pass" + (":aaaaaaaaaaaa" if name != "votes" else "")]


def test_a_current_snapshot_older_than_the_previous_one_is_refused_with_fraction_aware_times():
    # 00:30:00Z is earlier than 00:30:00.5Z, although "Z" sorts after "." as text.
    with pytest.raises(push.DeltaError):
        push.delta(_snapshot(as_of="2026-01-01T00:30:00.5Z"), _snapshot(as_of="2026-01-01T00:30:00Z"))
    assert push.delta(_snapshot(as_of="2026-01-01T00:30:00Z"), _snapshot(as_of="2026-01-01T00:30:00.5Z"))


def _escaped(word):
    """The word with its first letter as a JSON backslash-u escape, so only a decoded check can see it."""
    return "\\u%04x" % ord(word[0]) + word[1:]


def _deeper(text, times):
    """Each time, the backslash itself is escaped once more, so one more unescape round is needed."""
    for _ in range(times):
        text = text.replace("\\", "\\u005c")
    return text


@pytest.mark.parametrize("form", ["plain", "lowercase", "escaped", "double_escaped", "deep_escaped", "fullwidth",
                                  "json_file_escape"])
@pytest.mark.parametrize("where", ["tension_subject", "lane_question", "vote_agent", "lane_key"])
def test_a_guarded_word_in_the_view_refuses_the_run_and_nothing_is_printed(tmp_path, capsys, form, where):
    """P11: any letter case or compatibility form, before or after each unescape; the word is never echoed."""
    word = GUARD_WORDS[0]
    value = {"plain": word, "lowercase": word.lower(), "escaped": _escaped(word),
             "double_escaped": "\\u005c" + _escaped(word)[1:], "deep_escaped": _deeper(_escaped(word), 5),
             "fullwidth": chr(0xFF00 + ord(word[1]) - 0x20).join([word[0], word[2:]]), "json_file_escape": word}[form]
    current = _snapshot(as_of="2026-01-01T00:31:00Z", prs=[_pr(5, votes=BUILDS)])
    if where == "tension_subject":
        current["tensions"] = [_tension("t1", subject="x" + value)]
    elif where == "lane_question":
        current["next_question"]["lanes"]["fable-5"] = {"question": "q " + value, "attention_rank": None,
                                                        "refs": ["UNKNOWN"]}
    elif where == "vote_agent":
        current["prs"]["5"]["votes"][0]["agent"] = "x" + value
    else:
        current["next_question"]["lanes"]["x" + value] = UNKNOWN_ENTRY
    with pytest.raises(push.DeltaError):
        push.view(current)
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps(_snapshot()), encoding="utf-8")
    raw = json.dumps(current)
    if form == "json_file_escape":
        raw = raw.replace(word, _escaped(word))
        assert word not in raw
    path = tmp_path / "current.json"
    path.write_text(raw, encoding="utf-8")
    code = push.main(["--previous", str(previous), "--current", str(path)])
    captured = capsys.readouterr()
    assert code == 2 and captured.out == ""
    assert not any(guarded in captured.err.upper() for guarded in GUARD_WORDS)
    assert sorted(item.name for item in tmp_path.iterdir()) == ["current.json", "previous.json"]


def test_free_text_outside_the_view_is_never_printed_and_the_printed_lines_are_guarded(monkeypatch, capsys, tmp_path):
    current = _snapshot(as_of="2026-01-01T00:31:00Z", tensions=[{**_tension("t1"), "claim": "x " + GUARD_WORDS[1]}])
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps(_snapshot()), encoding="utf-8")
    path = tmp_path / "current.json"
    path.write_text(json.dumps(current), encoding="utf-8")
    assert push.main(["--previous", str(previous), "--current", str(path)]) == 0
    printed = capsys.readouterr().out
    assert printed and GUARD_WORDS[1] not in printed.upper()
    with pytest.raises(push.DeltaError):
        push.render_lines([{"schema": "wd.bridge-mind.delta.v0", "type": "added", "section": "tensions",
                            "key": "x" + GUARD_WORDS[1].lower(), "value": {}}])


def test_main_prints_only_to_stdout_reads_canonical_json_and_has_no_file_writer(tmp_path, capsys):
    """P12: no file is written; a duplicated key or a NaN is refused at reading."""
    previous = tmp_path / "previous.json"
    current = tmp_path / "current.json"
    previous.write_text(json.dumps(_snapshot(prs=[_pr(1, ci="reported_not_green")])), encoding="utf-8")
    valid = json.dumps(_snapshot(as_of="2026-01-01T00:31:00Z", prs=[_pr(1)]))
    current.write_text(valid, encoding="utf-8")
    assert push.main(["--previous", str(previous), "--current", str(current)]) == 0
    # Header, the as_of change and the PR change.
    assert len(capsys.readouterr().out.splitlines()) == 3
    with pytest.raises(SystemExit):
        push.main(["--previous", str(previous), "--current", str(current), "--audit-out", str(tmp_path / "x.jsonl")])
    for bad in ('{"authority": "merge", ' + valid[1:], valid.replace('"cursor_start": 0', '"cursor_start": NaN')):
        assert bad != valid
        current.write_text(bad, encoding="utf-8")
        assert push.main(["--previous", str(previous), "--current", str(current)]) == 2
        assert "not one canonical JSON object" in capsys.readouterr().err
    assert sorted(path.name for path in tmp_path.iterdir()) == ["current.json", "previous.json"]
    source = Path(push.__file__).read_text(encoding="utf-8")
    assert "write_text" not in source and "open(" not in source and "replace(temp" not in source


def test_no_change_gives_only_the_header():
    snapshot = _random_snapshot(random.Random(9), "2026-01-01T00:30:00Z")
    assert [change["type"] for change in push.delta(snapshot, copy.deepcopy(snapshot))] == ["header"]


# The one dormant consumer allowed to name the delta adapter: the shadow pipeline, run by hand on owned captures. It
# keeps the adapter unwired only while nothing names it in turn, so the check follows that one step as well.
DORMANT_CONSUMER = "wd_bridge_mind_shadow"


def _wiring(root: Path) -> tuple[int, list[str]]:
    """How many files were read, and every tools, bridge bin or waggledance file that reaches the delta adapter:
    one naming the adapter (other than itself and the dormant consumer in tools) or naming that consumer."""
    scanned, found = 0, []
    for word, own in (("wd_bridge_mind_delta_push", ("wd_bridge_mind_delta_push.py", DORMANT_CONSUMER + ".py")),
                      (DORMANT_CONSUMER, (DORMANT_CONSUMER + ".py",))):
        readers = [path for path in (root / "tools").glob("*.py") if path.name not in own]
        readers += list((root / ".agent-bridge" / "bin").glob("*.ps1"))
        readers += list((root / "waggledance").rglob("*.py"))
        scanned = max(scanned, len(readers))
        found += [f"{path.relative_to(root).as_posix()} names {word}" for path in readers
                  if word in path.read_text(encoding="utf-8", errors="replace")]
    return scanned, sorted(found)


def test_the_delta_is_not_wired_into_any_gate_monitor_or_bridge_code():
    """P9: nothing in tools, the bridge bin or waggledance reaches the delta adapter, except the one named dormant
    shadow pipeline in tools, which nothing names in turn."""
    root = Path(push.__file__).resolve().parents[1]
    scanned, found = _wiring(root)
    assert scanned > 100
    assert found == []
    source = Path(push.__file__).read_text(encoding="utf-8")
    assert "Write-AgentEvent" not in source and "subprocess" not in source and ".now(" not in source
    assert "bridge_mind_workspace" not in source


@pytest.mark.parametrize("path, text, flagged", [
    ("tools/merge_helper.py", "from tools import wd_bridge_mind_delta_push", True),  # an unapproved consumer
    (".agent-bridge/bin/Monitor-AgentBridge.ps1", "python tools/wd_bridge_mind_shadow.py", True),  # an activation
    ("waggledance/core/wd_bridge_mind_shadow.py", "import wd_bridge_mind_delta_push", True),  # allowed only in tools
    ("tools/wd_bridge_mind_shadow.py", "from tools import wd_bridge_mind_delta_push", False),  # the dormant consumer
])
def test_the_wiring_check_flags_an_unapproved_consumer_and_an_activation_path(tmp_path, path, text, flagged):
    """The negative controls of P9: the one allowance is the dormant consumer's own file in tools, nothing else."""
    for name in ("tools", ".agent-bridge/bin", "waggledance"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    assert bool(_wiring(tmp_path)[1]) is flagged
