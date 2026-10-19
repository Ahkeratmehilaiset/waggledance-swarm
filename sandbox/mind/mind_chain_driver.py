"""S1 transport slice: freeze ONE explicitly supplied committed shadow cycle.

Library only; no live reader, bridge write, network, clock, CLI, gate or launch.
freeze_committed_cycle proves local transport seals, not core/JOIN validity,
actor authenticity, complete coverage, utility or production acceptance. The
separate join_frozen_cycle calls the actual frozen loader and pure JOIN APIs,
with no receipts or memory. Independent scoring remains a separate stage.
Manifest hashes seal the OBSERVED COPY. Only the explicitly listed raw files
and canonical snapshot digest are bound to the producer's committed pointer;
receipt and detail-manifest bytes are observations, never committed evidence.
The consumer manifest lists JSON inputs only; auxiliary transport files keep
separate observation seals in source-binding.json, never JSON-input entries.

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
# The committed pointer names each GitHub file by a (file, sha256) key pair, both null or both set.
GITHUB_POINTER = {"context.json": ("context_file", "context_sha256"),
                  "observed-status.json": ("status_file", "status_sha256"),
                  "gh-pr-list.json": ("pr_list_file", "pr_list_sha256"),
                  "gh-open-status.json": ("open_status_file", "open_status_sha256")}
FROZEN_JSON_FILES = ("capture.json", "run/reflected.publication.json", *GITHUB_FILES)
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


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
    for name, (file_key, sha_key) in GITHUB_POINTER.items():
        reason = "context_binding" if name == "context.json" else "github_binding"
        if ok.get(file_key) is None:
            if ok.get(sha_key) is not None:
                raise ChainRefusal(reason)
        else:
            if ok[file_key] != f"cycles/{cycle}/{name}" or not isinstance(ok.get(sha_key), str):
                raise ChainRefusal(reason)
            names.append(name)
    folder = live / "cycles" / cycle
    files = {name: _read(folder / name) for name in names}
    for name, key, reason in (("capture.json", "capture_sha256", "capture_digest"),
                              ("state.json", "state_sha256", "state_digest"),
                              ("run/board.txt", "board_text_sha256", "board_digest"),
                              *((name, sha_key, "context_digest" if name == "context.json" else "github_digest")
                                for name, (_, sha_key) in GITHUB_POINTER.items())):
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

    Each JSON object uses ASCII keys and strict JSON. Each GitHub file the
    pointer binds is copied byte-exact (the lane view links the raw gh list
    digests) and listed for the loader; an unbound one stays an explicit null,
    so a partial capture loads as no GitHub. github_complete stays false: the
    transport never certifies GitHub completeness or currency; the view does.
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
    manifest_files = {name.replace("/", "\\"): _sha(raw) for name, raw in files.items()
                      if name in FROZEN_JSON_FILES}
    for name in GITHUB_FILES:
        manifest_files.setdefault(name, None)
    manifest = {"schema": "wd.mind-frozen-cycles.v1", "cycles": [{"cycle": cycle, "files": manifest_files}]}
    bound_names = ("capture.json", "state.json", "run/board.txt", *GITHUB_FILES)
    binding = {"schema": "wd.mind-chain-transport.v1", "authority": "none",
               "scope": "transport_seals_not_join_validation", "join_validated": False,
               "latest_sha256": _sha(pointer), "cycle": cycle,
               "producer_generation": state["generation"], "source": state["source"],
               "cursor": state["cursor"], "manifest_sha256": _sha(_canonical(manifest)),
               "file_seals_scope": "observed_copy_hashes_not_uniform_pointer_bindings",
               "observed_file_sha256": {name: _sha(raw) for name, raw in files.items()},
               "state_window_semantics": "NOT_VERIFIED_NOT_A_JOIN_INPUT",
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


def _join_module():
    # Lazy normal filesystem import from this Source tree, never Git/blob exec,
    # an installed-pointer substitution, a producer import or a product CLI.
    from sandbox.mind import join_turn
    return join_turn


def join_frozen_cycle(frozen_root, *, expected_binding_sha256, lanes=LANES):
    """Pure JOIN over one caller-pinned transport freeze, returning cards only.

    The supplied digest is correlation, not authentication or task authority.
    The actual loader validates the core publication and capture event bodies.
    A usable GitHub view needs all four pointer-bound GitHub files frozen.
    State/window/receipt/detail are observed transport bytes, never JOIN inputs.
    No F: default, write_turn, bridge write, clock, producer or memory call.
    """
    root = _root(frozen_root, exists=True)
    if not isinstance(expected_binding_sha256, str) or not HEX.fullmatch(expected_binding_sha256):
        raise ChainRefusal("binding_digest")
    binding_raw = _read(root / "source-binding.json")
    if _sha(binding_raw) != expected_binding_sha256:
        raise ChainRefusal("binding_digest")
    binding = _json(binding_raw)
    if not isinstance(binding, dict) or binding.get("schema") != "wd.mind-chain-transport.v1" \
            or binding.get("authority") != "none" or binding.get("join_validated") is not False:
        raise ChainRefusal("binding_schema")
    cycle = binding.get("cycle")
    if not isinstance(cycle, str) or not CYCLE.fullmatch(cycle):
        raise ChainRefusal("cycle_name")
    if not isinstance(lanes, (list, tuple)) or not 1 <= len(lanes) <= len(LANES) \
            or any(not isinstance(lane, str) or lane not in LANES for lane in lanes) \
            or len(set(lanes)) != len(lanes):
        raise ChainRefusal("lanes")
    observed = binding.get("observed_file_sha256")
    if not isinstance(observed, dict) or not set(FIXED_FILES) <= set(observed) \
            or not set(observed) - set(FIXED_FILES) <= set(GITHUB_FILES):
        raise ChainRefusal("observation_seals")
    github_frozen = set(GITHUB_FILES) <= set(observed)  # only then may the view use GitHub
    before = {name: _read(root / cycle / name) for name in observed}
    if any(_sha(raw) != observed[name] for name, raw in before.items()):
        raise ChainRefusal("frozen_changed")
    manifest_raw = _read(root / "manifest.json")
    if _sha(manifest_raw) != binding.get("manifest_sha256"):
        raise ChainRefusal("manifest_digest")
    expected_files = {name.replace("/", "\\"): _sha(raw) for name, raw in before.items()
                      if name in FROZEN_JSON_FILES}
    for name in GITHUB_FILES:
        expected_files.setdefault(name, None)
    if _json(manifest_raw) != {"schema": "wd.mind-frozen-cycles.v1",
                               "cycles": [{"cycle": cycle, "files": expected_files}]}:
        raise ChainRefusal("consumer_manifest")
    _privacy([binding_raw, manifest_raw, *before.values()])
    try:
        joiner = _join_module()
        loaded = joiner.fz.load_frozen(root)  # Explicit root; never a legacy data default.
        if loaded.get("manifest_sha256") != binding["manifest_sha256"]:
            raise ChainRefusal("manifest_digest")
        if len(loaded["cycles"]) != 1 or loaded["cycles"][0]["cycle"] != cycle:
            raise ChainRefusal("loaded_cycle")
        cards = []
        for lane in lanes:
            turn = joiner.turn_case(lane, loaded["cycles"][0], learn_ref="synthetic:not-executed",
                                    envelope=None, receipts=None)
            raw = turn["card"].encode("utf-8")
            view = turn["view"]
            usable = view["github"].get("usable")
            if len(raw) > 2048 or view["authority"] != "none" or view["coverage"] != "UNKNOWN" \
                    or type(usable) is not bool or (usable and not github_frozen):
                raise ChainRefusal("join_invariant")
            _privacy([raw])
            cards.append({"lane": lane, "text": turn["card"], "bytes": len(raw), "sha256": _sha(raw),
                          "coverage": view["coverage"], "github_usable": usable, "as_of_utc": turn["as_of_utc"]})
    except ChainRefusal:
        raise
    except Exception:
        raise ChainRefusal("join_refused") from None
    if _read(root / "source-binding.json") != binding_raw or _read(root / "manifest.json") != manifest_raw \
            or any(_read(root / cycle / name) != raw for name, raw in before.items()):
        raise ChainRefusal("frozen_changed")
    return {"schema": "wd.mind-chain-result.v1", "authority": "none", "scope": "frozen_observation_not_gate",
            "cycle": cycle, "transport_binding_sha256": expected_binding_sha256,
            "state_window_semantics": "NOT_VERIFIED_NOT_A_JOIN_INPUT", "join_calls_completed": len(cards),
            "cards": cards}


def run_committed_chain(live_root, frozen_root, *, lanes=LANES):
    """Freeze a finished cycle, then pure JOIN. A JOIN refusal keeps the freeze.

    A retained source-binding.json still says join_validated=false; no partial
    or failed JOIN result is published. No running producer is started/stopped.
    """
    binding = freeze_committed_cycle(live_root, frozen_root)
    return join_frozen_cycle(frozen_root, expected_binding_sha256=_sha(_canonical(binding)), lanes=lanes)
