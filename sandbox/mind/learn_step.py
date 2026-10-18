"""LEARN step v1: a learned state re-folded from RCO2's card outcome results. Sandbox, advisory, never a gate.

Why. The Mind sees, hears, joins and remembers, but nothing it does changes with what happened after a card was read:
REMEMBER v2 ranks the same way after a wrong vote as after a right one, and a card never says that a similar action
went wrong before. LEARN v1 closes that loop on evidence only, and only in words an agent can check.

Design: one direct Grok xhigh consultation (answer sha256 acc2c4ae..., round C, fable-5's audit folder), mapped by
fable-5 onto RCO2's real result shape ``wd.mind-join-card-outcomes.v1`` (``sandbox/mind/join_card_outcomes.py``,
contract v1.2). The state is a PURE re-fold ``learn(results, picks) -> state``: no clock, no file, no network, the
same inputs in any order give the same canonical bytes and ``state_hash``; rollback is a re-fold of a prefix.

* Evidence. Only rows with a known outcome (TAKEN_CORRECT, TAKEN_STALE, TAKEN_WRONG, OTHER, NONE) are evidence. RCO2's
  tool already turns every failed proof (coverage, verified identity, head interval, read receipt) into UNKNOWN, so
  UNKNOWN and PRE_READ are counted as non-evidence and never move a note. A taken row whose action lies outside
  RCO2's window [read, read + 7200 s] is not a row RCO2 can make, so it is no evidence. One card line seen with two
  different histories is no evidence (RCO2's aggregate calls that a bound, not a resolution); an event cited by
  several cards counts once, and an event whose rows disagree counts never (RCO2's aggregate_conflict).
* Tapes. Per lane and action kind (a build slot is ``build_vote``, a recognized RCO slot ``rco_vote``) the newest
  ``WINDOW_N`` rows by their time (the action's, or the read's for OTHER and NONE), at most ``CAP_PER_LANE_DAY``
  rows per lane per UTC day. Old evidence leaves the window; that is the decay. One lane never moves another lane.
* Notes for a card, at most two per kind, harm first: ``LEARN: similar <kind> wrong <w> of taken <t> (<reasons>;
  rows <h1>, <h2>)`` when known >= MIN_KNOWN, taken >= MIN_TAKEN and wrong >= MIN_WRONG; ``LEARN: similar <kind>
  correct <c> of taken <t> (rows ...)`` when correct >= MIN_CORRECT, with ``; median read-to-action <m> min over
  <n>`` when n >= MIN_TIMED timed correct rows. Every number is a numerator AND a denominator, the rows are cited
  (the 8-hex prefix of their row hash) and a card hash rotates the kind order, so no kind owns the first line.
* Lesson warnings. A picked REMEMBER lesson is associated with the cards it was picked for, only for cards whose
  every row is evidence still in the window (a PRE_READ row neither counts nor voids the card; UNKNOWN voids it). Support >= 5, wrong >= 2 and 2 * wrong >= support moves the lesson to
  the end of the top three with a warning ending ``association only``; LEARN never promotes a lesson.
* Drafts. Two or more wrong rows with one reason in one lane and kind give a CANDIDATE lesson from a fixed template
  (no PR number, head or free text). A draft never enters memory without a review record.

Review after the first push: Lead found that a returned state shared the module's PARAMS (fixed: the state is a
fresh object tree); a controlled-helper Grok review (consultation 2af02293) proposed six changes, two adopted after
checking RCO2's source (the read window, PRE_READ), one not adopted because RCO2's aggregate rules otherwise (one
event judged two ways stays no evidence).

Known limits: LEARN trusts RCO2's proofs (it cannot re-verify an identity or a head); OTHER and NONE still rest on
agent labels (RCO2's stated limit); an association is not a cause; the thresholds are a consultation's, not
calibrated on real outcomes (no sealed or real data was used).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_learn_step.py -q -p no:cacheprovider
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import bridge_mind_workspace as ws  # noqa: E402  (the shared decoded privacy guard)

SCHEMA = "wd.mind-learn-state.v1"
RESULT_SCHEMA = "wd.mind-join-card-outcomes.v1"
WINDOW_N = 200
CAP_PER_LANE_DAY = 32
READ_WINDOW_S = 7200  # RCO2 contract v1.2 WINDOW: a line is decided by an action in [read, read + 7200 s]
MIN_KNOWN, MIN_TAKEN, MIN_WRONG, MIN_CORRECT, MIN_TIMED = 8, 5, 2, 5, 5
LESSON_SUPPORT, LESSON_WRONG, LESSON_TOP = 5, 2, 3
DRAFT_MIN, DRAFTS_PER_LANE = 2, 8
TAKEN = ("TAKEN_CORRECT", "TAKEN_STALE", "TAKEN_WRONG")
KNOWN = TAKEN + ("OTHER", "NONE")
OUTCOMES = KNOWN + ("PRE_READ", "UNKNOWN")
WRONG_REASONS = ("pr_mismatch", "wrong_binding", "not_eligible")
RCO_SLOTS = tuple(ws.RECOGNIZED_RCOS)
BUILD_SLOTS = ("build_lead", "build_tools")
PARAMS = {"window_n": WINDOW_N, "cap_per_lane_day": CAP_PER_LANE_DAY, "read_window_s": READ_WINDOW_S,
          "min_known": MIN_KNOWN, "min_taken": MIN_TAKEN,
          "min_wrong": MIN_WRONG, "min_correct": MIN_CORRECT, "min_timed": MIN_TIMED,
          "lesson_support": LESSON_SUPPORT, "lesson_wrong": LESSON_WRONG, "lesson_top": LESSON_TOP,
          "draft_min": DRAFT_MIN, "drafts_per_lane": DRAFTS_PER_LANE, "rotation": "rot-v1"}
TEMPLATES = {
    "recheck_head_v1": "CANDIDATE, not a lesson until reviewed: before a {kind}, re-read the PR number and the live "
                       "head on the card line and vote only on that pair (seen as {reason}).",
    "recheck_eligibility_v1": "CANDIDATE, not a lesson until reviewed: before a {kind}, check that this lane may "
                              "vote in that slot at that head (seen as {reason}).",
}
CORRECTED = " A later valid vote by the same lane corrected it."
LANE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
HEX64 = re.compile(r"[0-9a-f]{64}")
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,199}")
UTC = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?Z")


class LearnError(ValueError):
    """Fixed messages that never echo input content."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_hash(card_sha256: str, lane: str, row: Mapping[str, Any]) -> str:
    """The identity of one outcome row on one card for one lane; notes cite its first 8 hex digits."""
    return sha256_text(canonical({"card": card_sha256, "lane": lane, "row": row}))


def rotate(order: Sequence[Any], card_sha256: str, lane: str) -> list[Any]:
    """The order shifted by a function of the card and the lane only (never of the state)."""
    order = list(order)
    if not order:
        return []
    shift = int.from_bytes(hashlib.sha256(f"{card_sha256}|{lane}|rot-v1".encode()).digest()[:4], "big") % len(order)
    return order[shift:] + order[:shift]


def _utc(value: Any) -> datetime.datetime | None:
    match = UTC.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    *parts, fraction = match.groups()
    try:
        return datetime.datetime(*map(int, parts), int((fraction or "0")[:6].ljust(6, "0")),
                                 tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def _guarded(value: Any) -> None:
    try:
        ws.guard_object(value)
    except Exception:  # the guard's own error text may name what it found; never echo it
        raise LearnError("a guarded word is in the input or the state; nothing is learned") from None


def _check_result(result: Any) -> None:
    if (not isinstance(result, Mapping) or result.get("schema") != RESULT_SCHEMA or result.get("authority") != "none"
            or not isinstance(result.get("lane"), str) or not LANE.fullmatch(result["lane"])
            or not isinstance(result.get("inputs"), Mapping)
            or not isinstance(result["inputs"].get("card_sha256"), str)
            or not HEX64.fullmatch(result["inputs"]["card_sha256"]) or not isinstance(result.get("rows"), list)):
        raise LearnError("an input is not a card outcome result")
    try:
        canonical(result)  # every value must be plain JSON: no NaN, infinity, set or bytes
    except (TypeError, ValueError):
        raise LearnError("an input holds a value JSON cannot hold") from None
    for row in result["rows"]:
        if (not isinstance(row, Mapping) or not isinstance(row.get("line"), int) or isinstance(row.get("line"), bool)
                or row.get("outcome") not in OUTCOMES):
            raise LearnError("an outcome row is malformed")


def _check_pick(pick: Any) -> None:
    if (not isinstance(pick, Mapping) or not isinstance(pick.get("card_sha256"), str)
            or not HEX64.fullmatch(pick["card_sha256"]) or not isinstance(pick.get("lane"), str)
            or not LANE.fullmatch(pick["lane"])
            or any(not isinstance(pick.get(key), list) for key in ("shown", "picked"))
            or any(not isinstance(ref, str) or not REF.fullmatch(ref) for ref in pick["shown"] + pick["picked"])
            or not set(pick["picked"]) <= set(pick["shown"])):
        raise LearnError("a pick record is malformed or picks a line it did not show")
    try:
        canonical(pick)
    except (TypeError, ValueError):
        raise LearnError("a pick record holds a value JSON cannot hold") from None


def action_kind(slot: Any) -> str | None:
    if slot in RCO_SLOTS:
        return "rco_vote"
    if slot in BUILD_SLOTS:
        return "build_vote"
    return None


def _entry(card: str, lane: str, row: Mapping[str, Any]) -> dict[str, Any] | None:
    """One evidence row as a tape entry, or None when a field the outcome needs is missing."""
    kind, outcome = action_kind(row.get("slot")), row["outcome"]
    taken = outcome in TAKEN
    read = _utc(row.get("read_utc"))
    when = _utc(row.get("event_utc")) if taken else read
    event = row.get("event_ref")
    if kind is None or when is None or (taken and (not isinstance(event, str) or not REF.fullmatch(event))):
        return None
    if taken and (read is None or not 0 <= (when - read).total_seconds() <= READ_WINDOW_S):
        return None  # RCO2 decides a line only by an action in [read, read + window]; anything else is not its row
    ms = row.get("read_to_action_ms")
    reason = row.get("reason") if outcome == "TAKEN_WRONG" else None
    return {"row_hash": row_hash(card, lane, row), "kind": kind, "outcome": outcome,
            "reason": (reason if reason in WRONG_REASONS else "other") if outcome == "TAKEN_WRONG" else None,
            "utc": when.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "event_ref": event if taken else None,
            "ms": ms if outcome == "TAKEN_CORRECT" and isinstance(ms, int) and not isinstance(ms, bool) and ms >= 0
            else None, "corrected": row.get("corrected_after_wrong") is True, "card": card}


def learn(results: Sequence[Any], picks: Sequence[Any] = ()) -> dict[str, Any]:
    """The learned state of these outcome results and REMEMBER pick records. Pure and order-free."""
    for result in results:
        _check_result(result)
    for pick in picks:
        _check_pick(pick)
    _guarded([list(results), list(picks)])
    rejected = Counter({key: 0 for key in ("line_conflict", "duplicate_event", "event_conflict", "cap", "malformed",
                                           "pick_mismatch", "pick_without_card")})
    lanes: dict[str, dict[str, Any]] = {}

    def lane_state(lane: str) -> dict[str, Any]:
        return lanes.setdefault(lane, {"non_evidence": {"unknown": 0, "pre_read": 0}, "tapes": {},
                                       "dropped_by_window": 0, "lessons": {}})

    lines: dict[tuple, set] = defaultdict(set)
    for result in results:
        for row in result["rows"]:
            lines[result["inputs"]["card_sha256"], result["lane"], row["line"]].add(canonical(row))
    card_rows: dict[tuple, list] = defaultdict(list)
    entries: list[dict[str, Any]] = []
    for (card, lane, _line), texts in sorted(lines.items()):
        lane_state(lane)
        if len(texts) > 1:
            rejected["line_conflict"] += 1
            card_rows[card, lane].append(None)
            continue
        row = json.loads(next(iter(texts)))
        if row["outcome"] == "PRE_READ":  # known and outside every denominator: it neither counts nor voids the card
            lanes[lane]["non_evidence"]["pre_read"] += 1
            card_rows.setdefault((card, lane), [])  # the card was seen
            continue
        if row["outcome"] == "UNKNOWN":
            lanes[lane]["non_evidence"]["unknown"] += 1
            card_rows[card, lane].append(None)
            continue
        entry = _entry(card, lane, row)
        if entry is None:
            rejected["malformed"] += 1
            card_rows[card, lane].append(None)
            continue
        entry["lane"] = lane
        entries.append(entry)
        card_rows[card, lane].append(entry)

    by_event: dict[tuple, list] = defaultdict(list)
    for entry in entries:
        if entry["event_ref"] is not None:
            by_event[entry["lane"], entry["event_ref"]].append(entry)
    dropped: set[str] = set()
    for group in by_event.values():
        if len(group) < 2:
            continue
        if len({(e["kind"], e["outcome"], e["reason"]) for e in group}) > 1:
            rejected["event_conflict"] += len(group)
            dropped.update(e["row_hash"] for e in group)
            continue
        group.sort(key=lambda e: (e["utc"], e["row_hash"]))
        rejected["duplicate_event"] += len(group) - 1
        dropped.update(e["row_hash"] for e in group[1:])
    alias = {e["row_hash"]: group[0]["row_hash"] for group in by_event.values() for e in group
             if e["row_hash"] in dropped and len({(x["kind"], x["outcome"], x["reason"]) for x in group}) == 1}
    entries = [e for e in entries if e["row_hash"] not in dropped]

    per_day: dict[tuple, list] = defaultdict(list)
    for entry in sorted(entries, key=lambda e: (e["utc"], e["row_hash"])):
        per_day[entry["lane"], entry["utc"][:10]].append(entry)
    capped = []
    for day_entries in per_day.values():
        rejected["cap"] += max(0, len(day_entries) - CAP_PER_LANE_DAY)
        capped.extend(day_entries[:CAP_PER_LANE_DAY])
    tapes: dict[tuple, list] = defaultdict(list)
    for entry in sorted(capped, key=lambda e: (e["utc"], e["row_hash"])):
        tapes[entry["lane"], entry["kind"]].append(entry)
    kept: set[str] = set()
    for (lane, kind), tape in sorted(tapes.items()):
        lanes[lane]["dropped_by_window"] += max(0, len(tape) - WINDOW_N)
        tape = tape[-WINDOW_N:]
        kept.update(e["row_hash"] for e in tape)
        lanes[lane]["tapes"][kind] = [{key: e[key] for key in ("row_hash", "outcome", "reason", "utc", "ms",
                                                               "corrected")} for e in tape]

    records: dict[tuple, set] = defaultdict(set)
    for pick in picks:
        records[pick["card_sha256"], pick["lane"]].add(canonical({"shown": pick["shown"], "picked": pick["picked"]}))
    lessons: dict[tuple, dict] = defaultdict(lambda: {"support": 0, "wrong": 0, "wrong_rows": []})
    for (card, lane), texts in sorted(records.items()):
        if len(texts) > 1:
            rejected["pick_mismatch"] += 1
            continue
        if (card, lane) not in card_rows:
            rejected["pick_without_card"] += 1
            continue
        rows = card_rows[card, lane]
        if any(e is None or alias.get(e["row_hash"], e["row_hash"]) not in kept for e in rows):
            continue  # a card with a non-evidence row or a row outside the window says nothing about a pick
        taken = [e for e in rows if e["outcome"] in TAKEN]
        if not taken:
            continue
        wrong = sorted((e for e in taken if e["outcome"] == "TAKEN_WRONG"), key=lambda e: (e["utc"], e["row_hash"]))
        for ref in sorted(set(json.loads(next(iter(texts)))["picked"])):
            stats = lessons[lane, ref]
            stats["support"] += 1
            if wrong:
                stats["wrong"] += 1
                stats["wrong_rows"].append([wrong[-1]["utc"], wrong[-1]["row_hash"]])
    for (lane, ref), stats in sorted(lessons.items()):
        lane_state(lane)["lessons"][ref] = {"support": stats["support"], "wrong": stats["wrong"],
                                            "cite": [h for _, h in sorted(stats["wrong_rows"], reverse=True)[:2]]}

    drafts = []
    for lane in sorted(lanes):
        found = []
        for kind, tape in sorted(lanes[lane]["tapes"].items()):
            groups: dict[str, list] = defaultdict(list)
            for entry in tape:
                if entry["outcome"] == "TAKEN_WRONG":
                    groups[entry["reason"]].append(entry)
            for reason, group in sorted(groups.items()):
                if len(group) < DRAFT_MIN:
                    continue
                base = "recheck_eligibility" if reason == "not_eligible" else "recheck_head"
                corrected = any(entry["corrected"] for entry in group)
                template = f"{base}_corrected_v1" if corrected else f"{base}_v1"
                text = TEMPLATES[f"{base}_v1"].format(kind=kind, reason=reason) + (CORRECTED if corrected else "")
                found.append({"lane": lane, "kind": kind, "reason": reason, "template_id": template,
                              "status": "CANDIDATE", "support_count": len(group),
                              "row_hashes": sorted(entry["row_hash"] for entry in group), "text": text,
                              "enters_memory": False, "needs": "a review record before any memory write"})
        found.sort(key=lambda draft: (-draft["support_count"], draft["kind"], draft["reason"]))
        drafts.extend(found[:DRAFTS_PER_LANE])

    state = {"schema": SCHEMA, "authority": "none", "scope": "offline_learned_state_not_gate", "params": PARAMS,
             "params_sha256": sha256_text(canonical(PARAMS)),
             "inputs": {"results_sha256": sha256_text(canonical(sorted({sha256_text(canonical(r)) for r in results}))),
                        "picks_sha256": sha256_text(canonical(sorted({sha256_text(canonical(p)) for p in picks})))},
             "lanes": lanes, "drafts": drafts, "rejected": dict(sorted(rejected.items()))}
    state = json.loads(canonical(state))  # a fresh object tree: nothing is shared with the module or another state
    state["state_hash"] = sha256_text(canonical(state))
    _guarded(state)
    return state


def verify(state: Any) -> bool:
    """True only when the state's hash matches its own canonical content."""
    if not isinstance(state, Mapping) or state.get("schema") != SCHEMA or not isinstance(state.get("state_hash"), str):
        return False
    body = {key: value for key, value in state.items() if key != "state_hash"}
    try:
        return sha256_text(canonical(body)) == state["state_hash"]
    except (TypeError, ValueError):
        return False


def summarize(state: Mapping[str, Any], lane: str, kind: str) -> dict[str, Any]:
    """Counts of one lane's tape for one action kind; only ``cite`` depends on the card hashes."""
    tape = state.get("lanes", {}).get(lane, {}).get("tapes", {}).get(kind, [])
    outcomes = Counter(entry["outcome"] for entry in tape)
    reasons = Counter(entry["reason"] for entry in tape if entry["outcome"] == "TAKEN_WRONG")
    timed = sorted(entry["ms"] for entry in tape if entry["outcome"] == "TAKEN_CORRECT" and entry["ms"] is not None)

    def newest(outcome):
        rows = sorted(((e["utc"], e["row_hash"]) for e in tape if e["outcome"] == outcome), reverse=True)
        return [row[1] for row in rows[:2]]
    return {"known": len(tape), "taken": sum(outcomes[o] for o in TAKEN), "correct": outcomes["TAKEN_CORRECT"],
            "stale": outcomes["TAKEN_STALE"], "wrong": outcomes["TAKEN_WRONG"], "other": outcomes["OTHER"],
            "none": outcomes["NONE"], "reasons": {r: reasons[r] for r in WRONG_REASONS + ("other",) if reasons[r]},
            "timed": len(timed), "median_ms": timed[(len(timed) - 1) // 2] if timed else None,
            "cite": {"wrong": newest("TAKEN_WRONG"), "correct": newest("TAKEN_CORRECT")}}


def _cite(hashes: Sequence[str]) -> str:
    return ", ".join(h[:8] for h in hashes)


def notes(state: Mapping[str, Any], lane: str, card: str | None = None) -> list[str]:
    """The LEARN lines for one lane's card: harm before praise in each kind; a card hash rotates the kind order.
    A state that fails its own hash gives no line."""
    if not verify(state):
        return []
    kinds = sorted(state["lanes"].get(lane, {}).get("tapes", {}))
    if card is not None:
        kinds = rotate(kinds, card, lane)
    out = []
    for kind in kinds:
        s = summarize(state, lane, kind)
        if s["known"] < MIN_KNOWN or s["taken"] < MIN_TAKEN:
            continue
        if s["wrong"] >= MIN_WRONG:
            reasons = ", ".join(f"{count} {reason}" for reason, count in s["reasons"].items())
            out.append(f"LEARN: similar {kind} wrong {s['wrong']} of taken {s['taken']} ({reasons}; rows "
                       f"{_cite(s['cite']['wrong'])})")
        if s["correct"] >= MIN_CORRECT:
            line = f"LEARN: similar {kind} correct {s['correct']} of taken {s['taken']} (rows {_cite(s['cite']['correct'])})"
            if s["timed"] >= MIN_TIMED:
                line += f"; median read-to-action {s['median_ms'] // 60000} min over {s['timed']}"
            out.append(line)
    return out


def lesson_order(state: Mapping[str, Any], lane: str, shown: Sequence[str]) -> tuple[list[str], dict[str, str]]:
    """REMEMBER's shown lessons with each warned lesson moved to the end of the top three; never a promotion."""
    shown = list(shown)
    if not verify(state):
        return shown, {}
    lessons = state["lanes"].get(lane, {}).get("lessons", {})
    warnings = {}
    for ref in shown[:LESSON_TOP]:
        stats = lessons.get(ref)
        if stats and stats["support"] >= LESSON_SUPPORT and stats["wrong"] >= LESSON_WRONG \
                and 2 * stats["wrong"] >= stats["support"]:
            warnings[ref] = (f"LEARN: after you picked this, the card was wrong {stats['wrong']} of support "
                             f"{stats['support']} (rows {_cite(stats['cite'])}); association only")
    top = shown[:LESSON_TOP]
    order = [ref for ref in top if ref not in warnings] + [ref for ref in top if ref in warnings] + shown[LESSON_TOP:]
    return order, warnings


# --- runner: fold result files into one state file (never overwritten), then read notes or a lesson order from it ----

def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise LearnError("an input file is unreadable") from None


def _state(path: Path) -> Mapping[str, Any]:
    state = _load(path)
    if not verify(state):
        raise LearnError("the state file fails its own hash")
    return state


def cmd_fold(args) -> int:
    picks = _load(args.picks).get("picks") if args.picks else []
    if not isinstance(picks, list):
        raise LearnError("the picks file has no picks list")
    state = learn([_load(path) for path in args.results], picks)
    with args.out.open("x", encoding="ascii", newline="") as handle:
        handle.write(canonical(state) + "\n")
    print(canonical({"state_hash": state["state_hash"], "lanes": sorted(state["lanes"]), "drafts": len(state["drafts"]),
                     "rejected": state["rejected"]}))
    return 0


def cmd_notes(args) -> int:
    for line in notes(_state(args.state), args.lane, args.card):
        print(line)
    return 0


def cmd_order(args) -> int:
    order, warnings = lesson_order(_state(args.state), args.lane, args.shown)
    print(canonical({"order": order, "warnings": warnings}))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LEARN step v1: a learned state from card outcome results")
    sub = parser.add_subparsers(dest="command", required=True)
    fold = sub.add_parser("fold")
    fold.add_argument("--results", type=Path, nargs="+", required=True)
    fold.add_argument("--picks", type=Path)
    fold.add_argument("--out", type=Path, required=True)
    note = sub.add_parser("notes")
    note.add_argument("--state", type=Path, required=True)
    note.add_argument("--lane", required=True)
    note.add_argument("--card")
    order = sub.add_parser("order")
    order.add_argument("--state", type=Path, required=True)
    order.add_argument("--lane", required=True)
    order.add_argument("--shown", nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        return {"fold": cmd_fold, "notes": cmd_notes, "order": cmd_order}[args.command](args)
    except (LearnError, FileExistsError) as error:
        print(f"REFUSED: {error if isinstance(error, LearnError) else 'the state file already exists'}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
