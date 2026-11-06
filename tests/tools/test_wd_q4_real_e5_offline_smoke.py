"""Opt-in REAL local E5 + REAL FAISS on a synthetic public-only corpus.

No live bridge, sealed case, operator data or production service is used.
Run only with an independently supplied model directory and weights digest;
the result does not prove production wiring or recall. Pytest basetemp MUST
be a fresh directory under this worktree's .codex-audit.
"""
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.environ.get("WD_TEST_REAL_E5_SMOKE") != "1", reason="explicit real-model opt-in")


def _hash_tree(root):
    """Before/after bytes of the existing model, never write there."""
    records = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AssertionError("model tree contains symlink")
        if path.is_file():
            h = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    h.update(chunk)
            records[path.relative_to(root).as_posix()] = (path.stat().st_size, h.hexdigest())
    return records


def test_real_local_e5_and_faiss_retrieve_only_synthetic_metadata(tmp_path, monkeypatch):
    assert os.name == "nt", "real checkpoint build requires Windows"
    assert os.path.commonpath((str(tmp_path.resolve()), str((ROOT / ".codex-audit").resolve()))) == str(
        (ROOT / ".codex-audit").resolve()), "temporary writes must stay in the audit tree"
    model = Path(os.environ["WD_TEST_E5_MODEL_DIR"])
    expected_sha = os.environ["WD_TEST_E5_WEIGHTS_SHA256"]
    revision = os.environ["WD_TEST_E5_REVISION"]
    assert model.is_absolute() and model.is_dir() and re.fullmatch(r"[0-9a-f]{64}", expected_sha)
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"):
        monkeypatch.setenv(name, "1")

    def no_network(*_args, **_kwargs):
        raise AssertionError("synthetic smoke forbids Python socket network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket.socket, "sendto", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)

    import faiss
    import numpy
    from sandbox.mind.step2 import encode, memory, q4, shared_bridge_snapshot as snapshot, shared_index_builder as builder
    from sandbox.mind.step2.test_status_public_filter import ROSTER, event, read_fixture
    from waggledance.core.bridge_log_reader import BridgeCursor

    before = _hash_tree(model)
    assert before["model.safetensors"][1] == expected_sha
    identity = encode.model_identity(model_dir=str(model), revision=revision)
    assert identity["weights_sha256"] == expected_sha
    encoder = encode.E5(model_dir=str(model), revision=revision)
    assert type(encoder.model).__name__ == "SentenceTransformer"
    assert encoder.policy["dim"] == 384 and encoder.policy["device"] == "cpu"

    lanes = ("codex-lead-1", "codex-tools-1", "fable-5")
    rows = [event(agent=lanes[i % len(lanes)], agent_uuid=ROSTER[lanes[i % len(lanes)]],
                  ts_utc=f"2026-10-30T12:{i:02d}:00Z", status="done", message="", paths=[], write_scope=[])
            for i in range(8)]
    rows.append(event(to="fable-5", message="synthetic-secret-message", payload={}))
    _, pins, source_path = read_fixture(tmp_path, rows)
    roster_doc = json.loads(pins["roster_bytes"])
    roster_doc["schema"] = "wd.q4-roster-snapshot.v1"
    pins["roster_bytes"] = json.dumps(roster_doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    pins["roster_sha256"] = hashlib.sha256(pins["roster_bytes"]).hexdigest()
    anchor_doc = json.loads(pins["anchor_bytes"])
    anchor_doc["roster_sha256"] = pins["roster_sha256"]
    pins["anchor_bytes"] = json.dumps(anchor_doc, sort_keys=True, separators=(",", ":")).encode("ascii")
    pins["anchor_sha256"] = hashlib.sha256(pins["anchor_bytes"]).hexdigest()
    tasks = frozenset({"public-task", "nonreview-private"})
    result = snapshot.read_anchored_delta(source_path, BridgeCursor(**anchor_doc["cursor_start"]), tasks,
        generation_path=anchor_doc["generation_path"], max_bytes=65536, max_rows=100, **pins)
    projection = result["projection"]
    assert len(projection["records"]) == 8 and "synthetic-secret-message" not in repr(projection)
    index_root = tmp_path / "synthetic-index"
    index_root.mkdir()
    source = {"snapshot_result": result, "expected_anchor_sha256": pins["anchor_sha256"],
              "mode": "public-v1", "public_policy_bytes": pins["public_policy_bytes"],
              "public_policy_sha256": pins["public_policy_sha256"], "anchor_bytes": pins["anchor_bytes"]}
    built = builder.build_memory(projection, builder.e5_passage_encoder(encoder.model),
        {key: identity[key] for key in ("model", "revision", "weights_sha256")}, 384, index_root, "one",
        approved_tasks=tasks, roster=ROSTER, exclude_review_signal=True,
        pins={key: pins[key] for key in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")},
        source=source)
    loaded = memory.Memory(index_root / "one")
    assert all(type(view.collection._index) is faiss.IndexFlatIP for view in loaded.views.values())
    assert all(view.healthy for view in loaded.views.values())
    answer = q4.Q4(loaded, encoder).ask("find done message records", "2026-10-30T12:30:00Z")
    assert answer["status"] == "ANSWER" and answer["results"]
    assert all(item["ref_kind"] == "sha256_of_projected_metadata" and item["canonical_event"] is False
               for item in answer["results"])
    abstain = q4.Q4(loaded, encoder).ask("find done message records", "2026-10-30T11:00:00Z")
    assert abstain["status"] == "ABSTAIN" and not abstain["results"]
    assert built["memory_sha256"] and numpy.isfinite(encoder.query("synthetic query")).all()
    assert encode.model_identity(model_dir=str(model), revision=revision) == identity
    assert _hash_tree(model) == before
