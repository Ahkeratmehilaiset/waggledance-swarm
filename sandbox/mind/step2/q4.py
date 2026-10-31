"""Read-only Q4 ranking port from 6cf81dbb; no CLI or memory writes."""
import datetime as dt
import math
import re
import time
from collections import Counter, defaultdict

import numpy as np
from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import memory as memory_mod

RULE = {"version": "q4-rule-v1.1", "agree_depth": 3, "views": ["w", "d", "b"], "shown_max": 3, "rrf_k": 60,
        "bm25_k1": 1.2, "bm25_b": 0.75, "guard_band_s": 1.0, "self_match_shingle": 8, "self_match_share": 0.5}
TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(text):
    return TOKEN.findall(text.lower())


def shingles(words, size):
    return {" ".join(words[i:i + size]) for i in range(len(words) - size + 1)}


class Lexical:
    """BM25 over the stored text of one record list, with an inverted index."""

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
        result = np.zeros(self.n)
        for token in set(tokens(text)):
            if token not in self.postings:
                continue
            index, count = self.postings[token]
            idf = math.log(1.0 + (self.n - len(index) + 0.5) / (len(index) + 0.5))
            norm = count + self.k1 * (1.0 - self.b + self.b * self.lengths[index] / self.average)
            result[index] += idf * count * (self.k1 + 1.0) / norm
        return result


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
        self.available = np.array([entry["row"]["provenance"]["available_utc"] for entry in self.entries], dtype=str)
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
                # No lexical evidence must remain outside the agreement depth,
                # including when there are fewer candidates than that depth.
                rank[scores <= 0.0] = max(len(index), self.rule["agree_depth"]) + 1
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
