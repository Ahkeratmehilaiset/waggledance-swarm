"""Fixed-peer snapshot transport over the existing pipe; synthetic peer backend only, no model or live data.

Expected API, intentionally absent when these tests were written:
``run_fixed_peer(*, executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd, timeout_seconds,
max_output_bytes)`` checks every caller pin BEFORE spawning, holds share-denying leases on the interpreter
and the fixed peer code for the whole run, sends the plan and the snapshot bundle, and sends the query only
after a READY whose binding equals the parent-computed plan digest (consistency, not authentication).
"""
import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from tools import wd_q4_secure_transport as transport

windows = pytest.mark.skipif(os.name != "nt", reason="Windows named-pipe transport")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


PEER = Path(transport.__file__).absolute()
BUNDLE = bytes(range(256)) * 400  # 102400 bytes: more than one pipe buffer
RUNTIME = canonical({"schema": "synthetic-runtime-manifest", "files": []})
QUERY = canonical({"situation": "synthetic situation", "time": "2026-10-20T13:00:00Z"})


def interpreter_sha():
    return sha(Path(sys.executable).read_bytes())


def plan(**changes):
    value = {"schema": "wd.q4-fixed-plan.v1", "backend": "synthetic-v1",
             "interpreter_sha256": interpreter_sha(), "peer_code_sha256": sha(PEER.read_bytes()),
             "runtime_manifest_sha256": sha(RUNTIME), "bundle_sha256": sha(BUNDLE), "bundle_bytes": len(BUNDLE)}
    value.update(changes)
    return canonical(value)


def call(tmp_path, **changes):
    plan_bytes = changes.pop("plan_bytes", None) or plan()
    kwargs = dict(executable=sys.executable, expected_interpreter_sha256=interpreter_sha(),
                  interpreter_max_bytes=64 * 1024 * 1024, plan_bytes=plan_bytes,
                  expected_plan_sha256=sha(plan_bytes), bundle_bytes=BUNDLE, runtime_manifest_bytes=RUNTIME,
                  query_bytes=QUERY, cwd=str(tmp_path), timeout_seconds=60, max_output_bytes=4096)
    kwargs.update(changes)
    return transport.run_fixed_peer(**kwargs)


@windows
def test_synthetic_fixed_peer_roundtrip_after_anchored_checks(tmp_path):
    plan_bytes = plan()
    output, code = call(tmp_path, plan_bytes=plan_bytes)
    answer = json.loads(output)
    assert code == 0
    assert answer == {"schema": "wd.q4-fixed-synthetic-answer.v2", "status": "SYNTHETIC",
                      "plan_sha256": sha(plan_bytes), "bundle_sha256": sha(BUNDLE), "query": "RECEIVED",
                      "backend": "synthetic-v1", "source_authentication": "UNKNOWN", "runtime": "NOT_VERIFIED"}


@windows
def test_real_peer_output_is_identical_for_different_queries(tmp_path):
    # RCO2 F1: no query digest or other query-derived value may leave the child.
    first = call(tmp_path, query_bytes=QUERY)
    second = call(tmp_path, query_bytes=canonical({"situation": "another synthetic situation"}))
    assert first == second
    assert sha(QUERY) not in first[0].decode("ascii")


@windows
def test_interpreter_and_peer_code_leases_cover_the_whole_run(tmp_path, monkeypatch):
    from tools import wd_q4_fixed_parent_attestation as attestation
    original, events = attestation.attest_file, []
    def recording(path, digest, *, max_bytes, enabled=False):
        lease = original(path, digest, max_bytes=max_bytes, enabled=enabled)
        events.append(("open", os.path.normcase(path), digest))
        close = lease.close
        lease_type = type("Lease", (), {"__enter__": lambda self: (lease.__enter__(), self)[1],
                                        "__exit__": lambda self, *a: (events.append(("close", digest)),
                                                                      lease.__exit__(*a))[1]})
        return lease_type()
    real_run = transport._run
    def run(*args, **kwargs):
        events.append(("run",))
        return real_run(*args, **kwargs)
    monkeypatch.setattr(attestation, "attest_file", recording)
    monkeypatch.setattr(transport, "_run", run)
    call(tmp_path)
    opens = [e for e in events if e[0] == "open"]
    assert {e[2] for e in opens} == {interpreter_sha(), sha(PEER.read_bytes())}
    assert {e[1] for e in opens} == {os.path.normcase(sys.executable), os.path.normcase(str(PEER))}
    run_index = events.index(("run",))
    assert all(events.index(e) < run_index for e in opens)
    assert all(events.index(e) > run_index for e in events if e[0] == "close")
    assert len([e for e in events if e[0] == "close"]) == 2


def _bad_cases():
    good = json.loads(plan())
    return [
        ("plan_digest", {"expected_plan_sha256": "0" * 64}),
        ("interpreter_pin", {"plan_bytes": plan(interpreter_sha256="1" * 64)}),
        ("expected_interpreter", {"expected_interpreter_sha256": "2" * 64}),
        ("peer_pin", {"plan_bytes": plan(peer_code_sha256="3" * 64)}),
        ("runtime_pin", {"runtime_manifest_bytes": RUNTIME + b" "}),
        ("bundle_digest", {"bundle_bytes": BUNDLE[:-1] + b"\x00"}),
        ("bundle_length", {"plan_bytes": plan(bundle_bytes=len(BUNDLE) + 1)}),
        ("production_backend", {"plan_bytes": plan(backend="query_public_snapshot")}),
        ("unknown_backend", {"plan_bytes": plan(backend="python-c")}),
        ("extra_key", {"plan_bytes": canonical({**good, "code": "print(1)"})}),
        ("missing_key", {"plan_bytes": canonical({k: v for k, v in good.items() if k != "runtime_manifest_sha256"})}),
        ("noncanonical", {"plan_bytes": json.dumps(good, indent=1).encode()}),
        ("schema", {"plan_bytes": plan(schema="wd.q4-fixed-plan.v0")}),
        ("query_too_large", {"query_bytes": b"x" * 65537}),
        ("empty_query", {"query_bytes": b""}),
        ("bytearray_bundle", {"bundle_bytes": bytearray(BUNDLE)}),
        ("other_executable", {"executable": str(PEER)}),
    ]


@pytest.mark.parametrize("name,changes", _bad_cases(), ids=[c[0] for c in _bad_cases()])
def test_every_pin_is_checked_before_any_spawn_or_query(tmp_path, monkeypatch, name, changes):
    monkeypatch.setattr(transport, "_run", lambda *a, **kw: pytest.fail("spawned before pins were checked"))
    with pytest.raises(transport._Refused) as caught:
        call(tmp_path, **changes)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert caught.value.args == ("secure_transport_refused",)


def _exchange(monkeypatch, ready_binding):
    sent = []
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_write", lambda _k, _p, data, _d: sent.append(data))
    def reply(_k, _p, _limit, _deadline):
        challenge = json.loads(sent[-1])
        return canonical({"schema": "wd.q4-fixed-ready.v1", "status": "READY", "nonce": challenge["nonce"],
                          "binding_sha256": ready_binding})
    monkeypatch.setattr(transport, "_frame_read", reply)
    return sent


def test_query_is_sent_only_after_a_ready_matching_the_parent_plan_digest(monkeypatch):
    plan_bytes = plan()
    control = {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32, "binding_sha256": sha(plan_bytes)}
    sent = _exchange(monkeypatch, sha(plan_bytes))
    transport._fixed_exchange(None, None, 11, 22, control, plan_bytes, BUNDLE, QUERY, 1)
    assert sent[0] == plan_bytes and sent[1] == BUNDLE
    assert set(json.loads(sent[2])) == {"schema", "nonce"}
    assert sent[3] == QUERY and len(sent) == 4


@pytest.mark.parametrize("binding", ["0" * 64, "echo"])
def test_mismatched_or_echoed_ready_never_receives_the_query(monkeypatch, binding):
    plan_bytes = plan()
    control = {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32, "binding_sha256": sha(plan_bytes)}
    sent = _exchange(monkeypatch, sha(plan_bytes + b"x") if binding == "echo" else binding)
    with pytest.raises(transport._Refused):
        transport._fixed_exchange(None, None, 11, 22, control, plan_bytes, BUNDLE, QUERY, 1)
    assert QUERY not in sent
    assert all(sha(plan_bytes) not in item.decode("latin-1") for item in sent[2:])


def test_child_verifies_plan_and_bundle_before_ready():
    plan_bytes = plan()
    peer = sha(PEER.read_bytes())
    parsed = transport._fixed_child_verify(plan_bytes, BUNDLE, peer)
    assert parsed["bundle_sha256"] == sha(BUNDLE)
    for args in ((plan_bytes, BUNDLE + b"x", peer), (plan_bytes, BUNDLE, "0" * 64),
                 (plan(backend="query_public_snapshot"), BUNDLE, peer), (b"{}", BUNDLE, peer)):
        with pytest.raises(transport._Refused):
            transport._fixed_child_verify(*args)


def test_child_answer_is_closed_and_bound_to_verified_bytes():
    plan_bytes = plan()
    parsed = transport._fixed_child_verify(plan_bytes, BUNDLE, sha(PEER.read_bytes()))
    raw = transport._fixed_child_answer(parsed, plan_bytes, BUNDLE, QUERY)
    answer = json.loads(raw)
    assert answer["plan_sha256"] == sha(plan_bytes) and answer["bundle_sha256"] == sha(BUNDLE)
    assert answer["query"] == "RECEIVED" and "query_sha256" not in answer
    assert QUERY.decode() not in raw.decode("ascii")


@pytest.mark.parametrize("other", [b"x", QUERY + b" ", b"q" * 65536, canonical({"situation": "s"})],
                         ids=["one_byte", "one_more_byte", "max_size", "other_situation"])
def test_child_answer_carries_no_query_derived_value(other):
    # Guessable situations: a digest, length or prefix of the query would confirm guesses.
    plan_bytes = plan()
    parsed = transport._fixed_child_verify(plan_bytes, BUNDLE, sha(PEER.read_bytes()))
    raw = transport._fixed_child_answer(parsed, plan_bytes, BUNDLE, QUERY)
    assert transport._fixed_child_answer(parsed, plan_bytes, BUNDLE, other) == raw
    for digest in (hashlib.sha256, hashlib.sha1, hashlib.md5, hashlib.blake2b):
        assert digest(QUERY).hexdigest() not in raw.decode("ascii")


@windows
def test_output_bound_is_enforced_on_the_real_fixed_peer(tmp_path):
    with pytest.raises(transport._Refused):
        call(tmp_path, max_output_bytes=16)


def test_fixed_peer_documents_limits():
    doc = transport.run_fixed_peer.__doc__
    for phrase in ("not authentication", "runtime", "NOT verified", "synthetic", "before spawning"):
        assert phrase in doc
