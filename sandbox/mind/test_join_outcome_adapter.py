"""Tests of the JOIN outcome adapter v0.1 (contract JOIN_OUTCOME_ADAPTER.v0.1.md, marker v0.1-r3): outcome rows for
the JOIN card a lane actually read. Every turn here is written by the real ``join_turn.write_turn`` from the real
``join_turn.turn_case`` over synthetic cycles built from the card tests' own fixtures (``tjd.many``), into the test's
own ``.codex-audit`` directory; the rows are checked against RCO2's unchanged helpers called directly. Synthetic
stand-in, disclosed: ``inputs`` (what ``join_turn.turn`` fills from a verified frozen root) is a fixed synthetic object,
because no frozen root is read here. No real, frozen or sealed data, no bridge, network or gate code.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_join_outcome_adapter.py -q
     -p no:cacheprovider
"""

import copy
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import join_card_outcomes as jco  # noqa: E402  (claude-rco-2's tool, imported unchanged)
import join_outcome_adapter as ad  # noqa: E402
import join_turn as jt  # noqa: E402
import lane_view as lv  # noqa: E402
import test_join_card as tjc  # noqa: E402  (REMEMBER envelope fixtures)
import test_join_card_delivery as tjd  # noqa: E402  (real core + real lane view fixtures)
import test_join_turn as tjt  # noqa: E402  (a real LEARN v1 state)
import test_lane_view as tlv  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

AS_OF = tjc.AS_OF
TOOLS = "codex-tools-1"
LEARN = tjc.LEARN
ISSUED, READ = "2026-10-13T19:06:00Z", "2026-10-13T19:10:00Z"
VOTE_AT, EARLY = "2026-10-13T19:20:00Z", "2026-10-13T18:50:00Z"
SOURCE = {"source": "synthetic fixture", "source_sha256": hashlib.sha256(b"synthetic fixture").hexdigest()}
# disclosed stand-in for the frozen-root inputs that join_turn.turn fills; the adapter only checks their form
SYNTH_INPUTS = {"frozen_root": "synthetic", "manifest_sha256": hashlib.sha256(b"synthetic manifest").hexdigest(),
                "cycle_files": {}, "cycle_nulls": []}
FIXED = ("turn_artifact_unavailable", "input_unreadable", "turn_manifest_invalid", "turn_output_digest_mismatch",
         "envelope_binding_mismatch", "card_not_rendered_from_turn", "card_lane_or_as_of_mismatch",
         "see_items_ambiguous", "see_item_mismatch", "view_schema_invalid")  # literal: section 6 of the contract
RESULT_KEYS = {"schema", "authority", "scope", "adapter_contract_sha256", "base_contract_sha256", "lane", "inputs",
               "card", "exposure", "shown_k", "join_records", "hidden_prs", "rows", "rows_unavailable", "not_shown",
               "elided_by_view_cap", "receipt_reasons_seen", "counts", "gap"}
INPUT_KEYS = {"view_sha256", "card_sha256", "turn_manifest_sha256", "issue_sha256", "receipts_sha256",
              "coverage_sha256", "head_truth_sha256", "eligibility_sha256", "identity_sha256", "events", "turn_inputs",
              "turn_code_sha256"}
GATE = re.compile(r"(?i)consensus|changes_requested|rco_pass|auto_merge|rule12|autosign|receipt|merg|event_taxonomy")
CLOSURE = """
import sys
from pathlib import Path
repo = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(repo), str(repo / "sandbox" / "mind"), str(repo / "sandbox" / "mind" / "step1")]
import join_outcome_adapter
for module in list(sys.modules.values()):
    path = Path(getattr(module, "__file__", None) or "/").resolve()
    if path.is_relative_to(repo):
        print(path.relative_to(repo).as_posix())
"""
STEP1 = """
import json
import sys
from pathlib import Path
here, view_file = Path(sys.argv[1]), Path(sys.argv[2])
sys.path[:0] = [str(here), str(here / "step1")]
import join_card_outcomes as jco
import lane_view
view_bytes = view_file.read_bytes()
card = lane_view.render_text(json.loads(view_bytes), "codex-tools-1").encode("utf-8")
def once():
    return jco.result_bytes(jco.outcomes(view_bytes, card, "codex-tools-1", None, [], {}, [], [], []))
before = once()
assert "join_outcome_adapter" not in sys.modules
import join_outcome_adapter
after = once()
first = json.loads(before)
print(json.dumps({"same": before == after, "card": first["card"], "rows": len(first["rows"])}))
"""


# --- fixtures ---------------------------------------------------------------------------------------------------------

def record(index):
    """PR number, branch (the canonical task) and full head of ``tjd.many``'s record ``index``."""
    return 2000 + index, f"codex-lead-1/wait-{index}-20261013", hashlib.sha1(f"head {index}".encode()).hexdigest()


def many_case(count, as_of=AS_OF):
    """``tjd.many`` as a loaded cycle (the case dict ``join_turn.turn_case`` takes); same events and GitHub truth."""
    events, githubs = [], []
    for index in range(count):
        number, branch, head = record(index)
        events += [tlv.vote(f"2026-10-13T17:4{index}:00Z", "claude-rco-1", head=head, task=branch, number=number),
                   tlv.vote(f"2026-10-13T17:4{index}:01Z", "codex-lead-1", head=head, task=branch, number=number)]
        githubs.append(tlv.github_of(head=head, number=number, branch=branch, observed=tjd.shift(as_of, minutes=-5)))
    return {"cycle": "synthetic", "snapshot": tlv.snapshot_of(events, as_of, log_identity=tjd.COMPLETE),
            "github": tlv.merge_github(*githubs), "bodies": {event_id(row): row for row in events}, "as_of_utc": as_of}


LONG = chr(0x6F22) * jc.SUMMARY_CHARS
REF = "r" * (jc.REF_CHARS - 6)


def one_long_pick():
    """One long pick: 4 records render k = 2, 3 records still k = 3 (measured by probe_fixture.py)."""
    return tjc.bound(tjc.answer(tjc.pick(f"{REF}000.md", summary=LONG)))


def contested_picks():
    """Two long picks, the first contested: the size bound forces k = 1 for 3 and for 4 records."""
    first = tjc.pick(f"{REF}000.md", summary=LONG, contested_with=["c" * 80 + ".md"])
    return tjc.bound(tjc.answer(first, tjc.pick(f"{REF}001.md", summary=LONG)))


@pytest.fixture
def sandbox_root(tmp_path):
    root = tmp_path / ".codex-audit"
    root.mkdir()
    return root


def write(root, count=3, *, envelope=None, learn_state=None, lane=TOOLS, name="turn", inputs=SYNTH_INPUTS):
    result = jt.turn_case(lane, many_case(count), learn_ref=LEARN, envelope=envelope, learn_state=learn_state)
    result["inputs"] = copy.deepcopy(inputs)
    jt.write_turn(root / name, result)
    return root / name, result


def handmade(root, view, *, name="hand", envelope=None):
    """A self-consistent hand-made turn: the view edited, delivery and card re-made from it, digests recomputed by
    the real writer."""
    delivery = jcd.deliver(view, jt.NO_RECEIPTS, now_utc=view["as_of_utc"])
    text = jc.card(TOOLS, delivery, view, envelope, learn_ref=LEARN)
    result = {"cycle": "synthetic", "lane": TOOLS, "as_of_utc": view["as_of_utc"], "now_utc": delivery["now_utc"],
              "view": view, "delivery": delivery, "card": text, "receipts_given": False, "envelope": envelope,
              "learn": None, "learn_state_sha256": None, "inputs": copy.deepcopy(SYNTH_INPUTS)}
    jt.write_turn(root / name, result)
    return root / name


def digest(turn, name):
    return hashlib.sha256((turn / name).read_bytes()).hexdigest()


def manifest_of(turn):
    return json.loads((turn / "turn.json").read_text(encoding="utf-8"))


def put_manifest(turn, manifest):
    (turn / "turn.json").write_bytes(jt._json_text(manifest).encode("utf-8"))


def rewrite(turn, name, data, *, fix=True):
    """Replace one output of the test's own turn; ``fix`` recomputes its manifest digest so the next check is hit."""
    (turn / name).write_bytes(data)
    if fix:
        manifest = manifest_of(turn)
        manifest["outputs"][name] = hashlib.sha256(data).hexdigest()
        put_manifest(turn, manifest)


def issue_for(turn, **change):
    return dict({"schema": jco.ISSUE_SCHEMA, "lane": TOOLS, "card_sha256": digest(turn, "card.txt"),
                 "view_sha256": digest(turn, "view.json"), "nonce": "nonce-1", "issued_utc": ISSUED}, **change)


def receipt_for(turn, **change):
    return dict({"schema": jco.RECEIPT_SCHEMA, "lane": TOOLS, "card_sha256": digest(turn, "card.txt"),
                 "nonce": "nonce-1", "read_utc": READ, "reader": TOOLS}, **change)


def tools_vote(index, ts=VOTE_AT, number=None):
    pr, branch, head = record(index)
    return tlv.vote(ts, TOOLS, head=head, task=branch, number=pr if number is None else number)


def evidence(turn, votes=(), *, count=3, verified=True, intervals=True, **change):
    """RCO2's inputs for one turn: a valid issue and read receipt, complete coverage, head intervals, eligibility at
    each vote and verified identity for each vote."""
    events = {event_id(body): body for body in votes}
    truth = []
    for index in range(count):
        pr, _, head = record(index)
        if intervals:
            truth.append(dict({"kind": "interval", "pr": pr, "head": head, "from_utc": "2026-10-13T17:00:00Z",
                               "to_utc": "2026-10-14T00:00:00Z", "complete": True}, **SOURCE))
        else:
            truth += [dict({"kind": "sample", "pr": pr, "head": head, "observed_utc": moment}, **SOURCE)
                      for moment in ("2026-10-13T19:19:00Z", "2026-10-13T19:21:00Z")]
    found = dict(
        issue=issue_for(turn), receipts=[receipt_for(turn)], events=events,
        coverage=[dict({"from_utc": "2026-10-13T17:00:00Z", "to_utc": "2026-10-14T00:00:00Z", "complete": True},
                       **SOURCE)],
        head_truth=truth,
        eligibility=[dict({"pr": body["payload"]["pr"], "head": body["payload"]["head"], "slot": "build_tools",
                           "lane": TOOLS, "eligible": True, "observed_utc": body["ts_utc"]}, **SOURCE)
                     for body in votes],
        identity=[dict({"event_ref": ref, "agent": TOOLS, "verified": verified, "method": "synthetic"}, **SOURCE)
                  for ref in events])
    found.update(change)
    return found


def judge(turn, lane=TOOLS, **options):
    options.setdefault("learn_ref", LEARN)
    return ad.judge(turn, lane, **options)


def outcomes(result):
    return [row["outcome"] for row in result["rows"]]


def oracle_rows(turn, k, found):
    """RCO2's own helpers, called directly over the written view and card bytes (the expected rows)."""
    view = json.loads((turn / "view.json").read_bytes())
    card_sha, view_sha = digest(turn, "card.txt"), digest(turn, "view.json")
    read, reason, _ = jco.read_time(found["issue"], found["receipts"], TOOLS, card_sha, view_sha)
    issued = jco._time(found["issue"].get("issued_utc"))
    kept, _ = jco.dedupe(jco.event_pairs(found["events"]))
    lane_events = sorted((event for event in kept.values() if event["body"].get("agent") == TOOLS),
                         key=lambda event: (event["ts"], event["ref"]))
    return [jco.outcome_row(line, TOOLS, read, reason, issued, jco._time(view.get("as_of_utc")), lane_events,
                            found["coverage"], found["head_truth"], found["eligibility"], found["identity"])
            for line in jco.card_lines(view, TOOLS)[:k]]


def refused(result, reason):
    assert reason in FIXED
    assert result["card"] == f"refused: {reason}" and result["exposure"] == "UNKNOWN"
    assert result["shown_k"] is None and result["join_records"] is None and result["hidden_prs"] is None
    assert set(result) == RESULT_KEYS and result["not_shown"] == []
    return result


def copy_without(turn, missing, root):
    """A new turn directory holding every file of ``turn`` except ``missing`` (nothing is deleted)."""
    other = root / f"without-{missing.replace('.', '-')}"
    other.mkdir()
    for path in turn.iterdir():
        if path.name != missing:
            shutil.copyfile(path, other / path.name)
    return other


# --- the fixtures are the card tests' own -----------------------------------------------------------------------------

@pytest.mark.parametrize("count", [3, 4])
def test_the_case_fixture_is_tjd_many_as_a_loaded_cycle(count):
    case = many_case(count)
    view = lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc=case["as_of_utc"])
    assert view == tjd.many(count, AS_OF)


@pytest.mark.parametrize("envelope", [None, "one", "contested"])
def test_the_written_view_and_delivery_re_render_the_written_card_byte_for_byte(sandbox_root, envelope):
    env = {None: None, "one": one_long_pick(), "contested": contested_picks()}[envelope]
    turn, result = write(sandbox_root, envelope=env)
    view = json.loads((turn / "view.json").read_bytes())
    delivery = json.loads((turn / "delivery.json").read_bytes())
    again = jc.card(TOOLS, delivery, view, env, learn_ref=LEARN).encode("utf-8")
    assert again == (turn / "card.txt").read_bytes() == result["card"].encode("utf-8")


# --- acceptance A0-A5 -------------------------------------------------------------------------------------------------

def test_a0_three_shown_lines_and_zero_lane_events_are_none(sandbox_root):
    turn, _ = write(sandbox_root)
    found = evidence(turn)
    result = judge(turn, **found)
    assert result["card"] == "accepted" and result["exposure"] == "SHOWN_LINES_KNOWN"
    assert set(result) == RESULT_KEYS and set(result["inputs"]) == INPUT_KEYS
    assert (result["shown_k"], result["join_records"], result["hidden_prs"]) == (3, 3, 0)
    assert outcomes(result) == ["NONE", "NONE", "NONE"] and result["not_shown"] == []
    assert result["rows"] == oracle_rows(turn, 3, found)
    assert [row["task"] for row in result["rows"]] == [record(index)[1] for index in range(3)]
    assert result["rows_unavailable"] is None and result["elided_by_view_cap"] == 0
    assert result["receipt_reasons_seen"] == [] and result["gap"] is None
    assert result["counts"] == jco.counts(result["rows"])


def test_a1_one_valid_vote_on_line_1_is_taken_correct_and_the_others_other(sandbox_root):
    turn, _ = write(sandbox_root)
    found = evidence(turn, [tools_vote(0)])
    result = judge(turn, **found)
    assert result["card"] == "accepted"
    assert outcomes(result) == ["TAKEN_CORRECT", "OTHER", "OTHER"] and result["not_shown"] == []
    assert result["rows"] == oracle_rows(turn, 3, found)
    assert result["rows"][0]["read_to_action_ms"] == 600000
    assert result["counts"]["follow_through"] == {"numerator": 1, "denominator": 3}


def test_a2_k1_scores_line_1_only_and_keeps_the_hidden_lines_vote_as_a_lane_event(sandbox_root):
    turn, _ = write(sandbox_root, envelope=contested_picks())
    found = evidence(turn, [tools_vote(1)])
    result = judge(turn, remember=contested_picks(), **found)
    assert result["card"] == "accepted" and result["shown_k"] == 1
    assert [row["line"] for row in result["rows"]] == [1] and outcomes(result) == ["OTHER"]
    assert [row["line"] for row in result["not_shown"]] == [2, 3]
    assert [row["pr"] for row in result["not_shown"]] == [2001, 2002]
    assert result["hidden_prs"] == 2 and result["join_records"] == 3
    assert result["rows"] == oracle_rows(turn, 1, found)
    assert result["counts"]["outcomes"]["OTHER"] == 1 and sum(result["counts"]["outcomes"].values()) == 1


def test_a2_a_pass_vote_on_line_1s_task_naming_the_line_2_pr_is_taken_wrong(sandbox_root):
    turn, _ = write(sandbox_root, envelope=contested_picks())
    found = evidence(turn, [tools_vote(0, number=2001)])
    result = judge(turn, remember=contested_picks(), **found)
    assert outcomes(result) == ["TAKEN_WRONG"] and result["rows"][0]["reason"] == "pr_mismatch"
    assert result["rows"] == oracle_rows(turn, 1, found)


@pytest.mark.parametrize("envelope, k", [(None, 3), ("one", 2), ("contested", 1)])
def test_a3_four_records_count_hidden_prs_apart_from_the_view_cap(sandbox_root, envelope, k):
    env = {None: None, "one": one_long_pick(), "contested": contested_picks()}[envelope]
    turn, _ = write(sandbox_root, 4, envelope=env)
    result = judge(turn, remember=env, **evidence(turn, count=4))
    assert result["card"] == "accepted" and result["shown_k"] == k
    assert result["join_records"] == 4 and result["hidden_prs"] == 4 - k and len(result["rows"]) == k
    assert result["elided_by_view_cap"] == 1 and len(result["not_shown"]) == 3 - k
    assert sum(result["counts"]["outcomes"].values()) == k


def test_a4_a_vote_before_the_read_is_pre_read(sandbox_root):
    turn, _ = write(sandbox_root)
    found = evidence(turn, [tools_vote(0, ts=EARLY)])
    result = judge(turn, **found)
    assert outcomes(result)[0] == "PRE_READ" and result["rows"] == oracle_rows(turn, 3, found)


def test_a4_head_samples_alone_never_verify_currency(sandbox_root):
    turn, _ = write(sandbox_root)
    found = evidence(turn, [tools_vote(0)], intervals=False)
    row = judge(turn, **found)["rows"][0]
    assert (row["outcome"], row["reason"], row["head_sampled"]) == ("UNKNOWN", "head_currency_unverified",
                                                                    "SAMPLED_CONSISTENT")


def test_a4_an_unverified_identity_is_unknown(sandbox_root):
    turn, _ = write(sandbox_root)
    row = judge(turn, **evidence(turn, [tools_vote(0)], verified=False))["rows"][0]
    assert (row["outcome"], row["reason"]) == ("UNKNOWN", "identity_unverified")


def test_a5_the_step1_path_of_rco2s_tool_is_byte_identical_before_and_after_the_adapter_import(sandbox_root):
    turn, _ = write(sandbox_root)
    done = subprocess.run([sys.executable, "-B", "-c", STEP1, str(HERE), str(turn / "view.json")],
                          capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {"same": True, "card": "accepted", "rows": 3}


def test_no_issue_and_no_receipt_is_rco2s_historical_gap(sandbox_root):
    turn, _ = write(sandbox_root)
    result = judge(turn, **evidence(turn, issue=None, receipts=[]))
    assert outcomes(result) == ["UNKNOWN"] * 3 and result["gap"] == jco.HISTORICAL_GAP
    assert {row["reason"] for row in result["rows"]} == {"receipt_missing"}


def test_a_real_learn_state_turn_is_accepted_with_both_envelopes(sandbox_root):
    state = tjt.learn_state()
    turn, written = write(sandbox_root, envelope=tjc.bound(tjc.answer(tjc.pick("lesson_a.md"))), learn_state=state)
    assert written["learn"] is not None
    result = judge(turn, remember=written["envelope"], learn=written["learn"], **evidence(turn))
    assert result["card"] == "accepted" and outcomes(result) == ["NONE", "NONE", "NONE"]
    assert result["inputs"]["turn_code_sha256"] == manifest_of(turn)["code_sha256"]
    assert result["inputs"]["turn_inputs"] == SYNTH_INPUTS


def test_result_names_the_r3_contract_and_rco2s_base_contract(sandbox_root):
    turn, _ = write(sandbox_root)
    result = judge(turn, **evidence(turn))
    text = (HERE / "JOIN_OUTCOME_ADAPTER.v0.1.md").read_bytes().replace(b"\r\n", b"\n")
    assert b"version marker `v0.1-r3`" in text
    assert result["adapter_contract_sha256"] == ad.ADAPTER_CONTRACT_SHA256 == hashlib.sha256(text).hexdigest()
    assert result["base_contract_sha256"] == jco.CONTRACT_SHA256
    assert (result["schema"], result["authority"], result["scope"]) == ("wd.mind-join-outcome-adapter.v0.1", "none",
                                                                        "offline_outcome_rows_not_gate")
    assert result["inputs"]["card_sha256"] == digest(turn, "card.txt")
    assert result["inputs"]["view_sha256"] == digest(turn, "view.json")
    assert result["inputs"]["turn_manifest_sha256"] == digest(turn, "turn.json")


def test_the_same_inputs_in_another_order_give_the_same_result(sandbox_root):
    turn, _ = write(sandbox_root)
    votes = [tools_vote(0), tools_vote(1, ts="2026-10-13T19:30:00Z")]
    found = evidence(turn, votes)
    again = dict(found, events=[{"ref": ref, "body": body} for ref, body in reversed(list(found["events"].items()))],
                 identity=list(reversed(found["identity"])), eligibility=list(reversed(found["eligibility"])))
    assert jco.canonical(judge(turn, **found)) == jco.canonical(judge(turn, **again))


# --- negatives N1-N12 -------------------------------------------------------------------------------------------------

def test_n1_one_changed_card_byte_is_refused_and_the_verified_views_lines_stay_unknown(sandbox_root):
    turn, _ = write(sandbox_root)
    data = bytearray((turn / "card.txt").read_bytes())
    data[-2] = ord("X") if data[-2] != ord("X") else ord("Y")
    rewrite(turn, "card.txt", bytes(data))
    result = refused(judge(turn, **evidence(turn)), "card_not_rendered_from_turn")
    assert outcomes(result) == ["UNKNOWN"] * 3 and result["rows_unavailable"] is None
    assert {row["reason"] for row in result["rows"]} == {"card_not_rendered_from_turn"}


def test_n2_reordered_see_items_are_refused(sandbox_root):
    turn, _ = write(sandbox_root)
    lines = (turn / "card.txt").read_text(encoding="utf-8").split("\n")
    head, *items = lines[2].split("; ")
    lines[2] = "; ".join(["SEE " + items[0], head[len("SEE "):], *items[1:]])
    rewrite(turn, "card.txt", "\n".join(lines).encode("utf-8"))
    refused(judge(turn, **evidence(turn)), "card_not_rendered_from_turn")


def test_n3_a_hand_made_view_whose_action_order_differs_from_the_records_is_refused(sandbox_root):
    view = copy.deepcopy(tjd.many(3, AS_OF))
    actions = view["lanes"][TOOLS]["actions"]
    actions[0], actions[1] = actions[1], actions[0]
    result = refused(judge(handmade(sandbox_root, view), **evidence(sandbox_root / "hand")), "see_item_mismatch")
    assert outcomes(result) == ["UNKNOWN"] * 3


@pytest.mark.parametrize("index", [0, 1, 2])
def test_n3_a_hand_made_view_whose_shown_line_names_another_head12_is_refused(sandbox_root, index):
    """Every shown item is checked, not only the first (a partial SEE match is never accepted)."""
    view = copy.deepcopy(tjd.many(3, AS_OF))
    head12 = record(index)[2][:12]
    view["lanes"][TOOLS]["actions"][index] = view["lanes"][TOOLS]["actions"][index].replace(head12, "f" * 12, 1)
    assert [row["reason"] for row in jco.card_lines(view, TOOLS)].count("line_record_mismatch") == 1
    refused(judge(handmade(sandbox_root, view), **evidence(sandbox_root / "hand")), "see_item_mismatch")


def test_n3_fewer_action_texts_than_shown_items_is_refused(sandbox_root):
    view = copy.deepcopy(tjd.many(3, AS_OF))
    view["lanes"][TOOLS]["actions"] = view["lanes"][TOOLS]["actions"][:2]
    refused(judge(handmade(sandbox_root, view), **evidence(sandbox_root / "hand")), "see_item_mismatch")


def test_n4_a_card_of_another_lane_is_refused(sandbox_root):
    turn, _ = write(sandbox_root, lane="codex-lead-1")
    result = refused(judge(turn, **evidence(turn)), "card_lane_or_as_of_mismatch")
    assert result["lane"] == TOOLS


def test_n4_a_manifest_of_another_as_of_is_refused(sandbox_root):
    turn, _ = write(sandbox_root)
    put_manifest(turn, dict(manifest_of(turn), as_of_utc="2026-10-13T19:01:00Z"))
    refused(judge(turn, **evidence(turn)), "card_lane_or_as_of_mismatch")


def test_n4_a_card_whose_header_names_another_lane_is_refused(sandbox_root):
    turn, _ = write(sandbox_root)
    text = (turn / "card.txt").read_text(encoding="utf-8").replace(f"JOIN {TOOLS} ", "JOIN codex-lead-1 ", 1)
    rewrite(turn, "card.txt", text.encode("utf-8"))
    refused(judge(turn, **evidence(turn)), "card_lane_or_as_of_mismatch")


def _manifest_edits():
    keys = ["schema", "version", "authority", "scope", "cycle", "lane", "as_of_utc", "now_utc", "inputs",
            "receipts_given", "remember_envelope_sha256", "learn_state_sha256", "learn_envelope_sha256", "outputs",
            "code_sha256"]  # literal: the 15 keys of section 3
    edits = [(f"drop_{key}", lambda m, key=key: m.pop(key)) for key in keys]
    edits.append(("extra_key", lambda m: m.update(extra=None)))
    for key in ("schema", "version", "authority", "scope", "cycle", "lane", "as_of_utc", "now_utc"):
        edits += [(f"bool_{key}", lambda m, key=key: m.update({key: True})),
                  (f"int_{key}", lambda m, key=key: m.update({key: 1}))]
    edits += [(f"null_{key}", lambda m, key=key: m.update({key: None}))
              for key in ("schema", "version", "authority", "scope", "cycle", "inputs", "lane", "as_of_utc",
                          "now_utc", "receipts_given", "outputs", "code_sha256")]
    edits += [("schema_other", lambda m: m.update(schema="wd.mind-join-turn.v1")),
              ("version_other", lambda m: m.update(version="join-turn-v0.2")),
              ("authority_other", lambda m: m.update(authority="gate")),
              ("scope_other", lambda m: m.update(scope="a gate")),
              ("receipts_given_string", lambda m: m.update(receipts_given="false")),
              ("receipts_given_int", lambda m: m.update(receipts_given=0)),
              ("learn_state_without_envelope", lambda m: m.update(learn_state_sha256="a" * 64)),
              ("learn_envelope_without_state", lambda m: m.update(learn_envelope_sha256="a" * 64)),
              ("remember_not_hex", lambda m: m.update(remember_envelope_sha256="A" * 64)),
              ("inputs_extra", lambda m: m["inputs"].update(extra=1)),
              ("inputs_missing", lambda m: m["inputs"].pop("cycle_nulls")),
              ("inputs_manifest_not_hex", lambda m: m["inputs"].update(manifest_sha256="x")),
              ("inputs_files_list", lambda m: m["inputs"].update(cycle_files=[])),
              ("inputs_nulls_object", lambda m: m["inputs"].update(cycle_nulls={})),
              ("inputs_root_bool", lambda m: m["inputs"].update(frozen_root=True)),
              ("outputs_extra", lambda m: m["outputs"].update({"turn.json": "a" * 64})),
              ("outputs_missing", lambda m: m["outputs"].pop("delivery.json")),
              ("outputs_not_hex", lambda m: m["outputs"].update({"view.json": "z" * 64})),
              ("code_extra", lambda m: m["code_sha256"].update({"x.py": "a" * 64})),
              ("code_missing", lambda m: m["code_sha256"].pop(sorted(m["code_sha256"])[0])),
              ("code_not_hex", lambda m: m["code_sha256"].update({sorted(m["code_sha256"])[0]: 5}))]
    return edits


@pytest.mark.parametrize("name, edit", _manifest_edits(), ids=[name for name, _ in _manifest_edits()])
def test_n5_every_manifest_deviation_is_refused(sandbox_root, name, edit):
    turn, _ = write(sandbox_root)
    manifest = manifest_of(turn)
    edit(manifest)
    put_manifest(turn, manifest)
    result = refused(judge(turn, **evidence(turn)), "turn_manifest_invalid")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_n5_a_duplicate_manifest_key_is_refused(sandbox_root):
    turn, _ = write(sandbox_root)
    text = (turn / "turn.json").read_text(encoding="utf-8")
    (turn / "turn.json").write_bytes(text.replace('"authority": "none",', '"authority": "none", "authority": "none",',
                                                  1).encode("utf-8"))
    assert (turn / "turn.json").read_text(encoding="utf-8").count('"authority"') == 2
    refused(judge(turn, **evidence(turn)), "turn_manifest_invalid")


def test_n5_the_library_turn_without_frozen_inputs_is_refused(sandbox_root):
    turn, _ = write(sandbox_root, inputs=None)
    assert manifest_of(turn)["inputs"] is None
    refused(judge(turn, **evidence(turn)), "turn_manifest_invalid")


@pytest.mark.parametrize("name", ["view.json", "delivery.json", "card.txt"])
def test_n6_an_output_digest_mismatch_is_refused(sandbox_root, name):
    turn, _ = write(sandbox_root)
    rewrite(turn, name, (turn / name).read_bytes() + b"\n", fix=False)
    result = refused(judge(turn, **evidence(turn)), "turn_output_digest_mismatch")
    if name != "view.json":  # the view still matches its digest: its lines stay, UNKNOWN
        assert outcomes(result) == ["UNKNOWN"] * 3
    else:
        assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_n7_a_needed_remember_envelope_that_is_missing_is_unavailable(sandbox_root):
    turn, _ = write(sandbox_root, envelope=one_long_pick())
    refused(judge(turn, **evidence(turn)), "turn_artifact_unavailable")


def test_n7_an_envelope_given_for_a_null_digest_or_another_envelope_is_refused(sandbox_root):
    turn, _ = write(sandbox_root)
    refused(judge(turn, remember=one_long_pick(), **evidence(turn)), "envelope_binding_mismatch")
    refused(judge(turn, learn={"schema": "x"}, **evidence(turn)), "envelope_binding_mismatch")
    other, _ = write(sandbox_root, envelope=one_long_pick(), name="other")
    refused(judge(other, remember=contested_picks(), **evidence(other)), "envelope_binding_mismatch")


def test_n7_a_needed_learn_envelope_that_is_missing_or_another_is_refused(sandbox_root):
    turn, written = write(sandbox_root, learn_state=tjt.learn_state())
    refused(judge(turn, **evidence(turn)), "turn_artifact_unavailable")
    edited = dict(written["learn"], notes=[])
    refused(judge(turn, learn=edited, **evidence(turn)), "envelope_binding_mismatch")


def test_n8_a_view_of_another_schema_is_refused(sandbox_root):
    turn, _ = write(sandbox_root)
    view = json.loads((turn / "view.json").read_bytes())
    view["schema"] = "wd.mind-lane-view.v2"
    rewrite(turn, "view.json", jt._json_text(view).encode("utf-8"))
    result = refused(judge(turn, **evidence(turn)), "view_schema_invalid")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_n8_a_view_without_lane_texts_is_refused_never_a_raw_error(sandbox_root):
    turn, _ = write(sandbox_root)
    view = json.loads((turn / "view.json").read_bytes())
    view["lanes"][TOOLS] = {"actions": "not a list", "more": 0}
    rewrite(turn, "view.json", jt._json_text(view).encode("utf-8"))
    refused(judge(turn, **evidence(turn)), "view_schema_invalid")


# --- repair after the 20746232 freeze: Tools REPORT F98B145E and RCO1's bound I/O result (both reproduced here) ------

MARKER = chr(80) + "RIVATE" + "_MARKER"  # a guarded word, built at run time
ESCAPED = "\\u0050" + "RIVATE" + "_MARKER"  # escaped; the decoded guard reveals it


def edit_view(turn, edit):
    """Edit the written view, keep its manifest digest right (ASCII JSON, so a lone surrogate escape survives)."""
    view = json.loads((turn / "view.json").read_bytes())
    edit(view)
    rewrite(turn, "view.json", json.dumps(view, indent=1, sort_keys=True).encode("ascii"))


@pytest.mark.parametrize("action", [None, 7, ["a list"], "\ud800"], ids=["null", "int", "list", "lone_surrogate"])
def test_r1_an_action_text_rco2s_card_lines_cannot_read_is_view_schema_invalid_never_a_raw_error(sandbox_root,
                                                                                                    action):
    # Tools F98B145E: actions [null] passed view_ok, the card was refused and the refusal's card_lines raised
    turn, _ = write(sandbox_root)
    edit_view(turn, lambda view: view["lanes"][TOOLS]["actions"].__setitem__(0, action))
    result = refused(judge(turn, **evidence(turn)), "view_schema_invalid")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_r1_a_pr_record_whose_empty_slots_card_lines_cannot_read_is_view_schema_invalid(sandbox_root):
    turn, _ = write(sandbox_root)
    edit_view(turn, lambda view: [record.update(empty_slots=None) for record in view["prs"].values()])
    result = refused(judge(turn, **evidence(turn)), "view_schema_invalid")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_r1_a_malformed_view_with_a_pending_rco2_input_refusal_is_view_schema_invalid(sandbox_root):
    turn, _ = write(sandbox_root)
    edit_view(turn, lambda view: view["lanes"][TOOLS]["actions"].__setitem__(0, None))
    result = refused(judge(turn, **evidence(turn, receipts="not a list")), "view_schema_invalid")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_r1_a_refusal_raised_before_view_ok_does_not_re_enter_a_malformed_view(sandbox_root):
    turn, _ = write(sandbox_root)
    edit_view(turn, lambda view: view["lanes"][TOOLS]["actions"].__setitem__(0, None))
    found = evidence(turn)
    (turn / "card.txt").unlink()  # refused after view.json was read, before view_ok: the refusal re-reads the view
    result = refused(judge(turn, **found), "turn_artifact_unavailable")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


@pytest.mark.parametrize("word", [MARKER, ESCAPED], ids=["plain", "escaped"])
def test_r2_a_guarded_word_in_a_copied_manifest_field_is_input_unreadable_never_a_workspace_error(sandbox_root, word):
    # RCO1 lead-rco1-join-adapter-io-safety-20746232-20261018-v1, check C2: inputs.frozen_root copied into the result
    turn, _ = write(sandbox_root)
    manifest = manifest_of(turn)
    manifest["inputs"]["frozen_root"] += " " + word
    put_manifest(turn, manifest)
    result = refused(judge(turn, **evidence(turn)), "input_unreadable")
    ad.ws.guard_object(result)
    assert result["inputs"]["turn_inputs"] is None and result["inputs"]["turn_code_sha256"] is None
    assert result["rows_unavailable"] is None and len(result["rows"]) == 3
    assert {(row["outcome"], row["reason"]) for row in result["rows"]} == {(jco.UNKNOWN, "input_unreadable")}
    more = json.loads((turn / "view.json").read_bytes())["lanes"][TOOLS]["more"]
    assert result["counts"] == jco.counts(result["rows"]) and result["elided_by_view_cap"] == more


def test_r2_a_guarded_word_in_a_view_text_a_row_would_copy_withholds_the_rows_too(sandbox_root):
    turn, _ = write(sandbox_root)
    _, branch, _ = record(0)
    data = (turn / "view.json").read_text(encoding="utf-8").replace(branch, branch + MARKER)
    rewrite(turn, "view.json", data.encode("utf-8"))
    result = refused(judge(turn, **evidence(turn)), "input_unreadable")
    ad.ws.guard_object(result)
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"
    assert result["inputs"]["turn_inputs"] is None and result["elided_by_view_cap"] is None


def test_r2_the_cli_prints_the_fixed_refusal_for_a_guarded_manifest_field_and_writes_it_guard_clean(sandbox_root,
                                                                                                      capsys):
    turn, _ = write(sandbox_root)
    manifest = manifest_of(turn)
    manifest["inputs"]["frozen_root"] += " " + MARKER
    put_manifest(turn, manifest)
    out = sandbox_root / "result.json"
    assert _cli(turn, out) == 2
    printed = capsys.readouterr()
    assert printed.out.strip() == "refused: input_unreadable" and MARKER not in printed.out + printed.err
    written = json.loads(out.read_bytes())
    ad.ws.guard_object(written)
    assert written["card"] == "refused: input_unreadable" and written["inputs"]["turn_inputs"] is None


def test_n9_an_ambiguous_k_is_refused(sandbox_root, monkeypatch):
    """Inert substitute, disclosed: the real see_line never renders two budgets alike, so N9 cannot be reached with
    real inputs. ``see_line`` is patched to render limit 2 as limit 3; the card (k = 3) re-renders unchanged, and two
    budgets then match."""
    turn, _ = write(sandbox_root)
    real = jc.see_line
    monkeypatch.setattr(jc, "see_line", lambda view, slot, records, limit: real(view, slot, records,
                                                                                3 if limit == 2 else limit))
    refused(judge(turn, **evidence(turn)), "see_items_ambiguous")


def test_n10a_an_issue_naming_the_step1_cards_hash_is_issue_invalid(sandbox_root):
    turn, _ = write(sandbox_root)
    view = json.loads((turn / "view.json").read_bytes())
    step1 = hashlib.sha256(lv.render_text(view, TOOLS).encode("utf-8")).hexdigest()
    result = judge(turn, **evidence(turn, issue=issue_for(turn, card_sha256=step1)))
    assert result["card"] == "accepted" and outcomes(result) == ["UNKNOWN"] * 3
    assert {row["reason"] for row in result["rows"]} == {"issue_invalid"} and result["receipt_reasons_seen"] == []


def test_n10b_a_receipt_naming_the_step1_cards_hash_is_receipt_digest_mismatch(sandbox_root):
    turn, _ = write(sandbox_root)
    view = json.loads((turn / "view.json").read_bytes())
    step1 = hashlib.sha256(lv.render_text(view, TOOLS).encode("utf-8")).hexdigest()
    result = judge(turn, **evidence(turn, receipts=[receipt_for(turn, card_sha256=step1)]))
    assert outcomes(result) == ["UNKNOWN"] * 3
    assert {row["reason"] for row in result["rows"]} == {"receipt_digest_mismatch"}
    assert result["receipt_reasons_seen"] == ["receipt_digest_mismatch"]


def test_n10c_an_issue_without_issued_utc_is_issue_invalid_never_a_raw_error(sandbox_root):
    turn, _ = write(sandbox_root)
    issue = issue_for(turn)
    issue.pop("issued_utc")
    result = judge(turn, **evidence(turn, issue=issue))
    assert {row["reason"] for row in result["rows"]} == {"issue_invalid"} and result["receipt_reasons_seen"] == []


@pytest.mark.parametrize("data", [b"[" * 10000 + b"]" * 10000, b"not json", b'{"a": NaN}', b"\xff\xfe{}",
                                  b'{"a": 1, "a": 1}', "\ufeff{}".encode("utf-8")],
                         ids=["deep", "text", "nan", "not_utf8", "duplicate", "bom"])
@pytest.mark.parametrize("name", ["view.json", "delivery.json"])
def test_n11_unreadable_view_or_delivery_json_is_refused(sandbox_root, name, data):
    turn, _ = write(sandbox_root)
    rewrite(turn, name, data)
    result = refused(judge(turn, **evidence(turn)), "input_unreadable")
    if name == "view.json":
        assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"
    else:  # the view is verified: its lines stay, UNKNOWN
        assert outcomes(result) == ["UNKNOWN"] * 3


@pytest.mark.parametrize("data", [b"[" * 10000 + b"]" * 10000, b"not json", b"\xff"], ids=["deep", "text", "bytes"])
def test_n11_unreadable_turn_json_is_refused(sandbox_root, data):
    turn, _ = write(sandbox_root)
    (turn / "turn.json").write_bytes(data)
    refused(judge(turn, **evidence(turn)), "input_unreadable")


def test_n11_a_too_deep_remember_envelope_is_refused(sandbox_root):
    turn, _ = write(sandbox_root, envelope=one_long_pick())
    deep = []
    for _ in range(100000):
        deep = [deep]
    refused(judge(turn, remember={"answer": deep}, **evidence(turn)), "input_unreadable")


@pytest.mark.parametrize("missing", ["turn.json", "view.json", "delivery.json", "card.txt"])
def test_n12_each_absent_artifact_is_an_observable_refusal(sandbox_root, missing):
    turn, _ = write(sandbox_root)
    other = copy_without(turn, missing, sandbox_root)
    result = refused(judge(other, **evidence(turn)), "turn_artifact_unavailable")
    if missing in ("turn.json", "view.json"):
        assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_n12_an_absent_turn_directory_is_an_observable_refusal(sandbox_root):
    result = refused(judge(sandbox_root / "never-written", **{"issue": None}), "turn_artifact_unavailable")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


def test_n12_a_present_but_unreadable_file_is_input_unreadable(sandbox_root):
    turn, _ = write(sandbox_root)
    other = copy_without(turn, "card.txt", sandbox_root)
    (other / "card.txt").mkdir()
    refused(judge(other, **evidence(turn)), "input_unreadable")


def test_an_unknown_lane_is_an_observable_refusal(sandbox_root):
    turn, _ = write(sandbox_root)
    result = refused(judge(turn, lane="codex-lead-9", **evidence(turn)), "card_lane_or_as_of_mismatch")
    assert result["rows"] == [] and result["rows_unavailable"] == "view_unverified"


@pytest.mark.parametrize("field", ["receipts", "coverage", "head_truth", "eligibility", "identity", "events"])
def test_rco2_inputs_of_the_wrong_shape_are_refused_never_a_raw_error(sandbox_root, field):
    turn, _ = write(sandbox_root)
    refused(judge(turn, **evidence(turn, **{field: "not a list"})), "input_unreadable")


def test_every_result_accepted_or_refused_passes_the_decoded_guard(sandbox_root, monkeypatch):
    turn, _ = write(sandbox_root)
    seen, real = [], ad.ws.guard_object
    monkeypatch.setattr(ad.ws, "guard_object", lambda value: (seen.append(value), real(value))[1])
    accepted = judge(turn, **evidence(turn))
    refusal = judge(sandbox_root / "never-written", issue=None)
    assert any(item is accepted for item in seen) and any(item is refusal for item in seen)


# --- CLI and import closure -------------------------------------------------------------------------------------------

def _cli(turn, out, *extra, files=None):
    files = files or {}
    args = ["--turn", str(turn), "--lane", TOOLS, "--learn-ref", LEARN, "--out", str(out), *extra]
    for name, value in files.items():
        path = out.parent / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        args += [f"--{name.replace('_', '-')}", str(path)]
    return ad.main(args)


def test_cli_writes_the_result_bytes_once_and_refuses_with_exit_2(sandbox_root, capsys):
    turn, _ = write(sandbox_root)
    found = evidence(turn, [tools_vote(0)])
    out = sandbox_root / "result.json"
    assert _cli(turn, out, files=found) == 0
    assert out.read_bytes() == (jco.canonical(judge(turn, **found)) + "\n").encode("ascii")
    with pytest.raises(SystemExit):
        _cli(turn, out)  # never overwritten
    data = (turn / "card.txt").read_bytes()
    rewrite(turn, "card.txt", data[:-2] + (b"Y" if data[-2:-1] != b"Y" else b"Z") + data[-1:])
    assert _cli(turn, sandbox_root / "refused.json") == 2
    assert capsys.readouterr().out.strip().endswith("refused: card_not_rendered_from_turn")
    assert json.loads((sandbox_root / "refused.json").read_bytes())["exposure"] == "UNKNOWN"


def test_cli_refuses_a_duplicate_key_in_an_rco2_input_file(sandbox_root, capsys):
    turn, _ = write(sandbox_root)
    (sandbox_root / "issue.json").write_text('{"schema": "a", "schema": "a"}', encoding="utf-8")
    code = ad.main(["--turn", str(turn), "--lane", TOOLS, "--learn-ref", LEARN, "--out",
                    str(sandbox_root / "r.json"), "--issue", str(sandbox_root / "issue.json")])
    assert code == 2 and capsys.readouterr().out.strip().endswith("refused: input_unreadable")


def test_cli_refuses_an_output_outside_a_codex_audit_root(sandbox_root):
    turn, _ = write(sandbox_root)
    outside = jt.REPO / "never-written-adapter-output" / "r.json"  # no .codex-audit ancestor, also when the
    assert jcd.output_refusal(outside)  # test's own basetemp sits under one
    with pytest.raises(SystemExit):
        ad.main(["--turn", str(turn), "--lane", TOOLS, "--learn-ref", LEARN, "--out", str(outside)])
    assert not outside.parent.exists()


def test_the_import_closure_is_the_turn_closure_plus_rco2s_tool_and_no_gate_code():
    done = subprocess.run([sys.executable, "-B", "-c", CLOSURE, str(jt.REPO)], capture_output=True, text=True,
                          check=True)
    loaded = set(done.stdout.split())
    assert loaded - set(jt.CODE) == {"sandbox/mind/join_card_outcomes.py", "sandbox/mind/join_outcome_adapter.py"}
    assert not [name for name in loaded if GATE.search(name)]
