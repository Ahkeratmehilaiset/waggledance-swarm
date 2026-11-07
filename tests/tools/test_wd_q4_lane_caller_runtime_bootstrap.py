"""Synthetic isolated bootstrap only; no live sessions, model or bridge."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from sandbox.mind.step2 import runtime_builder


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


@pytest.fixture
def candidate(tmp_path):
    repo=Path(__file__).resolve().parents[2]
    bundle=tmp_path/'bundle';code=bundle/'tools-bootstrap'
    (code/'tools').mkdir(parents=True);(code/'sandbox/mind/step2').mkdir(parents=True)
    bootstrap=code/'tools/wd_q4_lane_caller_runtime_bootstrap.py'
    shutil.copyfile(repo/'tools/wd_q4_lane_caller_runtime_bootstrap.py',bootstrap)
    verifier=code/'sandbox/mind/step2/runtime_manifest.py'
    shutil.copyfile(repo/'sandbox/mind/step2/runtime_manifest.py',verifier)
    caller_bytes=(repo/'tools/wd_q4_lane_caller_cli.py').read_bytes()+b'\n'+(b'def main(argv):\n'
        b' import os,sys,synthetic_pinned_dependency\n'
        b' assert argv==["--enabled"] and synthetic_pinned_dependency.VALUE=="pinned"\n'
        b' assert os.environ["AGENT_BRIDGE_AGENT"]=="codex-lead-1"\n'
        b' assert os.environ["AGENT_BRIDGE_AGENT_UUID"]=="synthetic-uuid"\n'
        b' assert os.environ["AGENT_BRIDGE_RUN_ID"]=="synthetic-run"\n'
        b' assert os.environ["AGENT_BRIDGE_OWNER_TOKEN"]=="synthetic-token"\n'
        b' assert not any("synthetic-query-canary" in x for x in sys.argv)\n'
        b' assert not any("synthetic-query-canary" in x for x in os.environ.values())\n'
        b' assert sys.stdin.buffer.read()==b"synthetic-query-canary"\n'
        b' sys.stdout.buffer.write(b"{\\"status\\":\\"SYNTHETIC\\"}\\n");return 0\n')
    caller=code/'tools/wd_q4_lane_caller_cli.py';caller.write_bytes(caller_bytes)
    (code/'tools/__init__.py').write_bytes(b'')
    runtime=tmp_path/'runtime';(runtime/'assets/code/tools').mkdir(parents=True)
    (runtime/'assets/code/tools/__init__.py').write_bytes(b'')
    (runtime/'assets/code/tools/wd_q4_lane_caller_cli.py').write_bytes(caller_bytes)
    (runtime/'site').mkdir();(runtime/'site/synthetic_pinned_dependency.py').write_text("VALUE='pinned'",encoding='ascii')
    (runtime/'model').mkdir();(runtime/'model/synthetic.txt').write_bytes(b'synthetic')
    rt_manifest=tmp_path/'runtime-manifest.json'
    built=runtime_builder.build_runtime_manifest(runtime,rt_manifest,generation='a'*40,revision='b'*40)
    fixed={'interpreter_sha256':sha(Path(sys.executable).read_bytes()),'runtime_root':str(runtime),
           'runtime_manifest_path':str(rt_manifest),'runtime_manifest_sha256':built['manifest_sha256'],
           'runtime_generation':'a'*40,'runtime_revision':'b'*40,'verifier_sha256':sha(verifier.read_bytes())}
    fixed_path=tmp_path/'fixed.json';fixed_path.write_bytes(canonical(fixed))
    fleet=canonical({'synthetic':'fleet'});(bundle/'wd-fleet.json').write_bytes(fleet)
    binding_path=tmp_path/'binding.json';binding_path.write_bytes(b'{"synthetic":true}')
    snapshot_path=tmp_path/'snapshot.json';snapshot_path.write_bytes(b'{"synthetic":true}')
    public_plan=tmp_path/'public-plan.json';public_plan.write_bytes(b'{"synthetic":true}')
    descriptor={'schema':'wd.q4-shared-session-config.v2','bundle_root':str(bundle),
        'interpreter':sys.executable,'interpreter_sha256':fixed['interpreter_sha256'],
        'interpreter_max_bytes':64*1024*1024,'runtime_manifest_path':str(rt_manifest),
        'runtime_manifest_sha256':fixed['runtime_manifest_sha256'],'fixed_plan_path':str(fixed_path),
        'fixed_plan_sha256':sha(fixed_path.read_bytes()),'fleet_sha256':sha(fleet),'lanes':['codex-lead-1'],
        'public_plan':str(public_plan),'cwd':str(tmp_path),'binding_path':str(binding_path),
        'binding_sha256':sha(binding_path.read_bytes()),'snapshot_path':str(snapshot_path),
        'snapshot_sha256':sha(snapshot_path.read_bytes()),'timeout_seconds':270,'max_output_bytes':65536,'outer':{},
        'expected_binding':{name:'a'*64 for name in ('pointer_sha256','source_anchor_sha256','encoder_sha256',
           'policy_sha256','roster_sha256','public_policy_sha256','descriptor_sha256','projection_sha256')}}
    descriptor_path=tmp_path/'descriptor.json';descriptor_path.write_bytes(canonical(descriptor))
    manifest={'schema_version':1,'source_commit':'c'*40,'files':{
        'tools-bootstrap/tools/wd_q4_lane_caller_runtime_bootstrap.py':sha(bootstrap.read_bytes()),
        'tools-bootstrap/tools/wd_q4_lane_caller_cli.py':sha(caller_bytes),
        'tools-bootstrap/tools/__init__.py':sha(b''),
        'tools-bootstrap/sandbox/mind/step2/runtime_manifest.py':sha(verifier.read_bytes()),'wd-fleet.json':sha(fleet)}}
    manifest_path=bundle/'deployment-manifest.json';manifest_path.write_bytes(canonical(manifest))
    env=dict(os.environ,WD_REBOOT_EXPECTED_MANIFEST_HASH=sha(manifest_path.read_bytes()),
        WD_Q4_SHARED_DESCRIPTOR_PATH=str(descriptor_path),WD_Q4_SHARED_DESCRIPTOR_SHA256=sha(descriptor_path.read_bytes()),
        AGENT_BRIDGE_AGENT='codex-lead-1',AGENT_BRIDGE_AGENT_UUID='synthetic-uuid',
        AGENT_BRIDGE_RUN_ID='synthetic-run',AGENT_BRIDGE_OWNER_TOKEN='synthetic-token')
    return {'bootstrap':bootstrap,'env':env,'runtime':runtime,'descriptor':descriptor_path,
            'fixed':fixed_path,'manifest':manifest_path,'bundle_caller':caller,'cwd':tmp_path}


def execute(candidate,argv=('--enabled',),input=b'synthetic-query-canary',isolated=True):
    flags=['-I','-S','-B'] if isolated else ['-S','-B']
    return subprocess.run([sys.executable,*flags,str(candidate['bootstrap']),*argv],input=input,
        cwd=candidate['cwd'],env=candidate['env'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)


def test_default_off_without_environment_or_stdin(candidate):
    result=execute(candidate,argv=())
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'OFF'}


def test_exact_runtime_and_stdin_with_preserved_identity(candidate):
    result=execute(candidate)
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'SYNTHETIC'}
    assert b'synthetic-query-canary' not in result.stdout+result.stderr


@pytest.mark.parametrize('fault',['descriptor','manifest','fixed','runtime_site','runtime_caller','bundle_caller','unlisted_argument'])
def test_bad_pins_refuse_before_reading_stdin(candidate,fault):
    env=dict(candidate['env']);argv=['--enabled'];flags=['-I','-S','-B']
    if fault=='descriptor':candidate['descriptor'].write_bytes(b'changed')
    if fault=='manifest':env['WD_REBOOT_EXPECTED_MANIFEST_HASH']='0'*64
    if fault=='fixed':candidate['fixed'].write_bytes(b'changed')
    if fault=='runtime_site':(candidate['runtime']/'site/synthetic_pinned_dependency.py').write_bytes(b'changed')
    if fault=='runtime_caller':(candidate['runtime']/'assets/code/tools/wd_q4_lane_caller_cli.py').write_bytes(b'changed')
    if fault=='bundle_caller':candidate['bundle_caller'].write_bytes(b'changed')
    if fault=='unlisted_argument':argv=['--module','os']
    p=subprocess.Popen([sys.executable,*flags,str(candidate['bootstrap']),*argv],cwd=candidate['cwd'],env=env,
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        p.wait(timeout=20)
        stdout,stderr=p.communicate(timeout=5)
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=5)
    assert p.returncode==2 and not stdout and b'synthetic-query-canary' not in stderr


def test_wrapper_style_s_b_forwards_stdin_to_isolated_child(candidate):
    injected=candidate['cwd']/'untrusted-pythonpath';injected.mkdir()
    (injected/'synthetic_pinned_dependency.py').write_text("raise RuntimeError('must not import')",encoding='ascii')
    marker=candidate['cwd']/'untrusted-import-ran'
    (injected/'hashlib.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\nraise RuntimeError()\n',encoding='ascii')
    candidate['env']['PYTHONPATH']=str(injected)
    result=execute(candidate,isolated=False)
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'SYNTHETIC'}
    assert not marker.exists() and b'synthetic-query-canary' not in result.stdout+result.stderr


def test_wrapper_relay_child_preflight_refuses_without_consuming_stdin(candidate):
    (candidate['runtime']/'site/synthetic_pinned_dependency.py').write_bytes(b'changed')
    p=subprocess.Popen([sys.executable,'-S','-B',str(candidate['bootstrap']),'--enabled'],cwd=candidate['cwd'],
        env=candidate['env'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        p.wait(timeout=20);stdout,stderr=p.communicate(timeout=5)
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=5)
    assert p.returncode==2 and not stdout and b'synthetic-query-canary' not in stderr


def test_consistent_runtime_with_different_caller_than_bundle_refuses(candidate):
    source=candidate['runtime']/'assets/code/tools/wd_q4_lane_caller_cli.py'
    source.write_bytes(source.read_bytes()+b'\n# synthetic changed caller\n')
    descriptor=json.loads(candidate['descriptor'].read_bytes())
    fixed=json.loads(candidate['fixed'].read_bytes())
    manifest_path=Path(fixed['runtime_manifest_path']);manifest_path.unlink()
    built=runtime_builder.build_runtime_manifest(candidate['runtime'],manifest_path,generation='a'*40,revision='b'*40)
    fixed['runtime_manifest_sha256']=built['manifest_sha256'];candidate['fixed'].write_bytes(canonical(fixed))
    descriptor['runtime_manifest_sha256']=built['manifest_sha256']
    descriptor['fixed_plan_sha256']=sha(candidate['fixed'].read_bytes())
    candidate['descriptor'].write_bytes(canonical(descriptor))
    candidate['env']['WD_Q4_SHARED_DESCRIPTOR_SHA256']=sha(candidate['descriptor'].read_bytes())
    result=execute(candidate)
    assert result.returncode==2 and not result.stdout


def test_preloaded_project_module_refuses(candidate):
    code=('import sys,types,runpy;sys.modules["tools"]=types.ModuleType("tools");'
          'sys.argv=[sys.argv[1],"--enabled"];runpy.run_path(sys.argv[0],run_name="__main__")')
    p=subprocess.Popen([sys.executable,'-I','-S','-B','-c',code,str(candidate['bootstrap'])],
        cwd=candidate['cwd'],env=candidate['env'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        p.wait(timeout=20);stdout,stderr=p.communicate(timeout=5)
    finally:
        if p.poll() is None:p.kill();p.wait(timeout=5)
    assert p.returncode==2 and not stdout and b'synthetic-query-canary' not in stderr


def _rebind_caller(candidate,body):
    runtime_caller=candidate['runtime']/'assets/code/tools/wd_q4_lane_caller_cli.py'
    raw=runtime_caller.read_bytes()+b'\n'+body
    runtime_caller.write_bytes(raw);candidate['bundle_caller'].write_bytes(raw)
    fixed=json.loads(candidate['fixed'].read_bytes())
    rt_manifest=Path(fixed['runtime_manifest_path']);rt_manifest.unlink()
    built=runtime_builder.build_runtime_manifest(candidate['runtime'],rt_manifest,generation='a'*40,revision='b'*40)
    fixed['runtime_manifest_sha256']=built['manifest_sha256'];candidate['fixed'].write_bytes(canonical(fixed))
    descriptor=json.loads(candidate['descriptor'].read_bytes())
    descriptor['runtime_manifest_sha256']=built['manifest_sha256']
    descriptor['fixed_plan_sha256']=sha(candidate['fixed'].read_bytes())
    candidate['descriptor'].write_bytes(canonical(descriptor))
    candidate['env']['WD_Q4_SHARED_DESCRIPTOR_SHA256']=sha(candidate['descriptor'].read_bytes())
    manifest=json.loads(candidate['manifest'].read_bytes())
    manifest['files']['tools-bootstrap/tools/wd_q4_lane_caller_cli.py']=sha(raw)
    candidate['manifest'].write_bytes(canonical(manifest))
    candidate['env']['WD_REBOOT_EXPECTED_MANIFEST_HASH']=sha(candidate['manifest'].read_bytes())


def test_post_entry_exception_is_unknown_and_suppresses_stdout(candidate):
    _rebind_caller(candidate,b'def main(argv):\n import sys\n sys.stdin.buffer.read()\n'
                             b' sys.stdout.write("synthetic-never-publish")\n raise RuntimeError()\n')
    result=execute(candidate,isolated=False)
    assert result.returncode==3 and not result.stdout and b'synthetic-query-canary' not in result.stderr


def test_abnormal_child_exit_is_unknown(candidate):
    _rebind_caller(candidate,b'def main(argv):\n import os,sys\n sys.stdin.buffer.read()\n os._exit(9)\n')
    result=execute(candidate,isolated=False)
    assert result.returncode==3 and not result.stdout


# RB1: every imported project module must match the bundle deployment manifest bytes, not only the runtime pins.
# RB3: verified runtime code must not shadow the standard library (or site) by path order.

HELPER = 'tools/synthetic_project_helper.py'
SUCCESS_MAIN = (b'def main(argv):\n import sys\n sys.stdin.buffer.read()\n'
                b' sys.stdout.buffer.write(b"{\\"status\\":\\"SYNTHETIC\\"}\\n");return 0\n')


def test_success_main_is_valid_source():
    compile(SUCCESS_MAIN, '<success-main>', 'exec')
    compile(main_importing(b'colorsys'), '<main-importing>', 'exec')


def _install(candidate, *, caller_tail, runtime=None, bundle=None, drop=()):
    """Append caller_tail to both caller copies, write extra runtime and bundle code files, rebind every pin."""
    runtime, bundle = runtime or {}, bundle or {}
    code = candidate['bootstrap'].parents[1]
    for relative, raw in runtime.items():
        target = candidate['runtime'] / 'assets/code' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for relative, raw in bundle.items():
        target = code / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    _rebind_caller(candidate, caller_tail)
    manifest = json.loads(candidate['manifest'].read_bytes())
    for relative, raw in bundle.items():
        manifest['files']['tools-bootstrap/' + relative] = sha(raw)
    for relative in drop:
        del manifest['files']['tools-bootstrap/' + relative]
    candidate['manifest'].write_bytes(canonical(manifest))
    candidate['env']['WD_REBOOT_EXPECTED_MANIFEST_HASH'] = sha(candidate['manifest'].read_bytes())


HELPER_SOURCE = b'VALUE = "bundle"\n'


def main_importing(module, check=b''):
    """The caller imports project modules lazily inside its functions, after its own preflight; mirror that."""
    return (b'def main(argv):\n import sys\n sys.stdin.buffer.read()\n import ' + module + b' as _imported\n' + check +
            b' sys.stdout.buffer.write(b"{\\"status\\":\\"SYNTHETIC\\"}\\n");return 0\n')


def outcome(candidate):
    result = execute(candidate)
    return result.returncode, result.stdout


def test_rb1_an_imported_project_module_matching_the_bundle_runs(candidate):
    _install(candidate, caller_tail=main_importing(b'tools.synthetic_project_helper'),
             runtime={HELPER: HELPER_SOURCE}, bundle={HELPER: HELPER_SOURCE})
    assert outcome(candidate) == (0, b'{"status":"SYNTHETIC"}\n')


def test_rb1_a_changed_project_module_is_unknown_and_unpublished(candidate):
    _install(candidate, caller_tail=main_importing(b'tools.synthetic_project_helper'),
             runtime={HELPER: b'VALUE = "older runtime"\n'}, bundle={HELPER: HELPER_SOURCE})
    assert outcome(candidate) == (3, b'')


def test_rb1_a_project_module_missing_from_the_bundle_manifest_is_unknown(candidate):
    _install(candidate, caller_tail=main_importing(b'tools.synthetic_project_helper'),
             runtime={HELPER: HELPER_SOURCE}, bundle={HELPER: HELPER_SOURCE}, drop=(HELPER,))
    assert outcome(candidate) == (3, b'')


def test_rb1_a_changed_package_init_refuses_before_entry(candidate):
    _install(candidate, caller_tail=SUCCESS_MAIN, runtime={'tools/__init__.py': b'# older runtime\n'})
    assert outcome(candidate) == (2, b'')


def test_rb1_a_package_init_missing_from_the_bundle_manifest_refuses_before_entry(candidate):
    _install(candidate, caller_tail=SUCCESS_MAIN, drop=('tools/__init__.py',))
    assert outcome(candidate) == (2, b'')


def test_rb3_a_stdlib_named_module_in_runtime_code_does_not_shadow_the_stdlib(candidate):
    base = os.path.normcase(os.path.abspath(sys.base_prefix))
    check = (b' import os\n assert os.path.normcase(os.path.abspath(_imported.__file__)).startswith('
             + repr(base).encode() + b')\n')
    _install(candidate, caller_tail=main_importing(b'colorsys', check),
             runtime={'colorsys.py': b'raise RuntimeError("synthetic shadow")\n'})
    assert outcome(candidate) == (0, b'{"status":"SYNTHETIC"}\n')


def test_rb3_a_non_project_module_loaded_from_runtime_code_is_unknown(candidate):
    _install(candidate, caller_tail=main_importing(b'synthetic_code_shadow'),
             runtime={'synthetic_code_shadow.py': b'VALUE = 1\n'})
    assert outcome(candidate) == (3, b'')


def test_rb3_runtime_code_shadowing_a_site_module_is_unknown(candidate):
    _install(candidate, caller_tail=main_importing(b'synthetic_pinned_dependency'),
             runtime={'synthetic_pinned_dependency.py': b'VALUE = "shadow"\n'})
    assert outcome(candidate) == (3, b'')
