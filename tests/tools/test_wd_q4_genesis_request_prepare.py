from pathlib import Path
import json
import pytest
from tests.tools import test_wd_q4_genesis_bootstrap as base
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools.test_wd_q4_staged_composer import setup_case
from tools import wd_q4_genesis_bootstrap as genesis
from tools import wd_q4_genesis_snapshot_prepare as snapshot_prep
from tools import wd_q4_staged_composer as staged
from tools.wd_q4_roster_from_fleet import build_roster_snapshot


def test_actual_genesis_to_composer_request_and_five_lane_binding(synthetic, monkeypatch, tmp_path):
    from tools import wd_q4_genesis_request_prepare as prep
    original = genesis.bootstrap
    observed=[]
    def probe(**kw):
        result=original(**kw)
        roster, roster_sha=build_roster_snapshot(kw['fleet_bytes'], kw['expected_fleet_sha256'], enabled=True)
        snap=snapshot_prep.prepare_snapshot(enabled=True, checkpoint_root=kw['checkpoint_root'],
            expected_pointer_sha256=result['pointer_sha256'], policy_bytes=kw['policy_bytes'],
            expected_policy_sha256=kw['policy_sha256'], roster_bytes=roster, expected_roster_sha256=roster_sha,
            public_policy_bytes=kw['public_policy_bytes'], expected_public_policy_sha256=kw['public_policy_sha256'])
        compose_dir=tmp_path/'compose-case'; compose_dir.mkdir()
        state_prev=staged._STATE_POINTER
        try:
            args, source, stage=setup_case(compose_dir)
            bundle=Path(args['expected_bundle'])
            wrapper=(bundle/'Invoke-WdQ4Query.ps1').read_bytes()
            fleet=kw['fleet_bytes']; (bundle/'wd-fleet.json').write_bytes(fleet)
            manifest=snapshot_prep.update._encode({'schema_version':1,'source_commit':'a'*40,
                'files':{'wd-fleet.json':snapshot_prep._sha(fleet),'Invoke-WdQ4Query.ps1':snapshot_prep._sha(wrapper)}})
            (bundle/'deployment-manifest.json').write_bytes(manifest)
            args['expected_manifest_sha256']=snapshot_prep._sha(manifest).upper()
            template=Path(args['request_path']); request=json.loads(template.read_bytes())
            plan_template=Path(request['descriptor']['public_plan'])
            output=compose_dir/'handoff'; output.mkdir()
            inputs=dict(enabled=True, request_template_path=str(template),
                expected_request_sha256=snapshot_prep._sha(template.read_bytes()),
                public_plan_template_path=str(plan_template), expected_public_plan_template_sha256=snapshot_prep._sha(plan_template.read_bytes()),
                bundle_root=str(bundle), expected_commit='a'*40, expected_manifest_sha256=snapshot_prep._sha(manifest),
                checkpoint_root=kw['checkpoint_root'], policy_paths=snap['policy_paths'],
                snapshot_bytes=snap['snapshot_bytes'], expected_snapshot_sha256=snap['snapshot_sha256'],
                expected_pointer_sha256=result['pointer_sha256'], stage_dir=str(stage), output_dir=str(output))
            for field in ('expected_snapshot_sha256', 'expected_pointer_sha256', 'expected_request_sha256',
                          'expected_public_plan_template_sha256', 'expected_manifest_sha256'):
                with pytest.raises(prep.RequestRefused):
                    prep.prepare(**{**inputs, field:'0'*64})
                assert list(output.iterdir())==[]
            with pytest.raises(prep.RequestRefused):
                prep.prepare(**{**inputs, 'output_dir':str(bundle)})
            broken_output = compose_dir/'partial-handoff'; broken_output.mkdir()
            real_write = prep.update._exclusive
            calls = []
            def fail_after_first(path, raw):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError('synthetic interruption')
                return real_write(path, raw)
            with monkeypatch.context() as scoped:
                scoped.setattr(prep.update, '_exclusive', fail_after_first)
                with pytest.raises(prep.RequestUnknown):
                    prep.prepare(**{**inputs,'output_dir':str(broken_output)})
            assert len(list(broken_output.iterdir())) == 1
            with monkeypatch.context() as scoped:
                scoped.setattr(prep.cli,'_request',lambda path: (_ for _ in ()).throw(AssertionError('reopened request')))
                ready=prep.prepare(**inputs)
            with pytest.raises(prep.RequestRefused):
                prep.prepare(**inputs)
            args['request_path']=ready['request_path']; args['expected_public_plan_sha256']=ready['public_plan_sha256']
            composed=staged.compose(**args)
            assert composed['status']=='PREPARED'
            got=json.loads(Path(composed['descriptor_path']).read_bytes())
            assert got['expected_binding']['pointer_sha256']==result['pointer_sha256']
            binding=json.loads((stage/'binding-source.json').read_bytes())
            assert set(binding['lanes'])=={'codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5'}
            observed.append(True)
        finally:
            staged._STATE_POINTER=state_prev
        return result
    monkeypatch.setattr(genesis,'bootstrap',probe)
    base.test_actual_producer_checkpoint_and_partial_continuation(synthetic,monkeypatch)
    assert observed==[True]


def test_off_does_not_read_inputs():
    from tools import wd_q4_genesis_request_prepare as prep
    assert prep.prepare()=={'status':'OFF'}
