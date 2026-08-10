"""Stage-2 indexer tests — replay/apply/checkpoint/idempotency.

Covers the R6 §6 checklist. Companion to test_vector_indexer.py
(Stage-1 projection tests) which stays green unchanged.
"""
from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from waggledance.core.magma import vector_events, vector_projection
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt


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


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _projected_event(
    model_id: str,
    *,
    cell_id: str = "thermal",
    revision: str = "v1",
    embedding_version: str = "1",
    dimension: int = 8,
    topology_digest: str | None = None,
    receipt_bound: bool = False,
):
    if topology_digest is None:
        topology_digest = vector_projection.retrieval_topology_digest(
            vector_projection.build_retrieval_topology_contract()
        )
    axiom = {
        "model_id": model_id,
        "cell_id": cell_id,
        "model_name": f"{model_id} {revision}",
        "description": f"validated {revision} solver contract",
        "variables": {"x": {"unit": "m"}},
        "solver_output_schema": {"primary": {"name": "result", "unit": "m"}},
        "formulas": [{"name": "main", "output_unit": "m", "expression": "x"}],
        "capabilities": ["deterministic"],
        "tags": [revision],
    }
    document = vector_projection.build_solver_contract_projection(
        axiom,
        source_digest=_digest(f"source:{model_id}:{revision}"),
        topology_digest=topology_digest,
    )
    embedding = vector_projection.build_embedding_contract(
        model_id="test-embedding",
        model_version=embedding_version,
        dimension=dimension,
    )
    identity = (
        _receipt_bound_identity(document, embedding)
        if receipt_bound
        else vector_projection.build_projection_source_identity(document)
    )
    event = vector_events.vector_upsert_requested(
        cell_id,
        model_id,
        document["solver_contract_digest"],
        projection_document=document,
        source_identity=identity,
        embedding_contract=embedding,
        topology_digest=topology_digest,
        source="test",
    )
    return event, document, identity, embedding, topology_digest


def _receipt_bound_identity(document, embedding):
    payload = vector_projection.build_projection_admission_payload(
        document, embedding
    )
    evaluation = build_evaluation_result(
        case_id=(
            "case:indexer_projection:" + document["canonical_solver_id"]
        ),
        subject_type="solver",
        target_payload=payload,
        risk_class="local_artifact",
        expected_gate="allow",
        actual_gate="allow",
        verifier_path=[vector_projection.PROJECTION_ADMISSION_EVALUATOR_VERSION],
        solver_selection=[document["canonical_solver_id"]],
        policy_version="policy:magma_faiss_projection:v1",
        charter_version="charter:candidate_projection_only:v1",
        domain_threshold_version="threshold:projection_contract:v1",
        verdict="pass",
        reason_codes=["magma:faiss:projection_contract_valid"],
        confidence_score=1.0,
        uncertainty_sources=[
            {
                "kind": "limited_evidence",
                "detail": "Projection binding is not solver outcome evidence.",
            }
        ],
    )
    receipt = build_magma_receipt(
        event_id=vector_projection.projection_admission_event_id(
            document, embedding
        ),
        ts_utc="2026-08-10T06:00:00Z",
        risk_class="local_artifact",
        payload=payload,
        evaluation_result=evaluation,
        policy_digest=_digest("projection-policy"),
        charter_digest=_digest("candidate-charter"),
        rco_decision_digest=_digest("local-rco-record"),
        world_snapshot_digest=_digest("local-world-snapshot"),
        solver_contract_digest=document["solver_contract_digest"],
        payload_visibility="full_payload",
    )
    proof = vector_projection.build_projection_receipt_proof(
        document,
        embedding,
        evaluation_result=evaluation,
        receipt=receipt,
    )
    return vector_projection.build_receipt_bound_projection_source_identity(
        document, embedding, proof
    )


def _cell_from_projected_events(*events):
    cell = mod.CellState(cell_id=events[0].cell_id)
    for event in events:
        mod._apply_event_to_state(cell, event, event.event_id())
    return cell


def _write_projection_payload(vector_root, cell, *, manifest_schema):
    payload = mod._projection_artifact_payload(
        cell,
        manifest_schema=manifest_schema,
    )
    commit_id, documents, _manifest, manifest, _commit, commit = payload
    commit_dir = vector_root / cell.cell_id / "commits" / commit_id
    commit_dir.mkdir(parents=True)
    (commit_dir / "documents.jsonl").write_bytes(documents)
    (commit_dir / "manifest.json").write_bytes(manifest)
    (commit_dir / "commit.json").write_bytes(commit)
    return commit_id, commit_dir


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


def test_missing_per_cell_checkpoint_anchor_fails_without_pointer_advance(workspace):
    first_event = _projected_event("a")
    _emit(workspace, first_event[0])
    first = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert first.cells_applied == 1
    pointer_path = workspace["vector_root"] / "thermal" / "current.json"
    original_pointer = pointer_path.read_bytes()

    second_event = _projected_event("b")
    _emit(workspace, second_event[0])
    checkpoint = json.loads(workspace["checkpoint"].read_text("utf-8"))
    checkpoint["per_cell"]["thermal"]["last_applied_event_id"] = (
        "evt_0000000000000000"
    )
    workspace["checkpoint"].write_text(
        json.dumps(checkpoint, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tampered_checkpoint = workspace["checkpoint"].read_bytes()

    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )

    assert report.cells_failed == 1
    assert "anchor was not found" in report.cell_results["thermal"].error
    assert pointer_path.read_bytes() == original_pointer
    assert workspace["checkpoint"].read_bytes() == tampered_checkpoint
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=first_event[3],
        expected_topology_digest=first_event[4],
    )
    assert [
        row["projection_document"]["canonical_solver_id"]
        for row in loaded["documents"]
    ] == ["a"]


def test_missing_explicit_since_anchor_fails_before_any_write(workspace):
    _emit(workspace, vector_events.vector_upsert_requested("thermal", "a", "s"))

    with pytest.raises(ValueError, match="requested event anchor was not found"):
        mod.apply(
            event_log=workspace["event_log"],
            vector_root=workspace["vector_root"],
            checkpoint_path=workspace["checkpoint"],
            since_event_id="evt_0000000000000000",
            dry_run=False,
        )

    assert not workspace["vector_root"].exists()
    assert not workspace["checkpoint"].exists()


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


def test_identical_projected_upsert_reactivates_after_delete(workspace):
    projected = _projected_event("heat")
    _emit(workspace, projected[0])
    first = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert first.cells_applied == 1

    _emit(workspace, vector_events.vector_delete_requested("thermal", "heat"))
    deleted = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert deleted.cells_applied == 1
    assert deleted.cell_results["thermal"].vector_count == 0

    _emit(workspace, projected[0])
    restored = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert restored.cells_applied == 1
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=projected[3],
        expected_topology_digest=projected[4],
    )
    assert loaded["manifest"]["document_count"] == 1
    assert loaded["documents"][0]["projection_document"] == projected[1]


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


def test_legacy_writer_rejects_preplanted_hardlink_without_overwrite(
    workspace,
    tmp_path,
):
    event = vector_events.vector_upsert_requested("thermal", "a", "s")
    _emit(workspace, event)
    expected_commit = mod.compute_commit_id(
        mod.CellState(cell_id="thermal", signatures={"a": "s"})
    )
    commit_dir = (
        workspace["vector_root"] / "thermal" / "commits" / expected_commit
    )
    commit_dir.mkdir(parents=True)
    outside = tmp_path / "outside-sentinel.json"
    sentinel = b"outside must remain unchanged\n"
    outside.write_bytes(sentinel)
    try:
        os.link(outside, commit_dir / "manifest.json")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")

    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )

    assert report.cells_failed == 1
    assert outside.read_bytes() == sentinel
    assert not (workspace["vector_root"] / "thermal" / "current.json").exists()
    assert not workspace["checkpoint"].exists()
    assert list((workspace["vector_root"] / "thermal" / "commits").glob(".stage-*")) == []


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


# ── Strict projection-only materialization ───────────────────────

def test_projected_apply_is_truthful_and_strictly_readable(workspace):
    event, document, _identity, embedding, topology_digest = _projected_event("heat")
    _emit(workspace, event)

    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )

    assert report.cells_applied == 1
    pointer_path = workspace["vector_root"] / "thermal" / "current.json"
    pointer = json.loads(pointer_path.read_text("utf-8"))
    assert pointer["schema_version"] == mod.PROJECTION_POINTER_SCHEMA
    assert pointer["commit_id"].startswith("proj_")
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=embedding,
        expected_topology_digest=topology_digest,
    )
    assert loaded["manifest"]["materialization_state"] == "projection_only"
    assert loaded["manifest"]["index_kind"] == "none"
    assert loaded["documents"][0]["projection_document"] == document

    commits = [
        item
        for item in vector_events.read_events(workspace["event_log"], strict=True)
        if item.event == vector_events.EVT_VECTOR_COMMIT_APPLIED
    ]
    assert commits[-1].payload["materialization_state"] == "projection_only"
    assert commits[-1].payload["index_kind"] == "none"


def test_manifest_v2_mixed_v1_v2_round_trip(workspace):
    v1 = _projected_event("legacy")
    v2 = _projected_event("receipted", receipt_bound=True)
    _emit_many(workspace, [v1[0], v2[0]])

    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert report.cells_applied == 1
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=v2[3],
        expected_topology_digest=v2[4],
    )

    assert loaded["manifest"]["schema_version"] == mod.PROJECTION_MANIFEST_V2_SCHEMA
    assert loaded["manifest"]["source_identity_schema_versions"] == sorted(
        [
            vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION,
            vector_projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION,
        ]
    )
    assert loaded["receipt_bound_count"] == 1
    assert loaded["unreceipted_count"] == 1
    assert loaded["receipt_structure_reverified"] is True
    assert loaded["receipt_authenticity_verified"] is False
    assert loaded["solver_outcome_verified"] is False
    assert loaded["runtime_authority_granted"] is False


def test_manifest_v1_backward_load_is_byte_stable(workspace):
    projected = _projected_event("legacy")
    cell = _cell_from_projected_events(projected[0])
    commit_id, commit_dir = _write_projection_payload(
        workspace["vector_root"],
        cell,
        manifest_schema=mod.PROJECTION_MANIFEST_V1_SCHEMA,
    )

    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=projected[3],
        expected_topology_digest=projected[4],
        commit_id=commit_id,
    )

    assert loaded["manifest"]["schema_version"] == mod.PROJECTION_MANIFEST_V1_SCHEMA
    assert loaded["manifest"]["source_identity_schema_version"] == (
        vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION
    )
    regenerated = mod._projection_artifact_payload(
        cell,
        manifest_schema=mod.PROJECTION_MANIFEST_V1_SCHEMA,
    )
    assert commit_id == (
        "proj_dffe673a3173fb113325f08652ae9b9913bc236ab351d71d106af24e7cb70c97"
    )
    assert (len(regenerated[1]), hashlib.sha256(regenerated[1]).hexdigest()) == (
        1613,
        "e4a93c71f8600c948fdf776d4cb695629ccd51148d37bc6f9b82422d62c335ee",
    )
    assert (len(regenerated[3]), hashlib.sha256(regenerated[3]).hexdigest()) == (
        1167,
        "dc76e1020c376153f3a934099b4db4b0665f01e83d8aca7312d79a92b08adb7b",
    )
    assert (len(regenerated[5]), hashlib.sha256(regenerated[5]).hexdigest()) == (
        430,
        "adfe8d812bfb00c02849fb20da6e95ed3e4117bb78daea7401e825b6685d6e4b",
    )
    assert (commit_dir / "documents.jsonl").read_bytes() == regenerated[1]
    assert (commit_dir / "manifest.json").read_bytes() == regenerated[3]
    assert (commit_dir / "commit.json").read_bytes() == regenerated[5]


def test_manifest_v1_cannot_label_receipt_bound_rows(workspace):
    projected = _projected_event("receipted", receipt_bound=True)
    cell = _cell_from_projected_events(projected[0])

    with pytest.raises(ValueError, match="v1 cannot encode receipt-bound"):
        mod._projection_artifact_payload(
            cell,
            manifest_schema=mod.PROJECTION_MANIFEST_V1_SCHEMA,
        )


def test_loader_independently_rejects_cross_bound_v2_receipt(
    workspace, monkeypatch
):
    _event, document, _identity, embedding, topology = _projected_event("heat")
    other_embedding = vector_projection.build_embedding_contract(
        model_id=embedding["model_id"],
        model_version="other-generation",
        dimension=embedding["dimension"],
    )
    wrong_identity = _receipt_bound_identity(document, other_embedding)
    cell = mod.CellState(
        cell_id="thermal",
        signatures={"heat": document["solver_contract_digest"]},
        projection_mode=True,
        projection_documents={"heat": document},
        projection_source_identities={"heat": wrong_identity},
        projection_embedding_contracts={"heat": embedding},
        projection_topology_digests={"heat": topology},
        embedding_contract=embedding,
        topology_digest=topology,
    )
    dedup_key = mod._projected_upsert_dedup_key(
        wrong_identity, embedding, topology
    )
    cell.seen_source_identities[dedup_key] = mod._projected_upsert_fingerprint(
        document, embedding, topology
    )

    real_binding_validator = vector_projection.validate_projection_source_binding
    monkeypatch.setattr(
        vector_projection,
        "validate_projection_source_binding",
        lambda _document, identity, _embedding: (
            vector_projection.validate_projection_source_identity(identity)
        ),
    )
    commit_id, _commit_dir = _write_projection_payload(
        workspace["vector_root"],
        cell,
        manifest_schema=mod.PROJECTION_MANIFEST_V2_SCHEMA,
    )
    monkeypatch.setattr(
        vector_projection,
        "validate_projection_source_binding",
        real_binding_validator,
    )

    with pytest.raises(ValueError, match="does not match projection contracts"):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=embedding,
            expected_topology_digest=topology,
            commit_id=commit_id,
        )


def test_incremental_v1_commit_rehydrates_into_mixed_v2_manifest(workspace):
    legacy = _projected_event("legacy")
    first_event_id = _emit(workspace, legacy[0])
    legacy_cell = _cell_from_projected_events(legacy[0])
    legacy_commit_id, _commit_dir = _write_projection_payload(
        workspace["vector_root"],
        legacy_cell,
        manifest_schema=mod.PROJECTION_MANIFEST_V1_SCHEMA,
    )
    mod._swap_projection_pointer(
        workspace["vector_root"] / "thermal",
        legacy_commit_id,
        None,
    )
    checkpoint = mod.Checkpoint(
        global_last_applied_event_id=first_event_id,
        per_cell={
            "thermal": mod.PerCellCheckpoint(
                last_applied_event_id=first_event_id,
                commit_id=legacy_commit_id,
                applied_ts="2026-08-10T06:00:00+00:00",
                vector_count=1,
            )
        },
    )
    mod.save_checkpoint(checkpoint, workspace["checkpoint"])

    receipted = _projected_event("receipted", receipt_bound=True)
    _emit(workspace, receipted[0])
    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert report.cells_applied == 1
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=receipted[3],
        expected_topology_digest=receipted[4],
    )
    assert loaded["manifest"]["schema_version"] == mod.PROJECTION_MANIFEST_V2_SCHEMA
    assert loaded["manifest"]["source_identity_schema_versions"] == sorted(
        [
            vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION,
            vector_projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION,
        ]
    )


@pytest.mark.parametrize(
    ("declared", "error"),
    [
        (
            [
                vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION,
                vector_projection.PROJECTION_SOURCE_IDENTITY_V1_VERSION,
            ],
            "schemas are invalid",
        ),
        (["magma.faiss.source_identity.v999"], "schemas are invalid"),
        ([{}], "schemas are invalid"),
        ([1], "schemas are invalid"),
        (
            [vector_projection.PROJECTION_SOURCE_IDENTITY_V2_VERSION],
            "schema manifest mismatch",
        ),
    ],
)
def test_manifest_v2_rejects_invalid_or_false_identity_version_lists(
    workspace, declared, error
):
    projected = _projected_event("legacy")
    _emit(workspace, projected[0])
    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    commit_id = report.cell_results["thermal"].commit_id
    manifest_path = (
        workspace["vector_root"]
        / "thermal"
        / "commits"
        / commit_id
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["source_identity_schema_versions"] = declared
    manifest_path.write_bytes(mod._canonical_json_line(manifest))

    with pytest.raises(ValueError, match=error):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=projected[3],
            expected_topology_digest=projected[4],
            commit_id=commit_id,
        )


def test_projected_artifacts_are_byte_deterministic(tmp_path):
    def materialize(root: Path):
        ws = {
            "event_log": root / "events.jsonl",
            "vector_root": root / "vector",
            "checkpoint": root / "checkpoint.json",
        }
        event, _document, _identity, _embedding, _topology = _projected_event("heat")
        _emit(ws, event)
        result = mod.apply(
            event_log=ws["event_log"],
            vector_root=ws["vector_root"],
            checkpoint_path=ws["checkpoint"],
            dry_run=False,
        )
        commit_id = result.cell_results["thermal"].commit_id
        commit_dir = ws["vector_root"] / "thermal" / "commits" / commit_id
        return {
            "documents": (commit_dir / "documents.jsonl").read_bytes(),
            "manifest": (commit_dir / "manifest.json").read_bytes(),
            "commit": (commit_dir / "commit.json").read_bytes(),
            "pointer": (ws["vector_root"] / "thermal" / "current.json").read_bytes(),
        }

    assert materialize(tmp_path / "a") == materialize(tmp_path / "b")


def test_strict_apply_rejects_truncated_event_before_writes(workspace):
    event, *_ = _projected_event("heat")
    _emit(workspace, event)
    with open(workspace["event_log"], "a", encoding="utf-8") as stream:
        stream.write('{"event":"vector.upsert_requested"')

    with pytest.raises(ValueError, match="invalid vector event JSON"):
        mod.apply(
            event_log=workspace["event_log"],
            vector_root=workspace["vector_root"],
            checkpoint_path=workspace["checkpoint"],
            dry_run=False,
        )
    assert not (workspace["vector_root"] / "thermal" / "current.json").exists()
    assert not workspace["checkpoint"].exists()


def test_incomplete_legacy_migration_does_not_swap(workspace):
    _emit_many(
        workspace,
        [
            vector_events.vector_upsert_requested("thermal", "a", "legacy-a"),
            vector_events.vector_upsert_requested("thermal", "b", "legacy-b"),
        ],
    )
    first = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    legacy_commit = first.cell_results["thermal"].commit_id

    event_a, *_ = _projected_event("a")
    _emit(workspace, event_a)
    partial = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert partial.cells_failed == 1
    pointer_path = workspace["vector_root"] / "thermal" / "current.json"
    assert json.loads(pointer_path.read_text("utf-8"))["commit_id"] == legacy_commit

    event_b, _doc_b, _id_b, embedding, topology_digest = _projected_event("b")
    _emit(workspace, event_b)
    complete = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert complete.cells_applied == 1
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=embedding,
        expected_topology_digest=topology_digest,
    )
    assert loaded["pointer"]["previous_commit_id"] == legacy_commit
    assert loaded["manifest"]["document_count"] == 2


@pytest.mark.parametrize("migration", ["embedding", "topology"])
def test_projection_generation_migrates_only_when_complete(workspace, migration):
    initial = [_projected_event("a"), _projected_event("b")]
    _emit_many(workspace, [item[0] for item in initial])
    first = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    first_commit = first.cell_results["thermal"].commit_id

    if migration == "embedding":
        changed = [
            _projected_event("a", embedding_version="2", dimension=16),
            _projected_event("b", embedding_version="2", dimension=16),
        ]
    else:
        new_topology = _digest("topology-v2")
        changed = [
            _projected_event("a", topology_digest=new_topology),
            _projected_event("b", topology_digest=new_topology),
        ]

    _emit(workspace, changed[0][0])
    partial = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert partial.cells_failed == 1
    pointer_path = workspace["vector_root"] / "thermal" / "current.json"
    assert json.loads(pointer_path.read_text("utf-8"))["commit_id"] == first_commit

    _emit(workspace, changed[1][0])
    complete = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert complete.cells_applied == 1
    assert complete.cell_results["thermal"].commit_id != first_commit
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=changed[1][3],
        expected_topology_digest=changed[1][4],
    )
    assert loaded["manifest"]["document_count"] == 2


def test_historical_identity_dedup_is_restart_stable(workspace):
    v1 = _projected_event("heat", revision="v1")
    v2 = _projected_event("heat", revision="v2")
    _emit_many(workspace, [v1[0], v2[0]])
    first = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    first_commit = first.cell_results["thermal"].commit_id

    _emit(workspace, v1[0])
    replayed = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert replayed.cells_skipped_no_change == 1
    assert replayed.cell_results["thermal"].commit_id == first_commit
    loaded = mod.load_verified_projection_commit(
        workspace["vector_root"],
        "thermal",
        expected_embedding_contract=v2[3],
        expected_topology_digest=v2[4],
    )
    assert loaded["documents"][0]["projection_document"] == v2[1]
    assert len(loaded["manifest"]["processed_source_identities"]) == 2


def test_projection_reader_rejects_tamper_extra_files_and_noncanonical_pointer(
    workspace,
):
    event, _document, _identity, embedding, topology_digest = _projected_event("heat")
    _emit(workspace, event)
    applied = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    commit_id = applied.cell_results["thermal"].commit_id
    commit_dir = workspace["vector_root"] / "thermal" / "commits" / commit_id
    (commit_dir / "unexpected.bin").write_bytes(b"not allowed")
    with pytest.raises(ValueError, match="unexpected artifacts"):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=embedding,
            expected_topology_digest=topology_digest,
        )
    (commit_dir / "unexpected.bin").unlink()

    pointer_path = workspace["vector_root"] / "thermal" / "current.json"
    pointer = json.loads(pointer_path.read_text("utf-8"))
    pointer_path.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonically encoded"):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=embedding,
            expected_topology_digest=topology_digest,
        )


def test_projection_reader_rejects_stale_model_topology_and_document_tamper(
    workspace,
):
    event, _document, _identity, embedding, topology_digest = _projected_event("heat")
    _emit(workspace, event)
    applied = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    commit_id = applied.cell_results["thermal"].commit_id
    wrong_embedding = vector_projection.build_embedding_contract(
        model_id="test-embedding",
        model_version="wrong",
        dimension=embedding["dimension"],
    )
    with pytest.raises(ValueError, match="embedding contract mismatch"):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=wrong_embedding,
            expected_topology_digest=topology_digest,
        )
    with pytest.raises(ValueError, match="topology digest mismatch"):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=embedding,
            expected_topology_digest=_digest("stale-topology"),
        )

    documents = (
        workspace["vector_root"]
        / "thermal"
        / "commits"
        / commit_id
        / "documents.jsonl"
    )
    documents.write_bytes(documents.read_bytes() + b"{}\n")
    with pytest.raises(ValueError):
        mod.load_verified_projection_commit(
            workspace["vector_root"],
            "thermal",
            expected_embedding_contract=embedding,
            expected_topology_digest=topology_digest,
        )


def test_projection_failure_before_swap_keeps_pointer_unpublished(workspace):
    event, *_ = _projected_event("heat")
    _emit(workspace, event)
    failed = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
        _fail_before_swap_for_cells={"thermal"},
    )
    assert failed.cells_failed == 1
    assert not (workspace["vector_root"] / "thermal" / "current.json").exists()
    assert not workspace["checkpoint"].exists()


def test_commits_link_cannot_escape_vector_root(workspace, tmp_path):
    event, *_ = _projected_event("heat")
    _emit(workspace, event)
    cell_dir = workspace["vector_root"] / "thermal"
    outside = tmp_path / "outside"
    cell_dir.mkdir(parents=True)
    outside.mkdir()
    link = cell_dir / "commits"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    report = mod.apply(
        event_log=workspace["event_log"],
        vector_root=workspace["vector_root"],
        checkpoint_path=workspace["checkpoint"],
        dry_run=False,
    )
    assert report.cells_failed == 1
    assert "escapes cell root" in report.cell_results["thermal"].error
    assert list(outside.iterdir()) == []


def test_checkpoint_path_identifiers_fail_closed(workspace):
    workspace["checkpoint"].write_text(
        json.dumps(
            {
                "schema_version": 1,
                "global_last_applied_event_id": None,
                "last_applied_ts": None,
                "per_cell": {
                    "../escape": {
                        "last_applied_event_id": None,
                        "commit_id": "faiss_0123456789abcdef",
                        "applied_ts": None,
                        "vector_count": 0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        mod.load_checkpoint(workspace["checkpoint"])
