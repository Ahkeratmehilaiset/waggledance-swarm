"""Fixed lane-caller bootstrap using one manifest-verified Q4 runtime.

Default OFF. Enabled execution needs inherited descriptor/deployment pins and
the exact descriptor interpreter. A -S -B wrapper relays inherited handles to
this script's fixed -I -S -B child; the child repeats the complete preflight.
It validates the bundled caller's own configuration before using runtime paths,
verifies the runtime tuple and invokes only that same caller from runtime code.
It never reads stdin before caller entry, changes identity variables, selects
another module, or places a query in argv/environment.  Pins are consistency
checks, not authentication; native DLLs, stdlib, same-user races and process
tree containment remain outside this bootstrap. A post-entry failure is UNKNOWN.
"""
import sys
import os

# The -S -B wrapper relay needs no project imports. Remove inherited path
# entries before importing other stdlib modules; the isolated child will add
# only its verified runtime paths. Interpreter startup still relies on the
# existing pinned wrapper's controlled environment.
if not sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode:
    _base=os.path.normcase(os.path.abspath(sys.base_prefix))
    def _stdlib_only(value):
        try:
            if not value:return False
            full=os.path.normcase(os.path.abspath(value))
            return os.path.commonpath((_base,full))==_base and 'site-packages' not in full.lower()
        except (OSError,ValueError):return False
    sys.path[:]=[value for value in sys.path if _stdlib_only(value)]

import hashlib
import hmac
import importlib
import importlib.abc
import importlib.machinery
import io
import json
from pathlib import Path
import re
import stat
import subprocess

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_PROJECT=('tools','sandbox','core','waggledance')
_BOOT='tools-bootstrap/tools/wd_q4_lane_caller_runtime_bootstrap.py'
_CALLER='tools-bootstrap/tools/wd_q4_lane_caller_cli.py'
_VERIFIER='tools-bootstrap/sandbox/mind/step2/runtime_manifest.py'


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def unique(pairs):
        out={}
        for key,value in pairs:
            if key in out:raise ValueError()
            out[key]=value
        return out
    return json.loads(raw.decode('utf-8','strict'),object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _path(value,*,directory=False):
    if type(value) is not str or not value:raise ValueError()
    path=Path(value)
    if not path.is_absolute() or '..' in path.parts or (os.name=='nt' and path.anchor.startswith('\\\\')):raise ValueError()
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:raise ValueError()
    if not (path.is_dir() if directory else path.is_file()):raise ValueError()
    return path


def _read(value,pin,limit):
    if type(pin) is not str or not _HEX.fullmatch(pin):raise ValueError()
    path=_path(str(value));before=path.stat()
    if before.st_size>limit:raise ValueError()
    with path.open('rb') as stream:
        opened=os.fstat(stream.fileno());raw=stream.read(min(limit,max(0,before.st_size))+1);after=os.fstat(stream.fileno())
    final=path.stat()
    if (len(raw)!=before.st_size or not os.path.samestat(before,opened) or not os.path.samestat(opened,after)
            or not os.path.samestat(after,final)
            or any(info.st_size!=before.st_size or info.st_mtime_ns!=before.st_mtime_ns for info in (opened,after,final))
            or not hmac.compare_digest(_sha(raw),pin)):raise ValueError()
    return raw


def _pin(value):
    if type(value) is not str:raise ValueError()
    value=value.lower()
    if not _HEX.fullmatch(value):raise ValueError()
    return value


def _project(name):return any(name==root or name.startswith(root+'.') for root in _PROJECT)


def _inside(value,root):
    actual=os.path.normcase(os.path.abspath(os.fspath(value)))
    base=os.path.normcase(os.path.abspath(os.fspath(root)))
    try:
        if os.path.commonpath((actual,base))!=base:return False
        for part in (Path(value),*Path(value).parents):
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:return False
        return True
    except (OSError,ValueError):return False


def _check_spec(spec,code):
    if spec is None:raise ImportError('project_origin_refused')
    origin,locations=spec.origin,spec.submodule_search_locations
    if origin is None and locations is None:raise ImportError('project_origin_refused')
    if origin is not None and not _inside(origin,code):raise ImportError('project_origin_refused')
    if locations is not None and (not locations or any(not _inside(p,code) for p in locations)):
        raise ImportError('project_origin_refused')


class _Guard(importlib.abc.MetaPathFinder):
    def __init__(self,code):self.code=code
    def find_spec(self,fullname,path=None,target=None):
        if not _project(fullname):return None
        spec=importlib.machinery.PathFinder.find_spec(fullname,path,target)
        if spec is not None:_check_spec(spec,self.code)
        return spec


def _check_origins(code,files):
    """Project modules must load from runtime code with the bundle manifest bytes (RB1); nothing else may (RB3)."""
    for name,module in tuple(sys.modules.items()):
        if module is None:continue
        spec=getattr(module,'__spec__',None)
        if _project(name):
            _check_spec(spec,code)
            if spec.origin is not None:
                relative=Path(os.path.abspath(spec.origin)).relative_to(Path(os.path.abspath(code))).as_posix()
                entry=files.get('tools-bootstrap/'+relative)
                if type(entry) is not str:raise ImportError('project_bundle_refused')
                try:_read(spec.origin,_pin(entry),16*1024*1024)
                except (OSError,ValueError):raise ImportError('project_bundle_refused') from None
        elif spec is not None and type(spec.origin) is str and os.path.isabs(spec.origin) and _inside(spec.origin,code):
            raise ImportError('code_shadow_refused')


class _BoundedBytes(io.BytesIO):
    def write(self,value):
        if self.tell()+len(value)>65536:raise ValueError()
        return super().write(value)


def _relay_isolated():
    """Wrapper-compatible relay; never read stdin or alter inherited env."""
    started=False
    try:
        if not sys.flags.no_site or not sys.dont_write_bytecode:raise ValueError()
        descriptor_pin=_pin(os.environ['WD_Q4_SHARED_DESCRIPTOR_SHA256'])
        manifest_pin=_pin(os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'])
        descriptor=_json(_read(os.environ['WD_Q4_SHARED_DESCRIPTOR_PATH'],descriptor_pin,65536))
        if type(descriptor) is not dict or descriptor.get('schema')!='wd.q4-shared-session-config.v2':raise ValueError()
        bundle=_path(descriptor['bundle_root'],directory=True)
        manifest=_json(_read(bundle/'deployment-manifest.json',manifest_pin,4*1024*1024))
        if type(manifest) is not dict or manifest.get('schema_version')!=1 or type(manifest.get('files')) is not dict:
            raise ValueError()
        own=bundle.joinpath(*_BOOT.split('/'))
        if not os.path.samefile(_path(__file__),_path(str(own))):raise ValueError()
        _read(own,_pin(manifest['files'][_BOOT]),1024*1024)
        if not os.path.samefile(_path(descriptor['interpreter']),_path(sys.executable)):raise ValueError()
        _read(sys.executable,_pin(descriptor['interpreter_sha256']),64*1024*1024)
        # stdin/stdout/stderr are inherited OS handles; no query is read or
        # serialized here. The surrounding host owns the process-tree Job.
        started=True
        child=subprocess.run([sys.executable,'-I','-S','-B',str(own),'--child'],check=False)
        return child.returncode if child.returncode in (0,2,3,4,130) else 3
    except BaseException:
        sys.stderr.write('{"status":"BOOTSTRAP_UNKNOWN"}\n' if started else '{"status":"BOOTSTRAP_REFUSED"}\n')
        return 3 if started else 2


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if not args:print('{"status":"OFF"}');return 0
    if args==['--enabled'] and not sys.flags.isolated:return _relay_isolated()
    entered=False
    try:
        if args not in (['--enabled'],['--child']) or not sys.flags.isolated or not sys.flags.no_site or not sys.dont_write_bytecode:
            raise ValueError()
        if any(_project(name) or name=='numpy' or name.startswith('numpy.') or name=='faiss' or name.startswith('faiss.')
               or name=='torch' or name.startswith('torch.') for name in sys.modules):raise ValueError()
        stdlib=[os.path.normcase(os.path.abspath(p)) for p in sys.path]
        base=os.path.normcase(os.path.abspath(sys.base_prefix))
        if any(not p or os.path.commonpath((base,p))!=base or 'site-packages' in p.lower() for p in stdlib):raise ValueError()
        if tuple(sys.meta_path)!=(importlib.machinery.BuiltinImporter,importlib.machinery.FrozenImporter,importlib.machinery.PathFinder):
            raise ValueError()
        descriptor_path=os.environ['WD_Q4_SHARED_DESCRIPTOR_PATH']
        descriptor_pin=_pin(os.environ['WD_Q4_SHARED_DESCRIPTOR_SHA256'])
        manifest_pin=_pin(os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'])
        descriptor_raw=_read(descriptor_path,descriptor_pin,65536);descriptor=_json(descriptor_raw)
        if type(descriptor) is not dict or descriptor.get('schema')!='wd.q4-shared-session-config.v2':raise ValueError()
        bundle=_path(descriptor['bundle_root'],directory=True)
        manifest=_json(_read(bundle/'deployment-manifest.json',manifest_pin,4*1024*1024))
        if type(manifest) is not dict or manifest.get('schema_version')!=1 or type(manifest.get('files')) is not dict:
            raise ValueError()
        files=manifest['files'];own_path=bundle.joinpath(*_BOOT.split('/'))
        if not os.path.samefile(_path(__file__),_path(str(own_path))):raise ValueError()
        _read(own_path,_pin(files[_BOOT]),1024*1024)
        caller_path=bundle.joinpath(*_CALLER.split('/'));caller_pin=_pin(files[_CALLER])
        caller_raw=_read(caller_path,caller_pin,1024*1024)
        # This pinned caller performs its own full descriptor/fleet/binding/
        # interpreter preflight, without entering main or touching stdin.
        preflight={'__name__':'_wd_lane_configuration_preflight','__file__':str(caller_path)}
        exec(compile(caller_raw,'<wd-pinned-lane-caller>','exec'),preflight)
        verified,verified_raw,verified_pin,_,_,_=preflight['_configuration']()
        if verified!=descriptor or verified_raw!=descriptor_raw or verified_pin!=descriptor_pin:raise ValueError()
        if not os.path.samefile(_path(descriptor['interpreter']),_path(sys.executable)):raise ValueError()
        _read(sys.executable,_pin(descriptor['interpreter_sha256']),64*1024*1024)
        fixed=_json(_read(descriptor['fixed_plan_path'],_pin(descriptor['fixed_plan_sha256']),65536))
        if (type(fixed) is not dict or fixed['interpreter_sha256']!=descriptor['interpreter_sha256']
                or fixed['runtime_manifest_sha256']!=descriptor['runtime_manifest_sha256']
                or fixed['runtime_manifest_path']!=descriptor['runtime_manifest_path']):raise ValueError()
        verifier_path=bundle.joinpath(*_VERIFIER.split('/'))
        verifier_pin=_pin(files[_VERIFIER])
        if verifier_pin!=_pin(fixed['verifier_sha256']):raise ValueError()
        verifier_raw=_read(verifier_path,verifier_pin,1024*1024)
        scope={'__name__':'_wd_lane_runtime_verifier'}
        exec(compile(verifier_raw,'<wd-pinned-runtime-verifier>','exec'),scope)
        root=_path(fixed['runtime_root'],directory=True)
        runtime_manifest_path=_path(fixed['runtime_manifest_path'])
        runtime_pin=_pin(fixed['runtime_manifest_sha256'])
        runtime_manifest_raw=_read(runtime_manifest_path,runtime_pin,8*1024*1024)
        scope['verify_runtime'](root,runtime_manifest_path,runtime_pin,
            expected_generation=fixed['runtime_generation'],expected_revision=fixed['runtime_revision'])
        runtime_manifest=_json(runtime_manifest_raw)
        entries=[r for r in runtime_manifest['files'] if type(r) is dict and r.get('path')=='assets/code/tools/wd_q4_lane_caller_cli.py']
        if len(entries)!=1 or _pin(entries[0]['sha256'])!=caller_pin:raise ValueError()
        code=_path(str(root/'assets/code'),directory=True);site=_path(str(root/'site'),directory=True)
        runtime_caller=code/'tools/wd_q4_lane_caller_cli.py'
        if _read(runtime_caller,caller_pin,1024*1024)!=caller_raw:raise ValueError()
        # The base path list comes exclusively from isolated Python; no inherited
        # PYTHONPATH or caller-controlled directory is added. Code follows the stdlib (RB3).
        sys.path[:]=[*sys.path,str(code),str(site)]
        expected_path=tuple(sys.path);guard=_Guard(code);sys.meta_path.insert(0,guard)
        caller=importlib.import_module('tools.wd_q4_lane_caller_cli')
        if not os.path.samefile(_path(caller.__file__),runtime_caller):raise ValueError()
        _check_origins(code,files)
        if tuple(sys.path)!=expected_path or sys.meta_path[0] is not guard:raise ValueError()
        output=_BoundedBytes();wrapped=io.TextIOWrapper(output,encoding='utf-8',write_through=True)
        prior=sys.stdout
        try:
            sys.stdout=wrapped;entered=True
            result=caller.main(['--enabled'])
            wrapped.flush()
            if (type(result) is not int or result not in (0,2,3,4,130)
                    or tuple(sys.path)!=expected_path or sys.meta_path[0] is not guard):raise ValueError()
            _check_origins(code,files)
            data=output.getvalue()
        finally:
            sys.stdout=prior;wrapped.detach()
        if result==0:sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
        return result
    except (KeyboardInterrupt,SystemExit,GeneratorExit):
        sys.stderr.write('{"status":"BOOTSTRAP_UNKNOWN"}\n' if entered else '{"status":"BOOTSTRAP_INTERRUPTED"}\n')
        return 3 if entered else 130
    except BaseException:
        sys.stderr.write('{"status":"BOOTSTRAP_UNKNOWN"}\n' if entered else '{"status":"BOOTSTRAP_REFUSED"}\n')
        return 3 if entered else 2


if __name__=='__main__':raise SystemExit(main())
