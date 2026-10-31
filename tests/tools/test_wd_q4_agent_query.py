"""Synthetic checks: no real runtime, model, memory, or subprocess."""

import io
import json
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
