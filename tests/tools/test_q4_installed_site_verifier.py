"""Offline verifier contract with tiny synthetic wheels and site trees."""
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest
from tests.tools.test_q4_wheel_archive_check import make_archive, record_hash


def sha(value): return hashlib.sha256(value).hexdigest()
def encoded(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


@pytest.fixture
def case(tmp_path):
    wheel_dir=tmp_path/'wheels'; site=tmp_path/'site'; aux=tmp_path/'audit-data'
    for root in (wheel_dir,site,aux): root.mkdir()
    wheel_bytes=make_archive(extra=[('alpha-1.0.data/data/share/man/man1/ialpha.1',b'private synthetic manual')])
    wheel=wheel_dir/'alpha-1.0-py3-none-any.whl'; wheel.write_bytes(wheel_bytes)
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as archive:
        for name in archive.namelist():
            if name.startswith('alpha-1.0.data/data/'):
                target=aux/'alpha'/name.split('.data/',1)[1]
            else: target=site/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(name))
    def pin(root):
        result={p.relative_to(root).as_posix():{'size':p.stat().st_size,'sha256':sha(p.read_bytes())}
                for p in root.rglob('*') if p.is_file()}
        return encoded(result)
    sp,ap=pin(site),pin(aux)
    lock=f'alpha==1.0 --hash=sha256:{sha(wheel_bytes)}\n'.encode()
    return {'enabled':True,'lock_bytes':lock,'lock_sha256':sha(lock),
            'wheels':[{'path':str(wheel),'sha256':sha(wheel_bytes)}],
            'site_root':str(site),'site_pins_bytes':sp,'site_pins_sha256':sha(sp),
            'aux_root':str(aux),'aux_pins_bytes':ap,'aux_pins_sha256':sha(ap),
            'max_bytes':1000000}


def verify(args):
    from tools import q4_installed_site_verifier as verifier
    return verifier.verify(**args)


def repin_tree(args,which):
    root=Path(args[which+'_root'])
    values={p.relative_to(root).as_posix():{'size':p.stat().st_size,'sha256':sha(p.read_bytes())}
            for p in root.rglob('*') if p.is_file()}
    args[which+'_pins_bytes']=encoded(values)
    args[which+'_pins_sha256']=sha(args[which+'_pins_bytes'])


def replace_wheel(args,data):
    Path(args['wheels'][0]['path']).write_bytes(data)
    args['wheels'][0]['sha256']=sha(data)
    args['lock_bytes']=f'alpha==1.0 --hash=sha256:{sha(data)}\n'.encode()
    args['lock_sha256']=sha(args['lock_bytes'])


def test_default_off_no_access():
    from tools import q4_installed_site_verifier as verifier
    assert verifier.verify()=={'status':'OFF'}


def test_locked_wheel_matches_both_site_and_data_aux(case):
    result=verify(case)
    assert result['status']=='VERIFIED_WITHIN_PINS'
    assert result['wheel_count']==1 and result['publisher_authentication']=='UNKNOWN'
    assert result['historical_install_provenance']=='UNKNOWN'


@pytest.mark.parametrize('scheme',['purelib','platlib'])
def test_library_schemes_map_to_site(case,scheme):
    entry=f'alpha-1.0.data/{scheme}/synthetic/module.py'
    data=make_archive(extra=[('alpha-1.0.data/data/share/man/man1/ialpha.1',b'private synthetic manual'),
                             (entry,b'synthetic module')])
    replace_wheel(case,data)
    target=Path(case['site_root'])/'synthetic'/'module.py'
    target.parent.mkdir(); target.write_bytes(b'synthetic module')
    with zipfile.ZipFile(io.BytesIO(data)) as wheel:
        (Path(case['site_root'])/'alpha-1.0.dist-info'/'RECORD').write_bytes(wheel.read('alpha-1.0.dist-info/RECORD'))
    repin_tree(case,'site')
    assert verify(case)['status']=='VERIFIED_WITHIN_PINS'


def test_unknown_data_scheme_fails_closed_even_when_source_pinned(case):
    data=make_archive(extra=[('alpha-1.0.data/unrecognized/thing',b'x')])
    replace_wheel(case,data)
    assert verify(case)=={'status':'UNKNOWN'}


def test_bad_archive_record_fails_even_with_matching_lock(case):
    def corrupt(rows): rows[0][1]='sha256='+'A'*43
    data=make_archive(record_change=corrupt)
    replace_wheel(case,data)
    assert verify(case)=={'status':'UNKNOWN'}


def test_different_wheels_cannot_claim_same_installed_path(case):
    from tools import q4_installed_site_verifier as verifier
    beta=Path(case['wheels'][0]['path']).with_name('beta-1.0-py3-none-any.whl')
    entries={'alpha/__init__.py':b'conflicting module',
             'beta-1.0.dist-info/METADATA':b'Metadata-Version: 2.1\nName: beta\nVersion: 1.0\n\n',
             'beta-1.0.dist-info/WHEEL':b'Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n'}
    table=io.StringIO(newline=''); writer=csv.writer(table,lineterminator='\n')
    for name,value in entries.items(): writer.writerow((name,record_hash(value),len(value)))
    writer.writerow(('beta-1.0.dist-info/RECORD','',''))
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as archive:
        for name,value in entries.items(): archive.writestr(name,value)
        archive.writestr('beta-1.0.dist-info/RECORD',table.getvalue().encode())
    data=stream.getvalue(); beta.write_bytes(data)
    assert verifier._archive(str(beta),sha(data),1000000)[0]=='beta'
    case['wheels'].append({'path':str(beta),'sha256':sha(data)})
    case['lock_bytes']+=f'beta==1.0 --hash=sha256:{sha(data)}\n'.encode()
    case['lock_sha256']=sha(case['lock_bytes'])
    assert verify(case)=={'status':'UNKNOWN'}


@pytest.mark.parametrize('fault',['lock_pin','wheel_changed','site_changed','site_extra','site_missing',
    'aux_missing','aux_changed','bad_site_pin','bad_aux_pin','pin_rewrite','bad_wheel_list','bad_budget'])
def test_bad_or_unknown_inputs_do_not_verify(case,fault):
    site=Path(case['site_root']); aux=Path(case['aux_root'])
    if fault=='lock_pin': case['lock_sha256']='0'*64
    elif fault=='wheel_changed': Path(case['wheels'][0]['path']).write_bytes(b'not a wheel')
    elif fault=='site_changed': (site/'alpha'/'__init__.py').write_bytes(b'changed')
    elif fault=='site_extra': (site/'unexpected.py').write_bytes(b'extra')
    elif fault=='site_missing': (site/'alpha'/'__init__.py').unlink()
    elif fault=='aux_missing': next(aux.rglob('ialpha.1')).unlink()
    elif fault=='aux_changed': next(aux.rglob('ialpha.1')).write_bytes(b'changed')
    elif fault=='bad_site_pin': case['site_pins_sha256']='0'*64
    elif fault=='bad_aux_pin': case['aux_pins_sha256']='0'*64
    elif fault=='pin_rewrite':
        obj=json.loads(case['site_pins_bytes']); obj['alpha/__init__.py']['sha256']='0'*64
        case['site_pins_bytes']=encoded(obj); case['site_pins_sha256']=sha(case['site_pins_bytes'])
    elif fault=='bad_wheel_list': case['wheels']*=2
    elif fault=='bad_budget': case['max_bytes']=1
    assert verify(case)=={'status':'UNKNOWN'}


def test_wrong_distribution_metadata_even_when_wheel_is_repinned(case):
    path=Path(case['wheels'][0]['path'])
    data=make_archive(extra=[('alpha-1.0.data/data/share/man/man1/ialpha.1',b'private synthetic manual')],
                      metadata_text=b'Metadata-Version: 2.1\nName: beta\nVersion: 1.0\n\n')
    path.write_bytes(data); case['wheels'][0]['sha256']=sha(data)
    case['lock_bytes']=f'alpha==1.0 --hash=sha256:{sha(data)}\n'.encode()
    case['lock_sha256']=sha(case['lock_bytes'])
    assert verify(case)=={'status':'UNKNOWN'}


def test_site_symlink_refused(case):
    path=Path(case['site_root'])/'alpha'/'__init__.py'
    original=path.read_bytes(); path.unlink()
    outside=Path(case['aux_root'])/'outside'; outside.write_bytes(original)
    try: path.symlink_to(outside)
    except OSError: pytest.skip('symlink unavailable for current Windows user')
    assert verify(case)=={'status':'UNKNOWN'}
