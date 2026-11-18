"""Synthetic real retained source I/O budget; no model and no killed subprocess."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from test_wd_mind_full_runtime import synthetic_bundle, canonical, _SYNTHETIC_ADMISSION_SCRIPT

ROOT = Path(__file__).resolve().parents[2]


def _run_owned(argv, *, cwd, code):
    process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    try:
        output, error = process.communicate(input=code, timeout=30)
    except BaseException:
        # Timeout/cancellation means HOLD: keep ownership until exit and EOF.
        while True:
            try:
                process.communicate(timeout=10)
                break
            except BaseException:
                print('HOLD: synthetic retained-query process remains owned', flush=True)
                try: time.sleep(1)
                except BaseException: pass
        raise AssertionError('synthetic child deadline or drain failed; late output discarded') from None
    assert process.returncode == 0, error
    return json.loads(output)


def _extract_invoke_code(source):
    tree = ast.parse(source)
    invoke = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'invoke')
    expression = next(node.value for node in invoke.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == 'code' for target in node.targets)
        and isinstance(node.value, ast.BinOp))
    assert (isinstance(expression.op, ast.Add) and isinstance(expression.left, ast.Name)
            and expression.left.id == '_SYNTHETIC_ADMISSION_SCRIPT'
            and isinstance(expression.right, ast.Constant) and isinstance(expression.right.value, str))
    return _SYNTHETIC_ADMISSION_SCRIPT + expression.right.value


def test_invoke_budget_injection_anchor_is_preserved_without_subprocess():
    source = Path(__file__).with_name('test_wd_mind_full_runtime.py').read_text(encoding='utf-8')
    code = _extract_invoke_code(source)
    anchor = "            owner._bind_policy_anchor(anchor)\n            owner._state = 'READY'\n"
    assert code.count(anchor) == 1
    injection = '            # synthetic injected prewarm marker\n'
    injected = code.replace(anchor, anchor + injection, 1)
    assert injected.index(injection.strip()) < injected.index('_prepare_direct_owner_for_test(owner)', injected.index(anchor))
    ast.parse(injected)


def test_real_retained_query_charges_both_source_resolutions_and_refuses_exhaustion(synthetic_bundle):
    source = (ROOT / 'tests/tools/test_wd_mind_full_runtime.py').read_text(encoding='utf-8')
    code = _extract_invoke_code(source)
    anchor = "            owner._bind_policy_anchor(anchor)\n            owner._state = 'READY'\n"
    addition = "            owner._source_budget = None\n            runtime._STARTUP_OWNER_SOURCE_BUDGET_ENABLED = True\n            owner._state = 'STARTING'\n            owner._source_budget = runtime._prewarm_source_budget(owner._snapshot)\n            owner._ensure_retained_locked(source_budget=owner._source_budget)\n            owner._prewarm_generation = owner._generation\n            owner._source_budget = None\n            owner._state = 'READY'\n            from tools import wd_mind_content_snapshot_package as source_package\n            real_resolve = source_package._ConsumerSourceOwner.resolve\n            observed_resolves = []\n            def instrumented_resolve(self, **kwargs):\n                budget = kwargs.get('source_budget')\n                if budget is None:\n                    raise AssertionError('unbudgeted_selected_resolution')\n                before = budget._remaining\n                result = real_resolve(self, **kwargs)\n                if budget._remaining >= before:\n                    raise AssertionError('selected_resolution_did_not_charge_source_reads')\n                observed_resolves.append(budget)\n                return result\n            source_package._ConsumerSourceOwner.resolve = instrumented_resolve\n"
    after = '            path = pathlib.Path(sys.argv[3]); original = path.read_bytes()\n'
    exhaustion = "            assert first['results'] and len(observed_resolves) == 2\n            assert observed_resolves[0] is observed_resolves[1]\n            assert owner._source_budget is None and owner._state == 'READY'\n            def exhausted(snapshot):\n                return runtime.prefix._SourceReadBudget(1)\n            runtime._local_query_source_budget = exhausted\n            try:\n                owner.query_local(enabled=True, lane='codex-lead-1', scope='own',\n                                  query='yhteyskatko uudelleenyritys')\n            except runtime.FullRuntimeRefused:\n                pass\n            else:\n                raise AssertionError('source_budget_not_enforced')\n            assert owner._state == 'HOLD' and owner._source_budget is not None\n            print(json.dumps({'result':'ok', 'real_resolves':len(observed_resolves),\n                              'budget_failure':'HOLD'})); sys.exit(0)\n"
    assert code.count(anchor) == 1 and code.count(after) == 1
    code = code.replace(anchor, anchor + addition, 1).replace(after, exhaustion + after, 1)
    bundle = synthetic_bundle['bundle']
    path = bundle / 'deployment-manifest.json'
    manifest = json.loads(path.read_bytes())
    policy = b'{"identifiers":[],"schema":"wd.mind-identifier-policy.v2"}'
    descriptor = canonical({'schema':'wd.mind-identifier-policy-approval.v1',
        'identifier_policy_sha256':hashlib.sha256(policy).hexdigest(), 'approval_reference_sha256':'b'*64})
    (bundle / 'wd-mind-identifier-policy-approval.json').write_bytes(descriptor)
    manifest['files']['wd-mind-identifier-policy-approval.json'] = hashlib.sha256(descriptor).hexdigest()
    raw = canonical(manifest)
    path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    result = _run_owned([sys.executable, '-B', '-', str(bundle), pin, str(synthetic_bundle['source']),
                         'package_direct_owner_source_recheck'], cwd=bundle, code=code)
    assert result == {'budget_failure':'HOLD', 'real_resolves':2, 'result':'ok'}
