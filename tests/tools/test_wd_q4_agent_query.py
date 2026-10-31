"""Synthetic checks: no real runtime, model, memory, or subprocess."""

import io
import json
import hashlib
from dataclasses import replace

from tools import wd_q4_agent_query as cli


class Unreadable:
    def read(self, *_):
        raise AssertionError("untrusted input must not be read")


def test_disabled_is_refused_before_reading_input():
    out = io.StringIO()
    assert cli.run([], Unreadable(), out, {}) == 2
    assert json.loads(out.getvalue())["reason"] == "disabled"


def test_environment_hashes_do_not_establish_anchor():
    out = io.StringIO()
    env = {"WD_Q4_READ_ENABLED": "1", "WD_Q4_RUNTIME_MANIFEST_SHA256": "a" * 64}
    assert cli.run([], Unreadable(), out, env) == 2
    assert json.loads(out.getvalue())["reason"] == "external_anchor_unavailable"


def test_arguments_are_not_a_query_channel():
    out = io.StringIO()
    assert cli.run(["private-sentinel"], Unreadable(), out, {}) == 2
    assert "private-sentinel" not in out.getvalue()
    assert json.loads(out.getvalue())["reason"] == "arguments_not_allowed"


def test_bounded_valid_json_is_kept_as_stdin_bytes_only():
    raw = b'{"situation":"synthetic-sentinel","time":"2026-10-31T12:00:00Z"}'
    assert cli.read_query(io.BytesIO(raw)) == raw


def test_duplicate_unknown_invalid_and_oversized_query_refused():
    samples = [b'{"situation":"one","situation":"two","time":"t"}',
               b'{"situation":"one","time":"t","extra":1}',
               b'{"situation":" ","time":"t"}', b'\xff', b'x' * (cli.MAX_QUERY + 1)]
    for sample in samples:
        try:
            cli.read_query(io.BytesIO(sample))
        except cli.QueryRefused:
            pass
        else:
            raise AssertionError("invalid input was accepted")


def test_no_launch_and_no_secret_when_anchor_is_missing(monkeypatch):
    monkeypatch.setattr(cli, "_invoke_launcher", lambda *_: (_ for _ in ()).throw(AssertionError("launched")))
    out = io.StringIO()
    assert cli.run([], io.BytesIO(b'{"situation":"secret","time":"t"}'), out,
                   {"WD_Q4_READ_ENABLED": "1"}) == 2
    assert "secret" not in out.getvalue()


def _config():
    return cli.VerifiedQ4Config(
        interpreter=r"C:\synthetic\python.exe", interpreter_sha256="1" * 64,
        bootstrap=r"C:\synthetic\runtime_bootstrap.py", bootstrap_sha256="2" * 64,
        verifier_sha256="3" * 64, runtime_root=r"C:\synthetic\runtime",
        runtime_manifest=r"C:\synthetic\runtime.json", runtime_manifest_sha256="4" * 64,
        runtime_generation="5" * 40, runtime_revision="6" * 40,
        memory=r"C:\synthetic\memory", descriptor=r"C:\synthetic\descriptor.json",
        descriptor_sha256="7" * 64, lock_file=r"C:\synthetic\q4.lock",
        lock_timeout="2", timeout=30,
    )


def _answer():
    return {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1",
            "descriptor_sha256": "7" * 64, "memory_sha256": "8" * 64,
            "results": [{"ref_sha256": "9" * 64, "doc_id_sha256": "a" * 64,
                         "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}]}


def test_typed_binding_invokes_launcher_with_query_on_stdin_only(monkeypatch, capsys):
    seen = {}
    def launcher(**kwargs):
        seen.update(kwargs)
        return _answer()
    monkeypatch.setattr(cli, "_invoke_launcher", launcher)
    raw = b'{"situation":"synthetic-query-sensitive","time":"2026-10-31T12:00:00Z"}'
    result = cli.query_with_verified_config(_config(), io.BytesIO(raw))
    assert seen.pop("query") == raw
    assert b"synthetic-query-sensitive" not in repr(seen).encode()
    assert seen["tuple_args"]["runtime-revision"] == "6" * 40
    assert result["results"][0]["ref_sha256"] == "9" * 64
    assert "synthetic-query-sensitive" not in repr(result)
    assert capsys.readouterr() == ("", "")


def test_invalid_binding_or_query_never_calls_launcher(monkeypatch):
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: (_ for _ in ()).throw(AssertionError("called")))
    for bad in [None, {}, replace(_config(), runtime_revision="bad"),
                replace(_config(), interpreter_sha256="bad"), replace(_config(), lock_timeout="31")]:
        try:
            cli.query_with_verified_config(bad, Unreadable())
        except cli.QueryRefused:
            pass
        else:
            raise AssertionError("bad binding accepted")
    try:
        cli.query_with_verified_config(_config(), io.BytesIO(b"not-json"))
    except cli.QueryRefused:
        pass
    else:
        raise AssertionError("bad query accepted")


def test_scope_and_descriptor_mismatch_fail_closed(monkeypatch):
    for mutation in ({"scope": "private-scope"}, {"descriptor_sha256": "0" * 64},
                     {"results": [{"ref_sha256": "9" * 64, "doc_id_sha256": "a" * 64,
                                   "ref_kind": None, "canonical_event": None}]}):
        answer = _answer()
        answer.update(mutation)
        monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: answer)
        try:
            cli.query_with_verified_config(_config(), io.BytesIO(b'{"situation":"s","time":"t"}'))
        except cli.QueryRefused:
            pass
        else:
            raise AssertionError("out-of-scope result accepted")


def test_launcher_errors_are_sanitized(monkeypatch):
    def fail(**_):
        raise RuntimeError("sensitive-query-or-path")
    monkeypatch.setattr(cli, "_invoke_launcher", fail)
    try:
        cli.query_with_verified_config(_config(), io.BytesIO(b'{"situation":"s","time":"t"}'))
    except cli.QueryRefused as exc:
        assert str(exc) == "launcher_refused"
        assert exc.__cause__ is None
    else:
        raise AssertionError("launcher error accepted")


def test_refusal_is_sanitized_and_abstain_keeps_scope(monkeypatch):
    raw = b'{"situation":"s","time":"t"}'
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: {"status": "REFUSED", "reason": "sensitive"})
    assert cli.query_with_verified_config(_config(), io.BytesIO(raw)) == {
        "status": "REFUSED", "reason": "runtime_refused", "results": []}
    answer = _answer()
    answer.update(status="ABSTAIN", results=[])
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: answer)
    assert cli.query_with_verified_config(_config(), io.BytesIO(raw))["status"] == "ABSTAIN"


def test_run_verified_prints_only_bounded_safe_projection(monkeypatch):
    answer = {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1",
              "descriptor_sha256": "7" * 64, "memory_sha256": "8" * 64,
              "results": [{"ref_sha256": "9" * 64, "doc_id_sha256": "a" * 64,
                           "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}]}
    seen = []
    def query(config, stdin):
        seen.append(stdin.read())
        return answer
    monkeypatch.setattr(cli, "query_with_verified_config", query)
    out = io.StringIO()
    assert cli.run_verified(_config(), io.BytesIO(b"synthetic-secret"), out) == 0
    assert seen == [b"synthetic-secret"]
    assert "synthetic-secret" not in out.getvalue()
    assert len(out.getvalue().encode("utf-8")) <= cli.MAX_OUTPUT
    assert json.loads(out.getvalue()) == answer


def test_run_verified_handles_refusal_without_echoing_exception(monkeypatch):
    def fail(*_):
        raise RuntimeError("private-sentinel")
    monkeypatch.setattr(cli, "query_with_verified_config", fail)
    out = io.StringIO()
    assert cli.run_verified(_config(), io.BytesIO(b""), out) == 2
    assert "private-sentinel" not in out.getvalue()
    assert json.loads(out.getvalue()) == {"schema": "wd.q4-agent-query.v1", "status": "REFUSED", "reason": "query_refused"}


def test_run_verified_refuses_oversized_and_malformed_results(monkeypatch):
    for value in [
        {"status": "ANSWER", "secret": "synthetic-sentinel"},
        {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1",
         "descriptor_sha256": "7" * 64, "memory_sha256": "8" * 64,
         "results": [{"ref_sha256": "9" * 64, "doc_id_sha256": "a" * 64}] * 1000},
    ]:
        monkeypatch.setattr(cli, "query_with_verified_config", lambda *_: value)
        out = io.StringIO()
        assert cli.run_verified(_config(), io.BytesIO(b""), out) == 2
        assert "synthetic-sentinel" not in out.getvalue()
        assert len(out.getvalue().encode("utf-8")) <= cli.MAX_OUTPUT


def test_run_verified_propagates_no_backend_refusal_text(monkeypatch):
    monkeypatch.setattr(cli, "query_with_verified_config", lambda *_: {
        "status": "REFUSED", "reason": "private-sentinel", "results": []})
    out = io.StringIO()
    assert cli.run_verified(_config(), io.BytesIO(b""), out) == 2
    assert "private-sentinel" not in out.getvalue()


def test_projection_preserves_verified_provenance_through_serialization():
    result = cli._project_result(_answer(), _config().descriptor_sha256)
    assert result["results"][0]["ref_kind"] == "sha256_of_projected_metadata"
    assert result["results"][0]["canonical_event"] is False
    output = cli._safe_result_for_output(result, _config())
    assert output["results"][0]["ref_kind"] == "sha256_of_projected_metadata"
    assert output["results"][0]["canonical_event"] is False


def test_output_rejects_missing_or_forged_provenance():
    for mutation in ({"ref_kind": None}, {"canonical_event": True}, {"canonical_event": 0}):
        result = cli._project_result(_answer(), _config().descriptor_sha256)
        result["results"][0].update(mutation)
        try:
            cli._safe_result_for_output(result, _config())
        except cli.QueryRefused:
            pass
        else:
            raise AssertionError("unverified provenance accepted")


def test_run_verified_keeps_provenance_from_launcher(monkeypatch):
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: _answer())
    output = io.StringIO()
    raw = b'{"situation":"synthetic-query","time":"2026-10-31T12:00:00Z"}'
    assert cli.run_verified(_config(), io.BytesIO(raw), output) == 0
    item = json.loads(output.getvalue())["results"][0]
    assert item["ref_kind"] == "sha256_of_projected_metadata"
    assert item["canonical_event"] is False
    assert "synthetic-query" not in output.getvalue()


def _resolution_fixture():
    from sandbox.mind.step2 import shared_projection as sp
    event = {"ts_utc": "2026-10-31T12:00:30Z", "agent": "codex-lead-1", "type": "message",
             "status": "done", "task_id": "synthetic-task", "message": "do-not-expose"}
    tasks = frozenset({"synthetic-task"})
    projection = sp.project_events([event], approved_tasks=tasks)
    data = json.dumps(projection, sort_keys=True, indent=1).encode()
    memory = json.dumps({"scope": "wd.q4-shared-projected-events.v1", "projection_schema": sp.SCHEMA,
                         "projection_sha256": hashlib.sha256(data).hexdigest()}, sort_keys=True).encode()
    record = projection["records"][0]
    result = _answer()
    result["memory_sha256"] = hashlib.sha256(memory).hexdigest()
    result["results"][0]["ref_sha256"] = hashlib.sha256(record["provenance"]["event_ref"].encode()).hexdigest()
    result["results"][0]["doc_id_sha256"] = hashlib.sha256(record["record_id"].encode()).hexdigest()
    return data, memory, tasks, record, result


def test_query_and_resolve_binds_same_input_and_returns_only_projected_text(monkeypatch):
    data, memory, tasks, record, result = _resolution_fixture()
    seen = []
    def launcher(**kwargs):
        seen.append(kwargs["query"])
        return result
    monkeypatch.setattr(cli, "_invoke_launcher", launcher)
    raw = b'{"situation":"sensitive query","time":"2026-10-31T12:02:00Z"}'
    got = cli.query_and_resolve(_config(), io.BytesIO(raw), memory, data, tasks)
    assert seen == [raw]
    assert got["status"] == "ANSWER" and got["results"][0]["stored_text"] == record["stored_text"]
    assert got["results"][0]["canonical_event"] is False
    assert "sensitive query" not in repr(got) and "do-not-expose" not in repr(got)


def test_query_and_resolve_refuses_wrong_time_hash_and_scope(monkeypatch):
    data, memory, tasks, record, result = _resolution_fixture()
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: result)
    late = b'{"situation":"sensitive","time":"2026-10-31T12:02:00Z"}'
    early = b'{"situation":"sensitive","time":"2026-10-31T12:00:45Z"}'
    invalid = b'{"situation":"sensitive","time":"not-time"}'
    for query, mem, proj, approved in [(early, memory, data, tasks), (invalid, memory, data, tasks),
                                       (late, memory + b" ", data, tasks), (late, memory, data + b" ", tasks),
                                       (late, memory, data, frozenset()), (late, memory, data, None)]:
        try:
            cli.query_and_resolve(_config(), io.BytesIO(query), mem, proj, approved)
        except cli.QueryRefused as exc:
            assert str(exc) == "resolution_refused" and "sensitive" not in str(exc)
        else:
            raise AssertionError("bad binding accepted")


def test_query_and_resolve_refuses_runtime_refused(monkeypatch):
    data, memory, tasks, _, _ = _resolution_fixture()
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: {"status": "REFUSED", "results": []})
    try:
        cli.query_and_resolve(_config(), io.BytesIO(b'{"situation":"s","time":"2026-10-31T12:02:00Z"}'),
                              memory, data, tasks)
    except cli.QueryRefused as exc:
        assert str(exc) == "resolution_refused"
    else:
        raise AssertionError("runtime refusal accepted")


def test_query_and_resolve_reads_original_stream_once_and_rejects_forged_descriptor(monkeypatch):
    data, memory, tasks, _, result = _resolution_fixture()
    class Counted(io.BytesIO):
        reads = 0
        def read(self, *args):
            self.reads += 1
            return super().read(*args)
    result["descriptor_sha256"] = "0" * 64
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: result)
    stream = Counted(b'{"situation":"s","time":"2026-10-31T12:02:00Z"}')
    try:
        cli.query_and_resolve(_config(), stream, memory, data, tasks)
    except cli.QueryRefused as exc:
        assert str(exc) == "resolution_refused"
    else:
        raise AssertionError("forged descriptor accepted")
    assert stream.reads == 1
