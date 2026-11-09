"""Re-derive Git content identity for Rule 9a review carry-forward.

This evidence proves content identity only. It grants no approval, CI result,
signature, merge, or runtime authority. Callers must re-evaluate the original
bridge identities, scope and vetoes and require CI at the proposed head.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Mapping

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_MAX_BYTES = 32 * 1024 * 1024
_DIFF_FLAGS = (
    "--no-color", "--no-ext-diff", "--no-textconv", "--binary", "--full-index",
    "--no-renames", "--no-relative", "--src-prefix=a/", "--dst-prefix=b/",
    "--ignore-submodules=none", "--diff-algorithm=myers", "--no-indent-heuristic",
    "--unified=3",
)
_CONFIG = (
    "core.autocrlf=false", "core.fsmonitor=false", "core.quotePath=true",
    "core.attributesFile=" + os.devnull, "diff.noprefix=false",
    "diff.mnemonicPrefix=false", "color.ui=false", "diff.external=",
    "diff.submodule=short", "diff.suppressBlankEmpty=false",
)


def _git_reader(repo_root: Path):
    if not isinstance(repo_root, Path) or not repo_root.is_dir():
        raise ValueError("content identity requires an explicit repository")
    root = repo_root.resolve(strict=True)
    executable = shutil.which("git")
    if not executable:
        raise ValueError("content identity Git executable unavailable")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_ATTR_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1",
               LC_ALL="C", LANG="C")
    command = [executable, "--no-replace-objects"]
    for setting in _CONFIG:
        command += ["-c", setting]

    def read(*args: str, data: bytes | None = None) -> bytes:
        try:
            result = subprocess.run(command + list(args), cwd=root, env=env, input=data,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=30, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("content identity Git read unavailable") from exc
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
    return dict(schema="wd.bridge-content-identity.v1", **values,
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
