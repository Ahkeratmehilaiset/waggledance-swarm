"""Default-off local wheel/installed-site consistency verifier.

Inputs and pins must be independently anchored by the caller. Matching local
wheel bytes, metadata, RECORD and physical files does not authenticate their
publisher, establish installation history, prove dependency closure, prevent
same-user races or authorize production use. No package code is imported.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import zipfile

from sandbox.mind.step2 import runtime_manifest as runtime
from tools import build_q4_runtime_plan as plan
from tools import q4_offline_site_candidate as offline
from tools import q4_wheel_archive_check as wheelcheck

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_TARGET='cp313-cp313-win_amd64'
_MAX_PINS=16*1024*1024


def _pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result: raise ValueError()
        result[key]=value
    return result


def _pins(raw,digest):
    if (type(raw) is not bytes or len(raw)>_MAX_PINS or type(digest) is not str
            or _HEX.fullmatch(digest) is None or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(),digest)):
        raise ValueError()
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=_pairs,
                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict or len(value)>runtime.MAX_FILES: raise ValueError()
    seen=set()
    for path,pin in value.items():
        wheelcheck._safe_name(path)
        if (path.casefold() in seen or type(pin) is not dict or set(pin)!={'size','sha256'}
                or type(pin['size']) is not int or pin['size']<0
                or type(pin['sha256']) is not str or _HEX.fullmatch(pin['sha256']) is None):
            raise ValueError()
        seen.add(path.casefold())
    return value


def _mapped(path,info_root,distribution):
    parts=path.split('/')
    if not parts[0].endswith('.data'):
        return 'site',path
    if parts[0]!=info_root[:-len('.dist-info')]+'.data' or len(parts)<3: raise ValueError()
    scheme=parts[1]
    if scheme in ('purelib','platlib'):
        target='/'.join(parts[2:]); root='site'
    elif scheme in ('data','headers','scripts'):
        target='/'.join([distribution,scheme,*parts[2:]]); root='aux'
    else: raise ValueError()
    wheelcheck._safe_name(target)
    return root,target


def _archive(wheel,expected_hash,budget):
    path=runtime._check_chain(wheel)
    runtime._check_no_named_streams(path)
    stream,initial=runtime._open_regular(path,min(budget,runtime.MAX_FILE_BYTES))
    with stream:
        digest=hashlib.sha256(); remaining=initial.st_size
        while remaining:
            block=stream.read(min(1024*1024,remaining))
            if not block: raise ValueError()
            digest.update(block); remaining-=len(block)
        if stream.read(1) or not hmac.compare_digest(digest.hexdigest(),expected_hash): raise ValueError()
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            wheelcheck._inspect_archive(archive,Path(path),_TARGET,budget)
            distribution,version=offline._wheel_name(Path(path),_TARGET)
            info_roots={name.split('/')[0] for name in archive.namelist()
                        if name.split('/')[0].endswith('.dist-info')}
            if len(info_roots)!=1: raise ValueError()
            info=next(iter(info_roots)); record=info+'/RECORD'
            rows=list(csv.reader(io.StringIO(archive.read(record).decode('utf-8'),newline=''),strict=True))
            results=[]
            for path_name,digest_text,size_text in rows:
                root,target=_mapped(path_name,info,distribution)
                if path_name==record:
                    contents=archive.read(record)
                    entry={'size':len(contents),'sha256':hashlib.sha256(contents).hexdigest()}
                else:
                    if not digest_text.startswith('sha256=') or not size_text.isdigit(): raise ValueError()
                    token=digest_text[7:]
                    value=base64.urlsafe_b64decode(token+'='*((-len(token))%4))
                    if len(value)!=32 or base64.urlsafe_b64encode(value).rstrip(b'=').decode()!=token: raise ValueError()
                    entry={'size':int(size_text),'sha256':value.hex()}
                results.append((root,target,entry))
        if not runtime._same_file(path,stream,initial): raise ValueError()
    return distribution,version,initial.st_size,results


def verify(*,enabled=False,lock_bytes=None,lock_sha256=None,wheels=None,site_root=None,site_pins_bytes=None,
           site_pins_sha256=None,aux_root=None,aux_pins_bytes=None,aux_pins_sha256=None,max_bytes=None):
    if enabled is False: return {'status':'OFF'}
    try:
        if enabled is not True or type(max_bytes) is not int or not 0<max_bytes<=runtime.MAX_TOTAL_BYTES:
            raise ValueError()
        locked=offline._lock_records(lock_bytes,lock_sha256)
        site_expected=_pins(site_pins_bytes,site_pins_sha256); aux_expected=_pins(aux_pins_bytes,aux_pins_sha256)
        roots={'site':plan._root(site_root),'aux':plan._root(aux_root)}
        if plan._related(roots['site'],roots['aux']) or plan._physically_related(roots['site'],roots['aux']):
            raise ValueError()
        if type(wheels) is not list or len(wheels)!=len(locked): raise ValueError()
        mapped={'site':{},'aux':{}}; seen_names=set(); folded={'site':set(),'aux':set()}; used=0
        for item in wheels:
            if (type(item) is not dict or set(item)!={'path','sha256'} or type(item['path']) is not str
                    or type(item['sha256']) is not str or _HEX.fullmatch(item['sha256']) is None): raise ValueError()
            wheel_path=runtime._check_chain(item['path'])
            if any(plan._related(wheel_path,root) for root in roots.values()): raise ValueError()
            name,version,size,entries=_archive(wheel_path,item['sha256'],max_bytes-used)
            if name in seen_names or locked.get(name)!=(version,item['sha256']): raise ValueError()
            seen_names.add(name); used+=size
            for root,target,pin in entries:
                if target.casefold() in folded[root]: raise ValueError()
                folded[root].add(target.casefold()); mapped[root][target]=pin
        if seen_names!=set(locked) or mapped!={'site':site_expected,'aux':aux_expected}: raise ValueError()
        for name,pins in (('site',site_expected),('aux',aux_expected)):
            actual,_,used=plan._tree_pins(roots[name],max_bytes,used,set(pins),'site_pin_mismatch')
            if actual!=pins: raise ValueError()
        for name,pins in (('site',site_expected),('aux',aux_expected)):
            if runtime._tree(roots[name])[0]!=set(pins): raise ValueError()
        return {'status':'VERIFIED_WITHIN_PINS','wheel_count':len(seen_names),
                'site_file_count':len(site_expected),'aux_file_count':len(aux_expected),
                'publisher_authentication':'UNKNOWN','historical_install_provenance':'UNKNOWN'}
    except Exception:
        return {'status':'UNKNOWN'}
