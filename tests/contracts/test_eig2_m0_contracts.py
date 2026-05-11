# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_eig2_config_defaults_are_conservative() -> None:
    path = ROOT / "configs" / "explosive_intelligence_growth_v2.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    cfg = data["explosive_intelligence_growth_v2"]
    assert cfg["enabled"] is False
    assert cfg["implemented"] is True
    assert cfg["enable_requires_profile_or_test_flag"] is True
    assert cfg["production_default"] == "hex2d_sparse_tunnels"
    assert cfg["topology"]["base"] == "hex2d"
    assert cfg["topology"]["virtual_3d_enabled"] is False
    assert cfg["topology"]["virtual_4d_enabled"] is False
    assert cfg["safety"]["no_llm_hot_path"] is True
    assert cfg["safety"]["preserve_raw_audit"] is True
    assert cfg["autonomous_mode"]["human_interaction_disabled"] is True
    assert cfg["autonomous_mode"]["autonomous_merge_to_main"] is False


def test_eig2_config_has_no_placeholder_values() -> None:
    path = ROOT / "configs" / "explosive_intelligence_growth_v2.yaml"
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    quotas = data["explosive_intelligence_growth_v2"]["resource_quotas"]
    assert all(value is not None for value in quotas.values())
    assert quotas["max_tunnel_promotions_per_day"] == 0


def test_bridge_event_schema_requires_projection_fields() -> None:
    schema_path = ROOT / ".orchestrator" / "contracts" / "eig2_bridge_event.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    required = set(schema["required"])
    assert {
        "protocol_version",
        "message_type",
        "id",
        "timestamp",
        "author",
        "payload_hash",
    } <= required
    assert schema["properties"]["protocol_version"]["const"] == "eig2-bridge-v1"


def test_config_schema_requires_disabled_alpha_default() -> None:
    schema_path = ROOT / ".orchestrator" / "contracts" / "eig2_config.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    props = schema["properties"]["explosive_intelligence_growth_v2"]["properties"]
    assert props["enabled"]["const"] is False
    assert props["implemented"]["const"] is True
    assert props["production_default"]["const"] == "hex2d_sparse_tunnels"


def test_config_yaml_validates_against_schema() -> None:
    config_path = ROOT / "configs" / "explosive_intelligence_growth_v2.yaml"
    schema_path = ROOT / ".orchestrator" / "contracts" / "eig2_config.schema.json"
    config = yaml.load(config_path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(config, schema)
