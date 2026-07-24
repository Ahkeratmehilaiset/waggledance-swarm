# SPDX-License-Identifier: BUSL-1.1
"""Adversarial matrix for W2B SeedSolverAdapterV1 + verify_seed_solver_replay."""

from __future__ import annotations

from copy import deepcopy

import pytest

import waggledance.core.seed_work_contracts as C
from waggledance.core.cell_identity import build_cell_identity
from waggledance.core.genesis_lineage import build_root_entry
from waggledance.core.seed_solver_adapter import (
    SeedSolverAdapterError, SeedSolverAdapterV1, verify_seed_solver_replay,
)

_S = lambda c: "sha256:" + c * 64  # noqa: E731
AS_OF = "2026-07-24T06:45:00Z"


def _snap(ident, root, *, generation="gen-1"):
    ids = (C._frozen_mapping(ident, "id"),)
    ents = (C._frozen_mapping(root, "e"),)
    head = C.derive_registry_head_digest(generation=generation, identities=ids, lineage_entries=ents)
    return {"schema_version": C.SNAPSHOT_SCHEMA, "generation": generation,
            "identities": [ident], "lineage_entries": [root], "head_digest": head}


def _setup():
    ident = build_cell_identity(pubkey_digest=_S("a"), genesis_material_digest=_S("b"),
                                created_at_utc="2026-07-24T06:00:00Z").to_mapping()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snap(ident, root)
    entry = {"entry_id": 1, "local_id": 1, "log_code": "LC", "device": "TOOL1",
             "status": "WIP", "created_at": "2026-07-24T05:00:00Z"}
    req = {"schema_version": C.REQUEST_SCHEMA, "entries": [dict(entry)],
           "repair_timeline": [dict(entry)],
           "tool_states": {"TOOL1": {"tool_id": "TOOL1", "state": "PRODUCTION"}},
           "comments": [], "subtools": {"TOOL1": []},
           "options": {"duplicate_window_hours": 24, "evidence_limit": 4, "max_open_window_days": 7}}
    env = C.build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                     lineage_proof=[root], registry_snapshot=snap,
                                     request_payload=req, as_of_utc=AS_OF)
    return env, snap


def test_execute_and_replay_ok():
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    assert res["advisory_only"] is True and res["external_writes_applied"] is False
    assert verify_seed_solver_replay(env, res, snap, AS_OF) == (True, None)


def test_execute_is_deterministic():
    env, snap = _setup()
    a = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    b = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    assert a == b


def test_snapshot_generation_mismatch_rejects_before_pdam():
    env, snap = _setup()
    bad = deepcopy(snap); bad["generation"] = "gen-2"
    # head recomputed so the snapshot self-consistency holds but generation != envelope binding
    ids = (C._frozen_mapping(bad["identities"][0], "i"),)
    ents = (C._frozen_mapping(bad["lineage_entries"][0], "e"),)
    bad["head_digest"] = C.derive_registry_head_digest(generation="gen-2", identities=ids, lineage_entries=ents)
    with pytest.raises(SeedSolverAdapterError, match="generation"):
        SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=bad, as_of_utc=AS_OF)


def test_snapshot_head_mismatch_rejects():
    env, snap = _setup()
    bad = deepcopy(snap); bad["head_digest"] = _S("0")
    with pytest.raises(SeedSolverAdapterError):
        SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=bad, as_of_utc=AS_OF)


def test_as_of_mismatch_rejects():
    env, snap = _setup()
    with pytest.raises(SeedSolverAdapterError, match="as_of"):
        SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap,
                                      as_of_utc="2026-07-24T07:00:00Z")


def test_bad_envelope_rejects():
    _env, snap = _setup()
    with pytest.raises(SeedSolverAdapterError):
        SeedSolverAdapterV1().execute(envelope={"not": "envelope"}, registry_snapshot=snap, as_of_utc=AS_OF)


def test_replay_action_tamper_fails_closed():
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    r = deepcopy(res)
    r["actions"] = list(r["actions"]) + [
        {"entry_id": 99, "device": "TOOL9", "kind": "CLOSE_OK", "current_status": "WIP",
         "target_status": "OK", "action_text": "injected", "duplicate_of": None}]
    # digest is now stale -> parse_seed_work_result inside replay rejects
    ok, _reason = verify_seed_solver_replay(env, r, snap, AS_OF)
    assert ok is False


def test_replay_wrong_snapshot_rejects():
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    bad = deepcopy(snap); bad["head_digest"] = _S("0")
    ok, _reason = verify_seed_solver_replay(env, res, bad, AS_OF)
    assert ok is False


def test_replay_solver_digest_mismatch_rejects():
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    r = deepcopy(res); r["solver_contract_digest"] = _S("0")
    ok, _reason = verify_seed_solver_replay(env, r, snap, AS_OF)
    assert ok is False


# --- live-boundary membership (lead fresh-head finding, #1560) --------------
def _cellB_snap(*, generation="gen-B"):
    """A VALID single-root registry snapshot for a DIFFERENT cell B -- it does
    NOT contain cell A's identity or proof entry."""
    identB = build_cell_identity(pubkey_digest=_S("1"), genesis_material_digest=_S("2"),
                                 created_at_utc="2026-07-24T06:00:00Z").to_mapping()
    rootB = build_root_entry(cell_id=identB["cell_id"], inherited_goal_slice_digest=_S("3"),
                             inherited_budget_slice_digest=_S("4")).to_mapping()
    return _snap(identB, rootB, generation=generation)


def _forge_result(env, cell_id, as_of):
    """Build a SELF-CONSISTENT result for `env` (here a forged envelope) so that
    verify_seed_solver_replay REACHES the live snapshot-binding boundary instead
    of short-circuiting on an earlier envelope_id/solver-digest mismatch. Uses
    the same shared solver path the adapter uses, then recomputes the result +
    evidence digests over the (forged) envelope's bindings."""
    from waggledance.core.seed_solver_adapter import _pdam_actions
    parsed = C.parse_seed_work_envelope(env)
    actions = _pdam_actions(parsed["request_payload"], as_of)
    result_digest = C.derive_result_digest(
        envelope_id=env["envelope_id"], cell_id=cell_id,
        lineage_entry_hash=env["lineage"]["entry_hash"], request_digest=env["request_digest"],
        solver_contract_digest=env["solver_contract_digest"],
        solver_config_digest=env["solver_config_digest"],
        registry_generation=env["registry_generation"],
        actions=actions, registry_head_digest=env["registry_head_digest"], as_of_utc=as_of)
    evidence_digest = C.derive_evidence_digest(
        envelope_id=env["envelope_id"], cell_id=cell_id, request_digest=env["request_digest"],
        registry_head_digest=env["registry_head_digest"], result_digest=result_digest)
    return {
        "schema_version": C.RESULT_SCHEMA, "envelope_id": env["envelope_id"], "cell_id": cell_id,
        "lineage_entry_hash": env["lineage"]["entry_hash"], "solver_id": C.SOLVER_ID,
        "solver_contract_digest": env["solver_contract_digest"],
        "solver_config_digest": env["solver_config_digest"], "request_digest": env["request_digest"],
        "registry_generation": env["registry_generation"],
        "registry_head_digest": env["registry_head_digest"], "as_of_utc": as_of,
        "action_schema_version": C.ACTION_SCHEMA_VERSION, "actions": actions,
        "result_digest": result_digest, "evidence_digest": evidence_digest,
        "advisory_only": True, "external_writes_applied": False,
    }


def test_forged_cross_snapshot_membership_rejected_at_live_boundary():
    """A STRUCTURALLY-VALID envelope that copies a FOREIGN snapshot's generation/
    head into itself -- falsely claiming membership in a registry that does not
    contain its identity -- passes the contract wire verifier (the envelope
    carries no snapshot, so membership cannot be checked there). It MUST be
    rejected independently at the live boundary: execute() raises AND
    verify_seed_solver_replay() returns false, both at snapshot membership.
    build_seed_work_envelope blocks an honest builder; this proves the
    execute/replay guard, not merely the build path."""
    envA, _snapA = _setup()
    identA_cell = envA["identity"]["cell_id"]
    snapB = _cellB_snap()  # valid single-root snapshot that EXCLUDES cell A
    # Hand-forge: rebind envelope A onto snapshot B's generation/head, recomputing
    # envelope_id so the wire verifier accepts it.
    forged = dict(envA)
    forged["registry_generation"] = snapB["generation"]
    forged["registry_head_digest"] = snapB["head_digest"]
    forged["envelope_id"] = C.derive_envelope_id(
        identity=envA["identity"], lineage=envA["lineage"], parent_lineage=envA["parent_lineage"],
        lineage_proof_digest=envA["lineage_proof_digest"],
        registry_generation=snapB["generation"], registry_head_digest=snapB["head_digest"],
        solver_contract_digest=envA["solver_contract_digest"],
        solver_config_digest=envA["solver_config_digest"],
        request_digest=envA["request_digest"], as_of_utc=envA["as_of_utc"])
    # Well-formed at the contract layer...
    assert C.verify_seed_work_envelope(forged) == (True, None)
    # ...but BOTH live entry points reject it at snapshot membership.
    with pytest.raises(SeedSolverAdapterError, match="membership"):
        SeedSolverAdapterV1().execute(envelope=forged, registry_snapshot=snapB, as_of_utc=AS_OF)
    forged_result = _forge_result(forged, identA_cell, AS_OF)
    ok, reason = verify_seed_solver_replay(forged, forged_result, snapB, AS_OF)
    assert ok is False and "membership" in (reason or "")


def test_forged_result_reaches_binding_boundary_sanity():
    """Guard for the test vector itself: the forged result is self-consistent and
    binds to its (honest) envelope, so replay passes when the snapshot matches --
    proving the membership rejection above is the ONLY reason replay fails there,
    not an incidental malformed-result short-circuit."""
    env, snap = _setup()
    forged_result = _forge_result(env, env["identity"]["cell_id"], AS_OF)
    assert verify_seed_solver_replay(env, forged_result, snap, AS_OF) == (True, None)


# --- replay result<->envelope echo binding (lead fresh-head finding, #1560) --
def _reseal(res):
    """Recompute result_digest + evidence_digest over `res`'s OWN fields so a
    field-tampered result stays SELF-CONSISTENT (parse_seed_work_result passes).
    This is exactly the adversary's power: a self-consistent result that echoes a
    different identity/binding field than the envelope it claims to answer."""
    rd = C.derive_result_digest(
        envelope_id=res["envelope_id"], cell_id=res["cell_id"],
        lineage_entry_hash=res["lineage_entry_hash"], request_digest=res["request_digest"],
        solver_contract_digest=res["solver_contract_digest"],
        solver_config_digest=res["solver_config_digest"],
        registry_generation=res["registry_generation"],
        actions=res["actions"], registry_head_digest=res["registry_head_digest"], as_of_utc=res["as_of_utc"])
    ed = C.derive_evidence_digest(
        envelope_id=res["envelope_id"], cell_id=res["cell_id"], request_digest=res["request_digest"],
        registry_head_digest=res["registry_head_digest"], result_digest=rd)
    out = dict(res); out["result_digest"] = rd; out["evidence_digest"] = ed
    return out


@pytest.mark.parametrize("field,value", [
    ("cell_id", _S("0")),
    ("lineage_entry_hash", _S("0")),
    ("request_digest", _S("0")),
    ("registry_generation", "gen-EVIL"),
    ("registry_head_digest", _S("0")),
    ("as_of_utc", "2026-07-24T06:45:01Z"),
])
def test_replay_result_echo_binding_tamper_rejected(field, value):
    """A SELF-CONSISTENT result that alters any identity/binding field vs the
    envelope it answers must be rejected by replay. Without the full echo binding,
    replay checked only envelope_id + solver digests + actions, so such a result
    would fail-open to (True, None)."""
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    bad = _reseal({**res, field: value})
    # Still self-consistent -- it parses on its own ...
    assert C.parse_seed_work_result(bad)
    # ... but replay must reject it: it does not bind THIS envelope.
    ok, reason = verify_seed_solver_replay(env, bad, snap, AS_OF)
    assert ok is False and reason is not None


# --- request composition rejected through the full producer path -------------
def _base_req():
    entry = {"entry_id": 1, "local_id": 1, "log_code": "LC", "device": "TOOL1",
             "status": "WIP", "created_at": "2026-07-24T05:00:00Z"}
    return {"schema_version": C.REQUEST_SCHEMA, "entries": [dict(entry)],
            "repair_timeline": [dict(entry)],
            "tool_states": {"TOOL1": {"tool_id": "TOOL1", "state": "PRODUCTION"}},
            "comments": [], "subtools": {"TOOL1": []},
            "options": {"duplicate_window_hours": 24, "evidence_limit": 4, "max_open_window_days": 7}}


def _build_with_request(req):
    ident = build_cell_identity(pubkey_digest=_S("a"), genesis_material_digest=_S("b"),
                                created_at_utc="2026-07-24T06:00:00Z").to_mapping()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snap(ident, root)
    return C.build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                      lineage_proof=[root], registry_snapshot=snap,
                                      request_payload=req, as_of_utc=AS_OF)


def test_build_rejects_foreign_labelled_tool_state():
    req = _base_req()
    req["tool_states"] = {"TOOL1": {"tool_id": "SOME_OTHER_TOOL", "state": "PRODUCTION"}}
    with pytest.raises(C.SeedWorkContractError, match="tool_id"):
        _build_with_request(req)


def test_build_rejects_duplicate_open_entry_and_timeline_row():
    req = _base_req()
    dup = dict(req["entries"][0])
    req["entries"] = [dict(dup), dict(dup)]
    req["repair_timeline"] = [dict(dup), dict(dup)]
    with pytest.raises(C.SeedWorkContractError, match="duplicate entry_id"):
        _build_with_request(req)


def test_build_rejects_over_byte_cap_request():
    # A request over MAX_REQUEST_BYTES (but under the 4 MiB envelope cap) must be
    # rejected on the producer/build path, not silently accepted.
    req = _base_req()
    req["comments"] = [{"tool_id": "TOOL1", "when": "2026-07-24T05:00:00Z",
                        "by_user": "u", "comment": "x" * C.MAX_REQUEST_BYTES}]
    with pytest.raises(C.SeedWorkContractError, match="canonical bytes"):
        _build_with_request(req)


def _reseal_request(env, forged_req):
    """Swap in a forged request and RESEAL request_digest + envelope_id over it so
    the envelope is fully self-consistent. Now the ONLY thing that can reject it at
    execute is the request-composition guard itself: were the guard absent, every
    digest would match and execute would emit a result (fail-open). This makes the
    live-boundary vector genuinely BITE the guard rather than request_digest/
    envelope_id tamper detection. (_base_req is already in normalized shape, so the
    resealed digest equals what parse would recompute if the guard were absent.)"""
    e = dict(env)
    e["request_payload"] = forged_req
    e["request_digest"] = C.derive_request_digest(forged_req, e["as_of_utc"])
    e["envelope_id"] = C.derive_envelope_id(
        identity=e["identity"], lineage=e["lineage"], parent_lineage=e["parent_lineage"],
        lineage_proof_digest=e["lineage_proof_digest"], registry_generation=e["registry_generation"],
        registry_head_digest=e["registry_head_digest"], solver_contract_digest=e["solver_contract_digest"],
        solver_config_digest=e["solver_config_digest"], request_digest=e["request_digest"],
        as_of_utc=e["as_of_utc"])
    return e


def test_execute_rejects_resealed_foreign_labelled_tool_state():
    env, snap = _setup()
    forged = _base_req()
    forged["tool_states"] = {"TOOL1": {"tool_id": "SOME_OTHER_TOOL", "state": "PRODUCTION"}}
    bad = _reseal_request(env, forged)
    assert C.verify_seed_work_envelope(bad)[0] is False  # rejected only by the guard
    with pytest.raises(SeedSolverAdapterError, match="tool_id"):
        SeedSolverAdapterV1().execute(envelope=bad, registry_snapshot=snap, as_of_utc=AS_OF)


def test_execute_rejects_resealed_duplicate_open_entry():
    env, snap = _setup()
    forged = _base_req()
    dup = dict(forged["entries"][0])
    forged["entries"] = [dict(dup), dict(dup)]
    forged["repair_timeline"] = [dict(dup), dict(dup)]
    bad = _reseal_request(env, forged)
    assert C.verify_seed_work_envelope(bad)[0] is False
    with pytest.raises(SeedSolverAdapterError, match="duplicate entry_id"):
        SeedSolverAdapterV1().execute(envelope=bad, registry_snapshot=snap, as_of_utc=AS_OF)


# --- item 1: producer amplification / validate provisional ResultV1 ----------
def test_execute_validates_provisional_result_and_rejects_amplification(monkeypatch):
    """The producer path (execute) must validate the ResultV1 it builds BEFORE
    returning: an over-cap result (amplification past MAX_RESULT_BYTES) is rejected
    fail-closed instead of emitted. Forcing the solver to over-produce proves the
    provisional-result gate bites (execute previously returned the dict unchecked)."""
    env, snap = _setup()
    import waggledance.core.seed_solver_adapter as A
    huge = [{"entry_id": 1, "device": "TOOL1", "kind": "CLOSE_OK",
             "current_status": "WIP", "target_status": "OK",
             "action_text": "x" * (C.MAX_RESULT_BYTES + 100), "duplicate_of": None}]
    monkeypatch.setattr(A, "_pdam_actions", lambda *a, **k: huge)
    with pytest.raises(SeedSolverAdapterError):
        A.SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)


def test_execute_returned_result_passes_wire_verifier():
    """Every result the adapter emits round-trips through the standalone wire
    verifier (the provisional validation is not a no-op)."""
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    assert C.parse_seed_work_result(res)


# --- item 3: as_of exact-str/canonical, guarded arithmetic, fail-closed -------
class _EqAny(str):
    def __eq__(self, o): return True
    def __ne__(self, o): return False
    def __hash__(self): return 0


def _edge_setup():
    """Envelope whose open entry sits at the calendar edge so the solver's window
    arithmetic (created_at + max_open_window_days) overflows datetime."""
    ident = build_cell_identity(pubkey_digest=_S("a"), genesis_material_digest=_S("b"),
                                created_at_utc="2026-07-24T06:00:00Z").to_mapping()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snap(ident, root)
    entry = {"entry_id": 1, "local_id": 1, "log_code": "LC", "device": "TOOL1",
             "status": "WIP", "created_at": "9999-12-31T00:00:00Z"}
    req = {"schema_version": C.REQUEST_SCHEMA, "entries": [dict(entry)],
           "repair_timeline": [dict(entry)],
           "tool_states": {"TOOL1": {"tool_id": "TOOL1", "state": "PRODUCTION"}},
           "comments": [], "subtools": {"TOOL1": []},
           "options": {"duplicate_window_hours": 24, "evidence_limit": 4,
                       "max_open_window_days": 366}}
    edge = "9999-12-31T23:59:59Z"
    env = C.build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                     lineage_proof=[root], registry_snapshot=snap,
                                     request_payload=req, as_of_utc=edge)
    return env, snap, edge


def test_execute_wraps_solver_datetime_overflow():
    env, snap, edge = _edge_setup()
    with pytest.raises(SeedSolverAdapterError, match="solver run failed"):
        SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=edge)


def test_replay_wraps_solver_failure_returns_false(monkeypatch):
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    import waggledance.core.seed_solver_adapter as A

    def boom(*a, **k):
        raise OverflowError("edge")

    monkeypatch.setattr(A, "_pdam_actions", boom)
    ok, reason = A.verify_seed_solver_replay(env, res, snap, AS_OF)
    assert ok is False and "replay failed" in (reason or "")


def test_execute_hostile_as_of_type_rejected():
    # A str subclass whose __eq__ is always-True would pass the != comparison; the
    # exact-str + canonical guard must reject it BEFORE the comparison/arithmetic.
    env, snap = _setup()
    with pytest.raises(SeedSolverAdapterError, match="canonical"):
        SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=_EqAny(AS_OF))


def test_replay_hostile_as_of_type_returns_false():
    env, snap = _setup()
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    ok, reason = verify_seed_solver_replay(env, res, snap, _EqAny(AS_OF))
    assert ok is False and "canonical" in (reason or "")


@pytest.mark.parametrize("bad_as_of", ["2026-07-24 06:45:00", "not-a-time", ""])
def test_execute_noncanonical_as_of_rejected(bad_as_of):
    env, snap = _setup()
    with pytest.raises(SeedSolverAdapterError, match="canonical"):
        SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=bad_as_of)


# --- item 4: end-to-end permutation invariance of the content address ---------
def _two_device_env(*, entry_order, comment_order, sub_order):
    ident = build_cell_identity(pubkey_digest=_S("a"), genesis_material_digest=_S("b"),
                                created_at_utc="2026-07-24T06:00:00Z").to_mapping()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snap(ident, root)
    e1 = {"entry_id": 1, "local_id": 1, "log_code": "LC1", "device": "TOOL1",
          "status": "WIP", "created_at": "2026-07-24T04:00:00Z"}
    e2 = {"entry_id": 2, "local_id": 2, "log_code": "LC2", "device": "TOOL2",
          "status": "WIP", "created_at": "2026-07-24T04:30:00Z"}
    c1 = {"tool_id": "TOOL1", "when": "2026-07-24T05:00:00Z", "by_user": "alice", "comment": "repair alpha"}
    c2 = {"tool_id": "TOOL1", "when": "2026-07-24T05:00:00Z", "by_user": "alice", "comment": "repair beta"}
    pool_e = {1: e1, 2: e2}
    pool_c = {1: c1, 2: c2}
    req = {"schema_version": C.REQUEST_SCHEMA,
           "entries": [dict(pool_e[i]) for i in entry_order],
           "repair_timeline": [dict(e1), dict(e2)],
           "tool_states": {"TOOL1": {"tool_id": "TOOL1", "state": "DOWN"},
                           "TOOL2": {"tool_id": "TOOL2", "state": "PRODUCTION"},
                           "TOOL1_A": {"tool_id": "TOOL1_A", "state": "DOWNTIME"},
                           "TOOL1_B": {"tool_id": "TOOL1_B", "state": "ENGINEERING"}},
           "comments": [dict(pool_c[i]) for i in comment_order],
           "subtools": {"TOOL1": list(sub_order), "TOOL2": []},
           "options": {"duplicate_window_hours": 24, "evidence_limit": 4, "max_open_window_days": 7}}
    env = C.build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                     lineage_proof=[root], registry_snapshot=snap,
                                     request_payload=req, as_of_utc=AS_OF)
    return env, snap


# --- item 1 (refinement): PRE-SOLVER result-output upper-bound gate -----------
def _max_card_env(*, shared_comments, comment_len, evidence_limit, n=None):
    """Build a VALID max-cardinality envelope. Every one of ``n`` open entries
    lives on its own device but shares one subtool ``SHARED`` whose comments are
    reusable as evidence across ALL devices -- the amplification the reproducer
    exploits (a small bounded request whose ResultV1 would be multi-megabyte)."""
    n = C.MAX_ENTRIES if n is None else n
    ident = build_cell_identity(pubkey_digest=_S("a"), genesis_material_digest=_S("b"),
                                created_at_utc="2026-07-24T06:00:00Z").to_mapping()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snap(ident, root)
    entries, timeline, tool_states, subtools = [], [], {}, {}
    if shared_comments:
        tool_states["SHARED"] = {"tool_id": "SHARED", "state": "PRODUCTION"}
    for i in range(n):
        dev = f"T{i}"
        e = {"entry_id": i + 1, "local_id": i + 1, "log_code": "LC", "device": dev,
             "status": "WIP", "created_at": "2026-07-24T05:00:00Z"}
        entries.append(dict(e)); timeline.append(dict(e))
        tool_states[dev] = {"tool_id": dev, "state": "PRODUCTION"}
        subtools[dev] = ["SHARED"] if shared_comments else []
    comments = [{"tool_id": "SHARED", "when": "2026-07-24T05:30:00Z", "by_user": "user",
                 "comment": "repair note " + "y" * comment_len} for _ in range(shared_comments)]
    req = {"schema_version": C.REQUEST_SCHEMA, "entries": entries, "repair_timeline": timeline,
           "tool_states": tool_states, "comments": comments, "subtools": subtools,
           "options": {"duplicate_window_hours": 24, "evidence_limit": evidence_limit,
                       "max_open_window_days": 7}}
    env = C.build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                     lineage_proof=[root], registry_snapshot=snap,
                                     request_payload=req, as_of_utc=AS_OF)
    return env, snap


def test_execute_rejects_amplifier_before_solver_runs(monkeypatch):
    """The BITING regression: a valid ~150 KB max-cardinality request whose
    ResultV1 would exceed MAX_RESULT_BYTES must be rejected on the input-derived
    PRE-SOLVER bound -- the solver (plan_close_actions) must NEVER be invoked and
    no provisional over-cap result is ever constructed. Wiring plan_close_actions
    to explode proves the gate fires strictly before the solver."""
    env, snap = _max_card_env(shared_comments=32, comment_len=228, evidence_limit=32)
    # The request itself is well under the 2 MiB request cap and the 4 MiB envelope
    # cap -- it is a legitimate wire object; only the OUTPUT would amplify.
    import waggledance.core.seed_solver_adapter as A
    from waggledance.core.magma.canonical import canonical_json_bytes
    assert len(canonical_json_bytes(env["request_payload"])) < C.MAX_REQUEST_BYTES

    called = []

    def _must_not_run(*a, **k):
        called.append(1)
        raise AssertionError("solver must not be called once the pre-solver gate fires")

    monkeypatch.setattr(A, "plan_close_actions", _must_not_run)
    with pytest.raises(SeedSolverAdapterError, match="bound"):
        A.SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    assert called == []  # solver never reached


def test_replay_rejects_amplifier_before_solver_runs(monkeypatch):
    """Defense in depth: verify_seed_solver_replay applies the same pre-solver
    output bound. An attacker supplies a SMALL, self-consistent result that binds
    the amplifier envelope (echo + snapshot binding pass) to provoke an expensive
    replay; the bound gate refuses before the solver builds a multi-megabyte
    in-memory action set."""
    env, snap = _max_card_env(shared_comments=32, comment_len=228, evidence_limit=32)
    import waggledance.core.seed_solver_adapter as A
    cell_id = env["identity"]["cell_id"]
    actions: list = []  # small forged actions -> the provided result parses fine
    result_digest = C.derive_result_digest(
        envelope_id=env["envelope_id"], cell_id=cell_id,
        lineage_entry_hash=env["lineage"]["entry_hash"], request_digest=env["request_digest"],
        solver_contract_digest=env["solver_contract_digest"],
        solver_config_digest=env["solver_config_digest"],
        registry_generation=env["registry_generation"], actions=actions,
        registry_head_digest=env["registry_head_digest"], as_of_utc=AS_OF)
    evidence_digest = C.derive_evidence_digest(
        envelope_id=env["envelope_id"], cell_id=cell_id, request_digest=env["request_digest"],
        registry_head_digest=env["registry_head_digest"], result_digest=result_digest)
    forged_small = {
        "schema_version": C.RESULT_SCHEMA, "envelope_id": env["envelope_id"], "cell_id": cell_id,
        "lineage_entry_hash": env["lineage"]["entry_hash"], "solver_id": C.SOLVER_ID,
        "solver_contract_digest": env["solver_contract_digest"],
        "solver_config_digest": env["solver_config_digest"], "request_digest": env["request_digest"],
        "registry_generation": env["registry_generation"],
        "registry_head_digest": env["registry_head_digest"], "as_of_utc": AS_OF,
        "action_schema_version": C.ACTION_SCHEMA_VERSION, "actions": actions,
        "result_digest": result_digest, "evidence_digest": evidence_digest,
        "advisory_only": True, "external_writes_applied": False,
    }
    assert C.parse_seed_work_result(forged_small)  # the provided result is valid + small

    def _must_not_run(*a, **k):
        raise AssertionError("solver must not be called")

    monkeypatch.setattr(A, "plan_close_actions", _must_not_run)
    ok, reason = A.verify_seed_solver_replay(env, forged_small, snap, AS_OF)
    assert ok is False and "upper bound" in (reason or "")


def test_execute_accepts_max_cardinality_lean_request():
    """Boundary: a max-cardinality (MAX_ENTRIES) request whose per-entry output is
    small stays UNDER the bound and executes normally -- the gate keys on the
    conservative OUTPUT estimate, not merely on entry count, so ordinary bulk
    requests are not collateral-damaged."""
    env, snap = _max_card_env(shared_comments=0, comment_len=0, evidence_limit=4)
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    assert len(res["actions"]) == C.MAX_ENTRIES
    assert C.parse_seed_work_result(res)
    assert verify_seed_solver_replay(env, res, snap, AS_OF) == (True, None)


def test_execute_result_permutation_invariant():
    """Permuting entries, tied comments, and subtool members yields the IDENTICAL
    envelope content-address and the IDENTICAL advisory result (canonicalization
    happens before digest + PDAM)."""
    ref_env, ref_snap = _two_device_env(entry_order=[1, 2], comment_order=[1, 2],
                                         sub_order=["TOOL1_A", "TOOL1_B"])
    perm_env, perm_snap = _two_device_env(entry_order=[2, 1], comment_order=[2, 1],
                                          sub_order=["TOOL1_B", "TOOL1_A"])
    assert ref_env["envelope_id"] == perm_env["envelope_id"]
    ref_res = SeedSolverAdapterV1().execute(envelope=ref_env, registry_snapshot=ref_snap, as_of_utc=AS_OF)
    perm_res = SeedSolverAdapterV1().execute(envelope=perm_env, registry_snapshot=perm_snap, as_of_utc=AS_OF)
    assert ref_res == perm_res
