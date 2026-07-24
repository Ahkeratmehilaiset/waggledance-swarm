# SPDX-License-Identifier: BUSL-1.1
"""Adversarial matrix for W2B SeedWork contracts (advisory, fail-closed)."""

from __future__ import annotations

from copy import deepcopy

import pytest

import waggledance.core.seed_work_contracts as C
from waggledance.core.seed_work_contracts import (
    SeedWorkContractError, SeedWorkEnvelopeV1, SeedWorkResultV1,
    build_seed_work_envelope, parse_seed_work_envelope, parse_seed_work_result,
    verify_seed_work_envelope, verify_seed_work_evidence_integrity,
)
from waggledance.core.cell_identity import build_cell_identity
from waggledance.core.genesis_lineage import build_root_entry
from waggledance.core.seed_solver_adapter import SeedSolverAdapterV1

_S = lambda c: "sha256:" + c * 64  # noqa: E731
AS_OF = "2026-07-24T06:45:00Z"


def _identity():
    return build_cell_identity(
        pubkey_digest=_S("a"), genesis_material_digest=_S("b"),
        created_at_utc="2026-07-24T06:00:00Z").to_mapping()


def _snapshot(ident, root, *, generation="gen-1"):
    ids = (C._frozen_mapping(ident, "id"),)
    ents = (C._frozen_mapping(root, "e"),)
    head = C.derive_registry_head_digest(generation=generation, identities=ids, lineage_entries=ents)
    return {"schema_version": C.SNAPSHOT_SCHEMA, "generation": generation,
            "identities": [ident], "lineage_entries": [root], "head_digest": head}


def _request():
    entry = {"entry_id": 1, "local_id": 1, "log_code": "LC", "device": "TOOL1",
             "status": "WIP", "created_at": "2026-07-24T05:00:00Z"}
    return {"schema_version": C.REQUEST_SCHEMA, "entries": [dict(entry)],
            "repair_timeline": [dict(entry)],
            "tool_states": {"TOOL1": {"tool_id": "TOOL1", "state": "PRODUCTION"}},
            "comments": [], "subtools": {"TOOL1": []},
            "options": {"duplicate_window_hours": 24, "evidence_limit": 4,
                        "max_open_window_days": 7}}


def _envelope():
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snapshot(ident, root)
    env = build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                   lineage_proof=[root], registry_snapshot=snap,
                                   request_payload=_request(), as_of_utc=AS_OF)
    return env, snap


# --- happy path ------------------------------------------------------------
def test_build_verify_execute_replay_round_trip():
    env, snap = _envelope()
    assert verify_seed_work_envelope(env) == (True, None)
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    assert parse_seed_work_result(res)
    assert verify_seed_work_evidence_integrity(env, res) == (True, None)
    assert res["advisory_only"] is True and res["external_writes_applied"] is False
    assert res["solver_id"] == C.SOLVER_ID


def test_records_construct_and_expose_mapping():
    env, snap = _envelope()
    rec = SeedWorkEnvelopeV1.parse(env)
    assert rec.envelope_id == env["envelope_id"] and rec.to_mapping() == parse_seed_work_envelope(env)
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    rres = SeedWorkResultV1.parse(res)
    assert rres.result_digest == res["result_digest"]


def test_known_answer_snapshot_head_digest_vector():
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    head = _snapshot(ident, root)["head_digest"]
    # deterministic + recomputable: same inputs -> same head digest
    assert head == _snapshot(ident, root)["head_digest"]
    assert head.startswith("sha256:") and len(head) == 71


# --- envelope digest / keyset tamper ---------------------------------------
def test_envelope_keyset_exact():
    env, _ = _envelope()
    broken = dict(env); broken["extra"] = 1
    ok, reason = verify_seed_work_envelope(broken)
    assert ok is False and reason == "envelope keyset"


@pytest.mark.parametrize("field", [
    "envelope_id", "lineage_proof_digest", "solver_contract_digest",
    "solver_config_digest", "request_digest", "registry_head_digest",
])
def test_envelope_digest_tamper_rejects(field):
    env, _ = _envelope()
    broken = deepcopy(env); broken[field] = _S("f")
    ok, _r = verify_seed_work_envelope(broken)
    assert ok is False


def test_envelope_wrong_solver_id_rejects():
    env, _ = _envelope()
    broken = dict(env); broken["solver_id"] = "wd.other.v1"
    assert verify_seed_work_envelope(broken) == (False, "envelope solver_id refused")


def test_envelope_forged_identity_rejects():
    env, _ = _envelope()
    broken = deepcopy(env); broken["identity"]["cell_id"] = _S("0")
    ok, _r = verify_seed_work_envelope(broken)
    assert ok is False


# --- registry snapshot -----------------------------------------------------
def test_snapshot_head_mismatch_rejects():
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snapshot(ident, root); snap["head_digest"] = _S("0")
    with pytest.raises(SeedWorkContractError):
        C.parse_registry_snapshot(snap)


def test_snapshot_keyset_and_generation():
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snapshot(ident, root); snap["extra"] = 1
    with pytest.raises(SeedWorkContractError):
        C.parse_registry_snapshot(snap)
    snap2 = _snapshot(ident, root); snap2["generation"] = ""
    with pytest.raises(SeedWorkContractError):
        C.parse_registry_snapshot(snap2)


# --- request validation + PDAM evidence completeness -----------------------
def _resolve(**overrides):
    req = _request()
    for k, v in overrides.items():
        req[k] = v
    return C.parse_seed_work_request(req, AS_OF)


def test_request_missing_parent_tool_state_rejects():
    with pytest.raises(SeedWorkContractError, match="parent ToolState"):
        _resolve(tool_states={})


def test_request_missing_subtools_key_rejects():
    with pytest.raises(SeedWorkContractError, match="subtools key"):
        _resolve(subtools={})


def test_request_named_subtool_without_state_rejects():
    with pytest.raises(SeedWorkContractError, match="subtool"):
        _resolve(subtools={"TOOL1": ["TOOL1_SUB"]})


def test_request_entry_not_in_timeline_rejects():
    with pytest.raises(SeedWorkContractError, match="repair_timeline"):
        _resolve(repair_timeline=[])


def test_request_unknown_status_enum_rejects():
    bad = dict(_request()["entries"][0]); bad["status"] = "OPEN_UNKNOWN"
    with pytest.raises(SeedWorkContractError):
        _resolve(entries=[bad])


def test_request_unknown_tool_state_enum_rejects():
    with pytest.raises(SeedWorkContractError):
        _resolve(tool_states={"TOOL1": {"tool_id": "TOOL1", "state": "MOON"}})


def test_request_keyset_exact():
    req = _request(); req["fifth"] = 1
    with pytest.raises(SeedWorkContractError, match="request keyset"):
        C.parse_seed_work_request(req, AS_OF)


@pytest.mark.parametrize("bad_ts", [
    "2026-07-24 06:00:00", "2026-07-24T06:00:00", "2026-07-24T06:00:00.0Z",
    "2026-13-01T00:00:00Z", "2026-07-24T06:00:00+00:00",
])
def test_bad_timestamps_reject(bad_ts):
    with pytest.raises(SeedWorkContractError):
        C.parse_utc(bad_ts, "ts")


def test_future_row_after_as_of_rejects():
    bad = dict(_request()["entries"][0]); bad["created_at"] = "2026-07-24T23:59:59Z"
    with pytest.raises(SeedWorkContractError, match="after as_of"):
        _resolve(entries=[bad], repair_timeline=[bad])


def test_options_out_of_range_reject():
    with pytest.raises(SeedWorkContractError):
        _resolve(options={"duplicate_window_hours": -1, "evidence_limit": 4, "max_open_window_days": 7})


def test_options_bool_is_not_int():
    with pytest.raises(SeedWorkContractError):
        _resolve(options={"duplicate_window_hours": True, "evidence_limit": 4, "max_open_window_days": 7})


def test_entries_over_cap_rejects():
    entry = dict(_request()["entries"][0])
    with pytest.raises(SeedWorkContractError, match="bounded"):
        _resolve(entries=[dict(entry, entry_id=i, local_id=i) for i in range(C.MAX_ENTRIES + 1)])


def test_request_byte_cap_boundary_enforced():
    # MAX_REQUEST_BYTES is enforced by the standalone parser: exactly-at-cap is
    # accepted, cap+1 rejects (a request under the 4 MiB envelope cap but over the
    # request cap must still fail on the producer path).
    from waggledance.core.magma.canonical import canonical_json_bytes
    req = _request()
    req["comments"] = [{"tool_id": "TOOL1", "when": "2026-07-24T05:00:00Z",
                        "by_user": "u", "comment": "x"}]
    base = len(canonical_json_bytes(req))
    pad = C.MAX_REQUEST_BYTES - base  # each extra ASCII char adds exactly one byte
    req["comments"][0]["comment"] = "x" * (1 + pad)  # total canonical == cap
    assert len(canonical_json_bytes(req)) == C.MAX_REQUEST_BYTES
    assert C.parse_seed_work_request(req, AS_OF)  # at cap: accepted + fully validates
    req["comments"][0]["comment"] = "x" * (2 + pad)  # cap + 1
    assert len(canonical_json_bytes(req)) == C.MAX_REQUEST_BYTES + 1
    with pytest.raises(SeedWorkContractError, match="canonical bytes"):
        C.parse_seed_work_request(req, AS_OF)


# --- request composition / relation fail-opens (lead fresh-head finding) -----
def test_request_tool_state_key_tool_id_mismatch_rejects():
    # Foreign-labelled state under a device key: solver would serve TOOL1 another
    # tool's state.
    with pytest.raises(SeedWorkContractError, match="tool_id"):
        _resolve(tool_states={"TOOL1": {"tool_id": "SOME_OTHER_TOOL", "state": "PRODUCTION"}})


def test_request_duplicate_entry_id_rejects():
    dup = dict(_request()["entries"][0])
    with pytest.raises(SeedWorkContractError, match="duplicate entry_id"):
        _resolve(entries=[dict(dup), dict(dup)], repair_timeline=[dict(dup), dict(dup)])


def test_request_duplicate_timeline_entry_id_rejects():
    e = dict(_request()["entries"][0])
    with pytest.raises(SeedWorkContractError, match="repair_timeline has a duplicate"):
        _resolve(repair_timeline=[dict(e), dict(e)])


def test_request_entry_not_exactly_once_in_timeline_rejects():
    # Same entry_id but a DIFFERENT (non-equal) timeline row for the device: the
    # open entry is not present exactly/equal once.
    e = dict(_request()["entries"][0])
    other = dict(e); other["log_code"] = "DIFFERENT"
    with pytest.raises(SeedWorkContractError, match="exactly once"):
        _resolve(entries=[dict(e)], repair_timeline=[dict(other)])


def test_request_subtool_self_member_rejects():
    with pytest.raises(SeedWorkContractError, match="itself"):
        _resolve(subtools={"TOOL1": ["TOOL1"]})


def test_request_duplicate_subtool_member_rejects():
    with pytest.raises(SeedWorkContractError, match="duplicate member"):
        _resolve(subtools={"TOOL1": ["TOOL1_SUB", "TOOL1_SUB"]})


# --- result parse ----------------------------------------------------------
def _result():
    env, snap = _envelope()
    return env, SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)


def test_result_advisory_flags_enforced():
    _env, res = _result()
    r = dict(res); r["advisory_only"] = False
    with pytest.raises(SeedWorkContractError):
        parse_seed_work_result(r)
    r2 = dict(res); r2["external_writes_applied"] = True
    with pytest.raises(SeedWorkContractError):
        parse_seed_work_result(r2)


def test_result_digest_tamper_rejects():
    _env, res = _result()
    r = dict(res); r["result_digest"] = _S("0")
    with pytest.raises(SeedWorkContractError):
        parse_seed_work_result(r)


def test_result_action_tamper_breaks_digest():
    _env, res = _result()
    r = deepcopy(res)
    if r["actions"]:
        r["actions"][0]["kind"] = "KEEP_WIP"
    else:
        r["actions"] = [{"entry_id": 9, "device": "X", "kind": "KEEP_WIP",
                         "current_status": "WIP", "target_status": "WIP",
                         "action_text": "x", "duplicate_of": None}]
    with pytest.raises(SeedWorkContractError):
        parse_seed_work_result(r)


def test_evidence_integrity_cross_binding():
    env, res = _result()
    r = deepcopy(res); r["request_digest"] = _S("0")
    # recompute digests to keep the result internally consistent but mismatched vs envelope
    ok, _reason = verify_seed_work_evidence_integrity(env, r)
    assert ok is False


def _reseal_result(res):
    """Recompute result_digest + evidence_digest over `res`'s OWN fields so a
    field-tampered result stays self-consistent (parse would otherwise reject on
    a stale digest, masking the field-level contract check under test)."""
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


@pytest.mark.parametrize("bad_as_of", [
    "", "not-a-time", "2026-07-24T09:45:00+03:00", "9999-99-99T99:99:99Z",
    "2026-07-24T06:45:00.0Z",
])
def test_result_noncanonical_as_of_rejected(bad_as_of):
    # Standalone ResultV1 must enforce the frozen canonical aware-UTC contract on
    # as_of_utc even when the result is self-consistently resealed over it.
    _env, res = _result()
    bad = _reseal_result({**res, "as_of_utc": bad_as_of})
    assert C.parse_seed_work_result(res)  # sanity: the un-tampered result parses
    with pytest.raises(SeedWorkContractError, match="as_of"):
        parse_seed_work_result(bad)


# --- hostile-type container probes -----------------------------------------
class _EqAny(str):
    def __eq__(self, o): return True
    def __ne__(self, o): return False
    def __hash__(self): return 0


def test_non_dict_and_subclass_inputs_reject_without_protocol():
    class _BadGet(dict):
        def __getitem__(self, k):
            raise AssertionError("must not be invoked")

    env, _ = _envelope()
    assert verify_seed_work_envelope(_BadGet(env))[0] is False
    assert verify_seed_work_envelope(["x"])[0] is False
    assert verify_seed_work_envelope(None)[0] is False


def test_alias_key_rejected():
    env, _ = _envelope()
    alias = {**env}
    del alias["envelope_id"]
    alias[_EqAny("envelope_id")] = env["envelope_id"]
    assert verify_seed_work_envelope(alias)[0] is False


# --- cross-relation + cross-snapshot membership (lead #1560 finding) --------
def _cellA():
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    return ident, root


def _cellB():
    ident = build_cell_identity(pubkey_digest=_S("1"), genesis_material_digest=_S("2"),
                                created_at_utc="2026-07-24T06:10:00Z").to_mapping()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("7"),
                            inherited_budget_slice_digest=_S("8")).to_mapping()
    return ident, root


def _snap_of(idents, entries, *, generation="gen-1"):
    ids = tuple(C._frozen_mapping(i, "i") for i in idents)
    ents = tuple(C._frozen_mapping(e, "e") for e in entries)
    head = C.derive_registry_head_digest(generation=generation, identities=ids, lineage_entries=ents)
    return {"schema_version": C.SNAPSHOT_SCHEMA, "generation": generation,
            "identities": list(idents), "lineage_entries": list(entries), "head_digest": head}


def test_cross_snapshot_identity_not_member_rejected():
    identA, rootA = _cellA()
    identB, rootB = _cellB()
    snapB = _snap_of([identB], [rootB])  # bound snapshot contains only B
    with pytest.raises(SeedWorkContractError, match="not a member"):
        build_seed_work_envelope(identity=identA, lineage=rootA, parent_lineage=None,
                                 lineage_proof=[rootA], registry_snapshot=snapB,
                                 request_payload=_request(), as_of_utc=AS_OF)


def test_missing_proof_member_rejected():
    identA, rootA = _cellA()
    # A DIFFERENT-content genesis root for the SAME cell A (distinct entry_hash).
    # The bound snapshot is a COMPLETE registry image of cell A (identA + rootA,
    # so identity/lineage cell_id sets are equal), but the envelope's proof cites
    # rootA2 -- whose entry_hash is absent from the snapshot. Membership must
    # reject it even though identity A itself IS a snapshot member.
    rootA2 = build_root_entry(cell_id=identA["cell_id"], inherited_goal_slice_digest=_S("9"),
                              inherited_budget_slice_digest=_S("e")).to_mapping()
    assert rootA2["entry_hash"] != rootA["entry_hash"]
    snap = _snap_of([identA], [rootA])  # identity present, proof entry (rootA2) absent
    with pytest.raises(SeedWorkContractError, match="not a member"):
        build_seed_work_envelope(identity=identA, lineage=rootA2, parent_lineage=None,
                                 lineage_proof=[rootA2], registry_snapshot=snap,
                                 request_payload=_request(), as_of_utc=AS_OF)


def test_unrelated_declared_lineage_rejected():
    identA, rootA = _cellA()
    _identB, rootB = _cellB()
    snap = _snap_of([identA], [rootA])
    # identity A + proof [rootA] but a separately-valid declared lineage B
    with pytest.raises(SeedWorkContractError, match="cell_id"):
        build_seed_work_envelope(identity=identA, lineage=rootB, parent_lineage=None,
                                 lineage_proof=[rootA], registry_snapshot=snap,
                                 request_payload=_request(), as_of_utc=AS_OF)


def test_wrong_parent_lineage_rejected():
    from waggledance.core.genesis_lineage import build_child_entry
    identA, rootA = _cellA()
    _identB, rootB = _cellB()
    identC = build_cell_identity(pubkey_digest=_S("3"), genesis_material_digest=_S("4"),
                                 created_at_utc="2026-07-24T06:20:00Z").to_mapping()
    childC = build_child_entry(cell_id=identC["cell_id"], parent_entry=rootA,
                               inherited_goal_slice_digest=_S("d"),
                               inherited_budget_slice_digest=_S("e")).to_mapping()
    # Complete registry image: both cell A (rootA) and cell C (childC) have an
    # identity AND a lineage entry, so the identity/lineage cell_id sets are equal.
    snap = _snap_of([identA, identC], [rootA, childC])
    # valid build first
    ok = build_seed_work_envelope(identity=identC, lineage=childC, parent_lineage=rootA,
                                  lineage_proof=[rootA, childC], registry_snapshot=snap,
                                  request_payload=_request(), as_of_utc=AS_OF)
    assert verify_seed_work_envelope(ok)[0] is True
    # wrong parent (rootB instead of rootA penultimate)
    with pytest.raises(SeedWorkContractError, match="penultimate"):
        build_seed_work_envelope(identity=identC, lineage=childC, parent_lineage=rootB,
                                 lineage_proof=[rootA, childC], registry_snapshot=snap,
                                 request_payload=_request(), as_of_utc=AS_OF)


def test_snapshot_duplicate_identity_and_entry_rejected():
    identA, rootA = _cellA()
    with pytest.raises(SeedWorkContractError, match="duplicate identity"):
        C.parse_registry_snapshot(_snap_of([identA, dict(identA)], [rootA]))
    with pytest.raises(SeedWorkContractError, match="duplicate lineage"):
        C.parse_registry_snapshot(_snap_of([identA], [rootA, dict(rootA)]))


def test_snapshot_invalid_member_rejected():
    identA, rootA = _cellA()
    forged = dict(identA); forged["cell_id"] = _S("0")
    with pytest.raises(SeedWorkContractError, match="identity invalid"):
        C.parse_registry_snapshot(_snap_of([forged], [rootA]))


# --- whole-registry closure (lead fresh-head finding, #1560) ----------------
def test_snapshot_multi_root_registry_rejected():
    # Two distinct, individually-valid roots are NOT one rooted tree.
    identA, rootA = _cellA()
    identB, rootB = _cellB()
    with pytest.raises(SeedWorkContractError, match="not closed"):
        C.parse_registry_snapshot(_snap_of([identA, identB], [rootA, rootB]))


def test_snapshot_orphan_child_registry_rejected():
    # A child whose parent entry is ABSENT from the registry is an orphan; the
    # entries are individually valid but not connected under one root.
    from waggledance.core.genesis_lineage import build_child_entry
    identA, rootA = _cellA()
    _identB, rootB = _cellB()
    identX = build_cell_identity(pubkey_digest=_S("5"), genesis_material_digest=_S("6"),
                                 created_at_utc="2026-07-24T06:30:00Z").to_mapping()
    orphan = build_child_entry(cell_id=identX["cell_id"], parent_entry=rootB,
                               inherited_goal_slice_digest=_S("d"),
                               inherited_budget_slice_digest=_S("e")).to_mapping()
    with pytest.raises(SeedWorkContractError, match="not closed"):
        C.parse_registry_snapshot(_snap_of([identA, identX], [rootA, orphan]))


def test_snapshot_duplicate_cell_id_distinct_entry_rejected():
    # Same cell_id, different content => different entry_hash: passes the
    # entry_hash-dedup but is a cell with TWO origins. Closure must reject it --
    # this is exactly the gap entry_hash-dedup alone does not close.
    identA, rootA = _cellA()
    rootA2 = build_root_entry(cell_id=identA["cell_id"], inherited_goal_slice_digest=_S("9"),
                              inherited_budget_slice_digest=_S("e")).to_mapping()
    assert rootA2["entry_hash"] != rootA["entry_hash"]
    with pytest.raises(SeedWorkContractError, match="not closed"):
        C.parse_registry_snapshot(_snap_of([identA], [rootA, rootA2]))


# --- item 2: identity<->lineage cell_id set equality (phantom / missing sibling)
def test_snapshot_phantom_lineage_cell_rejected():
    # A lineage entry whose cell has NO identity record: closure holds (the
    # phantom is a valid child of the genuine root), but the entry cell_id set
    # gains a cell the identity set lacks. Must fail-closed on the asymmetry.
    from waggledance.core.genesis_lineage import build_child_entry
    identA, rootA = _cellA()
    identB, _rootB = _cellB()
    phantom = build_child_entry(cell_id=identB["cell_id"], parent_entry=rootA,
                                inherited_goal_slice_digest=_S("d"),
                                inherited_budget_slice_digest=_S("e")).to_mapping()
    with pytest.raises(SeedWorkContractError, match="set-equal"):
        C.parse_registry_snapshot(_snap_of([identA], [rootA, phantom]))


def test_snapshot_missing_identity_sibling_rejected():
    # An identity whose cell has NO lineage entry (a missing sibling): the identity
    # set gains a cell the lineage set lacks. Must fail-closed on the asymmetry.
    identA, rootA = _cellA()
    identB, _rootB = _cellB()
    with pytest.raises(SeedWorkContractError, match="set-equal"):
        C.parse_registry_snapshot(_snap_of([identA, identB], [rootA]))


# --- item 4: canonicalization -> permutation-invariant request content address -
def _perm_request(*, entry_order, comment_order, sub_order):
    e1 = {"entry_id": 1, "local_id": 1, "log_code": "LC1", "device": "TOOL1",
          "status": "WIP", "created_at": "2026-07-24T04:00:00Z"}
    e2 = {"entry_id": 2, "local_id": 2, "log_code": "LC2", "device": "TOOL2",
          "status": "WIP", "created_at": "2026-07-24T04:30:00Z"}
    c1 = {"tool_id": "TOOL1", "when": "2026-07-24T05:00:00Z", "by_user": "alice", "comment": "repair alpha"}
    c2 = {"tool_id": "TOOL1", "when": "2026-07-24T05:00:00Z", "by_user": "alice", "comment": "repair beta"}
    pool_e = {1: e1, 2: e2}
    pool_c = {1: c1, 2: c2}
    return {"schema_version": C.REQUEST_SCHEMA,
            "entries": [dict(pool_e[i]) for i in entry_order],
            "repair_timeline": [dict(e1), dict(e2)],
            "tool_states": {"TOOL1": {"tool_id": "TOOL1", "state": "DOWN"},
                            "TOOL2": {"tool_id": "TOOL2", "state": "PRODUCTION"},
                            "TOOL1_A": {"tool_id": "TOOL1_A", "state": "DOWNTIME"},
                            "TOOL1_B": {"tool_id": "TOOL1_B", "state": "ENGINEERING"}},
            "comments": [dict(pool_c[i]) for i in comment_order],
            "subtools": {"TOOL1": list(sub_order), "TOOL2": []},
            "options": {"duplicate_window_hours": 24, "evidence_limit": 4, "max_open_window_days": 7}}


def test_request_canonicalization_permutation_invariant():
    from waggledance.core.seed_solver_adapter import _pdam_actions
    ref = C.parse_seed_work_request(
        _perm_request(entry_order=[1, 2], comment_order=[1, 2], sub_order=["TOOL1_A", "TOOL1_B"]), AS_OF)
    perm = C.parse_seed_work_request(
        _perm_request(entry_order=[2, 1], comment_order=[2, 1], sub_order=["TOOL1_B", "TOOL1_A"]), AS_OF)
    # normalized request, its content-address, and the PDAM advisory output are all
    # invariant to caller input permutation.
    assert ref == perm
    assert C.derive_request_digest(ref, AS_OF) == C.derive_request_digest(perm, AS_OF)
    assert _pdam_actions(ref, AS_OF) == _pdam_actions(perm, AS_OF)


def test_tied_comment_evidence_selection_deterministic():
    # Two comments that tie on the solver's evidence sort key (same human/repairish/
    # when) must select deterministically regardless of input order (canonical order
    # feeds the solver's stable sort + [:limit] truncation).
    from waggledance.core.seed_solver_adapter import _pdam_actions
    a = C.parse_seed_work_request(
        _perm_request(entry_order=[1], comment_order=[1, 2], sub_order=[]), AS_OF)
    b = C.parse_seed_work_request(
        _perm_request(entry_order=[1], comment_order=[2, 1], sub_order=[]), AS_OF)
    assert _pdam_actions(a, AS_OF) == _pdam_actions(b, AS_OF)


# --- item 5: generation length + aggregate snapshot byte cap -----------------
def test_snapshot_generation_over_length_rejected():
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snapshot(ident, root)
    snap["generation"] = "g" * (C.MAX_GENERATION_LEN + 1)
    with pytest.raises(SeedWorkContractError, match="generation"):
        C.parse_registry_snapshot(snap)


def test_snapshot_aggregate_byte_cap_precedes_verification(monkeypatch):
    # The aggregate byte cap must fire BEFORE per-member crypto verification: a
    # snapshot carrying an INVALID member but over the byte cap rejects on the cap,
    # not on the member -- proving the cheap guard runs before the expensive work.
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    forged = dict(ident); forged["cell_id"] = _S("0")
    snap = _snap_of([forged], [root])
    monkeypatch.setattr(C, "MAX_SNAPSHOT_BYTES", 32)
    with pytest.raises(SeedWorkContractError, match="canonical bytes"):
        C.parse_registry_snapshot(snap)


# --- item 6: result content address binds solver/lineage/generation fields ----
@pytest.mark.parametrize("field,value", [
    ("solver_contract_digest", _S("0")),
    ("solver_config_digest", _S("0")),
    ("lineage_entry_hash", _S("0")),
    ("registry_generation", "gen-EVIL"),
])
def test_result_content_address_binds_field(field, value):
    # Mutating a newly-bound field WITHOUT resealing invalidates result_digest: the
    # only check that rejects a still-shape-valid value here is the content-address
    # binding (before the fix these fields were outside the digest -> fail-open).
    _env, res = _result()
    bad = dict(res); bad[field] = value
    with pytest.raises(SeedWorkContractError, match="result_digest"):
        parse_seed_work_result(bad)


def test_result_empty_registry_generation_rejected():
    _env, res = _result()
    bad = dict(res); bad["registry_generation"] = ""
    with pytest.raises(SeedWorkContractError, match="registry_generation must be non-empty"):
        parse_seed_work_result(bad)


# --- item 1 (refinement): PRE-SOLVER result-output upper bound -----------------
def _env_from_request(req):
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snapshot(ident, root)
    env = build_seed_work_envelope(identity=ident, lineage=root, parent_lineage=None,
                                   lineage_proof=[root], registry_snapshot=snap,
                                   request_payload=req, as_of_utc=AS_OF)
    return env, snap


def test_estimate_bound_is_conservative_over_real_result():
    """The estimate must be a SOUND upper bound: for a request that actually
    exercises evidence lines AND limited-subtool notes, the input-derived bound is
    >= the canonical bytes of the real ResultV1 the solver produces."""
    from waggledance.core.magma.canonical import canonical_json_bytes
    # TOOL1 is DOWN with two LIMITED subtools + two comments -> action_text carries
    # both evidence and the limited note; TOOL2 is a plain PRODUCTION close.
    req = _perm_request(entry_order=[1, 2], comment_order=[1, 2], sub_order=["TOOL1_A", "TOOL1_B"])
    env, snap = _env_from_request(req)
    res = SeedSolverAdapterV1().execute(envelope=env, registry_snapshot=snap, as_of_utc=AS_OF)
    actual = len(canonical_json_bytes(res))
    bound = C.estimate_result_output_upper_bound(env["request_payload"])
    assert bound >= actual > 0


def test_estimate_bound_rejects_amplifier():
    """A valid max-cardinality request whose per-entry evidence reuse would amplify
    the ResultV1 past MAX_RESULT_BYTES has a bound over the cap, and the gate
    raises fail-closed."""
    ident = _identity()
    root = build_root_entry(cell_id=ident["cell_id"], inherited_goal_slice_digest=_S("d"),
                            inherited_budget_slice_digest=_S("e")).to_mapping()
    snap = _snapshot(ident, root)
    entries, timeline, tool_states, subtools = [], [], {"SHARED": {"tool_id": "SHARED", "state": "PRODUCTION"}}, {}
    for i in range(C.MAX_ENTRIES):
        dev = f"T{i}"
        e = {"entry_id": i + 1, "local_id": i + 1, "log_code": "LC", "device": dev,
             "status": "WIP", "created_at": "2026-07-24T05:00:00Z"}
        entries.append(dict(e)); timeline.append(dict(e))
        tool_states[dev] = {"tool_id": dev, "state": "PRODUCTION"}
        subtools[dev] = ["SHARED"]
    comments = [{"tool_id": "SHARED", "when": "2026-07-24T05:30:00Z", "by_user": "user",
                 "comment": "repair note " + "y" * 228} for _ in range(32)]
    req = {"schema_version": C.REQUEST_SCHEMA, "entries": entries, "repair_timeline": timeline,
           "tool_states": tool_states, "comments": comments, "subtools": subtools,
           "options": {"duplicate_window_hours": 24, "evidence_limit": 32, "max_open_window_days": 7}}
    parsed = C.parse_seed_work_request(req, AS_OF)
    assert C.estimate_result_output_upper_bound(parsed) > C.MAX_RESULT_BYTES
    with pytest.raises(SeedWorkContractError, match="upper bound"):
        C.require_result_output_within_bound(parsed)


def test_require_bound_accepts_ordinary_and_lean_bulk():
    """The gate accepts ordinary requests AND lean bulk (max-cardinality with small
    per-entry output): it keys on the output estimate, not the entry count."""
    parsed_small = C.parse_seed_work_request(_request(), AS_OF)
    assert C.estimate_result_output_upper_bound(parsed_small) <= C.MAX_RESULT_BYTES
    C.require_result_output_within_bound(parsed_small)  # no raise
    # Max-cardinality but lean: each entry on its own device, no comments/subtools.
    entries, timeline, tool_states, subtools = [], [], {}, {}
    for i in range(C.MAX_ENTRIES):
        dev = f"T{i}"
        e = {"entry_id": i + 1, "local_id": i + 1, "log_code": "LC", "device": dev,
             "status": "WIP", "created_at": "2026-07-24T05:00:00Z"}
        entries.append(dict(e)); timeline.append(dict(e))
        tool_states[dev] = {"tool_id": dev, "state": "PRODUCTION"}; subtools[dev] = []
    req = {"schema_version": C.REQUEST_SCHEMA, "entries": entries, "repair_timeline": timeline,
           "tool_states": tool_states, "comments": [], "subtools": subtools,
           "options": {"duplicate_window_hours": 24, "evidence_limit": 4, "max_open_window_days": 7}}
    parsed_lean = C.parse_seed_work_request(req, AS_OF)
    assert C.estimate_result_output_upper_bound(parsed_lean) <= C.MAX_RESULT_BYTES
    C.require_result_output_within_bound(parsed_lean)  # no raise


# --- item 7: solver/contract allowlists are DERIVED (no drift) ----------------
def test_solver_allowlist_conformance():
    import waggledance.core.pdam_close_solver as P
    # The contract allowlists are the solver's own vocabulary, not a private copy.
    assert C._STATUS_ALLOWLIST == frozenset(P.OPEN_STATUSES)
    assert C._TOOL_STATE_ALLOWLIST == frozenset(P.OK_LIKE_STATES | P.LIMITED_STATES)
    # derive_solver_contract_digest pins the ACTUAL solver behaviour: recompute the
    # digest straight from the solver's sets and require equality. If the solver's
    # status/state vocabulary drifts, this digest moves with it.
    expected = C.sha256_digest({
        "domain": C.SOLVER_CONTRACT_DOMAIN,
        "solver_id": C.SOLVER_ID,
        "request_schema": C.REQUEST_SCHEMA,
        "action_schema_version": C.ACTION_SCHEMA_VERSION,
        "request_keys": sorted(C.REQUEST_KEYS),
        "status_allowlist": sorted(P.OPEN_STATUSES),
        "tool_state_allowlist": sorted(P.OK_LIKE_STATES | P.LIMITED_STATES),
    })
    assert C.derive_solver_contract_digest() == expected
