"""Bounded local ancestry from externally approved Git commit anchors.

Default OFF. The caller must independently approve each named public-ref
commit and trust the Git executable and repository. This module neither fetches
nor proves a ref was public, authenticates a publisher, establishes freshness,
or decides which sources may be ingested. A hash is an integrity binding, not
authority. Only immutable 40-hex commit anchors are passed to Git.
"""
import hashlib
import json
import os
import re
import subprocess


class HeadObservationRefused(ValueError):
    """No reliable bounded observation is available."""


_HASH = re.compile(r'[0-9a-f]{40}\Z')
_REF = re.compile(r'refs/heads/[A-Za-z0-9][A-Za-z0-9._/-]{0,190}\Z')
_MAX_HEADS = 4096
_MAX_ANCHORS = 16


def _valid_ref(ref):
    return (type(ref) is str and _REF.fullmatch(ref) is not None
            and '..' not in ref and '//' not in ref and not ref.endswith(('/', '.', '.lock'))
            and all(not part.startswith('.') and not part.endswith('.') for part in ref.split('/')))


def observe(*, enabled=False, git_executable=None, repository=None, anchors=None,
            max_heads=_MAX_HEADS, timeout_seconds=30):
    """Read at most max_heads reachable commits from fixed anchors, never mutate refs.

    The returned sorted list is a recent, incomplete window and includes each
    approved anchor or refuses. Callers must separately pin the canonical result
    and supply fresh provenance before allowing it into any decision.
    """
    if enabled is False:
        return {'status': 'OFF'}
    if (enabled is not True or type(git_executable) is not str or not os.path.isabs(git_executable)
            or type(repository) is not str or not os.path.isabs(repository)
            or type(anchors) is not dict or not 1 <= len(anchors) <= _MAX_ANCHORS
            or any(not _valid_ref(key) or type(value) is not str or _HASH.fullmatch(value) is None
                   for key, value in anchors.items())
            or type(max_heads) is not int or not 1 <= max_heads <= _MAX_HEADS
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 120):
        raise HeadObservationRefused('inputs_invalid')
    pins = dict(sorted(anchors.items()))
    # Only immutable hashes reach Git. Inherited GIT_* variables and user/system
    # config are intentionally omitted; local repository config can still apply.
    environment = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR') if name in os.environ}
    environment.update({'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
                        'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_NO_LAZY_FETCH': '1',
                        'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0'})
    denied = ('protocol.allow', 'protocol.file.allow', 'protocol.git.allow', 'protocol.ssh.allow',
              'protocol.http.allow', 'protocol.https.allow', 'protocol.ext.allow')
    config = [item for name in denied for item in ('-c', name + '=never')]
    argv = [git_executable, '--no-replace-objects', *config,
            '-c', 'core.fsmonitor=false', '-C', repository, 'rev-list', '--topo-order',
            '--max-count=' + str(max_heads), *sorted(set(pins.values())), '--']
    try:
        proc = subprocess.run(argv, env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              timeout=timeout_seconds, check=False)
        if proc.returncode != 0 or len(proc.stdout) > max_heads * 41:
            raise HeadObservationRefused('git_refused')
        text = proc.stdout.decode('ascii')
        observed = text.splitlines()
        if (not observed or len(observed) > max_heads or any(_HASH.fullmatch(item) is None for item in observed)
                or len(set(observed)) != len(observed) or not set(pins.values()).issubset(observed)):
            raise HeadObservationRefused('ancestry_invalid')
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise HeadObservationRefused('git_refused') from None
    result = {'schema': 'wd.q4-public-git-head-observation.v1',
              'status': 'OBSERVED_LOCAL_PINNED_ANCESTRY', 'anchors': pins,
              'heads': sorted(observed), 'completeness': 'RECENT_WINDOW_ONLY',
              'public_origin_authentication': 'UNKNOWN'}
    raw = json.dumps(result, sort_keys=True, separators=(',', ':')).encode('ascii')
    result['sha256'] = hashlib.sha256(raw).hexdigest()
    return result
