"""RED-first contract for the default-off fixed prefix build child ``sandbox.mind.step2.wd_q4_prefix_build_child``.

Synthetic only: the model backend is a fake module injected into ``sys.modules`` (no model execution), the bridge
log is a temp file, the private state root is a temp directory. The child takes a CLOSED caller-pinned plan plus
the caller-owned source config, start prefix and roots; it never accepts an encoder callback. The encoder is the
fixed ``encode.E5`` path with its identity DERIVED by ``encode.model_identity`` from the pinned model directory and
checked against the plan's ``weights_sha256`` pin before the backend is loaded. Wrong or missing pins must refuse
before any side effect; the strict-prefix producer keeps prior-pointer and prefix continuity unchanged.
"""
from dataclasses import replace
import hashlib
import importlib
import inspect
import json
import os
import sys
import types

import numpy as np
import pytest

from sandbox.mind.step2 import encode as encode_mod
from sandbox.mind.step2 import shared_prefix_public_producer as producer
from sandbox.mind.step2 import shared_producer as old
from sandbox.mind.step2 import shared_public_producer as pub
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

SENTINEL = "synthetic-private-sentinel"
REVISION = "c" * 40
WEIGHTS = b"synthetic-weights-not-a-model"
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()
PIN_KEYS = ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")


def module():
    return importlib.import_module("sandbox.mind.step2.wd_q4_prefix_build_child")


def enc(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Poison:
    def __getattribute__(self, name):
        raise AssertionError("disabled must not inspect arguments")


# ---------------------------------------------------------------------------------------------------------------
# fake backend: a sentence_transformers module whose model hashes words into 384 unit vectors
# ---------------------------------------------------------------------------------------------------------------


class FakeModel:
    calls = []

    def __init__(self, path, device=None, local_files_only=None):
        FakeModel.calls.append({"path": path, "device": device, "local_files_only": local_files_only})

    def encode(self, inputs, **kw):
        FakeModel.calls.append({"inputs": list(inputs), "kw": dict(kw)})
        out = np.zeros((len(inputs), encode_mod.DIM), dtype=np.float32)
        for row, text in enumerate(inputs):
            for word in text.split():
                out[row, int(hashlib.sha256(word.encode()).hexdigest(), 16) % encode_mod.DIM] += 1.0
            norm = np.linalg.norm(out[row])
            out[row] = out[row] / norm if norm else np.ones(encode_mod.DIM, dtype=np.float32) / np.sqrt(encode_mod.DIM)
        return out


@pytest.fixture
def backend(monkeypatch):
    FakeModel.calls = []
    fake = types.ModuleType("sentence_transformers")
    fake.__version__ = "0.0-synthetic"
    fake.SentenceTransformer = FakeModel
    torch = types.ModuleType("torch")
    torch.__version__ = "0.0-synthetic"
    torch.get_num_threads = lambda: 1
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.delitem(sys.modules, "huggingface_hub.constants", raising=False)
    monkeypatch.setattr(os, "environ", dict(os.environ))  # the fixed loader pins offline env vars; keep them local
    return FakeModel


@pytest.fixture
def model_dir(tmp_path):
    path = tmp_path / "model"
    path.mkdir()
    (path / "model.safetensors").write_bytes(WEIGHTS)
    return path


# ---------------------------------------------------------------------------------------------------------------
# plan and inputs
# ---------------------------------------------------------------------------------------------------------------


def plan(model_dir, pins, *, generation="gen-a", pointer=None, **changes):
    value = {"schema": "wd.q4-prefix-build-plan.v1", "generation": generation, "expected_pointer_sha256": pointer,
             "max_prefix_bytes": 1 << 20, "timeout": 0.2,
             "encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": str(model_dir),
                         "weights_sha256": WEIGHTS_SHA, "dim": encode_mod.DIM},
             "pins": dict(pins)}
    value.update(changes)
    return value


def synthetic_config(tmp_path, pins_override=None):
    """A structurally valid PublicSourceConfig over synthetic bytes (no real snapshot); pins derive from the bytes."""
    anchor, policy, roster, public = (enc({"synthetic": name}) for name in ("anchor", "policy", "roster", "public"))
    cursor = BridgeCursor(offset=0, file_identity="synthetic-file")
    source = old.SourceConfig(source=str(tmp_path / "log.jsonl"), generation_path=str(tmp_path / "gen"), cursor=cursor,
                              anchor_bytes=anchor, anchor_sha256=sha(anchor), policy_bytes=policy,
                              policy_sha256=sha(policy), roster_bytes=roster, roster_sha256=sha(roster),
                              max_bytes=1 << 20, max_rows=100)
    config = old.PublicSourceConfig(source=source, public_policy_bytes=public, public_policy_sha256=sha(public))
    pins = {"anchor_sha256": sha(anchor), "policy_sha256": sha(policy), "roster_sha256": sha(roster),
            "public_policy_sha256": sha(public)}
    if pins_override:
        pins.update(pins_override)
    return config, pins, BridgePrefixPin(cursor, sha(b""))


def run(m, plan_doc, config, start, root, private_root, *, plan_bytes=None, plan_sha=None, enabled=True):
    raw = enc(plan_doc) if plan_bytes is None else plan_bytes
    return m.build_prefix_generation(enabled=enabled, plan_bytes=raw, expected_plan_sha256=sha(raw) if plan_sha is None else plan_sha,
                                     config=config, start_prefix=start, root=root, private_state_root=private_root)


def refused(reason, fn):
    m = module()
    with pytest.raises(m.BuildChildRefused) as exc:
        fn()
    error = exc.value
    assert error.args == (reason,)
    assert error.__cause__ is None and error.__context__ is None
    text = repr(error)
    assert SENTINEL not in text and WEIGHTS_SHA not in text and "\\" not in text and "/" not in text
    return error


def nothing_written(*roots):
    for root in roots:
        assert not any(root.iterdir()), root.name


# ---------------------------------------------------------------------------------------------------------------
# switch, signature, closed plan
# ---------------------------------------------------------------------------------------------------------------


def test_default_off_inspects_nothing():
    m = module()
    assert m.build_prefix_generation(plan_bytes=Poison(), expected_plan_sha256=Poison(), config=Poison(),
                                     start_prefix=Poison(), root=Poison(), private_state_root=Poison()) == {"status": "OFF"}


@pytest.mark.parametrize("literal", [1, 0, "true", None, [True], 1.0])
def test_enabled_must_be_the_bool_true(literal, tmp_path):
    m = module()
    refused("disabled", lambda: m.build_prefix_generation(enabled=literal, plan_bytes=b"{}", expected_plan_sha256=sha(b"{}"),
                                                          config=None, start_prefix=None, root=tmp_path, private_state_root=tmp_path))


def test_no_encoder_callback_or_identity_can_be_supplied_by_the_caller():
    m = module()
    parameters = inspect.signature(m.build_prefix_generation).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert set(parameters) == {"enabled", "plan_bytes", "expected_plan_sha256", "config", "start_prefix", "root",
                               "private_state_root"}
    assert m.PLAN_SCHEMA == "wd.q4-prefix-build-plan.v1"
    assert m.ENCODER_MODEL == encode_mod.MODEL and m.ENCODER_DIM == encode_mod.DIM == 384
    assert m.IDENTITY_SOURCE == "encode.model_identity"
    assert "fixed" in (m.__doc__ or "") and "callback" in (m.__doc__ or "")


def test_plan_pin_mismatch_refuses_before_parsing(monkeypatch, tmp_path, model_dir):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    raw = enc(plan(model_dir, pins))
    monkeypatch.setattr(m.json, "loads", lambda *a, **k: pytest.fail("plan parsed with a wrong pin"))
    refused("plan_refused", lambda: run(m, None, config, start, tmp_path, tmp_path, plan_bytes=raw, plan_sha="0" * 64))
    refused("plan_refused", lambda: run(m, None, config, start, tmp_path, tmp_path, plan_bytes=raw, plan_sha=sha(raw).upper()))
    refused("plan_refused", lambda: run(m, None, config, start, tmp_path, tmp_path, plan_bytes=raw, plan_sha=None if False else 5))


@pytest.mark.parametrize("raw", [None, "text", bytearray(b"{}"), b"", b"{", b"null", b"[]",
                                 b'{"schema":"wd.q4-prefix-build-plan.v1","schema":"x"}',
                                 b'{"schema":"wd.q4-prefix-build-plan.v1","timeout":NaN}'])
def test_plan_bytes_and_parser_details_refuse(raw, tmp_path):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    refused("plan_refused", lambda: run(m, None, config, start, tmp_path, tmp_path, plan_bytes=raw,
                                        plan_sha=sha(raw) if type(raw) is bytes else "0" * 64))


def test_plan_over_the_byte_limit_refuses(tmp_path, model_dir):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    raw = enc(plan(model_dir, pins))
    at_limit = raw + b" " * (m.PLAN_LIMIT - len(raw))
    refused("plan_refused", lambda: run(m, None, config, start, tmp_path, tmp_path, plan_bytes=at_limit + b" "))


def shape_changes():
    yield {"extra": SENTINEL}
    yield {"encode": SENTINEL}
    yield {"schema": "wd.q4-prefix-build-plan.v2"}
    yield {"generation": "../escape"}
    yield {"generation": 5}
    yield {"expected_pointer_sha256": "0" * 63}
    yield {"expected_pointer_sha256": SENTINEL}
    yield {"max_prefix_bytes": -1}
    yield {"max_prefix_bytes": "1048576"}
    yield {"timeout": 0}
    yield {"timeout": 40}
    yield {"timeout": "0.2"}
    yield {"pins": {k: "0" * 64 for k in PIN_KEYS[:-1]}}
    yield {"pins": {**{k: "0" * 64 for k in PIN_KEYS}, "extra_sha256": "0" * 64}}
    yield {"pins": {**{k: "0" * 64 for k in PIN_KEYS}, "anchor_sha256": "0" * 63}}
    yield {"pins": []}
    yield {"encoder": SENTINEL}
    yield {"encoder": {"model": "other/model", "revision": REVISION, "model_dir": "C:/synthetic", "weights_sha256": WEIGHTS_SHA, "dim": 384}}
    yield {"encoder": {"model": encode_mod.MODEL, "revision": "main", "model_dir": "C:/synthetic", "weights_sha256": WEIGHTS_SHA, "dim": 384}}
    yield {"encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": "relative/dir", "weights_sha256": WEIGHTS_SHA, "dim": 384}}
    yield {"encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": "C:/synthetic", "weights_sha256": WEIGHTS_SHA.upper(), "dim": 384}}
    yield {"encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": "C:/synthetic", "weights_sha256": WEIGHTS_SHA, "dim": 16}}
    yield {"encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": "C:/synthetic", "weights_sha256": WEIGHTS_SHA, "dim": 384, "callback": SENTINEL}}
    yield {"encoder": {"model": encode_mod.MODEL, "revision": REVISION, "model_dir": "C:/synthetic", "dim": 384}}


@pytest.mark.parametrize("change", list(shape_changes()))
def test_closed_plan_shape_negatives_refuse_before_any_side_effect(change, tmp_path, model_dir, monkeypatch):
    m = module()
    monkeypatch.setattr(m.encode_mod, "model_identity", lambda **k: pytest.fail("identity derived for a bad plan"))
    config, pins, start = synthetic_config(tmp_path)
    doc = plan(model_dir, pins)
    doc.update(change)
    root, private_root = tmp_path / "root", tmp_path / "private"
    root.mkdir(), private_root.mkdir()
    refused("plan_refused", lambda: run(m, doc, config, start, root, private_root))
    nothing_written(root, private_root)


# ---------------------------------------------------------------------------------------------------------------
# pins and encoder identity before side effects
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", PIN_KEYS)
def test_plan_pin_not_matching_the_config_refuses_before_identity_or_backend(key, tmp_path, model_dir, monkeypatch):
    m = module()
    monkeypatch.setattr(m.encode_mod, "model_identity", lambda **k: pytest.fail("identity derived with wrong pins"))
    monkeypatch.setattr(m.encode_mod, "E5", lambda **k: pytest.fail("backend loaded with wrong pins"))
    config, pins, start = synthetic_config(tmp_path)
    pins[key] = sha(b"other")
    root, private_root = tmp_path / "root", tmp_path / "private"
    root.mkdir(), private_root.mkdir()
    refused("pins_refused", lambda: run(m, plan(model_dir, pins), config, start, root, private_root))
    nothing_written(root, private_root)


@pytest.mark.parametrize("bad", ["config", "start", "root", "private"])
def test_request_object_types_refuse_before_identity(bad, tmp_path, model_dir, monkeypatch):
    m = module()
    monkeypatch.setattr(m.encode_mod, "model_identity", lambda **k: pytest.fail("identity derived for a bad request"))
    config, pins, start = synthetic_config(tmp_path)
    args = {"config": config, "start": start, "root": tmp_path, "private": tmp_path}
    args[bad] = SENTINEL if bad != "start" else replace(start, sha256=SENTINEL)
    refused("request_refused", lambda: run(m, plan(model_dir, pins), args["config"], args["start"], args["root"], args["private"]))


def test_weights_digest_mismatch_refuses_before_the_backend_is_loaded(tmp_path, model_dir, monkeypatch):
    m = module()
    monkeypatch.setattr(m.encode_mod, "E5", lambda **k: pytest.fail("backend loaded with a wrong weights pin"))
    config, pins, start = synthetic_config(tmp_path)
    doc = plan(model_dir, pins)
    doc["encoder"]["weights_sha256"] = sha(b"other-weights")
    refused("encoder_refused", lambda: run(m, doc, config, start, tmp_path, tmp_path))


def test_missing_or_unreadable_model_directory_refuses_without_a_path(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m.encode_mod, "E5", lambda **k: pytest.fail("backend loaded without weights"))
    config, pins, start = synthetic_config(tmp_path)
    missing = tmp_path / "no-model"
    refused("encoder_refused", lambda: run(m, plan(missing, pins), config, start, tmp_path, tmp_path))
    missing.mkdir()
    refused("encoder_refused", lambda: run(m, plan(missing, pins), config, start, tmp_path, tmp_path))


def test_backend_failure_refuses_fixed_without_cause(tmp_path, model_dir, backend):
    m = module()

    class Broken(backend):
        def __init__(self, *a, **k):
            raise RuntimeError(SENTINEL)
    sys.modules["sentence_transformers"].SentenceTransformer = Broken
    config, pins, start = synthetic_config(tmp_path)
    refused("encoder_refused", lambda: run(m, plan(model_dir, pins), config, start, tmp_path, tmp_path))


def test_producer_refusal_tokens_pass_through_without_chain(tmp_path, model_dir, backend, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    monkeypatch.setattr(m.producer, "publish_public_prefix",
                        lambda **k: (_ for _ in ()).throw(producer.PrefixProducerRefused("prepare_refused")))
    refused("prepare_refused", lambda: run(m, plan(model_dir, pins), config, start, tmp_path, tmp_path))
    monkeypatch.setattr(m.producer, "publish_public_prefix",
                        lambda **k: (_ for _ in ()).throw(producer.PrefixProducerRefused(SENTINEL)))
    refused("publication_state_unknown", lambda: run(m, plan(model_dir, pins), config, start, tmp_path, tmp_path))
    monkeypatch.setattr(m.producer, "publish_public_prefix", lambda **k: {"status": "OFF"})
    refused("publication_state_unknown", lambda: run(m, plan(model_dir, pins), config, start, tmp_path, tmp_path))


def test_request_handed_to_the_producer_carries_the_fixed_encoder_and_derived_identity(tmp_path, model_dir, backend, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    seen = {}

    def fake_publish(*, enabled, request):
        seen["enabled"], seen["request"] = enabled, request
        return {"status": "PUBLISHED_GENESIS", "pointer_sha256": "1" * 64, "generation": "gen-a",
                "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN"}
    monkeypatch.setattr(m.producer, "publish_public_prefix", fake_publish)
    report = run(m, plan(model_dir, pins), config, start, tmp_path, tmp_path)
    request = seen["request"]
    assert seen["enabled"] is True and type(request) is producer.PrefixPublicRequest
    assert request.identity == {"model": encode_mod.MODEL, "revision": REVISION, "weights_sha256": WEIGHTS_SHA}
    assert request.dim == 384 and request.generation == "gen-a" and request.expected_pointer_sha256 is None
    assert request.start_prefix == start and request.config is config and type(request.pins) is pub.CallerPins
    assert request.pins == pub.CallerPins(**pins)
    assert backend.calls[0]["local_files_only"] is True and backend.calls[0]["device"] == "cpu"
    vectors = request.encode(["alpha beta", "gamma"])
    assert vectors.shape == (2, 384) and vectors.dtype == np.dtype("<f4")
    assert backend.calls[-1]["inputs"] == ["passage: alpha beta", "passage: gamma"]
    assert backend.calls[-1]["kw"]["normalize_embeddings"] is True and backend.calls[-1]["kw"]["batch_size"] == 32
    assert report == {"status": "PUBLISHED_GENESIS", "pointer_sha256": "1" * 64, "generation": "gen-a",
                      "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN",
                      "encoder_identity_source": "encode.model_identity", "plan_sha256": sha(enc(plan(model_dir, pins)))}
    text = json.dumps(report)
    assert WEIGHTS_SHA not in text and str(model_dir) not in text and start.sha256 not in text


# ---------------------------------------------------------------------------------------------------------------
# real strict-prefix chain through the child (synthetic bridge log, fake backend, FAISS)
# ---------------------------------------------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="private state requires Windows fixed volume")
def test_genesis_empty_advance_and_history_keep_prefix_continuity_through_the_child(tmp_path, model_dir, backend):
    pytest.importorskip("faiss", reason="synthetic builder needs FAISS")
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as pf
    from sandbox.mind.step2 import test_shared_index_builder as f
    from sandbox.mind.step2 import prefix_private_state as ps
    from tools import wd_q4_prefix_public_query as reader
    m = module()
    root, private_root = tmp_path / "state", tmp_path / "private-state"
    root.mkdir(), private_root.mkdir()
    read_pins = dict(policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES,
                     roster_sha256=f.ROSTER_SHA, public_policy_bytes=f.PUBLIC_POLICY_BYTES,
                     public_policy_sha256=f.PUBLIC_POLICY_SHA, enabled=True)

    def inputs(kw, prior):
        anchor = json.loads(kw["expected_anchor_bytes"])
        source = old.SourceConfig(source=anchor["source_path"], generation_path=anchor["generation_path"],
            cursor=prior.cursor, anchor_bytes=kw["expected_anchor_bytes"], anchor_sha256=kw["expected_anchor_sha256"],
            policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES,
            roster_sha256=f.ROSTER_SHA, max_bytes=1 << 20, max_rows=100)
        config = old.PublicSourceConfig(source=source, public_policy_bytes=f.PUBLIC_POLICY_BYTES,
                                        public_policy_sha256=f.PUBLIC_POLICY_SHA)
        pins = {"anchor_sha256": kw["expected_anchor_sha256"], "policy_sha256": f.POLICY_SHA,
                "roster_sha256": f.ROSTER_SHA, "public_policy_sha256": f.PUBLIC_POLICY_SHA}
        return config, pins

    first, _, kw_a, start = pf._delta(tmp_path / "log", f.PUBLIC_EVENTS)
    config, pins = inputs(kw_a, start)
    a = run(m, plan(model_dir, pins, generation="gen-a"), config, start, root, private_root)
    assert a["status"] == "PUBLISHED_GENESIS" and a["generation"] == "gen-a"
    assert start.sha256 not in json.dumps(a) and WEIGHTS_SHA not in json.dumps(a)
    assert reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=a["pointer_sha256"], **read_pins)["generation"] == "gen-a"
    memory = json.loads((root / "gen-a" / "memory.json").read_bytes())
    assert memory["encoder"] == {"model": encode_mod.MODEL, "revision": REVISION, "weights_sha256": WEIGHTS_SHA}
    with ps.locked_state(private_root, root, enabled=True) as state:
        end_a = state.load(a["pointer_sha256"])
    assert end_a == first["candidate_prefix"]

    empty, _, kw_e, _ = pf._delta(tmp_path / "log", [f.event(agent="claude-rco-1")], end_a.cursor, end_a)
    config, pins = inputs(kw_e, end_a)
    # a start prefix that is not the private-state continuation refuses through the producer, nothing advances
    refused("private_state_refused", lambda: run(m, plan(model_dir, pins, pointer=a["pointer_sha256"]), config,
                                                  replace(end_a, sha256="0" * 64), root, private_root))
    b = run(m, plan(model_dir, pins, generation="unused", pointer=a["pointer_sha256"]), config, end_a, root, private_root)
    assert b["status"] == "EMPTY_ADVANCED" and end_a.sha256 not in json.dumps(b)
    with ps.locked_state(private_root, root, enabled=True) as state:
        next_start = state.load(b["pointer_sha256"])
    assert next_start == empty["candidate_prefix"]

    later, _, kw_b, _ = pf._delta(tmp_path / "log", f.LATER_PUBLIC_EVENTS, next_start.cursor, next_start)
    config, pins = inputs(kw_b, next_start)
    # a stale prior pointer in the plan refuses: continuity binds to the latest pointer
    refused("private_state_refused", lambda: run(m, plan(model_dir, pins, generation="gen-b", pointer=a["pointer_sha256"]),
                                                  config, next_start, root, private_root))
    c = run(m, plan(model_dir, pins, generation="gen-b", pointer=b["pointer_sha256"]), config, next_start, root, private_root)
    assert c["status"] == "PUBLISHED_HISTORY" and c["generation"] == "gen-b"
    resolved = reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=c["pointer_sha256"], **read_pins)
    assert resolved["generation"] == "gen-b" and later["candidate_prefix"].sha256 not in json.dumps(resolved)
    with pytest.raises(reader.PrefixPublicRefused):
        reader.resolve_prefix_public_pointer(root, expected_pointer_sha256=a["pointer_sha256"], **read_pins)
    assert all(text.startswith("passage: ") for call in backend.calls if "inputs" in call for text in call["inputs"])


def test_module_is_fixed_path_only():
    from pathlib import Path
    source = Path(module().__file__).read_text(encoding="utf-8")
    assert "from None" not in source and "from exc" not in source
    for token in ("subprocess", "socket", "urllib", "print(", "input("):
        assert token not in source, token
    assert "SentenceTransformer(" not in source  # loading goes through the fixed encode.E5 path only
    assert module().REASONS == frozenset({"disabled", "plan_refused", "request_refused", "pins_refused",
                                          "encoder_refused", "publication_state_unknown"})
