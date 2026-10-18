"""JOIN outcome adapter v0.1: outcome rows for the JOIN card a lane actually read. Offline, advisory, never a gate.

Contract: ``JOIN_OUTCOME_ADAPTER.v0.1.md`` at version marker ``v0.1-r3`` (``ADAPTER_CONTRACT_SHA256``, the sha256 of
that file's committed bytes), written by fable-5 and frozen before this code. claude-rco-2's outcome tool
``join_card_outcomes.py`` judges only the step-1 card ``lane_view.render_text`` writes; a JOIN card is refused there.
This ADDITIVE adapter judges the JOIN card itself and then calls RCO2's unchanged helpers (``card_lines``,
``read_time``, ``outcome_row``, ``counts``, ``dedupe``, ``event_pairs``, ``digest_set``) for the rows, so every
outcome rule stays RCO2's. It never edits RCO2's tool, its contract or its result schema.

``judge(turn_dir, lane, *, learn_ref, remember, learn, issue, receipts, events, coverage, head_truth, eligibility,
identity)`` reads one JOIN turn directory as ``join_turn.write_turn`` writes it and returns exactly one of two
results, never silence: accepted (``exposure == "SHOWN_LINES_KNOWN"``) or refused (``card == "refused: <fixed
reason>"``, ``exposure == "UNKNOWN"``). Accepted needs (contract sections 3 and 4): a manifest of exactly the 15
current keys and types, each output's bytes matching its digest, the REMEMBER and LEARN envelopes given exactly when
their digests are non-null and hashing to them, the lane and as_of agreeing, ``card.txt`` byte-equal to
``join_card.card`` re-rendered from the files, and the SEE line matching ``join_card.see_line`` for exactly one k,
each shown item agreeing with RCO2's row of the same line. Only the k shown SEE actions get rows; the other action
rows are listed in ``not_shown`` and the view's cap-elided texts counted in ``elided_by_view_cap``, both outside every
count; ``hidden_prs`` is ``len(records) - k``. An absent artifact is ``turn_artifact_unavailable``: exposure cannot be
established, which is never proof that no card was shown or read.

Hash consistency is not origin: a hand-made turn directory with consistent hashes passes; ``inputs`` and
``code_sha256`` of the manifest are copied into the result, never trusted. No clock, network, process or bridge
write; the CLI writes one new result file under an existing ``.codex-audit`` directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from tools import bridge_mind_workspace as ws  # noqa: E402
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import join_card_outcomes as jco  # noqa: E402  (claude-rco-2's tool, imported unchanged)
import join_turn as jt  # noqa: E402
import lane_view as lv  # noqa: E402

SCHEMA = "wd.mind-join-outcome-adapter.v0.1"
# the sha256 of JOIN_OUTCOME_ADAPTER.v0.1.md as committed at marker v0.1-r3 (filled from git, never typed)
ADAPTER_CONTRACT_SHA256 = "8d621972237192a351b52243fea4744e830b946e08840dd7c03e3207430f7636"
TURN_VERSION = "join-turn-v0.3"
TURN_SCOPE = "sandbox turn, not a gate"
MANIFEST_KEYS = ("schema", "version", "authority", "scope", "cycle", "lane", "as_of_utc", "now_utc", "inputs",
                 "receipts_given", "remember_envelope_sha256", "learn_state_sha256", "learn_envelope_sha256",
                 "outputs", "code_sha256")
INPUT_KEYS = ("frozen_root", "manifest_sha256", "cycle_files", "cycle_nulls")
OUTPUT_FILES = tuple(name for name in jt.OUTPUTS if name != "turn.json")
SEE_LINE = 2  # the card's third line: header, HEAR, SEE
SEE_BUDGETS = 3  # the card's budgets show 3, 2 or 1 SEE items
REASONS = ("turn_artifact_unavailable", "input_unreadable", "turn_manifest_invalid", "turn_output_digest_mismatch",
           "envelope_binding_mismatch", "card_not_rendered_from_turn", "card_lane_or_as_of_mismatch",
           "see_items_ambiguous", "see_item_mismatch", "view_schema_invalid")
HEX64 = re.compile(r"[0-9a-f]{64}")
ROW_KEYS = ("line", "pr", "slot", "head", "task", "text_sha256")
NOT_SHOWN_KEYS = ("line", "pr", "slot", "head", "text_sha256")


class Refused(Exception):
    """One fixed reason of contract section 6; never echoes input content."""

    def __init__(self, reason: str) -> None:
        assert reason in REASONS
        super().__init__(reason)
        self.reason = reason


class _Duplicate(ValueError):
    pass


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    if len({key for key, _ in pairs}) != len(pairs):
        raise _Duplicate("a duplicate key")
    return jcd._strict_object(pairs)


def parse_json(data: bytes, *, duplicate: str = "input_unreadable") -> Any:
    """Strict JSON from exact bytes (join_card_delivery's rules: no duplicate key, NaN, Infinity, byte order mark,
    non-UTF-8 or nesting too deep). A duplicate key refuses with ``duplicate``, anything else ``input_unreadable``."""
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object, parse_constant=jcd._no_constant)
    except _Duplicate:
        raise Refused(duplicate) from None
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise Refused("input_unreadable") from None


def _read(path: Path) -> bytes:
    try:
        if not path.exists():
            raise Refused("turn_artifact_unavailable")
        return path.read_bytes()
    except OSError:
        raise Refused("input_unreadable") from None


def _hex(value: Any) -> bool:
    return isinstance(value, str) and HEX64.fullmatch(value) is not None


def _hex_or_null(value: Any) -> bool:
    return value is None or _hex(value)


def manifest_ok(manifest: Any) -> bool:
    """Contract section 3: exactly the 15 keys of the current writer, each of its type (a bool is never a string)."""
    if not isinstance(manifest, dict) or set(manifest) != set(MANIFEST_KEYS):
        return False
    inputs, outputs, code = manifest["inputs"], manifest["outputs"], manifest["code_sha256"]
    return (manifest["schema"] == jt.TURN_SCHEMA and manifest["version"] == TURN_VERSION
            and manifest["authority"] == "none" and manifest["scope"] == TURN_SCOPE
            and all(isinstance(manifest[key], str) for key in ("cycle", "lane", "as_of_utc", "now_utc"))
            and isinstance(inputs, dict) and set(inputs) == set(INPUT_KEYS)
            and isinstance(inputs["frozen_root"], str) and _hex(inputs["manifest_sha256"])
            and isinstance(inputs["cycle_files"], dict) and isinstance(inputs["cycle_nulls"], list)
            and type(manifest["receipts_given"]) is bool
            and all(_hex_or_null(manifest[key]) for key in ("remember_envelope_sha256", "learn_state_sha256",
                                                            "learn_envelope_sha256"))
            and (manifest["learn_state_sha256"] is None) == (manifest["learn_envelope_sha256"] is None)
            and isinstance(outputs, dict) and set(outputs) == set(OUTPUT_FILES) and all(map(_hex, outputs.values()))
            and isinstance(code, dict) and set(code) == set(jt.CODE) and all(map(_hex, code.values())))


def view_ok(view: Any, lane: str) -> bool:
    """The lane-view schema and the lane's action texts in the form RCO2's ``card_lines`` reads."""
    if not isinstance(view, dict) or view.get("schema") != lv.SCHEMA or not isinstance(view.get("prs"), Mapping):
        return False
    lanes = view.get("lanes")
    if not isinstance(lanes, Mapping):
        return False
    entry = lanes.get(lane, {"actions": [], "more": 0})
    if not (isinstance(entry, Mapping) and isinstance(entry.get("actions"), list)
            and type(entry.get("more")) is int and entry["more"] >= 0):
        return False
    try:  # a view whose texts or records card_lines cannot read is never verified, so no refusal re-enters it
        jco.card_lines(view, lane)
    except (AttributeError, TypeError, ValueError):
        return False
    return True


def envelope_digest(envelope: Any) -> str:
    try:
        return lv.sha256_text(lv.canonical(envelope))
    except (TypeError, ValueError, RecursionError):
        raise Refused("input_unreadable") from None


def _bind_envelope(envelope: Any, expected: str | None) -> None:
    """Contract section 3 and 6: given exactly when the manifest's digest is non-null, and hashing to it."""
    if expected is None:
        if envelope is not None:
            raise Refused("envelope_binding_mismatch")
        return
    if envelope is None:
        raise Refused("turn_artifact_unavailable")
    if envelope_digest(envelope) != expected:
        raise Refused("envelope_binding_mismatch")


def derive_k(view: Mapping[str, Any], lane: str, see: str) -> tuple[str | None, list[Mapping[str, Any]], int]:
    """Contract section 4: k from the card's SEE line, never from the card or the manifest; exactly one budget."""
    slot, records = jc.lane_records(view, lane)
    if not records:
        options = [0] if see == jc.see_line(view, slot, records, 0) else []
    else:
        options = [k for k in range(1, min(SEE_BUDGETS, len(records)) + 1)
                   if see == jc.see_line(view, slot, records, k)]
    if len(options) > 1:
        raise Refused("see_items_ambiguous")
    if not options:
        raise Refused("see_item_mismatch")
    return slot, records, options[0]


def match_items(records: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], slot: str | None,
                k: int) -> None:
    """Every shown item i agrees with RCO2's row of line i on the PR, the full head and the slot, and that row has no
    ``line_record_mismatch``. Both come from the same view, so once the PR agrees the head and slot are implied
    (kept as assertions); what can fail on a hand-made view is the order, a mismatched text and too few texts."""
    if len(candidates) < k:
        raise Refused("see_item_mismatch")
    for index in range(k):
        record, line = records[index], candidates[index]
        if (line["reason"] is not None or line["line"] != index + 1 or line["pr"] != record["pr"]
                or line["head"] != record["github_head"] or line["slot"] != slot):
            raise Refused("see_item_mismatch")


def _sequence(value: Any) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        raise Refused("input_unreadable")
    return value


def _input_digests(issue: Any, receipts: Any, events: Any, coverage: Any, head_truth: Any, eligibility: Any,
                   identity: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """RCO2's input digests (its ``outcomes``) and the deduplicated events; a wrong shape refuses."""
    for value in (receipts, coverage, head_truth, eligibility, identity):
        _sequence(value)
    try:
        kept, event_counts = jco.dedupe(jco.event_pairs(events))
        digests = {"issue_sha256": jco.digest_set([issue]) if issue is not None else None,
                   "receipts_sha256": jco.digest_set(receipts), "coverage_sha256": jco.digest_set(coverage),
                   "head_truth_sha256": jco.digest_set(head_truth),
                   "eligibility_sha256": jco.digest_set(eligibility), "identity_sha256": jco.digest_set(identity),
                   "events": dict(event_counts, refs_sha256=jco.digest_set(sorted(kept)))}
    except (jco.OutcomeError, TypeError, ValueError, RecursionError):
        raise Refused("input_unreadable") from None
    return digests, kept


def judge(turn_dir: Path | str, lane: str, *, learn_ref: str, remember: Any = None, learn: Any = None,
          issue: Any = None, receipts: Any = (), events: Any = None, coverage: Any = (), head_truth: Any = (),
          eligibility: Any = (), identity: Any = ()) -> dict[str, Any]:
    """The adapter result of one JOIN turn for one lane (module docstring): accepted or refused, never None."""
    events = {} if events is None else events
    state: dict[str, Any] = {"view": None, "manifest": None, "files": {}}
    result: dict[str, Any] = {
        "schema": SCHEMA, "authority": "none", "scope": jco.SCOPE, "adapter_contract_sha256": ADAPTER_CONTRACT_SHA256,
        "base_contract_sha256": jco.CONTRACT_SHA256,
        "lane": lane if lane in lv.LANES else None,  # an unknown lane refuses below and is never echoed (section 6)
        "inputs": {"view_sha256": None, "card_sha256": None, "turn_manifest_sha256": None, "issue_sha256": None,
                   "receipts_sha256": None, "coverage_sha256": None, "head_truth_sha256": None,
                   "eligibility_sha256": None, "identity_sha256": None, "events": None, "turn_inputs": None,
                   "turn_code_sha256": None},
        "card": None, "exposure": "UNKNOWN", "shown_k": None, "join_records": None, "hidden_prs": None, "rows": [],
        "rows_unavailable": "view_unverified", "not_shown": [], "elided_by_view_cap": None,
        "receipt_reasons_seen": [], "counts": jco.counts([]), "gap": None}
    try:
        _accept(Path(turn_dir), lane, learn_ref, remember, learn, issue, receipts, events, coverage, head_truth,
                eligibility, identity, state, result)
    except Refused as refusal:
        _refuse(result, refusal.reason, state, lane)
    if _guarded(result):  # copied turn content trips the decoded guard: refuse without showing any of it (section 6)
        result["inputs"].update(turn_inputs=None, turn_code_sha256=None)
        _refuse(result, "input_unreadable", state, lane)
        if _guarded(result):  # a view text in a row: the view cannot be shown, so it counts as unverified
            _refuse(result, "input_unreadable", {"view": None, "manifest": None, "files": {}}, lane)
    ws.guard_object(result)
    return result


def _accept(turn: Path, lane: str, learn_ref: str, remember: Any, learn: Any, issue: Any, receipts: Any, events: Any,
            coverage: Any, head_truth: Any, eligibility: Any, identity: Any, state: dict[str, Any],
            result: dict[str, Any]) -> None:
    if lane not in lv.LANES:
        raise Refused("card_lane_or_as_of_mismatch")
    try:  # a wrong-shaped RCO2 input refuses only after the view is verified, so its lines stay (section 6)
        digests, kept = _input_digests(issue, receipts, events, coverage, head_truth, eligibility, identity)
        result["inputs"].update(digests)
        pending = None
    except Refused as refusal:
        pending, kept = refusal, {}
    raw = _read(turn / "turn.json")
    result["inputs"]["turn_manifest_sha256"] = jco.sha256_bytes(raw)
    manifest = parse_json(raw, duplicate="turn_manifest_invalid")
    if not manifest_ok(manifest):
        raise Refused("turn_manifest_invalid")
    state["manifest"] = manifest
    result["inputs"].update(turn_inputs=manifest["inputs"], turn_code_sha256=manifest["code_sha256"])
    files = state["files"]
    for name in OUTPUT_FILES:
        files[name] = _read(turn / name)
    result["inputs"].update(view_sha256=jco.sha256_bytes(files["view.json"]),
                            card_sha256=jco.sha256_bytes(files["card.txt"]))
    if jco.sha256_bytes(files["view.json"]) != manifest["outputs"]["view.json"]:
        raise Refused("turn_output_digest_mismatch")
    view = parse_json(files["view.json"])
    if not view_ok(view, lane):
        raise Refused("view_schema_invalid")
    state["view"] = view
    if pending is not None:
        raise pending
    if any(jco.sha256_bytes(files[name]) != manifest["outputs"][name] for name in OUTPUT_FILES):
        raise Refused("turn_output_digest_mismatch")
    delivery = parse_json(files["delivery.json"])
    try:
        text = files["card.txt"].decode("utf-8")
    except UnicodeDecodeError:
        raise Refused("card_not_rendered_from_turn") from None
    if (lane != manifest["lane"] or view.get("as_of_utc") != manifest["as_of_utc"]
            or not text.startswith(f"JOIN {lane} as_of={manifest['as_of_utc']} ")):
        raise Refused("card_lane_or_as_of_mismatch")
    _bind_envelope(remember, manifest["remember_envelope_sha256"])
    _bind_envelope(learn, manifest["learn_envelope_sha256"])
    try:
        again = jc.card(lane, delivery, view, remember, learn_ref=learn_ref, learn=learn).encode("utf-8")
    except (jc.JoinCardError, AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError):
        raise Refused("card_not_rendered_from_turn") from None
    if again != files["card.txt"]:
        raise Refused("card_not_rendered_from_turn")
    lines = text.split("\n")
    slot, records, k = derive_k(view, lane, lines[SEE_LINE] if len(lines) > SEE_LINE else "")
    candidates = jco.card_lines(view, lane)
    match_items(records, candidates, slot, k)
    read, reason, receipt_reasons = jco.read_time(issue, receipts, lane, result["inputs"]["card_sha256"],
                                                  result["inputs"]["view_sha256"])
    issued = jco._time(issue.get("issued_utc")) if isinstance(issue, Mapping) else None
    as_of = jco._time(view.get("as_of_utc"))
    lane_events = sorted((event for event in kept.values() if event["body"].get("agent") == lane),
                         key=lambda event: (event["ts"], event["ref"]))
    rows = [jco.outcome_row(line, lane, read, reason, issued, as_of, lane_events, coverage, head_truth, eligibility,
                            identity) for line in candidates[:k]]
    result.update(card="accepted", exposure="SHOWN_LINES_KNOWN", shown_k=k, join_records=len(records),
                  hidden_prs=len(records) - k, rows=rows, rows_unavailable=None,
                  not_shown=[{key: line[key] for key in NOT_SHOWN_KEYS} for line in candidates[k:]],
                  elided_by_view_cap=_elided(view, lane), receipt_reasons_seen=receipt_reasons,
                  counts=jco.counts(rows))
    if rows and all(row["reason"] == "receipt_missing" for row in rows) and issue is None:
        result["gap"] = jco.HISTORICAL_GAP


def _elided(view: Mapping[str, Any], lane: str) -> int:
    return view["lanes"].get(lane, {"actions": [], "more": 0})["more"]


def _verified_view(state: Mapping[str, Any], lane: str) -> Mapping[str, Any] | None:
    """Contract section 6: the view counts as verified when it is present, readable, matches the valid manifest's
    digest and has the lane-view form; only then do a refused card's lines stay, each UNKNOWN."""
    if state["view"] is not None:
        return state["view"]
    manifest, data = state["manifest"], state["files"].get("view.json")
    if manifest is None or data is None or jco.sha256_bytes(data) != manifest["outputs"]["view.json"]:
        return None
    try:
        view = parse_json(data)
    except Refused:
        return None
    return view if view_ok(view, lane) else None


def _refuse(result: dict[str, Any], reason: str, state: Mapping[str, Any], lane: str) -> None:
    view = _verified_view(state, lane) if lane in lv.LANES else None
    rows: list[dict[str, Any]] = []
    if view is not None:
        rows = [dict({key: line[key] for key in ROW_KEYS}, read_utc=jco.UNKNOWN, event_ref=None, event_utc=None,
                     read_to_action_ms=None, outcome=jco.UNKNOWN, reason=reason) for line in jco.card_lines(view, lane)]
    result.update(card=f"refused: {reason}", exposure="UNKNOWN", shown_k=None, join_records=None, hidden_prs=None,
                  rows=rows, rows_unavailable=None if view is not None else "view_unverified", not_shown=[],
                  elided_by_view_cap=_elided(view, lane) if view is not None else None, receipt_reasons_seen=[],
                  counts=jco.counts(rows), gap=None)


def _guarded(value: Any) -> bool:
    try:
        ws.guard_object(value)
    except ws.WorkspaceError:
        return True
    return False


def result_bytes(result: Mapping[str, Any]) -> bytes:
    return (jco.canonical(result) + "\n").encode("ascii")


def _load(path: str | None, default: Any) -> Any:
    return default if path is None else parse_json(_read(Path(path)))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--turn", required=True, help="one JOIN turn directory (join_turn.write_turn)")
    parser.add_argument("--lane", required=True)
    parser.add_argument("--learn-ref", required=True)
    for name in ("remember", "learn", "issue", "receipts", "events", "coverage", "head-truth", "eligibility",
                 "identity"):
        parser.add_argument(f"--{name}")
    parser.add_argument("--out", required=True, help="a new file under an existing .codex-audit directory")
    args = parser.parse_args(argv)
    out = Path(args.out)
    refusal = jcd.output_refusal(out)
    if refusal:
        raise SystemExit(refusal)
    if out.exists():
        raise SystemExit("refusing to overwrite the output")
    try:
        inputs = {"remember": _load(args.remember, None), "learn": _load(args.learn, None),
                  "issue": _load(args.issue, None), "receipts": _load(args.receipts, []),
                  "events": _load(args.events, {}), "coverage": _load(args.coverage, []),
                  "head_truth": _load(args.head_truth, []), "eligibility": _load(args.eligibility, []),
                  "identity": _load(args.identity, [])}
    except Refused as refused:
        print(f"refused: {refused.reason}")
        return 2
    result = judge(args.turn, args.lane, learn_ref=args.learn_ref, **inputs)
    data = result_bytes(result)
    with open(out, "xb") as stream:
        stream.write(data)
    if result["card"] != "accepted":
        print(result["card"])
        return 2
    print(json.dumps({"out": str(out), "sha256": jco.sha256_bytes(data), "shown_k": result["shown_k"],
                      "rows": len(result["rows"]), "outcomes": result["counts"]["outcomes"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
