"""RED-first contract for the default-off fixed prefix build child ``sandbox.mind.step2.wd_q4_prefix_build_child``.

Synthetic only: the model backend is a fake module injected into ``sys.modules`` (no model execution), the bridge
log is a temp file, the private state root is a temp directory. The child takes a CLOSED caller-pinned plan plus
the caller-owned source config, start prefix and roots; it never accepts an encoder callback. The encoder is the
fixed ``encode.E5`` path with its identity DERIVED by ``encode.model_identity`` from the pinned model directory and
checked against the plan's ``weights_sha256`` pin before the backend is loaded. Wrong or missing pins must refuse
before any side effect; the strict-prefix producer keeps prior-pointer and prefix continuity unchanged.

v2 (independent review findings): the plan's ``model_dir`` is bound to an explicitly caller-pinned
``trusted_model_root`` that must be a fixed-local drive-letter directory with no UNC, symlink, junction or other
reparse point anywhere from the root down through the model tree and no ``..`` escape, checked with metadata calls
only before ``encode.model_identity`` reads anything (``model_root_refused``). The pure preflight checks the
producer would otherwise make only after the backend loaded (start cursor equal to the source cursor and within
the prefix budget, snapshot bounds, policy/roster/public-policy parse, checkpoint root distinct from and not nested
with the private state root) run before identity derivation and backend load (``preflight_refused``); the
producer's locked validation, prior-pointer CAS and private-state continuity stay where they are.
"""
from dataclasses import replace
import hashlib
import importlib
import inspect
import json
import contextlib
import io
import os
from pathlib import Path
import sys
import types

import numpy as np
import pytest

from sandbox.mind.step2 import encode as encode_mod
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_prefix_public_producer as producer
from sandbox.mind.step2 import shared_producer as old
from sandbox.mind.step2 import shared_public_producer as pub
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

SENTINEL = "synthetic-private-sentinel"
REVISION = "c" * 40
WEIGHTS = b"synthetic-weights-not-a-model"
WEIGHTS_SHA = hashlib.sha256(WEIGHTS).hexdigest()
PIN_KEYS = ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256")
TASK = "lead-q4-prefix-build-child-synthetic-20261101-v1"
LANES = ("codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5")
POLICY_DOC = {"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [TASK, "other-task-1"]}
ROSTER_DOC = {"schema": "wd.q4-roster-snapshot.v1",
              "lanes": [{"agent": agent, "agent_uuid": f"0000000{n}-abcd-4ef0-8abc-00000000000{n}"}
                        for n, agent in enumerate(LANES, start=1)],
              "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": "00000009-abcd-4ef0-8abc-000000000009"}}
PUBLIC_POLICY_DOC = {"schema": "wd.mind-public-status-policy.v1", "public_tasks": [TASK], "public_types": ["message", "done"]}
UNC_DIRS = ("\\\\synthetic-host\\share\\model", "//synthetic-host/share/model", "\\\\?\\C:\\synthetic\\model")


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


def synthetic_config(tmp_path, pins_override=None, *, cursor=None, policy=None, roster=None, public=None, **source_changes):
    """A structurally valid PublicSourceConfig over synthetic bytes (no real snapshot) whose policy, roster and public
    policy documents parse under the producer's adapter; pins derive from the bytes."""
    anchor = enc({"synthetic": "anchor"})
    policy = enc(POLICY_DOC) if policy is None else policy
    roster = enc(ROSTER_DOC) if roster is None else roster
    public = enc(PUBLIC_POLICY_DOC) if public is None else public
    cursor = BridgeCursor(offset=0, file_identity="synthetic-file") if cursor is None else cursor
    fields = dict(source=str(tmp_path / "log.jsonl"), generation_path=str(tmp_path / "gen"), cursor=cursor,
                  anchor_bytes=anchor, anchor_sha256=sha(anchor), policy_bytes=policy, policy_sha256=sha(policy),
                  roster_bytes=roster, roster_sha256=sha(roster), max_bytes=1 << 20, max_rows=100)
    fields.update(source_changes)
    source = old.SourceConfig(**fields)
    config = old.PublicSourceConfig(source=source, public_policy_bytes=public, public_policy_sha256=sha(public))
    pins = {"anchor_sha256": sha(anchor), "policy_sha256": sha(policy), "roster_sha256": sha(roster),
            "public_policy_sha256": sha(public)}
    if pins_override:
        pins.update(pins_override)
    return config, pins, BridgePrefixPin(cursor, sha(b""))


def roots(tmp_path):
    """Distinct, non-nested checkpoint and private state roots (the producer and the preflight require both)."""
    root, private_root = tmp_path / "root", tmp_path / "private"
    root.mkdir(exist_ok=True), private_root.mkdir(exist_ok=True)
    return root, private_root


def trusted_root_of(plan_doc):
    """The caller-pinned trusted model root a test means unless it says otherwise: the pinned model_dir's parent."""
    try:
        return Path(plan_doc["encoder"]["model_dir"]).parent
    except (TypeError, KeyError):
        return Path("C:/")


DERIVE = object()  # run() derives the trusted root from the plan unless a test passes one (None included)


def run(m, plan_doc, config, start, root, private_root, *, plan_bytes=None, plan_sha=None, enabled=True, trusted=DERIVE):
    raw = enc(plan_doc) if plan_bytes is None else plan_bytes
    if trusted is DERIVE:
        trusted = trusted_root_of(plan_doc) if plan_doc is not None else None
    return m.build_prefix_generation(enabled=enabled, plan_bytes=raw, expected_plan_sha256=sha(raw) if plan_sha is None else plan_sha,
                                     config=config, start_prefix=start, root=root, private_state_root=private_root,
                                     trusted_model_root=trusted)


@contextlib.contextmanager
def no_reads(monkeypatch):
    """Inside this context every file open fails: model-root binding and preflight must use metadata calls only."""
    import builtins
    with monkeypatch.context() as patched:
        patched.setattr(builtins, "open", lambda *a, **k: pytest.fail("a file was opened before the refusal"))
        patched.setattr(io, "open", lambda *a, **k: pytest.fail("a file was opened before the refusal"))
        yield


def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))


def never_loads(m, monkeypatch):
    monkeypatch.setattr(m.encode_mod, "model_identity", lambda **k: pytest.fail("identity derived before the refusal"))
    monkeypatch.setattr(m.encode_mod, "E5", lambda **k: pytest.fail("backend loaded before the refusal"))


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
                                     start_prefix=Poison(), root=Poison(), private_state_root=Poison(),
                                     trusted_model_root=Poison()) == {"status": "OFF"}


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
                               "private_state_root", "trusted_model_root"}
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


@pytest.mark.parametrize("bad", ["config", "start", "root", "private", "trusted"])
@pytest.mark.parametrize("value", [SENTINEL, None, b"C:/synthetic", 5])
def test_request_object_types_refuse_before_identity(bad, value, tmp_path, model_dir, monkeypatch):
    m = module()
    never_loads(m, monkeypatch)
    config, pins, start = synthetic_config(tmp_path)
    root, private_root = roots(tmp_path)
    args = {"config": config, "start": start, "root": root, "private": private_root, "trusted": tmp_path}
    args[bad] = value if bad != "start" else replace(start, sha256=value)
    refused("request_refused", lambda: run(m, plan(model_dir, pins), args["config"], args["start"], args["root"],
                                           args["private"], trusted=args["trusted"]))
    nothing_written(root, private_root)


def test_weights_digest_mismatch_refuses_before_the_backend_is_loaded(tmp_path, model_dir, monkeypatch):
    m = module()
    monkeypatch.setattr(m.encode_mod, "E5", lambda **k: pytest.fail("backend loaded with a wrong weights pin"))
    config, pins, start = synthetic_config(tmp_path)
    doc = plan(model_dir, pins)
    doc["encoder"]["weights_sha256"] = sha(b"other-weights")
    refused("encoder_refused", lambda: run(m, doc, config, start, *roots(tmp_path)))


def test_missing_model_directory_or_weights_refuses_before_identity_without_a_path(tmp_path, monkeypatch):
    m = module()
    never_loads(m, monkeypatch)
    config, pins, start = synthetic_config(tmp_path)
    missing = tmp_path / "no-model"
    refused("model_root_refused", lambda: run(m, plan(missing, pins), config, start, *roots(tmp_path)))
    missing.mkdir()  # a model directory without the weights file the identity would read
    refused("model_root_refused", lambda: run(m, plan(missing, pins), config, start, *roots(tmp_path)))
    (missing / "model.safetensors").mkdir()  # the weights name present but not a regular file
    refused("model_root_refused", lambda: run(m, plan(missing, pins), config, start, *roots(tmp_path)))


def test_backend_failure_refuses_fixed_without_cause(tmp_path, model_dir, backend):
    m = module()

    class Broken(backend):
        def __init__(self, *a, **k):
            raise RuntimeError(SENTINEL)
    sys.modules["sentence_transformers"].SentenceTransformer = Broken
    config, pins, start = synthetic_config(tmp_path)
    refused("encoder_refused", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path)))


def test_producer_refusal_tokens_pass_through_without_chain(tmp_path, model_dir, backend, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    monkeypatch.setattr(m.producer, "publish_public_prefix",
                        lambda **k: (_ for _ in ()).throw(producer.PrefixProducerRefused("prepare_refused")))
    refused("prepare_refused", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path)))
    monkeypatch.setattr(m.producer, "publish_public_prefix",
                        lambda **k: (_ for _ in ()).throw(producer.PrefixProducerRefused(SENTINEL)))
    refused("publication_state_unknown", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path)))
    monkeypatch.setattr(m.producer, "publish_public_prefix", lambda **k: {"status": "OFF"})
    refused("publication_state_unknown", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path)))


def test_request_handed_to_the_producer_carries_the_fixed_encoder_and_derived_identity(tmp_path, model_dir, backend, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    seen = {}

    def fake_publish(*, enabled, request):
        seen["enabled"], seen["request"] = enabled, request
        return {"status": "PUBLISHED_GENESIS", "pointer_sha256": "1" * 64, "generation": "gen-a",
                "publisher_authentication": "UNKNOWN", "source_authenticity": "UNKNOWN", "freshness": "UNKNOWN"}
    monkeypatch.setattr(m.producer, "publish_public_prefix", fake_publish)
    report = run(m, plan(model_dir, pins), config, start, *roots(tmp_path))
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
    assert str(tmp_path) not in text  # the trusted root never appears in the report either


# ---------------------------------------------------------------------------------------------------------------
# v2 finding 1: model_dir bound to the caller-pinned trusted fixed-local non-reparse root before any read
# ---------------------------------------------------------------------------------------------------------------


def junction(target, link):
    _winapi = pytest.importorskip("_winapi", reason="junctions need the Windows runtime")
    if not hasattr(_winapi, "CreateJunction"):
        pytest.skip("CreateJunction unavailable")
    _winapi.CreateJunction(str(target), str(link))
    return link


def model_tree(path):
    path.mkdir(parents=True)
    (path / "model.safetensors").write_bytes(WEIGHTS)
    return path


def root_refused(m, monkeypatch, tmp_path, pins, model_dir, trusted, config=None, start=None):
    never_loads(m, monkeypatch)
    if config is None:
        config, _, start = synthetic_config(tmp_path)
    root, private_root = roots(tmp_path)
    before = tree(tmp_path)
    with no_reads(monkeypatch):
        refused("model_root_refused", lambda: run(m, plan(model_dir, pins), config, start, root, private_root, trusted=trusted))
    assert tree(tmp_path) == before
    nothing_written(root, private_root)


def test_model_dir_outside_the_trusted_root_refuses_before_any_read(tmp_path, model_dir, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    root_refused(m, monkeypatch, tmp_path, pins, model_dir, trusted)
    # a sibling whose name merely starts with the trusted root's name is outside it
    sibling = model_tree(tmp_path / "trusted-sibling" / "model")
    root_refused(m, monkeypatch, tmp_path, pins, sibling, trusted)


@pytest.mark.parametrize("escape", ["..", "trusted/../model", "./model"])
def test_dot_components_in_the_pinned_model_dir_refuse_as_escape(escape, tmp_path, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    trusted = tmp_path / "trusted"
    inside = model_tree(trusted / "model")
    # plain string joins: pathlib would collapse "." and the test means the component as pinned
    escaped = str(inside) + "/../../trusted/model" if escape == ".." else str(trusted) + "/" + escape
    assert os.path.isdir(escaped)  # every escape resolves to the real model directory, so only the binding refuses
    root_refused(m, monkeypatch, tmp_path, pins, escaped, trusted)


@pytest.mark.parametrize("unc", UNC_DIRS)
def test_unc_and_device_paths_refuse_as_model_dir_and_as_trusted_root(unc, tmp_path, model_dir, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    root_refused(m, monkeypatch, tmp_path, pins, unc, Path(unc).parent)
    root_refused(m, monkeypatch, tmp_path, pins, unc, tmp_path)
    root_refused(m, monkeypatch, tmp_path, pins, model_dir, Path(unc))


def test_trusted_root_not_a_directory_or_missing_refuses(tmp_path, model_dir, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    root_refused(m, monkeypatch, tmp_path, pins, model_dir, tmp_path / "absent")
    root_refused(m, monkeypatch, tmp_path, pins, model_dir, model_dir / "model.safetensors")


def test_relative_trusted_root_refuses_as_request_shape(tmp_path, model_dir, monkeypatch):
    m = module()
    never_loads(m, monkeypatch)
    config, pins, start = synthetic_config(tmp_path)
    refused("request_refused", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path), trusted="relative/root"))
    refused("request_refused", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path), trusted=""))


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse point")
def test_junction_model_dir_inside_the_trusted_root_refuses_before_any_read(tmp_path, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = model_tree(tmp_path / "outside" / "model")
    link = junction(outside, trusted / "model")
    assert (link / "model.safetensors").is_file()  # the escape is real: the weights resolve through the junction
    root_refused(m, monkeypatch, tmp_path, pins, link, trusted)
    root_refused(m, monkeypatch, tmp_path, pins, link / "nested", trusted)


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse point")
def test_junction_anywhere_below_the_model_dir_refuses(tmp_path, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    trusted = tmp_path / "trusted"
    inside = model_tree(trusted / "model")
    (inside / "tokenizer").mkdir()
    (tmp_path / "elsewhere").mkdir()
    junction(tmp_path / "elsewhere", inside / "tokenizer" / "vocab")
    root_refused(m, monkeypatch, tmp_path, pins, inside, trusted)


@pytest.mark.skipif(os.name != "nt", reason="junctions are a Windows reparse point")
def test_trusted_root_that_is_itself_a_junction_refuses(tmp_path, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    real = tmp_path / "real"
    model_tree(real / "model")
    link = junction(real, tmp_path / "trusted")
    root_refused(m, monkeypatch, tmp_path, pins, link / "model", link)
    # and a reparse point above the trusted root refuses too: the whole chain from the drive down must be plain
    root_refused(m, monkeypatch, tmp_path, pins, link / "model", link / "model")


def test_symlinked_model_dir_or_weights_refuse_before_any_read(tmp_path, monkeypatch):
    m = module()
    _, pins, _ = synthetic_config(tmp_path)
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = model_tree(tmp_path / "outside" / "model")
    try:
        os.symlink(outside, trusted / "model", target_is_directory=True)
        inside = model_tree(trusted / "plain")
        (inside / "model.safetensors").unlink()
        os.symlink(outside / "model.safetensors", inside / "model.safetensors")
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not permitted for this account")
    root_refused(m, monkeypatch, tmp_path, pins, trusted / "model", trusted)
    root_refused(m, monkeypatch, tmp_path, pins, inside, trusted)


@pytest.mark.parametrize("drive_type", [0, 1, 2, 4, 5, 6])
def test_non_fixed_drive_refuses_before_any_read(drive_type, tmp_path, model_dir, monkeypatch):
    m = module()
    seen = []

    def fake_drive_type(anchor):
        seen.append(anchor)
        return drive_type
    monkeypatch.setattr(m, "_drive_type", fake_drive_type)
    _, pins, _ = synthetic_config(tmp_path)
    root_refused(m, monkeypatch, tmp_path, pins, model_dir, tmp_path)
    assert seen and all(anchor == Path(tmp_path).anchor for anchor in seen)


def test_drive_type_probe_failure_refuses(tmp_path, model_dir, monkeypatch):
    m = module()
    monkeypatch.setattr(m, "_drive_type", lambda anchor: (_ for _ in ()).throw(OSError(SENTINEL)))
    _, pins, _ = synthetic_config(tmp_path)
    root_refused(m, monkeypatch, tmp_path, pins, model_dir, tmp_path)


def test_bound_model_dir_reaches_identity_unchanged_and_as_the_pinned_string(tmp_path, model_dir, monkeypatch):
    m = module()
    seen = {}

    def identity(*, model_dir, revision):
        seen["model_dir"], seen["revision"] = model_dir, revision
        raise OSError(SENTINEL)
    monkeypatch.setattr(m.encode_mod, "model_identity", identity)
    monkeypatch.setattr(m.encode_mod, "E5", lambda **k: pytest.fail("backend loaded after a failed identity"))
    config, pins, start = synthetic_config(tmp_path)
    refused("encoder_refused", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path), trusted=tmp_path))
    assert seen == {"model_dir": str(model_dir), "revision": REVISION}
    # the trusted root may equal the model directory itself
    seen.clear()
    refused("encoder_refused", lambda: run(m, plan(model_dir, pins), config, start, *roots(tmp_path), trusted=model_dir))
    assert seen["model_dir"] == str(model_dir)


# ---------------------------------------------------------------------------------------------------------------
# v2 finding 2: pure preflight before identity derivation and backend load
# ---------------------------------------------------------------------------------------------------------------


def preflight_refused(m, monkeypatch, tmp_path, pins, config, start, root=None, private_root=None):
    never_loads(m, monkeypatch)
    monkeypatch.setattr(m, "_drive_type", lambda anchor: pytest.fail("model root bound before the preflight"))
    default_root, default_private = roots(tmp_path)
    root = default_root if root is None else root
    private_root = default_private if private_root is None else private_root
    model_dir = tmp_path / "model"
    before = tree(tmp_path)
    with no_reads(monkeypatch):
        refused("preflight_refused", lambda: run(m, plan(model_dir, pins), config, start, root, private_root, trusted=tmp_path))
    assert tree(tmp_path) == before


def test_start_cursor_not_the_source_cursor_refuses_in_preflight(tmp_path, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    other = replace(start, cursor=BridgeCursor(offset=0, file_identity="other-synthetic-file"))
    preflight_refused(m, monkeypatch, tmp_path, pins, config, other)
    ahead = replace(start, cursor=BridgeCursor(offset=1, file_identity="synthetic-file"))
    preflight_refused(m, monkeypatch, tmp_path, pins, config, ahead)


def test_start_offset_beyond_the_plan_prefix_budget_refuses_in_preflight(tmp_path, monkeypatch):
    m = module()
    cursor = BridgeCursor(offset=(1 << 20) + 1, file_identity="synthetic-file")
    config, pins, start = synthetic_config(tmp_path, cursor=cursor)
    assert start.cursor == config.source.cursor
    preflight_refused(m, monkeypatch, tmp_path, pins, config, start)


@pytest.mark.parametrize("change", [{"max_bytes": 0}, {"max_bytes": snapshot.MAX_BYTES + 1}, {"max_bytes": "1"},
                                    {"max_rows": 0}, {"max_rows": snapshot.MAX_ROWS + 1}, {"max_rows": 10.0}])
def test_snapshot_bounds_refuse_in_preflight(change, tmp_path, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path, **change)
    preflight_refused(m, monkeypatch, tmp_path, pins, config, start)


@pytest.mark.parametrize("document", ["policy", "roster", "public"])
def test_policy_roster_or_public_policy_that_does_not_parse_refuses_in_preflight(document, tmp_path, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path, **{document: enc({"synthetic": document})})
    preflight_refused(m, monkeypatch, tmp_path, pins, config, start)  # pins match the bytes, the documents are invalid


def test_public_policy_outside_the_approved_tasks_refuses_in_preflight(tmp_path, monkeypatch):
    m = module()
    public = enc({**PUBLIC_POLICY_DOC, "public_tasks": ["not-approved-task"]})
    config, pins, start = synthetic_config(tmp_path, public=public)
    preflight_refused(m, monkeypatch, tmp_path, pins, config, start)


def test_review_task_in_the_policy_refuses_in_preflight(tmp_path, monkeypatch):
    m = module()
    policy = enc({**POLICY_DOC, "approved_tasks": [TASK, "lead-review-task-20261101"]})
    config, pins, start = synthetic_config(tmp_path, policy=policy)
    preflight_refused(m, monkeypatch, tmp_path, pins, config, start)


@pytest.mark.parametrize("conflict", ["same", "private_inside_root", "root_inside_private", "root_missing",
                                      "private_missing", "root_is_a_file"])
def test_root_conflicts_refuse_in_preflight(conflict, tmp_path, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    root, private_root = roots(tmp_path)
    if conflict == "same":
        pair = (root, root)
    elif conflict == "private_inside_root":
        (root / "private").mkdir()
        pair = (root, root / "private")
    elif conflict == "root_inside_private":
        (private_root / "deep" / "root").mkdir(parents=True)
        pair = (private_root / "deep" / "root", private_root)
    elif conflict == "root_missing":
        pair = (tmp_path / "absent", private_root)
    elif conflict == "private_missing":
        pair = (root, tmp_path / "absent")
    else:
        (tmp_path / "file").write_bytes(b"")
        pair = (tmp_path / "file", private_root)
    preflight_refused(m, monkeypatch, tmp_path, pins, config, start, *pair)


def test_pins_are_checked_before_the_preflight(tmp_path, monkeypatch):
    m = module()
    config, pins, start = synthetic_config(tmp_path)
    pins["policy_sha256"] = sha(b"other")
    never_loads(m, monkeypatch)
    root, private_root = roots(tmp_path)
    refused("pins_refused", lambda: run(m, plan(tmp_path / "model", pins), config,
                                        replace(start, cursor=BridgeCursor(offset=0, file_identity="other")), root, root))


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
                                          "preflight_refused", "model_root_refused", "encoder_refused",
                                          "publication_state_unknown"})
    doc = module().__doc__ or ""
    for word in ("trusted_model_root", "UNC", "reparse", "preflight", "CAS", "locked"):
        assert word in doc, word
