"""C2 single staged card snapshot; synthetic C3 fixture, no publication."""
import hashlib
import json

import pytest

from sandbox.mind.step2.shared_card_checkpoint import validate_card_snapshot


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def staged(tmp_path):
    # These are the actual C3 helper and producer at the task's frozen source head.
    from tests.tools.test_wd_q4_card_generation import prepared
    from tools.wd_q4_card_generation import prepare_card_generation
    pytest.importorskip("faiss")
    args, root = prepared(tmp_path)
    result = prepare_card_generation(**args)
    assert result["status"] == "STAGED"
    directory = root / args["generation_name"]
    artifacts = {str(path.relative_to(directory)).replace("\\", "/"): sha(path.read_bytes())
                 for path in directory.rglob("*") if path.is_file()}
    decision = json.loads(args["decision_bytes"])
    pointer = {"schema": "wd.q4-card-snapshot.v1", "generation": args["generation_name"],
               "artifacts": artifacts, "decision_sha256": args["decision_sha256"],
               "policy_sha256": args["policy_sha256"], "roster_sha256": args["roster_sha256"],
               "source_prefix": decision["source_prefix"]}
    call = dict(enabled=True, generation_dir=directory,
                decision_bytes=args["decision_bytes"], decision_sha256=args["decision_sha256"],
                policy_bytes=args["policy_bytes"], policy_sha256=args["policy_sha256"],
                roster_bytes=args["roster_bytes"], roster_sha256=args["roster_sha256"],
                card_policy_bytes=args["card_policy_bytes"], source_path=args["source_path"],
                generation_path=args["generation_path"], end_cursor=args["end_cursor"])
    return pointer, call, directory


def check(pointer, call):
    raw = canonical(pointer)
    return validate_card_snapshot(pointer_bytes=raw, expected_pointer_sha256=sha(raw), **call)


def test_default_off_never_reads_inputs():
    class Untouchable:
        def __getattribute__(self, name):
            raise AssertionError("input accessed while OFF")
    assert validate_card_snapshot(pointer_bytes=Untouchable()) == {"status": "OFF"}


def test_real_synthetic_c3_generation_is_validated_staged(staged):
    pointer, call, directory = staged
    before = {str(p): p.read_bytes() for p in directory.rglob("*") if p.is_file()}
    assert check(pointer, call) == {"status": "VALIDATED_STAGED", "completeness": "UNKNOWN"}
    assert before == {str(p): p.read_bytes() for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize("field", ["schema", "generation", "artifacts", "decision_sha256",
                                  "policy_sha256", "roster_sha256", "source_prefix"])
def test_missing_pointer_field_refuses(staged, field):
    pointer, call, _ = staged
    del pointer[field]
    assert check(pointer, call) == {"status": "INCOMPLETE"}


@pytest.mark.parametrize("field", ["previous", "history", "delta_projection_sha256", "unknown"])
def test_history_and_extra_fields_refuse(staged, field):
    pointer, call, _ = staged
    pointer[field] = None
    assert check(pointer, call) == {"status": "INCOMPLETE"}


@pytest.mark.parametrize("name", ["memory.json", "descriptor.json", "projection.json", "cell"])
def test_each_artifact_family_tamper_refuses(staged, name):
    pointer, call, directory = staged
    if name == "cell":
        name = next(key for key in pointer["artifacts"] if key.startswith("cells/"))
    path = directory / name
    path.write_bytes(path.read_bytes() + b"synthetic tamper")
    assert check(pointer, call) == {"status": "INCOMPLETE"}


@pytest.mark.parametrize("field", ["decision_sha256", "policy_sha256", "roster_sha256"])
def test_wrong_external_pin_refuses(staged, field):
    pointer, call, _ = staged
    call[field] = "0" * 64
    assert check(pointer, call) == {"status": "INCOMPLETE"}


def test_wrong_source_prefix_refuses(staged):
    pointer, call, _ = staged
    pointer["source_prefix"]["sha256"] = "0" * 64
    assert check(pointer, call) == {"status": "INCOMPLETE"}


def test_noncanonical_pointer_refuses(staged):
    pointer, call, _ = staged
    raw = json.dumps(pointer, indent=2).encode()
    assert validate_card_snapshot(pointer_bytes=raw, expected_pointer_sha256=sha(raw),
                                  **call) == {"status": "INCOMPLETE"}


@pytest.mark.parametrize("field", ["decision_bytes", "policy_bytes", "roster_bytes", "card_policy_bytes"])
def test_changed_pinned_document_bytes_refuse(staged, field):
    pointer, call, _ = staged
    call[field] = call[field] + b" "
    assert check(pointer, call) == {"status": "INCOMPLETE"}


@pytest.mark.parametrize("fault", ["missing_artifact", "extra_artifact", "source_offset",
                                  "source_generation", "v6_schema", "v6_memory"])
def test_closed_snapshot_and_version_bindings_refuse(staged, fault):
    pointer, call, directory = staged
    if fault == "missing_artifact":
        del pointer["artifacts"]["projection.json"]
    elif fault == "extra_artifact":
        pointer["artifacts"]["unknown.json"] = "0" * 64
    elif fault == "source_offset":
        pointer["source_prefix"]["offset"] += 1
    elif fault == "source_generation":
        pointer["source_prefix"]["generation"] = "different"
    elif fault == "v6_schema":
        pointer["schema"] = "wd.q4-shared-checkpoint.v4"
    else:
        path = directory / "memory.json"
        memory = json.loads(path.read_bytes())
        memory["projection_schema"] = "wd.bridge-mind-shared-projection.v6"
        path.write_bytes(canonical(memory))
        pointer["artifacts"]["memory.json"] = sha(path.read_bytes())
    assert check(pointer, call) == {"status": "INCOMPLETE"}


def test_existing_v6_resolver_still_refuses_card_memory_without_decision(staged):
    from sandbox.mind.step2 import shared_result_resolver as resolver
    _, call, directory = staged
    raw = (directory / "memory.json").read_bytes()
    with pytest.raises(resolver.ResolveRefused):
        resolver.resolve_results(memory_manifest=raw, expected_memory_sha256=sha(raw),
            projection_bytes=(directory / "projection.json").read_bytes(), results=[],
            query_utc="2026-11-03T00:00:00Z",
            policy_bytes=call["policy_bytes"], policy_sha256=call["policy_sha256"],
            roster_bytes=call["roster_bytes"], roster_sha256=call["roster_sha256"])


