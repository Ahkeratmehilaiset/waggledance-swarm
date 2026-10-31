"""Synthetic only: no real files, memory, model, network or child process."""
import datetime as dt
import hashlib
import io
import json

import pytest

from sandbox.mind.step2 import shared_projection as sp
from tools import wd_q4_agent_query as q4
from tools import wd_q4_resolved_child as child


def sha(value):
    return hashlib.sha256(value).hexdigest()


def config():
    return q4.VerifiedQ4Config(interpreter=r"C:\fake\python.exe", interpreter_sha256="1" * 64,
        bootstrap=r"C:\fake\runtime_bootstrap.py", bootstrap_sha256="2" * 64, verifier_sha256="3" * 64,
        runtime_root=r"C:\fake\runtime", runtime_manifest=r"C:\fake\runtime.json", runtime_manifest_sha256="4" * 64,
        runtime_generation="5" * 40, runtime_revision="6" * 40, memory=r"C:\fake\memory",
        descriptor=r"C:\fake\descriptor.json", descriptor_sha256="7" * 64, lock_file=r"C:\fake\lock",
        lock_timeout="2", timeout=30, scratch_root=r"C:\fake-scratch")


def fixture():
    policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["synthetic-task"]}).encode()
    roster = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
              "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
              "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
    fleet = json.dumps({"lanes": [{"agent": name, "agent_uuid": uid} for name, uid in roster.items()
                                    if name != "codex-tools-1"],
                        "tools_supervisor": {"agent": "codex-tools-1",
                                             "agent_uuid": roster["codex-tools-1"]}}).encode()
    event = {"ts_utc": "2026-10-31T12:00:10Z", "agent": "codex-lead-1", "type": "message", "status": "done",
             "task_id": "synthetic-task", "agent_uuid": roster["codex-lead-1"], "message": "private-body-sentinel"}
    projection = sp.project_events([event], approved_tasks=frozenset({"synthetic-task"}),
                                   roster=roster, exclude_review_signal=True)
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode()
    memory = json.dumps({"scope": "wd.q4-shared-projected-events.v1", "projection_schema": sp.SCHEMA,
                         "projection_sha256": sha(projection_bytes), "unused": "private-memory-sentinel",
                         "policy": {"authorship": "declared_roster_consistent", "review_signal_excluded": True,
                                    "policy_sha256": sha(policy), "roster_sha256": sha(fleet)}}).encode()
    record = projection["records"][0]
    result = {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1", "descriptor_sha256": "7" * 64,
              "memory_sha256": sha(memory), "results": [{"ref_kind": "sha256_of_projected_metadata",
              "canonical_event": False, "ref_sha256": sha(record["provenance"]["event_ref"].encode()),
              "doc_id_sha256": sha(record["record_id"].encode())}]}
    data = {"policy_bytes": policy, "policy_sha256": sha(policy), "memory_bytes": memory,
            "memory_sha256": sha(memory), "projection_bytes": projection_bytes,
            "projection_sha256": sha(projection_bytes), "roster_bytes": fleet, "roster_sha256": sha(fleet)}
    return data, result, record


def run(data, query=b'{"situation":"private-query-sentinel","time":"2026-10-31T12:02:00Z"}'):
    output = io.StringIO()
    code = child.run_resolved(config(), io.BytesIO(query), output, **data)
    return code, output.getvalue()


def test_synthetic_real_resolution_only_prints_approved_metadata(monkeypatch):
    data, result, record = fixture()
    monkeypatch.setattr(q4, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(q4, "_invoke_launcher", lambda **_: result)
    code, output = run(data)
    decoded = json.loads(output)
    assert code == 0 and decoded["status"] == "ANSWER"
    assert decoded["results"][0]["stored_text"] == record["stored_text"]
    assert decoded["results"][0]["canonical_event"] is False
    assert all(secret not in output for secret in ("private-body-sentinel", "private-query-sentinel", "private-memory-sentinel"))


def test_replaced_roster_is_rejected_before_launcher_even_with_matching_new_caller_pin(monkeypatch):
    data, _, _ = fixture()
    fleet = json.loads(data["roster_bytes"])
    fleet["lanes"][0]["agent_uuid"] = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    replacement = json.dumps(fleet).encode()
    data.update(roster_bytes=replacement, roster_sha256=sha(replacement))
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: pytest.fail("launcher reached"))
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"


def test_memory_policy_rejects_integer_one_before_launcher(monkeypatch):
    data, _, _ = fixture()
    memory = json.loads(data["memory_bytes"])
    memory["policy"]["review_signal_excluded"] = 1
    encoded = json.dumps(memory).encode()
    data.update(memory_bytes=encoded, memory_sha256=sha(encoded))
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: pytest.fail("launcher reached"))
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"


@pytest.mark.parametrize("pin", ["policy_sha256", "memory_sha256", "projection_sha256", "roster_sha256"])
def test_wrong_pin_refuses_before_query(monkeypatch, pin):
    data, _, _ = fixture()
    data[pin] = "0" * 64
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: pytest.fail("query called"))
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"
    assert "private-query-sentinel" not in output


@pytest.mark.parametrize("policy", [
    b'{"schema":"wd.q4-non-review-policy.v1","approved_tasks":[],"extra":0}',
    b'{"schema":"wd.q4-non-review-policy.v1","approved_tasks":[],"approved_tasks":[]}',
    b'{"schema":"wd.q4-non-review-policy.v1","approved_tasks":["x","x"]}',
    b'{"schema":"other","approved_tasks":[]}',
    b'{"schema":"wd.q4-non-review-policy.v1","approved_tasks":"x"}',
])
def test_bad_policy_is_refused_even_when_its_pin_matches(monkeypatch, policy):
    data, _, _ = fixture()
    data.update(policy_bytes=policy, policy_sha256=sha(policy))
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: pytest.fail("query called"))
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"


@pytest.mark.parametrize("value", [None, [], {"status": "ANSWER", "results": "private-sentinel"},
                                  {"status": "ANSWER", "results": [{"stored_text": "x" * 100000}]}])
def test_unexpected_or_oversized_result_never_echoes_content(monkeypatch, value):
    data, _, _ = fixture()
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: value)
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"
    assert "private-sentinel" not in output and len(output.encode()) <= child.MAX_OUTPUT


def test_valid_shape_exceeding_total_output_budget_is_refused(monkeypatch):
    data, _, _ = fixture()
    item = {"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64, "stored_text": "x" * 320,
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: {"status": "ANSWER", "results": [item] * 100})
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"
    assert len(output.encode()) <= child.MAX_OUTPUT


def test_policy_size_cap_refuses_before_query(monkeypatch):
    data, _, _ = fixture()
    data["policy_bytes"] = b"x" * (child.MAX_POLICY + 1)
    data["policy_sha256"] = sha(data["policy_bytes"])
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: pytest.fail("query called"))
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"


@pytest.mark.parametrize("written", [1, 0, None, "error"])
@pytest.mark.parametrize("refused", [False, True])
def test_short_or_failed_output_never_returns_success(monkeypatch, written, refused):
    data, _, _ = fixture()
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: {"status": "ABSTAIN", "results": []})
    if refused:
        data["policy_sha256"] = "0" * 64
    class ShortWriter:
        def __init__(self):
            self.partial = ""
        def write(self, text):
            if written == "error":
                raise OSError("sensitive-error")
            if isinstance(written, int):
                self.partial += text[:written]
            return written
    writer = ShortWriter()
    assert child.run_resolved(config(), io.BytesIO(b"sensitive-query"), writer, **data) == 2
    assert "sensitive-query" not in writer.partial


@pytest.mark.parametrize("agent,task,policy_tasks", [
    ("codex-lead-1", "unknown-task", []),
    ("codex-lead-1", "review-task", ["synthetic-task"]),
    ("claude-rco-1", "synthetic-task", ["synthetic-task"]),
    ("claude-rco-2", "synthetic-task", ["synthetic-task"]),
])
def test_projection_requires_explicit_task_policy_and_non_rco_agent_before_query(monkeypatch, agent, task, policy_tasks):
    data, _, _ = fixture()
    policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": policy_tasks}).encode()
    event = {"ts_utc": "2026-10-31T12:00:10Z", "agent": agent, "type": "message", "status": "done",
             "task_id": task, "message": "private-body-sentinel"}
    # Unknown/review task IDs get UNKNOWN in the v5 projection, so shape
    # validation alone cannot distinguish them from explicitly approved work.
    projection = sp.project_events([event], approved_tasks=frozenset(policy_tasks))
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode()
    memory = json.dumps({"scope": "wd.q4-shared-projected-events.v1", "projection_schema": sp.SCHEMA,
                         "projection_sha256": sha(projection_bytes)}).encode()
    data.update(policy_bytes=policy, policy_sha256=sha(policy), memory_bytes=memory, memory_sha256=sha(memory),
                projection_bytes=projection_bytes, projection_sha256=sha(projection_bytes))
    seen = []
    monkeypatch.setattr(q4, "query_and_resolve", lambda *_, **__: seen.append(True) or {"status": "ABSTAIN", "results": []})
    code, output = run(data)
    assert code == 2 and json.loads(output)["status"] == "REFUSED"
    assert seen == [] and "private-body-sentinel" not in output
