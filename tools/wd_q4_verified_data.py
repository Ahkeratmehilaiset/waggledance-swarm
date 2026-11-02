"""Default-off, synthetic pinned data snapshot; no filesystem I/O or live caller.

The caller independently supplies both allowed names and trusted expected hashes.
Hashes authenticate neither publisher nor policy, identity or freshness. This
helper is not wired to a Q4 consumer and does not isolate hostile same-process
code, native backends, disk paths or data read outside its exact-name accessor.
"""
from dataclasses import dataclass
import hashlib
import hmac
import re
from types import MappingProxyType


MAX_ITEM_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 1024
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMPONENT = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*\Z")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE)


class DataRefused(ValueError):
    """Fixed refusal without names, bytes or digests."""


def _valid_name(name):
    if type(name) is not str or not 1 <= len(name) <= 1024:
        return False
    parts = name.split("/")
    return all(1 <= len(part) <= 255 and _COMPONENT.fullmatch(part) is not None
               and _DEVICE.fullmatch(part) is None for part in parts)


@dataclass(frozen=True, slots=True)
class _Snapshot:
    _items: MappingProxyType

    def read(self, name):
        if _valid_name(name) and name in self._items:
            return self._items[name]
        raise DataRefused("verified_data_refused") from None


def make_snapshot(*, enabled=False, data=None, expected_sha256=None, required_names=None):
    """Copy caller-supplied bytes only after the complete exact-name set validates."""
    if enabled is False:
        return None
    try:
        if (enabled is not True or type(data) is not dict or type(expected_sha256) is not dict
                or type(required_names) is not frozenset or not 1 <= len(required_names) <= MAX_FILES
                or len(data) != len(required_names) or len(expected_sha256) != len(required_names)
                or any(not _valid_name(name) for name in required_names)
                or len({name.casefold() for name in required_names}) != len(required_names)
                or any(type(name) is not str for name in data)
                or any(type(name) is not str for name in expected_sha256)
                or set(data) != required_names or set(expected_sha256) != required_names):
            raise ValueError()
        owned, total = {}, 0
        for name in required_names:
            raw, expected = data[name], expected_sha256[name]
            if (type(raw) is not bytes or len(raw) > MAX_ITEM_BYTES or type(expected) is not str
                    or _DIGEST.fullmatch(expected) is None):
                raise ValueError()
            total += len(raw)
            if total > MAX_TOTAL_BYTES or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected):
                raise ValueError()
            owned[name] = memoryview(raw).tobytes()
        return _Snapshot(MappingProxyType(owned))
    except Exception:
        pass
    raise DataRefused("verified_data_refused") from None
