"""HEX MEMORY STEP 2, private read: one lane asks Q4 over the frozen memory through stdin, read-only.

usage: python -m sandbox.mind.step2.wd_q4_private_read --memory <memory dir> --descriptor <descriptor.json> --descriptor-sha256 <sha256> < query.json

The query (``{"situation": "<text>", "time": "<aware UTC>"}``, at most ``STDIN_BUDGET`` bytes, no other key) is read
from stdin only: the situation text never appears in process arguments, in a log, in a bridge event, on stderr or in
the output. The output on stdout is the Q4 result as ``q4.py`` computes it (status, reason, refs and metadata, never
record text, passed through the decoded privacy guard) plus the binding this read was made under.

Fail-closed binding, checked before anything is loaded or encoded:

* the descriptor file (``DESCRIPTOR_SCHEMA``) names the immutable memory by the sha256 of its ``memory.json``, the
  vector dimension and the expected encoder identity (``model``, ``revision``, ``weights_sha256``, as
  ``encode.model_identity`` writes it into every cell receipt); the lane's procedure pins the descriptor, this module
  never derives it;
* ``memory.json`` must hash to the descriptor (``memory_descriptor_mismatch``), the memory must open and verify
  (``memory_unreadable``: every cell's receipt is checked by ``memory.Memory``), its dimension must match, and every
  healthy cell's receipt must carry exactly the descriptor's encoder identity (``encoder_identity_mismatch``);
* the local runtime encoder (``encode.model_identity``: the cached revision and the weights sha256) must equal the
  descriptor before the model is loaded (``encoder_runtime_mismatch`` / ``encoder_runtime_unavailable``): a different
  cached model would make the query vector incomparable to the stored ones.

The output is a projection of the Q4 result over an allowlist (``project_result``): status, reason code, the rule
version Q4 reported for this run (whatever the port returns; not a frozen-v1 evidence claim), per result the kind,
cell, tier, time, view ranks and scores, and every reference as its sha256 plus the reference itself only when it is
hash-shaped (an event id). A lesson file name or any other path-like reference is never written; a lane resolves
the digests against its own allowed list. Unexpected keys are dropped, unexpected shapes refuse.

Nothing here authenticates the caller: no agent name is accepted or echoed, the output states
``caller_declared_not_authenticated``. Nothing is written anywhere: no corpus, memory, vector or model build; the
only model execution is the one query encode after every check passed. Exit 0 with a Q4 status (ANSWER, ABSTAIN,
DEGRADED, REFUSED) or exit 2 with a fixed adapter refusal reason; neither carries the query text.

Residual TOCTOU: ``memory.json`` is digested before and again after ``memory.Memory`` opened and verified the cells,
and the open itself binds every cell receipt to ``memory.json``; a directory swapped between those reads is refused
(``memory_descriptor_changed``), but a swap landing between the second digest and the FAISS reads inside Q4 is not
detected here. The read-only root and its ACL (operator) close that window; this module cannot.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

try:
    from sandbox.mind.step2 import corpus as corpus_mod
    from sandbox.mind.step2 import encode as encode_mod
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import q4 as q4_mod
except Exception:  # Missing native packages must not expose paths or traceback in the CLI.
    if __name__ != "__main__":
        raise
    print(json.dumps({"schema": "wd.bridge-mind-q4-private-read.v1", "status": "REFUSED",
                      "reason": "runtime_unavailable", "results": []}, sort_keys=True))
    sys.exit(2)

SCHEMA = "wd.bridge-mind-q4-private-read.v1"
DESCRIPTOR_SCHEMA = "wd.bridge-mind-q4-memory-descriptor.v1"
STDIN_BUDGET = 65536
DESCRIPTOR_BUDGET = 65536
SITUATION_MAX_CHARS = 4000
ENCODER_KEYS = ("model", "revision", "weights_sha256")
IDENTITY = "caller_declared_not_authenticated"
HEX64 = 64
STATUSES = ("ANSWER", "ABSTAIN", "DEGRADED", "REFUSED")
OPAQUE_REF = re.compile(r"(?:[a-z][a-z0-9_-]{0,15}:)?[0-9a-f]{40,64}")  # hash-shaped only; a file name never
TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REASON = re.compile(r"[a-z0-9_]{1,64}")
RULE = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
TIME = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|\+00:00)")

# Injection points for synthetic checks (a test replaces them with fakes); production uses the step-2 modules as is.
MEMORY_FACTORY = memory_mod.Memory
ENCODER_FACTORY = encode_mod.E5
RUNTIME_IDENTITY = encode_mod.model_identity
Q4_FACTORY = q4_mod.Q4
MEMORY_DIGEST = memory_mod.memory_sha256


class PrivateReadRefused(ValueError):
    """A fixed refusal reason; never carries the query, a path or a caller value."""


class PrivateArgumentParser(argparse.ArgumentParser):
    """Invalid arguments must not be reflected into logs or the terminal."""

    def error(self, message):
        self.exit(2, "invalid_arguments\n")


def _hex64(value):
    return isinstance(value, str) and len(value) == HEX64 and all(c in "0123456789abcdef" for c in value)


def read_query(stream):
    """The bounded stdin query: exactly ``situation`` (non-empty text) and ``time`` (text); nothing else."""
    try:
        raw = stream.read(STDIN_BUDGET + 1)
    except (OSError, ValueError):
        raise PrivateReadRefused("stdin_unreadable") from None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > STDIN_BUDGET:
        raise PrivateReadRefused("stdin_over_budget")
    try:
        query = json.loads(bytes(raw).decode("utf-8"))
    except (ValueError, RecursionError):
        raise PrivateReadRefused("stdin_not_json") from None
    if not isinstance(query, dict) or set(query) != {"situation", "time"}:
        raise PrivateReadRefused("query_shape")
    situation, when = query["situation"], query["time"]
    if not isinstance(situation, str) or not situation.strip() or len(situation) > SITUATION_MAX_CHARS:
        raise PrivateReadRefused("situation_invalid")
    if not isinstance(when, str) or corpus_mod.parse_utc(when) is None:
        raise PrivateReadRefused("time_invalid")
    return situation, when


def read_descriptor(path, expected_sha256):
    """The pinned memory descriptor: schema, memory_sha256 (hex64), dim (int) and the encoder identity keys."""
    if not _hex64(expected_sha256):
        raise PrivateReadRefused("descriptor_pin_invalid")
    try:
        with Path(path).open("rb") as descriptor_file:
            raw = descriptor_file.read(DESCRIPTOR_BUDGET + 1)
        if len(raw) > DESCRIPTOR_BUDGET:
            raise PrivateReadRefused("descriptor_over_budget")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise PrivateReadRefused("descriptor_pin_mismatch")
        descriptor = json.loads(raw.decode("utf-8"))
    except PrivateReadRefused:
        raise
    except (OSError, ValueError, RecursionError):
        raise PrivateReadRefused("descriptor_unreadable") from None
    if (not isinstance(descriptor, dict) or set(descriptor) != {"schema", "memory_sha256", "dim", "encoder"}
            or descriptor["schema"] != DESCRIPTOR_SCHEMA or not _hex64(descriptor["memory_sha256"])
            or type(descriptor["dim"]) is not int or descriptor["dim"] < 1
            or not isinstance(descriptor["encoder"], dict) or set(descriptor["encoder"]) != set(ENCODER_KEYS)
            or not all(isinstance(descriptor["encoder"][key], str) and descriptor["encoder"][key]
                       for key in ENCODER_KEYS)
            or not _hex64(descriptor["encoder"]["weights_sha256"])):
        raise PrivateReadRefused("descriptor_shape")
    return descriptor


def _identity_of(value):
    """The three identity keys of an encoder record, or None when any is missing or not text."""
    if not isinstance(value, dict) or not all(isinstance(value.get(key), str) for key in ENCODER_KEYS):
        return None
    return {key: value[key] for key in ENCODER_KEYS}


def bind_memory(memory_dir, descriptor):
    """The opened memory, after the digest, dimension and per-cell encoder identity checks; fails closed."""
    try:
        if MEMORY_DIGEST(Path(memory_dir)) != descriptor["memory_sha256"]:
            raise PrivateReadRefused("memory_descriptor_mismatch")
    except (OSError, ValueError) as exc:
        if isinstance(exc, PrivateReadRefused):
            raise
        raise PrivateReadRefused("memory_descriptor_unreadable") from None
    try:
        memory = MEMORY_FACTORY(Path(memory_dir))
    except memory_mod.MemoryError_:
        raise PrivateReadRefused("memory_unreadable") from None
    except Exception:  # Never leak a query, path, or backend exception to the caller.
        raise PrivateReadRefused("memory_unreadable") from None
    try:  # the directory could be re-pointed between the descriptor check and the open: digest it again
        unchanged = MEMORY_DIGEST(Path(memory_dir)) == descriptor["memory_sha256"]
    except (OSError, ValueError):
        unchanged = False
    if not unchanged:
        raise PrivateReadRefused("memory_descriptor_changed")
    if memory.memory.get("dim") != descriptor["dim"]:
        raise PrivateReadRefused("memory_dim_mismatch")
    healthy = [view for view in memory.views.values() if view.healthy]
    if not healthy:
        raise PrivateReadRefused("memory_no_healthy_cell")
    for view in healthy:
        try:
            recorded = _identity_of(view.receipt["content"]["encoder"])
        except (KeyError, TypeError):
            recorded = None
        if recorded != descriptor["encoder"]:
            raise PrivateReadRefused("encoder_identity_mismatch")
    return memory


def bind_runtime_encoder(descriptor):
    """The local encoder identity must equal the descriptor before any model is loaded."""
    try:
        runtime = _identity_of(RUNTIME_IDENTITY())
    except (OSError, ValueError, KeyError, TypeError):
        raise PrivateReadRefused("encoder_runtime_unavailable") from None
    if runtime != descriptor["encoder"]:
        raise PrivateReadRefused("encoder_runtime_mismatch")


def _token(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _opaque_ref(value):
    """A reference as the output carries it: always its sha256, the value itself only when it is hash-shaped."""
    if not isinstance(value, str) or not value:
        raise PrivateReadRefused("q4_result_shape")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return (value if _token(value, OPAQUE_REF) else None), digest


def project_result(result):
    """The Q4 result reduced to its allowlisted fields; anything else (a lesson file name, an unexpected key or
    type) is dropped or refused. ``rule`` is the version Q4 reported for this run, not an evidence claim."""
    if not isinstance(result, dict) or result.get("status") not in STATUSES:
        raise PrivateReadRefused("q4_result_shape")
    reason, rule = result.get("reason"), result.get("rule")
    if reason is not None and not _token(reason, REASON):
        raise PrivateReadRefused("q4_result_shape")
    if not _token(rule, RULE):
        raise PrivateReadRefused("q4_result_shape")
    items = []
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            raise PrivateReadRefused("q4_result_shape")
        ref, ref_sha256 = _opaque_ref(item.get("ref"))
        doc_id, doc_id_sha256 = _opaque_ref(item.get("doc_id"))
        kind, cell, tier, when = item.get("kind"), item.get("cell"), item.get("tier"), item.get("time_utc")
        ranks = item.get("view_ranks")
        if (not _token(kind, TOKEN) or not _token(cell, TOKEN) or not _token(tier, TOKEN)
                or not (when is None or _token(when, TIME))
                or not isinstance(ranks, dict) or set(ranks) != {"w", "d", "b"}
                or not all(type(r) is int and r > 0 for r in ranks.values())
                or not all(isinstance(item.get(k), (int, float)) and not isinstance(item.get(k), bool)
                           for k in ("score", "fused"))):
            raise PrivateReadRefused("q4_result_shape")
        items.append({"ref": ref, "ref_sha256": ref_sha256, "doc_id": doc_id, "doc_id_sha256": doc_id_sha256,
                      "kind": kind, "cell": cell, "tier": tier, "time_utc": when, "view_ranks": dict(ranks),
                      "score": item["score"], "fused": item["fused"], "fallback": bool(item.get("fallback"))})
    degraded = []
    for entry in result.get("degraded_cells") or []:
        if not isinstance(entry, dict) or not _token(entry.get("cell"), TOKEN) or not _token(entry.get("reason"),
                                                                                                REASON):
            raise PrivateReadRefused("q4_result_shape")
        degraded.append({"cell": entry["cell"], "reason": entry["reason"]})
    telemetry = result.get("telemetry")
    if telemetry is not None and not isinstance(telemetry, dict):
        raise PrivateReadRefused("q4_result_shape")
    numbers = {key: value for key, value in (telemetry or {}).items()
               if _token(key, TOKEN) and isinstance(value, (int, float)) and not isinstance(value, bool)}
    return {"status": result["status"], "abstain": result["status"] == "ABSTAIN", "reason": reason, "rule": rule,
            "degraded": bool(result.get("degraded")), "degraded_cells": degraded, "results": items,
            "telemetry": numbers}


def private_read(memory_dir, descriptor_path, descriptor_sha256, stream):
    """One private Q4 read: query from ``stream``, bindings from the descriptor; the guarded output object."""
    situation, when = read_query(stream)
    descriptor = read_descriptor(descriptor_path, descriptor_sha256)
    memory = bind_memory(memory_dir, descriptor)
    bind_runtime_encoder(descriptor)
    try:
        encoder = ENCODER_FACTORY()
    except Exception:  # noqa: BLE001 - a model that cannot load is one fixed reason, never its message
        raise PrivateReadRefused("encoder_load_failed") from None
    try:
        result = Q4_FACTORY(memory, encoder).ask(situation, when)
    except memory_mod.MemoryError_:
        raise PrivateReadRefused("memory_unreadable") from None
    except Exception:  # Backend messages can contain query text or private paths.
        raise PrivateReadRefused("q4_execution_failed") from None
    out = dict(project_result(result), schema=SCHEMA, identity=IDENTITY,
               binding={"descriptor_sha256": descriptor_sha256, "memory_sha256": descriptor["memory_sha256"], "dim": descriptor["dim"],
                        "encoder": dict(descriptor["encoder"])})
    try:
        corpus_mod.ws.guard_object(out)
    except corpus_mod.ws.WorkspaceError:  # a guarded word anywhere in the result: nothing of it is written
        raise PrivateReadRefused("output_guarded") from None
    return out


def main(argv=None, stdin=None, stdout=None):
    parser = PrivateArgumentParser(description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--memory", type=Path, required=True)
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--descriptor-sha256", required=True)
    args = parser.parse_args(argv)  # any other option, --situation included, is a usage error: nothing is read
    stdin = sys.stdin.buffer if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        out = private_read(args.memory, args.descriptor, args.descriptor_sha256, stdin)
    except PrivateReadRefused as exc:
        stdout.write(json.dumps({"schema": SCHEMA, "identity": IDENTITY, "status": "REFUSED", "reason": str(exc),
                                 "results": []}, sort_keys=True) + "\n")
        return 2
    stdout.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
