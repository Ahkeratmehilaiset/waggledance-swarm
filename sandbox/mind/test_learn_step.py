"""Tests for LEARN step v1 (learned state from RCO2's outcome results), synthetic only. Written BEFORE the code.

The cases follow the test list of one direct Grok xhigh consultation (answer sha256 acc2c4ae..., fable-5's audit
folder), mapped onto RCO2's real result shape (``wd.mind-join-card-outcomes.v1``): a failed proof is already an
UNKNOWN row there, an action kind is the vote slot's class, and a row's time is its action time (or its read time
for OTHER and NONE).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_learn_step.py -q -p no:cacheprovider
"""

import hashlib
import json
import random
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import learn_step as ls  # noqa: E402

LANE, OTHER_LANE = "claude-rco-2", "claude-rco-1"
DAY0 = "2026-10-18T"
_counter = [0]


def stamp(day=0, second=None):
    _counter[0] += 1
    second = _counter[0] if second is None else second
    return f"2026-10-{18 + day:02d}T{second // 3600 % 24:02d}:{second // 60 % 60:02d}:{second % 60:02d}Z"


def row(outcome, reason=None, ms=None, corrected=False, slot=LANE, day=0, event=None, line=1, pr=7):
    ts = stamp(day)
    ref = event or hashlib.sha256(f"{ts}|{_counter[0]}".encode()).hexdigest()
    taken = outcome.startswith("TAKEN") or outcome == "PRE_READ"
    return {"line": line, "pr": pr, "slot": slot, "head": "a" * 40, "task": "b/branch", "text_sha256": "c" * 64,
            "read_utc": ts, "event_ref": ref if taken else None, "event_utc": ts if taken else None,
            "read_to_action_ms": ms, "head_sampled": None, "later_actions": [], "corrected_after_wrong": corrected,
            "outcome": outcome, "reason": reason}


def result(rows, lane=LANE, card=None):
    card = card or hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return {"schema": "wd.mind-join-card-outcomes.v1", "authority": "none", "scope": "offline_outcome_rows_not_gate",
            "lane": lane, "inputs": {"card_sha256": card}, "card": "accepted", "rows": rows, "gap": None}


def cards(rows, lane=LANE):
    """One result (card) per row, so every row is its own card."""
    return [result([r], lane=lane) for r in rows]


def tape(state, lane=LANE, kind="rco_vote"):
    return ls.summarize(state, lane, kind)


def notes(state, lane=LANE):
    return ls.notes(state, lane)


# 1, 2 -------------------------------------------------------------------------------------------------------------

def test_all_unknown_rows_are_no_evidence():
    state = ls.learn(cards([row("UNKNOWN", "identity_unverified") for _ in range(10)]))
    assert state["lanes"][LANE]["non_evidence"]["unknown"] == 10
    assert tape(state)["known"] == 0 and notes(state) == [] and state["drafts"] == []
    assert state["lanes"].get(OTHER_LANE, {}).get("tapes", {}) == {}


def test_pre_read_rows_are_no_evidence():
    state = ls.learn(cards([row("PRE_READ", "valid_vote_before_read") for _ in range(5)]))
    assert state["lanes"][LANE]["non_evidence"]["pre_read"] == 5
    assert tape(state)["known"] == 0 and notes(state) == []


# 3 min-n --------------------------------------------------------------------------------------------------------------

def test_min_n_keeps_the_card_silent():
    state = ls.learn(cards([row("TAKEN_CORRECT") for _ in range(5)] + [row("NONE") for _ in range(2)]))
    assert (tape(state)["known"], tape(state)["taken"], tape(state)["correct"]) == (7, 5, 5) and notes(state) == []
    state = ls.learn(cards([row("TAKEN_STALE") for _ in range(5)] + [row("NONE") for _ in range(3)]))
    summary = tape(state)
    assert (summary["known"], summary["taken"], summary["correct"], summary["wrong"]) == (8, 5, 0, 0)
    assert notes(state) == [] and state["drafts"] == []


# 4 harm --------------------------------------------------------------------------------------------------------------

def harm_rows():
    return ([row("TAKEN_CORRECT") for _ in range(3)] + [row("TAKEN_WRONG", "pr_mismatch"),
            row("TAKEN_WRONG", "wrong_binding")] + [row("NONE") for _ in range(3)])


def test_harm_note_opens_cites_its_rows_and_outranks_praise():
    rows = harm_rows()
    state = ls.learn(cards(rows))
    wrong_hashes = [ls.row_hash(c["inputs"]["card_sha256"], LANE, c["rows"][0]) for c in cards(rows)
                    if c["rows"][0]["outcome"] == "TAKEN_WRONG"]
    expected = (f"LEARN: similar rco_vote wrong 2 of taken 5 (1 pr_mismatch, 1 wrong_binding; rows "
                f"{wrong_hashes[1][:8]}, {wrong_hashes[0][:8]})")
    assert notes(state) == [expected] and state["drafts"] == []
    state = ls.learn(cards([row("TAKEN_CORRECT") for _ in range(5)] + [row("TAKEN_WRONG", "pr_mismatch")
                                                                        for _ in range(2)] + [row("NONE")]))
    assert notes(state)[0].startswith("LEARN: similar rco_vote wrong 2 of taken 7")


# 5-7 praise and time ------------------------------------------------------------------------------------------------

def test_correct_note_without_time():
    state = ls.learn(cards([row("TAKEN_CORRECT") for _ in range(5)] + [row("NONE") for _ in range(3)]))
    assert len(notes(state)) == 1 and notes(state)[0].startswith("LEARN: similar rco_vote correct 5 of taken 5 (rows ")
    assert "median" not in notes(state)[0]


def test_odd_median():
    rows = [row("TAKEN_CORRECT", ms=m * 60000) for m in (1, 2, 3, 4, 5)] + [row("NONE") for _ in range(3)]
    assert notes(ls.learn(cards(rows)))[0].endswith("; median read-to-action 3 min over 5")


def test_even_median_and_too_few_timed():
    rows = [row("TAKEN_CORRECT", ms=m * 60000) for m in (1, 2, 3, 4, 10, 11)] + [row("NONE") for _ in range(2)]
    assert notes(ls.learn(cards(rows)))[0].endswith("; median read-to-action 3 min over 6")
    rows = [row("TAKEN_CORRECT", ms=m * 60000) for m in (1, 2, 3, 4)] + [row("TAKEN_CORRECT")] + \
        [row("NONE") for _ in range(3)]
    assert "median" not in notes(ls.learn(cards(rows)))[0]


# 8 position and rotation ----------------------------------------------------------------------------------------------

def test_the_same_evidence_on_other_cards_gives_the_same_counts():
    rows = harm_rows()
    one = ls.learn(cards(rows))
    two = ls.learn([result([r], card=hashlib.sha256(b"other" + bytes([i])).hexdigest()) for i, r in enumerate(rows)])
    assert tape(one) == {k: v for k, v in tape(two).items() if k != "cite"} | {"cite": tape(one)["cite"]}
    assert notes(one)[0].split(" (")[0] == notes(two)[0].split(" (")[0]


def test_rotation_is_a_pure_function_of_the_card_and_never_of_the_state():
    order = [(5, 1), (7, 2), (9, 3)]
    shown = ls.rotate(order, "d" * 64, LANE)
    assert sorted(shown) == order and shown == ls.rotate(order, "d" * 64, LANE)
    assert shown in [order[k:] + order[:k] for k in range(3)]
    assert ls.rotate([], "d" * 64, LANE) == []


# 9 isolation and poisoning ------------------------------------------------------------------------------------------

def test_one_lane_never_moves_another_lanes_state():
    rows = harm_rows()
    base = ls.learn(cards(rows))
    poisoned = ls.learn(cards(rows) + cards([row("TAKEN_WRONG", "pr_mismatch", slot=OTHER_LANE)
                                            for _ in range(9)], lane=OTHER_LANE))
    assert tape(base) == tape(poisoned) and notes(base) == notes(poisoned)
    assert notes(poisoned, OTHER_LANE) == ["LEARN: similar rco_vote wrong 9 of taken 9 (9 pr_mismatch; rows "
                                           + ", ".join(h[:8] for h in tape(poisoned, OTHER_LANE)["cite"]["wrong"]) + ")"]
    unproven = ls.learn(cards(rows + [row("UNKNOWN", "identity_unverified")]))
    assert tape(unproven) == tape(base) and notes(unproven) == notes(base)


def test_an_event_counts_once_however_many_cards_cite_it():
    shared = row("TAKEN_WRONG", "pr_mismatch", event="e" * 64)
    state = ls.learn([result([shared]), result([dict(shared)], card="f" * 64)])
    assert tape(state)["wrong"] == 1 and state["rejected"]["duplicate_event"] == 1


# 10 cap ---------------------------------------------------------------------------------------------------------------

def test_daily_cap():
    rows = [row("TAKEN_WRONG", "pr_mismatch", day=1) for _ in range(33)]
    state = ls.learn(cards(rows))
    assert tape(state)["known"] == 32 and state["rejected"]["cap"] == 1
    assert notes(state)[0].startswith("LEARN: similar rco_vote wrong 32 of taken 32")


# 11 replay ----------------------------------------------------------------------------------------------------------

def test_replay_is_order_free_and_fails_closed():
    results = cards(harm_rows())
    one = ls.learn(results)
    shuffled = list(results)
    random.Random(7).shuffle(shuffled)
    assert ls.canonical(ls.learn(shuffled)) == ls.canonical(one) and one["state_hash"] == ls.learn(shuffled)["state_hash"]
    assert ls.learn(results + [json.loads(json.dumps(results[0]))])["state_hash"] == one["state_hash"]
    divergent = json.loads(json.dumps(results[0]))  # the same card line with another history: RCO2's aggregate
    divergent["rows"][0]["outcome"] = "NONE"        # calls this a bound, not a resolution, so it is no evidence
    conflicted = ls.learn(results + [divergent])
    assert conflicted["rejected"]["line_conflict"] == 1
    assert (tape(conflicted)["known"], tape(conflicted)["correct"]) == (7, 2)
    assert ls.learn([])["state_hash"] == ls.learn([])["state_hash"]
    tampered = json.loads(json.dumps(one))
    entry = next(e for e in tampered["lanes"][LANE]["tapes"]["rco_vote"] if e["outcome"] == "NONE")
    entry.update(outcome="TAKEN_WRONG", reason="pr_mismatch")  # unverified, this would say "wrong 3 of taken 6"
    assert ls.notes(tampered, LANE) == [] and ls.verify(one) and not ls.verify(tampered)


def test_a_returned_state_shares_nothing_with_another_or_with_the_module():
    """Lead 07:41Z (LEARN_PARAMS_RETURN_ALIAS): the state exposed the module's PARAMS dict, so editing one returned
    state changed another snapshot, the next re-fold hash and the declared window."""
    results = cards(harm_rows())
    one, two = ls.learn(results), ls.learn(results)
    one["params"]["window_n"] = 1
    one["lanes"][LANE]["tapes"]["rco_vote"].clear()
    one["rejected"]["cap"] = 99
    assert ls.verify(two) and ls.learn(results)["state_hash"] == two["state_hash"]
    assert ls.PARAMS["window_n"] == ls.WINDOW_N == two["params"]["window_n"]
    assert tape(two)["known"] == 8 and two["rejected"]["cap"] == 0


def test_a_result_that_is_not_an_outcome_result_is_refused():
    bad = result([row("TAKEN_CORRECT")])
    for change in ({"schema": "x"}, {"authority": "gate"}, {"lane": "Not A Lane!"}, {"inputs": {"card_sha256": "z"}},
                   {"rows": "x"}, {"rows": ["x"]}):
        with pytest.raises(ls.LearnError):
            ls.learn([dict(bad, **change)])
    for value in (float("nan"), float("inf"), {1, 2}, b"x"):  # a value JSON cannot hold is refused, never a crash
        with pytest.raises(ls.LearnError):
            ls.learn([result([dict(row("TAKEN_CORRECT"), read_to_action_ms=value)], card="a" * 64)])
    for key in ("line", "outcome"):
        with pytest.raises(ls.LearnError):
            ls.learn([result([{k: v for k, v in row("TAKEN_CORRECT").items() if k != key}])])
    edge = row("TAKEN_CORRECT")
    edge.update(read_utc="9999-12-31T23:59:59Z", event_utc="9999-12-31T23:59:59Z")
    assert tape(ls.learn(cards([edge])))["correct"] == 1


# 12, 13 drafts and decay --------------------------------------------------------------------------------------------

def test_wrong_then_corrected_drafts_a_candidate_lesson_only_at_two():
    first = row("TAKEN_WRONG", "pr_mismatch", corrected=True)
    assert ls.learn(cards([first]))["drafts"] == [] and notes(ls.learn(cards([first]))) == []
    state = ls.learn(cards([first, row("TAKEN_WRONG", "pr_mismatch")]))
    (draft,) = state["drafts"]
    assert draft["template_id"] == "recheck_head_corrected_v1" and draft["support_count"] == 2
    assert draft["status"] == "CANDIDATE" and len(draft["row_hashes"]) == 2
    assert "7" not in draft["text"] and "a" * 8 not in draft["text"] and "rco_vote" in draft["text"]
    assert ls.learn(cards([row("TAKEN_WRONG", "pr_mismatch"), row("TAKEN_WRONG", "wrong_binding")]))["drafts"] == []


def test_decay_washes_out_an_old_warning():
    prefix = [row("TAKEN_WRONG", "pr_mismatch") for _ in range(5)] + [row("NONE") for _ in range(3)]
    tail = [row("NONE", day=1 + i // 25) for i in range(200)]
    old = ls.learn(cards(prefix))
    assert notes(old) and old["drafts"][0]["support_count"] == 5
    new = ls.learn(cards(prefix + tail))
    assert tape(new)["wrong"] == 0 and tape(new)["known"] == ls.WINDOW_N and notes(new) == []
    assert new["drafts"] == [] and new["lanes"][LANE]["dropped_by_window"] >= 5 and new["rejected"]["cap"] == 0


# 14, 15 REMEMBER warnings ------------------------------------------------------------------------------------------

def picks(card, shown=("A.md", "B.md", "C.md"), picked=("A.md",), lane=LANE):
    return {"card_sha256": card, "lane": lane, "shown": list(shown), "picked": list(picked)}


def lesson_cards(rollups):
    results, remember = [], []
    for i, rollup in enumerate(rollups):
        card = hashlib.sha256(f"card{i}{rollup}".encode()).hexdigest()
        outcome = {"wrong": ("TAKEN_WRONG", "pr_mismatch"), "correct": ("TAKEN_CORRECT", None)}[rollup]
        results.append(result([row(*outcome, pr=100 + i)], card=card))  # v1.1: each card acts on its own PR
        remember.append(picks(card))
    return results, remember


def test_a_lesson_followed_by_wrong_actions_is_warned_and_moved_last_never_promoted():
    results, remember = lesson_cards(["wrong", "wrong", "wrong", "correct", "correct"])
    state = ls.learn(results, remember)
    order, warnings = ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"])
    assert order == ["B.md", "C.md", "A.md"]
    assert warnings["A.md"].startswith("LEARN: on 5 cards where you picked this, wrong 3 of taken 5 actions, on 3 PRs "
                                       "(rows ")
    assert warnings["A.md"].endswith("; association only")
    results, remember = lesson_cards(["wrong", "wrong", "correct", "correct", "correct"])
    state = ls.learn(results, remember)
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})
    forged = json.loads(json.dumps(state))
    forged["lanes"][LANE]["lessons"]["A.md"]["wrong"] = 5
    assert ls.lesson_order(forged, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})


def test_partial_coverage_gives_no_lesson_evidence():
    results, remember = [], []
    for i in range(5):
        card = hashlib.sha256(f"partial{i}".encode()).hexdigest()
        results.append(result([row("TAKEN_WRONG", "pr_mismatch", line=1), row("UNKNOWN", "coverage_incomplete", line=2)],
                              card=card))
        remember.append(picks(card))
    state = ls.learn(results, remember)
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})
    assert tape(state)["wrong"] == 5


def test_mismatched_pick_records_for_one_card_are_rejected():
    results, remember = lesson_cards(["wrong"] * 6)
    remember.append(dict(remember[0], picked=["B.md"]))
    state = ls.learn(results, remember)
    assert state["rejected"]["pick_mismatch"] == 1  # both records of that card are dropped, the other five count
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"])[1]["A.md"].startswith(
        "LEARN: on 5 cards where you picked this, wrong 5 of taken 5 actions, on 5 PRs (rows ")


def test_a_pick_outside_the_shown_lines_or_a_guarded_word_refuses_the_whole_state():
    results, remember = lesson_cards(["wrong"] * 5)
    with pytest.raises(ls.LearnError):
        ls.learn(results, remember + [dict(remember[0], card_sha256="9" * 64, picked=["Z.md"])])
    word = "PRIVATE" + "_MARKER"
    with pytest.raises(ls.LearnError):
        ls.learn(results, [dict(remember[0], card_sha256="9" * 64, shown=[word + ".md"], picked=[word + ".md"])])


def test_the_runner_folds_once_and_reads_notes_and_order(tmp_path, capsys):
    results, remember = lesson_cards(["wrong", "wrong", "wrong", "correct", "correct"])
    results += cards([row("NONE") for _ in range(3)])
    paths = []
    for i, item in enumerate(results):
        paths.append(tmp_path / f"r{i}.json")
        paths[-1].write_text(json.dumps(item), encoding="utf-8")
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps({"picks": remember}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    argv = ["fold", "--results", *map(str, paths), "--picks", str(picks), "--out", str(state_path)]
    assert ls.main(argv) == 0
    folded = json.loads(capsys.readouterr().out)
    assert folded["state_hash"] == ls.learn(results, remember)["state_hash"]
    assert ls.main(argv) == 2 and "already exists" in capsys.readouterr().err  # a state file is never overwritten
    assert ls.main(["notes", "--state", str(state_path), "--lane", LANE]) == 0
    assert capsys.readouterr().out.startswith("LEARN: similar rco_vote wrong 3 of taken 5 (3 pr_mismatch; rows ")
    assert ls.main(["order", "--state", str(state_path), "--lane", LANE, "--shown", "A.md", "B.md", "C.md"]) == 0
    assert json.loads(capsys.readouterr().out)["order"] == ["B.md", "C.md", "A.md"]
    state_path.write_text(state_path.read_text(encoding="ascii").replace('"NONE"', '"OTHER"', 1), encoding="ascii")
    assert ls.main(["notes", "--state", str(state_path), "--lane", LANE]) == 2
    assert "fails its own hash" in capsys.readouterr().err


def test_an_action_time_outside_its_read_window_is_no_evidence():
    """Grok review D #4: 200 correct rows dated 2099 must not push two real wrongs out of the window."""
    wrongs = [row("TAKEN_WRONG", "pr_mismatch") for _ in range(2)]
    future = []
    for i in range(200):
        item = row("TAKEN_CORRECT", day=0)
        item["event_utc"] = f"2099-01-{1 + i // 32:02d}T00:00:{i % 32:02d}Z"
        future.append(item)
    before = row("TAKEN_CORRECT")
    before["event_utc"] = "2026-10-17T00:00:00Z"  # an action before its read is not a taken row here
    state = ls.learn(cards(wrongs + future + [before]))
    assert tape(state)["wrong"] == 2 and tape(state)["correct"] == 0 and state["rejected"]["malformed"] == 201
    inside = row("TAKEN_CORRECT")
    inside["event_utc"] = inside["read_utc"][:-1] + "Z"
    assert tape(ls.learn(cards([inside])))["correct"] == 1


def test_a_pre_read_line_does_not_void_a_card_for_lesson_evidence():
    """Grok review D #5: PRE_READ is known and outside every denominator; only UNKNOWN voids a card."""
    results, remember = [], []
    for i in range(5):
        card = hashlib.sha256(f"preread{i}".encode()).hexdigest()
        results.append(result([row("PRE_READ", "valid_vote_before_read", line=1),
                               row("TAKEN_WRONG", "pr_mismatch", line=2, pr=200 + i)], card=card))
        remember.append(picks(card, picked=("Lx.md",), shown=("Lx.md", "B.md", "C.md")))
    order, warnings = ls.lesson_order(ls.learn(results, remember), LANE, ["Lx.md", "B.md", "C.md"])
    assert order == ["B.md", "C.md", "Lx.md"] and "wrong 5 of taken 5 actions, on 5 PRs" in warnings["Lx.md"]
    only_pre = hashlib.sha256(b"only-pre").hexdigest()
    state = ls.learn([result([row("PRE_READ", "valid_vote_before_read")], card=only_pre)], [picks(only_pre)])
    assert state["rejected"]["pick_without_card"] == 0 and state["lanes"][LANE]["lessons"] == {}


def test_one_event_judged_two_ways_stays_no_evidence_as_in_rcos_aggregate():
    """Grok review D #6 is NOT adopted: RCO2's aggregate makes any disagreement on one event_ref
    UNKNOWN(aggregate_conflict); LEARN keeps the same rule so it never counts what the source calls a conflict."""
    a = row("TAKEN_WRONG", "pr_mismatch", event="9" * 64, line=10)
    b = row("TAKEN_WRONG", "wrong_binding", event="9" * 64, line=11)
    state = ls.learn([result([a, b])])
    assert tape(state)["wrong"] == 0 and state["rejected"]["event_conflict"] == 2


def test_slots_map_to_two_action_kinds_and_kinds_never_mix():
    rows = [row("TAKEN_WRONG", "pr_mismatch", slot="build_lead") for _ in range(5)] + \
        [row("NONE", slot="build_lead") for _ in range(3)]
    state = ls.learn(cards(rows))
    assert tape(state, kind="build_vote")["wrong"] == 5 and tape(state)["known"] == 0
    assert notes(state)[0].startswith("LEARN: similar build_vote wrong 5 of taken 5")
    rotated = ls.notes(state, LANE, card="d" * 64)
    assert sorted(rotated) == sorted(notes(state))


# 16 LEARN v1.1: a lesson's evidence is counted in distinct events, never in cards (RCO1 round 9) --------------------

def acted(outcome, reason, event, read_utc, event_utc, pr, line=1):
    """A taken row of a card read at ``read_utc`` whose line the action ``event`` decided at ``event_utc``: several
    cards hold the same event, as consecutive cards do inside RCO2's read window."""
    item = row(outcome, reason, event=event, line=line, pr=pr)
    item.update(read_utc=read_utc, event_utc=event_utc)
    return item


def picked_cards(rowsets, picked=("A.md",)):
    """One card per row list, each with this lane's pick record."""
    results, remember = [], []
    for i, rows in enumerate(rowsets):
        card = hashlib.sha256(f"v11-card{i}-{json.dumps(rows, sort_keys=True)}".encode()).hexdigest()
        results.append(result(rows, card=card))
        remember.append(picks(card, picked=picked))
    return results, remember


def counts(lesson):
    return {key: lesson[key] for key in ("cards", "taken", "wrong", "prs")}


def test_one_wrong_vote_held_by_three_cards_is_one_wrong_action_and_warns_nothing():
    """v1 counted cards, so one wrong vote that three consecutive cards held gave 'wrong 3 of support 5' and demoted
    the lesson; an event is one action however many cards hold it (RCO2's own aggregate rule)."""
    wrong = "1" * 64
    rowsets = [[acted("TAKEN_WRONG", "pr_mismatch", wrong, f"{DAY0}10:{10 * k:02d}:00Z", f"{DAY0}10:40:00Z", pr=301)]
               for k in range(3)]
    rowsets += [[acted("TAKEN_CORRECT", None, str(k) * 64, f"{DAY0}11:00:00Z", f"{DAY0}11:0{k}:00Z", pr=300 + k)]
                for k in (2, 3)]
    state = ls.learn(*picked_cards(rowsets))
    lesson = state["lanes"][LANE]["lessons"]["A.md"]
    assert counts(lesson) == {"cards": 5, "taken": 3, "wrong": 1, "prs": 1}
    assert len(lesson["cite"]) == 1
    assert set(lesson["cite"]) <= {entry["row_hash"] for entry in state["lanes"][LANE]["tapes"]["rco_vote"]}
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})


def test_a_busy_card_adds_each_of_its_actions_so_one_wrong_of_three_is_one_of_three():
    """v1 called a card wrong when any of its lines went wrong, so a lesson picked for busy cards took the blame for
    every line; now a card adds its distinct actions to each lesson picked for it."""
    rowsets = []
    for card in range(5):
        pr = 400 + 3 * card
        read = f"{DAY0}12:00:00Z"
        rowsets.append([acted("TAKEN_WRONG", "pr_mismatch", f"{pr:064d}", read, f"{DAY0}12:01:00Z", pr=pr)]
                       + [acted("TAKEN_CORRECT", None, f"{pr + k:064d}", read, f"{DAY0}12:0{1 + k}:00Z", pr=pr + k,
                                line=1 + k) for k in (1, 2)])
    state = ls.learn(*picked_cards(rowsets))
    assert counts(state["lanes"][LANE]["lessons"]["A.md"]) == {"cards": 5, "taken": 15, "wrong": 5, "prs": 5}
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})
    lessons = ls.learn(*picked_cards(rowsets[:1], picked=("A.md", "B.md", "C.md")))["lanes"][LANE]["lessons"]
    assert {ref: (lesson["cards"], lesson["taken"], lesson["wrong"]) for ref, lesson in lessons.items()} == \
        {ref: (1, 3, 1) for ref in ("A.md", "B.md", "C.md")}


def test_wrong_votes_on_one_pr_are_one_episode_and_warn_nothing():
    """The wrong actions must lie on at least LESSON_PRS different card-line PRs, so one bad episode on one PR cannot
    demote a lesson alone."""
    def rowsets(wrong_prs):
        return [[row("TAKEN_WRONG", "pr_mismatch", pr=pr)] for pr in wrong_prs] + \
            [[row("TAKEN_CORRECT", pr=600 + k)] for k in range(2)]
    state = ls.learn(*picked_cards(rowsets([7, 7, 7])))
    assert counts(state["lanes"][LANE]["lessons"]["A.md"]) == {"cards": 5, "taken": 5, "wrong": 3, "prs": 1}
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})
    order, warnings = ls.lesson_order(ls.learn(*picked_cards(rowsets([7, 7, 8]))), LANE, ["A.md", "B.md", "C.md"])
    assert order == ["B.md", "C.md", "A.md"]
    assert warnings["A.md"].startswith("LEARN: on 5 cards where you picked this, wrong 3 of taken 5 actions, on 2 PRs "
                                       "(rows ")


def two_wrong_votes_each_held_by_two_cards():
    first, second, right = "a1" * 32, "b2" * 32, "c3" * 32
    rowsets = [[acted("TAKEN_WRONG", "pr_mismatch", first, f"{DAY0}13:0{k}:00Z", f"{DAY0}13:30:00Z", pr=701)]
               for k in (0, 1)]
    rowsets += [[acted("TAKEN_WRONG", "wrong_binding", second, f"{DAY0}14:0{k}:00Z", f"{DAY0}14:30:00Z", pr=702)]
                for k in (0, 1)]
    return rowsets + [[acted("TAKEN_CORRECT", None, right, f"{DAY0}15:00:00Z", f"{DAY0}15:10:00Z", pr=703)]]


def test_two_wrong_votes_each_held_by_two_cards_warn_and_cite_two_events_of_the_tape():
    """Two wrong votes on two PRs, each held by two cards, and one right vote still warn (as in v1), now as wrong 2 of
    taken 3 actions; the two cited rows are two different events, each a row of the tape (v1 cited one row per card,
    so it cited one event twice, once by a row the tape dropped as a duplicate)."""
    state = ls.learn(*picked_cards(two_wrong_votes_each_held_by_two_cards()))
    taped = {entry["row_hash"]: entry for entry in state["lanes"][LANE]["tapes"]["rco_vote"]}
    lesson = state["lanes"][LANE]["lessons"]["A.md"]
    assert counts(lesson) == {"cards": 5, "taken": 3, "wrong": 2, "prs": 2}
    assert len(set(lesson["cite"])) == 2 and set(lesson["cite"]) <= set(taped)
    assert [taped[h]["utc"][:16] for h in lesson["cite"]] == [f"{DAY0}14:30", f"{DAY0}13:30"]  # the newest first
    order, warnings = ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"])
    assert order == ["B.md", "C.md", "A.md"]
    assert warnings == {"A.md": f"LEARN: on 5 cards where you picked this, wrong 2 of taken 3 actions, on 2 PRs (rows "
                                f"{lesson['cite'][0][:8]}, {lesson['cite'][1][:8]}); association only"}


def test_lesson_counts_are_order_free():
    results, remember = picked_cards(two_wrong_votes_each_held_by_two_cards())
    one = ls.learn(results, remember)
    random.Random(11).shuffle(results)
    random.Random(12).shuffle(remember)
    assert ls.canonical(ls.learn(results, remember)) == ls.canonical(one)


def test_half_of_the_actions_wrong_warns_and_four_cards_never_do():
    """2 * wrong >= taken holds at equality (wrong 2 of taken 4 warns), and fewer than LESSON_SUPPORT cards never warn
    however their actions stand: the cards after the third hold one right vote, so they add cards and no action."""
    def rowsets(cards):
        rows = [[acted("TAKEN_WRONG", "pr_mismatch", "f6" * 32, f"{DAY0}19:00:00Z", f"{DAY0}19:10:00Z", pr=811)],
                [acted("TAKEN_WRONG", "wrong_binding", "a7" * 32, f"{DAY0}19:20:00Z", f"{DAY0}19:30:00Z", pr=812)],
                [acted("TAKEN_CORRECT", None, "b8" * 32, f"{DAY0}19:40:00Z", f"{DAY0}19:50:00Z", pr=813)]]
        return rows + [[acted("TAKEN_CORRECT", None, "c9" * 32, f"{DAY0}20:0{k}:00Z", f"{DAY0}20:30:00Z", pr=814)]
                       for k in range(cards - 3)]
    five = ls.learn(*picked_cards(rowsets(5)))
    assert counts(five["lanes"][LANE]["lessons"]["A.md"]) == {"cards": 5, "taken": 4, "wrong": 2, "prs": 2}
    order, warnings = ls.lesson_order(five, LANE, ["A.md", "B.md", "C.md"])
    assert order == ["B.md", "C.md", "A.md"]
    assert warnings["A.md"].startswith("LEARN: on 5 cards where you picked this, wrong 2 of taken 4 actions, on 2 PRs "
                                       "(rows ")
    four = ls.learn(*picked_cards(rowsets(4)))
    assert counts(four["lanes"][LANE]["lessons"]["A.md"]) == {"cards": 4, "taken": 4, "wrong": 2, "prs": 2}
    assert ls.lesson_order(four, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})


def test_a_stale_action_is_taken_and_never_wrong():
    """TAKEN_STALE is a taken action (it counts in taken) and never a wrong one: two stale votes and one right vote
    under two wrong ones on two PRs stay below half."""
    rowsets = [[row("TAKEN_WRONG", "pr_mismatch", pr=821)], [row("TAKEN_WRONG", "wrong_binding", pr=822)],
               [row("TAKEN_STALE", pr=823)], [row("TAKEN_STALE", pr=824)], [row("TAKEN_CORRECT", pr=825)]]
    state = ls.learn(*picked_cards(rowsets))
    assert counts(state["lanes"][LANE]["lessons"]["A.md"]) == {"cards": 5, "taken": 5, "wrong": 2, "prs": 2}
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})


def test_the_cite_is_the_newest_wrong_actions_by_time_when_their_row_hashes_sort_the_other_way():
    """The cite orders the wrong actions by tape time, the newest first, never by row hash: the older wrong action
    moves minute by minute until its row hash sorts above the newer one's, where a hash order would cite it first."""
    def rowsets(minute):
        return [[acted("TAKEN_WRONG", "pr_mismatch", "d4" * 32, f"{DAY0}16:00:00Z", f"{DAY0}16:{minute:02d}:00Z",
                       pr=801)],
                [acted("TAKEN_WRONG", "wrong_binding", "e5" * 32, f"{DAY0}17:00:00Z", f"{DAY0}17:30:00Z", pr=802)]] + \
            [[acted("TAKEN_CORRECT", None, str(k) * 64, f"{DAY0}18:00:00Z", f"{DAY0}18:0{k}:00Z", pr=810 + k)]
             for k in (1, 2, 3)]
    for minute in range(1, 60):
        state = ls.learn(*picked_cards(rowsets(minute)))
        cite = state["lanes"][LANE]["lessons"]["A.md"]["cite"]
        if cite != sorted(cite, reverse=True):
            break
    else:
        pytest.fail("no minute put the row hashes against the times")
    taped = {entry["row_hash"]: entry for entry in state["lanes"][LANE]["tapes"]["rco_vote"]}
    assert [taped[h]["utc"][:16] for h in cite] == [f"{DAY0}17:30", f"{DAY0}16:{minute:02d}"]


def test_a_pr_that_is_not_an_int_proves_no_independence():
    rowsets = [[row("TAKEN_WRONG", "pr_mismatch", pr=pr)] for pr in (None, True, "8")] + \
        [[row("TAKEN_CORRECT", pr=900 + k)] for k in range(2)]
    state = ls.learn(*picked_cards(rowsets))
    assert tape(state)["wrong"] == 3 and state["lanes"][LANE]["lessons"]["A.md"]["prs"] == 0
    assert ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"]) == (["A.md", "B.md", "C.md"], {})


def test_v11_names_its_pr_rule_and_refuses_a_v1_state():
    assert ls.SCHEMA == "wd.mind-learn-state.v1.1" and ls.PARAMS["lesson_prs"] == ls.LESSON_PRS == 2
    old = {key: value for key, value in ls.learn([]).items() if key != "state_hash"}
    old["schema"] = "wd.mind-learn-state.v1"
    assert not ls.verify(dict(old, state_hash=ls.sha256_text(ls.canonical(old))))


# D20: the runner's input files (fold results and picks, notes and order state) ---------------------------------------

def _fold_inputs(tmp_path):
    results, remember = lesson_cards(["wrong", "wrong", "wrong", "correct", "correct"])
    paths = []
    for i, item in enumerate(results):
        paths.append(tmp_path / f"r{i}.json")
        paths[-1].write_text(json.dumps(item), encoding="utf-8")
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps({"picks": remember}), encoding="utf-8")
    return results, remember, paths, picks


def _fold_argv(paths, picks, state_path):
    return ["fold", "--results", *map(str, paths), "--picks", str(picks), "--out", str(state_path)]


@pytest.mark.parametrize("where", ["result_top", "result_row", "picks"])
def test_d20_a_fold_input_with_a_duplicate_key_is_refused_and_nothing_is_written(tmp_path, capsys, where):
    """R1: json.loads keeps the last of two equal keys, so the value a reader of the file sees first is not the one
    LEARN would fold. A runner input holding a duplicate key at any depth is refused with a fixed message."""
    results, remember, paths, picks = _fold_inputs(tmp_path)
    if where == "picks":
        picks.write_text('{"picks": [], "picks": ' + json.dumps(remember) + "}", encoding="utf-8")
    else:
        text = json.dumps(results[0])
        old, new = (("{", '{"schema": "other", ') if where == "result_top" else
                    ('"outcome": ', '"outcome": "TAKEN_CORRECT", "outcome": '))
        assert old in text
        paths[0].write_text(text.replace(old, new, 1), encoding="utf-8")
    state_path = tmp_path / "state.json"
    assert ls.main(_fold_argv(paths, picks, state_path)) == 2
    assert capsys.readouterr().err == "REFUSED: an input file has a duplicate key\n"
    assert not state_path.exists()


def test_d20_a_state_file_with_a_duplicate_key_is_refused_by_notes_and_order(tmp_path, capsys):
    results, remember, paths, picks = _fold_inputs(tmp_path)
    state_path = tmp_path / "state.json"
    assert ls.main(_fold_argv(paths, picks, state_path)) == 0
    capsys.readouterr()
    text = state_path.read_text(encoding="ascii")
    state_path.write_text('{"schema": "other", ' + text[1:], encoding="ascii")  # the real schema comes later and wins
    for argv in (["notes", "--state", str(state_path), "--lane", LANE],
                 ["order", "--state", str(state_path), "--lane", LANE, "--shown", "A.md", "B.md", "C.md"]):
        assert ls.main(argv) == 2
        assert capsys.readouterr().err == "REFUSED: an input file has a duplicate key\n"


@pytest.mark.parametrize("which", ["result", "picks", "state"])
def test_d20_a_runner_file_nested_too_deeply_is_a_fixed_refusal_never_a_raw_error(tmp_path, capsys, which):
    results, remember, paths, picks = _fold_inputs(tmp_path)
    deep = "[" * 200000 + "]" * 200000
    state_path = tmp_path / "state.json"
    if which == "state":
        state_path.write_text(deep, encoding="ascii")
        argv = ["notes", "--state", str(state_path), "--lane", LANE]
    else:
        (paths[0] if which == "result" else picks).write_text(deep, encoding="ascii")
        argv = _fold_argv(paths, picks, state_path)
    assert ls.main(argv) == 2
    assert capsys.readouterr().err == "REFUSED: an input file is unreadable\n"
    assert which == "state" or not state_path.exists()


@pytest.mark.parametrize("payload", ["[]", "null", '"picks"', "7"])
def test_d20_a_picks_file_that_is_not_an_object_is_a_fixed_refusal(tmp_path, capsys, payload):
    results, remember, paths, picks = _fold_inputs(tmp_path)
    picks.write_text(payload, encoding="ascii")
    state_path = tmp_path / "state.json"
    assert ls.main(_fold_argv(paths, picks, state_path)) == 2
    assert capsys.readouterr().err == "REFUSED: the picks file has no picks list\n"
    assert not state_path.exists()


def test_d20_fold_without_a_picks_file_folds_with_no_picks(tmp_path, capsys):
    results, remember, paths, picks = _fold_inputs(tmp_path)
    state_path = tmp_path / "state.json"
    assert ls.main(["fold", "--results", *map(str, paths), "--out", str(state_path)]) == 0
    assert json.loads(capsys.readouterr().out)["state_hash"] == ls.learn(results)["state_hash"]
    assert ls.learn(results)["state_hash"] != ls.learn(results, remember)["state_hash"]


STATE_PAYLOADS = {"list": "[]", "null": "null", "int": "7", "string": '"x"', "nan": "NaN", "overflow": "1e309",
                  "nan_inside": '{"schema": "@S", "state_hash": "@H", "x": NaN}',
                  "overflow_inside": '{"schema": "@S", "state_hash": "@H", "x": 1e309}'}


@pytest.mark.parametrize("name", sorted(STATE_PAYLOADS))
def test_d20_a_state_that_is_no_object_or_holds_a_non_finite_number_is_refused(tmp_path, capsys, name):
    """Grok's D20 challenge (advisory) named these two; verify already refused both. Pinned, not RED."""
    state_path = tmp_path / "state.json"
    state_path.write_text(STATE_PAYLOADS[name].replace("@S", ls.SCHEMA).replace("@H", "0" * 64), encoding="ascii")
    for argv in (["notes", "--state", str(state_path), "--lane", LANE],
                 ["order", "--state", str(state_path), "--lane", LANE, "--shown", "A.md", "B.md", "C.md"]):
        assert ls.main(argv) == 2
        assert capsys.readouterr().err == "REFUSED: the state file fails its own hash\n"


@pytest.mark.parametrize("how, message", [("list", "an input is not a card outcome result"),
                                          ("nan_inside", "an input holds a value JSON cannot hold")])
def test_d20_a_result_that_is_no_object_or_holds_nan_is_refused(tmp_path, capsys, how, message):
    results, remember, paths, picks = _fold_inputs(tmp_path)
    text = json.dumps(results[0])
    assert text.count('"gap": null') == 1
    paths[0].write_text("[]" if how == "list" else text.replace('"gap": null', '"gap": NaN'), encoding="utf-8")
    state_path = tmp_path / "state.json"
    assert ls.main(_fold_argv(paths, picks, state_path)) == 2
    assert capsys.readouterr().err == f"REFUSED: {message}\n"
    assert not state_path.exists()

@pytest.mark.parametrize("how", ["missing", "not_utf8", "not_json"])
def test_d20_a_runner_file_that_is_missing_not_utf8_or_not_json_is_a_fixed_refusal(tmp_path, capsys, how):
    """Pinned, not RED: _load refused these three before D20 too, but no test held them."""
    results, remember, paths, picks = _fold_inputs(tmp_path)
    if how == "missing":
        paths[0] = tmp_path / "absent.json"
    else:
        paths[0].write_bytes(b"\xff{}" if how == "not_utf8" else b'{"schema": ')
    state_path = tmp_path / "state.json"
    assert ls.main(_fold_argv(paths, picks, state_path)) == 2
    assert capsys.readouterr().err == "REFUSED: an input file is unreadable\n"
    assert not state_path.exists()
