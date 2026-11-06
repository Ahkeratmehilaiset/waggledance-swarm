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
    published = []
    from sandbox.mind.step2 import wd_q4_prefix_build_child as actual_child
    child = types.ModuleType("sandbox.mind.step2.wd_q4_prefix_build_child")
    child._plan_shape = actual_child._plan_shape
    child._actual = actual_child
    def run_child(*, batch_rows=False, **kwargs):
        from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, _file_identity, _open_log
        with _open_log(log) as opened:
            data = opened.read()
            data = data[:data.rfind(b"\n") + 1]
            cursor = BridgeCursor(offset=len(data), file_identity=_file_identity(opened), generation="synthetic-generation")
        published.append(BridgePrefixPin(cursor, sha(data)))
        calls.append(kwargs)
        return {"status": "PUBLISHED_GENESIS", "pointer_sha256": "a" * 64, "generation": "synthetic-generation",
                "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
                "coverage": "partial_ready_prefix" if kwargs.get("cutoff_utc") else "caught_up_to_final_snapshot_not_history", "source_refusals": "NOT_AGGREGATED",
                "encoder_identity_source": "encode.model_identity", "plan_sha256": sha(kwargs["plan_bytes"]),
                "backend_isolation": "in_process_environment_restored_no_os_isolation",
                "model_assets": "caller_manifest_verified_before_load_reread_by_backend"}
    child.build_prefix_generation = run_child
    monkeypatch.setitem(sys.modules, child.__name__, child)
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    child._real_accessor = checkpoint_module.read_verified_prefix_pointer
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer",
                        lambda *a, **k: ({"anchor_sha256": sha(calls[-1]["config"].source.anchor_bytes)}, published[-1]))
    args = dict(enabled=True, source_path=str(log), generation_path=str(side / "mind-generation.json"),
                generation="synthetic-generation", checkpoint_root=str(checkpoint), private_state_root=str(private),
                trusted_model_root=str(model), policy_bytes=policy, policy_sha256=sha(policy),
                public_policy_bytes=public, public_policy_sha256=sha(public), fleet_bytes=fleet,
                expected_fleet_sha256=sha(fleet), max_prefix_bytes=1024, max_bytes=1024, max_rows=10, min_headroom_bytes=1,
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
                                    "empty", "pending", "old_pointer", "encoder", "timeout", "row_budget", "byte_budget",
                                    "private_state", "private_pending", "headroom"])
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
    elif change == "private_state":
        (Path(args["private_state_root"]) / "prefix-private-state.json").write_bytes(b"existing")
    elif change == "private_pending":
        (Path(args["private_state_root"]) / "prefix-private-state.pending").write_bytes(b"existing")
    elif change == "headroom":
        args["min_headroom_bytes"] = args["max_prefix_bytes"]
    else:
        args["policy_sha256"] = "0" * 64
    with pytest.raises((genesis.GenesisRefused, genesis.GenesisUnknown)):
        genesis.bootstrap(**args)
    assert calls == []
    if change != "existing":
        assert not Path(args["generation_path"]).exists()


@pytest.mark.parametrize("change", ["truncate", "rewrite", "replace", "sidecar", "tuple", "interrupt"])
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


def test_default_headroom_refuses_before_sidecar(synthetic):
    args, calls = synthetic
    del args["min_headroom_bytes"]
    with pytest.raises(genesis.GenesisRefused):
        genesis.bootstrap(**args)
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


def test_append_after_verified_published_snapshot_is_allowed(synthetic, monkeypatch):
    args, calls = synthetic
    module = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    original = module.build_prefix_generation
    def append_after_publication(**kwargs):
        result = original(**kwargs)
        with open(args["source_path"], "ab") as stream:
            stream.write(b"later synthetic record\n")
        return result
    monkeypatch.setattr(module, "build_prefix_generation", append_after_publication)
    result = genesis.bootstrap(**args)
    assert result["status"] == "PUBLISHED_GENESIS"
    assert "end_pin" not in result and "cursor" not in result
    assert "genesis_snapshot_sha256" not in result
    assert len(calls) == 1


def test_append_before_publication_is_bound_by_published_end_pin(synthetic, monkeypatch):
    args, calls = synthetic
    module = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    original = module.build_prefix_generation
    def append_before(**kwargs):
        with open(args["source_path"], "ab") as stream:
            stream.write(b"new synthetic record\n")
        return original(**kwargs)
    monkeypatch.setattr(module, "build_prefix_generation", append_before)
    assert genesis.bootstrap(**args)["status"] == "PUBLISHED_GENESIS"
    assert len(calls) == 1


def test_rewriting_appended_published_portion_is_unknown(synthetic, monkeypatch):
    args, calls = synthetic
    module = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    original = module.build_prefix_generation
    def rewrite_new_portion(**kwargs):
        path = Path(args["source_path"])
        with path.open("ab") as stream:
            stream.write(b"new synthetic record\n")
        result = original(**kwargs)
        with path.open("r+b") as stream:
            stream.seek(-2, 2)
            stream.write(b"X")
        return result
    monkeypatch.setattr(module, "build_prefix_generation", rewrite_new_portion)
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args)
    assert len(calls) == 1


@pytest.mark.parametrize("fault", ["identity", "generation", "early", "over_budget", "digest", "midline", "anchor"])
def test_unbound_published_end_is_unknown(synthetic, monkeypatch, fault):
    from dataclasses import replace
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    args, calls = synthetic
    original = checkpoint_module.read_verified_prefix_pointer
    def bad_end(*positional, **keywords):
        view, pin = original(*positional, **keywords)
        if fault == "identity":
            pin = replace(pin, cursor=replace(pin.cursor, file_identity="windows-v1:00000000:0000000000000000"))
        elif fault == "generation":
            pin = replace(pin, cursor=replace(pin.cursor, generation="other"))
        elif fault == "early":
            pin = replace(pin, cursor=replace(pin.cursor, offset=pin.cursor.offset - 1))
        elif fault == "over_budget":
            pin = replace(pin, cursor=replace(pin.cursor, offset=args["max_prefix_bytes"] + 1))
        elif fault == "digest":
            pin = replace(pin, sha256="0" * 64)
        elif fault == "midline":
            path = Path(args["source_path"])
            with path.open("ab") as stream:
                stream.write(b"x\n")
            data = path.read_bytes()[:-1]
            pin = replace(pin, cursor=replace(pin.cursor, offset=len(data)), sha256=sha(data))
        else:
            view = {**view, "anchor_sha256": "0" * 64}
        return view, pin
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer", bad_end)
    with pytest.raises(genesis.GenesisUnknown):
        genesis.bootstrap(**args)
    assert len(calls) == 1


def test_partial_tail_kept_for_later_with_pinned_cutoff(synthetic):
    args, calls = synthetic
    path = Path(args["source_path"])
    path.write_bytes(path.read_bytes() + b"partial")
    result = genesis.bootstrap(**args, cutoff_utc="2026-10-31T13:00:00Z")
    assert result["status"] == "PUBLISHED_GENESIS" and result["coverage"] == "partial_ready_prefix"
    assert calls[0]["cutoff_utc"].tzinfo is not None
    private = json.loads((Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").read_bytes())
    route_bytes = base64.b64decode(private["route"]["bytes_b64"])
    assert sha(route_bytes) == private["route"]["sha256"]
    assert json.loads(route_bytes)["cutoff_utc"] == "2026-10-31T13:00:00Z"


def test_partial_genesis_is_explicit_and_never_claims_caught_up(synthetic, monkeypatch):
    args, calls = synthetic
    path = Path(args["source_path"])
    path.write_bytes(b"row\n" * 11)
    args["max_rows"] = 10
    module = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    # This checks admission and reporting; the real multi-call producer test below
    # supplies an actual checkpoint and bounded continuation.
    original = module.build_prefix_generation
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    from dataclasses import replace
    original_accessor = checkpoint_module.read_verified_prefix_pointer
    def first_ten(*positional, **keywords):
        view, pin = original_accessor(*positional, **keywords)
        return view, replace(pin, cursor=replace(pin.cursor, offset=40), sha256=sha(b"row\n" * 10))
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer", first_ten)
    result = genesis.bootstrap(**args, cutoff_utc="2026-10-31T13:00:00Z", batch_rows=True,
                               allow_partial_genesis=True)
    assert result["coverage"] == "partial_ready_prefix"
    assert result["genesis_initial_prefix_fully_covered"] is False
    assert len(calls) == 1


def test_partial_genesis_requires_explicit_cutoff_and_batch(synthetic):
    args, calls = synthetic
    with pytest.raises(genesis.GenesisRefused):
        genesis.bootstrap(**args, allow_partial_genesis=True)
    assert calls == [] and not Path(args["generation_path"]).exists()


@pytest.mark.skipif(sys.platform != "win32", reason="real private checkpoint requires Windows fixed volume")
def test_actual_producer_checkpoint_and_partial_continuation(synthetic, monkeypatch):
    """Real runner, child, producer, checkpoint and reader; synthetic encoder only."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    from tools import wd_mind_anchor_issuer as anchor_issuer
    from tools.wd_q4_roster_from_fleet import build_roster_snapshot
    from waggledance.core.bridge_log_reader import read_bridge_log
    from dataclasses import asdict
    args, _ = synthetic
    fake = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    child = fake._actual
    monkeypatch.setitem(sys.modules, fake.__name__, child)
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer", fake._real_accessor)
    monkeypatch.setattr(child, "_encoder", lambda encoder, identity:
                        lambda texts: np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384))
    model = Path(args["trusted_model_root"])
    weights = b"synthetic-weights-not-a-model"
    (model / "model.safetensors").write_bytes(weights)
    args["plan_template"]["encoder"].update(weights_sha256=sha(weights), assets={"model.safetensors": sha(weights)})
    args["plan_template"].update(max_prefix_bytes=65536, max_windows=4, timeout=10)
    args.update(max_prefix_bytes=65536, max_bytes=65536, max_rows=10)
    fleet = json.loads(args["fleet_bytes"])
    uuid = next(row["agent_uuid"] for row in fleet["lanes"] if row["agent"] == "codex-lead-1")
    event = {"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1", "agent_uuid": uuid,
             "type": "status", "status": "info", "task_id": "lead-mind-production-20261031",
             "to": "", "message": "", "paths": [], "payload": {"public_status": True}}
    first = raw(event) + b"\n"
    second = raw({**event, "ts_utc": "2026-10-31T12:01:00Z"}) + b"\n"
    log = Path(args["source_path"])
    log.write_bytes(first + second[:12])
    result = genesis.bootstrap(**args, cutoff_utc="2026-10-31T13:00:00Z", renewable=True)
    assert result["status"] == "PUBLISHED_GENESIS" and result["coverage"] == "partial_ready_prefix"
    roster_bytes, roster_sha = build_roster_snapshot(args["fleet_bytes"], args["expected_fleet_sha256"], enabled=True)
    _, prefix = checkpoint_module.read_verified_prefix_pointer(Path(args["checkpoint_root"]),
        frozenset({"lead-mind-production-20261031"}), expected_pointer_sha256=result["pointer_sha256"],
        policy_bytes=args["policy_bytes"], policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes,
        roster_sha256=roster_sha, expected_public_policy_sha256=args["public_policy_sha256"])
    assert prefix.cursor.offset == len(first) and prefix.sha256 == sha(first)
    with log.open("ab") as stream:
        stream.write(second[12:])
    resumed = read_bridge_log(log, cursor=prefix.cursor, generation_path=args["generation_path"],
        expected_prefix_sha256=prefix.sha256, max_prefix_bytes=65536, max_bytes=65536, max_rows=10)
    assert resumed.candidate_cursor.offset == len(first) + len(second)
    private_tuple = json.loads((Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").read_bytes())
    route = json.loads(base64.b64decode(private_tuple["route"]["bytes_b64"]))
    plan = json.loads(base64.b64decode(private_tuple["plan"]["bytes_b64"]))
    next_anchor, next_anchor_sha = anchor_issuer.issue_anchor(enabled=True, source_path=args["source_path"],
        generation_path=args["generation_path"], cursor=prefix.cursor, policy_bytes=args["policy_bytes"],
        policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes, roster_sha256=roster_sha,
        public_policy_bytes=args["public_policy_bytes"], public_policy_sha256=args["public_policy_sha256"])
    plan.update(generation="synthetic-generation-2", expected_pointer_sha256=result["pointer_sha256"])
    plan["pins"]["anchor_sha256"] = next_anchor_sha
    plan_bytes = raw(plan)
    route.update(cursor=asdict(prefix.cursor), prefix_sha256=prefix.sha256, anchor_sha256=next_anchor_sha,
                 plan_sha256=sha(plan_bytes))
    route_bytes = raw(route)
    continued = runner.run_once(enabled=True, route_bytes=route_bytes, expected_route_sha256=sha(route_bytes),
        plan_bytes=plan_bytes, policy_bytes=args["policy_bytes"], public_policy_bytes=args["public_policy_bytes"],
        fleet_bytes=args["fleet_bytes"], expected_fleet_sha256=args["expected_fleet_sha256"],
        roster_bytes=roster_bytes, anchor_bytes=next_anchor, renewable=True)
    assert continued["status"] == "PUBLISHED_HISTORY"
    _, final = checkpoint_module.read_verified_prefix_pointer(Path(args["checkpoint_root"]),
        frozenset({"lead-mind-production-20261031"}), expected_pointer_sha256=continued["pointer_sha256"],
        policy_bytes=args["policy_bytes"], policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes,
        roster_sha256=roster_sha, expected_public_policy_sha256=args["public_policy_sha256"])
    assert final.cursor.offset == len(first) + len(second) and final.sha256 == sha(first + second)


def test_real_batch_reader_reaches_64_windows_with_multiple_rows(tmp_path):
    from datetime import datetime, timezone
    from sandbox.mind.step2.test_public_catchup_batch_red import _fixture, _batch_read, _verify
    from sandbox.mind.step2.test_status_public_filter import event
    events = [event(ts_utc="2026-10-31T11:58:00Z") for _ in range(129)]
    pins, path, anchor, start, raw_bytes = _fixture(tmp_path, events)
    cutoff = datetime(2026, 10, 31, 13, tzinfo=timezone.utc)
    result = _batch_read(path, pins, anchor, start, cutoff, max_rows=2, max_windows=64)
    assert result["window_count"] == 64 and result["end_reason"] == "max_windows"
    assert result["candidate_cursor"].offset == raw_bytes.rfind(b"\n", 0, len(raw_bytes) - 2) + 1
    assert _verify(result, pins, start, cutoff)


@pytest.mark.skipif(sys.platform != "win32", reason="real private checkpoint requires Windows fixed volume")
def test_real_batched_genesis_and_fenced_continuations_cover_every_row(synthetic, monkeypatch):
    """Each run has one two-row window; real pointers chain until all seven rows land."""
    import inspect
    from dataclasses import asdict
    np = pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    from tools import wd_mind_anchor_issuer as anchor_issuer
    from tools.wd_q4_roster_from_fleet import build_roster_snapshot
    args, _ = synthetic
    fake = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    child = fake._actual
    if "batch_rows" not in inspect.signature(child.build_prefix_generation).parameters:
        pytest.skip("batch-capable child commit not yet integrated")
    monkeypatch.setitem(sys.modules, fake.__name__, child)
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer", fake._real_accessor)
    monkeypatch.setattr(child, "_encoder", lambda encoder, identity:
                        lambda texts: np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384))
    model = Path(args["trusted_model_root"])
    weights = b"synthetic-weights-not-a-model"
    (model / "model.safetensors").write_bytes(weights)
    args["plan_template"]["encoder"].update(weights_sha256=sha(weights), assets={"model.safetensors": sha(weights)})
    args["plan_template"].update(max_prefix_bytes=65536, max_windows=1, timeout=10)
    args.update(max_prefix_bytes=65536, max_bytes=1024, max_rows=2)
    fleet = json.loads(args["fleet_bytes"])
    uuid = next(row["agent_uuid"] for row in fleet["lanes"] if row["agent"] == "codex-lead-1")
    event = {"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1", "agent_uuid": uuid,
             "type": "status", "status": "info", "task_id": "lead-mind-production-20261031",
             "to": "", "message": "", "paths": [], "payload": {"public_status": True}}
    records = [raw({**event, "ts_utc": f"2026-10-31T12:00:{i:02d}Z"}) + b"\n" for i in range(7)]
    log = Path(args["source_path"])
    log.write_bytes(b"".join(records))
    result = genesis.bootstrap(**args, cutoff_utc="2026-10-31T13:00:00Z", batch_rows=True,
                               allow_partial_genesis=True, renewable=True)
    assert result["coverage"] == "partial_ready_prefix" and not result["genesis_initial_prefix_fully_covered"]
    roster_bytes, roster_sha = build_roster_snapshot(args["fleet_bytes"], args["expected_fleet_sha256"], enabled=True)
    def verified(report):
        return checkpoint_module.read_verified_prefix_pointer(Path(args["checkpoint_root"]),
            frozenset({"lead-mind-production-20261031"}), expected_pointer_sha256=report["pointer_sha256"],
            policy_bytes=args["policy_bytes"], policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes,
            roster_sha256=roster_sha, expected_public_policy_sha256=args["public_policy_sha256"])[1]
    prefix = verified(result)
    assert prefix.cursor.offset == len(b"".join(records[:2]))
    private_tuple = json.loads((Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").read_bytes())
    route = json.loads(base64.b64decode(private_tuple["route"]["bytes_b64"]))
    plan = json.loads(base64.b64decode(private_tuple["plan"]["bytes_b64"]))
    offsets = [prefix.cursor.offset]
    for index in range(2, 5):
        anchor, anchor_sha = anchor_issuer.issue_anchor(enabled=True, source_path=args["source_path"],
            generation_path=args["generation_path"], cursor=prefix.cursor, policy_bytes=args["policy_bytes"],
            policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes, roster_sha256=roster_sha,
            public_policy_bytes=args["public_policy_bytes"], public_policy_sha256=args["public_policy_sha256"])
        plan.update(generation=f"synthetic-generation-{index}", expected_pointer_sha256=result["pointer_sha256"])
        plan["pins"]["anchor_sha256"] = anchor_sha
        plan_bytes = raw(plan)
        route.update(cursor=asdict(prefix.cursor), prefix_sha256=prefix.sha256, anchor_sha256=anchor_sha,
                     plan_sha256=sha(plan_bytes))
        route_bytes = raw(route)
        result = runner.run_once(enabled=True, route_bytes=route_bytes, expected_route_sha256=sha(route_bytes),
            plan_bytes=plan_bytes, policy_bytes=args["policy_bytes"], public_policy_bytes=args["public_policy_bytes"],
            fleet_bytes=args["fleet_bytes"], expected_fleet_sha256=args["expected_fleet_sha256"],
            roster_bytes=roster_bytes, anchor_bytes=anchor, renewable=True)
        prefix = verified(result)
        offsets.append(prefix.cursor.offset)
        assert prefix.sha256 == sha(b"".join(records)[:prefix.cursor.offset])
        if index < 4:
            assert result["coverage"] == "partial_ready_prefix"
    assert result["coverage"] == "caught_up_to_final_snapshot_not_history"
    assert offsets == [len(b"".join(records[:n])) for n in (2, 4, 6, 7)]
    projection = json.loads((Path(args["checkpoint_root"]) / "synthetic-generation-4" / "projection.json").read_bytes())
    assert len(projection["records"]) == 7
    assert len({record["record_id"] for record in projection["records"]}) == 7
