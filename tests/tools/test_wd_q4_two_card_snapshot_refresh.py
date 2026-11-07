"""IM9: a newer synthetic C2 card snapshot must be publishable over the shared generations root.

Synthetic inputs only; no real data, no model run. The baseline generation B and a first candidate C come from
snapshot 1; snapshot 2 comes from an appended source prefix (a superseding card, a new external anchor) through a
second real orchestrator run and is composed as generation D into the same shared root. The real live selector
publishes C and then D; the baseline pointer and earlier generations must stay byte-identical and select must
choose D. The lane caller loader is real except its read of the fixture's placeholder interpreter.
"""
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from tests.tools.test_wd_q4_card_audit_orchestrator import (  # noqa: F401 - pytest fixtures
    _chain, attested, materialize, real_fixture, sha, staged_producer, tmp_path, write)
from tests.tools.test_wd_q4_real_card_generation import rt

_B, _C, _D = 'b' * 40, 'c' * 40, 'd' * 40


def _tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob('*'))}


def _append_prefix(request, tmp_path):
    """Snapshot 2 input: append one superseding card to the synthetic source and anchor the longer prefix."""
    from tools import wd_q4_external_generation_anchor as anchor
    from waggledance.core import bridge_log_reader as bridge
    decision = json.loads(Path(request['decision']['path']).read_bytes())
    source = Path(decision['source_path'])
    old = source.read_bytes()
    event = json.loads(old.splitlines()[0])
    newer = rt.fx.card(rt.fx.LEAD, 20, 'merged', {'pr': 2046}, 'green', 'none', ['genesis'],
                       head=event['payload']['public_knowledge']['head'], supersedes=rt.fx.event_id(event))
    newer['payload']['public_knowledge']['tags'].remove('synthetic')
    data = old + rt.fx.raw(newer) + b'\n'
    source.write_bytes(data)
    end = bridge.read_bridge_log(str(source)).candidate_cursor
    assert end.offset == len(data) > len(old)
    anchor_root = tmp_path / 'anchor-2'
    anchor_root.mkdir()
    prefix = hashlib.sha256(data).hexdigest()
    pins = anchor.prepare(enabled=True, source_path=str(source), original_cursor=bridge.BridgeCursor(
        end.offset, end.file_identity, None), expected_prefix_sha256=prefix, output_root=str(anchor_root))
    decision.update(cursor={'offset': end.offset, 'file_identity': end.file_identity, 'generation': None},
                    prefix_sha256=prefix, anchor={'root': str(anchor_root), 'generation_sha256': pins['generation_sha256'],
                                                  'binding_sha256': pins['binding_sha256']})
    path = tmp_path / 'decision-request-2.json'
    return {'path': str(path), 'sha256': write(path, decision)}


def test_newer_card_snapshot_from_appended_prefix_is_published_and_selected(attested, tmp_path, monkeypatch):
    pytest.importorskip('faiss')
    mod = importlib.import_module('tools.wd_q4_card_audit_orchestrator')
    from tools import wd_q4_lane_caller_cli as caller
    from tools import wd_q4_live_snapshot_publish as live
    shared = tmp_path / 'live' / 'generations'
    shared.mkdir(parents=True)
    path, _, _ = _chain(attested, tmp_path, monkeypatch, lambda out: shared)
    request = json.loads(path.read_bytes())
    finish = json.loads(Path(request['finish']['path']).read_bytes())

    def run_into(generation, name, decision=None):
        changed = dict(request)
        options = dict(finish['composer_options'], generation=generation)
        template = tmp_path / (name + '-finish-template.json')
        changed['finish'] = {'path': str(template), 'sha256': write(template, dict(finish, composer_options=options))}
        if decision is not None:
            changed['decision'] = decision
        output = tmp_path / (name + '-output')
        output.mkdir()
        changed['output_root'] = str(output)
        request_path = tmp_path / (name + '-request.json')
        result = mod.run(enabled=True, request_path=str(request_path), request_sha256=write(request_path, changed))
        assert result['status'] == 'PREPARED'
        return output

    first_out = run_into(_B, 'snapshot-1-baseline')
    card_pointer = first_out / 'snapshot' / 'card-snapshot-candidate.json'
    card_pointer_bytes = card_pointer.read_bytes()
    run_into(_C, 'snapshot-1-candidate')  # same source prefix, its own run
    run_into(_D, 'snapshot-2-candidate', _append_prefix(request, tmp_path))

    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH', attested['expected_manifest_sha256'])
    real_read = caller._read
    interpreter = attested['descriptor_options']['interpreter']
    monkeypatch.setattr(caller, '_read', lambda p, limit, pin=None: (
        b'' if str(p) == interpreter else real_read(p, limit, pin)))

    def descriptor(generation):
        item = shared / generation / 'shared-session-config.v2.json'
        return str(item), sha(item.read_bytes())
    base_path, base_pin = descriptor(_B)
    baseline = caller._configuration(base_path, base_pin)

    def publish(generation):
        candidate_path, candidate_pin = descriptor(generation)
        return live.publish(enabled=True, baseline_path=base_path, baseline_sha256=base_pin, baseline=baseline,
                            manifest_sha256=attested['expected_manifest_sha256'], candidate_path=candidate_path,
                            candidate_sha256=candidate_pin, candidate=caller._configuration(candidate_path, candidate_pin))

    def selected():
        return live.select(enabled=True, baseline_path=base_path, baseline_sha256=base_pin, baseline=baseline,
                           manifest_sha256=attested['expected_manifest_sha256'],
                           load_configuration=caller._configuration)

    old_plan = json.loads(Path(baseline[0]['public_plan']).read_bytes())
    new_plan = json.loads(Path(caller._configuration(*descriptor(_D))[0]['public_plan']).read_bytes())
    assert new_plan['pointer_sha256'] != old_plan['pointer_sha256']  # a different, newer C2 snapshot
    assert new_plan['source_anchor_sha256'] != old_plan['source_anchor_sha256']
    assert new_plan['pointer_path'] != old_plan['pointer_path']
    before = _tree(shared)
    publication = shared / 'q4-live-current.json'
    for generation in (_C, _D):
        assert publish(generation)['status'] == 'PUBLISHED'
        assert json.loads(publication.read_bytes())['generation'] == generation
        assert Path(selected()['configuration'][0]['public_plan']) == shared / generation / 'public-plan.json'
    after = _tree(shared)
    assert {k: v for k, v in after.items() if k != 'q4-live-current.json'} == {
        k: v for k, v in before.items() if k != 'q4-live-current.json'}
    assert card_pointer.read_bytes() == card_pointer_bytes  # the baseline card snapshot pointer is retained
