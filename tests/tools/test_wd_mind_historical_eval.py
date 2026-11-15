import hashlib
import json
import pytest

from tools import wd_mind_historical_eval as evaluate


def test_scorer_receives_results_only_in_memory_and_reports_aggregate():
    scorer = b'''import json\ndef cmd_score(args):\n    questions=json.load(open(args.testset,encoding="utf-8"))\n    rows=[json.loads(x) for x in open(args.results,encoding="utf-8")]\n    assert rows[0]["hits"][0]["text"] == "synthetic display"\n    output={"schema":"wd.mind-usefulness-scorecard.v1","overall":{"runs":len(rows)},"by_variant":{},"by_need":{},"per_question":[{"private":"never-output"}]}\n    with open(args.out,"w",encoding="utf-8") as target: json.dump(output,target)\n'''
    testset={'questions':[{'id':'S01'}]}
    rows=[{'question_id':'S01','variant':'en','system':'mind','status':'ANSWER','elapsed_seconds':0.1,
           'hits':[{'rank':1,'source_event_sha256':'a'*64,'text':'synthetic display'}]}]
    output=evaluate._score_aggregate(testset, rows, scorer, hashlib.sha256(scorer).hexdigest(), {})
    assert output['overall']['runs'] == 1
    assert 'per_question' not in output and 'synthetic display' not in json.dumps(output)


def test_default_disabled_does_not_touch_owner():
    with pytest.raises(evaluate.EvaluationRefused): evaluate.run_evaluation()


def test_official_scorer_on_synthetic_memory_only_when_present():
    from pathlib import Path
    if not evaluate.SCORER_PATH.is_file():
        pytest.skip('operator-pinned scorer not present on this host')
    source=evaluate.SCORER_PATH.read_bytes()
    assert hashlib.sha256(source).hexdigest() == evaluate.SCORER_SHA256
    question={'id':'S01','category':'synthetic','need':'recall','split':'dev','expect':'ANSWER',
        'age_days':10,'forbid_authors':[],'gold':[{'event_id':'a'*64}],'stale':[],
        'answer_tokens':[['synthetic']], 'stale_tokens':[]}
    testset={'as_of':{'log_lines':1},'questions':[question]}
    hits=[{'rank':1,'source_event_sha256':'a'*64,'text':'synthetic display'}]
    rows=[{'question_id':'S01','variant':lang,'system':'mind','status':'ANSWER',
           'elapsed_seconds':0.1,'hits':hits} for lang in ('en','fi')]
    output=evaluate._score_aggregate(testset,rows,source,evaluate.SCORER_SHA256,{'a'*64:'codex-lead-1'})
    assert output['overall']['runs'] == 2 and output['overall']['pass_rate'] == 1.0
    assert 'synthetic display' not in json.dumps(output)


def test_score_refuses_scorer_hash_mismatch():
    with pytest.raises(ValueError):
        evaluate._score_aggregate({},[],b'anything','0'*64,{})


def test_duplicate_json_and_duplicate_questions_refuse():
    with pytest.raises(ValueError): evaluate._json(b'{"x":1,"x":2}')
    question={'id':'S01','q_en':'synthetic en','q_fi':'synthetic fi'}
    with pytest.raises(ValueError): evaluate._validate_testset({'questions':[question,question]})


def test_preimport_verifies_all_package_files(tmp_path):
    bundle=tmp_path/'bundle'
    (bundle/'tools-bootstrap'/'tools').mkdir(parents=True)
    code=b'synthetic code'
    (bundle/'tools-bootstrap'/'tools'/'fixture.py').write_bytes(code)
    definition=json.dumps({'python_files':['tools/fixture.py']},separators=(',', ':')).encode()
    (bundle/'bridge-code-files.json').write_bytes(definition)
    files={'bridge-code-files.json':hashlib.sha256(definition).hexdigest(),
           'tools-bootstrap/tools/fixture.py':hashlib.sha256(code).hexdigest()}
    manifest=json.dumps({'schema_version':1,'files':files},separators=(',', ':')).encode()
    (bundle/'deployment-manifest.json').write_bytes(manifest)
    digest=hashlib.sha256(manifest).hexdigest()
    assert evaluate._verify_preimport_bundle(str(bundle),digest)['files'] == files
    (bundle/'tools-bootstrap'/'tools'/'fixture.py').write_bytes(b'changed')
    with pytest.raises(ValueError): evaluate._verify_preimport_bundle(str(bundle),digest)


@pytest.mark.parametrize('fast_build',[False,True])
def test_isolated_cli_aggregate_with_synthetic_runtime(tmp_path,fast_build):
    import os
    import subprocess
    import sys
    from pathlib import Path
    if not evaluate.SCORER_PATH.is_file():
        pytest.skip('operator-pinned scorer not present on this host')
    bundle=tmp_path/'bundle'; tools_dir=bundle/'tools-bootstrap'/'tools'; tools_dir.mkdir(parents=True)
    runtime=b'''import hashlib,sys\nclass Cursor:\n    offset=100\nclass Handler:\n    _memory = object()\n    _snapshot={"messages":{"end_cursor":Cursor()}}\n    def build(self): pass\n    def close(self): return {"state":"EXITED","owned":False}\ndef _owner_preflight(**kw): return True\ndef resolve_manifest_runtime_location(**kw):\n    return {"interpreter":sys.executable,"interpreter_sha256":hashlib.sha256(open(sys.executable,"rb").read()).hexdigest()}\ndef assemble_full_runtime(**kw): return object()\ndef create_manifest_worker_factory(**kw): return object()\ndef create_flat_request_handler(**kw): return Handler()\ndef build_fast_fenced_owner(**kw): kw['handler'].build()\ndef query_historical_flat_memory(**kw):\n    return {"results":[{"source_reference":{"sha256":"a"*64},"display_text":"synthetic-never-print"}]}\ndef _flat_check(memory):\n    return {"x":{"provenance":{"source_event_ref":"a"*64},"meta":{"agent":"codex-lead-1"}}}\n'''
    files={'tools/__init__.py':b'', 'tools/wd_mind_full_runtime.py':runtime,
           'tools/wd_mind_e5_rotating_client.py':b'BULK_CONTRACT_SHA256="b"*64\n'}
    manifest_files={}
    for rel, data in files.items():
        (bundle/'tools-bootstrap'/rel).write_bytes(data)
        manifest_files['tools-bootstrap/'+rel]=hashlib.sha256(data).hexdigest()
    definition=json.dumps({'python_files':list(files)},separators=(',', ':')).encode()
    (bundle/'bridge-code-files.json').write_bytes(definition)
    manifest_files['bridge-code-files.json']=hashlib.sha256(definition).hexdigest()
    manifest=json.dumps({'schema_version':1,'files':manifest_files},separators=(',', ':')).encode()
    (bundle/'deployment-manifest.json').write_bytes(manifest)
    question={'id':'S01','q_en':'secret synthetic question en','q_fi':'secret synthetic question fi',
        'category':'synthetic','need':'recall','split':'dev','expect':'ANSWER','age_days':10,'forbid_authors':[],
        'gold':[{'event_id':'a'*64}],'stale':[],'answer_tokens':[['synthetic-never-print']],'stale_tokens':[]}
    dataset=json.dumps({'as_of':{'log_lines':1},'questions':[question]}).encode()
    dataset_path=tmp_path/'synthetic-eval.json';dataset_path.write_bytes(dataset)
    harness=Path(evaluate.__file__).absolute()
    # Windows Store Python can report an unreadable app-execution alias even
    # when launched by its real binary. Resolve the registered interpreter
    # with the same version; the harness still verifies the executing binary.
    interpreter=Path(sys.executable)
    if os.name == 'nt' and interpreter.stat().st_size == 0:
        probe=subprocess.run(['py',f'-{sys.version_info.major}.{sys.version_info.minor}',
            '-I','-S','-B','-c','import sys; print(sys.executable)'],
            capture_output=True,text=True,check=True,timeout=30)
        interpreter=Path(probe.stdout.strip())
    assert interpreter.is_file() and interpreter.stat().st_size > 0
    args=[str(interpreter),'-I','-S','-B',str(harness),'--run-local-eval','--bundle-root',str(bundle),
        '--deployment-manifest-sha256',hashlib.sha256(manifest).hexdigest(), '--testset',str(dataset_path),
        '--testset-sha256',hashlib.sha256(dataset).hexdigest(),'--self-sha256',hashlib.sha256(harness.read_bytes()).hexdigest()]
    if fast_build: args.append('--fast-build')
    result=subprocess.run(args,capture_output=True,text=True,timeout=30,env=dict(os.environ))
    assert result.returncode == 0, result.stdout + result.stderr
    card=json.loads(result.stdout)
    assert card['status']=='MEASURED' and card['metrics']['overall']['pass_rate']==1.0
    assert card['build_profile'] == ('fast_fenced_v1' if fast_build else 'legacy_v1')
    assert 'synthetic-never-print' not in result.stdout + result.stderr
    assert 'secret synthetic question' not in result.stdout + result.stderr
    assert card['testset_cutoff_match']=='UNKNOWN'
    assert card['source_prefix_bytes']==100 and card['indexed_record_count']==1
    assert card['cold_build_seconds'] >= 0 and card['first_query_seconds'] >= 0


def test_retained_loader_refuses_disk_swap_and_unpinned_transitive(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    bundle=tmp_path/'bundle'; package=bundle/'tools-bootstrap'/'tools'; package.mkdir(parents=True)
    files={'tools/__init__.py':b'', 'tools/first.py':b'from tools import second\nVALUE=second.VALUE\n',
           'tools/second.py':b'VALUE="verified"\n'}
    entries={}
    for relative, raw in files.items():
        (bundle/'tools-bootstrap'/relative).write_bytes(raw)
        entries['tools-bootstrap/'+relative]=hashlib.sha256(raw).hexdigest()
    definition=json.dumps({'python_files':list(files)},separators=(',',':')).encode()
    (bundle/'bridge-code-files.json').write_bytes(definition)
    entries['bridge-code-files.json']=hashlib.sha256(definition).hexdigest()
    manifest=json.dumps({'schema_version':1,'files':entries},separators=(',',':')).encode()
    (bundle/'deployment-manifest.json').write_bytes(manifest)
    program=r'''import runpy,sys,pathlib
m=runpy.run_path(sys.argv[1],run_name='synthetic_test')
b=pathlib.Path(sys.argv[2]); manifest, retained=m['_verify_preimport_bundle'](str(b),sys.argv[3],retain_code=True)
(b/'tools-bootstrap/tools/second.py').write_text('VALUE="changed"\n')
(b/'tools-bootstrap/tools/unpinned.py').write_text('VALUE="injected"\n')
with m['_retained_imports'](b,retained):
 from tools import first
 print(first.VALUE)
 try: import tools.unpinned
 except ImportError: print('UNPINNED_REFUSED')
 else: print('UNPINNED_LOADED')
'''
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',program,str(Path(evaluate.__file__).absolute()),
        str(bundle),hashlib.sha256(manifest).hexdigest()],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['verified','UNPINNED_REFUSED']


def test_retained_loader_preserves_real_dependency_origin_checks(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    from tools import wd_mind_content_query_readonly as reader
    root=Path(__file__).resolve().parents[2]
    files=set(reader._REQUIRED_CODE) | {'tools/wd_mind_full_runtime.py',
        'tools/wd_mind_startup_content.py','tools/wd_mind_content_query_readonly.py'}
    bundle=tmp_path/'bundle'; entries={}
    for relative in files:
        raw=(root/relative).read_bytes()
        dest=bundle/'tools-bootstrap'/relative;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
        entries['tools-bootstrap/'+relative]=hashlib.sha256(raw).hexdigest()
    definition=json.dumps({'python_files':sorted(files)},separators=(',', ':')).encode()
    (bundle/'bridge-code-files.json').write_bytes(definition)
    entries['bridge-code-files.json']=hashlib.sha256(definition).hexdigest()
    manifest=json.dumps({'schema_version':1,'files':entries},separators=(',', ':')).encode()
    (bundle/'deployment-manifest.json').write_bytes(manifest)
    program=r'''import runpy,sys,pathlib
m=runpy.run_path(sys.argv[1],run_name='synthetic_test')
b=pathlib.Path(sys.argv[2]); _, code=m['_verify_preimport_bundle'](str(b),sys.argv[3],retain_code=True)
with m['_retained_imports'](b,code):
 from tools import wd_mind_full_runtime as runtime
 runtime.reader._check_code()
 bridge=runtime.reader._pinned_dependency('waggledance.core.bridge_log_reader',
    'waggledance/core/bridge_log_reader.py',('_validate_cursor',))
 print('ORIGIN_AND_PIN_MATCH' if bridge.__name__ == 'waggledance.core.bridge_log_reader' else 'MISMATCH')
'''
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',program,str(Path(evaluate.__file__).absolute()),
        str(bundle),hashlib.sha256(manifest).hexdigest()],capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ORIGIN_AND_PIN_MATCH'


def test_cli_fast_build_is_explicit(monkeypatch,capsys):
    calls=[]
    monkeypatch.setattr(evaluate,'run_evaluation',lambda **kw:calls.append(kw) or {'status':'synthetic'})
    args=['--run-local-eval','--bundle-root','synthetic','--deployment-manifest-sha256','a'*64,
          '--testset','synthetic','--testset-sha256','b'*64,'--self-sha256','c'*64]
    assert evaluate.main(args)==0
    assert evaluate.main(args+['--fast-build'])==0
    assert [call['fast_build'] for call in calls] == [False,True]
    capsys.readouterr()
