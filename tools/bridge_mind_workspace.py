# SPDX-License-Identifier: BUSL-1.1
"""Bridge mind reflective workspace v0: fills the reflection fields of one ``wd.bridge-mind.v1`` snapshot.

Dormant and unwired. The projector core (``tools/wd_bridge_mind.py``) emits a snapshot with ``source``,
``prs``, ``lanes`` and ``facts`` and empty reflection fields. This workspace is a pure function of that
snapshot (and, optionally, the previous one) and fills only:

* ``tensions``: mechanical contradictions, each with a rule id, the agents it names, and the refs of the
  facts it came from. The ``tension_id`` is structural (sha256 of rule, subject, claim and observation),
  so new evidence for the same contradiction keeps its id; against a previous snapshot each tension is
  ``new`` or ``persisting``. Rules:

  - ``claim_overlap``: live write claims of two agents overlap.
  - ``stale_vote``: a vote bound to a head that is not the PR head. It never counts; it only shows here.
  - ``expired_claim``: a lease that ended at or before ``as_of``. An expired claim is not completed work.
  - ``unacknowledged_command``: an expected responder of an exact request has no bound reply after the
    grace time.
  - ``coverage_unknown``: the source does not claim complete coverage, so nothing here is complete.
  - ``input_blocked``: the projector blocked a malformed canonical row; only its fixed reason and offset.
  - ``refused_input``: a fact without a valid event ref, or with a value outside the projection
    allowlist. It is named by its position only and is never used.

* ``attention``: one ordered, bounded list of suggestions. A PR reported CI-green with no veto whose only
  missing element is one vote comes first (the last missing vote goes first), then PRs that need only
  the operator signature, then reported CI-green PRs missing several votes, then high and other tensions.
  A suggestion is never a clearance.
* ``next_question``: one bounded question per lane and one global question, each from its first
  attention item, or ``UNKNOWN`` when the snapshot does not support one. A lane without an item is
  ``UNKNOWN``, never "idle": absence of a recent event is not proof of anything.

Rules of the derivation:

* Every tension, attention item and question carries refs: 64-hex event refs, or ``UNKNOWN`` only where
  the input has none to give (refused input, unknown coverage, a blocked row, an unsupported question).
  An output item without refs fails the whole run, so nothing is written.
* Event time only. "Now" is ``source.as_of_utc``; nothing reads the wall clock. Facts whose TTL has ended
  at ``as_of`` are not used. The same snapshot gives the same bytes, whatever the order of its lists.
* A strict projection allowlist: only identifiers (agents, tasks, requests, slots, elements, paths, heads)
  that match fixed patterns are copied, never free text or payloads. Every input string is checked
  after decoding; one that holds a guarded private-marker word, plain or backslash-u escaped, makes its
  fact a refused input. A guarded word that still reaches the output fails the run.
* ``authority`` must be ``none``. The workspace never sets it.

Observe-only: not on the gate verdict path, reads only its input files, writes only ``--out``, and
posts no bridge event.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The shared guarded-word check, imported so this file carries no marker literal: the charter diff gate
# refuses those literals, and a copied word list could drift from the shared one.
from tools.bridge_diff_privacy import _marker as _guarded_word  # noqa: E402

SNAPSHOT_SCHEMA = "wd.bridge-mind.v1"
POLICY = "wd.bridge-mind.workspace.policy.v0"
UNKNOWN = "UNKNOWN"
REF = re.compile(r"[0-9a-f]{64}")
HEAD = re.compile(r"[0-9a-f]{7,64}")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,119}")
SAFE_PATH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/\\+-]{0,199}")
REASON = re.compile(r"[a-z0-9_]{1,64}")
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")
_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z")
VOTE_SLOTS = ("build_lead", "build_tools", "rco")
COMMAND_GRACE = timedelta(minutes=15)
ATTENTION_LIMIT = 50
QUESTION_LIMIT = 400
UNKNOWN_REF_RULES = ("refused_input", "coverage_unknown", "input_blocked")
RULE_SEVERITY = {
    "claim_overlap": "high",
    "coverage_unknown": "high",
    "input_blocked": "high",
    "stale_vote": "medium",
    "unacknowledged_command": "medium",
    "refused_input": "medium",
    "expired_claim": "low",
}
ATTENTION_RANK = {"last_missing_vote": 0, "signature_only": 1, "votes_missing": 2, "tension_high": 3,
                  "tension": 4}


class WorkspaceError(ValueError):
    """Fixed messages that never echo input content."""


def parse_time(value: Any) -> datetime | None:
    """A UTC ``YYYY-MM-DDTHH:MM:SS[.fraction]Z`` time, or None. Fractions finer than microseconds are cut."""
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    base = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return base + timedelta(microseconds=int((match.group(2) or "").ljust(6, "0")[:6]))


def _has_marker(text: str) -> bool:
    """A guarded word in the text, or in its ``\\uXXXX``-unescaped form."""
    unescaped = _ESCAPED.sub(lambda match: chr(int(match.group(1), 16)), text)
    return _guarded_word(text) is not None or _guarded_word(unescaped) is not None


def _match(pattern: re.Pattern, value: Any) -> str | None:
    if isinstance(value, str) and pattern.fullmatch(value) and not _has_marker(value):
        return value
    return None


def _canonical(text: str) -> str:
    return " ".join(text.lower().split())


def tension_id(rule: str, subject: str, claim: str, observation: str) -> str:
    blob = "|".join([rule, subject, _canonical(claim), _canonical(observation)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _scope_key(path: str) -> str:
    """Repo-relative claim path, normalized the way Windows compares it."""
    text = path.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/").casefold()


def scopes_overlap(left: Iterable[str], right: Iterable[str]) -> list[str]:
    """Claim paths that are equal, or where one is a directory above the other."""
    hits = []
    for a in sorted({_scope_key(p) for p in left}):
        for b in sorted({_scope_key(p) for p in right}):
            if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                hits.append(a if a == b else f"{a} ~ {b}")
    return hits


class _Inputs:
    """The usable facts of one snapshot. A fact that fails the allowlist is refused by position only."""

    def __init__(self, snapshot: Mapping[str, Any]):
        if snapshot.get("schema") != SNAPSHOT_SCHEMA:
            raise WorkspaceError("snapshot schema is not wd.bridge-mind.v1")
        if snapshot.get("authority") != "none":
            raise WorkspaceError("snapshot authority must be none")
        source = snapshot.get("source") if isinstance(snapshot.get("source"), Mapping) else {}
        self.now = parse_time(source.get("as_of_utc"))
        if self.now is None:
            raise WorkspaceError("snapshot source needs an explicit as_of_utc")
        self.source_ref = _match(REF, source.get("last_ref")) or UNKNOWN
        self.coverage = source.get("coverage") if source.get("coverage") in ("complete", "partial", "unknown") \
            else "unknown"
        blocked = source.get("blocked") if isinstance(source.get("blocked"), Mapping) else None
        self.blocked = None
        if blocked is not None:
            offset = blocked.get("offset")
            self.blocked = {"reason": _match(REASON, blocked.get("reason")) or "unrecognized",
                            "offset": offset if isinstance(offset, int) and offset >= 0 else None}
        self.refused: list[dict[str, str]] = []
        self.prs = self._rows(snapshot, "prs", self._pr)
        self.lanes = self._rows(snapshot, "lanes", self._lane)
        self.commands = [fact for fact in self._rows(snapshot, "facts", self._fact) if fact is not None]

    def _refuse(self, where: str, ref: Any, reason: str) -> None:
        self.refused.append({"where": where, "ref": _match(REF, ref) or UNKNOWN, "reason": reason})

    def _rows(self, snapshot: Mapping[str, Any], key: str, reader) -> list[Any]:
        rows = snapshot.get(key) or []
        if not isinstance(rows, list):
            self._refuse(key, None, "not a list")
            return []
        out = []
        for index, row in enumerate(rows):
            where = f"{key}[{index}]"
            if not isinstance(row, Mapping):
                self._refuse(where, None, "not an object")
                continue
            value = reader(row, where)
            if value is not None:
                out.append(value)
        return out

    def _pr(self, row: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        ref, head = _match(REF, row.get("ref")), _match(HEAD, row.get("head"))
        if ref is None or head is None or not isinstance(row.get("pr"), int) or isinstance(row.get("pr"), bool):
            self._refuse(where, row.get("ref"), "pr needs pr, head and an event ref")
            return None
        ci = row.get("ci") if isinstance(row.get("ci"), Mapping) else {}
        ci_green = ci.get("state") == "green" and _match(REF, ci.get("ref")) is not None
        votes, vetoes, missing = [], [], []
        for index, vote in enumerate(row.get("votes") or []):
            vote = vote if isinstance(vote, Mapping) else {}
            item = {"slot": vote.get("slot") if vote.get("slot") in VOTE_SLOTS else None,
                    "agent": _match(SAFE_ID, vote.get("agent")), "head": _match(HEAD, vote.get("head")),
                    "ref": _match(REF, vote.get("ref"))}
            if None in item.values():
                self._refuse(f"{where}.votes[{index}]", vote.get("ref"), "vote needs slot, agent, head and an event ref")
                continue
            votes.append(item)
        for index, veto in enumerate(row.get("vetoes") or []):
            veto = veto if isinstance(veto, Mapping) else {}
            item = {"agent": _match(SAFE_ID, veto.get("agent")), "ref": _match(REF, veto.get("ref"))}
            if None in item.values():
                self._refuse(f"{where}.vetoes[{index}]", veto.get("ref"), "veto needs agent and an event ref")
                continue
            vetoes.append(item)
        for index, entry in enumerate(row.get("missing") or []):
            entry = entry if isinstance(entry, Mapping) else {}
            element, entry_ref = _match(SAFE_ID, entry.get("element")), _match(REF, entry.get("ref"))
            actors = [_match(SAFE_ID, actor) for actor in entry.get("actors") or []]
            if element is None or entry_ref is None or None in actors:
                self._refuse(f"{where}.missing[{index}]", entry.get("ref"),
                             "missing element needs element, safe actors and an event ref")
                continue
            missing.append({"element": element, "actors": sorted(set(actors)), "ref": entry_ref})
        return {"pr": row["pr"], "head": head, "ref": ref, "ci_green": ci_green,
                "ci_ref": _match(REF, ci.get("ref")), "votes": votes, "vetoes": vetoes, "missing": missing}

    def _lane(self, row: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        ref, agent = _match(REF, row.get("ref")), _match(SAFE_ID, row.get("agent"))
        if ref is None or agent is None:
            self._refuse(where, row.get("ref"), "lane needs a safe agent and an event ref")
            return None
        claims = []
        for index, claim in enumerate(row.get("claims") or []):
            claim = claim if isinstance(claim, Mapping) else {}
            task, claim_ref = _match(SAFE_ID, claim.get("task_id")), _match(REF, claim.get("ref"))
            expires = parse_time(claim.get("lease_expires_utc"))
            scope = [_match(SAFE_PATH, path) for path in claim.get("write_scope") or []]
            if task is None or claim_ref is None or expires is None or None in scope:
                self._refuse(f"{where}.claims[{index}]", claim.get("ref"),
                             "claim needs task_id, lease_expires_utc, safe paths and an event ref")
                continue
            claims.append({"task_id": task, "write_scope": scope, "expires": expires, "ref": claim_ref})
        return {"agent": agent, "ref": ref, "claims": claims}

    def _fact(self, row: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        """Only ``command`` facts feed the workspace; a fact whose TTL ended at ``as_of`` is not used."""
        ref, observed = _match(REF, row.get("ref")), parse_time(row.get("observed_utc"))
        ttl = row.get("ttl_seconds")
        if ref is None or observed is None:
            self._refuse(where, row.get("ref"), "fact needs observed_utc and an event ref")
            return None
        if isinstance(ttl, int) and not isinstance(ttl, bool) and observed + timedelta(seconds=ttl) <= self.now:
            return None
        if row.get("kind") != "command":
            return None
        value = row.get("value") if isinstance(row.get("value"), Mapping) else {}
        request_id, sender = _match(SAFE_ID, value.get("request_id")), _match(SAFE_ID, value.get("from"))
        expected = [_match(SAFE_ID, agent) for agent in value.get("expected_responders") or []]
        answered = [_match(SAFE_ID, agent) for agent in value.get("answered_by") or []]
        if request_id is None or sender is None or None in expected or None in answered:
            self._refuse(where, row.get("ref"), "command needs a safe request_id, sender and responders")
            return None
        return {"request_id": request_id, "from": sender, "at": observed, "ref": ref,
                "unanswered": sorted(set(expected) - set(answered))}


def _tension(rule: str, subject: str, claim: str, observation: str, actors: Iterable[str],
             refs: Iterable[str]) -> dict[str, Any]:
    return {"tension_id": tension_id(rule, subject, claim, observation), "rule": rule,
            "severity": RULE_SEVERITY[rule], "subject": subject, "claim": claim, "observation": observation,
            "actors": sorted({actor for actor in actors if actor}), "refs": sorted(set(refs))}


def derive_tensions(inputs: _Inputs) -> list[dict[str, Any]]:
    tensions: list[dict[str, Any]] = []
    if inputs.coverage != "complete":
        tensions.append(_tension("coverage_unknown", "source", "the snapshot is one view of the bridge log",
                                 f"its coverage is {inputs.coverage}, so no list here is complete", [],
                                 [inputs.source_ref]))
    if inputs.blocked is not None:
        offset = UNKNOWN if inputs.blocked["offset"] is None else str(inputs.blocked["offset"])
        tensions.append(_tension("input_blocked", f"offset:{offset}", f"the log row at offset {offset}",
                                 f"was blocked by the projector ({inputs.blocked['reason']}); the cursor did not "
                                 "advance past it", [], [inputs.source_ref]))
    for refused in inputs.refused:
        tensions.append(_tension("refused_input", refused["where"], f"input {refused['where']}",
                                 f"refused: {refused['reason']}", [], [refused["ref"]]))
    live = []
    for lane in inputs.lanes:
        for claim in lane["claims"]:
            if claim["expires"] <= inputs.now:
                tensions.append(_tension("expired_claim", f"claim:{claim['task_id']}",
                                         f"{lane['agent']} holds a write claim on {claim['task_id']}",
                                         "its lease ended at or before as_of; that is not completed work",
                                         [lane["agent"]], [claim["ref"]]))
            else:
                live.append((lane["agent"], claim))
    for index, (agent_a, claim_a) in enumerate(live):
        for agent_b, claim_b in live[index + 1:]:
            hits = scopes_overlap(claim_a["write_scope"], claim_b["write_scope"]) if agent_a != agent_b else []
            if hits:
                first, second = sorted([(agent_a, claim_a["task_id"]), (agent_b, claim_b["task_id"])])
                tensions.append(_tension("claim_overlap", f"claims:{first[1]}|{second[1]}",
                                         f"{first[0]} and {second[0]} both hold live write claims",
                                         "overlapping paths: " + ", ".join(hits), [agent_a, agent_b],
                                         [claim_a["ref"], claim_b["ref"]]))
    for pr in inputs.prs:
        for vote in pr["votes"]:
            if vote["head"] != pr["head"]:
                tensions.append(_tension("stale_vote", f"pr:{pr['pr']}:{vote['slot']}:{vote['agent']}",
                                         f"{vote['agent']} voted {vote['slot']} on PR{pr['pr']} at {vote['head'][:12]}",
                                         f"the PR head is {pr['head'][:12]}; a stale vote cannot approve it",
                                         [vote["agent"]], [vote["ref"], pr["ref"]]))
    for command in inputs.commands:
        if command["unanswered"] and inputs.now - command["at"] >= COMMAND_GRACE:
            tensions.append(_tension("unacknowledged_command", f"request:{command['request_id']}",
                                     f"{command['from']} sent request {command['request_id']}",
                                     "no bound reply from " + ", ".join(command["unanswered"]),
                                     command["unanswered"], [command["ref"]]))
    unique = {tension["tension_id"]: tension for tension in tensions}
    return [unique[key] for key in sorted(unique)]


def _pr_attention(pr: Mapping[str, Any]) -> dict[str, Any] | None:
    elements = {entry["element"]: entry for entry in pr["missing"]}
    if not elements:
        return None
    refs = [pr["ref"]] + [entry["ref"] for entry in pr["missing"]]
    if set(elements) == {"signature"}:
        return {"kind": "signature_only", "subject": f"pr:{pr['pr']}", "actors": ["operator"],
                "detail": f"PR{pr['pr']} at {pr['head'][:12]} needs only the operator signature", "refs": refs}
    if pr["ci_green"] and not pr["vetoes"] and set(elements) <= set(VOTE_SLOTS):
        actors = sorted({actor for entry in elements.values() for actor in entry["actors"]})
        slots = sorted(elements)
        return {"kind": "last_missing_vote" if len(slots) == 1 else "votes_missing", "subject": f"pr:{pr['pr']}",
                "actors": actors, "refs": refs + [pr["ci_ref"]],
                "detail": f"PR{pr['pr']} at {pr['head'][:12]} is reported CI-green; missing: {', '.join(slots)}"}
    return None


def derive_attention(inputs: _Inputs, tensions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    items = [item for item in (_pr_attention(pr) for pr in inputs.prs) if item is not None]
    for tension in tensions:
        items.append({"kind": "tension_high" if tension["severity"] == "high" else "tension",
                      "subject": f"tension:{tension['tension_id']}", "actors": list(tension["actors"]),
                      "detail": f"{tension['rule']}: {tension['claim']}; {tension['observation']}",
                      "refs": list(tension["refs"])})
    items.sort(key=lambda item: (ATTENTION_RANK[item["kind"]], item["subject"]))
    items = items[:ATTENTION_LIMIT]
    for rank, item in enumerate(items, 1):
        item.update({"rank": rank, "refs": sorted(set(item["refs"])), "suggestion_only": True})
    return items


def _question(item: Mapping[str, Any], who: str) -> str:
    if item["kind"] == "last_missing_vote":
        text = f"{item['detail']}. Can {who} give it at this exact head?"
    elif item["kind"] == "votes_missing":
        text = f"{item['detail']}. Which of these can {who} give at this exact head?"
    elif item["kind"] == "signature_only":
        text = f"{item['detail']}. Is it on the morning signature list?"
    else:
        text = f"How can {who} resolve {item['detail']}?"
    return text if len(text) <= QUESTION_LIMIT else UNKNOWN


def _entry(item: Mapping[str, Any] | None, who: str, partial: bool) -> dict[str, Any]:
    if item is None:
        return {"question": UNKNOWN, "attention_rank": None, "refs": [UNKNOWN]}
    question = _question(item, who)
    if question != UNKNOWN and partial:
        question += " Coverage is not complete, so check the exact head first."
    return {"question": question if len(question) <= QUESTION_LIMIT else UNKNOWN, "attention_rank": item["rank"],
            "refs": list(item["refs"])}


def derive_next_question(inputs: _Inputs, attention: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    partial = inputs.coverage != "complete"
    agents = {lane["agent"] for lane in inputs.lanes} | {actor for item in attention for actor in item["actors"]}
    lanes = {agent: _entry(next((item for item in attention if agent in item["actors"]), None), agent, partial)
             for agent in sorted(agents)}
    first = attention[0] if attention else None
    who = ", ".join(first["actors"]) if first and first["actors"] else "the fleet"
    return {"global": _entry(first, who, partial), "lanes": lanes}


def _check_output(fields: Mapping[str, Any]) -> None:
    def strings(value: Any):
        if isinstance(value, str):
            yield value
        elif isinstance(value, Mapping):
            for key, item in value.items():
                yield str(key)
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    if any(_has_marker(text) for text in strings(fields)):
        raise WorkspaceError("a guarded word reached the reflection; nothing is written")

    def check(refs: Any, unknown_ok: bool) -> None:
        valid = isinstance(refs, list) and refs and all(isinstance(ref, str) and (REF.fullmatch(ref) or ref == UNKNOWN)
                                                        for ref in refs)
        if not valid or (UNKNOWN in refs and not unknown_ok):
            raise WorkspaceError("an output item has no valid event ref; nothing is written")

    # UNKNOWN is a ref only where the input has none to give, and for what is derived from exactly that.
    unknown_tensions = {"tension:" + t["tension_id"] for t in fields["tensions"] if t["rule"] in UNKNOWN_REF_RULES}
    unknown_ranks = {item["rank"] for item in fields["attention"] if item["subject"] in unknown_tensions}
    for tension in fields["tensions"]:
        check(tension["refs"], tension["rule"] in UNKNOWN_REF_RULES)
    for item in fields["attention"]:
        check(item["refs"], item["rank"] in unknown_ranks)
    for entry in [fields["next_question"]["global"]] + list(fields["next_question"]["lanes"].values()):
        rank = entry["attention_rank"]
        check(entry["refs"], (rank is None and entry["question"] == UNKNOWN) or rank in unknown_ranks)


def reflection(snapshot: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The three reflection fields for one snapshot. Pure: no clock, no file, no network."""
    inputs = _Inputs(snapshot)
    tensions = derive_tensions(inputs)
    previous_ids = {str(item.get("tension_id")) for item in (previous or {}).get("tensions") or []
                    if isinstance(item, Mapping)}
    for tension in tensions:
        tension["lifecycle"] = "persisting" if tension["tension_id"] in previous_ids else "new"
    attention = derive_attention(inputs, tensions)
    fields = {"tensions": tensions, "attention": attention, "next_question": derive_next_question(inputs, attention)}
    _check_output(fields)
    return fields


def reflect(snapshot: Mapping[str, Any], previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A copy of the snapshot with only its reflection fields filled; every other field is unchanged."""
    out = copy.deepcopy(dict(snapshot))
    out.update(reflection(snapshot, previous))
    return out


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def render_text(snapshot: Mapping[str, Any]) -> str:
    lines = [f"as of {snapshot['source']['as_of_utc']} (coverage {snapshot['source'].get('coverage', UNKNOWN)})"]
    for item in snapshot["attention"]:
        lines.append(f"{item['rank']:>3} {item['kind']}: {item['detail']}"
                     + (f" [{', '.join(item['actors'])}]" if item["actors"] else ""))
    lines.append(f"? global: {snapshot['next_question']['global']['question']}")
    for agent, entry in snapshot["next_question"]["lanes"].items():
        lines.append(f"? {agent}: {entry['question']}")
    return "\n".join(lines) + "\n"


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceError("an input file is not readable JSON") from exc
    if not isinstance(value, dict):
        raise WorkspaceError("an input file is not a JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge mind reflective workspace v0 (read-only, dormant).")
    parser.add_argument("--snapshot", type=Path, required=True, help="A wd.bridge-mind.v1 snapshot JSON.")
    parser.add_argument("--previous", type=Path, default=None, help="The previous snapshot, for lifecycle.")
    parser.add_argument("--out", type=Path, default=None, help="Write the reflected snapshot JSON here.")
    parser.add_argument("--json", action="store_true", help="Print the reflected snapshot JSON instead of text.")
    args = parser.parse_args(argv)
    try:
        previous = _read_object(args.previous) if args.previous is not None else None
        reflected = reflect(_read_object(args.snapshot), previous)
    except WorkspaceError as exc:
        sys.stderr.write(f"bridge mind workspace refused: {exc}\n")
        return 2
    text = json.dumps(reflected, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text if args.json else render_text(reflected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
