"""Re-derive Git content identity for Rule 9a review carry-forward.

This evidence proves content identity only. It grants no approval, CI result,
signature, merge, or runtime authority. Callers must re-evaluate the original
bridge identities, scope and vetoes and require CI at the proposed head.

Known resource limit: _MAX_BYTES bounds accepted evidence after capture; PIPE
capture does not bound peak stdout memory. The command timeout remains separate.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Mapping

from tools import bridge_rco_enrollment as _trusted_paths

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_BYTES = 32 * 1024 * 1024
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_DIFF_FLAGS = (
    "--no-color", "--no-ext-diff", "--no-textconv", "--binary", "--full-index",
    "--no-renames", "--no-relative", "--src-prefix=a/", "--dst-prefix=b/",
    "--ignore-submodules=none", "--diff-algorithm=myers", "--no-indent-heuristic",
    "--unified=3", "--inter-hunk-context=0", "--word-diff=none", "--submodule=short",
    "--output-indicator-new=+", "--output-indicator-old=-", "--output-indicator-context= ",
    "-O" + os.devnull,
)
_CONFIG = (
    "core.autocrlf=false", "core.fsmonitor=false", "core.quotePath=true",
    "core.attributesFile=" + os.devnull, "diff.noprefix=false",
    "diff.mnemonicPrefix=false", "color.ui=false", "diff.external=",
    "diff.submodule=short", "diff.suppressBlankEmpty=false", "diff.orderFile=" + os.devnull,
)


def _file_bytes(path: Path, limit: int) -> tuple[bytes, tuple]:
    """Use the existing no-reparse validator and compare handle/path identities."""
    _trusted_paths._no_reparse(path)
    identity = lambda s: (s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode), s.st_size,
                          s.st_mtime_ns, getattr(s, "st_file_attributes", 0),
                          getattr(s, "st_reparse_tag", 0))
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
        raise ValueError("identity authority file is nonregular, empty or oversized")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after_read = os.fstat(stream.fileno())
    after = path.lstat()
    if (len(raw) != before.st_size or len(raw) > limit
            or any(identity(s) != identity(before) for s in (opened, after_read, after))):
        raise ValueError("identity authority file changed during read")
    _trusted_paths._no_reparse(path)
    return raw, identity(before)


def _native_git_snapshot(path: Path) -> dict[str, Any]:
    raw, identity = _file_bytes(path, 64 * 1024 * 1024)
    return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(), identity=identity)


def _fleet_git_authority() -> tuple[Path, dict, dict]:
    """Existing external manifest -> pinned fleet -> configured physical path.

    The executable digest is measured consistency, not an external digest pin;
    this does not authenticate or hash-pin Git's DLL/helper chain.
    """
    try:
        root_value = os.environ.get("WD_BRIDGE_BUNDLE_ROOT")
        pin = os.environ.get("WD_REBOOT_EXPECTED_MANIFEST_HASH")
        if (type(root_value) is not str or not root_value
                or type(pin) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", pin) is None):
            raise ValueError("Git identity needs existing external manifest authority")
        root = Path(root_value)
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("Git bundle authority root must be absolute")
        raw = _trusted_paths._manifest_bytes(root)
        if hashlib.sha256(raw).hexdigest() != pin.lower():
            raise ValueError("Git authority manifest hash mismatch")
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=_trusted_paths._pairs,
                              parse_constant=_trusted_paths._constant)
        if (type(manifest) is not dict or type(manifest.get("source_commit")) is not str
                or _SHA.fullmatch(manifest["source_commit"]) is None
                or root.name.lower() != manifest["source_commit"]):
            raise ValueError("Git authority manifest generation mismatch")
        pins = manifest.get("files")
        fleet_pin = pins.get("wd-fleet.json") if type(pins) is dict else None
        if type(fleet_pin) is not str or re.fullmatch(r"[0-9a-fA-F]{64}", fleet_pin) is None:
            raise ValueError("Git authority fleet pin missing")
        fleet_raw, _ = _file_bytes(root / "wd-fleet.json", 1024 * 1024)
        if hashlib.sha256(fleet_raw).hexdigest() != fleet_pin.lower():
            raise ValueError("Git authority fleet hash mismatch")
        fleet = json.loads(fleet_raw.decode("utf-8"), object_pairs_hook=_trusted_paths._pairs,
                           parse_constant=_trusted_paths._constant)
        if (type(fleet) is not dict or type(fleet.get("schema_version")) is not int
                or fleet["schema_version"] != 2 or type(fleet.get("git_executable")) is not str):
            raise ValueError("Git authority fleet is invalid")
        path = Path(fleet["git_executable"])
        if (not path.is_absolute() or ".." in path.parts
                or (os.name == "nt" and path.suffix.lower() != ".exe")):
            raise ValueError("Git executable authority path must be absolute")
        snapshot = _native_git_snapshot(path)
        authority = dict(manifest_sha256=pin.lower(), fleet_sha256=fleet_pin.lower(),
                         generation=manifest["source_commit"])
        return path, snapshot, authority
    except (OSError, UnicodeError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Git executable authority unavailable") from exc


def _git_reader(repo_root: Path):
    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise ValueError("content identity requires an explicit repository")
    root = repo_root.resolve(strict=True)
    executable, native_snapshot, authority = _fleet_git_authority()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               LC_ALL="C", LANG="C")
    command = [str(executable), "--no-replace-objects", "--attr-source=" + _EMPTY_TREE]
    for setting in _CONFIG:
        command += ["-c", setting]

    def read(*args: str, data: bytes | None = None) -> bytes:
        try:
            if _native_git_snapshot(executable) != native_snapshot:
                raise ValueError("Git executable identity changed before command")
            result = subprocess.run(command + list(args), cwd=root, env=env, input=data,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("content identity Git read unavailable") from exc
        if _native_git_snapshot(executable) != native_snapshot:
            raise ValueError("Git executable identity changed during command")
        if result.returncode or len(result.stdout) > _MAX_BYTES:
            raise ValueError("content identity Git read refused")
        return result.stdout

    try:
        actual = Path(os.fsdecode(read("rev-parse", "--show-toplevel").strip())).resolve(strict=True)
        if actual != root or read("rev-parse", "--is-shallow-repository").strip() != b"false":
            raise ValueError("content identity repository is incomplete")
        graft_path = Path(os.fsdecode(read("rev-parse", "--git-path", "info/grafts").strip()))
        if not graft_path.is_absolute():
            graft_path = root / graft_path
        if graft_path.exists() and graft_path.stat().st_size:
            raise ValueError("content identity grafted history refused")
    except (OSError, UnicodeError) as exc:
        raise ValueError("content identity repository unavailable") from exc
    read.authority = (executable, native_snapshot, authority)
    return read


def _merge_base(read, base: str, head: str) -> str:
    candidates = read("merge-base", "--all", base, head).splitlines()
    if len(candidates) != 1:
        raise ValueError("content identity requires one unambiguous merge-base")
    result = candidates[0].decode("ascii", "strict")
    if _SHA.fullmatch(result) is None:
        raise ValueError("invalid merge-base identity")
    return result


def _entries(raw: bytes) -> list[dict[str, str]]:
    parts = raw.split(b"\0")
    if not parts or parts.pop() != b"" or len(parts) % 2:
        raise ValueError("invalid raw Git path identity")
    entries = []
    for i in range(0, len(parts), 2):
        fields = parts[i].split()
        if len(fields) != 5 or not fields[0].startswith(b":") or not parts[i + 1]:
            raise ValueError("invalid raw Git path identity")
        old_mode, new_mode, old_blob, new_blob, status = [f.decode("ascii", "strict") for f in fields]
        old_mode = old_mode[1:]
        if (not re.fullmatch(r"[0-7]{6}", old_mode) or not re.fullmatch(r"[0-7]{6}", new_mode)
                or _SHA.fullmatch(old_blob) is None or _SHA.fullmatch(new_blob) is None
                or status not in {"A", "M", "D", "T"}):
            raise ValueError("invalid raw Git path identity")
        entries.append(dict(path_hex=parts[i + 1].hex(), old_mode=old_mode, new_mode=new_mode,
                            old_blob=old_blob, new_blob=new_blob, status=status))
    return entries


def derive_content_identity(*, repo_root: Path, old_base: str, old_head: str,
                            new_base: str, new_head: str) -> dict[str, Any]:
    """Read immutable Git objects; return identity evidence or refuse.

    Both canonical raw diff bytes AND stable patch-id AND changed path/mode/blob
    tuples must match. A patch-id alone intentionally ignores whitespace and
    cannot authorize carry. Missing objects, empty diffs and ambiguous histories
    never receive an identity proof. Git configuration cannot change diff flags.
    """
    values = dict(old_base=old_base, old_head=old_head, new_base=new_base, new_head=new_head)
    if any(type(value) is not str or _SHA.fullmatch(value) is None for value in values.values()):
        raise ValueError("content identity requires exact 40-hex Git objects")
    read = _git_reader(repo_root)
    for value in values.values():
        # Existence/type of the object, not a ref name or a caller assertion.
        if read("cat-file", "-t", value).strip() != b"commit":
            raise ValueError("content identity requires commit objects")
    old_mb = _merge_base(read, old_base, old_head)
    new_mb = _merge_base(read, new_base, new_head)
    old_diff = read("diff", *_DIFF_FLAGS, old_mb, old_head, "--")
    new_diff = read("diff", *_DIFF_FLAGS, new_mb, new_head, "--")
    if not old_diff or not new_diff:
        raise ValueError("empty diff has no carry identity")
    old_raw = read("diff-tree", "-r", "--raw", "--no-abbrev", "-z", "--no-renames", old_mb, old_head, "--")
    new_raw = read("diff-tree", "-r", "--raw", "--no-abbrev", "-z", "--no-renames", new_mb, new_head, "--")
    old_entries, new_entries = _entries(old_raw), _entries(new_raw)
    if old_diff != new_diff or not old_entries or old_entries != new_entries:
        raise ValueError("diff bytes or path/mode/blob content identity differs")

    def patch_id(raw):
        lines = read("patch-id", "--stable", data=raw).splitlines()
        if len(lines) != 1 or re.fullmatch(rb"[0-9a-f]{40} [0-9a-f]{40}", lines[0]) is None:
            raise ValueError("empty or ambiguous stable patch identity")
        return lines[0].split()[0].decode("ascii")

    old_patch, new_patch = patch_id(old_diff), patch_id(new_diff)
    if old_patch != new_patch:
        raise ValueError("stable patch content identity differs")
    executable, native, authority = read.authority
    if _fleet_git_authority() != read.authority:
        raise ValueError("Git executable or authority changed during proof")
    return dict(schema="wd.bridge-content-identity.v1", **values,
                git_executable_path=str(executable), git_executable_sha256=native["sha256"],
                git_executable_trust="anchored_fleet_path_measured_sha256", git_authority=authority,
                old_merge_base=old_mb, new_merge_base=new_mb,
                diff_flags=list(_DIFF_FLAGS), diff_bytes_b64=base64.b64encode(old_diff).decode("ascii"),
                old_diff_sha256=hashlib.sha256(old_diff).hexdigest(),
                new_diff_sha256=hashlib.sha256(new_diff).hexdigest(),
                old_patch_id=old_patch, new_patch_id=new_patch,
                old_entries=old_entries, new_entries=new_entries)


def verify_content_identity(*, proof: Mapping[str, Any], repo_root: Path,
                            old_base: str, old_head: str, new_base: str, new_head: str) -> dict[str, Any]:
    """Recompute every field from Git, including bytes; never trust stored flags."""
    current = derive_content_identity(repo_root=repo_root, old_base=old_base, old_head=old_head,
                                      new_base=new_base, new_head=new_head)
    if not isinstance(proof, Mapping) or dict(proof) != current:
        raise ValueError("content identity evidence failed re-derivation")
    return current
