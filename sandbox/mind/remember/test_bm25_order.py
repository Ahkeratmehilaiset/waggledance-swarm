"""Tests for D9 (Tools R03): a BM25 score that no hash seed can change, in step 2's ``q4.Lexical`` (rule v1's ``b``
view, which REMEMBER v2 also reads) and REMEMBER v3's ``SituationLexical`` (the per-situation ``b`` view of v2.1, v2.2
and v2.3). Synthetic records only: no e5, no real memory, no sealed case.

Python salts str hashes per process, so a set of question words iterates in a different order in each process. Both
scorers added each record's word contributions in that order, and float addition is not associative: the last bits of
a score, and with them a near-tie between two records, followed the process. Each score is now the exactly rounded sum
of its record's contributions. The oracle here sums them exactly (fractions.Fraction) and rounds once.

Written BEFORE the code change. The fixture was measured first, on the D8.1 code (round x1c in fable-5's D9 audit
folder; round x1 found no fixture and round x1b hung on its own fixture draw, both kept): 40 pairs of lessons whose
contributions are permutations of each other (the same four counts on different question words, the same length,
every question word in every pair record), so the exact BM25 scores of a pair are equal. Under each of the 24 orders
the four words can iterate in, at least 8 pairs did not tie, at least 3 put B before A and at least 11 records were not
exactly rounded, in both scorers; so each test below except the fixture check fails on the D8.1 code whatever the
process's hash seed is. Across 16 hash seeds the same memory gave 10 different score vectors and 3 (v2a) or 5 (v21a,
v22a, v23a) different cards, while rule v1's own answers took 1 distinct value.

Added after GREEN run 1: mutant run 1's Q03 (Python's own sum, compensated since 3.12, in place of math.fsum) survived,
since the BM25 contributions of one record are too alike in size for a compensated sum to round differently on any
input the fixture can build. So the exact sum became its own function, ``q4.exact_sums``, pinned on crafted values where
a running sum, Python's sum and an ascending sum all round wrong (round x1d), and by a test that both scorers sum
through it; those two tests are marked "added after GREEN 1".

run:C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember/test_bm25_order.py -q -p no:cacheprovider
"""

import hashlib
import json
import math
import random
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import remember_v23 as v23  # noqa: E402  (it loads remember_v22, remember_v21, remember_v3, remember_v2, step 2)

v22, v21, v2 = v23.v22, v23.v21, v23.v2
v3 = v21.v3
q4_mod, memory_mod = v2.q4_mod, v2.memory_mod
sys.path.insert(0, str(HERE.parent / "step2"))
import test_step2 as t2  # noqa: E402  (step-2 fixtures: event, write_inputs, FakeEncoder)

if t2.q4_mod is not q4_mod:
    raise ImportError("two step-2 q4 modules loaded")
WORDS = ("alpha", "bravo", "charlie", "delta")
QUERY = " ".join(WORDS)
ASKED = QUERY + " alpha zulu"  # a repeated word counts once; a word no record holds adds nothing
WHEN = "2026-10-14T12:00:00Z"
PAIRS, FILLERS, FIXTURE_SEED = 40, 8, 20261018
# round x1c's fixture rows as canonical JSON
MEASURED_ROWS_SHA256 = "6ba9e9be1096319a86ba7c942de0d70ed66c430a571d7e8a11fd94773baf68a1"


def lesson(name, summary, body):
    text = f"---\nname: {name}\ndescription: {summary}\n---\n\n{body}\n"
    return {"record_id": f"lesson:{name}@{hashlib.sha256(text.encode()).hexdigest()[:12]}", "kind": "lesson",
            "intent": "lesson", "time_utc": "2026-10-12T00:00:00.000000Z", "text": text, "summary": summary,
            "stored_text": text, "meta": {"kind": "lesson", "file": f"{name}.md"},
            "provenance": {"source_kind": "memory_note", "file": f"{name}.md",
                           "mtime_utc": "2026-10-12T10:00:00.000000Z", "source_sha256": "0" * 64,
                           "used_sha256": "0" * 64, "transform": None, "stored_text_cut_by_guard": False}}


def body(counts, filler):
    """Filler words first, then delta, charlie, bravo, alpha blocks: never the question's consecutive words."""
    return " ".join(filler + [word for word in reversed(WORDS) for _ in range(counts[word])])


def rows():
    """Round x1c's fixture: 40 pairs whose B holds a non-identity permutation of A's four counts (1..5, never all
    equal) with the same filler words and summary, then 8 lessons and one event that hold no question word."""
    rng = random.Random(FIXTURE_SEED)
    out = []
    for i in range(PAIRS):
        counts = [rng.randint(1, 5) for _ in WORDS]
        while len(set(counts)) == 1:  # an all-equal draw has no different permutation
            counts = [rng.randint(1, 5) for _ in WORDS]
        while True:
            order = rng.sample(range(4), 4)
            permuted = [counts[j] for j in order]
            if permuted != counts:
                break
        filler = [f"topic{i:02d}x{k}" for k in range(rng.randint(0, 9))]
        summary = f"pair {i:02d} notes on topic{i:02d}"
        out.append(lesson(f"pair_{i:02d}_a", summary, body(dict(zip(WORDS, counts)), filler)))
        out.append(lesson(f"pair_{i:02d}_b", summary, body(dict(zip(WORDS, permuted)), filler)))
    out += [lesson(f"filler_{k}", f"unrelated note {k} on colours", f"gardens menus kitchens number {k}")
            for k in range(FILLERS)]
    out.append(t2.event("2026-10-13T08:00:00.000000Z", "system cpu memory status health uptime disk process"))
    return sorted(out, key=lambda r: r["record_id"])


def pairs(data):
    files = [r["meta"].get("file") for r in data]
    return [(files.index(f"pair_{i:02d}_a.md"), files.index(f"pair_{i:02d}_b.md")) for i in range(PAIRS)]


def scorers(data):
    texts = [r["text"] for r in data]
    counts = [Counter(q4_mod.tokens(text)) for text in texts]
    k1, b = q4_mod.RULE["bm25_k1"], q4_mod.RULE["bm25_b"]
    return {"lexical": q4_mod.Lexical(texts, k1, b),
            "situation": v3.SituationLexical(counts, np.arange(len(texts)), k1, b)}


def exact(scorer, text):
    """Each record's BM25 from the scorer's own inputs and its own float expression per word, summed exactly
    (fractions.Fraction) and rounded once."""
    total = [Fraction(0)] * scorer.n
    for token in sorted(set(q4_mod.tokens(text))):
        if token not in scorer.postings:
            continue
        index, count = scorer.postings[token]
        idf = math.log(1.0 + (scorer.n - len(index) + 0.5) / (len(index) + 0.5))
        norm = count + scorer.k1 * (1.0 - scorer.b + scorer.b * scorer.lengths[index] / scorer.average)
        for position, value in zip(index.tolist(), (idf * count * (scorer.k1 + 1.0) / norm).tolist()):
            total[position] += Fraction(value)
    return np.array([float(value) for value in total])


def test_the_fixture_is_the_measured_one():
    """The precondition of the RED claim, so it passes on any code: the rows are round x1c's, no record holds the
    question as consecutive words (rule v1's self-match) and only the pair records hold question words."""
    data = rows()
    assert hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest() == MEASURED_ROWS_SHA256
    joined = [" " + " ".join(q4_mod.WORDS.findall(r["text"].lower())) + " " for r in data]
    assert not any(f" {QUERY} " in text for text in joined)
    holders = sorted(r["meta"]["file"] for r in data if set(WORDS) & set(q4_mod.tokens(r["text"])))
    assert holders == sorted(f"pair_{i:02d}_{side}.md" for i in range(PAIRS) for side in "ab")


@pytest.mark.parametrize("name", ["lexical", "situation"])
def test_pairs_with_permuted_contributions_tie_exactly(name):
    data = rows()
    scores = scorers(data)[name].scores(QUERY)
    assert [i for i, (a, b) in enumerate(pairs(data)) if scores[a] != scores[b]] == []
    assert all(scores[a] > 0.0 for a, _ in pairs(data))


@pytest.mark.parametrize("name", ["lexical", "situation"])
def test_each_score_is_the_exactly_rounded_sum_of_its_contributions(name):
    scorer = scorers(rows())[name]
    got, want = scorer.scores(ASKED), exact(scorer, ASKED)
    assert np.flatnonzero(got != want).tolist() == []
    assert int((want > 0.0).sum()) == 2 * PAIRS


@pytest.fixture(scope="module")
def q4(tmp_path_factory):
    root = tmp_path_factory.mktemp("bm25_order")
    corpus_dir, vectors_dir = t2.write_inputs(root, rows())
    memory_mod.build(corpus_dir, vectors_dir, root / "memory")
    return q4_mod.Q4(memory_mod.Memory(root / "memory"), t2.FakeEncoder())


ENGINES = {"v2a": lambda q4: v2.RememberV2(q4), "v21a": lambda q4: v21.RememberV21(q4, "v21a"),
           "v22a": lambda q4: v22.RememberV22(q4, "v22a"), "v23a": lambda q4: v23.RememberV23(q4, "v23a")}


@pytest.mark.parametrize("arm", sorted(ENGINES))
def test_every_engine_ranks_a_tied_pair_in_record_order(q4, arm):
    """The ``b`` rank each card is built from: A's id sorts before B's, so the stable rank of an exact tie lists A
    first in every process. v2a reads rule v1's Lexical; v21a, v22a and v23a read SituationLexical."""
    engine = ENGINES[arm](q4)
    index, _ = engine.applicable(QUERY, WHEN)
    assert len(index) == len(q4.ids) == 2 * PAIRS + FILLERS + 1  # every record is a candidate
    rank = dict(zip(index.tolist(), engine.ranks(QUERY, index)["b"].tolist()))
    files = [entry["row"]["provenance"].get("file") for entry in q4.entries]
    late = [i for i in range(PAIRS)
            if rank[files.index(f"pair_{i:02d}_a.md")] > rank[files.index(f"pair_{i:02d}_b.md")]]
    assert late == []


def test_exact_sums_round_once_where_other_sums_do_not():  # added after GREEN 1 (mutant run 1: Q03 survived)
    """Round x1d: 1 + 2**-53 + 2**-110 lies just above the midpoint between 1 and the next float, so the exactly rounded
    sum is 1 + 2**-52, while a running sum, Python's sum and an ascending sum all give 1.0; 0.1 + 0.2 + 0.3 tells a
    running and an ascending sum apart from it. A position with no values sums to 0.0."""
    near, tenths = [1.0, 2.0 ** -53, 2.0 ** -110], [0.1, 0.2, 0.3]
    got = q4_mod.exact_sums(3, {0: near, 2: tenths})
    assert got.tolist() == [float(sum(map(Fraction, near))), 0.0, float(sum(map(Fraction, tenths)))]
    assert got[0] == 1.0 + 2.0 ** -52


@pytest.mark.parametrize("name", ["lexical", "situation"])
def test_both_scorers_sum_through_exact_sums(name, monkeypatch):  # added after GREEN 1 (mutant run 1: Q03 survived)
    """Every score comes from ``q4.exact_sums``, called once with one value per record and distinct known question
    word (a repeated word counts once, an unknown word adds nothing), so the crafted test above covers every score."""
    data = rows()
    scorer = scorers(data)[name]
    seen, sentinel = [], np.arange(scorer.n) / 7.0
    monkeypatch.setattr(q4_mod, "exact_sums", lambda n, parts: seen.append((n, dict(parts))) or sentinel)
    assert scorer.scores(ASKED) is sentinel
    assert len(seen) == 1 and seen[0][0] == scorer.n
    assert sorted(seen[0][1]) == sorted(position for pair in pairs(data) for position in pair)
    assert all(len(values) == len(WORDS) for values in seen[0][1].values())
