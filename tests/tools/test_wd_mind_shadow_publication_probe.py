"""Only in-process synthetic files and PowerShell parsing; never native jobs."""
import importlib.util
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT, _run_powershell
from test_wd_startup_recovery import q


FIXTURE = ROOT / 'tests/windows/wd_mind_shadow_publication_fixture.py'
PROBE = ROOT / 'tests/windows/wd_mind_shadow_publication_probe.ps1'


def load_fixture():
    assert FIXTURE.is_file(), 'Actual publication fixture Source is missing'
    spec = importlib.util.spec_from_file_location('publication_fixture_under_test', FIXTURE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binding(phase):
    return {'run_id': 'synthetic-run', 'launch_id': 'synthetic-launch', 'phase': phase,
            'producer_sha256': 'e7719531d6e357407f9b2087fbfeb9d15a847aa7df1296413c35280e21d26a21',
            'core_sha256': 'cd009fbc890e15435919f31e52342e6c956825db523898b2c349b887a64d9557'}


def ps_json(script, ps):
    completed = _run_powershell(script, executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('phase', ['before_replace', 'after_replace'])
def test_real_extracted_replace_barrier_and_complete_pointer(tmp_path, phase):
    m = load_fixture()
    root = tmp_path / phase
    root.mkdir()
    observed = []

    class Parked(BaseException):
        pass

    def park():
        ready = json.loads((root / 'ready.json').read_bytes())
        assert ready['pid'] == 4242
        assert ready['binding'] == binding(phase)
        assert ready['phase'] == phase
        assert ready['record'] == 'at_publication_replace'
        expected = m.OLD_POINTER if phase == 'before_replace' else m.NEW_POINTER
        assert (root / 'latest.json').read_bytes() == expected
        observed.append(m.inspect_fixture(root, binding(phase), 4242))
        raise Parked()

    # In-process unwinding is NOT forced job termination. After os.replace,
    # production finally attempts to unlink the already-renamed temp; retain
    # that actual FileNotFoundError rather than pretending native death ran.
    expected_error = Parked if phase == 'before_replace' else FileNotFoundError
    with pytest.raises(expected_error):
        m.run_fixture(root, ROOT, binding(phase), park=park, pid_provider=lambda: 4242)
    assert len(observed) == 1
    assert observed[0]['pointer_complete'] is True
    assert observed[0]['last_ok_intact'] is True
    assert observed[0]['temporary_files'] == sorted(observed[0]['temporary_files'])
    assert observed[0]['extraction'] == ['atomic_write', '_sharing_retry']


@pytest.mark.parametrize('problem', ['foreign_run', 'foreign_phase', 'missing_pid', 'wrong_hash', 'wrong_function_hash', 'foreign_path', 'boolean_pid', 'extra_key', 'partial'])
def test_foreign_or_incomplete_ready_never_validates(tmp_path, problem):
    m = load_fixture()
    root = tmp_path / problem
    root.mkdir()

    class Parked(BaseException):
        pass

    def park():
        raise Parked()

    with pytest.raises(Parked):
        m.run_fixture(root, ROOT, binding('before_replace'), park=park, pid_provider=lambda: 4242)
    path = root / 'ready.json'
    value = json.loads(path.read_bytes())
    if problem == 'foreign_run':
        value['binding']['run_id'] = 'foreign'
    elif problem == 'foreign_phase':
        value['phase'] = 'after_replace'
    elif problem == 'missing_pid':
        del value['pid']
    elif problem == 'wrong_hash':
        value['binding']['core_sha256'] = '0' * 64
    elif problem == 'wrong_function_hash':
        value['function_sha256']['atomic_write'] = '0' * 64
    elif problem == 'foreign_path':
        value['target'] = str(root / 'foreign.json')
    elif problem == 'boolean_pid':
        value['pid'] = True
    elif problem == 'extra_key':
        value['unexpected'] = 'not allowed'
    if problem == 'partial':
        path.write_bytes(b'{')
    else:
        path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises((ValueError, KeyError)):
        m.inspect_fixture(root, binding('before_replace'), 4242)


def test_wrong_source_hash_and_record_overwrite_refuse(tmp_path):
    m = load_fixture()
    root = tmp_path / 'fixture'
    root.mkdir()
    bad = binding('before_replace')
    bad['producer_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='source_hash'):
        m.run_fixture(root, ROOT, bad, park=lambda: pytest.fail('unexpected barrier'))
    assert list(root.iterdir()) == []
    m.write_once(root / 'record.json', {'one': 1})
    before = (root / 'record.json').read_bytes()
    with pytest.raises(FileExistsError):
        m.write_once(root / 'record.json', {'two': 2})
    assert (root / 'record.json').read_bytes() == before


def test_corrupt_pointer_last_ok_and_duplicate_record_refuse(tmp_path):
    m = load_fixture()
    root = tmp_path / 'checks'
    root.mkdir()

    class Parked(BaseException):
        pass

    def park():
        raise Parked()

    with pytest.raises(Parked):
        m.run_fixture(root, ROOT, binding('before_replace'), park=park, pid_provider=lambda: 4242)
    (root / 'latest.json').write_bytes(b'{')
    with pytest.raises(ValueError, match='pointer_incomplete'):
        m.inspect_fixture(root, binding('before_replace'), 4242)
    (root / 'latest.json').write_bytes(m.OLD_POINTER)
    (root / 'old.capture').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='last_ok_changed'):
        m.inspect_fixture(root, binding('before_replace'), 4242)
    with pytest.raises(ValueError, match='duplicate_key'):
        m.strict_json(b'{"phase":"before_replace","phase":"before_replace"}')


def test_modified_source_bytes_refuse_before_fixture_writes(tmp_path):
    m = load_fixture()
    root = tmp_path / 'fixture'
    root.mkdir()
    code = tmp_path / 'code'
    producer = code / 'sandbox/mind/mind_capture_producer.py'
    producer.parent.mkdir(parents=True)
    producer.write_bytes(b'raise AssertionError("must never execute")')
    with pytest.raises(ValueError, match='source_hash'):
        m.run_fixture(root, code, binding('before_replace'), park=lambda: pytest.fail('unexpected barrier'))
    assert list(root.iterdir()) == []


def test_actual_retry_function_is_executed_without_real_sleep(tmp_path):
    m = load_fixture()
    calls = []
    sleeps = []

    def replace(source, target):
        calls.append((source, target))
        if len(calls) < 3:
            raise PermissionError('synthetic sharing window')
        m.os.replace(source, target)

    atomic, functions = m.extracted_functions(ROOT, binding('before_replace'), replace, sleep=sleeps.append)
    target = tmp_path / 'synthetic.json'
    atomic(target, b'synthetic bytes')
    assert target.read_bytes() == b'synthetic bytes'
    assert sleeps == [0.005, 0.01]
    assert len(calls) == 3
    assert functions == m.FUNCTION_PINS


def test_canonical_git_lf_source_bytes_are_supported_explicitly(tmp_path):
    m = load_fixture()
    code = tmp_path / 'canonical'
    for relative in ('sandbox/mind/mind_capture_producer.py', 'tools/wd_bridge_mind.py'):
        path = code / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / relative).read_bytes().replace(b'\r\n', b'\n'))
    declared = binding('before_replace')
    declared['producer_sha256'] = '441ad7b5bd9ef0f6bbb53466bd3eae0a601effdc61ec380850cc8313672ea275'
    declared['core_sha256'] = '3aaf56219fdef4afb208bfbf7c659422fb9618f2df95343bd6528148a389029a'
    target = tmp_path / 'canonical.json'
    atomic, hashes = m.extracted_functions(code, declared, m.os.replace)
    atomic(target, b'canonical synthetic bytes')
    assert target.read_bytes() == b'canonical synthetic bytes'
    assert hashes == m.FUNCTION_PINS


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_probe_safe_load_default_refusal_and_parse(ps):
    assert PROBE.is_file(), 'Actual publication owned-job probe Source is missing'
    script = f"""
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
[void][Management.Automation.Language.Parser]::ParseFile({q(PROBE)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'parse error'}}
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
. {q(PROBE)}
try {{Invoke-WdMindShadowPublicationProbe; throw 'default unexpectedly ran'}}
catch {{if($_.Exception.Message -notmatch 'explicit approved native opt-in'){{throw}}}}
@{{parsed=$true; default_refused=$true}}|ConvertTo-Json
"""
    assert ps_json(script, ps) == {'parsed': True, 'default_refused': True}


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_source_pin_and_owned_job_stop_with_managed_fake(ps):
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
. {q(PROBE)}
Assert-WdPublicationFilePin {q(ROOT / 'tools/wd_bridge_mind.py')} 'CD009FBC890E15435919F31E52342E6C956825DB523898B2C349B887A64D9557'
$job=[pscustomobject]@{{stopped=$false;calls=0}}
$job|Add-Member ScriptMethod Stop {{$this.stopped=$true;$this.calls++}}
$job|Add-Member ScriptMethod Wait {{param($ms) if($ms -ne 0){{throw 'unexpected wait'}};return $this.stopped}}
$job|Add-Member ScriptProperty ActiveProcesses {{if($this.stopped){{return 0}};return 1}}
$drained=Stop-WdPublicationOwnedJob -Managed $job -DrainSeconds 1
if(-not $drained -or $job.calls -ne 1){{throw 'wrong owned-job stop'}}
$unknown=[pscustomobject]@{{}}
$unknown|Add-Member ScriptMethod Stop {{}}
$unknown|Add-Member ScriptMethod Wait {{throw 'unknown count must not credit exit'}}
$unknown|Add-Member ScriptProperty ActiveProcesses {{return 'UNKNOWN'}}
if(Stop-WdPublicationOwnedJob -Managed $unknown -DrainSeconds 1){{throw 'unknown drain credited'}}
@{{drained=$true;calls=$job.calls}}|ConvertTo-Json
"""
    assert ps_json(script, ps) == {'drained': True, 'calls': 1}


@pytest.mark.parametrize('phase', ['before_replace', 'after_replace'])
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_ps_barrier_and_snapshot_validate_actual_synthetic_files(tmp_path, phase, ps):
    m = load_fixture()
    root = tmp_path / phase
    root.mkdir()

    class Parked(BaseException):
        pass

    def park():
        raise Parked()

    with pytest.raises(Parked if phase == 'before_replace' else FileNotFoundError):
        m.run_fixture(root, ROOT, binding(phase), park=park, pid_provider=lambda: 4242)
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
. {q(PROBE)}
$binding=ConvertFrom-Json {q(json.dumps(binding(phase)))}
$ready=Get-WdPublicationReady {q(root)} $binding 4242
$snapshot=Test-WdPublicationSnapshot {q(root)} {q(phase)} $ready.temporary
if(-not $snapshot.pointer_complete -or -not $snapshot.last_ok_intact){{throw 'wrong snapshot'}}
try {{Get-WdPublicationReady {q(root)} $binding 4243;throw 'foreign PID accepted'}}
catch {{if($_.Exception.Message -notmatch 'foreign ready'){{throw}}}}
$path=Join-Path {q(root)} 'ready.json'
$raw=[IO.File]::ReadAllText($path)
[IO.File]::WriteAllText($path,$raw.Replace('"record":','"record":"at_publication_replace","record":'))
try {{Get-WdPublicationReady {q(root)} $binding 4242;throw 'duplicate accepted'}}
catch {{if($_.Exception.Message -notmatch 'duplicate or missing'){{throw}}}}
@{{valid=$true;foreign_refused=$true}}|ConvertTo-Json
"""
    assert ps_json(script, ps) == {'valid': True, 'foreign_refused': True}
