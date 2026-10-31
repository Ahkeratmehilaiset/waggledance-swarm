"""Synthetic pure-plan contract: no filesystem, subprocess, model or network."""
import dataclasses
import hashlib
import importlib
import json

import pytest

from tools.wd_q4_agent_query import VerifiedQ4Config


def pinned(value):
    raw = json.dumps(value, sort_keys=True).encode("utf-8")
    return raw, hashlib.sha256(raw).hexdigest()


@pytest.fixture
def valid_plan():
    lanes = ["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5"]
    roster = {"schema": "wd.q4-roster-snapshot.v1",
              "lanes": [{"agent": name, "agent_uuid": f"{i:08x}-1111-4111-8111-111111111111"}
                        for i, name in enumerate(lanes, 1)],
              "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": "00000005-1111-4111-8111-111111111111"}}
    policy = {"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["public-task"]}
    public_policy = {"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                     "public_types": ["message"]}
    policy_bytes, policy_sha = pinned(policy)
    roster_bytes, roster_sha = pinned(roster)
    public_bytes, public_sha = pinned(public_policy)
    config = VerifiedQ4Config(interpreter=r"C:\synthetic\python.exe", interpreter_sha256="1" * 64,
        bootstrap=r"C:\synthetic\bootstrap.py", bootstrap_sha256="2" * 64,
        verifier_sha256="3" * 64, runtime_root=r"C:\synthetic\runtime",
        runtime_manifest=r"C:\synthetic\manifest.json", runtime_manifest_sha256="4" * 64,
        runtime_generation="a" * 40, runtime_revision="b" * 40,
        memory=r"C:\synthetic\index\gen-1", descriptor=r"C:\synthetic\index\gen-1\descriptor.json",
        descriptor_sha256="5" * 64, lock_file=r"C:\synthetic\reader.lock", lock_timeout="1",
        timeout=10, scratch_root=r"C:\synthetic\scratch")
    return {"schema": "wd.q4-public-plan.v1", "config": config, "root": r"C:\synthetic\index",
            "expected_pointer_sha256": "6" * 64, "expected_source_sha256": "7" * 64,
            "expected_encoder_sha256": "8" * 64,
            "policy_bytes": policy_bytes, "policy_sha256": policy_sha,
            "roster_bytes": roster_bytes, "roster_sha256": roster_sha,
            "public_policy_bytes": public_bytes, "public_policy_sha256": public_sha}


def module():
    return importlib.import_module("tools.wd_q4_public_plan")


def test_default_off_does_not_inspect_input():
    class Trap(dict):
        def __iter__(self):
            raise AssertionError("disabled inspected plan")
    assert module().validate_public_plan(Trap()) == {"status": "OFF"}
    for enabled in (0, 1, None, "true"):
        with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
            module().validate_public_plan(Trap(), enabled=enabled)


def test_exact_bound_plan_returns_immutable_data_and_explicit_unknown(valid_plan):
    value = module().validate_public_plan(valid_plan, enabled=True)
    assert value.schema == "wd.q4-public-validated-plan.v1"
    assert value.status == "VALIDATED_STRUCTURE_ONLY"
    assert value.authority == "none"
    assert value.publisher_authentication == value.source_authentication == value.freshness == "UNKNOWN"
    assert value.pointer_target == value.source_target == value.encoder_target == "NOT_CHECKED"
    assert value.config == valid_plan["config"] and value.root == valid_plan["root"]
    assert value.expected_pointer_sha256 == valid_plan["expected_pointer_sha256"]
    assert value.policy_bytes == valid_plan["policy_bytes"]
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        value.expected_pointer_sha256 = "f" * 64


@pytest.mark.parametrize("field", ["schema", "config", "root", "expected_pointer_sha256",
    "expected_source_sha256", "expected_encoder_sha256", "policy_bytes", "policy_sha256",
    "roster_bytes", "roster_sha256", "public_policy_bytes", "public_policy_sha256"])
def test_missing_field_refuses(valid_plan, field):
    valid_plan.pop(field)
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan(valid_plan, enabled=True)


def test_extra_or_wrong_container_refuses(valid_plan):
    for value in ({**valid_plan, "extra": True}, list(valid_plan.items()), None):
        with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
            module().validate_public_plan(value, enabled=True)


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "expected_source_sha256", "expected_encoder_sha256",
                                    "policy_sha256", "roster_sha256", "public_policy_sha256"])
@pytest.mark.parametrize("bad", [None, True, "A" * 64, "f" * 63, "g" * 64, "0" * 65])
def test_invalid_pin_refuses(valid_plan, field, bad):
    valid_plan[field] = bad
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan(valid_plan, enabled=True)


@pytest.mark.parametrize("field", ["policy_bytes", "roster_bytes", "public_policy_bytes"])
def test_mismatched_or_ambiguous_policy_bytes_refuse(valid_plan, field):
    for value in (valid_plan[field] + b" ", bytearray(valid_plan[field]), None):
        with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
            module().validate_public_plan({**valid_plan, field: value}, enabled=True)


@pytest.mark.parametrize("root", ["relative", r"\\server\share", r"C:\a\..\b", r"C:\a\\b", r"C:\x:stream"])
def test_bad_or_ambiguous_root_refuses(valid_plan, root):
    valid_plan["root"] = root
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan(valid_plan, enabled=True)


def test_config_must_name_a_direct_generation_and_its_descriptor(valid_plan):
    config = valid_plan["config"]
    for bad in (dataclasses.replace(config, memory=r"C:\elsewhere\gen"),
                dataclasses.replace(config, memory=valid_plan["root"]),
                dataclasses.replace(config, memory=r"C:\synthetic\index\a\nested"),
                dataclasses.replace(config, descriptor=r"C:\synthetic\index\gen-1\other.json"),
                {"memory": config.memory}):
        with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
            module().validate_public_plan({**valid_plan, "config": bad}, enabled=True)


@pytest.mark.parametrize("memory,descriptor", [
    (r"C:\Synthetic\index\gen-1", r"C:\Synthetic\index\gen-1\descriptor.json"),
    (r"C:\synthetic\index\gen-1", r"C:\synthetic\index\gen-1\Descriptor.json"),
])
def test_plan_path_spelling_matches_exact_resolver_binding(valid_plan, memory, descriptor):
    """The plan cannot approve a spelling that the query adapter will reject."""
    config = dataclasses.replace(valid_plan["config"], memory=memory, descriptor=descriptor)
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan({**valid_plan, "config": config}, enabled=True)


def test_rehashed_invalid_policy_document_is_not_accepted(valid_plan):
    bad_bytes, bad_hash = pinned({"schema": "wd.mind-public-status-policy.v1",
                                   "public_tasks": ["not-approved"], "public_types": ["message"]})
    valid_plan.update(public_policy_bytes=bad_bytes, public_policy_sha256=bad_hash)
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan(valid_plan, enabled=True)


def test_pure_validation_performs_no_runtime_io(valid_plan, monkeypatch):
    import builtins
    import socket
    import subprocess
    mod = module()  # Python module loading itself precedes the no-I/O guard.

    def forbidden(*args, **kwargs):
        raise AssertionError("validator must not perform I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert mod.validate_public_plan(valid_plan, enabled=True).status == "VALIDATED_STRUCTURE_ONLY"


def test_original_plan_mutation_during_validation_cannot_change_result(valid_plan, monkeypatch):
    mod = module()
    original = valid_plan["policy_bytes"]
    verify = mod.adapter.strict_pins

    def mutate_after_check(*args):
        result = verify(*args)
        valid_plan["policy_bytes"] = b"different-unverified-bytes"
        return result

    monkeypatch.setattr(mod.adapter, "strict_pins", mutate_after_check)
    value = mod.validate_public_plan(valid_plan, enabled=True)
    assert value.policy_bytes == original
    assert value.policy_bytes != valid_plan["policy_bytes"]


@pytest.mark.parametrize("field", ["interpreter", "bootstrap", "runtime_root", "runtime_manifest", "memory",
                                    "descriptor", "lock_file", "scratch_root"])
@pytest.mark.parametrize("bad", [r"C:\synthetic\NUL", r"C:\synthetic\file.", r"C:\synthetic\file?.json"])
def test_all_config_paths_refuse_device_or_ambiguous_windows_names(valid_plan, field, bad):
    invalid = dataclasses.replace(valid_plan["config"], **{field: bad})
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan({**valid_plan, "config": invalid}, enabled=True)


@pytest.mark.parametrize("generation", ["Uppercase", "_leading", "x" * 65, "a@b"])
def test_memory_generation_obeys_public_resolver_name_grammar(valid_plan, generation):
    memory = valid_plan["root"] + "\\" + generation
    invalid = dataclasses.replace(valid_plan["config"], memory=memory, descriptor=memory + r"\descriptor.json")
    with pytest.raises(module().PublicPlanRefused, match="^public_plan_refused$"):
        module().validate_public_plan({**valid_plan, "config": invalid}, enabled=True)


@pytest.mark.parametrize("field", ["schema", "status", "authority", "publisher_authentication",
                                    "source_authentication", "freshness", "pointer_target", "source_target",
                                    "encoder_target"])
def test_structural_labels_cannot_be_set_via_constructor(valid_plan, field):
    value = module().validate_public_plan(valid_plan, enabled=True)
    # dataclasses.replace raises ValueError on some supported Python releases
    # and TypeError on Python 3.13 for init=False fields.
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(value, **{field: "TRUSTED"})
    inputs = {f.name: getattr(value, f.name) for f in dataclasses.fields(value) if f.init}
    with pytest.raises(TypeError):
        type(value)(**inputs, **{field: "TRUSTED"})
