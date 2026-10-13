# SPDX-License-Identifier: BUSL-1.1
"""Bridge mind reflective workspace v0: fills the reflection fields of one ``wd.bridge-mind.v1`` snapshot.

Dormant and unwired. The projector core (``tools/wd_bridge_mind.py``, ``snapshot()``) emits a snapshot with
``source``, ``prs`` and ``lanes`` dictionaries, ``facts`` and empty reflection fields. This workspace is a
pure function of that snapshot (and, optionally, the previous reflected one) and fills only:

* ``tensions``: mechanical contradictions, each with a rule id, the agents it names, and the refs of the
  facts it came from. The ``tension_id`` is structural (sha256 of rule, subject, claim and observation),
  so new evidence for the same contradiction keeps its id; against a previous snapshot each tension is
  ``new`` or ``persisting``. Rules:

  - ``claim_overlap``: task claims (the core's ``claims.by_task`` rows) of two agents that are not expired
    overlap, an UNKNOWN lease included (bridge claim events carry no lease, so an unknown lease is the usual
    case, never a reason to skip a claim); one tension per pair of lanes, with every overlapping path and the
    refs of the task claims involved. Paths are compared
    as text the way Windows compares them (``claim_path_key``): a space or a colon is ordinary, and a path that
    climbs above its root refuses its task claim. No path is ever opened. A lane-level ``write_scope``, lease or
    ``expired`` that disagrees with its rows refuses every claim of the lane; one unusable row refuses only
    itself, and the lane's other task claims still count.
  - ``stale_vote``: the votes in ``stale_votes`` of one PR (bound to a head that is not the PR head, or retired
    by the core at the PR head after a later blocking signal of the same identity), folded into one tension per
    PR that names each of them and keeps every contributing ref. A stale vote never counts. A stale vote bound to
    the other head that an open PR's context saw is named by ``context_head_mismatch`` instead (it still never
    counts). An identity that also sent the same verified vote at a current head (the PR head, or that context
    head) is named in the observation but is not an actor: it has nothing to do.
  - ``veto_observed``: a veto observation on the PR. Its activity stays UNKNOWN; it is never read as cleared.
    Its status is any safe token, ``UNKNOWN`` included.
  - ``veto_own_later_pass``: in place of ``veto_observed`` for a veto with a known head when the same verified
    identity later sent ``rco_pass`` at a current head (the snapshot head, or the head an open PR's context saw).
    The veto stays observed and is never cleared; it only ranks low so questions point at live work.
  - ``expired_claim``: a task claim whose lease ended at or before ``as_of``. An expired claim is not
    completed work. A claim with an UNKNOWN lease is never expired here.
  - ``unacknowledged_command``: an expected responder of an exact request (a ``command`` fact) has no bound
    reply after the grace time. The core v0 emits no command facts yet, so this rule waits for them.
  - ``coverage_unknown``: the source does not claim complete coverage, so nothing here is complete.
  - ``input_blocked``: ``source.blocked`` is active; only its fixed reason and offset.
  - ``refused_input``: a row without valid ``source_refs``, or with a value outside the projection
    allowlist. It is named by its position only and is never used.
  - ``context_applied``, ``context_refused``, ``context_head_mismatch`` and ``class_unverified``: see the PR
    context below.

* Optional PR context (``context``, the CLI's ``--context``): one caller-supplied ``wd.bridge-mind-pr-context.v1``
  capture, for example of GitHub metadata, with ``authority`` ``none``, its ``capture_sha256`` and
  ``observed_utc``, the charter evidence ``{loaded, charter_blob, classifier_blob}`` and per PR
  ``{state, head, head_ref_name, path_class}``, where ``path_class`` is null or
  ``{charter_allowed, denylist_hits, a_hits, unrecognized}``. It is observational data, never authentication or
  task authority: a digest proves bytes, not truth. A context of any other shape, or observed more than
  ``CONTEXT_MAX_AGE`` away from ``as_of``, is not used at all (``context_refused``, with its fixed reason), and
  everything stays as without one. A used context gives one ``context_applied`` tension naming its full capture
  digest (a capture digest, not an event ref), observation time and charter evidence; it is a record, never an
  attention item. With it, a PR whose
  context state is not ``OPEN`` gets no suggestion, and the attention items of its tensions are left out
  (listed in ``context_applied``); a PR whose context head is not the snapshot head gets no suggestion either
  (``context_head_mismatch``, which also names the stale votes bound to the head the context saw). Only
  attention, questions and that stale-vote naming change: the votes, veto observations and their refs stay as
  they are, and nothing clears a veto.
* A ``wd.bridge-mind-pr-context.v2`` capture adds per PR ``commit_authors``: null (the list may be incomplete)
  or the lane-shaped git author names of the PR's commits; any other value refuses the whole context. A
  recognized RCO among them fills no RCO slot of that PR (author is not reviewer) and is never asked for it.
  A recognized RCO's own ``rco_recused`` at the exact PR head (core ``recusals``, a declared identity) does the
  same. These are the only recusal reads here; a declared author or recusal is data, not authentication.
* A class is computed only from a used context whose charter evidence is ``loaded`` exactly ``true`` with both
  artifact identities, for a PR whose context row is ``OPEN``, at the snapshot head, with a ``path_class``: a
  protected hit (an ``a_hits`` or ``denylist_hits`` entry) is ``protected``, none is ``not_protected``. Anything
  else leaves the class UNKNOWN. A declared class from the bridge is never enough for ``signature_only``: a PR
  with every reported vote, a declared ``a`` and no computed protected hit is a ``class_unverified`` tension,
  and empty ``a_hits`` never waive a signature while the charter or denylist evidence is unknown.
* Pseudo-lanes (``PSEUDO_AGENTS``, such as ``system``) keep their sourced facts but get no action question.

* ``attention``: one ordered, bounded list of suggestions. For each PR with a known head, reported CI
  green at that exact head and no veto observation (an unreadable one counts as one), the workspace counts
  the current-head votes of declared identities (a valid ``agent_uuid`` and ``verification`` ``declared``;
  the two must agree) into the consensus slots by the charter's identity map
  (``build_lead`` = codex-lead-1, ``build_tools`` = codex-tools-1, and one slot for each recognized RCO,
  because the installed rule-12 policy needs every present eligible RCO and the snapshot carries neither
  presence nor the author, so an RCO-authored PR never reaches ``signature_only`` here). Exactly one empty
  slot comes first (the last missing vote goes first), then PRs with every slot filled and a computed
  protected class (only the operator signature is left), then PRs missing several votes, then high and other
  tensions. A
  blocked source gives no PR suggestion at all, only its tensions. A suggestion is never a clearance: the
  gate computes the real verdict from canonical events, and the core marks gate eligibility UNKNOWN.
* ``next_question``: ``{"global": entry, "lanes": {agent: entry}}``, each entry from its first attention
  item, or ``UNKNOWN`` when the snapshot does not support one. A lane without an item is ``UNKNOWN``,
  never "idle": absence of a recent event is not proof of anything.

Rules of the derivation:

* Every tension, attention item and question carries refs: 64-hex event refs from ``source_refs``, or
  ``UNKNOWN`` only where the input has none to give (refused input, unknown coverage, a blocked row, an
  unsupported question). An output item without refs fails the whole run, so nothing is written.
* Event time only. "Now" is ``source.as_of_utc``; nothing reads the wall clock. Facts whose TTL has ended
  at ``as_of`` are not used. The same snapshot gives the same bytes, whatever the order of its lists.
* A strict projection allowlist: only identifiers (agents, tasks, requests, statuses, paths, heads) that
  match fixed patterns are copied into derived items, never free text or payloads. An input string that
  holds a guarded private-marker word makes its row a refused input. JSON inside strings is never parsed.
* The output boundary: ``reflect`` guards the complete reflected object (every key and value, including
  fields copied unchanged from the snapshot), and the CLI guards the exact text it prints as well, JSON or
  plain. The check is in any letter case and compatibility form, and before and after each backslash-u
  unescape. A hit refuses the whole snapshot with exit 2 and prints nothing; nothing is stripped.
* ``authority`` must be ``none`` and ``scope`` must be ``bounded_projection_not_gate``; neither is set here.

Observe-only: not on the gate verdict path, reads only its input files, writes only stdout, and posts no
bridge event. Publishing a reflected snapshot to a file is left to the core's owned, atomic publication.
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
import unicodedata

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The shared guarded-word check, imported so this file carries no marker literal: the charter diff gate
# refuses those literals, and a copied word list could drift from the shared one.
from tools.bridge_diff_privacy import _marker as _guarded_word  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

SNAPSHOT_SCHEMA = "wd.bridge-mind.v1"
SNAPSHOT_SCOPE = "bounded_projection_not_gate"
UNKNOWN = "UNKNOWN"
REF = re.compile(r"[0-9a-f]{64}")
HEAD = re.compile(r"[0-9a-f]{40}")
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,119}")
# The core's claim path alphabet (space, colon and backslash included) and length; the workspace also allows "+".
SAFE_PATH = re.compile(r"[A-Za-z0-9._][A-Za-z0-9._/\\:+ -]{0,2047}")
REASON = re.compile(r"[a-z0-9_]{1,64}")
# The core's task token (``_token``): a task id from the bridge, or UNKNOWN.
TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")
_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(?:Z|\+00:00)")
# The charter's consensus identities (CLAUDE.md rule 9a). Used only to suggest which slot is empty. The installed
# rule-12 review policy needs every present eligible RCO, and the snapshot says nothing about RCO presence or the
# PR author (recusal), so each recognized RCO is its own slot: a PR with one RCO pass still misses the other.
BUILD_SLOT = {"codex-lead-1": "build_lead", "codex-tools-1": "build_tools"}
RECOGNIZED_RCOS = ("claude-rco-1", "claude-rco-2")
VOTE_SLOTS = ("build_lead", "build_tools") + RECOGNIZED_RCOS
COMMAND_GRACE = timedelta(minutes=15)
ATTENTION_LIMIT = 50
QUESTION_LIMIT = 400
UNESCAPE_ROUNDS = 4
KEY_LIMIT = 200
UNKNOWN_REF_RULES = ("refused_input", "coverage_unknown", "input_blocked", "context_applied", "context_refused")
RULE_SEVERITY = {
    "claim_overlap": "high",
    "coverage_unknown": "high",
    "input_blocked": "high",
    "veto_observed": "high",
    "stale_vote": "medium",
    "unacknowledged_command": "medium",
    "refused_input": "medium",
    "context_refused": "medium",
    "context_head_mismatch": "medium",
    "class_unverified": "medium",
    "expired_claim": "low",
    "veto_own_later_pass": "low",
    "context_applied": "low",
}
CONTEXT_SCHEMA = "wd.bridge-mind-pr-context.v1"
# v2 adds per PR the declared git author names of its commits (null when the list may be incomplete).
CONTEXT_SCHEMA_V2 = "wd.bridge-mind-pr-context.v2"
CONTEXT_KEYS = frozenset({"schema", "authority", "capture_sha256", "observed_utc", "charter", "prs"})
CONTEXT_PR_KEYS = frozenset({"state", "head", "head_ref_name", "path_class"})
CONTEXT_PR_KEYS_V2 = CONTEXT_PR_KEYS | {"commit_authors"}
LANE_ID = re.compile(r"[a-z][a-z0-9-]{1,40}")
PATH_CLASS_KEYS = frozenset({"charter_allowed", "denylist_hits", "a_hits", "unrecognized"})
CHARTER_KEYS = frozenset({"loaded", "charter_blob", "classifier_blob"})
PR_STATES = ("OPEN", "MERGED", "CLOSED")
# A git blob id (sha1 or sha256) of a charter or classifier artifact.
BLOB = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
CONTEXT_MAX_AGE = timedelta(minutes=15)
NOT_ATTENTION_RULES = frozenset({"context_applied"})
CONTEXT_LIST_LIMIT = 40
# Not fleet lanes: their sourced facts stay visible, but they get no action question.
PSEUDO_AGENTS = frozenset({"system"})
_PR_SUBJECT = re.compile(r"pr:([1-9][0-9]{0,8})(?::.*)?")
ATTENTION_RANK = {"last_missing_vote": 0, "signature_only": 1, "votes_missing": 2, "tension_high": 3,
                  "tension": 4}


class WorkspaceError(ValueError):
    """Fixed messages that never echo input content."""


def parse_time(value: Any) -> datetime | None:
    """A UTC ``YYYY-MM-DDTHH:MM:SS[.fraction](Z|+00:00)`` time, or None. Fractions finer than microseconds are cut."""
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    try:
        base = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:  # the right shape but not a calendar time, such as month 99
        return None
    return base + timedelta(microseconds=int((match.group(2) or "").ljust(6, "0")[:6]))


def _has_marker(text: str) -> bool:
    """A guarded word in the text in any letter case or compatibility form, before or after each ``\\uXXXX`` unescape.

    The shared check is case-sensitive, so each form is NFKC-normalized, case-folded and upper-cased first. The
    unescape repeats until nothing changes (at most ``UNESCAPE_ROUNDS`` times), so a word escaped twice, which a
    reader would reveal by decoding twice, is found too. JSON inside the text is never parsed.
    """
    form = text
    for _ in range(UNESCAPE_ROUNDS):
        if _guarded_word(unicodedata.normalize("NFKC", form).casefold().upper()) is not None:
            return True
        unescaped = _ESCAPED.sub(lambda match: chr(int(match.group(1), 16)), form)
        if unescaped == form:
            return False
        form = unescaped
    return True


def guard_object(value: Any) -> None:
    """Refuse a whole object when any key or string value in it holds a guarded word; it is never stripped."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _has_marker(item):
                raise WorkspaceError("a guarded word is in the output; nothing is written")
        elif isinstance(item, Mapping):
            stack.extend(str(key) for key in item)
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)


def _match(pattern: re.Pattern, value: Any) -> str | None:
    if isinstance(value, str) and pattern.fullmatch(value) and not _has_marker(value):
        return value
    return None


def _refs(value: Any) -> list[str] | None:
    """A non-empty list of 64-hex event refs, sorted, or None."""
    if not isinstance(value, list) or not value:
        return None
    refs = [_match(REF, ref) for ref in value]
    return None if None in refs else sorted(set(refs))


def _first_ref(value: Any) -> str:
    """The first valid ref of a row, for a refusal that names it; UNKNOWN when there is none."""
    if isinstance(value, Mapping):
        value = value.get("source_refs")
    if isinstance(value, list):
        for ref in value:
            if _match(REF, ref):
                return ref
    return UNKNOWN


def _canonical(text: str) -> str:
    return " ".join(text.lower().split())


def tension_id(rule: str, subject: str, claim: str, observation: str) -> str:
    blob = "|".join([rule, subject, _canonical(claim), _canonical(observation)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def claim_path_key(path: str) -> str | None:
    """A declared claim path normalized the way Windows compares it, or None when it is not safe to compare.

    Text only: the path is never opened or resolved. Backslashes are slashes, ``.`` and empty segments go, ``..``
    removes the segment before it, each segment loses trailing spaces and dots, and the case is folded. A space
    or a colon is an ordinary character. A path that climbs above its root, or a segment of only dots and spaces,
    is None. The whole tree (``.``) is the empty key, which covers every path.
    """
    parts: list[str] = []
    for segment in path.replace("\\", "/").split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if not parts:
                return None
            parts.pop()
            continue
        trimmed = segment.rstrip(" .")
        if not trimmed:
            return None
        parts.append(trimmed.casefold())
    return "/".join(parts)


def _claim_key(path: Any) -> str | None:
    """The key of a declared path in the claim alphabet without a guarded word, or None."""
    return claim_path_key(path) if _match(SAFE_PATH, path) else None


def scopes_overlap(left: Iterable[str], right: Iterable[str]) -> list[str]:
    """Declared claim paths that are equal, or where one is a directory above the other, whichever side is which.

    Compared by ``claim_path_key``; a path that is not safe to compare refuses the call, it is never skipped.
    """
    sides = []
    for paths in (left, right):
        keys = {_claim_key(path) for path in paths}
        if None in keys:
            raise WorkspaceError("a claim path is not safe to compare")
        sides.append(keys)
    hits = set()
    for a in sides[0]:
        for b in sides[1]:
            if a == b or not a or not b or a.startswith(b + "/") or b.startswith(a + "/"):
                hits.add((a or ".") if a == b else " ~ ".join(sorted((a or ".", b or "."))))
    return sorted(hits)


def _hits(value: Any) -> list[str] | None:
    """A list of safe declared paths (possibly empty), or None."""
    if not isinstance(value, list):
        return None
    paths = [_match(SAFE_PATH, path) for path in value]
    return None if None in paths else sorted(set(paths))


def read_context(value: Any, now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """A usable PR context and None, or None and the fixed reason it is not used (None, None without one).

    The whole context is used or none of it: one malformed row refuses all of it, so a partial capture never
    looks complete. Booleans must be exact; ``"true"`` or ``1`` is not ``true``.
    """
    if value is None:
        return None, None
    if not isinstance(value, Mapping) or set(value) != CONTEXT_KEYS:
        return None, "context_shape_invalid"
    if value["schema"] not in (CONTEXT_SCHEMA, CONTEXT_SCHEMA_V2) or value["authority"] != "none":
        return None, "context_schema_invalid"
    capture, observed = _match(REF, value["capture_sha256"]), parse_time(value["observed_utc"])
    if capture is None or observed is None:
        return None, "context_identity_invalid"
    if abs(now - observed) > CONTEXT_MAX_AGE:
        return None, "context_stale"
    charter = value["charter"]
    if not isinstance(charter, Mapping) or set(charter) != CHARTER_KEYS or not isinstance(charter["loaded"], bool):
        return None, "context_charter_invalid"
    blobs = (_match(BLOB, charter["charter_blob"]), _match(BLOB, charter["classifier_blob"]))
    # Computed classes need the charter loaded and both artifact identities; otherwise the class stays UNKNOWN.
    charter_ok = charter["loaded"] is True and None not in blobs
    rows = value["prs"]
    if not isinstance(rows, Mapping):
        return None, "context_prs_invalid"
    prs: dict[int, dict[str, Any]] = {}
    for key, row in rows.items():
        number = int(key) if isinstance(key, str) and key.isdigit() and key == str(int(key)) and int(key) > 0 else None
        keys = CONTEXT_PR_KEYS_V2 if value["schema"] == CONTEXT_SCHEMA_V2 else CONTEXT_PR_KEYS
        if number is None or not isinstance(row, Mapping) or set(row) != keys \
                or row["state"] not in PR_STATES or _match(HEAD, row["head"]) is None \
                or _match(TASK, row["head_ref_name"]) is None:
            return None, "context_pr_invalid"
        path_class = row["path_class"]
        if path_class is not None:
            if not isinstance(path_class, Mapping) or set(path_class) != PATH_CLASS_KEYS \
                    or not isinstance(path_class["charter_allowed"], bool):
                return None, "context_path_class_invalid"
            hits = {name: _hits(path_class[name]) for name in ("denylist_hits", "a_hits", "unrecognized")}
            if None in hits.values():
                return None, "context_path_class_invalid"
            path_class = dict(hits, charter_allowed=path_class["charter_allowed"])
        authors = row.get("commit_authors")
        if authors is not None:
            if not isinstance(authors, list) or not all(_match(LANE_ID, name) for name in authors):
                return None, "context_pr_invalid"
            authors = sorted(set(authors))
        prs[number] = {"state": row["state"], "head": row["head"], "branch": row["head_ref_name"], "authors": authors,
                       "path_class": path_class}
    return {"capture": capture, "observed": value["observed_utc"], "charter_ok": charter_ok,
            "charter_blob": blobs[0] or UNKNOWN, "classifier_blob": blobs[1] or UNKNOWN, "prs": prs}, None


def vote_slot(agent: str, status: str) -> str | None:
    """The consensus slot a current-head vote fills, by the charter's identity map; None when it fills none."""
    if status == "rco_pass" and agent in RECOGNIZED_RCOS:
        return agent
    if status == "build_consensus_pass":
        return BUILD_SLOT.get(agent)
    return None


class _Inputs:
    """The usable rows of one core snapshot. A row that fails the allowlist is refused by position only."""

    def __init__(self, snapshot: Mapping[str, Any], context: Any = None):
        if snapshot.get("schema") != SNAPSHOT_SCHEMA:
            raise WorkspaceError("snapshot schema is not wd.bridge-mind.v1")
        if snapshot.get("authority") != "none" or snapshot.get("scope") != SNAPSHOT_SCOPE:
            raise WorkspaceError("snapshot authority must be none and scope bounded_projection_not_gate")
        source = snapshot.get("source") if isinstance(snapshot.get("source"), Mapping) else {}
        self.now = parse_time(source.get("as_of_utc"))
        if self.now is None:
            raise WorkspaceError("snapshot source needs an explicit as_of_utc")
        self.source_ref = _match(REF, source.get("last_event_ref")) or UNKNOWN
        coverage = source.get("coverage")
        self.coverage = coverage if coverage in ("complete", "partial", "unknown") else "unknown"
        # The core contract: blocked is null, or {active: true, offset, reason}. Anything else is read as blocked.
        blocked = source.get("blocked")
        self.blocked = None
        if isinstance(blocked, Mapping) and blocked.get("active") is True:
            offset = blocked.get("offset")
            self.blocked = {"reason": _match(REASON, blocked.get("reason")) or "unrecognized",
                            "offset": offset if type(offset) is int and offset >= 0 else None}
        elif blocked is not None:
            self.blocked = {"reason": "unrecognized", "offset": None}
        self.refused: list[dict[str, str]] = []
        self.prs = self._table(snapshot, "prs", self._pr)
        self.lanes = self._table(snapshot, "lanes", self._lane)
        self.commands = self._facts(snapshot)
        self.context, self.context_reason = read_context(context, self.now)

    def context_row(self, pr: Mapping[str, Any]) -> dict[str, Any] | None:
        return None if self.context is None else self.context["prs"].get(pr["pr"])

    def suppressed(self, pr: Mapping[str, Any]) -> str | None:
        """Why the context leaves this PR without suggestions (its state, or head_mismatch), or None."""
        row = self.context_row(pr)
        if row is None:
            return None
        if row["state"] != "OPEN":
            return row["state"]
        return "head_mismatch" if row["head"] != pr["head"] else None

    def authors(self, pr: Mapping[str, Any]) -> frozenset[str]:
        """The declared commit authors of a current, open PR from the context; empty when not known."""
        row = self.context_row(pr)
        if row is None or self.suppressed(pr) is not None or row["authors"] is None:
            return frozenset()
        return frozenset(row["authors"])

    def current_heads(self, pr: Mapping[str, Any]) -> dict[str, str]:
        """The PR head the snapshot names, and the context head of an open PR when it differs (declared)."""
        heads = {pr["head"]: "snapshot"} if pr["head"] else {}
        row = self.context_row(pr)
        if row is not None and row["state"] == "OPEN" and row["head"] not in heads:
            heads[row["head"]] = "context"
        return heads

    def context_head_votes(self, pr: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The stale votes of an open PR bound to the other head its context saw; they never count here."""
        heads = self.current_heads(pr)
        return [vote for vote in pr["stale_votes"] if heads.get(vote["head"]) == "context"]

    def computed_class(self, pr: Mapping[str, Any]) -> str | None:
        """``protected`` or ``not_protected`` from a used context with loaded charter evidence, else None."""
        row = self.context_row(pr)
        if row is None or not self.context["charter_ok"] or self.suppressed(pr) is not None \
                or row["path_class"] is None:
            return None
        return "protected" if row["path_class"]["a_hits"] or row["path_class"]["denylist_hits"] else "not_protected"

    def _refuse(self, where: str, row: Any, reason: str) -> None:
        self.refused.append({"where": where, "ref": _first_ref(row), "reason": reason})

    def _table(self, snapshot: Mapping[str, Any], key: str, reader) -> list[Any]:
        table = snapshot.get(key)
        if table is None:
            return []
        if not isinstance(table, Mapping):
            self._refuse(key, None, "not an object")
            return []
        out = []
        for index, name in enumerate(sorted(table, key=str)):
            row = table[name]
            where = f"{key}[{index}]"
            if not isinstance(row, Mapping) or not isinstance(name, str) or len(name) > KEY_LIMIT or _has_marker(name):
                self._refuse(where, row, "not an object with a safe key")
                continue
            value = reader(name, row, where)
            if value is not None:
                out.append(value)
        return out

    def _votes(self, rows: list[Any], where: str, *, veto: bool = False) -> tuple[list[dict[str, Any]], int]:
        """Vote-shaped rows and how many were refused.

        Only a declared identity (a valid ``agent_uuid`` with ``verification`` ``declared``) is ``verified``; the
        core marks an undeclared one ``UNKNOWN`` and ``unverified``, and a row whose two fields disagree is refused.
        A veto may name no head, and its status is any safe token, ``UNKNOWN`` included (the core keeps the sent one).
        """
        out, refused = [], 0
        for index, vote in enumerate(rows):
            vote = vote if isinstance(vote, Mapping) else {}
            head, uuid, verification = vote.get("head"), vote.get("agent_uuid"), vote.get("verification")
            declared = isinstance(uuid, str) and bool(UUID.fullmatch(uuid)) and verification == "declared"
            item = {"agent": _match(SAFE_ID, vote.get("agent")),
                    "head": UNKNOWN if veto and head == UNKNOWN else _match(HEAD, head),
                    "status": _match(SAFE_ID if veto else REASON, vote.get("status")),
                    "refs": _refs(vote.get("source_refs")), "verified": declared}
            if None in item.values() or not (declared or (uuid == UNKNOWN and verification == "unverified")):
                self._refuse(f"{where}[{index}]", vote, "vote needs agent, head, status, source_refs, and an "
                             "agent_uuid that agrees with its verification")
                refused += 1
                continue
            out.append(item)
        return out, refused

    def _pr(self, name: str, row: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        refs = _refs(row.get("source_refs"))
        number = row.get("pr")
        lists = [row.get(key) for key in ("votes", "stale_votes", "veto_observations")]
        if refs is None or type(number) is not int or str(number) != name or not all(isinstance(v, list) for v in lists):
            self._refuse(where, row, "pr needs pr equal to its key, source_refs and vote lists")
            return None
        head = _match(HEAD, row.get("head"))
        ci = row.get("reported_ci") if isinstance(row.get("reported_ci"), Mapping) else {}
        ci_refs = _refs(ci.get("source_refs"))
        # Green only when reported for this exact head, never because the core reset an older report.
        ci_green = head is not None and ci.get("status") == "reported_green" and ci.get("head") == head \
            and ci_refs is not None
        votes, _ = self._votes(lists[0], f"{where}.votes")
        stale, _ = self._votes(lists[1], f"{where}.stale_votes")
        # A vote in the current list at another head is stale too: it can never fill a slot.
        stale += [vote for vote in votes if vote["head"] != head]
        votes = [vote for vote in votes if vote["head"] == head]
        vetoes, unreadable = self._votes(lists[2], f"{where}.veto_observations", veto=True)
        # A recognized RCO's own declared recusal counts only at the PR head; an unreadable list counts as none.
        rows = row.get("recusals", [])
        recusals, _ = self._votes(rows if isinstance(rows, list) else [], f"{where}.recusals")
        recusals = [item for item in recusals if item["verified"] and item["head"] == head
                    and item["agent"] in RECOGNIZED_RCOS and item["status"] == "rco_recused"]
        klass = row.get("class") if row.get("class") in ("a", "b", "on-allowlist") else UNKNOWN
        return {"pr": number, "head": head, "refs": refs, "ci_green": ci_green, "ci_refs": ci_refs or [],
                "class": klass, "votes": votes, "stale_votes": stale, "vetoes": vetoes,
                "veto_unreadable": unreadable > 0, "recused": sorted({item["agent"] for item in recusals}),
                "recusal_refs": sorted({ref for item in recusals for ref in item["refs"]})}

    def _lane(self, name: str, row: Mapping[str, Any], where: str) -> dict[str, Any] | None:
        refs, agent, uuid = _refs(row.get("source_refs")), _match(SAFE_ID, row.get("agent")), row.get("agent_uuid")
        # The core keys a lane by agent|uuid, or agent|UNKNOWN|ref when the identity is not declared.
        keyed = agent is not None and isinstance(uuid, str) and (
            (UUID.fullmatch(uuid) and name == f"{agent}|{uuid}")
            or (uuid == UNKNOWN and name.startswith(f"{agent}|{UNKNOWN}|") and REF.fullmatch(name.rsplit("|", 1)[1])))
        if refs is None or not keyed:
            self._refuse(where, row, "lane needs a safe agent, an agent_uuid matching its key, and source_refs")
            return None
        lane = {"agent": agent, "key": name, "refs": refs, "claims": []}
        claims = row.get("claims")
        if isinstance(claims, Mapping):
            lane["claims"] = self._claims(claims, f"{where}.claims")
        elif claims not in (None, UNKNOWN):
            self._refuse(f"{where}.claims", None, "claims is neither an object nor UNKNOWN")
        return lane

    def _lease(self, row: Mapping[str, Any]) -> datetime | str | None:
        """The lease time, UNKNOWN, or None when it is not a time or disagrees with the row's ``expired``."""
        lease = row.get("lease_expires_utc")
        expires = parse_time(lease) if lease != UNKNOWN else UNKNOWN
        stated = row.get("expired")
        if expires not in (None, UNKNOWN) and isinstance(stated, bool) and stated != (expires <= self.now):
            return None
        return expires

    def _claims(self, claims: Mapping[str, Any], where: str) -> list[dict[str, Any]]:
        """The usable task claims of one lane (the core's ``by_task`` rows).

        The lane-level fields must be what the rows give: ``write_scope`` the union of the row scopes in any
        order (UNKNOWN when one is UNKNOWN), ``lease_expires_utc`` the one common lease or UNKNOWN, and
        ``expired`` agreeing with it. If not, every claim of the lane is refused together. Otherwise a row that is
        not usable is refused by its own position (in task_id order) and the lane's other task claims still count.
        """
        rows = claims.get("by_task")
        # Read in task_id order, so a refusal names the same position whatever the input order.
        rows = sorted(rows, key=lambda row: str(row.get("task_id")) if isinstance(row, Mapping) else "") \
            if isinstance(rows, list) else None
        scopes = [row.get("write_scope") if isinstance(row, Mapping) else None for row in rows or []]
        union = sorted({path for scope in scopes for path in scope}) \
            if all(isinstance(scope, list) and all(isinstance(path, str) for path in scope) for scope in scopes) \
            else UNKNOWN
        stated = claims.get("write_scope")
        if isinstance(stated, list) and all(isinstance(path, str) for path in stated):
            stated = sorted(set(stated))
        leases = [row.get("lease_expires_utc") if isinstance(row, Mapping) else None for row in rows or []]
        common = leases[0] if leases and all(lease == leases[0] for lease in leases) else UNKNOWN
        tasks = [row.get("task_id") if isinstance(row, Mapping) else None for row in rows or []]
        if rows is None or _refs(claims.get("source_refs")) is None or stated != union \
                or claims.get("lease_expires_utc") != common or self._lease(claims) is None \
                or len(set(map(str, tasks))) != len(tasks):
            self._refuse(where, claims, "claims need by_task rows with unique task ids, and a write_scope, "
                         "lease and expired that agree with those rows")
            return []
        out = []
        for index, row in enumerate(rows):
            row = row if isinstance(row, Mapping) else {}
            scope, refs, expires = row.get("write_scope"), _refs(row.get("source_refs")), self._lease(row)
            paths = [path if _claim_key(path) is not None else None for path in scope] \
                if isinstance(scope, list) else None
            task = _match(TASK, row.get("task_id"))
            if task is None or refs is None or expires is None or (paths is None and scope != UNKNOWN) \
                    or (paths is not None and None in paths):
                self._refuse(f"{where}.by_task[{index}]", row, "task claim needs a safe task_id, safe paths inside "
                             "their root or UNKNOWN, a lease time or UNKNOWN that agrees with expired, and source_refs")
                continue
            out.append({"task": task, "paths": paths, "expires": expires, "refs": refs})
        return out

    def _facts(self, snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Only ``command`` facts feed the workspace; a fact whose TTL ended at ``as_of`` is not used."""
        rows = snapshot.get("facts")
        out = []
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            where = f"facts[{index}]"
            if not isinstance(row, Mapping) or row.get("kind") != "command":
                continue
            refs, observed, ttl = _refs(row.get("source_refs")), parse_time(row.get("observed_at_utc")), row.get("ttl_seconds")
            if refs is None or observed is None:
                self._refuse(where, row, "command fact needs observed_at_utc and source_refs")
                continue
            if type(ttl) is int and observed + timedelta(seconds=ttl) <= self.now:
                continue
            value = row.get("value") if isinstance(row.get("value"), Mapping) else {}
            request_id, sender = _match(SAFE_ID, value.get("request_id")), _match(SAFE_ID, value.get("from"))
            expected = [_match(SAFE_ID, agent) for agent in value.get("expected_responders") or []]
            answered = [_match(SAFE_ID, agent) for agent in value.get("answered_by") or []]
            if request_id is None or sender is None or None in expected or None in answered:
                self._refuse(where, row, "command needs a safe request_id, sender and responders")
                continue
            out.append({"request_id": request_id, "from": sender, "at": observed, "refs": refs,
                        "unanswered": sorted(set(expected) - set(answered))})
        return out


def _tension(rule: str, subject: str, claim: str, observation: str, actors: Iterable[str],
             refs: Iterable[str]) -> dict[str, Any]:
    return {"tension_id": tension_id(rule, subject, claim, observation), "rule": rule,
            "severity": RULE_SEVERITY[rule], "subject": subject, "claim": claim, "observation": observation,
            "actors": sorted({actor for actor in actors if actor and actor != UNKNOWN}), "refs": sorted(set(refs))}


def _empty_slots(pr: Mapping[str, Any], authors: frozenset[str] = frozenset()) -> list[str]:
    """Vote slots with no verified current vote. A recognized RCO who is a declared commit author of the PR, or who
    declared ``rco_recused`` at the PR head, fills no RCO slot (author is not reviewer), so that slot is not missing
    and that RCO is never asked for it."""
    filled = {vote_slot(vote["agent"], vote["status"]) for vote in pr["votes"] if vote["verified"]} - {None}
    excused = set(authors) | set(pr.get("recused", ()))
    return [slot for slot in VOTE_SLOTS if slot not in filled and slot not in excused]


def _author_note(inputs: "_Inputs", pr: Mapping[str, Any]) -> str:
    excluded = sorted(set(RECOGNIZED_RCOS) & inputs.authors(pr))
    note = "" if not excluded else (f"; {', '.join(excluded)} is a declared commit author of this PR and fills no "
                                    "RCO slot")
    recused = [agent for agent in pr.get("recused", ()) if agent not in excluded]
    return note + ("" if not recused else f"; {', '.join(recused)} declared rco_recused at this head and fills no "
                                          "RCO slot")


def _suggestable(pr: Mapping[str, Any]) -> bool:
    # Any veto observation, or one that cannot be read, means no vote or signature suggestion at all.
    return pr["head"] is not None and pr["ci_green"] and not pr["vetoes"] and not pr["veto_unreadable"]


def _listed(items: Sequence[str]) -> str:
    shown = list(items[:CONTEXT_LIST_LIMIT])
    if len(items) > CONTEXT_LIST_LIMIT:
        shown.append(f"and {len(items) - CONTEXT_LIST_LIMIT} more")
    return ", ".join(shown) if shown else "none"


def _context_tensions(inputs: _Inputs) -> list[dict[str, Any]]:
    """The context's own tensions: refused or applied, changed heads, and declared classes left unverified."""
    out = []
    if inputs.context_reason is not None:
        out.append(_tension("context_refused", "context", "a PR context was supplied",
                            f"it is not used ({inputs.context_reason}); class and actionability stay as without "
                            "one", [], [inputs.source_ref]))
    context = inputs.context
    if context is not None:
        closed, moved, computed = [], [], 0
        for pr in inputs.prs:
            reason, row = inputs.suppressed(pr), inputs.context_row(pr)
            if reason in ("MERGED", "CLOSED"):
                closed.append(f"PR{pr['pr']} {reason}")
            elif reason == "head_mismatch":
                moved.append(f"PR{pr['pr']}")
                at_context = inputs.context_head_votes(pr)
                named = sorted({f"{vote['agent']} {vote['status']}" for vote in at_context})
                votes = (f"; {len(named)} vote(s) in the bridge view are bound to that head and are not counted "
                         f"here: {', '.join(named)}" if named else "")
                out.append(_tension("context_head_mismatch", f"pr:{pr['pr']}:context_head",
                                    f"the bridge view of PR{pr['pr']} is at "
                                    f"{pr['head'][:12] if pr['head'] else UNKNOWN}",
                                    f"the context capture {context['capture'][:16]} saw head {row['head'][:12]} on "
                                    f"{row['branch']}; no suggestion is made for the bridge head" + votes,
                                    [],  # bridge-view lag: no lane can resolve it, so it asks no one
                                    pr["refs"] + [ref for vote in at_context for ref in vote["refs"]]))
            computed += inputs.computed_class(pr) is not None
        charter = (f"charter loaded, charter blob {context['charter_blob']}, classifier blob "
                   f"{context['classifier_blob']}" if context["charter_ok"] else "charter evidence not loaded, "
                   "so no class is computed")
        out.append(_tension("context_applied", f"context:{context['capture'][:16]}",
                            f"caller-supplied PR context, capture sha256 {context['capture']} observed "
                            f"{context['observed']}; {charter}",
                            f"{len(context['prs'])} PRs in context; no suggestion for: {_listed(closed)}; head "
                            f"mismatch: {_listed(moved)}; computed class for {computed} PRs; tensions, votes and "
                            "vetoes stay as observed", [], [inputs.source_ref]))
    for pr in inputs.prs:
        # Every reported vote, a declared (a) class, but no computed protected hit: the signature need is UNKNOWN.
        # A blocked source is not current, so it gives no such head-bound observation.
        if inputs.blocked is None and _suggestable(pr) and not _empty_slots(pr, inputs.authors(pr)) \
                and pr["class"] == "a" \
                and inputs.suppressed(pr) is None and inputs.computed_class(pr) != "protected":
            out.append(_tension("class_unverified", f"pr:{pr['pr']}:class",
                                f"PR{pr['pr']} at {pr['head'][:12]} has every reported vote and a declared (a) class",
                                "loaded charter evidence computes no protected path hit, so the declared and the "
                                "computed class disagree" if inputs.computed_class(pr) == "not_protected" else
                                "no computed protected path hit from loaded charter evidence supports it, so whether "
                                "an operator signature is needed is UNKNOWN", [],
                                pr["refs"] + [ref for vote in pr["votes"] for ref in vote["refs"]]))
    return out


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
    live: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for lane in inputs.lanes:
        for claim in lane["claims"]:
            # Bridge claim events carry no lease, so most leases are UNKNOWN: such a claim is not expired, and it
            # still counts for overlap. Only a known lease that ended at or before as_of makes a claim expired.
            if claim["expires"] != UNKNOWN and claim["expires"] <= inputs.now:
                tensions.append(_tension("expired_claim", f"claim:{lane['key']}:{claim['task']}",
                                         f"{lane['agent']} holds a write claim for {claim['task']}",
                                         "its lease ended at or before as_of; that is not completed work",
                                         [lane["agent"]], claim["refs"]))
            elif claim["paths"] is not None:
                live.setdefault(lane["key"], (lane, []))[1].append(claim)
    lanes = [live[key] for key in sorted(live)]
    for index, (lane_a, claims_a) in enumerate(lanes):
        for lane_b, claims_b in lanes[index + 1:]:
            if lane_a["agent"] == lane_b["agent"]:
                continue
            # Every live task claim of one lane against every one of the other; one tension per pair of lanes.
            hits, refs = set(), set()
            for claim_a in claims_a:
                for claim_b in claims_b:
                    found = scopes_overlap(claim_a["paths"], claim_b["paths"])
                    if found:
                        hits.update(found)
                        refs.update(claim_a["refs"] + claim_b["refs"])
            if hits:
                tensions.append(_tension("claim_overlap", f"claims:{lane_a['key']}|{lane_b['key']}",
                                         f"{lane_a['agent']} and {lane_b['agent']} both hold write claims that are "
                                         "not expired",
                                         "overlapping paths: " + ", ".join(sorted(hits)),
                                         [lane_a["agent"], lane_b["agent"]], refs))
    for pr in inputs.prs:
        # A vote bound to the head an open PR's context saw is not stale noise: context_head_mismatch names it.
        stale = [vote for vote in pr["stale_votes"] if vote not in inputs.context_head_votes(pr)]
        if stale:
            # One tension per PR, naming each stale vote and keeping every contributing ref. The core also retires
            # a vote at the PR head when the same identity later sends a blocking signal.
            named = sorted({f"{vote['agent']} {vote['status']}@{vote['head'][:12]}"
                            + (" retired at the PR head" if vote["head"] == pr["head"] else "")
                            for vote in stale})
            # An identity that already sent the same verified vote at a current head (the PR head, or the head an
            # open PR's context saw) has nothing to do about its older vote: it is named, never asked (RCO1 10:38Z).
            current = {(vote["agent"], vote["status"]) for vote in pr["votes"] + inputs.context_head_votes(pr)
                       if vote["verified"]}
            done = sorted({vote["agent"] for vote in stale if (vote["agent"], vote["status"]) in current})
            tensions.append(_tension("stale_vote", f"pr:{pr['pr']}:stale_votes",
                                     f"PR{pr['pr']} at {pr['head'][:12] if pr['head'] else UNKNOWN} has "
                                     f"{len(named)} stale vote(s)",
                                     "; ".join(named) + "; a stale vote cannot approve it"
                                     + (f"; already voted at a current head: {', '.join(done)}" if done else ""),
                                     sorted({vote["agent"] for vote in stale} - set(done)),
                                     [ref for vote in stale for ref in vote["refs"]] + pr["refs"]))
        heads = inputs.current_heads(pr)
        for veto in pr["vetoes"]:
            # The same verified identity passed later at a current head: the older veto stays observed and is
            # never cleared, but it is labelled and ranked low so questions point at live work.
            later = sorted({(vote["head"], tuple(vote["refs"])) for vote in pr["votes"] + pr["stale_votes"]
                            if vote["agent"] == veto["agent"] and vote["status"] == "rco_pass" and vote["verified"]
                            and vote["head"] in heads and vote["head"] != veto["head"]})
            if later and veto["head"] != UNKNOWN:  # a veto without a head may be newer than any pass
                head, refs = later[0]
                tensions.append(_tension(
                    "veto_own_later_pass", f"pr:{pr['pr']}:veto:{veto['agent']}:{veto['head'][:12]}",
                    f"{veto['agent']} sent {veto['status']} on PR{pr['pr']} at {veto['head'][:12]}, then rco_pass at "
                    f"{head[:12]}" + (" (the head from the PR context capture)" if heads[head] == "context" else ""),
                    "the veto stays observed and is never cleared here; the same identity passed at a current head",
                    [veto["agent"]], veto["refs"] + list(refs) + pr["refs"]))
                continue
            tensions.append(_tension("veto_observed", f"pr:{pr['pr']}:veto:{veto['agent']}:{veto['head'][:12]}",
                                     f"{veto['agent']} sent {veto['status']} on PR{pr['pr']} at {veto['head'][:12]}",
                                     "the veto is observed; whether it is still active is UNKNOWN, never cleared here",
                                     [veto["agent"]], veto["refs"] + pr["refs"]))
    tensions.extend(_context_tensions(inputs))
    for command in inputs.commands:
        if command["unanswered"] and inputs.now - command["at"] >= COMMAND_GRACE:
            tensions.append(_tension("unacknowledged_command", f"request:{command['request_id']}",
                                     f"{command['from']} sent request {command['request_id']}",
                                     "no bound reply from " + ", ".join(command["unanswered"]),
                                     command["unanswered"], command["refs"]))
    # The same contradiction seen twice is one tension with the union of its refs, whatever the input order.
    unique: dict[str, dict[str, Any]] = {}
    for tension in tensions:
        kept = unique.setdefault(tension["tension_id"], tension)
        kept["refs"] = sorted(set(kept["refs"]) | set(tension["refs"]))
        kept["actors"] = sorted(set(kept["actors"]) | set(tension["actors"]))
    return [unique[key] for key in sorted(unique)]


def _pr_attention(inputs: _Inputs, pr: Mapping[str, Any]) -> dict[str, Any] | None:
    # A context that says the PR is not open, or saw another head, leaves it without a suggestion.
    if not _suggestable(pr) or inputs.suppressed(pr) is not None:
        return None
    empty = _empty_slots(pr, inputs.authors(pr))
    refs = pr["refs"] + pr["ci_refs"] + [ref for vote in pr["votes"] for ref in vote["refs"]] + pr["recusal_refs"]
    subject, head = f"pr:{pr['pr']}", pr["head"][:12]
    if not empty:
        # Only a computed protected hit from loaded charter evidence; a declared class never suffices.
        if inputs.computed_class(pr) != "protected":
            return None
        path_class = inputs.context_row(pr)["path_class"]
        return {"kind": "signature_only", "subject": subject, "actors": ["operator"], "refs": refs,
                "detail": f"PR{pr['pr']} at {head} has every reported vote and a computed protected path hit "
                          f"({len(path_class['a_hits'])} a_hits, {len(path_class['denylist_hits'])} denylist, "
                          f"capture {inputs.context['capture'][:16]}): only the operator signature is left"
                          + _author_note(inputs, pr)}
    actors = sorted({agent for slot in empty for agent in
                     ([slot] if slot in RECOGNIZED_RCOS else [a for a, s in BUILD_SLOT.items() if s == slot])})
    return {"kind": "last_missing_vote" if len(empty) == 1 else "votes_missing", "subject": subject, "actors": actors,
            "refs": refs, "detail": f"PR{pr['pr']} at {head} is reported CI-green; missing: {', '.join(empty)}"
                                    + _author_note(inputs, pr)}


def derive_attention(inputs: _Inputs, tensions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    # A blocked source is not current: no head-bound suggestion is made from it, only its tensions.
    prs = [] if inputs.blocked is not None else inputs.prs
    items = [item for item in (_pr_attention(inputs, pr) for pr in prs) if item is not None]
    # The tensions of a PR the context says is merged or closed stay in the tensions, not in attention.
    closed = {str(pr["pr"]) for pr in inputs.prs if inputs.suppressed(pr) in ("MERGED", "CLOSED")}
    for tension in tensions:
        about = _PR_SUBJECT.fullmatch(tension["subject"])
        # context_applied is a record of what the context did, not a contradiction to resolve.
        if (about is not None and about.group(1) in closed) or tension["rule"] in NOT_ATTENTION_RULES:
            continue
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
        return f"{item['detail']}. Can {who} give it at this exact head?"
    if item["kind"] == "votes_missing":
        return f"{item['detail']}. Which of these can {who} give at this exact head?"
    if item["kind"] == "signature_only":
        return f"{item['detail']}. Is it on the morning signature list?"
    return f"How can {who} resolve {item['detail']}?"


def _entry(item: Mapping[str, Any] | None, who: str, partial: bool) -> dict[str, Any]:
    if item is None:
        return {"question": UNKNOWN, "attention_rank": None, "refs": [UNKNOWN]}
    question = _question(item, who)
    if partial:
        question += " Coverage is not complete, so check the exact head first."
    if len(question) > QUESTION_LIMIT:
        return {"question": UNKNOWN, "attention_rank": None, "refs": [UNKNOWN]}
    return {"question": question, "attention_rank": item["rank"], "refs": list(item["refs"])}


def derive_next_question(inputs: _Inputs, attention: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    partial = inputs.coverage != "complete"
    agents = ({lane["agent"] for lane in inputs.lanes} | {actor for item in attention for actor in item["actors"]}) - {UNKNOWN}
    # A pseudo-lane keeps its entry, UNKNOWN: its facts stay visible but it is asked nothing.
    lanes = {agent: _entry(None if agent in PSEUDO_AGENTS else
                           next((item for item in attention if agent in item["actors"]), None), agent, partial)
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


def reflection(snapshot: Mapping[str, Any], previous: Mapping[str, Any] | None = None,
               context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The three reflection fields for one snapshot and an optional PR context. Pure: no clock, no file, no network."""
    inputs = _Inputs(snapshot, context)
    tensions = derive_tensions(inputs)
    previous_ids = {str(item.get("tension_id")) for item in (previous or {}).get("tensions") or []
                    if isinstance(item, Mapping)}
    for tension in tensions:
        tension["lifecycle"] = "persisting" if tension["tension_id"] in previous_ids else "new"
    attention = derive_attention(inputs, tensions)
    fields = {"tensions": tensions, "attention": attention, "next_question": derive_next_question(inputs, attention)}
    _check_output(fields)
    return fields


def reflect(snapshot: Mapping[str, Any], previous: Mapping[str, Any] | None = None,
            context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A copy of the snapshot with only its reflection fields filled; every other field is unchanged.

    The complete copy, every key and value and not only the reflection fields, is guarded before it is
    returned: a guarded word anywhere refuses the whole snapshot. Nothing is stripped or rewritten. The context
    changes only the reflection fields; it is never copied into the snapshot.
    """
    out = copy.deepcopy(dict(snapshot))
    out.update(reflection(snapshot, previous, context))
    guard_object(out)
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
    """One JSON object under the canonical bridge JSON contract (no duplicate keys, no NaN, safe numbers)."""
    try:
        return parse_bridge_json_object(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise WorkspaceError("an input file is not one canonical JSON object") from exc


def serialize(reflected: Mapping[str, Any], *, as_json: bool) -> str:
    """The exact text to emit, guarded as a whole: the object first, then the final text itself."""
    guard_object(reflected)
    try:
        text = (json.dumps(reflected, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
                if as_json else render_text(reflected))
    except (TypeError, ValueError, KeyError) as exc:
        raise WorkspaceError("the reflected snapshot cannot be serialized") from exc
    if _has_marker(text):
        raise WorkspaceError("a guarded word is in the output; nothing is written")
    return text


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge mind reflective workspace v0 (read-only, dormant).")
    parser.add_argument("--snapshot", type=Path, required=True, help="A wd.bridge-mind.v1 snapshot JSON.")
    parser.add_argument("--previous", type=Path, default=None, help="The previous reflected snapshot, for lifecycle.")
    parser.add_argument("--context", type=Path, default=None,
                        help="An optional caller-supplied wd.bridge-mind-pr-context.v1 capture (observational only).")
    parser.add_argument("--json", action="store_true", help="Print the reflected snapshot JSON instead of text.")
    args = parser.parse_args(argv)
    try:
        previous = _read_object(args.previous) if args.previous is not None else None
        context = _read_object(args.context) if args.context is not None else None
        text = serialize(reflect(_read_object(args.snapshot), previous, context), as_json=args.json)
    except WorkspaceError as exc:
        sys.stderr.write(f"bridge mind workspace refused: {exc}\n")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
