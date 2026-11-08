"""Default-off runtime-plan caller for one frozen staged v3 or content v4 fixed plan.

``plan_staged_runtime`` checks the caller pin of the fixed-plan bytes, parses them with the transport's own closed
parser (``wd_q4_secure_transport._fixed_plan``) and requires either the staged card backend
(``wd.q4-fixed-plan.v3``, ``staged-card-wrapper-v1``) or the content backend
(``wd.q4-fixed-plan.v4``, ``content-wrapper-v1``). It then calls
``build_q4_runtime_plan.plan_runtime_inputs`` with values taken from that plan, not from the caller:
``commit = bundle_commit``, ``generation = runtime_generation``, ``revision = runtime_revision`` and
the selected profile's backend SHA256 = ``backend_sha256``. So the planned code is the committed blobs of the bundle commit,
the selected path-loaded fixed backend and its import closure are pinned, and the committed helper blob must hash to
the plan's backend pin. The caller still names the code roots, site and model trees, model files, weights digest
and byte budget, exactly as for ``plan_runtime_inputs``.

The result carries the planner output unchanged under ``runtime_plan`` plus the fixed-plan digest, the bundle
commit, the backend pin and the plan's ``runtime_manifest_sha256`` as the manifest the later candidate build must
reproduce. It is an unverified candidate input: nothing is built, installed, written or activated here, the
fixed plan's issuer is not authenticated, and the bundle copy of the helper is checked by the transport, not here.
A refusal carries one fixed token: the planner's own token, ``fixed_plan_refused`` or
``staged_runtime_plan_refused``.

Command line: no arguments prints ``{"status":"OFF"}``; ``--enabled --request PATH --request-sha256 HEX`` reads
one pinned closed request (``wd.q4-staged-runtime-plan-request.v1``) and the fixed plan it names under its pin,
and prints the result as one canonical JSON line; a refusal prints ``{"error": TOKEN}`` to stderr and exits 2.
"""
import hashlib
import sys

__all__ = ["StagedRuntimePlanRefused", "plan_staged_runtime", "main", "SCHEMA", "REQUEST_SCHEMA"]

SCHEMA = "wd.q4-staged-runtime-plan.v1"
REQUEST_SCHEMA = "wd.q4-staged-runtime-plan-request.v1"
_FIXED_PLAN_LIMIT = 4096  # the transport's _MAX_PLAN
_REQUEST_LIMIT = 65536
_PLANNER_ARGS = ("git_executable", "repository", "code_roots", "extra_code_paths", "site_root", "model_root",
                 "expected_model_files", "expected_weights_sha256", "max_bytes", "timeout_seconds",
                 "expected_site_pins")
_REQUEST_KEYS = frozenset({"schema", "fixed_plan_path", "fixed_plan_sha256", *_PLANNER_ARGS})


class StagedRuntimePlanRefused(ValueError):
    """One fixed token; no path, file content or input text."""


def _staged_fixed_plan(plan_bytes, expected_sha256):
    from tools import wd_q4_secure_transport as transport

    if (type(plan_bytes) is not bytes or type(expected_sha256) is not str
            or hashlib.sha256(plan_bytes).hexdigest() != expected_sha256):
        raise ValueError()
    plan = transport._fixed_plan(plan_bytes)  # closed canonical schema, pins and paths
    if (plan["schema"], plan["backend"]) not in (("wd.q4-fixed-plan.v3", "staged-card-wrapper-v1"),
                                                  ("wd.q4-fixed-plan.v4", "content-wrapper-v1")):
        raise ValueError()
    return plan


def plan_staged_runtime(*, enabled=False, fixed_plan_bytes=None, expected_fixed_plan_sha256=None,
                        git_executable=None, repository=None, code_roots=None, site_root=None, model_root=None,
                        expected_model_files=None, expected_weights_sha256=None, max_bytes=None,
                        extra_code_paths=(), expected_site_pins=None, timeout_seconds=60):
    """Plan the runtime inputs of one frozen staged fixed plan; see the module doc. ``enabled`` must be True."""
    if enabled is False:
        return {"status": "OFF"}
    token = "staged_runtime_plan_refused"
    try:
        if enabled is not True:
            raise ValueError()
        token = "fixed_plan_refused"
        plan = _staged_fixed_plan(fixed_plan_bytes, expected_fixed_plan_sha256)
        token = "staged_runtime_plan_refused"
        from tools import build_q4_runtime_plan as planner

        try:
            profile_pin = ({"content_backend_sha256": plan["backend_sha256"]}
                           if plan["backend"] == "content-wrapper-v1"
                           else {"staged_backend_sha256": plan["backend_sha256"]})
            runtime = planner.plan_runtime_inputs(
                enabled=True, git_executable=git_executable, repository=repository, commit=plan["bundle_commit"],
                code_roots=code_roots, site_root=site_root, model_root=model_root,
                expected_model_files=expected_model_files, expected_weights_sha256=expected_weights_sha256,
                generation=plan["runtime_generation"], revision=plan["runtime_revision"], max_bytes=max_bytes,
                extra_code_paths=extra_code_paths, expected_site_pins=expected_site_pins,
                timeout_seconds=timeout_seconds, **profile_pin)
        except planner.RuntimePlanRefused as exc:
            token = exc.args[0]  # the planner's fixed vocabulary
            raise ValueError() from None
        if plan["backend"] == "content-wrapper-v1":
            token = "content_profile_mismatch"
            profile = runtime["content_runtime"]
            if (profile["transport"]["sha256"] != plan["peer_code_sha256"]
                    or profile["verifier"]["sha256"] != plan["verifier_sha256"]):
                raise ValueError()
        return {"schema": SCHEMA, "status": "CANDIDATE_UNVERIFIED", "fixed_plan_sha256": expected_fixed_plan_sha256,
                "bundle_commit": plan["bundle_commit"], "backend_sha256": plan["backend_sha256"],
                "expected_runtime_manifest_sha256": plan["runtime_manifest_sha256"], "runtime_plan": runtime}
    except Exception:
        pass
    raise StagedRuntimePlanRefused(token)


def _request(path, pin):
    from tools.wd_q4_public_card_plan_writer import _json, _read_pinned

    document = _json(_read_pinned(path, pin, _REQUEST_LIMIT))
    if type(document) is not dict or set(document) != _REQUEST_KEYS or document["schema"] != REQUEST_SCHEMA:
        raise ValueError()
    plan_bytes = _read_pinned(document["fixed_plan_path"], document["fixed_plan_sha256"], _FIXED_PLAN_LIMIT)
    return dict(fixed_plan_bytes=plan_bytes, expected_fixed_plan_sha256=document["fixed_plan_sha256"],
                **{name: document[name] for name in _PLANNER_ARGS})


def _emit(stream, text):
    try:
        stream.write(text)
        stream.flush()
    except Exception:
        pass


def main(argv=None):
    from tools.wd_q4_public_card_plan_writer import _encode

    if argv is None:
        argv = sys.argv[1:]
    if argv == []:
        _emit(sys.stdout, '{"status":"OFF"}\n')
        return 0
    token = "staged_runtime_plan_refused"
    try:
        if len(argv) != 5 or argv[0] != "--enabled" or argv[1] != "--request" or argv[3] != "--request-sha256":
            raise ValueError()
        arguments = _request(argv[2], argv[4])
        try:
            result = plan_staged_runtime(enabled=True, **arguments)
        except StagedRuntimePlanRefused as exc:
            token = exc.args[0]
            raise
        _emit(sys.stdout, _encode(result).decode("ascii") + "\n")
        return 0
    except Exception:
        _emit(sys.stderr, _encode({"error": token}).decode("ascii") + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
