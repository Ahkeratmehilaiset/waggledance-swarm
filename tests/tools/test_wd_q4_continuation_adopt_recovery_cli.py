import hashlib
import json
import pytest


def test_off_never_calls_recovery(capsys):
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    assert cli.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status': 'OFF'}


def test_exact_inputs_and_one_call(tmp_path, monkeypatch, capsys):
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    from tools import wd_q4_continuation_adopt_recovery as recovery
    def put(name, data):
        path = tmp_path / name
        path.write_bytes(data)
        return str(path), hashlib.sha256(data).hexdigest()
    base, base_sha = put('baseline.json', b'{}')
    tup, tup_sha = put('tuple.json', b'{"private":"not for output"}')
    receipt, receipt_sha = put('receipt.json', b'{}')
    manifest = 'a' * 64
    config = {'schema':'wd.q4-adopt-recovery-cli.v1','bundle_root':str(tmp_path),
              'manifest_sha256':manifest,'baseline_path':base,'baseline_sha256':base_sha,
              'tuple_path':tup,'tuple_sha256':tup_sha,'receipt_path':receipt,'receipt_sha256':receipt_sha}
    path, pin = put('config.json', json.dumps(config).encode())
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', manifest)
    seen=[]
    def recover(**kwargs):
        seen.append(kwargs)
        return {'status':'ADOPTED'}
    monkeypatch.setattr(recovery, 'recover_once', recover)
    assert cli.main(['--enable','--config',path,'--sha256',pin]) == 0
    assert json.loads(capsys.readouterr().out) == {'status':'ADOPTED'}
    assert len(seen)==1 and seen[0]['genesis_tuple_bytes']==b'{"private":"not for output"}'
    assert seen[0]['receipt_bytes']==b'{}' and seen[0]['baseline_sha256']==base_sha


def test_bad_pin_rejected_without_recovery(tmp_path, monkeypatch, capsys):
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    from tools import wd_q4_continuation_adopt_recovery as recovery
    p=tmp_path/'config.json'; p.write_bytes(b'{}')
    monkeypatch.setattr(recovery, 'recover_once', lambda **kw: pytest.fail('must not call'))
    assert cli.main(['--enable','--config',str(p),'--sha256','0'*64]) == 2
    assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}

@pytest.mark.parametrize('fault', ['extra', 'tuple', 'manifest'])
def test_untrusted_inputs_fail_closed(tmp_path, monkeypatch, capsys, fault):
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    from tools import wd_q4_continuation_adopt_recovery as recovery
    def put(name, data):
        path=tmp_path/name; path.write_bytes(data)
        return str(path), hashlib.sha256(data).hexdigest()
    base,bp=put('base',b'{}'); tup,tp=put('tuple',b'{}'); receipt,rp=put('receipt',b'{}')
    doc={'schema':'wd.q4-adopt-recovery-cli.v1','bundle_root':str(tmp_path),
         'manifest_sha256':'a'*64,'baseline_path':base,'baseline_sha256':bp,
         'tuple_path':tup,'tuple_sha256':tp,'receipt_path':receipt,'receipt_sha256':rp}
    if fault=='extra': doc['unexpected']='bad'
    if fault=='tuple': (tmp_path/'tuple').write_bytes(b'{"tampered":true}')
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', ('b' if fault=='manifest' else 'a')*64)
    path,pin=put('config',json.dumps(doc).encode())
    monkeypatch.setattr(recovery,'recover_once',lambda **kw: pytest.fail('must not call'))
    assert cli.main(['--enable','--config',path,'--sha256',pin])==2
    assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}


def test_real_recovery_with_interrupted_publication(actual_continuation, tmp_path, monkeypatch, capsys):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    args=actual_continuation
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',lambda *_a,**_k: (_ for _ in ()).throw(KeyboardInterrupt()))
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    def put(name,data):
        p=tmp_path/name; p.write_bytes(data)
        return str(p), hashlib.sha256(data).hexdigest()
    tup,tp=put('cli-tuple',args['genesis_tuple_bytes']); receipt,rp=put('cli-receipt',args['receipt_bytes'])
    doc={'schema':'wd.q4-adopt-recovery-cli.v1','bundle_root':str(tmp_path),
         'manifest_sha256':args['manifest_sha256'],'baseline_path':str(args['baseline_path']),
         'baseline_sha256':args['baseline_sha256'],'tuple_path':tup,'tuple_sha256':tp,
         'receipt_path':receipt,'receipt_sha256':rp}
    path,pin=put('cli-config',json.dumps(doc).encode())
    assert cli.main(['--enable','--config',path,'--sha256',pin])==0
    assert json.loads(capsys.readouterr().out)=={'status':'NOT_ADOPTED'}

from tests.tools.test_wd_q4_continuation_adopt import synthetic, actual_continuation

def test_unowned_partial_generation_remains_unknown(actual_continuation, tmp_path, monkeypatch, capsys):
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    args=actual_continuation
    private,generation=paths(args)
    def partial(*_a,**_k):
        generation.mkdir()
        (generation/'unowned.txt').write_bytes(b'keep this')
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',partial)
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    marker=private/'q4-continuation-adopt.pending.json'; old=marker.read_bytes()
    def put(name,data):
        p=tmp_path/name; p.write_bytes(data)
        return str(p),hashlib.sha256(data).hexdigest()
    tup,tp=put('tuple-private',args['genesis_tuple_bytes']); receipt,rp=put('receipt-private',args['receipt_bytes'])
    doc={'schema':'wd.q4-adopt-recovery-cli.v1','bundle_root':str(tmp_path),
         'manifest_sha256':args['manifest_sha256'],'baseline_path':str(args['baseline_path']),
         'baseline_sha256':args['baseline_sha256'],'tuple_path':tup,'tuple_sha256':tp,
         'receipt_path':receipt,'receipt_sha256':rp}
    path,pin=put('config-private',json.dumps(doc).encode())
    assert cli.main(['--enable','--config',path,'--sha256',pin])==3
    assert json.loads(capsys.readouterr().out)=={'status':'UNKNOWN'}
    assert marker.read_bytes()==old and (generation/'unowned.txt').read_bytes()==b'keep this'

def test_unknown_arguments_never_print_their_values(capsys):
    from tools.wd_q4_continuation_adopt_recovery_cli import main
    assert main(['--private-raw-secret'])==2
    captured=capsys.readouterr()
    assert captured.err=='' and json.loads(captured.out)=={'status':'REFUSED'}

def test_duplicate_option_is_refused_without_output_leak(capsys):
    from tools.wd_q4_continuation_adopt_recovery_cli import main
    assert main(['--enable','--config','private-1','--sha256','0'*64,'--config','private-2'])==2
    captured=capsys.readouterr()
    assert not captured.err and json.loads(captured.out)=={'status':'REFUSED'}

@pytest.mark.parametrize('size,expected,called', [(65536,0,True),(65537,2,False)])
def test_tuple_limit_matches_bootstrap(tmp_path,monkeypatch,capsys,size,expected,called):
    from tools import wd_q4_continuation_adopt_recovery_cli as cli
    from tools import wd_q4_continuation_adopt_recovery as recovery
    def put(name,data):
        path=tmp_path/name; path.write_bytes(data)
        return str(path),hashlib.sha256(data).hexdigest()
    base,bp=put('base',b'{}'); tup,tp=put('tuple',b'x'*size); receipt,rp=put('receipt',b'{}')
    doc={'schema':'wd.q4-adopt-recovery-cli.v1','bundle_root':str(tmp_path),
         'manifest_sha256':'a'*64,'baseline_path':base,'baseline_sha256':bp,
         'tuple_path':tup,'tuple_sha256':tp,'receipt_path':receipt,'receipt_sha256':rp}
    path,pin=put('config',json.dumps(doc).encode())
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH','a'*64)
    calls=[]
    def recover(**kwargs):
        calls.append(kwargs)
        return {'status':'NOT_NEEDED'}
    monkeypatch.setattr(recovery,'recover_once',recover)
    assert cli.main(['--enable','--config',path,'--sha256',pin])==expected
    assert bool(calls) is called
    assert json.loads(capsys.readouterr().out)=={'status':'NOT_NEEDED' if called else 'REFUSED'}
