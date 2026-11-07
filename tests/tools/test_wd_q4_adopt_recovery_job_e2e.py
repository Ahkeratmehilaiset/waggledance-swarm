"""Windows Job through the actual recovery, isolated synthetic state only."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

from tests.tools import test_wd_q4_genesis_bootstrap as genesis_fixture
from tests.tools import test_wd_q4_continuation_adopt as adopt_fixture
from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
from tools import wd_q4_live_update_once as update
from tools import wd_q4_continuation_adopt as adopt

ROOT=Path(__file__).resolve().parents[2]
def sha(raw): return hashlib.sha256(raw).hexdigest()
def enc(obj): return json.dumps(obj,sort_keys=True,separators=(',',':')).encode()

@pytest.fixture
def prepared(tmp_path,monkeypatch,capsys):
    case=tmp_path/'case'; case.mkdir()
    synthetic=genesis_fixture.synthetic.__wrapped__(case,monkeypatch)
    kwargs,_=synthetic
    runtime=case/'runtime'; (runtime/'assets/code').mkdir(parents=True); (runtime/'site').mkdir()
    (runtime/'assets/code/dummy.txt').write_text('synthetic')
    import numpy, faiss
    source_site=Path(numpy.__file__).parent.parent
    for name in ('numpy','numpy.libs','faiss','faiss_cpu.libs','packaging'):
        shutil.copytree(source_site/name,runtime/'site'/name,
                        ignore=shutil.ignore_patterns('__pycache__','*.pyc','tests','test'))
    model=runtime/'model'; shutil.move(kwargs['trusted_model_root'],model)
    kwargs['trusted_model_root']=str(model); kwargs['plan_template']['encoder']['model_dir']=str(model)
    original=update._encode
    def bundle_manifest(value):
        if isinstance(value,dict) and value.get('schema_version')==1 and 'wd-fleet.json' in value.get('files',{}):
            bundle=case/'compose-case/reboot-bundles'/('a'*40); code=bundle/'tools-bootstrap'
            import tools.wd_q4_continuation_adopt_recovery
            names=set()
            for module in list(sys.modules.values()):
                filename=getattr(module,'__file__',None)
                if filename is None: continue
                try: relative=Path(filename).resolve().relative_to(ROOT).as_posix()
                except ValueError: continue
                if relative.endswith('.py') and relative.split('/')[0] in ('tools','sandbox','waggledance','core'):
                    names.add(relative)
            names.update(('tools/wd_q4_continuation_adopt_recovery_cli.py',
                          'tools/wd_q4_continuation_adopt_recovery_bootstrap.py',
                          'tools/wd_q4_live_update_bootstrap.py',
                          'tools/wd_q4_continuation_runtime_bootstrap.py',
                          'sandbox/mind/step2/runtime_manifest.py'))
            files=dict(value['files'])
            for name in names:
                if not name.endswith('.py'): continue
                src=ROOT/name
                if not src.is_file(): continue
                dest=code/name; dest.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(src,dest)
                files['tools-bootstrap/'+name]=sha(dest.read_bytes()).upper()
            for name in ('WdQ4ProcessJob.ps1','Invoke-WdQ4ContinuationWorker.ps1','Invoke-WdQ4AdoptRecovery.ps1'):
                src=ROOT/'ops/windows/reboot'/name; shutil.copyfile(src,bundle/name)
                files[name]=sha((bundle/name).read_bytes()).upper()
            value={**value,'files':files}
        return original(value)
    monkeypatch.setattr(update,'_encode',bundle_manifest)
    args=adopt_fixture.actual_continuation.__wrapped__(synthetic,monkeypatch,case,capsys)
    return case,runtime,args


def launch(case,runtime,args):
    from sandbox.mind.step2 import runtime_builder
    bundle=Path(json.loads(Path(args['baseline_path']).read_bytes())['bundle_root'])
    verifier=bundle/'tools-bootstrap/sandbox/mind/step2/runtime_manifest.py'
    manifest=bundle/'deployment-manifest.json'; pin=sha(manifest.read_bytes())
    runtime_manifest=case/'runtime-manifest.json'
    built=runtime_builder.build_runtime_manifest(runtime,runtime_manifest,generation='a'*40,revision='b'*40)
    root=case/'job-inputs'; root.mkdir()
    tup=root/'tuple.json'; tup.write_bytes(args['genesis_tuple_bytes'])
    receipt=root/'receipt.json'; receipt.write_bytes(args['receipt_bytes'])
    config={'schema':'wd.q4-adopt-recovery-cli.v1','bundle_root':str(bundle),'manifest_sha256':pin,
            'baseline_path':args['baseline_path'],'baseline_sha256':args['baseline_sha256'],
            'tuple_path':str(tup),'tuple_sha256':sha(tup.read_bytes()),
            'receipt_path':str(receipt),'receipt_sha256':sha(receipt.read_bytes())}
    cfg=root/'config.json'; cfg.write_bytes(enc(config))
    spec={'schema':'wd.q4-genesis-runtime.v1','bundle_root':str(bundle),'manifest_sha256':pin,
          'interpreter_path':sys.executable,'interpreter_sha256':sha(Path(sys.executable).read_bytes()),
          'runtime_root':str(runtime),'runtime_manifest_path':str(runtime_manifest),
          'runtime_manifest_sha256':built['manifest_sha256'],'runtime_generation':'a'*40,
          'runtime_revision':'b'*40,'verifier_sha256':sha(verifier.read_bytes())}
    spec_path=root/'runtime.json'; spec_path.write_bytes(enc(spec))
    scratch=case/'scratch'; scratch.mkdir()
    host=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    script=bundle/'Invoke-WdQ4AdoptRecovery.ps1'
    cmd=['powershell','-NoProfile','-NonInteractive','-File',str(script),'-Run',
         '-BundleRoot',str(bundle),'-ExpectedCommit','a'*40,'-ExpectedManifestSha256',pin,
         '-ConfigPath',str(cfg),'-ExpectedConfigSha256',sha(cfg.read_bytes()),
         '-RuntimePath',str(spec_path),'-ExpectedRuntimeSha256',sha(spec_path.read_bytes()),
         '-ScratchRoot',str(scratch),'-ExpectedHostSha256',sha(host.read_bytes()),'-TimeoutSeconds','120']
    return subprocess.run(cmd,text=True,capture_output=True,timeout=140)


@pytest.mark.parametrize('mode',['before','after','partial'])
def test_job_reconciles_only_proven_adoptions(prepared,monkeypatch,mode):
    case,runtime,args=prepared; private,generation=paths(args)
    original=adopt.update._publish
    def interrupt(*a,**kw):
        if mode=='after': original(*a,**kw)
        if mode=='partial': generation.mkdir(); (generation/'unowned').write_bytes(b'keep')
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',interrupt)
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    marker=private/'q4-continuation-adopt.pending.json'; marker_bytes=marker.read_bytes()
    result=launch(case,runtime,args)
    expected={'before':'NOT_ADOPTED','after':'ADOPTED','partial':'UNKNOWN'}[mode]
    assert result.returncode==(3 if mode=='partial' else 0), result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':expected}
    if mode=='partial': assert marker.read_bytes()==marker_bytes and (generation/'unowned').read_bytes()==b'keep'
    else: assert not marker.exists()
