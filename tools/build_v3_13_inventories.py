#!/usr/bin/env python3
"""Build v3.13.0 seed ToolDescriptor and StateHandle inventories.

This is a static inventory pass. It reads tracked path names, not file content,
so it does not ingest credentials, browser profiles, personal documents, or
runtime data. The generated inventories are seed descriptors that later
profile-specific tooling can refine.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXTERNAL_DOMAINS = {"DOM-005", "DOM-006", "DOM-007", "DOM-009", "DOM-010", "DOM-011", "DOM-012", "DOM-015", "DOM-021"}
DEFAULT_RATE_LIMIT = {"max_workers": 1, "request_delay_s": 0}
SHARED_PROD_RATE_LIMIT = {"max_workers": 3, "request_delay_s": 0.15}


@dataclass(frozen=True)
class Inventory:
    tool_descriptors: list[dict]
    state_handles: list[dict]


def _safe_id(raw: str, *, prefix: str = "") -> str:
    value = raw.replace("\\", "/").lower()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    value = re.sub(r"_+", "_", value)
    if not value or not value[0].isalpha():
        value = f"id_{value}"
    if prefix and not value.startswith(prefix):
        value = f"{prefix}{value}"
    return value[:120].rstrip("_")


def tracked_paths(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def infer_domain(path: str) -> str:
    lower = path.lower()
    checks = [
        (("logbook", "mes", "pdam", "factory"), "DOM-011"),
        (("bank", "invoice", "receipt", "tax", "finance"), "DOM-006"),
        (("forum", "hosting", "cpanel", "mysql", "php"), "DOM-007"),
        (("browser", "playwright", "screenshot"), "DOM-005"),
        (("energy", "electricity", "spot", "cottage", "home", "weather", "frost"), "DOM-009"),
        (("telecom", "utility", "provider"), "DOM-010"),
        (("crypto", "market", "trading"), "DOM-012"),
        (("audio", "meeting", "transcribe", "speech"), "DOM-013"),
        (("property", "real_estate", "estate", "offer"), "DOM-014"),
        (("backup", "restore", "snapshot"), "DOM-016"),
        (("schema", "db", "sqlite", "sql"), "DOM-018"),
        (("test", "contract", "gate"), "DOM-019"),
        (("prompt", "briefing", "handoff", "markdown"), "DOM-020"),
        (("security", "audit", "session", "profile"), "DOM-015"),
    ]
    for needles, domain in checks:
        if any(needle in lower for needle in needles):
            return domain
    if lower.startswith("docs/"):
        return "DOM-020"
    if lower.startswith("schemas/"):
        return "DOM-018"
    if lower.startswith("tests/"):
        return "DOM-019"
    return "DOM-001"


def infer_phase(path: str) -> str:
    name = Path(path).name.lower()
    if any(token in name for token in ("sync", "fetch", "import", "pull")):
        return "sync"
    if any(token in name for token in ("build", "generate", "project", "catalog", "inventory")):
        return "project"
    if any(token in name for token in ("check", "doctor", "verify", "gate")):
        return "doctor"
    if any(token in name for token in ("fix", "repair", "recover", "restore")):
        return "repair"
    if any(token in name for token in ("report", "export")):
        return "report"
    if any(token in name for token in ("ingest", "parse", "extract")):
        return "ingest"
    return "observe"


def infer_write_risk(domain: str, phase: str) -> str:
    if domain in EXTERNAL_DOMAINS and phase in {"sync", "write", "repair"}:
        return "external_effect"
    if phase in {"project", "report", "repair", "persist", "index"}:
        return "local_artifact"
    if phase in {"ingest", "sync"}:
        return "internal_memory"
    return "informational"


def infer_source_class(path: str) -> str:
    lower = path.lower()
    suffix = Path(lower).suffix
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return "sqlite"
    if suffix == ".sql":
        return "sql_database"
    if suffix in {".md", ".txt", ".pdf"}:
        return "document_corpus"
    if suffix in {".json", ".jsonl", ".yaml", ".yml", ".toml"}:
        return "filesystem"
    if "browser" in lower or "profile" in lower:
        return "browser_profile"
    if lower.startswith("tests/"):
        return "tests"
    if lower.startswith("logs/"):
        return "logs"
    if "archive" in lower or "backup" in lower:
        return "archive"
    return "filesystem"


def infer_state_kind(path: str) -> str:
    lower = path.lower()
    suffix = Path(lower).suffix
    if suffix in {".db", ".sqlite", ".sqlite3"}:
        return "sqlite"
    if suffix == ".json" or lower.endswith(".schema.json"):
        return "json"
    if suffix == ".jsonl":
        return "log"
    if suffix == ".csv":
        return "csv"
    if suffix in {".md", ".txt", ".pdf"}:
        return "document_corpus"
    return "artifact_dir" if suffix == "" else "json"


def should_describe_tool(path: str) -> bool:
    lower = path.lower()
    if not lower.endswith((".py", ".ps1", ".bat", ".cmd")):
        return False
    return lower.startswith(("tools/", "scripts/", "waggledance/", "integrations/"))


def should_describe_state(path: str) -> bool:
    lower = path.lower()
    if lower.startswith((".git/", ".agent-bridge/shared/", "logs/")):
        return False
    return lower.endswith((".schema.json", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".md", ".db", ".sqlite", ".csv"))


def build_tool_descriptor(path: str, *, profile: str) -> dict:
    tool_id = _safe_id(Path(path).with_suffix("").as_posix(), prefix="tool_")
    domain = infer_domain(path)
    phase = infer_phase(path)
    risk = infer_write_risk(domain, phase)
    return {
        "schema_version": 1,
        "tool_id": tool_id,
        "profile": profile,
        "domain": domain,
        "phase": phase,
        "read_scopes": [f"repo:{path}"],
        "write_scopes": [f"artifact:generated/{tool_id}"] if risk == "local_artifact" else [],
        "credential_refs": [],
        "connector_refs": [],
        "state_refs": [],
        "checkpoint_refs": [],
        "rate_limits": SHARED_PROD_RATE_LIMIT if risk == "external_effect" else DEFAULT_RATE_LIMIT,
        "idempotency_key": f"{tool_id}:path",
        "dry_run_supported": risk != "external_effect",
        "shadow_supported": True,
        "rollback_artifact": None,
        "owner_agent": "codex",
        "promotion_gate_refs": ["v3_13_schema_bundle"],
        "write_risk_class": risk,
        "capture_supported": False,
        "capture_policy_ref": None,
    }


def build_state_handle(path: str, *, profile: str) -> dict:
    state_id = _safe_id(path, prefix="state_")
    source_class = infer_source_class(path)
    domain = infer_domain(path)
    return {
        "schema_version": 1,
        "state_id": state_id,
        "kind": infer_state_kind(path),
        "owner_tool": "tool_v3_13_inventory_generator",
        "plane": "filesystem_artifact",
        "readers": ["tool_v3_13_inventory_generator"],
        "writers": ["tool_v3_13_inventory_generator"],
        "single_writer_required": True,
        "wal_required": path.lower().endswith((".db", ".sqlite", ".sqlite3")),
        "freshness_query": f"repo:{path}",
        "integrity_query": f"repo:{path}",
        "backup_strategy": "snapshot",
        "recovery_strategy": "restore_snapshot",
        "sensitive_class": "internal" if domain not in {"DOM-015", "DOM-021"} else "opaque",
        "read_only_uri": f"repo:{path}",
        "projection_of": None,
        "high_watermark_ref": None,
        "source_class": source_class,
        "domain_refs": [domain],
        "write_modes_allowed": ["append"] if path.lower().endswith(".jsonl") else [],
    }


def build_inventories(paths: Iterable[str], *, profile: str = "default") -> Inventory:
    normalized = sorted({path.replace("\\", "/").strip("/") for path in paths if path})
    tools = [build_tool_descriptor(path, profile=profile) for path in normalized if should_describe_tool(path)]
    states = [build_state_handle(path, profile=profile) for path in normalized if should_describe_state(path)]
    return Inventory(tool_descriptors=tools, state_handles=states)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="Repository root. Defaults to cwd.")
    parser.add_argument("--profile", default="default", help="Profile id stamped into generated ToolDescriptors.")
    parser.add_argument("--tool-output", type=Path, required=True, help="Path for tool descriptor inventory JSON.")
    parser.add_argument("--state-output", type=Path, required=True, help="Path for state handle inventory JSON.")
    args = parser.parse_args()

    paths = tracked_paths(args.root)
    inventory = build_inventories(paths, profile=args.profile)
    args.tool_output.parent.mkdir(parents=True, exist_ok=True)
    args.state_output.parent.mkdir(parents=True, exist_ok=True)
    args.tool_output.write_text(
        json.dumps({"schema_version": 1, "tool_descriptors": inventory.tool_descriptors}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.state_output.write_text(
        json.dumps({"schema_version": 1, "state_handles": inventory.state_handles}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
