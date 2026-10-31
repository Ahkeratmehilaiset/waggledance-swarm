"""Synthetic launcher checks; no real memory, network, installation or model."""

import hashlib
import json
import os
from pathlib import Path
import sys
import time

import pytest

from sandbox.mind.step2 import runtime_launcher as launcher


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows job containment required")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@pytest.fixture
def candidate(tmp_path):
    bootstrap = tmp_path / "runtime_bootstrap.py"
    bootstrap.write_text("""import json,sys\nsys.stdin.buffer.read()\nprint(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1','identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'synthetic','results':[]}))\nprint('private stderr',file=sys.stderr)\nsys.exit(2)\n""", encoding="utf-8")
    verifier = tmp_path / "runtime_manifest.py"
    verifier.write_text("# synthetic only\n", encoding="utf-8")
    return dict(interpreter=Path(sys.executable), interpreter_sha256=digest(sys.executable),
                bootstrap=bootstrap, bootstrap_sha256=digest(bootstrap), verifier_sha256=digest(verifier),
                tuple_args={"runtime-root": str(tmp_path), "runtime-manifest": str(tmp_path / "manifest.json"),
                            "runtime-manifest-sha256": "a" * 64, "runtime-generation": "generation",
                            "runtime-revision": "revision", "memory": str(tmp_path / "memory"),
                            "descriptor": str(tmp_path / "descriptor.json"), "descriptor-sha256": "b" * 64,
                            "lock-file": str(tmp_path / "read.lock"), "lock-timeout": "1"},
                query=b'{"situation":"synthetic","time":"2026-01-01T00:00:00Z"}', timeout=5)


def test_private_stderr_suppressed_and_stdin_not_in_args(candidate, capsys):
    result = launcher.launch(**candidate)
    assert result == {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
                      "status": "REFUSED", "reason": "reader_refused", "results": []}
    assert capsys.readouterr().err == ""


def test_query_is_delivered_only_on_stdin_in_isolated_mode(candidate):
    candidate["bootstrap"].write_text(
        "import json,sys\n"
        "query=sys.stdin.buffer.read()\n"
        "assert b'synthetic_secret' in query and all('synthetic_secret' not in a for a in sys.argv)\n"
        "assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode\n"
        "print(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1',"
        "'identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'synthetic','results':[]}))\n"
        "sys.exit(2)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    candidate["query"] = b'{"situation":"synthetic_secret","time":"2026-01-01T00:00:00Z"}'
    assert launcher.launch(**candidate)["status"] == "REFUSED"


def test_extra_private_output_fields_are_discarded(candidate):
    candidate["bootstrap"].write_text(
        "import json,sys\n"
        "print(json.dumps({'schema':'wd.bridge-mind-q4-private-read.v1',"
        "'identity':'caller_declared_not_authenticated','status':'REFUSED','reason':'synthetic',"
        "'results':[], 'extra_private_field':'private text'}))\n"
        "sys.exit(2)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    assert "private" not in json.dumps(launcher.launch(**candidate)).replace("private-read", "")


def test_duplicate_json_keys_are_rejected(candidate):
    candidate["bootstrap"].write_text(
        "print('{\\\"status\\\":\\\"REFUSED\\\",\\\"status\\\":\\\"ANSWER\\\"}')\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher.launch(**candidate)


def test_projected_result_keeps_only_hash_checked_refs(candidate):
    ref = "a" * 64
    digest_ref = hashlib.sha256(ref.encode()).hexdigest()
    doc = "b" * 64
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "reason": None, "rule": "q4-rule-v1.2", "private": "discard me",
               "binding": {"descriptor_sha256": "b" * 64, "memory_sha256": "c" * 64,
                           "scope": "wd.q4-shared-projected-events.v1"},
               "results": [{"ref": ref, "ref_sha256": digest_ref, "doc_id": doc,
                            "doc_id_sha256": hashlib.sha256(doc.encode()).hexdigest(),
                            "kind": "event", "cell": "cell1", "tier": "private", "time_utc": None,
                            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                            "score": 1.0, "fused": 0.5, "extra": "discard me"}]}
    result = launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)
    assert result["results"][0]["ref_sha256"] == digest_ref
    assert "ref" not in result["results"][0]
    assert "discard me" not in json.dumps(result)
    payload["results"][0]["ref_sha256"] = "0" * 64
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


def test_untrusted_tokens_are_never_forwarded(candidate):
    ref = "a" * 64
    item = {"ref": ref, "ref_sha256": hashlib.sha256(ref.encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": "event", "cell": "secret_cell",
            "tier": "secret_tier", "time_utc": None, "ref_kind": "sha256_of_projected_metadata",
            "canonical_event": False,
            "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "reason": "secret_reason", "rule": "secret_rule",
               "binding": {"descriptor_sha256": "b" * 64, "memory_sha256": "c" * 64,
                           "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    result = launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)
    assert "secret_" not in json.dumps(result)


def test_refusal_reason_is_fixed_not_child_supplied(candidate):
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "REFUSED", "reason": "secret_reason", "results": []}
    result = launcher._validate_output(json.dumps(payload).encode(), 2, "b" * 64)
    assert result["reason"] == "reader_refused"
    assert "secret_reason" not in json.dumps(result)


def test_early_missing_import_refusal_without_identity_is_fixed_shape():
    early = {"schema": "wd.bridge-mind-q4-private-read.v1", "status": "REFUSED",
             "reason": "runtime_unavailable", "results": []}
    result = launcher._validate_output(json.dumps(early).encode(), 2, "b" * 64)
    assert result == early
    for alteration, code in (({"reason": "secret_reason"}, 2), ({"extra": "private"}, 2),
                             ({"results": ["private"]}, 2), ({"status": "ANSWER"}, 2), ({}, 0)):
        mutated = dict(early, **alteration)
        with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
            launcher._validate_output(json.dumps(mutated).encode(), code, "b" * 64)


def test_correct_shared_scope_is_required_and_forwarded():
    scope = "wd.q4-shared-projected-events.v1"
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ABSTAIN", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": scope}, "results": []}
    assert launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)["scope"] == scope
    for bad_scope in (None, "wd.q4-private-memory.v1", "wd.q4-shared-projected-events.v2"):
        if bad_scope is None:
            payload["binding"].pop("scope", None)
        else:
            payload["binding"]["scope"] = bad_scope
        with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
            launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


def test_legacy_reference_is_refused_even_with_shared_binding():
    ref = "a" * 64
    item = {"ref": ref, "ref_sha256": hashlib.sha256(ref.encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": "event", "cell": "c", "tier": "t", "time_utc": None,
            "ref_kind": None, "canonical_event": None, "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


@pytest.mark.parametrize("kind,ref", [("lesson", "a" * 64), ("event", "a" * 40),
                                      ("event", "event:" + "a" * 64), ("event", None)])
def test_shared_binding_rejects_nonprojected_reference(kind, ref):
    item = {"ref": ref, "ref_sha256": hashlib.sha256((ref or "empty").encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": kind, "cell": "c", "tier": "t", "time_utc": None,
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False, "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


@pytest.mark.parametrize("canonical", [0, True, None])
def test_projected_reference_requires_explicit_false(candidate, canonical):
    ref = "a" * 64
    item = {"ref": ref, "ref_sha256": hashlib.sha256(ref.encode()).hexdigest(), "doc_id": None,
            "doc_id_sha256": "b" * 64, "kind": "event", "cell": "c", "tier": "t", "time_utc": None,
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": canonical, "score": 1, "fused": 1}
    payload = {"schema": "wd.bridge-mind-q4-private-read.v1", "identity": "caller_declared_not_authenticated",
               "status": "ANSWER", "rule": "v1", "binding": {"descriptor_sha256": "b" * 64,
               "memory_sha256": "c" * 64, "scope": "wd.q4-shared-projected-events.v1"}, "results": [item]}
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher._validate_output(json.dumps(payload).encode(), 0, "b" * 64)


@pytest.mark.parametrize("key", ["bootstrap_sha256", "verifier_sha256", "interpreter_sha256"])
def test_modified_trusted_file_refuses_before_launch(candidate, key):
    candidate[key] = "0" * 64
    with pytest.raises(launcher.LaunchRefused, match="trust_pin_mismatch"):
        launcher.launch(**candidate)


def test_nonlocal_and_relative_paths_refused(candidate):
    candidate["tuple_args"]["memory"] = "relative-memory"
    with pytest.raises(launcher.LaunchRefused, match="invalid_path"):
        launcher.launch(**candidate)


def test_invalid_output_is_not_forwarded(candidate):
    candidate["bootstrap"].write_text("print('private output should not propagate')", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    with pytest.raises(launcher.LaunchRefused, match="invalid_output"):
        launcher.launch(**candidate)


def test_oversized_output_is_refused(candidate):
    candidate["bootstrap"].write_text("print('x'*3000000)", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    with pytest.raises(launcher.LaunchRefused, match="output_limit"):
        launcher.launch(**candidate)


def test_watchdog_terminates_parent_and_descendant(candidate, tmp_path):
    marker = tmp_path / "descendant-ran.txt"
    candidate["bootstrap"].write_text(
        "import subprocess,sys,time\n"
        "subprocess.Popen([sys.executable, '-c', "
        + repr("import pathlib,time; time.sleep(2); pathlib.Path(" + repr(str(marker)) + ").write_text('late')")
        + "])\ntime.sleep(30)\n", encoding="utf-8")
    candidate["bootstrap_sha256"] = digest(candidate["bootstrap"])
    candidate["timeout"] = 0.5
    with pytest.raises(launcher.LaunchRefused, match="watchdog_timeout"):
        launcher.launch(**candidate)
    time.sleep(3)
    assert not marker.exists()


def test_query_shape_and_watchdog_are_bounded(candidate):
    candidate["query"] = b"x" * 65537
    with pytest.raises(launcher.LaunchRefused, match="invalid_query"):
        launcher.launch(**candidate)
    candidate["query"] = b"{}"
    candidate["timeout"] = 1801
    with pytest.raises(launcher.LaunchRefused, match="invalid_timeout"):
        launcher.launch(**candidate)
