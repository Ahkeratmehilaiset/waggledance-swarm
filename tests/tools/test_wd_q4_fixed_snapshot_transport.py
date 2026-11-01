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
    assert json.loads(raw)["query"] == "RECEIVED"


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
    for phrase in ("not authentication", "runtime", "NOT verified", "synthetic", "before spawning",
                   "before any bundle", "bundle ACK", "real backend is unavailable"):
        assert phrase in doc


# --- v4: default-off public-snapshot backend behind an externally pinned runtime manifest ---------------------
# Synthetic runtime tree only: its snapshot module is a STUB that stands in for query_public_snapshot, so these
# tests prove the loader order and the wiring, never a real model, index or Q4 result.

VERIFIER = PEER.parents[1] / "sandbox" / "mind" / "step2" / "runtime_manifest.py"
GENERATION, REVISION = "a" * 40, "b" * 40
STUB = '''import hashlib
import os


def query_public_snapshot(*, pointer_bytes, expected_pointer_sha256, approved_tasks, policy_bytes, policy_sha256,
                          roster_bytes, roster_sha256, expected_public_policy_sha256, active_artifact_bytes,
                          cell_snapshots, history_bytes, public_policy_bytes, expected_source_sha256,
                          expected_encoder_sha256, query_bytes, model_dir, expected_runtime_revision, enabled):
    sha = lambda data: hashlib.sha256(data).hexdigest()
    bound = (enabled is True and sha(pointer_bytes) == expected_pointer_sha256 and sha(policy_bytes) == policy_sha256
             and sha(roster_bytes) == roster_sha256 and sha(public_policy_bytes) == expected_public_policy_sha256
             and type(query_bytes) is bytes and len(query_bytes) > 0 and type(approved_tasks) is frozenset)
    return {"status": "ANSWER" if bound else "ABSTAIN",
            "results": [{"stub": "synthetic-runtime", "tasks": sorted(approved_tasks),
                         "artifacts": sorted(active_artifact_bytes), "cells": sorted(cell_snapshots),
                         "history": sorted(history_bytes), "model_dir": os.path.basename(model_dir),
                         "revision": expected_runtime_revision, "source": expected_source_sha256,
                         "encoder": expected_encoder_sha256}],
            "source_authentication": "UNKNOWN", "freshness": "UNKNOWN"}
'''
PARTS = {"pointer_bytes": b"synthetic-pointer", "policy_bytes": b"synthetic-policy",
         "roster_bytes": b"synthetic-roster", "public_policy_bytes": b"synthetic-public-policy",
         "active_artifact_bytes": {"descriptor.json": b"d", "memory.json": b"m"},
         "history_bytes": {"q4-pointer-" + "1" * 64 + ".json": b"h"},
         "cell_snapshots": {"cell-a": {"pointer": b"p", "log": b"l", "artifacts": {"index": b"\x00\xff"}}}}


def synthetic_runtime(base, stub=STUB):
    from sandbox.mind.step2 import runtime_builder
    root = base / "runtime"
    step2 = root / "assets" / "code" / "sandbox" / "mind" / "step2"
    step2.mkdir(parents=True)
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


def public_plan(root, manifest, manifest_sha, bundle, **changes):
    value = {"schema": "wd.q4-fixed-plan.v2", "backend": "public-snapshot-v1",
             "interpreter_sha256": interpreter_sha(), "peer_code_sha256": sha(PEER.read_bytes()),
             "runtime_manifest_sha256": manifest_sha, "bundle_sha256": sha(bundle), "bundle_bytes": len(bundle),
             "verifier_sha256": sha(VERIFIER.read_bytes()), "runtime_root": str(root),
             "runtime_manifest_path": str(manifest), "runtime_generation": GENERATION, "runtime_revision": REVISION,
             "approved_tasks": ["task-a", "task-b"], "expected_pointer_sha256": sha(PARTS["pointer_bytes"]),
             "policy_sha256": sha(PARTS["policy_bytes"]), "roster_sha256": sha(PARTS["roster_bytes"]),
             "expected_public_policy_sha256": sha(PARTS["public_policy_bytes"]),
             "expected_source_sha256": "5" * 64, "expected_encoder_sha256": "6" * 64}
    value.update(changes)
    return canonical(value)


def public_call(tmp_path, enabled=True, **changes):
    root, manifest, manifest_sha = changes.pop("runtime", None) or synthetic_runtime(tmp_path)
    bundle = changes.pop("bundle", None) or transport.fixed_snapshot_bundle(**PARTS)
    plan_bytes = changes.pop("plan_bytes", None) or public_plan(root, manifest, manifest_sha, bundle)
    kwargs = dict(executable=sys.executable, expected_interpreter_sha256=interpreter_sha(),
                  interpreter_max_bytes=64 * 1024 * 1024, plan_bytes=plan_bytes, expected_plan_sha256=sha(plan_bytes),
                  bundle_bytes=bundle, runtime_manifest_bytes=manifest.read_bytes(), query_bytes=QUERY,
                  cwd=str(tmp_path), timeout_seconds=60, max_output_bytes=8192, public_snapshot_enabled=enabled)
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
    # The flag enables only the public backend; it never changes the synthetic plan path.
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
    assert raw == transport._canonical({"status": "SYNTHETIC-PREPARED"})
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
def test_public_backend_roundtrip_over_a_synthetic_verified_runtime(tmp_path):
    output, code = public_call(tmp_path)
    answer = json.loads(output)
    assert code == 0
    assert answer["schema"] == "wd.q4-fixed-public-answer.v1" and answer["status"] == "ANSWER"
    assert answer["source_authentication"] == "UNKNOWN" and answer["freshness"] == "UNKNOWN"
    assert answer["runtime"] == "MANIFEST_VERIFIED_BEFORE_IMPORT_NOT_ISOLATED"
    assert answer["results"] == [{"stub": "synthetic-runtime", "tasks": ["task-a", "task-b"],
                                  "artifacts": ["descriptor.json", "memory.json"], "cells": ["cell-a"],
                                  "history": ["q4-pointer-" + "1" * 64 + ".json"], "model_dir": "model",
                                  "revision": REVISION, "source": "5" * 64, "encoder": "6" * 64}]
    assert sha(QUERY) not in output.decode("ascii") and QUERY.decode() not in output.decode("ascii")


@windows
@pytest.mark.parametrize("tamper", ["changed_stub", "extra_file", "manifest_generation"])
def test_public_backend_refuses_a_runtime_that_does_not_match_the_pinned_manifest(tmp_path, tamper):
    root, manifest, manifest_sha = synthetic_runtime(tmp_path)
    runtime = (root, manifest, manifest_sha)
    bundle = transport.fixed_snapshot_bundle(**PARTS)
    changes = {"runtime": runtime, "bundle": bundle}
    stub = root / "assets" / "code" / "sandbox" / "mind" / "step2" / "shared_index_checkpoint.py"
    if tamper == "changed_stub":
        stub.write_bytes(stub.read_bytes().replace(b'"ANSWER" if bound', b'"ANSWER" if True'))
    elif tamper == "extra_file":
        (root / "site" / "planted.py").write_bytes(b"raise SystemExit(0)")
    else:
        changes["plan_bytes"] = public_plan(root, manifest, manifest_sha, bundle, runtime_generation="c" * 40)
    with pytest.raises(transport._Refused):
        public_call(tmp_path, **changes)
