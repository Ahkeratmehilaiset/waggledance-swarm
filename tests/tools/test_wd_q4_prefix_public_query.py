"""Synthetic, default-off strict-prefix read seam; never calls a model or live bridge."""
import copy
import hashlib
import importlib
import io
import json
import os
from pathlib import Path

import pytest

from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin


def sha(data):
    return hashlib.sha256(data).hexdigest()


def enc(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


@pytest.fixture
def seam(monkeypatch, tmp_path):
    mod = importlib.import_module("tools.wd_q4_prefix_public_query")
    root = tmp_path / "root"
    generation = root / "gen-a"
    generation.mkdir(parents=True)
    encoder = {"model": "synthetic", "revision": "a" * 40, "weights_sha256": "c" * 64}
    memory = b"synthetic memory"
    projection = enc({"records": [{"meta": {"agent": "fable-5", "task_id": "public-task", "type": "status"}}]})
    descriptor = enc({"schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": "wd.q4-shared-projected-events.v1",
                      "memory_sha256": sha(memory), "dim": 3, "encoder": encoder})
    (generation / "memory.json").write_bytes(memory)
    (generation / "projection.json").write_bytes(projection)
    (generation / "descriptor.json").write_bytes(descriptor)
    pointer = root / mod.checkpoint.POINTER
    pointer.write_bytes(b"synthetic prefix pointer")
    artifacts = {"memory_sha256": sha(memory), "projection_sha256": sha(projection),
                 "descriptor_sha256": sha(descriptor), "receipt_sha256": "a" * 64}
    pin = BridgePrefixPin(BridgeCursor(0, "posix-v1:1:2", "synthetic-generation"), "e" * 64)
    view = {"pointer_sha256": sha(pointer.read_bytes()), "generation": "gen-a", "artifacts": artifacts,
            "anchor_sha256": "b" * 64, "cursor_end_sha256": "c" * 64,
            "watermark_sha256": sha(mod.snapshot._token(pin.cursor).encode())}
    args = dict(root=root, expected_pointer_sha256=view["pointer_sha256"], expected_source_sha256=view["anchor_sha256"],
                expected_encoder_sha256=sha(enc(encoder)), policy_bytes=b"synthetic-policy", policy_sha256="1" * 64,
                roster_bytes=b"synthetic-roster", roster_sha256="2" * 64,
                public_policy_bytes=b"synthetic-public-policy", public_policy_sha256="3" * 64, enabled=True)
    config = object()
    state = dict(mod=mod, args=args, config=config, view=view, pin=pin, pointer=pointer, calls=[])
    def strict(root, tasks, **kwargs):
        state["calls"].append((root, tasks, kwargs))
        if (kwargs["expected_pointer_sha256"] != state["view"]["pointer_sha256"] or
                kwargs["policy_sha256"] != "1" * 64 or kwargs["roster_sha256"] != "2" * 64 or
                kwargs["expected_public_policy_sha256"] != "3" * 64):
            raise ValueError("mismatch")
        return copy.deepcopy(state["view"]), state["pin"]
    def forbidden(*args, **kwargs):
        pytest.fail("strict route must never use the legacy verifier")
    def read(path, expected, *, limit):
        raw = Path(path).read_bytes()
        if len(raw) > limit or sha(raw) != expected:
            raise ValueError("mismatch")
        return raw
    monkeypatch.setattr(mod.checkpoint, "read_verified_prefix_pointer", strict)
    monkeypatch.setattr(mod.checkpoint, "read_verified_pointer", forbidden)
    monkeypatch.setattr(mod.checkpoint, "_secure", lambda path: Path(path))
    monkeypatch.setattr(mod.resolver, "_read_matching", read)
    monkeypatch.setattr(mod.adapter, "strict_pins", lambda *a: (frozenset({"public-task"}), {}))
    monkeypatch.setattr(mod.adapter, "public_status_policy", lambda *a: (frozenset({"public-task"}), frozenset({"status"})))
    return state


def query(state, **changes):
    return state["mod"].query_public_prefix(state["config"], io.BytesIO(b"synthetic"), **dict(state["args"], **changes))


def resolve(state, **changes):
    args = dict(state["args"], **changes)
    del args["expected_source_sha256"], args["expected_encoder_sha256"]
    return state["mod"].resolve_prefix_public_pointer(**args)


def test_default_off_refuses_without_read(seam):
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^disabled$"):
        query(seam, enabled=False)
    assert not seam["calls"]


class NoRead:
    def read(self, *args):
        pytest.fail("a refused query must not consume stdin")


def _forbid_backend(monkeypatch):
    from tools import wd_q4_agent_query as q4_query

    def forbidden(*args, **kwargs):
        pytest.fail("a refused query must not reach the launcher or the resolver")
    monkeypatch.setattr(q4_query, "query_and_resolve", forbidden)
    monkeypatch.setattr(q4_query, "_invoke_launcher", forbidden)


def _forbid_native(monkeypatch):
    """A None entry makes any import of the native stack fail loudly: a refusal path must never deserialize FAISS."""
    import sys
    for name in ("faiss", "numpy"):
        monkeypatch.setitem(sys.modules, name, None)


def _unreached_backend(seam):
    """The explicit NON-PRODUCTION seam marker wrapping a backend that must never run."""
    return seam["mod"].SyntheticInProcessBackend(lambda *a, **k: pytest.fail("a refused query must not reach the backend"))


def _seam_query(seam, config, stdin, **args):
    return seam["mod"]._synthetic_seam_query_public_prefix(config, stdin, synthetic_backend=_unreached_backend(seam), **args)


PRODUCTION_PARAMETERS = {"config", "stdin", "root", "expected_pointer_sha256", "expected_source_sha256",
                         "expected_encoder_sha256", "policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
                         "public_policy_bytes", "public_policy_sha256", "enabled"}


def q4_config(seam, **changes):
    """A caller-asserted VerifiedQ4Config bound to the seam's generation (fake runtime pins, real tmp paths)."""
    from tools import wd_q4_agent_query as q4_query
    generation = seam["args"]["root"] / "gen-a"
    fields = dict(interpreter=r"C:\synthetic\python.exe", interpreter_sha256="6" * 64,
                  bootstrap=r"C:\synthetic\bootstrap.py", bootstrap_sha256="7" * 64, verifier_sha256="8" * 64,
                  runtime_root=r"C:\synthetic\runtime", runtime_manifest=r"C:\synthetic\manifest.json",
                  runtime_manifest_sha256="9" * 64, runtime_generation="b" * 40, runtime_revision="a" * 40,
                  memory=str(generation), descriptor=str(generation / "descriptor.json"),
                  descriptor_sha256=seam["view"]["artifacts"]["descriptor_sha256"],
                  lock_file=r"C:\synthetic\reader.lock", lock_timeout="1", timeout=5, scratch_root=r"C:\synthetic\scratch")
    fields.update(changes)
    return q4_query.VerifiedQ4Config(**fields)


QUERY = b'{"situation":"SYNTHETIC_SITUATION_NEVER_OUTPUT","time":"2000-01-01T00:00:00Z"}'


def test_production_query_has_no_backend_or_transport_parameter(seam):
    import inspect
    params = inspect.signature(seam["mod"].query_public_prefix).parameters
    assert set(params) == PRODUCTION_PARAMETERS and params["enabled"].default is False
    for extra in ({"synthetic_backend": _unreached_backend(seam)}, {"transport": object()}, {"backend": object()}):
        with pytest.raises(TypeError):
            seam["mod"].query_public_prefix(q4_config(seam), NoRead(), **dict(seam["args"], **extra))
    assert not seam["calls"]


@pytest.mark.parametrize("inputs", ["perfect", "bad_pin", "bad_bytes", "bare_config"])
def test_enabled_production_query_refuses_before_pins_files_stdin_backend_or_native(seam, monkeypatch, inputs):
    """No verified transport exists: the production boundary refuses first, whatever the caller supplies."""
    _forbid_backend(monkeypatch)
    _forbid_native(monkeypatch)
    mod = seam["mod"]
    monkeypatch.setattr(mod.resolver, "_read_matching", lambda *a, **k: pytest.fail("no file read before the transport exists"))
    monkeypatch.setattr(mod.checkpoint, "read_verified_prefix_pointer", lambda *a, **k: pytest.fail("no pointer read"))
    monkeypatch.setattr(mod, "resolve_prefix_public_pointer", lambda *a, **k: pytest.fail("no resolve before the transport"))
    config, args = q4_config(seam), dict(seam["args"])
    if inputs == "bad_pin":
        args["expected_source_sha256"] = "zz"
    elif inputs == "bad_bytes":
        args["roster_bytes"] = "text"
    elif inputs == "bare_config":
        config = object()
    with pytest.raises(mod.PrefixPublicRefused, match="^secure_transport_unavailable$"):
        mod.query_public_prefix(config, NoRead(), **args)
    assert not seam["calls"]


def test_enabled_query_with_an_unvalidated_config_refuses_before_stdin_or_backend(seam, monkeypatch):
    _forbid_backend(monkeypatch)
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _seam_query(seam, seam["config"], NoRead(), **seam["args"])  # seam config is a bare object
    assert not seam["calls"]


@pytest.mark.parametrize("marker", [None, True, 1, "SyntheticInProcessBackend", object()])
def test_seam_refuses_without_the_explicit_marker_object(seam, monkeypatch, marker):
    """No bool-only trust: the seam runs only with the constructed marker, never with a flag or a bare callable."""
    _forbid_backend(monkeypatch)
    _forbid_native(monkeypatch)
    mod = seam["mod"]
    monkeypatch.setattr(mod.resolver, "_read_matching", lambda *a, **k: pytest.fail("no file read without the marker"))
    with pytest.raises(mod.PrefixPublicRefused, match="^synthetic_backend_required$"):
        mod._synthetic_seam_query_public_prefix(q4_config(seam), NoRead(), synthetic_backend=marker, **seam["args"])
    with pytest.raises(mod.PrefixPublicRefused, match="^synthetic_backend_required$"):
        mod._synthetic_seam_query_public_prefix(q4_config(seam), NoRead(),
                                                synthetic_backend=lambda *a, **k: pytest.fail("bare callable ran"),
                                                **seam["args"])
    with pytest.raises(TypeError):
        mod.SyntheticInProcessBackend(True)
    with pytest.raises(TypeError):
        mod.SyntheticInProcessBackend(None)
    assert not seam["calls"]


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "expected_source_sha256", "expected_encoder_sha256",
                                   "policy_sha256", "roster_sha256", "public_policy_sha256"])
@pytest.mark.parametrize("bad", [None, "0" * 63, "X" * 64, 0])
def test_query_refuses_before_stdin_when_any_caller_pin_is_absent_or_malformed(seam, monkeypatch, field, bad):
    _forbid_backend(monkeypatch)
    _forbid_native(monkeypatch)
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _seam_query(seam, q4_config(seam), NoRead(), **dict(seam["args"], **{field: bad}))
    assert not seam["calls"]


@pytest.mark.parametrize("field", ["policy_bytes", "roster_bytes", "public_policy_bytes"])
def test_query_refuses_before_stdin_when_pinned_bytes_are_not_bytes(seam, monkeypatch, field):
    _forbid_backend(monkeypatch)
    _forbid_native(monkeypatch)
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _seam_query(seam, q4_config(seam), NoRead(), **dict(seam["args"], **{field: "text"}))
    assert not seam["calls"]


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "1"])
def test_query_default_off_touches_nothing(seam, monkeypatch, enabled):
    _forbid_backend(monkeypatch)
    _forbid_native(monkeypatch)
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^disabled$"):
        seam["mod"].query_public_prefix(object(), NoRead(), **dict(seam["args"], enabled=enabled))
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^disabled$"):
        _seam_query(seam, object(), NoRead(), **dict(seam["args"], enabled=enabled))
    assert not seam["calls"]


@pytest.fixture
def answered(seam, monkeypatch):
    """The seam plus a recording in-process backend injected through the NON-PRODUCTION marker (no launcher, no model);
    the module's own route to wd_q4_agent_query.query_and_resolve and the launcher is forbidden."""
    _forbid_backend(monkeypatch)
    seam["answer"] = {"status": "ANSWER", "results": [
        {"ref_sha256": "a" * 64, "doc_id_sha256": "d" * 64, "stored_text": "SYNTHETIC_STORED_TEXT",
         "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}]}
    seam["queries"] = []

    def query(config, stream, memory_raw, projection_raw, tasks, **kwargs):
        seam["queries"].append((config, stream.read(), memory_raw, projection_raw, tasks, kwargs))
        return copy.deepcopy(seam["answer"])
    seam["backend"] = seam["mod"].SyntheticInProcessBackend(query)
    return seam


def _query(seam, config=None, stdin=None, **changes):
    return seam["mod"]._synthetic_seam_query_public_prefix(
        q4_config(seam) if config is None else config, io.BytesIO(QUERY) if stdin is None else stdin,
        synthetic_backend=seam["backend"], **dict(seam["args"], **changes))


@pytest.mark.skipif(os.name != "nt", reason="VerifiedQ4Config paths are Windows drive paths")
def test_enabled_prefix_query_binds_the_verified_generation_and_returns_only_public_text(answered):
    seam = answered
    out = _query(seam)
    assert out["schema"] == "wd.q4-prefix-public-query.v1" and out["status"] == "ANSWER"
    assert out["results"] == seam["answer"]["results"]
    binding = out["binding"]
    assert binding["pointer_sha256"] == seam["args"]["expected_pointer_sha256"]
    assert binding["encoder_sha256"] == seam["args"]["expected_encoder_sha256"]
    assert binding["public_policy_sha256"] == seam["args"]["public_policy_sha256"]
    for key, value in seam["view"]["artifacts"].items():
        assert binding[key] == value
    assert set(binding) == {"pointer_sha256", "encoder_sha256", "public_policy_sha256",
                            "descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256"}
    # verified but withheld: the source anchor, policy and roster digests commit to private material
    assert out["withheld"] == ["policy_sha256", "roster_sha256", "source_anchor_sha256"]
    assert set(out) == {"schema", "status", "results", "binding", "withheld", "publisher_authentication",
                        "source_filter_attestation", "freshness", "same_user_aba"}
    for field in ("publisher_authentication", "source_filter_attestation", "freshness", "same_user_aba"):
        assert out[field] == "UNKNOWN"
    text = json.dumps(out)
    assert "SYNTHETIC_SITUATION" not in text and seam["pin"].sha256 not in text
    assert str(seam["args"]["root"]) not in text and "gen-a" not in text and "C:" not in text
    assert seam["args"]["expected_source_sha256"] not in text and seam["view"]["cursor_end_sha256"] not in text
    assert seam["args"]["policy_sha256"] not in text and seam["args"]["roster_sha256"] not in text
    assert "synthetic-policy" not in text and "synthetic-roster" not in text
    assert len(seam["queries"]) == 1
    config, raw, memory_raw, projection_raw, tasks, kwargs = seam["queries"][0]
    assert raw == QUERY and memory_raw == b"synthetic memory"
    assert sha(projection_raw) == seam["view"]["artifacts"]["projection_sha256"]
    assert tasks == frozenset({"public-task"}) and kwargs["expected_memory_sha256"] == seam["view"]["artifacts"]["memory_sha256"]
    assert kwargs["policy_sha256"] == "1" * 64 and kwargs["roster_sha256"] == "2" * 64
    assert config.memory == str(seam["args"]["root"] / "gen-a")  # the caller's config, never rebound
    assert sum(1 for call in seam["calls"]) >= 2  # the strict prefix verifier ran for the resolve AND for the query view


@pytest.mark.skipif(os.name != "nt", reason="VerifiedQ4Config paths are Windows drive paths")
@pytest.mark.parametrize("status", ["ABSTAIN", "DEGRADED"])
def test_abstain_and_degraded_keep_their_status_with_the_binding(answered, status):
    seam = answered
    seam["answer"] = {"status": status, "results": []}
    out = _query(seam)
    assert out["status"] == status and out["results"] == [] and out["binding"]["pointer_sha256"] == seam["args"]["expected_pointer_sha256"]


@pytest.mark.skipif(os.name != "nt", reason="VerifiedQ4Config paths are Windows drive paths")
@pytest.mark.parametrize("kind", ["anchor_pin", "encoder_pin", "pointer_pin", "config_memory", "config_descriptor_sha",
                                  "config_revision", "pointer_rewritten", "descriptor_rewritten", "projection_rewritten"])
def test_binding_pin_or_config_mismatch_refuses_before_the_query_runs(answered, kind):
    seam = answered
    config, changes = q4_config(seam), {}
    generation = seam["args"]["root"] / "gen-a"
    if kind == "anchor_pin":
        changes["expected_source_sha256"] = "f" * 64
    elif kind == "encoder_pin":
        changes["expected_encoder_sha256"] = "f" * 64
    elif kind == "pointer_pin":
        changes["expected_pointer_sha256"] = "f" * 64
    elif kind == "config_memory":
        config = q4_config(seam, memory=str(seam["args"]["root"] / "gen-b"))
    elif kind == "config_descriptor_sha":
        config = q4_config(seam, descriptor_sha256="f" * 64)
    elif kind == "config_revision":
        config = q4_config(seam, runtime_revision="b" * 40)
    elif kind == "pointer_rewritten":
        seam["pointer"].write_bytes(b"replaced after the caller pinned it")
    elif kind == "descriptor_rewritten":
        (generation / "descriptor.json").write_bytes(b"{}")
    elif kind == "projection_rewritten":
        (generation / "projection.json").write_bytes(enc({"records": []}))
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _query(seam, config=config, **changes)
    assert seam["queries"] == []


@pytest.mark.skipif(os.name != "nt", reason="VerifiedQ4Config paths are Windows drive paths")
@pytest.mark.parametrize("kind", ["anchor_pin", "encoder_pin", "config_revision", "config_memory"])
def test_source_and_encoder_pins_are_verified_before_the_resolver_and_any_backend(answered, monkeypatch, kind):
    """Caller pins bind the pointer view and the descriptor (JSON) before the resolver or anything native runs."""
    seam = answered
    monkeypatch.setattr(seam["mod"], "resolve_prefix_public_pointer",
                        lambda *a, **k: pytest.fail("the resolver ran before the caller pins were verified"))
    config, changes = q4_config(seam), {}
    if kind == "anchor_pin":
        changes["expected_source_sha256"] = "f" * 64
    elif kind == "encoder_pin":
        changes["expected_encoder_sha256"] = "f" * 64
    elif kind == "config_revision":
        config = q4_config(seam, runtime_revision="b" * 40)
    elif kind == "config_memory":
        config = q4_config(seam, memory=str(seam["args"]["root"] / "gen-b"))
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _query(seam, config=config, **changes)
    assert seam["queries"] == []


def test_refusals_carry_no_cause_context_or_internal_frames(seam):
    """A fixed refusal is raised outside every handler: no chained cause, no implicit context, no inner frames."""
    import traceback
    mod = seam["mod"]
    cases = []
    resolve_args = {key: value for key, value in seam["args"].items()
                    if key not in ("expected_source_sha256", "expected_encoder_sha256")}
    with pytest.raises(mod.PrefixPublicRefused) as info:  # direct calls: a helper frame would hide the frame count
        mod.resolve_prefix_public_pointer(**dict(resolve_args, expected_pointer_sha256="f" * 64))
    cases.append((info.value, "resolution_refused"))
    with pytest.raises(mod.PrefixPublicRefused) as info:
        mod.query_public_prefix(object(), NoRead(), **seam["args"])
    cases.append((info.value, "secure_transport_unavailable"))
    with pytest.raises(mod.PrefixPublicRefused) as info:
        mod.query_public_prefix(object(), NoRead(), **dict(seam["args"], enabled=False))
    cases.append((info.value, "disabled"))
    with pytest.raises(mod.PrefixPublicRefused) as info:
        mod._synthetic_seam_query_public_prefix(q4_config(seam), NoRead(), synthetic_backend=_unreached_backend(seam),
                                                **dict(seam["args"], expected_source_sha256="f" * 64))
    cases.append((info.value, "prefix_query_refused"))
    with pytest.raises(mod.PrefixPublicRefused) as info:
        mod._synthetic_seam_query_public_prefix(q4_config(seam), NoRead(), synthetic_backend=True, **seam["args"])
    cases.append((info.value, "synthetic_backend_required"))
    for exc, code in cases:
        assert exc.args == (code,) and str(exc) == code
        assert exc.__cause__ is None and exc.__context__ is None
        frames = traceback.extract_tb(exc.__traceback__)
        assert len(frames) <= 2, [frame.name for frame in frames]  # the test line and the public function only
        rendered = "".join(traceback.format_exception(exc))
        for private in ("_view", "_read_matching", "strict_pins", "mismatch", "ValueError:", "Traceback (most recent call last):\n  File"):
            assert rendered.count(private) <= (1 if private.startswith("Traceback") else 0), (code, private)


@pytest.mark.skipif(os.name != "nt", reason="VerifiedQ4Config paths are Windows drive paths")
@pytest.mark.parametrize("change", ["status", "shape", "text_bytes", "ref_pin", "canonical", "answer_without_results",
                                    "abstain_with_results"])
def test_invalid_or_oversize_backend_results_refuse_the_entire_response(answered, change):
    seam = answered
    item = seam["answer"]["results"][0]
    if change == "status":
        seam["answer"]["status"] = "REFUSED"
    elif change == "shape":
        item["extra"] = 1
    elif change == "text_bytes":
        item["stored_text"] = "\u00e4" * 161  # 322 UTF-8 bytes, one over the public text limit
    elif change == "ref_pin":
        item["ref_sha256"] = "zz"
    elif change == "canonical":
        item["canonical_event"] = True
    elif change == "answer_without_results":
        seam["answer"]["results"] = []
    elif change == "abstain_with_results":
        seam["answer"]["status"] = "ABSTAIN"
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _query(seam)


@pytest.mark.skipif(os.name != "nt", reason="VerifiedQ4Config paths are Windows drive paths")
def test_pointer_replaced_during_the_query_refuses_the_response(answered, monkeypatch):
    seam = answered

    def query(config, stream, memory_raw, projection_raw, tasks, **kwargs):
        seam["pointer"].write_bytes(b"replaced while the backend ran")
        return copy.deepcopy(seam["answer"])
    seam["backend"] = seam["mod"].SyntheticInProcessBackend(query)
    with pytest.raises(seam["mod"].PrefixPublicRefused, match="^prefix_query_refused$"):
        _query(seam)


def test_query_module_stays_fixed_path_only():
    import ast
    source = Path(importlib.import_module("tools.wd_q4_prefix_public_query").__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imported & {"subprocess", "socket", "urllib", "http", "ctypes", "os"}
    assert "sandbox.mind.step2.wd_q4_private_read" not in imported and "runtime_launcher" not in source
    assert "WD_Q4_READ_ENABLED" not in source and "environ" not in source and "argv" not in source
    doc = ast.get_docstring(tree)
    for word in ("stand-in", "in-process", "launcher", "UNKNOWN", "OFF", "never returned", "secure transport",
                 "fixed-peer", "snapshot binding", "NON-PRODUCTION", "withheld"):
        assert word in doc, word
    assert "from None" not in source  # refusals are raised outside every handler, never chained
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    production = functions["query_public_prefix"]
    statements = [node for node in production.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    assert statements and all(isinstance(node, (ast.If, ast.Raise)) for node in statements)
    calls = [node for node in ast.walk(production) if isinstance(node, ast.Call)]
    assert calls and all(isinstance(call.func, ast.Name) and call.func.id == "PrefixPublicRefused" for call in calls)
    assert "_synthetic_seam_query_public_prefix" in functions and "SyntheticInProcessBackend" in source
    for raise_node in (node for node in ast.walk(tree) if isinstance(node, ast.Raise)):
        assert raise_node.cause is None


def test_prefix_resolver_returns_no_private_prefix(seam):
    out = resolve(seam)
    assert out["freshness"] == "UNKNOWN"
    assert out["publisher_authentication"] == "NOT_AUTHENTICATED"
    assert out["source_filter_attestation"] == "NOT_VERIFIED"
    assert seam["pin"].sha256 not in json.dumps(out)
    assert len(seam["calls"]) == 1


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"])
@pytest.mark.parametrize("bad", [None, "0" * 64])
def test_missing_or_wrong_independent_caller_pin_refuses(seam, field, bad):
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam, **{field: bad})


def test_legacy_pointer_and_missing_prefix_refuse_without_legacy_fallback(seam, monkeypatch):
    def refused(*args, **kwargs):
        raise ValueError("legacy or missing prefix")
    monkeypatch.setattr(seam["mod"].checkpoint, "read_verified_prefix_pointer", refused)
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam)


def test_pointer_replacement_during_resolution_refuses(seam, monkeypatch):
    original = seam["mod"].resolver._read_matching
    def replacement(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if Path(path).name == "projection.json":
            seam["pointer"].write_bytes(b"changed")
        return value
    monkeypatch.setattr(seam["mod"].resolver, "_read_matching", replacement)
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam)


def test_rco_projected_record_refuses(seam):
    path = seam["args"]["root"] / "gen-a" / "projection.json"
    path.write_bytes(enc({"records": [{"meta": {"agent": "claude-rco-1", "task_id": "public-task", "type": "status"}}]}))
    seam["view"]["artifacts"]["projection_sha256"] = sha(path.read_bytes())
    with pytest.raises(seam["mod"].PrefixPublicRefused):
        resolve(seam)


@pytest.mark.skipif(os.name != "nt", reason="checkpoint requires Windows fixed volumes")
def test_real_synthetic_prefix_genesis_and_history_resolve_without_legacy_fallback(tmp_path, monkeypatch):
    pytest.importorskip("faiss", reason="synthetic builder requires FAISS")
    from tools import wd_q4_prefix_public_query as mod
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as prefix_fixtures
    from sandbox.mind.step2 import test_shared_index_builder as fixtures

    root = tmp_path / "memory"
    root.mkdir()
    first, source_a, kw_a, start = prefix_fixtures._delta(tmp_path / "log", fixtures.PUBLIC_EVENTS)
    a = fixtures.build(root, name="gen-a", proj=first["projection"], approved_tasks=None, roster=None,
                       source=source_a, allow_unbound_source=False)
    first_receipt = mod.checkpoint.commit_prefix_genesis(root, "gen-a", start.cursor, first["candidate_cursor"],
        fixtures.APPROVED, expected_pointer_sha256=None, expected_artifacts=fixtures._artifacts_of(a),
        initial_cursor=start.cursor, snapshot_result=first, expected_start_prefix=start, **kw_a)
    pins = dict(policy_bytes=fixtures.POLICY_BYTES, policy_sha256=fixtures.POLICY_SHA,
                roster_bytes=fixtures.ROSTER_BYTES, roster_sha256=fixtures.ROSTER_SHA,
                public_policy_bytes=fixtures.PUBLIC_POLICY_BYTES, public_policy_sha256=fixtures.PUBLIC_POLICY_SHA,
                enabled=True)
    def forbidden(*args, **kwargs):
        pytest.fail("legacy verifier must never be called")
    monkeypatch.setattr(mod.checkpoint, "read_verified_pointer", forbidden)
    first_binding = mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=first_receipt["pointer_sha256"], **pins)
    assert first_binding["generation"] == "gen-a" and first["candidate_prefix"].sha256 not in json.dumps(first_binding)
    with pytest.raises(mod.PrefixPublicRefused):
        mod.resolve_prefix_public_pointer(root, expected_pointer_sha256="0" * 64, **pins)
    prior = first["candidate_prefix"]
    second, source_b, kw_b, _ = prefix_fixtures._delta(tmp_path / "log", fixtures.LATER_PUBLIC_EVENTS,
                                                        prior.cursor, prior)
    b = fixtures.build(root, name="gen-b", proj=second["projection"], approved_tasks=None, roster=None,
        source=source_b, allow_unbound_source=False,
        predecessor=dict(fixtures.predecessor_of(root, "gen-a", a),
                         watermark={"root": str(root), "pointer_sha256": first_receipt["pointer_sha256"]}))
    next_receipt = mod.checkpoint.commit_prefix_history(root, "gen-b", prior.cursor, second["candidate_cursor"],
        fixtures.APPROVED, snapshot_result=second, expected_pointer_sha256=first_receipt["pointer_sha256"],
        expected_artifacts=fixtures._artifacts_of(b), expected_start_prefix=prior, **kw_b)
    next_binding = mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=next_receipt["pointer_sha256"], **pins)
    assert next_binding["generation"] == "gen-b" and prior.sha256 not in json.dumps(next_binding)
    with pytest.raises(mod.PrefixPublicRefused):
        mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=first_receipt["pointer_sha256"], **pins)
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir()
    legacy, anchor_hash, anchor_bytes, cursor = fixtures._real_public_snapshot(
        mod.snapshot, tmp_path / "legacy-log", fixtures.PUBLIC_EVENTS)
    old = fixtures.build(legacy_root, name="gen-old", proj=legacy["projection"], approved_tasks=None, roster=None,
                         source=fixtures._public_source(legacy, anchor_hash, anchor_bytes), allow_unbound_source=False)
    mod.checkpoint.commit_generation(legacy_root, "gen-old", cursor, legacy["candidate_cursor"], fixtures.APPROVED,
        expected_pointer_sha256=None, expected_artifacts=fixtures._artifacts_of(old), initial_cursor=cursor,
        snapshot_result=legacy, policy_bytes=fixtures.POLICY_BYTES, expected_policy_sha256=fixtures.POLICY_SHA,
        roster_bytes=fixtures.ROSTER_BYTES, expected_roster_sha256=fixtures.ROSTER_SHA,
        expected_anchor_sha256=anchor_hash, expected_anchor_bytes=anchor_bytes, **fixtures.PUBLIC_CHECKPOINT_KWARGS)
    legacy_pointer = sha((legacy_root / mod.checkpoint.POINTER).read_bytes())
    with pytest.raises(mod.PrefixPublicRefused):
        mod.resolve_prefix_public_pointer(legacy_root, expected_pointer_sha256=legacy_pointer, **pins)


# ---------------------------------------------------------------------------------------------------------------
# the real synthetic chain: child publication -> prefix resolve -> ONE agent query answered from the built memory
# (the launcher is replaced by an explicitly labeled IN-PROCESS STAND-IN; no runtime tree, model or service exists)
# ---------------------------------------------------------------------------------------------------------------

from sandbox.mind.step2.test_wd_q4_prefix_build_child import backend, model_dir  # noqa: E402,F401 - pytest fixtures


def _in_process_launcher_stand_in(record, model_path):
    """SYNTHETIC STAND-IN for runtime_launcher.launch: the real q4.Q4 over the built memory, in THIS process, with the
    same fake word-hash encoder the child loaded. It proves the data path, not the runtime, the isolation or a model."""
    from sandbox.mind.step2 import memory as memory_mod, q4
    from sandbox.mind.step2.test_wd_q4_prefix_build_child import FakeModel

    class QueryEncoder:
        model = FakeModel(str(model_path), device="cpu", local_files_only=True)

        def query(self, text):
            return self.model.encode(["query: " + text])[0]

    def launch(**kwargs):
        record.append(kwargs)
        query = json.loads(kwargs["query"].decode("utf-8"))
        tuple_args = kwargs["tuple_args"]
        generation = Path(tuple_args["memory"])
        answer = q4.Q4(memory_mod.Memory(generation), QueryEncoder()).ask(query["situation"], query["time"])
        return {"status": answer["status"], "scope": "wd.q4-shared-projected-events.v1",
                "descriptor_sha256": tuple_args["descriptor-sha256"],
                "memory_sha256": sha((generation / "memory.json").read_bytes()),
                "results": [{"ref_sha256": sha(item["ref"].encode()), "doc_id_sha256": sha(item["doc_id"].encode()),
                             "ref_kind": item["ref_kind"], "canonical_event": item["canonical_event"]}
                            for item in answer["results"]]}
    return launch


@pytest.mark.skipif(os.name != "nt", reason="private state and the launcher config need Windows fixed volumes")
def test_child_publication_is_answered_by_one_synthetic_agent_query(tmp_path, model_dir, backend, monkeypatch):
    pytest.importorskip("faiss", reason="the synthetic builder needs FAISS")
    from sandbox.mind.step2 import encode as encode_mod
    from sandbox.mind.step2 import shared_producer as old
    from sandbox.mind.step2 import test_prefix_checkpoint_v1 as pf
    from sandbox.mind.step2 import test_shared_index_builder as f
    from sandbox.mind.step2 import test_wd_q4_prefix_build_child as child_tests
    from tools import wd_q4_agent_query as q4_query
    from tools import wd_q4_prefix_public_query as mod

    events = [f.public_event(ts_utc="2026-10-31T12:00:00Z"),
              f.public_event(type="done", status="done", agent="codex-tools-1", ts_utc="2026-10-31T12:05:00Z"),
              f.public_event(status="info", agent="fable-5", ts_utc="2026-10-31T12:10:00Z"),
              f.public_event(type="done", status="done", agent="codex-tools-1", ts_utc="2026-10-31T12:15:00Z"),
              f.public_event(type="done", status="handoff", agent="fable-5", ts_utc="2026-10-31T12:20:00Z"),
              f.public_event(status="in_progress", agent="codex-lead-1", ts_utc="2026-10-31T12:25:00Z")]
    root, private_root = tmp_path / "state", tmp_path / "private-state"
    root.mkdir(), private_root.mkdir()
    first, _, kw_a, start = pf._delta(tmp_path / "log", events)
    anchor = json.loads(kw_a["expected_anchor_bytes"])
    source = old.SourceConfig(source=anchor["source_path"], generation_path=anchor["generation_path"], cursor=start.cursor,
        anchor_bytes=kw_a["expected_anchor_bytes"], anchor_sha256=kw_a["expected_anchor_sha256"],
        policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES, roster_sha256=f.ROSTER_SHA,
        max_bytes=1 << 20, max_rows=100)
    config = old.PublicSourceConfig(source=source, public_policy_bytes=f.PUBLIC_POLICY_BYTES, public_policy_sha256=f.PUBLIC_POLICY_SHA)
    pins = {"anchor_sha256": kw_a["expected_anchor_sha256"], "policy_sha256": f.POLICY_SHA, "roster_sha256": f.ROSTER_SHA,
            "public_policy_sha256": f.PUBLIC_POLICY_SHA}
    # 1. the child publishes gen-a through the fixed encoder path (fake backend, real builder, real prefix checkpoint)
    report = child_tests.run(child_tests.module(), child_tests.plan(model_dir, pins, generation="gen-a"), config, start,
                             root, private_root)
    assert report["status"] == "PUBLISHED_GENESIS" and report["generation"] == "gen-a"
    # 2. the caller resolves by the pointer digest the producer RETURNED (never re-derived from disk)
    read_pins = dict(policy_bytes=f.POLICY_BYTES, policy_sha256=f.POLICY_SHA, roster_bytes=f.ROSTER_BYTES,
                     roster_sha256=f.ROSTER_SHA, public_policy_bytes=f.PUBLIC_POLICY_BYTES,
                     public_policy_sha256=f.PUBLIC_POLICY_SHA)
    binding = mod.resolve_prefix_public_pointer(root, expected_pointer_sha256=report["pointer_sha256"], enabled=True, **read_pins)
    assert binding["generation"] == "gen-a" and binding["memory_root"] == str(root / "gen-a")
    # 3. the caller's independent pins: the plan's encoder record and the anchor bytes it supplied to the producer
    encoder_pin = sha(enc({"model": encode_mod.MODEL, "revision": child_tests.REVISION, "weights_sha256": child_tests.WEIGHTS_SHA}))
    anchor_pin = kw_a["expected_anchor_sha256"]
    q4_config = q4_query.VerifiedQ4Config(interpreter=r"C:\synthetic\python.exe", interpreter_sha256="6" * 64,
        bootstrap=r"C:\synthetic\bootstrap.py", bootstrap_sha256="7" * 64, verifier_sha256="8" * 64,
        runtime_root=r"C:\synthetic\runtime", runtime_manifest=r"C:\synthetic\manifest.json", runtime_manifest_sha256="9" * 64,
        runtime_generation="b" * 40, runtime_revision=child_tests.REVISION, memory=binding["memory_root"],
        descriptor=binding["descriptor_path"], descriptor_sha256=binding["descriptor_sha256"],
        lock_file=r"C:\synthetic\reader.lock", lock_timeout="1", timeout=5, scratch_root=r"C:\synthetic\scratch")
    launches = []
    monkeypatch.setattr(q4_query, "_invoke_launcher", _in_process_launcher_stand_in(launches, model_dir))
    # the NON-PRODUCTION seam marker: the real agent-query resolver with its launcher replaced above
    backend = mod.SyntheticInProcessBackend(lambda *a, **k: q4_query.query_and_resolve(*a, **k))
    situation, when = "what did codex-tools-1 finish done", "2026-10-31T13:00:00Z"
    stdin = io.BytesIO(json.dumps({"situation": situation, "time": when}).encode())
    query_args = dict(root=root, expected_pointer_sha256=report["pointer_sha256"], expected_source_sha256=anchor_pin,
                      expected_encoder_sha256=encoder_pin, enabled=True, **read_pins)
    # 4a. the PRODUCTION entry refuses before stdin, the launcher or FAISS: no verified transport exists
    with pytest.raises(mod.PrefixPublicRefused, match="^secure_transport_unavailable$"):
        mod.query_public_prefix(q4_config, NoRead(), **query_args)
    assert launches == []
    # 4b. ONE agent query through the internal seam
    out = mod._synthetic_seam_query_public_prefix(q4_config, stdin, synthetic_backend=backend, **query_args)
    assert out["schema"] == "wd.q4-prefix-public-query.v1" and out["status"] == "ANSWER" and out["results"]
    projection = json.loads(Path(binding["projection_path"]).read_bytes())
    texts = {record["stored_text"] for record in projection["records"]}
    assert all(item["stored_text"] in texts and item["canonical_event"] is False
               and item["ref_kind"] == "sha256_of_projected_metadata" for item in out["results"])
    assert any("codex-tools-1" in item["stored_text"] for item in out["results"])
    assert out["binding"] == {"pointer_sha256": report["pointer_sha256"], "encoder_sha256": encoder_pin,
                              "public_policy_sha256": f.PUBLIC_POLICY_SHA,
                              **{key: binding[key] for key in ("descriptor_sha256", "memory_sha256", "projection_sha256", "receipt_sha256")}}
    assert out["withheld"] == ["policy_sha256", "roster_sha256", "source_anchor_sha256"]
    for field in ("publisher_authentication", "source_filter_attestation", "freshness", "same_user_aba"):
        assert out[field] == "UNKNOWN"
    text = json.dumps(out)
    assert f.SENTINEL not in text and situation not in text and str(tmp_path) not in text and str(model_dir) not in text
    assert start.sha256 not in text and first["candidate_prefix"].sha256 not in text and child_tests.WEIGHTS_SHA not in text
    assert anchor_pin not in text and f.POLICY_SHA not in text and f.ROSTER_SHA not in text
    assert kw_a["expected_anchor_bytes"].decode("utf-8", "replace")[:40] not in text
    assert len(launches) == 1 and launches[0]["tuple_args"]["memory"] == binding["memory_root"]
    assert json.loads(launches[0]["query"].decode()) == {"situation": situation, "time": when}

    # 5. negatives on the SAME publication: every wrong pin refuses before the stand-in runs again
    def refused(**changes):
        args = dict(query_args)
        args.update(changes)
        with pytest.raises(mod.PrefixPublicRefused, match="^prefix_query_refused$"):
            mod._synthetic_seam_query_public_prefix(args.pop("config", q4_config), NoRead(), synthetic_backend=backend, **args)
    refused(expected_source_sha256="f" * 64)
    refused(expected_encoder_sha256="f" * 64)
    refused(expected_pointer_sha256="f" * 64)
    refused(policy_sha256="f" * 64)
    refused(config=q4_query.VerifiedQ4Config(**dict(vars(q4_config), runtime_revision="d" * 40)))
    refused(config=q4_query.VerifiedQ4Config(**dict(vars(q4_config), memory=str(root / "gen-b"))))
    pointer = root / mod.checkpoint.POINTER
    kept = pointer.read_bytes()
    pointer.write_bytes(kept + b"\n")
    refused()
    pointer.write_bytes(kept)
    with pytest.raises(mod.PrefixPublicRefused, match="^disabled$"):
        mod._synthetic_seam_query_public_prefix(q4_config, NoRead(), synthetic_backend=backend,
                                                **dict(query_args, enabled=False))
    with pytest.raises(mod.PrefixPublicRefused, match="^synthetic_backend_required$"):
        mod._synthetic_seam_query_public_prefix(q4_config, NoRead(), synthetic_backend=True, **query_args)
    assert len(launches) == 1
    # the restored pointer answers again: the refusals above left no state behind
    stdin = io.BytesIO(json.dumps({"situation": situation, "time": when}).encode())
    again = mod._synthetic_seam_query_public_prefix(q4_config, stdin, synthetic_backend=backend, **query_args)
    assert again["results"] == out["results"] and len(launches) == 2
