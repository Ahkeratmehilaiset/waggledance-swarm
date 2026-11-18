# SPDX-License-Identifier: BUSL-1.1
"""Offline E5 vector worker for a previously verified runtime bootstrap.

This module is not a bridge entrypoint. The pinned isolated bootstrap must
verify the bundle, interpreter, runtime tree, model assets, and the exact
worker bytes before invoking ``main``. It does not authorize a data source or
authenticate the caller. Requests and float32 responses travel only on bounded
private pipes; no request text or vectors are written to logs or files.

The E5 input policy is based on sandbox/mind/step2/encode.py at commit
e98c5aef5bd3bd66c61e5692e738f17a4ecf6beb, adapted for already-prefixed
passage/query batches. OS network isolation, native closure and same-user
races are not proven by this module.
"""

from contextlib import nullcontext
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys

MAX_FRAME = 65536
MAX_BATCH = 4
MAX_INPUT_CHARS = 2009
DIMENSION = 384
_RESPONSE = struct.Struct("<4sIBH32s32s")
_LENGTH = struct.Struct("<I")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
BULK_CONTRACT = b'{"frame_bytes":65536,"inflight_bytes":65540,"nonce_limit":8192,"schema":"wd.mind-e5-bulk-contract.v1","stdout_bytes":67108864}'
BULK_CONTRACT_SHA256 = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'
_SENTINEL = r"C:\Python\project2\.codex-audit\wd-mind-runtime\q4-reader.lock"


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _read_exact(stream, size, *, eof_ok=False):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if type(part) is not bytes or len(part) > size - len(data):
            raise ValueError("invalid_read")
        if not part:
            if eof_ok and not data:
                return None
            raise ValueError("truncated_frame")
        data.extend(part)
    return bytes(data)


def _request(stream):
    header = _read_exact(stream, _LENGTH.size, eof_ok=True)
    if header is None:
        return None
    size = _LENGTH.unpack(header)[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("request_size")
    raw = _read_exact(stream, size)
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("constant")))
    if (type(request) is not dict or set(request) != {"id", "op", "texts", "nonce", "request_sha256"}
            or type(request["id"]) is not int or not 1 <= request["id"] <= 0xFFFFFFFF
            or request["op"] not in ("query", "passage")
            or type(request["nonce"]) is not str or not _HEX.fullmatch(request["nonce"])
            or type(request["request_sha256"]) is not str or not _HEX.fullmatch(request["request_sha256"])
            or type(request["texts"]) is not list or not 1 <= len(request["texts"]) <= MAX_BATCH
            or (request["op"] == "query" and len(request["texts"]) != 1)):
        raise ValueError("request_shape")
    prefix = request["op"] + ": "
    for text in request["texts"]:
        if (type(text) is not str or not text.startswith(prefix) or not text[len(prefix):].strip()
                or len(text) > MAX_INPUT_CHARS or "\x00" in text):
            raise ValueError("invalid_text")
    return request


def _vectors(model, texts):
    rows = model.encode(texts, batch_size=MAX_BATCH, normalize_embeddings=True,
                        convert_to_numpy=True, show_progress_bar=False)
    dtype = getattr(rows, "dtype", None)
    if (getattr(rows, "shape", None) != (len(texts), DIMENSION)
            or getattr(dtype, "name", None) != "float32" or getattr(dtype, "kind", None) != "f"
            or getattr(dtype, "itemsize", None) != 4 or len(rows) != len(texts)):
        raise ValueError("vector_shape_or_dtype")
    output = bytearray()
    for row in rows:
        if len(row) != DIMENSION:
            raise ValueError("vector_dimension")
        values = [float(value) for value in row]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("vector_not_finite")
        packed = struct.pack("<" + "f" * DIMENSION, *values)
        rounded = struct.unpack("<" + "f" * DIMENSION, packed)
        if tuple(values) != rounded:
            raise ValueError("lossy_float32_vector")
        norm = math.sqrt(sum(value * value for value in rounded))
        if not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
            raise ValueError("vector_not_normalized")
        output.extend(packed)
    return bytes(output)


def _send(stream, request_id, payload, count, nonce, request_sha256):
    frame = _RESPONSE.pack(b"ME5R", request_id, 0, count, nonce, request_sha256) + payload
    if len(frame) > MAX_FRAME:
        raise ValueError("response_size")
    stream.write(_LENGTH.pack(len(frame)) + frame)
    stream.flush()


def _nonce_budget(contract_sha256):
    if contract_sha256 is None:
        return 1024
    if (type(contract_sha256) is not str or contract_sha256 != BULK_CONTRACT_SHA256
            or hashlib.sha256(BULK_CONTRACT).hexdigest() != BULK_CONTRACT_SHA256):
        raise ValueError('bulk_contract')
    return 8192


def serve(input_stream, output_stream, model, *, reader_slot=None, bulk_contract_sha256=None):
    """Serve bounded framed requests using a caller-authorized model object."""
    nonce_budget = _nonce_budget(bulk_contract_sha256)
    _send(output_stream, 0, b"", 0, b"\0" * 32, b"\0" * 32)  # model-loaded handshake, not source proof
    previous_id = 0
    active_nonce = active_digest = None
    retired_nonces = set()
    while True:
        request = _request(input_stream)
        if request is None:
            return 0
        if request["id"] <= previous_id:
            raise ValueError("request_order")
        if request["nonce"] != active_nonce:
            used = len(retired_nonces) + (1 if bulk_contract_sha256 is not None and active_nonce is not None else 0)
            if request["nonce"] in retired_nonces or used >= nonce_budget:
                raise ValueError("request_replay")
            if active_nonce is not None:
                retired_nonces.add(active_nonce)
            active_nonce, active_digest = request["nonce"], request["request_sha256"]
        elif request["request_sha256"] != active_digest:
            raise ValueError("request_binding_changed")
        previous_id = request["id"]
        guard = (reader_slot(_SENTINEL, timeout_seconds=30) if reader_slot is not None else nullcontext())
        with guard:
            payload = _vectors(model, request["texts"])
        _send(output_stream, request["id"], payload, len(request["texts"]),
              bytes.fromhex(active_nonce), bytes.fromhex(active_digest))


# Closed table. Every failure of this worker leaves through one of these exit codes, so the parent can name the
# stage without any text from the child; nothing is written to stdout or stderr. The worker itself never returns
# 2: an exit code of 2 means the bootstrap refused (it prints its own fixed refusal line) or the interpreter
# did not start.
STAGE_EXIT_CODES = {"arguments": 20, "model_pin": 21, "offline_environment": 22, "reader_mutex": 23,
                    "library_import": 24, "model_load": 25, "serving": 29}


def main(*, model_dir, weights_sha256, generation, bulk_contract_sha256=None):
    """Load one verified local model; transport only a fixed stage code on failure."""
    stage = "arguments"
    try:
        _nonce_budget(bulk_contract_sha256)
        if (type(weights_sha256) is not str or not _HEX.fullmatch(weights_sha256)
                or type(generation) is not str or not _GENERATION.fullmatch(generation)):
            raise ValueError("identity")
        directory = Path(model_dir)
        if not directory.is_absolute():
            raise ValueError("model_path")
        stage = "model_pin"
        hasher = hashlib.sha256()
        with (directory / "model.safetensors").open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                hasher.update(block)
        if hasher.hexdigest() != weights_sha256:
            raise ValueError("model_pin")
        stage = "offline_environment"
        if (os.environ.get("HF_HUB_OFFLINE") != "1" or os.environ.get("TRANSFORMERS_OFFLINE") != "1"):
            raise ValueError("offline_required")
        stage = "reader_mutex"
        slot = sys.modules.get("sandbox.mind.step2.reader_slot")
        if slot is None or getattr(slot, "_generation", None) != generation or not callable(getattr(slot, "reader_slot", None)):
            raise ValueError("mutex_required")
        stage = "library_import"
        from sentence_transformers import SentenceTransformer
        stage = "model_load"
        model = SentenceTransformer(str(directory), device="cpu", local_files_only=True)
        stage = "serving"
        return serve(sys.stdin.buffer, sys.stdout.buffer, model, reader_slot=slot.reader_slot,
                     bulk_contract_sha256=bulk_contract_sha256)
    except BaseException:
        # SystemExit and KeyboardInterrupt included: a library that exits on its own must not look like a clean end.
        return STAGE_EXIT_CODES[stage]
