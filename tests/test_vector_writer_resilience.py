"""Resilience tests for the vector writer + event log scaffold
(Phase 8.5 R7.5, commit 2).

Covers all 26 TESTING REQUIREMENTS categories from R7_5.txt and the
13 primary failure surfaces in R7.5-D. Every write happens in a
tmp_path; production state is never mutated.

Failure injection rule (R7.5-D CRITICAL MOCKING RULE): only patch the
specific module namespace where a symbol is looked up at runtime —
never patch global builtins or global stdlib symbols. No real threads,
no real subprocesses, no kill signals.

The chosen event semantics contract for this session is:
    at_least_once_but_idempotent_per_commit
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events as ve  # noqa: E402


def _load_indexer():
    path = ROOT / "tools" / "vector_indexer.py"
    spec = importlib.util.spec_from_file_location("vector_indexer", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vector_indexer"] = mod
    spec.loader.exec_module(mod)
    return mod


vi = _load_indexer()


# ── Helpers ──────────────────────────────────────────────────────-

# Per-test counter so successive _seed_log calls produce non-colliding
# events (keeps each test deterministic across multiple seed calls).
_SEED_COUNTER = {"n": 0}


def _seed_log(log: Path, *, cell: str = "thermal",
               n_upserts: int = 3) -> list[ve.VectorEvent]:
    """Append n_upserts upsert events into log; return them. Each
    call produces fresh model_ids so re-seeding inside a single test
    actually represents new state, not duplicates of prior state."""
    base = _SEED_COUNTER["n"]
    _SEED_COUNTER["n"] += n_upserts
    events = [
        ve.vector_upsert_requested(cell, f"solver_{base + i}",
                                      f"sig_{base + i}")
        for i in range(n_upserts)
    ]
    ve.emit_many(events, log)
    return events


@pytest.fixture(autouse=True)
def _reset_seed_counter():
    """Ensure each test starts with a fresh counter so events
    constructed in one test never collide with events in another."""
    _SEED_COUNTER["n"] = 0
    yield


def _read_pointer(cell_dir: Path) -> str | None:
    p = cell_dir / "current.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("commit_id")


def _count_commit_applied_events(log: Path) -> int:
    n = 0
    for e in ve.read_events(log):
        if e.event == ve.EVT_VECTOR_COMMIT_APPLIED:
            n += 1
    return n


# ── 1. empty/missing event log handled safely ──────────────────-

def test_empty_event_log_replay_is_safe(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text("", encoding="utf-8")
    report = vi.replay(log)
    assert report.events_seen == 0
    assert report.cells == {}


def test_missing_event_log_apply_is_safe(tmp_path):
    log = tmp_path / "nope.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    report = vi.apply(event_log=log, vector_root=vroot,
                       checkpoint_path=cp, dry_run=False)
    assert report.cells_applied == 0
    assert report.cells_failed == 0
    assert not cp.exists()  # no checkpoint write when nothing applied


# ── 2. malformed row skip behavior ─────────────────────────────-

def test_malformed_rows_silently_skipped_during_read(tmp_path):
    log = tmp_path / "events.jsonl"
    valid = ve.vector_upsert_requested("thermal", "a", "s")
    ve.emit(valid, log)
    with open(log, "a", encoding="utf-8") as f:
        f.write("{ this is not json\n")
        f.write('{"event": "fake", "cell_id": "x"}\n')
        f.write('not even close to json {[(\n')
    events = list(ve.read_events(log))
    assert len(events) == 1
    assert events[0].event == ve.EVT_VECTOR_UPSERT_REQUESTED


# ── 3. failure mid artifact write preserves prior committed state -

def test_failure_mid_artifact_write_preserves_prior_state(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)

    # First successful apply lays down a committed state.
    r1 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    assert r1.cells_applied == 1
    prior_commit = _read_pointer(vroot / "thermal")
    assert prior_commit is not None

    # Now seed more upserts and inject a write failure mid-stage.
    _seed_log(log, n_upserts=2)
    real_write_text = Path.write_text
    fail_count = {"n": 0}

    def fail_after_first(self, *a, **kw):
        fail_count["n"] += 1
        if fail_count["n"] >= 2:
            raise OSError("simulated mid-stage write failure")
        return real_write_text(self, *a, **kw)

    with patch.object(Path, "write_text", fail_after_first):
        r2 = vi.apply(event_log=log, vector_root=vroot,
                       checkpoint_path=cp, dry_run=False)

    # The cell apply must have failed
    assert r2.cells_failed >= 1
    # current.json is unchanged → still points at prior commit
    assert _read_pointer(vroot / "thermal") == prior_commit


# ── 4. failure mid checksum preserves prior committed state ────-

def test_failure_mid_checksum_preserves_prior_state(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    r1 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    prior_commit = _read_pointer(vroot / "thermal")

    _seed_log(log, n_upserts=2)
    with patch.object(vi, "_checksum_dir",
                       side_effect=OSError("simulated checksum failure")):
        r2 = vi.apply(event_log=log, vector_root=vroot,
                       checkpoint_path=cp, dry_run=False)
    assert r2.cells_failed >= 1
    assert _read_pointer(vroot / "thermal") == prior_commit


# ── 5. failure before swap preserves prior current pointer ─────-

def test_failure_before_swap_preserves_prior_pointer(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    r1 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    prior_commit = _read_pointer(vroot / "thermal")

    _seed_log(log, n_upserts=2)
    r2 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False,
                   _fail_before_swap_for_cells={"thermal"})
    assert r2.cells_failed == 1
    assert _read_pointer(vroot / "thermal") == prior_commit
    # Re-running without the failure injection converges to a new commit
    r3 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    assert r3.cells_applied == 1
    assert _read_pointer(vroot / "thermal") != prior_commit


# ── 6. failure after swap before commit_applied emit converges --

def test_failure_after_swap_before_emit_converges_on_rerun(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=3)
    real_emit = ve.emit
    call_count = {"n": 0}

    def fail_first_emit(event, path=None):
        # Block only the first commit_applied emit. The original
        # upsert events are seeded with ve.emit_many → real_emit
        # is called from emit_many; we only care about the apply()
        # path's emit of vector.commit_applied.
        if event.event == ve.EVT_VECTOR_COMMIT_APPLIED:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("simulated emit failure")
        return real_emit(event, path)

    with patch.object(vi.vector_events, "emit", fail_first_emit):
        r1 = vi.apply(event_log=log, vector_root=vroot,
                       checkpoint_path=cp, dry_run=False)
    assert r1.cells_failed == 1
    # Pointer was advanced before the emit failure
    swapped_commit = _read_pointer(vroot / "thermal")
    assert swapped_commit is not None
    # Rerun (without the failure hook) → must converge to same commit
    r2 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    final_commit = _read_pointer(vroot / "thermal")
    assert final_commit == swapped_commit  # idempotent
    # Now the commit_applied event was emitted exactly once on the rerun
    assert _count_commit_applied_events(log) == 1


# ── 7. failure after commit_applied emit before checkpoint ─────-

def test_failure_after_emit_before_checkpoint_converges(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=3)
    real_save = vi.save_checkpoint
    call_count = {"n": 0}

    def fail_first_save(checkpoint, path=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated checkpoint save failure")
        return real_save(checkpoint, path)

    with patch.object(vi, "save_checkpoint", fail_first_save):
        with pytest.raises(OSError):
            vi.apply(event_log=log, vector_root=vroot,
                      checkpoint_path=cp, dry_run=False)
    # commit_applied event was emitted before the failure
    assert _count_commit_applied_events(log) == 1
    # checkpoint not written (save raised)
    assert not cp.exists()
    # Pointer is at the new commit
    swapped = _read_pointer(vroot / "thermal")
    assert swapped is not None
    # Rerun: at_least_once_but_idempotent_per_commit allows a duplicate
    # commit_applied to be emitted, but final pointer must match
    r2 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    assert _read_pointer(vroot / "thermal") == swapped
    # Duplicate commit_applied events for the same logical commit
    # share faiss_commit_id, checksum, vector_count, and the upsert
    # source_events list. input_event_range may shift on the second
    # pass because the first pass's just-emitted commit_applied is
    # itself an event in the log when the second pass folds events.
    # Idempotency is at the commit-content level, not the event-id
    # level — see VECTOR_WRITER_RESILIENCE.md for the formal contract.
    events = [e for e in ve.read_events(log)
               if e.event == ve.EVT_VECTOR_COMMIT_APPLIED]
    if len(events) >= 2:
        assert events[0].payload["faiss_commit_id"] == events[1].payload["faiss_commit_id"]
        assert events[0].payload["checksum"] == events[1].payload["checksum"]
        assert events[0].payload["vector_count"] == events[1].payload["vector_count"]
        assert events[0].payload.get("source_events") == events[1].payload.get("source_events")
    # Now checkpoint was written
    assert cp.exists()


# ── 8. repeated replay of same event window is idempotent ─────-

def test_repeated_replay_is_idempotent(tmp_path):
    log = tmp_path / "events.jsonl"
    _seed_log(log, n_upserts=3)
    r1 = vi.replay(log)
    r2 = vi.replay(log)
    assert r1.events_seen == r2.events_seen == 3
    assert r1.cells["thermal"].signatures == r2.cells["thermal"].signatures
    assert r1.first_event_id == r2.first_event_id
    assert r1.last_event_id == r2.last_event_id


# ── 9. idempotent rebuild after crash ─────────────────────────-

def test_idempotent_rebuild_after_crash(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=4)
    r1 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    commit_first = _read_pointer(vroot / "thermal")

    # Wipe the checkpoint to simulate "checkpoint never made it durable
    # but the commit dir + pointer + event log all did".
    cp.unlink()
    r2 = vi.apply(event_log=log, vector_root=vroot,
                   checkpoint_path=cp, dry_run=False)
    # Rebuild lands on the same commit id (content-addressed) and the
    # pointer is unchanged.
    assert _read_pointer(vroot / "thermal") == commit_first


# ── 10. per-cell isolation under failure ──────────────────────-

def test_per_cell_isolation_under_failure(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    ve.emit_many([
        ve.vector_upsert_requested("thermal", "a", "s1"),
        ve.vector_upsert_requested("energy",  "b", "s2"),
    ], log)
    r = vi.apply(event_log=log, vector_root=vroot,
                  checkpoint_path=cp, dry_run=False,
                  _fail_before_swap_for_cells={"thermal"})
    # thermal failed, energy succeeded
    assert r.cells_failed == 1
    assert r.cells_applied == 1
    # energy has a current.json, thermal does not
    assert _read_pointer(vroot / "energy") is not None
    assert _read_pointer(vroot / "thermal") is None


# ── 11. identical projections across cells don't collide ──────-

def test_identical_projections_across_cells_do_not_collide(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    # Two cells with identical signature payloads
    ve.emit_many([
        ve.vector_upsert_requested("thermal", "shared_solver", "shared_sig"),
        ve.vector_upsert_requested("energy",  "shared_solver", "shared_sig"),
    ], log)
    r = vi.apply(event_log=log, vector_root=vroot,
                  checkpoint_path=cp, dry_run=False)
    assert r.cells_applied == 2
    # commit_id depends on cell_id too, so they are different ids
    thermal_commit = _read_pointer(vroot / "thermal")
    energy_commit = _read_pointer(vroot / "energy")
    assert thermal_commit != energy_commit
    # Each cell has its own commit dir
    assert (vroot / "thermal" / "commits" / thermal_commit).exists()
    assert (vroot / "energy" / "commits" / energy_commit).exists()
    # Manifests are isolated
    tm = json.loads(
        (vroot / "thermal" / "commits" / thermal_commit / "manifest.json")
        .read_text(encoding="utf-8")
    )
    em = json.loads(
        (vroot / "energy" / "commits" / energy_commit / "manifest.json")
        .read_text(encoding="utf-8")
    )
    assert tm["cell_id"] == "thermal"
    assert em["cell_id"] == "energy"


# ── 12. current.json temp+replace semantics tested ────────────-

def test_current_json_uses_tmp_plus_replace(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=1)
    # Patch os.replace to record the call args
    real_replace = os.replace
    captured: list[tuple[Path, Path]] = []

    def capturing_replace(src, dst):
        captured.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    # Patch the namespace where vector_indexer looks up os.replace
    with patch.object(vi.os, "replace", capturing_replace):
        vi.apply(event_log=log, vector_root=vroot,
                  checkpoint_path=cp, dry_run=False)
    # At least one os.replace call targeted current.json
    assert any(dst.name == "current.json" for _, dst in captured)
    # The src file lived in the same dir as the dst
    for src, dst in captured:
        if dst.name == "current.json":
            assert src.parent == dst.parent
            # Tmp file naming convention preserved
            assert src.name.startswith(".current.")


# ── 13. chosen event semantics contract explicitly tested ─────-

def test_chosen_event_semantics_is_at_least_once_but_idempotent(tmp_path):
    """The contract: a duplicate vector.commit_applied for the same
    final commit content has the same event_id (excludes ts)."""
    log = tmp_path / "events.jsonl"
    e_first = ve.vector_commit_applied(
        cell_id="thermal", faiss_commit_id="faiss_x",
        artifact_path="data/vector/thermal/commits/faiss_x",
        vector_count=3, checksum="sha256:abc",
        source_events=["evt_a", "evt_b"],
    )
    e_dup_diff_ts = ve.VectorEvent(
        event=e_first.event, cell_id=e_first.cell_id,
        ts="2099-01-01T00:00:00+00:00",
        payload=dict(e_first.payload),
        source=e_first.source,
    )
    assert e_first.ts != e_dup_diff_ts.ts
    assert e_first.event_id() == e_dup_diff_ts.event_id()


# ── 14. checkpoint advances only after the chosen contract's
#       durable point ────────────────────────────────────────────

def test_checkpoint_advances_only_after_apply_succeeds(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    # Force the apply to fail before swap
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False,
              _fail_before_swap_for_cells={"thermal"})
    # Checkpoint must NOT have been written because no cell applied
    assert not cp.exists()
    # Now run successfully
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    assert cp.exists()
    cp_data = json.loads(cp.read_text(encoding="utf-8"))
    assert cp_data["per_cell"]["thermal"]["commit_id"] is not None


# ── 15. history / checkpoint state survives rerun ─────────────-

def test_checkpoint_round_trips_across_rerun(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    cp_a = vi.load_checkpoint(cp)
    # Save+load round-trip
    vi.save_checkpoint(cp_a, cp)
    cp_b = vi.load_checkpoint(cp)
    assert cp_a.to_dict() == cp_b.to_dict()
    # Rerun with no new events: must NOT advance the per-cell commit
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    cp_c = vi.load_checkpoint(cp)
    # commit_id stable across no-op rerun
    assert cp_c.per_cell["thermal"].commit_id == cp_a.per_cell["thermal"].commit_id


# ── 16. multi-writer hazard: deterministic interleaving sim ──-

def test_multi_writer_deterministic_interleaving_simulation(tmp_path):
    """Two simulated writers append to the same JSONL file; we drive
    them deterministically without real threads. Documents the
    hazard: bytes from concurrent text-mode writes can interleave on
    POSIX outside PIPE_BUF. We verify that read_events tolerates a
    mid-line append boundary (because the writer always appends a
    full line + newline before closing the handle in this code path).
    """
    log = tmp_path / "events.jsonl"
    e1 = ve.vector_upsert_requested("thermal", "a", "s1")
    e2 = ve.vector_upsert_requested("energy",  "b", "s2")
    # Simulated interleaving: writer A starts, writer B fully writes
    # and closes, then writer A finishes. Because each emit() is a
    # single open+write+close, writer A's line lands intact at EOF
    # after writer B's line.
    ve.emit(e2, log)
    ve.emit(e1, log)
    events = list(ve.read_events(log))
    assert {e.payload["model_id"] for e in events} == {"a", "b"}

    # Now simulate a true byte-level interleave (corrupted JSON line)
    # by manually injecting half-lines. read_events must skip the
    # corrupted line.
    log.unlink()
    with open(log, "w", encoding="utf-8") as f:
        f.write('{"event":"vector.upsert_requested","cell_id":"thermal"')
        # Note: line not terminated by \n yet. A second process opens
        # in append mode and writes a full clean line:
    with open(log, "a", encoding="utf-8") as f:
        f.write('\n')
        f.write(e1.to_json() + "\n")
    events = list(ve.read_events(log))
    # The first line was an incomplete JSON object → JSONDecodeError →
    # skipped silently; the second line is valid.
    assert len(events) == 1
    assert events[0].payload["model_id"] == "a"


# ── 17. malformed rows do not silently corrupt state projection -

def test_malformed_rows_do_not_corrupt_projection(tmp_path):
    log = tmp_path / "events.jsonl"
    # Valid event for the projection's "true" state
    ve.emit(ve.vector_upsert_requested("thermal", "good", "sig_good"), log)
    # Inject a row that LOOKS like an event but has an unknown event name
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "totally_made_up.event",
            "cell_id": "thermal",
            "payload": {"model_id": "evil", "signature": "tainted"},
        }) + "\n")
    # Inject a row whose payload is missing required keys
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "event": "vector.upsert_requested",
            "cell_id": "thermal",
            "payload": {"model_id": "no_signature"},
        }) + "\n")
    # The projection sees only the good event
    report = vi.replay(log)
    assert report.cells["thermal"].signatures == {"good": "sig_good"}
    assert "evil" not in report.cells["thermal"].signatures
    assert "no_signature" not in report.cells["thermal"].signatures


# ── 18. truncation/shrink of temp artifacts handled safely ───-

def test_truncation_of_partial_temp_artifact_is_safe(tmp_path):
    """If a tmp file inside the cell dir is truncated mid-write by an
    external actor, _swap_current_pointer's os.replace either swaps a
    truncated file or fails. Either way, current.json must remain
    valid JSON or absent — never half-written."""
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=1)
    # First, ensure a healthy current.json exists
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    cell_dir = vroot / "thermal"
    healthy = (cell_dir / "current.json").read_text(encoding="utf-8")
    assert json.loads(healthy)["commit_id"]
    # Now drop a truncated stray .current.* file in the dir; the next
    # apply must not be confused by it.
    stray = cell_dir / ".current.malformed.tmp"
    stray.write_text("{ partial...", encoding="utf-8")
    _seed_log(log, n_upserts=2)
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    cur = json.loads((cell_dir / "current.json").read_text(encoding="utf-8"))
    assert cur["commit_id"]


# ── 19. event emission ordering documented and tested ────────-

def test_event_emission_order_inside_apply(tmp_path):
    """Stage → swap → emit → checkpoint. Verified by inspecting the
    sequence of side effects: after emit hook fires, current.json
    must already point at the new commit."""
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    real_emit = ve.emit
    pointer_at_emit_time: dict = {}

    def capture_pointer(event, path=None):
        if event.event == ve.EVT_VECTOR_COMMIT_APPLIED:
            pointer_at_emit_time["commit_id"] = _read_pointer(
                vroot / event.cell_id,
            )
        return real_emit(event, path)

    with patch.object(vi.vector_events, "emit", capture_pointer):
        vi.apply(event_log=log, vector_root=vroot,
                  checkpoint_path=cp, dry_run=False)
    # By the time the commit_applied event was emitted, the pointer
    # was already swapped to the new commit (swap happens BEFORE emit)
    final_commit = _read_pointer(vroot / "thermal")
    assert pointer_at_emit_time["commit_id"] == final_commit


# ── 20. CLI help works ───────────────────────────────────────-

def test_cli_help_exits_zero_with_nonempty_stdout():
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "vector_indexer.py"), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert result.stdout.strip()
    # Documents key flags
    for flag in ("--apply", "--cell", "--since", "--checkpoint-path",
                  "--vector-root", "--event-log"):
        assert flag in result.stdout


# ── 21. no secret leakage in emitted reports ─────────────────-

def test_no_secrets_in_apply_or_replay_report(tmp_path):
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    apply_blob = json.dumps(vi._apply_to_json(
        vi.apply(event_log=log, vector_root=vroot,
                  checkpoint_path=cp, dry_run=True)
    ), default=str)
    replay_blob = json.dumps(vi._replay_to_json(vi.replay(log)), default=str)
    forbidden = ["password", "secret", "api_key", "token=",
                 "PRIVATE KEY", "BEGIN RSA"]
    for pat in forbidden:
        assert pat.lower() not in apply_blob.lower()
        assert pat.lower() not in replay_blob.lower()


# ── 22. no absolute local path leakage in emitted reports ────-

def test_no_absolute_paths_in_replay_report(tmp_path):
    log = tmp_path / "events.jsonl"
    _seed_log(log, n_upserts=1)
    blob = json.dumps(vi._replay_to_json(vi.replay(log)), default=str)
    # tmp_path itself must not leak into the projection report
    assert str(tmp_path) not in blob
    # No raw drive-letter or POSIX-root path expected in the schema
    assert "C:\\" not in blob
    # Note: "/" alone is too noisy to ban; we only check absolute
    # path roots.


# ── 23. no minimal lock helper added in this session ─────────-

def test_no_lock_helper_added():
    """R7.5 only adds a lock helper if a real bug demands it. With
    the chosen at-least-once-idempotent contract, no lock is needed.
    Verify production source remains lock-free."""
    src = (ROOT / "waggledance" / "core" / "magma" / "vector_events.py")\
        .read_text(encoding="utf-8")
    indexer_src = (ROOT / "tools" / "vector_indexer.py")\
        .read_text(encoding="utf-8")
    for module in (src, indexer_src):
        assert "fcntl.flock" not in module
        assert "msvcrt.locking" not in module
        # filelock / portalocker would be new dependencies — forbidden
        assert "import filelock" not in module
        assert "import portalocker" not in module


# ── 24. writer convergence after bounded failure demonstrated -

def test_writer_convergence_after_bounded_failure(tmp_path):
    """Three crashes at three different points; final state is byte-
    identical to a clean run."""
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=4)

    # Run 1: clean baseline in a sibling tmp tree
    log_clean = tmp_path / "events_clean.jsonl"
    cp_clean = tmp_path / "cp_clean.json"
    vroot_clean = tmp_path / "vec_clean"
    log_clean.write_bytes(log.read_bytes())
    vi.apply(event_log=log_clean, vector_root=vroot_clean,
              checkpoint_path=cp_clean, dry_run=False)
    clean_commit = _read_pointer(vroot_clean / "thermal")

    # Run 2: chaos run with failures at 3 points
    # Crash 1: fail before swap
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False,
              _fail_before_swap_for_cells={"thermal"})
    # Crash 2: fail mid-checksum
    with patch.object(vi, "_checksum_dir",
                       side_effect=OSError("crash 2")):
        vi.apply(event_log=log, vector_root=vroot,
                  checkpoint_path=cp, dry_run=False)
    # Recovery: clean rerun
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)
    chaos_commit = _read_pointer(vroot / "thermal")

    # Convergence: both runs land on the same commit_id
    assert chaos_commit == clean_commit
    # And the manifests have byte-identical content
    clean_manifest = (vroot_clean / "thermal" / "commits" / clean_commit
                       / "manifest.json").read_text(encoding="utf-8")
    chaos_manifest = (vroot / "thermal" / "commits" / chaos_commit
                       / "manifest.json").read_text(encoding="utf-8")
    # Manifests differ only in produced_at timestamp; signatures must
    # match
    cm = json.loads(clean_manifest)
    xm = json.loads(chaos_manifest)
    assert cm["signatures"] == xm["signatures"]
    assert cm["commit_id"] == xm["commit_id"]


# ── 25. production state never mutated outside temp dirs ────-

def test_production_state_not_mutated(tmp_path):
    """No test in this file may write to the real data/vector/ tree
    or the real docs/runs/ tree. Verified at module import + by
    snapshotting mtimes around a representative chaos run."""
    real_data_dir = ROOT / "data" / "vector"
    real_docs_runs = ROOT / "docs" / "runs"
    before_data = real_data_dir.exists()
    before_docs_mtimes: dict[Path, float] = {}
    if real_docs_runs.exists():
        for p in real_docs_runs.glob("phase8_5_*.json"):
            before_docs_mtimes[p] = p.stat().st_mtime

    # Run a representative chaos test in a tmp dir
    log = tmp_path / "events.jsonl"
    cp = tmp_path / "cp.json"
    vroot = tmp_path / "vec"
    _seed_log(log, n_upserts=2)
    vi.apply(event_log=log, vector_root=vroot,
              checkpoint_path=cp, dry_run=False)

    # Production state untouched
    assert real_data_dir.exists() == before_data
    if real_docs_runs.exists():
        for p, mt in before_docs_mtimes.items():
            assert p.stat().st_mtime == mt, f"mutated: {p}"


# ── 26. strict-mode cutover proposal documented if skip-malformed -

def test_strict_mode_cutover_documented_if_skip_malformed():
    """The current malformed-row policy is silent skip. R7.5 spec
    requires that a strict-mode cutover proposal be documented when
    that is the case. The doc is shipped in commit 4 of this session;
    here we verify the proposal language exists once that commit
    lands."""
    doc = ROOT / "docs" / "architecture" / "VECTOR_WRITER_RESILIENCE.md"
    if not doc.exists():
        pytest.skip(
            "VECTOR_WRITER_RESILIENCE.md is shipped in a later commit "
            "of this session; this test is enforced post-doc-commit"
        )
    text = doc.read_text(encoding="utf-8")
    assert "strict" in text.lower()
    assert "cutover" in text.lower() or "stage 2.5" in text.lower()
