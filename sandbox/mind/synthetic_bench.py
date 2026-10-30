"""S13i synthetic Mind bench (fable-5, 2026-10-26): a runnable bench of six questions on DETERMINISTIC SYNTHETIC input.
Sandbox, advisory, never a gate. It measures what the CODE does on constructed geometry and text; a fake rank proves
nothing about real retrieval, and no row is a measurement of the fleet's memory (no real data, no M0/M1/M2).

Questions (``QUESTIONS``), each a row per compared code version:

* ``top3_relevance``             are the three shown lines lessons of the planted topic?
* ``duplicates_displacing``      do near-duplicates of one lesson take the shown slots from distinct lessons?
* ``stale_contradictory_order``  is a superseded lesson marked and listed after its successor; is a contradictory
                                 pair marked at all?
* ``irrelevant_warning``         does an unrelated situation get a warning (abstain, refusal, nothing listed) or lines?
* ``utf8_card_budget``           JOIN: do cards with multibyte REMEMBER text stay within ``join_card.CARD_BYTES``
                                 without a refusal, and are drops counted whole?
* ``same_input_determinism``     is the whole bench byte-identical when run twice from the same seed?

Compared code: JOIN is ``sandbox/mind/join_card.py`` at this tree (always runnable; the in-repo delivery and lane-view
fixtures of ``test_join_card_delivery.py`` make the cards). REMEMBER is not on main: its code dependencies live in the
public Source-only Git revision 6cf81dbb (``sandbox/mind/remember`` and ``step2``). When ``--remember-export DIR``
names an export of those files (the same relative layout), the four REMEMBER questions run the real engines
``RememberV2`` (arms v2c gate, v2a shortlist), ``RememberV3`` (arm v3t: per-situation BM25) and ``RememberV21`` (arm
v21a: MAY_OPPOSE marks) over a STRICT FAKE step-2 ``Q4``: synthetic unit vectors and texts, step 2's own BM25 and
self-match code, an encoder that returns only vectors registered for exactly that text and raises for any other, no
model, no FAISS index, no file of the fleet's memory. The export is verified before anything is imported: every
required file must have the git blob id registered in ``REMEMBER_BLOBS`` (``verify_export``); the export tree must hold
nothing but those ten files, their directories and the one named non-.py manifest ``EXPORT_MANIFEST`` at the root
(``export_tree_entries``: an extra ``.py``, a ``tools/``, ``core/`` or ``waggledance/`` package, a ``__pycache__`` or
any ``.pyc`` is a named refusal, because step 2's ``corpus.py`` puts the export root at ``sys.path[0]``); every cached
module of those ten names is displaced unconditionally; bytecode writing is off during the import; and each imported
module must originate from exactly the file registered for its name, still with the registered bytes. Otherwise, and
without the export, those rows are ``NOT_RUN_DEPENDENCY`` with the mismatch or dependency named: never a fake pass,
never an asserted revision. Native dependency of that dormant path, stated plainly and no wider: step 2's ``memory.py``
and ``cell.py`` import ``core.faiss_store`` from the repo root, so the faiss LIBRARY loads at import time (no index is
built or read); torch and sentence_transformers are imported inside ``E5.__init__`` of step 2's ``encode.py`` and load
only if an ``E5`` is constructed, which the strict fake ``Q4`` never does. CI never takes this path.

Generator (``generate(seed)``): one seeded orthonormal basis of ``DIM`` vectors (Gram-Schmidt over ``random.Random``
normals), so every planted cosine is exact: six topic directions plus one direction for irrelevant situations; per
topic four distinct lessons at cosine ``LESSON_COS`` to the topic (pairwise ``LESSON_COS**2``, below REMEMBER's
``TOPIC_COSINE``); three near-duplicates of lesson t0_l0 at ``DUP_COS`` (above ``PAIR_COSINE``); in topic 1 a newer
lesson whose text claims ``supersedes:`` the older; in topic 2 an ``always``/``never`` contradictory pair; two event
records. Twelve relevant situations (two per topic, cosine ``QUERY_COS`` to the topic, sharing the topic's tokens) and
three irrelevant ones (the seventh direction, tokens never in the corpus). The pre-registration (hypotheses, metrics,
termination rule) is in the author's audit folder; the bench records, it does not judge.

usage: python -B sandbox/mind/synthetic_bench.py --out <dir inside a .codex-audit folder> [--seed N]
       [--remember-export <dir with sandbox/mind/remember and sandbox/mind/step2 of 6cf81dbb>]
Writes ``bench_<seed>.json`` exclusively (never replaces a file) and prints one summary line; exit 2 on a refusal.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _path in (str(ROOT), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402

SCHEMA = "wd.s13i-synthetic-mind-bench.v1"
SEEDS = (20261026, 20261027)
QUESTIONS = ("top3_relevance", "duplicates_displacing", "stale_contradictory_order", "irrelevant_warning",
             "utf8_card_budget", "same_input_determinism")
REMEMBER_QUESTIONS = QUESTIONS[:4]
ARMS = ("v2c", "v2a", "v3t", "v21a")
NOT_RUN = "NOT_RUN_DEPENDENCY"
REMEMBER_REVISION = "6cf81dbb"
DEPENDENCY = (f"REMEMBER code is not on main; the row needs the code dependencies of public Source-only Git revision "
              f"{REMEMBER_REVISION} (sandbox/mind/remember and sandbox/mind/step2) given with --remember-export")
REMEMBER_BLOBS = {  # git blob id of each required file at public revision 6cf81dbb (git rev-parse; derived, not typed)
    "sandbox/mind/remember/remember_v2.py": "d786ef6f9ff0d582681068e04f3269c8e08bd900",
    "sandbox/mind/remember/remember_v3.py": "260f8ea783fe5074f56e6e23c3bf4aa41fedf694",
    "sandbox/mind/remember/remember_v21.py": "a52bb327b0a81a1c00a38f47a3d1aca82af1fc9a",
    "sandbox/mind/remember/prompts.py": "036fe9633febf52f3681e48116d72ea4b9c23095",
    "sandbox/mind/step2/q4.py": "eb457381658dce37b6742ef55f99d35593af7f97",
    "sandbox/mind/step2/corpus.py": "9aa057465f95c12b72ff3a10f7e32215c8b93228",
    "sandbox/mind/step2/memory.py": "dbe927e874d0b42d53c06cdee465d878e4f79dbd",
    "sandbox/mind/step2/cell.py": "8e52cc70cd7926a823c736b734382c5fe2b6c81d",
    "sandbox/mind/step2/run_q4.py": "5fa8f06c12256ef3073e432bae8d262586db4ade",
    "sandbox/mind/step2/encode.py": "5d9cfad31b861e32f0ac8cb14e46aeefb588ef09",
}
REMEMBER_MODULES = tuple(Path(rel).stem for rel in REMEMBER_BLOBS)
EXPORT_MANIFEST = "EXPORT_MANIFEST.json"  # the one tolerated extra entry of an export: a non-.py manifest file at its root
DIM = 64
TOPICS = 6
LESSONS_PER_TOPIC = 4
DUPLICATES = 3
LESSON_COS = 0.94   # lesson to its topic; pairwise 0.8836 < TOPIC_COSINE 0.93: distinct lessons, separate groups
DUP_COS = 0.97      # duplicate to t0_l0 (> PAIR_COSINE 0.95); duplicate to duplicate 0.9409 (> TOPIC_COSINE)
QUERY_COS = 0.95    # situation to its topic
PAIR_COSINE = 0.95  # REMEMBER v2's PAIR_COSINE at 6cf81dbb, restated for the plant check
TOPIC_COSINE = 0.93
SITUATION_TIME = "2026-10-20T00:00:00Z"
FIRST_DAY = (2026, 9, 1)
SUPERSEDE_GAP_DAYS = 10
AS_OF = "2026-10-13T19:00:00Z"  # the JOIN fixtures' night (test_join_card.py)
NOW = "2026-10-13T19:05:00Z"
LEARN = "s13i-bench:learn-v0"
SHOWN = 3
CJK = chr(0x6F22)  # a three-byte UTF-8 character
DROPPED = re.compile(r"\+(\d+) more (picks|PRs) not shown")


class BenchError(ValueError):
    """Fixed messages; the bench never guesses."""


# --- the generator --------------------------------------------------------------------------------------------------

def _unit(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector]


def _basis(rng: random.Random, count: int, dim: int) -> list[list[float]]:
    """``count`` orthonormal vectors by Gram-Schmidt over seeded normals (exact dot products up to float error)."""
    out: list[list[float]] = []
    while len(out) < count:
        vector = [rng.gauss(0.0, 1.0) for _ in range(dim)]
        for other in out:
            dot = sum(a * b for a, b in zip(vector, other))
            vector = [a - dot * b for a, b in zip(vector, other)]
        if math.sqrt(sum(x * x for x in vector)) < 1e-6:
            continue
        out.append(_unit(vector))
    return out


def _mix(cos: float, main: Sequence[float], side: Sequence[float]) -> list[float]:
    sin = math.sqrt(1.0 - cos * cos)
    return _unit([cos * a + sin * b for a, b in zip(main, side)])


def _day(offset: int) -> str:
    import datetime as dt
    return (dt.date(*FIRST_DAY) + dt.timedelta(days=offset)).isoformat()


class Synthetic:
    """The generated memory and situations of one seed (plain Python; vectors are lists of floats)."""

    def __init__(self, seed: int):
        rng = random.Random(seed)
        self.seed = seed
        need = TOPICS + 1 + TOPICS * LESSONS_PER_TOPIC + DUPLICATES + 2 * TOPICS + 2
        if need > DIM:
            raise BenchError("the basis is too small for the plants")
        basis = iter(_basis(rng, need, DIM))
        self.topics = [next(basis) for _ in range(TOPICS)]
        self.irrelevant_direction = next(basis)
        filler = ["run", "check", "before", "the", "push", "lesson"]
        self.records: dict[str, dict[str, Any]] = {}
        self.vectors: dict[str, list[float]] = {}
        self.plants: dict[str, Any] = {}
        day = 0
        for t in range(TOPICS):
            words = [f"t{t}w{j}" for j in range(1, 7)]
            for i in range(LESSONS_PER_TOPIC):
                name = f"t{t}_l{i}"
                text = (f"{words[i]} {words[(i + 1) % 6]}: always run the {words[2]} check before the {words[3]} push "
                        f"{filler[i % len(filler)]} {words[(i + 4) % 6]}")
                if t == 2 and i == 1:
                    text = text.replace("always run", "never run")
                offset = day
                if t == 1 and i == 1:
                    offset = day + SUPERSEDE_GAP_DAYS
                    text += f"\nsupersedes: {self._file('t1_l0', self.records['t1_l0']['day'])}"
                self._add(name, "lesson", t, text, _mix(LESSON_COS, self.topics[t], next(basis)), offset)
                day += 1
        for k in range(DUPLICATES):
            base = self.records["t0_l0"]
            text = base["text"].replace(filler[0], filler[(k + 1) % len(filler)], 1)
            self._add(f"t0_dup{k}", "lesson", 0, text, _mix(DUP_COS, self.vectors["t0_l0"], next(basis)), day)
            day += 1
        for k in range(2):
            self._add(f"t3_ev{k}", "event", 3, f"t3w{k + 1} event about the t3w3 check and the t3w4 push",
                      _mix(LESSON_COS, self.topics[3], next(basis)), day)
            day += 1
        self.plants = {"duplicates_of_t0_l0": [f"t0_dup{k}" for k in range(DUPLICATES)],
                       "superseded_pair": ("t1_l0", "t1_l1"), "contradictory_pair": ("t2_l0", "t2_l1")}
        self.relevant = []
        for t in range(TOPICS):
            for q in range(2):
                words = [f"t{t}w{j}" for j in range(1, 7)]
                text = f"{words[q]} question {words[5 - q]} how should the {words[2]} check be done"
                self.relevant.append({"name": f"s_t{t}_q{q}", "topic": t, "text": text,
                                      "vector": _mix(QUERY_COS, self.topics[t], next(basis))})
        self.irrelevant = [{"name": f"s_irr{k}", "topic": None, "text": f"zq{k}a zq{k}b zq{k}c zq{k}d unrelated matter",
                            "vector": list(self.irrelevant_direction)} for k in range(3)]
        self.names = sorted(self.records)

    @staticmethod
    def _file(name: str, day: str) -> str:
        return f"{name}_{day.replace('-', '')}.md"

    def _add(self, name: str, kind: str, topic: int, text: str, vector: list[float], offset: int) -> None:
        day = _day(offset)
        record = {"name": name, "kind": kind, "topic": topic, "text": text, "day": day,
                  "time_utc": f"{day}T00:00:00.000000Z", "available_utc": f"{day}T12:00:00.000000Z"}
        if kind == "lesson":
            record["file"] = self._file(name, day)
        else:
            record["event_ref"] = f"{day}T10:00:00Z|codex-lead-1|message|s13i-bench-{name}"
        self.records[name] = record
        self.vectors[name] = vector

    def lesson(self, name: str) -> dict[str, Any]:
        return self.records[name]

    def cosine(self, a: str, b: str) -> float:
        return sum(x * y for x, y in zip(self.vectors[a], self.vectors[b]))

    def cosine_to_vector(self, name: str, vector: Sequence[float]) -> float:
        return sum(x * y for x, y in zip(self.vectors[name], vector))

    def situations(self) -> list[dict[str, Any]]:
        return list(self.relevant) + list(self.irrelevant)

    def is_duplicate_pair(self, a: str, b: str) -> bool:
        return a != b and self.cosine(a, b) >= PAIR_COSINE

    def digest(self) -> str:
        body = {"records": self.records, "vectors": {k: [round(x, 12) for x in v] for k, v in self.vectors.items()},
                "situations": [{**s, "vector": [round(x, 12) for x in s["vector"]]} for s in self.situations()]}
        return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def generate(seed: int) -> Synthetic:
    return Synthetic(seed)


# --- plain values and canonical text ---------------------------------------------------------------------------------

def _plain(value: Any) -> Any:
    """JSON-plain copy: numpy scalars and arrays, tuples, sets and frozensets become Python values."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if hasattr(value, "tolist"):
        return _plain(value.tolist())
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return str(value)
    return value


def canonical(value: Any) -> str:
    return json.dumps(_plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- the strict fake Q4 and the REMEMBER engines ---------------------------------------------------------------------

class _View:
    def __init__(self, rows, texts):
        self.healthy, self.reason, self.rows = True, None, rows
        self.collection = type("Collection", (), {})()
        self.collection._texts = texts
        self.collection.count = len(texts)


class _Memory:
    def __init__(self, view, dim):
        self.views = {"cell_0": view}
        self.memory = {"dim": dim}
        self.topology = None  # read only for a quarantined cell; the fake has none


class _StrictEncoder:
    """Returns the vector registered for exactly ``text``; any other text is an error, never an embedding."""

    def __init__(self, registered: Mapping[str, Sequence[float]], np):
        self._np = np
        self._registered = {text: np.asarray(vector, dtype=np.float32) for text, vector in registered.items()}

    def query(self, text: str):
        if text not in self._registered:
            raise BenchError("the strict fake encoder has no vector for this text")
        return self._registered[text]


def git_blob_id(data: bytes) -> str:
    """The git blob id of ``data`` (sha1 over ``blob <size>\\0`` + bytes): files are compared with the revision by it."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _key(path: str | Path) -> str:
    return os.path.normcase(str(Path(path).resolve()))


def verify_export(export: str | Path, expected: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Compare every required file of ``export`` with the registered blob ids BEFORE anything is imported (pure: reads
    bytes, imports nothing, changes nothing). ``ok`` only when every file exists with exactly the registered bytes."""
    expected = REMEMBER_BLOBS if expected is None else expected
    export = Path(export).resolve()
    verified: dict[str, str] = {}
    mismatch: list[str] = []
    missing: list[str] = []
    for rel, want in sorted(expected.items()):
        path = export / rel
        if not path.is_file():
            missing.append(rel)
        elif git_blob_id(path.read_bytes()) == want:
            verified[rel] = want
        else:
            mismatch.append(rel)
    return {"ok": not mismatch and not missing, "verified": verified, "mismatch": mismatch, "missing": missing}


def export_tree_entries(export: str | Path, expected: Mapping[str, str] | None = None) -> list[str]:
    """Every entry (file or directory, relative, ``/``-joined, sorted) under the export root that is NOT one of the
    registered files, one of their parent directories, or the one named non-.py manifest FILE ``EXPORT_MANIFEST`` at the
    root. Pure: lists names, reads no bytes, imports nothing. An empty list means the tree is exactly the registered set.
    Reason: step 2's ``corpus.py`` inserts the export root at ``sys.path[0]``, so an extra ``tools/``, ``core/`` or
    ``waggledance/`` package, any extra ``.py``, a ``__pycache__`` or a ``.pyc`` could shadow this tree unverified."""
    expected = REMEMBER_BLOBS if expected is None else expected
    export = Path(export).resolve()
    allowed: set[str] = set()
    for rel in expected:
        parts = Path(rel).parts
        allowed.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    unexpected: list[str] = []
    for path in sorted(export.rglob("*")):
        rel = path.relative_to(export).as_posix()
        if rel in allowed:
            continue
        if rel == EXPORT_MANIFEST and not EXPORT_MANIFEST.endswith(".py") and path.is_file():
            continue
        unexpected.append(rel)
    return unexpected


def _import_module(name: str):
    """The import seam of ``load_remember`` (tests replace it to stand for a finder or cache serving other code)."""
    return importlib.import_module(name)


def load_remember(export: Path) -> dict[str, Any]:
    """Import the exported ``REMEMBER_REVISION`` modules only after verifying them, in this order, each step a named
    ``BenchError`` before any import or ``sys.path`` change when it fails: (1) every required file must have the registered
    blob id (``verify_export``); (2) the export tree must hold nothing but those files, their directories and the one named
    manifest (``export_tree_entries``); (3) every cached module of the ten names is displaced unconditionally, same-path
    ones included (code already in memory is not the verified bytes, whatever its ``__file__`` says), the displaced names
    recorded; (4) the import runs with bytecode writing off (``sys.dont_write_bytecode``, restored afterwards); (5) every
    imported module must originate from exactly the file registered for ITS name (``q4`` from ``step2/q4.py``, never
    another verified file) and that file must still have the registered bytes. The returned identity is what was verified
    and imported, never a constant. Repo-root packages (tools/core/waggledance) come from this tree. Native dependency,
    stated no wider than the code: step 2's ``memory.py`` and ``cell.py`` import ``core.faiss_store``, so the faiss library
    loads at import time (no index is built or read); torch and sentence_transformers are imported inside ``E5.__init__``
    of step 2's ``encode.py`` and load only if an ``E5`` is constructed, which the strict fake ``Q4`` never does."""
    export = Path(export).resolve()
    expected = dict(REMEMBER_BLOBS)
    verdict = verify_export(export, expected)
    if not verdict["ok"]:
        raise BenchError(f"the export does not match revision {REMEMBER_REVISION}: missing [{', '.join(verdict['missing'])}]"
                         f" mismatch [{', '.join(verdict['mismatch'])}]")
    unexpected = export_tree_entries(export, expected)
    if unexpected:
        raise BenchError(f"the export holds entries outside the registered set of revision {REMEMBER_REVISION}: "
                         f"[{', '.join(unexpected)}]; only the registered files, their directories and {EXPORT_MANIFEST} "
                         f"are allowed; nothing imported")
    by_key = {_key(export / rel): rel for rel in expected}
    by_name = {Path(rel).stem: rel for rel in expected}
    names = tuple(by_name)
    displaced = []
    for name in names:
        if name in sys.modules:
            displaced.append(name)
            del sys.modules[name]  # every cached module of these names, same-path ones included: code already in
            # memory is not the verified bytes, whatever its __file__ says; the fresh import below is the only one trusted
    remember_dir, step2 = export / "sandbox" / "mind" / "remember", export / "sandbox" / "mind" / "step2"
    for _path in (str(step2), str(remember_dir)):
        if _path not in sys.path:
            sys.path.insert(0, _path)
    bytecode_before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no .pyc of the export is written by this import
    try:
        modules = {name: _import_module(name) for name in ("remember_v2", "remember_v3", "remember_v21", "q4")}
    finally:
        sys.dont_write_bytecode = bytecode_before
    origins: dict[str, str] = {}
    for name in names:
        module = modules.get(name, sys.modules.get(name))
        if module is None:
            continue  # a verified file the imported chain did not need
        origin = getattr(module, "__file__", None)
        rel = by_key.get(_key(origin)) if origin else None
        if rel is None:
            raise BenchError(f"imported module {name} originates from {origin!r}, not from the verified export")
        if rel != by_name[name]:
            raise BenchError(f"imported module {name} originates from {rel}, not from its own registered file "
                             f"{by_name[name]}")
        if git_blob_id(Path(origin).read_bytes()) != expected[rel]:
            raise BenchError(f"imported module {name}: {rel} changed after verification")
        origins[name] = rel
    v2, v3, v21, q4_mod = modules["remember_v2"], modules["remember_v3"], modules["remember_v21"], modules["q4"]
    return {"v2": v2, "v3": v3, "v21": v21, "q4": q4_mod, "np": v2.np, "files": verdict["verified"], "origins": origins,
            "displaced": displaced, "versions": {"v2": v2.VERSION, "v3": v3.VERSION, "v21": v21.VERSION}}


def strict_fake_q4(remember: Mapping[str, Any], synth: Synthetic):
    """A step-2 ``Q4`` built without its constructor: synthetic vectors and texts, Q4's own BM25, self-match and
    result code, no encoder model, no FAISS index."""
    q4_mod, np = remember["q4"], remember["np"]
    q4 = q4_mod.Q4.__new__(q4_mod.Q4)
    q4.rule = dict(q4_mod.RULE)
    q4.quarantined = {}
    q4.ids = list(synth.names)
    rows, texts, entries = [], [], []
    for name in q4.ids:
        record = synth.records[name]
        provenance = {"record": name, "view": "d", "available_utc": record["available_utc"],
                      "tier": {"source_type": "synthetic"}}
        if record["kind"] == "lesson":
            provenance["file"] = record["file"]
        else:
            provenance["event_ref"] = record["event_ref"]
        row = {"record_id": name, "kind": record["kind"], "time_utc": record["time_utc"], "provenance": provenance}
        vector = np.asarray(synth.vectors[name], dtype=np.float32)
        entries.append({"cell": "cell_0", "row": row, "text": record["text"], "w": vector, "d": vector})
        rows.append(row)
        texts.append(record["text"])
    q4.entries = entries
    q4.w = np.vstack([entry["w"] for entry in entries])
    q4.d = np.vstack([entry["d"] for entry in entries])
    q4.available = np.array([entry["row"]["provenance"]["available_utc"] for entry in entries])
    q4.lexical = q4_mod.Lexical(texts, q4.rule["bm25_k1"], q4.rule["bm25_b"])
    size = q4.rule["self_match_shingle"]
    q4.joined, q4.shingle_index = [], defaultdict(list)
    for position, entry in enumerate(entries):
        words = q4_mod.WORDS.findall(entry["text"].lower())
        q4.joined.append(" " + " ".join(words) + " ")
        for shingle in q4_mod.shingles(words, size):
            q4.shingle_index[shingle].append(position)
    q4.memory = _Memory(_View(rows, texts), DIM)
    q4.encoder = _StrictEncoder({s["text"]: s["vector"] for s in synth.situations()}, np)
    return q4


def _engines(remember: Mapping[str, Any], q4) -> dict[str, Any]:
    v2, v3, v21 = remember["v2"], remember["v3"], remember["v21"]
    engines = {"v2c": v2.RememberV2(q4), "v2a": v2.RememberV2(q4), "v3t": v3.RememberV3(q4, hop=False),
               "v21a": v21.RememberV21(q4, "v21a")}
    engines["v3t"].set_arm("v3t")
    return engines


def ask(arm: str, engine: Any, situation: Mapping[str, Any]) -> dict[str, Any]:
    """One situation through one arm: the shown lines in order (at most ``SHOWN``), the answer, the status."""
    if arm == "v2c":
        answer = engine.gate(situation["text"], SITUATION_TIME, situation["text"])
        lines = list(answer["results"])
        listed_status = answer["status"]
    else:
        listed = engine.shortlist(situation["text"], SITUATION_TIME, [situation["text"]])
        listed_status = listed["status"]
        lines = list(listed.get("lines") or [])
        chosen = list(range(1, min(SHOWN, len(lines)) + 1))
        answer = engine.apply_picks(listed, {"picks": chosen, "contested": [], "nothing_applies": not chosen})
    shown = lines[:SHOWN]
    return {"status": listed_status, "answer_status": answer["status"], "reason": answer.get("reason"),
            "shown": shown, "all_lines": lines, "answer": answer}


def _ref_name(synth: Synthetic, ref: str) -> str | None:
    for name, record in synth.records.items():
        if record.get("file") == ref or record.get("event_ref") == ref:
            return name
    return None


def remember_rows(synth: Synthetic, remember: Mapping[str, Any] | None, dependency: str | None) -> tuple[list, dict]:
    """The four REMEMBER questions per arm, or NOT_RUN rows; also the engines' answers per situation for Q5."""
    if remember is None:
        return [{"question": q, "code": arm, "status": NOT_RUN, "dependency": dependency, "metrics": {}, "detail": None}
                for q in REMEMBER_QUESTIONS for arm in ARMS], {}
    q4 = strict_fake_q4(remember, synth)
    engines = _engines(remember, q4)
    answers: dict[str, dict[str, Any]] = {}
    rows = []
    for arm, engine in engines.items():
        asked = {s["name"]: ask(arm, engine, s) for s in synth.situations()}
        answers[arm] = {name: _plain(out["answer"]) for name, out in asked.items()}
        # Q1 top3 relevance over the relevant situations
        hits, anys, detail = 0, 0, {}
        for s in synth.relevant:
            names = [_ref_name(synth, line["ref"]) for line in asked[s["name"]]["shown"]]
            topics = [synth.records[n]["topic"] if n else None for n in names]
            hits += bool(topics) and all(t == s["topic"] for t in topics)
            anys += any(t == s["topic"] for t in topics)
            detail[s["name"]] = {"status": asked[s["name"]]["answer_status"], "reason": asked[s["name"]]["reason"],
                                 "shown": names}
        rows.append({"question": "top3_relevance", "code": arm, "status": "RUN", "dependency": None,
                     "metrics": {"hit_at_3": hits / len(synth.relevant), "any_at_3": anys / len(synth.relevant),
                                 "situations": len(synth.relevant)}, "detail": detail})
        # Q2 duplicates displacing distinct lessons (topic 0)
        dup_total, distinct_total, detail = 0, 0, {}
        topic0 = [s for s in synth.relevant if s["topic"] == 0]
        for s in topic0:
            names = [_ref_name(synth, line["ref"]) for line in asked[s["name"]]["shown"]]
            dups = [n for n in names if n and any(synth.is_duplicate_pair(n, m) for m in names if m)]
            distinct = [n for n in names if n and synth.records[n]["topic"] == 0 and n not in dups]
            dup_total += len(dups)
            distinct_total += len(distinct)
            detail[s["name"]] = {"shown": names, "duplicates": dups, "distinct": distinct}
        rows.append({"question": "duplicates_displacing", "code": arm, "status": "RUN", "dependency": None,
                     "metrics": {"duplicates_shown": dup_total, "distinct_shown": distinct_total,
                                 "situations": len(topic0), "shown_per_situation": SHOWN}, "detail": detail})
        # Q3 stale (topic 1) and contradictory (topic 2)
        older, newer = synth.plants["superseded_pair"]
        a, b = synth.plants["contradictory_pair"]
        marked, newer_first, both_super, both_contra, opposed, detail = 0, 0, 0, 0, 0, {}
        for s in [x for x in synth.relevant if x["topic"] == 1]:
            lines = asked[s["name"]]["all_lines"]
            names = [_ref_name(synth, line["ref"]) for line in lines]
            if older in names and newer in names:
                both_super += 1
                line = lines[names.index(older)]
                marked += line.get("superseded_by") == synth.records[newer]["file"]
                newer_first += names.index(newer) < names.index(older)
            detail[s["name"]] = {"listed": names, "superseded_by_of_older": lines[names.index(older)].get("superseded_by")
                                 if older in names else None}
        for s in [x for x in synth.relevant if x["topic"] == 2]:
            lines = asked[s["name"]]["all_lines"]
            names = [_ref_name(synth, line["ref"]) for line in lines]
            if a in names and b in names:
                both_contra += 1
                opposed += any(line.get(key) for line in lines for key in line if "oppose" in str(key))
            detail[s["name"]] = {"listed": names,
                                 "oppose_keys": sorted({str(k) for line in lines for k in line if "oppose" in str(k)})}
        if hasattr(engine, "opposed") and hasattr(remember["v21"], "opposition"):
            # diagnostic detail, not a metric: the detector on the planted pair as stored, and on the same two
            # sentences without their leading label tokens (the label makes the clause heads differ)
            text_a, text_b = synth.records[a]["text"], synth.records[b]["text"]
            strip = lambda text: text.split(": ", 1)[1] if ": " in text else text  # noqa: E731
            detail["opposition_direct"] = remember["v21"].opposition(text_a, text_b, "lesson", "lesson", synth.cosine(a, b))
            detail["opposition_without_lead_label"] = remember["v21"].opposition(strip(text_a), strip(text_b), "lesson",
                                                                                 "lesson", synth.cosine(a, b))
        rows.append({"question": "stale_contradictory_order", "code": arm, "status": "RUN", "dependency": None,
                     "metrics": {"superseded_both_listed": both_super, "superseded_marked": marked,
                                 "newer_first": newer_first, "contradictory_both_listed": both_contra,
                                 "opposition_marked": opposed, "situations_per_pair": 2}, "detail": detail})
        # Q4 irrelevant situations
        warned, shown_total, detail = 0, 0, {}
        for s in synth.irrelevant:
            out = asked[s["name"]]
            shown = len(out["all_lines"])
            is_warning = out["answer_status"] in ("ABSTAIN", "REFUSED") or shown == 0
            warned += is_warning
            shown_total += shown
            detail[s["name"]] = {"status": out["answer_status"], "reason": out["reason"], "lines": shown}
        rows.append({"question": "irrelevant_warning", "code": arm, "status": "RUN", "dependency": None,
                     "metrics": {"warned": warned, "situations": len(synth.irrelevant), "lines_total": shown_total},
                     "detail": detail})
    return rows, answers


# --- JOIN: the card budget -------------------------------------------------------------------------------------------

def _fixtures():
    import test_join_card_delivery as tjd  # the in-repo delivery and lane-view fixtures (real core, synthetic night)
    views = {"case": tjd.case(AS_OF), "many6": tjd.many(6, AS_OF)}
    return {name: (view, jcd.deliver(view, tjd.receipts(), now_utc=NOW)) for name, view in views.items()}


def synthetic_answers(synth: Synthetic) -> dict[str, dict[str, Any]]:
    """The bench's own REMEMBER v2-shaped answers with multibyte text: three picks of 150 CJK characters each."""
    out = {}
    for s in synth.relevant[:4]:
        picks = []
        for n in range(3):
            picks.append({"ref": f"{CJK * 4}_{s['name']}_{n}.md", "kind": "lesson", "time_utc": synth.records["t0_l0"]["time_utc"],
                          "summary": CJK * jc.SUMMARY_CHARS, "contested_with": [], "pair_with": [], "superseded_by": None,
                          "topic": {"group": 1, "records": 3, "first_day": "2026-09-01", "last_day": "2026-09-03"}})
        out[s["name"]] = {"status": "ANSWER", "abstain": False, "reason": None, "degraded": False, "degraded_cells": [],
                          "results": picks, "version": "remember-v2.0", "telemetry": {}}
    return out


def card_rows(synth: Synthetic, answers_by_code: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    fixtures = _fixtures()
    rows = []
    for code, answers in answers_by_code.items():
        cards = refused = over = picks_dropped = prs_dropped = 0
        max_bytes = 0
        reasons: Counter = Counter()
        for situation_name, answer in sorted(answers.items()):
            situation = sha256_text(situation_name)
            for fixture_name, (view, delivery) in fixtures.items():
                for lane in sorted(jc.lv.LANES):
                    try:
                        envelope = jc.remember_envelope(lane, AS_OF, situation, answer)
                        text = jc.card(lane, delivery, view, envelope, learn_ref=LEARN)
                    except jc.JoinCardError as error:
                        refused += 1
                        reasons[str(error)] += 1
                        continue
                    cards += 1
                    size = len(text.encode("utf-8"))
                    max_bytes = max(max_bytes, size)
                    over += size > jc.CARD_BYTES
                    for count, what in DROPPED.findall(text):
                        if what == "picks":
                            picks_dropped += int(count)
                        else:
                            prs_dropped += int(count)
        rows.append({"question": "utf8_card_budget", "code": code, "status": "RUN", "dependency": None,
                     "metrics": {"cards": cards, "max_bytes": max_bytes, "card_bytes_bound": jc.CARD_BYTES,
                                 "over_budget": over, "refused": refused, "dropped_picks": picks_dropped,
                                 "dropped_prs": prs_dropped},
                     "detail": {"refusal_reasons": dict(reasons), "fixtures": sorted(fixtures), "lanes": len(jc.lv.LANES)}})
    return rows


# --- the bench --------------------------------------------------------------------------------------------------------

def _rows(seed: int, remember: Mapping[str, Any] | None, dependency: str | None) -> list[dict[str, Any]]:
    synth = generate(seed)
    rows, answers = remember_rows(synth, remember, dependency)
    answers_by_code = {"join_synthetic": synthetic_answers(synth)}
    for arm, by_situation in answers.items():
        answers_by_code[f"join_with_{arm}"] = {name: answer for name, answer in by_situation.items()
                                               if answer.get("results")}
    rows += card_rows(synth, answers_by_code)
    return _plain(rows)


def run_bench(seed: int, remember_export: str | Path | None = None) -> dict[str, Any]:
    remember, dependency, import_error = None, DEPENDENCY, None
    if remember_export is not None:
        try:
            remember = load_remember(Path(remember_export))
            dependency = None
        except Exception as error:  # the dependency is named, the rows stay NOT_RUN
            import_error = f"{type(error).__name__}: {error}"
            dependency = f"import of the exported {REMEMBER_REVISION} code failed: {import_error}"
    first = _rows(seed, remember, dependency)
    second = _rows(seed, remember, dependency)
    digest_first, digest_second = sha256_text(canonical(first)), sha256_text(canonical(second))
    first.append({"question": "same_input_determinism", "code": "bench", "status": "RUN", "dependency": None,
                  "metrics": {"identical": digest_first == digest_second, "digest": digest_first,
                              "second_digest": digest_second}, "detail": {"runs": 2, "in_process": True}})
    synth = generate(seed)
    code = {"join_card_version": jc.VERSION, "join_card_sha256": hashlib.sha256((HERE / "join_card.py").read_bytes()).hexdigest(),
            "bench_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "remember_export": None if remember is None else {"revision": REMEMBER_REVISION,
                                                                 "verified_blobs": remember["files"],
                                                                 "module_origins": remember["origins"],
                                                                 "versions": remember["versions"],
                                                                 "displaced_modules": remember["displaced"]},
            "remember_import_error": import_error, "python": sys.version.split()[0]}
    return {"schema": SCHEMA, "seed": seed, "generator_digest": synth.digest(), "code": code, "rows": first,
            "constants": {"dim": DIM, "topics": TOPICS, "lessons_per_topic": LESSONS_PER_TOPIC, "duplicates": DUPLICATES,
                          "lesson_cos": LESSON_COS, "dup_cos": DUP_COS, "query_cos": QUERY_COS, "shown": SHOWN}}


def summary_line(out: Mapping[str, Any]) -> str:
    parts = [f"s13i-bench seed={out['seed']}"]
    for row in out["rows"]:
        if row["status"] == "RUN":
            metrics = " ".join(f"{k}={v}" for k, v in row["metrics"].items() if k not in ("digest", "second_digest"))
            parts.append(f"{row['question']}/{row['code']}: {metrics}")
        else:
            parts.append(f"{row['question']}/{row['code']}: {row['status']}")
    return "; ".join(parts)


NOT_AUDIT = "the output directory is not inside a .codex-audit folder"
NOT_A_DIR = "the output directory does not exist"
EXISTS = "the output file already exists; nothing is replaced"


def refusal_for(out_dir: Path, seed: int) -> str | None:
    """Why the CLI refuses to write, or None: the folder rule first (a pure path check), then the filesystem."""
    out_dir = Path(out_dir)
    if ".codex-audit" not in out_dir.parts:
        return NOT_AUDIT
    if not out_dir.is_dir():
        return NOT_A_DIR
    if (out_dir / f"bench_{seed}.json").exists():
        return EXISTS
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=SEEDS[0])
    parser.add_argument("--remember-export", type=Path)
    args = parser.parse_args(argv)
    out_dir = args.out.resolve()
    refusal = refusal_for(out_dir, args.seed)
    if refusal is not None:
        print(f"refused: {refusal}")
        return 2
    target = out_dir / f"bench_{args.seed}.json"
    result = run_bench(args.seed, args.remember_export)
    with open(target, "x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(_plain(result), ensure_ascii=False, sort_keys=True, indent=1) + "\n")
    print(summary_line(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
