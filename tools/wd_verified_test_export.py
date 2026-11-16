# SPDX-License-Identifier: BUSL-1.1
"""Run tests on a byte-verified Git export, not a checkout or development tree.

Use only from a trusted caller's pinned PowerShell companion. Its own Job result,
not this process's output or job-membership check, is the bounded-run evidence.
Same-user Windows rename/reparse races are not excluded. D15 is an unmodified,
hash-checked pytest hook, not an OS sandbox: it does not attest child-process
file access or resolve all links. Nothing creates refs or grants a bridge claim.
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
import xml.etree.ElementTree as ET

HOOK_SHA256 = '9bb5415cd1e922ee7e0892ff7aa3c9e8251f58ab54044c5b810edb49dff77541'
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_COMMIT = re.compile(r'^[0-9a-f]{40}$')
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$', re.I)

# Additional measurement plugin. The pinned D15 plugin is copied unchanged.
# Only hashes of node IDs are written; this is an observation within pytest,
# not attestation against a malicious same-user process or malicious plugin.
_SELECTION_PLUGIN = r'''import hashlib, json, os
import pytest

PHASE = os.environ['WD_VERIFIED_SELECTION_PHASE']
REPORT = os.environ['WD_VERIFIED_SELECTION_REPORT']
TARGETS = json.loads(os.environ['WD_VERIFIED_SELECTION_TARGETS'])
STATE = {'before': None, 'after': None, 'matches': None, 'deselected': 0, 'phases': {}}

def nodehash(node):
    return hashlib.sha256(node.encode('utf-8')).hexdigest()

def matches(node, target):
    return node == target or node.startswith(target + '[') or node.startswith(target + '::')

@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    if PHASE not in ('collect', 'execute') or bool(config.option.collectonly) != (PHASE == 'collect'):
        raise pytest.UsageError('verified export refuses unexpected collect-only mode')
    if config.getoption('keyword') or config.getoption('markexpr'):
        raise pytest.UsageError('verified export refuses keyword or marker filtering')

@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    STATE['before'] = [nodehash(item.nodeid) for item in items]
    yield
    STATE['after'] = [nodehash(item.nodeid) for item in items]
    STATE['matches'] = [sum(matches(item.nodeid, target) for item in items) for target in TARGETS]

@pytest.hookimpl(tryfirst=True)
def pytest_deselected(items):
    STATE['deselected'] += len(items)

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_logreport(report):
    node = nodehash(report.nodeid)
    STATE['phases'].setdefault(node, {}).setdefault(report.when, []).append(report.outcome)
    if report.when == 'teardown':
        report.user_properties.append(('wd_nodeid_sha256', node))

@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    value = {'schema': 'wd.pytest-selection-observation.v1', 'phase': PHASE, **STATE}
    with open(REPORT, 'x', encoding='utf-8') as out:
        json.dump(value, out, sort_keys=True)
'''


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_file(path: Path, limit: int) -> bytes:
    _plain(path)
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('report_too_large')
    return data


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
    # Git archive may otherwise expand LF to CRLF based on host checkout config.
    # Committed export attributes can still change bytes: the comparison refuses.
    return [git, '--no-replace-objects', '--no-optional-locks',
            '-c', 'core.autocrlf=false', '-c', 'core.eol=lf', '-C', repo, *args]


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


def _junit_counts(data: bytes) -> dict[str, int]:
    if not data or len(data) > 16 * 1024 * 1024:
        raise ValueError('junit')
    root = ET.fromstring(data)
    cases = list(root.iter('testcase'))
    skipped = sum(case.find('skipped') is not None for case in cases)
    failed = sum(case.find('failure') is not None for case in cases)
    errors = sum(case.find('error') is not None for case in cases)
    passed = len(cases) - skipped - failed - errors
    if not cases or passed < 1 or failed or errors or passed + skipped != len(cases):
        raise ValueError('junit')
    return {'tests_total': len(cases), 'tests_passed': passed, 'tests_skipped': skipped}


def _selection_evidence(collect: dict, execute: dict, junit: bytes, target_count: int) -> dict[str, int]:
    """Cross-check two observed pytest selections, per-node phases, and JUnit."""
    schema = 'wd.pytest-selection-observation.v1'
    keys = {'schema', 'phase', 'before', 'after', 'matches', 'deselected', 'phases'}
    for value, phase in ((collect, 'collect'), (execute, 'execute')):
        if type(value) is not dict or set(value) != keys or value['schema'] != schema or value['phase'] != phase:
            raise ValueError('selection')
        before, after, matches = value['before'], value['after'], value['matches']
        if (type(before) is not list or type(after) is not list or not before or
                any(type(x) is not str or not _SHA256.fullmatch(x) for x in before + after) or
                len(before) != len(set(before)) or len(after) != len(set(after)) or
                set(before) != set(after) or value['deselected'] != 0 or
                type(matches) is not list or len(matches) != target_count or
                any(type(x) is not int or x < 1 for x in matches)):
            raise ValueError('selection')
    nodes = set(collect['after'])
    if (nodes != set(execute['after']) or collect['matches'] != execute['matches'] or
            collect['phases'] != {} or type(execute['phases']) is not dict):
        raise ValueError('selection')
    if set(execute['phases']) != nodes:
        raise ValueError('selection')
    for phases in execute['phases'].values():
        if type(phases) is not dict or phases != {'setup': ['passed'], 'call': ['passed'], 'teardown': ['passed']}:
            raise ValueError('selection')
    counts = _junit_counts(junit)
    if counts['tests_total'] != len(nodes) or counts['tests_skipped']:
        raise ValueError('selection')
    cases = list(ET.fromstring(junit).iter('testcase'))
    hashes = []
    for case in cases:
        properties = case.findall("./properties/property[@name='wd_nodeid_sha256']")
        if len(properties) != 1:
            raise ValueError('selection')
        node = properties[0].get('value')
        if type(node) is not str or not _SHA256.fullmatch(node):
            raise ValueError('selection')
        hashes.append(node)
    if len(hashes) != len(set(hashes)) or set(hashes) != nodes:
        raise ValueError('selection')
    return counts


def _validate_targets(targets: object) -> list[str]:
    if type(targets) is not list or not targets or len(targets) > 500 or sum(len(x) for x in targets if type(x) is str) > 16000:
        raise ValueError('targets')
    for item in targets:
        if (type(item) is not str or not item.startswith('tests/') or '::' not in item or
                any(not part for part in item.split('::')) or '\n' in item or '\r' in item or
                not _safe_name(item.split('::')[0]).endswith('.py')):
            raise ValueError('target')
    return targets


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
    # A positive result is a diagnostic only: any other job may also satisfy it.
    # The trusted pinned PowerShell caller must check its own bounded Job result.
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
    targets = _validate_targets(config['targets'])
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
    d15_plugin = hook_dir / 'hook_plugin_d15.py'
    selection_plugin = hook_dir / 'hook_verified_selection.py'
    d15_plugin.write_bytes(hook_bytes)
    selection_bytes = _SELECTION_PLUGIN.encode('utf-8')
    selection_plugin.write_bytes(selection_bytes)
    collect_report = audit / 'd15-collect.jsonl'
    report = audit / 'd15-execute.jsonl'
    collect_selection = audit / 'selection-collect.json'
    execute_selection = audit / 'selection-execute.json'
    junit = audit / 'pytest.xml'
    env = {key: value for key, value in os.environ.items() if key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')}
    env.update(PYTEST_ADDOPTS='-p hook_plugin_d15 -p hook_verified_selection -p no:cacheprovider',
               PYTHONPATH=str(hook_dir) + os.pathsep + str(target), PYTHONDONTWRITEBYTECODE='1',
               WD_VERIFIED_SELECTION_TARGETS=json.dumps(targets, ensure_ascii=True))
    results = []
    for phase, d15_report, selected in (('collect', collect_report, collect_selection),
                                        ('execute', report, execute_selection)):
        env.update(WD_D15_HOOK_REPORT=str(d15_report), WD_VERIFIED_SELECTION_PHASE=phase,
                   WD_VERIFIED_SELECTION_REPORT=str(selected))
        args = [sys.executable, '-B', '-m', 'pytest', '-q', '--tb=short', '-ra',
                '--rootdir=' + str(target), '--basetemp=' + str(Path(env['TEMP']) / ('pytest-' + phase))]
        args += ['--collect-only'] if phase == 'collect' else ['--junitxml=' + str(junit)]
        result = subprocess.run(args + targets, cwd=str(target), env=env, stdin=subprocess.DEVNULL, check=False)
        results.append(result.returncode)
        if result.returncode != 0 or not d15_report.is_file() or not selected.is_file():
            raise ValueError('pytest_or_hook')
        for path in (d15_report, selected, d15_plugin, selection_plugin):
            _plain(path)
        if _sha(d15_plugin.read_bytes()) != HOOK_SHA256 or selection_plugin.read_bytes() != selection_bytes:
            raise ValueError('hook_pin')
        rows = [json.loads(line) for line in _bounded_file(d15_report, 64 * 1024 * 1024).decode('utf-8').splitlines()]
        if not rows or rows[-1].get('kind') != 'summary' or rows[-1].get('refused') != 0 or rows[-1].get('exit') != 0:
            raise ValueError('pytest_or_hook')
    after = _verify_tree(str(git), str(repo), target, entries, audit_allowed=True)
    if before != after or not junit.is_file():
        raise ValueError('postcheck')
    junit_bytes = _bounded_file(junit, 16 * 1024 * 1024)
    collect_bytes = _bounded_file(collect_selection, 16 * 1024 * 1024)
    execute_bytes = _bounded_file(execute_selection, 16 * 1024 * 1024)
    counts = _selection_evidence(json.loads(collect_bytes), json.loads(execute_bytes), junit_bytes, len(targets))
    return {'schema': 'wd.test-export-intermediate.v1', 'authority': 'none', **counts,
            'commit': config['commit'], 'files': len(entries),
            'tree_manifest_sha256': before, 'hook_sha256': HOOK_SHA256, 'pytest_exit': results[-1],
            'junit_sha256': _sha(junit_bytes), 'hook_report_sha256': _sha(_bounded_file(report, 64 * 1024 * 1024)),
            'collect_hook_sha256': _sha(_bounded_file(collect_report, 64 * 1024 * 1024)),
            'collect_selection_sha256': _sha(collect_bytes),
            'execute_selection_sha256': _sha(execute_bytes),
            'selection_plugin_sha256': _sha(selection_bytes), 'scope': 'export_test_only',
            'selection_coverage': 'OBSERVED_EXACT', 'child_filesystem_attestation': 'UNKNOWN',
            'pytest_discovery_authentication': 'UNKNOWN'}


def main() -> int:
    try:
        config = json.load(sys.stdin)
        result = _run(config)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        safe_reasons = {'job_membership_required', 'config', 'pin', 'absolute_path_required',
                        'work_directory', 'executable_pin', 'hook_pin', 'targets', 'target',
                        'untracked_target', 'commit', 'nested_workspace', 'workspace_binding',
                        'unsafe_name', 'tree_record', 'non_regular_git_entry', 'duplicate_path',
                        'empty_tree', 'archive_member', 'archive_member_too_large',
                        'archive_truncated', 'archive_missing', 'archive', 'reparse_point',
                        'not_directory', 'blob_header', 'blob_mismatch', 'git_cat_file',
                        'extra_file', 'postcheck', 'pytest_or_hook', 'junit', 'selection', 'report_too_large'}
        reason = str(exc) if type(exc) is ValueError and str(exc) in safe_reasons else 'internal_error'
        print(json.dumps({'schema': 'wd.test-export-intermediate.v1', 'authority': 'none',
                          'status': 'refused', 'reason': reason}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
