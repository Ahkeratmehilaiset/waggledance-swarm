"""REMEMBER v2 over the verified hex memory: the joiner's rewrite, a union shortlist, the joiner's judgement and
same-situation marks. Sandbox, advisory, never a gate.

Why. v1 (q4-rule-v1, frozen 994bdb8b) answers only with records that all three views (e5 on text, e5 on the one-line
summary, BM25) rank in their top 3. On the fresh blinded sets (RCO2 grade F84D60D4) it abstained on 4/4 negatives,
but recall@3 was 3/9. Post hoc, three misses sat next to the gate and three were vocabulary gaps (best ranks 10-41,
the situation and the lesson use different words). RCO1's conflict probe (ADBE5B30) showed a newer opposite lesson
ranked first in 14/20 answers and never marked.

Arms, all frozen before any new blinded case is opened (``ARMS``):

* ``v2c`` (Grok 9e936081, the single change): the joining agent first rewrites the situation as one lesson-style line
  (``prompts.REWRITE``). The UNCHANGED v1 gate then runs on that line. Applicability (time and self-match) is still
  judged on the original situation.
* ``v2a``: the union of each view's top ``DEPTH`` for the original situation, at most ``CAP`` lines (ref, date,
  summary), in reciprocal-rank-fusion order. The joining agent picks at most 3 lines that apply, or answers that
  nothing applies (``prompts.JUDGE``).
* ``v2ca`` (the pre-registered PRIMARY product candidate): as ``v2a``, over the union for BOTH the original situation
  and the rewrite.

Topic groups against flooding (v2a and v2ca; RCO1's conflict probe v1.1, result 1E908C97: three differently worded
opposite lessons filled every top 3, so the true lesson was hidden while an opposite was shown in 5/20 answers).
Two records are on the same topic when their summary vectors are at least ``TOPIC_COSINE`` alike.

* Each view contributes its top ``DEPTH`` TOPICS for each query, not its top ``DEPTH`` records. A record on the same
  topic as one already counted in that walk adds no depth, so copies cannot crowd other topics out.
* The pool is grouped in fused order. Each pool record not yet grouped opens a group with every applicable record
  (not only pool records) on its topic that no earlier group holds. A group shows at most ``GROUP_LINES`` lines:
  its head (the best fused record), its earliest and its latest record, in time order. However many copies say
  the same thing, a group costs at most three lines, and the oldest and the newest record on the topic are always
  shown together.
* Every line of a group with more than one record carries ``[TOPIC g: n records, first..last day]``: the shown
  records may disagree, and the memory never picks a winner. Groups are listed whole or not at all.

Marks, in every arm. Two shown records of DIFFERENT groups whose summary vectors are at least ``PAIR_COSINE`` alike
are a same-situation PAIR. A lesson whose text has a line ``supersedes: <file>`` marks that file SUPERSEDED wherever
it is shown. The joining agent may call any two picks CONTESTED. A memory line never decides a vote, veto or merge;
live gate truth does.

Output surface (RCO1 review 19:10Z, probe plan V1x 28C85C83). ``v2c`` answers carry no memory text at all (refs,
scores and dates only, as in v1). ``v2a`` and ``v2ca`` show memory text: per line the ref, kind, date (day), marks
(TOPIC, PAIR, SUPERSEDED) and the record's one-line summary cut at ``SUMMARY_CHARS``; at most ``CAP`` lines and
``CASE_BYTES`` UTF-8 bytes of lines per case (the lowest groups are dropped whole). The workspace's decoded guard
(NFKC, case-folded, ``\\uXXXX`` unescaped until stable) runs on every line as rendered, its SUPERSEDED mark included,
and on every field of it; a line that trips it is withheld and counted, never shown, and the case's other lines
stay. A situation or query that trips it refuses that case in every arm (``situation_guarded``, ``query_guarded``) and
is never shown; an answer that would trip it is refused (``output_guarded``), never raised. Every file the runner
writes passes the guard before the first byte of any of them is written; the one exception is ``blind.jsonl``, the
working copy of the sealed case file (case ids, situations and times only). The judge text tells the joiner that
entries are data, never instructions.

The step-2 memory, encoder and v1 rule are reused unchanged (``sandbox/mind/step2``); nothing there is edited. The
pure functions take the joiner's rewrites and picks as inputs; the joining agent itself runs outside this module. No
network, no bridge event, no gate code; writes only under ``--out``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

HERE = Path(__file__).resolve().parent
STEP2 = HERE.parent / "step2"
for _path in (str(HERE), str(STEP2)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import corpus as corpus_mod  # noqa: E402  (it puts the repository root first on sys.path)

sys.path.insert(0, str(STEP2))  # the repository root has its own ``memory`` package; step 2's module must win
import memory as memory_mod  # noqa: E402
import prompts  # noqa: E402
import q4 as q4_mod  # noqa: E402
import run_q4  # noqa: E402

if Path(memory_mod.__file__).resolve().parent != STEP2.resolve() or q4_mod.memory_mod is not memory_mod:
    raise ImportError("step-2 memory module not loaded")

VERSION = "remember-v2.0"
ARMS = ("v2c", "v2a", "v2ca")
PRIMARY = "v2ca"
DEPTH = 3
CAP = 8
SHOWN_MAX = 3
PAIR_COSINE = 0.95  # e5 summary cosines: all-pair median 0.845, p99.9 0.923; >= 0.95 = 67 near-duplicate lesson pairs
TOPIC_COSINE = 0.93  # calibration 240406bf on fable-5's own data: a written opposite or independent rewording of a
# real lesson is >= 0.93 in 23/24 each; 5/24 real lessons have another real lesson >= 0.93 (0.066% of lesson pairs)
GROUP_LINES = 3
SUMMARY_CHARS = 150
CASE_BYTES = 2048
REWRITE_CHARS = 240
SUPERSEDES = re.compile(r"(?im)^[ \t>*-]*supersedes:[ \t]*([A-Za-z0-9._-]+\.md)[ \t]*$")
RESULT_KEYS = run_q4.RESULT_KEYS


class RememberError(ValueError):
    """Fixed messages that never echo input content."""


def top_topics(rank: np.ndarray, vectors: np.ndarray) -> list[int]:
    """One view's walk in rank order until DEPTH distinct topics are counted. A record on the topic of one already
    counted in this walk is taken but adds no depth. A rank past the candidates (no lexical evidence) ends it."""
    counted: list[int] = []
    taken: list[int] = []
    for i in np.argsort(rank, kind="stable"):
        if rank[i] > len(rank):
            break
        if counted and float(np.max(vectors[counted] @ vectors[i])) >= TOPIC_COSINE:
            taken.append(int(i))
            continue
        if len(counted) == DEPTH:
            break
        counted.append(int(i))
        taken.append(int(i))
    return taken


def one_line(text: str, limit: int = SUMMARY_CHARS) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit - 3].rstrip() + "..."


def guarded(value: Any) -> bool:
    """True when any key or string inside ``value`` holds a guarded word (the workspace's decoded check)."""
    try:
        corpus_mod.ws.guard_object(value)
    except corpus_mod.ws.WorkspaceError:
        return True
    return False


class RememberV2:
    """The query side of REMEMBER v2 over one loaded step-2 ``Q4`` (its memory, encoder and v1 rule)."""

    def __init__(self, q4: Any):
        self.q4 = q4
        summaries: dict[str, str] = {}
        for view in q4.memory.views.values():
            if not view.healthy:
                continue
            for position, row in enumerate(view.rows):
                if row["provenance"]["view"] == "d":
                    summaries[row["provenance"]["record"]] = view.collection._texts[position]
        self.summary = [summaries.get(record_id, "") for record_id in q4.ids]
        self.supersedes: dict[str, str] = {}
        for entry in q4.entries:
            if entry["row"]["kind"] != "lesson":
                continue
            for older in SUPERSEDES.findall(entry["text"]):
                newer = entry["row"]["provenance"]["file"]
                if older != newer:
                    self.supersedes.setdefault(older, newer)

    # --- the shared steps ------------------------------------------------------------------------------------------

    def applicable(self, situation: str, situation_time_utc: str):
        """(candidate positions, telemetry), or (None, reason) for a refused question. Rule v1, unchanged."""
        when = corpus_mod.parse_utc(situation_time_utc) if isinstance(situation_time_utc, str) else None
        if not isinstance(situation, str) or not situation.strip():
            return None, "situation_empty"
        if when is None:
            return None, "situation_time_invalid"
        if guarded(situation):
            return None, "situation_guarded"  # refused in every arm; the text is never shown
        import datetime as dt
        limit = corpus_mod.utc_text(when - dt.timedelta(seconds=self.q4.rule["guard_band_s"]))
        fresh = self.q4.available < limit
        self_match = self.q4._self_matches(situation)
        candidate = fresh & ~self_match
        telemetry = {"records": len(self.q4.entries), "candidates": int(candidate.sum()),
                     "excluded_not_before_situation": int((~fresh).sum()),
                     "excluded_self_match": int((fresh & self_match).sum())}
        return np.flatnonzero(candidate), telemetry

    def ranks(self, text: str, index: np.ndarray) -> dict[str, np.ndarray]:
        """Rank 1..n of every candidate in each view for ``text``; no shared token is no lexical evidence."""
        query = self.q4.encoder.query(text)
        views = {"w": self.q4.w[index] @ query, "d": self.q4.d[index] @ query,
                 "b": self.q4.lexical.scores(text)[index]}
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

    # --- the arms --------------------------------------------------------------------------------------------------

    def gate(self, situation: str, situation_time_utc: str, query: str) -> dict[str, Any]:
        """v2c: the v1 three-view top-3 gate on ``query``; applicability on the original situation."""
        started = time.perf_counter()
        index, telemetry = self.applicable(situation, situation_time_utc)
        if index is None:
            return self._answer("REFUSED", telemetry, [], {}, started)
        if not isinstance(query, str) or not query.strip():
            return self._answer("REFUSED", "rewrite_empty", [], telemetry, started)
        if guarded(query):
            return self._answer("REFUSED", "query_guarded", [], telemetry, started)
        if not len(index):
            return self._answer("ABSTAIN", "empty_cell", [], telemetry, started)
        ranks = self.ranks(query, index)
        depth = self.q4.rule["agree_depth"]
        fused = sum(1.0 / (self.q4.rule["rrf_k"] + ranks[name]) for name in ("w", "d", "b"))
        agreed = np.flatnonzero(np.all([ranks[name] <= depth for name in ("w", "d", "b")], axis=0))
        agreed = sorted(agreed, key=lambda i: (-fused[i], self.q4.ids[index[i]]))[:SHOWN_MAX]
        results = [self._result(int(index[i]), float(fused[i]), {name: int(ranks[name][i]) for name in "wdb"},
                                summary=False) for i in agreed]  # the joiner sees no memory text in this arm
        if not results:
            return self._answer("ABSTAIN", "no_view_consensus", [], telemetry, started)
        return self._answer("ANSWER", None, self._marked(results), telemetry, started)

    def topic_groups(self, index: np.ndarray, pool: Sequence[int], vectors: np.ndarray) -> list[dict[str, Any]]:
        """Pool records in fused order open groups over ALL applicable records on their topic (module docstring)."""
        grouped = np.zeros(len(index), dtype=bool)
        groups = []
        for head in pool:
            if grouped[head]:
                continue
            members = np.flatnonzero((vectors @ vectors[head] >= TOPIC_COSINE) & ~grouped)
            members = np.union1d(members, [head]).astype(int)
            grouped[members] = True
            when = {int(j): self._when(int(index[j])) for j in members}
            ordered = sorted(when, key=when.get)
            show = sorted({int(head), ordered[0], ordered[-1]}, key=when.get)
            groups.append({"head": int(head), "members": ordered, "show": show})
        return groups

    def _when(self, position: int) -> tuple:
        """Time order inside a group: the claimed time (a lesson's file-name day), then available_utc, then the id."""
        row = self.q4.entries[position]["row"]
        return (corpus_mod.parse_utc(row["time_utc"]), corpus_mod.parse_utc(row["provenance"]["available_utc"]),
                self.q4.ids[position])

    def shortlist(self, situation: str, situation_time_utc: str, queries: Sequence[str]) -> dict[str, Any]:
        """v2a / v2ca: topic groups over each view's top DEPTH topics for every query, at most CAP lines."""
        started = time.perf_counter()
        index, telemetry = self.applicable(situation, situation_time_utc)
        if index is None:
            return {"status": "REFUSED", "reason": telemetry, "lines": []}
        if any(not isinstance(text, str) or not text.strip() for text in queries) or not queries:
            return {"status": "REFUSED", "reason": "query_empty", "lines": []}
        if guarded(list(queries)):
            return {"status": "REFUSED", "reason": "query_guarded", "lines": []}
        if not len(index):
            return {"status": "EMPTY", "reason": "empty_cell", "lines": [], "telemetry": telemetry}
        vectors = self.q4.d[index]
        fused = np.zeros(len(index))
        member = np.zeros(len(index), dtype=bool)
        all_ranks = []
        for text in queries:
            ranks = self.ranks(text, index)
            all_ranks.append(ranks)
            for name in ("w", "d", "b"):
                fused += 1.0 / (self.q4.rule["rrf_k"] + ranks[name])
                member[top_topics(ranks[name], vectors)] = True
        pool = sorted(np.flatnonzero(member), key=lambda i: (-fused[i], self.q4.ids[index[i]]))
        groups = self.topic_groups(index, pool, vectors)
        lines: list[dict[str, Any]] = []
        withheld, stopped_by = 0, None
        for number, group in enumerate(groups, 1):
            members = group["members"]
            days = (self.q4.entries[index[members[0]]]["row"]["time_utc"][:10],
                    self.q4.entries[index[members[-1]]]["row"]["time_utc"][:10])
            batch = []
            for i in group["show"]:
                line = self._result(int(index[i]), float(fused[i]),
                                    {f"{name}{qn}": int(ranks[name][i]) for qn, ranks in enumerate(all_ranks)
                                     for name in ("w", "d", "b")})
                line["topic"] = {"group": number, "records": len(members), "first_day": days[0],
                                 "last_day": days[1], "head": i == group["head"]}
                line["superseded_by"] = self._superseded_by(line)  # its mark is part of the guarded line
                if guarded(line) or corpus_mod.ws._has_marker(line_text(1, line)):
                    withheld += 1  # the decoded guard runs on the rendered line itself, before anyone sees it
                    continue
                batch.append(line)
            trial = self._marked([dict(line) for line in lines + batch])
            if len(trial) > CAP or rendered_bytes(trial) > CASE_BYTES:
                stopped_by = "cap" if len(trial) > CAP else "bytes"
                break  # a group is listed whole or not at all
            lines = trial
        telemetry = dict(telemetry, union=int(member.sum()), groups=len(groups),
                         grouped_records=int(sum(len(group["members"]) for group in groups)),
                         withheld_by_guard=withheld, stopped_by=stopped_by, line_bytes=rendered_bytes(lines),
                         ms=round((time.perf_counter() - started) * 1000, 1))
        return {"status": "LISTED" if lines else "EMPTY", "reason": None if lines else "nothing_listable",
                "lines": lines, "telemetry": telemetry}

    def apply_picks(self, listed: Mapping[str, Any], picks: Any) -> dict[str, Any]:
        """The joiner's picks over one shortlist -> an answer. Invalid picks are a refusal, never a guess."""
        started = time.perf_counter()
        if listed["status"] == "REFUSED":
            return self._answer("REFUSED", listed["reason"], [], {}, started)
        if listed["status"] == "EMPTY":
            return self._answer("ABSTAIN", listed["reason"], [], listed.get("telemetry", {}), started)
        lines = listed["lines"]
        if not isinstance(picks, Mapping) or set(picks) - {"picks", "contested", "nothing_applies"} \
                or not isinstance(picks.get("picks"), list) or not isinstance(picks.get("nothing_applies"), bool):
            return self._answer("REFUSED", "picks_unreadable", [], listed.get("telemetry", {}), started)
        chosen = picks["picks"]
        if len(chosen) > SHOWN_MAX or len(set(map(repr, chosen))) != len(chosen) \
                or any(type(n) is not int or not 1 <= n <= len(lines) for n in chosen) \
                or picks["nothing_applies"] != (not chosen):
            return self._answer("REFUSED", "picks_invalid", [], listed.get("telemetry", {}), started)
        contested = picks.get("contested") or []
        if not isinstance(contested, list) or any(
                not isinstance(pair, list) or len(pair) != 2 or any(n not in chosen for n in pair) for pair in contested):
            return self._answer("REFUSED", "contested_invalid", [], listed.get("telemetry", {}), started)
        if not chosen:
            return self._answer("ABSTAIN", "nothing_applies", [], listed.get("telemetry", {}), started)
        results = [dict(lines[n - 1]) for n in chosen]
        for result in results:
            group = (result.get("topic") or {}).get("group")
            result["topic_others"] = [line["ref"] for line in lines if group is not None and line["ref"] != result["ref"]
                                      and (line.get("topic") or {}).get("group") == group]
        refs = {n: lines[n - 1]["ref"] for n in chosen}
        for result in results:
            result["contested_with"] = sorted(refs[b] if refs[a] == result["ref"] else refs[a]
                                              for a, b in contested if result["ref"] in (refs[a], refs[b]))
        return self._answer("ANSWER", None, self._marked(results), listed.get("telemetry", {}), started)

    # --- output ----------------------------------------------------------------------------------------------------

    def _result(self, position: int, fused: float, ranks: Mapping[str, int], summary: bool = True) -> dict[str, Any]:
        base = self.q4._result(position, 0.0, fused, {})
        base["score"] = round(fused, 6)
        base["view_ranks"] = dict(ranks)
        if summary:
            base["summary"] = one_line(self.summary[position])
        base["position"] = position
        return base

    def _superseded_by(self, result: Mapping[str, Any]) -> str | None:
        return self.supersedes.get(result["ref"]) if result["kind"] == "lesson" else None

    def _marked(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """PAIR marks between shown records and SUPERSEDED marks from ``supersedes:`` lines."""
        for result in results:
            result.setdefault("contested_with", [])
            result["pair_with"] = []
            result["superseded_by"] = self._superseded_by(result)
        for a in range(len(results)):
            for b in range(a + 1, len(results)):
                pa, pb = results[a]["position"], results[b]["position"]
                group_a, group_b = (results[a].get("topic") or {}).get("group"), (results[b].get("topic") or {}).get("group")
                if group_a is not None and group_a == group_b:
                    continue  # the TOPIC mark already says it
                if float(self.q4.d[pa] @ self.q4.d[pb]) >= PAIR_COSINE:
                    results[a]["pair_with"].append(results[b]["ref"])
                    results[b]["pair_with"].append(results[a]["ref"])
        return results

    def _answer(self, status, reason, results, telemetry, started) -> dict[str, Any]:
        degraded = [{"cell": cell_id, "reason": code,
                     "ring1": sorted(self.q4.memory.topology.get_neighbors(cell_id, max_ring=1))}
                    for cell_id, code in sorted(self.q4.quarantined.items())]
        if degraded and status in ("ANSWER", "ABSTAIN"):
            status, reason = "DEGRADED", "cell_quarantined"
            for result in results:
                result["fallback"] = True
        out = {"status": status, "abstain": status == "ABSTAIN", "reason": reason, "degraded": bool(degraded),
               "degraded_cells": degraded, "results": results, "version": VERSION,
               "telemetry": dict(telemetry or {}, ms=round((time.perf_counter() - started) * 1000, 1))}
        if guarded(out):  # refused, never raised: one guarded record must not stop the other cases
            out.update(status="REFUSED", abstain=False, reason="output_guarded", results=[])
            corpus_mod.ws.guard_object(out)
        return out


# --- the joiner's text: rendering and reading ---------------------------------------------------------------------

def line_text(n: int, line: Mapping[str, Any]) -> str:
    """One shortlist line exactly as the joiner reads it: number, ref, kind, day, marks and the cut summary."""
    marks = ""
    topic = line.get("topic")
    if topic and topic["records"] > 1:
        marks += f" [TOPIC {topic['group']}: {topic['records']} records, {topic['first_day']}..{topic['last_day']}]"
    if line.get("pair_with"):
        marks += f" [PAIR with {', '.join(line['pair_with'])}]"
    if line.get("superseded_by"):
        marks += f" [SUPERSEDED by {line['superseded_by']}]"
    return f"[{n}] {line['ref']} ({line['kind']}, {line['time_utc'][:10]}){marks}: {line['summary']}"


def rendered_bytes(lines: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(line_text(n, line).encode("utf-8")) + 1 for n, line in enumerate(lines, 1))


def situation_text(situation: Any) -> str:
    """The situation as the joining agent reads it; a guarded one is withheld (the case is refused in every arm)."""
    return "withheld (guarded word)" if guarded(situation) else one_line(situation or "", 2000)


def render_shortlist(case_id: str, situation: str, listed: Mapping[str, Any]) -> str:
    """What the joining agent reads for one case: the situation and the numbered lines."""
    lines = [f"CASE {case_id}", f"SITUATION: {situation_text(situation)}"]
    if listed["status"] != "LISTED":
        lines.append(f"SHORTLIST: none ({listed['reason']})")
    lines += [line_text(n, line) for n, line in enumerate(listed["lines"], 1)]
    text = "\n".join(lines) + "\n"
    if corpus_mod.ws._has_marker(text):
        raise RememberError("a guarded word is in a shortlist; nothing is written")
    return text


def read_rewrites(data: Any, ids: Sequence[str]) -> dict[str, str]:
    """``{"rewrites": [{"id", "line"}]}`` -> {id: line}; every id exactly once, one line each, at most REWRITE_CHARS."""
    rows = data.get("rewrites") if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        raise RememberError("rewrites unreadable")
    out = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {"id", "line"} or not isinstance(row["line"], str) \
                or row["id"] in out or row["id"] not in ids:
            raise RememberError("rewrites invalid")
        out[row["id"]] = one_line(row["line"], REWRITE_CHARS)
    if set(out) != set(ids):
        raise RememberError("rewrites incomplete")
    return out


def read_picks(data: Any, ids: Sequence[str]) -> dict[str, Any]:
    """``{"cases": [{"id", "picks", "contested", "nothing_applies"}]}`` -> {id: picks}; every id exactly once."""
    rows = data.get("cases") if isinstance(data, Mapping) else None
    if not isinstance(rows, list):
        raise RememberError("picks unreadable")
    out = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("id") not in ids or row["id"] in out:
            raise RememberError("picks invalid")
        out[row["id"]] = {key: value for key, value in row.items() if key != "id"}
    if set(out) != set(ids):
        raise RememberError("picks incomplete")
    return out


# --- runner -------------------------------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract(case_id: str, answer: Mapping[str, Any]) -> dict[str, Any]:
    obj = {"id": case_id, "status": answer["status"], "abstain": bool(answer["abstain"]),
           "degraded": bool(answer.get("degraded")), "fallback": bool(answer.get("degraded")),
           "crash": answer["status"] == "CRASH",
           "results": [{key: result[key] for key in RESULT_KEYS} for result in answer["results"][:SHOWN_MAX]]}
    if answer.get("reason"):
        obj["reason"] = answer["reason"]
    return obj


def jsonl_text(rows: Sequence[Mapping[str, Any]]) -> str:
    return "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_outputs(files: Mapping[Path, str], unguarded: Sequence[Path] = ()) -> None:
    """Write the files only after every one of them passed the decoded guard, so a trip writes nothing. ``unguarded``
    names the working copy of the sealed case file (case text only). Each written file must hash as its text did."""
    if any(path not in unguarded and corpus_mod.ws._has_marker(text) for path, text in files.items()):
        raise RememberError("a guarded word is in an output; nothing is written")
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        if sha256_file(path) != text_sha256(text):
            raise RememberError("a written file does not hash as its text")


def source_blobs() -> dict[str, str]:
    files = sorted(HERE.glob("*.py")) + sorted(STEP2.glob("*.py"))
    return {f"{path.parent.name}/{path.name}": run_q4.git_blob_id(path) for path in files}


def cmd_prepare(args) -> int:
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    blind = [{"id": case_id, "situation": situation, "time": when}
             for case_id, situation, when in run_q4.blind_cases(data, args.format)]
    out = args.out
    blind_text = jsonl_text(blind)
    rewrite_text = prompts.REWRITE + "\n\n" + "".join(f"CASE {row['id']}\nSITUATION: {situation_text(row['situation'])}"
                                                      "\n\n" for row in blind)
    manifest = {"cases": str(args.cases), "cases_sha256": sha256_file(args.cases),
                "blind_sha256": text_sha256(blind_text), "rewrite_input_sha256": text_sha256(rewrite_text),
                "format": args.format, "prompts": prompts.hashes(), "version": VERSION}
    write_outputs({out / "blind.jsonl": blind_text, out / "rewrite-input.md": rewrite_text,
                   out / "prepare-manifest.json": json.dumps(manifest, indent=1, sort_keys=True) + "\n"},
                  unguarded=(out / "blind.jsonl",))
    print(json.dumps({"cases": len(blind), "blind_sha256": manifest["blind_sha256"]}))
    return 0


def _load_engine(memory_dir: Path):
    import encode as encode_mod
    return RememberV2(q4_mod.Q4(memory_mod.Memory(memory_dir), encode_mod.E5()))


def cmd_shortlists(args) -> int:
    out = args.out
    blind = [json.loads(line) for line in (out / "blind.jsonl").read_text(encoding="utf-8").splitlines()]
    ids = [row["id"] for row in blind]
    rewrites = read_rewrites(json.loads(args.rewrites.read_text(encoding="utf-8")), ids)
    engine = _load_engine(args.memory)
    gate_rows, gate_diag, judge_text = [], [], {"v2a": [prompts.JUDGE, ""], "v2ca": [prompts.JUDGE, ""]}
    lists = {"v2a": [], "v2ca": []}
    for row in blind:
        rewrite = rewrites[row["id"]]
        answer = engine.gate(row["situation"], row["time"], rewrite)
        gate_rows.append(contract(row["id"], answer))
        gate_diag.append({"id": row["id"], "answer": answer, "rewrite": None if guarded(rewrite) else rewrite})
        for arm, queries in (("v2a", [row["situation"]]), ("v2ca", [row["situation"], rewrite])):
            listed = engine.shortlist(row["situation"], row["time"], queries)
            lists[arm].append({"id": row["id"], "listed": listed})
            judge_text[arm].append(render_shortlist(row["id"], row["situation"], listed))
    files = {"v2c/outputs.jsonl": jsonl_text(gate_rows), "v2c/diagnostics.jsonl": jsonl_text(gate_diag)}
    for arm in ("v2a", "v2ca"):
        files[f"{arm}/shortlists.jsonl"] = jsonl_text(lists[arm])
        files[f"{arm}/judge-input.md"] = "\n".join(judge_text[arm])
    hashes = {name: text_sha256(text) for name, text in files.items()}
    manifest = {"version": VERSION, "memory_sha256": sha256_file(args.memory / "memory.json"),
                "rewrites_sha256": sha256_file(args.rewrites), "outputs": hashes, "code_git_blobs": source_blobs(),
                "prompts": prompts.hashes(), "depth": DEPTH, "cap": CAP, "pair_cosine": PAIR_COSINE,
                "topic_cosine": TOPIC_COSINE, "group_lines": GROUP_LINES, "case_bytes": CASE_BYTES}
    files["shortlists-manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    write_outputs({out / name: text for name, text in files.items()})
    print(json.dumps({"cases": len(blind), "outputs": hashes}, indent=1))
    return 0


def cmd_compose(args) -> int:
    out = args.out
    blind = [json.loads(line) for line in (out / "blind.jsonl").read_text(encoding="utf-8").splitlines()]
    ids = [row["id"] for row in blind]
    engine = _load_engine(args.memory)
    files, hashes = {}, {}
    for arm, path in (("v2a", args.picks_v2a), ("v2ca", args.picks_v2ca)):
        if path is None:
            continue
        picks = read_picks(json.loads(path.read_text(encoding="utf-8")), ids)
        listed = {json.loads(line)["id"]: json.loads(line)["listed"]
                  for line in (out / arm / "shortlists.jsonl").read_text(encoding="utf-8").splitlines()}
        rows, diag = [], []
        for case_id in ids:
            answer = engine.apply_picks(listed[case_id], picks[case_id])
            rows.append(contract(case_id, answer))
            diag.append({"id": case_id, "answer": answer, "picks": picks[case_id]})
        files[f"{arm}/outputs.jsonl"] = jsonl_text(rows)
        files[f"{arm}/diagnostics.jsonl"] = jsonl_text(diag)
        hashes[f"{arm}/picks_sha256"] = sha256_file(path)
    hashes.update({name: text_sha256(text) for name, text in files.items()})
    manifest = {"version": VERSION, "memory_sha256": sha256_file(args.memory / "memory.json"), "outputs": hashes,
                "code_git_blobs": source_blobs(), "prompts": prompts.hashes()}
    files["compose-manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    write_outputs({out / name: text for name, text in files.items()})
    print(json.dumps(hashes, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--cases", type=Path, required=True)
    prep.add_argument("--format", choices=("fresh", "testset"), default="fresh")
    prep.add_argument("--out", type=Path, required=True)
    short = sub.add_parser("shortlists")
    short.add_argument("--memory", type=Path, required=True)
    short.add_argument("--rewrites", type=Path, required=True)
    short.add_argument("--out", type=Path, required=True)
    comp = sub.add_parser("compose")
    comp.add_argument("--memory", type=Path, required=True)
    comp.add_argument("--picks-v2a", type=Path)
    comp.add_argument("--picks-v2ca", type=Path)
    comp.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return {"prepare": cmd_prepare, "shortlists": cmd_shortlists, "compose": cmd_compose}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
