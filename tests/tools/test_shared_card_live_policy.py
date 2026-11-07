"""Closed, non-review card policy; no live bridge or publication in these tests."""
import hashlib
import json
from pathlib import Path

from sandbox.mind.step2 import public_knowledge_card as card
from sandbox.mind.step2 import shared_capture_adapter as capture
from tools import wd_q4_public_card_prefix_verifier as verifier


ROOT = Path(__file__).resolve().parents[2] / 'configs' / 'mind'


def _read(name):
    raw = (ROOT / name).read_bytes()
    return raw, hashlib.sha256(raw).hexdigest()


def test_minimal_policy_accepts_only_closed_public_cards():
    nonreview, nonreview_sha = _read('shared_card_non_review_policy.json')
    writers_raw, writers_sha = _read('shared_card_writer_policy.json')
    vocabulary, vocabulary_sha = _read('shared_card_vocabulary.json')
    paths, paths_sha = _read('shared_card_paths.json')
    lanes = ('codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5')
    uuids = {name: f'{index:08d}-1111-4111-8111-111111111111' for index, name in enumerate(lanes, 1)}
    roster = json.dumps({'lanes': [{'agent': name, 'agent_uuid': uuids[name]} for name in lanes
                                   if name != 'codex-tools-1'],
                         'tools_supervisor': {'agent': 'codex-tools-1', 'agent_uuid': uuids['codex-tools-1']}}).encode()
    tasks, resolved_roster = capture.strict_pins(nonreview, nonreview_sha, roster, hashlib.sha256(roster).hexdigest())
    assert tasks == frozenset({'mind-public-knowledge-v1'})
    assert set(resolved_roster) == set(lanes)
    assert verifier._policy(writers_raw, writers_sha, tasks) == {'codex-lead-1', 'codex-tools-1', 'fable-5'}
    event = {'ts_utc': '2026-11-07T07:37:00Z', 'agent': 'codex-lead-1', 'type': 'status',
             'task_id': 'mind-public-knowledge-v1', 'status': 'info', 'to': '', 'message': '',
             'paths': [], 'write_scope': [], 'payload': {'public_knowledge': {
                 'v': '1', 'kind': 'milestone', 'subject': {'component': 'mind'}, 'outcome': 'partial',
                 'next': 'needs_fix', 'valid_until_utc': '2026-11-08T07:37:00Z', 'tags': []}}}
    result = card.validate(enabled=True, event=event, source_ref=card._event_id(event),
                           vocabulary_bytes=vocabulary, expected_vocabulary_sha256=vocabulary_sha,
                           path_allowlist_bytes=paths, expected_path_allowlist_sha256=paths_sha,
                           now_utc='2026-11-07T07:38:00Z')
    assert result['status'] == 'CURRENT'
    assert result['stored_text'] == 'milestone partial codex-lead-1 component:mind next:needs_fix'
    assert json.loads(paths)['paths'] == []
