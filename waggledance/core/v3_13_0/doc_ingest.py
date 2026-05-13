# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Sprint 2 DocIngest v1 local proposal contract.

This module is intentionally narrow. It does not parse arbitrary
operator documents, recurse through directories, call networks, or activate
solvers. It turns an explicit HOME/COTTAGE dry-run input directory into a
bounded proposal payload that SolverSynthesizer or a direct operator harness
can use to assemble a SCH-005 SolverCandidateManifest.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any

from waggledance.core.v3_13_0.anti_pattern_catalog import (
    scan_for_credential_patterns,
)


SUPPORTED_TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".csv", ".txt", ".md"}
SUPPORTED_METADATA_ONLY_SUFFIXES = {".pdf"}
SUPPORTED_SUFFIXES = SUPPORTED_TEXT_SUFFIXES | SUPPORTED_METADATA_ONLY_SUFFIXES
PROFILE_CONFIG_STEMS = {"profile_config", "profile"}
MAX_FILES = 20
MAX_TEXT_FILE_BYTES = 200_000
REF_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")


class DocIngestError(ValueError):
    """Fail-closed DocIngest validation error."""


@dataclass(frozen=True)
class DocIngestProposal:
    """Payload data for a bridge/MAGMA solver_candidate_proposal event."""

    profile_id: str
    profile_kind: str
    input_root_name: str
    source_docs: tuple[str, ...]
    warnings: tuple[str, ...]
    candidate_manifest_seed: dict[str, Any]
    event_type: str = "solver_candidate_proposal"

    def to_event_payload(self) -> dict[str, Any]:
        """Return JSON-serializable payload data.

        The key is deliberately named event_type so callers do not invent a
        new bridge event ``type`` value for the proposal.
        """
        return {
            "event_type": self.event_type,
            "profile_id": self.profile_id,
            "profile_kind": self.profile_kind,
            "input_root_name": self.input_root_name,
            "source_docs": list(self.source_docs),
            "warnings": list(self.warnings),
            "candidate_manifest_seed": dict(self.candidate_manifest_seed),
        }


def build_doc_ingest_proposal(
    input_root: str | Path,
    *,
    profile_kind: str,
    candidate_id: str,
) -> DocIngestProposal:
    """Build a bounded DocIngest proposal from one explicit local directory."""
    root = Path(input_root).resolve()
    if not root.exists() or not root.is_dir():
        raise DocIngestError(f"input_root must be an existing directory: {root}")
    _validate_ref_id(candidate_id, "candidate_id")

    expected_profile_kind = _normalize_profile_kind(profile_kind)
    entries = _list_top_level_entries(root)
    for path in entries:
        _ensure_under_root(path, root)
    profile_path = _find_profile_config(entries)
    profile = _parse_profile_config(profile_path)
    profile_id = str(profile.get("profile_id", "")).strip()
    actual_profile_kind = _normalize_profile_kind(str(profile.get("profile_kind", "")))
    if not profile_id:
        raise DocIngestError("profile_config is missing profile_id")
    _validate_ref_id(profile_id, "profile_id")
    if actual_profile_kind != expected_profile_kind:
        raise DocIngestError(
            f"profile_kind mismatch: requested {expected_profile_kind}, "
            f"profile_config has {actual_profile_kind}"
        )

    source_docs: list[str] = []
    warnings: list[str] = []
    seen_refs: set[str] = set()
    for path in entries:
        if path == profile_path:
            continue
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise DocIngestError(f"unsupported suffix for DocIngest: {path.name}")
        ref = _source_ref_for_path(path)
        if ref in seen_refs:
            raise DocIngestError(f"duplicate source ref generated: {ref}")
        seen_refs.add(ref)
        source_docs.append(ref)
        if suffix in SUPPORTED_TEXT_SUFFIXES:
            _scan_text_file_for_credentials(path)
        else:
            warnings.append(f"{ref}: metadata-only; no PDF parser in DocIngest v1")

    if not source_docs:
        raise DocIngestError("DocIngest requires at least one source document")

    source_docs.sort()
    seed = _manifest_seed(
        profile_kind=actual_profile_kind,
        candidate_id=candidate_id,
        profile_id=profile_id,
        source_docs=source_docs,
    )
    return DocIngestProposal(
        profile_id=profile_id,
        profile_kind=actual_profile_kind,
        input_root_name=root.name,
        source_docs=tuple(source_docs),
        warnings=tuple(warnings),
        candidate_manifest_seed=seed,
    )


def _list_top_level_entries(root: Path) -> list[Path]:
    entries = sorted(root.iterdir(), key=lambda path: path.name.casefold())
    if len(entries) > MAX_FILES:
        raise DocIngestError(f"too many input files: {len(entries)} > {MAX_FILES}")
    for path in entries:
        if path.is_dir():
            raise DocIngestError(
                f"nested directories are unsupported by DocIngest v1: {path.name}"
            )
    return entries


def _find_profile_config(entries: list[Path]) -> Path:
    matches = [
        path for path in entries
        if path.stem.casefold() in PROFILE_CONFIG_STEMS
        and path.suffix.lower() in {".json", ".yaml", ".yml"}
    ]
    if not matches:
        raise DocIngestError("profile_config.json/yaml is required")
    if len(matches) > 1:
        raise DocIngestError("multiple profile_config files are not allowed")
    return matches[0]


def _parse_profile_config(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    _fail_on_credentials(text, path.name)
    if path.suffix.lower() == ".json":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DocIngestError(f"profile_config JSON parse failed: {exc.msg}") from exc
        if not isinstance(parsed, dict):
            raise DocIngestError("profile_config JSON must be an object")
        return parsed
    return _parse_yaml_lite(text)


def _parse_yaml_lite(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by v3.13.0 profile examples.

    Supported syntax is top-level ``key: value`` scalars and top-level
    ``key:`` lists with ``- item`` entries. Nested mappings, nested lists,
    flow style, anchors, tags, multiline strings, and indentation semantics
    are intentionally unsupported.
    """
    parsed: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if not current_list_key:
                raise DocIngestError("yaml-lite list item without key")
            parsed.setdefault(current_list_key, []).append(stripped[2:].strip())
            continue
        if ":" not in stripped:
            raise DocIngestError(f"yaml-lite unsupported line: {stripped[:40]}")
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            raise DocIngestError("yaml-lite empty key")
        if value:
            parsed[key] = _coerce_scalar(value)
            current_list_key = None
        else:
            parsed[key] = []
            current_list_key = key
    return parsed


def _coerce_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value.isdigit():
        return int(value)
    return value.strip("'\"")


def _scan_text_file_for_credentials(path: Path) -> None:
    text = _read_text(path)
    _fail_on_credentials(text, path.name)


def _read_text(path: Path) -> str:
    size = path.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        raise DocIngestError(
            f"text file too large for DocIngest v1: {path.name} ({size} bytes)"
        )
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DocIngestError(f"text file must be UTF-8: {path.name}") from exc


def _fail_on_credentials(text: str, label: str) -> None:
    hits = scan_for_credential_patterns(text)
    if hits:
        names = ", ".join(hit.pattern_name for hit in hits[:5])
        raise DocIngestError(f"credential-like content refused in {label}: {names}")


def _source_ref_for_path(path: Path) -> str:
    stem = re.sub(r"[^a-z0-9_.:-]+", "_", path.stem.strip().lower())
    stem = stem.strip("_")
    ref = f"doc:{stem}"
    _validate_ref_id(ref, f"source ref for {path.name}")
    return ref


def _validate_ref_id(value: str, label: str) -> None:
    if not REF_ID_RE.match(value):
        raise DocIngestError(f"{label} is not a valid ref_id: {value!r}")


def _normalize_profile_kind(profile_kind: str) -> str:
    normalized = profile_kind.strip().lower()
    if normalized not in {"home", "cottage"}:
        raise DocIngestError(f"unsupported profile_kind: {profile_kind!r}")
    return normalized


def _ensure_under_root(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise DocIngestError(f"input path escapes input_root: {path}") from exc


def _manifest_seed(
    *,
    profile_kind: str,
    candidate_id: str,
    profile_id: str,
    source_docs: list[str],
) -> dict[str, Any]:
    defaults = _profile_defaults(profile_kind)
    return {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "source_docs": list(source_docs),
        "source_tools": [],
        "training_contracts": list(defaults["training_contracts"]),
        "state_handles": list(defaults["state_handles"]),
        "connector_handles": list(defaults["connector_handles"]),
        "shadow_inputs": list(defaults["shadow_inputs"]),
        "shadow_expected_outputs": list(defaults["shadow_expected_outputs"]),
        "divergence_score": None,
        "accepted_differences": [],
        "rejected_differences": [],
        "promotion_decision": "awaiting_shadow",
        "rollback_plan": f"recovery:{candidate_id}",
        "operator_review_id": f"op_review:{profile_id}",
        "provenance_signatures": [],
        "activation_state": "unactivated",
    }


def _profile_defaults(profile_kind: str) -> dict[str, tuple[str, ...]]:
    if profile_kind == "home":
        return {
            "training_contracts": (
                "ctr_date", "ctr_search", "ctr_vector",
                "ctr_memory", "ctr_cross_ref",
            ),
            "state_handles": (
                "state:spot_price_store",
                "state:consumption_forecast",
                "state:optimizer_recommendations",
            ),
            "connector_handles": ("conn:spot_price_public_feed",),
            "shadow_inputs": ("synth_24h_winter", "synth_24h_summer"),
            "shadow_expected_outputs": (
                "recommendation_with_savings_estimate_winter",
                "recommendation_with_savings_estimate_summer",
            ),
        }
    if profile_kind == "cottage":
        return {
            "training_contracts": ("ctr_date", "ctr_vector", "ctr_memory"),
            "state_handles": (
                "state:weather_forecast_cache",
                "state:sensor_history",
                "state:frost_risk_predictions",
            ),
            "connector_handles": ("conn:weather_forecast_public",),
            "shadow_inputs": (
                "synth_cold_snap_24h",
                "synth_thaw_24h",
                "synth_steady_freeze_72h",
            ),
            "shadow_expected_outputs": (
                "high_risk_alert_within_6h",
                "no_risk_within_24h",
                "medium_risk_within_72h",
            ),
        }
    raise DocIngestError(f"unsupported profile_kind defaults: {profile_kind!r}")


__all__ = [
    "DocIngestError",
    "DocIngestProposal",
    "build_doc_ingest_proposal",
]
