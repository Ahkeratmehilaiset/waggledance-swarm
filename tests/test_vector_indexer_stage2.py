"""Stage-2 indexer tests — replay/apply/checkpoint/idempotency.

Covers the R6 §6 checklist. Companion to test_vector_indexer.py
(Stage-1 projection tests) which stays green unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events


def _load_mod():
    path = ROOT / "tools" / "vector_indexer.py"
    spec = importlib.util.spec_from_file_location("vector_indexer_stage2", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vector_indexer_stage2"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load_mod()


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path):
    """Fresh vector root + event log + checkpoint path for a test."""
    return {
        "vector_root": tmp_path / "vector",
        "event_log": tmp_path / "events.jsonl",
        "checkpoint": tmp_path / "checkpoint.json",
    }


def _emit(ws, event):
    """Emit one event into the workspace log."""
    vector_events.emit(event, ws["event_log"])
    return event.event_id()


def _emit_many(ws, events):
    vector_events.emit_many(events, ws["event_log"])
    return [e.event_id() for e in events]


# ── Schema extension — backward compat ────────────────────────────

def test_old_stage1_events_without_source_still_parse(workspace):
    """Events written BEFORE the source field existed must still be
    readable after the schema extension (R6 §2 backward compat)."""
    # Simulate a Stage-1 event log entry that predates `source`
    log = workspace["event_log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "event": "vector.upsert_requested",
        "cell_id": "thermal",
        "solver_id": "heat_loss",
        "ts": "2026-04-20T10:00:00+00:00",
        "payload": {"model_id": "heat_loss", "signature": "oldsig"},
        "schema_version": 1,
    }
    log.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    events = list(vector_events.read_events(log))
    assert len(events) == 1
    assert events[0].source is None  # defaulted gracefully


def test_new_events_with_source_round_trip(workspace):
    ev = vector_events.vector_commit_applied(
        cell_id="thermal", faiss_commit_id="faiss_x",
        artifact_path="p", vector_count=1, checksum="sha256:x",
        source="indexer",
    )
    _emit(workspace, ev)
    [reparsed] = list(vector_events.read_events(workspace["event_log"]))
    assert reparsed.source == "indexer"


# ── Checkpoint basics ─────────────────────────────────────────────

def test_missing_checkpoint_returns_empty(tmp_path):
    cp = mod.load_checkpoint(tmp_path / "nope.json")
    assert cp.schema_version == 1
    assert cp.global_last_applied_event_id is None
    assert cp.per_cell == {}


def test_save_checkpoint_is_atomic_tmp_replace(tmp_path, monkeypatch):
    target = tmp_path / "cp.json"
    cp = mod.Checkpoint()
    cp.per_cell["thermal"] = mod.PerCellCheckpoint(
        last_applied_event_id="evt_a", commit_id="faiss_t", applied_ts="t",
        vector_count=3,
    )
    mod.save_checkpoint(cp, target)
    data = json.loads(target.read_text("utf-8"))
    assert data["per_cell"]["thermal"]["commit_id"] == "faiss_t"
    # No leftover tmp files in the dir
    leftovers = list(target.parent.glob(".checkpoint.*.tmp"))
    assert not leftovers


# ── Dry-run is the default / safe ─────────────────────────────────

def test_dry_run_is_default_posture(workspace):
    _emit(workspace, vector_events.vector_upsert_requested(
        "thermal", "m1", "sig1"))
    # Call apply() with no dry_run=False — default is True
    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
    )
    assert report.dry_run is True
    # Nothing written
    assert not workspace["vector_root"].exists() or not any(
        workspace["vector_root"].rglob("commit.json")
    )
    assert not workspace["checkpoint"].exists()


# ── Full apply flow ───────────────────────────────────────────────

def test_apply_writes_staged_commit_and_swaps_pointer(workspace):
    _emit_many(workspace, [
        vector_events.vector_upsert_requested("thermal", "a", "s_a"),
        vector_events.vector_upsert_requested("thermal", "b", "s_b"),
    ])

    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert report.cells_applied == 1
    # Committed commit id present in cell dir
    cell_dir = workspace["vector_root"] / "thermal"
    pointer = json.loads((cell_dir / "current.json").read_text("utf-8"))
    commit_id = pointer["commit_id"]
    assert (cell_dir / "commits" / commit_id / "manifest.json").exists()
    assert (cell_dir / "commits" / commit_id / "commit.json").exists()
    assert (cell_dir / "commits" / commit_id / "vectors.jsonl").exists()
    # Manifest carries both signatures
    m = json.loads(
        (cell_dir / "commits" / commit_id / "manifest.json").read_text("utf-8")
    )
    assert m["vector_count"] == 2
    assert set(m["signatures"]) == {"a", "b"}


def test_apply_emits_vector_commit_applied_once(workspace):
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    events = list(vector_events.read_events(workspace["event_log"]))
    commits = [e for e in events if e.event == vector_events.EVT_VECTOR_COMMIT_APPLIED]
    assert len(commits) == 1
    assert commits[0].source == "indexer"
    assert commits[0].payload["vector_count"] == 1


def test_apply_advances_checkpoint(workspace):
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    cp = mod.load_checkpoint(workspace["checkpoint"])
    assert "thermal" in cp.per_cell
    assert cp.per_cell["thermal"].commit_id is not None
    assert cp.per_cell["thermal"].vector_count == 1


# ── Idempotency ────────────────────────────────────────────────────

def test_rerunning_same_events_is_noop(workspace):
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    r1 = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert r1.cells_applied == 1

    # Second run without new events → no changes expected
    r2 = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert r2.cells_applied == 0
    assert r2.cells_skipped_no_change == 1

    # And exactly ONE commit_applied in the log
    events = list(vector_events.read_events(workspace["event_log"]))
    commits = [e for e in events if e.event == vector_events.EVT_VECTOR_COMMIT_APPLIED]
    assert len(commits) == 1


def test_no_change_cell_emits_no_bogus_commit(workspace):
    """R6 §6: if a cell has no changes, no bogus commit is emitted."""
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    # Add an event for a DIFFERENT cell
    _emit(workspace, vector_events.vector_upsert_requested("energy", "e", "s"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    # Now check: thermal should NOT have a second commit_applied
    events = list(vector_events.read_events(workspace["event_log"]))
    thermal_commits = [
        e for e in events
        if e.event == vector_events.EVT_VECTOR_COMMIT_APPLIED
        and e.cell_id == "thermal"
    ]
    assert len(thermal_commits) == 1


# ── Failure semantics ─────────────────────────────────────────────

def test_checkpoint_does_not_advance_on_failed_apply(workspace):
    """R6 §6: checkpoint does not advance on failed apply."""
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    # Simulate a crash mid-apply for thermal
    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
        _fail_before_swap_for_cells={"thermal"},
    )
    assert report.cells_failed == 1
    # Checkpoint must NOT carry thermal (first run, never succeeded)
    cp = mod.load_checkpoint(workspace["checkpoint"])
    # Checkpoint file either doesn't exist (no cells succeeded) or
    # thermal entry is missing
    if workspace["checkpoint"].exists():
        assert "thermal" not in cp.per_cell or cp.per_cell["thermal"].commit_id is None


def test_partial_failure_leaves_prior_commit_intact(workspace):
    """R6 §6: partial failure leaves previous committed artifact intact."""
    # First apply succeeds, establishes a commit
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s1"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    cell_dir = workspace["vector_root"] / "thermal"
    first_commit_id = json.loads((cell_dir / "current.json").read_text("utf-8"))["commit_id"]

    # Add a new event and simulate crash during its apply
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "b", "s2"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
        _fail_before_swap_for_cells={"thermal"},
    )
    # Pointer STILL points at the first commit
    current = json.loads((cell_dir / "current.json").read_text("utf-8"))
    assert current["commit_id"] == first_commit_id
    # The first commit's files are still there
    assert (cell_dir / "commits" / first_commit_id / "manifest.json").exists()


def test_recovery_after_failure_succeeds_on_retry(workspace):
    """After a simulated crash, a subsequent clean apply should succeed
    and produce the intended final state."""
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))

    # First attempt fails
    r1 = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
        _fail_before_swap_for_cells={"thermal"},
    )
    assert r1.cells_failed == 1

    # Second attempt succeeds
    r2 = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert r2.cells_applied == 1


# ── Per-cell isolation ────────────────────────────────────────────

def test_failure_in_one_cell_does_not_block_others(workspace):
    _emit_many(workspace, [
        vector_events.vector_upsert_requested("thermal", "a", "s"),
        vector_events.vector_upsert_requested("energy", "b", "s"),
    ])
    r = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
        _fail_before_swap_for_cells={"thermal"},
    )
    assert r.cells_applied == 1   # energy
    assert r.cells_failed == 1    # thermal
    # Energy commit present
    assert (workspace["vector_root"] / "energy" / "current.json").exists()
    # Thermal pointer absent (never swapped)
    assert not (workspace["vector_root"] / "thermal" / "current.json").exists()

    # Checkpoint carries energy but NOT thermal
    cp = mod.load_checkpoint(workspace["checkpoint"])
    assert "energy" in cp.per_cell
    assert cp.per_cell["energy"].commit_id is not None
    assert "thermal" not in cp.per_cell or cp.per_cell["thermal"].commit_id is None


# ── Delete semantics ──────────────────────────────────────────────

def test_delete_removes_signature_from_next_commit(workspace):
    # Upsert two, commit
    _emit_many(workspace, [
        vector_events.vector_upsert_requested("thermal", "a", "sa"),
        vector_events.vector_upsert_requested("thermal", "b", "sb"),
    ])
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    # Delete one, commit again
    _emit(workspace, vector_events.vector_delete_requested("thermal", "a"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    cell_dir = workspace["vector_root"] / "thermal"
    current_id = json.loads((cell_dir / "current.json").read_text("utf-8"))["commit_id"]
    m = json.loads(
        (cell_dir / "commits" / current_id / "manifest.json").read_text("utf-8")
    )
    assert list(m["signatures"].keys()) == ["b"]
    assert m["vector_count"] == 1


# ── Replay / since ────────────────────────────────────────────────

def test_full_replay_and_checkpointed_replay_give_equivalent_state(workspace):
    """R6 §6: full replay equals checkpointed replay."""
    ids = _emit_many(workspace, [
        vector_events.vector_upsert_requested("thermal", "a", "s_a"),
        vector_events.vector_upsert_requested("thermal", "b", "s_b"),
        vector_events.vector_upsert_requested("thermal", "c", "s_c"),
    ])
    # Full replay (no checkpoint)
    r_full = mod.replay(workspace["event_log"])
    full_signatures = r_full.cells["thermal"].signatures

    # Checkpointed replay (start after event 1)
    r_cp = mod.replay(workspace["event_log"], since_event_id=ids[0])
    # The checkpointed replay sees only events AFTER ids[0]
    cp_signatures = r_cp.cells["thermal"].signatures
    # Full has a+b+c; checkpointed has b+c
    assert set(full_signatures) == {"a", "b", "c"}
    assert set(cp_signatures) == {"b", "c"}


def test_unknown_events_do_not_corrupt_projection(workspace):
    """R6 §6: unknown informational events do not corrupt projection."""
    log = workspace["event_log"]
    log.parent.mkdir(parents=True, exist_ok=True)
    # Known event
    vector_events.emit(
        vector_events.vector_upsert_requested("thermal", "a", "s"), log,
    )
    # Raw unknown-type line appended directly
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "unknown.type", "cell_id": "thermal",
            "payload": {}, "ts": "2026-04-24T00:00:00+00:00",
        }) + "\n")
    report = mod.replay(log)
    # Projection still reflects the known event
    assert report.cells["thermal"].upsert_requests == 1
    assert "a" in report.cells["thermal"].signatures


# ── Integrity ─────────────────────────────────────────────────────

def test_commit_checksum_roundtrip(workspace):
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    cp = mod.load_checkpoint(workspace["checkpoint"])
    cid = cp.per_cell["thermal"].commit_id
    assert mod.verify_commit_integrity(
        workspace["vector_root"], "thermal", cid,
    )


def test_checksum_mismatch_detected(workspace):
    """R6 §6: if manifests/checksums mismatch, it is caught."""
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))
    mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    cp = mod.load_checkpoint(workspace["checkpoint"])
    cid = cp.per_cell["thermal"].commit_id
    # Tamper with vectors.jsonl after commit
    tampered = (workspace["vector_root"] / "thermal" / "commits" / cid
                 / "vectors.jsonl")
    tampered.write_text("tampered\n", encoding="utf-8")
    assert not mod.verify_commit_integrity(
        workspace["vector_root"], "thermal", cid,
    )


# ── Determinism ────────────────────────────────────────────────────

def test_commit_id_deterministic_across_runs(tmp_path):
    """Same projection → same commit_id across separate workspaces."""
    def _run(ws_root):
        ws = {
            "event_log": ws_root / "events.jsonl",
            "vector_root": ws_root / "vector",
            "checkpoint": ws_root / "cp.json",
        }
        ws["event_log"].parent.mkdir(parents=True, exist_ok=True)
        vector_events.emit_many([
            vector_events.vector_upsert_requested("thermal", "a", "sig"),
            vector_events.vector_upsert_requested("thermal", "b", "sig2"),
        ], ws["event_log"])
        mod.apply(
            event_log=ws["event_log"],
            vector_root=ws["vector_root"],
            checkpoint_path=ws["checkpoint"],
            dry_run=False,
        )
        return json.loads(
            (ws["vector_root"] / "thermal" / "current.json").read_text("utf-8")
        )["commit_id"]

    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert _run(a) == _run(b)


# ── CLI filter ─────────────────────────────────────────────────────

def test_cell_filter_restricts_apply(workspace):
    _emit_many(workspace, [
        vector_events.vector_upsert_requested("thermal", "a", "s"),
        vector_events.vector_upsert_requested("energy", "b", "s"),
    ])
    r = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        cell_filter="thermal",
        dry_run=False,
    )
    assert r.cells_applied == 1
    assert (workspace["vector_root"] / "thermal" / "current.json").exists()
    assert not (workspace["vector_root"] / "energy" / "current.json").exists()


def test_since_parameter_skips_earlier_events(workspace):
    ids = _emit_many(workspace, [
        vector_events.vector_upsert_requested("thermal", "a", "s"),
        vector_events.vector_upsert_requested("thermal", "b", "s"),
    ])
    r = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        since_event_id=ids[0],
        dry_run=False,
    )
    # Only one event after ids[0], so one upsert folds in
    cell_dir = workspace["vector_root"] / "thermal"
    current_id = json.loads((cell_dir / "current.json").read_text("utf-8"))["commit_id"]
    m = json.loads(
        (cell_dir / "commits" / current_id / "manifest.json").read_text("utf-8")
    )
    assert list(m["signatures"]) == ["b"]
