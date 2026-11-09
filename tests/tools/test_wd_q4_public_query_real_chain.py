"""Synthetic public Q4 chain on Windows; the Q4 query backend alone is mocked.

This is not a live publisher, authenticated source, model, network, or fleet test.
The snapshot, builder, checkpoint, resolver and configuration syntax checks are real.
"""

import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import wd_q4_agent_query as q4
from tools import wd_q4_public_pointer_resolver as resolver
from tools import wd_q4_public_query as public_query


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires fixed Windows volumes")
def test_real_public_snapshot_builder_checkpoint_resolver_query(monkeypatch, tmp_path):
    from sandbox.mind.step2 import test_shared_index_builder as fixtures

    snapshot, _, checkpoint = fixtures._public_integration_modules()
    root = tmp_path / "public-series"
    root.mkdir()
    result, anchor_hash, anchor_bytes, start = fixtures._real_public_snapshot(
        snapshot, tmp_path / "synthetic-log", fixtures.MIXED_EVENTS
    )
    built = fixtures.build(
        root, name="gen-a", proj=result["projection"], approved_tasks=None, roster=None,
        source=fixtures._public_source(result, anchor_hash, anchor_bytes), allow_unbound_source=False,
    )
    artifacts = fixtures._artifacts_of(built)
    checkpoint.commit_generation(
        root, "gen-a", start, result["candidate_cursor"], fixtures.APPROVED,
        expected_pointer_sha256=None, expected_artifacts=artifacts, initial_cursor=start,
        snapshot_result=result, policy_bytes=fixtures.POLICY_BYTES,
        expected_policy_sha256=fixtures.POLICY_SHA, roster_bytes=fixtures.ROSTER_BYTES,
        expected_roster_sha256=fixtures.ROSTER_SHA, expected_anchor_sha256=anchor_hash,
        expected_anchor_bytes=anchor_bytes, **fixtures.PUBLIC_CHECKPOINT_KWARGS,
    )
    pointer_hash = sha((root / checkpoint.POINTER).read_bytes())
    pins = dict(
        expected_pointer_sha256=pointer_hash, policy_bytes=fixtures.POLICY_BYTES,
        policy_sha256=fixtures.POLICY_SHA, roster_bytes=fixtures.ROSTER_BYTES,
        roster_sha256=fixtures.ROSTER_SHA, public_policy_bytes=fixtures.PUBLIC_POLICY_BYTES,
        public_policy_sha256=fixtures.PUBLIC_POLICY_SHA, enabled=True,
    )
    resolved = resolver.resolve_public_pointer(root, **pins)
    assert resolved["generation"] == "gen-a"
    assert resolved["pointer_sha256"] == pointer_hash
    for field, digest in artifacts.items():
        assert resolved[field] == digest
    generation = root / "gen-a"
    assert Path(resolved["memory_root"]) == generation
    assert fixtures.SENTINEL.encode() not in fixtures._files_text(generation)
    descriptor = json.loads((generation / "descriptor.json").read_bytes())
    encoder_sha = public_query._encoder_digest(descriptor["encoder"])
    config = q4.VerifiedQ4Config(
        interpreter=r"C:\synthetic\python.exe", interpreter_sha256="1" * 64,
        bootstrap=r"C:\synthetic\bootstrap.py", bootstrap_sha256="2" * 64,
        verifier_sha256="3" * 64, runtime_root=r"C:\synthetic\runtime",
        runtime_manifest=r"C:\synthetic\manifest.json", runtime_manifest_sha256="4" * 64,
        runtime_generation="b" * 40, runtime_revision=descriptor["encoder"]["revision"],
        memory=resolved["memory_root"], descriptor=resolved["descriptor_path"],
        descriptor_sha256=artifacts["descriptor_sha256"], lock_file=r"C:\synthetic\reader.lock",
        lock_timeout="1", timeout=5, scratch_root=r"C:\synthetic\scratch",
    )
    # Structure validation is intentionally not proof that caller-provided
    # pins, policy origins, runtime, or publisher are trustworthy.
    from tools import wd_q4_public_plan as public_plan
    plan = public_plan.validate_public_plan({
        "schema": "wd.q4-public-plan.v1", "config": config, "root": str(root),
        **{k: v for k, v in pins.items() if k != "enabled"},
        "expected_source_sha256": anchor_hash,
        "expected_encoder_sha256": encoder_sha,
    }, enabled=True)
    assert plan.status == "VALIDATED_STRUCTURE_ONLY"
    assert plan.pointer_target == "NOT_CHECKED"
    calls = []

    # Only the actual runtime launcher/model backend is replaced. Both the
    # query adapter's input/output checks and shared_result_resolver are real.
    record = result["projection"]["records"][0]
    reference = {"ref_sha256": sha(record["provenance"]["event_ref"].encode()),
                 "doc_id_sha256": sha(record["record_id"].encode()),
                 "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}

    def fake_q4_backend(**kwargs):
        calls.append(kwargs)
        assert kwargs["tuple_args"]["memory"] == config.memory
        assert kwargs["tuple_args"]["descriptor-sha256"] == artifacts["descriptor_sha256"]
        return {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1",
                "descriptor_sha256": artifacts["descriptor_sha256"],
                "memory_sha256": artifacts["memory_sha256"], "results": [reference]}

    monkeypatch.setattr(public_query.q4_query, "_invoke_launcher", fake_q4_backend)
    query_pins = {**pins, "expected_source_sha256": anchor_hash, "expected_encoder_sha256": encoder_sha}

    def query(**changes):
        raw = json.dumps({"situation": "synthetic", "time": datetime.now(timezone.utc).isoformat()}).encode()
        return public_query.query_public(config, io.BytesIO(raw), root=root, **{**query_pins, **changes})

    output = query()
    assert len(calls) == 1
    assert output["status"] == "ANSWER"
    assert output["schema"] == "wd.q4-public-query.v2"
    assert output["results"] == [{**reference, "stored_text": record["stored_text"]}]
    assert output["binding"]["pointer_sha256"] == pointer_hash
    assert output["binding"]["source_anchor_sha256"] == anchor_hash
    assert output["binding"]["encoder_sha256"] == encoder_sha
    for field, digest in artifacts.items():
        assert output["binding"][field] == digest
    assert all(output[field] == "UNKNOWN" for field in
               ("publisher_authentication", "source_filter_attestation", "freshness", "same_user_aba"))
    assert fixtures.SENTINEL not in json.dumps(output)
    assert record["stored_text"] in json.dumps(output)
    for field in ("expected_pointer_sha256", "expected_source_sha256", "expected_encoder_sha256",
                  "policy_sha256", "roster_sha256", "public_policy_sha256"):
        with pytest.raises(public_query.PublicQueryRefused, match="^public_query_refused$"):
            query(**{field: "0" * 64})
        assert len(calls) == 1

    # Exercise the actual child adapter with the real public resolver and
    # verifier. Only the launcher/model backend above is synthetic.
    from tools import wd_q4_public_child as child
    raw = json.dumps({"situation": "synthetic", "time": datetime.now(timezone.utc).isoformat()}).encode()
    child_output = io.StringIO()
    code = child.run_public(config, io.BytesIO(raw), child_output, root=root, **query_pins)
    assert code == 0
    assert json.loads(child_output.getvalue()) == output
    assert len(calls) == 2
    refused_output = io.StringIO()
    code = child.run_public(config, io.BytesIO(raw), refused_output, root=root,
                            **{**query_pins, "expected_pointer_sha256": "0" * 64})
    assert code == 2
    assert json.loads(refused_output.getvalue())["status"] == "REFUSED"
    assert len(calls) == 2
