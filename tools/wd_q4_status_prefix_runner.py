"""Explicit one-shot status-only prefix build; no scheduler or live wiring.

The caller supplies route bytes and their externally established pin, the build
plan, anchor and exact pinned policy/roster bytes. This module authenticates
none of them. It never reads a source row or logs inputs; the existing child
reads raw rows in-process for prefix verification and filtering, including rows
which are not public. Caller must restrict access to that process and private
state. A hash is not source authentication, freshness or complete history.

Two exclusive per-root pending markers serialize cooperating invocations of
this runner. An exception or interruption after reservation leaves any markers
in place: both roots require external reconciliation and no automatic retry is
performed. The markers also remain after a successful return: this is a single
bounded run, not an automatic continuation or continuous indexer. Other writers
which do not honor these markers are not fenced by this wrapper; use the child's
fenced plan and independent coordination. Path/identity checks do not defeat
hostile same-user TOCTOU replacement or prove ACL, power-loss or isolation.
The source directory and the three output/model roots must be disjoint. Their
directory identities are rechecked after the child; this does not prove that
individual model files remained unchanged during the child's operation.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_producer as producer
from tools import wd_q4_guard_state_binding as roots
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin

_TASK = "lead-mind-production-20261031"
_POLICY_SHA = frozenset({"0a9b1f67bc7015924ef595a17f7b092274c0fd568691f9d1ef07bf6a7b9decd2",
                         "95e21913155ebc941ab031761343381b407954dd97f65b9482237961f7a312c7"})
_PUBLIC_SHA = frozenset({"a5c5f0d91fc27c799678d1d97a28f48c540997ac91385ae097921388e2a4cdb5",
                         "29382d02a3adcf8dc5c3f3a078f9a1dc5565fdcf1a0f14f9c8c49fc1bb793268"})
_ROSTER_SHA = frozenset({"b60bbfd5cfa28bb93053c9b7492378d19c0abb184bd2569398109c50ef24adcb",
                         "bded859e8ae0ffc42c5a56007883d30e0eb4fafd028da7ff8261ae3bbbf9aaf7"})
_HEX = re.compile(r"[a-f0-9]{64}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_IDENTITY = re.compile(r"(?:windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]+:[0-9a-f]+)\Z")
_PENDING = ".wd-q4-status-prefix-runner.pending"
_ROUTE_KEYS = frozenset({"schema", "source", "generation_path", "checkpoint_root", "private_state_root",
                         "trusted_model_root", "cursor", "prefix_sha256", "max_bytes", "max_rows", "plan_sha256",
                         "anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"})
_REPORT_KEYS = frozenset({"status", "pointer_sha256", "generation", "publisher_authentication",
                          "source_authenticity", "freshness", "encoder_identity_source", "plan_sha256",
                          "backend_isolation", "model_assets"})


class StatusRunnerRefused(ValueError):
    """Fixed pre-child refusal; no input or path in the error."""


class StatusRunnerOutcomeUnknown(RuntimeError):
    """Reservation or publication was entered: reconcile both roots before use."""


class StatusRunnerInterrupted(BaseException):
    """Interrupted after reservation: reconcile both roots; do not retry."""


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _json(raw, limit):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError()
    result = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=roots._unique, parse_constant=roots._constant)
    if type(result) is not dict:
        raise ValueError()
    return result


def _pinned(raw, expected, allowed=None, limit=65536):
    if (type(raw) is not bytes or len(raw) > limit or type(expected) is not str or not _HEX.fullmatch(expected)
            or not hmac.compare_digest(_digest(raw), expected) or (allowed is not None and expected not in allowed)):
        raise ValueError()


def _plain_file(text):
    path = roots._root(text)
    roots._observe(path.parent)
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
        raise ValueError()
    return path


def _separate(left, right):
    # Reject equal or nested roots, including same-file aliases observed now.
    return not (any(os.path.samefile(left, node) for node in (right, *right.parents))
                or any(os.path.samefile(right, node) for node in (left, *left.parents)))


def _absent(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    return False


def _create(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError()
        with os.fdopen(fd, "wb", closefd=False) as stream:
            if stream.write(raw) != len(raw):
                raise OSError()
            stream.flush()
        os.fsync(fd)
        return info
    finally:
        os.close(fd)


def _verify_own(path, expected, info):
    current = path.lstat()
    if (not stat.S_ISREG(current.st_mode) or stat.S_ISLNK(current.st_mode)
            or getattr(current, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or not os.path.samestat(current, info)):
        raise ValueError()
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not os.path.samestat(opened, info) or os.read(fd, len(expected) + 1) != expected:
            raise ValueError()
    finally:
        os.close(fd)
    if not os.path.samestat(path.lstat(), info):
        raise ValueError()


def _result_valid(result, route, plan):
    expected_keys = _REPORT_KEYS | ({"coverage", "source_refusals"} if plan["schema"].endswith(".v3") else set())
    if (type(result) is not dict or frozenset(result) != expected_keys
            or type(result["status"]) is not str
            or result["status"] not in ("PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED", "EMPTY_NO_CHANGE")
            or type(result["pointer_sha256"]) is not str or _HEX.fullmatch(result["pointer_sha256"]) is None
            or any(type(result[name]) is not str or result[name] != "UNKNOWN"
                   for name in ("publisher_authentication", "source_authenticity", "freshness"))
            or result["plan_sha256"] != route["plan_sha256"]
            or result["encoder_identity_source"] != "encode.model_identity"
            or result["backend_isolation"] != "in_process_environment_restored_no_os_isolation"
            or result["model_assets"] != "caller_manifest_verified_before_load_reread_by_backend"):
        return False
    status, previous = result["status"], plan["expected_pointer_sha256"]
    if ((previous is None) != (status == "PUBLISHED_GENESIS")
            or (status in ("PUBLISHED_GENESIS", "PUBLISHED_HISTORY") and result["generation"] != plan["generation"])
            or (status in ("EMPTY_ADVANCED", "EMPTY_NO_CHANGE") and result["generation"] is not None)
            or (status == "EMPTY_NO_CHANGE" and result["pointer_sha256"] != previous)
            or (status in ("PUBLISHED_HISTORY", "EMPTY_ADVANCED") and result["pointer_sha256"] == previous)):
        return False
    return (plan["schema"].endswith(".v1") or
            (result["coverage"] == "caught_up_to_final_snapshot_not_history"
             and result["source_refusals"] == "NOT_AGGREGATED"))


def run_once(*, enabled=False, route_bytes=None, expected_route_sha256=None, plan_bytes=None,
             policy_bytes=None, public_policy_bytes=None, roster_bytes=None, anchor_bytes=None):
    """Call the fixed child at most once for an independently pinned route."""
    if enabled is False:
        return {"status": "OFF"}
    prepared = False
    try:
        if enabled is not True:
            raise ValueError()
        _pinned(route_bytes, expected_route_sha256)
        route = _json(route_bytes, 65536)
        if frozenset(route) != _ROUTE_KEYS or route["schema"] != "wd.q4-status-prefix-route.v1":
            raise ValueError()
        _pinned(plan_bytes, route["plan_sha256"])
        _pinned(anchor_bytes, route["anchor_sha256"], limit=16384)
        _pinned(policy_bytes, route["policy_sha256"], _POLICY_SHA)
        _pinned(public_policy_bytes, route["public_policy_sha256"], _PUBLIC_SHA)
        _pinned(roster_bytes, route["roster_sha256"], _ROSTER_SHA, limit=1024 * 1024)
        tasks, _ = adapter.strict_pins(policy_bytes, route["policy_sha256"], roster_bytes, route["roster_sha256"])
        public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, route["public_policy_sha256"], tasks)
        if tasks != frozenset({_TASK}) or public_tasks != frozenset({_TASK}) or public_types != frozenset({"status"}):
            raise ValueError()
        plan = _json(plan_bytes, 65536)
        if (plan.get("schema") not in ("wd.q4-prefix-build-plan.v1", "wd.q4-prefix-build-plan.v3")
                or (plan["schema"].endswith(".v3") and plan.get("fenced") is not True)
                or type(plan.get("max_prefix_bytes")) is not int or not 0 <= plan["max_prefix_bytes"] <= 1024**3
                or plan.get("pins") != {key: route[key] for key in ("anchor_sha256", "policy_sha256",
                                                                      "roster_sha256", "public_policy_sha256")}):
            raise ValueError()
        cursor = route["cursor"]
        if (type(cursor) is not dict or set(cursor) != {"offset", "file_identity", "generation"}
                or type(cursor["offset"]) is not int or not 0 <= cursor["offset"] <= plan["max_prefix_bytes"]
                or type(cursor["file_identity"]) is not str or _IDENTITY.fullmatch(cursor["file_identity"]) is None
                or type(cursor["generation"]) is not str or _GENERATION.fullmatch(cursor["generation"]) is None
                or type(route["prefix_sha256"]) is not str or _HEX.fullmatch(route["prefix_sha256"]) is None
                or type(route["max_bytes"]) is not int or not 1 <= route["max_bytes"] <= 4 * 1024 * 1024
                or type(route["max_rows"]) is not int or not 1 <= route["max_rows"] <= 10000
                or (plan.get("expected_pointer_sha256") is None and
                    (cursor["offset"] != 0 or route["prefix_sha256"] != _digest(b"")))):
            raise ValueError()
        source, sidecar = _plain_file(route["source"]), _plain_file(route["generation_path"])
        checkpoint = roots._root(route["checkpoint_root"])
        private = roots._root(route["private_state_root"])
        model = roots._root(route["trusted_model_root"])
        checkpoint_ids, private_ids, model_ids = roots._observe(checkpoint), roots._observe(private), roots._observe(model)
        source_parent_ids = roots._observe(source.parent)
        if not all((_separate(checkpoint, private), _separate(checkpoint, model), _separate(private, model),
                    *(_separate(source.parent, root) for root in (checkpoint, private, model)))):
            raise ValueError()
        marks = (checkpoint / _PENDING, private / _PENDING)
        if not all(_absent(path) for path in marks):
            raise ValueError()
        cursor = BridgeCursor(**cursor)
        prefix = BridgePrefixPin(cursor=cursor, sha256=route["prefix_sha256"])
        config = producer.PublicSourceConfig(source=producer.SourceConfig(
            source=source, generation_path=sidecar, cursor=cursor, anchor_bytes=anchor_bytes,
            anchor_sha256=route["anchor_sha256"], policy_bytes=policy_bytes, policy_sha256=route["policy_sha256"],
            roster_bytes=roster_bytes, roster_sha256=route["roster_sha256"], max_bytes=route["max_bytes"],
            max_rows=route["max_rows"]), public_policy_bytes=public_policy_bytes,
            public_policy_sha256=route["public_policy_sha256"])
        from sandbox.mind.step2.wd_q4_prefix_build_child import build_prefix_generation
        prepared = True
    except Exception:
        pass
    if not prepared:
        route_bytes = expected_route_sha256 = plan_bytes = policy_bytes = public_policy_bytes = roster_bytes = None
        anchor_bytes = route = plan = source = sidecar = checkpoint = private = model = config = None
        raise StatusRunnerRefused("status_runner_refused")

    uncertain = True
    interrupted = False
    report = None
    try:
        token = secrets.token_hex(32).encode("ascii") + b"\n"
        if (roots._observe(checkpoint) != checkpoint_ids or roots._observe(private) != private_ids
                or roots._observe(model) != model_ids or roots._observe(source.parent) != source_parent_ids):
            raise ValueError()
        first = _create(marks[0], token)
        second = _create(marks[1], token)
        report = build_prefix_generation(enabled=True, plan_bytes=plan_bytes, expected_plan_sha256=route["plan_sha256"],
                                         config=config, start_prefix=prefix, root=checkpoint, private_state_root=private,
                                         trusted_model_root=model)
        if not _result_valid(report, route, plan):
            raise ValueError()
        if (roots._observe(private) != private_ids or roots._observe(checkpoint) != checkpoint_ids
                or roots._observe(model) != model_ids or roots._observe(source.parent) != source_parent_ids):
            raise ValueError()
        _verify_own(marks[0], token, first)
        _verify_own(marks[1], token, second)
        uncertain = False
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        interrupted = True
    except Exception:
        pass
    except BaseException:
        interrupted = True
    if uncertain:
        report = config = prefix = route_bytes = expected_route_sha256 = route = plan = plan_bytes = None
        policy_bytes = public_policy_bytes = roster_bytes = anchor_bytes = source = sidecar = checkpoint = private = model = None
        if interrupted:
            raise StatusRunnerInterrupted("publication_state_unknown")
        raise StatusRunnerOutcomeUnknown("publication_state_unknown")
    return report
