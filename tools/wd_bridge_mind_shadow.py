# SPDX-License-Identifier: BUSL-1.1
"""Dormant Bridge Mind shadow pipeline: one owned audit capture in, the whole Mind chain out; nothing is wired.

Inputs are files a caller already wrote under one ``.codex-audit`` root:

* ``--capture``: one ``wd.bridge-mind-capture.v1`` object holding the events one inherited pinned reader run
  returned (``reader``, ``reader_stdout_sha256`` of that run's exact output, ``observed_utc``, ``events``);
* ``--context`` (optional): one ``wd.bridge-mind-pr-context.v1`` PR context (see ``tools/bridge_mind_workspace.py``),
  observational data only;
* ``--identity-context`` (optional, the G-K opt-in): one ``wd.bridge-mind.identity-context.v1`` object, or a file
  that is exactly JSON ``null`` (an explicit UNKNOWN context). Only a given file reaches the workspace's
  keyword-only ``identity_context``; an omitted one is never passed as ``None``, so a run without it is the
  legacy run, byte for byte. It is caller tuple consistency, never origin authentication or gate eligibility,
  and the receipt names its path and digest (``identity_context``);
* ``--previous`` (optional): the previous run's ``reflected.publication.json``.

This module never reads the bridge log, never calls the bridge, GitHub, a provider or a gate, never starts a
process and never writes a bridge event. One run is a pure function of its inputs (time is the capture's
``observed_utc``, never the wall clock):

1. Envelopes ``{ref, event}``: each ref is the canonical event id of the captured row. It is capture-derived: a
   row the reader truncated has a ref that is not the log's own, so the capture digest travels with every output.
2. ``tools/wd_bridge_mind.py`` ``full_rebuild`` over the capture window, declared as its own complete log
   (``log_identity`` names the capture digest). Nothing before the window is seen; it is never whole-log coverage.
3. ``tools/bridge_mind_workspace.py`` ``reflect`` with the previous reflected snapshot and the context, and the
   identity context only when a file is given.
4. The reflected snapshot is published with the core's ``write_snapshot`` and read back with its
   ``read_snapshot``, so the envelope digest, the audit root and the path guards are verified. The previous
   publication is read the same way. An envelope is never stripped by hand.
5. Delta lines against the previous snapshot (``tools/wd_bridge_mind_delta_push.py``) and the mind text view of
   ``tools/wd_pr_missing_slot_board.py``, both from the verified snapshot.

``--out-dir`` must be a new directory inside the audit root; every file is created exclusively there. The receipt
(``receipt.json``, also printed) names every input and output digest and the run's limits. The core and the board
are imported only for a run: when either is missing the run refuses with a fixed message, never a fallback.
Every refusal is a fixed message that never echoes input content.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import bridge_mind_workspace as workspace  # noqa: E402
from tools import wd_bridge_mind_delta_push as delta_push  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

CAPTURE_SCHEMA = "wd.bridge-mind-capture.v1"
CAPTURE_KEYS = frozenset({"schema", "reader", "reader_stdout_sha256", "observed_utc", "events"})
# The inherited pinned readers a capture may come from; the reader string is a label, not authentication.
READERS = frozenset({"Read-AgentBridge.ps1 -Raw", "Read-AgentBridge.ps1 -Compact -EventId"})
RECEIPT_SCHEMA = "wd.bridge-mind-shadow-receipt.v1"
INPUT_LIMIT = 64 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
LIMITS = (
    "capture window only: nothing before the first captured event is seen, never whole-log coverage",
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


def _read_object(path: Path, *, null: bool = False) -> tuple[dict[str, Any] | None, str]:
    """One canonical JSON object (the bridge reader: no duplicate key, no NaN) and the sha256 of its bytes.

    With ``null``, a file that is exactly JSON ``null`` (JSON whitespace around it allowed) reads as ``None``: the
    identity context's explicit UNKNOWN, never the same as an omitted file. Any other value is still refused.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read(INPUT_LIMIT + 1)
    except OSError:
        raise ShadowRefused("an input file cannot be read") from None
    if len(data) > INPUT_LIMIT:
        raise ShadowRefused("an input file is larger than the shadow run reads")
    try:
        text = data.decode("utf-8")
        value = None if null and text.strip(" \t\n\r") == "null" else parse_bridge_json_object(text)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ShadowRefused("an input file is not one canonical JSON object"
                            + (" or JSON null" if null else "")) from None
    return value, hashlib.sha256(data).hexdigest()


def read_capture(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != CAPTURE_KEYS or value["schema"] != CAPTURE_SCHEMA:
        raise ShadowRefused("the capture is not a wd.bridge-mind-capture.v1 object")
    if value["reader"] not in READERS or not isinstance(value["reader_stdout_sha256"], str) \
            or not _SHA.fullmatch(value["reader_stdout_sha256"]) or workspace.parse_time(value["observed_utc"]) is None:
        raise ShadowRefused("the capture does not name its pinned reader, output digest and observation time")
    if not isinstance(value["events"], list) or not all(isinstance(event, dict) for event in value["events"]):
        raise ShadowRefused("the capture events are not a list of objects")
    return value


def _write_new(path: Path, data: bytes) -> str:
    with open(path, "xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def run(capture_path: Path, audit_root: Path, out_dir: Path, *, context_path: Path | None = None,
        previous_path: Path | None = None, identity_context_path: Path | None = None) -> dict[str, Any]:
    core = _module("tools.wd_bridge_mind")
    board = _module("tools.wd_pr_missing_slot_board")
    from tools.bridge_compact_view import event_id

    capture_file = _owned(capture_path, audit_root)
    capture, capture_sha = _read_object(capture_file)
    capture = read_capture(capture)
    context, context_sha = (None, None)
    if context_path is not None:
        context, context_sha = _read_object(_owned(context_path, audit_root))
    # G-K opt-in: the identity keyword reaches the workspace only when a file is given (a JSON null file is the
    # explicit UNKNOWN context); an omitted file is never passed as None, so the legacy call is unchanged.
    identity_options: dict[str, Any] = {}
    identity_file, identity_sha = None, None
    if identity_context_path is not None:
        identity_file = _owned(identity_context_path, audit_root)
        identity, identity_sha = _read_object(identity_file, null=True)
        identity_options = {"identity_context": identity}
    out = _owned(out_dir, audit_root, new=True)
    events = capture["events"]
    as_of = capture["observed_utc"]
    source = {"log_identity": "capture-window:" + capture_sha, "generation": None, "cursor_start": 0,
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
        reflected = workspace.reflect(snapshot, previous, context, **identity_options)
    except workspace.WorkspaceError:
        raise ShadowRefused("the workspace refused the snapshot") from None
    out.mkdir()
    publication = out / "reflected.publication.json"
    try:
        digest = core.write_snapshot(reflected, publication, audit_root=audit_root)
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
        _write_new(out / "delta.jsonl", delta_push.render_lines(changes).encode("utf-8"))
    try:
        text = board.render_mind_text(board.build_mind_view(verified, digest))
    except board.MindRefused:
        raise ShadowRefused("the board refused the verified snapshot") from None
    board_sha = _write_new(out / "board.txt", text.encode("utf-8"))
    receipt = {
        "schema": RECEIPT_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
        "capture": {"path": str(capture_file), "sha256": capture_sha, "reader": capture["reader"],
                    "reader_stdout_sha256": capture["reader_stdout_sha256"], "observed_utc": as_of,
                    "events": len(events)},
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
    if identity_file is not None:  # an omitted identity file adds no key: the legacy receipt bytes are unchanged
        receipt["identity_context"] = {"path": str(identity_file), "sha256": identity_sha}
    encoded = json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    workspace.guard_object(receipt)
    _write_new(out / "receipt.json", encoded.encode("ascii"))
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dormant Bridge Mind shadow pipeline over owned audit captures.")
    parser.add_argument("--capture", type=Path, required=True, help="A wd.bridge-mind-capture.v1 file.")
    parser.add_argument("--audit-root", type=Path, required=True, help="The owned .codex-audit directory.")
    parser.add_argument("--out-dir", type=Path, required=True, help="A new directory inside the audit root.")
    parser.add_argument("--context", type=Path, default=None, help="An optional wd.bridge-mind-pr-context.v1 file.")
    parser.add_argument("--identity-context", type=Path, default=None,
                        help="An optional wd.bridge-mind.identity-context.v1 file, or JSON null for an explicit "
                             "UNKNOWN context (G-K opt-in, never authentication).")
    parser.add_argument("--previous", type=Path, default=None, help="The previous reflected.publication.json.")
    args = parser.parse_args(argv)
    try:
        receipt = run(args.capture, args.audit_root, args.out_dir, context_path=args.context,
                      previous_path=args.previous, identity_context_path=args.identity_context)
    except (ShadowRefused, workspace.WorkspaceError) as exc:
        sys.stderr.write(f"bridge mind shadow refused: {exc}\n")
        return 2
    sys.stdout.write(json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
