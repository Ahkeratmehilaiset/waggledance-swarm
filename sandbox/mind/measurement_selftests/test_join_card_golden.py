"""One synthetic D19 real-JOIN oracle, authored by codex-tools-1.

No test-module fixture imports, fake_join, live data, memory or model calls.
EXPECTED_CARD was hand-derived and frozen BEFORE the first subject execution:
EXPECTED_CARD.txt SHA256 2fbc3d26... (615 bytes), DERIVATION.json 71a992be....
This all-abstain card tests correctness, not owed-action utility or M1/M2.

When included at the requested repo path, pytest collects the single test.
Tools' exact-source author runs use the CLI below in a fresh interpreter with
the EXISTING B1A782C1 data-denial Boundary (not a new harness). Boundary.install
is process-scoped and therefore is never installed in a shared pytest process.
The reusable test restores all subject import-path/module changes.

python -I -B -S test_join_card_golden.py --source-root ROOT
  --boundary-path EXISTING_M0_RUNNER --out OWN_AUDIT_NEW_DIR [--mutant]
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

AS_OF = "2026-10-19T12:00:00Z"
CYCLE = "20261019T120000Z"
LANE = "codex-tools-1"
LEARN_REF = "synthetic:not-executed"
MUTANT = "MUTANT_WRONG_LANE_HEADER"
BOUNDARY_SHA256 = "b1a782c13a2b96e37bde9953cfdae30c45e69ef167578ad0c8fa227b828d279c"
GOLDEN_SHA256 = "2fbc3d26c8517de28567deb5ecc5729ea65e6d85b0ee5374076140bdf57ae8a1"
EXPECTED_CARD = (
    "JOIN codex-tools-1 as_of=2026-10-19T12:00:00Z now=2026-10-19T12:00:00Z join-card-v0.6; advisory, "
    "not a gate; check the canonical tools before any vote\n"
    "HEAR waits over 30 min on your slots: new 0, unchanged 0, carried 0, unknown 0, below 0 "
    "(join-card-delivery-semantics-v1.5, card_trust card_coverage_not_complete)\n"
    "SEE no PR with your build_tools slot empty; abstained 1 (UNKNOWN reasons in the view), coverage UNKNOWN\n"
    "SLOT none: no PR in the view has your slot empty\n"
    "UNDERSTAND nothing of yours is open in this view; nothing is derived\n"
    "LEARN v0 records nothing from this card; LEARN contract synthetic:not-executed\n"
).encode("utf-8")
SOURCE_PINS = {
    "sandbox/mind/mind_chain_driver.py": "9b1dd2b42e7dfd8e85197c1116c891994d2f4c076bf663345466fd9efc4cfc9e",
    "sandbox/mind/join_card.py": "832bd0f5e95a9b3a8d610efc7fde271a2da60f70736b2ad3fe83e4237761cf40",
    "sandbox/mind/join_card_delivery.py": "b98321c15d8b47e082433b024250acafafff1d0c02e8beaf77dbd2b5f84ac527",
    "sandbox/mind/join_turn.py": "41392ff7fff86417673ce58c850a09d2b55e9457f6106f8c7f8164d17d56c861",
    "sandbox/mind/learn_step.py": "1104f225b4a1b971e8dc304e36ee494324b13ae8d4bbdde07452d43034925ab2",
    "sandbox/mind/step1/lane_view.py": "aa0ed04e514e84af441b98b71aee60b9e00654cf0f93f537aa594076bf652751",
    "sandbox/mind/step1/lane_view_frozen.py": "f8e9b903936dffb018900b80276369b4c1d52ff36c18a55f3114701f8bea5ddf",
    "tools/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "tools/bridge_compact_view.py": "67a3c279f6c28e08eb995dc2c204aee50f689a3f6c73a35bfe50b9dfbcedac70",
    "tools/bridge_diff_privacy.py": "88399ed7f0f3688eec6cf1a3c6cb443703c596fc914740e3aec0bbe26253d23f",
    "tools/bridge_mind_workspace.py": "4792e0edd737b479dbc746ae1e20c58c55c7a687689eb66a1eb5c3bf0e41c92e",
    "tools/wd_bridge_mind.py": "de5dd43be99125a14efd1e08c96ff162049411b76c451c0ee1129caeb86c7117",
    "waggledance/__init__.py": "2e5cec7a55d7037c67b4287bd6f86ae8d1c89e287f52d1746cfc8ba787644b7c",
    "waggledance/core/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "waggledance/core/bridge_log_reader.py": "ca13123220e9759e94b51c4d2b61f9e90cffbe94e0b19abb078045e66700a100"
}
SUBJECT_PREFIXES = (
    "sandbox", "tools", "waggledance", "join_turn", "join_card", "join_card_delivery",
    "lane_view", "lane_view_frozen", "learn_step", "_wd_join_golden_driver",
)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _subject_name(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in SUBJECT_PREFIXES)


@contextlib.contextmanager
def _subject_modules(root, *, exact_pins):
    """Scoped filesystem imports; verified source, never an old bytecode cache."""
    old_path = list(sys.path)
    old_modules = {name: module for name, module in sys.modules.items() if _subject_name(name)}
    old_bytecode = sys.dont_write_bytecode
    allowed = {str((root / name).resolve()).casefold(): (name, expected)
               for name, expected in SOURCE_PINS.items()}
    original = importlib.machinery.SourceFileLoader.get_code
    loaded = []

    def checked_code(loader, fullname):
        path = Path(loader.path).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            return original(loader, fullname)  # interpreter runtime, not other repository Source
        entry = allowed.get(str(path).casefold())
        if entry is None:
            raise AssertionError("unlisted_subject_import")
        raw = path.read_bytes()
        if exact_pins and _sha(raw) != entry[1]:
            raise AssertionError("executed_source_pin")
        loaded.append(entry[0])
        return compile(raw, str(path), "exec", dont_inherit=True)

    try:
        for name in tuple(sys.modules):
            if _subject_name(name):
                del sys.modules[name]
        sys.path[:] = [str(root), *old_path]
        sys.dont_write_bytecode = True
        with patch.object(importlib.machinery.SourceFileLoader, "get_code", checked_code):
            driver = _load_file("_wd_join_golden_driver", root / "sandbox/mind/mind_chain_driver.py")
            joiner = driver._join_module()  # real filesystem JOIN, never replaced
            assert Path(joiner.__file__).resolve() == root / "sandbox/mind/join_turn.py"
            assert Path(joiner.fz.__file__).resolve() == root / "sandbox/mind/step1/lane_view_frozen.py"
            yield driver, joiner, loaded
    finally:
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
        for name in tuple(sys.modules):
            if _subject_name(name):
                del sys.modules[name]
        sys.modules.update(old_modules)


def _committed_cycle(audit, core, event_id):
    """Inline synthetic producer-shaped fixture; no other test module is read."""
    events = [{
        "ts_utc": "2026-10-19T11:59:00Z",
        "agent": "codex-lead-1",
        "agent_uuid": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
        "type": "message", "status": "requested", "task_id": "synthetic/join-golden",
        "payload": {"pr": 7, "head": "a" * 40},
    }]
    capture = _encoded({"events": events})
    origin = {"log_identity": "capture-window:" + _sha(capture), "generation": None,
              "cursor_start": 0, "cursor_end": 1, "event_count": 1, "coverage": "complete"}
    state = core.full_rebuild([{"ref": event_id(events[0]), "event": events[0]}],
                              source=origin, as_of_utc=AS_OF)
    snapshot = core.snapshot(state, as_of_utc=AS_OF)
    cursor_object = {"file_identity": "synthetic-golden-file", "generation": "synthetic-golden-log", "offset": 1}
    cursor = "bc1." + base64.urlsafe_b64encode(_encoded(cursor_object)).decode("ascii").rstrip("=")
    transport_state = {
        "schema": "wd.bridge-mind-producer-state.v1", "generation": 1, "cursor": cursor,
        "source": {"file_identity": "synthetic-golden-file", "log_generation": "synthetic-golden-log"},
        "window": [], "window_sha256": _sha(b""),
    }
    publication_sha = _sha(_encoded(snapshot))
    payloads = {
        "capture.json": capture, "state.json": _encoded(transport_state),
        "detail-manifest.txt": b"synthetic-only\n",
        "run/reflected.publication.json": _encoded({
            "schema": "wd.bridge-mind-publication.v1", "snapshot": snapshot, "snapshot_sha256": publication_sha}),
        "run/board.txt": b"UNKNOWN\n",
        "run/receipt.json": _encoded({"publication": {"snapshot_sha256": publication_sha}}),
    }
    live = audit / "live"
    cycle = live / "cycles" / CYCLE
    (cycle / "run").mkdir(parents=True)
    for name, raw in payloads.items():
        with (cycle / name).open("xb") as handle:
            handle.write(raw)
    last_ok = {
        "cycle_dir": CYCLE, "generation": 1, "observed_utc": AS_OF,
        "capture_file": f"cycles/{CYCLE}/capture.json", "capture_sha256": _sha(capture),
        "state_file": f"cycles/{CYCLE}/state.json", "state_sha256": _sha(payloads["state.json"]),
        "context_file": None, "context_sha256": None,
        "publication_file": f"cycles/{CYCLE}/run/reflected.publication.json",
        "board_file": f"cycles/{CYCLE}/run/board.txt",
        "receipt_file": f"cycles/{CYCLE}/run/receipt.json",
        "snapshot_sha256": publication_sha, "board_text_sha256": _sha(payloads["run/board.txt"]),
    }
    latest = {"schema": "wd.bridge-mind-shadow-latest.v2", "authority": "none",
              "scope": "shadow_observation_not_gate", "committed_utc": AS_OF, "last_ok": last_ok}
    with (live / "latest.json").open("xb") as handle:
        handle.write(_encoded(latest))
    return live


def _check_card(root, audit, *, mutant=False, exact_pins=False):
    assert len(EXPECTED_CARD) == 615
    assert _sha(EXPECTED_CARD) == GOLDEN_SHA256, "frozen_oracle_changed"
    paths_before = list(sys.path)
    with _subject_modules(root, exact_pins=exact_pins) as (driver, joiner, loaded):
        core = sys.modules["tools.wd_bridge_mind"]
        event_id = sys.modules["tools.bridge_compact_view"].event_id
        live = _committed_cycle(audit, core, event_id)
        real_turn = joiner.turn_case
        observed = []

        def observe_turn(*args, **kwargs):
            turn = real_turn(*args, **kwargs)
            observed.append(turn)
            return turn

        real_card = joiner.jc.card

        def wrong_lane(*args, **kwargs):
            text = real_card(*args, **kwargs)
            assert text.startswith("JOIN codex-tools-1 ")
            return text.replace("JOIN codex-tools-1 ", "JOIN fable-5 ", 1)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(joiner, "turn_case", observe_turn))
            if mutant:
                stack.enter_context(patch.object(joiner.jc, "card", wrong_lane))
            result = driver.run_committed_chain(live, audit / "frozen", lanes=(LANE,))

        assert len(observed) == result["join_calls_completed"] == len(result["cards"]) == 1
        turn = observed[0]
        row = result["cards"][0]
        raw = row["text"].encode("utf-8")
        # Independent literal oracle: never turn_case output used as expected.
        assert raw == EXPECTED_CARD, "golden_card_bytes"
        assert row["lane"] == turn["lane"] == LANE
        assert row["as_of_utc"] == turn["as_of_utc"] == turn["now_utc"] == AS_OF
        assert row["bytes"] == 615 and len(raw) <= 2048
        assert row["sha256"] == GOLDEN_SHA256
        assert result["authority"] == turn["view"]["authority"] == turn["delivery"]["authority"] == "none"
        assert result["scope"] == "frozen_observation_not_gate" and result["cycle"] == CYCLE
        assert row["coverage"] == turn["view"]["coverage"] == "UNKNOWN"
        assert row["github_usable"] is False
        assert turn["view"]["github"] == {"usable": False, "reason": "github_absent"}, "view_github_reason"
        assert turn["view"]["abstained"] == [{
            "pr": 7, "reasons": ["github_capture_missing"],
            "detail": "UNKNOWN: PR7: no usable GitHub capture in this cycle",
        }]
        assert turn["view"]["counts"]["abstain"] == 1
        assert not any(lane["actions"] for lane in turn["view"]["lanes"].values())
        assert turn["delivery"]["card_trust"] == "card_coverage_not_complete"
        assert turn["delivery"]["ledger"] == []
        assert turn["envelope"] is None and turn["learn"] is None and turn["receipts_given"] is False
        assert b"LEARN contract synthetic:not-executed\n" in raw
        assert b"REMEMBER" not in raw and b"credited=" not in raw
        binding_raw = (audit / "frozen/source-binding.json").read_bytes()
        assert _sha(binding_raw) == result["transport_binding_sha256"]
        assert json.loads(binding_raw)["join_validated"] is False
        outcome = {"card_sha256": _sha(raw), "card_bytes": len(raw), "join_calls": len(observed),
                   "loaded_source_files": sorted(set(loaded)), "expected_sha256": GOLDEN_SHA256}
    assert sys.path == paths_before, "persistent_sys_path_mutation"
    return outcome


def test_real_join_card_matches_hand_derived_golden(tmp_path):
    """Ordinary synthetic regression test; temporary Source imports are restored."""
    root = Path(__file__).resolve().parents[3]
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    _check_card(root, audit)


def _main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--boundary-path", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--mutant", action="store_true")
    args = parser.parse_args()
    root, out, boundary_path = args.source_root.resolve(), args.out.resolve(), args.boundary_path.resolve()
    own_audit = Path(__file__).resolve().parent
    if not out.is_relative_to(own_audit) or out == own_audit or out.exists():
        raise RuntimeError("new_owned_audit_required")
    if _sha(boundary_path.read_bytes()) != BOUNDARY_SHA256:
        raise RuntimeError("boundary_pin")
    pins = {root / relative: expected for relative, expected in SOURCE_PINS.items()}
    for path, expected in pins.items():
        if _sha(path.read_bytes()) != expected:
            raise RuntimeError("source_pin")
    assert _sha(EXPECTED_CARD) == GOLDEN_SHA256 and len(EXPECTED_CARD) == 615
    boundary_module = _load_file("_wd_existing_m0_boundary", boundary_path)
    out.mkdir()
    boundary = boundary_module.Boundary(root, pins, out, {sys.base_prefix, sys.prefix},
                                       {Path(__file__).resolve(), boundary_path})
    boundary.install()  # Existing B1A782C1, Python-only, no native isolation claim.
    code, details = 0, {}
    try:
        details = _check_card(root, out, mutant=args.mutant, exact_pins=True)
        status, reason = "PASS", None
    except AssertionError as error:
        code, status, reason = 1, "FAIL", str(error)
        trace = error.__traceback__
        frames = []
        while trace is not None:
            frames.append({"file": trace.tb_frame.f_code.co_filename,
                           "line": trace.tb_lineno, "function": trace.tb_frame.f_code.co_name})
            trace = trace.tb_next
        details = {"failure_frames": frames}
    except BaseException as error:
        code, status, reason = 2, "REFUSED", type(error).__name__
    result = {
        "schema": "wd.synthetic-join-golden-result.v1", "status": status, "exit_code": code,
        "reason": reason, "mutant": MUTANT if args.mutant else None,
        "source_pins": SOURCE_PINS, "boundary_sha256": BOUNDARY_SHA256,
        "candidate_sha256": _sha(Path(__file__).read_bytes()),
        "expected_sha256": GOLDEN_SHA256, "expected_bytes": 615,
        "boundary_denials": boundary.denials, "last_denial": boundary.last_denial,
        "evidence_class": "author_synthetic_only", "actual_measurement": "NOT_RUN",
        "os_isolation": "NOT_PROVEN", **details,
    }
    with (out / "RESULT.json").open("x", encoding="ascii", newline="\n") as handle:
        json.dump(result, handle, indent=1, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps({key: result[key] for key in
                     ("status", "exit_code", "reason", "mutant", "boundary_denials", "expected_sha256")}))
    return code


if __name__ == "__main__":
    raise SystemExit(_main())

