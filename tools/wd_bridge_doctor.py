#!/usr/bin/env python3
"""F29 read-only bridge doctor: report missing components and provider states.

Inputs are explicit files only:

* the component manifest ``configs/bridge_components.json`` (``wd.bridge-components.v1``);
* a local paths config (``wd.bridge-local-paths.v1``) mapping each ``path_key`` to an
  absolute path; no machine path is hard-coded here;
* an optional provider evidence file (``wd.bridge-provider-evidence.v1``) with the
  auth, quota and observed-turn states that some other, authorised tool observed.

The doctor never executes anything, never queries a provider, never authenticates and
never installs. It only calls ``os.stat`` on configured paths. Every unknown result is
treated as missing. A callback or a live process is never readiness: a provider is
ready only when its CLI is present AND fresh explicit evidence shows valid auth,
available quota and a succeeded observed turn. The report is deterministic for the
same inputs and ``--now``; it never echoes evidence free text, so credentials placed
in evidence are not reproduced.

Exit codes: 0 ready, 1 degraded (an optional feature is disabled), 2 refuse (a
required feature is unsatisfied), 3 invalid input (nothing was evaluated).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat as stat_module
import sys
from typing import Any, Sequence

MANIFEST_SCHEMA = "wd.bridge-components.v1"
PATHS_SCHEMA = "wd.bridge-local-paths.v1"
EVIDENCE_SCHEMA = "wd.bridge-provider-evidence.v1"
REPORT_SCHEMA = "wd.bridge-doctor-report.v1"

ID_RE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
LANE_RE = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
SOURCE_RE = re.compile(r"[A-Za-z0-9._:/-]{1,128}\Z")
MAX_INPUT_BYTES = 1024 * 1024
MAX_FUTURE_SKEW = timedelta(seconds=300)

COMPONENT_KINDS = ("directory", "executable")
EVIDENCE_DIMENSIONS = ("auth", "quota", "observed_turn")
POSITIVE = {"auth": "valid", "quota": "available", "observed_turn": "succeeded"}
NEGATIVE = {"auth": "invalid", "quota": "exhausted", "observed_turn": "failed"}
GUIDANCE = {
    "auth": "Authenticate the {p} CLI interactively as the operator; the doctor never authenticates.",
    "quota": "Wait for the recorded {p} quota reset or route to another pool; the doctor never buys capacity.",
    "observed_turn": ("Provide fresh evidence of a succeeded {p} turn; a callback, a queue receipt "
                      "or a live process is not readiness."),
    "unknown": "Provide fresh explicit {p} {d} evidence from an authorised observer; unknown is treated as missing.",
}

MANIFEST_KEYS = {"schema", "description", "lanes", "provider_state_max_age_seconds",
                 "components", "providers", "features"}
COMPONENT_KEYS = {"id", "kind", "path_key", "description", "install"}
PROVIDER_KEYS = {"id", "cli_component"}
FEATURE_KEYS = {"id", "description", "components", "providers",
                "required_for_lanes", "optional_for_lanes"}


class DoctorInputError(ValueError):
    """An input file is missing, oversized or does not match its strict schema."""


def _unique(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise DoctorInputError("duplicate JSON key: " + str(key)[:64])
        result[key] = value
    return result


def _reject_constant(value: str):
    raise DoctorInputError("non-finite JSON constant: " + value)


def load_json(path: Path, what: str) -> Any:
    """Strict JSON: bounded size, UTF-8, no duplicate keys, no NaN/Infinity."""
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
    except OSError as exc:
        raise DoctorInputError(what + " unreadable: " + type(exc).__name__) from exc
    if len(raw) > MAX_INPUT_BYTES:
        raise DoctorInputError(what + " exceeds " + str(MAX_INPUT_BYTES) + " bytes")
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique,
                          parse_constant=_reject_constant)
    except DoctorInputError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DoctorInputError(what + " is not valid JSON: " + type(exc).__name__) from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DoctorInputError(message)


def _exact_keys(value: Any, allowed: set, required: set, what: str) -> None:
    _require(type(value) is dict, what + " must be an object")
    extra, missing = set(value) - allowed, required - set(value)
    _require(not extra, what + " has unknown keys: " + ",".join(sorted(extra)))
    _require(not missing, what + " lacks keys: " + ",".join(sorted(missing)))


def _string_list(value: Any, pattern: re.Pattern | None, what: str, allow_star: bool = False) -> list[str]:
    _require(type(value) is list and all(type(v) is str for v in value), what + " must be a list of strings")
    _require(len(value) == len(set(value)), what + " has duplicates")
    for item in value:
        if allow_star and item == "*":
            continue
        _require(pattern is None or bool(pattern.fullmatch(item)), what + " has an invalid entry")
    if allow_star:
        _require("*" not in value or len(value) == 1, what + ": '*' must stand alone")
    return list(value)


def validate_manifest(manifest: Any) -> dict:
    """Validate the whole manifest before any evaluation; any defect is invalid input."""
    _exact_keys(manifest, MANIFEST_KEYS, MANIFEST_KEYS - {"description"}, "manifest")
    _require(manifest["schema"] == MANIFEST_SCHEMA, "unsupported manifest schema")
    lanes = _string_list(manifest["lanes"], LANE_RE, "manifest.lanes")
    _require(bool(lanes), "manifest.lanes is empty")

    ages = manifest["provider_state_max_age_seconds"]
    _exact_keys(ages, set(EVIDENCE_DIMENSIONS), set(EVIDENCE_DIMENSIONS), "provider_state_max_age_seconds")
    for dimension, seconds in ages.items():
        _require(type(seconds) is int and 1 <= seconds <= 7 * 86400,
                 "max age for " + dimension + " must be an int in 1..604800")

    _require(type(manifest["components"]) is list and manifest["components"], "components must be a non-empty list")
    components: dict[str, dict] = {}
    path_keys: set[str] = set()
    for item in manifest["components"]:
        _exact_keys(item, COMPONENT_KEYS, COMPONENT_KEYS, "component")
        _require(type(item["id"]) is str and bool(ID_RE.fullmatch(item["id"])), "invalid component id")
        _require(item["id"] not in components, "duplicate component id " + item["id"])
        _require(item["kind"] in COMPONENT_KINDS, "invalid component kind for " + item["id"])
        _require(type(item["path_key"]) is str and bool(ID_RE.fullmatch(item["path_key"])),
                 "invalid path_key for " + item["id"])
        _require(item["path_key"] not in path_keys, "duplicate path_key " + item["path_key"])
        _require(type(item["description"]) is str and item["description"].strip() != "",
                 "empty description for " + item["id"])
        install = item["install"]
        _exact_keys(install, {"instructions"}, {"instructions"}, "install of " + item["id"])
        steps = install["instructions"]
        _require(type(steps) is list and steps and all(type(s) is str and s.strip() for s in steps),
                 "install.instructions must be non-empty strings for " + item["id"])
        path_keys.add(item["path_key"])
        components[item["id"]] = item

    _require(type(manifest["providers"]) is list, "providers must be a list")
    providers: dict[str, dict] = {}
    for item in manifest["providers"]:
        _exact_keys(item, PROVIDER_KEYS, PROVIDER_KEYS, "provider")
        _require(type(item["id"]) is str and bool(ID_RE.fullmatch(item["id"])), "invalid provider id")
        _require(item["id"] not in providers, "duplicate provider id " + item["id"])
        _require(item["cli_component"] in components, "provider " + item["id"] + " names an unknown cli_component")
        providers[item["id"]] = item

    _require(type(manifest["features"]) is list and manifest["features"], "features must be a non-empty list")
    features: dict[str, dict] = {}
    for item in manifest["features"]:
        _exact_keys(item, FEATURE_KEYS, FEATURE_KEYS - {"description"}, "feature")
        _require(type(item["id"]) is str and bool(ID_RE.fullmatch(item["id"])), "invalid feature id")
        _require(item["id"] not in features, "duplicate feature id " + item["id"])
        needed = _string_list(item["components"], ID_RE, "feature " + item["id"] + " components")
        _require(all(c in components for c in needed), "feature " + item["id"] + " names an unknown component")
        wanted = _string_list(item["providers"], ID_RE, "feature " + item["id"] + " providers")
        _require(all(p in providers for p in wanted), "feature " + item["id"] + " names an unknown provider")
        _require(bool(needed or wanted), "feature " + item["id"] + " requires nothing")
        required = _string_list(item["required_for_lanes"], LANE_RE, "required_for_lanes", allow_star=True)
        optional = _string_list(item["optional_for_lanes"], LANE_RE, "optional_for_lanes", allow_star=True)
        for lane in required + optional:
            _require(lane == "*" or lane in lanes, "feature " + item["id"] + " names an unknown lane")
        _require(not (set(required) & set(optional)) and not ("*" in required and optional),
                 "feature " + item["id"] + " is both required and optional for a lane")
        features[item["id"]] = item
    return {"lanes": lanes, "ages": dict(ages), "components": components,
            "providers": providers, "features": features}


def validate_paths(paths_config: Any) -> dict[str, str]:
    _exact_keys(paths_config, {"schema", "paths"}, {"schema", "paths"}, "paths config")
    _require(paths_config["schema"] == PATHS_SCHEMA, "unsupported paths config schema")
    mapping = paths_config["paths"]
    _require(type(mapping) is dict, "paths config 'paths' must be an object")
    for key, value in mapping.items():
        _require(bool(ID_RE.fullmatch(key)), "invalid path key in paths config")
        _require(type(value) is str, "path value for " + key + " must be a string")
    return dict(mapping)


def validate_evidence(evidence: Any) -> dict:
    _exact_keys(evidence, {"schema", "providers"}, {"schema", "providers"}, "evidence")
    _require(evidence["schema"] == EVIDENCE_SCHEMA, "unsupported evidence schema")
    _require(type(evidence["providers"]) is dict, "evidence providers must be an object")
    return evidence["providers"]


def check_component(component: dict, paths: dict[str, str]) -> dict:
    """Return present/missing/unknown for one component; only os.stat is called."""
    raw = paths.get(component["path_key"])
    result = {"id": component["id"], "kind": component["kind"], "path_key": component["path_key"]}
    if raw is None or raw.strip() == "":
        return {**result, "state": "unknown", "reason": "path_not_configured"}
    if "\0" in raw or not os.path.isabs(raw):
        return {**result, "state": "unknown", "reason": "path_not_absolute"}
    result["path"] = raw
    try:
        info = os.stat(raw)
    except FileNotFoundError:
        return {**result, "state": "missing", "reason": "path_absent"}
    except (OSError, ValueError) as exc:
        return {**result, "state": "unknown", "reason": "stat_failed:" + type(exc).__name__}
    is_dir = stat_module.S_ISDIR(info.st_mode)
    is_file = stat_module.S_ISREG(info.st_mode)
    if component["kind"] == "directory" and not is_dir:
        return {**result, "state": "missing", "reason": "not_a_directory"}
    if component["kind"] == "executable" and not is_file:
        return {**result, "state": "missing", "reason": "not_a_file"}
    return {**result, "state": "present", "reason": "path_present"}


def _parse_time(value: Any) -> datetime | None:
    if type(value) is not str or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def evidence_state(entry: Any, dimension: str, max_age: int, now: datetime) -> dict:
    """One provider dimension from explicit evidence; anything doubtful is unknown."""
    if type(entry) is not dict:
        return {"state": "unknown", "reason": "no_evidence"}
    state = entry.get("state")
    observed = _parse_time(entry.get("observed_at_utc"))
    if state not in (POSITIVE[dimension], NEGATIVE[dimension]):
        return {"state": "unknown", "reason": "unrecognised_state"}
    if observed is None:
        return {"state": "unknown", "reason": "no_valid_timestamp"}
    if observed - now > MAX_FUTURE_SKEW:
        return {"state": "unknown", "reason": "future_dated"}
    if now - observed > timedelta(seconds=max_age):
        return {"state": "unknown", "reason": "stale", "observed_at_utc": observed.isoformat()}
    result = {"state": state, "reason": "fresh_evidence", "observed_at_utc": observed.isoformat()}
    source = entry.get("source")
    if type(source) is str and SOURCE_RE.fullmatch(source):
        result["source"] = source
    return result


def provider_states(provider: dict, component_results: dict[str, dict], evidence: dict,
                    ages: dict, now: datetime) -> dict:
    cli = component_results[provider["cli_component"]]["state"]
    states = {"cli": {"state": {"present": "installed"}.get(cli, cli),
                      "reason": component_results[provider["cli_component"]]["reason"]}}
    entry = evidence.get(provider["id"]) if isinstance(evidence, dict) else None
    for dimension in EVIDENCE_DIMENSIONS:
        states[dimension] = evidence_state(entry.get(dimension) if type(entry) is dict else None,
                                           dimension, ages[dimension], now)
    ready = (states["cli"]["state"] == "installed"
             and all(states[d]["state"] == POSITIVE[d] for d in EVIDENCE_DIMENSIONS))
    return {"id": provider["id"], "states": states, "ready": ready}


def _applicability(feature: dict, lane: str) -> str:
    if "*" in feature["required_for_lanes"] or lane in feature["required_for_lanes"]:
        return "required"
    if "*" in feature["optional_for_lanes"] or lane in feature["optional_for_lanes"]:
        return "optional"
    return "not_applicable"


def evaluate(manifest: dict, paths: dict[str, str], evidence: dict, lane: str, now: datetime) -> dict:
    """Pure evaluation of validated inputs; returns the deterministic report."""
    _require(lane in manifest["lanes"], "lane is not declared in the manifest")
    components = {cid: check_component(c, paths) for cid, c in sorted(manifest["components"].items())}
    providers = {pid: provider_states(p, components, evidence, manifest["ages"], now)
                 for pid, p in sorted(manifest["providers"].items())}
    feature_reports, guidance = [], {}
    for fid, feature in sorted(manifest["features"].items()):
        applicability = _applicability(feature, lane)
        if applicability == "not_applicable":
            feature_reports.append({"id": fid, "applicability": applicability, "status": "not_applicable",
                                    "reasons": []})
            continue
        reasons = []
        for cid in sorted(feature["components"]):
            state = components[cid]["state"]
            if state != "present":
                reasons.append("component:" + cid + ":" + state)
                if applicability == "required":
                    guidance["component:" + cid] = {
                        "component": cid, "state": state, "reason": components[cid]["reason"],
                        "instructions": list(manifest["components"][cid]["install"]["instructions"])}
        for pid in sorted(feature["providers"]):
            report = providers[pid]
            if report["ready"]:
                continue
            cli_state = report["states"]["cli"]["state"]
            if cli_state != "installed":
                reasons.append("provider:" + pid + ":cli:" + cli_state)
                if applicability == "required":
                    cid = manifest["providers"][pid]["cli_component"]
                    guidance["component:" + cid] = {
                        "component": cid, "state": components[cid]["state"], "reason": components[cid]["reason"],
                        "instructions": list(manifest["components"][cid]["install"]["instructions"])}
            for dimension in EVIDENCE_DIMENSIONS:
                state = report["states"][dimension]["state"]
                if state == POSITIVE[dimension]:
                    continue
                reasons.append("provider:" + pid + ":" + dimension + ":" + state)
                if applicability == "required":
                    template = GUIDANCE["unknown"] if state == "unknown" else GUIDANCE[dimension]
                    guidance["provider:" + pid + ":" + dimension] = {
                        "provider": pid, "dimension": dimension, "state": state,
                        "instructions": [template.format(p=pid, d=dimension)]}
        if not reasons:
            status = "enabled"
        else:
            status = "unsatisfied" if applicability == "required" else "disabled"
        feature_reports.append({"id": fid, "applicability": applicability, "status": status,
                                "reasons": reasons})
    if any(f["status"] == "unsatisfied" for f in feature_reports):
        verdict = "refuse"
    elif any(f["status"] == "disabled" for f in feature_reports):
        verdict = "degraded"
    else:
        verdict = "ready"
    return {"schema": REPORT_SCHEMA, "lane": lane, "evaluated_at_utc": now.isoformat(),
            "verdict": verdict, "components": list(components.values()),
            "providers": list(providers.values()), "features": feature_reports,
            "missing_required": [guidance[k] for k in sorted(guidance)],
            "installs_performed": False, "authority_effect": "none",
            "note": ("Read-only doctor: no execution, provider query, authentication or install. "
                     "Unknown is treated as missing; a callback or live process is not readiness.")}


EXIT_CODES = {"ready": 0, "degraded": 1, "refuse": 2}


def run(manifest_path: Path, paths_path: Path, evidence_path: Path | None, lane: str,
        now: datetime) -> tuple[int, dict]:
    try:
        _require(bool(LANE_RE.fullmatch(lane)), "invalid lane name")
        manifest = validate_manifest(load_json(manifest_path, "manifest"))
        paths = validate_paths(load_json(paths_path, "paths config"))
        evidence = validate_evidence(load_json(evidence_path, "evidence")) if evidence_path else {}
        report = evaluate(manifest, paths, evidence, lane, now)
    except DoctorInputError as exc:
        return 3, {"schema": REPORT_SCHEMA, "verdict": "invalid_input", "error": str(exc)[:300],
                   "installs_performed": False, "authority_effect": "none"}
    return EXIT_CODES[report["verdict"]], report


def _parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    parsed = _parse_time(value)
    if parsed is None:
        raise SystemExit("--now must be an ISO-8601 timestamp with a timezone")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--paths-config", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--now", help="evaluation time (ISO-8601 with timezone); default: current UTC")
    args = parser.parse_args(argv)
    code, report = run(args.manifest, args.paths_config, args.evidence, args.lane, _parse_now(args.now))
    sys.stdout.write(json.dumps(report, sort_keys=True, ensure_ascii=True) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
