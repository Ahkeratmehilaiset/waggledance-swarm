"""Rule 9b activation-receipt verifier.

Implemented so far: the canonical JSON encoder that produces the bytes the
ConfirmDigest is taken over (v5 slice 1), and the CLOSED receipt schema with
independent receipt verification (v5 slice 2). The security probes, task
probes, sealed runtime manifest checks and the full admission verdict land in
later slices and are deliberately absent rather than stubbed, so nothing can
mistake an unimplemented check for a passing one.

Why the encoder lives here and not in a shared utility: the digest has
exactly two producers -- this verifier and the elevated PowerShell activator
-- and they must agree byte for byte. Keeping the Python side next to the
code that re-derives the digest at verification time makes the pairing
visible; the parity contract itself is enforced by
``tests/tools/test_wd_rule9b_activation.py``, which runs both implementations
against a shared vector table under both PowerShell hosts.

The two encoders must also REFUSE the same inputs, not merely agree on the
ones they both accept. Where one side would silently coerce an input the
other rejects, the coercion is the bug: PowerShell's UTF-8 encoder replaces a
lone surrogate with U+FFFD while Python raises, and PowerShell's dictionary
path used to cast a non-string key to a string and then look the original
value up under that string, silently emitting null. Every such input is now
refused on both sides with a typed error.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

# Object keys are restricted to printable ASCII, and this is a real
# constraint rather than a stylistic one.
#
# Python's ``sort_keys=True`` orders keys by Unicode code point. The .NET
# ordinal comparer the PowerShell side must use orders them by UTF-16 code
# unit. Those two orders are identical across the Basic Multilingual Plane
# and DIVERGE for astral keys, which sort above U+E000..U+FFFF in UTF-16
# order and below them by code point. Rather than test that divergence and
# hope no receipt ever hits it, the encoders refuse the inputs that could
# reach it. The Rule 9b receipt schema is closed and every field name in it
# is ASCII, so this costs nothing and removes the class.
_KEY_MIN = 0x20
_KEY_MAX = 0x7E

# Integers are limited to what BOTH sides can represent. PowerShell has no
# arbitrary-precision integer in this path; Python does. An unbounded Python
# integer would encode here and throw there, which is a divergence even
# though the PowerShell side fails closed.
_INT_MIN = -(2 ** 63)
_INT_MAX = 2 ** 63 - 1

_SURROGATE_MIN = 0xD800
_SURROGATE_MAX = 0xDFFF


class CanonicalJsonError(ValueError):
    """Raised when a payload cannot be canonicalized deterministically."""


def _assert_string_encodable(value: str, path: str) -> None:
    """Refuse text that the two encoders would not agree how to encode.

    A lone surrogate is not valid Unicode text. Python's UTF-8 encoder
    raises on it; .NET's silently substitutes U+FFFD. Left unchecked the
    PowerShell side would produce bytes for a payload the Python side calls
    unencodable -- and would produce bytes that are not the payload.
    """
    for index, char in enumerate(value):
        code = ord(char)
        if _SURROGATE_MIN <= code <= _SURROGATE_MAX:
            raise CanonicalJsonError(
                f"{path}: lone UTF-16 surrogate U+{code:04X} at index {index}; "
                "this is not valid Unicode text and the two encoders disagree "
                "about it -- Python refuses it and .NET silently substitutes "
                "U+FFFD"
            )


def _assert_canonicalizable(value: Any, path: str = "$") -> None:
    """Refuse anything whose canonical form is not provably reproducible."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        _assert_string_encodable(value, path)
        return
    if isinstance(value, int):
        if not (_INT_MIN <= value <= _INT_MAX):
            raise CanonicalJsonError(
                f"{path}: integer {value} is outside signed 64-bit range; "
                "the PowerShell encoder cannot represent it, so the two sides "
                "would not agree"
            )
        return
    if isinstance(value, float):
        raise CanonicalJsonError(
            f"{path}: floats have no single reproducible JSON form; "
            "represent the value as an integer or a string"
        )
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJsonError(
                    f"{path}: object keys must be strings, got {type(key)!r}"
                )
            for char in key:
                if not (_KEY_MIN <= ord(char) <= _KEY_MAX):
                    raise CanonicalJsonError(
                        f"{path}: object key {key!r} contains a non-printable-ASCII "
                        "character; Python and .NET order such keys differently, so "
                        "the canonical form would depend on which side produced it"
                    )
            _assert_canonicalizable(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_canonicalizable(item, f"{path}[{index}]")
        return
    raise CanonicalJsonError(f"{path}: unsupported type {type(value)!r}")


def canonical_json_bytes(payload: Any) -> bytes:
    """Return the exact bytes the ConfirmDigest is computed over.

    ``ensure_ascii=False`` is load-bearing. At its default of True, Python
    escapes every non-ASCII character, U+007F and astral pairs, while a
    conforming RFC 8259 encoder emits them literally -- so the two producers
    would disagree on any path or message containing such a character, and
    the digest the operator signs would not identify the payload applied.

    Escaping is therefore exactly what RFC 8259 requires and nothing more:
    the quotation mark, the reverse solidus, and control characters below
    U+0020. The solidus is not escaped. U+007F is not escaped.
    """
    _assert_canonicalizable(payload)
    text = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")


def canonical_receipt_digest(payload: Any) -> str:
    """SHA-256 over the canonical bytes, lowercase hex."""
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


# ---------------------------------------------------------------------------
# Closed receipt schema
# ---------------------------------------------------------------------------
# The schema is CLOSED: an unknown field is a blocker, not something to ignore.
# An open schema lets whoever can write the authority root add fields a future
# reader might honour, and lets a field vanish in a refactor with nothing
# noticing. Every field below is required; the only legitimately empty value is
# previous_driver_sha256 on a first activation, and that exception is spelled
# out rather than achieved by loosening the type.
RECEIPT_SCHEMA_ID = "wd.rule9b.activation_receipt.v1"

# A receipt stamped slightly ahead of this clock is tolerated; anything beyond
# is refused rather than explained away.
APPLIED_AT_FUTURE_SKEW_MINUTES = 2
MAX_APPLY_WINDOW_DAYS = 30

_HEX40 = "0123456789abcdef"


def _is_hex(value: Any, length: int) -> bool:
    """Lowercase hex of an exact length. Uppercase is refused deliberately:
    two spellings of one SHA would compare unequal in one place and equal in
    another."""
    return (
        isinstance(value, str)
        and len(value) == length
        and all(ch in _HEX40 for ch in value)
    )


def _is_sha1(value: Any) -> bool:
    return _is_hex(value, 40)


def _is_sha256(value: Any) -> bool:
    return _is_hex(value, 64)


def _is_optional_sha256(value: Any) -> bool:
    """Empty only, or a full SHA-256. Not "none", not null, not absent."""
    return value == "" or _is_sha256(value)


def _is_positive_int(value: Any) -> bool:
    # bool is an int in Python; a boolean file count is a defect, not a count.
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == dt.timedelta(0)


def _is_canonical_relpath(value: Any) -> bool:
    """A repository-relative POSIX path and nothing else.

    Backslashes, absolute paths, drive letters and parent traversal are all
    refused: a consumer resolving such a path would read somewhere the
    activation never covered.
    """
    if not isinstance(value, str) or not value:
        return False
    if "\\" in value or value.startswith("/") or ":" in value:
        return False
    parts = value.split("/")
    return all(p and p not in (".", "..") for p in parts)


def _is_relpath_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
        _is_canonical_relpath(item) for item in value
    )


def _is_cause_b_blob_pair(value: Any) -> bool:
    """Exactly the two cause-B blob ids, never one and never a longer list."""
    return isinstance(value, list) and len(value) == 2 and all(_is_sha1(v) for v in value)


def _is_schema_id(value: Any) -> bool:
    return value == RECEIPT_SCHEMA_ID


RECEIPT_FIELDS: Mapping[str, Any] = {
    "schema": _is_schema_id,
    "activation_pr_number": _is_positive_int,
    "activation_head": _is_sha1,
    "activation_base_sha": _is_sha1,
    "activation_tree_sha": _is_sha1,
    "activation_pr_changed_paths": _is_relpath_list,
    "runtime_generation_id": lambda v: isinstance(v, str) and bool(v),
    "runtime_manifest_sha256": _is_sha256,
    "runtime_file_count": _is_positive_int,
    "driver_sha256": _is_sha256,
    "verifier_sha256": _is_sha256,
    "previous_driver_sha256": _is_optional_sha256,
    "cause_b_blob_ids": _is_cause_b_blob_pair,
    "applied_at_utc": _is_utc_timestamp,
    "effective_expiry_utc": _is_utc_timestamp,
    "confirm_digest": lambda v: _is_sha256(v),
}

DIGEST_EXCLUDED_FIELD = "confirm_digest"


def validate_receipt_schema(receipt: Any) -> list[str]:
    """Return every schema blocker. Never raises: callers branch on the list."""
    blockers: list[str] = []
    if not isinstance(receipt, dict):
        return [f"receipt must be a JSON object, got {type(receipt).__name__}"]

    for name in receipt:
        if name not in RECEIPT_FIELDS:
            blockers.append(f"unknown field: {name}")

    for name, is_valid in RECEIPT_FIELDS.items():
        if name not in receipt:
            blockers.append(f"missing required field: {name}")
            continue
        if not is_valid(receipt[name]):
            blockers.append(f"malformed field: {name}")

    return blockers


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
        dt.timezone.utc
    )


def verify_receipt_file(
    path: Any,
    *,
    expected_activation_head: str,
    now_utc: dt.datetime,
) -> dict[str, Any]:
    """Open, parse and verify a receipt. There is no way to skip this.

    Deliberately has no skip/trust/force parameter: an optional fail-closed
    check is not a fail-closed check. The caller gets a report and branches on
    it; a report is only ``verified`` when the blocker list is empty.
    """
    blockers: list[str] = []
    receipt: Any = None

    file_path = Path(path)
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        return {
            "verified": False,
            "blockers": [f"receipt unreadable: {exc.__class__.__name__}"],
            "receipt": None,
        }

    # Strict UTF-8, never utf-8-sig. A BOM-tolerant read would accept bytes
    # that differ from the signed canonical payload.
    if raw.startswith(b"\xef\xbb\xbf"):
        return {
            "verified": False,
            "blockers": ["receipt has a UTF-8 BOM; the signed payload has none"],
            "receipt": None,
        }
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "verified": False,
            "blockers": [f"receipt is not valid UTF-8 JSON: {exc.__class__.__name__}"],
            "receipt": None,
        }

    blockers.extend(validate_receipt_schema(receipt))
    if blockers:
        # Nothing below can be trusted to have the right shape.
        return {"verified": False, "blockers": blockers, "receipt": receipt}

    body = {k: v for k, v in receipt.items() if k != DIGEST_EXCLUDED_FIELD}
    try:
        expected_digest = canonical_receipt_digest(body)
    except CanonicalJsonError as exc:
        blockers.append(f"receipt is not canonicalizable: {exc}")
        return {"verified": False, "blockers": blockers, "receipt": receipt}
    if receipt[DIGEST_EXCLUDED_FIELD] != expected_digest:
        blockers.append(
            "confirm_digest does not match the receipt contents; "
            "the receipt was altered after it was sealed"
        )

    if receipt["activation_head"] != expected_activation_head:
        blockers.append(
            "activation_head does not match the signed activation generation: "
            f"{receipt['activation_head']} != {expected_activation_head}"
        )

    applied_at = _parse_utc(receipt["applied_at_utc"])
    expiry = _parse_utc(receipt["effective_expiry_utc"])
    if not isinstance(now_utc, dt.datetime) or now_utc.tzinfo is None:
        blockers.append("verification clock must be a timezone-aware UTC datetime")
        return {"verified": False, "blockers": blockers, "receipt": receipt}
    now = now_utc.astimezone(dt.timezone.utc)

    if applied_at > now + dt.timedelta(minutes=APPLIED_AT_FUTURE_SKEW_MINUTES):
        blockers.append(
            "applied_at_utc is in the future; that is a clock problem or a "
            "forged window, not evidence of an activation"
        )
    if expiry <= applied_at:
        blockers.append("effective_expiry_utc is not after applied_at_utc")
    if expiry - applied_at > dt.timedelta(days=MAX_APPLY_WINDOW_DAYS):
        blockers.append(
            f"the window exceeds the {MAX_APPLY_WINDOW_DAYS}-day cap; a frozen "
            "policy must not outlive the policy it froze"
        )
    if now >= expiry:
        blockers.append("the receipt has expired")

    return {"verified": not blockers, "blockers": blockers, "receipt": receipt}
