"""Synthetic in-memory transport checks; no live publisher, model, or native execution."""

import hashlib
import io
import json
from unittest.mock import patch

import pytest

from tools import wd_q4_public_child as child
from tools.wd_q4_agent_query import VerifiedQ4Config


def digest(value):
    return hashlib.sha256(value).hexdigest()


def valid_config():
    return VerifiedQ4Config(
        interpreter=r"C:\synthetic\python.exe", interpreter_sha256="8" * 64,
        bootstrap=r"C:\synthetic\bootstrap.py", bootstrap_sha256="9" * 64, verifier_sha256="a" * 64,
        runtime_root=r"C:\synthetic\runtime", runtime_manifest=r"C:\synthetic\runtime.json",
        runtime_manifest_sha256="b" * 64, runtime_generation="c" * 40, runtime_revision="d" * 40,
        memory=r"C:\synthetic\index\gen-1", descriptor=r"C:\synthetic\index\gen-1\descriptor.json", descriptor_sha256="4" * 64,
        lock_file=r"C:\synthetic\lock", lock_timeout="1", timeout=30.0, scratch_root=r"C:\synthetic\scratch",
    )


def test_mismatched_descriptor_is_not_released(inputs):
    result = valid_response(inputs)
    result["binding"]["descriptor_sha256"] = "f" * 64
    out = io.StringIO()
    with patch("tools.wd_q4_public_query.query_public", return_value=result):
        code = child.run_public(valid_config(), io.BytesIO(b"query"), out, enabled=True, **inputs)
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"


def test_unvalidated_config_is_refused_before_stdin_or_backend(inputs):
    reads = []
    class NoRead:
        def read(self, *args):
            reads.append(True)
            raise AssertionError("must not read")
    out = io.StringIO()
    with patch("tools.wd_q4_public_query.query_public", return_value=valid_response(inputs)):
        code = child.run_public(object(), NoRead(), out, enabled=True, **inputs)
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED" and not reads


@pytest.fixture
def inputs():
    lanes = ["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5"]
    roster_value = {"schema": "wd.q4-roster-snapshot.v1",
        "lanes": [{"agent": name, "agent_uuid": f"{i:08x}-1111-4111-8111-111111111111"}
                  for i, name in enumerate(lanes, 1)],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": "00000005-1111-4111-8111-111111111111"}}
    policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["public-task"]}).encode()
    roster = json.dumps(roster_value).encode()
    public_policy = json.dumps({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                                "public_types": ["message"]}).encode()
    return {
        "root": r"C:\synthetic\index",
        "expected_pointer_sha256": "1" * 64,
        "expected_source_sha256": "2" * 64,
        "expected_encoder_sha256": "3" * 64,
        "policy_bytes": policy, "policy_sha256": digest(policy),
        "roster_bytes": roster, "roster_sha256": digest(roster),
        "public_policy_bytes": public_policy, "public_policy_sha256": digest(public_policy),
    }


def valid_response(inputs):
    return {
        "schema": "wd.q4-public-query.v2", "status": "ANSWER",
        "results": [{"stored_text": "synthetic", "ref_sha256": "a" * 64,
                     "doc_id_sha256": "b" * 64, "ref_kind": "sha256_of_projected_metadata",
                     "canonical_event": False}],
        "binding": {"pointer_sha256": inputs["expected_pointer_sha256"],
                    "source_anchor_sha256": inputs["expected_source_sha256"],
                    "encoder_sha256": inputs["expected_encoder_sha256"],
                    "policy_sha256": inputs["policy_sha256"], "roster_sha256": inputs["roster_sha256"],
                    "public_policy_sha256": inputs["public_policy_sha256"],
                    "descriptor_sha256": "4" * 64, "memory_sha256": "5" * 64,
                    "projection_sha256": "6" * 64, "receipt_sha256": "7" * 64},
        "publisher_authentication": "UNKNOWN", "source_filter_attestation": "UNKNOWN",
        "freshness": "UNKNOWN", "same_user_aba": "UNKNOWN",
    }


def test_supplied_query_function_cannot_forge_public_answer(inputs):
    """F1: A caller-supplied callback cannot stand in for the public resolver."""
    out = io.StringIO()
    called = []

    def fake_query(*args, **kwargs):
        called.append(True)
        return valid_response(inputs)

    try:
        code = child.run_public(valid_config(), io.BytesIO(b"synthetic-query"), out,
                                enabled=True, query_fn=fake_query, **inputs)
    except TypeError:
        return  # Removing the callback argument also closes the injection seam.
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"
    assert not called


def test_exact_plan_is_required_before_query_callback(inputs, monkeypatch):
    """F2: No callback may run for an unvalidated root or policy tuple."""
    from tools import wd_q4_public_query

    called = []

    def fake_query(*args, **kwargs):
        called.append(True)
        return valid_response(inputs)

    monkeypatch.setattr(wd_q4_public_query, "query_public", fake_query)
    out = io.StringIO()
    # Relative roots do not form a structurally valid, direct generation plan.
    invalid = {**inputs, "root": "synthetic-root"}
    code = child.run_public(valid_config(), io.BytesIO(b"synthetic-query"), out, enabled=True, **invalid)
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"
    assert not called


def run(inputs, query_fn, query=b"synthetic-query", *, enabled=True, stdout=None):
    out = io.StringIO() if stdout is None else stdout
    with patch("tools.wd_q4_public_query.query_public", new=query_fn):
        code = child.run_public(valid_config(), io.BytesIO(query), out, enabled=enabled, **inputs)
    return code, out


def test_default_off_does_not_touch_stdin_or_call_query(inputs):
    class NoRead:
        def read(self, *args):
            raise AssertionError("must not read")
    out = io.StringIO()
    assert child.run_public(object(), NoRead(), out, **inputs) == 2
    assert json.loads(out.getvalue())["status"] == "REFUSED"


def test_success_forwards_bounded_stdin_and_validated_binding(inputs):
    seen = []
    def query(config, stream, **kwargs):
        seen.append((stream.read(), kwargs))
        return valid_response(inputs)
    code, out = run(inputs, query)
    assert code == 0 and json.loads(out.getvalue()) == valid_response(inputs)
    assert seen[0][0] == b"synthetic-query" and seen[0][1]["enabled"] is True
    assert all(seen[0][1][key] == value for key, value in inputs.items())


def test_chunked_input_is_collected_to_eof(inputs):
    class Chunked:
        def __init__(self):
            self.parts = iter([b"first", b"-second", b""])
        def read(self, size):
            assert size > 0
            return next(self.parts)
    seen, out = [], io.StringIO()
    def query(config, stream, **kwargs):
        seen.append(stream.read())
        return valid_response(inputs)
    with patch("tools.wd_q4_public_query.query_public", new=query):
        assert child.run_public(valid_config(), Chunked(), out, enabled=True, **inputs) == 0
    assert seen == [b"first-second"]


@pytest.mark.parametrize("enabled", [None, 0, 1, "true"])
def test_only_explicit_boolean_true_enables(inputs, enabled):
    code, out = run(inputs, lambda *a, **k: pytest.fail("called"), enabled=enabled)
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"


@pytest.mark.parametrize("name", ["expected_pointer_sha256", "expected_source_sha256", "expected_encoder_sha256",
                                  "policy_sha256", "roster_sha256", "public_policy_sha256"])
def test_missing_pin_refuses_before_read_and_query(inputs, name):
    inputs[name] = None
    class NoRead:
        def read(self, *args):
            raise AssertionError("must not read")
    out = io.StringIO()
    with patch("tools.wd_q4_public_query.query_public", side_effect=AssertionError("called")):
        assert child.run_public(valid_config(), NoRead(), out, enabled=True, **inputs) == 2
    assert json.loads(out.getvalue())["status"] == "REFUSED"


@pytest.mark.parametrize("name", ["policy_bytes", "roster_bytes", "public_policy_bytes"])
def test_mismatched_policy_bytes_refuse_before_query(inputs, name):
    inputs[name] = b"other"
    code, out = run(inputs, lambda *a, **k: pytest.fail("called"))
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"


@pytest.mark.parametrize("query", [b"", b"x" * 65537], ids=["empty", "oversized"])
def test_empty_or_oversized_query_refuses_without_backend(inputs, query):
    code, out = run(inputs, lambda *a, **k: pytest.fail("called"), query)
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"


@pytest.mark.parametrize("change", ["binding", "auth", "extra", "text", "canonical", "status", "oversized"])
def test_invalid_backend_response_is_fixed_refusal_without_private_output(inputs, change):
    result = valid_response(inputs)
    if change == "binding":
        result["binding"]["pointer_sha256"] = "f" * 64
    elif change == "auth":
        result["publisher_authentication"] = "VERIFIED"
    elif change == "extra":
        result["secret"] = "private-sentinel"
    elif change == "text":
        result["results"][0]["stored_text"] = "x" * 321
    elif change == "canonical":
        result["results"][0]["canonical_event"] = True
    elif change == "status":
        result["status"] = "REFUSED"
    else:
        result["results"] *= 101
    code, out = run(inputs, lambda *a, **k: result)
    assert code == 2 and json.loads(out.getvalue())["status"] == "REFUSED"
    assert "private-sentinel" not in out.getvalue()


def test_exception_is_not_echoed(inputs):
    def fail(*args, **kwargs):
        raise RuntimeError("private-sentinel")
    code, out = run(inputs, fail)
    assert code == 2 and "private-sentinel" not in out.getvalue()


@pytest.mark.parametrize("has_results", [False, True])
def test_degraded_status_is_preserved_not_promoted(inputs, has_results):
    result = valid_response(inputs)
    result["status"] = "DEGRADED"
    if not has_results:
        result["results"] = []
    code, out = run(inputs, lambda *a, **k: result)
    assert code == 0 and json.loads(out.getvalue())["status"] == "DEGRADED"


def test_short_write_is_failure_without_retry(inputs):
    class Short:
        calls = 0
        def write(self, value):
            self.calls += 1
            return len(value) - 1
    out = Short()
    code, _ = run(inputs, lambda *a, **k: valid_response(inputs), stdout=out)
    assert code == 2 and out.calls == 1


def test_flush_failure_is_not_reported_as_success(inputs):
    class BadFlush(io.StringIO):
        def flush(self):
            raise OSError("private-sentinel")
    out = BadFlush()
    code, _ = run(inputs, lambda *a, **k: valid_response(inputs), stdout=out)
    assert code == 2 and "private-sentinel" not in out.getvalue()
