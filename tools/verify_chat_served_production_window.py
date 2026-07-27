# SPDX-License-Identifier: BUSL-1.1
"""Read-only JSON CLI for one exact chat-served production measurement window."""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.verify_magma_receipt import verify_manifest  # noqa: E402
from waggledance.core.magma.canonical import sha256_digest  # noqa: E402
from waggledance.core.magma.chat_served_claim_window_evidence import (  # noqa: E402
    PRODUCTION_WINDOW_VERIFICATION_SCHEMA,
    ProductionWindowVerification,
    verify_production_window,
)

INPUT_SCHEMA = "magma.chat_served_production_window_verifier_input.v1"
_INPUT_KEYS = frozenset({
    "schema_version",
    "start_boundary",
    "final_boundary",
    "clean_shutdown_marker",
    "ledger_entries",
    "enabled_samples",
    "pending_failures",
    "receipt_index",
    "served_point_observations",
})
_MANIFEST_KEYS = frozenset({"chain_id", "entries"})
_MANIFEST_ENTRY_KEYS = frozenset({"receipt", "payload", "evaluation_result"})
_CHAT_SERVED_CHAIN_ID = "magma:chat_service:served:v0"
MAX_JSON_INPUT_BYTES = 64 * 1024 * 1024


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Verify a closed chat-served production-window evidence envelope. "
            "The command is measurement-only and never mutates runtime state."
        ),
    )
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--receipt-root", required=True, type=Path)
    parser.add_argument("--expected-window-id", required=True)
    parser.add_argument("--expected-source-head", required=True)
    parser.add_argument(
        "--previously-verified-window-id",
        action="append",
        default=[],
    )
    return parser


def _invalid_result() -> ProductionWindowVerification:
    return ProductionWindowVerification(
        ok=False,
        phase="pre_marker_rejected",
        reason="cli_input_invalid",
        marker_verified=False,
        ledger_entries=0,
        enabled_samples=0,
        pending_failures=0,
        receipt_index_entries=0,
        served_point_observations=0,
        receipt_terminals=0,
    )


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON value")


def _read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_JSON_INPUT_BYTES:
        raise ValueError("JSON input exceeds bound")
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_object_without_duplicates,
        parse_constant=_reject_constant,
    )


def _path_is_link(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & reparse_flag)


def _safe_relative(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


class _ReceiptResolver:
    """Contained, no-follow receipt loader plus canonical-verifier callback."""

    def __init__(self, receipt_root: Path) -> None:
        self._root_lexical = Path(os.path.abspath(os.fspath(receipt_root)))
        if (
            not self._root_lexical.is_dir()
            or _path_is_link(self._root_lexical)
        ):
            raise ValueError("invalid receipt root")
        self._root_resolved = self._root_lexical.resolve(strict=True)
        self._bundle_paths: dict[str, Path] = {}

    def _contained_file(self, relative: object, *, parent: Path | None = None) -> Path:
        if not _safe_relative(relative):
            raise ValueError("unsafe relative artifact reference")
        base = self._root_lexical if parent is None else parent
        candidate = base.joinpath(*str(relative).split("/"))
        lexical = Path(os.path.abspath(os.fspath(candidate)))
        try:
            lexical.relative_to(self._root_lexical)
        except ValueError as exc:
            raise ValueError("artifact escapes receipt root") from exc
        cursor = self._root_lexical
        for part in lexical.relative_to(self._root_lexical).parts:
            cursor = cursor / part
            if _path_is_link(cursor):
                raise ValueError("linked receipt artifact")
        resolved = lexical.resolve(strict=True)
        try:
            resolved.relative_to(self._root_resolved)
        except ValueError as exc:
            raise ValueError("resolved artifact escapes receipt root") from exc
        if not resolved.is_file():
            raise ValueError("receipt artifact is not a file")
        return resolved

    def resolve(self, manifest_ref: str) -> Mapping[str, Any] | None:
        manifest_path = self._contained_file(manifest_ref)
        manifest = _read_json(manifest_path)
        if (
            type(manifest) is not dict
            or set(manifest) != _MANIFEST_KEYS
            or manifest.get("chain_id") != _CHAT_SERVED_CHAIN_ID
        ):
            return None
        entries = manifest.get("entries")
        if not isinstance(entries, list) or len(entries) != 1:
            return None
        entry = entries[0]
        if type(entry) is not dict or set(entry) != _MANIFEST_ENTRY_KEYS:
            return None
        receipt_path = self._contained_file(
            entry["receipt"],
            parent=manifest_path.parent,
        )
        payload_path = self._contained_file(
            entry["payload"],
            parent=manifest_path.parent,
        )
        # Resolve evaluation too: a missing/escaping sidecar must fail before the
        # canonical verifier is considered.
        self._contained_file(
            entry["evaluation_result"],
            parent=manifest_path.parent,
        )
        bundle = {
            "receipt": _read_json(receipt_path),
            "payload": _read_json(payload_path),
        }
        self._bundle_paths[sha256_digest(bundle)] = manifest_path
        return bundle

    def verify(self, bundle: Mapping[str, Any]) -> bool:
        try:
            manifest_path = self._bundle_paths.get(sha256_digest(bundle))
            if manifest_path is None:
                return False
            report = verify_manifest(manifest_path)
            return bool(
                isinstance(report, Mapping)
                and report.get("ok") is True
                and type(report.get("receipt_count")) is int
                and report.get("receipt_count") == 1
                and report.get("errors") == []
            )
        except Exception:  # noqa: BLE001 - canonical verification fails closed
            return False


def _verify(
    envelope: object,
    receipt_root: Path,
    *,
    expected_window_id: str,
    expected_source_head: str,
    previously_verified_window_ids: Sequence[str],
) -> ProductionWindowVerification:
    if (
        type(envelope) is not dict
        or set(envelope) != _INPUT_KEYS
        or envelope.get("schema_version") != INPUT_SCHEMA
    ):
        return _invalid_result()
    try:
        resolver = _ReceiptResolver(receipt_root)
        return verify_production_window(
            expected_window_id=expected_window_id,
            expected_source_head=expected_source_head,
            start_boundary=envelope["start_boundary"],
            final_boundary=envelope["final_boundary"],
            clean_shutdown_marker=envelope["clean_shutdown_marker"],
            ledger_entries=envelope["ledger_entries"],
            enabled_samples=envelope["enabled_samples"],
            pending_failures=envelope["pending_failures"],
            receipt_index=envelope["receipt_index"],
            served_point_observations=envelope[
                "served_point_observations"
            ],
            resolve_receipt_bundle=resolver.resolve,
            verify_receipt_bundle=resolver.verify,
            content_address_receipt=sha256_digest,
            previously_verified_window_ids=previously_verified_window_ids,
        )
    except Exception:  # noqa: BLE001 - CLI reduces all errors to a fixed verdict
        return _invalid_result()


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        envelope = _read_json(args.evidence)
        result = _verify(
            envelope,
            args.receipt_root,
            expected_window_id=args.expected_window_id,
            expected_source_head=args.expected_source_head,
            previously_verified_window_ids=(
                args.previously_verified_window_id
            ),
        )
    except Exception:  # noqa: BLE001 - no paths or exception text enter JSON
        result = _invalid_result()
    payload = result._asdict()
    # Defensive contract assertions keep the stable output non-authorizing.
    assert payload["schema_version"] == PRODUCTION_WINDOW_VERIFICATION_SCHEMA
    assert payload["measurement_only"] is True
    assert payload["claim_safe_count"] == 0
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
