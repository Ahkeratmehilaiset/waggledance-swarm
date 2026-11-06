"""Synthetic caller adapter tests: declared-consistent lane binding, never authentication.

Pure tests replace the existing chain entry (``query_session``) with a recorder; the one
``windows`` test runs the real synthetic fixed peer through the unpatched chain.
No live lane, bridge, model or sealed data is involved; the query canary is synthetic.
"""
import hashlib
import importlib
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import wd_q4_lane_session_client as session
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
from tools.wd_q4_secure_transport import TransportCleanupUnknown

CANARY = "synthetic-private-query-canary"
LANE = "codex-lead-1"
LANES = ("codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5")
UUIDS = {agent: "0000000%d-0000-4000-8000-000000000000" % (index + 1) for index, agent in enumerate(LANES)}
UUIDS["codex-tools-1"] = "00000009-0000-4000-8000-000000000000"
BINDING_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
                "public_policy_sha256", "descriptor_sha256", "projection_sha256")
TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
         "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}
windows = pytest.mark.skipif(os.name != "nt", reason="the real synthetic fixed peer needs the Windows transport")


def test_the_trust_shape_the_adapter_requires_is_the_chain_constant():
    from tools import wd_q4_lane_query
    assert adapter()._TRUST == wd_q4_lane_query._TRUST == TRUST


def adapter():
    return importlib.import_module("tools.wd_q4_lane_caller_adapter")


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


WORKTREES = {agent: r"C:\synthetic\lanes" + "\\" + agent for agent in LANES}
PROCESS_CWD = r"C:\synthetic\lanes\codex-lead-1\work"


def fleet_bytes(worktrees=None, drop_worktree=False):
    """The pinned fleet document: lanes carry the per-lane worktree as the real manifest does."""
    worktrees = dict(WORKTREES if worktrees is None else worktrees)
    lanes = []
    for agent in LANES:
        entry = {"agent": agent, "agent_uuid": UUIDS[agent]}
        if not drop_worktree:
            entry["worktree"] = worktrees[agent]
        lanes.append(entry)
    return canon({"lanes": lanes, "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": UUIDS["codex-tools-1"]}})


def synthetic_inputs():
    """The sixteen pinned chain inputs with self-consistent synthetic pins (never a real runtime)."""
    fleet = fleet_bytes()
    binding = canon({"schema": "wd.q4-lane-binding.v1", "synthetic": True})
    fixed_plan = canon({"backend": "public-snapshot-v1", "synthetic": True})
    bundle = b"synthetic-fixed-bundle"
    manifest = b"synthetic-runtime-manifest"
    return {"fleet_bytes": fleet, "expected_fleet_sha256": sha(fleet),
            "binding_bytes": binding, "expected_binding_sha256": sha(binding),
            "bundle_root": r"C:\synthetic\bundle", "public_plan_path": r"C:\synthetic\bundle\public-plan.json",
            "executable": r"C:\synthetic\python\python.exe", "expected_interpreter_sha256": "1" * 64,
            "interpreter_max_bytes": 64 * 1024 * 1024, "fixed_plan_bytes": fixed_plan,
            "expected_fixed_plan_sha256": sha(fixed_plan), "fixed_bundle_bytes": bundle,
            "runtime_manifest_bytes": manifest, "cwd": r"C:\synthetic\lanes\codex-lead-1\work",
            "timeout_seconds": 60, "max_output_bytes": 65536}


def descriptor_for(inputs, lanes=LANES, expected_binding=None):
    binding = expected_binding or {key: "2" * 64 for key in BINDING_KEYS}
    document = {"schema": "wd.q4-shared-session-config.v2", "fleet_sha256": inputs["expected_fleet_sha256"],
                "lanes": list(lanes), "bundle_root": inputs["bundle_root"], "public_plan": inputs["public_plan_path"],
                "interpreter": inputs["executable"], "cwd": inputs["cwd"],
                "binding_path": r"C:\synthetic\pins\binding.json", "fixed_plan_path": r"C:\synthetic\pins\fixed.json",
                "snapshot_path": r"C:\synthetic\pins\snapshot.bin",
                "runtime_manifest_path": r"C:\synthetic\runtime\manifest.json",
                "interpreter_sha256": inputs["expected_interpreter_sha256"],
                "binding_sha256": inputs["expected_binding_sha256"],
                "fixed_plan_sha256": inputs["expected_fixed_plan_sha256"],
                "snapshot_sha256": sha(inputs["fixed_bundle_bytes"]),
                "runtime_manifest_sha256": sha(inputs["runtime_manifest_bytes"]),
                "interpreter_max_bytes": inputs["interpreter_max_bytes"], "timeout_seconds": inputs["timeout_seconds"],
                "max_output_bytes": inputs["max_output_bytes"],
                "outer": {"outer-path": r"C:\synthetic\runtime\outer.py", "outer-sha256": "3" * 64,
                          "runtime-root": r"C:\synthetic\runtime", "runtime-generation": "a" * 40,
                          "runtime-revision": "b" * 40, "verifier-sha256": "4" * 64, "public-outer-sha256": "5" * 64,
                          "public-child-sha256": "6" * 64, "public-query-sha256": "7" * 64, "worker-sha256": "8" * 64},
                "expected_binding": binding}
    return canon(document)


def identity_for(descriptor, agent=LANE, **changes):
    document = {"schema": "wd.q4-lane-caller-identity.v1", "agent": agent, "agent_uuid": UUIDS.get(agent, "9" * 8),
                "worktree": r"C:\synthetic\lanes\codex-lead-1", "cwd": r"C:\synthetic\lanes\codex-lead-1\work",
                "owner_token_sha256": "c" * 64, "caller_pid": os.getpid(), "run_id": "wd-synthetic-generation",
                "descriptor_sha256": sha(descriptor)}
    document.update(changes)
    return canon(document)


def answer_for(lane, descriptor, status="ANSWER"):
    binding = json.loads(descriptor)["expected_binding"]
    return {"schema": "wd.q4-lane-answer.v1", "lane": lane, "status": status, "reason": "", "result_count": 1,
            "results": [{"ref": "synthetic-ref", "doc_id": "synthetic-doc", "stored_text": "synthetic record"}],
            "binding": dict(binding), "trust": dict(TRUST)}


@pytest.fixture
def data():
    inputs = synthetic_inputs()
    descriptor = descriptor_for(inputs)
    identity = identity_for(descriptor)
    return {"inputs": inputs, "descriptor": descriptor, "identity": identity,
            "kw": dict(enabled=True, identity_bytes=identity, expected_identity_sha256=sha(identity),
                       descriptor_bytes=descriptor, expected_descriptor_sha256=sha(descriptor),
                       situation=CANARY, time=datetime(2026, 1, 1, tzinfo=timezone.utc), inputs=inputs)}


@pytest.fixture
def chain(monkeypatch):
    """Replace the existing chain entry with a recorder that returns a configurable outcome."""
    calls = []
    state = {"outcome": None}

    def fake(**kwargs):
        calls.append(kwargs)
        outcome = state["outcome"]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
    monkeypatch.setattr(session, "query_session", fake)
    monkeypatch.setattr(adapter(), "_QUARANTINED", False)
    monkeypatch.setattr(adapter(), "_HOLD", [])
    monkeypatch.setattr(adapter(), "_process_cwd", lambda: PROCESS_CWD, raising=False)
    return calls, state


def call(data, **changes):
    kw = dict(data["kw"])
    kw.update(changes)
    return adapter().call_lane_session(**kw)


# --- default off and switch -------------------------------------------------------

def test_default_off_reads_nothing_and_calls_nothing(chain):
    class Opaque:
        def __getattribute__(self, key):
            raise AssertionError("inspected")
    calls, _ = chain
    assert adapter().call_lane_session(enabled=False, identity_bytes=Opaque(), expected_identity_sha256=Opaque(),
                                       descriptor_bytes=Opaque(), expected_descriptor_sha256=Opaque(),
                                       situation=Opaque(), time=Opaque(), inputs=Opaque()) == {"status": "OFF"}
    assert calls == []


@pytest.mark.parametrize("enabled", [1, "True", None, True.__class__])
def test_non_boolean_enable_refuses_before_the_chain(data, chain, enabled):
    calls, _ = chain
    with pytest.raises(ValueError, match="^caller_refused$"):
        call(data, enabled=enabled)
    assert calls == []


def test_there_is_no_lane_argument():
    import inspect
    parameters = inspect.signature(adapter().call_lane_session).parameters
    assert "lane" not in parameters
    assert all(parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values())


# --- declared-consistent identity: refusals before the chain ------------------------

@pytest.mark.parametrize("fault", [
    "lane-not-in-roster", "lane-not-in-descriptor", "uuid-mismatch", "stale-caller-pid", "cwd-outside-worktree",
    "cwd-is-worktree-prefix-sibling", "token-missing", "token-malformed", "identity-pin-mismatch",
    "identity-extra-key", "identity-missing-key", "identity-wrong-schema", "identity-not-bytes",
    "identity-descriptor-pin-differs", "identity-duplicate-key", "identity-nonfinite",
])
def test_inconsistent_identity_refuses_before_the_chain(data, chain, fault):
    calls, _ = chain
    inputs, descriptor = data["inputs"], data["descriptor"]
    identity = data["identity"]
    changes = {}
    if fault == "lane-not-in-roster":
        changes = {"agent": "codex-tools-2", "agent_uuid": UUIDS["codex-tools-1"]}
    elif fault == "lane-not-in-descriptor":
        descriptor = descriptor_for(inputs, lanes=("claude-rco-1", "claude-rco-2"))
        changes = {"descriptor_sha256": sha(descriptor)}
    elif fault == "uuid-mismatch":
        changes = {"agent_uuid": UUIDS["fable-5"]}
    elif fault == "stale-caller-pid":
        changes = {"caller_pid": os.getpid() + 100000}
    elif fault == "cwd-outside-worktree":
        changes = {"cwd": r"C:\synthetic\lanes\fable-5\work"}
    elif fault == "cwd-is-worktree-prefix-sibling":
        changes = {"cwd": r"C:\synthetic\lanes\codex-lead-1-other\work"}
    elif fault == "token-missing":
        changes = {"owner_token_sha256": ""}
    elif fault == "token-malformed":
        changes = {"owner_token_sha256": "C" * 64}
    elif fault == "identity-descriptor-pin-differs":
        changes = {"descriptor_sha256": "d" * 64}
    if changes:
        identity = identity_for(descriptor, **changes)
    kw = dict(identity_bytes=identity, expected_identity_sha256=sha(identity),
              descriptor_bytes=descriptor, expected_descriptor_sha256=sha(descriptor))
    if fault == "identity-pin-mismatch":
        kw["expected_identity_sha256"] = sha(identity + b" ")
    elif fault == "identity-extra-key":
        kw["identity_bytes"] = canon({**json.loads(identity), "extra": CANARY})
        kw["expected_identity_sha256"] = sha(kw["identity_bytes"])
    elif fault == "identity-missing-key":
        document = json.loads(identity)
        del document["owner_token_sha256"]
        kw["identity_bytes"] = canon(document)
        kw["expected_identity_sha256"] = sha(kw["identity_bytes"])
    elif fault == "identity-wrong-schema":
        kw["identity_bytes"] = canon({**json.loads(identity), "schema": "wd.q4-lane-caller-identity.v0"})
        kw["expected_identity_sha256"] = sha(kw["identity_bytes"])
    elif fault == "identity-not-bytes":
        kw["identity_bytes"] = identity.decode("ascii")
    elif fault == "identity-duplicate-key":
        kw["identity_bytes"] = identity[:-1] + b',"agent":"fable-5"}'
        kw["expected_identity_sha256"] = sha(kw["identity_bytes"])
    elif fault == "identity-nonfinite":
        kw["identity_bytes"] = identity[:-1] + b',"caller_pid":NaN}'
        kw["expected_identity_sha256"] = sha(kw["identity_bytes"])
    with pytest.raises(ValueError, match="^caller_refused$") as caught:
        call(data, **kw)
    assert calls == []
    assert CANARY not in repr(caught.value) and caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("fault", [
    "descriptor-pin-mismatch", "descriptor-not-bytes", "descriptor-wrong-schema", "descriptor-extra-key",
    "descriptor-missing-key", "fleet-pin-differs", "binding-pin-differs", "fixed-plan-pin-differs",
    "snapshot-pin-differs", "runtime-manifest-pin-differs", "interpreter-pin-differs", "bundle-root-differs",
    "public-plan-differs", "interpreter-path-differs", "cwd-differs", "timeout-differs", "max-output-differs",
    "interpreter-max-differs", "inputs-extra-key", "inputs-missing-key", "inputs-not-dict", "descriptor-unavailable",
])
def test_anchor_mismatch_refuses_before_the_chain(data, chain, fault):
    calls, _ = chain
    inputs = dict(data["inputs"])
    document = json.loads(data["descriptor"])
    kw = {}
    if fault == "descriptor-wrong-schema":
        document["schema"] = "wd.q4-shared-session-config.v1"
    elif fault == "descriptor-extra-key":
        document["extra"] = CANARY
    elif fault == "descriptor-missing-key":
        del document["outer"]
    elif fault == "fleet-pin-differs":
        document["fleet_sha256"] = "e" * 64
    elif fault == "binding-pin-differs":
        document["binding_sha256"] = "e" * 64
    elif fault == "fixed-plan-pin-differs":
        document["fixed_plan_sha256"] = "e" * 64
    elif fault == "snapshot-pin-differs":
        document["snapshot_sha256"] = "e" * 64
    elif fault == "runtime-manifest-pin-differs":
        document["runtime_manifest_sha256"] = "e" * 64
    elif fault == "interpreter-pin-differs":
        document["interpreter_sha256"] = "e" * 64
    elif fault == "bundle-root-differs":
        document["bundle_root"] = r"C:\synthetic\other-bundle"
    elif fault == "public-plan-differs":
        document["public_plan"] = r"C:\synthetic\bundle\other-plan.json"
    elif fault == "interpreter-path-differs":
        document["interpreter"] = r"C:\synthetic\python\other.exe"
    elif fault == "cwd-differs":
        document["cwd"] = r"C:\synthetic\lanes\codex-lead-1\other"
    elif fault == "timeout-differs":
        document["timeout_seconds"] = 61
    elif fault == "max-output-differs":
        document["max_output_bytes"] = 65535
    elif fault == "interpreter-max-differs":
        document["interpreter_max_bytes"] = 1
    elif fault == "inputs-extra-key":
        inputs["extra"] = CANARY
    elif fault == "inputs-missing-key":
        del inputs["cwd"]
    elif fault == "inputs-not-dict":
        inputs = [inputs]
    descriptor = canon(document)
    identity = identity_for(descriptor)
    kw.update(identity_bytes=identity, expected_identity_sha256=sha(identity), descriptor_bytes=descriptor,
              expected_descriptor_sha256=sha(descriptor), inputs=inputs)
    if fault == "descriptor-pin-mismatch":
        kw["expected_descriptor_sha256"] = sha(descriptor + b" ")
    elif fault == "descriptor-not-bytes":
        kw["descriptor_bytes"] = bytearray(descriptor)
    elif fault == "descriptor-unavailable":
        kw["descriptor_bytes"] = None
        kw["expected_descriptor_sha256"] = None
    with pytest.raises(ValueError, match="^caller_refused$") as caught:
        call(data, **kw)
    assert calls == []
    assert CANARY not in repr(caught.value) and caught.value.__cause__ is None and caught.value.__context__ is None


# --- the one chain call and its same-call validation ------------------------------

def test_consistent_call_uses_the_existing_chain_exactly_once_and_labels_trust(data, chain):
    calls, state = chain
    state["outcome"] = answer_for(LANE, data["descriptor"])
    result = call(data)
    assert len(calls) == 1
    passed = calls[0]
    assert passed["lane"] == LANE and passed["enabled"] is True
    assert passed["situation"] == CANARY and passed["time"] == data["kw"]["time"]
    assert {key: passed[key] for key in data["inputs"]} == data["inputs"]
    assert set(passed) == set(data["inputs"]) | {"lane", "situation", "time", "enabled"}
    assert result["status"] == "VALIDATED" and result["answer"] == state["outcome"]
    assert result["lane"] == LANE and result["lane_binding"] == "DECLARED_CONSISTENT"
    assert result["lane_authentication"] == "UNKNOWN" and result["cleanup"] == "chain_confirmed"
    assert result["inner_status"] == "ANSWER" and result["result_count"] == 1
    assert result["answer_sha256"] == sha(canon(state["outcome"]))
    assert result["identity_sha256"] == data["kw"]["expected_identity_sha256"]
    assert result["descriptor_sha256"] == data["kw"]["expected_descriptor_sha256"]
    assert len(result["call_id"]) == 32 and int(result["call_id"], 16) >= 0
    assert set(result) == {"schema", "status", "lane", "lane_binding", "lane_authentication", "cleanup", "inner_status",
                           "result_count", "answer", "answer_sha256", "identity_sha256", "descriptor_sha256", "call_id"}
    rendered = json.dumps(result, sort_keys=True)
    assert CANARY not in rendered and "c" * 64 not in rendered and "synthetic-generation" not in rendered


@pytest.mark.parametrize("status", ["ABSTAIN", "DEGRADED"])
def test_abstain_and_degraded_are_validated_outcomes_not_answers_of_record(data, chain, status):
    _, state = chain
    state["outcome"] = answer_for(LANE, data["descriptor"], status=status)
    result = call(data)
    assert result["status"] == "VALIDATED" and result["inner_status"] == status


def test_chain_refusal_is_returned_without_an_answer(data, chain):
    _, state = chain
    state["outcome"] = {"schema": "wd.q4-lane-answer.v1", "lane": LANE, "status": "REFUSED", "reason": "runtime_refused",
                        "result_count": 0, "results": [], "binding": None, "trust": dict(TRUST)}
    result = call(data)
    assert result["status"] == "REFUSED" and result["inner_status"] == "REFUSED"
    assert result["answer"] is None and result["answer_sha256"] is None and result["result_count"] == 0
    assert result["cleanup"] == "chain_confirmed"


@pytest.mark.parametrize("fault", [
    "wrong-lane", "extra-key", "missing-key", "status-unknown", "results-not-list", "binding-differs",
    "binding-missing-key", "trust-claims-authentication", "trust-not-dict", "not-a-dict", "dict-subclass",
    "result-count-mismatch", "bytes-instead-of-dict", "none",
])
def test_partial_or_foreign_chain_output_is_refused_and_discarded(data, chain, fault):
    _, state = chain
    answer = answer_for(LANE, data["descriptor"])
    if fault == "wrong-lane":
        answer["lane"] = "fable-5"
    elif fault == "extra-key":
        answer["stdout"] = CANARY
    elif fault == "missing-key":
        del answer["binding"]
    elif fault == "status-unknown":
        answer["status"] = "UNKNOWN"
    elif fault == "results-not-list":
        answer["results"] = (answer["results"][0],)
    elif fault == "binding-differs":
        answer["binding"]["pointer_sha256"] = "f" * 64
    elif fault == "binding-missing-key":
        del answer["binding"]["pointer_sha256"]
    elif fault == "trust-claims-authentication":
        answer["trust"]["lane_authentication"] = "VERIFIED"
    elif fault == "trust-not-dict":
        answer["trust"] = None
    elif fault == "not-a-dict":
        answer = [answer]
    elif fault == "dict-subclass":
        class Answer(dict):
            pass
        answer = Answer(answer)
    elif fault == "result-count-mismatch":
        answer["result_count"] = 2
    elif fault == "bytes-instead-of-dict":
        answer = canon(answer) + b"\n" + canon({"second": CANARY})
    elif fault == "none":
        answer = None
    state["outcome"] = answer
    result = call(data)
    assert result["status"] == "REFUSED" and result["inner_status"] == "output_refused"
    assert result["answer"] is None and result["answer_sha256"] is None and result["result_count"] == 0
    assert CANARY not in json.dumps(result, sort_keys=True, default=repr)


# --- cleanup, timeout, interruption and quarantine ----------------------------------

def test_timeout_is_unknown_cleanup_and_quarantines_this_process(data, chain):
    # Lead 2026-11-06 11:19Z / 11:24Z: a plain TimeoutError carries no proof that the child was drained
    # (statements outside the transport's protected cleanup operations can skip termination), so the
    # adapter must not assert chain_confirmed and the process guard must be retained.
    calls, state = chain
    state["outcome"] = TimeoutError(CANARY)
    result = call(data)
    assert result["status"] == "UNKNOWN" and result["inner_status"] == "timeout" and result["cleanup"] == "unknown"
    assert result["answer"] is None and result["answer_sha256"] is None and result["result_count"] == 0
    assert CANARY not in json.dumps(result, sort_keys=True)
    assert adapter()._QUARANTINED is True
    state["outcome"] = answer_for(LANE, data["descriptor"])
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["transport", "attestation"])
def test_unknown_cleanup_is_unknown_and_quarantines_this_process(data, chain, kind):
    calls, state = chain
    lease = object()
    state["outcome"] = (TransportCleanupUnknown("timeout") if kind == "transport"
                        else AttestationCleanupUnknown(lease))
    result = call(data)
    assert result["status"] == "UNKNOWN" and result["cleanup"] == "unknown" and result["answer"] is None
    assert result["inner_status"] == kind + "_cleanup_unknown"
    if kind == "attestation":
        assert adapter()._HOLD == [lease]
    state["outcome"] = answer_for(LANE, data["descriptor"])
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)
    assert len(calls) == 1


def test_quarantine_is_checked_before_any_input_is_read(chain):
    class Opaque:
        def __getattribute__(self, key):
            raise AssertionError("inspected")
    calls, _ = chain
    adapter()._QUARANTINED = True
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        adapter().call_lane_session(enabled=True, identity_bytes=Opaque(), expected_identity_sha256=Opaque(),
                                    descriptor_bytes=Opaque(), expected_descriptor_sha256=Opaque(),
                                    situation=Opaque(), time=Opaque(), inputs=Opaque())
    assert calls == []


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit])
def test_interruption_after_the_call_started_quarantines_and_re_raises(data, chain, signal):
    calls, state = chain
    state["outcome"] = signal(CANARY)
    with pytest.raises(signal) as caught:
        call(data)
    assert CANARY not in repr(caught.value)
    assert adapter()._QUARANTINED is True
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)
    assert len(calls) == 1


def test_other_chain_exceptions_are_unknown_cleanup_and_quarantine_this_process(data, chain):
    # The chain maps unexpected exceptions to an ordinary refusal, which can originate after a child was
    # launched; without a positive cleanup signal the adapter reports UNKNOWN and keeps the guard.
    calls, state = chain
    state["outcome"] = RuntimeError(CANARY)
    result = call(data)
    assert result["status"] == "UNKNOWN" and result["inner_status"] == "session_refused"
    assert result["cleanup"] == "unknown" and result["answer"] is None
    assert CANARY not in json.dumps(result, sort_keys=True)
    assert adapter()._QUARANTINED is True
    state["outcome"] = answer_for(LANE, data["descriptor"])
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)
    assert len(calls) == 1


# --- Grok challenge cases (report b537ba1b): open answer fields, dot parts, races ----------

@pytest.mark.parametrize("fault", [
    "trust-extra-key", "trust-value-not-unknown", "reason-nonempty", "schema-differs", "item-extra-key",
    "item-missing-key", "item-nonhex-ref", "item-nested-value", "item-inf-value", "item-not-dict", "too-many-results",
    "refused-with-foreign-reason",
])
def test_open_fields_of_a_chain_answer_are_refused_before_storage(data, chain, fault):
    _, state = chain
    answer = answer_for(LANE, data["descriptor"])
    if fault == "trust-extra-key":
        answer["trust"]["query"] = CANARY
    elif fault == "trust-value-not-unknown":
        answer["trust"]["freshness"] = CANARY
    elif fault == "reason-nonempty":
        answer["reason"] = "c" * 64
    elif fault == "schema-differs":
        answer["schema"] = "wd.q4-lane-answer.v2"
    elif fault == "item-extra-key":
        answer["results"][0]["cwd"] = CANARY
    elif fault == "item-missing-key":
        del answer["results"][0]["stored_text"]
    elif fault == "item-nonhex-ref":
        answer["results"][0]["ref_sha256"] = CANARY
    elif fault == "item-nested-value":
        answer["results"][0]["stored_text"] = {"text": CANARY}
    elif fault == "item-inf-value":
        answer["results"][0]["stored_text"] = float("inf")
    elif fault == "item-not-dict":
        answer["results"][0] = [CANARY]
    elif fault == "too-many-results":
        answer["results"] = [dict(answer["results"][0]) for _ in range(101)]
        answer["result_count"] = 101
    elif fault == "refused-with-foreign-reason":
        answer.update(status="REFUSED", reason=CANARY, results=[], result_count=0, binding=None)
    state["outcome"] = answer
    result = call(data)
    assert result["status"] == "REFUSED" and result["inner_status"] == "output_refused"
    assert result["answer"] is None and result["answer_sha256"] is None
    assert CANARY not in json.dumps(result, sort_keys=True, default=repr)
    assert adapter()._QUARANTINED is False


def test_validated_answer_items_are_the_closed_resolved_records(data, chain):
    _, state = chain
    answer = answer_for(LANE, data["descriptor"])
    answer["results"] = [{"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64, "stored_text": "synthetic record"}]
    state["outcome"] = answer
    result = call(data)
    assert result["status"] == "VALIDATED" and result["answer"]["results"] == answer["results"]


def answer_for(lane, descriptor, status="ANSWER"):  # noqa: F811 - closed resolved item shape from here on
    binding = json.loads(descriptor)["expected_binding"]
    return {"schema": "wd.q4-lane-answer.v1", "lane": lane, "status": status, "reason": "", "result_count": 1,
            "results": [{"ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64, "stored_text": "synthetic record"}],
            "binding": dict(binding), "trust": dict(TRUST)}


@pytest.mark.parametrize("field, value", [
    ("cwd", r"C:\synthetic\lanes\codex-lead-1\..\fable-5\work"),
    ("cwd", r"C:\synthetic\lanes\codex-lead-1\.\work"),
    ("worktree", r"C:\synthetic\lanes\..\lanes\codex-lead-1"),
    ("worktree", ""),
])
def test_dot_parts_in_cwd_or_worktree_refuse_before_the_chain(data, chain, field, value):
    calls, _ = chain
    identity = identity_for(data["descriptor"], **{field: value})
    with pytest.raises(ValueError, match="^caller_refused$"):
        call(data, identity_bytes=identity, expected_identity_sha256=sha(identity))
    assert calls == []


def test_a_concurrent_call_in_this_process_refuses_busy_before_reading_inputs(data, chain):
    class Opaque:
        def __getattribute__(self, key):
            raise AssertionError("inspected")
    calls, _ = chain
    assert adapter()._LOCK.acquire(blocking=False)
    try:
        with pytest.raises(ValueError, match="^caller_busy$"):
            adapter().call_lane_session(enabled=True, identity_bytes=Opaque(), expected_identity_sha256=Opaque(),
                                        descriptor_bytes=Opaque(), expected_descriptor_sha256=Opaque(),
                                        situation=Opaque(), time=Opaque(), inputs=Opaque())
    finally:
        adapter()._LOCK.release()
    assert calls == []
    assert adapter().call_lane_session(enabled=False, identity_bytes=Opaque(), expected_identity_sha256=Opaque(),
                                       descriptor_bytes=Opaque(), expected_descriptor_sha256=Opaque(),
                                       situation=Opaque(), time=Opaque(), inputs=Opaque()) == {"status": "OFF"}


@pytest.mark.parametrize("kind", ["transport", "attestation"])
def test_cleanup_unknown_raised_during_the_pre_check_quarantines(data, chain, monkeypatch, kind):
    calls, _ = chain
    exc = TransportCleanupUnknown("other") if kind == "transport" else AttestationCleanupUnknown(object())

    def raising(*args, **kwargs):
        raise exc
    monkeypatch.setattr(adapter().roster, "build_roster_snapshot", raising)
    with pytest.raises(ValueError, match="^caller_refused$"):
        call(data)
    assert calls == [] and adapter()._QUARANTINED is True
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)


def test_any_other_base_exception_from_the_chain_leaves_the_quarantine_set(data, chain):
    calls, state = chain
    state["outcome"] = GeneratorExit()
    with pytest.raises(GeneratorExit):
        call(data)
    assert adapter()._QUARANTINED is True and len(calls) == 1
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)


def test_quarantine_is_set_during_the_chain_call_and_cleared_only_after_a_confirmed_outcome(data, chain):
    calls, state = chain
    seen = []

    def fake(**kwargs):
        calls.append(kwargs)
        seen.append(adapter()._QUARANTINED)
        return answer_for(LANE, data["descriptor"])
    import tools.wd_q4_lane_session_client as client
    client.query_session = fake
    assert call(data)["status"] == "VALIDATED"
    assert seen == [True] and adapter()._QUARANTINED is False


# --- caller v3 (RCO1 F1, RCO2 cleanup label): checked worktree/cwd binding -----------------

@pytest.mark.parametrize("actual", [
    r"C:\synthetic\lanes\codex-lead-1\elsewhere", r"C:\synthetic\lanes\codex-lead-1", r"C:\synthetic\lanes\fable-5\work",
    r"C:\synthetic\lanes\codex-lead-1\work\deeper", r"D:\synthetic\lanes\codex-lead-1\work",
])
def test_identity_cwd_must_equal_the_actual_process_cwd(data, chain, monkeypatch, actual):
    calls, _ = chain
    monkeypatch.setattr(adapter(), "_process_cwd", lambda: actual, raising=False)
    with pytest.raises(ValueError, match="^caller_refused$") as caught:
        call(data)
    assert calls == [] and caught.value.__context__ is None
    assert CANARY not in repr(caught.value) and "synthetic" not in repr(caught.value)


@pytest.mark.parametrize("actual", [
    PROCESS_CWD.replace("work", "wor\u212a"),           # KELVIN SIGN casefolds to k: a distinct NTFS directory
    PROCESS_CWD.replace("synthetic", "\u017fynthetic"),  # LATIN SMALL LETTER LONG S casefolds to s
    r"\\srv\share\synthetic\lanes\codex-lead-1\work",   # UNC: empty leading parts are refused, not dropped
    r"C:\synthetic\\lanes\codex-lead-1\work",            # doubled separator: an empty part is refused
    r"\synthetic\lanes\codex-lead-1\work",                # drive-less root
])
def test_identity_cwd_equality_is_ascii_case_only_and_refuses_empty_parts(data, chain, monkeypatch, actual):
    # Grok 1cf1c81e: str.casefold admits distinct NTFS directories; only ASCII letters fold now.
    calls, _ = chain
    monkeypatch.setattr(adapter(), "_process_cwd", lambda: actual, raising=False)
    with pytest.raises(ValueError, match="^caller_refused$") as caught:
        call(data)
    assert calls == [] and caught.value.__context__ is None and "synthetic" not in repr(caught.value)


@pytest.mark.parametrize("pinned", ["C:\\\u017fynthetic\\lanes\\codex-lead-1", r"C:\synthetic\lanes\\codex-lead-1",
                                    "C:\\synthetic\\lane\u017f\\codex-lead-1"])
def test_fleet_worktree_equality_is_ascii_case_only_and_refuses_empty_parts(data, chain, pinned):
    calls, _ = chain
    inputs = dict(data["inputs"])
    fleet = fleet_bytes({**WORKTREES, LANE: pinned})
    inputs["fleet_bytes"], inputs["expected_fleet_sha256"] = fleet, sha(fleet)
    descriptor = descriptor_for(inputs)
    identity = identity_for(descriptor)
    with pytest.raises(ValueError, match="^caller_refused$") as caught:
        call(data, inputs=inputs, descriptor_bytes=descriptor, expected_descriptor_sha256=sha(descriptor),
             identity_bytes=identity, expected_identity_sha256=sha(identity))
    assert calls == [] and caught.value.__context__ is None and "synthetic" not in repr(caught.value)


@pytest.mark.parametrize("actual", [
    r"C:/synthetic/lanes/codex-lead-1/work", "c:\\SYNTHETIC\\Lanes\\codex-lead-1\\work\\", r"C:\synthetic\lanes\codex-lead-1\work",
])
def test_identity_cwd_equal_to_the_actual_process_cwd_up_to_separators_and_case_is_consistent(data, chain, monkeypatch, actual):
    calls, state = chain
    state["outcome"] = answer_for(LANE, data["descriptor"])
    monkeypatch.setattr(adapter(), "_process_cwd", lambda: actual, raising=False)
    assert call(data)["status"] == "VALIDATED" and len(calls) == 1


@pytest.mark.parametrize("fault", ["fleet-worktree-differs", "fleet-worktree-missing", "fleet-worktree-not-a-string",
                                   "fleet-worktree-empty", "fleet-worktree-sibling-prefix", "fleet-worktree-is-a-parent",
                                   "identity-worktree-differs"])
def test_identity_worktree_must_equal_the_pinned_fleet_lane_worktree_or_fail_closed(data, chain, fault):
    calls, _ = chain
    inputs = dict(data["inputs"])
    identity_changes = {}
    if fault == "fleet-worktree-differs":
        fleet = fleet_bytes({**WORKTREES, LANE: r"C:\synthetic\lanes\other"})
    elif fault == "fleet-worktree-missing":
        fleet = fleet_bytes(drop_worktree=True)
    elif fault == "fleet-worktree-not-a-string":
        document = json.loads(fleet_bytes())
        next(entry for entry in document["lanes"] if entry["agent"] == LANE)["worktree"] = ["C:", "synthetic"]
        fleet = canon(document)
    elif fault == "fleet-worktree-empty":
        fleet = fleet_bytes({**WORKTREES, LANE: ""})
    elif fault == "fleet-worktree-sibling-prefix":
        fleet = fleet_bytes({**WORKTREES, LANE: r"C:\synthetic\lanes\codex-lead-1\work"})
    elif fault == "fleet-worktree-is-a-parent":
        # equality, not prefix: a pinned parent directory must not admit a deeper declared worktree
        fleet = fleet_bytes({**WORKTREES, LANE: r"C:\synthetic\lanes"})
    else:
        fleet = fleet_bytes()
        identity_changes = {"worktree": r"C:\synthetic\lanes", "cwd": PROCESS_CWD}
    inputs["fleet_bytes"], inputs["expected_fleet_sha256"] = fleet, sha(fleet)
    descriptor = descriptor_for(inputs)
    identity = identity_for(descriptor, **identity_changes)
    with pytest.raises(ValueError, match="^caller_refused$") as caught:
        call(data, inputs=inputs, descriptor_bytes=descriptor, expected_descriptor_sha256=sha(descriptor),
             identity_bytes=identity, expected_identity_sha256=sha(identity))
    assert calls == [] and caught.value.__context__ is None and "synthetic" not in repr(caught.value)


@pytest.mark.parametrize("pinned", [r"C:/synthetic/lanes/codex-lead-1", "c:\\Synthetic\\LANES\\codex-lead-1\\"])
def test_fleet_worktree_equal_up_to_separators_and_case_is_consistent(data, chain, pinned):
    calls, state = chain
    inputs = dict(data["inputs"])
    fleet = fleet_bytes({**WORKTREES, LANE: pinned})
    inputs["fleet_bytes"], inputs["expected_fleet_sha256"] = fleet, sha(fleet)
    descriptor = descriptor_for(inputs)
    identity = identity_for(descriptor)
    state["outcome"] = answer_for(LANE, descriptor)
    result = call(data, inputs=inputs, descriptor_bytes=descriptor, expected_descriptor_sha256=sha(descriptor),
                  identity_bytes=identity, expected_identity_sha256=sha(identity))
    assert result["status"] == "VALIDATED" and len(calls) == 1 and result["lane_authentication"] == "UNKNOWN"


@pytest.mark.parametrize("outcome", [session.LaneSessionRefused("synthetic-pre-transport"), ValueError(CANARY),
                                     OSError(CANARY)])
def test_generic_refusal_cannot_be_told_from_a_post_launch_failure_so_it_is_unknown_and_quarantines(data, chain, outcome):
    # The adapter cannot distinguish a pre-transport refusal from a refusal raised after a launch; both
    # are reported UNKNOWN with cleanup unknown, and only an explicit chain outcome clears the guard.
    calls, state = chain
    state["outcome"] = outcome
    result = call(data)
    assert result["status"] == "UNKNOWN" and result["inner_status"] == "session_refused"
    assert result["cleanup"] == "unknown" and result["answer"] is None
    assert adapter()._QUARANTINED is True and len(calls) == 1
    assert CANARY not in json.dumps(result, sort_keys=True)
    with pytest.raises(ValueError, match="^caller_quarantined$"):
        call(data)
    assert len(calls) == 1


def test_the_docstring_states_the_caller_provided_anchors_and_the_traceback_limit():
    text = adapter().__doc__
    assert "caller-provided" in text and "os.getcwd" in text and "no positive cleanup signal" in text
    assert "frame locals" in text and "chain_confirmed" in text and "ASCII" in text
    assert "no_unknown_reported" not in text and "mechanically" not in text


# --- the real synthetic chain (Windows named-pipe transport) --------------------------

@windows
def test_real_synthetic_chain_returns_a_validated_answer_with_unknown_authentication(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("lane_session_client_test_helpers_caller",
        Path(__file__).with_name("test_wd_q4_lane_session_client.py"))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    case = helper.case.__wrapped__(monkeypatch, tmp_path)
    monkeypatch.setattr(adapter(), "_QUARANTINED", False)
    inputs = {key: value for key, value in case.items()
              if key not in ("lane", "situation", "time", "enabled") and not key.startswith("_")}
    binding_document = json.loads(case["binding_bytes"])
    roster = json.loads(inputs["fleet_bytes"])
    uuid = next(entry["agent_uuid"] for entry in roster["lanes"] if entry["agent"] == LANE)
    # F1 (RCO1): the identity cwd is checked against the actual process cwd and the identity worktree
    # against the pinned fleet lane worktree; the synthetic fleet gains the worktree field the real
    # manifest carries (the roster builder drops it, so the roster pin of the binding is unchanged).
    worktree = os.getcwd()
    for entry in roster["lanes"]:
        entry["worktree"] = worktree if entry["agent"] == LANE else str(tmp_path / entry["agent"])
    inputs["fleet_bytes"] = canon(roster)
    inputs["expected_fleet_sha256"] = sha(inputs["fleet_bytes"])
    document = json.loads(descriptor_for(inputs, lanes=binding_document["lanes"],
                                         expected_binding=binding_document["binding"]))
    document["cwd"] = inputs["cwd"]
    descriptor = canon(document)
    identity = identity_for(descriptor, agent=LANE, agent_uuid=uuid, worktree=worktree, cwd=worktree)
    result = adapter().call_lane_session(
        enabled=True, identity_bytes=identity, expected_identity_sha256=sha(identity), descriptor_bytes=descriptor,
        expected_descriptor_sha256=sha(descriptor), situation=case["situation"], time=case["time"], inputs=inputs)
    assert result["status"] == "VALIDATED" and result["inner_status"] == "ANSWER"
    assert result["answer"]["lane"] == LANE and len(result["answer"]["results"]) == len(case["_expected"])
    assert all(set(item) >= {"ref_sha256", "doc_id_sha256", "stored_text"} for item in result["answer"]["results"])
    assert result["answer"]["trust"]["lane_authentication"] == "UNKNOWN"
    assert result["lane_authentication"] == "UNKNOWN" and result["lane_binding"] == "DECLARED_CONSISTENT"
    assert result["cleanup"] == "chain_confirmed" and result["result_count"] == len(case["_expected"])
