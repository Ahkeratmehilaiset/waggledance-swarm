"""D19 (Lead's slice mind-chain-real-join-integration-20261019): the S1 chain driver run end to end with the REAL JOIN
stages, never fake_join. A synthetic committed shadow cycle whose capture events and published snapshot are what the
real core writes (the step-1 fixtures of step1/test_lane_view.py, with a capture-window log identity as the shadow
producers write it) goes through mind_chain_driver.run_committed_chain: freeze_committed_cycle copies it, and
join_frozen_cycle loads the copy with the actual frozen loader (lane_view_frozen.load_frozen) and calls the actual
join_turn.turn_case for every lane. Each card must equal the card turn_case writes for the same cycle held in memory.
The chain freezes no GitHub capture, so the real view abstains on every PR before it reads an event body: the cards do
not depend on the bodies (measured below), and the load-back test is what pins the snapshot, bodies and as-of that JOIN
is given. A snapshot whose identity claims complete coverage and the transport-shaped snapshot of
test_mind_chain_driver.py are refused by the real chain with fixed reasons, and the freeze stays with join_validated
false. Synthetic only: no data disk, no private memory, no model, no live feed; no
REMEMBER envelope, LEARN state or receipts (envelope=None, receipts=None, as the driver calls JOIN), so a pass here is
JOIN only and does not stand for the missing REMEMBER adapter.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_mind_chain_real_join.py -q -p no:cacheprovider
"""
import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[1]), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_turn as jt  # noqa: E402
import lane_view_frozen as fz  # noqa: E402
import test_lane_view as tlv  # noqa: E402  (step-1 fixtures: the real core writes the snapshot rows)
from tools.bridge_compact_view import event_id  # noqa: E402

LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")  # literal: never the module's list
CYCLE = "20261013T180000Z"  # the step-1 fixtures' as-of as a producer cycle directory name
LEARN_REF = "synthetic:not-executed"  # the driver's own literal, so the expected card is the one JOIN writes for it
NULLS = ["context.json", "gh-open-status.json", "gh-pr-list.json", "observed-status.json"]


def load_driver():
    # The same file-path load as test_mind_chain_driver.py; nothing here replaces _join_module.
    spec = importlib.util.spec_from_file_location("s1_chain_real_join_under_test", HERE / "mind_chain_driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DRIVER = load_driver()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def committed_cycle(audit, events, snapshot):
    """One finished producer cycle as test_mind_chain_driver.py's ``finished`` fixture lays it out, with real core
    content: capture.json holds the events and the publication holds the core snapshot of exactly those events."""
    live = audit / "live"
    folder = live / "cycles" / CYCLE
    (folder / "run").mkdir(parents=True)
    cursor_object = {"file_identity": "synthetic-file-1", "generation": "synthetic-log-1", "offset": len(events)}
    cursor = "bc1." + base64.urlsafe_b64encode(encoded(cursor_object)).decode().rstrip("=")
    state = {"schema": "wd.bridge-mind-producer-state.v1", "generation": 1, "cursor": cursor,
             "source": {"file_identity": "synthetic-file-1", "log_generation": "synthetic-log-1"},
             "window": [], "window_sha256": digest(b"")}
    snapshot_sha = digest(encoded(snapshot))
    payloads = {
        "capture.json": encoded({"events": events}),
        "state.json": encoded(state),
        "detail-manifest.txt": b"synthetic-only\n",
        "run/reflected.publication.json": encoded({"schema": "wd.bridge-mind-publication.v1", "snapshot": snapshot,
                                                   "snapshot_sha256": snapshot_sha}),
        "run/board.txt": b"UNKNOWN\n",
        "run/receipt.json": encoded({"publication": {"snapshot_sha256": snapshot_sha}}),
    }
    for name, raw in payloads.items():
        (folder / name).write_bytes(raw)
    last_ok = {"cycle_dir": CYCLE, "generation": 1, "observed_utc": tlv.AS_OF,
               "capture_file": f"cycles/{CYCLE}/capture.json", "capture_sha256": digest(payloads["capture.json"]),
               "state_file": f"cycles/{CYCLE}/state.json", "state_sha256": digest(payloads["state.json"]),
               "context_file": None, "context_sha256": None,
               "publication_file": f"cycles/{CYCLE}/run/reflected.publication.json",
               "board_file": f"cycles/{CYCLE}/run/board.txt", "receipt_file": f"cycles/{CYCLE}/run/receipt.json",
               "snapshot_sha256": snapshot_sha, "board_text_sha256": digest(payloads["run/board.txt"]),
               "reader": "synthetic-compact-reader", "counts": {}, "global_question": "UNKNOWN", "measures": {}}
    latest = {"schema": "wd.bridge-mind-shadow-latest.v2", "authority": "none", "scope": "shadow_observation_not_gate",
              "committed_utc": tlv.AS_OF, "last_ok": last_ok, "verify": "synthetic-only",
              "report_to": {"type": "message", "task_id": "synthetic"}, "advisory": "observation only"}
    (live / "latest.json").write_bytes(encoded(latest))
    return live


def core_cycle(audit, *, log_identity=None):
    """The step-1 PR1905 events, their core snapshot as of the fixtures' as-of, and the live cycle holding both. The
    default identity is a capture window named by the capture's own sha256, as the shadow producers name it."""
    events = tlv.base_events()
    identity = log_identity or "capture-window:" + digest(encoded({"events": events}))
    snapshot = tlv.snapshot_of(events, tlv.AS_OF, log_identity=identity)
    return events, snapshot, committed_cycle(audit, events, snapshot)


def binding_of(frozen):
    return json.loads((frozen / "source-binding.json").read_bytes())


def test_the_driver_reaches_the_real_join_modules():
    joiner = DRIVER._join_module()
    assert Path(joiner.__file__).resolve() == HERE / "join_turn.py"
    assert Path(joiner.fz.__file__).resolve() == HERE / "step1" / "lane_view_frozen.py"
    assert tuple(DRIVER.LANES) == LANES


def test_the_real_chain_writes_the_cards_join_writes_for_the_same_cycle(tmp_path):
    audit = tmp_path / ".codex-audit"
    events, snapshot, live = core_cycle(audit)
    frozen = audit / "frozen"
    result = DRIVER.run_committed_chain(live, frozen)
    assert result["schema"] == "wd.mind-chain-result.v1" and result["authority"] == "none"
    assert result["scope"] == "frozen_observation_not_gate" and result["cycle"] == CYCLE
    assert result["transport_binding_sha256"] == digest((frozen / "source-binding.json").read_bytes())
    assert result["join_calls_completed"] == len(LANES) == len(result["cards"])
    case = {"cycle": CYCLE, "snapshot": snapshot, "github": None,
            "bodies": {event_id(row): row for row in events}, "as_of_utc": tlv.AS_OF}
    for lane, card in zip(LANES, result["cards"]):
        expected = jt.turn_case(lane, case, learn_ref=LEARN_REF, envelope=None, receipts=None)
        assert card["lane"] == lane
        assert card["text"] == expected["card"]
        assert card["bytes"] == len(expected["card"].encode("utf-8")) <= 2048
        assert card["sha256"] == digest(expected["card"].encode("utf-8"))
        assert card["coverage"] == "UNKNOWN" and card["github_usable"] is False
        assert card["as_of_utc"] == expected["as_of_utc"] == tlv.AS_OF
    assert len({card["text"] for card in result["cards"]}) == len(LANES)  # each lane's own card: no mixup passes
    assert binding_of(frozen)["join_validated"] is False  # the freeze record is never rewritten by a JOIN pass


def test_the_frozen_copy_loads_back_to_exactly_what_join_reads(tmp_path):
    audit = tmp_path / ".codex-audit"
    events, snapshot, live = core_cycle(audit)
    frozen = audit / "frozen"
    binding = DRIVER.freeze_committed_cycle(live, frozen)
    loaded = fz.load_frozen(frozen)
    assert loaded["manifest_sha256"] == binding["manifest_sha256"]
    cycle, = loaded["cycles"]
    assert cycle["cycle"] == CYCLE and cycle["snapshot"] == snapshot and cycle["github"] is None
    assert cycle["bodies"] == {event_id(row): row for row in events}
    assert cycle["as_of_utc"] == tlv.AS_OF
    assert sorted(cycle["nulls"]) == NULLS
    assert loaded["captures"] == [{"cycle": CYCLE, "sha256": binding["observed_file_sha256"]["capture.json"],
                                   "events": len(events)}]


def test_with_no_github_capture_every_pr_abstains_before_a_body_is_read(tmp_path):
    # The chain never freezes GitHub, so the real view abstains on every PR before it reads an event body: a card is
    # the same with no bodies at all, and only the load-back test above pins the bodies JOIN is given.
    events, snapshot, _ = core_cycle(tmp_path / ".codex-audit")
    case = {"cycle": CYCLE, "snapshot": snapshot, "github": None,
            "bodies": {event_id(row): row for row in events}, "as_of_utc": tlv.AS_OF}
    for lane in LANES:
        turn = jt.turn_case(lane, case, learn_ref=LEARN_REF, envelope=None, receipts=None)
        bare = jt.turn_case(lane, dict(case, bodies={}), learn_ref=LEARN_REF, envelope=None, receipts=None)
        assert bare["card"] == turn["card"] and bare["view"] == turn["view"]
        view = turn["view"]
        assert view["prs"] and view["counts"]["abstain"] == len(view["prs"]) == len(view["abstained"])
        assert all(row["reasons"] == ["github_capture_missing"] for row in view["abstained"])
        assert view["github"]["usable"] is False and not any(entry["actions"] for entry in view["lanes"].values())


def test_a_snapshot_claiming_complete_coverage_is_refused_and_the_freeze_stays(tmp_path):
    # Not a capture window, so the real lane view reports complete coverage; the chain never passes that on.
    audit = tmp_path / ".codex-audit"
    events, snapshot, live = core_cycle(audit, log_identity="events:" + "a" * 64)
    case = {"cycle": CYCLE, "snapshot": snapshot, "github": None,
            "bodies": {event_id(row): row for row in events}, "as_of_utc": tlv.AS_OF}
    assert jt.turn_case(LANES[0], case, learn_ref=LEARN_REF)["view"]["coverage"] == "complete"
    frozen = audit / "frozen"
    with pytest.raises(DRIVER.ChainRefusal) as refused:
        DRIVER.run_committed_chain(live, frozen)
    assert refused.value.primary_reason == "join_invariant" and not refused.value.cleanup_failed
    assert binding_of(frozen)["join_validated"] is False


def test_the_transport_shaped_snapshot_is_refused_by_the_real_join_and_the_freeze_stays(tmp_path):
    # test_mind_chain_driver.py's finished-cycle snapshot: transport-shaped only, which the core refuses.
    audit = tmp_path / ".codex-audit"
    snapshot = {"authority": "none", "scope": "bounded_projection_not_gate", "source": {"as_of_utc": tlv.AS_OF}}
    live = committed_cycle(audit, [], snapshot)
    frozen = audit / "frozen"
    with pytest.raises(fz.FrozenInputError):
        fz.read_publication({"schema": "wd.bridge-mind-publication.v1", "snapshot": snapshot,
                             "snapshot_sha256": digest(encoded(snapshot))})
    with pytest.raises(DRIVER.ChainRefusal) as refused:
        DRIVER.run_committed_chain(live, frozen)
    assert refused.value.primary_reason == "join_refused" and not refused.value.cleanup_failed
    assert binding_of(frozen)["join_validated"] is False
