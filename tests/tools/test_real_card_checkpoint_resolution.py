"""Fabricated real-format cards/local Git only; no live source or model."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_card_checkpoint as checkpoint
from sandbox.mind.step2 import shared_result_resolver as resolver
from tests.tools.test_wd_q4_real_card_generation import real_fixture
from tools import wd_q4_card_generation as producer


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def snapshot(real_fixture):
    pytest.importorskip("faiss")
    args, out, event, observation = real_fixture
    assert producer.prepare_card_generation(**args)["status"] == "STAGED"
    directory = out / args["generation_name"]
    projection = json.loads((directory / "projection.json").read_bytes())
    assert projection["synthetic"] is False
    decision = json.loads(args["decision_bytes"])
    pointer = dict(schema="wd.q4-card-snapshot.v1", generation=args["generation_name"],
        artifacts={p.relative_to(directory).as_posix(): sha(p.read_bytes())
                   for p in directory.rglob("*") if p.is_file()},
        decision_sha256=args["decision_sha256"], policy_sha256=args["policy_sha256"],
        roster_sha256=args["roster_sha256"], source_prefix=decision["source_prefix"])
    call = dict(enabled=True, pointer_bytes=canonical(pointer), expected_pointer_sha256=sha(canonical(pointer)),
        generation_dir=directory, decision_bytes=args["decision_bytes"], decision_sha256=args["decision_sha256"],
        **{k: args[k] for k in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
                                "card_policy_bytes", "source_path", "generation_path", "end_cursor")})
    record = projection["records"][0]
    hit = dict(ref_sha256=sha(record["provenance"]["event_ref"].encode()),
               doc_id_sha256=sha(record["record_id"].encode()),
               ref_kind="sha256_of_projected_metadata", canonical_event=False)
    resolve = dict(memory_manifest=(directory / "memory.json").read_bytes(),
        expected_memory_sha256=sha((directory / "memory.json").read_bytes()),
        projection_bytes=(directory / "projection.json").read_bytes(), results=[hit],
        query_utc="2026-11-03T00:00:00Z", card_decision_bytes=args["decision_bytes"],
        card_decision_sha256=args["decision_sha256"],
        **{k: args[k] for k in ("policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256")})
    return dict(call=call, resolve=resolve, pointer=pointer, directory=directory,
                record=record, hit=hit, observation=observation)


def test_actual_real_format_c3_snapshot_validates_without_writes(snapshot):
    before = {str(p): p.read_bytes() for p in snapshot["directory"].rglob("*") if p.is_file()}
    assert checkpoint.validate_card_snapshot(**snapshot["call"]) == {
        "status": "VALIDATED_STAGED", "completeness": "UNKNOWN"}
    assert before == {str(p): p.read_bytes() for p in snapshot["directory"].rglob("*") if p.is_file()}


def test_actual_real_format_results_resolve_exact_stored_text(snapshot):
    resolved = resolver.resolve_results(**snapshot["resolve"])
    assert resolved == [dict(snapshot["hit"], stored_text=snapshot["record"]["stored_text"])]
    assert snapshot["observation"]["public_origin_authentication"] == "UNKNOWN"


def test_real_results_deduplicate_after_verified_decision(snapshot):
    snapshot["resolve"]["results"] *= 2
    assert len(resolver.resolve_results(**snapshot["resolve"])) == 1


def _scope_posix_pointer_fixture(snapshot, monkeypatch, pointer_resolver):
    if os.name != "nt":
        # Only the existing synthetic generation is allowed; production's
        # Windows fixed-volume guard remains unchanged outside this test.
        from tests.tools.test_real_c2_enforcement import _secure_posix_fixture
        directory = snapshot["directory"]
        monkeypatch.setattr(pointer_resolver.checkpoint, "_secure",
            lambda path, *, missing_leaf=False: _secure_posix_fixture(
                path, directory, missing_leaf=missing_leaf))


def test_real_snapshot_reaches_actual_staged_pointer_resolver(snapshot, monkeypatch):
    from tools import wd_q4_public_pointer_resolver as pointer_resolver
    _scope_posix_pointer_fixture(snapshot, monkeypatch, pointer_resolver)
    actual = checkpoint.validate_card_snapshot
    checked = []
    def observe(**kwargs):
        result = actual(**kwargs)
        checked.append(result)
        return result
    monkeypatch.setattr(checkpoint, "validate_card_snapshot", observe)
    call = snapshot["call"].copy()
    call["root"] = call.pop("generation_dir").parent
    call["card_decision_bytes"] = call.pop("decision_bytes")
    call["card_decision_sha256"] = call.pop("decision_sha256")
    assert pointer_resolver.resolve_card_snapshot_pointer(**call)["status"] == "VALIDATED_STAGED"
    assert checked == [{"status": "VALIDATED_STAGED", "completeness": "UNKNOWN"}]


def test_staged_pointer_refuses_changed_synthetic_source(snapshot, monkeypatch):
    from tools import wd_q4_public_pointer_resolver as pointer_resolver
    _scope_posix_pointer_fixture(snapshot, monkeypatch, pointer_resolver)
    source = Path(snapshot["call"]["source_path"])
    data = source.read_bytes()
    source.write_bytes(b"!" + data[1:])
    actual = checkpoint.validate_card_snapshot
    checked = []
    def observe(**kwargs):
        result = actual(**kwargs)
        checked.append(result)
        return result
    monkeypatch.setattr(checkpoint, "validate_card_snapshot", observe)
    call = snapshot["call"].copy()
    call["root"] = call.pop("generation_dir").parent
    call["card_decision_bytes"] = call.pop("decision_bytes")
    call["card_decision_sha256"] = call.pop("decision_sha256")
    with pytest.raises(pointer_resolver.PublicPointerRefused):
        pointer_resolver.resolve_card_snapshot_pointer(**call)
    assert checked == [{"status": "INCOMPLETE"}]


@pytest.mark.parametrize("field", ["decision_bytes", "decision_sha256", "policy_sha256", "roster_sha256"])
def test_missing_or_wrong_decision_pin_never_enables_real_route(snapshot, field):
    value = None if field == "decision_bytes" else "0" * 64
    snapshot["call"][field] = value
    assert checkpoint.validate_card_snapshot(**snapshot["call"]) == {"status": "INCOMPLETE"}
    snapshot["resolve"]["card_" + field if field.startswith("decision") else field] = value
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**snapshot["resolve"])


@pytest.mark.parametrize("fault", ["forged_status", "synthetic_decision", "wrong_heads", "wrong_prefix"])
def test_recomputed_but_mismatched_decision_refuses(snapshot, fault):
    call = snapshot["call"]
    decision = json.loads(call["decision_bytes"])
    if fault == "forged_status":
        decision = {"status": "VALIDATED_STAGED"}
    elif fault == "synthetic_decision":
        decision.update(decision="enable_synthetic_card_route", synthetic=True)
        del decision["head_observation_sha256"]
    elif fault == "wrong_heads":
        decision["observed_heads"] = []
    else:
        decision["source_prefix"]["sha256"] = "0" * 64
    raw = canonical(decision)
    call.update(decision_bytes=raw, decision_sha256=sha(raw))
    pointer = snapshot["pointer"]
    pointer["decision_sha256"] = sha(raw)
    call.update(pointer_bytes=canonical(pointer), expected_pointer_sha256=sha(canonical(pointer)))
    assert checkpoint.validate_card_snapshot(**call) == {"status": "INCOMPLETE"}
    snapshot["resolve"].update(card_decision_bytes=raw, card_decision_sha256=sha(raw))
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**snapshot["resolve"])


@pytest.mark.parametrize("fault", ["source", "generation", "card_policy", "cell"])
def test_real_source_and_artifact_tampering_refuses(snapshot, fault):
    call = snapshot["call"]
    if fault == "card_policy":
        call["card_policy_bytes"] += b"tamper"
    else:
        path = (Path(call["source_path"]) if fault == "source" else
                Path(call["generation_path"]) if fault == "generation" else
                snapshot["directory"] / next(n for n in snapshot["pointer"]["artifacts"] if n.startswith("cells/")))
        data = path.read_bytes()
        path.write_bytes(b"!" + data[1:])
    assert checkpoint.validate_card_snapshot(**call) == {"status": "INCOMPLETE"}


@pytest.mark.parametrize("fault", ["wrong_ref", "extra", "canonical", "future_record", "invalid_time", "too_many"])
def test_real_result_contract_refuses_unknown_shapes(snapshot, fault):
    args = snapshot["resolve"]
    item = args["results"][0]
    if fault == "wrong_ref": item["ref_sha256"] = "0" * 64
    if fault == "extra": item["unexpected"] = "value"
    if fault == "canonical": item["canonical_event"] = True
    if fault == "future_record": args["query_utc"] = "2026-10-01T00:00:00Z"
    if fault == "invalid_time": args["query_utc"] = "invalid"
    if fault == "too_many": args["results"] *= 101
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**args)


def test_no_decision_keeps_real_results_off(snapshot):
    del snapshot["resolve"]["card_decision_bytes"]
    del snapshot["resolve"]["card_decision_sha256"]
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(**snapshot["resolve"])
