"""Default-off, pure public-anchor continuation checks; no publisher or reader.

This is a mix-up guard, not authentication or a proof that the log was read.
The caller must independently preserve the exact root and previous anchor
digests and must obtain the expected candidate cursor from its trusted prior
handoff. A caller that constructs those assertions itself proves no origin,
continuity, completeness, freshness, access control or resistance to same-user
replacement. Source rotation requires a separately authorized new root.

Inputs are supplied bytes and a caller-supplied cursor only. The function never
opens a source, reads or writes a file, schedules work, or publishes records.
It checks ONE immediate transition; it cannot reconstruct earlier transitions.
The returned closed result contains no source paths, cursor or event content.
"""

import hashlib
import hmac
import json
import ntpath
import posixpath
import re


_LIMIT = 16384
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = frozenset({"schema", "source_path", "generation_path", "cursor_start", "policy_sha256",
                   "roster_sha256", "status_mode", "public_policy_sha256"})
_CURSOR_KEYS = frozenset({"offset", "file_identity", "generation"})
_DIGEST_FIELDS = ("policy_sha256", "roster_sha256", "public_policy_sha256")


class SeriesRefused(ValueError):
    """Fixed refusal without disclosing any source path, cursor, or pin."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _text(value):
    return type(value) is str and 0 < len(value) <= _LIMIT and "\x00" not in value


def _cursor(value):
    if (type(value) is not dict or set(value) != _CURSOR_KEYS
            or type(value["offset"]) is not int or not 0 <= value["offset"] <= (1 << 63) - 1
            or not _text(value["file_identity"]) or not _text(value["generation"])):
        raise ValueError()
    return {key: value[key] for key in _CURSOR_KEYS}


def _anchor(raw, expected):
    if (type(raw) is not bytes or len(raw) > _LIMIT or type(expected) is not str
            or _HEX.fullmatch(expected) is None
            or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
        raise ValueError()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if (type(value) is not dict or set(value) != _KEYS or value["schema"] != "wd.q4-source-anchor.v1"
            or value["status_mode"] != "public-v1"
            or any(type(value[name]) is not str or _HEX.fullmatch(value[name]) is None
                   for name in _DIGEST_FIELDS)):
        raise ValueError()
    for name in ("source_path", "generation_path"):
        path = value[name]
        if not _text(path) or not (ntpath.isabs(path) or posixpath.isabs(path)):
            raise ValueError()
    value["cursor_start"] = _cursor(value["cursor_start"])
    return value


def check_continuation(*, enabled=False, root_anchor_bytes=None, root_anchor_sha256=None,
                       previous_anchor_bytes=None, previous_anchor_sha256=None,
                       next_anchor_bytes=None, next_anchor_sha256=None, expected_candidate_cursor=None):
    """Check one caller-bound transition, returning ADVANCED or NO_ADVANCE.

    Previous/root and next/previous identity and generation must match. The next
    cursor must equal the separately supplied candidate exactly, and offsets may
    never move behind either the prior cursor or the original root. Equal offsets
    represent an idle/no-advance transition, not a fresh or complete source.
    A successful result is never a publisher identity or evidence of a read.
    """
    if enabled is False:
        return {"status": "OFF"}
    try:
        if enabled is not True:
            raise ValueError()
        root = _anchor(root_anchor_bytes, root_anchor_sha256)
        previous = _anchor(previous_anchor_bytes, previous_anchor_sha256)
        following = _anchor(next_anchor_bytes, next_anchor_sha256)
        candidate = _cursor(expected_candidate_cursor)
        for key in _KEYS - {"cursor_start"}:
            if root[key] != previous[key] or root[key] != following[key]:
                raise ValueError()
        initial, prior, next_cursor = root["cursor_start"], previous["cursor_start"], following["cursor_start"]
        for key in ("file_identity", "generation"):
            if initial[key] != prior[key] or initial[key] != next_cursor[key]:
                raise ValueError()
        if (next_cursor != candidate or prior["offset"] < initial["offset"]
                or next_cursor["offset"] < prior["offset"]):
            raise ValueError()
        status = "ADVANCED" if next_cursor["offset"] > prior["offset"] else "NO_ADVANCE"
        return {"status": status, "authority": "none", "source_authenticity": "UNKNOWN"}
    except Exception:
        raise SeriesRefused("public_series_refused") from None
