"""Synthetic lane link; no installed bundle, model or real bridge is touched."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import wd_q4_lane_link as link


LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


class NoRead:
    def read(self, *args):
        raise AssertionError("query was read before authorization")


@pytest.fixture
def plan(tmp_path):
    root = tmp_path / ("a" * 40)
    root.mkdir()
    wrapper = root / "Invoke-WdQ4Query.ps1"
    wrapper.write_bytes(b"synthetic wrapper; never executed")
    wrapper_sha = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    manifest = {"schema_version": 1, "source_commit": "a" * 40,
                "files": {"Invoke-WdQ4Query.ps1": wrapper_sha}}
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "deployment-manifest.json").write_bytes(raw)
    return {"bundle_root": root, "expected_commit": "a" * 40,
            "expected_manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "expected_wrapper_sha256": wrapper_sha}


def _reply():
    return json.dumps({"schema": "wd.q4-agent-wrapper.v1", "status": "ABSTAIN",
                       "scope": "wd.q4-shared-projected-events.v1",
                       "descriptor_sha256": "b" * 64, "memory_sha256": "c" * 64,
                       "content_mode": "hash_only_unresolved", "results": []}).encode()


def test_default_off_and_missing_transport_never_read_or_execute(plan):
    with pytest.raises(link.LaneLinkRefused, match="^disabled$"):
        link.invoke(lane=LANES[0], query_bytes=NoRead(), **plan)
    with pytest.raises(link.LaneLinkRefused, match="^transport_unavailable$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), **plan)


@pytest.mark.parametrize("lane", LANES)
def test_five_declared_lanes_send_query_only_through_stdin_seam(plan, lane):
    calls = []
    query = b'{"situation":"synthetic-secret","time":"2026-01-01T00:00:00Z"}'

    def transport(*, wrapper, arguments, stdin_bytes):
        calls.append((wrapper, arguments, stdin_bytes))
        return _reply(), 0

    value = link.invoke(enabled=True, lane=lane, query_bytes=query, transport=transport, **plan)
    assert value["status"] == "ABSTAIN"
    assert len(calls) == 1 and calls[0][2] == query
    assert b"synthetic-secret" not in repr(calls[0][:2]).encode()
    assert calls[0][0] == str(plan["bundle_root"] / "Invoke-WdQ4Query.ps1")
    assert calls[0][1] == ("-BundleRoot", str(plan["bundle_root"]), "-ExpectedBundleCommit",
                           plan["expected_commit"], "-ExpectedManifestSha256", plan["expected_manifest_sha256"])
    assert "synthetic-secret" not in json.dumps(value)


@pytest.mark.parametrize("field", ["expected_commit", "expected_manifest_sha256", "expected_wrapper_sha256"])
def test_mismatched_or_missing_caller_pins_fail_before_query(plan, field):
    plan[field] = "0" * len(plan[field])
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)
    plan[field] = ""
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)


@pytest.mark.parametrize("name", ["deployment-manifest.json", "Invoke-WdQ4Query.ps1"])
def test_changed_file_fails_before_query(plan, name):
    (plan["bundle_root"] / name).write_bytes(b"tampered")
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)


def test_unknown_lane_and_oversized_input_refuse(plan):
    with pytest.raises(link.LaneLinkRefused, match="^invalid_lane$"):
        link.invoke(enabled=True, lane="unlisted", query_bytes=NoRead(), transport=lambda **_: None, **plan)
    with pytest.raises(link.LaneLinkRefused, match="^invalid_query$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=b"x" * 65537, transport=lambda **_: None, **plan)


def test_stream_input_is_refused_without_reading(plan):
    def must_not_run(**_):
        pytest.fail("stream reached transport")

    with pytest.raises(link.LaneLinkRefused, match="^invalid_query$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=must_not_run, **plan)
    with pytest.raises(link.LaneLinkRefused, match="^invalid_query$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=io.BytesIO(b"partial"), transport=must_not_run, **plan)


def test_duplicate_manifest_keys_are_not_accepted_even_if_the_bytes_are_pinned(plan):
    manifest = plan["bundle_root"] / "deployment-manifest.json"
    raw = manifest.read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    manifest.write_bytes(raw)
    plan["expected_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(link.LaneLinkRefused, match="^binding_refused$"):
        link.invoke(enabled=True, lane=LANES[0], query_bytes=NoRead(), transport=lambda **_: None, **plan)


def test_uppercase_manifest_and_external_hash_pins_are_supported(plan):
    path = plan["bundle_root"] / "deployment-manifest.json"
    manifest = json.loads(path.read_bytes())
    manifest["files"]["Invoke-WdQ4Query.ps1"] = plan["expected_wrapper_sha256"].upper()
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    plan["expected_manifest_sha256"] = hashlib.sha256(raw).hexdigest().upper()
    plan["expected_wrapper_sha256"] = plan["expected_wrapper_sha256"].upper()
    result = link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic",
                         transport=lambda **_: (_reply(), 0), **plan)
    assert result["status"] == "ABSTAIN"


@pytest.mark.parametrize("reply", [b"not json", b'{"schema":"wd.q4-agent-wrapper.v1","status":"ABSTAIN","status":"ANSWER"}', b"x" * 65537],
                         ids=["invalid-json", "duplicate-keys", "oversized"])
def test_malformed_or_oversized_reply_refuses_without_echo(plan, reply):
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$") as exc:
        link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic-secret",
                    transport=lambda **_: (reply, 0), **plan)
    assert "synthetic-secret" not in repr(exc.value) and "not json" not in repr(exc.value)


def test_transport_error_is_sanitized(plan):
    def transport(**_):
        raise RuntimeError("synthetic-private-path")
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$") as exc:
        link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic-secret", transport=transport, **plan)
    assert "synthetic-private-path" not in repr(exc.value)


def test_resolved_reply_preserves_only_the_expected_wrapper_shape(plan):
    value = {"schema": "wd.q4-agent-wrapper.v2", "status": "ANSWER",
             "scope": "wd.q4-shared-projected-events.v1", "content_mode": "sanitized_projected_metadata",
             "memory_sha256": "1" * 64, "projection_sha256": "2" * 64,
             "policy_sha256": "3" * 64, "roster_sha256": "4" * 64,
             "results": [{"ref_sha256": "5" * 64, "doc_id_sha256": "6" * 64,
                          "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                          "stored_text": "message done codex-lead-1 synthetic-task"}]}
    reply = json.dumps(value).encode()
    result = link.invoke(enabled=True, lane=LANES[0], query_bytes=b"synthetic-query",
                         transport=lambda **_: (reply, 0), **plan)
    assert result == value


@pytest.fixture
def public_plan(plan, tmp_path):
    document = {"schema": "wd.q4-public-wrapper-plan.v1", "config": {"descriptor_sha256": "a" * 64},
                "root": "synthetic-root", "policy_path": "synthetic-policy", "roster_path": "synthetic-roster",
                "public_policy_path": "synthetic-public-policy", "pointer_sha256": "1" * 64,
                "source_anchor_sha256": "2" * 64, "encoder_sha256": "3" * 64,
                "policy_sha256": "4" * 64, "roster_sha256": "5" * 64, "public_policy_sha256": "6" * 64}
    data = json.dumps(document, sort_keys=True).encode()
    path = tmp_path / "public-plan.json"
    path.write_bytes(data)
    return {**plan, "public_plan_path": path, "expected_public_plan_sha256": hashlib.sha256(data).hexdigest()}, document


def public_reply(document):
    binding = {k: document[k] for k in ("pointer_sha256", "source_anchor_sha256", "encoder_sha256",
                                         "policy_sha256", "roster_sha256", "public_policy_sha256")}
    binding.update({"descriptor_sha256": document["config"]["descriptor_sha256"], "memory_sha256": "7" * 64,
                    "projection_sha256": "8" * 64, "receipt_sha256": "9" * 64})
    return {"schema": "wd.q4-public-query.v2", "status": "ANSWER", "binding": binding,
            "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN",
            "same_user_aba": "UNKNOWN", "results": [{"stored_text": "synthetic metadata",
            "ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64,
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}]}


def test_public_default_off_never_invokes_transport(public_plan):
    plan, _ = public_plan
    with pytest.raises(link.LaneLinkRefused, match="^disabled$"):
        link.invoke_public(lane=LANES[0], query_bytes=NoRead(), **plan)


@pytest.mark.parametrize("lane", LANES)
def test_public_five_lanes_use_explicit_plan_and_stdin_only(public_plan, lane):
    plan, document = public_plan
    calls = []
    reply = public_reply(document)
    def transport(**kwargs):
        calls.append(kwargs)
        return json.dumps(reply).encode(), 0
    result = link.invoke_public(enabled=True, lane=lane, query_bytes=b"synthetic-secret", transport=transport, **plan)
    assert result == reply
    assert calls[0]["stdin_bytes"] == b"synthetic-secret"
    assert "synthetic-secret" not in repr(calls[0]["arguments"])
    assert calls[0]["arguments"] == ("-BundleRoot", str(plan["bundle_root"]), "-ExpectedBundleCommit", plan["expected_commit"],
        "-ExpectedManifestSha256", plan["expected_manifest_sha256"], "-Public", "-PublicPlanPath", str(plan["public_plan_path"]),
        "-ExpectedPublicPlanSha256", plan["expected_public_plan_sha256"])


@pytest.mark.parametrize("name", ["pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
                                   "roster_sha256", "public_policy_sha256", "descriptor_sha256"])
def test_public_mismatched_result_binding_refuses(public_plan, name):
    plan, document = public_plan
    reply = public_reply(document)
    reply["binding"][name] = "f" * 64
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$"):
        link.invoke_public(enabled=True, lane=LANES[0], query_bytes=b"synthetic", **plan,
                           transport=lambda **_: (json.dumps(reply).encode(), 0))


@pytest.mark.parametrize("kind", ["changed_plan", "wrong_plan_pin", "wrong_schema", "unknown_changed", "oversized_text"])
def test_public_plan_or_result_validation_refuses(public_plan, kind):
    plan, document = public_plan
    reply = public_reply(document)
    if kind == "changed_plan":
        plan["public_plan_path"].write_bytes(b"tampered")
    elif kind == "wrong_plan_pin":
        plan["expected_public_plan_sha256"] = "0" * 64
    elif kind == "wrong_schema":
        reply["schema"] = "wd.q4-agent-wrapper.v2"
    elif kind == "unknown_changed":
        reply["freshness"] = "CURRENT"
    else:
        reply["results"][0]["stored_text"] = "ä" * 161
    with pytest.raises(link.LaneLinkRefused, match="^(binding_refused|transport_refused)$"):
        link.invoke_public(enabled=True, lane=LANES[0], query_bytes=b"synthetic", **plan,
                           transport=lambda **_: (json.dumps(reply).encode(), 0))


def test_public_runtime_refusal_is_fixed_and_does_not_echo(public_plan):
    plan, _ = public_plan
    raw = b'{"schema":"wd.q4-public-query.v2","status":"REFUSED","reason":"synthetic-private"}'
    result = link.invoke_public(enabled=True, lane=LANES[0], query_bytes=b"synthetic", **plan,
                                transport=lambda **_: (raw, 2))
    assert result == {"schema": "wd.q4-public-query.v2", "status": "REFUSED", "reason": "runtime_refused"}


def test_public_changed_plan_during_transport_refuses(public_plan):
    plan, document = public_plan
    def transport(**kwargs):
        plan["public_plan_path"].write_bytes(b"changed")
        return json.dumps(public_reply(document)).encode(), 0
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$"):
        link.invoke_public(enabled=True, lane=LANES[0], query_bytes=b"synthetic", transport=transport, **plan)


@pytest.mark.parametrize("raw,code", [(b'{"schema":"wd.q4-public-query.v2","status":"ABSTAIN","status":"ANSWER"}', 0),
                                      (b"x" * 32769, 0),
                                      (b'{"schema":"wd.q4-public-query.v2","status":"REFUSED","reason":"x"}', 0)],
                         ids=["duplicate-status", "oversized", "refusal-wrong-exit"])
def test_public_malformed_reply_refuses(public_plan, raw, code):
    plan, _ = public_plan
    with pytest.raises(link.LaneLinkRefused, match="^transport_refused$"):
        link.invoke_public(enabled=True, lane=LANES[0], query_bytes=b"synthetic", **plan,
                           transport=lambda **_: (raw, code))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell and job wrapper required")
def test_public_lane_wrapper_job_chain(tmp_path):
    """Actual wrapper and job, synthetic bundle/outer/child; no runtime model."""
    project = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("synthetic_wrapper_fixture", project / "tests/tools/test_wd_q4_agent_wrapper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = module.bundle.__wrapped__(tmp_path)
    bundle["put"]("tools-bootstrap/tools/wd_q4_agent_query.py",
                  b"class VerifiedQ4Config:\n def __init__(self,**kwargs): self.kwargs=kwargs\n")
    package = json.loads((bundle["root"] / "bridge-code-files.json").read_bytes())
    for relative in ("tools/wd_q4_public_outer.py", "tools/wd_q4_public_child.py", "tools/wd_q4_public_query.py"):
        package["python_files"].append(relative)
        bundle["put"]("tools-bootstrap/" + relative, b"# synthetic\n")
    bundle["put"]("bridge-code-files.json", json.dumps(package).encode())
    cfg = bundle["config"]
    root = bundle["root"] / "tools-bootstrap"
    config = dict(interpreter=sys.executable, interpreter_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                  bootstrap=str(root / "sandbox/mind/step2/runtime_bootstrap.py"),
                  bootstrap_sha256=bundle["files"]["tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py"],
                  verifier_sha256=bundle["files"]["tools-bootstrap/sandbox/mind/step2/runtime_manifest.py"],
                  runtime_root=cfg["runtime_root"], runtime_manifest=cfg["runtime_manifest_path"],
                  runtime_manifest_sha256=cfg["runtime_manifest_sha256"], runtime_generation=cfg["runtime_generation"],
                  runtime_revision=cfg["runtime_revision"], memory=cfg["memory_root"],
                  descriptor=cfg["descriptor_path"], descriptor_sha256=cfg["descriptor_sha256"],
                  lock_file=cfg["lock_file"], lock_timeout="0.25", timeout=30, scratch_root=cfg["scratch_root"])
    document = dict(config=config, schema="wd.q4-public-wrapper-plan.v1", root=cfg["memory_root"],
                    pointer_sha256="1" * 64, source_anchor_sha256="2" * 64, encoder_sha256="3" * 64)
    for name in ("policy", "roster", "public_policy"):
        path = tmp_path / (name + ".json")
        data = (name + "-synthetic").encode()
        path.write_bytes(data)
        document[name + "_path"] = str(path)
        document[name + "_sha256"] = hashlib.sha256(data).hexdigest()
    reply = public_reply(document)
    outer = ("import json,types\n"
             "def bootstrap_public_imports(**kwargs):\n"
             " assert kwargs['enabled'] is True\n"
             " def run_public(config,stream,stdout,**pins):\n"
             "  assert stream.read()==b'synthetic-query'\n"
             "  assert pins['policy_bytes']==b'policy-synthetic'\n"
             "  assert pins['roster_bytes']==b'roster-synthetic'\n"
             "  assert pins['public_policy_bytes']==b'public_policy-synthetic'\n"
             "  stdout.write(json.dumps(" + repr(reply) + "));stdout.flush();return 0\n"
             " return {'status':'MODULES_IMPORTED','child':types.SimpleNamespace(run_public=run_public)}\n")
    bundle["put"]("tools-bootstrap/tools/wd_q4_public_outer.py", outer.encode())
    bundle["seal"]()
    manifest_path = bundle["root"] / "deployment-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["bridge_code_package"] = {"python_executable": sys.executable,
                                        "python_executable_sha256": config["interpreter_sha256"]}
    manifest_bytes = json.dumps(manifest).encode()
    manifest_path.write_bytes(manifest_bytes)
    plan_bytes = json.dumps(document).encode()
    plan_path = tmp_path / "public-plan.json"
    plan_path.write_bytes(plan_bytes)
    def transport(*, wrapper, arguments, stdin_bytes):
        run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-File", wrapper, *arguments],
                             input=stdin_bytes, capture_output=True, timeout=45)
        assert b"synthetic-query" not in run.stdout + run.stderr
        return run.stdout, run.returncode
    value = link.invoke_public(enabled=True, lane=LANES[0], bundle_root=bundle["root"], expected_commit=module.COMMIT,
        expected_manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        expected_wrapper_sha256=bundle["files"]["Invoke-WdQ4Query.ps1"], public_plan_path=plan_path,
        expected_public_plan_sha256=hashlib.sha256(plan_bytes).hexdigest(), query_bytes=b"synthetic-query",
        transport=transport)
    assert value == reply
