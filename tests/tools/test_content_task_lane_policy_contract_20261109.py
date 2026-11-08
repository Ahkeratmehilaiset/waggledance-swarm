"""Synthetic operator-policy witnesses: ordinary work may quote reviews."""
import hashlib
import json
from pathlib import Path

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_shared_content_projection as fixtures
from tools.bridge_compact_view import event_id

POLICY = Path(__file__).resolve().parents[2] / 'configs/mind/content_message_policy_v2.json'


@pytest.mark.parametrize('lane', [fixtures.LEAD, fixtures.TOOLS, fixtures.FABLE])
@pytest.mark.parametrize('text', ['RCO verdict PASS; next work is ready.', 'Review said tests pass.', 'The reviewer noted a fix; Tools applied it.'])
def test_task_lane_policy_admits_review_mentions_without_event_allowlist(lane, text):
    raw = POLICY.read_bytes()
    doc = json.loads(raw)
    assert doc['schema'] == 'wd.mind-content-policy.v1'
    assert 'approved_source_refs' not in doc
    assert hashlib.sha256(raw).hexdigest() == 'f9ecba66c392c65c2723a7b33791b628a5c5ee1fc2ccd8dc4a6c16e2496872ef'
    row = fixtures.row(text, agent=lane, task_id='lead-mind-4h-package-20261108')
    ref = event_id(row)
    result = cp.build_content_projection(enabled=True, rows={ref: row}, policy_bytes=raw, policy_sha256=hashlib.sha256(raw).hexdigest(), roster=fixtures.ROSTER)
    assert len(result['records']) == 1
    assert result['records'][0]['provenance']['source_event_ref'] == ref


def test_task_lane_policy_keeps_exact_removal_for_all_chunks():
    doc = json.loads(POLICY.read_bytes())
    row = fixtures.row('Review said tests pass.', task_id='lead-mind-4h-package-20261108')
    ref = event_id(row)
    doc['removed_source_refs'] = [ref]
    raw = json.dumps(doc, sort_keys=True).encode('ascii')
    result = cp.build_content_projection(enabled=True, rows={ref: row}, policy_bytes=raw, policy_sha256=hashlib.sha256(raw).hexdigest(), roster=fixtures.ROSTER)
    assert result['records'] == []
