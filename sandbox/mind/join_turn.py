"""JOIN turn v0.3: one command from a verified frozen capture cycle to one lane's JOIN card. Sandbox, advisory, never a
gate.

The chain reuses each verified stage as it is; nothing is re-implemented here:

1. ``lane_view_frozen.load_frozen(root)``: the manifest-verified cycles (one mismatched byte aborts the load).
2. ``lane_view.lane_view(snapshot, github, bodies, as_of_utc=<the cycle's as_of>)``: the step-1 view, the same call
   ``run_acceptance_1a`` makes.
3. ``join_card_delivery.deliver(view, receipts, now_utc=..., notified=...)``: HEAR. Without a receipts document the
   turn passes one with no sources, so every receipt is UNKNOWN (no_source), never NONE.
4. ``join_card.card(lane, delivery, view, envelope, learn_ref=..., learn=...)``: the card. A REMEMBER envelope is
   passed through as it is; the card checks its lane, turn and digest (the situation is cited, not verified).

(v0.3) With a LEARN v1 state (``learn_step.learn``'s output, ``--learn-state``) the turn makes the card's LEARN envelope
(``learn_envelope``): the state must match its own hash (``learn_step.verify``) and have been folded with this
``learn_step``'s parameters (``PARAMS`` and its digest), else the turn is refused; the REMEMBER envelope, when given, is
checked first with the card's own check; then ``learn_step.notes(state, lane, <the view's canonical digest>)`` and the
warnings of ``learn_step.lesson_order`` for the first three REMEMBER picks go into ``join_card.learn_envelope``. A state
whose hash matches but whose content LEARN cannot read is refused with a fixed reason, never a raw error. turn.json
records the state's ``state_hash`` and the envelope's sha256 (both null without a state).

``now`` defaults to the cycle's as_of: a frozen cycle has no later clock, and a frozen cycle is never live input. The
body store is shared by every frozen cycle; ``lane_view.body`` uses a body only for a ref the cycle's snapshot names,
only when the body's own canonical ref is that ref, and only when its ``ts_utc`` is not after the cycle's as_of.
``--out DIR`` writes view.json, delivery.json, card.txt and turn.json: the cycle, lane, as_of and now, the sha256 of the
frozen manifest, of every cycle file and of every output, and the sha256 of every repo file the turn imports (``CODE``;
a test keeps it equal to the import closure). DIR must be a new directory inside an existing ``.codex-audit``
directory, the nearest such ancestor being the output root: there is no default root and no F: fallback, and
a path outside one or a root that is not an existing directory is refused before any read or write (F-2, the
check of ``join_card_delivery.output_refusal``). An existing DIR is refused, so an earlier turn is never
overwritten. Every text passes the decoded guard before any byte is written.
v0.1 writes the files into a fresh sibling directory ``.<DIR name>.staging-<16 hex>``: each is created exclusively,
flushed to disk and read back, and one rename then moves the whole turn into place. So DIR either holds the complete
turn or does not exist. (v0.2) Just before the rename the staging directory must hold exactly the four files, so a
file that another writer put there is refused, never carried into the turn (RCO1's fault probe v2, P15); only one
that appears between that listing and the rename would still be carried. On Windows the rename never replaces
anything at DIR (measured on NTFS: an existing DIR refuses it); POSIX rename(2) would replace a directory that another
writer created empty during the call, and the sandbox runs on Windows. A rename that an open file in the staging
directory blocks, such as a scanner's, is retried briefly. A refusal or an error removes the files the turn wrote and
the staging directory (best effort), and the caller gets that refusal or error, never a cleanup error: (v0.2) a
cleanup step that fails with any ordinary exception, not only an OSError, never stops the next one (RCO1's P14). A
crash can leave the staging directory behind, and it is never a turn. Missing parents of DIR are created first and
stay, also when the turn is then refused. The ordinary error paths (each write step of each file, the readback, the
rename count and a failing cleanup) are tested by injection, after the Tools static audit of v0.1 (2026-10-16). This
is a tested design property, not a crash-safety claim: no power-loss test was run and no directory is flushed, so
a successful return does not make the rename durable (after a power loss DIR may be absent and the staging
directory complete), and an actor that finds the random staging name could swap a file in it (Grok f36aaa4b; like
a junction race, out of scope). Without ``--out`` nothing is written. ``--frozen-root`` has no default: without
it the command exits 2 with its usage before any read. Exit 0 prints the card; exit 2 prints
``refused: <fixed reason>``; a file nested too deeply to read, or to write as an output, is refused like an
unreadable one, before any byte is written.

usage: python -B sandbox/mind/join_turn.py --lane <lane> --cycle <cycle> --learn-ref <ref> --frozen-root DIR
       [--receipts FILE] [--now UTC] [--notified FILE] [--remember ENVELOPE] [--learn-state FILE] [--out DIR]
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[1]), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from tools import bridge_mind_workspace as ws  # noqa: E402
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import lane_view as lv  # noqa: E402
import lane_view_frozen as fz  # noqa: E402
import learn_step as ls  # noqa: E402

VERSION = "join-turn-v0.3"
TURN_SCHEMA = "wd.mind-join-turn.v0"
NO_RECEIPTS = {"schema": jcd.RECEIPTS_SCHEMA, "sources": {}, "rows": []}
CASE_KEYS = ("snapshot", "github", "bodies", "as_of_utc")
OUTPUTS = ("view.json", "delivery.json", "card.txt", "turn.json")
REPO = HERE.parents[1]
CODE = ("sandbox/mind/join_card.py", "sandbox/mind/join_card_delivery.py", "sandbox/mind/join_turn.py",
        "sandbox/mind/learn_step.py", "sandbox/mind/step1/lane_view.py", "sandbox/mind/step1/lane_view_frozen.py", "tools/__init__.py",
        "tools/bridge_compact_view.py", "tools/bridge_diff_privacy.py", "tools/bridge_mind_workspace.py",
        "tools/wd_bridge_mind.py", "waggledance/__init__.py",
        "waggledance/core/__init__.py", "waggledance/core/bridge_log_reader.py")  # the repo import closure, tested
EXISTS = "the output directory already exists; a turn is only written into a new directory"
FOREIGN = "the staging directory holds a file the turn did not write; nothing is written there"
RENAME_TRIES = 6  # waits 0.05, 0.1, 0.2, 0.4 and 0.8 s between the tries


class JoinTurnError(ValueError):
    """Fixed messages that never echo input content."""


def turn_case(lane: str, case: Mapping[str, Any], *, learn_ref: str, receipts: Any = None, now_utc: str | None = None,
              notified: Any = (), envelope: Any = None, learn_state: Any = None) -> dict[str, Any]:
    """One lane's turn over one loaded cycle: its view, delivery and card (the module docstring)."""
    if not isinstance(case, Mapping) or not all(key in case for key in CASE_KEYS):
        raise JoinTurnError("the cycle is not a loaded frozen cycle")
    view = lv.lane_view(case["snapshot"], case["github"], case["bodies"], as_of_utc=case["as_of_utc"])
    now = case["as_of_utc"] if now_utc is None else now_utc
    delivery = jcd.deliver(view, NO_RECEIPTS if receipts is None else receipts, now_utc=now, notified=notified)
    learn = None if learn_state is None else learn_envelope(lane, view, envelope, learn_state)
    text = jc.card(lane, delivery, view, envelope, learn_ref=learn_ref, learn=learn)
    return {"cycle": case.get("cycle"), "lane": lane, "as_of_utc": view["as_of_utc"], "now_utc": delivery["now_utc"],
            "view": view, "delivery": delivery, "card": text, "receipts_given": receipts is not None,
            "envelope": envelope, "learn": learn, "learn_state_sha256": None if learn is None else learn["state_sha256"],
            "inputs": None}


def learn_envelope(lane: str, view: Mapping[str, Any], envelope: Any, state: Any) -> dict[str, Any]:
    """v0.3: the card's LEARN envelope of one LEARN v1 state for this lane and view (module docstring)."""
    try:
        verified = ls.verify(state)
    except RecursionError:  # too deep to have a canonical hash
        verified = False
    if not verified:
        raise JoinTurnError("the LEARN state does not match its own hash")
    if state.get("params") != ls.PARAMS or state.get("params_sha256") != ls.sha256_text(ls.canonical(ls.PARAMS)):
        raise JoinTurnError("the LEARN state was folded with other parameters")
    if envelope is not None:
        jc._check_envelope(envelope, lane, view["as_of_utc"])  # the card's own refusal, before LEARN reads its refs
    seed = jcd.view_digest(view)
    try:
        notes = ls.notes(state, lane, seed)
        warnings = ls.lesson_order(state, lane, jc._warnable_refs(envelope))[1]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, RecursionError):
        raise JoinTurnError("the LEARN state is not a LEARN v1 state") from None
    return jc.learn_envelope(lane, view["as_of_utc"], seed, notes, warnings, state_sha256=state["state_hash"],
                             params_sha256=state["params_sha256"])


def turn(lane: str, cycle: str, *, root: Path, **options: Any) -> dict[str, Any]:
    """``turn_case`` over the one cycle of the verified frozen manifest at ``root`` (no default root, F-2) named
    ``cycle``."""
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
    """The four outputs in the new directory ``out`` (module docstring); returns the turn manifest. On a refusal or an
    error ``out`` is not created and the caller gets that refusal or error; the files this turn wrote and its staging
    directory are removed, best effort (``_discard``). Missing parents of ``out`` are created first and stay."""
    target = Path(out).resolve()
    refusal = jcd.output_refusal(target)
    if refusal:
        raise JoinTurnError(refusal)
    if any((target / name).exists() for name in OUTPUTS):
        raise JoinTurnError("the output directory already holds a turn")
    if target.exists():
        raise JoinTurnError(EXISTS)
    files = {"view.json": _json_text(result["view"]), "delivery.json": _json_text(result["delivery"]),
             "card.txt": result["card"]}
    envelope = result.get("envelope")
    manifest = {"schema": TURN_SCHEMA, "version": VERSION, "authority": "none", "scope": "sandbox turn, not a gate",
                "cycle": result["cycle"], "lane": result["lane"], "as_of_utc": result["as_of_utc"],
                "now_utc": result["now_utc"], "inputs": result.get("inputs"),
                "receipts_given": result["receipts_given"],
                "remember_envelope_sha256": None if envelope is None else lv.sha256_text(lv.canonical(envelope)),
                "learn_state_sha256": result.get("learn_state_sha256"),
                "learn_envelope_sha256": None if result.get("learn") is None
                else lv.sha256_text(lv.canonical(result["learn"])),
                "outputs": {name: lv.sha256_text(text) for name, text in files.items()}, "code_sha256": code_sha256()}
    files["turn.json"] = _json_text(manifest)
    if any(ws._has_marker(text) for text in files.values()):
        raise JoinTurnError("a guarded word is in an output; nothing is written")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.staging-{secrets.token_hex(8)}"
    staging.mkdir()  # exclusive: a fresh directory of this turn's own
    written: list[str] = []
    try:
        for name, text in files.items():
            with (staging / name).open("xb") as handle:
                written.append(name)
                handle.write(text.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
        if any(hashlib.sha256((staging / name).read_bytes()).hexdigest() != lv.sha256_text(text)
               for name, text in files.items()):
            raise JoinTurnError("an output file does not hold the bytes written")
        if sorted(os.listdir(staging)) != sorted(files):  # v0.2: never carry another writer's file into the turn
            raise JoinTurnError(FOREIGN)
        _move_into_place(staging, target)
    except BaseException:
        _discard(staging, written)
        raise
    return manifest


def _move_into_place(staging: Path, target: Path) -> None:
    """One rename moves the whole turn to ``target``. It never replaces anything there: Windows refuses an existing
    target (FileExistsError, measured on NTFS), and POSIX replaces only an empty directory. An open file in the staging
    directory blocks the rename on Windows (PermissionError, measured), so that alone is retried, ``RENAME_TRIES`` times
    in all."""
    for attempt in range(RENAME_TRIES):
        try:
            os.rename(staging, target)
            return
        except FileExistsError:
            raise JoinTurnError(EXISTS) from None
        except OSError as error:
            if error.errno in (errno.ENOTEMPTY, errno.ENOTDIR):  # POSIX: something is already at the target
                raise JoinTurnError(EXISTS) from None
            if not isinstance(error, PermissionError) or attempt == RENAME_TRIES - 1:
                raise JoinTurnError("the turn could not be moved into its output directory; nothing is written "
                                    "there") from None
            time.sleep(0.05 * 2 ** attempt)


def _discard(staging: Path, written: Sequence[str]) -> None:
    """Best effort after a refusal or an error: the files this turn created in its staging directory, then the
    directory. Nothing the turn did not create is removed, so the directory stays if anything else is in it. A step
    that fails with any ordinary exception never stops the next one, and the caller's error stands (v0.2)."""
    for name in written:
        try:
            (staging / name).unlink()
        except Exception:  # noqa: BLE001 - best effort: the caller must see its own error or refusal
            pass
    try:
        staging.rmdir()
    except Exception:  # noqa: BLE001 - as above
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lane", required=True)
    parser.add_argument("--cycle", required=True)
    parser.add_argument("--learn-ref", required=True)
    parser.add_argument("--frozen-root", type=Path, required=True)  # no default frozen root (F-2)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--now")
    parser.add_argument("--notified", type=Path)
    parser.add_argument("--remember", type=Path)
    parser.add_argument("--learn-state", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        refusal = jcd.output_refusal(args.out) if args.out else None
        if refusal:  # an output root is admitted before any read (F-2)
            raise JoinTurnError(refusal)
        receipts = jcd._load(args.receipts) if args.receipts else None
        notified = jcd._load(args.notified) if args.notified else ()
        envelope = jcd._load(args.remember) if args.remember else None
        learn_state = jcd._load(args.learn_state) if args.learn_state else None
        result = turn(args.lane, args.cycle, root=args.frozen_root, learn_ref=args.learn_ref, receipts=receipts,
                      now_utc=args.now, notified=notified, envelope=envelope, learn_state=learn_state)
        if args.out:
            write_turn(args.out, result)
    except (JoinTurnError, jc.JoinCardError, jcd.DeliveryError, lv.LaneViewError, fz.FrozenInputError) as error:
        print(f"refused: {error}")
        return 2
    except (ValueError, OSError, RecursionError):  # a file nested too deeply to read or write is unreadable too
        print("refused: a file could not be read or written")
        return 2
    sys.stdout.write(result["card"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
