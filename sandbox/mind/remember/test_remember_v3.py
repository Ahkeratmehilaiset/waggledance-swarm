"""Tests for the REMEMBER v3 dev prototype on synthetic data (no e5, no F: data). Memories are built by the frozen
step-2 code from the step-2 fixtures, as in test_remember_v2.py. Disclosure: the module was written before these
tests. Each behavioural test therefore carries its own twin on the unchanged v2 behaviour (global lexical, hop off,
WHEN off), and the twin must differ.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember -q -p no:cacheprovider
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import remember_v3 as r3  # noqa: E402  (first: through remember_v2 it puts the step-2 modules in place)

rv = r3.v2
sys.path.insert(0, str(HERE.parent / "step2"))
import test_step2 as t2  # noqa: E402  (step-2 fixtures: FakeEncoder, lesson, event, write_inputs)

EARLY = "2026-10-12T10:00:00.000000Z"
LATE = "2026-10-13T20:00:00.000000Z"  # after SITUATION_TIME, before the fixture cutoff
SITUATION_TIME = "2026-10-13T12:00:00Z"


class PassageEncoder(t2.FakeEncoder):
    """The step-2 fake encoder plus the passages call that build_when uses."""

    def passages(self, texts):
        return np.vstack([self.vector(text) for text in texts]).astype("<f4")


def note(name, summary, body, mtime=EARLY):
    text = f"---\nname: {name}\ndescription: {summary}\n---\n\n{body}"
    row = t2.lesson(name, summary, mtime)
    row.update(record_id=f"lesson:{name}@{hashlib.sha256(text.encode()).hexdigest()[:12]}", text=text,
               stored_text=text)
    return row


def build(tmp_path, rows):
    corpus_dir, vectors_dir = t2.write_inputs(tmp_path, sorted(rows, key=lambda row: row["record_id"]))
    memory_dir = tmp_path / "memory"
    rv.memory_mod.build(corpus_dir, vectors_dir, memory_dir)
    return memory_dir


def q4_of(memory_dir):
    return rv.q4_mod.Q4(rv.memory_mod.Memory(memory_dir), PassageEncoder())


def by_id(engine, index, scores):
    return {engine.q4.ids[int(p)]: float(s) for p, s in zip(index, scores)}


def linked_rows(extra=()):
    a = note("cold_reboot", "cold reboot of a lane", "A cold reboot of a lane loses its cursor; event 4beec3445bb7 "
             "has the trace.")
    b = note("planned_restart", "planned lane restart", "A planned lane restart drops the zeppelinium marker; see "
             "4BEEC344 for it.")
    return t2.records() + [a, b] + list(extra)


# --- per-situation lexical -------------------------------------------------------------------------------------------

def test_without_hop_the_situation_lexical_equals_a_fresh_step2_lexical_over_the_same_texts(tmp_path):
    engine = r3.RememberV3(q4_of(build(tmp_path, linked_rows())))
    index, _ = engine.applicable("which watcher lost its cursor", SITUATION_TIME)
    texts = [engine.q4.entries[int(p)]["text"] for p in index]
    fresh = rv.q4_mod.Lexical(texts, engine.q4.rule["bm25_k1"], engine.q4.rule["bm25_b"])
    for query in ("cold reboot cursor", "zeppelinium", "json digest escaping powershell", "nothing shared here zz"):
        assert np.array_equal(engine.lexical(index).scores(query), fresh.scores(query))


def test_a_record_not_yet_available_never_moves_a_lexical_score(tmp_path):
    late = note("late_flood", "watchdog watchdog", "watchdog " * 80 + "a long later note about the watchdog", LATE)
    with_late = r3.RememberV3(q4_of(build(tmp_path / "a", linked_rows([late]))))
    without = r3.RememberV3(q4_of(build(tmp_path / "b", linked_rows())))
    situation, query = "the watchdog cursor", "watchdog cursor reboot"
    index_a, _ = with_late.applicable(situation, SITUATION_TIME)
    index_b, _ = without.applicable(situation, SITUATION_TIME)
    assert len(index_a) == len(index_b) and len(with_late.q4.ids) == len(without.q4.ids) + 1
    v3_a = by_id(with_late, index_a, with_late.lexical(index_a).scores(query))
    v3_b = by_id(without, index_b, without.lexical(index_b).scores(query))
    assert v3_a == v3_b  # v3: the later note is invisible to the statistics
    v2_a = by_id(with_late, index_a, with_late.q4.lexical.scores(query)[index_a])
    v2_b = by_id(without, index_b, without.q4.lexical.scores(query)[index_b])
    assert v2_a != v2_b  # twin: v2's global lexical lets the later note move earlier records' scores


# --- the hop -------------------------------------------------------------------------------------------------------

def test_the_hop_lets_a_lesson_be_found_by_its_linked_lessons_rare_word(tmp_path):
    engine = r3.RememberV3(q4_of(build(tmp_path, linked_rows())))
    index, _ = engine.applicable("which lane lost its marker", SITUATION_TIME)
    cold = next(p for p in index if engine.q4.ids[int(p)].startswith("lesson:cold_reboot@"))
    local = list(index).index(cold)
    assert engine.lexical(index).scores("zeppelinium")[local] == 0.0  # twin: hop off, no shared word
    engine.set_arm("v3h")
    assert engine.lexical(index).scores("zeppelinium")[local] > 0.0
    taken = engine.last_borrowed[int(cold)]
    donor = next(p for p in index if engine.q4.ids[int(p)].startswith("lesson:planned_restart@"))
    assert "zeppelinium" in taken and len(taken) <= r3.HOP_TERMS
    assert set(taken) <= set(engine.counts[int(donor)]) and not set(taken) & set(engine.counts[int(cold)])


def test_a_reference_cited_by_more_than_four_lessons_links_nothing(tmp_path):
    crowd = [note(f"crowd_{i}", f"crowd note {i}", f"crowd note {i} cites 4beec344 and word{i}x") for i in range(3)]
    four = r3.RememberV3(q4_of(build(tmp_path / "four", linked_rows(crowd[:2]))), hop=True)
    five = r3.RememberV3(q4_of(build(tmp_path / "five", linked_rows(crowd))), hop=True)
    for engine, expect_links in ((four, True), (five, False)):
        index, _ = engine.applicable("which lane lost its marker", SITUATION_TIME)
        engine.lexical(index)
        assert bool(engine.last_borrowed) is expect_links


def test_an_inapplicable_donor_lends_nothing(tmp_path):
    late_b = note("planned_restart", "planned lane restart", "A planned lane restart drops the zeppelinium marker; "
                  "see 4BEEC344 for it.", LATE)
    rows = [row for row in linked_rows() if not row["record_id"].startswith("lesson:planned_restart@")] + [late_b]
    engine = r3.RememberV3(q4_of(build(tmp_path, rows)), hop=True)
    index, _ = engine.applicable("which lane lost its marker", SITUATION_TIME)
    engine.lexical(index)
    assert engine.last_borrowed == {}  # the only other citer is not available yet, so the reference has degree 1
    late_engine_index, _ = engine.applicable("which lane lost its marker", "2026-10-13T23:00:00Z")
    engine.lexical(late_engine_index)
    assert engine.last_borrowed  # twin: once the donor is available, the link exists


def test_events_never_lend_or_borrow(tmp_path):
    rows = linked_rows([t2.event("2026-10-13T08:30:00.000000Z", "quasarite seen near 4beec344")])
    engine = r3.RememberV3(q4_of(build(tmp_path, rows)), hop=True)
    index, _ = engine.applicable("which lane lost its marker", SITUATION_TIME)
    engine.lexical(index)
    events = {int(p) for p in index if not engine.lesson[int(p)]}
    assert events and not events & set(engine.last_borrowed)
    assert not any("quasarite" in taken for taken in engine.last_borrowed.values())


# --- the WHEN view -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("intro\n\n**How to apply:** check the exit code first.\n\n**Why:** it lied.", "**How to apply:** check the exit "
     "code first."),
    ("intro\nHow to apply: a\n- b\n\nRelated: [[x]]", "How to apply: a\n- b"),
    ("intro only, no section", None),
    ("**How to apply:** " + "y" * 900, "**How to apply:** " + "y" * (r3.WHEN_CHARS - len("**How to apply:** "))),
    ("text that mentions how to apply in a sentence", None),
])
def test_when_section(text, expected):
    assert r3.when_section(text) == expected


def when_rows():
    hidden = note("hidden_trigger", "a note about colours", "A long story about colours and gardens.\n\n**How to "
                  "apply:** when the lighthouse beacon flickers, recheck the tide table.")
    return t2.records() + [hidden]


def test_build_and_read_when_round_trip_and_refuse_a_changed_input(tmp_path):
    q4 = q4_of(build(tmp_path, when_rows()))
    manifest = r3.build_when(q4, "m" * 64, tmp_path / "when")
    assert [row["id"] for row in manifest["records"]] == sorted(row["id"] for row in manifest["records"])
    vectors, mask = r3.read_when(q4, "m" * 64, tmp_path / "when")
    hidden = q4.ids.index(next(i for i in q4.ids if i.startswith("lesson:hidden_trigger@")))
    assert mask[hidden] and mask.sum() == len(manifest["records"]) and vectors.shape == q4.d.shape
    with pytest.raises(r3.RememberV3Error):
        r3.read_when(q4, "n" * 64, tmp_path / "when")  # another memory
    body = (tmp_path / "when" / "when-vectors.f32").read_bytes()
    (tmp_path / "when" / "when-vectors.f32").write_bytes(body[:-1] + bytes([body[-1] ^ 1]))
    with pytest.raises(r3.RememberV3Error):
        r3.read_when(q4, "m" * 64, tmp_path / "when")  # changed vectors


def test_the_when_view_is_max_pooled_into_the_summary_view_and_adds_no_line(tmp_path):
    q4 = q4_of(build(tmp_path, when_rows()))
    r3.build_when(q4, "m" * 64, tmp_path / "when")
    when = r3.read_when(q4, "m" * 64, tmp_path / "when")
    plain, pooled = r3.RememberV3(q4), r3.RememberV3(q4, when=when)
    situation = "the lighthouse beacon flickers again"
    index, _ = plain.applicable(situation, SITUATION_TIME)
    hidden = list(index).index(next(p for p in index if q4.ids[int(p)].startswith("lesson:hidden_trigger@")))
    assert pooled.ranks(situation, index)["d"][hidden] < plain.ranks(situation, index)["d"][hidden]  # twin: WHEN off
    listed = pooled.shortlist(situation, SITUATION_TIME, [situation])
    refs = [line["ref"] for line in listed["lines"]]
    assert len(refs) == len(set(refs)) and len(refs) <= rv.CAP and rv.rendered_bytes(listed["lines"]) <= rv.CASE_BYTES


def test_set_arm_switches_and_refuses_an_unknown_arm_or_missing_vectors(tmp_path):
    engine = r3.RememberV3(q4_of(build(tmp_path, linked_rows())))
    engine.set_arm("v3h")
    assert engine.hop and engine.when_vectors is None
    with pytest.raises(r3.RememberV3Error):
        engine.set_arm("v3w")
    with pytest.raises(r3.RememberV3Error):
        engine.set_arm("v9")
