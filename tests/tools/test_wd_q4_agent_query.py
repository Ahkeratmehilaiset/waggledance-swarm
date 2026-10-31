"""Synthetic checks: no real runtime, model, memory, or subprocess."""

import io
import json
import hashlib
import datetime as dt
from dataclasses import replace

import pytest

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
        lock_timeout="2", timeout=30, scratch_root=r"C:\synthetic-scratch",
    )


def _answer():
    return {"status": "ANSWER", "scope": "wd.q4-shared-projected-events.v1",
            "descriptor_sha256": "7" * 64, "memory_sha256": "8" * 64,
            "results": [{"ref_sha256": "9" * 64, "doc_id_sha256": "a" * 64,
                         "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}]}


def test_typed_binding_invokes_launcher_with_query_on_stdin_only(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
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


def test_explicit_scratch_root_is_forwarded_and_missing_refuses_before_input(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    seen = {}
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **kwargs: seen.update(kwargs) or _answer())
    config = replace(_config(), scratch_root=r"C:\synthetic-scratch")
    cli.query_with_verified_config(config, io.BytesIO(b'{"situation":"s","time":"2026-10-31T12:00:00Z"}'))
    assert seen["scratch_root"] == r"C:\synthetic-scratch"
    seen.clear()
    with pytest.raises(cli.QueryRefused):
        cli.query_with_verified_config(replace(config, scratch_root=""), Unreadable())
    assert seen == {}


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
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    for mutation in ({"scope": "private-scope"}, {"descriptor_sha256": "0" * 64},
                     {"results": [{"ref_sha256": "9" * 64, "doc_id_sha256": "a" * 64,
                                   "ref_kind": None, "canonical_event": None}]}):
        answer = _answer()
        answer.update(mutation)
        monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: answer)
        try:
            cli.query_with_verified_config(_config(), io.BytesIO(b'{"situation":"s","time":"2026-10-31T12:00:00Z"}'))
        except cli.QueryRefused:
            pass
        else:
            raise AssertionError("out-of-scope result accepted")


def test_launcher_errors_are_sanitized(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    def fail(**_):
        raise RuntimeError("sensitive-query-or-path")
    monkeypatch.setattr(cli, "_invoke_launcher", fail)
    try:
        cli.query_with_verified_config(_config(), io.BytesIO(b'{"situation":"s","time":"2026-10-31T12:00:00Z"}'))
    except cli.QueryRefused as exc:
        assert str(exc) == "launcher_refused"
        assert exc.__cause__ is None
    else:
        raise AssertionError("launcher error accepted")


def test_refusal_is_sanitized_and_abstain_keeps_scope(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    raw = b'{"situation":"s","time":"2026-10-31T12:00:00Z"}'
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: {"status": "REFUSED", "reason": "sensitive"})
    assert cli.query_with_verified_config(_config(), io.BytesIO(raw)) == {
        "status": "REFUSED", "reason": "runtime_refused", "results": []}
    answer = _answer()
    answer.update(status="ABSTAIN", results=[])
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: answer)
    assert cli.query_with_verified_config(_config(), io.BytesIO(raw))["status"] == "ABSTAIN"


def test_known_refusal_is_preserved_but_unknown_never_echoed(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    raw = b'{"situation":"s","time":"2026-10-31T12:00:00Z"}'
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: {"status": "REFUSED", "reason": "reader_busy"})
    output = io.StringIO()
    assert cli.run_verified(_config(), io.BytesIO(raw), output) == 2
    assert json.loads(output.getvalue())["reason"] == "reader_busy"
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: {"status": "REFUSED", "reason": "private-fake-reason"})
    output = io.StringIO()
    assert cli.run_verified(_config(), io.BytesIO(raw), output) == 2
    assert json.loads(output.getvalue())["reason"] == "runtime_refused"
    assert "private-fake-reason" not in output.getvalue()


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
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: _answer())
    output = io.StringIO()
    raw = b'{"situation":"synthetic-query","time":"2026-10-31T12:00:00Z"}'
    assert cli.run_verified(_config(), io.BytesIO(raw), output) == 0
    item = json.loads(output.getvalue())["results"][0]
    assert item["ref_kind"] == "sha256_of_projected_metadata"
    assert item["canonical_event"] is False
    assert "synthetic-query" not in output.getvalue()


def test_run_verified_short_write_is_not_success(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: _answer())
    class ShortWriter:
        def write(self, text):
            return len(text) - 1
    raw = b'{"situation":"synthetic-query","time":"2026-10-31T12:00:00Z"}'
    assert cli.run_verified(_config(), io.BytesIO(raw), ShortWriter()) == 2


def _resolution_fixture():
    from sandbox.mind.step2 import shared_projection as sp
    roster = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
              "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
              "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
    fleet = json.dumps({"lanes": [{"agent": name, "agent_uuid": uid} for name, uid in roster.items()
                                   if name != "codex-tools-1"],
                        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": roster["codex-tools-1"]}}).encode()
    policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": ["synthetic-task"]}).encode()
    pins = {"policy_bytes": policy, "policy_sha256": hashlib.sha256(policy).hexdigest(),
            "roster_bytes": fleet, "roster_sha256": hashlib.sha256(fleet).hexdigest()}
    event = {"ts_utc": "2026-10-31T12:00:30Z", "agent": "codex-lead-1", "type": "message",
             "status": "done", "task_id": "synthetic-task", "agent_uuid": roster["codex-lead-1"],
             "message": "do-not-expose"}
    tasks = frozenset({"synthetic-task"})
    projection = sp.project_events([event], approved_tasks=tasks, roster=roster, exclude_review_signal=True)
    data = json.dumps(projection, sort_keys=True, indent=1).encode()
    memory = json.dumps({"scope": "wd.q4-shared-projected-events.v1", "projection_schema": sp.SCHEMA,
                         "projection_sha256": hashlib.sha256(data).hexdigest(),
                         "policy": {"authorship": "declared_roster_consistent", "review_signal_excluded": True,
                                    "policy_sha256": pins["policy_sha256"],
                                    "roster_sha256": pins["roster_sha256"]}}, sort_keys=True).encode()
    pins["expected_memory_sha256"] = hashlib.sha256(memory).hexdigest()
    record = projection["records"][0]
    result = _answer()
    result["memory_sha256"] = hashlib.sha256(memory).hexdigest()
    result["results"][0]["ref_sha256"] = hashlib.sha256(record["provenance"]["event_ref"].encode()).hexdigest()
    result["results"][0]["doc_id_sha256"] = hashlib.sha256(record["record_id"].encode()).hexdigest()
    return data, memory, tasks, record, result, pins


def test_query_and_resolve_requires_all_pins_before_reading_or_launching(monkeypatch):
    data, memory, tasks, _, _, _ = _resolution_fixture()
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: pytest.fail("launcher reached"))
    stream = io.BytesIO(b'{"situation":"synthetic","time":"2026-10-31T12:02:00Z"}')
    with pytest.raises(cli.QueryRefused, match="resolution_refused"):
        cli.query_and_resolve(_config(), stream, memory, data, tasks)
    assert stream.tell() == 0


def test_query_and_resolve_binds_same_input_and_returns_only_projected_text(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    data, memory, tasks, record, result, pins = _resolution_fixture()
    seen = []
    def launcher(**kwargs):
        seen.append(kwargs["query"])
        return result
    monkeypatch.setattr(cli, "_invoke_launcher", launcher)
    raw = b'{"situation":"sensitive query","time":"2026-10-31T12:02:00Z"}'
    got = cli.query_and_resolve(_config(), io.BytesIO(raw), memory, data, tasks, **pins)
    assert seen == [raw]
    assert got["status"] == "ANSWER" and got["results"][0]["stored_text"] == record["stored_text"]
    assert got["results"][0]["canonical_event"] is False
    assert "sensitive query" not in repr(got) and "do-not-expose" not in repr(got)


def test_query_and_resolve_refuses_wrong_time_hash_and_scope(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    data, memory, tasks, record, result, pins = _resolution_fixture()
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: result)
    late = b'{"situation":"sensitive","time":"2026-10-31T12:02:00Z"}'
    early = b'{"situation":"sensitive","time":"2026-10-31T12:00:45Z"}'
    invalid = b'{"situation":"sensitive","time":"not-time"}'
    for query, mem, proj, approved in [(early, memory, data, tasks), (invalid, memory, data, tasks),
                                       (late, memory + b" ", data, tasks), (late, memory, data + b" ", tasks),
                                       (late, memory, data, frozenset()), (late, memory, data, None)]:
        try:
            cli.query_and_resolve(_config(), io.BytesIO(query), mem, proj, approved, **pins)
        except cli.QueryRefused as exc:
            assert str(exc) == "resolution_refused" and "sensitive" not in str(exc)
        else:
            raise AssertionError("bad binding accepted")


def test_query_and_resolve_refuses_runtime_refused(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    data, memory, tasks, _, _, pins = _resolution_fixture()
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: {"status": "REFUSED", "results": []})
    try:
        cli.query_and_resolve(_config(), io.BytesIO(b'{"situation":"s","time":"2026-10-31T12:02:00Z"}'),
                              memory, data, tasks, **pins)
    except cli.QueryRefused as exc:
        assert str(exc) == "resolution_refused"
    else:
        raise AssertionError("runtime refusal accepted")


def test_query_and_resolve_reads_original_stream_once_and_rejects_forged_descriptor(monkeypatch):
    monkeypatch.setattr(cli, "_utc_now", lambda: dt.datetime(2026, 10, 31, 13, tzinfo=dt.timezone.utc))
    data, memory, tasks, _, result, pins = _resolution_fixture()
    class Counted(io.BytesIO):
        reads = 0
        def read(self, *args):
            self.reads += 1
            return super().read(*args)
    result["descriptor_sha256"] = "0" * 64
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **_: result)
    stream = Counted(b'{"situation":"s","time":"2026-10-31T12:02:00Z"}')
    try:
        cli.query_and_resolve(_config(), stream, memory, data, tasks, **pins)
    except cli.QueryRefused as exc:
        assert str(exc) == "resolution_refused"
    else:
        raise AssertionError("forged descriptor accepted")
    assert stream.reads == 1


def test_query_and_resolve_rejects_future_before_launcher_and_allows_now_and_past(monkeypatch):
    data, memory, tasks, _, result, pins = _resolution_fixture()
    now = dt.datetime(2026, 10, 31, 12, 2, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(cli, "_utc_now", lambda: now, raising=False)
    seen = []
    def launch(**kwargs):
        seen.append(kwargs["query"])
        return result
    monkeypatch.setattr(cli, "_invoke_launcher", launch)
    def query_at(when):
        return io.BytesIO(json.dumps({"situation": "synthetic", "time": when}).encode())
    try:
        cli.query_and_resolve(_config(), query_at("2026-10-31T12:02:00.000001Z"), memory, data, tasks, **pins)
    except cli.QueryRefused as exc:
        assert str(exc) == "resolution_refused"
    else:
        raise AssertionError("future timestamp accepted")
    assert seen == []
    for when in ("2026-10-31T12:02:00Z", "2026-10-31T12:01:59.999999Z"):
        assert cli.query_and_resolve(_config(), query_at(when), memory, data, tasks, **pins)["status"] == "ANSWER"
    assert len(seen) == 2


def test_low_level_and_run_verified_reject_future_without_launch(monkeypatch):
    now = dt.datetime(2026, 10, 31, 12, 2, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(cli, "_utc_now", lambda: now)
    seen = []
    monkeypatch.setattr(cli, "_invoke_launcher", lambda **kw: seen.append(kw) or _answer())
    for stamp in ("2026-10-31T12:02:00.000001Z", "2026-10-31T12:02:00.0000001Z"):
        raw = json.dumps({"situation": "synthetic", "time": stamp}).encode()
        try:
            cli.query_with_verified_config(_config(), io.BytesIO(raw))
        except cli.QueryRefused:
            pass
        else:
            raise AssertionError("future low-level query accepted")
        output = io.StringIO()
        assert cli.run_verified(_config(), io.BytesIO(raw), output) == 2
        assert json.loads(output.getvalue())["status"] == "REFUSED"
    assert seen == []
