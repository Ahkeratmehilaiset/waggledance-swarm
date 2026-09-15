"""Fail-closed conformance tests for the dormant v4 profile truth contract."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Iterable

import jsonschema
import pytest
import yaml
from yaml.constructor import ConstructorError


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v4_0_0"
PROFILE_DIR = ROOT / "configs" / "profiles" / "v4"
CATALOG_DIR = ROOT / "configs" / "capabilities"
DOC_PATH = ROOT / "docs" / "architecture" / "V4_PROFILE_CAPABILITY_TRUTH_CONTRACT.md"

PROFILE_IDS = frozenset({"HOME", "COTTAGE", "FACTORY", "GADGET"})
STATE_IDS = frozenset(
    {
        "ABSENT",
        "UNPROVISIONED",
        "STARTING",
        "READY",
        "DEGRADED",
        "BLOCKED",
        "FAILED",
        "STALE",
        "STOPPED",
    }
)
FOUNDATION_IDS = frozenset(
    {
        "audit.chat_served_receipts",
        "audit.claim_safe_evaluator",
        "audit.receipt_chain_verifier",
        "audit.runtime_receipts",
        "routing.deterministic_solver_first",
        "safety.child_privacy",
        "safety.credential_non_exposure",
        "safety.deny_by_default",
        "safety.external_write_gate",
        "safety.redaction",
    }
)
RECEIPT_IDS = frozenset(
    {
        "audit.runtime_receipts",
        "audit.chat_served_receipts",
        "audit.receipt_chain_verifier",
    }
)
EVALUATOR_DEPENDENCIES = frozenset(
    {
        "audit.receipt_chain_verifier",
        "safety.child_privacy",
        "safety.credential_non_exposure",
        "safety.deny_by_default",
        "safety.external_write_gate",
        "safety.redaction",
    }
)
FULL_GATED_IDS = frozenset(
    {
        "autonomy.autofix",
        "autonomy.canary",
        "autonomy.eig2",
        "autonomy.high_risk_gated_pipeline",
        "autonomy.learned_tunnels",
        "autonomy.low_risk_autogrowth",
        "autonomy.query_api",
        "autonomy.stage2_execution",
        "autonomy.virtual_dimensions",
        "governance.write_rco",
        "mesh.dynamic_subdivision",
        "mesh.hex_2d",
        "mesh.ring_messaging",
        "providers.real_http",
        "retrieval.hybrid_faiss",
    }
)
EXPECTED_NA_CAPABILITIES = {
    "HOME": frozenset(),
    "COTTAGE": frozenset(),
    "FACTORY": frozenset(),
    "GADGET": frozenset({"providers.real_http", "retrieval.hybrid_faiss"}),
}
EXPECTED_GATED_IDS = {
    "HOME": FULL_GATED_IDS,
    "COTTAGE": FULL_GATED_IDS,
    "FACTORY": FULL_GATED_IDS,
    "GADGET": FULL_GATED_IDS
    - {"providers.real_http", "retrieval.hybrid_faiss"},
}
EXPECTED_NA_CATALOG_IDS = {
    "HOME": frozenset({"retrieve.semantic_search"}),
    "COTTAGE": frozenset(
        {"retrieve.semantic_search", "sense.camera_frigate", "sense.home_assistant"}
    ),
    "FACTORY": frozenset(
        {
            "retrieve.semantic_search",
            "sense.audio",
            "sense.camera_frigate",
            "sense.home_assistant",
        }
    ),
    "GADGET": frozenset(
        {
            "retrieve.semantic_search",
            "retrieve.vector_search",
            "sense.audio",
            "sense.camera_frigate",
            "sense.home_assistant",
            "solve.causal",
            "solve.neural_classifier",
            "verify.consensus",
        }
    ),
}
EXPECTED_DEPENDENCIES = {
    "audit.chat_served_receipts": frozenset(
        {"safety.credential_non_exposure", "safety.redaction"}
    ),
    "audit.claim_safe_evaluator": EVALUATOR_DEPENDENCIES,
    "audit.receipt_chain_verifier": frozenset(
        {"audit.chat_served_receipts", "audit.runtime_receipts"}
    ),
    "audit.runtime_receipts": frozenset(
        {"safety.credential_non_exposure", "safety.redaction"}
    ),
    "autonomy.autofix": frozenset({"autonomy.canary", "governance.write_rco"}),
    "autonomy.canary": frozenset({"autonomy.learned_tunnels"}),
    "autonomy.eig2": frozenset({"mesh.dynamic_subdivision"}),
    "autonomy.high_risk_gated_pipeline": frozenset(
        {"autonomy.stage2_execution", "safety.external_write_gate"}
    ),
    "autonomy.learned_tunnels": frozenset({"autonomy.virtual_dimensions"}),
    "autonomy.low_risk_autogrowth": frozenset(
        {"autonomy.canary", "safety.external_write_gate"}
    ),
    "autonomy.query_api": frozenset({"routing.deterministic_solver_first"}),
    "autonomy.stage2_execution": frozenset(
        {"governance.write_rco", "safety.external_write_gate"}
    ),
    "autonomy.virtual_dimensions": frozenset({"autonomy.eig2"}),
    "governance.write_rco": frozenset({"safety.external_write_gate"}),
    "mesh.dynamic_subdivision": frozenset({"mesh.ring_messaging"}),
    "mesh.hex_2d": frozenset({"routing.deterministic_solver_first"}),
    "mesh.ring_messaging": frozenset({"mesh.hex_2d"}),
    "packaging.production_container": frozenset(
        {"safety.credential_non_exposure"}
    ),
    "providers.real_http": frozenset(
        {
            "safety.credential_non_exposure",
            "safety.external_write_gate",
            "safety.redaction",
        }
    ),
    "retrieval.hybrid_faiss": frozenset({"routing.deterministic_solver_first"}),
    "routing.deterministic_solver_first": frozenset(
        {"safety.deny_by_default", "safety.redaction"}
    ),
    "safety.child_privacy": frozenset(),
    "safety.credential_non_exposure": frozenset(),
    "safety.deny_by_default": frozenset(),
    "safety.external_write_gate": frozenset(),
    "safety.redaction": frozenset(),
}
REQUIRED_INTEGRATIONS = {
    "HOME": frozenset(
        {"alerts", "electricity", "frigate", "home_assistant", "mqtt", "voice_audio", "weather"}
    ),
    "COTTAGE": frozenset(
        {"alerts", "electricity", "mqtt", "offline_queue", "voice_audio", "weather"}
    ),
    "FACTORY": frozenset(
        {"alertmanager", "device_enrollment", "mqtt", "otel", "prometheus", "watchdogs"}
    ),
    "GADGET": frozenset(
        {"local_model", "mqtt", "power_loss_recovery", "sensors", "signed_ota"}
    ),
}
BUDGETS = {
    "GADGET": (128, 2, 32, 4096),
    "COTTAGE": (256, 4, 64, 8192),
    "HOME": (1024, 8, 100, 8192),
    "FACTORY": (4096, 16, 200, 16384),
}
COMMON_REQUIRED_PROBES = frozenset(
    {
        "backup_restore",
        "clock_sync",
        "config_schema",
        "hardware_readiness",
        "integration_readiness",
        "receipt_chain",
        "required_capabilities",
        "secret_non_exposure",
        "unexpected_vector_backend_reachability",
    }
)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe loader that refuses duplicate YAML mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _strict_json_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_json_object,
    )
    assert isinstance(value, dict)
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    assert isinstance(value, dict)
    return value


def _profile_schema() -> dict[str, Any]:
    return _load_json(SCHEMA_DIR / "profile_manifest.schema.json")


def _state_schema() -> dict[str, Any]:
    return _load_json(SCHEMA_DIR / "capability_state.schema.json")


def _profile_validator() -> jsonschema.Draft7Validator:
    return jsonschema.Draft7Validator(_profile_schema())


def _state_validator() -> jsonschema.Draft7Validator:
    return jsonschema.Draft7Validator(
        _state_schema(),
        format_checker=jsonschema.FormatChecker(),
    )


def _manifests() -> dict[str, dict[str, Any]]:
    paths = sorted(PROFILE_DIR.glob("*.yaml"))
    loaded: dict[str, dict[str, Any]] = {}
    for path in paths:
        manifest = _load_yaml(path)
        profile_id = manifest["profile_id"]
        assert profile_id not in loaded, "duplicate profile_id across v4 manifests"
        loaded[profile_id] = manifest
    return loaded


def _catalog_ids_from_current_sources() -> frozenset[str]:
    ids: list[str] = []
    for path in sorted(CATALOG_DIR.glob("*.yaml")):
        document = _load_yaml(path)
        for item in document["capabilities"]:
            ids.append(item["id"])
    assert len(ids) == len(set(ids)), "duplicate capability ID in current catalog"
    return frozenset(ids)


def _schema_inventory(definition: str) -> frozenset[str]:
    return frozenset(_profile_schema()["definitions"][definition]["required"])


def _state_reason(prefix: str, state: str) -> str:
    return f"{prefix}.{state.lower()}"


def _component_observation(target: dict[str, Any], prefix: str) -> dict[str, Any]:
    state = target["initial_state"]
    return {
        "classification": target["classification"],
        "enabled_by_default": target["enabled_by_default"],
        "release_required": target["release_required"],
        "state": state,
        "ready": False,
        "artifact_digest": None,
        "reason_code": _state_reason(prefix, state),
    }


def _minimal_state_snapshot(manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    if manifest is None:
        manifest = _manifests()["HOME"]
    capabilities = {}
    for capability_id, target in manifest["capabilities"].items():
        state = target["initial_state"]
        capabilities[capability_id] = {
            "classification": target["classification"],
            "enabled_by_default": target["enabled_by_default"],
            "release_required": target["release_required"],
            "readiness_phase": target["readiness_phase"],
            "activation_requires_claim_safe": target["activation_requires_claim_safe"],
            "dependencies": list(target["dependencies"]),
            "state": state,
            "servable": False,
            "dependencies_ready": False,
            "receipts_complete": False,
            "artifact_digest": None,
            "reason_code": _state_reason("capability", state),
        }
    catalog = {}
    for catalog_id, target in manifest["catalog_crosswalk"].items():
        projected = {
            "classification": target["classification"],
            "enabled_by_default": target["classification"] == "required",
            "release_required": target["classification"] == "required",
            "initial_state": (
                "UNPROVISIONED" if target["classification"] == "required" else "ABSENT"
            ),
        }
        catalog[catalog_id] = _component_observation(projected, "catalog")
    return {
        "schema_version": "waggledance.capability_state.p0.v1",
        "release_target": "4.0.0",
        "activation_authority": "none",
        "profile_id": manifest["profile_id"],
        "source_head_sha": "a" * 40,
        "manifest_digest": "b" * 64,
        "config_digest": "c" * 64,
        "observed_at_utc": "2026-08-31T00:00:00Z",
        "supervisor_live": True,
        "foundation_ready": False,
        "profile_state": "UNPROVISIONED",
        "profile_ready": False,
        "claim_safe": False,
        "receipt_evidence": {
            "window_id": None,
            "source_head_sha": None,
            "served_count": 0,
            "runtime_covered_count": 0,
            "chat_covered_count": 0,
            "gap_count": 0,
            "chain_head_digest": None,
            "evidence_digest": None,
            "complete": False,
        },
        "capabilities": capabilities,
        "catalog_capabilities": catalog,
        "integrations": {
            item_id: _component_observation(target, "integration")
            for item_id, target in manifest["integrations"].items()
        },
        "probes": {
            item_id: _component_observation(target, "probe")
            for item_id, target in manifest["probes"].items()
        },
        "blockers": ["profile.contract_unwired"],
    }


def _assert_invalid(validator: jsonschema.Draft7Validator, value: object) -> None:
    assert list(validator.iter_errors(value)), "adversarial value unexpectedly valid"


def _assert_component_projection(
    observations: dict[str, Any],
    targets: dict[str, Any],
    prefix: str,
) -> None:
    assert set(observations) == set(targets)
    for item_id, target in targets.items():
        observation = observations[item_id]
        assert observation["classification"] == target["classification"]
        assert observation["enabled_by_default"] is target["enabled_by_default"]
        assert observation["release_required"] is target["release_required"]
        assert observation["state"] == target["initial_state"]
        assert observation["reason_code"] == _state_reason(
            prefix, target["initial_state"]
        )


def _assert_catalog_projection(
    observations: dict[str, Any], manifest: dict[str, Any]
) -> None:
    profile_id = manifest["profile_id"]
    assert set(observations) == _catalog_ids_from_current_sources()
    for catalog_id, observation in observations.items():
        expected_na = catalog_id in EXPECTED_NA_CATALOG_IDS[profile_id]
        expected_classification = "not_applicable" if expected_na else "required"
        expected_state = "ABSENT" if expected_na else "UNPROVISIONED"
        assert observation["classification"] == expected_classification
        assert observation["enabled_by_default"] is (not expected_na)
        assert observation["release_required"] is (not expected_na)
        assert observation["state"] == expected_state
        assert observation["reason_code"] == _state_reason("catalog", expected_state)


def _assert_snapshot_matches_manifest(
    snapshot: dict[str, Any], manifest: dict[str, Any]
) -> None:
    assert snapshot["profile_id"] == manifest["profile_id"]
    assert set(snapshot["capabilities"]) == set(manifest["capabilities"])
    for capability_id, target in manifest["capabilities"].items():
        observation = snapshot["capabilities"][capability_id]
        for key in (
            "classification",
            "enabled_by_default",
            "release_required",
            "readiness_phase",
            "activation_requires_claim_safe",
            "dependencies",
        ):
            assert observation[key] == target[key]
        assert observation["state"] == target["initial_state"]
        assert observation["reason_code"] == _state_reason(
            "capability", target["initial_state"]
        )
    _assert_catalog_projection(snapshot["catalog_capabilities"], manifest)
    _assert_component_projection(snapshot["integrations"], manifest["integrations"], "integration")
    _assert_component_projection(snapshot["probes"], manifest["probes"], "probe")


def _assert_manifest_semantics(manifest: dict[str, Any]) -> None:
    profile_id = manifest["profile_id"]
    capabilities = manifest["capabilities"]
    assert set(capabilities) == _schema_inventory("capability_targets")
    assert set(manifest["integrations"]) == _schema_inventory("integration_targets")
    assert set(manifest["probes"]) == _schema_inventory("probe_targets")
    assert set(manifest["catalog_crosswalk"]) == _catalog_ids_from_current_sources()

    phase_rank = {"foundation": 0, "release": 1}
    for capability_id, target in capabilities.items():
        expected_na = capability_id in EXPECTED_NA_CAPABILITIES[profile_id]
        assert target["classification"] == (
            "not_applicable" if expected_na else "required"
        )
        assert target["activation_requires_claim_safe"] is (
            capability_id in EXPECTED_GATED_IDS[profile_id]
        )
        expected_dependencies = (
            frozenset() if expected_na else EXPECTED_DEPENDENCIES[capability_id]
        )
        assert frozenset(target["dependencies"]) == expected_dependencies
        if target["classification"] == "not_applicable":
            assert target["initial_state"] == "ABSENT"
            assert target["dependencies"] == []
            assert target["readiness_phase"] == "excluded"
            assert target["activation_requires_claim_safe"] is False
            continue
        assert target["initial_state"] == "UNPROVISIONED"
        assert capability_id not in target["dependencies"]
        for dependency in target["dependencies"]:
            dependency_target = capabilities[dependency]
            assert dependency_target["classification"] == "required"
            assert phase_rank[dependency_target["readiness_phase"]] <= phase_rank[target["readiness_phase"]]

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(capability_id: str) -> None:
        if capability_id in visited:
            return
        assert capability_id not in visiting, f"dependency cycle at {capability_id}"
        visiting.add(capability_id)
        for dependency in capabilities[capability_id]["dependencies"]:
            visit(dependency)
        visiting.remove(capability_id)
        visited.add(capability_id)

    for capability_id in capabilities:
        visit(capability_id)

    policy = manifest["claim_safe_policy"]
    assert frozenset(
        capability_id
        for capability_id, target in capabilities.items()
        if target["readiness_phase"] == "foundation"
    ) == FOUNDATION_IDS
    assert frozenset(policy["foundation_capabilities"]) == FOUNDATION_IDS
    assert frozenset(policy["receipt_capabilities"]) == RECEIPT_IDS
    assert frozenset(capabilities["audit.runtime_receipts"]["dependencies"]) == {
        "safety.credential_non_exposure",
        "safety.redaction",
    }
    assert frozenset(capabilities["audit.chat_served_receipts"]["dependencies"]) == {
        "safety.credential_non_exposure",
        "safety.redaction",
    }
    assert frozenset(capabilities["audit.receipt_chain_verifier"]["dependencies"]) == {
        "audit.chat_served_receipts",
        "audit.runtime_receipts",
    }
    assert frozenset(
        capabilities["audit.claim_safe_evaluator"]["dependencies"]
    ) == EVALUATOR_DEPENDENCIES
    assert frozenset(policy["gated_capabilities"]) == EXPECTED_GATED_IDS[profile_id]
    assert frozenset(policy["gated_capabilities"]) == frozenset(
        capability_id
        for capability_id, target in capabilities.items()
        if target["activation_requires_claim_safe"]
    )
    assert all(
        capabilities[capability_id]["classification"] == "required"
        for capability_id in policy["foundation_capabilities"]
    )

    for catalog_id, target in manifest["catalog_crosswalk"].items():
        assert target["classification"] == (
            "not_applicable"
            if catalog_id in EXPECTED_NA_CATALOG_IDS[profile_id]
            else "required"
        )
        if target["classification"] == "required":
            assert target["target_kind"] == "catalog_capability"
            assert target["target_id"] == catalog_id
        elif target["target_kind"] == "migration_only":
            assert target["target_id"] == catalog_id
        else:
            assert target["target_kind"] == "none"
            assert target["target_id"] == "none"
    semantic_search = manifest["catalog_crosswalk"]["retrieve.semantic_search"]
    assert semantic_search["classification"] == "not_applicable"
    if profile_id == "GADGET":
        assert semantic_search["target_kind"] == "none"
        assert semantic_search["target_id"] == "none"
    else:
        assert semantic_search["target_kind"] == "migration_only"
        assert semantic_search["target_id"] == "retrieve.semantic_search"


def test_v4_schemas_are_valid_draft7_and_strict_json() -> None:
    for schema in (_profile_schema(), _state_schema()):
        jsonschema.Draft7Validator.check_schema(schema)


def test_duplicate_mapping_keys_are_rejected(tmp_path: Path) -> None:
    duplicate_json = tmp_path / "duplicate.json"
    duplicate_json.write_text('{"key": 1, "key": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        _load_json(duplicate_json)

    duplicate_yaml = tmp_path / "duplicate.yaml"
    duplicate_yaml.write_text("key: 1\nkey: 2\n", encoding="utf-8")
    with pytest.raises(ConstructorError, match="duplicate key"):
        _load_yaml(duplicate_yaml)


def test_schema_inventories_match_and_catalog_is_current() -> None:
    profile_schema = _profile_schema()
    state_schema = _state_schema()
    capability_ids = _schema_inventory("capability_targets")
    integration_ids = _schema_inventory("integration_targets")
    probe_ids = _schema_inventory("probe_targets")
    catalog_ids = _catalog_ids_from_current_sources()

    assert len(capability_ids) == 26
    assert len(integration_ids) == 17
    assert len(probe_ids) == 10
    assert len(catalog_ids) == 27
    assert set(profile_schema["definitions"]["capability_id"]["enum"]) == capability_ids
    assert set(profile_schema["definitions"]["catalog_id"]["enum"]) == catalog_ids
    assert set(state_schema["definitions"]["capability_id"]["enum"]) == capability_ids
    assert set(state_schema["definitions"]["catalog_id"]["enum"]) == catalog_ids


def test_exact_stable_profile_set_and_apiary_has_no_manifest() -> None:
    manifests = _manifests()
    assert set(manifests) == PROFILE_IDS
    assert not (PROFILE_DIR / "apiary.yaml").exists()
    for path in PROFILE_DIR.glob("*.yaml"):
        assert _load_yaml(path)["profile_id"] == path.stem.upper()
    assert all(
        manifest["excluded_profiles"] == ["APIARY"] for manifest in manifests.values()
    )


def test_every_manifest_validates_is_dormant_and_semantically_closed() -> None:
    validator = _profile_validator()
    for profile_id, manifest in _manifests().items():
        validator.validate(manifest)
        _assert_manifest_semantics(manifest)
        assert manifest["profile_id"] == profile_id
        assert manifest["contract_status"] == "target_state_unwired"
        assert manifest["runtime_wiring"] is False
        assert manifest["claim_safe_policy"]["initial_value"] is False
        assert manifest["claim_safe_policy"]["activation_authority"] == "none"


def test_manifest_schema_rejects_authority_fallback_and_unknowns() -> None:
    validator = _profile_validator()
    home = _manifests()["HOME"]

    mutations = (
        (("profile_id",), "APIARY"),
        (("runtime_wiring",), True),
        (("claim_safe_policy", "activation_authority"), "lead"),
        (("vector_policy", "backend"), "chroma"),
        (("vector_policy", "fallback"), "inmemory"),
        (("vector_policy", "chroma_serving_allowed"), True),
    )
    for path, value in mutations:
        forged = deepcopy(home)
        target: Any = forged
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        _assert_invalid(validator, forged)

    forged = deepcopy(home)
    forged["capabilities"]["safety.redaction"]["silent_fallback"] = True
    _assert_invalid(validator, forged)

    coherent_wrong_home_backend = deepcopy(home)
    coherent_wrong_home_backend["vector_policy"].update(
        backend="none",
        chroma_mode="none",
        migration_source_read_only=False,
        migration_target="none",
    )
    _assert_invalid(validator, coherent_wrong_home_backend)

    coherent_wrong_gadget_backend = deepcopy(_manifests()["GADGET"])
    coherent_wrong_gadget_backend["vector_policy"].update(
        backend="faiss",
        chroma_mode="migration_only",
        migration_source_read_only=True,
        migration_target="faiss",
    )
    _assert_invalid(validator, coherent_wrong_gadget_backend)


def test_required_na_and_dependency_semantics_fail_closed() -> None:
    home = _manifests()["HOME"]

    wrong_target = deepcopy(home)
    wrong_target["catalog_crosswalk"]["solve.math"]["target_id"] = "solve.stats"
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(wrong_target)

    gate_removed = deepcopy(home)
    gate_removed["capabilities"]["autonomy.high_risk_gated_pipeline"][
        "activation_requires_claim_safe"
    ] = False
    gate_removed["claim_safe_policy"]["gated_capabilities"].remove(
        "autonomy.high_risk_gated_pipeline"
    )
    _assert_invalid(_profile_validator(), gate_removed)
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(gate_removed)

    dependency_removed = deepcopy(home)
    dependency_removed["capabilities"]["autonomy.high_risk_gated_pipeline"][
        "dependencies"
    ] = []
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(dependency_removed)

    foundation_gated = deepcopy(home)
    foundation_gated["capabilities"]["safety.redaction"][
        "activation_requires_claim_safe"
    ] = True
    foundation_gated["claim_safe_policy"]["gated_capabilities"].append(
        "safety.redaction"
    )
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(foundation_gated)

    downgraded = deepcopy(home)
    target = downgraded["capabilities"]["autonomy.high_risk_gated_pipeline"]
    target.update(
        classification="not_applicable",
        enabled_by_default=False,
        release_required=False,
        initial_state="ABSENT",
        readiness_phase="excluded",
        activation_requires_claim_safe=False,
        dependencies=[],
        rationale="This forged downgrade must not pass the independent contract.",
    )
    downgraded["claim_safe_policy"]["gated_capabilities"].remove(
        "autonomy.high_risk_gated_pipeline"
    )
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(downgraded)

    catalog_downgraded = deepcopy(home)
    catalog_downgraded["catalog_crosswalk"]["solve.math"] = {
        "classification": "not_applicable",
        "target_kind": "none",
        "target_id": "none",
        "rationale": "This forged catalog downgrade must fail the expected matrix.",
    }
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(catalog_downgraded)

    phase_inversion = deepcopy(home)
    phase_inversion["capabilities"]["routing.deterministic_solver_first"][
        "dependencies"
    ] = ["mesh.hex_2d"]
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(phase_inversion)

    cycle = deepcopy(home)
    cycle["capabilities"]["mesh.hex_2d"]["dependencies"] = ["mesh.ring_messaging"]
    with pytest.raises(AssertionError):
        _assert_manifest_semantics(cycle)


def test_profile_integrations_and_probes_are_exact() -> None:
    integration_ids = _schema_inventory("integration_targets")
    probe_ids = _schema_inventory("probe_targets")
    for profile_id, manifest in _manifests().items():
        required_integrations = frozenset(
            item_id
            for item_id, target in manifest["integrations"].items()
            if target["classification"] == "required"
        )
        assert required_integrations == REQUIRED_INTEGRATIONS[profile_id]
        assert set(manifest["integrations"]) == integration_ids

        required_probes = frozenset(
            item_id
            for item_id, target in manifest["probes"].items()
            if target["classification"] == "required"
        )
        expected_probes = COMMON_REQUIRED_PROBES
        if profile_id == "GADGET":
            expected_probes |= {"unexpected_remote_provider_reachability"}
        assert required_probes == expected_probes
        assert set(manifest["probes"]) == probe_ids


def test_gadget_carveouts_have_no_vector_or_remote_provider_authority() -> None:
    gadget = _manifests()["GADGET"]
    assert gadget["vector_policy"] == {
        "backend": "none",
        "fallback": "none",
        "authoritative_only_when_ready": True,
        "chroma_mode": "none",
        "chroma_serving_allowed": False,
        "migration_source_read_only": False,
        "migration_target": "none",
    }
    for capability_id in ("retrieval.hybrid_faiss", "providers.real_http"):
        target = gadget["capabilities"][capability_id]
        assert target["classification"] == "not_applicable"
        assert target["initial_state"] == "ABSENT"
        assert capability_id not in gadget["claim_safe_policy"]["gated_capabilities"]
    assert gadget["probes"]["unexpected_vector_backend_reachability"]["classification"] == "required"
    assert gadget["probes"]["unexpected_remote_provider_reachability"]["classification"] == "required"
    assert gadget["integrations"]["local_model"]["classification"] == "required"
    assert gadget["catalog_crosswalk"]["explain.llm_reasoning"]["classification"] == "required"
    assert gadget["catalog_crosswalk"]["verify.hallucination"]["classification"] == "required"


def test_full_profiles_use_faiss_and_chroma_is_migration_only() -> None:
    for profile_id in ("HOME", "COTTAGE", "FACTORY"):
        policy = _manifests()[profile_id]["vector_policy"]
        assert policy == {
            "backend": "faiss",
            "fallback": "none",
            "authoritative_only_when_ready": True,
            "chroma_mode": "migration_only",
            "chroma_serving_allowed": False,
            "migration_source_read_only": True,
            "migration_target": "faiss",
        }


def test_resource_budgets_match_approved_target_envelopes_and_are_monotonic() -> None:
    manifests = _manifests()
    keys = ("max_memory_mb", "max_replay_concurrency", "l1_prefetch_k", "l3_elevated_budget")
    for profile_id, expected in BUDGETS.items():
        assert tuple(manifests[profile_id]["budgets"][key] for key in keys) == expected
    order = ("GADGET", "COTTAGE", "HOME", "FACTORY")
    for key in keys:
        values = [manifests[profile_id]["budgets"][key] for profile_id in order]
        assert values == sorted(values)


def test_digest_and_receipt_policies_are_exact() -> None:
    for manifest in _manifests().values():
        assert manifest["digest_policy"] == {
            "algorithm": "sha256",
            "manifest_preimage": "exact_utf8_file_bytes",
            "config_preimage": "canonical_json_rfc8785",
            "snapshot_preimage": "canonical_json_rfc8785",
            "artifact_preimage": "exact_artifact_bytes",
            "source_binding": "exact_git_head",
        }
        assert manifest["receipt_evidence_policy"] == {
            "window_id_required": True,
            "exact_head_required": True,
            "served_count_equals_runtime_covered_count": True,
            "served_count_equals_chat_covered_count": True,
            "gap_count": 0,
            "chain_head_digest_required": True,
            "evidence_digest_required": True,
        }


def test_initial_snapshots_validate_and_exactly_project_manifests() -> None:
    validator = _state_validator()
    for manifest in _manifests().values():
        snapshot = _minimal_state_snapshot(manifest)
        validator.validate(snapshot)
        _assert_snapshot_matches_manifest(snapshot, manifest)
        assert snapshot["profile_ready"] is False
        assert snapshot["foundation_ready"] is False
        assert snapshot["claim_safe"] is False
        assert snapshot["receipt_evidence"]["complete"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("activation_authority", "lead"),
        ("foundation_ready", True),
        ("profile_state", "READY"),
        ("profile_ready", True),
        ("claim_safe", True),
    ),
)
def test_p0_snapshot_cannot_assert_activation_or_readiness(field: str, value: Any) -> None:
    snapshot = _minimal_state_snapshot()
    snapshot[field] = value
    _assert_invalid(_state_validator(), snapshot)


def test_p0_snapshot_rejects_missing_unknown_and_extra_inventory() -> None:
    validator = _state_validator()
    base = _minimal_state_snapshot()
    for collection in ("capabilities", "catalog_capabilities", "integrations", "probes"):
        missing = deepcopy(base)
        missing[collection].pop(next(iter(missing[collection])))
        _assert_invalid(validator, missing)

        unknown = deepcopy(base)
        unknown[collection]["unknown.component"] = deepcopy(next(iter(unknown[collection].values())))
        _assert_invalid(validator, unknown)


def test_p0_blockers_are_blocking_codes_and_always_include_contract_unwired() -> None:
    validator = _state_validator()
    snapshot = _minimal_state_snapshot()

    non_blocker = deepcopy(snapshot)
    non_blocker["blockers"] = ["profile.contract_unwired", "capability.ready"]
    _assert_invalid(validator, non_blocker)

    missing_unwired = deepcopy(snapshot)
    missing_unwired["blockers"] = ["profile.required_component_not_ready"]
    _assert_invalid(validator, missing_unwired)


def test_snapshot_projection_rejects_manifest_and_reason_drift() -> None:
    manifest = _manifests()["HOME"]
    snapshot = _minimal_state_snapshot(manifest)

    classification_drift = deepcopy(snapshot)
    classification_drift["capabilities"]["safety.redaction"]["classification"] = "not_applicable"
    with pytest.raises(AssertionError):
        _assert_snapshot_matches_manifest(classification_drift, manifest)

    dependency_drift = deepcopy(snapshot)
    dependency_drift["capabilities"]["routing.deterministic_solver_first"]["dependencies"] = []
    with pytest.raises(AssertionError):
        _assert_snapshot_matches_manifest(dependency_drift, manifest)

    reason_drift = deepcopy(snapshot)
    reason_drift["probes"]["backup_restore"]["reason_code"] = "probe.failed"
    _assert_invalid(_state_validator(), reason_drift)
    with pytest.raises(AssertionError):
        _assert_snapshot_matches_manifest(reason_drift, manifest)


def test_state_invariants_keep_nonready_and_gated_items_unservable() -> None:
    validator = _state_validator()
    snapshot = _minimal_state_snapshot()

    nonready = deepcopy(snapshot)
    nonready["capabilities"]["safety.redaction"]["servable"] = True
    _assert_invalid(validator, nonready)

    missing_dependencies = deepcopy(snapshot)
    item = missing_dependencies["capabilities"]["safety.redaction"]
    item.update(
        state="READY",
        servable=True,
        dependencies_ready=False,
        artifact_digest="d" * 64,
        reason_code="capability.ready",
    )
    _assert_invalid(validator, missing_dependencies)

    gated = deepcopy(snapshot)
    item = gated["capabilities"]["mesh.hex_2d"]
    item.update(
        state="READY",
        servable=True,
        dependencies_ready=True,
        artifact_digest="e" * 64,
        reason_code="capability.ready",
    )
    _assert_invalid(validator, gated)


def test_ready_capability_requires_an_exact_artifact_digest() -> None:
    validator = _state_validator()
    sealed = _minimal_state_snapshot()
    item = sealed["capabilities"]["safety.redaction"]
    item.update(
        state="READY",
        servable=True,
        dependencies_ready=True,
        artifact_digest="f" * 64,
        reason_code="capability.ready",
    )
    validator.validate(sealed)

    unsealed = deepcopy(sealed)
    unsealed["capabilities"]["safety.redaction"]["artifact_digest"] = None
    _assert_invalid(validator, unsealed)


@pytest.mark.parametrize(("collection", "item_id"), (("integrations", "mqtt"), ("probes", "backup_restore")))
def test_failed_required_component_is_recorded_without_p0_authority(
    collection: str, item_id: str
) -> None:
    snapshot = _minimal_state_snapshot()
    item = snapshot[collection][item_id]
    prefix = "integration" if collection == "integrations" else "probe"
    item.update(state="FAILED", ready=False, reason_code=f"{prefix}.failed")
    _state_validator().validate(snapshot)
    assert snapshot["profile_ready"] is False
    assert snapshot["claim_safe"] is False


def test_closed_state_and_reason_registries() -> None:
    definitions = _state_schema()["definitions"]
    assert set(definitions["capability_state"]["enum"]) == STATE_IDS
    reason_codes = set(definitions["reason_code"]["enum"])
    for prefix in ("capability", "catalog", "integration", "probe"):
        assert {f"{prefix}.{state.lower()}" for state in STATE_IDS} <= reason_codes
    assert {
        "profile.contract_unwired",
        "profile.required_component_not_ready",
        "profile.supervisor_stopped",
    } <= reason_codes


def test_contract_document_is_explicitly_non_authorizing_target_state() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    lowered = text.lower()
    for phrase in (
        "target-state source material",
        "not the current runtime",
        "does not grant",
        "target_state_unwired",
        "claim_safe",
        "operator-explicit",
        "migration_only",
        "canonical_json_rfc8785",
        "catalog_capability",
    ):
        assert phrase in lowered
    for forbidden in ("ready in production", "default served", "signed gadget envelope"):
        assert forbidden not in lowered


def test_contract_paths_are_not_autoloaded_by_runtime_or_tools() -> None:
    needles = ("configs/profiles/v4", "schemas/v4_0_0")
    for source_root in (ROOT / "waggledance", ROOT / "tools"):
        for path in source_root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="strict").replace("\\", "/")
            assert not any(needle in text for needle in needles), path.relative_to(ROOT)
