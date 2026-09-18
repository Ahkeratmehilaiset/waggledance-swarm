"""Physical local/shared resources and repository-logical source resources."""
from __future__ import annotations

from dataclasses import dataclass
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


def resolve_resources(scopes: Sequence[str], *, cwd: str, bridge_root: str) -> tuple[ResourceScope, ...]:
    result = []
    for scope in scopes:
        raw = scope.replace('\\', '/').strip()
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
