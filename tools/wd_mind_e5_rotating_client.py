# SPDX-License-Identifier: BUSL-1.1
"""Rotate cooperative E5 workers before their fixed transport budgets expire.

The caller must authenticate the factory, encoder and snapshot independently.
``source_check`` must revalidate the same frozen source and return its anchored
digest; a digest echo alone is not source authentication. Each worker retains
its original bounded pipes, replay checks and EOF-only shutdown. This wrapper
does not install, start or publish anything by itself.
"""
import hashlib
import hmac
import json
import math
import re
import threading

from tools.wd_mind_e5_client import GracefulE5WorkerClient
from tools.wd_mind_e5_lifecycle import MAX_STDOUT_BYTES, BULK_STDOUT_BYTES, BULK_CONTRACT_SHA256

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FRAME_OVERHEAD = 79  # 4-byte length plus <4sIBH32s32s response header
_VECTOR_BYTES = 384 * 4
_MAX_REQUESTS = 1024
_MAX_RECORDS = 256
_MAX_SESSION_NONCES = 65536


class RotationRefused(ValueError):
    """Fixed refusal; no source content or underlying exception is returned."""


def _validate_build_fence(rotator, source_check, request_fence):
    """Only the exact verified full-runtime fence may alter build checks."""
    from types import MethodType
    from tools import wd_mind_full_runtime as runtime
    if (type(source_check) is not MethodType or type(request_fence) is not MethodType
            or type(source_check.__self__) is not runtime._FastBuildFence
            or request_fence.__self__ is not source_check.__self__
            or source_check.__func__ is not runtime._FastBuildFence.check
            or request_fence.__func__ is not runtime._FastBuildFence.request):
        raise ValueError('build_fence_identity')
    source_check.__self__._verify_arm(rotator)
    return tuple(source_check.__self__.rows)


class RotatingGracefulE5Client:
    """Sequential worker generations; HOLD retains the existing owner.

    factory must return start_e5_worker_graceful's result dictionary. The
    trusted caller must keep this wrapper alive while ``owner`` is non-None.
    Replay protection persists across workers for this wrapper's lifetime. Its
    fixed nonce budget fails closed; it is not persisted across new wrappers.
    """

    def __init__(self, *, factory, encoder_binding_sha256, snapshot_sha256,
                 source_check, shutdown_timeout, bulk_contract_sha256=None):
        if (not callable(factory) or not callable(source_check)
                or type(encoder_binding_sha256) is not str or not _HEX.fullmatch(encoder_binding_sha256)
                or type(snapshot_sha256) is not str or not _HEX.fullmatch(snapshot_sha256)
                or type(shutdown_timeout) not in (int, float) or not math.isfinite(shutdown_timeout)
                or not 0 < shutdown_timeout <= 1800
                or (bulk_contract_sha256 is not None and
                    (type(bulk_contract_sha256) is not str or bulk_contract_sha256 != BULK_CONTRACT_SHA256))):
            raise RotationRefused("mind_e5_rotation_refused")
        self._factory, self._source_check = factory, source_check
        self._binding, self._snapshot = encoder_binding_sha256, snapshot_sha256
        self._timeout = float(shutdown_timeout)
        self._bulk_contract_sha256 = bulk_contract_sha256
        self._stdout_limit = BULK_STDOUT_BYTES if bulk_contract_sha256 is not None else MAX_STDOUT_BYTES
        self._request_limit = 8192 if bulk_contract_sha256 is not None else _MAX_REQUESTS
        self._client = self._owner = None
        self._used_bytes = _FRAME_OVERHEAD  # loaded handshake
        self._requests = 0
        self._session_nonces = set()
        self._failed = self._closed = self._hold = False
        self._lock = threading.Lock()
        self._build_fence = self._build_source = self._build_previous = None
        self._build_rows, self._build_index = None, 0

    def arm_build_fence(self, *, enabled=False, source_check=None, request_fence=None):
        """Install opt-in request/source fences before the first worker exists."""
        if (enabled is not True or not callable(source_check) or isinstance(source_check, type)
                or not callable(request_fence) or isinstance(request_fence, type)):
            raise RotationRefused("mind_e5_rotation_refused")
        if not self._lock.acquire(blocking=False):
            raise RotationRefused("mind_e5_rotation_refused")
        try:
            if (self._failed or self._closed or self._hold or self._build_fence is not None
                    or self._client is not None or self._owner is not None or self._session_nonces):
                raise ValueError("build_fence_state")
            rows = _validate_build_fence(self, source_check, request_fence)
            if type(rows) is not tuple or not rows:
                raise ValueError("build_anchor")
            self._build_rows, self._build_index = rows, 0
            self._build_previous, self._build_source = self._source_check, source_check
            self._source_check, self._build_fence = source_check, request_fence
        except Exception:
            raise RotationRefused("mind_e5_rotation_refused") from None
        finally:
            self._lock.release()

    def disarm_build_fence(self, *, enabled=False):
        """Restore the original source callback after the scoped build."""
        if enabled is not True or not self._lock.acquire(blocking=False):
            raise RotationRefused("mind_e5_rotation_refused")
        try:
            if self._build_fence is None or self._source_check is not self._build_source:
                raise ValueError("build_fence_state")
            self._source_check = self._build_previous
            self._build_fence = self._build_source = self._build_previous = None
            self._build_rows, self._build_index = None, 0
        except Exception:
            raise RotationRefused("mind_e5_rotation_refused") from None
        finally:
            self._lock.release()

    @property
    def owner(self):
        return self._owner

    def _check_source(self):
        result = self._source_check()
        if type(result) is not str or not hmac.compare_digest(result, self._snapshot):
            raise ValueError("source_changed")

    def _finish(self):
        if self._client is None:
            if self._owner is not None:
                raise ValueError("owner_retained")
            return {"state": "EXITED", "owned": False, "exit_code": 0}
        if type(self._client) is not GracefulE5WorkerClient or self._client.owner is not self._owner:
            raise ValueError("owner_retained")
        status = self._client.close(timeout=self._timeout)
        if (type(status) is not dict or status.get("state") != "EXITED"
                or status.get("owned") is not False or status.get("exit_code") != 0):
            self._hold = True
            raise ValueError("owner_retained")
        self._client = self._owner = None
        self._hold = False
        return status

    def _start(self):
        result = self._factory()
        if type(result) is not dict:
            raise ValueError("start")
        self._owner = result.get("owner")
        self._client = result.get("client")
        if (result.get("status") != "READY" or type(self._client) is not GracefulE5WorkerClient
                or self._owner is None or self._client.owner is not self._owner
                or self._client._binding_sha256 != self._binding
                or self._client._closed or self._client._next_id != 1 or self._client._used_nonces):
            raise ValueError("start")
        if (self._bulk_contract_sha256 is not None and
                (result.get('bulk_contract_sha256') != self._bulk_contract_sha256
                 or getattr(self._client, '_bulk_contract_sha256', None) != self._bulk_contract_sha256
                 or getattr(self._owner, '_bulk_contract_sha256', None) != self._bulk_contract_sha256)):
            raise ValueError('bulk_contract')
        status = self._owner.status()
        if type(status) is not dict or status.get("state") != "RUNNING" or status.get("owned") is not True:
            raise ValueError("owner")
        self._requests, self._used_bytes = 0, _FRAME_OVERHEAD

    def _validate(self, request):
        if type(request) is not dict or request.get("operation") not in ("refresh", "query"):
            raise ValueError("request")
        field = "passages" if request["operation"] == "refresh" else "query"
        keys = {"schema", "operation", "nonce", "encoder_binding_sha256", "request_sha256", field}
        if (set(request) != keys or request["schema"] != "wd.mind-supervised-e5-request.v1"
                or request["encoder_binding_sha256"] != self._binding
                or type(request["nonce"]) is not str or not _HEX.fullmatch(request["nonce"])
                or type(request["request_sha256"]) is not str or not _HEX.fullmatch(request["request_sha256"])):
            raise ValueError("request")
        values = request[field] if field == "passages" else [request[field]]
        if type(values) is not list or not 1 <= len(values) <= _MAX_RECORDS:
            raise ValueError("count")
        prefix = "passage: " if field == "passages" else "query: "
        if any(type(value) is not str or not value.startswith(prefix)
               or not value[len(prefix):].strip() or len(value) > 2009 or "\x00" in value
               for value in values):
            raise ValueError("text")
        unsigned = {key: value for key, value in request.items() if key != "request_sha256"}
        raw = json.dumps(unsigned, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), request["request_sha256"]):
            raise ValueError("digest")
        return len(values)

    def encode_supervised(self, request):
        if not self._lock.acquire(blocking=False):
            raise RotationRefused("mind_e5_rotation_refused")
        try:
            if self._failed or self._closed:
                raise ValueError("closed")
            count = self._validate(request)
            fence, source, frozen = self._build_fence, self._build_source, None
            anchor, index = self._build_rows, self._build_index
            if fence is not None:
                if (self._source_check is not source or type(anchor) is not tuple
                        or request.get("operation") != "refresh" or not 1 <= count <= 4
                        or index + count > len(anchor)
                        or tuple(request["passages"]) != tuple(text for _, text in anchor[index:index+count])):
                    raise ValueError("build_source_changed")
                frozen = json.dumps(request, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")
                request = json.loads(frozen.decode("utf-8"))
                if self._validate(request) != count:
                    raise ValueError("build_request_changed")
            nonce = request["nonce"]
            if nonce in self._session_nonces or len(self._session_nonces) >= _MAX_SESSION_NONCES:
                raise ValueError("session_nonce_replay_or_budget")
            # Consume before starting a worker or exposing any data to one;
            # failures close this wrapper and never make the nonce reusable.
            self._session_nonces.add(nonce)
            if fence is not None:
                fence("before", frozen)
                if (self._build_fence is not fence or self._source_check is not source
                        or self._build_rows is not anchor or self._build_index != index):
                    raise ValueError("build_fence_changed")
            needed = ((count + 3) // 4) * _FRAME_OVERHEAD + count * _VECTOR_BYTES
            if _FRAME_OVERHEAD + needed > self._stdout_limit:
                raise ValueError("budget")
            self._check_source()
            if self._client is not None and (self._requests >= self._request_limit
                                             or self._used_bytes + needed > self._stdout_limit):
                self._finish()
                self._check_source()
            if self._client is None:
                self._start()
            result = self._client.encode_supervised(request)
            if (type(result) is not dict or set(result) != {"nonce", "request_sha256", "vector_bytes"}
                    or result["nonce"] != request["nonce"] or result["request_sha256"] != request["request_sha256"]
                    or type(result["vector_bytes"]) is not bytes
                    or len(result["vector_bytes"]) != count * _VECTOR_BYTES):
                raise ValueError("response")
            self._check_source()
            if fence is not None:
                current = json.dumps(request, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")
                if (current != frozen or self._build_fence is not fence or self._source_check is not source
                        or self._build_rows is not anchor or self._build_index != index):
                    raise ValueError("build_request_changed")
                fence("after", frozen)
                if (self._build_fence is not fence or self._source_check is not source
                        or self._build_rows is not anchor or self._build_index != index):
                    raise ValueError("build_fence_changed")
                self._build_index = index + count
            self._used_bytes += needed
            self._requests += 1
            return result
        except Exception:
            self._failed = True
            if not self._hold:
                try:
                    self._finish()
                except Exception:
                    pass  # The owner remains reachable; do not start a replacement.
            raise RotationRefused("mind_e5_rotation_refused") from None
        finally:
            self._lock.release()

    def close(self):
        if not self._lock.acquire(blocking=False):
            raise RotationRefused("mind_e5_rotation_refused")
        try:
            self._closed = True
            return self._finish()
        except Exception:
            self._failed = True
            raise RotationRefused("mind_e5_rotation_refused") from None
        finally:
            self._lock.release()
