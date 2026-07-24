# SPDX-License-Identifier: BUSL-1.1
"""W2B SeedSolverAdapterV1 -- deterministic, advisory PDAM close adapter.

``SeedSolverAdapterV1.execute`` validates a fully-bound ``SeedWorkEnvelopeV1``
against ONE immutable caller-supplied registry snapshot (whose opaque
``generation`` and whole-registry ``head_digest`` MUST equal the envelope's
bindings -- any acquisition failure, change, or mismatch rejects BEFORE the
solver runs, with no fallback and no partial result), then reruns the pinned
``wd.pdam_close.v1`` solver and records its deterministic advisory actions.

``verify_seed_solver_replay`` re-derives the solver run independently from the
envelope's validated request and the exact same snapshot, and requires the
recomputed canonical actions to EXACTLY equal the result's actions. The result
is advisory only (``advisory_only=True``, ``external_writes_applied=False``);
it carries no authority and is not a MAGMA authenticity claim.

Stable-input policy is ``caller_owned_quiescent.v1`` (see seed_work_contracts):
inputs are decoded objects that the caller must hold quiescent; this pure
adapter cannot coordinate external writers and claims no cross-object atomicity.
"""

from __future__ import annotations

from typing import Optional

from waggledance.core.pdam_close_solver import (
    LogbookEntry, MesComment, PlannedCloseAction, ToolState, plan_close_actions,
)
from waggledance.core.seed_work_contracts import (
    ACTION_SCHEMA_VERSION, RESULT_SCHEMA, SOLVER_ID, SeedWorkContractError,
    derive_evidence_digest, derive_result_digest, parse_registry_snapshot,
    parse_seed_work_envelope, parse_seed_work_result, parse_utc,
    require_result_output_within_bound, require_snapshot_membership,
)


class SeedSolverAdapterError(Exception):
    """The adapter refuses to produce a result (fail-closed, no partial)."""


def _as_of_is_canonical(as_of_utc: object) -> bool:
    """Require the EXTERNAL as_of argument to be an exact ``str`` AND a canonical
    aware-UTC-Z instant BEFORE it is used in any equality or datetime arithmetic.
    A hostile ``__eq__`` type (or a non-canonical lexeme) must not slip through the
    ``as_of_utc == envelope`` comparison, and the solver's window arithmetic must
    never run on an unvalidated value."""
    if type(as_of_utc) is not str:
        return False
    try:
        parse_utc(as_of_utc, "as_of_utc")
    except SeedWorkContractError:
        return False
    return True


def _bind_snapshot(envelope: dict, registry_snapshot: object) -> dict:
    """Parse the caller-supplied snapshot and require it to match the envelope's
    generation + head digest EXACTLY. Reject before the solver runs."""
    try:
        snap = parse_registry_snapshot(registry_snapshot)
    except SeedWorkContractError as exc:
        raise SeedSolverAdapterError(f"registry snapshot rejected: {exc}") from exc
    if snap["generation"] != envelope["registry_generation"]:
        raise SeedSolverAdapterError("registry snapshot generation != envelope binding")
    if snap["head_digest"] != envelope["registry_head_digest"]:
        raise SeedSolverAdapterError("registry snapshot head_digest != envelope binding")
    # The envelope's cell + every proof entry must be MEMBERS of this snapshot
    # (closes the cross-snapshot relation forgery at the live boundary too).
    try:
        require_snapshot_membership(
            identity=envelope["identity"], proof=tuple(envelope["lineage_proof"]), snapshot=snap)
    except SeedWorkContractError as exc:
        raise SeedSolverAdapterError(f"snapshot membership rejected: {exc}") from exc
    return snap


def _logbook(entry: dict) -> LogbookEntry:
    return LogbookEntry(
        entry_id=entry["entry_id"], local_id=entry["local_id"],
        log_code=entry["log_code"], device=entry["device"], status=entry["status"],
        created_at=parse_utc(entry["created_at"], "entry.created_at"),
        issue=entry.get("issue", ""), action=entry.get("action", ""),
    )


def _pdam_actions(request: dict, as_of_utc: str) -> list[dict]:
    """Deterministically rerun the pinned solver from a VALIDATED request and
    return canonical action dicts. This is the single shared solver path used by
    both execute and verify_seed_solver_replay (no divergence)."""
    entries = [_logbook(e) for e in request["entries"]]
    timeline = [_logbook(e) for e in request["repair_timeline"]]
    tool_states = {
        k: ToolState(tool_id=v["tool_id"], state=v["state"], substate=v.get("substate", ""),
                     comment=v.get("comment", ""), updated_by=v.get("updated_by", ""))
        for k, v in request["tool_states"].items()
    }
    comments = [
        MesComment(tool_id=c["tool_id"], when=parse_utc(c["when"], "comment.when"),
                   by_user=c["by_user"], comment=c["comment"], stage=c.get("stage", ""))
        for c in request["comments"]
    ]
    subtools = {k: list(v) for k, v in request["subtools"].items()}
    opts = request["options"]
    actions: list[PlannedCloseAction] = plan_close_actions(
        entries=entries, repair_timeline=timeline, tool_states=tool_states,
        comments=comments, subtools=subtools,
        duplicate_window_hours=opts["duplicate_window_hours"],
        evidence_limit=opts["evidence_limit"],
        max_open_window_days=opts["max_open_window_days"],
        now=parse_utc(as_of_utc, "as_of_utc"),
    )
    return [
        {"entry_id": a.entry_id, "device": a.device, "kind": a.kind,
         "current_status": a.current_status, "target_status": a.target_status,
         "action_text": a.action_text, "duplicate_of": a.duplicate_of}
        for a in actions
    ]


class SeedSolverAdapterV1:
    """Stateless, deterministic PDAM close adapter. No I/O, no clock, no
    registration, no authority."""

    solver_id = SOLVER_ID

    def execute(self, *, envelope: object, registry_snapshot: object, as_of_utc: str) -> dict:
        """Verify the envelope + snapshot binding, rerun the pinned solver, and
        return a fully-bound SeedWorkResultV1 wire dict (advisory only)."""
        try:
            env = parse_seed_work_envelope(envelope)
        except SeedWorkContractError as exc:
            raise SeedSolverAdapterError(f"envelope rejected: {exc}") from exc
        # EXTERNAL as_of: exact-str + canonical BEFORE equality/arithmetic.
        if not _as_of_is_canonical(as_of_utc):
            raise SeedSolverAdapterError("as_of_utc must be a canonical ISO-8601 UTC-Z str")
        if as_of_utc != env["as_of_utc"]:
            raise SeedSolverAdapterError("as_of_utc != envelope as_of_utc")
        snap = _bind_snapshot(env, registry_snapshot)
        # PRE-SOLVER output bound (producer amplification): a valid max-cardinality
        # request can pass MAX_REQUEST_BYTES / MAX_ENTRIES yet drive the solver to
        # emit a multi-megabyte ResultV1 (one action per open entry, each action
        # reusing the same bounded evidence pool). Reject on a conservative,
        # input-derived upper bound on the result-output size BEFORE _pdam_actions
        # runs, so the solver is never invoked and no provisional over-cap result is
        # ever constructed. The post-result MAX_RESULT_BYTES validation below is
        # retained as defense in depth.
        try:
            require_result_output_within_bound(env["request_payload"])
        except SeedWorkContractError as exc:
            raise SeedSolverAdapterError(f"result output bound exceeded: {exc}") from exc
        # Any solver failure (e.g. a datetime-arithmetic OverflowError near the
        # calendar edge) is wrapped fail-closed -- never leaked, never a partial
        # result. The OUTPUT side is additionally bounded by validating the
        # provisional ResultV1 below (MAX_RESULT_BYTES + full self-consistency).
        try:
            actions = _pdam_actions(env["request_payload"], env["as_of_utc"])
        except SeedWorkContractError as exc:
            raise SeedSolverAdapterError(f"solver input rejected: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 -- fail-closed on any solver failure
            raise SeedSolverAdapterError(f"solver run failed: {exc}") from exc
        cell_id = env["identity"]["cell_id"]
        lineage_entry_hash = env["lineage"]["entry_hash"]
        result_digest = derive_result_digest(
            envelope_id=env["envelope_id"], cell_id=cell_id,
            lineage_entry_hash=lineage_entry_hash, request_digest=env["request_digest"],
            solver_contract_digest=env["solver_contract_digest"],
            solver_config_digest=env["solver_config_digest"],
            registry_generation=snap["generation"], actions=actions,
            registry_head_digest=env["registry_head_digest"], as_of_utc=env["as_of_utc"])
        evidence_digest = derive_evidence_digest(
            envelope_id=env["envelope_id"], cell_id=cell_id,
            request_digest=env["request_digest"],
            registry_head_digest=env["registry_head_digest"], result_digest=result_digest)
        result = {
            "schema_version": RESULT_SCHEMA,
            "envelope_id": env["envelope_id"],
            "cell_id": cell_id,
            "lineage_entry_hash": lineage_entry_hash,
            "solver_id": SOLVER_ID,
            "solver_contract_digest": env["solver_contract_digest"],
            "solver_config_digest": env["solver_config_digest"],
            "request_digest": env["request_digest"],
            "registry_generation": snap["generation"],
            "registry_head_digest": snap["head_digest"],
            "as_of_utc": env["as_of_utc"],
            "action_schema_version": ACTION_SCHEMA_VERSION,
            "actions": actions,
            "result_digest": result_digest,
            "evidence_digest": evidence_digest,
            "advisory_only": True,
            "external_writes_applied": False,
        }
        # Validate the provisional ResultV1 before returning (fail-closed): this
        # enforces MAX_RESULT_BYTES against amplification and re-derives every
        # digest, so the adapter can never emit a result that would not itself pass
        # the wire verifier.
        try:
            parse_seed_work_result(result)
        except SeedWorkContractError as exc:
            raise SeedSolverAdapterError(f"produced result failed contract: {exc}") from exc
        return result


def verify_seed_solver_replay(envelope: object, result: object,
                              registry_snapshot: object, as_of_utc: str) -> tuple[bool, Optional[str]]:
    """Independent deterministic replay: rerun the pinned solver from the
    envelope's validated request + the exact same snapshot and require the
    recomputed canonical actions to EXACTLY equal the result's actions. Binds the
    FULL result<->envelope echo, the snapshot membership, and both solver digests
    BEFORE trusting the actions. Fail-closed."""
    from waggledance.core.seed_work_contracts import (
        evidence_binding_consistent, parse_seed_work_result,
    )

    try:
        env = parse_seed_work_envelope(envelope)
        res = parse_seed_work_result(result)
    except SeedWorkContractError as exc:
        return False, str(exc)
    # EXTERNAL as_of: exact-str + canonical BEFORE equality/arithmetic (a hostile
    # __eq__ or non-canonical lexeme must not pass the comparison or reach the
    # solver's window arithmetic).
    if not _as_of_is_canonical(as_of_utc):
        return False, "as_of_utc must be a canonical ISO-8601 UTC-Z str"
    if as_of_utc != env["as_of_utc"]:
        return False, "as_of_utc != envelope as_of_utc"
    # FULL result<->envelope echo binding (envelope_id, BOTH solver digests,
    # request_digest, registry_generation, registry_head_digest, as_of_utc,
    # cell_id, lineage_entry_hash). A result is self-consistent about its OWN
    # fields -- its result/evidence digests recompute over them -- so binding only
    # envelope_id + solver digests let a self-consistent result silently alter
    # cell_id / lineage_entry_hash / request_digest / registry_generation /
    # registry_head_digest / as_of_utc and still "replay". Delegate to the shared
    # helper over the ALREADY-PARSED per-call env/res (no re-parse of the mutable
    # originals) so replay and evidence-integrity cannot drift.
    ok, reason = evidence_binding_consistent(env, res)
    if not ok:
        return False, f"result does not bind this envelope: {reason}"
    try:
        _bind_snapshot(env, registry_snapshot)
    except SeedSolverAdapterError as exc:
        return False, str(exc)
    # PRE-SOLVER output bound (defense in depth): refuse to replay a request whose
    # conservative result-output upper bound exceeds MAX_RESULT_BYTES, so a
    # max-cardinality amplifier envelope cannot force the verifier to build a
    # multi-megabyte in-memory action set before comparison.
    try:
        require_result_output_within_bound(env["request_payload"])
    except SeedWorkContractError as exc:
        return False, str(exc)
    # Rerun the solver fail-closed: any failure (e.g. datetime-arithmetic overflow)
    # returns False rather than leaking an exception out of the verifier.
    try:
        replay = _pdam_actions(env["request_payload"], env["as_of_utc"])
    except Exception as exc:  # noqa: BLE001 -- verifier never leaks
        return False, f"solver replay failed: {exc}"
    if replay != res["actions"]:
        return False, "replay actions do not match result actions"
    return True, None
