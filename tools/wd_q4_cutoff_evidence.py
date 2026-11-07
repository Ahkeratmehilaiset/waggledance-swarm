"""Default-off, private observation of producer cutoff bound to a reader bundle.

The caller must independently authenticate both supplied hashes and bind the
bundle to the reader. The existing v3 preparation preflight verifies source,
private state, the cooperative watermark, route and runner receipt at a point
in time. It requires the same pointer to remain current and an empty stage;
it is intentionally unsuitable as a general offline verifier. This does not
prove a real read, source authenticity, historical completeness, wall-clock
freshness, crash durability, or resistance to same-user races. The cutoff is
private metadata and must not be published as an agent-use receipt.
"""
import hashlib
import hmac
import re
from pathlib import Path

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import prefix_private_state as private_state
from tools import wd_q4_genesis_to_composer_cli as composer
from tools import wd_q4_secure_transport as transport
from tools import wd_q4_status_prefix_runner as runner
from tools import wd_q4_production_continuation as continuation
from tools import wd_q4_genesis_cli as genesis_cli

_HEX = re.compile(r'[0-9a-f]{64}\Z')


def observe(*, enabled=False, config_path=None, expected_config_sha256=None,
            bundle_bytes=None, expected_bundle_sha256=None):
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if (enabled is not True or type(bundle_bytes) is not bytes
                or not 0 < len(bundle_bytes) <= transport._MAX_BUNDLE
                or any(type(pin) is not str or _HEX.fullmatch(pin) is None
                       for pin in (expected_config_sha256, expected_bundle_sha256))
                or not hmac.compare_digest(hashlib.sha256(bundle_bytes).hexdigest(), expected_bundle_sha256)):
            raise ValueError()
        # The existing lock helper opens O_CREAT. Require both lock files to
        # exist before calling it; same-user removal remains outside the trust
        # boundary and could still race this check.
        config = genesis_cli._doc(composer._read(config_path, expected_config_sha256))
        genesis = genesis_cli._doc(composer._read(config['genesis_config_path'], config['genesis_config_sha256']))
        for path in (Path(genesis['private_state_root']) / private_state._LOCK,
                     Path(genesis['checkpoint_root']) / checkpoint.LOCK):
            if not checkpoint._secure(path).is_file():
                raise ValueError()
        _config, gc, pin, policy, roster, roster_sha, public, v3 = composer._preflight(
            config_path, expected_config_sha256)
        if v3 is None:
            raise ValueError()
        receipt, runner_receipt, private, root, pointer_pin, prefix = v3
        parts = transport._fixed_bundle_decode(bundle_bytes)
        if pin != pointer_pin or not hmac.compare_digest(hashlib.sha256(parts['pointer_bytes']).hexdigest(), pin):
            raise ValueError()
        if (parts['policy_bytes'] != policy or parts['roster_bytes'] != roster
                or parts['public_policy_bytes'] != public):
            raise ValueError()
        tasks, approved_roster = capture.strict_pins(policy, gc['policy_sha256'], roster, roster_sha)
        capture.public_status_policy(public, gc['public_policy_sha256'], tasks)
        pointer = checkpoint._parse_pointer_snapshot(parts['pointer_bytes'], expected_pointer_sha256=pin,
            policy_sha=gc['policy_sha256'], roster_sha=roster_sha, prefix_required=True)
        checkpoint._bind_active_artifact_bytes(parts['active_artifact_bytes'],
                                               expected_hashes=pointer['artifacts'], enabled=True)
        checkpoint._validate_pointer_history(None, parts['pointer_bytes'], pointer, tasks, approved_roster,
            gc['policy_sha256'], roster_sha, policy_bytes=policy, roster_bytes=roster,
            expected_source_mode=checkpoint._requested_source_mode('public-v1', gc['public_policy_sha256']),
            prefix_required=True, snapshot_bytes=parts['history_bytes'])
        build = checkpoint._parse(parts['active_artifact_bytes'][checkpoint.ARTIFACTS['receipt_sha256']])
        coverage = build.get('source_coverage')
        if (type(coverage) is not dict or set(coverage) != {'kind', 'end_reason', 'cutoff_utc'}
                or coverage['kind'] != 'ready_caught_up' or coverage['end_reason'] != 'snapshot_end'
                or continuation._utc(coverage['cutoff_utc']) != runner._cutoff_datetime(receipt['cutoff_utc'])):
            raise ValueError()
        # The complete producer chain and private/current pointer must still
        # match after reading the immutable bundle bytes.
        composer._verify_v3(receipt, runner_receipt, private, root, pointer_pin, prefix)
        return {'status': 'CUTOFF_WITHIN_PINNED_PREFIX', 'cutoff_utc': receipt['cutoff_utc'],
                'freshness': 'UNKNOWN', 'source_authentication': 'UNKNOWN', 'history_coverage': 'UNKNOWN'}
    except Exception:
        return {'status': 'UNKNOWN'}
