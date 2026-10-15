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
ARMS = {"v3t": (False, False), "v3h": (True, False), "v3w": (False, True), "v3hw": (True, True)}  # arm: (hop, when)
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
        hop, uses_when = ARMS[arm]
        if uses_when and when is None and self.when_vectors is None:
            raise RememberV3Error("this arm needs when vectors")
        self.hop = hop
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
