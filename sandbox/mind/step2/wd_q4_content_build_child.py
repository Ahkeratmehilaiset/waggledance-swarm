"""Default-off content-memory build child (route B; 4h memory preparation, Lead 2026-11-08 16:58Z).

``build_content_generation`` builds ONE route B content memory (``content_memory.build_route_content_memory``) from
one caller-pinned closed plan ``wd.q4-content-build-plan.v1`` with the fixed E5 encoder path of
``wd_q4_prefix_build_child`` (the model root bound to the caller's trusted fixed-local root, every asset hashed
against the plan manifest, the identity derived from the pinned directory, the offline load with the process
environment restored) and the Git the caller passes as ``trusted_git`` (RCO1 Q1: the fleet record's pin, never a
plan value; the plan names a repository root and policy paths only and has no Git field). Before this child a
content memory could be built only with the synthetic Spy encoder of the tests: ``build_route_content_memory``
takes ``encode``, ``identity`` and ``dim`` from its caller and no production caller existed.

Order, fail closed, one fixed token each and nothing written before the build step: disabled -> plan pin
(``plan_refused``) -> plan shape -> request shape (absolute output root, absolute trusted model root, trusted Git
pin shape; ``request_refused``) -> the decision, the non-review policy and the roster read by their plan pins and
the decision verified against those pins (``sources_refused`` or the decision's own token) -> the route sources
through ``content_memory.snapshot_sources`` with the trusted Git (a document-bearing decision without a trusted pin
is ``git_pin_unavailable`` before any subprocess) -> model root (``model_root_refused``) -> assets
(``assets_refused``) -> identity (``identity_refused``) -> encoder (``encoder_refused``) -> build (the build's own
token). The report carries the memory, descriptor and projection digests, the record and document counts, the
anchored prefix size and the build seconds; ``authority`` is ``none`` and ``publisher_authentication`` is ``none``:
nothing is published, pointed to, installed or activated here. The real build runs only under an
operator-approved pinned policy and decision; the tests use synthetic sources and a synthetic backend.
"""
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path

from sandbox.mind.step2 import content_memory as content
from sandbox.mind.step2 import wd_q4_prefix_build_child as prefix

__all__ = ["ContentBuildRefused", "build_content_generation", "PLAN_SCHEMA", "PLAN_KEYS", "MESSAGE_KEYS",
           "DOCUMENT_KEYS", "REASONS", "REPORT_KEYS"]

PLAN_SCHEMA = "wd.q4-content-build-plan.v1"
PLAN_LIMIT = 1 << 16
POLICY_LIMIT = 1 << 20
PLAN_KEYS = frozenset({"schema", "name", "encoder", "decision_path", "decision_sha256", "nonreview_policy_path",
                       "nonreview_policy_sha256", "roster_path", "roster_sha256", "messages", "documents"})
MESSAGE_KEYS = frozenset({"source_path", "generation_path", "policy_path"})
DOCUMENT_KEYS = frozenset({"repo_root", "policy_path"})
REASONS = frozenset({"disabled", "plan_refused", "request_refused", "sources_refused", "git_pin_unavailable",
                     "model_root_refused", "assets_refused", "identity_refused", "encoder_refused", "build_refused"})
REPORT_KEYS = frozenset({"schema", "status", "name", "memory_dir", "plan_sha256", "decision_sha256", "memory_sha256",
                         "descriptor_sha256", "projection_sha256", "projection_bytes", "records", "document_count",
                         "message_prefix_bytes", "encoder", "trusted_git_sha256", "build_seconds", "authority",
                         "publisher_authentication"})
REPORT_SCHEMA = "wd.q4-content-build-report.v1"
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class ContentBuildRefused(ValueError):
    """Nothing was built; the message is one fixed token and never input content."""


def _hex64(value):
    return type(value) is str and content.HEX64.fullmatch(value) is not None


def _absolute(value):
    return type(value) is str and bool(value) and os.path.isabs(value)


def _encoder_shape(encoder):
    return (type(encoder) is dict and frozenset(encoder) == prefix.ENCODER_KEYS
            and encoder["model"] == prefix.ENCODER_MODEL and type(encoder["revision"]) is str
            and prefix._HEX40.fullmatch(encoder["revision"]) is not None and _hex64(encoder["weights_sha256"])
            and type(encoder["dim"]) is int and encoder["dim"] == prefix.ENCODER_DIM
            and _absolute(encoder["model_dir"]) and prefix._assets_shape(encoder["assets"]))


def _plan_shape(plan):
    if type(plan) is not dict or frozenset(plan) != PLAN_KEYS or plan["schema"] != PLAN_SCHEMA:
        return False
    if type(plan["name"]) is not str or _NAME.fullmatch(plan["name"]) is None or not _encoder_shape(plan["encoder"]):
        return False
    for key in ("decision_path", "nonreview_policy_path", "roster_path"):
        if not _absolute(plan[key]):
            return False
    for key in ("decision_sha256", "nonreview_policy_sha256", "roster_sha256"):
        if not _hex64(plan[key]):
            return False
    messages, documents = plan["messages"], plan["documents"]
    if messages is None and documents is None:
        return False
    if messages is not None and (type(messages) is not dict or frozenset(messages) != MESSAGE_KEYS
                                 or not all(_absolute(messages[key]) for key in MESSAGE_KEYS)):
        return False
    if documents is not None and (type(documents) is not dict or frozenset(documents) != DOCUMENT_KEYS
                                  or not all(_absolute(documents[key]) for key in DOCUMENT_KEYS)):
        return False
    return True


def _read_pinned(path, pin, limit):
    """The bytes at an absolute path when they hash to the lowercase-hex pin within the limit; ValueError otherwise."""
    if not _absolute(path) or not _hex64(pin):
        raise ValueError()
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), pin):
        raise ValueError()
    return raw


def _refuse(token):
    raise ContentBuildRefused(token)


def _sources(plan, decision, trusted_git):
    """``(messages, documents)`` of ``build_route_content_memory`` through ``content_memory.snapshot_sources``: the
    policies read by the DECISION pins, the Git exactly the trusted pin."""
    sources = dict.fromkeys(content.SNAPSHOT_SOURCE_KEYS)
    messages, documents = plan["messages"], plan["documents"]
    if (messages is None) != (decision["message_policy_sha256"] is None):
        _refuse("sources_refused")
    if (documents is None) != (decision["doc_policy_sha256"] is None):
        _refuse("sources_refused")
    try:
        if messages is not None:
            sources.update(message_source_path=messages["source_path"],
                           message_generation_path=messages["generation_path"],
                           message_policy_bytes=_read_pinned(messages["policy_path"],
                                                             decision["message_policy_sha256"], POLICY_LIMIT))
        if documents is not None:
            if trusted_git is None:
                _refuse("git_pin_unavailable")
            sources.update(doc_repo_root=documents["repo_root"],
                           doc_policy_bytes=_read_pinned(documents["policy_path"], decision["doc_policy_sha256"],
                                                         POLICY_LIMIT),
                           doc_git_executable=trusted_git["git_executable"], doc_git_sha256=trusted_git["git_sha256"])
    except (OSError, ValueError, TypeError) as failure:
        if isinstance(failure, ContentBuildRefused):
            raise
        _refuse("sources_refused")
    return content.snapshot_sources(decision, sources, trusted_git)


def build_content_generation(*, enabled=False, plan_bytes=None, expected_plan_sha256=None, trusted_model_root=None,
                             trusted_git=None, root=None):
    """Build one content memory; see the module doc. ``enabled`` must be the bool ``True``."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        _refuse("request_refused")
    if (type(plan_bytes) is not bytes or len(plan_bytes) > PLAN_LIMIT or not _hex64(expected_plan_sha256)
            or not hmac.compare_digest(hashlib.sha256(plan_bytes).hexdigest(), expected_plan_sha256)):
        _refuse("plan_refused")
    try:
        plan = prefix._parse(plan_bytes)
    except prefix.BuildChildRefused:
        plan = None
    if plan is None or not _plan_shape(plan):
        _refuse("plan_refused")
    if not (prefix._absolute_root(root) and prefix._absolute_root(trusted_model_root)):
        _refuse("request_refused")
    if trusted_git is not None and not content._trusted_git_pin(trusted_git):
        _refuse("request_refused")
    try:
        decision_bytes = _read_pinned(plan["decision_path"], plan["decision_sha256"], content.MAX_DECISION)
        policy_bytes = _read_pinned(plan["nonreview_policy_path"], plan["nonreview_policy_sha256"], POLICY_LIMIT)
        roster_bytes = _read_pinned(plan["roster_path"], plan["roster_sha256"], POLICY_LIMIT)
    except (OSError, ValueError):
        _refuse("sources_refused")
    try:
        decision = content.verify_content_route_decision(decision_bytes, plan["decision_sha256"],
                                                         policy_sha256=plan["nonreview_policy_sha256"],
                                                         roster_sha256=plan["roster_sha256"])
        messages, documents = _sources(plan, decision, trusted_git)
    except content.ContentMemoryRefused as refusal:
        raise ContentBuildRefused(str(refusal)) from None
    encoder = plan["encoder"]
    files = prefix._model_root_bound(encoder["model_dir"], trusted_model_root, root, root)
    if files is None:
        _refuse("model_root_refused")
    if not prefix._assets_bound(encoder["model_dir"], encoder["assets"], files):
        _refuse("assets_refused")
    identity = prefix._identity(encoder)
    if identity is None:
        _refuse("identity_refused")
    encode = prefix._encoder(encoder, identity)
    if encode is None:
        _refuse("encoder_refused")
    started = time.monotonic()
    try:
        built = content.build_route_content_memory(
            enabled=True, messages=messages, documents=documents, decision_bytes=decision_bytes,
            decision_sha256=plan["decision_sha256"], policy_bytes=policy_bytes,
            policy_sha256=plan["nonreview_policy_sha256"], roster_bytes=roster_bytes,
            roster_sha256=plan["roster_sha256"], encode=encode, identity=identity, dim=encoder["dim"],
            root=Path(root), name=plan["name"], trusted_git=trusted_git)
    except content.ContentMemoryRefused as refusal:
        raise ContentBuildRefused(str(refusal)) from None
    except Exception:  # noqa: BLE001 - one fixed token, never a message from the backend or the file system
        raise ContentBuildRefused("build_refused") from None
    seconds = time.monotonic() - started
    memory_dir = Path(root) / plan["name"]
    projection_raw = (memory_dir / "projection.json").read_bytes()
    projection = json.loads(projection_raw)
    documents_part = projection.get("documents")
    return {"schema": REPORT_SCHEMA, "status": "BUILT", "name": plan["name"], "memory_dir": str(memory_dir),
            "plan_sha256": expected_plan_sha256, "decision_sha256": plan["decision_sha256"],
            "memory_sha256": built["memory_sha256"], "descriptor_sha256": built["descriptor_sha256"],
            "projection_sha256": hashlib.sha256(projection_raw).hexdigest(), "projection_bytes": len(projection_raw),
            "records": built["records"],
            "document_count": 0 if documents_part is None else len(documents_part["records"]),
            "message_prefix_bytes": None if messages is None else messages["end_cursor"].offset,
            "encoder": identity, "trusted_git_sha256": None if trusted_git is None else trusted_git["git_sha256"],
            "build_seconds": round(seconds, 3), "authority": "none", "publisher_authentication": "none"}
