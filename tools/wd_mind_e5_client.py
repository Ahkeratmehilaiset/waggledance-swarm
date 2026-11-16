# SPDX-License-Identifier: BUSL-1.1
"""Bounded private-pipe client for an independently pinned E5 worker.

Construct this only with a process and Windows Job already established by the
trusted runtime supervisor. A successful handshake is not model, provider,
source, or sender authentication. The caller must retain the job for the
entire session and authenticate code and assets before creating the process.
This client neither starts processes nor accesses network, disk, or logs.
"""

import json
import hashlib
import hmac
import math
import queue
import re
import secrets
import struct
import threading
import time

_LENGTH = struct.Struct("<I")
_RESPONSE = struct.Struct("<4sIBH32s32s")
_MAX_FRAME = 65536
_MAX_BATCH = 4
_MAX_RECORDS = 256
BULK_CONTRACT_SHA256 = '61998bfeea1342988301f30f21d9781017249ef66761ec81874e5cc34633bc08'
_DIMENSION = 384
_HEX = re.compile(r"[0-9a-f]{64}\Z")


class E5ClientRefused(ValueError):
    """Fixed refusal token; no private text or provider exception is exposed."""


class E5WorkerClient:
    """One request at a time over private pipes; no fallback or retries."""

    def __init__(self, process, job, *, timeout, encoder_binding_sha256=None, bulk_contract_sha256=None,
                 handshake_deadline=None):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 1800:
            raise E5ClientRefused("mind_e5_worker_refused")
        self._process, self._job, self._timeout = process, job, float(timeout)
        if encoder_binding_sha256 is not None and (type(encoder_binding_sha256) is not str or not _HEX.fullmatch(encoder_binding_sha256)):
            raise E5ClientRefused("mind_e5_worker_refused")
        self._binding_sha256 = encoder_binding_sha256
        if (bulk_contract_sha256 is not None and
                (type(bulk_contract_sha256) is not str or bulk_contract_sha256 != BULK_CONTRACT_SHA256 or job is not None)):
            raise E5ClientRefused("mind_e5_worker_refused")
        self._bulk_contract_sha256 = bulk_contract_sha256
        self._nonce_limit = 8192 if bulk_contract_sha256 is not None else 1024
        self._used_nonces = set()
        self._mutex, self._closed, self._next_id = threading.RLock(), False, 1
        try:
            if process.stdin is None or process.stdout is None:
                raise ValueError("pipes")
            deadline = time.monotonic() + self._timeout
            if handshake_deadline is not None:
                if type(handshake_deadline) not in (int, float) or not math.isfinite(handshake_deadline):
                    raise ValueError("deadline")
                deadline = min(deadline, float(handshake_deadline))
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            self._response(0, 0, deadline, b"\0" * 32, b"\0" * 32)
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
        except Exception:
            self.close()
            raise E5ClientRefused("mind_e5_worker_refused") from None

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._job.terminate()
        except Exception:
            pass
        try:
            self._job.close()
        except Exception:
            pass
        try:
            self._process.wait(timeout=2)
        except Exception:
            pass

    def _interrupt_cleanup(self):
        # Legacy clients cannot cooperatively drain an independently owned
        # process. Keep their references and do not force termination here.
        self._closed = True

    def _retire_on_interrupt(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._interrupt_cleanup()
        except BaseException:
            pass  # Preserve the original cancellation and retained owner.

    def _bounded(self, operation, deadline):
        result = queue.Queue(maxsize=1)
        def run():
            try:
                result.put((True, operation()))
            except Exception:
                result.put((False, None))
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(max(0, deadline - time.monotonic()))
        if thread.is_alive() or result.empty():
            raise ValueError("deadline")
        success, value = result.get_nowait()
        if not success:
            raise ValueError("pipe")
        return value

    @staticmethod
    def _read_exact(stream, size):
        data = bytearray()
        while len(data) < size:
            part = stream.read(size - len(data))
            if type(part) is not bytes or not part or len(part) > size - len(data):
                raise ValueError("short_read")
            data.extend(part)
        return bytes(data)

    def _response(self, request_id, expected_count, deadline, nonce, request_sha256):
        def read():
            size = _LENGTH.unpack(self._read_exact(self._process.stdout, _LENGTH.size))[0]
            if not _RESPONSE.size <= size <= _MAX_FRAME:
                raise ValueError("frame_size")
            return self._read_exact(self._process.stdout, size)
        frame = self._bounded(read, deadline)
        magic, got_id, status, count, got_nonce, got_digest = _RESPONSE.unpack(frame[:_RESPONSE.size])
        if (magic != b"ME5R" or got_id != request_id or status != 0 or count != expected_count
                or got_nonce != nonce or got_digest != request_sha256
                or len(frame) != _RESPONSE.size + count * _DIMENSION * 4):
            raise ValueError("frame_shape")
        values = struct.unpack("<" + "f" * (count * _DIMENSION), frame[_RESPONSE.size:])
        rows = []
        for index in range(count):
            row = list(values[index * _DIMENSION:(index + 1) * _DIMENSION])
            norm = math.sqrt(sum(value * value for value in row))
            if not all(math.isfinite(value) for value in row) or not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
                raise ValueError("vector_invalid")
            rows.append(row)
        return rows, frame[_RESPONSE.size:]

    def _request(self, operation, texts, nonce, request_sha256, deadline):
        if self._closed or not self._mutex.acquire(blocking=False):
            raise E5ClientRefused("mind_e5_worker_refused")
        try:
            if (operation not in ("query", "passage") or type(texts) not in (list, tuple)
                    or not 1 <= len(texts) <= _MAX_BATCH or self._next_id > 0xFFFFFFFF
                    or type(nonce) is not str or not _HEX.fullmatch(nonce)
                    or type(request_sha256) is not str or not _HEX.fullmatch(request_sha256)
                    or (operation == "query" and len(texts) != 1)):
                raise ValueError("request")
            prefix = operation + ": "
            if any(type(text) is not str or not text.startswith(prefix) or not text[len(prefix):].strip()
                   or len(text) > 2009 or "\x00" in text for text in texts):
                raise ValueError("text")
            request_id = self._next_id
            body = json.dumps({"id": request_id, "op": operation, "texts": list(texts),
                               "nonce": nonce, "request_sha256": request_sha256},
                              ensure_ascii=True, separators=(",", ":")).encode("ascii")
            if len(body) > _MAX_FRAME:
                raise ValueError("request_size")
            packet = _LENGTH.pack(len(body)) + body
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            def write():
                written = self._process.stdin.write(packet)
                if written != len(packet):
                    raise ValueError("short_write")
                self._process.stdin.flush()
            self._bounded(write, deadline)
            result = self._response(request_id, len(texts), deadline, bytes.fromhex(nonce), bytes.fromhex(request_sha256))
            self._next_id += 1
            return result
        except Exception:
            self.close()
            raise E5ClientRefused("mind_e5_worker_refused") from None
        except BaseException:
            # A partial multi-frame operation must never be resumed. Preserve
            # the original cancellation even if cooperative cleanup fails.
            self._retire_on_interrupt()
            raise
        finally:
            self._mutex.release()

    def passage_encoder(self, texts):
        if type(texts) not in (tuple, list) or not 1 <= len(texts) <= _MAX_RECORDS:
            self.close()
            raise E5ClientRefused("mind_e5_worker_refused")
        if not self._mutex.acquire(blocking=False):
            raise E5ClientRefused("mind_e5_worker_refused")
        try:
            rows = []
            nonce, digest = self._direct_binding("passage", texts)
            deadline = time.monotonic() + self._timeout
            for index in range(0, len(texts), _MAX_BATCH):
                batch, _ = self._request("passage", texts[index:index + _MAX_BATCH], nonce, digest, deadline)
                rows.extend(batch)
            return rows
        finally:
            self._mutex.release()

    def query_encoder(self, text):
        if not self._mutex.acquire(blocking=False):
            raise E5ClientRefused("mind_e5_worker_refused")
        try:
            nonce, digest = self._direct_binding("query", [text])
            rows, _ = self._request("query", [text], nonce, digest, time.monotonic() + self._timeout)
            return rows[0]
        finally:
            self._mutex.release()

    def encode_supervised(self, request, *, frame_check=None, call_deadline=None):
        """Echo one supervisor binding with original float32 payload bytes.

        The supervisor validates source and caller authority independently.
        The echo binds routing only; it does not prove model computation.
        Synchronous frame checks cannot be interrupted by this deadline; a
        late check or a late return is refused once control returns here.
        """
        if self._closed or not self._mutex.acquire(blocking=False):
            raise E5ClientRefused("mind_e5_worker_refused")
        deadline = time.monotonic() + self._timeout
        try:
            if call_deadline is not None:
                if type(call_deadline) not in (int, float) or not math.isfinite(call_deadline):
                    raise ValueError("deadline")
                deadline = min(deadline, float(call_deadline))
                if time.monotonic() >= deadline:
                    raise ValueError("deadline")
            if frame_check is not None and (not callable(frame_check) or isinstance(frame_check, type)):
                raise ValueError("frame_check")
            if type(request) is not dict or request.get("operation") not in ("refresh", "query"):
                raise ValueError("request")
            field = "passages" if request["operation"] == "refresh" else "query"
            required = {"schema", "operation", "nonce", "encoder_binding_sha256", "request_sha256", field}
            if (set(request) != required or request["schema"] != "wd.mind-supervised-e5-request.v1"
                    or self._binding_sha256 is None or request["encoder_binding_sha256"] != self._binding_sha256
                    or type(request["nonce"]) is not str or not _HEX.fullmatch(request["nonce"])
                    or type(request["request_sha256"]) is not str or not _HEX.fullmatch(request["request_sha256"])
                    or request["nonce"] in self._used_nonces or len(self._used_nonces) >= self._nonce_limit):
                raise ValueError("binding")
            unsigned = {key: value for key, value in request.items() if key != "request_sha256"}
            raw = json.dumps(unsigned, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
            if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), request["request_sha256"]):
                raise ValueError("request_digest")
            texts = request[field] if field == "passages" else [request[field]]
            if type(texts) is not list or not 1 <= len(texts) <= _MAX_RECORDS:
                raise ValueError("count")
            self._used_nonces.add(request["nonce"])
            operation = "passage" if field == "passages" else "query"
            frames = []
            for index in range(0, len(texts), _MAX_BATCH):
                if frame_check is not None:
                    frame_check()
                    if time.monotonic() >= deadline:
                        raise ValueError("deadline")
                _, vector_bytes = self._request(operation, texts[index:index + _MAX_BATCH],
                                                request["nonce"], request["request_sha256"], deadline)
                if frame_check is not None:
                    frame_check()
                    if time.monotonic() >= deadline:
                        raise ValueError("deadline")
                frames.append(vector_bytes)
            result = {"nonce": request["nonce"], "request_sha256": request["request_sha256"],
                      "vector_bytes": b"".join(frames)}
            if time.monotonic() >= deadline:
                raise ValueError("deadline")
            return result
        except Exception:
            self.close()
            raise E5ClientRefused("mind_e5_worker_refused") from None
        except BaseException:
            self._retire_on_interrupt()
            raise
        finally:
            self._mutex.release()

    @staticmethod
    def _direct_binding(operation, texts):
        nonce = secrets.token_hex(32)
        body = json.dumps({"schema": "wd.mind-e5-direct-request.v1", "nonce": nonce,
                           "op": operation, "texts": list(texts)},
                          sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        return nonce, hashlib.sha256(body).hexdigest()


class _OwnedPipes:
    """The existing codec sees pipes; actual I/O stays in three fixed pumps."""

    def __init__(self, owner):
        self.owner, self.deadline = owner, 0
        self.stdin = self.stdout = self

    def read(self, size):
        return self.owner.read(size, self.deadline)

    def write(self, packet):
        return self.owner.write(packet, self.deadline)

    def flush(self):
        pass  # owner.write acknowledges only after its pump flushes.


class GracefulE5WorkerClient(E5WorkerClient):
    """Explicit cooperative alternative; never terminates or releases a live child.

    close(timeout=...) returns a status including HOLD and retained ownership.
    Protocol refusals request EOF without extending an expired request deadline.
    The caller can inspect owner.status() after a refusal and wait again later.
    Existing frame, nonce, digest, batching and float32 checks are unchanged.
    """

    def __init__(self, owner, *, timeout, encoder_binding_sha256=None, bulk_contract_sha256=None,
                 handshake_deadline=None):
        if getattr(owner, '_bulk_contract_sha256', None) != bulk_contract_sha256:
            raise E5ClientRefused("mind_e5_worker_refused")
        self.owner = owner
        self._transport = _OwnedPipes(owner)
        super().__init__(self._transport, None, timeout=timeout, encoder_binding_sha256=encoder_binding_sha256,
                         bulk_contract_sha256=bulk_contract_sha256, handshake_deadline=handshake_deadline)

    def _bounded(self, operation, deadline):
        self._transport.deadline = deadline
        if time.monotonic() >= deadline:
            raise ValueError("deadline")
        return operation()

    def close(self, *, timeout=0):
        self._closed = True
        return self.owner.shutdown(timeout=timeout)

    def _interrupt_cleanup(self):
        try:
            self.close(timeout=0)
        except BaseException:
            # If shutdown itself fails, retain the actual owner and request a
            # non-terminating HOLD before preserving the original interrupt.
            try:
                self.owner._fault('client_interrupt_cleanup_failed')
            except BaseException:
                pass
            raise
