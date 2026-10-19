"""JOIN card HEAR: an observe-only delivery ledger and formatter over one frozen lane view. Sandbox, never a gate.

Why (PR1905, night of 2026-10-13/14): a CI-green PR waited 9 h 46 min with one approval slot empty (the Tools build
vote). The step-1 lane view of that night shows the empty slot only as a line inside the owning lane's own card. No
line crosses a wait threshold, none is visible to the operator, and nothing says whether the lane was asked, reached,
read it or answered.

``deliver(view, receipts, *, now_utc, notified=())`` reads one frozen lane view (``wd.mind-lane-view.v1a``, the
step-1 card, ``sandbox/mind/step1/lane_view.py``) and one explicit receipts document (``wd.mind-delivery-receipts.v1``).
It returns the ledger, the operator lines, one card text per lane and the identities notified after this turn. It is
pure: no clock, file, network, process or bridge write, and the same inputs give the same bytes. It runs no
collector of its own: receipts come only from the document passed in.

Frozen semantics v1 (fixed before the tests):

S1 Condition. A wait condition is a view PR record with state ``action`` and exactly ONE empty slot. CI green at
   the GitHub head, no observed veto, every body verified and the slot's eligibility (commit-author and recused RCOs
   fill no slot) all come from the lane view's own verified reading; nothing here re-derives or infers them. The
   owning lane is the slot's owner. Two or more empty slots, a full slot set (``votes_complete``), or an abstained
   or refused PR is no condition.
   (v1.2) Every PR record must be a mapping, and an action record must hold the lane view's empty slots, credited
   votes (slot and ref), PR number (equal to its key), full head, branch and checks, each of the type the lane view
   writes; otherwise the turn is refused with a fixed message, never a raw error. A record in any other state is
   not read beyond its state. Keys of any text are ordered without raising.
   (v1.3) An action record's checks must also be the lane view's own ``n/n`` form (every required check SUCCESS at
   that head; n from 1 to 999, so the echoed field stays short). With that, every field a HEAR text line echoes is
   a validated identifier or ref, a stamped time, a count or fixed wording, so no input can add, split or reorder a
   line.
S2 Wait. ``wait = observed - since``. ``observed`` is the view's ``as_of_utc``; ``since`` is its
   ``waiting_since_utc``, the later of the last credited vote and the CI completion at that head. Both are whole
   seconds, cut and never rounded up. The wait is never extrapolated to ``now``. When since is UNKNOWN or after
   observed, the condition is ``unknown`` (``wait_start_unknown``, ``wait_start_after_observed``).
   (v1.1) Every time this module reads is cut to whole seconds when it is read, before any comparison: now, as_of,
   waiting_since, and each receipt's at and complete-through time. A printed time is exactly the time compared.
S3 Threshold. ``alert`` only when wait > 30 min, i.e. strictly more than 1800 s. 29:59 and 30:00 are
   ``below_threshold``; 30:01 alerts.
S4 Card trust. The view must report coverage ``complete`` and be at most 15 min old at ``now`` (the workspace's
   context max age), and ``now`` must not be before it. Otherwise every condition is ``unknown``
   (``card_coverage_not_complete``, ``card_stale``, ``card_from_future``). An unknown condition is never shown as a
   wait. It is listed in one UNKNOWN line with its PR, head and reason, and never notified.
   (v1.2) Every lane card names the card trust, and a card that is not current states no absence: a lane without a
   condition then says that no wait is stated, and so does the operator preview when it has no alert line.
   (v1.4) A current operator preview without an alert line states its absence ("no new wait over 30 min on a last
   open slot") only when no condition is unknown and no new wait was carried; otherwise its UNKNOWN line and its
   counts stand alone, as a lane card states its own absence only when it has no other line.
S5 Receipts. For the condition's lane, PR and exact head, each of requested, delivered, read and answered has one
   of three states:
   * OBSERVED: the earliest matching row at or before ``observed``, with its ref and time;
   * NONE: no matching row, and that kind's source is complete through ``observed``;
   * UNKNOWN: there is no source for the kind, its coverage ends before ``observed``, or the receipts document is
     unreadable.
   Rows of another lane, another PR or another head never count. The receipts document is strict: an unexpected
   key, kind, lane, head or time, or a guarded word anywhere in it, makes it unreadable as a whole. Receipts never
   create, remove or delay an alert; they only describe it.
   (v1.1) Reading receipts never raises: a value of an unexpected type at any depth makes the document unreadable.
   The CLI reads each file as strict JSON (a duplicate key, NaN or Infinity, or bytes that are not plain UTF-8 JSON
   are refused). A receipts file that cannot be read that way is an unreadable document; a view or notified file
   that cannot be read that way refuses the turn. (v1.2) A file nested too deeply to parse is not readable that way.
   (v1.5) The document must also agree with the turn's clock and with itself: no source is complete through a time after
   ``now``, every row's kind has a source, and no row is after its kind's complete-through time. A NONE then never rests
   on coverage claimed for a time not yet reached, and every row lies inside its kind's declared complete coverage.
   These are internal checks only: that a source really holds every matching row up to its complete-through time is
   the source's own claim, trusted and never checked here, so a NONE is only as true as that claim. A document that
   breaks any of these checks is unreadable as a whole. A NONE states its absence through ``observed``, the time it was
   checked for, never through its source's later complete-through time: a matching row after ``observed`` is not used,
   so that later time could state an absence that the document contradicts.
S6 Identity and dedupe. identity = sha256 of the canonical {kind, pr, full head, slot, lane, since}; since is the S2
   value written as whole UTC seconds with ``Z`` (v1.1), never the view's own spelling. An alert whose
   identity is in ``notified`` is not shown again. It is counted in one line of unchanged waits with its PR and
   identity. A new head, slot, lane or since is a new identity, and it is shown.
S7 Size. A lane card is at most 2048 UTF-8 bytes. Alert lines that do not fit are not shown and are NOT added to
   the notified set, so the next turn shows them; one line counts them and cites the first. The notified set after
   a turn is the notified set before it plus exactly the identities shown in that turn.
S8 Lines. Each alert line uses fixed ``key=value`` fields, so any model family reads the whole conjunction at once
   (Grok tip f3206f6e). The fields are: pr, the full 40-hex head, slot, lane, last_open_slot=yes, wait in whole
   minutes, since, observed, coverage, CI at head, veto none observed, the credited vote refs, the four receipt
   states with refs, and the identity. ``last_open_slot=yes`` is printed only for an ``alert``. The output holds
   only identifiers, refs, SHAs, times, counts and fixed wording, and passes the workspace's decoded guard; a guard
   hit refuses the whole output. Known limitation, kept on purpose: one guarded word in one PR's echoed field
   therefore withholds every lane's card in that turn; refusing only that row needs a separately registered
   semantics change.
S9 View binding (v1.2). The output's card names ``view_canonical_sha256``, the sha256 of the view's canonical JSON
   (``lane_view.canonical``), so a reader can bind the delivery to the exact view it read; a JSON copy of a lane
   view (string keys only) has the same digest. A view without canonical JSON (NaN or Infinity, a set, bytes, mixed
   key types, nesting too deep) refuses the turn.

v1.1 (2026-10-14, after RCO1's probes of 2302ee59, plan 4FEAA88E, result 1185E841): F1 a sources entry that was not
a mapping made reading raise, so one malformed receipts document dropped every lane's alert; F2 the CLI kept the
last of two duplicate keys, which turned an observed receipt into an affirmative NONE; F3 sub-second times were
compared uncut.

v1.2 (2026-10-15, the evening audit): R1 a malformed view PR record raised a raw TypeError or KeyError (RCO2's
public G-L result of 19:02:55Z, processed by Lead at 19:13:18Z; the type sweeps that now test S1 are mine); R2 a
lane card did not name the card trust, so a stale, future or partial card of a lane without a condition said "no
wait over 30 min" (my static G-L map and my registered lane-card age observation of 19:35:33Z); R3 nothing bound a
delivery to the exact view it read (S9). The JOIN card v0.2 checks that binding.

v1.3 (2026-10-16, fable-5's own probe): an action record's checks was read as any text and echoed as ``ci_at_head``
in the alert line, so a view record whose checks held a line break could add a forged line to a lane card and to the
operator preview. S1 now requires the lane view's ``n/n`` form.

v1.4 (2026-10-16, a design observation in RCO1's rerun of its line-safety harness on JOIN card v0.3): with one
condition unknown on a current card (``wait_start_unknown``), the operator preview still printed "no new wait over 30
min on a last open slot" before its UNKNOWN line, which could read as an all-clear. S4 now states that absence only
when no condition is unknown and no new wait was carried; the lane cards already stated none then.

v1.5 (2026-10-16, RCO1's static review of 650e8870 for Lead, answered 06:35:29Z; Lead record 3AFDDC5F): G1 a source
complete through a time after now (even 2099) gave an affirmative NONE through that time; G2 the rows of a kind without
a source gave OBSERVED, although S5 calls a kind without a source UNKNOWN; G3 a row after its own kind's
complete-through time was accepted. N1 (my own, found while changing the S5 states test for G3): a NONE stated its
absence through its source's complete-through time, although only rows at or before observed are checked, so with a
matching row between the two it stated an absence that the document contradicts. None could change an alert. S5 now
makes a document of G1, G2 or G3 unreadable, and a NONE states its absence through observed. A source may still be
complete through a time after the view's as_of (a collector that ran later), never after the turn's clock.

D14 (2026-10-19, fable-5, the REMEMBER lineage, which had v1.1 here): the card lineage's v1.5 (blob 9d459aeb70cd)
with one CLI change and no semantics change (``SEMANTICS`` stays v1.5). v1.5 made the output's JSON text inside the
exclusive open (F-2, DLV2), and the output holds the view's snapshot one level deeper than the view does, so the
deepest view that the strict reader and the delivery accept left an EMPTY output file and a raw RecursionError
(observed on synthetic views only, D14 red2; never on a real view). The whole text is now made, and encoded as UTF-8,
before the output's directory or file exists; an output that cannot be made that way is refused with a fixed message
(``UNWRITABLE``), and nothing is made. An OS error while the file is written can still leave a cut file, as before.

usage: python -B sandbox/mind/join_card_delivery.py --view <lane-view.json> --receipts <receipts.json> --now <UTC>
       [--notified <json list>] [--out <new file inside an existing .codex-audit directory>]
The operator preview goes to stdout, read-only. ``--out`` also writes the full output JSON, only as a new file
inside an existing ``.codex-audit`` directory, the nearest such ancestor of the path being its output root:
there is no default root and no F: fallback, and a path outside one, a root that is not an existing directory
and an existing file are refused before any read; the file is created exclusively, so nothing is ever replaced
(F-2, RCO2 DLV2). Its whole text is made, and encoded as UTF-8, before its directory or file; an output that
cannot be made that way is refused, and nothing is made (D14).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1]
for _path in (str(SRC), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from tools import bridge_mind_workspace as ws  # noqa: E402
import lane_view as lv  # noqa: E402

SCHEMA = "wd.mind-join-card-delivery.v1"
RECEIPTS_SCHEMA = "wd.mind-delivery-receipts.v1"
SEMANTICS = "join-card-delivery-semantics-v1.5"
UNKNOWN = ws.UNKNOWN
THRESHOLD = timedelta(minutes=30)
CARD_MAX_AGE = ws.CONTEXT_MAX_AGE
CARD_BYTES = 2048
KINDS = ("requested", "delivered", "read", "answered")
HEAD = re.compile(r"[0-9a-f]{40}")
REF = re.compile(r"[0-9A-Za-z:_-]{8,128}")
IDENTITY = re.compile(r"[0-9a-f]{64}")
CHECKS = re.compile(r"([1-9][0-9]{0,2})/\1")  # S1 (v1.3): the lane view's f"{count}/{count}" of an action record
ROW_KEYS = frozenset({"kind", "lane", "pr", "head", "ref", "at_utc"})
UNCHANGED_LIMIT = 8
UNKNOWN_LIMIT = 5
AUDIT_DIR = ".codex-audit"  # an output only inside an existing directory of this name (F-2, DLV2)
OUTSIDE = "the output path is not inside a .codex-audit directory"
NOT_A_ROOT = "the output root is not an existing .codex-audit directory"
EXISTING = "the output file already exists; nothing is replaced"
UNWRITABLE = "the output cannot be written as UTF-8 JSON; nothing is written"  # D14: before any directory or file


class DeliveryError(ValueError):
    """Fixed messages that never echo input content."""


def identity(pr: int, head: str, slot: str, lane: str, since: str) -> str:
    return lv.sha256_text(lv.canonical({"kind": "only_empty_slot", "pr": pr, "head": head, "slot": slot,
                                        "lane": lane, "since": since}))


def _time(value: Any) -> datetime | None:
    """A UTC time cut to whole seconds (never rounded up), or None. Every time this module reads comes here (S2)."""
    moment = ws.parse_time(value) if isinstance(value, str) else None
    return None if moment is None else moment.replace(microsecond=0)


def read_receipts(receipts: Any, now: datetime) -> tuple[list[dict[str, Any]] | None, dict[str, datetime] | None]:
    """(rows, complete-through time per kind), or (None, None) when the document is unreadable as a whole.

    It never raises (S5): a value of an unexpected type anywhere makes the document unreadable, so a receipts
    document can never remove an alert. ``now`` is the turn's clock, cut to whole seconds (S2); the document must
    agree with it (S5 v1.5).
    """
    try:
        return _parse_receipts(receipts, now)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None, None


def _parse_receipts(receipts: Any, now: datetime) -> tuple[list[dict[str, Any]] | None, dict[str, datetime] | None]:
    if not isinstance(receipts, Mapping) or set(receipts) != {"schema", "sources", "rows"} \
            or receipts["schema"] != RECEIPTS_SCHEMA:
        return None, None
    try:
        ws.guard_object(receipts)
    except ws.WorkspaceError:
        return None, None
    sources, rows = receipts["sources"], receipts["rows"]
    if not isinstance(sources, Mapping) or not isinstance(rows, list):
        return None, None
    through: dict[str, datetime] = {}
    for kind, source in sources.items():
        if kind not in KINDS or not isinstance(source, Mapping) or set(source) != {"complete_through_utc"}:
            return None, None
        when = _time(source["complete_through_utc"])
        if when is None:
            return None, None
        through[kind] = when
    parsed = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != ROW_KEYS:
            return None, None
        when = _time(row["at_utc"])
        if row["kind"] not in KINDS or row["lane"] not in lv.LANES or type(row["pr"]) is not int \
                or not isinstance(row["head"], str) or not HEAD.fullmatch(row["head"]) \
                or not isinstance(row["ref"], str) or not REF.fullmatch(row["ref"]) or when is None:
            return None, None
        parsed.append(dict(row, at=when))
    if not _consistent(parsed, through, now):
        return None, None
    return parsed, through


def _consistent(rows: list[dict[str, Any]], through: Mapping[str, datetime], now: datetime) -> bool:
    """S5 (v1.5): no source is complete through a time after now, every row's kind has a source, and no row is after
    its kind's complete-through time."""
    return all(when <= now for when in through.values()) \
        and all(row["kind"] in through and row["at"] <= through[row["kind"]] for row in rows)


def receipt_states(rows: list[dict[str, Any]] | None, through: Mapping[str, datetime] | None, lane: str, pr: int,
                   head: str, observed: datetime) -> dict[str, dict[str, str]]:
    """S5: OBSERVED, NONE or UNKNOWN for each kind, for exactly this lane, PR and head."""
    states: dict[str, dict[str, str]] = {}
    for kind in KINDS:
        if rows is None or through is None:
            states[kind] = {"state": UNKNOWN, "reason": "receipts_unreadable"}
            continue
        found = sorted((row["at"], row["ref"]) for row in rows if row["kind"] == kind and row["lane"] == lane
                       and row["pr"] == pr and row["head"] == head and row["at"] <= observed)
        if found:
            states[kind] = {"state": "OBSERVED", "ref": found[0][1], "at_utc": lv.stamp(found[0][0])}
        elif kind not in through:
            states[kind] = {"state": UNKNOWN, "reason": "no_source"}
        elif through[kind] < observed:
            states[kind] = {"state": UNKNOWN, "reason": "source_coverage_short"}
        else:  # S5 (v1.5): the absence is stated through observed, the time it was checked for
            states[kind] = {"state": "NONE", "through_utc": lv.stamp(observed)}
    return states


def _card_trust(view: Mapping[str, Any], observed: datetime, now: datetime) -> str | None:
    """S4: None when the card may be read as current, else the reason every condition is unknown."""
    if view.get("coverage") != "complete":
        return "card_coverage_not_complete"
    if now < observed:
        return "card_from_future"
    if now - observed > CARD_MAX_AGE:
        return "card_stale"
    return None


def view_digest(view: Any) -> str | None:
    """S9 (v1.2): the sha256 of the view's canonical JSON (``lane_view.canonical``), or None when it has none."""
    try:
        return lv.sha256_text(lv.canonical(view))
    except (TypeError, ValueError, RecursionError):
        return None


def _pr_order(key: Any) -> tuple[int, str]:
    """The PR order of a view key: ASCII digits by number, any other key first; it never raises (v1.2)."""
    if isinstance(key, str) and key.isascii() and key.isdigit() and len(key) <= 18:
        return int(key), key
    return -1, str(key)


def _condition(key: Any, record: Any) -> str | None:
    """S1: the one empty slot of a wait condition, or None; a record that is not the lane view's refuses (v1.2)."""
    if not isinstance(record, Mapping):
        raise DeliveryError("a card record is not a lane view v1a record")
    if record.get("state") != "action":
        return None
    slots, credited, pr, head = (record.get(name) for name in ("empty_slots", "credited", "pr", "github_head"))
    if not isinstance(slots, list) or not slots \
            or not all(isinstance(slot, str) and slot in lv.SLOT_OWNER for slot in slots) \
            or not isinstance(credited, list) \
            or not all(isinstance(vote, Mapping) and isinstance(vote.get("slot"), str) and vote["slot"] in lv.SLOT_OWNER
                       and isinstance(vote.get("ref"), str) and REF.fullmatch(vote["ref"]) for vote in credited) \
            or type(pr) is not int or str(pr) != key or not isinstance(head, str) or not HEAD.fullmatch(head) \
            or not isinstance(record.get("branch"), str) or not isinstance(record.get("checks"), str) \
            or not CHECKS.fullmatch(record["checks"]):
        raise DeliveryError("a card record is not a lane view v1a action record")
    return slots[0] if len(slots) == 1 else None


def ledger_rows(view: Mapping[str, Any], receipts: Any, observed: datetime, now: datetime) -> list[dict[str, Any]]:
    """S1-S6: one ledger row per wait condition, in PR order."""
    trust = _card_trust(view, observed, now)
    rows, through = read_receipts(receipts, now)
    ledger = []
    for key in sorted(view["prs"], key=_pr_order):
        record = view["prs"][key]
        slot = _condition(key, record)
        if slot is None:
            continue
        lane, pr, head = lv.SLOT_OWNER[slot], record["pr"], record["github_head"]
        since = _time(record.get("waiting_since_utc"))
        row: dict[str, Any] = {
            "identity": identity(pr, head, slot, lane, lv.stamp(since)),
            "pr": pr, "head": head, "slot": slot, "lane": lane, "branch": record["branch"],
            "checks": record["checks"], "waiting_since_utc": lv.stamp(since), "observed_utc": lv.stamp(observed),
            "credited": [{"slot": vote["slot"], "ref": vote["ref"]} for vote in record["credited"]],
        }
        if trust is not None:
            row.update(state="unknown", reason=trust)
        elif since is None:
            row.update(state="unknown", reason="wait_start_unknown")
        elif since > observed:
            row.update(state="unknown", reason="wait_start_after_observed")
        else:
            wait = observed - since
            row.update(state="alert" if wait > THRESHOLD else "below_threshold",
                       wait_seconds=int(wait.total_seconds()))
        row["receipts"] = receipt_states(rows, through, lane, pr, head, observed)
        ledger.append(row)
    return ledger


def _receipt_text(kind: str, state: Mapping[str, str]) -> str:
    if state["state"] == "OBSERVED":
        return f"{kind}={state['ref'][:16]}@{state['at_utc']}"
    if state["state"] == "NONE":
        return f"{kind}=none_through_{state['through_utc']}"
    return f"{kind}=UNKNOWN({state['reason']})"


def alert_line(row: Mapping[str, Any]) -> str:
    """S8: the one fixed-field line for an alert."""
    credited = ",".join(f"{vote['slot']}:{vote['ref'][:16]}" for vote in row["credited"])
    return (f"WAIT pr={row['pr']} head={row['head']} slot={row['slot']} lane={row['lane']} last_open_slot=yes "
            f"wait_min={row['wait_seconds'] // 60} since={row['waiting_since_utc']} observed={row['observed_utc']} "
            f"coverage=complete ci_at_head={row['checks']} veto=none_observed credited={credited} "
            + " ".join(_receipt_text(kind, row["receipts"][kind]) for kind in KINDS)
            + f" id={row['identity'][:12]}")


def notified_after(prior: frozenset[str], shown: Iterable[str], alerts: Iterable[str]) -> list[str]:
    """S7: the notified set grows by exactly the identities shown in this turn (``alerts`` is deliberately unused)."""
    return sorted(prior | set(shown))


def _size(lines: list[str]) -> int:
    return len(("\n".join(lines) + "\n").encode("utf-8"))


def _card(lane: str, observed: str, coverage: str, trust: str | None, fresh: list[dict[str, Any]],
          repeated: list[dict[str, Any]], unknown: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """One lane card within CARD_BYTES: (text, identities shown, identities carried to the next turn)."""
    head = [f"HEAR {lane} as_of={observed} card_coverage={coverage} card_trust={trust or 'current'}; observe-only, "
            "not a gate; check the canonical tools before any vote"]
    tail = []
    if repeated:
        cited = ",".join(f"pr{row['pr']}:{row['identity'][:12]}" for row in repeated[:UNCHANGED_LIMIT])
        more = f"+{len(repeated) - UNCHANGED_LIMIT}" if len(repeated) > UNCHANGED_LIMIT else ""
        tail.append(f"UNCHANGED waits already shown: {len(repeated)} ({cited}{more})")
    if unknown:
        cited = ";".join(f"pr{row['pr']} head={row['head'][:12]} slot={row['slot']} reason={row['reason']}"
                         for row in unknown[:UNKNOWN_LIMIT])
        more = f";+{len(unknown) - UNKNOWN_LIMIT}" if len(unknown) > UNKNOWN_LIMIT else ""
        tail.append(f"UNKNOWN, not asserted: {len(unknown)} ({cited}{more})")
    shown, carried, body = [], [], []
    for row in fresh:
        line = alert_line(row)
        reserve = [f"CARRIED to next turn: {len(fresh)} waits, first pr{row['pr']}:{row['identity'][:12]}"]
        if carried or _size(head + body + [line] + tail + reserve) > CARD_BYTES:
            carried.append(row["identity"])
            continue
        body.append(line)
        shown.append(row["identity"])
    if carried:
        first = next(row for row in fresh if row["identity"] == carried[0])
        tail.append(f"CARRIED to next turn: {len(carried)} waits, first pr{first['pr']}:{first['identity'][:12]}")
    if not body and not tail:  # S4 (v1.2): a card that is not current states no absence
        body.append("no wait over 30 min on a slot this lane owns" if trust is None
                    else f"no wait is stated for this lane: card_trust={trust}")
    text = "\n".join(head + body + tail) + "\n"
    if len(text.encode("utf-8")) > CARD_BYTES:
        raise DeliveryError("a lane card exceeds its size bound")
    return text, shown, carried


def deliver(view: Any, receipts: Any, *, now_utc: str, notified: Iterable[str] = ()) -> dict[str, Any]:
    """The ledger, operator lines and lane cards of one turn. Pure and deterministic."""
    now = _time(now_utc)
    if now is None:
        raise DeliveryError("an explicit now_utc is required")
    digest = view_digest(view)
    if digest is None or not isinstance(view, Mapping) or view.get("schema") != lv.SCHEMA \
            or not isinstance(view.get("prs"), Mapping):
        raise DeliveryError("the card is not a lane view v1a")
    observed = _time(view.get("as_of_utc"))
    if observed is None:
        raise DeliveryError("the card has no as_of_utc")
    if not isinstance(notified, (list, tuple, set, frozenset)):
        raise DeliveryError("the notified identities are not a list")
    prior = list(notified)
    if not all(isinstance(item, str) and IDENTITY.fullmatch(item) for item in prior):
        raise DeliveryError("a notified identity is not a sha256")
    prior_set = frozenset(prior)
    ledger = ledger_rows(view, receipts, observed, now)
    trust = _card_trust(view, observed, now)
    coverage = "complete" if view.get("coverage") == "complete" else UNKNOWN
    order = sorted(ledger, key=lambda row: (row["waiting_since_utc"], row["pr"]))
    lanes, shown, carried = {}, [], []
    for lane in lv.LANES:
        mine = [row for row in order if row["lane"] == lane]
        fresh = [row for row in mine if row["state"] == "alert" and row["identity"] not in prior_set]
        repeated = [row for row in mine if row["state"] == "alert" and row["identity"] in prior_set]
        unknown = [row for row in mine if row["state"] == "unknown"]
        text, lane_shown, lane_carried = _card(lane, lv.stamp(observed), coverage, trust, fresh, repeated, unknown)
        lanes[lane] = text
        shown += lane_shown
        carried += lane_carried
    by_id = {row["identity"]: row for row in ledger}
    for row in ledger:
        row["shown"] = row["identity"] in shown
        row["previously_notified"] = row["identity"] in prior_set
        row["carried"] = row["identity"] in carried
    operator = [alert_line(by_id[item]) for item in sorted(shown, key=lambda i: (by_id[i]["waiting_since_utc"],
                                                                                  by_id[i]["pr"]))]
    counts = {state: sum(row["state"] == state for row in ledger) for state in ("alert", "below_threshold", "unknown")}
    counts.update(shown=len(shown), carried=len(carried),
                  unchanged=sum(row["state"] == "alert" and row["previously_notified"] for row in ledger))
    out = {
        "schema": SCHEMA, "semantics": SEMANTICS, "authority": "none", "scope": "observe_only_not_gate",
        "observed_utc": lv.stamp(observed), "now_utc": lv.stamp(now), "card_trust": trust or "current",
        "card": {"schema": view.get("schema"), "as_of_utc": view.get("as_of_utc"), "coverage": coverage,
                 "snapshot": view.get("snapshot"), "view_canonical_sha256": digest},
        "receipts_readable": read_receipts(receipts, now)[0] is not None,
        "ledger": ledger, "operator_lines": operator, "lanes": lanes,
        "notified": notified_after(prior_set, shown, [row["identity"] for row in ledger if row["state"] == "alert"]),
        "counts": counts,
    }
    try:
        ws.guard_object(out)
    except ws.WorkspaceError:
        raise DeliveryError("a guarded word is in the output; nothing is returned") from None
    return out


def _preview_absence(out: Mapping[str, Any], unknown: list[Mapping[str, Any]]) -> list[str]:
    """S4: what a preview without an alert line states. A card that is not current states no absence (v1.2); a
    current one states it only when no condition is unknown and no new wait was carried (v1.4)."""
    if out["card_trust"] != "current":
        return [f"no wait is stated: card_trust={out['card_trust']}"]
    if unknown or out["counts"]["carried"]:
        return []
    return ["no new wait over 30 min on a last open slot"]


def operator_preview(out: Mapping[str, Any]) -> str:
    counts = out["counts"]
    lines = [f"JOIN-card delivery preview as_of={out['observed_utc']} now={out['now_utc']} "
             f"card_trust={out['card_trust']}; observe-only, nothing was sent"]
    unknown = [row for row in out["ledger"] if row["state"] == "unknown"]
    lines += out["operator_lines"] or _preview_absence(out, unknown)
    if unknown:
        cited = ";".join(f"pr{row['pr']} head={row['head'][:12]} slot={row['slot']} lane={row['lane']} "
                         f"reason={row['reason']}" for row in unknown[:UNKNOWN_LIMIT])
        more = f";+{len(unknown) - UNKNOWN_LIMIT}" if len(unknown) > UNKNOWN_LIMIT else ""
        lines.append(f"UNKNOWN, not asserted: {len(unknown)} ({cited}{more})")
    lines.append(f"counts: alert={counts['alert']} shown={counts['shown']} unchanged={counts['unchanged']} "
                 f"carried={counts['carried']} below_threshold={counts['below_threshold']} unknown={counts['unknown']}")
    text = "\n".join(lines) + "\n"
    if ws._has_marker(text):
        raise DeliveryError("a guarded word is in the output; nothing is returned")
    return text


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("a duplicate key")
        out[key] = value
    return out


def _no_constant(name: str) -> Any:
    raise ValueError("NaN or Infinity is not JSON")


def _load(path: Path) -> Any:
    """Strict JSON (F2): a duplicate key, NaN or Infinity, a byte order mark, bytes that are not UTF-8 or (v1.2)
    nesting too deep to parse (RecursionError) raise."""
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object, parse_constant=_no_constant)


def output_refusal(out: Path) -> str | None:
    """Why ``out`` is refused as an output path, or None (F-2, RCO2 DLV2; ``join_turn`` uses this same check): the
    nearest ancestor of its resolved path named ``.codex-audit`` is the output root and must be an existing directory.
    There is no default root and no F: fallback; the check reads no file and runs before any read or write."""
    target = Path(out).resolve()
    roots = [parent for parent in target.parents if parent.name == AUDIT_DIR]
    if not roots:
        return OUTSIDE
    if not roots[0].is_dir():
        return NOT_A_ROOT
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--notified", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.out is not None:  # admitted before any read, and an existing file is never replaced (F-2, DLV2)
        refusal = output_refusal(args.out) or (EXISTING if args.out.exists() else None)
        if refusal:
            print(f"refused: {refusal}", file=sys.stderr)
            return 2
    try:
        view, notified = _load(args.view), (_load(args.notified) if args.notified else ())
    except (OSError, ValueError, RecursionError):
        print("refused: the view or notified file is not strict UTF-8 JSON", file=sys.stderr)
        return 2
    try:
        receipts = _load(args.receipts)
    except (OSError, ValueError, RecursionError):
        receipts = None  # S5: an unreadable receipts document describes nothing and never removes an alert
    try:
        out = deliver(view, receipts, now_utc=args.now, notified=notified)
        preview = operator_preview(out)
    except DeliveryError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    if args.out is not None:
        try:  # D14: the whole text exists, and encodes as UTF-8, before the output's directory or file does
            text = json.dumps(out, sort_keys=True, ensure_ascii=False, indent=1) + "\n"
            text.encode("utf-8")
        except (RecursionError, ValueError):  # nested too deeply to write, or text that is not UTF-8
            print(f"refused: {UNWRITABLE}", file=sys.stderr)
            return 2
        args.out.parent.mkdir(parents=True, exist_ok=True)
        try:
            with args.out.open("x", encoding="utf-8") as handle:  # exclusive: a file made meanwhile stays
                handle.write(text)
        except FileExistsError:
            print(f"refused: {EXISTING}", file=sys.stderr)
            return 2
    sys.stdout.write(preview)
    return 0


if __name__ == "__main__":
    sys.exit(main())
