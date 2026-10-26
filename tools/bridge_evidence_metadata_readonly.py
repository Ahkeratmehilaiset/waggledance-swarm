"""Dormant metadata helpers and Python-contract dry-run; no bridge writer.

Run: python -B -m tools.bridge_evidence_metadata_readonly --dry-run
Stdin is {"event": {...}, "request": {...}, "target": "...", "enrollment": {...}}.
Only event is required. Missing binding/enrollment remains HOLD (exit 3);
invalid input is REFUSED (exit 2). There is no authorization/PASS exit.

The existing Python schema's format/provenance and request correlation checks
are reused. This is NOT PowerShell-hook equivalence, enrollment verification,
CI, a merge verdict, a live bridge check, or permission to lift any hold.
Enrollment fixtures are untrusted descriptions, even with "verified": true.
No installed enrollment context is loaded, and no registered tool is run.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import math
import re
import sys
from types import MappingProxyType
from typing import Any, TextIO

MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
MAX_STAT_VALUE = 9_223_372_036_854_775_807
STAT_FIELDS = frozenset({
    "creation_time_ticks", "last_write_time_ticks", "last_access_time_ticks",
    "last_write_utc_ticks", "creation_utc_ticks",
    "st_mtime_ns", "st_atime_ns", "st_ctime_ns",
})
FIELD_DICTIONARY = MappingProxyType({
    "diff_sha256": "Alias of net_diff_sha256: exact lowercase SHA256, never preferred on conflict.",
    "net_diff_sha256": "Whole raw comparison diff SHA256, not an API-rendered substitute.",
    **{key: "Nonnegative Int64 stat value; JSON representation is an exact decimal string."
       for key in sorted(STAT_FIELDS)},
})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_DECIMAL = re.compile(r"0|[1-9][0-9]*")


def _stat_string(value: Any) -> str:
    if type(value) is int:
        if not 0 <= value <= MAX_STAT_VALUE:
            raise ValueError("STAT_VALUE_OUT_OF_RANGE")
        return str(value)
    if type(value) is str and _DECIMAL.fullmatch(value):
        # Check length before int(), including Python's integer-conversion limit.
        if len(value) <= 19 and int(value) <= MAX_STAT_VALUE:
            return value
    raise ValueError("STAT_VALUE_NOT_EXACT_NONNEGATIVE_INT64")


def normalize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Copy JSON metadata; never mutate inputs, round ticks, or choose an alias.

    Eight explicitly named stat fields are stringified before outgoing JSON.
    Other integers must fit the interoperable JSON safe-integer range. Nested
    objects/arrays are checked too; unknown types, cycles/depth >32 and NaN fail.
    Ticks and ns are different units: their field names/values are not converted.
    """
    if not isinstance(metadata, Mapping):
        raise ValueError("METADATA_NOT_OBJECT")

    def visit(value: Any, depth: int = 0) -> Any:
        if depth > 32:
            raise ValueError("METADATA_TOO_DEEP_OR_CYCLIC")
        if isinstance(value, Mapping):
            if any(type(key) is not str for key in value):
                raise ValueError("JSON_KEY_NOT_STRING")
            result = {
                key: _stat_string(item) if key in STAT_FIELDS else visit(item, depth + 1)
                for key, item in value.items()
            }
            aliases = [result[key] for key in ("diff_sha256", "net_diff_sha256") if key in result]
            if aliases:
                if any(type(item) is not str or not _SHA256.fullmatch(item) for item in aliases):
                    raise ValueError("DIFF_SHA256_INVALID")
                if len(set(aliases)) != 1:
                    raise ValueError("DIFF_SHA256_CONFLICT")
                result["diff_sha256"] = result["net_diff_sha256"] = aliases[0]
                payload = result.get("payload")
                if isinstance(payload, dict) and "net_diff_sha256" in payload:
                    if payload["net_diff_sha256"] != aliases[0]:
                        raise ValueError("DIFF_SHA256_ENVELOPE_CONFLICT")
            return result
        if type(value) is list:
            return [visit(item, depth + 1) for item in value]
        if value is None or type(value) in (bool, str):
            return value
        if type(value) is int and abs(value) <= MAX_SAFE_JSON_INTEGER:
            return value
        if type(value) is float and math.isfinite(value) and abs(value) <= MAX_SAFE_JSON_INTEGER:
            return value
        raise ValueError("JSON_VALUE_UNSAFE_OR_UNSUPPORTED")

    return visit(metadata)


def dry_run(
    event: Mapping[str, Any], *, request: Mapping[str, Any] | None = None,
    target: str | None = None, enrollment: Mapping[str, Any] | None = None,
    provenance: Any = None,
) -> dict[str, Any]:
    """Inspect fixtures using the actual existing Python boundary, without writes.

    A caller may inject the schema's provenance object; it is still checked by
    that schema, not manufactured from JSON claims. Enrollment is NEVER trusted
    here. Correlation alone proves neither canonical append order nor completion.
    """
    result: dict[str, Any] = {
        "status": "HOLD", "dry_run": True, "schema_valid": False,
        "binding_valid": None, "enrollment_status": "UNKNOWN",
        "merge_authorized": False, "reasons": [],
        "scope": "PYTHON_FORMAT_PROVENANCE_CORRELATION_ONLY",
    }

    def refuse(reason: str) -> dict[str, Any]:
        result["status"] = "REFUSED"
        result["reasons"].append(reason)
        return result

    try:
        candidate = normalize_metadata(event)
    except (ValueError, TypeError):
        return refuse("METADATA_INVALID")
    try:
        from tools import bridge_v2_event_schema as schema
        from tools import bridge_v2_request_contract as contract
    except ImportError:
        return refuse("EXISTING_PYTHON_VALIDATOR_UNAVAILABLE")
    try:
        schema.validate_event_for_write(candidate, provenance=provenance)
    except (ValueError, TypeError):
        return refuse("EVENT_SCHEMA")
    result["schema_valid"] = True
    if request is None:
        result["reasons"].append("BINDING_MISSING")
    elif type(target) is not str or not target:
        return refuse("TARGET_MISSING")
    else:
        try:
            binding = normalize_metadata(request)
            matched = contract.reply_matches_request(
                binding, candidate, target, require_explicit_correlation=True,
            )
        except (ValueError, TypeError, KeyError):
            return refuse("BINDING_INVALID")
        result["binding_valid"] = matched
        if not matched:
            return refuse("BINDING_MISMATCH")
    if enrollment is None:
        result["reasons"].append("ENROLLMENT_UNKNOWN")
    elif isinstance(enrollment, Mapping):
        result["enrollment_status"] = "UNVERIFIED"
        result["reasons"].append("ENROLLMENT_UNVERIFIED")
    else:
        return refuse("ENROLLMENT_INVALID")
    return result


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def main(
    argv: list[str] | None = None, *, stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> int:
    """Explicit stdin/stdout dry-run only; HOLD=3, REFUSED=2, never apply."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True)
    parser.parse_args(argv)
    incoming = sys.stdin if stdin is None else stdin
    outgoing = sys.stdout if stdout is None else stdout
    try:
        text = incoming.read(1_048_577)
        if len(text) > 1_048_576:
            raise ValueError("INPUT_TOO_LARGE")
        body = json.loads(text, object_pairs_hook=_unique_object)
        if not isinstance(body, dict) or "event" not in body:
            raise ValueError("EVENT_MISSING")
        if set(body) - {"event", "request", "target", "enrollment"}:
            raise ValueError("UNKNOWN_INPUT_FIELD")
        result = dry_run(**body)
    except (ValueError, TypeError, RecursionError):
        result = {
            "status": "REFUSED", "dry_run": True, "schema_valid": False,
            "binding_valid": None, "enrollment_status": "UNKNOWN",
            "merge_authorized": False, "reasons": ["CLI_INPUT_INVALID"],
            "scope": "PYTHON_FORMAT_PROVENANCE_CORRELATION_ONLY",
        }
    print(json.dumps(result, sort_keys=True, allow_nan=False), file=outgoing)
    return 2 if result["status"] == "REFUSED" else 3


if __name__ == "__main__":
    raise SystemExit(main())


