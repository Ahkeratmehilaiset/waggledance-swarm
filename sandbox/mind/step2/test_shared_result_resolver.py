"""Synthetic-only coverage of projected Q4 hash resolution."""
import copy
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import shared_result_resolver as resolver
from tools import wd_q4_agent_query as api


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fixture():
    event = {"ts_utc": "2026-01-01T12:00:59Z", "agent": "codex-lead-1", "type": "progress",
             "status": "done", "task_id": "synthetic-task", "message": "private synthetic text",
             "payload": {"secret": "synthetic"}}
    approved = frozenset({"synthetic-task"})
    projection = sp.project_events([event, dict(event, message="other dropped text")], approved_tasks=approved)
    data = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    manifest = json.dumps({"scope": "wd.q4-shared-projected-events.v1", "projection_schema": sp.SCHEMA,
                           "projection_sha256": digest(data)}, sort_keys=True).encode()
    record = projection["records"][0]
    hit = {"ref_sha256": digest(record["provenance"]["event_ref"].encode()),
           "doc_id_sha256": digest(record["record_id"].encode()),
           "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}
    return dict(memory_manifest=manifest, expected_memory_sha256=digest(manifest), projection_bytes=data,
                results=[hit], query_utc="2026-01-01T12:01:01.000001Z", approved_tasks=approved), record


def test_resolves_only_approved_text_and_deduplicates_identical_hits():
    args, record = fixture()
    args["results"].append(copy.deepcopy(args["results"][0]))
    result = resolver.resolve_results(**args)
    assert len(result) == 1
    assert result[0]["stored_text"] == record["stored_text"]
    assert result[0]["canonical_event"] is False
    assert result[0]["ref_kind"] == "sha256_of_projected_metadata"
    assert "private synthetic text" not in str(result) and "secret" not in str(result)


def test_agent_api_projects_validated_launcher_result_to_resolver_contract():
    args, record = fixture()
    launcher_result = {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1",
                       "descriptor_sha256": "d" * 64, "memory_sha256": "e" * 64,
                       "results": [args["results"][0]]}
    result = api._project_result(launcher_result, "d" * 64)
    assert set(result["results"][0]) == {"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
    args["results"] = result["results"]
    assert resolver.resolve_results(**args)[0]["stored_text"] == record["stored_text"]


@pytest.mark.parametrize("query", ["2026-01-01T12:00:10Z", "2026-01-01T12:01:01Z", "invalid"])
def test_no_future_or_boundary_resolution(query):
    args, _ = fixture()
    args["query_utc"] = query
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**args)


@pytest.mark.parametrize("mutation", ["manifest_hash", "manifest_bytes", "projection_bytes", "noncanonical",
                                       "unknown_ref", "mismatched_doc", "extra_result_key", "wrong_scope",
                                       "wrong_schema", "unapproved_task", "forged_record"])
def test_mismatch_and_tampering_fail_closed(mutation):
    args, _ = fixture()
    if mutation == "manifest_hash":
        args["expected_memory_sha256"] = "0" * 64
    elif mutation == "manifest_bytes":
        args["memory_manifest"] += b" "
    elif mutation == "projection_bytes":
        args["projection_bytes"] += b" "
    elif mutation == "noncanonical":
        p = json.loads(args["projection_bytes"])
        args["projection_bytes"] = json.dumps(p).encode()
        manifest = json.loads(args["memory_manifest"])
        manifest["projection_sha256"] = digest(args["projection_bytes"])
        args["memory_manifest"] = json.dumps(manifest).encode()
        args["expected_memory_sha256"] = digest(args["memory_manifest"])
    elif mutation == "unknown_ref":
        args["results"][0]["ref_sha256"] = "0" * 64
    elif mutation == "mismatched_doc":
        args["results"][0]["doc_id_sha256"] = "0" * 64
    elif mutation == "extra_result_key":
        args["results"][0]["message"] = "must not echo"
    elif mutation in ("wrong_scope", "wrong_schema"):
        manifest = json.loads(args["memory_manifest"])
        manifest["scope" if mutation == "wrong_scope" else "projection_schema"] = "invalid"
        args["memory_manifest"] = json.dumps(manifest).encode()
        args["expected_memory_sha256"] = digest(args["memory_manifest"])
    elif mutation == "unapproved_task":
        args["approved_tasks"] = frozenset()
    elif mutation == "forged_record":
        projection = json.loads(args["projection_bytes"])
        projection["records"][0]["stored_text"] = "private synthetic text"
        args["projection_bytes"] = json.dumps(projection, sort_keys=True, indent=1).encode()
        manifest = json.loads(args["memory_manifest"])
        manifest["projection_sha256"] = digest(args["projection_bytes"])
        args["memory_manifest"] = json.dumps(manifest).encode()
        args["expected_memory_sha256"] = digest(args["memory_manifest"])
    with pytest.raises(resolver.ResolveRefused) as exc:
        resolver.resolve_results(**args)
    assert "private" not in str(exc.value) and "must not echo" not in str(exc.value)


def test_partial_result_is_never_returned_when_later_hit_is_unknown():
    args, _ = fixture()
    args["results"].append({"ref_sha256": "0" * 64, "doc_id_sha256": "0" * 64,
                            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False})
    with pytest.raises(resolver.ResolveRefused, match="^resolution_refused$"):
        resolver.resolve_results(**args)


@pytest.mark.parametrize("field,value", [("ref_kind", "projected_event"), ("ref_kind", None),
                                         ("canonical_event", True), ("canonical_event", 0)])
def test_launcher_provenance_must_be_explicit_and_exact(field, value):
    args, _ = fixture()
    args["results"][0][field] = value
    with pytest.raises(resolver.ResolveRefused, match="^resolution_refused$"):
        resolver.resolve_results(**args)


@pytest.mark.parametrize("field", ["ref_kind", "canonical_event"])
def test_missing_launcher_provenance_is_not_assumed(field):
    args, _ = fixture()
    del args["results"][0][field]
    with pytest.raises(resolver.ResolveRefused, match="^resolution_refused$"):
        resolver.resolve_results(**args)


def test_duplicate_json_manifest_keys_are_refused_even_when_hash_is_pinned():
    args, _ = fixture()
    args["memory_manifest"] = (b'{"scope":"wd.q4-shared-projected-events.v1",'
                               b'"scope":"wd.q4-shared-projected-events.v1"}')
    args["expected_memory_sha256"] = digest(args["memory_manifest"])
    with pytest.raises(resolver.ResolveRefused, match="^resolution_refused$"):
        resolver.resolve_results(**args)


def test_invalid_noncanonical_provenance_refused_even_when_attacker_repins():
    args, _ = fixture()
    projection = json.loads(args["projection_bytes"])
    projection["records"][0]["provenance"]["canonical_event"] = True
    args["projection_bytes"] = json.dumps(projection, sort_keys=True, indent=1).encode()
    manifest = json.loads(args["memory_manifest"])
    manifest["projection_sha256"] = digest(args["projection_bytes"])
    args["memory_manifest"] = json.dumps(manifest).encode()
    args["expected_memory_sha256"] = digest(args["memory_manifest"])
    with pytest.raises(resolver.ResolveRefused, match="^resolution_refused$"):
        resolver.resolve_results(**args)
