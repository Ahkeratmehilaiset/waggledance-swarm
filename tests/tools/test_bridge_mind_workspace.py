"""Bridge mind reflective workspace v0 over wd.bridge-mind.v1: derived, referenced, deterministic, observe-only.

Probe ids: P1-P13 from the RCO2 adversarial probe plan, R1-R7 from the RCO1 additions (bridge mind spec v0.2).
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import random

import pytest

from tools import bridge_mind_workspace as mind

# Event time far from the wall clock: a derivation that read the clock would see every lease as ended.
AS_OF = "2026-01-01T00:30:00Z"
HEAD = "a" * 40
OLD = "b" * 40
# Built from parts, as the repo's other guard tests do, so this file never carries the literal tokens.
GUARD_WORDS = ("PRIVATE_" + "MARKER", "_DO_NOT_" + "LEAK")


def ref(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()


def _lane(agent, claims=()):
    return {"agent": agent, "agent_uuid": f"uuid-{agent}", "identity_basis": "declared", "ref": ref(f"lane:{agent}"),
            "claims": [{"task_id": task, "write_scope": list(scope), "lease_expires_utc": expires,
                        "ref": ref(f"claim:{task}")} for task, scope, expires in claims]}


def _pr(number, missing, votes=(), head=HEAD, ci="green", vetoes=()):
    return {"pr": number, "head": head, "ref": ref(f"pr:{number}"), "ci": {"state": ci, "ref": ref(f"ci:{number}")},
            "votes": [{"slot": slot, "agent": agent, "head": vote_head, "ref": ref(f"vote:{number}:{agent}")}
                      for slot, agent, vote_head in votes],
            "vetoes": [{"agent": agent, "ref": ref(f"veto:{number}:{agent}")} for agent in vetoes],
            "missing": [{"element": element, "actors": list(actors), "ref": ref(f"missing:{number}:{element}")}
                        for element, actors in missing]}


def _command(request_id, observed, expected, answered=(), ttl=None):
    fact = {"kind": "command", "ref": ref(f"command:{request_id}"), "observed_utc": observed,
            "value": {"request_id": request_id, "from": "claude-rco-1", "expected_responders": list(expected),
                      "answered_by": list(answered)}}
    if ttl is not None:
        fact["ttl_seconds"] = ttl
    return fact


def _snapshot(coverage="complete", **parts):
    snapshot = {"schema": "wd.bridge-mind.v1", "authority": "none",
                "source": {"log_identity": "fixture", "as_of_utc": AS_OF, "coverage": coverage, "event_count": 3,
                           "last_ref": ref("source")},
                "prs": [], "lanes": [], "facts": [], "tensions": [], "attention": [], "next_question": {}}
    snapshot.update(parts)
    return snapshot


def _rules(fields):
    return {tension["rule"]: tension for tension in fields["tensions"]}


def test_each_tension_rule_is_derived_with_its_refs():
    """P6 stale vote, P7 expired claim is not done, claim overlap, unacknowledged command, R5 blocked row."""
    lanes = [
        _lane("claude-rco-2", [("claude-rco-2/q5", ["tools\\Board.py"], "2026-01-01T01:00:00Z")]),
        _lane("fable-5", [("fable-5/board", ["./tools/board.py", "tests/x.py"], "2026-01-01T01:00:00Z"),
                          ("fable-5/board-tests", ["tests"], "2026-01-01T01:00:00Z"),
                          ("fable-5/old", ["docs/old.md"], AS_OF)]),
    ]
    prs = [_pr(7, [("rco", ["claude-rco-1"])], votes=[("rco", "claude-rco-2", OLD), ("build_lead", "codex-lead-1", HEAD)])]
    facts = [_command("req-old", "2026-01-01T00:15:00Z", ["fable-5", "codex-lead-1"], ["codex-lead-1"])]
    snapshot = _snapshot(lanes=lanes, prs=prs, facts=facts)
    snapshot["source"]["blocked"] = {"reason": "invalid_json", "offset": 4096}
    fields = mind.reflection(snapshot)
    rules = _rules(fields)
    assert set(rules) == {"claim_overlap", "expired_claim", "stale_vote", "unacknowledged_command", "input_blocked"}
    # One agent's own overlapping claims are not a tension; only the two-agent overlap is.
    assert [tension["rule"] for tension in fields["tensions"]].count("claim_overlap") == 1
    overlap = rules["claim_overlap"]
    assert overlap["actors"] == ["claude-rco-2", "fable-5"] and overlap["severity"] == "high"
    assert overlap["refs"] == sorted([ref("claim:claude-rco-2/q5"), ref("claim:fable-5/board")])
    assert rules["expired_claim"]["subject"] == "claim:fable-5/old"
    assert "not completed work" in rules["expired_claim"]["observation"]
    assert rules["stale_vote"]["actors"] == ["claude-rco-2"]
    assert rules["stale_vote"]["refs"] == sorted([ref("vote:7:claude-rco-2"), ref("pr:7")])
    assert rules["unacknowledged_command"]["actors"] == ["fable-5"]
    assert rules["unacknowledged_command"]["refs"] == [ref("command:req-old")]
    assert rules["input_blocked"]["subject"] == "offset:4096" and rules["input_blocked"]["refs"] == [ref("source")]
    assert "invalid_json" in rules["input_blocked"]["observation"]
    assert all(tension["lifecycle"] == "new" and len(tension["tension_id"]) == 16 for tension in fields["tensions"])


def test_lease_ttl_and_grace_boundaries_are_pure_functions_of_as_of():
    """P2: the boundary is decided by as_of alone, on both sides."""
    lanes = [_lane("fable-5", [("fable-5/at", ["a.py"], AS_OF), ("fable-5/after", ["b.py"], "2026-01-01T00:30:00.000001Z")])]
    facts = [
        _command("grace-exact", "2026-01-01T00:15:00Z", ["fable-5"]),
        _command("grace-short", "2026-01-01T00:15:01Z", ["fable-5"]),
        _command("ttl-ended", "2026-01-01T00:00:00Z", ["fable-5"], ttl=1800),
        _command("ttl-live", "2026-01-01T00:00:00Z", ["fable-5"], ttl=1801),
    ]
    tensions = mind.reflection(_snapshot(lanes=lanes, facts=facts))["tensions"]
    subjects = sorted(tension["subject"] for tension in tensions)
    assert subjects == ["claim:fable-5/at", "request:grace-exact", "request:ttl-live"]


def test_attention_puts_the_last_missing_ci_green_vote_first_and_is_only_a_suggestion():
    prs = [
        _pr(30, [("build_lead", ["codex-lead-1"]), ("rco", ["claude-rco-1"])]),
        _pr(20, [("signature", ["operator"])]),
        _pr(40, [("rco", ["claude-rco-2"])], ci="pending"),
        _pr(45, [("rco", ["claude-rco-2"])], vetoes=["claude-rco-1"]),
        _pr(10, [("rco", ["claude-rco-2"])]),
        _pr(50, []),
    ]
    lanes = [_lane("fable-5", [("fable-5/old", ["docs/old.md"], "2026-01-01T00:00:00Z")])]
    fields = mind.reflection(_snapshot(prs=prs, lanes=lanes))
    order = [(item["kind"], item["subject"]) for item in fields["attention"]]
    assert order == [("last_missing_vote", "pr:10"), ("signature_only", "pr:20"), ("votes_missing", "pr:30"),
                     ("tension", "tension:" + fields["tensions"][0]["tension_id"])]
    assert [item["rank"] for item in fields["attention"]] == [1, 2, 3, 4]
    first = fields["attention"][0]
    assert first["actors"] == ["claude-rco-2"]
    assert first["refs"] == sorted([ref("pr:10"), ref("missing:10:rco"), ref("ci:10")])
    # P10: a suggestion never carries clearance.
    assert all(item["suggestion_only"] is True for item in fields["attention"])
    text = mind.canonical_json(fields).lower()
    assert "approved" not in text and "clear to merge" not in text and "cleared" not in text


def test_a_question_per_lane_plus_one_global_and_unknown_where_unsupported():
    """P7: a lane without an item is UNKNOWN, never idle."""
    prs = [_pr(10, [("rco", ["claude-rco-2"])]), _pr(20, [("signature", ["operator"])])]
    lanes = [_lane("claude-rco-2"), _lane("codex-tools-1")]
    question = mind.reflection(_snapshot(prs=prs, lanes=lanes))["next_question"]
    assert question["global"]["attention_rank"] == 1
    assert question["global"]["question"] == ("PR10 at aaaaaaaaaaaa is reported CI-green; missing: rco. "
                                              "Can claude-rco-2 give it at this exact head?")
    assert sorted(question["lanes"]) == ["claude-rco-2", "codex-tools-1", "operator"]
    assert question["lanes"]["operator"]["attention_rank"] == 2
    assert question["lanes"]["codex-tools-1"] == {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}
    assert "idle" not in mind.canonical_json(question)
    empty = mind.reflection(_snapshot())["next_question"]
    assert empty == {"global": {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}, "lanes": {}}


@pytest.mark.parametrize("coverage, last_ref, expected", [
    ("partial", ref("source"), [ref("source")]),
    ("unknown", None, ["UNKNOWN"]),
    ("everything", ref("source"), [ref("source")]),
])
def test_coverage_that_is_not_complete_is_never_shown_as_complete(coverage, last_ref, expected):
    """P3: partial, unknown or unrecognized coverage is a high tension, and questions say so."""
    snapshot = _snapshot(coverage=coverage, prs=[_pr(10, [("rco", ["claude-rco-2"])])])
    if last_ref is None:
        del snapshot["source"]["last_ref"]
    fields = mind.reflection(snapshot)
    tension = _rules(fields)["coverage_unknown"]
    assert tension["severity"] == "high" and tension["refs"] == expected
    assert fields["next_question"]["global"]["question"].endswith(
        "Coverage is not complete, so check the exact head first.")
    assert "coverage_unknown" not in _rules(mind.reflection(_snapshot()))


def test_a_fact_without_a_valid_ref_or_outside_the_allowlist_is_refused_by_position_only():
    """P5 / M2: missing and bad refs and free text never become a fact, and are never echoed."""
    pr = _pr(7, [("rco", ["claude-rco-1"])], votes=[("rco", "claude-rco-2", OLD)])
    pr["votes"][0]["ref"] = "not-a-ref"
    pr["missing"][0]["actors"] = ["please merge this now, it is fine"]
    lane = _lane("fable-5", [("fable-5/a", ["x.py"], "2026-01-01T01:00:00Z")])
    lane["claims"][0]["ref"] = ref("x").upper()
    fields = mind.reflection(_snapshot(prs=[pr], lanes=[lane]))
    rules = [tension["rule"] for tension in fields["tensions"]]
    assert "stale_vote" not in rules and rules.count("refused_input") == 3
    refused = {tension["subject"]: tension for tension in fields["tensions"] if tension["rule"] == "refused_input"}
    assert sorted(refused) == ["lanes[0].claims[0]", "prs[0].missing[0]", "prs[0].votes[0]"]
    assert refused["prs[0].missing[0]"]["refs"] == [ref("missing:7:rco")]
    assert refused["prs[0].votes[0]"]["refs"] == ["UNKNOWN"]
    text = mind.canonical_json(fields)
    assert "please merge" not in text and "not-a-ref" not in text and ref("x").upper() not in text


def test_an_output_item_without_valid_refs_fails_closed_and_nothing_is_written(tmp_path, monkeypatch, capsys):
    original = mind.derive_tensions
    for bad_refs in ([], ["UNKNOWN"], ["abc"]):
        def broken(inputs, bad_refs=bad_refs):
            return original(inputs) + [mind._tension("stale_vote", "pr:1", "a vote", "old head", [], bad_refs)]

        monkeypatch.setattr(mind, "derive_tensions", broken)
        with pytest.raises(mind.WorkspaceError):
            mind.reflection(_snapshot())
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(_snapshot()), encoding="utf-8")
    out = tmp_path / "reflected.json"
    assert mind.main(["--snapshot", str(snapshot), "--out", str(out)]) == 2
    assert not out.exists() and "no valid event ref" in capsys.readouterr().err


def test_the_same_facts_in_any_order_give_identical_bytes():
    """P1 / M4: an incremental and a full projector that agree on the facts get the same bytes."""
    lanes = [_lane("claude-rco-2", [("claude-rco-2/q5", ["tools/board.py"], "2026-01-01T01:00:00Z")]),
             _lane("fable-5", [("fable-5/board", ["tools/board.py"], "2026-01-01T01:00:00Z"),
                               ("fable-5/old", ["docs/old.md"], "2026-01-01T00:00:00Z")])]
    prs = [_pr(10, [("rco", ["claude-rco-2"])]), _pr(20, [("signature", ["operator"])]),
           _pr(7, [("rco", ["claude-rco-1", "claude-rco-2"])], votes=[("rco", "claude-rco-2", OLD)])]
    facts = [_command("r1", "2026-01-01T00:00:00Z", ["fable-5", "codex-tools-1"]),
             _command("r2", "2026-01-01T00:01:00Z", ["claude-rco-2"])]
    snapshot = _snapshot(lanes=lanes, prs=prs, facts=facts)
    first = mind.canonical_json(mind.reflect(snapshot))
    shuffler = random.Random(1813)
    for _ in range(6):
        shuffled = copy.deepcopy(snapshot)
        for key in ("lanes", "prs", "facts"):
            shuffler.shuffle(shuffled[key])
        for lane in shuffled["lanes"]:
            shuffler.shuffle(lane["claims"])
        for pr in shuffled["prs"]:
            shuffler.shuffle(pr["missing"])
            for entry in pr["missing"]:
                shuffler.shuffle(entry["actors"])
        for fact in shuffled["facts"]:
            shuffler.shuffle(fact["value"]["expected_responders"])
        reflected = mind.reflect(shuffled)
        for key in ("lanes", "prs", "facts"):
            reflected[key] = snapshot[key]
        assert mind.canonical_json(reflected) == first


def test_only_event_time_is_used():
    """P2 / M5: no wall clock anywhere in the derivation."""
    lane = _lane("fable-5", [("fable-5/live", ["a.py"], "2026-01-01T00:30:00.2Z")])
    assert "expired_claim" not in _rules(mind.reflection(_snapshot(lanes=[lane])))
    source = Path(mind.__file__).read_text(encoding="utf-8")
    assert ".now(" not in source and "time.time" not in source and "utcnow" not in source
    assert "import time" not in source and "from time" not in source


def test_tension_ids_are_structural_and_lifecycle_follows_the_previous_snapshot():
    lanes = [_lane("fable-5", [("fable-5/old", ["docs/old.md"], "2026-01-01T00:00:00Z")])]
    first = mind.reflect(_snapshot(lanes=lanes))
    expired_id = first["tensions"][0]["tension_id"]
    # New evidence for the same contradiction (another ref) keeps the id.
    moved = copy.deepcopy(lanes)
    moved[0]["claims"][0]["ref"] = ref("claim:fable-5/old:refreshed")
    second = mind.reflect(_snapshot(lanes=moved, coverage="partial"), first)
    lifecycle = {tension["tension_id"]: (tension["rule"], tension["lifecycle"]) for tension in second["tensions"]}
    assert lifecycle[expired_id] == ("expired_claim", "persisting")
    assert sorted(value for value in lifecycle.values()) == [("coverage_unknown", "new"), ("expired_claim", "persisting")]


def _escaped(word):
    """The word with its first letter as a JSON backslash-u escape, so only a decoded check can see it."""
    return "\\u%04x" % ord(word[0]) + word[1:]


def _assert_no_guard_word(text):
    assert not any(word in text for word in GUARD_WORDS)
    if text.lstrip().startswith("{"):
        decoded = json.dumps(json.loads(text), ensure_ascii=False)
        assert not any(word in decoded for word in GUARD_WORDS)


def test_guarded_words_never_reach_the_output_plain_or_escaped(tmp_path, capsys, monkeypatch):
    """P11 / M3: decoded guard on input, and a final output guard that fails closed."""
    lanes = [_lane("fable-5", [("fable-5/x" + GUARD_WORDS[0], ["a.py"], "2026-01-01T00:00:00Z")]),
             # A path may hold a backslash, so a literal backslash-u form passes the allowlist; only the
             # unescaped check refuses it.
             _lane("codex-tools-1", [("codex-tools-1/y", ["docs\\" + _escaped(GUARD_WORDS[1])[1:]],
                                      "2026-01-01T01:00:00Z")])]
    facts = [_command("req-" + _escaped(GUARD_WORDS[1]), "2026-01-01T00:00:00Z", ["fable-5"])]
    prs = [_pr(9, [("rco", ["x" + GUARD_WORDS[1]])])]
    raw = json.dumps(_snapshot(lanes=lanes, facts=facts, prs=prs)).replace(GUARD_WORDS[0], _escaped(GUARD_WORDS[0]))
    assert GUARD_WORDS[0] not in raw
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(raw, encoding="utf-8")
    out = tmp_path / "reflected.json"
    assert mind.main(["--snapshot", str(snapshot), "--out", str(out)]) == 0
    _assert_no_guard_word(capsys.readouterr().out)
    fields = json.loads(out.read_text(encoding="utf-8"))
    reflected_only = {key: fields[key] for key in ("tensions", "attention", "next_question")}
    _assert_no_guard_word(json.dumps(reflected_only))
    refused = sorted(tension["subject"] for tension in fields["tensions"] if tension["rule"] == "refused_input")
    assert refused == ["facts[0]", "lanes[0].claims[0]", "lanes[1].claims[0]", "prs[0].missing[0]"]

    def leaking(inputs):
        return [mind._tension("stale_vote", "pr:1", "vote by x" + GUARD_WORDS[0], "old head", [], [ref("v")])]

    monkeypatch.setattr(mind, "derive_tensions", leaking)
    with pytest.raises(mind.WorkspaceError):
        mind.reflection(_snapshot())


def test_authority_stays_none_and_only_the_reflection_fields_change():
    """P10: no clearance; the workspace never touches source, prs, lanes, facts or authority."""
    snapshot = _snapshot(prs=[_pr(10, [("rco", ["claude-rco-2"])])], lanes=[_lane("claude-rco-2")])
    before = copy.deepcopy(snapshot)
    reflected = mind.reflect(snapshot)
    assert snapshot == before
    assert {key: value for key, value in reflected.items() if key not in ("tensions", "attention", "next_question")} == \
        {key: value for key, value in before.items() if key not in ("tensions", "attention", "next_question")}
    assert reflected["authority"] == "none"
    for bad in ({"authority": "merge"}, {"schema": "wd.bridge-mind.v0"}, {"source": {"coverage": "complete"}}):
        with pytest.raises(mind.WorkspaceError):
            mind.reflection({**snapshot, **bad})


def test_main_writes_only_its_out_file(tmp_path, capsys):
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(_snapshot(prs=[_pr(10, [("rco", ["claude-rco-2"])])])), encoding="utf-8")
    out = tmp_path / "reflected.json"
    assert mind.main(["--snapshot", str(snapshot), "--out", str(out), "--json"]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["reflected.json", "snapshot.json"]
    assert json.loads(capsys.readouterr().out) == json.loads(out.read_text(encoding="utf-8"))
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert mind.main(["--snapshot", str(bad), "--out", str(tmp_path / "never.json")]) == 2
    assert not (tmp_path / "never.json").exists()


@pytest.mark.parametrize("left, right, hit", [
    (["tools\\Board.py"], ["./tools/board.py"], ["tools/board.py"]),
    (["tools"], ["tools/board.py"], ["tools ~ tools/board.py"]),
    (["tools/a"], ["tools/ab.py"], []),
    ([".codex-audit/x/"], [".codex-audit/x"], [".codex-audit/x"]),
])
def test_claim_scopes_overlap_the_way_windows_compares_paths(left, right, hit):
    assert mind.scopes_overlap(left, right) == hit


def test_the_workspace_is_not_wired_into_any_gate_or_bridge_code():
    """M1 / P9: nothing in tools, the bridge bin or waggledance reads the workspace."""
    root = Path(mind.__file__).resolve().parents[1]
    readers = [path for path in (root / "tools").glob("*.py") if path.name != "bridge_mind_workspace.py"]
    readers += list((root / ".agent-bridge" / "bin").glob("*.ps1"))
    readers += list((root / "waggledance").rglob("*.py"))
    assert len(readers) > 100
    assert not [path.name for path in readers
                if "bridge_mind_workspace" in path.read_text(encoding="utf-8", errors="replace")]
