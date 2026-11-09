"""Opt-in fixed C2 input validation; no launcher or live-state discovery.

Caller must independently pin/authenticate the plan and trusted code. Hashes
prove byte consistency only. Path rechecks do not prevent same-user ABA races.
"""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import subprocess
import sys


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _wrapper_command(plan, system_root, public_plan_bytes):
    """The only admitted command; its timeout comes solely from the pinned plan."""
    for value in (system_root, plan['bundle_root'], plan['wrapper_path'], plan['public_plan_path']):
        if (type(value) is not str or not 3 < len(value) <= 512 or not value.isascii() or not value.isprintable()
                or value[0].lower() != 'c' or value[1:3] != ':\\' or '/' in value
                or '\\\\' in value or ':' in value[2:]
                or any(part in ('','.','..') or part.endswith(('.', ' ')) for part in value[3:].split('\\'))):
            raise ValueError('staged_wrapper_refused')
    if os.path.normcase(os.path.normpath(plan['wrapper_path'])) != os.path.normcase(
            os.path.join(plan['bundle_root'], 'Invoke-WdQ4Query.ps1')):
        raise ValueError('staged_wrapper_refused')
    for key in ('deployment_manifest_sha256','public_plan_sha256'):
        if (type(plan[key]) is not str or len(plan[key]) != 64
                or any(c not in '0123456789abcdef' for c in plan[key])):
            raise ValueError('staged_wrapper_refused')
    if type(plan['bundle_commit']) is not str or len(plan['bundle_commit']) != 40 or any(
            c not in '0123456789abcdef' for c in plan['bundle_commit']):
        raise ValueError('staged_wrapper_refused')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('staged_wrapper_refused')
            result[key] = value
        return result
    if (type(public_plan_bytes) is not bytes or not 0 < len(public_plan_bytes) <= 65536
            or not hmac.compare_digest(_sha(public_plan_bytes),plan['public_plan_sha256'])):
        raise ValueError('staged_wrapper_refused')
    try:
        public = json.loads(public_plan_bytes.decode('utf-8', 'strict'), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (type(public) is not dict or public.get('schema') != 'wd.q4-public-wrapper-plan.v5'
                or type(public.get('config')) is not dict):
            raise ValueError()
        timeout = public['config']['timeout']
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800):
            raise ValueError()
    except Exception:
        raise ValueError('staged_wrapper_refused') from None
    host = os.path.join(system_root,'System32','WindowsPowerShell','v1.0','powershell.exe')
    return [host,'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',plan['wrapper_path'],
            '-BundleRoot',plan['bundle_root'],'-ExpectedBundleCommit',plan['bundle_commit'],
            '-ExpectedManifestSha256',plan['deployment_manifest_sha256'],'-Public',
            '-PublicPlanPath',plan['public_plan_path'],'-ExpectedPublicPlanSha256',plan['public_plan_sha256'],
            '-TimeoutSeconds',str(timeout)]


def validate_content_input(plan, bundle_bytes, public_plan_bytes, *, enabled=False):
    """Independently check the pinned projection; the v5 wrapper re-derives sources before query."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True:
            raise ValueError()
        from tools import wd_q4_secure_transport as fixed
        from tools import wd_q4_lane_link as link
        from tools import wd_q4_public_pointer_resolver as resolver
        from tools.wd_q4_public_query import _encoder_digest
        from sandbox.mind.step2 import content_memory as content
        from sandbox.mind.step2 import shared_capture_adapter as capture
        if (type(bundle_bytes) is not bytes or len(bundle_bytes) != plan['bundle_bytes']
                or not hmac.compare_digest(_sha(bundle_bytes), plan['bundle_sha256'])
                or type(public_plan_bytes) is not bytes
                or not hmac.compare_digest(_sha(public_plan_bytes), plan['public_plan_sha256'])):
            raise ValueError()
        _, actual_plan, pins = link._public_plan(plan['public_plan_path'], plan['public_plan_sha256'])
        if actual_plan != public_plan_bytes or pins != {name: plan[name] for name in fixed._CONTENT_PINS}:
            raise ValueError()
        public = link._json(public_plan_bytes)
        if public['schema'] != 'wd.q4-public-wrapper-plan.v5':
            raise ValueError()
        data = fixed._fixed_content_bundle_decode(bundle_bytes)
        for key in ('policy', 'roster', 'content_decision'):
            pin = plan[key + '_sha256']
            if (_sha(data[key]) != pin or public[key + '_sha256'] != pin
                    or resolver._read_matching(Path(public[key + '_path']), pin, limit=16*1024*1024) != data[key]):
                raise ValueError()
        tasks, _ = capture.strict_pins(data['policy'], plan['policy_sha256'], data['roster'], plan['roster_sha256'])
        if type(plan.get('approved_tasks')) is not list or frozenset(plan['approved_tasks']) != tasks:
            raise ValueError()
        memory_root = Path(public['config']['memory'])
        if str(memory_root / 'descriptor.json') != public['config']['descriptor']:
            raise ValueError()
        for key, filename in (('memory', 'memory.json'), ('projection', 'projection.json'),
                              ('descriptor', 'descriptor.json')):
            pin = plan[key + '_sha256']
            if _sha(data[key]) != pin or resolver._read_matching(memory_root / filename, pin, limit=16*1024*1024) != data[key]:
                raise ValueError()
        descriptor = link._json(data['descriptor'])
        manifest = link._json(data['memory'])
        projection = link._json(data['projection'])
        if (_encoder_digest(descriptor['encoder']) != plan['encoder_sha256']
                or descriptor['encoder']['revision'] != public['config']['runtime_revision']
                or descriptor['memory_sha256'] != plan['memory_sha256']
                or manifest['projection_sha256'] != plan['projection_sha256']):
            raise ValueError()
        decision = content.verify_content_route_decision(data['content_decision'], plan['content_decision_sha256'],
                           policy_sha256=plan['policy_sha256'], roster_sha256=plan['roster_sha256'])
        content.validate_route_projection(projection)
        content.check_content_route_evidence(manifest.get('content_route'), projection, decision,
                                             plan['content_decision_sha256'])
        for prefix in ('message', 'doc'):
            pin = decision[prefix + '_policy_sha256']
            path = public.get(prefix + '_policy_path')
            if pin is None:
                if path is not None:
                    raise ValueError()
            else:
                resolver._read_matching(Path(path), pin, limit=65536)
        return {'status': 'VALIDATED_CONTENT', 'completeness': 'UNKNOWN'}
    except Exception:
        raise ValueError('content_input_refused') from None


def validate_content_result(plan, bundle_bytes, public_plan_bytes, query_bytes, result_bytes, *, enabled=False):
    """Recheck pinned artifacts and independently resolve each claimed answer."""
    if enabled is False:
        return {'status': 'OFF'}
    try:
        if enabled is not True or validate_content_input(plan, bundle_bytes, public_plan_bytes, enabled=True) != {
                'status': 'VALIDATED_CONTENT', 'completeness': 'UNKNOWN'}:
            raise ValueError()
        from tools import wd_q4_lane_link as link
        from tools import wd_q4_secure_transport as fixed
        from sandbox.mind.step2 import shared_capture_adapter as capture
        from sandbox.mind.step2 import shared_result_resolver as resolver
        query = link._json(query_bytes)
        if (type(query) is not dict or set(query) != {'situation', 'time'}
                or type(query['situation']) is not str or type(query['time']) is not str):
            raise ValueError()
        _, _, pins = link._public_plan(plan['public_plan_path'], plan['public_plan_sha256'])
        answer = link._public_result(result_bytes, 0, pins)
        data = fixed._fixed_content_bundle_decode(bundle_bytes)
        if (answer['status'] not in ('ANSWER', 'ABSTAIN', 'DEGRADED') or
                any(answer['binding'][key + '_sha256'] != _sha(data[key])
                    for key in ('memory', 'projection', 'descriptor'))):
            raise ValueError()
        tasks, _ = capture.strict_pins(data['policy'], plan['policy_sha256'], data['roster'], plan['roster_sha256'])
        claims = [{key: item[key] for key in ('ref_sha256', 'doc_id_sha256', 'ref_kind', 'canonical_event')}
                  for item in answer['results']]
        resolved = resolver.resolve_results(memory_manifest=data['memory'], expected_memory_sha256=plan['memory_sha256'],
            projection_bytes=data['projection'], results=claims, query_utc=query['time'], approved_tasks=tasks,
            policy_bytes=data['policy'], policy_sha256=plan['policy_sha256'], roster_bytes=data['roster'],
            roster_sha256=plan['roster_sha256'], content_decision_bytes=data['content_decision'],
            content_decision_sha256=plan['content_decision_sha256'])
        resolved = [{key: item[key] for key in ('ref_sha256', 'doc_id_sha256', 'ref_kind', 'canonical_event', 'stored_text')}
                    for item in resolved]
        if (resolved != answer['results'] or validate_content_input(plan, bundle_bytes, public_plan_bytes, enabled=True)
                != {'status': 'VALIDATED_CONTENT', 'completeness': 'UNKNOWN'}):
            raise ValueError()
        return json.dumps(answer, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    except Exception:
        raise ValueError('content_result_refused') from None


class _PreparedBackend:
    """One fixed staged backend inside the caller-owned outer Windows Job."""

    def __init__(self, plan, public_bytes, command, services, imports, before, roots, code, swig):
        self.plan, self.public_bytes, self.command = plan, public_bytes, command
        self.services, self.imports, self.before, self.roots, self.code, self.swig = (
            services, imports, before, roots, code, swig)
        self._accepted_bundle = None

    def _check_imports(self):
        self.services['_module_origins'](self.before, self.roots, self.code, self.imports, self.swig)
        self.swig.recheck()
        self.imports.check()

    def validate_bundle(self, bundle_bytes):
        self._check_imports()
        if validate_content_input(self.plan, bundle_bytes, self.public_bytes, enabled=True) != {
                'status':'VALIDATED_CONTENT','completeness':'UNKNOWN'}:
            raise ValueError('staged_bundle_refused')
        self._check_imports()
        self._accepted_bundle = _sha(bundle_bytes)

    def __call__(self, plan, plan_bytes, bundle_bytes, query_bytes):
        if (plan is not self.plan or self._accepted_bundle != _sha(bundle_bytes)
                or type(query_bytes) is not bytes or not 0 < len(query_bytes) <= 65536):
            raise ValueError('staged_dispatch_refused')
        self.validate_bundle(bundle_bytes)
        self._check_imports()
        process = None
        try:
            process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=self.plan['bundle_root'], close_fds=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            process.stdin.write(query_bytes)
            process.stdin.close()
            output = process.stdout.read(32769)
            process.stdout.close()
            if len(output) > 32768 or process.wait() != 0:
                raise ValueError('staged_wrapper_refused')
            self._check_imports()
            return validate_content_result(plan, bundle_bytes, self.public_bytes, query_bytes, output, enabled=True)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()


def prepare_content_backend(plan, peer_path, services, *, enabled=False):
    """Prepare the fixed wrapper only after independently pinned runtime verification.

    Called in the already-owned peer Job. The outer transport remains solely
    responsible for timeouts, process-tree drain and transport/attestation tickets.
    """
    if enabled is False:
        return {'status':'OFF'}
    try:
        if enabled is not True or type(plan) is not dict or type(services) is not dict:
            raise ValueError()
        check = services['_check']
        check(plan.get('backend') == 'content-wrapper-v1')
        services['_read_staged_backend'](peer_path, plan)
        system_root = os.environ.get('SYSTEMROOT', '')
        public_bytes = services['_staged_read'](plan['public_plan_path'], plan['public_plan_sha256'], 65536)
        command = _wrapper_command(plan, system_root, public_bytes)
        services['_staged_read'](command[0], plan['host_sha256'], 128*1024*1024)
        services['_contain_child_output']()
        services['_reject_preloaded_runtime']()
        services['_reject_preloaded_faiss']()
        imports = services['_install_controlled_imports'](plan['runtime_root'], plan['runtime_manifest_path'],
                                                        plan['runtime_manifest_sha256'])
        code_root = Path(peer_path).absolute().parents[1]
        verifier = code_root/'sandbox'/'mind'/'step2'/'runtime_manifest.py'
        verifier_bytes = services['_staged_read'](verifier, plan['verifier_sha256'], 131072)
        manifest = json.loads(services['_staged_read'](Path(plan['bundle_root'])/'deployment-manifest.json',
            plan['deployment_manifest_sha256'],4*1024*1024).decode('utf-8'))
        check(str(manifest['files']['tools-bootstrap/sandbox/mind/step2/runtime_manifest.py']).lower() ==
              plan['verifier_sha256'])
        verifier_scope = {'__name__':'_wd_staged_runtime_verifier'}
        exec(compile(verifier_bytes,'<wd-staged-runtime-verifier>','exec'),verifier_scope)
        root = Path(plan['runtime_root'])
        verifier_scope['verify_runtime'](root, Path(plan['runtime_manifest_path']), plan['runtime_manifest_sha256'],
            expected_generation=plan['runtime_generation'], expected_revision=plan['runtime_revision'])
        code, site, model = root/'assets'/'code', root/'site', root/'model'
        check(code.is_dir() and site.is_dir() and model.is_dir())
        roots = [os.path.normcase(os.path.realpath(path)) for path in (code,site)] + services['_interpreter_roots']()
        swig = services['_FaissSwigOrigin'](root,plan['runtime_manifest_path'],plan['runtime_manifest_sha256'])
        before = set(sys.modules)
        sys.path.insert(0,str(code));sys.path.append(str(site))
        if swig.needs_faiss():
            import importlib
            importlib.import_module('faiss')
            services['_module_origins'](before,roots,code,imports,swig)
            check(swig.module is not None)
        result = _PreparedBackend(plan,public_bytes,command,services,imports,before,roots,code,swig)
        result._check_imports()
        return result
    except Exception:
        raise ValueError('staged_backend_preparation_refused') from None
