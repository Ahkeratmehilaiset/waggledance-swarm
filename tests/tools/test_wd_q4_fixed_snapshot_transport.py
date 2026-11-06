"""Fixed-peer snapshot transport over the existing pipe; synthetic peer backend only, no model or live data.

Expected API, intentionally absent when these tests were written:
``run_fixed_peer(*, executable, expected_interpreter_sha256, interpreter_max_bytes, plan_bytes,
expected_plan_sha256, bundle_bytes, runtime_manifest_bytes, query_bytes, cwd, timeout_seconds,
max_output_bytes)`` checks every caller pin BEFORE spawning, holds share-denying leases on the interpreter
and the fixed peer code for the whole run. It sends the plan and checks READY before sending the bundle,
then checks the bundle ACK before sending the query. These checks are consistency, not authentication.
"""
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
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


def _ready(nonce, binding):
    return canonical({"schema": "wd.q4-fixed-ready.v1", "status": "READY", "nonce": nonce, "binding_sha256": binding})


def _ack(nonce, bundle_digest, **changes):
    value = {"schema": "wd.q4-fixed-bundle-ack.v1", "status": "BUNDLE_VERIFIED", "nonce": nonce,
             "bundle_sha256": bundle_digest}
    value.update(changes)
    return canonical(value)


def _exchange(monkeypatch, ready_binding, ack=None):
    """Scripted peer: READY for the first read, then the ACK (default: correct) for the second."""
    sent, reads = [], []
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: None)
    monkeypatch.setattr(transport, "_frame_write", lambda _k, _p, data, _d: sent.append(data))
    def reply(_k, _p, limit, _deadline):
        reads.append((len(sent), limit))
        nonce = json.loads(sent[1])["nonce"]
        if len(reads) == 1:
            return _ready(nonce, ready_binding)
        return ack(nonce) if ack is not None else _ack(nonce, sha(BUNDLE))
    monkeypatch.setattr(transport, "_frame_read", reply)
    return sent, reads


def _control(plan_bytes):
    return {"schema": "wd.q4-fixed-control.v1", "nonce": "a" * 32, "binding_sha256": sha(plan_bytes)}


def test_no_bundle_before_ready_and_no_query_before_bundle_ack(monkeypatch):
    plan_bytes = plan()
    sent, reads = _exchange(monkeypatch, sha(plan_bytes))
    transport._fixed_exchange(None, None, 11, 22, _control(plan_bytes), plan_bytes, BUNDLE, QUERY, 1)
    # phase 1: plan, nonce-only challenge, READY; phase 2: bundle, ACK, then the query
    assert sent[0] == plan_bytes
    assert json.loads(sent[1]) == {"schema": "wd.q4-fixed-challenge.v1", "nonce": "a" * 32}
    assert sent[2] == BUNDLE and sent[3] == QUERY and len(sent) == 4
    assert [count for count, _ in reads] == [2, 3]  # READY read after plan+challenge, ACK read after bundle
    assert all(limit <= 4096 for _, limit in reads)


@pytest.mark.parametrize("binding", ["0" * 64, "echo"])
def test_mismatched_or_echoed_ready_never_receives_the_bundle_or_query(monkeypatch, binding):
    plan_bytes = plan()
    sent, _ = _exchange(monkeypatch, sha(plan_bytes + b"x") if binding == "echo" else binding)
    with pytest.raises(transport._Refused):
        transport._fixed_exchange(None, None, 11, 22, _control(plan_bytes), plan_bytes, BUNDLE, QUERY, 1)
    assert BUNDLE not in sent and QUERY not in sent and len(sent) == 2
    assert all(sha(plan_bytes) not in item.decode("latin-1") for item in sent[1:])


def _bad_acks():
    return [
        ("wrong_digest", lambda n: _ack(n, sha(BUNDLE + b"x"))),
        ("plan_digest", lambda n: _ack(n, sha(plan()))),
        ("wrong_nonce", lambda n: _ack("b" * 32, sha(BUNDLE))),
        ("wrong_status", lambda n: _ack(n, sha(BUNDLE), status="READY")),
        ("wrong_schema", lambda n: _ack(n, sha(BUNDLE), schema="wd.q4-fixed-ready.v1")),
        ("extra_key", lambda n: _ack(n, sha(BUNDLE), query="x")),
        ("missing_key", lambda n: canonical({"schema": "wd.q4-fixed-bundle-ack.v1", "status": "BUNDLE_VERIFIED",
                                             "nonce": n})),
        ("duplicate_key", lambda n: _ack(n, sha(BUNDLE))[:-1] + b',"status":"BUNDLE_VERIFIED"}'),
        ("second_ready", lambda n: _ready(n, sha(plan()))),
        ("empty", lambda n: b""),
    ]


@pytest.mark.parametrize("name,ack", _bad_acks(), ids=[c[0] for c in _bad_acks()])
def test_bad_bundle_ack_never_receives_the_query(monkeypatch, name, ack):
    plan_bytes = plan()
    sent, _ = _exchange(monkeypatch, sha(plan_bytes), ack=ack)
    with pytest.raises(transport._Refused) as caught:
        transport._fixed_exchange(None, None, 11, 22, _control(plan_bytes), plan_bytes, BUNDLE, QUERY, 1)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert QUERY not in sent and sent[2] == BUNDLE and len(sent) == 3


def test_parent_refuses_a_bundle_not_matching_the_plan_before_any_io(monkeypatch):
    plan_bytes = plan()
    monkeypatch.setattr(transport, "_peer", lambda *a, **kw: pytest.fail("peer I/O before the bundle check"))
    with pytest.raises(transport._Refused):
        transport._fixed_exchange(None, None, 11, 22, _control(plan_bytes), plan_bytes, BUNDLE[:-1] + b"\x01",
                                  QUERY, 1)


class _ScriptedParent:
    """Feeds child frames in order and records every child read limit and write."""

    def __init__(self, frames):
        self.frames, self.events = list(frames), []

    def read_frame(self, limit):
        self.events.append(("read", limit))
        frame = self.frames.pop(0)
        if callable(frame):
            frame = frame(self)
        assert len(frame) <= limit, "child read limit smaller than the frame"
        return frame

    def write_frame(self, data):
        self.events.append(("write", data))

    def nonce_reply(self):
        return [e[1] for e in self.events if e[0] == "write"]


def _child_frames(plan_bytes=None, bundle=BUNDLE, query=QUERY):
    challenge = canonical({"schema": "wd.q4-fixed-challenge.v1", "nonce": "c" * 32})
    return [plan_bytes or plan(), challenge, bundle, query]


def test_child_writes_ready_before_reading_the_bundle_and_ack_before_the_query():
    plan_bytes = plan()
    parent = _ScriptedParent(_child_frames(plan_bytes))
    raw = transport._fixed_child_protocol(parent.read_frame, parent.write_frame, sha(PEER.read_bytes()))
    assert parent.events == [
        ("read", transport._MAX_PLAN), ("read", 4096), ("write", _ready("c" * 32, sha(plan_bytes))),
        ("read", len(BUNDLE)), ("write", _ack("c" * 32, sha(BUNDLE))), ("read", transport._MAX_INPUT)]
    assert json.loads(transport._fixed_unwrap("c" * 32, QUERY, raw, 4096))["query"] == "RECEIVED"


def test_child_bundle_read_limit_is_the_plan_length_not_the_global_cap():
    parent = _ScriptedParent(_child_frames())
    transport._fixed_child_protocol(parent.read_frame, parent.write_frame, sha(PEER.read_bytes()))
    limits = [e[1] for e in parent.events if e[0] == "read"]
    assert limits[2] == len(BUNDLE) < transport._MAX_BUNDLE


@pytest.mark.parametrize("case", ["bad_plan", "peer_pin", "bad_challenge", "bundle_digest", "bundle_short"])
def test_child_refuses_without_ready_or_ack_on_bad_input(case):
    frames = _child_frames()
    peer = sha(PEER.read_bytes())
    if case == "bad_plan":
        frames[0] = plan(backend="query_public_snapshot")
    elif case == "peer_pin":
        peer = "0" * 64
    elif case == "bad_challenge":
        frames[1] = canonical({"schema": "wd.q4-fixed-challenge.v1", "nonce": "c" * 32, "binding_sha256": "0" * 64})
    elif case == "bundle_digest":
        frames[2] = BUNDLE[:-1] + b"\x01"
    else:
        frames[2] = BUNDLE[:-1]
    parent = _ScriptedParent(frames)
    with pytest.raises(transport._Refused):
        transport._fixed_child_protocol(parent.read_frame, parent.write_frame, peer)
    writes = parent.nonce_reply()
    if case in ("bad_plan", "peer_pin", "bad_challenge"):
        assert writes == [] and ("read", len(BUNDLE)) not in parent.events
    else:
        assert writes == [_ready("c" * 32, sha(plan()))]
    assert ("read", transport._MAX_INPUT) not in parent.events


def test_child_plan_and_bundle_checks_reject_invalid_inputs():
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
    for phrase in ("not authentication", "runtime", "NOT verified", "synthetic", "before spawning",
                   "before any bundle", "bundle ACK", "real backend is unavailable"):
        assert phrase in doc


# --- v5: query-plus-answer binding (RCO2 design 2026-11-06 09:24:09Z) -----------------------------------------
# The child binds its inner answer to the exact query under the per-call control nonce, the parent verifies the
# binding before any use of the answer. The nonce travels in the challenge, so this binds three byte strings
# only: it is NOT lane authentication, NOT freshness and NOT proof that a model used the query.
NONCE = "d" * 32
MAGIC = b"WDQ4BND2"
DOMAIN = b"wd.q4-query-bind.v2"
INNER = canonical({"schema": "wd.q4-fixed-synthetic-answer.v2", "status": "SYNTHETIC", "query": "RECEIVED"})
OTHER_QUERY = canonical({"situation": "another situation", "time": "2026-10-20T13:00:00Z"})


def mac(nonce, query, inner):
    message = DOMAIN + b"\x00" + struct.pack(">I", len(query)) + query + struct.pack(">I", len(inner)) + inner
    return hmac.new(bytes.fromhex(nonce), message, hashlib.sha256).hexdigest().encode("ascii")


def frame(nonce, query, inner):
    return MAGIC + mac(nonce, query, inner) + inner


def test_bound_frame_is_magic_mac_inner_over_the_nonce_the_exact_query_and_the_exact_inner():
    wrapped = transport._fixed_wrap(NONCE, QUERY, INNER)
    assert wrapped == frame(NONCE, QUERY, INNER)
    assert len(wrapped) == len(INNER) + transport._BIND_OVERHEAD == len(INNER) + 72
    assert transport._fixed_unwrap(NONCE, QUERY, wrapped, len(INNER)) == INNER
    # the nonce is the key, not part of the frame; the MAC is domain separated
    assert bytes.fromhex(NONCE) not in wrapped and NONCE.encode() not in wrapped
    assert hmac.new(bytes.fromhex(NONCE), QUERY + INNER, hashlib.sha256).hexdigest().encode() not in wrapped


@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_same_query_and_nonce_with_one_inner_byte_altered_after_the_mac_is_refused(position):
    wrapped = transport._fixed_wrap(NONCE, QUERY, INNER)
    index = {"first": 0, "middle": len(INNER) // 2, "last": len(INNER) - 1}[position] + transport._BIND_OVERHEAD
    altered = wrapped[:index] + bytes([wrapped[index] ^ 0x01]) + wrapped[index + 1:]
    assert len(altered) == len(wrapped)
    with pytest.raises(transport._Refused) as caught:
        transport._fixed_unwrap(NONCE, QUERY, altered, 4096)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("case", ["query_swap", "frame_for_another_query", "nonce_replay", "mac_edited",
                                  "mac_uppercase", "mac_not_hex"])
def test_query_swap_nonce_replay_and_mac_edits_are_refused(case):
    wrapped = transport._fixed_wrap(NONCE, QUERY, INNER)
    nonce, query, candidate = NONCE, QUERY, wrapped
    digest = wrapped[8:72]
    if case == "query_swap":
        query = OTHER_QUERY
    elif case == "frame_for_another_query":
        candidate = transport._fixed_wrap(NONCE, OTHER_QUERY, INNER)
    elif case == "nonce_replay":
        nonce = "e" * 32  # a frame bound under one call's nonce presented to another call
    elif case == "mac_edited":
        candidate = MAGIC + (b"0" if digest[:1] != b"0" else b"1") + digest[1:] + INNER
    elif case == "mac_uppercase":
        if digest.upper() == digest:
            pytest.skip("digest has no letters")
        candidate = MAGIC + digest.upper() + INNER
    else:
        candidate = MAGIC + b"z" * 64 + INNER
    with pytest.raises(transport._Refused) as caught:
        transport._fixed_unwrap(nonce, query, candidate, 4096)
    text = repr(caught.value) + repr(caught.value.args)
    assert caught.value.__context__ is None and NONCE not in text and digest.decode() not in text
    assert "synthetic situation" not in text and "another situation" not in text


@pytest.mark.parametrize("name", ["empty", "magic_only", "one_short", "wrong_magic", "older_magic", "no_inner",
                                  "not_bytes"])
def test_malformed_prefix_is_refused(name):
    wrapped = transport._fixed_wrap(NONCE, QUERY, INNER)
    candidates = {"empty": b"", "magic_only": MAGIC, "one_short": wrapped[:transport._BIND_OVERHEAD - 1],
                  "wrong_magic": b"XXXXXXXX" + wrapped[8:], "older_magic": b"WDQ4BND1" + wrapped[8:],
                  "no_inner": wrapped[:transport._BIND_OVERHEAD], "not_bytes": wrapped.decode("latin-1")}
    with pytest.raises(transport._Refused):
        transport._fixed_unwrap(NONCE, QUERY, candidates[name], 4096)


def test_inner_cap_is_the_caller_cap_and_the_frame_cap_adds_only_the_framing_overhead():
    inner = b"x" * 100
    wrapped = transport._fixed_wrap(NONCE, QUERY, inner)
    assert transport._fixed_unwrap(NONCE, QUERY, wrapped, 100) == inner
    with pytest.raises(transport._Refused):
        transport._fixed_unwrap(NONCE, QUERY, wrapped, 99)
    assert transport._BIND_OVERHEAD == len(MAGIC) + 64 == 72
    # the global inner cap is retained on the child side; the frame may exceed it by the overhead only
    assert len(transport._fixed_wrap(NONCE, QUERY, b"x" * transport._MAX_OUTPUT)) == transport._MAX_OUTPUT + 72
    with pytest.raises(transport._Refused):
        transport._fixed_wrap(NONCE, QUERY, b"x" * (transport._MAX_OUTPUT + 1))
    for bad in (b"", 1, None):
        with pytest.raises(transport._Refused):
            transport._fixed_wrap(NONCE, QUERY, bad)
    for bad_nonce in ("d" * 31, "d" * 33, "D" * 32, "g" * 32, 7):
        with pytest.raises(transport._Refused):
            transport._fixed_wrap(bad_nonce, QUERY, INNER)
    with pytest.raises(transport._Refused):
        transport._fixed_unwrap(NONCE, QUERY, wrapped, transport._MAX_OUTPUT + 1)


def test_child_protocol_returns_the_frame_bound_to_the_challenge_nonce_and_the_exact_query_it_read():
    plan_bytes = plan()
    peer = sha(PEER.read_bytes())
    parent = _ScriptedParent(_child_frames(plan_bytes))
    raw = transport._fixed_child_protocol(parent.read_frame, parent.write_frame, peer)
    inner = transport._fixed_child_answer(transport._fixed_child_verify(plan_bytes, BUNDLE, peer), plan_bytes,
                                          BUNDLE, QUERY)
    assert raw == frame("c" * 32, QUERY, inner)
    assert transport._fixed_unwrap("c" * 32, QUERY, raw, 4096) == inner
    for nonce, query in (("c" * 32, QUERY + b" "), ("c" * 32, OTHER_QUERY), ("e" * 32, QUERY)):
        with pytest.raises(transport._Refused):
            transport._fixed_unwrap(nonce, query, raw, 4096)


def _parent_with_fake_child(monkeypatch, reply):
    """run_fixed_peer with the leases stubbed and the pipe replaced: reply(nonce, query) is the child output."""
    from tools import wd_q4_fixed_parent_attestation as attestation
    seen = []

    @contextlib.contextmanager
    def fake_attest(*args, **kwargs):
        yield

    def fake_run(command, cwd, metadata, stdin_bytes, timeout_seconds, max_output_bytes, fixed=None):
        control = fixed[0]
        seen.append((control["nonce"], stdin_bytes, max_output_bytes))
        return reply(control["nonce"], stdin_bytes), 0
    monkeypatch.setattr(attestation, "attest_file", fake_attest)
    monkeypatch.setattr(transport, "_run", fake_run)
    return seen


def test_parent_accepts_only_the_frame_bound_to_its_own_nonce_and_query(tmp_path, monkeypatch):
    seen = _parent_with_fake_child(monkeypatch, lambda nonce, query: transport._fixed_wrap(nonce, query, INNER))
    assert call(tmp_path) == (INNER, 0)
    nonce, query, cap = seen[0]
    assert len(nonce) == 32 and query == QUERY and cap == 4096


@pytest.mark.parametrize("case", ["unbound_inner", "other_nonce", "other_query", "altered_inner", "inner_over_cap"])
def test_parent_refuses_unbound_or_misbound_child_output_after_the_run(tmp_path, monkeypatch, case):
    def reply(nonce, query):
        if case == "unbound_inner":
            return INNER
        if case == "other_nonce":
            return transport._fixed_wrap("e" * 32, query, INNER)
        if case == "other_query":
            return transport._fixed_wrap(nonce, OTHER_QUERY, INNER)
        if case == "altered_inner":
            wrapped = transport._fixed_wrap(nonce, query, INNER)
            return wrapped[:-1] + bytes([wrapped[-1] ^ 0x01])
        return transport._fixed_wrap(nonce, query, b"x" * 4097)
    seen = _parent_with_fake_child(monkeypatch, reply)
    with pytest.raises(transport._Refused) as caught:
        call(tmp_path)
    text = repr(caught.value) + repr(caught.value.args)
    assert len(seen) == 1 and caught.value.__context__ is None
    assert seen[0][0] not in text and "synthetic situation" not in text and transport._QUARANTINE == []


def test_unwrap_failure_is_a_plain_refusal_and_cleanup_uncertainty_passes_through_unchanged(tmp_path, monkeypatch):
    _parent_with_fake_child(monkeypatch, lambda nonce, query: INNER)
    with pytest.raises(transport._Refused):
        call(tmp_path)
    assert transport._QUARANTINE == []

    def uncertain(*args, **kwargs):
        raise transport.TransportCleanupUnknown("timeout")
    monkeypatch.setattr(transport, "_run", uncertain)
    with pytest.raises(transport.TransportCleanupUnknown) as caught:
        call(tmp_path)
    assert caught.value.prior_outcome == "timeout"

    def timed_out(*args, **kwargs):
        raise TimeoutError("synthetic private")
    monkeypatch.setattr(transport, "_run", timed_out)
    with pytest.raises(TimeoutError, match="^secure_transport_timeout$"):
        call(tmp_path)


def test_diagnostic_output_is_not_wrapped_and_its_contract_is_unchanged(tmp_path, monkeypatch):
    _parent_with_fake_child(monkeypatch, lambda nonce, query: b"after_query")
    plan_bytes = plan()
    result = transport._run_fixed_peer_impl(sys.executable, interpreter_sha(), 64 * 1024 * 1024, plan_bytes,
                                            sha(plan_bytes), BUNDLE, RUNTIME, QUERY, str(tmp_path), 60, 4096,
                                            False, "observe")
    assert result == {"status": "DIAGNOSTIC", "phase": "after_query", "cleanup": "UNKNOWN"}


@windows
def test_real_fixed_peer_roundtrip_is_bound_and_the_inner_cap_is_exact(tmp_path):
    output, code = call(tmp_path)
    assert code == 0 and json.loads(output)["query"] == "RECEIVED" and not output.startswith(MAGIC)
    assert call(tmp_path, max_output_bytes=len(output)) == (output, 0)
    with pytest.raises(transport._Refused):
        call(tmp_path, max_output_bytes=len(output) - 1)


def test_fixed_peer_documents_what_the_binding_is_not():
    doc = transport.run_fixed_peer.__doc__
    for phrase in ("not lane authentication", "not freshness", "used the query", "control nonce",
                   "wd.q4-query-bind.v2"):
        assert phrase in doc


# --- v4: default-off public-snapshot backend behind an externally pinned runtime manifest ---------------------
# Synthetic runtime tree only: its snapshot module is a STUB that stands in for query_public_snapshot, so these
# tests prove the loader order and the wiring, never a real model, index or Q4 result.

VERIFIER = PEER.parents[1] / "sandbox" / "mind" / "step2" / "runtime_manifest.py"
GENERATION, REVISION = "a" * 40, "b" * 40
STUB_TEMPLATE = '''import hashlib
import json
import sys

MODE = "__MODE__"
STATUS = "__STATUS__"
RESULTS = json.loads(__RESULTS__)
__NOISE__

def query_public_snapshot(*, pointer_bytes, expected_pointer_sha256, approved_tasks, policy_bytes, policy_sha256,
                          roster_bytes, roster_sha256, expected_public_policy_sha256, active_artifact_bytes,
                          cell_snapshots, history_bytes, public_policy_bytes, expected_source_sha256,
                          expected_encoder_sha256, query_bytes, model_dir, expected_runtime_revision, enabled):
    sha = lambda data: hashlib.sha256(data).hexdigest()
    bound = (enabled is True and sha(pointer_bytes) == expected_pointer_sha256 and sha(policy_bytes) == policy_sha256
             and sha(roster_bytes) == roster_sha256 and sha(public_policy_bytes) == expected_public_policy_sha256
             and type(query_bytes) is bytes and len(query_bytes) > 0 and type(approved_tasks) is frozenset
             and NOISE_OK)
    if not bound:
        return {"status": "ABSTAIN", "results": [], "source_authentication": "UNKNOWN", "freshness": "UNKNOWN"}
    results = [dict(item) for item in RESULTS]
    if MODE == "echo":
        results = [dict(item, stored_text=query_bytes.decode("utf-8")) for item in results]
    elif MODE == "unknown":
        results = [dict(results[0], ref_sha256="0" * 64)]
    elif MODE == "extra":
        results = [dict(item, situation="x") for item in results]
    return {"status": STATUS, "results": results, "source_authentication": "UNKNOWN", "freshness": "UNKNOWN"}
'''
NOISE = '''import ctypes
import os
from ctypes import wintypes as W

print("IMPORT-NOISE")
os.write(2, b"IMPORT-NOISE-FD2")
_k = ctypes.WinDLL("kernel32")
_k.GetStdHandle.restype = W.HANDLE
_k.GetStdHandle.argtypes = [W.DWORD]
_k.GetFileType.argtypes = [W.HANDLE]
_k.CreateFileW.restype = W.HANDLE
_k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
_STREAMS = [_k.GetFileType(_k.GetStdHandle(s & 0xFFFFFFFF)) for s in (-10, -11, -12)]
_CONSOLE = _k.CreateFileW("CONOUT$", 0x40000000, 3, None, 3, 0, None) not in (None, ctypes.c_void_p(-1).value)
NOISE_OK = _STREAMS == [2, 2, 2] and not _CONSOLE and sys.stdout is sys.stderr
'''


def make_stub(results=(), mode="good", status="ANSWER", noisy=False, outside=None):
    noise = NOISE if noisy else "NOISE_OK = True"
    if outside is not None:  # a runtime module that reaches outside the verified tree at import time
        noise += "\nsys.path.append(%r)\nimport planted_outside\n" % str(outside)
    return (STUB_TEMPLATE.replace("__MODE__", mode).replace("__STATUS__", status)
            .replace("__RESULTS__", repr(json.dumps(list(results)))).replace("__NOISE__", noise))


STUB = make_stub()
PARTS = {"pointer_bytes": b"synthetic-pointer", "policy_bytes": b"synthetic-policy",
         "roster_bytes": b"synthetic-roster", "public_policy_bytes": b"synthetic-public-policy",
         "active_artifact_bytes": {"descriptor.json": b"d", "memory.json": b"m"},
         "history_bytes": {"q4-pointer-" + "1" * 64 + ".json": b"h"},
         "cell_snapshots": {"cell-a": {"pointer": b"p", "log": b"l", "artifacts": {"index": b"\x00\xff"}}}}
PART_PINS = {"expected_pointer_sha256": sha(PARTS["pointer_bytes"]), "policy_sha256": sha(PARTS["policy_bytes"]),
             "roster_sha256": sha(PARTS["roster_bytes"]),
             "expected_public_policy_sha256": sha(PARTS["public_policy_bytes"]),
             "expected_source_sha256": "5" * 64, "expected_encoder_sha256": "6" * 64}
QUERY_TIME = json.loads(QUERY)["time"]


def test_snapshot_fixture_does_not_import_native_checkpoint(monkeypatch):
    """The metadata-only graph must work without loading a native backend."""
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        blocked = (name == "faiss" or name.endswith(".faiss_store")
                   or name.endswith(".test_verified_snapshot_checkpoint")
                   or (name == "sandbox.mind.step2"
                       and "test_verified_snapshot_checkpoint" in (fromlist or ())))
        if blocked:
            raise AssertionError("synthetic fixture imported native checkpoint")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    assert snapshot_case(monkeypatch)["resolved"]


def snapshot_case(monkeypatch):
    """A pure synthetic pointer/memory/projection graph and its resolved hits."""
    import importlib.util

    from sandbox.mind.step2 import shared_result_resolver as resolver
    spec = importlib.util.spec_from_file_location(
        "fixed_result_test_helpers_snapshot", Path(__file__).with_name("test_wd_q4_fixed_result_adapter.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    args = helper.make_case(canonical_roster=True)
    projection = json.loads(args["active_artifact_bytes"]["projection.json"])
    hits = [{"ref_sha256": sha(r["provenance"]["event_ref"].encode()), "doc_id_sha256": sha(r["record_id"].encode()),
             "ref_kind": r["provenance"]["ref_kind"], "canonical_event": False} for r in projection["records"]]
    resolved = resolver.resolve_results(
        memory_manifest=args["active_artifact_bytes"]["memory.json"],
        expected_memory_sha256=json.loads(args["pointer_bytes"])["artifacts"]["memory_sha256"],
        projection_bytes=args["active_artifact_bytes"]["projection.json"], results=hits, query_utc=QUERY_TIME,
        approved_tasks=args["approved_tasks"], policy_bytes=args["policy_bytes"], policy_sha256=args["policy_sha256"],
        roster_bytes=args["roster_bytes"], roster_sha256=args["roster_sha256"])
    assert resolved
    return {"parts": {name: args[name] for name in PARTS}, "pins": {name: args[name] for name in transport._PUBLIC_PINS},
            "tasks": sorted(args["approved_tasks"]), "resolved": resolved}


def synthetic_runtime(base, stub=STUB, namespace=True):
    from sandbox.mind.step2 import runtime_builder
    root = base / "runtime"
    step2 = root / "assets" / "code" / "sandbox" / "mind" / "step2"
    step2.mkdir(parents=True)
    if not namespace:
        for package in (step2.parents[1], step2.parent, step2):
            (package / "__init__.py").write_bytes(b"")
    (step2 / "shared_index_checkpoint.py").write_bytes(stub.encode("ascii"))
    (root / "site").mkdir()
    (root / "site" / "synthetic_site.txt").write_bytes(b"site")
    (root / "model").mkdir()
    (root / "model" / "synthetic_model.bin").write_bytes(b"not a model")
    manifest = base / "runtime-manifest.json"
    result = runtime_builder.build_runtime_manifest(root, manifest, generation=GENERATION, revision=REVISION)
    return root, manifest, result["manifest_sha256"]


def public_plan(root, manifest, manifest_sha, bundle, pins=None, tasks=None, **changes):
    value = {"schema": "wd.q4-fixed-plan.v2", "backend": "public-snapshot-v1",
             "interpreter_sha256": interpreter_sha(), "peer_code_sha256": sha(PEER.read_bytes()),
             "runtime_manifest_sha256": manifest_sha, "bundle_sha256": sha(bundle), "bundle_bytes": len(bundle),
             "verifier_sha256": sha(VERIFIER.read_bytes()), "runtime_root": str(root),
             "runtime_manifest_path": str(manifest), "runtime_generation": GENERATION, "runtime_revision": REVISION,
             "approved_tasks": tasks or ["task-a", "task-b"], **(pins or PART_PINS)}
    value.update(changes)
    return canonical(value)


def public_call(tmp_path, enabled=True, case=None, **changes):
    parts = case["parts"] if case else PARTS
    root, manifest, manifest_sha = changes.pop("runtime", None) or synthetic_runtime(tmp_path)
    bundle = changes.pop("bundle", None) or transport.fixed_snapshot_bundle(**parts)
    plan_bytes = changes.pop("plan_bytes", None) or public_plan(
        root, manifest, manifest_sha, bundle, pins=case and case["pins"], tasks=case and case["tasks"])
    kwargs = dict(executable=sys.executable, expected_interpreter_sha256=interpreter_sha(),
                  interpreter_max_bytes=64 * 1024 * 1024, plan_bytes=plan_bytes, expected_plan_sha256=sha(plan_bytes),
                  bundle_bytes=bundle, runtime_manifest_bytes=manifest.read_bytes(), query_bytes=QUERY,
                  cwd=str(tmp_path), timeout_seconds=60, max_output_bytes=65536, public_snapshot_enabled=enabled)
    kwargs.update(changes)
    return transport.run_fixed_peer(**kwargs)


def test_snapshot_bundle_codec_is_closed_and_roundtrips():
    raw = transport.fixed_snapshot_bundle(**PARTS)
    assert transport._fixed_bundle_decode(raw) == PARTS
    document = json.loads(raw)
    assert document["schema"] == "wd.q4-fixed-snapshot-bundle.v1" and canonical(document) == raw
    for broken in (canonical({**document, "extra": "eA=="}), raw.replace(b'"schema"', b'"schemb"'),
                   canonical({**document, "pointer": "not base64!"}), canonical({**document, "pointer": "eA"}),
                   json.dumps(document, indent=1).encode(), b"[]"):
        with pytest.raises(transport._Refused):
            transport._fixed_bundle_decode(broken)


def test_public_plan_is_closed_and_pins_absolute_runtime_paths(tmp_path):
    root, manifest = tmp_path / "rt", tmp_path / "m.json"
    good = public_plan(root, manifest, "7" * 64, b"x")
    assert transport._fixed_plan(good)["backend"] == "public-snapshot-v1"
    document = json.loads(good)
    for change in ({"runtime_root": "relative/rt"}, {"runtime_manifest_path": "m.json"},
                   {"runtime_generation": "A" * 40}, {"runtime_revision": "b" * 39}, {"verifier_sha256": "x"},
                   {"approved_tasks": []}, {"approved_tasks": ["b", "a"]}, {"approved_tasks": ["a", "a"]},
                   {"schema": "wd.q4-fixed-plan.v1"}, {"backend": "synthetic-v1"}, {"extra": 1}):
        with pytest.raises(transport._Refused):
            transport._fixed_plan(canonical({**document, **change}))
    with pytest.raises(transport._Refused):
        transport._fixed_plan(canonical({k: v for k, v in document.items() if k != "expected_encoder_sha256"}))
    with pytest.raises(transport._Refused):  # the synthetic plan schema cannot name the public backend
        transport._fixed_plan(plan(backend="public-snapshot-v1"))


@pytest.mark.parametrize("change", ["disabled", "verifier_pin", "manifest_bytes", "not_bool"])
def test_public_backend_is_default_off_and_pinned_before_any_spawn(tmp_path, monkeypatch, change):
    monkeypatch.setattr(transport, "_run", lambda *a, **kw: pytest.fail("spawned before public pins were checked"))
    runtime = synthetic_runtime(tmp_path)
    bundle = transport.fixed_snapshot_bundle(**PARTS)
    kwargs = {"runtime": runtime, "bundle": bundle}
    if change == "disabled":
        kwargs["enabled"] = False
    elif change == "not_bool":
        kwargs["enabled"] = 1
    elif change == "verifier_pin":
        kwargs["plan_bytes"] = public_plan(*runtime, bundle, verifier_sha256="8" * 64)
    else:
        kwargs["runtime_manifest_bytes"] = runtime[1].read_bytes() + b" "
    with pytest.raises(transport._Refused):
        public_call(tmp_path, **kwargs)


def test_public_flag_must_be_a_bool_even_for_the_synthetic_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "_run", lambda *a, **kw: pytest.fail("spawned"))
    with pytest.raises(transport._Refused):
        call(tmp_path, public_snapshot_enabled="yes")


def test_child_verifies_and_imports_the_runtime_before_ready_or_any_bundle(tmp_path, monkeypatch):
    root, manifest, manifest_sha = synthetic_runtime(tmp_path)
    bundle = transport.fixed_snapshot_bundle(**PARTS)
    plan_bytes = public_plan(root, manifest, manifest_sha, bundle)
    order = []
    def prepare(plan, peer_path):
        order.append(("prepare", plan["backend"], peer_path))
        return lambda plan_, plan_bytes_, bundle_, query_: transport._canonical({"status": "SYNTHETIC-PREPARED"})
    monkeypatch.setattr(transport, "_fixed_child_backend", prepare)
    parent = _ScriptedParent([plan_bytes, canonical({"schema": "wd.q4-fixed-challenge.v1", "nonce": "c" * 32}),
                              bundle, QUERY])
    original_write = parent.write_frame
    parent.write_frame = lambda data: (order.append(("write", json.loads(data)["status"])), original_write(data))
    raw = transport._fixed_child_protocol(parent.read_frame, parent.write_frame, sha(PEER.read_bytes()),
                                          peer_path=str(PEER))
    # v5: the child output is bound to the challenge nonce and the exact query it read
    assert transport._fixed_unwrap("c" * 32, QUERY, raw, 4096) == transport._canonical({"status": "SYNTHETIC-PREPARED"})
    assert order == [("prepare", "public-snapshot-v1", str(PEER)), ("write", "READY"), ("write", "BUNDLE_VERIFIED")]


def test_child_failed_runtime_preparation_writes_no_ready_and_reads_no_bundle(tmp_path, monkeypatch):
    root, manifest, manifest_sha = synthetic_runtime(tmp_path)
    bundle = transport.fixed_snapshot_bundle(**PARTS)
    def prepare(plan, peer_path):
        raise ValueError("runtime_verification_failed")
    monkeypatch.setattr(transport, "_fixed_child_backend", prepare)
    parent = _ScriptedParent([public_plan(root, manifest, manifest_sha, bundle)])
    with pytest.raises(transport._Refused):
        transport._fixed_child_protocol(parent.read_frame, parent.write_frame, sha(PEER.read_bytes()),
                                        peer_path=str(PEER))
    assert parent.events == [("read", transport._MAX_PLAN)]


@windows
def test_public_backend_roundtrip_returns_parent_reresolved_projection_text(tmp_path, monkeypatch):
    case = snapshot_case(monkeypatch)
    output, code = public_call(tmp_path, case=case, runtime=synthetic_runtime(tmp_path, make_stub(case["resolved"])))
    assert code == 0
    assert json.loads(output) == {"schema": "wd.q4-fixed-public-result.v1", "status": "ANSWER",
                                  "results": case["resolved"], "source_authentication": "UNKNOWN",
                                  "freshness": "UNKNOWN", "runtime": "MANIFEST_VERIFIED_BEFORE_IMPORT_NOT_ISOLATED",
                                  "validation": "PARENT_RERESOLVED_AGAINST_PINNED_PROJECTION"}
    assert sha(QUERY) not in output.decode("ascii") and json.loads(QUERY)["situation"] not in output.decode("ascii")


@windows
def test_public_backend_roundtrip_with_real_tree_namespace_layout(tmp_path, monkeypatch):
    # Production has no __init__.py in sandbox, sandbox/mind, or sandbox/mind/step2.
    case = snapshot_case(monkeypatch)
    runtime = synthetic_runtime(tmp_path, make_stub(case["resolved"]), namespace=True)
    output, code = public_call(tmp_path, case=case, runtime=runtime)
    assert code == 0 and json.loads(output)["results"] == case["resolved"]


@windows
@pytest.mark.parametrize("mode", ["echo", "unknown", "extra"])
def test_malicious_runtime_answers_are_refused_by_the_parent(tmp_path, monkeypatch, mode):
    case = snapshot_case(monkeypatch)
    with pytest.raises(transport._Refused):
        public_call(tmp_path, case=case, runtime=synthetic_runtime(tmp_path, make_stub(case["resolved"], mode=mode)))


@windows
def test_runtime_module_imported_from_outside_the_verified_tree_is_refused(tmp_path, monkeypatch):
    case = snapshot_case(monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "planted_outside.py").write_bytes(b"VALUE = 1\n")
    stub = make_stub(case["resolved"], outside=outside)
    with pytest.raises(transport._Refused):
        public_call(tmp_path, case=case, runtime=synthetic_runtime(tmp_path, stub))


@windows
@pytest.mark.parametrize("tamper", ["changed_stub", "extra_file", "manifest_generation"])
def test_public_backend_refuses_a_runtime_that_does_not_match_the_pinned_manifest(tmp_path, monkeypatch, tamper):
    case = snapshot_case(monkeypatch)
    runtime = synthetic_runtime(tmp_path, make_stub(case["resolved"]))
    root, manifest, manifest_sha = runtime
    changes = {"runtime": runtime, "case": case}
    stub = root / "assets" / "code" / "sandbox" / "mind" / "step2" / "shared_index_checkpoint.py"
    if tamper == "changed_stub":
        stub.write_bytes(stub.read_bytes().replace(b'MODE = "good"', b'MODE = "echo"'))
    elif tamper == "extra_file":
        (root / "site" / "planted.py").write_bytes(b"raise SystemExit(0)")
    else:
        bundle = transport.fixed_snapshot_bundle(**case["parts"])
        changes.update(bundle=bundle, plan_bytes=public_plan(root, manifest, manifest_sha, bundle, pins=case["pins"],
                                                             tasks=case["tasks"], runtime_generation="c" * 40))
    with pytest.raises(transport._Refused):
        public_call(tmp_path, **changes)


# --- v5 parent validation: closed answer, re-resolution against the pinned projection in the bundle ------------

def _validation(tmp_path, monkeypatch):
    case = snapshot_case(monkeypatch)
    bundle = transport.fixed_snapshot_bundle(**case["parts"])
    plan_bytes = public_plan(tmp_path / "rt", tmp_path / "m.json", "7" * 64, bundle, pins=case["pins"],
                             tasks=case["tasks"])
    return case, plan_bytes, bundle


def _child_answer(plan_bytes, bundle, results, status="ANSWER", **changes):
    value = {"schema": "wd.q4-fixed-public-answer.v1", "status": status, "results": results,
             "plan_sha256": sha(plan_bytes), "bundle_sha256": sha(bundle), "backend": "public-snapshot-v1",
             "source_authentication": "UNKNOWN", "freshness": "UNKNOWN",
             "runtime": "MANIFEST_VERIFIED_BEFORE_IMPORT_NOT_ISOLATED"}
    value.update(changes)
    return canonical(value)


def test_parent_validation_returns_only_projection_text_for_known_pairs(tmp_path, monkeypatch):
    case, plan_bytes, bundle = _validation(tmp_path, monkeypatch)
    out = transport._fixed_public_validate(plan_bytes, bundle, QUERY, _child_answer(plan_bytes, bundle, case["resolved"]))
    assert json.loads(out)["results"] == case["resolved"]
    abstain = transport._fixed_public_validate(plan_bytes, bundle, QUERY,
                                               _child_answer(plan_bytes, bundle, [], status="ABSTAIN"))
    assert json.loads(abstain)["status"] == "ABSTAIN" and json.loads(abstain)["results"] == []


def _bad_answers(case, plan_bytes, bundle):
    good = case["resolved"]
    first = good[0]
    return {
        "query_echo": _child_answer(plan_bytes, bundle, [dict(first, stored_text=QUERY.decode())]),
        "changed_text": _child_answer(plan_bytes, bundle, [dict(first, stored_text=first["stored_text"] + " ")]),
        "unknown_pair": _child_answer(plan_bytes, bundle, [dict(first, ref_sha256="0" * 64)]),
        "swapped_pair": _child_answer(plan_bytes, bundle, [dict(first, doc_id_sha256=first["ref_sha256"])]),
        "extra_key": _child_answer(plan_bytes, bundle, [dict(first, situation="x")]),
        "missing_key": _child_answer(plan_bytes, bundle, [{k: v for k, v in first.items() if k != "stored_text"}]),
        "canonical_true": _child_answer(plan_bytes, bundle, [dict(first, canonical_event=True)]),
        "duplicate": _child_answer(plan_bytes, bundle, [first, first]),
        "answer_empty": _child_answer(plan_bytes, bundle, []),
        "abstain_with_hits": _child_answer(plan_bytes, bundle, good, status="ABSTAIN"),
        "unknown_status": _child_answer(plan_bytes, bundle, good, status="OFF"),
        "plan_digest": _child_answer(plan_bytes, bundle, good, plan_sha256="0" * 64),
        "bundle_digest": _child_answer(plan_bytes, bundle, good, bundle_sha256="0" * 64),
        "schema": _child_answer(plan_bytes, bundle, good, schema="wd.q4-fixed-public-answer.v0"),
        "authenticated_claim": _child_answer(plan_bytes, bundle, good, source_authentication="VERIFIED"),
        "extra_top_key": _child_answer(plan_bytes, bundle, good, query=QUERY.decode()),
        "noncanonical": json.dumps(json.loads(_child_answer(plan_bytes, bundle, good)), indent=1).encode(),
        "not_json": b"\xff",
    }


@pytest.mark.parametrize("name", ["query_echo", "changed_text", "unknown_pair", "swapped_pair", "extra_key",
                                  "missing_key", "canonical_true", "duplicate", "answer_empty", "abstain_with_hits",
                                  "unknown_status", "plan_digest", "bundle_digest", "schema", "authenticated_claim",
                                  "extra_top_key", "noncanonical", "not_json"])
def test_parent_validation_refuses_malicious_or_malformed_child_answers(tmp_path, monkeypatch, name):
    case, plan_bytes, bundle = _validation(tmp_path, monkeypatch)
    with pytest.raises(transport._Refused) as caught:
        transport._fixed_public_validate(plan_bytes, bundle, QUERY, _bad_answers(case, plan_bytes, bundle)[name])
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_parent_validation_refuses_a_bundle_whose_pointer_does_not_match_the_plan(tmp_path, monkeypatch):
    case, plan_bytes, bundle = _validation(tmp_path, monkeypatch)
    other = transport.fixed_snapshot_bundle(**dict(case["parts"], pointer_bytes=case["parts"]["pointer_bytes"] + b" "))
    with pytest.raises(transport._Refused):
        transport._fixed_public_validate(plan_bytes, other, QUERY, _child_answer(plan_bytes, other, case["resolved"]))


def test_closed_result_items_are_checked_on_both_sides():
    item = {"ref_sha256": "1" * 64, "doc_id_sha256": "2" * 64, "stored_text": "t",
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}
    assert transport._fixed_closed_results([item]) == [item]
    for bad in ([dict(item, extra=1)], [{k: v for k, v in item.items() if k != "ref_kind"}],
                [dict(item, ref_sha256="X" * 64)], [dict(item, stored_text=1)], [dict(item, ref_kind="other")],
                [dict(item, canonical_event=0)], "not a list", [item] * 101):
        with pytest.raises(transport._Refused):
            transport._fixed_closed_results(bad)


# --- v5: process-scoped output containment before verifier/runtime/backend execution --------------------------

CONTAIN_SCRIPT = r'''
import ctypes, json, os, sys
from ctypes import wintypes as W
sys.path.insert(0, sys.argv[1])
report_path = sys.argv[2]
os.write(1, b"CONTROL-BEFORE\n")
from tools import wd_q4_secure_transport as transport
transport._contain_child_output()
facts = {}
print("LEAK-PY-PRINT")
sys.stderr.write("LEAK-SYS-STDERR\n")
sys.__stdout__.write("LEAK-DUNDER-STDOUT\n")
facts["os_write"] = [os.write(1, b"LEAK-OSWRITE-1\n"), os.write(2, b"LEAK-OSWRITE-2\n")]
ucrt = ctypes.CDLL("ucrtbase")
facts["crt_write"] = [ucrt._write(1, b"LEAK-CRT-WRITE-1\n", 17), ucrt._write(2, b"LEAK-CRT-WRITE-2\n", 17)]
ucrt.puts(b"LEAK-CRT-PUTS")
ucrt.fflush(None)
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.GetStdHandle.restype = W.HANDLE
k.GetStdHandle.argtypes = [W.DWORD]
k.WriteFile.argtypes = [W.HANDLE, ctypes.c_char_p, W.DWORD, ctypes.POINTER(W.DWORD), ctypes.c_void_p]
k.GetFileType.argtypes = [W.HANDLE]
written, ok = W.DWORD(), []
for std in (-11 & 0xFFFFFFFF, -12 & 0xFFFFFFFF):
    ok.append(bool(k.WriteFile(k.GetStdHandle(std), b"LEAK-WIN32\n", 11, ctypes.byref(written), None)))
facts["win32_write_ok"] = ok
facts["std_types"] = [k.GetFileType(k.GetStdHandle(s & 0xFFFFFFFF)) for s in (-10, -11, -12)]
facts["std_names"] = [transport._nt_object_name(k.GetStdHandle(s & 0xFFFFFFFF)) for s in (-10, -11, -12)]
k.CreateFileW.restype = W.HANDLE
k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, ctypes.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
console = k.CreateFileW("CONOUT$", 0x40000000, 3, None, 3, 0, None)
facts["conout_opened"] = console not in (None, ctypes.c_void_p(-1).value)
with open(report_path, "w", encoding="ascii") as report:
    json.dump(facts, report)
'''


@windows
def test_containment_silences_python_crt_and_win32_standard_channels(tmp_path):
    import subprocess
    script, report = tmp_path / "contain.py", tmp_path / "report.json"
    script.write_text(CONTAIN_SCRIPT, encoding="ascii")
    done = subprocess.run([sys.executable, "-I", "-S", "-B", str(script), str(PEER.parents[1]), str(report)],
                          capture_output=True, timeout=60, cwd=str(tmp_path))
    assert done.returncode == 0
    assert done.stdout == b"CONTROL-BEFORE\n"  # positive control: the channel was captured before containment
    assert done.stderr == b""
    facts = json.loads(report.read_text(encoding="ascii"))
    assert facts["os_write"] == [15, 15] and facts["crt_write"] == [17, 17]  # writes succeed into NUL
    assert facts["win32_write_ok"] == [True, True]
    assert facts["std_types"] == [2, 2, 2]  # FILE_TYPE_CHAR (NUL) for stdin, stdout and stderr
    assert facts["std_names"] == ["\\Device\\Null"] * 3  # the exact NUL device, not just any character device
    assert facts["conout_opened"] is False  # console detached: CONOUT$ cannot be reopened


def test_containment_is_the_first_step_of_the_public_preparation(tmp_path, monkeypatch):
    root, manifest, manifest_sha = synthetic_runtime(tmp_path)
    plan_value = transport._fixed_plan(public_plan(root, manifest, manifest_sha, b"x"))
    calls = []
    monkeypatch.setattr(transport, "_contain_child_output", lambda: calls.append("contain"))
    monkeypatch.setattr(transport, "open", lambda *a, **kw: calls.append("open") or pytest.fail("read"), raising=False)
    with pytest.raises(transport._Refused):  # pytest itself is not -I -S -B, so the flags check refuses next
        transport._fixed_child_backend(plan_value, str(PEER))
    assert calls == ["contain"]


def test_failed_containment_is_a_fixed_refusal_before_any_verifier_read(tmp_path, monkeypatch):
    root, manifest, manifest_sha = synthetic_runtime(tmp_path)
    bundle = transport.fixed_snapshot_bundle(**PARTS)
    def broken():
        raise OSError("nul unavailable")
    monkeypatch.setattr(transport, "_contain_child_output", broken)
    parent = _ScriptedParent([public_plan(root, manifest, manifest_sha, bundle)])
    with pytest.raises(transport._Refused) as caught:
        transport._fixed_child_protocol(parent.read_frame, parent.write_frame, sha(PEER.read_bytes()),
                                        peer_path=str(PEER))
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert parent.events == [("read", transport._MAX_PLAN)]


def test_synthetic_backend_does_not_touch_process_streams(monkeypatch):
    monkeypatch.setattr(transport, "_contain_child_output", lambda: pytest.fail("synthetic path contained"))
    assert transport._fixed_child_backend(transport._fixed_plan(plan()), None) is transport._fixed_child_answer


@windows
def test_public_backend_runtime_code_runs_with_contained_streams(tmp_path, monkeypatch):
    # The stub answers ABSTAIN unless, at import time, stdin/stdout/stderr are NUL, CONOUT$ cannot be opened and
    # Python's streams are the shared NUL sink; the parent then refuses nothing and the status shows the facts.
    case = snapshot_case(monkeypatch)
    output, code = public_call(tmp_path, case=case,
                               runtime=synthetic_runtime(tmp_path, make_stub(case["resolved"], noisy=True)))
    assert code == 0 and json.loads(output)["status"] == "ANSWER"
    assert b"IMPORT-NOISE" not in output


# --- v6: the parent enforces the pinned public policy, source and encoder before re-resolution ---------------

POLICY_SCHEMA = "wd.mind-public-status-policy.v1"
# Each policy is valid and correctly pinned, and every projected record stays APPROVED (resolve_results accepts
# the pairs, see snapshot_case); the records are simply outside the public boundary the plan pins.
NONPUBLIC = {
    "task_not_public": {"schema": POLICY_SCHEMA, "public_tasks": ["private-task"], "public_types": ["message"]},
    "type_not_public": {"schema": POLICY_SCHEMA, "public_tasks": ["public-task"], "public_types": ["status"]},
}


def _narrowed(case, policy):
    raw = canonical(policy)
    return dict(case, parts=dict(case["parts"], public_policy_bytes=raw),
                pins=dict(case["pins"], expected_public_policy_sha256=sha(raw)))


def _validation_for(tmp_path, case):
    bundle = transport.fixed_snapshot_bundle(**case["parts"])
    plan_bytes = public_plan(tmp_path / "rt", tmp_path / "m.json", "7" * 64, bundle, pins=case["pins"],
                             tasks=case["tasks"])
    return plan_bytes, bundle


def _refused_without_text(case, plan_bytes, bundle):
    with pytest.raises(transport._Refused) as caught:
        transport._fixed_public_validate(plan_bytes, bundle, QUERY,
                                         _child_answer(plan_bytes, bundle, case["resolved"]))
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    shown = str(caught.value) + repr(caught.value) + repr(caught.value.args)
    assert all(item["stored_text"] not in shown for item in case["resolved"])


@pytest.mark.parametrize("name", sorted(NONPUBLIC))
def test_parent_refuses_approved_but_nonpublic_records_in_a_forged_answer(tmp_path, monkeypatch, name):
    case = _narrowed(snapshot_case(monkeypatch), NONPUBLIC[name])
    _refused_without_text(case, *_validation_for(tmp_path, case))


@pytest.mark.parametrize("pin", ["expected_public_policy_sha256", "expected_source_sha256",
                                 "expected_encoder_sha256"])
def test_parent_binds_public_policy_source_and_encoder_pins(tmp_path, monkeypatch, pin):
    case = snapshot_case(monkeypatch)
    case = dict(case, pins=dict(case["pins"], **{pin: "0" * 64}))
    _refused_without_text(case, *_validation_for(tmp_path, case))


@windows
def test_real_spawn_answer_over_nonpublic_records_is_refused_by_the_parent(tmp_path, monkeypatch):
    # The stub's own bound check passes (its policy pin matches the narrowed bytes), so only the parent can refuse.
    case = _narrowed(snapshot_case(monkeypatch), NONPUBLIC["task_not_public"])
    with pytest.raises(transport._Refused) as caught:
        public_call(tmp_path, case=case, runtime=synthetic_runtime(tmp_path, make_stub(case["resolved"])))
    assert caught.value.__context__ is None and caught.value.__cause__ is None


NAME_SCRIPT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from tools import wd_q4_secure_transport as transport
seen = []
real = getattr(transport, "_nt_object_name", None)
def lying(handle):
    seen.append(real(handle) if real else None)
    return "\\Device\\ConDrv"
transport._nt_object_name = lying
try:
    transport._contain_child_output()
    outcome = "contained"
except transport._Refused as refused:
    outcome = "refused" if refused.__cause__ is None and refused.__context__ is None else "chained"
with open(sys.argv[2], "w", encoding="ascii") as report:
    json.dump({"outcome": outcome, "seen": seen}, report)
'''


@windows
def test_containment_refuses_a_character_device_that_is_not_exactly_device_null(tmp_path):
    import subprocess
    script, report = tmp_path / "name.py", tmp_path / "report.json"
    script.write_text(NAME_SCRIPT, encoding="ascii")
    done = subprocess.run([sys.executable, "-I", "-S", "-B", str(script), str(PEER.parents[1]), str(report)],
                          capture_output=True, timeout=60, cwd=str(tmp_path))
    assert done.returncode == 0
    facts = json.loads(report.read_text(encoding="ascii"))
    assert facts["outcome"] == "refused"
    assert facts["seen"][:1] == ["\\Device\\Null"]  # the real name of the opened handle was queried first


def test_module_origins_accept_only_the_interpreter_stdlib_and_dlls(monkeypatch):
    import types
    roots = transport._interpreter_roots()
    base = Path(sys.base_prefix)
    before = set(sys.modules)
    module = types.ModuleType("wd_planted_origin")
    monkeypatch.setitem(sys.modules, "wd_planted_origin", module)
    for planted in (base / "Lib" / "site-packages" / "planted.py", base / "planted.py",
                    base / "Scripts" / "planted.py", base / "Lib" / "site-packages" / "pkg" / "__init__.py"):
        module.__file__ = str(planted)
        with pytest.raises(transport._Refused):
            transport._module_origins(before, roots)
    for allowed in (base / "Lib" / "json" / "__init__.py", base / "DLLs" / "_ctypes.pyd"):
        module.__file__ = str(allowed)
        transport._module_origins(before, roots)


@pytest.mark.parametrize("name", ["sandbox", "sandbox.mind", "sandbox.mind.step2"])
@pytest.mark.parametrize("variant", ["good", "foreign", "extra", "parent", "forged_loader", "forged_path",
                                     "forged_name", "forged_origin", "forged_file", "missing", "regular_package"])
def test_namespace_origins_accept_only_the_exact_verified_code_directories(tmp_path, monkeypatch, name, variant):
    import importlib.machinery
    import types

    root = tmp_path / "code"
    expected = root.joinpath(*name.split("."))
    expected.mkdir(parents=True)
    before = set(sys.modules) - {name}
    locations = [str(expected)]
    loader = importlib.machinery.NamespaceLoader(name, locations, None)
    spec = importlib.machinery.ModuleSpec(name, loader, is_package=True)
    spec.submodule_search_locations = locations
    module = types.ModuleType(name)
    module.__file__ = None
    module.__spec__ = spec
    module.__loader__ = loader
    module.__path__ = locations
    if variant == "foreign":
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        locations[:] = [str(foreign)]
    elif variant == "extra":
        locations.append(str(tmp_path))
    elif variant == "parent":
        locations[:] = [str(expected.parent)]
    elif variant == "forged_loader":
        spec.loader = object()
        module.__loader__ = spec.loader
    elif variant == "forged_path":
        module.__path__ = list(locations)
    elif variant == "forged_name":
        spec.name = "another"
    elif variant == "forged_origin":
        spec.origin = "built-in"
    elif variant == "forged_file":
        module.__file__ = str(expected / "forged.py")
    elif variant == "missing":
        locations[:] = [str(expected / "missing")]
    elif variant == "regular_package":
        (expected / "__init__.py").write_bytes(b"")
    monkeypatch.setitem(sys.modules, name, module)
    if variant == "good":
        transport._module_origins(before, [str(root)], root)
    else:
        with pytest.raises(transport._Refused):
            transport._module_origins(before, [str(root)], root)


def test_unknown_namespace_remains_refused(tmp_path, monkeypatch):
    import importlib.machinery
    import types

    name = "unexpected_namespace"
    before = set(sys.modules) - {name}
    paths = [str(tmp_path)]
    loader = importlib.machinery.NamespaceLoader(name, paths, None)
    spec = importlib.machinery.ModuleSpec(name, loader, is_package=True)
    spec.submodule_search_locations = paths
    module = types.ModuleType(name)
    module.__spec__, module.__loader__, module.__path__ = spec, loader, paths
    monkeypatch.setitem(sys.modules, name, module)
    with pytest.raises(transport._Refused):
        transport._module_origins(before, [str(tmp_path)], tmp_path)


@pytest.mark.parametrize("name", ["sandbox", "sandbox.mind", "sandbox.mind.step2"])
@pytest.mark.parametrize("form", ["module", "package"])
def test_named_packages_refuse_real_regular_modules_from_another_allowed_root(tmp_path, monkeypatch, name, form):
    import importlib.util

    code = tmp_path / "code"
    code.joinpath(*name.split(".")).mkdir(parents=True)
    site = tmp_path / "site"
    target = site.joinpath(*name.split("."))
    if form == "module":
        target.parent.mkdir(parents=True)
        source = target.with_suffix(".py")
    else:
        target.mkdir(parents=True)
        source = target / "__init__.py"
    source.write_text("MARKER = 'synthetic'\n", encoding="ascii")
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    before = set(sys.modules) - {name}
    monkeypatch.setitem(sys.modules, name, module)
    roots = [os.path.normcase(os.path.realpath(path)) for path in (code, site)]
    with pytest.raises(transport._Refused):
        transport._module_origins(before, roots, code)


@windows
@pytest.mark.parametrize("failure", ["complete", "locked-file-missing", "dependency-before-ready", "dependency-after-query"])
def test_synthetic_locked_child_runtime_missing_dependency_fails_closed(tmp_path, monkeypatch, failure):
    """Real synthetic peer; no real model, installed bundle, or live query."""
    case = snapshot_case(monkeypatch)
    stub = make_stub(case["resolved"])
    missing = "wd_q4_nonexistent_synthetic_dependency_37ff"
    if failure == "dependency-before-ready":
        stub = "import " + missing + "\n" + stub
    elif failure == "dependency-after-query":
        marker = "    sha = lambda data: hashlib.sha256(data).hexdigest()"
        assert marker in stub
        stub = stub.replace(marker, "    import " + missing + "\n" + marker, 1)
    runtime = synthetic_runtime(tmp_path, stub)
    root, manifest, manifest_sha = runtime
    if failure == "locked-file-missing":
        # This file belongs to the new synthetic test runtime and is pinned by its manifest.
        (root / "site" / "synthetic_site.txt").unlink()
    bundle = transport.fixed_snapshot_bundle(**case["parts"])
    written = []
    original = transport._frame_write
    def observe(k, pipe, data, deadline):
        if data == bundle:
            written.append("bundle")
        if data == QUERY:
            written.append("query")
        return original(k, pipe, data, deadline)
    monkeypatch.setattr(transport, "_frame_write", observe)
    if failure == "complete":
        output, code = public_call(tmp_path, case=case, runtime=runtime, bundle=bundle)
        assert code == 0 and json.loads(output)["results"] == case["resolved"]
        assert written == ["bundle", "query"]
        return
    with pytest.raises(transport._Refused) as caught:
        public_call(tmp_path, case=case, runtime=runtime, bundle=bundle)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert missing not in str(caught.value) and QUERY.decode() not in str(caught.value)
    if failure in ("locked-file-missing", "dependency-before-ready"):
        assert written == []
    else:
        # A lazily missing dependency is discovered only after the query was sent.
        assert written == ["bundle", "query"]
