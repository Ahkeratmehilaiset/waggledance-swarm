"""S1 transport slice: freeze ONE explicitly supplied committed shadow cycle.

Library only; no live reader, bridge write, network, clock, CLI, gate or launch.
The producer latest-v2 interface is provisional until its tested Source freeze.
This function proves local transport seals, not core/JOIN semantic validity,
actor authenticity, complete coverage, utility or production acceptance. The
actual load_frozen/JOIN chain and independent scoring remain separate stages.
Manifest hashes seal the OBSERVED COPY. Only the explicitly listed raw files
and canonical snapshot digest are bound to the producer's committed pointer;
receipt and detail-manifest bytes are observations, never committed evidence.

Only new directories under explicit local .codex-audit roots are written. The
latest pointer and every source byte are rechecked before publication. This
detects observed prune/publish races, not an unbroken lease with the producer:
a changed/missing source refuses; no claim that freeze ran before prune.
Directory rename is no-replace on Windows; no power-loss, hostile same-user
TOCTOU, ACL/owner or hardlink isolation claim is made. Never delete a running
producer's lock. Failed staging cleanup is reported, never silently ignored.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat


MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_NODES = 200000
CYCLE = re.compile(r"[0-9]{8}T[0-9]{6}Z\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
FIXED_FILES = ("capture.json", "state.json", "detail-manifest.txt",
               "run/reflected.publication.json", "run/board.txt", "run/receipt.json")
GITHUB_FILES = ("context.json", "observed-status.json", "gh-pr-list.json", "gh-open-status.json")


class ChainRefusal(ValueError):
    """Fixed codes only; input text, host paths and secrets are never echoed."""

    def __init__(self, primary_reason, *, cleanup_failed=False):
        self.primary_reason = primary_reason
        self.cleanup_failed = cleanup_failed
        super().__init__(primary_reason + (";staging_cleanup_failed" if cleanup_failed else ""))


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if not key.isascii() or key in result:
            raise ChainRefusal("json_invalid")
        result[key] = value
    return result


def _json(raw):
    try:
        def no_constant(_):
            raise ValueError
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=no_constant)
        pending, count = [(value, 0)], 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 64 or count > MAX_NODES:
                raise ValueError
            if isinstance(item, dict): pending.extend((v, depth + 1) for v in item.values())
            elif isinstance(item, list): pending.extend((v, depth + 1) for v in item)
            elif isinstance(item, float) and not math.isfinite(item):
                raise ValueError
        return value
    except (UnicodeError, ValueError, RecursionError):
        raise ChainRefusal("json_invalid") from None


def _is_reparse(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _guard_path(path):
    # lstat every existing component: resolve() alone would hide junctions.
    try:
        for part in reversed((path, *path.parents)):
            if part.exists() or part.is_symlink():
                if _is_reparse(part):
                    raise ChainRefusal("reparse_path")
    except OSError:
        raise ChainRefusal("source_unavailable") from None


def _root(path, *, exists):
    value = Path(path)
    if not value.is_absolute() or value.anchor.startswith(("\\\\", "//")):
        raise ChainRefusal("audit_root")
    if ".codex-audit" not in value.parts or ".." in value.parts:
        raise ChainRefusal("audit_root")
    _guard_path(value)
    if exists and not value.is_dir():
        raise ChainRefusal("audit_root")
    return value


def _read(path):
    _guard_path(path)
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise ChainRefusal("file_type")
        with path.open("rb") as handle:
            raw = handle.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ChainRefusal("file_bound")
        return raw
    except OSError:
        raise ChainRefusal("source_unavailable") from None


def _privacy(raws):
    # Shared decoded guard, not a copied marker list or gate-module import.
    from tools import bridge_mind_workspace as workspace
    try:
        for raw in raws:
            if workspace._has_marker(raw.decode("utf-8")):
                raise ChainRefusal("privacy_guard")
    except (UnicodeError, RecursionError):
        raise ChainRefusal("privacy_guard") from None


def _cursor_state(raw, last_ok):
    state = _json(raw)
    if not isinstance(state, dict) or set(state) != {
            "schema", "cursor", "generation", "source", "window", "window_sha256"}:
        raise ChainRefusal("state_schema")
    if state["schema"] != "wd.bridge-mind-producer-state.v1":
        raise ChainRefusal("state_schema")
    if type(state["generation"]) is not int or state["generation"] < 1 \
            or type(last_ok.get("generation")) is not int or state["generation"] != last_ok["generation"]:
        raise ChainRefusal("state_generation")
    token = state["cursor"]
    if not isinstance(token, str) or len(token) > 2048 or not re.fullmatch(r"bc1\.[A-Za-z0-9_-]+", token):
        raise ChainRefusal("cursor")
    try:
        cursor = _json(base64.b64decode(token[4:] + "=" * (-len(token[4:]) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, ChainRefusal):
        raise ChainRefusal("cursor") from None
    if not isinstance(cursor, dict) or set(cursor) != {"file_identity", "generation", "offset"} \
            or type(cursor["offset"]) is not int or cursor["offset"] < 0 \
            or not isinstance(cursor["file_identity"], str) or not cursor["file_identity"] \
            or len(cursor["file_identity"]) > 1024 \
            or (cursor["generation"] is not None and not isinstance(cursor["generation"], str)):
        raise ChainRefusal("cursor")
    if state["source"] != {"file_identity": cursor["file_identity"], "log_generation": cursor["generation"]}:
        raise ChainRefusal("source_identity")
    if not isinstance(state["window"], list) or not isinstance(state["window_sha256"], str) \
            or not HEX.fullmatch(state["window_sha256"]):
        raise ChainRefusal("state_schema")
    return state


def _gather(live):
    pointer = _read(live / "latest.json")
    latest = _json(pointer)
    if not isinstance(latest, dict) or latest.get("schema") != "wd.bridge-mind-shadow-latest.v2":
        raise ChainRefusal("latest_schema")
    if latest.get("authority") != "none" or latest.get("scope") != "shadow_observation_not_gate":
        raise ChainRefusal("authority")
    ok = latest.get("last_ok")
    if not isinstance(ok, dict):
        raise ChainRefusal("latest_schema")
    cycle = ok.get("cycle_dir")
    if not isinstance(cycle, str) or not CYCLE.fullmatch(cycle):
        raise ChainRefusal("cycle_name")
    for key, name in (("capture_file", "capture.json"), ("state_file", "state.json"),
                      ("publication_file", "run/reflected.publication.json"),
                      ("board_file", "run/board.txt"), ("receipt_file", "run/receipt.json")):
        if ok.get(key) != f"cycles/{cycle}/{name}":
            raise ChainRefusal("cycle_path")
    names = list(FIXED_FILES)
    if ok.get("context_file") is None:
        if ok.get("context_sha256") is not None:
            raise ChainRefusal("context_binding")
    else:
        if ok["context_file"] != f"cycles/{cycle}/context.json" or not isinstance(ok.get("context_sha256"), str):
            raise ChainRefusal("context_binding")
        names.append("context.json")
    folder = live / "cycles" / cycle
    files = {name: _read(folder / name) for name in names}
    for name, key, reason in (("capture.json", "capture_sha256", "capture_digest"),
                              ("state.json", "state_sha256", "state_digest"),
                              ("run/board.txt", "board_text_sha256", "board_digest"),
                              ("context.json", "context_sha256", "context_digest")):
        if name in files and _sha(files[name]) != ok.get(key):
            raise ChainRefusal(reason)
    parsed = {name: _json(raw) for name, raw in files.items() if name.endswith(".json")}
    capture = parsed["capture.json"]
    if not isinstance(capture, dict) or not isinstance(capture.get("events"), list) \
            or not all(isinstance(item, dict) for item in capture["events"]):
        raise ChainRefusal("capture_shape")
    publication = parsed["run/reflected.publication.json"]
    if not isinstance(publication, dict) or set(publication) != {"schema", "snapshot", "snapshot_sha256"} \
            or publication["schema"] != "wd.bridge-mind-publication.v1" \
            or not isinstance(publication["snapshot"], dict) \
            or publication["snapshot_sha256"] != ok.get("snapshot_sha256") \
            or _sha(_canonical(publication["snapshot"])) != ok.get("snapshot_sha256"):
        raise ChainRefusal("snapshot_digest")
    state = _cursor_state(files["state.json"], ok)
    _privacy([pointer, *files.values(), *(_canonical(value) for value in parsed.values())])
    return pointer, latest, files, state


def freeze_committed_cycle(live_root, frozen_root):
    """Transport-only immutable copy, rechecked against the committed pointer.

    Each JSON object uses ASCII keys and strict JSON. Missing GitHub raw files
    remain explicit nulls; context alone never becomes complete GitHub evidence.
    Nothing imports the core gate/classifier or runs a producer or JOIN here.
    """
    live = _root(live_root, exists=True)
    target = _root(frozen_root, exists=False)
    if live == target or live in target.parents or target in live.parents:
        raise ChainRefusal("root_overlap")
    if target.exists() or target.is_symlink():
        raise ChainRefusal("output_exists")
    if not target.parent.is_dir() or live.stat().st_dev != target.parent.stat().st_dev:
        raise ChainRefusal("local_volume")
    pointer, latest, files, state = _gather(live)
    cycle = latest["last_ok"]["cycle_dir"]
    manifest_files = {name.replace("/", "\\"): _sha(raw) for name, raw in files.items()}
    for name in GITHUB_FILES:
        manifest_files.setdefault(name, None)
    manifest = {"schema": "wd.mind-frozen-cycles.v1", "authority": "none",
                "file_seals_scope": "observed_copy_hashes_not_uniform_pointer_bindings",
                "cycles": [{"cycle": cycle, "files": manifest_files}]}
    bound_names = ("capture.json", "state.json", "run/board.txt", "context.json")
    binding = {"schema": "wd.mind-chain-transport.v1", "authority": "none",
               "scope": "transport_seals_not_join_validation", "join_validated": False,
               "latest_sha256": _sha(pointer), "cycle": cycle,
               "producer_generation": state["generation"], "source": state["source"],
               "cursor": state["cursor"], "manifest_sha256": _sha(_canonical(manifest)),
               "pointer_bound_raw_files": {name: _sha(files[name]) for name in bound_names if name in files},
               "pointer_bound_snapshot_sha256": latest["last_ok"]["snapshot_sha256"],
               "publication_binding": "canonical_snapshot_only_not_envelope_raw_bytes",
               "not_pointer_bound": ["detail-manifest.txt", "run/receipt.json"],
               "coverage": "UNKNOWN", "github_complete": False}
    output = {f"{cycle}/{name}": raw for name, raw in files.items()}
    output.update({"manifest.json": _canonical(manifest), "source-binding.json": _canonical(binding)})
    _privacy(output.values())  # Before creating even the staging directory.
    staging = target.parent / f".{target.name}.staging-{secrets.token_hex(12)}"
    written, directories = [], []
    try:
        staging.mkdir()
        directories.append(staging)
        for name, raw in output.items():
            path = staging / name
            for parent in reversed(path.parents):
                if parent != staging and staging in parent.parents and not parent.exists():
                    parent.mkdir()
                    directories.append(parent)
            with path.open("xb") as handle:
                written.append(path)
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            if _read(path) != raw:
                raise ChainRefusal("output_changed")
        # Recheck every observed source byte, not just the capture digest.
        if _read(live / "latest.json") != pointer or any(
                _read(live / "cycles" / cycle / name) != raw for name, raw in files.items()):
            raise ChainRefusal("source_changed")
        expected = set(output)
        observed = {path.relative_to(staging).as_posix() for path in staging.rglob("*") if path.is_file()}
        if observed != expected:
            raise ChainRefusal("foreign_output")
        _guard_path(target)
        if target.exists():
            raise ChainRefusal("output_exists")
        os.rename(staging, target)
    except BaseException as error:
        cleanup_failed = False
        for path in reversed(written):
            try: path.unlink()
            except OSError: cleanup_failed = True
        for path in reversed(directories):
            try: path.rmdir()
            except OSError: cleanup_failed = True
        if cleanup_failed:
            if isinstance(error, ChainRefusal):
                raise ChainRefusal(error.primary_reason, cleanup_failed=True) from None
            if not isinstance(error, Exception):
                error.add_note("staging_cleanup_failed")
                raise
            reason = "output_unavailable" if isinstance(error, OSError) else "output_failed"
            raise ChainRefusal(reason, cleanup_failed=True) from None
        if isinstance(error, ChainRefusal):
            raise
        if isinstance(error, OSError):
            raise ChainRefusal("output_unavailable") from None
        raise
    return binding
