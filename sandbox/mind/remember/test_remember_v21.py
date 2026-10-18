"""Tests for REMEMBER v2.1 dev on synthetic data (no e5, no real memory, no sealed cases). Written BEFORE the code.

Opposite marks (``MAY_OPPOSE``): RCO1 measured that on fresh, independently worded pairs the true lesson is shown
with its opposite UNMARKED in 80/80 v2a observations. The detector design and its first table come from a direct Grok
xhigh consultation (answer sha256 050b9f27..., in fable-5's audit folder); fable-5 changed three rules where the table
and the pseudocode disagreed (each is named in the module docstring). The cosine is a fixture column, so a
similarity-only implementation cannot pass.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember -q -p no:cacheprovider
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import remember_v21 as r21  # noqa: E402

L = "lesson"

# (A, B, cosine, expected reason or None); Grok's table rows 1-22 in order.
OPPOSITE_TABLE = [
    ("retry the whole call on any transient error", "never retry a non-idempotent call; fail fast and report", 0.80,
     "polarity_flip"),
    ("retry the whole call on any transient error", "on a transient error, retry the entire call", 0.96, None),
    ("retry the whole call on any transient error", "pin the lockfile before every install", 0.70, None),
    ("prefer typed errors over string matching", "prefer string matching over typed errors", 0.94, "argument_swap"),
    ("prefer typed errors over string matching", "prefer typed errors to matching on strings", 0.95, None),
    ("never fail to validate inputs before a write", "validate inputs before a write", 0.93, None),
    ("never fail to validate inputs before a write", "do not validate inputs before a write", 0.91, "polarity_flip"),
    ("retry on any error", "retry only on a timeout error", 0.88, "quantifier_flip"),
    ("you should log request ids", "you must log request ids", 0.97, None),
    ("you may log request ids", "you must not log request ids", 0.90, "polarity_flip"),
    ("if the payload is not signed, reject the request", "reject the request when the signature check fails", 0.86,
     None),
    ("do not retry if the call is not idempotent", "retry the call when it is not idempotent", 0.84, "polarity_flip"),
    ("retry when the socket times out", "retry when the connection is reset", 0.92, None),
    ("\u00e4l\u00e4 yrit\u00e4 uudelleen koko kutsua", "retry the whole call", 0.72, "polarity_flip"),
    ("yrit\u00e4 uudelleen koko kutsua aina", "always retry the whole call", 0.72, None),
    ("enable response caching for GET requests", "disable response caching for GET requests", 0.94, "antonym_head"),
    ("do not enable response caching for GET requests", "disable response caching for GET requests", 0.94, None),
    ("never commit generated lockfiles", "always redact secrets from logs", 0.86, None),
    ("not only validate inputs but also validate outputs", "validate inputs before a write", 0.90, None),
    ("trust the client-supplied content type", "never trust a content type supplied by the client", 0.94,
     "polarity_flip"),
    ("always validate inputs before writing the response", "always write the response before validating inputs", 0.93,
     "argument_swap"),
    ("prefer retry over abort for an idempotent GET", "never abort an idempotent GET", 0.90, None),
]


def reason(a, b, cosine, kind_a=L, kind_b=L):
    mark = r21.opposition(a, b, kind_a, kind_b, cosine)
    return None if mark is None else mark["reason"]


@pytest.mark.parametrize("row", range(len(OPPOSITE_TABLE)))
def test_opposite_table(row):
    a, b, cosine, expected = OPPOSITE_TABLE[row]
    assert reason(a, b, cosine) == expected


@pytest.mark.parametrize("row", range(len(OPPOSITE_TABLE)))
def test_opposite_is_symmetric(row):
    a, b, cosine, expected = OPPOSITE_TABLE[row]
    assert reason(b, a, cosine) == expected


def test_cosine_backfill_only_at_or_above_its_threshold():
    a, b = "retry when the socket times out", "never retry when the connection resets"
    assert reason(a, b, 0.91) == "cosine_backfill"
    assert reason(a, b, 0.89) is None


def test_a_bare_event_never_opposes_a_lesson():
    for cosine in (0.5, 0.95, 0.99):
        assert reason("the agent retried the call", "never retry the call", cosine, kind_a="event") is None


def test_prefer_conflict_when_the_other_line_rejects_the_preferred_arm():
    assert reason("prefer retry over abort for an idempotent GET", "never retry an idempotent GET", 0.90) \
        == "prefer_conflict"


def test_the_mark_explains_itself_and_never_names_a_winner():
    mark = r21.opposition(*OPPOSITE_TABLE[0][:2], L, L, 0.80)
    assert set(mark) == {"reason", "cues", "shared", "ov", "pol"}
    assert "never" in mark["cues"] and {"retry", "call"} <= set(mark["shared"])
    assert mark["pol"] == "+1/-1" and 0.0 < mark["ov"] <= 1.0
    text = r21.mark_text("lesson:b", mark)
    assert text.startswith("[MAY_OPPOSE lesson:b reason=polarity_flip")
    assert "right" not in text and "wrong" not in text and "verdict" not in text


def test_non_and_un_prefixes_are_not_negations():
    assert reason("retry a non-idempotent call", "retry an unsafe call", 0.90) is None


def test_a_negation_inside_a_condition_does_not_flip_the_directive():
    assert reason("reject the request if it is not signed", "reject the request when it is unsigned", 0.90) is None


def test_double_negation_is_agreement():
    assert reason("do not avoid retrying the call", "retry the call", 0.90) is None
    assert reason("avoid retrying the call", "retry the call", 0.90) == "polarity_flip"


# --- the engine on a built synthetic memory ----------------------------------------------------------------------------

rv = r21.v2
sys.path.insert(0, str(HERE.parent / "step2"))
import test_step2 as t2  # noqa: E402  (step-2 fixtures: FakeEncoder, lesson, event, write_inputs)

WHEN = "2026-10-14T12:00:00Z"
SITUATION = "a transient error on the call: should the lane retry the whole call"
TRUE = "retry the whole call on any transient error"
OPPOSITE = "never retry a non-idempotent call; fail fast and report"


def build(tmp_path, rows):
    corpus_dir, vectors_dir = t2.write_inputs(tmp_path, sorted(rows, key=lambda row: row["record_id"]))
    memory_dir = tmp_path / "memory"
    rv.memory_mod.build(corpus_dir, vectors_dir, memory_dir)
    return memory_dir


def q4(memory_dir):
    return rv.q4_mod.Q4(rv.memory_mod.Memory(memory_dir), t2.FakeEncoder())


@pytest.fixture()
def pair_memory(tmp_path):
    return build(tmp_path, t2.records() + [t2.lesson("lesson_retry_true", TRUE),
                                           t2.lesson("lesson_retry_opposite", OPPOSITE)])


def test_v21_marks_the_opposite_that_v2_left_unmarked(pair_memory):
    queries = [SITUATION]
    v2_lines = {line["ref"]: line for line in rv.RememberV2(q4(pair_memory)).shortlist(SITUATION, WHEN, queries)["lines"]}
    assert {"lesson_retry_true.md", "lesson_retry_opposite.md"} <= set(v2_lines)  # both shown by v2 ...
    assert all("may_oppose" not in line for line in v2_lines.values())  # ... and nothing says they disagree
    listed = r21.RememberV21(q4(pair_memory), "v21a").shortlist(SITUATION, WHEN, queries)
    lines = {line["ref"]: line for line in listed["lines"]}
    true, opposite = lines["lesson_retry_true.md"], lines["lesson_retry_opposite.md"]
    assert [m["ref"] for m in true["may_oppose"]] == ["lesson_retry_opposite.md"]
    assert [m["ref"] for m in opposite["may_oppose"]] == ["lesson_retry_true.md"]
    assert true["may_oppose"][0]["reason"] == "polarity_flip"
    text = r21.render_shortlist("C1", SITUATION, listed)
    assert "[MAY_OPPOSE lesson_retry_opposite.md reason=polarity_flip" in text and r21.MARK_GLOSS in text
    assert listed["telemetry"]["may_oppose_pairs"] == 1


def test_the_arm_is_checked_from_its_queries_not_only_declared(pair_memory):
    a, ca = r21.RememberV21(q4(pair_memory), "v21a"), r21.RememberV21(q4(pair_memory), "v21ca")
    assert a.shortlist(SITUATION, WHEN, [SITUATION, "a rewrite"])["reason"] == "queries_do_not_match_arm"
    assert ca.shortlist(SITUATION, WHEN, [SITUATION])["reason"] == "queries_do_not_match_arm"
    assert ca.shortlist(SITUATION, WHEN, ["another situation", "a rewrite"])["reason"] == "queries_do_not_match_arm"
    assert ca.shortlist(SITUATION, WHEN, [SITUATION, "retry the whole call on transient errors"])["status"] == "LISTED"
    with pytest.raises(r21.RememberV21Error):
        r21.RememberV21(q4(pair_memory), "v2c")


def test_an_answer_names_its_arm_and_version_and_keeps_the_marks_of_its_picks(pair_memory):
    engine = r21.RememberV21(q4(pair_memory), "v21ca")
    listed = engine.shortlist(SITUATION, WHEN, [SITUATION, "retry the whole call on transient errors"])
    number = 1 + [line["ref"] for line in listed["lines"]].index("lesson_retry_true.md")
    answer = engine.apply_picks(listed, {"picks": [number], "nothing_applies": False})
    assert answer["status"] == "ANSWER" and answer["arm"] == "v21ca" and answer["version"] == r21.VERSION
    assert [m["ref"] for m in answer["results"][0]["may_oppose"]] == ["lesson_retry_opposite.md"]
    abstain = engine.apply_picks(listed, {"picks": [], "nothing_applies": True})
    assert abstain["status"] == "ABSTAIN" and abstain["arm"] == "v21ca"


def test_marks_never_break_the_byte_cap_and_groups_stay_whole(tmp_path):
    rows = t2.records() + [t2.lesson(f"lesson_retry_{i}", (TRUE if i % 2 else OPPOSITE) + f" variant {i} " + "x" * 90)
                           for i in range(12)]
    listed = r21.RememberV21(q4(build(tmp_path, rows)), "v21a").shortlist(SITUATION, WHEN, [SITUATION])
    assert r21.rendered_bytes(listed["lines"]) <= rv.CASE_BYTES and len(listed["lines"]) <= rv.CAP
    shown = {line["ref"] for line in listed["lines"]}
    for line in listed["lines"]:
        assert all(m["ref"] in shown for m in line.get("may_oppose", []))  # a mark never names a dropped line


def test_the_runner_writes_both_arms_and_composes_answers(pair_memory, tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    (out / "blind.jsonl").write_text(json.dumps({"id": "C1", "situation": SITUATION, "time": WHEN}) + "\n",
                                     encoding="utf-8")
    rewrites = tmp_path / "rewrites.json"
    rewrites.write_text(json.dumps({"rewrites": [{"id": "C1", "line": "retry the whole call on transient errors"}]}),
                        encoding="utf-8")

    def load(memory_dir, arm):
        return r21.RememberV21(q4(memory_dir), arm)
    assert r21.cmd_shortlists(argparse.Namespace(memory=pair_memory, rewrites=rewrites, out=out), load=load) == 0
    listed = json.loads((out / "v21a" / "shortlists.jsonl").read_text(encoding="utf-8").splitlines()[0])["listed"]
    number = 1 + [line["ref"] for line in listed["lines"]].index("lesson_retry_true.md")
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps({"cases": [{"id": "C1", "picks": [number], "nothing_applies": False}]}),
                     encoding="utf-8")
    args = argparse.Namespace(memory=pair_memory, picks_v21a=picks, picks_v21ca=None, out=out)
    assert r21.cmd_compose(args, load=load) == 0
    row = json.loads((out / "v21a" / "outputs.jsonl").read_text(encoding="utf-8"))
    assert row["status"] == "ANSWER" and row["results"][0]["ref"] == "lesson_retry_true.md"
    manifest = json.loads((out / "shortlists-v21-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == r21.VERSION and manifest["constants"]["flood_min"] == r21.FLOOD_MIN
    judge = (out / "v21a" / "judge-input.md").read_text(encoding="utf-8")
    assert "[MAY_OPPOSE lesson_retry_opposite.md" in judge and (out / "v21ca" / "judge-input.md").exists()


def test_unrelated_shown_lines_get_no_mark(tmp_path):
    listed = r21.RememberV21(q4(build(tmp_path, t2.records())), "v21a").shortlist(
        "watcher retry reasons narrowed again in pr review", WHEN, ["watcher retry reasons narrowed again in pr review"])
    assert listed["status"] == "LISTED"
    assert all(line["may_oppose"] == [] for line in listed["lines"])


# --- the date fence: forged-date floods (Grok round B, answer sha256 3580b6f3...; its 16 cases are the spec) ----------

def fr(rid, kind, identity, batch, available, claimed, score, text=None):
    """One fence row; a date without a clock time is UTC midnight."""
    def utc(day):
        return day if "T" in day else day + "T00:00:00Z"
    return {"id": rid, "kind": kind, "identity": identity, "batch": batch, "available": utc(available),
            "claimed": utc(claimed), "score": score, "text": text if text is not None else f"text of {rid}"}


def fenced(rows):
    out = r21.fence(rows, group="g")
    return out["header"], [(rid, out["marks"][rid]) for rid in out["show"]]


DU, BD, CAA, FL = "DATE_UNVERIFIED", "BACKDATED", "CLAIMED_AFTER_AVAILABLE", "FLOOD"
FENCE_CASES = {
    1: ([fr("T", "event", "alice", "B0", "2024-06-01", "2024-05-20", 0.70, "true"),
         fr("F1", "event", "mallory", "B1", "2024-08-01", "2019-01-01", 0.95),
         fr("F2", "event", "mallory", "B1", "2024-08-01", "2024-08-01", 0.94),
         fr("F3", "event", "mallory", "B1", "2024-08-01", "2024-08-01", 0.93)],
        "[TOPIC g: 1 trusted, 2024-05-20..2024-05-20; +1 untrusted] FLOOD 3 from writer mallory", [("T", [])]),
    2: ([fr("OLD", "lesson", "lessons/a", "B0", "2024-06-01", "2020-01-01", 0.60),
         fr("NEW", "lesson", "lessons/a", "B0", "2024-06-01", "2024-05-01", 0.90)],
        "[TOPIC g: 2 trusted, 2020-01-01..2024-05-01 DATE_UNVERIFIED]", [("OLD", [DU]), ("NEW", [DU])]),
    3: ([fr("b", "lesson", "p/b", "B0", "2024-06-01", "2022-01-01", 0.50),
         fr("a", "lesson", "p/a", "B0", "2024-06-01", "2022-01-01", 0.50),
         fr("c", "lesson", "p/c", "B0", "2024-06-01", "2023-01-01", 0.40)],
        "[TOPIC g: 3 trusted, 2022-01-01..2023-01-01 DATE_UNVERIFIED]", [("a", [DU]), ("c", [DU])]),
    4: ([fr("T", "lesson", "p/t", "B0", "2024-06-01", "2024-06-01", 0.80),
         fr("FUT", "lesson", "p/f", "B1", "2024-07-01", "2025-01-01", 0.99)],
        "[TOPIC g: 1 trusted, 2024-06-01..2024-06-01; +1 untrusted]", [("T", []), ("FUT", [CAA])]),
    5: ([fr("T", "lesson", "p/t", "B0", "2024-06-01", "2024-05-20", 0.70, "true"),
         fr("S1", "lesson", "p/x", "B1", "2024-08-01", "2019-01-01", 0.99, "dup"),
         fr("S2", "lesson", "p/y", "B1", "2024-08-01", "2024-08-01", 0.98, "dup"),
         fr("S3", "lesson", "p/z", "B1", "2024-08-01", "2024-12-01", 0.97, "dup")],
        "[TOPIC g: 1 trusted, 2024-05-20..2024-05-20; +1 untrusted] FLOOD 3 from 3 paths", [("T", [])]),
    6: ([fr("OLD", "lesson", "p/old", "B0", "2023-01-10", "2023-01-10", 0.75),
         fr("NEW", "lesson", "p/new", "B1", "2024-02-02", "2024-02-02", 0.85)],
        "[TOPIC g: 2 trusted, 2023-01-10..2024-02-02]", [("OLD", []), ("NEW", [])]),
    7: ([fr("T", "lesson", "p/t", "B0", "2024-06-01", "2024-06-01", 0.80),
         fr("BD", "lesson", "p/bd", "B1", "2024-08-01", "2019-01-01", 0.99)],
        "[TOPIC g: 1 trusted, 2024-06-01..2024-06-01; +1 untrusted]", [("T", []), ("BD", [BD])]),
    8: ([fr("T", "lesson", "p/a", "B0", "2024-06-01", "2024-06-01", 0.80),
         fr("E", "lesson", "p/a", "B1", "2024-07-01", "2018-01-01", 0.70),
         fr("M", "lesson", "p/a", "B2", "2024-08-01", "2024-08-01", 0.70),
         fr("L", "lesson", "p/a", "B3", "2024-09-01", "2024-09-01", 0.70)],
        "[TOPIC g: 3 trusted, 2024-06-01..2024-09-01; +1 untrusted]", [("T", []), ("E", [BD]), ("L", [])]),
    9: ([fr("E", "lesson", "p/e", "B0", "2024-01-01", "2024-01-01", 0.50),
         fr("MID", "lesson", "p/m", "B1", "2024-02-01", "2024-02-01", 0.90),
         fr("L", "lesson", "p/l", "B2", "2024-03-01", "2024-03-01", 0.50)],
        "[TOPIC g: 3 trusted, 2024-01-01..2024-03-01]", [("E", []), ("MID", []), ("L", [])]),
    10: ([fr("E", "lesson", "p/e", "B0", "2024-01-01", "2024-01-01", 0.40),
          fr("LOW", "lesson", "p/low", "B1", "2024-02-01", "2024-02-01", 0.30),
          fr("MID", "lesson", "p/m", "B2", "2024-03-01", "2024-03-01", 0.95),
          fr("L", "lesson", "p/l", "B3", "2024-04-01", "2024-04-01", 0.40)],
         "[TOPIC g: 4 trusted, 2024-01-01..2024-04-01]", [("E", []), ("MID", []), ("L", [])]),
    11: ([fr("F1", "lesson", "lessons/a", "B0", "2024-06-01", "2010-01-01", 0.50),
          fr("F2", "lesson", "lessons/a", "B0", "2024-06-01", "2020-06-01", 0.90),
          fr("F3", "lesson", "lessons/a", "B0", "2024-06-01", "2024-05-01", 0.40)],
         "[TOPIC g: 0 trusted, untrusted 2010-01-01..2024-05-01; +1 untrusted] FLOOD 3 from path lessons/a",
         [("F2", [FL, DU])]),
    12: ([fr("OLD", "lesson", "p/a", "B0", "2024-06-01", "2020-01-01", 0.60),
          fr("NEW", "lesson", "p/a", "B0", "2024-06-01", "2024-05-28", 0.85),
          fr("F1", "lesson", "p/a", "B1", "2024-08-01", "1990-01-01", 0.99),
          fr("F2", "lesson", "p/a", "B1", "2024-08-01", "2024-08-01", 0.98),
          fr("F3", "lesson", "p/a", "B1", "2024-08-01", "2030-01-01", 0.97)],
         "[TOPIC g: 2 trusted, 2020-01-01..2024-05-28 DATE_UNVERIFIED; +1 untrusted] FLOOD 3 from path p/a",
         [("OLD", [DU]), ("NEW", [])]),
    13: ([fr("ONLY", "lesson", "p/o", "B0", "2024-06-01", "2015-03-03", 0.77)],
         "[TOPIC g: 1 trusted, 2015-03-03..2015-03-03 DATE_UNVERIFIED]", [("ONLY", [DU])]),
    14: ([fr("NEW", "lesson", "p/new", "B0", "2024-06-01T00:00:00Z", "2024-05-01", 0.90),
          fr("OLD", "lesson", "p/old", "B1", "2024-06-01T00:10:00Z", "2019-04-04", 0.60)],
         "[TOPIC g: 2 trusted, 2019-04-04..2024-05-01 DATE_UNVERIFIED]", [("OLD", [DU]), ("NEW", [DU])]),
    15: ([fr("T", "lesson", "p/t", "B0", "2024-06-01", "2024-06-01", 0.80, "true"),
          fr("D1", "lesson", "p/a", "B1", "2024-08-02", "2019-01-01", 0.99, "dup"),
          fr("D2", "lesson", "p/a", "B1", "2024-08-02", "2024-08-02", 0.98, "dup")],
         "[TOPIC g: 1 trusted, 2024-06-01..2024-06-01; +1 untrusted]", [("T", []), ("D1", [BD])]),
    16: ([fr("T", "lesson", "p/t", "B0", "2024-06-01", "2022-06-01", 0.70),
          fr("A", "lesson", "p/a", "B0", "2024-06-01", "2010-01-01", 0.95),
          fr("C", "lesson", "p/c", "B0", "2024-06-01", "2024-05-01", 0.94)],
         "[TOPIC g: 3 trusted, 2010-01-01..2024-05-01 DATE_UNVERIFIED]", [("A", [DU]), ("C", [DU])]),
}


@pytest.mark.parametrize("case", sorted(FENCE_CASES))
def test_date_fence_case(case):
    rows, header, lines = FENCE_CASES[case]
    assert fenced(rows) == (header, lines)
    assert fenced(list(reversed(rows))) == (header, lines)  # the input order never matters


def test_date_fence_pinned_residual_is_stated_not_hidden():
    """Case 16: three same-wave notes from three paths can still bracket a middle note (a stated miss)."""
    out = r21.fence(FENCE_CASES[16][0], group="g")
    assert "T" not in out["show"] and out["n"] == 3 and out["u"] == 0


def test_date_fence_counts_never_inflate():
    out = r21.fence(FENCE_CASES[5][0], group="g")
    assert (out["n"], out["u"], out["flood"]) == (1, 1, 3)
    out = r21.fence(FENCE_CASES[15][0], group="g")
    assert (out["n"], out["u"], out["flood"]) == (1, 1, 0)


def test_date_fence_refuses_a_malformed_row():
    good = fr("T", "lesson", "p/t", "B0", "2024-06-01", "2024-06-01", 0.80)
    for change in ({"available": "yesterday"}, {"score": float("nan")}, {"kind": "note"}, {"id": ""}):
        with pytest.raises(r21.RememberV21Error):
            r21.fence([dict(good, **change)], group="g")
    with pytest.raises(r21.RememberV21Error):
        r21.fence([good, dict(good)], group="g")  # a duplicate id
    with pytest.raises(r21.RememberV21Error):
        r21.fence([], group="g")


TRUE_SUMMARY = "decoded guard json string escaped marker raw text regex"


def forged_memory(tmp_path, forged):
    """The true lesson (claimed 2026-10-10, written 2026-10-12T10) and forged near-duplicates written 26 h later,
    so in a later wave (a wave starts after a gap of more than 24 h)."""
    true = t2.lesson("lesson_alpha", TRUE_SUMMARY)
    true.update(time_utc="2026-10-10T00:00:00.000000Z")
    rows = [row for row in t2.records() if row["provenance"].get("file") != "lesson_json.md"] + [true]
    for i, claimed in enumerate(forged):
        row = t2.lesson(f"lesson_forged_{i}", TRUE_SUMMARY, mtime="2026-10-13T12:00:00.000000Z")
        row.update(time_utc=claimed)
        rows.append(row)
    return build(tmp_path, rows)


FORGED = ["2019-01-01T00:00:00.000000Z", "2026-10-13T00:00:00.000000Z", "2026-10-13T00:00:00.000000Z"]


def test_a_forged_date_flood_no_longer_hides_the_true_lesson(tmp_path):
    memory_dir = forged_memory(tmp_path, FORGED)
    queries = ["the decoded guard json string escaped marker regex"]
    v2_refs = [line["ref"] for line in rv.RememberV2(q4(memory_dir)).shortlist(queries[0], WHEN, queries)["lines"]]
    assert "lesson_alpha.md" not in v2_refs  # the defect: the forged early and late notes take the slots
    listed = r21.RememberV21(q4(memory_dir), "v21a").shortlist(queries[0], WHEN, queries)
    lines = {line["ref"]: line for line in listed["lines"]}
    assert "lesson_alpha.md" in lines and not any(ref.startswith("lesson_forged_") for ref in lines)
    header = lines["lesson_alpha.md"]["topic"]["header"]
    assert header.endswith("FLOOD 3 from path (memory dir)") and "2019" not in header and "1 trusted" in header
    text = r21.render_shortlist("C1", queries[0], listed)
    assert header in text and r21.FENCE_GLOSS in text


def test_two_forged_notes_below_the_flood_size_are_marked_and_cannot_take_the_earliest_slot(tmp_path):
    memory_dir = forged_memory(tmp_path, FORGED[:2])
    queries = ["the decoded guard json string escaped marker regex"]
    listed = r21.RememberV21(q4(memory_dir), "v21a").shortlist(queries[0], WHEN, queries)
    lines = {line["ref"]: line for line in listed["lines"]}
    assert "lesson_alpha.md" in lines
    backdated = [ref for ref, line in lines.items() if "BACKDATED" in line["date_marks"]]
    assert backdated == ["lesson_forged_0.md"]
    assert "FLOOD" not in lines["lesson_alpha.md"]["topic"]["header"]
