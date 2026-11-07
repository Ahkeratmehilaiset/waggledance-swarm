"""Synthetic isolated FAISS origin checks; no live bridge, query or model data."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT=Path(__file__).resolve().parents[2]


def _runtime():
    value=os.environ.get('Q4_SYNTHETIC_FAISS_RUNTIME_ROOT','')
    if not value:
        pytest.skip('explicit synthetic runtime with FAISS required')
    root=Path(value); manifest=root.parent/'runtime-manifest.json'
    if not (root/'site'/'faiss').is_dir() or not manifest.is_file():
        pytest.skip('explicit synthetic FAISS runtime or manifest unavailable')
    return root,manifest


@pytest.mark.skipif(os.name!='nt',reason='native image ownership check requires Windows')
@pytest.mark.parametrize('scenario',('extra_attribute','context','destructor','pointer_in_image'))
def test_real_manifested_swig_capsule_and_adversarial_replacements(scenario):
    runtime,manifest=_runtime()
    code=r'''import ctypes as C,hashlib,json,os,sys,types
from pathlib import Path
repo,root,manifest=map(Path,sys.argv[1:4]); scenario=sys.argv[4]
sys.path[:0]=[str(repo),str(root/'site')]
from tools import wd_q4_secure_transport as t
def refused(fn):
 try: fn()
 except t._Refused: return
 raise AssertionError('failure must refuse')
def preloaded(name):
 sys.modules[name]=types.ModuleType(name)
 try: refused(t._reject_preloaded_faiss)
 finally: del sys.modules[name]
preloaded('faiss');preloaded('faiss.fake');preloaded('swig_runtime_data4')
# A name-correct injected module and named capsule must still be rejected
# before the trusted FAISS import, regardless of its chosen memory address.
new=C.pythonapi.PyCapsule_New;new.argtypes=[C.c_void_p,C.c_char_p,C.c_void_p];new.restype=C.py_object
fake_memory=C.create_string_buffer(32);fake_name=b'swig_runtime_data4.type_pointer_capsule'
fake=types.ModuleType('swig_runtime_data4');fake.type_pointer_capsule=new(C.addressof(fake_memory),fake_name,None)
sys.modules['swig_runtime_data4']=fake
try: refused(t._reject_preloaded_faiss)
finally: del sys.modules['swig_runtime_data4']
t._reject_preloaded_faiss();before=set(sys.modules)
import faiss
module=sys.modules['swig_runtime_data4']; capsule=module.type_pointer_capsule
pin=hashlib.sha256(manifest.read_bytes()).hexdigest()
ctx=t._FaissSwigOrigin(root,manifest,pin)
roots=[os.path.normcase(os.path.realpath(repo)),os.path.normcase(os.path.realpath(root/'site'))]+t._interpreter_roots()
t._module_origins(before,roots,repo,swig_origin=ctx);ctx.recheck()
assert ctx.module is module and ctx.capsule is capsule
# Same module name and genuine capsule, different module object: must refuse.
fake=types.ModuleType('swig_runtime_data4');fake.type_pointer_capsule=capsule
sys.modules['swig_runtime_data4']=fake
try: refused(lambda:ctx.accept(fake))
finally: sys.modules['swig_runtime_data4']=module
# Same module and correct capsule name, but replacement object: must refuse.
getptr=C.pythonapi.PyCapsule_GetPointer;getptr.argtypes=[C.py_object,C.c_char_p];getptr.restype=C.c_void_p
new=C.pythonapi.PyCapsule_New;new.argtypes=[C.c_void_p,C.c_char_p,C.c_void_p];new.restype=C.py_object
name=b'swig_runtime_data4.type_pointer_capsule'; address=getptr(capsule,name)
replacement=new(address,name,None);module.type_pointer_capsule=replacement
try: refused(ctx.recheck)
finally: module.type_pointer_capsule=capsule
ctx.recheck()
# Mutations are restored before any FAISS use or process shutdown.
if scenario=='extra_attribute':
 module.unexpected='synthetic'
 try: refused(ctx.recheck)
 finally: del module.unexpected
elif scenario=='context':
 setctx=C.pythonapi.PyCapsule_SetContext;setctx.argtypes=[C.py_object,C.c_void_p];setctx.restype=C.c_int
 assert setctx(capsule,C.c_void_p(1))==0
 try: refused(ctx.recheck)
 finally: assert setctx(capsule,None)==0
elif scenario=='destructor':
 getd=C.pythonapi.PyCapsule_GetDestructor;getd.argtypes=[C.py_object];getd.restype=C.c_void_p
 setd=C.pythonapi.PyCapsule_SetDestructor;setd.argtypes=[C.py_object,C.c_void_p];setd.restype=C.c_int
 destructor=getd(capsule);assert destructor and setd(capsule,None)==0
 try: refused(ctx.recheck)
 finally: assert setd(capsule,C.c_void_p(destructor))==0
elif scenario=='pointer_in_image':
 setptr=C.pythonapi.PyCapsule_SetPointer;setptr.argtypes=[C.py_object,C.c_void_p];setptr.restype=C.c_int
 assert setptr(capsule,C.c_void_p(address+1))==0
 try: refused(ctx.recheck)
 finally: assert setptr(capsule,C.c_void_p(address))==0
ctx.recheck()
# A valid capsule over private memory cannot pass the native-image check.
array=C.create_string_buffer(32); module.type_pointer_capsule=new(C.addressof(array),name,None)
try: refused(lambda:t._FaissSwigOrigin(root,manifest,pin).accept(module))
finally: module.type_pointer_capsule=capsule
# Missing or forged manifest pin and non-capsule values fail closed.
refused(lambda:t._FaissSwigOrigin(root,manifest,'0'*64).accept(module))
module.type_pointer_capsule=object()
try: refused(ctx.recheck)
finally: module.type_pointer_capsule=capsule
# Unrelated missing-origin modules remain forbidden.
sys.modules['synthetic_unknown_origin']=types.ModuleType('synthetic_unknown_origin')
try: refused(lambda:t._module_origins(before,roots,repo,swig_origin=ctx))
finally: del sys.modules['synthetic_unknown_origin']
print('synthetic_faiss_origin_checks_passed')
'''
    run=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(ROOT),str(runtime),str(manifest),scenario],
                       cwd=ROOT,capture_output=True,timeout=90)
    assert run.returncode==0,run.stderr.decode('utf-8','replace')
    assert run.stdout.strip()==b'synthetic_faiss_origin_checks_passed'
