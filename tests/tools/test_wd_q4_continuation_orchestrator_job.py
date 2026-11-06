"""Synthetic Windows Job checks; no live bridge, scheduler or deployment."""
import json
import inspect
import os
from pathlib import Path
import shutil
import subprocess

from tests.tools.test_wd_q4_continuation_runtime_bootstrap import candidate, job, v3_job, encode, sha
from tests.tools import test_wd_q4_continuation_runtime_bootstrap as job_fixtures
from tests.tools.test_wd_q4_continuation_job_v3 import real_cli_with_synthetic_continuation

ROOT = Path(__file__).resolve().parents[2]
LAUNCH = ROOT / 'ops/windows/reboot/Invoke-WdQ4ContinuationOrchestrator.ps1'


def test_launcher_default_off():
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {'status': 'OFF'}


def test_launcher_refuses_invalid_config_without_starting_job():
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run'],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 2
    assert json.loads(result.stdout) == {'status': 'REFUSED'}


def prepare_synthetic_orchestrator_job(item, nested=None, interrupt=False, wrong_receipt=False):
    # The Job, bootstrap and CLI are real; only the orchestrator's runtime
    # implementation is a local synthetic writer. No live state is opened.
    v3_job(item)
    bundle = item['bundle']
    code = bundle / 'tools-bootstrap/tools'
    for name in ('wd_q4_continuation_orchestrator_bootstrap.py', 'wd_q4_continuation_orchestrator_cli.py'):
        shutil.copyfile(ROOT / 'tools' / name, code / name)
    (code / 'wd_q4_descriptor_cli.py').write_text(
        'import hashlib\nfrom pathlib import Path\n'
        'def _read_pinned(path, limit, pin, reason):\n'
        ' raw=Path(path).read_bytes()\n'
        ' if len(raw)>limit or hashlib.sha256(raw).hexdigest()!=pin: raise ValueError()\n'
        ' return raw\n', encoding='ascii')
    (code / 'wd_q4_genesis_cli.py').write_text(
        'import json\ndef _c_path(path): return path\ndef _doc(raw): return json.loads(raw)\n', encoding='ascii')
    private = bundle.parent / 'private'
    nested_source = ''
    nested_call = ''
    if interrupt:
        pending = str(private / 'q4-continuation-orchestrator.pending.json')
        nested_call = (' marker=Path(' + repr(pending) + ')\n'
                       ' if marker.exists(): raise RuntimeError()\n'
                       ' marker.write_bytes(b"synthetic pending\\n")\n raise KeyboardInterrupt()\n')
    if nested is not None:
        from tools import wd_q4_continuation_orchestrator as actual
        nested_source = 'import subprocess\n' + inspect.getsource(actual._run_fixed) + '\n'
        host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        script = ROOT / 'ops/windows/reboot/Invoke-WdQ4Continuation.ps1'
        arguments = (str(nested['bundle']), str(script), str(host), str(nested['config']), nested['config_pin'],
                     nested['manifest_pin'], 'c' * 40, str(nested['spec']), nested['spec_pin'],
                     str(nested['scratch']), sha(host.read_bytes()), 30)
        nested_call = ' result=_run_fixed(*' + repr(arguments) + ')\n if result.returncode!=0: raise RuntimeError()\n'
    result_route = '"c"*64' if wrong_receipt else '"b"*64'
    (code / 'wd_q4_continuation_orchestrator.py').write_text(
        'from datetime import datetime\nimport hashlib,json,os\nfrom pathlib import Path\n'
        + nested_source +
        'class OrchestrationRefused(Exception): pass\n'
        'class _Runner:\n @staticmethod\n def _cutoff_datetime(v): return datetime.fromisoformat(v.replace("Z","+00:00"))\n'
        'runner=_Runner()\n'
        'def raw(v): return json.dumps(v,sort_keys=True,separators=(",", ":")).encode("ascii")\n'
        'def run_once(*, cutoff_utc, **kwargs):\n' + nested_call +
        ' p=Path(' + repr(str(private)) + ')\n'
        ' h=json.loads((p/"q4-cutoff-current.json").read_bytes())\n'
        ' old=json.loads((p/("q4-cutoff-record-"+h["record_sha256"]+".json")).read_bytes())\n'
        ' r={"schema":"wd.q4-cutoff-record.v1","previous_record_sha256":h["record_sha256"],'
        '"cutoff_utc":cutoff_utc,"attempt":"a"*64,"route_sha256":"b"*64,'
        '"runner_receipt_sha256":"f"*64,"pointer_sha256":old["pointer_sha256"]}\n'
        ' b=raw(r); pin=hashlib.sha256(b).hexdigest()\n'
        ' (p/("q4-cutoff-record-"+pin+".json")).write_bytes(b)\n'
        ' (p/"q4-cutoff-current.json").write_bytes(raw({"schema":"wd.q4-cutoff-head.v1","record_sha256":pin}))\n'
        ' return {"status":"DEFERRED","pointer_sha256":r["pointer_sha256"],"runner_receipt_sha256":"f"*64,'
        '"cutoff_utc":cutoff_utc,"previous_cutoff_utc":old["cutoff_utc"],"route_sha256":' + result_route + ','
        '"watermark_record_sha256":pin,"cutoff_monotonicity":"COOPERATIVE_LEASE_PERSISTED_CRASH_UNKNOWN"}\n',
        encoding='ascii')
    bootstrap = code / 'wd_q4_continuation_orchestrator_bootstrap.py'
    files = {'tools-bootstrap/' + p.relative_to(bundle / 'tools-bootstrap').as_posix(): sha(p.read_bytes()).upper()
             for p in (bundle / 'tools-bootstrap').rglob('*') if p.is_file()}
    for name in ('WdQ4ProcessJob.ps1', 'Invoke-WdQ4ContinuationWorker.ps1'):
        files[name] = sha((bundle / name).read_bytes()).upper()
    item['manifest'].write_bytes(encode({'schema_version': 1, 'source_commit': 'c' * 40, 'files': files}))
    item['manifest_pin'] = sha(item['manifest'].read_bytes())
    spec = json.loads(item['spec'].read_bytes())
    spec['manifest_sha256'] = item['manifest_pin']
    item['spec'].write_bytes(encode(spec))
    item['spec_pin'] = sha(item['spec'].read_bytes())
    template = json.loads(item['config'].read_bytes())
    template['manifest_sha256'] = item['manifest_pin']
    template_path = item['config'].parent / 'template.json'
    template_path.write_bytes(encode(template))
    scratch = bundle.parent / 'scratch'
    scratch.mkdir()
    host = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    config = {'schema': 'wd.q4-continuation-orchestrator-cli.v1', 'bundle_root': str(bundle),
              'template_path': str(template_path), 'template_sha256': sha(template_path.read_bytes()),
              'expected_commit': 'c' * 40, 'expected_manifest_sha256': item['manifest_pin'],
              'runtime_path': str(item['spec']), 'runtime_sha256': item['spec_pin'], 'scratch_root': str(scratch),
              'expected_host_sha256': sha(host.read_bytes()), 'cutoff_utc': '2026-10-31T13:03:00Z',
              'timeout_seconds': 30}
    item['config'].write_bytes(encode(config))
    item['config_pin'] = sha(item['config'].read_bytes())
    item['scratch'] = scratch
    item['host_pin'] = sha(host.read_bytes())
    return item


def run_synthetic_job(item):
    command = ['powershell', '-NoProfile', '-NonInteractive', '-File', str(LAUNCH), '-Run',
        '-BundleRoot', str(item['bundle']), '-ExpectedCommit', 'c' * 40,
        '-ExpectedManifestSha256', item['manifest_pin'], '-ConfigPath', str(item['config']),
        '-ExpectedConfigSha256', item['config_pin'], '-RuntimePath', str(item['spec']),
        '-ExpectedRuntimeSha256', item['spec_pin'], '-ScratchRoot', str(item['scratch']),
        '-ExpectedHostSha256', item['host_pin'], '-TimeoutSeconds', '150']
    return subprocess.run(command, capture_output=True, text=True, timeout=45)


def test_real_job_bootstrap_cli_twice_with_synthetic_orchestrator(job):
    item = prepare_synthetic_orchestrator_job(job)
    first = run_synthetic_job(item)
    assert first.returncode == 0, first.stdout + first.stderr
    one = json.loads(first.stdout)
    assert one['status'] == 'DEFERRED'
    private = item['bundle'].parent / 'private'
    assert not (private / 'q4-continuation-orchestrator-job.pending.json').exists()
    assert len(list(private.glob('q4-orchestrator-job-result-*.json'))) == 1
    config = json.loads(item['config'].read_bytes())
    config['cutoff_utc'] = '2026-10-31T13:04:00Z'
    item['config'].write_bytes(encode(config))
    item['config_pin'] = sha(item['config'].read_bytes())
    second = run_synthetic_job(item)
    assert second.returncode == 0, second.stdout + second.stderr
    two = json.loads(second.stdout)
    assert two['previous_cutoff_utc'] == one['cutoff_utc']
    assert two['watermark_record_sha256'] != one['watermark_record_sha256']
    assert not (private / 'q4-continuation-orchestrator-job.pending.json').exists()
    assert len(list(private.glob('q4-orchestrator-job-result-*.json'))) == 2


def test_real_nested_windows_jobs_use_actual_fixed_call(job, tmp_path):
    inner_root = tmp_path / 'inner'
    inner_root.mkdir()
    inner = job_fixtures.job.__wrapped__(job_fixtures.candidate.__wrapped__(inner_root))
    real_cli_with_synthetic_continuation(inner)
    inner['scratch'] = inner_root / 'scratch'
    inner['scratch'].mkdir()
    outer = prepare_synthetic_orchestrator_job(job, nested=inner)
    result = run_synthetic_job(outer)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'DEFERRED'


def test_interruption_is_unknown_and_pending_survives_a_second_invocation(job):
    item = prepare_synthetic_orchestrator_job(job, interrupt=True)
    pending = item['bundle'].parent / 'private/q4-continuation-orchestrator.pending.json'
    outer_pending = item['bundle'].parent / 'private/q4-continuation-orchestrator-job.pending.json'
    for _ in range(2):
        result = run_synthetic_job(item)
        assert result.returncode == 3
        assert json.loads(result.stdout) == {'status': 'UNKNOWN'}
        assert pending.read_bytes() == b'synthetic pending\n'
        assert outer_pending.exists()


def test_landed_result_with_mismatched_record_is_unknown(job):
    item = prepare_synthetic_orchestrator_job(job, wrong_receipt=True)
    result = run_synthetic_job(item)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {'status': 'UNKNOWN'}
    assert (item['bundle'].parent / 'private/q4-continuation-orchestrator-job.pending.json').exists()
    again = run_synthetic_job(item)
    assert again.returncode == 3
    assert json.loads(again.stdout) == {'status': 'UNKNOWN'}
