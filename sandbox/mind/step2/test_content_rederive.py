"""Source re-derivation of a route B content snapshot (Lead source GO 2026-11-08 13:20Z, design D1 / RCO2 MP1).

After the build every downstream check of a content memory was structural: record ids and event refs are hashes of
the record's own text and source fields, so a projection whose text never occurred in the anchored bridge prefix or
the pinned document blobs passed as long as its pins were supplied. ``validate_content_snapshot`` re-reads exactly
the anchored message prefix and the pinned document blobs, re-derives the projection the way the route build does
and requires byte equality with the pinned projection. ``resolve_route_content`` re-screens every returned record's
decoded text at read time. Synthetic data only.
"""
import hashlib
import json

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_index_builder as sib
from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_content_route as tcr
from sandbox.mind.step2 import test_shared_doc_projection as tdp

route = tcr.route


def sha(data):
    return hashlib.sha256(data).hexdigest()


def built(tmp_path, route):
    _, memory_dir = tcr.build_route(tmp_path, route)
    return (memory_dir / "projection.json").read_bytes()


def validate(route, data, **changes):
    kwargs = dict(enabled=True, projection_bytes=data, projection_sha256=sha(data),
                  messages=route["messages"], documents=route["documents"], decision_bytes=route["decision"],
                  decision_sha256=route["decision_sha"], **tcr.strict_pins())
    kwargs.update(changes)
    return cm.validate_content_snapshot(**kwargs)


def refused(token, route, data, **changes):
    with pytest.raises(cm.ContentMemoryRefused, match="^" + token + "$"):
        validate(route, data, **changes)


def canonical(projection):
    return json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")


def forge(projection, part="messages", text="Synteettinen rivi jota lahteessa ei koskaan ollut."):
    """RCO2 MP1 reproduction: other output-class text with every self-derived field recomputed from it."""
    copy = json.loads(json.dumps(projection))
    record = copy[part]["records"][0]
    provenance, encoded = record["provenance"], cp.encode_text(text)
    if part == "messages":
        ref = sha(cp._canonical({"text": encoded, "source": provenance["source_event_ref"],
                                 "chunk": provenance["chunk_index"]}))
        record["record_id"] = "content:" + ref
    else:
        ref = sha(cp._canonical({"text": encoded, "source": provenance["source_doc_ref"],
                                 "section": provenance["section_index"], "chunk": provenance["chunk_index"]}))
        record["record_id"] = "doc:" + ref
    provenance["event_ref"] = ref
    record["stored_text"] = record["summary"] = encoded
    return copy


def forged_memory(tmp_path, route, projection):
    """The forged projection built into a memory with the module's own build and the same decision evidence."""
    decision = cm.verify_content_route_decision(route["decision"], route["decision_sha"],
                                                **{key: value for key, value in tcr.strict_pins().items()
                                                   if key in ("policy_sha256", "roster_sha256")})
    strict = tcr.strict_pins()
    policy = {"authorship": sp.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
              "policy_sha256": strict["policy_sha256"], "roster_sha256": strict["roster_sha256"]}
    out = tmp_path / "forged"
    out.mkdir()
    cm._build(projection, tcm.Spy(), tcm.IDENTITY, tcm.DIM, out, "route-f", cm.ROUTE_SCOPE, sib.DESCRIPTOR_SCHEMA,
              policy, cm._route_evidence(decision, route["decision_sha"], projection))
    return out / "route-f", decision


def test_the_snapshot_check_is_off_by_default(route):
    assert cm.validate_content_snapshot() == {"status": "OFF"}
    with pytest.raises(cm.ContentMemoryRefused, match="^enabled_invalid$"):
        cm.validate_content_snapshot(enabled=1)


def test_an_unchanged_snapshot_re_derives_to_the_same_bytes(tmp_path, route):
    projection_bytes = built(tmp_path, route)
    assert validate(route, projection_bytes) == {
        "status": "MATCH", "projection_sha256": sha(projection_bytes), "decision_sha256": route["decision_sha"],
        "source_prefix": route["prefix"], "doc_commit": route["commit"]}


@pytest.mark.parametrize("part", ["messages", "documents"])
def test_a_forged_row_that_passes_every_structural_check_is_refused(tmp_path, route, part):
    projection = forge(json.loads(built(tmp_path, route)), part)
    memory_dir, decision = forged_memory(tmp_path, route, projection)
    manifest = json.loads((memory_dir / "memory.json").read_bytes())
    stored = json.loads((memory_dir / "projection.json").read_bytes())
    cm.validate_route_projection(stored)  # the structural checks accept the forged row: the MP1 gap
    assert cm.check_content_route_evidence(manifest["content_route"], stored, decision, route["decision_sha"])
    refused("snapshot_mismatch", route, (memory_dir / "projection.json").read_bytes())


@pytest.mark.parametrize("change", [
    lambda p: p["messages"]["records"].pop(),
    lambda p: p["documents"]["records"].pop(),
    lambda p: p.update(documents=None),
])
def test_a_snapshot_missing_derived_records_is_refused(tmp_path, route, change):
    projection = json.loads(built(tmp_path, route))
    change(projection)
    refused("snapshot_mismatch", route, canonical(projection))


def test_bytes_other_than_the_canonical_serialization_are_refused(tmp_path, route):
    projection = json.loads(built(tmp_path, route))
    refused("snapshot_mismatch", route, json.dumps(projection, sort_keys=True).encode("utf-8"))


def test_the_projection_pin_is_checked_before_any_source_is_read(tmp_path, route, monkeypatch):
    projection_bytes = built(tmp_path, route)
    reads = []
    monkeypatch.setattr(cm, "_read_prefix_rows", lambda *args: reads.append(args))
    refused("snapshot_mismatch", route, projection_bytes, projection_sha256="0" * 64)
    refused("snapshot_mismatch", route, projection_bytes, projection_sha256=chr(233) * 64)
    refused("snapshot_mismatch", route, projection_bytes, projection_sha256=None)
    refused("snapshot_mismatch", route, projection_bytes, projection_bytes=projection_bytes.decode("utf-8"))
    assert reads == []


def test_rows_appended_after_the_anchored_prefix_are_not_part_of_the_snapshot(tmp_path, route):
    projection_bytes = built(tmp_path, route)
    with open(route["path"], "ab") as stream:
        stream.write(tcr.raw(tcr.prefix_rows()[0] | {"ts_utc": "2026-11-07T10:09:00Z"}) + b"\n")
    assert validate(route, projection_bytes)["status"] == "MATCH"


def test_a_prefix_changed_after_the_build_is_refused(tmp_path, route):
    projection_bytes = built(tmp_path, route)
    data = route["path"].read_bytes()
    route["path"].write_bytes(data.replace(b"Lane kuittasi", b"Lane kuittaa", 1))
    refused("source_unverified", route, projection_bytes)


def test_documents_from_another_commit_are_refused(tmp_path, route):
    projection_bytes = built(tmp_path, route)
    (tmp_path / "other").mkdir()
    other, _, _ = tcm.doc_source(tmp_path / "other", {"docs/guide.md": tdp.doc_bytes(tcm.DOC_TEXT + " Lisays.")},
                                 ["docs/guide.md"])
    with pytest.raises(cm.ContentMemoryRefused):
        validate(route, projection_bytes, documents=other)


def test_a_repository_without_the_pinned_commit_is_refused(tmp_path, route):
    projection_bytes = built(tmp_path, route)
    empty = tmp_path / "empty-repo"
    empty.mkdir()
    with pytest.raises(cm.ContentMemoryRefused):
        validate(route, projection_bytes, documents=dict(route["documents"], repo_root=str(empty)))


@pytest.mark.parametrize("change", [
    lambda r: {"messages": None},
    lambda r: {"documents": None},
    lambda r: {"messages": dict(r["messages"], policy_bytes=tcr.message_policy(tasks=(tcr.OTHER_TASK,))[0],
                                policy_sha256=tcr.message_policy(tasks=(tcr.OTHER_TASK,))[1])},
    lambda r: {"decision_sha256": "0" * 64},
    lambda r: {"policy_sha256": "0" * 64},
])
def test_sources_or_pins_other_than_the_decision_are_refused(tmp_path, route, change):
    projection_bytes = built(tmp_path, route)
    with pytest.raises(cm.ContentMemoryRefused):
        validate(route, projection_bytes, **change(route))


@pytest.mark.parametrize("pins, token", [
    ({"git_sha256": "0" * 64}, "repo_refused"),
    ({"git_executable": "git"}, "repo_refused"),
    ({"git_sha256": "A" * 64}, "repo_refused"),
    ({"git_executable": None}, "repo_refused"),
])
def test_the_document_re_derivation_runs_only_the_pinned_git(tmp_path, route, pins, token):
    projection_bytes = built(tmp_path, route)
    refused(token, route, projection_bytes, documents=dict(route["documents"], **pins))


def test_documents_without_the_git_pins_are_not_a_source(tmp_path, route):
    projection_bytes = built(tmp_path, route)
    documents = {key: value for key, value in route["documents"].items() if not key.startswith("git_")}
    refused("sources_invalid", route, projection_bytes, documents=documents)


@pytest.mark.parametrize("kind", ["blob", "commit", "tree"])
def test_a_git_object_altered_at_re_derivation_is_refused(tmp_path, route, monkeypatch, kind):
    """The re-derivation re-hashes every object it reads (MP3), so a changed object never yields a MATCH."""
    from sandbox.mind.step2 import shared_doc_projection as dp
    projection_bytes = built(tmp_path, route)
    original = dp._git

    def altered(root, env, *args):
        result = original(root, env, *args)
        if args[:2] == ("cat-file", kind):
            return bytes([result[0] ^ 1]) + result[1:]
        return result

    monkeypatch.setattr(dp, "_git", altered)
    with pytest.raises(cm.ContentMemoryRefused):
        validate(route, projection_bytes)


def test_a_returned_record_whose_text_fails_the_screen_is_refused_at_read_time(tmp_path, route):
    projection = json.loads(built(tmp_path, route))
    record = projection["messages"]["records"][0]
    record["stored_text"] = cp.encode_text("Ota yhteys synthetic.user" + chr(64) + "example.invalid kiitos")
    with pytest.raises(cm.ContentMemoryRefused, match="^result_unresolved$"):
        cm.resolve_route_content(projection, tcr.hits([record]), tcm.ASK_TIME)


@pytest.mark.parametrize("text", ["Katso https://example.invalid/sivu tarkemmin", "Kaksi  valia tassa"])
def test_a_returned_record_the_screen_would_rewrite_is_refused_at_read_time(tmp_path, route, text):
    """KEEP alone is not enough: the screen must give back exactly the stored text."""
    projection = json.loads(built(tmp_path, route))
    record = projection["messages"]["records"][0]
    assert cp.classify(text)[0] == "KEEP" and cp.classify(text)[1] != cp.encode_text(text)
    record["stored_text"] = cp.encode_text(text)
    with pytest.raises(cm.ContentMemoryRefused, match="^result_unresolved$"):
        cm.resolve_route_content(projection, tcr.hits([record]), tcm.ASK_TIME)


def test_the_read_time_screen_does_not_add_a_length_bound(tmp_path, route):
    projection = json.loads(built(tmp_path, route))
    record = projection["documents"]["records"][0]
    record["stored_text"] = cp.encode_text(" ".join(["Pitka dokumenttiosio"] * 400))
    assert len(cp.decode_text(record["stored_text"])) > cp.MAX_MESSAGE_CHARS
    assert len(cm.resolve_route_content(projection, tcr.hits([record]), tcm.ASK_TIME)) == 1


def test_projection_bytes_over_the_bound_are_refused_before_hashing(tmp_path, route, monkeypatch):
    projection_bytes = built(tmp_path, route)
    monkeypatch.setattr(cm, "MAX_PROJECTION_BYTES", len(projection_bytes) - 1)
    refused("snapshot_mismatch", route, projection_bytes)
    monkeypatch.setattr(cm, "MAX_PROJECTION_BYTES", len(projection_bytes))
    assert validate(route, projection_bytes)["status"] == "MATCH"


def test_every_genuine_record_passes_the_read_time_screen(tmp_path, route):
    projection = json.loads(built(tmp_path, route))
    records = projection["messages"]["records"] + projection["documents"]["records"]
    assert len(cm.resolve_route_content(projection, tcr.hits(records), tcm.ASK_TIME)) == len(records)


def sources_of(route):
    messages, documents = route["messages"], route["documents"]
    return {"message_source_path": messages["source_path"], "message_generation_path": messages["generation_path"],
            "message_policy_bytes": messages["policy_bytes"], "doc_repo_root": documents["repo_root"],
            "doc_policy_bytes": documents["policy_bytes"], "doc_git_executable": documents["git_executable"],
            "doc_git_sha256": documents["git_sha256"]}


def test_the_query_sources_map_back_to_the_decision_sources(route):
    """MP1 R1: the query path names locations only; the cursor and the policy pins come from the decision."""
    messages, documents = cm.snapshot_sources(json.loads(route["decision"]), sources_of(route))
    assert messages == route["messages"] and documents == route["documents"]


MESSAGE_NULL = {"message_source_path": None, "message_generation_path": None, "message_policy_bytes": None}
DOC_NULL = {"doc_repo_root": None, "doc_policy_bytes": None, "doc_git_executable": None, "doc_git_sha256": None}


@pytest.mark.parametrize("decision_change, source_change", [
    ({}, {"extra": 1}),
    ({}, {"doc_git_sha256": "A" * 64}),
    ({}, {"doc_git_sha256": "0" * 63}),
    ({}, {"doc_git_executable": "git"}),
    ({}, {"doc_repo_root": "repo"}),
    ({}, {"doc_policy_bytes": "policy"}),
    ({}, {"message_policy_bytes": None}),
    ({}, MESSAGE_NULL),
    ({}, DOC_NULL),
    ({"message_policy_sha256": None, "source_prefix": None}, {}),
    ({"doc_policy_sha256": None}, {}),
    ({"doc_policy_sha256": None}, dict(DOC_NULL, doc_git_sha256="0" * 64)),
    ({"message_policy_sha256": None, "source_prefix": None}, dict(MESSAGE_NULL, message_policy_bytes=b"x")),
])
def test_query_sources_not_matching_the_decision_shape_are_refused(route, decision_change, source_change):
    decision = dict(json.loads(route["decision"]), **decision_change)
    with pytest.raises(cm.ContentMemoryRefused, match="^sources_invalid$"):
        cm.snapshot_sources(decision, dict(sources_of(route), **source_change))


@pytest.mark.parametrize("key", ["message_source_path", "message_generation_path"])
def test_a_relative_bridge_location_is_refused_even_when_it_resolves(route, monkeypatch, key):
    """A relative path would follow the working directory; only absolute locations are a source."""
    import os
    absolute = sources_of(route)[key]
    monkeypatch.chdir(os.path.dirname(absolute))
    with pytest.raises(cm.ContentMemoryRefused, match="^sources_invalid$"):
        cm.snapshot_sources(json.loads(route["decision"]), dict(sources_of(route), **{key: os.path.basename(absolute)}))
