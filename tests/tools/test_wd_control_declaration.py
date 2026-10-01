"""Hostile twins for tools/wd_control_declaration.py (ported from the fable-5 1ef44 model twins, plus the
quiet-key token, 512-character and row-cap twins). Pure: in-memory rows only."""
from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import wd_control_declaration as module  # noqa: E402
from tools import wd_routing_cancellations as sa  # noqa: E402

classify_all = module.classify_declarations

LEAD = {"agent": "codex-lead-1", "agent_uuid": "u-lead", "session_id": "s-1", "run_id": "r-1"}
D_OLD, D_NEW = "a" * 64, "b" * 64


def old(**over):
    r = {**LEAD, "type": "wake_request", "status": "assigned", "task_id": "t/1", "request_id": "old",
         "request_digest": D_OLD, "message": "do the work", "payload": {"task_revision": "v1"}}
    r.update(over)
    return r


def decl(state="none", target=None, **over):
    r = {**LEAD, "type": "wake_request", "status": "assigned", "task_id": "t/1", "request_id": "new",
         "request_digest": D_NEW, "message": "StandingOneShot HOLD retained; do not stop",
         "payload": {"task_revision": "v1", "control": {"schema": "wd.task-control-declaration.v1", "task_id": "t/1",
                                                         "request_id": "new", "state": state, "target": target}}}
    r.update(over)
    return r


TGT = {"request_id": "old", "request_digest": D_OLD}


def last(rows, identity=LEAD):
    return classify_all(rows, identity)[-1]


# ---- positive twins (must hold in both models) ----
def test_none_with_hold_boilerplate_is_none():
    assert last([old(), decl()]) == ("none", None)


@pytest.mark.parametrize("state", ["cancel", "hold", "resume"])
def test_valid_targeted_state(state):
    assert last([old(), decl(state, dict(TGT))]) == (state, "old")


def test_no_declaration_is_v1():
    r = decl(); r["payload"] = {"task_revision": "v1"}
    assert last([old(), r]) == ("v1", None)


def test_outputs_never_contain_a_clear():
    kinds = {k for k, _ in classify_all([old(), decl("resume", dict(TGT)), decl()], LEAD)}
    assert kinds <= {"v1", "unknown", "none", "cancel", "hold", "resume"}


# ---- hooks: subclasses must never be consulted ----
class LyingStr(str):
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = str.__hash__


class LyingDict(dict):
    def get(self, key, default=None):
        return "codex-lead-1" if key == "agent" else dict.get(self, key, default)


class AlwaysEqual:
    def __eq__(self, other):
        return True

    def __ne__(self, other):
        return False

    __hash__ = object.__hash__


def test_GAP_str_subclass_schema_is_unknown():
    r = decl(); r["payload"]["control"]["schema"] = LyingStr("forged")
    assert last([old(), r]) == ("unknown", None)


def test_GAP_str_subclass_agent_is_unknown():
    r = decl(); r["agent"] = LyingStr("fable-5")
    assert last([old(), r]) == ("unknown", None)


def test_GAP_dict_subclass_row_is_unknown():
    r = LyingDict(decl(agent="fable-5"))
    assert last([old(), r]) == ("unknown", None)


def test_GAP_dict_subclass_control_is_unknown():
    r = decl(); r["payload"] = LyingDict(r["payload"])
    assert last([old(), r]) == ("unknown", None)


def test_GAP_str_subclass_control_keys_are_unknown():
    r = decl(); c = r["payload"]["control"]; r["payload"]["control"] = {LyingStr(k): v for k, v in c.items()}
    assert last([old(), r]) == ("unknown", None)


# ---- loose identity input ----
@pytest.mark.parametrize("bad", ["hook_value", "extra_key", "missing_key", "int_value", "other_agent", "not_dict"])
def test_GAP_identity_input_must_be_closed_exact(bad):
    ident = dict(LEAD)
    if bad == "hook_value":
        ident["agent_uuid"] = AlwaysEqual()
    elif bad == "extra_key":
        ident["nonce"] = "x"
    elif bad == "missing_key":
        ident.pop("run_id")
    elif bad == "int_value":
        ident["session_id"] = 1
    elif bad == "other_agent":
        ident = {**LEAD, "agent": "fable-5"}
    else:
        ident = list(LEAD.items())
    r = decl(agent_uuid="forged-uuid") if bad == "hook_value" else decl()
    assert last([old(), r], ident) == ("unknown", None)


# ---- cycles and depth ----
def test_GAP_cycle_in_payload_is_unknown_without_recursion_error():
    r = decl(); loop = {}; loop["self"] = loop; r["payload"]["extra"] = loop
    assert last([old(), r]) == ("unknown", None)


def test_GAP_depth_over_cap_is_unknown():
    r = decl(); deep = "x"
    for _ in range(40):
        deep = {"n": deep}
    r["payload"]["extra"] = deep
    assert last([old(), r]) == ("unknown", None)


def test_GAP_nan_in_payload_is_unknown():
    r = decl(); r["payload"]["score"] = float("nan")
    assert last([old(), r]) == ("unknown", None)


# ---- typed controls besides the declaration (none must not hide them) ----
TYPED = {
    "control_type": lambda r: r.update(type="decision", status="assigned"),
    "status_abort": lambda r: r.update(status="abort_requested"),
    "status_cancelled": lambda r: r.update(status="cancelled"),
    "payload_directive": lambda r: r["payload"].update(directive="hold"),
    "payload_hold_key": lambda r: r["payload"].update(hold=True),
    "nested_control_state": lambda r: r["payload"].update(result={"control": {"state": "pause"}}),
    "envelope_control_key": lambda r: r.update(control={"state": "none"}),
    "legacy_fact_at_payload_root": lambda r: r.update(status="cancelled") or r["payload"].update(
        schema="wd.request-cancellation.v1", cancelled_request_id="old", cancelled_request_digest=D_OLD,
        scope="whole_request"),
    "stop_in_directive_list": lambda r: r["payload"].update(action=["go", "stop"]),
    "not_a_request_pair": lambda r: r.update(type="message", status="answered"),
}


@pytest.mark.parametrize("name", sorted(TYPED))
def test_GAP_typed_control_besides_declaration_is_unknown(name):
    r = decl(); TYPED[name](r)
    assert last([old(), r]) == ("unknown", None)


@pytest.mark.parametrize("name", sorted(TYPED))
def test_typed_control_with_cancel_declaration_is_unknown(name):
    r = decl("cancel", dict(TGT)); TYPED[name](r)
    assert last([old(), r]) == ("unknown", None)


# ---- replay and target ambiguity by verified position ----
def test_GAP_later_duplicate_request_id_makes_declaration_unknown():
    assert classify_all([old(), decl(), decl()], LEAD)[1] == ("unknown", None)


def test_earlier_duplicate_request_id_is_unknown():
    assert last([old(), decl(), decl()]) == ("unknown", None)


def test_target_after_declaration_is_unknown():
    assert classify_all([decl("cancel", dict(TGT)), old()], LEAD)[0] == ("unknown", None)


def test_GAP_target_duplicated_later_is_unknown():
    assert classify_all([old(), decl("cancel", dict(TGT)), old()], LEAD)[1] == ("unknown", None)


def test_GAP_target_with_foreign_uuid_is_unknown():
    assert last([old(agent_uuid="other"), decl("cancel", dict(TGT))]) == ("unknown", None)


def test_GAP_target_not_a_request_is_unknown():
    assert last([old(type="message", status="answered"), decl("cancel", dict(TGT))]) == ("unknown", None)


def test_target_other_task_is_unknown():
    assert last([old(task_id="t/2"), decl("cancel", dict(TGT))]) == ("unknown", None)


def test_target_label_with_space_is_unknown():
    assert last([old(agent="codex-lead-1 "), decl("cancel", dict(TGT))]) == ("unknown", None)


def test_target_stored_digest_mismatch_is_unknown():
    assert last([old(request_digest="c" * 64), decl("cancel", dict(TGT))]) == ("unknown", None)


def test_GAP_target_id_must_be_valid_id_charset():
    r = decl("cancel", {"request_id": "old id!", "request_digest": D_OLD})
    assert last([old(request_id="old id!"), r]) == ("unknown", None)


# ---- malformed declaration (kept from 9756) ----
MAL = {
    "extra_nonce": lambda r: r["payload"]["control"].update(nonce="x"),
    "self_digest": lambda r: r["payload"]["control"].update(request_digest=D_NEW),
    "state_bool": lambda r: r["payload"]["control"].update(state=True),
    "state_list": lambda r: r["payload"]["control"].update(state=["none"]),
    "wrong_schema": lambda r: r["payload"]["control"].update(schema="wd.task-control-declaration.v2"),
    "task_mismatch": lambda r: r["payload"]["control"].update(task_id="t/2"),
    "replayed_declaration_other_row_id": lambda r: r.update(request_id="other"),
    "no_envelope_digest": lambda r: r.pop("request_digest"),
    "upper_envelope_digest": lambda r: r.update(request_digest=D_NEW.upper()),
    "foreign_responder": lambda r: r.update(agent="fable-5"),
    "foreign_session": lambda r: r.update(session_id="s-2"),
    "control_not_object": lambda r: r["payload"].update(control="none"),
    "none_with_target": lambda r: r["payload"]["control"].update(target=dict(TGT)),
}


@pytest.mark.parametrize("name", sorted(MAL))
def test_malformed_declaration_is_unknown(name):
    r = decl(); MAL[name](r)
    assert last([old(), r]) == ("unknown", None)


def test_rows_are_not_mutated():
    rows = [old(), decl("cancel", dict(TGT))]
    before = copy.deepcopy(rows)
    classify_all(rows, LEAD)
    assert rows == before


# ---- quiet keys exempt only token-shaped values, exactly as S-A _signals (Lead 22:01:31Z gap) ----
QUIET_HOSTILE = {
    "ids_with_space": {"ids": "stop here"},
    "request_id_513_chars": {"request_id": "stop" + "a" * 509},
    "time_field_with_space": {"deadline_utc": "stop now"},
    "agent_with_space": {"agent": "halt please"},
    "id_list_with_space": {"trace_ids": ["ok", "abort this"]},
    "key_ending_in_id_without_separator": {"paid": "stop"},
    "write_scope_with_space": {"write_scope": "hold everything"},
}
QUIET_SAFE = {
    "ids_token": {"ids": "stop-123"},
    "request_id_512_chars": {"request_id": "stop" + "a" * 508},
    "time_field_token": {"deadline_utc": "2026-10-01T22:00:00Z"},
    "task_id_naming_stop": {"task_ids": ["codex-lead-1/bridge-stop-hook-20261001"]},
    "ts_token": {"ts": "halt-1"},
}


@pytest.mark.parametrize("name", sorted(QUIET_HOSTILE))
def test_quiet_key_with_a_non_token_control_value_is_unknown(name):
    r = decl(); r["payload"].update(QUIET_HOSTILE[name])
    assert last([old(), r]) == ("unknown", None)


@pytest.mark.parametrize("name", sorted(QUIET_SAFE))
def test_quiet_key_with_a_token_value_stays_none(name):
    r = decl(); r["payload"].update(QUIET_SAFE[name])
    assert last([old(), r]) == ("none", None)


def test_the_token_bound_is_exactly_512_characters():
    assert sa._TOKEN.fullmatch("a" * 512) and not sa._TOKEN.fullmatch("a" * 513)


def test_the_quiet_key_rule_is_the_imported_s_a_predicate_not_a_copy():
    assert module._sa is sa
    assert not hasattr(module, "CONTROL_STEMS") and not hasattr(module, "_QUIET_KEY") and not hasattr(module, "_TOKEN")


# ---- global row cap before iteration ----
class ExplodingRow(dict):
    pass


def test_rows_over_max_events_are_refused_before_any_row_is_read(monkeypatch):
    monkeypatch.setattr(sa, "MAX_EVENTS", 3)
    calls = []
    monkeypatch.setattr(sa, "_strict", lambda value: calls.append(value) or True)
    with pytest.raises(ValueError):
        classify_all([old(), decl(), old(request_id="x"), old(request_id="y")], LEAD)
    assert calls == []


def test_rows_at_max_events_are_classified(monkeypatch):
    monkeypatch.setattr(sa, "MAX_EVENTS", 2)
    assert classify_all([old(), decl()], LEAD) == [("v1", None), ("none", None)]


def test_the_real_cap_refuses_one_more_than_max_events():
    with pytest.raises(ValueError):
        classify_all([None] * (sa.MAX_EVENTS + 1), LEAD)


@pytest.mark.parametrize("rows", [tuple(), "rows", ExplodingRow(), None])
def test_rows_must_be_an_exact_list(rows):
    with pytest.raises(TypeError):
        classify_all(rows, LEAD)


def test_list_subclass_rows_are_refused():
    class Rows(list):
        pass
    with pytest.raises(TypeError):
        classify_all(Rows([old(), decl()]), LEAD)


def test_shared_cycle_between_rows_is_unknown():
    shared = {}; shared["again"] = shared
    a = decl(); a["payload"]["extra"] = shared
    assert classify_all([old(), a], LEAD)[1] == ("unknown", None)


def test_history_without_declaration_stays_v1_even_with_control_words():
    r = old(request_id="h1", message="HOLD everything, cancel the release", status="cancelled")
    assert classify_all([old(), r], LEAD)[1] == ("v1", None)


# ---- GAP D (RCO2 5b799 F4) and the non-authority identity twin (RCO2 5b799 F5, kills M8) ----
def test_a_non_authority_identity_cannot_declare_even_for_its_own_rows():
    lane = {"agent": "fable-5", "agent_uuid": "u-lane", "session_id": "s-l", "run_id": "r-l"}
    own = decl(**lane)
    assert classify_all([old(**lane), own], lane)[1] == ("unknown", None)


def nan_copy(row, value=float("nan")):
    bad = copy.deepcopy(row)
    bad["payload"]["score"] = value
    return bad


def test_GAP_D_malformed_duplicate_after_makes_the_declaration_unknown():
    assert classify_all([old(), decl(), nan_copy(decl())], LEAD) == [("v1", None), ("unknown", None), ("unknown", None)]


def test_GAP_D_malformed_duplicate_before_makes_the_declaration_unknown():
    assert classify_all([old(), nan_copy(decl()), decl()], LEAD)[2] == ("unknown", None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_GAP_D_any_unrelated_malformed_row_makes_every_declaration_unknown(value):
    unrelated = nan_copy(old(request_id="z", task_id="t/9"), value)
    out = classify_all([old(), decl("cancel", dict(TGT)), unrelated], LEAD)
    assert out == [("v1", None), ("unknown", None), ("unknown", None)]


def test_malformed_row_keeps_history_v1():
    hist = old(request_id="h1", message="HOLD", status="cancelled")
    assert classify_all([old(), hist, nan_copy(old(request_id="z"))], LEAD) == [
        ("v1", None), ("v1", None), ("unknown", None)]


HOOK_CALLS = []


class HookDict(dict):
    def __getitem__(self, key):
        HOOK_CALLS.append("getitem"); return dict.__getitem__(self, key)

    def get(self, key, default=None):
        HOOK_CALLS.append("get"); return dict.get(self, key, default)

    def __contains__(self, key):
        HOOK_CALLS.append("contains"); return dict.__contains__(self, key)

    def items(self):
        HOOK_CALLS.append("items"); return dict.items(self)

    def keys(self):
        HOOK_CALLS.append("keys"); return dict.keys(self)

    def __iter__(self):
        HOOK_CALLS.append("iter"); return dict.__iter__(self)

    def __eq__(self, other):
        HOOK_CALLS.append("eq"); return dict.__eq__(self, other)

    __hash__ = None


class HookList(list):
    def __iter__(self):
        HOOK_CALLS.append("list-iter"); return list.__iter__(self)

    def __getitem__(self, index):
        HOOK_CALLS.append("list-getitem"); return list.__getitem__(self, index)

    def __len__(self):
        HOOK_CALLS.append("list-len"); return list.__len__(self)


@pytest.mark.parametrize("shape", ["row", "payload", "nested_list"])
def test_GAP_D_hook_rows_are_unknown_with_zero_hooks_and_declarations_unknown(shape):
    if shape == "row":
        hostile = HookDict(decl(request_id="other"))
    elif shape == "payload":
        hostile = old(request_id="other"); hostile["payload"] = HookDict(hostile["payload"])
    else:
        hostile = old(request_id="other"); hostile["payload"]["steps"] = HookList(["a", "b"])
    HOOK_CALLS.clear()
    out = classify_all([old(), decl(), hostile], LEAD)
    calls = list(HOOK_CALLS)
    assert calls == []
    assert out == [("v1", None), ("unknown", None), ("unknown", None)]


def test_no_malformed_row_keeps_the_valid_declaration():
    assert classify_all([old(), decl(), old(request_id="z")], LEAD)[1] == ("none", None)
