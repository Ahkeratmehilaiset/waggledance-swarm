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


def test_a_result_that_is_not_an_outcome_result_is_refused():
    bad = result([row("TAKEN_CORRECT")])
    for change in ({"schema": "x"}, {"authority": "gate"}, {"lane": "Not A Lane!"}, {"inputs": {"card_sha256": "z"}}):
        with pytest.raises(ls.LearnError):
            ls.learn([dict(bad, **change)])


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
        results.append(result([row(*outcome)], card=card))
        remember.append(picks(card))
    return results, remember


def test_a_lesson_followed_by_wrong_actions_is_warned_and_moved_last_never_promoted():
    results, remember = lesson_cards(["wrong", "wrong", "wrong", "correct", "correct"])
    state = ls.learn(results, remember)
    order, warnings = ls.lesson_order(state, LANE, ["A.md", "B.md", "C.md"])
    assert order == ["B.md", "C.md", "A.md"]
    assert warnings["A.md"].startswith("LEARN: after you picked this, the card was wrong 3 of support 5 (rows ")
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
        "LEARN: after you picked this, the card was wrong 5 of support 5")


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


def test_slots_map_to_two_action_kinds_and_kinds_never_mix():
    rows = [row("TAKEN_WRONG", "pr_mismatch", slot="build_lead") for _ in range(5)] + \
        [row("NONE", slot="build_lead") for _ in range(3)]
    state = ls.learn(cards(rows))
    assert tape(state, kind="build_vote")["wrong"] == 5 and tape(state)["known"] == 0
    assert notes(state)[0].startswith("LEARN: similar build_vote wrong 5 of taken 5")
    rotated = ls.notes(state, LANE, card="d" * 64)
    assert sorted(rotated) == sorted(notes(state))
