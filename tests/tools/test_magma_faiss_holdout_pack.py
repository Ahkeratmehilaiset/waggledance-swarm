from __future__ import annotations

import copy
import hashlib
import runpy
from pathlib import Path
from typing import Any, Callable

import pytest

from tools import magma_faiss_holdout_frame as holdout_frame
from tools import magma_faiss_holdout_pack as holdout_pack
from tools import magma_faiss_holdout_protocol as holdout_protocol
from waggledance.core.hex_cell_topology import ALL_CELLS
from waggledance.core.magma.canonical import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
FRAME_FIXTURES = runpy.run_path(
    str(ROOT / "tests" / "tools" / "test_magma_faiss_holdout_frame.py")
)
SELECTION_SEED = FRAME_FIXTURES["SELECTION_SEED"]
FRAME_KEY = FRAME_FIXTURES["FRAME_KEY"]

CAPTURE_KEY = b"c" * 32
QUERY_KEY = b"q" * 32
RAW_A_KEY = b"a" * 32
RAW_B_KEY = b"b" * 32
ADJUDICATION_KEY = b"d" * 32
LABEL_KEY = b"l" * 32


class _StringSubclass(str):
    pass


class _DictSubclass(dict):
    pass


def _key_id(label: str) -> str:
    return "key_" + hashlib.sha256(label.encode("utf-8")).hexdigest()


CAPTURE_KEY_ID = _key_id("capture")
QUERY_KEY_ID = _key_id("query")
RAW_A_KEY_ID = _key_id("raw-a")
RAW_B_KEY_ID = _key_id("raw-b")
ADJUDICATION_KEY_ID = _key_id("adjudication")
LABEL_KEY_ID = _key_id("label")


def _raw_boundary() -> dict[str, Any]:
    return holdout_pack._no_authority_boundary(
        "raw_adjudication_declaration_only"
    )


def _fixture() -> dict[str, Any]:
    protocol, state, frame = FRAME_FIXTURES["_fixture"]()
    frame_commitment, selection = FRAME_FIXTURES["_selection"](
        protocol, state, frame
    )
    query_capture = holdout_pack.build_query_capture_projection(
        frame,
        protocol=protocol,
        preregistration_state=state,
        selection_projection=selection,
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=frame_commitment,
        capture_id_key=CAPTURE_KEY,
    )
    query_binding = holdout_pack.build_query_pack_binding(
        query_capture,
        frame=frame,
        protocol=protocol,
        preregistration_state=state,
        selection_projection=selection,
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=frame_commitment,
        capture_id_key=CAPTURE_KEY,
        capture_id_key_id=CAPTURE_KEY_ID,
        query_pack_key_id=QUERY_KEY_ID,
        declared_selection_completed_at_utc="2026-08-12T16:01:00Z",
        declared_query_pack_frozen_at_utc="2026-08-12T16:02:00Z",
    )
    query_commitment = holdout_pack.create_query_pack_commitment(
        query_capture,
        query_pack_binding=query_binding,
        protocol=protocol,
        query_pack_key=QUERY_KEY,
    )
    case_by_id = {
        case["opaque_case_id"]: case
        for case in [*frame["positive_cases"], *frame["ood_cases"]]
    }
    labels: list[dict[str, Any]] = []
    selected_cases_by_query = {
        case_by_id[case_id]["query"]: case_by_id[case_id]
        for case_id in selection["selected_case_ids"]
    }
    for query_row in query_capture["queries"]:
        case = selected_cases_by_query[query_row["query"]]
        labels.append(
            {
                "capture_id": query_row["capture_id"],
                "disposition": case["design_case_class"],
                "expected_solver_id": case["design_solver_id"],
                "expected_cell_id": case["design_cell_id"],
                "ood_class": (
                    None
                    if case["design_case_class"] == "positive"
                    else case["stratum"]
                ),
            }
        )
    raw_packs: list[dict[str, Any]] = []
    for actor_id, key_id in zip(
        frame["roles"]["adjudicator_actor_ids"],
        (RAW_A_KEY_ID, RAW_B_KEY_ID),
        strict=True,
    ):
        raw_packs.append(
            {
                "schema_version": holdout_pack.RAW_LABEL_PACK_SCHEMA,
                "protocol_digest": query_binding["protocol_digest"],
                "frame_commitment": query_binding["frame_commitment"],
                "selection_projection_digest": query_binding[
                    "selection_projection_digest"
                ],
                "query_pack_commitment": query_commitment,
                "adjudicator_actor_id": actor_id,
                "commitment_key_id": key_id,
                "declared_adjudication_started_at_utc": (
                    "2026-08-12T16:03:00Z"
                ),
                "declared_raw_pack_sealed_at_utc": (
                    "2026-08-12T16:04:00Z"
                ),
                "attestations": {
                    "query_pack_only_case_input": True,
                    "candidate_scores_unseen": True,
                    "peer_raw_label_plaintext_unseen_before_own_seal": True,
                    "design_cell_metadata_unseen": True,
                    "reconciliation_performed": False,
                    "post_selection_replacement_performed": False,
                },
                "label_count": len(labels),
                "labels": copy.deepcopy(labels),
                "capability_boundary": _raw_boundary(),
            }
        )
    return {
        "protocol": protocol,
        "state": state,
        "frame": frame,
        "frame_commitment": frame_commitment,
        "selection": selection,
        "query_capture": query_capture,
        "query_binding": query_binding,
        "query_commitment": query_commitment,
        "raw_packs": raw_packs,
        "raw_keys": {
            RAW_A_KEY_ID: RAW_A_KEY,
            RAW_B_KEY_ID: RAW_B_KEY,
        },
    }


@pytest.fixture(scope="module")
def bundle() -> dict[str, Any]:
    return _fixture()


def _seal(
    source: dict[str, Any],
    *,
    raw_packs: Any | None = None,
    raw_keys: Any | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    arguments = {
        "protocol": source["protocol"],
        "preregistration_state": source["state"],
        "selection_projection": source["selection"],
        "query_capture_projection": source["query_capture"],
        "query_pack_binding": source["query_binding"],
        "raw_label_packs": (
            source["raw_packs"] if raw_packs is None else raw_packs
        ),
        "raw_label_pack_keys": (
            source["raw_keys"] if raw_keys is None else raw_keys
        ),
        "selection_seed": SELECTION_SEED,
        "frame_commitment_key": FRAME_KEY,
        "expected_frame_commitment": source["frame_commitment"],
        "capture_id_key": CAPTURE_KEY,
        "query_pack_key": QUERY_KEY,
        "adjudication_key": ADJUDICATION_KEY,
        "adjudication_key_id": ADJUDICATION_KEY_ID,
        "label_pack_key": LABEL_KEY,
        "label_pack_key_id": LABEL_KEY_ID,
        "declared_agreement_checked_at_utc": "2026-08-12T16:05:00Z",
        "declared_pack_sealed_at_utc": "2026-08-12T16:06:00Z",
    }
    arguments.update(overrides)
    return holdout_pack.build_pack_seal_projection(
        source["frame"], **arguments
    )


def _walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if type(value) is dict:
        keys.update(value)
        for item in value.values():
            keys.update(_walk_keys(item))
    elif type(value) is list:
        for item in value:
            keys.update(_walk_keys(item))
    return keys


def test_valid_query_and_pack_seal_are_exact_and_non_authoritative(
    bundle: dict[str, Any],
) -> None:
    source = copy.deepcopy(bundle)
    seal = _seal(bundle)
    query_capture = bundle["query_capture"]

    assert bundle == source
    assert set(query_capture) == {
        "schema_version",
        "query_count",
        "queries",
        "capability_boundary",
    }
    assert len(query_capture["queries"]) == 257
    assert all(
        set(row) == {"capture_id", "query"}
        for row in query_capture["queries"]
    )
    assert all(
        row["capture_id"].startswith("capture_")
        for row in query_capture["queries"]
    )
    assert [row["capture_id"] for row in query_capture["queries"]] == sorted(
        row["capture_id"] for row in query_capture["queries"]
    )
    source_ids = set(bundle["selection"]["selected_case_ids"])
    assert source_ids.isdisjoint(
        row["capture_id"] for row in query_capture["queries"]
    )
    assert bundle["query_commitment"] == (
        "hmac-sha256:95a2b3ba93068e52dd0f69f15d57c50ff0335b386d94357699ec52efee858951"
    )
    assert seal["adjudication_commitment"] == (
        "hmac-sha256:fb3636f42da644e81b753fb0727b06294b8ce96e2a173be5927a2c5d977b4b8d"
    )
    assert seal["label_pack_commitment"] == (
        "hmac-sha256:572f24f532d49beaba75ac029edc4038512a0f8317438d35a7d3f44ff9deddf8"
    )
    assert seal["selection_attempt"] == 0
    assert seal["capture_not_started_attestation"] is True
    assert all(
        value is False
        for key, value in seal["capability_boundary"].items()
        if key != "artifact_class"
    )
    assert not {
        "queries",
        "labels",
        "query",
        "disposition",
        "expected_solver_id",
        "expected_cell_id",
        "ood_class",
    } & _walk_keys(seal)
    serialized = canonical_json_bytes(seal)
    for case in bundle["frame"]["positive_cases"][:2]:
        assert case["query"].encode("utf-8") not in serialized


def test_query_capture_exact_recomputation_rejects_label_metadata_and_order(
    bundle: dict[str, Any],
) -> None:
    mutations: list[dict[str, Any]] = []
    extra = copy.deepcopy(bundle["query_capture"])
    extra["queries"][0]["disposition"] = "positive"
    mutations.append(extra)
    changed = copy.deepcopy(bundle["query_capture"])
    changed["queries"][0]["query"] += " changed"
    mutations.append(changed)
    reordered = copy.deepcopy(bundle["query_capture"])
    reordered["queries"][0], reordered["queries"][1] = (
        reordered["queries"][1],
        reordered["queries"][0],
    )
    mutations.append(reordered)
    authority = copy.deepcopy(bundle["query_capture"])
    authority["capability_boundary"]["holdout_pack_created"] = True
    mutations.append(authority)
    nonbool_authority = copy.deepcopy(bundle["query_capture"])
    nonbool_authority["capability_boundary"]["runtime_authority_granted"] = 0
    mutations.append(nonbool_authority)
    oversized = copy.deepcopy(bundle["query_capture"])
    oversized["queries"][0]["query"] = "x" * (
        holdout_frame.MAX_QUERY_UTF8_BYTES + 1
    )
    mutations.append(oversized)

    for mutated in mutations:
        with pytest.raises(holdout_pack.HoldoutPackError):
            holdout_pack.validate_query_capture_projection(
                mutated,
                frame=bundle["frame"],
                protocol=bundle["protocol"],
                preregistration_state=bundle["state"],
                selection_projection=bundle["selection"],
                selection_seed=SELECTION_SEED,
                frame_commitment_key=FRAME_KEY,
                expected_frame_commitment=bundle["frame_commitment"],
                capture_id_key=CAPTURE_KEY,
            )


def test_private_query_binding_is_exact_and_recomputed(
    bundle: dict[str, Any],
) -> None:
    assert holdout_pack.validate_query_pack_binding(
        bundle["query_binding"],
        query_capture_projection=bundle["query_capture"],
        frame=bundle["frame"],
        protocol=bundle["protocol"],
        preregistration_state=bundle["state"],
        selection_projection=bundle["selection"],
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=bundle["frame_commitment"],
        capture_id_key=CAPTURE_KEY,
    ) == bundle["query_binding"]

    mutations = []
    changed_digest = copy.deepcopy(bundle["query_binding"])
    changed_digest["query_capture_projection_digest"] = "sha256:" + "0" * 64
    mutations.append(changed_digest)
    authority = copy.deepcopy(bundle["query_binding"])
    authority["capability_boundary"]["query_order_blinding_verified"] = True
    mutations.append(authority)
    for mutated in mutations:
        with pytest.raises(holdout_pack.HoldoutPackError):
            holdout_pack.validate_query_pack_binding(
                mutated,
                query_capture_projection=bundle["query_capture"],
                frame=bundle["frame"],
                protocol=bundle["protocol"],
                preregistration_state=bundle["state"],
                selection_projection=bundle["selection"],
                selection_seed=SELECTION_SEED,
                frame_commitment_key=FRAME_KEY,
                expected_frame_commitment=bundle["frame_commitment"],
                capture_id_key=CAPTURE_KEY,
            )


def test_query_projection_rejects_capture_collision_and_key_coercion(
    bundle: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        holdout_pack,
        "_capture_id",
        lambda *args, **kwargs: "capture_" + "0" * 64,
    )
    with pytest.raises(holdout_pack.HoldoutPackError, match="capture_id_collision"):
        holdout_pack.build_query_capture_projection(
            bundle["frame"],
            protocol=bundle["protocol"],
            preregistration_state=bundle["state"],
            selection_projection=bundle["selection"],
            selection_seed=SELECTION_SEED,
            frame_commitment_key=FRAME_KEY,
            expected_frame_commitment=bundle["frame_commitment"],
            capture_id_key=CAPTURE_KEY,
        )

    with pytest.raises(holdout_pack.HoldoutPackError):
        holdout_pack.create_query_pack_commitment(
            bundle["query_capture"],
            query_pack_binding=bundle["query_binding"],
            protocol=bundle["protocol"],
            query_pack_key=bytearray(QUERY_KEY),
        )


def test_raw_pack_input_order_is_canonical_and_does_not_change_seal(
    bundle: dict[str, Any],
) -> None:
    expected = _seal(bundle)
    permuted = copy.deepcopy(bundle["raw_packs"])
    permuted.reverse()
    for pack in permuted:
        pack["labels"].reverse()

    assert _seal(bundle, raw_packs=permuted) == expected


def test_each_raw_pack_is_independently_committed_before_comparison(
    bundle: dict[str, Any],
) -> None:
    seal = _seal(bundle)
    observed = {}
    for raw_pack in bundle["raw_packs"]:
        key_id = raw_pack["commitment_key_id"]
        observed[raw_pack["adjudicator_actor_id"]] = (
            holdout_pack.create_raw_label_pack_commitment(
                raw_pack,
                query_capture_projection=bundle["query_capture"],
                query_pack_binding=bundle["query_binding"],
                protocol=bundle["protocol"],
                query_pack_key=QUERY_KEY,
                raw_label_pack_key=bundle["raw_keys"][key_id],
            )
        )
    assert observed == {
        binding["adjudicator_actor_id"]: binding["hmac_commitment"]
        for binding in seal["raw_label_bindings"]
    }

    peer_mutation = copy.deepcopy(bundle["raw_packs"][1])
    _set_alternative_valid_label(peer_mutation["labels"][0])
    first = bundle["raw_packs"][0]
    assert holdout_pack.create_raw_label_pack_commitment(
        first,
        query_capture_projection=bundle["query_capture"],
        query_pack_binding=bundle["query_binding"],
        protocol=bundle["protocol"],
        query_pack_key=QUERY_KEY,
        raw_label_pack_key=RAW_A_KEY,
    ) == observed[first["adjudicator_actor_id"]]


def _set_alternative_valid_label(label: dict[str, Any]) -> None:
    if label["disposition"] == "positive":
        label.update(
            {
                "disposition": "ood",
                "expected_solver_id": None,
                "expected_cell_id": None,
                "ood_class": holdout_protocol.OOD_STRATA[0],
            }
        )
    else:
        label.update(
            {
                "disposition": "positive",
                "expected_solver_id": "wrong_solver",
                "expected_cell_id": ALL_CELLS[0],
                "ood_class": None,
            }
        )


def _make_one_disagreement(packs: list[dict[str, Any]]) -> None:
    _set_alternative_valid_label(packs[0]["labels"][0])


def _make_same_wrong_design(packs: list[dict[str, Any]]) -> None:
    for pack in packs:
        _set_alternative_valid_label(pack["labels"][0])


@pytest.mark.parametrize(
    ("label", "mutator", "error"),
    [
        (
            "disagreement",
            _make_one_disagreement,
            "disagreement",
        ),
        (
            "both_invalid",
            lambda packs: [
                pack["labels"][0].update(
                    {
                        "disposition": "invalid_or_ambiguous",
                        "expected_solver_id": None,
                        "expected_cell_id": None,
                        "ood_class": None,
                    }
                )
                for pack in packs
            ],
            "invalid_or_ambiguous",
        ),
        (
            "same_wrong_design",
            _make_same_wrong_design,
            "design_mismatch",
        ),
        (
            "missing_label",
            lambda packs: packs[0]["labels"].pop(),
            "label_count",
        ),
        (
            "duplicate_label",
            lambda packs: packs[0]["labels"].__setitem__(
                1, copy.deepcopy(packs[0]["labels"][0])
            ),
            "unique",
        ),
        (
            "unknown_actor",
            lambda packs: packs[0].__setitem__(
                "adjudicator_actor_id", "actor_" + "f" * 64
            ),
            "roster",
        ),
    ],
)
def test_raw_label_failures_are_terminal(
    bundle: dict[str, Any],
    label: str,
    mutator: Callable[[list[dict[str, Any]]], Any],
    error: str,
) -> None:
    del label
    mutated = copy.deepcopy(bundle["raw_packs"])
    mutator(mutated)
    if len(mutated[0]["labels"]) != mutated[0]["label_count"]:
        mutated[0]["label_count"] = len(mutated[0]["labels"])
    with pytest.raises(holdout_pack.HoldoutPackError, match=error):
        _seal(bundle, raw_packs=mutated)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("query_pack_only_case_input", False),
        ("candidate_scores_unseen", False),
        ("peer_raw_label_plaintext_unseen_before_own_seal", False),
        ("design_cell_metadata_unseen", False),
        ("reconciliation_performed", True),
        ("post_selection_replacement_performed", True),
    ],
)
def test_raw_pack_rejects_attestation_drift(
    bundle: dict[str, Any], key: str, value: bool
) -> None:
    mutated = copy.deepcopy(bundle["raw_packs"])
    mutated[0]["attestations"][key] = value
    with pytest.raises(holdout_pack.HoldoutPackError):
        _seal(bundle, raw_packs=mutated)


def test_raw_pack_rejects_conditional_label_and_cross_query_binding(
    bundle: dict[str, Any],
) -> None:
    malformed = copy.deepcopy(bundle["raw_packs"])
    ood_index = next(
        index
        for index, row in enumerate(malformed[0]["labels"])
        if row["disposition"] == "ood"
    )
    malformed[0]["labels"][ood_index]["expected_solver_id"] = "solver_00"
    wrong_query = copy.deepcopy(bundle["raw_packs"])
    wrong_query[0]["query_pack_commitment"] = "hmac-sha256:" + "0" * 64

    for mutated in (malformed, wrong_query):
        with pytest.raises(holdout_pack.HoldoutPackError):
            _seal(bundle, raw_packs=mutated)


def test_pack_rejects_key_material_and_identifier_reuse(
    bundle: dict[str, Any],
) -> None:
    reused_material = copy.deepcopy(bundle["raw_keys"])
    reused_material[RAW_A_KEY_ID] = QUERY_KEY
    duplicate_id = copy.deepcopy(bundle["raw_packs"])
    duplicate_id[0]["commitment_key_id"] = QUERY_KEY_ID
    duplicate_id_keys = {
        QUERY_KEY_ID: RAW_A_KEY,
        RAW_B_KEY_ID: RAW_B_KEY,
    }

    with pytest.raises(holdout_pack.HoldoutPackError, match="pairwise_distinct"):
        _seal(bundle, raw_keys=reused_material)
    with pytest.raises(holdout_pack.HoldoutPackError, match="distinct"):
        _seal(
            bundle,
            raw_packs=duplicate_id,
            raw_keys=duplicate_id_keys,
        )


def test_pack_chronology_is_declared_but_not_promoted_to_trusted_time(
    bundle: dict[str, Any],
) -> None:
    raw_before_query = copy.deepcopy(bundle["raw_packs"])
    raw_before_query[0]["declared_adjudication_started_at_utc"] = (
        "2026-08-12T16:01:00Z"
    )
    with pytest.raises(holdout_pack.HoldoutPackError, match="chronology"):
        _seal(bundle, raw_packs=raw_before_query)
    with pytest.raises(holdout_pack.HoldoutPackError, match="agreement_check"):
        _seal(
            bundle,
            declared_agreement_checked_at_utc="2026-08-12T16:03:00Z",
        )
    with pytest.raises(holdout_pack.HoldoutPackError, match="pack_seal"):
        _seal(
            bundle,
            declared_pack_sealed_at_utc="2026-08-12T16:04:00Z",
        )
    assert _seal(bundle)["capability_boundary"]["trusted_time_verified"] is False


def test_pack_seal_projection_requires_exact_recomputation(
    bundle: dict[str, Any],
) -> None:
    seal = _seal(bundle)
    assert holdout_pack.validate_pack_seal_projection(
        seal,
        frame=bundle["frame"],
        protocol=bundle["protocol"],
        preregistration_state=bundle["state"],
        selection_projection=bundle["selection"],
        query_capture_projection=bundle["query_capture"],
        query_pack_binding=bundle["query_binding"],
        raw_label_packs=bundle["raw_packs"],
        raw_label_pack_keys=bundle["raw_keys"],
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=bundle["frame_commitment"],
        capture_id_key=CAPTURE_KEY,
        query_pack_key=QUERY_KEY,
        adjudication_key=ADJUDICATION_KEY,
        label_pack_key=LABEL_KEY,
    ) == seal

    mutations = []
    extra = copy.deepcopy(seal)
    extra["labels"] = []
    mutations.append(extra)
    changed = copy.deepcopy(seal)
    changed["label_pack_commitment"] = "hmac-sha256:" + "0" * 64
    mutations.append(changed)
    authority = copy.deepcopy(seal)
    authority["capability_boundary"]["holdout_evidence_gate_met"] = True
    mutations.append(authority)
    nonbool = copy.deepcopy(seal)
    nonbool["capability_boundary"]["runtime_authority_granted"] = 0
    mutations.append(nonbool)
    swapped_key_ids = copy.deepcopy(seal)
    (
        swapped_key_ids["adjudication_key_id"],
        swapped_key_ids["label_pack_key_id"],
    ) = (
        swapped_key_ids["label_pack_key_id"],
        swapped_key_ids["adjudication_key_id"],
    )
    mutations.append(swapped_key_ids)
    changed_valid_seal_time = copy.deepcopy(seal)
    changed_valid_seal_time["declared_pack_sealed_at_utc"] = (
        "2026-08-12T16:07:00Z"
    )
    mutations.append(changed_valid_seal_time)

    for mutated in mutations:
        with pytest.raises(holdout_pack.HoldoutPackError):
            holdout_pack.validate_pack_seal_projection(
                mutated,
                frame=bundle["frame"],
                protocol=bundle["protocol"],
                preregistration_state=bundle["state"],
                selection_projection=bundle["selection"],
                query_capture_projection=bundle["query_capture"],
                query_pack_binding=bundle["query_binding"],
                raw_label_packs=bundle["raw_packs"],
                raw_label_pack_keys=bundle["raw_keys"],
                selection_seed=SELECTION_SEED,
                frame_commitment_key=FRAME_KEY,
                expected_frame_commitment=bundle["frame_commitment"],
                capture_id_key=CAPTURE_KEY,
                query_pack_key=QUERY_KEY,
                adjudication_key=ADJUDICATION_KEY,
                label_pack_key=LABEL_KEY,
            )


def test_query_commitment_is_domain_bound(bundle: dict[str, Any]) -> None:
    label_domain = holdout_protocol.create_hmac_commitment(
        "label_pack",
        key=QUERY_KEY,
        protocol_digest=bundle["query_binding"]["protocol_digest"],
        payload={
            "query_capture_projection": bundle["query_capture"],
            "query_pack_binding": bundle["query_binding"],
        },
    )
    assert label_domain != bundle["query_commitment"]


def test_query_and_raw_shapes_reject_container_and_string_subclasses(
    bundle: dict[str, Any],
) -> None:
    query_subclass = _DictSubclass(bundle["query_capture"])
    raw_subclass = copy.deepcopy(bundle["raw_packs"])
    raw_subclass[0]["labels"][0]["capture_id"] = _StringSubclass(
        raw_subclass[0]["labels"][0]["capture_id"]
    )

    with pytest.raises(holdout_pack.HoldoutPackError):
        holdout_pack.validate_query_capture_projection(
            query_subclass,
            frame=bundle["frame"],
            protocol=bundle["protocol"],
            preregistration_state=bundle["state"],
            selection_projection=bundle["selection"],
            selection_seed=SELECTION_SEED,
            frame_commitment_key=FRAME_KEY,
            expected_frame_commitment=bundle["frame_commitment"],
            capture_id_key=CAPTURE_KEY,
        )
    with pytest.raises(holdout_pack.HoldoutPackError):
        _seal(bundle, raw_packs=raw_subclass)


def test_class_coded_source_ids_do_not_reach_query_projection() -> None:
    protocol, state, frame = FRAME_FIXTURES["_fixture"]()
    for index, case in enumerate(frame["positive_cases"]):
        suffix = hashlib.sha256(f"positive-{index}".encode()).hexdigest()[1:]
        case["opaque_case_id"] = "case_0" + suffix
    for index, case in enumerate(frame["ood_cases"]):
        suffix = hashlib.sha256(f"ood-{index}".encode()).hexdigest()[1:]
        case["opaque_case_id"] = "case_f" + suffix
    frame_commitment, selection = FRAME_FIXTURES["_selection"](
        protocol, state, frame
    )
    query_capture = holdout_pack.build_query_capture_projection(
        frame,
        protocol=protocol,
        preregistration_state=state,
        selection_projection=selection,
        selection_seed=SELECTION_SEED,
        frame_commitment_key=FRAME_KEY,
        expected_frame_commitment=frame_commitment,
        capture_id_key=CAPTURE_KEY,
    )
    serialized = canonical_json_bytes(query_capture)
    assert b"case_0" not in serialized
    assert b"case_f" not in serialized
    assert all(
        row["capture_id"].startswith("capture_")
        for row in query_capture["queries"]
    )


def test_hmac_outputs_are_commitments_not_confidentiality_claims(
    bundle: dict[str, Any],
) -> None:
    seal = _seal(bundle)
    assert seal["label_pack_commitment"].startswith("hmac-sha256:")
    assert seal["capability_boundary"]["query_pack_custody_verified"] is False
    assert seal["capability_boundary"]["hmac_key_custody_verified"] is False
    assert (
        seal["capability_boundary"]["private_pack_confidentiality_verified"]
        is False
    )
    assert seal["capability_boundary"]["label_seal_publication_verified"] is False
    assert seal["capability_boundary"]["adjudication_authenticity_verified"] is False
    assert (
        seal["capability_boundary"][
            "adjudicator_model_family_independence_verified"
        ]
        is False
    )
    assert seal["capability_boundary"]["query_order_blinding_verified"] is False
    assert (
        seal["capability_boundary"]["semantic_label_correctness_verified"]
        is False
    )


@pytest.mark.parametrize(
    "capability",
    [
        "adjudicator_model_family_independence_verified",
        "query_order_blinding_verified",
        "hmac_key_custody_verified",
        "private_pack_confidentiality_verified",
        "semantic_label_correctness_verified",
    ],
)
@pytest.mark.parametrize("forged_value", [True, 0])
def test_explicit_external_capabilities_cannot_be_forged(
    bundle: dict[str, Any],
    capability: str,
    forged_value: Any,
) -> None:
    seal = _seal(bundle)
    seal["capability_boundary"][capability] = forged_value
    with pytest.raises(holdout_pack.HoldoutPackError):
        holdout_pack.validate_pack_seal_projection(
            seal,
            frame=bundle["frame"],
            protocol=bundle["protocol"],
            preregistration_state=bundle["state"],
            selection_projection=bundle["selection"],
            query_capture_projection=bundle["query_capture"],
            query_pack_binding=bundle["query_binding"],
            raw_label_packs=bundle["raw_packs"],
            raw_label_pack_keys=bundle["raw_keys"],
            selection_seed=SELECTION_SEED,
            frame_commitment_key=FRAME_KEY,
            expected_frame_commitment=bundle["frame_commitment"],
            capture_id_key=CAPTURE_KEY,
            query_pack_key=QUERY_KEY,
            adjudication_key=ADJUDICATION_KEY,
            label_pack_key=LABEL_KEY,
        )
