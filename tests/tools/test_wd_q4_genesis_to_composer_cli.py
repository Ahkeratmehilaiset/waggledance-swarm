from pathlib import Path
import json
import pytest
from tests.tools import test_wd_q4_genesis_bootstrap as base
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools.test_wd_q4_staged_composer import setup_case
from tools import wd_q4_genesis_bootstrap as genesis
from tools import wd_q4_genesis_snapshot_prepare as helpers
from tools import wd_q4_staged_composer as staged
from waggledance.core.bridge_log_reader import _file_identity


@pytest.mark.parametrize('fault', ['none', 'optional_false', 'request_after_snapshot', 'continuation', 'continuation_v2', 'continuation_v3', 'v3_pending_after_snapshot', 'v3_pending_after_composer', 'partial_forged', 'late_skip'])
def test_actual_genesis_receipt_to_five_lane_descriptor(synthetic, monkeypatch, tmp_path, capsys, fault):
    from tools import wd_q4_genesis_to_composer_cli as program
    original=genesis.bootstrap
    observed=[]
    class FinishedContinuation(Exception):
        pass
    def probe(**kw):
        path=Path(kw['source_path'])
        complete=path.read_bytes().splitlines(keepends=True)[0]
        if fault=='late_skip':
            rows=base.f2_rows(kw,base.EMPTY_EARLY+base.EMPTY_EARLY+['public','private','public','other_task'])
            kw.update(base.f2_args(kw,rows,max_windows=2))
            kw.update(resume_genesis_skip=True,renewable=True)
            complete=path.read_bytes()
        if fault in ('continuation','continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer','partial_forged'):
            first=json.loads(complete)
            second=base.raw({**first,'ts_utc':'2026-10-31T12:01:00Z'})+b'\n'
            path.write_bytes(complete+second)
            kw['plan_template']['max_windows']=1
            kw['allow_partial_genesis']=True
            kw['batch_rows']=True
            kw['max_rows']=1
        with path.open('rb') as stream:
            source_identity=_file_identity(stream)
        kw.update(expected_source_identity=source_identity, expected_source_prefix_bytes=len(complete),
                  expected_source_prefix_sha256=helpers._sha(complete))
        if fault=='late_skip':
            for _ in range(2):
                with pytest.raises(genesis.GenesisRefused,match='^genesis_skip_progress$'):
                    original(**kw)
        result=original(**kw)
        assert result['genesis_initial_prefix_fully_covered'] is (fault not in ('continuation','continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer','partial_forged'))
        selected={'status':'PUBLISHED_GENESIS','pointer_sha256':result['pointer_sha256'],
                  'runner_receipt_sha256':result['runner_receipt_sha256']}
        if fault in ('continuation','continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer'):
            from tools import wd_q4_production_continuation as continuation
            raw_tuple=(Path(kw['private_state_root'])/'q4-genesis-tuple-v1.json').read_bytes()
            cutoffs=({'cutoff_utc':'2026-10-31T13:10:00Z','previous_cutoff_utc':'2026-10-31T13:00:00Z'}
                     if fault in ('continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer') else {})
            if fault in ('continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer'):
                cutoffs.update(watermark_enabled=True,expected_watermark_record_sha256=None)
            continued=continuation.continue_once(enabled=True,tuple_bytes=raw_tuple,
                expected_tuple_sha256=result['genesis_tuple_sha256'],expected_pointer_sha256=result['pointer_sha256'],
                policy_bytes=kw['policy_bytes'],public_policy_bytes=kw['public_policy_bytes'],
                fleet_bytes=kw['fleet_bytes'],expected_fleet_sha256=kw['expected_fleet_sha256'],**cutoffs)
            assert continued['status']=='caught_up_to_cutoff'
            selected={k:continued[k] for k in ('status','pointer_sha256','runner_receipt_sha256')}
            if cutoffs:
                selected.update({k:continued[k] for k in ('cutoff_utc','previous_cutoff_utc',
                                                            'route_sha256','cutoff_monotonicity')})
            if fault in ('continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer'):
                selected['watermark_record_sha256']=continued['watermark_record_sha256']
        compose_dir=tmp_path/'compose-case'; compose_dir.mkdir()
        previous=staged._STATE_POINTER
        try:
            args, source, stage=setup_case(compose_dir)
            bundle=Path(args['expected_bundle']); wrapper=(bundle/'Invoke-WdQ4Query.ps1').read_bytes()
            fleet=kw['fleet_bytes']; (bundle/'wd-fleet.json').write_bytes(fleet)
            manifest=helpers.update._encode({'schema_version':1,'source_commit':'a'*40,
                'files':{'wd-fleet.json':helpers._sha(fleet),'Invoke-WdQ4Query.ps1':helpers._sha(wrapper)}})
            (bundle/'deployment-manifest.json').write_bytes(manifest)
            source_path=Path(kw['source_path']); complete=(source_path.read_bytes() if fault=='late_skip'
                else source_path.read_bytes().splitlines(keepends=True)[0])
            with source_path.open('rb') as stream: identity=_file_identity(stream)
            policy=compose_dir/'policy'; policy.write_bytes(kw['policy_bytes'])
            public=compose_dir/'public-policy'; public.write_bytes(kw['public_policy_bytes'])
            cfg={'schema':'wd.q4-genesis-cli.v1','bundle_root':str(bundle),'manifest_sha256':helpers._sha(manifest),
                'source_path':str(source_path),'source_sha256':helpers._sha(complete),'source_bytes':len(complete),
                'source_file_identity':identity,'generation_path':kw['generation_path'],'generation':kw['generation'],
                'checkpoint_root':kw['checkpoint_root'],'private_state_root':kw['private_state_root'],
                'trusted_model_root':kw['trusted_model_root'],'policy_path':str(policy),'policy_sha256':kw['policy_sha256'],
                'public_policy_path':str(public),'public_policy_sha256':kw['public_policy_sha256'],
                'fleet_sha256':kw['expected_fleet_sha256'],'max_prefix_bytes':kw['max_prefix_bytes'],
                'max_bytes':kw['max_bytes'],'max_rows':kw['max_rows'],'plan_template':kw['plan_template'],
                'renewable':True,'cutoff_utc':'2026-10-31T13:00:00Z','batch_rows':kw.get('batch_rows',False),
                'min_headroom_bytes':kw['min_headroom_bytes'],'allow_partial_genesis':kw.get('allow_partial_genesis',False)}
            if fault=='late_skip': cfg['resume_genesis_skip']=True
            if fault=='optional_false': cfg['resume_genesis_skip']=False
            genesis_cfg=compose_dir/'genesis-config.json'; genesis_cfg.write_bytes(helpers.update._encode(cfg))
            receipt=compose_dir/'receipt.json'; receipt.write_bytes(helpers.update._encode(selected))
            template=Path(args['request_path']); request=json.loads(template.read_bytes()); plan=Path(request['descriptor']['public_plan'])
            output=Path(args['generation_root'])/'handoff'; output.mkdir()
            artifact_root=Path(args['generation_root'])
            root_info=artifact_root.stat()
            index_info=Path(kw['checkpoint_root']).stat()
            config={'schema':'wd.q4-genesis-to-composer.v1','genesis_config_path':str(genesis_cfg),
                'genesis_config_sha256':helpers._sha(genesis_cfg.read_bytes()),'receipt_path':str(receipt),
                'receipt_sha256':helpers._sha(receipt.read_bytes()),'genesis_tuple_sha256':result['genesis_tuple_sha256'],
                'request_template_path':str(template),'request_template_sha256':helpers._sha(template.read_bytes()),
                'public_plan_template_path':str(plan),'public_plan_template_sha256':helpers._sha(plan.read_bytes()),
                'expected_commit':'a'*40,'stage_dir':str(stage),'output_dir':str(output),
                'generation_root':args['generation_root'],'bundle_store_root':args['bundle_store_root'],
                'active_bundle_root':args['active_bundle_root'],'state_sha256':args['expected_state_sha256'],
                'artifact_private_root':str(artifact_root),'artifact_root_device':root_info.st_dev,
                'artifact_root_inode':root_info.st_ino,'index_root_device':index_info.st_dev,
                'index_root_inode':index_info.st_ino}
            config_path=compose_dir/'pipeline.json'; config_path.write_bytes(helpers.update._encode(config))
            call=['--enable','--config',str(config_path),'--sha256',helpers._sha(config_path.read_bytes())]
            assert program.main(['--enable','--config',str(config_path),'--sha256','0'*64])==2
            assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
            assert not list(output.iterdir()) and not list(stage.iterdir())
            if fault=='late_skip':
                original_gc=genesis_cfg.read_bytes()
                for value,partial in ((1,True),('true',True),(None,True),(True,False)):
                    invalid={**cfg,'resume_genesis_skip':value,'allow_partial_genesis':partial}
                    genesis_cfg.write_bytes(helpers.update._encode(invalid))
                    bad={**config,'genesis_config_sha256':helpers._sha(genesis_cfg.read_bytes())}
                    config_path.write_bytes(helpers.update._encode(bad))
                    assert program.main(['--enable','--config',str(config_path),
                                         '--sha256',helpers._sha(config_path.read_bytes())])==2
                    assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                    assert not list(output.iterdir()) and not list(stage.iterdir())
                genesis_cfg.write_bytes(original_gc)
                config_path.write_bytes(helpers.update._encode(config))
            if fault=='partial_forged':
                assert program.main(call)==2
                assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                assert not list(output.iterdir()) and not list(stage.iterdir())
                observed.append(True)
                raise FinishedContinuation()
            wrong_root={**config,'artifact_root_inode':root_info.st_ino+1}
            config_path.write_bytes(helpers.update._encode(wrong_root))
            assert program.main(['--enable','--config',str(config_path),
                                 '--sha256',helpers._sha(config_path.read_bytes())])==2
            assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
            config_path.write_bytes(helpers.update._encode(config))
            wrong_index={**config,'index_root_inode':index_info.st_ino+1}
            config_path.write_bytes(helpers.update._encode(wrong_index))
            assert program.main(['--enable','--config',str(config_path),
                                 '--sha256',helpers._sha(config_path.read_bytes())])==2
            assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
            config_path.write_bytes(helpers.update._encode(config))
            receipt_original=receipt.read_bytes()
            if fault=='continuation_v3':
                watermark=Path(kw['private_state_root'])/('q4-cutoff-record-'+selected['watermark_record_sha256']+'.json')
                original_mark=watermark.read_bytes()
                watermark.write_bytes(original_mark+b' ')
                assert program.main(call)==2
                assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                watermark.write_bytes(original_mark)
                mixed=dict(selected); mixed.pop('watermark_record_sha256')
                for invalid in (mixed, {**selected,'watermark_record_sha256':'0'*64},
                                {**selected,'cutoff_monotonicity':'CALLER_ASSERTED_REPLAY_CRASH_UNPROVEN'},
                                {**selected,'unexpected':'x'}):
                    receipt.write_bytes(helpers.update._encode(invalid))
                    bad={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
                    config_path.write_bytes(helpers.update._encode(bad))
                    assert program.main(['--enable','--config',str(config_path),
                                         '--sha256',helpers._sha(config_path.read_bytes())])==2
                    assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                    assert not list(output.iterdir()) and not list(stage.iterdir())
                receipt.write_bytes(receipt_original)
                config_path.write_bytes(helpers.update._encode(config))
                private=Path(kw['private_state_root'])
                pending=private/'q4-cutoff.pending.json'
                pending.write_bytes(b'{}')
                assert program.main(call)==3
                assert json.loads(capsys.readouterr().out)=={'status':'UNKNOWN'}
                assert not list(output.iterdir()) and not list(stage.iterdir())
                pending.unlink()
                head=private/'q4-cutoff-current.json'
                original_head=head.read_bytes()
                original_doc=json.loads(original_mark)
                for key,value in (('route_sha256','0'*64),('runner_receipt_sha256','1'*64),
                                  ('pointer_sha256','2'*64),('cutoff_utc','2026-10-31T13:11:00Z')):
                    forged=helpers.update._encode({**original_doc,key:value})
                    forged_sha=helpers._sha(forged)
                    forged_path=private/('q4-cutoff-record-'+forged_sha+'.json')
                    forged_path.write_bytes(forged)
                    head.write_bytes(helpers.update._encode({'schema':'wd.q4-cutoff-head.v1','record_sha256':forged_sha}))
                    receipt.write_bytes(helpers.update._encode({**selected,'watermark_record_sha256':forged_sha}))
                    bad={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
                    config_path.write_bytes(helpers.update._encode(bad))
                    assert program.main(['--enable','--config',str(config_path),
                                         '--sha256',helpers._sha(config_path.read_bytes())])==2
                    assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                    assert not list(output.iterdir()) and not list(stage.iterdir())
                    forged_path.unlink()
                head.write_bytes(original_head)
                receipt.write_bytes(receipt_original)
                config_path.write_bytes(helpers.update._encode(config))
            if fault=='continuation_v2':
                for key, value in (('route_sha256','0'*64), ('cutoff_utc','2026-10-31T12:59:00Z'),
                                   ('previous_cutoff_utc','2026-10-31T13:11:00Z'),
                                   ('previous_cutoff_utc','2026-10-31T12:59:00Z'),
                                   ('cutoff_monotonicity','VERIFIED'), ('status','PUBLISHED_GENESIS')):
                    receipt.write_bytes(helpers.update._encode({**selected,key:value}))
                    bad={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
                    config_path.write_bytes(helpers.update._encode(bad))
                    assert program.main(['--enable','--config',str(config_path),
                                         '--sha256',helpers._sha(config_path.read_bytes())])==2
                    assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                    assert not list(output.iterdir()) and not list(stage.iterdir())
                missing=dict(selected); missing.pop('previous_cutoff_utc')
                receipt.write_bytes(helpers.update._encode(missing))
                bad={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
                config_path.write_bytes(helpers.update._encode(bad))
                assert program.main(['--enable','--config',str(config_path),
                                     '--sha256',helpers._sha(config_path.read_bytes())])==2
                assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                route_path=Path(kw['private_state_root'])/('q4-continuation-route-'+selected['route_sha256']+'.json')
                route=json.loads(route_path.read_bytes())
                route['cutoff_utc']='2026-10-31T13:11:00Z'
                forged=helpers.update._encode(route); forged_sha=helpers._sha(forged)
                forged_path=route_path.with_name('q4-continuation-route-'+forged_sha+'.json')
                forged_path.write_bytes(forged)
                receipt.write_bytes(helpers.update._encode({**selected,'route_sha256':forged_sha,
                                                            'cutoff_utc':'2026-10-31T13:11:00Z'}))
                bad={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
                config_path.write_bytes(helpers.update._encode(bad))
                assert program.main(['--enable','--config',str(config_path),
                                     '--sha256',helpers._sha(config_path.read_bytes())])==2
                assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                forged_path.unlink()
            if fault=='late_skip':
                skip=Path(kw['private_state_root'])/base.SKIP
                original_skip=skip.read_bytes()
                skip.write_bytes(original_skip+b' ')
                assert program.main(call)==2
                assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
                skip.write_bytes(original_skip)
            receipt.write_bytes(helpers.update._encode({'status':'PUBLISHED_GENESIS_PARTIAL',
                                                        'pointer_sha256':result['pointer_sha256']}))
            partial={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
            config_path.write_bytes(helpers.update._encode(partial))
            assert program.main(['--enable','--config',str(config_path),
                                 '--sha256',helpers._sha(config_path.read_bytes())])==2
            assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
            assert not list(output.iterdir()) and not list(stage.iterdir())
            if fault not in ('continuation','continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer'):
                # A genesis runner receipt is not a continuation completion proof.
                receipt.write_bytes(helpers.update._encode({**selected,'status':'caught_up_to_cutoff'}))
                claimed={**config,'receipt_sha256':helpers._sha(receipt.read_bytes())}
                config_path.write_bytes(helpers.update._encode(claimed))
                assert program.main(['--enable','--config',str(config_path),
                                     '--sha256',helpers._sha(config_path.read_bytes())])==2
                assert json.loads(capsys.readouterr().out)=={'status':'REFUSED'}
            receipt.write_bytes(receipt_original)
            config_path.write_bytes(helpers.update._encode(config))
            if fault=='v3_pending_after_composer':
                pending=Path(kw['private_state_root'])/'q4-cutoff.pending.json'
                real_composer=program.composer.compose
                def interrupted(**kwargs):
                    prepared=real_composer(**kwargs)
                    pending.write_bytes(b'{}')
                    return prepared
                with monkeypatch.context() as scoped:
                    scoped.setattr(program.composer,'compose',interrupted)
                    assert program.main(call)==3
                assert json.loads(capsys.readouterr().out)=={'status':'UNKNOWN'}
                assert list(output.iterdir()) and list(stage.iterdir())
                pending.unlink()
            elif fault=='v3_pending_after_snapshot':
                pending=Path(kw['private_state_root'])/'q4-cutoff.pending.json'
                real_snapshot=program.snapshot_prep.prepare_snapshot
                def interrupted(**kwargs):
                    prepared=real_snapshot(**kwargs)
                    pending.write_bytes(b'{}')
                    return prepared
                with monkeypatch.context() as scoped:
                    scoped.setattr(program.snapshot_prep,'prepare_snapshot',interrupted)
                    assert program.main(call)==3
                assert json.loads(capsys.readouterr().out)=={'status':'UNKNOWN'}
                assert list(Path(kw['checkpoint_root']).glob('q4-snapshot-input-*'))
                assert not list(output.iterdir()) and not list(stage.iterdir())
                pending.unlink()
            elif fault=='request_after_snapshot':
                with monkeypatch.context() as scoped:
                    scoped.setattr(program.request_prep,'prepare',lambda **kw: (_ for _ in ()).throw(OSError()))
                    assert program.main(call)==3
                assert json.loads(capsys.readouterr().out)=={'status':'UNKNOWN'}
                assert list(Path(kw['checkpoint_root']).glob('q4-snapshot-input-*'))
                assert not list(output.iterdir()) and not list(stage.iterdir())
            else:
                assert program.main(call)==0
                report=json.loads(capsys.readouterr().out)
                assert report['status']=='PREPARED' and report['pointer_sha256']
                assert Path(report['pointer_path']).exists()
                binding=json.loads((stage/'binding-source.json').read_bytes())
                assert binding['binding']['pointer_sha256']==selected['pointer_sha256']
                assert set(binding['lanes'])=={'codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5'}
            observed.append(True)
        finally:
            staged._STATE_POINTER=previous
        if fault in ('continuation','continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer','late_skip'):
            raise FinishedContinuation()
        return result
    monkeypatch.setattr(genesis,'bootstrap',probe)
    if fault in ('continuation','continuation_v2','continuation_v3','v3_pending_after_snapshot','v3_pending_after_composer','partial_forged','late_skip'):
        with pytest.raises(FinishedContinuation):
            base.test_actual_producer_checkpoint_and_partial_continuation(synthetic,monkeypatch)
    else:
        base.test_actual_producer_checkpoint_and_partial_continuation(synthetic,monkeypatch)
    assert observed==[True]


def test_off_is_inert(capsys):
    from tools import wd_q4_genesis_to_composer_cli as program
    assert program.main([])==0
    assert json.loads(capsys.readouterr().out)=={'status':'OFF'}
