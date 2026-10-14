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
S2 Wait. ``wait = observed - since``. ``observed`` is the view's ``as_of_utc``; ``since`` is its
   ``waiting_since_utc``, the later of the last credited vote and the CI completion at that head. Both are whole
   seconds, cut and never rounded up. The wait is never extrapolated to ``now``. When since is UNKNOWN or after
   observed, the condition is ``unknown`` (``wait_start_unknown``, ``wait_start_after_observed``).
S3 Threshold. ``alert`` only when wait > 30 min, i.e. strictly more than 1800 s. 29:59 and 30:00 are
   ``below_threshold``; 30:01 alerts.
S4 Card trust. The view must report coverage ``complete`` and be at most 15 min old at ``now`` (the workspace's
   context max age), and ``now`` must not be before it. Otherwise every condition is ``unknown``
   (``card_coverage_not_complete``, ``card_stale``, ``card_from_future``). An unknown condition is never shown as a
   wait. It is listed in one UNKNOWN line with its PR, head and reason, and never notified.
S5 Receipts. For the condition's lane, PR and exact head, each of requested, delivered, read and answered has one
   of three states:
   * OBSERVED: the earliest matching row at or before ``observed``, with its ref and time;
   * NONE: no matching row, and that kind's source is complete through ``observed``;
   * UNKNOWN: there is no source for the kind, its coverage ends before ``observed``, or the receipts document is
     unreadable.
   Rows of another lane, another PR or another head never count. The receipts document is strict: an unexpected
   key, kind, lane, head or time, or a guarded word anywhere in it, makes it unreadable as a whole. Receipts never
   create, remove or delay an alert; they only describe it.
S6 Identity and dedupe. identity = sha256 of the canonical {kind, pr, full head, slot, lane, since}. An alert whose
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
   hit refuses the whole output.

usage: python -B sandbox/mind/join_card_delivery.py --view <lane-view.json> --receipts <receipts.json> --now <UTC>
       [--notified <json list>] [--out <path under F:\\waggledance_memory\\sandbox>]
The operator preview goes to stdout. ``--out`` writes the full output JSON and only under the sandbox data root.
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
SEMANTICS = "join-card-delivery-semantics-v1"
UNKNOWN = ws.UNKNOWN
THRESHOLD = timedelta(minutes=30)
CARD_MAX_AGE = ws.CONTEXT_MAX_AGE
CARD_BYTES = 2048
KINDS = ("requested", "delivered", "read", "answered")
HEAD = re.compile(r"[0-9a-f]{40}")
REF = re.compile(r"[0-9A-Za-z:_-]{8,128}")
IDENTITY = re.compile(r"[0-9a-f]{64}")
ROW_KEYS = frozenset({"kind", "lane", "pr", "head", "ref", "at_utc"})
UNCHANGED_LIMIT = 8
UNKNOWN_LIMIT = 5
SANDBOX_ROOT = Path(r"F:\waggledance_memory\sandbox")


class DeliveryError(ValueError):
    """Fixed messages that never echo input content."""


def identity(pr: int, head: str, slot: str, lane: str, since: str) -> str:
    return lv.sha256_text(lv.canonical({"kind": "only_empty_slot", "pr": pr, "head": head, "slot": slot,
                                        "lane": lane, "since": since}))


def _time(value: Any) -> datetime | None:
    return ws.parse_time(value) if isinstance(value, str) else None


def read_receipts(receipts: Any) -> tuple[list[dict[str, Any]] | None, dict[str, datetime] | None]:
    """(rows, complete-through time per kind), or (None, None) when the document is unreadable as a whole."""
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
        when = _time(source.get("complete_through_utc")) if isinstance(source, Mapping) else None
        if kind not in KINDS or set(source) != {"complete_through_utc"} or when is None:
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
    return parsed, through


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
        else:
            states[kind] = {"state": "NONE", "through_utc": lv.stamp(through[kind])}
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


def ledger_rows(view: Mapping[str, Any], receipts: Any, observed: datetime, now: datetime) -> list[dict[str, Any]]:
    """S1-S6: one ledger row per wait condition, in PR order."""
    trust = _card_trust(view, observed, now)
    rows, through = read_receipts(receipts)
    ledger = []
    for key in sorted(view["prs"], key=lambda text: int(text) if str(text).isdigit() else -1):
        record = view["prs"][key]
        if not isinstance(record, Mapping) or record.get("state") != "action" \
                or len(record.get("empty_slots") or []) != 1:
            continue
        slot = record["empty_slots"][0]
        lane, pr, head = lv.SLOT_OWNER.get(slot), record.get("pr"), record.get("github_head")
        if lane is None or type(pr) is not int or str(pr) != key or not isinstance(head, str) \
                or not HEAD.fullmatch(head):
            raise DeliveryError("a card record is not a lane view v1a action record")
        since_text = record.get("waiting_since_utc")
        since = _time(since_text)
        row: dict[str, Any] = {
            "identity": identity(pr, head, slot, lane, since_text if since is not None else UNKNOWN),
            "pr": pr, "head": head, "slot": slot, "lane": lane, "branch": record.get("branch"),
            "checks": record.get("checks"), "waiting_since_utc": lv.stamp(since), "observed_utc": lv.stamp(observed),
            "credited": [{"slot": vote["slot"], "ref": vote["ref"]} for vote in record.get("credited") or []],
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


def _card(lane: str, observed: str, coverage: str, fresh: list[dict[str, Any]], repeated: list[dict[str, Any]],
          unknown: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """One lane card within CARD_BYTES: (text, identities shown, identities carried to the next turn)."""
    head = [f"HEAR {lane} as_of={observed} card_coverage={coverage}; observe-only, not a gate; check the canonical "
            "tools before any vote"]
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
    if not body and not tail:
        body.append("no wait over 30 min on a slot this lane owns")
    text = "\n".join(head + body + tail) + "\n"
    if len(text.encode("utf-8")) > CARD_BYTES:
        raise DeliveryError("a lane card exceeds its size bound")
    return text, shown, carried


def deliver(view: Any, receipts: Any, *, now_utc: str, notified: Iterable[str] = ()) -> dict[str, Any]:
    """The ledger, operator lines and lane cards of one turn. Pure and deterministic."""
    now = _time(now_utc)
    if now is None:
        raise DeliveryError("an explicit now_utc is required")
    if not isinstance(view, Mapping) or view.get("schema") != lv.SCHEMA or not isinstance(view.get("prs"), Mapping):
        raise DeliveryError("the card is not a lane view v1a")
    observed = _time(view.get("as_of_utc"))
    if observed is None:
        raise DeliveryError("the card has no as_of_utc")
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
        text, lane_shown, lane_carried = _card(lane, lv.stamp(observed), coverage, fresh, repeated, unknown)
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
                 "snapshot": view.get("snapshot")},
        "receipts_readable": read_receipts(receipts)[0] is not None,
        "ledger": ledger, "operator_lines": operator, "lanes": lanes,
        "notified": notified_after(prior_set, shown, [row["identity"] for row in ledger if row["state"] == "alert"]),
        "counts": counts,
    }
    try:
        ws.guard_object(out)
    except ws.WorkspaceError:
        raise DeliveryError("a guarded word is in the output; nothing is returned") from None
    return out


def operator_preview(out: Mapping[str, Any]) -> str:
    counts = out["counts"]
    lines = [f"JOIN-card delivery preview as_of={out['observed_utc']} now={out['now_utc']} "
             f"card_trust={out['card_trust']}; observe-only, nothing was sent"]
    lines += out["operator_lines"] or ["no new wait over 30 min on a last open slot"]
    unknown = [row for row in out["ledger"] if row["state"] == "unknown"]
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


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--view", type=Path, required=True)
    parser.add_argument("--receipts", type=Path, required=True)
    parser.add_argument("--now", required=True)
    parser.add_argument("--notified", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.out is not None:
        resolved = args.out.resolve()
        if SANDBOX_ROOT.resolve() not in resolved.parents:
            print("refused: --out must be under the sandbox data root", file=sys.stderr)
            return 2
    out = deliver(_load(args.view), _load(args.receipts), now_utc=args.now,
                  notified=_load(args.notified) if args.notified else ())
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(out, sort_keys=True, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    sys.stdout.write(operator_preview(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
