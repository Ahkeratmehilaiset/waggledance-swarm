import hashlib
import importlib
import json

import pytest


COMMIT = 'a' * 40
NAMES = ('Invoke-WdQ4SessionCommand.ps1', 'Invoke-WdQ4SessionHost.ps1',
         'WdQ4ProcessJob.ps1', 'tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py',
         'tools-bootstrap/tools/wd_q4_session_outer_entry.py')


def manifest(files=None, **extra):
    doc = {'schema_version': 1, 'source_commit': COMMIT,
           'files': files if files is not None else {name: '1' * 64 for name in NAMES}}
    doc.update(extra)
    return json.dumps(doc).encode()


def resolve(raw, *, sha=None, commit=COMMIT, enabled=True):
    mod = importlib.import_module('tools.wd_q4_manifest_host_binding')
    return mod.resolve_manifest_host_binding(raw, expected_manifest_sha256=sha or hashlib.sha256(raw).hexdigest(),
                                             expected_commit=commit, enabled=enabled)


def test_disabled_ignores_all_inputs():
    mod = importlib.import_module('tools.wd_q4_manifest_host_binding')
    assert mod.resolve_manifest_host_binding(object(), expected_manifest_sha256=object(),
                                             expected_commit=object()) is None


def test_returns_only_fixed_pins_from_authenticated_manifest():
    raw = manifest({**{name: 'AB' * 32 for name in NAMES}, 'unrelated.txt': 'c' * 64},
                   bridge_code_package={'python_executable_sha256': '2' * 64})
    assert resolve(raw) == {name: 'ab' * 32 for name in NAMES}


@pytest.mark.parametrize('raw,sha,commit', [
    (manifest(), '0' * 64, COMMIT),
    (manifest(), None, 'b' * 40),
    (manifest({name: '1' * 64 for name in NAMES[1:]}), None, COMMIT),
    (manifest({**{name: '1' * 64 for name in NAMES}, NAMES[0]: 'wrong'}), None, COMMIT),
    (manifest({**{name: '1' * 64 for name in NAMES}, '../evil': '2' * 64}), None, COMMIT),
    (manifest({**{name: '1' * 64 for name in NAMES}, 'tools-bootstrap/../evil': '2' * 64}), None, COMMIT),
    (manifest({**{name: '1' * 64 for name in NAMES}, 'tools-bootstrap\\evil': '2' * 64}), None, COMMIT),
    (manifest({**{name: '1' * 64 for name in NAMES}, NAMES[0].lower(): '2' * 64}), None, COMMIT),
    (b'{"schema_version":1,"schema_version":1,"source_commit":"' + COMMIT.encode() + b'","files":{}}', None, COMMIT),
    (b'{"schema_version":1,"source_commit":"' + COMMIT.encode() + b'","files":{"x":"1","x":"2"}}', None, COMMIT),
    (b'\xff', None, COMMIT),
    (manifest(schema_version=True), None, COMMIT),
])
def test_rejects_untrusted_or_malformed_manifest(raw, sha, commit):
    mod = importlib.import_module('tools.wd_q4_manifest_host_binding')
    with pytest.raises(mod.ManifestBindingError, match='^q4_manifest_binding_refused$'):
        resolve(raw, sha=sha, commit=commit)
