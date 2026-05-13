from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "schemas" / "v3_13_0"
INVENTORY_GENERATOR_PATH = ROOT / "tools" / "build_v3_13_inventories.py"
CATALOG_GENERATOR_PATH = ROOT / "tools" / "build_v3_13_domain_catalog.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _validator(name: str) -> jsonschema.Draft7Validator:
    schema = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def test_inventory_generator_outputs_schema_valid_seed_rows() -> None:
    generator = _load_module(INVENTORY_GENERATOR_PATH, "build_v3_13_inventories")
    inventory = generator.build_inventories(
        [
            "tools/sync_factory_logbook.py",
            "tools/check_health.py",
            "schemas/v3_13_0/tool_descriptor.schema.json",
            "docs/architecture/V3_13_SCHEMA_BUNDLE.md",
        ],
        profile="factory_anchor",
    )

    assert [tool["tool_id"] for tool in inventory.tool_descriptors] == [
        "tool_tools_check_health",
        "tool_tools_sync_factory_logbook",
    ]
    assert [state["state_id"] for state in inventory.state_handles] == [
        "state_docs_architecture_v3_13_schema_bundle_md",
        "state_schemas_v3_13_0_tool_descriptor_schema_json",
    ]

    tool_validator = _validator("tool_descriptor.schema.json")
    for tool in inventory.tool_descriptors:
        tool_validator.validate(tool)

    state_validator = _validator("state_handle.schema.json")
    for state in inventory.state_handles:
        state_validator.validate(state)

    sync_tool = inventory.tool_descriptors[1]
    assert sync_tool["domain"] == "DOM-011"
    assert sync_tool["phase"] == "sync"
    assert sync_tool["write_risk_class"] == "external_effect"
    assert sync_tool["rate_limits"] == {"max_workers": 3, "request_delay_s": 0.15}


def test_generated_inventories_feed_domain_catalog_projection() -> None:
    inventory_generator = _load_module(INVENTORY_GENERATOR_PATH, "build_v3_13_inventories")
    catalog_generator = _load_module(CATALOG_GENERATOR_PATH, "build_v3_13_domain_catalog")
    inventory = inventory_generator.build_inventories(
        [
            "tools/build_v3_13_domain_catalog.py",
            "tools/sync_factory_logbook.py",
            "schemas/v3_13_0/state_handle.schema.json",
        ],
        profile="factory_anchor",
    )

    catalog = catalog_generator.build_domain_catalog(
        inventory.tool_descriptors,
        inventory.state_handles,
        generated_at_utc="2026-05-13T06:45:00Z",
    )
    _validator("domain_catalog.schema.json").validate(catalog)

    domains = {row["domain_id"]: row for row in catalog["domains"]}
    assert domains["DOM-001"]["tool_descriptor_ids"] == ["tool_tools_build_v3_13_domain_catalog"]
    assert domains["DOM-011"]["primary_risk"] == "external_effect"
    assert "state_schemas_v3_13_0_state_handle_schema_json" in domains["DOM-018"]["state_handle_ids"]


def test_inventory_generator_does_not_read_file_contents(monkeypatch) -> None:
    generator = _load_module(INVENTORY_GENERATOR_PATH, "build_v3_13_inventories")

    def fail_if_content_read(*args, **kwargs):  # pragma: no cover - defensive monkeypatch
        raise AssertionError("file content should not be read")

    monkeypatch.setattr(Path, "read_text", fail_if_content_read)
    inventory = generator.build_inventories(["tools/sync_factory_logbook.py"], profile="factory_anchor")
    assert inventory.tool_descriptors[0]["credential_refs"] == []
