"""Tests of situation_from_view (D10, fable-5): synthetic views only. The binding tests run step 1's REAL lane_view on
its own synthetic two-PR case (test_lane_view's builders, through the real core); the other tests build small views
by hand in lane_view's shape. No real capture, memory or model is read.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember/test_situation_from_view.py -q
"""
import copy
import hashlib
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import situation_from_view as sv  # noqa: E402

sys.path.insert(0, str(HERE.parent / "step1"))
import lane_view as lv  # noqa: E402
import test_lane_view as tlv  # noqa: E402  (step 1's synthetic builders, through the real core)

AS_OF = "2026-10-18T12:00:00Z"
GUARDED = "P" + "RIVATE" + "_MARKER"  # built at run time, never typed whole (test_remember_v2's construction)
FORMS = {"plain": GUARDED, "mixed_case": GUARDED[:3].lower() + GUARDED[3:],
         "escaped": "\\u%04x" % ord(GUARDED[0]) + GUARDED[1:],
         "in_a_link": f"[[{GUARDED}]]"}  # cleaning drops a [[link]], so the guard reads the raw body (mutant run 1)


def guarded(text):
    """The workspace's decoded guard, called here directly (never through the module under test)."""
    try:
        lv.ws.guard_object(text)
    except lv.ws.WorkspaceError:
        return True
    return False


def real_view():
    """Step 1's two-PR case: PR1905 has one empty slot (build_tools); PR1906 has three (build_tools and both RCOs) and
    waits longer (test_lane_view_the_only_empty_slot_comes_first_and_lines_are_limited)."""
    events = tlv.base_events() + [tlv.vote("2026-10-13T16:30:00Z", "codex-lead-1", head=tlv.SECOND_HEAD,
                                           task=tlv.SECOND_BRANCH, number=tlv.SECOND)]
    github = tlv.merge_github(tlv.github_of(), tlv.github_of(tlv.SECOND_HEAD, number=tlv.SECOND,
                                                             branch=tlv.SECOND_BRANCH, authors=["codex-tools-1"],
                                                             completed="2026-10-13T16:00:00Z"))
    return tlv.view_of(tlv.make_case(events, github))


def record(pr, branch, slots=("build_tools",), waiting="2026-10-18T11:00:00Z"):
    head = hashlib.sha1(f"head {pr}".encode()).hexdigest()
    return {"pr": pr, "state": "action", "empty_slots": list(slots), "waiting_since_utc": waiting, "branch": branch,
            "github_head": head, "reasons": [], "failing_checks": []}


def line(rec, slot):
    """The opening words lane_view.action_line gives a record's line (the binding tests check the real lines)."""
    kind = "RCO" if slot in lv.ws.RECOGNIZED_RCOS else "build"
    others = [other for other in rec["empty_slots"] if other != slot]
    where = f"({slot}, task {rec['branch']})"
    return (f"{kind} vote on PR{rec['pr']} at {rec['github_head'][:12]} "
            + (f"is the only empty slot {where}" if not others else f"is empty {where}; also empty: {', '.join(others)}")
            + "; the vote is still required")


def view_of(*records, as_of=AS_OF):
    """A small view in lane_view's shape; a lane's lines follow the records in the order given."""
    lanes = {lane: {"actions": [], "more": 0} for lane in lv.LANES}
    for rec in records:
        for slot in rec["empty_slots"]:
            lanes[lv.SLOT_OWNER[slot]]["actions"].append(line(rec, slot))
    return {"as_of_utc": as_of, "lanes": lanes, "prs": {str(rec["pr"]): rec for rec in records}}


def request(ts, to, message, kind="wake_request", ref=""):
    return {"ts_utc": ts, "agent": "codex-lead-1", "type": kind, "to": to, "message": message, "ref": ref}


DRIFT = record(7001, "codex-lead-1/lock-resolver-drift-check")


# --- Binding: the rule reads step 1's own names, order and record shape --------------------------------------------

def test_the_rule_uses_lane_views_lanes_and_slot_owners_and_remembers_tokenizer():
    assert sv.LANES == lv.LANES and sv.SLOT_OWNER == lv.SLOT_OWNER
    q4_source = (HERE.parent / "step2" / "q4.py").read_text(encoding="utf-8")
    assert f'TOKEN = re.compile(r"{sv.TOKEN.pattern}", re.UNICODE)' in q4_source and sv.TOKEN.flags & re.UNICODE


def test_an_action_record_has_no_reasons_and_no_failing_checks():
    """Why the rule uses neither: lane_view keeps reasons and failing check names only for a PR it abstains on (an
    action needs every required check green), and an abstained PR gives no action line."""
    view = real_view()
    actions = [rec for rec in view["prs"].values() if rec["state"] == "action"]
    assert len(actions) == 2 and all(rec["reasons"] == [] and rec["failing_checks"] == [] for rec in actions)
    results = {name: "SUCCESS" for name in tlv.REQUIRED} | {"unified": "FAILURE"}
    failing = tlv.view_of(tlv.make_case(github=tlv.github_of(results=results)))
    rec = failing["prs"][str(tlv.NUMBER)]
    assert rec["state"] == "abstain" and rec["failing_checks"] == ["unified"]
    assert sv.situation_from_view(failing, "codex-tools-1", tlv.AS_OF) == ("", "no_action", ())


def test_the_head_is_the_lanes_first_action_in_lane_views_order():
    view = real_view()
    heads = {lane: sv.head_record(view, lane) for lane in lv.LANES}
    # PR1906 waits longer (16:30:00Z) but has three empty slots; the only empty slot (PR1905) comes first
    assert heads["codex-tools-1"][0] == "build_tools" and heads["codex-tools-1"][1]["pr"] == tlv.NUMBER
    for rco in lv.ws.RECOGNIZED_RCOS:
        assert heads[rco][0] == rco and heads[rco][1]["pr"] == tlv.SECOND
    assert heads["codex-lead-1"] is None and heads["fable-5"] is None
    for lane, head in heads.items():
        if head is not None:
            kind = "RCO" if head[0] in lv.ws.RECOGNIZED_RCOS else "build"
            assert view["lanes"][lane]["actions"][0].startswith(f"{kind} vote on PR{head[1]['pr']} at ")


def test_the_real_view_gives_one_situation_two_identifier_only_and_two_no_action():
    view = real_view()
    assert sv.situation_from_view(view, "codex-tools-1", tlv.AS_OF) == (
        "codex-tools-1 build vote | codex lead operator manual ten train", "", ("lane", "branch"))
    for rco in lv.ws.RECOGNIZED_RCOS:  # codex-tools-1/second-20261013: one word ("second"); lane names never count
        assert sv.situation_from_view(view, rco, tlv.AS_OF) == ("", "identifier_only", ("lane", "branch"))
    for lane in ("codex-lead-1", "fable-5"):
        assert sv.situation_from_view(view, lane, tlv.AS_OF) == ("", "no_action", ())


# --- The request: the lane's task ----------------------------------------------------------------------------------

def test_the_newest_request_to_the_lane_is_its_task():
    asks = [request("2026-10-13T17:00:00Z", "claude-rco-1,claude-rco-2", "Review the second train: lock resolver drift"),
            request("2026-10-13T17:30:00Z", " claude-rco-1 ", "Check the vote binding on the EXACT head first.")]
    got = sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, asks)
    assert got == ("claude-rco-1 rco review | check the vote binding on the exact head first. | codex tools second", "",
                   ("lane", "request", "branch"))
    assert sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, asks[::-1]) == got
    listed = request("2026-10-13T17:45:00Z", ["codex-tools-1", "claude-rco-1"], "A listed recipient counts too")
    newest = sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, asks + [listed])
    assert newest.text.split(" | ")[1] == "a listed recipient counts too"


@pytest.mark.parametrize("ask", [
    request("2026-10-13T17:30:00Z", "claude-rco-2", "Review the lock resolver drift"),
    request("2026-10-13T17:30:00Z", "claude-rco-10", "Review the lock resolver drift"),
    request("2026-10-13T17:30:00Z", "claude-rco-1", "Review the lock resolver drift", kind="message"),
    request("2026-10-13T18:00:00.000001Z", "claude-rco-1", "Review the lock resolver drift"),
    request("2026-10-13T17:30:00", "claude-rco-1", "Review the lock resolver drift"),
], ids=["other_lane", "name_prefix", "message", "after_as_of_by_1us", "time_without_zone"])
def test_a_request_to_another_lane_not_a_request_after_as_of_or_without_a_time_is_ignored(ask):
    assert sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, [ask]) == (
        "", "identifier_only", ("lane", "branch"))


def test_a_request_exactly_at_as_of_is_seen():
    ask = request(tlv.AS_OF, "claude-rco-1", "Review the lock resolver drift")
    assert sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, [ask]) == (
        "claude-rco-1 rco review | review the lock resolver drift | codex tools second", "", ("lane", "request", "branch"))


def test_the_newest_request_is_found_by_time_not_by_text():  # added after mutant run 1 (R02 survived)
    """As text a fraction sorts before "Z": 17:30:00.5Z is the newer request, though the lesser string."""
    asks = [request("2026-10-13T17:30:00Z", "claude-rco-1", "Review the lock resolver drift"),
            request("2026-10-13T17:30:00.5Z", "claude-rco-1", "Check the exact head binding")]
    got = sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, asks)
    assert got.text.split(" | ")[1] == "check the exact head binding"


def test_requests_at_one_time_give_one_task_in_any_input_order():  # added after mutant run 1 (R09 survived)
    """parse_time keeps microseconds, so bridge stamps that differ in the seventh digit tie; the ref then decides."""
    asks = [request("2026-10-13T17:30:00.0000001Z", "claude-rco-1", "Review the lock resolver drift", ref="a" * 64),
            request("2026-10-13T17:30:00.0000009Z", "claude-rco-1", "Check the exact head binding", ref="b" * 64)]
    got = sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, asks)
    assert got == sv.situation_from_view(real_view(), "claude-rco-1", tlv.AS_OF, asks[::-1])
    assert got.text.split(" | ")[1] == "check the exact head binding"


# --- Privacy: the decoded guard before any word is taken, and on the finished text --------------------------------

@pytest.mark.parametrize("form", sorted(FORMS))
def test_a_guarded_request_body_is_dropped_whole_never_echoed(form):
    ask = request("2026-10-13T17:30:00Z", "codex-tools-1", f"Vote after the drift check {FORMS[form]} today")
    assert guarded(ask["message"])  # precondition: the decoded guard sees this form
    got = sv.situation_from_view(real_view(), "codex-tools-1", tlv.AS_OF, [ask])
    assert got == sv.situation_from_view(real_view(), "codex-tools-1", tlv.AS_OF)  # as if no request were given
    assert "drift" not in got.text and "today" not in got.text


def test_a_guarded_word_in_the_branch_refuses_the_situation_before_it_is_split():
    rec = record(7002, f"codex-lead-1/fix-{GUARDED.lower()}-drift-check")
    assert guarded(rec["branch"]) and not guarded("codex lead fix private marker drift check")  # split, it would pass
    assert sv.situation_from_view(view_of(rec), "codex-tools-1", AS_OF) == ("", "guarded", ())


def test_the_finished_situation_passes_the_guard(monkeypatch):
    """The decoded guard also runs on the finished text (here a stand-in guard trips only on it)."""
    monkeypatch.setattr(sv, "_guarded", lambda text: " | " in text)
    assert sv.situation_from_view(view_of(DRIFT), "codex-tools-1", AS_OF) == ("", "guarded", ("lane", "branch"))


# --- The words -----------------------------------------------------------------------------------------------------

def test_shas_uuids_links_and_numbers_never_reach_the_situation():
    sha, uuid = hashlib.sha1(b"x").hexdigest(), tlv.UUIDS["claude-rco-2"]
    rec = record(7003, f"fable-5/{sha[:9]}-lock-resolver-{uuid}-drift-1905-v2")
    ask = request("2026-10-18T11:30:00Z", "codex-tools-1", f"Recheck [[lesson_lock_drift]] at {sha} for {uuid} in PR 1905")
    assert sv.situation_from_view(view_of(rec), "codex-tools-1", AS_OF, [ask]) == (
        "codex-tools-1 build vote | recheck at for in pr 1905 | fable lock resolver drift v2", "",
        ("lane", "request", "branch"))


def test_a_hex_run_of_seven_is_dropped_and_one_of_six_kept():  # added after mutant run 1 (D04 and D05 survived)
    """The threshold is a git short sha (7): a six-character hex run and a six-letter word of hex letters stay."""
    sha = hashlib.sha1(b"x").hexdigest()
    ask = request("2026-10-18T11:30:00Z", "codex-tools-1", f"Check {sha[:6]} then {sha[:7]} facade")
    got = sv.situation_from_view(view_of(DRIFT), "codex-tools-1", AS_OF, [ask])
    assert got.text.split(" | ")[1] == f"check {sha[:6]} then facade"


def test_branch_words_are_lower_case_and_kept_once_in_first_seen_order():
    rec = record(7004, "codex-tools-1/drift-check-Drift-lock-check")
    assert sv.situation_from_view(view_of(rec), "codex-tools-1", AS_OF).text == (
        "codex-tools-1 build vote | codex tools drift check lock")


@pytest.mark.parametrize("branch, reason", [
    ("codex-lead-1/claude-rco-fable-lock-drift", "identifier_only"),
    ("codex-lead-1/claude-rco-fable-lock-drift-check", ""),
    ("codex-lead-1/20261018-1905-77", "identifier_only"),
], ids=["two_words_lane_names_never_count", "three_words", "numbers_only"])
def test_fewer_than_min_words_words_is_identifier_only(branch, reason):
    assert sv.MIN_WORDS == 3
    assert sv.situation_from_view(view_of(record(7005, branch)), "codex-tools-1", AS_OF).reason == reason


def test_numbers_and_lane_names_in_a_request_never_count_as_words():
    ask = request("2026-10-18T11:30:00Z", "codex-tools-1", "codex-lead-1 asks: 1905 1906 claude-rco-2")
    got = sv.situation_from_view(view_of(record(7006, "1905-77")), "codex-tools-1", AS_OF, [ask])
    assert got == ("", "identifier_only", ("lane", "request"))  # one word: "asks"


def test_a_repeated_word_counts_once():  # added after mutant run 1 (W07 survived)
    ask = request("2026-10-18T11:30:00Z", "codex-tools-1", "drift drift drift")
    got = sv.situation_from_view(view_of(record(7012, "1905-77")), "codex-tools-1", AS_OF, [ask])
    assert got == ("", "identifier_only", ("lane", "request"))


def test_a_long_request_and_branch_are_clipped_at_word_ends_inside_the_e5_window():
    words = [f"w{chr(103 + i % 20)}{chr(103 + i // 20 % 20)}x" for i in range(400)]  # 400 distinct non-hex words
    ask = request("2026-10-18T11:30:00Z", "codex-tools-1", " ".join(words))
    got = sv.situation_from_view(view_of(record(7007, "codex-lead-1/" + "-".join(words[:40]))), "codex-tools-1", AS_OF,
                                 [ask])
    lane, asked, branch = got.text.split(" | ")
    assert got.reason == "" and len(got.text) <= 1800 and lane == "codex-tools-1 build vote"
    assert len(asked) <= sv.CAPS["request"] < len(asked) + 5 and (" ".join(words) + " ").startswith(asked + " ")
    branch_words = " ".join(["codex", "lead"] + words[:40])
    assert len(branch) <= sv.CAPS["branch"] < len(branch) + 5 and (branch_words + " ").startswith(branch + " ")


@pytest.mark.parametrize("cap, clipped", [(9, "aaaa bbbb"), (10, "aaaa bbbb"), (11, "aaaa bbbb"),
                                          (14, "aaaa bbbb cccc"), (3, "aaa")],
                         ids=["cap_ends_a_word", "cap_on_a_space", "cap_inside_a_word", "fits", "one_long_word"])
def test_a_clip_cuts_at_a_space_and_keeps_the_word_the_cap_ends(cap, clipped):  # added after GREEN 1 (it dropped it)
    assert sv._clip("aaaa bbbb cccc" if cap > 3 else "aaaabbbb", cap) == clipped


# --- The view: its first line and its time; the caller's time -----------------------------------------------------

def test_the_lanes_first_line_must_name_the_head_at_its_head_slot_and_task():
    view = view_of(DRIFT)
    assert sv.situation_from_view(view, "codex-tools-1", AS_OF).reason == ""
    first = view["lanes"]["codex-tools-1"]["actions"][0]
    for broken in (first.replace("PR7001", "PR7009"), first.replace(DRIFT["github_head"][:12], "0" * 12),
                   first.replace("(build_tools,", "(build_lead,"), first.replace("lock-resolver", "lock-resolved"),
                   "RCO" + first[len("build"):], None):
        changed = copy.deepcopy(view)
        changed["lanes"]["codex-tools-1"]["actions"] = [] if broken is None else [broken]
        assert sv.situation_from_view(changed, "codex-tools-1", AS_OF) == ("", "view_mismatch", ())


def test_another_prs_first_line_is_refused_though_the_heads_line_follows():  # added after mutant run 1 (L05 survived)
    older = record(7010, "codex-lead-1/lock-drift-check", waiting="2026-10-18T10:00:00Z")
    newer = record(7011, "codex-lead-1/resolver-train-merge")
    assert sv.situation_from_view(view_of(older, newer), "codex-tools-1", AS_OF).reason == ""
    assert sv.situation_from_view(view_of(newer, older), "codex-tools-1", AS_OF) == ("", "view_mismatch", ())


def test_equal_waiting_times_go_by_pr_number():  # added after mutant run 1 (P04 survived)
    """lane_view's last tie-break: of two only empty slots waiting since one time, the lower PR is the head."""
    low, high = record(7008, "codex-lead-1/lock-drift-check"), record(7009, "codex-lead-1/resolver-train-merge")
    assert sv.situation_from_view(view_of(low, high), "codex-tools-1", AS_OF) == (
        "codex-tools-1 build vote | codex lead lock drift check", "", ("lane", "branch"))
    assert sv.situation_from_view(view_of(high, low), "codex-tools-1", AS_OF) == ("", "view_mismatch", ())


def test_a_view_later_than_as_of_or_without_a_time_is_refused():
    for later in ("2026-10-18T12:00:00.000001Z", "noon", None):
        assert sv.situation_from_view(view_of(DRIFT, as_of=later), "codex-tools-1", AS_OF) == ("", "view_time", ())
    assert sv.situation_from_view(view_of(DRIFT, as_of="2026-10-18T11:59:59Z"), "codex-tools-1", AS_OF).reason == ""


@pytest.mark.parametrize("as_of", [None, "", "2026-10-18 12:00:00Z", "2026-10-18T12:00:00", "2026-10-18T15:00:00+03:00"])
def test_an_unreadable_as_of_raises(as_of):
    with pytest.raises(ValueError):
        sv.situation_from_view(view_of(DRIFT), "codex-tools-1", as_of)


def test_the_rule_is_free_of_input_order_and_changes_no_input():
    view = real_view()
    asks = [request("2026-10-13T17:00:00Z", "claude-rco-1", "Review the second train: lock resolver drift"),
            request("2026-10-13T17:30:00Z", "claude-rco-1", "Check the vote binding on the exact head first")]
    before = copy.deepcopy((view, asks))
    got = sv.situation_from_view(view, "claude-rco-1", tlv.AS_OF, asks)
    assert (view, asks) == before and got.reason == ""
    flipped = copy.deepcopy(view)
    flipped["prs"] = dict(reversed(list(flipped["prs"].items())))
    assert sv.situation_from_view(flipped, "claude-rco-1", tlv.AS_OF, asks[::-1]) == got
    assert [sv.head_record(flipped, lane) for lane in lv.LANES] == [sv.head_record(view, lane) for lane in lv.LANES]
