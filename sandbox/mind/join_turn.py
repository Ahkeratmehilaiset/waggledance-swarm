"""JOIN turn v0: one command from a verified frozen capture cycle to one lane's JOIN card. Sandbox, advisory, never a
gate.

The chain reuses each verified stage as it is; nothing is re-implemented here:

1. ``lane_view_frozen.load_frozen(root)``: the manifest-verified cycles (one mismatched byte aborts the load).
2. ``lane_view.lane_view(snapshot, github, bodies, as_of_utc=<the cycle's as_of>)``: the step-1 view, the same call
   ``run_acceptance_1a`` makes.
3. ``join_card_delivery.deliver(view, receipts, now_utc=..., notified=...)``: HEAR. Without a receipts document the
   turn passes one with no sources, so every receipt is UNKNOWN (no_source), never NONE.
4. ``join_card.card(lane, delivery, view, envelope, learn_ref=...)``: the card. A REMEMBER envelope is passed through
   as it is; the card checks its lane, turn and digest (the situation is cited, not verified).

``now`` defaults to the cycle's as_of: a frozen cycle has no later clock, and a frozen cycle is never live input. The
body store is shared by every frozen cycle; ``lane_view.body`` uses a body only for a ref the cycle's snapshot names,
only when the body's own canonical ref is that ref, and only when its ``ts_utc`` is not after the cycle's as_of.
``--out`` (only under the sandbox data root, never over an earlier turn) writes view.json, delivery.json, card.txt and
turn.json: the cycle, lane, as_of and now, the sha256 of the frozen manifest, of every cycle file and of every output,
and the sha256 of every repo file the turn imports (``CODE``; a test keeps it equal to the import closure). Every text
passes the decoded guard before any byte is written, and each file is created exclusively, then read back. Without
``--out`` nothing is written. Exit 0 prints the card; exit 2 prints ``refused: <fixed reason>``.

usage: python -B sandbox/mind/join_turn.py --lane <lane> --cycle <cycle> --learn-ref <ref> [--frozen-root DIR]
       [--receipts FILE] [--now UTC] [--notified FILE] [--remember ENVELOPE] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[1]), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from tools import bridge_mind_workspace as ws  # noqa: E402
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import lane_view as lv  # noqa: E402
import lane_view_frozen as fz  # noqa: E402

VERSION = "join-turn-v0"
TURN_SCHEMA = "wd.mind-join-turn.v0"
SANDBOX_ROOT = Path(r"F:\waggledance_memory\sandbox")
NO_RECEIPTS = {"schema": jcd.RECEIPTS_SCHEMA, "sources": {}, "rows": []}
CASE_KEYS = ("snapshot", "github", "bodies", "as_of_utc")
OUTPUTS = ("view.json", "delivery.json", "card.txt", "turn.json")
REPO = HERE.parents[1]
CODE = ("sandbox/mind/join_card.py", "sandbox/mind/join_card_delivery.py", "sandbox/mind/join_turn.py",
        "sandbox/mind/step1/lane_view.py", "sandbox/mind/step1/lane_view_frozen.py", "tools/__init__.py",
        "tools/bridge_compact_view.py", "tools/bridge_diff_privacy.py", "tools/bridge_event_taxonomy.py",
        "tools/bridge_mind_workspace.py", "tools/wd_bridge_mind.py", "waggledance/__init__.py",
        "waggledance/core/__init__.py", "waggledance/core/bridge_log_reader.py")  # the repo import closure, tested


class JoinTurnError(ValueError):
    """Fixed messages that never echo input content."""


def turn_case(lane: str, case: Mapping[str, Any], *, learn_ref: str, receipts: Any = None, now_utc: str | None = None,
              notified: Any = (), envelope: Any = None) -> dict[str, Any]:
    """One lane's turn over one loaded cycle: its view, delivery and card (the module docstring)."""
    if not isinstance(case, Mapping) or not all(key in case for key in CASE_KEYS):
        raise JoinTurnError("the cycle is not a loaded frozen cycle")
    view = lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc=case["as_of_utc"])
    now = case["as_of_utc"] if now_utc is None else now_utc
    delivery = jcd.deliver(view, NO_RECEIPTS if receipts is None else receipts, now_utc=now, notified=notified)
    text = jc.card(lane, delivery, view, envelope, learn_ref=learn_ref)
    return {"cycle": case.get("cycle"), "lane": lane, "as_of_utc": view["as_of_utc"], "now_utc": delivery["now_utc"],
            "view": view, "delivery": delivery, "card": text, "receipts_given": receipts is not None,
            "envelope": envelope, "inputs": None}


def turn(lane: str, cycle: str, *, root: Path = fz.FROZEN_ROOT, **options: Any) -> dict[str, Any]:
    """``turn_case`` over the one cycle of the verified frozen manifest at ``root`` named ``cycle``."""
    frozen = fz.load_frozen(root)
    matches = [case for case in frozen["cycles"] if case["cycle"] == cycle]
    if len(matches) != 1:
        raise JoinTurnError("the cycle is not exactly once in the frozen manifest")
    result = turn_case(lane, matches[0], **options)
    result["inputs"] = {"frozen_root": str(root), "manifest_sha256": frozen["manifest_sha256"],
                        "cycle_files": dict(matches[0]["files"]), "cycle_nulls": list(matches[0]["nulls"])}
    return result


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def code_sha256() -> dict[str, str]:
    return {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in CODE}


def write_turn(out: Path, result: Mapping[str, Any]) -> dict[str, Any]:
    """The four outputs under ``out`` (module docstring); returns the turn manifest. Nothing is written on a refusal."""
    root, target = SANDBOX_ROOT.resolve(), Path(out).resolve()
    if target == root or root not in target.parents:
        raise JoinTurnError("the output directory is not under the sandbox data root")
    if any((target / name).exists() for name in OUTPUTS):
        raise JoinTurnError("the output directory already holds a turn")
    files = {"view.json": _json_text(result["view"]), "delivery.json": _json_text(result["delivery"]),
             "card.txt": result["card"]}
    envelope = result.get("envelope")
    manifest = {"schema": TURN_SCHEMA, "version": VERSION, "authority": "none", "scope": "sandbox turn, not a gate",
                "cycle": result["cycle"], "lane": result["lane"], "as_of_utc": result["as_of_utc"],
                "now_utc": result["now_utc"], "inputs": result.get("inputs"),
                "receipts_given": result["receipts_given"],
                "remember_envelope_sha256": None if envelope is None else lv.sha256_text(lv.canonical(envelope)),
                "outputs": {name: lv.sha256_text(text) for name, text in files.items()}, "code_sha256": code_sha256()}
    files["turn.json"] = _json_text(manifest)
    if any(ws._has_marker(text) for text in files.values()):
        raise JoinTurnError("a guarded word is in an output; nothing is written")
    target.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        try:
            with (target / name).open("xb") as handle:  # exclusive: an earlier turn is never overwritten
                handle.write(text.encode("utf-8"))
        except FileExistsError:
            raise JoinTurnError("the output directory already holds a turn") from None
    if any(hashlib.sha256((target / name).read_bytes()).hexdigest() != lv.sha256_text(text) for name, text in files.items()):
        raise JoinTurnError("an output file does not hold the bytes written")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", required=True)
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--learn-ref", required=True)
    parser.add_argument("--frozen-root", type=Path, default=fz.FROZEN_ROOT)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--now")
    parser.add_argument("--notified", type=Path)
    parser.add_argument("--remember", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        receipts = jcd._load(args.receipts) if args.receipts else None
        notified = jcd._load(args.notified) if args.notified else ()
        envelope = jcd._load(args.remember) if args.remember else None
        result = turn(args.lane, args.cycle, root=args.frozen_root, learn_ref=args.learn_ref, receipts=receipts,
                      now_utc=args.now, notified=notified, envelope=envelope)
        if args.out:
            write_turn(args.out, result)
    except (JoinTurnError, jc.JoinCardError, jcd.DeliveryError, lv.LaneViewError, fz.FrozenInputError) as error:
        print(f"refused: {error}")
        return 2
    except (ValueError, OSError):
        print("refused: a file could not be read or written")
        return 2
    sys.stdout.write(result["card"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
