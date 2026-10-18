"""HEX MEMORY STEP 2, Q4: "have we seen this before?" over the verified hex memory, or a reasoned ABSTAIN.

usage: python -B sandbox/mind/step2/q4.py --memory <memory dir> --situation "<text>" --time <UTC>

Rule v1 (frozen before any fresh blinded case is opened; chosen on the used 10-13 sets only, see calibrate.py):

1. REFUSED when the situation is empty or its time is not an aware UTC time.
2. Applicability BEFORE ranking. A record is a candidate only when
   * it existed in this form at least 1 s before the situation (``available_utc``: lesson file mtime, event time);
   * it is not the situation itself: no record whose stored text holds half or more of the situation's 8-word
     shingles (the whole situation when it is shorter) is ever shown.
3. Three independent views rank every candidate: ``w`` e5 cosine to the record text, ``d`` e5 cosine to the record
   summary (a lesson's own description line), ``b`` BM25 (k1 1.2, b 0.75) over the stored text. A record that shares
   no token with the situation has no lexical rank at all (zero BM25 is no evidence, never a tie-break). A record's
   BM25 is the exactly rounded sum of its word contributions, the same in every process (D9; before, the sum followed
   the per-process hash order of a set of words, and so did its last bits and any near-tie).
4. A record is AGREED when all three views rank it within the top 3. No agreed record: ABSTAIN
   (``no_view_consensus``); no candidate at all: ABSTAIN (``empty_cell``). Otherwise ANSWER with the agreed
   records, at most 3, ordered by reciprocal-rank fusion (k 60). One view alone is never enough: on the used sets every
   single view answered every negative.
5. A QUARANTINED cell makes the outcome DEGRADED, never ABSTAIN and never a clean ANSWER: the rule runs over the
   healthy cells only and every listed record is marked ``fallback``; the quarantined cell's WaggleDance ring-1
   neighbours are named as the fallback route.

Every healthy cell is searched (WaggleDance's global layer; at 3030 records a full search costs milliseconds), so
keyword routing can never hide the learning cell. Results carry refs and metadata only, never record text, and pass
the decoded privacy guard. No network, no bridge event, no gate code.
"""

import argparse
import datetime as dt
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import corpus as corpus_mod  # noqa: E402
import memory as memory_mod  # noqa: E402

RULE = {"version": "q4-rule-v1", "agree_depth": 3, "views": ["w", "d", "b"], "shown_max": 3, "rrf_k": 60,
        "bm25_k1": 1.2, "bm25_b": 0.75, "guard_band_s": 1.0, "self_match_shingle": 8, "self_match_share": 0.5}
TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(text):
    return TOKEN.findall(text.lower())


def shingles(words, size):
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


def bm25_scores(text, n, postings, lengths, average, k1, b):
    """BM25 of ``n`` records for ``text`` from an inverted index (token -> record positions and counts). A record's
    score is the exactly rounded sum of its word contributions (``exact_sums``). A set of words iterates in a
    per-process hash order and float addition is not associative, so a running sum let the hash seed set the last bits
    of a score and with them any near-tie (D9, Tools R03). Now no order can change a bit, and records whose
    contributions are the same numbers tie exactly, so a stable rank lists them in record order."""
    parts = defaultdict(list)
    for token in set(tokens(text)):
        if token not in postings:
            continue
        index, count = postings[token]
        idf = math.log(1.0 + (n - len(index) + 0.5) / (len(index) + 0.5))
        norm = count + k1 * (1.0 - b + b * lengths[index] / average)
        for position, value in zip(index.tolist(), (idf * count * (k1 + 1.0) / norm).tolist()):
            parts[position].append(value)
    return exact_sums(n, parts)


def exact_sums(n, parts):
    """``n`` sums: at each position of ``parts`` (position -> values) the exactly rounded sum (math.fsum) of its values,
    which no order of them can change; 0.0 elsewhere."""
    result = np.zeros(n)
    for position, values in parts.items():
        result[position] = math.fsum(values)
    return result


class Lexical:
    """BM25 over the stored text of one record list, with an inverted index (``bm25_scores``)."""

    def __init__(self, texts, k1, b):
        self.n, self.k1, self.b = len(texts), k1, b
        self.lengths = np.array([len(tokens(text)) for text in texts], dtype=np.float64)
        self.average = float(self.lengths.mean()) if self.n else 0.0
        postings = defaultdict(list)
        for index, text in enumerate(texts):
            for token, count in Counter(tokens(text)).items():
                postings[token].append((index, count))
        self.postings = {token: (np.array([i for i, _ in rows]), np.array([c for _, c in rows], dtype=np.float64))
                         for token, rows in postings.items()}

    def scores(self, text):
        return bm25_scores(text, self.n, self.postings, self.lengths, self.average, self.k1, self.b)


class Q4:
    """The query side of one loaded memory: all healthy cells, both vector views and the lexical view."""

    def __init__(self, memory, encoder, rule=None):
        self.memory, self.encoder, self.rule = memory, encoder, dict(rule or RULE)
        rows = {}
        self.quarantined = {}
        for cell_id, view in memory.views.items():
            if not view.healthy:
                self.quarantined[cell_id] = view.reason
                continue
            vectors = view.collection._index.reconstruct_n(0, view.collection.count) if view.collection.count \
                else np.zeros((0, memory.memory["dim"]), dtype=np.float32)
            for position, row in enumerate(view.rows):
                record_id, which = row["provenance"]["record"], row["provenance"]["view"]
                entry = rows.setdefault(record_id, {"cell": cell_id, "row": row})
                entry[which] = vectors[position]
                if which == "w":
                    entry["text"] = view.collection._texts[position]
        self.ids = sorted(record_id for record_id, entry in rows.items() if "w" in entry and "d" in entry)
        if len(self.ids) != len(rows):
            raise memory_mod.MemoryError_("record_view_missing")
        self.entries = [rows[record_id] for record_id in self.ids]
        dim = memory.memory["dim"]
        self.w = np.vstack([entry["w"] for entry in self.entries]) if self.entries else np.zeros((0, dim), np.float32)
        self.d = np.vstack([entry["d"] for entry in self.entries]) if self.entries else np.zeros((0, dim), np.float32)
        self.available = np.array([entry["row"]["provenance"]["available_utc"] for entry in self.entries])
        self.lexical = Lexical([entry["text"] for entry in self.entries], self.rule["bm25_k1"], self.rule["bm25_b"])
        size = self.rule["self_match_shingle"]
        self.joined = []
        self.shingle_index = defaultdict(list)  # shingle -> positions of the records that contain it (built once)
        for position, entry in enumerate(self.entries):
            words = WORDS.findall(entry["text"].lower())
            self.joined.append(" " + " ".join(words) + " ")
            for shingle in shingles(words, size):
                self.shingle_index[shingle].append(position)

    def _self_matches(self, situation):
        """Records holding half or more of the situation's 8-word shingles (the whole situation when shorter)."""
        size, share = self.rule["self_match_shingle"], self.rule["self_match_share"]
        words = WORDS.findall(situation.lower())
        found = np.zeros(len(self.entries), dtype=bool)
        if not words:
            return found
        if len(words) < size:
            needle = " " + " ".join(words) + " "
            return np.array([needle in joined for joined in self.joined], dtype=bool)
        wanted = shingles(words, size)
        counts = Counter(position for shingle in wanted for position in self.shingle_index.get(shingle, ()))
        for position, count in counts.items():
            found[position] = count >= share * len(wanted)
        return found

    def ask(self, situation, situation_time_utc):
        started = time.perf_counter()
        when = corpus_mod.parse_utc(situation_time_utc) if isinstance(situation_time_utc, str) else None
        if not isinstance(situation, str) or not situation.strip():
            return self._out("REFUSED", "situation_empty", [], {}, started)
        if when is None:
            return self._out("REFUSED", "situation_time_invalid", [], {}, started)
        limit = corpus_mod.utc_text(when - dt.timedelta(seconds=self.rule["guard_band_s"]))
        fresh = self.available < limit
        self_match = self._self_matches(situation)
        candidate = fresh & ~self_match
        telemetry = {"records": len(self.entries), "candidates": int(candidate.sum()),
                     "excluded_not_before_situation": int((~fresh).sum()),
                     "excluded_self_match": int((fresh & self_match).sum())}
        if not candidate.any():
            return self._out("DEGRADED" if self.quarantined else "ABSTAIN", "empty_cell", [], telemetry, started)
        query = self.encoder.query(situation)
        index = np.flatnonzero(candidate)
        views = {"w": self.w[index] @ query, "d": self.d[index] @ query, "b": self.lexical.scores(situation)[index]}
        ranks = {}
        for name, scores in views.items():
            order = np.argsort(-scores, kind="stable")
            rank = np.empty(len(index), dtype=int)
            rank[order] = np.arange(1, len(index) + 1)
            if name == "b":
                rank[scores <= 0.0] = len(index) + 1  # no shared token is no lexical evidence, never a tie-break
            ranks[name] = rank
        depth = self.rule["agree_depth"]
        agreed = np.flatnonzero(np.all([ranks[name] <= depth for name in self.rule["views"]], axis=0))
        fused = sum(1.0 / (self.rule["rrf_k"] + ranks[name]) for name in self.rule["views"])
        agreed = sorted(agreed, key=lambda i: (-fused[i], self.ids[index[i]]))[:self.rule["shown_max"]]
        results = [self._result(index[i], float(views["w"][i]), float(fused[i]),
                                {name: int(ranks[name][i]) for name in self.rule["views"]}) for i in agreed]
        if self.quarantined:
            return self._out("DEGRADED", "cell_quarantined", results, telemetry, started)
        if not results:
            return self._out("ABSTAIN", "no_view_consensus", [], telemetry, started)
        return self._out("ANSWER", None, results, telemetry, started)

    def _result(self, position, cosine, fused, ranks):
        entry = self.entries[position]
        provenance = entry["row"]["provenance"]
        if entry["row"]["kind"] == "lesson":
            ref = provenance["file"]
        else:
            ref = provenance["event_ref"]
        return {"ref": ref, "doc_id": self.ids[position], "kind": entry["row"]["kind"], "score": round(cosine, 6),
                "fused": round(fused, 6), "view_ranks": ranks, "cell": entry["cell"],
                "time_utc": entry["row"]["time_utc"], "tier": provenance["tier"]["source_type"],
                "fallback": bool(self.quarantined)}

    def _out(self, status, reason, results, telemetry, started):
        degraded = [{"cell": cell_id, "reason": code,
                     "ring1": sorted(self.memory.topology.get_neighbors(cell_id, max_ring=1))}
                    for cell_id, code in sorted(self.quarantined.items())]
        out = {"status": status, "abstain": status == "ABSTAIN", "reason": reason, "degraded": bool(self.quarantined),
               "degraded_cells": degraded, "results": results, "rule": self.rule["version"],
               "telemetry": dict(telemetry, ms=round((time.perf_counter() - started) * 1000, 1))}
        corpus_mod.ws.guard_object(out)
        return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--situation", required=True)
    parser.add_argument("--time", required=True)
    args = parser.parse_args(argv)
    import encode as encode_mod
    q4 = Q4(memory_mod.Memory(args.memory), encode_mod.E5())
    print(json.dumps(q4.ask(args.situation, args.time), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
