"""Only in-process synthetic files and PowerShell parsing; never native jobs."""
import importlib.util
import hashlib
import json
from pathlib import Path
import re
import sys

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
    # Observe the exact supported checkout bytes; never normalize unknown bytes
    # into an approved pin or assume the author's Windows CRLF checkout.
    module = load_fixture()
    observed = {'run_id': 'synthetic-run', 'launch_id': 'synthetic-launch', 'phase': phase}
    for key, (relative, checkout_pin) in module.PINS.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual not in (checkout_pin, module.CANONICAL_PINS[key]):
            raise ValueError('unsupported_source_bytes')
        observed[key] = actual
    return observed


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('encoding', ['lf', 'crlf', 'mutated'])
def test_composed_probe_pins_the_exact_owned_accounting_loop(tmp_path, ps, encoding):
    canonical = (ROOT / 'ops/windows/reboot/Invoke-WdLaneTurnLoop.ps1').read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(canonical).hexdigest() == 'fef4d7f152d7bba9a345c6213e02c51a218e8bf61999a8058af6fb20cacc84e9'
    data = canonical.replace(b'\n', b'\r\n') if encoding == 'crlf' else canonical
    if encoding == 'mutated':
        data += b'\n# not the frozen accounting Source\n'
    loop = tmp_path / 'synthetic-loop.ps1'
    loop.write_bytes(data)
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
. {q(PROBE)}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(PROBE)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'probe parse error'}}
$assignments=@($ast.FindAll({{param($node)
    $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -ceq '$loopPin'
}},$true))
if($assignments.Count -ne 1){{throw 'loop admission assignment changed'}}
$loop={q(loop)}
try {{
    $actual=. ([scriptblock]::Create($assignments[0].Right.Extent.Text))
    @{{accepted=$true;sha256=$actual.sha256}}|ConvertTo-Json
}} catch {{
    if($_.Exception.Message -cne 'refused: source hash'){{throw}}
    @{{accepted=$false}}|ConvertTo-Json
}}
"""
    result = ps_json(script, ps)
    if encoding == 'mutated':
        assert result == {'accepted': False}
    else:
        assert result == {'accepted': True, 'sha256': hashlib.sha256(data).hexdigest()}


def ps_json(script, ps):
    completed = _run_powershell(script, executable=ps, check=False)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('reparse_at', ['none', 'root', 'parent', 'ancestor'])
@pytest.mark.skipif(sys.platform != 'win32', reason='Public probe admits only existing C-drive audit roots')
def test_audit_root_reparse_ancestry_refuses_before_pins_or_writes(tmp_path, ps, reparse_at):
    # RCO2 / bot P2: strict metadata fakes, never a real junction or native job.
    # The next pin checkpoint throws even in the positive control, before any
    # type initializer, constructor or publication write can be reached.
    audit = tmp_path / '.codex-audit' / 'audit'
    audit.mkdir(parents=True)
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
. {q(PROBE)}
$audit={q(audit)};$where={q(reparse_at)}
$normal=[IO.FileAttributes]::Directory
$linked=$normal -bor [IO.FileAttributes]::ReparsePoint
$ancestor=[pscustomobject]@{{Attributes=$normal;Parent=$null}}
$parent=[pscustomobject]@{{Attributes=$normal;Parent=$ancestor}}
$leaf=[pscustomobject]@{{Attributes=$normal;Parent=$parent}}
switch -CaseSensitive ($where) {{
    'root' {{$leaf.Attributes=$linked}}
    'parent' {{$parent.Attributes=$linked}}
    'ancestor' {{$ancestor.Attributes=$linked}}
}}
$global:publicationAuditMetadata=$leaf
$global:publicationAuditPath=$audit
$global:publicationPinCalls=0
function Get-Item {{
    [CmdletBinding()] param([string]$LiteralPath)
    if($LiteralPath -cne $global:publicationAuditPath){{throw 'unexpected metadata read'}}
    return $global:publicationAuditMetadata
}}
function Assert-WdPublicationFilePin {{
    $global:publicationPinCalls++
    throw 'synthetic: next pin checkpoint'
}}
$state=''
try {{
    Invoke-WdMindShadowPublicationProbe -ApprovedNativeExecution -CodeRoot {q(ROOT)} `
        -AuditRoot $audit -RunId synthetic -LaunchId synthetic -Phase before_replace
    throw 'unexpected completed probe'
}} catch {{
    if($_.Exception.Message -ceq 'refused: reparse audit root'){{$state='refused'}}
    elseif($_.Exception.Message -ceq 'synthetic: next pin checkpoint'){{$state='admitted'}}
    else{{throw}}
}}
@{{state=$state;pin_calls=$global:publicationPinCalls}}|ConvertTo-Json -Compress
"""
    expected = {'state': 'admitted', 'pin_calls': 1} if reparse_at == 'none' else {
        'state': 'refused', 'pin_calls': 0}
    assert ps_json(script, ps) == expected
    assert list(audit.iterdir()) == []


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
    declared['producer_sha256'] = '299dce580d9d8fbdf3c5dbeb7fdc793c3d51ddb4913134a334456b7f282e8c61'
    declared['core_sha256'] = '8555bd3fda512078ba61e3638af7f17ba3de55be11e126f6a7740ae140a67211'
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
Assert-WdPublicationFilePin {q(ROOT / 'tools/wd_bridge_mind.py')} {q(binding('before_replace')['core_sha256'])}
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


def source_copy(tmp_path, newline):
    code = tmp_path / 'source'
    for relative in ('sandbox/mind/mind_capture_producer.py', 'tools/wd_bridge_mind.py'):
        target = code / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = (ROOT / relative).read_bytes().replace(b'\r\n', b'\n')
        target.write_bytes(data if newline == 'lf' else data.replace(b'\n', b'\r\n'))
    return code


@pytest.mark.parametrize('newline', ['lf', 'crlf'])
def test_binding_uses_actual_supported_source_bytes(tmp_path, monkeypatch, newline):
    # RCO2 F1: the same immutable Source can be checked out with either measured
    # encoding. Bind the bytes actually exercised, not the author's CRLF host.
    module = load_fixture()
    code = source_copy(tmp_path, newline)
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', code)
    observed = binding('before_replace')
    for key, (relative, _) in module.PINS.items():
        assert observed[key] == hashlib.sha256((code / relative).read_bytes()).hexdigest()
    atomic, _ = module.extracted_functions(code, observed, module.os.replace)
    target = tmp_path / 'synthetic-result.json'
    atomic(target, b'synthetic bytes')
    assert target.read_bytes() == b'synthetic bytes'


def test_binding_refuses_unrecognized_source_bytes(tmp_path, monkeypatch):
    code = source_copy(tmp_path, 'lf')
    (code / 'tools/wd_bridge_mind.py').write_bytes(b'unrecognized synthetic Source')
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', code)
    with pytest.raises(ValueError, match='unsupported_source_bytes'):
        binding('before_replace')


@pytest.mark.parametrize('newline', ['lf', 'crlf'])
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_ps_source_pin_check_uses_the_actual_encoding(tmp_path, monkeypatch, newline, ps):
    # Exercise the actual existing PowerShell pin check with both byte sets.
    code = source_copy(tmp_path, newline)
    monkeypatch.setattr(sys.modules[__name__], 'ROOT', code)
    test_source_pin_and_owned_job_stop_with_managed_fake(ps)


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_future_fixture_child_argv_is_isolated_before_script_path(ps):
    # RCO2 F2: evaluate only the actual argv assignment, never launch its child.
    script = f"""
$ErrorActionPreference='Stop'
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(PROBE)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'parse error'}}
$nodes=@($ast.FindAll({{param($n)
    $n -is [Management.Automation.Language.AssignmentStatementAst] -and
    $n.Left.Extent.Text -ceq '$arguments'
}},$true))
if($nodes.Count -ne 1){{throw 'argv assignment not unique'}}
$fixture='SYNTHETIC-FIXTURE.py';$fixtureRoot='SYNTHETIC-ROOT';$CodeRoot='SYNTHETIC-CODE'
$binding=@{{run_id='SYNTHETIC-RUN'}}
. ([scriptblock]::Create($nodes[0].Extent.Text))
@($arguments)|ConvertTo-Json -Compress
"""
    argv = ps_json(script, ps)
    assert argv[:2] == ['-I', 'SYNTHETIC-FIXTURE.py']
    assert argv[2:] == ['--approved-native-fixture', '--root', 'SYNTHETIC-ROOT',
                       '--code-root', 'SYNTHETIC-CODE', '--binding-json', '{"run_id":"SYNTHETIC-RUN"}']


def test_extracted_atomic_write_charges_the_exact_bytes_before_its_temporary_exists(tmp_path):
    # RCO2 P1 BC4A1E18: PR1922 made atomic_write charge OutputBudget before mkstemp. The fixture resolves that
    # global to a fixture-owned charge (never the real budget class); it must see the exact size first.
    m = load_fixture()
    target = tmp_path / 'synthetic.json'
    seen = []

    def charge(size):
        seen.append((size, sorted(path.name for path in tmp_path.iterdir())))

    atomic, functions = m.extracted_functions(ROOT, binding('before_replace'), m.os.replace, charge=charge)
    atomic(target, b'synthetic bytes')
    assert seen == [(len(b'synthetic bytes'), [])]
    assert target.read_bytes() == b'synthetic bytes'
    assert functions == m.FUNCTION_PINS


def test_refused_charge_leaves_no_temporary_and_no_replace(tmp_path):
    m = load_fixture()
    replaced = []

    class Refused(Exception):
        pass

    def charge(size):
        raise Refused(size)

    atomic, _ = m.extracted_functions(ROOT, binding('before_replace'), lambda *pair: replaced.append(pair), charge=charge)
    with pytest.raises(Refused):
        atomic(tmp_path / 'synthetic.json', b'synthetic bytes')
    assert replaced == []
    assert list(tmp_path.iterdir()) == []


def test_probe_pins_equal_the_fixture_pins():
    # One pin set: the probe's own producer/core admissions and its ready function hashes equal the fixture's.
    m = load_fixture()
    text = PROBE.read_text(encoding='utf-8')
    for key, variable in (('producer_sha256', '$producerHash'), ('core_sha256', '$coreHash')):
        lines = [line for line in text.splitlines() if line.lstrip().startswith(variable + ' = ')]
        assert len(lines) == 1, variable
        found = [value.lower() for value in re.findall(r"'([0-9A-Fa-f]{64})'", lines[0])]
        assert found == [m.PINS[key][1], m.CANONICAL_PINS[key]], variable
    found = dict(re.findall(r"function_sha256\.(atomic_write|_sharing_retry) -cne '([0-9a-f]{64})'", text))
    assert found == m.FUNCTION_PINS


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('encoding', ['lf', 'crlf', 'mutated'])
def test_probe_source_pins_admit_the_current_producer_and_core_bytes(tmp_path, ps, encoding):
    # RCO2 P1 BC4A1E18: evaluate only the actual producer/core admission assignments against synthetic copies,
    # never the probe body, a native type or a child process.
    code = source_copy(tmp_path, 'crlf' if encoding == 'crlf' else 'lf')
    producer = code / 'sandbox/mind/mind_capture_producer.py'
    if encoding == 'mutated':
        producer.write_bytes(producer.read_bytes() + b'\n# not the merged producer Source\n')
    script = f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
function Add-Type {{throw 'unexpected native compilation'}}
function Start-Process {{throw 'unexpected process'}}
function Get-Process {{throw 'unexpected process query'}}
. {q(PROBE)}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(PROBE)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'probe parse error'}}
$CodeRoot={q(code)}
$result=[ordered]@{{}}
foreach($name in @('producerHash','coreHash')){{
    $nodes=@($ast.FindAll({{param($node)
        $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -ceq ('$'+$name)
    }},$true))
    if($nodes.Count -ne 1){{throw "source admission assignment changed: $name"}}
    try {{$result[$name]=. ([scriptblock]::Create($nodes[0].Right.Extent.Text))}}
    catch {{if($_.Exception.Message -cne 'refused: source hash'){{throw}};$result[$name]='refused'}}
}}
$result|ConvertTo-Json -Compress
"""
    core = hashlib.sha256((code / 'tools/wd_bridge_mind.py').read_bytes()).hexdigest()
    expected = 'refused' if encoding == 'mutated' else hashlib.sha256(producer.read_bytes()).hexdigest()
    assert ps_json(script, ps) == {'producerHash': expected, 'coreHash': core}
