"""Build pinned staged-composer inputs from a verified genesis snapshot.

No install, activation, query, or live-pointer mutation. The caller independently
owns all roots and pins. Output artifacts can contain private snapshot data and
must not be logged. Writes are exclusive; partial writes mean UNKNOWN. This is
not authentication against same-user path races or proof of runtime readiness.
"""
import json
from pathlib import Path
import re

from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_index_preflight as preflight
from tools import wd_q4_descriptor_cli as cli
from tools import wd_q4_descriptor_pointer as paths
from tools import wd_q4_lane_binding_producer as binding
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_live_update_once as update
from tools import wd_q4_secure_transport as transport

_HEX = re.compile(r'[0-9a-f]{64}\Z')


class RequestRefused(ValueError):
    """No output artifact write was attempted."""


class RequestUnknown(RuntimeError):
    """An output write was attempted; inspect before retry."""


def _parse_request(raw):
    """Apply the descriptor CLI's closed request shape to the pinned bytes."""
    document=json.loads(raw.decode('ascii'),object_pairs_hook=cli._pairs,parse_constant=cli._constant)
    if (type(document) is not dict or set(document)!=cli._REQUEST_KEYS
            or document['schema']!=cli.REQUEST_SCHEMA or not cli._printable(document)):
        raise ValueError()
    for _,path_key,pin_key in cli._SOURCES:
        if (type(document[path_key]) is not str or type(document[pin_key]) is not str
                or not cli._HEX64.fullmatch(document[pin_key])): raise ValueError()
        cli._check_path(document[path_key],'source_refused')
    if (type(document['target_dir']) is not str or type(document['generation']) is not str
            or type(document['lanes']) is not list): raise ValueError()
    fixed,descriptor=document['fixed_plan'],document['descriptor']
    if (type(fixed) is not dict or set(fixed)!=cli._FIXED_PLAN_KEYS or
            type(descriptor) is not dict or set(descriptor)!=cli._DESCRIPTOR_KEYS
            or type(descriptor['outer']) is not dict): raise ValueError()
    for fixed_key,descriptor_key in cli._SHARED_PINS:
        if fixed[fixed_key]!=descriptor[descriptor_key]: raise ValueError()
    for fixed_key,outer_key in cli._SHARED_OUTER_PINS:
        if outer_key not in descriptor['outer'] or fixed[fixed_key]!=descriptor['outer'][outer_key]: raise ValueError()
    return document


def prepare(*, enabled=False, request_template_path=None, expected_request_sha256=None,
        public_plan_template_path=None, expected_public_plan_template_sha256=None,
        bundle_root=None, expected_commit=None, expected_manifest_sha256=None,
        checkpoint_root=None, policy_paths=None, snapshot_bytes=None, expected_snapshot_sha256=None,
        expected_pointer_sha256=None, stage_dir=None, output_dir=None):
    if enabled is False:
        return {'status': 'OFF'}
    attempted = False
    try:
        if (enabled is not True or any(type(pin) is not str or not _HEX.fullmatch(pin)
                for pin in (expected_request_sha256, expected_public_plan_template_sha256,
                            expected_manifest_sha256, expected_snapshot_sha256, expected_pointer_sha256))
                or type(snapshot_bytes) is not bytes or not 0 < len(snapshot_bytes) <= transport._MAX_BUNDLE
                or update._sha(snapshot_bytes) != expected_snapshot_sha256
                or type(policy_paths) not in (list, tuple) or len(policy_paths) != 3):
            raise ValueError()
        bundle, checkpoint, stage, output = [paths._absolute(item) for item in
                                               (bundle_root, checkpoint_root, stage_dir, output_dir)]
        for item in (bundle, checkpoint, stage, output):
            paths._chain(item)
            if not item.is_dir(): raise ValueError()
        if (any(not live._disjoint(output, item) for item in (bundle, checkpoint, stage))
                or not live._disjoint(stage, bundle) or not live._disjoint(stage, checkpoint)
                or any(output.iterdir()) or any(stage.iterdir())):
            raise ValueError()
        req_raw = cli._read_pinned(request_template_path, cli.LIMITS['request'], expected_request_sha256, 'request_refused')
        request = _parse_request(req_raw)
        template = cli._read_pinned(public_plan_template_path, 65536, expected_public_plan_template_sha256, 'source_refused')
        plan = live._parse(template)
        parts = transport._fixed_bundle_decode(snapshot_bytes)
        pointer = live._parse(parts['pointer_bytes'])
        if update._sha(parts['pointer_bytes']) != expected_pointer_sha256:
            raise ValueError()
        policies = [paths._absolute(path) for path in policy_paths]
        for path, key in zip(policies, ('policy_bytes', 'roster_bytes', 'public_policy_bytes')):
            path.relative_to(checkpoint)
            if live._read(path, 65536) != parts[key]: raise ValueError()
        index_descriptor = live._parse(parts['active_artifact_bytes']['descriptor.json'])
        policy_sha, roster_sha, public_sha = [update._sha(parts[key]) for key in
                                               ('policy_bytes','roster_bytes','public_policy_bytes')]
        tasks, _ = capture.strict_pins(parts['policy_bytes'], policy_sha, parts['roster_bytes'], roster_sha)
        plan.update(root=str(checkpoint), policy_path=str(policies[0]), roster_path=str(policies[1]),
            public_policy_path=str(policies[2]), pointer_sha256=expected_pointer_sha256,
            source_anchor_sha256=pointer['anchor_sha256'], encoder_sha256=preflight._encoder_digest(index_descriptor['encoder']),
            policy_sha256=policy_sha, roster_sha256=roster_sha, public_policy_sha256=public_sha)
        plan['config']['descriptor_sha256'] = pointer['artifacts']['descriptor_sha256']
        plan_raw = update._encode(plan)
        manifest = cli._read_pinned(str(bundle / 'deployment-manifest.json'), 2 * 1024 * 1024,
                                     expected_manifest_sha256, 'source_refused')
        manifest_doc = live._parse(manifest)
        fleet_pin = manifest_doc['files']['wd-fleet.json'].lower()
        wrapper_pin = manifest_doc['files']['Invoke-WdQ4Query.ps1'].lower()
        fleet = cli._read_pinned(str(bundle / 'wd-fleet.json'), cli.LIMITS['fleet'], fleet_pin, 'source_refused')
        wrapper = cli._read_pinned(str(bundle / 'Invoke-WdQ4Query.ps1'), 1024 * 1024, wrapper_pin, 'source_refused')
        built = binding.build_lane_binding(enabled=True, expected_commit=expected_commit,
            manifest_bytes=manifest, expected_manifest_sha256=expected_manifest_sha256,
            fleet_bytes=fleet, expected_fleet_sha256=fleet_pin, wrapper_bytes=wrapper,
            expected_wrapper_sha256=wrapper_pin, public_plan_bytes=plan_raw,
            expected_public_plan_sha256=update._sha(plan_raw), snapshot_bytes=snapshot_bytes,
            expected_snapshot_sha256=expected_snapshot_sha256)
        pins = live._parse(built['binding_bytes'])['binding']
        plan_path, snapshot_path, request_path = [output / name for name in
            ('public-plan.json','snapshot.json','composer-request.json')]
        request.update(target_dir=str(stage), fleet_path=str(bundle/'wd-fleet.json'), fleet_sha256=fleet_pin,
            binding_path=str(stage/'binding-source.json'), binding_sha256='0'*64,
            snapshot_path=str(snapshot_path), snapshot_sha256=expected_snapshot_sha256)
        request['descriptor'].update(bundle_root=str(bundle), public_plan=str(plan_path), expected_binding=pins)
        request['fixed_plan'].update(bundle_sha256=expected_snapshot_sha256, bundle_bytes=len(snapshot_bytes),
            expected_pointer_sha256=pins['pointer_sha256'], policy_sha256=pins['policy_sha256'],
            roster_sha256=pins['roster_sha256'], expected_public_policy_sha256=pins['public_policy_sha256'],
            expected_source_sha256=pins['source_anchor_sha256'], expected_encoder_sha256=pins['encoder_sha256'],
            approved_tasks=sorted(tasks))
        request_raw = update._encode(request)
        if len(request_raw) > cli.LIMITS['request'] or len(plan_raw) > 65536: raise ValueError()
        for path, raw in ((plan_path,plan_raw),(snapshot_path,snapshot_bytes),(request_path,request_raw)):
            attempted = True
            update._exclusive(path,raw)
        written = cli._read_pinned(str(request_path), cli.LIMITS['request'], update._sha(request_raw), 'request_refused')
        if _parse_request(written) != request: raise ValueError()
        return {'status':'PREPARED', 'request_path':str(request_path), 'request_sha256':update._sha(request_raw),
                'public_plan_path':str(plan_path), 'public_plan_sha256':update._sha(plan_raw),
                'snapshot_path':str(snapshot_path), 'snapshot_sha256':expected_snapshot_sha256}
    except Exception:
        if attempted: raise RequestUnknown('request_prepare_unknown') from None
        raise RequestRefused('request_prepare_refused') from None
