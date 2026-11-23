"""Dormant Mind rollout admission and tracked-file closure, without activation.

The caller supplies the installed root/external manifest anchor and reviewed main
commit/tree/membership. This module does not discover those authorities, approve
a merge/deploy, or inspect semantic dependencies. A future transaction driver must
call the installed copy and retain its own operator-reviewed activation contract.
There is no CLI, installation, pointer write, startup or restore operation here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from types import MappingProxyType
from typing import Mapping, Sequence


class Refused(ValueError):
    """Admission could not be established; no fallback is authorized."""


POLICY_PATH = "tools-bootstrap/tools/wd_mind_rollout_policy.py"
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILES = 4096
_SHA1 = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z", re.ASCII)
_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?\Z", re.I | re.ASCII)
_FORBIDDEN = re.compile(r'[<>:"\\|?*\x00-\x1f\x7f]')
_EXCLUDED_COMPONENTS = frozenset({".agent-bridge", "control_plane"})


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise Refused("Expected an externally bound SHA256")
    return value.lower()


def canonical_path(value: str) -> str:
    """One relative POSIX spelling with no Windows filename/stream aliases."""
    if not isinstance(value, str) or not value or _FORBIDDEN.search(value):
        raise Refused("Invalid relative file path")
    parts = value.split("/")
    if any(not part or part in (".", "..") or part.endswith((" ", "."))
           or _DEVICE.fullmatch(part) for part in parts):
        raise Refused("Noncanonical relative file path")
    return value


def mind_path(value: str) -> bool:
    """Whole-file Rule14 list with explicit governance/Stage2 path exclusions.

    This is deployment path admission, not classify_ab or a Stage2 permission.
    Runtime-path-binding content still requires the independent Rule10 boundary.
    Data-use/privacy authority is separate: a code directory named capture, judge
    or private is not itself excluded from the operator's whole-file path list.
    """
    try:
        parts = canonical_path(value).split("/")
    except Refused:
        return False
    if any(part.casefold() in _EXCLUDED_COMPONENTS
           or part.casefold().split(".", 1)[0] == "runtime_path_bindings" for part in parts):
        return False
    if len(parts) >= 3 and parts[:2] in (["sandbox", "mind"], ["configs", "mind"]):
        return True
    if len(parts) == 2 and parts[0] == "tools" and parts[1].startswith("wd_q4_"):
        return True
    if len(parts) == 3 and parts[:2] == ["tests", "tools"] and parts[2].startswith("test_wd_q4_"):
        return True
    return (len(parts) == 4 and parts[:3] == ["ops", "windows", "reboot"]
            and parts[3].startswith(("Get-WdQ4", "Invoke-WdQ4")) and parts[3].endswith(".ps1"))


def _names(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not values or len(values) > MAX_FILES:
        raise Refused("A bounded, nonempty explicit file set is required")
    names = tuple(canonical_path(value) for value in values)
    if len({value.casefold() for value in names}) != len(names):
        raise Refused("File set has a duplicate or case collision")
    return tuple(sorted(names))


def _unlinked(path: Path) -> None:
    """Reject symbolic links and Windows reparse points, including ancestors."""
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except OSError as error:
            raise Refused("Missing or unreadable path") from error
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise Refused("Linked/reparse path is not eligible")


def _file(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    _unlinked(path)
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise Refused("Nonregular, linked or over-budget file")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise Refused("File exceeded byte budget during read")
        return data
    except OSError as error:
        raise Refused("Unreadable file") from error


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused("Duplicate JSON key")
        result[key] = value
    return result


def _json(data: bytes):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object,
                          parse_constant=lambda _: (_ for _ in ()).throw(Refused("Nonfinite JSON")))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Refused("Malformed or duplicate-key JSON") from error


@dataclass(frozen=True)
class InstalledPolicy:
    generation: str
    manifest_sha256: str
    policy_sha256: str
    files: Mapping[str, str]


def verify_installed_policy(root: Path, external_manifest_sha256: str, generation: str) -> InstalledPolicy:
    """Validate an externally selected, complete immutable installed bundle.

    The external anchor/root must come from trusted launcher state. Supplying an
    arbitrary hash is not an operator signature. A worktree copy never substitutes
    for the executing installed policy, even if its bytes happen to be identical.
    """
    root = Path(root).absolute()
    if not isinstance(generation, str) or not _SHA1.fullmatch(generation) or root.name != generation:
        raise Refused("Installed generation/root mismatch")
    _unlinked(root)
    executing = Path(__file__).absolute()
    if executing != root.joinpath(*POLICY_PATH.split("/")):
        raise Refused("Policy must execute from the installed bundle")
    manifest = _file(root / "deployment-manifest.json")
    anchor = _hash(external_manifest_sha256)
    if _sha(manifest) != anchor:
        raise Refused("Installed external manifest anchor mismatch")
    parsed = _json(manifest)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("files"), dict):
        raise Refused("Installed manifest has no file mapping")
    files = parsed["files"]
    names = _names(tuple(files))
    if POLICY_PATH not in files or "deployment-manifest.json" in files:
        raise Refused("Installed policy is not pinned or manifest pins itself")
    expected = {name: _hash(files[name]) for name in names}
    seen = set()
    total = 0
    for directory, dirs, leaves in os.walk(root, followlinks=False, onerror=lambda e: (_ for _ in ()).throw(e)):
        for name in dirs:
            _unlinked(Path(directory) / name)
        for name in leaves:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == "deployment-manifest.json":
                continue
            if relative not in expected:
                raise Refused("Unmanifested installed file")
            data = _file(path)
            total += len(data)
            if total > MAX_TOTAL_BYTES or _sha(data) != expected[relative]:
                raise Refused("Installed file hash/budget mismatch")
            seen.add(relative)
    if seen != set(expected):
        raise Refused("Installed manifest closure is incomplete")
    return InstalledPolicy(generation, anchor, expected[POLICY_PATH], MappingProxyType(expected))


def _git(repo: Path, *args: str) -> bytes:
    # Never execute candidate Python, hooks, textconv, external diff or a shell.
    # Read-only Git probes also avoid optional index locks and inherited GIT_* routing.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_CONFIG_NOSYSTEM="1")
    try:
        result = subprocess.run(
            ["git", "-c", "core.longpaths=true", "-c", "core.fsmonitor=false",
             "-c", "core.hooksPath=" + os.devnull, "-c", "core.excludesFile=" + os.devnull,
             "-c", "diff.external=", "-C", str(repo), *args],
            env=env, capture_output=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused("Git probe unavailable") from error
    if result.returncode:
        raise Refused(f"Git {args[0]} probe refused (exit {result.returncode})")
    return result.stdout


def _tree(repo: Path, commit: str) -> dict[str, tuple[str, str]]:
    entries = {}
    try:
        for record in _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit).split(b"\0"):
            if not record:
                continue
            descriptor, path = record.split(b"\t", 1)
            mode, kind, oid = descriptor.decode("ascii").split(" ")
            entries[path.decode("utf-8")] = (mode, oid)
    except (ValueError, UnicodeError) as error:
        raise Refused("Unparseable Git tree") from error
    return entries


@dataclass(frozen=True)
class TrackedClosure:
    installed_commit: str
    main_commit: str
    main_tree: str
    changed_paths: tuple[str, ...]
    files: Mapping[str, bytes | None]
    manifest: bytes
    manifest_sha256: str


def build_tracked_closure(*, repo: Path, installed_commit: str, main_commit: str,
                          main_tree: str, members: Sequence[str]) -> TrackedClosure:
    """Export one explicit reviewed file set from exact main Git objects.

    Rename detection is disabled: deletion and addition both enter admission.
    Deleted members are recorded as absence, never silently omitted. The manifest
    fixes source commit/tree, installed base, changes, file modes, hashes and sizes.
    This is not a resolver for imports or a proof that a deploy is authorized.
    """
    repo = Path(repo).absolute()
    _unlinked(repo)
    if any(not isinstance(value, str) or not _SHA1.fullmatch(value)
           for value in (installed_commit, main_commit, main_tree)):
        raise Refused("Full exact Git hashes required")
    # Even a read-only status probe can execute a clean/process filter when it
    # hashes a same-size modified file. Refuse configured drivers before status;
    # never run them and then call their side effects a failed admission.
    keys = _git(repo, "config", "--name-only", "--list", "-z").split(b"\0")
    if any(key.lower().startswith(b"filter.") for key in keys):
        raise Refused("Configured Git filters are unsupported during admission")
    if (_git(repo, "rev-parse", "HEAD").strip().decode("ascii") != main_commit
            or _git(repo, "rev-parse", "refs/remotes/origin/main").strip().decode("ascii") != main_commit
            or _git(repo, "rev-parse", main_commit + "^{tree}").strip().decode("ascii") != main_tree):
        raise Refused("Exact main/HEAD/tree mismatch")
    old = _tree(repo, installed_commit)
    new = _tree(repo, main_commit)
    # Parent config inspection does not cover a nested repository's filters.
    # Reject candidate gitlinks before status can recurse into one, including a
    # gitlink outside the exported member set. No nested working code is needed.
    if any(mode == "160000" for mode, _ in new.values()):
        raise Refused("Candidate gitlinks are unsupported before status admission")
    if _git(repo, "status", "--porcelain", "-z", "--untracked-files=all"):
        raise Refused("Candidate tree is not clean")
    _git(repo, "merge-base", "--is-ancestor", installed_commit, main_commit)
    try:
        changed = tuple(sorted(path.decode("utf-8") for path in
                               _git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
                                    "--name-only", "-z", installed_commit, main_commit).split(b"\0") if path))
    except UnicodeError as error:
        raise Refused("Non-UTF8 Git path") from error
    names = _names(members)
    if not changed or any(not mind_path(path) for path in (*changed, *names)):
        raise Refused("Diff/member is not wholly Mind eligible")
    if not set(changed).issubset(names):
        raise Refused("Tracked closure omits a changed endpoint")
    data = {}
    descriptors = {}
    total = 0
    for name in names:
        for tree in (old, new):
            if name in tree and tree[name][0] not in ("100644", "100755"):
                raise Refused("Symlink or gitlink member is forbidden")
        if name not in new:
            if name not in old:
                raise Refused("Member is not tracked in either source tree")
            data[name] = descriptors[name] = None
            continue
        mode, oid = new[name]
        size = int(_git(repo, "cat-file", "-s", oid).strip())
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            raise Refused("Tracked file byte budget exceeded")
        content = _git(repo, "cat-file", "blob", oid)
        if len(content) != size:
            raise Refused("Git blob size changed")
        data[name] = content
        descriptors[name] = {"mode": mode, "git_blob": oid, "bytes": size, "sha256": _sha(content)}
        total += size
    manifest = json.dumps({"schema": "wd.mind-tracked-closure.v1", "installed_commit": installed_commit,
                           "main_commit": main_commit, "main_tree": main_tree,
                           "changed_paths": changed, "files": descriptors},
                          sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return TrackedClosure(installed_commit, main_commit, main_tree, changed,
                          MappingProxyType(data), manifest, _sha(manifest))
