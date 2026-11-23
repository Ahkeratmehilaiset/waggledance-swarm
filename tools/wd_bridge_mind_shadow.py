# SPDX-License-Identifier: BUSL-1.1
"""Dormant Bridge Mind shadow pipeline: one owned audit capture in, the whole Mind chain out; nothing is wired.

Inputs are files a caller already wrote under one ``.codex-audit`` root:

* ``--capture``: one ``wd.bridge-mind-capture.v1`` object holding the events one inherited pinned reader run
  returned (``reader``, ``reader_stdout_sha256`` of that run's exact output, ``observed_utc``, ``events``);
* ``--context`` (optional): one ``wd.bridge-mind-pr-context.v1`` PR context (see ``tools/bridge_mind_workspace.py``),
  observational data only;
* ``--previous`` (optional): the previous run's ``reflected.publication.json``.

This module never reads the bridge log, never calls the bridge, GitHub, a provider or a gate, never starts a
process and never writes a bridge event. One run is a pure function of its inputs (time is the capture's
``observed_utc``, never the wall clock):

1. Envelopes ``{ref, event}``: each ref is the canonical event id of the captured row. It is capture-derived: a
   row the reader truncated has a ref that is not the log's own, so the capture digest travels with every output.
2. ``tools/wd_bridge_mind.py`` ``full_rebuild`` over the capture window. With a capture ``provenance`` (the pinned
   compact reader's own position cursor plus the file identity and log generation it decodes to, re-decoded here and
   required to agree) the source names a capture WINDOW of that bridge log (``capture-window:<file_identity>``),
   never the log itself, and carries the generation as the reader reported it: ``None`` stays ``None`` and is
   reported UNKNOWN, never invented. Without provenance the legacy declaration stays (``capture-window:<capture
   digest>``, generation ``None``). Either way the cursor range is the window's own rows and the core's ``complete``
   is complete over that window only: nothing before the window is seen; it is never whole-log coverage. The
   ``capture-window:`` prefix is the window marker this repo's readers already bind on (RCO1 P1).
3. ``tools/bridge_mind_workspace.py`` ``reflect`` with the previous reflected snapshot and the context.
4. The reflected snapshot is published with the core's ``write_snapshot`` and read back with its
   ``read_snapshot``, so the envelope digest, the audit root and the path guards are verified. The previous
   publication is read the same way. An envelope is never stripped by hand.
5. Delta lines against the previous snapshot (``tools/wd_bridge_mind_delta_push.py``) and the mind text view of
   ``tools/wd_pr_missing_slot_board.py``, both from the verified snapshot, each guarded whole before it is written
   (the delta's ``render_lines``, the board's ``serialize_mind_view``).

``--out-dir`` must be a new directory inside the audit root; every file is created exclusively there. The receipt
(``receipt.json``, also printed) names every input and output digest and the run's limits. The core and the board
are imported only for a run: when either is missing the run refuses with a fixed message, never a fallback.
Every refusal is a fixed message that never echoes input content.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import bridge_mind_workspace as workspace  # noqa: E402
from tools import wd_bridge_mind_delta_push as delta_push  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

CAPTURE_SCHEMA = "wd.bridge-mind-capture.v1"
CAPTURE_KEYS = frozenset({"schema", "reader", "reader_stdout_sha256", "observed_utc", "events"})
# Optional capture provenance: the compact reader's position cursor the producer committed, with the file identity
# and log generation that cursor decodes to. Re-decoding and comparing them here proves the self-consistency of
# producer-written fields with the cursor they name; it does not authenticate who read the log or that the captured
# rows came from it (RCO1 P2). The cursor proves a position, not an origin.
PROVENANCE_KEYS = frozenset({"reader_cursor", "file_identity", "log_generation"})
WINDOW_PREFIX = "capture-window:"  # the published identity is always a window, named by its digest or by its log
_CURSOR = re.compile(r"bc1\.[A-Za-z0-9_-]{1,2044}\Z")
# The bridge log reader's own identity shape (waggledance/core/bridge_log_reader.py _file_identity):
# "windows-v1:<volume serial 8 hex>:<file index 16 hex>" or "posix-v1:<dev hex>:<ino hex>"; nothing else (RCO1 P3).
_LOG_IDENTITY = re.compile(r"(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]{1,16}:[0-9a-f]{1,16})\Z")
_LOG_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
# The inherited pinned readers a capture may come from; the reader string is a label, not authentication.
READERS = frozenset({"Read-AgentBridge.ps1 -Raw", "Read-AgentBridge.ps1 -Compact -EventId"})
RECEIPT_SCHEMA = "wd.bridge-mind-shadow-receipt.v1"
INPUT_LIMIT = 64 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
LIMITS = (
    "capture window only: nothing before the first captured event is seen, never whole-log coverage",
    "provenance binds the window to the bridge log and generation the pinned reader's cursor names, and the published "
    "identity stays a window of that log (capture-window:<file identity>), never the log itself; a null generation "
    "stays UNKNOWN and is never invented; the cursor proves a position, not who read it",
    "refs are capture-derived event ids; a row the pinned reader truncated has a ref that is not the log's own",
    "a digest proves bytes, not truth; identities are declared, never authenticated",
    "observe only: no gate, no verdict, no merge, no activation, no bridge write",
)


class ShadowRefused(ValueError):
    """Fixed messages only; never input content."""


def _module(name: str):
    try:
        return importlib.import_module(name)
    except ImportError:
        raise ShadowRefused(f"a required Mind module is missing: {name}") from None


def _owned(path: Path, root: Path, *, new: bool = False) -> Path:
    """An absolute path inside the audit root with no link on the way; ``new`` ones must not exist yet."""
    if root.name != ".codex-audit" or not root.is_dir():
        raise ShadowRefused("the audit root is not a .codex-audit directory")
    resolved_root = root.resolve(strict=True)
    if os.path.normcase(os.path.abspath(root)) != os.path.normcase(str(resolved_root)):
        raise ShadowRefused("the audit root goes through a link")
    if ".." in Path(path).parts:
        raise ShadowRefused("a path climbs out of its directory")
    target = Path(os.path.abspath(path))
    # A link or junction anywhere on the way makes the resolved path differ from the written one.
    if os.path.normcase(str(target.resolve(strict=False))) != os.path.normcase(str(target)):
        raise ShadowRefused("a path goes through a link")
    if resolved_root not in target.parents:
        raise ShadowRefused("a path is not inside the audit root")
    if new and target.exists():
        raise ShadowRefused("the output directory already exists")
    if not new and not target.is_file():
        raise ShadowRefused("an input file does not exist")
    return target


def _read_object(path: Path) -> tuple[dict[str, Any], str]:
    """One canonical JSON object (the bridge reader: no duplicate key, no NaN) and the sha256 of its bytes."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(INPUT_LIMIT + 1)
    except OSError:
        raise ShadowRefused("an input file cannot be read") from None
    if len(data) > INPUT_LIMIT:
        raise ShadowRefused("an input file is larger than the shadow run reads")
    try:
        value = parse_bridge_json_object(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ShadowRefused("an input file is not one canonical JSON object") from None
    return value, hashlib.sha256(data).hexdigest()


def decode_position(token: Any) -> tuple[str, str | None, int]:
    """``(file_identity, generation, offset)`` of a canonical compact-reader position cursor, else a fixed refusal.
    The body must re-encode to the very token (no padding, canonical key order), the identity must have the
    reader's token form, the generation must be ``None`` or a token and the offset a non-negative int."""
    if not isinstance(token, str) or not _CURSOR.fullmatch(token):
        raise ShadowRefused("the capture provenance is not a canonical compact-reader position cursor")
    body = token[len("bc1."):]
    try:
        value = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8"))
    except (ValueError, RecursionError):  # binascii.Error and UnicodeDecodeError are ValueErrors
        raise ShadowRefused("the capture provenance is not a canonical compact-reader position cursor") from None
    if not isinstance(value, dict) or set(value) != {"file_identity", "generation", "offset"}:
        raise ShadowRefused("the capture provenance is not a canonical compact-reader position cursor")
    identity, generation, offset = value["file_identity"], value["generation"], value["offset"]
    if (not isinstance(identity, str) or not _LOG_IDENTITY.fullmatch(identity) or type(offset) is not int or offset < 0
            or (generation is not None and (not isinstance(generation, str) or not _LOG_GENERATION.fullmatch(generation)))):
        raise ShadowRefused("the capture provenance is not a canonical compact-reader position cursor")
    canonical = json.dumps({"file_identity": identity, "generation": generation, "offset": offset},
                           sort_keys=True, separators=(",", ":")).encode("utf-8")
    if base64.urlsafe_b64encode(canonical).decode("ascii").rstrip("=") != body:
        raise ShadowRefused("the capture provenance is not a canonical compact-reader position cursor")
    return identity, generation, offset


def read_provenance(value: Any) -> dict[str, Any] | None:
    """The capture's provenance with the cursor re-decoded, or ``None`` for a legacy capture without one."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != PROVENANCE_KEYS:
        raise ShadowRefused("the capture provenance is not {reader_cursor, file_identity, log_generation}")
    identity, generation, _ = decode_position(value["reader_cursor"])
    if value["file_identity"] != identity or value["log_generation"] != generation:
        raise ShadowRefused("the capture provenance does not match its reader cursor")
    return {"reader_cursor": value["reader_cursor"], "file_identity": identity, "log_generation": generation}


def read_capture(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != CAPTURE_SCHEMA \
            or set(value) not in (CAPTURE_KEYS, CAPTURE_KEYS | {"provenance"}):
        raise ShadowRefused("the capture is not a wd.bridge-mind-capture.v1 object")
    if "provenance" in value:
        if not isinstance(value["provenance"], dict):
            raise ShadowRefused("the capture provenance is not {reader_cursor, file_identity, log_generation}")
        read_provenance(value["provenance"])
    if value["reader"] not in READERS or not isinstance(value["reader_stdout_sha256"], str) \
            or not _SHA.fullmatch(value["reader_stdout_sha256"]) or workspace.parse_time(value["observed_utc"]) is None:
        raise ShadowRefused("the capture does not name its pinned reader, output digest and observation time")
    if not isinstance(value["events"], list) or not all(isinstance(event, dict) for event in value["events"]):
        raise ShadowRefused("the capture events are not a list of objects")
    return value


def _write_new(path: Path, data: bytes, charge: Callable[[int], object] | None = None) -> str:
    if charge is not None:  # the caller's pre-write byte budget; it raises to refuse the write
        charge(len(data))
    with open(path, "xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def run(capture_path: Path, audit_root: Path, out_dir: Path, *, context_path: Path | None = None,
        previous_path: Path | None = None, charge: Callable[[int], object] | None = None) -> dict[str, Any]:
    """One shadow run. ``charge`` (optional; the capture producer's B5 budget) is called with each output's
    byte length before that output is written (publication, delta, board, receipt) and raises to refuse it."""
    core = _module("tools.wd_bridge_mind")
    board = _module("tools.wd_pr_missing_slot_board")
    from tools.bridge_compact_view import event_id

    capture_file = _owned(capture_path, audit_root)
    capture, capture_sha = _read_object(capture_file)
    capture = read_capture(capture)
    context, context_sha = (None, None)
    if context_path is not None:
        context, context_sha = _read_object(_owned(context_path, audit_root))
    out = _owned(out_dir, audit_root, new=True)
    events = capture["events"]
    as_of = capture["observed_utc"]
    provenance = read_provenance(capture.get("provenance"))
    if provenance is None:  # legacy capture: the window is its own log, named by its digest
        log_identity, generation = WINDOW_PREFIX + capture_sha, None
    else:  # a window of the bridge log the committed reader cursor names, never that log itself (RCO1 P1); a null
        # generation stays None (UNKNOWN), never invented
        log_identity, generation = WINDOW_PREFIX + provenance["file_identity"], provenance["log_generation"]
    # cursor_start/cursor_end are the window's own row range and the core's "complete" is complete over that window
    # only; the identity's window prefix is what readers bind on, so no field claims the whole log.
    source = {"log_identity": log_identity, "generation": generation, "cursor_start": 0,
              "cursor_end": len(events), "event_count": len(events), "coverage": "complete"}
    try:
        previous = None if previous_path is None else core.read_snapshot(_owned(previous_path, audit_root),
                                                                          audit_root=audit_root)
        state = core.full_rebuild([{"ref": event_id(event), "event": event} for event in events], source=source,
                                  as_of_utc=as_of)
        snapshot = core.snapshot(state, as_of_utc=as_of)
    except core.ProjectionError as exc:
        reason = str(exc) if str(exc) in core.BLOCKED_REASONS | {"snapshot_read_refused", "snapshot_publication_invalid",
                                                                   "snapshot_content_mismatch"} else "projection_refused"
        raise ShadowRefused("the core refused: " + reason) from None
    try:
        reflected = workspace.reflect(snapshot, previous, context)
    except workspace.WorkspaceError:
        raise ShadowRefused("the workspace refused the snapshot") from None
    out.mkdir()
    publication = out / "reflected.publication.json"
    try:
        digest = core.write_snapshot(reflected, publication, audit_root=audit_root, charge=charge)
        verified = core.read_snapshot(publication, audit_root=audit_root)
    except core.ProjectionError:
        raise ShadowRefused("the core refused the reflected publication") from None
    if verified != reflected:
        raise ShadowRefused("the publication read back differs from what was published")
    changes = None
    if previous is not None:
        try:
            changes = delta_push.delta(previous, verified)
        except delta_push.DeltaError:
            raise ShadowRefused("the delta refused the snapshot pair") from None
        _write_new(out / "delta.jsonl", delta_push.render_lines(changes).encode("utf-8"), charge)
    try:  # the board's guarded serializer, not its bare renderer: the board text is guarded whole like every output
        _, text = board.serialize_mind_view(board.build_mind_view(verified, digest), as_json=False)
    except board.MindRefused:
        raise ShadowRefused("the board refused the verified snapshot") from None
    board_sha = _write_new(out / "board.txt", text.encode("utf-8"), charge)
    receipt = {
        "schema": RECEIPT_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
        "capture": {"path": str(capture_file), "sha256": capture_sha, "reader": capture["reader"],
                    "reader_stdout_sha256": capture["reader_stdout_sha256"], "observed_utc": as_of,
                    "events": len(events),
                    "provenance": None if provenance is None else {
                        "file_identity": provenance["file_identity"],
                        "log_generation": "UNKNOWN" if generation is None else generation,
                        "reader_cursor_sha256": hashlib.sha256(provenance["reader_cursor"].encode("ascii")).hexdigest(),
                        "coverage": "bounded_window_not_whole_log"}},
        "context": None if context_path is None else {"path": str(Path(os.path.abspath(context_path))),
                                                      "sha256": context_sha},
        "previous": None if previous_path is None else {"path": str(Path(os.path.abspath(previous_path))),
                                                        "snapshot_sha256": hashlib.sha256(
                                                            core.canonical_bytes(previous)).hexdigest()},
        "publication": {"path": str(publication), "snapshot_sha256": digest},
        "delta_lines": None if changes is None else len(changes),
        "board_text_sha256": board_sha,
        "counts": {"prs": len(verified["prs"]), "lanes": len(verified["lanes"]), "facts": len(verified["facts"]),
                   "tensions": len(verified["tensions"]), "attention": len(verified["attention"])},
        "global_question": verified["next_question"]["global"],
        "limits": list(LIMITS),
    }
    encoded = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    workspace.guard_object(receipt)
    _write_new(out / "receipt.json", encoded.encode("ascii"), charge)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dormant Bridge Mind shadow pipeline over owned audit captures.")
    parser.add_argument("--capture", type=Path, required=True, help="A wd.bridge-mind-capture.v1 file.")
    parser.add_argument("--audit-root", type=Path, required=True, help="The owned .codex-audit directory.")
    parser.add_argument("--out-dir", type=Path, required=True, help="A new directory inside the audit root.")
    parser.add_argument("--context", type=Path, default=None, help="An optional wd.bridge-mind-pr-context.v1 file.")
    parser.add_argument("--previous", type=Path, default=None, help="The previous reflected.publication.json.")
    args = parser.parse_args(argv)
    try:
        receipt = run(args.capture, args.audit_root, args.out_dir, context_path=args.context,
                      previous_path=args.previous)
    except (ShadowRefused, workspace.WorkspaceError) as exc:
        sys.stderr.write(f"bridge mind shadow refused: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
