"""Rule 9b activation-receipt verifier.

Implemented so far (red-test-first, v5 slice 1): the canonical JSON encoder
that produces the bytes the ConfirmDigest is taken over. The receipt schema,
security probes, task probes and admission verdict land in later slices and
are deliberately absent rather than stubbed, so nothing can mistake an
unimplemented check for a passing one.

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

import hashlib
import json
from typing import Any

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
