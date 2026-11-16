# SPDX-License-Identifier: BUSL-1.1
"""Run tests on a byte-verified Git export, not a checkout or development tree.

Execution currently refuses before creating a tree: exact owned-Job identity
and race-free Windows path traversal have not been established.
The Windows PowerShell companion is intended to execute this program inside the
caller-owned bounded Job Object. D15 is an unmodified, hash-checked pytest hook, not an OS
sandbox: it does not attest child-process file access or resolve all links.
Nothing here creates refs, modifies a checkout, or grants a bridge claim.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import threading

HOOK_SHA256 = '9bb5415cd1e922ee7e0892ff7aa3c9e8251f58ab54044c5b810edb49dff77541'
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_COMMIT = re.compile(r'^[0-9a-f]{40}$')
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$', re.I)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain(path: Path) -> None:
    info = path.lstat()
    if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('reparse_point')


def _plain_ancestors(path: Path) -> None:
    for candidate in (path, *path.parents):
        _plain(candidate)


def _safe_name(name: str) -> str:
    if not name or '\\' in name or ':' in name or '\x00' in name:
        raise ValueError('unsafe_name')
    parts = name.split('/')
    if any(not part or part in ('.', '..') or part.endswith(('.', ' '))
           or _RESERVED.match(part) or part.casefold() == '.git' for part in parts):
        raise ValueError('unsafe_name')
    if PurePosixPath(name).is_absolute():
        raise ValueError('unsafe_name')
    return name


def _tree_entries(raw: bytes) -> dict[str, str]:
    result: dict[str, str] = {}
    folded: set[str] = set()
    for row in raw.split(b'\0'):
        if not row:
            continue
        meta, sep, name_bytes = row.partition(b'\t')
        if not sep:
            raise ValueError('tree_record')
        mode, kind, oid = meta.decode('ascii').split(' ')
        if mode not in ('100644', '100755') or kind != 'blob' or not re.fullmatch(r'[0-9a-f]{40}', oid):
            raise ValueError('non_regular_git_entry')
        name = _safe_name(name_bytes.decode('utf-8', 'strict'))
        key = ntpath.normcase(name.replace('/', '\\'))
        if key in folded:
            raise ValueError('duplicate_path')
        folded.add(key)
        result[name] = oid
    if not result:
        raise ValueError('empty_tree')
    return result


def _git(git: str, repo: str, *args: str) -> list[str]:
    return [git, '--no-replace-objects', '--no-optional-locks', '-C', repo, *args]


def _checked_directory(root: Path, parts: list[str]) -> Path:
    """Refuse known reparse ancestors before each mkdir; not a Windows atomic fence."""
    current = root
    _plain_ancestors(current)
    for part in parts:
        _plain_ancestors(current)
        candidate = current / part
        try:
            candidate.lstat()
        except FileNotFoundError:
            candidate.mkdir()
        _plain(candidate)
        if not candidate.is_dir():
            raise ValueError('not_directory')
        current = candidate
    return current


def _extract_archive(stream, target: Path, entries: dict[str, str]) -> None:
    seen: set[str] = set()
    with tarfile.open(fileobj=stream, mode='r|*') as archive:
        for item in archive:
            name = item.name.rstrip('/')
            _safe_name(name)
            if item.isdir():
                continue
            if not item.isfile() or name not in entries or name in seen:
                raise ValueError('archive_member')
            if item.size > 128 * 1024 * 1024:
                raise ValueError('archive_member_too_large')
            parts = name.split('/')
            parent = _checked_directory(target, parts[:-1])
            path = parent / parts[-1]
            source = archive.extractfile(item)
            if source is None:
                raise ValueError('archive_member')
            with path.open('xb') as dest:
                left = item.size
                while left:
                    chunk = source.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise ValueError('archive_truncated')
                    dest.write(chunk)
                    left -= len(chunk)
            seen.add(name)
    if seen != set(entries):
        raise ValueError('archive_missing')


def _verify_tree(git: str, repo: str, target: Path, entries: dict[str, str], *, audit_allowed: bool) -> str:
    # Compare actual file bytes against cat-file blob bytes, not just working-tree hashes.
    process = subprocess.Popen(_git(git, repo, 'cat-file', '--batch'), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdin is not None and process.stdout is not None
    errors: list[BaseException] = []
    def feed() -> None:
        try:
            for oid in entries.values():
                process.stdin.write((oid + '\n').encode('ascii'))
            process.stdin.close()
        except BaseException as exc:
            errors.append(exc)
    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    digest = hashlib.sha256()
    try:
        for name, oid in entries.items():
            header = process.stdout.readline().split()
            if len(header) != 3 or header[0].decode('ascii') != oid or header[1] != b'blob':
                raise ValueError('blob_header')
            left = int(header[2])
            path = target.joinpath(*name.split('/'))
            _plain(path)
            file_digest = hashlib.sha256()
            with path.open('rb') as stream:
                while left:
                    size = min(left, 1024 * 1024)
                    blob = process.stdout.read(size)
                    actual = stream.read(size)
                    if len(blob) != size or actual != blob:
                        raise ValueError('blob_mismatch')
                    file_digest.update(actual)
                    left -= size
                if stream.read(1) or process.stdout.read(1) != b'\n':
                    raise ValueError('blob_mismatch')
            digest.update(name.encode('utf-8') + b'\0' + file_digest.hexdigest().encode('ascii') + b'\n')
        writer.join()
        if errors or process.wait() != 0:
            raise ValueError('git_cat_file')
    finally:
        if process.poll() is None:
            process.terminate()  # only this invocation's own test-source child
            process.wait()
    expected = set(entries)
    for path in target.rglob('*'):
        _plain(path)
        if path.is_file():
            name = path.relative_to(target).as_posix()
            if name not in expected and not (audit_allowed and name.startswith('.codex-audit/')):
                raise ValueError('extra_file')
    return digest.hexdigest()


def _inside_windows_job() -> bool:
    if sys.platform != 'win32':
        return False
    inside = ctypes.c_int()
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.IsProcessInJob.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int))
    return bool(kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(inside)) and inside.value)


def _execution_boundary_proven() -> bool:
    """No proof yet binds this process to the wrapper's exact job and limits.

    Path checks also have a Windows rename/reparse TOCTOU. Until both boundaries
    are independently established, the command intentionally refuses execution.
    """
    return False


def _validate_workspace(git: str, repo: Path, workspace: Path) -> None:
    """Require a registered, non-nested workspace in the same Git repository."""
    for parent in workspace.parents:
        if (parent / '.git').exists():
            raise ValueError('nested_workspace')
    def git_value(path: Path, *args: str) -> str:
        return subprocess.check_output(_git(git, str(path), 'rev-parse', *args),
                                       stderr=subprocess.DEVNULL).decode('utf-8').strip()
    top = Path(git_value(workspace, '--show-toplevel'))
    common = Path(git_value(workspace, '--path-format=absolute', '--git-common-dir'))
    expected_common = Path(git_value(repo, '--path-format=absolute', '--git-common-dir'))
    if (ntpath.normcase(str(top)) != ntpath.normcase(str(workspace)) or
            ntpath.normcase(str(common)) != ntpath.normcase(str(expected_common))):
        raise ValueError('workspace_binding')


def _run(config: dict) -> dict:
    if not _execution_boundary_proven():
        raise ValueError('execution_boundary_unproven')
    if not _inside_windows_job():
        raise ValueError('job_membership_required')
    keys = {'git', 'git_sha256', 'repo', 'workspace', 'commit', 'work', 'hook', 'python_sha256', 'targets'}
    if type(config) is not dict or set(config) != keys:
        raise ValueError('config')
    if not _COMMIT.fullmatch(config['commit']) or not _SHA256.fullmatch(config['git_sha256']) or not _SHA256.fullmatch(config['python_sha256']):
        raise ValueError('pin')
    git, hook, work = Path(config['git']), Path(config['hook']), Path(config['work'])
    repo, workspace = Path(config['repo']), Path(config['workspace'])
    for path in (git, hook, work, repo, workspace):
        if not path.is_absolute():
            raise ValueError('absolute_path_required')
        _plain_ancestors(path)
    audit = workspace / '.codex-audit'
    if not work.is_dir() or any(work.iterdir()) or audit not in work.parents:
        raise ValueError('work_directory')
    if _sha(git.read_bytes()) != config['git_sha256'] or _sha(Path(sys.executable).read_bytes()) != config['python_sha256']:
        raise ValueError('executable_pin')
    hook_bytes = hook.read_bytes()
    if _sha(hook_bytes) != HOOK_SHA256:
        raise ValueError('hook_pin')
    _validate_workspace(str(git), repo, workspace)
    targets = config['targets']
    if type(targets) is not list or not targets or len(targets) > 500:
        raise ValueError('targets')
    for item in targets:
        if type(item) is not str or not item.startswith('tests/') or not _safe_name(item.split('::')[0]).endswith('.py'):
            raise ValueError('target')
    command = _git(str(git), str(repo), 'rev-parse', '--verify', config['commit'] + '^{commit}')
    if subprocess.check_output(command, stderr=subprocess.DEVNULL).decode('ascii').strip() != config['commit']:
        raise ValueError('commit')
    entries = _tree_entries(subprocess.check_output(_git(str(git), str(repo), 'ls-tree', '-r', '-z', '--full-tree', config['commit']), stderr=subprocess.DEVNULL))
    if any(item.split('::')[0] not in entries for item in targets):
        raise ValueError('untracked_target')
    target = work / 'export'
    target.mkdir()
    archive = subprocess.Popen(_git(str(git), str(repo), 'archive', '--format=tar', config['commit']), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert archive.stdout is not None
    try:
        _extract_archive(archive.stdout, target, entries)
        if archive.wait() != 0:
            raise ValueError('archive')
    finally:
        if archive.poll() is None:
            archive.terminate()
            archive.wait()
    before = _verify_tree(str(git), str(repo), target, entries, audit_allowed=False)
    audit = target / '.codex-audit' / 'verified-export'
    hook_dir = audit / 'hook'
    hook_dir.mkdir(parents=True)
    (hook_dir / 'hook_plugin_d15.py').write_bytes(hook_bytes)
    report = audit / 'd15.jsonl'
    junit = audit / 'pytest.xml'
    env = {key: value for key, value in os.environ.items() if key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
    env.update(PYTEST_ADDOPTS='-p hook_plugin_d15 -p no:cacheprovider',
               PYTHONPATH=str(hook_dir) + os.pathsep + str(target), PYTHONDONTWRITEBYTECODE='1',
               WD_D15_HOOK_REPORT=str(report))
    args = [sys.executable, '-B', '-m', 'pytest', '-q', '--tb=short', '-ra',
            '--basetemp=' + str(Path(env['TEMP']) / 'pytest'), '--junitxml=' + str(junit), *targets]
    result = subprocess.run(args, cwd=str(target), env=env, stdin=subprocess.DEVNULL, check=False)
    after = _verify_tree(str(git), str(repo), target, entries, audit_allowed=True)
    if before != after or not report.is_file() or not junit.is_file():
        raise ValueError('postcheck')
    records = [json.loads(line) for line in report.read_text(encoding='utf-8').splitlines()]
    if not records or records[-1].get('kind') != 'summary' or records[-1].get('refused') != 0 or records[-1].get('exit') != 0 or result.returncode != 0:
        raise ValueError('pytest_or_hook')
    return {'schema': 'wd.verified-test-export.v1', 'commit': config['commit'], 'files': len(entries),
            'tree_manifest_sha256': before, 'hook_sha256': HOOK_SHA256, 'pytest_exit': result.returncode,
            'junit_sha256': _sha(junit.read_bytes()), 'hook_report_sha256': _sha(report.read_bytes()),
            'scope': 'export_test_only', 'child_filesystem_attestation': 'UNKNOWN'}


def main() -> int:
    try:
        config = json.load(sys.stdin)
        result = _run(config)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({'schema': 'wd.verified-test-export.v1', 'status': 'refused'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
