"""Synthetic manifest and helper bytes only; no helper execution or live bundle."""
import hashlib
import json
import os

import pytest

from tools import wd_q4_secure_transport as fixed

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows bundle paths')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def staged(tmp_path):
    root = tmp_path/'bundle'
    tools = root/'tools-bootstrap'/'tools'
    tools.mkdir(parents=True)
    peer = tools/'wd_q4_secure_transport.py'
    helper = tools/'wd_q4_staged_fixed_backend.py'
    wrapper = root/'Invoke-WdQ4Query.ps1'
    peer.write_bytes(b'synthetic peer')
    helper.write_bytes(b'synthetic backend; never execute')
    wrapper.write_bytes(b'synthetic wrapper; never execute')
    manifest = {'schema_version':1,'source_commit':'a'*40,'files':{
        'tools-bootstrap/tools/wd_q4_secure_transport.py':sha(peer.read_bytes()),
        'tools-bootstrap/tools/wd_q4_staged_fixed_backend.py':sha(helper.read_bytes()),
        'Invoke-WdQ4Query.ps1':sha(wrapper.read_bytes())}}
    raw = json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    (root/'deployment-manifest.json').write_bytes(raw)
    plan = {'bundle_root':str(root),'bundle_commit':'a'*40,'deployment_manifest_sha256':sha(raw),
            'peer_code_sha256':sha(peer.read_bytes()),'backend_sha256':sha(helper.read_bytes()),
            'wrapper_path':str(wrapper),'wrapper_sha256':sha(wrapper.read_bytes())}
    return root, peer, helper, plan


def test_pinned_backend_is_read_but_not_executed(staged):
    _, peer, helper, plan = staged
    assert fixed._read_staged_backend(str(peer), plan) == helper.read_bytes()


@pytest.mark.parametrize('damage', ['helper','peer','manifest','wrapper','backend_pin','foreign_peer'])
def test_staged_backend_refuses_unpinned_or_foreign_source(staged,tmp_path,damage):
    root, peer, helper, plan = staged
    if damage in ('helper','peer','manifest','wrapper'):
        paths = {'helper':helper,'peer':peer,'manifest':root/'deployment-manifest.json',
                 'wrapper':root/'Invoke-WdQ4Query.ps1'}
        with paths[damage].open('ab') as stream:
            stream.write(b'changed')
    elif damage == 'backend_pin':
        plan['backend_sha256'] = '0'*64
    else:
        peer = tmp_path/'wd_q4_secure_transport.py'
        peer.write_bytes(b'synthetic peer')
    with pytest.raises(fixed._Refused):
        fixed._read_staged_backend(str(peer), plan)
