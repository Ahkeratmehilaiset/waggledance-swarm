"""Tests for the REMEMBER reachability and crowding audit on synthetic data (no e5, no real memory, no sealed cases).
Written BEFORE the code, against a skeleton.

RCO2 round 4 part B (proposal in the RCO2 session audit folder, NOT run by RCO2) asks, per lesson, whether a situation
that asks for what the lesson teaches reaches it on the card, and if not, who crowds it out; round 10 orders that audit
first, read-only, on the current memory, by a Medium non-author. fable-5 builds the tool and tests it here only on
synthetic memories. Each fixture is a measured precondition (explore_d8*.py in fable-5's audit folder): the engines'
own cards over a memory REBUILT with the lesson's probe span removed were measured before the assertions were written,
so no expected status comes from the audit's own code. Round x1 measured with test_step2's hashed encoder in 4096
dimensions, where each memory wrote about 1.26 MB, too much for the audit folder's byte budget; so the encoder became a
collision-free vocabulary (DIM 512), each memory is built once per run, and rounds x2 and x3 measured every fixture
again before the first run (x3: the sort test's 8-versus-6 and tie fixtures and the event crowder, added so that the
crowding order differs from the ref order). After RED run 1 and before any code, three text-only probe tests were
added (a CRLF copy of the first, sentence ends, several sections), so RED run 2 ran the whole file again. After GREEN
run 1, every line marked "added after GREEN 1" (or 2) was written to tell apart a planned mutant the suite could not
(rounds x4 and x5 measured their fixtures; x4's first x_text design left queue_drain first on w, so x5 measured a
second).

D8.1 (Lead's D8 reconciliation: a card that lists nothing was counted as unreachable): every line marked "D8.1" was
written before the code change and ran RED against the D8 code; its fixtures were measured first (round x2 of the D8.1
audit folder; its round x1 measured nothing).

run:C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember -q -p no:cacheprovider
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import reach_audit as ra  # noqa: E402

v2 = ra.v2
sys.path.insert(0, str(HERE.parent / "step2"))
import test_step2 as t2  # noqa: E402  (step-2 fixtures: CUTOFF, event, encode_mod)

WHEN = "2026-10-14T12:00:00Z"
DIM = 512
VOCABULARY = {}  # token -> its own dimension, first seen first, shared by every encoder and memory of a run
GUARDED = "P" + "RIVATE" + "_MARKER"  # built at run time, never typed whole
PROBE = "Wait for the queue to drain before the retry."
FIRST = "Never retry a rebalance while the queue still drains."
DRAIN = "drain the queue fully before a retry of a shard rebalance"
PROBE_B = "Pin every cache key when you rotate a signing token."
FIRST_B = "Rotate the signing token only after every cache key is pinned."
PIN = "pin each cache key and rotate the signing token later"
TOPIC = [f"zq{k:03d}" for k in range(200)]  # synthetic topic words: no probe word


class Encoder:
    """A bag of words with ONE dimension per distinct token (VOCABULARY), so no two words ever share a dimension and a
    cosine is exactly the word overlap of two texts, whatever order the tokens were first seen in; DIM only bounds the
    vocabulary, and an overflow fails loudly. (test_step2 hashes words into buckets instead; that needed 4096
    dimensions here, and each memory then wrote about 1.26 MB.) It counts the passages it encodes."""

    def __init__(self):
        self.passages = 0

    def vector(self, text):
        out = np.zeros(DIM, dtype=np.float32)
        for word in ra.q4_mod.tokens(text):
            index = VOCABULARY.setdefault(word, len(VOCABULARY))
            if index >= DIM:
                raise AssertionError("the fixture vocabulary outgrew DIM")
            out[index] += 1.0
        norm = np.linalg.norm(out)
        return (out / norm if norm else np.ones(DIM, dtype=np.float32) / np.sqrt(DIM)).astype("<f4")

    def query(self, text):
        return self.vector(text)

    def passage(self, text):
        self.passages += 1
        return self.vector(text)


def lesson(stem, description, how="", why="", day=9, hour=10, tail="", name=None, cut=False):
    """A lesson note as the step-2 corpus stores it: frontmatter (name, description), the description again as the
    first body line, then its Why and How-to-apply sections and ``tail``; written (mtime) 2026-10-<day> <hour>:00."""
    name = name or stem.replace("_", "-")
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n"
    if why:
        text += f"\n**Why:** {why}\n"
    if how:
        text += f"\n**How to apply:** {how}\n"
    text += tail
    return {"record_id": f"lesson:{stem}@{hashlib.sha256(text.encode()).hexdigest()[:12]}", "kind": "lesson",
            "intent": "lesson", "time_utc": f"2026-10-{day:02d}T00:00:00.000000Z", "text": text,
            "summary": description, "stored_text": text, "meta": {"kind": "lesson", "file": f"{stem}.md"},
            "provenance": {"source_kind": "memory_note", "file": f"{stem}.md",
                           "mtime_utc": f"2026-10-{day:02d}T{hour:02d}:00:00.000000Z", "source_sha256": "0" * 64,
                           "used_sha256": "0" * 64, "transform": None, "stored_text_cut_by_guard": cut}}


FILLERS = [lesson(f"filler_{k}", f"unrelated note {k} on colours gardens menus") for k in range(4)]
EVENT = t2.event("2026-10-13T08:00:00.000000Z", "system cpu memory status health uptime disk process")
EVENT_CROWD = t2.event("2026-10-13T09:00:00.000000Z", "wait for the queue drain to settle, then the retry goes before")


def drain(**extra):
    return lesson("queue_drain", DRAIN, how=f"{FIRST} {PROBE}", **extra)


def crowders(count, start=0):
    """``count`` lessons, each on its own topic (four topic words), that hold every word of PROBE in another order."""
    return [lesson(f"crowd_{k:02d}", " ".join(TOPIC[4 * k:4 * k + 4]) + " wait for the queue drain to settle, then the"
                   " retry goes before") for k in range(start, start + count)]


def pin_family(count):
    """PROBE_B's lesson and ``count`` lessons on their own topics that hold every word of PROBE_B in another order."""
    return [lesson("cache_pin", PIN, how=f"{FIRST_B} {PROBE_B}")] + [
        lesson(f"pin_{k:02d}", " ".join(TOPIC[100 + 4 * k:104 + 4 * k]) + " rotate a signing token when you pin"
               " every cache key") for k in range(count)]


def reachable_rows():
    return FILLERS + [EVENT, drain()]


def crowded_rows(count):
    return reachable_rows() + crowders(count)


def build(root, rows):
    """A step-2 memory of ``rows`` with this module's encoder (test_step2.write_inputs, in DIM dimensions)."""
    rows = sorted(rows, key=lambda row: row["record_id"])
    corpus_dir, vectors_dir = root / "corpus", root / "vectors"
    corpus_dir.mkdir(parents=True)
    vectors_dir.mkdir(parents=True)
    body = "".join(ra.corpus_mod.canonical(row) + "\n" for row in rows).encode("utf-8")
    (corpus_dir / "corpus.jsonl").write_bytes(body)
    manifest = {"cutoff_utc": t2.CUTOFF, "records": len(rows), "corpus_sha256": hashlib.sha256(body).hexdigest(),
                "record_ids_sha256": hashlib.sha256("\n".join(r["record_id"] for r in rows).encode()).hexdigest()}
    (corpus_dir / "corpus-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    encoder = Encoder()
    vectors = np.vstack([np.vstack([encoder.vector(row[t2.encode_mod.VIEW_FIELD[view]]) for row in rows])
                         for view in t2.encode_mod.VIEWS]).astype("<f4")
    raw = vectors.tobytes()
    (vectors_dir / "vectors.f32").write_bytes(raw)
    (vectors_dir / "vectors-manifest.json").write_text(json.dumps({
        "views": list(t2.encode_mod.VIEWS), "count": len(rows), "rows": 2 * len(rows), "dim": DIM,
        "corpus_sha256": manifest["corpus_sha256"], "record_ids_sha256": manifest["record_ids_sha256"],
        "vectors_sha256": hashlib.sha256(raw).hexdigest(), "encoder": {"model": "fake", "dim": DIM}}),
        encoding="utf-8")
    memory = root / "memory"
    v2.memory_mod.build(corpus_dir, vectors_dir, memory)
    return memory


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    """``memory(rows)`` is the memory of ``rows``, built ONCE per run of this module (a build writes its vectors three
    times, and every run's generated bytes count against a budget); each test loads its own Q4 from it."""
    built = {}

    def get(rows):
        rows = sorted(rows, key=lambda row: row["record_id"])
        key = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        if key not in built:
            built[key] = build(tmp_path_factory.mktemp("memory"), rows)
        return built[key]
    return get


def load(memory):
    return v2.q4_mod.Q4(v2.memory_mod.Memory(memory), Encoder())


def position_of(q4, file):
    return next(i for i, entry in enumerate(q4.entries) if entry["row"]["provenance"].get("file") == file)


def masked_text(q4, file):
    text = q4.entries[position_of(q4, file)]["text"]
    probe = ra.probe_of(text, file)
    return text[:probe["start"]] + text[probe["end"]:]


def replaced(rows, file, text):
    """``rows`` with ``file``'s stored text replaced by ``text`` (same record id, so the same order)."""
    return [dict(row, text=text, stored_text=text) if row["meta"].get("file") == file else row for row in rows]


def run(memory, rows, arm="v21a", when=WHEN):
    return ra.audit(load(memory(rows)), arm, when, Encoder().passage)


def row_of(report, ref):
    return next(row for row in report["rows"] if row["ref"] == ref)


# --- the probe -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_the_probe_is_the_last_qualifying_how_to_apply_sentence_with_its_exact_span(newline):
    text = drain()["text"].replace("\n", newline)
    probe = ra.probe_of(text, "queue_drain.md")
    assert probe["probe"] == PROBE and text[probe["start"]:probe["end"]] == PROBE
    assert probe["qualifying"] == 2 and probe["single"] is False
    assert probe["sha256"] == hashlib.sha256(PROBE.encode("utf-8")).hexdigest() and probe["words"] == 9


def test_a_probe_never_names_its_lesson_links_or_files_and_has_enough_words():
    how = ("Apply self_ref whenever the gate trips hard. Follow [[other-note]] for every detail here. Cite notes.md in "
           "the final reply always. Use the SELF-REF checklist before each merge. Too short now.")
    assert ra.probe_of(lesson("self_ref", "a plain description", how=how)["text"], "self_ref.md") is None
    single = ra.probe_of(lesson("self_ref", "a plain description", how=how + " Keep the queue warm overnight.")["text"],
                         "self_ref.md")
    assert single["probe"] == "Keep the queue warm overnight." and single["qualifying"] == 1 and single["single"]
    assert ra.MIN_PROBE_WORDS == 4


@pytest.mark.parametrize("text", [
    lesson("plain", "a description with no sections at all")["text"],
    lesson("why_only", "a description", why="Because the queue drains slowly at night.")["text"],
    "---\nname: fm\ndescription: **How to apply:** Wait for the queue to drain first.\n---\n\nbody without sections\n",
    "---\nname: fm\ndescription: a note\n**How to apply:** Wait for the queue to drain first.\n---\n\nno sections\n",
])
def test_no_section_in_the_body_means_no_probe(text):
    assert ra.probe_of(text, "plain.md") is None


def test_the_section_ends_at_the_next_marker_and_markers_ignore_case():
    text = lesson("cased", "a description", tail="\n**how to apply:** Pin the cache key before you rotate.\n"
                  "\n**Why:** The rotation broke three lanes last week.\n")["text"]
    probe = ra.probe_of(text, "cased.md")
    assert probe["probe"] == "Pin the cache key before you rotate." and probe["qualifying"] == 1


def test_a_probe_spanning_lines_is_one_sentence_with_its_spaces_collapsed():
    raw = "Wait for the queue\nto drain before   the retry."
    text = lesson("lines", "a description", how=raw)["text"]
    probe = ra.probe_of(text, "lines.md")
    assert probe["probe"] == PROBE and text[probe["start"]:probe["end"]] == raw


def test_a_stop_a_blank_line_or_a_list_item_ends_a_sentence_and_the_item_mark_is_not_in_the_probe():
    how = ("Never deploy on a Friday evening! Keep the cache warm before a deploy\n\n"
           "Pin every key before the rotation\n- Rotate tokens after the pin\n")
    text = lesson("items", "a description", how=how)["text"]
    probe = ra.probe_of(text, "items.md")
    assert probe["probe"] == "Rotate tokens after the pin" and probe["qualifying"] == 4
    assert text[probe["start"]:probe["end"]] == "Rotate tokens after the pin"


def test_every_how_to_apply_section_counts_and_the_last_qualifying_sentence_wins():
    text = lesson("two", "a description", how="Drain the queue before the retry.",
                  tail="\n**Why:** It broke twice.\n\n**How to apply:** Pin the cache key before you rotate.\n")["text"]
    probe = ra.probe_of(text, "two.md")
    assert probe["probe"] == "Pin the cache key before you rotate." and probe["qualifying"] == 2


# --- the masked view -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("arm", sorted(ra.ENGINES))
def test_the_masked_view_equals_a_memory_rebuilt_without_the_probe(memory, arm):
    rows = crowded_rows(4)
    base = load(memory(rows))
    position = position_of(base, "queue_drain.md")
    text = masked_text(base, "queue_drain.md")
    view = ra.masked_q4(base, position, text, Encoder().passage)
    other = load(memory(replaced(rows, "queue_drain.md", text)))
    assert view.ids == other.ids and [e["text"] for e in view.entries] == [e["text"] for e in other.entries]
    assert np.array_equal(view.w, other.w) and np.array_equal(view.d, other.d)
    # Added after GREEN 1, the last three: a span the cut removed, a needle that is not on word bounds, and exactly
    # half of the 8-word shingles, each of which tells a one-record self-match apart from rule v1's own.
    for situation in (PROBE, FIRST, DRAIN, "queue fully before", "unrelated note on colours gardens menus",
                      "wait for the queue to drain", "rain the queue fully before",
                      "Never retry a rebalance while the queue still waits"):
        assert np.array_equal(view.lexical.scores(situation), other.lexical.scores(situation))
        assert np.array_equal(view._self_matches(situation), other._self_matches(situation))
    make = ra.ENGINES[arm][0]
    assert make(view).shortlist(PROBE, WHEN, [PROBE]) == make(other).shortlist(PROBE, WHEN, [PROBE])  # D12: no clock


def test_the_retained_w_row_is_kept_when_the_window_sees_the_same_text(memory):
    base = load(memory(reachable_rows()))
    position = position_of(base, "queue_drain.md")
    probe = ra.probe_of(base.entries[position]["text"], "queue_drain.md")
    text = masked_text(base, "queue_drain.md")
    encoder = Encoder()
    kept = ra.masked_q4(base, position, text, encoder.passage, window=probe["start"])
    assert encoder.passages == 0 and np.array_equal(kept.w[position], base.w[position])
    moved = ra.masked_q4(base, position, text, encoder.passage, window=probe["start"] + 1)
    assert encoder.passages == 1 and not np.array_equal(moved.w[position], base.w[position])
    assert np.allclose(moved.w[position], encoder.vector(text), rtol=0.0, atol=1e-6)  # stored as a build stores it
    doubled = ra.masked_q4(base, position, text, lambda t: 2.0 * encoder.vector(t))  # added after GREEN 2
    assert np.allclose(doubled.w[position], encoder.vector(text), rtol=0.0, atol=1e-6)  # normalised, as a build does


def test_an_audit_leaves_the_loaded_memory_unchanged(memory):
    base = load(memory(crowded_rows(4)))
    w, texts, joined = base.w.copy(), [entry["text"] for entry in base.entries], list(base.joined)
    scores, matches = base.lexical.scores(PROBE), base._self_matches(PROBE)
    ra.audit(base, "v21a", WHEN, Encoder().passage)
    assert np.array_equal(base.w, w) and [entry["text"] for entry in base.entries] == texts and base.joined == joined
    assert np.array_equal(base.lexical.scores(PROBE), scores) and np.array_equal(base._self_matches(PROBE), matches)
    assert "_self_matches" not in vars(base)


# --- statuses ------------------------------------------------------------------------------------------------------

def test_r4_left_the_probe_in_a_two_sentence_lesson_so_rule_v1_refused_it_the_audit_masks_it(memory):
    """R4 masks only a lesson with ONE qualifying sentence. Unmasked, the probe is a verbatim span of the lesson, and
    rule v1's self-match removes the lesson from its own question (measured precondition below)."""
    base = load(memory(reachable_rows()))
    index, _ = v2.RememberV2(base).applicable(PROBE, WHEN)
    assert position_of(base, "queue_drain.md") not in set(int(i) for i in index)
    row = row_of(ra.audit(base, "v21a", WHEN, Encoder().passage), "queue_drain.md")
    assert row["status"] == "reachable" and row["probe"]["qualifying"] == 2 and not row["probe"]["single"]


@pytest.mark.parametrize("arm", sorted(ra.ENGINES))
def test_a_lesson_its_probe_reaches_is_on_the_card_as_its_topics_head(memory, arm):
    report = run(memory, reachable_rows(), arm)
    row = row_of(report, "queue_drain.md")
    assert row["status"] == "reachable" and row["card"]["line"] == 1 and row["card"]["head"] is True
    assert row["views"] == {"w": 1, "d": 1, "b": 1} and row["fused_rank"] == 1
    assert row["fused"] == row["card"]["score"]  # added after GREEN 1: the audit fuses as the arm's own engine scores
    assert row["topic_on_card"] is False  # added after GREEN 2: L's own line is not another line on its topic
    packing = {"stopped_by": None, "skipped_groups": [] if arm in ("v22a", "v23a") else None, "withheld_by_guard": 0}
    assert row["card"]["reason"] is None and row["card"]["packing"] == packing  # D8.1
    assert row["crowding"] == {"records": 0, "lessons": 0, "events": 0} and row["crowders"] == []
    assert report["arm"] == arm and report["version"] == ra.ENGINES[arm][1]


@pytest.mark.parametrize("arm", sorted(ra.ENGINES))
def test_a_crowded_lesson_is_unreachable_and_names_who_crowds_it(memory, arm):
    report = run(memory, crowded_rows(4), arm)
    row = row_of(report, "queue_drain.md")
    assert row["status"] == "unreachable" and row["card"]["line"] is None and row["card"]["lines"] >= 3
    assert row["card"]["score"] is None and 0.0 < row["fused"] < 3.0 / 61  # added after GREEN 1
    assert sorted(row["crowders"]) == [f"crowd_{k:02d}.md" for k in range(4)]
    assert row["crowding"] == {"records": 4, "lessons": 4, "events": 0} and row["fused_rank"] == 5
    assert row["topic_on_card"] is False and report["unreachable"] == ["queue_drain.md"]


def test_crowders_are_listed_up_to_crowders_shown_and_counted_in_full(memory):
    row = row_of(run(memory, crowded_rows(12)), "queue_drain.md")
    assert row["crowding"]["records"] == 12 and row["fused_rank"] == 13
    assert len(row["crowders"]) == ra.CROWDERS_SHOWN == 10 and set(row["crowders"]) < {f"crowd_{k:02d}.md"
                                                                                        for k in range(12)}


def test_the_fused_rank_counts_all_three_views_as_the_engines_do(memory):
    """Added after GREEN 1: x_text's one-word description shares nothing with the probe, but its short text holds the
    probe's words, so the views disagree (measured, round x5): queue_drain is second on w and b and first on d, and
    still fuses first, since x_text's d rank is the last of the records that share no word; a fusion of w and b alone
    puts x_text first. (A first design, round x4, left queue_drain first on w.)"""
    tail = "\nthe retry before the queue drain: wait for it to settle\n"
    row = row_of(run(memory, reachable_rows() + [lesson("x_text", "zq190", tail=tail)]), "queue_drain.md")
    assert row["views"] == {"w": 2, "d": 1, "b": 2} and row["fused_rank"] == 1 and row["crowders"] == []
    assert row["fused"] == row["card"]["score"]


@pytest.mark.parametrize("arm", sorted(ra.ENGINES))
def test_a_lesson_shown_under_its_twins_head_is_reachable_and_its_topic_is_on_the_card(memory, arm):
    """Added after GREEN 1: drain_twin has queue_drain's description (one topic) and more of the probe's words, so it
    heads the topic group and queue_drain is the group's other shown line (measured)."""
    twin = lesson("drain_twin", DRAIN, how="wait for the queue drain to settle, then the retry goes before")
    row = row_of(run(memory, reachable_rows() + [twin], arm), "queue_drain.md")
    assert row["status"] == "reachable" and row["card"]["line"] == 2 and row["card"]["head"] is False
    assert row["topic_on_card"] is True and row["crowders"] == ["drain_twin.md"]
    assert row["fused"] == row["card"]["score"]


def test_an_event_fused_before_a_lesson_is_counted_as_an_event_by_its_event_ref(memory):
    row = row_of(run(memory, reachable_rows() + [EVENT_CROWD]), "queue_drain.md")
    assert row["status"] == "reachable" and row["fused_rank"] == 2
    assert row["crowding"] == {"records": 1, "lessons": 0, "events": 1}
    assert row["crowders"] == [EVENT_CROWD["provenance"]["event_ref"]]


def test_unreachable_lessons_are_sorted_by_crowding_then_ref(memory):
    report = run(memory, crowded_rows(8) + pin_family(6))
    assert report["unreachable"] == ["queue_drain.md", "cache_pin.md"]  # crowded by 8 records, then by 6
    assert [row_of(report, ref)["crowding"]["records"] for ref in report["unreachable"]] == [8, 6]
    assert run(memory, crowded_rows(4) + pin_family(4))["unreachable"] == ["cache_pin.md", "queue_drain.md"]  # a tie


def test_every_other_status_is_named_and_counted(memory):
    rows = reachable_rows() + [
        lesson("echo_probe", "Pin the lease before you restart the watcher.",
               how="Pin the lease before you restart the watcher."),
        lesson("guard_note", "redact tokens before logging", how=f"Mask the {GUARDED} value before you log it."),
        lesson("cut_note", "a cut note", how="Rotate the shard map before the next deploy.", cut=True),
        lesson("plain_note", "a description with no sections at all"),
        lesson("late_note", "a late note", how="Rotate the shard map before the next deploy.", day=13)]
    report = run(memory, rows, when="2026-10-12T12:00:00Z")
    status = {row["ref"]: row["status"] for row in report["rows"]}
    assert status == {"echo_probe.md": "probe_not_clean", "guard_note.md": "probe_guarded",
                      "cut_note.md": "stored_text_cut", "plain_note.md": "no_probe", "late_note.md": "not_available",
                      "queue_drain.md": "reachable", **{f"filler_{k}.md": "no_probe" for k in range(4)}}
    assert report["counts"] == {"not_available": 1, "stored_text_cut": 1, "no_probe": 5, "probe_guarded": 1,
                                "probe_not_clean": 1, "card_refused": 0, "card_empty": 0,  # D8.1: two statuses
                                "reachable": 1, "unreachable": 0}
    assert tuple(report["counts"]) == ra.STATUSES and report["lessons"] == len(report["rows"]) == 10


@pytest.mark.parametrize("arm", sorted(ra.ENGINES))
def test_a_card_that_lists_nothing_is_card_empty_with_its_reason_never_unreachable(memory, arm):
    """D8.1: queue_drain's summary alone carries the guarded word (synthetic: the corpus would cut such a text), so
    every arm withholds its line; with no other candidate at the time, the card lists nothing (measured)."""
    late = lesson("late_note", "a late note on colours", how="Rotate the shard map before the next deploy.", day=13)
    report = run(memory, [dict(drain(), summary=f"{DRAIN} {GUARDED}"), late], arm, when="2026-10-12T12:00:00Z")
    row = row_of(report, "queue_drain.md")
    assert row["status"] == "card_empty" and row["fused_rank"] == 1 and row["crowding"]["records"] == 0
    assert row["card"]["status"] == "EMPTY" and row["card"]["reason"] == "nothing_listable"
    assert row["card"]["lines"] == 0 and row["card"]["line"] is None
    assert row["card"]["packing"]["withheld_by_guard"] == 1
    assert report["counts"]["card_empty"] == 1 and report["counts"]["unreachable"] == 0 and report["unreachable"] == []


@pytest.mark.parametrize("arm", sorted(ra.ENGINES))
def test_a_lesson_whose_line_the_guard_withholds_is_unreachable_with_no_crowding_and_its_packing_says_so(memory, arm):
    """D8.1: the same lesson beside the fillers and the event fuses first, yet the card shows only the others
    (measured): unreachable is not crowding, and the card's packing counts the withheld line."""
    row = row_of(run(memory, reachable_rows()[:-1] + [dict(drain(), summary=f"{DRAIN} {GUARDED}")], arm),
                 "queue_drain.md")
    assert row["status"] == "unreachable" and row["card"]["status"] == "LISTED" and row["card"]["reason"] is None
    assert row["fused_rank"] == 1 and row["crowding"]["records"] == 0 and row["crowders"] == []
    assert row["card"]["packing"]["withheld_by_guard"] == 1


class FixedCard:
    """An arm's own engine whose card is replaced by a fixed one; applicable and ranks stay the engine's (D8.1)."""

    def __init__(self, engine, listed):
        self.engine, self.listed = engine, listed

    def applicable(self, situation, when):
        return self.engine.applicable(situation, when)

    def ranks(self, situation, index):
        return self.engine.ranks(situation, index)

    def shortlist(self, situation, when, queries):
        return dict(self.listed)


def test_a_refused_card_is_card_refused_with_its_reason_and_an_unknown_card_status_is_refused(memory, monkeypatch):
    """D8.1: no fixture makes a real engine refuse after rule v1 admitted the probe (the guard runs first, and v2.1 to
    v2.3 never expect output_guarded), so the refused card is a fixed one."""
    make, version = ra.ENGINES["v21a"]
    refused = {"status": "REFUSED", "reason": "output_guarded", "lines": []}
    monkeypatch.setitem(ra.ENGINES, "v21a", (lambda q: FixedCard(make(q), refused), version))
    report = run(memory, reachable_rows())
    row = row_of(report, "queue_drain.md")
    assert row["status"] == "card_refused" and row["card"]["status"] == "REFUSED"
    assert row["card"]["reason"] == "output_guarded" and row["card"]["lines"] == 0
    assert row["card"]["packing"] == {"stopped_by": None, "skipped_groups": None, "withheld_by_guard": None}
    assert report["counts"]["card_refused"] == 1 and report["unreachable"] == []
    monkeypatch.setitem(ra.ENGINES, "v21a", (lambda q: FixedCard(make(q), {"status": "LATER", "lines": []}), version))
    with pytest.raises(ra.ReachAuditError):
        run(memory, reachable_rows())


def test_a_report_has_one_row_per_lesson_in_ref_order_and_never_record_text(memory):
    rows = crowded_rows(4) + pin_family(2)
    report = run(memory, rows)
    lessons = sorted(row["meta"]["file"] for row in rows if row["kind"] == "lesson")
    assert [row["ref"] for row in report["rows"]] == lessons
    dumped = json.dumps(report)
    for sentence in (PROBE, FIRST, DRAIN, PROBE_B, FIRST_B, PIN, "wait for the queue drain"):
        assert sentence not in dumped
    assert not v2.guarded(report) and report["schema"] == ra.SCHEMA and report["time_utc"] == WHEN
    assert report["constants"] == {"min_probe_words": 4, "crowders_shown": 10, "pair_cosine": v2.PAIR_COSINE,
                                   "topic_cosine": v2.TOPIC_COSINE, "rrf_k": 60, "depth": v2.DEPTH, "cap": v2.CAP}


def test_an_unknown_arm_or_a_bad_time_is_refused_before_any_lesson_is_read(memory):
    base = load(memory(reachable_rows()))
    with pytest.raises(ra.ReachAuditError):
        ra.audit(base, "v21ca", WHEN, Encoder().passage)
    with pytest.raises(ra.ReachAuditError):
        ra.audit(base, "v21a", "2026-10-14 12:00", Encoder().passage)


# --- near duplicates -----------------------------------------------------------------------------------------------

def test_near_duplicates_are_union_find_clusters_at_the_pair_cosine(memory):
    words = TOPIC[:30]
    second = words[:29] + [TOPIC[30]]
    third = second[:28] + [TOPIC[31], TOPIC[30]]
    rows = [EVENT, lesson("dup_a", " ".join(words)), lesson("dup_b", " ".join(second)),
            lesson("dup_c", " ".join(third)), lesson("other_d", " ".join(TOPIC[40:60])),
            lesson("dup_late", " ".join(words), day=13)]  # added after GREEN 1: dup_a's copy, written after the time
    q4 = load(memory(rows))
    a, b, c = (position_of(q4, f"dup_{x}.md") for x in "abc")
    assert float(q4.d[a] @ q4.d[b]) >= v2.PAIR_COSINE and float(q4.d[b] @ q4.d[c]) >= v2.PAIR_COSINE
    assert float(q4.d[a] @ q4.d[c]) < v2.PAIR_COSINE  # the chain links a and c only through b
    report = ra.audit(q4, "v21a", "2026-10-12T12:00:00Z", Encoder().passage)
    assert report["clusters"] == [["dup_a.md", "dup_b.md", "dup_c.md"]]  # only lessons available at the time
    assert ra.clusters(q4, range(len(q4.ids))) == [["dup_a.md", "dup_b.md", "dup_c.md", "dup_late.md"]]


# --- the command line ----------------------------------------------------------------------------------------------

def test_the_cli_writes_one_report_with_the_memory_hash_and_never_overwrites(memory, tmp_path, capsys):
    path = memory(reachable_rows())
    out = tmp_path / "report.json"
    loads = []

    def fake_load(memory_dir):
        loads.append(memory_dir)
        return load(memory_dir), Encoder().passage, None

    argv = ["--memory", str(path), "--arm", "v21a", "--time", WHEN, "--out", str(out)]
    assert ra.main(argv, load=fake_load) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["memory_sha256"] == hashlib.sha256((path / "memory.json").read_bytes()).hexdigest()
    assert report["code_git_blobs"]["remember/reach_audit.py"] == v2.run_q4.git_blob_id(HERE / "reach_audit.py")
    assert row_of(report, "queue_drain.md")["status"] == "reachable"
    assert json.loads(capsys.readouterr().out)["counts"]["reachable"] == 1
    with pytest.raises(ra.ReachAuditError):
        ra.main(argv, load=fake_load)
    with pytest.raises(ra.ReachAuditError):
        ra.main(["--memory", str(path), "--arm", "v21a", "--time", "soon", "--out", str(tmp_path / "x.json")],
                load=fake_load)
    with pytest.raises(SystemExit):
        ra.main(["--memory", str(path), "--arm", "v3t", "--time", WHEN, "--out", str(tmp_path / "y.json")],
                load=fake_load)
    assert loads == [path] and not (tmp_path / "x.json").exists()
