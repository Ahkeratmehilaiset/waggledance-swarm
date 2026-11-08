"""Default-off writer of the v5 public content plan (useful content route B, caller-pinned content snapshot).

``write_content_plan`` reads ONE caller-pinned v1 public wrapper plan (``wd.q4-public-wrapper-plan.v1``, the card
writer's exact v1 check), the caller-pinned content route decision and the installed content memory directory, and
writes exclusively into one existing EMPTY absolute directory ``public-plan.v5.json``
(``wd.q4-public-wrapper-plan.v5``): the v1 plan's ``config``, policy and roster paths and pins and encoder pin, plus
``content_decision_path`` / ``content_decision_sha256``, the memory and projection pins of that directory and the
seven source locations of the request (v5, RCO2 MP1 R1: the public child re-derives the projection from them before
every query, so the writer is no longer the only place the sources are checked). It references the caller's
decision, policy and source files, it copies none.

Before anything is written, every check ``wd_q4_public_query.query_content_snapshot`` makes before stdin runs here
on the same inputs: the decision verifies against the plan's policy and roster pins
(``content_memory.verify_content_route_decision``), the policy and roster read by the plan pins pass the strict
adapter, ``descriptor.json`` hashes to the plan ``config.descriptor_sha256`` and is the v2 shared descriptor under
the shared scope with the plan encoder digest, ``memory.json`` hashes to the descriptor memory pin and names the
content projection schema, ``projection.json`` hashes to the manifest projection pin. In addition the projection
must pass ``content_memory.validate_route_projection`` and the manifest route evidence must be exactly what this
decision and projection give (``check_content_route_evidence``), so a plan never pairs a memory with a decision it
was not built under. The encoder runtime revision is checked by the query against its runtime config, not here.

Source re-derivation (RCO2 MP1, Lead source GO 2026-11-08): the structural checks only prove that each record agrees
with itself, so the writer also re-reads the decision's sources and requires the pinned projection bytes to be
exactly what they derive to (``content_memory.validate_content_snapshot``): the bridge log and generation file of
the anchored message prefix (read only up to the decision's offset, prefix sha256 and generation; rows appended
later are not part of it) and the git repository holding the pinned document commit, read only with the TRUSTED
Git pin (RCO1 Q1): ``trusted_git`` is exactly ``git_executable`` (absolute) and ``git_sha256`` supplied by the
caller apart from the request (in deployment the fleet record's pin, the same source the query wrapper uses), the
request's ``doc_git_executable`` / ``doc_git_sha256`` must equal it, and a document-bearing decision without a
trusted pin refuses before any Git starts (MP3: the trusted Git is re-hashed before each call, every object
re-hashed). The writer has no fleet access, so this trust is exactly as good as the caller's reading of the fleet
record; the query path checks the same pin against the deployment-manifest-pinned fleet record on every query. The
content message policy and the document policy are read by the DECISION pins. Sources are given exactly when the
decision pins them.
The projection bytes checked are the same bytes the pins name. This runs once per written plan, not per query.

What this does NOT establish: who issued the decision (unsigned, caller-pinned; a plan written by hand outside this
writer skips the re-derivation and is caught only by a later signed release), freshness, that the memory is
installed where the wrapper runtime config points, or any activation; same-user races stay UNKNOWN. A refusal
(``ContentPlanRefused``) means nothing was written; ``ContentPlanUnknown`` means a write was attempted and the
directory must be inspected before any retry. Both carry one fixed token and no input content.

Command line: no arguments prints ``{"status":"OFF"}`` and reads nothing; ``--enabled --request PATH
--request-sha256 HEX`` reads one pinned closed request ``wd.q4-public-content-plan-request.v3`` (keys
public_plan_path, public_plan_sha256, output_dir, memory_dir, content_decision_path, content_decision_sha256,
message_source_path, message_generation_path, message_policy_path, doc_repo_root, doc_policy_path,
doc_git_executable, doc_git_sha256; v1 and v2 refuse), optionally followed by exactly
``--trusted-git-executable PATH --trusted-git-sha256 HEX`` (the trusted pin, required when the decision pins
documents), and prints the descriptor as one canonical JSON line. A refusal prints one fixed line to stderr and
exits 2 (unknown: 3).
"""
import sys

from tools import wd_q4_public_card_plan_writer as base

__all__ = ["ContentPlanRefused", "ContentPlanUnknown", "write_content_plan", "main", "PLAN_V5", "REQUEST_SCHEMA",
           "RESULT_SCHEMA", "PLAN_NAME"]

PLAN_V5 = "wd.q4-public-wrapper-plan.v5"
REQUEST_SCHEMA = "wd.q4-public-content-plan-request.v3"
RESULT_SCHEMA = "wd.q4-public-content-plan-written.v1"
PLAN_NAME = "public-plan.v5.json"
_SCOPE = "wd.q4-shared-projected-events.v1"
_DESCRIPTOR_SCHEMA = "wd.bridge-mind-q4-memory-descriptor.v2"
_ARTIFACT_LIMIT = 16 * 1024 * 1024  # == wd_q4_public_query._ARTIFACT_LIMIT
_DECISION_LIMIT = 65536  # == content_memory.MAX_DECISION and the Invoke-WdQ4Query.ps1 content plan read limit
_ROSTER_LIMIT = 1048576
_SOURCE_KEYS = ("message_source_path", "message_generation_path", "message_policy_path", "doc_repo_root",
                "doc_policy_path", "doc_git_executable", "doc_git_sha256")
_REQUEST_KEYS = frozenset({"schema", "public_plan_path", "public_plan_sha256", "output_dir", "memory_dir",
                           "content_decision_path", "content_decision_sha256", *_SOURCE_KEYS})
_REFUSAL = '{"error":"content_plan_refused"}\n'
_UNKNOWN = '{"error":"content_plan_unknown"}\n'

_exclusive = base._exclusive


class ContentPlanRefused(ValueError):
    """Nothing was written; fixed token ``content_plan_refused``."""


class ContentPlanUnknown(RuntimeError):
    """A write was attempted; fixed token ``content_plan_unknown``. Inspect the output directory before a retry."""


def _route_sources(decision, sources, trusted_git=None):
    """``(messages, documents, plan_sources)``: the route sources of ``validate_content_snapshot``, exactly those
    the decision pins, policies read by its pins, through ``content_memory.snapshot_sources`` (the same helper the
    query path uses, with the same trusted Git pin), and the normalized source locations the v5 plan carries."""
    from sandbox.mind.step2 import content_memory as content
    message_keys, doc_keys = _SOURCE_KEYS[:3], _SOURCE_KEYS[3:]
    snapshot = dict.fromkeys(content.SNAPSHOT_SOURCE_KEYS)
    plan_sources = dict.fromkeys(_SOURCE_KEYS)
    if decision["message_policy_sha256"] is None:
        if any(sources[key] is not None for key in message_keys):
            raise ValueError()
    else:
        for key in message_keys:
            plan_sources[key] = str(base._path(sources[key]))
        snapshot.update(message_source_path=plan_sources["message_source_path"],
                        message_generation_path=plan_sources["message_generation_path"],
                        message_policy_bytes=base._read_pinned(plan_sources["message_policy_path"],
                                                               decision["message_policy_sha256"]))
    if decision["doc_policy_sha256"] is None:
        if any(sources[key] is not None for key in doc_keys):
            raise ValueError()
    else:
        if not base._hex(sources["doc_git_sha256"]):
            raise ValueError()
        for key in ("doc_repo_root", "doc_policy_path", "doc_git_executable"):
            plan_sources[key] = str(base._path(sources[key]))
        plan_sources["doc_git_sha256"] = sources["doc_git_sha256"]
        snapshot.update(doc_repo_root=plan_sources["doc_repo_root"],
                        doc_policy_bytes=base._read_pinned(plan_sources["doc_policy_path"],
                                                           decision["doc_policy_sha256"]),
                        doc_git_executable=plan_sources["doc_git_executable"],
                        doc_git_sha256=plan_sources["doc_git_sha256"])
    messages, documents = content.snapshot_sources(decision, snapshot, trusted_git)
    return messages, documents, plan_sources


def _content_plan(plan, memory_dir, content_decision_path, content_decision_sha256, sources, trusted_git):
    from sandbox.mind.step2 import content_memory as content
    from sandbox.mind.step2 import shared_capture_adapter as adapter
    from sandbox.mind.step2.shared_index_preflight import _encoder_digest

    decision_raw = base._read_pinned(content_decision_path, content_decision_sha256, _DECISION_LIMIT)
    decision = content.verify_content_route_decision(decision_raw, content_decision_sha256,
                                                     policy_sha256=plan["policy_sha256"],
                                                     roster_sha256=plan["roster_sha256"])
    policy_raw = base._read_pinned(plan["policy_path"], plan["policy_sha256"])
    roster_raw = base._read_pinned(plan["roster_path"], plan["roster_sha256"], _ROSTER_LIMIT)
    adapter.strict_pins(policy_raw, plan["policy_sha256"], roster_raw, plan["roster_sha256"])
    directory = base._path(memory_dir)
    descriptor = base._json(base._read_pinned(str(directory / "descriptor.json"),
                                              plan["config"]["descriptor_sha256"]))
    if (type(descriptor) is not dict or set(descriptor) != {"schema", "scope", "memory_sha256", "dim", "encoder"}
            or descriptor["schema"] != _DESCRIPTOR_SCHEMA or descriptor["scope"] != _SCOPE
            or type(descriptor["dim"]) is not int or descriptor["dim"] < 1
            or _encoder_digest(descriptor["encoder"]) != plan["encoder_sha256"]):
        raise ValueError()
    manifest = base._json(base._read_pinned(str(directory / "memory.json"), descriptor["memory_sha256"],
                                            _ARTIFACT_LIMIT))
    if type(manifest) is not dict or manifest.get("projection_schema") != content.SCHEMA:
        raise ValueError()
    projection_raw = base._read_pinned(str(directory / "projection.json"), manifest["projection_sha256"],
                                       _ARTIFACT_LIMIT)
    projection = base._json(projection_raw)
    content.validate_route_projection(projection)  # ContentMemoryRefused is a ValueError
    content.check_content_route_evidence(manifest.get("content_route"), projection, decision,
                                         content_decision_sha256)
    messages, documents, plan_sources = _route_sources(decision, sources, trusted_git)
    checked = content.validate_content_snapshot(
        enabled=True, projection_bytes=projection_raw, projection_sha256=manifest["projection_sha256"],
        messages=messages, documents=documents, decision_bytes=decision_raw, decision_sha256=content_decision_sha256,
        policy_bytes=policy_raw, policy_sha256=plan["policy_sha256"], roster_bytes=roster_raw,
        roster_sha256=plan["roster_sha256"])
    if checked.get("status") != "MATCH":
        raise ValueError()
    return {"schema": PLAN_V5, "config": dict(plan["config"]), "policy_path": plan["policy_path"],
            "roster_path": plan["roster_path"], "policy_sha256": plan["policy_sha256"],
            "roster_sha256": plan["roster_sha256"], "encoder_sha256": plan["encoder_sha256"],
            "content_decision_path": str(base._path(content_decision_path)),
            "content_decision_sha256": content_decision_sha256, "memory_sha256": descriptor["memory_sha256"],
            "projection_sha256": manifest["projection_sha256"], **plan_sources}


def write_content_plan(*, enabled=False, public_plan_path=None, expected_public_plan_sha256=None, output_dir=None,
                       memory_dir=None, content_decision_path=None, content_decision_sha256=None,
                       message_source_path=None, message_generation_path=None, message_policy_path=None,
                       doc_repo_root=None, doc_policy_path=None, doc_git_executable=None, doc_git_sha256=None,
                       trusted_git=None):
    """Write the v5 content plan; see the module doc. ``enabled`` must be the bool ``True``; ``trusted_git`` is
    the trusted Git pin (``git_executable``, ``git_sha256``) or None."""
    if enabled is False:
        return {"status": "OFF"}
    attempted = failed = False
    result = None
    try:
        if enabled is not True:
            raise ValueError()
        plan = base._v1_plan(base._read_pinned(public_plan_path, expected_public_plan_sha256))
        sources = {"message_source_path": message_source_path, "message_generation_path": message_generation_path,
                   "message_policy_path": message_policy_path, "doc_repo_root": doc_repo_root,
                   "doc_policy_path": doc_policy_path, "doc_git_executable": doc_git_executable,
                   "doc_git_sha256": doc_git_sha256}
        if trusted_git is not None and type(trusted_git) is not dict:
            raise ValueError()
        document = _content_plan(plan, memory_dir, content_decision_path, content_decision_sha256, sources,
                                 trusted_git)
        out = base._path(output_dir)
        if not out.is_dir() or any(out.iterdir()):
            raise ValueError()
        plan_path = out / PLAN_NAME
        plan_raw = base._encode(document)
        if len(plan_raw) > base._LIMIT:
            raise ValueError()
        attempted = True
        _exclusive(plan_path, plan_raw)
        if base._read_pinned(str(plan_path), base._sha(plan_raw)) != plan_raw:
            raise ValueError()
        result = {"schema": RESULT_SCHEMA, "status": "WRITTEN", "base_public_plan_sha256": expected_public_plan_sha256,
                  "public_plan_path": str(plan_path), "public_plan_sha256": base._sha(plan_raw),
                  "content_decision_sha256": content_decision_sha256, "memory_sha256": document["memory_sha256"],
                  "projection_sha256": document["projection_sha256"], "source_rederivation": "MATCH",
                  "authority": "none", "issuer_authentication": "UNKNOWN"}
    except Exception:
        failed = True
    if failed:
        if attempted:
            raise ContentPlanUnknown("content_plan_unknown")
        raise ContentPlanRefused("content_plan_refused")
    return result


def _request(path, pin):
    document = base._json(base._read_pinned(path, pin))
    if type(document) is not dict or set(document) != _REQUEST_KEYS or document["schema"] != REQUEST_SCHEMA:
        raise ValueError()
    return {"public_plan_path": document["public_plan_path"],
            "expected_public_plan_sha256": document["public_plan_sha256"], "output_dir": document["output_dir"],
            "memory_dir": document["memory_dir"], "content_decision_path": document["content_decision_path"],
            "content_decision_sha256": document["content_decision_sha256"],
            **{key: document[key] for key in _SOURCE_KEYS}}


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv == []:
        sys.stdout.write('{"status":"OFF"}\n')
        return 0
    try:
        if len(argv) not in (5, 9) or argv[:2] != ["--enabled", "--request"] or argv[3] != "--request-sha256":
            raise ContentPlanRefused("content_plan_refused")
        trusted = None
        if len(argv) == 9:
            if argv[5] != "--trusted-git-executable" or argv[7] != "--trusted-git-sha256":
                raise ContentPlanRefused("content_plan_refused")
            trusted = {"git_executable": argv[6], "git_sha256": argv[8]}
        try:
            arguments = _request(argv[2], argv[4])
        except Exception:
            arguments = None
        if arguments is None:
            raise ContentPlanRefused("content_plan_refused")
        result = write_content_plan(enabled=True, trusted_git=trusted, **arguments)
        sys.stdout.write(base._encode(result).decode("ascii") + "\n")
        sys.stdout.flush()
        return 0
    except ContentPlanUnknown:
        return base._fail(_UNKNOWN, 3)
    except Exception:
        return base._fail(_REFUSAL, 2)


if __name__ == "__main__":
    sys.exit(main())
