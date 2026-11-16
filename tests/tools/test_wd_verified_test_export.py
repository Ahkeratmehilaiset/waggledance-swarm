# SPDX-License-Identifier: BUSL-1.1
"""Synthetic coverage for the test-only, byte-verified export route."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

from tools import wd_verified_test_export as export


def _tar(members):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, body, kind in members:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.size = len(body) if kind == tarfile.REGTYPE else 0
            archive.addfile(item, io.BytesIO(body) if item.size else None)
    return io.BytesIO(stream.getvalue())


@pytest.mark.parametrize('name', ['', '../x', '/root', 'a//b', 'a/../b', 'a\\b',
                                  'x:stream', '.git/config', 'CON.txt', 'x.'])
def test_unsafe_or_git_paths_are_refused(name):
    with pytest.raises(ValueError):
        export._safe_name(name)


def test_git_tree_accepts_only_unique_regular_files():
    oid = b'a' * 40
    assert export._tree_entries(b'100644 blob ' + oid + b'\ttools/a.py\0') == {'tools/a.py': 'a' * 40}
    for raw in (b'120000 blob ' + oid + b'\tlink\0',
                b'160000 commit ' + oid + b'\tsubmodule\0',
                b'100644 blob ' + oid + b'\tA\0' + b'100644 blob ' + oid + b'\ta\0'):
        with pytest.raises(ValueError):
            export._tree_entries(raw)


def test_archive_only_writes_exact_regular_selected_members(tmp_path):
    target = tmp_path / 'export'
    target.mkdir()
    content = b'synthetic file\n'
    export._extract_archive(_tar([('tests/a.py', content, tarfile.REGTYPE)]), target, {'tests/a.py': 'a' * 40})
    assert (target / 'tests/a.py').read_bytes() == content
    assert not (target / '.git').exists()


@pytest.mark.parametrize('members,entries', [
    ([('../escape', b'x', tarfile.REGTYPE)], {'safe': 'a' * 40}),
    ([('safe', b'x', tarfile.SYMTYPE)], {'safe': 'a' * 40}),
    ([('wrong', b'x', tarfile.REGTYPE)], {'safe': 'a' * 40}),
    ([], {'missing': 'a' * 40}),
    ([('safe', b'x', tarfile.REGTYPE), ('safe', b'y', tarfile.REGTYPE)], {'safe': 'a' * 40}),
])
def test_archive_refuses_traversal_links_missing_extra_or_duplicates(tmp_path, members, entries):
    target = tmp_path / 'export'
    target.mkdir()
    with pytest.raises(ValueError):
        export._extract_archive(_tar(members), target, entries)
    assert not (tmp_path / 'escape').exists()
    assert not (target / '.git').exists()


def test_existing_symlink_parent_refuses_before_creating_outside_directory(tmp_path):
    target, outside = tmp_path / 'export', tmp_path / 'outside'
    target.mkdir()
    outside.mkdir()
    try:
        os.symlink(outside, target / 'tests', target_is_directory=True)
    except OSError as error:
        if getattr(error, 'winerror', None) == 1314:
            pytest.skip('Windows account cannot create a symbolic link')
        raise
    archive = _tar([('tests/new/test.py', b'x', tarfile.REGTYPE)])
    with pytest.raises(ValueError, match='reparse_point'):
        export._extract_archive(archive, target, {'tests/new/test.py': 'a' * 40})
    assert not (outside / 'new').exists()


def test_actual_git_blob_comparison_rejects_changes_and_extra_files(tmp_path):
    git = shutil.which('git')
    if not git:
        pytest.skip('git unavailable')
    repo = Path(__file__).resolve().parents[2]
    oid = subprocess.check_output([git, '-C', str(repo), 'rev-parse', 'HEAD:README.md']).decode().strip()
    body = subprocess.check_output([git, '-C', str(repo), 'cat-file', 'blob', oid])
    target = tmp_path / 'export'
    target.mkdir()
    (target / 'README.md').write_bytes(body)
    entries = {'README.md': oid}
    first = export._verify_tree(git, str(repo), target, entries, audit_allowed=False)
    assert len(first) == 64
    (target / 'README.md').write_bytes(body + b'altered')
    with pytest.raises(ValueError):
        export._verify_tree(git, str(repo), target, entries, audit_allowed=False)
    (target / 'README.md').write_bytes(body)
    (target / 'unexpected.py').write_bytes(b'synthetic')
    with pytest.raises(ValueError):
        export._verify_tree(git, str(repo), target, entries, audit_allowed=False)


def test_reparse_attributes_are_refused_without_following_target(tmp_path, monkeypatch):
    path = tmp_path / 'synthetic'
    path.write_bytes(b'x')
    real_lstat = Path.lstat
    class Reparse:
        st_file_attributes = 0x400
        def __init__(self, actual): self.actual = actual
        def __getattr__(self, name): return getattr(self.actual, name)
    monkeypatch.setattr(Path, 'lstat', lambda self: Reparse(real_lstat(self)) if self == path else real_lstat(self))
    with pytest.raises(ValueError, match='reparse_point'):
        export._plain(path)


def test_junit_counts_passed_and_skipped_separately():
    data = b'<testsuites><testsuite><testcase name="a"/><testcase name="b"><skipped/></testcase></testsuite></testsuites>'
    assert export._junit_counts(data) == {'tests_total': 2, 'tests_passed': 1, 'tests_skipped': 1}


@pytest.mark.parametrize('data', [
    b'<testsuites/>',
    b'<testsuites><testcase><skipped/></testcase></testsuites>',
    b'<testsuites><testcase/><testcase><failure/></testcase></testsuites>',
    b'<testsuites><testcase/><testcase><error/></testcase></testsuites>',
])
def test_junit_refuses_empty_all_skipped_failed_and_error(data):
    with pytest.raises(ValueError, match='junit'):
        export._junit_counts(data)


def test_real_pytest_collect_only_is_not_accepted_as_execution(tmp_path):
    synthetic = tmp_path / 'test_synthetic.py'
    synthetic.write_text('def test_example():\n    assert True\n', encoding='utf-8')
    junit = tmp_path / 'collection.xml'
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '--collect-only',
                             '--junitxml=' + str(junit), str(synthetic)], cwd=str(tmp_path),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    assert result.returncode == 0 and junit.is_file()
    with pytest.raises(ValueError, match='junit'):
        export._junit_counts(junit.read_bytes())


def _selection_phase(tmp_path, phase, target, *extra):
    hook = tmp_path / 'hook'
    hook.mkdir(exist_ok=True)
    (hook / 'hook_verified_selection.py').write_text(export._SELECTION_PLUGIN, encoding='utf-8')
    report = tmp_path / (phase + '.json')
    junit = tmp_path / 'executed.xml'
    env = {key: value for key, value in os.environ.items() if key in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH')}
    env.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONPATH=str(hook),
               PYTEST_ADDOPTS='-p hook_verified_selection -p no:cacheprovider',
               WD_VERIFIED_SELECTION_TARGETS=json.dumps([target]), WD_VERIFIED_SELECTION_PHASE=phase,
               WD_VERIFIED_SELECTION_REPORT=str(report))
    args = [sys.executable, '-B', '-m', 'pytest', '-q', '--tb=short', '--rootdir=' + str(tmp_path)]
    args += ['--collect-only'] if phase == 'collect' else ['--junitxml=' + str(junit)]
    result = subprocess.run(args + list(extra) + [target], cwd=tmp_path, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
    return result, report, junit


def _synthetic_parameterized_suite(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / 'test_synthetic.py').write_text(
        "import pytest\n@pytest.mark.parametrize('n',[1,2],ids=['one','two'])\n"
        "def test_parameterized(n): assert n > 0\n", encoding='utf-8')
    return 'tests/test_synthetic.py::test_parameterized'


def test_collected_and_executed_parameterized_node_ids_match_junit(tmp_path):
    target = _synthetic_parameterized_suite(tmp_path)
    collected, c_report, _ = _selection_phase(tmp_path, 'collect', target)
    executed, e_report, junit = _selection_phase(tmp_path, 'execute', target)
    assert collected.returncode == 0 and executed.returncode == 0
    c = json.loads(c_report.read_bytes())
    e = json.loads(e_report.read_bytes())
    assert export._selection_evidence(c, e, junit.read_bytes(), 1) == {
        'tests_total': 2, 'tests_passed': 2, 'tests_skipped': 0}
    # The node-id hashes in evidence must not disclose parameter names.
    assert b'one' not in c_report.read_bytes() and b'two' not in e_report.read_bytes()
    e['after'].pop()
    with pytest.raises(ValueError, match='selection'):
        export._selection_evidence(c, e, junit.read_bytes(), 1)


def _synthetic_selection_evidence():
    node = 'a' * 64
    common = {'schema': 'wd.pytest-selection-observation.v1', 'before': [node],
              'after': [node], 'matches': [1], 'deselected': 0}
    collected = {**common, 'phase': 'collect', 'phases': {}}
    executed = {**common, 'phase': 'execute', 'phases': {
        node: {'setup': ['passed'], 'call': ['passed'], 'teardown': ['passed']}}}
    junit = ('<testsuites><testcase><properties><property name="wd_nodeid_sha256" value="'
             + node + '"/></properties></testcase></testsuites>').encode()
    return collected, executed, junit


@pytest.mark.parametrize('fault', ['missing_node', 'missing_target', 'deselected', 'skipped',
                                  'failed', 'duplicate_phase', 'bad_junit_node', 'missing_junit_property'])
def test_selection_evidence_refuses_missing_filtered_or_unexecuted_node(fault):
    collected, executed, junit = _synthetic_selection_evidence()
    node = 'a' * 64
    if fault == 'missing_node': executed['before'] = []
    elif fault == 'missing_target': collected['matches'] = [0]
    elif fault == 'deselected': executed['deselected'] = 1
    elif fault == 'skipped': executed['phases'][node]['call'] = ['skipped']
    elif fault == 'failed': executed['phases'][node]['teardown'] = ['failed']
    elif fault == 'duplicate_phase': executed['phases'][node]['call'] = ['passed', 'passed']
    elif fault == 'bad_junit_node': junit = junit.replace(node.encode(), b'b' * 64)
    elif fault == 'missing_junit_property': junit = b'<testsuites><testcase/></testsuites>'
    with pytest.raises(ValueError, match='selection'):
        export._selection_evidence(collected, executed, junit, 1)


@pytest.mark.parametrize('targets', [[], ['tests/test_a.py'], ['tests/test_a.py::'],
                                     ['tests/../other.py::test_a'], ['tests/test_a.py::test_a\n-k bad']])
def test_only_explicit_safe_test_nodes_are_accepted(targets):
    with pytest.raises(ValueError):
        export._validate_targets(targets)


@pytest.mark.parametrize('bad_option', ['-k', '-m'])
def test_pytest_keyword_and_marker_filtering_refused(tmp_path, bad_option):
    target = _synthetic_parameterized_suite(tmp_path)
    result, _, _ = _selection_phase(tmp_path, 'collect', target, bad_option, 'one')
    assert result.returncode != 0


def test_project_addopts_keyword_filtering_refused(tmp_path):
    target = _synthetic_parameterized_suite(tmp_path)
    (tmp_path / 'pytest.ini').write_text('[pytest]\naddopts = -k one\n', encoding='utf-8')
    result, _, _ = _selection_phase(tmp_path, 'collect', target)
    assert result.returncode != 0


def test_execution_phase_rejects_collect_only(tmp_path):
    target = _synthetic_parameterized_suite(tmp_path)
    result, _, _ = _selection_phase(tmp_path, 'execute', target, '--collect-only')
    assert result.returncode != 0


def test_execution_refuses_without_any_windows_job(monkeypatch):
    monkeypatch.setattr(export, '_inside_windows_job', lambda: False)
    with pytest.raises(ValueError, match='job_membership_required'):
        export._run({})


def test_wrong_d15_pin_refuses_before_creating_export(tmp_path, monkeypatch):
    monkeypatch.setattr(export, '_inside_windows_job', lambda: True)
    audit = tmp_path / '.codex-audit'
    work = audit / 'work'
    work.mkdir(parents=True)
    fake_git = tmp_path / 'git.exe'
    fake_git.write_bytes(b'synthetic executable')
    hook = tmp_path / 'hook.py'
    hook.write_bytes(b'synthetic wrong hook')
    config = {'git': str(fake_git), 'git_sha256': hashlib.sha256(fake_git.read_bytes()).hexdigest(),
              'repo': str(tmp_path), 'workspace': str(tmp_path), 'commit': 'a' * 40, 'work': str(work), 'hook': str(hook),
              'python_sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
              'targets': ['tests/synthetic.py']}
    with pytest.raises(ValueError, match='hook_pin'):
        export._run(config)
    assert not (work / 'export').exists()


def test_powershell_includes_unmodified_owned_job_and_bounds():
    content = (Path(__file__).resolve().parents[2] / 'tools' / 'Invoke-WdVerifiedTestExport.ps1').read_bytes()
    marker = b'# The preceding helper is byte-identical'
    prefix, sep, _ = content.partition(marker)
    assert sep
    # Git may normalize line endings; compare canonicalized helper bytes.
    original_lf_sha = '411307227939a2e6255730bc2f1b0261f591dddfc5590bde8d858361c7269113'
    assert hashlib.sha256(prefix.rstrip(b'\r\n').replace(b'\r\n', b'\n') + b'\n').hexdigest() == original_lf_sha
    assert b'Invoke-WdQ4ProcessJob -Executable' in content
    assert b'-TimeoutSeconds $TimeoutSeconds -MaxOutputBytes $MaxOutputBytes -DetailedOutcome' in content
    assert b"$result.Cleanup -cne 'confirmed'" in content
    assert b'wd.test-export-intermediate.v1' in content
    assert b'wd.verified-test-export-job-result.v1' in content
    assert b"same_user_path_race='UNKNOWN'" in content


@pytest.mark.skipif(sys.platform != 'win32', reason='PowerShell boundary guard is Windows-specific')
def test_powershell_refuses_unpinned_executables_before_creating_any_workspace(tmp_path):
    powershell = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = Path(__file__).resolve().parents[2] / 'tools' / 'Invoke-WdVerifiedTestExport.ps1'
    workspace = tmp_path / 'must-not-be-created'
    quoted_script = str(script).replace("'", "''")
    quoted_workspace = str(workspace).replace("'", "''")
    command = (f". '{quoted_script}'; try {{ Invoke-WdVerifiedTestExport "
               f"-Workspace '{quoted_workspace}' -Repository '{quoted_workspace}' "
               "-Commit ('a'*40) -GitExecutable 'C:\\fake.exe' -GitSha256 ('b'*64) "
               "-PythonExecutable 'C:\\fake.exe' -PythonSha256 ('c'*64) "
               "-RunnerSha256 ('d'*64) -D15HookPath 'C:\\fake.py' -Targets 'tests/a.py' | Out-Null; exit 7 "
               "} catch { exit 0 }")
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, timeout=30)
    assert result.returncode == 0
    assert not workspace.exists()


@pytest.mark.skipif(sys.platform != 'win32', reason='Owned Job Object is Windows-specific')
def test_owned_job_timeout_and_output_cap_leave_confirmed_cleanup(tmp_path):
    powershell = Path(os.environ['SYSTEMROOT']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    script = Path(__file__).resolve().parents[2] / 'tools' / 'Invoke-WdVerifiedTestExport.ps1'
    work, scratch = tmp_path / 'work', tmp_path / 'scratch'
    work.mkdir()
    scratch.mkdir()
    def quote(path): return str(path).replace("'", "''")
    command = (f". '{quote(script)}'; "
               f"$a=Invoke-WdQ4ProcessJob -Executable '{quote(sys.executable)}' "
               f"-Arguments @('-c','import time;time.sleep(20)') -WorkingDirectory '{quote(work)}' "
               f"-ScratchRoot '{quote(scratch)}' -InputBytes ([byte[]]@()) -TimeoutSeconds 1 -MaxOutputBytes 2048 -DetailedOutcome; "
               f"$b=Invoke-WdQ4ProcessJob -Executable '{quote(sys.executable)}' "
               f"-Arguments @('-c','print(\"x\"*1048576)') -WorkingDirectory '{quote(work)}' "
               f"-ScratchRoot '{quote(scratch)}' -InputBytes ([byte[]]@()) -TimeoutSeconds 20 -MaxOutputBytes 512 -DetailedOutcome; "
               "@($a,$b) | Select-Object Outcome,Cleanup,ExitCode | ConvertTo-Json -Compress")
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', command],
                            capture_output=True, text=True, timeout=40, check=True)
    values = __import__('json').loads(result.stdout)
    assert values[0]['Outcome'] == 'timeout' and values[0]['Cleanup'] == 'confirmed'
    assert values[1]['Outcome'] == 'refused' and values[1]['Cleanup'] == 'confirmed'
    assert values[0]['ExitCode'] is None and values[1]['ExitCode'] is None
