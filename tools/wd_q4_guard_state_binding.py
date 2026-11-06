"""Bind a cooperative guard directory to ALREADY externally pinned fleet bytes.

The caller must first anchor the fleet pin to its independently supplied
deployment-manifest pin. A caller-computed hash is not external authority.
This module accepts no root from environment, query, cwd or a fallback. It
does not create directories, markers, ACLs, cleanup witnesses or native calls.

All directories must be provisioned separately. Revalidation detects observed
directory-identity changes; it is not a directory handle, a TOCTOU guarantee,
same-user authentication or proof of crash/power-loss durability. Callers must
revalidate immediately before guard acquisition and before verified release.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat


_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_HEX = re.compile(r"[0-9a-fA-F]{64}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_MAX_FLEET = 1024 * 1024


class GuardStateUnavailable(ValueError):
    """Fixed refusal without paths or input contents."""


def _check(condition):
    if not condition:
        raise ValueError()


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _check(key not in value)
        value[key] = item
    return value


def _constant(_value):
    raise ValueError()


def _root(value):
    _check(type(value) is str and 0 < len(value) <= 32767
           and not any(ord(char) < 32 or ord(char) == 127 for char in value)
           and not value.startswith(("\\\\", "//")))
    # Check lexical parts BEFORE Path can normalize away a dot segment.
    segments = value.replace("\\", "/").split("/")
    _check(not any(part in (".", "..") for part in segments))
    path = Path(value)
    _check(path.is_absolute() and path != Path(path.anchor))
    if os.name == "nt":
        _check(re.match(r"^[A-Za-z]:[\\/]", value) is not None
               and ":" not in value[2:])
    return path


def _observe(path):
    identities = []
    for part in (path, *path.parents):
        info = part.lstat()
        _check(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
               and not (getattr(info, "st_file_attributes", 0) & _REPARSE))
        identities.append((info.st_dev, info.st_ino))
    return tuple(identities)


class _BoundRoot:
    __slots__ = ("_path", "_identities")

    def __init__(self, path, identities):
        self._path, self._identities = path, identities

    @property
    def path(self):
        return self._path

    def revalidate(self):
        try:
            _check(_observe(self._path) == self._identities)
            return self._path
        except Exception:
            raise GuardStateUnavailable("guard_state_unavailable") from None


def bind_state_root(*, fleet_bytes, expected_fleet_sha256, lane):
    """Return path/revalidate for one fixed lane's existing fleet-derived root.

    expected_fleet_sha256 is the pin the caller has already obtained from its
    externally anchored manifest, not a replacement self-hash or query field.
    This helper checks those bytes again but does not establish the authority.
    """
    try:
        _check(type(lane) is str and lane in _LANES)
        _check(type(fleet_bytes) is bytes and 0 < len(fleet_bytes) <= _MAX_FLEET
               and type(expected_fleet_sha256) is str
               and _HEX.fullmatch(expected_fleet_sha256) is not None)
        _check(hmac.compare_digest(hashlib.sha256(fleet_bytes).hexdigest(),
                                   expected_fleet_sha256.lower()))
        fleet = json.loads(fleet_bytes.decode("utf-8", "strict"),
                           object_pairs_hook=_unique, parse_constant=_constant)
        _check(type(fleet) is dict)
        runtime = _root(fleet.get("runtime_root"))
        path = runtime / "shared" / ".codex-audit" / lane / "q4-caller-state"
        return _BoundRoot(path, _observe(path))
    except Exception:
        raise GuardStateUnavailable("guard_state_unavailable") from None
