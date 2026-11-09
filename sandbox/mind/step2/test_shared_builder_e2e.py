"""Synthetic strict source-to-index chain; hashes here are not source authentication."""
import hashlib
import json
import os
from dataclasses import asdict

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("faiss")

from sandbox.mind.step2 import memory, q4, shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_index_builder as builder
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_result_resolver as resolver
from sandbox.mind.step2 import runtime_launcher
from waggledance.core.bridge_log_reader import read_bridge_log

TASKS = frozenset({"synthetic-task"})
ROSTER = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
          "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
          "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
          "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
          "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
ENCODER_IDENTITY = {"model": "synthetic", "revision": "a" * 40, "weights_sha256": "b" * 64}


def sha(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class FakeE5:
    def __call__(self, texts):
        return np.asarray([[1.0, 0.0] for _ in texts], dtype="<f4")

    def query(self, text):
        return np.asarray([1.0, 0.0], dtype="<f4")


@pytest.mark.skipif(os.name != "nt", reason="launcher is Windows-only")
def test_launcher_refuses_without_explicit_scratch_before_any_child(monkeypatch):
    calls = []
    monkeypatch.setattr(runtime_launcher.subprocess, "Popen", lambda *args, **kwargs: calls.append(True))
    with pytest.raises(runtime_launcher.LaunchRefused, match="^scratch_required$"):
        runtime_launcher.launch(interpreter="unused", interpreter_sha256="0" * 64, bootstrap="unused",
            bootstrap_sha256="0" * 64, verifier_sha256="0" * 64, tuple_args={}, query=b"{}", timeout=1,
            scratch_root=None)
    assert not calls


@pytest.mark.skipif(os.name != "nt", reason="checkpoint publication supports fixed Windows volumes only")
def test_anchored_delta_through_builder_checkpoint_and_strict_resolver(tmp_path):
    source = tmp_path / "synthetic.jsonl"
    source.write_bytes(b"")
    generation_path = tmp_path / "generation.json"
    generation_path.write_text('{"generation":"synthetic"}', encoding="utf-8")
    start = read_bridge_log(source, generation_path=generation_path).candidate_cursor
    assert start is not None and start.generation == "synthetic"
    rows = [{"ts_utc": f"2026-01-01T12:0{i}:20Z", "agent": lane, "agent_uuid": ROSTER[lane],
             "type": "message", "status": "done", "task_id": "synthetic-task",
             "message": "synthetic-private-do-not-index", "payload": {"private": "synthetic-secret"}}
            for i, lane in enumerate(("codex-lead-1", "codex-tools-1", "fable-5", "codex-lead-1",
                                      "codex-tools-1", "fable-5", "codex-lead-1", "codex-tools-1"))]
    source.write_bytes(b"".join(encoded(row) + b"\n" for row in rows))
    policy = encoded({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(TASKS)})
    roster = encoded({"schema": "wd.q4-roster-snapshot.v1",
                     "lanes": [{"agent": k, "agent_uuid": v, "role": "synthetic"} for k, v in ROSTER.items()
                                if k != "codex-tools-1"],
                      "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"],
                                           "role": "synthetic"}})
    anchor = encoded({"schema": "wd.q4-source-anchor.v1", "source_path": str(source.resolve()),
                      "generation_path": str(generation_path.resolve()), "cursor_start": asdict(start),
                      "policy_sha256": sha(policy), "roster_sha256": sha(roster)})
    result = snapshot.read_anchored_delta(source, start, TASKS, anchor_bytes=anchor, anchor_sha256=sha(anchor),
        generation_path=generation_path, max_bytes=65536, max_rows=100, policy_bytes=policy,
        policy_sha256=sha(policy), roster_bytes=roster, roster_sha256=sha(roster))
    projection = result["projection"]
    assert projection["accepted"] == 8
    assert snapshot.verify_anchored_projection(result, projection, expected_anchor_sha256=sha(anchor),
                                               expected_policy_sha256=sha(policy), expected_roster_sha256=sha(roster))
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode()
    source_binding = {"schema": "wd.q4-shared-index-source-binding.v1", "anchor_sha256": sha(anchor),
        "projection_sha256": sha(projection_bytes), "cursor_start_sha256": sha(snapshot._token(start).encode()),
        "cursor_end_sha256": sha(snapshot._token(result["candidate_cursor"]).encode()),
        "policy_sha256": sha(policy), "roster_sha256": sha(roster)}
    index_root = tmp_path / "q4-index"
    index_root.mkdir()
    encoder = FakeE5()
    # fda1e54 interface, subject to Fablen final cursor-binding correction.
    # This test must not claim authenticated source or production readiness.
    built = builder.build_memory(projection, encoder, ENCODER_IDENTITY, 2, index_root, "one",
        approved_tasks=TASKS, roster=ROSTER, exclude_review_signal=True,
        pins={"policy_bytes": policy, "policy_sha256": sha(policy), "roster_bytes": roster,
              "roster_sha256": sha(roster)},
        source={"snapshot_result": result, "expected_anchor_sha256": sha(anchor)})
    directory = index_root / "one"
    receipt = json.loads((directory / "build_receipt.json").read_bytes())
    assert receipt.get("source_binding") == source_binding, "builder lacks anchored source-binding interface"
    expected_policy = {"authorship": "declared_roster_consistent", "review_signal_excluded": True,
                       "policy_sha256": sha(policy), "roster_sha256": sha(roster)}
    manifest = (directory / "memory.json").read_bytes()
    assert json.loads(manifest)["policy"] == expected_policy and receipt["policy"] == expected_policy
    artifacts = {key: sha((directory / name).read_bytes()) for key, name in checkpoint.ARTIFACTS.items()}
    pointer = checkpoint.commit_generation(index_root, "one", start, result["candidate_cursor"], TASKS,
        expected_pointer_sha256=None, initial_cursor=start, expected_artifacts=artifacts, policy_bytes=policy,
        expected_policy_sha256=sha(policy), roster_bytes=roster, expected_roster_sha256=sha(roster),
        expected_anchor_sha256=sha(anchor))
    assert pointer["cursor_end"] == asdict(result["candidate_cursor"])
    answer = q4.Q4(memory.Memory(directory), encoder).ask("message synthetic-query", "2026-01-01T12:10:00Z")
    assert answer["status"] == "ANSWER" and answer["results"]
    hits = [{"ref_sha256": sha(item["ref"].encode()), "doc_id_sha256": sha(item["doc_id"].encode()),
             "ref_kind": item["ref_kind"], "canonical_event": item["canonical_event"]} for item in answer["results"]]
    resolved = resolver.resolve_results(memory_manifest=manifest, expected_memory_sha256=built["memory_sha256"],
        projection_bytes=projection_bytes, results=hits, query_utc="2026-01-01T12:10:00Z", approved_tasks=TASKS,
        policy_bytes=policy, policy_sha256=sha(policy), roster_bytes=roster, roster_sha256=sha(roster))
    assert resolved and all(item["canonical_event"] is False for item in resolved)
    assert "synthetic-private-do-not-index" not in repr(resolved) and "synthetic-secret" not in repr(resolved)
