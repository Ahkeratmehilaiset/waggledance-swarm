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


REAL_PRESCAN = getattr(genesis, "_prescan_public_rows", None)


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
    # The fixture's one-line log is no bridge event; F2 tests and the real-producer tests use the real pre-scan.
    monkeypatch.setattr(genesis, "_prescan_public_rows", lambda *args, **kwargs: None, raising=False)
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


@pytest.mark.parametrize("fault", ["identity", "length", "hash", "incomplete"])
def test_independent_source_pin_mismatch_refuses_before_sidecar(synthetic, fault):
    from waggledance.core.bridge_log_reader import _file_identity, _open_log
    args, calls = synthetic
    path = Path(args["source_path"])
    data = path.read_bytes()
    with _open_log(path) as stream:
        identity = _file_identity(stream)
    pin = dict(expected_source_identity=identity, expected_source_prefix_bytes=len(data),
               expected_source_prefix_sha256=sha(data))
    if fault == "identity":
        pin["expected_source_identity"] = "windows-v1:00000000:0000000000000000"
    elif fault == "length":
        pin["expected_source_prefix_bytes"] = len(data) + 100
    elif fault == "hash":
        pin["expected_source_prefix_sha256"] = "0" * 64
    else:
        del pin["expected_source_prefix_sha256"]
    with pytest.raises(genesis.GenesisRefused):
        genesis.bootstrap(**args, **pin)
    assert calls == [] and not Path(args["generation_path"]).exists()


def test_independent_source_pin_allows_append_after_frozen_prefix(synthetic):
    from waggledance.core.bridge_log_reader import _file_identity, _open_log
    args, calls = synthetic
    path = Path(args["source_path"])
    data = path.read_bytes()
    with _open_log(path) as stream:
        identity = _file_identity(stream)
    with path.open("ab") as stream:
        stream.write(b"later synthetic row\n")
    result = genesis.bootstrap(**args, expected_source_identity=identity, expected_source_prefix_bytes=len(data),
                               expected_source_prefix_sha256=sha(data))
    assert result["status"] == "PUBLISHED_GENESIS" and len(calls) == 1


def test_source_pin_rechecked_after_pre_admission_rewrite(synthetic, monkeypatch):
    from waggledance.core.bridge_log_reader import _file_identity, _open_log
    args, calls = synthetic
    path = Path(args["source_path"])
    data = path.read_bytes()
    with _open_log(path) as stream:
        identity = _file_identity(stream)
    original = genesis._same_source
    checked = []
    def rewrite_after_first_check(*pos, **kwargs):
        result = original(*pos, **kwargs)
        checked.append(True)
        if len(checked) == 1:
            path.write_bytes(b"x" * (len(data) - 1) + b"\n")
        return result
    monkeypatch.setattr(genesis, "_same_source", rewrite_after_first_check)
    with pytest.raises(genesis.GenesisRefused):
        genesis.bootstrap(**args, expected_source_identity=identity, expected_source_prefix_bytes=len(data),
                          expected_source_prefix_sha256=sha(data))
    assert len(checked) >= 2 and calls == [] and not Path(args["generation_path"]).exists()


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
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
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
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    statuses = ("requested", "answered", "milestone", "info", "handoff", "done", "delivered")
    def vector(text):
        value = np.zeros(384, dtype=np.float32)
        for index, status in enumerate(statuses):
            if status in text.split():
                value[index] = 1
        return value
    monkeypatch.setattr(child, "_encoder", lambda encoder, identity:
                        lambda texts: np.stack([vector(text) for text in texts]))
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
    records = [raw({**event, "status": status, "ts_utc": f"2026-10-31T12:{i:02d}:00Z"}) + b"\n"
               for i, status in enumerate(statuses)]
    records.append(raw({**event, "ts_utc": "2026-10-31T12:07:00Z", "to": "fable-5",
                        "message": "synthetic-private-must-not-be-returned"}) + b"\n")
    log = Path(args["source_path"])
    log.write_bytes(b"".join(records))
    from waggledance.core.bridge_log_reader import _file_identity, _open_log
    with _open_log(log) as source:
        source_identity = _file_identity(source)
    result = genesis.bootstrap(**args, cutoff_utc="2026-10-31T13:00:00Z", batch_rows=True,
        allow_partial_genesis=True, renewable=True, expected_source_identity=source_identity,
        expected_source_prefix_bytes=sum(map(len, records)), expected_source_prefix_sha256=sha(b"".join(records)))
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
    assert offsets == [len(b"".join(records[:n])) for n in (2, 4, 6, 8)]
    projection = json.loads((Path(args["checkpoint_root"]) / "synthetic-generation-4" / "projection.json").read_bytes())
    assert len(projection["records"]) == 7
    assert len({record["record_id"] for record in projection["records"]}) == 7
    # The real in-process Q4 query/resolve boundary sees all four windows; only
    # the E5 encoder is replaced by this deterministic synthetic encoder.
    from sandbox.mind.step2 import wd_q4_private_read as reader
    root = Path(args["checkpoint_root"])
    active = root / "synthetic-generation-4"
    active_bytes = {name: (active / name).read_bytes() for name in checkpoint_module.ARTIFACTS.values()}
    pointer_bytes = (root / checkpoint_module.POINTER).read_bytes()
    pointer = json.loads(pointer_bytes)
    history = {p.name: p.read_bytes() for pattern in ("q4-pointer-*.json", "q4-delta-*.json")
               for p in root.glob(pattern)}
    for name in ("synthetic-generation", "synthetic-generation-2", "synthetic-generation-3", "synthetic-generation-4"):
        for artifact in checkpoint_module.ARTIFACTS.values():
            history[f"{name}/{artifact}"] = (root / name / artifact).read_bytes()
    memory = json.loads(active_bytes["memory.json"])
    cells = {}
    for name, info in memory["cells"].items():
        cell = active / "cells" / name
        cell_gen = cell / f"gen-{info['generation']:06d}"
        cells[name] = {"pointer": (cell / "current.json").read_bytes(), "log": (cell / "LOG.jsonl").read_bytes(),
            "artifacts": {"index": (cell_gen / "faiss" / f"cell_{name}" / "index.faiss").read_bytes(),
                          "meta": (cell_gen / "faiss" / f"cell_{name}" / "meta.json").read_bytes(),
                          "vectors": (cell_gen / "vectors.f32").read_bytes(),
                          "provenance": (cell_gen / "provenance.jsonl").read_bytes(),
                          "receipt": (cell_gen / "receipt.json").read_bytes()}}
    descriptor = json.loads(active_bytes["descriptor.json"])
    monkeypatch.setattr(reader, "RUNTIME_IDENTITY", lambda **kwargs: dict(descriptor["encoder"]))
    class Encoder:
        def query(self, text):
            return vector(text)
    monkeypatch.setattr(reader, "ENCODER_FACTORY", lambda **kwargs: Encoder())
    query_args = dict(pointer_bytes=pointer_bytes, expected_pointer_sha256=sha(pointer_bytes),
        approved_tasks=frozenset({"lead-mind-production-20261031"}), policy_bytes=args["policy_bytes"],
        policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes, roster_sha256=roster_sha,
        expected_public_policy_sha256=args["public_policy_sha256"], public_policy_bytes=args["public_policy_bytes"],
        expected_source_sha256=pointer["anchor_sha256"], expected_encoder_sha256=sha(raw(descriptor["encoder"])),
        active_artifact_bytes=active_bytes, cell_snapshots=cells, history_bytes=history, model_dir=str(model),
        expected_runtime_revision=descriptor["encoder"]["revision"], enabled=True)
    visited = []
    for module, names in ((checkpoint_module, ("_parse_pointer_snapshot", "_bind_active_artifact_bytes",
                           "_freeze_history_snapshot", "_copy_snapshot_cells", "_validate_pointer_history",
                           "_preflight_public_snapshot", "_validate_projection_cell_views")),
                          (reader, ("read_descriptor_bytes", "read_query", "bind_runtime_encoder",
                                    "_validate_bound_memory", "private_read_snapshot"))):
        for name in names:
            original = getattr(module, name)
            def watch(*pos, _fn=original, _name=name, **kw):
                visited.append(_name)
                return _fn(*pos, **kw)
            monkeypatch.setattr(module, name, watch)
    for status in statuses:
        try:
            reply = checkpoint_module.query_public_snapshot(**query_args,
                query_bytes=raw({"situation": f"locate {status} event", "time": "2026-10-31T13:00:00Z"}))
        except checkpoint_module.CheckpointRefused:
            pytest.fail(f"synthetic query refused after {visited}")
        assert reply["status"] == "ANSWER"
        assert any(item["stored_text"].split()[1] == status for item in reply["results"])
        assert all(not item["canonical_event"] for item in reply["results"])
        assert "synthetic-private-must-not-be-returned" not in json.dumps(reply)
    absent = checkpoint_module.query_public_snapshot(**query_args,
        query_bytes=raw({"situation": "unfindableunique", "time": "2026-10-31T13:00:00Z"}))
    assert absent["status"] == "ABSTAIN" and absent["results"] == []
    from sandbox.mind.step2 import shared_projection, q4
    # Distinct timestamps within different minutes can produce distinct refs
    # while their searchable text remains identical; answers show at most 3.
    left = projection["records"][0]
    fields = {"ts_utc": "2026-10-31T12:40:00Z", **left["meta"]}
    one = shared_projection._record(fields)
    fields["ts_utc"] = "2026-10-31T12:41:00Z"
    two = shared_projection._record(fields)
    assert one["stored_text"] == two["stored_text"] and one["record_id"] != two["record_id"]
    assert q4.RULE["shown_max"] == 3 and q4.RULE["agree_depth"] == 3


# F2 (RCO1 genesis source-pin review 11-06 21:01Z): when the coverable windows of the first run hold no public row
# of an approved task, the producer refuses after the sidecar, tuple and runner state exist, so the one-shot is
# spent. Genesis now pre-scans those exact windows before admission and refuses without any one-shot state.
def f2_rows(args, kinds):
    """Synthetic bridge rows: "public" is a public status of the approved task; "other_task" and "private" are
    structurally valid rows the public projection excludes."""
    fleet = json.loads(args["fleet_bytes"])
    uuid = next(row["agent_uuid"] for row in fleet["lanes"] if row["agent"] == "codex-lead-1")
    base = {"ts_utc": "2026-10-31T12:00:00Z", "agent": "codex-lead-1", "agent_uuid": uuid, "type": "status",
            "status": "info", "task_id": "lead-mind-production-20261031", "to": "", "message": "", "paths": [],
            "payload": {"public_status": True}}
    rows = []
    for index, kind in enumerate(kinds):
        event = {**base, "ts_utc": f"2026-10-31T12:{index:02d}:00Z"}
        if kind == "other_task":
            event["task_id"] = "lead-unrelated-20261031"
        elif kind == "private":
            event.update(to="fable-5", message="synthetic-private-must-not-be-returned")
        elif kind == "young":
            event["ts_utc"] = "2026-10-31T12:59:30Z"
        rows.append(raw(event) + b"\n")
    return rows


def f2_args(args, rows, max_windows):
    pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    Path(args["source_path"]).write_bytes(b"".join(rows))
    args["plan_template"].update(max_prefix_bytes=65536, max_windows=max_windows)
    args.update(max_prefix_bytes=65536, max_bytes=4096, max_rows=2)
    return dict(args, cutoff_utc="2026-10-31T13:00:00Z", batch_rows=True, allow_partial_genesis=True)


def no_one_shot_state(args):
    """Nothing of the one-shot exists and the pre-scan probe left nothing behind."""
    return (not Path(args["generation_path"]).exists()
            and sorted(Path(args["private_state_root"]).iterdir()) == []
            and sorted(Path(args["checkpoint_root"]).iterdir()) == [])


EMPTY_EARLY = ["other_task", "private", "other_task", "private"]


def test_f2_no_public_row_in_the_coverable_windows_refuses_before_any_one_shot_state(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, EMPTY_EARLY + ["public"]), max_windows=2)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)
    # The run was not spent: the same genesis with windows that reach the public row is admitted once.
    run["plan_template"]["max_windows"] = 3
    assert genesis.bootstrap(**run)["status"] == "PUBLISHED_GENESIS" and len(calls) == 1


def test_f2_a_public_row_in_a_later_coverable_window_is_reached(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, ["other_task", "private", "public"]), max_windows=2)
    assert genesis.bootstrap(**run)["status"] == "PUBLISHED_GENESIS" and len(calls) == 1


def test_f2_only_a_young_public_row_is_no_ready_row_and_refuses_before_admission(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, ["other_task", "young"]), max_windows=4)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)


def test_f2_a_row_the_reader_refuses_is_refused_before_admission(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, [b'{"synthetic": not json\n'] + f2_rows(args, ["public"]), max_windows=2)
    with pytest.raises(genesis.GenesisRefused):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)


def test_f2_an_incomplete_catchup_without_a_cutoff_refuses_and_leaves_no_probe(synthetic, monkeypatch):
    # Without a cutoff the static budget already refuses most such sources; the pre-scan mirrors the producer's
    # catchup_incomplete decision for any chain that still ends short of the snapshot.
    args, calls = synthetic
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    monkeypatch.setattr(snapshot, "read_public_catchup", lambda *positional, **keywords: {
        "caught_up": False, "end_reason": "max_windows", "windows": ()})
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, ["public"]), max_windows=1)
    for key in ("cutoff_utc", "batch_rows", "allow_partial_genesis"):
        run.pop(key)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_catchup_incomplete$"):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)


def test_f2_the_probe_never_uses_the_real_sidecar_path_or_anchor(synthetic, monkeypatch):
    args, calls = synthetic
    seen = []
    from tools import wd_mind_anchor_issuer as anchor_issuer
    original = anchor_issuer.issue_anchor

    def watch(**kwargs):
        seen.append((kwargs["generation_path"], Path(kwargs["generation_path"]).exists()))
        return original(**kwargs)

    monkeypatch.setattr(anchor_issuer, "issue_anchor", watch)
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, ["private", "public"]), max_windows=1)
    assert genesis.bootstrap(**run)["status"] == "PUBLISHED_GENESIS"
    probe, real = [path for path, _ in seen if path != args["generation_path"]], [
        path for path, _ in seen if path == args["generation_path"]]
    assert len(probe) == 1 and len(real) == 1
    assert Path(probe[0]).parent.parent == Path(args["private_state_root"]) and not Path(probe[0]).exists()
    assert not Path(probe[0]).parent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="real private checkpoint requires Windows fixed volume")
def test_f2_real_producer_empty_first_windows_no_longer_spend_the_one_shot(synthetic, monkeypatch):
    """Real pre-scan, runner, child, producer and checkpoint; only the encoder is synthetic."""
    import inspect
    np = pytest.importorskip("numpy")
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    args, _ = synthetic
    fake = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    child = fake._actual
    if "batch_rows" not in inspect.signature(child.build_prefix_generation).parameters:
        pytest.skip("batch-capable child commit not yet integrated")
    monkeypatch.setitem(sys.modules, fake.__name__, child)
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer", fake._real_accessor)
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    monkeypatch.setattr(child, "_encoder", lambda encoder, identity:
                        lambda texts: np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384))
    model = Path(args["trusted_model_root"])
    weights = b"synthetic-weights-not-a-model"
    (model / "model.safetensors").write_bytes(weights)
    args["plan_template"]["encoder"].update(weights_sha256=sha(weights), assets={"model.safetensors": sha(weights)})
    args["plan_template"].update(timeout=10)
    rows = f2_rows(args, EMPTY_EARLY + ["public", "private", "private", "private"])
    run = f2_args(args, rows, max_windows=2)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    assert no_one_shot_state(args)
    run["plan_template"]["max_windows"] = 3
    result = genesis.bootstrap(**run)
    assert result["status"] == "PUBLISHED_GENESIS" and result["coverage"] == "partial_ready_prefix"
    projection = json.loads((Path(args["checkpoint_root"]) / args["generation"] / "projection.json").read_bytes())
    assert len(projection["records"]) == 1
    assert b"synthetic-private-must-not-be-returned" not in json.dumps(projection).encode()


# Late public (lead-mind-genesis-late-public-20261106-v1): with the opt-in resume_genesis_skip, a bounded pre-scan that
# finds no public row records a private resumable skip receipt instead of only refusing; a later run resumes from it
# and genesis is admitted at the receipt offset, which the runner accepts only when bound to that receipt.
SKIP = "q4-genesis-skip-v1.json"


def skip_receipt(args):
    path = Path(args["private_state_root"]) / SKIP
    return path.read_bytes() if path.exists() else None


def only_skip_receipt(args):
    """No one-shot state exists, and the private root holds nothing but the skip receipt."""
    return (not Path(args["generation_path"]).exists()
            and sorted(p.name for p in Path(args["private_state_root"]).iterdir()) == [SKIP]
            and sorted(Path(args["checkpoint_root"]).iterdir()) == [])


def identity_of(path):
    from waggledance.core.bridge_log_reader import _file_identity, _open_log
    with _open_log(Path(path)) as stream:
        return _file_identity(stream)


def test_late_skip_records_a_private_resumable_receipt_and_spends_nothing(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    rows = f2_rows(args, EMPTY_EARLY + ["public"])
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    assert calls == [] and only_skip_receipt(args)
    raw_receipt = skip_receipt(args)
    receipt = json.loads(raw_receipt)
    k = len(b"".join(rows[:4]))
    assert raw_receipt == raw(receipt)
    assert receipt == {"schema": "wd.q4-genesis-skip.v1", "file_identity": identity_of(args["source_path"]),
                       "generation": "synthetic-generation", "policy_sha256": args["policy_sha256"],
                       "roster_sha256": receipt["roster_sha256"], "public_policy_sha256": args["public_policy_sha256"],
                       "offset": k, "prefix_sha256": sha(b"".join(rows[:4])), "previous_sha256": None, "runs": 1}
    assert len(receipt["roster_sha256"]) == 64
    assert b"synthetic-private-must-not-be-returned" not in raw_receipt


def test_late_skip_resumes_and_binds_genesis_to_the_receipt_offset(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    rows = f2_rows(args, EMPTY_EARLY + EMPTY_EARLY + ["public", "private"])
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    first = skip_receipt(args)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    second = skip_receipt(args)
    assert json.loads(second)["offset"] == len(b"".join(rows[:8])) and json.loads(second)["runs"] == 2
    assert json.loads(second)["previous_sha256"] == sha(first) and only_skip_receipt(args)
    seen = []
    original = runner.run_once

    def watch(**kwargs):
        seen.append(json.loads(kwargs["route_bytes"]))
        return original(**kwargs)

    monkeypatch.setattr(runner, "run_once", watch)
    assert genesis.bootstrap(**run)["status"] == "PUBLISHED_GENESIS" and len(calls) == 1
    k = len(b"".join(rows[:8]))
    assert seen[0]["cursor"]["offset"] == k and seen[0]["prefix_sha256"] == sha(b"".join(rows[:8]))
    assert seen[0]["genesis_skip_sha256"] == sha(second) and skip_receipt(args) == second
    assert calls[0]["config"].source.cursor.offset == k


def test_late_skip_off_by_default_keeps_the_f2_refusal_and_writes_no_receipt(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, EMPTY_EARLY + ["public"]), max_windows=2)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)


def test_late_skip_requires_explicit_partial_genesis(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = dict(f2_args(args, f2_rows(args, EMPTY_EARLY), max_windows=2), resume_genesis_skip=True,
               allow_partial_genesis=False)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_refused$"):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_refused$"):
        genesis.bootstrap(**dict(run, allow_partial_genesis=True, resume_genesis_skip=1))
    assert calls == [] and no_one_shot_state(args)


def test_late_skip_never_passes_a_young_public_row(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    # The young row sits inside the second window, so that window ends deferred on its exact byte boundary.
    rows = f2_rows(args, ["other_task", "private", "other_task", "young", "other_task"])
    run = dict(f2_args(args, rows, max_windows=4), resume_genesis_skip=True)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    receipt = skip_receipt(args)
    assert json.loads(receipt)["offset"] == len(b"".join(rows[:3]))
    # The next run starts at the young row: no ready row and no progress, so the receipt stays as it was.
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    assert skip_receipt(args) == receipt and calls == [] and only_skip_receipt(args)


def test_late_skip_after_source_end_resumes_when_a_public_row_is_appended(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    rows = f2_rows(args, EMPTY_EARLY + ["public"])
    run = dict(f2_args(args, rows[:4], max_windows=4), resume_genesis_skip=True)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    assert json.loads(skip_receipt(args))["offset"] == len(b"".join(rows[:4]))
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    with open(args["source_path"], "ab") as stream:
        stream.write(rows[4])
    assert genesis.bootstrap(**run)["status"] == "PUBLISHED_GENESIS" and len(calls) == 1
    assert calls[0]["config"].source.cursor.offset == len(b"".join(rows[:4]))


@pytest.mark.parametrize("fault", ["rewrite_skipped_byte", "replace_source", "generation", "fleet", "tamper_offset",
                                   "tamper_prefix", "noncanonical", "midline_offset", "beyond_source",
                                   "tamper_policy", "tamper_roster", "tamper_public_policy"])
def test_late_skip_receipt_is_bound_to_source_identity_bytes_and_pins(synthetic, monkeypatch, fault):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    rows = f2_rows(args, EMPTY_EARLY + ["public"])
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    path = Path(args["private_state_root"]) / SKIP
    source = Path(args["source_path"])
    value = json.loads(path.read_bytes())
    if fault == "rewrite_skipped_byte":
        data = bytearray(source.read_bytes())
        data[data.index(b"lead-unrelated")] = ord("L")
        with open(source, "r+b") as stream:
            stream.write(bytes(data))
    elif fault == "replace_source":
        copy = source.with_name("copy.jsonl")
        copy.write_bytes(source.read_bytes())
        copy.replace(source)
    elif fault == "generation":
        run = dict(run, generation="other-generation", plan_template={**run["plan_template"],
                                                                      "generation": "other-generation"})
    elif fault == "fleet":
        fleet = json.loads(args["fleet_bytes"])
        fleet["lanes"][0]["agent_uuid"] = "99999999-1111-4111-8111-111111111111"
        fleet_bytes = raw(fleet)
        monkeypatch.setattr(runner, "_FLEET_SHA", frozenset({sha(fleet_bytes)}))
        run = dict(run, fleet_bytes=fleet_bytes, expected_fleet_sha256=sha(fleet_bytes))
    elif fault in ("tamper_offset", "tamper_prefix", "midline_offset", "beyond_source"):
        value.update({"tamper_offset": {"offset": len(b"".join(rows[:2]))},
                      "tamper_prefix": {"prefix_sha256": "e" * 64},
                      "midline_offset": {"offset": value["offset"] - 3,
                                         "prefix_sha256": sha(b"".join(rows)[:value["offset"] - 3])},
                      "beyond_source": {"offset": 60000, "prefix_sha256": "e" * 64}}[fault])
        path.write_bytes(raw(value))
    elif fault == "noncanonical":
        path.write_bytes(json.dumps(value, sort_keys=True, indent=1).encode("ascii"))
    elif fault.startswith("tamper_") and fault[len("tamper_"):] + "_sha256" in value:
        value[fault[len("tamper_"):] + "_sha256"] = "e" * 64
        path.write_bytes(raw(value))
    before = path.read_bytes()
    with pytest.raises(genesis.GenesisRefused, match="^(genesis_skip_receipt_invalid|genesis_refused)$"):
        genesis.bootstrap(**run)
    assert calls == [] and path.read_bytes() == before and only_skip_receipt(args)


def test_late_skip_honours_the_independent_source_pin(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    rows = f2_rows(args, EMPTY_EARLY + ["public"])
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True)
    data = b"".join(rows)
    pinned = dict(run, expected_source_identity=identity_of(args["source_path"]),
                  expected_source_prefix_bytes=len(data), expected_source_prefix_sha256=sha(data))
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**pinned)
    receipt = skip_receipt(args)
    assert json.loads(receipt)["file_identity"] == pinned["expected_source_identity"]
    wrong = dict(pinned, expected_source_identity="windows-v1:00000000:0000000000000000")
    with pytest.raises(genesis.GenesisRefused):
        genesis.bootstrap(**wrong)
    assert skip_receipt(args) == receipt and calls == []
    assert genesis.bootstrap(**pinned)["status"] == "PUBLISHED_GENESIS" and len(calls) == 1


def test_late_skip_receipt_raced_by_another_writer_is_not_overwritten(synthetic, monkeypatch):
    args, calls = synthetic
    rows = f2_rows(args, EMPTY_EARLY + EMPTY_EARLY + ["public"])
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True)
    path = Path(args["private_state_root"]) / SKIP

    def racing(*positional, **keywords):
        result = REAL_PRESCAN(*positional, **keywords)
        path.write_bytes(b"another writer")
        return result

    monkeypatch.setattr(genesis, "_prescan_public_rows", racing)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_refused$"):
        genesis.bootstrap(**run)
    assert path.read_bytes() == b"another writer" and calls == []
    assert sorted(p.name for p in Path(args["private_state_root"]).iterdir()) == [SKIP]


# RCO2 follow-ups on the F2 pre-scan (relayed by Lead 11-06 21:27Z).
def test_prescan_cleanup_failure_keeps_the_actual_refusal(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, EMPTY_EARLY + ["public"]), max_windows=2)

    def failing_rmdir(self):
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(Path, "rmdir", failing_rmdir)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_no_public_row$"):
        genesis.bootstrap(**run)
    assert calls == [] and not Path(args["generation_path"]).exists()


def test_prescan_mkdir_failure_removes_nothing_it_did_not_create(synthetic, monkeypatch):
    args, calls = synthetic
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, ["public"]), max_windows=2)
    removed = []

    def failing_mkdir(self, *positional, **keywords):
        raise FileExistsError()

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    monkeypatch.setattr(Path, "rmdir", lambda self: removed.append(self))
    with pytest.raises(genesis.GenesisRefused, match="^genesis_refused$"):
        genesis.bootstrap(**run)
    assert removed == [] and calls == []


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_interrupt_before_admission_is_not_converted_into_a_refusal(synthetic, monkeypatch, signal):
    args, calls = synthetic
    from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    run = f2_args(args, f2_rows(args, ["public"]), max_windows=2)

    def interrupted(*positional, **keywords):
        raise signal()

    monkeypatch.setattr(snapshot, "read_public_catchup", interrupted)
    with pytest.raises(signal):
        genesis.bootstrap(**run)
    assert calls == [] and no_one_shot_state(args)


@pytest.mark.skipif(sys.platform != "win32", reason="real private checkpoint requires Windows fixed volume")
def test_late_skip_real_producer_publishes_every_public_row_after_the_skip(synthetic, monkeypatch):
    """Real pre-scan, skip receipts, runner, child, producer and checkpoint; only the encoder is synthetic. Every
    public row of the source is published once; no public row lies in the skipped prefix."""
    import inspect
    np = pytest.importorskip("numpy")
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint_module
    args, _ = synthetic
    fake = sys.modules["sandbox.mind.step2.wd_q4_prefix_build_child"]
    child = fake._actual
    if "batch_rows" not in inspect.signature(child.build_prefix_generation).parameters:
        pytest.skip("batch-capable child commit not yet integrated")
    monkeypatch.setitem(sys.modules, fake.__name__, child)
    monkeypatch.setattr(checkpoint_module, "read_verified_prefix_pointer", fake._real_accessor)
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    monkeypatch.setattr(child, "_encoder", lambda encoder, identity:
                        lambda texts: np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384))
    model = Path(args["trusted_model_root"])
    weights = b"synthetic-weights-not-a-model"
    (model / "model.safetensors").write_bytes(weights)
    args["plan_template"]["encoder"].update(weights_sha256=sha(weights), assets={"model.safetensors": sha(weights)})
    args["plan_template"].update(timeout=10)
    kinds = EMPTY_EARLY + EMPTY_EARLY + ["public", "private", "public", "other_task"]
    rows = f2_rows(args, kinds)
    data = b"".join(rows)
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True, renewable=True,
               expected_source_identity=identity_of(args["source_path"]),
               expected_source_prefix_bytes=len(data), expected_source_prefix_sha256=sha(data))
    for _ in range(2):
        with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
            genesis.bootstrap(**run)
    assert json.loads(skip_receipt(args))["offset"] == len(b"".join(rows[:8]))
    result = genesis.bootstrap(**run)
    assert result["status"] == "PUBLISHED_GENESIS" and result["genesis_initial_prefix_fully_covered"] is True
    projection = json.loads((Path(args["checkpoint_root"]) / args["generation"] / "projection.json").read_bytes())
    assert len(projection["records"]) == kinds.count("public") == 2
    assert b"synthetic-private-must-not-be-returned" not in json.dumps(projection).encode()
    assert b"lead-unrelated-20261031" not in json.dumps(projection).encode()
    # Continuation (Lead 21:44Z): a route with a prior pointer that still carries the genesis-only key is refused,
    # never silently ignored; built explicitly without it, the continuation advances from the published pointer.
    from dataclasses import asdict
    from tools import wd_mind_anchor_issuer as anchor_issuer
    from tools.wd_q4_roster_from_fleet import build_roster_snapshot
    roster_bytes, roster_sha = build_roster_snapshot(args["fleet_bytes"], args["expected_fleet_sha256"], enabled=True)

    def verified(report):
        return checkpoint_module.read_verified_prefix_pointer(Path(args["checkpoint_root"]),
            frozenset({"lead-mind-production-20261031"}), expected_pointer_sha256=report["pointer_sha256"],
            policy_bytes=args["policy_bytes"], policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes,
            roster_sha256=roster_sha, expected_public_policy_sha256=args["public_policy_sha256"])[1]

    prefix = verified(result)
    assert prefix.cursor.offset == len(data) and prefix.sha256 == sha(data)
    appended = f2_rows(args, ["public"])[0]
    with open(args["source_path"], "ab") as stream:
        stream.write(appended)
    private_tuple = json.loads((Path(args["private_state_root"]) / "q4-genesis-tuple-v1.json").read_bytes())
    route = json.loads(base64.b64decode(private_tuple["route"]["bytes_b64"]))
    plan = json.loads(base64.b64decode(private_tuple["plan"]["bytes_b64"]))
    assert route["genesis_skip_sha256"] == sha(skip_receipt(args))
    anchor, anchor_sha = anchor_issuer.issue_anchor(enabled=True, source_path=args["source_path"],
        generation_path=args["generation_path"], cursor=prefix.cursor, policy_bytes=args["policy_bytes"],
        policy_sha256=args["policy_sha256"], roster_bytes=roster_bytes, roster_sha256=roster_sha,
        public_policy_bytes=args["public_policy_bytes"], public_policy_sha256=args["public_policy_sha256"])
    plan.update(generation="synthetic-generation-2", expected_pointer_sha256=result["pointer_sha256"])
    plan["pins"]["anchor_sha256"] = anchor_sha
    plan_bytes = raw(plan)
    route.update(cursor=asdict(prefix.cursor), prefix_sha256=prefix.sha256, anchor_sha256=anchor_sha,
                 plan_sha256=sha(plan_bytes))

    def attempt(value):
        route_bytes = raw(value)
        return runner.run_once(enabled=True, route_bytes=route_bytes, expected_route_sha256=sha(route_bytes),
            plan_bytes=plan_bytes, policy_bytes=args["policy_bytes"], public_policy_bytes=args["public_policy_bytes"],
            fleet_bytes=args["fleet_bytes"], expected_fleet_sha256=args["expected_fleet_sha256"],
            roster_bytes=roster_bytes, anchor_bytes=anchor, renewable=True)

    with pytest.raises(runner.StatusRunnerRefused):
        attempt(route)
    continued = attempt({key: value for key, value in route.items() if key != "genesis_skip_sha256"})
    end = verified(continued)
    assert end.cursor.offset == len(data) + len(appended) and end.sha256 == sha(data + appended)


def test_late_skip_receipt_changed_before_admission_spends_nothing(synthetic, monkeypatch):
    args, calls = synthetic
    rows = f2_rows(args, EMPTY_EARLY + ["public"])
    run = dict(f2_args(args, rows, max_windows=2), resume_genesis_skip=True)
    monkeypatch.setattr(genesis, "_prescan_public_rows", REAL_PRESCAN)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_skip_progress$"):
        genesis.bootstrap(**run)
    path = Path(args["private_state_root"]) / SKIP
    value = json.loads(path.read_bytes())

    def racing(*positional, **keywords):
        result = REAL_PRESCAN(*positional, **keywords)
        path.write_bytes(raw(dict(value, runs=value["runs"] + 5)))
        return result

    monkeypatch.setattr(genesis, "_prescan_public_rows", racing)
    with pytest.raises(genesis.GenesisRefused, match="^genesis_refused$"):
        genesis.bootstrap(**run)
    assert calls == [] and only_skip_receipt(args)
