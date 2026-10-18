"""Hello Mind v0.1: one public synthetic fixture through the real JOIN chain, end to end. Sandbox, advisory, never a
gate, not Q4 and not a utility measurement.

Lead round 9/10 (proposal R09_PROPOSAL.v1.md, Grok 3d7b96b4), decided by fable-5 as diamond round D3. Every stage is
the existing code, called as its own CLI calls it; nothing is re-implemented and no stage is stubbed:

1. ``pins``: the live component versions must be exactly ``PINS``, the pair this module was written for (JOIN turn
   v0.3, card v0.6 (D11: this module was first written for card v0.5; Hello Mind passes no REMEMBER or LEARN input,
   so in the card it renders v0.6 changes only the version in the header), outcome adapter v0.1 reading turn v0.3,
   frozen manifest v1, turn schema v0). Any other pair is
   refused before any output.
2. ``fixture``: ``write_fixture`` writes one public synthetic cycle (``CYCLE``) with the six files a frozen cycle has
   and its ``wd.mind-frozen-cycles.v1`` manifest. The snapshot is the real core's (``tools.wd_bridge_mind``
   ``full_rebuild`` and ``snapshot``) over four fixed synthetic events, in its publication envelope; the GitHub
   files are fixed synthetic objects whose raw lists the derived files link by digest. claude-rco-2 is a commit
   author of the one PR, so it fills no slot, and the Tools build vote is the only empty one. No clock, disk default,
   historical root, bridge, GitHub, model, network or private data is read.
3. ``load``: ``lane_view_frozen.load_frozen``, the verified load (one changed byte refuses it).
4. ``turn``: ``join_turn.turn`` (load, ``lane_view``, HEAR ``deliver`` without receipts, so every receipt is UNKNOWN
   no_source, and ``card``) for the lane ``LANE``.
5. ``write_turn``: ``join_turn.write_turn`` into ``<out>\\turn``.
6. ``judge``: ``join_outcome_adapter.judge`` over that directory (its re-render refuses a changed card even when its
   digest is fixed); its result is written as ``outcome.json``.

``report.json`` holds each seam's status (``ok``, ``refused`` with the stage's own fixed reason, or ``not_run`` after
a refusal), the fixture, turn-output, outcome and code digests (``HELLO_CODE``, the import closure, kept equal by a
test), the card's UTF-8 size, the explicit frozen as-of and ``authority`` none. A capture that ``--pruned-capture``
leaves null is never read, and the report then names the event inventory UNKNOWN. The outcome is at best
``ACCEPTED_CARD_UTILITY_UNKNOWN``: a synthetic card has no real use event or receipt, so its utility is UNKNOWN, never
a successful action.

Output: ``--out`` must be a new directory inside an existing ``.codex-audit`` directory (the check of
``join_card_delivery.output_refusal``); an existing one is refused before any byte is written, so a first run's bytes
are never replaced. A refusal after the fixture is written keeps the fixture and the report as evidence: nothing is
ever removed, and there is no whole-directory or crash-atomicity claim (``write_turn`` keeps its own).

usage: python -B sandbox/mind/hello_mind.py --out DIR [--pruned-capture]
exit 0 prints the card; exit 2 prints ``refused: <reason>`` or ``refused: <seam>: <reason>``.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[1]), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
import join_card as jc  # noqa: E402
import join_card_delivery as jcd  # noqa: E402
import join_outcome_adapter as jo  # noqa: E402
import join_turn as jt  # noqa: E402
import lane_view as lv  # noqa: E402
import lane_view_frozen as fz  # noqa: E402

VERSION = "hello-mind-v0.1"
REPORT_SCHEMA = "wd.mind-hello-report.v0"
SCOPE = "public synthetic fixture integration; not Q4, not a gate, not a utility measurement"
PINS = {"join_turn": "join-turn-v0.3", "join_card": "join-card-v0.6",
        "outcome_adapter": "wd.mind-join-outcome-adapter.v0.1", "adapter_turn": "join-turn-v0.3",
        "frozen_manifest": "wd.mind-frozen-cycles.v1", "turn_schema": "wd.mind-join-turn.v0"}
HELLO_CODE = tuple(sorted({*jt.CODE, "sandbox/mind/hello_mind.py", "sandbox/mind/join_outcome_adapter.py",
                           "sandbox/mind/join_card_outcomes.py"}))  # the repo import closure, tested
SEAMS = ("pins", "fixture", "load", "turn", "write_turn", "judge")
ACCEPTED = "ACCEPTED_CARD_UTILITY_UNKNOWN"
PAIR = "the component versions are not the pair Hello Mind was written for; nothing is written"
EXISTS = "the output directory already exists; Hello Mind only writes into a new directory"
NOT_INSIDE = "the frozen fixture is not inside the output directory"
NOT_THE_FIXTURE = "the frozen manifest is not one Hello Mind cycle"
READ_WRITE = "a file could not be read or written"
REFUSALS = (fz.FrozenInputError, jt.JoinTurnError, jc.JoinCardError, jcd.DeliveryError, lv.LaneViewError,
            ws.WorkspaceError)

# the fixture: one lane, one cycle, one PR whose only empty slot is the Tools build vote (fixed, synthetic, public)
LANE = "codex-tools-1"
CYCLE = "hello-1"
AS_OF = "2026-10-18T12:00:00Z"
OBSERVED = "2026-10-18T11:55:00Z"
CHECKS_DONE = "2026-10-18T10:59:00Z"
LEARN_REF = "hello-mind:no-learn-state"
PR = 1
BRANCH = "hello-mind/synthetic-pr-1"
HEAD_A, HEAD_B = (hashlib.sha1(seed).hexdigest() for seed in (b"hello mind head A", b"hello mind head B"))
REQUIRED = ("unified", "test (3.12)")
AUTHORS = ("claude-rco-2", "codex-lead-1", "codex-tools-1")  # an RCO that is a commit author fills no slot
UUIDS = {lane: f"00000000-0000-4000-8000-{index:012d}" for index, lane in enumerate(lv.LANES, 1)}
LOG_IDENTITY = "events:" + hashlib.sha256(b"hello mind synthetic event log").hexdigest()  # not a capture window


class HelloMindError(ValueError):
    """Fixed messages that never echo input content."""


def live_pins() -> dict[str, str]:
    return {"join_turn": jt.VERSION, "join_card": jc.VERSION, "outcome_adapter": jo.SCHEMA,
            "adapter_turn": jo.TURN_VERSION, "frozen_manifest": fz.MANIFEST_SCHEMA, "turn_schema": jt.TURN_SCHEMA}


def _check_pins() -> None:
    if live_pins() != PINS:
        raise HelloMindError(PAIR)


def _event(ts: str, agent: str, kind: str, status: str, payload: dict[str, Any], task: str = BRANCH) -> dict[str, Any]:
    return {"ts_utc": ts, "agent": agent, "type": kind, "status": status, "task_id": task, "payload": payload,
            "agent_uuid": UUIDS[agent]}


EVENTS = (
    _event("2026-10-18T10:00:00Z", "codex-tools-1", "message", "in_progress", {}, task="codex-tools-1/hello-lane-work"),
    _event("2026-10-18T10:30:00Z", "codex-lead-1", "message", "requested", {"pr": PR, "head": HEAD_A}),
    _event("2026-10-18T11:00:00Z", "claude-rco-1", "decision", "rco_pass",
           {"pr": PR, "head": HEAD_B, "task_id": BRANCH}),
    _event("2026-10-18T11:00:01Z", "codex-lead-1", "decision", "build_consensus_pass",
           {"pr": PR, "head": HEAD_B, "canonical_task_id": BRANCH, "merge_owner": "operator"}),
)


def fixture_events() -> list[dict[str, Any]]:
    """The fixture's event rows (copies): the Tools lane's own work, a request at head A, then the RCO1 and Lead votes
    at head B, the GitHub head, so the bridge head lags GitHub as in the PR1905 night."""
    return copy.deepcopy(list(EVENTS))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture_github() -> dict[str, Any]:
    """The four GitHub objects of the cycle: required checks all SUCCESS at head B, the raw lists linked by digest."""
    rollup = [{"__typename": "CheckRun", "completedAt": CHECKS_DONE, "conclusion": "SUCCESS", "name": name,
               "status": "COMPLETED"} for name in REQUIRED]
    pr_list = [{"headRefName": BRANCH, "headRefOid": HEAD_B, "number": PR, "state": "OPEN"}]
    open_status = [{"headRefOid": HEAD_B, "mergeStateStatus": "CLEAN", "number": PR, "statusCheckRollup": rollup}]
    context = {"schema": "wd.bridge-mind-pr-context.v2", "authority": "none",
               "capture_sha256": _digest(lv.canonical(pr_list).encode("utf-8")), "observed_utc": OBSERVED,
               "charter": {"loaded": True, "charter_blob": "1" * 40, "classifier_blob": "2" * 40},
               "prs": {str(PR): {"state": "OPEN", "head": HEAD_B, "head_ref_name": BRANCH, "path_class": None,
                                 "commit_authors": list(AUTHORS)}}}
    status = {"schema": "wd.bridge-mind-gh-status.v1", "authority": "none", "observed_utc": OBSERVED,
              "required_contexts": list(REQUIRED), "list_sha256": _digest(lv.canonical(open_status).encode("utf-8")),
              "mentions_captured": False,
              "prs": {str(PR): {"head": HEAD_B, "merge_state": "CLEAN",
                                "required_results": {name: ["SUCCESS"] for name in REQUIRED},
                                "missing_required": [], "all_required_success": True}}}
    return {"context": context, "status": status, "pr_list": pr_list, "open_status": open_status}


def fixture_files(*, capture: bool = True) -> dict[str, bytes | None]:
    """The six files of the cycle by manifest name; the capture is None when ``capture`` is false (pruned)."""
    events = fixture_events()
    github = fixture_github()
    source = {"log_identity": LOG_IDENTITY, "generation": None, "cursor_start": 0, "cursor_end": len(events),
              "event_count": len(events), "coverage": "complete"}
    state = core.full_rebuild([{"ref": event_id(row), "event": row} for row in events], source=source,
                              as_of_utc=AS_OF)
    snapshot = core.snapshot(state, as_of_utc=AS_OF)
    publication = {"schema": "wd.bridge-mind-publication.v1", "snapshot": snapshot,
                   "snapshot_sha256": _digest(core.canonical_bytes(snapshot))}
    return {"capture.json": core.canonical_bytes({"events": events}) if capture else None,
            "context.json": core.canonical_bytes(github["context"]),
            "observed-status.json": core.canonical_bytes(github["status"]),
            "gh-pr-list.json": lv.canonical(github["pr_list"]).encode("utf-8"),
            "gh-open-status.json": lv.canonical(github["open_status"]).encode("utf-8"),
            fz.PUBLICATION: core.canonical_bytes(publication)}


def write_fixture(root: Path, *, capture: bool = True) -> Path:
    """The frozen fixture in the new directory ``root``: each file created exclusively, then the manifest."""
    root = Path(root)
    root.mkdir(parents=True)  # a new directory only: an existing one raises
    digests: dict[str, str | None] = {}
    for name, data in fixture_files(capture=capture).items():
        if data is None:
            digests[name] = None
            continue
        path = root.joinpath(CYCLE, *name.split("\\"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as handle:
            handle.write(data)
        digests[name] = _digest(data)
    with (root / "manifest.json").open("xb") as handle:
        handle.write(core.canonical_bytes({"schema": fz.MANIFEST_SCHEMA,
                                           "cycles": [{"cycle": CYCLE, "files": digests}]}))
    return root


def judge_turn(turn: Path) -> dict[str, Any]:
    """The real outcome adapter over one turn directory of the fixture lane."""
    return jo.judge(turn, LANE, learn_ref=LEARN_REF)


def chain(out: Path, frozen: Path) -> dict[str, Any]:
    """Seams 3 to 6 over the fixture at ``frozen`` (inside the existing directory ``out``), then ``report.json``; the
    first refusing seam stops the chain and every later one is ``not_run``. Returns the report."""
    _check_pins()
    out, frozen = Path(out).resolve(), Path(frozen).resolve()
    if not frozen.is_relative_to(out) or frozen == out:
        raise HelloMindError(NOT_INSIDE)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA, "version": VERSION, "authority": "none", "scope": SCOPE, "lane": LANE, "cycle": CYCLE,
        "pins": dict(PINS), "frozen_root": frozen.relative_to(out).as_posix(), "frozen_as_of": None, "fixture": None,
        "event_inventory": "UNKNOWN", "card_utf8_bytes": None, "turn_outputs": None, "outcome_sha256": None,
        "adapter": None, "outcome": "REFUSED", "utility": "UNKNOWN",
        "utility_reason": "a synthetic card has no real use event or receipt",
        "code_sha256": {name: _digest((jt.REPO / name).read_bytes()) for name in HELLO_CODE},
        "seams": [{"name": "pins", "status": "ok"}]}
    found: dict[str, Any] = {}

    def fixture() -> dict[str, Any]:
        manifest, manifest_sha = fz.load_manifest(frozen)
        cycles = manifest["cycles"]
        if len(cycles) != 1 or not isinstance(cycles[0], dict) or cycles[0].get("cycle") != CYCLE \
                or not isinstance(cycles[0].get("files"), dict):
            raise HelloMindError(NOT_THE_FIXTURE)
        report["fixture"] = {"manifest_sha256": manifest_sha, "files": dict(cycles[0]["files"])}
        return {}

    def load() -> dict[str, Any]:
        data = fz.load_frozen(frozen)
        [case] = data["cycles"]
        report["frozen_as_of"] = case["as_of_utc"]
        events = sum(capture["events"] for capture in data["captures"])
        report["event_inventory"] = ("UNKNOWN_capture_pruned" if "capture.json" in case["nulls"]
                                     else f"fixture_events_{events}")
        return {"nulls": list(case["nulls"]), "captures": len(data["captures"])}

    def turn() -> dict[str, Any]:
        found["turn"] = jt.turn(LANE, CYCLE, root=frozen, learn_ref=LEARN_REF)
        report["card_utf8_bytes"] = len(found["turn"]["card"].encode("utf-8"))
        return {}

    def write_turn() -> dict[str, Any]:
        report["turn_outputs"] = dict(jt.write_turn(out / "turn", found["turn"])["outputs"])
        return {}

    def judge() -> dict[str, Any]:
        result = judge_turn(out / "turn")
        data = jo.result_bytes(result)
        with (out / "outcome.json").open("xb") as handle:
            handle.write(data)
        report["outcome_sha256"] = _digest(data)
        report["adapter"] = {key: result.get(key) for key in ("card", "exposure", "shown_k", "hidden_prs", "gap")}
        card = result.get("card")
        if card != "accepted":
            raise HelloMindError(card[len("refused: "):] if isinstance(card, str) and card.startswith("refused: ")
                                 else "the outcome adapter result is not accepted")
        return {}

    stopped = False
    for name, seam in (("fixture", fixture), ("load", load), ("turn", turn), ("write_turn", write_turn),
                       ("judge", judge)):
        if stopped:
            report["seams"].append({"name": name, "status": "not_run"})
            continue
        try:
            report["seams"].append({"name": name, "status": "ok", **seam()})
        except (HelloMindError, *REFUSALS) as error:
            report["seams"].append({"name": name, "status": "refused", "reason": str(error)})
            stopped = True
        except (ValueError, OSError, RecursionError):
            report["seams"].append({"name": name, "status": "refused", "reason": READ_WRITE})
            stopped = True
    if not stopped:
        report["outcome"] = ACCEPTED
    ws.guard_object(report)
    with (out / "report.json").open("xb") as handle:
        handle.write((json.dumps(report, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    return report


def run(out: Path, *, capture: bool = True) -> dict[str, Any]:
    """The whole chain into the new directory ``out`` (module docstring); returns the report."""
    _check_pins()
    target = Path(out).resolve()
    refusal = jcd.output_refusal(target)
    if refusal:
        raise HelloMindError(refusal)
    if target.exists():
        raise HelloMindError(EXISTS)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.mkdir()
    except FileExistsError:
        raise HelloMindError(EXISTS) from None
    return chain(target, write_fixture(target / "frozen", capture=capture))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)  # no default output directory
    parser.add_argument("--pruned-capture", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = run(args.out, capture=not args.pruned_capture)
    except (HelloMindError, *REFUSALS) as error:
        print(f"refused: {error}")
        return 2
    except (ValueError, OSError, RecursionError):
        print(f"refused: {READ_WRITE}")
        return 2
    if report["outcome"] != ACCEPTED:
        seam = next(seam for seam in report["seams"] if seam["status"] == "refused")
        print(f"refused: {seam['name']}: {seam['reason']}")
        return 2
    sys.stdout.write((Path(args.out) / "turn" / "card.txt").read_bytes().decode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
