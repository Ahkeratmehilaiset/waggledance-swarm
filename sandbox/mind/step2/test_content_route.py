"""Route B for the useful content memory (Lead decision 2026-11-07 18:56Z): the content memory under the shared scope
and the v2 shared descriptor, so the isolated child stays the only reader, opened only by a pinned content route
decision. Synthetic data only: rows, policies, roster, prefix, repository and decision are synthetic pinned bytes."""
import hashlib
import json

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import q4 as q4_mod
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_index_builder as sib
from sandbox.mind.step2 import shared_projection as sp
from sandbox.mind.step2 import shared_result_resolver as resolver
from sandbox.mind.step2 import test_content_memory as tcm
from sandbox.mind.step2 import test_shared_content_projection as tcp
from sandbox.mind.step2 import test_shared_doc_projection as tdp

OTHER_TASK = "synthetic-other-task-v1"
UNAPPROVED_TASK = "synthetic-unapproved-task-v1"


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_pins(tasks=(tcp.TASK, OTHER_TASK)):
    policy = raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": list(tasks)})
    fleet = raw({"lanes": [{"agent": lane, "agent_uuid": tcp.ROSTER[lane]} for lane in tcp.ROSTER if lane != tcp.TOOLS],
                 "tools_supervisor": {"agent": tcp.TOOLS, "agent_uuid": tcp.ROSTER[tcp.TOOLS]}})
    return {"policy_bytes": policy, "policy_sha256": sha(policy), "roster_bytes": fleet, "roster_sha256": sha(fleet)}


def message_policy(tasks=(tcp.TASK,)):
    data = raw(tcp.policy_doc(approved_tasks=list(tasks)))
    return data, sha(data)


def prefix_rows():
    return [tcp.row(tcp.FINNISH, minute=1), tcp.row("Lane kuittasi tehtavan valmiiksi.", minute=2, type="done",
                                                    agent=tcp.TOOLS),
            tcp.row("Toinen tehtava ei kuulu muistiin.", minute=3, task_id=UNAPPROVED_TASK)]


def write_prefix(directory, rows):
    from waggledance.core import bridge_log_reader as bridge
    directory.mkdir(exist_ok=True)
    path, generation = directory / "events.jsonl", directory / "generation.json"
    generation.write_bytes(raw({"generation": "synthetic-g1"}))
    content = b"".join(raw(row) + b"\n" for row in rows)
    path.write_bytes(content)
    cursor = bridge.read_bridge_log(str(path), generation_path=str(generation)).candidate_cursor
    return path, generation, cursor, sha(content)


def decision_of(*, prefix=None, message_pin=None, doc_pin=None, strict=None, **overrides):
    strict = strict_pins() if strict is None else strict
    value = dict(cm.module_sha256s(), schema=cm.DECISION_SCHEMA, decision=cm.DECISION_NAME,
                 projection_schema=cm.SCHEMA, scope=cm.ROUTE_SCOPE, nonreview_policy_sha256=strict["policy_sha256"],
                 roster_sha256=strict["roster_sha256"], message_policy_sha256=message_pin, doc_policy_sha256=doc_pin,
                 source_prefix=prefix)
    value.update(overrides)
    data = raw(value)
    return data, sha(data)


@pytest.fixture
def route(tmp_path):
    """Sources, pins and a decision for a route with messages and documents."""
    path, generation, cursor, prefix_sha = write_prefix(tmp_path / "prefix", prefix_rows())
    policy_bytes, policy_sha = message_policy()
    messages = {"source_path": str(path), "generation_path": str(generation), "end_cursor": cursor,
                "policy_bytes": policy_bytes, "policy_sha256": policy_sha}
    documents, _, commit = tcm.doc_source(tmp_path, {"docs/guide.md": tdp.doc_bytes(tcm.DOC_TEXT)}, ["docs/guide.md"])
    prefix = {"sha256": prefix_sha, "offset": cursor.offset, "generation": cursor.generation}
    decision, decision_sha = decision_of(prefix=prefix, message_pin=policy_sha, doc_pin=documents["policy_sha256"])
    return {"messages": messages, "documents": documents, "decision": decision, "decision_sha": decision_sha,
            "prefix": prefix, "commit": commit, "path": path}


def trusted_git(route):
    """The trusted Git pin a builder passes (RCO1 Q1): in deployment the fleet record's, here the fixture's own."""
    return {"git_executable": route["documents"]["git_executable"], "git_sha256": route["documents"]["git_sha256"]}


def build_route(tmp_path, route, name="route-a", **changes):
    pytest.importorskip("faiss")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    kwargs = dict(enabled=True, messages=route["messages"], documents=route["documents"],
                  decision_bytes=route["decision"], decision_sha256=route["decision_sha"], encode=tcm.Spy(),
                  identity=tcm.IDENTITY, dim=tcm.DIM, root=out, name=name, trusted_git=trusted_git(route),
                  **strict_pins())
    kwargs.update(changes)
    built = cm.build_route_content_memory(**kwargs)
    memory_dir = out / name
    return built, memory_dir


def load(memory_dir):
    manifest = json.loads((memory_dir / "memory.json").read_bytes())
    projection = json.loads((memory_dir / "projection.json").read_bytes())
    return manifest, projection


# --- constants -------------------------------------------------------------------------------------------------------

def test_route_constants_are_the_shared_reader_constants():
    assert cm.ROUTE_SCOPE == sib.SCOPE == resolver.SCOPE
    assert cm.PROJECTED_REF_KIND == q4_mod.PROJECTED_REF_KIND == resolver.REF_KIND
    assert cm.SCHEMA not in (resolver.PROJECTION_SCHEMA, resolver.CARD_PROJECTION_SCHEMA)


def test_route_build_is_default_off():
    assert cm.build_route_content_memory() == {"status": "OFF"}
    with pytest.raises(cm.ContentMemoryRefused, match="enabled_invalid"):
        cm.build_route_content_memory(enabled=1)


# --- decision --------------------------------------------------------------------------------------------------------

PREFIX = {"sha256": "a" * 64, "offset": 10, "generation": "synthetic-g1"}


def verify(data, digest, strict=None):
    strict = strict_pins() if strict is None else strict
    return cm.verify_content_route_decision(data, digest, policy_sha256=strict["policy_sha256"],
                                            roster_sha256=strict["roster_sha256"])


def test_a_valid_decision_verifies():
    data, digest = decision_of(prefix=PREFIX, message_pin="b" * 64, doc_pin="c" * 64)
    assert verify(data, digest)["decision"] == cm.DECISION_NAME
    data, digest = decision_of(doc_pin="c" * 64)
    assert verify(data, digest)["source_prefix"] is None


@pytest.mark.parametrize("change", [
    {"schema": "wd.mind-content-route-decision.v0"}, {"decision": "enable_card_route"},
    {"projection_schema": resolver.PROJECTION_SCHEMA}, {"scope": cm.SCOPE}, {"content_module_sha256": "0" * 64},
    {"message_module_sha256": "0" * 64}, {"doc_module_sha256": "0" * 64}, {"nonreview_policy_sha256": "0" * 64},
    {"roster_sha256": "0" * 64}, {"message_policy_sha256": None}, {"doc_policy_sha256": "C" * 64},
    {"source_prefix": None}, {"source_prefix": dict(PREFIX, offset=True)}, {"source_prefix": dict(PREFIX, offset=0)},
    {"source_prefix": dict(PREFIX, generation="bad generation")}, {"source_prefix": dict(PREFIX, extra=1)},
    {"extra": 1},
])
def test_any_other_decision_is_refused(change):
    data, digest = decision_of(prefix=PREFIX, message_pin="b" * 64, doc_pin="c" * 64, **change)
    with pytest.raises(cm.ContentMemoryRefused, match="decision_invalid"):
        verify(data, digest)


def test_a_decision_without_any_source_or_with_a_prefix_but_no_messages_is_refused():
    for kwargs in ({}, {"prefix": PREFIX, "doc_pin": "c" * 64}):
        data, digest = decision_of(**kwargs)
        with pytest.raises(cm.ContentMemoryRefused, match="decision_invalid"):
            verify(data, digest)


def test_decision_bytes_must_be_canonical_and_pinned():
    data, digest = decision_of(doc_pin="c" * 64)
    pretty = json.dumps(json.loads(data), sort_keys=True, indent=1).encode("ascii")
    for bad, pin in ((pretty, sha(pretty)), (data, "0" * 64), (data + b" ", sha(data + b" ")), (b"", sha(b""))):
        with pytest.raises(cm.ContentMemoryRefused, match="decision_invalid"):
            verify(bad, pin)
    data_missing = raw({k: v for k, v in json.loads(data).items() if k != "source_prefix"})
    with pytest.raises(cm.ContentMemoryRefused, match="decision_invalid"):
        verify(data_missing, sha(data_missing))


def test_a_decision_for_other_strict_pins_is_refused():
    data, digest = decision_of(doc_pin="c" * 64)
    with pytest.raises(cm.ContentMemoryRefused, match="decision_invalid"):
        verify(data, digest, strict=strict_pins(tasks=(tcp.TASK,)))


def test_a_changed_content_module_invalidates_the_decision(monkeypatch):
    data, digest = decision_of(doc_pin="c" * 64)
    real = cm.module_sha256s()
    monkeypatch.setattr(cm, "module_sha256s", lambda: dict(real, doc_module_sha256="d" * 64))
    with pytest.raises(cm.ContentMemoryRefused, match="decision_invalid"):
        verify(data, digest)


# --- route build -----------------------------------------------------------------------------------------------------

def test_route_memory_is_for_the_shared_reader_and_binds_policy_and_evidence(tmp_path, route):
    built, memory_dir = build_route(tmp_path, route)
    manifest, projection = load(memory_dir)
    assert manifest["scope"] == sib.SCOPE and manifest["projection_schema"] == cm.SCHEMA
    assert built["descriptor"]["schema"] == sib.DESCRIPTOR_SCHEMA and built["descriptor"]["scope"] == sib.SCOPE
    strict = strict_pins()
    assert manifest["policy"] == {"authorship": sp.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
                                  "policy_sha256": strict["policy_sha256"], "roster_sha256": strict["roster_sha256"]}
    assert manifest["content_route"] == {"decision_sha256": route["decision_sha"], "source_prefix": route["prefix"],
                                         "source_status": "MATCH_WITHIN_PREFIX", "completeness": "UNKNOWN",
                                         "message_policy_sha256": route["messages"]["policy_sha256"],
                                         "doc_policy_sha256": route["documents"]["policy_sha256"],
                                         "doc_commit": route["commit"]}
    texts = [cp.decode_text(r["stored_text"]) for r in projection["messages"]["records"]]
    assert texts == [tcp.FINNISH, "Lane kuittasi tehtavan valmiiksi."]
    assert projection["messages"]["refused"] == {}  # the unapproved task row is never read from the prefix
    assert cm.validate_route_projection(projection) is projection
    assert manifest["records"] == built["records"] == 4


def test_rows_appended_after_the_anchored_prefix_are_not_read(tmp_path, route):
    with open(route["path"], "ab") as handle:
        handle.write(raw(tcp.row("Myohemmin lisatty rivi.", minute=9)) + b"\n")
    _, memory_dir = build_route(tmp_path, route)
    _, projection = load(memory_dir)
    assert projection["messages"]["accepted"] == 2


def test_a_changed_prefix_is_not_the_anchored_source(tmp_path, route):
    data = route["path"].read_bytes()
    route["path"].write_bytes(data.replace(b"valmiiksi", b"valmiixsi", 1))
    with pytest.raises(cm.ContentMemoryRefused, match="source_unverified"):
        build_route(tmp_path, route)
    assert not (tmp_path / "out" / "route-a").exists()


def test_a_cursor_other_than_the_decision_prefix_is_refused(tmp_path, route):
    prefix = dict(route["prefix"], offset=route["prefix"]["offset"] - 1)
    decision, digest = decision_of(prefix=prefix, message_pin=route["messages"]["policy_sha256"],
                                   doc_pin=route["documents"]["policy_sha256"])
    with pytest.raises(cm.ContentMemoryRefused, match="source_unanchored"):
        build_route(tmp_path, route, decision_bytes=decision, decision_sha256=digest)


def test_policies_other_than_the_decision_pins_are_refused(tmp_path, route):
    other, other_sha = message_policy(tasks=(tcp.TASK, OTHER_TASK))
    messages = dict(route["messages"], policy_bytes=other, policy_sha256=other_sha)
    with pytest.raises(cm.ContentMemoryRefused, match="decision_mismatch"):
        build_route(tmp_path, route, messages=messages)
    with pytest.raises(cm.ContentMemoryRefused, match="decision_mismatch"):
        build_route(tmp_path, route, documents=None)


def test_message_tasks_must_be_inside_the_provisioned_non_review_list(tmp_path, route):
    wide, wide_sha = message_policy(tasks=(tcp.TASK, UNAPPROVED_TASK))
    messages = dict(route["messages"], policy_bytes=wide, policy_sha256=wide_sha)
    decision, digest = decision_of(prefix=route["prefix"], message_pin=wide_sha,
                                   doc_pin=route["documents"]["policy_sha256"])
    with pytest.raises(cm.ContentMemoryRefused, match="policy_not_subset"):
        build_route(tmp_path, route, messages=messages, decision_bytes=decision, decision_sha256=digest)


def test_invalid_strict_pins_refuse_before_anything_is_read(tmp_path, route):
    with pytest.raises(cm.ContentMemoryRefused, match="policy_invalid"):
        build_route(tmp_path, route, policy_sha256="0" * 64)


def test_a_documents_only_route_needs_no_prefix(tmp_path, route):
    decision, digest = decision_of(doc_pin=route["documents"]["policy_sha256"])
    built, memory_dir = build_route(tmp_path, route, messages=None, decision_bytes=decision, decision_sha256=digest)
    manifest, projection = load(memory_dir)
    assert manifest["content_route"]["source_status"] == "NO_MESSAGES" and projection["messages"] is None
    assert built["records"] == 2


def test_cell_rows_are_in_the_one_form_the_child_reads(tmp_path, route):
    from sandbox.mind.step2 import memory as memory_mod
    _, memory_dir = build_route(tmp_path, route)
    raw_manifest = (memory_dir / "memory.json").read_bytes()
    memory = memory_mod.Memory(memory_dir, manifest_bytes=raw_manifest)
    for view in memory.views.values():
        for row in view.rows:
            assert row["kind"] == "event" and row["provenance"]["ref_kind"] == cm.PROJECTED_REF_KIND
            assert row["provenance"]["canonical_event"] is False
    answer = q4_mod.Q4(memory, tcm.Encoder()).ask("miten hex-muisti tallentaa parven", tcm.ASK_TIME)
    assert all(result["ref_kind"] == cm.PROJECTED_REF_KIND for result in answer["results"])


# --- projection check, evidence, resolution ----------------------------------------------------------------------------

def tamper(projection, part, change):
    copy = json.loads(json.dumps(projection))
    change(copy[part]["records"])
    return copy


@pytest.mark.parametrize("part, change", [
    ("messages", lambda records: records[0].update(stored_text=cp.encode_text("vaihdettu"))),
    ("messages", lambda records: records[0].update(record_id="content:" + "0" * 64)),
    ("documents", lambda records: records[0].update(kind="event")),
    ("documents", lambda records: records[0]["provenance"].update(path="docs/other.md")),
    ("documents", lambda records: records[0]["provenance"].update(ref_kind=cm.PROJECTED_REF_KIND)),
    ("messages", lambda records: records.append(dict(records[0]))),
    ("messages", lambda records: records[0]["provenance"].update(source_kind="card")),
    ("documents", lambda records: records[0]["provenance"].update(source_doc_ref="0" * 64)),
])
def test_the_projection_check_refuses_any_inconsistent_record(tmp_path, route, part, change):
    _, memory_dir = build_route(tmp_path, route)
    _, projection = load(memory_dir)
    with pytest.raises(cm.ContentMemoryRefused, match="projection_invalid"):
        cm.validate_route_projection(tamper(projection, part, change))


@pytest.mark.parametrize("key, value", [("decision_sha256", "0" * 64), ("source_status", "NO_MESSAGES"),
                                        ("completeness", "COMPLETE"), ("doc_commit", "0" * 40),
                                        ("message_policy_sha256", None), ("extra", 1)])
def test_the_evidence_must_be_what_the_decision_and_projection_give(tmp_path, route, key, value):
    _, memory_dir = build_route(tmp_path, route)
    manifest, projection = load(memory_dir)
    decision = verify(route["decision"], route["decision_sha"])
    assert cm.check_content_route_evidence(manifest["content_route"], projection, decision, route["decision_sha"])
    with pytest.raises(cm.ContentMemoryRefused, match="evidence_invalid"):
        cm.check_content_route_evidence(dict(manifest["content_route"], **{key: value}), projection, decision,
                                        route["decision_sha"])


def hits(records):
    return [{"ref_sha256": sha(r["provenance"]["event_ref"].encode("ascii")),
             "doc_id_sha256": sha(r["record_id"].encode("ascii")), "ref_kind": cm.PROJECTED_REF_KIND,
             "canonical_event": False} for r in records]


def test_hash_only_hits_resolve_to_the_encoded_text(tmp_path, route):
    _, memory_dir = build_route(tmp_path, route)
    _, projection = load(memory_dir)
    records = projection["messages"]["records"] + projection["documents"]["records"]
    resolved = cm.resolve_route_content(projection, hits(records) + hits(records[:1]), tcm.ASK_TIME)
    assert [item["stored_text"] for item in resolved] == [r["stored_text"] for r in records]
    assert all(item["ref_kind"] == cm.PROJECTED_REF_KIND and item["canonical_event"] is False for item in resolved)


@pytest.mark.parametrize("change", [
    lambda item: item.update(ref_sha256="0" * 64), lambda item: item.update(ref_kind=cp.REF_KIND),
    lambda item: item.update(canonical_event=True), lambda item: item.update(extra=1),
    lambda item: item.update(doc_id_sha256="A" * 64),
])
def test_any_other_hit_is_unresolved(tmp_path, route, change):
    _, memory_dir = build_route(tmp_path, route)
    _, projection = load(memory_dir)
    item = hits(projection["messages"]["records"])[0]
    change(item)
    with pytest.raises(cm.ContentMemoryRefused, match="result_unresolved"):
        cm.resolve_route_content(projection, [item], tcm.ASK_TIME)


def test_a_hit_not_yet_visible_at_the_query_time_is_unresolved(tmp_path, route):
    _, memory_dir = build_route(tmp_path, route)
    _, projection = load(memory_dir)
    with pytest.raises(cm.ContentMemoryRefused, match="result_unresolved"):
        cm.resolve_route_content(projection, hits(projection["messages"]["records"]), "2026-11-07T12:01:30Z")
    with pytest.raises(cm.ContentMemoryRefused, match="query_time_invalid"):
        cm.resolve_route_content(projection, [], "not-a-time")


@pytest.mark.parametrize("key, value", [("scope", sib.SCOPE), ("authority", "full"), ("schema", cp.SCHEMA)])
def test_the_projection_check_refuses_other_top_level_values(tmp_path, route, key, value):
    _, memory_dir = build_route(tmp_path, route)
    _, projection = load(memory_dir)
    with pytest.raises(cm.ContentMemoryRefused, match="projection_invalid"):
        cm.validate_route_projection(dict(projection, **{key: value}))


# --- increment 6: the resolver content branch and the isolated child --------------------------------------------------

def resolver_inputs(tmp_path, route, **changes):
    built, memory_dir = build_route(tmp_path, route)
    strict = strict_pins()
    data = dict(memory_manifest=(memory_dir / "memory.json").read_bytes(), expected_memory_sha256=built["memory_sha256"],
                projection_bytes=(memory_dir / "projection.json").read_bytes(), query_utc=tcm.ASK_TIME,
                content_decision_bytes=route["decision"], content_decision_sha256=route["decision_sha"], **strict)
    data.update(changes)
    return built, memory_dir, data


def child_hits(memory_dir, built, monkeypatch, situation):
    """The output of the real isolated reader through the real launcher output check (no process)."""
    from sandbox.mind.step2 import runtime_launcher
    from sandbox.mind.step2 import wd_q4_private_read as reader
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kw: tcm.Encoder())
    monkeypatch.setattr(reader, "bind_runtime_encoder", lambda descriptor, model_dir: None)
    out = reader._private_read_locked(memory_dir, memory_dir / "descriptor.json", built["descriptor_sha256"], situation,
                                      tcm.ASK_TIME, "C:/fake/model", tcm.IDENTITY["revision"])
    return runtime_launcher._validate_output(json.dumps(out).encode("utf-8"), 0, built["descriptor_sha256"])


def test_the_isolated_child_reads_the_route_memory_and_the_resolver_answers(tmp_path, route, monkeypatch):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    checked = child_hits(memory_dir, built, monkeypatch, "miten hex-muisti tallentaa parven p" + tcm.A + tcm.A + "t"
                         + tcm.O + "kset")
    results = checked["results"]
    assert results and all(set(item) == {"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
                           for item in results)
    resolved = resolver.resolve_results(results=results, **data)
    texts = [cp.decode_text(item["stored_text"]) for item in resolved]
    assert tcm.DOC_TEXT in texts and all(tcp.TEXT_CLASS.fullmatch(item["stored_text"]) for item in resolved)


def test_the_resolver_answers_hash_hits_with_the_content_decision(tmp_path, route):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    _, projection = load(memory_dir)
    records = projection["messages"]["records"]
    resolved = resolver.resolve_results(results=hits(records), **data)
    assert [item["stored_text"] for item in resolved] == [r["stored_text"] for r in records]


def test_without_the_content_decision_the_v6_path_refuses_the_route_memory(tmp_path, route):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    _, projection = load(memory_dir)
    plain = {k: v for k, v in data.items() if not k.startswith("content_decision")}
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(results=hits(projection["messages"]["records"]), **plain)


@pytest.mark.parametrize("change", [
    {"card_decision_bytes": b"{}", "card_decision_sha256": "0" * 64},
    {"content_decision_sha256": "0" * 64},
    {"policy_bytes": None, "policy_sha256": None, "roster_bytes": None, "roster_sha256": None},
    {"query_utc": "not-a-time"},
])
def test_the_content_branch_refuses_other_inputs(tmp_path, route, change):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    _, projection = load(memory_dir)
    data.update(change)
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(results=hits(projection["messages"]["records"]), **data)


def rewrite(memory_dir, edit_manifest=None, edit_projection=None):
    from sandbox.mind.step2 import cell as cell_mod
    manifest, projection = load(memory_dir)
    if edit_projection is not None:
        edit_projection(projection)
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    manifest["projection_sha256"] = sha(projection_bytes)
    if edit_manifest is not None:
        edit_manifest(manifest)
    manifest_bytes = cell_mod.canonical(manifest).encode("utf-8")
    return manifest_bytes, projection_bytes, projection


@pytest.mark.parametrize("edit_manifest, edit_projection", [
    (lambda m: m["content_route"].update(doc_commit="0" * 40), None),
    (lambda m: m.pop("content_route"), None),
    (lambda m: m["policy"].update(review_signal_excluded=False), None),
    (lambda m: m.update(scope=cm.SCOPE), None),
    (None, lambda p: p["messages"]["records"][0]["meta"].update(agent=tcp.RCO1)),
    (None, lambda p: p["messages"]["records"][0]["meta"].update(agent="unknown-lane")),
    (None, lambda p: p["messages"]["records"][0]["meta"].update(task_id=UNAPPROVED_TASK)),
    (None, lambda p: p["documents"]["records"][0].update(stored_text=cp.encode_text("vaihdettu"))),
])
def test_the_content_branch_rechecks_manifest_and_projection(tmp_path, route, edit_manifest, edit_projection):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    manifest_bytes, projection_bytes, projection = rewrite(memory_dir, edit_manifest, edit_projection)
    data.update(memory_manifest=manifest_bytes, expected_memory_sha256=sha(manifest_bytes),
                projection_bytes=projection_bytes)
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(results=hits(projection["documents"]["records"][1:]), **data)


def test_the_rewrite_helper_alone_keeps_an_answering_memory(tmp_path, route):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    manifest_bytes, projection_bytes, projection = rewrite(memory_dir)
    data.update(memory_manifest=manifest_bytes, expected_memory_sha256=sha(manifest_bytes),
                projection_bytes=projection_bytes)
    assert resolver.resolve_results(results=hits(projection["documents"]["records"][1:]), **data)


def test_a_non_canonical_projection_encoding_is_refused(tmp_path, route):
    from sandbox.mind.step2 import cell as cell_mod
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    _, projection = load(memory_dir)
    compact = json.dumps(projection, sort_keys=True).encode("utf-8")
    manifest_bytes, _, _ = rewrite(memory_dir)
    manifest = json.loads(manifest_bytes)
    manifest["projection_sha256"] = sha(compact)
    manifest_bytes = cell_mod.canonical(manifest).encode("utf-8")
    data.update(memory_manifest=manifest_bytes, expected_memory_sha256=sha(manifest_bytes), projection_bytes=compact)
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(results=hits(projection["documents"]["records"]), **data)


def test_a_content_decision_never_opens_a_card_or_v6_memory(tmp_path, route):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    for schema in (resolver.PROJECTION_SCHEMA, resolver.CARD_PROJECTION_SCHEMA):
        manifest_bytes, projection_bytes, projection = rewrite(memory_dir,
                                                               lambda m: m.update(projection_schema=schema))
        data.update(memory_manifest=manifest_bytes, expected_memory_sha256=sha(manifest_bytes),
                    projection_bytes=projection_bytes)
        with pytest.raises(resolver.ResolveRefused):
            resolver.resolve_results(results=hits(projection["documents"]["records"]), **data)


def test_a_projection_not_pinned_by_the_manifest_is_refused(tmp_path, route):
    built, memory_dir, data = resolver_inputs(tmp_path, route)
    _, projection = load(memory_dir)
    projection["documents"]["records"] = projection["documents"]["records"][:-1]
    shorter = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    manifest_bytes, _, _ = rewrite(memory_dir, edit_projection=lambda p: p["documents"].update(
        records=p["documents"]["records"][:-1]))
    repinned = dict(data, memory_manifest=manifest_bytes, expected_memory_sha256=sha(manifest_bytes),
                    projection_bytes=shorter)
    assert resolver.resolve_results(results=hits(projection["documents"]["records"]), **repinned)
    data.update(projection_bytes=shorter)
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(results=hits(projection["documents"]["records"]), **data)


@pytest.mark.parametrize("stray", [{"content_decision_sha256": "0" * 64}, {"content_decision_bytes": b"{}"}])
def test_any_content_decision_part_keeps_a_v6_memory_closed(stray):
    from sandbox.mind.step2.test_shared_result_resolver import fixture as v6_fixture
    args, record = v6_fixture()
    assert resolver.resolve_results(**args)[0]["stored_text"] == record["stored_text"]
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**dict(args, **stray))
