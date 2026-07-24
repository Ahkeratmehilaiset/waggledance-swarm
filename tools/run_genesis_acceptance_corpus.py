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
_JSON_BOMS = (
    b"\x00\x00\xfe\xff",
    b"\xff\xfe\x00\x00",
    b"\xef\xbb\xbf",
    b"\xfe\xff",
    b"\xff\xfe",
)

_DOMAIN_ID = "wd.cell_identity.digest.v1"
_DOMAIN_LIN = "wd.genesis_lineage.digest.v1"
_SCHEMA_ID = "wd.cell_identity.v1"
_SCHEMA_LIN = "wd.genesis_lineage.v1"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_UTC = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z$"
)
_CASE_ID = re.compile(
    r"^(identity|lineage|restore|authority|timestamp)\."
    r"(positive|negative)\.[a-z0-9_]+$"
)
AXES = frozenset({"identity", "lineage", "restore", "authority", "timestamp"})
CASE_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "case_id",
        "axis",
        "oracle_verdict",
        "verifier_verdict",
        "reason",
        "diverged",
        "matched_expectation",
        "operator_authorized",
        "oracle_digest",
    }
)
IDENTITY_KEYS = frozenset(
    {
        "schema_version",
        "cell_id",
        "pubkey_digest",
        "genesis_material_digest",
        "created_at_utc",
    }
)
LINEAGE_KEYS = frozenset(
    {
        "schema_version",
        "cell_id",
        "parent_cell_id",
        "lineage_prev_hash",
        "depth",
        "inherited_goal_slice_digest",
        "inherited_budget_slice_digest",
        "entry_hash",
    }
)
AUTHORITY_SUBJECT_KEYS = frozenset({"identity_mapping"})

# Frozen W2C-A v4 inert-corpus cells. The manifest is deliberately exact:
# repeating an axis/kind under a new label cannot satisfy matrix coverage.
REQUIRED_CASE_MANIFEST = {
    "identity.positive.positive": ("identity", "positive"),
    "identity.negative.cell_id_mismatch": ("identity", "negative"),
    "identity.negative.forged_id": ("identity", "negative"),
    "identity.negative.bad_sha256_shape": ("identity", "negative"),
    "identity.negative.missing_key": ("identity", "negative"),
    "identity.negative.null_field": ("identity", "negative"),
    "identity.negative.wrong_type_field": ("identity", "negative"),
    "identity.negative.extra_key": ("identity", "negative"),
    "lineage.positive.positive_entry": ("lineage", "positive"),
    "lineage.positive.positive_link": ("lineage", "positive"),
    "lineage.positive.positive_registry": ("lineage", "positive"),
    "lineage.negative.entry_hash_mismatch": ("lineage", "negative"),
    "lineage.negative.self_parent": ("lineage", "negative"),
    "lineage.negative.depth_off_by_one": ("lineage", "negative"),
    "lineage.negative.root_marker_disagreement": ("lineage", "negative"),
    "lineage.negative.orphan_parent": ("lineage", "negative"),
    "lineage.negative.two_roots": ("lineage", "negative"),
    "lineage.negative.duplicate_cell_id": ("lineage", "negative"),
    "lineage.negative.broken_prev_link": ("lineage", "negative"),
    "lineage.negative.non_monotonic_depth": ("lineage", "negative"),
    "restore.positive.same_facts_same_id": ("restore", "positive"),
    "restore.positive.from_stored_mapping": ("restore", "positive"),
    "restore.negative.drifted_field_rejects": ("restore", "negative"),
    "authority.positive.identity_only": ("authority", "positive"),
    "authority.negative.grant_field": ("authority", "negative"),
    "authority.negative.budget_field": ("authority", "negative"),
    "authority.negative.capability_field": ("authority", "negative"),
    "timestamp.positive.positive": ("timestamp", "positive"),
    "timestamp.negative.unicode_decimal": ("timestamp", "negative"),
    "timestamp.negative.impossible_calendar": ("timestamp", "negative"),
    "timestamp.negative.trailing_zero_fraction": ("timestamp", "negative"),
    "timestamp.negative.non_utc": ("timestamp", "negative"),
    "timestamp.negative.naive": ("timestamp", "negative"),
}

# Hostile Python values cannot be represented by inert JSON. The CLI reports
# these required cells but never claims to have executed them.
REQUIRED_CODE_PROBES = frozenset(
    {
        "subclass_value_lying_eq",
        "homoglyph_key",
        "flapping_mapping",
        "nonreturning_mapping",
        "nonreturning_sequence",
        "dict_subclass",
        "list_subclass",
    }
)

# Attributable W2C-A reconstruction proceeds by completed axis. Each digest
# binds the full inert case: id, axis/kind, subject, golden, and exact expected
# verdict/reason. Unpinned manifest cells remain visibly missing rather than
# being credited through labels alone.
PINNED_CASE_DIGESTS = {
    "identity.positive.positive": (
        "sha256:841dc9e7bec0502b22d5b83a1012871005e641e719085b2d3827e3d24a7b7a55"
    ),
    "identity.negative.cell_id_mismatch": (
        "sha256:560bf7219af413d7eee8bf813518de29359532efb1528487bc372ab18443abc1"
    ),
    "identity.negative.forged_id": (
        "sha256:1b89c1d1807490d748a5ae798f4cd447ad130eadd8b427306bdd086b62d03a9d"
    ),
    "identity.negative.bad_sha256_shape": (
        "sha256:06cbf934fb5a8df7a0c02dd800aff411e298a97e22c6d40c5c72ec5074a11514"
    ),
    "identity.negative.missing_key": (
        "sha256:244ef2f26d30e8fdcd2a0d962acc386f758b951f15735525e0781dbbaac8b1b4"
    ),
    "identity.negative.null_field": (
        "sha256:69587ec3d0dfbbf9460c1b461fd79aca83ce283b29f1c6a52ce6eb7d28f53d22"
    ),
    "identity.negative.wrong_type_field": (
        "sha256:65d437480a0b9f61582771dcd876d3776b0a163abc90e4eb3516dbaa5c1da9f2"
    ),
    "identity.negative.extra_key": (
        "sha256:16b75846dfcef64d525ae4a1bd3fa56d766386a720bc740b60dee6b5f72dd924"
    ),
    "lineage.positive.positive_entry": (
        "sha256:eb00e1fb2e54e893341eeda3c9c0d9248cf8c1efb523dc8b6ca6d823ae56aed3"
    ),
    "lineage.positive.positive_link": (
        "sha256:cdfd5561465ed1cba5251b9c21fe8d7ef28818b7c6698cf688c440370fd24188"
    ),
    "lineage.positive.positive_registry": (
        "sha256:f3b646ac4471326bb293831767b25f49b60bc91ff165d4142b1c55c2aa3cdded"
    ),
    "lineage.negative.entry_hash_mismatch": (
        "sha256:efe31d0d8726e9d423088ec658f6f91a444f8b9ef1974f7044b745e949c9957b"
    ),
    "lineage.negative.self_parent": (
        "sha256:4279c9111616607bb781d2560e7f5bcc8fff464b55333070c753b8dc7f8730e8"
    ),
    "lineage.negative.depth_off_by_one": (
        "sha256:13dd6cf1e3d5849dfd620834916f5dc4297c3ffa69a1cc2036cd83b1277a8d9e"
    ),
    "lineage.negative.root_marker_disagreement": (
        "sha256:bcdda1170c91b7f0f143d5a97994c627d3500034afc9475644b59d1ec1ca4e88"
    ),
    "lineage.negative.orphan_parent": (
        "sha256:9c77bc6de641c4c7ec62d6f3240f6b9994bca4d9a89e9816d9a065a2cccc0383"
    ),
    "lineage.negative.two_roots": (
        "sha256:78c37f1ad3f0426644f2c8656cc5394ff952bd2d5000e85844258f7b5b034987"
    ),
    "lineage.negative.duplicate_cell_id": (
        "sha256:c56eedb733360af29aaf34fef67af2b4eea735e985e29eaefa950086f541ae5d"
    ),
    "lineage.negative.broken_prev_link": (
        "sha256:3506612816e2ec98c9978f26f32aafd9da16e65b65197ab3776419b936c8ae86"
    ),
    "lineage.negative.non_monotonic_depth": (
        "sha256:bc8164149d052245681daf2a91de745303398b7ef7023a0aaa4ffa466fb9baca"
    ),
    "authority.positive.identity_only": (
        "sha256:a3ec2ae77efd3e325b7ee4bc128cd1d970e77ee32ac5ccf3977a13aa5405afb9"
    ),
    "authority.negative.grant_field": (
        "sha256:afead55748591c41d4386c9d82b55e84aeb66fb866ca2744aea1a59feb73e4fe"
    ),
    "authority.negative.budget_field": (
        "sha256:119c26fd52c249ed2f5ac21f551d8f4b756f427cfa9be1fe36aec7a5d9644cff"
    ),
    "authority.negative.capability_field": (
        "sha256:a35374f62b384af8940e6f852b0e4c3658f97262ab3534461e58b6abebbf3751"
    ),
    "timestamp.positive.positive": (
        "sha256:d2b97e07956cb4ab31c093773c175c464b58d8e2508c5f13fd30a5733175b8ec"
    ),
    "timestamp.negative.unicode_decimal": (
        "sha256:5725b85399af387102392f8eaf710cee4b1c233d26cd8edb6cffdb1e51e8a134"
    ),
    "timestamp.negative.impossible_calendar": (
        "sha256:fcd2a470e6a2ec536f585b84920e63d5112d52e2ead9916c933b88af6470bf6a"
    ),
    "timestamp.negative.trailing_zero_fraction": (
        "sha256:7eaeda22cd02477b81977c3867c9dc099b192e694ca48948b85dbfb69b0b4f09"
    ),
    "timestamp.negative.non_utc": (
        "sha256:65c9d38410618bee707991d84af91c128e969e3f46b1620fa92c54872afc1d0d"
    ),
    "timestamp.negative.naive": (
        "sha256:8b04bdf8599e4651e5039f07a47f3a1342740ca4c59bee0bc6cbfab99a0a0788"
    ),
}


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
    if set(report) != CASE_REPORT_KEYS:
        raise CorpusError("report:keyset")
    fields = {
        "schema_version": report.get("schema_version"),
        "case_id": report.get("case_id"),
        "axis": report.get("axis"),
        "oracle_verdict": report.get("oracle_verdict"),
        "verifier_verdict": report.get("verifier_verdict"),
        "reason": report.get("reason"),
        "diverged": report.get("diverged"),
        "matched_expectation": report.get("matched_expectation"),
        "operator_authorized": report.get("operator_authorized"),
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
    if type(fields["diverged"]) is not bool:
        raise CorpusError("report:diverged")
    if type(fields["matched_expectation"]) is not bool:
        raise CorpusError("report:matched_expectation")
    if type(fields["operator_authorized"]) is not bool:
        raise CorpusError("report:operator_authorized")
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
    _require_bounded_json_depth(case)
    try:
        case_bytes = _canonical_bytes(case)
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise CorpusError("case:json") from exc
    if len(case_bytes) > MAX_CASE_BYTES:
        raise CorpusError("caps:case_bytes")
    expectation = case.get("expect")
    if type(expectation) is not dict or set(expectation) != {"verdict", "reason"}:
        raise CorpusError("expect:not_mapping")
    if set(case) != {"case_id", "kind", "axis", "subject", "golden", "expect"}:
        raise CorpusError("case:keyset")
    if (
        type(case["case_id"]) is not str
        or _CASE_ID.fullmatch(case["case_id"]) is None
    ):
        raise CorpusError("case:case_id")
    if type(case["kind"]) is not str or case["kind"] not in {
        "positive",
        "negative",
    }:
        raise CorpusError("case:kind")
    if type(case["axis"]) is not str or case["axis"] not in AXES:
        raise CorpusError("case:axis")
    binding = REQUIRED_CASE_MANIFEST.get(case["case_id"])
    if binding is None:
        raise CorpusError("case:semantic_id")
    if binding != (case["axis"], case["kind"]):
        raise CorpusError("case:semantic_binding")
    if type(case["subject"]) is not dict:
        raise CorpusError("case:subject")
    if case["golden"] is not None and type(case["golden"]) is not dict:
        raise CorpusError("case:golden")
    if expectation["verdict"] not in {"ACCEPT", "REJECT"}:
        raise CorpusError("expect:verdict")
    if expectation["reason"] is not None and type(expectation["reason"]) is not str:
        raise CorpusError("expect:reason")
    pinned_digest = PINNED_CASE_DIGESTS.get(case["case_id"])
    if pinned_digest is not None and _digest(case) != pinned_digest:
        raise CorpusError("case:semantic_digest")
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
    if cell_id == parent_cell_id:
        raise CorpusError("self_parent")
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
    if raw.startswith(_JSON_BOMS):
        raise CorpusError("corpus:encoding")
    try:
        text = raw.decode("utf-8", errors="strict")
        document = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as exc:
        raise CorpusError("corpus:encoding") from exc
    except (ValueError, RecursionError) as exc:
        raise CorpusError("corpus:json") from exc
    _require_bounded_json_depth(document)
    try:
        _canonical_bytes(document)
    except (TypeError, UnicodeError, ValueError, RecursionError) as exc:
        raise CorpusError("corpus:json") from exc
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
    case_ids: list[str] = []
    for case in cases:
        if type(case) is not dict:
            raise CorpusError("case:not_mapping")
        checked_case = _require_case_shape(case)
        case_ids.append(checked_case["case_id"])
    if len(case_ids) != len(set(case_ids)):
        raise CorpusError("case:duplicate_id")
    return document


def _identity_result(case: dict[str, Any]) -> dict[str, Any]:
    from waggledance.core.cell_identity import verify_cell_identity

    mapping = case["subject"]["identity_mapping"]
    oracle_ok = False
    if (
        type(mapping) is dict
        and set(mapping) == IDENTITY_KEYS
        and type(mapping.get("schema_version")) is str
        and mapping["schema_version"] == _SCHEMA_ID
    ):
        try:
            expected = ref_cell_id(
                pubkey_digest=mapping["pubkey_digest"],
                genesis_material_digest=mapping["genesis_material_digest"],
                created_at_utc=mapping["created_at_utc"],
            )
        except (CorpusError, KeyError, TypeError, ValueError):
            pass
        else:
            oracle_ok = (
                type(mapping.get("cell_id")) is str
                and mapping["cell_id"] == expected
            )
    verifier_ok, verifier_reason = verify_cell_identity(mapping)
    return {
        "oracle_verdict": "ACCEPT" if oracle_ok else "REJECT",
        "verifier_verdict": "ACCEPT" if verifier_ok else "REJECT",
        "reason": None if verifier_ok else verifier_reason,
        "diverged": oracle_ok is not verifier_ok,
    }


def _authority_result(case: dict[str, Any]) -> dict[str, Any]:
    """Verify the zero-authority envelope before invoking the identity SUT."""

    subject = case["subject"]
    if set(subject) != AUTHORITY_SUBJECT_KEYS:
        return {
            "oracle_verdict": "REJECT",
            "verifier_verdict": "REJECT",
            "reason": "authority:keyset",
            "diverged": False,
        }
    return _identity_result(case)


def _lineage_entry_reference(entry: object) -> tuple[bool, str | None]:
    if type(entry) is not dict or set(entry) != LINEAGE_KEYS:
        return False, None
    if (
        type(entry.get("schema_version")) is not str
        or entry["schema_version"] != _SCHEMA_LIN
    ):
        return False, None
    try:
        expected = ref_entry_hash(
            **{
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
        )
    except (CorpusError, KeyError, TypeError, ValueError):
        return False, None
    return (
        type(entry.get("entry_hash")) is str
        and entry["entry_hash"] == expected,
        expected,
    )


def _lineage_link_reference(child: object, parent: object) -> bool:
    child_ok, _ = _lineage_entry_reference(child)
    parent_ok, _ = _lineage_entry_reference(parent)
    if not child_ok or not parent_ok:
        return False
    assert type(child) is dict and type(parent) is dict
    return bool(
        child["parent_cell_id"] == parent["cell_id"]
        and child["lineage_prev_hash"] == parent["entry_hash"]
        and child["depth"] == parent["depth"] + 1
    )


def _lineage_registry_reference(entries: object) -> bool:
    if type(entries) not in {list, tuple} or not entries:
        return False
    frozen: list[dict[str, Any]] = []
    for entry in entries:
        ok, _ = _lineage_entry_reference(entry)
        if not ok or type(entry) is not dict:
            return False
        frozen.append(entry)
    by_cell: dict[str, dict[str, Any]] = {}
    for entry in frozen:
        cell_id = entry["cell_id"]
        if cell_id in by_cell:
            return False
        by_cell[cell_id] = entry
    roots = [
        entry
        for entry in frozen
        if entry["parent_cell_id"] == "genesis:root"
    ]
    if len(roots) != 1:
        return False
    for entry in frozen:
        if entry["parent_cell_id"] == "genesis:root":
            continue
        parent = by_cell.get(entry["parent_cell_id"])
        if parent is None or not _lineage_link_reference(entry, parent):
            return False
    return True


def _lineage_result(case: dict[str, Any]) -> dict[str, Any]:
    from waggledance.core.genesis_lineage import (
        verify_lineage_entry,
        verify_lineage_link,
        verify_lineage_registry,
    )

    subject = case["subject"]
    lineage_kind = subject["lineage_kind"]
    if lineage_kind == "entry":
        entry = subject["entry"]
        oracle_ok, expected = _lineage_entry_reference(entry)
        golden = case["golden"]
        if golden is not None:
            oracle_ok = bool(
                oracle_ok
                and expected == golden.get("entry_hash")
                and entry.get("depth") == golden.get("depth")
            )
        verifier_ok, verifier_reason = verify_lineage_entry(entry)
    elif lineage_kind == "link":
        link = subject["link"]
        oracle_ok = _lineage_link_reference(link["child"], link["parent"])
        verifier_ok, verifier_reason = verify_lineage_link(
            link["child"], link["parent"]
        )
    elif lineage_kind == "registry":
        registry = subject["registry"]
        oracle_ok = _lineage_registry_reference(registry)
        verifier_ok, verifier_reason = verify_lineage_registry(registry)
    else:
        raise CorpusError("lineage:kind")
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
        if axis in {"identity", "timestamp"}:
            result = _identity_result(checked_case)
        elif axis == "authority":
            result = _authority_result(checked_case)
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
        and result["reason"] == expectation.get("reason")
        and not result["diverged"]
    )
    result["operator_authorized"] = False
    result["oracle_digest"] = None
    try:
        result["oracle_digest"] = derive_case_report_digest(result)
    except CorpusError:
        pass
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
    present_case_ids = {case["case_id"] for case in document["cases"]}
    missing_case_ids = sorted(
        set(REQUIRED_CASE_MANIFEST).difference(present_case_ids)
    )
    corpus_matrix_complete = not missing_case_ids
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
        "missing_case_ids": missing_case_ids,
        "corpus_matrix_complete": corpus_matrix_complete,
        "required_code_probes": sorted(REQUIRED_CODE_PROBES),
        "code_probe_gate": "separate_executable_test_required",
        "coverage_complete": corpus_matrix_complete,
        "ok": not mismatches and not divergences and corpus_matrix_complete,
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
