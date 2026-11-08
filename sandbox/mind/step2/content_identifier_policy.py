"""Caller-pinned private identifier policy; no environment or key management.

The caller must provision a secret random key and protect the policy bytes and
key. An HMAC proves byte consistency with that key, not the caller's authority.
This foundation does not activate content processing or persist any material.
"""

import hashlib
import hmac
import json
import re
import unicodedata

_SCHEMA = "wd.mind-identifier-policy.v1"
_TAG = re.compile(r"[0-9a-f]{64}\Z")


class PolicyRefused(ValueError):
    """Fixed-token refusal; input material is never included."""


def _refuse():
    raise PolicyRefused("policy_refused") from None


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


class IdentifierPolicy:
    """Immutable match interface; callers must not log or serialize its state."""

    __slots__ = ("__values",)

    def __init__(self, values):
        object.__setattr__(self, "_IdentifierPolicy__values", tuple(values))

    def __setattr__(self, name, value):
        raise AttributeError("immutable")

    def __repr__(self):
        return "<IdentifierPolicy private>"

    def contains(self, text):
        if type(text) is not str:
            _refuse()
        normalized = unicodedata.normalize("NFKC", text).casefold()
        return any(value in normalized for value in self.__values)


def load_policy(policy_bytes, key, expected_tag):
    """Validate caller-supplied private policy bytes and their keyed HMAC-SHA256."""
    try:
        if (type(policy_bytes) is not bytes or not 0 < len(policy_bytes) <= 65536
                or type(key) is not bytes or not 32 <= len(key) <= 1024
                or type(expected_tag) is not str or _TAG.fullmatch(expected_tag) is None):
            _refuse()
        actual = hmac.new(key, policy_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(actual, expected_tag):
            _refuse()
        document = json.loads(policy_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        if (type(document) is not dict or set(document) != {"schema", "identifiers"}
                or document["schema"] != _SCHEMA or type(document["identifiers"]) is not list
                or not 1 <= len(document["identifiers"]) <= 256):
            _refuse()
        values = []
        for item in document["identifiers"]:
            if type(item) is not str:
                _refuse()
            normalized = unicodedata.normalize("NFKC", item).casefold()
            if (not 3 <= len(normalized) <= 128 or normalized != normalized.strip()
                    or any(unicodedata.category(c).startswith("C") for c in normalized)
                    or normalized in values):
                _refuse()
            values.append(normalized)
        return IdentifierPolicy(values)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _refuse()
