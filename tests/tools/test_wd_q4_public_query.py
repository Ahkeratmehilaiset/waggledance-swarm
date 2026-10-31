"""PROPOSAL ONLY: synthetic adapter seam tests, no model/native/runtime calls.

Apply this file alone and preserve RED before applying the proposed module.
These mocks test adapter wiring, not real checkpoint/Q4 compatibility or source
authenticity. Existing public-source filter and real-chain consumers must also
be run by the custodian under its approved D15 tuple.
"""
import ast
import copy
import dataclasses
import hashlib
import importlib
import io
import json
from pathlib import Path

import pytest

from tools import wd_q4_agent_query as q4


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


@pytest.fixture
def seam(monkeypatch):
    mod = importlib.import_module("tools.wd_q4_public_query")
    pointer = b"synthetic-pinned-pointer"
    memory = b"synthetic-memory-seam-only"
    projection = b"synthetic-projection-seam-only"
    encoder = {"model": "synthetic-no-model", "revision": "a" * 40,
               "weights_sha256": "c" * 64}
    descriptor = {"schema": "wd.bridge-mind-q4-memory-descriptor.v2",
                  "scope": "wd.q4-shared-projected-events.v1",
                  "memory_sha256": sha(memory), "dim": 3, "encoder": encoder}
    blobs = {"q4-latest.json": pointer, "memory.json": memory,
             "projection.json": projection, "descriptor.json": canonical(descriptor)}
    artifacts = {"descriptor_sha256": sha(blobs["descriptor.json"]),
                 "memory_sha256": sha(memory), "projection_sha256": sha(projection),
                 "receipt_sha256": "e" * 64}
    args = {"root": r"C:\synthetic", "expected_pointer_sha256": sha(pointer),
            "expected_source_sha256": "d" * 64,
            "expected_encoder_sha256": sha(canonical(encoder)),
            "policy_bytes": b"synthetic-policy", "policy_sha256": "1" * 64,
            "roster_bytes": b"synthetic-roster", "roster_sha256": "2" * 64,
            "public_policy_bytes": b"synthetic-public", "public_policy_sha256": "3" * 64,
            "enabled": True}
    binding = {"memory_root": r"C:\synthetic\gen-a",
               "descriptor_path": r"C:\synthetic\gen-a\descriptor.json",
               "projection_path": r"C:\synthetic\gen-a\projection.json",
               "generation": "gen-a", **artifacts,
               "pointer_sha256": args["expected_pointer_sha256"],
               "policy_sha256": args["policy_sha256"],
               "roster_sha256": args["roster_sha256"],
               "public_policy_sha256": args["public_policy_sha256"],
               "source_mode": "public-v1"}
    view = {"pointer_sha256": args["expected_pointer_sha256"], "generation": "gen-a",
            "artifacts": dict(artifacts), "anchor_sha256": args["expected_source_sha256"],
            "cursor_end_sha256": "4" * 64, "watermark_sha256": "5" * 64}
    config = q4.VerifiedQ4Config(
        interpreter=r"C:\synthetic\python.exe", interpreter_sha256="6" * 64,
        bootstrap=r"C:\synthetic\bootstrap.py", bootstrap_sha256="7" * 64,
        verifier_sha256="8" * 64, runtime_root=r"C:\synthetic\runtime",
        runtime_manifest=r"C:\synthetic\manifest.json", runtime_manifest_sha256="9" * 64,
        runtime_generation="b" * 40, runtime_revision=encoder["revision"],
        memory=binding["memory_root"], descriptor=binding["descriptor_path"],
        descriptor_sha256=artifacts["descriptor_sha256"], lock_file=r"C:\synthetic\reader.lock",
        lock_timeout="1", timeout=5, scratch_root=r"C:\synthetic\scratch")
    answer = {"status": "ANSWER", "results": [
        {"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64,
         "stored_text": "SYNTHETIC_STORED_TEXT_NOT_TO_BE_ECHOED",
         "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}]}
    state = {"mod": mod, "args": args, "config": config, "binding": binding,
             "view": view, "blobs": blobs, "descriptor": descriptor,
             "answer": answer, "calls": [], "reads": [], "queries": []}
    def resolve(root, **kwargs):
        state["calls"].append(("resolve", root, kwargs))
        return copy.deepcopy(binding)
    def verify(root, tasks, **kwargs):
        state["calls"].append(("verify", root, tasks, kwargs))
        return copy.deepcopy(view)
    def read(path, expected, *, limit):
        name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
        state["reads"].append((name, expected, limit))
        raw = blobs[name]
        if len(raw) > limit or sha(raw) != expected:
            raise ValueError("synthetic hash refusal")
        return raw
    def query(config, stream, memory_raw, projection_raw, tasks, **kwargs):
        state["queries"].append((config, stream.read(), memory_raw, projection_raw, tasks, kwargs))
        return copy.deepcopy(state["answer"])
    monkeypatch.setattr(mod.pointer_resolver, "resolve_public_pointer", resolve)
    monkeypatch.setattr(mod.pointer_resolver.checkpoint, "read_verified_pointer", verify)
    monkeypatch.setattr(mod.pointer_resolver, "_read_matching", read)
    monkeypatch.setattr(mod.pointer_resolver.adapter, "strict_pins",
                        lambda *a: (frozenset({"public-task", "nonpublic-policy-task"}), {}))
    monkeypatch.setattr(mod.q4_query, "query_and_resolve", query)
    return state


def invoke(s, **changes):
    args = dict(s["args"], **changes)
    return s["mod"].query_public(s["config"],
                                io.BytesIO(b'{"situation":"SYNTHETIC_QUERY","time":"2000-01-01T00:00:00Z"}'),
                                **args)


@pytest.mark.parametrize("enabled", [False, None, 0, 1, "1"])
def test_disabled_has_no_stdin_resolver_backend_or_artifact_effect(seam, enabled):
    class NoRead:
        def read(self, *args):
            raise AssertionError("disabled must not consume input")
    args = dict(seam["args"], enabled=enabled)
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^disabled$"):
        seam["mod"].query_public(object(), NoRead(), **args)
    assert seam["calls"] == [] and seam["reads"] == [] and seam["queries"] == []


def test_enabled_exact_binding_provenance_and_no_text_echo(seam):
    out = invoke(seam)
    assert out["status"] == "ANSWER"
    assert out["results"] == [{key: value for key, value in seam["answer"]["results"][0].items()
                               if key != "stored_text"}]
    assert out["binding"]["pointer_sha256"] == seam["args"]["expected_pointer_sha256"]
    assert out["binding"]["source_anchor_sha256"] == seam["args"]["expected_source_sha256"]
    assert out["binding"]["encoder_sha256"] == seam["args"]["expected_encoder_sha256"]
    assert out["binding"]["memory_sha256"] == seam["binding"]["memory_sha256"]
    for field in ("publisher_authentication", "source_filter_attestation", "freshness", "same_user_aba"):
        assert out[field] == "UNKNOWN"
    encoded = json.dumps(out)
    assert "SYNTHETIC_QUERY" not in encoded and "SYNTHETIC_STORED_TEXT" not in encoded
    assert "C:" not in encoded and "gen-a" not in encoded
    assert len(seam["queries"]) == 1
    call = seam["queries"][0]
    assert call[0] is seam["config"]
    assert b"SYNTHETIC_QUERY" in call[1]
    assert call[2] == seam["blobs"]["memory.json"]
    assert call[3] == seam["blobs"]["projection.json"]
    assert call[4] == frozenset({"public-task", "nonpublic-policy-task"})
    assert call[5]["expected_memory_sha256"] == seam["binding"]["memory_sha256"]
    resolve_args = seam["calls"][0][2]
    assert resolve_args["expected_pointer_sha256"] == seam["args"]["expected_pointer_sha256"]
    assert resolve_args["policy_sha256"] == seam["args"]["policy_sha256"]
    assert resolve_args["public_policy_sha256"] == seam["args"]["public_policy_sha256"]
    assert resolve_args["enabled"] is True
    verify_args = seam["calls"][1][3]
    assert verify_args["expected_mode"] == "public-v1"
    assert verify_args["expected_public_policy_sha256"] == seam["args"]["public_policy_sha256"]
    assert [entry[0] for entry in seam["reads"]].count("q4-latest.json") == 2


@pytest.mark.parametrize("field", ["expected_pointer_sha256", "expected_source_sha256",
                                    "expected_encoder_sha256", "policy_sha256",
                                    "roster_sha256", "public_policy_sha256"])
@pytest.mark.parametrize("value", [None, "", "A" * 64])
def test_bad_or_missing_pin_refuses_before_resolver(seam, field, value):
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam, **{field: value})
    assert seam["calls"] == [] and seam["queries"] == []


@pytest.mark.parametrize("field", ["policy_sha256", "roster_sha256", "public_policy_sha256",
                                    "pointer_sha256", "source_mode"])
def test_binding_pin_or_mode_mismatch_refuses_before_backend(seam, field):
    seam["binding"][field] = "legacy" if field == "source_mode" else "f" * 64
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)
    assert seam["queries"] == []


@pytest.mark.parametrize("field", ["descriptor_sha256", "memory_sha256",
                                    "projection_sha256", "receipt_sha256"])
def test_reverified_artifact_mismatch_refuses(seam, field):
    seam["view"]["artifacts"][field] = "f" * 64
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)
    assert seam["queries"] == []


def test_independent_source_anchor_pin_mismatch_refuses(seam):
    seam["view"]["anchor_sha256"] = "f" * 64
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)
    assert seam["queries"] == []


def test_independent_encoder_pin_mismatch_refuses(seam):
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam, expected_encoder_sha256="f" * 64)
    assert seam["queries"] == []


def test_config_is_not_silently_rebound_to_resolved_generation(seam):
    seam["config"] = dataclasses.replace(seam["config"], memory=r"C:\synthetic\old")
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)
    assert seam["queries"] == []


def test_encoder_identity_change_refuses_even_with_new_matching_descriptor_pin(seam):
    seam["descriptor"]["encoder"]["weights_sha256"] = "f" * 64
    raw = canonical(seam["descriptor"])
    digest = sha(raw)
    seam["blobs"]["descriptor.json"] = raw
    seam["binding"]["descriptor_sha256"] = digest
    seam["view"]["artifacts"]["descriptor_sha256"] = digest
    seam["config"] = dataclasses.replace(seam["config"], descriptor_sha256=digest)
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)
    assert seam["queries"] == []


def test_resolver_refusal_is_not_reinterpreted_as_public_authentication(seam, monkeypatch):
    def refused(*args, **kwargs):
        raise ValueError("SYNTHETIC_PRIVATE_ROW_OR_SOURCE_BOUNDARY_REFUSAL")
    monkeypatch.setattr(seam["mod"].pointer_resolver, "resolve_public_pointer", refused)
    with pytest.raises(seam["mod"].PublicQueryRefused) as exc:
        invoke(seam)
    assert str(exc.value) == "public_query_refused"
    assert seam["queries"] == []


def test_ordinary_pointer_replacement_before_query_refuses(seam):
    seam["blobs"]["q4-latest.json"] = b"replacement"
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)
    assert seam["queries"] == []


def test_ordinary_pointer_replacement_during_query_refuses(seam, monkeypatch):
    def moved(*args, **kwargs):
        seam["blobs"]["q4-latest.json"] = b"replacement"
        return copy.deepcopy(seam["answer"])
    monkeypatch.setattr(seam["mod"].q4_query, "query_and_resolve", moved)
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)


@pytest.mark.parametrize("field,value", [("canonical_event", True),
                                         ("ref_kind", "canonical_event"),
                                         ("doc_id_sha256", "bad")])
def test_result_reference_provenance_mismatch_refuses(seam, field, value):
    seam["answer"]["results"][0][field] = value
    with pytest.raises(seam["mod"].PublicQueryRefused, match="^public_query_refused$"):
        invoke(seam)


def test_no_network_subprocess_private_reader_import_or_cli_activation(seam):
    tree = ast.parse(Path(seam["mod"].__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"subprocess", "socket", "requests", "urllib", "http", "os"}
    assert "wd_q4_private_read" not in Path(seam["mod"].__file__).read_text(encoding="utf-8")
