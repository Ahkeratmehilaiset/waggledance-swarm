"""Synthetic bridge package: imports must not require the optional Q4 site."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
DEFINITION=ROOT/'ops/windows/reboot/bridge-code-files.json'
SCRIPT=r'''
import importlib, importlib.abc, json, os, sys
code, site, modules_json = sys.argv[1:]
sys.path[:0]=[code,site]
attempts=[]
class Forbidden(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'faiss','numpy','torch','sentence_transformers'}:
            attempts.append(fullname)
            raise ImportError('forbidden_optional_q4_import')
sys.meta_path.insert(0,Forbidden())
try:
    importlib.import_module('numpy')
except ImportError:
    pass
else:
    raise SystemExit('guard did not intercept optional Q4 dependency')
if attempts != ['numpy']:
    raise SystemExit('guard interception not observable')
attempts.clear()
forbidden={'faiss','numpy','torch','sentence_transformers'}
if any(name.partition('.')[0] in forbidden for name in sys.modules):
    raise SystemExit('optional Q4 module preloaded')
def audit(event,args):
    if event in {'subprocess.Popen','os.system','os.remove','os.rmdir','os.rename','os.mkdir',
                 'socket.connect','socket.bind','socket.getaddrinfo'}:
        raise PermissionError('synthetic_smoke_side_effect')
    if event=='open' and len(args)>=3:
        mode,flags=args[1],args[2]
        if (isinstance(mode,str) and any(c in mode for c in 'wax+')) or (
            isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)):
            raise PermissionError('synthetic_smoke_write')
sys.addaudithook(audit)
errors=[]
for name in json.loads(modules_json):
    try:
        module=importlib.import_module(name)
        origin=getattr(module,'__file__',None)
        if not origin or os.path.commonpath((os.path.realpath(code),os.path.realpath(origin)))!=os.path.realpath(code):
            errors.append([name,'UnexpectedOrigin','module not from synthetic package'])
    except Exception as e:
        errors.append([name,type(e).__name__,str(e)])
loaded=sorted(name for name in sys.modules if name.partition('.')[0] in forbidden)
print(json.dumps({'errors':errors,'forbidden_attempts':attempts,'forbidden_loaded':loaded},sort_keys=True))
raise SystemExit(bool(errors or attempts or loaded))
'''


def _sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def synthetic_bundle(tmp_path):
    site_env=os.environ.get('WD_BRIDGE_PYTHON_SITE')
    expected=os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH','').lower()
    if not site_env or len(expected)!=64:
        pytest.skip('requires separately manifest-pinned lightweight bridge site')
    source_site=Path(site_env).resolve()
    bundle=source_site.parent.parent
    manifest_bytes=(bundle/'deployment-manifest.json').read_bytes()
    assert _sha(manifest_bytes)==expected
    manifest=json.loads(manifest_bytes)
    definition=json.loads(DEFINITION.read_text(encoding='utf-8'))
    code=tmp_path/'tools-bootstrap'; site=code/'python-site'
    for relative in definition['python_files']:
        target=code/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/relative,target)
    prefix='tools-bootstrap/python-site/'
    entries={k[len(prefix):]:v for k,v in manifest['files'].items() if k.startswith(prefix)}
    assert entries, 'manifest contains no pinned lightweight bridge site'
    for relative,digest in entries.items():
        source=source_site/relative
        data=source.read_bytes()
        assert _sha(data)==digest.lower(), relative
        target=site/relative; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
    modules=set(definition['import_smoke']['package_modules'])
    modules.update(path[:-3].replace('/','.') for path in definition['python_entrypoints'].values())
    return code,site,sorted(modules)


def _run(code,site,modules):
    env={key:value for key,value in os.environ.items() if key.upper() in {'SYSTEMROOT','WINDIR','PATH'}}
    env.update(TEMP=str(code.parent),TMP=str(code.parent),PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable,'-I','-S','-B','-c',SCRIPT,str(code),str(site),json.dumps(modules)],
                          cwd=code.parent,env=env,capture_output=True,text=True,timeout=90)


def test_import_smoke_and_entrypoints_do_not_require_optional_q4_site(synthetic_bundle):
    result=_run(*synthetic_bundle)
    assert result.returncode==0, result.stdout+'\n'+result.stderr


def test_forbidden_eager_import_is_detected_even_if_caught(synthetic_bundle):
    code,site,modules=synthetic_bundle
    mutant=code/'tools'/'synthetic_q4_eager.py'
    mutant.write_text('try:\n import numpy\nexcept ImportError:\n pass\n',encoding='utf-8')
    result=_run(code,site,['tools.synthetic_q4_eager'])
    assert result.returncode!=0
    assert json.loads(result.stdout)['forbidden_attempts']==['numpy']


def test_forbidden_sys_modules_injection_is_detected(synthetic_bundle):
    code,site,modules=synthetic_bundle
    mutant=code/'tools'/'synthetic_q4_eager.py'
    mutant.write_text('import sys, types\nsys.modules["numpy"]=types.ModuleType("numpy")\n',encoding='utf-8')
    result=_run(code,site,['tools.synthetic_q4_eager'])
    assert result.returncode!=0
    assert json.loads(result.stdout)['forbidden_loaded']==['numpy']
