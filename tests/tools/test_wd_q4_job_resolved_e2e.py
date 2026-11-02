"""Synthetic wrapper/job/real-resolved-child integration; no live model or data.

The whole agent-query module, including config and ranking, is a stub. It
constructs one synthetic hit and invokes the real resolver, while the real
child still validates its inputs. Q4, FAISS, E5 and the runtime launcher are
not exercised. Sibling modules come from the worktree, not an installed bundle.
"""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("q4_wrapper_test_fixture", ROOT / "tests/tools/test_wd_q4_agent_wrapper.py")
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)
bundle = fixture_module.bundle
HOSTS = fixture_module.HOSTS


def assert_preflight_rejected_before_access(observation):
    assert observation["result"]["status"] == "REFUSED"
    assert observation["result"]["reason"] == "binding_refused"
    assert observation["query_position"] == 0
    assert observation["helper_executed"] is False


@pytest.mark.parametrize("observation", [
    {"result": {"status": "REFUSED", "reason": "runtime_refused"}, "query_position": 0, "helper_executed": False},
    {"result": {"status": "REFUSED", "reason": "binding_refused"}, "query_position": 1, "helper_executed": False},
    {"result": {"status": "REFUSED", "reason": "binding_refused"}, "query_position": 0, "helper_executed": True},
])
def test_preflight_assertion_rejects_refusal_only_false_greens(observation):
    # Each observation would satisfy the old status-only assertion.
    assert observation["result"]["status"] == "REFUSED"
    with pytest.raises(AssertionError):
        assert_preflight_rejected_before_access(observation)


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("tamper_helper", [False, True], ids=["verified", "tampered-helper"])
def test_manifest_pinned_job_resolves_synthetic_answer(bundle, host, tamper_helper):
    if not host:
        pytest.skip("Windows PowerShell host unavailable")
    fm = fixture_module
    _, _, projection = fm.add_resolved_fixture(bundle)
    record = projection["records"][0]
    ref = record["provenance"]["event_ref"]
    ref_sha = hashlib.sha256(ref.encode()).hexdigest()
    doc_sha = hashlib.sha256(record["record_id"].encode()).hexdigest()

    # Entire agent-query API is synthetic. The actual child and resolver
    # independently validate the pinned projection/policy; Q4 is not called.
    stub = '''import json
from sandbox.mind.step2 import shared_result_resolver as resolver
class VerifiedQ4Config:
 def __init__(self, **kwargs): pass
def query_with_verified_config(*args, **kwargs):
 raise AssertionError("unresolved path must not run")
def query_and_resolve(config, stdin, memory, projection, tasks, **pins):
 query=json.loads(stdin.read())
 assert query["situation"] == "query-sensitive"
 row=json.loads(projection)["records"][0]
 import hashlib
 sha=lambda s: hashlib.sha256(s.encode()).hexdigest()
 hit={"ref_sha256":sha(row["provenance"]["event_ref"]),"doc_id_sha256":sha(row["record_id"]),"ref_kind":"sha256_of_projected_metadata","canonical_event":False}
 resolved=resolver.resolve_results(memory_manifest=memory,projection_bytes=projection,results=[hit],query_utc=query["time"],approved_tasks=tasks,**pins)
 return {"status":"ANSWER","results":resolved}
'''
    # Sibling imports deliberately use the worktree; no package closure claim.
    files = {
        "tools/__init__.py": ("__path__.append(" + repr(str(ROOT / "tools")) + ")\n").encode(),
        "tools/wd_q4_agent_query.py": stub.encode(),
        "tools/wd_q4_resolved_child.py": (ROOT / "tools/wd_q4_resolved_child.py").read_bytes(),
        "sandbox/__init__.py": ("__path__.append(" + repr(str(ROOT / "sandbox")) + ")\n").encode(),
    }
    definition = json.loads((bundle["root"] / "bridge-code-files.json").read_bytes())
    for relative, data in files.items():
        bundle["put"]("tools-bootstrap/" + relative, data)
        if relative not in definition["python_files"]:
            definition["python_files"].append(relative)
    bundle["put"]("bridge-code-files.json", json.dumps(definition).encode())
    bundle["config"]["code_manifest_sha256"] = bundle["files"]["bridge-code-files.json"]
    bundle["config"]["python_path"] = sys.executable
    bundle["config"]["python_sha256"] = fm.digest(Path(sys.executable).read_bytes())
    fleet = json.loads((bundle["root"] / "wd-fleet.json").read_bytes())
    fleet["q4_read"] = bundle["config"]
    bundle["put"]("wd-fleet.json", json.dumps(fleet).encode())
    # The shared fixture's seal captures its original synthetic interpreter;
    # create this candidate's manifest with the real test Python pin instead.
    manifest = {"schema_version": 1, "source_commit": fm.COMMIT, "files": bundle["files"],
                "bridge_code_package": {"python_executable": sys.executable,
                                        "python_executable_sha256": bundle["config"]["python_sha256"]}}
    raw = json.dumps(manifest).encode()
    (bundle["root"] / "deployment-manifest.json").write_bytes(raw)
    bundle["pin"] = fm.digest(raw)
    helper_marker = bundle["root"].parent / "synthetic-helper-executed.txt"
    if tamper_helper:
        # If the unpinned helper is evaluated, mark the synthetic audit root.
        # The byte change deliberately does not update the pinned manifest.
        (bundle["root"] / "WdQ4ProcessJob.ps1").write_text(
            "[IO.File]::WriteAllText(" + fm.quote(helper_marker) + ",'executed')\n", encoding="utf-8")
    query = base64.b64encode(json.dumps({"situation": "query-sensitive", "time": "2026-10-31T12:02:00Z"}).encode()).decode()
    script = ("$raw=[Convert]::FromBase64String(" + fm.quote(query) + ")\n"
              "$s=[IO.MemoryStream]::new([byte[]]$raw)\n"
              "$r=Invoke-WdQ4Query -BundleRoot " + fm.quote(bundle["root"]) +
              " -ExpectedBundleCommit " + fm.quote(fm.COMMIT) +
              " -ExpectedManifestSha256 " + fm.quote(bundle["pin"]) +
              " -InputStream $s -TimeoutSeconds 15\n"
              "@{result=$r;query_position=$s.Position;helper_executed=(Test-Path -LiteralPath " +
              fm.quote(helper_marker) + ")}|ConvertTo-Json -Depth 8 -Compress")
    observation = fm.run_ps(host, script)
    if tamper_helper:
        assert_preflight_rejected_before_access(observation)
        assert not helper_marker.exists()
        return
    result = observation["result"]
    assert result["status"] == "ANSWER", result
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["stored_text"] == record["stored_text"]
    assert hit["ref_sha256"] == ref_sha and hit["doc_id_sha256"] == doc_sha
    assert hit["canonical_event"] is False and hit["ref_kind"] == "sha256_of_projected_metadata"
    assert "raw-private-payload-sentinel" not in json.dumps(result)
