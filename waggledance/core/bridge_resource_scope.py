"""Physical local/shared resources and repository-logical source resources."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from typing import Sequence


@dataclass(frozen=True)
class ResourceScope:
    kind: str
    path: str
    root: str = ""


def _unalias(path: Path) -> str:
    if any(part and part != '.' and (part.endswith(('.', ' ')) or re.search(r'~[0-9]', part))
           for part in str(path).replace('\\', '/').split('/')):
        raise ValueError('ambiguous Windows root alias')
    absolute = path.absolute()
    for component in (absolute, *absolute.parents):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError(f"resource path contains a link/reparse point: {component}")
    return str(absolute).replace('\\', '/').rstrip('/').casefold()


_ABSOLUTE_POINTER = re.compile(r'^(?:[A-Za-z]:)?[/\\]')


def _pointer(path: str) -> str:
    """The first line of a small git pointer file (.git or commondir), surrounding whitespace removed."""
    with open(path, 'rb') as handle:
        data = handle.read(4097)
    if len(data) > 4096:
        raise ValueError('git pointer file is oversized')
    lines = data.decode('utf-8').splitlines()
    return lines[0].strip() if lines else ''


def _git_top_level(top: str) -> None:
    """Raise unless ``top`` is a git top level: "<top>/.git" is a directory, or a file "gitdir: <dir>" whose admin dir
    has commondir and a gitdir back-link naming this .git (RS7-L2); the git dir holds HEAD and its common dir holds
    objects/ and refs/. Same test as tools/bridge_v2_resource_scope.py, read from the file system only."""
    marker = os.lstat(top + '/.git')
    if stat.S_ISDIR(marker.st_mode):
        git_dir = top + '/.git'
    elif stat.S_ISREG(marker.st_mode):
        line = _pointer(top + '/.git')
        if not line.startswith('gitdir:') or not line[7:].strip():
            raise ValueError('the .git file is not a gitdir pointer')
        target = line[7:].strip()
        git_dir = target if _ABSOLUTE_POINTER.match(target) else top + '/' + target
        os.lstat(git_dir + '/commondir')
        back = _pointer(git_dir + '/gitdir')
        back = back if _ABSOLUTE_POINTER.match(back) else git_dir + '/' + back
        if os.path.normcase(os.path.normpath(back)) != os.path.normcase(os.path.normpath(top + '/.git')):
            raise ValueError("the worktree admin dir's gitdir back-link names another .git")
    else:
        raise ValueError('.git is neither a directory nor a file')
    common = git_dir
    try:
        os.lstat(git_dir + '/commondir')
    except FileNotFoundError:
        pass
    else:
        target = _pointer(git_dir + '/commondir')
        common = target if _ABSOLUTE_POINTER.match(target) else git_dir + '/' + target
    if not (stat.S_ISREG(os.lstat(git_dir + '/HEAD').st_mode) and stat.S_ISDIR(os.lstat(common + '/objects').st_mode)
            and stat.S_ISDIR(os.lstat(common + '/refs').st_mode)):
        raise ValueError('the git dir lacks HEAD, objects or refs')


def _require_top_level(cwd: str) -> None:
    """RS7: scopes are repository-relative and an absolute entry is made relative to the cwd, so a cwd below the top
    level (or outside any repository) names one file two ways and resources_overlap misses the conflict; a valid top
    level nested below another valid top level does the same (RS7-D). Both are refused, as by the v2 resolver."""
    top = cwd.rstrip('/\\')
    try:
        _git_top_level(top)
    except (OSError, ValueError) as error:
        raise ValueError('the claim cwd is not a repository top level (no valid .git there): every scope except * '
                         'is refused, fail-closed (' + str(error)[:120] + ')') from None
    normal = os.path.normpath(os.path.abspath(top)).replace('\\', '/')
    parts = normal.rstrip('/').split('/')
    floor = 4 if normal.startswith('//') else 1   # a UNC path's shallowest directory is its share
    for depth in range(len(parts) - 1, floor - 1, -1):
        ancestor = '/'.join(parts[:depth])
        try:
            _git_top_level(ancestor)
        except (OSError, ValueError):
            continue
        raise ValueError('the claim cwd is a repository nested inside another repository (' + ancestor[:120]
                         + '): one file has two repository paths, so every scope except * is refused, fail-closed')


def resolve_resources(scopes: Sequence[str], *, cwd: str, bridge_root: str) -> tuple[ResourceScope, ...]:
    result = []
    for scope in scopes:
        raw = scope.replace('\\', '/').strip()
        if raw != '*' and cwd:
            _require_top_level(cwd)   # a claim without cwd stays conservative (resources_overlap), never refused here
        kind = 'repo'
        explicit = re.match(r'^([a-z_-]+):', raw, re.I)
        if explicit and not re.match(r'^[A-Za-z]:/', raw):
            kind, raw = raw.split(':', 1)
            kind = kind.lower()
            if kind not in ('repo', 'worktree', 'shared'):
                raise ValueError('unknown resource kind')
        elif raw.casefold().strip('/') == '.codex-audit/wd-current-state.json' and cwd:
            kind = 'worktree'
        if '..' in raw.split('/') or ':' in raw and not re.match(r'^[A-Za-z]:/', raw):
            raise ValueError('resource traversal or alternate stream is forbidden')
        if any(part and part != '.' and (part.endswith(('.', ' ')) or re.search(r'~[0-9]', part)) for part in raw.split('/')):
            raise ValueError('ambiguous Windows path alias')
        if Path(raw).is_absolute():
            full = _unalias(Path(raw))
            base = _unalias(Path(cwd)) if cwd else ''
            shared = _unalias(Path(bridge_root))
            if base and full.startswith(base + '/'):
                raw = full[len(base)+1:]
                if raw == '.codex-audit/wd-current-state.json': kind = 'worktree'
            elif full.startswith(shared + '/'):
                kind, raw = 'shared', full[len(shared)+1:]
            else:
                raise ValueError('absolute scope is outside the worktree/shared root')
        elif re.match(r'^[A-Za-z]:/', raw):
            raise ValueError('foreign-platform absolute scope cannot be resolved safely')
        raw = '/'.join(part for part in raw.split('/') if part and part != '.').casefold()
        if not raw or ('*' in raw and raw != '*') or '?' in raw:
            raise ValueError('scope must name a path or the whole repository (*)')
        if kind == 'worktree':
            if not cwd or not (raw == '.codex-audit' or raw.startswith('.codex-audit/')):
                raise ValueError('worktree resources require a cwd and must be under .codex-audit')
            root = _unalias(Path(cwd))
            _unalias(Path(cwd) / raw)
        elif kind == 'shared':
            root = _unalias(Path(bridge_root))
            _unalias(Path(bridge_root) / raw)
        else:
            root = ''
            if cwd and raw != '*': _unalias(Path(cwd) / raw)
        result.append(ResourceScope(kind, raw, root))
    return tuple(result)


def resources_overlap(left: ResourceScope, right: ResourceScope) -> bool:
    if left.path == '*' or right.path == '*': return True
    if left.kind == 'repo' or right.kind == 'repo':
        # An old claim without cwd remains conservative, never a bypass.
        a, b = left.path, right.path
    else:
        a, b = left.root + '/' + left.path, right.root + '/' + right.path
    return a == b or a.startswith(b + '/') or b.startswith(a + '/')
