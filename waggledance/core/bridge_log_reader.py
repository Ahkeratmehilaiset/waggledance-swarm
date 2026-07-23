"""Fail-closed, cursor-neutral bridge JSONL snapshot/delta reader.

This module is an adapter core.  It never persists a cursor, creates a
generation sidecar, or mutates the bridge log.  Callers may commit the
returned candidate cursor only after they have accepted the returned rows.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, BinaryIO


DEFAULT_MAX_BYTES = 4 * 1024 * 1024
MAX_MAX_BYTES = 64 * 1024 * 1024
_MAX_GENERATION_BYTES = 512
_MAX_JSON_NESTING_DEPTH = 32
_MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991
_GENERATION_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_UTF8_BOM = b"\xef\xbb\xbf"


class BridgeReadStatus(str, Enum):
    """The only outcomes exposed by the reader contract."""

    OK = "OK"
    IDLE = "IDLE"
    RETRY = "RETRY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class BridgeCursor:
    """An accepted position in one bridge-log generation."""

    offset: int
    file_identity: str
    generation: str | None = None


@dataclass(frozen=True, slots=True)
class BridgeReadResult:
    """Rows plus a candidate cursor; the reader never commits it."""

    status: BridgeReadStatus
    reason: str
    rows: tuple[dict[str, Any], ...] = ()
    candidate_cursor: BridgeCursor | None = None
    bytes_read: int = 0
    bytes_consumed: int = 0
    snapshot_length: int | None = None
    read_calls: int = 0
    requested_offset: int = 0


class _GenerationError(Exception):
    def __init__(self, status: BridgeReadStatus, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {value}")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number is not finite")
    return parsed


def _parse_safe_json_int(value: str) -> int:
    parsed = int(value)
    if not -_MAX_SAFE_JSON_INTEGER <= parsed <= _MAX_SAFE_JSON_INTEGER:
        raise ValueError("JSON integer exceeds exact binary64 range")
    return parsed


def _ascii_fold_bridge_key(key: str) -> str:
    """Fold only ASCII A-Z; every non-ASCII code point remains distinct."""

    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in key)


def _bridge_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    normalized_keys: set[str] = set()
    for key, value in pairs:
        if not key.isascii():
            raise ValueError("JSON object property names must be ASCII")
        folded = _ascii_fold_bridge_key(key)
        if folded in normalized_keys:
            raise ValueError("duplicate or ASCII-case-colliding JSON key")
        normalized_keys.add(folded)
        result[key] = value
    return result


def _validate_json_lexical_contract(text: str) -> None:
    """Enforce bounded nesting and surrogate-escape rules before parsing.

    Direct supplementary Unicode characters decoded from valid UTF-8 remain
    valid.  JSON ``\\u`` escapes whose UTF-16 code unit is a surrogate are
    rejected so Python and Windows PowerShell cannot decode them differently.
    """

    depth = 0
    in_string = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            if char == '"':
                in_string = False
            elif char == "\\":
                if index + 1 >= len(text):
                    return  # The JSON parser reports the malformed escape.
                escape = text[index + 1]
                if escape == "u" and index + 5 < len(text):
                    digits = text[index + 2 : index + 6]
                    if all(digit in "0123456789abcdefABCDEF" for digit in digits):
                        code_unit = int(digits, 16)
                        if 0xD800 <= code_unit <= 0xDFFF:
                            raise ValueError("JSON UTF-16 surrogate escapes are forbidden")
                        index += 5
                    else:
                        index += 1
                else:
                    index += 1
            elif 0xD800 <= ord(char) <= 0xDFFF:
                raise ValueError("unpaired Unicode surrogate")
        else:
            if char == '"':
                in_string = True
            elif char in "[{":
                depth += 1
                if depth > _MAX_JSON_NESTING_DEPTH:
                    raise ValueError("JSON nesting exceeds contract limit")
            elif char in "]}":
                depth -= 1
        index += 1


def _load_bridge_json(text: str) -> Any:
    _validate_json_lexical_contract(text)
    return json.loads(
        text,
        parse_constant=_reject_json_constant,
        parse_float=_parse_finite_json_float,
        parse_int=_parse_safe_json_int,
        object_pairs_hook=_bridge_json_object,
    )


if os.name == "nt":
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation_time", _FILETIME),
            ("last_access_time", _FILETIME),
            ("last_write_time", _FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        )


def _file_identity(stream: BinaryIO) -> str:
    """Return identity from the already-open log handle."""

    if os.name == "nt":
        import msvcrt

        info = _BY_HANDLE_FILE_INFORMATION()
        handle = msvcrt.get_osfhandle(stream.fileno())
        get_info = ctypes.WinDLL("kernel32", use_last_error=True).GetFileInformationByHandle
        get_info.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
        )
        get_info.restype = wintypes.BOOL
        if not get_info(handle, ctypes.byref(info)):
            error = ctypes.get_last_error()
            raise OSError(error, "GetFileInformationByHandle failed")
        return (
            f"windows-v1:{info.volume_serial:08x}:"
            f"{info.file_index_high:08x}{info.file_index_low:08x}"
        )

    stat = os.fstat(stream.fileno())
    return f"posix-v1:{stat.st_dev:x}:{stat.st_ino:x}"


def _open_log(path: Path) -> BinaryIO:
    """Open once, explicitly permitting append writers and replacement."""

    if os.name == "nt":
        import msvcrt

        create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        handle = create_file(
            str(path),
            0x80000000,  # GENERIC_READ
            0x00000001 | 0x00000002 | 0x00000004,  # READ | WRITE | DELETE
            None,
            3,  # OPEN_EXISTING
            0x00000080,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
        except BaseException:
            ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(handle)
            raise
        return os.fdopen(descriptor, "rb", buffering=0)
    return path.open("rb", buffering=0)


def _read_generation(path: Path) -> str:
    """Read the optional, externally managed generation document."""

    try:
        with path.open("rb") as stream:
            raw = stream.read(_MAX_GENERATION_BYTES + 1)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        raise _GenerationError(
            BridgeReadStatus.RETRY, "generation_unavailable"
        ) from exc
    if len(raw) > _MAX_GENERATION_BYTES:
        raise _GenerationError(BridgeReadStatus.BLOCKED, "generation_invalid")
    if raw.startswith(_UTF8_BOM):
        raise _GenerationError(BridgeReadStatus.BLOCKED, "generation_bom")
    try:
        text = raw.decode("utf-8", errors="strict")
        document = _load_bridge_json(text)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise _GenerationError(
            BridgeReadStatus.BLOCKED, "generation_invalid"
        ) from exc
    if not isinstance(document, dict) or set(document) != {"generation"}:
        raise _GenerationError(BridgeReadStatus.BLOCKED, "generation_invalid")
    generation = document.get("generation")
    if not isinstance(generation, str) or not _GENERATION_RE.fullmatch(generation):
        raise _GenerationError(BridgeReadStatus.BLOCKED, "generation_invalid")
    return generation


def _result(
    status: BridgeReadStatus,
    reason: str,
    *,
    offset: int,
    rows: tuple[dict[str, Any], ...] = (),
    candidate: BridgeCursor | None = None,
    bytes_read: int = 0,
    bytes_consumed: int = 0,
    snapshot_length: int | None = None,
    read_calls: int = 0,
) -> BridgeReadResult:
    return BridgeReadResult(
        status=status,
        reason=reason,
        rows=rows,
        candidate_cursor=candidate,
        bytes_read=bytes_read,
        bytes_consumed=bytes_consumed,
        snapshot_length=snapshot_length,
        read_calls=read_calls,
        requested_offset=offset,
    )


def _validate_cursor(cursor: BridgeCursor | None) -> str | None:
    if cursor is None:
        return None
    if not isinstance(cursor, BridgeCursor):
        return "cursor_invalid"
    if isinstance(cursor.offset, bool) or not isinstance(cursor.offset, int):
        return "cursor_invalid"
    if (
        cursor.offset < 0
        or not isinstance(cursor.file_identity, str)
        or not cursor.file_identity
    ):
        return "cursor_invalid"
    if cursor.generation is not None and (
        not isinstance(cursor.generation, str)
        or not _GENERATION_RE.fullmatch(cursor.generation)
    ):
        return "cursor_invalid"
    return None


def _parse_rows(payload: bytes, *, starts_at_zero: bool) -> tuple[dict[str, Any], ...]:
    if starts_at_zero and payload.startswith(_UTF8_BOM):
        raise ValueError("log_bom")
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid_utf8") from exc

    parsed: list[dict[str, Any]] = []
    for line in text.split("\n")[:-1]:
        if line.endswith("\r"):
            line = line[:-1]
        if not line:
            raise ValueError("json_not_object")
        try:
            row = _load_bridge_json(line)
        except (json.JSONDecodeError, ValueError, RecursionError) as exc:
            raise ValueError("invalid_json") from exc
        if not isinstance(row, dict):
            raise ValueError("json_not_object")
        parsed.append(row)
    return tuple(parsed)


def read_bridge_log(
    path: str | os.PathLike[str],
    *,
    cursor: BridgeCursor | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    generation_path: str | os.PathLike[str] | None = None,
) -> BridgeReadResult:
    """Read a stable, bounded JSONL snapshot or delta.

    With no cursor, reading starts at byte zero (snapshot).  With a cursor,
    only bytes at and after its offset are read (delta).  A generation path is
    optional, but when configured it is checked before and after the read and
    must match the cursor on delta reads.
    """

    cursor_error = _validate_cursor(cursor)
    offset = cursor.offset if isinstance(cursor, BridgeCursor) else 0
    if cursor_error:
        return _result(BridgeReadStatus.BLOCKED, cursor_error, offset=offset)
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or not 1 <= max_bytes <= MAX_MAX_BYTES
    ):
        return _result(BridgeReadStatus.BLOCKED, "max_bytes_invalid", offset=offset)

    generation_file = Path(generation_path) if generation_path is not None else None
    if generation_file is None and cursor is not None and cursor.generation is not None:
        return _result(
            BridgeReadStatus.BLOCKED, "generation_configuration_changed", offset=offset
        )

    generation_before: str | None = None
    if generation_file is not None:
        try:
            generation_before = _read_generation(generation_file)
        except _GenerationError as exc:
            return _result(exc.status, exc.reason, offset=offset)
        if cursor is not None and cursor.generation != generation_before:
            return _result(
                BridgeReadStatus.RETRY, "generation_changed", offset=offset
            )

    log_path = Path(path)
    stream: BinaryIO | None = None
    try:
        try:
            stream = _open_log(log_path)
        except (FileNotFoundError, NotADirectoryError):
            status = BridgeReadStatus.IDLE if cursor is None else BridgeReadStatus.RETRY
            reason = "log_missing" if cursor is None else "log_disappeared"
            return _result(status, reason, offset=offset)
        except (PermissionError, OSError):
            return _result(BridgeReadStatus.RETRY, "log_unavailable", offset=offset)

        try:
            identity = _file_identity(stream)
            snapshot_length = os.fstat(stream.fileno()).st_size
        except OSError:
            return _result(BridgeReadStatus.BLOCKED, "identity_unavailable", offset=offset)

        if cursor is not None and cursor.file_identity != identity:
            return _result(
                BridgeReadStatus.RETRY,
                "file_identity_changed",
                offset=offset,
                snapshot_length=snapshot_length,
            )
        if offset > snapshot_length:
            return _result(
                BridgeReadStatus.RETRY,
                "log_truncated",
                offset=offset,
                snapshot_length=snapshot_length,
            )

        validation_bytes_read = 0
        read_calls = 0
        if offset:
            try:
                stream.seek(offset - 1, os.SEEK_SET)
                read_calls = 1
                preceding = stream.read(1)
            except OSError:
                return _result(
                    BridgeReadStatus.RETRY,
                    "log_io_error",
                    offset=offset,
                    snapshot_length=snapshot_length,
                )
            read_calls = 1
            validation_bytes_read = len(preceding)
            if len(preceding) != 1:
                return _result(
                    BridgeReadStatus.RETRY,
                    "snapshot_changed",
                    offset=offset,
                    bytes_read=validation_bytes_read,
                    snapshot_length=snapshot_length,
                    read_calls=read_calls,
                )
            if preceding != b"\n":
                return _result(
                    BridgeReadStatus.BLOCKED,
                    "cursor_not_lf_boundary",
                    offset=offset,
                    bytes_read=validation_bytes_read,
                    snapshot_length=snapshot_length,
                    read_calls=read_calls,
                )

        remaining = snapshot_length - offset
        requested = min(remaining, max_bytes)
        data = b""
        if requested:
            try:
                stream.seek(offset, os.SEEK_SET)
                read_calls += 1
                data = stream.read(requested)
            except OSError:
                return _result(
                    BridgeReadStatus.RETRY,
                    "log_io_error",
                    offset=offset,
                    bytes_read=validation_bytes_read,
                    snapshot_length=snapshot_length,
                    read_calls=read_calls,
                )
            if len(data) != requested:
                return _result(
                    BridgeReadStatus.RETRY,
                    "snapshot_changed",
                    offset=offset,
                    bytes_read=validation_bytes_read + len(data),
                    snapshot_length=snapshot_length,
                    read_calls=read_calls,
                )

        last_lf = data.rfind(b"\n")
        if last_lf < 0:
            status = (
                BridgeReadStatus.BLOCKED
                if len(data) >= max_bytes
                else BridgeReadStatus.IDLE
            )
            reason = "record_exceeds_max_bytes" if status is BridgeReadStatus.BLOCKED else "partial_record"
            candidate = None
            if status is BridgeReadStatus.IDLE:
                candidate = BridgeCursor(offset, identity, generation_before)
            result = _result(
                status,
                reason,
                offset=offset,
                candidate=candidate,
                bytes_read=validation_bytes_read + len(data),
                snapshot_length=snapshot_length,
                read_calls=read_calls,
            )
        else:
            consumed = last_lf + 1
            try:
                rows = _parse_rows(data[:consumed], starts_at_zero=(offset == 0))
            except ValueError as exc:
                result = _result(
                    BridgeReadStatus.BLOCKED,
                    str(exc),
                    offset=offset,
                    bytes_read=validation_bytes_read + len(data),
                    snapshot_length=snapshot_length,
                    read_calls=read_calls,
                )
            else:
                next_offset = offset + consumed
                result = _result(
                    BridgeReadStatus.OK,
                    "rows_available",
                    offset=offset,
                    rows=rows,
                    candidate=BridgeCursor(next_offset, identity, generation_before),
                    bytes_read=validation_bytes_read + len(data),
                    bytes_consumed=consumed,
                    snapshot_length=snapshot_length,
                    read_calls=read_calls,
                )

        try:
            after_identity = _file_identity(stream)
            after_length = os.fstat(stream.fileno()).st_size
        except OSError:
            return _result(
                BridgeReadStatus.RETRY,
                "snapshot_changed",
                offset=offset,
                bytes_read=result.bytes_read,
                snapshot_length=snapshot_length,
                read_calls=result.read_calls,
            )
        if after_identity != identity:
            return _result(
                BridgeReadStatus.RETRY,
                "file_identity_changed_during_read",
                offset=offset,
                bytes_read=result.bytes_read,
                snapshot_length=snapshot_length,
                read_calls=result.read_calls,
            )
        if after_length < snapshot_length:
            return _result(
                BridgeReadStatus.RETRY,
                "snapshot_changed",
                offset=offset,
                bytes_read=result.bytes_read,
                snapshot_length=snapshot_length,
                read_calls=result.read_calls,
            )

        if generation_file is not None:
            try:
                generation_after = _read_generation(generation_file)
            except _GenerationError as exc:
                return _result(
                    exc.status,
                    exc.reason,
                    offset=offset,
                    bytes_read=result.bytes_read,
                    snapshot_length=snapshot_length,
                    read_calls=result.read_calls,
                )
            if generation_after != generation_before:
                return _result(
                    BridgeReadStatus.RETRY,
                    "generation_changed_during_read",
                    offset=offset,
                    bytes_read=result.bytes_read,
                    snapshot_length=snapshot_length,
                    read_calls=result.read_calls,
                )
        return result
    finally:
        if stream is not None:
            stream.close()


__all__ = (
    "BridgeCursor",
    "BridgeReadResult",
    "BridgeReadStatus",
    "DEFAULT_MAX_BYTES",
    "MAX_MAX_BYTES",
    "read_bridge_log",
)
