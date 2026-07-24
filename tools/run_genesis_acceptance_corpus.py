# SPDX-License-Identifier: BUSL-1.1
"""Offline W2A Genesis acceptance corpus runner.

The reference functions in this file intentionally hard-code the W2A v1
domains, schema tags, and canonical JSON recipe.  They do not import the W2A
implementation.  Public W2A builders/verifiers are invoked separately as the
system under test.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CORPUS_SCHEMA = "wd.genesis_acceptance_corpus.v1"
CASE_REPORT_SCHEMA = "wd.genesis_acceptance_report.v1"
REPORT_SCHEMA = "wd.genesis_acceptance_corpus_report.v1"
_CASE_REPORT_DIGEST_DOMAIN = "wd.genesis_acceptance_report.digest.v1"
MAX_RAW_BYTES = 1_048_576
MAX_CASES = 4_096
MAX_CASE_BYTES = 65_536
MAX_JSON_DEPTH = 128

_DOMAIN_ID = "wd.cell_identity.digest.v1"
_DOMAIN_LIN = "wd.genesis_lineage.digest.v1"
_SCHEMA_ID = "wd.cell_identity.v1"
_SCHEMA_LIN = "wd.genesis_lineage.v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z$"
)


class CorpusError(ValueError):
    """The corpus is outside the bounded inert-data contract."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _require_bounded_json_depth(value: object) -> None:
    pending = [(value, 0)]
    while pending:
        current, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise CorpusError("caps:json_depth")
        if type(current) is dict:
            pending.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            pending.extend((item, depth + 1) for item in current)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def derive_case_report_digest(report: object) -> str:
    """Re-derive the digest-bound portion of an AcceptanceReportV1."""

    if type(report) is not dict:
        raise CorpusError("report:not_mapping")
    fields = {
        "schema_version": report.get("schema_version"),
        "case_id": report.get("case_id"),
        "axis": report.get("axis"),
        "oracle_verdict": report.get("oracle_verdict"),
        "verifier_verdict": report.get("verifier_verdict"),
        "reason": report.get("reason"),
    }
    if (
        type(fields["schema_version"]) is not str
        or fields["schema_version"] != CASE_REPORT_SCHEMA
    ):
        raise CorpusError("report:schema_version")
    if type(fields["case_id"]) is not str:
        raise CorpusError("report:case_id")
    if type(fields["axis"]) is not str or fields["axis"] not in {
        "identity",
        "lineage",
        "restore",
        "authority",
        "timestamp",
    }:
        raise CorpusError("report:axis")
    if (
        type(fields["oracle_verdict"]) is not str
        or fields["oracle_verdict"] not in {"ACCEPT", "REJECT"}
    ):
        raise CorpusError("report:oracle_verdict")
    if (
        type(fields["verifier_verdict"]) is not str
        or fields["verifier_verdict"] not in {"ACCEPT", "REJECT"}
    ):
        raise CorpusError("report:verifier_verdict")
    if fields["reason"] is not None and type(fields["reason"]) is not str:
        raise CorpusError("report:reason")
    return _digest({"domain": _CASE_REPORT_DIGEST_DOMAIN, **fields})


def _require_plain_json(value: object) -> None:
    pending = [value]
    seen_containers: set[int] = set()
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > MAX_CASE_BYTES + 1:
            raise CorpusError("caps:case_bytes")
        if type(current) is dict:
            marker = id(current)
            if marker in seen_containers:
                raise CorpusError("case:json")
            seen_containers.add(marker)
            if any(type(key) is not str for key in current):
                raise CorpusError("case:json")
            pending.extend(current.values())
        elif type(current) is list:
            marker = id(current)
            if marker in seen_containers:
                raise CorpusError("case:json")
            seen_containers.add(marker)
            pending.extend(current)
        elif current is None or type(current) in {str, bool, int, float}:
            continue
        else:
            raise CorpusError("case:json")


def _require_case_shape(case: object) -> dict[str, Any]:
    if type(case) is not dict:
        raise CorpusError("case:not_mapping")
    _require_plain_json(case)
    try:
        case_bytes = _canonical_bytes(case)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CorpusError("case:json") from exc
    if len(case_bytes) > MAX_CASE_BYTES:
        raise CorpusError("caps:case_bytes")
    expectation = case.get("expect")
    if type(expectation) is not dict or set(expectation) != {"verdict", "reason"}:
        raise CorpusError("expect:not_mapping")
    if set(case) != {"case_id", "kind", "axis", "subject", "golden", "expect"}:
        raise CorpusError("case:keyset")
    if type(case["case_id"]) is not str or not case["case_id"]:
        raise CorpusError("case:case_id")
    if case["kind"] not in {"positive", "negative"}:
        raise CorpusError("case:kind")
    if case["axis"] not in {
        "identity",
        "lineage",
        "restore",
        "authority",
        "timestamp",
    }:
        raise CorpusError("case:axis")
    if type(case["subject"]) is not dict:
        raise CorpusError("case:subject")
    if case["golden"] is not None and type(case["golden"]) is not dict:
        raise CorpusError("case:golden")
    if expectation["verdict"] not in {"ACCEPT", "REJECT"}:
        raise CorpusError("expect:verdict")
    if expectation["reason"] is not None and type(expectation["reason"]) is not str:
        raise CorpusError("expect:reason")
    return case


def _require_digest(value: object, label: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise CorpusError(f"{label}:sha256")
    return value


def _require_timestamp(value: object) -> str:
    if type(value) is not str or _UTC.fullmatch(value) is None:
        raise CorpusError("created_at_utc:shape")
    try:
        datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError as exc:
        raise CorpusError("created_at_utc:calendar") from exc
    if "." in value and value[value.index(".") + 1 : -1].endswith("0"):
        raise CorpusError("created_at_utc:trailing_zero")
    return value


def ref_cell_id(
    *,
    pubkey_digest: str,
    genesis_material_digest: str,
    created_at_utc: str,
) -> str:
    """Independent CellIdentityV1 known-answer reference."""

    _require_digest(pubkey_digest, "pubkey_digest")
    _require_digest(genesis_material_digest, "genesis_material_digest")
    _require_timestamp(created_at_utc)
    return _digest(
        {
            "domain": _DOMAIN_ID,
            "schema_version": _SCHEMA_ID,
            "pubkey_digest": pubkey_digest,
            "genesis_material_digest": genesis_material_digest,
            "created_at_utc": created_at_utc,
        }
    )


def ref_entry_hash(
    *,
    cell_id: str,
    parent_cell_id: str,
    lineage_prev_hash: str,
    depth: int,
    inherited_goal_slice_digest: str,
    inherited_budget_slice_digest: str,
) -> str:
    """Independent GenesisLineageV1 known-answer reference."""

    _require_digest(cell_id, "cell_id")
    if type(parent_cell_id) is not str:
        raise CorpusError("parent_cell_id:type")
    if parent_cell_id != "genesis:root":
        _require_digest(parent_cell_id, "parent_cell_id")
    _require_digest(lineage_prev_hash, "lineage_prev_hash")
    _require_digest(inherited_goal_slice_digest, "goal_digest")
    _require_digest(inherited_budget_slice_digest, "budget_digest")
    if type(depth) is not int or not 0 <= depth <= 1_000_000:
        raise CorpusError("depth")
    is_root = parent_cell_id == "genesis:root"
    if is_root != (depth == 0) or is_root != (
        lineage_prev_hash == "sha256:" + "0" * 64
    ):
        raise CorpusError("root_markers")
    return _digest(
        {
            "domain": _DOMAIN_LIN,
            "schema_version": _SCHEMA_LIN,
            "cell_id": cell_id,
            "parent_cell_id": parent_cell_id,
            "lineage_prev_hash": lineage_prev_hash,
            "depth": depth,
            "inherited_goal_slice_digest": inherited_goal_slice_digest,
            "inherited_budget_slice_digest": inherited_budget_slice_digest,
        }
    )


def load_corpus(path: Path) -> dict[str, Any]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_RAW_BYTES + 1)
    if len(raw) > MAX_RAW_BYTES:
        raise CorpusError("caps:raw_bytes")
    try:
        document = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CorpusError("corpus:json") from exc
    _require_bounded_json_depth(document)
    if type(document) is not dict or set(document) != {
        "schema_version",
        "cases",
    }:
        raise CorpusError("corpus:keyset")
    if document["schema_version"] != CORPUS_SCHEMA:
        raise CorpusError("corpus:schema_version")
    cases = document["cases"]
    if type(cases) is not list:
        raise CorpusError("corpus:cases")
    if len(cases) > MAX_CASES:
        raise CorpusError("caps:case_count")
    for case in cases:
        if type(case) is not dict:
            raise CorpusError("case:not_mapping")
        if len(_canonical_bytes(case)) > MAX_CASE_BYTES:
            raise CorpusError("caps:case_bytes")
    return document


def _identity_result(case: dict[str, Any]) -> dict[str, Any]:
    from waggledance.core.cell_identity import verify_cell_identity

    mapping = case["subject"]["identity_mapping"]
    facts = {
        key: mapping[key]
        for key in (
            "pubkey_digest",
            "genesis_material_digest",
            "created_at_utc",
        )
    }
    expected = ref_cell_id(**facts)
    oracle_ok = mapping.get("cell_id") == expected
    verifier_ok, verifier_reason = verify_cell_identity(mapping)
    return {
        "oracle_verdict": "ACCEPT" if oracle_ok else "REJECT",
        "verifier_verdict": "ACCEPT" if verifier_ok else "REJECT",
        "reason": None if verifier_ok else verifier_reason,
        "diverged": oracle_ok is not verifier_ok,
    }


def _lineage_result(case: dict[str, Any]) -> dict[str, Any]:
    from waggledance.core.genesis_lineage import verify_lineage_entry

    entry = case["subject"]["entry"]
    fields = {
        key: entry[key]
        for key in (
            "cell_id",
            "parent_cell_id",
            "lineage_prev_hash",
            "depth",
            "inherited_goal_slice_digest",
            "inherited_budget_slice_digest",
        )
    }
    expected = ref_entry_hash(**fields)
    oracle_ok = entry.get("entry_hash") == expected
    verifier_ok, verifier_reason = verify_lineage_entry(entry)
    return {
        "oracle_verdict": "ACCEPT" if oracle_ok else "REJECT",
        "verifier_verdict": "ACCEPT" if verifier_ok else "REJECT",
        "reason": None if verifier_ok else verifier_reason,
        "diverged": oracle_ok is not verifier_ok,
    }


def _restore_result(case: dict[str, Any]) -> dict[str, Any]:
    from waggledance.core.cell_identity import (
        build_cell_identity,
        verify_cell_identity,
    )

    stored = case["subject"]["stored_mapping"]
    facts = {
        key: stored[key]
        for key in (
            "pubkey_digest",
            "genesis_material_digest",
            "created_at_utc",
        )
    }
    expected = ref_cell_id(**facts)
    stored_ok, stored_reason = verify_cell_identity(stored)
    rebuilt = build_cell_identity(**facts).to_mapping() if stored_ok else None
    oracle_ok = stored.get("cell_id") == expected
    verifier_ok = bool(
        stored_ok
        and rebuilt == stored
        and rebuilt["cell_id"] == case["golden"]["cell_id"]
    )
    return {
        "oracle_verdict": "ACCEPT" if oracle_ok else "REJECT",
        "verifier_verdict": "ACCEPT" if verifier_ok else "REJECT",
        "reason": None if verifier_ok else stored_reason or "restore_mismatch",
        "diverged": oracle_ok is not verifier_ok,
    }


def run_case(case: object) -> dict[str, Any]:
    case_id = case.get("case_id") if type(case) is dict else None
    axis = case.get("axis") if type(case) is dict else None
    expectation: object = None
    try:
        checked_case = _require_case_shape(case)
        case_id = checked_case["case_id"]
        axis = checked_case["axis"]
        expectation = checked_case["expect"]
        if axis in {"identity", "authority", "timestamp"}:
            result = _identity_result(checked_case)
        elif axis == "lineage":
            result = _lineage_result(checked_case)
        else:
            result = _restore_result(checked_case)
    except (CorpusError, KeyError, TypeError, ValueError) as exc:
        result = {
            "oracle_verdict": "REJECT",
            "verifier_verdict": "REJECT",
            "reason": str(exc) or type(exc).__name__,
            "diverged": False,
        }
    if result["diverged"]:
        result["reason"] = "oracle:verifier_divergence"
    result["schema_version"] = CASE_REPORT_SCHEMA
    result["case_id"] = case_id
    result["axis"] = axis
    result["matched_expectation"] = (
        type(expectation) is dict
        and result["verifier_verdict"] == expectation.get("verdict")
    )
    try:
        result["oracle_digest"] = derive_case_report_digest(result)
    except CorpusError:
        result["oracle_digest"] = None
    return result


def run_corpus(path: Path) -> dict[str, Any]:
    document = load_corpus(path)
    results = [run_case(case) for case in document["cases"]]
    mismatches = [
        result["case_id"] for result in results if not result["matched_expectation"]
    ]
    divergences = [
        result["case_id"] for result in results if result["diverged"]
    ]
    covered_axis_kinds = {
        (case.get("axis"), case.get("kind")) for case in document["cases"]
    }
    required_axis_kinds = {
        (axis, kind)
        for axis in ("identity", "lineage", "restore", "authority", "timestamp")
        for kind in ("positive", "negative")
    }
    coverage_complete = required_axis_kinds <= covered_axis_kinds
    return {
        "schema_version": REPORT_SCHEMA,
        "corpus_digest": _digest(document),
        "total": len(results),
        "accepted": sum(
            result["verifier_verdict"] == "ACCEPT" for result in results
        ),
        "rejected": sum(
            result["verifier_verdict"] == "REJECT" for result in results
        ),
        "mismatches": mismatches,
        "divergences": divergences,
        "coverage_complete": coverage_complete,
        "ok": not mismatches and not divergences and coverage_complete,
        "results": results,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_corpus(args.corpus)
    except (OSError, CorpusError) as exc:
        report = {"schema_version": REPORT_SCHEMA, "ok": False, "error": str(exc)}
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
