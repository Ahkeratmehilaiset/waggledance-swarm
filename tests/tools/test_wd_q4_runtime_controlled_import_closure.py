"""The controlled loader is a dynamic extra, not a static import closure."""
from pathlib import Path
import json
import pytest

from tests.tools import test_build_q4_runtime_plan as base
from tools import build_q4_runtime_plan as planner
from tools import build_q4_runtime_candidate as builder
from sandbox.mind.step2 import runtime_manifest

HELPER = 'sandbox/mind/step2/q4_controlled_imports.py'


def test_explicit_helper_is_in_runtime_plan(tmp_path):
    source = {'tools/peer.py': b"import importlib\nMODULE = importlib.import_module('sandbox.mind.step2.q4_controlled_imports')\n",
              HELPER: b'VALUE = 42\n'}
    repo, commit = base.make_repo(tmp_path, source)
    site = base.make_tree(tmp_path/'site', {'pkg/__init__.py': b''})
    model = base.make_tree(tmp_path/'model', {'model.safetensors': base.WEIGHTS})
    args = dict(git_executable=base.GIT, repository=str(repo), commit=commit, code_roots=['tools/peer.py'],
                site_root=str(site), model_root=str(model), expected_model_files=['model.safetensors'],
                expected_weights_sha256=base.sha(base.WEIGHTS), generation=base.GENERATION,
                revision=base.REVISION, max_bytes=1024*1024, enabled=True)
    old_plan = planner.plan_runtime_inputs(**args)
    assert 'assets/code/' + HELPER not in old_plan['pins']
    assert 'tools/peer.py' in old_plan['dynamic_loaders']
    plan = planner.plan_runtime_inputs(**args, extra_code_paths=[HELPER])
    assert plan['pins']['assets/code/' + HELPER]['sha256'] == base.sha(source[HELPER])
    assets = tmp_path/'assets'
    for name in plan['code_paths']:
        target = assets/'code'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repo/name).read_bytes())
    audit = tmp_path/'.codex-audit'
    audit.mkdir()
    candidate, manifest = audit/'candidate', audit/'runtime-manifest.json'
    result = builder.build_candidate_from_sources({'assets':assets,'site':site,'model':model}, candidate, manifest,
        pins=plan['pins'], generation=base.GENERATION, revision=base.REVISION, max_bytes=1024*1024)
    records = {item['path']:item for item in json.loads(manifest.read_bytes())['files']}
    assert records['assets/code/' + HELPER]['sha256'] == base.sha(source[HELPER])
    file = candidate/'assets'/'code'/HELPER
    assert file.read_bytes() == source[HELPER]
    assert runtime_manifest.verify_runtime(candidate,manifest,result['manifest_sha256'],
        expected_generation=base.GENERATION,expected_revision=base.REVISION)['file_count'] == len(plan['pins'])
    file.write_bytes(b'changed')
    with pytest.raises(ValueError):
        runtime_manifest.verify_runtime(candidate,manifest,result['manifest_sha256'],
            expected_generation=base.GENERATION,expected_revision=base.REVISION)
