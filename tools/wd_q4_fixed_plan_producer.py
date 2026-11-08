"""Default-off, no I/O producer of one ``wd.q4-fixed-plan.v2`` plan for the fixed public-snapshot peer.

This producer is default-off and performs no I/O. Every value is an explicit caller argument:
nothing is discovered, defaulted, hashed from a file, read from the environment or derived from
"latest" state. The checks mirror the parser ``_fixed_plan`` of ``tools/wd_q4_secure_transport.py``
(the closed key set, 64 lowercase hex pins, 40 or 64 lowercase hex generation and revision,
absolute ASCII printable paths of at most 512 characters, a positive bundle length of at most
16 MiB, a sorted unique list of 1..64 ASCII tasks and canonical bytes of at most 4096) so that a
refused plan is never emitted, plus the rule of the verifier ``sandbox/mind/step2/runtime_manifest.py``
that the manifest lives outside the runtime root, on the same drive, and that neither path is a UNC
path. Path parts the verifier's chain check refuses lexically (a part with a colon, a trailing dot or
space, which Win32 would strip to open a different path, a dot or dot-dot part, a device name) and
the forward slash the shared descriptor validator refuses are refused here too, so a plan is never
emitted for a path a consumer refuses before reading it; facts only the filesystem knows (8.3 short
names, reparse points, alternate data streams on existing files) stay the verifier's job. Caller
values are never normalised: an unsorted or duplicated task list is refused, not sorted.

A matching pin is not authentication, not freshness and not a runtime proof. The equalities this
module cannot check are checked by the consumers at run time, which refuse on any mismatch: the
parent compares interpreter_sha256 with the digest of sys.executable, peer_code_sha256 with the
digest of wd_q4_secure_transport.py, verifier_sha256 with the digest of runtime_manifest.py,
runtime_manifest_sha256 with the manifest bytes, and bundle_sha256 and bundle_bytes with the
snapshot bundle; the child verifies the runtime tree against that manifest, whose generation and
revision must equal the plan's; the parent's public validation requires approved_tasks to equal
the task set of the pinned policy and the pointer, roster, public policy, source and encoder pins
to match the bundle. The plan's public-snapshot backend stays refused by the transport unless the
caller enables it explicitly.

Output is canonical ASCII JSON (sorted keys, no whitespace, no BOM) plus its sha256, which is the
binding the parent derives and checks against the caller's pin; the caller writes the bytes and
pins the digest independently (for example as fixed_plan_sha256 of the shared descriptor).
Refusals carry one closed reason and never a caller value in their arguments or repr; as in the
sibling producers, the traceback frame locals of this call still hold the caller's arguments until
the frames are released, so a caller that formats tracebacks with locals must not do so for secrets.
"""
import hashlib
import json
import re

SCHEMA = "wd.q4-fixed-plan.v2"
BACKEND = "public-snapshot-v1"
MAX_PLAN_BYTES = 4096
MAX_BUNDLE_BYTES = 16 * 1024 * 1024
MAX_PATH_CHARS = 512
MAX_TASKS = 64
MAX_TASK_CHARS = 128
PINS = ("interpreter_sha256", "peer_code_sha256", "verifier_sha256", "runtime_manifest_sha256", "bundle_sha256",
        "expected_pointer_sha256", "policy_sha256", "roster_sha256", "expected_public_policy_sha256",
        "expected_source_sha256", "expected_encoder_sha256")
REASONS = frozenset({"disabled", "pin_refused", "path_refused", "limit_refused", "tasks_refused", "schema_refused"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DRIVE = re.compile(r"[A-Za-z]:\\")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE | re.DOTALL)


class FixedPlanRefused(Exception):
    """Closed reason only; never carries a caller value."""


def _refuse(reason):
    raise FixedPlanRefused(reason)


def _pin(value, pattern=_HEX64):
    if type(value) is not str or pattern.match(value) is None:
        _refuse("pin_refused")
    return value


def _path(value):
    """Parser rule (absolute, ASCII, printable, at most 512) narrowed to a backslash drive path, never UNC,
    whose parts the verifier chain check and the shared descriptor validator accept lexically."""
    if (type(value) is not str or not 0 < len(value) <= MAX_PATH_CHARS or not value.isascii()
            or not value.isprintable() or _DRIVE.match(value) is None or "/" in value):
        _refuse("path_refused")
    for part in value[3:].split("\\") if len(value) > 3 else ():
        if (not part or part in (".", "..") or ":" in part or part.endswith((".", " "))
                or _DEVICE.match(part) is not None):
            _refuse("path_refused")
    return value


def _fold(value):
    return value.rstrip("\\").casefold()


def _task_ok(task):
    return type(task) is str and 0 < len(task) <= MAX_TASK_CHARS and task.isascii() and task.isprintable()


def build_fixed_plan(*, enabled=False, interpreter_sha256, peer_code_sha256, verifier_sha256,
                     runtime_manifest_sha256, bundle_sha256, expected_pointer_sha256, policy_sha256, roster_sha256,
                     expected_public_policy_sha256, expected_source_sha256, expected_encoder_sha256,
                     runtime_generation, runtime_revision, runtime_root, runtime_manifest_path, bundle_bytes,
                     approved_tasks):
    """``{"plan_bytes", "plan_sha256"}`` for explicit pins, or ``FixedPlanRefused``."""
    if enabled is not True:
        _refuse("disabled")
    document = {"schema": SCHEMA, "backend": BACKEND}
    for name, value in (("interpreter_sha256", interpreter_sha256), ("peer_code_sha256", peer_code_sha256),
                        ("verifier_sha256", verifier_sha256), ("runtime_manifest_sha256", runtime_manifest_sha256),
                        ("bundle_sha256", bundle_sha256), ("expected_pointer_sha256", expected_pointer_sha256),
                        ("policy_sha256", policy_sha256), ("roster_sha256", roster_sha256),
                        ("expected_public_policy_sha256", expected_public_policy_sha256),
                        ("expected_source_sha256", expected_source_sha256),
                        ("expected_encoder_sha256", expected_encoder_sha256)):
        document[name] = _pin(value)
    document["runtime_generation"] = _pin(runtime_generation, _IDENTITY)
    document["runtime_revision"] = _pin(runtime_revision, _IDENTITY)
    root, manifest = _path(runtime_root), _path(runtime_manifest_path)
    root_folded, manifest_folded = _fold(root), _fold(manifest)
    if (root_folded[0] != manifest_folded[0] or manifest_folded == root_folded
            or manifest_folded.startswith(root_folded + "\\")):
        _refuse("path_refused")
    document["runtime_root"], document["runtime_manifest_path"] = root, manifest
    if type(bundle_bytes) is not int or not 0 < bundle_bytes <= MAX_BUNDLE_BYTES:
        _refuse("limit_refused")
    document["bundle_bytes"] = bundle_bytes
    if (type(approved_tasks) is not list or not 0 < len(approved_tasks) <= MAX_TASKS
            or not all(_task_ok(task) for task in approved_tasks) or approved_tasks != sorted(set(approved_tasks))):
        _refuse("tasks_refused")
    document["approved_tasks"] = list(approved_tasks)
    if set(document) != {"schema", "backend", "runtime_generation", "runtime_revision", "runtime_root",
                         "runtime_manifest_path", "bundle_bytes", "approved_tasks", *PINS}:
        _refuse("schema_refused")
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                     allow_nan=False).encode("ascii")
    if len(raw) > MAX_PLAN_BYTES:
        _refuse("limit_refused")
    return {"plan_bytes": raw, "plan_sha256": hashlib.sha256(raw).hexdigest()}


def build_staged_fixed_plan(*, enabled=False, interpreter_sha256, peer_code_sha256, verifier_sha256,
        runtime_manifest_sha256, bundle_sha256, expected_pointer_sha256, policy_sha256, roster_sha256,
        expected_public_policy_sha256, expected_source_sha256, expected_encoder_sha256,
        runtime_generation, runtime_revision, runtime_root, runtime_manifest_path, bundle_bytes, approved_tasks,
        card_decision_sha256, card_policy_sha256, wrapper_sha256, deployment_manifest_sha256,
        public_plan_sha256, host_sha256, backend_sha256, bundle_root, bundle_commit, wrapper_path, public_plan_path):
    """Build the closed staged-card plan; caller must independently authorize every pin."""
    base = build_fixed_plan(enabled=enabled, interpreter_sha256=interpreter_sha256,
        peer_code_sha256=peer_code_sha256, verifier_sha256=verifier_sha256,
        runtime_manifest_sha256=runtime_manifest_sha256, bundle_sha256=bundle_sha256,
        expected_pointer_sha256=expected_pointer_sha256, policy_sha256=policy_sha256, roster_sha256=roster_sha256,
        expected_public_policy_sha256=expected_public_policy_sha256, expected_source_sha256=expected_source_sha256,
        expected_encoder_sha256=expected_encoder_sha256, runtime_generation=runtime_generation,
        runtime_revision=runtime_revision, runtime_root=runtime_root, runtime_manifest_path=runtime_manifest_path,
        bundle_bytes=bundle_bytes, approved_tasks=approved_tasks)
    document = json.loads(base['plan_bytes'])
    for key, value in (('card_decision_sha256',card_decision_sha256), ('card_policy_sha256',card_policy_sha256),
                       ('wrapper_sha256',wrapper_sha256), ('deployment_manifest_sha256',deployment_manifest_sha256),
                       ('public_plan_sha256',public_plan_sha256), ('host_sha256',host_sha256),
                       ('backend_sha256',backend_sha256)):
        document[key] = _pin(value)
    for key, value in (('bundle_root',bundle_root), ('wrapper_path',wrapper_path),
                       ('public_plan_path',public_plan_path)):
        document[key] = _path(value)
    if any(value[0].lower() != 'c' for value in (runtime_root, runtime_manifest_path, bundle_root, wrapper_path, public_plan_path)):
        _refuse('path_refused')
    if _fold(wrapper_path) != _fold(bundle_root + r'\Invoke-WdQ4Query.ps1'):
        _refuse('path_refused')
    document.update(schema='wd.q4-fixed-plan.v3', backend='staged-card-wrapper-v1',
                    bundle_commit=_pin(bundle_commit, re.compile(r'[0-9a-f]{40}\Z')))
    raw = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(raw) > MAX_PLAN_BYTES:
        _refuse('limit_refused')
    return {'plan_bytes':raw, 'plan_sha256':hashlib.sha256(raw).hexdigest()}


def build_content_fixed_plan(*, enabled=False, interpreter_sha256, peer_code_sha256, verifier_sha256,
        runtime_manifest_sha256, bundle_sha256, encoder_sha256, policy_sha256, roster_sha256,
        content_decision_sha256, descriptor_sha256, memory_sha256, projection_sha256,
        runtime_generation, runtime_revision, runtime_root, runtime_manifest_path, bundle_bytes, approved_tasks,
        wrapper_sha256, deployment_manifest_sha256, public_plan_sha256, host_sha256, backend_sha256,
        bundle_root, bundle_commit, wrapper_path, public_plan_path):
    """Build a distinct closed content-wrapper plan with seven independently pinned content fields."""
    base = build_fixed_plan(enabled=enabled, interpreter_sha256=interpreter_sha256,
        peer_code_sha256=peer_code_sha256, verifier_sha256=verifier_sha256,
        runtime_manifest_sha256=runtime_manifest_sha256, bundle_sha256=bundle_sha256,
        expected_pointer_sha256='0' * 64, policy_sha256=policy_sha256, roster_sha256=roster_sha256,
        expected_public_policy_sha256='0' * 64, expected_source_sha256='0' * 64,
        expected_encoder_sha256=encoder_sha256, runtime_generation=runtime_generation,
        runtime_revision=runtime_revision, runtime_root=runtime_root, runtime_manifest_path=runtime_manifest_path,
        bundle_bytes=bundle_bytes, approved_tasks=approved_tasks)
    document = json.loads(base['plan_bytes'])
    for key in ('expected_pointer_sha256', 'expected_public_policy_sha256', 'expected_source_sha256',
                'expected_encoder_sha256'):
        del document[key]
    for key, value in (('encoder_sha256', encoder_sha256), ('content_decision_sha256', content_decision_sha256),
        ('descriptor_sha256', descriptor_sha256), ('memory_sha256', memory_sha256),
        ('projection_sha256', projection_sha256), ('wrapper_sha256', wrapper_sha256),
        ('deployment_manifest_sha256', deployment_manifest_sha256), ('public_plan_sha256', public_plan_sha256),
        ('host_sha256', host_sha256), ('backend_sha256', backend_sha256)):
        document[key] = _pin(value)
    for key, value in (('bundle_root', bundle_root), ('wrapper_path', wrapper_path),
                       ('public_plan_path', public_plan_path)):
        document[key] = _path(value)
    if any(value[0].lower() != 'c' for value in (runtime_root, runtime_manifest_path, bundle_root, wrapper_path, public_plan_path)):
        _refuse('path_refused')
    if _fold(wrapper_path) != _fold(bundle_root + r'\Invoke-WdQ4Query.ps1'):
        _refuse('path_refused')
    document.update(schema='wd.q4-fixed-plan.v4', backend='content-wrapper-v1',
                    bundle_commit=_pin(bundle_commit, re.compile(r'[0-9a-f]{40}\Z')))
    raw = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(raw) > MAX_PLAN_BYTES:
        _refuse('limit_refused')
    return {'plan_bytes': raw, 'plan_sha256': hashlib.sha256(raw).hexdigest()}
