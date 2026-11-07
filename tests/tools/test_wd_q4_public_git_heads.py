"""Synthetic local repositories; no network, real bridge or public-origin claim."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import wd_q4_public_git_heads as heads


@pytest.fixture
def repository(tmp_path):
    git = shutil.which('git')
    if git is None:
        pytest.skip('Git is required for the local synthetic repository')
    path = tmp_path / 'synthetic-repo'
    path.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
               GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid',
               GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    def run(*args):
        return subprocess.run([git, '-C', str(path), *args], env=env, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()
    run('init', '-q')
    commits = []
    for value in ('one', 'two', 'three'):
        (path / 'synthetic.txt').write_text(value, encoding='ascii')
        run('add', 'synthetic.txt')
        run('commit', '-q', '-m', value)
        commits.append(run('rev-parse', 'HEAD'))
    return str(Path(git).resolve()), path, commits


def test_default_off_never_inspects_inputs():
    class NoTouch:
        def __getattribute__(self, name):
            raise AssertionError('OFF accessed input')
    assert heads.observe(git_executable=NoTouch(), repository=NoTouch(), anchors=NoTouch()) == {'status': 'OFF'}


def test_exact_approved_anchor_local_ancestors_are_deterministic(repository):
    git, path, commits = repository
    args = dict(enabled=True, git_executable=git, repository=str(path),
                anchors={'refs/heads/public-main': commits[-1]}, max_heads=16)
    result = heads.observe(**args)
    assert result == heads.observe(**args)
    assert result['heads'] == sorted(commits)
    assert result['status'] == 'OBSERVED_LOCAL_PINNED_ANCESTRY'
    assert result['public_origin_authentication'] == 'UNKNOWN'
    assert result['completeness'] == 'RECENT_WINDOW_ONLY'
    canonical = json.dumps({k: v for k, v in result.items() if k != 'sha256'},
                           sort_keys=True, separators=(',', ':')).encode('ascii')
    assert result['sha256'] == hashlib.sha256(canonical).hexdigest()


def test_recent_window_is_bounded_and_keeps_anchor(repository):
    git, path, commits = repository
    result = heads.observe(enabled=True, git_executable=git, repository=str(path),
                           anchors={'refs/heads/public-main': commits[-1]}, max_heads=2)
    assert result['heads'] == sorted(commits[-2:])


@pytest.mark.parametrize('anchor', ['', '0' * 40, '-n1', 'A' * 40, 'f' * 64])
def test_invalid_or_missing_anchor_refuses(repository, anchor):
    git, path, _ = repository
    with pytest.raises(heads.HeadObservationRefused):
        heads.observe(enabled=True, git_executable=git, repository=str(path),
                      anchors={'refs/heads/public-main': anchor})


def test_malformed_inputs_refuse_before_git_call(monkeypatch, repository):
    git, path, commits = repository
    monkeypatch.setattr(heads.subprocess, 'run', lambda *a, **k: pytest.fail('invoked git'))
    for anchors in ({}, {'-bad': commits[-1]}, {'refs/heads/main': commits[-1], 'refs/tags/a': commits[-1]}):
        with pytest.raises(heads.HeadObservationRefused):
            heads.observe(enabled=True, git_executable=git, repository=str(path), anchors=anchors)


def test_process_failure_refuses(repository, monkeypatch):
    git, path, commits = repository
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('git', 1)
    monkeypatch.setattr(heads.subprocess, 'run', timeout)
    with pytest.raises(heads.HeadObservationRefused):
        heads.observe(enabled=True, git_executable=git, repository=str(path),
                      anchors={'refs/heads/public-main': commits[-1]})


def test_git_invocation_strips_inherited_git_options_and_denies_transports(repository, monkeypatch):
    git, path, commits = repository
    original = subprocess.run
    seen = []
    def capture(argv, **kwargs):
        seen.append((argv, kwargs['env']))
        return original(argv, **kwargs)
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'protocol.file.allow')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'always')
    monkeypatch.setattr(heads.subprocess, 'run', capture)
    heads.observe(enabled=True, git_executable=git, repository=str(path),
                  anchors={'refs/heads/public-main': commits[-1]})
    assert len(seen) == 1
    argv, environment = seen[0]
    assert '--no-replace-objects' in argv
    assert 'protocol.file.allow=never' in argv and 'protocol.https.allow=never' in argv
    assert 'GIT_CONFIG_COUNT' not in environment and environment['GIT_NO_LAZY_FETCH'] == '1'


def test_anchor_missing_from_bounded_window_refuses(repository):
    git, path, commits = repository
    with pytest.raises(heads.HeadObservationRefused, match='ancestry_invalid'):
        heads.observe(enabled=True, git_executable=git, repository=str(path), max_heads=1,
                      anchors={'refs/heads/older': commits[0], 'refs/heads/newer': commits[-1]})
