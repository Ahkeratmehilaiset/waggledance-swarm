"""Genesis admission and one-shot runner binding using synthetic files only."""
import hashlib
import base64
import json
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools import wd_q4_genesis_bootstrap as genesis
from tools import wd_q4_status_prefix_runner as runner


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    source, side, checkpoint, private, model = [tmp_path / x for x in ("source", "side", "checkpoint", "private", "model")]
    for directory in (source, side, checkpoint, private, model):
        directory.mkdir()
    log = source / "events.jsonl"
    log.write_bytes(b'{"synthetic":true}\n')
    task = "lead-mind-production-20261031"
    names = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
    lanes = [{"agent": n, "agent_uuid": f"{i:08d}-1111-4111-8111-111111111111"} for i, n in enumerate(names, 1)]
    fleet = raw({"lanes": [x for x in lanes if x["agent"] != "codex-tools-1"], "tools_supervisor": lanes[1]})
    policy = raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [task]})
    public = raw({"schema": "wd.mind-public-status-policy.v1", "public_tasks": [task], "public_types": ["status"]})
    for key, data in (("_FLEET_SHA", fleet), ("_POLICY_SHA", policy), ("_PUBLIC_SHA", public)):
        monkeypatch.setattr(runner, key, frozenset({sha(data)}))
    calls = []
    from sandbox.mind.step2 import wd_q4_prefix_build_child as actual_child
    child = types.ModuleType("sandbox.mind.step2.wd_q4_prefix_build_child")
    child._plan_shape = actual_child._plan_shape
    def run_child(**kwargs):
        calls.append(kwargs)
        return {"status": "PUBLISHED_GENESIS", "pointer_sha256": "a" * 64, "generation": "synthetic-generation",
                "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
                "coverage": "caught_up_to_final_snapshot_not_history", "source_refusals": "NOT_AGGREGATED",
                "encoder_identity_source": "encode.model_identity", "plan_sha256": sha(kwargs["plan_bytes"]),
                "backend_isolation": "in_process_environment_restored_no_os_isolation",
                "model_assets": "caller_manifest_verified_before_load_reread_by_backend"}
    child.build_prefix_generation = run_child
    monkeypatch.setitem(sys.modules, child.__name__, child)
    args = dict(enabled=True, source_path=str(log), generation_path=str(side / "mind-generation.json"),
                generation="synthetic-generation", checkpoint_root=str(checkpoint), private_state_root=str(private),
                trusted_model_root=str(model), policy_bytes=policy, policy_sha256=sha(policy),
                public_policy_bytes=public, public_policy_sha256=sha(public), fleet_bytes=fleet,
                expected_fleet_sha256=sha(fleet), max_prefix_bytes=1024, max_bytes=1024, max_rows=10,
                plan_template={"schema": "wd.q4-prefix-build-plan.v3", "generation": "synthetic-generation",
                               "expected_pointer_sha256": None, "max_prefix_bytes": 1024, "max_windows": 1,
                               "timeout": 1, "encoder": {"model": "intfloat/multilingual-e5-small",
                               "revision": "a" * 40, "model_dir": str(model), "weights_sha256": "b" * 64,
                               "dim": 384, "assets": {"model.safetensors": "b" * 64}}, "fenced": True})
    return args, calls


def test_disabled_does_not_touch_inputs():
    assert genesis.bootstrap() == {"status": "OFF"}


def test_real_runner_gets_bound_cursor_and_exclusive_sidecar(synthetic):
    args, calls = synthetic
    result = genesis.bootstrap(**args)
    assert result["status"] == "PUBLISHED_GENESIS"
    assert len(calls) == 1
    assert calls[0]["config"].source.cursor.offset == 0
    assert calls[0]["start_prefix"].sha256 == sha(b"")
    assert json.loads(open(args["generation_path"], "rb").read()) == {"generation": args["generation"]}
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args)
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["oversize", "partial", "existing", "bad_pin", "windows", "budget",
                                    "empty", "pending", "old_pointer", "encoder", "timeout", "row_budget", "byte_budget"])
def test_invalid_inputs_do_not_call_runner(synthetic, change):
    args, calls = synthetic
    if change == "oversize":
        args["max_prefix_bytes"] = 1
    elif change == "partial":
        open(args["source_path"], "wb").write(b"partial")
    elif change == "existing":
        open(args["generation_path"], "wb").write(b"owned-before")
    elif change == "windows":
        args["plan_template"]["max_windows"] = 65
    elif change == "budget":
        args["max_prefix_bytes"] = 1024**3
        args["plan_template"].update(max_prefix_bytes=1024**3, max_windows=3)
    elif change == "empty":
        Path(args["source_path"]).write_bytes(b"")
    elif change == "pending":
        (Path(args["private_state_root"]) / runner._PENDING).write_bytes(b"existing")
    elif change == "old_pointer":
        (Path(args["checkpoint_root"]) / "q4-latest.json").write_bytes(b"existing")
    elif change == "encoder":
        args["plan_template"]["encoder"] = {}
    elif change == "timeout":
        args["plan_template"]["timeout"] = 31
    elif change == "row_budget":
        Path(args["source_path"]).write_bytes(b"x\n" * 11)
    elif change == "byte_budget":
        args["max_bytes"] = 2
    else:
        args["policy_sha256"] = "0" * 64
    with pytest.raises((genesis.GenesisRefused, genesis.GenesisUnknown)):
        genesis.bootstrap(**args)
    assert calls == []
    if change != "existing":
        assert not Path(args["generation_path"]).exists()


@pytest.mark.parametrize("change", ["append", "truncate", "rewrite", "replace", "sidecar", "tuple", "interrupt"])
def test_changes_after_runner_entry_are_unknown_and_not_retried(synthetic, monkeypatch, change):
    args, calls = synthetic
    module = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    original = module.build_prefix_generation
    def disrupt(**kwargs):
        result = original(**kwargs)
        log = Path(args["source_path"])
        if change == "append":
            with log.open("ab") as stream:
                stream.write(b"append\n")
        elif change == "truncate":
            log.write_bytes(b"x\n")
        elif change == "rewrite":
            log.write_bytes(b"x" * (log.stat().st_size - 1) + b"\n")
        elif change == "replace":
            replacement = log.with_name("replacement")
            replacement.write_bytes(log.read_bytes())
            replacement.replace(log)
        elif change == "sidecar":
            Path(args["generation_path"]).write_bytes(b'{"generation":"other"}')
        elif change == "tuple":
            (Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").write_bytes(b"tampered")
        else:
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(module, "build_prefix_generation", disrupt)
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args)
    assert len(calls) == 1
    assert Path(args["generation_path"]).exists()
    assert (Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").exists()
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args)
    assert len(calls) == 1


def test_concurrent_initializations_admit_at_most_one(synthetic):
    args, calls = synthetic
    def attempt():
        try:
            return genesis.bootstrap(**args)["status"]
        except genesis.GenesisUnknown:
            return "UNKNOWN"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert outcomes.count("PUBLISHED_GENESIS") == 1
    assert outcomes.count("UNKNOWN") == 1
    assert len(calls) == 1


def test_private_tuple_is_bound_and_exists_before_real_runner(synthetic, monkeypatch):
    args, calls = synthetic
    module = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    original = module.build_prefix_generation
    private_file = Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json"
    def inspect(**kwargs):
        doc = json.loads(private_file.read_bytes())
        assert doc["publication"] == "UNKNOWN"
        for name in ("anchor", "route", "plan"):
            content = base64.b64decode(doc[name]["bytes_b64"], validate=True)
            assert sha(content) == doc[name]["sha256"]
        assert base64.b64decode(doc["plan"]["bytes_b64"]) == kwargs["plan_bytes"]
        assert base64.b64decode(doc["anchor"]["bytes_b64"]) == kwargs["config"].source.anchor_bytes
        return original(**kwargs)
    monkeypatch.setattr(module, "build_prefix_generation", inspect)
    result = genesis.bootstrap(**args)
    assert result["genesis_tuple_sha256"] == sha(private_file.read_bytes())
    assert len(calls) == 1


def test_existing_private_tuple_is_never_overwritten(synthetic):
    args, calls = synthetic
    private_file = Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json"
    private_file.write_bytes(b"previous")
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args)
    assert private_file.read_bytes() == b"previous"
    assert not Path(args["generation_path"]).exists()
    assert calls == []


def test_opt_in_renewable_requires_real_recovery_proof(synthetic, monkeypatch):
    from tools import wd_q4_status_prefix_recovery as recovery
    args, calls = synthetic
    recovery_calls = []
    def refuse_without_real_pointer(**kwargs):
        recovery_calls.append(kwargs)
        raise ValueError("no real publication")
    monkeypatch.setattr(recovery, "complete_owned_publication", refuse_without_real_pointer)
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args, renewable=True)
    assert len(calls) == len(recovery_calls) == 1
    assert (Path(args["private_state_root"]) / runner._PENDING).exists()
