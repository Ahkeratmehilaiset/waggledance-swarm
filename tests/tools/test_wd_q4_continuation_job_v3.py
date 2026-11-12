"""Real Job/bootstrap/CLI, with a synthetic continuation and synthetic data."""
import base64
import json
import os
from pathlib import Path
import shutil

import pytest

from tests.tools.test_wd_q4_continuation_runtime_bootstrap import candidate, job, v3_job, run_job, repin, encode, sha

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Job and PowerShell host required')


def real_cli_with_synthetic_continuation(item):
    expected, record_path = v3_job(item)
    bundle = item['bundle']
    code = bundle / 'tools-bootstrap/tools'
    source_cli = Path(__file__).resolve().parents[2] / 'tools/wd_q4_production_continuation_cli.py'
    shutil.copyfile(source_cli, item['cli'])
    full_result = dict(expected, runner_calls=1, coverage='partial_ready_prefix',
                       source_authenticity='UNKNOWN', freshness='UNKNOWN')
    (code / 'wd_q4_production_continuation.py').write_text(
        'from pathlib import Path\nfrom datetime import datetime\nimport base64\n'
        'class ContinuationRefused(Exception): pass\n'
        'class _Roots:\n'
        ' @staticmethod\n def _root(path): return Path(path)\n'
        ' @staticmethod\n def _observe(path): return None\n'
        'class _Genesis: roots=_Roots()\n'
        'genesis=_Genesis()\n'
        'class _Runner:\n'
        ' @staticmethod\n def _cutoff_datetime(value): return datetime.fromisoformat(value.replace("Z","+00:00"))\n'
        'runner=_Runner()\n'
        'def _part(value, limit): return base64.b64decode(value["bytes_b64"])\n'
        'def continue_once(**kwargs): return ' + repr(full_result) + '\n', encoding='ascii')
    (code / 'wd_q4_descriptor_cli.py').write_text(
        'import hashlib\nfrom pathlib import Path\n'
        'def _read_pinned(path, limit, pin, reason):\n'
        ' raw=Path(path).read_bytes()\n'
        ' if len(raw)>limit or hashlib.sha256(raw).hexdigest()!=pin: raise ValueError()\n'
        ' return raw\n', encoding='ascii')
    (code / 'wd_q4_genesis_cli.py').write_text(
        'import json\ndef _c_path(path): return path\ndef _doc(raw): return json.loads(raw)\n', encoding='ascii')
    (code / 'wd_q4_cutoff_watermark.py').write_text('# synthetic fixture only\n', encoding='ascii')
    fleet = b'{"synthetic":true}'
    (bundle / 'wd-fleet.json').write_bytes(fleet)
    tuple_doc = json.loads(item['tuple'].read_bytes())
    identity = {'identity': 'synthetic-source', 'prefix_bytes': 1, 'prefix_sha256': 'a' * 64}
    tuple_doc['independent_source_pin'] = identity
    item['tuple'].write_bytes(encode(tuple_doc))
    config = json.loads(item['config'].read_bytes())
    policy = item['config'].parent / 'policy.json'
    public = item['config'].parent / 'public.json'
    config.update(tuple_sha256=sha(item['tuple'].read_bytes()), policy_sha256=sha(policy.read_bytes()),
                  public_policy_sha256=sha(public.read_bytes()), fleet_sha256=sha(fleet), pointer_sha256=expected['pointer_sha256'],
                  source_identity=identity['identity'], source_prefix_bytes=identity['prefix_bytes'],
                  source_prefix_sha256=identity['prefix_sha256'])
    item['config'].write_bytes(encode(config))
    repin(item)
    # repin assembles code files; add the actual synthetic fleet pin and rebind all pins.
    manifest = json.loads(item['manifest'].read_bytes())
    manifest['files']['wd-fleet.json'] = sha(fleet)
    item['manifest'].write_bytes(encode(manifest))
    item['manifest_pin'] = sha(item['manifest'].read_bytes())
    for name, pin in (('config', 'config_pin'), ('spec', 'spec_pin')):
        value = json.loads(item[name].read_bytes())
        value['manifest_sha256'] = item['manifest_pin']
        item[name].write_bytes(encode(value))
        item[pin] = sha(item[name].read_bytes())
    return expected, record_path


def test_actual_cli_through_real_job_with_synthetic_continuation(job):
    expected, _ = real_cli_with_synthetic_continuation(job)
    scratch = job['bundle'].parent / 'scratch'
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == expected


def test_actual_cli_job_refuses_changed_record(job):
    _, record = real_cli_with_synthetic_continuation(job)
    record.write_bytes(record.read_bytes() + b' ')
    scratch = job['bundle'].parent / 'scratch'
    scratch.mkdir()
    result = run_job(job, scratch)
    assert result.returncode == 3
    assert json.loads(result.stdout) == {'status': 'UNKNOWN'}
