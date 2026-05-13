#!/usr/bin/env python3
"""Build a v3.13.0 DomainCatalog projection.

The catalog is derived state. ToolDescriptor and StateHandle inventories stay
the source inputs; this tool only groups their public metadata into domain
rows for review, planning, and runtime discovery.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DOMAIN_NAMES: dict[str, str] = {
    "DOM-001": "Tool lifecycle, sync, status, doctor, repair",
    "DOM-002": "Core memory, vector, graph, search",
    "DOM-003": "Email and message streams",
    "DOM-004": "Documents, PDFs, OCR, report generation",
    "DOM-005": "Browser-only external systems",
    "DOM-006": "Finance, banking, accounting, tax, reconciliation",
    "DOM-007": "Forum, hosting, cPanel, PHP/MySQL, web-admin",
    "DOM-008": "Waggledance web, branding, SEO, i18n",
    "DOM-009": "Energy, home, cottage, device automation",
    "DOM-010": "Telecom, utilities, service-provider syncs",
    "DOM-011": "Factory logbook, MES, shifts, equipment reconciliation",
    "DOM-012": "Crypto and market intelligence",
    "DOM-013": "Meeting, audio, transcription, local speech",
    "DOM-014": "Real-estate, property, offers, comparison",
    "DOM-015": "Security, audit, account/session evidence",
    "DOM-016": "Backup, restore, server snapshots, package archives",
    "DOM-017": "Operator UI and dashboards",
    "DOM-018": "DB state and schema archetypes",
    "DOM-019": "Test and invariant corpus",
    "DOM-020": "Markdown briefings, blueprints, prompts, handoffs",
    "DOM-021": "Browser profiles and evidence artifacts",
}

RISK_RANK = {
    "informational": 0,
    "internal_memory": 1,
    "local_artifact": 2,
    "external_effect": 3,
}

SENSITIVE_CLASSES = {"restricted", "secret", "opaque"}


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_list(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        value = payload.get(key, [])
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    raise ValueError(f"expected a list or object with '{key}' array")


def _scope_sources(scopes: list[Any]) -> set[str]:
    sources: set[str] = set()
    for raw in scopes:
        if not isinstance(raw, str):
            continue
        prefix = raw.split(":", 1)[0].strip()
        if prefix:
            sources.add(prefix)
    return sources


def _domain_ids_for_state(state: dict[str, Any], owner_domains: dict[str, str]) -> set[str]:
    refs = state.get("domain_refs")
    if isinstance(refs, list) and refs:
        return {str(ref) for ref in refs}
    owner_tool = state.get("owner_tool")
    if isinstance(owner_tool, str) and owner_tool in owner_domains:
        return {owner_domains[owner_tool]}
    return set()


def build_domain_catalog(
    tool_descriptors: list[dict[str, Any]],
    state_handles: list[dict[str, Any]],
    *,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    generated_at_utc = generated_at_utc or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    owner_domains = {
        str(tool["tool_id"]): str(tool["domain"])
        for tool in tool_descriptors
        if isinstance(tool.get("tool_id"), str) and isinstance(tool.get("domain"), str)
    }

    rows: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sources": set(),
            "risk_rank": 0,
            "sensitive": False,
            "owners": set(),
            "tool_descriptor_ids": set(),
            "state_handle_ids": set(),
        }
    )

    for tool in tool_descriptors:
        domain_id = str(tool.get("domain", ""))
        if not domain_id:
            continue
        row = rows[domain_id]
        row["tool_descriptor_ids"].add(str(tool.get("tool_id")))
        row["owners"].add(str(tool.get("owner_agent", "shared")))
        row["sources"].update(_scope_sources(tool.get("read_scopes", [])))
        row["sources"].update(_scope_sources(tool.get("write_scopes", [])))
        risk = str(tool.get("write_risk_class") or "informational")
        row["risk_rank"] = max(row["risk_rank"], RISK_RANK.get(risk, 0))
        if tool.get("credential_refs") or risk == "external_effect":
            row["sensitive"] = True

    for state in state_handles:
        for domain_id in _domain_ids_for_state(state, owner_domains):
            row = rows[domain_id]
            row["state_handle_ids"].add(str(state.get("state_id")))
            source = state.get("source_class") or state.get("kind") or state.get("plane")
            if isinstance(source, str) and source:
                row["sources"].add(source)
            if state.get("sensitive_class") in SENSITIVE_CLASSES:
                row["sensitive"] = True
            if state.get("plane") == "external_system":
                row["risk_rank"] = max(row["risk_rank"], RISK_RANK["external_effect"])
            elif state.get("plane") == "filesystem_artifact":
                row["risk_rank"] = max(row["risk_rank"], RISK_RANK["local_artifact"])
            elif state.get("plane") == "control_state":
                row["risk_rank"] = max(row["risk_rank"], RISK_RANK["internal_memory"])

    ranked_risks = {value: key for key, value in RISK_RANK.items()}
    domains = []
    for domain_id in sorted(rows):
        row = rows[domain_id]
        owners = {owner for owner in row["owners"] if owner}
        owner_agent = owners.pop() if len(owners) == 1 else "shared"
        domains.append(
            {
                "domain_id": domain_id,
                "domain": DOMAIN_NAMES.get(domain_id, domain_id),
                "sources": sorted(row["sources"]),
                "primary_risk": ranked_risks[row["risk_rank"]],
                "sensitive": bool(row["sensitive"]),
                "owner_agent": owner_agent if owner_agent in {"codex", "claude", "shared", "operator"} else "shared",
                "tool_descriptor_ids": sorted(row["tool_descriptor_ids"]),
                "state_handle_ids": sorted(row["state_handle_ids"]),
            }
        )

    return {
        "schema_version": 1,
        "generated_at_utc": generated_at_utc,
        "source_counts": {
            "tool_descriptors": len(tool_descriptors),
            "state_handles": len(state_handles),
        },
        "domains": domains,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", required=True, type=Path, help="JSON list or object with tool_descriptors array")
    parser.add_argument("--states", required=True, type=Path, help="JSON list or object with state_handles array")
    parser.add_argument("--output", type=Path, help="Write catalog JSON to this path")
    args = parser.parse_args()

    tools = _as_list(_load_json(args.tools), "tool_descriptors")
    states = _as_list(_load_json(args.states), "state_handles")
    catalog = build_domain_catalog(tools, states)
    text = json.dumps(catalog, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
