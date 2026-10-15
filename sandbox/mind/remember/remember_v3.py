"""REMEMBER v3 dev prototype: v2ca plus three registered changes, each one switchable. Sandbox, advisory, never a gate.

For fable-5, the REMEMBER owner, to adopt or drop (operator directive 10-15 in the RCO2 session: help with the
REMEMBER design and coding, and make Grok think through the best variations). The ideas come from one direct Grok xhigh
round (answer sha256 745c0431...). The spec was registered before this code, in the RCO2 session worktree:
``.codex-audit/rco2-remember-grok-variations-20261015/PROTOTYPE_SPEC_V3_RCO2.v1.md`` (sha256 e48781c7...).

Nothing in remember_v2.py, prompts.py or step2 is edited: ``RememberV3`` subclasses ``RememberV2`` and overrides only
``ranks``. Applicability, the shortlist, topic groups, caps, marks and the judge text are v2's, unchanged.

* Per-situation BM25, in every arm (plan v2.1 T18). One lexical index per applicable index, built from per-record
  token counts that are made once. idf, average length and postings use only the applicable records, so a later
  record never moves a score. Tokens, k1 and b are step 2's; without the hop the scores equal a fresh step-2
  ``Lexical`` over the same texts (a test holds this).
* Hop (``hop=True``), Grok's BLUE. References are 8-hex families and PR numbers (``refs_of``), read from APPLICABLE
  LESSONS only.
  - A reference cited by 2..4 applicable lessons links them.
  - Each linked lesson borrows, for this index only, up to ``HOP_TERMS`` tokens it lacks from its donors. The tokens
    come from the top ``HOP_IDF_TOP`` of idf over the applicable index, highest idf first, then by token.
  - A borrowed token counts once and adds to the length.
  - Events are never donors or receivers, and no vector changes.
* WHEN view (``when`` vectors), part of Grok's GREEN. A lesson whose stored text has a ``How to apply`` section gets
  one more e5 vector of that section (``when_section``). Its ``d`` score is the larger of the summary cosine and the
  WHEN cosine, so the extra vector adds no slot.
* BRD (arm ``v3brd``): best-rank displacement, from Grok round 2 (answer 5a4c3701), under spec addendum v1.1
  (sha256 0cbcce8e...). Its tests were written before this code.
  - The base is the v3t card. Each situation view nominates its first ``BRD_DEPTH`` topic heads.
  - A topic absent from the card is admitted only when its best situation-view rank is strictly better than the
    worst primary line's.
  - It may only take the place of an extra line (the 2nd or 3rd line of a shown topic), last extra first.
  - It never removes a primary, never adds a line, and never breaks the byte cap.
  - Caution: those extras are the earliest and latest lines v2 shows for RCO1's conflict-probe defense, so any BRD
    build needs that probe rerun.

usage: python -B remember_v3.py build-when --memory <memory dir> --out <dir>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import remember_v2 as v2  # noqa: E402  (it loads step 2's corpus, memory and q4 modules once)

q4_mod = v2.q4_mod
VERSION = "remember-v3-dev.0"
ARMS = {"v3t": (False, False, False), "v3h": (True, False, False), "v3w": (False, True, False),
        "v3hw": (True, True, False), "v3brd": (False, False, True)}  # arm: (hop, when, brd)
BRD_DEPTH = 8
BRD_KEYS = ("primary", "best_rank", "views", "rewrite_top")
HOP_TERMS = 8
HOP_IDF_TOP = 0.05
HOP_DEGREE = (2, 4)
WHEN_CHARS = 600
WHEN_SCHEMA = "rco2.remember-v3-when-vectors.v0"
_HEX_REF = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{8,64}(?![0-9a-fA-F])")
_PR_REF = re.compile(r"(?:\bPR\s?|#)(\d{3,5})\b")
_WHEN_HEAD = re.compile(r"(?im)^[ \t>*-]*how to apply\b")
_SECTION_END = re.compile(r"\n[ \t]*\n(?=[ \t]*(?:\*\*|#|Related\b|\[\[))")


class RememberV3Error(ValueError):
    """Fixed messages that never echo input content."""


def refs_of(text: str) -> set[str]:
    """The references a text cites: 8-hex families (a hex token of 8..64 characters with a digit and a letter a-f,
    named by its first 8 characters, so a short and a full form of one ref agree) and PR numbers."""
    found = set()
    for token in _HEX_REF.findall(text):
        low = token.lower()
        if any(c.isdigit() for c in low) and any(c in "abcdef" for c in low):
            found.add("hex:" + low[:8])
    found.update("pr:" + number for number in _PR_REF.findall(text))
    return found


def when_section(text: str) -> str | None:
    """The ``How to apply`` section of a stored text: from its heading to the first blank line that comes before a
    heading, a ``Related`` line or a link line, at most WHEN_CHARS characters. None when the text has none."""
    head = _WHEN_HEAD.search(text)
    if head is None:
        return None
    rest = text[head.start():]
    end = _SECTION_END.search(rest)
    section = (rest[:end.start()] if end else rest)[:WHEN_CHARS].strip()
    return section or None


def topic_heads(rank: np.ndarray, vectors: np.ndarray, depth: int) -> list[int]:
    """One view's walk in rank order: the first record of each new topic, until ``depth`` topics are counted. A rank
    past the candidates (no lexical evidence) ends it, as in v2's ``top_topics``."""
    counted: list[int] = []
    for i in np.argsort(rank, kind="stable"):
        if rank[i] > len(rank):
            break
        if counted and float(np.max(vectors[counted] @ vectors[i])) >= v2.TOPIC_COSINE:
            continue
        if len(counted) == depth:
            break
        counted.append(int(i))
    return counted


def brd_order(nominees: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Grok's frozen order: best rank, then more nominating views, then the rewrite's top 8, then the record id."""
    return sorted(nominees, key=lambda n: (n["best_rank"], -n["views"], not n["rewrite_top"], n["doc_id"]))


def brd_apply(lines: Sequence[Mapping[str, Any]], nominees: Sequence[Mapping[str, Any]], fits) -> tuple[list, int, int]:
    """Best-rank displacement over one card: (new lines, swaps, admitted). A nominee is admitted when its best rank is
    strictly better than the worst primary's. Each admitted nominee, in ``brd_order``, takes the place of the LAST extra
    line and is appended as a primary, if ``fits`` accepts the trial card. Primaries are never removed, nothing is added
    without a removal, and a nominee that does not fit is skipped."""
    out = [dict(line) for line in lines]
    primaries = [line["best_rank"] for line in out if line["primary"]]
    if not primaries:
        return out, 0, 0
    worst = max(primaries)
    admitted = brd_order([n for n in nominees if n["best_rank"] < worst])
    swaps = 0
    for nominee in admitted:
        extras = [i for i, line in enumerate(out) if not line["primary"]]
        if not extras:
            break
        trial = out[:extras[-1]] + out[extras[-1] + 1:] + [dict(nominee, primary=True)]
        if fits(trial):
            out, swaps = trial, swaps + 1
    return out, swaps, len(admitted)


class SituationLexical:
    """BM25 over one applicable index (step 2's tokens, k1 and b), from per-record token counts made once.
    ``extra`` maps a record position to borrowed tokens; each counts once and adds to that record's length."""

    def __init__(self, counts: Sequence[Counter], index: np.ndarray, k1: float, b: float,
                 extra: Mapping[int, Sequence[str]] | None = None):
        self.k1, self.b, self.n = k1, b, len(index)
        rows: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.lengths = np.zeros(self.n, dtype=np.float64)
        for local, position in enumerate(index):
            bag = counts[int(position)]
            borrowed = (extra or {}).get(int(position), ())
            if borrowed:
                bag = bag + Counter(borrowed)
            self.lengths[local] = sum(bag.values())
            for token, count in bag.items():
                rows[token].append((local, count))
        self.average = float(self.lengths.mean()) if self.n else 0.0
        self.postings = {token: (np.array([i for i, _ in found]), np.array([c for _, c in found], dtype=np.float64))
                         for token, found in rows.items()}

    def scores(self, text: str) -> np.ndarray:
        result = np.zeros(self.n)
        for token in set(q4_mod.tokens(text)):
            if token not in self.postings:
                continue
            where, count = self.postings[token]
            idf = math.log(1.0 + (self.n - len(where) + 0.5) / (len(where) + 0.5))
            norm = count + self.k1 * (1.0 - self.b + self.b * self.lengths[where] / self.average)
            result[where] += idf * count * (self.k1 + 1.0) / norm
        return result


class RememberV3(v2.RememberV2):
    """v2ca with per-situation BM25, an optional hop and an optional WHEN view (module docstring)."""

    def __init__(self, q4: Any, hop: bool = False, when: tuple[np.ndarray, np.ndarray] | None = None):
        super().__init__(q4)
        self.hop = bool(hop)
        self.brd = False
        self.when_vectors, self.when_mask = when if when is not None else (None, None)
        if self.when_vectors is not None and (self.when_vectors.shape != q4.d.shape
                                              or self.when_mask.shape != (len(q4.ids),)):
            raise RememberV3Error("when vectors do not match the loaded memory")
        self.counts = [Counter(q4_mod.tokens(entry["text"])) for entry in q4.entries]
        self.lesson = np.array([entry["row"]["kind"] == "lesson" for entry in q4.entries], dtype=bool)
        self.refs = [refs_of(entry["text"]) if self.lesson[i] else set() for i, entry in enumerate(q4.entries)]
        self._cache: tuple[str, SituationLexical] | None = None
        self.last_borrowed: dict[int, list[str]] = {}

    def set_arm(self, arm: str, when: tuple[np.ndarray, np.ndarray] | None = None) -> None:
        """Switch to a registered arm on the same loaded memory (the lexical cache is keyed by the hop flag)."""
        if arm not in ARMS:
            raise RememberV3Error("unknown arm")
        hop, uses_when, brd = ARMS[arm]
        if uses_when and when is None and self.when_vectors is None:
            raise RememberV3Error("this arm needs when vectors")
        self.hop, self.brd = hop, brd
        if not uses_when:
            self.when_vectors, self.when_mask = None, None
        elif when is not None:
            self.when_vectors, self.when_mask = when

    def borrowed(self, index: np.ndarray) -> dict[int, list[str]]:
        """Hop tokens per receiving lesson position, from applicable lessons only (module docstring)."""
        lessons = [int(p) for p in index if self.lesson[int(p)]]
        cited: dict[str, list[int]] = defaultdict(list)
        for position in lessons:
            for ref in self.refs[position]:
                cited[ref].append(position)
        n = len(index)
        df = Counter(token for p in index for token in self.counts[int(p)])
        if not df:
            return {}
        idf = {token: math.log(1.0 + (n - count + 0.5) / (count + 0.5)) for token, count in df.items()}
        cut = float(np.quantile(np.fromiter(idf.values(), dtype=np.float64), 1.0 - HOP_IDF_TOP))
        low, high = HOP_DEGREE
        out = {}
        for position in lessons:
            donors = sorted({donor for ref in self.refs[position] if low <= len(cited[ref]) <= high
                             for donor in cited[ref] if donor != position})
            if not donors:
                continue
            own = self.counts[position]
            pool = {token for donor in donors for token in self.counts[donor]
                    if token not in own and idf[token] >= cut}
            take = sorted(pool, key=lambda token: (-idf[token], token))[:HOP_TERMS]
            if take:
                out[position] = take
        return out

    def lexical(self, index: np.ndarray) -> SituationLexical:
        key = hashlib.sha256(np.asarray(index, dtype=np.int64).tobytes() + bytes([self.hop])).hexdigest()
        if self._cache is None or self._cache[0] != key:
            extra = self.borrowed(index) if self.hop else None
            self.last_borrowed = extra or {}
            self._cache = (key, SituationLexical(self.counts, index, self.q4.rule["bm25_k1"], self.q4.rule["bm25_b"],
                                                 extra))
        return self._cache[1]

    def ranks(self, text: str, index: np.ndarray) -> dict[str, np.ndarray]:
        """v2's ranks with the per-situation lexical view and, when loaded, the WHEN view max-pooled into ``d``."""
        query = self.q4.encoder.query(text)
        summary = self.q4.d[index] @ query
        if self.when_vectors is not None:
            mask = self.when_mask[index]
            if mask.any():
                summary = summary.copy()
                summary[mask] = np.maximum(summary[mask], (self.when_vectors[index] @ query)[mask])
        views = {"w": self.q4.w[index] @ query, "d": summary, "b": self.lexical(index).scores(text)}
        out = {}
        for name, scores in views.items():
            order = np.argsort(-scores, kind="stable")
            rank = np.empty(len(index), dtype=int)
            rank[order] = np.arange(1, len(index) + 1)
            if name == "b":
                rank[scores <= 0.0] = len(index) + 1
            out[name] = rank
        out["_w_cosine"] = views["w"]
        return out

    def shortlist_arm(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        """The current arm's card: BRD over the v3t card, or the shortlist itself."""
        if self.brd:
            return self.shortlist_brd(situation, situation_time_utc, queries)
        return self.shortlist(situation, situation_time_utc, queries)

    def shortlist_brd(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        """Addendum v1.1, choices C1-C9: the v3t card, then best-rank displacement from the SITUATION views only."""
        listed = self.shortlist(situation, situation_time_utc, queries)  # C1: the v3t card
        if listed["status"] != "LISTED":
            return listed  # C8
        index, _ = self.applicable(situation, situation_time_utc)
        local = {int(p): i for i, p in enumerate(index)}
        vectors = self.q4.d[index]
        ranks = self.ranks(situation, index)
        best = np.minimum(np.minimum(ranks["w"], ranks["d"]), ranks["b"])  # C2
        lines = [dict(line) for line in listed["lines"]]
        heads = {line["topic"]["group"] for line in lines if line["topic"]["head"]}
        seen: set[int] = set()
        for line in lines:  # C3: the head is the primary; without a shown head, the group's first shown line
            group = line["topic"]["group"]
            line["primary"] = bool(line["topic"]["head"]) or (group not in heads and group not in seen)
            seen.add(group)
            line["best_rank"] = int(best[local[line["position"]]])
        topics: list[dict[str, Any]] = []
        for view in ("w", "d", "b"):  # C4: nominated topic heads, merged across views
            for head in topic_heads(ranks[view], vectors, BRD_DEPTH):
                same = next((t for t in topics if float(vectors[t["member"]] @ vectors[head]) >= v2.TOPIC_COSINE), None)
                if same is None:
                    topics.append({"member": head, "views": {view}})
                    continue
                same["views"].add(view)
                if (best[head], self.q4.ids[index[head]]) < (best[same["member"]], self.q4.ids[index[same["member"]]]):
                    same["member"] = head
        rewrite_heads: list[int] = []
        for text in list(queries)[1:]:
            rewrite_ranks = self.ranks(text, index)
            for view in ("w", "d", "b"):
                rewrite_heads += topic_heads(rewrite_ranks[view], vectors, BRD_DEPTH)
        on_card = [local[line["position"]] for line in lines]
        worst = max(line["best_rank"] for line in lines if line["primary"])
        fused = sum(1.0 / (self.q4.rule["rrf_k"] + ranks[view]) for view in ("w", "d", "b"))
        nominees = []
        for topic in topics:
            member = topic["member"]
            if member in on_card or float(np.max(vectors[on_card] @ vectors[member])) >= v2.TOPIC_COSINE:
                continue  # C5: present on the card
            if best[member] >= worst:
                continue  # never admitted, so no line is built for it
            line = self._result(int(index[member]), float(fused[member]),
                                {f"{view}0": int(ranks[view][member]) for view in ("w", "d", "b")})
            members = np.flatnonzero(vectors @ vectors[member] >= v2.TOPIC_COSINE)
            when = sorted((self._when(int(index[j])), int(j)) for j in members)
            line["topic"] = {"group": 0, "records": int(len(members)),
                             "first_day": self.q4.entries[index[when[0][1]]]["row"]["time_utc"][:10],
                             "last_day": self.q4.entries[index[when[-1][1]]]["row"]["time_utc"][:10], "head": True}
            line["superseded_by"] = self._superseded_by(line)
            if v2.guarded(line) or v2.corpus_mod.ws._has_marker(v2.line_text(1, line)):
                continue  # C7: the guard runs on the new line as v2 runs it on its own
            line.update(best_rank=int(best[member]), views=len(topic["views"]),
                        rewrite_top=any(float(vectors[h] @ vectors[member]) >= v2.TOPIC_COSINE
                                        for h in rewrite_heads))
            nominees.append(line)

        def clean(card: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
            top = max(line["topic"]["group"] for line in lines)
            out = []
            for line in card:
                line = {key: value for key, value in line.items() if key not in BRD_KEYS}
                if line["topic"]["group"] == 0:  # C6: an inserted line is its own group, numbered after the rest
                    top += 1
                    line["topic"] = dict(line["topic"], group=top)
                out.append(line)
            return self._marked(out)

        def fits(card: Sequence[Mapping[str, Any]]) -> bool:  # C7
            marked = clean(card)
            return len(marked) <= v2.CAP and v2.rendered_bytes(marked) <= v2.CASE_BYTES

        chosen, swaps, admitted = brd_apply(lines, nominees, fits)
        final = clean(chosen)
        telemetry = dict(listed.get("telemetry") or {}, brd_admitted=admitted, brd_swaps=swaps,
                         line_bytes=v2.rendered_bytes(final))  # C9
        return {"status": "LISTED", "reason": None, "lines": final, "telemetry": telemetry}


# --- WHEN vectors: built once, retained, verified on load ---------------------------------------------------------

def when_inputs(q4: Any) -> list[tuple[int, str]]:
    """(position, section) for every lesson whose stored text has a section, in record-id order."""
    return [(i, section) for i, entry in enumerate(q4.entries)
            if entry["row"]["kind"] == "lesson" and (section := when_section(entry["text"])) is not None]


def build_when(q4: Any, memory_sha256: str, out_dir: Path) -> dict[str, Any]:
    rows = when_inputs(q4)
    vectors = q4.encoder.passages([section for _, section in rows]) if rows \
        else np.zeros((0, q4.d.shape[1]), dtype="<f4")
    body = np.ascontiguousarray(vectors, dtype="<f4").tobytes()
    manifest = {"schema": WHEN_SCHEMA, "version": VERSION, "memory_sha256": memory_sha256,
                "ids_sha256": hashlib.sha256("\n".join(q4.ids).encode("utf-8")).hexdigest(),
                "records": [{"id": q4.ids[i], "section_sha256": hashlib.sha256(section.encode("utf-8")).hexdigest()}
                            for i, section in rows],
                "vectors_sha256": hashlib.sha256(body).hexdigest(), "dim": int(q4.d.shape[1]),
                "policy": getattr(q4.encoder, "policy", None), "when_chars": WHEN_CHARS}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "when-vectors.f32").write_bytes(body)
    (out_dir / "when-manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def read_when(q4: Any, memory_sha256: str, when_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """(vectors aligned to q4.ids, mask) after checking the files against the memory and the stored sections."""
    manifest = json.loads((when_dir / "when-manifest.json").read_text(encoding="utf-8"))
    body = (when_dir / "when-vectors.f32").read_bytes()
    if manifest.get("schema") != WHEN_SCHEMA or hashlib.sha256(body).hexdigest() != manifest["vectors_sha256"] \
            or manifest["memory_sha256"] != memory_sha256 \
            or manifest["ids_sha256"] != hashlib.sha256("\n".join(q4.ids).encode("utf-8")).hexdigest():
        raise RememberV3Error("when vectors do not match the loaded memory")
    expected = {q4.ids[i]: hashlib.sha256(section.encode("utf-8")).hexdigest() for i, section in when_inputs(q4)}
    if {row["id"]: row["section_sha256"] for row in manifest["records"]} != expected \
            or [row["id"] for row in manifest["records"]] != sorted(expected):
        raise RememberV3Error("when sections do not match the loaded memory")
    dim = manifest["dim"]
    rows = np.frombuffer(body, dtype="<f4").reshape(len(manifest["records"]), dim)
    vectors = np.zeros((len(q4.ids), dim), dtype=np.float32)
    mask = np.zeros(len(q4.ids), dtype=bool)
    where = {record_id: i for i, record_id in enumerate(q4.ids)}
    for row, record in zip(rows, manifest["records"]):
        vectors[where[record["id"]]] = row
        mask[where[record["id"]]] = True
    return vectors, mask


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-when")
    build.add_argument("--memory", type=Path, required=True)
    build.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    engine = v2._load_engine(args.memory)
    manifest = build_when(engine.q4, v2.sha256_file(args.memory / "memory.json"), args.out)
    print(json.dumps({"records": len(manifest["records"]), "vectors_sha256": manifest["vectors_sha256"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
